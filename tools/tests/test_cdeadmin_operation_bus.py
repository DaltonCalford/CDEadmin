##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Operation, event, and evidence bus tests for CDE-PREP-090."""

from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import ModuleType, SimpleNamespace


ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / 'web'
if str(WEB) not in sys.path:
    sys.path.insert(0, str(WEB))
if 'pgadmin' not in sys.modules:
    pgadmin_package = ModuleType('pgadmin')
    pgadmin_package.__path__ = [str(WEB / 'pgadmin')]
    sys.modules['pgadmin'] = pgadmin_package

from pgadmin.cdeadmin.operations import (  # noqa: E402
    AdapterCancellation,
    AdapterObservation,
    AdapterStart,
    EvidenceUnavailableError,
    IdempotencyConflictError,
    JsonOperationStore,
    LocalProcessAdapter,
    MemoryOperationStore,
    OperationAccessError,
    OperationBus,
    OperationBusError,
    OperationStateError,
    PostStateResult,
    RemoteProviderAdapter,
    RetentionPolicy,
    init_app,
    service_for_app,
)


OWNER = 'principal:owner'
READER = 'principal:reader'
APPROVER = 'principal:approver'


def identity():
    return {
        'contract_version': '1.0.0',
        'provider_id': 'org.example.operation-provider',
        'provider_version': '1.0.0',
        'profile_id': 'example-operation-profile',
        'profile_version': '1.0.0',
        'evidence_reference': 'cde-prep-090:test-provider',
    }


def provider_operation(
    operation_id='provider-operation-one', terminal=False, receipt=None
):
    return {
        'identity': identity(),
        'operation_id': operation_id,
        'operation_kind': 'example-admin-work',
        'target_resource_id': 'example:resource:one',
        'capability_id': 'example.admin.execute',
        'risk_class': 'admin',
        'provider_state': {'opaque': ['do', 'not', 'interpret']},
        'terminal': terminal,
        'provider_receipt': receipt,
        'extensions': {'example': {'authority': 'provider'}},
    }


def request(approval_required=False, password='do-not-store'):
    return {
        'operation_kind': 'example-admin-work',
        'capability_id': 'example.admin.execute',
        'risk_class': 'admin',
        'adapter_id': 'example.remote',
        'target_resource_id': 'example:resource:one',
        'payload': {
            'setting': 'safe-value',
            'password': password,
        },
        'approval_required': approval_required,
    }


class Clock:
    def __init__(self):
        self.value = datetime(2026, 8, 31, 12, tzinfo=timezone.utc)

    def __call__(self):
        return self.value

    def advance(self, **kwargs):
        self.value += timedelta(**kwargs)


class FakeAdapter:
    authority_kind = 'remote_provider'

    def __init__(self):
        self.start_calls = 0
        self.cancel_calls = 0
        self.inspect_calls = 0
        self.fail_start = False
        self.fail_cancel = False
        self.terminal_on_start = False
        self.terminal_on_inspect = False
        self.confirm_post_state = False
        self.receipt_secret = None

    @staticmethod
    def validate(_operation):
        return {'valid': True, 'diagnostic': 'provider-valid'}

    @staticmethod
    def preview(_operation):
        return {
            'summary': 'provider preview',
            'credential_token': 'must-be-redacted',
        }

    def start(self, _operation):
        self.start_calls += 1
        if self.fail_start:
            raise RuntimeError('start response lost')
        receipt = None
        if self.terminal_on_start:
            receipt = {'disposition': 'provider-complete'}
            if self.receipt_secret is not None:
                receipt['secret_token'] = self.receipt_secret
        return AdapterStart(provider_operation=provider_operation(
            terminal=self.terminal_on_start, receipt=receipt,
        ))

    def inspect(self, operation):
        self.inspect_calls += 1
        observed = copy.deepcopy(operation['provider_operation'])
        observed['terminal'] = self.terminal_on_inspect
        if self.terminal_on_inspect:
            observed['provider_receipt'] = {
                'disposition': 'provider-observed-complete'
            }
        return AdapterObservation(
            provider_operation=observed,
            progress={'percent': 50},
        )

    def cancel(self, operation):
        self.cancel_calls += 1
        if self.fail_cancel:
            raise RuntimeError('cancel response lost')
        observed = copy.deepcopy(operation['provider_operation'])
        observed['provider_receipt'] = {
            'cancel_request_accepted': True,
            'outcome': 'pending-provider-observation',
        }
        return AdapterCancellation(
            accepted=True, provider_operation=observed,
            detail={'transport': 'response-observed'},
        )

    def validate_post_state(self, _operation):
        return PostStateResult(
            self.confirm_post_state,
            {'provider_check': 'independent'},
        )


