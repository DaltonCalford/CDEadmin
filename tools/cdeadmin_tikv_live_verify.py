#!/usr/bin/env python3
##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Verify bounded TiKV native reads, mutations, and transaction dispatch."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import time
import uuid
from types import ModuleType, SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / 'web'
if str(WEB) not in sys.path:
    sys.path.insert(0, str(WEB))
if 'pgadmin' not in sys.modules:
    package = ModuleType('pgadmin')
    package.__path__ = [str(WEB / 'pgadmin')]
    sys.modules['pgadmin'] = package

from pgadmin.cdeadmin.providers.tikv.provider import (  # noqa: E402
    create_provider,
)


class Permissions:
    @staticmethod
    def require(_permission, _scope='endpoint'):
        return None

    @staticmethod
    def allows(_permission, _scope='endpoint'):
        return True


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pd-endpoint', action='append', required=True)
    parser.add_argument('--helper-path', type=Path, required=True)
    parser.add_argument('--api-version', type=int, choices=(1, 2), default=1)
    parser.add_argument('--allow-mutation', action='store_true')
    parser.add_argument('--output', type=Path)
    return parser.parse_args()


def execute(provider, session_id, operation, sequence):
    started = time.monotonic()
    token = provider.execute({
        'session_id': session_id,
        'execution_id': f'tikv-live-{sequence}',
        'source': json.dumps(operation),
        'parameters': {},
    })
    result = provider.describe_result({
        'operation_id': token['operation_id'],
    })
    payload = result['extensions']['tikv']['payload']
    native = payload['native']
    if native.get('provider_finality_only') is not True:
        raise RuntimeError('TiKV provider finality boundary was not retained')
    if native.get('automatic_mutation_retry_by_cdeadmin') is not False:
        raise RuntimeError('CDEadmin mutation retry boundary was not retained')
    return {
        'duration_seconds': round(time.monotonic() - started, 3),
        'entries': payload['entries'],
        'native': native,
    }


def apply_visual(provider, route, target, operation, draft):
    request = {
        'resource_kind': 'key-range', 'operation_id': operation,
        'draft': draft, '_provider_route': route,
        'target_resource': target,
    }
    validation = provider.validate_visual_admin(request)
    if not validation['valid']:
        raise RuntimeError(
            f'key-range.{operation} validation failed: '
            f'{validation["errors"]}'
        )
    plan = provider.plan_visual_admin(request)
    result = provider.apply_visual_admin({
        'plan_id': plan['plan_id'],
        'plan_digest': plan['plan_digest'],
        'confirmed': True,
    })
    native = result['provider_result']
    native_observation = native.get('native') or {}
    if result['transaction_finality_interpreted_by_common_code']:
        raise RuntimeError('common visual code interpreted TiKV finality')
    return {
        'operation': f'key-range.{operation}',
        'provider_constructed': bool(
            plan.get('command_preview', {}).get('provider_constructed')
        ),
        'accepted': native.get('accepted'),
        'provider_finality_only': native.get('provider_finality_only'),
        'automatic_mutation_retry_by_cdeadmin': native.get(
            'automatic_mutation_retry_by_cdeadmin'
        ),
        'conditional_delete_atomic': native_observation.get(
            'conditional_delete_atomic'
        ),
        'identity_revalidated_before_delete': native_observation.get(
            'identity_revalidated_before_delete'
        ),
    }


