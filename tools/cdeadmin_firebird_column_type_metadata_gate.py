#!/usr/bin/env python3
##########################################################################
# CDEadmin - Multi-engine Database Administration
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
##########################################################################

"""Check type-editor metadata against native recreated scalar fields."""

import argparse
import json
import traceback
import uuid
from pathlib import Path, PurePosixPath
from types import SimpleNamespace

from cdeadmin_firebird_admin_mapping_gate import (
    _create_client, _route_arguments, _resources,
)
from pgadmin.cdeadmin.providers.firebird import columns
from pgadmin.cdeadmin.providers.firebird.column_type_metadata import (
    type_editor_values,
)


def run(profiles):
    import firebird.driver as driver
    document = json.loads(profiles.read_text())
    route = next(dict(item) for item in document['profiles']
                 if item['engine'] == 'firebird')
    route.setdefault('host', document.get('host', '127.0.0.1'))
    route['database'] = str(PurePosixPath(route['database']).parent /
                            ('cde_type_metadata_' + uuid.uuid4().hex + '.fdb'))
    _create_client(SimpleNamespace(acquire_secret=None))
    connection = driver.create_database(password=route['password'],
                                        **_route_arguments(route, driver))
    result = {'complete': False, 'checks': [], 'routine_checks': [],
              'failures': [],
              'fixture_database': route['database'], 'fixture_removed': False}

    def execute(source):
        with connection.cursor() as cursor:
            cursor.execute(source)
        connection.commit()

    def field(table, kind='column'):
        resources = _resources(connection, {'route': route})
        connection.commit()
        return next(item['native'] for item in resources if
                    item['resource_kind'] == kind and
                    item['display_path'] == (
                        [table, 'V'] if kind == 'column' else [table]))

    cases = [(name, {'data_type': name, 'length': 30, 'domain': 'D'})
             for name in columns.TYPES]
    cases.extend([
        ('NUMERIC38', {'data_type': 'NUMERIC', 'precision': 38, 'scale': 38}),
        ('DECIMAL18', {'data_type': 'DECIMAL', 'precision': 18, 'scale': 3}),
        ('UTF8', {'data_type': 'VARCHAR', 'length': 20,
                  'character_set': 'UTF8'}),
        ('TEXT BLOB', {'data_type': 'BLOB', 'blob_subtype': 1,
                       'segment_size': 120, 'character_set': 'UTF8'}),
        ('TIME TZ', {'data_type': 'TIME', 'time_zone': 'WITH TIME ZONE'}),
        ('TIMESTAMP TZ', {'data_type': 'TIMESTAMP',
                          'time_zone': 'WITH TIME ZONE'}),
        ('DECFLOAT16', {'data_type': 'DECFLOAT', 'precision': 16}),
        ('FLOAT53', {'data_type': 'FLOAT', 'precision': 53}),
    ])
    keys = ('field_type', 'field_sub_type', 'field_length', 'field_scale',
            'field_precision', 'character_length', 'segment_length',
            'character_set')

    def routine_replay(kind, definition):
        keyword = kind.upper()
        created = []
        source = ('CREATE PROCEDURE R1 (P ' + definition + ') RETURNS (O ' +
                  definition + ') AS BEGIN O = P; SUSPEND; END' if
                  kind == 'procedure' else 'CREATE FUNCTION R1 (P ' +
                  definition + ') RETURNS ' + definition +
                  ' AS BEGIN RETURN P; END')
        try:
            execute(source)
            created.append('R1')
            original = field('R1', kind)
            ddl = original['ddl']
            prefix = 'CREATE ' + keyword + ' "R1"'
            assert ddl.startswith(prefix), ddl
            replay = ddl.replace(prefix, 'CREATE ' + keyword + ' "R2"', 1)
            execute(replay.rstrip().rstrip(';'))
            created.append('R2')
            observed = field('R2', kind)

            def signature(value):
                return [{key: parameter.get(key) for key in (
                    'name', 'position', 'mode', 'return_value', *keys)}
                        for parameter in value['parameters']]

            expected = signature(original)
            actual = signature(observed)
            assert expected == actual, (expected, actual, ddl)
            return {'kind': kind, 'ddl': ddl, 'parameters': actual}
        finally:
            if connection.main_transaction.is_active():
                connection.rollback()
            for name in reversed(created):
                execute('DROP ' + keyword + ' ' + name)
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT RDB$GET_CONTEXT('SYSTEM','ENGINE_VERSION') "
                           'FROM RDB$DATABASE')
            result['engine_version'] = cursor.fetchone()[0]
        connection.commit()
        assert result['engine_version'] == '5.0.4'
        execute('CREATE DOMAIN D AS BIGINT')
        for label, draft in cases:
            created = []
            try:
                definition = columns.data_type(draft)
                execute('CREATE TABLE T (V ' + definition + ')')
                created.append('T')
                original = field('T')
                values = type_editor_values(original)
                assert values, ('missing editor type', original)
                recreated = columns.data_type(values)
                execute('CREATE TABLE U (V ' + recreated + ')')
                created.append('U')
                observed = field('U')
                expected = {key: original.get(key) for key in keys}
                actual = {key: observed.get(key) for key in keys}
                assert expected == actual, (expected, actual)
                execute('DROP TABLE U')
                created.remove('U')
                ddl = field('T', 'table')['ddl']
                assert ddl.startswith('CREATE TABLE "T"')
                replay = ddl.replace('CREATE TABLE "T"',
                                     'CREATE TABLE "U"', 1)
                execute(replay.rstrip().rstrip(';'))
                created.append('U')
                from_metadata = field('U')
                assert {key: from_metadata.get(key) for key in keys} == (
                    expected), (label, ddl, expected, from_metadata)
                result['checks'].append({'case': label, 'source': definition,
                                         'values': values,
                                         'recreated': recreated,
                                         'native_fields': actual,
                                         'metadata_ddl': ddl,
                                         'metadata_replay': True})
            except Exception:
                result['failures'].append({
                    'case': label, 'traceback': traceback.format_exc()})
                if connection.main_transaction.is_active():
                    connection.rollback()
            finally:
                for name in reversed(created):
                    execute('DROP TABLE ' + name)
            for kind in ('procedure', 'function'):
                try:
                    result['routine_checks'].append({
                        'case': label, **routine_replay(kind, definition)})
                except Exception:
                    result['failures'].append({
                        'case': label + '-' + kind,
                        'traceback': traceback.format_exc()})
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
                          and len(result['checks']) == len(cases) and
                          len(result['routine_checks']) == 2 * len(cases))
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
