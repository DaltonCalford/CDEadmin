#!/usr/bin/env python3
##########################################################################
# CDEadmin - Multi-engine Database Administration
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
##########################################################################

"""Check native rename receipts, commit/rollback and protected dependencies."""

import argparse
import json
import traceback
import uuid
from pathlib import Path, PurePosixPath
from types import SimpleNamespace

from cdeadmin_firebird_admin_mapping_gate import (
    ADMINISTRATION, _create_client, _route_arguments,
)
from pgadmin.cdeadmin.security.secrets import SecretLease
from pgadmin.cdeadmin.sdk.relational import RelationalClientError


def run(profiles):
    import firebird.driver as driver
    document = json.loads(profiles.read_text())
    route = next(dict(item) for item in document['profiles']
                 if item['engine'] == 'firebird')
    route.setdefault('host', document.get('host', '127.0.0.1'))
    route['database'] = str(PurePosixPath(route['database']).parent /
                            ('cde_rename_id_' + uuid.uuid4().hex + '.fdb'))
    password = route.pop('password')
    client = _create_client(SimpleNamespace(
        acquire_secret=lambda *_args: SecretLease(password)))
    connection = driver.create_database(password=password,
                                        **_route_arguments(route, driver))
    trusted = {**route, 'credential_reference_id': 'owned-rename-secret',
               'principal_reference': 'owned-rename-principal'}
    result = {'complete': False, 'cases': [], 'failures': [],
              'fixture_database': route['database'], 'fixture_removed': False}

    def execute(sql):
        with connection.cursor() as cursor:
            cursor.execute(sql)

    def names():
        with connection.cursor() as cursor:
            cursor.execute('SELECT TRIM(TRAILING FROM RDB$FIELD_NAME) '
                           'FROM RDB$RELATION_FIELDS '
                           "WHERE RDB$RELATION_NAME = 'T' ORDER BY "
                           'RDB$FIELD_POSITION')
            return [row[0] for row in cursor.fetchall()]

    def make_plan(old, new):
        return ADMINISTRATION.plan({
            '_provider_route': trusted, 'resource_kind': 'column',
            'operation_id': 'rename', 'target_resource': {
                'resource_id': 'column:T:' + old,
                'display_path': ['T', old],
            }, 'draft': {'new_name': new},
        })

    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT RDB$GET_CONTEXT('SYSTEM','ENGINE_VERSION') "
                           'FROM RDB$DATABASE')
            result['engine_version'] = cursor.fetchone()[0]
        connection.commit()
        assert result['engine_version'] == '5.0.4'
        for label, declaration, dependency, allowed in (
                ('stored', 'INTEGER', None, True),
                ('identity', 'BIGINT GENERATED ALWAYS AS IDENTITY',
                 None, True),
                ('computed', 'COMPUTED BY (X + 1)', None, True),
                ('array', 'INTEGER[1:3]', None, True),
                ('blob', 'BLOB SUB_TYPE TEXT', None, True),
                ('not-null', 'INTEGER NOT NULL', None, True),
                ('ordinary-index', 'INTEGER', 'CREATE INDEX IX ON T(V)', True),
                ('primary-key', 'INTEGER PRIMARY KEY', None, False),
                ('dependent-view', 'INTEGER',
                 'CREATE VIEW VW AS SELECT V FROM T', False)):
            try:
                execute('CREATE TABLE T (X INTEGER, V ' + declaration + ')')
                if dependency:
                    execute(dependency)
                connection.commit()
                before = names()
                connection.commit()
                if not allowed:
                    denied = False
                    try:
                        ADMINISTRATION.apply(client, make_plan('V', 'W'),
                                             connection=connection)
                    except RelationalClientError as error:
                        expected_code = ('335544342' if label == 'primary-key'
                                         else '336068814')
                        assert expected_code in str(error), str(error)
                        denied = True
                    assert denied, 'Native protected rename unexpectedly ran'
                    assert names() == before
                    connection.rollback()
                    result['cases'].append({'case': label, 'denied': True})
                    continue
                staged = ADMINISTRATION.apply(
                    client, make_plan('V', 'W'), connection=connection)
                receipt = staged['resource_identity_change']
                assert receipt['native_identity_verified'] is True
                assert receipt['committed_by_provider'] is False
                assert receipt['staged_in_provider_session'] is True
                assert names() == ['X', 'W']
                connection.rollback()
                assert names() == before
                connection.commit()
                applied = ADMINISTRATION.apply(client, make_plan('V', 'W'))
                receipt = applied['resource_identity_change']
                assert receipt['committed_by_provider'] is True
                assert receipt['staged_in_provider_session'] is False
                assert receipt['resource_id'] == 'column:T:W'
                assert names() == ['X', 'W']
                connection.commit()
                restored = ADMINISTRATION.apply(client, make_plan('W', 'V'))
                assert restored['resource_identity_change']['field_id'] == (
                    receipt['field_id'])
                assert names() == before
                connection.commit()
                result['cases'].append({'case': label, 'receipt': receipt,
                                        'rollback_and_commit_verified': True})
            except Exception:
                result['failures'].append({'case': label,
                                          'traceback': traceback.format_exc()})
            finally:
                if connection.main_transaction.is_active():
                    connection.rollback()
                if label == 'dependent-view':
                    execute('DROP VIEW VW')
                execute('DROP TABLE T')
                connection.commit()
    except Exception:
        result['failures'].append({'case': 'fixture-lifecycle',
                                   'traceback': traceback.format_exc()})
    finally:
        try:
            if connection.main_transaction.is_active():
                connection.rollback()
            connection.drop_database()
            result['fixture_removed'] = True
        except Exception:
            result['failures'].append({'case': 'fixture-cleanup',
                                       'traceback': traceback.format_exc()})
    result['complete'] = (not result['failures'] and result['fixture_removed']
                          and len(result['cases']) == 9)
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
                      'cases': len(result['cases']),
                      'fixture_removed': result['fixture_removed'],
                      'failures': result['failures']}))
    return 0 if result['complete'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
