#!/usr/bin/env python3
##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Run fail-closed, read-only qualification against a distributed runtime."""

from __future__ import annotations

import argparse
import importlib
import json
import os
import sys
import time
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


ENGINES = {
    'apache_ignite': {
        'module': 'apache_ignite', 'port': 10800,
        'query': None,
    },
    'cockroachdb': {
        'module': 'cockroachdb', 'port': 26257,
        'query': 'SELECT 1 AS cdeadmin_probe',
    },
    'dolt': {
        'module': 'dolt', 'port': 3306,
        'query': 'SELECT 1 AS cdeadmin_probe',
    },
    'foundationdb': {
        'module': 'foundationdb', 'port': 4500,
        'query': json.dumps({
            'operation': 'get', 'key': 'cdeadmin/qualification/nonexistent',
        }),
    },
    'immudb': {
        'module': 'immudb', 'port': 5432,
        'query': 'SELECT 1 AS cdeadmin_probe',
    },
    'tidb': {
        'module': 'tidb', 'port': 4000,
        'query': 'SELECT 1 AS cdeadmin_probe',
    },
    'tikv': {
        'module': 'tikv', 'port': 2379,
        'query': json.dumps({
            'operation': 'get', 'key': 'cdeadmin/qualification/nonexistent',
        }),
    },
    'vitess': {
        'module': 'vitess', 'port': 15306,
        'query': 'SELECT 1 AS cdeadmin_probe',
    },
    'yugabytedb': {
        'module': 'yugabytedb', 'port': 5433,
        'query': 'SELECT 1 AS cdeadmin_probe',
    },
}


class _Lease:
    def __init__(self, value):
        self.value = bytearray(value.encode('utf-8'))

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        for index in range(len(self.value)):
            self.value[index] = 0

    def use(self, callback):
        return callback(memoryview(self.value))


class _Permissions:
    def __init__(self, password):
        self.password = password

    @staticmethod
    def require(_permission, _scope='endpoint'):
        return None

    @staticmethod
    def allows(_permission, _scope='endpoint'):
        return True

    def acquire_secret(self, *_args):
        if self.password is None:
            raise RuntimeError('qualification password is unavailable')
        return _Lease(self.password)


def parser():
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument('engine', choices=tuple(ENGINES))
    value.add_argument('--host', default='127.0.0.1')
    value.add_argument('--port', type=int)
    value.add_argument('--database')
    value.add_argument('--user')
    value.add_argument('--password-environment')
    value.add_argument('--sslmode')
    value.add_argument('--rest-port', type=int, default=8080)
    value.add_argument('--vtgate-http-host')
    value.add_argument('--vtgate-http-port', type=int, default=15001)
    value.add_argument('--vtgate-http-tls-mode', default='disable')
    value.add_argument('--cluster-file')
    value.add_argument('--fdbcli-path')
    value.add_argument('--pd-endpoint', action='append')
    value.add_argument('--tikv-helper-path')
    value.add_argument('--output', type=Path)
    return value


def _provider(engine, permissions):
    name = ENGINES[engine]['module']
    module = importlib.import_module(
        f'pgadmin.cdeadmin.providers.{name}.provider'
    )
    context = SimpleNamespace(
        endpoint_id=f'{engine}-live-{uuid.uuid4()}',
        session_namespace=f'{engine}-live-session-{uuid.uuid4()}',
        mode='legacy_native',
    )
    return module.create_provider(context, permissions), module.PROFILE


