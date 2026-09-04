#!/usr/bin/env python3
##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Qualify the complete TiDB control plane against exact 8.5.6 binaries."""

from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
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

from pgadmin.cdeadmin.providers.tidb.provider import (  # noqa: E402
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
    verify,
)


IMAGES = (
    'pingcap/pd:v8.5.6',
    'pingcap/tikv:v8.5.6',
    'pingcap/tidb:v8.5.6',
    'pingcap/tiflash:v8.5.6',
    'pingcap/ticdc:v8.5.6',
    'pingcap/br:v8.5.6',
)
DEFAULT_TOOLCHAIN = Path(
    '/home/dcalford/Sandbox/cdeadmin_toolchains/tidb-8.5.6'
)


def _run(arguments, *, check=True, timeout=120):
    return subprocess.run(
        arguments, check=check, capture_output=True, text=True,
        timeout=timeout,
    )


def _free_port():
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        return int(listener.getsockname()[1])


def _image_identity(image):
    document = json.loads(_run([
        'docker', 'image', 'inspect', image, '--format', '{{json .}}',
    ]).stdout)
    return {
        'requested_reference': image,
        'image_id': document.get('Id'),
        'repo_digests': sorted(document.get('RepoDigests') or []),
    }


def _tool_version(arguments, label, environment=None):
    result = subprocess.run(
        arguments, check=True, capture_output=True, text=True,
        timeout=120, env=environment,
    )
    output = result.stdout + result.stderr
    admitted = (
        'Release Version: v8.5.6' in output or
        'Release Version:   8.5.6' in output
    )
    if not admitted:
        raise RuntimeError(f'{label} is not the exact 8.5.6 binary')
    return output.strip()


class _Processes:
    def __init__(self, log_dir):
        self.log_dir = log_dir
        self.items = []

    def start(self, name, arguments, environment=None):
        stream = (self.log_dir / f'{name}.log').open('w', encoding='utf-8')
        process = subprocess.Popen(
            arguments, stdout=stream, stderr=subprocess.STDOUT,
            text=True, env=environment,
        )
        self.items.append((name, process, stream))
        return process

    def assert_running(self):
        stopped = [name for name, process, _stream in self.items
                   if process.poll() is not None]
        if stopped:
            raise RuntimeError(
                'TiDB qualification process stopped: ' + ', '.join(stopped)
            )

    def close(self):
        for _name, process, _stream in reversed(self.items):
            if process.poll() is None:
                process.terminate()
        for _name, process, stream in reversed(self.items):
            if process.poll() is None:
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=10)
            stream.close()


def _wait_http(url, processes, timeout=120):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        processes.assert_running()
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if 200 <= response.status < 500:
                    return
        except Exception:
            pass
        time.sleep(0.5)
    raise RuntimeError(f'TiDB service did not become ready: {url}')


def _connect(port, database=None):
    import mysql.connector
    options = {
        'host': '127.0.0.1', 'port': port, 'user': 'root',
        'autocommit': True, 'connection_timeout': 3,
    }
    if database:
        options['database'] = database
    return mysql.connector.connect(**options)


def _wait_sql(port, processes, timeout=180):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        processes.assert_running()
        try:
            connection = _connect(port)
            cursor = connection.cursor()
            cursor.execute('SELECT TIDB_VERSION()')
            value = str(cursor.fetchone()[0])
            connection.close()
            if 'Release Version: v8.5.6' in value:
                return
        except Exception:
            pass
        time.sleep(0.5)
    raise RuntimeError('TiDB SQL endpoint did not become ready')


def _wait_store(pd_port, store_kind, processes, timeout=180):
    deadline = time.monotonic() + timeout
    url = f'http://127.0.0.1:{pd_port}/pd/api/v1/stores'
    while time.monotonic() < deadline:
        processes.assert_running()
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                stores = json.load(response).get('stores', [])
            for item in stores:
                store = item.get('store') or {}
                labels = {
                    value.get('key'): value.get('value')
                    for value in store.get('labels') or []
                }
                engine = labels.get('engine', 'tikv')
                if engine == store_kind and store.get('state_name') == 'Up':
                    return
        except Exception:
            pass
        time.sleep(1)
    raise RuntimeError(f'{store_kind} did not register as an Up store')


