##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""ClickHouse 25.12 HTTP/JSON provider and administration tests."""

from __future__ import annotations

import json
import sys
import unittest
import urllib.parse
from pathlib import Path
from types import ModuleType


ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / 'web'
if str(WEB) not in sys.path:
    sys.path.insert(0, str(WEB))
if 'pgadmin' not in sys.modules:
    package = ModuleType('pgadmin')
    package.__path__ = [str(WEB / 'pgadmin')]
    sys.modules['pgadmin'] = package

from pgadmin.cdeadmin.providers.clickhouse.client import (  # noqa: E402
    ClickHouseClient,
    ClickHouseClientError,
    ClickHouseUnknownOutcomeError,
)
from pgadmin.cdeadmin.providers.clickhouse.provider import (  # noqa: E402
    PROFILE,
)
from pgadmin.cdeadmin.results.renderers import builtin_renderers  # noqa: E402
from pgadmin.cdeadmin.visual_admin.catalog import (  # noqa: E402
    catalog_for_engine,
)


class SecretLease:
    def __init__(self, value=b'qualification-password'):
        self.value = bytearray(value)
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        for index in range(len(self.value)):
            self.value[index] = 0
        self.closed = True

    def use(self, callback):
        return callback(memoryview(self.value))


class Response:
    status = 200
    headers = {'X-ClickHouse-Summary': '{}'}

    def __init__(self, document):
        self.payload = (
            b'' if document is None
            else json.dumps(document).encode('utf-8')
        )

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self, _maximum):
        return self.payload


def result(data=(), meta=()):
    return {
        'meta': list(meta), 'data': list(data), 'rows': len(data),
        'statistics': {'elapsed': 0.001, 'rows_read': len(data)},
    }


class HTTPFactory:
    def __init__(self, version='25.12.10.7'):
        self.version = version
        self.requests = []

    def __call__(self, request, timeout, context):
        body = request.data.decode('utf-8')
        query = body.split('\n', 1)[0]
        args = urllib.parse.parse_qs(
            urllib.parse.urlsplit(request.full_url).query
        )
        headers = dict(request.header_items())
        self.requests.append({
            'query': query, 'body': body, 'args': args,
            'headers': headers, 'timeout': timeout, 'context': context,
        })
        if 'version() AS version' in query:
            return Response(result([{
                'version': self.version, 'revision': 54490,
            }], [
                {'name': 'version', 'type': 'String'},
                {'name': 'revision', 'type': 'UInt32'},
            ]))
        if 'FROM system.databases' in query:
            return Response(result([{
                'name': 'qualification', 'engine': 'Atomic', 'comment': '',
            }]))
        if 'FROM system.tables' in query:
            return Response(result([{
                'database': 'qualification', 'name': 'widgets',
                'engine': 'MergeTree', 'primary_key': 'id',
            }]))
        if 'FROM system.columns' in query:
            if 'is_in_primary_key = 1' in query:
                return Response(result([{'name': 'id', 'type': 'UInt64'}]))
            if query.startswith('SELECT type'):
                return Response(result([{'type': 'String'}]))
            return Response(result([{
                'database': 'qualification', 'table': 'widgets',
                'name': 'id', 'type': 'UInt64', 'position': 1,
            }]))
        if 'FROM system.parts' in query:
            return Response(result([{
                'database': 'qualification', 'table': 'widgets',
                'partition_id': 'all', 'rows': 1, 'bytes_on_disk': 100,
                'part_count': 1,
            }]))
        if 'FROM system.users' in query:
            return Response(result([{
                'name': 'default', 'id': 'one', 'storage': 'users.xml',
                'auth_type': ['no_password'],
            }]))
        if 'FROM system.roles' in query:
            return Response(result([{
                'name': 'analyst', 'id': 'two', 'storage': 'local',
            }]))
        if 'FROM system.row_policies' in query:
            return Response(result([]))
        if 'SELECT * FROM `qualification`.`widgets`' in query:
            return Response(result(
                [{'id': 1, 'value': 'before'}],
                [
                    {'name': 'id', 'type': 'UInt64'},
                    {'name': 'value', 'type': 'String'},
                ],
            ))
        if query.startswith('SELECT'):
            return Response(result(
                [{'answer': 42}], [{'name': 'answer', 'type': 'UInt8'}]
            ))
        return Response(None)


def route(**changes):
    value = {
        'route_id': 'clickhouse-qualification',
        'host': '127.0.0.1', 'port': 8123,
        'database': 'qualification', 'username': 'cdeadmin',
        'tls_mode': 'disable', 'connect_timeout': 2,
        'statement_timeout': 5,
    }
    value.update(changes)
    return value


def target(kind='table', **changes):
    native = {
        'database': 'qualification', 'table': 'widgets', 'name': 'widgets',
    }
    native.update(changes)
    return {
        'resource_kind': kind,
        'extensions': {'clickhouse': {'native': native}},
    }


