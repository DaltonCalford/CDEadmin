#!/usr/bin/env python3
"""Qualify object-bound grants on an isolated, labelled Firebird instance."""

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
        _configure_client_library, _create_client, SecretLease,
        browser_checks,
    )
else:
    from cdeadmin_firebird_external_functions_gate import (
        docker, published_port, remove_owned, OWNER, _route_arguments,
        _configure_client_library, _create_client, SecretLease,
        browser_checks,
    )
from pgadmin.cdeadmin.providers.firebird import object_privileges as bound
from pgadmin.cdeadmin.providers.firebird.provider import (
    ADMINISTRATION as BASE, _resources,
)
from pgadmin.cdeadmin.providers.firebird.error_diagnostics import status_codes
from pgadmin.cdeadmin.providers.relational_admin import (
    RelationalAdministration,
)


ADMINISTRATION = RelationalAdministration(replace(
    BASE.dialect, supported={**BASE.dialect.supported, **{
        kind: BASE.dialect.supported[kind] | bound.OPERATIONS
        for kind in bound.KINDS}}))

FIXTURES = (
    'CREATE TABLE T (C INTEGER PRIMARY KEY, V INTEGER)',
    'INSERT INTO T VALUES (1, 7)',
    'CREATE VIEW VW AS SELECT C, V FROM T',
    'CREATE PROCEDURE P AS BEGIN END',
    'CREATE FUNCTION F RETURNS INTEGER AS BEGIN RETURN 1; END',
    'CREATE PACKAGE PK AS BEGIN PROCEDURE P; FUNCTION F RETURNS INTEGER; END',
    'CREATE PACKAGE BODY PK AS BEGIN PROCEDURE P AS BEGIN END '
    'FUNCTION F RETURNS INTEGER AS BEGIN RETURN 2; END END',
    'CREATE PACKAGE PK2 AS BEGIN PROCEDURE P; FUNCTION F RETURNS INTEGER; END',
    'CREATE PACKAGE BODY PK2 AS BEGIN PROCEDURE P AS BEGIN END '
    'FUNCTION F RETURNS INTEGER AS BEGIN RETURN 3; END END',
    'CREATE VIEW V_GLOBAL AS SELECT F() AS V FROM RDB$DATABASE',
    'CREATE VIEW V_PACKAGE AS SELECT PK.F() AS V FROM RDB$DATABASE',
    'CREATE VIEW V_PACKAGE2 AS SELECT PK2.F() AS V FROM RDB$DATABASE',
    'CREATE PROCEDURE CALL_GLOBAL AS BEGIN EXECUTE PROCEDURE P; END',
    'CREATE PROCEDURE CALL_PACKAGE AS BEGIN EXECUTE PROCEDURE PK.P; END',
    'CREATE PROCEDURE CALL_PACKAGE2 AS BEGIN EXECUTE PROCEDURE PK2.P; END',
    'CREATE SEQUENCE S', "CREATE EXCEPTION E 'owned expected exception'",
    'DECLARE EXTERNAL FUNCTION EF INTEGER RETURNS INTEGER BY VALUE '
    "ENTRY_POINT 'not_invoked' MODULE_NAME 'not_installed'",
    'CREATE TABLE "T""東京" ("C.with.dot" INTEGER PRIMARY KEY)',
)


