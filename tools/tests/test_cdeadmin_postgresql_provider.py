##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""PostgreSQL preservation provider tests for CDE-PREP-050."""

from __future__ import annotations

import json
import sys
import unittest
import uuid
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / 'web'
if str(WEB) not in sys.path:
    sys.path.insert(0, str(WEB))

if 'pgadmin' not in sys.modules:
    pgadmin_package = ModuleType('pgadmin')
    pgadmin_package.__path__ = [str(WEB / 'pgadmin')]
    sys.modules['pgadmin'] = pgadmin_package
if 'pgadmin.utils' not in sys.modules:
    pgadmin_utils_package = ModuleType('pgadmin.utils')
    pgadmin_utils_package.__path__ = [str(WEB / 'pgadmin/utils')]
    sys.modules['pgadmin.utils'] = pgadmin_utils_package
if 'config' not in sys.modules:
    sys.modules['config'] = ModuleType('config')

from pgadmin.cdeadmin.core import (  # noqa: E402
    EndpointContext,
    ProviderRegistry,
    endpoint_scope,
    route_query_tool_execute,
    route_query_tool_fetch,
    route_query_tool_manager,
    route_query_tool_poll,
)
from pgadmin.cdeadmin.providers.postgresql.provider import (  # noqa: E402
    LEGACY_DRIVER_TYPE,
    PostgreSQLCatalogSource,
    PostgreSQLProvider,
    PostgreSQLProviderError,
    TABULAR_RENDER_CAPABILITY,
    TOOL_DISPOSITIONS,
)


MANIFEST_PATH = (
    WEB / 'pgadmin/cdeadmin/providers/postgresql/provider_manifest.json'
)
PROVIDER_ROOT = MANIFEST_PATH.parent


def endpoint(label='one'):
    endpoint_id = uuid.uuid5(uuid.NAMESPACE_URL, f'postgresql:{label}')

    def namespace(purpose):
        return str(uuid.uuid5(endpoint_id, f'namespace:{purpose}'))

    return EndpointContext(
        endpoint_id=str(endpoint_id),
        mode='legacy_native',
        experience_family='postgresql',
        provider_id='org.pgadmin.postgresql',
        provider_version='9.17.0',
        profile_id='postgresql-native',
        profile_version='18.3',
        target_adapter_id='legacy-pgadmin-server',
        target_adapter_version='9.17.0',
        pool_namespace=namespace('pool'),
        session_namespace=namespace('session'),
        cache_namespace=namespace('cache'),
        diagnostic_namespace=namespace('diagnostic'),
        effective_permissions=frozenset({
            'network', 'secret_read', 'data_read', 'data_write',
            'administer', 'execute',
        }),
        legacy_driver_type=LEGACY_DRIVER_TYPE,
    )


class FakePermissions:
    def __init__(self, allowed=None):
        self.allowed = set(allowed or {
            'network', 'data_read', 'execute',
        })
        self.calls = []

    def require(self, permission_id, scope='endpoint'):
        self.calls.append((permission_id, scope))
        if permission_id not in self.allowed:
            raise RuntimeError(f'denied: {permission_id}')


class FakeConnection:
    def __init__(self):
        self.executions = []
        self.cancelled = []

    def transaction_status(self):
        return 2

    def execute_async(self, source, parameters=None, **kwargs):
        self.executions.append((source, parameters, kwargs))
        return True, None

    def poll(self, **_kwargs):
        return 1, None

    def async_fetchmany_2darray(self, records):
        return True, [[1, 'row']]

    def get_column_info(self):
        return [{'name': 'id'}, {'name': 'value'}]

    def cancel_transaction(self, connection_id, database_id):
        self.cancelled.append((connection_id, database_id))
        return True, 'cancelled'


class FakeManager:
    version = 18.3
    server_type = 'pg'

    def __init__(self, connection=None):
        self._connection = connection or FakeConnection()
        self.connection_calls = []

    def connection(self, **kwargs):
        self.connection_calls.append(kwargs)
        return self._connection


class FakeDriver:
    def __init__(self, manager=None):
        self.manager = manager or FakeManager()
        self.server_ids = []

    def connection_manager(self, server_id):
        self.server_ids.append(server_id)
        return self.manager

    @staticmethod
    def version():
        return '3.3.4'


