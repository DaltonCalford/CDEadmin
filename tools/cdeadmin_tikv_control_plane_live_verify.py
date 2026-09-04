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
import base64
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
    value.add_argument(
        '--full-control', action='store_true',
        help='Exercise every TiKV/PD control against a four-store cluster.',
    )
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
           post_state=True, post_state_attempts=1,
           post_state_interval=0.25):
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
        post_state_value = {}
        for attempt in range(max(1, post_state_attempts)):
            observed = provider.validate_visual_admin_post_state({
                'operation_id': evidence['operation_id'],
            })
            post_state_value = observed.get('post_state') or {}
            if post_state_value.get('confirmed') is True:
                break
            if attempt + 1 < post_state_attempts:
                time.sleep(post_state_interval)
        evidence['post_state_confirmed'] = post_state_value.get('confirmed')
        evidence['post_state_observations'] = attempt + 1
        if post_state_value.get('confirmed') is not True:
            raise RuntimeError(
                f'{kind}.{operation} post-state was not confirmed: '
                f'{post_state_value.get("reason")}'
            )
    else:
        if provider_result.get('accepted') is not True:
            raise RuntimeError(f'{kind}.{operation} was not accepted by PD')
        evidence['post_state_confirmed'] = None
        evidence['native_acknowledgement_only'] = True
    return evidence


def _backend(provider):
    return provider.client.backend


def _pd(provider, route, path):
    backend = _backend(provider)
    return backend._pd_document(backend._route({'route': route}), path)


def _stores(provider, route):
    document = _pd(provider, route, '/pd/api/v1/stores')
    return [
        row.get('store') or {} for row in document.get('stores', [])
        if isinstance(row, dict)
    ]


def _regions(provider, route):
    document = _pd(provider, route, '/pd/api/v1/regions')
    return [
        row for row in document.get('regions', [])
        if isinstance(row, dict) and row.get('id') is not None
    ]


def _resource(kind, *path):
    return {
        'resource_kind': kind,
        'resource_id': f'tikv:{kind}:' + ':'.join(map(str, path)),
        'display_name': str(path[-1]),
        'display_path': list(path),
        'authority_path': ['tikv', kind, *path],
        'generation': 'live-qualification',
    }


def _wait_until(predicate, label, attempts=120, interval=0.5):
    last = None
    for _attempt in range(attempts):
        last = predicate()
        if last:
            return last
        time.sleep(interval)
    raise RuntimeError(f'TiKV {label} did not reach its required state')


def _memcmp_encode(value):
    encoded = bytearray()
    for offset in range(0, len(value) + 1, 8):
        chunk = value[offset:offset + 8]
        padding = 8 - len(chunk)
        encoded.extend(chunk)
        encoded.extend(b'\x00' * padding)
        encoded.append(0xff - padding)
        if padding:
            break
    return bytes(encoded)


def _api_v2_raw_region_key(user_key):
    return _memcmp_encode(b'r\x00\x00\x00' + user_key.encode('utf-8')).hex()


def _region_for_encoded_key(provider, route, encoded_key):
    key = bytes.fromhex(encoded_key)
    for row in _regions(provider, route):
        start = bytes.fromhex(row.get('start_key') or '')
        end = bytes.fromhex(row.get('end_key') or '')
        if key >= start and (not end or key < end):
            return row
    raise RuntimeError('TiKV Region for qualification key is unavailable')


def _run_raw(provider, route, operation, key, value=None):
    command = {
        'operation': operation,
        'key_base64': base64.b64encode(key.encode('utf-8')).decode('ascii'),
    }
    if value is not None:
        command['value_base64'] = base64.b64encode(
            value.encode('utf-8')
        ).decode('ascii')
    backend = _backend(provider)
    return backend._run_helper(backend._route({'route': route}), command)


def _assert_store_limits(provider, route, store_ids, rate, limit_type):
    limits = _pd(provider, route, '/pd/api/v1/stores/limit')
    if not all(
        float((limits.get(str(store_id)) or {}).get(limit_type, -1)) ==
        float(rate) for store_id in store_ids
    ):
        raise RuntimeError('TiKV store limit post-state is incorrect')


def _scheduler_status(provider, route, name):
    document = _pd(
        provider, route,
        '/pd/api/v1/schedulers/diagnostic/' + name,
    )
    return str(document.get('status', '')).lower()


