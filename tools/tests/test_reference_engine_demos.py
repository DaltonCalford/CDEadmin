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
import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock


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

    def test_tidb_readiness_uses_authenticated_sql_without_database(self):
        import mysql.connector
        connection = mock.MagicMock()
        cursor = connection.cursor.return_value
        cursor.fetchone.return_value = ('8.0.11-TiDB-v8.5.6',)
        with mock.patch.object(mysql.connector, 'connect',
                               return_value=connection) as connect:
            self.assertTrue(self.estate._tidb_sql_ready())
        self.assertNotIn('database', connect.call_args.kwargs)
        self.assertEqual([mock.call('SELECT VERSION()'),
                          mock.call('ADMIN SHOW DDL JOBS 1')],
                         cursor.execute.call_args_list)
        cursor.close.assert_called_once()
        connection.close.assert_called_once()

    def test_tidb_readiness_rejects_wrong_engine_and_closes_on_failure(self):
        import mysql.connector
        for failure in ('wrong_version', 'query_failure'):
            with self.subTest(failure=failure):
                connection = mock.MagicMock()
                cursor = connection.cursor.return_value
                cursor.fetchone.return_value = ('8.0.11-MySQL',)
                if failure == 'query_failure':
                    cursor.execute.side_effect = RuntimeError('not ready')
                with mock.patch.object(mysql.connector, 'connect',
                                       return_value=connection):
                    with self.assertRaises(RuntimeError):
                        self.estate._tidb_sql_ready()
                cursor.close.assert_called_once()
                connection.close.assert_called_once()

    def test_every_profile_has_a_native_seed_adapter(self):
        engines = set(self.estate.ENGINE_ORDER)
        self.assertEqual(26, len(engines))
        self.assertEqual(engines, {item["engine"] for item in self.profiles})
        self.assertEqual(engines, set(self.seed.SEEDERS))

    def test_vitess_readiness_requires_its_native_keyspace(self):
        import mysql.connector
        for rows, ready in [([], False), ([('test_keyspace',)], True)]:
            with self.subTest(rows=rows):
                connection = mock.MagicMock()
                cursor = connection.cursor.return_value
                cursor.fetchall.return_value = rows
                with mock.patch.object(mysql.connector, 'connect',
                                       return_value=connection):
                    if ready:
                        self.assertTrue(self.estate._vitess_sql_ready())
                    else:
                        with self.assertRaises(RuntimeError):
                            self.estate._vitess_sql_ready()
                cursor.execute.assert_called_once_with('SHOW VITESS_KEYSPACES')
                cursor.close.assert_called_once()
                connection.close.assert_called_once()

    def test_tidb_uses_separate_storage_without_removing_old_volumes(self):
        source = (DEMO / 'demo_estate.py').read_text()
        start = source.split('def start_tidb():')[1].split(
            'def _tidb_sql_ready():')[0]
        self.assertNotIn('start_tikv_stack()', start)
        self.assertIn('cdeadmin-demo-tidb-retained-api-v2', start)
        self.assertIn("'docker', 'rename'", start)
        self.assertNotIn("'docker', 'rm'", start)
        config = (DEMO / 'config/tidb/transactional.toml').read_text()
        self.assertIn('api-version = 1', config)
        self.assertIn('enable-ttl = false', config)

    def test_http_readiness_checks_native_response(self):
        response = mock.MagicMock()
        response.__enter__.return_value = response
        with mock.patch.object(self.estate.urllib.request, 'urlopen',
                               return_value=response):
            response.read.return_value = b'Ok.\n'
            self.assertTrue(self.estate._http_engine_ready('clickhouse'))
            response.read.return_value = b'not ready'
            with self.assertRaises(RuntimeError):
                self.estate._http_engine_ready('clickhouse')
            for status, succeeds in [('red', False), ('yellow', True),
                                     ('green', True)]:
                with mock.patch.object(self.estate.json, 'load',
                                       return_value={'status': status}):
                    if succeeds:
                        self.assertTrue(self.estate._http_engine_ready(
                            'opensearch'))
                    else:
                        with self.assertRaises(RuntimeError):
                            self.estate._http_engine_ready('opensearch')

    def test_all_demo_profiles_pass_real_registration_validation(self):
        from pgadmin.cdeadmin.endpoints.profiles import (
            registration_profiles, provider_route_options,
        )
        registrations = {p['profile_id']: p for p in registration_profiles()}
        for item in self.profiles:
            with self.subTest(engine=item['engine']):
                profile = self.registration.registration_for(
                    item, registrations)
                route = self.registration.route_for(
                    item, '127.0.0.1', profile, provider_route_options)
                self.assertNotIn('password', route)
                if item['engine'] == 'vitess':
                    self.assertEqual(15099, route['vtgate_http_port'])
                if item['engine'] == 'tikv':
                    self.assertNotIn('enable_ttl', route)
                if item['engine'] == 'tidb':
                    self.assertTrue(route['ssl_disabled'])
                if item['engine'] == 'mysql':
                    self.assertFalse(route['ssl_disabled'])

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
            # Exact container distributions may legitimately live below a
            # product-owned home (for example /home/yugabyte). Reject the
            # workstation-specific path that would make the fixture
            # non-portable, not every valid container path.
            self.assertNotIn("/home/dcalford/", content, str(path))
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

    def test_tikv_fixture_uses_four_api_v2_ttl_stores(self):
        profiles = {item["engine"]: item for item in self.profiles}
        tikv = profiles["tikv"]
        self.assertEqual(2, tikv["api_version"])
        self.assertTrue(tikv["enable_ttl"])
        self.assertEqual(4, len(self.estate.TIKV_NODES))
        self.assertEqual(
            "api-v2-four-store-control-plane-v1",
            self.estate.TIKV_CONFIGURATION,
        )

        names = [node[0] for node in self.estate.TIKV_NODES]
        client_ports = [node[1] for node in self.estate.TIKV_NODES]
        status_ports = [node[2] for node in self.estate.TIKV_NODES]
        volumes = [node[3] for node in self.estate.TIKV_NODES]
        self.assertEqual(4, len(set(names)))
        self.assertEqual(4, len(set(client_ports)))
        self.assertEqual(4, len(set(status_ports)))
        self.assertEqual(4, len(set(volumes)))

        configuration = (
            DEMO / "config/tikv/api-v2.toml"
        ).read_text(encoding="utf-8")
        self.assertIn("api-version = 2", configuration)
        self.assertIn("enable-ttl = true", configuration)

    def test_required_checked_in_configuration_exists(self):
        required = (
            "bootstrap.py", "demo_estate.py", "seed_demo.py",
            "register_demo_profiles.py",
            "requirements.txt", "engine_versions.json",
            "connection_profiles.template.json", "config/ignite-config.xml",
            "config/vitess/docker-compose.yml",
            "config/vitess/test_keyspace_vschema.json",
            "config/vitess/tables/test_keyspace_schema_file.sql",
            "config/tikv/api-v2.toml",
        )
        for relative in required:
            self.assertTrue((DEMO / relative).is_file(), relative)

    def test_every_profile_has_an_executable_lifecycle_launcher(self):
        lifecycle = DEMO / "lifecycle"
        launchers = {
            path.stem for path in lifecycle.glob("*.sh")
            if not path.name.startswith("_")
        }
        self.assertEqual(set(self.estate.ENGINE_ORDER), launchers)
        for engine in self.estate.ENGINE_ORDER:
            launcher = lifecycle / f"{engine}.sh"
            self.assertTrue(launcher.stat().st_mode & 0o111, launcher)
            syntax = subprocess.run(
                ["bash", "-n", str(launcher)], capture_output=True,
                text=True, check=False,
            )
            self.assertEqual(0, syntax.returncode, syntax.stderr)
            content = launcher.read_text(encoding="utf-8")
            self.assertIn(f" {engine} \"$@\"", content)

        helper = lifecycle / "_engine_lifecycle.sh"
        self.assertTrue(helper.is_file())
        self.assertTrue(helper.stat().st_mode & 0o111)
        syntax = subprocess.run(
            ["bash", "-n", str(helper)], capture_output=True,
            text=True, check=False,
        )
        self.assertEqual(0, syntax.returncode, syntax.stderr)

    def test_every_launcher_dispatches_startup_and_shutdown(self):
        lifecycle = DEMO / "lifecycle"
        with tempfile.TemporaryDirectory() as temporary:
            temporary_path = Path(temporary)
            calls = temporary_path / "calls.log"
            python = temporary_path / "python3"
            python.write_text(
                "#!/usr/bin/env bash\n"
                "printf '%s\\n' \"$*\" >> \"$CDEADMIN_LIFECYCLE_LOG\"\n",
                encoding="utf-8",
            )
            python.chmod(0o755)
            environment = dict(os.environ)
            environment["PATH"] = (
                f"{temporary_path}{os.pathsep}{environment['PATH']}"
            )
            environment["CDEADMIN_LIFECYCLE_LOG"] = str(calls)

            for engine in self.estate.ENGINE_ORDER:
                launcher = lifecycle / f"{engine}.sh"
                subprocess.run(
                    [launcher, "startup"], check=True, env=environment
                )
                subprocess.run(
                    [launcher, "shutdown"], check=True, env=environment
                )

            observed = calls.read_text(encoding="utf-8").splitlines()
            expected = []
            for engine in self.estate.ENGINE_ORDER:
                expected.extend((
                    f"{DEMO / 'demo_estate.py'} start {engine}",
                    f"{DEMO / 'demo_estate.py'} stop {engine}",
                ))
            self.assertEqual(expected, observed)

    def test_status_can_be_limited_to_one_profile(self):
        with mock.patch("builtins.print") as output:
            self.estate.status("sqlite")
        document = json.loads(output.call_args.args[0])
        self.assertEqual([{"engine": "sqlite", "runtime": "file",
                           "seeded": (
                               DEMO / "evidence/sqlite.json"
                           ).exists()}], document)

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
