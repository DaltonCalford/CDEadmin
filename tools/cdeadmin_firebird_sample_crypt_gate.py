#!/usr/bin/env python3
"""Exercise gbak plugin transport using NON-SECURE Firebird sample plugins.

This qualifies API behavior, never cryptographic strength. The sample XOR
plugins are loaded only into a labelled disposable container, not a demo or
user server. Build their unmodified Firebird 5.0.4 sources outside the repo.
"""
import argparse
import hashlib
import os
import re
import secrets
import time
import uuid
import json
from pathlib import Path
from types import SimpleNamespace

if __package__:
    from .cdeadmin_firebird_logical_volumes_gate import (
        OWNER, docker, published_port, remove_owned)
    from .cdeadmin_firebird_service_security_gate import (
        read_container_file, write_container_file)
    from .cdeadmin_firebird_admin_mapping_gate import (
        _create_client, _route_arguments)
else:
    from cdeadmin_firebird_logical_volumes_gate import (
        OWNER, docker, published_port, remove_owned)
    from cdeadmin_firebird_service_security_gate import (
        read_container_file, write_container_file)
    from cdeadmin_firebird_admin_mapping_gate import (
        _create_client, _route_arguments)
from pgadmin.cdeadmin.providers.firebird.provider import (
    ADMINISTRATION, _configure_client_library, _resources)
from pgadmin.cdeadmin.providers.firebird.error_diagnostics import status_codes
from pgadmin.cdeadmin.providers.firebird.encryption_info import (
    read_encryption_text)
from pgadmin.cdeadmin.security.secrets import SecretLease


