#!/usr/bin/env python3
##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Run the destructive, self-cleaning YugabyteDB YCQL activation gate."""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from pathlib import Path
from types import ModuleType, SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / 'web'
if str(WEB) not in sys.path:
    sys.path.insert(0, str(WEB))
if 'pgadmin' not in sys.modules:
    package = ModuleType('pgadmin')
    package.__path__ = [str(WEB / 'pgadmin')]
    sys.modules['pgadmin'] = package

from pgadmin.cdeadmin.providers.yugabytedb_ycql.client import (  # noqa: E402
    YugabyteDBYCQLClient,
)
from pgadmin.cdeadmin.providers.yugabytedb_ycql.provider import (  # noqa: E402
    PROFILE,
    create_provider,
)


EXPECTED_DRIVER = '3.30.1'
EXPECTED_SERVER = '2025.2.2.2'


class Lease:
    def __init__(self, value):
        self.value = bytearray(value.encode('utf-8'))

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        for index in range(len(self.value)):
            self.value[index] = 0

    def use(self, callback):
        return callback(memoryview(self.value))


class Permissions:
    def __init__(self, password):
        self.password = password

    @staticmethod
    def require(_permission, _scope='endpoint'):
        return None

    @staticmethod
    def allows(_permission, _scope='endpoint'):
        return True

    def acquire_secret(self, reference, *_args):
        if reference != 'qualification-admin' or self.password is None:
            raise RuntimeError('qualification credential is unavailable')
        return Lease(self.password)


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument('--host', default='127.0.0.1')
    result.add_argument('--port', type=int, default=9042)
    result.add_argument('--version-api-host')
    result.add_argument('--version-api-port', type=int, default=7000)
    result.add_argument('--local-dc', default='datacenter1')
    result.add_argument('--username')
    result.add_argument('--password-env', default='CDEADMIN_YCQL_PASSWORD')
    result.add_argument('--output', type=Path)
    return result


def context():
    endpoint = str(uuid.uuid4())
    return SimpleNamespace(
        endpoint_id=endpoint,
        mode='legacy_native',
        experience_family='yugabytedb-ycql',
        provider_id=PROFILE.provider_id,
        provider_version='0.1.0',
        profile_id=PROFILE.profile_id,
        profile_version=PROFILE.exact_version,
        target_adapter_id='ycql-cassandra-driver',
        target_adapter_version='cassandra-driver-3.30.1-ycql-v4',
        pool_namespace=str(uuid.uuid4()),
        session_namespace=str(uuid.uuid4()),
        cache_namespace=str(uuid.uuid4()),
        diagnostic_namespace=str(uuid.uuid4()),
        effective_permissions=frozenset({
            'network', 'secret_read', 'data_read', 'data_write',
            'administer', 'execute',
        }),
        declared_runtime_family='yugabytedb',
        verified_runtime_family='yugabytedb',
        verified_runtime_version=EXPECTED_SERVER,
        runtime_verification_state='verified',
        runtime_evidence_reference=(
            'cde-yugabytedb-ycql-live:2025.2.2.2:20260903'
        ),
        runtime_identity_generation='yugabytedb-ycql-live',
    )


def target(kind, name, native):
    return {
        'resource_id': f'qualification:{kind}:{name}',
        'resource_kind': kind,
        'display_name': name,
        'display_path': ['yugabytedb', 'ycql', kind, name],
        'authority_path': ['yugabytedb', 'ycql', kind, name],
        'generation': 'live-qualification',
        'extensions': {'yugabytedb': {
            'provider_owned': True, 'native': native,
        }},
    }


def request(route, kind, operation, draft=None, native_target=None):
    return {
        'resource_kind': kind,
        'operation_id': operation,
        'target_resource': native_target,
        'draft': draft or {},
        '_provider_route': route,
    }


def apply(provider, value):
    validation = provider.validate_visual_admin(value)
    if not validation['valid']:
        raise RuntimeError(
            'visual validation failed: ' + json.dumps(
                validation['errors'], sort_keys=True
            )
        )
    plan = provider.plan_visual_admin(value)
    if plan['state'] != 'ready':
        raise RuntimeError('visual plan did not become ready')
    result = provider.apply_visual_admin({
        'plan_id': plan['plan_id'],
        'plan_digest': plan['plan_digest'],
        'confirmed': True,
    })
    native = result['provider_result']
    if native.get('accepted') is not True:
        raise RuntimeError('provider plan was not accepted')
    if native.get('transaction_finality_interpreted_by_common_code'):
        raise RuntimeError('common code interpreted YCQL finality')
    return result