def _wait_region_leader(pd_port, processes, timeout=120):
    deadline = time.monotonic() + timeout
    url = f'http://127.0.0.1:{pd_port}/pd/api/v1/regions'
    while time.monotonic() < deadline:
        processes.assert_running()
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                regions = json.load(response).get('regions') or []
            if regions and all((item.get('leader') or {}).get('id')
                               for item in regions):
                return
        except Exception:
            pass
        time.sleep(0.5)
    raise RuntimeError('TiKV regions did not elect usable leaders')


def _write_pd_config(root):
    config = root / 'pd.toml'
    config.write_text(
        '[replication]\nmax-replicas = 1\n', encoding='utf-8'
    )
    return config


def _write_tidb_config(root):
    config = root / 'tidb.toml'
    config.write_text(
        'split-table = false\n\n'
        '[log]\n'
        f'slow-query-file = "{root / "logs" / "tidb-slow.log"}"\n',
        encoding='utf-8',
    )
    return config


def _write_tiflash_config(root, ports):
    learner = root / 'tiflash-learner.toml'
    learner.write_text(
        '[server]\n'
        f'engine-addr = "127.0.0.1:{ports["flash_service"]}"\n\n'
        '[raftstore]\n'
        'apply-pool-size = 2\nstore-pool-size = 2\n'
        'snap-handle-pool-size = 2\n\n'
        '[security]\nredact-info-log = true\n',
        encoding='utf-8',
    )
    config = root / 'tiflash.toml'
    config.write_text(
        'listen_host = "127.0.0.1"\n'
        f'tmp_path = "{root / "tiflash-tmp"}"\n\n'
        '[storage]\n[storage.main]\n'
        f'dir = ["{root / "tiflash-data"}"]\n\n'
        '[flash]\n'
        f'service_addr = "127.0.0.1:{ports["flash_service"]}"\n\n'
        '[flash.proxy]\n'
        f'addr = "127.0.0.1:{ports["flash_proxy"]}"\n'
        f'advertise-addr = "127.0.0.1:{ports["flash_proxy"]}"\n'
        f'status-addr = "127.0.0.1:{ports["flash_status"]}"\n'
        f'advertise-status-addr = "127.0.0.1:{ports["flash_status"]}"\n'
        f'engine-addr = "127.0.0.1:{ports["flash_service"]}"\n'
        f'data-dir = "{root / "tiflash-proxy"}"\n'
        f'config = "{learner}"\n'
        f'log-file = "{root / "logs" / "tiflash-proxy-native.log"}"\n'
        'log-level = "info"\n\n'
        '[logger]\nlevel = "info"\n'
        f'log = "{root / "logs" / "tiflash-native.log"}"\n'
        f'errorlog = "{root / "logs" / "tiflash-error.log"}"\n'
        'size = "10M"\ncount = 3\n\n'
        '[raft]\n'
        f'pd_addr = "127.0.0.1:{ports["pd_client"]}"\n\n'
        '[status]\n'
        f'metrics_port = {ports["flash_metrics"]}\n\n'
        '[security]\nredact_info_log = true\n',
        encoding='utf-8',
    )
    return config


