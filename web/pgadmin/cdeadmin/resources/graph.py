##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Endpoint-isolated resource graph, cache and explorer service."""

from __future__ import annotations

import base64
import binascii
import copy
import json
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping
from uuid import UUID, uuid5

from pgadmin.cdeadmin.contracts.v1.runtime import (
    ContractValidationError,
    validate_contract,
)
from pgadmin.cdeadmin.security.redaction import redact
from pgadmin.cdeadmin.security.service import SecurityService
from pgadmin.cdeadmin.security.models import RuntimeIdentityError

from .models import (
    ExplorerAction,
    ExplorerBadge,
    ExplorerNode,
    ResourceAccessError,
    ResourceCommandContribution,
    ResourceGraphError,
    ResourceInspectorContribution,
    ResourcePage,
    ResourceRef,
    StaleResourceGenerationError,
)


MAX_PAGE_SIZE = 500
FIXTURE_MARKER = 'cdeadmin_fixture'


@dataclass
class _CacheEntry:
    generation: str
    resources: tuple[dict[str, Any], ...]


class GenerationAwareResourceCache:
    """Cache resource pages without sharing endpoint generations."""

    def __init__(self):
        self._epochs: dict[str, int] = {}
        self._pages: dict[tuple[str, str], _CacheEntry] = {}
        self._resources: dict[tuple[str, str], dict[str, Any]] = {}
        self._lock = threading.RLock()

    def generation(self, context) -> str:
        with self._lock:
            epoch = self._epochs.get(context.cache_namespace, 0)
        return str(uuid5(
            UUID(context.cache_namespace),
            f'cdeadmin-resource-generation:{epoch}',
        ))

    def page(self, context, parent: ResourceRef):
        generation = self.generation(context)
        with self._lock:
            entry = self._pages.get(
                (context.cache_namespace, parent.resource_id)
            )
            if entry is None or entry.generation != generation:
                return None
            return tuple(copy.deepcopy(entry.resources))

    def put(
        self, context, parent: ResourceRef,
        resources: tuple[dict[str, Any], ...],
    ) -> str:
        generation = self.generation(context)
        with self._lock:
            self._pages[(context.cache_namespace, parent.resource_id)] = (
                _CacheEntry(generation, copy.deepcopy(resources))
            )
            for resource in resources:
                self._resources[
                    (context.cache_namespace, resource['resource_id'])
                ] = copy.deepcopy(resource)
        return generation

    def resource(self, context, ref: ResourceRef):
        with self._lock:
            resource = self._resources.get(
                (context.cache_namespace, ref.resource_id)
            )
            return copy.deepcopy(resource)

    def invalidate(self, context) -> str:
        """Invalidate the complete endpoint generation atomically."""
        namespace = context.cache_namespace
        with self._lock:
            self._epochs[namespace] = self._epochs.get(namespace, 0) + 1
            self._pages = {
                key: value for key, value in self._pages.items()
                if key[0] != namespace
            }
            self._resources = {
                key: value for key, value in self._resources.items()
                if key[0] != namespace
            }
        return self.generation(context)


class ResourceContributionRegistry:
    """Inspector and command contributions selected by resource kind."""

    def __init__(self):
        self._inspectors: dict[str, ResourceInspectorContribution] = {}
        self._commands: dict[str, ResourceCommandContribution] = {}

    def register_inspector(
        self, contribution: ResourceInspectorContribution
    ) -> None:
        if contribution.inspector_id in self._inspectors:
            raise ResourceGraphError('inspector ID is already registered')
        self._inspectors[contribution.inspector_id] = contribution

    def register_command(
        self, contribution: ResourceCommandContribution
    ) -> None:
        if contribution.command_id in self._commands:
            raise ResourceGraphError('command ID is already registered')
        self._commands[contribution.command_id] = contribution

    def inspector(self, inspector_id: str, resource_kind: str):
        contribution = self._inspectors.get(inspector_id)
        if contribution is None or (
            resource_kind not in contribution.resource_kinds
        ):
            raise ResourceAccessError('resource inspector is unavailable')
        return contribution

    def command(self, command_id: str, resource_kind: str):
        contribution = self._commands.get(command_id)
        if contribution is None or (
            resource_kind not in contribution.resource_kinds
        ):
            raise ResourceAccessError('resource command is unavailable')
        return contribution

    def commands_for(self, resource_kind: str):
        return tuple(
            item for item in self._commands.values()
            if resource_kind in item.resource_kinds
        )


