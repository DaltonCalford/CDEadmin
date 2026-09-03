#!/usr/bin/env python3
##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Verify Ignite native cache and provider-driven visual mutations."""

from __future__ import annotations

import argparse
import json
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

from pgadmin.cdeadmin.providers.apache_ignite.provider import (  # noqa: E402
    PROFILE,
    create_provider,
)


class _Permissions:
    @staticmethod
    def require(_permission, _scope='endpoint'):
        return None

    @staticmethod
    def allows(_permission, _scope='endpoint'):
        return True

    @staticmethod
    def acquire_secret(*_args):
        raise RuntimeError('Ignite qualification has no credential')


def parser():
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument('--host', default='127.0.0.1')
    value.add_argument('--port', type=int, default=10800)
    value.add_argument('--rest-port', type=int, default=8080)
    value.add_argument('--allow-mutation', action='store_true')
    value.add_argument('--output', type=Path, required=True)
    return value


def _apply(provider, route, operation, draft, target=None):
    request = {
        'resource_kind': 'cache',
        'operation_id': operation,
        'draft': draft,
        '_provider_route': route,
    }
    if target is not None:
        request['target_resource'] = target
    validation = provider.validate_visual_admin(request)
    if not validation['valid']:
        raise RuntimeError(
            f'cache.{operation} validation failed: '
            f'{validation["errors"]}'
        )
    plan = provider.plan_visual_admin(request)
    result = provider.apply_visual_admin({
        'plan_id': plan['plan_id'],
        'plan_digest': plan['plan_digest'],
        'confirmed': True,
    })
    native = result['provider_result']
    if result['transaction_finality_interpreted_by_common_code'] or (
            native['transaction_finality_interpreted_by_common_code']):
        raise RuntimeError('common code interpreted Ignite finality')
    return {
        'operation': f'cache.{operation}',
        'provider_constructed': bool(
            plan.get('command_preview', {}).get('provider_constructed')
        ),
        'accepted': native.get('accepted'),
        'provider_native_outcome': native.get('provider_native_outcome'),
    }


def verify(args):
    if not args.allow_mutation:
        raise RuntimeError(
            'live mutation verification requires --allow-mutation'
        )
    context = SimpleNamespace(
        endpoint_id=f'apache-ignite-mutation-{uuid.uuid4()}',
        session_namespace=f'apache-ignite-session-{uuid.uuid4()}',
        mode='legacy_native',
        runtime_verification_state='verified',
        declared_runtime_family='apache_ignite',
        verified_runtime_family='apache_ignite',
    )
    provider = create_provider(context, _Permissions())
    route = {
        'route_id': 'apache-ignite-mutation-gate',
        'host': args.host,
        'port': args.port,
        'rest_port': args.rest_port,
        'partition_aware': True,
    }
    suffix = uuid.uuid4().hex[:12]
    cache_name = f'cdeadmin_gate_{suffix}'
    target = {
        'resource_id': f'cache:{cache_name}',
        'resource_kind': 'cache',
        'display_name': cache_name,
        'display_path': [cache_name],
        'authority_path': ['cache', cache_name],
        'generation': 'live-mutation-gate',
    }
    operations = []
    cleanup = []
    failures = []
    cache_created = False
    started = time.time()
    identity = provider.discover_endpoint({'route': route})[
        'verified_runtime'
    ]
    try:
        operations.append(_apply(
            provider, route, 'create', {
                'name': cache_name, 'backups_number': 1,
            },
        ))
        cache_created = True
        operations.append(_apply(
            provider, route, 'insert', {
                'key': 'one', 'value': 'provider-created',
            }, target,
        ))
        page = provider.read_visual_admin_rows({
            '_provider_route': route,
            'target_resource': target,
            'limit': 20,
        })
        row = next(item for item in page['rows'] if item['values']['key'] == (
            'one'
        ))
        if not row.get('identity_token'):
            raise RuntimeError('Ignite did not issue a cache row identity')
        operations.append(_apply(
            provider, route, 'update', {
                'selector': {'identity_token': row['identity_token']},
                'value': 'provider-updated',
            }, target,
        ))
        page = provider.read_visual_admin_rows({
            '_provider_route': route,
            'target_resource': target,
            'limit': 20,
        })
        updated = next(
            item for item in page['rows'] if item['values']['key'] == 'one'
        )
        if updated['values']['value'] != 'provider-updated':
            raise RuntimeError('Ignite cache update was not observed')
        operations.append(_apply(
            provider, route, 'delete', {
                'selector': {
                    'identity_token': updated['identity_token'],
                },
                'confirmation': cache_name,
            }, target,
        ))
        page = provider.read_visual_admin_rows({
            '_provider_route': route,
            'target_resource': target,
            'limit': 20,
        })
        if page['rows']:
            raise RuntimeError('Ignite cache delete was not observed')
    except Exception as exc:
        failures.append(f'{type(exc).__name__}: {exc}')
    finally:
        if cache_created:
            try:
                cleanup.append(_apply(
                    provider, route, 'drop', {
                        'cascade': False, 'confirmation': cache_name,
                    }, target,
                ))
            except Exception as exc:
                failures.append(
                    f'cache cleanup {type(exc).__name__}: {exc}'
                )
        provider.client.close()

    evidence = {
        'schema': 'cdeadmin.ignite-native-live-verification.v1',
        'engine_id': 'apache_ignite',
        'expected_runtime': PROFILE.exact_version,
        'verified_runtime': identity,
        'visual_administration_only': True,
        'raw_command_supplied_by_gate': False,
        'automatic_mutation_retry': False,
        'common_transaction_finality_interpretation': False,
        'operations': operations,
        'cleanup': cleanup,
        'failures': failures,
        'status': 'passed' if not failures else 'failed',
        'duration_seconds': round(time.time() - started, 3),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(evidence, indent=2, sort_keys=True) + '\n',
        encoding='utf-8',
    )
    return evidence


def main():
    args = parser().parse_args()
    try:
        evidence = verify(args)
    except Exception as exc:
        print(f'{type(exc).__name__}: {exc}', file=sys.stderr)
        return 1
    print(json.dumps({
        'engine_id': evidence['engine_id'],
        'status': evidence['status'],
        'output': str(args.output.resolve()),
    }, sort_keys=True))
    return 0 if evidence['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
