#!/usr/bin/env python3
"""Compare generated table/type editors with native stored-dialect behavior."""
import argparse
import json
from pathlib import Path

if __package__:
    from . import cdeadmin_firebird_views_gate as base
else:
    import cdeadmin_firebird_views_gate as base


def type_cases():
    cases = [(name, {'data_type': name}, name) for name in (
        'SMALLINT', 'INTEGER', 'BIGINT', 'INT128', 'FLOAT',
        'DOUBLE PRECISION', 'BOOLEAN', 'DATE')]
    cases += [(name, {'data_type': name, 'length': 30}, name + '(30)')
              for name in ('CHAR', 'VARCHAR', 'NCHAR', 'NCHAR VARYING',
                           'BINARY', 'VARBINARY')]
    for name in ('NUMERIC', 'DECIMAL'):
        for precision in (9, 18, 38):
            cases.append((name + str(precision), {
                'data_type': name, 'precision': precision, 'scale': 2},
                f'{name}({precision}, 2)'))
    for name in ('TIME', 'TIMESTAMP'):
        for zone in ('WITHOUT TIME ZONE', 'WITH TIME ZONE'):
            cases.append((name + ' ' + zone, {
                'data_type': name, 'time_zone': zone}, name + ' ' + zone))
    for precision in (16, 34):
        cases.append(('DECFLOAT' + str(precision), {
            'data_type': 'DECFLOAT', 'precision': precision},
            f'DECFLOAT({precision})'))
    cases.extend([
        ('FLOAT53', {'data_type': 'FLOAT', 'precision': 53}, 'FLOAT(53)'),
        ('BLOB', {'data_type': 'BLOB'}, 'BLOB SUB_TYPE 0'),
        ('TEXT BLOB', {'data_type': 'BLOB', 'blob_subtype': 1,
                       'character_set': 'UTF8'},
         'BLOB SUB_TYPE 1 CHARACTER SET "UTF8"'),
        ('DOMAIN', {'data_type': 'DOMAIN', 'domain': 'D'}, '"D"'),
    ])
    return cases


def native_oracle_type(label, definition):
    # The baseline deliberately uses native unquoted identifiers. Quoting a
    # domain/charset would itself trigger the ambiguity guard under test.
    return {'DOMAIN': 'D',
            'TEXT BLOB': 'BLOB SUB_TYPE 1 CHARACTER SET UTF8'}.get(
                label, definition)


