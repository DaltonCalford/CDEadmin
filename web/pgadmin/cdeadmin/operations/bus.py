##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Evidence-backed common operation bus."""

from __future__ import annotations

import copy
import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Mapping

from pgadmin.cdeadmin.contracts.v1.runtime import validate_contract
from pgadmin.cdeadmin.security.redaction import redact

from .models import (
    AccessPolicy,
    AdapterCancellation,
    AdapterObservation,
    AdapterRegistrationError,
    AdapterStart,
    EventReplay,
    EvidenceUnavailableError,
    IdempotencyConflictError,
    OperationAccessError,
    OperationBusError,
    OperationRequest,
    OperationStateError,
    PostStateResult,
    RetentionPolicy,
)
from .store import MemoryOperationStore


BUS_IDENTITY = {
    'contract_version': '1.0.0',
    'provider_id': 'org.cdeadmin.operation-bus',
    'provider_version': '1.0.0',
    'profile_id': 'cdeadmin-common-operations',
    'profile_version': '1.0.0',
    'evidence_reference': 'cde-prep-090:common-operation-bus',
}

_VISUAL_AUDIT_FIELDS = frozenset({
    'schema', 'operation_id', 'plan_id', 'engine_id', 'resource_kind',
    'operation_kind', 'target_resource_id', 'stage', 'provider_result',
    'impact', 'long_running', 'cancellable', 'cancel_request_dispatched',
    'cancel_response', 'unknown_outcome', 'post_state_required', 'post_state',
    'provider_finality_authority', 'automatic_mutation_retry', 'created_at',
    'updated_at', 'last_event_sequence', 'events', 'observation_blocker',
    'provider_observation',
})
_AUDIT_MAX_BYTES = 1024 * 1024
_AUDIT_MAX_TEXT = 4 * 1024
_AUDIT_MAX_ITEMS = 512
_AUDIT_MAX_DEPTH = 12


def _bounded_audit_value(value, depth=0):
    """Bound provider-owned audit detail without changing live results."""
    if depth >= _AUDIT_MAX_DEPTH:
        return {'audit_value_omitted': True, 'reason': 'depth_limit'}
    if isinstance(value, Mapping):
        items = list(value.items())
        result = {
            str(key): _bounded_audit_value(item, depth + 1)
            for key, item in items[:_AUDIT_MAX_ITEMS]
        }
        if len(items) > _AUDIT_MAX_ITEMS:
            result['_audit_omitted_fields'] = (
                len(items) - _AUDIT_MAX_ITEMS
            )
        return result
    if isinstance(value, (list, tuple)):
        result = [
            _bounded_audit_value(item, depth + 1)
            for item in value[:_AUDIT_MAX_ITEMS]
        ]
        if len(value) > _AUDIT_MAX_ITEMS:
            result.append({
                'audit_items_omitted': len(value) - _AUDIT_MAX_ITEMS,
            })
        return result
    if isinstance(value, str) and len(value.encode('utf-8')) > _AUDIT_MAX_TEXT:
        encoded = value.encode('utf-8')[:_AUDIT_MAX_TEXT]
        return encoded.decode('utf-8', errors='ignore') + '[AUDIT TRUNCATED]'
    return copy.deepcopy(value)


def _bounded_provider_audit(record):
    # Bound untrusted provider strings before regular-expression redaction so
    # a pathological multi-megabyte response cannot monopolize the request.
    public = redact(_bounded_audit_value({
        key: copy.deepcopy(value)
        for key, value in record.items()
        if key in _VISUAL_AUDIT_FIELDS
    }))
    if len(json.dumps(
            public, sort_keys=True, separators=(',', ':'), default=str
    ).encode('utf-8')) <= _AUDIT_MAX_BYTES:
        return public
    for field_name in (
        'provider_result', 'provider_observation', 'cancel_response',
        'post_state', 'impact',
    ):
        if field_name in public:
            public[field_name] = {
                'audit_value_omitted': True,
                'reason': 'record_size_limit',
            }
        if len(json.dumps(
                public, sort_keys=True, separators=(',', ':'), default=str
        ).encode('utf-8')) <= _AUDIT_MAX_BYTES:
            return public
    public['events'] = list(public.get('events') or [])[-50:]
    return public


def utc_now():
    return datetime.now(timezone.utc)


def iso_time(value):
    return value.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')