def _start_cluster(root, toolchain, processes):
    ports = {
        name: _free_port() for name in (
            'pd_client', 'pd_peer', 'tikv', 'tikv_status', 'tidb',
            'tidb_status', 'cdc', 'flash_service', 'flash_proxy',
            'flash_status', 'flash_metrics',
        )
    }
    binaries = toolchain / 'bin'
    pd_config = _write_pd_config(root)
    processes.start('pd', [
        str(binaries / 'pd-server'), '--name=pd',
        f'--config={pd_config}',
        f'--data-dir={root / "pd"}',
        f'--client-urls=http://127.0.0.1:{ports["pd_client"]}',
        f'--advertise-client-urls=http://127.0.0.1:{ports["pd_client"]}',
        f'--peer-urls=http://127.0.0.1:{ports["pd_peer"]}',
        f'--advertise-peer-urls=http://127.0.0.1:{ports["pd_peer"]}',
        '--initial-cluster=pd=http://127.0.0.1:' + str(ports['pd_peer']),
    ])
    _wait_http(
        f'http://127.0.0.1:{ports["pd_client"]}/pd/api/v1/version',
        processes,
    )
    processes.start('tikv', [
        str(binaries / 'tikv-server'),
        f'--addr=127.0.0.1:{ports["tikv"]}',
        f'--advertise-addr=127.0.0.1:{ports["tikv"]}',
        f'--status-addr=127.0.0.1:{ports["tikv_status"]}',
        f'--advertise-status-addr=127.0.0.1:{ports["tikv_status"]}',
        f'--pd=127.0.0.1:{ports["pd_client"]}',
        f'--data-dir={root / "tikv"}',
    ])
    _wait_store(ports['pd_client'], 'tikv', processes)
    _wait_http(
        f'http://127.0.0.1:{ports["tikv_status"]}/status', processes,
    )
    _wait_region_leader(ports['pd_client'], processes)
    tidb_config = _write_tidb_config(root)
    processes.start('tidb', [
        str(binaries / 'tidb-server'), '-P', str(ports['tidb']),
        f'--config={tidb_config}',
        '--status', str(ports['tidb_status']), '--store=tikv',
        f'--path=127.0.0.1:{ports["pd_client"]}', '--host=127.0.0.1',
        f'--temp-dir={root / "tidb-tmp"}',
    ])
    _wait_sql(ports['tidb'], processes)
    _sql(
        ports['tidb'],
        'SET GLOBAL tidb_enable_dist_task = OFF',
    )
    _sql(
        ports['tidb'],
        'SET GLOBAL tidb_ddl_enable_fast_reorg = OFF',
    )
    config = _write_tiflash_config(root, ports)
    environment = os.environ.copy()
    environment['LD_LIBRARY_PATH'] = str(toolchain / 'tiflash')
    processes.start('tiflash', [
        str(toolchain / 'tiflash' / 'tiflash'), 'server',
        f'--config-file={config}',
    ], environment)
    _wait_store(ports['pd_client'], 'tiflash', processes, timeout=300)
    processes.start('ticdc', [
        str(binaries / 'cdc'), 'server',
        f'--pd=http://127.0.0.1:{ports["pd_client"]}',
        f'--addr=127.0.0.1:{ports["cdc"]}',
        f'--advertise-addr=http://127.0.0.1:{ports["cdc"]}',
        f'--data-dir={root / "cdc"}',
    ])
    _wait_http(f'http://127.0.0.1:{ports["cdc"]}/status', processes)
    return ports


def _provider(route):
    context = _context(PROFILE)
    secrets_service = EndpointSecretService()
    provider = create_provider(
        context, _permissions(context, secrets_service)
    )
    discovered = provider.discover_endpoint({'route': route})
    provider.close()
    context = _verified_context(context, discovered)
    return create_provider(
        context, _permissions(context, secrets_service)
    )


def _target(kind, path):
    return {
        'resource_id': ':'.join([kind, *path]),
        'resource_kind': kind,
        'display_name': path[-1],
        'display_path': path,
    }


def _apply(provider, route, kind, operation, draft, target=None,
           post_timeout=60):
    plan = provider.plan_visual_admin({
        'resource_kind': kind, 'operation_id': operation,
        'target_resource': target, 'draft': draft,
        '_provider_route': route,
    })
    result = provider.apply_visual_admin({
        'plan_id': plan['plan_id'], 'plan_digest': plan['plan_digest'],
        'confirmed': True,
    })
    operation_id = result['control_operation']['operation_id']
    deadline = time.monotonic() + post_timeout
    while True:
        observed = provider.validate_visual_admin_post_state({
            'operation_id': operation_id,
        })
        if observed['post_state']['confirmed']:
            return result
        if time.monotonic() >= deadline:
            raise RuntimeError(
                f'{kind}.{operation} post-state was not confirmed')
        time.sleep(1)


