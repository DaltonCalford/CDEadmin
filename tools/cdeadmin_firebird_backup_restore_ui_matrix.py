#!/usr/bin/env python3
"""Run backup/restore UI gates on newly owned localhost Firebird databases."""
import argparse
import json
import os
import re
import subprocess
import sys
import uuid
from pathlib import Path, PurePosixPath

if __package__:
    from .cdeadmin_firebird_query_ui_gate import _load_profile
    from .cdeadmin_firebird_admin_mapping_gate import _route_arguments
else:
    from cdeadmin_firebird_query_ui_gate import _load_profile
    from cdeadmin_firebird_admin_mapping_gate import _route_arguments
from pgadmin.cdeadmin.providers.firebird.provider import (
    _configure_client_library)


ROOT = Path(__file__).resolve().parents[1]


def resolve_container(name):
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', name):
        raise ValueError('Invalid reference container name')
    result = subprocess.run(
        ['docker', 'inspect', '--format', '{{.Id}}', name],
        capture_output=True, check=False, timeout=45)
    identity = result.stdout.decode().strip()
    if result.returncode or not re.fullmatch(r'[0-9a-f]{64}', identity):
        raise RuntimeError('Reference container identity is unavailable')
    return identity


def docker(container, *arguments, absent_ok=False):
    result = subprocess.run(['docker', *arguments[:1], container,
                             *arguments[1:]], capture_output=True,
                            check=False, timeout=45)
    allowed = (0, 1) if absent_ok else (0,)
    if result.returncode not in allowed:
        raise RuntimeError('Owned Firebird fixture Docker operation failed')
    return result


def validate_profile(profile, container):
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', container):
        raise ValueError('Invalid reference container name')
    if profile.get('host') not in ('127.0.0.1', 'localhost'):
        raise ValueError('Owned Firebird UI fixtures require localhost')
    database = profile.get('database')
    if (not isinstance(database, str) or
            not PurePosixPath(database).is_absolute()):
        raise ValueError('Reference database must be an absolute server path')
    if any(char in database for char in ('\x00', '\r', '\n')):
        raise ValueError('Reference path contains a control character')
    port = profile.get('port')
    if (isinstance(port, bool) or not isinstance(port, int) or
            not 1 <= port < 65536):
        raise ValueError('Reference Firebird port is invalid')
    ports = docker(container, 'port', '3050/tcp').stdout.decode().splitlines()
    if '127.0.0.1:' + str(port) not in ports:
        raise ValueError('Reference container does not publish this endpoint')


def database_connection(native, profile, password, path, *, create=False):
    operation = native.create_database if create else native.connect
    return operation(password=password, **_route_arguments(
        {**profile, 'database': path}, native))


def drop_owned_database(native, profile, password, path):
    connection = database_connection(native, profile, password, path)
    try:
        connection.drop_database()
    except Exception:
        # Drop releases a successful attachment itself. A failed drop must
        # still close its connection, and must never fall back to unlinking.
        connection.close()
        raise


