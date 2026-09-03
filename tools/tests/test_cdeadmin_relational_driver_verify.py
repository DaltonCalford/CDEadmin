##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Tests for relational full-connection driver verification."""

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.cdeadmin_relational_driver_verify import (
    PROFILES,
    _verification_succeeded,
    _version,
    verify_connection,
    verify_sqlite_cross_version,
)


class RelationalDriverVerificationTests(unittest.TestCase):

    def test_version_normalization(self):
        self.assertEqual('18.3', _version('PostgreSQL 18.3'))
        self.assertEqual('12.2.2', _version('12.2.2-MariaDB'))
        self.assertEqual('1.5.2', _version('v1.5.2'))
        self.assertIsNone(_version('unknown'))

    def test_sqlite_driver_session_is_complete_but_profile_is_separate(self):
        result = verify_connection('sqlite')
        self.assertEqual('passed', result.driver_status)
        self.assertEqual('passed', result.stages['connect'])
        self.assertEqual('passed', result.stages['parameter_roundtrip'])
        self.assertEqual('passed', result.stages['metadata'])
        self.assertFalse(result.stages['transaction_observation'][
            'finality_inferred'
        ])
        expected = (
            'exact_match' if sqlite3.sqlite_version == PROFILES['sqlite']
            else 'profile_mismatch'
        )
        self.assertEqual(expected, result.target_status)
        self.assertEqual(
            sqlite3.sqlite_version == PROFILES['sqlite'],
            result.activation_ready,
        )

    def test_cross_version_refuses_non_exact_target(self):
        with tempfile.TemporaryDirectory() as directory:
            fake = Path(directory) / 'sqlite3'
            fake.write_text('#!/bin/sh\necho 3.45.1\n', encoding='utf-8')
            fake.chmod(0o700)
            result = verify_sqlite_cross_version(fake)
        self.assertEqual('failed', result['status'])
        self.assertEqual('3.45.1', result['target_runtime'])
        self.assertEqual(
            'CDE_SQLITE_CROSS_VERSION_VERIFICATION_FAILED',
            result['diagnostics'][0]['code'],
        )

    def test_postgresql_without_explicit_dsn_is_blocked_not_failed(self):
        with patch.dict('os.environ', {}, clear=True):
            result = verify_connection('postgresql')
        self.assertEqual('blocked', result.driver_status)
        self.assertEqual('blocked', result.stages['configuration'])
        self.assertEqual(
            'CDE_RELATIONAL_CONNECTION_INPUT_REQUIRED',
            result.diagnostics[0]['code'],
        )

    def test_gate_rejects_blocked_or_non_exact_connection(self):
        blocked = {
            'driver_status': 'blocked',
            'activation_ready': False,
        }
        compatible_only = {
            'driver_status': 'passed',
            'activation_ready': False,
        }
        self.assertFalse(_verification_succeeded([blocked], None))
        self.assertFalse(_verification_succeeded([compatible_only], {
            'status': 'passed',
        }))

    def test_gate_accepts_exact_connection_and_cross_version_result(self):
        exact = {
            'driver_status': 'passed',
            'activation_ready': True,
        }
        self.assertTrue(_verification_succeeded([exact], None))
        self.assertTrue(_verification_succeeded([exact], {
            'status': 'passed',
        }))
        self.assertFalse(_verification_succeeded([exact], {
            'status': 'failed',
        }))


if __name__ == '__main__':
    unittest.main()
