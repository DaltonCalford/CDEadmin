#!/usr/bin/env python3
##########################################################################
# CDEadmin - Multi-engine Database Administration
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
##########################################################################

"""Compare native and leased-client CREATE DATABASE grant paths safely."""

import argparse
import json
import traceback
import uuid
from pathlib import Path, PurePosixPath
from types import SimpleNamespace

from cdeadmin_firebird_admin_mapping_gate import (
    ADMINISTRATION, _create_client, _route_arguments,
)
from pgadmin.cdeadmin.providers.firebird.provider import _initialize_connection
from pgadmin.cdeadmin.providers.firebird.mappings import identifier
from pgadmin.cdeadmin.security.secrets import SecretLease


def run(profiles):
    import firebird.driver as driver
    document = json.loads(profiles.read_text())
    route = next(dict(item) for item in document['profiles']
                 if item['engine'] == 'firebird')
    route.setdefault('host', document.get('host', '127.0.0.1'))
    route['database'] = str(PurePosixPath(route['database']).parent /
                            ('cde_creator_path_' + uuid.uuid4().hex + '.fdb'))
    password = route.pop('password')
    client = _create_client(SimpleNamespace(
        acquire_secret=lambda *_args: SecretLease(password)))
    admin = driver.create_database(password=password,
                                   **_route_arguments(route, driver))
    role = 'CDE_CREATOR_' + uuid.uuid4().hex[:20].upper()
    result = {'complete': False, 'observations': [], 'failures': [],
              'qualification_claim': False,
              'purpose': 'diagnose database-local versus security-role scope',
              'fixture_database': route['database'], 'fixture_removed': False,
              'database_creator_removed': False}
    sql = 'GRANT CREATE DATABASE TO ROLE ' + identifier(role)
    leased_route = {**route, 'credential_reference_id': 'owned-test-secret',
                    'principal_reference': 'owned-test-principal'}

    def remove_creator():
        if admin.main_transaction.is_active():
            admin.rollback()
        with admin.cursor() as cursor:
            cursor.execute('SELECT COUNT(*) FROM SEC$DB_CREATORS '
                           'WHERE SEC$USER = ? AND SEC$USER_TYPE = 13',
                           (role,))
            exists = cursor.fetchone()[0]
        admin.commit()
        if exists:
            with admin.cursor() as cursor:
                cursor.execute('REVOKE CREATE DATABASE FROM ROLE ' +
                               identifier(role))
            admin.commit()
        with admin.cursor() as cursor:
            cursor.execute('SELECT COUNT(*) FROM SEC$DB_CREATORS '
                           'WHERE SEC$USER = ? AND SEC$USER_TYPE = 13',
                           (role,))
            assert cursor.fetchone()[0] == 0
        admin.commit()

    try:
        with admin.cursor() as cursor:
            cursor.execute('CREATE ROLE ' + identifier(role))
        admin.commit()
        for mode in ('direct', 'initialized', 'leased', 'leased-executor'):
            connection = None
            observation = {'mode': mode}
            try:
                if mode == 'leased-executor':
                    plan = ADMINISTRATION.plan({
                        '_provider_route': leased_route,
                        'resource_kind': 'privilege', 'operation_id': 'grant',
                        'draft': {'privilege_scope': 'database',
                                  'database_privileges': ['CREATE'],
                                  'principal_kind': 'ROLE',
                                  'principal': role}})
                    ADMINISTRATION.apply(client, plan)
                else:
                    connection = client._connect({'route': leased_route}) if (
                        mode == 'leased') else driver.connect(
                            password=password,
                            **_route_arguments(route, driver))
                    if mode == 'initialized':
                        _initialize_connection(connection, route, driver)
                    with connection.cursor() as cursor:
                        cursor.execute('SELECT CURRENT_USER, CURRENT_ROLE '
                                       'FROM RDB$DATABASE')
                        observation['identity'] = cursor.fetchone()
                    connection.commit()
                    with connection.cursor() as cursor:
                        cursor.execute(sql)
                    connection.commit()
                observation['accepted'] = True
            except Exception as error:
                observation.update(accepted=False,
                                   error_type=type(error).__name__,
                                   gds_codes=getattr(error, 'gds_codes', ()),
                                   message=str(error))
            finally:
                if connection is not None:
                    if connection.main_transaction.is_active():
                        connection.rollback()
                    client._forget_and_close(connection)
                remove_creator()
            result['observations'].append(observation)
    except Exception:
        result['failures'].append({'traceback': traceback.format_exc()})
    finally:
        try:
            remove_creator()
            result['database_creator_removed'] = True
        except Exception:
            result['failures'].append({'case': 'creator-cleanup',
                                       'owned_role': role,
                                       'traceback': traceback.format_exc()})
        try:
            if admin.main_transaction.is_active():
                admin.rollback()
            admin.drop_database()
            result['fixture_removed'] = True
        except Exception:
            result['failures'].append({'case': 'database-cleanup',
                                       'traceback': traceback.format_exc()})
    result['complete'] = (not result['failures'] and result['fixture_removed']
                          and result['database_creator_removed'] and
                          len(result['observations']) == 4)
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
                      'observations': result['observations']}))
    return 0 if result['complete'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
