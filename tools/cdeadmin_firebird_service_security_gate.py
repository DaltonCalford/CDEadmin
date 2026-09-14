#!/usr/bin/env python3
"""Qualify Services API security-context selection in an owned container."""

import argparse
import io
import json
import os
import re
import secrets
import subprocess
import tarfile
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

if __package__:
    from .cdeadmin_firebird_admin_mapping_gate import (
        _create_client, _route_arguments,
    )
else:
    from cdeadmin_firebird_admin_mapping_gate import (
        _create_client, _route_arguments,
    )
from pgadmin.cdeadmin.providers.firebird.provider import (
    _configure_client_library,
)
from pgadmin.cdeadmin.sdk.relational import RelationalClientError
from pgadmin.cdeadmin.security.secrets import SecretLease


def docker(*args, input_data=None, env=None, timeout=45, include_stderr=False):
    result = subprocess.run(['docker', *args], input=input_data,
                            capture_output=True, env=env, timeout=timeout,
                            check=False)
    if result.returncode:
        # Docker diagnostics may include environment/configuration values.
        raise RuntimeError(f'Owned Docker action {args[0]} failed')
    return result.stdout + (result.stderr if include_stderr else b'')


def read_container_file(container, path):
    data = docker('cp', container + ':' + path, '-')
    with tarfile.open(fileobj=io.BytesIO(data)) as archive:
        files = [item for item in archive.getmembers() if item.isfile()]
        if len(files) != 1:
            raise RuntimeError('Expected exactly one owned container file')
        member = files[0]
        return archive.extractfile(member).read(), member.uid, member.gid


def write_container_file(container, directory, name, data, uid, gid, mode):
    if '/' in name or name in {'.', '..'}:
        raise ValueError('Container archive filename is invalid')
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode='w') as archive:
        member = tarfile.TarInfo(name)
        member.size, member.uid, member.gid, member.mode = (
            len(data), uid, gid, mode)
        # Firebird ConfigCache starts with a zero timestamp; an epoch-zero
        # databases.conf compares equal and is never initially loaded.
        member.mtime = int(time.time())
        archive.addfile(member, io.BytesIO(data))
    docker('cp', '-a', '-', container + ':' + directory,
           input_data=stream.getvalue())


def published_port(container):
    value = docker('port', container, '3050/tcp').decode().strip()
    if not re.fullmatch(r'127\.0\.0\.1:\d+', value):
        raise RuntimeError('Owned listener is not loopback-only')
    port = int(value.rsplit(':', 1)[1])
    if not 1 <= port <= 65535:
        raise RuntimeError('Owned listener port is invalid')
    return port


def remove_owned_container(container):
    if not re.fullmatch('[0-9a-f]{64}', container):
        raise ValueError('Owned container identity is invalid')
    owner = docker('inspect', '--format',
                   '{{index .Config.Labels "cdeadmin-owned-gate"}}',
                   container).decode()
    if owner.strip() != 'firebird-service-security':
        raise RuntimeError('Container ownership label does not match')
    docker('rm', '--force', '--volumes', container)


