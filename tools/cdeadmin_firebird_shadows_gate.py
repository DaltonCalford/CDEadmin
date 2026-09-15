#!/usr/bin/env python3
"""Qualify shadow catalog/DDL/filesystem behavior on an owned Firebird."""

import argparse
import json
import os
import re
import secrets
import subprocess
import time
import traceback
import uuid
from pathlib import Path
from types import SimpleNamespace

if __package__:
    from .cdeadmin_firebird_external_functions_gate import (
        docker, published_port, remove_owned, OWNER, _route_arguments,
        _configure_client_library,
        _create_client, SecretLease, browser_checks,
    )
else:
    from cdeadmin_firebird_external_functions_gate import (
        docker, published_port, remove_owned, OWNER, _route_arguments,
        _configure_client_library,
        _create_client, SecretLease, browser_checks,
    )
from pgadmin.cdeadmin.providers.firebird import shadows
from pgadmin.cdeadmin.providers.firebird.error_diagnostics import status_codes
from pgadmin.cdeadmin.providers.firebird.provider import (
    ADMINISTRATION, _resources,
)
from pgadmin.cdeadmin.resources.properties import normalize_resource_properties


def run(image, build_root, browser_options=None):
    import firebird.driver as native
    _configure_client_library(native)
    build_root.mkdir(parents=True, exist_ok=False)
    password = secrets.token_urlsafe(24)
    database = '/var/lib/firebird/data/owned_shadows.fdb'
    container = connection = client = None
    result = {'schema': 'cdeadmin.firebird-shadows.v1', 'complete': False,
              'checks': [], 'failures': [], 'task_evidence': {},
              'owned_container_removed': False,
              'credential_values_exported': False}
    phase = 'create-owned-server'

    def connect():
        return native.connect(password=password,
                              **_route_arguments(route, native))

    def sql(statement, parameters=()):
        with connection.cursor() as cursor:
            cursor.execute(statement, parameters)
            return cursor.fetchall() if cursor.description else []

    def rollback():
        if connection.main_transaction.is_active():
            connection.rollback()

    def failure(label, error):
        result['failures'].append({
            'case': label, 'error_type': type(error).__name__,
            'native_status_codes': list(status_codes(error)),
            'frames': [{'file': Path(frame.filename).name,
                        'line': frame.lineno, 'function': frame.name}
                       for frame in traceback.extract_tb(
                           error.__traceback__)]})

    def apply(operation, number, **draft):
        if operation == 'create':
            draft['number'] = number
        else:
            draft['confirmation'] = str(number)
        request = {'_provider_route': route, 'resource_kind': 'shadow',
                   'operation_id': operation, 'draft': draft,
                   'target_resource': {'resource_kind': 'shadow',
                                       'display_name': str(number)}}
        assert not ADMINISTRATION.validate(request)['errors']
        plan = ADMINISTRATION.plan(request)
        receipt = ADMINISTRATION.apply(client, plan, connection=connection)
        assert receipt['staged_in_provider_session'] is True
        statements = [item['source'] for item in
                      plan['command_preview']['statements']]
        return statements

    def catalog(number):
        return sql('SELECT RDB$FILE_NAME, '
                   'RDB$FILE_SEQUENCE, RDB$FILE_START, RDB$FILE_LENGTH, '
                   'RDB$FILE_FLAGS FROM RDB$FILES WHERE '
                   'RDB$SHADOW_NUMBER = ? ORDER BY RDB$FILE_SEQUENCE',
                   (number,))

    def exists(filename):
        if not filename.startswith('/var/lib/firebird/data/owned_shadow_'):
            raise RuntimeError('File observation is outside the owned fixture')
        probe = subprocess.run(
            ['docker', 'exec', container, 'test', '-f', filename],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20,
            check=False)
        if probe.returncode not in (0, 1):
            raise RuntimeError('Owned file observation failed')
        return probe.returncode == 0

    def lifecycle(number, mode, conditional, multiple, preserve):
        nonlocal connection
        path = '/var/lib/firebird/data/owned_shadow_' + str(number)
        paths = [path + "_影's.shd"]
        baseline = path + '_baseline.shd'
        if conditional:
            apply('create', number + 100, filename=baseline)
            connection.commit()
        options = {'filename': paths[0], 'mode': mode,
                   'conditional': conditional}
        if multiple:
            paths.extend([path + '_two.shd', path + '_three.shd'])
            options.update(length=256, secondary_files=[
                {'filename': paths[1], 'length': 256},
                {'filename': paths[2], 'start': 700},
            ])
        apply('create', number, **options)
        assert len(catalog(number)) == len(paths)
        rollback()
        assert not catalog(number)
        rollback()
        assert not any(exists(filename) for filename in paths)
        creation = apply('create', number, **options)
        connection.commit()
        rows = catalog(number)
        assert [row[0] for row in rows] == paths
        assert [row[1] for row in rows] == list(range(len(paths)))
        flags = shadows.file_flags(rows[0][4], shadow=True)
        assert flags['shadow'] is True
        assert flags['manual'] is (mode == 'MANUAL')
        assert flags['conditional'] is conditional
        assert all(exists(filename) for filename in paths)
        rollback()
        observed = shadows.metadata(number, rows)
        assert observed['mode'] == mode
        assert observed['conditional'] is conditional
        resources = _resources(connection, {'route': route})
        for resource in resources:
            normalize_resource_properties(resource.get('native', {}))
        resource = next(item for item in resources if
                        item['resource_kind'] == 'shadow' and
                        item['display_name'] == str(number))
        assert resource['native']['ddl'] == observed['ddl']
        children = [item for item in resources if
                    item['resource_kind'] == 'storage-file' and
                    item['native'].get('navigator_parent_resource_id') ==
                    resource['resource_id']]
        assert [item['native']['filename'] for item in children] == paths
        assert [item['display_name'] for item in children] == paths
        assert 'files' in resource['native']['property_sections']
        rollback()
        replay = {**observed['creation_values']}
        replay.pop('number')
        replay['filename'] += '_replay'
        replay['secondary_files'] = [
            {**record, 'filename': record['filename'] + '_replay'}
            for record in replay['secondary_files']]
        replay_number = 30000 if number == 32767 else number + 200
        apply('create', replay_number, **replay)
        connection.commit()
        replay_rows = catalog(replay_number)
        assert [row[1:] for row in replay_rows] == [row[1:] for row in rows]
        rollback()
        apply('drop', replay_number, preserve_files=False)
        connection.commit()
        apply('drop', number, preserve_files=preserve)
        rollback()
        assert len(catalog(number)) == len(paths)
        rollback()
        assert all(exists(filename) for filename in paths)
        removal = apply('drop', number, preserve_files=preserve)
        connection.commit()
        assert not catalog(number)
        rollback()
        # Close the attachment so native deferred file release has completed.
        connection.close()
        connection = connect()
        assert all(exists(filename) is preserve for filename in paths)
        assert not any(exists(filename + '_replay') for filename in paths)
        result['task_evidence']['visual_admin.shadow.create'] = {
            'statements': creation, 'live_execution': 'passed'}
        result['task_evidence']['visual_admin.shadow.drop'] = {
            'statements': removal, 'live_execution': 'passed'}
        if conditional:
            apply('drop', number + 100, preserve_files=False)
            connection.commit()
            connection.close()
            connection = connect()
            assert not exists(baseline)
        return {'catalog': rows, 'create_rollback': True,
                'drop_rollback': True, 'preserve_files': preserve,
                'filesystem_verified': True, 'metadata_replay_verified': True,
                'provider_catalog_verified': True}

    def native_denial(label, statement):
        try:
            sql(statement)
            connection.commit()
        except native.Error as error:
            result.setdefault('native_denials', []).append({
                'case': label,
                'native_status_codes': list(status_codes(error))})
            rollback()
        else:
            raise AssertionError('Invalid native shadow task was accepted')

    def duplicate_number():
        path = '/var/lib/firebird/data/owned_shadow_duplicate.shd'
        apply('create', 400, filename=path)
        connection.commit()
        before = catalog(400)
        rollback()
        native_denial('duplicate-number',
                      'CREATE SHADOW 400 AUTO ' + shadows.literal(path + '_x'))
        assert catalog(400) == before
        rollback()
        assert not exists(path + '_x')
        apply('drop', 400, preserve_files=False)
        connection.commit()

    def duplicate_paths():
        path = '/var/lib/firebird/data/owned_shadow_conflict.shd'
        apply('create', 401, filename=path)
        connection.commit()
        before = catalog(401)
        rollback()
        for label, conflict in [('same-database', database),
                                ('existing-shadow-file', path)]:
            native_denial(label, 'CREATE SHADOW 402 AUTO ' +
                          shadows.literal(conflict))
            assert not catalog(402)
            assert catalog(401) == before
            rollback()
        apply('drop', 401, preserve_files=False)
        connection.commit()

    def parser_boundaries():
        path = '/var/lib/firebird/data/owned_shadow_invalid.shd'
        for label, statement in (
                ('zero-number', 'CREATE SHADOW 0 ' + shadows.literal(path)),
                ('large-number', 'CREATE SHADOW 32768 ' +
                 shadows.literal(path)),
                ('negative-length', 'CREATE SHADOW 450 ' +
                 shadows.literal(path) + ' LENGTH -1'),
                ('large-length', 'CREATE SHADOW 450 ' + shadows.literal(path)
                 + ' LENGTH 2147483648'),
                ('missing-file-start', 'CREATE SHADOW 450 ' +
                 shadows.literal(path) + ' FILE ' +
                 shadows.literal(path + '_two'))):
            native_denial(label, statement)
            assert not catalog(450)
            rollback()
            assert not exists(path)

    def permissions():
        user = 'SHADOW_OPERATOR'
        user_password = secrets.token_urlsafe(24)
        path = '/var/lib/firebird/data/owned_shadow_permissions.shd'
        sql('CREATE USER ' + user + ' PASSWORD ' +
            shadows.literal(user_password))
        connection.commit()

        def as_user(statement, denied):
            attachment = native.connect(
                password=user_password, **_route_arguments({**route,
                                                            'user': user},
                                                           native))
            try:
                try:
                    with attachment.cursor() as cursor:
                        cursor.execute(statement)
                    attachment.commit()
                except native.Error as error:
                    if not denied:
                        raise
                    codes = list(status_codes(error))
                    assert 335544352 in codes
                    result.setdefault('permission_denials', []).append({
                        'operation': statement.split()[0].lower(),
                        'native_status_codes': codes})
                    attachment.rollback()
                else:
                    assert not denied
                    result.setdefault('permission_admissions', []).append(
                        statement.split()[0].lower())
            finally:
                attachment.close()

        create = 'CREATE SHADOW 460 AUTO ' + shadows.literal(path)
        drop = 'DROP SHADOW 460 DELETE FILE'
        as_user(create, True)
        sql('GRANT ALTER DATABASE TO USER ' + user)
        connection.commit()
        as_user(create, False)
        assert len(catalog(460)) == 1
        rollback()
        sql('REVOKE ALTER DATABASE FROM USER ' + user)
        connection.commit()
        as_user(drop, True)
        assert len(catalog(460)) == 1
        rollback()
        sql('GRANT ALTER DATABASE TO USER ' + user)
        connection.commit()
        as_user(drop, False)
        assert not catalog(460)
        rollback()
        sql('DROP USER ' + user)
        connection.commit()

    def database_storage_catalog():
        path = '/var/lib/firebird/data/owned_shadow_database_extra.fdb'
        difference = '/var/lib/firebird/data/owned_shadow_difference.delta'

        def files():
            resources = _resources(connection, {'route': route})
            for resource in resources:
                normalize_resource_properties(resource.get('native', {}))
            return [item for item in resources
                    if item['resource_kind'] == 'storage-file' and
                    item.get('native', {}).get('file_kind') != 'shadow']

        statement = 'ALTER DATABASE ADD FILE ' + shadows.literal(path) + (
            ' STARTING AT PAGE 8192 LENGTH 1024 PAGES')
        sql(statement)
        rollback()
        assert not exists(path)
        sql(statement)
        connection.commit()
        secondary = next(item for item in files()
                         if item['native']['filename'] == path)
        assert secondary['native']['file_kind'] == 'secondary-database'
        assert secondary['native']['start'] == 8192
        assert secondary['native']['length'] == 1024
        assert exists(path)
        rollback()
        sql('ALTER DATABASE BEGIN BACKUP')
        rollback()
        assert not any(item['native']['file_kind'] == 'difference'
                       for item in files())
        rollback()
        sql('ALTER DATABASE BEGIN BACKUP')
        connection.commit()
        try:
            default = next(item for item in files()
                           if item['native']['file_kind'] == 'difference')
            assert default['native']['filename'] is None
            assert default['native']['backup_active'] is True
            assert default['native']['filename_source'] == 'Firebird default'
            assert default['display_name'] != 'None'
        finally:
            rollback()
            sql('ALTER DATABASE END BACKUP')
            connection.commit()
        assert not any(item['native']['file_kind'] == 'difference'
                       for item in files())
        rollback()
        sql('ALTER DATABASE ADD DIFFERENCE FILE ' +
            shadows.literal(difference))
        connection.commit()
        named = next(item for item in files()
                     if item['native']['file_kind'] == 'difference')
        assert named['native']['filename'] == difference
        assert named['native']['backup_active'] is False
        rollback()
        sql('ALTER DATABASE DROP DIFFERENCE FILE')
        connection.commit()
        assert not any(item['native']['file_kind'] == 'difference'
                       for item in files())
        rollback()
        return {'secondary_file': secondary['native'],
                'default_difference': default['native'],
                'named_difference': named['native'],
                'backup_transition_verified': True,
                'rollback_verified': True,
                'workspace_normalization_verified': True}

    def trailing_space_filename():
        # Reproduce only on these owned shadow files, never the demo database.
        path = '/var/lib/firebird/data/owned_shadow_space.shd '
        apply('create', 470, filename=path)
        connection.commit()
        assert catalog(470)[0][0] == path
        resources = _resources(connection, {'route': route})
        file = next(item for item in resources if
                    item['resource_kind'] == 'storage-file' and
                    item.get('native', {}).get('filename') == path)
        assert file['display_name'] == path
        rollback()
        try:
            apply('drop', 470, preserve_files=False)
        except Exception as error:
            assert shadows.DELETE_WARNING in str(error)
        else:
            raise AssertionError('Unsafe native deletion was not blocked')
        assert catalog(470) and exists(path)
        rollback()
        apply('drop', 470, preserve_files=True)
        connection.commit()
        assert not catalog(470) and exists(path)
        rollback()
        canary = '/var/lib/firebird/data/owned_shadow_space_canary.fdb'
        unsafe = canary + ' '
        canary_connection = native.create_database(
            password=password, **_route_arguments(
                {**route, 'database': canary}, native))
        canary_connection.close()
        apply('create', 472, filename=unsafe)
        connection.commit()
        assert exists(canary) and exists(unsafe)
        # Intentionally bypass the product guard solely to observe the
        # underlying 5.0.4 defect on a disposable shadow and canary database.
        sql('DROP SHADOW 472 DELETE FILE')
        connection.commit()
        assert exists(unsafe) and not exists(canary)
        rollback()
        return {'exact_catalog_path': True,
                'provider_delete_blocked': True,
                'preserve_file_verified': True,
                'native_trimmed_path_deleted': True,
                'native_exact_path_retained': True,
                'scope': 'owned disposable shadow and canary database only'}

    try:
        container = docker(
            'create', '--name', 'cdeadmin-shadows-' + uuid.uuid4().hex[:16],
            '--label', 'cdeadmin-owned-gate=' + OWNER,
            '--publish', '127.0.0.1::3050', '--env', 'FIREBIRD_ROOT_PASSWORD',
            '--env', 'FIREBIRD_DATABASE', image,
            env=dict(os.environ, FIREBIRD_ROOT_PASSWORD=password,
                     FIREBIRD_DATABASE=database)).decode().strip()
        if not re.fullmatch('[0-9a-f]{64}', container):
            raise RuntimeError('Invalid owned container identity')
        result['container_id'] = container
        docker('start', container)
        route = {'host': '127.0.0.1', 'port': published_port(container),
                 'user': 'SYSDBA', 'database': database, 'timeout': 2,
                 'auth_plugin_list': 'Srp256',
                 'credential_reference_id': 'owned-shadow-secret',
                 'principal_reference': 'owned-shadow-principal'}
        phase = 'readiness'
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            try:
                connection = connect()
                break
            except native.Error:
                time.sleep(0.25)
        if connection is None:
            raise RuntimeError('Owned server readiness deadline exceeded')
        result['engine_version'] = sql(
            "SELECT RDB$GET_CONTEXT('SYSTEM', 'ENGINE_VERSION') "
            'FROM RDB$DATABASE')[0][0]
        assert result['engine_version'] == '5.0.4'
        rollback()
        client = _create_client(SimpleNamespace(
            acquire_secret=lambda *_args: SecretLease(password)))
        primary_files = [item for item in _resources(
            connection, {'route': route})
                         if item['resource_kind'] == 'storage-file' and
                         item['native'].get('file_kind') == 'primary-database']
        assert len(primary_files) == 1
        assert primary_files[0]['native']['filename'] == database
        rollback()
        number = 1
        for mode in ('AUTO', 'MANUAL'):
            for conditional in (False, True):
                for multiple in (False, True):
                    for preserve in (False, True):
                        label = f'{mode}-{conditional}-{multiple}-{preserve}'
                        try:
                            evidence = lifecycle(number, mode, conditional,
                                                 multiple, preserve)
                            result['checks'].append(
                                {'case': label, **evidence})
                        except Exception as error:
                            failure(label, error)
                        finally:
                            rollback()
                            number += 1
        for label, callback in (
                ('maximum-shadow-number', lambda: lifecycle(
                    32767, 'AUTO', False, False, False)),
                ('duplicate-number', duplicate_number),
                ('conflicting-server-paths', duplicate_paths),
                ('native-parser-boundaries', parser_boundaries),
                ('database-alter-permission', permissions)):
            try:
                callback()
                result['checks'].append({'case': label})
            except Exception as error:
                failure(label, error)
            finally:
                rollback()
        if not result['failures']:
            phase = 'trailing-space-filename-safety'
            result['filename_safety'] = trailing_space_filename()
        if not result['failures']:
            phase = 'database-storage-catalog'
            result['storage_catalog'] = database_storage_catalog()
        if browser_options is not None and not result['failures']:
            phase = 'owned-shadow-browser-matrix'
            result['browser_checks'] = browser_checks(
                browser_options, route, password, container, build_root,
                gate_kind='shadows',
                fixture_kind='firebird-shadows-qualification')
    except Exception as error:
        failure(phase, error)
    finally:
        if connection is not None:
            try:
                rollback()
                connection.close()
            except Exception as error:
                failure('close-attachment', error)
        if container is not None:
            try:
                remove_owned(container)
                result['owned_container_removed'] = True
            except Exception as error:
                failure('remove-owned-container', error)
        if client is not None:
            try:
                client.close()
            except Exception as error:
                failure('close-provider', error)
    result['complete'] = (len(result['checks']) == 21 and
                          not result['failures'] and
                          result['owned_container_removed'] and
                          all(item['passed'] for item in
                              result.get('browser_checks', [])))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', default='firebirdsql/firebird:5.0.4')
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
    result = run(options.image, options.build_root,
                 options if options.source_config_db else None)
    options.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result))
    raise SystemExit(0 if result['complete'] else 1)


if __name__ == '__main__':
    main()
