##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Executable relational administration and row-identity contract tests."""

from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
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

from pgadmin.cdeadmin.providers.sqlite.provider import (  # noqa: E402
    PROFILE,
    _resources,
    _route_arguments,
)
from pgadmin.cdeadmin.providers.firebird.provider import (  # noqa: E402
    ADMINISTRATION as FIREBIRD_ADMINISTRATION,
)
from pgadmin.cdeadmin.providers.duckdb.provider import (  # noqa: E402
    ADMINISTRATION as DUCKDB_ADMINISTRATION,
    _create_client as duckdb_client,
)
from pgadmin.cdeadmin.providers.mysql_family.provider import (  # noqa: E402
    MARIADB_ADMINISTRATION,
    MYSQL_ADMINISTRATION,
)
from pgadmin.cdeadmin.providers.relational_admin import (  # noqa: E402
    RelationalAdministration,
    RelationalAdminDialect,
)
from pgadmin.cdeadmin.sdk import (  # noqa: E402
    PilotProfile,
    RelationalClientConfig,
    RelationalClientError,
    RelationalDBAPIClient,
)
from pgadmin.cdeadmin.visual_admin import (  # noqa: E402
    ProviderVisualAdministration,
)


class Permissions:
    @staticmethod
    def allows(_permission, _scope='resource'):
        return True

    @staticmethod
    def require(_permission, _scope='resource'):
        return None


class AdministrationClient:
    def __init__(self, administration):
        self.administration = administration

    def supports_admin_operation(self, resource_kind, operation_id):
        return self.administration.supports(resource_kind, operation_id)

    def visual_admin_catalog(self, catalog):
        return self.administration.catalog(catalog)

    def validate_admin_operation(self, request):
        return self.administration.validate(request)

    def plan_admin_operation(self, request):
        return self.administration.plan(request)

    @staticmethod
    def apply_admin_operation(_request):
        return {'accepted': True}


def client_and_admin():
    profile = PilotProfile(
        'org.cdeadmin.sqlite.admin-test', 'sqlite-admin-test',
        'sqlite', 'SQLite', sqlite3.sqlite_version, 'embedded_sqlite',
        'relational', 'sqlite-sql', 'SQLite SQL',
        'sqlite-native-transaction', 'tabular', PROFILE.resource_kinds,
        PROFILE.admin_tools, PROFILE.required_permissions,
    )
    admin = RelationalAdministration(RelationalAdminDialect(
        engine_id='sqlite', supports_cascade=False,
        database_create_mode='embedded-file',
        database_extension='.sqlite',
        supported={
            'database': frozenset({'inspect', 'create'}),
            'column': frozenset({'inspect', 'create', 'rename', 'drop'}),
            'index': frozenset({'inspect', 'create', 'drop'}),
            'virtual-table': frozenset({
                'inspect', 'create', 'rename', 'drop',
            }),
            'fts-table': frozenset({
                'inspect', 'create', 'rename', 'drop',
            }),
            'table': frozenset({
                'inspect', 'create', 'alter', 'rename', 'drop',
                'insert', 'update', 'delete',
            }),
        },
    ))
    client = RelationalDBAPIClient(RelationalClientConfig(
        profile=profile,
        module_name='sqlite3',
        version_query='SELECT sqlite_version()',
        connect_arguments=_route_arguments,
        metadata_reader=_resources,
        administration=admin,
    ), sqlite3)
    return client, admin


def request(route, operation, draft, target=None):
    return {
        'engine_id': 'sqlite',
        'resource_kind': 'table',
        'operation_id': operation,
        'target_resource': target,
        'draft': draft,
        '_provider_route': route,
    }