def _sql(port, source, parameters=(), database=None, fetch=False):
    connection = _connect(port, database)
    cursor = connection.cursor()
    try:
        cursor.execute(source, parameters)
        return cursor.fetchall() if fetch and cursor.description else []
    finally:
        cursor.close()
        connection.close()


def _current_tso(port):
    connection = _connect(port)
    cursor = connection.cursor()
    try:
        cursor.execute('BEGIN')
        cursor.execute('SELECT TIDB_CURRENT_TSO()')
        value = str(cursor.fetchone()[0])
        cursor.execute('ROLLBACK')
        return value
    finally:
        cursor.close()
        connection.close()


def _br(toolchain, pd_port, arguments, timeout=600):
    command = [
        str(toolchain / 'bin' / 'br'), *arguments,
        f'--pd=127.0.0.1:{pd_port}', '--redact-info-log=true',
    ]
    return _run(command, timeout=timeout)


def _wait_log_checkpoint(toolchain, pd_port, task, target, timeout=300):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = _br(toolchain, pd_port, [
            'log', 'status', '--json', f'--task-name={task}',
        ])
        try:
            document = json.loads(result.stdout)
        except ValueError:
            document = None
        rows = document if isinstance(document, list) else [document]
        for row in rows:
            if not isinstance(row, dict):
                continue
            checkpoint = row.get('checkpoint') or row.get('checkpoint-ts')
            try:
                if int(checkpoint) >= int(target):
                    return
            except (TypeError, ValueError):
                pass
        time.sleep(2)
    raise RuntimeError('TiDB log backup did not reach the restore TSO')


