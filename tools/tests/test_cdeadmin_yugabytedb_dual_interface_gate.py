##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""YugabyteDB dual-interface live-gate orchestration tests."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / 'tools'
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import cdeadmin_yugabytedb_dual_interface_live_gate as gate  # noqa: E402


def arguments(output, **changes):
    values = {
        'host': '127.0.0.1', 'ysql_port': 5433, 'ycql_port': 9042,
        'version_api_host': None, 'version_api_port': 7000,
        'ysql_database': 'yugabyte', 'ysql_user': 'yugabyte',
        'ysql_password_env': None, 'ysql_sslmode': None,
        'ycql_local_dc': 'datacenter1', 'ycql_username': None,
        'ycql_password_env': 'CDEADMIN_YCQL_PASSWORD',
        'allow_mutation': True, 'output': output,
    }
    values.update(changes)
    return SimpleNamespace(**values)


class YugabyteDBDualInterfaceGateTests(unittest.TestCase):

    def test_both_protocol_owned_gates_are_required(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'dual.json'
            with patch.object(gate, 'verify_ysql', return_value={
                'status': 'passed'
            }) as ysql, patch.object(gate, 'verify_ycql', return_value={
                'passed': True
            }) as ycql:
                result = gate.verify(arguments(output))
        self.assertTrue(result['passed'])
        self.assertEqual({'ysql', 'ycql'}, set(result['interfaces']))
        self.assertEqual(
            'postgresql_wire', result['interfaces']['ysql']['protocol_id']
        )
        self.assertEqual('cql', result['interfaces']['ycql']['protocol_id'])
        self.assertEqual(5433, ysql.call_args.args[0].port)
        self.assertEqual(9042, ycql.call_args.args[0].port)

    def test_one_interface_failure_fails_the_combined_gate(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'dual.json'
            with patch.object(gate, 'verify_ysql', return_value={
                'status': 'passed'
            }), patch.object(gate, 'verify_ycql', return_value={
                'passed': False
            }):
                result = gate.verify(arguments(output))
        self.assertFalse(result['passed'])
        self.assertEqual('failed', result['interfaces']['ycql']['status'])

    def test_mutation_consent_is_mandatory(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(RuntimeError, 'allow-mutation'):
                gate.verify(arguments(
                    Path(directory) / 'dual.json', allow_mutation=False
                ))


if __name__ == '__main__':
    unittest.main()