def harness(*, store=None, retention=None, clock=None):
    adapter = FakeAdapter()
    bus = OperationBus(
        store=store,
        retention=retention,
        clock=clock or Clock(),
    )
    bus.register_adapter('example.remote', adapter)
    return bus, adapter


def submitted(bus, **kwargs):
    return bus.submit(
        request(kwargs.pop('approval_required', False)),
        OWNER,
        kwargs.pop('idempotency_key', 'request-one'),
        **kwargs,
    )


def started(bus):
    operation = submitted(bus)
    operation = bus.validate(operation['operation_id'], OWNER)
    operation = bus.preview(operation['operation_id'], OWNER)
    return bus.start(operation['operation_id'], OWNER)


class OperationLifecycleTests(unittest.TestCase):

    def test_validate_preview_approve_start_lifecycle(self):
        bus, adapter = harness()
        operation = submitted(
            bus, approval_required=True, readers=(APPROVER,),
            approvers=(APPROVER,),
        )
        operation = bus.validate(operation['operation_id'], OWNER)
        self.assertEqual('validated', operation['stage'])
        operation = bus.preview(operation['operation_id'], OWNER)
        self.assertEqual('awaiting_approval', operation['stage'])
        operation = bus.approve(
            operation['operation_id'], APPROVER
        )
        self.assertEqual('approved', operation['stage'])
        operation = bus.start(operation['operation_id'], OWNER)
        self.assertEqual('provider_active', operation['stage'])
        self.assertEqual(1, adapter.start_calls)

    def test_approval_is_authorization_filtered(self):
        bus, _adapter = harness()
        operation = submitted(bus, approval_required=True)
        operation = bus.validate(operation['operation_id'], OWNER)
        operation = bus.preview(operation['operation_id'], OWNER)
        with self.assertRaises(OperationAccessError):
            bus.approve(operation['operation_id'], READER)

    def test_provider_terminal_response_is_not_common_closure(self):
        bus, adapter = harness()
        adapter.terminal_on_start = True
        operation = started(bus)
        self.assertEqual('provider_disposition_recorded', operation['stage'])
        self.assertTrue(operation['provider_terminal_reported'])
        self.assertFalse(operation['orchestration_terminal'])
        with self.assertRaises(OperationStateError):
            bus.close(operation['operation_id'], OWNER)

    def test_post_state_must_be_confirmed_before_close(self):
        bus, adapter = harness()
        adapter.terminal_on_start = True
        operation = started(bus)
        operation = bus.validate_post_state(operation['operation_id'], OWNER)
        self.assertEqual('post_state_pending', operation['stage'])
        adapter.confirm_post_state = True
        operation = bus.validate_post_state(operation['operation_id'], OWNER)
        self.assertEqual('post_state_validated', operation['stage'])
        operation = bus.close(operation['operation_id'], OWNER)
        self.assertTrue(operation['orchestration_terminal'])

    def test_start_response_loss_is_unknown_and_never_replayed(self):
        bus, adapter = harness()
        adapter.fail_start = True
        operation = started(bus)
        self.assertEqual('unknown_outcome', operation['stage'])
        operation = bus.start(operation['operation_id'], OWNER)
        self.assertEqual('unknown_outcome', operation['stage'])
        self.assertEqual(1, adapter.start_calls)

    def test_sensitive_values_are_redacted_before_persistence(self):
        bus, _adapter = harness()
        operation = submitted(bus)
        self.assertEqual(
            '[REDACTED]', operation['request']['payload']['password']
        )
        operation = bus.validate(operation['operation_id'], OWNER)
        operation = bus.preview(operation['operation_id'], OWNER)
        self.assertEqual(
            '[REDACTED]', operation['preview']['credential_token']
        )
        snapshot = bus.store.export_state()
        self.assertNotIn('do-not-store', repr(snapshot))

    def test_raw_idempotency_key_is_not_persisted(self):
        bus, _adapter = harness()
        submitted(bus, idempotency_key='caller-private-key-value')
        self.assertNotIn(
            'caller-private-key-value', repr(bus.store.export_state())
        )

    def test_malformed_reader_collection_fails_closed(self):
        bus, _adapter = harness()
        with self.assertRaises(OperationBusError):
            submitted(bus, readers='principal:not-an-array')


