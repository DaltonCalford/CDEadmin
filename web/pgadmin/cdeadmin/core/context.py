##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Immutable endpoint context and request-local scope."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Iterable, Iterator, Mapping
from uuid import UUID


ENDPOINT_MODES = frozenset({
    'legacy_native',
    'scratchbird_native',
})
RUNTIME_VERIFICATION_STATES = frozenset({
    'unverified', 'verified', 'mismatch', 'stale', 'failed',
})


class EndpointContextError(ValueError):
    """Endpoint context is missing required or valid isolation identity."""


def _uuid(value: str, field_name: str) -> str:
    try:
        parsed = UUID(value)
    except (AttributeError, TypeError, ValueError) as exc:
        raise EndpointContextError(
            f'{field_name} must be a UUID'
        ) from exc
    return str(parsed)


def _required(value: str | None, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise EndpointContextError(f'{field_name} must not be empty')
    return value.strip()


@dataclass(frozen=True)
class EndpointContext:
    """Provider resolution identity for exactly one endpoint.

    The context carries routing and isolation identity only. Provider-native
    transaction presentation remains opaque and engine-owned.
    """

    endpoint_id: str
    mode: str
    experience_family: str
    provider_id: str
    provider_version: str | None
    profile_id: str
    profile_version: str | None
    target_adapter_id: str
    target_adapter_version: str | None
    pool_namespace: str
    session_namespace: str
    cache_namespace: str
    diagnostic_namespace: str
    effective_permissions: frozenset[str] = field(default_factory=frozenset)
    legacy_driver_type: str | None = None
    declared_runtime_family: str | None = None
    verified_runtime_family: str | None = None
    verified_runtime_version: str | None = None
    runtime_verification_state: str = 'unverified'
    runtime_evidence_reference: str | None = None
    runtime_identity_generation: str | None = None

    def __post_init__(self):
        uuid_fields = (
            'endpoint_id',
            'pool_namespace',
            'session_namespace',
            'cache_namespace',
            'diagnostic_namespace',
        )
        normalized = []
        for name in uuid_fields:
            value = _uuid(getattr(self, name), name)
            object.__setattr__(self, name, value)
            normalized.append(value)
        if len(set(normalized)) != len(normalized):
            raise EndpointContextError(
                'endpoint and isolation namespace UUIDs must be distinct'
            )
        if self.mode not in ENDPOINT_MODES:
            raise EndpointContextError('mode is not supported by contract v1')
        for name in (
            'experience_family', 'provider_id', 'profile_id',
            'target_adapter_id',
        ):
            object.__setattr__(
                self, name, _required(getattr(self, name), name)
            )
        permissions = frozenset(
            _required(value, 'effective_permissions item')
            for value in self.effective_permissions
        )
        object.__setattr__(self, 'effective_permissions', permissions)
        if self.legacy_driver_type is not None:
            object.__setattr__(
                self,
                'legacy_driver_type',
                _required(self.legacy_driver_type, 'legacy_driver_type'),
            )
        if self.runtime_verification_state not in (
            RUNTIME_VERIFICATION_STATES
        ):
            raise EndpointContextError(
                'runtime verification state is invalid'
            )
        for name in (
            'declared_runtime_family', 'verified_runtime_family',
            'verified_runtime_version', 'runtime_evidence_reference',
            'runtime_identity_generation',
        ):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, _required(value, name))

    @property
    def isolation_key(self) -> tuple[str, ...]:
        """Return the complete provider-instance isolation identity."""
        return (
            self.endpoint_id,
            self.mode,
            self.experience_family,
            self.provider_id,
            self.provider_version or '',
            self.profile_id,
            self.profile_version or '',
            self.target_adapter_id,
            self.target_adapter_version or '',
            self.pool_namespace,
            self.session_namespace,
            self.cache_namespace,
            self.diagnostic_namespace,
            self.declared_runtime_family or '',
            self.verified_runtime_family or '',
            self.verified_runtime_version or '',
            self.runtime_verification_state,
            self.runtime_evidence_reference or '',
            self.runtime_identity_generation or '',
            ','.join(sorted(self.effective_permissions)),
        )

    @classmethod
    def from_identity(
        cls,
        identity: Mapping[str, object],
        effective_permissions: Iterable[str] = (),
        legacy_driver_type: str | None = None,
    ) -> 'EndpointContext':
        """Build a context from the additive endpoint compatibility facade."""
        return cls(
            endpoint_id=identity['endpoint_id'],
            mode=identity['endpoint_mode'],
            experience_family=identity['experience_family'],
            provider_id=identity['provider_id'],
            provider_version=identity.get('provider_version'),
            profile_id=identity['profile_id'],
            profile_version=identity.get('profile_version'),
            target_adapter_id=identity['target_adapter_id'],
            target_adapter_version=identity.get('target_adapter_version'),
            pool_namespace=identity['pool_namespace'],
            session_namespace=identity['session_namespace'],
            cache_namespace=identity['cache_namespace'],
            diagnostic_namespace=identity['diagnostic_namespace'],
            effective_permissions=frozenset(effective_permissions),
            legacy_driver_type=legacy_driver_type,
            declared_runtime_family=identity.get(
                'declared_runtime_family'
            ),
            verified_runtime_family=identity.get(
                'verified_runtime_family'
            ),
            verified_runtime_version=identity.get(
                'verified_runtime_version'
            ),
            runtime_verification_state=identity.get(
                'runtime_verification_state', 'unverified'
            ),
            runtime_evidence_reference=identity.get(
                'runtime_evidence_reference'
            ),
            runtime_identity_generation=identity.get(
                'runtime_identity_generation'
            ),
        )


_CURRENT_ENDPOINT_CONTEXT: ContextVar[EndpointContext | None] = ContextVar(
    'cdeadmin_endpoint_context', default=None
)


def current_endpoint_context() -> EndpointContext | None:
    """Return the context bound to the current request/task, if any."""
    return _CURRENT_ENDPOINT_CONTEXT.get()


@contextmanager
def endpoint_scope(context: EndpointContext) -> Iterator[EndpointContext]:
    """Bind an endpoint context and reliably restore the prior scope."""
    if not isinstance(context, EndpointContext):
        raise EndpointContextError('endpoint scope requires EndpointContext')
    token = _CURRENT_ENDPOINT_CONTEXT.set(context)
    try:
        yield context
    finally:
        _CURRENT_ENDPOINT_CONTEXT.reset(token)
