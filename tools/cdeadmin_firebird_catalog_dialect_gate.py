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


def udr_package_header():
    return ('BEGIN FUNCTION F(A INTEGER, B INTEGER, C INTEGER) '
            'RETURNS INTEGER; '
            'FUNCTION G(X INTEGER) RETURNS INTEGER; '
            'PROCEDURE Z(START_N INTEGER NOT NULL, END_N INTEGER NOT NULL) '
            'RETURNS(N INTEGER NOT NULL); END')


def udr_package_body(updated=False):
    return (
        'BEGIN FUNCTION H(A INTEGER, B INTEGER, C INTEGER) RETURNS INTEGER '
        "EXTERNAL NAME 'udrcpp_example!sum_args' ENGINE UDR; "
        'FUNCTION F(A INTEGER, B INTEGER, C INTEGER) RETURNS INTEGER '
        "EXTERNAL NAME 'udrcpp_example!sum_args' ENGINE UDR "
        "AS 'opaque ; it''s preserved'; "
        'PROCEDURE Z(START_N INTEGER NOT NULL, END_N INTEGER NOT NULL) '
        'RETURNS(N INTEGER NOT NULL) '
        "EXTERNAL NAME 'udrcpp_example!gen_rows' ENGINE UDR; "
        'FUNCTION G(X INTEGER) RETURNS INTEGER AS BEGIN RETURN H(X, ' +
        ('4, 5' if updated else '2, 3') + '); END END')


