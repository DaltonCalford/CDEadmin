#!/usr/bin/env python3
"""Execute native external triggers using two owned Firebird databases."""
import argparse
import json
import traceback
from pathlib import Path

if __package__:
    from . import cdeadmin_firebird_views_gate as base
else:
    import cdeadmin_firebird_views_gate as base


def declaration(active=True):
    return ('FOR UDR_ROWS ' + ('ACTIVE' if active else 'INACTIVE') +
            ' AFTER INSERT POSITION 4 '
            "EXTERNAL NAME 'udrcpp_example!replicate!owned' ENGINE UDR")


def verify(connection, client, route, password, result):
    import firebird.driver as native
    from firebird.driver import core
    from pgadmin.cdeadmin.providers.firebird.database_creation import (
        create_database,
    )
    from pgadmin.cdeadmin.providers.firebird.provider import (
        ADMINISTRATION, _database_create_arguments, _route_arguments,
        _resources,
    )
    from pgadmin.cdeadmin.providers.firebird.error_diagnostics import (
        status_codes,
    )

    replica = {**route, 'database':
               '/var/lib/firebird/data/owned_udr_replica.fdb'}
    args = _database_create_arguments(
        replica, _route_arguments(replica)['database'],
        {'sql_dialect': 3}, native)
    created = create_database(native, core, password=password, **args)
    created.close()
    handle = client.open_session({'route': route})
    checks = result['external_trigger_checks'] = []

    def sql(source, current=handle, parameters=()):
        with current.cursor() as cursor:
            cursor.execute(source, parameters)
            return cursor.fetchall() if cursor.description else []

    def rollback():
        if handle.main_transaction.is_active():
            client.control_transaction(handle, 'rollback')

    def commit():
        client.control_transaction(handle, 'commit')

    def destination():
        observer = client.open_session({'route': replica})
        try:
            return sql('SELECT ID, TXT FROM UDR_ROWS ORDER BY ID', observer)
        finally:
            client.close_session(observer)

    def metadata():
        return next(item['native'] for item in _resources(
            handle, {'route': route}) if item['resource_kind'] == 'trigger'
            and item['display_name'] == 'UDR_COPY')

    def task(operation, active=True):
        draft = {'declaration': declaration(active)}
        draft['confirmation' if operation == 'recreate' else 'name'] = (
            'UDR_COPY')
        plan = ADMINISTRATION.plan({
            'resource_kind': 'trigger', 'operation_id': operation,
            '_provider_route': route, 'draft': draft,
            'target_resource': {'resource_kind': 'trigger',
                                'display_name': 'UDR_COPY'}})
        return ADMINISTRATION.apply(client, plan, connection=handle)

    def insert(number):
        sql('INSERT INTO UDR_ROWS VALUES (?, ?)', parameters=(
            number, "Row ; it's 東京 " + str(number)))

    def expected(numbers):
        return [(number, "Row ; it's 東京 " + str(number))
                for number in numbers]

    def observe(label, source_ids, destination_ids, active=True):
        observed = metadata()
        assert observed['engine_name'] == 'UDR'
        assert observed['entrypoint'] == 'udrcpp_example!replicate!owned'
        assert observed['inactive'] == ('0' if active else '1')
        assert observed['position'] == '4'
        source_rows = sql('SELECT ID, TXT FROM UDR_ROWS ORDER BY ID')
        destination_rows = destination()
        assert source_rows == expected(source_ids), source_rows
        assert destination_rows == expected(destination_ids), destination_rows
        checks.append({'phase': label, 'passed': True,
                       'source_rows': source_rows,
                       'destination_rows': destination_rows,
                       'inactive': observed['inactive']})

    phase = 'setup'
    try:
        schema = ('CREATE TABLE UDR_ROWS (ID INTEGER PRIMARY KEY, '
                  'TXT VARCHAR(60) CHARACTER SET UTF8)')
        sql(schema)
        sql('CREATE TABLE REPLICATE_CONFIG (NAME VARCHAR(31) NOT NULL, '
            'DATA_SOURCE VARCHAR(255) NOT NULL)')
        commit()
        sql('INSERT INTO REPLICATE_CONFIG VALUES (?, ?)', parameters=(
            'owned', replica['database']))
        commit()
        other = client.open_session({'route': replica})
        try:
            sql(schema, other)
            client.control_transaction(other, 'commit')
        finally:
            client.close_session(other)
        task('create_or_alter')
        commit()
        phase = 'active-rollback'
        insert(1)
        observe('staged-insert', [1], [])
        rollback()
        observe('rolled-back-insert', [], [])
        phase = 'active-commit'
        insert(2)
        commit()
        observe('committed-insert', [2], [2])
        phase = 'inactive'
        task('create_or_alter', False)
        commit()
        insert(3)
        commit()
        observe('inactive-insert', [2, 3], [2], False)
        phase = 'activation-rollback'
        task('create_or_alter')
        rollback()
        insert(4)
        commit()
        observe('activation-rolled-back', [2, 3, 4], [2], False)
        phase = 'activation-commit'
        task('create_or_alter')
        commit()
        insert(5)
        commit()
        observe('activation-committed', [2, 3, 4, 5], [2, 5])
        phase = 'recreate'
        task('recreate')
        commit()
        insert(6)
        commit()
        observe('recreated-trigger', [2, 3, 4, 5, 6], [2, 5, 6])
        phase = 'export-replay'
        sql("COMMENT ON TRIGGER UDR_COPY IS 'External ; it''s preserved'")
        commit()
        before = metadata()
        statements = before['recreation_statements']
        assert len(statements) == 2
        sql('DROP TRIGGER UDR_COPY')
        commit()
        for statement in statements:
            sql(statement)
        commit()
        assert metadata()['description'] == before['description']
        insert(7)
        commit()
        observe('export-replayed', [2, 3, 4, 5, 6, 7], [2, 5, 6, 7])
        result['external_trigger_recreation'] = statements
        phase = 'destination-error'
        other = client.open_session({'route': replica})
        try:
            sql('INSERT INTO UDR_ROWS VALUES (?, ?)', other,
                parameters=(8, expected([8])[0][1]))
            client.control_transaction(other, 'commit')
        finally:
            client.close_session(other)
        insert(9)
        transaction = handle.main_transaction.info.id
        try:
            insert(8)
        except native.DatabaseError as exc:
            native_codes = status_codes(exc)
            assert 335544926 in native_codes, native_codes
            assert 'violation of PRIMARY or UNIQUE KEY constraint' in str(exc)
            result['external_trigger_destination_native_error'] = {
                'codes': list(native_codes), 'sqlstate': exc.sqlstate,
                'duplicate_key_message_verified': True}
        else:
            raise AssertionError(
                'Native destination failure did not propagate')
        token = client.submit_query(handle, {
            'source': 'INSERT INTO UDR_ROWS VALUES (?, ?)',
            'parameters': [8, expected([8])[0][1]]})
        token.worker.join(20)
        assert not token.worker.is_alive(), 'Trigger query did not finish'
        failure = client.describe_result(token)
        assert failure['complete']
        assert failure['payload']['execution_state'] == 'failed', failure
        error = failure['payload']['error']
        assert error['native_status_codes'] == list(native_codes), error
        result['external_trigger_destination_error'] = error
        assert client.cancel(token) is False
        assert handle.main_transaction.info.id == transaction
        observe('destination-error-preserves-pending-work',
                [2, 3, 4, 5, 6, 7, 9], [2, 5, 6, 7, 8])
        rollback()
        observe('destination-error-explicit-rollback',
                [2, 3, 4, 5, 6, 7], [2, 5, 6, 7, 8])
    except Exception as exc:
        result['failures'].append({
            'case': 'external-trigger-' + phase,
            'error_type': type(exc).__name__,
            'message': str(exc).replace(password, '<redacted>'),
            'frames': [{'function': frame.name, 'line': frame.lineno}
                       for frame in traceback.extract_tb(exc.__traceback__)]})
    finally:
        client.close_session(handle)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        parser.error('Refusing to overwrite evidence')
    result = base.run(extra_checks=verify)
    checks = result.get('external_trigger_checks', [])
    result['complete'] = (result['complete'] and len(checks) == 10 and
                          all(check['passed'] for check in checks))
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({'complete': result['complete'],
                      'failures': result['failures']}))
    return 0 if result['complete'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