def _set_pd_config(provider, route, key, value):
    backend = _backend(provider)
    native = backend._pd_mutate(
        backend._route({'route': route}), 'POST', '/pd/api/v1/config',
        {key: value},
    )
    return {
        'operation': f'cluster.config.{key}',
        'provider_result': native,
        'provider_finality_only': True,
        'automatic_mutation_retry_by_cdeadmin': False,
    }


def _evict_store_present(provider, route, store_id):
    if 'evict-leader-scheduler' not in set(
            _pd(provider, route, '/pd/api/v1/schedulers') or []):
        return False
    document = _pd(
        provider, route,
        '/pd/api/v1/scheduler-config/evict-leader-scheduler/list',
    )
    return str(store_id) in document.get('store-id-ranges', {})


def _full_control_steps(provider, route, suffix, operations, cleanup):
    if route['api_version'] != 2:
        raise RuntimeError('full TiKV control qualification requires API v2')
    stores = _stores(provider, route)
    up_store_ids = sorted(
        int(item['id']) for item in stores
        if item.get('state_name') == 'Up'
    )
    if len(up_store_ids) < 4:
        raise RuntimeError(
            'full TiKV control qualification requires four Up stores'
        )
    resources = provider.list_resources({'route': route})
    cluster = _target(resources, 'cluster')
    schedulers = {
        item['display_name']: item for item in resources
        if item['resource_kind'] == 'scheduler'
    }
    paused = []
    for name in ('balance-leader-scheduler', 'balance-region-scheduler'):
        target = schedulers.get(name)
        if target is None:
            raise RuntimeError(f'TiKV required scheduler {name} is absent')
        operations.append(_apply(
            provider, route, 'scheduler', 'pause',
            {'delay_seconds': 600}, target,
            post_state_attempts=120,
        ))
        paused.append(name)

    original_limits = _pd(provider, route, '/pd/api/v1/stores/limit')
    original_add_rates = {
        store_id: float(original_limits[str(store_id)]['add-peer'])
        for store_id in up_store_ids
    }
    original_add_rate = original_add_rates[up_store_ids[0]]
    trial_rate = original_add_rate + 1
    result = _apply(
        provider, route, 'cluster', 'set_all_store_limits', {
            'rate': trial_rate, 'limit_type': 'add-peer', 'labels': {},
        }, cluster, post_state=False,
    )
    _assert_store_limits(
        provider, route, up_store_ids, trial_rate, 'add-peer'
    )
    result['external_post_state_confirmed'] = True
    operations.append(result)
    for restore_store_id, restore_rate in original_add_rates.items():
        restored = _apply(
            provider, route, 'store', 'set_limit', {
                'rate': restore_rate, 'limit_type': 'add-peer',
            }, _resource('store', restore_store_id), post_state=False,
        )
        _assert_store_limits(
            provider, route, [restore_store_id], restore_rate, 'add-peer'
        )
        restored['external_post_state_confirmed'] = True
        cleanup.append(restored)

    store_id = up_store_ids[-1]
    store = _target(resources, 'store', store_id)
    store_original_rate = original_add_rates[store_id]
    store_trial_rate = store_original_rate + 2
    result = _apply(
        provider, route, 'store', 'set_limit', {
            'rate': store_trial_rate, 'limit_type': 'add-peer',
        }, store, post_state=False,
    )
    _assert_store_limits(
        provider, route, [store_id], store_trial_rate, 'add-peer'
    )
    result['external_post_state_confirmed'] = True
    operations.append(result)
    restored = _apply(
        provider, route, 'store', 'set_limit', {
            'rate': store_original_rate, 'limit_type': 'add-peer',
        }, store, post_state=False,
    )
    _assert_store_limits(
        provider, route, [store_id], store_original_rate, 'add-peer'
    )
    restored['external_post_state_confirmed'] = True
    cleanup.append(restored)

    before_active = {
        int(item['id']) for item in _stores(provider, route)
        if item.get('state_name') != 'Tombstone'
    }
    result = _apply(
        provider, route, 'cluster', 'remove_tombstones', {}, cluster,
        post_state=False,
    )
    after_active = {
        int(item['id']) for item in _stores(provider, route)
        if item.get('state_name') != 'Tombstone'
    }
    if before_active != after_active:
        raise RuntimeError('TiKV tombstone cleanup removed an active store')
    result['external_post_state_confirmed'] = True
    operations.append(result)

    shuffle_name = 'shuffle-leader-scheduler'
    if shuffle_name in schedulers:
        raise RuntimeError('TiKV qualification scheduler already exists')
    operations.append(_apply(
        provider, route, 'scheduler', 'create',
        {'scheduler_name': shuffle_name}, None,
        post_state_attempts=20,
    ))
    shuffle = _resource('scheduler', shuffle_name)
    operations.append(_apply(
        provider, route, 'scheduler', 'drop', {}, shuffle,
        post_state_attempts=20,
    ))

    region = next((
        item for item in _regions(provider, route)
        if len(item.get('peers') or []) == 3 and item.get('leader')
    ), None)
    if region is None:
        raise RuntimeError('TiKV three-voter Region is unavailable')
    region_id = int(region['id'])
    rule_id = f'cdeadmin-{suffix}'
    rule_draft = {
        'group_id': 'pd', 'rule_id': rule_id,
        'role': 'voter', 'count': 3,
        'start_key': region.get('start_key') or '',
        'end_key': region.get('end_key') or '',
        'location_labels': [], 'constraints': [],
        'rule_index': 100, 'override': True,
    }
    operations.append(_apply(
        provider, route, 'placement-rule', 'create',
        rule_draft, None, post_state_attempts=20,
    ))
    rule = _resource('placement-rule', 'pd', rule_id)
    altered_rule = dict(rule_draft)
    altered_rule.pop('group_id')
    altered_rule.pop('rule_id')
    altered_rule['rule_index'] = 101
    operations.append(_apply(
        provider, route, 'placement-rule', 'alter',
        altered_rule, rule, post_state_attempts=20,
    ))
    operations.append(_apply(
        provider, route, 'placement-rule', 'drop', {}, rule,
        post_state_attempts=20,
    ))

    region = next(item for item in _regions(provider, route)
                  if int(item['id']) == region_id)
    peer_store_ids = {
        int(item['store_id']) for item in region.get('peers') or []
    }
    spare_store_id = next(
        item for item in up_store_ids if item not in peer_store_ids
    )
    original_leader = int(region['leader']['store_id'])
    destination_leader = next(
        item for item in peer_store_ids if item != original_leader
    )
    region_target = _resource('region', region_id)
    operations.append(_apply(
        provider, route, 'region', 'transfer_leader',
        {'to_store_id': destination_leader}, region_target,
        post_state_attempts=120,
    ))

    source_store_id = next(
        item for item in peer_store_ids
        if item not in {original_leader, destination_leader}
    )
    operations.append(_apply(
        provider, route, 'region', 'transfer_peer', {
            'from_store_id': source_store_id,
            'to_store_id': spare_store_id,
        }, region_target, post_state_attempts=120,
    ))
    operations.append(_apply(
        provider, route, 'region', 'transfer_peer', {
            'from_store_id': spare_store_id,
            'to_store_id': source_store_id,
        }, region_target, post_state_attempts=120,
    ))
    learner_label = f'cdeadmin-learner-{suffix}'
    learner_rule_id = f'{suffix}-learner'
    spare_store = _target(resources, 'store', spare_store_id)
    operations.append(_apply(
        provider, route, 'store', 'set_labels', {
            'labels': {learner_label: 'true'}, 'force': False,
        }, spare_store, post_state_attempts=20,
    ))
    config = _pd(provider, route, '/pd/api/v1/config')
    replica_limit = config['schedule']['replica-schedule-limit']
    configured = _set_pd_config(
        provider, route, 'replica-schedule-limit', 0
    )
    _wait_until(
        lambda: _pd(provider, route, '/pd/api/v1/config')['schedule'][
            'replica-schedule-limit'
        ] == 0,
        'replica scheduler pause', attempts=20,
    )
    configured['external_post_state_confirmed'] = True
    operations.append(configured)
    learner_rule_draft = {
        'group_id': 'cdeadmin', 'rule_id': learner_rule_id,
        'role': 'learner', 'count': 1,
        'start_key': region.get('start_key') or '',
        'end_key': region.get('end_key') or '',
        'location_labels': [],
        'constraints': [{
            'key': learner_label, 'op': 'in', 'values': ['true'],
        }],
        'rule_index': 100, 'override': False,
    }
    operations.append(_apply(
        provider, route, 'placement-rule', 'create',
        learner_rule_draft, None, post_state_attempts=20,
    ))
    operations.append(_apply(
        provider, route, 'region', 'add_learner',
        {'store_id': spare_store_id}, region_target,
        post_state_attempts=120,
    ))
    operations.append(_apply(
        provider, route, 'region', 'remove_peer',
        {'store_id': spare_store_id}, region_target,
        post_state_attempts=120,
    ))
    learner_rule = _resource(
        'placement-rule', 'cdeadmin', learner_rule_id
    )
    cleanup.append(_apply(
        provider, route, 'placement-rule', 'drop', {}, learner_rule,
        post_state_attempts=20,
    ))
    voter_rule_id = f'{suffix}-voter'
    voter_rule_draft = dict(learner_rule_draft)
    voter_rule_draft.update({
        'rule_id': voter_rule_id, 'role': 'voter',
    })
    operations.append(_apply(
        provider, route, 'placement-rule', 'create',
        voter_rule_draft, None, post_state_attempts=20,
    ))
    operations.append(_apply(
        provider, route, 'region', 'add_peer',
        {'store_id': spare_store_id}, region_target,
        post_state_attempts=120,
    ))
    operations.append(_apply(
        provider, route, 'region', 'remove_peer',
        {'store_id': spare_store_id}, region_target,
        post_state_attempts=120,
    ))
    cleanup.append(_apply(
        provider, route, 'placement-rule', 'drop', {},
        _resource('placement-rule', 'cdeadmin', voter_rule_id),
        post_state_attempts=20,
    ))
    cleanup.append(_apply(
        provider, route, 'store', 'delete_label', {
            'label_key': learner_label,
        }, spare_store, post_state_attempts=20,
    ))
    restored_config = _set_pd_config(
        provider, route, 'replica-schedule-limit', replica_limit
    )
    _wait_until(
        lambda: _pd(provider, route, '/pd/api/v1/config')['schedule'][
            'replica-schedule-limit'
        ] == replica_limit,
        'replica scheduler restoration', attempts=20,
    )
    restored_config['external_post_state_confirmed'] = True
    cleanup.append(restored_config)
    operations.append(_apply(
        provider, route, 'region', 'scatter', {'group': suffix},
        region_target, post_state=False,
    ))

    first_key = f'cdeadmin/{suffix}/split/a'
    split_key = f'cdeadmin/{suffix}/split/m'
    last_key = f'cdeadmin/{suffix}/split/z'
    for key in (first_key, split_key, last_key):
        _run_raw(provider, route, 'put', key, 'qualification')
    encoded_split_key = _api_v2_raw_region_key(split_key)
    split_region = _region_for_encoded_key(
        provider, route, encoded_split_key
    )
    split_region_id = int(split_region['id'])
    split_target = _resource('region', split_region_id)
    before_ids = {int(item['id']) for item in _regions(provider, route)}
    operations.append(_apply(
        provider, route, 'region', 'split', {
            'policy': 'usekey', 'keys': [encoded_split_key],
        }, split_target, post_state_attempts=120,
    ))
    after_regions = _regions(provider, route)
    new_ids = {
        int(item['id']) for item in after_regions
    }.difference(before_ids)
    if len(new_ids) != 1:
        raise RuntimeError('TiKV split did not produce one child Region')
    new_region_id = new_ids.pop()
    new_region = next(
        item for item in after_regions if int(item['id']) == new_region_id
    )
    old_region = next(
        item for item in after_regions if int(item['id']) == split_region_id
    )
    adjacent = (
        new_region.get('end_key') == old_region.get('start_key') or
        old_region.get('end_key') == new_region.get('start_key')
    )
    if not adjacent:
        raise RuntimeError('TiKV split children are not adjacent')
    operations.append(_apply(
        provider, route, 'region', 'merge', {
            'target_region_id': split_region_id,
        }, _resource('region', new_region_id),
        post_state_attempts=120,
    ))
    for key in (first_key, split_key, last_key):
        _run_raw(provider, route, 'delete', key)

    evict = _apply(
        provider, route, 'store', 'evict_leaders', {}, store,
        post_state_attempts=20,
    )
    operations.append(evict)
    cancelled = provider.cancel_visual_admin_operation({
        'operation_id': evict['operation_id'],
    })
    if cancelled.get('cancel_request_dispatched') is not True:
        raise RuntimeError('TiKV leader eviction cancellation was not sent')
    _wait_until(
        lambda: not _evict_store_present(provider, route, store_id),
        'leader eviction cancellation', attempts=40,
    )
    cleanup.append({
        'operation': 'store.evict_leaders.cancel',
        'operation_id': evict['operation_id'],
        'post_state_confirmed': True,
    })

    operations.append(_apply(
        provider, route, 'store', 'mark_offline', {}, store,
        post_state_attempts=40,
    ))
    operations.append(_apply(
        provider, route, 'store', 'bring_up', {}, store,
        post_state_attempts=40,
    ))

    keyspace_name = f'cdeadmin-{suffix}'
    operations.append(_apply(
        provider, route, 'keyspace', 'create', {
            'name': keyspace_name,
            'config': {'qualification': suffix},
        }, None, post_state_attempts=20,
    ))
    keyspace = _resource('keyspace', keyspace_name)
    operations.append(_apply(
        provider, route, 'keyspace', 'update_config', {
            'config': {'qualification': suffix + '-updated'},
        }, keyspace, post_state_attempts=20,
    ))
    for operation in (
            'disable', 'enable', 'disable', 'archive', 'tombstone'):
        operations.append(_apply(
            provider, route, 'keyspace', operation, {}, keyspace,
            post_state_attempts=20,
        ))

    if destination_leader != original_leader:
        current = next((
            item for item in _regions(provider, route)
            if int(item['id']) == region_id
        ), None)
        if current and any(
            int(item.get('store_id', -1)) == original_leader
            for item in current.get('peers') or []
        ) and int(
            (current.get('leader') or {}).get('store_id', -1)
        ) != original_leader:
            cleanup.append(_apply(
                provider, route, 'region', 'transfer_leader',
                {'to_store_id': original_leader}, region_target,
                post_state_attempts=120,
            ))
    for name in reversed(paused):
        cleanup.append(_apply(
            provider, route, 'scheduler', 'resume', {},
            _resource('scheduler', name), post_state_attempts=120,
        ))


