#!/usr/bin/env python3
##########################################################################
# CDEadmin - Multi-engine Database Administration
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
##########################################################################

"""Table/domain identity and publication replay on a disposable database."""

import argparse
import json
import traceback
import uuid
from pathlib import Path, PurePosixPath
from types import SimpleNamespace

import sqlparse

from cdeadmin_firebird_admin_mapping_gate import (
    _create_client, _route_arguments, _resources,
)


def run(profiles):
    import firebird.driver as driver
    document = json.loads(profiles.read_text())
    route = next(dict(item) for item in document['profiles']
                 if item['engine'] == 'firebird')
    route.setdefault('host', document.get('host', '127.0.0.1'))
    filename = 'cde_table_metadata_' + uuid.uuid4().hex + '.fdb'
    route['database'] = str(PurePosixPath(route['database']).parent / filename)
    _create_client(SimpleNamespace(acquire_secret=None))
    connection = driver.create_database(password=route['password'],
                                        **_route_arguments(route, driver))
    result = {'passed': False, 'checks': [], 'failures': [],
              'database': route['database'], 'fixture_removed': False}

    def execute(sql, parameters=()):
        with connection.cursor() as cursor:
            cursor.execute(sql, parameters)
        connection.commit()

    def rows(sql, parameters=()):
        with connection.cursor() as cursor:
            cursor.execute(sql, parameters)
            value = cursor.fetchall()
        connection.commit()
        return value

    def table():
        resources = _resources(connection, {'route': route})
        connection.commit()
        return next(item['native'] for item in resources if
                    item['resource_kind'] == 'table' and
                    item['display_name'] == 'T')

    def replay(source):
        execute('DROP TABLE T')
        # Fixtures here contain no PSQL bodies; split only complete SQL DDL.
        for statement in sqlparse.split(source):
            execute(statement.rstrip().removesuffix(';'))

    def case(label, callback):
        try:
            result['checks'].append({'case': label, **callback()})
        except Exception:
            result['failures'].append({'case': label,
                                       'traceback': traceback.format_exc()})
        finally:
            if connection.main_transaction.is_active():
                connection.rollback()
            if rows("SELECT 1 FROM RDB$RELATIONS WHERE RDB$RELATION_NAME='T'"):
                execute('DROP TABLE T')

    try:
        result['engine_version'] = rows(
            "SELECT RDB$GET_CONTEXT('SYSTEM','ENGINE_VERSION') "
            'FROM RDB$DATABASE')[0][0]
        assert result['engine_version'] == '5.0.4'
        domains = ('rdb$domain', 'RDb$domain', 'rDb$domain', 'Domain Name')
        for domain in domains:
            execute('CREATE DOMAIN "' + domain + '" AS INTEGER DEFAULT 7 '
                    'CHECK (VALUE > 0)')

            def check_domain(domain=domain):
                execute('CREATE TABLE T (V "' + domain + '")')
                ddl = table()['ddl']
                assert '"' + domain + '"' in ddl, ddl
                replay(ddl)
                source = rows('SELECT TRIM(TRAILING FROM RDB$FIELD_SOURCE) '
                              'FROM RDB$RELATION_FIELDS WHERE '
                              "RDB$RELATION_NAME='T' AND RDB$FIELD_NAME='V'")
                assert source == [(domain,)], source
                return {'ddl': ddl, 'domain_preserved': True}

            case('domain-reference-' + domain, check_domain)
            execute('DROP DOMAIN "' + domain + '"')

        for constraint in ('INTEG_custom', 'integ_custom', 'Explicit Key'):
            def check_constraint(constraint=constraint):
                execute('CREATE TABLE T (V INTEGER, CONSTRAINT "' +
                        constraint + '" UNIQUE (V))')
                ddl = table()['ddl']
                replay(ddl)
                names = rows('SELECT TRIM(TRAILING FROM RDB$CONSTRAINT_NAME) '
                             'FROM RDB$RELATION_CONSTRAINTS WHERE '
                             "RDB$RELATION_NAME='T'")
                assert names == [(constraint,)], (names, ddl)
                return {'ddl': ddl, 'constraint_name_preserved': True}

            case('constraint-name-' + constraint, check_constraint)

        def not_null():
            execute('CREATE TABLE T (V INTEGER '
                    'CONSTRAINT "Named NN" NOT NULL)')
            ddl = table()['ddl']
            replay(ddl)
            names = rows('SELECT TRIM(TRAILING FROM RDB$CONSTRAINT_NAME) '
                         'FROM RDB$RELATION_CONSTRAINTS WHERE '
                         "RDB$RELATION_NAME='T'")
            assert names == [('Named NN',)], (names, ddl)
            return {'ddl': ddl, 'not_null_identity_preserved': True}

        case('named-not-null', not_null)
        execute('CREATE TABLE P (ID INTEGER PRIMARY KEY)')

        def constraint_fingerprint():
            return rows('SELECT TRIM(TRAILING FROM C.RDB$CONSTRAINT_NAME), '
                        'TRIM(TRAILING FROM C.RDB$CONSTRAINT_TYPE), '
                        'TRIM(TRAILING FROM C.RDB$INDEX_NAME), '
                        'I.RDB$INDEX_TYPE FROM RDB$RELATION_CONSTRAINTS C '
                        'LEFT JOIN RDB$INDICES I ON '
                        'I.RDB$INDEX_NAME=C.RDB$INDEX_NAME WHERE '
                        "C.RDB$RELATION_NAME='T' ORDER BY 1")

        for kind in ('UNIQUE', 'PRIMARY KEY', 'FOREIGN KEY'):
            for direction in ('ASCENDING', 'DESCENDING'):
                for named_index in (False, True):
                    def check_index(kind=kind, direction=direction,
                                    named_index=named_index):
                        clause = kind + ' (V)'
                        if kind == 'FOREIGN KEY':
                            clause += ' REFERENCES P (ID) ON DELETE CASCADE'
                        if named_index:
                            clause += ' USING ' + direction + ' INDEX "Key IX"'
                        execute('CREATE TABLE T (V INTEGER, CONSTRAINT K ' +
                                clause + ')')
                        before = constraint_fingerprint()
                        ddl = table()['ddl']
                        replay(ddl)
                        after = constraint_fingerprint()
                        assert before == after, (before, after, ddl)
                        return {'ddl': ddl, 'constraints': after}

                    case(f'backing-index-{kind}-{direction}-{named_index}',
                         check_index)

        for generation in ('ALWAYS', 'BY DEFAULT'):
            for explicit in (False, True):
                def check_identity(generation=generation, explicit=explicit):
                    suffix = (' CONSTRAINT "Identity NN" NOT NULL' if
                              explicit else '')
                    execute('CREATE TABLE T (V BIGINT GENERATED ' +
                            generation + ' AS IDENTITY' + suffix + ')')
                    before = constraint_fingerprint()
                    ddl = table()['ddl']
                    replay(ddl)
                    after = constraint_fingerprint()
                    assert before == after, (before, after, ddl)
                    return {'ddl': ddl, 'constraints': after}

                case(f'identity-nullability-{generation}-{explicit}',
                     check_identity)

        def comments():
            execute('CREATE TABLE T (V INTEGER)')
            execute("COMMENT ON TABLE T IS '  table ''é''; text  '")
            execute("COMMENT ON COLUMN T.V IS '  column ''é''; text  '")
            before = table()
            ddl = before['ddl']
            replay(ddl)
            after = table()
            assert after.get('description') == before['description'], ddl
            assert after['columns'][0].get('description') == (
                before['columns'][0]['description']), ddl
            return {'ddl': ddl, 'comments_preserved': True}

        case('table-and-column-comments', comments)

        for enabled in (False, True):
            def check_publication(enabled=enabled):
                policy = 'INCLUDE ALL TO' if enabled else 'EXCLUDE ALL FROM'
                execute('ALTER DATABASE ' + policy + ' PUBLICATION')
                execute('CREATE GLOBAL TEMPORARY TABLE T (V INTEGER) '
                        'ON COMMIT PRESERVE ROWS')
                ddl = table()['ddl']
                inverse = 'EXCLUDE ALL FROM' if enabled else 'INCLUDE ALL TO'
                execute('ALTER DATABASE ' + inverse + ' PUBLICATION')
                replay(ddl)
                membership = rows(
                    'SELECT COUNT(*) FROM RDB$PUBLICATION_TABLES '
                    "WHERE RDB$TABLE_NAME='T'")[0][0]
                assert membership == int(enabled), (membership, ddl)
                return {'ddl': ddl, 'publication_restored': enabled}

            case('temporary-publication-' + str(enabled), check_publication)
    except Exception:
        result['failures'].append({'case': 'infrastructure',
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
    result['passed'] = not result['failures'] and result['fixture_removed']
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profiles', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        result = run(args.profiles)
    except Exception:
        result = {'passed': False, 'checks': [], 'fixture_removed': False,
                  'failures': [{'case': 'initialization',
                                'traceback': traceback.format_exc()}]}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({'passed': result['passed'],
                      'checks': len(result['checks']),
                      'failures': len(result['failures'])}))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
