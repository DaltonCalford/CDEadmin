#!/usr/bin/env python3
##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Qualify complete CockroachDB 26.1.3 visual administration."""

from __future__ import annotations

import argparse
import json
import re
import secrets
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

from pgadmin.cdeadmin.providers.cockroachdb.provider import (  # noqa: E402
    PROFILE,
    create_provider,
)


IMAGE = 'cockroachdb/cockroach:v26.1.3'
SQL_PORT = 26257
LOCALITIES = (
    'region=ca-east,zone=ca-east-a',
    'region=ca-central,zone=ca-central-a',
    'region=ca-west,zone=ca-west-a',
    'region=ca-east,zone=ca-east-b',
    'region=ca-west,zone=ca-west-b',
)


class Permissions:
    @staticmethod
    def require(_permission, _scope='endpoint'):
        return None

    @staticmethod
    def allows(_permission, _scope='endpoint'):
        return True

    @staticmethod
    def acquire_secret(*_args):
        raise RuntimeError('CockroachDB insecure gate requested a secret')


def _run(arguments, *, check=True, timeout=300):
    return subprocess.run(
        arguments, check=check, capture_output=True, text=True,
        timeout=timeout,
    )


def _target(kind, *path):
    parts = tuple(str(item) for item in path)
    return {
        'resource_id': ':'.join((kind, *parts)),
        'resource_kind': kind,
        'display_name': parts[-1],
        'display_path': list(parts),
        'authority_path': [*parts[:-1], kind, parts[-1]],
        'generation': 'cockroachdb-full-live-gate',
    }


def _apply(provider, route, kind, operation, draft=None, target=None):
    request = {
        'resource_kind': kind,
        'operation_id': operation,
        'draft': draft or {},
        '_provider_route': route,
    }
    if target is not None:
        request['target_resource'] = target
    validation = provider.validate_visual_admin(request)
    if validation.get('valid') is not True:
        raise RuntimeError(
            f'{kind}.{operation} validation failed: '
            f'{validation.get("errors")}'
        )
    plan = provider.plan_visual_admin(request)
    if plan.get('state') != 'ready' or plan.get(
            'execution_available') is not True:
        raise RuntimeError(f'{kind}.{operation} plan is not executable')
    result = provider.apply_visual_admin({
        'plan_id': plan['plan_id'],
        'plan_digest': plan['plan_digest'],
        'confirmed': True,
    })
    native = result.get('provider_result') or {}
    if native.get('accepted') is not True:
        raise RuntimeError(f'{kind}.{operation} was not accepted')
    if result.get('automatic_mutation_retry') is not False:
        raise RuntimeError(f'{kind}.{operation} admitted automatic retry')
    if result.get('transaction_finality_interpreted_by_common_code'):
        raise RuntimeError(f'{kind}.{operation} used common finality')
    record = {
        'resource_kind': kind,
        'operation_id': operation,
        'provider_constructed': bool(
            plan.get('command_preview', {}).get('provider_constructed')),
        'accepted': True,
        'automatic_mutation_retry': False,
        'post_state_required': bool(result.get('post_state_required')),
    }
    if record['post_state_required']:
        observed = provider.validate_visual_admin_post_state({
            'operation_id': result['control_operation']['operation_id'],
        })
        post_state = observed.get('post_state') or {}
        record['post_state_confirmed'] = post_state.get('confirmed') is True
        if not record['post_state_confirmed']:
            raise RuntimeError(
                f'{kind}.{operation} post-state was not confirmed')
    return record, native


def _connect(host, port, database='defaultdb'):
    import psycopg

    return psycopg.connect(
        host=host, port=port, user='root', dbname=database,
        sslmode='disable', connect_timeout=10, autocommit=True,
    )


def _execute(connection, source, parameters=()):
    with connection.cursor() as cursor:
        cursor.execute(source, parameters)
        return list(cursor.fetchall()) if cursor.description else []


def _resource(client, route, kind, predicate):
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        resources = client.list_resources({'route': route})
        for item in resources:
            if item['resource_kind'] == kind and predicate(item):
                return item
        time.sleep(0.25)
    raise RuntimeError(f'CockroachDB {kind} resource was not observed')