def run(image, plugins):
    import firebird.driver as native
    _configure_client_library(native)
    plugin_files = [plugins / ('lib' + name + '.so') for name in (
        'fbSampleDbCrypt', 'fbSampleKeyHolder')]
    if not all(file.is_file() for file in plugin_files):
        raise ValueError(
            'Both compiled NON-SECURE sample plugins are required')
    password = secrets.token_urlsafe(24)
    primary = '/var/lib/firebird/data/primary.fdb'
    container = connection = client = None
    phase = 'create-container'
    result = {'complete': False, 'sample_plugins_are_not_secure': True,
              'credential_values_exported': False, 'cases': [],
              'native_crypto_headers': [],
              'failures': [], 'owned_container_removed': False,
              'plugin_sha256': {file.name: hashlib.sha256(
                  file.read_bytes()).hexdigest() for file in plugin_files}}

    def connect(database=primary):
        return native.connect(password=password, **_route_arguments(
            {**route, 'database': database}, native))

    def operation(action, draft):
        request = {'engine_id': 'firebird', 'resource_kind': 'database',
                   'operation_id': action, 'draft': draft,
                   '_provider_route': route}
        assert not ADMINISTRATION.validate(request)['errors']
        plan = ADMINISTRATION.plan(request)
        applied = ADMINISTRATION.apply(client, {
            'provider_payload': plan['provider_payload']})
        receipt = applied['driver_observation']
        assert receipt['server_completed']
        assert receipt['service_release']['service_handle_released']

    def verify(destination, key=None, crypt=None, check_catalog=False):
        handle = connect(destination)
        try:
            with handle.cursor() as cursor:
                cursor.execute('SELECT ID FROM OWNED_CRYPT_DATA')
                assert cursor.fetchall() == [(1,)]
            handle.rollback()
            flags = handle.info.get_info(native.DbInfoCode.CRYPT_STATE)
            assert flags & native.EncryptionFlag.ENCRYPTED
            observed_key = read_encryption_text(
                handle.info, 'encryption_key_name')
            observed_plugin = read_encryption_text(
                handle.info, 'encryption_plugin')
            result['native_crypto_headers'].append({
                'database': destination, 'key_name': observed_key,
                'plugin': observed_plugin, 'state_flags': int(flags)})
            if key is not None:
                assert observed_key == key
            if crypt is not None:
                assert observed_plugin == crypt
            if check_catalog:
                resource = next(item for item in _resources(handle, {})
                                if item['resource_kind'] == 'database')
                assert resource['native']['encryption_key_name'] == key
                assert resource['native']['encryption_plugin'] == crypt
                handle.rollback()
        finally:
            handle.close()

    try:
        env = dict(os.environ, FIREBIRD_ROOT_PASSWORD=password,
                   FIREBIRD_DATABASE=primary)
        container = docker(
            'create', '--name', 'cdeadmin-sample-crypt-' +
            uuid.uuid4().hex[:16],
            '--label', 'cdeadmin-owned-gate=' + OWNER,
            '--publish', '127.0.0.1::3050', '--env', 'FIREBIRD_ROOT_PASSWORD',
            '--env', 'FIREBIRD_DATABASE', image, env=env).decode().strip()
        if not re.fullmatch('[0-9a-f]{64}', container):
            raise RuntimeError('Owned container identity is invalid')
        result['container_id'] = container
        phase = 'install-owned-sample-plugins'
        for file in plugin_files:
            write_container_file(container, '/opt/firebird/plugins', file.name,
                                 file.read_bytes(), 0, 0, 0o755)
        write_container_file(
            container, '/opt/firebird/plugins', 'fbSampleKeyHolder.conf',
            ('Auto = true\nKeyOWNED = 90\nKey東京 = 90\n'
             'KeyROTATED = 91\n').encode(),
            0, 0, 0o644)
        write_container_file(
            container, '/opt/firebird/plugins', 'fbSampleDbCrypt.conf',
            b'Auto = false\n', 0, 0, 0o644)
        write_container_file(
            container, '/opt/firebird/plugins', 'fbWrongKeyHolder.conf',
            b'Auto = true\nKeyOWNED = 91\n', 0, 0, 0o644)
        config, uid, gid = read_container_file(
            container, '/opt/firebird/plugins.conf')
        aliases = ''
        for alias, registered in (
            ('鍵保持', 'fbSampleKeyHolder'), ('暗号', 'fbSampleDbCrypt'),
            ('WrongKeyHolder', 'fbSampleKeyHolder'),
        ):
            config_name = ('fbWrongKeyHolder' if alias == 'WrongKeyHolder'
                           else registered)
            aliases += (
                f'\nPlugin = {alias} {{\n'
                f'  RegisterName = {registered}\n'
                f'  Module = $(dir_plugins)/{registered}\n'
                f'  ConfigFile = $(dir_plugins)/{config_name}.conf\n}}\n')
        write_container_file(
            container, '/opt/firebird', 'plugins.conf',
            config + aliases.encode(), uid, gid, 0o644)
        config, uid, gid = read_container_file(
            container, '/opt/firebird/firebird.conf')
        write_container_file(
            container, '/opt/firebird', 'firebird.conf',
            config + b'\nKeyHolderPlugin = fbSampleKeyHolder\n',
            uid, gid, 0o644)
        phase = 'start-container'
        docker('start', container)
        route = {'host': '127.0.0.1', 'port': published_port(container),
                 'user': 'SYSDBA', 'database': primary, 'timeout': 2,
                 'auth_plugin_list': 'Srp256',
                 'credential_reference_id': 'owned-sample-secret',
                 'principal_reference': 'owned-sample-principal'}
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
        with connection.cursor() as cursor:
            cursor.execute('CREATE TABLE OWNED_CRYPT_DATA (ID INTEGER)')
        connection.commit()
        with connection.cursor() as cursor:
            cursor.execute('INSERT INTO OWNED_CRYPT_DATA VALUES (1)')
        connection.commit()
        connection.close()
        connection = None
        client = _create_client(SimpleNamespace(
            acquire_secret=lambda *_args: SecretLease(password)))
        variants = (
            ('OWNED', 'fbSampleKeyHolder', 'fbSampleDbCrypt'),
            ('東京', 'fbSampleKeyHolder', 'fbSampleDbCrypt'),
            ('OWNED', '鍵保持', 'fbSampleDbCrypt'),
            ('OWNED', 'fbSampleKeyHolder', '暗号'),
            ('OWNED', '鍵保持', '暗号'),
        )
        for index, (key, holder, crypt) in enumerate(variants):
            backup = primary + f'.sample-{index}.fbk'
            phase = f'backup-key-{index}'
            try:
                operation('backup_logical', {
                    'backup_file': backup, 'key_holder': holder,
                    'crypt_plugin': crypt, 'key_name': key})
                result['cases'].append({'case': phase})
            except Exception as exc:
                result['failures'].append({
                    'stage': phase, 'type': type(exc).__name__,
                    'native_status_codes': list(status_codes(exc))})
                continue
            for explicit in (False, True):
                phase = f'restore-key-{index}-explicit-{explicit}'
                destination = primary + '.' + phase + '.fdb'
                try:
                    operation('restore_logical', {
                        'backup_file': backup, 'restore_database': destination,
                        'key_holder': holder,
                        **({'key_name': key, 'crypt_plugin': crypt}
                           if explicit else {})})
                    # Native restore.epp constructs the KEY identifier
                    # without quoting. This is a server restriction, not
                    # missing client-side UTF-8 transport.
                    assert key != '東京'
                    verify(destination, key, crypt)
                    result['cases'].append({
                        'case': phase, 'native_encrypted_flag': True,
                        'rows_verified': 1})
                except Exception as exc:
                    codes = list(status_codes(exc))
                    if key == '東京' and 335544634 in codes:
                        try:
                            docker('exec', container, 'test', '-e',
                                   destination)
                            result['cases'].append({
                                'case': phase,
                                'native_unquoted_key_rejection': codes,
                                'partial_destination_created': True})
                        except Exception as observation_error:
                            result['failures'].append({
                                'stage': phase + '-partial-destination',
                                'type': type(observation_error).__name__})
                    else:
                        result['failures'].append({
                            'stage': phase, 'type': type(exc).__name__,
                            'native_status_codes': codes})
            if key == '東京':
                phase = 'restore-explicit-valid-key-alias'
                destination = primary + '.valid-key-alias.fdb'
                try:
                    # Both names intentionally resolve to the SAME sample
                    # key byte. This is not arbitrary key substitution.
                    operation('restore_logical', {
                        'backup_file': backup, 'restore_database': destination,
                        'key_holder': holder, 'key_name': 'OWNED',
                        'crypt_plugin': crypt})
                    verify(destination, 'OWNED', crypt)
                    result['cases'].append({
                        'case': phase, 'native_encrypted_flag': True,
                        'rows_verified': 1})
                except Exception as exc:
                    result['failures'].append({
                        'stage': phase, 'type': type(exc).__name__,
                        'native_status_codes': list(status_codes(exc))})
        phase = 'restore-new-destination-key-and-plugin'
        destination = primary + '.rotated.fdb'
        try:
            operation('restore_logical', {
                'backup_file': primary + '.sample-0.fbk',
                'restore_database': destination,
                'key_holder': 'fbSampleKeyHolder',
                'key_name': 'ROTATED', 'crypt_plugin': '暗号'})
            verify(destination, 'ROTATED', '暗号', check_catalog=True)
            result['cases'].append({
                'case': phase, 'native_encrypted_flag': True,
                'rows_verified': 1,
                'destination_key_and_plugin_verified': True})
        except Exception as error:
            result['failures'].append({
                'stage': phase, 'type': type(error).__name__,
                'native_status_codes': list(status_codes(error))})
        phase = 'non-admin-header-visibility'
        try:
            reader_password = secrets.token_urlsafe(24)
            handle = connect()
            try:
                with handle.cursor() as cursor:
                    cursor.execute(
                        "CREATE USER OWNED_READER PASSWORD '" +
                        reader_password.replace("'", "''") + "'")
                handle.commit()
            finally:
                handle.close()
            reader_route = {**route, 'user': 'OWNED_READER',
                            'database': primary + '.rotated.fdb'}
            reader = native.connect(password=reader_password,
                                    **_route_arguments(reader_route, native))
            try:
                assert reader.info.get_info(native.DbInfoCode.CRYPT_STATE) & (
                    native.EncryptionFlag.ENCRYPTED)
                for field in ('encryption_key_name', 'encryption_plugin'):
                    try:
                        read_encryption_text(reader.info, field)
                    except Exception as error:
                        assert 335544788 in status_codes(error)
                    else:
                        raise AssertionError('Privileged header was disclosed')
                resource = next(item for item in _resources(reader, {})
                                if item['resource_kind'] == 'database')
                observations = resource['native']['information_observations']
                for field in ('encryption_key_name', 'encryption_plugin'):
                    assert resource['native'][field] is None
                    observed = observations[field]
                    assert observed['available'] is False
                    assert 335544788 in observed['native_status_codes']
                reader.rollback()
                result['cases'].append({
                    'case': phase, 'state_visible': True,
                    'key_and_plugin_access_denied': True,
                    'catalog_unavailability_explicit': True})
            finally:
                reader.close()
        except Exception as error:
            result['failures'].append({
                'stage': phase, 'type': type(error).__name__,
                'native_status_codes': list(status_codes(error))})
        for label, overrides, expected_code in (
            ('wrong-key', {'key_holder': 'WrongKeyHolder'}, 335545108),
            ('missing-holder', {'key_holder': 'CDEADMIN_MISSING_HOLDER'},
             335545205),
        ):
            phase = 'restore-denied-' + label
            destination = primary + '.' + phase + '.fdb'
            observed_codes = []
            try:
                try:
                    operation('restore_logical', {
                        'backup_file': primary + '.sample-0.fbk',
                        'restore_database': destination, **overrides})
                except Exception as error:
                    codes = list(status_codes(error))
                    observed_codes = codes
                    assert expected_code in codes
                    docker('exec', container, 'test', '!', '-e', destination)
                    result['cases'].append({
                        'case': phase, 'native_rejection': codes,
                        'destination_not_created': True})
                else:
                    raise AssertionError('Invalid crypt request succeeded')
            except Exception as error:
                result['failures'].append({
                    'stage': phase, 'type': type(error).__name__,
                    'native_status_codes': (
                        observed_codes or list(status_codes(error)))})
    except Exception as exc:
        result['failures'].append({
            'stage': phase, 'type': type(exc).__name__,
            'native_status_codes': list(status_codes(exc))})
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
    result['complete'] = (not result['failures'] and len(result['cases']) == 20
                          and result['owned_container_removed'])
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', default='firebirdsql/firebird:5.0.4')
    parser.add_argument('--plugins', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    options = parser.parse_args()
    if options.output.exists():
        raise SystemExit('Choose a new evidence output file')
    result = run(options.image, options.plugins)
    options.output.parent.mkdir(parents=True, exist_ok=True)
    options.output.write_text(json.dumps(result, indent=2) + '\n')
    raise SystemExit(0 if result['complete'] else 1)


if __name__ == '__main__':
    main()
