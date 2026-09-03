##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Provider-owned external tool authority tests."""

from __future__ import annotations

import sys
import tempfile
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

from pgadmin.cdeadmin.sdk.tooling import (  # noqa: E402
    ProviderToolError,
    ProviderToolGrant,
    ProviderToolRunner,
)


class ProviderToolRunnerTests(unittest.TestCase):

    def test_secret_file_is_private_redacted_and_removed(self):
        with tempfile.TemporaryDirectory(
            prefix='cdeadmin-provider-tool-test-'
        ) as workspace:
            secret = 'credential-canary-never-export'
            runner = ProviderToolRunner({'python': sys.executable})
            grant = ProviderToolGrant(
                executable_id='python', workspace=workspace,
                endpoint_host='127.0.0.1', endpoint_port=27017,
            )
            result = runner.run(
                grant, [
                    '-c',
                    'import pathlib,sys;print('
                    'pathlib.Path(sys.argv[1]).read_text(),end="")',
                ], secret_config=secret.encode('utf-8'),
                secret_argument='{path}', secret_suffix='.secret',
                redact_values=(secret,),
            )
            self.assertEqual(0, result['return_code'])
            self.assertEqual('[redacted]', result['stdout'])
            self.assertNotIn(secret, repr(result))
            self.assertEqual([], list(Path(workspace).iterdir()))
            self.assertTrue(result['local_process_observation_only'])
            self.assertFalse(result['remote_finality_inferred'])

    def test_grants_and_arguments_fail_closed(self):
        with self.assertRaisesRegex(ProviderToolError, 'over-broad'):
            ProviderToolGrant(
                executable_id='python', workspace='/',
                endpoint_host='127.0.0.1', endpoint_port=27017,
            )
        with tempfile.TemporaryDirectory(
            prefix='cdeadmin-provider-tool-test-'
        ) as workspace:
            grant = ProviderToolGrant(
                executable_id='missing', workspace=workspace,
                endpoint_host='127.0.0.1', endpoint_port=27017,
            )
            runner = ProviderToolRunner({'missing': '/absent/cdeadmin-tool'})
            with self.assertRaisesRegex(ProviderToolError, 'unavailable'):
                runner.run(grant, [])

    def test_secret_environment_requires_an_explicit_name_grant(self):
        with tempfile.TemporaryDirectory(
            prefix='cdeadmin-provider-tool-test-'
        ) as workspace:
            runner = ProviderToolRunner({'python': sys.executable})
            grant = ProviderToolGrant(
                executable_id='python', workspace=workspace,
                endpoint_host='127.0.0.1', endpoint_port=7687,
                secret_environment_names=('NEO4J_PASSWORD',),
            )
            secret = 'environment-canary-never-export'
            result = runner.run(
                grant, [
                    '-c', 'import os;print(os.environ["NEO4J_PASSWORD"])',
                ], secret_environment={'NEO4J_PASSWORD': secret},
                redact_values=(secret,),
            )
            self.assertEqual('[redacted]\n', result['stdout'])
            with self.assertRaisesRegex(ProviderToolError, 'not granted'):
                runner.run(
                    grant, ['-c', 'pass'],
                    secret_environment={'UNGRANTED_SECRET': secret},
                )


if __name__ == '__main__':
    unittest.main()