class IdempotencyAndCancellationTests(unittest.TestCase):

    def test_submit_recovers_same_idempotent_operation(self):
        bus, _adapter = harness()
        first = submitted(bus, correlation_id='correlation-one')
        second = submitted(bus, correlation_id='ignored-on-recovery')
        self.assertEqual(first['operation_id'], second['operation_id'])
        self.assertEqual('correlation-one', second['correlation_id'])
        self.assertEqual(1, len(bus.replay_events(
            first['operation_id'], OWNER
        ).events))

    def test_idempotency_key_collision_fails_closed(self):
        bus, _adapter = harness()
        submitted(bus)
        changed = request(password='another-secret')
        with self.assertRaises(IdempotencyConflictError):
            bus.submit(changed, OWNER, 'request-one')

    def test_cancellation_dispatch_is_idempotent_and_outcome_aware(self):
        bus, adapter = harness()
        operation = started(bus)
        first = bus.cancel(operation['operation_id'], OWNER)
        second = bus.cancel(operation['operation_id'], OWNER)
        self.assertEqual(1, adapter.cancel_calls)
        self.assertEqual(
            first['cancel_request']['request_id'],
            second['cancel_request']['request_id'],
        )
        self.assertEqual('cancel_requested', second['stage'])
        self.assertTrue(second['cancel_request']['accepted'])

    def test_lost_cancel_response_becomes_unknown_without_retry(self):
        bus, adapter = harness()
        adapter.fail_cancel = True
        operation = started(bus)
        operation = bus.cancel(operation['operation_id'], OWNER)
        self.assertEqual('unknown_outcome', operation['stage'])
        bus.cancel(operation['operation_id'], OWNER)
        self.assertEqual(1, adapter.cancel_calls)

    def test_terminal_provider_operation_cannot_be_cancelled(self):
        bus, adapter = harness()
        adapter.terminal_on_start = True
        operation = started(bus)
        with self.assertRaises(OperationStateError):
            bus.cancel(operation['operation_id'], OWNER)
        self.assertEqual(0, adapter.cancel_calls)


class LocalAndRemoteAuthorityTests(unittest.TestCase):

    def test_local_exit_does_not_change_remote_disposition(self):
        bus, _adapter = harness()
        operation = started(bus)
        operation = bus.record_local_process_exit(
            operation['operation_id'], OWNER, 'pgadmin-pid-one', 0
        )
        self.assertEqual('provider_active', operation['stage'])
        self.assertFalse(operation['provider_terminal_reported'])
        event = bus.replay_events(
            operation['operation_id'], OWNER
        ).events[-1]
        self.assertFalse(event['payload']['remote_disposition_changed'])

    def test_local_adapter_exit_remains_nonterminal_supervision_fact(self):
        local = LocalProcessAdapter(
            launcher=lambda _operation: {'process_id': 'local-one'},
            inspector=lambda handle: {
                'process_id': handle['process_id'], 'exit_code': 0,
            },
            canceller=lambda _handle: {'accepted': True},
        )
        bus = OperationBus(clock=Clock())
        bus.register_adapter('local.process', local)
        item = request()
        item['adapter_id'] = 'local.process'
        operation = bus.submit(item, OWNER, 'local-request')
        operation = bus.validate(operation['operation_id'], OWNER)
        operation = bus.start(operation['operation_id'], OWNER)
        operation = bus.refresh(operation['operation_id'], OWNER)
        self.assertEqual('local_process_exited', operation['stage'])
        self.assertFalse(operation['provider_terminal_reported'])
        self.assertFalse(operation['orchestration_terminal'])

    def test_remote_provider_adapter_preserves_opaque_contract(self):
        class Provider:
            def get_operation(self, value):
                return value

            def cancel(self, value):
                result = copy.deepcopy(value)
                result['provider_receipt'] = {
                    'cancel_request_accepted': True
                }
                return result

        binding = SimpleNamespace(instance=Provider())
        adapter = RemoteProviderAdapter(
            binding,
            lambda _binding, _operation: provider_operation(),
        )
        bus = OperationBus(clock=Clock())
        bus.register_adapter('example.remote', adapter)
        operation = started(bus)
        operation = bus.refresh(operation['operation_id'], OWNER)
        self.assertEqual(
            ['do', 'not', 'interpret'],
            operation['provider_operation']['provider_state']['opaque'],
        )


