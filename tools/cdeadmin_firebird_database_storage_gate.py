#!/usr/bin/env python3
"""Qualify native storage tasks using separately owned disposable databases."""

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
from dataclasses import replace
from types import SimpleNamespace

if __package__:
    from .cdeadmin_firebird_external_functions_gate import (
        docker, published_port, remove_owned, OWNER, _route_arguments,
        _configure_client_library,
        _create_client, SecretLease, ADMINISTRATION, browser_checks,
    )
else:
    from cdeadmin_firebird_external_functions_gate import (
        docker, published_port, remove_owned, OWNER, _route_arguments,
        _configure_client_library,
        _create_client, SecretLease, ADMINISTRATION, browser_checks,
    )
from pgadmin.cdeadmin.providers.firebird import database_storage as storage
from pgadmin.cdeadmin.providers.firebird.error_diagnostics import status_codes
from pgadmin.cdeadmin.providers.relational_admin import (
    RelationalAdministration,
)
from pgadmin.cdeadmin.sdk.relational import RelationalClientError

# Only this isolated qualification instance admits the new operations until
# their full activation evidence is incorporated into the packaged contract.
ADMINISTRATION = RelationalAdministration(replace(
    ADMINISTRATION.dialect, supported={
        **ADMINISTRATION.dialect.supported,
        'database': ADMINISTRATION.dialect.supported['database'] |
        storage.OPERATIONS}))