def _job_id(native):
    for statement in native.get('statement_results') or []:
        for row in statement.get('rows') or []:
            if isinstance(row, (list, tuple)) and row:
                try:
                    return int(row[0])
                except (TypeError, ValueError):
                    continue
    raise RuntimeError('CockroachDB operation returned no job identifier')


def _record(passed, operations, record):
    passed.setdefault(record['resource_kind'], set()).add(
        record['operation_id'])
    operations.append(record)


def verify(host, port, cockroach_path):
    run_id = uuid.uuid4().hex[:10]
    standard = f'cdeadmin_standard_{run_id}'
    multi = f'cdeadmin_multi_{run_id}'
    restored = f'cdeadmin_restored_{run_id}'
    table_name = 'items'
    second_table = 'more_items'
    index_name = 'items_value_idx'
    materialized = 'item_totals'
    renamed_materialized = 'item_totals_current'
    schedule_label = f'cdeadmin_schedule_{run_id}'
    backup_uri = f'nodelocal://1/cdeadmin/{run_id}/backup'
    route = {
        'route_id': f'cockroachdb-full-{run_id}',
        'host': host,
        'port': port,
        'user': 'root',
        'database': standard,
        'sslmode': 'disable',
        'autocommit': True,
        'cockroach_path': str(Path(cockroach_path).resolve()),
        'cockroach_insecure': True,
        'cockroach_cli_timeout_seconds': 300,
        'backup_destination_allowlist': [
            f'nodelocal://1/cdeadmin/{run_id}/'],
        'changefeed_sink_allowlist': [
            f'nodelocal://1/cdeadmin/{run_id}/', 'null://'],
    }
    context = SimpleNamespace(
        endpoint_id=f'cockroachdb-full-{run_id}',
        session_namespace=f'cockroachdb-session-{run_id}',
        pool_namespace=f'cockroachdb-pool-{run_id}',
        mode='legacy_native',
        runtime_verification_state='verified',
        declared_runtime_family='cockroachdb',
        verified_runtime_family='cockroachdb',
    )
    provider = create_provider(context, Permissions())
    client = provider.client
    operations = []
    passed = {}
    cleanup = []
    failures = []
    live_changefeeds = set()
    node_in_transition = None
    started = time.time()
    fixture = _connect(host, port)
    original_rangefeed = None

    def apply(kind, operation, draft=None, target=None):
        record, native = _apply(
            provider, route, kind, operation, draft, target)
        _record(passed, operations, record)
        return native

    try:
        identity = client.runtime_identity({'route': route})
        if identity.get('version') != PROFILE.exact_version:
            raise RuntimeError('CockroachDB exact runtime identity changed')
        node_rows = _execute(
            fixture,
            'SHOW CLUSTER SETTING kv.rangefeed.enabled',
        )
        original_rangefeed = bool(node_rows[0][0])
        _execute(fixture, 'SET CLUSTER SETTING kv.rangefeed.enabled = true')
        _execute(fixture, f'DROP DATABASE IF EXISTS "{standard}" CASCADE')
        _execute(fixture, f'DROP DATABASE IF EXISTS "{multi}" CASCADE')
        _execute(fixture, f'DROP DATABASE IF EXISTS "{restored}" CASCADE')
        _execute(fixture, f'CREATE DATABASE "{standard}"')
        _execute(
            fixture,
            f'CREATE TABLE "{standard}".public.{table_name} '
            '(id INT PRIMARY KEY, value STRING)',
        )
        _execute(
            fixture,
            f'CREATE TABLE "{standard}".public.{second_table} '
            '(id INT PRIMARY KEY, value STRING)',
        )
        _execute(
            fixture,
            f'CREATE INDEX {index_name} ON '
            f'"{standard}".public.{table_name}(value)',
        )
        _execute(
            fixture,
            f'ALTER TABLE "{standard}".public.{table_name} '
            'SET (schema_locked = false)',
        )
        _execute(
            fixture,
            f'ALTER TABLE "{standard}".public.{second_table} '
            'SET (schema_locked = false)',
        )

        resources = client.list_resources({'route': route})
        nodes = [item for item in resources if item['resource_kind'] == 'node']
        if len(nodes) < 5 or any(
                item.get('native', {}).get('build') != 'v26.1.3'
                for item in nodes):
            raise RuntimeError(
                'CockroachDB range qualification requires five exact nodes')
        apply('node', 'inspect', target=nodes[0])

        apply('materialized-view', 'create', {
            'name': materialized,
            'parent': 'public',
            'query': f'SELECT count(*) AS total FROM {table_name}',
            'with_data': True,
        })
        materialized_target = _target(
            'materialized-view', 'public', materialized)
        apply('materialized-view', 'inspect', target=materialized_target)
        apply('materialized-view', 'refresh', target=materialized_target)
        apply('materialized-view', 'rename', {
            'new_name': renamed_materialized,
        }, materialized_target)
        materialized_target = _target(
            'materialized-view', 'public', renamed_materialized)
        apply('materialized-view', 'drop', {
            'confirmation': renamed_materialized,
        }, materialized_target)

        database_target = _target('database', standard)
        table_target = _target('table', 'public', table_name)
        index_target = _target(
            'index', 'public', table_name, index_name)
        apply('database', 'configure_zone', {
            'num_replicas': 3, 'gc_ttl_seconds': 3600,
        }, database_target)
        apply('database', 'reset_zone', target=database_target)
        apply('table', 'configure_zone', {
            'num_replicas': 4, 'num_voters': 3,
            'gc_ttl_seconds': 3600,
        }, table_target)
        apply('table', 'scatter', target=table_target)
        apply('index', 'scatter', target=index_target)

        range_resource = _resource(
            client, route, 'range',
            lambda item: len(item.get('native', {}).get(
                'non_voting_replicas') or []) == 1,
        )
        native_range = range_resource['native']
        range_target = _target(
            'range', standard, range_resource['display_name'])
        apply('range', 'inspect', target=range_target)
        lease_source = int(native_range['lease_holder'])
        lease_destination = next(
            int(item) for item in native_range['voting_replicas']
            if int(item) != lease_source)
        apply('range', 'relocate_lease', {
            'destination_store_id': lease_destination,
        }, range_target)
        apply('range', 'relocate_lease', {
            'destination_store_id': lease_source,
        }, range_target)

        node_ids = {int(item['display_name']) for item in nodes}
        native_range = _resource(
            client, route, 'range',
            lambda item: item['display_name'] == (
                range_resource['display_name']),
        )['native']
        occupied = {
            *map(int, native_range['voting_replicas']),
            *map(int, native_range['non_voting_replicas']),
        }
        spare = next(iter(node_ids.difference(occupied)))
        voter_source = int(native_range['voting_replicas'][0])
        apply('range', 'relocate_voter', {
            'source_store_id': voter_source,
            'destination_store_id': spare,
        }, range_target)
        apply('range', 'relocate_voter', {
            'source_store_id': spare,
            'destination_store_id': voter_source,
        }, range_target)

        native_range = _resource(
            client, route, 'range',
            lambda item: item['display_name'] == (
                range_resource['display_name']),
        )['native']
        occupied = {
            *map(int, native_range['voting_replicas']),
            *map(int, native_range['non_voting_replicas']),
        }
        spare = next(iter(node_ids.difference(occupied)))
        nonvoter_source = int(native_range['non_voting_replicas'][0])
        apply('range', 'relocate_nonvoter', {
            'source_store_id': nonvoter_source,
            'destination_store_id': spare,
        }, range_target)
        apply('range', 'relocate_nonvoter', {
            'source_store_id': spare,
            'destination_store_id': nonvoter_source,
        }, range_target)
        apply('table', 'reset_zone', target=table_target)

        _execute(fixture, f'CREATE DATABASE "{multi}"')
        multi_target = _target('database', multi)
        apply('database', 'set_primary_region', {
            'region': 'ca-east'}, multi_target)
        apply('database', 'add_region', {
            'region': 'ca-central'}, multi_target)
        apply('database', 'add_region', {
            'region': 'ca-west'}, multi_target)
        apply('database', 'set_secondary_region', {
            'region': 'ca-central'}, multi_target)
        apply('database', 'drop_secondary_region', target=multi_target)
        apply('database', 'set_secondary_region', {
            'region': 'ca-central'}, multi_target)
        apply('database', 'set_survival_goal', {
            'goal': 'region'}, multi_target)
        apply('database', 'set_survival_goal', {
            'goal': 'zone'}, multi_target)
        apply('database', 'set_placement', {
            'policy': 'restricted'}, multi_target)
        apply('database', 'set_placement', {
            'policy': 'default'}, multi_target)
        apply('database', 'set_primary_region', {
            'region': 'ca-west'}, multi_target)
        apply('database', 'set_primary_region', {
            'region': 'ca-east'}, multi_target)
        apply('database', 'drop_region', {
            'region': 'ca-west'}, multi_target)
        apply('database', 'add_region', {
            'region': 'ca-west'}, multi_target)
        apply('database', 'set_survival_goal', {
            'goal': 'region'}, multi_target)
        _execute(
            fixture,
            f'CREATE TABLE "{multi}".public.regional_items '
            '(id INT PRIMARY KEY, value STRING)',
        )
        _execute(
            fixture,
            f'ALTER TABLE "{multi}".public.regional_items '
            'SET (schema_locked = false)',
        )
        multi_table = _target(
            'table', multi, 'public', 'regional_items')
        apply('table', 'set_locality', {
            'locality': 'global'}, multi_table)
        apply('table', 'set_locality', {
            'locality': 'regional_by_table', 'region': 'ca-central',
        }, multi_table)
        apply('table', 'set_locality', {
            'locality': 'regional_by_row'}, multi_table)

        feed_native = apply('table', 'create_changefeed', {
            'sink_uri': f'nodelocal://1/cdeadmin/{run_id}/cdc-a',
            'initial_scan': 'no', 'format': 'json', 'envelope': 'wrapped',
        }, _target('table', standard, 'public', table_name))
        feed_id = _job_id(feed_native)
        live_changefeeds.add(feed_id)
        changefeed = _target('changefeed', feed_id)
        apply('changefeed', 'inspect', target=changefeed)
        apply('changefeed', 'pause', target=changefeed)
        apply('changefeed', 'add_table', {
            'table_name': f'{standard}.public.{second_table}',
            'initial_scan': 'no',
        }, changefeed)
        apply('changefeed', 'set_sink', {
            'sink_uri': f'nodelocal://1/cdeadmin/{run_id}/cdc-b',
        }, changefeed)
        apply('changefeed', 'drop_table', {
            'table_name': f'{standard}.public.{second_table}',
        }, changefeed)
        apply('changefeed', 'resume', target=changefeed)
        apply('changefeed', 'cancel', target=changefeed)
        live_changefeeds.discard(feed_id)

        job_native = apply('table', 'create_changefeed', {
            'sink_uri': 'null://', 'initial_scan': 'no',
            'format': 'json', 'envelope': 'wrapped',
        }, _target('table', standard, 'public', table_name))
        job_id = _job_id(job_native)
        live_changefeeds.add(job_id)
        job_target = _target('job', job_id)
        apply('job', 'inspect', target=job_target)
        apply('job', 'pause', {'reason': 'CDEadmin live gate'}, job_target)
        apply('job', 'resume', target=job_target)
        apply('job', 'cancel', target=job_target)
        live_changefeeds.discard(job_id)

        schedule_rows = _execute(
            fixture,
            f'CREATE SCHEDULE "{schedule_label}" FOR BACKUP DATABASE '
            f'"{standard}" INTO \'nodelocal://1/cdeadmin/{run_id}/schedule\' '
            "RECURRING '@daily'",
        )
        if not schedule_rows:
            raise RuntimeError('CockroachDB schedule fixture returned no rows')
        schedule_id = int(schedule_rows[0][0])
        schedule_target = _target('schedule', schedule_id)
        apply('schedule', 'inspect', target=schedule_target)
        apply('schedule', 'pause', target=schedule_target)
        apply('schedule', 'resume', target=schedule_target)
        apply('schedule', 'drop', target=schedule_target)

        apply('database', 'backup', {
            'destination_uri': backup_uri,
            'revision_history': 'with_revision_history',
        }, database_target)
        apply('cluster', 'restore_database', {
            'source_uri': backup_uri,
            'database_name': standard,
            'new_database_name': restored,
        }, _target('cluster', 'CockroachDB'))
        restored_rows = _execute(
            fixture,
            'SELECT count(*) FROM [SHOW DATABASES] WHERE database_name = %s',
            (restored,),
        )
        if restored_rows != [(1,)]:
            raise RuntimeError(
                'CockroachDB restored database was not observed')

        node_target = next(
            item for item in nodes if item['display_name'] == '2')
        node_in_transition = 2
        apply('node', 'decommission', {
            'checks': 'enabled', 'wait': 'none'}, node_target)
        apply('node', 'recommission', target=node_target)
        node_in_transition = None
    except Exception as exc:
        failures.append(f'{type(exc).__name__}: {exc}')
    finally:
        for job_id in sorted(live_changefeeds):
            try:
                _execute(fixture, 'CANCEL JOB %s', (job_id,))
                cleanup.append({'changefeed_job': job_id, 'canceled': True})
            except Exception as exc:
                failures.append(
                    f'cleanup.changefeed.{job_id}: '
                    f'{type(exc).__name__}: {exc}')
        if node_in_transition is not None:
            result = _run([
                str(Path(cockroach_path).resolve()), 'node', 'recommission',
                '--insecure', f'--host={host}:{port}',
                str(node_in_transition),
            ], check=False)
            cleanup.append({
                'node_id': node_in_transition,
                'recommissioned': result.returncode == 0,
            })
            if result.returncode != 0:
                failures.append('cleanup.node: recommission failed')
        try:
            companion_schedules = _execute(
                fixture,
                'SELECT id FROM [SHOW SCHEDULES] WHERE label = %s',
                (schedule_label,),
            )
            for (schedule_id,) in companion_schedules:
                _execute(fixture, 'DROP SCHEDULE %s', (schedule_id,))
            cleanup.append({
                'schedule_label': schedule_label,
                'remaining_schedules_dropped': len(companion_schedules),
            })
        except Exception as exc:
            failures.append(
                f'cleanup.schedule: {type(exc).__name__}: {exc}')
        for database in (restored, multi, standard):
            try:
                _execute(
                    fixture,
                    f'DROP DATABASE IF EXISTS "{database}" CASCADE')
                cleanup.append({'database': database, 'absent': True})
            except Exception as exc:
                failures.append(
                    f'cleanup.database.{database}: '
                    f'{type(exc).__name__}: {exc}')
        if original_rangefeed is not None:
            try:
                value = 'true' if original_rangefeed else 'false'
                _execute(
                    fixture,
                    f'SET CLUSTER SETTING kv.rangefeed.enabled = {value}')
            except Exception as exc:
                failures.append(
                    f'cleanup.rangefeed-setting: {type(exc).__name__}: {exc}')
        fixture.close()
        client.close()

    concepts = {
        'servers': {'cluster': {'restore_database'}},
        'databases': {'database': {
            'configure_zone', 'reset_zone', 'set_primary_region',
            'add_region', 'drop_region', 'set_secondary_region',
            'drop_secondary_region', 'set_survival_goal', 'set_placement',
            'backup',
        }},
        'tables': {'table': {
            'configure_zone', 'reset_zone', 'set_locality',
            'create_changefeed', 'scatter',
        }},
        'materialized_views': {'materialized-view': {
            'inspect', 'create', 'rename', 'drop', 'refresh',
        }},
        'indexes': {'index': {'scatter'}},
        'replication_objects': {'changefeed': {
            'inspect', 'pause', 'resume', 'cancel', 'add_table',
            'drop_table', 'set_sink',
        }},
        'jobs_and_events': {
            'job': {'inspect', 'pause', 'resume', 'cancel'},
            'schedule': {'inspect', 'pause', 'resume', 'drop'},
        },
    }
    passed_concepts = {}
    for concept, kinds in concepts.items():
        admitted = {
            kind: sorted(required.intersection(passed.get(kind, set())))
            for kind, required in kinds.items()
        }
        missing = {
            kind: sorted(required.difference(passed.get(kind, set())))
            for kind, required in kinds.items()
            if required.difference(passed.get(kind, set()))
        }
        if missing:
            failures.append(f'coverage.{concept}: {missing}')
        passed_concepts[concept] = {
            'status': 'passed' if not missing else 'failed',
            'operations': admitted,
        }
    return {
        'schema': 'cdeadmin.provider-object-live-evidence.v1',
        'engine_id': 'cockroachdb',
        'exact_profile': PROFILE.exact_version,
        'run_id': f'cockroachdb-full-{run_id}',
        'concepts': {'relational': passed_concepts},
        'passed_resource_operations': {
            kind: sorted(values) for kind, values in sorted(passed.items())
        },
        'operations': operations,
        'cleanup': cleanup,
        'provider_finality_authority': True,
        'automatic_mutation_retry': False,
        'raw_commands_used_by_product': False,
        'fixture_sql_scope': 'isolated qualification setup and cleanup only',
        'failures': failures,
        'status': 'passed' if not failures else 'failed',
        'started_at': started,
        'completed_at': time.time(),
    }


