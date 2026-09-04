#!/usr/bin/env python3
##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Qualify full YugabyteDB YSQL control administration on exact 2025.2.2.2."""

from __future__ import annotations

import argparse
import json
import re
import secrets
import subprocess
import sys
import time
from pathlib import Path
from types import ModuleType


ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / 'web'
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(WEB) not in sys.path:
    sys.path.insert(0, str(WEB))
if 'pgadmin' not in sys.modules:
    package = ModuleType('pgadmin')
    package.__path__ = [str(WEB / 'pgadmin')]
    sys.modules['pgadmin'] = package

from pgadmin.cdeadmin.providers.yugabytedb.provider import (  # noqa: E402
    PROFILE,
    create_provider,
)
from pgadmin.cdeadmin.security import EndpointSecretService  # noqa: E402
from tools.cdeadmin_relational_provider_live_verify import (  # noqa: E402
    _context,
    _merge_object_evidence,
    _object_operation_evidence,
    _permissions,
    _verified_context,
)


def _run(arguments, *, check=True, timeout=180):
    return subprocess.run(
        arguments, check=check, capture_output=True, text=True,
        timeout=timeout,
    )


def _image_identity(container, expected):
    document = json.loads(_run([
        'docker', 'inspect', container, '--format', '{{json .}}',
    ]).stdout)
    image = str(document.get('Config', {}).get('Image') or '')
    if image != expected:
        raise RuntimeError(
            f'YugabyteDB container {container} is not {expected}'
        )
    return {
        'container': container, 'requested_reference': image,
        'image_id': document.get('Image'),
    }


def _provider(route):
    context = _context(PROFILE)
    secrets_service = EndpointSecretService()
    permissions = _permissions(context, secrets_service)
    provider = create_provider(context, permissions)
    discovered = provider.discover_endpoint({'route': route})
    provider.close()
    context = _verified_context(context, discovered)
    return create_provider(
        context, _permissions(context, secrets_service)
    ), discovered


def _target(kind, path, native=None):
    value = {
        'resource_id': ':'.join([kind, *path]),
        'resource_kind': kind, 'display_name': path[-1],
        'display_path': path, 'authority_path': [kind, *path],
        'generation': 'yugabytedb-full-live-gate',
    }
    if native:
        value['native'] = dict(native)
    return value


class _Qualification:
    def __init__(self):
        self.passed = {}
        self.failures = {}
        self.receipts = []

    def record(self, kind, operation):
        self.passed.setdefault(kind, set()).add(operation)

    def apply(
            self, provider, route, kind, operation, draft, target=None,
            *, label=None):
        label = label or f'{kind}.{operation}'
        try:
            request = {
                'resource_kind': kind, 'operation_id': operation,
                'target_resource': target, 'draft': draft,
                '_provider_route': route,
            }
            validation = provider.validate_visual_admin(request)
            if validation.get('valid') is not True:
                raise RuntimeError(f'validation failed: {validation}')
            plan = provider.plan_visual_admin(request)
            result = provider.apply_visual_admin({
                'plan_id': plan['plan_id'],
                'plan_digest': plan['plan_digest'], 'confirmed': True,
            })
            if result.get('automatic_mutation_retry') is not False:
                raise RuntimeError(
                    'provider admitted automatic mutation retry')
            operation_id = result['control_operation']['operation_id']
            if result.get('post_state_required'):
                observed = provider.validate_visual_admin_post_state({
                    'operation_id': operation_id,
                })
                state = observed.get('post_state') or {}
                if state.get('confirmed') is not True:
                    raise RuntimeError(
                        f'post-state not confirmed: {state.get("reason")}'
                    )
                observation = 'post_state_confirmed'
            else:
                observed = provider.refresh_visual_admin_operation({
                    'operation_id': operation_id,
                })
                if observed.get('stage') != 'provider_observation_recorded':
                    raise RuntimeError('provider observation was not recorded')
                observation = observed['stage']
            self.record(kind, operation)
            self.receipts.append({
                'operation': label, 'plan_id': plan['plan_id'],
                'provider_constructed': bool(
                    plan.get('command_preview', {}).get(
                        'provider_constructed')),
                'observation': observation,
                'automatic_mutation_retry': False,
            })
            print(f'PASS {label}', flush=True)
            return result
        except Exception as exc:
            self.failures[label] = f'{type(exc).__name__}: {exc}'
            print(f'FAIL {label}: {self.failures[label]}', flush=True)
            return None

    def inspect(self, provider, route, kind, name):
        label = f'{kind}.inspect'
        try:
            resources = provider.list_resources({'route': route})
            item = next(
                value for value in resources
                if value.get('resource_kind') == kind and
                value.get('display_name') == name
            )
            observed = provider.inspect_resource({
                'route': route, 'resource_id': item['resource_id'],
            })
            if observed.get('resource_kind') != kind:
                raise RuntimeError('resource kind changed during inspection')
            self.record(kind, 'inspect')
            print(f'PASS {label}', flush=True)
            return item
        except Exception as exc:
            self.failures[label] = f'{type(exc).__name__}: {exc}'
            print(f'FAIL {label}: {self.failures[label]}', flush=True)
            return None


