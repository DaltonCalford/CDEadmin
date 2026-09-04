#!/usr/bin/env python3
##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Qualify reversible TiKV PD control-plane administration."""

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
    PROFILE,
    create_provider,
)


class Permissions:
    @staticmethod
    def require(_permission, _scope='endpoint'):
        return None

    @staticmethod
    def allows(_permission, _scope='endpoint'):
        return True


def parser():
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument('--pd-endpoint', action='append', required=True)
    value.add_argument('--helper-path', type=Path, required=True)
    value.add_argument('--api-version', type=int, choices=(1, 2), default=1)
    value.add_argument('--allow-cluster-mutation', action='store_true')
    value.add_argument('--output', type=Path)
    value.add_argument('--object-evidence', type=Path)
    return value


def _target(resources, kind, name=None):
    matches = [
        item for item in resources if item['resource_kind'] == kind and (
            name is None or str(item.get('display_name')) == str(name)
        )
    ]
    if not matches:
        raise RuntimeError(f'TiKV {kind} qualification target is unavailable')
    return matches[0]


def _apply(provider, route, kind, operation, draft, target,
           post_state=True):
    request = {
        'resource_kind': kind,
        'operation_id': operation,
        'draft': draft,
        '_provider_route': route,
        'target_resource': target,
    }
    validation = provider.validate_visual_admin(request)
    if not validation['valid']:
        raise RuntimeError(
            f'{kind}.{operation} validation failed: '
            f'{validation["errors"]}'
        )
    plan = provider.plan_visual_admin(request)
    if plan['state'] != 'ready' or plan.get('execution_available') is not True:
        raise RuntimeError(f'{kind}.{operation} did not produce a ready plan')
    result = provider.apply_visual_admin({
        'plan_id': plan['plan_id'],
        'plan_digest': plan['plan_digest'],
        'confirmed': True,
    })
    provider_result = result['provider_result']
    if result['transaction_finality_interpreted_by_common_code']:
        raise RuntimeError('common code interpreted TiKV operation finality')
    if provider_result.get('provider_finality_only') is not True:
        raise RuntimeError('TiKV provider finality boundary was not retained')
    if provider_result.get(
            'automatic_mutation_retry_by_cdeadmin') is not False:
        raise RuntimeError('TiKV mutation retry boundary was not retained')
    evidence = {
        'operation': f'{kind}.{operation}',
        'operation_id': result['control_operation']['operation_id'],
        'provider_constructed': bool(
            plan.get('command_preview', {}).get('provider_constructed')
        ),
        'accepted': provider_result.get('accepted'),
        'provider_finality_only': True,
        'automatic_mutation_retry_by_cdeadmin': False,
    }
    if post_state:
        observed = provider.validate_visual_admin_post_state({
            'operation_id': evidence['operation_id'],
        })
        post_state_value = observed.get('post_state') or {}
        evidence['post_state_confirmed'] = post_state_value.get('confirmed')
        if post_state_value.get('confirmed') is not True:
            raise RuntimeError(
                f'{kind}.{operation} post-state was not confirmed: '
                f'{post_state_value.get("reason")}'
            )
    return evidence


