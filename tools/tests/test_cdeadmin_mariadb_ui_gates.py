##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""MariaDB native maintenance and tool browser-gate contracts."""

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

from pgadmin.cdeadmin.providers.mysql_family.provider import (  # noqa: E402
    MARIADB_ADMINISTRATION,
    MARIADB_PROFILE,
)
from pgadmin.cdeadmin.visual_admin.catalog import (  # noqa: E402
    catalog_for_engine,
)
from tools.cdeadmin_mariadb_backup_restore_ui_gate import (  # noqa: E402
    CASES as TOOL_CASES,
    _operations as tool_operations,
)
from tools.cdeadmin_mariadb_maintenance_ui_gate import (  # noqa: E402
    CASES as MAINTENANCE_CASES,
    _operations as maintenance_operations,
)
from tools.cdeadmin_mariadb_ui_orchestrator import (  # noqa: E402
    _browser_gate_complete,
)
from tools.cdeadmin_provider_object_form_gate import (  # noqa: E402
    _preview_values,
)
from tools.cdeadmin_sqlite_grid_ui_gate import _engine_identity  # noqa: E402


class MariaDBUIGateTests(unittest.TestCase):

    def test_data_studio_uses_mariadb_owned_source_and_plan_templates(self):
        self.assertIn('FROM qualification', MARIADB_PROFILE.starter_source)
        self.assertEqual(
            ('EXPLAIN FORMAT=JSON {source}',
             'ANALYZE FORMAT=JSON {source}'),
            tuple(source for _label, source in
                  MARIADB_PROFILE.query_plan_templates),
        )
        self.assertEqual(
            ('mariadb', 'mariadb-native', '12.2.2'),
            _engine_identity('MariaDB'),
        )

    def test_every_maintenance_case_has_a_mariadb_owned_form(self):
        operations = maintenance_operations()
        self.assertEqual(6, len(MAINTENANCE_CASES))
        self.assertEqual({
            'analyze_tables', 'check_objects', 'optimize_tables',
            'repair_objects', 'checksum_tables',
        }, {case['operation_id'] for case in MAINTENANCE_CASES})
        self.assertTrue(all(
            operations[case['operation_id']]['form_id'].startswith(
                'mariadb_'
            ) for case in MAINTENANCE_CASES
        ))

    def test_mariadb_tool_cases_use_distinct_native_forms(self):
        operations = tool_operations()
        self.assertEqual(
            ['backup_logical', 'restore_logical'],
            [case['operation_id'] for case in TOOL_CASES],
        )
        self.assertEqual(
            'mariadb_backup_logical',
            operations['backup_logical']['form_id'],
        )
        self.assertEqual(
            'mariadb_restore_logical',
            operations['restore_logical']['form_id'],
        )

    def test_mariadb_server_owns_exact_upgrade_check_form(self):
        catalog = MARIADB_ADMINISTRATION.catalog(
            catalog_for_engine('mariadb')
        )
        server = next(
            item for item in catalog['objects']
            if item['resource_kind'] == 'server'
        )
        operation = next(
            item for item in server['operations']
            if item['operation_id'] == 'check_upgrade_required'
        )
        self.assertEqual('mariadb_upgrade_check', operation['form_id'])
        self.assertEqual('read', operation['mutation_class'])
        self.assertEqual([], operation['form']['fields'])

    def test_orchestrator_honours_each_browser_gate_terminal_schema(self):
        self.assertTrue(_browser_gate_complete({
            'schema': 'cdeadmin.provider-object-form-gate.v1',
            'complete': True,
        }))
        self.assertTrue(_browser_gate_complete({
            'schema': 'cdeadmin.mariadb-maintenance-ui-gate.v1',
            'passed': True,
        }))
        self.assertTrue(_browser_gate_complete({
            'schema': 'cdeadmin.mariadb-database-lifecycle-ui-gate.v1',
            'complete': True,
        }))
        self.assertFalse(_browser_gate_complete({
            'schema': 'cdeadmin.provider-object-form-gate.v1',
            'complete': False,
            'passed': True,
        }))

    def test_focused_form_gate_uses_mariadb_owned_security_drafts(self):
        catalog = MARIADB_ADMINISTRATION.catalog(
            catalog_for_engine('mariadb')
        )
        operations = {
            (resource['resource_kind'], item['operation_id']): {
                **item, 'resource_kind': resource['resource_kind'],
            }
            for resource in catalog['objects']
            if resource['resource_kind'] in {
                'user', 'role', 'replication-channel',
            }
            for item in resource['operations']
        }
        target = {
            'display_name': 'cdeadmin_qa',
            'extensions': {'mariadb': {'native': {}}},
        }
        self.assertEqual(
            {
                'Primary authentication': 'PASSWORD',
                'Password': 'ui-preview-replacement',
            },
            _preview_values(
                'user', operations[('user', 'alter')], target, 'mariadb'
            ),
        )
        self.assertEqual(
            'cdeadmin_qa_user@%',
            _preview_values(
                'role', operations[('role', 'grant')], target, 'mariadb'
            )['User or role receiving the role'],
        )
        replication = _preview_values(
            'replication-channel',
            operations[('replication-channel', 'create')],
            target, 'mariadb',
        )
        self.assertEqual('127.0.0.1', replication['Primary host'])
        self.assertEqual('replicator', replication['Replication user'])


if __name__ == '__main__':
    unittest.main()
