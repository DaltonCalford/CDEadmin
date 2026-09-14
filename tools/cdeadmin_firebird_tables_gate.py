#!/usr/bin/env python3
##########################################################################
# CDEadmin - Multi-engine Database Administration
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
##########################################################################

"""Qualify native table attributes on an owned disposable Firebird server.

Never changes the demo server or its ExternalFileAccess policy. The container,
database and external files belong exclusively to this run and are removed.
"""

import argparse
import json
import os
import secrets
import subprocess
import sys
import tempfile
import time
import traceback
import uuid
from pathlib import Path
from types import SimpleNamespace

if __package__:
    from .cdeadmin_firebird_admin_mapping_gate import (
        ADMINISTRATION, RelationalClientError, _create_client,
        _route_arguments,
    )
else:
    from cdeadmin_firebird_admin_mapping_gate import (
        ADMINISTRATION, RelationalClientError, _create_client,
        _route_arguments,
    )


def external_browser(route, options, result):
    """Exercise external-file forms without persisting test credentials."""
    root = options.output.parent / 'external-browser'
    root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='profiles-',
                                     dir=root) as directory:
        profiles = Path(directory) / 'profiles.json'
        descriptor = os.open(profiles, os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                             0o600)
        with os.fdopen(descriptor, 'w') as stream:
            json.dump({'profiles': [{'engine': 'firebird', **route}]}, stream)
        for scale, theme in (('100', 'default'), ('200', 'high-contrast')):
            evidence = root / ('scale-' + scale)
            command = [sys.executable,
                       str(Path(__file__).with_name(
                           'cdeadmin_firebird_ui_orchestrator.py')),
                       '--source-config-db', str(options.browser_config_db),
                       '--desktop-user', options.desktop_user,
                       '--database', route['database'],
                       '--firebird-port', str(route['port']),
                       '--client-library',
                       os.environ['CDEADMIN_FIREBIRD_CLIENT_LIBRARY'],
                       '--profiles', str(profiles), '--gate-kind', 'columns',
                       '--font-scale', scale, '--theme', theme,
                       '--timeout', '30']
            for key, filename in (
                    ('evidence-root', 'screenshots'),
                    ('summary-output', 'summary.json'),
                    ('manifest-output', 'manifest.json'),
                    ('output', 'result.json'), ('server-log', 'server.log'),
                    ('browser-log', 'browser.log')):
                command.extend(['--' + key, str(evidence / filename)])
            completed = subprocess.run(command, env=dict(
                os.environ, CDEADMIN_FIREBIRD_DEMO_PASSWORD=route['password'],
                CDEADMIN_FIREBIRD_COLUMNS_SCOPE='external'), check=False)
            result.setdefault('browser_runs', []).append({
                'scale': scale, 'exit_code': completed.returncode,
                'evidence': str(evidence)})
            if completed.returncode:
                result['failures'].append({'case': 'external-browser-' + scale,
                                           'exit_code': completed.returncode})
    result['browser_profiles_removed'] = True