def cases():
    # Definitions are native input, not output from the renderer being tested.
    result = [
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
            'FUNCTION H(X INTEGER) RETURNS INTEGER AS '
            'BEGIN RETURN X + 2; END '
            'PROCEDURE V(X INTEGER) RETURNS(Y INTEGER) AS '
            'BEGIN Y = X + 6; END '
            'FUNCTION F(X INTEGER) RETURNS INTEGER AS '
            "BEGIN /* Keep ; and 'quotes' */ RETURN H(X); END "
            'PROCEDURE Z(X INTEGER) RETURNS(Y INTEGER) AS '
            'BEGIN EXECUTE PROCEDURE V(X) RETURNING_VALUES Y; END END'],
         'DROP PACKAGE P', ('header_source', 'body_source', 'description',
                            'sql_security', 'package_sql_security',
                            'member_comments')),
        ('package', 'PH', [
            'CREATE PACKAGE PH SQL SECURITY DEFINER AS '
            'BEGIN FUNCTION F(X INTEGER) RETURNS INTEGER; END'],
         'DROP PACKAGE PH', ('header_source', 'body_source', 'description',
                             'sql_security', 'package_sql_security',
                             'member_comments')),
        ('package', 'PU', [
            'CREATE PACKAGE PU AS ' + udr_package_header(),
            'CREATE PACKAGE BODY PU AS ' + udr_package_body()],
         'DROP PACKAGE PU', ('header_source', 'body_source', 'description',
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
    for suffix, body in (('N', ''), ('S', " AS '  body; it''s preserved  '")):
        for kind, name, definition, entry in (
                ('function', 'UF', '(A INTEGER, B INTEGER, C INTEGER) '
                 'RETURNS INTEGER DETERMINISTIC', 'sum_args'),
                ('procedure', 'UP', '(START_N INTEGER NOT NULL, '
                 'END_N INTEGER NOT NULL) RETURNS (N INTEGER NOT NULL)',
                 'gen_rows'),
                ('trigger', 'UT', 'FOR SENTINEL INACTIVE AFTER INSERT '
                 'POSITION 3', 'replicate!unused')):
            name += suffix
            result.append((
                kind, name, f'CREATE {kind.upper()} {name} {definition} '
                f"EXTERNAL NAME 'udrcpp_example!{entry}' ENGINE UDR" + body,
                f'DROP {kind.upper()} {name}',
                ('metadata_source', 'engine_name', 'entrypoint',
                 'description', 'sql_security',
                 'deterministic', 'trigger_type', 'inactive', 'position') +
                (() if kind == 'trigger' else ('parameters',))))
    return result


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
                     'visibility': item['native'].get('member_visibility'),
                     'engine_name': item['native'].get('engine_name'),
                     'entrypoint': item['native'].get('entrypoint'),
                     'source': item['native'].get('metadata_source'),
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
            if name == 'PU':
                assert sql('SELECT PU.F(1, 2, 3), PU.G(5) '
                           'FROM RDB$DATABASE') == [(6, 10)]
                assert sql('SELECT N FROM PU.Z(2, 4)') == [(2,), (3,), (4,)]
                try:
                    sql('SELECT PU.H(1, 2, 3) FROM RDB$DATABASE')
                except native.DatabaseError as exc:
                    assert 335545018 in exc.gds_codes
                    assert exc.sqlstate == '42000'
                    result.setdefault('private_udr_denials', []).append({
                        'dialect': dialect, 'codes': list(exc.gds_codes),
                        'sqlstate': exc.sqlstate})
                else:
                    raise AssertionError(
                        'Private UDR callable outside package')
            if name in {'UFN', 'UFS'}:
                assert sql(f'SELECT {name}(2, 3, 4) FROM RDB$DATABASE') == [
                    (9,)]
                assert sql(f'SELECT {name}(2, NULL, 4) FROM RDB$DATABASE') == [
                    (None,)]
            if name in {'UPN', 'UPS'}:
                assert sql(f'SELECT N FROM {name}(2, 4)') == [(2,), (3,), (4,)]
                assert sql(f'SELECT N FROM {name}(4, 2)') == []
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
                for source, code in (
                        ('SELECT P.H(5) FROM RDB$DATABASE', 335545018),
                        ('EXECUTE PROCEDURE P.V(5)', 335545019)):
                    try:
                        sql(source)
                    except native.DatabaseError as exc:
                        assert exc.sqlstate == '42000', str(exc)
                        assert code in exc.gds_codes, str(exc)
                        denials = result.setdefault(
                            'private_member_denials', [])
                        denials.append({
                            'dialect': dialect, 'source': source,
                            'sqlstate': exc.sqlstate,
                            'native_codes': list(exc.gds_codes)})
                    else:
                        raise AssertionError('Private member callable outside '
                                             'its package: ' + source)

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
                            targets.extend([
                                ('FUNCTION', 'H', ('X',)),
                                ('PROCEDURE', 'V', ('X', 'Y'))])
                        elif name == 'PU':
                            targets = [
                                ('FUNCTION', 'F', ('A', 'B', 'C')),
                                ('FUNCTION', 'G', ('X',)),
                                ('FUNCTION', 'H', ('A', 'B', 'C')),
                                ('PROCEDURE', 'Z', ('START_N', 'END_N', 'N'))]
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
                            assert member['visibility'] == (
                                'private' if member['name'] in {'H', 'V'}
                                else 'public')
                            assert member['description'] == (
                                "Member ; it's preserved")
                            for parameter in member['parameters']:
                                if parameter['name']:
                                    assert parameter['description'] == (
                                        "Member parameter ; it's preserved")
                    if kind in {'procedure', 'function'}:
                        parameter_names = [p['name'] for p in
                                           before['parameters'] if p['name']]
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
                        assert before['package_sql_security'] == {
                            'P': 'INVOKER', 'PH': 'DEFINER', 'PU': 'INHERIT',
                        }[name], check
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
                    if name == 'PU':
                        from pgadmin.cdeadmin.providers.firebird.provider \
                            import ADMINISTRATION
                        from pgadmin.cdeadmin.providers.firebird.ddl_dialect \
                            import generated_dialect

                        rollback()
                        request = {
                            'resource_kind': 'package',
                            'operation_id': 'replace_body',
                            '_provider_route': configured,
                            'target_resource': {'resource_kind': 'package',
                                                'display_name': 'PU'},
                            'draft': {'body': udr_package_body(True)}}
                        with generated_dialect(dialect):
                            plan = ADMINISTRATION.plan(request)
                        sql('INSERT INTO SENTINEL VALUES (81)')
                        ADMINISTRATION.apply(client, plan, connection=handle)
                        assert sql('SELECT X FROM SENTINEL') == [(81,)]
                        rollback()
                        restored = metadata(kind, name)
                        assert comparable_fields(restored, fields) == expected
                        assert sql('SELECT X FROM SENTINEL') == []
                        rollback()
                        ADMINISTRATION.apply(client, plan, connection=handle)
                        handle.commit()
                        replaced = metadata(kind, name)
                        assert replaced['body_status']['validity'] == 'valid'
                        assert replaced['body_source'] == (
                            udr_package_body(True))
                        fresh = client.open_session({'route': configured})
                        try:
                            with fresh.cursor() as cursor:
                                cursor.execute('SELECT PU.F(1, 2, 3), PU.G(5) '
                                               'FROM RDB$DATABASE')
                                assert cursor.fetchall() == [(6, 14)]
                                cursor.execute('SELECT N FROM PU.Z(2, 4)')
                                assert cursor.fetchall() == [
                                    (2,), (3,), (4,)]
                        finally:
                            client.close_session(fresh)
                        check['package_udr_body_replacement'] = {
                            'passed': True, 'rollback_restored_metadata': True,
                            'pending_work_preserved': True,
                            'fresh_committed_execution': True}
                    if name in {'UFN', 'UFS', 'UPN', 'UPS'}:
                        from pgadmin.cdeadmin.providers.firebird import (
                            functions, procedures,
                        )
                        from pgadmin.cdeadmin.providers.firebird.ddl_dialect \
                            import generated_dialect
                        from pgadmin.cdeadmin.providers.firebird.\
                            character_metadata import identifier

                        compiler = (functions if kind == 'function'
                                    else procedures)
                        with generated_dialect(dialect):
                            prefix = (f'CREATE {kind.upper()} ' +
                                      identifier(name))
                        assert statements[0].startswith(prefix)
                        declaration = statements[0][len(prefix):].split(
                            '\nEXTERNAL', 1)[0]
                        entry = ('sum_args' if kind == 'function'
                                 else 'gen_rows')
                        declaration += (
                            "\nEXTERNAL NAME 'udrcpp_example!" + entry +
                            "' ENGINE UDR AS 'Updated ; it''s exact  '")
                        target = {'resource_kind': kind, 'display_name': name}
                        sql(f'GRANT EXECUTE ON {kind.upper()} '
                            f'{name} TO ROLE R')
                        handle.commit()
                        grant_query = (
                            'SELECT TRIM(RDB$PRIVILEGE) '
                            'FROM RDB$USER_PRIVILEGES '
                            f"WHERE RDB$RELATION_NAME = '{name}' "
                            "AND RDB$USER = 'R' AND RDB$USER_TYPE = 13 "
                            'AND RDB$OBJECT_TYPE = ' +
                            ('15' if kind == 'function' else '5'))

                        def check_grant(phase, expected):
                            observed = sql(grant_query)
                            assert observed == expected, {
                                'phase': phase, 'expected': expected,
                                'observed': observed}

                        lifecycle = check['replacement_checks'] = []
                        for operation in ('create_or_alter', 'recreate'):
                            original = metadata(kind, name)
                            check_grant(operation + '-before', [('X',)])
                            rollback()
                            draft = {'declaration': declaration}
                            draft['name' if operation == 'create_or_alter'
                                  else 'confirmation'] = name
                            with generated_dialect(dialect):
                                command = compiler.compile_operation(
                                    operation, draft, target)
                            sql('INSERT INTO SENTINEL VALUES (73)')
                            sql(command)
                            assert sql('SELECT X FROM SENTINEL') == [(73,)]
                            rollback()
                            restored = metadata(kind, name)
                            assert comparable_fields(restored, fields) == (
                                comparable_fields(original, fields))
                            assert sql('SELECT X FROM SENTINEL') == []
                            check_grant(operation + '-rollback', [('X',)])
                            rollback()
                            sql(command)
                            handle.commit()
                            changed = metadata(kind, name)
                            assert changed['metadata_source'] == (
                                "Updated ; it's exact  ")
                            assert changed['engine_name'] == 'UDR'
                            assert changed['entrypoint'] == (
                                'udrcpp_example!' + entry)
                            assert changed['description'] == (
                                original['description'] if operation ==
                                'create_or_alter' else None)
                            check_grant(operation + '-commit',
                                        [('X',)] if operation ==
                                        'create_or_alter' else [])
                            for parameter in changed['parameters']:
                                if parameter['name']:
                                    assert parameter['description'] == (
                                        "Parameter ; it's preserved" if
                                        operation == 'create_or_alter'
                                        else None)
                            # Observe execution from a new attachment after
                            # commit; do not confuse warmed routine caches
                            # with the durable replacement definition.
                            fresh = client.open_session({'route': configured})
                            try:
                                with fresh.cursor() as cursor:
                                    query = (
                                        f'SELECT {name}(2, 3, 4) '
                                        'FROM RDB$DATABASE' if kind ==
                                        'function' else
                                        f'SELECT N FROM {name}(2, 4)')
                                    cursor.execute(query)
                                    expected_rows = ([(9,)] if kind ==
                                                     'function' else
                                                     [(2,), (3,), (4,)])
                                    assert cursor.fetchall() == expected_rows
                            finally:
                                client.close_session(fresh)
                            lifecycle.append({
                                'operation': operation, 'passed': True,
                                'source': command,
                                'rollback_restored_metadata': True,
                                'pending_work_preserved': True,
                                'comments_preserved': operation ==
                                'create_or_alter',
                                'execute_grant_preserved': operation ==
                                'create_or_alter',
                                'fresh_committed_execution': True})
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
            evolution = {'dialect': dialect, 'passed': False, 'states': []}
            result.setdefault('package_evolution_checks', []).append(evolution)
            try:
                from pgadmin.cdeadmin.providers.firebird import packages
                from pgadmin.cdeadmin.providers.firebird.ddl_dialect import (
                    generated_dialect,
                )
                original = metadata('package', 'P')
                rollback()

                def observe(phase, validity, source_available=True):
                    observed = metadata('package', 'P')
                    state = observed['body_status']
                    evolution['states'].append({
                        'phase': phase, 'body_status': state,
                        'raw_flag': observed['valid_body'],
                        'warnings': observed.get('catalog_warnings', [])})
                    assert state == {'validity': validity,
                                     'source_available': source_available}
                    assert (packages.INVALID_BODY_WARNING in observed.get(
                        'catalog_warnings', [])) == (validity == 'invalid')

                def task(operation, draft):
                    with generated_dialect(dialect):
                        statements = packages.compile_operation(
                            operation, draft,
                            {'resource_kind': 'package', 'display_name': 'P'})
                    for statement in statements:
                        sql(statement)

                header = {'header': original['header_source'],
                          'sql_security': 'INVOKER'}
                body = {'body': original['body_source']}
                observe('initial', 'valid')
                rollback()
                sql('INSERT INTO SENTINEL VALUES (99)')
                task('alter', header)
                observe('staged-header', 'invalid')
                assert sql('SELECT X FROM SENTINEL') == [(99,)]
                rollback()
                observe('header-rollback', 'valid')
                assert sql('SELECT X FROM SENTINEL') == []
                rollback()
                task('alter', header)
                handle.commit()
                observe('committed-header', 'invalid')
                rollback()
                task('replace_body', body)
                observe('staged-body', 'valid')
                rollback()
                observe('body-rollback', 'invalid')
                rollback()
                task('replace_body', body)
                handle.commit()
                observe('committed-body', 'valid')
                behavior('P')
                rollback()
                task('drop_body', {'confirmation': 'P'})
                observe('staged-drop-body', 'unknown', False)
                rollback()
                observe('drop-body-rollback', 'valid')
                evolution['passed'] = True
            except Exception as exc:
                result['failures'].append({
                    'case': f'package-evolution-{dialect}',
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
    replacements = [replacement for check in checks for replacement in
                    check.get('replacement_checks', [])]
    package_udr = [check['package_udr_body_replacement'] for check in checks
                   if 'package_udr_body_replacement' in check]
    result['complete'] = (result['complete'] and
                          len(package_udr) == 2 and
                          all(check['passed'] for check in package_udr) and
                          len(replacements) == 16 and
                          all(check['passed'] for check in replacements) and
                          len(checks) == 2 * len(cases()) and
                          all(check['passed'] for check in checks) and
                          len(result.get('package_evolution_checks', [])) == 2
                          and all(check['passed'] for check in
                                  result['package_evolution_checks']))
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({'complete': result['complete'],
                      'failures': result['failures']}))
    return 0 if result['complete'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
