##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Firebird exact-runtime staging safety tests."""

import tempfile
import unittest
from pathlib import Path

from tools.cdeadmin_firebird_object_live_orchestrator import (
    _runtime_fingerprint,
    _stage_runtime,
)


class FirebirdObjectLiveOrchestratorTests(unittest.TestCase):

    def test_staging_copies_writable_state_and_does_not_change_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / 'source'
            target = root / 'target'
            source.mkdir()
            for name in (
                'firebird.conf', 'databases.conf', 'plugins.conf',
                'security5.fdb', 'firebird.msg', 'replication.conf',
            ):
                (source / name).write_text(name, encoding='utf-8')
            for name in ('bin', 'intl', 'lib', 'plugins', 'tzdata'):
                directory = source / name
                directory.mkdir()
                (directory / 'payload').write_text(name, encoding='utf-8')

            before = _runtime_fingerprint(source)
            _stage_runtime(source, target)
            (target / 'security5.fdb').write_text(
                'staged mutation', encoding='utf-8'
            )

            self.assertEqual(before, _runtime_fingerprint(source))
            self.assertFalse((target / 'security5.fdb').is_symlink())
            self.assertTrue((target / 'bin').is_symlink())
            self.assertTrue((target / 'firebird.msg').is_symlink())

    def test_runtime_fingerprint_detects_source_content_change(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / 'runtime.dat'
            path.write_text('before', encoding='utf-8')
            before = _runtime_fingerprint(root)
            path.write_text('after', encoding='utf-8')
            self.assertNotEqual(before, _runtime_fingerprint(root))


if __name__ == '__main__':
    unittest.main()
