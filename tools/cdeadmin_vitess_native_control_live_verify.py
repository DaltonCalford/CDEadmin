#!/usr/bin/env python3
##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Qualify exact Vitess 23.0.3 native object administration controls."""

from __future__ import annotations

import argparse
import json
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

from pgadmin.cdeadmin.providers.vitess.provider import PROFILE  # noqa: E402
from tools.cdeadmin_relational_provider_live_verify import (  # noqa: E402
    _merge_object_evidence,
    _object_operation_evidence,
)
from tools.cdeadmin_vitess_full_live_gate import (  # noqa: E402
    _Qualification,
    _provider,
    _remove_container,
    _target,
)


REQUIRED = {
    'backup': {'drop', 'inspect'},
    'routing-rule': {'apply', 'inspect'},
    'shard': {
        'create', 'drop', 'emergency_reparent', 'planned_reparent',
    },
    'tablet': {
        'backup', 'change_type', 'drop', 'ping', 'refresh_state', 'restore',
        'run_health_check', 'set_writable',
    },
    'vindex': {'create', 'drop'},
    'vschema': {'rebuild'},
}


def _run(arguments, *, check=True, timeout=180):
    result = subprocess.run(
        arguments, check=False, capture_output=True, text=True,
        timeout=timeout,
    )
    if check and result.returncode != 0:
        detail = ' '.join(
            ((result.stdout or '') + (result.stderr or '')).split())[:2048]
        raise RuntimeError(
            f'command failed with status {result.returncode}: {detail}')
    return result


def _native(resource):
    native = resource.get('native')
    if isinstance(native, dict):
        return native
    extensions = resource.get('extensions')
    if isinstance(extensions, dict):
        provider = extensions.get('vitess')
        if isinstance(provider, dict) and isinstance(
                provider.get('native'), dict):
            return provider['native']
    return {}


def _tablet_alias(row):
    alias = row.get('alias') if isinstance(row, dict) else None
    if not isinstance(alias, dict) or not isinstance(
            alias.get('cell'), str):
        return None
    try:
        return f'{alias["cell"]}-{int(alias["uid"]):010d}'
    except (KeyError, TypeError, ValueError):
        return None


def _client(args, *arguments, check=True, timeout=180):
    return _run([
        str(args.vtctldclient_path.resolve()), '--server',
        args.vtctld_server, *arguments,
    ], check=check, timeout=timeout)


def _tablets(args, keyspace):
    result = _client(
        args, 'GetTablets', '--format', 'json', '--keyspace', keyspace,
    )
    rows = json.loads(result.stdout)
    return rows if isinstance(rows, list) else []


def _wait_tablets(args, keyspace, count, timeout=180):
    deadline = time.monotonic() + timeout
    last = []
    while time.monotonic() < deadline:
        try:
            last = _tablets(args, keyspace)
            if len(last) >= count and all(
                    _client(
                        args, 'PingTablet', _tablet_alias(row),
                        check=False, timeout=15,
                    ).returncode == 0 for row in last):
                return last
        except (OSError, RuntimeError, ValueError,
                subprocess.SubprocessError):
            pass
        time.sleep(1)
    raise RuntimeError(
        f'Vitess did not publish {count} healthy tablets: {last}')


def _wait_primary(args, keyspace, alias=None, timeout=180):
    deadline = time.monotonic() + timeout
    last = {}
    while time.monotonic() < deadline:
        try:
            response = _client(args, 'GetShard', f'{keyspace}/-')
            last = json.loads(response.stdout)
            primary = (last.get('shard') or {}).get('primary_alias')
            current = _tablet_alias({'alias': primary})
            if current and (alias is None or current == alias) and _client(
                    args, 'PingTablet', current, check=False,
                    timeout=15).returncode == 0:
                return current
        except (OSError, RuntimeError, ValueError,
                subprocess.SubprocessError):
            pass
        time.sleep(1)
    raise RuntimeError(
        f'Vitess primary {alias or "tablet"} was not observed: {last}')


def _start_tablet(args, container, keyspace, uid):
    topology = (
        '--topo-implementation consul --topo-global-server-address '
        'consul1:8500 --topo-global-root vitess/global'
    )
    _run([
        'docker', 'run', '-d', '--name', container,
        '--network', args.docker_network,
        '-v', f'{args.compose_dir.resolve()}:/script',
        '-v', f'{args.backup_volume}:/vt/vtdataroot/backups',
        '-e', f'TOPOLOGY_FLAGS={topology}', '-e', 'GRPC_PORT=15999',
        '-e', 'WEB_PORT=8080', '-e', 'CELL=test',
        '-e', f'KEYSPACE={keyspace}', '-e', 'SHARD=-',
        '-e', 'ROLE=replica', '-e', f'VTHOST={container}',
        '-e', 'EXTERNAL_DB=0', args.image, 'sh', '-c',
        f'/script/vttablet-up.sh {uid}',
    ])


def _require(result, label):
    if result is None:
        raise RuntimeError(f'Vitess {label} failed')
    return result


