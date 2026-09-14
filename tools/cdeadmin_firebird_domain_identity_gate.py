#!/usr/bin/env python3
##########################################################################
# CDEadmin - Multi-engine Database Administration
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
##########################################################################

"""Verify domain rename continuity in an exclusively owned native database."""

import argparse
import json
import traceback
import uuid
from pathlib import Path, PurePosixPath
from types import SimpleNamespace

from cdeadmin_firebird_admin_mapping_gate import (
    ADMINISTRATION, _create_client, _route_arguments,
)
from pgadmin.cdeadmin.providers.firebird.mappings import identifier
from pgadmin.cdeadmin.security.secrets import SecretLease
from pgadmin.cdeadmin.sdk.relational import RelationalClientError


def run(profiles):
    import firebird.driver as driver
    document = json.loads(profiles.read_text())
    route = next(dict(item) for item in document['profiles']
                 if item['engine'] == 'firebird')
    route.setdefault('host', document.get('host', '127.0.0.1'))
    route['database'] = str(PurePosixPath(route['database']).parent /
                            ('cde_domain_id_' + uuid.uuid4().hex + '.fdb'))
    password = route.pop('password')
    client = _create_client(SimpleNamespace(
        acquire_secret=lambda *_args: SecretLease(password)))
    connection = driver.create_database(password=password,
                                        **_route_arguments(route, driver))
    trusted = {**route, 'credential_reference_id': 'owned-domain-secret',
               'principal_reference': 'owned-domain-principal'}
    result = {'complete': False, 'cases': [], 'failures': [],
              'fixture_database': route['database'], 'fixture_removed': False}

    def execute(sql, params=(), fetch=False):
        with connection.cursor() as cursor:
            cursor.execute(sql, params)
            return list(cursor.fetchall()) if fetch else None

    def observe(name):
        fields = execute(
            'SELECT RDB$FIELD_TYPE, RDB$FIELD_SUB_TYPE, RDB$FIELD_LENGTH, '
            'RDB$FIELD_SCALE, RDB$DEFAULT_SOURCE, RDB$VALIDATION_SOURCE, '
            'RDB$NULL_FLAG, RDB$DESCRIPTION FROM RDB$FIELDS '
            'WHERE RDB$FIELD_NAME = ?', (name,), True)
        dimensions = execute(
            'SELECT RDB$DIMENSION, RDB$LOWER_BOUND, RDB$UPPER_BOUND '
            'FROM RDB$FIELD_DIMENSIONS WHERE RDB$FIELD_NAME = ? '
            'ORDER BY RDB$DIMENSION', (name,), True)
        source = execute(
            'SELECT TRIM(TRAILING FROM RDB$FIELD_SOURCE) '
            "FROM RDB$RELATION_FIELDS WHERE RDB$RELATION_NAME = 'T' "
            "AND RDB$FIELD_NAME = 'V'", fetch=True)
        assert source == [(name,)], source
        assert len(fields) == 1
        return fields, dimensions

    def plan(old, new):
        return ADMINISTRATION.plan({
            '_provider_route': trusted, 'resource_kind': 'domain',
            'operation_id': 'rename', 'target_resource': {
                'display_path': [old]}, 'draft': {'new_name': new},
        })

    cases = [
        ('scalar', 'INTEGER DEFAULT 7 NOT NULL CHECK (VALUE > 0)',
         'W', '7', None),
        ('array', 'INTEGER[-2:3,1:2]', 'Array:%name', None, None),
        ('blob', 'BLOB SUB_TYPE TEXT CHARACTER SET UTF8',
         '文書', "'preserved text'", None),
        ('quoted', 'VARCHAR(40) CHARACTER SET UTF8',
         ' Domain."%:名', "'preserved value'", None),
        ('indexed', 'INTEGER', 'INDEX_DOMAIN', '19',
         'CREATE INDEX IX ON T(V)'),
        ('routine', 'INTEGER', 'ROUTINE_DOMAIN', '23',
         'CREATE PROCEDURE P (X TYPE OF D) RETURNS (Y TYPE OF D) '
         'AS BEGIN Y = X; SUSPEND; END'),
    ]
    try:
        result['engine_version'] = execute(
            "SELECT RDB$GET_CONTEXT('SYSTEM','ENGINE_VERSION') "
            'FROM RDB$DATABASE', fetch=True)[0][0]
        connection.commit()
        assert result['engine_version'] == '5.0.4'
        for label, declaration, new, value, dependency in cases:
            try:
                execute('CREATE DOMAIN D AS ' + declaration)
                execute("COMMENT ON DOMAIN D IS 'retained domain comment'")
                execute('CREATE TABLE T (V D)')
                if dependency:
                    execute(dependency)
                connection.commit()
                if value is not None:
                    execute('INSERT INTO T (V) VALUES (' + value + ')')
                    connection.commit()
                before = observe('D')
                rows = (execute('SELECT V FROM T', fetch=True)
                        if value is not None else [])
                connection.commit()
                staged = ADMINISTRATION.apply(client, plan('D', new),
                                              connection=connection)
                assert staged['resource_identity_change'][
                    'staged_in_provider_session'] is True
                assert observe(new) == before
                connection.rollback()
                assert observe('D') == before
                connection.commit()
                if label == 'routine':
                    try:
                        ADMINISTRATION.apply(client, plan('D', new))
                    except RelationalClientError as error:
                        assert '335544630' in str(error), str(error)
                    else:
                        raise AssertionError('Expected native dependency deny')
                    assert observe('D') == before
                    connection.commit()
                    try:
                        execute('ALTER DOMAIN D TO ' + identifier(new))
                        connection.commit()
                    except driver.DatabaseError as error:
                        assert 335544630 in error.gds_codes
                        connection.rollback()
                    else:
                        raise AssertionError('Raw native rename differed')
                    assert observe('D') == before
                    assert execute('SELECT Y FROM P(41)',
                                   fetch=True) == [(41,)]
                    connection.commit()
                    result['cases'].append({
                        'case': label, 'native_commit_denial_verified': True,
                        'provider_rollback_verified': True,
                    })
                    continue
                applied = ADMINISTRATION.apply(client, plan('D', new))
                receipt = applied['resource_identity_change']
                assert receipt['committed_by_provider'] is True
                assert receipt['record_key_retained'] is False
                assert observe(new) == before
                if value is not None:
                    assert execute('SELECT V FROM T', fetch=True) == rows
                connection.commit()
                ADMINISTRATION.apply(client, plan(new, 'D'))
                assert observe('D') == before
                connection.commit()
                result['cases'].append({
                    'case': label, 'receipt': receipt,
                    'rollback_commit_and_dependents': True})
            except Exception:
                result['failures'].append({'case': label,
                                          'traceback': traceback.format_exc()})
            finally:
                if connection.main_transaction.is_active():
                    connection.rollback()
                # Enumerate only this gate's objects in its owned database.
                if execute("SELECT 1 FROM RDB$PROCEDURES WHERE "
                           "RDB$PROCEDURE_NAME = 'P'", fetch=True):
                    execute('DROP PROCEDURE P')
                if execute("SELECT 1 FROM RDB$RELATIONS WHERE "
                           "RDB$RELATION_NAME = 'T'", fetch=True):
                    execute('DROP TABLE T')
                connection.commit()
                for name in ('D', new):
                    if execute('SELECT 1 FROM RDB$FIELDS '
                               'WHERE RDB$FIELD_NAME = ?', (name,), True):
                        execute('DROP DOMAIN ' + identifier(name))
                connection.commit()
        for label, new in [('duplicate', 'E'), ('reserved-name', 'RDB$NEW')]:
            try:
                execute('CREATE DOMAIN D AS INTEGER')
                execute('CREATE DOMAIN E AS INTEGER')
                execute('CREATE TABLE T (V D)')
                connection.commit()
                native_codes = None
                try:
                    execute('ALTER DOMAIN D TO ' + identifier(new))
                    connection.commit()
                except driver.DatabaseError as error:
                    native_codes = tuple(error.gds_codes)
                    connection.rollback()
                assert native_codes, 'Native invalid rename was not rejected'
                execute('INSERT INTO T VALUES (91)')
                try:
                    ADMINISTRATION.apply(client, plan('D', new),
                                         connection=connection)
                except RelationalClientError as error:
                    assert all(str(code) in str(error)
                               for code in native_codes)
                else:
                    raise AssertionError('Provider invalid rename accepted')
                assert execute('SELECT V FROM T', fetch=True) == [(91,)]
                observe('D')
                connection.rollback()
                assert execute('SELECT V FROM T', fetch=True) == []
                connection.commit()
                result['cases'].append({
                    'case': label, 'native_denial_codes': native_codes,
                    'caller_work_preserved': True})
            except Exception:
                result['failures'].append({'case': label,
                                          'traceback': traceback.format_exc()})
            finally:
                if connection.main_transaction.is_active():
                    connection.rollback()
                execute('DROP TABLE T')
                execute('DROP DOMAIN D')
                execute('DROP DOMAIN E')
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
                          and len(result['cases']) == len(cases) + 2)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profiles', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = run(args.profiles)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))
    return 0 if result['complete'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