def _published_port(container):
    result = _run(['docker', 'port', container, f'{SQL_PORT}/tcp'])
    match = re.search(r':(\d+)\s*$', result.stdout)
    if match is None:
        raise RuntimeError('CockroachDB SQL port was not published')
    return int(match.group(1))


def _wait_ready(container, port, timeout=180):
    deadline = time.monotonic() + timeout
    initialized = False
    last_connection_error = None
    while time.monotonic() < deadline:
        if not initialized:
            result = _run([
                'docker', 'exec', container, '/cockroach/cockroach',
                'init', '--insecure', f'--host={container}:{SQL_PORT}',
            ], check=False, timeout=30)
            initialized = result.returncode == 0 or (
                'cluster has already been initialized' in (
                    result.stdout + result.stderr).lower())
        if initialized:
            try:
                connection = _connect('127.0.0.1', port)
                connection.close()
                return
            except Exception as exc:
                last_connection_error = type(exc).__name__
        time.sleep(1)
    raise RuntimeError(
        'CockroachDB exact cluster did not become ready '
        f'(last connection result: {last_connection_error})')


def _image_identity(image):
    result = _run([
        'docker', 'image', 'inspect', image, '--format', '{{json .}}'])
    document = json.loads(result.stdout)
    return {
        'requested_reference': image,
        'image_id': document.get('Id'),
        'repo_digests': sorted(document.get('RepoDigests') or []),
    }


