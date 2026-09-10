#!/usr/bin/env python3
##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Qualify provider-driven Ignite cache access across a node failure."""

from __future__ import annotations

import argparse
import importlib
import json
import os
import subprocess
import sys
import time
import urllib.parse
import urllib.request
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


class _Lease:
    def __init__(self, value):
        self.value = bytearray(value.encode('utf-8'))

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.value[:] = b'\x00' * len(self.value)

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
        if reference != 'root':
            raise RuntimeError('Ignite qualification secret is unavailable')
        return _Lease(self.password)


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=10800)
    parser.add_argument('--rest-port', type=int, default=18080)
    parser.add_argument('--secondary-container', required=True)
    parser.add_argument(
        '--password-environment', default='CDEADMIN_IGNITE_PASSWORD')
    parser.add_argument('--allow-failure-injection', action='store_true')
    parser.add_argument('--output', type=Path, required=True)
    return parser.parse_args()


def topology(host, port, username, password):
    query = urllib.parse.urlencode({
        'cmd': 'top', 'user': username, 'password': password,
    })
    with urllib.request.urlopen(
        f'http://{host}:{port}/ignite?{query}', timeout=5
    ) as response:
        payload = json.loads(response.read(1024 * 1024))
    if payload.get('successStatus') != 0:
        raise RuntimeError('Ignite topology request failed')
    return payload.get('response') or []


def wait_topology(host, port, expected, username, password, timeout=90):
    started = time.monotonic()
    observations = 0
    while time.monotonic() - started < timeout:
        observations += 1
        try:
            nodes = topology(host, port, username, password)
            if len(nodes) == expected:
                return {
                    'node_count': len(nodes), 'observations': observations,
                    'duration_seconds': round(
                        time.monotonic() - started, 3
                    ),
                    'node_ids': sorted(
                        str(row.get('nodeId')) for row in nodes
                    ),
                }
        except Exception:
            pass
        time.sleep(1)
    raise RuntimeError(
        f'Ignite topology did not reach {expected} nodes'
    )


def apply(provider, route, target, operation, draft):
    request = {
        'resource_kind': 'cache', 'operation_id': operation,
        'draft': draft, '_provider_route': route,
    }
    if target is not None:
        request['target_resource'] = target
    validation = provider.validate_visual_admin(request)
    if not validation['valid']:
        raise RuntimeError(
            f'cache.{operation} validation failed: {validation["errors"]}'
        )
    plan = provider.plan_visual_admin(request)
    result = provider.apply_visual_admin({
        'plan_id': plan['plan_id'],
        'plan_digest': plan['plan_digest'],
        'confirmed': True,
    })
    if result['transaction_finality_interpreted_by_common_code']:
        raise RuntimeError('common visual code interpreted Ignite finality')
    native = result['provider_result']
    return {
        'operation': f'cache.{operation}',
        'accepted': native.get('accepted'),
        'provider_constructed': bool(
            plan.get('command_preview', {}).get('provider_constructed')
        ),
        'provider_native_outcome': native.get('provider_native_outcome'),
    }


def docker(action, container):
    subprocess.run(
        ['docker', action, container], check=True, capture_output=True,
        text=True, timeout=60,
    )