def _control_evidence(root, toolchain, ports):
    suffix = secrets.token_hex(4)
    database = f'cde_full_{suffix}'
    table = 'facts'
    policy = f'cde_policy_{suffix}'
    group = f'cde_group_{suffix}'
    changefeed = f'cde-feed-{suffix}'
    log_task = f'cde-log-{suffix}'
    br_root = root / 'br'
    full_uri = f'local://{br_root / "full"}'
    database_uri = f'local://{br_root / "database"}'
    table_uri = f'local://{br_root / "table"}'
    log_uri = f'local://{br_root / "log"}'
    route = {
        'route_id': 'exact-full-live-qualification',
        'host': '127.0.0.1', 'port': ports['tidb'], 'user': 'root',
        'database': 'test', 'principal_reference': 'local-qualification',
        'connection_timeout': 10,
        'br_path': str(toolchain / 'bin' / 'br'),
        'br_pd_addresses': f'127.0.0.1:{ports["pd_client"]}',
        'br_storage_allowlist': [f'local://{br_root}/'],
        'br_timeout_seconds': 600,
        'ticdc_path': str(toolchain / 'bin' / 'cdc'),
        'ticdc_server': f'http://127.0.0.1:{ports["cdc"]}',
        'ticdc_sink_allowlist': ['blackhole://'],
    }
    passed = {}
    failures = {}

    def record(kind, operation):
        passed.setdefault(kind, set()).add(operation)

    def apply(kind, operation, draft, target=None, timeout=60):
        label = f'{kind}.{operation}'
        try:
            value = _apply(
                provider, route, kind, operation, draft, target,
                post_timeout=timeout,
            )
            record(kind, operation)
            print(f'PASS {label}', flush=True)
            return value
        except Exception as exc:
            failures[label] = f'{type(exc).__name__}: {exc}'
            print(f'FAIL {label}: {failures[label]}', flush=True)
            return None

    _br(toolchain, ports['pd_client'], [
        'log', 'start', f'--task-name={log_task}', f'--storage={log_uri}',
    ])
    _sql(ports['tidb'], f'CREATE DATABASE `{database}`')
    _sql(
        ports['tidb'],
        f'CREATE TABLE `{database}`.`{table}` ('
        'id BIGINT PRIMARY KEY, value VARCHAR(255)) '
        'PARTITION BY RANGE(id) ('
        'PARTITION p0 VALUES LESS THAN (1000), '
        'PARTITION p1 VALUES LESS THAN MAXVALUE)',
    )
    _sql(
        ports['tidb'],
        f'INSERT INTO `{database}`.`{table}` VALUES (1, %s)', ('before',),
    )
    provider = _provider(route)
    cluster = _target('cluster', ['TiDB'])
    database_target = _target('database', [database])
    table_target = _target('table', [database, table])
    partition_target = _target('partition', [database, table, 'p0'])
    policy_target = _target('placement-policy', [policy])
    group_target = _target('resource-group', [group])

    apply('placement-policy', 'create', {
        'name': policy, 'followers': 1,
    })
    apply('placement-policy', 'alter', {
        'followers': 1, 'schedule': 'even',
    }, policy_target)
    apply('database', 'configure_placement', {
        'policy_name': policy,
    }, database_target)
    apply('table', 'configure_placement', {
        'policy_name': policy,
    }, table_target)
    apply('partition', 'configure_placement', {
        'policy_name': policy,
    }, partition_target)
    apply('resource-group', 'create', {
        'name': group, 'ru_mode': 'limited', 'ru_per_sec': 100,
        'priority': 'LOW', 'burstable': True,
    })
    apply('resource-group', 'alter', {
        'ru_mode': 'unlimited', 'priority': 'HIGH', 'burstable': False,
    }, group_target)
    apply('table', 'set_tiflash_replica', {
        'replica_count': 1, 'location_labels': [],
    }, table_target, timeout=300)
    apply('database', 'set_tiflash_replica', {
        'replica_count': 1, 'location_labels': [],
    }, database_target, timeout=300)

    created = apply('changefeed', 'create', {
        'changefeed_id': changefeed, 'sink_uri': 'blackhole://',
    }, timeout=120)
    changefeed_target = _target('changefeed', [changefeed])
    if created is not None:
        resources = provider.list_resources({'route': route})
        discovered = next((
            item for item in resources
            if item['resource_kind'] == 'changefeed' and
            item['display_name'] == changefeed
        ), None)
        if discovered is None:
            failures['changefeed.inspect'] = 'CreatedResourceMissing'
        else:
            provider.inspect_resource({
                'route': route, 'resource_id': discovered['resource_id'],
            })
            record('changefeed', 'inspect')
        apply('changefeed', 'pause', {}, changefeed_target)
        apply('changefeed', 'resume', {}, changefeed_target)
        apply('changefeed', 'remove', {}, changefeed_target)

    _sql(
        ports['tidb'],
        f'CREATE TABLE `{database}`.`ddl_work` ('
        'id BIGINT AUTO_INCREMENT PRIMARY KEY, value VARCHAR(255))',
    )
    _sql(
        ports['tidb'],
        f'INSERT INTO `{database}`.`ddl_work`(value) '
        "SELECT REPEAT('x', 200) FROM information_schema.columns a "
        'CROSS JOIN information_schema.columns b LIMIT 150000',
    )
    ddl_outcome = {}

    def run_ddl():
        try:
            _sql(
                ports['tidb'],
                f'ALTER TABLE `{database}`.`ddl_work` '
                'ADD INDEX value_idx(value)',
            )
            ddl_outcome['state'] = 'completed'
        except Exception as exc:
            ddl_outcome['state'] = type(exc).__name__

    ddl_thread = threading.Thread(target=run_ddl)
    ddl_thread.start()
    job_id = None
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline and ddl_thread.is_alive():
        rows = _sql(
            ports['tidb'], 'ADMIN SHOW DDL JOBS 20', fetch=True,
        )
        job_id = next((
            int(row[0]) for row in rows
            if row[1] == database and row[2] == 'ddl_work' and
            str(row[11]).lower() not in {
                'synced', 'done', 'rollback done', 'cancelled', 'canceled',
            }
        ), None)
        if job_id is not None:
            break
        time.sleep(0.05)
    if job_id is None:
        failures['job.cancel'] = 'RunningDDLJobMissing'
    else:
        apply('job', 'cancel', {}, _target('job', [str(job_id)]))
    ddl_thread.join(timeout=120)

    apply('cluster', 'backup_full', {'storage_uri': full_uri}, cluster, 300)
    # BR's snapshot metadata can select a timestamp fractionally ahead of the
    # command return. Establish a later TSO boundary before generating the
    # log-only mutation used to prove point-in-time recovery.
    time.sleep(2)
    _sql(
        ports['tidb'],
        f'INSERT INTO `{database}`.`{table}` VALUES (2, %s)', ('after',),
    )
    target_tso = _current_tso(ports['tidb'])
    _wait_log_checkpoint(
        toolchain, ports['pd_client'], log_task, target_tso,
    )
    _br(toolchain, ports['pd_client'], [
        'log', 'stop', f'--task-name={log_task}',
    ])

    apply('table', 'backup_br', {'storage_uri': table_uri}, table_target, 300)
    _sql(ports['tidb'], f'DROP TABLE `{database}`.`{table}`')
    apply('table', 'restore_br', {'storage_uri': table_uri}, table_target, 300)
    apply(
        'database', 'backup_br', {'storage_uri': database_uri},
        database_target, 300,
    )
    _sql(ports['tidb'], f'DROP DATABASE `{database}`')
    apply(
        'database', 'restore_br', {'storage_uri': database_uri},
        database_target, 300,
    )
    _sql(ports['tidb'], f'DROP DATABASE `{database}`')
    apply('cluster', 'restore_full', {
        'storage_uri': full_uri, 'verification_database': database,
    }, cluster, 300)
    _sql(ports['tidb'], f'DROP DATABASE `{database}`')
    _sql(
        ports['tidb'],
        'DROP DATABASE IF EXISTS '
        '`__TiDB_BR_Temporary_Snapshot_Restore_Checkpoint`',
    )
    _sql(
        ports['tidb'],
        'DROP DATABASE IF EXISTS '
        '`__TiDB_BR_Temporary_Log_Restore_Checkpoint`',
    )
    restored = apply('cluster', 'restore_point', {
        'storage_uri': log_uri, 'full_backup_storage_uri': full_uri,
        'restore_timestamp': target_tso,
        'verification_database': database,
    }, cluster, 600)
    if restored is not None:
        rows = _sql(
            ports['tidb'],
            f'SELECT value FROM `{database}`.`{table}` WHERE id = 2',
            fetch=True,
        )
        if rows != [('after',)]:
            failures['cluster.restore_point.row'] = (
                'RestoredPointInTimeRowMissing'
            )

    tables = _sql(
        ports['tidb'],
        'SELECT TABLE_NAME FROM information_schema.TABLES '
        'WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s',
        (database, table), fetch=True,
    )
    if tables:
        apply('table', 'set_tiflash_replica', {
            'replica_count': 0, 'location_labels': [],
        }, table_target, timeout=120)
        apply('database', 'set_tiflash_replica', {
            'replica_count': 0, 'location_labels': [],
        }, database_target, timeout=120)
    apply('resource-group', 'drop', {}, group_target)
    placement_partitions = _sql(
        ports['tidb'],
        'SELECT TABLE_SCHEMA, TABLE_NAME, PARTITION_NAME FROM '
        'information_schema.PARTITIONS WHERE '
        'TIDB_PLACEMENT_POLICY_NAME = %s AND PARTITION_NAME IS NOT NULL',
        (policy,), fetch=True,
    )
    for schema_name, table_name, partition_name in placement_partitions:
        apply(
            'partition', 'reset_placement', {},
            _target('partition', [schema_name, table_name, partition_name]),
        )
    placement_tables = _sql(
        ports['tidb'],
        'SELECT TABLE_SCHEMA, TABLE_NAME FROM information_schema.TABLES '
        'WHERE TIDB_PLACEMENT_POLICY_NAME = %s',
        (policy,), fetch=True,
    )
    for schema_name, table_name in placement_tables:
        apply(
            'table', 'reset_placement', {},
            _target('table', [schema_name, table_name]),
        )
    placement_databases = _sql(
        ports['tidb'],
        'SELECT SCHEMA_NAME FROM information_schema.SCHEMATA '
        'WHERE TIDB_PLACEMENT_POLICY_NAME = %s',
        (policy,), fetch=True,
    )
    for (schema_name,) in placement_databases:
        apply(
            'database', 'reset_placement', {},
            _target('database', [schema_name]),
        )
    apply('placement-policy', 'drop', {}, policy_target)
    evidence = _object_operation_evidence(
        provider, passed, 'tidb',
        scope='exact-full-distributed-control-plane', failures=failures,
    )
    provider.close()
    return evidence


