#!/usr/bin/env python3
##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Qualify exact YugabyteDB 2025.2.2.2 native object controls."""

from __future__ import annotations

import argparse
import json
import secrets
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
)
from tools.cdeadmin_relational_provider_live_verify import (  # noqa: E402
    _object_operation_evidence,
)
from tools.cdeadmin_yugabytedb_full_live_gate import (  # noqa: E402
    _Qualification,
    _drop_database,
    _provider,
    _target,
)


REQUIRED = {
    'cluster': {'set_load_balancer'},
    'master': {'inspect', 'leader_stepdown'},
    'node': {
        'add_blacklist', 'add_leader_blacklist',
        'remove_blacklist', 'remove_leader_blacklist',
    },
    'placement-policy': {'clear', 'configure', 'inspect'},
    'snapshot': {'create_database', 'drop', 'inspect', 'restore'},
    'table': {'compact'},
    'tablet': {'inspect', 'leader_stepdown', 'split'},
}


def _native(resource):
    native = resource.get('native')
    if isinstance(native, dict):
        return native
    extensions = resource.get('extensions')
    if isinstance(extensions, dict):
        provider = extensions.get('yugabytedb')
        if isinstance(provider, dict) and isinstance(
                provider.get('native'), dict):
            return provider['native']
    return {}


def _placement(document):
    replication = document.get('replicationInfo', document)
    live = replication.get('liveReplicas', replication) if isinstance(
        replication, dict
    ) else {}
    blocks = live.get('placementBlocks', []) if isinstance(live, dict) else []
    placements = []
    for block in blocks:
        cloud = block.get('cloudInfo', {}) if isinstance(block, dict) else {}
        parts = [
            cloud.get('placementCloud'), cloud.get('placementRegion'),
            cloud.get('placementZone'),
        ]
        if not all(isinstance(item, str) and item for item in parts):
            continue
        value = '.'.join(parts)
        minimum = block.get('minNumReplicas')
        if isinstance(minimum, int) and minimum > 0:
            value += f':{minimum}'
        placements.append(value)
    replicas = live.get('numReplicas') if isinstance(live, dict) else None
    if not placements or not isinstance(replicas, int) or replicas < 1:
        raise RuntimeError('YugabyteDB original placement is unavailable')
    return {
        'placements': placements,
        'replication_factor': replicas,
        'placement_uuid': live.get('placementUuid'),
    }


def _missing(passed):
    return {
        kind: sorted(operations.difference(passed.get(kind, set())))
        for kind, operations in REQUIRED.items()
        if operations.difference(passed.get(kind, set()))
    }