class EventReplayAndRestartTests(unittest.TestCase):

    def test_event_replay_is_authorized(self):
        bus, _adapter = harness()
        operation = submitted(bus, readers=(READER,))
        bus.validate(operation['operation_id'], OWNER)
        replay = bus.replay_events(operation['operation_id'], READER, 1)
        self.assertEqual([2], [event['sequence'] for event in replay.events])
        with self.assertRaises(OperationAccessError):
            bus.replay_events(operation['operation_id'], 'principal:stranger')

    def test_retention_reports_replay_gap(self):
        retention = RetentionPolicy(max_events_per_operation=2)
        bus, _adapter = harness(retention=retention)
        operation = submitted(bus)
        bus.validate(operation['operation_id'], OWNER)
        bus.preview(operation['operation_id'], OWNER)
        replay = bus.replay_events(operation['operation_id'], OWNER, 0)
        self.assertTrue(replay.gap_detected)
        self.assertEqual(2, replay.oldest_available_sequence)

    def test_internal_event_loss_is_detected_after_restart(self):
        bus, _adapter = harness()
        operation = submitted(bus)
        bus.validate(operation['operation_id'], OWNER)
        bus.preview(operation['operation_id'], OWNER)
        state = bus.store.export_state()
        state['events'][operation['operation_id']].pop(1)
        restarted, _adapter = harness(
            store=MemoryOperationStore(state)
        )
        replay = restarted.replay_events(
            operation['operation_id'], OWNER, 0
        )
        self.assertTrue(replay.gap_detected)

    def test_json_restart_preserves_identity_sequence_and_idempotency(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'operation-state.json'
            first, _adapter = harness(store=JsonOperationStore(path))
            operation = submitted(first)
            first.validate(operation['operation_id'], OWNER)
            restarted, _adapter = harness(store=JsonOperationStore(path))
            recovered = submitted(restarted)
            self.assertEqual(
                operation['operation_id'], recovered['operation_id']
            )
            restarted.preview(operation['operation_id'], OWNER)
            replay = restarted.replay_events(
                operation['operation_id'], OWNER
            )
            self.assertEqual([1, 2, 3], [
                event['sequence'] for event in replay.events
            ])


class ReceiptAndEvidenceTests(unittest.TestCase):

    def test_receipt_access_requires_separate_authorization(self):
        bus, adapter = harness()
        adapter.terminal_on_start = True
        operation = submitted(
            bus, readers=(READER,), receipt_readers=(APPROVER,)
        )
        bus.validate(operation['operation_id'], OWNER)
        bus.start(operation['operation_id'], OWNER)
        reader_view = bus.get_operation(operation['operation_id'], READER)
        self.assertIsNone(
            reader_view['provider_operation']['provider_receipt']
        )
        with self.assertRaises(OperationAccessError):
            bus.get_receipt(operation['operation_id'], READER)
        receipt = bus.get_receipt(operation['operation_id'], APPROVER)
        self.assertEqual('provider-complete', receipt['disposition'])

    def test_provider_receipt_is_redacted_before_persistence(self):
        bus, adapter = harness()
        adapter.terminal_on_start = True
        adapter.receipt_secret = 'provider-private-token'
        operation = started(bus)
        receipt = bus.get_receipt(operation['operation_id'], OWNER)
        self.assertEqual('[REDACTED]', receipt['secret_token'])
        self.assertNotIn(
            'provider-private-token', repr(bus.store.export_state())
        )

    def test_evidence_is_validated_authorized_and_retained(self):
        clock = Clock()
        bus, _adapter = harness(clock=clock)
        operation = submitted(bus, receipt_readers=(APPROVER,))
        evidence = {
            'identity': identity(),
            'evidence_id': 'evidence-one',
            'evidence_kind': 'provider-receipt-digest',
            'subject_id': operation['operation_id'],
            'collected_at': '2026-08-31T12:00:00Z',
            'expires_at': None,
            'digest': 'sha256:opaque-digest',
            'location': 'evidence://authorized-store/evidence-one',
            'extensions': {'token': 'must-be-redacted'},
        }
        stored = bus.add_evidence(
            operation['operation_id'], OWNER, evidence
        )
        self.assertEqual('[REDACTED]', stored['extensions']['token'])
        self.assertEqual(
            stored, bus.get_evidence('evidence-one', APPROVER)
        )
        with self.assertRaises(OperationAccessError):
            bus.get_evidence('evidence-one', READER)
        clock.advance(days=91)
        with self.assertRaises(EvidenceUnavailableError):
            bus.get_evidence('evidence-one', OWNER)
        result = bus.purge_retention()
        self.assertEqual(1, result['evidence_removed'])


class ApplicationIntegrationTests(unittest.TestCase):

    def test_init_app_is_idempotent_and_service_is_discoverable(self):
        app = SimpleNamespace(extensions={}, config={})
        first = init_app(app)
        second = init_app(app)
        self.assertIs(first, second)
        self.assertIs(first, service_for_app(app))

    def test_provider_audit_is_durable_redacted_and_endpoint_scoped(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'operation-state.json'
            first = OperationBus(store=JsonOperationStore(path))
            record = {
                'schema': 'cdeadmin.visual-admin.operation.v1',
                'operation_id': 'visual-one', 'plan_id': 'plan-one',
                'engine_id': 'immudb', 'resource_kind': 'database',
                'operation_kind': 'create',
                'stage': 'provider_response_recorded',
                'provider_result': {
                    'accepted': True, 'access_token': 'must-not-persist',
                },
                'provider_finality_authority': True,
                'automatic_mutation_retry': False,
                'created_at': '2026-09-02T12:00:00Z',
                'updated_at': '2026-09-02T12:00:00Z',
                'events': [],
                'provider_payload': {
                    'route': {'password': 'must-not-persist'},
                },
            }
            stored = first.record_provider_audit(
                'endpoint-one', OWNER, record
            )
            self.assertNotIn('provider_payload', stored)
            self.assertEqual(
                '[REDACTED]',
                stored['provider_result']['access_token'],
            )
            restarted = OperationBus(store=JsonOperationStore(path))
            self.assertEqual(
                ['visual-one'],
                [item['operation_id'] for item in
                 restarted.list_provider_audit('endpoint-one', OWNER)],
            )
            with self.assertRaises(OperationAccessError):
                restarted.get_provider_audit(
                    'endpoint-one', READER, 'visual-one'
                )
            self.assertEqual(
                [], restarted.list_provider_audit('endpoint-two', OWNER)
            )

    def test_provider_audit_rejects_common_finality_claims(self):
        bus = OperationBus()
        with self.assertRaisesRegex(
            OperationBusError, 'finality authority'
        ):
            bus.record_provider_audit('endpoint-one', OWNER, {
                'operation_id': 'visual-one',
                'provider_finality_authority': False,
                'automatic_mutation_retry': False,
            })

    def test_provider_audit_bounds_provider_owned_output(self):
        bus = OperationBus()
        stored = bus.record_provider_audit('endpoint-one', OWNER, {
            'schema': 'cdeadmin.visual-admin.operation.v1',
            'operation_id': 'visual-large',
            'provider_result': {
                'stdout': 'x' * (2 * 1024 * 1024),
                'rows': list(range(1000)),
            },
            'provider_finality_authority': True,
            'automatic_mutation_retry': False,
        })
        self.assertLess(
            len(json.dumps(stored).encode('utf-8')), 1024 * 1024
        )
        self.assertTrue(
            stored['provider_result']['stdout'].endswith(
                '[AUDIT TRUNCATED]'
            )
        )
        self.assertEqual(
            {'audit_items_omitted': 488},
            stored['provider_result']['rows'][-1],
        )


if __name__ == '__main__':
    unittest.main()
