#!/usr/bin/env python3
"""Qualify BLOB filters in a disposable server, never in a user's instance."""

import argparse
import ctypes
import io
import json
import os
import re
import secrets
import struct
import subprocess
import tarfile
import time
import traceback
import uuid
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

if __package__:
    from .cdeadmin_firebird_external_functions_gate import (
        archive_files, docker, published_port, remove_owned, OWNER,
        _route_arguments, _configure_client_library,
        _create_client, ADMINISTRATION, SecretLease, browser_checks,
    )
else:
    from cdeadmin_firebird_external_functions_gate import (
        archive_files, docker, published_port, remove_owned, OWNER,
        _route_arguments, _configure_client_library,
        _create_client, ADMINISTRATION, SecretLease, browser_checks,
    )
from pgadmin.cdeadmin.providers.firebird import blob_filters
from pgadmin.cdeadmin.providers.firebird.error_diagnostics import status_codes
from pgadmin.cdeadmin.providers.firebird.provider import _resources
from pgadmin.cdeadmin.providers.relational_admin import (
    RelationalAdministration,
)
from pgadmin.cdeadmin.sdk.relational import RelationalClientError


ADMINISTRATION = RelationalAdministration(replace(
    ADMINISTRATION.dialect, supported={
        **ADMINISTRATION.dialect.supported,
        'blob-filter': blob_filters.OPERATIONS}))


def filtered_blob(connection, segments, *, source=-71, target=1,
                  on_write=False, buffer_size=7):
    """Exercise the installed filter via the driver's native BLOB interface."""
    from firebird.driver.types import StateResult
    from firebird.driver.fbapi import ISC_QUAD

    if (isinstance(buffer_size, bool) or not isinstance(buffer_size, int) or
            not 1 <= buffer_size <= 65535):
        raise ValueError('BLOB filter buffer requires 1 to 65535 bytes')
    if not connection.main_transaction.is_active():
        connection.begin()
    transaction = connection.main_transaction._tra
    blob_id = ISC_QUAD()
    bpb = (bytes([1, 1, 2]) + struct.pack('<h', source) + bytes([2, 2]) +
           struct.pack('<h', target))
    writer = connection._att.create_blob(
        transaction, blob_id, bpb if on_write else None)
    try:
        for segment in segments:
            writer.put_segment(len(segment), segment)
    except BaseException as error:
        try:
            writer.cancel()
        except Exception as cleanup:
            raise error from cleanup
        raise
    else:
        writer.close()
    reader = connection._att.open_blob(
        transaction, blob_id, None if on_write else bpb)
    chunks = []
    try:
        buffer = ctypes.create_string_buffer(buffer_size)
        count = ctypes.c_uint()
        while True:
            state = reader.get_segment(buffer_size, buffer,
                                       ctypes.byref(count))
            if state == StateResult.NO_DATA:
                break
            chunks.append(bytes(buffer.raw[:count.value]))
    except BaseException as error:
        try:
            reader.close()
        except Exception as cleanup:
            raise error from cleanup
        raise
    else:
        reader.close()
    return b''.join(chunks)


