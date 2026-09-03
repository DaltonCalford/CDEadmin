"""Seeded-violation tests for the CDEadmin architecture gate."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cdeadmin_architecture_gate as gate  # noqa: E402


class ArchitectureGateTestCase(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.source = Path(self.temporary.name)
        self.policy = {
            "baseline_id": "test-baseline",
            "common_roots": ["web/pgadmin/cdeadmin/core"],
            "provider_roots": ["web/pgadmin/cdeadmin/providers"],
            "test_path_parts": ["tests"],
            "support_claim_states": {
                "implemented": "qualified",
                "experimental": "limited",
                "deferred": "unavailable",
                "connector_managed": "external",
                "compatibility_mapped": "profiled",
                "unsupported": "refused",
            },
            "legacy_global_driver": {
                "root": "web/pgadmin",
                "token": "PG_DEFAULT_DRIVER",
                "baseline_total": 2,
                "maximum_by_file": {
                    "web/pgadmin/legacy.py": 2,
                },
            },
        }
        self.write(
            "web/pgadmin/legacy.py",
            "PG_DEFAULT_DRIVER\nPG_DEFAULT_DRIVER\n",
        )
        occurrences = gate.legacy_driver_occurrences(
            self.source, self.policy
        )
        self.policy["legacy_global_driver"][
            "occurrence_fingerprints_by_file"
        ] = occurrences

    def tearDown(self):
        self.temporary.cleanup()

    def write(self, relative: str, value: str) -> Path:
        path = self.source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value, encoding="utf-8")
        return path

    def rules(self) -> list[str]:
        return [item["rule"] for item in gate.evaluate(
            self.source, self.policy
        )["violations"]]

    def test_legacy_global_driver_inventory_only_shrinks(self):
        self.assertEqual([], self.rules())
        self.write("web/pgadmin/legacy.py", "PG_DEFAULT_DRIVER\n")
        result = gate.evaluate(self.source, self.policy)
        inventory = result["legacy_global_driver_inventory"]
        self.assertEqual(1, inventory["removed"])
        self.assertEqual([], result["violations"])
        self.write("web/pgadmin/new_common.py", "PG_DEFAULT_DRIVER\n")
        self.assertIn("no-new-global-driver-file", self.rules())

    def test_same_count_changed_global_driver_statement_fails(self):
        self.write(
            "web/pgadmin/legacy.py",
            "driver = PG_DEFAULT_DRIVER\nother = PG_DEFAULT_DRIVER\n",
        )
        self.assertIn("global-driver-new-occurrence", self.rules())

    def test_seeded_python_and_javascript_engine_branches_fail(self):
        self.write(
            "web/pgadmin/cdeadmin/core/service.py",
            "def route(engine):\n    return engine == 'any-engine'\n",
        )
        self.write(
            "web/pgadmin/cdeadmin/core/view.js",
            "if (providerId === 'any-provider') { enabled = true; }\n",
        )
        self.assertEqual(2, self.rules().count("no-common-engine-branch"))

    def test_common_core_provider_import_fails(self):
        self.write(
            "web/pgadmin/cdeadmin/core/service.py",
            "from pgadmin.cdeadmin import providers\n",
        )
        self.assertIn("common-provider-import-boundary", self.rules())

    def test_cross_provider_import_fails(self):
        self.write(
            "web/pgadmin/cdeadmin/providers/alpha/service.py",
            "from pgadmin.cdeadmin.providers.beta import Adapter\n",
        )
        self.write(
            "web/pgadmin/cdeadmin/providers/beta/service.py",
            "class Adapter:\n    pass\n",
        )
        self.assertIn("cross-provider-import-boundary", self.rules())

    def test_fixture_provider_below_production_root_fails(self):
        self.write(
            "web/pgadmin/cdeadmin/providers/fixture_demo/provider.py",
            "class Provider:\n    pass\n",
        )
        self.assertIn("fixture-production-exclusion", self.rules())

    def test_support_claims_are_vocabulary_and_evidence_gated(self):
        manifest = self.source / "support_claims.json"
        manifest.write_text(
            json.dumps(
                [
                    {"support_state": "implemented"},
                    {"support_state": "deferred", "enabled": True},
                    {"support_state": "invented"},
                ]
            ),
            encoding="utf-8",
        )
        rules = self.rules()
        self.assertIn("support-claim-evidence", rules)
        self.assertIn("support-claim-fail-closed", rules)
        self.assertIn("support-claim-vocabulary", rules)

    def test_versioned_identity_supplies_support_evidence(self):
        manifest = self.source / "provider_manifest.json"
        manifest.write_text(
            json.dumps({
                "support_state": "implemented",
                "identity": {"evidence_reference": "evidence:current"},
            }),
            encoding="utf-8",
        )
        self.assertNotIn("support-claim-evidence", self.rules())

    def test_freeze_rejects_increases_to_legacy_inventory(self):
        self.write(
            "web/pgadmin/legacy.py",
            "PG_DEFAULT_DRIVER\nPG_DEFAULT_DRIVER\nPG_DEFAULT_DRIVER\n",
        )
        policy_path = self.source / "policy.json"
        with self.assertRaises(SystemExit):
            gate.freeze_legacy(self.source, policy_path, self.policy)


if __name__ == "__main__":
    unittest.main()