def run(image, build_root, browser_options=None):
    import firebird.driver as native
    _configure_client_library(native)
    build_root.mkdir(parents=True, exist_ok=False)
    password = secrets.token_urlsafe(24)
    user_password = secrets.token_urlsafe(24)
    user = 'CDE_OBJECT_READER'
    database = '/var/lib/firebird/data/owned_object_privileges.fdb'
    container = connection = client = None
    result = {'schema': 'cdeadmin.firebird-object-privileges.v1',
              'complete': False, 'checks': [], 'failures': [],
              'permission_checks': [],
              'task_evidence': {}, 'owned_container_removed': False,
              'credential_values_exported': False}
    phase = 'create-owned-server'

    def sql(source, parameters=()):
        with connection.cursor() as cursor:
            cursor.execute(source, parameters)
            return cursor.fetchall() if cursor.description else []

    def failure(label, error):
        result['failures'].append({
            'case': label, 'error_type': type(error).__name__,
            'native_status_codes': list(status_codes(error)),
            'frames': [{'file': Path(frame.filename).name,
                        'line': frame.lineno, 'function': frame.name}
                       for frame in traceback.extract_tb(
                           error.__traceback__)]})

    def rollback():
        if connection.main_transaction.is_active():
            connection.rollback()

    def snapshot():
        rows = sql('SELECT RDB$RELATION_NAME, RDB$FIELD_NAME, '
                   'RDB$PRIVILEGE, RDB$GRANTOR, RDB$GRANT_OPTION, '
                   'RDB$USER_TYPE, RDB$OBJECT_TYPE FROM RDB$USER_PRIVILEGES '
                   'WHERE RDB$USER = ?', (user,))
        return {tuple(value.rstrip(' ') if isinstance(value, str) else value
                      for value in row) for row in rows}

    def apply(kind, operation, target, draft):
        request = {'_provider_route': route, 'resource_kind': kind,
                   'operation_id': operation, 'target_resource': target,
                   'draft': draft}
        assert not ADMINISTRATION.validate(request)['errors']
        plan = ADMINISTRATION.plan(request)
        receipt = ADMINISTRATION.apply(client, plan, connection=connection)
        assert receipt['staged_in_provider_session'] is True
        statements = [item['source'] for item in
                      plan['command_preview']['statements']]
        result['task_evidence'][f'visual_admin.{kind}.{operation}'] = {
            'statements': statements, 'live_execution': 'passed'}
        return statements

    def cycle(kind, name, values, package=None, column_list=False):
        label = kind + '-' + name + '-' + ','.join(values)
        if package:
            label += '-package-' + package
        if column_list:
            label += '-column-list'
        try:
            rollback()
            target = next(item for item in _resources(connection, {})
                          if item['resource_kind'] == kind and
                          item['display_name'] == name and
                          item.get('native', {}).get('package') == package)
            rollback()
            before = snapshot()
            rollback()
            draft = {'principal_kind': 'USER', 'principal': user,
                     'privileges': values}
            if column_list:
                draft.update(update_columns=[{'name': 'V'}],
                             reference_columns=[{'name': 'C'}])
            grant = apply(kind, 'grant', target, draft)
            granted = snapshot()
            assert granted != before
            observed = _resources(connection, {})
            native_grants = [item['native'] for item in observed if
                             item['resource_kind'] == 'privilege' and
                             item['native'].get('grantee') == user]
            assert native_grants
            assert all(item['target_resolution']['state'] == 'resolved'
                       for item in native_grants)
            if kind in {'function', 'procedure'}:
                for routine in observed:
                    if (routine['resource_kind'] != kind or
                            routine['display_name'] != name):
                        continue
                    detail = routine['native']
                    direct = [item for item in detail.get('privileges', [])
                              if item.get('grantee') == user]
                    inherited = [item for item in detail.get(
                        'package_privileges', {}).get('privileges', [])
                        if item.get('grantee') == user]
                    assert bool(direct) is (
                        package is None and not detail.get('package'))
                    assert bool(inherited) is (
                        package is not None and detail.get('package') ==
                        package)
            rollback()
            assert snapshot() == before
            rollback()
            apply(kind, 'grant', target, draft)
            connection.commit()
            assert snapshot() == granted
            rollback()
            revoke = dict(draft, confirmation=user)
            revoked_source = apply(kind, 'revoke', target, revoke)
            assert snapshot() == before
            rollback()
            assert snapshot() == granted
            rollback()
            apply(kind, 'revoke', target, revoke)
            connection.commit()
            assert snapshot() == before
            native_type, native_name, column = bound.target_identity(
                kind, target)
            delta = granted - before
            assert all(row[0] == native_name for row in delta)
            if column:
                assert all(row[1] == column for row in delta)
            result['checks'].append({
                'case': label, 'kind': kind, 'grant': grant,
                'revoke': revoked_source, 'native_type': native_type,
                'native_name': native_name, 'column': column,
                'native_privileges': sorted(delta, key=repr),
                'grant_rollback_verified': True,
                'revoke_rollback_verified': True,
                'native_target_resolution_verified': True,
                'committed_roundtrip_verified': True})
        except Exception as error:
            failure(label, error)
        finally:
            try:
                rollback()
            except Exception as error:
                failure('rollback-' + label, error)

    def access(label, source, expected):
        other = native.connect(password=user_password, **_route_arguments(
            {**route, 'user': user}, native))
        try:
            try:
                with other.cursor() as cursor:
                    cursor.execute(source)
                    if cursor.description:
                        cursor.fetchall()
                accepted, codes = True, []
            except native.DatabaseError as error:
                accepted, codes = False, list(status_codes(error))
            result['permission_checks'].append({
                'case': label, 'accepted': accepted,
                'native_status_codes': codes, 'fresh_attachment': True})
            assert accepted is expected
            if not expected:
                assert 335544352 in codes
        finally:
            try:
                if other.main_transaction.is_active():
                    other.rollback()
            finally:
                other.close()

    def permission_cycle(kind, name, privilege, source):
        label = 'effective-' + kind
        try:
            rollback()
            target = next(item for item in _resources(connection, {}) if
                          item['resource_kind'] == kind and
                          item['display_name'] == name and
                          not item.get('native', {}).get('package') and
                          (kind != 'column' or item['display_path'] ==
                           ['T', name]))
            rollback()
            value = {'principal_kind': 'USER', 'principal': user,
                     'privileges': [privilege]}
            access(label + '-before', source, False)
            apply(kind, 'grant', target, value)
            connection.commit()
            access(label + '-granted', source, True)
            apply(kind, 'revoke', target, dict(value, confirmation=user))
            connection.commit()
            access(label + '-revoked', source, False)
        except Exception as error:
            failure(label, error)
        finally:
            try:
                rollback()
            except Exception as error:
                failure('rollback-' + label, error)

    try:
        container = docker(
            'create', '--name',
            'cdeadmin-object-rights-' + uuid.uuid4().hex[:16],
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
                 'credential_reference_id': 'owned-object-rights-secret',
                 'principal_reference': 'owned-object-rights-principal'}
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
        phase = 'seed-owned-objects'
        for source in FIXTURES:
            sql(source)
            connection.commit()
        sql('CREATE USER ' + user + " PASSWORD '" + user_password + "'")
        connection.commit()
        phase = 'catalog-namespace-collisions'
        resources = _resources(connection, {})
        result['catalog_namespaces'] = []
        result['native_dependency_rows'] = sql(
            'SELECT TRIM(RDB$DEPENDENT_NAME), RDB$DEPENDENT_TYPE, '
            'TRIM(RDB$DEPENDED_ON_NAME), RDB$DEPENDED_ON_TYPE, '
            'TRIM(RDB$FIELD_NAME), TRIM(RDB$PACKAGE_NAME) '
            'FROM RDB$DEPENDENCIES WHERE RDB$DEPENDED_ON_NAME IN (?, ?)',
            ('F', 'P'))
        for kind, name, caller_prefix in (('function', 'F', 'V_'),
                                          ('procedure', 'P', 'CALL_')):
            routines = [item for item in resources if
                        item['resource_kind'] == kind and
                        item['display_name'] == name]
            try:
                assert len(routines) == 3
                assert len({item['resource_id'] for item in routines}) == 3
            except Exception as error:
                failure('namespace-identities-' + kind, error)
                continue
            for package, caller in ((None, caller_prefix + 'GLOBAL'),
                                    ('PK', caller_prefix + 'PACKAGE'),
                                    ('PK2', caller_prefix + 'PACKAGE2')):
                routine = next(item for item in routines if
                               item['native'].get('package') == package)
                dependencies = routine['native'].get('dependents', [])
                callers = ({resolved['display_name'] for item in dependencies
                            for resolved in item.get('dependent_resolution',
                                                     {}).get('resources', [])
                            if resolved['resource_kind'] == 'view'}
                           if kind == 'function' else {
                               item['object_name'] for item in dependencies})
                result['catalog_namespaces'].append({
                    'kind': kind, 'package': package, 'name': name,
                    'expected_caller': caller, 'observed_callers': sorted(
                        callers), 'authority_path': routine['authority_path']})
                try:
                    assert callers == {caller}
                    assert routine['authority_path'] == (
                        [package, kind, name] if package else [kind, name])
                except Exception as error:
                    failure('namespace-' + kind + '-' + str(package), error)
        result['catalog_namespaces_verified'] = not result['failures']
        rollback()
        for kind, name in (('table', 'T'), ('view', 'VW'), ('procedure', 'P'),
                           ('function', 'F'), ('package', 'PK'),
                           ('sequence', 'S'), ('exception', 'E'),
                           ('external-function', 'EF'), ('column', 'C')):
            for privilege in bound.allowed_privileges(kind):
                cycle(kind, name, [privilege])
        for kind, name in (('function', 'F'), ('procedure', 'P')):
            cycle(kind, name, ['EXECUTE'], package='PK')
            cycle(kind, name, ['EXECUTE'], package='PK2')
        for kind, name in (('table', 'T'), ('view', 'VW')):
            cycle(kind, name, ['UPDATE', 'REFERENCES'], column_list=True)
        cycle('table', 'T"東京', ['SELECT'])
        cycle('column', 'C.with.dot', ['UPDATE', 'REFERENCES'])
        for kind, name, privilege, source in (
                ('table', 'T', 'SELECT', 'SELECT C, V FROM T'),
                ('view', 'VW', 'SELECT', 'SELECT C, V FROM VW'),
                ('procedure', 'P', 'EXECUTE', 'EXECUTE PROCEDURE P'),
                ('function', 'F', 'EXECUTE', 'SELECT F() FROM RDB$DATABASE'),
                ('package', 'PK', 'EXECUTE', 'EXECUTE PROCEDURE PK.P'),
                ('sequence', 'S', 'USAGE', 'SELECT GEN_ID(S, 0) '
                 'FROM RDB$DATABASE'),
                ('column', 'C', 'UPDATE', 'UPDATE T SET C = 2')):
            permission_cycle(kind, name, privilege, source)
        if browser_options is not None and not result['failures']:
            phase = 'owned-browser-matrix'
            result['browser_checks'] = browser_checks(
                browser_options, route, password, container, build_root,
                gate_kind='object-privileges',
                fixture_kind='firebird-object-privileges-qualification')
    except Exception as error:
        failure(phase, error)
    finally:
        for name, item in (('attachment', connection), ('provider', client)):
            if item is not None:
                try:
                    item.close()
                except Exception as error:
                    failure('close-' + name, error)
        if container is not None:
            try:
                remove_owned(container)
                result['owned_container_removed'] = True
            except Exception as error:
                failure('remove-owned-container', error)
    expected = sum(len(bound.allowed_privileges(kind)) for kind in bound.KINDS)
    result['complete'] = (len(result['checks']) == expected + 8 and
                          len(result['permission_checks']) == 21 and
                          result.get('catalog_namespaces_verified') is True and
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
    options.output.parent.mkdir(parents=True, exist_ok=True)
    options.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({'complete': result['complete'],
                      'passed': len(result['checks']),
                      'failed': len(result['failures'])}))
    return 0 if result['complete'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
