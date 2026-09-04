#!/usr/bin/env python3
##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Exact Neo4j 2026.04.0 live qualification gate.

The password is read only from ``CDEADMIN_NEO4J_PASSWORD``. Test objects use
a unique prefix and are removed in ``finally`` blocks. Results distinguish
provider defects from edition- or privilege-dependent unavailable features.
"""

from __future__ import annotations

import argparse
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
EXPECTED_DRIVER = '6.3.0'

FULL_OBJECT_OPERATIONS = {
    'database': ['alter', 'create', 'drop', 'inspect'],
    'node': ['delete', 'insert', 'inspect', 'update'],
    'relationship': ['delete', 'insert', 'inspect', 'update'],
    'label': ['inspect'],
    'constraint': ['create', 'drop', 'inspect'],
    'index': ['create', 'drop', 'inspect'],
    'procedure': ['execute', 'inspect'],
    'transaction': ['execute', 'inspect'],
    'query-plan': ['execute', 'inspect'],
    'graph-projection': ['create', 'drop', 'inspect'],
    'server': ['alter', 'execute', 'inspect'],
}

COMMUNITY_OBJECT_OPERATIONS = {
    'database': ['inspect'],
    'node': ['delete', 'insert', 'inspect', 'update'],
    'relationship': ['delete', 'insert', 'inspect', 'update'],
    'label': ['inspect'],
    'constraint': ['create', 'drop', 'inspect'],
    'index': ['create', 'drop', 'inspect'],
    'procedure': ['execute', 'inspect'],
    'transaction': ['execute', 'inspect'],
    'query-plan': ['execute', 'inspect'],
}


class Lease:
    def __init__(self, value):
        self.value = bytearray(value.encode('utf-8'))

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        for index in range(len(self.value)):
            self.value[index] = 0

    def use(self, callback):
        return callback(memoryview(self.value))


class Permissions:
    def require(self, _permission, _scope='endpoint'):
        return None

    def allows(self, _permission, _scope='endpoint'):
        return True


def parser():
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument('--host', default='127.0.0.1')
    value.add_argument('--port', type=int, default=7687)
    value.add_argument('--database', default='neo4j')
    value.add_argument('--username', default='neo4j')
    value.add_argument(
        '--tls-mode', choices=('disabled', 'system-ca', 'self-signed'),
        default='disabled',
    )
    value.add_argument('--direct', action='store_true')
    value.add_argument('--output', type=Path)
    value.add_argument('--object-evidence', type=Path)
    return value


def observed(label, callback, required=True):
    try:
        detail = callback()
        return {
            'gate': label, 'status': 'passed', 'required': required,
            'detail': detail,
        }
    except Exception as exc:
        return {
            'gate': label,
            'status': 'failed' if required else 'unavailable',
            'required': required,
            'error_type': type(exc).__name__,
            'error': str(exc)[:1000],
        }


def consume(result):
    rows = [record.data() for record in result]
    result.consume()
    return rows


def _apply(provider, request):
    plan = provider.plan_visual_admin(request)
    if plan['state'] != 'ready':
        raise RuntimeError('Neo4j visual administration plan is not ready')
    result = provider.apply_visual_admin({
        'plan_id': plan['plan_id'],
        'plan_digest': plan['plan_digest'],
        'confirmed': True,
    })
    if result['transaction_finality_interpreted_by_common_code']:
        raise RuntimeError('common code interpreted Neo4j finality')
    return result


def _object_evidence(run_id, operations=None, gds_surface_sha256=None):
    operations = {
        kind: sorted(set(values))
        for kind, values in (operations or {}).items()
    }
    concepts = {}
    bindings = {
        'databases': 'database',
        'nodes': 'node',
        'relationships': 'relationship',
        'labels': 'label',
        'constraints': 'constraint',
        'indexes': 'index',
        'procedures': 'procedure',
        'transactions': 'transaction',
        'query_plans': 'query-plan',
        'graph_projections': 'graph-projection',
        'cluster_members': 'server',
    }
    graph = {}
    for concept_id, resource_kind in bindings.items():
        if operations.get(resource_kind):
            graph[concept_id] = {
                'status': 'passed',
                'operations': {
                    resource_kind: operations[resource_kind],
                },
            }
    if not gds_surface_sha256:
        graph.pop('graph_projections', None)
    if graph:
        concepts['graph'] = graph
    missing = {
        kind: sorted(set(expected).difference(operations.get(kind, [])))
        for kind, expected in FULL_OBJECT_OPERATIONS.items()
        if set(expected).difference(operations.get(kind, []))
    }
    passed = not missing and bool(gds_surface_sha256)
    result = {
        'schema': 'cdeadmin.provider-object-live-evidence.v1',
        'engine_id': 'neo4j', 'exact_profile': EXPECTED_SERVER,
        'run_id': run_id,
        'evidence_scope': 'graph-navigator-and-object-editor-operations',
        'raw_commands_used_for_provider_operations': False,
        'common_transaction_finality_interpreted': False,
        'qualification_edition': 'community',
        'passed_resource_operations': operations,
        'operation_failures': missing,
        'external_surface_failures': ([] if gds_surface_sha256 else [
            'neo4j-graph-data-science-plugin',
        ]),
        'concepts': concepts,
        'passed': passed,
    }
    if gds_surface_sha256:
        result.update({
            'surface_id': 'neo4j-graph-data-science-plugin',
            'surface_sha256': gds_surface_sha256,
        })
    return result


def main(argv=None):
    args = parser().parse_args(argv)
    password = os.environ.get('CDEADMIN_NEO4J_PASSWORD')
    if password is None:
        parser().error('CDEADMIN_NEO4J_PASSWORD is required')

    import neo4j

    def acquire(*_args):
        return Lease(password)

    route = {
        'host': args.host, 'port': args.port, 'database': args.database,
        'username': args.username,
        'credential_reference_id': 'live-gate-credential',
        'principal_reference': 'live-gate-principal',
        'routing': not args.direct, 'tls_mode': args.tls_mode,
    }
    adapter = Neo4jClient(acquire)
    prefix = 'cdeadmin_' + uuid.uuid4().hex[:12]
    label = prefix + '_node'
    rel_type = prefix + '_rel'
    index_name = prefix + '_index'
    constraint_name = prefix + '_constraint'
    database_name = prefix + '_database'
    role_name = prefix + '_role'
    gates = []
    passed_visual_operations = {}

    gates.append(observed('driver-version', lambda: {
        'expected': EXPECTED_DRIVER,
        'observed': neo4j.__version__,
        'match': neo4j.__version__ == EXPECTED_DRIVER,
    } if neo4j.__version__ == EXPECTED_DRIVER else (_ for _ in ()).throw(
        RuntimeError(
            f'expected driver {EXPECTED_DRIVER}, observed {neo4j.__version__}'
        )
    )))
    identity = None
    identity_gate = observed(
        'runtime-identity', lambda: adapter.runtime_identity(
            {'route': route}, None
        )
    )
    gates.append(identity_gate)
    if identity_gate['status'] == 'passed':
        identity = identity_gate['detail']
        if identity.get('version') != EXPECTED_SERVER:
            identity_gate.update({
                'status': 'failed',
                'error': f'expected {EXPECTED_SERVER}, observed '
                         f'{identity.get("version")}',
            })

    driver, normalized = adapter._connect({'route': route})
    graph_session = driver.session(database=args.database)
    system_session = driver.session(database='system')
    try:
        def graph_crud():
            consume(graph_session.run(
                f'CREATE (a:`{label}` {{name: $first}}), '
                f'(b:`{label}` {{name: $second}}) '
                f'CREATE (a)-[r:`{rel_type}` {{weight: 1}}]->(b) '
                'RETURN elementId(a) AS a, elementId(b) AS b, '
                'elementId(r) AS r',
                {'first': 'alpha', 'second': 'beta'},
            ))
            consume(graph_session.run(
                f'MATCH (n:`{label}` {{name: $name}}) '
                'SET n.updated = true RETURN n.updated AS updated',
                {'name': 'alpha'},
            ))
            rows = consume(graph_session.run(
                f'MATCH (a:`{label}`)-[r:`{rel_type}`]->(b:`{label}`) '
                'RETURN a.name AS first, b.name AS second, r.weight AS weight'
            ))
            if rows != [{'first': 'alpha', 'second': 'beta', 'weight': 1}]:
                raise RuntimeError('graph CRUD readback differed')
            return {'records': len(rows), 'parameterized_values': True}

        gates.append(observed('graph-crud', graph_crud))

        def schema():
            consume(graph_session.run(
                f'CREATE RANGE INDEX `{index_name}` IF NOT EXISTS '
                f'FOR (n:`{label}`) ON (n.name)'
            ))
            consume(graph_session.run(
                f'CREATE CONSTRAINT `{constraint_name}` IF NOT EXISTS '
                f'FOR (n:`{label}`) REQUIRE n.constraint_key IS UNIQUE'
            ))
            rows = consume(graph_session.run(
                'SHOW INDEXES YIELD name WHERE name IN $names RETURN name',
                {'names': [index_name, constraint_name]},
            ))
            if len(rows) < 2:
                raise RuntimeError('schema objects were not observed')
            return {'observed_names': sorted(row['name'] for row in rows)}

        gates.append(observed('schema-administration', schema))

        def resource_and_result():
            resources = adapter.list_resources({'route': route})
            handle = adapter.open_session({'route': route})
            try:
                token = adapter.execute(handle, {
                    'source': f'MATCH (n:`{label}`) RETURN n',
                    'parameters': {},
                })
                result = adapter.describe_result(token)
                if result['result_kind'] != 'graph':
                    raise RuntimeError('graph result kind was not preserved')
                transaction = adapter.describe_transaction(handle)
                if transaction['common_finality_inference']:
                    raise RuntimeError('common finality inference was enabled')
                return {
                    'resource_kinds': sorted({
                        item['resource_kind'] for item in resources
                    }),
                    'graph_records': len(result['payload']['graphs']),
                }
            finally:
                handle.close()

        gates.append(observed(
            'provider-resource-result-transaction', resource_and_result
        ))

        def visual_object_administration():
            context = SimpleNamespace(
                endpoint_id='neo4j-live-gate', mode='legacy_native',
                runtime_verification_state='verified',
                verified_runtime_family='neo4j',
                declared_runtime_family='neo4j',
                effective_permissions=frozenset({
                    'network', 'secret_read', 'data_read', 'data_write',
                    'administer', 'execute', 'filesystem',
                }),
                session_namespace='neo4j-live-gate',
                cache_namespace='neo4j-live-gate',
            )
            provider = Neo4jPilotProvider(
                context, Permissions(), Neo4jClient(acquire)
            )
            visual_label = label + '_visual'
            held_session = None
            held_transaction = None

            def remember(kind, operation):
                passed_visual_operations.setdefault(kind, set()).add(
                    operation
                )

            def apply(kind, operation, target=None, draft=None):
                result = _apply(provider, {
                    'resource_kind': kind, 'operation_id': operation,
                    'target_resource': target, 'draft': draft or {},
                    '_provider_route': route,
                })
                remember(kind, operation)
                return result

            def find(resources, kind, name=None):
                for resource in resources:
                    if resource['resource_kind'] != kind:
                        continue
                    if name is None or resource['display_name'] == name:
                        return resource
                raise RuntimeError(
                    f'Neo4j {kind} resource was not discovered: {name}'
                )

            def target(kind, name, native):
                return {
                    'resource_id': f'neo4j:{kind}:{name}',
                    'resource_kind': kind, 'display_name': name,
                    'extensions': {'neo4j': {'native': native}},
                }

            try:
                resources = provider.list_resources({'route': route})
                apply(
                    'database', 'inspect',
                    find(resources, 'database', args.database),
                )
                graph_target = find(resources, 'graph', args.database)
                first = apply('node', 'insert', graph_target, {
                    'values': {
                        'labels': [visual_label],
                        'properties': {
                            'name': 'visual-alpha', 'external_id': prefix,
                        },
                    },
                    'options': {},
                })['provider_result']['records'][0]['n']
                second = apply('node', 'insert', graph_target, {
                    'values': {
                        'labels': [visual_label],
                        'properties': {'name': 'visual-beta'},
                    },
                    'options': {},
                })['provider_result']['records'][0]['n']
                first_target = target(
                    'node', first['element_id'], first
                )
                apply('node', 'inspect', first_target)
                apply('node', 'update', first_target, {
                    'selector': {'element_id': first['element_id']},
                    'changes': {'properties': {'reviewed': True}},
                })

                relationship = apply(
                    'relationship', 'insert', graph_target, {
                        'values': {
                            'type': rel_type + '_VISUAL',
                            'start_node_element_id': first['element_id'],
                            'end_node_element_id': second['element_id'],
                            'properties': {'weight': 7},
                        },
                        'options': {},
                    },
                )['provider_result']['records'][0]['r']
                relationship_target = target(
                    'relationship', relationship['element_id'], relationship
                )
                apply('relationship', 'inspect', relationship_target)
                apply('relationship', 'update', relationship_target, {
                    'selector': {
                        'element_id': relationship['element_id'],
                    },
                    'changes': {'properties': {'weight': 8}},
                })

                resources = provider.list_resources({'route': route})
                apply('label', 'inspect', find(
                    resources, 'label', visual_label
                ))
                apply('index', 'create', None, {
                    'name': index_name + '_visual',
                    'options': {
                        'type': 'range', 'entity_type': 'node',
                        'label': visual_label, 'properties': ['name'],
                    },
                })
                apply('constraint', 'create', None, {
                    'name': constraint_name + '_visual',
                    'options': {
                        'type': 'unique', 'entity_type': 'node',
                        'label': visual_label,
                        'properties': ['external_id'],
                    },
                })
                resources = provider.list_resources({'route': route})
                visual_index = find(
                    resources, 'index', index_name + '_visual'
                )
                visual_constraint = find(
                    resources, 'constraint', constraint_name + '_visual'
                )
                apply('index', 'inspect', visual_index)
                apply('constraint', 'inspect', visual_constraint)

                procedure = find(resources, 'procedure', 'db.labels')
                apply('procedure', 'inspect', procedure)
                apply('procedure', 'execute', procedure, {
                    'action': 'execute', 'arguments': [],
                })
                plan_workspace = find(resources, 'query-plan')
                apply('query-plan', 'inspect', plan_workspace)
                plan_result = apply(
                    'query-plan', 'execute', plan_workspace, {
                        'source': 'RETURN $value AS value',
                        'parameters': {'value': 42}, 'mode': 'profile',
                    },
                )
                if not plan_result['provider_result']['summary'].get(
                        'query_plan'):
                    raise RuntimeError('Neo4j query plan was not returned')

                transaction_target = target(
                    'transaction', 'visual-observation',
                    {'transactionId': 'visual-observation'},
                )
                apply('transaction', 'inspect', transaction_target)
                held_session = driver.session(database=args.database)
                held_transaction = held_session.begin_transaction()
                marker = prefix + '_held_transaction'
                held_transaction.run(
                    'UNWIND range(1, 100000000) AS value '
                    f'RETURN value // {marker}'
                )
                active = consume(system_session.run(
                    'SHOW TRANSACTIONS YIELD transactionId, currentQuery '
                    'WHERE currentQuery CONTAINS $marker '
                    'RETURN transactionId', {'marker': marker},
                ))
                if not active:
                    raise RuntimeError(
                        'Neo4j held transaction was not observable'
                    )
                transaction_target = target(
                    'transaction', active[0]['transactionId'], active[0]
                )
                apply('transaction', 'execute', transaction_target, {
                    'action': 'terminate',
                    'arguments': {
                        'transaction_id': active[0]['transactionId'],
                    },
                })

                apply('relationship', 'delete', relationship_target, {
                    'selector': {
                        'element_id': relationship['element_id'],
                    },
                    'confirmation': relationship['element_id'],
                })
                apply('node', 'delete', first_target, {
                    'selector': {'element_id': first['element_id']},
                    'confirmation': first['element_id'],
                })
                apply('index', 'drop', visual_index, {
                    'confirmation': index_name + '_visual',
                })
                apply('constraint', 'drop', visual_constraint, {
                    'confirmation': constraint_name + '_visual',
                })
                observed_operations = {
                    kind: sorted(values)
                    for kind, values in passed_visual_operations.items()
                }
                if observed_operations != COMMUNITY_OBJECT_OPERATIONS:
                    raise RuntimeError(
                        'Neo4j Community visual operation matrix is '
                        f'incomplete: {observed_operations!r}'
                    )
                return {
                    'qualification_edition': 'community',
                    'resource_operation_count': sum(
                        len(values) for values in observed_operations.values()
                    ),
                    'resource_kinds': sorted(observed_operations),
                    'raw_commands_used_for_provider_operations': False,
                    'common_finality_interpreted': False,
                }
            finally:
                if held_transaction is not None:
                    try:
                        held_transaction.close()
                    except Exception:
                        pass
                if held_session is not None:
                    held_session.close()
                try:
                    consume(graph_session.run(
                        f'MATCH (n:`{visual_label}`) DETACH DELETE n'
                    ))
                except Exception:
                    pass
                for statement in (
                    f'DROP INDEX `{index_name}_visual` IF EXISTS',
                    f'DROP CONSTRAINT `{constraint_name}_visual` IF EXISTS',
                ):
                    try:
                        consume(graph_session.run(statement))
                    except Exception:
                        pass
                provider.close()

        gates.append(observed(
            'visual-object-administration', visual_object_administration
        ))

        def bounded_stream_cancellation():
            handle = adapter.open_session({'route': route})
            try:
                token = adapter.execute(handle, {
                    'source': (
                        'UNWIND range(1, 100000) AS item '
                        'RETURN item'
                    ),
                    'parameters': {},
                })
                page = adapter.describe_result(token)
                records = page['payload']['graphs']
                if len(records) != 500 or page['complete']:
                    raise RuntimeError(
                        'bounded streaming did not retain continuation'
                    )
                if not adapter.cancel(token):
                    raise RuntimeError('live result cancellation was refused')
                transaction = adapter.describe_transaction(handle)
                if transaction['common_finality_inference']:
                    raise RuntimeError(
                        'cancellation inferred transaction finality'
                    )
                return {
                    'page_records': len(records),
                    'provider_retained_before_cancel': True,
                    'cancellation_requested': True,
                    'common_finality_inference': False,
                }
            finally:
                handle.close()

        gates.append(observed(
            'bounded-stream-cancellation', bounded_stream_cancellation
        ))

        def database_lifecycle():
            consume(system_session.run(
                f'CREATE DATABASE `{database_name}` IF NOT EXISTS'
            ))
            rows = consume(system_session.run(
                'SHOW DATABASE $name YIELD name, currentStatus '
                'RETURN name, currentStatus', {'name': database_name},
            ))
            if not rows:
                raise RuntimeError('created database was not observed')
            return rows

        gates.append(observed(
            'database-lifecycle', database_lifecycle, required=False
        ))

        def role_lifecycle():
            consume(system_session.run(
                f'CREATE ROLE `{role_name}` IF NOT EXISTS'
            ))
            rows = consume(system_session.run(
                'SHOW ROLES YIELD role WHERE role = $role RETURN role',
                {'role': role_name},
            ))
            if not rows:
                raise RuntimeError('created role was not observed')
            return rows

        gates.append(observed(
            'role-security-lifecycle', role_lifecycle, required=False
        ))
    finally:
        for statement in (
            f'DROP INDEX `{index_name}` IF EXISTS',
            f'DROP CONSTRAINT `{constraint_name}` IF EXISTS',
            f'MATCH (n:`{label}`) DETACH DELETE n',
        ):
            try:
                consume(graph_session.run(statement))
            except Exception:
                pass
        for statement in (
            f'DROP DATABASE `{database_name}` IF EXISTS DESTROY DATA',
            f'DROP ROLE `{role_name}` IF EXISTS',
        ):
            try:
                consume(system_session.run(statement))
            except Exception:
                pass
        graph_session.close()
        system_session.close()
        adapter._forget_driver(driver)
        adapter.close()

    report = {
        'schema': 'cdeadmin.neo4j-live-gate.v1',
        'server_expected': EXPECTED_SERVER,
        'driver_expected': EXPECTED_DRIVER,
        'route': {
            'host': args.host, 'port': args.port,
            'database': args.database, 'tls_mode': args.tls_mode,
            'routing': normalized.get('routing', True),
        },
        'identity': identity,
        'gates': gates,
        'required_passed': all(
            item['status'] == 'passed'
            for item in gates if item['required']
        ),
        'optional_unavailable': [
            item['gate'] for item in gates
            if item['status'] == 'unavailable'
        ],
        'transaction_finality_interpreted_by_common_code': False,
    }
    report['object_experience_evidence'] = _object_evidence(
        f'neo4j-{EXPECTED_SERVER}-{prefix}', passed_visual_operations
    )
    output = json.dumps(report, indent=2, sort_keys=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output + '\n', encoding='utf-8')
    if args.object_evidence:
        args.object_evidence.parent.mkdir(parents=True, exist_ok=True)
        args.object_evidence.write_text(json.dumps(
            report['object_experience_evidence'], indent=2, sort_keys=True,
        ) + '\n', encoding='utf-8')
    print(output)
    return 0 if report['required_passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
