#!/usr/bin/env python3
"""Qualify split gbak backup/restore in a labelled disposable Firebird 5."""
import argparse
import hashlib
import json
import os
import re
import secrets
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

if __package__:
    from .cdeadmin_firebird_service_security_gate import docker, published_port
    from .cdeadmin_firebird_admin_mapping_gate import (
        _create_client, _route_arguments)
else:
    from cdeadmin_firebird_service_security_gate import docker, published_port
    from cdeadmin_firebird_admin_mapping_gate import (
        _create_client, _route_arguments)
from pgadmin.cdeadmin.providers.firebird.provider import (
    ADMINISTRATION, _configure_client_library)
from pgadmin.cdeadmin.providers.firebird.error_diagnostics import status_codes
from pgadmin.cdeadmin.security.secrets import SecretLease
from pgadmin.cdeadmin.sdk.relational import RelationalClientError


OWNER = 'firebird-logical-volumes'


def remove_owned(container):
    if not re.fullmatch('[0-9a-f]{64}', container):
        raise ValueError('Invalid owned container identity')
    label = docker('inspect', '--format',
                   '{{index .Config.Labels "cdeadmin-owned-gate"}}',
                   container).decode().strip()
    if label != OWNER:
        raise RuntimeError('Container ownership does not match')
    docker('rm', '--force', '--volumes', container)


