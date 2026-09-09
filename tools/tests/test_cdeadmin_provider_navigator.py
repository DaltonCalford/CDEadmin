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
