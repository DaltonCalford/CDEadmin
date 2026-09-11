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
import shutil
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
    'alias': ['alter', 'create', 'drop', 'inspect'],
    'backup': ['execute', 'inspect'],
    'composite-database': ['alter', 'create', 'drop', 'inspect'],
    'consistency-check': ['execute', 'inspect'],
    'constraint': ['create', 'drop', 'inspect'],
    'database': ['alter', 'create', 'drop', 'inspect'],
    'dbms': ['execute', 'inspect'],
    'export': ['execute', 'inspect'],
    'function': ['inspect'],
    'graph': ['insert', 'inspect'],
    'graph-projection': ['create', 'drop', 'inspect'],
    'import': ['execute', 'inspect'],
    'index': ['create', 'drop', 'inspect'],
    'label': ['inspect'],
    'node': ['delete', 'insert', 'inspect', 'update'],
    'privilege': ['grant', 'inspect', 'revoke'],
    'procedure': ['execute', 'inspect'],
    'property': ['inspect'],
    'query': ['execute', 'inspect'],
    'query-plan': ['execute', 'inspect'],
    'relationship': ['delete', 'insert', 'inspect', 'update'],
    'relationship-type': ['inspect'],
    'restore': ['execute', 'inspect'],
    'role': ['create', 'drop', 'grant', 'inspect', 'rename', 'revoke'],
    'server': ['alter', 'execute', 'inspect'],
    'setting': ['inspect'],
    'shell': ['execute', 'inspect'],
    'transaction': ['execute', 'inspect'],
    'user': ['alter', 'create', 'drop', 'inspect', 'rename'],
}

EXTERNAL_OBJECT_KINDS = frozenset({'graph-projection', 'server'})
ONLINE_OBJECT_OPERATIONS = {
    kind: operations for kind, operations in FULL_OBJECT_OPERATIONS.items()
    if kind not in EXTERNAL_OBJECT_KINDS
}

