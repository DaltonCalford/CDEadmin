##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Provider-envelope mechanics for client-injected actual-engine pilots.

The injected client and the profile-specific package own engine semantics.
This module validates provider-declared runtime identity policies, isolates
handles and constructs versioned CDEadmin envelopes. It never chooses a
profile from query content, interprets transaction state or retries an
operation.
"""

from __future__ import annotations

import copy
import inspect
import uuid
from dataclasses import dataclass
from typing import Any, Mapping

from pgadmin.cdeadmin.visual_admin import ProviderVisualAdministration


PROVIDER_VERSION = '0.1.0'
CONTRACT_VERSION = '1.1.0'
EVIDENCE_REFERENCE = 'cde-prep-150:actual-engine-pilots-20260831'
TABULAR_RENDERER_ID = 'cdeadmin.result.tabular.legacy-grid'
TABULAR_COMPONENT = 'SchemaView/DataGridView'


class PilotProviderError(RuntimeError):
    """An actual-engine pilot request cannot be handled safely."""


class RuntimeIdentityError(PilotProviderError):
    """The connected runtime is outside the declared provider profile."""


def _mapping(value: object, name='request') -> dict[str, Any]:
    if isinstance(value, Mapping):
        return copy.deepcopy(dict(value))
    to_dict = getattr(value, 'to_dict', None)
    if callable(to_dict):
        return copy.deepcopy(to_dict())
    raise PilotProviderError(f'{name} must be a contract DTO')


def _required(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PilotProviderError(f'{name} must not be empty')
    return value.strip()


@dataclass(frozen=True)
class PilotProfile:
    """All profile decisions owned by one semantic provider package."""

    provider_id: str
    profile_id: str
    engine_id: str
    engine_name: str
    exact_version: str
    protocol_id: str
    model_family: str
    language_profile: str
    language_name: str
    transaction_model: str
    result_kind: str
    resource_kinds: tuple[str, ...]
    admin_tools: tuple[str, ...]
    required_permissions: tuple[str, ...] = ('network',)
    language_mime_type: str = 'text/x-sql'
    result_renderer_kind: str = 'tabular'
    result_renderer_id: str = TABULAR_RENDERER_ID
    result_component_reference: str = TABULAR_COMPONENT
    result_records_field: str = 'rows'
    result_export_formats: tuple[str, ...] = ('csv', 'json')
    result_worker_required: bool = False
    minimum_version: str | None = None
    maximum_version_exclusive: str | None = None
    semantic_sql_dialect: Mapping[str, Any] | None = None
    semantic_materialization_kind: str | None = None
    semantic_materialization_defaults: Mapping[str, Any] | None = None

    def __post_init__(self):
        fields = (
            'provider_id', 'profile_id', 'engine_id', 'engine_name',
            'exact_version', 'protocol_id', 'model_family',
            'language_profile', 'language_name', 'transaction_model',
            'result_kind', 'language_mime_type', 'result_renderer_kind',
            'result_renderer_id', 'result_component_reference',
            'result_records_field',
        )
        for name in fields:
            object.__setattr__(
                self, name, _required(getattr(self, name), name)
            )
        for name in (
            'resource_kinds', 'admin_tools', 'required_permissions',
            'result_export_formats',
        ):
            values = getattr(self, name)
            if not values or len(values) != len(set(values)):
                raise PilotProviderError(
                    f'{name} must be unique and non-empty'
                )
            if any(not isinstance(item, str) or not item for item in values):
                raise PilotProviderError(f'{name} contains an invalid value')
        for name in ('minimum_version', 'maximum_version_exclusive'):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, _required(value, name))
                self._numeric_version(value)
        if self.semantic_sql_dialect is not None:
            if not isinstance(self.semantic_sql_dialect, Mapping):
                raise PilotProviderError(
                    'semantic_sql_dialect must be an object'
                )
        if self.semantic_materialization_kind is not None:
            object.__setattr__(
                self, 'semantic_materialization_kind', _required(
                    self.semantic_materialization_kind,
                    'semantic_materialization_kind',
                )
            )
            if self.semantic_sql_dialect is None:
                raise PilotProviderError(
                    'semantic materialization requires a semantic compiler'
                )
            language = self.semantic_sql_dialect.get('language_profile')
            if language != self.language_profile:
                raise PilotProviderError(
                    'semantic compiler language must match the provider '
                    'profile'
                )
        if self.semantic_materialization_defaults is not None:
            if self.semantic_materialization_kind is None:
                raise PilotProviderError(
                    'semantic materialization defaults require a kind'
                )
            if not isinstance(
                self.semantic_materialization_defaults, Mapping
            ):
                raise PilotProviderError(
                    'semantic_materialization_defaults must be an object'
                )
        if (
            self.minimum_version is not None and
            self.maximum_version_exclusive is not None and
            not self._version_less_than(
                self.minimum_version, self.maximum_version_exclusive
            )
        ):
            raise PilotProviderError(
                'minimum_version must precede maximum_version_exclusive'
            )

    @staticmethod
    def _numeric_version(value):
        parts = str(value).split('.')
        if not parts or any(not item.isdigit() for item in parts):
            raise PilotProviderError(
                'version ranges require dot-separated numeric versions'
            )
        return tuple(int(item) for item in parts)

    @classmethod
    def _version_less_than(cls, left, right):
        left_parts = cls._numeric_version(left)
        right_parts = cls._numeric_version(right)
        width = max(len(left_parts), len(right_parts))
        return left_parts + (0,) * (width - len(left_parts)) < (
            right_parts + (0,) * (width - len(right_parts))
        )

    def accepts_runtime_version(self, observed):
        """Apply the semantic provider's exact or numeric range policy."""
        if self.minimum_version is None:
            return observed == self.exact_version
        try:
            below_minimum = self._version_less_than(
                observed, self.minimum_version
            )
            at_or_above_maximum = (
                self.maximum_version_exclusive is not None and
                not self._version_less_than(
                    observed, self.maximum_version_exclusive
                )
            )
        except PilotProviderError:
            return False
        return not below_minimum and not at_or_above_maximum

    @property
    def version_requirement(self):
        if self.minimum_version is None:
            return self.exact_version
        maximum = (
            f', <{self.maximum_version_exclusive}'
            if self.maximum_version_exclusive is not None else ''
        )
        return f'>={self.minimum_version}{maximum}'