def _table_id(executable, masters, database, table):
    result = _run([
        str(executable), '--master_addresses', masters,
        'list_tables', 'include_db_type', 'include_table_id',
        'include_table_type',
    ])
    pattern = re.compile(
        rf'^ysql\.{re.escape(database)}\.{re.escape(table)} '
        r'\[ysql_schema=public\] \[([A-Fa-f0-9-]+)\] '
        r'[A-Fa-f0-9-]+ table$', re.MULTILINE,
    )
    match = pattern.search(result.stdout)
    if match is None:
        raise RuntimeError(f'YugabyteDB table ID is unavailable: {table}')
    return match.group(1)


def _new_resource(provider, route, kind, before):
    resources = provider.list_resources({'route': route})
    return next(
        item for item in resources
        if item.get('resource_kind') == kind and
        item.get('display_name') not in before
    )


def _create_database_and_tables(
        qualification, provider, route, database, tables, label):
    admin_route = dict(route, database='yugabyte')
    created = qualification.apply(
        provider, admin_route, 'database', 'create', {'name': database},
        label=f'database.create.{label}',
    )
    if created is None:
        return False
    route['database'] = database
    for table in tables:
        created = qualification.apply(provider, route, 'table', 'create', {
            'name': table, 'parent': 'public',
            'columns': [
                {'name': 'id', 'type': 'BIGINT', 'nullable': False,
                 'primary_key': True},
                {'name': 'value', 'type': 'TEXT', 'nullable': True,
                 'primary_key': False},
            ],
            'constraints': [],
        }, label=f'table.create.{label}.{table}')
        if created is None:
            return False
    return True


def _drop_database(qualification, provider, route, database, label):
    admin_route = dict(route, database='yugabyte')
    return qualification.apply(
        provider, admin_route, 'database', 'drop', {
            'cascade': False, 'confirmation': database,
        }, _target('database', [database]), label=f'database.drop.{label}',
    )


