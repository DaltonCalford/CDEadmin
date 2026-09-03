##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Common Data Studio extraction tests for CDE-PREP-070."""

from __future__ import annotations

import copy
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock


ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / 'web'
FIXTURE = (
    ROOT / 'tools/tests/fixtures/cdeadmin_data_studio/'
    'scratchbird_non_operational_story.json'
)
if str(WEB) not in sys.path:
    sys.path.insert(0, str(WEB))
if 'pgadmin' not in sys.modules:
    pgadmin_package = ModuleType('pgadmin')
    pgadmin_package.__path__ = [str(WEB / 'pgadmin')]
    sys.modules['pgadmin'] = pgadmin_package

from pgadmin.cdeadmin.core import EndpointContext  # noqa: E402
from pgadmin.cdeadmin.data_studio import (  # noqa: E402
    BoundedHistory,
    CHANNELS,
    CompletionContribution,
    DataStudioAccessError,
    DataStudioContributionRegistry,
    DataStudioError,
    DataStudioService,
    ExecutionContribution,
    FixtureExecutionError,
    LanguageContribution,
    SessionContribution,
    init_app,
    service_for_app,
)


PROVIDER_ID = 'org.example.data-studio'
PROVIDER_VERSION = '1.0.0'


def context(label='one'):
    endpoint_id = uuid.uuid5(uuid.NAMESPACE_URL, f'data-studio:{label}')

    def namespace(purpose):
        return str(uuid.uuid5(endpoint_id, purpose))

    return EndpointContext(
        endpoint_id=str(endpoint_id),
        mode='legacy_native',
        experience_family='relational',
        provider_id=PROVIDER_ID,
        provider_version=PROVIDER_VERSION,
        profile_id='example-sql',
        profile_version='1.0.0',
        target_adapter_id='example-adapter',
        target_adapter_version='1.0.0',
        pool_namespace=namespace('pool'),
        session_namespace=namespace('session'),
        cache_namespace=namespace('cache'),
        diagnostic_namespace=namespace('diagnostic'),
        effective_permissions=frozenset({'network', 'execute'}),
    )


def identity():
    return {
        'contract_version': '1.0.0',
        'provider_id': PROVIDER_ID,
        'provider_version': PROVIDER_VERSION,
        'profile_id': 'example-sql',
        'profile_version': '1.0.0',
        'evidence_reference': 'cde-prep-070:test-provider',
    }


def endpoint_payload(ctx):
    return {
        'identity': identity(),
        'endpoint_id': ctx.endpoint_id,
        'mode': ctx.mode,
        'declared_runtime': {},
        'verified_runtime': {'engine': 'opaque-test-engine'},
        'route': {'route_id': 'route-one'},
        'capability_generation': ctx.cache_namespace,
        'extensions': {},
    }