def _route(args):
    specification = ENGINES[args.engine]
    route = {
        'route_id': f'{args.engine}-live-gate',
        'host': args.host,
        'port': args.port or specification['port'],
    }
    if args.database:
        route['database'] = args.database
    if args.user:
        route['user'] = args.user
    if args.sslmode:
        route['sslmode'] = args.sslmode
    if args.password_environment:
        route.update({
            'credential_reference_id': f'{args.engine}-live-secret',
            'principal_reference': 'cdeadmin-live-qualifier',
        })
    if args.engine == 'apache_ignite':
        route['rest_port'] = args.rest_port
    elif args.engine == 'immudb':
        route.update({
            'web_host': args.host,
            'web_port': args.rest_port,
            'web_tls_mode': 'disable',
        })
    elif args.engine == 'vitess':
        route.update({
            'vtgate_http_port': args.vtgate_http_port,
            'vtgate_http_tls_mode': args.vtgate_http_tls_mode,
        })
        if args.vtgate_http_host:
            route['vtgate_http_host'] = args.vtgate_http_host
    elif args.engine == 'foundationdb':
        if not args.cluster_file or not args.fdbcli_path:
            raise ValueError(
                'FoundationDB requires --cluster-file and --fdbcli-path'
            )
        route.update({
            'cluster_file': str(Path(args.cluster_file).resolve()),
            'fdbcli_path': str(Path(args.fdbcli_path).resolve()),
        })
    elif args.engine == 'tikv':
        if not args.tikv_helper_path:
            raise ValueError('TiKV requires --tikv-helper-path')
        route['pd_endpoints'] = args.pd_endpoint or [
            f'{args.host}:{args.port or specification["port"]}'
        ]
    return route


def _record(evidence, category, callback, failures):
    started = time.monotonic()
    try:
        value = callback()
        if category == 'resource_discovery':
            value = {
                'resource_count': len(value),
                'resource_kinds': sorted({
                    item['resource_kind'] for item in value
                }),
            }
        evidence['categories'][category] = {
            'status': 'passed', 'details': value,
            'duration_seconds': round(time.monotonic() - started, 3),
        }
        return value
    except Exception as exc:
        evidence['categories'][category] = {
            'status': 'failed', 'error_type': type(exc).__name__,
            'duration_seconds': round(time.monotonic() - started, 3),
        }
        failures.append(f'{category}: {type(exc).__name__}: {exc}')
        return None


def verify(args):
    password = (
        os.environ.get(args.password_environment)
        if args.password_environment else None
    )
    permissions = _Permissions(password)
    if args.engine == 'tikv' and args.tikv_helper_path:
        os.environ['CDEADMIN_TIKV_HELPER_PATH'] = str(
            Path(args.tikv_helper_path).resolve())
    provider, profile = _provider(args.engine, permissions)
    route = _route(args)
    request = {'route': route}
    evidence = {
        'schema': 'cdeadmin.distributed-readonly-live-gate.v1',
        'engine_id': args.engine,
        'expected_runtime': profile.exact_version,
        'protocol_id': profile.protocol_id,
        'mutation_performed': False,
        'automatic_mutation_retry': False,
        'common_transaction_finality_interpretation': False,
        'started_at': time.time(), 'categories': {},
    }
    failures = []
    _record(
        evidence, 'runtime_identity',
        lambda: provider.discover_endpoint(request)['verified_runtime'],
        failures,
    )
    _record(
        evidence, 'resource_discovery',
        lambda: provider.list_resources(request), failures,
    )
    session = _record(
        evidence, 'session_open', lambda: provider.open_session(request),
        failures,
    )
    if session is not None:
        _record(
            evidence, 'transaction_observation',
            lambda: provider.describe_transaction({
                'session_id': session['session_id'],
            }), failures,
        )
        query = ENGINES[args.engine]['query']
        if query is not None:
            def execute_probe():
                operation = provider.execute({
                    'session_id': session['session_id'],
                    'execution_id': f'{args.engine}-read-probe',
                    'source': query, 'parameters': {},
                })
                result = provider.describe_result({
                    'operation_id': operation['operation_id'],
                })
                return {
                    'result_kind': result['result_kind'],
                    'complete': result['complete'],
                }

            _record(evidence, 'read_execution', execute_probe, failures)
    provider.close()
    evidence['completed_at'] = time.time()
    evidence['passed'] = not failures
    evidence['failures'] = failures
    return evidence


def main():
    args = parser().parse_args()
    try:
        evidence = verify(args)
    except Exception as exc:
        evidence = {
            'schema': 'cdeadmin.distributed-readonly-live-gate.v1',
            'engine_id': args.engine, 'passed': False,
            'failures': [f'setup: {type(exc).__name__}: {exc}'],
        }
    document = json.dumps(evidence, indent=2, sort_keys=True)
    print(document)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(document + '\n', encoding='utf-8')
    return 0 if evidence['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