class RelationalVisualAdministrationTests(unittest.TestCase):

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.route = {
            'database': str(Path(self.temporary.name) / 'admin.sqlite'),
            'filesystem_root': self.temporary.name,
            'route_id': 'local-test',
        }
        self.client, self.admin = client_and_admin()
        self.target = {
            'resource_id': 'table:main:widgets',
            'resource_kind': 'table',
            'display_name': 'widgets',
            'display_path': ['main', 'widgets'],
        }

    def tearDown(self):
        self.client.close()
        self.temporary.cleanup()

    def apply(self, native_plan):
        return self.admin.apply(self.client, {
            'provider_payload': native_plan['provider_payload'],
        })

    def test_structured_ddl_and_grid_crud_execute_without_raw_commands(self):
        created = self.admin.plan(request(
            self.route, 'create', {
                'name': 'widgets',
                'definition': '',
                'options': {'columns': [
                    {
                        'name': 'id', 'type': 'INTEGER',
                        'nullable': False, 'primary_key': True,
                    },
                    {'name': 'name', 'type': 'TEXT', 'nullable': False},
                ]},
            },
        ))
        self.assertNotIn(str(self.route['database']), str(
            created['command_preview']
        ))
        self.assertTrue(self.apply(created)['commit_requested'])

        column = self.admin.plan({
            'resource_kind': 'column', 'operation_id': 'create',
            'target_resource': None,
            'draft': {
                'name': 'note', 'table': 'main.widgets',
                'data_type': 'TEXT', 'nullable': True,
                'default': '', 'primary_key': False,
            },
            '_provider_route': self.route,
        })
        self.apply(column)
        column_target = {
            'resource_id': 'column:main:widgets:note',
            'resource_kind': 'column', 'display_name': 'note',
            'display_path': ['main', 'widgets', 'note'],
        }
        renamed = self.admin.plan({
            'resource_kind': 'column', 'operation_id': 'rename',
            'target_resource': column_target,
            'draft': {'new_name': 'comment'},
            '_provider_route': self.route,
        })
        self.apply(renamed)
        column_target['display_name'] = 'comment'
        column_target['display_path'][-1] = 'comment'
        dropped = self.admin.plan({
            'resource_kind': 'column', 'operation_id': 'drop',
            'target_resource': column_target,
            'draft': {'cascade': False, 'confirmation': 'drop-column'},
            '_provider_route': self.route,
        })
        self.apply(dropped)

        virtual = self.admin.plan({
            'resource_kind': 'fts-table', 'operation_id': 'create',
            'target_resource': None,
            'draft': {
                'name': 'widget_search', 'module': 'fts5',
                'columns': ['title', 'content'],
            },
            '_provider_route': self.route,
        })
        self.apply(virtual)
        virtual_target = {
            'resource_id': 'fts-table:main:widget_search',
            'resource_kind': 'fts-table', 'display_name': 'widget_search',
            'display_path': ['main', 'widget_search'],
        }
        dropped_virtual = self.admin.plan({
            'resource_kind': 'fts-table', 'operation_id': 'drop',
            'target_resource': virtual_target,
            'draft': {'cascade': False, 'confirmation': 'drop-virtual'},
            '_provider_route': self.route,
        })
        self.apply(dropped_virtual)

        inserted = self.admin.plan(request(
            self.route, 'insert', {
                'values': {'id': 1, 'name': 'first'}, 'options': {},
            }, self.target,
        ))
        self.assertTrue(self.apply(inserted)['accepted'])

        page = self.admin.read_rows(self.client, {
            '_provider_route': self.route,
            'target_resource': self.target,
            'limit': 50,
        })
        self.assertTrue(page['editable'])
        self.assertEqual('first', page['rows'][0]['values']['name'])
        token = page['rows'][0]['identity_token']

        updated = self.admin.plan(request(
            self.route, 'update', {
                'selector': {'identity_token': token},
                'changes': {'name': 'second'},
                'concurrency_token': token,
            }, self.target,
        ))
        self.assertEqual(1, updated['provider_payload']['compiled'][
            'statements'
        ][0]['expected_rowcount'])
        self.assertEqual(
            ('second', 1, 'first'),
            updated['provider_payload']['compiled']['statements'][0][
                'parameters'
            ],
        )
        self.apply(updated)

        page = self.admin.read_rows(self.client, {
            '_provider_route': self.route,
            'target_resource': self.target,
        })
        self.assertEqual('second', page['rows'][0]['values']['name'])
        token = page['rows'][0]['identity_token']
        deleted = self.admin.plan(request(
            self.route, 'delete', {
                'selector': {'identity_token': token},
                'concurrency_token': token,
                'confirmation': 'provider-row-delete',
            }, self.target,
        ))
        self.apply(deleted)
        page = self.admin.read_rows(self.client, {
            '_provider_route': self.route,
            'target_resource': self.target,
        })
        self.assertEqual([], page['rows'])

    def test_complete_raw_ddl_and_unissued_row_selectors_are_rejected(self):
        validation = self.admin.validate(request(
            self.route, 'create', {
                'name': 'widgets',
                'definition': 'CREATE TABLE bypass(id INTEGER)',
                'options': {},
            },
        ))
        self.assertEqual(
            'complete_native_command_forbidden',
            validation['errors'][0]['code'],
        )
        with self.assertRaisesRegex(
            RelationalClientError, 'provider-issued row identity token'
        ):
            self.admin.plan(request(
                self.route, 'update', {
                    'selector': {'id': 1}, 'changes': {'name': 'unsafe'},
                }, self.target,
            ))

    def test_embedded_database_creation_stays_in_approved_root(self):
        route = {
            **self.route,
            'database_create_root': self.temporary.name,
        }
        created = self.admin.plan({
            'engine_id': 'sqlite',
            'resource_kind': 'database',
            'operation_id': 'create',
            'target_resource': None,
            'draft': {'name': 'created', 'options': {}},
            '_provider_route': route,
        })
        self.assertEqual(
            'embedded-create-database',
            created['command_preview']['driver_operation'],
        )
        self.assertNotIn(self.temporary.name, str(
            created['command_preview']
        ))
        self.apply(created)
        self.assertTrue(
            (Path(self.temporary.name) / 'created.sqlite').exists()
        )
        with self.assertRaisesRegex(
            RelationalClientError, 'safe unqualified file name'
        ):
            self.admin.plan({
                'engine_id': 'sqlite',
                'resource_kind': 'database',
                'operation_id': 'create',
                'target_resource': None,
                'draft': {'name': '../escape', 'options': {}},
                '_provider_route': route,
            })

    def test_user_password_is_redacted_from_validation_and_plan(self):
        context = SimpleNamespace(
            endpoint_id='endpoint-test', mode='legacy_native',
            runtime_verification_state='verified',
            verified_runtime_family='mysql',
            declared_runtime_family='mysql',
            effective_permissions=frozenset({
                'data_read', 'data_write', 'administer',
            }),
        )
        visual = ProviderVisualAdministration(
            context, Permissions(), 'mysql', '9.7.0',
            AdministrationClient(MYSQL_ADMINISTRATION),
        )
        secret = "never-render-this'password"
        value = {
            'resource_kind': 'user',
            'operation_id': 'create',
            'target_resource': None,
            'draft': {
                'name': 'operator', 'host': 'localhost',
                'password': secret, 'plugin': '', 'active': True,
                'administrator': False,
            },
            '_provider_route': {'route_id': 'trusted'},
        }
        validation = visual.validate(value)
        self.assertEqual('<redacted>', validation['draft']['password'])
        plan = visual.plan(value)
        self.assertNotIn(secret, str(plan))
        self.assertIn('<redacted>', str(plan))

    def test_firebird_user_and_role_plans_use_native_structured_syntax(self):
        route = {
            'database': 'server:/srv/firebird/current.fdb',
            'route_id': 'firebird-test',
        }
        user = FIREBIRD_ADMINISTRATION.plan({
            'resource_kind': 'user', 'operation_id': 'create',
            'target_resource': None,
            'draft': {
                'name': 'OPERATOR', 'password': 'secret-value',
                'host': '%', 'plugin': 'Srp256', 'active': True,
                'administrator': True,
            },
            '_provider_route': route,
        })
        preview = str(user['command_preview'])
        self.assertIn('GRANT ADMIN ROLE', preview)
        self.assertNotIn('secret-value', preview)
        role = FIREBIRD_ADMINISTRATION.plan({
            'resource_kind': 'role', 'operation_id': 'create',
            'target_resource': None,
            'draft': {
                'name': 'DATA_ADMIN',
                'system_privileges': ['CREATE_TABLE', 'DROP_ANY_TABLE'],
            },
            '_provider_route': route,
        })
        self.assertIn('SET SYSTEM PRIVILEGES TO', str(
            role['command_preview']
        ))
        trigger = FIREBIRD_ADMINISTRATION.plan({
            'resource_kind': 'trigger', 'operation_id': 'create',
            'target_resource': None,
            'draft': {
                'name': 'BI_WIDGETS', 'parent': '', 'table': 'WIDGETS',
                'timing': 'BEFORE', 'events': ['INSERT', 'UPDATE'],
                'active': True, 'position': 10,
                'body': 'BEGIN NEW.ID = NEXT VALUE FOR WIDGET_SEQ; END',
            },
            '_provider_route': route,
        })
        trigger_source = trigger['command_preview']['statements'][0]['source']
        self.assertIn(
            'FOR "WIDGETS" ACTIVE BEFORE INSERT OR UPDATE POSITION 10 AS',
            trigger_source,
        )
        procedure = FIREBIRD_ADMINISTRATION.plan({
            'resource_kind': 'procedure', 'operation_id': 'create',
            'target_resource': None,
            'draft': {
                'name': 'GET_WIDGET', 'parent': '',
                'parameters': [{'name': 'P_ID', 'type': 'BIGINT'}],
                'return_parameters': [
                    {'name': 'P_NAME', 'type': 'VARCHAR(100)'},
                ],
                'returns': '', 'body': 'BEGIN SUSPEND; END',
            },
            '_provider_route': route,
        })
        self.assertIn('RETURNS ("P_NAME" VARCHAR(100)) AS', str(
            procedure['command_preview']
        ))
        package = FIREBIRD_ADMINISTRATION.plan({
            'resource_kind': 'package', 'operation_id': 'create',
            'target_resource': None,
            'draft': {
                'name': 'WIDGET_API', 'parent': '',
                'header': 'BEGIN PROCEDURE P; END',
                'body': 'BEGIN PROCEDURE P AS BEGIN END END',
            },
            '_provider_route': route,
        })
        self.assertEqual(2, len(
            package['command_preview']['statements']
        ))

    def test_mysql_trigger_event_and_privilege_plans_are_dialect_owned(self):
        route = {'host': 'mysql.example', 'route_id': 'mysql-test'}
        trigger = MYSQL_ADMINISTRATION.plan({
            'resource_kind': 'trigger', 'operation_id': 'create',
            'target_resource': None,
            'draft': {
                'name': 'bi_widgets', 'parent': 'app',
                'table': 'app.widgets', 'timing': 'BEFORE',
                'events': ['INSERT'], 'active': True, 'position': 0,
                'body': 'SET NEW.created_at = CURRENT_TIMESTAMP',
            },
            '_provider_route': route,
        })
        self.assertIn('FOR EACH ROW', str(trigger['command_preview']))
        event = MYSQL_ADMINISTRATION.plan({
            'resource_kind': 'event', 'operation_id': 'create',
            'target_resource': None,
            'draft': {
                'name': 'purge_widgets', 'parent': 'app',
                'schedule': 'EVERY 1 DAY', 'preserve': True,
                'enabled': True,
                'body': 'DELETE FROM app.widgets WHERE expired = 1',
            },
            '_provider_route': route,
        })
        self.assertIn('ON SCHEDULE EVERY 1 DAY', str(
            event['command_preview']
        ))
        privilege = MYSQL_ADMINISTRATION.plan({
            'resource_kind': 'privilege', 'operation_id': 'grant',
            'target_resource': None,
            'draft': {
                'principal': 'operator@localhost',
                'object_type': 'TABLE', 'object_name': 'app.widgets',
                'privileges': ['SELECT', 'UPDATE'], 'grant_option': False,
            },
            '_provider_route': route,
        })
        privilege_source = privilege['command_preview']['statements'][0][
            'source'
        ]
        self.assertIn("TO 'operator'@'localhost'", privilege_source)

    def test_mariadb_package_specification_and_body_are_provider_built(self):
        route = {'host': 'mariadb.example', 'route_id': 'mariadb-test'}
        package = MARIADB_ADMINISTRATION.plan({
            'resource_kind': 'package', 'operation_id': 'create',
            'target_resource': None,
            'draft': {
                'name': 'widget_api', 'parent': 'app',
                'header': 'PROCEDURE get_widget(); END',
                'body': (
                    'PROCEDURE get_widget() BEGIN SELECT 1; END; END'
                ),
            },
            '_provider_route': route,
        })
        statements = package['command_preview']['statements']
        self.assertEqual(2, len(statements))
        self.assertIn(
            'CREATE PACKAGE `app`.`widget_api`', statements[0]['source']
        )
        self.assertIn(
            'CREATE PACKAGE BODY `app`.`widget_api`',
            statements[1]['source'],
        )