class FakeProvider:
    def __init__(self, ctx):
        self.context = ctx
        self.calls = []
        self.cancel_error = False
        self.invalid_poll = False
        self.last_execution_id = None

    def data_studio_contributions(self):
        profiles = frozenset({'example-sql'})
        return {
            'languages': (
                LanguageContribution(
                    'example-sql', 'Example SQL', 'text/x-example-sql',
                    frozenset({'relational'}),
                ),
            ),
            'completions': (
                CompletionContribution(
                    'example.complete', profiles,
                    FakeProvider._dispatch_complete,
                ),
            ),
            'sessions': (
                SessionContribution(
                    'example.session', profiles,
                    FakeProvider._dispatch_open_session,
                    FakeProvider._dispatch_transaction,
                ),
            ),
            'executions': (
                ExecutionContribution(
                    'example.execute', profiles,
                    FakeProvider._dispatch_execute,
                    FakeProvider._dispatch_poll,
                    FakeProvider._dispatch_cancel,
                ),
            ),
        }

    @staticmethod
    def _dispatch_open_session(binding, request):
        return binding.instance._open_session(binding, request)

    @staticmethod
    def _dispatch_transaction(binding, request):
        return binding.instance._transaction(binding, request)

    @staticmethod
    def _dispatch_execute(binding, request):
        return binding.instance._execute(binding, request)

    @staticmethod
    def _dispatch_poll(binding, request):
        return binding.instance._poll(binding, request)

    @staticmethod
    def _dispatch_cancel(binding, request):
        return binding.instance._cancel(binding, request)

    @staticmethod
    def _dispatch_complete(binding, request):
        return binding.instance._complete(binding, request)

    def _open_session(self, _binding, request):
        self.calls.append(('open', request['endpoint_id']))
        return {
            'identity': identity(),
            'session_id': 'session-one',
            'endpoint_id': request['endpoint_id'],
            'route_id': 'route-one',
            'principal_reference': 'principal-one',
            'language_profile': 'example-sql',
            'transaction_model': 'provider-native-opaque',
            'provider_state': {'handle': 'opaque'},
            'occurrence_id': 'provider-session-occurrence',
            'limits': {},
            'extensions': {},
        }

    def _transaction(self, _binding, request):
        self.calls.append(('transaction', request['session_id']))
        return {
            'identity': identity(),
            'session_id': request['session_id'],
            'transaction_model': 'provider-native-opaque',
            'provider_payload': {
                'uninterpreted': 'provider word may resemble active',
                'visibility': {'opaque': [7, 11, 13]},
            },
            'authority_reference': 'provider:example',
            'extensions': {},
        }

    def _execute(self, _binding, request):
        self.calls.append(('execute', copy.deepcopy(request)))
        self.last_execution_id = request['execution_id']
        return operation(request['execution_id'])

    def _poll(self, _binding, request):
        self.calls.append(('poll', request['operation_id']))
        if self.invalid_poll:
            return {'not': 'an operation'}, None
        current = operation(self.last_execution_id, terminal=True)
        current['operation_id'] = request['operation_id']
        current['provider_receipt'] = {
            'disposition': 'provider-defined-finished'
        }
        result = {
            'identity': identity(),
            'result_id': 'result-one',
            'execution_id': self.last_execution_id,
            'result_kind': 'tabular',
            'schema': {'columns': []},
            'stream_reference': None,
            'complete': True,
            'continuation': None,
            'extensions': {
                'cdeadmin_channels': [{
                    'channel': 'notice',
                    'payload': {'message': 'provider notice'},
                }],
            },
        }
        return current, result

    def _cancel(self, _binding, request):
        self.calls.append(('cancel', request['operation_id']))
        if self.cancel_error:
            raise RuntimeError('provider cancellation response unavailable')
        current = copy.deepcopy(request)
        current['provider_receipt'] = {
            'provider_disposition': 'request-seen',
        }
        return current

    def _complete(self, _binding, request):
        self.calls.append(('complete', request['session_id']))
        return [{'label': request['source_before_cursor'] + '-completion'}]


def operation(execution_id, terminal=False):
    return {
        'identity': identity(),
        'operation_id': 'operation-one',
        'operation_kind': 'provider-query',
        'target_resource_id': None,
        'capability_id': 'example.execute',
        'risk_class': 'unknown',
        'provider_state': {'uninterpreted': 'working'},
        'terminal': terminal,
        'provider_receipt': None,
        'extensions': {
            'cdeadmin_channels': [{
                'channel': 'progress',
                'payload': {'percent': 10},
            }],
            'test': {'execution_id': execution_id},
        },
    }


class FakeRegistry:
    def __init__(self, binding):
        self.binding = binding
        self.resolve_calls = 0

    def resolve(self, ctx):
        self.resolve_calls += 1
        if ctx.endpoint_id != self.binding.context.endpoint_id:
            raise RuntimeError('wrong endpoint')
        return self.binding


def harness(label='one', history=None):
    ctx = context(label)
    provider = FakeProvider(ctx)
    binding = SimpleNamespace(
        context=ctx,
        instance=provider,
        manifest={'identity': identity()},
    )
    registry = FakeRegistry(binding)
    return ctx, provider, registry, DataStudioService(
        registry, history=history
    )


class DataStudioContributionTests(unittest.TestCase):

    def test_registry_rejects_duplicate_language_profile(self):
        registry = DataStudioContributionRegistry()
        item = LanguageContribution(
            'example-sql', 'Example', 'text/plain',
            frozenset({'relational'}),
        )
        registry.register_language(item)
        with self.assertRaises(DataStudioError):
            registry.register_language(item)

    def test_service_registers_all_provider_contribution_types(self):
        ctx, _provider, _registry, service = harness()
        opened = service.open_session(
            ctx, endpoint_payload(ctx), 'example-sql'
        )
        self.assertEqual(
            'example.session', opened['session_contribution_id']
        )
        self.assertEqual(
            'example.execute', opened['execution_contribution_id']
        )
        self.assertEqual(
            'example.complete', opened['completion_contribution_id']
        )

    def test_completion_is_dispatched_by_language_contribution(self):
        ctx, provider, _registry, service = harness()
        service.open_session(ctx, endpoint_payload(ctx), 'example-sql')
        result = service.complete(
            ctx, 'session-one', 'SELECT ex', 'ex'
        )
        self.assertEqual([{'label': 'ex-completion'}], result)
        self.assertIn(('complete', 'session-one'), provider.calls)

    def test_endpoint_bound_contribution_callback_is_rejected(self):
        ctx, provider, _registry, service = harness()
        contribution = CompletionContribution(
            'unsafe.complete', frozenset({'example-sql'}),
            provider._complete,
        )
        with self.assertRaises(DataStudioError):
            service._reject_endpoint_bound_callbacks(contribution)


