#!/usr/bin/env python3
"""Round-trip non-table Firebird catalog DDL on stored dialects 1 and 3."""
import argparse
import json
import re
from pathlib import Path

if __package__:
    from . import cdeadmin_firebird_views_gate as base
else:
    import cdeadmin_firebird_views_gate as base


def cases():
    # Definitions are native input, not output from the renderer being tested.
    return [
        ('domain', 'D', "CREATE DOMAIN D AS VARCHAR(20) DEFAULT 'A\"B' "
         'NOT NULL CHECK (CHAR_LENGTH(VALUE) > 0)', 'DROP DOMAIN D',
         ('field_type', 'field_length', 'character_length', 'character_set',
          'default_source', 'not_null', 'validation_source', 'collation',
          'description')),
        ('sequence', 'S', 'CREATE SEQUENCE S START WITH 7 INCREMENT BY 3',
         'DROP SEQUENCE S', ('initial_value', 'increment', 'description')),
        ('exception', 'E', "CREATE EXCEPTION E 'Exact A\"B; message'",
         'DROP EXCEPTION E', ('message', 'description')),
        ('role', 'R', 'CREATE ROLE R', 'DROP ROLE R',
         ('owner', 'system_privileges', 'description')),
        ('collation', 'C', 'CREATE COLLATION C FOR UTF8 FROM UNICODE '
         'NO PAD CASE INSENSITIVE ACCENT INSENSITIVE', 'DROP COLLATION C',
         ('attributes', 'base_collation', 'specific_attributes',
          'character_set', 'description')),
        ('authentication-mapping', 'M', 'CREATE MAPPING M USING ANY PLUGIN '
         "FROM USER 'CDE_NOLOGIN' TO USER SYSDBA", 'DROP MAPPING M',
         ('using', 'plugin', 'source_database', 'from_type', 'from',
          'to_type', 'to', 'description')),
        ('blob-filter', 'F', 'DECLARE FILTER F INPUT_TYPE -20 OUTPUT_TYPE -21 '
         "ENTRY_POINT 'not_invoked' MODULE_NAME 'not_loaded'", 'DROP FILTER F',
         ('input_subtype', 'output_subtype', 'entrypoint', 'module_name',
          'description')),
        ('character-set', 'UTF8', None, None,
         ('default_collation', 'description', 'bytes_per_character')),
        ('package', 'P', [
            'CREATE PACKAGE P SQL SECURITY INVOKER AS '
            'BEGIN FUNCTION F(X INTEGER) RETURNS INTEGER; '
            'PROCEDURE Z(X INTEGER) RETURNS(Y INTEGER); END',
            'CREATE PACKAGE BODY P AS BEGIN '
            'FUNCTION F(X INTEGER) RETURNS INTEGER AS '
            "BEGIN /* Keep ; and 'quotes' */ RETURN X + 2; END "
            'PROCEDURE Z(X INTEGER) RETURNS(Y INTEGER) AS '
            'BEGIN Y = X + 6; END END'],
         'DROP PACKAGE P', ('header_source', 'body_source', 'description',
                            'sql_security', 'package_sql_security',
                            'member_comments')),
        ('package', 'PH', [
            'CREATE PACKAGE PH SQL SECURITY DEFINER AS '
            'BEGIN FUNCTION F(X INTEGER) RETURNS INTEGER; END'],
         'DROP PACKAGE PH', ('header_source', 'body_source', 'description',
                             'sql_security', 'package_sql_security',
                             'member_comments')),
        ('function', 'FUN', 'CREATE FUNCTION FUN(X INTEGER) RETURNS INTEGER '
         'SQL SECURITY INVOKER AS BEGIN RETURN X + 3; END',
         'DROP FUNCTION FUN', ('metadata_source', 'sql_security',
                               'description', 'parameters')),
        ('procedure', 'PR', 'CREATE PROCEDURE PR(X INTEGER) '
         'RETURNS(Y INTEGER) '
         'SQL SECURITY INVOKER AS BEGIN Y = X + 4; END',
         'DROP PROCEDURE PR', ('metadata_source', 'sql_security',
                               'description', 'parameters')),
        ('trigger', 'TR', 'CREATE TRIGGER TR FOR SENTINEL INACTIVE '
         'BEFORE UPDATE POSITION 0 SQL SECURITY INVOKER '
         'AS BEGIN NEW.X = OLD.X; END',
         'DROP TRIGGER TR', ('metadata_source', 'sql_security', 'inactive',
                             'trigger_type', 'position', 'description')),
    ]


