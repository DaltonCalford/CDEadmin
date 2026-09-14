#!/usr/bin/env python3
##########################################################################
# CDEadmin - Multi-engine Database Administration
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
##########################################################################

"""Check native metadata BLOBs larger than the old catalog casts allowed."""

import argparse
import hashlib
import json
import traceback
import uuid
from pathlib import Path, PurePosixPath
from types import SimpleNamespace

from cdeadmin_firebird_admin_mapping_gate import (
    _create_client, _route_arguments, _resources,
)


def run(profiles, repetitions=1000):
    document = json.loads(profiles.read_text())
    route = next(dict(item) for item in document['profiles']
                 if item['engine'] == 'firebird')
    route.setdefault('host', document.get('host', '127.0.0.1'))
    route['database'] = str(PurePosixPath(route['database']).parent /
                            ('cdeadmin_metadata_' + uuid.uuid4().hex + '.fdb'))
    _create_client(SimpleNamespace(acquire_secret=None))
    import firebird.driver as driver
    connection = driver.create_database(
        password=route['password'], **_route_arguments(route, driver))
    result = {'schema': 'cdeadmin.firebird-long-metadata.v1',
              'passed': False, 'checks': [], 'failures': [],
              'fixture_database': route['database'], 'fixture_removed': False}
    text = 'start ' + ('metadata é ' * repetitions) + ' end '
    result['text_length'] = len(text)
    quoted = "'" + text.replace("'", "''") + "'"
    # UTF8 default-value constants have a native 16,383-character ceiling;
    # catalog comments and PSQL source have different native limits.
    default_literal = "'" + text[:11011].replace("'", "''") + "'"
    statements = [
        'CREATE TABLE META_TABLE (ID INTEGER, V BLOB SUB_TYPE TEXT)',
        f'COMMENT ON TABLE META_TABLE IS {quoted}',
        f'COMMENT ON COLUMN META_TABLE.V IS {quoted}',
        f'ALTER TABLE META_TABLE ALTER COLUMN V SET DEFAULT {default_literal}',
        'CREATE VIEW META_VIEW AS SELECT ID, V /*' + text +
        '*/ FROM META_TABLE',
        f'COMMENT ON VIEW META_VIEW IS {quoted}',
        'CREATE DOMAIN META_DOMAIN AS BLOB SUB_TYPE TEXT DEFAULT '
        f'{default_literal} '
        'CHECK (VALUE IS NULL /*' + text + '*/ OR CHAR_LENGTH(VALUE) > 0)',
        f'COMMENT ON DOMAIN META_DOMAIN IS {quoted}',
        'CREATE TRIGGER META_TRIGGER FOR META_TABLE BEFORE INSERT AS '
        'BEGIN /*' + text + '*/ NEW.ID = 1; END',
        f'COMMENT ON TRIGGER META_TRIGGER IS {quoted}',
        'CREATE PROCEDURE META_PROC RETURNS (V INTEGER) AS BEGIN /*' + text +
        '*/ V = 1; SUSPEND; END',
        f'COMMENT ON PROCEDURE META_PROC IS {quoted}',
        'CREATE FUNCTION META_FUNC RETURNS INTEGER AS BEGIN /*' + text +
        '*/ RETURN 1; END',
        f'COMMENT ON FUNCTION META_FUNC IS {quoted}',
        'CREATE PACKAGE META_PACKAGE AS BEGIN /*' + text +
        '*/ FUNCTION F RETURNS INTEGER; END',
        'CREATE PACKAGE BODY META_PACKAGE AS BEGIN FUNCTION F RETURNS INTEGER '
        'AS BEGIN /*' + text + '*/ RETURN 1; END END',
        f'COMMENT ON PACKAGE META_PACKAGE IS {quoted}',
        "CREATE EXCEPTION META_EXCEPTION 'test'",
        f'COMMENT ON EXCEPTION META_EXCEPTION IS {quoted}',
    ]
    expected = [
        ('table', 'META_TABLE', 'description', 'RDB$RELATIONS',
         'RDB$RELATION_NAME', 'RDB$DESCRIPTION'),
        ('view', 'META_VIEW', 'definition', 'RDB$RELATIONS',
         'RDB$RELATION_NAME', 'RDB$VIEW_SOURCE'),
        ('view', 'META_VIEW', 'description', 'RDB$RELATIONS',
         'RDB$RELATION_NAME', 'RDB$DESCRIPTION'),
        ('column', 'V', 'description', 'RDB$RELATION_FIELDS',
         'RDB$RELATION_NAME', 'RDB$DESCRIPTION'),
        ('column', 'V', 'default_source', 'RDB$RELATION_FIELDS',
         'RDB$RELATION_NAME', 'RDB$DEFAULT_SOURCE'),
        ('domain', 'META_DOMAIN', 'default_source', 'RDB$FIELDS',
         'RDB$FIELD_NAME', 'RDB$DEFAULT_SOURCE'),
        ('domain', 'META_DOMAIN', 'validation_source', 'RDB$FIELDS',
         'RDB$FIELD_NAME', 'RDB$VALIDATION_SOURCE'),
        ('domain', 'META_DOMAIN', 'description', 'RDB$FIELDS',
         'RDB$FIELD_NAME', 'RDB$DESCRIPTION'),
        ('trigger', 'META_TRIGGER', 'metadata_source', 'RDB$TRIGGERS',
         'RDB$TRIGGER_NAME', 'RDB$TRIGGER_SOURCE'),
        ('trigger', 'META_TRIGGER', 'description', 'RDB$TRIGGERS',
         'RDB$TRIGGER_NAME', 'RDB$DESCRIPTION'),
        ('procedure', 'META_PROC', 'metadata_source', 'RDB$PROCEDURES',
         'RDB$PROCEDURE_NAME', 'RDB$PROCEDURE_SOURCE'),
        ('procedure', 'META_PROC', 'description', 'RDB$PROCEDURES',
         'RDB$PROCEDURE_NAME', 'RDB$DESCRIPTION'),
        ('function', 'META_FUNC', 'metadata_source', 'RDB$FUNCTIONS',
         'RDB$FUNCTION_NAME', 'RDB$FUNCTION_SOURCE'),
        ('function', 'META_FUNC', 'description', 'RDB$FUNCTIONS',
         'RDB$FUNCTION_NAME', 'RDB$DESCRIPTION'),
        ('package', 'META_PACKAGE', 'header_source', 'RDB$PACKAGES',
         'RDB$PACKAGE_NAME', 'RDB$PACKAGE_HEADER_SOURCE'),
        ('package', 'META_PACKAGE', 'body_source', 'RDB$PACKAGES',
         'RDB$PACKAGE_NAME', 'RDB$PACKAGE_BODY_SOURCE'),
        ('package', 'META_PACKAGE', 'description', 'RDB$PACKAGES',
         'RDB$PACKAGE_NAME', 'RDB$DESCRIPTION'),
        ('exception', 'META_EXCEPTION', 'description', 'RDB$EXCEPTIONS',
         'RDB$EXCEPTION_NAME', 'RDB$DESCRIPTION'),
    ]
    threshold = driver.driver_config.stream_blob_threshold.value
    try:
        result['phase'] = 'create-fixture'
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT RDB$GET_CONTEXT('SYSTEM', 'ENGINE_VERSION') "
                'FROM RDB$DATABASE')
            result['engine_version'] = cursor.fetchone()[0]
            for sql in statements:
                cursor.execute(sql)
                connection.commit()
        result['phase'] = 'catalog-read'
        driver.driver_config.stream_blob_threshold.value = 1
        resources = _resources(connection, {'route': route})
        connection.commit()
        for kind, name, field, relation, name_field, source_field in expected:
            target = next((item for item in resources if
                           item['resource_kind'] == kind and
                           item['display_name'] == name and
                           (kind != 'column' or item['display_path'] ==
                            ['META_TABLE', 'V'])), None)
            with connection.cursor() as cursor:
                extra = " AND RDB$FIELD_NAME = 'V'" if kind == 'column' else ''
                cursor.execute(f'SELECT {source_field} FROM {relation} '
                               f'WHERE {name_field} = ?{extra}',
                               ('META_TABLE' if kind == 'column' else name,))
                native = cursor.fetchone()[0]
                if callable(getattr(native, 'read', None)):
                    blob = native
                    native = blob.read()
                    blob.close()
            connection.commit()
            actual = target.get('native', {}).get(field) if target else None
            entry = {'kind': kind, 'name': name, 'field': field,
                     'native_length': len(native),
                     'native_sha256': hashlib.sha256(
                         native.encode()).hexdigest(),
                     'actual_length': len(actual) if isinstance(actual, str)
                     else None}
            if actual == native:
                result['checks'].append(entry)
            else:
                result['failures'].append(entry)
        result['passed'] = not result['failures']
    except Exception as error:
        result['failures'].append({'type': type(error).__name__,
                                   'message': str(error),
                                   'traceback': traceback.format_exc()})
    finally:
        driver.driver_config.stream_blob_threshold.value = threshold
        connection.drop_database()
        result['fixture_removed'] = True
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profiles', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--repetitions', type=int, choices=(1000, 5000),
                        default=1000)
    args = parser.parse_args()
    result = run(args.profiles, args.repetitions)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))
    return 0 if result['passed'] and result['fixture_removed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