class DataStudioLifecycleTests(unittest.TestCase):

    def setUp(self):
        self.ctx, self.provider, self.registry, self.service = harness()
        self.service.open_session(
            self.ctx, endpoint_payload(self.ctx), 'example-sql'
        )

    def _execute(self, **kwargs):
        return self.service.execute(
            self.ctx, 'session-one', 'SELECT :secret_value',
            parameters={'secret_value': 'do-not-export'},
            **kwargs,
        )

    def test_execution_tracks_occurrence_without_common_success_state(self):
        item = self._execute()
        self.assertEqual('provider_active', item['lifecycle_state'])
        self.assertEqual('not_requested', item['cancellation_state'])
        self.assertNotIn('source', item['request_summary'])
        self.assertNotIn('parameters', item['request_summary'])
        self.assertEqual(
            ['secret_value'], item['request_summary']['parameter_names']
        )
        self.assertNotIn('success', repr(item).casefold())
        self.assertNotIn('rolled_back', repr(item).casefold())

    def test_poll_records_provider_terminal_and_typed_channels(self):
        item = self._execute()
        polled = self.service.poll(self.ctx, item['occurrence_id'])
        self.assertEqual('provider_terminal', polled['lifecycle_state'])
        self.assertEqual(
            'provider-defined-finished',
            polled['operation']['provider_receipt']['disposition'],
        )
        self.assertEqual(
            ['progress', 'progress', 'notice'],
            [message['channel'] for message in polled['channels']],
        )

    def test_cancel_request_is_distinct_from_provider_disposition(self):
        item = self._execute()
        cancelled = self.service.request_cancel(
            self.ctx, item['occurrence_id']
        )
        self.assertEqual(
            'provider_response_recorded',
            cancelled['cancellation_state'],
        )
        self.assertEqual('provider_active', cancelled['lifecycle_state'])
        self.assertEqual(
            'request-seen',
            cancelled['operation']['provider_receipt'][
                'provider_disposition'
            ],
        )

    def test_cancel_response_failure_preserves_unknown_outcome(self):
        item = self._execute()
        self.provider.cancel_error = True
        with self.assertRaises(RuntimeError):
            self.service.request_cancel(self.ctx, item['occurrence_id'])
        current = self.service.occurrence(item['occurrence_id'])
        self.assertEqual(
            'provider_response_failed', current['cancellation_state']
        )
        self.assertEqual('provider_active', current['lifecycle_state'])

    def test_transaction_presentation_is_retained_without_interpretation(self):
        expected = self.provider._transaction(
            None, {'session_id': 'session-one'}
        )
        actual = self.service.refresh_transaction(
            self.ctx, 'session-one'
        )
        self.assertEqual(expected['provider_payload'], actual[
            'provider_payload'
        ])
        session = self.service.session('session-one')
        self.assertEqual(actual, session['transaction_presentation'])

    def test_invalid_provider_poll_is_rejected(self):
        item = self._execute()
        self.provider.invalid_poll = True
        with self.assertRaises(DataStudioAccessError):
            self.service.poll(self.ctx, item['occurrence_id'])

    def test_polled_result_can_enter_typed_result_service(self):
        typed_results = SimpleNamespace(admit_provider_result=Mock())
        typed_results.admit_provider_result.return_value = {
            'result_id': 'result-one'
        }
        self.service.result_service = typed_results
        item = self._execute()
        self.service.poll(self.ctx, item['occurrence_id'])
        admitted = self.service.admit_result(
            self.ctx, item['occurrence_id'], {'example.result.render'}
        )
        self.assertEqual('result-one', admitted['result_id'])
        call = typed_results.admit_provider_result.call_args
        self.assertEqual(self.ctx, call.args[0])
        self.assertEqual('result-one', call.args[1]['result_id'])

    def test_cross_endpoint_session_use_is_refused(self):
        item = self._execute()
        other = context('other')
        with self.assertRaises(DataStudioAccessError):
            self.service.poll(other, item['occurrence_id'])


