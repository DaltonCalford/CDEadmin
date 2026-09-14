#!/usr/bin/env python3
"""Native character metadata qualification in an owned Firebird container."""

import argparse
import io
import json
import os
import re
import secrets
import tarfile
import time
import traceback
import uuid
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


def run(image):
    import firebird.driver as native
    _configure_client_library(native)
    password = secrets.token_urlsafe(24)
    database = '/var/lib/firebird/data/owned_character.fdb'
    container = connection = client = None
    result = {'schema': 'cdeadmin.firebird-character-metadata.v1',
              'complete': False, 'checks': [], 'failures': [],
              'task_evidence': {}, 'owned_container_removed': False,
              'credential_values_exported': False}
    phase = 'create-owned-container'

    def sql(source, parameters=()):
        with connection.cursor() as cursor:
            cursor.execute(source, parameters)
            return cursor.fetchall() if cursor.description else []

    def apply(kind, action, draft, name=None):
        request = {'_provider_route': route, 'resource_kind': kind,
                   'operation_id': action, 'draft': draft,
                   'target_resource': ({'display_name': name,
                                        'display_path': [name]}
                                       if name is not None else None)}
        assert not ADMINISTRATION.validate(request)['errors']
        plan = ADMINISTRATION.plan(request)
        ADMINISTRATION.apply(client, plan, connection=connection)
        return [item['source'] for item in
                plan['command_preview']['statements']]

    def proof(kind, action, statements):
        result['task_evidence'][f'visual_admin.{kind}.{action}'] = {
            'statements': statements, 'live_execution': 'passed'}

    def count(name):
        return sql('SELECT COUNT(*) FROM RDB$COLLATIONS '
                   'WHERE RDB$COLLATION_NAME = ?', (name,))[0][0]

    def catalog(name, kind='collation'):
        return next(item['native'] for item in _resources(connection, {})
                    if item['resource_kind'] == kind and
                    item['display_name'] == name)

    def failure(case, error):
        result['failures'].append({'case': case,
                                  'error_type': type(error).__name__,
                                   'native_status_codes': list(
                                      status_codes(error)),
                                   'frames': [
                                      {'file': Path(frame.filename).name,
                                       'line': frame.lineno,
                                       'function': frame.name}
                                      for frame in traceback.extract_tb(
                                          error.__traceback__)]})

    try:
        environment = dict(os.environ, FIREBIRD_ROOT_PASSWORD=password,
                           FIREBIRD_DATABASE=database)
        container = docker(
            'create', '--name', 'cdeadmin-character-' + uuid.uuid4().hex[:16],
            '--label', 'cdeadmin-owned-gate=' + OWNER,
            '--publish', '127.0.0.1::3050', '--env', 'FIREBIRD_ROOT_PASSWORD',
            '--env', 'FIREBIRD_DATABASE', image,
            env=environment).decode().strip()
        if not re.fullmatch('[0-9a-f]{64}', container):
            raise RuntimeError('Invalid owned container identity')
        result['container_id'] = container
        # Install an alias only in this newly created, labelled fixture. This
        # qualifies the no-FROM grammar without modifying the user's server.
        configuration = (
            'charset = UTF8 {\n'
            '    collation = OWNED_SAME UNICODE\n}\n').encode('ascii')
        archive_bytes = io.BytesIO()
        with tarfile.open(fileobj=archive_bytes, mode='w') as archive:
            member = tarfile.TarInfo('cdeadmin_owned_character.conf')
            member.size = len(configuration)
            member.mode = 0o644
            archive.addfile(member, io.BytesIO(configuration))
        docker('cp', '-', container + ':/opt/firebird/intl/',
               input_data=archive_bytes.getvalue())
        docker('start', container)
        route = {'host': '127.0.0.1', 'port': published_port(container),
                 'user': 'SYSDBA', 'database': database, 'timeout': 2,
                 'auth_plugin_list': 'Srp256',
                 'credential_reference_id': 'owned-character-secret',
                 'principal_reference': 'owned-character-principal'}
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
        phase = 'restricted-character-administration'
        for name in ('OWNED_PERMISSION_0', 'OWNED_PERMISSION_1'):
            apply('collation', 'create', {
                'name': name, 'character_set': 'UTF8',
                'base_collation': 'UNICODE'})
        connection.commit()
        restricted_password = secrets.token_urlsafe(24)
        sql("CREATE USER OWNED_CHAR_USER PASSWORD '" +
            restricted_password.replace("'", "''") + "'")
        connection.commit()
        admin_connection = connection
        restricted = native.connect(password=restricted_password,
                                    **_route_arguments(
                                        {**route, 'user': 'OWNED_CHAR_USER'},
                                        native))
        try:
            connection = restricted
            for case, kind, action, draft, target in (
                    ('unprivileged-create-denied', 'collation', 'create', {
                        'name': 'OWNED_USER_COLLATION',
                        'character_set': 'UTF8',
                        'base_collation': 'UNICODE'}, None),
                    ('unprivileged-default-change-denied', 'character-set',
                     'alter', {'default_collation': 'UNICODE'}, 'UTF8'),
                    ('unprivileged-charset-comment-denied', 'character-set',
                     'comment', {'description': 'Must not change'}, 'UTF8'),
                    ('unprivileged-collation-comment-denied', 'collation',
                     'comment', {'description': 'Must not change'},
                     'OWNED_PERMISSION_0'),
                    ('unprivileged-drop-denied', 'collation', 'drop',
                     {'confirmation': 'OWNED_PERMISSION_1'},
                     'OWNED_PERMISSION_1')):
                try:
                    try:
                        apply(kind, action, draft, target)
                    except Exception as error:
                        codes = list(status_codes(error))
                        expected = (335545264 if action == 'create' else
                                    335544352)
                        assert expected in codes, codes
                        result['checks'].append({'case': case,
                                                 'native_status_codes': codes})
                    else:
                        raise AssertionError('Unprivileged task succeeded')
                except Exception as error:
                    failure(case, error)
                finally:
                    if connection.main_transaction.is_active():
                        connection.rollback()
            connection = admin_connection
            apply('privilege', 'grant', {
                'privilege_scope': 'ddl_class', 'ddl_class': 'COLLATION',
                'ddl_privileges': ['CREATE'], 'principal_kind': 'USER',
                'principal': 'OWNED_CHAR_USER'})
            connection.commit()
            # Native attachment privilege caches need a fresh attachment to
            # observe a grant made by a different administrator connection.
            restricted.close()
            restricted = native.connect(password=restricted_password,
                                        **_route_arguments(
                                            {**route,
                                             'user': 'OWNED_CHAR_USER'},
                                            native))
            connection = restricted
            apply('collation', 'create', {
                'name': 'OWNED_USER_COLLATION', 'character_set': 'UTF8',
                'base_collation': 'UNICODE'})
            connection.commit()
            assert catalog('OWNED_USER_COLLATION')[
                'owner'] == 'OWNED_CHAR_USER'
            connection.rollback()
            apply('collation', 'comment', {'description': 'Owner comment'},
                  'OWNED_USER_COLLATION')
            connection.commit()
            assert catalog('OWNED_USER_COLLATION')['description'] == (
                'Owner comment')
            connection.rollback()
            apply('collation', 'drop', {
                'confirmation': 'OWNED_USER_COLLATION'},
                'OWNED_USER_COLLATION')
            connection.commit()
            assert count('OWNED_USER_COLLATION') == 0
            connection.rollback()
            result['checks'].append({
                'case': 'granted-create-owner-comment-drop',
                'owner': 'OWNED_CHAR_USER'})
            connection = admin_connection
            apply('privilege', 'revoke', {
                'privilege_scope': 'ddl_class', 'ddl_class': 'COLLATION',
                'ddl_privileges': ['CREATE'], 'principal_kind': 'USER',
                'principal': 'OWNED_CHAR_USER',
                'confirmation': 'OWNED_CHAR_USER'})
            connection.commit()
            restricted.close()
            restricted = native.connect(password=restricted_password,
                                        **_route_arguments(
                                            {**route,
                                             'user': 'OWNED_CHAR_USER'},
                                            native))
            connection = restricted
            try:
                apply('collation', 'create', {
                    'name': 'OWNED_USER_COLLATION', 'character_set': 'UTF8',
                    'base_collation': 'UNICODE'})
            except Exception as error:
                codes = list(status_codes(error))
                assert 335545264 in codes
                assert count('OWNED_USER_COLLATION') == 0
                result['checks'].append({'case': 'revoked-create-denied',
                                         'native_status_codes': codes})
            else:
                raise AssertionError('Revoked CREATE authority survived')
            connection.rollback()
        finally:
            connection = admin_connection
            restricted.close()
        phase = 'flag-cycles'
        for mask in range(8):
            case = 'flags-create-drop-rollback-recreation-' + str(mask)
            name = 'OWNED_東京_' + str(mask)
            draft = {'name': name, 'character_set': 'UTF8',
                     'base_collation': 'UNICODE',
                     'padding': 'PAD_SPACE' if mask & 1 else 'NO_PAD',
                     'case_sensitivity': ('INSENSITIVE' if mask & 2 else
                                          'SENSITIVE'),
                     'accent_sensitivity': ('INSENSITIVE' if mask & 4 else
                                            'SENSITIVE'),
                     'description': "Owned é ' metadata\nline two  "}
            try:
                create_sql = apply('collation', 'create', draft)
                assert count(name) == 1
                connection.rollback()
                assert count(name) == 0
                connection.rollback()
                apply('collation', 'create', draft)
                connection.commit()
                observed = catalog(name)
                assert observed['attributes'] == str(mask)
                assert observed['character_set'] == 'UTF8'
                assert observed['description'] == draft['description']
                assert observed['owner'] == 'SYSDBA'
                assert 'privileges' in observed['property_sections']
                recreation = observed['recreation_statements']
                connection.rollback()
                drop_sql = apply('collation', 'drop', {'confirmation': name},
                                 name)
                connection.rollback()
                assert count(name) == 1
                connection.rollback()
                apply('collation', 'drop', {'confirmation': name}, name)
                connection.commit()
                for statement in recreation:
                    sql(statement)
                connection.commit()
                recreated = catalog(name)
                for key in ('attributes', 'character_set', 'description',
                            'base_collation', 'specific_attributes'):
                    assert recreated[key] == observed[key], key
                connection.rollback()
                proof('collation', 'create', create_sql)
                proof('collation', 'drop', drop_sql)
                result['checks'].append({'case': case,
                                         'statements': create_sql,
                                         'recreation': recreation})
            except Exception as error:
                if mask in (4, 5) and 336068830 in status_codes(error):
                    assert count(name) == 0
                    result['checks'].append({
                        'case': case,
                        'expected_native_rejection': 336068830,
                        'reason': 'ICU accent-insensitive requires '
                                  'case-insensitive comparison',
                        'created': False})
                else:
                    failure(case, error)
            finally:
                if connection.main_transaction.is_active():
                    connection.rollback()
        phase = 'comment-cycles'
        for kind, name in (('collation', 'OWNED_東京_0'),
                           ('character-set', 'UTF8')):
            case = kind + '-comment-set-clear-rollback'
            try:
                before = catalog(name, kind).get('description')
                connection.rollback()
                statements = apply(kind, 'comment', {
                    'description': "東京 é ' comment  "}, name)
                connection.rollback()
                assert catalog(name, kind).get('description') == before
                connection.rollback()
                apply(kind, 'comment', {
                    'description': "東京 é ' comment  "}, name)
                connection.commit()
                assert catalog(name, kind)['description'] == (
                    "東京 é ' comment  ")
                connection.rollback()
                apply(kind, 'comment', {'description': ''}, name)
                connection.commit()
                assert catalog(name, kind)['description'] is None
                connection.rollback()
                proof(kind, 'comment', statements)
                result['checks'].append(
                    {'case': case, 'statements': statements})
            except Exception as error:
                failure(case, error)
            finally:
                if connection.main_transaction.is_active():
                    connection.rollback()
        phase = 'charset-default'
        before = catalog('UTF8', 'character-set')['default_collation']
        assert 'privileges' in catalog('UTF8', 'character-set')[
            'property_sections']
        connection.rollback()
        sql('CREATE TABLE OWNED_BEFORE_DEFAULT (V VARCHAR(20) '
            'CHARACTER SET UTF8)')
        connection.commit()
        statements = apply('character-set', 'alter', {
            'default_collation': 'OWNED_東京_0'}, 'UTF8')
        connection.rollback()
        assert catalog('UTF8', 'character-set')['default_collation'] == before
        connection.rollback()
        apply('character-set', 'alter', {
            'default_collation': 'OWNED_東京_0'}, 'UTF8')
        connection.commit()
        assert catalog('UTF8', 'character-set')['default_collation'] == (
            'OWNED_東京_0')
        connection.rollback()
        proof('character-set', 'alter', statements)
        result['checks'].append({'case': 'charset-default-commit-rollback',
                                 'statements': statements})
        sql('CREATE TABLE OWNED_AFTER_DEFAULT (V VARCHAR(20) '
            'CHARACTER SET UTF8)')
        connection.commit()
        column_collations = dict(sql(
            'SELECT TRIM(RF.RDB$RELATION_NAME), TRIM(C.RDB$COLLATION_NAME) '
            'FROM RDB$RELATION_FIELDS RF JOIN RDB$FIELDS F ON '
            'F.RDB$FIELD_NAME = RF.RDB$FIELD_SOURCE JOIN RDB$COLLATIONS C ON '
            'C.RDB$CHARACTER_SET_ID = F.RDB$CHARACTER_SET_ID AND '
            'C.RDB$COLLATION_ID = COALESCE(RF.RDB$COLLATION_ID, '
            'F.RDB$COLLATION_ID) WHERE RF.RDB$RELATION_NAME IN '
            "('OWNED_BEFORE_DEFAULT', 'OWNED_AFTER_DEFAULT')"))
        assert column_collations == {'OWNED_BEFORE_DEFAULT': before,
                                     'OWNED_AFTER_DEFAULT': 'OWNED_東京_0'}
        connection.rollback()
        result['checks'].append({'case': 'default-applies-only-new-columns',
                                 'column_collations': column_collations})
        for invalid in ('ASCII', 'OWNED_ABSENT'):
            case = 'invalid-default-preserves-current-' + invalid
            try:
                apply('character-set', 'alter', {
                    'default_collation': invalid}, 'UTF8')
            except Exception as error:
                codes = list(status_codes(error))
                assert codes
                assert catalog('UTF8', 'character-set')[
                    'default_collation'] == 'OWNED_東京_0'
                result['checks'].append({'case': case,
                                         'native_status_codes': codes})
            else:
                raise AssertionError('Invalid charset default was accepted')
            connection.rollback()
        phase = 'inheritance-and-specific-attributes'
        variants = [
            ('inherited-flags', {'base_collation': 'OWNED_東京_7'}, 7, False),
            ('external-implementation', {
                'source_mode': 'EXTERNAL', 'external_name': 'UNICODE',
                'padding': 'NO_PAD', 'case_sensitivity': 'INSENSITIVE',
                'accent_sensitivity': 'INSENSITIVE'}, 6, False),
            ('numeric-sort', {'base_collation': 'UNICODE',
                              'specific_attributes': [
                                  {'name': 'NUMERIC-SORT', 'value': '1'}]},
             None, True),
            ('inherited-numeric-sort', {'base_collation': 'OWNED_EXTRA_2'},
             None, True),
            ('duplicate-last-wins', {'base_collation': 'OWNED_EXTRA_2',
                                     'specific_attributes': [
                                         {'name': 'NUMERIC-SORT',
                                          'value': '1'},
                                         {'name': 'NUMERIC-SORT',
                                          'value': '0'}]},
             None, False),
            ('empty-removes-inherited', {'base_collation': 'OWNED_EXTRA_2',
                                         'specific_attributes': [
                                             {'name': 'NUMERIC-SORT'}]},
             None, False),
            ('same-name-installed-implementation', {
                'source_mode': 'SAME_NAME'}, 0, False),
        ]
        for index, (case, options, attributes, numeric) in enumerate(variants):
            name = ('OWNED_SAME' if options.get('source_mode') == 'SAME_NAME'
                    else 'OWNED_EXTRA_' + str(index))
            try:
                statements = apply('collation', 'create', {
                    'name': name, 'character_set': 'UTF8', **options})
                connection.commit()
                observed = catalog(name)
                if attributes is not None:
                    assert observed['attributes'] == str(attributes)
                comparison = sql(
                    'SELECT IIF(_UTF8 \'2\' COLLATE "' + name +
                    '" < _UTF8 \'10\' COLLATE "' + name +
                    '", 1, 0) FROM RDB$DATABASE')[0][0]
                assert bool(comparison) is numeric
                recreation = observed['recreation_statements']
                connection.rollback()
                apply('collation', 'drop', {'confirmation': name}, name)
                connection.commit()
                for statement in recreation:
                    sql(statement)
                connection.commit()
                recreated = catalog(name)
                for key in ('attributes', 'specific_attributes',
                            'base_collation'):
                    assert recreated[key] == observed[key], key
                result['checks'].append({'case': case,
                                         'statements': statements,
                                         'recreation': recreation})
            except Exception as error:
                failure(case, error)
            finally:
                if connection.main_transaction.is_active():
                    connection.rollback()
        phase = 'native-rejections'
        for case, options in (
                ('invalid-numeric-sort', {'base_collation': 'UNICODE',
                                          'specific_attributes': [
                                              {'name': 'NUMERIC-SORT',
                                               'value': 'invalid'}]}),
                ('mismatched-character-set', {'base_collation': 'ASCII'}),
                ('missing-external-implementation', {
                    'source_mode': 'EXTERNAL',
                    'external_name': 'OWNED_ABSENT_IMPLEMENTATION'}),
                ('missing-same-name-implementation', {
                    'source_mode': 'SAME_NAME'})):
            name = 'OWNED_REJECTED'
            try:
                try:
                    apply('collation', 'create', {
                        'name': name, 'character_set': 'UTF8', **options})
                except Exception as error:
                    codes = list(status_codes(error))
                    assert codes, 'Native rejection must retain status codes'
                    assert count(name) == 0
                    result['checks'].append({'case': case,
                                             'native_status_codes': codes,
                                             'created': False})
                else:
                    raise AssertionError('Native invalid input was accepted')
            except Exception as error:
                failure(case, error)
            finally:
                if connection.main_transaction.is_active():
                    connection.rollback()
        phase = 'dependent-column-drop-denied'
        sql('CREATE TABLE OWNED_DEPENDENCY (V VARCHAR(20) '
            'CHARACTER SET UTF8 COLLATE "OWNED_EXTRA_0")')
        connection.commit()
        try:
            apply('collation', 'drop', {'confirmation': 'OWNED_EXTRA_0'},
                  'OWNED_EXTRA_0')
        except Exception as error:
            codes = list(status_codes(error))
            assert codes
            assert count('OWNED_EXTRA_0') == 1
            result['checks'].append({'case': phase,
                                     'native_status_codes': codes,
                                     'dependent_object_preserved': True})
        else:
            raise AssertionError('Dropping a dependent collation succeeded')
        connection.rollback()
    except Exception as error:
        failure(phase, error)
    finally:
        for label, resource in (('connection', connection),
                                ('client', client)):
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
                          len(result['checks']) == 33 and
                          result['owned_container_removed'])
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', default='firebirdsql/firebird:5.0.4')
    parser.add_argument('--output', type=Path, required=True)
    options = parser.parse_args()
    if options.output.exists():
        raise SystemExit('Use a new evidence file')
    result = run(options.image)
    options.output.parent.mkdir(parents=True, exist_ok=True)
    options.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({'complete': result['complete'],
                      'checks': len(result['checks']),
                      'failures': result['failures']}))
    return 0 if result['complete'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
