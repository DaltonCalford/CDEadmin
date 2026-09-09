##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Static portability gates for the reference-engine demo estate."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import unittest


ROOT = Path(__file__).resolve().parents[2]
DEMO = ROOT / "tools/reference_engine_demos"


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ReferenceEngineDemoTestCase(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.estate = load_module("demo_estate", DEMO / "demo_estate.py")
        cls.seed = load_module("seed_demo", DEMO / "seed_demo.py")
        cls.registration = load_module(
            "register_demo_profiles", DEMO / "register_demo_profiles.py"
        )
        template = (DEMO / "connection_profiles.template.json").read_text(
            encoding="utf-8"
        )
        cls.profiles = json.loads(
            template.replace("${DEMO_ROOT}", str(DEMO)).replace(
                "${REPOSITORY_ROOT}", str(ROOT)
            )
        )["profiles"]

    def test_every_profile_has_a_native_seed_adapter(self):
        engines = set(self.estate.ENGINE_ORDER)
        self.assertEqual(26, len(engines))
        self.assertEqual(engines, {item["engine"] for item in self.profiles})
        self.assertEqual(engines, set(self.seed.SEEDERS))

    def test_reference_version_manifest_is_complete(self):
        manifest = json.loads(
            (DEMO / "engine_versions.json").read_text(encoding="utf-8")
        )
        self.assertFalse(manifest["scratchbird_native_included"])
        self.assertEqual(25, len(manifest["engines"]))
        manifest_names = {item["engine"] for item in manifest["engines"]}
        self.assertEqual(
            set(self.estate.ENGINE_ORDER) - {"yugabytedb_ycql"},
            manifest_names,
        )

    def test_fixture_has_no_workstation_paths_or_latest_images(self):
        checked_suffixes = {".py", ".json", ".xml", ".yml", ".yaml", ".sh"}
        for path in DEMO.rglob("*"):
            relative = path.relative_to(DEMO)
            if relative.parts[0] in {"data", "evidence", "runtime"}:
                continue
            if not path.is_file() or path.suffix not in checked_suffixes:
                continue
            content = path.read_text(encoding="utf-8")
            self.assertNotIn("/home/", content, str(path))
            self.assertNotIn(":latest", content, str(path))

    def test_portable_firebird_redis_and_helper_profiles(self):
        profiles = {item["engine"]: item for item in self.profiles}
        self.assertEqual(53050, profiles["firebird"]["port"])
        self.assertEqual(56379, profiles["redis"]["port"])
        self.assertTrue(
            profiles["tikv"]["helper_path"].endswith(
                "runtime/bin/cdeadmin-tikv-helper"
            )
        )
        self.assertTrue(
            profiles["foundationdb"]["fdbcli_path"].endswith(
                "runtime/foundationdb/bin/fdbcli"
            )
        )

    def test_required_checked_in_configuration_exists(self):
        required = (
            "bootstrap.py", "demo_estate.py", "seed_demo.py",
            "register_demo_profiles.py",
            "requirements.txt", "engine_versions.json",
            "connection_profiles.template.json", "config/ignite-config.xml",
            "config/vitess/docker-compose.yml",
            "config/vitess/test_keyspace_vschema.json",
            "config/vitess/tables/test_keyspace_schema_file.sql",
        )
        for relative in required:
            self.assertTrue((DEMO / relative).is_file(), relative)

    def test_registration_retains_database_below_network_server(self):
        class Target:
            def __init__(self, **values):
                self.__dict__.update(values)

        endpoint = SimpleNamespace(
            id="endpoint-id", database_targets=[],
            routes=[SimpleNamespace(configuration="{}")],
            profile_generation="old",
        )
        server = SimpleNamespace(endpoint_profile=endpoint)
        session = SimpleNamespace(add=lambda target: (
            endpoint.database_targets.append(target)
        ))
        route = {
            "host": "127.0.0.1", "port": 53050,
            "database": "/var/lib/firebird/data/example.fdb",
            "charset": "UTF8",
        }
        registration = {
            "route_kind": "network",
            "form_contract": {"database": {"forms": {"define": {
                "fields": [
                    {"field_id": "database"},
                    {"field_id": "charset", "default": "UTF8"},
                ]
            }}}},
        }
        self.registration.retain_database_target(
            SimpleNamespace(session=session), Target, server, {}, route,
            registration,
        )
        self.assertEqual(1, len(endpoint.database_targets))
        target = endpoint.database_targets[0]
        self.assertEqual("example.fdb", target.display_name)
        self.assertEqual(
            "/var/lib/firebird/data/example.fdb", target.database
        )
        self.assertEqual({"charset": "UTF8"}, json.loads(
            target.configuration
        ))
        self.assertNotIn(
            "database", json.loads(endpoint.routes[0].configuration)
        )


if __name__ == "__main__":
    unittest.main()