class FakeCatalog:
    def __init__(self):
        self.calls = []

    def read(self, kind, route, object_id=None):
        self.calls.append((kind, dict(route), object_id))
        rows = {
            'database': [{'did': 10, 'name': 'appdb'}],
            'schema': [{'oid': 20, 'name': 'public'}],
            'table': [{'oid': 30, 'name': 'orders'}],
        }
        return rows[kind]


def parent_resource(child_kind, **route):
    postgresql = {'child_kind': child_kind, 'server_id': 7}
    postgresql.update(route)
    return {
        'identity': {},
        'endpoint_id': endpoint().endpoint_id,
        'resource_id': 'postgresql:endpoint',
        'identity_kind': 'endpoint',
        'resource_kind': 'endpoint',
        'model_family': 'relational',
        'display_name': 'PostgreSQL',
        'authority_path': ['server', '7'],
        'display_path': ['PostgreSQL'],
        'is_virtual': False,
        'generation': 'generation-1',
        'capability_ids': [],
        'extensions': {'postgresql': postgresql},
    }


class PostgreSQLProviderContractTests(unittest.TestCase):

    def setUp(self):
        self.permissions = FakePermissions()
        self.driver = FakeDriver()
        self.catalog = FakeCatalog()
        self.provider = PostgreSQLProvider(
            endpoint(), self.permissions, self.driver, self.catalog
        )

    def test_production_manifest_matches_structural_provider(self):
        manifest = json.loads(MANIFEST_PATH.read_text(encoding='utf-8'))
        schema = json.loads((
            WEB / 'pgadmin/cdeadmin/contracts/v1/contract.schema.json'
        ).read_text(encoding='utf-8'))
        interfaces = schema['x-cdeadmin-provider-interfaces']
        for contract_name in manifest['contracts']:
            for method_name in interfaces[contract_name]:
                self.assertTrue(callable(getattr(
                    self.provider, method_name, None
                )))

    def test_endpoint_adapter_reports_and_discovers_runtime(self):
        request = {'route': {'server_id': 7}}
        diagnostic = self.provider.validate_endpoint(request)
        discovered = self.provider.discover_endpoint(request)
        self.assertEqual('CDE_PG_ENDPOINT_VALID', diagnostic['code'])
        self.assertEqual(
            '18.3', discovered['verified_runtime']['server_version']
        )
        self.assertEqual(
            LEGACY_DRIVER_TYPE, discovered['verified_runtime']['driver']
        )
        self.assertEqual(
            'verified', discovered['verified_runtime']['verification_state']
        )

    def test_endpoint_discovery_rejects_wrong_exact_runtime_or_driver(self):
        self.driver.manager.version = 18.4
        with self.assertRaisesRegex(
            PostgreSQLProviderError, 'exact profile 18.3'
        ):
            self.provider.discover_endpoint({'route': {'server_id': 7}})

    def test_preserved_administration_declares_every_relational_concept(self):
        descriptor = self.provider.visual_admin_descriptor()
        coverage = descriptor['concept_coverage']
        self.assertTrue(coverage['declaration_ready'])
        self.assertFalse(coverage['activation_ready'])
        self.assertEqual(0, coverage['undeclared_count'])
        concepts = coverage['families'][0]['concepts']
        self.assertTrue(all(
            item['declared_status'] == 'supported' for item in concepts
        ))
        self.assertTrue(all(
            item['external_surface'] ==
            'pgadmin.preserved-postgresql-administration'
            for item in concepts
        ))
        self.assertTrue(all(
            item['operation_obligations'] for item in concepts
        ))
        self.driver.manager.version = 18.3
        self.driver.version = lambda: '3.2.10'
        with self.assertRaisesRegex(
            PostgreSQLProviderError, 'approved exact versions'
        ):
            self.provider.discover_endpoint({'route': {'server_id': 7}})

    def test_database_schema_table_resource_coverage_is_provider_owned(self):
        requests = (
            parent_resource('database'),
            parent_resource('schema', database_id=10),
            parent_resource('table', database_id=10, schema_id=20),
        )
        resources = [
            self.provider.list_resources(request)[0]
            for request in requests
        ]
        self.assertEqual(
            ['database', 'schema', 'table'],
            [item['resource_kind'] for item in resources],
        )
        self.assertEqual(
            ['appdb', 'public', 'orders'],
            [item['display_name'] for item in resources],
        )
        self.assertEqual(
            [('data_read', 'resource')] * 3, self.permissions.calls
        )

    def test_resource_contributions_are_postgresql_owned_and_read_only(self):
        contributions = self.provider.resource_contributions()
        inspector = contributions['inspectors'][0]
        command = contributions['commands'][0]
        self.assertEqual(
            'postgresql.catalog.inspector', inspector.inspector_id
        )
        self.assertEqual('postgresql.catalog.inspect', command.command_id)
        self.assertEqual('postgresql.catalog.read', command.capability_id)
        self.assertEqual('read', command.mutation_class)
        self.assertEqual('data_read', command.required_permission)

    def test_session_execution_result_operation_and_diagnostic_adapters(self):
        session = self.provider.open_session({
            'route': {'server_id': 7, 'database_id': 10},
        })
        execution = {
            'execution_id': 'execution-1',
            'session_id': session['session_id'],
            'source': 'SELECT 1',
            'parameters': {},
        }
        operation = self.provider.execute(execution)
        result = self.provider.describe_result(operation)
        current = self.provider.get_operation(operation)
        transaction = self.provider.describe_transaction(session)
        diagnostic = self.provider.translate_diagnostic({
            'code': '42601',
            'message': 'syntax error',
            'severity': 'error',
            'retryable': False,
            'details': {'position': 3},
        })
        self.assertTrue(result['complete'])
        self.assertEqual(
            [[1, 'row']], result['extensions']['postgresql']['rows']
        )
        self.assertTrue(current['terminal'])
        self.assertEqual(
            'PostgreSQL provider-owned opaque value',
            transaction['provider_payload']['interpretation'],
        )
        self.assertEqual('42601', diagnostic['code'])

    def test_semantic_query_uses_preserved_postgresql_execution(self):
        session = self.provider.open_session({
            'route': {'server_id': 7, 'database_id': 10},
        })
        model = {
            'contract_version': '1.0.0',
            'name': 'PostgreSQL provider model',
            'description': 'Provider execution qualification',
            'sources': [{
                'id': 'facts', 'resource_id': 'postgresql:facts',
                'relation': ['public', 'facts'], 'alias': 'facts',
            }],
            'joins': [], 'dimensions': [],
            'measures': [{
                'id': 'row_count', 'name': 'Row count',
                'aggregation': 'count', 'field': None, 'format': '0',
            }],
            'default_filters': [], 'materializations': [],
            'security': {}, 'annotations': {'qualification': True},
        }
        query = {
            'axes': {'rows': [], 'columns': [], 'pages': []},
            'measures': ['row_count'], 'filters': [],
            'totals': False, 'limit': 10,
        }
        descriptor = self.provider.semantic_model_descriptor()
        operation = self.provider.execute_analysis({
            'session_id': session['session_id'],
            'execution_id': 'semantic-execution-1',
            'semantic_model': model,
            'semantic_query': query,
        })
        result = self.provider.describe_result(operation)
        source, parameters, _options = (
            self.driver.manager._connection.executions[-1]
        )
        self.assertTrue(descriptor['execution_available'])
        self.assertEqual('postgresql-sql', descriptor['language_profile'])
        self.assertIn(
            'period_comparison',
            descriptor['time_intelligence']['operations'],
        )
        self.assertIn(
            'running_sum', descriptor['analytical_windows']['operations']
        )
        self.assertIn('COUNT(*)', source)
        self.assertIn('"public"."facts" AS "facts"', source)
        self.assertIsNone(parameters)
        self.assertTrue(result['complete'])

    def test_cancel_uses_preserved_connection_authority(self):
        session = self.provider.open_session({
            'route': {
                'server_id': 7,
                'database_id': 10,
                'connection_id': 'query-tool-1',
            },
        })
        operation = self.provider.execute({
            'execution_id': 'execution-1',
            'session_id': session['session_id'],
            'source': 'SELECT pg_sleep(10)',
            'parameters': {},
        })
        cancelled = self.provider.cancel(operation)
        self.assertTrue(
            cancelled['provider_receipt']['cancel_request_accepted']
        )
        self.assertEqual(
            'pending-provider-observation',
            cancelled['provider_receipt']['outcome'],
        )
        self.assertFalse(cancelled['terminal'])
        self.assertEqual(
            [('query-tool-1', 10)], self.driver.manager._connection.cancelled
        )

    def test_data_studio_contributions_are_postgresql_owned(self):
        contributions = self.provider.data_studio_contributions()
        language = contributions['languages'][0]
        completion = contributions['completions'][0]
        session = contributions['sessions'][0]
        execution = contributions['executions'][0]
        self.assertEqual('postgresql-sql', language.language_profile)
        self.assertEqual(
            'postgresql.query-tool.completion',
            completion.contribution_id,
        )
        self.assertEqual(
            'postgresql.query-tool.session', session.contribution_id
        )
        self.assertEqual(
            'postgresql.query-tool.execution', execution.contribution_id
        )

    def test_data_studio_completion_uses_session_bound_adapter(self):
        session = self.provider.open_session({
            'route': {'server_id': 7, 'database_id': 10},
        })
        adapter = Mock()
        adapter.get_completions.return_value = [{'label': 'orders'}]
        self.provider._completion_adapters[session['session_id']] = adapter
        actual = self.provider.complete({
            'session_id': session['session_id'],
            'full_source': 'SELECT * FROM ord',
            'source_before_cursor': 'ord',
        })
        self.assertEqual([{'label': 'orders'}], actual)
        adapter.get_completions.assert_called_once_with(
            'SELECT * FROM ord', 'ord'
        )

    def test_result_contribution_normalizes_postgresql_tabular_rows(self):
        contribution = self.provider.result_contributions()['adapters'][0]
        provider_result = {
            'result_id': 'result-one',
            'execution_id': 'execution-one',
            'result_kind': 'tabular',
            'schema': {'columns': [{'name': 'id'}]},
            'complete': True,
            'continuation': None,
            'extensions': {
                'postgresql': {'rows': [[1], [2]]},
            },
        }
        descriptor = contribution.describe(None, provider_result)
        self.assertEqual(
            TABULAR_RENDER_CAPABILITY, contribution.required_capability
        )
        self.assertEqual([[1], [2]], descriptor['records'])
        self.assertEqual(
            'SchemaView/DataGridView',
            descriptor['component_reference'],
        )

    def test_result_renderer_selection_is_provider_owned(self):
        selected = self.provider.select_renderer({
            'result_kind': 'tabular',
        })
        self.assertEqual('result-renderer', selected['resource_kind'])
        self.assertEqual(
            [TABULAR_RENDER_CAPABILITY], selected['capability_ids']
        )
        self.assertEqual(
            'SchemaView/DataGridView',
            selected['extensions']['postgresql']['component_reference'],
        )

    def test_tool_dispositions_are_explicit_and_provider_local(self):
        tools = self.provider.list_tools({'route': {'server_id': 7}})
        self.assertEqual(set(TOOL_DISPOSITIONS), {
            item['display_name'] for item in tools
        })
        for item in tools:
            disposition = item['extensions']['postgresql']
            self.assertEqual('provider-local', disposition['ownership'])
            self.assertFalse(disposition['common_service'])

    def test_provider_instances_do_not_share_endpoint_state(self):
        second = PostgreSQLProvider(
            endpoint('two'), FakePermissions(), self.driver, self.catalog
        )
        self.provider.open_session({'route': {'server_id': 7}})
        self.assertTrue(self.provider._sessions)
        self.assertEqual({}, second._sessions)
        self.assertIsNot(self.provider._sessions, second._sessions)

    def test_non_operational_fixture_coexists_without_state(self):
        fixture = SimpleNamespace(
            validate_endpoint=Mock(), discover_endpoint=Mock()
        )
        self.provider.open_session({'route': {'server_id': 7}})
        self.assertFalse(hasattr(fixture, '_sessions'))
        self.assertNotIn(fixture, self.provider._sessions.values())