class DuckDBVisualAdministrationTests(unittest.TestCase):

    def test_exact_duckdb_driver_executes_structured_table_and_row_workflow(
        self,
    ):
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / 'exact.duckdb'
            route = {
                'database': str(database), 'filesystem_root': temporary,
                'route_id': 'duckdb-test',
            }
            target = {
                'resource_id': 'table:exact:main:widgets',
                'resource_kind': 'table', 'display_name': 'widgets',
                'display_path': ['exact', 'main', 'widgets'],
            }
            client = duckdb_client()
            try:
                created = DUCKDB_ADMINISTRATION.plan({
                    'resource_kind': 'table', 'operation_id': 'create',
                    'target_resource': None,
                    'draft': {
                        'name': 'widgets', 'parent': 'main',
                        'columns': [
                            {
                                'name': 'id', 'type': 'INTEGER',
                                'nullable': False, 'primary_key': True,
                            },
                            {'name': 'name', 'type': 'VARCHAR'},
                        ],
                        'constraints': [],
                    },
                    '_provider_route': route,
                })
                DUCKDB_ADMINISTRATION.apply(client, {
                    'provider_payload': created['provider_payload'],
                })
                inserted = DUCKDB_ADMINISTRATION.plan({
                    'resource_kind': 'table', 'operation_id': 'insert',
                    'target_resource': target,
                    'draft': {
                        'values': {'id': 7, 'name': 'duck'}, 'options': {},
                    },
                    '_provider_route': route,
                })
                DUCKDB_ADMINISTRATION.apply(client, {
                    'provider_payload': inserted['provider_payload'],
                })
                page = DUCKDB_ADMINISTRATION.read_rows(client, {
                    '_provider_route': route,
                    'target_resource': target,
                })
                self.assertTrue(page['editable'])
                self.assertEqual('duck', page['rows'][0]['values']['name'])
                enum_plan = DUCKDB_ADMINISTRATION.plan({
                    'resource_kind': 'type', 'operation_id': 'create',
                    'target_resource': None,
                    'draft': {
                        'name': 'mood', 'type_kind': 'ENUM',
                        'base_type': '', 'enum_values': ['ok', 'great'],
                        'fields': [],
                    },
                    '_provider_route': route,
                })
                DUCKDB_ADMINISTRATION.apply(client, {
                    'provider_payload': enum_plan['provider_payload'],
                })
                macro_plan = DUCKDB_ADMINISTRATION.plan({
                    'resource_kind': 'macro', 'operation_id': 'create',
                    'target_resource': None,
                    'draft': {
                        'name': 'double_value', 'parameters': ['value'],
                        'table_macro': False, 'expression': 'value * 2',
                    },
                    '_provider_route': route,
                })
                DUCKDB_ADMINISTRATION.apply(client, {
                    'provider_payload': macro_plan['provider_payload'],
                })
                connection = client.open_session({'route': route})
                try:
                    cursor = connection.execute(
                        'SELECT double_value(4), CAST(\'ok\' AS mood)'
                    )
                    self.assertEqual((8, 'ok'), cursor.fetchone())
                finally:
                    client.close()
            finally:
                client.close()


if __name__ == '__main__':
    unittest.main()