# Retained as the exact base-edition evidence subset used by the independent
# GDS and Enterprise evidence-composition tests.
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
    value.add_argument(
        '--tool-workspace', type=Path,
        default=ROOT / 'tools/reference_engine_demos/runtime/'
        'tool_workspaces/neo4j',
    )
    value.add_argument(
        '--supplement-object-evidence', type=Path, action='append',
        default=[],
    )
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
    validation = provider.validate_visual_admin(request)
    if not validation['valid']:
        failures = '; '.join(
            f"{item.get('field_id', '<form>')}: {item.get('message', item)}"
            for item in validation.get('errors', [])
        )
        raise RuntimeError(
            'Neo4j visual validation failed for '
            f"{request.get('resource_kind')}:{request.get('operation_id')}: "
            f'{failures or "unspecified validation failure"}'
        )
    plan = provider.plan_visual_admin(request)
    if plan['state'] != 'ready':
        raise RuntimeError('Neo4j visual administration plan is not ready')
    try:
        result = provider.apply_visual_admin({
            'plan_id': plan['plan_id'],
            'plan_digest': plan['plan_digest'],
            'confirmed': True,
        })
    except Exception as exc:
        raise RuntimeError(
            'Neo4j provider apply failed for '
            f"{request.get('resource_kind')}:"
            f"{request.get('operation_id')}: {exc}"
        ) from exc
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
        'qualification_edition': 'enterprise',
        'passed_resource_operations': operations,
        'missing_resource_operations': missing,
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

    bundled_tools = ROOT / 'tools/reference_engine_demos/config/neo4j/bin'
    os.environ.setdefault(
        'CDEADMIN_NEO4J_ADMIN_BINARY', str(bundled_tools / 'neo4j-admin')
    )
    os.environ.setdefault(
        'CDEADMIN_NEO4J_CYPHER_SHELL_BINARY',
        str(bundled_tools / 'cypher-shell'),
    )

    import neo4j

    def acquire(*_args):
        return Lease(password)

    route = {
        'host': args.host, 'port': args.port, 'database': args.database,
        'username': args.username,
        'credential_reference_id': 'live-gate-credential',
        'principal_reference': 'live-gate-principal',
        'routing': not args.direct, 'tls_mode': args.tls_mode,
        'tool_workspace': str(args.tool_workspace.resolve()),
    }
    adapter = Neo4jClient(acquire)
    prefix = 'cdeadmin_' + uuid.uuid4().hex[:12]
    label = prefix + '_node'
    rel_type = prefix + '_rel'
    index_name = prefix + '_index'
    constraint_name = prefix + '_constraint'
    database_name = prefix.replace('_', '-') + '-database'
    composite_name = prefix.replace('_', '-') + '-composite'
    alias_name = prefix.replace('_', '-') + '-alias'
    user_name = prefix + '_user'
    renamed_user = user_name + '_renamed'
    role_name = prefix + '_role'
    renamed_role = role_name + '_renamed'
    restored_database = prefix.replace('_', '-') + '-restored'
    imported_database = prefix.replace('_', '-') + '-imported'
    offline_database = prefix.replace('_', '-') + '-offline'
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
                dbms_target = find(resources, 'dbms')
                apply('dbms', 'inspect', dbms_target)
                apply('dbms', 'execute', dbms_target, {
                    'action': 'clear-query-caches',
                })
                apply(
                    'database', 'inspect',
                    find(resources, 'database', args.database),
                )
                graph_target = find(resources, 'graph', args.database)
                apply('graph', 'inspect', graph_target)
                apply('graph', 'insert', graph_target, {
                    'values': {
                        'kind': 'node', 'labels': [visual_label],
                        'properties': {'source': 'graph-workspace'},
                    },
                })
                first = apply('node', 'insert', graph_target, {
                    'values': {
                        'labels': [visual_label],
                        'properties': {
                            'name': 'visual-alpha', 'external_id': prefix,
                        },
                    },
                })['provider_result']['records'][0]['n']
                second = apply('node', 'insert', graph_target, {
                    'values': {
                        'labels': [visual_label],
                        'properties': {'name': 'visual-beta'},
                    },
                })['provider_result']['records'][0]['n']
                first_target = target(
                    'node', first['element_id'], first
                )
                apply('node', 'inspect', first_target)
                apply('node', 'update', first_target, {
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
                    },
                )['provider_result']['records'][0]['r']
                relationship_target = target(
                    'relationship', relationship['element_id'], relationship
                )
                apply('relationship', 'inspect', relationship_target)
                apply('relationship', 'update', relationship_target, {
                    'changes': {'properties': {'weight': 8}},
                })

                resources = provider.list_resources({'route': route})
                apply('label', 'inspect', find(
                    resources, 'label', visual_label
                ))
                apply('relationship-type', 'inspect', find(
                    resources, 'relationship-type', rel_type + '_VISUAL'
                ))
                apply('property', 'inspect', find(
                    resources, 'property', 'external_id'
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
                apply('function', 'inspect', find(resources, 'function'))
                apply('setting', 'inspect', find(resources, 'setting'))
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
                query_target = target(
                    'query', active[0]['transactionId'], active[0]
                )
                apply('query', 'inspect', query_target)
                apply('query', 'execute', query_target, {
                    'action': 'terminate',
                    'arguments': {
                        'transaction_id': active[0]['transactionId'],
                    },
                })
                held_transaction.close()
                held_transaction = held_session.begin_transaction()
                marker = prefix + '_held_transaction_second'
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
                        'second Neo4j held transaction was not observable'
                    )
                transaction_target = target(
                    'transaction', active[0]['transactionId'], active[0]
                )
                apply('transaction', 'inspect', transaction_target)
                apply('transaction', 'execute', transaction_target, {
                    'action': 'terminate',
                    'arguments': {
                        'transaction_id': active[0]['transactionId'],
                    },
                })

                apply('database', 'create', None, {
                    'name': database_name,
                    'default_language': 'CYPHER 25',
                    'wait_mode': 'wait', 'wait_seconds': 30,
                })
                resources = provider.list_resources({'route': route})
                created_database = find(
                    resources, 'database', database_name
                )
                apply('database', 'inspect', created_database)
                apply('database', 'alter', created_database, {
                    'action': 'configure', 'access': 'read-only',
                    'wait_mode': 'wait', 'wait_seconds': 30,
                })
                apply('alias', 'create', None, {
                    'name': alias_name,
                    'options': {'database': database_name},
                })
                resources = provider.list_resources({'route': route})
                alias_target = find(resources, 'alias', alias_name)
                apply('alias', 'inspect', alias_target)
                apply('alias', 'alter', alias_target, {
                    'changes': {'database': args.database},
                })
                apply('alias', 'drop', alias_target)
                apply('database', 'drop', created_database, {
                    'confirmation': database_name,
                    'data_disposition': 'destroy',
                    'alias_action': 'restrict', 'wait_seconds': 30,
                })

                apply('composite-database', 'create', None, {
                    'name': composite_name,
                    'default_language': 'CYPHER 5',
                    'wait_mode': 'wait', 'wait_seconds': 30,
                })
                resources = provider.list_resources({'route': route})
                composite_target = find(
                    resources, 'composite-database', composite_name
                )
                apply('composite-database', 'inspect', composite_target)
                apply('composite-database', 'alter', composite_target, {
                    'action': 'configure',
                    'default_language': 'CYPHER 25',
                    'wait_mode': 'wait', 'wait_seconds': 30,
                })
                apply('composite-database', 'drop', composite_target, {
                    'confirmation': composite_name,
                    'data_disposition': 'destroy',
                    'alias_action': 'restrict', 'wait_seconds': 30,
                })

                apply('user', 'create', None, {
                    'name': user_name,
                    'options': {
                        'credential_reference_id': 'visual-user-secret',
                        'password_change_required': False,
                        'status': 'active',
                    },
                })
                resources = provider.list_resources({'route': route})
                user_target = find(resources, 'user', user_name)
                apply('user', 'inspect', user_target)
                apply('user', 'alter', user_target, {
                    'changes': {'status': 'suspended'},
                })
                apply('user', 'rename', user_target, {
                    'new_name': renamed_user,
                })
                resources = provider.list_resources({'route': route})
                user_target = find(resources, 'user', renamed_user)

                apply('role', 'create', None, {
                    'name': role_name, 'options': {},
                })
                resources = provider.list_resources({'route': route})
                role_target = find(resources, 'role', role_name)
                apply('role', 'inspect', role_target)
                apply('role', 'rename', role_target, {
                    'new_name': renamed_role,
                })
                resources = provider.list_resources({'route': route})
                role_target = find(resources, 'role', renamed_role)
                apply('role', 'grant', role_target, {
                    'principal': renamed_user,
                })
                apply('role', 'revoke', role_target, {
                    'principal': renamed_user,
                })
                privilege_draft = {
                    'principal': renamed_role,
                    'privileges': {
                        'effect': 'grant', 'scope': 'graph',
                        'action': 'MATCH', 'graph': args.database,
                        'resource': {
                            'kind': 'elements', 'names': ['*'],
                        },
                    },
                }
                resources = provider.list_resources({'route': route})
                privilege_target = find(resources, 'privilege')
                apply(
                    'privilege', 'grant', privilege_target, privilege_draft
                )
                apply('privilege', 'inspect', privilege_target)
                apply(
                    'privilege', 'revoke', privilege_target, privilege_draft
                )
                apply('role', 'drop', role_target)
                apply('user', 'drop', user_target)

                tool_targets = {}
                resources = provider.list_resources({'route': route})
                for tool_kind in (
                    'backup', 'restore', 'import', 'export', 'shell',
                    'consistency-check',
                ):
                    tool_targets[tool_kind] = find(resources, tool_kind)
                    apply(tool_kind, 'inspect', tool_targets[tool_kind])

                tool_root = args.tool_workspace.resolve() / prefix
                backup_path = tool_root / 'backup'
                dump_path = tool_root / 'dump'
                backup_path.mkdir(parents=True, exist_ok=False)
                dump_path.mkdir(parents=True, exist_ok=False)
                apply('shell', 'execute', tool_targets['shell'], {
                    'action': 'script',
                    'arguments': {
                        'database': args.database,
                        'path': f'{prefix}/shell-observation',
                        'script': 'RETURN 42 AS cdeadmin_shell_value',
                    },
                })
                apply('backup', 'execute', tool_targets['backup'], {
                    'action': 'backup',
                    'arguments': {
                        'database': args.database,
                        'path': f'{prefix}/backup',
                    },
                })
                backup_artifacts = sorted(backup_path.glob('*.backup'))
                if len(backup_artifacts) != 1:
                    raise RuntimeError(
                        'Neo4j provider backup did not create one artifact'
                    )
                apply('restore', 'execute', tool_targets['restore'], {
                    'action': 'restore',
                    'arguments': {
                        'database': restored_database,
                        'path': str(backup_artifacts[0].relative_to(
                            args.tool_workspace.resolve()
                        )),
                    },
                })
                consume(system_session.run(
                    f'CREATE DATABASE `{restored_database}` WAIT 30 SECONDS'
                ))
                restored_session = driver.session(
                    database=restored_database
                )
                try:
                    rows = consume(restored_session.run(
                        'RETURN 1 AS restored'
                    ))
                    if rows != [{'restored': 1}]:
                        raise RuntimeError(
                            'Neo4j restored database was not queryable'
                        )
                finally:
                    restored_session.close()
                consume(system_session.run(
                    f'DROP DATABASE `{restored_database}` '
                    'DESTROY DATA WAIT 30 SECONDS'
                ))

                nodes_path = tool_root / 'nodes.csv'
                nodes_path.write_text(
                    'personId:ID,name,:LABEL\n'
                    '1,Imported Person,CDEadminImported\n',
                    encoding='utf-8',
                )
                apply('import', 'execute', tool_targets['import'], {
                    'action': 'full',
                    'arguments': {
                        'database': imported_database,
                        'path': f'{prefix}/import-observation',
                        'nodes': f'{prefix}/nodes.csv',
                    },
                })
                consume(system_session.run(
                    f'CREATE DATABASE `{imported_database}` WAIT 30 SECONDS'
                ))
                imported_session = driver.session(
                    database=imported_database
                )
                try:
                    rows = consume(imported_session.run(
                        'MATCH (n:CDEadminImported) RETURN count(n) AS count'
                    ))
                    if rows != [{'count': 1}]:
                        raise RuntimeError(
                            'Neo4j provider import readback differed'
                        )
                finally:
                    imported_session.close()
                consume(system_session.run(
                    f'DROP DATABASE `{imported_database}` '
                    'DESTROY DATA WAIT 30 SECONDS'
                ))

                consume(system_session.run(
                    f'CREATE DATABASE `{offline_database}` WAIT 30 SECONDS'
                ))
                offline_session = driver.session(database=offline_database)
                try:
                    consume(offline_session.run(
                        'CREATE (:CDEadminOffline {qualified: true})'
                    ))
                finally:
                    offline_session.close()
                consume(system_session.run(
                    f'STOP DATABASE `{offline_database}` WAIT 30 SECONDS'
                ))
                apply(
                    'consistency-check', 'execute',
                    tool_targets['consistency-check'], {
                        'action': 'check',
                        'arguments': {
                            'database': offline_database,
                            'path': f'{prefix}/consistency.report',
                        },
                    },
                )
                apply('export', 'execute', tool_targets['export'], {
                    'action': 'dump',
                    'arguments': {
                        'database': offline_database,
                        'path': f'{prefix}/dump',
                    },
                })
                dump_artifact = dump_path / f'{offline_database}.dump'
                if not dump_artifact.is_file():
                    raise RuntimeError(
                        'Neo4j provider dump artifact was not created'
                    )
                consume(system_session.run(
                    f'DROP DATABASE `{offline_database}` '
                    'DESTROY DATA WAIT 30 SECONDS'
                ))

                apply('relationship', 'delete', relationship_target, {
                })
                apply('node', 'delete', first_target, {
                    'selector': {'detach': True},
                })
                apply('index', 'drop', visual_index)
                apply('constraint', 'drop', visual_constraint)
                observed_operations = {
                    kind: sorted(values)
                    for kind, values in passed_visual_operations.items()
                }
                if observed_operations != ONLINE_OBJECT_OPERATIONS:
                    raise RuntimeError(
                        'Neo4j online visual operation matrix is '
                        f'incomplete: {observed_operations!r}'
                    )
                return {
                    'qualification_edition': 'enterprise',
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
                shutil.rmtree(
                    args.tool_workspace.resolve() / prefix,
                    ignore_errors=True,
                )

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
            f'DROP ALIAS `{alias_name}` IF EXISTS FOR DATABASE',
            f'DROP COMPOSITE DATABASE `{composite_name}` IF EXISTS '
            'CASCADE ALIASES DESTROY DATA',
            f'DROP DATABASE `{database_name}` IF EXISTS DESTROY DATA',
            f'DROP ROLE `{renamed_role}` IF EXISTS',
            f'DROP ROLE `{role_name}` IF EXISTS',
            f'DROP USER `{renamed_user}` IF EXISTS',
            f'DROP USER `{user_name}` IF EXISTS',
            f'DROP DATABASE `{restored_database}` IF EXISTS DESTROY DATA',
            f'DROP DATABASE `{imported_database}` IF EXISTS DESTROY DATA',
            f'DROP DATABASE `{offline_database}` IF EXISTS DESTROY DATA',
        ):
            try:
                consume(system_session.run(statement))
            except Exception:
                pass
        graph_session.close()
        system_session.close()
        adapter._forget_driver(driver)
        adapter.close()

    supplement_sha256 = None

    def supplement_evidence():
        nonlocal supplement_sha256
        merged = []
        for supplied_path in args.supplement_object_evidence:
            path = supplied_path.resolve()
            document = json.loads(path.read_text(encoding='utf-8'))
            if document.get('schema') != (
                    'cdeadmin.provider-object-live-evidence.v1'):
                raise RuntimeError(
                    f'Neo4j supplement schema is invalid: {path}'
                )
            if document.get('engine_id') != 'neo4j' or str(
                    document.get('exact_profile')) != EXPECTED_SERVER:
                raise RuntimeError(
                    f'Neo4j supplement identity is invalid: {path}'
                )
            if document.get('passed') is not True or document.get(
                    'operation_failures'):
                raise RuntimeError(
                    f'Neo4j supplement did not pass: {path}'
                )
            for kind, operations in document.get(
                    'passed_resource_operations', {}).items():
                passed_visual_operations.setdefault(kind, set()).update(
                    operations
                )
            if document.get('surface_id') == (
                    'neo4j-graph-data-science-plugin'):
                supplement_sha256 = document.get('surface_sha256')
            merged.append({
                'path': str(path),
                'resource_operation_count': sum(len(values) for values in (
                    document.get('passed_resource_operations') or {}
                ).values()),
            })
        return {'supplements': merged}

    gates.append(observed(
        'supplement-object-evidence', supplement_evidence,
        required=bool(args.supplement_object_evidence),
    ))

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
        f'neo4j-{EXPECTED_SERVER}-{prefix}', passed_visual_operations,
        supplement_sha256,
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