def orchestrate(image, cockroach_path):
    suffix = secrets.token_hex(5)
    network = f'cdeadmin-cockroach-full-{suffix}'
    containers = [f'{network}-n{index}' for index in range(1, 6)]
    started = []
    result = None
    _run(['docker', 'network', 'create', network])
    try:
        joins = ','.join(f'{name}:{SQL_PORT}' for name in containers[:3])
        for index, (container, locality) in enumerate(
                zip(containers, LOCALITIES)):
            command = [
                'docker', 'run', '-d', '--name', container,
                '--network', network,
            ]
            if index == 0:
                command.extend(['-p', f'127.0.0.1::{SQL_PORT}'])
            command.extend([
                image, 'start', '--insecure', f'--join={joins}',
                f'--listen-addr=0.0.0.0:{SQL_PORT}',
                f'--advertise-addr={container}:{SQL_PORT}',
                '--http-addr=0.0.0.0:8080', f'--locality={locality}',
                '--store=/cockroach/cockroach-data',
            ])
            _run(command)
            started.append(container)
        port = _published_port(containers[0])
        _wait_ready(containers[0], port)
        result = verify('127.0.0.1', port, cockroach_path)
        result['runtime_image'] = _image_identity(image)
        result['isolated_runtime_container'] = True
        result['exact_cluster_node_count'] = 5
    finally:
        for container in reversed(started):
            _run(['docker', 'rm', '-f', container], check=False, timeout=60)
        _run(['docker', 'network', 'rm', network], check=False, timeout=60)
    if result is None:
        raise RuntimeError('CockroachDB qualification returned no evidence')
    result['containers_removed'] = True
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', default=IMAGE)
    parser.add_argument('--cockroach-path', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    options = parser.parse_args(argv)
    result = orchestrate(options.image, options.cockroach_path)
    options.output.parent.mkdir(parents=True, exist_ok=True)
    options.output.write_text(
        json.dumps(result, indent=2, sort_keys=True, default=str) + '\n',
        encoding='utf-8',
    )
    print(json.dumps({
        'engine_id': result['engine_id'],
        'exact_profile': result['exact_profile'],
        'status': result['status'],
        'operation_count': len(result['operations']),
        'failure_count': len(result['failures']),
        'output': str(options.output.resolve()),
    }, indent=2, sort_keys=True))
    return 0 if result['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
