"""Tests for the engine-aware Data Studio integration gate."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cdeadmin_engine_aware_data_studio_gate as gate  # noqa: E402


class EngineAwareDataStudioGateTests(unittest.TestCase):

    def test_repository_surface_passes(self):
        source = Path(__file__).resolve().parents[2]
        self.assertTrue(gate.evaluate(source)['passed'])

    def test_missing_surface_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)
            paths = (
                'web/pgadmin/static/js/Dialogs/ProviderWorkspaceContent.jsx',
                'web/pgadmin/browser/server_groups/servers/__init__.py',
                'web/pgadmin/cdeadmin/workspace/service.py',
                'web/pgadmin/cdeadmin/data_studio/studio.py',
            )
            for relative in paths:
                path = source / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('', encoding='utf-8')
            result = gate.evaluate(source)
            self.assertFalse(result['passed'])
            self.assertGreater(len(result['violations']), 0)


if __name__ == '__main__':
    unittest.main()
