##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Provider Object Explorer hierarchy contract tests."""

from __future__ import annotations

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

from pgadmin.cdeadmin.navigator import (  # noqa: E402
    ProviderNavigatorError,
    database_entries,
    decode_navigator_state,
    encode_navigator_state,
    is_loopback_server,
    resource_children,
    server_display_name,
)


def resource(resource_id, kind, name, path):
    return {
        'resource_id': resource_id,
        'resource_kind': kind,
        'display_name': name,
        'display_path': path,
        'authority_path': ['opaque', resource_id],
    }


class ProviderNavigatorTests(unittest.TestCase):

    def test_empty_groups_use_declared_parents_and_native_creation(self):
        catalog = {'objects': [
            {'resource_kind': 'table', 'navigator': {
                'parent_kinds': ['database']}, 'operations': [
                    {'operation_id': 'create', 'native_supported': True}]},
            {'resource_kind': 'column', 'navigator': {
                'parent_kinds': ['table']}, 'operations': [
                    {'operation_id': 'create', 'native_supported': True}]},
            {'resource_kind': 'fiction', 'navigator': {
                'parent_kinds': ['database']}, 'operations': [
                    {'operation_id': 'create', 'native_supported': False}]},
        ]}
        root = {'scope': 'database'}
        self.assertEqual(['Tables'], [
            n['label'] for n in resource_children([], root, catalog)])
        self.assertEqual([], resource_children([], {
            **root, 'scope': 'kind', 'resource_kind': 'table'}, catalog))
        table = resource('t', 'table', 'empty', ['empty'])
        nodes = resource_children([table], {
            'scope': 'kind', 'resource_kind': 'table'}, catalog)
        self.assertTrue(nodes[0]['has_children'])
        self.assertEqual(['Columns'], [n['label'] for n in resource_children(
            [table], {'scope': 'resource', 'resource_id': 't',
                      'parent_path': ['empty']}, catalog)])
        self.assertEqual([], resource_children([], {
            **root, 'object_scope': 'system'}, catalog))

    def test_empty_relations_prefer_declared_schema_over_database(self):
        catalog = {'objects': [
            {'resource_kind': 'schema', 'navigator': {
                'parent_kinds': ['database']}, 'operations': [
                    {'operation_id': 'create'}]},
            {'resource_kind': 'table', 'navigator': {
                'parent_kinds': ['database', 'schema']}, 'operations': [
                    {'operation_id': 'create'}]},
        ]}
        self.assertEqual(['Schemas'], [n['label'] for n in resource_children(
            [], {'scope': 'database'}, catalog)])

    def test_relation_children_do_not_belong_to_same_named_types(self):
        from pgadmin.cdeadmin.providers.catalog_presentation import (
            annotate_relation_ownership,
        )
        for relation_kind in ('table', 'view', 'materialized-view'):
            with self.subTest(kind=relation_kind):
                relation = resource('relation', relation_kind, 'x', ['s', 'x'])
                row_type = resource('row-type', 'type', 'x', ['s', 'x'])
                children = [resource(k, k, k, ['s', 'x', k]) for k in
                            ('column', 'index', 'constraint', 'trigger')]
                values = annotate_relation_ownership(
                    [relation, row_type, *children])
                self.assertEqual([], resource_children(values, {
                    'scope': 'resource', 'resource_id': 'row-type',
                    'parent_path': ['x']}))
                self.assertEqual(4, len(resource_children(values, {
                    'scope': 'resource', 'resource_id': 'relation',
                    'parent_path': ['x']})))

    def test_storage_is_relation_child_without_changing_authority(self):
        from pgadmin.cdeadmin.providers.catalog_presentation import (
            annotate_relation_ownership,
        )
        table = resource('table:x', 'table', 'x', ['db', 'x'])
        storage = resource('storage:x', 'table-storage', 'x', ['db', 'x'])
        column = resource('column:id', 'column', 'id', ['db', 'x', 'id'])
        authority = list(storage['authority_path'])
        values = annotate_relation_ownership([table, storage, column])
        self.assertEqual(authority, storage['authority_path'])
        self.assertEqual('storage:x', storage['resource_id'])
        self.assertEqual(['db', 'x', 'Storage'], storage['display_path'])
        self.assertEqual('table:x', storage['native'][
            'navigator_parent_resource_id'])
        self.assertEqual([], resource_children(values, {
            'scope': 'resource', 'resource_id': 'storage:x',
            'parent_path': ['x', 'Storage']}))

    def test_ambiguous_relation_ownership_is_not_guessed(self):
        from pgadmin.cdeadmin.providers.catalog_presentation import (
            annotate_relation_ownership,
        )
        values = [resource('a', 'table', 'x', ['x']),
                  resource('b', 'view', 'x', ['x']),
                  resource('c', 'column', 'id', ['x', 'id'])]
        annotate_relation_ownership(values)
        self.assertNotIn('native', values[-1])

    def test_relation_grants_keep_owner_across_hidden_grantee_path(self):
        from pgadmin.cdeadmin.providers.catalog_presentation import (
            annotate_relation_ownership,
        )
        values = [resource('t', 'table', 'x', ['s', 'x']),
                  resource('type', 'type', 'x', ['s', 'x']),
                  resource('grant', 'privilege', 'read',
                           ['s', 'x', 'reader', 'read'])]
        values[-1]['native'] = {'navigator_relation_path': ['s', 'x']}
        annotate_relation_ownership(values)
        state = {'scope': 'resource', 'parent_path': ['x'],
                 'resource_id': 'type'}
        self.assertEqual([], resource_children(values, state))
        state['resource_id'] = 't'
        self.assertEqual(['Roles and grants'], [
            n['label'] for n in resource_children(values, state)])

    def test_wrapped_metadata_keeps_same_named_objects_children_distinct(self):
        table = resource('table:x', 'table', 'X', ['X'])
        sequence = resource('sequence:x', 'sequence', 'X', ['X'])
        column = resource('column:x:id', 'column', 'ID', ['X', 'ID'])
        for item in (table, sequence, column):
            item['extensions'] = {'firebird': {'native': {
                'system_object': True,
            }}}
        column['extensions']['firebird']['native'][
            'navigator_parent_resource_id'] = 'table:x'
        values = [table, sequence, column]
        self.assertEqual(['sys'], [n['label'] for n in resource_children(
            values, {'scope': 'database'})])
        state = {'scope': 'resource', 'object_scope': 'system',
                 'parent_path': ['X'], 'resource_id': 'sequence:x'}
        self.assertEqual([], resource_children(values, state))
        state['resource_id'] = 'table:x'
        self.assertEqual(['Columns'], [
            n['label'] for n in resource_children(values, state)])

    def test_unrepresented_catalog_prefix_moves_complete_subtree(self):
        values = [
            resource('t', 'table', 'orders', ['main', 'orders']),
            resource('c', 'column', 'id', ['main', 'orders', 'id']),
        ]
        nodes = resource_children(values, {'scope': 'kind',
                                           'resource_kind': 'table'})
        self.assertTrue(nodes[0]['has_children'])
        self.assertEqual(['orders'], nodes[0]['display_path'])
        columns = resource_children(values, {
            'scope': 'kind', 'resource_kind': 'column',
            'parent_path': ['orders']})
        self.assertEqual(['id'], [n['label'] for n in columns])

    def test_system_folder_is_presentation_only_and_preserves_identity(self):
        system = resource('rdb', 'table', 'RDB$DATABASE', ['RDB$DATABASE'])
        system['native'] = {'system_object': True}
        column = resource('col', 'column', 'RDB$DESCRIPTION',
                          ['RDB$DATABASE', 'RDB$DESCRIPTION'])
        user = resource('user', 'table', 'CUSTOMERS', ['CUSTOMERS'])
        owned = resource(
            'ri', 'trigger', 'CHECK_FK', ['CUSTOMERS', 'CHECK_FK'])
        owned['native'] = {'system_object': True}
        values = [system, column, user, owned]
        root = {'scope': 'database'}
        self.assertEqual(['sys', 'Tables'], [
            item['label'] for item in resource_children(values, root)])
        sys_state = {**root, 'object_scope': 'system'}
        self.assertEqual(['Tables'], [
            item['label'] for item in resource_children(values, sys_state)])
        tables = {**sys_state, 'scope': 'kind', 'resource_kind': 'table'}
        nodes = resource_children(values, tables)
        self.assertEqual(['RDB$DATABASE'], [item['label'] for item in nodes])
        self.assertEqual(['opaque', 'rdb'],
                         nodes[0]['resource']['authority_path'])
        columns = {**sys_state, 'scope': 'kind', 'resource_kind': 'column',
                   'parent_path': ['RDB$DATABASE']}
        self.assertEqual(['RDB$DESCRIPTION'], [
            item['label'] for item in resource_children(values, columns)])
        self.assertEqual(sys_state, decode_navigator_state(
            encode_navigator_state(sys_state)))
        self.assertEqual(['RDB$DATABASE'], system['display_path'])
        children = resource_children(values, {
            'scope': 'kind', 'resource_kind': 'trigger',
            'parent_path': ['CUSTOMERS']})
        self.assertEqual(['CHECK_FK'], [item['label'] for item in children])

    def test_server_labels_represent_hosts_not_connection_profile_names(self):
        for host in ('127.0.0.1', '::1', '[::1]', 'localhost'):
            self.assertEqual(
                'localhost', server_display_name(host, 'Demo profile')
            )
            self.assertTrue(is_loopback_server(host))
        self.assertEqual(
            'db01.example.net',
            server_display_name('db01.example.net', 'Production'),
        )
        self.assertEqual(
            'Embedded demo', server_display_name('', 'Embedded demo')
        )
        self.assertFalse(is_loopback_server('db01.example.net'))

    def test_owned_children_group_without_reparenting_authority(self):
        table = resource('table', 'table', 'CUSTOMERS', ['CUSTOMERS'])
        values = [table] + [
            resource(kind, kind, name, ['CUSTOMERS', name])
            for kind, name in [('column', 'ID'), ('trigger', 'BI_CUSTOMERS'),
                               ('index', 'PK_CUSTOMERS'),
                               ('constraint', 'CUSTOMERS_PK')]
        ]
        state = {'scope': 'resource', 'resource_id': 'table',
                 'parent_path': ['CUSTOMERS']}
        self.assertEqual(
            ['Columns', 'Constraints', 'Indexes', 'Triggers'],
            [n['label'] for n in resource_children(values, state)])
        for kind in ('column', 'trigger', 'index', 'constraint'):
            nodes = resource_children(values, {
                **state, 'scope': 'kind', 'resource_kind': kind})
            self.assertEqual(1, len(nodes))
            self.assertEqual(['opaque', kind],
                             nodes[0]['resource']['authority_path'])

    def test_legacy_and_retained_databases_become_server_children(self):
        retained = database_entries({'targets': [{
            'target_id': 'target-one', 'database': '/srv/one.fdb',
            'display_name': 'one.fdb', 'active': False,
        }], 'legacy_route_database': '/ignored/legacy.fdb'})
        self.assertEqual(
            ['target-one'], [item['target_id'] for item in retained]
        )
        self.assertFalse(retained[0]['active'])

        legacy = database_entries({
            'targets': [],
            'legacy_route_database': '/srv/firebird/demo.fdb',
        })
        self.assertEqual('legacy-route-database', legacy[0]['target_id'])
        self.assertEqual('demo.fdb', legacy[0]['display_name'])
        self.assertTrue(legacy[0]['active'])

        redis = database_entries({
            'targets': [], 'legacy_route_database': 0,
        })
        self.assertEqual('0', redis[0]['database'])
        self.assertEqual('0', redis[0]['display_name'])

    def test_navigator_state_round_trips_and_rejects_unknown_fields(self):
        state = {
            'scope': 'kind', 'target_id': 'database-one',
            'database': 'example', 'display_name': 'example',
            'parent_path': ['orders'], 'resource_kind': 'column',
        }
        self.assertEqual(
            state, decode_navigator_state(encode_navigator_state(state))
        )
        with self.assertRaisesRegex(ProviderNavigatorError, 'invalid'):
            decode_navigator_state(encode_navigator_state({
                'scope': 'database', 'parent_path': [], 'forged': True,
            }))

    def test_firebird_database_groups_objects_and_table_children(self):
        resources = [
            resource('server:Firebird', 'server', 'Firebird', ['Firebird']),
            resource('database:current', 'database', 'current', ['current']),
            resource('table:CUSTOMER', 'table', 'CUSTOMER', ['CUSTOMER']),
            resource(
                'column:CUSTOMER:ID', 'column', 'ID', ['CUSTOMER', 'ID']
            ),
            resource('sequence:CUSTOMER_ID', 'sequence', 'CUSTOMER_ID', [
                'CUSTOMER_ID'
            ]),
        ]
        base = {
            'scope': 'database', 'target_id': 'legacy-route-database',
            'database': '/srv/demo.fdb', 'display_name': 'demo.fdb',
            'parent_path': [],
        }
        groups = resource_children(resources, base)
        self.assertEqual(
            ['Sequences', 'Tables'], [item['label'] for item in groups]
        )
        tables = resource_children(resources, {
            **base, 'scope': 'kind', 'resource_kind': 'table',
        })
        self.assertEqual(['CUSTOMER'], [item['label'] for item in tables])
        self.assertTrue(tables[0]['has_children'])
        children = resource_children(resources, {
            **base, 'scope': 'resource',
            'parent_path': ['CUSTOMER'],
            'resource_id': 'table:CUSTOMER',
        })
        self.assertEqual(['Columns'], [item['label'] for item in children])

    def test_provider_object_can_own_a_different_resource_kind(self):
        resources = [
            resource(
                'binary-log:mariadb-bin.000001', 'binary-log',
                'mariadb-bin.000001', ['mariadb-bin.000001'],
            ),
            resource(
                'binary-log-event:mariadb-bin.000001:4',
                'binary-log-event', '4 / Format_desc',
                ['mariadb-bin.000001', '4 / Format_desc'],
            ),
        ]
        base = {
            'scope': 'database', 'target_id': 'database-one',
            'database': 'app', 'display_name': 'app', 'parent_path': [],
        }
        groups = resource_children(resources, base)
        self.assertEqual(['Binary logs'], [item['label'] for item in groups])
        logs = resource_children(resources, {
            **base, 'scope': 'kind', 'resource_kind': 'binary-log',
        })
        self.assertTrue(logs[0]['has_children'])
        children = resource_children(resources, {
            **base, 'scope': 'resource',
            'parent_path': ['mariadb-bin.000001'],
            'resource_id': 'binary-log:mariadb-bin.000001',
        })
        self.assertEqual(
            ['Binary log events'],
            [item['label'] for item in children],
        )

    def test_matching_firebird_database_does_not_hide_database_local_paths(
            self):
        resources = [
            resource(
                'database:demo', 'database', 'demo.fdb', ['demo.fdb']
            ),
            resource('table:CUSTOMER', 'table', 'CUSTOMER', ['CUSTOMER']),
            resource(
                'column:CUSTOMER:ID', 'column', 'ID', ['CUSTOMER', 'ID']
            ),
        ]
        base = {
            'scope': 'database', 'target_id': 'demo',
            'database': '/srv/demo.fdb', 'display_name': 'demo.fdb',
            'parent_path': [],
        }

        self.assertEqual(
            ['Tables'],
            [item['label'] for item in resource_children(resources, base)],
        )

    def test_database_prefix_prevents_cross_database_objects(self):
        resources = [
            resource('database:a', 'database', 'a', ['a']),
            resource('database:b', 'database', 'b', ['b']),
            resource('table:a:t', 'table', 't', ['a', 't']),
            resource('table:b:t', 'table', 't', ['b', 't']),
        ]
        base = {
            'scope': 'database', 'target_id': 'a', 'database': 'a',
            'display_name': 'a', 'parent_path': [],
        }
        self.assertEqual(
            ['Tables'],
            [item['label'] for item in resource_children(resources, base)],
        )
        tables = resource_children(resources, {
            **base, 'scope': 'kind', 'resource_kind': 'table',
        })
        self.assertEqual(
            ['table:a:t'], [item['resource_id'] for item in tables]
        )

    def test_embedded_database_native_path_retains_table_children(self):
        database = resource(
            'database:main', 'database', 'main', ['main']
        )
        database['extensions'] = {
            'sqlite': {'native': {'path': '/srv/data/example.sqlite'}}
        }
        resources = [
            database,
            resource('table:main:t', 'table', 't', ['main', 't']),
            resource(
                'column:main:t:id', 'column', 'id', ['main', 't', 'id']
            ),
            resource(
                'constraint:main:t:pk', 'constraint', 'pk_t',
                ['main', 't', 'pk_t'],
            ),
        ]
        base = {
            'scope': 'database', 'target_id': 'database-one',
            'database': '/srv/data/example.sqlite',
            'display_name': 'example.sqlite', 'parent_path': [],
        }
        tables = resource_children(resources, {
            **base, 'scope': 'kind', 'resource_kind': 'table',
        })
        self.assertEqual(['t'], [item['label'] for item in tables])
        self.assertTrue(tables[0]['has_children'])
        children = resource_children(resources, {
            **base, 'scope': 'resource', 'parent_path': ['t'],
            'resource_id': 'table:main:t',
        })
        self.assertEqual(
            ['Columns', 'Constraints'],
            [item['label'] for item in children],
        )

    def test_embedded_database_retains_declared_attached_database_branch(self):
        database = resource(
            'database:main', 'database', 'main', ['main']
        )
        database['native'] = {'path': '/srv/data/example.sqlite'}
        resources = [
            database,
            resource(
                'database:archive', 'attached-database', 'archive',
                ['archive'],
            ),
            resource(
                'table:archive:item', 'table', 'item', ['archive', 'item']
            ),
        ]
        base = {
            'scope': 'database', 'target_id': 'database-one',
            'database': '/srv/data/example.sqlite',
            'display_name': 'example.sqlite', 'parent_path': [],
        }
        groups = resource_children(resources, base)
        self.assertEqual(
            ['Attached databases'], [item['label'] for item in groups]
        )
        attached = resource_children(resources, {
            **base, 'scope': 'kind',
            'resource_kind': 'attached-database',
        })
        self.assertEqual(['archive'], [item['label'] for item in attached])
        self.assertTrue(attached[0]['has_children'])

    def test_embedded_database_retains_provider_global_resources(self):
        database = resource(
            'database:main', 'database', 'main', ['main']
        )
        database['native'] = {'path': '/srv/data/example.sqlite'}
        resources = [
            database,
            resource(
                'table:main:item', 'table', 'item', ['main', 'item']
            ),
            resource(
                'pragma:page_size', 'pragma', 'page_size',
                ['Pragmas', 'page_size'],
            ),
            resource(
                'extension:fts5', 'extension', 'fts5',
                ['Extensions', 'fts5'],
            ),
        ]
        base = {
            'scope': 'database', 'target_id': 'database-one',
            'database': '/srv/data/example.sqlite',
            'display_name': 'example.sqlite', 'parent_path': [],
        }
        self.assertEqual(
            ['Extensions', 'Pragmas', 'Tables'],
            [item['label'] for item in resource_children(resources, base)],
        )

    def test_server_scope_collapses_unmaterialized_display_folders(self):
        resources = [
            resource('server:Firebird', 'server', 'Firebird', ['Firebird']),
            resource(
                'service:isql', 'service-operation', 'isql',
                ['Firebird', 'Services', 'isql'],
            ),
        ]
        base = {'scope': 'server', 'parent_path': []}
        self.assertEqual(
            ['Service operations'],
            [item['label'] for item in resource_children(resources, base)],
        )
        operations = resource_children(resources, {
            **base, 'scope': 'kind',
            'resource_kind': 'service-operation',
        })
        self.assertEqual(['isql'], [item['label'] for item in operations])


if __name__ == '__main__':
    unittest.main()