def verify(args):
    if not args.allow_cluster_mutation:
        raise ValueError(
            'live verification requires --allow-cluster-mutation'
        )
    helper = args.helper_path.expanduser().resolve()
    os.environ['CDEADMIN_TIKV_HELPER_PATH'] = str(helper)
    context = SimpleNamespace(
        endpoint_id=f'tikv-control-{uuid.uuid4()}',
        session_namespace=f'tikv-control-session-{uuid.uuid4()}',
        mode='legacy_native', runtime_verification_state='verified',
        declared_runtime_family='tikv', verified_runtime_family='tikv',
    )
    provider = create_provider(context, Permissions())
    route = {
        'route_id': f'tikv-control-{uuid.uuid4()}',
        'pd_endpoints': args.pd_endpoint,
        'api_version': args.api_version,
    }
    suffix = uuid.uuid4().hex[:12]
    label_key = f'cdeadmin-qualification-{suffix}'
    operations = []
    cleanup = []
    failures = []
    scheduler_paused = False
    label_created = False
    identity = None
    resource_kinds = []
    started = time.time()

    try:
        identity = provider.discover_endpoint({'route': route})[
            'verified_runtime'
        ]
        if identity['version'] != PROFILE.exact_version:
            raise RuntimeError('TiKV exact runtime identity changed')
        resources = provider.list_resources({'route': route})
        resource_kinds = sorted({
            item['resource_kind'] for item in resources
        })
        store = _target(resources, 'store')
        scheduler = _target(
            resources, 'scheduler', 'balance-leader-scheduler'
        )
        original = store.get('native') or {}
        status = original.get('status') or {}
        leader_weight = status.get('leader_weight', 1)
        region_weight = status.get('region_weight', 1)

        operations.append(_apply(
            provider, route, 'store', 'set_labels',
            {'labels': {label_key: 'temporary'}, 'force': False}, store,
        ))
        label_created = True
        operations.append(_apply(
            provider, route, 'store', 'set_weights', {
                'leader_weight': leader_weight,
                'region_weight': region_weight,
            }, store, post_state=False,
        ))
        operations.append(_apply(
            provider, route, 'scheduler', 'pause',
            {'delay_seconds': 30}, scheduler,
        ))
        scheduler_paused = True
        operations.append(_apply(
            provider, route, 'scheduler', 'resume', {}, scheduler,
        ))
        scheduler_paused = False
        operations.append(_apply(
            provider, route, 'store', 'delete_label',
            {'label_key': label_key}, store,
        ))
        label_created = False
    except Exception as exc:
        failures.append(f'qualification: {type(exc).__name__}: {exc}')
    finally:
        if scheduler_paused:
            try:
                resources = provider.list_resources({'route': route})
                scheduler = _target(
                    resources, 'scheduler', 'balance-leader-scheduler'
                )
                cleanup.append(_apply(
                    provider, route, 'scheduler', 'resume', {}, scheduler,
                ))
            except Exception as exc:
                failures.append(
                    f'cleanup.scheduler: {type(exc).__name__}: {exc}'
                )
        if label_created:
            try:
                resources = provider.list_resources({'route': route})
                store = _target(resources, 'store')
                cleanup.append(_apply(
                    provider, route, 'store', 'delete_label',
                    {'label_key': label_key}, store,
                ))
            except Exception as exc:
                failures.append(
                    f'cleanup.label: {type(exc).__name__}: {exc}'
                )
        provider.close()

    return {
        'schema': 'cdeadmin.tikv-control-plane-live.v1',
        'engine_id': 'tikv',
        'expected_runtime': PROFILE.exact_version,
        'runtime_identity': identity,
        'resource_kinds': resource_kinds,
        'temporary_label_key': label_key,
        'operations': operations,
        'cleanup': cleanup,
        'provider_finality_authority': True,
        'automatic_mutation_retry': False,
        'common_transaction_finality_interpretation': False,
        'started_at': started,
        'completed_at': time.time(),
        'failures': failures,
        'passed': not failures,
    }


def object_evidence(evidence):
    """Translate directly observed PD operations into gate evidence."""
    if not evidence.get('passed'):
        raise RuntimeError('failed TiKV control run cannot become evidence')
    kinds = set(evidence.get('resource_kinds', []))
    inspected = {
        kind: ['inspect'] for kind in (
            'cluster', 'store', 'region', 'peer', 'scheduler',
            'configuration',
        ) if kind in kinds
    }
    cluster_operations = dict(inspected)
    exercised = {}
    for item in evidence.get('operations', []):
        kind, operation = item['operation'].split('.', 1)
        exercised.setdefault(kind, []).append(operation)
    for kind, operations in exercised.items():
        cluster_operations.setdefault(kind, []).extend(operations)
        cluster_operations[kind] = sorted(set(cluster_operations[kind]))
    replication = {
        kind: ['inspect'] for kind in (
            'region', 'peer', 'placement-rule'
        ) if kind in kinds
    }
    return {
        'schema': 'cdeadmin.provider-object-live-evidence.v1',
        'engine_id': 'tikv', 'exact_profile': '8.5.6',
        'run_id': f"tikv-control-{evidence['started_at']}",
        'concepts': {'key_value': {
            'replication': {
                'status': 'passed', 'operations': replication,
            },
            'sentinel_or_cluster_state': {
                'status': 'passed', 'operations': cluster_operations,
            },
        }},
    }


def main():
    args = parser().parse_args()
    try:
        evidence = verify(args)
    except Exception as exc:
        evidence = {
            'schema': 'cdeadmin.tikv-control-plane-live.v1',
            'engine_id': 'tikv',
            'passed': False,
            'failures': [f'setup: {type(exc).__name__}: {exc}'],
        }
    document = json.dumps(evidence, indent=2, sort_keys=True)
    print(document)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(document + '\n', encoding='utf-8')
    if args.object_evidence and evidence.get('passed'):
        value = json.dumps(
            object_evidence(evidence), indent=2, sort_keys=True
        ) + '\n'
        args.object_evidence.parent.mkdir(parents=True, exist_ok=True)
        args.object_evidence.write_text(value, encoding='utf-8')
    return 0 if evidence['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