def run(image, source_root, build_root, browser_options=None):
    import firebird.driver as native
    _configure_client_library(native)
    build_root.mkdir(parents=True, exist_ok=False)
    password = secrets.token_urlsafe(24)
    database = '/var/lib/firebird/data/owned_filter.fdb'
    container = connection = client = None
    result = {'schema': 'cdeadmin.firebird-blob-filters.v1',
              'complete': False, 'checks': [], 'failures': [],
              'task_evidence': {},
              'owned_container_removed': False,
              'credential_values_exported': False}
    phase = 'compile-owned-filter'

    def rollback():
        if connection.main_transaction.is_active():
            connection.rollback()

    def sql(source, parameters=()):
        with connection.cursor() as cursor:
            cursor.execute(source, parameters)
            return cursor.fetchall() if cursor.description else []

    def apply(action, draft, name=None, kind='blob-filter'):
        request = {'_provider_route': route, 'resource_kind': kind,
                   'operation_id': action, 'draft': draft,
                   'target_resource': ({'display_name': name,
                                        'display_path': [name]}
                                       if name else None)}
        assert not ADMINISTRATION.validate(request)['errors']
        plan = ADMINISTRATION.plan(request)
        ADMINISTRATION.apply(client, plan, connection=connection)
        statements = [item['source'] for item in
                      plan['command_preview']['statements']]
        result['task_evidence']['visual_admin.' + kind + '.' + action] = {
            'statements': statements, 'live_execution': 'passed'}
        return statements

    def definition(name, **values):
        return {'name': name, 'input_subtype': -71, 'output_subtype': 1,
                'entrypoint': 'owned_uppercase', 'module_name': 'owned_filter',
                **values}

    def metadata(name):
        rows = sql('SELECT RDB$INPUT_SUB_TYPE, RDB$OUTPUT_SUB_TYPE, '
                   'TRIM(TRAILING FROM RDB$ENTRYPOINT), '
                   'TRIM(TRAILING FROM RDB$MODULE_NAME), '
                   'TRIM(TRAILING FROM RDB$OWNER_NAME), RDB$DESCRIPTION '
                   'FROM RDB$FILTERS WHERE RDB$FUNCTION_NAME = ?', (name,))
        return rows[0] if rows else None

    def failure(name, error):
        result['failures'].append({
            'case': name, 'error_type': type(error).__name__,
            'native_status_codes': list(status_codes(error)),
            'frames': [{'file': Path(frame.filename).name,
                        'line': frame.lineno, 'function': frame.name}
                       for frame in traceback.extract_tb(
                           error.__traceback__)]})

    def case(name, action):
        try:
            rollback()
            observation = action()
            result['checks'].append({'case': name, **(observation or {})})
        except Exception as error:
            failure(name, error)
        finally:
            try:
                rollback()
            except Exception as error:
                failure('rollback-' + name, error)

    try:
        library = build_root / 'libowned_filter.so'
        fixture = Path(__file__).parent / \
            'fixtures/firebird_owned_blob_filter.cpp'
        with (build_root / 'compiler.log').open('w') as log:
            subprocess.run(['g++', '-std=c++11', '-shared', '-fPIC', '-Wall',
                            '-Wextra', '-Werror', '-I',
                            str(source_root / 'src/include'), str(fixture),
                            '-o', str(library)], stdout=log,
                           stderr=subprocess.STDOUT, check=True, timeout=60)
        phase = 'create-owned-server'
        container = docker(
            'create', '--name', 'cdeadmin-filter-' + uuid.uuid4().hex[:16],
            '--label', 'cdeadmin-owned-gate=' + OWNER,
            '--publish', '127.0.0.1::3050', '--env', 'FIREBIRD_ROOT_PASSWORD',
            '--env', 'FIREBIRD_DATABASE', image,
            env=dict(os.environ, FIREBIRD_ROOT_PASSWORD=password,
                     FIREBIRD_DATABASE=database)).decode().strip()
        if not re.fullmatch('[0-9a-f]{64}', container):
            raise RuntimeError('Invalid owned container identity')
        result['container_id'] = container
        phase = 'configure-owned-library'
        archive = docker('cp', container + ':/opt/firebird/firebird.conf', '-')
        with tarfile.open(fileobj=io.BytesIO(archive)) as source:
            members = [item for item in source.getmembers()
                       if item.name == 'firebird.conf' and item.isfile()]
            assert len(members) == 1
            configuration = source.extractfile(members[0]).read().decode()
        configuration = re.sub(r'(?m)^\s*UdfAccess\s*=.*$', '', configuration)
        configuration += '\nUdfAccess = Restrict UDF\n'
        docker('cp', '-', container + ':/opt/firebird/',
               input_data=archive_files({
                   'firebird.conf': configuration.encode(),
                   'UDF/libowned_filter.so': library.read_bytes()}))
        docker('start', container)
        route = {'host': '127.0.0.1', 'port': published_port(container),
                 'user': 'SYSDBA', 'database': database, 'timeout': 2,
                 'auth_plugin_list': 'Srp256',
                 'credential_reference_id': 'owned-filter-secret',
                 'principal_reference': 'owned-filter-principal'}
        phase = 'readiness'
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            try:
                connection = native.connect(
                    password=password, **_route_arguments(route, native))
                break
            except native.Error:
                time.sleep(0.25)
        if connection is None:
            raise RuntimeError('Owned Firebird readiness deadline exceeded')
        result['engine_version'] = sql(
            "SELECT RDB$GET_CONTEXT('SYSTEM', 'ENGINE_VERSION') "
            'FROM RDB$DATABASE')[0][0]
        assert result['engine_version'] == '5.0.4'
        client = _create_client(SimpleNamespace(
            acquire_secret=lambda *_args: SecretLease(password)))

        def lifecycle():
            name = 'CDE_FILTER_é"東京'
            values = definition(name, description="note ' é\n  ")
            statements = apply('create', values)
            assert metadata(name) == (-71, 1, 'owned_uppercase',
                                      'owned_filter', 'SYSDBA', "note ' é\n  ")
            item = next(item for item in _resources(connection, {})
                        if item['resource_kind'] == 'blob-filter' and
                        item['display_name'] == name)
            assert item['native']['recreation_statements'] == statements
            assert set(item['native']['property_sections']) == {
                'properties', 'ddl', 'dependencies', 'dependents',
                'privileges', 'operations'}
            assert item['native']['owner'] == 'SYSDBA'
            assert item['native']['security_class']
            rollback()
            assert metadata(name) is None
            apply('create', values)
            connection.commit()
            assert metadata(name)[5] == values['description']
            apply('comment', {'description': 'changed'}, name)
            assert metadata(name)[5] == 'changed'
            rollback()
            assert metadata(name)[5] == values['description']
            apply('comment', {'description': ''}, name)
            connection.commit()
            assert metadata(name)[5] is None
            apply('drop', {'confirmation': name}, name)
            assert metadata(name) is None
            rollback()
            assert metadata(name) is not None
            apply('drop', {'confirmation': name}, name)
            connection.commit()
            assert metadata(name) is None
            return {'native_statements': statements}
        case('declaration-comment-drop-commit-rollback', lifecycle)

        for number in (-32768, -1, 0, 1, 32767):
            def numeric(number=number):
                name = 'CDE_NUMBER_' + str(abs(number)) + str(number < 0)
                apply('create', definition(name, input_subtype=number,
                                           output_subtype=-72))
                assert metadata(name)[:2] == (number, -72)
                return {'input_subtype': number, 'declaration_only': True}
            case('numeric-subtype-' + str(number), numeric)

        def mnemonic():
            values = definition('CDE_MNEMONIC')
            values.pop('output_subtype')
            values.update(output_mode='MNEMONIC', output_mnemonic='TEXT')
            apply('create', values)
            assert metadata('CDE_MNEMONIC')[:2] == (-71, 1)
        case('registered-mnemonic', mnemonic)

        def custom_mnemonic():
            subtype_name = 'CDE "custom 東京'
            name = 'CDE_CUSTOM_SUBTYPE_FILTER'
            sql('INSERT INTO RDB$TYPES (RDB$FIELD_NAME, RDB$TYPE, '
                'RDB$TYPE_NAME, RDB$SYSTEM_FLAG) VALUES (?, ?, ?, ?)',
                ('RDB$FIELD_SUB_TYPE', -79, subtype_name, 0))
            connection.commit()
            values = definition(name)
            values.pop('input_subtype')
            values.update(input_mode='MNEMONIC', input_mnemonic=subtype_name)
            apply('create', values)
            connection.commit()
            assert metadata(name)[:2] == (-79, 1)
            assert filtered_blob(connection, [b'custom'], source=-79) == (
                b'CUSTOM')
            rollback()
            apply('drop', {'confirmation': name}, name)
            sql('DELETE FROM RDB$TYPES WHERE RDB$FIELD_NAME = ? AND '
                'RDB$TYPE = ? AND RDB$TYPE_NAME = ?',
                ('RDB$FIELD_SUB_TYPE', -79, subtype_name))
            connection.commit()
            return {'registered_custom_subtype': -79,
                    'quoted_unicode_mnemonic': True,
                    'native_invocation_verified': True}
        case('registered-custom-mnemonic', custom_mnemonic)

        def recreation():
            name = 'CDE_RECREATE'
            values = definition(name, description="retained ' note\n  ")
            apply('create', values)
            connection.commit()
            observed = next(item['native'] for item in
                            _resources(connection, {})
                            if item['resource_kind'] == 'blob-filter' and
                            item['display_name'] == name)
            rollback()
            apply('drop', {'confirmation': name}, name)
            connection.commit()
            for statement in observed['recreation_statements']:
                sql(statement)
            connection.commit()
            recreated = next(item['native'] for item in
                             _resources(connection, {})
                             if item['resource_kind'] == 'blob-filter' and
                             item['display_name'] == name)
            assert recreated['recreation_statements'] == (
                observed['recreation_statements'])
            apply('drop', {'confirmation': name}, name)
            connection.commit()
        case('catalog-recreation-round-trip', recreation)

        for label, source in (
                ('unknown-mnemonic', 'DECLARE FILTER CDE_BAD INPUT_TYPE '
                 'CDE_ABSENT_TYPE OUTPUT_TYPE 1 ENTRY_POINT \'x\' '
                 'MODULE_NAME \'x\''),
                ('below-subtype-range', 'DECLARE FILTER CDE_BAD INPUT_TYPE '
                 '-32769 OUTPUT_TYPE 1 ENTRY_POINT \'x\' MODULE_NAME \'x\''),
                ('above-subtype-range', 'DECLARE FILTER CDE_BAD INPUT_TYPE '
                 '32768 OUTPUT_TYPE 1 ENTRY_POINT \'x\' MODULE_NAME \'x\''),
                ('unsupported-alter', 'ALTER FILTER CDE_BAD '
                 'ENTRY_POINT \'x\''),
                ('unsupported-execute-grant',
                 'GRANT EXECUTE ON FILTER CDE_BAD TO PUBLIC')):
            def rejected(source=source):
                try:
                    sql(source)
                    connection.commit()
                except (native.DatabaseError, RelationalClientError) as error:
                    codes = list(status_codes(error))
                    assert codes
                    return {'native_status_codes': codes}
                raise AssertionError('Unsupported native statement accepted')
            case(label, rejected)

        for same_name in (False, True):
            def duplicate(same_name=same_name):
                name = 'CDE_DUPLICATE'
                apply('create', definition(name))
                try:
                    apply('create', definition(
                        name if same_name else name + '_PAIR',
                        input_subtype=-72 if same_name else -71))
                    connection.commit()
                except (native.DatabaseError, RelationalClientError) as error:
                    codes = list(status_codes(error))
                    assert codes
                    return {'native_status_codes': codes}
                raise AssertionError('Duplicate filter declaration accepted')
            case('duplicate-name' if same_name else 'duplicate-subtype-pair',
                 duplicate)

        for direction in ('read', 'write'):
            def invocation(direction=direction):
                name = 'CDE_INVOKE_' + direction
                apply('create', definition(name))
                connection.commit()
                try:
                    segments = [b'hello\x00 world', b'a' * 131, b'Z123\xff']
                    actual = filtered_blob(connection, segments,
                                           on_write=direction == 'write')
                    assert actual == b''.join(segments).upper()
                    return {'bytes_verified': len(actual),
                            'small_buffer_segment_continuation': True}
                finally:
                    try:
                        rollback()
                        apply('drop', {'confirmation': name}, name)
                        connection.commit()
                    except Exception as error:
                        failure('cleanup-native-filter-' + direction, error)
            case('native-filter-' + direction, invocation)

        for attribute in ('entrypoint', 'module_name'):
            def missing(attribute=attribute):
                name = 'CDE_MISSING_' + attribute
                source = -75 if attribute == 'entrypoint' else -76
                apply('create', definition(name, input_subtype=source,
                                           **{attribute: 'not_present'}))
                connection.commit()
                try:
                    try:
                        filtered_blob(connection, [b'owned'], source=source)
                    except native.DatabaseError as error:
                        codes = list(status_codes(error))
                        assert codes
                        return {'native_status_codes': codes,
                                'missing_prerequisite': attribute}
                    raise AssertionError('Missing native library was ignored')
                finally:
                    rollback()
                    apply('drop', {'confirmation': name}, name)
                    connection.commit()
            case('missing-' + attribute, missing)

        def retained_filter():
            name = 'CDE_CACHED_FILTER'
            apply('create', definition(name, input_subtype=-77))
            connection.commit()
            assert filtered_blob(
                connection,
                [b'first'],
                source=-
                77) == b'FIRST'
            rollback()
            apply('drop', {'confirmation': name}, name)
            connection.commit()
            assert metadata(name) is None
            assert filtered_blob(
                connection,
                [b'cached'],
                source=-
                77) == b'CACHED'
            return {'catalog_declaration_removed': True,
                    'database_filter_cache_retains_loaded_code': True}
        case('loaded-filter-survives-declaration-drop', retained_filter)

        def permissions():
            nonlocal connection
            admin = connection
            restricted = None
            user = 'CDE_FILTER_USER'
            restricted_password = secrets.token_urlsafe(24)
            sql('CREATE USER ' + user + " PASSWORD '" +
                restricted_password + "'")
            admin.commit()
            apply('create', definition('CDE_ADMIN_FILTER'))
            admin.commit()
            privilege = {'privilege_scope': 'ddl_class', 'ddl_class': 'FILTER',
                         'ddl_privileges': ['CREATE'],
                         'principal_kind': 'USER',
                         'principal': user}

            def fresh():
                nonlocal restricted, connection
                if restricted is not None:
                    restricted.close()
                restricted = native.connect(password=restricted_password,
                                            **_route_arguments(
                                                {**route, 'user': user},
                                                native))
                connection = restricted

            try:
                fresh()
                denied = []
                for action, values, name in (
                        ('create', definition('CDE_USER_FILTER',
                                              input_subtype=-74), None),
                        ('comment', {'description': 'unauthorized'},
                         'CDE_ADMIN_FILTER'),
                        ('drop', {'confirmation': 'CDE_ADMIN_FILTER'},
                         'CDE_ADMIN_FILTER')):
                    try:
                        apply(action, values, name)
                        restricted.commit()
                    except (native.DatabaseError,
                            RelationalClientError) as error:
                        assert status_codes(error)
                        denied.append(action)
                    else:
                        raise AssertionError('Restricted mutation succeeded')
                    finally:
                        rollback()
                connection = admin
                apply('grant', privilege, kind='privilege')
                admin.commit()
                fresh()
                apply(
                    'create',
                    definition(
                        'CDE_USER_FILTER',
                        input_subtype=-
                        74))
                restricted.commit()
                assert metadata('CDE_USER_FILTER')[4] == user
                apply('comment', {'description': 'owner'}, 'CDE_USER_FILTER')
                restricted.commit()
                assert metadata('CDE_USER_FILTER')[5] == 'owner'
                apply('drop', {'confirmation': 'CDE_USER_FILTER'},
                      'CDE_USER_FILTER')
                restricted.commit()
                connection = admin
                apply('revoke', {**privilege, 'confirmation': user},
                      kind='privilege')
                admin.commit()
                fresh()
                try:
                    apply(
                        'create',
                        definition(
                            'CDE_REVOKED',
                            input_subtype=-
                            74))
                    restricted.commit()
                except (native.DatabaseError, RelationalClientError) as error:
                    assert status_codes(error)
                else:
                    raise AssertionError('Revoked CREATE FILTER still allowed')
                return {'denied_without_grant': denied,
                        'granted_owner_comment_drop': True,
                        'revoke_fresh_attachment_denied': True}
            finally:
                if restricted is not None:
                    restricted.close()
                connection = admin
                rollback()
                apply('drop', {'confirmation': 'CDE_ADMIN_FILTER'},
                      'CDE_ADMIN_FILTER')
                sql('DROP USER ' + user)
                admin.commit()
        case('restricted-grant-owner-revoke', permissions)
        if browser_options is not None and not result['failures']:
            phase = 'owned-browser-matrix'
            result['browser_checks'] = browser_checks(
                browser_options, route, password, container, build_root,
                gate_kind='blob-filters',
                fixture_kind='firebird-blob-filter-qualification')
    except Exception as error:
        failure(phase, error)
    finally:
        if connection is not None:
            try:
                connection.close()
            except Exception as error:
                failure('close-owned-attachment', error)
        if client is not None:
            try:
                client.close()
            except Exception as error:
                failure('close-provider-client', error)
        if container is not None:
            try:
                if result['failures']:
                    log = docker('exec', container, 'cat',
                                 '/opt/firebird/firebird.log')
                    (build_root / 'native-server.log').write_text(
                        log.decode(errors='replace').replace(
                            password, '[redacted]'))
            except Exception as error:
                failure('collect-owned-server-log', error)
            try:
                remove_owned(container)
                result['owned_container_removed'] = True
            except Exception as error:
                failure('remove-owned-container', error)
    result['complete'] = (len(result['checks']) == 22 and
                          not result['failures'] and
                          result['owned_container_removed'] and
                          all(item['passed'] for item in
                              result.get('browser_checks', [])))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', default='firebirdsql/firebird:5.0.4')
    parser.add_argument('--source-root', type=Path, required=True)
    parser.add_argument('--build-root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--source-config-db', type=Path)
    parser.add_argument('--desktop-user')
    parser.add_argument('--font-scale', type=int, choices=(100, 200, 300),
                        action='append')
    options = parser.parse_args()
    if options.output.exists():
        raise SystemExit('Use a new evidence file')
    if options.source_config_db and not options.desktop_user:
        parser.error('Browser checks require --desktop-user')
    result = run(options.image, options.source_root, options.build_root,
                 options if options.source_config_db else None)
    options.output.parent.mkdir(parents=True, exist_ok=True)
    options.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({'complete': result['complete'],
                      'passed': len(result['checks']),
                      'failed': len(result['failures'])}))
    return 0 if result['complete'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
