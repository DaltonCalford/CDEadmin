##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Contracts for the live-qualified XTDB 2.1 provider."""

from __future__ import annotations

import sys
import unittest
import uuid
import json
from unittest.mock import patch
from pathlib import Path
from types import ModuleType, SimpleNamespace


ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / 'web'
if str(WEB) not in sys.path:
    sys.path.insert(0, str(WEB))
if 'pgadmin' not in sys.modules:
    package = ModuleType('pgadmin')
    package.__path__ = [str(WEB / 'pgadmin')]
    sys.modules['pgadmin'] = package

from pgadmin.cdeadmin.core import EndpointContext  # noqa: E402
from pgadmin.cdeadmin.providers.xtdb.client import (  # noqa: E402
    XTDBClient,
    XTDBClientError,
    XTDBDependencyError,
    XTDBUnknownOutcomeError,
)
from pgadmin.cdeadmin.providers.xtdb.provider import (  # noqa: E402
    PROFILE,
    XTDBPilotProvider,
)


class FakeAdapters:
    def __init__(self):
        self.registered = []

    def register_dumper(self, value_type, dumper):
        self.registered.append((value_type, dumper))


class FakeCursor:
    def __init__(self, connection):
        self.connection = connection
        self.description = None
        self.rows = []
        self.rowcount = -1
        self.closed = False

    def execute(self, source, parameters=None):
        source_text = str(source)
        self.connection.calls.append((source_text, parameters))
        if self.connection.fail_next:
            self.connection.fail_next = False
            raise ConnectionError('lost response')
        lowered = source_text.lower()
        if 'xt.version()' in lowered:
            self._rows(['xtdb_version'], [(self.connection.version,)])
        elif 'from information_schema.tables' in lowered:
            self._rows(
                ['table_schema', 'table_name', 'table_type'],
                [
                    ('public', 'people', 'BASE TABLE'),
                    ('xt', 'txs', 'BASE TABLE'),
                ],
            )
        elif 'from information_schema.columns' in lowered:
            self._rows(
                ['table_schema', 'table_name', 'column_name', 'data_type'],
                [
                    ('public', 'people', '_id', 'utf8'),
                    ('public', 'people', 'name', 'utf8'),
                ],
            )
        elif 'from pg_catalog.pg_user' in lowered:
            self._rows(
                ['username', 'usesuper'], [('xtdb', True), ('reader', False)]
            )
        elif 'from "public"."people"' in lowered:
            self._rows(
                [
                    '_id', 'name', '__cde_valid_from', '__cde_valid_to',
                    '__cde_system_from', '__cde_system_to',
                ],
                [
                    (
                        'person-1', 'Ada', '2026-01-01T00:00:00Z', None,
                        '2026-01-02T00:00:00Z', None,
                    ),
                ],
            )
        elif lowered.startswith(('update ', 'delete ')):
            self.description = None
            self.rows = []
            self.rowcount = 0
        else:
            self.description = None
            self.rows = []
            self.rowcount = 1

    def _rows(self, names, rows):
        self.description = [(name, 'text') for name in names]
        self.rows = list(rows)
        self.rowcount = len(self.rows)

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return list(self.rows)

    def fetchmany(self, count):
        return list(self.rows[:count])

    def close(self):
        self.closed = True


class FakeConnection:
    def __init__(self, version='XTDB @ 2.1.0'):
        self.version = version
        self.adapters = FakeAdapters()
        self.calls = []
        self.fail_next = False
        self.autocommit = True
        self.closed = False
        self.cancelled = False

    def cursor(self):
        return FakeCursor(self)

    def cancel(self):
        self.cancelled = True

    def close(self):
        self.closed = True


