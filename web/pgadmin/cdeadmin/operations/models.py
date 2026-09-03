##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Common operation-bus models with provider-owned outcome semantics."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Mapping


RISK_CLASSES = frozenset({'read', 'write', 'admin', 'destructive', 'unknown'})
OPERATION_STAGES = frozenset({
    'draft', 'validated', 'validation_failed', 'previewed',
    'awaiting_approval', 'approved', 'start_requested', 'provider_active',
    'cancel_requested', 'provider_disposition_recorded',
    'local_process_exited', 'unknown_outcome', 'post_state_pending',
    'post_state_validated', 'approval_rejected', 'closed',
})


class OperationBusError(RuntimeError):
    """Base operation-bus error."""


class OperationAccessError(OperationBusError):
    """The principal is not authorized for the requested operation data."""


class OperationStateError(OperationBusError):
    """The requested transition is not safe from the current state."""


class IdempotencyConflictError(OperationBusError):
    """An idempotency key was reused for a different request."""


class AdapterRegistrationError(OperationBusError):
    """An operation adapter registration is invalid or ambiguous."""


class EvidenceUnavailableError(OperationBusError):
    """Evidence is missing, expired, or unavailable to the principal."""


def _string_set(values, field_name):
    if values is None:
        return frozenset()
    if not isinstance(values, (list, tuple, set, frozenset)):
        raise OperationBusError(f'{field_name} must be an array')
    result = frozenset(
        value.strip() for value in values
        if isinstance(value, str) and value.strip()
    )
    if len(result) != len(values):
        raise OperationBusError(
            f'{field_name} contains an invalid or duplicate principal'
        )
    return result


@dataclass(frozen=True)
class OperationRequest:
    """Provider-independent request admitted to the orchestration bus."""

    operation_kind: str
    capability_id: str
    risk_class: str
    adapter_id: str
    target_resource_id: str | None = None
    payload: Mapping[str, Any] = field(default_factory=dict)
    approval_required: bool = False

    def __post_init__(self):
        for name in ('operation_kind', 'capability_id', 'adapter_id'):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise OperationBusError(f'{name} must be a non-empty string')
        if self.risk_class not in RISK_CLASSES:
            raise OperationBusError(
                f'unknown operation risk class {self.risk_class!r}'
            )
        if self.target_resource_id is not None and not isinstance(
            self.target_resource_id, str
        ):
            raise OperationBusError('target_resource_id must be a string')
        if not isinstance(self.payload, Mapping):
            raise OperationBusError('operation payload must be an object')
        if not isinstance(self.approval_required, bool):
            raise OperationBusError('approval_required must be a boolean')

    @classmethod
    def from_value(cls, value):
        if isinstance(value, cls):
            return value
        if not isinstance(value, Mapping):
            raise OperationBusError('operation request must be an object')
        try:
            return cls(
                operation_kind=value['operation_kind'],
                capability_id=value['capability_id'],
                risk_class=value['risk_class'],
                adapter_id=value['adapter_id'],
                target_resource_id=value.get('target_resource_id'),
                payload=value.get('payload', {}),
                approval_required=value.get('approval_required', False),
            )
        except KeyError as exc:
            raise OperationBusError(
                f'operation request is missing {exc.args[0]}'
            ) from exc

    def to_dict(self):
        return {
            'operation_kind': self.operation_kind,
            'capability_id': self.capability_id,
            'risk_class': self.risk_class,
            'adapter_id': self.adapter_id,
            'target_resource_id': self.target_resource_id,
            'payload': dict(self.payload),
            'approval_required': self.approval_required,
        }


@dataclass(frozen=True)
class AccessPolicy:
    """Persisted access grants for one operation and its sensitive records."""

    owner_id: str
    readers: frozenset[str] = field(default_factory=frozenset)
    approvers: frozenset[str] = field(default_factory=frozenset)
    receipt_readers: frozenset[str] = field(default_factory=frozenset)

    def __post_init__(self):
        if not isinstance(self.owner_id, str) or not self.owner_id.strip():
            raise OperationBusError('owner_id must be a non-empty string')
        object.__setattr__(self, 'readers', _string_set(
            self.readers, 'readers'
        ))
        object.__setattr__(self, 'approvers', _string_set(
            self.approvers, 'approvers'
        ))
        object.__setattr__(self, 'receipt_readers', _string_set(
            self.receipt_readers, 'receipt_readers'
        ))

    @property
    def operation_readers(self):
        return self.readers | {self.owner_id}

    @property
    def approval_principals(self):
        return self.approvers | {self.owner_id}

    @property
    def sensitive_readers(self):
        return self.receipt_readers | {self.owner_id}

    def to_dict(self):
        return {
            'owner_id': self.owner_id,
            'readers': sorted(self.readers),
            'approvers': sorted(self.approvers),
            'receipt_readers': sorted(self.receipt_readers),
        }

    @classmethod
    def from_dict(cls, value):
        return cls(
            value['owner_id'],
            frozenset(value.get('readers', ())),
            frozenset(value.get('approvers', ())),
            frozenset(value.get('receipt_readers', ())),
        )


@dataclass(frozen=True)
class RetentionPolicy:
    """Bounded audit/evidence retention without deleting operation identity."""

    max_events_per_operation: int = 1000
    event_ttl: timedelta = timedelta(days=30)
    evidence_ttl: timedelta = timedelta(days=90)

    def __post_init__(self):
        if self.max_events_per_operation < 1:
            raise OperationBusError('event retention limit must be positive')
        if self.event_ttl.total_seconds() <= 0:
            raise OperationBusError('event retention TTL must be positive')
        if self.evidence_ttl.total_seconds() <= 0:
            raise OperationBusError('evidence retention TTL must be positive')


@dataclass(frozen=True)
class AdapterStart:
    """Adapter start response; all provider fields remain opaque."""

    provider_operation: Mapping[str, Any] | None = None
    local_process: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class AdapterObservation:
    """One explicit provider or local-process observation."""

    provider_operation: Mapping[str, Any] | None = None
    local_process: Mapping[str, Any] | None = None
    progress: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class AdapterCancellation:
    """Outcome of dispatching a cancellation request, not work finality."""

    accepted: bool | None
    provider_operation: Mapping[str, Any] | None = None
    detail: Mapping[str, Any] = field(default_factory=dict)
    unknown_outcome: bool = False


@dataclass(frozen=True)
class PostStateResult:
    """Independent post-state validation result."""

    confirmed: bool
    detail: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class EventReplay:
    """Authorized replay window with explicit event-loss signalling."""

    events: tuple[Mapping[str, Any], ...]
    after_sequence: int
    latest_sequence: int
    oldest_available_sequence: int | None
    gap_detected: bool
