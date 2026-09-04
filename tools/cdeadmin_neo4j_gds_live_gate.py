#!/usr/bin/env python3
##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Qualify the exact Neo4j Graph Data Science projection surface."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
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

from pgadmin.cdeadmin.providers.neo4j.client import Neo4jClient  # noqa: E402
from pgadmin.cdeadmin.providers.neo4j.provider import (  # noqa: E402
    Neo4jPilotProvider,
)


EXPECTED_SERVER = '2026.04.0'
EXPECTED_GDS = '2026.04.0'
SURFACE_ID = 'neo4j-graph-data-science-plugin'


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
    value.add_argument('--host', default='127.0.0.1')
    value.add_argument('--port', type=int, default=7687)
    value.add_argument('--database', default='neo4j')
    value.add_argument('--username')
    value.add_argument(
        '--password-environment', default='CDEADMIN_NEO4J_PASSWORD',
    )
    value.add_argument('--plugin', type=Path, required=True)
    value.add_argument('--output', type=Path)
    value.add_argument('--object-evidence', type=Path)
    return value


def _apply(provider, route, operation, target=None, draft=None):
    plan = provider.plan_visual_admin({
        'resource_kind': 'graph-projection',
        'operation_id': operation, 'target_resource': target,
        'draft': draft or {}, '_provider_route': route,
    })
    if plan['state'] != 'ready':
        raise RuntimeError(
            f'GDS {operation} plan is not ready: {plan.get("blockers")}'
        )
    result = provider.apply_visual_admin({
        'plan_id': plan['plan_id'], 'plan_digest': plan['plan_digest'],
        'confirmed': True,
    })
    if result['transaction_finality_interpreted_by_common_code']:
        raise RuntimeError('common code interpreted Neo4j finality')
    return result


def _records(result):
    rows = [dict(record) for record in result]
    result.consume()
    return rows


def object_evidence(run_id, surface_sha256, observed):
    observed = sorted(set(observed))
    missing = sorted({'create', 'inspect', 'drop'}.difference(observed))
    return {
        'schema': 'cdeadmin.provider-object-live-evidence.v1',
        'engine_id': 'neo4j', 'exact_profile': EXPECTED_SERVER,
        'run_id': run_id,
        'evidence_scope': 'neo4j-gds-graph-projection-operations',
        'surface_id': SURFACE_ID, 'surface_sha256': surface_sha256,
        'concepts': {'graph': {'graph_projections': {
            'status': 'passed',
            'operations': {'graph-projection': observed},
        }}},
        'passed_resource_operations': {'graph-projection': observed},
        'missing_resource_operations': (
            {'graph-projection': missing} if missing else {}
        ),
        'operation_failures': [],
        'raw_commands_used_for_provider_operations': False,
        'automatic_mutation_retry': False,
        'common_transaction_finality_interpreted': False,
        'passed': not missing,
    }