def run_scale(options, native, profile, password, scale):
    if not re.fullmatch(r'[0-9a-f]{64}', options.container):
        raise ValueError('Owned fixture requires a resolved container ID')
    folder = options.output_root / ('scale-' + str(scale))
    folder.mkdir(parents=True, exist_ok=False)
    root = PurePosixPath(profile['database']).parent
    path = str(root / ('cde_history_ui_' + uuid.uuid4().hex + '.fdb'))
    restored = path + '.RESTORED.fdb'
    preserved = path + '.RESTORED.PRESERVE.fdb'
    gate_kind = getattr(options, 'gate_kind', 'backup-history')
    if gate_kind not in {'backup-history', 'logical-volumes'}:
        raise ValueError('Unsupported owned backup browser gate')
    backups = ([path + '.single.fbk', *[
        path + f'.part-{part}.fbk' for part in range(1, 4)]]
               if gate_kind == 'logical-volumes' else
               [path + '.' + part + '.nbk'
                for part in ('ROWS', 'DAYS', 'GUID')])
    claimed = False
    result = {'scale': scale, 'complete': False, 'target_database': path,
              'restored_database': restored, 'preserved_database': preserved,
              'databases_removed': [],
              'backup_files_absent': [], 'failures': [],
              'credential_values_exported': False}
    try:
        for candidate in (path, restored, preserved, *backups):
            docker(options.container, 'exec', 'test', '!', '-e', candidate)
        claimed = True
        connection = database_connection(native, profile, password, path,
                                         create=True)
        try:
            if '5.0.4' not in connection.info.firebird_version:
                raise RuntimeError('Reference Firebird version is not 5.0.4')
        finally:
            connection.close()
        command = [sys.executable, str(
            ROOT / 'tools/cdeadmin_firebird_ui_orchestrator.py'),
            '--source-config-db', str(options.source_config_db),
            '--desktop-user', options.desktop_user, '--database', path,
            '--firebird-port', str(profile['port']),
            '--client-library', str(options.client_library),
            '--profiles', str(options.profiles),
            '--gate-kind', gate_kind,
            '--font-scale', str(scale), '--theme',
            'default' if scale == 100 else 'high-contrast',
            '--timeout', str(options.timeout),
            '--evidence-root', str(folder / 'screenshots')]
        for option, filename in (
                ('summary-output', 'summary.json'),
                ('manifest-output', 'manifest.csv'),
                ('output', 'browser.json'),
                ('server-log', 'server.log'), ('browser-log', 'browser.log')):
            command.extend(['--' + option, str(folder / filename)])
        if options.browser_binary:
            command.extend(['--browser-binary', options.browser_binary])
        environment = dict(os.environ,
                           CDEADMIN_FIREBIRD_DEMO_PASSWORD=password,
                           CDEADMIN_FIREBIRD_CLIENT_LIBRARY=str(
                               options.client_library))
        with (folder / 'orchestrator.log').open('w') as log:
            process = subprocess.run(command, cwd=ROOT, env=environment,
                                     stdout=log, stderr=subprocess.STDOUT,
                                     check=False)
        if process.returncode:
            raise RuntimeError('Firebird browser gate failed')
        observed = json.loads((folder / 'browser.json').read_text())
        if (observed.get('complete') is not True or
                observed.get('source_config_unchanged') is not True or
                observed.get('target_database') != path):
            raise RuntimeError('Firebird browser evidence target mismatch')
        docker(options.container, 'exec', 'test', '-e', restored)
        docker(options.container, 'exec', 'test', '-e', preserved)
    except Exception as exc:
        result['failures'].append({'stage': 'run', 'type': type(exc).__name__})
    finally:
        if claimed:
            try:
                for candidate in (preserved, restored, path):
                    found = docker(options.container, 'exec', 'test', '-e',
                                   candidate, absent_ok=True)
                    if found.returncode == 0:
                        drop_owned_database(native, profile, password,
                                            candidate)
                        docker(options.container, 'exec', 'test', '!', '-e',
                               candidate)
                        result['databases_removed'].append(candidate)
                for candidate in backups:
                    docker(options.container, 'exec', 'rm', '-f', candidate)
                    docker(options.container, 'exec', 'test', '!', '-e',
                           candidate)
                    result['backup_files_absent'].append(candidate)
            except Exception as exc:
                result['failures'].append({'stage': 'cleanup',
                                           'type': type(exc).__name__})
        result['complete'] = not result['failures']
        (folder / 'result.json').write_text(
            json.dumps(result, indent=2) + '\n')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profiles', type=Path, required=True)
    parser.add_argument('--source-config-db', type=Path, required=True)
    parser.add_argument('--desktop-user', required=True)
    parser.add_argument('--client-library', type=Path, required=True)
    parser.add_argument('--container', default='cdeadmin-demo-firebird')
    parser.add_argument('--output-root', type=Path, required=True)
    parser.add_argument('--font-scale', action='append', type=int,
                        choices=(100, 200, 300))
    parser.add_argument('--timeout', type=int, default=30)
    parser.add_argument('--browser-binary')
    parser.add_argument('--gate-kind', default='backup-history',
                        choices=('backup-history', 'logical-volumes'))
    options = parser.parse_args()
    if options.timeout < 1:
        raise SystemExit('Browser timeout must be positive')
    for key in ('profiles', 'source_config_db', 'client_library',
                'output_root'):
        setattr(options, key, getattr(options, key).resolve())
    for path in (options.profiles, options.source_config_db,
                 options.client_library):
        if not path.is_file():
            raise SystemExit('Required Firebird gate input file is missing')
    profile = _load_profile(options.profiles)
    password = profile.pop('password', None)
    if not isinstance(password, str) or not password:
        raise SystemExit('Reference Firebird credential is unavailable')
    options.container = resolve_container(options.container)
    validate_profile(profile, options.container)
    scales = list(dict.fromkeys(options.font_scale or (100, 200, 300)))
    if any((options.output_root / ('scale-' + str(scale))).exists()
           for scale in scales):
        raise SystemExit('Choose a fresh output directory; evidence exists')
    os.environ['CDEADMIN_FIREBIRD_CLIENT_LIBRARY'] = str(
        options.client_library)
    import firebird.driver as native
    _configure_client_library(native)
    results = []
    for scale in scales:
        result = run_scale(options, native, profile, password, scale)
        results.append(result)
        print(f'{scale}%: {"passed" if result["complete"] else "failed"}',
              flush=True)
    raise SystemExit(0 if all(item['complete'] for item in results) else 1)


if __name__ == '__main__':
    main()
