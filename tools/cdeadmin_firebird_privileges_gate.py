#!/usr/bin/env python3
##########################################################################
# CDEadmin - Multi-engine Database Administration
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
##########################################################################

"""Native privilege compiler cycles in a disposable Firebird database."""

import argparse
import json
import traceback
import uuid
from pathlib import Path, PurePosixPath
from types import SimpleNamespace

from cdeadmin_firebird_admin_mapping_gate import (
    ADMINISTRATION, _create_client, _route_arguments, _resources,
)
from pgadmin.cdeadmin.providers.firebird.privileges import (
    OBJECT_PRIVILEGES, DDL_CLASSES, PRINCIPAL_KINDS,
)
from pgadmin.cdeadmin.providers.firebird.mappings import identifier, literal


def run(profiles):
    import firebird.driver as driver
    document = json.loads(profiles.read_text())
    route = next(dict(item) for item in document['profiles']
                 if item['engine'] == 'firebird')
    route.setdefault('host', document.get('host', '127.0.0.1'))
    route['database'] = str(PurePosixPath(route['database']).parent /
                            ('cde_privileges_' + uuid.uuid4().hex + '.fdb'))
    client = _create_client(SimpleNamespace(acquire_secret=None))
    connection = driver.create_database(password=route['password'],
                                        **_route_arguments(route, driver))
    result = {'complete': False, 'checks': [], 'failures': [],
              'fixture_database': route['database'], 'fixture_removed': False,
              'qualification_claim': False, 'permission_checks': [],
              'temporary_user_removed': False}
    test_user = 'CDE_PRIV_' + uuid.uuid4().hex[:20].upper()
    test_password = uuid.uuid4().hex
    user_created = False
    creator_touched = False
    creator_draft = {'privilege_scope': 'database',
                     'database_privileges': ['CREATE'],
                     'principal_kind': 'USER', 'principal': test_user}

    def creator_count():
        with connection.cursor() as cursor:
            cursor.execute('SELECT COUNT(*) FROM SEC$DB_CREATORS '
                           'WHERE SEC$USER = ? AND SEC$USER_TYPE = 8',
                           (test_user,))
            return cursor.fetchone()[0]

    def execute(source):
        with connection.cursor() as cursor:
            cursor.execute(source)

    def snapshot():
        with connection.cursor() as cursor:
            cursor.execute('SELECT RDB$USER, RDB$RELATION_NAME, '
                           'RDB$FIELD_NAME, RDB$PRIVILEGE, RDB$GRANTOR, '
                           'RDB$GRANT_OPTION, RDB$USER_TYPE, RDB$OBJECT_TYPE '
                           'FROM RDB$USER_PRIVILEGES')
            return set(cursor.fetchall())

    def apply(operation, draft):
        request = {'_provider_route': route, 'resource_kind': 'privilege',
                   'operation_id': operation, 'draft': draft}
        assert not ADMINISTRATION.validate(request)['errors']
        plan = ADMINISTRATION.plan(request)
        receipt = ADMINISTRATION.apply(client, plan, connection=connection)
        assert receipt['staged_in_provider_session'] is True
        return plan['command_preview']['statements'][0]['source']

    def access(label, source, accepted, role=None, denial_code=335544352):
        other = driver.connect(password=test_password, **_route_arguments(
            {**route, 'user': test_user, 'role': role}, driver))
        try:
            try:
                with other.cursor() as cursor:
                    cursor.execute(source)
                    rows = cursor.fetchall() if cursor.description else []
                observed = {'accepted': True, 'rows': rows}
            except driver.DatabaseError as error:
                observed = {'accepted': False, 'gds_codes': error.gds_codes}
            result['permission_checks'].append({'case': label, **observed})
            assert observed['accepted'] is accepted, (label, observed)
            if not accepted:
                assert denial_code in observed['gds_codes'], (label, observed)
            elif source == 'SELECT X, V FROM B':
                assert observed['rows'] == [(3, 7)]
        except Exception:
            result['failures'].append({'case': label,
                                       'traceback': traceback.format_exc()})
        finally:
            if other.main_transaction.is_active():
                other.rollback()
            other.close()

    def case(label, draft, revoke_mode=None):
        try:
            before = snapshot()
            connection.commit()
            source = apply('grant', draft)
            granted = snapshot()
            assert granted != before, 'grant did not change native catalog'
            connection.rollback()
            assert snapshot() == before, 'grant rollback changed catalog'
            connection.commit()
            apply('grant', draft)
            connection.commit()
            assert snapshot() == granted, 'committed grant differs'
            connection.commit()
            revoke = {**draft, 'grant_option': False,
                      'confirmation': ', '.join([
                          draft.get('principal') or 'PUBLIC',
                          *(item.get('name') or 'PUBLIC' for item in
                            draft.get('additional_grantees', []))])}
            expected_revoked = before
            if revoke_mode == 'option-only':
                revoke['grant_option_only'] = True
                expected_revoked = before | {
                    (*row[:5], 0, *row[6:]) for row in granted - before}
            elif revoke_mode == 'all-objects':
                revoke = {
                    'privilege_scope': 'all_objects',
                    'principal_kind': draft['principal_kind'],
                    'principal': draft['principal'],
                    'confirmation': draft['principal']}
            revoke_source = apply('revoke', revoke)
            assert snapshot() == expected_revoked, 'revoke differs'
            connection.rollback()
            assert snapshot() == granted, 'revoke rollback lost grant'
            connection.commit()
            apply('revoke', revoke)
            connection.commit()
            assert snapshot() == expected_revoked, 'committed revoke differs'
            connection.commit()
            if revoke_mode == 'option-only':
                apply('revoke', {**revoke, 'grant_option_only': False})
                connection.commit()
                assert snapshot() == before, 'final revoke differs'
                connection.commit()
            result['checks'].append({'case': label, 'source': source,
                                     'revoke_source': revoke_source,
                                     'grant_and_revoke_rollback': True,
                                     'committed_roundtrip': True,
                                     'native_added_grants': len(
                                         granted - before)})
        except Exception:
            result['failures'].append({'case': label,
                                       'traceback': traceback.format_exc()})
            if connection.main_transaction.is_active():
                connection.rollback()
            # Caller recreates the owned fixture before collecting more cases.
            return False
        return True

    def seed():
        for source in [
                'CREATE TABLE T (X INTEGER PRIMARY KEY, V INTEGER)',
                'CREATE TABLE B (X INTEGER, V INTEGER)',
                'CREATE VIEW VW AS SELECT X FROM T',
                'CREATE PROCEDURE P AS BEGIN END',
                'CREATE FUNCTION F RETURNS INTEGER AS BEGIN RETURN 1; END',
                'CREATE PACKAGE PK AS BEGIN PROCEDURE P; END',
                'CREATE PACKAGE BODY PK AS BEGIN PROCEDURE P AS '
                'BEGIN END END',
                'CREATE TRIGGER TR FOR T INACTIVE BEFORE INSERT AS BEGIN END',
                'CREATE SEQUENCE S', "CREATE EXCEPTION E 'test'",
                'CREATE ROLE R']:
            execute(source)
            connection.commit()

    try:
        seed()
        targets = {'TABLE': 'B', 'VIEW': 'VW', 'PROCEDURE': 'P',
                   'FUNCTION': 'F', 'PACKAGE': 'PK', 'SEQUENCE': 'S',
                   'GENERATOR': 'S', 'EXCEPTION': 'E'}
        base = {'principal': 'R', 'principal_kind': 'ROLE',
                'object_type': 'TABLE', 'object_name': 'B',
                'privileges': ['SELECT']}
        cases = []
        for kind, values in OBJECT_PRIVILEGES.items():
            for value in values:
                cases.append((kind + '-' + value, {
                    **base, 'object_type': kind, 'object_name': targets[kind],
                    'privileges': [value]}))
        for kind in DDL_CLASSES:
            for privilege in ('CREATE', 'ALTER ANY', 'DROP ANY', 'ALL'):
                cases.append(('class-' + kind + '-' + privilege, {
                    'principal': 'R', 'principal_kind': 'ROLE',
                    'privilege_scope': 'ddl_class', 'ddl_class': kind,
                    'ddl_privileges': [privilege]}))
        principals = {'USER': 'CDE_PRIV_TEST', 'ROLE': 'R', 'PUBLIC': '',
                      'PROCEDURE': 'P', 'FUNCTION': 'F', 'PACKAGE': 'PK',
                      'TRIGGER': 'TR', 'VIEW': 'VW', 'GROUP': 'CDE_PRIV_GROUP',
                      'SYSTEM PRIVILEGE': 'USER_MANAGEMENT'}
        for kind in PRINCIPAL_KINDS:
            cases.append(('grantee-' + kind, {
                **base, 'principal_kind': kind,
                'principal': principals[kind]}))
        for key, privilege in [('update_columns', 'UPDATE'),
                               ('reference_columns', 'REFERENCES')]:
            cases.append(('columns-' + privilege, {
                **base, 'privileges': [privilege],
                key: [{'name': 'X'}, {'name': 'V'}]}))
        for privilege in ('ALTER', 'DROP', 'ALL'):
            cases.append(('database-' + privilege, {
                'principal': 'R', 'principal_kind': 'ROLE',
                'privilege_scope': 'database',
                'database_privileges': [privilege]}))
        cases.append(('grant-option-grantor', {
            **base, 'grant_option': True, 'grantor': 'SYSDBA'}))
        cases.append(('revoke-option-only', {
            **base, 'grant_option': True, 'grantor': 'SYSDBA'}, 'option-only'))
        cases.append(('revoke-column-option-only', {
            **base, 'privileges': ['UPDATE'],
            'update_columns': [{'name': 'V'}], 'grant_option': True},
            'option-only'))
        cases.append(('revoke-all-on-all', base, 'all-objects'))
        cases.append(('multiple-grantees', {**base, 'additional_grantees': [
            {'kind': 'USER', 'name': 'CDE_MULTIGRANTEE'},
            {'kind': 'PUBLIC'}]}))
        result['expected_checks'] = len(cases)
        for values in cases:
            if not case(*values):
                connection.drop_database()
                result.setdefault('failed_case_fixtures_removed', []).append(
                    values[0])
                connection = driver.create_database(
                    password=route['password'],
                    **_route_arguments(route, driver))
                seed()
        execute('CREATE USER ' + identifier(test_user) + ' PASSWORD ' +
                literal(test_password))
        connection.commit()
        user_created = True
        execute('INSERT INTO B VALUES (3, 7)')
        connection.commit()
        access('ungranted-select-denied', 'SELECT X, V FROM B', False)
        access('ungranted-update-denied', 'UPDATE B SET V = 8', False)
        access('nonowner-alter-denied', 'ALTER TABLE B ADD Z INTEGER', False)
        user_grant = {**base, 'principal_kind': 'USER', 'principal': test_user,
                      'grant_option': True}
        apply('grant', user_grant)
        connection.commit()
        access('granted-select', 'SELECT X, V FROM B', True)
        delegate = 'GRANT SELECT ON B TO USER CDE_PRIV_DELEGATE'
        access('grant-option-allows-delegation', delegate, True)
        apply('revoke', {**user_grant, 'grant_option': False,
                         'grant_option_only': True,
                         'confirmation': test_user})
        connection.commit()
        access('option-revoke-keeps-select', 'SELECT X, V FROM B', True)
        # Native DYN 173: no grant option on table/view (not isc_no_priv).
        access('option-revoke-denies-delegation', delegate, False,
               denial_code=336068781)
        apply('grant', {**base, 'privileges': ['UPDATE'],
                        'update_columns': [{'name': 'V'}]})
        connection.commit()
        execute('GRANT R TO USER ' + identifier(test_user))
        connection.commit()
        access('inactive-role-update-denied', 'UPDATE B SET V = 8', False)
        access('active-role-column-update', 'UPDATE B SET V = 8', True, 'R')
        access('ungranted-column-update-denied', 'UPDATE B SET X = 8', False,
               'R')
        apply('grant', {'privilege_scope': 'ddl_class', 'ddl_class': 'TABLE',
                        'ddl_privileges': ['CREATE'],
                        'principal_kind': 'ROLE', 'principal': 'R'})
        connection.commit()
        access('inactive-role-create-denied',
               'CREATE TABLE CDE_OWNED (X INTEGER)', False,
               denial_code=335545264)
        access('active-role-class-create',
               'CREATE TABLE CDE_OWNED (X INTEGER)', True, 'R')
        apply('revoke', {**user_grant, 'grant_option': False,
                         'confirmation': test_user})
        connection.commit()
        access('full-revoke-denies-select', 'SELECT X, V FROM B', False)
        access('role-column-update-still-separate', 'UPDATE B SET V = 8', True,
               'R')
        with connection.cursor() as cursor:
            cursor.execute('SELECT X, V FROM B')
            assert cursor.fetchall() == [(3, 7)]
        connection.commit()
        assert creator_count() == 0
        connection.commit()
        creator_touched = True
        apply('grant', creator_draft)
        before_rollback = creator_count()
        connection.rollback()
        after_rollback = creator_count()
        connection.commit()
        apply('grant', creator_draft)
        connection.commit()
        assert creator_count() == 1
        connection.commit()
        catalog = _resources(connection, {'route': route})
        connection.commit()
        creator = next(item['native'] for item in catalog if
                       item['resource_kind'] == 'privilege' and
                       item['native'].get('catalog_source') ==
                       'SEC$DB_CREATORS' and
                       item['native']['grantee'] == test_user)
        assert creator['target_resolution']['state'] == 'server-scope'
        assert creator['grant_option_supported'] is False
        result['server_catalog_scope_verified'] = True
        created_route = {
            **route, 'user': test_user, 'role': None,
            'database': str(PurePosixPath(route['database']).parent /
                            ('cde_created_' + uuid.uuid4().hex + '.fdb'))}
        created = None
        try:
            created = driver.create_database(password=test_password,
                                             **_route_arguments(created_route,
                                                                driver))
            with created.cursor() as cursor:
                cursor.execute('SELECT MON$OWNER FROM MON$DATABASE')
                assert cursor.fetchone()[0].rstrip() == test_user
        finally:
            if created is not None:
                if created.main_transaction.is_active():
                    created.rollback()
                created.drop_database()
                result['created_database_removed'] = True
        apply('revoke', {**creator_draft, 'confirmation': test_user})
        connection.commit()
        assert creator_count() == 0
        connection.commit()
        denied = None
        try:
            created = driver.create_database(password=test_password,
                                             **_route_arguments(created_route,
                                                                driver))
        except driver.DatabaseError as error:
            denied = {'gds_codes': error.gds_codes,
                      'native_message': str(error)}
        else:
            created.drop_database()
        assert denied is not None, 'CREATE DATABASE still allowed after revoke'
        result['database_creation'] = {
            'grant_visible_before_rollback': before_rollback,
            'grant_visible_after_rollback': after_rollback,
            'granted_create_owner_verified': True,
            'revoked_create_denied': denied,
        }
    except Exception:
        result['failures'].append({'case': 'infrastructure',
                                   'traceback': traceback.format_exc()})
    finally:
        if creator_touched:
            try:
                if connection.main_transaction.is_active():
                    connection.rollback()
                if creator_count():
                    apply('revoke', {**creator_draft,
                                     'confirmation': test_user})
                connection.commit()
                assert creator_count() == 0
                connection.commit()
                result['database_creator_removed'] = True
            except Exception:
                result['failures'].append({
                    'case': 'creator-cleanup', 'owned_user': test_user,
                    'traceback': traceback.format_exc()})
        if user_created:
            try:
                if connection.main_transaction.is_active():
                    connection.rollback()
                execute('DROP USER ' + identifier(test_user))
                connection.commit()
                with connection.cursor() as cursor:
                    cursor.execute('SELECT COUNT(*) FROM SEC$USERS WHERE '
                                   'SEC$USER_NAME = ?', (test_user,))
                    assert cursor.fetchone()[0] == 0
                connection.commit()
                result['temporary_user_removed'] = True
            except Exception:
                result['failures'].append({
                    'case': 'user-cleanup', 'owned_user': test_user,
                    'traceback': traceback.format_exc()})
        else:
            result['temporary_user_removed'] = True
        try:
            if connection.main_transaction.is_active():
                connection.rollback()
            connection.drop_database()
            result['fixture_removed'] = True
        except Exception:
            result['failures'].append({'case': 'cleanup',
                                       'traceback': traceback.format_exc()})
    result['complete'] = (not result['failures'] and result['fixture_removed']
                          and result['temporary_user_removed'] and
                          len(result['permission_checks']) == 14
                          and result.get('database_creator_removed') is True
                          and result.get('created_database_removed') is True
                          and len(result['checks']) ==
                          result.get('expected_checks'))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profiles', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = run(args.profiles)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({'complete': result['complete'],
                      'checks': len(result['checks']),
                      'failures': len(result['failures'])}))
    return 0 if result['complete'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
