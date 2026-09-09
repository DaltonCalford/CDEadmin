##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Provider-owned Object Explorer context menu tests."""

import sys
import unittest
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

from pgadmin.cdeadmin.context_menu import (  # noqa: E402
    CONTEXT_ACTION_SCHEMA,
    connector_context_actions,
    database_target_context_actions,
    endpoint_context_actions,
    resource_context_actions,
)


def profile(engine_id, *, route_kind='network', multiple=False):
    database_forms = {
        operation_id: {
            'form_id': (
                f'cdeadmin.{engine_id}-native.database.{operation_id}.v1'
            ),
            'supported': True,
            'disabled_reason': None,
        }
        for operation_id in (
            'define', 'connect', 'create', 'edit', 'alter', 'drop', 'remove'
        )
    }
    return {
        'profile_id': f'{engine_id}-native',
        'display_name': engine_id.title(),
        'engine_id': engine_id,
        'engine_display_name': engine_id.title(),
        'interface_display_name': engine_id.title(),
        'route_kind': route_kind,
        'database_targeting': {
            'multiple': multiple,
            'create_and_activate': multiple,
        },
        'form_contract': {
            'server': {'forms': {
                operation_id: {'form_id': (
                    f'cdeadmin.{engine_id}-native.server.'
                    f'{operation_id}.v1'
                )}
                for operation_id in ('define', 'edit', 'remove')
            }},
            'database': {
                'noun': 'database', 'forms': database_forms,
            },
        },
    }