def main(argv=None):
    args = parser().parse_args(argv)
    plugin = args.plugin.resolve()
    raw_plugin = plugin.read_bytes()
    surface_sha256 = hashlib.sha256(raw_plugin).hexdigest()
    password = os.environ.get(args.password_environment)
    if args.username and password is None:
        parser().error(
            f'{args.password_environment} is required with --username'
        )

    def acquire(*_args):
        if password is None:
            raise RuntimeError('qualification secret is unavailable')
        return Lease(password)

    route = {
        'host': args.host, 'port': args.port, 'database': args.database,
        'routing': False, 'tls_mode': 'disabled',
        'auth_mode': 'basic' if args.username else 'none',
    }
    if args.username:
        route.update({
            'username': args.username,
            'credential_reference_id': 'neo4j-gds-live-credential',
            'principal_reference': 'neo4j-gds-live-principal',
        })
    context = SimpleNamespace(
        endpoint_id='neo4j-gds-live', mode='legacy_native',
        runtime_verification_state='verified',
        verified_runtime_family='neo4j',
        declared_runtime_family='neo4j',
        effective_permissions=frozenset({
            'network', 'secret_read', 'data_read', 'data_write',
            'administer', 'execute', 'filesystem',
        }),
        session_namespace='neo4j-gds-live',
        cache_namespace='neo4j-gds-live',
    )
    provider = Neo4jPilotProvider(
        context, Permissions(), Neo4jClient(
            acquire, gds_surface_sha256=surface_sha256,
        ),
    )
    probe = Neo4jClient(acquire, gds_surface_sha256=surface_sha256)
    driver = None
    session = None
    graph_name = 'cdeadmin_gds_' + uuid.uuid4().hex[:12]
    label = 'CDEadminGDS' + uuid.uuid4().hex[:8]
    relationship_type = 'CDEADMIN_GDS_EDGE'
    projection_target = None
    created = False
    observed = set()
    try:
        identity = probe.runtime_identity({'route': route})
        if identity.get('version') != EXPECTED_SERVER:
            raise RuntimeError('Neo4j exact server identity changed')
        driver, _normalized = probe._connect({'route': route})
        session = driver.session(database=args.database)
        version_rows = _records(session.run(
            'RETURN gds.version() AS version'
        ))
        version = version_rows[0].get('version') if version_rows else None
        if version != EXPECTED_GDS:
            raise RuntimeError(
                f'expected GDS {EXPECTED_GDS}, observed {version!r}'
            )
        _records(session.run(
            f'CREATE (a:`{label}` {{id: 1}}), '
            f'(b:`{label}` {{id: 2}}) '
            f'CREATE (a)-[:`{relationship_type}` {{weight: 1}}]->(b)'
        ))
        created_result = _apply(provider, route, 'create', draft={
            'name': graph_name,
            'node_projection': {
                label: {'properties': ['id']},
            },
            'relationship_projection': {
                relationship_type: {
                    'orientation': 'NATURAL',
                    'properties': ['weight'],
                },
            },
            'configuration': {'readConcurrency': 1},
        })
        observed.add('create')
        created = True
        records = created_result['provider_result'].get('records', [])
        if not records or records[0].get('graphName') != graph_name:
            raise RuntimeError('GDS create response did not identify graph')
        projections = [
            resource for resource in provider.list_resources({'route': route})
            if resource['resource_kind'] == 'graph-projection' and
            resource['display_name'] == graph_name
        ]
        if len(projections) != 1:
            raise RuntimeError('GDS projection navigator identity changed')
        projection_target = projections[0]
        inspected = _apply(
            provider, route, 'inspect', target=projection_target,
        )
        observed.add('inspect')
        inspected_rows = inspected['provider_result'].get('records', [])
        if not inspected_rows or inspected_rows[0].get(
                'graphName') != graph_name:
            raise RuntimeError('GDS inspect response did not identify graph')
        _apply(
            provider, route, 'drop', target=projection_target,
            draft={'confirmation': graph_name},
        )
        observed.add('drop')
        created = False
        remaining = _records(session.run(
            'CALL gds.graph.list($name)', {'name': graph_name}
        ))
        if remaining:
            raise RuntimeError('GDS projection remained after drop')
    finally:
        if created and session is not None:
            try:
                _records(session.run(
                    'CALL gds.graph.drop($name, false)', {'name': graph_name}
                ))
            except Exception:
                pass
        if session is not None:
            try:
                _records(session.run(
                    f'MATCH (n:`{label}`) DETACH DELETE n'
                ))
            finally:
                session.close()
        if driver is not None:
            probe._forget_driver(driver)
        probe.close()
        provider.close()
    object_evidence_document = object_evidence(
        graph_name, surface_sha256, observed
    )
    report = {
        'schema': 'cdeadmin.neo4j-gds-live-gate.v1',
        'server_expected': EXPECTED_SERVER,
        'gds_expected': EXPECTED_GDS,
        'driver_expected': '6.3.0',
        'runtime_identity': identity,
        'plugin': {
            'filename': plugin.name, 'size_bytes': len(raw_plugin),
            'sha256': surface_sha256,
        },
        'object_evidence': object_evidence_document,
        'passed': object_evidence_document['passed'],
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
            json.dumps(
                object_evidence_document, indent=2, sort_keys=True
            ) + '\n',
            encoding='utf-8',
        )
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
