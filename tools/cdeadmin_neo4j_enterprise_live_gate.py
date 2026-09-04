#!/usr/bin/env python3
##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Qualify Neo4j Enterprise database and cluster-member administration."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path
from types import ModuleType, SimpleNamespace

import neo4j


ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / 'web'
if str(WEB) not in sys.path:
    sys.path.insert(0, str(WEB))
if 'pgadmin' not in sys.modules:
    package = ModuleType('pgadmin')
    package.__path__ = [str(WEB / 'pgadmin')]
    sys.modules['pgadmin'] = package

from pgadmin.cdeadmin.providers.neo4j.client import Neo4jClient  # noqa: E402
from pgadmin.cdeadmin.providers.neo4j.provider import (  # noqa: E402
    Neo4jPilotProvider,
)


EXPECTED_SERVER = '2026.04.0'
EXPECTED_DRIVER = '6.3.0'
EXPECTED_IMAGE_ID = (
    'sha256:18c99b99084b3a21603616ab4e73a884067f87ce3ab6b155f210d716f472ae32'
)
SURFACE_ID = 'neo4j-enterprise-cluster'
REQUIRED_OPERATIONS = {
    'database': {'create', 'inspect', 'alter', 'drop'},
    'server': {'inspect', 'alter', 'execute'},
}


class Lease:
    def __init__(self, value):
        self.value = bytearray(value.encode('utf-8'))

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.value[:] = b'\x00' * len(self.value)

    def use(self, callback):
        return callback(memoryview(self.value))


class Permissions:
    @staticmethod
    def require(_permission, _scope='endpoint'):
        return None

    @staticmethod
    def allows(_permission, _scope='endpoint'):
        return True


def parser():
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument('--endpoint', action='append', required=True)
    value.add_argument('--container', action='append', required=True)
    value.add_argument('--username', default='neo4j')
    value.add_argument(
        '--password-environment', default='CDEADMIN_NEO4J_PASSWORD',
    )
    value.add_argument('--failover-container', required=True)
    value.add_argument('--output', type=Path)
    value.add_argument('--object-evidence', type=Path)
    return value


def _endpoint(value):
    host, separator, port = value.rpartition(':')
    if not separator or not host or not port.isdigit():
        raise ValueError(f'invalid endpoint {value!r}')
    port_value = int(port)
    if not 1 <= port_value <= 65535:
        raise ValueError(f'invalid endpoint port {port_value}')
    return host, port_value


def _docker(*arguments, check=True):
    completed = subprocess.run(
        ['docker', *arguments], text=True, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, check=False, timeout=120,
    )
    if check and completed.returncode:
        raise RuntimeError(
            f'Docker orchestration failed ({completed.returncode}): '
            f'{completed.stdout[-1000:]}'
        )
    return completed.stdout.strip()


def _apply(provider, route, kind, operation, target=None, draft=None):
    plan = provider.plan_visual_admin({
        'resource_kind': kind, 'operation_id': operation,
        'target_resource': target, 'draft': draft or {},
        '_provider_route': route,
    })
    if plan['state'] != 'ready':
        raise RuntimeError(
            f'{kind}.{operation} plan is not ready: {plan.get("blockers")}'
        )
    result = provider.apply_visual_admin({
        'plan_id': plan['plan_id'], 'plan_digest': plan['plan_digest'],
        'confirmed': True,
    })
    if result['transaction_finality_interpreted_by_common_code']:
        raise RuntimeError('common code interpreted Neo4j finality')
    return result


def _find(resources, kind, name=None):
    matches = [
        resource for resource in resources
        if resource['resource_kind'] == kind and (
            name is None or resource['display_name'] == name
        )
    ]
    if len(matches) != 1:
        raise RuntimeError(
            f'expected one {kind} resource for {name!r}, found {len(matches)}'
        )
    return matches[0]


def _native(resource):
    return resource.get('extensions', {}).get('neo4j', {}).get('native', {})


def _server_restore_draft(native):
    allowed = list(native.get('allowedDatabases') or [])
    denied = list(native.get('deniedDatabases') or [])
    if allowed:
        database_filter = 'allow'
        patterns = allowed
    elif denied:
        database_filter = 'deny'
        patterns = denied
    else:
        database_filter = 'any'
        patterns = []
    return {
        'mode_constraint': native.get('modeConstraint') or 'NONE',
        'database_filter': database_filter,
        'database_patterns': patterns,
        'tags': list(native.get('tags') or []),
    }