def _find(provider, route, kind, name=None, path=None):
    matches = [
        item for item in provider.list_resources({'route': route})
        if item.get('resource_kind') == kind and
        (name is None or item.get('display_name') == name) and
        (path is None or item.get('display_path') == path)
    ]
    if len(matches) != 1:
        raise RuntimeError(
            f'Vitess {kind} resource is not uniquely observable: {matches}')
    return matches[0]


def _missing(passed):
    return {
        kind: sorted(operations.difference(passed.get(kind, set())))
        for kind, operations in REQUIRED.items()
        if operations.difference(passed.get(kind, set()))
    }


def verify(args):
    executable = args.vtctldclient_path.expanduser().resolve()
    if not executable.is_file():
        raise RuntimeError('Vitess exact vtctldclient wrapper is unavailable')
    args.vtctldclient_path = executable
    provider = _provider()
    route = {
        'route_id': 'vitess-native-control-qualification',
        'host': args.host, 'port': args.port, 'user': 'root',
        'database': args.source_keyspace,
        'vtgate_http_host': args.host,
        'vtgate_http_port': args.http_port,
        'vtgate_http_tls_mode': 'disable',
        'vtctldclient_path': str(executable),
        'vtctld_server': args.vtctld_server,
        'vtctld_action_timeout_seconds': 1800,
    }
    qualification = _Qualification(provider, route)
    suffix = secrets.token_hex(4)
    keyspace = f'cde_native_{suffix}'
    vindex_name = f'cde_vdx_{suffix}'
    first_uid = 1000 + int(suffix[:2], 16) * 3
    while first_uid % 100 % 3 == 0:
        first_uid += 1
    second_uid = first_uid + 1
    while second_uid % 100 % 3 == 0:
        second_uid += 1
    first_alias = f'test-{first_uid:010d}'
    second_alias = f'test-{second_uid:010d}'
    containers = [
        f'cdeadmin-vitess-native-{suffix}-a',
        f'cdeadmin-vitess-native-{suffix}-b',
    ]
    keyspace_target = _target('keyspace', [keyspace])
    shard_target = _target('shard', [keyspace, '-'])
    keyspace_created = False
    shard_created = False
    first_started = False
    second_started = False
    backup_target = None
    original_rules = None
    started = time.time()
    discovered = None
    try:
        discovered = provider.discover_endpoint({'route': route})
        if discovered['verified_runtime']['version'] != PROFILE.exact_version:
            raise RuntimeError('Vitess exact runtime identity changed')

        routing = _find(
            provider, route, 'routing-rule', 'global-routing-rules')
        qualification.inspect(
            'routing-rule', routing['display_name'],
            routing['display_path'])
        original_rules = {'rules': _native(routing).get('rules', [])}
        _require(qualification.apply(
            'routing-rule', 'apply', {
                'rules': original_rules,
                'cells': [], 'skip_rebuild': False,
            }), 'routing rule apply')

        _require(qualification.apply('keyspace', 'create', {
            'name': keyspace, 'durability_policy': 'none',
            'allow_empty_vschema': True,
        }), 'temporary keyspace create')
        keyspace_created = True
        _require(qualification.apply('shard', 'create', {
            'keyspace': keyspace, 'shard': '-',
        }), 'shard create')
        shard_created = True

        _start_tablet(args, containers[0], keyspace, first_uid)
        first_started = True
        _start_tablet(args, containers[1], keyspace, second_uid)
        second_started = True
        _wait_tablets(args, keyspace, 2)
        primary_alias = _wait_primary(args, keyspace)
        _client(
            args, 'ApplySchema', '--sql',
            'CREATE TABLE cdeadmin_fixture ('
            'id BIGINT NOT NULL PRIMARY KEY)', keyspace,
        )
        _wait_tablets(args, keyspace, 2)
        replica_alias = (
            second_alias if primary_alias == first_alias else first_alias
        )
        primary_container = containers[
            0 if primary_alias == first_alias else 1]
        replica_container = containers[
            1 if replica_alias == second_alias else 0]
        route['database'] = keyspace
        primary_target = _target(
            'tablet', [keyspace, '-', primary_alias])
        replica_target = _target(
            'tablet', [keyspace, '-', replica_alias])

        for operation in ('ping', 'refresh_state', 'run_health_check'):
            _require(qualification.apply(
                'tablet', operation, {}, replica_target),
                f'tablet {operation}')
        _require(qualification.apply(
            'tablet', 'change_type', {'tablet_type': 'rdonly'},
            replica_target), 'tablet change to rdonly')
        _require(qualification.apply(
            'tablet', 'change_type', {'tablet_type': 'replica'},
            replica_target, label='tablet.change_type.restore'),
            'tablet change to replica')
        _require(qualification.apply(
            'tablet', 'set_writable', {'writable': False},
            primary_target), 'tablet disable writes')
        _require(qualification.apply(
            'tablet', 'set_writable', {'writable': True},
            primary_target, label='tablet.set_writable.restore'),
            'tablet enable writes')

        _require(qualification.apply(
            'vindex', 'create', {
                'name': vindex_name, 'vindex_type': 'xxhash',
                'parameters': {},
            }), 'vindex create')
        vindex_target = _find(
            provider, route, 'vindex', vindex_name,
            [keyspace, vindex_name])
        vschema = _find(provider, route, 'vschema', keyspace, [keyspace])
        _require(qualification.apply(
            'vschema', 'rebuild', {'cells': []}, vschema),
            'VSchema rebuild')
        _require(qualification.apply(
            'vindex', 'drop', {'confirmation': vindex_name},
            vindex_target), 'vindex drop')

        _require(qualification.apply(
            'tablet', 'backup', {}, replica_target), 'tablet backup')
        backup_target = _find(provider, route, 'backup')
        qualification.inspect(
            'backup', backup_target['display_name'],
            backup_target['display_path'])
        _require(qualification.apply(
            'tablet', 'restore', {}, replica_target), 'tablet restore')
        _wait_tablets(args, keyspace, 2)

        _require(qualification.apply(
            'shard', 'planned_reparent', {
                'new_primary': replica_alias,
                'expected_primary': primary_alias,
                'allow_cross_cell': False,
            }, shard_target), 'planned reparent')
        _wait_primary(args, keyspace, replica_alias)
        _run(['docker', 'stop', '--timeout', '30', replica_container])
        if replica_container == containers[0]:
            first_started = False
        else:
            second_started = False
        _require(qualification.apply(
            'shard', 'emergency_reparent', {
                'new_primary': primary_alias,
                'expected_primary': replica_alias,
                'prevent_cross_cell': True,
                'wait_for_all_tablets': False,
            }, shard_target), 'emergency reparent')
        _wait_primary(args, keyspace, primary_alias)

        _require(qualification.apply(
            'tablet', 'drop', {'allow_primary': True}, replica_target),
            'tablet topology drop')
        _remove_container(replica_container)
        _require(qualification.apply(
            'backup', 'drop', {}, backup_target), 'backup drop')
        backup_target = None

        _run(['docker', 'stop', '--timeout', '30', primary_container])
        if primary_container == containers[0]:
            first_started = False
        else:
            second_started = False
        _require(qualification.apply(
            'shard', 'drop', {
                'recursive': True, 'even_if_serving': True,
            }, shard_target), 'shard drop')
        shard_created = False
        _remove_container(primary_container)
    except Exception as exc:
        qualification.failures.setdefault(
            'qualification', f'{type(exc).__name__}: {exc}')
    finally:
        if backup_target is not None:
            qualification.apply(
                'backup', 'drop', {}, backup_target,
                label='backup.drop.cleanup')
        if first_started:
            _run(
                ['docker', 'stop', '--timeout', '30', containers[0]],
                check=False, timeout=60)
        if second_started:
            _run(
                ['docker', 'stop', '--timeout', '30', containers[1]],
                check=False, timeout=60)
        for container in containers:
            _remove_container(container)
        if shard_created:
            qualification.apply('shard', 'drop', {
                'recursive': True, 'even_if_serving': True,
            }, shard_target, label='shard.drop.cleanup')
        if keyspace_created:
            qualification.apply('keyspace', 'drop', {
                'recursive': True, 'force': False,
            }, keyspace_target, label='keyspace.drop.cleanup')
        provider.close()

    missing = _missing(qualification.passed)
    evidence_provider = _provider()
    try:
        object_evidence = _object_operation_evidence(
            evidence_provider, qualification.passed, PROFILE.engine_id,
            scope='exact-vitess-native-control-plane',
            failures={**qualification.failures, **{
                f'missing.{kind}': ','.join(operations)
                for kind, operations in missing.items()
            }},
        )
        if args.baseline_object_evidence:
            object_evidence = _merge_object_evidence(
                json.loads(args.baseline_object_evidence.read_text(
                    encoding='utf-8')),
                object_evidence,
            )
    finally:
        evidence_provider.close()
    return {
        'schema': 'cdeadmin.vitess-native-control-live.v1',
        'engine_id': PROFILE.engine_id,
        'exact_profile': PROFILE.exact_version,
        'activation_ready': not object_evidence['operation_failures'],
        'verified_runtime': (
            discovered['verified_runtime'] if discovered else None),
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
    parser.add_argument('--port', type=int, default=15306)
    parser.add_argument('--http-port', type=int, default=15099)
    parser.add_argument('--vtctld-server', default='localhost:15999')
    parser.add_argument('--vtctldclient-path', type=Path, required=True)
    parser.add_argument('--compose-dir', type=Path, required=True)
    parser.add_argument(
        '--docker-network', default='cdeadmin-demo-vitess_default')
    parser.add_argument(
        '--backup-volume', default='cdeadmin-demo-vitess_vitess_backups')
    parser.add_argument('--image', default='vitess/lite:v23.0.3')
    parser.add_argument('--source-keyspace', default='test_keyspace')
    parser.add_argument('--baseline-object-evidence', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--object-output', type=Path, required=True)
    args = parser.parse_args(argv)
    result = verify(args)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, indent=2, sort_keys=True) + '\n',
        encoding='utf-8',
    )
    args.object_output.parent.mkdir(parents=True, exist_ok=True)
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