class DataStudioHistoryAndFixtureTests(unittest.TestCase):

    def test_history_is_bounded_and_recursively_redacted(self):
        history = BoundedHistory(max_entries=2, max_bytes=1024)
        history.append('one', 'subject', {
            'password': 'hidden',
            'nested': {'authorization': 'hidden'},
        })
        history.append('two', 'subject', {'value': 2})
        history.append('three', 'subject', {'value': 3})
        exported = history.export()
        self.assertEqual(['two', 'three'], [
            item['event_kind'] for item in exported
        ])
        self.assertLessEqual(history.encoded_bytes, history.max_bytes)
        separate = BoundedHistory()
        separate.append('redacted', 'subject', {
            'password': 'hidden',
            'nested': {'authorization': 'hidden'},
        })
        self.assertNotIn('hidden', repr(separate.export()))

    def test_execution_history_never_exports_source_or_parameter_values(self):
        ctx, _provider, _registry, service = harness()
        service.open_session(ctx, endpoint_payload(ctx), 'example-sql')
        service.execute(
            ctx, 'session-one', 'SELECT super_private_value',
            parameters={'password': 'super_private_parameter'},
        )
        exported = repr(service.export_history())
        self.assertNotIn('super_private_value', exported)
        self.assertNotIn('super_private_parameter', exported)

    def test_output_policy_redaction_applies_to_channel_and_export(self):
        ctx, _provider, _registry, service = harness()
        service.open_session(ctx, endpoint_payload(ctx), 'example-sql')
        item = service.execute(
            ctx, 'session-one', 'SELECT 1',
            output_policy={'redact_keys': ['api_key']},
        )
        channel = service.publish_channel(
            item['occurrence_id'], 'diagnostic',
            {'api_key': 'never-export-this', 'message': 'safe'},
        )
        self.assertEqual('[REDACTED]', channel['payload']['api_key'])
        self.assertNotIn('never-export-this', repr(service.export_history()))

    def test_all_typed_provider_channels_are_admitted(self):
        ctx, _provider, _registry, service = harness()
        service.open_session(ctx, endpoint_payload(ctx), 'example-sql')
        item = service.execute(ctx, 'session-one', 'SELECT 1')
        admitted = {
            service.publish_channel(
                item['occurrence_id'], channel, {'message': channel}
            )['channel']
            for channel in CHANNELS
        }
        self.assertEqual(CHANNELS, admitted)

    def test_invalid_output_policy_redaction_keys_fail_closed(self):
        ctx, _provider, _registry, service = harness()
        service.open_session(ctx, endpoint_payload(ctx), 'example-sql')
        with self.assertRaises(DataStudioAccessError):
            service.execute(
                ctx, 'session-one', 'SELECT 1',
                output_policy={'redact_keys': 'api_key'},
            )

    def test_scratchbird_fixture_cannot_resolve_provider_or_execute(self):
        ctx, _provider, registry, service = harness('fixture')
        loaded = service.load_fixture_story(ctx, FIXTURE)
        self.assertTrue(loaded[0]['fixture'])
        self.assertFalse(loaded[0]['execution_enabled'])
        self.assertEqual(0, registry.resolve_calls)
        with self.assertRaises(FixtureExecutionError):
            service.execute(
                ctx, 'scratchbird-fixture-session', 'SELECT 1'
            )
        self.assertEqual(0, registry.resolve_calls)

    def test_fixture_requires_explicit_non_production_marker(self):
        ctx, _provider, _registry, service = harness('fixture')
        with tempfile.TemporaryDirectory() as directory:
            invalid = Path(directory) / 'fixture.json'
            invalid.write_text(
                '{"production": false, "sessions": []}',
                encoding='utf-8',
            )
            with self.assertRaises(DataStudioAccessError):
                service.load_fixture_story(ctx, invalid)

    def test_common_layer_has_no_postgresql_transaction_constants(self):
        source = ' '.join(
            path.read_text(encoding='utf-8')
            for path in (
                WEB / 'pgadmin/cdeadmin/data_studio'
            ).glob('*.py')
        )
        for value in (
            'TX_STATUS_IDLE', 'TX_STATUS_ACTIVE', 'TX_STATUS_INTRANS',
            'TX_STATUS_INERROR', 'PQTRANS_',
        ):
            self.assertNotIn(value, source)

    def test_application_service_initialization_is_idempotent(self):
        app = SimpleNamespace(extensions={})
        registry = object()
        first = init_app(app, registry)
        self.assertIs(first, init_app(app, registry))
        self.assertIs(first, service_for_app(app))


if __name__ == '__main__':
    unittest.main()
