##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Security, runtime-identity, and isolation policy models."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping
from uuid import UUID

from pgadmin.cdeadmin.core.context import ENDPOINT_MODES


AUTHORITY_MODES = {
    'legacy_engine_auth': 'legacy_native',
    'scratchbird_native_auth': 'scratchbird_native',
}
RUNTIME_STATES = frozenset({
    'unverified', 'verified', 'mismatch', 'stale', 'failed',
})
MUTATION_CLASSES = frozenset({
    'none', 'read', 'write', 'admin', 'destructive',
})


class SecurityPolicyError(RuntimeError):
    """Base error for common security policy refusal."""


class SecretAccessError(SecurityPolicyError):
    """A secret reference cannot be resolved for the requested scope."""


class RuntimeIdentityError(SecurityPolicyError):
    """Runtime identity is unverified, mismatched, stale, or invalid."""


class IsolationPolicyError(SecurityPolicyError):
    """An endpoint isolation key request is incomplete or invalid."""


def required_string(value, field_name):
    if not isinstance(value, str) or not value.strip():
        raise SecurityPolicyError(f'{field_name} must be a non-empty string')
    return value.strip()


def endpoint_uuid(value, field_name='endpoint_id'):
    try:
        return str(UUID(value))
    except (AttributeError, TypeError, ValueError) as exc:
        raise SecurityPolicyError(f'{field_name} must be a UUID') from exc


def string_set(values, field_name):
    if not isinstance(values, (list, tuple, set, frozenset)):
        raise SecurityPolicyError(f'{field_name} must be an array')
    result = frozenset(
        value.strip() for value in values
        if isinstance(value, str) and value.strip()
    )
    if len(result) != len(values):
        raise SecurityPolicyError(
            f'{field_name} contains an invalid or duplicate value'
        )
    return result


@dataclass(frozen=True)
class SecretReference:
    """Endpoint/mode-bound pointer to secret material outside endpoint DTOs."""

    reference_id: str
    endpoint_id: str
    endpoint_mode: str
    secret_kind: str
    storage_kind: str
    resolver_id: str
    locator: str = field(repr=False)
    allowed_purposes: frozenset[str] = field(default_factory=frozenset)
    required_permission: str = 'secret_read'
    authority_scope: str = 'legacy_engine_auth'

    def __post_init__(self):
        object.__setattr__(
            self, 'reference_id', endpoint_uuid(
                self.reference_id, 'reference_id'
            )
        )
        object.__setattr__(
            self, 'endpoint_id', endpoint_uuid(self.endpoint_id)
        )
        for name in (
            'secret_kind', 'storage_kind', 'resolver_id', 'locator',
            'required_permission', 'authority_scope',
        ):
            object.__setattr__(
                self, name, required_string(getattr(self, name), name)
            )
        if self.endpoint_mode not in ENDPOINT_MODES:
            raise SecurityPolicyError('secret endpoint mode is invalid')
        expected_mode = AUTHORITY_MODES.get(self.authority_scope)
        if expected_mode is None:
            raise SecurityPolicyError('secret authority scope is invalid')
        if expected_mode != self.endpoint_mode:
            raise SecurityPolicyError(
                'secret authority scope does not match endpoint mode'
            )
        purposes = string_set(self.allowed_purposes, 'allowed_purposes')
        if not purposes:
            raise SecurityPolicyError('allowed_purposes must not be empty')
        object.__setattr__(self, 'allowed_purposes', purposes)


@dataclass(frozen=True)
class RuntimeIdentityClaim:
    """Handshake evidence kept separate from declared endpoint identity."""

    endpoint_id: str
    endpoint_mode: str
    declared_runtime_family: str
    verification_state: str
    verified_runtime_family: str | None = None
    verified_runtime_version: str | None = None
    evidence_reference: str | None = None
    generation: str | None = None

    def __post_init__(self):
        object.__setattr__(
            self, 'endpoint_id', endpoint_uuid(self.endpoint_id)
        )
        object.__setattr__(
            self, 'declared_runtime_family', required_string(
                self.declared_runtime_family, 'declared_runtime_family'
            )
        )
        if self.endpoint_mode not in ENDPOINT_MODES:
            raise SecurityPolicyError('runtime endpoint mode is invalid')
        if self.verification_state not in RUNTIME_STATES:
            raise SecurityPolicyError('runtime verification state is invalid')
        for name in (
            'verified_runtime_family', 'verified_runtime_version',
            'evidence_reference', 'generation',
        ):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, required_string(value, name))


@dataclass(frozen=True)
class CapabilitySnapshot:
    """Endpoint/mode/runtime-bound authorization presentation."""

    endpoint_id: str
    endpoint_mode: str
    generation: str
    runtime_identity_digest: str
    capability_ids: frozenset[str]
    permissions: frozenset[str]
    extensions: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        object.__setattr__(
            self, 'endpoint_id', endpoint_uuid(self.endpoint_id)
        )
        if self.endpoint_mode not in ENDPOINT_MODES:
            raise SecurityPolicyError('snapshot endpoint mode is invalid')
        for name in ('generation', 'runtime_identity_digest'):
            object.__setattr__(
                self, name, required_string(getattr(self, name), name)
            )
        object.__setattr__(self, 'capability_ids', string_set(
            self.capability_ids, 'capability_ids'
        ))
        object.__setattr__(self, 'permissions', string_set(
            self.permissions, 'permissions'
        ))
        if not isinstance(self.extensions, Mapping):
            raise SecurityPolicyError('snapshot extensions must be an object')

    def to_dict(self):
        return {
            'endpoint_id': self.endpoint_id,
            'endpoint_mode': self.endpoint_mode,
            'generation': self.generation,
            'runtime_identity_digest': self.runtime_identity_digest,
            'capability_ids': sorted(self.capability_ids),
            'permissions': sorted(self.permissions),
            'extensions': dict(self.extensions),
        }
