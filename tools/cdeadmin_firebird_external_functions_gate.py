#!/usr/bin/env python3
"""Exercise provider-owned UDF tasks in a newly owned isolated Firebird."""

import argparse
import io
import json
import os
import re
import secrets
import subprocess
import sys
import tarfile
import time
import traceback
import uuid
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

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
from pgadmin.cdeadmin.providers.firebird.provider import _resources
from pgadmin.cdeadmin.providers.firebird.error_diagnostics import status_codes
from pgadmin.cdeadmin.providers.firebird import external_functions
from pgadmin.cdeadmin.providers.relational_admin import (
    RelationalAdministration,
)

# Qualify the new tasks before changing the packaged activation contract.
# This scoped administration instance cannot activate unqualified application
# sessions or alter other providers' task declarations.
ADMINISTRATION = RelationalAdministration(replace(
    ADMINISTRATION.dialect, supported={
        **ADMINISTRATION.dialect.supported,
        'external-function': external_functions.OPERATIONS}))


def archive_files(files):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode='w') as archive:
        for parent in sorted({str(Path(name).parent)
                             for name in files} - {'.'}):
            member = tarfile.TarInfo(parent)
            member.type = tarfile.DIRTYPE
            member.mode = 0o755
            archive.addfile(member)
        for name, content in files.items():
            member = tarfile.TarInfo(name)
            member.size = len(content)
            member.mode = 0o755 if name.endswith('.so') else 0o644
            archive.addfile(member, io.BytesIO(content))
    return buffer.getvalue()


def browser_checks(options, route, password, container, build_root, *,
                   gate_kind='external-functions',
                   fixture_kind='firebird-udf-qualification'):
    profile = {**route, 'engine': 'firebird', 'password': password,
               'fixture_kind': fixture_kind,
               'owned_container_id': container}
    private_profile = build_root / 'private-browser-profile.json'
    descriptor = os.open(private_profile, os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                         0o600)
    checks = []
    try:
        with os.fdopen(descriptor, 'w') as output:
            json.dump({'profiles': [profile]}, output)
        root = Path(__file__).resolve().parents[1]
        for scale in options.font_scale or (100, 200, 300):
            folder = build_root / ('browser-' + str(scale))
            folder.mkdir(exist_ok=False)
            command = [sys.executable, str(
                root / 'tools/cdeadmin_firebird_ui_orchestrator.py'),
                '--source-config-db', str(options.source_config_db),
                '--desktop-user', options.desktop_user,
                '--database', route['database'],
                '--firebird-port', str(route['port']),
                '--client-library',
                os.environ['CDEADMIN_FIREBIRD_CLIENT_LIBRARY'],
                '--profiles', str(private_profile),
                '--gate-kind', gate_kind,
                '--font-scale', str(scale), '--theme', 'high-contrast',
                '--timeout', '60', '--evidence-root',
                str(folder / 'screenshots')]
            for option, filename in (
                    ('summary-output', 'summary.json'),
                    ('manifest-output', 'manifest.csv'),
                    ('output', 'result.json'), ('server-log', 'server.log'),
                    ('browser-log', 'browser.log')):
                command.extend(['--' + option, str(folder / filename)])
            try:
                with (folder / 'orchestrator.log').open('w') as log:
                    process = subprocess.run(
                        command, cwd=root, env=dict(
                            os.environ,
                            CDEADMIN_FIREBIRD_DEMO_PASSWORD=password),
                        stdout=log, stderr=subprocess.STDOUT, check=False)
            except OSError as error:
                checks.append({'scale': scale, 'passed': False,
                               'evidence': str(folder),
                               'error_type': type(error).__name__})
                continue
            result_path = folder / 'result.json'
            observed = (json.loads(result_path.read_text())
                        if result_path.exists() else {})
            passed = (process.returncode == 0 and
                      observed.get('complete') is True and
                      observed.get('source_config_unchanged') is True and
                      observed.get('target_database') == route['database'])
            checks.append({'scale': scale, 'passed': passed,
                           'evidence': str(folder),
                           'source_config_unchanged': observed.get(
                               'source_config_unchanged')})
    finally:
        private_profile.unlink()
    return checks


