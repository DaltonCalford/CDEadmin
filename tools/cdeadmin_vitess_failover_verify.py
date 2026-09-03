#!/usr/bin/env python3
##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Qualify provider-driven Vitess administration across tablet failover."""

from __future__ import annotations

import argparse
import importlib
import json
import subprocess
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


class Permissions:
    @staticmethod
    def require(_permission, _scope='endpoint'):
        return None

    @staticmethod
    def allows(_permission, _scope='endpoint'):
        return True

    @staticmethod
    def acquire_secret(*_args):
        raise RuntimeError('Vitess qualification has no credential')


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=15306)
    parser.add_argument('--http-port', type=int, default=15099)
    parser.add_argument('--keyspace', default='test_keyspace')
    parser.add_argument('--shard', default='-80')
    parser.add_argument(
        '--container-prefix', default='cdeadmin-vitess-qual-vttablet'
    )
    parser.add_argument('--allow-failure-injection', action='store_true')
    parser.add_argument('--output', type=Path, required=True)
    return parser.parse_args()


def route(args, database):
    return {
        'route_id': 'vitess-failover-gate', 'host': args.host,
        'port': args.port, 'user': 'root', 'database': database,
        'vtgate_http_host': args.host,
        'vtgate_http_port': args.http_port,
        'vtgate_http_tls_mode': 'disable',
    }


def target(table, database):
    return {
        'resource_id': f'table:{database}:{table}',
        'resource_kind': 'table', 'display_name': table,
        'display_path': [database, table],
        'authority_path': [database, 'table', table],
        'generation': 'live-failover-gate',
    }


def apply(provider, route_value, target_value, operation, draft):
    request = {
        'resource_kind': 'table', 'operation_id': operation,
        'draft': draft, '_provider_route': route_value,
    }
    if target_value is not None:
        request['target_resource'] = target_value
    validation = provider.validate_visual_admin(request)
    if not validation['valid']:
        raise RuntimeError(
            f'table.{operation} validation failed: {validation["errors"]}'
        )
    plan = provider.plan_visual_admin(request)
    result = provider.apply_visual_admin({
        'plan_id': plan['plan_id'],
        'plan_digest': plan['plan_digest'],
        'confirmed': True,
    })
    if result['transaction_finality_interpreted_by_common_code']:
        raise RuntimeError('common visual code interpreted Vitess finality')
    native = result['provider_result']
    return {
        'operation': f'table.{operation}',
        'provider_constructed': bool(
            plan.get('command_preview', {}).get('provider_constructed')
        ),
        'commit_requested': native.get('commit_requested'),
        'driver_observation_only': native.get('driver_observation_only'),
        'statement_count': len(native.get('statement_results', [])),
    }


def tablets(provider, route_value):
    client = provider.client
    connection = client._connect({'route': route_value})
    cursor = connection.cursor()
    try:
        cursor.execute('SHOW VITESS_TABLETS')
        return [dict(zip(
            [str(item[0]) for item in cursor.description], row
        )) for row in cursor.fetchall()]
    finally:
        client._safe_close(cursor)
        client._forget_and_close(connection)


def primary(provider, route_value, keyspace, shard):
    for row in tablets(provider, route_value):
        if (
            row['Keyspace'] == keyspace and row['Shard'] == shard and
            row['TabletType'] == 'PRIMARY' and row['State'] == 'SERVING'
        ):
            return row
    return None


def wait_primary(provider, route_value, keyspace, shard, excluded_alias,
                 timeout=120):
    started = time.monotonic()
    observations = 0
    while time.monotonic() - started < timeout:
        observations += 1
        try:
            row = primary(provider, route_value, keyspace, shard)
            if row is not None and row['Alias'] != excluded_alias:
                return {
                    'alias': row['Alias'], 'hostname': row['Hostname'],
                    'observations': observations,
                    'duration_seconds': round(
                        time.monotonic() - started, 3
                    ),
                }
        except Exception:
            pass
        time.sleep(1)
    raise RuntimeError('VTOrc did not publish a replacement primary')


