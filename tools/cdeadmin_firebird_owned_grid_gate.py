#!/usr/bin/env python3
##########################################################################
# CDEadmin - Multi-engine Database Administration
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
##########################################################################

"""Run the rendered grid gate against an owned, freshly seeded database."""

import argparse
import json
import os
import subprocess
import sys
import tempfile
import traceback
import uuid
from pathlib import Path, PurePosixPath
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.cdeadmin_firebird_admin_mapping_gate import (  # noqa: E402
    _create_client, _route_arguments,
)
from tools.reference_engine_demos.seed_demo import seed_firebird  # noqa: E402


def run(options):
    import firebird.driver as driver
    document = json.loads(options.profiles.read_text())
    route = next(dict(item) for item in document['profiles']
                 if item['engine'] == 'firebird')
    route.setdefault('host', document.get('host', '127.0.0.1'))
    source_database = route['database']
    route['database'] = str(PurePosixPath(source_database).parent /
                            ('cde_grid_owned_' + uuid.uuid4().hex + '.fdb'))
    password = route.pop('password')
    client = _create_client(SimpleNamespace(acquire_secret=None))
    result = {'complete': False, 'fixture_created': False,
              'fixture_removed': False, 'failures': [],
              'source_database_used_for_mutations': False,
              'fixture_database': route['database'],
              'credential_values_exported': False}
    connection = None
    profiles = None
    options.output_root.mkdir(parents=True, exist_ok=True)
    try:
        connection = driver.create_database(
            password=password, **_route_arguments(route, driver))
        result['fixture_created'] = True
        result['seed'] = seed_firebird(
            connection=connection, database_path=route['database'])
        connection.commit()
        connection.close()
        connection = None
        with tempfile.TemporaryDirectory(
                prefix='owned-grid-private-', dir=options.output_root) as temp:
            profiles = Path(temp) / 'profiles.json'
            descriptor = os.open(
                profiles, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, 'w') as target:
                json.dump({'profiles': [{**route, 'password': password}]},
                          target)
            environment = dict(os.environ,
                               CDEADMIN_FIREBIRD_DEMO_PASSWORD=password)
            command = [sys.executable,
                       str(ROOT / 'tools' /
                           'cdeadmin_firebird_ui_orchestrator.py'),
                       '--source-config-db', str(options.source_config_db),
                       '--desktop-user', options.desktop_user,
                       '--database', route['database'],
                       '--host', route['host'], '--user',
                       route.get('user', route.get('username', 'SYSDBA')),
                       '--firebird-port', str(route['port']),
                       '--client-library', os.environ[
                           'CDEADMIN_FIREBIRD_CLIENT_LIBRARY'],
                       '--profiles', str(profiles), '--gate-kind', 'grid',
                       '--font-scale', str(options.font_scale),
                       '--theme', options.theme, '--timeout', '45']
            for key, filename in (
                    ('evidence-root', 'screenshots'),
                    ('summary-output', 'browser-summary.json'),
                    ('manifest-output', 'manifest.csv'),
                    ('output', 'orchestrator.json'),
                    ('server-log', 'server.log'),
                    ('browser-log', 'browser.log')):
                command.extend(['--' + key,
                                str(options.output_root / filename)])
            completed = subprocess.run(command, cwd=ROOT, env=environment,
                                       check=False)
            observation = options.output_root / 'orchestrator.json'
            if observation.is_file():
                result['orchestrator'] = json.loads(observation.read_text())
            if completed.returncode != 0:
                raise RuntimeError('Owned-database browser gate failed')
        result['private_profiles_removed'] = not profiles.exists()
    except Exception:
        result['failures'].append({'traceback': traceback.format_exc()})
    finally:
        result['private_profiles_removed'] = (
            profiles is None or not profiles.exists())
        if result['fixture_created']:
            try:
                if connection is None:
                    connection = driver.connect(
                        password=password, **_route_arguments(route, driver))
                if connection.main_transaction.is_active():
                    connection.rollback()
                connection.drop_database()
                result['fixture_removed'] = True
                connection = None
            except Exception:
                result['failures'].append({
                    'case': 'owned-database-cleanup',
                    'traceback': traceback.format_exc()})
        if connection is not None:
            client._forget_and_close(connection)
    result['complete'] = bool(
        not result['failures'] and result['fixture_removed'] and
        result.get('private_profiles_removed') and
        result.get('orchestrator', {}).get('complete'))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profiles', type=Path, required=True)
    parser.add_argument('--source-config-db', type=Path, required=True)
    parser.add_argument('--desktop-user', required=True)
    parser.add_argument('--output-root', type=Path, required=True)
    parser.add_argument('--font-scale', type=int, choices=(100, 200),
                        default=100)
    parser.add_argument('--theme', choices=('default', 'high-contrast'),
                        default='default')
    options = parser.parse_args()
    result = run(options)
    (options.output_root / 'result.json').write_text(
        json.dumps(result, indent=2) + '\n')
    print(json.dumps({'complete': result['complete'],
                      'fixture_removed': result['fixture_removed'],
                      'failures': result['failures']}))
    return 0 if result['complete'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
