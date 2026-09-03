#!/usr/bin/env python3
##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Verify FoundationDB native data and visual-administration mutations."""

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

from pgadmin.cdeadmin.providers.foundationdb.provider import (  # noqa: E402
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


def parser():
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument('--cluster-file', type=Path, required=True)
    value.add_argument('--fdbcli-path', type=Path, required=True)
    value.add_argument('--allow-mutation', action='store_true')
    value.add_argument('--output', type=Path, required=True)
    return value


def _apply(provider, route, kind, operation, draft, target=None):
    request = {
        'resource_kind': kind,
        'operation_id': operation,
        'draft': draft,
        '_provider_route': route,
    }
    if target is not None:
        request['target_resource'] = target
    validation = provider.validate_visual_admin(request)
    if not validation['valid']:
        raise RuntimeError(
            f'{kind}.{operation} validation failed: '
            f'{validation["errors"]}'
        )
    plan = provider.plan_visual_admin(request)
    result = provider.apply_visual_admin({
        'plan_id': plan['plan_id'],
        'plan_digest': plan['plan_digest'],
        'confirmed': True,
    })
    if result['transaction_finality_interpreted_by_common_code']:
        raise RuntimeError('common visual code interpreted finality')
    return {
        'operation': f'{kind}.{operation}',
        'provider_constructed': bool(
            plan.get('command_preview', {}).get('provider_constructed')
        ),
        'accepted': result['provider_result'].get('accepted'),
        'native_operation': result['provider_result'].get(
            'native_operation'
        ),
        'common_finality_interpretation': result['provider_result'].get(
            'transaction_finality_interpreted_by_common_code'
        ),
        'commit_requested': result['provider_result'].get(
            'commit_requested'
        ),
        'commit_returned': result['provider_result'].get('commit_returned'),
    }


def _execute(provider, session_id, command):
    operation = provider.execute({
        'session_id': session_id,
        'execution_id': str(uuid.uuid4()),
        'source': json.dumps(command, separators=(',', ':')),
        'parameters': {},
    })
    result = provider.describe_result({
        'operation_id': operation['operation_id'],
    })
    return result['extensions']['foundationdb']['payload']


def verify(args):
    if not args.allow_mutation:
        raise RuntimeError(
            'live mutation verification requires --allow-mutation'
        )
    context = SimpleNamespace(
        endpoint_id=f'foundationdb-mutation-{uuid.uuid4()}',
        session_namespace=f'foundationdb-session-{uuid.uuid4()}',
        mode='legacy_native',
        runtime_verification_state='verified',
        declared_runtime_family='foundationdb',
        verified_runtime_family='foundationdb',
    )
    provider = create_provider(context, _Permissions())
    route = {
        'route_id': 'foundationdb-mutation-gate',
        'cluster_file': str(args.cluster_file.resolve()),
        'fdbcli_path': str(args.fdbcli_path.resolve()),
    }
    suffix = uuid.uuid4().hex
    directory_name = f'cdeadmin-gate-{suffix[:12]}'
    key = f'cdeadmin/qualification/{suffix}'
    visual_key = f'cdeadmin/qualification/{suffix}/visual'
    value = f'provider-value-{suffix[:12]}'
    directory = {
        'resource_id': f'directory:{directory_name}',
        'resource_kind': 'directory',
        'display_name': directory_name,
        'display_path': [directory_name],
        'authority_path': ['directory', directory_name],
        'generation': 'live-mutation-gate',
    }
    operations = []
    cleanup = []
    failures = []
    directory_created = False
    key_created = False
    visual_key_created = False
    started = time.time()
    identity = provider.discover_endpoint({'route': route})[
        'verified_runtime'
    ]
    try:
        operations.append(_apply(
            provider, route, 'directory', 'create',
            {'name': directory_name, 'definition': '', 'options': {}},
        ))
        directory_created = True
        names = {
            item['display_name'] for item in provider.list_resources({
                'route': route,
            }) if item['resource_kind'] == 'directory'
        }
        if directory_name not in names:
            raise RuntimeError('created FoundationDB directory was not listed')

        session = provider.open_session({'route': route})
        session_id = session['session_id']
        transaction = provider.describe_transaction({
            'session_id': session_id,
        })
        if transaction['extensions']['foundationdb'][
                'common_finality_inference']:
            raise RuntimeError('common code inferred FoundationDB finality')
        set_result = _execute(provider, session_id, {
            'operation': 'set', 'key': key, 'value': value,
        })
        key_created = True
        get_result = _execute(provider, session_id, {
            'operation': 'get', 'key': key,
        })
        expected_value = value.encode('utf-8').hex()
        if get_result['entries'][0]['value'] != expected_value:
            raise RuntimeError('FoundationDB committed value was not observed')
        operations.extend((
            {
                'operation': 'key.set',
                'accepted': set_result['entries'][0]['accepted'],
                'provider_transaction_authority': True,
            },
            {
                'operation': 'key.get',
                'value_observed': True,
                'provider_transaction_authority': True,
            },
        ))

        key_range = next(
            item for item in provider.list_resources({'route': route})
            if item['resource_kind'] == 'key-range'
        )
        operations.append(_apply(
            provider, route, 'key-range', 'insert',
            {'key': visual_key, 'value': 'visual-one'}, key_range,
        ))
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
        operations.append(_apply(
            provider, route, 'key-range', 'update', {
                'selector': {'identity_token': row['identity_token']},
                'value': 'visual-two',
            }, key_range,
        ))
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
            raise RuntimeError('FoundationDB visual update was not observed')
        operations.append(_apply(
            provider, route, 'key-range', 'delete', {
                'selector': {'identity_token': row['identity_token']},
                'confirmation': visual_key,
            }, key_range,
        ))
        visual_key_created = False
    except Exception as exc:
        failures.append(f'{type(exc).__name__}: {exc}')
    finally:
        if visual_key_created:
            try:
                session = provider.open_session({'route': route})
                clear_result = _execute(
                    provider, session['session_id'],
                    {'operation': 'clear', 'key': visual_key},
                )
                cleanup.append({
                    'operation': 'visual-key.clear',
                    'accepted': clear_result['entries'][0]['accepted'],
                    'provider_transaction_authority': True,
                })
            except Exception as exc:
                failures.append(
                    f'visual key cleanup {type(exc).__name__}: {exc}'
                )
        if key_created:
            try:
                session = provider.open_session({'route': route})
                clear_result = _execute(
                    provider, session['session_id'],
                    {'operation': 'clear', 'key': key},
                )
                cleanup.append({
                    'operation': 'key.clear',
                    'accepted': clear_result['entries'][0]['accepted'],
                    'provider_transaction_authority': True,
                })
            except Exception as exc:
                failures.append(f'key cleanup {type(exc).__name__}: {exc}')
        if directory_created:
            try:
                cleanup.append(_apply(
                    provider, route, 'directory', 'drop', {
                        'cascade': False, 'confirmation': directory_name,
                    }, directory,
                ))
            except Exception as exc:
                failures.append(
                    f'directory cleanup {type(exc).__name__}: {exc}'
                )
        provider.client.close()

    evidence = {
        'schema': 'cdeadmin.foundationdb-native-live-verification.v1',
        'engine_id': 'foundationdb',
        'expected_runtime': PROFILE.exact_version,
        'verified_runtime': identity,
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
