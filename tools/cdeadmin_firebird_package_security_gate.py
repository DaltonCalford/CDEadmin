#!/usr/bin/env python3
"""Verify package SQL SECURITY with non-admin sessions on owned Firebird."""
import argparse
import json
import secrets
from pathlib import Path
from types import SimpleNamespace

if __package__:
    from . import cdeadmin_firebird_views_gate as base
else:
    import cdeadmin_firebird_views_gate as base


HEADER = ('BEGIN FUNCTION F RETURNS INTEGER; '
          'FUNCTION WHO RETURNS VARCHAR(63); '
          'PROCEDURE P RETURNS(V INTEGER); END')
BODY = (
    'BEGIN FUNCTION F RETURNS INTEGER AS DECLARE V INTEGER; BEGIN '
    'SELECT V FROM SEC_DATA INTO :V; RETURN V; END '
    'FUNCTION WHO RETURNS VARCHAR(63) AS BEGIN '
    "RETURN RDB$GET_CONTEXT('SYSTEM', 'EFFECTIVE_USER'); END "
    'PROCEDURE P RETURNS(V INTEGER) AS BEGIN '
    'SELECT V FROM SEC_DATA INTO :V; SUSPEND; END END')
MODES = ('INHERIT', 'INVOKER', 'DEFINER')


def package_request(mode):
    return {'resource_kind': 'package', 'operation_id': 'create',
            'draft': {'name': 'SEC_' + mode, 'sql_security': mode,
                      'header': HEADER, 'body': BODY}}


def verify(connection, client, route, password, result):
    from pgadmin.cdeadmin.providers.firebird.provider import ADMINISTRATION
    from pgadmin.cdeadmin.providers.firebird.character_metadata import literal

    secret = secrets.token_urlsafe(24)
    reader = base._create_client(SimpleNamespace(
        acquire_secret=lambda *_args: base.SecretLease(secret)))
    handle = client.open_session({'route': route})
    checks = result['package_security_checks'] = []

    def sql(source):
        with handle.cursor() as cursor:
            cursor.execute(source)

    def apply(request):
        request = {**request, '_provider_route': route}
        assert ADMINISTRATION.validate(request) == {'errors': []}
        receipt = ADMINISTRATION.apply(
            client, ADMINISTRATION.plan(request), connection=handle)
        assert receipt['staged_in_provider_session']

    def finish(action='commit'):
        client.control_transaction(handle, action)

    def default(mode):
        apply({'resource_kind': 'database', 'operation_id': 'alter',
               'target_resource': {'resource_kind': 'database',
                                   'display_name': route['database']},
               'draft': {'default_sql_security': mode}})

    def table_privilege(operation):
        apply({'resource_kind': 'privilege', 'operation_id': operation,
               'draft': {'object_type': 'TABLE', 'object_name': 'SEC_DATA',
                         'privileges': ['SELECT'], 'principal_kind': 'USER',
                         'principal': 'SEC_READER', **(
                             {'confirmation': 'SEC_READER'}
                             if operation == 'revoke' else {})}})

    def observe(phase, inherited, table_access=False):
        for mode in MODES:
            effective = inherited if mode == 'INHERIT' else mode
            privileged = effective == 'DEFINER'
            name = 'SEC_' + mode
            queries = [
                ('function', f'SELECT {name}.F() FROM RDB$DATABASE',
                 [(73,)] if privileged or table_access else None),
                ('procedure', f'SELECT V FROM {name}.P',
                 [(73,)] if privileged or table_access else None),
                ('effective-user', f'SELECT {name}.WHO() FROM RDB$DATABASE',
                 [('SEC_READER',)]),
                ('direct-table', 'SELECT V FROM SEC_DATA',
                 [(73,)] if table_access else None)]
            for label, source, expected in queries:
                other = None
                case = phase + ':' + mode + ':' + label
                try:
                    other = reader.open_session({'route': {
                        **route, 'user': 'SEC_READER'}})
                    token = reader.submit_query(other, {'source': source})
                    token.worker.join(20)
                    assert not token.worker.is_alive()
                    observed = reader.describe_result(token)
                    assert observed['complete']
                    payload = observed['payload']
                    if expected is None:
                        assert payload['execution_state'] == 'failed', payload
                        assert 335544352 in payload['error'][
                            'native_status_codes'], payload
                    else:
                        assert payload['execution_state'] != 'failed', payload
                        assert [tuple(row) for row in payload['rows']] == (
                            expected), payload
                        with other.cursor() as cursor:
                            cursor.execute(source)
                            native_rows = cursor.fetchall()
                        assert native_rows == expected, native_rows
                        if label == 'effective-user':
                            # This context-only function returns the attaching
                            # user in the tested native 5.0.4 build, including
                            # DEFINER packages. Do not synthesize the owner.
                            observations = result.setdefault(
                                'effective_user_observations', [])
                            observations.append({
                                'case': case, 'effective_mode': effective,
                                'native_rows': native_rows,
                                'provider_rows': payload['rows']})
                    checks.append({'case': case, 'passed': True,
                                   'accepted': expected is not None})
                except Exception as exc:
                    result['failures'].append({
                        'case': case, 'error_type': type(exc).__name__,
                        'message': str(exc).replace(secret, '<redacted>')
                        .replace(password, '<redacted>')})
                finally:
                    if other is not None:
                        reader.close_session(other)

    try:
        sql('CREATE USER SEC_READER PASSWORD ' + literal(secret))
        sql('CREATE TABLE SEC_DATA (V INTEGER)')
        finish()
        sql('INSERT INTO SEC_DATA VALUES (73)')
        default('INVOKER')
        finish()
        for mode in MODES:
            apply(package_request(mode))
            finish()
            sql('GRANT EXECUTE ON PACKAGE SEC_' + mode + ' TO USER SEC_READER')
            finish()
        observe('execute-only', 'INVOKER')
        table_privilege('grant')
        finish()
        observe('table-granted', 'INVOKER', True)
        table_privilege('revoke')
        finish()
        observe('table-revoked', 'INVOKER')
        default('DEFINER')
        finish('rollback')
        observe('default-rollback', 'INVOKER')
        default('DEFINER')
        finish()
        observe('default-definer', 'DEFINER')
        default('INVOKER')
        finish()
        observe('default-invoker-restored', 'INVOKER')
    except Exception as exc:
        result['failures'].append({
            'case': 'package-security-lifecycle',
            'error_type': type(exc).__name__,
            'message': str(exc).replace(secret, '<redacted>')
            .replace(password, '<redacted>')})
    finally:
        client.close_session(handle)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        parser.error('Refusing to overwrite evidence')
    result = base.run(extra_checks=verify)
    checks = result.get('package_security_checks', [])
    result['complete'] = (result['complete'] and len(checks) == 72 and
                          all(check['passed'] for check in checks))
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({'complete': result['complete'],
                      'failures': result['failures']}))
    return 0 if result['complete'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