def run(image, build_root, browser_options=None):
    import firebird.driver as native
    _configure_client_library(native)
    build_root.mkdir(parents=True, exist_ok=False)
    password = secrets.token_urlsafe(24)
    container = bootstrap = connection = client = None
    result = {'schema': 'cdeadmin.firebird-database-storage.v1',
              'executor': 'provider-native', 'complete': False,
              'checks': [], 'failures': [], 'task_evidence': {},
              'owned_container_removed': False,
              'credential_values_exported': False}
    phase = 'create-owned-server'

    def failure(label, error):
        result['failures'].append({
            'case': label, 'error_type': type(error).__name__,
            'native_status_codes': list(status_codes(error)),
            'frames': [{'file': Path(frame.filename).name,
                        'line': frame.lineno, 'function': frame.name}
                       for frame in traceback.extract_tb(
                           error.__traceback__)]})

    def sql(statement, parameters=()):
        with connection.cursor() as cursor:
            cursor.execute(statement, parameters)
            return cursor.fetchall() if cursor.description else []

    def rollback():
        if connection is not None and connection.main_transaction.is_active():
            connection.rollback()

    def task(operation, commit=True, **values):
        draft = {'confirmation': database, **values}
        if operation == 'begin_backup':
            draft['confirm_difference_overwrite'] = True
        request = {
            '_provider_route': {**route, 'database': database},
            'resource_kind': 'database', 'operation_id': operation,
            'draft': draft,
            'target_resource': {'resource_kind': 'database',
                                'display_name': database}}
        assert not ADMINISTRATION.validate(request)['errors']
        plan = ADMINISTRATION.plan(request)
        receipt = ADMINISTRATION.apply(client, plan, connection=connection)
        assert receipt['staged_in_provider_session'] is True
        statements = [item['source'] for item in
                      plan['command_preview']['statements']]
        if commit:
            connection.commit()
            result['task_evidence'][
                'visual_admin.database.' + operation] = {
                    'statements': statements, 'live_execution': 'passed'}
        return statements

    def files():
        return sql('SELECT RDB$FILE_NAME, RDB$FILE_SEQUENCE, RDB$FILE_START, '
                   'RDB$FILE_LENGTH, RDB$FILE_FLAGS FROM RDB$FILES '
                   'ORDER BY RDB$FILE_SEQUENCE')

    def exists(filename):
        if not filename.startswith(database):
            raise RuntimeError('File observation is outside the owned fixture')
        probe = subprocess.run(
            ['docker', 'exec', container, 'test', '-f', filename],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20,
            check=False)
        if probe.returncode not in (0, 1):
            raise RuntimeError('Owned file observation failed')
        return probe.returncode == 0

    def rejected(label, callback):
        try:
            callback()
        except (native.Error, RelationalClientError) as error:
            codes = list(status_codes(error))
            assert codes
            result.setdefault('native_denials', []).append({
                'case': label, 'native_status_codes': codes})
            rollback()
        else:
            raise AssertionError('Invalid native storage task was accepted')

    def file_layout(multiple, explicit):
        records = [{'filename': database + "_影's.extra",
                    **({'start': 8192, 'length': 4096} if explicit else {})}]
        if multiple:
            records.append({'filename': database + '_second.extra',
                            'start': 16384, 'length': 1024})
        task('add_files', commit=False, files=records)
        assert len(files()) == len(records)
        rollback()
        assert not files()
        rollback()
        assert all(not exists(item['filename']) for item in records)
        default_rejected = False
        if not explicit:
            try:
                task('add_files', files=records)
            except (native.Error, RelationalClientError) as error:
                assert 335545212 in status_codes(error)
                default_rejected = True
                rollback()
                records[0]['start'] = 8192
                task('add_files', files=records)
        else:
            task('add_files', files=records)
        observed = files()
        assert all(exists(item['filename']) for item in records)
        assert [row[0] for row in observed] == [
            item['filename'] for item in records]
        assert [row[1] for row in observed] == list(range(1, len(records) + 1))
        if explicit:
            assert observed[0][2] == 8192
        if multiple:
            assert observed[1][2:4] == (16384, 1024)
        rollback()
        rejected('duplicate-file-' + str(multiple) + '-' + str(explicit),
                 lambda: task('add_files', files=[records[0]]))
        assert files() == observed
        rollback()
        sql('CREATE TABLE STORAGE_MARKER (ID INTEGER)')
        connection.commit()
        sql('INSERT INTO STORAGE_MARKER VALUES (1)')
        connection.commit()
        assert sql('SELECT ID FROM STORAGE_MARKER') == [(1,)]
        rollback()
        return {'files': observed, 'rollback_verified': True,
                'duplicate_rejected': True, 'data_access_verified': True,
                'physical_files_verified': True,
                'native_default_start_rejected': default_rejected}

    def backup_cycle(named):
        path = database + ("_影's.delta" if named else '.delta')
        if named:
            task('add_difference_file', commit=False, filename=path)
            rollback()
            assert not files()
            rollback()
            task('add_difference_file', filename=path)
            assert files()[0][0] == path
            rollback()
            rejected('duplicate-difference', lambda: task(
                'add_difference_file', filename=path + '_other'))
        sql('CREATE TABLE STORAGE_MARKER (ID INTEGER)')
        connection.commit()
        sql('INSERT INTO STORAGE_MARKER VALUES (1)')
        connection.commit()
        task('begin_backup', commit=False)
        rollback()
        assert sql('SELECT MON$BACKUP_STATE FROM MON$DATABASE') == [(0,)]
        rollback()
        assert not exists(path)
        task('begin_backup')
        assert exists(path)
        assert sql('SELECT MON$BACKUP_STATE FROM MON$DATABASE') == [(1,)]
        during = files()
        assert len(during) == 1 and during[0][4] == 96
        assert during[0][0] == (path if named else None)
        rollback()
        rejected('already-in-backup-' + str(named),
                 lambda: task('begin_backup'))
        if named:
            rejected('remove-difference-during-backup',
                     lambda: task('drop_difference_file'))
            assert files() == during
            rollback()
        sql('INSERT INTO STORAGE_MARKER VALUES (2)')
        connection.commit()
        task('end_backup', commit=False)
        rollback()
        assert sql('SELECT MON$BACKUP_STATE FROM MON$DATABASE') == [(1,)]
        rollback()
        task('end_backup')
        assert not exists(path)
        assert sql('SELECT MON$BACKUP_STATE FROM MON$DATABASE') == [(0,)]
        assert sql('SELECT ID FROM STORAGE_MARKER ORDER BY ID') == [(1,), (2,)]
        after = files()
        assert (len(after) == 1 and after[0][4] == 32) if named else not after
        rollback()
        rejected('already-out-of-backup-' + str(named),
                 lambda: task('end_backup'))
        if named:
            task('drop_difference_file', commit=False)
            rollback()
            assert files()
            rollback()
            task('drop_difference_file')
            assert not files()
            rollback()
        rejected('missing-difference-' + str(named),
                 lambda: task('drop_difference_file'))
        return {'named': named, 'during': during, 'after': after,
                'mode_rollback_verified': True,
                'physical_delta_verified': True,
                'delta_changes_retained': True}

    def permissions():
        nonlocal connection
        user = 'STORAGE_OPERATOR'
        user_password = secrets.token_urlsafe(24)
        sql('CREATE USER ' + user + ' PASSWORD ' +
            storage.literal(user_password))
        connection.commit()
        admissions = []
        denials = []
        drafts = {
            'add_difference_file': {'filename': database + '.difference'},
            'begin_backup': {'confirm_difference_overwrite': True},
            'end_backup': {}, 'drop_difference_file': {},
            'add_files': {'files': [{'filename': database + '.extra',
                                     'start': 512}]},
        }
        try:
            for operation, draft in drafts.items():
                statements = storage.compile_operation(operation, {
                    'confirmation': database, **draft}, database)
                for permitted in (False, True):
                    sql(('GRANT' if permitted else 'REVOKE') +
                        ' ALTER DATABASE ' + ('TO' if permitted else 'FROM') +
                        ' USER ' + user)
                    connection.commit()
                    connection.close()
                    connection = None
                    attachment = native.connect(
                        password=user_password, **_route_arguments({
                            **route, 'database': database, 'user': user},
                            native))
                    try:
                        try:
                            with attachment.cursor() as cursor:
                                for statement in statements:
                                    cursor.execute(statement)
                            attachment.commit()
                        except native.Error as error:
                            if permitted:
                                failure('permission-admission-' + operation,
                                        error)
                                raise
                            codes = list(status_codes(error))
                            assert 335544352 in codes
                            denials.append({'operation': operation,
                                           'native_status_codes': codes})
                            attachment.rollback()
                        else:
                            assert permitted
                            admissions.append(operation)
                    finally:
                        attachment.close()
                        connection = native.connect(
                            password=password, **_route_arguments({
                                **route, 'database': database}, native))
                sql('REVOKE ALTER DATABASE FROM USER ' + user)
                connection.commit()
        finally:
            rollback()
            if connection is not None:
                try:
                    sql('DROP USER ' + user)
                    connection.commit()
                except Exception as error:
                    failure('remove-storage-test-user', error)
        return {'permission_admissions': admissions,
                'permission_denials': denials}

    def busy_file_extension():
        attachment = native.connect(
            password=password, **_route_arguments({
                **route, 'database': database}, native))
        try:
            # Materialize a separate attachment before requesting extension.
            with attachment.cursor() as cursor:
                cursor.execute('SELECT CURRENT_CONNECTION FROM RDB$DATABASE')
                assert cursor.fetchone()[0]
            attachment.commit()
            try:
                task('add_files', files=[{'filename': database + '.extra',
                                          'start': 8192}])
            except (native.Error, RelationalClientError) as error:
                codes = list(status_codes(error))
                assert 335544453 in codes
                rollback()
            else:
                raise AssertionError('Busy file extension was not rejected')
            assert not files() and not exists(database + '.extra')
            rollback()
            with attachment.cursor() as cursor:
                cursor.execute('SELECT CURRENT_CONNECTION FROM RDB$DATABASE')
                assert cursor.fetchone()[0]
            attachment.rollback()
        finally:
            attachment.close()
        task('add_files', files=[{'filename': database + '.extra',
                                  'start': 8192}])
        assert exists(database + '.extra')
        return {'native_status_codes': codes,
                'other_attachment_preserved': True,
                'explicit_maintenance_retry_passed': True}

    def difference_file_safety():
        extra = database + '.extra'
        task('add_files', files=[{'filename': extra, 'start': 8192}])
        sql('CREATE TABLE STORAGE_MARKER (ID INTEGER)')
        connection.commit()

        def guarded(operation, **draft):
            sql('INSERT INTO STORAGE_MARKER VALUES (1)')
            try:
                task(operation, **draft)
            except RelationalClientError as error:
                assert 'known database or shadow' in str(error)
            else:
                raise AssertionError('Known database file was not protected')
            assert sql('SELECT ID FROM STORAGE_MARKER') == [(1,)]
            rollback()
            assert not sql('SELECT ID FROM STORAGE_MARKER')
            rollback()

        guarded('add_difference_file', filename=extra)
        assert all(not (row[4] or 0) & 32 for row in files())
        rollback()
        # An existing unsafe configuration must also be caught at BEGIN time.
        # Defining this owned path does not perform the destructive BEGIN.
        sql('ALTER DATABASE ADD DIFFERENCE FILE ' + storage.literal(database))
        connection.commit()
        guarded('begin_backup')
        assert sql('SELECT MON$BACKUP_STATE FROM MON$DATABASE') == [(0,)]
        rollback()
        task('drop_difference_file')
        assert exists(database) and exists(extra)
        return {'known_secondary_protected': True,
                'existing_primary_collision_protected': True,
                'caller_pending_work_preserved': True,
                'caller_rollback_verified': True}

    def repeated_extension(backup):
        nonlocal connection
        first = [{'filename': database + '.one', 'start': 100000,
                  'length': 1000},
                 {'filename': database + '.two', 'start': 102000,
                  'length': 1000}]
        task('add_files', files=first)
        if backup:
            task('begin_backup')
            task('end_backup')
        connection.close()
        connection = native.connect(
            password=password, **_route_arguments({
                **route, 'database': database}, native))
        try:
            task('add_files', files=[
                {'filename': database + '.three', 'start': 200000,
                 'length': 1000},
                {'filename': database + '.four', 'start': 202000,
                 'length': 1000}])
        except (native.Error, RelationalClientError) as error:
            if not backup:
                raise
            codes = list(status_codes(error))
            assert 335545273 in codes
            rollback()
            return {'native_backup_extension_defect_reproduced': True,
                    'native_status_codes': codes,
                    'failure_retained_without_automatic_retry': True}
        if backup:
            raise AssertionError('Native defect was not reproduced')
        assert len(files()) == 4
        return {'reopened_before_extension': True, 'backup_cycle': backup}

    def backup_then_near_extension():
        task('begin_backup')
        task('end_backup')
        task('add_files', files=[
            {'filename': database + '.near', 'start': 512},
            {'filename': database + '.next', 'start': 1024}])
        assert len(files()) == 2
        return {'backup_before_file_extension': True,
                'starting_pages': [512, 1024]}

    try:
        bootstrap_path = '/var/lib/firebird/data/owned_storage_bootstrap.fdb'
        container = docker(
            'create', '--name', 'cdeadmin-storage-' + uuid.uuid4().hex[:16],
            '--label', 'cdeadmin-owned-gate=' + OWNER,
            '--publish', '127.0.0.1::3050', '--env', 'FIREBIRD_ROOT_PASSWORD',
            '--env', 'FIREBIRD_DATABASE', image,
            env=dict(os.environ, FIREBIRD_ROOT_PASSWORD=password,
                     FIREBIRD_DATABASE=bootstrap_path)).decode().strip()
        if not re.fullmatch('[0-9a-f]{64}', container):
            raise RuntimeError('Invalid owned container identity')
        result['container_id'] = container
        docker('start', container)
        route = {'host': '127.0.0.1', 'port': published_port(container),
                 'user': 'SYSDBA', 'database': bootstrap_path, 'timeout': 2,
                 'auth_plugin_list': 'Srp256'}
        phase = 'readiness'
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            try:
                bootstrap = native.connect(password=password,
                                           **_route_arguments(route, native))
                break
            except native.Error:
                time.sleep(0.25)
        if bootstrap is None:
            raise RuntimeError('Owned Firebird did not become ready')
        with bootstrap.cursor() as cursor:
            cursor.execute(
                "SELECT RDB$GET_CONTEXT('SYSTEM', 'ENGINE_VERSION') "
                'FROM RDB$DATABASE')
            result['engine_version'] = cursor.fetchone()[0]
        assert result['engine_version'] == '5.0.4'
        client = _create_client(SimpleNamespace(
            acquire_secret=lambda *_args: SecretLease(password)))
        bootstrap.close()
        bootstrap = None
        cases = [(f'files-{multiple}-{explicit}',
                  lambda multiple=multiple, explicit=explicit:
                  file_layout(multiple, explicit))
                 for multiple in (False, True)
                 for explicit in (False, True)]
        cases += [(f'backup-{named}', lambda named=named: backup_cycle(named))
                  for named in (False, True)]
        cases.append(('database-alter-permission', permissions))
        cases.append(('busy-file-extension', busy_file_extension))
        cases.append(('difference-file-safety', difference_file_safety))
        cases += [(f'repeated-extension-{backup}',
                   lambda backup=backup: repeated_extension(backup))
                  for backup in (False, True)]
        cases.append(('backup-then-near-extension',
                      backup_then_near_extension))
        for index, (label, callback) in enumerate(cases):
            database = f'/var/lib/firebird/data/owned_storage_{index}.fdb'
            removed = False
            try:
                connection = native.create_database(
                    password=password, **_route_arguments(
                        {**route, 'database': database}, native))
                evidence = callback()
                result['checks'].append({'case': label, **evidence})
            except Exception as error:
                failure(label, error)
            finally:
                if connection is not None:
                    try:
                        rollback()
                        connection.drop_database()
                        removed = True
                    except Exception as error:
                        failure(label + '-drop', error)
                        try:
                            connection.close()
                        except Exception as close_error:
                            failure(label + '-close', close_error)
                    connection = None
                result.setdefault('database_cleanup', []).append({
                    'case': label, 'removed': removed})
        if browser_options is not None and not result['failures']:
            phase = 'browser-forms'
            database = '/var/lib/firebird/data/owned_storage_browser.fdb'
            browser_route = {**route, 'database': database}
            result['browser_checks'] = []
            for scale in browser_options.font_scale or (100, 200, 300):
                connection = native.create_database(
                    password=password,
                    **_route_arguments(browser_route, native))
                connection.close()
                connection = None
                selected_options = SimpleNamespace(
                    **{**vars(browser_options), 'font_scale': [scale]})
                result['browser_checks'].extend(browser_checks(
                    selected_options, browser_route, password, container,
                    build_root, gate_kind='database-storage',
                    fixture_kind='firebird-storage-qualification'))
                connection = native.connect(
                    password=password,
                    **_route_arguments(browser_route, native))
                connection.drop_database()
                connection = None
            if not all(item['passed'] for item in result['browser_checks']):
                result['failures'].append({'case': 'browser-forms',
                                           'error_type': 'BrowserGateFailure'})
    except Exception as error:
        failure(phase, error)
    finally:
        if client is not None:
            try:
                client.close()
            except Exception as error:
                failure('close-provider', error)
        if bootstrap is not None:
            try:
                bootstrap.close()
            except Exception as error:
                failure('bootstrap-close', error)
        if container is not None:
            try:
                remove_owned(container)
                result['owned_container_removed'] = True
            except Exception as error:
                failure('remove-owned-container', error)
    result['complete'] = (len(result['checks']) == 12 and
                          not result['failures'] and
                          result['owned_container_removed'])
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', default='firebirdsql/firebird:5.0.4')
    parser.add_argument('--build-root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--browser', action='store_true')
    parser.add_argument('--source-config-db', type=Path,
                        default=Path('/var/lib/cdeadmin/cdeadmin.db'))
    parser.add_argument('--desktop-user', default='dalton.calford@gmail.com')
    parser.add_argument('--font-scale', type=int, action='append',
                        choices=(100, 200, 300))
    options = parser.parse_args()
    if options.output.exists():
        raise SystemExit('Use a new evidence file')
    result = run(options.image, options.build_root,
                 options if options.browser else None)
    options.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result))
    raise SystemExit(0 if result['complete'] else 1)


if __name__ == '__main__':
    main()