def parse_time(value):
    if not isinstance(value, str):
        raise OperationBusError('timestamp must be a string')
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError as exc:
        raise OperationBusError(f'invalid timestamp {value!r}') from exc


def _fingerprint(value):
    try:
        encoded = json.dumps(
            value, sort_keys=True, separators=(',', ':'),
            ensure_ascii=True, default=str,
        ).encode('utf-8')
    except (TypeError, ValueError) as exc:
        raise OperationBusError(
            'operation request cannot be normalized for idempotency'
        ) from exc
    return hashlib.sha256(encoded).hexdigest()


class OperationBus:
    """Application-level operation lifecycle and authorized event stream.

    The bus controls orchestration state only. Provider operations, receipts,
    transaction presentations, and finality claims are validated and retained
    as opaque provider-owned values.
    """

    def __init__(self, store=None, retention=None, clock=utc_now):
        self.store = store or MemoryOperationStore()
        self.retention = retention or RetentionPolicy()
        self._clock = clock
        self._adapters = {}

    def register_adapter(self, adapter_id, adapter):
        if not isinstance(adapter_id, str) or not adapter_id.strip():
            raise AdapterRegistrationError(
                'adapter_id must be a non-empty string'
            )
        for method in (
            'validate', 'preview', 'start', 'inspect', 'cancel',
            'validate_post_state',
        ):
            if not callable(getattr(adapter, method, None)):
                raise AdapterRegistrationError(
                    f'adapter {adapter_id!r} lacks {method}()'
                )
        if adapter_id in self._adapters:
            raise AdapterRegistrationError(
                f'adapter {adapter_id!r} is already registered'
            )
        self._adapters[adapter_id] = adapter

    def record_provider_audit(self, endpoint_id, principal, record):
        """Durably retain only a provider operation's public presentation."""
        if not isinstance(endpoint_id, str) or not endpoint_id:
            raise OperationBusError('audit endpoint_id must not be empty')
        if not isinstance(principal, str) or not principal:
            raise OperationBusError('audit principal must not be empty')
        if not isinstance(record, Mapping):
            raise OperationBusError('provider audit record must be an object')
        operation_id = record.get('operation_id')
        if not isinstance(operation_id, str) or not operation_id:
            raise OperationBusError(
                'provider audit operation_id must not be empty'
            )
        public = _bounded_provider_audit(record)
        if public.get('provider_finality_authority') is not True or \
                public.get('automatic_mutation_retry') is not False:
            raise OperationBusError(
                'provider audit record violates finality authority'
            )

        def change(state):
            records = state['provider_audit']
            existing = records.get(operation_id)
            if existing is not None and (
                existing['endpoint_id'] != endpoint_id or
                existing['principal'] != principal
            ):
                raise OperationAccessError(
                    'provider audit identity belongs to another owner'
                )
            records[operation_id] = {
                'endpoint_id': endpoint_id,
                'principal': principal,
                'record': public,
            }
            owned = [
                (key, value) for key, value in records.items()
                if value['endpoint_id'] == endpoint_id and
                value['principal'] == principal
            ]
            if len(owned) > 1024:
                oldest = min(
                    owned,
                    key=lambda item: item[1]['record'].get(
                        'updated_at', item[1]['record'].get('created_at', '')
                    ),
                )[0]
                records.pop(oldest, None)
            return copy.deepcopy(public)

        return self.store.change(change)

    def list_provider_audit(self, endpoint_id, principal):
        """Return the caller-owned durable control-plane audit history."""
        state = self.store.read()
        values = [
            copy.deepcopy(value['record'])
            for value in state['provider_audit'].values()
            if value['endpoint_id'] == endpoint_id and
            value['principal'] == principal
        ]
        return sorted(
            values,
            key=lambda item: item.get(
                'updated_at', item.get('created_at', '')
            ),
            reverse=True,
        )

    def get_provider_audit(self, endpoint_id, principal, operation_id):
        """Read one caller-owned durable control-plane audit record."""
        state = self.store.read()
        value = state['provider_audit'].get(operation_id)
        if value is None:
            raise OperationStateError('provider audit record is unavailable')
        if value['endpoint_id'] != endpoint_id or \
                value['principal'] != principal:
            raise OperationAccessError(
                'provider audit access is not authorized'
            )
        return copy.deepcopy(value['record'])

    def _adapter(self, operation):
        adapter_id = operation['request']['adapter_id']
        try:
            return self._adapters[adapter_id]
        except KeyError as exc:
            raise AdapterRegistrationError(
                f'adapter {adapter_id!r} is not registered'
            ) from exc

    @staticmethod
    def _operation(state, operation_id):
        try:
            return state['operations'][operation_id]
        except KeyError as exc:
            raise OperationStateError(
                f'operation {operation_id!r} does not exist'
            ) from exc

    @staticmethod
    def _policy(operation):
        return AccessPolicy.from_dict(operation['access'])

    def _authorize(self, operation, principal, sensitive=False):
        if not isinstance(principal, str) or not principal:
            raise OperationAccessError('principal must be explicit')
        policy = self._policy(operation)
        readers = (
            policy.sensitive_readers if sensitive
            else policy.operation_readers
        )
        if principal not in readers:
            raise OperationAccessError('operation access is not authorized')

    def _emit(self, state, operation, event_kind, payload=None):
        sequence = operation['last_sequence'] + 1
        event = validate_contract('Event', {
            'identity': copy.deepcopy(BUS_IDENTITY),
            'event_id': str(uuid.uuid4()),
            'event_kind': event_kind,
            'occurred_at': iso_time(self._clock()),
            'sequence': sequence,
            'subject_id': operation['operation_id'],
            'payload': redact(payload or {}),
            'extensions': {
                'cdeadmin': {
                    'correlation_id': operation['correlation_id'],
                    'orchestration_stage': operation['stage'],
                    'authority': 'common-orchestration-only',
                },
            },
        })
        events = state['events'].setdefault(operation['operation_id'], [])
        events.append(event)
        operation['last_sequence'] = sequence
        self._prune_events(events)
        operation['oldest_available_sequence'] = (
            events[0]['sequence'] if events else None
        )
        operation['updated_at'] = event['occurred_at']
        operation['version'] += 1
        return event

    def _prune_events(self, events):
        cutoff = self._clock() - self.retention.event_ttl
        retained = [
            event for event in events
            if parse_time(event['occurred_at']) >= cutoff
        ]
        limit = self.retention.max_events_per_operation
        if len(retained) > limit:
            retained = retained[-limit:]
        events[:] = retained

    def submit(
        self,
        request,
        principal,
        idempotency_key,
        correlation_id=None,
        readers=(),
        approvers=(),
        receipt_readers=(),
    ):
        """Create or idempotently recover an operation request."""
        specification = OperationRequest.from_value(request)
        if not isinstance(idempotency_key, str) or not idempotency_key.strip():
            raise OperationBusError(
                'idempotency_key must be a non-empty string'
            )
        policy = AccessPolicy(
            principal, readers, approvers, receipt_readers,
        )
        raw_request = specification.to_dict()
        fingerprint = _fingerprint(raw_request)
        scoped_key = _fingerprint({
            'principal': principal,
            'idempotency_key': idempotency_key,
        })
        correlation = correlation_id or str(uuid.uuid4())
        if not isinstance(correlation, str) or not correlation.strip():
            raise OperationBusError(
                'correlation_id must be a non-empty string'
            )

        def change(state):
            existing = state['idempotency'].get(scoped_key)
            if existing is not None:
                if existing['fingerprint'] != fingerprint:
                    raise IdempotencyConflictError(
                        'idempotency key is bound to another request'
                    )
                operation = self._operation(
                    state, existing['operation_id']
                )
                self._authorize(operation, principal)
                return self._view(operation, principal)
            operation_id = str(uuid.uuid4())
            now = iso_time(self._clock())
            operation = {
                'operation_id': operation_id,
                'correlation_id': correlation,
                'idempotency_key_digest': _fingerprint(idempotency_key),
                'request_fingerprint': fingerprint,
                'request': redact(raw_request),
                'access': policy.to_dict(),
                'stage': 'draft',
                'validation': None,
                'preview': None,
                'approval': None,
                'provider_operation': None,
                'local_process': None,
                'cancel_request': None,
                'post_state': None,
                'created_at': now,
                'updated_at': now,
                'last_sequence': 0,
                'oldest_available_sequence': None,
                'version': 0,
            }
            state['operations'][operation_id] = operation
            state['events'][operation_id] = []
            state['idempotency'][scoped_key] = {
                'operation_id': operation_id,
                'fingerprint': fingerprint,
            }
            self._emit(state, operation, 'operation.created', {
                'operation_kind': specification.operation_kind,
                'capability_id': specification.capability_id,
                'risk_class': specification.risk_class,
            })
            return self._view(operation, principal)

        return self.store.change(change)

    def _view(self, operation, principal, include_receipt=False):
        self._authorize(operation, principal)
        value = copy.deepcopy(operation)
        value.pop('request_fingerprint', None)
        value['orchestration_terminal'] = value['stage'] == 'closed'
        provider_operation = value.get('provider_operation')
        if isinstance(provider_operation, dict):
            value['provider_terminal_reported'] = bool(
                provider_operation.get('terminal')
            )
            allowed = principal in self._policy(operation).sensitive_readers
            if not include_receipt or not allowed:
                provider_operation['provider_receipt'] = None
        else:
            value['provider_terminal_reported'] = False
        return value

    def get_operation(self, operation_id, principal, include_receipt=False):
        state = self.store.read()
        operation = self._operation(state, operation_id)
        if include_receipt:
            self._authorize(operation, principal, sensitive=True)
        return self._view(operation, principal, include_receipt)

    def validate(self, operation_id, principal):
        operation = self.get_operation(operation_id, principal, True)
        if operation['stage'] in {
            'validated', 'previewed', 'awaiting_approval', 'approved',
        }:
            return self.get_operation(operation_id, principal)
        if operation['stage'] != 'draft':
            raise OperationStateError('operation cannot be validated now')
        result = redact(self._adapter(operation).validate(operation))
        valid = result.get('valid') is True

        def change(state):
            current = self._operation(state, operation_id)
            self._authorize(current, principal)
            current['validation'] = result
            current['stage'] = 'validated' if valid else 'validation_failed'
            self._emit(
                state, current, 'operation.validated', {'valid': valid}
            )
            return self._view(current, principal)

        return self.store.change(change)

    def preview(self, operation_id, principal):
        operation = self.get_operation(operation_id, principal, True)
        if operation['stage'] in {
            'previewed', 'awaiting_approval', 'approved',
        }:
            return self.get_operation(operation_id, principal)
        if operation['stage'] != 'validated':
            raise OperationStateError(
                'only a validated operation can be previewed'
            )
        result = redact(self._adapter(operation).preview(operation))

        def change(state):
            current = self._operation(state, operation_id)
            self._authorize(current, principal)
            current['preview'] = result
            current['stage'] = (
                'awaiting_approval'
                if current['request']['approval_required'] else 'previewed'
            )
            self._emit(state, current, 'operation.previewed', {
                'approval_required': current['request'][
                    'approval_required'
                ],
            })
            return self._view(current, principal)

        return self.store.change(change)

    def approve(self, operation_id, principal, decision='approved'):
        if decision not in {'approved', 'rejected'}:
            raise OperationBusError('approval decision is invalid')
        state = self.store.read()
        operation = self._operation(state, operation_id)
        self._authorize(operation, principal)
        if principal not in self._policy(operation).approval_principals:
            raise OperationAccessError('principal cannot approve operation')
        if operation['stage'] == 'approved' and decision == 'approved':
            return self._view(operation, principal)
        if operation['stage'] not in {'awaiting_approval', 'previewed'}:
            raise OperationStateError('operation cannot be approved now')

        def change(candidate):
            current = self._operation(candidate, operation_id)
            current['approval'] = {
                'decision': decision,
                'principal': principal,
                'occurred_at': iso_time(self._clock()),
            }
            current['stage'] = (
                'approved' if decision == 'approved'
                else 'approval_rejected'
            )
            self._emit(candidate, current, f'operation.{decision}', {
                'principal': principal,
            })
            return self._view(current, principal)

        return self.store.change(change)

    def start(self, operation_id, principal):
        operation = self.get_operation(operation_id, principal, True)
        if operation['stage'] in {
            'start_requested', 'provider_active', 'cancel_requested',
            'provider_disposition_recorded', 'local_process_exited',
            'unknown_outcome', 'post_state_pending', 'post_state_validated',
            'closed',
        }:
            return self.get_operation(operation_id, principal)
        approval_required = operation['request']['approval_required']
        allowed = {'approved'} if approval_required else {
            'validated', 'previewed'
        }
        if operation['stage'] not in allowed:
            raise OperationStateError('operation is not authorized to start')

        def mark_dispatched(state):
            current = self._operation(state, operation_id)
            self._authorize(current, principal)
            current['stage'] = 'start_requested'
            current['start_request'] = {
                'dispatch_id': str(uuid.uuid4()),
                'requested_at': iso_time(self._clock()),
                'principal': principal,
            }
            self._emit(state, current, 'operation.start_requested', {
                'dispatch_id': current['start_request']['dispatch_id'],
            })
            return copy.deepcopy(current)

        dispatched = self.store.change(mark_dispatched)
        try:
            response = self._adapter(dispatched).start(dispatched)
            if not isinstance(response, AdapterStart):
                raise OperationBusError('adapter returned an invalid start')
        except Exception as exc:
            return self._mark_unknown(
                operation_id, principal, 'start.response_unavailable', exc
            )

        def apply_response(state):
            current = self._operation(state, operation_id)
            terminal_reported = bool(response.provider_operation) and bool(
                response.provider_operation.get('terminal')
            )
            if response.provider_operation is not None:
                provider = redact(validate_contract(
                    'Operation', response.provider_operation
                ))
                current['provider_operation'] = provider
                current['stage'] = (
                    'provider_disposition_recorded'
                    if provider['terminal'] else 'provider_active'
                )
            if response.local_process is not None:
                current['local_process'] = redact(response.local_process)
                if response.provider_operation is None:
                    current['stage'] = 'provider_active'
            self._emit(state, current, 'operation.start_observed', {
                'provider_operation_present': (
                    response.provider_operation is not None
                ),
                'local_process_present': response.local_process is not None,
                'provider_terminal_reported': terminal_reported,
            })
            return self._view(current, principal)

        return self.store.change(apply_response)

    def _mark_unknown(self, operation_id, principal, event_kind, error):
        def change(state):
            operation = self._operation(state, operation_id)
            self._authorize(operation, principal)
            operation['stage'] = 'unknown_outcome'
            if event_kind == 'operation.cancel_response_unknown':
                operation['cancel_request'][
                    'dispatch_state'
                ] = 'response_unknown'
            self._emit(state, operation, event_kind, {
                'error_type': type(error).__name__,
                'outcome': 'unknown',
                'automatic_replay': False,
            })
            return self._view(operation, principal)

        return self.store.change(change)

    def refresh(self, operation_id, principal):
        operation = self.get_operation(operation_id, principal, True)
        if operation['stage'] in {'draft', 'validated', 'previewed'}:
            raise OperationStateError('operation has not been started')
        try:
            response = self._adapter(operation).inspect(operation)
            if not isinstance(response, AdapterObservation):
                raise OperationBusError(
                    'adapter returned an invalid observation'
                )
        except Exception as exc:
            def record_failure(state):
                current = self._operation(state, operation_id)
                self._emit(state, current, 'operation.observation_failed', {
                    'error_type': type(exc).__name__,
                })
                return self._view(current, principal)
            return self.store.change(record_failure)

        def apply_observation(state):
            current = self._operation(state, operation_id)
            terminal_reported = bool(response.provider_operation) and bool(
                response.provider_operation.get('terminal')
            )
            local_exit_observed = bool(response.local_process) and (
                response.local_process.get('exit_code') is not None
            )
            if response.provider_operation is not None:
                provider = redact(validate_contract(
                    'Operation', response.provider_operation
                ))
                current['provider_operation'] = provider
                current['stage'] = (
                    'provider_disposition_recorded'
                    if provider['terminal'] else 'provider_active'
                )
            if response.local_process is not None:
                current['local_process'] = redact(response.local_process)
                if all((
                    response.local_process.get('exit_code') is not None,
                    response.provider_operation is None,
                    getattr(
                        self._adapter(current), 'authority_kind', None
                    ) == 'local_process',
                )):
                    current['stage'] = 'local_process_exited'
            self._emit(state, current, 'operation.progress_observed', {
                'progress': response.progress or {},
                'provider_terminal_reported': terminal_reported,
                'local_exit_observed': local_exit_observed,
            })
            return self._view(current, principal)

        return self.store.change(apply_observation)

    def record_local_process_exit(
        self, operation_id, principal, process_id, exit_code
    ):
        """Link a supervisor exit without changing remote disposition."""
        if not isinstance(process_id, str) or not process_id:
            raise OperationBusError('process_id must be a non-empty string')
        if isinstance(exit_code, bool) or not isinstance(exit_code, int):
            raise OperationBusError('exit_code must be an integer')

        def change(state):
            operation = self._operation(state, operation_id)
            self._authorize(operation, principal)
            if operation['stage'] == 'closed':
                raise OperationStateError(
                    'closed operation cannot accept local process events'
                )
            operation['local_process'] = {
                'process_id': process_id,
                'exit_code': exit_code,
                'observed_at': iso_time(self._clock()),
            }
            adapter = self._adapter(operation)
            if getattr(adapter, 'authority_kind', None) == 'local_process':
                operation['stage'] = 'local_process_exited'
            self._emit(state, operation, 'operation.local_process_exited', {
                'process_id': process_id,
                'exit_code': exit_code,
                'remote_disposition_changed': False,
            })
            return self._view(operation, principal)

        return self.store.change(change)

    def cancel(self, operation_id, principal):
        operation = self.get_operation(operation_id, principal, True)
        if operation.get('cancel_request') is not None:
            return self.get_operation(operation_id, principal)
        provider = operation.get('provider_operation')
        if isinstance(provider, Mapping) and provider.get('terminal') is True:
            raise OperationStateError(
                'provider already reported a terminal disposition'
            )
        if operation['stage'] in {
            'draft', 'validated', 'previewed', 'awaiting_approval',
            'approved', 'closed',
        }:
            raise OperationStateError('operation cannot be cancelled now')

        def mark_request(state):
            current = self._operation(state, operation_id)
            request = {
                'request_id': str(uuid.uuid4()),
                'requested_at': iso_time(self._clock()),
                'principal': principal,
                'dispatch_state': 'requested',
                'accepted': None,
            }
            current['cancel_request'] = request
            current['stage'] = 'cancel_requested'
            self._emit(state, current, 'operation.cancel_requested', {
                'request_id': request['request_id'],
            })
            return copy.deepcopy(current)

        dispatched = self.store.change(mark_request)
        try:
            response = self._adapter(dispatched).cancel(dispatched)
            if not isinstance(response, AdapterCancellation):
                raise OperationBusError(
                    'adapter returned an invalid cancellation'
                )
        except Exception as exc:
            return self._mark_unknown(
                operation_id, principal, 'operation.cancel_response_unknown',
                exc,
            )

        def apply_response(state):
            current = self._operation(state, operation_id)
            terminal_reported = bool(response.provider_operation) and bool(
                response.provider_operation.get('terminal')
            )
            current['cancel_request']['dispatch_state'] = 'response_observed'
            current['cancel_request']['accepted'] = response.accepted
            current['cancel_request']['detail'] = redact(response.detail)
            if response.provider_operation is not None:
                provider = redact(validate_contract(
                    'Operation', response.provider_operation
                ))
                current['provider_operation'] = provider
                if provider['terminal']:
                    current['stage'] = 'provider_disposition_recorded'
            if response.unknown_outcome:
                current['stage'] = 'unknown_outcome'
            self._emit(state, current, 'operation.cancel_response_observed', {
                'accepted': response.accepted,
                'provider_terminal_reported': terminal_reported,
                'outcome_unknown': response.unknown_outcome,
            })
            return self._view(current, principal)

        return self.store.change(apply_response)

    def validate_post_state(self, operation_id, principal):
        operation = self.get_operation(operation_id, principal, True)
        if operation['stage'] not in {
            'provider_disposition_recorded', 'local_process_exited',
            'unknown_outcome', 'post_state_pending',
        }:
            raise OperationStateError('post-state validation is not ready')
        try:
            response = self._adapter(operation).validate_post_state(operation)
            if not isinstance(response, PostStateResult):
                raise OperationBusError(
                    'adapter returned an invalid post-state result'
                )
        except Exception as exc:
            response = PostStateResult(False, {
                'error_type': type(exc).__name__,
                'outcome': 'unknown',
            })

        def change(state):
            current = self._operation(state, operation_id)
            current['post_state'] = {
                'confirmed': response.confirmed,
                'detail': redact(response.detail),
                'observed_at': iso_time(self._clock()),
            }
            current['stage'] = (
                'post_state_validated'
                if response.confirmed else 'post_state_pending'
            )
            self._emit(state, current, 'operation.post_state_observed', {
                'confirmed': response.confirmed,
            })
            return self._view(current, principal)

        return self.store.change(change)

    def close(self, operation_id, principal):
        def change(state):
            operation = self._operation(state, operation_id)
            self._authorize(operation, principal)
            if operation['stage'] != 'post_state_validated':
                raise OperationStateError(
                    'operation requires confirmed post-state before closure'
                )
            operation['stage'] = 'closed'
            self._emit(state, operation, 'operation.closed')
            return self._view(operation, principal)
        return self.store.change(change)

    def replay_events(self, operation_id, principal, after_sequence=0):
        if isinstance(after_sequence, bool) or not isinstance(
            after_sequence, int
        ) or after_sequence < 0:
            raise OperationBusError(
                'event cursor must be a non-negative integer'
            )
        state = self.store.read()
        operation = self._operation(state, operation_id)
        self._authorize(operation, principal)
        events = state['events'].get(operation_id, [])
        selected = [
            copy.deepcopy(event) for event in events
            if event['sequence'] > after_sequence
        ]
        oldest = events[0]['sequence'] if events else None
        gap = bool(
            oldest is not None and after_sequence < oldest - 1
        )
        expected = max(after_sequence + 1, oldest or after_sequence + 1)
        for event in selected:
            if event['sequence'] != expected:
                gap = True
            expected = event['sequence'] + 1
        return EventReplay(
            tuple(selected), after_sequence, operation['last_sequence'],
            oldest, gap,
        )

    def get_receipt(self, operation_id, principal):
        state = self.store.read()
        operation = self._operation(state, operation_id)
        self._authorize(operation, principal, sensitive=True)
        provider = operation.get('provider_operation')
        if not isinstance(provider, Mapping):
            return None
        return copy.deepcopy(provider.get('provider_receipt'))

    def add_evidence(self, operation_id, principal, evidence, readers=()):
        descriptor = validate_contract('Evidence', evidence)
        if descriptor['subject_id'] != operation_id:
            raise OperationBusError(
                'operation evidence subject_id must match the operation'
            )
        now = self._clock()
        if descriptor['expires_at'] is None:
            descriptor['expires_at'] = iso_time(
                now + self.retention.evidence_ttl
            )
        elif parse_time(descriptor['expires_at']) <= now:
            raise OperationBusError('expired evidence cannot be added')
        descriptor = redact(descriptor)
        evidence_policy = AccessPolicy(
            principal, receipt_readers=readers
        )

        def change(state):
            operation = self._operation(state, operation_id)
            self._authorize(operation, principal, sensitive=True)
            evidence_id = descriptor['evidence_id']
            if evidence_id in state['evidence']:
                existing = state['evidence'][evidence_id]['descriptor']
                if existing != descriptor:
                    raise IdempotencyConflictError(
                        'evidence_id is bound to another descriptor'
                    )
                return copy.deepcopy(existing)
            authorized = set(evidence_policy.sensitive_readers)
            authorized.update(self._policy(operation).sensitive_readers)
            state['evidence'][evidence_id] = {
                'descriptor': descriptor,
                'readers': sorted(authorized),
            }
            self._emit(state, operation, 'operation.evidence_recorded', {
                'evidence_id': evidence_id,
                'evidence_kind': descriptor['evidence_kind'],
            })
            return copy.deepcopy(descriptor)

        return self.store.change(change)

    def get_evidence(self, evidence_id, principal):
        state = self.store.read()
        try:
            record = state['evidence'][evidence_id]
        except KeyError as exc:
            raise EvidenceUnavailableError('evidence is unavailable') from exc
        if principal not in record['readers']:
            raise OperationAccessError('evidence access is not authorized')
        descriptor = record['descriptor']
        expires_at = descriptor.get('expires_at')
        if expires_at is not None and parse_time(expires_at) <= self._clock():
            raise EvidenceUnavailableError('evidence has expired')
        return copy.deepcopy(descriptor)

    def purge_retention(self):
        now = self._clock()

        def change(state):
            removed_events = 0
            for operation_id, events in state['events'].items():
                before = len(events)
                self._prune_events(events)
                removed_events += before - len(events)
                operation = state['operations'].get(operation_id)
                if operation is not None:
                    operation['oldest_available_sequence'] = (
                        events[0]['sequence'] if events else None
                    )
            expired = []
            for evidence_id, record in state['evidence'].items():
                expires_at = record['descriptor'].get('expires_at')
                if expires_at is not None and parse_time(expires_at) <= now:
                    expired.append(evidence_id)
            for evidence_id in expired:
                del state['evidence'][evidence_id]
            return {
                'events_removed': removed_events,
                'evidence_removed': len(expired),
            }

        return self.store.change(change)