class ClickHouseProviderTestCase(unittest.TestCase):
    def setUp(self):
        self.http = HTTPFactory()
        self.leases = []
        self.secret_requests = []

        def acquire(*args):
            self.secret_requests.append(args)
            lease = SecretLease()
            self.leases.append(lease)
            return lease

        self.client = ClickHouseClient(
            secret_acquirer=acquire, urlopen=self.http
        )

    def tearDown(self):
        self.client.close()

    def test_profile_and_manifest_record_live_activation(self):
        self.assertEqual('25.12.10.7-stable', PROFILE.exact_version)
        self.assertEqual('columnar-analytic', PROFILE.model_family)
        self.assertEqual('http_json', PROFILE.protocol_id)
        manifest = json.loads((
            WEB / 'pgadmin/cdeadmin/providers/clickhouse/'
            'provider_manifest.json'
        ).read_text(encoding='utf-8'))
        self.assertTrue(manifest['enabled'])
        self.assertTrue(manifest['production_registration'])
        self.assertEqual('experimental', manifest['support_state'])
        self.assertEqual(
            'passed', manifest['provenance']['activation_gate']
        )
        self.assertFalse(manifest['registration']['requires_secret'])
        self.assertTrue(manifest['registration']['supports_secret'])

    def test_exact_runtime_identity_is_required(self):
        identity = self.client.runtime_identity({'route': route()})
        self.assertEqual('25.12.10.7-stable', identity['version'])
        self.assertIn('revision 54490', identity['build_id'])
        wrong = ClickHouseClient(urlopen=HTTPFactory('25.12.10.8'))
        with self.assertRaisesRegex(ClickHouseClientError, 'exact'):
            wrong.runtime_identity({'route': route()})

    def test_route_rejects_inline_secrets_and_endpoint_injection(self):
        with self.assertRaisesRegex(ClickHouseClientError, 'inline'):
            self.client.open_session({'route': route(password='unsafe')})
        with self.assertRaisesRegex(ClickHouseClientError, 'host'):
            self.client.open_session({'route': route(
                host='127.0.0.1/?query=DROP'
            )})
        with self.assertRaisesRegex(ClickHouseClientError, 'CA file'):
            self.client.open_session({'route': route(
                tls_mode='verify-full'
            )})

    def test_secret_lease_authenticates_without_source_exposure(self):
        request_route = route(
            credential_reference_id='clickhouse-password',
            principal_reference='operator-one',
        )
        self.client.runtime_identity({'route': request_route})
        sent = self.http.requests[-1]
        self.assertEqual(
            'qualification-password',
            sent['headers']['X-clickhouse-key'],
        )
        self.assertNotIn('qualification-password', sent['query'])
        self.assertTrue(self.leases[-1].closed)
        self.assertEqual(
            ('clickhouse-password', 'operator-one', 'connect',
             'database_password'),
            self.secret_requests[-1],
        )

    def test_columnar_execution_and_transaction_observation(self):
        session = self.client.open_session({'route': route()})
        token = self.client.execute(session, {'source': 'SELECT 42 AS answer'})
        described = self.client.describe_result(token)
        self.assertEqual('columnar', described['result_kind'])
        self.assertEqual(42, described['payload']['rows'][0]['answer'])
        self.assertEqual(
            1, described['schema']['statistics']['rows_read']
        )
        transaction = self.client.describe_transaction(session)
        self.assertTrue(transaction['statement_atomicity_only'])
        self.assertFalse(transaction['multi_statement_transaction_supported'])
        self.assertFalse(transaction['automatic_replay'])
        self.assertFalse(self.client.cancel(token))

    def test_mutations_request_sync_and_transport_failure_is_unknown(self):
        session = self.client.open_session({'route': route()})
        self.client.execute(session, {
            'source': 'ALTER TABLE widgets DELETE WHERE id = 1',
        })
        args = self.http.requests[-1]['args']
        self.assertEqual(['2'], args['mutations_sync'])
        self.assertEqual(['2'], args['alter_sync'])

        def fail(*_args):
            raise TimeoutError('connection lost')

        failing = ClickHouseClient(urlopen=fail)
        failing_session = failing.open_session({'route': route()})
        with self.assertRaises(ClickHouseUnknownOutcomeError):
            failing.execute(failing_session, {
                'source': 'INSERT INTO widgets VALUES (1)',
            })

    def test_resources_cover_columnar_and_access_control_objects(self):
        resources = self.client.list_resources({'route': route()})
        kinds = {item['resource_kind'] for item in resources}
        self.assertTrue({
            'server', 'database', 'table', 'column', 'partition',
            'user', 'role',
        }.issubset(kinds))
        partition = next(
            item for item in resources
            if item['resource_kind'] == 'partition'
        )
        self.assertEqual('all', partition['display_name'])
        security = self.client.describe_security({'route': route()})
        self.assertFalse(security['native']['passwords_exposed'])

    def test_visual_catalog_exposes_native_forms_for_all_profile_kinds(self):
        catalog = self.client.visual_admin_catalog(
            catalog_for_engine('clickhouse')
        )
        objects = {item['resource_kind']: item for item in catalog['objects']}
        self.assertEqual(set(PROFILE.resource_kinds), set(objects))
        self.assertFalse(catalog['common_finality_interpretation'])
        self.assertTrue(all(
            operation.get('form')
            for item in objects.values()
            for operation in item['operations']
        ))

    def test_structured_table_ddl_rejects_second_statement(self):
        request = {
            'resource_kind': 'table', 'operation_id': 'create',
            '_provider_route': route(),
            'draft': {
                'database': 'qualification', 'name': 'events',
                'columns': [
                    {'name': 'id', 'type': 'UInt64'},
                    {'name': 'payload', 'type': 'String'},
                ],
                'engine': 'MergeTree', 'order_by': 'id',
                'partition_by': 'toYYYYMM(event_time)',
            },
        }
        plan = self.client.plan_admin_operation(request)
        source, _parameters, _rows = self.client._compile_admin(
            plan['provider_payload'], self.client._route({'route': route()})
        )
        self.assertIn('ENGINE = MergeTree()', source)
        self.assertIn('ORDER BY id', source)
        request['draft']['order_by'] = 'id; DROP DATABASE default'
        bad = self.client.plan_admin_operation(request)
        with self.assertRaisesRegex(ClickHouseClientError, 'one expression'):
            self.client._compile_admin(
                bad['provider_payload'],
                self.client._route({'route': route()}),
            )

    def test_materialized_view_without_destination_has_provider_storage(self):
        request = {
            'resource_kind': 'materialized-view', 'operation_id': 'create',
            '_provider_route': route(),
            'draft': {
                'database': 'qualification', 'name': 'semantic_rollup',
                'engine': 'MergeTree', 'order_by': 'tuple()',
                'select': 'SELECT category, sum(value) AS total '
                'FROM qualification.events GROUP BY category',
            },
        }
        plan = self.client.plan_admin_operation(request)
        source, _parameters, _rows = self.client._compile_admin(
            plan['provider_payload'], self.client._route({'route': route()})
        )
        self.assertIn(
            'ENGINE = MergeTree() ORDER BY tuple() AS SELECT', source
        )
        drop = self.client.plan_admin_operation({
            'resource_kind': 'materialized-view', 'operation_id': 'drop',
            '_provider_route': route(),
            'target_resource': target(
                'materialized-view', name='semantic_rollup'
            ),
            'draft': {'acknowledge_drop': True},
        })
        drop_source, _parameters, _rows = self.client._compile_admin(
            drop['provider_payload'], self.client._route({'route': route()})
        )
        self.assertEqual(
            'DROP VIEW `qualification`.`semantic_rollup`', drop_source
        )

    def test_editable_grid_uses_route_bound_single_use_primary_key(self):
        page = self.client.read_admin_rows({
            '_provider_route': route(),
            'target_resource': target(), 'limit': 20, 'offset': 0,
        })
        self.assertTrue(page['editable'])
        self.assertEqual(['id'], page['row_identity_columns'])
        token = page['rows'][0]['concurrency_token']
        request = {
            'resource_kind': 'table', 'operation_id': 'update',
            'target_resource': target(), '_provider_route': route(),
            'draft': {
                'selector': {'id': 1}, 'concurrency_token': token,
                'changes': {'value': 'after'},
            },
        }
        plan = self.client.plan_admin_operation(request)
        self.client.apply_admin_operation({
            'provider_payload': plan['provider_payload']
        })
        mutation = self.http.requests[-1]
        self.assertIn('UPDATE `value` = {cde_change_0:String}',
                      mutation['query'])
        self.assertEqual(['2'], mutation['args']['mutations_sync'])
        with self.assertRaisesRegex(ClickHouseClientError, 'absent or stale'):
            self.client.apply_admin_operation({
                'provider_payload': plan['provider_payload']
            })

    def test_grid_refuses_primary_key_update(self):
        page = self.client.read_admin_rows({
            '_provider_route': route(), 'target_resource': target(),
        })
        request = {
            'resource_kind': 'table', 'operation_id': 'update',
            'target_resource': target(), '_provider_route': route(),
            'draft': {
                'selector': {'id': 1},
                'concurrency_token': page['rows'][0]['concurrency_token'],
                'changes': {'id': 2},
            },
        }
        plan = self.client.plan_admin_operation(request)
        with self.assertRaisesRegex(ClickHouseClientError, 'primary-key'):
            self.client.apply_admin_operation({
                'provider_payload': plan['provider_payload']
            })

    def test_columnar_renderer_and_frontend_are_production_capable(self):
        renderers = {item.renderer_id: item for item in builtin_renderers()}
        renderer = renderers['cdeadmin.result.columnar.grid']
        self.assertFalse(renderer.fixture_safe)
        self.assertEqual(
            'cdeadmin/results/ColumnarView', renderer.component_reference
        )
        source = (WEB / 'pgadmin/static/js/Dialogs/'
                  'ProviderWorkspaceContent.jsx').read_text(encoding='utf-8')
        self.assertIn('function ColumnarView', source)
        self.assertIn('cdeadmin/results/ColumnarView', source)


if __name__ == '__main__':
    unittest.main()