@dataclass
class _Session:
    handle: object
    route_id: str


@dataclass
class _Operation:
    token: object
    execution_id: str
    operation: dict[str, Any]


class ActualEnginePilotProvider:
    """Fail-closed facade over one profile-specific client adapter."""

    def __init__(self, context, permissions, client, profile: PilotProfile):
        if client is None:
            raise PilotProviderError('approved client adapter is unavailable')
        self.context = context
        self.permissions = permissions
        self.client = client
        self.profile = profile
        self._sessions: dict[str, _Session] = {}
        self._operations: dict[str, _Operation] = {}
        self._visual_admin = ProviderVisualAdministration(
            context,
            permissions,
            profile.engine_id,
            profile.exact_version,
            client,
        )

    def visual_admin_descriptor(self):
        """Return this provider's declarative visual administration surface."""
        return self._visual_admin.descriptor()

    def validate_visual_admin(self, request):
        """Validate a visual draft without generating engine commands."""
        return self._visual_admin.validate(request)

    def plan_visual_admin(self, request):
        """Ask the target adapter for a native, non-executing plan."""
        return self._visual_admin.plan(request)

    def apply_visual_admin(self, request):
        """Execute a retained provider plan through the target adapter."""
        return self._visual_admin.apply(request)

    def read_visual_admin_rows(self, request):
        """Read a provider-identified editable page from one base table."""
        return self._visual_admin.read_rows(request)

    def cancel_visual_admin_rows(self, request):
        """Cancel a provider-retained editable-data cursor."""
        return self._visual_admin.cancel_rows(request)

    def list_visual_admin_operations(self, request=None):
        """List endpoint-local provider operation observations."""
        if request is not None:
            _mapping(request)
        return self._visual_admin.list_operations()

    def get_visual_admin_operation(self, request):
        """Return one endpoint-local provider operation observation."""
        return self._visual_admin.get_operation(request)

    def refresh_visual_admin_operation(self, request):
        """Ask the provider for a fresh operation observation."""
        return self._visual_admin.refresh_operation(request)

    def cancel_visual_admin_operation(self, request):
        """Dispatch one provider-owned cancellation request."""
        return self._visual_admin.cancel_operation(request)

    def validate_visual_admin_post_state(self, request):
        """Ask the provider to independently validate post-state."""
        return self._visual_admin.validate_operation_post_state(request)

    def semantic_model_descriptor(self):
        """Describe provider-owned semantic compilation availability."""
        available = self.profile.semantic_sql_dialect is not None
        return {
            'provider_id': self.profile.provider_id,
            'engine_id': self.profile.engine_id,
            'model_family': self.profile.model_family,
            'execution_available': available,
            'language_profile': (
                self.profile.semantic_sql_dialect.get('language_profile')
                if available else None
            ),
            'materialization': {
                'execution_available': (
                    self.profile.semantic_materialization_kind is not None
                ),
                'resource_kind': self.profile.semantic_materialization_kind,
                'provider_planned': True,
            },
            'reason': None if available else (
                'Provider has not declared a semantic-query compiler'
            ),
        }

    def describe_semantics(self, request):
        """Implement the SDK semantic layer discovery contract."""
        _mapping(request)
        descriptor = self.semantic_model_descriptor()
        return self._resource({
            'resource_id': (
                f'{self.profile.engine_id}:semantic-model-descriptor'
            ),
            'resource_kind': 'semantic-model-descriptor',
            'display_name': (
                f'{self.profile.engine_name} semantic model support'
            ),
            'authority_path': [
                self.profile.engine_id, 'semantic-model-descriptor',
            ],
            'generation': self.profile.exact_version,
            'is_virtual': True,
            'capability_ids': (
                [f'{self.profile.engine_id}.semantic-query.execute']
                if descriptor['execution_available'] else []
            ),
            'native': descriptor,
        })

    def compile_semantic_query(self, model, query):
        """Invoke the compiler using only this provider's declared dialect."""
        if self.profile.semantic_sql_dialect is None:
            from pgadmin.cdeadmin.semantic_models import (
                SemanticCompilationUnavailable,
            )
            raise SemanticCompilationUnavailable(
                'endpoint provider has no semantic-query compiler'
            )
        from pgadmin.cdeadmin.semantic_models.compiler import compile_sql
        return compile_sql(model, query, self.profile.semantic_sql_dialect)

    def execute_analysis(self, request):
        """Implement provider-owned compile-and-execute analysis."""
        payload = _mapping(request)
        compiled = self.compile_semantic_query(
            payload.get('semantic_model'), payload.get('semantic_query')
        )
        payload['source'] = compiled['source']
        payload['parameters'] = compiled.get('parameters', {})
        payload['language_profile'] = compiled['language_profile']
        return self.execute(payload)

    def plan_semantic_materialization(self, request):
        """Plan a generated rollup through the provider admin contract."""
        kind = self.profile.semantic_materialization_kind
        if kind is None:
            raise PilotProviderError(
                'provider has no semantic materialization contract'
            )
        payload = _mapping(request)
        compiled = _mapping(payload.get('compiled'), 'compiled query')
        item = _mapping(payload.get('materialization'), 'materialization')
        name = _required(item.get('name'), 'materialization.name')
        target = item.get('target') or []
        if not isinstance(target, list) or not all(
            isinstance(part, str) and part for part in target
        ):
            raise PilotProviderError(
                'materialization.target must be an identifier path'
            )
        draft = copy.deepcopy(dict(
            self.profile.semantic_materialization_defaults or {}
        ))
        draft.update({'name': name, 'select': _required(
            compiled.get('source'), 'compiled.source'
        )})
        if target:
            draft['database'] = target[0]
        admin_request = {
            'resource_kind': kind,
            'operation_id': 'create',
            'target_resource': None,
            'draft': draft,
            '_provider_route': payload.get('_provider_route'),
        }
        checked = self._visual_admin.validate(admin_request)
        if not checked['valid']:
            raise PilotProviderError(
                'semantic materialization failed provider validation'
            )
        return self._visual_admin.plan(admin_request)

    def _identity(self):
        return {
            'contract_version': CONTRACT_VERSION,
            'provider_id': self.profile.provider_id,
            'provider_version': PROVIDER_VERSION,
            'profile_id': self.profile.profile_id,
            'profile_version': self.profile.exact_version,
            'evidence_reference': EVIDENCE_REFERENCE,
        }

    def _extension(self, value):
        return {self.profile.engine_id: value}

    @property
    def result_render_capability(self):
        return (
            f'{self.profile.engine_id}.result.'
            f'{self.profile.result_renderer_kind}.render'
        )

    def _require(self, permission_id, scope='endpoint'):
        self.permissions.require(permission_id, scope)

    def _runtime_identity(self, request, handle=None):
        runtime_identity = self.client.runtime_identity
        try:
            inspect.signature(runtime_identity).bind(request, handle)
        except (TypeError, ValueError):
            raw_identity = runtime_identity()
        else:
            raw_identity = runtime_identity(request, handle)
        identity = _mapping(
            raw_identity,
            'runtime identity',
        )
        required = ('engine_id', 'version', 'build_id', 'protocol_id')
        for name in required:
            _required(identity.get(name), f'runtime_identity.{name}')
        expected = {
            'engine_id': self.profile.engine_id,
            'protocol_id': self.profile.protocol_id,
        }
        mismatches = {
            name: {'expected': value, 'observed': identity.get(name)}
            for name, value in expected.items()
            if identity.get(name) != value
        }
        if not self.profile.accepts_runtime_version(identity['version']):
            mismatches['version'] = {
                'expected': self.profile.version_requirement,
                'observed': identity['version'],
            }
        if mismatches:
            raise RuntimeIdentityError(
                'runtime identity does not match the provider profile'
            )
        return identity

    def _diagnostic(self, code, severity='info', retryable=False,
                    exception_type=None):
        details = {}
        if exception_type:
            details['exception_type'] = exception_type
        return {
            'identity': self._identity(),
            'diagnostic_id': str(uuid.uuid4()),
            'severity': severity,
            'code': code,
            'message': f'{self.profile.engine_name} provider diagnostic',
            'retryable': bool(retryable),
            'details': details,
            'extensions': self._extension({'provider_owned': True}),
        }

    def validate_endpoint(self, request):
        payload = _mapping(request)
        route = payload.get('route')
        if not isinstance(route, Mapping) or not route:
            return self._diagnostic(
                'CDE_ACTUAL_ENDPOINT_INVALID', severity='error'
            )
        forbidden = {'password', 'secret', 'token', 'credential'}
        if forbidden.intersection(key.lower() for key in route):
            return self._diagnostic(
                'CDE_ACTUAL_INLINE_CREDENTIAL_FORBIDDEN', severity='error'
            )
        return self._diagnostic('CDE_ACTUAL_ENDPOINT_STRUCTURALLY_VALID')

    def discover_endpoint(self, request):
        for permission in self.profile.required_permissions:
            self._require(permission)
        payload = _mapping(request)
        runtime = self._runtime_identity(payload)
        payload['identity'] = self._identity()
        payload['verified_runtime'] = {
            **runtime,
            'verification_state': 'verified',
            'evidence_reference': EVIDENCE_REFERENCE,
        }
        return payload

    def _resource(self, native, parent_id=None):
        native = _mapping(native, 'native resource')
        kind = _required(native.get('resource_kind'), 'resource_kind')
        common_virtual = {
            'language-profile', 'tool', 'security-descriptor',
            'semantic-model-descriptor',
        }
        if (
            kind not in self.profile.resource_kinds and
            kind not in common_virtual
        ):
            raise PilotProviderError(
                f'client returned unadmitted resource kind {kind!r}'
            )
        resource_id = _required(native.get('resource_id'), 'resource_id')
        name = _required(native.get('display_name'), 'display_name')
        authority = native.get('authority_path')
        if not isinstance(authority, list) or not authority:
            raise PilotProviderError(
                'authority_path must be a non-empty array'
            )
        return {
            'identity': self._identity(),
            'endpoint_id': self.context.endpoint_id,
            'resource_id': resource_id,
            'identity_kind': 'provider-native-id',
            'resource_kind': kind,
            'model_family': self.profile.model_family,
            'display_name': name,
            'parent_resource_id': parent_id,
            'display_path': list(native.get('display_path') or [name]),
            'authority_path': list(authority),
            'is_virtual': bool(native.get('is_virtual', False)),
            'generation': _required(
                native.get('generation'), 'resource generation'
            ),
            'capability_ids': list(native.get('capability_ids') or []),
            'extensions': self._extension({
                'provider_owned': True,
                'native': native.get('native', {}),
            }),
        }

    def list_resources(self, request):
        self._require('data_read', 'resource')
        payload = _mapping(request)
        resources = self.client.list_resources(payload)
        if not isinstance(resources, (list, tuple)):
            raise PilotProviderError('client resource result must be an array')
        return [
            self._resource(item, payload.get('resource_id'))
            for item in resources
        ]

    def inspect_resource(self, request):
        self._require('data_read', 'resource')
        payload = _mapping(request)
        return self._resource(
            self.client.inspect_resource(payload),
            payload.get('parent_resource_id'),
        )

    def describe_language(self, request):
        _mapping(request)
        return [self._resource({
            'resource_id': f'{self.profile.engine_id}:language:'
                           f'{self.profile.language_profile}',
            'resource_kind': 'language-profile',
            'display_name': self.profile.language_name,
            'authority_path': [
                self.profile.engine_id, 'language',
                self.profile.language_profile,
            ],
            'generation': self.profile.exact_version,
            'is_virtual': True,
            'native': {'language_profile': self.profile.language_profile},
        })]

    @staticmethod
    def _studio_open_session(binding, request):
        return binding.instance.open_session(request)

    @staticmethod
    def _studio_describe_transaction(binding, request):
        return binding.instance.describe_transaction(request)

    @staticmethod
    def _studio_execute(binding, request):
        return binding.instance.execute(request)

    @staticmethod
    def _studio_poll(binding, request):
        result = binding.instance.describe_result(request)
        operation = binding.instance.get_operation(request)
        return operation, result

    @staticmethod
    def _studio_cancel(binding, request):
        return binding.instance.cancel(request)

    def data_studio_contributions(self):
        """Expose the exact provider profile through common studio ports."""
        from pgadmin.cdeadmin.data_studio import (
            ExecutionContribution,
            LanguageContribution,
            SessionContribution,
        )

        profiles = frozenset({self.profile.language_profile})
        prefix = f'{self.profile.engine_id}.data-studio'
        return {
            'languages': (
                LanguageContribution(
                    self.profile.language_profile,
                    self.profile.language_name,
                    self.profile.language_mime_type,
                    frozenset({self.profile.model_family}),
                ),
            ),
            'sessions': (
                SessionContribution(
                    f'{prefix}.session',
                    profiles,
                    ActualEnginePilotProvider._studio_open_session,
                    ActualEnginePilotProvider._studio_describe_transaction,
                ),
            ),
            'executions': (
                ExecutionContribution(
                    f'{prefix}.execution',
                    profiles,
                    ActualEnginePilotProvider._studio_execute,
                    ActualEnginePilotProvider._studio_poll,
                    ActualEnginePilotProvider._studio_cancel,
                ),
            ),
        }

    @staticmethod
    def _describe_provider_result(binding, result):
        provider = binding.instance
        engine_id = provider.profile.engine_id
        extension = result.get('extensions', {}).get(engine_id, {})
        payload = extension.get('payload') or {}
        if isinstance(payload, Mapping):
            records = payload.get(
                provider.profile.result_records_field
            ) or []
        elif isinstance(payload, list):
            records = payload
        else:
            records = []
        if not isinstance(records, list):
            raise PilotProviderError(
                'provider result records must be an array'
            )
        columns = result.get('schema', {}).get('columns', [])
        names = [
            item.get('name') for item in columns
            if isinstance(item, Mapping) and isinstance(item.get('name'), str)
        ]
        normalized = []
        for record in records:
            if isinstance(record, Mapping):
                normalized.append(copy.deepcopy(dict(record)))
            elif (
                isinstance(record, (list, tuple)) and
                len(names) == len(record)
            ):
                normalized.append(dict(zip(
                    names, copy.deepcopy(list(record))
                )))
            else:
                normalized.append(copy.deepcopy(record))
        return {
            'descriptor_version': '1.0.0',
            'capability_id': provider.result_render_capability,
            'records': normalized,
            'limits': {
                'max_records': 10000,
                'max_page_size': 500,
                'max_record_bytes': 256 * 1024,
                'max_descriptor_bytes': 4 * 1024 * 1024,
            },
            'sampling': {
                'mode': 'head',
                'limit': min(max(len(records), 1), 10000),
            },
            'export_policy': {
                'enabled': True,
                'formats': list(
                    provider.profile.result_export_formats
                ),
                'max_records': 10000,
                'max_bytes': 8 * 1024 * 1024,
                'redact_keys': [],
            },
            'worker_policy': {
                'required': provider.profile.result_worker_required,
                'timeout_seconds': 2.0,
            },
            'renderer_id': provider.profile.result_renderer_id,
            'component_reference': (
                provider.profile.result_component_reference
            ),
        }

    def result_contributions(self):
        """Normalize exact-profile results for a provider-selected renderer."""
        from pgadmin.cdeadmin.results import ResultAdapterContribution

        return {
            'adapters': (
                ResultAdapterContribution(
                    f'{self.profile.engine_id}.result.'
                    f'{self.profile.result_renderer_kind}.adapter',
                    frozenset({self.profile.result_kind}),
                    self.result_render_capability,
                    ActualEnginePilotProvider._describe_provider_result,
                ),
            ),
        }

    def open_session(self, request):
        for permission in self.profile.required_permissions:
            self._require(permission)
        payload = _mapping(request)
        handle = self.client.open_session(payload)
        try:
            self._runtime_identity(payload, handle)
        except Exception:
            close = getattr(handle, 'close', None)
            if callable(close):
                close()
            raise
        session_id = str(uuid.uuid4())
        route_id = str(payload.get('route', {}).get('route_id', 'direct'))
        self._sessions[session_id] = _Session(handle, route_id)
        return {
            'identity': self._identity(),
            'session_id': session_id,
            'endpoint_id': self.context.endpoint_id,
            'route_id': route_id,
            'principal_reference': str(
                payload.get('route', {}).get('principal_reference', 'endpoint')
            ),
            'language_profile': self.profile.language_profile,
            'transaction_model': self.profile.transaction_model,
            'provider_state': {'handle_retained_by_provider': True},
            'occurrence_id': self.context.session_namespace,
            'limits': {},
            'extensions': self._extension({'provider_owned': True}),
        }

    def describe_transaction(self, request):
        payload = _mapping(request)
        session_id = payload.get('session_id')
        session = self._sessions.get(session_id)
        if session is None:
            raise PilotProviderError('provider session is unavailable')
        state = _mapping(
            self.client.describe_transaction(session.handle),
            'transaction presentation',
        )
        return {
            'identity': self._identity(),
            'session_id': session_id,
            'transaction_model': self.profile.transaction_model,
            'provider_payload': state,
            'authority_reference': f'provider:{self.profile.provider_id}',
            'extensions': self._extension({
                'opaque_provider_value': True,
                'common_finality_inference': False,
            }),
        }

    def execute(self, request):
        self._require('execute')
        payload = _mapping(request)
        session = self._sessions.get(payload.get('session_id'))
        if session is None:
            raise PilotProviderError('provider session is unavailable')
        token = self.client.execute(session.handle, payload)
        operation_id = str(uuid.uuid4())
        operation = {
            'identity': self._identity(),
            'operation_id': operation_id,
            'operation_kind': f'{self.profile.engine_id}-execute',
            'target_resource_id': payload.get('target_resource_id'),
            'capability_id': f'{self.profile.engine_id}.query.execute',
            'risk_class': 'unknown',
            'provider_state': {'token_retained_by_provider': True},
            'terminal': False,
            'provider_receipt': None,
            'extensions': self._extension({'provider_owned': True}),
        }
        self._operations[operation_id] = _Operation(
            token, str(payload.get('execution_id', '')), operation
        )
        return copy.deepcopy(operation)

    def cancel(self, request):
        self._require('execute')
        operation = self._operation(_mapping(request).get('operation_id'))
        accepted = bool(self.client.cancel(operation.token))
        operation.operation['provider_receipt'] = {
            'cancel_request_accepted': accepted,
            'outcome': 'pending-provider-observation',
        }
        return copy.deepcopy(operation.operation)

    def describe_result(self, request):
        operation = self._operation(_mapping(request).get('operation_id'))
        native = _mapping(
            self.client.describe_result(operation.token), 'result'
        )
        kind = native.get('result_kind')
        if kind != self.profile.result_kind:
            raise PilotProviderError(
                'client returned an unadmitted result kind'
            )
        complete = bool(native.get('complete', False))
        operation.operation['terminal'] = complete
        return {
            'identity': self._identity(),
            'result_id': str(uuid.uuid4()),
            'execution_id': operation.execution_id,
            'result_kind': kind,
            'schema': dict(native.get('schema') or {}),
            'stream_reference': native.get('stream_reference'),
            'complete': complete,
            'continuation': None if complete else operation.operation[
                'operation_id'
            ],
            'extensions': self._extension({
                'provider_owned': True,
                'payload': native.get('payload'),
            }),
        }

    def select_renderer(self, request):
        """Select this profile's admitted bounded result renderer."""
        payload = _mapping(request)
        if payload.get('result_kind') != self.profile.result_kind:
            raise PilotProviderError(
                'provider has no renderer for this result kind'
            )
        return {
            'identity': self._identity(),
            'endpoint_id': self.context.endpoint_id,
            'resource_id': (
                f'{self.profile.engine_id}:renderer:'
                f'{self.profile.result_renderer_kind}'
            ),
            'identity_kind': 'provider-renderer-id',
            'resource_kind': 'result-renderer',
            'model_family': self.profile.model_family,
            'display_name': (
                f'{self.profile.engine_name} '
                f'{self.profile.result_renderer_kind} result'
            ),
            'parent_resource_id': None,
            'display_path': [
                self.profile.engine_name, 'Result renderers',
                self.profile.result_renderer_kind,
            ],
            'authority_path': [
                self.profile.engine_id, 'result-renderer',
                self.profile.result_renderer_kind,
            ],
            'is_virtual': True,
            'generation': self.context.cache_namespace,
            'capability_ids': [self.result_render_capability],
            'extensions': self._extension({
                'component_reference': (
                    self.profile.result_component_reference
                ),
                'provider_owned': True,
            }),
        }

    def translate_diagnostic(self, request):
        payload = _mapping(request)
        return self._diagnostic(
            str(payload.get('code') or 'CDE_ACTUAL_PROVIDER_FAULT'),
            severity=str(payload.get('severity') or 'error'),
            retryable=bool(payload.get('retryable', False)),
            exception_type=payload.get('exception_type'),
        )

    def get_operation(self, request):
        return copy.deepcopy(
            self._operation(_mapping(request).get('operation_id')).operation
        )

    def list_tools(self, request):
        _mapping(request)
        return [self._resource({
            'resource_id': f'{self.profile.engine_id}:tool:{tool}',
            'resource_kind': 'tool',
            'display_name': tool,
            'authority_path': [self.profile.engine_id, 'tool', tool],
            'generation': self.profile.exact_version,
            'is_virtual': True,
            'native': {'common_service': False},
        }) for tool in self.profile.admin_tools]

    def describe_security(self, request):
        self._require('data_read', 'resource')
        payload = _mapping(request)
        native = _mapping(
            self.client.describe_security(payload), 'security descriptor'
        )
        forbidden = {'password', 'secret', 'token'}
        if any(key.lower() in forbidden for key in native):
            raise PilotProviderError(
                'security descriptor contains secret data'
            )
        return self._resource({
            'resource_id': _required(native.get('resource_id'), 'resource_id'),
            'resource_kind': 'security-descriptor',
            'display_name': _required(
                native.get('display_name'), 'display_name'
            ),
            'authority_path': list(native.get('authority_path') or []),
            'generation': _required(native.get('generation'), 'generation'),
            'is_virtual': True,
            'native': native.get('native', {}),
        }, payload.get('resource_id'))

    def _operation(self, operation_id):
        try:
            return self._operations[operation_id]
        except KeyError as exc:
            raise PilotProviderError(
                'provider operation is unavailable'
            ) from exc

    def close(self):
        self._sessions.clear()
        self._operations.clear()
        close = getattr(self.client, 'close', None)
        if callable(close):
            close()