def run(toolchain, log_output):
    identities = [_image_identity(image) for image in IMAGES]
    tiflash_environment = os.environ.copy()
    tiflash_environment['LD_LIBRARY_PATH'] = str(toolchain / 'tiflash')
    versions = {
        'pd': _tool_version([str(toolchain / 'bin' / 'pd-server'),
                             '--version'], 'PD'),
        'tikv': _tool_version([str(toolchain / 'bin' / 'tikv-server'),
                               '--version'], 'TiKV'),
        'tidb': _tool_version([str(toolchain / 'bin' / 'tidb-server'),
                               '-V'], 'TiDB'),
        'tiflash': _tool_version(
            [str(toolchain / 'tiflash' / 'tiflash'), 'version'], 'TiFlash',
            tiflash_environment,
        ),
        'ticdc': _tool_version([str(toolchain / 'bin' / 'cdc'), 'version'],
                               'TiCDC'),
        'br': _tool_version([str(toolchain / 'bin' / 'br'), '--version'],
                            'BR'),
    }
    runtime_parent = log_output.parent / 'runtime'
    runtime_parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
            prefix='cdeadmin-tidb-full-', dir=runtime_parent) as value:
        runtime = Path(value)
        logs = runtime / 'logs'
        logs.mkdir()
        previous_tmpdir = os.environ.get('TMPDIR')
        os.environ['TMPDIR'] = str(logs)
        for name in (
                'tiflash-data', 'tiflash-proxy', 'tiflash-tmp', 'tidb-tmp',
                'br'):
            (runtime / name).mkdir()
        processes = _Processes(logs)
        try:
            ports = _start_cluster(runtime, toolchain, processes)
            base = verify('tidb', '127.0.0.1', ports['tidb'])
            control = _control_evidence(runtime, toolchain, ports)
            object_evidence = _merge_object_evidence(
                base['object_experience_evidence'], control,
            )
        finally:
            processes.close()
            log_output.mkdir(parents=True, exist_ok=True)
            for source in logs.iterdir():
                if source.is_file():
                    shutil.copy2(source, log_output / source.name)
            if previous_tmpdir is None:
                os.environ.pop('TMPDIR', None)
            else:
                os.environ['TMPDIR'] = previous_tmpdir
        base['object_experience_evidence'] = object_evidence
        base['runtime_images'] = identities
        base['runtime_tool_versions'] = versions
        base['runtime_scope'] = (
            'single-PD single-TiKV TiDB TiFlash TiCDC BR exact topology'
        )
        base['all_processes_stopped'] = True
        base['credential_values_exported'] = False
        base['activation_ready'] = not object_evidence['operation_failures']
        return base


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--toolchain', type=Path, default=DEFAULT_TOOLCHAIN)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--object-output', type=Path, required=True)
    parser.add_argument('--log-dir', type=Path, required=True)
    options = parser.parse_args(argv)
    result = run(options.toolchain.resolve(), options.log_dir.resolve())
    options.output.parent.mkdir(parents=True, exist_ok=True)
    options.output.write_text(
        json.dumps(result, indent=2, sort_keys=True) + '\n', encoding='utf-8'
    )
    options.object_output.parent.mkdir(parents=True, exist_ok=True)
    options.object_output.write_text(
        json.dumps(
            result['object_experience_evidence'], indent=2, sort_keys=True,
        ) + '\n', encoding='utf-8',
    )
    print(json.dumps({
        'engine_id': result['engine_id'],
        'exact_profile': result['exact_profile'],
        'activation_ready': result['activation_ready'],
        'operation_failures': result['object_experience_evidence'][
            'operation_failures'
        ],
        'all_processes_stopped': result['all_processes_stopped'],
    }, indent=2, sort_keys=True))
    return 0 if result['activation_ready'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
