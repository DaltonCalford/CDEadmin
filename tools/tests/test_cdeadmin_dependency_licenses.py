"""Tests for installed dependency license evidence capture."""

from __future__ import annotations

import csv
import json
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cdeadmin_dependency_licenses  # noqa: E402


class DependencyLicenseTestCase(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.node_root = self.root / "node_modules"
        package = self.node_root / "demo"
        package.mkdir(parents=True)
        (package / "package.json").write_text(
            json.dumps(
                {
                    "name": "demo",
                    "version": "1.2.3",
                    "license": {"type": "MIT"},
                    "repository": {"url": "https://example.test/demo"},
                }
            ),
            encoding="utf-8",
        )
        non_package = self.node_root / "demo" / "fixture"
        non_package.mkdir()
        (non_package / "package.json").write_text(
            '"valid JSON but not an object"', encoding="utf-8"
        )
        self.output = self.root / "evidence"

    def tearDown(self):
        self.temporary.cleanup()

    def test_node_rows_normalize_license_and_repository(self):
        rows = cdeadmin_dependency_licenses.node_rows([self.node_root])
        self.assertEqual("MIT", rows[0]["declared_license"])
        self.assertEqual("https://example.test/demo", rows[0]["homepage"])

    @mock.patch.object(cdeadmin_dependency_licenses, "python_rows")
    def test_capture_writes_inventory_and_summary(self, python_rows):
        python_rows.return_value = []
        args = Namespace(
            output=str(self.output),
            node_root=[str(self.node_root)],
            baseline_id="test-baseline",
            overwrite=False,
        )
        self.assertEqual(0, cdeadmin_dependency_licenses.capture(args))
        with (self.output / "installed_dependency_license_inventory.csv").open(
            newline=""
        ) as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual("demo", rows[0]["name"])
        summary = json.loads(
            (
                self.output / "installed_dependency_license_summary.json"
            ).read_text()
        )
        self.assertEqual({"node": 1}, summary["ecosystem_counts"])


if __name__ == "__main__":
    unittest.main()
