#!/usr/bin/env python3
##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Qualify Vitess 23.0.3 visual objects against an exact live topology."""

from __future__ import annotations

import argparse
import json
import secrets
import subprocess
import sys
import time
from pathlib import Path
from types import ModuleType, SimpleNamespace


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

from pgadmin.cdeadmin.providers.vitess.provider import (  # noqa: E402
    PROFILE,
    create_provider,
)
from tools.cdeadmin_relational_provider_live_verify import (  # noqa: E402
    _merge_object_evidence,
    _object_operation_evidence,
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
        raise RuntimeError('Vitess qualification has no credential')


def _run(arguments, *, check=True, timeout=180):
    return subprocess.run(
        arguments, check=check, capture_output=True, text=True,
        timeout=timeout,
    )


def _provider():
    suffix = secrets.token_hex(8)
    context = SimpleNamespace(
        endpoint_id=f'vitess-full-{suffix}',
        session_namespace=f'vitess-full-session-{suffix}',
        mode='legacy_native', runtime_verification_state='verified',
        declared_runtime_family='vitess',
        verified_runtime_family='vitess',
    )
    return create_provider(context, _Permissions())


def _target(kind, path):
    return {
        'resource_id': ':'.join([kind, *path]),
        'resource_kind': kind, 'display_name': path[-1],
        'display_path': path, 'authority_path': [kind, *path],
        'generation': 'vitess-full-live-gate',
    }


def _image_identity(reference):
    document = json.loads(_run([
        'docker', 'image', 'inspect', reference, '--format', '{{json .}}',
    ]).stdout)
    return {
        'requested_reference': reference,
        'image_id': document.get('Id'),
        'repo_digests': sorted(document.get('RepoDigests') or []),
    }


def _client_version(executable, server):
    result = _run([str(executable), '--server', server, '--version'])
    output = (result.stdout + result.stderr).strip()
    if '23.0.3' not in output:
        raise RuntimeError('vtctldclient is not exact version 23.0.3')
    return output


def _wait_primary(executable, server, keyspace, timeout=120):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = _run([
            str(executable), '--server', server, 'GetTablets',
            '--format', 'json', '--keyspace', keyspace,
        ], check=False, timeout=15)
        try:
            rows = json.loads(result.stdout)
        except ValueError:
            rows = []
        for row in rows if isinstance(rows, list) else []:
            if row.get('keyspace') == keyspace and row.get('type') == 1:
                return row
        time.sleep(1)
    raise RuntimeError(f'Vitess keyspace {keyspace} has no primary tablet')


def _wait_shard_primary(
        executable, server, keyspace, shard, timeout=180):
    deadline = time.monotonic() + timeout
    last_error = 'tablet was not present in topology'
    while time.monotonic() < deadline:
        result = _run([
            str(executable), '--server', server, 'GetTablets',
            '--format', 'json', '--keyspace', keyspace,
        ], check=False, timeout=15)
        try:
            rows = json.loads(result.stdout)
        except ValueError:
            rows = []
        for row in rows if isinstance(rows, list) else []:
            if row.get('shard') != shard or row.get('type') != 1:
                continue
            alias = row.get('alias') or {}
            name = f'{alias.get("cell")}-{int(alias.get("uid")):010d}'
            ping = _run([
                str(executable), '--server', server, 'PingTablet', name,
            ], check=False, timeout=15)
            if ping.returncode == 0:
                return row
            last_error = (ping.stderr or ping.stdout).strip()
        time.sleep(1)
    raise RuntimeError(
        f'Vitess shard {keyspace}/{shard} has no responsive primary: '
        f'{last_error}'
    )


def _wait_vtgate(args, keyspace, timeout=120):
    """Wait until VTGate can route a read to the new primary."""
    import mysql.connector

    deadline = time.monotonic() + timeout
    last_error = 'VTGate did not return a response'
    while time.monotonic() < deadline:
        connection = None
        cursor = None
        try:
            connection = mysql.connector.connect(
                host=args.host, port=args.port, user='root',
                database=keyspace, connection_timeout=5,
            )
            cursor = connection.cursor()
            cursor.execute('SHOW TABLES')
            cursor.fetchall()
            return
        except Exception as exc:
            last_error = f'{type(exc).__name__}: {exc}'
        finally:
            if cursor is not None:
                cursor.close()
            if connection is not None:
                connection.close()
        time.sleep(1)
    raise RuntimeError(
        f'Vitess keyspace {keyspace} is not routable: {last_error}'
    )


def _start_tablet(args, container, keyspace, uid, shard='-'):
    if uid % 100 % 3 == 0:
        raise RuntimeError(
            'Vitess compose reserves every third tablet UID for rdonly; '
            f'{uid} cannot bootstrap a primary'
        )
    topology = (
        '--topo-implementation consul --topo-global-server-address '
        'consul1:8500 --topo-global-root vitess/global'
    )
    _run([
        'docker', 'run', '-d', '--name', container,
        '--network', args.docker_network, '-v',
        f'{args.compose_dir.resolve()}:/script',
        '-e', f'TOPOLOGY_FLAGS={topology}', '-e', 'GRPC_PORT=15999',
        '-e', 'WEB_PORT=8080', '-e', 'CELL=test',
        '-e', f'KEYSPACE={keyspace}', '-e', f'SHARD={shard}',
        '-e', 'ROLE=primary', '-e', f'VTHOST={container}',
        '-e', 'EXTERNAL_DB=0', args.image, 'sh', '-c',
        f'/script/vttablet-up.sh {uid}',
    ])


def _remove_container(container):
    return _run(
        ['docker', 'rm', '-f', '-v', container], check=False, timeout=60,
    ).returncode == 0


def _next_primary_uids(start, count):
    values = []
    candidate = start + 1
    while len(values) < count:
        if candidate % 100 % 3 != 0:
            values.append(candidate)
        candidate += 1
    return values


def _online_migrations(provider, route, table):
    def native(item):
        direct = item.get('native')
        if isinstance(direct, dict):
            return direct
        return item.get('extensions', {}).get(
            'vitess', {}).get('native', {})

    return [
        item for item in provider.list_resources({'route': route})
        if item.get('resource_kind') == 'online-ddl' and
        native(item).get('table') == table
    ]


def _wait_online_migration(
        provider, route, table, excluded=(), statuses=(), migration=None,
        timeout=180):
    excluded = set(excluded)
    statuses = {str(value).lower() for value in statuses}
    deadline = time.monotonic() + timeout
    last = []
    while time.monotonic() < deadline:
        last = _online_migrations(provider, route, table)
        for item in reversed(last):
            if migration is not None and item['display_name'] != migration:
                continue
            if item['display_name'] in excluded:
                continue
            native = item.get('extensions', {}).get(
                'vitess', {}).get('native', item.get('native', {}))
            status = str(native.get('status', '')).lower()
            ready = 'ready' in statuses and native.get(
                'ready_to_complete') is True
            if not statuses or status in statuses or ready:
                return item
        time.sleep(1)
    states = [
        (item.get('display_name'), item.get('extensions', {}).get(
            'vitess', {}).get('native', item.get('native', {})).get(
                'status'))
        for item in last
    ]
    raise RuntimeError(
        f'Vitess online DDL did not reach {sorted(statuses)}: {states}'
    )


class _Qualification:
    def __init__(self, provider, route):
        self.provider = provider
        self.route = route
        self.passed = {}
        self.failures = {}
        self.receipts = []

    def record(self, kind, operation):
        self.passed.setdefault(kind, set()).add(operation)

    def apply(self, kind, operation, draft, target=None, *, label=None):
        label = label or f'{kind}.{operation}'
        try:
            request = {
                'resource_kind': kind, 'operation_id': operation,
                'target_resource': target, 'draft': draft,
                '_provider_route': self.route,
            }
            validation = self.provider.validate_visual_admin(request)
            if validation.get('valid') is not True:
                raise RuntimeError(f'validation failed: {validation}')
            plan = self.provider.plan_visual_admin(request)
            result = self.provider.apply_visual_admin({
                'plan_id': plan['plan_id'],
                'plan_digest': plan['plan_digest'], 'confirmed': True,
            })
            if result.get('transaction_finality_interpreted_by_common_code'):
                raise RuntimeError('common code interpreted Vitess finality')
            if result.get('automatic_mutation_retry') is not False:
                raise RuntimeError('Vitess mutation admitted automatic retry')
            operation_id = result['control_operation']['operation_id']
            if result.get('post_state_required'):
                observed = self.provider.validate_visual_admin_post_state({
                    'operation_id': operation_id,
                })
                state = observed.get('post_state') or {}
                if state.get('confirmed') is not True:
                    raise RuntimeError(
                        f'post-state not confirmed: {state.get("reason")}'
                    )
                observation = 'post_state_confirmed'
            else:
                observed = self.provider.refresh_visual_admin_operation({
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

    def inspect(self, kind, name, path):
        label = f'{kind}.inspect'
        try:
            resources = self.provider.list_resources({'route': self.route})
            resource = next(
                item for item in resources
                if item.get('resource_kind') == kind and
                item.get('display_name') == name and
                item.get('display_path') == path
            )
            observed = self.provider.inspect_resource({
                'route': self.route, 'resource_id': resource['resource_id'],
            })
            if observed.get('resource_kind') != kind:
                raise RuntimeError('resource kind changed during inspection')
            self.record(kind, 'inspect')
            print(f'PASS {label}', flush=True)
            return resource
        except Exception as exc:
            self.failures[label] = f'{type(exc).__name__}: {exc}'
            print(f'FAIL {label}: {self.failures[label]}', flush=True)
            return None


def _exercise_online_ddl(provider, qualification, route, keyspace, suffix):
    table = f'cde_online_{suffix}'
    table_target = _target('table', [keyspace, table])
    created = qualification.apply('table', 'create', {
        'name': table, 'parent': keyspace,
        'columns': [{
            'name': 'id', 'type': 'BIGINT', 'nullable': False,
            'primary_key': True,
        }],
        'constraints': [], 'register_in_vschema': False,
    }, label='table.create.online_ddl')
    if created is None:
        return

    def schedule(strategy, column):
        existing = {
            item['display_name']
            for item in _online_migrations(provider, route, table)
        }
        route['init_command'] = f"SET @@ddl_strategy='{strategy}'"
        try:
            altered = qualification.apply('table', 'alter', {
                'add_columns': [{
                    'name': column, 'type': 'INT', 'nullable': True,
                }],
            }, table_target, label=f'table.alter.online_ddl.{column}')
        finally:
            route.pop('init_command', None)
        if altered is None:
            return None
        return _wait_online_migration(
            provider, route, table, excluded=existing)

    try:
        launch = schedule(
            'online --postpone-launch', f'launch_{suffix}')
        if launch is not None:
            migration = launch['display_name']
            target = _target('online-ddl', [keyspace, migration])
            qualification.inspect(
                'online-ddl', migration, [keyspace, migration])
            qualification.apply('online-ddl', 'throttle', {}, target)
            qualification.apply('online-ddl', 'unthrottle', {}, target)
            qualification.apply('online-ddl', 'launch', {}, target)
            _wait_online_migration(
                provider, route, table, statuses=('complete',),
                migration=migration,
                timeout=300,
            )

        retry = schedule(
            'online --postpone-launch', f'retry_{suffix}')
        if retry is not None:
            migration = retry['display_name']
            target = _target('online-ddl', [keyspace, migration])
            qualification.apply('online-ddl', 'cancel', {}, target)
            _wait_online_migration(
                provider, route, table, statuses=('cancelled',),
                migration=migration,
            )
            qualification.apply('online-ddl', 'retry', {}, target)
            _wait_online_migration(
                provider, route, table, statuses=('queued',),
                migration=migration,
            )
            qualification.apply(
                'online-ddl', 'cancel', {}, target,
                label='online-ddl.cancel.retry_cleanup')
            _wait_online_migration(
                provider, route, table, statuses=('cancelled',),
                migration=migration,
            )

        complete = schedule(
            'online --postpone-completion', f'complete_{suffix}')
        if complete is not None:
            migration = complete['display_name']
            target = _target('online-ddl', [keyspace, migration])
            _wait_online_migration(
                provider, route, table, statuses=('ready',),
                migration=migration, timeout=300)
            qualification.apply('online-ddl', 'complete', {}, target)
            _wait_online_migration(
                provider, route, table, statuses=('complete',),
                migration=migration, timeout=300,
            )

        cutover = schedule(
            'online --postpone-completion', f'cutover_{suffix}')
        if cutover is not None:
            migration = cutover['display_name']
            target = _target('online-ddl', [keyspace, migration])
            _wait_online_migration(
                provider, route, table, statuses=('ready',),
                migration=migration, timeout=300)
            qualification.apply(
                'online-ddl', 'force_cutover', {}, target)
            qualification.apply(
                'online-ddl', 'complete', {}, target,
                label='online-ddl.complete.force_cutover')
            _wait_online_migration(
                provider, route, table, statuses=('complete',),
                migration=migration, timeout=300,
            )

        if launch is not None:
            migration = launch['display_name']
            qualification.apply(
                'online-ddl', 'cleanup', {},
                _target('online-ddl', [keyspace, migration]))
    except Exception as exc:
        qualification.failures['online-ddl.lifecycle'] = (
            f'{type(exc).__name__}: {exc}'
        )
        print(
            'FAIL online-ddl.lifecycle: '
            f'{qualification.failures["online-ddl.lifecycle"]}',
            flush=True,
        )
    finally:
        route.pop('init_command', None)
        qualification.apply('table', 'drop', {
            'drop_vschema_registration': False,
            'confirmation': table,
        }, table_target, label='table.drop.online_ddl_cleanup')


def _exercise_reshard(
        args, provider, qualification, route, keyspace, suffix):
    workflow = f'cde_reshard_{suffix}'
    table = f'cde_reshard_table_{suffix}'
    target = _target('workflow', [keyspace, workflow])
    containers = []
    route['database'] = keyspace
    created = qualification.apply('table', 'create', {
        'name': table, 'parent': keyspace,
        'columns': [{
            'name': 'id', 'type': 'BIGINT', 'nullable': False,
            'primary_key': True,
        }],
        'constraints': [], 'register_in_vschema': True,
        'vindex_name': 'xxhash', 'vindex_columns': ['id'],
        'vindex_type': 'xxhash',
    }, label='table.create.reshard_source')
    if created is None:
        return
    try:
        for uid, shard in zip(
                _next_primary_uids(args.tablet_uid, 2), ('-80', '80-')):
            container = (
                f'cdeadmin-vitess-reshard-{suffix}-{uid}'
            )
            _start_tablet(args, container, keyspace, uid, shard)
            containers.append(container)
            _wait_shard_primary(
                args.vtctldclient_path, args.vtctld_server,
                keyspace, shard,
            )
            qualification.apply('shard', 'set_primary_serving', {
                'is_serving': False,
            }, _target('shard', [keyspace, shard]), label=(
                f'shard.set_primary_serving.{shard}'
            ))
        qualification.apply('keyspace', 'rebuild_graph', {
            'cells': [], 'allow_partial': False,
        }, _target('keyspace', [keyspace]))
        result = qualification.apply('workflow', 'create_reshard', {
            'workflow_name': workflow, 'target_keyspace': keyspace,
            'source_shards': ['-'], 'target_shards': ['-80', '80-'],
            'tablet_types': 'primary',
        })
        if result is not None:
            qualification.inspect(
                'workflow', workflow, [keyspace, workflow])
            operation_id = result['control_operation']['operation_id']
            cancelled = provider.cancel_visual_admin_operation({
                'operation_id': operation_id,
            })
            if cancelled.get('stage') != 'cancel_response_recorded':
                raise RuntimeError(
                    'Vitess Reshard cancellation response was not recorded'
                )
            qualification.receipts.append({
                'operation': 'workflow.create_reshard.cancel',
                'observation': cancelled['stage'],
                'automatic_mutation_retry': False,
            })
            print('PASS workflow.create_reshard.cancel', flush=True)
    except Exception as exc:
        qualification.failures['workflow.create_reshard.lifecycle'] = (
            f'{type(exc).__name__}: {exc}'
        )
        print(
            'FAIL workflow.create_reshard.lifecycle: '
            f'{qualification.failures["workflow.create_reshard.lifecycle"]}',
            flush=True,
        )
    finally:
        for container in reversed(containers):
            _remove_container(container)


def _exercise(args, provider, route):
    suffix = secrets.token_hex(4)
    keyspace = f'cde_full_{suffix}'
    sequence = f'cde_seq_{suffix}'
    materialize = f'cde_mat_{suffix}'
    move = f'cde_move_{suffix}'
    traffic = f'cde_traffic_{suffix}'
    traffic_table = f'cde_traffic_table_{suffix}'
    container = f'cdeadmin-vitess-full-{suffix}'
    qualification = _Qualification(provider, route)
    keyspace_target = _target('keyspace', [keyspace])
    tablet_started = False
    keyspace_created = False
    try:
        created = qualification.apply('keyspace', 'create', {
            'name': keyspace, 'durability_policy': 'none',
            'allow_empty_vschema': True,
        })
        keyspace_created = created is not None
        if not keyspace_created:
            return qualification, container
        _start_tablet(args, container, keyspace, args.tablet_uid)
        tablet_started = True
        _wait_primary(args.vtctldclient_path, args.vtctld_server, keyspace)
        _wait_vtgate(args, keyspace)
        route['database'] = keyspace

        sequence_target = _target('sequence', [keyspace, sequence])
        if qualification.apply('sequence', 'create', {
                'name': sequence, 'parent': keyspace,
                'start': 10, 'cache': 50,
        }) is not None:
            qualification.inspect(
                'sequence', sequence, [keyspace, sequence])
            qualification.apply('sequence', 'alter', {
                'restart': 100, 'cache': 25,
            }, sequence_target)
            qualification.apply('sequence', 'drop', {
                'confirmation': sequence,
            }, sequence_target)

        materialize_target = _target(
            'materialize', [keyspace, materialize])
        if qualification.apply('materialize', 'create', {
                'workflow_name': materialize,
                'target_keyspace': keyspace,
                'source_keyspace': args.source_keyspace,
                'table_settings': [{
                    'target_table': args.source_table,
                    'source_expression': f'SELECT * FROM {args.source_table}',
                    'create_ddl': 'copy',
                }],
                'tablet_types': 'primary',
        }) is not None:
            qualification.inspect(
                'materialize', materialize, [keyspace, materialize])
            qualification.inspect(
                'workflow', materialize, [keyspace, materialize])
            resources = provider.list_resources({'route': route})
            stream = next((
                item for item in resources
                if item.get('resource_kind') == 'vreplication-stream' and
                materialize in item.get('display_path', [])
            ), None)
            if stream is None:
                qualification.failures['vreplication-stream.inspect'] = (
                    'CreatedStreamMissing'
                )
            else:
                provider.inspect_resource({
                    'route': route, 'resource_id': stream['resource_id'],
                })
                qualification.record('vreplication-stream', 'inspect')
                print('PASS vreplication-stream.inspect', flush=True)
            qualification.apply(
                'materialize', 'stop', {}, materialize_target)
            qualification.apply(
                'materialize', 'start', {}, materialize_target)
            qualification.apply(
                'materialize', 'drop', {}, materialize_target)

        workflow_target = _target('workflow', [keyspace, move])
        if qualification.apply('workflow', 'create_move_tables', {
                'workflow_name': move, 'target_keyspace': keyspace,
                'source_keyspace': args.source_keyspace,
                'tables': [args.source_table], 'tablet_types': 'primary',
        }) is not None:
            qualification.inspect('workflow', move, [keyspace, move])
            qualification.apply('workflow', 'stop', {}, workflow_target)
            qualification.apply('workflow', 'start', {}, workflow_target)
            qualification.apply('workflow', 'drop', {}, workflow_target)

        _exercise_online_ddl(
            provider, qualification, route, keyspace, suffix)

        route['database'] = args.source_keyspace
        source_table_target = _target(
            'table', [args.source_keyspace, traffic_table])
        target_table_target = _target('table', [keyspace, traffic_table])
        traffic_target = _target('workflow', [keyspace, traffic])
        source_table_created = qualification.apply('table', 'create', {
            'name': traffic_table, 'parent': args.source_keyspace,
            'columns': [{
                'name': 'id', 'type': 'BIGINT', 'nullable': False,
                'primary_key': True,
            }],
            'constraints': [], 'register_in_vschema': True,
            'vindex_name': 'xxhash', 'vindex_columns': ['id'],
        }) is not None
        if source_table_created:
            route['database'] = keyspace
            created = qualification.apply('workflow', 'create_move_tables', {
                'workflow_name': traffic, 'target_keyspace': keyspace,
                'source_keyspace': args.source_keyspace,
                'tables': [traffic_table], 'tablet_types': 'primary',
            }, label='workflow.create_move_tables.traffic')
            if created is not None:
                qualification.apply('workflow', 'switch_traffic', {
                    'workflow_family': 'movetables',
                    'tablet_types': 'primary',
                }, traffic_target)
                qualification.apply('workflow', 'reverse_traffic', {
                    'workflow_family': 'movetables',
                    'tablet_types': 'primary',
                }, traffic_target)
                qualification.apply('workflow', 'switch_traffic', {
                    'workflow_family': 'movetables',
                    'tablet_types': 'all',
                }, traffic_target, label='workflow.switch_traffic.final')
                completed = qualification.apply('workflow', 'complete', {
                    'workflow_family': 'movetables',
                    'keep_data': False, 'keep_routing_rules': False,
                    'rename_tables': False,
                    'ignore_source_keyspace': False,
                }, traffic_target)
                if completed is not None:
                    qualification.apply('table', 'drop', {
                        'drop_vschema_registration': True,
                        'confirmation': traffic_table,
                    }, target_table_target,
                        label='table.drop.traffic_cleanup')
                else:
                    workflows = provider.list_resources({'route': route})
                    present = any(
                        item.get('resource_kind') == 'workflow' and
                        item.get('display_path') == [keyspace, traffic]
                        for item in workflows
                    )
                    if present:
                        qualification.apply('workflow', 'reverse_traffic', {
                            'workflow_family': 'movetables',
                            'tablet_types': 'all',
                        }, traffic_target,
                            label='workflow.reverse_traffic.cleanup')
                        qualification.apply(
                            'workflow', 'drop', {}, traffic_target,
                            label='workflow.drop.traffic_cleanup')
                    route['database'] = args.source_keyspace
                    qualification.apply('table', 'drop', {
                        'drop_vschema_registration': True,
                        'confirmation': traffic_table,
                    }, source_table_target,
                        label='table.drop.traffic_cleanup')
            else:
                route['database'] = args.source_keyspace
                qualification.apply('table', 'drop', {
                    'drop_vschema_registration': True,
                    'confirmation': traffic_table,
                }, source_table_target, label='table.drop.traffic_cleanup')

        _exercise_reshard(
            args, provider, qualification, route, keyspace, suffix)
    finally:
        if tablet_started:
            _remove_container(container)
        if keyspace_created:
            qualification.apply('keyspace', 'drop', {
                'recursive': True, 'force': False,
            }, keyspace_target, label='keyspace.drop.cleanup')
    return qualification, container


def run(args):
    executable = args.vtctldclient_path.expanduser().resolve()
    compose_dir = args.compose_dir.expanduser().resolve()
    if not executable.is_file() or not compose_dir.is_dir():
        raise RuntimeError('Vitess qualification toolchain is incomplete')
    identity = _image_identity(args.image)
    version = _client_version(executable, args.vtctld_server)
    provider = _provider()
    route = {
        'route_id': 'vitess-full-live-gate', 'host': args.host,
        'port': args.port, 'user': 'root',
        'database': args.source_keyspace,
        'vtgate_http_host': args.host,
        'vtgate_http_port': args.http_port,
        'vtgate_http_tls_mode': 'disable',
        'vtctldclient_path': str(executable),
        'vtctld_server': args.vtctld_server,
    }
    discovered = provider.discover_endpoint({'route': route})
    if discovered['verified_runtime']['version'] != PROFILE.exact_version:
        raise RuntimeError('Vitess runtime does not match exact profile')
    started = time.time()
    try:
        qualification, container = _exercise(
            args, provider, route.copy())
        evidence = _object_operation_evidence(
            provider, qualification.passed, PROFILE.engine_id,
            scope='exact-full-vitess-control-plane',
            failures=qualification.failures,
        )
        if args.baseline_object_evidence:
            baseline = json.loads(
                args.baseline_object_evidence.read_text(encoding='utf-8'))
            evidence = _merge_object_evidence(baseline, evidence)
    finally:
        provider.close()
    return {
        'schema': 'cdeadmin.vitess-full-live-verification.v1',
        'engine_id': PROFILE.engine_id,
        'exact_profile': PROFILE.exact_version,
        'activation_ready': not evidence['operation_failures'],
        'verified_runtime': discovered['verified_runtime'],
        'runtime_image': identity, 'vtctldclient_version': version,
        'object_experience_evidence': evidence,
        'operation_receipts': qualification.receipts,
        'automatic_mutation_retry': False,
        'common_transaction_finality_interpretation': False,
        'credential_values_exported': False,
        'owned_container_removed': _run([
            'docker', 'inspect', container,
        ], check=False).returncode != 0,
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
        '--docker-network', default='cdeadmin-vitess-qual_default')
    parser.add_argument('--image', default='vitess/lite:v23.0.3')
    parser.add_argument('--source-keyspace', default='test_keyspace')
    parser.add_argument('--source-table', default='messages')
    parser.add_argument('--tablet-uid', type=int, default=501)
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
    args.object_output.parent.mkdir(parents=True, exist_ok=True)
    args.object_output.write_text(json.dumps(
        result['object_experience_evidence'], indent=2, sort_keys=True,
    ) + '\n', encoding='utf-8')
    print(json.dumps({
        'engine_id': result['engine_id'],
        'activation_ready': result['activation_ready'],
        'operation_failures': result['object_experience_evidence'][
            'operation_failures'],
        'owned_container_removed': result['owned_container_removed'],
    }, indent=2, sort_keys=True))
    return 0 if result['activation_ready'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
