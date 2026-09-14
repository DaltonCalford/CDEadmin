#!/usr/bin/env python3
##########################################################################
# CDEadmin - Multi-engine Database Administration
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
##########################################################################

"""Verify local admin-mapping DDL in a uniquely named disposable database."""

import argparse
import json
import os
import secrets
import subprocess
import sys
import traceback
import uuid
from pathlib import Path, PurePosixPath
from types import ModuleType, SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'web'))
if 'pgadmin' not in sys.modules:
    package = ModuleType('pgadmin')
    package.__path__ = [str(ROOT / 'web/pgadmin')]
    sys.modules['pgadmin'] = package

from pgadmin.cdeadmin.providers.firebird.provider import (  # noqa: E402
    ADMINISTRATION, _admin_mapping_state, _create_client, _resources,
    _route_arguments,
)
from pgadmin.cdeadmin.sdk.relational import RelationalClientError  # noqa: E402


def run(profiles, browser_options=None):
    document = json.loads(profiles.read_text())
    route = next(dict(p) for p in document['profiles']
                 if p['engine'] == 'firebird')
    route.setdefault('host', document.get('host', '127.0.0.1'))
    route['database'] = str(PurePosixPath(route['database']).parent /
                            ('cdeadmin_mapping_' + uuid.uuid4().hex + '.fdb'))
    client = _create_client(SimpleNamespace(acquire_secret=None))
    from firebird import driver
    connection = driver.create_database(
        password=route['password'], **_route_arguments(route, driver))
    result = {'status': 'failed', 'checks': [], 'failures': [],
              'fixture_database': route['database'],
              'fixture_removed': False,
              'windows_authentication_verified': False}
    target = {'resource_kind': 'role', 'display_name': 'RDB$ADMIN'}
    username = 'CDE_MAP_' + uuid.uuid4().hex[:16].upper()
    result['temporary_user'] = username
    user_created = False

    def user_operation(operation, draft):
        plan = ADMINISTRATION.plan({
            '_provider_route': route, 'resource_kind': 'user',
            'operation_id': operation,
            'target_resource': {'display_name': username}, 'draft': draft})
        ADMINISTRATION.apply(client, plan, connection=connection)
        connection.commit()

    def state():
        with connection.cursor() as cursor:
            return _admin_mapping_state(cursor)

    def apply(action):
        plan = ADMINISTRATION.plan({
            '_provider_route': route, 'resource_kind': 'role',
            'operation_id': 'configure_admin_mapping',
            'target_resource': target, 'draft': {'mapping_action': action}})
        ADMINISTRATION.apply(client, plan, connection=connection)
        result.setdefault('statements', {})[action] = plan[
            'command_preview']['statements'][0]['source']

    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT RDB$GET_CONTEXT('SYSTEM', 'ENGINE_VERSION') "
                'FROM RDB$DATABASE')
            result['engine_version'] = cursor.fetchone()[0]
        assert state()['present'] is False
        connection.commit()
        apply('SET')
        assert state()['canonical'] is True
        connection.rollback()
        assert state()['present'] is False
        connection.commit()
        result['checks'].append('set-rollback-restores-absence')
        for iteration in range(2):
            apply('SET')
            connection.commit()
            assert state()['canonical'] is True
            connection.commit()
        result['checks'].append('set-commit-and-replace-idempotent')
        password = secrets.token_urlsafe(24)
        user_operation('create', {'name': username, 'password': password})
        user_created = True
        user_route = {**route, 'user': username}
        other = driver.connect(password=password,
                               **_route_arguments(user_route, driver))
        try:
            for action in ('SET', 'DROP'):
                plan = ADMINISTRATION.plan({
                    '_provider_route': user_route, 'resource_kind': 'role',
                    'operation_id': 'configure_admin_mapping',
                    'target_resource': target,
                    'draft': {'mapping_action': action}})
                try:
                    ADMINISTRATION.apply(client, plan, connection=other)
                except RelationalClientError as error:
                    assert 'DatabaseError' in str(error), error
                else:
                    raise AssertionError('Unprivileged mapping change allowed')
                assert state()['canonical'] is True
                connection.commit()
                result['checks'].append('unprivileged-' + action + '-denied')
        finally:
            other.close()
        role = next(item for item in _resources(connection, {'route': route})
                    if item['resource_kind'] == 'role' and
                    item['display_name'] == 'RDB$ADMIN')
        assert role['native']['auto_admin_mapping']['canonical'] is True
        assert not role['native'].get('ddl')
        assert 'built-in' in role['native']['ddl_unavailable_reason']
        connection.commit()
        result['checks'].append('system-role-inspector-no-fabricated-create')
        apply('DROP')
        assert state()['present'] is False
        connection.rollback()
        assert state()['canonical'] is True
        connection.commit()
        result['checks'].append('drop-rollback-restores-mapping')
        apply('DROP')
        connection.commit()
        assert state()['present'] is False
        connection.commit()
        result['checks'].append('drop-commit-removes-mapping')
        try:
            apply('DROP')
        except RelationalClientError as error:
            assert 'DatabaseError' in str(error), error
            assert not connection.main_transaction.is_active()
        else:
            raise AssertionError('DROP of absent native mapping succeeded')
        assert state()['present'] is False
        connection.commit()
        result['checks'].append('absent-drop-native-error-no-state-change')
        apply('SET')
        connection.close()
        connection = driver.connect(password=route['password'],
                                    **_route_arguments(route, driver))
        assert state()['present'] is False
        connection.commit()
        result['checks'].append('uncommitted-close-discards-mapping')
        if browser_options is not None:
            root = browser_options.output.parent / 'browser-execution'
            command = [sys.executable, str(ROOT / 'tools/'
                       'cdeadmin_firebird_ui_orchestrator.py'),
                       '--source-config-db',
                       str(browser_options.source_config_db),
                       '--desktop-user', browser_options.desktop_user,
                       '--database', route['database'],
                       '--firebird-port', str(route['port']),
                       '--client-library', os.environ[
                           'CDEADMIN_FIREBIRD_CLIENT_LIBRARY'],
                       '--profiles', str(profiles), '--gate-kind', 'mapping',
                       '--timeout', '30']
            for key, filename in (
                    ('evidence-root', 'screenshots'),
                    ('summary-output', 'summary.json'),
                    ('manifest-output', 'manifest.json'),
                    ('output', 'result.json'), ('server-log', 'server.log'),
                    ('browser-log', 'browser.log')):
                command.extend(['--' + key, str(root / filename)])
            environment = dict(os.environ)
            environment['CDEADMIN_FIREBIRD_DEMO_PASSWORD'] = route['password']
            completed = subprocess.run(command, env=environment, check=False)
            assert completed.returncode == 0, 'Browser mutation gate failed'
            with connection.cursor() as cursor:
                assert _admin_mapping_state(cursor)['present'] is False
            connection.commit()
            result['checks'].append('browser-set-drop-native-postconditions')
        result['status'] = 'passed'
    except Exception as error:
        result['failures'].append({'type': type(error).__name__,
                                   'message': str(error),
                                   'traceback': traceback.format_exc()})
    finally:
        try:
            try:
                connection.close()
            except Exception:
                pass
            connection = driver.connect(password=route['password'],
                                        **_route_arguments(route, driver))
            if user_created:
                user_operation('drop', {'confirmation': username})
                with connection.cursor() as cursor:
                    cursor.execute('SELECT COUNT(*) FROM SEC$USERS '
                                   'WHERE SEC$USER_NAME = ?', (username,))
                    assert cursor.fetchone()[0] == 0
                connection.commit()
                user_created = False
            connection.drop_database()
            result['fixture_removed'] = True
        except Exception as error:
            result['status'] = 'failed'
            result['failures'].append({'cleanup': type(error).__name__})
        result['temporary_user_removed'] = not user_created
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profiles', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--browser', action='store_true')
    parser.add_argument('--source-config-db', type=Path)
    parser.add_argument('--desktop-user')
    options = parser.parse_args()
    if options.browser and not (options.source_config_db and
                                options.desktop_user):
        parser.error('--browser requires --source-config-db '
                     'and --desktop-user')
    result = run(options.profiles, options if options.browser else None)
    options.output.parent.mkdir(parents=True, exist_ok=True)
    options.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))
    return 0 if result['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