def _exercise(args, consumer, producer, consumer_route, producer_route):
    suffix = secrets.token_hex(4)
    database = f'cde_yb_full_{suffix}'
    tables = (f'base_{suffix}', f'added_{suffix}')
    tablespace_name = f'cde_yb_ts_{suffix}'
    group = f'cde_xcluster_{suffix}'
    qualification = _Qualification()
    consumer_created = False
    producer_created = False
    try:
        consumer_created = _create_database_and_tables(
            qualification, consumer, consumer_route, database, tables,
            'consumer',
        )
        producer_created = _create_database_and_tables(
            qualification, producer, producer_route, database, tables,
            'producer',
        )
        if not consumer_created or not producer_created:
            return qualification

        resources = consumer.list_resources({'route': consumer_route})
        table_resource = next(
            item for item in resources
            if item.get('resource_kind') == 'table' and
            item.get('display_name') == tables[0]
        )
        tablespace_created = qualification.apply(
            consumer, consumer_route, 'tablespace', 'create', {
                'name': tablespace_name,
                'replica_placement': {
                    'num_replicas': 1,
                    'placement_blocks': [{
                        'cloud': 'cloud1', 'region': 'datacenter1',
                        'zone': 'rack1', 'min_num_replicas': 1,
                    }],
                },
            },
        )
        if tablespace_created is not None:
            qualification.apply(
                consumer, consumer_route, 'table', 'configure_placement', {
                    'tablespace': tablespace_name,
                }, table_resource,
            )

        before = {
            item['display_name'] for item in resources
            if item.get('resource_kind') == 'changefeed'
        }
        if qualification.apply(
                consumer, consumer_route, 'changefeed', 'create', {
                    'namespace': f'ysql.{database}',
                    'checkpoint_type': 'EXPLICIT', 'record_type': 'CHANGE',
                    'snapshot_mode': 'EXPORT_SNAPSHOT',
                    'dynamic_tables': True,
                }) is not None:
            changefeed = _new_resource(
                consumer, consumer_route, 'changefeed', before)
            qualification.inspect(
                consumer, consumer_route, 'changefeed',
                changefeed['display_name'])
            qualification.apply(
                consumer, consumer_route, 'changefeed', 'drop', {},
                changefeed,
            )

        before = {
            item['display_name'] for item in consumer.list_resources({
                'route': consumer_route,
            }) if item.get('resource_kind') == 'schedule'
        }
        if qualification.apply(
                consumer, consumer_route, 'schedule', 'create', {
                    'interval_minutes': 60, 'retention_minutes': 180,
                    'namespace': f'ysql.{database}',
                }) is not None:
            schedule = _new_resource(
                consumer, consumer_route, 'schedule', before)
            qualification.inspect(
                consumer, consumer_route, 'schedule',
                schedule['display_name'])
            qualification.apply(
                consumer, consumer_route, 'schedule', 'alter', {
                    'interval_minutes': 120, 'retention_minutes': 240,
                }, schedule,
            )
            current = qualification.inspect(
                consumer, consumer_route, 'schedule',
                schedule['display_name'])
            native = (current or {}).get('native')
            if not isinstance(native, dict):
                native = (current or {}).get('extensions', {}).get(
                    'yugabytedb', {}).get('native', {})
            snapshots = native.get('snapshots', [])
            if snapshots:
                qualification.apply(
                    consumer, consumer_route, 'schedule', 'restore', {
                        'restore_timestamp': snapshots[0]['snapshot_time'],
                    }, schedule,
                )
            else:
                qualification.failures['schedule.restore'] = (
                    'snapshot schedule emitted no restorable snapshot'
                )
            qualification.apply(
                consumer, consumer_route, 'schedule', 'drop', {}, schedule)

        producer_ids = [
            _table_id(
                args.producer_yb_admin_path,
                args.producer_master_addresses, database, table,
            ) for table in tables
        ]
        if qualification.apply(
                consumer, consumer_route, 'xcluster-replication', 'create', {
                    'replication_group_id': group,
                    'producer_master_addresses': [
                        args.producer_master_addresses,
                    ],
                    'table_ids': [producer_ids[0]], 'bootstrap_ids': [],
                    'transactional': False,
                }) is not None:
            xcluster = _target('xcluster-replication', [group])
            qualification.inspect(
                consumer, consumer_route, 'xcluster-replication', group)
            qualification.apply(
                consumer, consumer_route, 'xcluster-replication',
                'set_enabled', {'enabled': False}, xcluster)
            qualification.apply(
                consumer, consumer_route, 'xcluster-replication',
                'set_enabled', {'enabled': True}, xcluster,
                label='xcluster-replication.set_enabled.reenable')
            qualification.apply(
                consumer, consumer_route, 'xcluster-replication',
                'add_tables', {'table_ids': [producer_ids[1]],
                               'bootstrap_ids': []}, xcluster)
            qualification.apply(
                consumer, consumer_route, 'xcluster-replication',
                'remove_tables', {'table_ids': [producer_ids[1]],
                                  'ignore_errors': False}, xcluster)
            qualification.apply(
                consumer, consumer_route, 'xcluster-replication', 'drop', {
                    'ignore_errors': False,
                }, xcluster)
        if tablespace_created is not None:
            qualification.apply(
                consumer, consumer_route, 'table', 'configure_placement', {
                    'tablespace': 'pg_default',
                }, table_resource,
                label='table.configure_placement.reset',
            )
            qualification.apply(
                consumer, consumer_route, 'tablespace', 'drop', {
                    'cascade': False, 'confirmation': tablespace_name,
                }, _target('tablespace', [tablespace_name]),
            )
    finally:
        if consumer_created:
            consumer.close()
            cleanup, _identity = _provider(dict(
                consumer_route, database='yugabyte'))
            try:
                _drop_database(
                    qualification, cleanup, consumer_route, database,
                    'consumer.cleanup')
            finally:
                cleanup.close()
        if producer_created:
            producer.close()
            cleanup, _identity = _provider(dict(
                producer_route, database='yugabyte'))
            try:
                _drop_database(
                    qualification, cleanup, producer_route, database,
                    'producer.cleanup')
            finally:
                cleanup.close()
    return qualification


