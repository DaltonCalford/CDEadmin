##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""MySQL native maintenance browser-gate contract tests."""

import unittest

from tools.cdeadmin_mysql_maintenance_ui_gate import (
    CASES,
    _operations,
    _using_data_case,
)


class MySQLMaintenanceUIGateTests(unittest.TestCase):

    def test_every_case_has_a_provider_owned_form(self):
        operations = _operations()
        self.assertEqual(9, len(CASES))
        self.assertEqual(
            {case['operation_id'] for case in CASES},
            set(operations).intersection({
                'analyze_tables', 'check_tables', 'optimize_tables',
                'repair_tables', 'checksum_tables',
            }),
        )

    def test_using_data_uses_canonical_server_document(self):
        template = next(
            case for case in CASES
            if case['case_id'] == 'analyze_histogram_using_data'
        )
        document = {'z': "operator's value", 'a': [1, 2]}
        case = _using_data_case(template, document)
        self.assertIsNone(template['expected'])
        self.assertEqual(document, case['values']['histogram_data'])
        self.assertEqual(
            'ANALYZE TABLE `cdeadmin_demo`.`qualification` UPDATE '
            'HISTOGRAM ON `value` USING DATA '
            "'{\"a\":[1,2],\"z\":\"operator''s value\"}'",
            case['expected'],
        )


if __name__ == '__main__':
    unittest.main()