class FakeModule:
    __version__ = '3.3.4'

    class StrDumperVarchar:
        pass

    class Jsonb:
        def __init__(self, value):
            self.obj = value

    types = SimpleNamespace(
        string=SimpleNamespace(StrDumperVarchar=StrDumperVarchar),
        json=SimpleNamespace(Jsonb=Jsonb),
    )

    def __init__(self, version='XTDB @ 2.1.0'):
        self.version = version
        self.connections = []
        self.connect_kwargs = []

    def connect(self, **kwargs):
        self.connect_kwargs.append(kwargs)
        connection = FakeConnection(self.version)
        self.connections.append(connection)
        return connection


class Lease:
    def __init__(self, value):
        self.value = value.encode('utf-8')

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def use(self, callback):
        return callback(memoryview(self.value))


class FakeHTTPResponse:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    @staticmethod
    def read(_limit):
        return b'Ready.'


class Permissions:
    def __init__(self):
        self.calls = []

    def require(self, permission, scope='endpoint'):
        self.calls.append((permission, scope))

    @staticmethod
    def allows(_permission, _scope='endpoint'):
        return True


def acquire(reference, principal, purpose, secret_type):
    if not all((reference, principal, purpose, secret_type)):
        raise AssertionError('secret acquisition contract is incomplete')
    return Lease('correct horse battery staple')


def route(label='one'):
    return {
        'route_id': label, 'host': '127.0.0.1', 'port': 5432,
        'database': 'xtdb', 'username': 'xtdb', 'tls_mode': 'disable',
        'connect_timeout': 10, 'statement_timeout': 30,
        'application_name': 'CDEadmin',
        'credential_reference_id': 'credential-one',
        'principal_reference': 'principal-one',
    }


def context():
    endpoint = uuid.uuid5(uuid.NAMESPACE_URL, 'xtdb:test')

    def child(value):
        return str(uuid.uuid5(endpoint, value))

    return EndpointContext(
        endpoint_id=str(endpoint), mode='legacy_native',
        experience_family='xtdb', provider_id=PROFILE.provider_id,
        provider_version='0.1.0', profile_id=PROFILE.profile_id,
        profile_version=PROFILE.exact_version,
        target_adapter_id='psycopg-xtdb-pgwire-client',
        target_adapter_version='psycopg-c-3.3.4',
        pool_namespace=child('pool'), session_namespace=child('session'),
        cache_namespace=child('cache'),
        diagnostic_namespace=child('diagnostic'),
        effective_permissions=frozenset({
            'network', 'secret_read', 'data_read', 'data_write',
            'administer', 'execute', 'filesystem',
        }),
        runtime_verification_state='verified',
        declared_runtime_family='xtdb', verified_runtime_family='xtdb',
    )


