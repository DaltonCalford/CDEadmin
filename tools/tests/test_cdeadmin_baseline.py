"""Tests for the CDEadmin baseline capture tool."""

from __future__ import annotations

import csv
import json
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cdeadmin_baseline  # noqa: E402


class CdeadminBaselineTestCase(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.source = self.root / "source"
        self.output = self.root / "evidence"
        (self.source / "web" / "pgadmin" / "demo").mkdir(parents=True)
        (self.source / "web" / "migrations" / "versions").mkdir(parents=True)
        (self.source / "runtime").mkdir()
        (self.source / "web" / "regression" / "javascript").mkdir(parents=True)
        (
            self.source / "web" / "regression" / "feature_tests"
        ).mkdir(parents=True)
        (self.source / "web" / "version.py").write_text(
            "APP_RELEASE = 9\nAPP_REVISION = 17\nAPP_SUFFIX = ''\n",
            encoding="utf-8",
        )
        (self.source / "web" / "branding.py").write_text(
            "APP_NAME = 'pgAdmin 4'\nAPP_SHORT_NAME = 'pgadmin4'\n",
            encoding="utf-8",
        )
        (self.source / "web" / "pgadmin" / "demo" / "__init__.py").write_text(
            "@blueprint.route('/items', methods=['POST', 'GET'], "
            "endpoint='items')\n"
            "def items_view():\n    return None\n",
            encoding="utf-8",
        )
        migration_path = (
            self.source / "web" / "migrations" / "versions" / "first.py"
        )
        migration_path.write_text(
            "revision = 'first'\ndown_revision = None\n"
            "def upgrade():\n    pass\n"
            "def downgrade():\n    pass\n",
            encoding="utf-8",
        )
        (self.source / "requirements.txt").write_text(
            "Flask==3.1.*\n", encoding="utf-8"
        )
        (self.source / "web" / "package.json").write_text(
            json.dumps(
                {
                    "name": "demo",
                    "license": "PostgreSQL",
                    "dependencies": {"react": "1.0"},
                }
            ),
            encoding="utf-8",
        )
        (self.source / "runtime" / "package.json").write_text(
            json.dumps({"name": "runtime", "license": "PostgreSQL"}),
            encoding="utf-8",
        )
        (self.source / "parser.txt").write_text(
            "GETTOKEN = parser_next_token\n", encoding="utf-8"
        )

    def tearDown(self):
        self.temporary.cleanup()

    def args(self, **overrides):
        values = {
            "source": str(self.source),
            "output": str(self.output),
            "revision": "0123456789abcdef",
            "baseline_id": "test-baseline",
            "captured_at": "2026-08-31T00:00:00+00:00",
            "require_clean": False,
            "overwrite": False,
        }
        values.update(overrides)
        return Namespace(**values)

    def test_capture_writes_versioned_inventories(self):
        self.assertEqual(0, cdeadmin_baseline.capture(self.args()))
        metadata = json.loads(
            (self.output / "baseline_metadata.json").read_text()
        )
        self.assertEqual("test-baseline", metadata["baseline_id"])
        self.assertEqual("9.17", metadata["application_version"])
        route_path = self.output / "api_route_inventory.csv"
        with route_path.open(newline="") as stream:
            routes = list(csv.DictReader(stream))
        self.assertEqual("/items", routes[0]["route"])
        self.assertEqual("GET|POST", routes[0]["methods"])
        migration_path = self.output / "migration_inventory.csv"
        with migration_path.open(newline="") as stream:
            migrations = list(csv.DictReader(stream))
        self.assertEqual("first", migrations[0]["revision"])
        self.assertEqual("True", migrations[0]["has_downgrade"])
        secret_scan = json.loads(
            (self.output / "secret_scan.json").read_text()
        )
        self.assertEqual([], secret_scan["findings"])

    def test_output_inside_source_is_rejected(self):
        with self.assertRaises(SystemExit):
            cdeadmin_baseline.capture(
                self.args(output=str(self.source / "generated"))
            )

    def test_nonempty_output_requires_overwrite(self):
        self.output.mkdir()
        (self.output / "existing.txt").write_text("preserve", encoding="utf-8")
        with self.assertRaises(SystemExit):
            cdeadmin_baseline.capture(self.args())
        self.assertEqual(
            "preserve", (self.output / "existing.txt").read_text()
        )


if __name__ == "__main__":
    unittest.main()
