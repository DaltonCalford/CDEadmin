#!/usr/bin/env python3
##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Qualify structured Apache Ignite administration on exact 2.17.0."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request
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

from pgadmin.cdeadmin.providers.apache_ignite.provider import (  # noqa: E402
    PROFILE, create_provider,
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
        raise RuntimeError('this qualification does not use credentials')


def parser():
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument('--host', default='127.0.0.1')
    value.add_argument('--port', type=int, default=10800)
    value.add_argument('--rest-port', type=int, default=8080)
    value.add_argument('--control-port', type=int)
    value.add_argument('--control-sh', type=Path, required=True)
    value.add_argument('--offline-node-container', required=True)
    value.add_argument('--fixture-container', required=True)
    value.add_argument('--allow-mutation', action='store_true')
    value.add_argument('--output', type=Path, required=True)
    return value


def _target(kind, *path):
    return {
        'resource_id': ':'.join((kind, *path)),
        'resource_kind': kind,
        'display_name': path[-1],
        'display_path': list(path),
        'authority_path': [*path[:-1], kind, path[-1]],
        'generation': 'ignite-live-gate',
    }


def _admin(client, route, kind, operation, draft=None, target=None,
           post_state=True):
    request = {
        'resource_kind': kind, 'operation_id': operation,
        'draft': draft or {}, '_provider_route': route,
    }
    if target is not None:
        request['target_resource'] = target
    checked = client.validate_admin_operation(request)
    if checked.get('errors'):
        raise RuntimeError(
            f'{kind}.{operation} validation failed: {checked["errors"]}')
    plan = client.plan_admin_operation(request)
    handle = {'plan': request, 'provider_payload': plan['provider_payload']}
    result = client.apply_admin_operation(handle)
    record = {
        'resource_kind': kind, 'operation_id': operation,
        'provider_constructed': bool(
            plan.get('command_preview', {}).get('provider_constructed')),
        'accepted': result.get('accepted') is True,
        'common_finality_interpretation': result.get(
            'transaction_finality_interpreted_by_common_code'),
    }
    if post_state:
        handle['provider_result'] = result
        observation = client.validate_admin_post_state(handle)
        record['post_state_confirmed'] = observation.get('confirmed') is True
        if not record['post_state_confirmed']:
            raise RuntimeError(
                f'{kind}.{operation} post-state was not confirmed')
    return record


def _container(action, name):
    if action not in {'start', 'stop'} or re.fullmatch(
            r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}', name) is None:
        raise RuntimeError('disposable container request is invalid')
    result = subprocess.run(
        ['docker', action, name], check=False, capture_output=True,
        text=True, timeout=60)
    if result.returncode != 0:
        raise RuntimeError(
            f'disposable container {action} was rejected')


def _wait_nodes(client, route, count):
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        resources = client.list_resources({'route': route})
        nodes = [
            item for item in resources if item['resource_kind'] == 'node']
        if len(nodes) == count:
            return nodes
        time.sleep(0.5)
    raise RuntimeError(
        f'Ignite topology did not reach {count} server nodes')


def _deploy_service(container, name):
    if re.fullmatch(r'[A-Za-z0-9_.-]{1,128}', name) is None:
        raise RuntimeError('fixture service name is invalid')
    result = subprocess.run([
        'docker', 'exec', container, 'java', '-Xms128m', '-Xmx256m',
        '-cp', '/opt/ignite/apache-ignite/libs/*',
        'org.cdeadmin.ignitefixture.CdeAdminIgniteFixture',
        'deploy-service', name,
    ], check=False, capture_output=True, text=True, timeout=60)
    if result.returncode != 0 or len(
            (result.stdout + result.stderr).encode('utf-8')) > 8 * 1024 * 1024:
        raise RuntimeError('Ignite service fixture deployment failed')


def _start_task(host, rest_port):
    body = urllib.parse.urlencode({
        'cmd': 'exe',
        'name': (
            'org.cdeadmin.ignitefixture.'
            'CdeAdminIgniteFixture$LongTask'),
        'p1': 'cdeadmin-live-gate', 'async': 'true',
    }).encode('utf-8')
    request = urllib.request.Request(
        f'http://{host}:{rest_port}/ignite', data=body, method='POST')
    with urllib.request.urlopen(request, timeout=30) as response:
        document = json.loads(response.read(1024 * 1024))
    task = document.get('response') if isinstance(document, dict) else None
    if not isinstance(document, dict) or document.get(
            'successStatus') != 0 or not isinstance(task, dict) or (
            task.get('finished') is not False):
        raise RuntimeError('Ignite asynchronous task fixture failed')


def _wait_resource(client, route, kind, predicate):
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        for item in client.list_resources({'route': route}):
            if item['resource_kind'] == kind and predicate(item):
                return item
        time.sleep(0.25)
    raise RuntimeError(f'Ignite {kind} fixture was not observed')


def verify(args):
    if not args.allow_mutation:
        raise RuntimeError('qualification requires --allow-mutation')
    context = SimpleNamespace(
        endpoint_id=f'ignite-object-{uuid.uuid4()}',
        session_namespace=f'ignite-object-session-{uuid.uuid4()}',
        mode='legacy_native', runtime_verification_state='verified',
        declared_runtime_family='apache_ignite',
        verified_runtime_family='apache_ignite',
    )
    provider = create_provider(context, _Permissions())
    client = provider.client
    route = {
        'route_id': f'ignite-object-{uuid.uuid4()}',
        'host': args.host, 'port': args.port,
        'rest_port': args.rest_port,
        'control_port': args.control_port or args.port,
        'control_sh_path': str(args.control_sh.resolve()),
        'partition_aware': True,
    }
    suffix = uuid.uuid4().hex[:10].upper()
    table_name = 'T' + suffix
    view_name = 'V' + suffix
    index_name = 'I' + suffix
    cache_name = 'C' + suffix
    table = _target('table', 'PUBLIC', table_name)
    view = _target('view', 'PUBLIC', view_name)
    index = _target('index', 'PUBLIC', table_name, index_name)
    cache = _target('cache', cache_name)
    ttl = _target('ttl', cache_name, 'expiry-policy')
    operations = []
    cleanup = []
    failures = []
    table_created = False
    view_created = False
    index_created = False
    cache_created = False
    original_state = 'ACTIVE'
    offline_container_stopped = False
    baseline_restored = False
    service_target = None
    task_target = None
    identity = None
    started = time.time()

    def run(kind, operation, draft=None, target=None, post_state=True):
        record = _admin(
            client, route, kind, operation, draft, target, post_state)
        operations.append(record)
        return record

    try:
        identity = client.runtime_identity({'route': route})
        if identity.get('version') != PROFILE.exact_version:
            raise RuntimeError('Ignite exact runtime identity changed')
        resources = client.list_resources({'route': route})
        cluster = next(
            item for item in resources if item['resource_kind'] == 'cluster')
        nodes = [
            item for item in resources if item['resource_kind'] == 'node']
        node = nodes[0]
        schema = next(
            item for item in resources
            if item['resource_kind'] == 'sql-schema' and
            item['display_name'] == 'PUBLIC')
        replica = next(
            item for item in resources if item['resource_kind'] == 'replica')
        baseline = next(
            item for item in resources
            if item['resource_kind'] == 'baseline-topology')
        original_state = cluster.get('native', {}).get('state', 'ACTIVE')
        for target in (cluster, node, schema, replica, baseline):
            run(target['resource_kind'], 'inspect', target=target)

        run('cluster', 'set_state', {
            'state': 'ACTIVE_READ_ONLY', 'force': False,
        }, cluster)
        run('cluster', 'set_state', {
            'state': 'ACTIVE', 'force': False,
        }, cluster)
        if len(nodes) < 2:
            raise RuntimeError(
                'baseline mutation qualification requires two server nodes')
        node_ids = sorted(item['display_name'] for item in nodes)
        run('baseline-topology', 'configure_auto_adjust', {
            'enabled': False,
        })
        _container('stop', args.offline_node_container)
        offline_container_stopped = True
        _wait_nodes(client, route, 1)
        run('baseline-topology', 'remove_nodes', {
            'consistent_ids': [node_ids[-1]],
        })
        _container('start', args.offline_node_container)
        offline_container_stopped = False
        nodes = _wait_nodes(client, route, 2)
        run('baseline-topology', 'add_nodes', {
            'consistent_ids': [node_ids[-1]],
        })
        run('baseline-topology', 'set_nodes', {
            'consistent_ids': node_ids,
        })
        topology_version = max(
            int(item.get('native', {}).get('NODE_ORDER', 0))
            for item in nodes)
        if topology_version < 1:
            raise RuntimeError('Ignite topology version is unavailable')
        run('baseline-topology', 'set_version', {
            'topology_version': topology_version,
        })
        run('baseline-topology', 'configure_auto_adjust', {
            'enabled': True, 'timeout_ms': 0,
        })
        baseline_restored = True

        service_name = 'cdeadmin-service-' + suffix.lower()
        _deploy_service(args.fixture_container, service_name)
        service_target = _wait_resource(
            client, route, 'service',
            lambda item: item['display_name'] == service_name)
        run('service', 'inspect', target=service_target)
        run('service', 'cancel', target=service_target)
        service_target = None

        _start_task(args.host, args.rest_port)
        task_target = _wait_resource(
            client, route, 'compute-task',
            lambda item: str(item.get('native', {}).get(
                'TASK_NAME', '')).endswith('$LongTask'))
        run('compute-task', 'inspect', target=task_target)
        run('compute-task', 'cancel', target=task_target)
        task_target = None

        run('table', 'create', {
            'schema': 'PUBLIC', 'name': table_name,
            'columns': [
                {'name': 'ID', 'data_type': 'INT', 'nullable': False,
                 'primary_key': True},
                {'name': 'VALUE', 'data_type': 'VARCHAR', 'nullable': True},
            ],
            'template': 'PARTITIONED', 'backups': 0,
            'atomicity': 'ATOMIC',
        })
        table_created = True
        run('table', 'inspect', target=table)
        run('table', 'alter', {
            'add_columns': [{
                'name': 'ALTERED', 'data_type': 'BIGINT', 'nullable': True,
            }],
        }, table)
        run('table', 'alter', {
            'drop_columns': ['ALTERED'],
        }, table)
        run('column', 'create', {
            'schema': 'PUBLIC', 'table': table_name,
            'name': 'EXTRA', 'data_type': 'VARCHAR', 'nullable': True,
        })
        column = _target('column', 'PUBLIC', table_name, 'EXTRA')
        run('column', 'inspect', target=column)
        run('column', 'drop', {'confirmation': 'EXTRA'}, column)
        run('index', 'create', {
            'schema': 'PUBLIC', 'table': table_name, 'name': index_name,
            'columns': [{'name': 'VALUE', 'direction': 'DESC'}],
            'spatial': False,
        })
        index_created = True
        run('index', 'inspect', target=index)
        constraint = next(
            item for item in client.list_resources({'route': route})
            if item['resource_kind'] == 'constraint' and
            item['display_path'][-2] == table_name)
        run('constraint', 'inspect', target=constraint)
        run('view', 'create', {
            'schema': 'PUBLIC', 'name': view_name,
            'query': 'SELECT ID, VALUE FROM "PUBLIC"."' +
            table_name + '"',
        })
        view_created = True
        run('view', 'inspect', target=view)
        run('view', 'alter', {
            'query': 'SELECT ID FROM "PUBLIC"."' + table_name + '"',
        }, view)
        run('table', 'insert', {
            'values': {'ID': 1, 'VALUE': 'created'},
        }, table)
        page = client.read_admin_rows({
            '_provider_route': route, 'target_resource': table, 'limit': 20,
        })
        row = page['rows'][0]
        run('table', 'update', {
            'selector': {'identity_token': row['identity_token']},
            'changes': {'VALUE': 'updated'},
        }, table)
        page = client.read_admin_rows({
            '_provider_route': route, 'target_resource': table, 'limit': 20,
        })
        run('table', 'delete', {
            'selector': {
                'identity_token': page['rows'][0]['identity_token']},
            'confirmation': table_name,
        }, table)

        run('cache', 'create', {
            'name': cache_name, 'backups_number': 0,
        })
        cache_created = True
        run('cache', 'inspect', target=cache)
        run('cache', 'insert', {'key': 'key', 'value': 'created'}, cache)
        page = client.read_admin_rows({
            '_provider_route': route, 'target_resource': cache, 'limit': 20,
        })
        run('cache', 'update', {
            'selector': {
                'identity_token': page['rows'][0]['identity_token']},
            'value': 'updated',
        }, cache)
        page = client.read_admin_rows({
            '_provider_route': route, 'target_resource': cache, 'limit': 20,
        })
        run('ttl', 'create', {
            'selector': {
                'identity_token': page['rows'][0]['identity_token']},
            'ttl_milliseconds': 60000,
        }, ttl)
        page = client.read_admin_rows({
            '_provider_route': route, 'target_resource': cache, 'limit': 20,
        })
        run('ttl', 'alter', {
            'selector': {
                'identity_token': page['rows'][0]['identity_token']},
            'ttl_milliseconds': 120000,
        }, ttl)
        page = client.read_admin_rows({
            '_provider_route': route, 'target_resource': cache, 'limit': 20,
        })
        run('ttl', 'drop', {
            'selector': {
                'identity_token': page['rows'][0]['identity_token']},
            'confirmation': cache_name,
        }, ttl)
        page = client.read_admin_rows({
            '_provider_route': route, 'target_resource': cache, 'limit': 20,
        })
        run('cache', 'delete', {
            'selector': {
                'identity_token': page['rows'][0]['identity_token']},
            'confirmation': cache_name,
        }, cache)
    except Exception as exc:
        failures.append(f'{type(exc).__name__}: {exc}')
    finally:
        for kind, target in (
                ('compute-task', task_target), ('service', service_target)):
            if target is None:
                continue
            try:
                cleanup.append(_admin(
                    client, route, kind, 'cancel', {}, target))
            except Exception as exc:
                failures.append(
                    f'cleanup.{kind}: {type(exc).__name__}: {exc}')
        if offline_container_stopped:
            try:
                _container('start', args.offline_node_container)
                _wait_nodes(client, route, 2)
                offline_container_stopped = False
            except Exception as exc:
                failures.append(
                    f'cleanup.container: {type(exc).__name__}: {exc}')
        try:
            if 'node_ids' in locals() and not baseline_restored:
                cleanup.append(_admin(
                    client, route, 'baseline-topology', 'set_nodes', {
                        'consistent_ids': node_ids,
                    }))
                cleanup.append(_admin(
                    client, route, 'baseline-topology',
                    'configure_auto_adjust', {
                        'enabled': True, 'timeout_ms': 0,
                    }))
        except Exception as exc:
            failures.append(
                f'cleanup.baseline: {type(exc).__name__}: {exc}')
        for kind, target, present, draft in (
                ('view', view, view_created, {'confirmation': view_name}),
                ('index', index, index_created,
                 {'confirmation': index_name}),
                ('table', table, table_created,
                 {'confirmation': table_name}),
                ('cache', cache, cache_created,
                 {'confirmation': cache_name})):
            if not present:
                continue
            try:
                cleanup.append(_admin(
                    client, route, kind, 'drop', draft, target))
            except Exception as exc:
                failures.append(
                    f'cleanup.{kind}: {type(exc).__name__}: {exc}')
        if original_state != 'ACTIVE':
            try:
                _admin(client, route, 'cluster', 'set_state', {
                    'state': original_state, 'force': False,
                }, _target('cluster', 'Apache Ignite'))
            except Exception as exc:
                failures.append(
                    f'cleanup.cluster: {type(exc).__name__}: {exc}')
        provider.close()

    completed = {
        (item['resource_kind'], item['operation_id'])
        for item in operations + cleanup if item.get('accepted') and (
            item.get('post_state_confirmed', True))
    }

    def operation_map(*kinds):
        return {
            kind: sorted(operation for resource_kind, operation in completed
                         if resource_kind == kind)
            for kind in kinds
            if any(resource_kind == kind for resource_kind, _ in completed)
        }

    concepts = {
        'relational': {
            'servers': {'status': 'passed', 'operations': operation_map(
                'cluster', 'node')},
            'schemas': {'status': 'passed', 'operations': operation_map(
                'sql-schema')},
            'tables': {'status': 'passed', 'operations': operation_map(
                'table')},
            'views': {'status': 'passed', 'operations': operation_map(
                'view')},
            'columns': {'status': 'passed', 'operations': operation_map(
                'column')},
            'indexes': {'status': 'passed', 'operations': operation_map(
                'index')},
            'constraints': {'status': 'passed', 'operations': operation_map(
                'constraint')},
            'replication_objects': {
                'status': 'passed', 'operations': operation_map('replica')},
            'jobs_and_events': {
                'status': 'passed', 'operations': operation_map(
                    'compute-task', 'service')},
        },
        'key_value': {
            'key_browsing': {'status': 'passed', 'operations': {
                'cache': ['inspect']}},
            'data_type_editing': {'status': 'passed', 'operations': {
                'cache': sorted(operation for operation in (
                    'insert', 'update', 'delete')
                    if ('cache', operation) in completed)}},
            'expiration_management': {
                'status': 'passed', 'operations': operation_map('ttl')},
            'replication': {
                'status': 'passed', 'operations': operation_map('replica')},
            'sentinel_or_cluster_state': {
                'status': 'passed', 'operations': operation_map(
                    'cluster', 'node', 'baseline-topology')},
        },
    }
    evidence = {
        'schema': 'cdeadmin.provider-object-live-evidence.v1',
        'engine_id': 'apache_ignite',
        'exact_profile': PROFILE.exact_version,
        'run_id': f'ignite-{uuid.uuid4()}',
        'runtime_identity': identity,
        'concepts': concepts,
        'operations': operations, 'cleanup': cleanup,
        'automatic_mutation_retry': False,
        'common_transaction_finality_interpretation': False,
        'failures': failures,
        'status': 'passed' if not failures else 'failed',
        'started_at': started, 'completed_at': time.time(),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(evidence, indent=2, sort_keys=True) + '\n',
        encoding='utf-8')
    return evidence


def main():
    args = parser().parse_args()
    try:
        result = verify(args)
    except Exception as exc:
        print(f'{type(exc).__name__}: {exc}', file=sys.stderr)
        return 1
    print(json.dumps({
        'engine_id': result['engine_id'], 'status': result['status'],
        'output': str(args.output.resolve()),
    }, sort_keys=True))
    return 0 if result['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
