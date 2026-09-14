#!/usr/bin/env python3
"""Verify Firebird TCP address families with an owned IPv6 loopback relay.

The relay does not modify the reference server/network configuration. Only a
uniquely named database is created and dropped. Demo data is read-only.
"""

import argparse
import importlib.metadata
import ipaddress
import json
import select
import socket
import socketserver
import subprocess
import threading
import uuid
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from types import SimpleNamespace

from cdeadmin_firebird_admin_mapping_gate import (
    ADMINISTRATION, RelationalClientError, _create_client, _route_arguments,
)
from pgadmin.cdeadmin.providers.firebird.provider import _server_arguments
from pgadmin.cdeadmin.providers.firebird.connection_strings import database_dsn
from pgadmin.cdeadmin.security.secrets import SecretLease


@contextmanager
def ipv6_relay(upstream):
    stopping = threading.Event()
    errors = []

    class Handler(socketserver.BaseRequestHandler):
        def handle(self):
            try:
                with socket.create_connection(upstream, timeout=10) as remote:
                    self.request.settimeout(10)
                    while not stopping.is_set():
                        readable, _, _ = select.select(
                            [self.request, remote], [], [], 0.2)
                        for source in readable:
                            data = source.recv(65536)
                            if not data:
                                return
                            target = (remote if source is self.request
                                      else self.request)
                            target.sendall(data)
            except Exception as exc:
                if not stopping.is_set():
                    errors.append(type(exc).__name__)

    class Server(socketserver.ThreadingTCPServer):
        address_family = socket.AF_INET6
        daemon_threads = False

    server = Server(('::1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        yield server.server_address[1], errors
    finally:
        stopping.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        if thread.is_alive():
            raise RuntimeError('Owned IPv6 relay did not stop')


def run(profiles, container):
    import firebird.driver as driver
    document = json.loads(profiles.read_text())
    route = next(dict(item) for item in document['profiles']
                 if item['engine'] == 'firebird')
    route.setdefault('host', document.get('host', '127.0.0.1'))
    if not ipaddress.ip_address(route['host']).is_loopback:
        raise ValueError('The address gate requires a loopback demo profile')
    password = route.pop('password')
    route.update(credential_reference_id='owned-address-secret',
                 principal_reference='owned-address-principal')
    client = _create_client(SimpleNamespace(
        acquire_secret=lambda *_args: SecretLease(password)))
    path = str(PurePosixPath(route['database']).parent /
               ('cde_address_' + uuid.uuid4().hex + '.fdb'))
    result = {'complete': False, 'cases': [], 'failures': [],
              'driver_version': importlib.metadata.version('firebird-driver'),
              'fixture_database': path, 'fixture_removed': False,
              'relay_stopped': False, 'credential_values_exported': False,
              'windows_server_qualified': False}
    creation_requested = False

    def exists(target=None):
        process = subprocess.run(
            ['docker', 'exec', container, 'test', '-e', target or path],
            capture_output=True, check=False)
        if process.returncode not in (0, 1):
            raise RuntimeError('Cannot observe owned fixture file')
        return process.returncode == 0

    def failure(case, exc):
        cause = exc.__cause__ or exc
        result['failures'].append({'case': case,
                                   'error_type': type(exc).__name__,
                                   'message': str(exc).replace(
                                       password, '[redacted]'),
                                   'native_status_codes': list(
                                       getattr(cause, 'gds_codes', ()) or ()),
                                   'native_message': str(cause).replace(
                                       password, '[redacted]')})

    def attach_case(name, values, expected_path):
        connection = driver.connect(
            password=password, **_route_arguments(values, driver))
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT RDB$GET_CONTEXT('SYSTEM', "
                               "'ENGINE_VERSION'), RDB$GET_CONTEXT('SYSTEM', "
                               "'DB_NAME') FROM RDB$DATABASE")
                version, actual_path = cursor.fetchone()
                assert version == '5.0.4'
                assert actual_path == expected_path
            connection.rollback()
        finally:
            connection.close()
        result['cases'].append({'case': name, 'engine_version': version,
                                'database_identity_verified': True,
                                'connection_closed': True})

    try:
        assert not exists(), 'Owned fixture path already exists'
        with ipv6_relay((route['host'], int(route['port']))) as (port, errors):
            routes = [
                ('ipv4-legacy', dict(route)),
                ('ipv4-inet4', {**route, 'protocol': 'INET4'}),
                ('ipv6-legacy', {**route, 'host': '::1', 'port': port}),
                ('ipv6-inet', {**route, 'host': '::1', 'port': port,
                               'protocol': 'INET', 'timeout': 10}),
                ('ipv6-inet6', {**route, 'host': '::1', 'port': port,
                                'protocol': 'INET6', 'timeout': 10}),
                ('ipv6-bracketed', {**route, 'host': '[::1]', 'port': port,
                                    'protocol': 'INET6', 'timeout': 10}),
            ]
            for name, values in routes:
                try:
                    attach_case(name, values, route['database'])
                except Exception as exc:
                    failure(name, exc)
                try:
                    service = driver.connect_server(
                        password=password,
                        **_server_arguments(values, driver))
                    try:
                        assert '5.0.4' in service.info.version
                    finally:
                        service.close()
                    result['cases'].append({'case': name + '-service',
                                            'version_verified': True,
                                            'connection_closed': True})
                except Exception as exc:
                    failure(name + '-service', exc)
            values = routes[4][1]
            try:
                plan = ADMINISTRATION.plan({
                    '_provider_route': values, 'resource_kind': 'database',
                    'operation_id': 'create', 'target_resource': None,
                    'draft': {'database_path': path}})
                creation_requested = True
                receipt = ADMINISTRATION.apply(client, plan)
                assert receipt['accepted'] is True
                assert exists()
                result['cases'].append({'case': 'ipv6-create-database',
                                        'native_file_verified': True})
                for name, variant in routes:
                    try:
                        attach_case(name + '-owned-database',
                                    {**variant, 'database': path}, path)
                    except Exception as exc:
                        failure(name + '-owned-database', exc)
                drop_route = {**values, 'database': path}
                plan = ADMINISTRATION.plan({
                    '_provider_route': drop_route, 'resource_kind': 'database',
                    'operation_id': 'drop', 'target_resource': {
                        'resource_kind': 'database', 'display_name': path,
                        'display_path': [path], 'extensions': {'cdeadmin': {
                            'database_target_id': str(uuid.uuid4())}}},
                    'draft': {'confirmation': path}})
                receipt = ADMINISTRATION.apply(client, plan)
                assert receipt['accepted'] is True
                assert not exists()
                result['fixture_removed'] = True
                result['cases'].append({'case': 'ipv6-drop-database',
                                        'native_file_absence_verified': True})
                rejected_path = path[:-4] + ':part.fdb'
                assert not exists(rejected_path)
                codes = []
                for dispatch in ('native', 'provider'):
                    try:
                        if dispatch == 'native':
                            config_name = (
                                'address_negative_' + uuid.uuid4().hex)
                            config = driver.driver_config.register_database(
                                config_name)
                            config.database.value = database_dsn(
                                rejected_path, '::1', port, 'INET6')
                            connection = driver.create_database(
                                database=config_name, user=route['user'],
                                password=password, charset='UTF8')
                            connection.drop_database()
                        else:
                            plan = ADMINISTRATION.plan({
                                '_provider_route': values,
                                'resource_kind': 'database',
                                'operation_id': 'create',
                                'target_resource': None,
                                'draft': {'database_path': rejected_path}})
                            ADMINISTRATION.apply(client, plan)
                        raise AssertionError('Expected native path rejection')
                    except (driver.DatabaseError,
                            RelationalClientError) as exc:
                        native_codes = list(
                            getattr(exc, 'gds_codes', ()) or ())
                        assert native_codes == [335544375]
                        codes.append(native_codes)
                    finally:
                        if exists(rejected_path):
                            arguments = _route_arguments(
                                {**route, 'database': rejected_path}, driver)
                            connection = driver.connect(
                                password=password, **arguments)
                            connection.drop_database()
                    assert not exists(rejected_path)
                assert codes[0] == codes[1]
                result['cases'].append({
                    'case': 'colon-path-native-rejection-preserved',
                    'native_status_codes': codes[0],
                    'native_file_absence_verified': True})
            except Exception as exc:
                failure('owned-database-lifecycle', exc)
                if not exists():
                    try:
                        connection = driver.create_database(
                            database=database_dsn(path, '::1', port, 'INET6'),
                            user=route['user'], password=password,
                            charset='UTF8')
                        connection.drop_database()
                        result['direct_native_create_accepted'] = True
                    except Exception as native_exc:
                        failure('direct-native-create-diagnostic', native_exc)
            result['relay_errors'] = errors
        result['relay_stopped'] = True
    except Exception as exc:
        failure('gate', exc)
    finally:
        if creation_requested and not result['fixture_removed']:
            try:
                if exists():
                    arguments = _route_arguments(
                        {**route, 'database': path}, driver)
                    connection = driver.connect(password=password, **arguments)
                    connection.drop_database()
                result['fixture_removed'] = not exists()
            except Exception as exc:
                failure('cleanup', exc)
    result['complete'] = (len(result['cases']) == 21 and
                          result['fixture_removed'] and
                          result['relay_stopped'] and
                          not result.get('relay_errors') and
                          not result['failures'])
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profiles', type=Path, required=True)
    parser.add_argument('--container', default='cdeadmin-demo-firebird')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = run(args.profiles, args.container)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))
    return 0 if result['complete'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