def verify(args):
    executable = args.yb_admin_path.expanduser().resolve()
    if not executable.is_file():
        raise RuntimeError('YugabyteDB exact yb-admin wrapper is unavailable')
    route = {
        'route_id': 'yugabytedb-native-control-qualification',
        'host': args.host, 'port': args.port, 'user': 'yugabyte',
        'database': 'yugabyte', 'autocommit': True,
        'connection_timeout': 10, 'yb_admin_path': str(executable),
        'master_addresses': args.master_addresses,
        'yb_admin_timeout_ms': 120000,
    }
    provider, discovered = _provider(route)
    qualification = _Qualification()
    suffix = secrets.token_hex(4)
    database = f'cde_yb_control_{suffix}'
    table_name = f'control_{suffix}'
    database_created = False
    load_balancer_disabled = False
    placement_changed = False
    server_blacklisted = False
    leader_blacklisted = False
    master_changed = False
    original_placement = None
    snapshot = None
    started = time.time()
    try:
        if discovered['verified_runtime']['version'] != PROFILE.exact_version:
            raise RuntimeError('YugabyteDB exact runtime identity changed')
        resources = provider.list_resources({'route': route})
        cluster = next(
            item for item in resources
            if item.get('resource_kind') == 'cluster')
        placement = next(
            item for item in resources
            if item.get('resource_kind') == 'placement-policy')
        masters = [
            item for item in resources
            if item.get('resource_kind') == 'master'
        ]
        nodes = [
            item for item in resources
            if item.get('resource_kind') == 'node'
        ]
        if len(masters) < 2 or len(nodes) < 2:
            raise RuntimeError(
                'YugabyteDB control qualification requires two live nodes')
        secondary_node = next(
            item for item in nodes
            if args.secondary_node_match in item['display_name'])
        secondary_uuid = _native(secondary_node).get('uuid')
        if not isinstance(secondary_uuid, str) or not secondary_uuid:
            raise RuntimeError('YugabyteDB secondary TServer UUID is absent')
        leader = next(
            item for item in masters if _native(item).get('role') == 'LEADER')
        follower = next(item for item in masters if item is not leader)

        qualification.inspect(
            provider, route, 'master', leader['display_name'])
        qualification.inspect(
            provider, route, 'placement-policy', placement['display_name'])
        original_placement = _placement(_native(placement))

        qualification.apply(
            provider, route, 'cluster', 'set_load_balancer',
            {'enabled': False}, cluster)
        load_balancer_disabled = True
        qualification.apply(
            provider, route, 'cluster', 'set_load_balancer',
            {'enabled': True}, cluster,
            label='cluster.set_load_balancer.reenable')
        load_balancer_disabled = False

        qualification.apply(
            provider, route, 'node', 'add_blacklist', {}, secondary_node)
        server_blacklisted = True
        qualification.apply(
            provider, route, 'node', 'remove_blacklist', {}, secondary_node)
        server_blacklisted = False
        qualification.apply(
            provider, route, 'node', 'add_leader_blacklist', {},
            secondary_node)
        leader_blacklisted = True
        qualification.apply(
            provider, route, 'node', 'remove_leader_blacklist', {},
            secondary_node)
        leader_blacklisted = False

        qualification.apply(
            provider, route, 'placement-policy', 'configure', {
                'placements': ['cloud1.datacenter1.rack1:2'],
                'replication_factor': 2,
            })
        placement_changed = True
        created = qualification.apply(
            provider, dict(route, database='yugabyte'),
            'database', 'create', {'name': database},
            label='database.create.native-control',
        )
        if created is None:
            raise RuntimeError('YugabyteDB control fixture creation failed')
        database_created = True
        route['database'] = database
        created = qualification.apply(
            provider, route, 'table', 'create', {
                'name': table_name,
                'parent': 'public',
                'columns': [
                    {
                        'name': 'id',
                        'type': 'BIGINT',
                        'nullable': False,
                        'primary_key': True,
                    },
                    {
                        'name': 'value',
                        'type': 'TEXT',
                        'nullable': True,
                        'primary_key': False,
                    },
                ],
                'constraints': [],
            }, label=f'table.create.native-control.{table_name}',
        )
        if created is None:
            raise RuntimeError('YugabyteDB control table creation failed')

        resources = provider.list_resources({'route': route})
        table = next(
            item for item in resources
            if item.get('resource_kind') == 'table' and
            item.get('display_name') == table_name)
        tablet = next(
            item for item in resources
            if item.get('resource_kind') == 'tablet' and
            _native(item).get('table') == table_name)
        qualification.inspect(
            provider, route, 'tablet', tablet['display_name'])
        qualification.apply(
            provider, route, 'table', 'compact', {
                'timeout_seconds': 60,
                'include_indexes': False,
                'include_vector_indexes': False,
            }, table)
        changed = qualification.apply(
            provider, route, 'master', 'leader_stepdown', {
                'destination_uuid': follower['display_name'],
            }, leader)
        if changed is None:
            raise RuntimeError('YugabyteDB master stepdown failed')
        master_changed = True
        restored = qualification.apply(
            provider, route, 'master', 'leader_stepdown', {
                'destination_uuid': leader['display_name'],
            }, follower, label='master.leader_stepdown.restore')
        if restored is None:
            raise RuntimeError('YugabyteDB master leadership restore failed')
        master_changed = False
        tablet_native = _native(tablet)
        tablet_leader = tablet_native.get('leader')
        followers = tablet_native.get('followers')
        if not isinstance(tablet_leader, dict) or not isinstance(
                followers, list) or not followers:
            raise RuntimeError(
                'YugabyteDB tablet has no observed follower for stepdown')
        destination = next(
            item.get('uuid') for item in followers
            if isinstance(item, dict) and isinstance(item.get('uuid'), str)
        )
        qualification.apply(
            provider, route, 'tablet', 'leader_stepdown', {
                'destination_uuid': destination,
            }, tablet)
        qualification.apply(
            provider, route, 'tablet', 'split', {}, tablet)

        before = {
            item['display_name'] for item in provider.list_resources({
                'route': route,
            }) if item.get('resource_kind') == 'snapshot'
        }
        qualification.apply(
            provider, route, 'snapshot', 'create_database', {
                'database': database,
            })
        snapshots = [
            item for item in provider.list_resources({'route': route})
            if item.get('resource_kind') == 'snapshot' and
            item['display_name'] not in before
        ]
        if len(snapshots) != 1:
            raise RuntimeError('YugabyteDB created snapshot is ambiguous')
        snapshot = snapshots[0]
        qualification.inspect(
            provider, route, 'snapshot', snapshot['display_name'])
        qualification.apply(
            provider, route, 'snapshot', 'restore', {}, snapshot)
        qualification.apply(
            provider, route, 'snapshot', 'drop', {}, snapshot)
        snapshot = None

        qualification.apply(
            provider, route, 'placement-policy', 'clear', {}, placement)
        qualification.apply(
            provider, route, 'placement-policy', 'configure',
            original_placement, label='placement-policy.configure.restore')
        placement_changed = False
    except Exception as exc:
        qualification.failures.setdefault(
            'qualification', f'{type(exc).__name__}: {exc}')
    finally:
        if snapshot is not None:
            qualification.apply(
                provider, route, 'snapshot', 'drop', {}, snapshot,
                label='snapshot.drop.cleanup')
        if master_changed:
            qualification.apply(
                provider, route, 'master', 'leader_stepdown', {
                    'destination_uuid': leader['display_name'],
                }, follower, label='master.leader_stepdown.cleanup')
        if leader_blacklisted:
            qualification.apply(
                provider, route, 'node', 'remove_leader_blacklist', {},
                secondary_node, label='node.remove_leader_blacklist.cleanup')
        if server_blacklisted:
            qualification.apply(
                provider, route, 'node', 'remove_blacklist', {},
                secondary_node, label='node.remove_blacklist.cleanup')
        if load_balancer_disabled:
            qualification.apply(
                provider, route, 'cluster', 'set_load_balancer',
                {'enabled': True}, cluster,
                label='cluster.set_load_balancer.cleanup')
        if placement_changed and original_placement:
            qualification.apply(
                provider, route, 'placement-policy', 'configure',
                original_placement,
                label='placement-policy.configure.cleanup')
        if database_created:
            provider.close()
            cleanup, _identity = _provider(dict(
                route, database='yugabyte'))
            try:
                _drop_database(
                    qualification, cleanup, route, database,
                    'native-control.cleanup')
            finally:
                cleanup.close()
        else:
            provider.close()

    missing = _missing(qualification.passed)
    evidence_provider, _identity = _provider(dict(
        route, database='yugabyte'))
    try:
        object_evidence = _object_operation_evidence(
            evidence_provider, qualification.passed, PROFILE.engine_id,
            scope='exact-yugabytedb-native-control-plane',
            failures={**qualification.failures, **{
                f'missing.{kind}': ','.join(operations)
                for kind, operations in missing.items()
            }},
        )
    finally:
        evidence_provider.close()
    return {
        'schema': 'cdeadmin.yugabytedb-native-control-live.v1',
        'engine_id': PROFILE.engine_id,
        'exact_profile': PROFILE.exact_version,
        'activation_ready': not qualification.failures and not missing,
        'verified_runtime': discovered['verified_runtime'],
        'object_experience_evidence': object_evidence,
        'operation_receipts': qualification.receipts,
        'missing_operations': missing,
        'automatic_mutation_retry': False,
        'common_transaction_finality_interpretation': False,
        'duration_seconds': round(time.time() - started, 3),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=55433)
    parser.add_argument('--master-addresses', required=True)
    parser.add_argument('--yb-admin-path', type=Path, required=True)
    parser.add_argument(
        '--secondary-node-match', default='qualification-2')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--object-output', type=Path, required=True)
    args = parser.parse_args(argv)
    result = verify(args)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, indent=2, sort_keys=True) + '\n',
        encoding='utf-8',
    )
    args.object_output.write_text(json.dumps(
        result['object_experience_evidence'], indent=2, sort_keys=True,
    ) + '\n', encoding='utf-8')
    print(json.dumps({
        'activation_ready': result['activation_ready'],
        'missing_operations': result['missing_operations'],
        'operation_failures': result['object_experience_evidence'][
            'operation_failures'],
    }, indent=2, sort_keys=True))
    return 0 if result['activation_ready'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