class PostgreSQLCatalogOwnershipTests(unittest.TestCase):

    def test_catalog_source_uses_existing_templates_for_all_three_levels(self):
        manager = FakeManager()
        driver = FakeDriver(manager)
        renderer = Mock(return_value='SELECT preserved_catalog')
        source = PostgreSQLCatalogSource(driver, renderer)
        manager._connection.execute_dict = Mock(
            return_value=(True, {'rows': [{'did': 10, 'name': 'appdb'}]})
        )
        manager._connection.execute_2darray = Mock(
            side_effect=(
                (True, {'rows': [{'oid': 20, 'name': 'public'}]}),
                (True, {'rows': [{'oid': 30, 'name': 'orders'}]}),
            )
        )
        source.read('database', {'server_id': 7})
        source.read('schema', {'server_id': 7, 'database_id': 10})
        source.read('table', {
            'server_id': 7, 'database_id': 10, 'schema_id': 20,
        })
        templates = [call.args[0] for call in renderer.call_args_list]
        self.assertEqual('databases/sql/#18.3#/nodes.sql', templates[0])
        self.assertEqual('schemas/pg/#18.3#/sql/nodes.sql', templates[1])
        self.assertEqual('tables/sql/#18.3#/nodes.sql', templates[2])

    def test_provider_does_not_copy_postgresql_sql_templates(self):
        self.assertEqual([], list(PROVIDER_ROOT.rglob('*.sql')))
        preserved = (
            WEB / 'pgadmin/browser/server_groups/servers/databases'
        )
        self.assertTrue(any(preserved.rglob('nodes.sql')))

    def test_unknown_resource_family_is_refused(self):
        source = PostgreSQLCatalogSource(FakeDriver(), Mock())
        with self.assertRaises(PostgreSQLProviderError):
            source.read('future-catalog', {'server_id': 7})