def run(args):
    consumer_executable = args.yb_admin_path.expanduser().resolve()
    producer_executable = args.producer_yb_admin_path.expanduser().resolve()
    if not consumer_executable.is_file() or not producer_executable.is_file():
        raise RuntimeError('YugabyteDB qualification toolchain is incomplete')
    images = [
        _image_identity(args.consumer_container, args.image),
        _image_identity(args.producer_container, args.image),
    ]
    consumer_route = {
        'route_id': 'yugabytedb-full-consumer', 'host': args.host,
        'port': args.port, 'user': 'yugabyte', 'database': 'yugabyte',
        'autocommit': True, 'connection_timeout': 10,
        'yb_admin_path': str(consumer_executable),
        'master_addresses': args.master_addresses,
        'yb_admin_timeout_ms': 120000,
    }
    producer_route = {
        'route_id': 'yugabytedb-full-producer', 'host': args.host,
        'port': args.producer_port, 'user': 'yugabyte',
        'database': 'yugabyte', 'autocommit': True,
        'connection_timeout': 10,
        'yb_admin_path': str(producer_executable),
        'master_addresses': args.producer_master_addresses,
        'yb_admin_timeout_ms': 120000,
    }
    consumer, discovered = _provider(consumer_route)
    producer, producer_discovered = _provider(producer_route)
    started = time.time()
    try:
        qualification = _exercise(
            args, consumer, producer, consumer_route, producer_route)
        evidence = _object_operation_evidence(
            consumer, qualification.passed, PROFILE.engine_id,
            scope='exact-full-yugabytedb-control-plane',
            failures=qualification.failures,
        )
        if args.baseline_object_evidence:
            baseline = json.loads(args.baseline_object_evidence.read_text(
                encoding='utf-8'))
            evidence = _merge_object_evidence(baseline, evidence)
    finally:
        consumer.close()
        producer.close()
    return {
        'schema': 'cdeadmin.yugabytedb-full-live-verification.v1',
        'engine_id': PROFILE.engine_id, 'exact_profile': PROFILE.exact_version,
        'activation_ready': not evidence['operation_failures'],
        'verified_runtime': discovered['verified_runtime'],
        'producer_verified_runtime': producer_discovered['verified_runtime'],
        'runtime_images': images,
        'object_experience_evidence': evidence,
        'operation_receipts': qualification.receipts,
        'automatic_mutation_retry': False,
        'common_transaction_finality_interpretation': False,
        'credential_values_exported': False,
        'duration_seconds': round(time.time() - started, 3),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=25433)
    parser.add_argument('--producer-port', type=int, default=35433)
    parser.add_argument('--master-addresses',
                        default='cdeadmin-yugabytedb-full:7100')
    parser.add_argument('--producer-master-addresses',
                        default='cdeadmin-yugabytedb-producer:7100')
    parser.add_argument('--yb-admin-path', type=Path, required=True)
    parser.add_argument('--producer-yb-admin-path', type=Path, required=True)
    parser.add_argument('--consumer-container',
                        default='cdeadmin-yugabytedb-full')
    parser.add_argument('--producer-container',
                        default='cdeadmin-yugabytedb-producer')
    parser.add_argument('--image',
                        default='yugabytedb/yugabyte:2025.2.2.2-b11')
    parser.add_argument('--baseline-object-evidence', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--object-output', type=Path, required=True)
    args = parser.parse_args(argv)
    result = run(args)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, indent=2, sort_keys=True) + '\n',
        encoding='utf-8',
    )
    args.object_output.write_text(json.dumps(
        result['object_experience_evidence'], indent=2, sort_keys=True,
    ) + '\n', encoding='utf-8')
    print(json.dumps({
        'engine_id': result['engine_id'],
        'activation_ready': result['activation_ready'],
        'operation_failures': result['object_experience_evidence'][
            'operation_failures'],
    }, indent=2, sort_keys=True))
    return 0 if result['activation_ready'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