def verify(args):
    if not args.allow_failure_injection:
        raise RuntimeError(
            'live failure injection requires --allow-failure-injection'
        )
    password = os.environ.get(args.password_environment)
    if password is None:
        raise RuntimeError('Ignite password environment is absent')
    module = importlib.import_module(
        'pgadmin.cdeadmin.providers.apache_ignite.provider'
    )
    context = SimpleNamespace(
        endpoint_id=f'ignite-failover-{uuid.uuid4()}',
        session_namespace=f'ignite-failover-session-{uuid.uuid4()}',
        mode='legacy_native', runtime_verification_state='verified',
        experience_family='apache_ignite',
        provider_id='org.cdeadmin.apache-ignite',
        profile_id='apache-ignite-native',
        declared_runtime_family='apache_ignite',
        verified_runtime_family='apache_ignite',
    )
    provider = module.create_provider(context, Permissions(password))
    route = {
        'route_id': 'ignite-failover-gate', 'host': args.host,
        'port': args.port, 'rest_port': args.rest_port,
        'partition_aware': True,
        'auth_mode': 'username-password', 'username': 'ignite',
        'principal_reference': 'cdeadmin-ignite-failover-live-gate',
        'credential_references': {'database_password': 'root'},
    }
    cache_name = f'cdeadmin_failover_{uuid.uuid4().hex[:12]}'
    target = {
        'resource_id': f'cache:{cache_name}', 'resource_kind': 'cache',
        'display_name': cache_name, 'display_path': [cache_name],
        'authority_path': ['cache', cache_name],
        'generation': 'live-failover-gate',
    }
    evidence = {
        'schema': 'cdeadmin.apache-ignite-failover-live-verification.v1',
        'engine_id': 'apache_ignite', 'expected_runtime': '2.17.0',
        'secondary_container': args.secondary_container,
        'failure_injection_authorized': True,
        'automatic_mutation_retry': False,
        'common_transaction_finality_interpretation': False,
        'steps': {}, 'failures': [], 'status': 'failed',
    }
    cache_created = False
    secondary_stopped = False
    try:
        evidence['steps']['initial_topology'] = wait_topology(
            args.host, args.rest_port, 2, 'ignite', password
        )
        identity = provider.discover_endpoint({'route': route})[
            'verified_runtime'
        ]
        if identity['version'] != '2.17.0':
            raise RuntimeError('Ignite runtime version is not exact')
        evidence['verified_runtime'] = identity
        evidence['steps']['create'] = apply(
            provider, route, None, 'create', {
                'name': cache_name, 'backups_number': 1,
            },
        )
        cache_created = True
        for index in range(32):
            apply(provider, route, target, 'insert', {
                'key': f'key-{index:02d}', 'value': f'value-{index:02d}',
            })
        evidence['steps']['seed'] = {
            'entry_count': 32, 'mutation_retried': False,
        }
        docker('stop', args.secondary_container)
        secondary_stopped = True
        evidence['steps']['degraded_topology'] = wait_topology(
            args.host, args.rest_port, 1, 'ignite', password
        )
        page = provider.read_visual_admin_rows({
            '_provider_route': route, 'target_resource': target, 'limit': 100,
        })
        if len(page['rows']) != 32:
            raise RuntimeError('Ignite backup did not retain all cache rows')
        row = next(
            item for item in page['rows']
            if item['values']['key'] == 'key-00'
        )
        evidence['steps']['degraded_update'] = apply(
            provider, route, target, 'update', {
                'selector': {'identity_token': row['identity_token']},
                'value': 'updated-after-node-loss',
            },
        )
        evidence['steps']['degraded_update']['mutation_retried'] = False
        docker('start', args.secondary_container)
        secondary_stopped = False
        evidence['steps']['recovered_topology'] = wait_topology(
            args.host, args.rest_port, 2, 'ignite', password
        )
        page = provider.read_visual_admin_rows({
            '_provider_route': route, 'target_resource': target, 'limit': 100,
        })
        row = next(
            item for item in page['rows']
            if item['values']['key'] == 'key-00'
        )
        if row['values']['value'] != 'updated-after-node-loss':
            raise RuntimeError('Ignite post-recovery value was not observed')
        evidence['steps']['post_recovery_read'] = {
            'entry_count': len(page['rows']), 'updated_value_observed': True,
        }
        evidence['status'] = 'passed'
    except Exception as exc:
        evidence['failures'].append(f'{type(exc).__name__}: {exc}')
    finally:
        if secondary_stopped:
            try:
                docker('start', args.secondary_container)
                wait_topology(
                    args.host, args.rest_port, 2, 'ignite', password)
            except Exception as exc:
                evidence['failures'].append(
                    f'recovery {type(exc).__name__}: {exc}'
                )
        if cache_created:
            try:
                evidence['steps']['cleanup'] = apply(
                    provider, route, target, 'drop', {
                        'confirmation': cache_name,
                    },
                )
            except Exception as exc:
                evidence['failures'].append(
                    f'cleanup {type(exc).__name__}: {exc}'
                )
        provider.close()
    if evidence['failures']:
        evidence['status'] = 'failed'
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(evidence, indent=2, sort_keys=True) + '\n',
        encoding='utf-8',
    )
    return evidence


def main():
    args = arguments()
    try:
        evidence = verify(args)
    except Exception as exc:
        print(f'{type(exc).__name__}: {exc}', file=sys.stderr)
        return 1
    print(json.dumps({
        'engine_id': evidence['engine_id'], 'status': evidence['status'],
        'output': str(args.output.resolve()),
    }, sort_keys=True))
    return 0 if evidence['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
