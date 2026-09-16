#!/usr/bin/env python3
"""Qualify native view replacement in a labelled disposable Firebird 5.0.4."""
import argparse
import json
import os
from pathlib import Path
import re
import secrets
import time
import traceback
from types import SimpleNamespace
import uuid

if __package__:
    from .cdeadmin_firebird_logical_volumes_gate import (
        docker, published_port, remove_owned, OWNER, _route_arguments,
        _create_client, _configure_client_library, ADMINISTRATION, SecretLease,
    )
else:
    from cdeadmin_firebird_logical_volumes_gate import (
        docker, published_port, remove_owned, OWNER, _route_arguments,
        _create_client, _configure_client_library, ADMINISTRATION, SecretLease,
    )
from pgadmin.cdeadmin.providers.firebird.character_metadata import identifier
from pgadmin.cdeadmin.providers.firebird.error_diagnostics import status_codes
from pgadmin.cdeadmin.providers.firebird.provider import _resources


def run(image='firebirdsql/firebird:5.0.4'):
    import firebird.driver as native
    _configure_client_library(native)
    password = secrets.token_urlsafe(24)
    container = connection = client = None
    result = {'schema': 'cdeadmin.firebird-views.v1', 'complete': False,
              'checks': [], 'failures': [], 'task_evidence': {},
              'owned_container_removed': False}

    def failure(case, error):
        result['failures'].append({'case': case,
                                   'error_type': type(error).__name__,
                                   'message': str(error).replace(
                                       password, '<redacted>'),
                                   'frames': [{
                                       'function': frame.name,
                                       'line': frame.lineno,
                                   } for frame in traceback.extract_tb(
                                       error.__traceback__)],
                                   'native_status_codes': list(
                                       status_codes(error))})

    def sql(source, parameters=(), handle=None):
        with (handle or connection).cursor() as cursor:
            cursor.execute(source, parameters)
            return cursor.fetchall() if cursor.description else []

    def rollback():
        if connection.main_transaction.is_active():
            connection.rollback()

    def apply(operation, name, value, handle=None, columns=None):
        draft = {'definition': value,
                 'columns': columns if columns is not None else [
                     {'name': 'VALUE'}]}
        draft['name' if operation == 'create_or_alter' else 'confirmation'] = (
            name)
        request = {'resource_kind': 'view', 'operation_id': operation,
                   '_provider_route': route, 'draft': draft,
                   'target_resource': {'resource_kind': 'view',
                                       'display_name': name}}
        assert not ADMINISTRATION.validate(request)['errors']
        plan = ADMINISTRATION.plan(request)
        receipt = ADMINISTRATION.apply(
            client, plan, connection=handle or connection)
        assert receipt['staged_in_provider_session'] is True
        result['task_evidence']['visual_admin.view.' + operation] = {
            'statements': [s['source'] for s in
                           plan['command_preview']['statements']],
            'live_execution': 'passed'}

    def values(name):
        return sql('SELECT * FROM ' + identifier(name))

    def definition(name):
        return sql('SELECT RDB$VIEW_SOURCE FROM RDB$RELATIONS WHERE '
                   'RDB$RELATION_NAME = ?', (name,))[0][0]

    def lifecycle(name):
        q = identifier(name)
        apply('create_or_alter', name, 'SELECT 1 FROM RDB$DATABASE')
        assert 'SELECT 1' in definition(name)
        rollback()
        assert not sql('SELECT 1 FROM RDB$RELATIONS WHERE '
                       'RDB$RELATION_NAME = ?', (name,))
        rollback()
        apply('create_or_alter', name, 'SELECT 1 FROM RDB$DATABASE')
        connection.commit()
        sql('GRANT SELECT ON ' + q + ' TO PUBLIC')
        connection.commit()
        apply('create_or_alter', name, 'SELECT 2 FROM RDB$DATABASE')
        assert 'SELECT 2' in definition(name)
        rollback()
        assert values(name) == [(1,)]
        rollback()
        apply('create_or_alter', name, 'SELECT 2 FROM RDB$DATABASE')
        connection.commit()
        assert sql('SELECT RDB$PRIVILEGE FROM RDB$USER_PRIVILEGES WHERE '
                   "RDB$RELATION_NAME = ? AND RDB$USER = 'PUBLIC'", (name,))
        apply('recreate', name, 'SELECT 3 FROM RDB$DATABASE')
        assert 'SELECT 3' in definition(name)
        rollback()
        assert values(name) == [(2,)]
        rollback()
        apply('recreate', name, 'SELECT 3 FROM RDB$DATABASE')
        connection.commit()
        assert not sql('SELECT RDB$PRIVILEGE FROM RDB$USER_PRIVILEGES WHERE '
                       "RDB$RELATION_NAME = ? AND RDB$USER = 'PUBLIC'",
                       (name,))
        resource = next(item for item in _resources(connection, route)
                        if item['resource_kind'] == 'view' and
                        item['display_name'] == name)
        assert resource['native']['view_columns'] == [{'name': 'VALUE'}]
        assert values(name) == [(3,)]
        rollback()
        result['checks'].append({'case': 'lifecycle-' + name,
                                 'rollback_commit_verified': True,
                                 'grant_semantics_verified': True,
                                 'catalog_columns_verified': True})

    def dependency():
        sql('CREATE VIEW V_DEP AS SELECT * FROM V_BASE')
        connection.commit()
        try:
            apply('recreate', 'V_BASE', 'SELECT 9 FROM RDB$DATABASE')
            connection.commit()
        except Exception as error:
            codes = list(status_codes(error))
            assert 335544630 in codes, codes
            rollback()
            assert values('V_BASE') == values('V_DEP') == [(3,)]
            return {'native_status_codes': codes,
                    'original_and_dependent_preserved': True}
        raise AssertionError('Recreation ignored dependent view')

    def permission():
        from pgadmin.cdeadmin.providers.firebird.character_metadata import (
            literal,
        )
        sql('CREATE USER VIEW_READER PASSWORD ' + literal(password))
        connection.commit()
        reader = native.connect(password=password, **_route_arguments(
            {**route, 'user': 'VIEW_READER'}, native))
        denials = []
        try:
            for operation in ('create_or_alter', 'recreate'):
                try:
                    apply(operation, 'V_BASE', 'SELECT 4 FROM RDB$DATABASE',
                          handle=reader)
                    reader.commit()
                except Exception as error:
                    codes = list(status_codes(error))
                    assert 335544352 in codes, codes
                    denials.append({'operation': operation,
                                    'native_status_codes': codes})
                else:
                    raise AssertionError('Unprivileged mutation admitted')
                finally:
                    if reader.main_transaction.is_active():
                        reader.rollback()
        finally:
            reader.close()
            sql('DROP USER VIEW_READER')
            connection.commit()
        assert values('V_BASE') == [(3,)]
        return {'denials': denials, 'view_unchanged': True}

    def ordered_columns():
        columns = [{'name': 'B'}, {'name': 'A"東京'}] + [
            {'name': 'C' + str(i)} for i in range(2, 12)]
        for operation, value in (('create_or_alter', 1), ('recreate', 2)):
            apply(operation, 'V_ORDER',
                  f'WITH Q AS (SELECT {value} X FROM RDB$DATABASE) '
                  'SELECT ' + ', '.join('X + ' + str(i * 10)
                                        for i in range(12)) + ' FROM Q',
                  columns=columns)
            connection.commit()
            expected = [tuple(value + i * 10 for i in range(12))]
            assert values('V_ORDER') == expected
            resource = next(r for r in _resources(connection, route)
                            if r['resource_kind'] == 'view' and
                            r['display_name'] == 'V_ORDER')
            assert resource['native']['view_columns'] == columns
            ddl = resource['native']['ddl']
            assert '("B", "A""東京", "C2",' in ddl
            rollback()
            sql('DROP VIEW "V_ORDER"')
            connection.commit()
            sql(ddl)
            connection.commit()
            assert values('V_ORDER') == expected
            recreated = next(r for r in _resources(connection, route)
                             if r['resource_kind'] == 'view' and
                             r['display_name'] == 'V_ORDER')
            assert recreated['native']['view_columns'] == columns
            assert recreated['native']['ddl'] == ddl
            rollback()
        return {'ordered_columns_verified': True, 'cte_verified': True,
                'metadata_recreation_roundtrip_verified': True}

    def invalid_query():
        sql('CREATE TABLE PENDING_VIEW_WORK (ID INTEGER)')
        connection.commit()
        sql('INSERT INTO PENDING_VIEW_WORK VALUES (1)')
        try:
            apply('create_or_alter', 'V_BAD', 'SELECT FROM')
        except Exception as error:
            codes = list(status_codes(error))
            assert 335544569 in codes, codes
            assert sql('SELECT ID FROM PENDING_VIEW_WORK') == [(1,)]
            rollback()
            assert sql('SELECT ID FROM PENDING_VIEW_WORK') == []
            return {'native_status_codes': codes,
                    'pending_work_preserved': True, 'rollback_verified': True}
        raise AssertionError('Invalid query admitted')

    try:
        container = docker(
            'create', '--name', 'cdeadmin-views-' + uuid.uuid4().hex[:16],
            '--label', 'cdeadmin-owned-gate=' + OWNER, '--memory', '512m',
            '--publish', '127.0.0.1::3050', '--env', 'FIREBIRD_ROOT_PASSWORD',
            '--env', 'FIREBIRD_DATABASE', image,
            env=dict(os.environ, FIREBIRD_ROOT_PASSWORD=password,
                     FIREBIRD_DATABASE='owned_views.fdb')).decode().strip()
        if not re.fullmatch('[0-9a-f]{64}', container):
            raise RuntimeError('Invalid owned container identity')
        docker('start', container)
        route = {'host': '127.0.0.1', 'port': published_port(container),
                 'user': 'SYSDBA',
                 'database': '/var/lib/firebird/data/owned_views.fdb',
                 'timeout': 2, 'auth_plugin_list': 'Srp256',
                 'credential_reference_id': 'owned-view-secret',
                 'principal_reference': 'owned-view-principal'}
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
        for name in ('V_BASE', 'V"東京'):
            try:
                lifecycle(name)
            except Exception as error:
                failure('lifecycle-' + name, error)
            finally:
                rollback()
        for label, callback in (
                ('dependency-denial', dependency),
                ('permission-denials', permission),
                ('ordered-columns-cte', ordered_columns),
                ('invalid-query-pending-work', invalid_query)):
            try:
                result['checks'].append({'case': label, **callback()})
            except Exception as error:
                failure(label, error)
            finally:
                rollback()
    except Exception as error:
        failure('gate', error)
    finally:
        if connection is not None:
            try:
                rollback()
                connection.close()
            except Exception as error:
                failure('close-attachment', error)
        if client is not None:
            try:
                client.close()
            except Exception as error:
                failure('close-provider', error)
        if container is not None:
            try:
                remove_owned(container)
                result['owned_container_removed'] = True
            except Exception as error:
                failure('remove-owned-container', error)
    result['complete'] = (len(result['checks']) == 6 and
                          not result['failures'] and
                          result['owned_container_removed'])
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    options = parser.parse_args()
    if options.output.exists():
        raise SystemExit('Use a new evidence file')
    result = run()
    options.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({'complete': result['complete'],
                      'passed': len(result['checks']),
                      'failures': result['failures']}))
    return 0 if result['complete'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
