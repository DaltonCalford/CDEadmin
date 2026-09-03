##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Opaque protocol transport models for CDEadmin provider packages."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping
from uuid import UUID


ENDPOINT_MODES = frozenset({
    'legacy_native',
    'scratchbird_native',
})
TRANSPORT_KINDS = frozenset({
    'tcp_binary', 'http', 'grpc', 'embedded_helper', 'library_api',
})
CLIENT_STATES = frozenset({
    'selected_installed', 'selected_not_installed', 'boundary_only_unselected',
})


class TransportError(RuntimeError):
    """An opaque transport request cannot be admitted or completed."""


class TransportSelectionError(TransportError):
    """A protocol/client selection record is invalid or unavailable."""


class TransportIsolationError(TransportError):
    """A request attempted to cross an endpoint or credential boundary."""


class TransportUnavailableError(TransportError):
    """A selected client or runtime boundary is not currently available."""


def required_string(value, field_name):
    if not isinstance(value, str) or not value.strip():
        raise TransportError(f'{field_name} must be a non-empty string')
    return value.strip()


def endpoint_uuid(value, field_name='endpoint_id'):
    try:
        return str(UUID(value))
    except (AttributeError, TypeError, ValueError) as exc:
        raise TransportError(f'{field_name} must be a UUID') from exc


def immutable_mapping(value, field_name):
    if not isinstance(value, Mapping):
        raise TransportError(f'{field_name} must be an object')
    return MappingProxyType(dict(value))


def frame_tuple(values, field_name):
    if not isinstance(values, (list, tuple)):
        raise TransportError(f'{field_name} must be an array')
    result = []
    for value in values:
        if not isinstance(value, (bytes, bytearray, memoryview)):
            raise TransportError(f'{field_name} must contain byte frames')
        result.append(bytes(value))
    return tuple(result)


@dataclass(frozen=True)
class ProtocolSelection:
    """One reviewed client boundary; never a semantic provider selection."""

    protocol_id: str
    transport_kind: str
    framing_authority: str
    authentication_authority: str
    client_state: str
    client_package: str | None = None
    client_versions: tuple[str, ...] = field(default_factory=tuple)
    security_controls: frozenset[str] = field(default_factory=frozenset)

    def __post_init__(self):
        for name in (
            'protocol_id', 'framing_authority',
            'authentication_authority',
        ):
            object.__setattr__(
                self, name, required_string(getattr(self, name), name)
            )
        if self.transport_kind not in TRANSPORT_KINDS:
            raise TransportSelectionError('transport kind is invalid')
        if self.client_state not in CLIENT_STATES:
            raise TransportSelectionError('client state is invalid')
        if self.client_package is not None:
            object.__setattr__(
                self,
                'client_package',
                required_string(self.client_package, 'client_package'),
            )
        versions = tuple(
            required_string(item, 'client_versions item')
            for item in self.client_versions
        )
        if len(set(versions)) != len(versions):
            raise TransportSelectionError('client versions contain duplicates')
        if self.client_state.startswith('selected_') and (
            self.client_package is None or not versions
        ):
            raise TransportSelectionError(
                'a selected client requires package and exact version records'
            )
        if self.client_state == 'boundary_only_unselected' and (
            self.client_package is not None or versions
        ):
            raise TransportSelectionError(
                'an unselected client cannot imply a package or version'
            )
        controls = frozenset(
            required_string(item, 'security_controls item')
            for item in self.security_controls
        )
        object.__setattr__(self, 'client_versions', versions)
        object.__setattr__(self, 'security_controls', controls)


@dataclass(frozen=True)
class TransportRequest:
    """Endpoint-bound opaque frames supplied by a semantic provider."""

    endpoint_id: str
    endpoint_mode: str
    provider_id: str
    operation_id: str
    protocol_id: str
    frames: tuple[bytes, ...]
    principal_id: str
    credential_reference_id: str | None = None
    timeout_seconds: float = 30.0
    attributes: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        object.__setattr__(
            self, 'endpoint_id', endpoint_uuid(self.endpoint_id)
        )
        if self.endpoint_mode not in ENDPOINT_MODES:
            raise TransportError('endpoint mode is invalid')
        for name in (
            'provider_id', 'operation_id', 'protocol_id', 'principal_id',
        ):
            object.__setattr__(
                self, name, required_string(getattr(self, name), name)
            )
        object.__setattr__(
            self, 'frames', frame_tuple(self.frames, 'frames')
        )
        if self.credential_reference_id is not None:
            object.__setattr__(
                self,
                'credential_reference_id',
                endpoint_uuid(
                    self.credential_reference_id,
                    'credential_reference_id',
                ),
            )
        if isinstance(self.timeout_seconds, bool) or not isinstance(
            self.timeout_seconds, (int, float)
        ) or self.timeout_seconds <= 0:
            raise TransportError('timeout_seconds must be positive')
        object.__setattr__(
            self,
            'attributes',
            immutable_mapping(self.attributes, 'attributes'),
        )


@dataclass(frozen=True)
class TransportResponse:
    """Opaque returned frames plus transport-only observations."""

    endpoint_id: str
    operation_id: str
    protocol_id: str
    frames: tuple[bytes, ...]
    attributes: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        object.__setattr__(
            self, 'endpoint_id', endpoint_uuid(self.endpoint_id)
        )
        for name in ('operation_id', 'protocol_id'):
            object.__setattr__(
                self, name, required_string(getattr(self, name), name)
            )
        object.__setattr__(
            self, 'frames', frame_tuple(self.frames, 'frames')
        )
        object.__setattr__(
            self,
            'attributes',
            immutable_mapping(self.attributes, 'attributes'),
        )


@dataclass(frozen=True)
class TransportFault:
    """Redacted endpoint-local transport failure evidence."""

    diagnostic_code: str
    endpoint_id: str
    operation_id: str
    protocol_id: str
    error_type: str
    retry_class: str = 'provider_decides'

    def __post_init__(self):
        object.__setattr__(
            self, 'endpoint_id', endpoint_uuid(self.endpoint_id)
        )
        for name in (
            'diagnostic_code', 'operation_id', 'protocol_id', 'error_type',
            'retry_class',
        ):
            object.__setattr__(
                self, name, required_string(getattr(self, name), name)
            )