def _wait_for(provider, route, predicate, label, timeout=180):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        try:
            resources = provider.list_resources({'route': route})
            last = resources
            if predicate(resources):
                return resources
        except Exception as exc:
            last = f'{type(exc).__name__}: {exc}'
        time.sleep(2)
    raise RuntimeError(f'timed out waiting for {label}: {last!r}')


def object_evidence(run_id, image_id, observed, failures=None):
    observed = {
        kind: sorted(set(operations))
        for kind, operations in observed.items()
    }
    missing = {
        kind: sorted(required.difference(observed.get(kind, [])))
        for kind, required in REQUIRED_OPERATIONS.items()
        if required.difference(observed.get(kind, []))
    }
    failures = list(failures or [])
    database_passed = not missing.get('database') and not failures
    server_passed = not missing.get('server') and not failures
    return {
        'schema': 'cdeadmin.provider-object-live-evidence.v1',
        'engine_id': 'neo4j', 'exact_profile': EXPECTED_SERVER,
        'run_id': run_id,
        'evidence_scope': 'neo4j-enterprise-database-cluster-operations',
        'qualification_edition': 'enterprise',
        'surface_id': SURFACE_ID, 'surface_sha256': image_id.removeprefix(
            'sha256:'
        ),
        'concepts': {'graph': {
            'databases': {
                'status': 'passed' if database_passed else 'failed',
                'operations': {'database': observed.get('database', [])},
            },
            'cluster_members': {
                'status': 'passed' if server_passed else 'failed',
                'operations': {'server': observed.get('server', [])},
            },
        }},
        'passed_resource_operations': observed,
        'missing_resource_operations': missing,
        'operation_failures': failures,
        'raw_commands_used_for_provider_operations': False,
        'automatic_mutation_retry': False,
        'common_transaction_finality_interpreted': False,
        'passed': (
            not missing and not failures and image_id == EXPECTED_IMAGE_ID
        ),
    }