def run(options=None):
    import firebird.driver as driver
    client = _create_client(SimpleNamespace(acquire_secret=None))
    name = 'cdeadmin-table-gate-' + uuid.uuid4().hex[:16]
    ownership = uuid.uuid4().hex
    password = secrets.token_urlsafe(30)
    environment = dict(os.environ, FIREBIRD_ROOT_PASSWORD=password)
    result = {'schema': 'cdeadmin.firebird-tables.v1', 'passed': False,
              'checks': [], 'failures': [], 'container': name,
              'container_removed': False, 'database_removed': False}
    connection = None

    def docker(*args, check=True):
        return subprocess.run(['docker', *args], env=environment, check=check,
                              capture_output=True, text=True, timeout=90)

    try:
        docker('run', '--detach', '--rm', '--name', name,
               '--label', 'cdeadmin.table-gate=' + ownership,
               '--publish', '127.0.0.1::3050',
               '--env', 'FIREBIRD_ROOT_PASSWORD',
               '--env', 'FIREBIRD_CONF_ExternalFileAccess=Restrict '
               '/var/lib/firebird/data', 'firebirdsql/firebird:5.0.4')
        address = docker('port', name, '3050/tcp').stdout.strip()
        port = int(address.split(':')[-1])
        route = {'host': '127.0.0.1', 'port': port, 'user': 'SYSDBA',
                 'password': password,
                 'database': '/var/lib/firebird/data/table_gate.fdb'}
        for attempt in range(90):
            try:
                connection = driver.create_database(
                    password=password, **_route_arguments(route, driver))
                break
            except driver.DatabaseError:
                if attempt == 89:
                    raise
                time.sleep(1)

        def execute(sql, parameters=(), session=None):
            with (session or connection).cursor() as cursor:
                cursor.execute(sql, parameters)

        def rows(sql, parameters=(), session=None):
            with (session or connection).cursor() as cursor:
                cursor.execute(sql, parameters)
                return cursor.fetchall()

        def state():
            return rows(
                'SELECT R.RDB$RELATION_TYPE, R.RDB$SQL_SECURITY, '
                'CASE WHEN EXISTS(SELECT 1 FROM RDB$PUBLICATION_TABLES P '
                "WHERE P.RDB$PUBLICATION_NAME = 'RDB$DEFAULT' AND "
                'P.RDB$TABLE_NAME = R.RDB$RELATION_NAME) THEN 1 ELSE 0 END '
                "FROM RDB$RELATIONS R WHERE R.RDB$RELATION_NAME = 'T'")

        def apply(operation, draft, session=None):
            plan = ADMINISTRATION.plan({
                '_provider_route': route, 'resource_kind': 'table',
                'operation_id': operation, 'draft': draft,
                'target_resource': {
                    'display_name': 'T', 'display_path': ['T']}})
            ADMINISTRATION.apply(client, plan,
                                 connection=session or connection)
            return [item['source'] for item in
                    plan['command_preview']['statements']]

        def create(draft):
            return apply('create', {
                'name': 'T', 'columns': [{'name': 'V', 'column_mode': 'STORED',
                                         'data_type': 'INTEGER'}], **draft})

        def clean_table():
            if connection.main_transaction.is_active():
                connection.rollback()
            if state():
                connection.commit()
                execute('DROP TABLE T')
            connection.commit()

        def case(label, callback):
            try:
                evidence = callback()
                result['checks'].append({'case': label, **(evidence or {})})
            except Exception:
                result['failures'].append({
                    'case': label, 'traceback': traceback.format_exc()})
            finally:
                clean_table()

        result['engine_version'] = rows(
            "SELECT RDB$GET_CONTEXT('SYSTEM', 'ENGINE_VERSION') "
            'FROM RDB$DATABASE')[0][0]
        assert result['engine_version'] == '5.0.4'
        connection.commit()

        def creation(draft, expected):
            sql = create(draft)
            connection.rollback()
            assert state() == [], 'CREATE rollback left native relation'
            connection.commit()
            create(draft)
            connection.commit()
            assert state() == [expected], (state(), expected)
            connection.commit()
            return {'statements': sql, 'metadata': expected,
                    'create_rollback_preserved': True}

        for kind in ('PERSISTENT', 'EXTERNAL', 'GLOBAL TEMPORARY'):
            for security, native_security in (
                    ('INHERIT', None), ('INVOKER', False), ('DEFINER', True)):
                variants = (('DELETE ROWS', 'PRESERVE ROWS') if
                            kind == 'GLOBAL TEMPORARY' else
                            ('DEFAULT', 'ENABLE', 'DISABLE'))
                for variant in variants:
                    draft = {'table_type': kind, 'sql_security': security}
                    if kind == 'GLOBAL TEMPORARY':
                        draft['on_commit'] = variant
                        native_type = 5 if variant == 'DELETE ROWS' else 4
                        published = 0
                    else:
                        native_type = 2 if kind == 'EXTERNAL' else 0
                        draft['publication'] = variant
                        published = 1 if variant == 'ENABLE' else 0
                    if kind == 'EXTERNAL':
                        draft['external_file'] = (
                            '/var/lib/firebird/data/' + uuid.uuid4().hex)
                    expected = (native_type, native_security, published)
                    case(f'create-{kind}-{security}-{variant}',
                         lambda d=draft, e=expected: creation(d, e))

        def publication_default(enabled, temporary=False):
            execute('ALTER DATABASE ' + (
                'INCLUDE ALL TO' if enabled else 'EXCLUDE ALL FROM') +
                ' PUBLICATION')
            connection.commit()
            draft = {'table_type': 'GLOBAL TEMPORARY' if temporary else
                     'PERSISTENT'}
            expected = (5 if temporary else 0, None, int(enabled))
            return creation(draft, expected)

        for enabled in (True, False):
            for temporary in (False, True):
                case(f'publication-policy-{enabled}-temporary-{temporary}',
                     lambda e=enabled, t=temporary: publication_default(e, t))

        def external_io():
            filename = '/var/lib/firebird/data/' + uuid.uuid4().hex
            create({'table_type': 'EXTERNAL', 'external_file': filename})
            connection.commit()
            execute('INSERT INTO T VALUES (42)')
            connection.rollback()
            assert rows('SELECT V FROM T') == [(42,)], (
                'Native external writes are not transactional')
            connection.commit()
            for statement in ('UPDATE T SET V = 43', 'DELETE FROM T'):
                try:
                    execute(statement)
                except driver.DatabaseError:
                    connection.rollback()
                else:
                    raise AssertionError('External mutation unexpectedly '
                                         'accepted: ' + statement)
                assert rows('SELECT V FROM T') == [(42,)]
                connection.commit()
            execute('DROP TABLE T')
            connection.commit()
            docker('exec', name, 'test', '-f', filename)
            create({'table_type': 'EXTERNAL', 'external_file': filename})
            connection.commit()
            assert rows('SELECT V FROM T') == [(42,)]
            connection.commit()
            return {'rollback_does_not_undo_external_insert': True,
                    'update_delete_rejected': True,
                    'drop_preserves_file_and_reattach_reads_it': True}

        case('external-file-native-io-semantics', external_io)

        def retention(mode):
            create({'table_type': 'GLOBAL TEMPORARY', 'on_commit': mode})
            connection.commit()
            execute('INSERT INTO T VALUES (1)')
            connection.commit()
            expected = [(1,)] if mode == 'PRESERVE ROWS' else []
            assert rows('SELECT V FROM T') == expected
            connection.commit()
            execute('INSERT INTO T VALUES (2)')
            connection.rollback()
            assert rows('SELECT V FROM T') == expected
            connection.commit()
            other = driver.connect(password=password,
                                   **_route_arguments(route, driver))
            try:
                assert rows('SELECT V FROM T', session=other) == []
                execute('INSERT INTO T VALUES (3)', session=other)
                other.commit()
                assert rows('SELECT V FROM T') == expected
                connection.commit()
            finally:
                other.close()
            return {'commit_retention': True, 'rollback_preserved': True,
                    'attachments_isolated': True}

        for mode in ('DELETE ROWS', 'PRESERVE ROWS'):
            case('temporary-retention-' + mode, lambda m=mode: retention(m))

        def alter(draft, expected, temporary=False):
            create({'table_type': 'GLOBAL TEMPORARY' if temporary else
                    'PERSISTENT', 'sql_security': 'DEFINER'})
            connection.commit()
            before = state()
            connection.commit()
            sql = apply('alter', draft)
            connection.rollback()
            assert state() == before
            connection.commit()
            apply('alter', draft)
            connection.commit()
            assert state() == [expected], (state(), expected)
            connection.commit()
            return {'statements': sql, 'metadata': expected,
                    'alter_rollback_preserved': True}

        for security, native_security in (
                ('INHERIT', None), ('INVOKER', False), ('DEFINER', True)):
            for publication in ('ENABLE', 'DISABLE'):
                for temporary in (False, True):
                    case(f'alter-{security}-{publication}-temp-{temporary}',
                         lambda s=security, p=publication, n=native_security,
                         t=temporary: alter(
                             {'sql_security': s, 'publication': p},
                             (5 if t else 0, n, int(p == 'ENABLE')), t))

        username = 'CDE_TABLE_' + uuid.uuid4().hex[:12].upper()
        user_password = secrets.token_urlsafe(30)
        execute('CREATE USER ' + username +
                " PASSWORD '" + user_password + "'")
        connection.commit()
        execute('CREATE TABLE SECRET_DATA (V INTEGER)')
        connection.commit()
        execute('INSERT INTO SECRET_DATA VALUES (73)')
        connection.commit()

        def security_runtime(mode, inherited, altering=False):
            execute('ALTER DATABASE SET DEFAULT SQL SECURITY ' + inherited)
            connection.commit()
            create({'sql_security': 'INVOKER' if altering else mode})
            connection.commit()
            if altering:
                apply('alter', {'sql_security': mode})
                connection.commit()
            execute('CREATE TRIGGER TR_T FOR T ACTIVE BEFORE INSERT '
                    'AS BEGIN SELECT V FROM SECRET_DATA INTO NEW.V; END')
            execute('GRANT INSERT ON T TO USER ' + username)
            connection.commit()
            other_route = {**route, 'user': username}
            other = driver.connect(password=user_password,
                                   **_route_arguments(other_route, driver))
            effective = inherited if mode == 'INHERIT' else mode
            try:
                try:
                    execute('INSERT INTO T VALUES (1)', session=other)
                    other.commit()
                except driver.DatabaseError:
                    other.rollback()
                    if effective == 'DEFINER':
                        raise
                else:
                    assert effective == 'DEFINER', (
                        'INVOKER unexpectedly read ungranted secret table')
                expected = [(73,)] if effective == 'DEFINER' else []
                assert rows('SELECT V FROM T') == expected
                connection.commit()
                before = state()
                connection.commit()
                for change in ({'sql_security': 'DEFINER'},
                               {'publication': 'ENABLE'}):
                    native_statement = (
                        'ALTER TABLE T ALTER SQL SECURITY DEFINER'
                        if 'sql_security' in change else
                        'ALTER TABLE T ENABLE PUBLICATION')
                    try:
                        execute(native_statement, session=other)
                    except driver.DatabaseError as error:
                        # DDL wraps no_priv in an unsuccessful metadata
                        # update (-607); inspect the native status vector.
                        assert 335544352 in error.gds_codes, (
                            'Native denial was not an authorization error',
                            error.gds_codes)
                        other.rollback()
                    else:
                        other.rollback()
                        raise AssertionError('Native non-owner ALTER accepted')
                    try:
                        apply('alter', change, other)
                    except RelationalClientError:
                        assert not other.main_transaction.is_active(), (
                            'Failed operation left a transaction active')
                    else:
                        other.rollback()
                        raise AssertionError(
                            'Non-owner altered table metadata')
                    assert state() == before
                    connection.commit()
            finally:
                other.close()
            return {'effective_trigger_security': effective,
                    'unprivileged_alter_rejected': True}

        for altering in (False, True):
            for mode in ('INHERIT', 'INVOKER', 'DEFINER'):
                for inherited in ('INVOKER', 'DEFINER'):
                    label = (f'security-runtime-{mode}-{inherited}'
                             f'-alter-{altering}')
                    case(label,
                         lambda m=mode, i=inherited, a=altering:
                         security_runtime(m, i, a))
        execute('DROP USER ' + username)
        connection.commit()
        connection.close()
        connection = driver.connect(
            password=password, **_route_arguments(route, driver))

        def external_attachments():
            evidence = []
            for publication in ('DEFAULT', 'ENABLE', 'DISABLE'):
                other = driver.connect(password=password,
                                       **_route_arguments(route, driver))
                try:
                    timeout_tpb = driver.tpb(driver.Isolation.SNAPSHOT,
                                             lock_timeout=5)
                    other.default_tpb = timeout_tpb
                    other.main_transaction.default_tpb = timeout_tpb
                    plan = ADMINISTRATION.plan({
                        '_provider_route': route, 'resource_kind': 'table',
                        'operation_id': 'create', 'draft': {
                            'name': 'T', 'table_type': 'EXTERNAL',
                            'external_file': '/var/lib/firebird/data/' +
                            uuid.uuid4().hex, 'publication': publication,
                            'columns': [{'name': 'V', 'column_mode': 'STORED',
                                         'data_type': 'INTEGER'}]}})
                    ADMINISTRATION.apply(client, plan, connection=other)
                    other.commit()
                finally:
                    other.close()
                assert state()[0][2] == int(publication == 'ENABLE')
                connection.commit()
                clean_table()
                evidence.append(publication)
            return {'independent_attachments': evidence}

        case('external-create-independent-attachments', external_attachments)
        if options is not None and options.browser_config_db:
            connection.drop_database()
            result['native_matrix_database_removed'] = True
            route['database'] = '/var/lib/firebird/data/browser_gate.fdb'
            connection = driver.create_database(
                password=password, **_route_arguments(route, driver))
            connection.close()
            connection = None
            external_browser(route, options, result)
            connection = driver.connect(
                password=password, **_route_arguments(route, driver))
    except Exception:
        result['failures'].append({'case': 'infrastructure',
                                   'traceback': traceback.format_exc()})
    finally:
        if connection is not None:
            try:
                if connection.main_transaction.is_active():
                    connection.rollback()
                connection.drop_database()
                result['database_removed'] = True
            except Exception:
                result['failures'].append({
                    'case': 'database-cleanup',
                    'traceback': traceback.format_exc()})
        try:
            owned = docker('inspect', '--format',
                           '{{ index .Config.Labels "cdeadmin.table-gate" }}',
                           name, check=False)
            if owned.returncode == 0 and owned.stdout.strip() == ownership:
                docker('rm', '--force', '--volumes', name)
                result['container_removed'] = True
            elif owned.returncode == 0:
                raise RuntimeError('Container ownership did not match')
            elif 'No such object' in owned.stderr:
                result['container_removed'] = True
            else:
                raise RuntimeError('Container absence could not be verified')
        except Exception:
            result['failures'].append({
                'case': 'container-cleanup',
                'traceback': traceback.format_exc()})
    result['passed'] = (not result['failures'] and
                        result['database_removed'] and
                        result['container_removed'])
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--browser-config-db', type=Path)
    parser.add_argument('--desktop-user')
    args = parser.parse_args()
    if args.browser_config_db and not args.desktop_user:
        parser.error('--browser-config-db requires --desktop-user')
    result = run(args)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({'passed': result['passed'],
                      'checks': len(result['checks']),
                      'failures': len(result['failures'])}))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
