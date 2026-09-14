#!/usr/bin/env python3
"""Observe native creation attachments in uniquely owned databases."""

import argparse
import importlib.metadata
import json
import subprocess
import traceback
import uuid
from pathlib import Path, PurePosixPath
from types import SimpleNamespace
from unittest.mock import patch

from cdeadmin_firebird_address_gate import ipv6_relay
from cdeadmin_firebird_admin_mapping_gate import (
    ADMINISTRATION, _create_client, _route_arguments,
)
from pgadmin.cdeadmin.security.secrets import SecretLease


def run(profiles, container):
    import firebird.driver as driver
    from firebird.driver.types import DPBItem, XpbKind
    document = json.loads(profiles.read_text())
    route = next(dict(item) for item in document['profiles']
                 if item['engine'] == 'firebird')
    route.setdefault('host', document.get('host', '127.0.0.1'))
    if route['host'] not in {'127.0.0.1', 'localhost'}:
        raise ValueError('This gate requires the loopback reference demo')
    password = route.pop('password')
    route.update(credential_reference_id='owned-creation-settings',
                 principal_reference='owned-creation-principal')
    client = _create_client(SimpleNamespace(
        acquire_secret=lambda *_args: SecretLease(password)))
    result = {'complete': False, 'cases': [], 'failures': [],
              'driver_version': importlib.metadata.version('firebird-driver'),
              'cleanup': [], 'credential_values_exported': False,
              'windows_authentication_qualified': False}
    real_create = driver.create_database

    def exists(path):
        process = subprocess.run(['docker', 'exec', container, 'test', '-e',
                                  path], capture_output=True, check=False)
        if process.returncode not in {0, 1}:
            raise RuntimeError('Owned fixture existence cannot be observed')
        return process.returncode == 0

    def failure(name, exc):
        result['failures'].append({'case': name,
                                   'error_type': type(exc).__name__,
                                   'message': str(exc).replace(password,
                                                               '[redacted]')})

    with ipv6_relay((route['host'], int(route['port']))) as (port, errors):
        variants = [
            ('INET4', False, 8192, 'WIN1252', 1),
            ('INET4', True, 16384, 'UTF8', 3),
            ('INET6', False, 4096, 'ISO8859_1', 3),
            ('INET6', True, 32768, 'UTF8', 3),
        ]
        for protocol, compression, page_size, charset, dialect in variants:
            name = f'{protocol}-compression-{compression}-dialect-{dialect}'
            path = str(PurePosixPath(route['database']).parent /
                       ('cde_create_settings_' + uuid.uuid4().hex + '.fdb'))
            values = {**route, 'protocol': protocol,
                      'host': '::1' if protocol == 'INET6' else route['host'],
                      'port': port if protocol == 'INET6' else route['port'],
                      'timeout': 12, 'dummy_packet_interval': 20,
                      'auth_plugin_list': 'Srp256', 'wire_crypt': 'Required',
                      'wire_compression': compression, 'no_gc': True,
                      'no_db_triggers': True, 'dbkey_scope': 'ATTACHMENT',
                      'session_time_zone': 'America/Toronto',
                      'charset': 'UTF8'}
            observation = {}

            def observed_create(**arguments):
                connection = real_create(**arguments)
                try:
                    # Read only an explicit non-secret allowlist. Do not use
                    # driver 1.10.11 DPB.parse_buffer, which never advances.
                    integers = {
                        DPBItem.CONNECT_TIMEOUT: 12,
                        DPBItem.DUMMY_PACKET_INTERVAL: 20,
                        DPBItem.NO_DB_TRIGGERS: 1,
                        DPBItem.NO_GARBAGE_COLLECT: 1,
                        DPBItem.DBKEY_SCOPE: driver.DBKeyScope.ATTACHMENT,
                    }
                    strings = {
                        DPBItem.AUTH_PLUGIN_LIST: 'Srp256',
                        DPBItem.SESSION_TIME_ZONE: 'America/Toronto',
                    }
                    with driver.get_api().util.get_xpb_builder(
                            XpbKind.DPB, connection._dpb) as dpb:
                        for tag, expected in integers.items():
                            assert dpb.find_first(tag)
                            assert dpb.get_int() == expected
                        for tag, expected in strings.items():
                            assert dpb.find_first(tag)
                            assert dpb.get_string() == expected
                        assert dpb.find_first(DPBItem.CONFIG)
                        configuration = dpb.get_string()
                    assert 'WireCrypt=Required' in configuration
                    assert ('WireCompression=' + str(compression).lower()
                            in configuration)
                    observation['selected_dpb_options_verified'] = True
                    with connection.cursor() as cursor:
                        cursor.execute(
                            'SELECT MON$AUTH_METHOD, MON$WIRE_COMPRESSED, '
                            'MON$WIRE_ENCRYPTED, MON$GARBAGE_COLLECTION '
                            'FROM MON$ATTACHMENTS WHERE '
                            'MON$ATTACHMENT_ID = CURRENT_CONNECTION')
                        auth, compressed, encrypted, gc = cursor.fetchone()
                        assert auth.rstrip() == 'Srp256'
                        assert bool(compressed) is compression
                        assert bool(encrypted) is True
                        # Firebird 5.0.4 applies dpb_no_garbage to attach,
                        # not create (jrd.cpp). Record actual native behavior;
                        # the DPB assertion above only proves transmission.
                        assert bool(gc) is True
                        observation['creation_garbage_collection'] = bool(gc)
                        cursor.execute(
                            "SELECT RDB$CHARACTER_SET_NAME, "
                            "RDB$GET_CONTEXT('SYSTEM', 'SESSION_TIMEZONE') "
                            'FROM RDB$DATABASE')
                        default_charset, timezone = cursor.fetchone()
                        assert default_charset.rstrip() == charset
                        assert timezone == 'America/Toronto'
                    assert connection.info.page_size == page_size
                    assert connection.info.sql_dialect == dialect
                    connection.rollback()
                    observation.update(
                        authentication=auth.rstrip(),
                        wire_compressed=bool(compressed),
                        wire_encrypted=bool(encrypted), page_size=page_size,
                        database_charset=charset, database_sql_dialect=dialect,
                        session_time_zone=timezone)
                except Exception as exc:
                    observation['failed_check'] = [
                        {'function': frame.name, 'line': frame.lineno}
                        for frame in traceback.extract_tb(exc.__traceback__)]
                    connection.close()
                    raise
                return connection

            requested = False
            try:
                assert not exists(path)
                plan = ADMINISTRATION.plan({
                    '_provider_route': values, 'resource_kind': 'database',
                    'operation_id': 'create', 'target_resource': None,
                    'draft': {'database_path': path, 'page_size': page_size,
                              'default_charset': charset,
                              'sql_dialect': dialect}})
                requested = True
                with patch.object(driver, 'create_database',
                                  side_effect=observed_create) as create:
                    receipt = ADMINISTRATION.apply(client, plan)
                assert create.call_count == 1
                assert receipt['accepted'] is True
                assert exists(path)
                connection = driver.connect(
                    password=password, **_route_arguments(
                        {**values, 'database': path}, driver))
                try:
                    with connection.cursor() as cursor:
                        cursor.execute(
                            'SELECT MON$GARBAGE_COLLECTION '
                            'FROM MON$ATTACHMENTS WHERE '
                            'MON$ATTACHMENT_ID = CURRENT_CONNECTION')
                        assert bool(cursor.fetchone()[0]) is False
                    connection.rollback()
                    observation['reattachment_garbage_collection'] = False
                finally:
                    connection.close()
                result['cases'].append({'case': name, **observation})
            except Exception as exc:
                failure(name, exc)
                result['failures'][-1]['observed'] = observation
            finally:
                if requested:
                    try:
                        if exists(path):
                            arguments = _route_arguments(
                                {**route, 'database': path}, driver)
                            connection = driver.connect(
                                password=password, **arguments)
                            connection.drop_database()
                        assert not exists(path)
                        result['cleanup'].append({'path': path,
                                                  'removed': True})
                    except Exception as exc:
                        failure(name + '-cleanup', exc)
        result['relay_errors'] = errors
    result['relay_stopped'] = True
    result['complete'] = (len(result['cases']) == 4 and
                          len(result['cleanup']) == 4 and
                          not result['failures'] and
                          not result['relay_errors'])
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