def main(argv=None):
    args = parser().parse_args(argv)
    if len(args.endpoint) < 3 or len(args.container) < 3:
        parser().error('at least three endpoints and containers are required')
    if args.failover_container not in args.container:
        parser().error('failover container must be in the qualified set')
    if neo4j.__version__ != EXPECTED_DRIVER:
        raise RuntimeError(
            f'expected driver {EXPECTED_DRIVER}, observed '
            f'{neo4j.__version__}'
        )
    endpoints = [_endpoint(value) for value in args.endpoint]
    password = os.environ.get(args.password_environment)
    if password is None:
        parser().error(f'{args.password_environment} is required')

    image_ids = {
        _docker('inspect', '--format', '{{.Image}}', name)
        for name in args.container
    }
    if image_ids != {EXPECTED_IMAGE_ID}:
        raise RuntimeError(
            f'Enterprise container image identity changed: {image_ids!r}'
        )

    def acquire(*_args):
        return Lease(password)

    route = {
        'host': 'cdeadmin-neo4j-enterprise.invalid', 'port': 7687,
        'resolver_addresses': args.endpoint, 'database': 'neo4j',
        'username': args.username,
        'credential_reference_id': 'neo4j-enterprise-live-credential',
        'principal_reference': 'neo4j-enterprise-live-principal',
        'routing': True, 'tls_mode': 'disabled',
        'connection_timeout': 10, 'connection_acquisition_timeout': 30,
        'max_transaction_retry_time': 0,
    }
    context = SimpleNamespace(
        endpoint_id='neo4j-enterprise-live', mode='legacy_native',
        runtime_verification_state='verified',
        verified_runtime_family='neo4j',
        declared_runtime_family='neo4j',
        effective_permissions=frozenset({
            'network', 'secret_read', 'data_read', 'data_write',
            'administer', 'execute', 'filesystem',
        }),
        session_namespace='neo4j-enterprise-live',
        cache_namespace='neo4j-enterprise-live',
    )
    provider = Neo4jPilotProvider(
        context, Permissions(), Neo4jClient(acquire)
    )
    run_id = 'cdeadmin_neo4j_ee_' + uuid.uuid4().hex[:12]
    database_name = 'cdeadmin-ee-' + uuid.uuid4().hex[:12]
    member_name = run_id + '_member'
    observed = {'database': set(), 'server': set()}
    checks = []
    database_target = None
    member_target = None
    original_member_name = None
    original_member_draft = None
    stopped = False
    try:
        identity = provider.client.runtime_identity({'route': route})
        if identity.get('version') != EXPECTED_SERVER or identity.get(
                'native', {}).get('edition') != 'enterprise':
            raise RuntimeError(
                f'Enterprise runtime identity changed: {identity}'
            )
        resources = _wait_for(
            provider, route,
            lambda values: len([
                item for item in values
                if item['resource_kind'] == 'server' and
                _native(item).get('health') == 'Available'
            ]) >= 3,
            'three available Enterprise cluster members',
        )
        servers = [
            item for item in resources if item['resource_kind'] == 'server'
        ]
        member_target = servers[0]
        original_member_name = member_target['display_name']
        original_member_draft = _server_restore_draft(
            _native(member_target)
        )
        _apply(provider, route, 'server', 'inspect', member_target)
        observed['server'].add('inspect')
        _apply(provider, route, 'server', 'alter', member_target, {
            'mode_constraint': 'NONE', 'database_filter': 'any',
            'tags': ['cdeadmin-enterprise-live'],
        })
        observed['server'].add('alter')
        resources = _wait_for(
            provider, route,
            lambda values: 'cdeadmin-enterprise-live' in _native(_find(
                values, 'server', member_target['display_name']
            )).get('tags', []),
            'cluster-member tag alteration',
        )
        member_target = _find(
            resources, 'server', member_target['display_name']
        )
        _apply(provider, route, 'server', 'execute', member_target, {
            'action': 'rename', 'new_name': member_name,
        })
        observed['server'].add('execute')
        resources = _wait_for(
            provider, route,
            lambda values: any(
                item['resource_kind'] == 'server' and
                item['display_name'] == member_name for item in values
            ),
            'cluster-member rename',
        )
        member_target = _find(resources, 'server', member_name)
        _apply(provider, route, 'server', 'execute', member_target, {
            'action': 'cordon',
        })
        resources = _wait_for(
            provider, route,
            lambda values: _native(_find(
                values, 'server', member_name
            )).get('state') == 'Cordoned',
            'cluster-member cordon',
        )
        member_target = _find(resources, 'server', member_name)
        _apply(provider, route, 'server', 'execute', member_target, {
            'action': 'enable', 'mode_constraint': 'NONE',
            'database_filter': 'any', 'tags': ['cdeadmin-enterprise-live'],
        })
        resources = _wait_for(
            provider, route,
            lambda values: _native(_find(
                values, 'server', member_name
            )).get('state') == 'Enabled',
            'cluster-member enable',
        )
        member_target = _find(resources, 'server', member_name)

        _apply(provider, route, 'database', 'create', draft={
            'name': database_name, 'database_kind': 'standard',
            'default_language': 'CYPHER 25', 'primaries': 3,
            'secondaries': 0, 'store_format': 'block',
            'tx_log_enrichment': 'OFF', 'wait_mode': 'wait',
            'wait_seconds': 120,
        })
        observed['database'].add('create')
        resources = _wait_for(
            provider, route,
            lambda values: any(
                item['resource_kind'] == 'database' and
                item['display_name'] == database_name and
                _native(item).get('currentStatus') == 'online'
                for item in values
            ),
            'created database online',
        )
        database_target = _find(resources, 'database', database_name)
        _apply(provider, route, 'database', 'inspect', database_target)
        observed['database'].add('inspect')
        _apply(provider, route, 'database', 'alter', database_target, {
            'action': 'configure', 'database_kind': 'standard',
            'access': 'read-only', 'default_language': 'unchanged',
            'tx_log_enrichment': 'DIFF', 'wait_mode': 'wait',
            'wait_seconds': 120,
        })
        observed['database'].add('alter')
        resources = _wait_for(
            provider, route,
            lambda values: _native(_find(
                values, 'database', database_name
            )).get('access') == 'read-only',
            'database read-only alteration',
        )
        database_target = _find(resources, 'database', database_name)
        _apply(provider, route, 'database', 'alter', database_target, {
            'action': 'configure', 'database_kind': 'standard',
            'access': 'read-write', 'primaries': 2, 'secondaries': 1,
            'default_language': 'CYPHER 25',
            'tx_log_enrichment': 'FULL', 'wait_mode': 'wait',
            'wait_seconds': 120,
        })
        resources = _wait_for(
            provider, route,
            lambda values: _native(_find(
                values, 'database', database_name
            )).get('access') == 'read-write',
            'database read/write and topology alteration',
        )
        checks.append({
            'check': 'database_lifecycle_and_topology', 'passed': True,
        })
        database_target = _find(resources, 'database', database_name)
        _apply(provider, route, 'database', 'drop', database_target, {
            'data_disposition': 'destroy', 'alias_action': 'restrict',
            'wait_seconds': 120, 'confirmation': database_name,
        })
        observed['database'].add('drop')
        database_target = None
        _wait_for(
            provider, route,
            lambda values: not any(
                item['resource_kind'] == 'database' and
                item['display_name'] == database_name for item in values
            ),
            'database removal',
        )

        _docker('stop', '--time', '30', args.failover_container)
        stopped = True
        failover = Neo4jClient(acquire)
        try:
            failover_identity = failover.runtime_identity({'route': route})
            if failover_identity.get('version') != EXPECTED_SERVER:
                raise RuntimeError('failover endpoint identity changed')
            deadline = time.monotonic() + 60
            available = []
            while time.monotonic() < deadline:
                failover_resources = failover.list_resources({
                    'route': route,
                })
                available = [
                    item for item in failover_resources
                    if item['resource_kind'] == 'server' and
                    item.get('native', {}).get('health') == 'Available'
                ]
                if len(available) >= 2:
                    break
                time.sleep(2)
            if len(available) < 2:
                raise RuntimeError(
                    'routing failover lost cluster quorum: '
                    f'{failover_resources!r}'
                )
        finally:
            failover.close()
        checks.append({
            'check': 'fresh_driver_multi_endpoint_failover', 'passed': True,
            'stopped_container': args.failover_container,
        })
        _docker('start', args.failover_container)
        stopped = False
        _wait_for(
            provider, route,
            lambda values: len([
                item for item in values
                if item['resource_kind'] == 'server' and
                _native(item).get('health') == 'Available'
            ]) >= 3,
            'cluster recovery after failover',
        )
        checks.append({
            'check': 'three_member_cluster_recovery', 'passed': True,
        })
    finally:
        if stopped:
            _docker('start', args.failover_container, check=False)
        if database_target is not None:
            try:
                _apply(provider, route, 'database', 'drop', database_target, {
                    'data_disposition': 'destroy',
                    'alias_action': 'cascade', 'wait_seconds': 120,
                    'confirmation': database_name,
                })
            except Exception:
                pass
        else:
            try:
                resources = provider.list_resources({'route': route})
                leftovers = [
                    item for item in resources
                    if item['resource_kind'] == 'database' and
                    item['display_name'] == database_name
                ]
                if len(leftovers) == 1:
                    _apply(provider, route, 'database', 'drop', leftovers[0], {
                        'data_disposition': 'destroy',
                        'alias_action': 'cascade', 'wait_seconds': 120,
                        'confirmation': database_name,
                    })
            except Exception:
                pass
        if member_target is not None:
            try:
                _apply(
                    provider, route, 'server', 'alter', member_target,
                    original_member_draft or {
                        'mode_constraint': 'NONE',
                        'database_filter': 'any', 'tags': [],
                    },
                )
            except Exception:
                pass
            if original_member_name and member_target.get(
                    'display_name') != original_member_name:
                try:
                    _apply(
                        provider, route, 'server', 'execute', member_target, {
                            'action': 'rename',
                            'new_name': original_member_name,
                        }
                    )
                except Exception:
                    pass
        provider.close()

    evidence = object_evidence(run_id, EXPECTED_IMAGE_ID, observed)
    report = {
        'schema': 'cdeadmin.neo4j-enterprise-live-gate.v1',
        'server_expected': EXPECTED_SERVER,
        'driver_expected': EXPECTED_DRIVER,
        'driver_observed': neo4j.__version__,
        'image_id': EXPECTED_IMAGE_ID,
        'container_count': len(args.container),
        'endpoint_count': len(endpoints),
        'runtime_identity': identity,
        'checks': checks, 'object_evidence': evidence,
        'raw_commands_used_for_provider_operations': False,
        'runtime_orchestration_is_provider_operation': False,
        'passed': evidence['passed'] and all(
            item['passed'] for item in checks
        ),
    }
    rendered = json.dumps(report, indent=2, sort_keys=True) + '\n'
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding='utf-8')
    else:
        sys.stdout.write(rendered)
    if args.object_evidence:
        args.object_evidence.parent.mkdir(parents=True, exist_ok=True)
        args.object_evidence.write_text(
            json.dumps(evidence, indent=2, sort_keys=True) + '\n',
            encoding='utf-8',
        )
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