def verify(args):
    if not args.allow_mutation:
        raise ValueError('live verification requires --allow-mutation')
    os.environ['CDEADMIN_TIKV_HELPER_PATH'] = str(
        args.helper_path.expanduser().resolve())
    context = SimpleNamespace(
        endpoint_id=f'tikv-mutation-{uuid.uuid4()}',
        session_namespace=f'tikv-mutation-session-{uuid.uuid4()}',
        mode='legacy_native',
        runtime_verification_state='verified',
        declared_runtime_family='tikv',
        verified_runtime_family='tikv',
    )
    provider = create_provider(context, Permissions())
    route = {
        'route_id': 'tikv-mutation-live-gate',
        'pd_endpoints': args.pd_endpoint,
        'api_version': args.api_version,
    }
    prefix = f'cdeadmin/qualification/{uuid.uuid4()}'
    raw_first = f'{prefix}/raw/first'
    transaction_first = f'{prefix}/transaction/first'
    transaction_second = f'{prefix}/transaction/second'
    visual_key = f'{prefix}/visual'
    evidence = {
        'schema': 'cdeadmin.tikv-native-live-verification.v1',
        'engine_id': 'tikv',
        'expected_runtime': '8.5.6',
        'key_prefix': prefix,
        'mutation_authorized': True,
        'automatic_mutation_retry': False,
        'common_transaction_finality_interpretation': False,
        'started_at': time.time(),
        'steps': {},
        'passed': False,
    }
    session_id = None
    visual_key_created = False
    failures = []

    def step(name, operation):
        try:
            value = execute(
                provider, session_id, operation, len(evidence['steps']))
            evidence['steps'][name] = {'status': 'passed', **value}
            return value
        except Exception as exc:
            evidence['steps'][name] = {
                'status': 'failed', 'error_type': type(exc).__name__,
            }
            failures.append(f'{name}: {type(exc).__name__}: {exc}')
            return None

    try:
        identity = provider.discover_endpoint({'route': route})[
            'verified_runtime']
        evidence['runtime_identity'] = identity
        opened = provider.open_session({'route': route})
        session_id = opened['session_id']
        step('raw_put', {
            'operation': 'put', 'key': raw_first, 'value': 'one',
        })
        read = step('raw_get', {'operation': 'get', 'key': raw_first})
        if read and read['entries'][0].get('value') != 'one':
            failures.append('raw_get: value mismatch')
        cas = step('compare_and_swap', {
            'operation': 'compare_and_swap', 'key': raw_first,
            'previous_value': 'one', 'value': 'two',
        })
        if cas and cas['entries'][0].get('swapped') is not True:
            failures.append('compare_and_swap: provider rejected mutation')
        step('bounded_scan', {
            'operation': 'scan', 'start_key': prefix,
            'end_key': f'{prefix}0', 'limit': 10,
        })
        step('native_transaction_seed', {
            'operation': 'transaction', 'mutations': [
                {
                    'operation': 'set', 'key': transaction_first,
                    'value': 'transaction-one',
                },
            ],
        })
        transaction = step('native_transaction_commit', {
            'operation': 'transaction', 'keys': [transaction_first],
            'mutations': [
                {
                    'operation': 'set', 'key': transaction_second,
                    'value': 'transaction-two',
                },
                {'operation': 'delete', 'key': transaction_first},
            ],
        })
        if transaction:
            native = transaction['native']
            if native.get('commit_requested') is not True or native.get(
                    'commit_returned') is not True:
                failures.append(
                    'native_transaction_commit: commit observation missing')
        observed = step('verify_transaction_commit', {
            'operation': 'transaction',
            'keys': [transaction_first, transaction_second],
        })
        if observed and observed['entries'][0].get('found') is not False:
            failures.append('verify_transaction_delete: key still present')
        if observed and observed['entries'][1].get(
                'value') != 'transaction-two':
            failures.append('verify_transaction_insert: value mismatch')

        key_range = next(
            item for item in provider.list_resources({'route': route})
            if item['resource_kind'] == 'key-range'
        )
        evidence['steps']['visual_insert'] = {
            'status': 'passed',
            **apply_visual(
                provider, route, key_range, 'insert',
                {'key': visual_key, 'value': 'visual-one'},
            ),
        }
        visual_key_created = True
        page = provider.read_visual_admin_rows({
            '_provider_route': route, 'target_resource': key_range,
            'start_key': visual_key, 'end_key': visual_key + '\x00',
            'limit': 20,
        })
        row = next(
            item for item in page['rows']
            if item['values']['key'] == visual_key
        )
        evidence['steps']['visual_update'] = {
            'status': 'passed',
            **apply_visual(provider, route, key_range, 'update', {
                'selector': {'identity_token': row['identity_token']},
                'value': 'visual-two',
            }),
        }
        page = provider.read_visual_admin_rows({
            '_provider_route': route, 'target_resource': key_range,
            'start_key': visual_key, 'end_key': visual_key + '\x00',
            'limit': 20,
        })
        row = next(
            item for item in page['rows']
            if item['values']['key'] == visual_key
        )
        if row['values']['value'] != 'visual-two':
            failures.append('visual_update: value mismatch')
        evidence['steps']['visual_delete'] = {
            'status': 'passed',
            **apply_visual(provider, route, key_range, 'delete', {
                'selector': {'identity_token': row['identity_token']},
                'confirmation': visual_key,
            }),
        }
        visual_key_created = False
    except Exception as exc:
        failures.append(f'setup: {type(exc).__name__}: {exc}')
    finally:
        if session_id is not None:
            if visual_key_created:
                step('cleanup_visual', {
                    'operation': 'delete', 'key': visual_key,
                })
            step('cleanup_raw', {
                'operation': 'delete', 'key': raw_first,
            })
            step('cleanup_transaction', {
                'operation': 'transaction', 'mutations': [
                    {'operation': 'delete', 'key': transaction_first},
                    {'operation': 'delete', 'key': transaction_second},
                ],
            })
        provider.close()
    evidence['completed_at'] = time.time()
    evidence['failures'] = failures
    evidence['passed'] = not failures
    return evidence


def main():
    args = arguments()
    try:
        evidence = verify(args)
    except Exception as exc:
        evidence = {
            'schema': 'cdeadmin.tikv-native-live-verification.v1',
            'engine_id': 'tikv', 'passed': False,
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
