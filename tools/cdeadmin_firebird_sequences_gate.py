#!/usr/bin/env python3
"""Qualify sequence semantics on an owned, disposable Firebird server."""

import argparse
import json
import os
import re
import secrets
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
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
from pgadmin.cdeadmin.providers.firebird import sequences
from pgadmin.cdeadmin.providers.firebird.error_diagnostics import status_codes
from pgadmin.cdeadmin.providers.firebird.provider import ADMINISTRATION
from pgadmin.cdeadmin.sdk.relational import RelationalClientError


def run(image, build_root, browser_options=None):
    import firebird.driver as native
    _configure_client_library(native)
    build_root.mkdir(parents=True, exist_ok=False)
    password = secrets.token_urlsafe(24)
    database = '/var/lib/firebird/data/owned_sequences.fdb'
    container = connection = client = None
    result = {'schema': 'cdeadmin.firebird-sequences.v1', 'complete': False,
              'checks': [], 'failures': [], 'task_evidence': {},
              'owned_container_removed': False,
              'credential_values_exported': False}
    phase = 'create-owned-server'

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

    def apply(operation, name, **draft):
        if operation in {'create', 'create_or_alter'}:
            draft['name'] = name
        if operation in {'drop', 'recreate', 'set_current'}:
            draft['confirmation'] = name
        request = {'_provider_route': route, 'resource_kind': 'sequence',
                   'operation_id': operation, 'draft': draft,
                   'target_resource': {'resource_kind': 'sequence',
                                       'display_name': name}}
        assert not ADMINISTRATION.validate(request)['errors']
        plan = ADMINISTRATION.plan(request)
        receipt = ADMINISTRATION.apply(client, plan, connection=connection)
        assert receipt['staged_in_provider_session'] is True
        statements = [item['source'] for item in
                      plan['command_preview']['statements']]
        result['task_evidence']['visual_admin.sequence.' + operation] = {
            'statements': statements, 'live_execution': 'passed'}

    def current(name):
        return sql('SELECT GEN_ID(' + sequences.identifier(name) +
                   ', 0) FROM RDB$DATABASE')[0][0]

    def consume(name):
        return sql('SELECT NEXT VALUE FOR ' + sequences.identifier(name) +
                   ' FROM RDB$DATABASE')[0][0]

    def catalog(name):
        rows = sql('SELECT RDB$INITIAL_VALUE, RDB$GENERATOR_INCREMENT, '
                   'RDB$DESCRIPTION FROM RDB$GENERATORS '
                   'WHERE RDB$GENERATOR_NAME = ?', (name,))
        return rows[0] if rows else None

    def lifecycle(name, step):
        apply('create', name, increment=step)
        assert catalog(name)[:2] == (1, step)
        rollback()
        assert catalog(name) is None
        rollback()
        apply('create', name, increment=step)
        connection.commit()
        assert consume(name) == 1
        rollback()
        assert current(name) == 1
        assert consume(name) == 1 + step
        rollback()
        # Sequence consumption is not undone by rolling back row work.
        assert current(name) == 1 + step
        apply('set_current', name, current='123')
        connection.commit()
        assert current(name) == 123
        assert consume(name) == 123 + step
        rollback()
        apply('alter', name, restart='123')
        connection.commit()
        assert consume(name) == 123
        rollback()
        apply('alter', name, restart_initial=True)
        connection.commit()
        assert consume(name) == 1
        rollback()
        apply('comment', name, description="Owner's 序列\nSecond line")
        rollback()
        assert catalog(name)[2] is None
        rollback()
        apply('comment', name, description="Owner's 序列\nSecond line")
        connection.commit()
        assert catalog(name)[2] == "Owner's 序列\nSecond line"
        rollback()
        apply('comment', name, description='')
        connection.commit()
        assert catalog(name)[2] is None
        rollback()
        apply('drop', name)
        rollback()
        assert catalog(name) is not None
        rollback()
        apply('drop', name)
        connection.commit()
        assert catalog(name) is None

    def exact_value(value):
        name = 'S_MIN' if value < 0 else 'S_MAX'
        step = 1 if value < 0 else -1
        apply('create', name, start=str(value), increment=step)
        connection.commit()
        assert consume(name) == value
        rollback()
        apply('set_current', name, current=str(value))
        connection.commit()
        assert current(name) == value
        assert consume(name) == value + step
        rollback()
        apply('alter', name, restart=str(value))
        connection.commit()
        assert consume(name) == value
        rollback()
        apply('drop', name)
        connection.commit()

    def replacement():
        name = 'S_REPLACE'
        apply('create_or_alter', name, start='10', increment=2)
        connection.commit()
        sql('GRANT USAGE ON SEQUENCE S_REPLACE TO PUBLIC')
        connection.commit()

        def granted():
            return sql('SELECT RDB$PRIVILEGE FROM RDB$USER_PRIVILEGES '
                       "WHERE RDB$RELATION_NAME = ? AND RDB$USER = 'PUBLIC' "
                       'AND RDB$OBJECT_TYPE = 14', (name,))

        apply('create_or_alter', name, start='50', increment=3)
        connection.commit()
        assert consume(name) == 50
        assert catalog(name)[:2] == (10, 3)
        assert granted()
        rollback()
        apply('create_or_alter', name, restart_initial=True)
        connection.commit()
        assert consume(name) == 10
        rollback()
        apply('recreate', name, start='70', increment=-2)
        rollback()
        assert catalog(name)[:2] == (10, 3)
        assert granted()
        rollback()
        apply('recreate', name, start='70', increment=-2)
        connection.commit()
        assert catalog(name)[:2] == (70, -2)
        assert consume(name) == 70
        assert not granted()
        rollback()
        apply('drop', name)
        connection.commit()

    def restart_rollback(operation):
        name = 'S_ROLLBACK_' + operation.upper()
        apply('create', name, start='10', increment=2)
        connection.commit()
        assert consume(name) == 10
        rollback()
        apply(operation, name, **({'current': '100'}
                                  if operation == 'set_current' else
                                  {'restart': '100'}))
        rollback()
        observed = current(name)
        observation = {
            'operation': operation, 'before': '10',
            'after_rollback': str(observed)}
        result.setdefault('rollback_observations', []).append(observation)
        assert observed == 10
        rollback()
        values = ({'current': '100'} if operation == 'set_current' else
                  {'restart': '100'})
        apply(operation, name, **values)
        local = 102 if operation == 'set_current' else 100
        assert consume(name) == local
        rollback()
        assert current(name) == 10
        rollback()
        observation['pending_consumption_rollback'] = True
        apply(operation, name, **values)
        assert consume(name) == local
        connection.commit()
        assert current(name) == local
        rollback()
        observation['pending_consumption_commit'] = str(local)
        apply('drop', name)
        connection.commit()

    def dependencies(operation):
        name = 'S_DEP_' + operation.upper()
        view = 'V_DEP_' + operation.upper()
        apply('create', name)
        connection.commit()
        sql('CREATE VIEW ' + view + ' AS SELECT NEXT VALUE FOR ' + name +
            ' AS V FROM RDB$DATABASE')
        connection.commit()
        evidence_key = 'visual_admin.sequence.' + operation
        previous_evidence = result['task_evidence'].get(evidence_key)
        try:
            apply(operation, name)
            connection.commit()
        except (native.Error, RelationalClientError) as error:
            codes = list(status_codes(error))
            assert 335544630 in codes
            result.setdefault('dependency_denials', []).append({
                'operation': operation, 'native_status_codes': codes})
            rollback()
        else:
            raise AssertionError('Dependent sequence unexpectedly removed')
        if previous_evidence is not None:
            result['task_evidence'][evidence_key] = previous_evidence
        assert sql('SELECT V FROM ' + view) == [(1,)]
        rollback()
        sql('DROP VIEW ' + view)
        connection.commit()
        apply('drop', name)
        connection.commit()

    def concurrency():
        name = 'S_CONCURRENT'
        apply('create', name)
        connection.commit()

        def worker(_index):
            attachment = native.connect(
                password=password, **_route_arguments(route, native))
            try:
                values = []
                with attachment.cursor() as cursor:
                    for _ in range(50):
                        cursor.execute('SELECT NEXT VALUE FOR S_CONCURRENT '
                                       'FROM RDB$DATABASE')
                        values.append(cursor.fetchone()[0])
                attachment.rollback()
                return values
            finally:
                attachment.close()

        with ThreadPoolExecutor(max_workers=4) as pool:
            groups = list(pool.map(worker, range(4)))
        values = [value for group in groups for value in group]
        assert sorted(values) == list(range(1, 201))
        assert current(name) == 200
        rollback()
        result['concurrent_consumption'] = {
            'attachments': 4, 'unique_values': 200,
            'rolled_back_consumption_retained': True}
        apply('drop', name)
        connection.commit()

    def parser_boundaries():
        for magnitude in ('-2147483648', '2147483648'):
            try:
                sql('CREATE SEQUENCE S_INVALID INCREMENT BY ' + magnitude)
            except native.Error as error:
                codes = list(status_codes(error))
                assert 335544569 in codes and 335544634 in codes
                result.setdefault('parser_denials', []).append({
                    'increment': magnitude, 'native_status_codes': codes})
                rollback()
            else:
                raise AssertionError('Invalid increment unexpectedly accepted')
        assert catalog('S_INVALID') is None

    def permissions():
        name = 'S_PERMISSIONS'
        user = 'SEQUENCE_CONSUMER'
        user_password = secrets.token_urlsafe(24)
        sql('CREATE USER ' + user + ' PASSWORD ' +
            sequences.literal(user_password))
        apply('create', name)
        connection.commit()
        sql('GRANT USAGE ON SEQUENCE ' + name + ' TO USER ' + user)
        connection.commit()
        attachment = native.connect(password=user_password,
                                    **_route_arguments({**route, 'user': user},
                                                       native))
        try:
            with attachment.cursor() as cursor:
                cursor.execute('SELECT NEXT VALUE FOR S_PERMISSIONS '
                               'FROM RDB$DATABASE')
                assert cursor.fetchone()[0] == 1
                attachment.rollback()
                for operation, draft in (
                        ('alter', {'restart': '30'}),
                        ('set_current', {'current': '30',
                                         'confirmation': name}),
                        ('drop', {'confirmation': name}),
                        ('recreate', {'confirmation': name})):
                    statement = sequences.compile_operation(operation, draft, {
                        'resource_kind': 'sequence', 'display_name': name})[0]
                    try:
                        cursor.execute(statement)
                        attachment.commit()
                    except native.Error as error:
                        codes = list(status_codes(error))
                        assert 335544352 in codes
                        result.setdefault('permission_denials', []).append({
                            'operation': operation,
                            'native_status_codes': codes})
                        attachment.rollback()
                    else:
                        raise AssertionError('USAGE allowed administration')
        finally:
            attachment.close()
        assert current(name) == 1
        rollback()
        apply('drop', name)
        sql('DROP USER ' + user)
        connection.commit()

    try:
        container = docker(
            'create', '--name', 'cdeadmin-sequences-' + uuid.uuid4().hex[:16],
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
                 'credential_reference_id': 'owned-sequence-secret',
                 'principal_reference': 'owned-sequence-principal'}
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
            raise RuntimeError('Owned server readiness deadline exceeded')
        result['engine_version'] = sql(
            "SELECT RDB$GET_CONTEXT('SYSTEM', 'ENGINE_VERSION') "
            'FROM RDB$DATABASE')[0][0]
        assert result['engine_version'] == '5.0.4'
        rollback()
        client = _create_client(SimpleNamespace(
            acquire_secret=lambda *_args: SecretLease(password)))
        cases = [
            *[(f'lifecycle-{step}-{quoted}',
               lambda step=step, quoted=quoted: lifecycle(
                   ('S"東京' if quoted else 'S_ASCII') + str(abs(step)), step))
              for step in (1, -1, -(2 ** 31) + 1, 2 ** 31 - 1)
              for quoted in (False, True)],
            ('int64-minimum', lambda: exact_value(sequences.MINIMUM)),
            ('int64-maximum', lambda: exact_value(sequences.MAXIMUM)),
            ('create-or-alter-versus-recreate', replacement),
            ('set-current-rollback', lambda: restart_rollback('set_current')),
            ('restart-next-rollback', lambda: restart_rollback('alter')),
            ('dependency-drop', lambda: dependencies('drop')),
            ('dependency-recreate', lambda: dependencies('recreate')),
            ('concurrent-consumers', concurrency),
            ('parser-increment-boundaries', parser_boundaries),
            ('usage-does-not-authorize-administration', permissions),
        ]
        for label, callback in cases:
            try:
                callback()
                result['checks'].append({'case': label})
            except Exception as error:
                failure(label, error)
            finally:
                rollback()
        if browser_options is not None and not result['failures']:
            phase = 'owned-sequence-browser-matrix'
            result['browser_checks'] = browser_checks(
                browser_options, route, password, container, build_root,
                gate_kind='sequences',
                fixture_kind='firebird-sequences-qualification')
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
    result['complete'] = (len(result['checks']) == 18 and
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
