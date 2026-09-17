#!/usr/bin/env python3
"""Verify effective packaged UDR permissions in an owned Firebird server."""
import argparse
import json
import secrets
from pathlib import Path
from types import SimpleNamespace

if __package__:
    from . import cdeadmin_firebird_views_gate as base
    from .cdeadmin_firebird_catalog_dialect_gate import (
        udr_package_header, udr_package_body,
    )
else:
    import cdeadmin_firebird_views_gate as base
    from cdeadmin_firebird_catalog_dialect_gate import (
        udr_package_header, udr_package_body,
    )


def permission_request(operation, principal_kind='ROLE'):
    principal = 'PU_ROLE' if principal_kind == 'ROLE' else 'PU_READER'
    draft = {'principal': principal, 'principal_kind': principal_kind,
             'object_type': 'PACKAGE', 'object_name': 'PU',
             'privileges': ['EXECUTE']}
    if operation == 'revoke':
        draft['confirmation'] = principal
    return {'resource_kind': 'privilege', 'operation_id': operation,
            'draft': draft}


def verify(connection, client, route, password, result):
    import firebird.driver as native
    from pgadmin.cdeadmin.providers.firebird.error_diagnostics import (
        status_codes,
    )
    from pgadmin.cdeadmin.providers.firebird.provider import (
        ADMINISTRATION,
    )
    from pgadmin.cdeadmin.providers.firebird.character_metadata import literal

    user_password = secrets.token_urlsafe(24)
    reader = base._create_client(SimpleNamespace(
        acquire_secret=lambda *_args: base.SecretLease(user_password)))
    handle = client.open_session({'route': route})
    checks = result['package_permission_checks'] = []
    delegation = result['package_delegation_checks'] = []

    def sql(source):
        with handle.cursor() as cursor:
            cursor.execute(source)

    def finish(operation):
        client.control_transaction(handle, operation)

    def task(operation, principal_kind='ROLE', **options):
        request = {**permission_request(operation, principal_kind),
                   '_provider_route': route}
        request['draft'].update(options)
        assert ADMINISTRATION.validate(request) == {'errors': []}
        receipt = ADMINISTRATION.apply(
            client, ADMINISTRATION.plan(request), connection=handle)
        assert receipt['staged_in_provider_session'] is True

    def access(phase, allowed, role=None, user='PU_READER'):
        # Fresh attachments avoid claiming cached role/permission invalidation.
        for name, source, expected in [
                ('public-external-function',
                 'SELECT PU.F(1, 2, 3) FROM RDB$DATABASE', [(6,)]),
                ('public-external-procedure',
                 'SELECT N FROM PU.Z(2, 4)', [(2,), (3,), (4,)]),
                ('private-external-via-wrapper',
                 'SELECT PU.G(5) FROM RDB$DATABASE', [(10,)]),
                ('direct-private-external',
                 'SELECT PU.H(1, 2, 3) FROM RDB$DATABASE', None)]:
            other = None
            label = phase + ':' + name
            try:
                other = reader.open_session({'route': {
                    **route, 'user': user, 'role': role}})
                token = reader.submit_query(other, {'source': source})
                token.worker.join(20)
                assert not token.worker.is_alive(), 'Query did not finish'
                observed = reader.describe_result(token)
                payload = observed['payload']
                assert observed['complete'], observed
                accepted = allowed and expected is not None
                if accepted:
                    assert payload['execution_state'] != 'failed', payload
                    assert [tuple(row) for row in payload['rows']] == expected
                    # Independently compare the provider envelope with native
                    # cursor results through the same authenticated session.
                    with other.cursor() as cursor:
                        cursor.execute(source)
                        assert cursor.fetchall() == expected
                else:
                    assert payload['execution_state'] == 'failed', payload
                    codes = payload['error']['native_status_codes']
                    denial = 335545018 if expected is None else 335544352
                    assert denial in codes, codes
                checks.append({'case': label, 'accepted': accepted,
                               'passed': True})
            except Exception as exc:
                result['failures'].append({
                    'case': label, 'error_type': type(exc).__name__,
                    'message': str(exc).replace(user_password, '<redacted>')
                    .replace(password, '<redacted>')})
            finally:
                if other is not None:
                    reader.close_session(other)

    def delegate(phase, allowed, commit=False):
        other = reader.open_session({'route': {
            **route, 'user': 'PU_READER', 'role': None}})
        try:
            request = {**permission_request('grant', 'USER'),
                       '_provider_route': route}
            request['draft']['principal'] = 'PU_DELEGATE'
            plan = ADMINISTRATION.plan(request)
            source = plan['command_preview']['statements'][0]['source']
            # Compare denied provider execution with the native driver's
            # status vector instead of classifying errors by message text.
            native_codes = None
            if not allowed:
                try:
                    with other.cursor() as cursor:
                        cursor.execute(source)
                except native.DatabaseError as exc:
                    native_codes = status_codes(exc)
                else:
                    raise AssertionError('Native delegation was not denied')
                assert native_codes
            try:
                receipt = ADMINISTRATION.apply(reader, plan, connection=other)
            except Exception as exc:
                assert not allowed, type(exc).__name__
                assert status_codes(exc) == native_codes
                reader.control_transaction(other, 'rollback')
            else:
                assert allowed, 'Provider delegation was not denied'
                assert receipt['staged_in_provider_session'] is True
                reader.control_transaction(
                    other, 'commit' if commit else 'rollback')
            delegation.append({'case': phase, 'passed': True,
                               'allowed': allowed, 'committed': commit,
                               'native_denial_codes': native_codes})
        except Exception as exc:
            result['failures'].append({
                'case': phase, 'error_type': type(exc).__name__,
                'message': str(exc).replace(user_password, '<redacted>')
                .replace(password, '<redacted>')})
        finally:
            reader.close_session(other)

    try:
        for source in [
                'CREATE USER PU_READER PASSWORD ' + literal(user_password),
                'CREATE USER PU_DELEGATE PASSWORD ' + literal(user_password),
                'CREATE ROLE PU_ROLE',
                'GRANT PU_ROLE TO USER PU_READER',
                'CREATE PACKAGE PU AS ' + udr_package_header(),
                'CREATE PACKAGE BODY PU AS ' + udr_package_body()]:
            sql(source)
            finish('commit')
        access('ungranted', False, 'PU_ROLE')
        task('grant')
        finish('rollback')
        access('grant-rolled-back', False, 'PU_ROLE')
        task('grant')
        finish('commit')
        access('inactive-role', False)
        access('active-role', True, 'PU_ROLE')
        task('revoke')
        finish('rollback')
        access('revoke-rolled-back', True, 'PU_ROLE')
        task('revoke')
        finish('commit')
        access('revoke-committed', False, 'PU_ROLE')
        task('grant', 'USER')
        finish('commit')
        access('direct-user-grant', True)
        task('revoke', 'USER')
        finish('commit')
        access('direct-user-revoked', False)
        task('grant', 'USER', grant_option=True)
        finish('commit')
        delegate('delegated-grant-rolled-back', True)
        access('delegate-after-rollback', False, user='PU_DELEGATE')
        delegate('delegated-grant-committed', True, commit=True)
        access('delegate-after-commit', True, user='PU_DELEGATE')
        task('revoke', 'USER', grant_option_only=True)
        finish('rollback')
        delegate('option-revoke-rolled-back', True)
        task('revoke', 'USER', grant_option_only=True)
        finish('commit')
        access('option-revoke-retains-own-execute', True)
        delegate('option-revoke-denies-delegation', False)
        access('option-revoke-cascades-delegate', False, user='PU_DELEGATE')
        task('revoke', 'USER')
        finish('commit')
        access('full-revoke-removes-own-execute', False)
    except Exception as exc:
        result['failures'].append({
            'case': 'package-permission-lifecycle',
            'error_type': type(exc).__name__,
            'message': str(exc).replace(user_password, '<redacted>')
            .replace(password, '<redacted>')})
    finally:
        client.close_session(handle)
        # The user exists only in base.run's disposable server security DB.


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        parser.error('Refusing to overwrite evidence')
    result = base.run(extra_checks=verify)
    checks = result.get('package_permission_checks', [])
    delegation = result.get('package_delegation_checks', [])
    result['complete'] = (
        result['complete'] and len(checks) == 52 and len(delegation) == 4 and
        all(check['passed'] for check in checks + delegation))
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({'complete': result['complete'],
                      'failures': result['failures']}))
    return 0 if result['complete'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