def verify(args):
    import cassandra

    run_id = uuid.uuid4().hex[:12]
    keyspace = f'cdeadmin_ycql_{run_id}'
    table_name = 'events'
    type_name = 'address'
    index_name = 'events_value'
    role_name = f'cdeadmin_ycql_role_{run_id}'
    password = os.environ.get(args.password_env) if args.username else None
    if args.username and password is None:
        raise RuntimeError(
            f'credential environment variable {args.password_env} is unset'
        )
    permissions = Permissions(password)
    client = YugabyteDBYCQLClient(permissions.acquire_secret)
    provider = create_provider(context(), permissions, client)
    route = {
        'route_id': 'exact-live-qualification',
        'host': args.host,
        'port': args.port,
        'version_api_host': args.version_api_host or args.host,
        'version_api_port': args.version_api_port,
        'version_api_scheme': 'http',
        'local_dc': args.local_dc,
        'tls_mode': 'disabled',
        'compression': 'none',
        'consistency': 'LOCAL_ONE',
        'serial_consistency': 'LOCAL_SERIAL',
        'protocol_version': 4,
        'request_timeout': 60,
        'connect_timeout': 15,
    }
    if args.username:
        route.update({
            'username': args.username,
            'credential_reference_id': 'qualification-admin',
            'principal_reference': 'cdeadmin-live-qualifier',
        })
    endpoint_request = {
        'route': route,
        'capability_generation': f'live-{run_id}',
    }
    categories = {}
    failures = []
    table_resource = None

    def category(name, callback):
        try:
            categories[name] = {'state': 'passed', 'detail': callback()}
        except Exception as exc:
            categories[name] = {
                'state': 'failed',
                'detail': f'{type(exc).__name__}: {exc}',
            }
            failures.append(name)

    category('dependency', lambda: {
        'driver': cassandra.__version__,
        'expected': EXPECTED_DRIVER,
        'exact': cassandra.__version__ == EXPECTED_DRIVER,
    } if cassandra.__version__ == EXPECTED_DRIVER else (_ for _ in ()).throw(
        RuntimeError('driver version mismatch')
    ))

    def runtime_gate():
        discovered = provider.discover_endpoint(endpoint_request)
        identity = discovered['verified_runtime']
        if identity['engine_id'] != 'yugabytedb':
            raise RuntimeError('engine identity mismatch')
        if identity['version'] != EXPECTED_SERVER:
            raise RuntimeError('server version mismatch')
        if identity['native']['native_protocol_version'] != '4':
            raise RuntimeError('YCQL protocol mismatch')
        return identity

    category('runtime_identity', runtime_gate)

    def schema_gate():
        nonlocal table_resource
        apply(provider, request(route, 'keyspace', 'create', {
            'name': keyspace,
            'replication': {
                'class': 'NetworkTopologyStrategy', args.local_dc: 1,
            },
            'durable_writes': True,
        }))
        apply(provider, request(route, 'user-defined-type', 'create', {
            'keyspace': keyspace, 'name': type_name,
            'fields': [{'name': 'city', 'type': 'text'}],
        }))
        apply(provider, request(route, 'table', 'create', {
            'keyspace': keyspace, 'name': table_name,
            'columns': [
                {'name': 'tenant', 'type': 'text'},
                {'name': 'event_id', 'type': 'int'},
                {'name': 'value', 'type': 'text'},
            ],
            'partition_keys': ['tenant'],
            'clustering_keys': ['event_id'],
            'tablets': 1,
            'transactions_enabled': True,
            'transaction_consistency': 'strong',
        }))
        apply(provider, request(route, 'index', 'create', {
            'keyspace': keyspace, 'table': table_name,
            'name': index_name, 'target': 'value',
        }))
        resources = provider.list_resources(endpoint_request)
        expected = {
            ('keyspace', keyspace), ('table', table_name),
            ('user-defined-type', type_name), ('index', index_name),
        }
        observed = {
            (item['resource_kind'], item['display_name'])
            for item in resources
        }
        if not expected.issubset(observed):
            raise RuntimeError(
                f'resource discovery omitted {expected - observed}'
            )
        table_resource = next(
            item for item in resources
            if item['resource_kind'] == 'table' and
            item['display_name'] == table_name and
            item['extensions']['yugabytedb']['native'].get(
                'keyspace_name'
            ) == keyspace
        )
        forbidden = {
            'materialized-view', 'function', 'aggregate',
            'tracing-session', 'repair', 'compaction', 'snapshot',
        }
        if forbidden.intersection(item[0] for item in observed):
            raise RuntimeError('Cassandra-only resources were advertised')
        return {'created_and_discovered': sorted(
            f'{kind}:{name}' for kind, name in expected
        )}

    category('schema_and_discovery', schema_gate)

    def data_gate():
        if table_resource is None:
            raise RuntimeError('table resource is unavailable')
        apply(provider, request(route, 'table', 'insert', {
            'values': {'tenant': 'one', 'event_id': 1, 'value': 'before'},
        }, table_resource))
        page = provider.read_visual_admin_rows({
            '_provider_route': route,
            'target_resource': table_resource,
            'filter': {'tenant': 'one'},
            'limit': 20,
        })
        row = next(
            item for item in page['rows']
            if item['values'].get('event_id') == 1
        )
        apply(provider, request(route, 'table', 'update', {
            'selector': {'identity_token': row['identity_token']},
            'changes': {'value': 'after'},
        }, table_resource))
        page = provider.read_visual_admin_rows({
            '_provider_route': route,
            'target_resource': table_resource,
            'filter': {'tenant': 'one'},
            'limit': 20,
        })
        row = next(
            item for item in page['rows']
            if item['values'].get('event_id') == 1
        )
        if row['values'].get('value') != 'after':
            raise RuntimeError('visual update was not observed')
        apply(provider, request(route, 'table', 'delete', {
            'selector': {'identity_token': row['identity_token']},
            'confirmation': 'delete-row',
        }, table_resource))
        return {'insert': True, 'grid_read': True, 'update': True,
                'delete': True}

    category('visual_data_crud', data_gate)

    def language_gate():
        session = provider.open_session(endpoint_request)
        operation = provider.execute({
            'session_id': session['session_id'],
            'execution_id': str(uuid.uuid4()),
            'source': (
                f'SELECT * FROM "{keyspace}"."{table_name}" '
                'WHERE tenant = %s'
            ),
            'parameters': ('one',),
        })
        result = provider.describe_result(operation)
        transaction = provider.describe_transaction({
            'session_id': session['session_id'],
        })
        if transaction['provider_payload']['common_finality_inference']:
            raise RuntimeError('common finality inference was enabled')
        return {
            'result_kind': result['result_kind'],
            'opaque_outcome': True,
            'transaction': transaction,
        }

    category('language_and_native_outcome', language_gate)

    def security_gate():
        apply(provider, request(route, 'role', 'create', {
            'name': role_name, 'login': False, 'superuser': False,
        }))
        descriptor = provider.describe_security(endpoint_request)
        roles = descriptor['extensions']['yugabytedb']['native']['roles']
        if not any(item.get('role') == role_name for item in roles):
            raise RuntimeError('created YCQL role was not discovered')
        role_target = target('role', role_name, {'role': role_name})
        apply(provider, request(
            route, 'role', 'drop', {'confirmation': 'drop-role'},
            role_target,
        ))
        return {'role_round_trip': True, 'security_descriptor': True}

    category('security', security_gate)

    def rejection_gate():
        try:
            client.runtime_identity({'route': {
                **route, 'protocol_version': 5,
            }})
        except Exception:
            pass
        else:
            raise RuntimeError('protocol-v5 route was accepted')
        invalid = request(route, 'table', 'create', {
            'keyspace': keyspace, 'name': 'invalid_tablets',
            'columns': [{'name': 'id', 'type': 'int'}],
            'partition_keys': ['id'], 'tablets': 'all',
        })
        checked = provider.validate_visual_admin(invalid)
        if checked['valid']:
            raise RuntimeError('invalid tablet count was accepted')
        return {'protocol_v5_rejected': True, 'invalid_form_rejected': True}

    category('fail_closed', rejection_gate)

    try:
        for kind, name, native in (
            ('table', table_name, {
                'keyspace_name': keyspace, 'table_name': table_name,
            }),
            ('user-defined-type', type_name, {
                'keyspace_name': keyspace, 'type_name': type_name,
            }),
            ('role', role_name, {'role': role_name}),
        ):
            try:
                apply(provider, request(
                    route, kind, 'drop', {'confirmation': f'drop-{kind}'},
                    target(kind, name, native),
                ))
            except Exception:
                pass
        keyspace_target = target(
            'keyspace', keyspace, {'keyspace_name': keyspace}
        )
        apply(provider, request(
            route, 'keyspace', 'drop', {'confirmation': 'drop-keyspace'},
            keyspace_target,
        ))
        categories['cleanup'] = {'state': 'passed', 'detail': {
            'keyspace_removed': keyspace,
        }}
    except Exception as exc:
        categories['cleanup'] = {
            'state': 'failed',
            'detail': f'{type(exc).__name__}: {exc}',
        }
        failures.append('cleanup')
    finally:
        provider.close()

    report = {
        'schema': 'cdeadmin.yugabytedb-ycql-live-gate.v1',
        'engine_id': 'yugabytedb',
        'interface': 'ycql',
        'profile': EXPECTED_SERVER,
        'driver': EXPECTED_DRIVER,
        'passed': not failures,
        'categories': categories,
        'failures': failures,
    }
    return report


def main():
    args = parser().parse_args()
    report = verify(args)
    encoded = json.dumps(report, indent=2, sort_keys=True) + '\n'
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding='utf-8')
    print(encoded, end='')
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