class XTDBClientContractTests(unittest.TestCase):

    def setUp(self):
        self.module = FakeModule()
        self.client = XTDBClient(acquire, self.module)

    def test_driver_version_is_pinned(self):
        wrong = FakeModule()
        wrong.__version__ = '3.3.5'
        with self.assertRaises(XTDBDependencyError):
            XTDBClient(acquire, wrong)

    def test_route_rejects_unknown_fields_and_tls_downgrade(self):
        with self.assertRaisesRegex(XTDBClientError, 'unknown fields'):
            self.client.open_session({'route': {**route(), 'password': 'x'}})
        with self.assertRaisesRegex(XTDBClientError, 'authority file'):
            self.client.open_session({
                'route': {**route(), 'tls_mode': 'verify-full'}
            })

    def test_exact_native_identity_and_string_dumper(self):
        identity = self.client.runtime_identity({'route': route()})
        self.assertEqual('xtdb', identity['engine_id'])
        self.assertEqual('2.1.0', identity['version'])
        self.assertEqual('postgresql_wire', identity['protocol_id'])
        connection = self.module.connections[0]
        self.assertTrue(connection.adapters.registered)
        self.assertNotIn('password', route())
        self.assertEqual(
            'correct horse battery staple',
            self.module.connect_kwargs[0]['password'],
        )

    def test_postgresql_compatibility_version_is_not_identity(self):
        client = XTDBClient(acquire, FakeModule('PostgreSQL 16'))
        with self.assertRaisesRegex(XTDBClientError, 'did not prove XTDB'):
            client.runtime_identity({'route': route()})

    def test_query_results_are_bitemporal_documents(self):
        session = self.client.open_session({'route': route()})
        token = self.client.execute(session, {
            'source': 'SELECT * FROM "public"."people"', 'parameters': [],
        })
        result = self.client.describe_result(token)
        self.assertEqual('document', result['result_kind'])
        self.assertEqual('person-1', result['payload']['rows'][0]['_id'])
        self.assertTrue(self.client.cancel(token))
        self.assertTrue(session.connection.cancelled)

    def test_read_only_route_refuses_mutation(self):
        session = self.client.open_session({
            'route': {**route(), 'read_only': True}
        })
        with self.assertRaisesRegex(XTDBClientError, 'read-only'):
            self.client.execute(session, {
                'source': 'INSERT INTO people (_id) VALUES (%s)',
                'parameters': ['one'],
            })

    def test_uncertain_mutation_is_not_retried(self):
        session = self.client.open_session({'route': route()})
        session.connection.fail_next = True
        with self.assertRaises(XTDBUnknownOutcomeError) as raised:
            self.client.execute(session, {
                'source': 'INSERT INTO people (_id) VALUES (%s)',
                'parameters': ['one'],
            })
        self.assertFalse(raised.exception.retryable)
        self.assertEqual(1, len(session.connection.calls))

    def test_resources_and_security_are_native_and_redacted(self):
        resources = self.client.list_resources({'route': route()})
        kinds = {item['resource_kind'] for item in resources}
        self.assertTrue({
            'cluster', 'node', 'database', 'schema', 'table', 'column',
            'document', 'entity', 'valid-time', 'system-time',
            'transaction-log',
            'user',
        }.issubset(kinds))
        security = self.client.describe_security({'route': route()})
        self.assertFalse(security['native']['passwords_exposed'])
        self.assertNotIn('passwd', str(security).lower())
        self.assertFalse(
            security['native']['roles_and_grants_supported']
        )

    def test_catalog_exposes_only_native_operations(self):
        from pgadmin.cdeadmin.visual_admin.catalog import catalog_for_engine

        catalog = self.client.visual_admin_catalog(
            catalog_for_engine('xtdb')
        )
        table = next(
            item for item in catalog['objects']
            if item['resource_kind'] == 'table'
        )
        operations = {item['operation_id'] for item in table['operations']}
        self.assertIn('erase', operations)
        self.assertTrue(
            self.client.supports_admin_operation('table', 'create')
        )
        self.assertFalse(
            self.client.supports_admin_operation('table', 'drop')
        )
        self.assertFalse(
            self.client.supports_admin_operation('index', 'create')
        )
        self.assertFalse(catalog['xtql_transport_qualified'])
        forms = {
            item['resource_kind']: next(
                operation['form'] for operation in item['operations']
                if operation['operation_id'] == 'inspect'
            )
            for item in catalog['objects']
            if item['resource_kind'] in {'valid-time', 'system-time'}
        }
        defaults = {
            kind: {
                field['field_id']: field.get('default')
                for field in form['fields']
            }
            for kind, form in forms.items()
        }
        self.assertEqual('all', defaults['valid-time']['valid_time_mode'])
        self.assertEqual('all', defaults['system-time']['system_time_mode'])

    def test_editable_page_uses_route_bound_single_use_identity(self):
        target = {
            'native': {
                'database': 'xtdb', 'schema': 'public', 'table': 'people'
            }
        }
        page = self.client.read_admin_rows({
            '_provider_route': route(), 'target_resource': target, 'limit': 10,
        })
        self.assertTrue(page['editable'])
        token = page['rows'][0]['identity_token']
        plan = self.client.plan_admin_operation({
            'resource_kind': 'table', 'operation_id': 'update',
            'target_resource': target,
            'draft': {
                'selector': {'identity_token': token},
                'changes': {'name': 'Grace'},
                'concurrency_token': token, 'options': {},
            },
            '_provider_route': route(),
        })
        result = self.client.apply_admin_operation({
            'provider_payload': plan['provider_payload']
        })
        self.assertTrue(result['native_response_observed'])
        with self.assertRaisesRegex(XTDBClientError, 'absent or stale'):
            self.client.apply_admin_operation({
                'provider_payload': plan['provider_payload']
            })

    def test_row_identity_cannot_cross_routes(self):
        target = {'native': {'schema': 'public', 'table': 'people'}}
        page = self.client.read_admin_rows({
            '_provider_route': route('one'),
            'target_resource': target, 'limit': 10,
        })
        token = page['rows'][0]['identity_token']
        plan = self.client.plan_admin_operation({
            'resource_kind': 'table', 'operation_id': 'delete',
            'target_resource': target,
            'draft': {
                'selector': {'identity_token': token},
                'concurrency_token': token,
                'confirmation': 'provider-row-delete', 'options': {},
            },
            '_provider_route': route('two'),
        })
        with self.assertRaisesRegex(XTDBClientError, 'another endpoint'):
            self.client.apply_admin_operation({
                'provider_payload': plan['provider_payload']
            })

    def test_database_and_user_plans_never_expose_password(self):
        user_plan = self.client.plan_admin_operation({
            'resource_kind': 'user', 'operation_id': 'create',
            'target_resource': None,
            'draft': {
                'username': 'ada',
                'password_reference': 'new-password-secret',
            },
            '_provider_route': route(),
        })
        self.assertIn('<redacted>', str(user_plan['command_preview']))
        self.assertNotIn('correct horse', str(user_plan))
        user_result = self.client.apply_admin_operation({
            'provider_payload': user_plan['provider_payload']
        })
        self.assertTrue(user_result['native_response_observed'])
        self.assertEqual(
            'CREATE USER ada WITH PASSWORD \'correct horse battery staple\'',
            self.module.connections[-1].calls[-1][0],
        )
        database_plan = self.client.plan_admin_operation({
            'resource_kind': 'database', 'operation_id': 'create',
            'target_resource': None,
            'draft': {
                'name': 'history',
                'config_yaml': 'log: !Local\n  path: history/log',
            },
            '_provider_route': route(),
        })
        result = self.client.apply_admin_operation({
            'provider_payload': database_plan['provider_payload']
        })
        self.assertTrue(result['native_response_observed'])

    def test_visual_database_plan_accepts_bounded_multiline_yaml(self):
        provider = XTDBPilotProvider(context(), Permissions(), self.client)
        plan = provider.plan_visual_admin({
            'resource_kind': 'database', 'operation_id': 'create',
            'target_resource': None,
            'draft': {
                'name': 'history',
                'config_yaml': (
                    'log: !InMemory\n'
                    'storage: !InMemory\n'
                ),
            },
            '_provider_route': route(),
        })
        self.assertEqual('ready', plan['state'])

    def test_user_names_reject_delimited_or_injectable_forms(self):
        for username in ('quoted user', '"quoted"', 'name;select'):
            result = self.client.validate_admin_operation({
                'resource_kind': 'user', 'operation_id': 'create',
                'target_resource': None,
                'draft': {
                    'username': username,
                    'password_reference': 'new-password-secret',
                },
            })
            self.assertTrue(result['errors'], username)

    def test_health_and_finish_block_are_bounded_provider_operations(self):
        target = {'native': {'healthz_url': 'http://127.0.0.1:8080'}}
        inspect_plan = self.client.plan_admin_operation({
            'resource_kind': 'health', 'operation_id': 'inspect',
            'target_resource': target, 'draft': {'check': 'ready'},
            '_provider_route': {
                **route(), 'healthz_url': 'http://127.0.0.1:8080'
            },
        })
        with patch(
            'pgadmin.cdeadmin.providers.xtdb.client.urllib.request.urlopen',
            return_value=FakeHTTPResponse(),
        ) as request:
            result = self.client.apply_admin_operation({
                'provider_payload': inspect_plan['provider_payload']
            })
        self.assertEqual(200, result['http_status'])
        self.assertEqual('GET', request.call_args.args[0].method)

        finish_plan = self.client.plan_admin_operation({
            'resource_kind': 'health', 'operation_id': 'execute',
            'target_resource': target,
            'draft': {
                'action': 'finish-block', 'acknowledge_operation': True,
            },
            '_provider_route': {
                **route(), 'healthz_url': 'http://127.0.0.1:8080'
            },
        })
        self.assertEqual(
            'POST /system/finish-block',
            finish_plan['command_preview']['statement'],
        )

    def test_provider_descriptor_marks_unavailable_postgres_surfaces(self):
        provider = XTDBPilotProvider(context(), Permissions(), self.client)
        descriptor = provider.visual_admin_descriptor()
        coverage = descriptor['concept_coverage']
        self.assertTrue(coverage['declaration_ready'])
        self.assertEqual(0, coverage['undeclared_count'])
        self.assertEqual(0, coverage['blocking_missing_count'])
        self.assertEqual(
            ['document', 'relational', 'bitemporal'],
            [item['family_id'] for item in coverage['families']],
        )
        temporal = next(
            item for item in descriptor['objects']
            if item['resource_kind'] == 'valid-time'
        )
        self.assertEqual('temporal', temporal['navigator']['group_id'])
        self.assertEqual(
            'bitemporal-object', temporal['editor']['editor_kind']
        )
        index = next(
            item for item in descriptor['objects']
            if item['resource_kind'] == 'index'
        )
        create = next(
            item for item in index['operations']
            if item['operation_id'] == 'create'
        )
        self.assertFalse(create['native_supported'])
        self.assertIn('provider_operation_unavailable', create['blockers'])
        table = next(
            item for item in descriptor['objects']
            if item['resource_kind'] == 'table'
        )
        erase = next(
            item for item in table['operations']
            if item['operation_id'] == 'erase'
        )
        self.assertTrue(erase['confirmation_required'])
        self.assertTrue(erase['execution_available'])

    def test_manifest_records_live_qualified_activation(self):
        provider_root = WEB / 'pgadmin/cdeadmin/providers/xtdb'
        manifest = json.loads(
            (provider_root / 'provider_manifest.json').read_text(
                encoding='utf-8'
            )
        )
        matrix = json.loads(
            (provider_root / 'compatibility_matrix.json').read_text(
                encoding='utf-8'
            )
        )
        self.assertTrue(manifest['enabled'])
        self.assertTrue(manifest['production_registration'])
        self.assertEqual('experimental', manifest['support_state'])
        self.assertEqual(
            'production_experimental_provider',
            matrix['activation_state'],
        )

    def test_bitemporal_renderer_and_frontend_are_present(self):
        from pgadmin.cdeadmin.results.renderers import builtin_renderers

        renderer = next(
            item for item in builtin_renderers()
            if item.renderer_id == (
                'cdeadmin.result.bitemporal-document.inspector'
            )
        )
        view = renderer.render({
            'result_kind': 'document', 'schema': {'columns': []}
        }, [{'_id': 'one', '_system_from': 'now'}])
        self.assertEqual('bitemporal_document', view['family'])
        self.assertEqual('_id', view['temporal_fields']['identity'])
        frontend = (
            WEB / 'pgadmin/static/js/Dialogs/ProviderWorkspaceContent.jsx'
        ).read_text(encoding='utf-8')
        self.assertIn('function BitemporalDocumentView', frontend)
        self.assertIn('cdeadmin/results/BitemporalDocumentView', frontend)


if __name__ == '__main__':
    unittest.main()
