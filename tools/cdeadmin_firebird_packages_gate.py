#!/usr/bin/env python3
"""Execute package lifecycle variants on a labelled, disposable Firebird."""

import argparse
import json
import os
import re
import secrets
import time
import traceback
import uuid
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

if __package__:
    from .cdeadmin_firebird_external_functions_gate import (
        docker, published_port, remove_owned, OWNER, _route_arguments,
        _configure_client_library, _create_client, SecretLease, browser_checks,
    )
else:
    from cdeadmin_firebird_external_functions_gate import (
        docker, published_port, remove_owned, OWNER, _route_arguments,
        _configure_client_library, _create_client, SecretLease, browser_checks,
    )
from pgadmin.cdeadmin.providers.firebird import packages
from pgadmin.cdeadmin.providers.firebird.error_diagnostics import status_codes
from pgadmin.cdeadmin.providers.firebird.provider import ADMINISTRATION as BASE
from pgadmin.cdeadmin.providers.relational_admin import (
    RelationalAdministration,
)
from pgadmin.cdeadmin.sdk.relational import RelationalClientError


ADMINISTRATION = RelationalAdministration(replace(
    BASE.dialect, supported={**BASE.dialect.supported,
                             'package': packages.OPERATIONS | {
                                 'grant', 'revoke'}}))