def run(image, *, secondary_files=False):
    import firebird.driver as native
    _configure_client_library(native)
    name = 'cdeadmin-gbak-volumes-' + uuid.uuid4().hex[:16]
    password = secrets.token_urlsafe(24)
    primary = '/var/lib/firebird/data/primary.fdb'
    container = connection = client = None
    phase = 'create-container'
    result = {'complete': False, 'cases': [], 'failures': [],
              'container_name': name, 'owned_container_removed': False,
              'secondary_database_files': secondary_files,
              'credential_values_exported': False}

    def connect(database=primary):
        return native.connect(password=password, **_route_arguments(
            {**route, 'database': database}, native))

    def operation(operation_id, draft):
        request = {'engine_id': 'firebird', 'resource_kind': 'database',
                   'operation_id': operation_id, 'draft': draft,
                   '_provider_route': route}
        assert not ADMINISTRATION.validate(request)['errors']
        plan = ADMINISTRATION.plan(request)
        applied = ADMINISTRATION.apply(client, {
            'provider_payload': plan['provider_payload']})
        observed = applied['driver_observation']
        assert observed['server_completed'] is True
        assert observed['service_release']['service_handle_released'] is True
        return observed

    try:
        env = dict(os.environ, FIREBIRD_ROOT_PASSWORD=password,
                   FIREBIRD_DATABASE=primary)
        container = docker(
            'create', '--name', name, '--label',
            'cdeadmin-owned-gate=' + OWNER,
            '--publish', '127.0.0.1::3050', '--env', 'FIREBIRD_ROOT_PASSWORD',
            '--env', 'FIREBIRD_DATABASE', image, env=env).decode().strip()
        if not re.fullmatch('[0-9a-f]{64}', container):
            raise RuntimeError('Owned container identity is invalid')
        result['container_id'] = container
        phase = 'start-container'
        docker('start', container)
        route = {'host': '127.0.0.1', 'port': published_port(container),
                 'user': 'SYSDBA', 'database': primary, 'timeout': 2,
                 'auth_plugin_list': 'Srp256',
                 'credential_reference_id': 'owned-volumes-secret',
                 'principal_reference': 'owned-volumes-principal'}
        phase = 'readiness'
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            try:
                connection = connect()
                break
            except native.Error:
                time.sleep(0.25)
        if connection is None:
            raise RuntimeError('Owned Firebird readiness deadline exceeded')
        assert '5.0.4' in connection.info.firebird_version
        phase = 'owned-payload'
        # Incompressible text ensures every small split volume is used, even
        # when testing native ZIP compression. Only its digest is exported.
        rows = [(index, secrets.token_hex(1000))
                for index in range(2000 if secondary_files else 400)]
        expected = hashlib.sha256(''.join(row[1] for row in rows).encode())
        with connection.cursor() as cursor:
            cursor.execute('CREATE TABLE OWNED_VOLUME_PAYLOAD '
                           '(ID INTEGER PRIMARY KEY, V VARCHAR(2000))')
        connection.commit()
        with connection.cursor() as cursor:
            cursor.executemany(
                'INSERT INTO OWNED_VOLUME_PAYLOAD VALUES (?, ?)', rows)
        connection.commit()
        connection.close()
        connection = None
        client = _create_client(SimpleNamespace(
            acquire_secret=lambda *_args: SecretLease(password)))
        for label, sizes, flags in (
            ('two-minimum', [2048], []),
            ('three-expanded', [8192, 16384], ['EXPAND']),
            ('three-zipped', [8192, 16384], ['ZIP']),
        ):
            phase = label
            files = [f'/var/lib/firebird/data/{label}-é-{index}.fbk'
                     for index in range(len(sizes) + 1)]
            volumes = [{'filename': path, **(
                {'size_bytes': sizes[index]} if index < len(sizes) else {})}
                for index, path in enumerate(files)]
            observation = operation('backup_logical', {
                'split_backup': True, 'backup_volumes': volumes,
                'backup_flags': flags, 'verbose': True,
                'statistics': 'TDWR', 'parallel_workers': 2})
            file_sizes = [int(docker('exec', container, 'stat', '-c', '%s',
                                     path).decode().strip()) for path in files]
            assert all(size > 0 for size in file_sizes)
            destination = f'/var/lib/firebird/data/{label}.fdb'
            additional_files = [destination + '.second', destination + '.last']
            allocations = [256, 64]
            operation('restore_logical', {
                'backup_file': files[0], 'additional_backup_files': files[1:],
                'restore_database': destination, 'verbose': True,
                **({'additional_database_files': additional_files,
                    'database_file_pages': allocations}
                   if secondary_files else {})})
            connection = connect(destination)
            with connection.cursor() as cursor:
                cursor.execute('SELECT ID, V FROM OWNED_VOLUME_PAYLOAD '
                               'ORDER BY ID')
                actual = cursor.fetchall()
                if secondary_files:
                    cursor.execute('SELECT RDB$FILE_NAME, RDB$FILE_START '
                                   'FROM RDB$FILES ORDER BY RDB$FILE_START')
                    catalog_files = [(row[0].strip(), row[1])
                                     for row in cursor.fetchall()]
                    assert catalog_files == list(zip(
                        additional_files, (257, 321)))
            assert actual == rows
            connection.rollback()
            connection.close()
            connection = None
            secondary_sizes = []
            if secondary_files:
                secondary_sizes = [int(docker(
                    'exec', container, 'stat', '-c', '%s', file).decode())
                    for file in additional_files]
                assert all(size > 8192 for size in secondary_sizes)
            result['cases'].append({
                'case': label, 'requested_sizes': sizes,
                'actual_sizes': file_sizes, 'row_count': len(actual),
                'payload_sha256': expected.hexdigest(),
                'secondary_file_sizes': secondary_sizes,
                'native_output_returned': bool(observation['output']),
                'ordered_restore_verified': True})
            for denial, bad_files, expected_code in (
                ('wrong-order', list(reversed(files)), 336331015),
                # svc.cpp concatenates backup and database arguments. A
                # missing split volume consumes the output filename as an
                # input; burp.cpp reports gbak_open_bkup_error before create.
                ('missing-volume', files[:-1], 336330817),
            ):
                phase = label + '-' + denial
                denied_destination = destination + '.' + denial + '.fdb'
                try:
                    operation('restore_logical', {
                        'backup_file': bad_files[0],
                        'additional_backup_files': bad_files[1:],
                        'restore_database': denied_destination})
                except RelationalClientError as exc:
                    denied_codes = status_codes(exc)
                    if expected_code not in denied_codes:
                        result['failures'].append({
                            'stage': phase, 'type': 'UnexpectedNativeDenial',
                            'native_status_codes': list(denied_codes),
                            'expected_native_code': expected_code})
                else:
                    raise AssertionError('Invalid split restore was accepted')
                # Firebird validates split headers before opening the output.
                docker('exec', container, 'test', '!', '-e',
                       denied_destination)
                result['cases'].append({
                    'case': phase, 'native_status_codes': list(denied_codes),
                    'destination_not_created': True})
    except Exception as exc:
        result['failures'].append({'stage': phase, 'type': type(exc).__name__,
                                   'native_status_codes': list(
                                       status_codes(exc))})
    finally:
        for handle in (connection, client):
            if handle is not None:
                try:
                    handle.close()
                except Exception as exc:
                    result['failures'].append({'stage': 'release',
                                               'type': type(exc).__name__})
        if container is not None:
            try:
                remove_owned(container)
                result['owned_container_removed'] = True
            except Exception as exc:
                result['failures'].append({'stage': 'cleanup',
                                           'type': type(exc).__name__})
    result['complete'] = (not result['failures'] and len(result['cases']) == 9
                          and result['owned_container_removed'])
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', default='firebirdsql/firebird:5.0.4')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--secondary-files', action='store_true')
    options = parser.parse_args()
    if options.output.exists():
        raise SystemExit('Choose a new evidence output file')
    result = run(options.image, secondary_files=options.secondary_files)
    options.output.parent.mkdir(parents=True, exist_ok=True)
    options.output.write_text(json.dumps(result, indent=2) + '\n')
    raise SystemExit(0 if result['complete'] else 1)


if __name__ == '__main__':
    main()
