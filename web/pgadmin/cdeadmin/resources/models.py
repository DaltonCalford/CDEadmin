##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Resource graph and explorer data-transfer objects."""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping
from uuid import UUID


class ResourceGraphError(RuntimeError):
    """Base error for common resource graph operations."""


class ResourceAccessError(ResourceGraphError):
    """A resource or command cannot be admitted safely."""


class StaleResourceGenerationError(ResourceGraphError):
    """The caller referenced an invalidated explorer generation."""


def _required_string(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ResourceGraphError(f'{field_name} must not be empty')
    return value.strip()


@dataclass(frozen=True)
class ResourceRef:
    """An endpoint-qualified opaque provider resource identifier."""

    endpoint_id: str
    resource_id: str

    def __post_init__(self):
        try:
            endpoint_id = str(UUID(self.endpoint_id))
        except (AttributeError, TypeError, ValueError) as exc:
            raise ResourceGraphError(
                'resource endpoint_id must be a UUID'
            ) from exc
        object.__setattr__(self, 'endpoint_id', endpoint_id)
        object.__setattr__(
            self,
            'resource_id',
            _required_string(self.resource_id, 'resource_id'),
        )


@dataclass(frozen=True)
class ResourcePage:
    """One immutable page from an endpoint-scoped resource generation."""

    parent: ResourceRef
    generation: str
    items: tuple[Mapping[str, Any], ...]
    next_cursor: str | None
    total_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            'parent': {
                'endpoint_id': self.parent.endpoint_id,
                'resource_id': self.parent.resource_id,
            },
            'generation': self.generation,
            'items': copy.deepcopy([dict(item) for item in self.items]),
            'next_cursor': self.next_cursor,
            'total_count': self.total_count,
        }


@dataclass(frozen=True)
class ResourceInspectorContribution:
    """A provider or product inspector for selected resource kinds."""

    inspector_id: str
    resource_kinds: frozenset[str]
    inspect: Callable[[object, Mapping[str, Any]], Mapping[str, Any]]

    def __post_init__(self):
        object.__setattr__(
            self,
            'inspector_id',
            _required_string(self.inspector_id, 'inspector_id'),
        )
        if not self.resource_kinds or not all(
            isinstance(item, str) and item
            for item in self.resource_kinds
        ):
            raise ResourceGraphError(
                'inspector resource_kinds must not be empty'
            )
        if not callable(self.inspect):
            raise ResourceGraphError('inspector callback must be callable')


@dataclass(frozen=True)
class ResourceCommandContribution:
    """A capability and permission gated explorer command."""

    command_id: str
    title: str
    capability_id: str
    resource_kinds: frozenset[str]
    mutation_class: str = 'none'
    required_permission: str | None = None
    invoke: Callable[
        [object, Mapping[str, Any], Mapping[str, Any]], object
    ] | None = None
    fixture_safe: bool = False

    def __post_init__(self):
        for field_name in ('command_id', 'title', 'capability_id'):
            object.__setattr__(
                self,
                field_name,
                _required_string(getattr(self, field_name), field_name),
            )
        if self.mutation_class not in {
            'none', 'read', 'write', 'admin', 'destructive'
        }:
            raise ResourceGraphError('command mutation_class is unknown')
        if not self.resource_kinds:
            raise ResourceGraphError(
                'command resource_kinds must not be empty'
            )
        if self.required_permission is not None:
            object.__setattr__(
                self,
                'required_permission',
                _required_string(
                    self.required_permission, 'required_permission'
                ),
            )


@dataclass(frozen=True)
class ExplorerBadge:
    """Visible endpoint or evidence classification."""

    badge_id: str
    label: str
    tone: str = 'neutral'


@dataclass(frozen=True)
class ExplorerAction:
    """One admitted capability-driven context menu action."""

    command_id: str
    title: str
    mutation_class: str


@dataclass(frozen=True)
class ExplorerNode:
    """Common explorer presentation independent of provider ID syntax."""

    resource: Mapping[str, Any]
    ref: ResourceRef
    badges: tuple[ExplorerBadge, ...] = field(default_factory=tuple)
    actions: tuple[ExplorerAction, ...] = field(default_factory=tuple)
    non_production: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            'resource': copy.deepcopy(dict(self.resource)),
            'ref': {
                'endpoint_id': self.ref.endpoint_id,
                'resource_id': self.ref.resource_id,
            },
            'badges': [badge.__dict__.copy() for badge in self.badges],
            'actions': [action.__dict__.copy() for action in self.actions],
            'non_production': self.non_production,
        }