def verify(connection, client, route, password, result):
    import firebird.driver as native
    from firebird.driver import core
    from pgadmin.cdeadmin.providers.firebird.database_creation import (
        create_database,
    )
    from pgadmin.cdeadmin.providers.firebird.provider import (
        _database_create_arguments, _route_arguments, _resources,
    )
    from pgadmin.cdeadmin.providers.firebird.column_type_metadata import (
        type_editor_values,
    )
    from pgadmin.cdeadmin.providers.firebird import columns
    from pgadmin.cdeadmin.providers.firebird.error_diagnostics import (
        status_codes,
    )

    checks = result['legacy_editor_checks'] = []
    keys = ('field_type', 'field_sub_type', 'field_length', 'field_scale',
            'field_precision', 'character_length', 'segment_length',
            'character_set')

    for dialect in (1, 3):
        configured = {**route, 'database':
                      f'/var/lib/firebird/data/editor_{dialect}.fdb'}
        args = _database_create_arguments(
            configured, _route_arguments(configured)['database'],
            {'sql_dialect': dialect}, native)
        created = create_database(native, core, password=password, **args)
        created.close()
        handle = client.open_session({'route': configured})

        def rollback():
            if handle.main_transaction.is_active():
                handle.rollback()

        def sql(source, parameters=()):
            with handle.cursor() as cursor:
                cursor.execute(source, parameters)
                return cursor.fetchall() if cursor.description else []

        def apply(operation, draft, name='T'):
            request = {'resource_kind': 'table', 'operation_id': operation,
                       '_provider_route': configured, 'draft': draft,
                       'target_resource': {'resource_kind': 'table',
                                           'display_name': name,
                                           'display_path': [name]}}
            assert base.ADMINISTRATION.validate(request) == {'errors': []}
            plan = client.plan_admin_operation(request)
            if operation == 'create' and name == 'T':
                observation['generated_source'] = [
                    s['source'] for s in plan['command_preview']['statements']]
            receipt = base.ADMINISTRATION.apply(client, plan,
                                                connection=handle)
            assert receipt['staged_in_provider_session'] is True
            return [s['source'] for s in plan['command_preview']['statements']]

        def create(values, name='T'):
            return apply('create', {'name': name, 'columns': [{
                'name': 'V', 'column_mode': 'STORED', **values}]}, name)

        def catalog():
            return _resources(handle, {'route': configured})

        def field(resources, name):
            return next(item['native'] for item in resources if
                        item['resource_kind'] == 'column' and
                        item['display_path'] == [name, 'V'])

        def signature(value):
            return {key: value.get(key) for key in keys}

        try:
            assert handle.sql_dialect == 3
            assert handle.info.sql_dialect == dialect
            sql('CREATE DOMAIN D AS INTEGER')
            sql('CREATE TABLE SENTINEL (ID INTEGER)')
            handle.commit()
            for label, values, definition in type_cases():
                observation = {
                    'stored_dialect': dialect, 'case': label,
                    'type_sql': definition,
                    'native_oracle_type': native_oracle_type(
                        label, definition),
                    'outcome': 'unverified'}
                checks.append(observation)
                try:
                    assert columns.data_type(values) == definition
                    native_codes = ()
                    try:
                        sql('CREATE TABLE ORACLE (V ' +
                            native_oracle_type(label, definition) + ')')
                    except native.DatabaseError as exc:
                        native_codes = status_codes(exc)
                        assert native_codes
                    finally:
                        rollback()
                    observation['native_status_codes'] = list(native_codes)
                    sql('INSERT INTO SENTINEL VALUES (42)')
                    transaction_id = handle.main_transaction.info.id
                    provider_codes = ()
                    statements = []
                    try:
                        statements = create(values)
                    except Exception as exc:
                        provider_codes = status_codes(exc)
                        if not provider_codes:
                            raise
                    observation['provider_status_codes'] = list(provider_codes)
                    assert handle.main_transaction.info.id == transaction_id
                    assert sql('SELECT ID FROM SENTINEL') == [(42,)]
                    rollback()
                    assert sql('SELECT ID FROM SENTINEL') == []
                    assert sql('SELECT 1 FROM RDB$RELATIONS WHERE '
                               "RDB$RELATION_NAME = 'T'") == []
                    rollback()
                    observation['pending_work_preserved'] = True
                    assert provider_codes == native_codes, (
                        native_codes, provider_codes)
                    if native_codes:
                        observation.update(outcome='native_baseline_rejection',
                                           pending_work_preserved=True)
                        continue
                    statements = create(values)
                    handle.commit()
                    resources = catalog()
                    original = field(resources, 'T')
                    editor = type_editor_values(original)
                    assert editor, ('missing editor values', original)
                    rollback()
                    create(editor, 'U')
                    handle.commit()
                    assert signature(field(catalog(), 'U')) == signature(
                        original)
                    rollback()
                    apply('drop', {'confirmation': 'U'}, 'U')
                    rollback()
                    assert field(catalog(), 'U')
                    rollback()
                    apply('drop', {'confirmation': 'U'}, 'U')
                    handle.commit()
                    ddl = next(item['native']['ddl'] for item in resources if
                               item['resource_kind'] == 'table' and
                               item['display_path'] == ['T'])
                    prefix = ('CREATE TABLE T' if dialect == 1 else
                              'CREATE TABLE "T"')
                    replacement = ('CREATE TABLE U' if dialect == 1 else
                                   'CREATE TABLE "U"')
                    assert ddl.startswith(prefix), ddl
                    replay = ddl.replace(prefix, replacement, 1)
                    sql(replay.rstrip().rstrip(';'))
                    handle.commit()
                    assert signature(field(catalog(), 'U')) == signature(
                        original)
                    rollback()
                    observation.update({
                        'outcome': 'generated_editor_verified',
                        'statements': statements,
                        'editor_values': editor,
                        'native_fields': signature(original),
                        'metadata_ddl': ddl,
                        'create_rollback_commit': True,
                        'drop_rollback_commit': True,
                        'metadata_replay': True,
                        'pending_work_preserved': True})
                except Exception as exc:
                    observation['outcome'] = 'application_or_verification_gap'
                    result['failures'].append({
                        'case': f'editor-{dialect}-{label}',
                        'error_type': type(exc).__name__,
                        'message': str(exc).replace(password, '<redacted>')})
                finally:
                    rollback()
                    for name in ('U', 'T'):
                        if sql('SELECT 1 FROM RDB$RELATIONS WHERE '
                               'RDB$RELATION_NAME = ?', (name,)):
                            sql('DROP TABLE ' + name)
                            handle.commit()
                        else:
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
    result['complete'] = (result['complete'] and
                          len(result.get('legacy_editor_checks', [])) ==
                          2 * len(type_cases()))
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({'complete': result['complete'],
                      'failures': result['failures']}))
    return 0 if result['complete'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