class PostgreSQLQueryToolRoutingTests(unittest.TestCase):

    def test_legacy_query_tool_manager_is_unchanged_without_context(self):
        manager = object()
        app = SimpleNamespace(extensions={})
        self.assertIs(manager, route_query_tool_manager(app, 7, manager))

    def test_verified_endpoint_routes_query_tool_through_provider_port(self):
        manifest = json.loads(MANIFEST_PATH.read_text(encoding='utf-8'))
        registry = ProviderRegistry()
        driver = FakeDriver()
        with patch(
            'pgadmin.cdeadmin.providers.postgresql.provider.'
            'DriverRegistry.get',
            return_value=driver,
        ):
            registry.register_package(
                manifest,
                'pgadmin.cdeadmin.providers.postgresql.provider',
            )
            app = SimpleNamespace(extensions={
                'cdeadmin_provider_registry': registry,
            })
            with endpoint_scope(endpoint()):
                actual = route_query_tool_manager(
                    app, 7, driver.manager
                )
                execution = route_query_tool_execute(
                    app,
                    driver.manager._connection,
                    'SELECT 1',
                )
                polled = route_query_tool_poll(
                    app, driver.manager._connection, no_result=True
                )
                fetched = route_query_tool_fetch(
                    app, driver.manager._connection, 100
                )
        self.assertIs(driver.manager, actual)
        self.assertEqual([], driver.server_ids)
        self.assertEqual((True, None), execution)
        self.assertEqual((1, None), polled)
        self.assertEqual((True, [[1, 'row']]), fetched)

    def test_query_tool_source_keeps_postgresql_transactions_local(self):
        source = (
            WEB / 'pgadmin/tools/sqleditor/utils/start_running_query.py'
        ).read_text(encoding='utf-8')
        self.assertIn('route_query_tool_execute(', source)
        self.assertIn('conn.execute_void("BEGIN;")', source)
        self.assertIn('conn.execute_void("ROLLBACK;")', source)
        result_source = (
            WEB / 'pgadmin/tools/sqleditor/__init__.py'
        ).read_text(encoding='utf-8')
        self.assertIn('route_query_tool_poll(', result_source)
        self.assertIn('route_query_tool_fetch(', result_source)
        core_source = ' '.join(
            path.read_text(encoding='utf-8')
            for path in (WEB / 'pgadmin/cdeadmin/core').glob('*.py')
        )
        self.assertNotIn('execute_void("BEGIN;")', core_source)
        self.assertNotIn('execute_void("ROLLBACK;")', core_source)


if __name__ == '__main__':
    unittest.main()