class ResourceExplorerService:
    """Provider-neutral paging, inspection and explorer presentation."""

    def __init__(
        self, provider_registry, contributions=None, cache=None,
        security_service=None,
    ):
        self.provider_registry = provider_registry
        self.contributions = (
            contributions or ResourceContributionRegistry()
        )
        self.cache = cache or GenerationAwareResourceCache()
        self.security = security_service or SecurityService()
        self._contributed_providers: set[tuple[str, str]] = set()
        self._contribution_lock = threading.RLock()

    @staticmethod
    def _resource(payload: Mapping[str, Any]) -> dict[str, Any]:
        try:
            return redact(validate_contract('Resource', payload))
        except ContractValidationError as exc:
            raise ResourceAccessError(
                'provider returned an invalid resource'
            ) from exc

    @staticmethod
    def _ref(resource: Mapping[str, Any]) -> ResourceRef:
        return ResourceRef(
            resource['endpoint_id'], resource['resource_id']
        )

    @staticmethod
    def _binding_for_resources(provider_registry, context):
        binding = provider_registry.resolve(context)
        if 'ResourceProvider' not in binding.manifest['contracts']:
            raise ResourceAccessError(
                'endpoint provider has no resource contract'
            )
        binding.require_permission('data_read', 'resource')
        return binding

    def list_page(
        self, context, parent_payload: Mapping[str, Any], *,
        page_size: int = 50, cursor: str | None = None,
        expected_generation: str | None = None,
    ) -> ResourcePage:
        if (
            isinstance(page_size, bool) or
            not isinstance(page_size, int) or
            not 1 <= page_size <= MAX_PAGE_SIZE
        ):
            raise ResourceAccessError(
                f'page_size must be between 1 and {MAX_PAGE_SIZE}'
            )
        parent = self._resource(parent_payload)
        parent_ref = self._ref(parent)
        self._require_endpoint(context, parent_ref)
        binding = self._binding_for_resources(
            self.provider_registry, context
        )
        self._ensure_contributions(binding)
        generation = self.cache.generation(context)
        self._require_generation(generation, expected_generation)
        offset = self._decode_cursor(
            cursor, parent_ref, generation
        ) if cursor else 0
        resources = self.cache.page(context, parent_ref)
        if resources is None:
            raw = binding.instance.list_resources(copy.deepcopy(parent))
            resources = self._admit_children(context, parent_ref, raw)
            generation = self.cache.put(
                context, parent_ref, resources
            )
        if offset > len(resources):
            raise ResourceAccessError('page cursor offset exceeds result')
        end = min(offset + page_size, len(resources))
        next_cursor = None
        if end < len(resources):
            next_cursor = self._encode_cursor(
                parent_ref, generation, end
            )
        return ResourcePage(
            parent_ref,
            generation,
            tuple(copy.deepcopy(resources[offset:end])),
            next_cursor,
            len(resources),
        )

    def inspect(
        self, context, ref: ResourceRef, *,
        inspector_id: str | None = None,
        expected_generation: str | None = None,
    ) -> Mapping[str, Any]:
        self._require_endpoint(context, ref)
        self._require_generation(
            self.cache.generation(context), expected_generation
        )
        resource = self._known_resource(context, ref)
        binding = self._binding_for_resources(
            self.provider_registry, context
        )
        self._ensure_contributions(binding)
        if inspector_id is None:
            result = binding.instance.inspect_resource(resource)
        else:
            inspector = self.contributions.inspector(
                inspector_id, resource['resource_kind']
            )
            result = inspector.inspect(binding, resource)
        admitted = self._resource(result)
        if self._ref(admitted) != ref:
            raise ResourceAccessError(
                'inspector changed provider resource identity'
            )
        return admitted

    def context_menu(
        self, context, ref: ResourceRef, *, fixture: bool = False
    ) -> tuple[ExplorerAction, ...]:
        self._require_endpoint(context, ref)
        resource = self._known_resource(context, ref)
        binding = self.provider_registry.resolve(context)
        self._ensure_contributions(binding)
        capabilities = frozenset(resource['capability_ids'])
        actions = []
        for command in self.contributions.commands_for(
            resource['resource_kind']
        ):
            if command.capability_id not in capabilities:
                continue
            if fixture and (
                not command.fixture_safe or
                command.mutation_class not in {'none', 'read'}
            ):
                continue
            if command.required_permission and not binding.permissions.allows(
                command.required_permission, 'resource'
            ):
                continue
            try:
                self.security.authorize(
                    context, command.mutation_class
                )
            except RuntimeIdentityError:
                continue
            actions.append(ExplorerAction(
                command.command_id,
                command.title,
                command.mutation_class,
            ))
        return tuple(actions)

    def invoke(
        self, context, ref: ResourceRef, command_id: str,
        payload: Mapping[str, Any] | None = None, *, fixture: bool = False,
    ):
        resource = self._known_resource(context, ref)
        command = self.contributions.command(
            command_id, resource['resource_kind']
        )
        admitted = {
            action.command_id
            for action in self.context_menu(context, ref, fixture=fixture)
        }
        if command_id not in admitted or command.invoke is None:
            raise ResourceAccessError('resource command is not admitted')
        binding = self.provider_registry.resolve(context)
        return command.invoke(binding, resource, dict(payload or {}))

    def explorer_node(
        self, context, ref: ResourceRef, *, fixture: bool = False
    ) -> ExplorerNode:
        resource = self._known_resource(context, ref)
        identity = resource.get('identity', {})
        profile = ' '.join(filter(None, (
            context.profile_id, context.profile_version
        )))
        badges = [
            ExplorerBadge('endpoint-mode', context.mode.replace('_', ' ')),
            ExplorerBadge('profile', profile),
            ExplorerBadge(
                'evidence',
                str(identity.get('evidence_reference', 'unverified')),
            ),
        ]
        if fixture:
            badges.append(ExplorerBadge(
                'fixture', 'NON-PRODUCTION FIXTURE', 'danger'
            ))
        return ExplorerNode(
            resource,
            ref,
            tuple(badges),
            () if fixture else self.context_menu(context, ref),
            fixture,
        )

    def invalidate(self, context) -> str:
        return self.cache.invalidate(context)

    def load_fixture_story(
        self, context, path: Path
    ) -> tuple[ExplorerNode, ...]:
        payload = json.loads(path.read_text(encoding='utf-8'))
        if payload.get('production') is not False:
            raise ResourceAccessError(
                'fixture story must declare production=false'
            )
        resources = tuple(
            self._resource(item) for item in payload.get('resources', [])
        )
        for resource in resources:
            ref = self._ref(resource)
            self._require_endpoint(context, ref)
            marker = resource.get('extensions', {}).get(FIXTURE_MARKER)
            if not isinstance(marker, Mapping) or not marker.get(
                'non_production'
            ):
                raise ResourceAccessError(
                    'fixture resource lacks non-production marker'
                )
        parent_groups: dict[str, list[dict[str, Any]]] = {}
        for resource in resources:
            parent_groups.setdefault(
                str(resource.get('parent_resource_id') or '__root__'), []
            ).append(resource)
        for parent_id, children in parent_groups.items():
            self.cache.put(
                context,
                ResourceRef(context.endpoint_id, parent_id),
                tuple(children),
            )
        return tuple(
            self.explorer_node(context, self._ref(item), fixture=True)
            for item in resources
        )

    def _known_resource(self, context, ref: ResourceRef):
        self._require_endpoint(context, ref)
        resource = self.cache.resource(context, ref)
        if resource is None:
            raise ResourceAccessError(
                'resource is unknown in the current endpoint generation'
            )
        return resource

    def _ensure_contributions(self, binding):
        identity = binding.manifest['identity']
        key = (
            identity['provider_id'], identity['provider_version']
        )
        with self._contribution_lock:
            if key in self._contributed_providers:
                return
            provider_contributions = getattr(
                binding.instance, 'resource_contributions', None
            )
            if callable(provider_contributions):
                payload = provider_contributions()
                if not isinstance(payload, Mapping) or set(payload).difference(
                    {'inspectors', 'commands'}
                ):
                    raise ResourceGraphError(
                        'provider resource contributions are invalid'
                    )
                for inspector in payload.get('inspectors', ()):
                    if not isinstance(
                        inspector, ResourceInspectorContribution
                    ):
                        raise ResourceGraphError(
                            'provider inspector contribution is invalid'
                        )
                    self.contributions.register_inspector(inspector)
                for command in payload.get('commands', ()):
                    if not isinstance(command, ResourceCommandContribution):
                        raise ResourceGraphError(
                            'provider command contribution is invalid'
                        )
                    self.contributions.register_command(command)
            self._contributed_providers.add(key)

    def _admit_children(self, context, parent_ref, raw):
        if not isinstance(raw, (list, tuple)):
            raise ResourceAccessError(
                'provider resource listing must be an array'
            )
        admitted = []
        seen = set()
        for item in raw:
            resource = self._resource(item)
            ref = self._ref(resource)
            self._require_endpoint(context, ref)
            if resource.get('parent_resource_id') != parent_ref.resource_id:
                raise ResourceAccessError(
                    'provider child does not name the requested parent'
                )
            if ref.resource_id in seen:
                raise ResourceAccessError(
                    'provider returned duplicate resource identity'
                )
            seen.add(ref.resource_id)
            admitted.append(resource)
        return tuple(admitted)

    @staticmethod
    def _require_endpoint(context, ref: ResourceRef):
        if ref.endpoint_id != context.endpoint_id:
            raise ResourceAccessError(
                'resource does not belong to the active endpoint'
            )

    @staticmethod
    def _require_generation(current, expected):
        if expected is not None and expected != current:
            raise StaleResourceGenerationError(
                'resource generation is stale'
            )

    @staticmethod
    def _encode_cursor(ref: ResourceRef, generation: str, offset: int):
        payload = json.dumps({
            'endpoint_id': ref.endpoint_id,
            'parent_resource_id': ref.resource_id,
            'generation': generation,
            'offset': offset,
        }, separators=(',', ':'), sort_keys=True).encode('utf-8')
        return base64.urlsafe_b64encode(payload).decode('ascii').rstrip('=')

    @staticmethod
    def _decode_cursor(cursor, ref, generation):
        if not isinstance(cursor, str) or not cursor:
            raise ResourceAccessError('page cursor is invalid')
        try:
            padding = '=' * (-len(cursor) % 4)
            payload = json.loads(base64.urlsafe_b64decode(
                cursor + padding
            ).decode('utf-8'))
        except (
            ValueError, UnicodeDecodeError, json.JSONDecodeError,
            binascii.Error,
        ) as exc:
            raise ResourceAccessError('page cursor is invalid') from exc
        if not isinstance(payload, Mapping):
            raise ResourceAccessError('page cursor is invalid')
        if set(payload) != {
            'endpoint_id', 'parent_resource_id', 'generation', 'offset'
        }:
            raise ResourceAccessError('page cursor is invalid')
        if (
            payload['endpoint_id'] != ref.endpoint_id or
            payload['parent_resource_id'] != ref.resource_id
        ):
            raise ResourceAccessError(
                'page cursor belongs to another resource'
            )
        if payload['generation'] != generation:
            raise StaleResourceGenerationError(
                'page cursor generation is stale'
            )
        offset = payload['offset']
        if (
            isinstance(offset, bool) or
            not isinstance(offset, int) or offset < 0
        ):
            raise ResourceAccessError('page cursor offset is invalid')
        return offset
