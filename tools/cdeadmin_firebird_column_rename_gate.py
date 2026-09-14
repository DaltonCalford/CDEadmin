#!/usr/bin/env python3
##########################################################################
# CDEadmin - Multi-engine Database Administration
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
##########################################################################

"""Native rename observations on an owned disposable Firebird database."""

import argparse
import json
import traceback
import uuid
from pathlib import Path, PurePosixPath
from types import SimpleNamespace

from cdeadmin_firebird_admin_mapping_gate import (
    _create_client, _route_arguments,
)
from pgadmin.cdeadmin.providers.firebird.mappings import identifier
from pgadmin.cdeadmin.providers.firebird.provider import (
    _materialize_catalog_value,
)


def run(profiles):
    import firebird.driver as driver
    document = json.loads(profiles.read_text())
    route = next(dict(item) for item in document['profiles']
                 if item['engine'] == 'firebird')
    route.setdefault('host', document.get('host', '127.0.0.1'))
    route['database'] = str(PurePosixPath(route['database']).parent /
                            ('cde_rename_' + uuid.uuid4().hex + '.fdb'))
    _create_client(SimpleNamespace(acquire_secret=None))
    connection = driver.create_database(password=route['password'],
                                        **_route_arguments(route, driver))
    tpb = driver.tpb(driver.Isolation.SNAPSHOT, lock_timeout=5)
    connection.default_tpb = tpb
    connection.main_transaction.default_tpb = tpb
    result = {'complete': False, 'observations': [], 'failures': [],
              'fixture_database': route['database'], 'fixture_removed': False,
              'qualification_claim': False}

    def execute(source):
        with connection.cursor() as cursor:
            cursor.execute(source)

    def state():
        with connection.cursor() as cursor:
            cursor.execute('SELECT TRIM(TRAILING FROM RDB$FIELD_NAME), '
                           'RDB$FIELD_POSITION FROM RDB$RELATION_FIELDS '
                           "WHERE RDB$RELATION_NAME = 'T' ORDER BY 2")
            fields = cursor.fetchall()
        connection.commit()
        return fields

    def data(name='V'):
        with connection.cursor() as cursor:
            cursor.execute('SELECT X, ' + identifier(name) + ' FROM T')
            value = [_materialize_catalog_value(item)
                     for item in cursor.fetchone()]
        connection.commit()
        return value

    variants = {
        'stored': 'INTEGER', 'identity': 'BIGINT GENERATED ALWAYS AS IDENTITY',
        'computed': 'INTEGER COMPUTED BY (X + 1)',
        'primary': 'INTEGER PRIMARY KEY', 'not-null': 'INTEGER NOT NULL',
        'unique': 'INTEGER UNIQUE', 'foreign': 'INTEGER REFERENCES P(ID)',
        'check': 'INTEGER CHECK (V > 0)',
        'array': 'INTEGER[1:3]', 'blob': 'BLOB SUB_TYPE TEXT',
        'indexed': 'INTEGER', 'view-dependent': 'INTEGER',
        'trigger-dependent': 'INTEGER', 'procedure-dependent': 'INTEGER',
        'computed-dependent': 'INTEGER, C COMPUTED BY (V + 1)',
    }
    names = ['W', 'select', 'Quote"Name', ' Dot.Name', 'épreuve', 'N' * 63,
             'V', 'X']
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT RDB$GET_CONTEXT('SYSTEM','ENGINE_VERSION') "
                           'FROM RDB$DATABASE')
            result['engine_version'] = cursor.fetchone()[0]
        connection.commit()
        assert result['engine_version'] == '5.0.4'
        execute('CREATE TABLE P (ID INTEGER PRIMARY KEY)')
        connection.commit()
        execute('INSERT INTO P VALUES (7)')
        connection.commit()
        for variant, definition in variants.items():
            execute('CREATE TABLE T (X INTEGER, V ' + definition + ')')
            connection.commit()
            if variant == 'indexed':
                execute('CREATE INDEX IX ON T (V)')
            elif variant == 'view-dependent':
                execute('CREATE VIEW VW AS SELECT V FROM T')
            elif variant == 'trigger-dependent':
                execute('CREATE TRIGGER TR FOR T BEFORE INSERT AS '
                        'BEGIN NEW.V = 1; END')
            elif variant == 'procedure-dependent':
                execute('CREATE PROCEDURE PR RETURNS (O INTEGER) AS BEGIN '
                        'SELECT FIRST 1 V FROM T INTO :O; SUSPEND; END')
            if connection.main_transaction.is_active():
                connection.commit()
            if variant in {'computed', 'identity'}:
                execute('INSERT INTO T (X) VALUES (3)')
            else:
                value = ("'Text é'" if variant == 'blob' else
                         'NULL' if variant == 'array' else '7')
                execute('INSERT INTO T (X, V) VALUES (3, ' + value + ')')
            connection.commit()
            baseline_data = data()
            for name in names:
                observation = {'variant': variant, 'new_name': name}
                try:
                    before = state()
                    source = 'ALTER TABLE T ALTER COLUMN V TO ' + identifier(
                        name)
                    try:
                        execute(source)
                        observation['accepted'] = True
                    except driver.DatabaseError as error:
                        observation.update(accepted=False,
                                           sqlcode=error.sqlcode,
                                           gds_codes=error.gds_codes,
                                           native_message=str(error))
                    finally:
                        if connection.main_transaction.is_active():
                            connection.rollback()
                    assert state() == before, 'rollback changed field names'
                    observation['rollback_preserved'] = True
                    assert data() == baseline_data, 'rollback changed data'
                    if observation['accepted']:
                        execute(source)
                        connection.commit()
                        assert state() == [
                            (name if field == 'V' else field, position)
                            for field, position in before]
                        assert data(name) == baseline_data
                        if variant == 'indexed':
                            with connection.cursor() as cursor:
                                cursor.execute(
                                    'SELECT TRIM(TRAILING FROM '
                                    'RDB$FIELD_NAME) FROM RDB$INDEX_SEGMENTS '
                                    "WHERE RDB$INDEX_NAME = 'IX'")
                                assert cursor.fetchone()[0] == name
                            connection.commit()
                        if variant == 'not-null':
                            with connection.cursor() as cursor:
                                cursor.execute(
                                    'SELECT TRIM(TRAILING FROM '
                                    'CC.RDB$TRIGGER_NAME) FROM '
                                    'RDB$CHECK_CONSTRAINTS CC JOIN '
                                    'RDB$RELATION_CONSTRAINTS RC ON '
                                    'RC.RDB$CONSTRAINT_NAME = '
                                    'CC.RDB$CONSTRAINT_NAME WHERE '
                                    "RC.RDB$RELATION_NAME = 'T' AND "
                                    "RC.RDB$CONSTRAINT_TYPE = 'NOT NULL'")
                                assert cursor.fetchone()[0] == name
                            connection.commit()
                        execute('ALTER TABLE T ALTER COLUMN ' +
                                identifier(name) + ' TO V')
                        connection.commit()
                        assert state() == before
                        assert data() == baseline_data
                        observation['committed_roundtrip'] = True
                    result['observations'].append(observation)
                except Exception:
                    result['failures'].append({
                        'variant': variant, 'new_name': name,
                        'traceback': traceback.format_exc()})
                    if connection.main_transaction.is_active():
                        connection.rollback()
            if variant == 'view-dependent':
                execute('DROP VIEW VW')
                connection.commit()
            elif variant == 'procedure-dependent':
                execute('DROP PROCEDURE PR')
                connection.commit()
            execute('DROP TABLE T')
            connection.commit()
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
    result['complete'] = (not result['failures'] and result['fixture_removed']
                          and len(result['observations']) ==
                          len(variants) * len(names))
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
                      'observations': len(result['observations']),
                      'failures': len(result['failures'])}))
    return 0 if result['complete'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