def wait_alias_serving(provider, route_value, alias, timeout=120):
    started = time.monotonic()
    observations = 0
    while time.monotonic() - started < timeout:
        observations += 1
        try:
            for row in tablets(provider, route_value):
                if row['Alias'] == alias and row['State'] == 'SERVING':
                    return {
                        'alias': alias, 'tablet_type': row['TabletType'],
                        'observations': observations,
                        'duration_seconds': round(
                            time.monotonic() - started, 3
                        ),
                    }
        except Exception:
            pass
        time.sleep(1)
    raise RuntimeError('restarted Vitess tablet did not return to service')


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
    module = importlib.import_module(
        'pgadmin.cdeadmin.providers.vitess.provider'
    )
    context = SimpleNamespace(
        endpoint_id=f'vitess-failover-{uuid.uuid4()}',
        session_namespace=f'vitess-failover-session-{uuid.uuid4()}',
        mode='legacy_native', runtime_verification_state='verified',
        declared_runtime_family='vitess',
        verified_runtime_family='vitess',
    )
    provider = module.create_provider(context, Permissions())
    base_route = route(args, args.keyspace)
    shard_route = route(
        args, f'{args.keyspace}:{args.shard}@primary'
    )
    table_name = f'cdeadmin_failover_{uuid.uuid4().hex[:12]}'
    table = target(table_name, args.keyspace)
    evidence = {
        'schema': 'cdeadmin.vitess-failover-live-verification.v1',
        'engine_id': 'vitess', 'expected_runtime': '23.0.3',
        'keyspace': args.keyspace, 'shard': args.shard,
        'failure_injection_authorized': True,
        'automatic_mutation_retry': False,
        'common_transaction_finality_interpretation': False,
        'raw_mutation_sql_supplied_by_gate': False,
        'steps': {}, 'failures': [], 'status': 'failed',
    }
    table_created = False
    stopped_container = None
    old_alias = None
    try:
        identity = provider.discover_endpoint({'route': base_route})[
            'verified_runtime'
        ]
        if identity['version'] != '23.0.3':
            raise RuntimeError('Vitess runtime version is not exact')
        evidence['verified_runtime'] = identity
        evidence['steps']['create'] = apply(
            provider, base_route, None, 'create', {
                'name': table_name, 'parent': args.keyspace,
                'columns': [
                    {
                        'name': 'id', 'type': 'BIGINT', 'nullable': False,
                        'primary_key': True,
                    },
                    {
                        'name': 'note', 'type': 'VARCHAR(255)',
                        'nullable': False,
                    },
                ],
                'constraints': [], 'register_in_vschema': True,
                'vindex_name': 'xxhash', 'vindex_columns': ['id'],
            },
        )
        table_created = True
        for index in range(16):
            apply(provider, base_route, table, 'insert', {
                'values': {'id': index, 'note': f'before-{index}'},
            })
        evidence['steps']['seed'] = {
            'row_count': 16, 'mutation_retried': False,
        }
        old = primary(
            provider, base_route, args.keyspace, args.shard
        )
        if old is None:
            raise RuntimeError('initial Vitess primary is unavailable')
        old_alias = str(old['Alias'])
        uid = str(int(old_alias.rsplit('-', 1)[-1]))
        stopped_container = f'{args.container_prefix}{uid}-1'
        evidence['steps']['initial_primary'] = {
            'alias': old_alias, 'hostname': old['Hostname'],
            'container': stopped_container,
        }
        docker('stop', stopped_container)
        evidence['steps']['replacement_primary'] = wait_primary(
            provider, base_route, args.keyspace, args.shard, old_alias
        )
        shard_target = target(table_name, args.keyspace)
        evidence['steps']['post_failover_insert'] = apply(
            provider, shard_route, shard_target, 'insert', {
                'values': {'id': 1000001, 'note': 'after-failover'},
            },
        )
        evidence['steps']['post_failover_insert'][
            'mutation_retried'
        ] = False
        page = provider.read_visual_admin_rows({
            '_provider_route': shard_route,
            'target_resource': shard_target, 'limit': 100,
        })
        if not any(
            item['values'].get('id') == 1000001
            for item in page['rows']
        ):
            raise RuntimeError('post-failover routed row was not observed')
        evidence['steps']['post_failover_read'] = {
            'routed_row_observed': True, 'read_only_observation': True,
        }
        docker('start', stopped_container)
        evidence['steps']['rejoined_tablet'] = wait_alias_serving(
            provider, base_route, old_alias
        )
        stopped_container = None
        evidence['status'] = 'passed'
    except Exception as exc:
        evidence['failures'].append(f'{type(exc).__name__}: {exc}')
    finally:
        if stopped_container:
            try:
                docker('start', stopped_container)
                if old_alias:
                    wait_alias_serving(provider, base_route, old_alias)
            except Exception as exc:
                evidence['failures'].append(
                    f'recovery {type(exc).__name__}: {exc}'
                )
        if table_created:
            try:
                evidence['steps']['cleanup'] = apply(
                    provider, base_route, table, 'drop', {
                        'cascade': False, 'confirmation': table_name,
                        'drop_vschema_registration': True,
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