def run(image, build_root, browser_options=None):
    import firebird.driver as native
    _configure_client_library(native)
    build_root.mkdir(parents=True, exist_ok=False)
    password = secrets.token_urlsafe(24)
    database = '/var/lib/firebird/data/owned_packages.fdb'
    container = connection = client = None
    result = {'schema': 'cdeadmin.firebird-packages.v1', 'complete': False,
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
                        'line': frame.lineno,
                        'function': frame.name} for frame in
                       traceback.extract_tb(error.__traceback__)]})

    def apply(operation, draft, name):
        request = {'_provider_route': route, 'resource_kind': 'package',
                   'operation_id': operation, 'draft': draft,
                   'target_resource': {'resource_kind': 'package',
                                       'display_name': name}}
        assert not ADMINISTRATION.validate(request)['errors']
        plan = ADMINISTRATION.plan(request)
        receipt = ADMINISTRATION.apply(client, plan, connection=connection)
        assert receipt['staged_in_provider_session'] is True
        statements = [item['source'] for item in
                      plan['command_preview']['statements']]
        result['task_evidence']['visual_admin.package.' + operation] = {
            'statements': statements, 'live_execution': 'passed'}

    def catalog(name):
        rows = sql('SELECT RDB$PACKAGE_HEADER_SOURCE, '
                   'RDB$PACKAGE_BODY_SOURCE, '
                   'RDB$SQL_SECURITY, RDB$VALID_BODY_FLAG, RDB$DESCRIPTION '
                   'FROM RDB$PACKAGES WHERE RDB$PACKAGE_NAME = ?', (name,))
        return rows[0] if rows else None

    def lifecycle(name, security):
        header = 'BEGIN FUNCTION F RETURNS INTEGER; END'
        body = 'BEGIN FUNCTION F RETURNS INTEGER AS BEGIN RETURN 1; END END'
        values = {'name': name, 'header': header, 'sql_security': security}
        apply('create', values, name)
        assert catalog(name)[1] is None
        rollback()
        assert catalog(name) is None
        rollback()
        lifecycle_committed(name, security, header, body, values)

    def replacement_semantics():
        name = 'PK_UPSERT'
        quoted = packages.identifier(name)
        header = 'BEGIN FUNCTION F RETURNS INTEGER; END'
        body = 'BEGIN FUNCTION F RETURNS INTEGER AS BEGIN RETURN 7; END END'
        apply('create_or_alter', {'name': name, 'header': header}, name)
        connection.commit()
        apply('create_body', {'body': body}, name)
        connection.commit()
        sql('GRANT EXECUTE ON PACKAGE ' + quoted + ' TO PUBLIC')
        connection.commit()

        def public_grants():
            return sql('SELECT RDB$PRIVILEGE FROM RDB$USER_PRIVILEGES '
                       'WHERE RDB$RELATION_NAME = ? AND RDB$USER = ? '
                       'AND RDB$OBJECT_TYPE = 18', (name, 'PUBLIC'))

        apply('create_or_alter', {'name': name, 'header': header}, name)
        connection.commit()
        assert public_grants()
        assert not catalog(name)[3]
        rollback()
        apply('replace_body', {'body': body}, name)
        connection.commit()
        apply('recreate', {'confirmation': name, 'header': header,
                           'body': body.replace('RETURN 7', 'RETURN 8')}, name)
        assert not public_grants()
        rollback()
        assert public_grants()
        assert sql('SELECT ' + quoted + '.F() FROM RDB$DATABASE') == [(7,)]
        rollback()
        apply('recreate', {'confirmation': name, 'header': header,
                           'body': body.replace('RETURN 7', 'RETURN 8')}, name)
        connection.commit()
        assert not public_grants()
        assert sql('SELECT ' + quoted + '.F() FROM RDB$DATABASE') == [(8,)]
        rollback()
        apply('drop', {'confirmation': name}, name)
        connection.commit()

    def dependency_denial(operation):
        name = 'PK_DEP_' + operation.upper()
        view = 'VIEW_' + operation.upper()
        header = 'BEGIN FUNCTION F RETURNS INTEGER; END'
        body = 'BEGIN FUNCTION F RETURNS INTEGER AS BEGIN RETURN 9; END END'
        apply('create', {'name': name, 'header': header, 'body': body}, name)
        connection.commit()
        sql('CREATE VIEW ' + view + ' AS SELECT ' + name +
            '.F() AS V FROM RDB$DATABASE')
        connection.commit()
        try:
            apply(operation, {
                'alter': {'header': 'BEGIN END'},
                'drop': {'confirmation': name},
                'recreate': {'confirmation': name, 'header': 'BEGIN END'},
            }[operation], name)
            connection.commit()
        except native.DatabaseError as error:
            codes = list(status_codes(error))
            assert codes
            result.setdefault('native_denials', []).append({
                'operation': operation, 'native_status_codes': codes})
        else:
            raise AssertionError('Native dependency denial was expected')
        finally:
            rollback()
        assert sql('SELECT V FROM ' + view) == [(9,)]
        rollback()
        sql('DROP VIEW ' + view)
        connection.commit()
        apply('drop', {'confirmation': name}, name)
        connection.commit()

    def lifecycle_committed(name, security, header, body, values):
        apply('create', values, name)
        connection.commit()
        expected = {'INHERIT': None, 'INVOKER': False,
                    'DEFINER': True}[security]
        assert catalog(name)[2] == expected
        rollback()
        apply('create_body', {'body': body}, name)
        rollback()
        assert catalog(name)[1] is None
        rollback()
        apply('create_body', {'body': body}, name)
        connection.commit()
        expression = 'SELECT ' + packages.identifier(name) + \
                     '.F() FROM RDB$DATABASE'
        assert sql(expression) == [(1,)]
        rollback()
        replacement = body.replace('RETURN 1', 'RETURN 2')
        apply('replace_body', {'body': replacement}, name)
        rollback()
        assert sql(expression) == [(1,)]
        rollback()
        apply('replace_body', {'body': replacement}, name)
        connection.commit()
        assert sql(expression) == [(2,)]
        assert catalog(name)[2] == expected
        rollback()
        apply('alter', {'header': header, 'sql_security': 'INHERIT'}, name)
        assert catalog(name)[2] is None
        assert not catalog(name)[3]
        rollback()
        assert catalog(name)[2] == expected
        rollback()
        apply('alter', {'header': header, 'sql_security': 'INHERIT'}, name)
        connection.commit()
        assert catalog(name)[2] is None
        assert not catalog(name)[3]
        rollback()
        apply('replace_body', {'body': replacement}, name)
        connection.commit()
        assert catalog(name)[3]
        rollback()
        apply('comment', {'description': "Owner's 東京 notes"}, name)
        rollback()
        assert catalog(name)[4] is None
        rollback()
        apply('comment', {'description': "Owner's 東京 notes"}, name)
        connection.commit()
        assert catalog(name)[4] == "Owner's 東京 notes"
        rollback()
        apply('drop_body', {'confirmation': name}, name)
        rollback()
        assert sql(expression) == [(2,)]
        rollback()
        apply('drop_body', {'confirmation': name}, name)
        connection.commit()
        assert catalog(name)[1] is None
        assert catalog(name)[0]
        rollback()
        apply('create_body', {'body': body}, name)
        connection.commit()
        assert sql(expression) == [(1,)]
        rollback()
        apply('drop', {'confirmation': name}, name)
        rollback()
        assert sql(expression) == [(1,)]
        rollback()
        apply('drop', {'confirmation': name}, name)
        connection.commit()
        assert catalog(name) is None
        rollback()

    def failed_body_atomicity(borrowed):
        name = 'PK_FAILED_' + ('BORROWED' if borrowed else 'OWNED')
        pending = 'PENDING_' + ('BORROWED' if borrowed else 'OWNED')
        sql('CREATE TABLE ' + pending + ' (ID INTEGER)')
        connection.commit()
        request = {'resource_kind': 'package', 'operation_id': 'create',
                   '_provider_route': route, 'draft': {
                       'name': name, 'header':
                       'BEGIN FUNCTION F RETURNS INTEGER; END',
                       'body': 'BEGIN FUNCTION F RETURNS INTEGER AS '
                       'BEGIN RETURN ; END END'}}
        plan = ADMINISTRATION.plan(request)
        if not borrowed:
            # Prove this credential route can execute an independently owned
            # operation before expecting a syntax failure, not a login error.
            baseline = dict(request, draft={
                'name': name + '_BASE', 'header': 'BEGIN END'})
            receipt = ADMINISTRATION.apply(
                client, ADMINISTRATION.plan(baseline))
            assert receipt['accepted'] is True
            assert catalog(name + '_BASE') is not None
            rollback()
        sql('INSERT INTO ' + pending + ' VALUES (1)')
        try:
            ADMINISTRATION.apply(client, plan,
                                 connection=connection if borrowed else None)
        except RelationalClientError as error:
            codes = list(status_codes(error))
            assert 335544569 in codes
            assert not getattr(error, 'task_rollback_unconfirmed', False)
            result.setdefault('atomicity_checks', []).append({
                'borrowed': borrowed, 'native_status_codes': codes,
                'pending_work_preserved': True})
        else:
            raise AssertionError(
                'Invalid package body was unexpectedly accepted')
        assert catalog(name) is None
        assert sql('SELECT ID FROM ' + pending) == [(1,)]
        connection.commit()
        assert sql('SELECT ID FROM ' + pending) == [(1,)]
        rollback()
        sql('DROP TABLE ' + pending)
        connection.commit()
        if not borrowed:
            apply('drop', {'confirmation': name + '_BASE'}, name + '_BASE')
            connection.commit()

    try:
        container = docker(
            'create', '--name', 'cdeadmin-packages-' + uuid.uuid4().hex[:16],
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
                 'credential_reference_id': 'owned-package-secret',
                 'principal_reference': 'owned-package-principal'}
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
        for security in ('INHERIT', 'INVOKER', 'DEFINER'):
            for prefix in ('PK_', 'PK"東京_'):
                name = prefix + security
                label = 'lifecycle-' + name
                try:
                    lifecycle(name, security)
                    result['checks'].append({
                        'case': label, 'security': security,
                        'header_body_separation': True,
                        'rollback_commit_verified': True})
                except Exception as error:
                    failure(label, error)
                finally:
                    rollback()
        for label, callback in (
                ('create-or-alter-versus-recreate', replacement_semantics),
                ('failed-body-borrowed-task-savepoint',
                 lambda: failed_body_atomicity(True)),
                ('failed-body-owned-transaction',
                 lambda: failed_body_atomicity(False)),
                *[(f'dependency-denial-{operation}',
                   lambda action=operation: dependency_denial(action))
                  for operation in ('alter', 'drop', 'recreate')]):
            try:
                callback()
                result['checks'].append({'case': label})
            except Exception as error:
                failure(label, error)
            finally:
                rollback()
        if browser_options is not None and not result['failures']:
            phase = 'owned-package-browser-matrix'
            result['browser_checks'] = browser_checks(
                browser_options, route, password, container, build_root,
                gate_kind='packages',
                fixture_kind='firebird-packages-qualification')
    except Exception as error:
        failure(phase, error)
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
    result['complete'] = (len(result['checks']) == 12 and
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
    print(json.dumps({'complete': result['complete'],
                      'passed': len(result['checks']),
                      'failed': len(result['failures'])}))
    return 0 if result['complete'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
