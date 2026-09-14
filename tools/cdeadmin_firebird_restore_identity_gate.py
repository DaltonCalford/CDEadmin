#!/usr/bin/env python3
"""Verify restore identity/counter policy in an owned replication server."""
import argparse
import json
import os
import re
import secrets
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

if __package__:
    from .cdeadmin_firebird_service_security_gate import (
        docker, published_port, write_container_file)
    from .cdeadmin_firebird_admin_mapping_gate import (
        _create_client, _route_arguments)
else:
    from cdeadmin_firebird_service_security_gate import (
        docker, published_port, write_container_file)
    from cdeadmin_firebird_admin_mapping_gate import (
        _create_client, _route_arguments)
from pgadmin.cdeadmin.providers.firebird.provider import (
    ADMINISTRATION, _configure_client_library)
from pgadmin.cdeadmin.security.secrets import SecretLease
from pgadmin.cdeadmin.sdk.relational import RelationalClientError
from pgadmin.cdeadmin.providers.firebird.error_diagnostics import status_codes
from pgadmin.cdeadmin.providers.firebird.restore_policy import (
    physical_restore_policy)


OWNER = 'firebird-restore-identity'


def remove_owned(container):
    if not re.fullmatch('[0-9a-f]{64}', container):
        raise ValueError('Invalid owned container identity')
    label = docker('inspect', '--format',
                   '{{index .Config.Labels "cdeadmin-owned-gate"}}',
                   container).decode().strip()
    if label != OWNER:
        raise RuntimeError('Container ownership does not match')
    docker('rm', '--force', '--volumes', container)