def run(image):
    import firebird.driver as driver
    _configure_client_library(driver)
    token = uuid.uuid4().hex
    name = 'cdeadmin-firebird-security-' + token[:16]
    root_password = secrets.token_urlsafe(24)
    alternate_password = 'Alt-密-' + secrets.token_hex(10)
    username = 'CDE_ALT_' + token[:12].upper()
    database = '/var/lib/firebird/data/context_target.fdb'
    alternate = '/var/lib/firebird/data/alternate_security.fdb'
    alias = 'context_owned'
    result = {'complete': False, 'cases': [], 'failures': [],
              'container_name': name, 'owned_container_removed': False,
              'credential_values_exported': False}
    container = None
    phase = 'create-container'

    def connection(route):
        return driver.connect(password=root_password,
                              **_route_arguments(route, driver))

    def await_server(route):
        deadline = time.monotonic() + 45
        last_codes = ()
        while time.monotonic() < deadline:
            try:
                opened = connection(route)
                opened.close()
                return
            except driver.Error as exc:
                last_codes = tuple(getattr(exc, 'gds_codes', ()))
                time.sleep(0.25)
        raise RuntimeError('Owned Firebird readiness deadline exceeded; '
                           f'native status codes {last_codes}')

    try:
        env = dict(os.environ, FIREBIRD_ROOT_PASSWORD=root_password,
                   FIREBIRD_DATABASE=database)
        container = docker(
            'create', '--name', name, '--label',
            'cdeadmin-owned-gate=firebird-service-security',
            '--publish', '127.0.0.1::3050',
            '--env', 'FIREBIRD_ROOT_PASSWORD', '--env', 'FIREBIRD_DATABASE',
            image, env=env).decode().strip()
        if not re.fullmatch('[0-9a-f]{64}', container):
            raise RuntimeError('Owned container identity is invalid')
        result['container_id'] = container
        docker('start', container)
        route = {'host': '127.0.0.1', 'port': published_port(container),
                 'user': 'SYSDBA', 'database': database,
                 'auth_plugin_list': 'Srp256', 'timeout': 2}
        result['initial_port'] = route['port']
        phase = 'initial-readiness'
        await_server(route)
        phase = 'offline-security-copy'
        docker('stop', '--time', '20', container)
        # Copy only this stopped fixture's security store. Never copy an open
        # security database or any database from the user's demo instance.
        security, uid, gid = read_container_file(
            container, '/opt/firebird/security5.fdb')
        write_container_file(container, '/var/lib/firebird/data',
                             'alternate_security.fdb', security, uid, gid,
                             0o600)
        security = b''
        config, _uid, _gid = read_container_file(
            container, '/opt/firebird/databases.conf')
        config += (f'\n{alias} = {database}\n{{\n'
                   f'  SecurityDatabase = {alternate}\n}}\n'
                   f'owned_security = {alternate}\n{{\n'
                   '  RemoteAccess = false\n}\n').encode()
        write_container_file(container, '/opt/firebird', 'databases.conf',
                             config, 0, 0, 0o644)
        phase = 'alternate-start'
        docker('start', container)
        phase = 'alternate-port'
        route['port'] = published_port(container)
        result['alternate_port'] = route['port']
        phase = 'alternate-readiness'
        await_server(route)
        phase = 'alternate-user-create'
        admin = connection(route)
        try:
            with admin.cursor() as cursor:
                cursor.execute('CREATE USER "' + username + '" PASSWORD \'' +
                               alternate_password + '\' USING PLUGIN Srp')
            admin.commit()
        finally:
            admin.close()
        result['cases'].append('alternate-security-account-created')
        for context, expected in ((None, False), (database, True),
                                  (alias, True), ('missing_' + token, False)):
            client = _create_client(SimpleNamespace(
                acquire_secret=lambda *_args: SecretLease(
                    alternate_password)))
            service_route = {
                **route, 'user': username,
                'service_expected_database': context,
                'credential_reference_id': 'owned-alternate-secret',
                'principal_reference': 'owned-alternate-principal',
            }
            service_route.pop('database')
            case = ('default' if context is None else
                    'filename' if context == database else
                    'alias' if context == alias else 'unknown-context')
            try:
                try:
                    server = client._connect_server({'route': service_route})
                except RelationalClientError as exc:
                    assert not expected
                    assert 335544472 in exc.gds_codes
                    assert not client._connections
                else:
                    assert expected
                    assert '5.0.4' in server.info.version
                    assert client.close_session(server)[
                        'service_handle_released'] is True
                result['cases'].append('native-service-security-' + case)
                if expected:
                    handle = client.open_session({'route': {
                        **service_route, 'database': context}})
                    with handle.cursor() as cursor:
                        cursor.execute('SELECT CURRENT_USER FROM RDB$DATABASE')
                        assert cursor.fetchone() == (username,)
                    assert client.close_session(handle)[
                        'connection_released'] is True
                    result['cases'].append(
                        'native-database-security-' + case)
            except Exception as exc:
                result['failures'].append({'case': case,
                                           'error_type': type(exc).__name__})
            finally:
                client.close()
    except Exception as exc:
        result['failures'].append({'case': 'fixture',
                                   'phase': phase,
                                   'error_type': type(exc).__name__,
                                   'message': str(exc).replace(
                                       root_password, '[redacted]').replace(
                                           alternate_password, '[redacted]')})
    finally:
        if container is not None and re.fullmatch('[0-9a-f]{64}', container):
            if result['failures']:
                try:
                    logs = docker('logs', '--tail', '60', container,
                                  include_stderr=True).decode(errors='replace')
                    result['fixture_log_redacted'] = logs.replace(
                        root_password, '[redacted]').replace(
                            alternate_password, '[redacted]')
                except Exception:
                    result['fixture_log_unavailable'] = True
            try:
                remove_owned_container(container)
                result['owned_container_removed'] = True
            except Exception as exc:
                result['failures'].append({'case': 'owned-container-cleanup',
                                           'error_type': type(exc).__name__})
    result['complete'] = (len(result['cases']) == 7 and
                          not result['failures'] and
                          result['owned_container_removed'])
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', default='firebirdsql/firebird:5.0.4')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = run(args.image)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))
    return 0 if result['complete'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