def run(image, source_root, build_root, browser_options=None):
    import firebird.driver as native
    _configure_client_library(native)
    build_root.mkdir(parents=True, exist_ok=False)
    library = build_root / 'libcde_owned_udf.so'
    fixture = Path(__file__).resolve().parent / \
        'fixtures/firebird_owned_udf.cpp'
    password = secrets.token_urlsafe(24)
    database = '/var/lib/firebird/data/owned_udf.fdb'
    container = connection = client = None
    result = {'schema': 'cdeadmin.firebird-external-functions.v1',
              'complete': False, 'checks': [], 'failures': [],
              'task_evidence': {}, 'owned_container_removed': False,
              'credential_values_exported': False}
    phase = 'compile-owned-library'

    def rollback():
        if connection.main_transaction.is_active():
            connection.rollback()

    def sql(source, parameters=()):
        with connection.cursor() as cursor:
            cursor.execute(source, parameters)
            return cursor.fetchall() if cursor.description else []

    def apply(action, draft, name=None, kind='external-function'):
        request = {'_provider_route': route, 'resource_kind': kind,
                   'operation_id': action, 'draft': draft,
                   'target_resource': ({'display_name': name,
                                        'display_path': [name]}
                                       if name is not None else None)}
        assert not ADMINISTRATION.validate(request)['errors']
        plan = ADMINISTRATION.plan(request)
        ADMINISTRATION.apply(client, plan, connection=connection)
        statements = [item['source'] for item in
                      plan['command_preview']['statements']]
        result['task_evidence'][f'visual_admin.{kind}.{action}'] = {
            'statements': statements, 'live_execution': 'passed'}
        return statements

    def definition(name, **values):
        return {'name': name, 'arguments': [{'data_type': 'INTEGER'}],
                'return_data_type': 'INTEGER', 'return_mechanism': 'VALUE',
                'entrypoint': 'owned_value', 'module_name': 'cde_owned_udf',
                **values}

    def catalog(name):
        return next(item['native'] for item in _resources(connection, {})
                    if item['resource_kind'] == 'external-function' and
                    item['display_name'] == name)

    def failure(case, error):
        result['failures'].append({
            'case': case, 'error_type': type(error).__name__,
            'native_status_codes': list(status_codes(error)),
            'frames': [{'file': Path(frame.filename).name,
                        'line': frame.lineno, 'function': frame.name}
                       for frame in traceback.extract_tb(
                           error.__traceback__)]})

    def case(name, operation):
        try:
            rollback()
            observation = operation()
            result['checks'].append({'case': name, **(observation or {})})
        except Exception as error:
            failure(name, error)
        finally:
            rollback()

    try:
        with (build_root / 'compiler.log').open('w') as log:
            subprocess.run(['g++', '-std=c++11', '-shared', '-fPIC', '-Wall',
                            '-Wextra', '-Werror', '-I',
                            str(source_root / 'src/include'), str(fixture),
                            '-o', str(library)], stdout=log,
                           stderr=subprocess.STDOUT, check=True, timeout=60)
        phase = 'create-owned-container'
        environment = dict(os.environ, FIREBIRD_ROOT_PASSWORD=password,
                           FIREBIRD_DATABASE=database)
        container = docker(
            'create', '--name', 'cdeadmin-udf-' + uuid.uuid4().hex[:16],
            '--label', 'cdeadmin-owned-gate=' + OWNER,
            '--publish', '127.0.0.1::3050', '--env', 'FIREBIRD_ROOT_PASSWORD',
            '--env', 'FIREBIRD_DATABASE', image,
            env=environment).decode().strip()
        if not re.fullmatch('[0-9a-f]{64}', container):
            raise RuntimeError('Invalid owned container identity')
        result['container_id'] = container
        phase = 'owned-library-configuration'
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
                   'firebird.conf': configuration.encode()}))
        contents = library.read_bytes()
        docker('cp', '-', container + ':/opt/firebird/',
               input_data=archive_files({
                   'UDF/libcde_owned_udf.so': contents,
                   'UDF/libcde_owned_udf_alias.so': contents}))
        docker('start', container)
        route = {'host': '127.0.0.1', 'port': published_port(container),
                 'user': 'SYSDBA', 'database': database, 'timeout': 2,
                 'auth_plugin_list': 'Srp256',
                 'credential_reference_id': 'owned-udf-secret',
                 'principal_reference': 'owned-udf-principal'}
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
        connection.rollback()
        client = _create_client(SimpleNamespace(
            acquire_secret=lambda *_args: SecretLease(password)))

        def lifecycle():
            name = 'OWNED_東京'
            values = definition(name, description="Owned é ' comment\n  ")
            apply('create', values)
            rollback()
            assert not sql('SELECT 1 FROM RDB$FUNCTIONS '
                           'WHERE RDB$FUNCTION_NAME = ?', (name,))
            rollback()
            apply('create', values)
            connection.commit()
            assert sql('SELECT "OWNED_東京"(5) FROM RDB$DATABASE') == [(12,)]
            metadata = catalog(name)
            assert metadata['description'] == values['description']
            assert metadata['parameters']
            assert 'privileges' in metadata['property_sections']
            rollback()
            apply('comment', {'description': 'Changed'}, name)
            rollback()
            assert catalog(name)['description'] == values['description']
            rollback()
            apply('comment', {'description': ''}, name)
            connection.commit()
            assert catalog(name)['description'] is None
            rollback()
            for selection, changes, expected in (
                    ('ENTRY_POINT', {'entrypoint': 'owned_other'}, 16),
                    ('MODULE_NAME', {
                     'module_name': 'cde_owned_udf_alias'}, 16),
                    ('BOTH', {'entrypoint': 'owned_value',
                              'module_name': 'cde_owned_udf'}, 12)):
                apply('alter', {'alter_target': selection, **changes}, name)
                connection.commit()
                assert sql('SELECT "OWNED_東京"(5) FROM RDB$DATABASE') == [
                    (expected,)]
                rollback()
            apply('drop', {'confirmation': name}, name)
            rollback()
            assert sql('SELECT "OWNED_東京"(5) FROM RDB$DATABASE') == [(12,)]
            rollback()
            apply('drop', {'confirmation': name}, name)
            connection.commit()
            for statement in metadata['recreation_statements']:
                sql(statement)
            connection.commit()
            assert sql('SELECT "OWNED_東京"(5) FROM RDB$DATABASE') == [(12,)]
            assert catalog(name)['description'] == values['description']
            return {'native_value': 12, 'recreation': metadata['ddl']}

        case('create-alter-comment-drop-rollback-recreation', lifecycle)
        for name, entrypoint, arguments, returned, expression, expected in (
                ('REFERENCE', 'owned_reference', [{'data_type': 'INTEGER'}],
                 {'return_data_type': 'INTEGER',
                  'return_mechanism': 'REFERENCE'}, '5', 22),
                ('NULL', 'owned_null', [
                    {'data_type': 'INTEGER', 'mechanism': 'NULL'}],
                 {}, 'NULL', -99),
                ('DESCRIPTOR', 'owned_descriptor', [
                    {'data_type': 'INTEGER', 'mechanism': 'DESCRIPTOR'}],
                 {}, '5', 24),
                ('RETURN_DESCRIPTOR', 'owned_return_descriptor',
                 [{'data_type': 'INTEGER'}],
                 {'return_mechanism': 'DESCRIPTOR'}, '5', 28),
                ('FREE_IT', 'owned_free', [{'data_type': 'INTEGER'}],
                 {'return_mechanism': 'FREE_IT'}, '5', 36),
                ('DESCRIPTOR_FREE_IT', 'owned_free_descriptor',
                 [{'data_type': 'INTEGER'}],
                 {'return_mechanism': 'DESCRIPTOR_FREE_IT'}, '5', 36),
                ('PARAMETER', 'owned_parameter_descriptor',
                 [{'data_type': 'INTEGER'}, {'data_type': 'INTEGER',
                                             'mechanism': 'DESCRIPTOR'}],
                 {'return_mode': 'PARAMETER', 'return_parameter': 2}, '5', 34),
                ('PARAMETER_REFERENCE_NATIVE_LIMIT', 'owned_parameter',
                 [{'data_type': 'INTEGER'}] * 2,
                 {'return_mode': 'PARAMETER', 'return_parameter': 2}, '5', 0),
                ('CSTRING', 'owned_string', [{'data_type': 'CSTRING',
                                              'length': 128}],
                 {'return_data_type': 'CSTRING', 'return_length': 128,
                  'return_mechanism': 'REFERENCE'}, "'hello'", 'hello!')):
            def execute(name=name, entrypoint=entrypoint, arguments=arguments,
                        returned=returned, expression=expression,
                        expected=expected):
                values = definition('OWNED_' + name, entrypoint=entrypoint,
                                    arguments=arguments, **returned)
                if returned.get('return_mode') == 'PARAMETER':
                    values.pop('return_data_type')
                    values.pop('return_mechanism')
                apply('create', values)
                connection.commit()
                observed = sql('SELECT "OWNED_' + name + '"(' + expression +
                               ') FROM RDB$DATABASE')[0][0]
                assert observed == expected
                return {'native_value': observed}
            case('native-mechanism-' + name, execute)

        def scalar_array():
            sql('CREATE TABLE OWNED_ARRAYS (A INTEGER[0:2])')
            connection.commit()
            sql('INSERT INTO OWNED_ARRAYS VALUES (?)', ([2, 3, 5],))
            connection.commit()
            apply('create', definition(
                'OWNED_ARRAY', entrypoint='owned_array',
                arguments=[{'data_type': 'INTEGER',
                            'mechanism': 'SCALAR_ARRAY'}]))
            connection.commit()
            assert sql('SELECT OWNED_ARRAY(A) FROM OWNED_ARRAYS') == [(10,)]
            return {'native_value': 10, 'array_bounds': [0, 2]}
        case('native-mechanism-SCALAR_ARRAY', scalar_array)

        for count, entrypoint, expected in ((0, 'owned_zero', 47),
                                            (15, 'owned_fifteen', 15)):
            def boundary(count=count, entrypoint=entrypoint,
                         expected=expected):
                name = 'OWNED_BOUND_' + str(count)
                apply('create', definition(name, entrypoint=entrypoint,
                                           arguments=[{'data_type': 'INTEGER'}]
                                           * count))
                connection.commit()
                observed = sql('SELECT "' + name + '"(' + ','.join(
                    ['1'] * count) + ') FROM RDB$DATABASE')[0][0]
                assert observed == expected
                return {'argument_count': count, 'native_value': observed}
            case('argument-boundary-' + str(count), boundary)

        def blob_boundary():
            name = 'OWNED_BLOB_RETURN'
            apply('create', definition(
                name, arguments=[{'data_type': 'INTEGER'}] * 14,
                return_data_type='BLOB', return_mechanism='REFERENCE'))
            connection.commit()
            observed = catalog(name)
            assert str(observed['return_argument']) == '15'
            rollback()
            apply('drop', {'confirmation': name}, name)
            connection.commit()
            for statement in observed['recreation_statements']:
                sql(statement)
            connection.commit()
            assert catalog(name)['parameters'] == observed['parameters']
            return {'input_count': 14, 'return_position': 15,
                    'native_execution': 'declaration-only-ABI-not-invoked'}
        case('blob-return-argument-boundary', blob_boundary)

        for attribute in ('module_name', 'entrypoint'):
            def missing(attribute=attribute):
                name = 'OWNED_MISSING_' + attribute.upper()
                apply('create', definition(
                    name, **{attribute: 'owned_absent'}))
                connection.commit()
                try:
                    sql('SELECT "' + name + '"(5) FROM RDB$DATABASE')
                except Exception as error:
                    codes = list(status_codes(error))
                    assert codes
                    return {'native_status_codes': codes,
                            'installed_prerequisite_required': attribute}
                raise AssertionError('Missing native prerequisite was ignored')
            case('missing-' + attribute, missing)

        declarations = []
        for type_name in external_functions.TYPES:
            value = {'data_type': type_name}
            if type_name in ('CHAR', 'VARCHAR', 'NCHAR', 'NCHAR VARYING',
                             'BINARY', 'VARBINARY', 'CSTRING'):
                value['length'] = 32
            if type_name in ('NUMERIC', 'DECIMAL'):
                value.update(precision=38, scale=12)
            declarations.append(value)
        declarations.extend([
            {'data_type': 'TIME', 'time_zone': 'WITH TIME ZONE'},
            {'data_type': 'TIMESTAMP', 'time_zone': 'WITH TIME ZONE'},
            {'data_type': 'CSTRING', 'length': 32, 'character_set': 'UTF8'},
            {'data_type': 'INTEGER', 'mechanism': 'SCALAR_ARRAY'},
        ])
        for index, declaration in enumerate(declarations):
            def declare(index=index, declaration=declaration):
                name = 'OWNED_DECL_' + str(index)
                apply('create', definition(name, arguments=[declaration]))
                connection.commit()
                observed = catalog(name)
                ddl = observed['ddl']
                assert ddl.startswith('DECLARE EXTERNAL FUNCTION')
                rollback()
                apply('drop', {'confirmation': name}, name)
                connection.commit()
                sql(ddl.rstrip(';'))
                connection.commit()
                recreated = catalog(name)
                for key in ('parameters', 'return_argument', 'module_name',
                            'entrypoint'):
                    assert recreated[key] == observed[key], key
                return {'declaration': declaration, 'recreation': ddl,
                        'native_execution': 'declaration-only-ABI-not-invoked'}
            case('declaration-recreation-' + str(index), declare)

        def dependency():
            name = 'OWNED_DEPENDENT'
            apply('create', definition(name))
            connection.commit()
            sql('CREATE VIEW OWNED_UDF_VIEW AS SELECT '
                'OWNED_DEPENDENT(5) AS N FROM RDB$DATABASE')
            connection.commit()
            metadata = catalog(name)
            result['dependency_observation'] = {
                'native_rows': sql('SELECT RDB$DEPENDENT_TYPE, '
                                   'RDB$DEPENDED_ON_TYPE '
                                   'FROM RDB$DEPENDENCIES '
                                   'WHERE RDB$DEPENDED_ON_NAME = ?', (name,)),
                'catalog_dependents': metadata.get('dependents', [])}
            rollback()
            try:
                apply('drop', {'confirmation': name}, name)
                connection.commit()
            except Exception as error:
                codes = list(status_codes(error))
                assert codes
                rollback()
                assert sql('SELECT N FROM OWNED_UDF_VIEW') == [(12,)]
                return {'native_status_codes': codes,
                        'dependent_preserved': True}
            raise AssertionError('Native dependency did not protect UDF')
        case('dependent-view-drop-denied', dependency)

        def permissions():
            nonlocal connection
            name = 'OWNED_PERMISSION'
            apply('create', definition(name))
            connection.commit()
            restricted_password = secrets.token_urlsafe(24)
            sql("CREATE USER OWNED_UDF_USER PASSWORD '" +
                restricted_password.replace("'", "''") + "'")
            connection.commit()
            admin = connection
            denied = []

            def restricted_connection():
                return native.connect(password=restricted_password,
                                      **_route_arguments(
                                          {**route, 'user': 'OWNED_UDF_USER'},
                                          native))

            restricted = restricted_connection()
            try:
                connection = restricted
                for action, values in (
                        ('create', definition('OWNED_NO_PERMISSION')),
                        ('alter', {'entrypoint': 'owned_other',
                                   'alter_target': 'ENTRY_POINT'}),
                        ('comment', {'description': 'not owner'}),
                        ('drop', {'confirmation': name})):
                    try:
                        apply(action, values, name if action != 'create'
                              else None)
                    except Exception as error:
                        codes = list(status_codes(error))
                        assert codes
                        denied.append({'operation': action,
                                       'native_status_codes': codes})
                    else:
                        raise AssertionError('Unauthorized UDF task succeeded')
                    rollback()
                connection = admin
                privilege = {'principal_kind': 'USER',
                             'principal': 'OWNED_UDF_USER',
                             'object_type': 'FUNCTION', 'object_name': name,
                             'privileges': ['EXECUTE']}
                apply('grant', privilege, kind='privilege')
                connection.commit()
                restricted.close()
                restricted = restricted_connection()
                connection = restricted
                assert sql('SELECT OWNED_PERMISSION(5) FROM RDB$DATABASE') == [
                    (12,)]
                rollback()
                connection = admin
                apply('revoke', {**privilege,
                                 'confirmation': 'OWNED_UDF_USER'},
                      kind='privilege')
                connection.commit()
                restricted.close()
                restricted = restricted_connection()
                connection = restricted
                try:
                    sql('SELECT OWNED_PERMISSION(5) FROM RDB$DATABASE')
                except Exception as error:
                    codes = list(status_codes(error))
                    assert codes
                    denied.append({'operation': 'execute-after-revoke',
                                   'native_status_codes': codes})
                else:
                    raise AssertionError('Revoked EXECUTE survived')
                return {'denied_operations': denied,
                        'granted_execute_result': 12}
            finally:
                connection = admin
                restricted.close()
        case('restricted-lifecycle-execute-grant-revoke', permissions)
        if browser_options is not None:
            phase = 'owned-browser-matrix'
            result['browser_checks'] = browser_checks(
                browser_options, route, password, container, build_root)
            result['private_browser_profile_removed'] = not (
                build_root / 'private-browser-profile.json').exists()
    except Exception as error:
        failure(phase, error)
    finally:
        for label, resource in (
                ('connection', connection), ('client', client)):
            if resource is not None:
                try:
                    resource.close()
                except Exception as error:
                    failure('close-' + label, error)
        if container is not None:
            try:
                remove_owned(container)
                result['owned_container_removed'] = True
            except Exception as error:
                failure('remove-owned-container', error)
    result['complete'] = (not result['failures'] and
                          len(result['checks']) == 43
                          and result['owned_container_removed'] and
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
                      'checks': len(result['checks']),
                      'failures': result['failures']}))
    return 0 if result['complete'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