def run(image, *, read_only_source=False):
    import firebird.driver as native
    _configure_client_library(native)
    token = uuid.uuid4().hex
    name = 'cdeadmin-restore-identity-' + token[:16]
    password = secrets.token_urlsafe(24)
    primary = '/var/lib/firebird/data/primary.fdb'
    container = connection = client = None
    phase = 'create-container'
    result = {'complete': False, 'cases': [], 'failures': [],
              'read_only_source': read_only_source,
              'container_name': name, 'owned_container_removed': False,
              'credential_values_exported': False}

    def connect(database=primary):
        return native.connect(password=password, **_route_arguments(
            {**route, 'database': database}, native))

    def ready():
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            try:
                handle = connect()
            except native.Error:
                time.sleep(0.25)
                continue
            handle.close()
            return
        raise RuntimeError('Owned Firebird readiness deadline exceeded')

    def identity(handle):
        with handle.cursor() as cursor:
            cursor.execute("SELECT RDB$GET_CONTEXT('SYSTEM', 'DB_GUID'), "
                           "RDB$GET_CONTEXT('SYSTEM', 'REPLICATION_SEQUENCE') "
                           'FROM RDB$DATABASE')
            guid, sequence = cursor.fetchone()
        handle.rollback()
        return guid, int(sequence)

    def operation(name, draft, database=primary):
        request = {'engine_id': 'firebird', 'resource_kind': 'database',
                   'operation_id': name, 'draft': draft,
                   '_provider_route': {**route, 'database': database}}
        assert not ADMINISTRATION.validate(request)['errors']
        plan = ADMINISTRATION.plan(request)
        expected = None
        if name in {'restore_physical', 'fixup_database'}:
            expected = physical_restore_policy(name, draft)
        if expected is not None:
            assert plan['command_preview']['restore_policy_requested'] == (
                expected)
        applied = ADMINISTRATION.apply(client, {
            'provider_payload': plan['provider_payload']})
        observed = applied['driver_observation']
        assert observed['server_completed'] is True
        assert observed['service_release']['service_handle_released'] is True
        if expected is not None:
            assert observed['restore_policy_requested'] == expected

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
                 'credential_reference_id': 'owned-identity-secret',
                 'principal_reference': 'owned-identity-principal'}
        phase = 'initial-readiness'
        ready()
        phase = 'configure-owned-replication'
        owner = docker('exec', container, 'stat', '-c', '%u:%g',
                       primary).decode().strip()
        assert re.fullmatch(r'\d+:\d+', owner)
        journal = '/var/lib/firebird/data/journal'
        archive = '/var/lib/firebird/data/archive'
        docker('exec', container, 'mkdir', journal, archive)
        docker('exec', container, 'chown', owner, journal, archive)
        docker('stop', '--time', '20', container)
        configuration = (
            f'database = {primary}\n{{\n'
            f' journal_directory = {journal}\n'
            f' journal_archive_directory = {archive}\n'
            ' journal_segment_size = 1048576\n'
            ' journal_archive_timeout = 0\n'
            ' report_errors = true\n}\n').encode()
        write_container_file(container, '/opt/firebird', 'replication.conf',
                             configuration, 0, 0, 0o644)
        docker('start', container)
        route['port'] = published_port(container)
        ready()
        phase = 'produce-nonzero-replication-counter'
        connection = connect()
        assert '5.0.4' in connection.info.firebird_version
        with connection.cursor() as cursor:
            cursor.execute('CREATE TABLE OWNED_RESTORE_PAYLOAD '
                           '(ID INTEGER PRIMARY KEY, V VARCHAR(30))')
        connection.commit()
        for statement in (
                'ALTER DATABASE INCLUDE TABLE OWNED_RESTORE_PAYLOAD '
                'TO PUBLICATION', 'ALTER DATABASE ENABLE PUBLICATION'):
            with connection.cursor() as cursor:
                cursor.execute(statement)
            connection.commit()
        with connection.cursor() as cursor:
            cursor.execute('INSERT INTO OWNED_RESTORE_PAYLOAD VALUES (?, ?)',
                           (1, 'Identity restore proof'))
        connection.commit()
        source_guid, source_sequence = identity(connection)
        assert source_guid and source_sequence > 0
        result['source_replication_sequence'] = source_sequence
        connection.close()
        connection = None
        client = _create_client(SimpleNamespace(
            acquire_secret=lambda *_args: SecretLease(password)))
        if read_only_source:
            phase = 'set-source-read-only'
            operation('set_access_mode', {'mode': 'READ_ONLY'})
            connection = connect()
            source_guid, source_sequence = identity(connection)
            connection.close()
            connection = None
            result['source_replication_sequence'] = source_sequence
            phase = 'read-only-physical-backup-denial'
            try:
                operation('backup_physical', {
                    'backup_file': primary + '.denied.nbk', 'backup_level': 0})
            except RelationalClientError as exc:
                denied_codes = status_codes(exc)
                assert 335544765 in denied_codes
            else:
                raise AssertionError('Read-only physical backup was accepted')
            connection = connect()
            assert identity(connection) == (source_guid, source_sequence)
            with connection.cursor() as cursor:
                cursor.execute('SELECT MON$READ_ONLY, MON$BACKUP_STATE '
                               'FROM MON$DATABASE')
                assert cursor.fetchone() == (1, 0)
                cursor.execute('SELECT COUNT(*) FROM RDB$BACKUP_HISTORY')
                assert cursor.fetchone() == (0,)
            connection.rollback()
            connection.close()
            connection = None
            result['cases'].append({'case': phase,
                                    'native_denial_codes': list(denied_codes),
                                    'source_identity_unchanged': True,
                                    'source_read_only': True,
                                    'source_backup_state_normal': True,
                                    'history_unchanged': True})
            phase = 'read-only-logical-backup'
            logical = primary + '.fbk'
            operation('backup_logical', {
                'backup_file': logical, 'verbose': False})
            for access in ('READ_WRITE', 'READ_ONLY'):
                phase = 'logical-restore-' + access.lower()
                destination = primary + '.' + access.lower() + '.fdb'
                operation('restore_logical', {
                    'backup_file': logical, 'restore_database': destination,
                    'access_mode': access, 'verbose': False})
                connection = connect(destination)
                with connection.cursor() as cursor:
                    cursor.execute('SELECT MON$READ_ONLY FROM MON$DATABASE')
                    assert cursor.fetchone() == (int(access == 'READ_ONLY'),)
                    cursor.execute('SELECT ID, V FROM OWNED_RESTORE_PAYLOAD')
                    assert cursor.fetchall() == [(1, 'Identity restore proof')]
                connection.rollback()
                connection.close()
                connection = None
                result['cases'].append({'case': phase,
                                        'read_only': access == 'READ_ONLY',
                                        'payload_verified': True})
            return result
        phase = 'physical-full-backup'
        backup = primary + '.nbk'
        operation('backup_physical', {
            'backup_file': backup, 'backup_level': 0})
        connection = connect()
        with connection.cursor() as cursor:
            cursor.execute('SELECT FIRST 1 RDB$GUID FROM RDB$BACKUP_HISTORY '
                           'ORDER BY RDB$BACKUP_ID DESC')
            backup_guid = cursor.fetchone()[0].strip()
        connection.rollback()
        with connection.cursor() as cursor:
            cursor.execute('INSERT INTO OWNED_RESTORE_PAYLOAD VALUES (?, ?)',
                           (2, 'Incremental restore proof'))
        connection.commit()
        increment_guid, increment_sequence = identity(connection)
        connection.close()
        connection = None
        increment = primary + '.increment.nbk'
        operation('backup_physical', {
            'backup_file': increment, 'database_guid': backup_guid})
        for preserve in (False, True):
            phase = 'restore-preserve' if preserve else 'restore-reset'
            destination = primary + ('.preserve.fdb' if preserve else
                                     '.reset.fdb')
            operation('restore_physical', {
                'backup_files': [backup], 'restore_database': destination,
                'restore_flags': ['SEQUENCE'] if preserve else []})
            connection = connect(destination)
            restored_guid, restored_sequence = identity(connection)
            assert (restored_guid == source_guid) is preserve
            assert restored_sequence == (source_sequence if preserve else 0)
            with connection.cursor() as cursor:
                cursor.execute('SELECT MON$READ_ONLY FROM MON$DATABASE')
                assert cursor.fetchone() == (int(read_only_source),)
                cursor.execute('SELECT ID, V FROM OWNED_RESTORE_PAYLOAD')
                assert cursor.fetchall() == [(1, 'Identity restore proof')]
            connection.rollback()
            connection.close()
            connection = None
            result['cases'].append({'case': phase,
                                    'guid_preserved': preserve,
                                    'replication_sequence': restored_sequence,
                                    'read_only': read_only_source,
                                    'payload_verified': True})
            phase = ('in-place-preserve' if preserve else 'in-place-reset')
            inplace = {
                'backup_files': [increment], 'restore_database': destination,
                'restore_flags': ['IN_PLACE'] + (
                    ['SEQUENCE'] if preserve else [])}
            operation('restore_physical', inplace)
            connection = connect(destination)
            actual_guid, actual_sequence = identity(connection)
            assert (actual_guid == increment_guid) is preserve
            assert actual_sequence == (increment_sequence if preserve else 0)
            with connection.cursor() as cursor:
                cursor.execute('SELECT MON$READ_ONLY FROM MON$DATABASE')
                assert cursor.fetchone() == (1,)
                cursor.execute('SELECT ID, V FROM OWNED_RESTORE_PAYLOAD '
                               'ORDER BY ID')
                expected = [(1, 'Identity restore proof'),
                            (2, 'Incremental restore proof')]
                assert cursor.fetchall() == expected
            connection.rollback()
            try:
                with connection.cursor() as cursor:
                    cursor.execute('INSERT INTO OWNED_RESTORE_PAYLOAD '
                                   'VALUES (3, \'must be denied\')')
            except native.DatabaseError as exc:
                denied_codes = tuple(exc.gds_codes)
                assert 335544765 in denied_codes  # read_only_database
            else:
                raise AssertionError('In-place result accepted a write')
            finally:
                connection.rollback()
            connection.close()
            connection = None
            result['cases'].append({'case': phase,
                                    'guid_preserved': preserve,
                                    'replication_sequence': actual_sequence,
                                    'payload_verified': True,
                                    'read_only': True,
                                    'write_denial_codes': denied_codes})
            # Replaying the same increment has the wrong predecessor GUID.
            # It must be denied, never interpreted as a safe mutation retry.
            phase += '-replay-denial'
            try:
                operation('restore_physical', inplace)
            except RelationalClientError as exc:
                replay_codes = tuple(exc.gds_codes)
                assert 337117247 in replay_codes  # nbackup_wrong_orderbk
            else:
                raise AssertionError('Repeated increment was accepted')
            connection = connect(destination)
            with connection.cursor() as cursor:
                cursor.execute('SELECT ID, V FROM OWNED_RESTORE_PAYLOAD '
                               'ORDER BY ID')
                assert cursor.fetchall() == expected
            connection.rollback()
            connection.close()
            connection = None
            result['cases'].append({'case': phase, 'payload_unchanged': True,
                                    'native_denial_codes': replay_codes})
        for preserve in (False, True):
            phase = 'fixup-preserve' if preserve else 'fixup-reset'
            destination = primary + ('.fix-preserve.fdb' if preserve else
                                     '.fix-reset.fdb')
            docker('exec', container, 'test', '!', '-e', destination)
            docker('exec', container, 'cp', backup, destination)
            docker('exec', container, 'chown', owner, destination)
            draft = {'fixup_flags': ['SEQUENCE'] if preserve else []}
            operation('fixup_database', draft, destination)
            connection = connect(destination)
            actual_guid, actual_sequence = identity(connection)
            assert (actual_guid == source_guid) is preserve
            assert actual_sequence == (source_sequence if preserve else 0)
            with connection.cursor() as cursor:
                cursor.execute('SELECT MON$READ_ONLY FROM MON$DATABASE')
                assert cursor.fetchone() == (int(read_only_source),)
                cursor.execute('SELECT ID, V FROM OWNED_RESTORE_PAYLOAD')
                assert cursor.fetchall() == [(1, 'Identity restore proof')]
            connection.rollback()
            connection.close()
            connection = None
            result['cases'].append({'case': phase,
                                    'guid_preserved': preserve,
                                    'replication_sequence': actual_sequence,
                                    'read_only': read_only_source,
                                    'payload_verified': True})
            phase += '-already-fixed-denial'
            try:
                operation('fixup_database', draft, destination)
            except RelationalClientError as exc:
                fixed_codes = tuple(exc.gds_codes)
                assert 337117232 in fixed_codes  # nbackup_fixup_wrongstate
            else:
                raise AssertionError('An already-fixed database was accepted')
            connection = connect(destination)
            assert identity(connection) == (actual_guid, actual_sequence)
            connection.close()
            connection = None
            result['cases'].append({'case': phase, 'identity_unchanged': True,
                                    'native_denial_codes': fixed_codes})
    except Exception as exc:
        result['failures'].append({'stage': phase,
                                   'type': type(exc).__name__,
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
        result['complete'] = (not result['failures'] and
                              len(result['cases']) == (
                                  3 if read_only_source else 10) and
                              result['owned_container_removed'])
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', default='firebirdsql/firebird:5.0.4')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--read-only-source', action='store_true')
    options = parser.parse_args()
    if options.output.exists():
        raise SystemExit('Choose a new evidence output file')
    result = run(options.image, read_only_source=options.read_only_source)
    options.output.parent.mkdir(parents=True, exist_ok=True)
    options.output.write_text(json.dumps(result, indent=2) + '\n')
    raise SystemExit(0 if result['complete'] else 1)


if __name__ == '__main__':
    main()