def _full_control(provider, route, suffix, operations, cleanup):
    """Run the full control suite and restore all reversible native state."""
    if route['api_version'] != 2:
        raise RuntimeError('full TiKV control qualification requires API v2')
    initial_stores = _stores(provider, route)
    initial_up = {
        int(item['id']) for item in initial_stores
        if item.get('state_name') == 'Up'
    }
    initial_limits = _pd(provider, route, '/pd/api/v1/stores/limit')
    initial_config = _pd(provider, route, '/pd/api/v1/config')
    initial_replica_limit = initial_config['schedule'][
        'replica-schedule-limit'
    ]
    initial_schedulers = set(
        _pd(provider, route, '/pd/api/v1/schedulers') or []
    )
    managed_schedulers = {
        'balance-leader-scheduler', 'balance-region-scheduler',
    }
    initial_scheduler_states = {
        name: _scheduler_status(provider, route, name)
        for name in managed_schedulers if name in initial_schedulers
    }
    paused_at_start = [
        name for name, status in initial_scheduler_states.items()
        if status == 'paused'
    ]
    if paused_at_start:
        raise RuntimeError(
            'full TiKV control qualification requires active schedulers: ' +
            ', '.join(sorted(paused_at_start))
        )
    initial_regions = _regions(provider, route)
    protected_region = next((
        item for item in initial_regions
        if len(item.get('peers') or []) == 3 and item.get('leader')
    ), None)
    rule_id = f'cdeadmin-{suffix}'
    learner_rule_id = f'{suffix}-learner'
    voter_rule_id = f'{suffix}-voter'
    learner_label = f'cdeadmin-learner-{suffix}'
    keyspace_name = f'cdeadmin-{suffix}'
    shuffle_name = 'shuffle-leader-scheduler'
    evict_store_id = max(initial_up) if initial_up else None
    keys = tuple(
        f'cdeadmin/{suffix}/split/{part}' for part in ('a', 'm', 'z')
    )
    failure = None
    cleanup_failures = []
    try:
        _full_control_steps(
            provider, route, suffix, operations, cleanup
        )
    except Exception as exc:
        failure = exc

    def recover(label, action):
        try:
            value = action()
            if value is not None:
                cleanup.append(value)
        except Exception as exc:
            cleanup_failures.append(
                f'{label}: {type(exc).__name__}: {exc}'
            )

    def current_schedulers():
        return set(_pd(provider, route, '/pd/api/v1/schedulers') or [])

    if shuffle_name not in initial_schedulers and (
            shuffle_name in current_schedulers()):
        recover('scheduler.drop', lambda: _apply(
            provider, route, 'scheduler', 'drop', {},
            _resource('scheduler', shuffle_name), post_state_attempts=20,
        ))
    if evict_store_id is not None:
        evict_name = f'evict-leader-scheduler-{evict_store_id}'
        scheduler_names = current_schedulers()
        evict_config = {}
        if 'evict-leader-scheduler' in scheduler_names:
            evict_config = _pd(
                provider, route,
                '/pd/api/v1/scheduler-config/'
                'evict-leader-scheduler/list',
            )
        if str(evict_store_id) in evict_config.get(
                'store-id-ranges', {}):
            recover('store.evict.cancel', lambda: _backend(
                provider
            )._pd_mutate(
                _backend(provider)._route({'route': route}), 'DELETE',
                f'/pd/api/v1/schedulers/{evict_name}',
            ))

    rules = _pd(provider, route, '/pd/api/v1/config/rules')
    for group_id, cleanup_rule_id in (
            ('pd', rule_id), ('cdeadmin', learner_rule_id),
            ('cdeadmin', voter_rule_id)):
        if any(
            isinstance(item, dict) and item.get('group_id') == group_id and
            item.get('id') == cleanup_rule_id for item in rules
        ):
            recover('placement-rule.drop', lambda group_id=group_id,
                    cleanup_rule_id=cleanup_rule_id: _apply(
                        provider, route, 'placement-rule', 'drop', {},
                        _resource(
                            'placement-rule', group_id, cleanup_rule_id
                        ), post_state_attempts=20,
                    ))

    for store_row in _stores(provider, route):
        labels = {
            item.get('key') for item in store_row.get('labels', [])
            if isinstance(item, dict)
        }
        if learner_label in labels:
            cleanup_store_id = int(store_row['id'])
            recover('store.learner-label', lambda
                    cleanup_store_id=cleanup_store_id: _apply(
                        provider, route, 'store', 'delete_label', {
                            'label_key': learner_label,
                        }, _resource('store', cleanup_store_id),
                        post_state_attempts=20,
                    ))

    current_replica_limit = _pd(
        provider, route, '/pd/api/v1/config'
    )['schedule']['replica-schedule-limit']
    if current_replica_limit != initial_replica_limit:
        def restore_replica_limit():
            restored = _set_pd_config(
                provider, route, 'replica-schedule-limit',
                initial_replica_limit,
            )
            _wait_until(
                lambda: _pd(
                    provider, route, '/pd/api/v1/config'
                )['schedule']['replica-schedule-limit'] ==
                initial_replica_limit,
                'replica scheduler cleanup', attempts=20,
            )
            restored['external_post_state_confirmed'] = True
            return restored

        recover('cluster.replica-limit', restore_replica_limit)

    def retire_keyspace():
        listed = _pd(provider, route, '/pd/api/v2/keyspaces')
        current = next((
            item for item in listed.get('keyspaces', [])
            if isinstance(item, dict) and item.get('name') == keyspace_name
        ), None)
        if current is None:
            return None
        state = str(current.get('state', '')).upper()
        restored = []
        keyspace = _resource('keyspace', keyspace_name)
        if state == 'ENABLED':
            restored.append(_apply(
                provider, route, 'keyspace', 'disable', {}, keyspace,
                post_state_attempts=20,
            ))
            state = 'DISABLED'
        if state == 'DISABLED':
            restored.append(_apply(
                provider, route, 'keyspace', 'archive', {}, keyspace,
                post_state_attempts=20,
            ))
            state = 'ARCHIVED'
        if state == 'ARCHIVED':
            restored.append(_apply(
                provider, route, 'keyspace', 'tombstone', {}, keyspace,
                post_state_attempts=20,
            ))
        return {
            'operation': 'keyspace.retire.cleanup',
            'keyspace': keyspace_name,
            'operations': restored,
            'post_state_confirmed': True,
        } if restored else None

    recover('keyspace.retire', retire_keyspace)

    initial_region_ids = {
        int(item['id']) for item in initial_regions
    }
    first_encoded = _api_v2_raw_region_key(keys[0])
    last_encoded = _api_v2_raw_region_key(keys[-1])
    initial_first = next((
        item for item in initial_regions
        if bytes.fromhex(first_encoded) >= bytes.fromhex(
            item.get('start_key') or ''
        ) and (
            not item.get('end_key') or bytes.fromhex(first_encoded) <
            bytes.fromhex(item['end_key'])
        )
    ), None)
    initial_last = next((
        item for item in initial_regions
        if bytes.fromhex(last_encoded) >= bytes.fromhex(
            item.get('start_key') or ''
        ) and (
            not item.get('end_key') or bytes.fromhex(last_encoded) <
            bytes.fromhex(item['end_key'])
        )
    ), None)
    if initial_first and initial_last and (
            initial_first['id'] == initial_last['id']):
        current_first = _region_for_encoded_key(
            provider, route, first_encoded
        )
        current_last = _region_for_encoded_key(
            provider, route, last_encoded
        )
        if current_first['id'] != current_last['id']:
            candidates = (current_first, current_last)
            source = next((
                item for item in candidates
                if int(item['id']) not in initial_region_ids
            ), current_first)
            target = next(
                item for item in candidates if item['id'] != source['id']
            )
            recover('region.split-merge', lambda: _apply(
                provider, route, 'region', 'merge', {
                    'target_region_id': int(target['id']),
                }, _resource('region', int(source['id'])),
                post_state_attempts=120,
            ))

    for key in keys:
        recover(f'raw-key.delete.{key}', lambda key=key: {
            'operation': 'key.delete.cleanup',
            'key': key,
            'provider_result': _run_raw(
                provider, route, 'delete', key
            ),
            'post_state_confirmed': True,
        })

    if protected_region is not None:
        protected_id = int(protected_region['id'])
        desired_peers = {
            int(item['store_id']) for item in protected_region.get(
                'peers', [])
        }

        def restore_peers():
            current = next((
                item for item in _regions(provider, route)
                if int(item['id']) == protected_id
            ), None)
            if current is None:
                return None
            current_peers = {
                int(item['store_id']): str(
                    item.get('role_name', '')
                ).lower() for item in current.get('peers') or []
            }
            missing = list(desired_peers.difference(current_peers))
            extras = list(set(current_peers).difference(desired_peers))
            restored = []
            while missing and extras:
                source = extras.pop()
                destination = missing.pop()
                if current_peers[source] == 'learner':
                    restored.append(_apply(
                        provider, route, 'region', 'remove_peer',
                        {'store_id': source}, _resource(
                            'region', protected_id
                        ), post_state_attempts=120,
                    ))
                    restored.append(_apply(
                        provider, route, 'region', 'add_peer',
                        {'store_id': destination}, _resource(
                            'region', protected_id
                        ), post_state_attempts=120,
                    ))
                else:
                    restored.append(_apply(
                        provider, route, 'region', 'transfer_peer', {
                            'from_store_id': source,
                            'to_store_id': destination,
                        }, _resource('region', protected_id),
                        post_state_attempts=120,
                    ))
            for destination in missing:
                restored.append(_apply(
                    provider, route, 'region', 'add_peer',
                    {'store_id': destination},
                    _resource('region', protected_id),
                    post_state_attempts=120,
                ))
            for source in extras:
                restored.append(_apply(
                    provider, route, 'region', 'remove_peer',
                    {'store_id': source}, _resource(
                        'region', protected_id
                    ), post_state_attempts=120,
                ))
            desired_leader = int(
                protected_region['leader']['store_id']
            )
            current = next((
                item for item in _regions(provider, route)
                if int(item['id']) == protected_id
            ), None)
            if current and int(
                    (current.get('leader') or {}).get('store_id', -1)
                    ) != desired_leader:
                restored.append(_apply(
                    provider, route, 'region', 'transfer_leader',
                    {'to_store_id': desired_leader},
                    _resource('region', protected_id),
                    post_state_attempts=120,
                ))
            return {
                'operation': 'region.restore.cleanup',
                'region_id': protected_id,
                'operations': restored,
                'post_state_confirmed': True,
            } if restored else None

        recover('region.restore', restore_peers)

    current_stores = {
        int(item['id']): item for item in _stores(provider, route)
    }
    for store_id in initial_up:
        if str(current_stores.get(store_id, {}).get(
                'state_name', '')).lower() != 'up':
            recover(f'store.bring-up.{store_id}', lambda store_id=store_id: (
                _apply(
                    provider, route, 'store', 'bring_up', {},
                    _resource('store', store_id), post_state_attempts=40,
                )
            ))

    current_limits = _pd(provider, route, '/pd/api/v1/stores/limit')
    for store_id in initial_up:
        original = initial_limits.get(str(store_id), {})
        current = current_limits.get(str(store_id), {})
        for limit_type in ('add-peer', 'remove-peer'):
            if original.get(limit_type) is not None and float(
                    current.get(limit_type, -1)
                    ) != float(original[limit_type]):
                recover(
                    f'store.limit.{store_id}.{limit_type}',
                    lambda store_id=store_id, limit_type=limit_type,
                    rate=float(original[limit_type]): _apply(
                        provider, route, 'store', 'set_limit', {
                            'rate': rate, 'limit_type': limit_type,
                        }, _resource('store', store_id), post_state=False,
                    ),
                )

    for scheduler_name, initial_status in initial_scheduler_states.items():
        current_status = _scheduler_status(provider, route, scheduler_name)
        if initial_status != 'paused' and current_status == 'paused':
            recover(f'scheduler.resume.{scheduler_name}', lambda
                    scheduler_name=scheduler_name: _apply(
                        provider, route, 'scheduler', 'resume', {},
                        _resource('scheduler', scheduler_name),
                        post_state_attempts=40,
                    ))

    if cleanup_failures:
        detail = '; '.join(cleanup_failures)
        if failure is not None:
            raise RuntimeError(
                f'{type(failure).__name__}: {failure}; cleanup: {detail}'
            ) from failure
        raise RuntimeError(f'TiKV full-control cleanup failed: {detail}')
    if failure is not None:
        raise failure


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
            post_state_attempts=40,
        ))
        scheduler_paused = True
        operations.append(_apply(
            provider, route, 'scheduler', 'resume', {}, scheduler,
            post_state_attempts=40,
        ))
        scheduler_paused = False
        operations.append(_apply(
            provider, route, 'store', 'delete_label',
            {'label_key': label_key}, store,
        ))
        label_created = False
        if args.full_control:
            _full_control(
                provider, route, suffix, operations, cleanup
            )
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
    cluster_allowed = {
        'cluster': {'remove_tombstones', 'set_all_store_limits'},
        'region': {
            'add_learner', 'add_peer', 'merge', 'remove_peer',
            'scatter', 'split', 'transfer_leader', 'transfer_peer',
        },
        'scheduler': {'create', 'drop', 'pause', 'resume'},
        'store': {
            'bring_up', 'delete_label', 'evict_leaders', 'mark_offline',
            'set_labels', 'set_limit', 'set_weights',
        },
    }
    for kind, allowed in cluster_allowed.items():
        operations = set(exercised.get(kind, [])).intersection(allowed)
        if not operations:
            continue
        cluster_operations.setdefault(kind, []).extend(operations)
        cluster_operations[kind] = sorted(set(cluster_operations[kind]))
    replication = {
        kind: ['inspect'] for kind in (
            'region', 'peer', 'placement-rule'
        ) if kind in kinds
    }
    for kind in ('region', 'placement-rule'):
        if kind in exercised:
            allowed = (
                cluster_allowed['region'] if kind == 'region' else
                {'create', 'alter', 'drop'}
            )
            replication.setdefault(kind, []).extend(
                set(exercised[kind]).intersection(allowed)
            )
            replication[kind] = sorted(set(replication[kind]))
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
