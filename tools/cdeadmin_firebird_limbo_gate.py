#!/usr/bin/env python3
"""Qualify native limbo reports and resolution in an owned Firebird server."""

import argparse
import json
import io
import os
import re
import secrets
import socket
import subprocess
import tarfile
import tempfile
import time
import traceback
import uuid
from pathlib import Path
from types import SimpleNamespace

if __package__:
    from .cdeadmin_firebird_external_functions_gate import (
        docker, remove_owned, OWNER, _route_arguments,
        _configure_client_library, browser_checks,
    )
else:
    from cdeadmin_firebird_external_functions_gate import (
        docker, remove_owned, OWNER, _route_arguments,
        _configure_client_library, browser_checks,
    )
from pgadmin.cdeadmin.providers.firebird import limbo
from pgadmin.cdeadmin.providers.firebird.provider import (
    _server_arguments, connect_service, _create_client,
)
from pgadmin.cdeadmin.security.secrets import SecretLease
from pgadmin.cdeadmin.providers.firebird.error_diagnostics import status_codes


def run(image, browser_options=None):
    import firebird.driver as native
    import firebird.driver.core as core
    _configure_client_library(native)
    password = secrets.token_urlsafe(24)
    container = server = None
    connections = []
    result = {'complete': False, 'checks': [], 'failures': [],
              'owned_container_removed': False,
              'credential_values_exported': False}

    def failure(label, error):
        result['failures'].append({
            'case': label, 'error_type': type(error).__name__,
            'native_status_codes': list(status_codes(error)),
            'frames': [{'file': Path(frame.filename).name,
                        'line': frame.lineno, 'function': frame.name}
                       for frame in traceback.extract_tb(
                           error.__traceback__)]})

    def connect(path):
        connection = native.connect(password=password, **_route_arguments(
            {**route, 'database': path}, native))
        connections.append(connection)
        return connection

    def create(path):
        connection = native.create_database(
            password=password, **_route_arguments(
                {**route, 'database': path}, native))
        connections.append(connection)
        with connection.cursor() as cursor:
            cursor.execute('CREATE TABLE RECOVERY_MARKER (ID INTEGER)')
        connection.commit()
        return connection

    def prepare(paths):
        handles = [create(path) for path in paths]
        manager = (native.DistributedTransactionManager(
            handles, default_action=native.DefaultAction.ROLLBACK)
            if len(paths) > 1 else handles[0].main_transaction)
        identifiers = []
        for handle in handles:
            cursor = (manager.cursor(handle) if len(paths) > 1
                      else manager.cursor())
            with cursor:
                cursor.execute('INSERT INTO RECOVERY_MARKER VALUES (1)')
                cursor.execute('SELECT CURRENT_TRANSACTION FROM RDB$DATABASE')
                identifiers.append(cursor.fetchone()[0])
        manager._tra.prepare()
        # Native attachment detach preserves a prepared transaction. DB-API
        # close/default-action or releasing the transaction first can resolve
        # it, invalidating the fault fixture. All handles belong to this gate.
        for handle in handles:
            handle._att.detach()
            handle._att = None
        manager._tra.release()
        manager._tra = None
        for handle in handles:
            if handle.main_transaction is not manager:
                handle.main_transaction._tra = None
        return identifiers

    def observe(path):
        connection = connect(path)
        try:
            identifiers = limbo.inventory(connection, native)
            count = None
            if not identifiers:
                with connection.cursor() as cursor:
                    cursor.execute('SELECT COUNT(*) FROM RECOVERY_MARKER')
                    count = cursor.fetchone()[0]
            return {'limbo_ids': [str(value) for value in identifiers],
                    'committed_rows': count}
        finally:
            connection.close()

    phase = 'create-owned-server'
    try:
        bootstrap = '/var/lib/firebird/data/owned_limbo_bootstrap.fdb'
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            port = listener.getsockname()[1]
        container = docker(
            'create', '--name', 'cdeadmin-limbo-' + uuid.uuid4().hex[:16],
            '--label', 'cdeadmin-owned-gate=' + OWNER,
            '--hostname', socket.gethostname(),
            '--publish', f'127.0.0.1:{port}:{port}',
            '--env', 'FIREBIRD_ROOT_PASSWORD',
            '--env', 'FIREBIRD_DATABASE', image,
            env=dict(os.environ, FIREBIRD_ROOT_PASSWORD=password,
                     FIREBIRD_DATABASE=bootstrap)).decode().strip()
        if not re.fullmatch(r'[0-9a-f]{64}', container):
            raise ValueError('Owned container identity is invalid')
        # DTC recovery descriptions retain the original endpoint. Give this
        # owned server the same internal/external port so its native utility
        # can reattach to that exact endpoint without an invented path rewrite.
        archive = docker('cp', container + ':/opt/firebird/firebird.conf', '-')
        with tarfile.open(fileobj=io.BytesIO(archive)) as package:
            files = [item for item in package.getmembers() if item.isfile()]
            assert len(files) == 1
            configuration = package.extractfile(
                files[0]).read().decode('utf-8')
        configuration = re.sub(
            r'(?m)^\s*RemoteServicePort\s*=.*$', '', configuration)
        with tempfile.TemporaryDirectory(
                prefix='cdeadmin-limbo-config-') as tmp:
            config_path = Path(tmp) / 'firebird.conf'
            config_path.write_text(configuration +
                                   f'\nRemoteServicePort = {port}\n')
            docker('cp', str(config_path),
                   container + ':/opt/firebird/firebird.conf')
        docker('start', container)
        assert docker('port', container, f'{port}/tcp').decode().strip() == (
            f'127.0.0.1:{port}')
        route = {'host': '127.0.0.1', 'port': port,
                 'user': 'SYSDBA', 'database': bootstrap, 'timeout': 2,
                 'auth_plugin_list': 'Srp256'}
        phase = 'readiness'
        deadline = time.monotonic() + 45
        ready = None
        while time.monotonic() < deadline:
            try:
                ready = connect(bootstrap)
                break
            except native.Error:
                time.sleep(0.25)
        if ready is None:
            raise RuntimeError('Owned Firebird did not become ready')
        with ready.cursor() as cursor:
            cursor.execute(
                "SELECT RDB$GET_CONTEXT('SYSTEM', 'ENGINE_VERSION') "
                'FROM RDB$DATABASE')
            result['engine_version'] = cursor.fetchone()[0]
        assert result['engine_version'] == '5.0.4'
        ready.close()
        server = connect_service(native, core, password=password,
                                 **_server_arguments(route, native))
        for index, (distributed, action, prior) in enumerate([
                (False, 'commit_limbo', None),
                (False, 'rollback_limbo', None),
                (True, 'commit_limbo', None),
                (True, 'rollback_limbo', None),
                (True, 'recover_limbo', 'commit'),
                (True, 'recover_limbo', 'rollback')]):
            label = f'{distributed}-{action}-{prior}'
            check = {'case': label}
            result['checks'].append(check)
            try:
                paths = [('/var/lib/firebird/data/' +
                          f'owned_limbo_{index}_{part}.fdb')
                         for part in range(2 if distributed else 1)]
                identifiers = prepare(paths)
                before = [observe(path) for path in paths]
                assert [item['limbo_ids'] for item in before] == [
                    [str(identifier)] for identifier in identifiers]
                if prior:
                    participant = connect(paths[0])
                    transaction = participant._att.reconnect_transaction(
                        identifiers[0].to_bytes(8, 'little'))
                    getattr(transaction, prior)()
                    participant.close()
                selected = 1 if prior else 0
                observer = connect(paths[selected])
                check['attachment_report'] = limbo.inspect_attachment(
                    observer, native)
                observer.close()
                assert len(check['attachment_report']) == 1
                observed = check['attachment_report'][0]
                assert observed['transaction_id'] == str(identifiers[selected])
                assert observed['kind'] == (
                    'distributed' if distributed else 'single')
                assert observed['peer_states_observed'] is False
                if distributed:
                    actual = [item['transaction_id'] for item in
                              observed['participants']]
                    assert actual == [str(value) for value in identifiers]
                if distributed:
                    observer = connect(paths[selected])
                    with observer.cursor() as cursor:
                        cursor.execute(
                            'SELECT RDB$TRANSACTION_DESCRIPTION FROM '
                            'RDB$TRANSACTIONS WHERE RDB$TRANSACTION_ID = ?',
                            (identifiers[selected],))
                        description = cursor.fetchone()[0]
                        if hasattr(description, 'read'):
                            description = description.read()
                    observer.close()
                    check['owned_native_description'] = bytes(
                        description).decode('utf-8', errors='backslashreplace')
                    try:
                        listing = docker(
                            'exec', '--env', 'ISC_USER',
                            '--env', 'ISC_PASSWORD',
                            container, '/opt/firebird/bin/gfix', '-list',
                            f'127.0.0.1/{port}:{paths[selected]}',
                            env=dict(os.environ, ISC_USER='SYSDBA',
                                     ISC_PASSWORD=password))
                        check['native_cli_list'] = listing.decode(
                            'utf-8', errors='replace').replace(
                                password, '[redacted]')
                    except subprocess.CalledProcessError as error:
                        check['native_cli_list_exit'] = error.returncode
                try:
                    report = limbo.inspect(
                        server.database, paths[selected], native)
                    check['native_report'] = report
                    assert len(report) == 1
                    assert report[0]['transaction_id'] == str(
                        identifiers[selected])
                except native.Error as error:
                    check['native_service_list_error'] = list(
                        status_codes(error))
                    if not distributed:
                        raise
                if distributed:
                    # Compare an explicit client-owned native coordinator.
                    # Paths and IDs come from this gate's owned fixture, not
                    # an untrusted description or automatic credential reuse.
                    participants = [
                        (connect(path), str(identifiers[part]))
                        for part, path in enumerate(paths)
                        if not (prior and part == 0)]
                    decision = prior or (
                        'rollback' if action == 'rollback_limbo' else 'commit')
                    recovery = limbo.NativeRecovery(
                        participants, decision, native)
                    try:
                        check['client_native_coordinator'] = recovery.run()
                    finally:
                        # Preserve any still-prepared participants on failure;
                        # never use DTC's default-action COMMIT or release the
                        # joined handle before its attachments are detached.
                        recovery.close()
                    assert recovery.closed is True
                else:
                    try:
                        output = limbo.resolve(
                            server.database, paths[selected], action,
                            str(identifiers[selected]), native)
                        check['resolution_output_bytes'] = len(output)
                    except Exception as error:
                        check['resolution_error'] = {
                            'type': type(error).__name__,
                            'native_status_codes': list(status_codes(error))}
                check['after'] = [observe(path) for path in paths]
                expected = 0 if action == 'rollback_limbo' or prior == (
                    'rollback') else 1
                assert all(item['limbo_ids'] == [] and
                           item['committed_rows'] == expected
                           for item in check['after'])
                check['passed'] = True
            except Exception as error:
                check['passed'] = False
                failure(label, error)
        for phase in ('before-decision', 'after-decision', 'detach-failure'):
            check = {'case': phase}
            result['checks'].append(check)
            try:
                paths = [f'/var/lib/firebird/data/owned_limbo_{phase}_{i}.fdb'
                         for i in range(2)]
                identifiers = prepare(paths)
                owned = [connect(path) for path in paths]

                class TransactionProxy:
                    def __init__(self, native_handle):
                        self.native_handle = native_handle

                    @property
                    def _refcnt(self):
                        return self.native_handle._refcnt

                    def join(self, other):
                        return TransactionProxy(self.native_handle.join(
                            other.native_handle))

                    def commit(self):
                        if phase == 'after-decision':
                            self.native_handle.commit()
                        raise RuntimeError('Owned injected response loss')

                    def release(self):
                        return self.native_handle.release()

                # Wrap only the owned reconnect entry point. Native transaction
                # creation, join, optional commit and cleanup remain real.
                for participant in owned:
                    reconnect = participant._att.reconnect_transaction
                    participant._att.reconnect_transaction = (
                        lambda value, reconnect=reconnect:
                        TransactionProxy(reconnect(value)))
                recovery = limbo.NativeRecovery(
                    list(zip(owned, identifiers)), 'commit', native)
                try:
                    recovery.run()
                except RuntimeError:
                    pass
                else:
                    raise AssertionError(
                        'The injected failure was not observed')
                assert recovery.dispatched and not recovery.returned
                if phase == 'detach-failure':
                    original_close = owned[0].close
                    owned[0].close = lambda: (_ for _ in ()).throw(
                        RuntimeError('Owned injected detach failure'))
                    try:
                        recovery.close()
                    except RuntimeError:
                        pass
                    else:
                        raise AssertionError('Failed detach was not retained')
                    assert not recovery.closed and recovery._handles
                    check['other_attachment_released'] = owned[1].is_closed()
                    assert check['other_attachment_released'] is True
                    owned[0].close = original_close
                recovery.close()
                assert recovery.closed
                check['after_cleanup'] = [observe(path) for path in paths]
                if phase == 'after-decision':
                    assert all(item['limbo_ids'] == [] and
                               item['committed_rows'] == 1
                               for item in check['after_cleanup'])
                else:
                    assert [item['limbo_ids'] for item in check[
                        'after_cleanup']] == [[str(value)]
                                              for value in identifiers]
                    # A separate explicit decision, not a cleanup default.
                    cleanup = limbo.NativeRecovery([
                        (connect(path), identifier)
                        for path, identifier in zip(paths, identifiers)],
                        'rollback', native)
                    try:
                        cleanup.run()
                    finally:
                        cleanup.close()
                    assert all(observe(path)['committed_rows'] == 0
                               for path in paths)
                check['passed'] = True
            except Exception as error:
                check['passed'] = False
                failure(phase, error)
        for decision in ('commit', 'rollback'):
            phase = 'provider-local-' + decision
            check = {'case': phase}
            result['checks'].append(check)
            client = None
            try:
                path = ('/var/lib/firebird/data/owned_provider_' +
                        decision + '.fdb')
                identifier = str(prepare([path])[0])
                client = _create_client(SimpleNamespace(
                    acquire_secret=lambda *_args: SecretLease(password)))
                provider_route = {**route, 'database': path,
                                  'credential_reference_id': 'owned-limbo',
                                  'principal_reference': 'owned-qa'}

                def task(operation, draft):
                    request = {'resource_kind': 'database',
                               'operation_id': operation, 'draft': draft,
                               '_provider_route': provider_route}
                    assert not client.config.administration.validate(
                        request)['errors']
                    plan = client.plan_admin_operation(request)
                    assert not client._connections
                    return client.apply_admin_operation(plan)[
                        'driver_observation']

                before = task('inspect_limbo', {})
                assert [item['transaction_id'] for item in before[
                    'transactions']] == [identifier]
                assert before['inventory_complete'] is True
                check['decision'] = task(decision + '_limbo_local',
                                         {'transaction_id': identifier,
                                          'confirmation': identifier,
                                          'database_confirmation': path,
                                          'coordinator_reviewed': True})
                assert check['decision']['native_decision_returned'] is True
                assert check['decision']['attachment_released'] is True
                assert not client._connections and not client._recoveries
                assert task('inspect_limbo', {})['transactions'] == []
                check['independent_post_state'] = observe(path)
                assert check['independent_post_state'] == {
                    'limbo_ids': [],
                    'committed_rows': 1 if decision == 'commit' else 0}
                check['passed'] = True
            except Exception as error:
                check['passed'] = False
                failure(phase, error)
            finally:
                if client is not None:
                    try:
                        client.close()
                    except Exception as error:
                        failure('close-owned-provider', error)
        for role in ('Recovery Role', 'R' * 63):
            phase = 'native-role-' + ('spaced' if ' ' in role else 'maximum')
            check = {'case': phase}
            result['checks'].append(check)
            client = None
            try:
                path = '/var/lib/firebird/data/' + phase + '.fdb'
                identifier = str(prepare([path])[0])
                username = 'LIMBO_' + ('SPACED' if ' ' in role else 'MAXIMUM')
                limited_password = secrets.token_urlsafe(24)
                admin = connect(path)
                try:
                    with admin.cursor() as cursor:
                        cursor.execute('CREATE USER ' + username +
                                       " PASSWORD '" + limited_password +
                                       "' USING PLUGIN Srp")
                        cursor.execute('CREATE ROLE "' + role + '"')
                        cursor.execute('GRANT "' + role + '" TO ' + username)
                    admin.commit()
                finally:
                    admin.close()
                limited_route = {**route, 'database': path, 'user': username,
                                 'role': role,
                                 'credential_reference_id': 'owned-limbo-user',
                                 'principal_reference': 'owned-qa'}
                client = _create_client(SimpleNamespace(
                    acquire_secret=lambda *_args: SecretLease(
                        limited_password)))
                observer = client._connect({'route': limited_route})
                with observer.cursor() as cursor:
                    cursor.execute('SELECT CURRENT_ROLE FROM RDB$DATABASE')
                    actual_role = cursor.fetchone()[0].strip()
                client._release_attachment(observer)
                check['native_current_role'] = actual_role
                assert actual_role == role
                observation = client.run_limbo_operation(
                    {'route': {**limited_route, 'role': None}},
                    'inspect_limbo', {'role': role})
                assert [item['transaction_id'] for item in observation[
                    'transactions']] == [identifier]
                # This deliberately calls the native provider adapter, not
                # the application's maintenance permission admission layer.
                # Qualify what Firebird actually permits for a non-owner.
                check['native_nonowner_decision'] = client.run_limbo_operation(
                    {'route': {**limited_route, 'role': None}},
                    'rollback_limbo_local', {
                        'role': role, 'transaction_id': identifier,
                        'confirmation': identifier,
                        'database_confirmation': path,
                        'coordinator_reviewed': True})
                assert check['native_nonowner_decision'][
                    'native_decision_returned'] is True
                assert observe(path) == {'limbo_ids': [], 'committed_rows': 0}
                check['passed'] = True
            except Exception as error:
                check['passed'] = False
                failure(phase, error)
            finally:
                if client is not None:
                    try:
                        client.close()
                    except Exception as error:
                        failure('close-owned-role-provider', error)
        phase = 'large-limbo-inventory'
        check = {'case': phase}
        result['checks'].append(check)
        try:
            path = '/var/lib/firebird/data/owned_limbo_large.fdb'
            create(path).close()
            identifiers = []
            for value in range(200):
                participant = connect(path)
                with participant.cursor() as cursor:
                    cursor.execute('INSERT INTO RECOVERY_MARKER VALUES (?)',
                                   (value,))
                    cursor.execute('SELECT CURRENT_TRANSACTION '
                                   'FROM RDB$DATABASE')
                    identifiers.append(cursor.fetchone()[0])
                transaction = participant.main_transaction._tra
                transaction.prepare()
                participant._att.detach()
                participant._att = None
                transaction.release()
                participant.main_transaction._tra = None
            observer = connect(path)
            local = limbo.inspect_attachment(observer, native)
            observer.close()
            assert {item['transaction_id'] for item in local} == {
                str(value) for value in identifiers}
            service_rows = limbo.inspect(server.database, path, native)
            check['attachment_inventory_count'] = len(local)
            check['service_inventory_count'] = len(service_rows)
            check['native_service_inventory_incomplete'] = (
                len(service_rows) < len(local))
            assert check['native_service_inventory_incomplete'] is True
            recovery = limbo.NativeRecovery([
                (connect(path), identifier) for identifier in identifiers],
                'rollback', native)
            try:
                recovery.run()
            finally:
                recovery.close()
            assert observe(path) == {'limbo_ids': [], 'committed_rows': 0}
            check['passed'] = True
        except Exception as error:
            check['passed'] = False
            failure(phase, error)
        if browser_options is not None:
            result['browser_checks'] = []
            for scale in browser_options.font_scale or (100, 200, 300):
                phase = 'browser-' + str(scale)
                folder = browser_options.build_root / phase
                folder.mkdir(parents=True, exist_ok=False)
                path = ('/var/lib/firebird/data/owned_limbo_browser_' +
                        str(scale) + '.fdb')
                try:
                    create(path).close()
                    selected_options = SimpleNamespace(**{
                        **vars(browser_options), 'font_scale': [scale]})
                    result['browser_checks'].extend(browser_checks(
                        selected_options, {**route, 'database': path},
                        password,
                        container, folder, gate_kind='limbo',
                        fixture_kind='firebird-limbo-qualification'))
                except Exception as error:
                    failure(phase, error)
            if not result['browser_checks'] or not all(
                    item['passed'] for item in result['browser_checks']):
                result['failures'].append({'case': 'browser-qualification',
                                           'error_type': 'FailedBrowserGate'})
    except Exception as error:
        failure(phase, error)
    finally:
        for connection in connections:
            try:
                connection.close()
            except Exception as error:
                failure('close-owned-attachment', error)
        if server is not None:
            try:
                server.close()
            except Exception as error:
                failure('close-owned-service', error)
        if container:
            try:
                remove_owned(container)
                result['owned_container_removed'] = True
            except Exception as error:
                failure('remove-owned-container', error)
    result['complete'] = (len(result['checks']) == 14 and
                          not result['failures'] and
                          result['owned_container_removed'])
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', default='firebirdsql/firebird:5.0.4')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--browser', action='store_true')
    parser.add_argument('--build-root', type=Path)
    parser.add_argument('--source-config-db', type=Path,
                        default=Path('/var/lib/cdeadmin/cdeadmin.db'))
    parser.add_argument('--desktop-user', default='dalton.calford@gmail.com')
    parser.add_argument('--font-scale', type=int, action='append',
                        choices=(100, 200, 300))
    options = parser.parse_args()
    if options.output.exists():
        parser.error('Use a new evidence file')
    if options.browser:
        if options.build_root is None or options.build_root.exists():
            parser.error('Browser tests require a new --build-root directory')
        options.build_root.mkdir(parents=True, exist_ok=False)
    result = run(options.image, options if options.browser else None)
    options.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result))
    return 0 if result['complete'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
