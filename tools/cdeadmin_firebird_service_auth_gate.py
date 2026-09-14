#!/usr/bin/env python3
"""Verify Unicode service credentials using owned database/account fixtures."""

import argparse
import json
import secrets
import traceback
import uuid
from pathlib import Path, PurePosixPath
from types import SimpleNamespace

if __package__:
    from .cdeadmin_firebird_admin_mapping_gate import (
        _create_client, _route_arguments,
    )
else:
    from cdeadmin_firebird_admin_mapping_gate import (
        _create_client, _route_arguments,
    )
from pgadmin.cdeadmin.security.secrets import SecretLease
from pgadmin.cdeadmin.providers.firebird.provider import (
    _configure_client_library,
)
from pgadmin.cdeadmin.sdk.relational import RelationalClientError


def service_context(driver, buffer):
    # Driver 1.10.11 SPB_ATTACH.parse_buffer does not advance its iterator.
    # Inspect only non-secret tags with an explicit bounded native iterator.
    observed = {'user': None, 'expected_db': None, 'utf8': False}
    core = driver.core
    with driver.get_api().util.get_xpb_builder(
            core.XpbKind.SPB_ATTACH, buffer) as builder:
        for _index in range(len(buffer) + 1):
            if builder.is_eof():
                return observed
            tag = builder.get_tag()
            if tag == core.SPBItem.USER_NAME:
                observed['user'] = builder.get_string(encoding='utf-8')
            elif tag == core.SPBItem.EXPECTED_DB:
                observed['expected_db'] = builder.get_string(encoding='utf-8')
            elif tag == core.SPBItem.UTF8_FILENAME:
                observed['utf8'] = True
            builder.move_next()
    raise AssertionError('Service parameter iteration did not terminate')


def run(profiles, ascii_user=False):
    import firebird.driver as driver
    _configure_client_library(driver)
    document = json.loads(profiles.read_text())
    route = next(dict(item) for item in document['profiles']
                 if item['engine'] == 'firebird')
    route.setdefault('host', document.get('host', '127.0.0.1'))
    password = route.pop('password')
    token = uuid.uuid4().hex
    path = str(PurePosixPath(route['database']).parent /
               ('cde_service_auth_' + token + '_東京.fdb'))
    prefix = 'CDE_SVC_' if ascii_user else 'CDE_SVC_É_'
    username = prefix + token[:12].upper()
    user_password = 'Svc-密-é-' + secrets.token_hex(8)
    quoted_user = '"' + username.replace('"', '""') + '"'
    result = {'complete': False, 'cases': [], 'failures': [],
              'owned_database_removed': False, 'owned_user_removed': False,
              'fixture_database': path, 'fixture_account': username,
              'credential_values_exported': False}
    admin = None
    user_owned = False
    services = []

    def rollback():
        if admin.main_transaction.is_active():
            admin.rollback()

    def execute(source, parameters=()):
        with admin.cursor() as cursor:
            cursor.execute(source, parameters)

    def user_exists():
        with admin.cursor() as cursor:
            cursor.execute('SELECT COUNT(*) FROM SEC$USERS WHERE '
                           'SEC$USER_NAME = ?', (username,))
            return cursor.fetchone()[0] > 0

    try:
        admin = driver.create_database(password=password, **_route_arguments(
            {**route, 'database': path}, driver))
        assert not user_exists(), 'Owned account collision'
        rollback()
        user_owned = True
        execute('CREATE USER ' + quoted_user + " PASSWORD '" +
                user_password.replace("'", "''") + "' USING PLUGIN Srp")
        admin.commit()
        assert user_exists()
        rollback()
        result['cases'].append('unicode-srp-account-created')
        for context in (None, path):
            service_route = {
                **route, 'user': username, 'trusted_auth': False,
                'auth_plugin_list': 'Srp256',
                'credential_reference_id': 'owned-service-secret',
                'principal_reference': 'owned-service-principal',
                'service_expected_database': context,
            }
            service_route.pop('database', None)
            client = _create_client(SimpleNamespace(
                acquire_secret=lambda *_args: SecretLease(user_password)))
            services.append(client)
            handle = client._connect_server({'route': service_route})
            assert '5.0.4' in handle.info.version
            assert handle.encoding == 'utf-8'
            observed = service_context(driver, handle.spb)
            assert observed['user'] == username
            assert observed['expected_db'] == context
            assert observed['utf8'] is True
            receipt = client.close_session(handle)
            assert receipt['service_handle_released'] is True
            result['cases'].append(
                'unicode-service-auth-' + ('context' if context else 'server'))
            rejected = _create_client(SimpleNamespace(
                acquire_secret=lambda *_args: SecretLease(
                    user_password + '-wrong')))
            services.append(rejected)
            try:
                rejected._connect_server({'route': service_route})
            except RelationalClientError as exc:
                assert 335544472 in exc.gds_codes, (
                    'Wrong-password check did not receive native login denial')
                assert not rejected._connections
                result['cases'].append('wrong-password-rejected-' +
                                       ('context' if context else 'server'))
            else:
                raise AssertionError('Wrong service credential was accepted')
    except (Exception, KeyboardInterrupt) as exc:
        result['failures'].append({'stage': 'gate',
                                   'error_type': type(exc).__name__,
                                   'message': str(exc).replace(
                                       password, '[redacted]').replace(
                                           user_password, '[redacted]'),
                                   'locations': [
                                       {'file': Path(frame.filename).name,
                                        'line': frame.lineno}
                                       for frame in traceback.extract_tb(
                                           exc.__traceback__)],
                                   'native_status_codes': list(
                                       getattr(exc, 'gds_codes', ()))})
    finally:
        for client in services:
            try:
                client.close()
            except Exception as exc:
                result['failures'].append({'stage': 'service-release',
                                           'error_type': type(exc).__name__})
        if admin is not None:
            try:
                rollback()
                exists = user_exists() if user_owned else False
                rollback()
                if exists:
                    execute('DROP USER ' + quoted_user + ' USING PLUGIN Srp')
                    admin.commit()
                result['owned_user_removed'] = (
                    not user_exists() if user_owned else True)
                rollback()
            except Exception as exc:
                result['failures'].append({'stage': 'owned-user-cleanup',
                                           'error_type': type(exc).__name__})
            try:
                admin.drop_database()
                result['owned_database_removed'] = True
            except Exception as exc:
                result['failures'].append({'stage': 'owned-database-cleanup',
                                           'error_type': type(exc).__name__})
            finally:
                admin.close()
    result['complete'] = (
        len(result['cases']) == 5 and not result['failures'] and
        result['owned_user_removed'] and result['owned_database_removed'])
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profiles', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--ascii-user', action='store_true')
    options = parser.parse_args()
    result = run(options.profiles, options.ascii_user)
    options.output.parent.mkdir(parents=True, exist_ok=True)
    options.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))
    return 0 if result['complete'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