def comparable_fields(native, fields):
    """Ignore only engine-allocated implicit-domain sequence numbers."""
    values = {field: native.get(field) for field in fields}
    if 'parameters' in values:
        values['parameters'] = [
            {**parameter, 'domain': '<implicit-domain>'}
            if re.fullmatch(r'RDB\$[0-9]+', parameter.get('domain') or '')
            else dict(parameter) for parameter in values['parameters']]
    return values


def verify(connection, client, route, password, result):
    import firebird.driver as native
    from firebird.driver import core
    from pgadmin.cdeadmin.providers.firebird.database_creation import (
        create_database,
    )
    from pgadmin.cdeadmin.providers.firebird.provider import (
        _database_create_arguments, _route_arguments, _resources,
    )

    checks = result['catalog_dialect_checks'] = []
    for dialect in (1, 3):
        configured = {**route, 'database':
                      f'/var/lib/firebird/data/catalog_{dialect}.fdb'}
        args = _database_create_arguments(
            configured, _route_arguments(configured)['database'],
            {'sql_dialect': dialect}, native)
        created = create_database(native, core, password=password, **args)
        created.close()
        handle = client.open_session({'route': configured})

        def sql(source):
            with handle.cursor() as cursor:
                cursor.execute(source)
                return cursor.fetchall() if cursor.description else []

        def rollback():
            if handle.main_transaction.is_active():
                handle.rollback()

        def metadata(kind, name):
            resources = _resources(handle, {'route': configured})
            detail = next(item['native'] for item in resources if
                          item['resource_kind'] == kind and
                          item['display_name'] == name)
            if kind == 'package':
                detail['member_comments'] = sorted([
                    {'kind': item['resource_kind'],
                     'name': item['display_name'],
                     'description': item['native'].get('description'),
                     'parameters': [
                         {'name': p['name'],
                          'description': p.get('description')}
                         for p in item['native'].get('parameters', [])]}
                    for item in resources if item['resource_kind'] in {
                        'function', 'procedure'} and
                    item['native'].get('package') == name],
                    key=lambda member: (member['kind'], member['name']))
            return detail

        def behavior(name):
            sources = {
                'P': ('SELECT P.F(5) FROM RDB$DATABASE', 7),
                'FUN': ('SELECT FUN(5) FROM RDB$DATABASE', 8),
                'PR': ('EXECUTE PROCEDURE PR(5)', 9),
            }
            if name in sources:
                source, expected = sources[name]
                assert sql(source) == [(expected,)]
            if name == 'P':
                assert sql('EXECUTE PROCEDURE P.Z(5)') == [(11,)]

        try:
            sql('CREATE TABLE SENTINEL (X INTEGER)')
            handle.commit()
            for kind, name, create, drop, fields in cases():
                check = {'dialect': dialect, 'kind': kind, 'name': name,
                         'passed': False}
                checks.append(check)
                try:
                    if create:
                        for statement in (create if isinstance(create, list)
                                          else [create]):
                            sql(statement)
                        handle.commit()
                    if kind in {'role', 'collation', 'blob-filter', 'sequence',
                                'package', 'domain', 'exception', 'function',
                                'procedure', 'trigger',
                                'authentication-mapping'}:
                        noun = {'blob-filter': 'FILTER',
                                'authentication-mapping': 'MAPPING'}.get(
                                    kind, kind.upper())
                        sql('COMMENT ON ' + noun + ' ' + name +
                            ' IS \'Exact A"B; it\'\'s preserved\'')
                        handle.commit()
                    before = metadata(kind, name)
                    if kind == 'package':
                        targets = [('FUNCTION', 'F', ('X',))]
                        if name == 'P':
                            targets.append(('PROCEDURE', 'Z', ('X', 'Y')))
                        for noun, member, parameters in targets:
                            sql(f'COMMENT ON {noun} {name}.{member} '
                                "IS 'Member ; it''s preserved'")
                            for parameter in parameters:
                                sql(f'COMMENT ON {noun} PARAMETER '
                                    f'{name}.{member}.{parameter} '
                                    "IS 'Member parameter ; it''s preserved'")
                        handle.commit()
                        before = metadata(kind, name)
                        assert len(before['member_comments']) == len(targets)
                        for member in before['member_comments']:
                            assert member['description'] == (
                                "Member ; it's preserved")
                            for parameter in member['parameters']:
                                if parameter['name']:
                                    assert parameter['description'] == (
                                        "Member parameter ; it's preserved")
                    if kind in {'procedure', 'function'}:
                        parameter_names = ('X', 'Y') if kind == (
                            'procedure') else ('X',)
                        for parameter_name in parameter_names:
                            sql(f'COMMENT ON {kind.upper()} PARAMETER '
                                f'{name}.{parameter_name} '
                                "IS 'Parameter ; it''s preserved'")
                        handle.commit()
                        before = metadata(kind, name)
                        for parameter in before['parameters']:
                            if parameter['name'] in parameter_names:
                                assert parameter['description'] == (
                                    "Parameter ; it's preserved")
                    if kind == 'package':
                        check['observed_security'] = {
                            field: before.get(field) for field in
                            ('sql_security', 'package_sql_security')}
                        assert before['package_sql_security'] == (
                            'INVOKER' if name == 'P' else 'DEFINER'), check
                    behavior(name)
                    statements = before.get('recreation_statements') or [
                        before['ddl'].rstrip().rstrip(';')]
                    check['statements'] = statements
                    expected = comparable_fields(before, fields)
                    check['native_before'] = {
                        field: before.get(field) for field in fields}
                    rollback()
                    # Pending work is preserved by rendering and staging DDL.
                    sql('INSERT INTO SENTINEL VALUES (42)')
                    transaction = handle.main_transaction.info.id
                    assert metadata(kind, name)['ddl'] == before['ddl']
                    assert handle.main_transaction.info.id == transaction
                    if drop:
                        sql(drop)
                    for statement in statements:
                        sql(statement)
                    assert sql('SELECT X FROM SENTINEL') == [(42,)]
                    rollback()
                    assert sql('SELECT X FROM SENTINEL') == []
                    restored = metadata(kind, name)
                    restored_fields = comparable_fields(restored, fields)
                    assert restored_fields == expected, {
                        'stage': 'rollback', 'expected': expected,
                        'actual': restored_fields}
                    rollback()
                    if drop:
                        sql(drop)
                        handle.commit()
                    for statement in statements:
                        sql(statement)
                    handle.commit()
                    after = metadata(kind, name)
                    behavior(name)
                    check['native_after'] = {
                        field: after.get(field) for field in fields}
                    after_fields = comparable_fields(after, fields)
                    assert after_fields == expected, {
                        'stage': 'committed-recreation', 'expected': expected,
                        'actual': after_fields}
                    assert handle.sql_dialect == 3
                    assert handle.info.sql_dialect == dialect
                    check.update(passed=True, pending_work_preserved=True,
                                 rollback_commit_verified=True,
                                 metadata_identity_verified=True)
                except Exception as exc:
                    result['failures'].append({
                        'case': f'catalog-{dialect}-{kind}-{name}',
                        'error_type': type(exc).__name__,
                        'message': str(exc).replace(password, '<redacted>')})
                finally:
                    rollback()
        finally:
            client.close_session(handle)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        parser.error('Refusing to overwrite evidence')
    result = base.run(extra_checks=verify)
    checks = result.get('catalog_dialect_checks', [])
    result['complete'] = (result['complete'] and
                          len(checks) == 2 * len(cases()) and
                          all(check['passed'] for check in checks))
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({'complete': result['complete'],
                      'failures': result['failures']}))
    return 0 if result['complete'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