class ContextMenuTests(unittest.TestCase):

    def test_firebird_connector_separates_endpoint_create_and_register(self):
        actions = connector_context_actions(
            'firebird', [profile('firebird', multiple=True)],
            status={'available': True, 'listening_ports': [3050]},
        )
        by_id = {item['command_id']: item for item in actions}
        self.assertIn('connector.firebird.create_database', by_id)
        self.assertIn('connector.firebird.register_database', by_id)
        self.assertEqual(
            by_id['connector.firebird.create_database']['arguments'][
                'registration_intent'
            ],
            'create_database',
        )
        self.assertEqual(
            by_id['connector.firebird.register_database']['arguments'][
                'registration_intent'
            ],
            'register_existing',
        )

    def test_unavailable_local_network_endpoint_is_disabled(self):
        actions = connector_context_actions(
            'mysql', [profile('mysql')],
            status={'available': False, 'ports': [3306]},
            localhost_placeholder=True,
        )
        registration = next(item for item in actions if item[
            'handler'
        ] == 'register_endpoint')
        self.assertFalse(registration['enabled'])
        self.assertTrue(registration['disabled_reason'])

    def test_provider_endpoint_actions_never_contain_postgresql_commands(self):
        actions = endpoint_context_actions(
            profile('firebird', multiple=True), 'verified',
            is_password_saved=True,
        )
        labels = ' '.join(item['label'].lower() for item in actions)
        self.assertNotIn('wal', labels)
        self.assertNotIn('psql', labels)
        self.assertNotIn('reload configuration', labels)
        self.assertTrue(all(
            item['schema'] == CONTEXT_ACTION_SCHEMA for item in actions
        ))
        self.assertFalse(any(
            item['command_id'].startswith('endpoint.firebird.workspace.')
            for item in actions
        ))

    def test_unverified_endpoint_keeps_verification_available(self):
        actions = endpoint_context_actions(
            profile('mysql', multiple=True), 'unverified'
        )
        verify = next(item for item in actions if item[
            'handler'
        ] == 'verify_endpoint')
        workspace = [item for item in actions if item[
            'handler'
        ] == 'open_workspace']
        self.assertTrue(verify['enabled'])
        self.assertTrue(workspace)
        properties = next(item for item in workspace if item[
            'command_id'
        ] == 'endpoint.mysql.properties')
        remove = next(item for item in workspace if item[
            'command_id'
        ] == 'endpoint.mysql.forget')
        self.assertTrue(properties['enabled'])
        self.assertTrue(remove['enabled'])
        self.assertTrue(all(
            not item['enabled'] for item in workspace
            if item not in (properties, remove)
        ))

    def test_endpoint_remove_uses_exact_provider_form(self):
        actions = endpoint_context_actions(
            profile('sqlite', route_kind='embedded_file'), 'verified'
        )
        remove = next(item for item in actions if item[
            'command_id'
        ] == 'endpoint.sqlite.forget')
        self.assertEqual('open_workspace', remove['handler'])
        self.assertEqual('connections', remove['arguments']['tab'])
        self.assertEqual('remove', remove['arguments']['server_mode'])
        self.assertEqual(
            'cdeadmin.sqlite-native.server.remove.v1',
            remove['arguments']['form_id'],
        )
        self.assertFalse(remove['requires_confirmation'])
        self.assertFalse(any(
            item['command_id'] == 'endpoint.sqlite.routes'
            for item in actions
        ))

    def test_mariadb_endpoint_exposes_exact_server_upgrade_check(self):
        actions = endpoint_context_actions(
            profile('mariadb', multiple=True), 'verified'
        )
        action = next(
            item for item in actions if item['command_id'] ==
            'endpoint.mariadb.check_upgrade_required'
        )
        self.assertTrue(action['enabled'])
        self.assertEqual('maintenance', action['menu_group'])
        self.assertEqual('administration', action['arguments']['tab'])
        self.assertEqual('server', action['arguments']['resource_kind'])
        self.assertEqual(
            'check_upgrade_required', action['arguments']['operation_id']
        )

    def test_resource_operations_come_from_provider_catalog(self):
        catalog = {'objects': [{
            'resource_kind': 'table',
            'editor': {'sections': ['properties', 'data']},
            'operations': [{
                'operation_id': 'inspect', 'title': 'Inspect table',
                'mutation_class': 'read', 'execution_available': True,
                'native_supported': True,
            }, {
                'operation_id': 'alter', 'title': 'Alter table',
                'mutation_class': 'admin', 'execution_available': True,
                'native_supported': True,
            }, {
                'operation_id': 'drop', 'title': 'Drop table',
                'mutation_class': 'destructive',
                'execution_available': False,
                'native_supported': True,
                'blockers': ['provider denied drop'],
                'confirmation_required': True,
            }, {
                'operation_id': 'vacuum', 'title': 'Vacuum table',
                'mutation_class': 'admin',
                'execution_available': False,
                'native_supported': False,
                'blockers': ['provider_operation_unavailable'],
            }],
        }]}
        actions = resource_context_actions(
            profile('mysql'),
            {'resource_id': 'table:app.customer',
             'resource_kind': 'table'},
            catalog,
            database_target_id='database-one',
        )
        by_id = {item['command_id']: item for item in actions}
        inspect = by_id['resource.mysql.table.inspect']
        self.assertEqual('administration', inspect['arguments']['tab'])
        self.assertEqual('inspect', inspect['arguments']['operation_id'])
        self.assertEqual('table', inspect['arguments']['resource_kind'])
        self.assertEqual(
            'table:app.customer', inspect['arguments']['resource_id']
        )
        self.assertEqual(
            'database-one', inspect['arguments']['database_target_id']
        )
        self.assertTrue(by_id['resource.mysql.table.alter']['enabled'])
        self.assertFalse(by_id['resource.mysql.table.drop']['enabled'])
        self.assertEqual(
            by_id['resource.mysql.table.drop']['disabled_reason'],
            'provider denied drop',
        )
        self.assertFalse(by_id['resource.mysql.table.drop'][
            'macro_callable'
        ])
        self.assertNotIn('resource.mysql.table.vacuum', by_id)

    def test_database_resource_opens_provider_query_workspace(self):
        catalog = {'objects': [{
            'resource_kind': 'table',
            'editor': {'sections': ['properties', 'data']},
            'operations': [],
        }]}
        actions = resource_context_actions(
            profile('firebird'),
            {
                'resource_id': 'table:WORK_ORDERS',
                'resource_kind': 'table',
            },
            catalog,
            database_target_id='firebird-database-one',
        )
        query = next(item for item in actions if item['command_id'] ==
                     'resource.firebird.table.query')
        self.assertEqual('Open Firebird SQL editor...', query['label'])
        self.assertEqual('open_workspace', query['handler'])
        self.assertEqual('studio', query['arguments']['tab'])
        self.assertEqual(
            'firebird-database-one',
            query['arguments']['database_target_id'],
        )
        self.assertEqual(
            'table:WORK_ORDERS', query['arguments']['resource_id']
        )

    def test_server_scoped_resource_does_not_invent_query_workspace(self):
        catalog = {'objects': [{
            'resource_kind': 'metric',
            'editor': {'sections': ['properties', 'statistics']},
            'operations': [],
        }]}
        actions = resource_context_actions(
            profile('firebird'),
            {'resource_id': 'metric:connections',
             'resource_kind': 'metric'},
            catalog,
        )
        self.assertFalse(any(
            item['command_id'].endswith('.query') for item in actions
        ))

    def test_database_metric_can_open_query_without_object_editor(self):
        actions = resource_context_actions(
            profile('firebird'),
            {
                'resource_id': 'metric:monitoring:MON$DATABASE',
                'resource_kind': 'metric',
            },
            {'objects': []},
            database_target_id='database-one',
        )
        self.assertEqual(1, len(actions))
        self.assertEqual(
            'resource.firebird.metric.query', actions[0]['command_id']
        )
        self.assertEqual('studio', actions[0]['arguments']['tab'])

    def test_resource_menu_does_not_manufacture_common_actions(self):
        catalog = {'objects': [{
            'resource_kind': 'index',
            'editor': {'sections': ['properties']},
            'operations': [{
                'operation_id': 'create', 'title': 'Create index',
                'mutation_class': 'admin', 'execution_available': False,
                'native_supported': False, 'target_required': False,
            }],
        }]}
        actions = resource_context_actions(
            profile('xtdb'),
            {'resource_id': 'index:example', 'resource_kind': 'index'},
            catalog,
        )
        self.assertEqual([], actions)

    def test_database_target_actions_open_exact_provider_forms(self):
        actions = database_target_context_actions(
            profile('firebird'), {
                'target_id': 'database-one', 'display_name': 'Example',
            }
        )
        by_id = {item['command_id']: item for item in actions}
        properties = by_id['database.firebird.properties']
        self.assertEqual('properties', properties['arguments']['tab'])
        self.assertEqual('open_workspace', properties['handler'])
        edit = by_id['database.firebird.edit']
        self.assertEqual('edit', edit['arguments']['database_mode'])
        self.assertEqual(
            'cdeadmin.firebird-native.database.edit.v1',
            edit['arguments']['form_id'],
        )
        self.assertEqual('open_workspace', edit['handler'])
        self.assertNotIn('database.firebird.administration', by_id)
        self.assertNotIn('database.firebird.operations', by_id)
        self.assertNotIn('database.firebird.movement', by_id)
        for resource_kind in ('user', 'role', 'privilege'):
            security = by_id[
                f'database.firebird.security.{resource_kind}'
            ]
            self.assertEqual(
                'administration', security['arguments']['tab']
            )
            self.assertEqual(
                resource_kind, security['arguments']['resource_kind']
            )
        expected_services = {
            'backup_logical', 'restore_logical', 'backup_physical',
            'restore_physical', 'database_statistics',
            'validate_database', 'repair_database', 'sweep_database',
            'shutdown_database', 'bring_online',
            'set_page_cache_size', 'set_sweep_interval',
            'set_space_reservation', 'set_write_mode', 'set_access_mode',
            'set_sql_dialect', 'activate_shadow', 'remove_linger',
            'fixup_database', 'set_replica_mode', 'upgrade_database',
        }
        self.assertTrue(all(
            f'database.firebird.{operation}' in by_id
            for operation in expected_services
        ))
        for operation in expected_services:
            action = by_id[f'database.firebird.{operation}']
            self.assertEqual('administration', action['arguments']['tab'])
            self.assertEqual('database', action['arguments']['resource_kind'])
            self.assertEqual(operation, action['arguments']['operation_id'])

    def test_mariadb_database_actions_expose_native_maintenance_forms(self):
        actions = database_target_context_actions(
            profile('mariadb'), {
                'target_id': 'mariadb-one',
                'display_name': 'inventory',
            }
        )
        by_id = {item['command_id']: item for item in actions}
        expected = {
            'analyze_tables', 'check_objects', 'optimize_tables',
            'repair_objects', 'checksum_tables', 'backup_logical',
            'restore_logical',
        }
        self.assertTrue(all(
            f'database.mariadb.{operation}' in by_id
            for operation in expected
        ))
        for operation in expected:
            action = by_id[f'database.mariadb.{operation}']
            self.assertEqual('administration', action['arguments']['tab'])
            self.assertEqual('database', action['arguments']['resource_kind'])
            self.assertEqual(operation, action['arguments']['operation_id'])
            if operation == 'restore_logical':
                self.assertEqual('destructive', action['mutation_class'])
                self.assertTrue(action['requires_confirmation'])
                self.assertFalse(action['macro_callable'])
            else:
                self.assertTrue(action['macro_callable'])

    def test_sqlite_database_actions_name_native_file_operations(self):
        sqlite = profile('sqlite', route_kind='embedded_file', multiple=True)
        sqlite['form_contract']['database']['noun'] = 'database file'
        actions = database_target_context_actions(sqlite, {
            'target_id': 'sqlite-one', 'display_name': 'example.sqlite',
        })
        by_id = {item['command_id']: item for item in actions}
        self.assertEqual(
            'Configure SQLite database file...',
            by_id['database.sqlite.alter']['label'],
        )
        self.assertEqual(
            'Delete SQLite database file...',
            by_id['database.sqlite.drop']['label'],
        )
        self.assertEqual(
            'destructive',
            by_id['database.sqlite.drop']['mutation_class'],
        )
        expected_tasks = {
            'backup': ('Online backup...', 'backup', 'admin'),
            'restore': ('Restore from backup...', 'restore', 'destructive'),
            'integrity_check': (
                'Integrity check...', 'diagnostics', 'read'
            ),
            'quick_check': (
                'Quick integrity check...', 'diagnostics', 'read'
            ),
            'foreign_key_check': (
                'Foreign-key check...', 'diagnostics', 'read'
            ),
            'vacuum': ('Vacuum database...', 'maintenance', 'admin'),
            'incremental_vacuum': (
                'Incremental vacuum...', 'maintenance', 'admin'
            ),
            'optimize': (
                'Optimize query planner...', 'maintenance', 'admin'
            ),
            'analyze': (
                'Analyze statistics...', 'maintenance', 'admin'
            ),
            'reindex': ('Rebuild indexes...', 'maintenance', 'admin'),
            'wal_checkpoint': (
                'WAL checkpoint...', 'maintenance', 'admin'
            ),
        }
        for operation, (label, group, mutation) in expected_tasks.items():
            action = by_id[f'database.sqlite.{operation}']
            self.assertEqual(label, action['label'])
            self.assertEqual(group, action['menu_group'])
            self.assertEqual(mutation, action['mutation_class'])
            self.assertEqual('administration', action['arguments']['tab'])
            self.assertEqual('database', action['arguments']['resource_kind'])
            self.assertEqual(operation, action['arguments']['operation_id'])
            self.assertEqual(
                operation == 'restore', action['requires_confirmation']
            )

    def test_duckdb_database_actions_expose_native_file_operations(self):
        duckdb = profile('duckdb', route_kind='embedded_file', multiple=True)
        actions = database_target_context_actions(duckdb, {
            'target_id': 'duckdb-one', 'display_name': 'example.duckdb',
        })
        by_id = {item['command_id']: item for item in actions}
        expected = {
            'checkpoint': ('Checkpoint database...', 'maintenance'),
            'force_checkpoint': ('Force checkpoint...', 'maintenance'),
            'vacuum': ('Vacuum database...', 'maintenance'),
            'analyze': ('Analyze statistics...', 'maintenance'),
            'export_database': ('Export database...', 'backup'),
            'import_database': ('Import database...', 'restore'),
        }
        for operation, (label, group) in expected.items():
            action = by_id[f'database.duckdb.{operation}']
            self.assertEqual(label, action['label'])
            self.assertEqual(group, action['menu_group'])
            self.assertEqual(operation, action['arguments']['operation_id'])
            self.assertEqual('database', action['arguments']['resource_kind'])


if __name__ == '__main__':
    unittest.main()
