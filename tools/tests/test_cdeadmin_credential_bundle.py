##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Typed protected-column credential bundle tests."""

from __future__ import annotations

import unittest
import sys
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

from pgadmin.cdeadmin.security import (  # noqa: E402
    SecretAccessError,
    credential_from_protected_value,
    decode_credential_bundle,
    encode_credential_bundle,
)


class CredentialBundleTests(unittest.TestCase):

    def test_multiple_credentials_round_trip_by_kind(self):
        encoded = encode_credential_bundle({
            'database_password': 'primary-canary',
            'database_password_2': 'second-canary',
            'cloud_session_token': 'token-canary',
        })
        self.assertEqual(
            'second-canary',
            credential_from_protected_value(
                encoded, 'database_password_2'
            ),
        )
        self.assertEqual(
            'token-canary', decode_credential_bundle(encoded)[
                'cloud_session_token'
            ]
        )

    def test_legacy_value_requires_an_explicit_matching_kind(self):
        self.assertEqual(
            'legacy-canary',
            credential_from_protected_value(
                'legacy-canary', 'database_password',
                legacy_kind='database_password',
            ),
        )
        with self.assertRaisesRegex(SecretAccessError, 'does not match'):
            credential_from_protected_value(
                'legacy-canary', 'api_token',
                legacy_kind='database_password',
            )

    def test_malformed_and_missing_values_fail_closed(self):
        with self.assertRaises(SecretAccessError):
            decode_credential_bundle('CDEADMIN-CREDENTIAL-BUNDLE-V1:{}')
        with self.assertRaises(SecretAccessError):
            encode_credential_bundle({'database_password': ''})


if __name__ == '__main__':
    unittest.main()
