##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

import unittest

from tools.cdeadmin_firebird_object_evidence_compose import (
    compose_documents,
)


class FirebirdObjectEvidenceComposeTestCase(unittest.TestCase):
    def test_compose_requires_every_native_database_operation(self):
        base = {
            'engine_id': 'firebird', 'exact_profile': '5.0.4',
            'operation_failures': {}, 'raw_commands_used': False,
            'passed_resource_operations': {
                'database': ['inspect', 'create', 'alter', 'drop'],
            },
            'concepts': {'relational': {'databases': {
                'status': 'passed', 'operations': {
                    'database': ['inspect', 'create', 'alter', 'drop'],
                },
            }}},
        }
        normal = {
            'reference_profile': '5.0.4', 'passed': True,
            'results': [
                {'operation': operation, 'passed': True}
                for operation in (
                    'backup_logical', 'restore_logical', 'backup_physical',
                    'restore_physical', 'validate_database',
                    'repair_database', 'sweep_database',
                    'database_statistics', 'shutdown_database',
                    'bring_online', 'set_page_cache_size',
                    'set_sweep_interval', 'set_space_reservation',
                    'set_write_mode', 'set_access_mode', 'set_sql_dialect',
                    'remove_linger', 'set_replica_mode', 'upgrade_database',
                )
            ],
        }
        faults = {
            'reference_profile': '5.0.4', 'passed': True,
            'results': [
                {'operation': 'activate_shadow', 'passed': True},
                {'operation': 'fixup_database', 'passed': True},
                {
                    'operation': 'fixup_database_normal_state',
                    'passed': True,
                },
            ],
        }
        result = compose_documents(base, normal, faults, [])
        operations = result['concepts']['relational']['databases'][
            'operations']['database']
        self.assertEqual(25, len(operations))
        self.assertNotIn('fixup_database_normal_state', operations)

    def test_compose_rejects_failed_service_evidence(self):
        with self.assertRaisesRegex(ValueError, 'did not pass'):
            compose_documents(
                {
                    'engine_id': 'firebird', 'exact_profile': '5.0.4',
                    'operation_failures': {}, 'raw_commands_used': False,
                },
                {'reference_profile': '5.0.4', 'passed': False},
                {'reference_profile': '5.0.4', 'passed': True},
                [],
            )


if __name__ == '__main__':
    unittest.main()
