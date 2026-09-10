#!/usr/bin/env python3
##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Qualify the CDEadmin provider against Apache Cassandra 5.0.8.

The gate never accepts a compatible-looking substitute: both the Python
driver and server release must exactly match the selected profile. Passwords
are acquired through provider secret leases and are never written to output.
Test objects are uniquely named and removed on every reachable exit path.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
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

from pgadmin.cdeadmin.providers.cassandra.client import (  # noqa: E402
    CassandraClient,
    CassandraClientError,
)
from pgadmin.cdeadmin.providers.cassandra.provider import (  # noqa: E402
    PROFILE,
    create_provider,
)


EXPECTED_SERVER = '5.0.8'
EXPECTED_DRIVER = '3.30.1'
CATEGORIES = (
    'dependency', 'runtime', 'topology', 'resource', 'cql',
    'wide_column_result', 'native_outcome', 'schema', 'data_crud',
    'consistency', 'security', 'fault', 'tooling', 'object_operations', 'tls',
)
OBJECT_OPERATIONS = {
    'cluster': {'inspect'},
    'datacenter': {'inspect'},
    'node': {'inspect'},
    'keyspace': {'inspect', 'create', 'alter', 'drop'},
    'table': {
        'inspect', 'create', 'alter', 'insert', 'update', 'delete',
        'truncate', 'drop',
    },
    'column': {'inspect', 'create', 'rename', 'drop'},
    'index': {'inspect', 'create', 'drop'},
    'user-defined-type': {'inspect', 'create', 'alter', 'drop'},
    'materialized-view': {'inspect', 'create', 'alter', 'drop'},
    'trigger': {'inspect', 'create', 'drop'},
    'function': {'inspect', 'create', 'drop'},
    'aggregate': {'inspect', 'create', 'drop'},
    'role': {'inspect', 'create', 'alter', 'grant', 'revoke', 'drop'},
    'permission': {'inspect', 'grant', 'revoke'},
    'identity': {'inspect', 'create', 'drop'},
    'query': {'inspect'},
    'tracing-session': {'inspect'},
    'replication': {'inspect', 'alter'},
    'repair': {'inspect', 'execute'},
    'compaction': {'inspect', 'execute'},
    'snapshot': {'inspect', 'execute'},
    'backup': {'inspect', 'execute'},
    'restore': {'inspect', 'execute'},
    'shell': {'inspect', 'execute'},
}
CONCEPT_BINDINGS = {
    'keyspaces': ('keyspace',),
    'tables': ('table',),
    'columns': ('column',),
    'types': ('user-defined-type',),
    'materialized_views': ('materialized-view',),
    'indexes': ('index',),
    'triggers': ('trigger',),
    'functions': ('function',),
    'aggregates': ('aggregate',),
    'roles_and_permissions': ('role', 'permission'),
    'identities': ('identity',),
    'replication': ('replication',),
    'compaction': ('compaction',),
    'topology': ('cluster', 'datacenter', 'node'),
    'queries': ('query',),
    'tracing': ('tracing-session',),
    'repair': ('repair',),
    'snapshots': ('snapshot',),
    'backup_and_restore': ('backup', 'restore'),
    'shell': ('shell',),
}


class _Lease:
    def __init__(self, value):
        self.value = bytearray(value.encode('utf-8'))

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        for index in range(len(self.value)):
            self.value[index] = 0

    def use(self, callback):
        return callback(memoryview(self.value))


class _Permissions:
    def __init__(self, secrets_by_reference):
        self.secrets_by_reference = secrets_by_reference

    @staticmethod
    def require(_permission, _scope='endpoint'):
        return None

    @staticmethod
    def allows(_permission, _scope='endpoint'):
        return True

    def acquire_secret(self, reference, _principal, _purpose, _kind):
        try:
            return _Lease(self.secrets_by_reference[reference])
        except KeyError as exc:
            raise CassandraClientError(
                'qualification secret reference is unavailable'
            ) from exc


def parser():
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument('--host', default='127.0.0.1')
    value.add_argument('--contact-points', default='127.0.0.2,127.0.0.3')
    value.add_argument('--port', type=int, default=19042)
    value.add_argument('--jmx-port', type=int, default=17199)
    value.add_argument('--storage-port', type=int, default=17000)
    value.add_argument('--local-dc', default='datacenter1')
    value.add_argument('--username', default='cassandra')
    value.add_argument(
        '--tls-mode', choices=('disabled', 'system-ca', 'self-signed'),
        default='disabled',
    )
    value.add_argument('--expected-nodes', type=int, default=3)
    value.add_argument(
        '--fixture-container', default='cdeadmin-demo-cassandra',
    )
    value.add_argument('--workspace', type=Path, required=True)
    value.add_argument('--output', type=Path)
    value.add_argument('--object-output', type=Path)
    return value


def _context(tls_mode):
    endpoint_id = str(uuid.uuid5(
        uuid.NAMESPACE_URL,
        f'cdeadmin-live:cassandra:5.0.8:{tls_mode}',
    ))

    def child(purpose):
        return str(uuid.uuid5(uuid.UUID(endpoint_id), purpose))
    return SimpleNamespace(
        endpoint_id=endpoint_id, mode='legacy_native',
        experience_family='cassandra', provider_id=PROFILE.provider_id,
        provider_version='0.1.0', profile_id=PROFILE.profile_id,
        profile_version=PROFILE.exact_version,
        target_adapter_id='cql-client',
        target_adapter_version='cassandra-driver-3.30.1',
        pool_namespace=child('pool'), session_namespace=child('session'),
        cache_namespace=child('cache'),
        diagnostic_namespace=child('diagnostic'),
        effective_permissions=frozenset({
            'network', 'secret_read', 'data_read', 'data_write',
            'administer', 'execute', 'filesystem',
        }),
        declared_runtime_family='cassandra',
        verified_runtime_family='cassandra',
        verified_runtime_version=EXPECTED_SERVER,
        runtime_verification_state='verified',
        runtime_evidence_reference=(
            'cde-cassandra-live:cassandra-5.0.8:20260902'
        ),
        runtime_identity_generation='cassandra-5.0.8-live',
    )


def _apply(provider, request):
    label = f'{request["resource_kind"]}.{request["operation_id"]}'
    plan = None
    try:
        validation = provider.validate_visual_admin(request)
        if not validation['valid']:
            failures = '; '.join(
                f"{item.get('field_id', '<form>')}: "
                f"{item.get('message', item)}"
                for item in validation.get('errors', [])
            )
            raise RuntimeError(
                f'{label} validation failed: '
                f'{failures or "unspecified validation failure"}'
            )
        plan = provider.plan_visual_admin(request)
        if plan['state'] != 'ready':
            raise RuntimeError('Cassandra visual plan is not ready')
        result = provider.apply_visual_admin({
            'plan_id': plan['plan_id'],
            'plan_digest': plan['plan_digest'],
            'confirmed': True,
        })
    except Exception as exc:
        raise RuntimeError(f'{label} failed: {exc}') from exc
    native = result['provider_result']
    if native.get('accepted') is not True and native.get(
            'driver_observation_only') is not True:
        raise RuntimeError('Cassandra did not accept the provider plan')
    if native.get('transaction_finality_interpreted_by_common_code'):
        raise RuntimeError('common code interpreted Cassandra finality')
    if provider.client.admin_operation_requires_dialect(
        request['resource_kind'], request['operation_id']
    ):
        evidence = getattr(
            provider, '_qualification_dialect_task_evidence', None
        )
        if isinstance(evidence, list):
            evidence.append({
                'task_id': (
                    f'visual_admin.{request["resource_kind"]}.'
                    f'{request["operation_id"]}'
                ),
                'status': 'passed',
                'command_preview': plan['command_preview'],
            })
    return result


def _target(kind, name, native):
    return {
        'resource_id': f'qualification:{kind}:{name}',
        'resource_kind': kind,
        'display_name': name,
        'display_path': ['cassandra', kind, name],
        'authority_path': ['cassandra', kind, name],
        'generation': 'live-qualification',
        'extensions': {'cassandra': {'provider_owned': True,
                                     'native': native}},
    }


def _request(route, kind, operation, draft=None, target=None):
    return {
        'resource_kind': kind,
        'operation_id': operation,
        'target_resource': target,
        'draft': draft or {},
        '_provider_route': route,
    }


def _execute(provider, session_id, source, parameters=()):
    operation = provider.execute({
        'session_id': session_id,
        'execution_id': str(uuid.uuid4()),
        'source': source,
        'parameters': parameters,
    })
    return provider.describe_result(operation)


def _rows(result):
    return result['extensions']['cassandra']['payload']['rows']


def verify(args, password):
    import cassandra

    run_id = uuid.uuid4().hex[:12]
    prefix = 'cdeadmin_' + run_id
    keyspace = prefix
    table = 'items'
    type_name = 'address'
    index_name = 'items_value_sai'
    view_name = 'items_by_value'
    trigger_name = 'qualification_trigger'
    trigger_class = 'org.cdeadmin.cassandra.CDEadminQualificationTrigger'
    function_name = 'sum_state'
    aggregate_name = 'sum_int'
    role_name = prefix + '_role'
    member_role_name = prefix + '_member'
    identity_name = f'spiffe://cdeadmin/qualification/{run_id}'
    role_password = secrets.token_urlsafe(32)
    admin_reference = 'qualification-admin'
    jmx_reference = 'qualification-jmx'
    role_reference = 'qualification-role'
    bad_reference = 'qualification-bad'
    secrets_by_reference = {
        admin_reference: password,
        jmx_reference: password,
        role_reference: role_password,
        bad_reference: 'definitely-not-the-correct-password-' + run_id,
    }
    permissions = _Permissions(secrets_by_reference)
    context = _context(args.tls_mode)
    client = CassandraClient(permissions.acquire_secret)
    provider = create_provider(context, permissions, client)
    provider._qualification_dialect_task_evidence = []
    workspace = args.workspace.resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    restore_fixture_root = workspace / f'{prefix}_sstable_restore'
    route = {
        'route_id': f'exact-live-{args.tls_mode}',
        'host': args.host, 'port': args.port,
        'contact_points': [
            item.strip() for item in args.contact_points.split(',')
            if item.strip()
        ],
        'local_dc': args.local_dc,
        'username': args.username,
        'credential_reference_id': admin_reference,
        'credential_references': {
            'database_password': admin_reference,
            'jmx_password': jmx_reference,
        },
        'principal_reference': 'cdeadmin-live-qualifier',
        'tls_mode': args.tls_mode,
        'consistency': 'LOCAL_QUORUM',
        'serial_consistency': 'LOCAL_SERIAL',
        'protocol_version': 5,
        'request_timeout': 60,
        'connect_timeout': 15,
        'jmx_port': args.jmx_port,
        'storage_port': args.storage_port,
        'jmx_auth_mode': 'password',
        'jmx_username': args.username,
        'tool_workspace': str(workspace),
    }
    base_request = {
        'route': route,
        'capability_generation': f'exact-live-{run_id}',
    }

    def wait_for_catalog(source, parameters, label):
        from cassandra.auth import PlainTextAuthProvider
        from cassandra.cluster import Cluster
        from cassandra.policies import WhiteListRoundRobinPolicy

        nodes = [args.host] + route['contact_points']
        deadline = time.monotonic() + route.get(
            'schema_agreement_timeout', 10
        ) + 20
        pending = list(dict.fromkeys(nodes))
        last_errors = {}
        while pending and time.monotonic() < deadline:
            remaining = []
            for host in pending:
                cluster = Cluster(
                    contact_points=[host], port=args.port,
                    auth_provider=PlainTextAuthProvider(
                        username=args.username, password=password,
                    ),
                    protocol_version=5,
                    load_balancing_policy=WhiteListRoundRobinPolicy([host]),
                    connect_timeout=10,
                    control_connection_timeout=10,
                )
                session_value = None
                try:
                    session_value = cluster.connect()
                    rows = list(session_value.execute(source, parameters))
                    if not rows:
                        remaining.append(host)
                except Exception as exc:
                    last_errors[host] = type(exc).__name__
                    remaining.append(host)
                finally:
                    if session_value is not None:
                        session_value.shutdown()
                    cluster.shutdown()
            pending = remaining
            if pending:
                time.sleep(0.5)
        if pending:
            raise RuntimeError(
                f'{label} did not converge on nodes {pending}: '
                f'{last_errors}'
            )
    categories = {name: 'not_run' for name in CATEGORIES}
    details = {}
    failures = []
    observed_operations = {
        kind: set() for kind in OBJECT_OPERATIONS
    }
    session = None
    resources = []
    table_target = None
    identity_token = None

    def category(name, callback):
        try:
            details[name] = callback()
        except Exception as exc:
            message = str(exc)
            for secret in secrets_by_reference.values():
                message = message.replace(secret, '[redacted]')
            categories[name] = 'failed'
            failures.append({
                'category': name,
                'error_type': type(exc).__name__,
                'message': message[:2000],
            })
        else:
            categories[name] = 'passed'

    category('dependency', lambda: (
        {'expected': EXPECTED_DRIVER, 'observed': cassandra.__version__}
        if cassandra.__version__ == EXPECTED_DRIVER else
        (_ for _ in ()).throw(RuntimeError(
            f'expected driver {EXPECTED_DRIVER}, observed '
            f'{cassandra.__version__}'
        ))
    ))

    def runtime_gate():
        discovered = provider.discover_endpoint(base_request)
        identity = discovered['verified_runtime']
        if identity['version'] != EXPECTED_SERVER:
            raise RuntimeError(
                f'expected Cassandra {EXPECTED_SERVER}, observed '
                f'{identity["version"]}'
            )
        if str(identity['native']['native_protocol_version']) != '5':
            raise RuntimeError('native protocol v5 was not negotiated')
        return identity

    category('runtime', runtime_gate)

    def topology_gate():
        nonlocal resources
        resources = provider.list_resources(base_request)
        nodes = [item for item in resources
                 if item['resource_kind'] == 'node']
        identities = {
            json.dumps(
                item['extensions']['cassandra']['native'].get('host_id'),
                sort_keys=True,
            ) for item in nodes
        }
        versions = {
            str(item['extensions']['cassandra']['native'].get(
                'release_version'
            )) for item in nodes
        }
        if len(nodes) != args.expected_nodes or len(identities) != len(nodes):
            raise RuntimeError(
                f'expected {args.expected_nodes} distinct nodes, observed '
                f'{len(nodes)}'
            )
        if versions != {EXPECTED_SERVER}:
            raise RuntimeError(f'cluster version set is {sorted(versions)}')
        return {'nodes': len(nodes), 'versions': sorted(versions),
                'distinct_host_ids': len(identities)}

    category('topology', topology_gate)

    def resource_gate():
        kinds = {item['resource_kind'] for item in resources}
        required = {
            'cluster', 'datacenter', 'node', 'keyspace', 'role',
            'query', 'repair', 'compaction', 'snapshot',
            'backup', 'restore', 'shell',
        }
        missing = required.difference(kinds)
        if missing:
            raise RuntimeError('missing resource kinds: ' +
                               ', '.join(sorted(missing)))
        tools = {item['display_name'] for item in provider.list_tools({})}
        if tools != {'cqlsh', 'nodetool', 'sstableloader'}:
            raise RuntimeError('tool resources are incomplete')
        descriptor = provider.visual_admin_descriptor()
        expected_objects = {
            'cluster', 'datacenter', 'node', 'keyspace', 'table',
            'column', 'index', 'materialized-view', 'user-defined-type',
            'trigger', 'function', 'aggregate', 'role', 'permission',
            'identity', 'replication', 'query', 'tracing-session', 'repair',
            'compaction', 'snapshot', 'backup', 'restore', 'shell',
        }
        observed = {item['resource_kind'] for item in descriptor['objects']}
        if observed != expected_objects:
            raise RuntimeError('visual administration catalog is incomplete')
        return {'resource_kinds': sorted(kinds),
                'visual_objects': sorted(observed), 'tools': sorted(tools)}

    category('resource', resource_gate)

    def cql_gate():
        nonlocal session
        session = provider.open_session(base_request)
        result = _execute(
            provider, session['session_id'],
            'SELECT release_version, cql_version FROM system.local',
        )
        rows = _rows(result)
        if len(rows) != 1 or rows[0]['release_version'] != EXPECTED_SERVER:
            raise RuntimeError('parameter-free CQL query did not round trip')
        return {'language': provider.describe_language({})[0][
            'display_name'
        ], 'rows': len(rows)}

    category('cql', cql_gate)

    def result_gate():
        if session is None:
            raise RuntimeError('CQL session is unavailable')
        result = _execute(
            provider, session['session_id'],
            'SELECT cluster_name, tokens FROM system.local',
        )
        if result['result_kind'] != 'wide_column' or not result['complete']:
            raise RuntimeError('wide-column result contract was not preserved')
        renderer = provider.select_renderer(result)
        native = renderer['extensions']['cassandra']
        if native['component_reference'] != (
            'cdeadmin/results/WideColumnView'
        ):
            raise RuntimeError('wide-column production renderer unavailable')
        return {'result_kind': result['result_kind'],
                'component': native['component_reference']}

    category('wide_column_result', result_gate)

    def outcome_gate():
        if session is None:
            raise RuntimeError('CQL session is unavailable')
        outcome = provider.describe_transaction(session)
        payload = outcome['provider_payload']
        extension = outcome['extensions']['cassandra']
        if payload.get('common_finality_inference') is not False:
            raise RuntimeError('common finality inference was enabled')
        if payload.get('retry_decision_owned_by_common_code') is not False:
            raise RuntimeError('common retry ownership was enabled')
        if extension.get('opaque_provider_value') is not True:
            raise RuntimeError('provider outcome was not opaque')
        return payload

    category('native_outcome', outcome_gate)

    def schema_gate():
        nonlocal resources, table_target
        keyspace_target = _target(
            'keyspace', keyspace, {'keyspace_name': keyspace}
        )
        _apply(provider, _request(route, 'keyspace', 'create', {
            'name': keyspace,
            'replication': {
                'class': 'NetworkTopologyStrategy',
                args.local_dc: args.expected_nodes,
            },
            'durable_writes': True,
        }))
        _apply(provider, _request(
            route, 'keyspace', 'alter', {
                'replication': {
                    'class': 'NetworkTopologyStrategy',
                    args.local_dc: args.expected_nodes,
                },
                'durable_writes': True,
            }, keyspace_target,
        ))
        _apply(provider, _request(route, 'user-defined-type', 'create', {
            'keyspace': keyspace, 'name': type_name,
            'fields': [{'name': 'street', 'type': 'text'}],
        }))
        type_target = _target('user-defined-type', type_name, {
            'keyspace_name': keyspace, 'type_name': type_name,
        })
        _apply(provider, _request(
            route, 'user-defined-type', 'alter',
            {'fields': [{'name': 'postal_code', 'type': 'text'}]},
            type_target,
        ))
        _apply(provider, _request(route, 'table', 'create', {
            'keyspace': keyspace, 'name': table,
            'columns': [
                {'name': 'tenant', 'type': 'text'},
                {'name': 'item_id', 'type': 'int'},
                {'name': 'value', 'type': 'text'},
                {'name': 'score', 'type': 'int'},
            ],
            'partition_keys': ['tenant'],
            'clustering_keys': ['item_id'],
            'options': {'comment': 'CDEadmin live qualification'},
        }))
        resources = provider.list_resources(base_request)
        table_target = next(
            item for item in resources
            if item['resource_kind'] == 'table' and
            item['display_name'] == table and
            item['extensions']['cassandra']['native'].get(
                'keyspace_name'
            ) == keyspace
        )
        _apply(provider, _request(
            route, 'table', 'alter',
            {'changes': {'add_columns': [
                {'name': 'table_added', 'type': 'text'},
            ]}}, table_target,
        ))
        column_target = _target('column', 'item_id', {
            'keyspace_name': keyspace, 'table_name': table,
            'column_name': 'item_id', 'kind': 'clustering',
        })
        _apply(provider, _request(
            route, 'column', 'create',
            {'name': 'visual_added', 'type': 'text'}, table_target,
        ))
        _apply(provider, _request(
            route, 'column', 'rename', {'new_name': 'item_key'},
            column_target,
        ))
        renamed_column = _target('column', 'item_key', {
            'keyspace_name': keyspace, 'table_name': table,
            'column_name': 'item_key', 'kind': 'clustering',
        })
        _apply(provider, _request(
            route, 'column', 'rename', {'new_name': 'item_id'},
            renamed_column,
        ))
        regular_column = _target('column', 'visual_added', {
            'keyspace_name': keyspace, 'table_name': table,
            'column_name': 'visual_added', 'kind': 'regular',
        })
        _apply(provider, _request(
            route, 'column', 'drop',
            {'confirmation': 'drop-column'},
            regular_column,
        ))
        _apply(provider, _request(route, 'index', 'create', {
            'keyspace': keyspace, 'table': table, 'name': index_name,
            'target': 'value', 'index_kind': 'sai', 'options': {},
        }))
        _apply(provider, _request(route, 'materialized-view', 'create', {
            'keyspace': keyspace, 'name': view_name,
            'base_table': table, 'select_columns': ['*'],
            'not_null_columns': ['value', 'tenant', 'item_id'],
            'partition_keys': ['value'],
            'clustering_keys': ['tenant', 'item_id'], 'options': {},
        }))
        _apply(provider, _request(
            route, 'materialized-view', 'alter', {
                'options': {
                    'comment': 'CDEadmin live qualification view',
                },
            }, _target('materialized-view', view_name, {
                'keyspace_name': keyspace, 'view_name': view_name,
            }),
        ))
        _apply(provider, _request(route, 'trigger', 'create', {
            'keyspace': keyspace, 'table': table,
            'name': trigger_name, 'class_name': trigger_class,
        }))
        _apply(provider, _request(route, 'function', 'create', {
            'keyspace': keyspace, 'name': function_name,
            'arguments': [
                {'name': 'state_value', 'type': 'int'},
                {'name': 'input_value', 'type': 'int'},
            ],
            'return_type': 'int', 'language': 'java',
            'called_on_null_input': True,
            'body': ('return (state_value == null ? 0 : state_value) + '
                     '(input_value == null ? 0 : input_value);'),
        }))
        wait_for_catalog(
            'SELECT function_name FROM system_schema.functions WHERE '
            'keyspace_name = %s AND function_name = %s',
            (keyspace, function_name), 'function schema',
        )
        _apply(provider, _request(route, 'aggregate', 'create', {
            'keyspace': keyspace, 'name': aggregate_name,
            'argument_types': ['int'],
            'state_function': function_name, 'state_type': 'int',
            'initial_condition': '0',
        }))
        resources = provider.list_resources(base_request)
        required = {
            ('keyspace', keyspace), ('table', table),
            ('user-defined-type', type_name), ('index', index_name),
            ('materialized-view', view_name),
            ('trigger', trigger_name),
            ('function', function_name), ('aggregate', aggregate_name),
        }
        observed = {(item['resource_kind'], item['display_name'])
                    for item in resources}
        missing = required.difference(observed)
        if missing:
            raise RuntimeError('schema discovery missing ' + repr(missing))
        table_target = next(
            item for item in resources
            if item['resource_kind'] == 'table' and
            item['display_name'] == table and
            item['extensions']['cassandra']['native'].get(
                'keyspace_name'
            ) == keyspace
        )
        return {'created_and_discovered': sorted(
            f'{kind}:{name}' for kind, name in required
        )}

    category('schema', schema_gate)
    if categories['schema'] == 'passed':
        observed_operations['keyspace'].update({'create', 'alter'})
        observed_operations['table'].update({'create', 'alter'})
        observed_operations['column'].update({'create', 'rename', 'drop'})
        observed_operations['user-defined-type'].update({'create', 'alter'})
        observed_operations['index'].add('create')
        observed_operations['materialized-view'].update({'create', 'alter'})
        observed_operations['trigger'].add('create')
        observed_operations['function'].add('create')
        observed_operations['aggregate'].add('create')

    def data_gate():
        nonlocal identity_token
        if table_target is None:
            raise RuntimeError('table target is unavailable')
        _apply(provider, _request(route, 'table', 'insert', {
            'values': {
                'tenant': 'alpha', 'item_id': 1,
                'value': 'before', 'score': 7,
            },
            'options': {'if_not_exists': True},
        }, table_target))
        page = provider.read_visual_admin_rows({
            'target_resource': table_target, '_provider_route': route,
            'limit': 20, 'filter': {'tenant': 'alpha'},
        })
        if not page['editable'] or len(page['rows']) != 1:
            raise RuntimeError('editable wide-column row page failed')
        identity_token = page['rows'][0]['identity_token']
        _apply(provider, _request(route, 'table', 'update', {
            'selector': {'identity_token': identity_token},
            'changes': {'value': 'after', 'score': 8},
        }, table_target))
        page = provider.read_visual_admin_rows({
            'target_resource': table_target, '_provider_route': route,
            'limit': 20, 'filter': {'tenant': 'alpha'},
        })
        row = page['rows'][0]
        if row['values']['value'] != 'after' or row['values']['score'] != 8:
            raise RuntimeError('visual update did not round trip')
        _apply(provider, _request(route, 'table', 'delete', {
            'selector': {'identity_token': row['identity_token']},
            'confirmation': 'delete-row',
        }, table_target))
        empty = provider.read_visual_admin_rows({
            'target_resource': table_target, '_provider_route': route,
            'limit': 20, 'filter': {'tenant': 'alpha'},
        })
        if empty['rows']:
            raise RuntimeError('visual delete did not round trip')
        return {'insert_update_delete': True,
                'identity_policy': page['identity_policy']}

    category('data_crud', data_gate)
    if categories['data_crud'] == 'passed':
        observed_operations['table'].update({'insert', 'update', 'delete'})

    def consistency_gate():
        if session is None:
            raise RuntimeError('CQL session is unavailable')
        first = _execute(
            provider, session['session_id'],
            f'INSERT INTO "{keyspace}"."{table}" '
            '(tenant, item_id, value, score) VALUES (%s, %s, %s, %s) '
            'IF NOT EXISTS', ('lwt', 1, 'first', 1),
        )
        second = _execute(
            provider, session['session_id'],
            f'INSERT INTO "{keyspace}"."{table}" '
            '(tenant, item_id, value, score) VALUES (%s, %s, %s, %s) '
            'IF NOT EXISTS', ('lwt', 1, 'second', 2),
        )
        first_rows, second_rows = _rows(first), _rows(second)
        if not first_rows or not second_rows:
            raise RuntimeError('LWT did not return native observations')
        applied_key = next((key for key in first_rows[0]
                            if key.lower() == '[applied]'), None)
        if (applied_key is None or first_rows[0][applied_key] is not True or
                second_rows[0][applied_key] is not False):
            raise RuntimeError('LWT native applied observations differed')
        transaction = provider.describe_transaction(session)[
            'provider_payload'
        ]
        return {
            'regular_consistency': route['consistency'],
            'serial_consistency': route['serial_consistency'],
            'first_applied': first_rows[0][applied_key],
            'second_applied': second_rows[0][applied_key],
            'observation_authority': transaction[
                'lightweight_transaction_outcome'
            ],
        }

    category('consistency', consistency_gate)

    def security_gate():
        role_target = _target('role', role_name, {'role': role_name})
        member_target = _target(
            'role', member_role_name, {'role': member_role_name}
        )
        _apply(provider, _request(route, 'role', 'create', {
            'name': role_name, 'login': True, 'superuser': False,
            'password_credential_reference': role_reference,
            'options': {},
        }))
        _apply(provider, _request(route, 'role', 'alter', {
            'login': True, 'superuser': False,
            'options': {},
        }, role_target))
        _apply(provider, _request(route, 'role', 'create', {
            'name': member_role_name, 'login': False, 'superuser': False,
            'options': {},
        }))
        membership = {
            'principal': member_role_name,
            'privileges': [role_name],
        }
        _apply(provider, _request(
            route, 'role', 'grant', membership, member_target,
        ))
        _apply(provider, _request(
            route, 'role', 'revoke', membership, member_target,
        ))
        permission = {
            'principal': role_name, 'privileges': ['SELECT', 'MODIFY'],
            'resource': {'kind': 'table', 'keyspace': keyspace,
                         'table': table},
        }
        _apply(provider, _request(
            route, 'permission', 'grant', permission,
            _target('permission', role_name, {'role': role_name}),
        ))
        security = provider.describe_security(base_request)
        native = security['extensions']['cassandra']['native']
        roles = native.get('roles', [])
        permissions_seen = native.get('permissions', [])
        if not any(item.get('role') == role_name for item in roles):
            raise RuntimeError('created Cassandra role was not discovered')
        if not any(item.get('role') == role_name
                   for item in permissions_seen):
            raise RuntimeError('granted permission was not discovered')
        _apply(provider, _request(
            route, 'permission', 'revoke', permission,
            _target('permission', role_name, {'role': role_name}),
        ))
        _apply(provider, _request(route, 'identity', 'create', {
            'identity': identity_name, 'role': role_name,
        }))
        identities = [
            item for item in provider.list_resources(base_request)
            if item['resource_kind'] == 'identity' and
            item['display_name'] == identity_name
        ]
        if len(identities) != 1:
            raise RuntimeError('certificate identity was not discovered')
        return {
            'role_discovered': True, 'permission_discovered': True,
            'identity_discovered': True, 'credentials_reported': False,
        }

    category('security', security_gate)
    if categories['security'] == 'passed':
        observed_operations['role'].update({
            'create', 'alter', 'grant', 'revoke',
        })
        observed_operations['permission'].update({'grant', 'revoke'})
        observed_operations['identity'].add('create')

    def fault_gate():
        invalid = dict(route)
        invalid['protocol_version'] = 4
        try:
            client.runtime_identity({'route': invalid})
        except CassandraClientError:
            pass
        else:
            raise RuntimeError('old native protocol route was accepted')
        bad = dict(route)
        bad['credential_reference_id'] = bad_reference
        bad['credential_references'] = dict(route['credential_references'])
        bad['credential_references']['database_password'] = bad_reference
        try:
            client.runtime_identity({'route': bad})
        except CassandraClientError:
            pass
        else:
            raise RuntimeError('invalid authentication was accepted')
        if identity_token is not None and table_target is not None:
            try:
                provider.plan_visual_admin(_request(
                    route, 'table', 'update', {
                        'selector': {'identity_token': identity_token},
                        'changes': {'value': 'replayed'},
                    }, table_target,
                ))
            except CassandraClientError:
                pass
            else:
                raise RuntimeError('consumed row identity was reusable')
        return {'old_protocol_refused': True, 'bad_auth_refused': True,
                'row_identity_replay_refused': identity_token is not None}

    category('fault', fault_gate)

    def tooling_gate():
        cql_file = workspace / f'{prefix}.cql'
        cql_file.write_text(
            'SELECT release_version FROM system.local;\n',
            encoding='utf-8',
        )

        def tool(kind, action, arguments):
            payload = {
                'resource_kind': kind, 'operation_id': 'execute',
                'draft': {'action': action, 'arguments': arguments},
                'native': {}, '_provider_route': route,
            }
            return client.apply_admin_operation({
                'provider_payload': payload
            })

        shell = tool('shell', 'file', {'path': cql_file.name})
        if EXPECTED_SERVER not in shell['tool_result']['stdout']:
            raise RuntimeError('cqlsh did not return the exact version')
        snapshot_name = prefix + '_snapshot'
        snapshot = tool('snapshot', 'snapshot', {
            'name': snapshot_name, 'keyspace': keyspace,
        })
        tool('snapshot', 'clearsnapshot', {
            'name': snapshot_name, 'keyspace': keyspace,
        })
        sstable_path = workspace / 'sstables'
        sstable_path.mkdir(exist_ok=True)
        try:
            tool('restore', 'sstableloader', {'path': sstable_path.name})
        except CassandraClientError as exc:
            if 'environment authprovider' in str(exc).lower():
                raise RuntimeError(
                    'sstableloader environment AuthProvider is unavailable'
                ) from exc
            loader_observation = 'native-empty-input-refusal'
        else:
            loader_observation = 'accepted-empty-input'
        return {
            'cqlsh_return_code': shell['tool_result']['return_code'],
            'cqlsh_tls_mode': route['tls_mode'],
            'nodetool_snapshot_return_code': snapshot[
                'tool_result'
            ]['return_code'],
            'authenticated_sstableloader_environment_provider': True,
            'sstableloader_observation': loader_observation,
        }

    category('tooling', tooling_gate)

    def object_operations_gate():
        if table_target is None:
            raise RuntimeError('wide-column object targets are unavailable')

        def observe(kind, operation, draft=None, target=None):
            result = _apply(
                provider, _request(
                    route, kind, operation, draft=draft, target=target
                )
            )
            observed_operations[kind].add(operation)
            return result

        keyspace_target = _target(
            'keyspace', keyspace, {'keyspace_name': keyspace}
        )
        cluster_target = next(
            item for item in resources
            if item['resource_kind'] == 'cluster'
        )
        datacenter_target = next(
            item for item in resources
            if item['resource_kind'] == 'datacenter'
        )
        node_target = next(
            item for item in resources
            if item['resource_kind'] == 'node'
        )
        replication_target = _target(
            'replication', keyspace, {'keyspace_name': keyspace}
        )
        column_target = _target('column', 'item_id', {
            'keyspace_name': keyspace, 'table_name': table,
            'column_name': 'item_id', 'kind': 'clustering',
        })
        type_target = _target('user-defined-type', type_name, {
            'keyspace_name': keyspace, 'type_name': type_name,
        })
        view_target = _target('materialized-view', view_name, {
            'keyspace_name': keyspace, 'view_name': view_name,
        })
        index_target = _target('index', index_name, {
            'keyspace_name': keyspace, 'table_name': table,
            'index_name': index_name,
        })
        trigger_target = _target('trigger', trigger_name, {
            'keyspace_name': keyspace, 'table_name': table,
            'trigger_name': trigger_name,
        })
        function_target = _target('function', function_name, {
            'keyspace_name': keyspace, 'function_name': function_name,
            'argument_types': ['int', 'int'],
        })
        aggregate_target = _target('aggregate', aggregate_name, {
            'keyspace_name': keyspace, 'aggregate_name': aggregate_name,
            'argument_types': ['int'],
        })
        role_target = _target('role', role_name, {'role': role_name})
        member_target = _target(
            'role', member_role_name, {'role': member_role_name}
        )
        permission_target = _target(
            'permission', role_name, {'role': role_name}
        )
        identity_target = _target('identity', identity_name, {
            'identity': identity_name, 'role': role_name,
        })
        query_target = _target('query', 'CQL query execution', {
            'name': 'CQL query execution', 'virtual_resource': True,
        })
        tracing_id = uuid.uuid4()
        tracing_target = _target('tracing-session', str(tracing_id), {
            'session_id': {'$type': 'UUID', '$value': str(tracing_id)},
        })
        repair_target = _target('repair', 'Repair operations', {
            'name': 'Repair operations', 'tool_resource': True,
        })
        compaction_target = _target(
            'compaction', 'Compaction operations', {
                'name': 'Compaction operations', 'tool_resource': True,
            }
        )
        snapshot_target = _target('snapshot', 'Snapshot operations', {
            'name': 'Snapshot operations', 'tool_resource': True,
        })
        backup_target = _target('backup', 'SSTable backup tools', {
            'name': 'SSTable backup tools', 'tool_resource': True,
        })
        restore_target = _target('restore', 'SSTable restore tools', {
            'name': 'SSTable restore tools', 'tool_resource': True,
        })
        shell_target = _target('shell', 'CQL shell', {
            'name': 'CQL shell', 'tool_resource': True,
        })
        observe('cluster', 'inspect', target=cluster_target)
        observe('datacenter', 'inspect', target=datacenter_target)
        observe('node', 'inspect', target=node_target)
        observe('keyspace', 'inspect', target=keyspace_target)
        observe('table', 'inspect', target=table_target)
        observe('column', 'inspect', target=column_target)
        observe('index', 'inspect', target=index_target)
        observe('user-defined-type', 'inspect', target=type_target)
        observe('materialized-view', 'inspect', target=view_target)
        observe('trigger', 'inspect', target=trigger_target)
        observe('function', 'inspect', target=function_target)
        observe('aggregate', 'inspect', target=aggregate_target)
        observe('role', 'inspect', target=role_target)
        observe('permission', 'inspect', target=permission_target)
        observe('identity', 'inspect', target=identity_target)
        observe('query', 'inspect', target=query_target)
        observe('tracing-session', 'inspect', target=tracing_target)
        observe('replication', 'inspect', target=replication_target)
        observe('replication', 'alter', {
            'replication': {
                'class': 'NetworkTopologyStrategy',
                args.local_dc: args.expected_nodes,
            },
            'durable_writes': True,
        }, replication_target)
        observe('repair', 'inspect', target=repair_target)
        observe('repair', 'execute', {
            'action': 'repair', 'arguments': {'keyspace': keyspace},
            'confirmation': 'repair-keyspace',
        }, repair_target)
        observe('compaction', 'inspect', target=compaction_target)
        observe('compaction', 'execute', {
            'action': 'compact', 'arguments': {'keyspace': keyspace},
            'confirmation': 'compact-keyspace',
        }, compaction_target)
        snapshot_name = prefix + '_object_snapshot'
        observe('snapshot', 'inspect', target=snapshot_target)
        observe('snapshot', 'execute', {
            'action': 'snapshot',
            'arguments': {'name': snapshot_name, 'keyspace': keyspace},
            'confirmation': 'snapshot-keyspace',
        }, snapshot_target)
        observe('snapshot', 'execute', {
            'action': 'clearsnapshot',
            'arguments': {'name': snapshot_name, 'keyspace': keyspace},
            'confirmation': 'clear-snapshot',
        }, snapshot_target)
        backup_name = prefix + '_backup'
        observe('backup', 'inspect', target=backup_target)
        observe('backup', 'execute', {
            'action': 'snapshot',
            'arguments': {'name': backup_name, 'keyspace': keyspace},
            'confirmation': 'create-backup',
        }, backup_target)
        observe('backup', 'execute', {
            'action': 'snapshot',
            'arguments': {
                'name': backup_name + '_second', 'keyspace': keyspace,
            },
            'confirmation': 'create-backup',
        }, backup_target)
        for name in (backup_name, backup_name + '_second'):
            observe('snapshot', 'execute', {
                'action': 'clearsnapshot',
                'arguments': {'name': name, 'keyspace': keyspace},
                'confirmation': 'clear-backup-snapshot',
            }, snapshot_target)
        restore_snapshot = prefix + '_restore_source'
        observe('backup', 'execute', {
            'action': 'snapshot',
            'arguments': {
                'name': restore_snapshot, 'keyspace': keyspace,
            },
            'confirmation': 'create-restore-source',
        }, backup_target)
        located = subprocess.run([
            'docker', 'exec', args.fixture_container, 'find',
            f'/var/lib/cassandra/data/{keyspace}', '-type', 'd',
            '-path', f'*/snapshots/{restore_snapshot}', '-print',
        ], check=False, capture_output=True, text=True)
        if located.returncode != 0:
            raise RuntimeError('Cassandra snapshot source lookup failed')
        snapshot_paths = [
            Path(value.strip()) for value in located.stdout.splitlines()
            if value.strip()
        ]
        snapshot_path = next((
            value for value in snapshot_paths
            if value.parents[1].name.startswith(table + '-')
        ), None)
        if snapshot_path is None:
            raise RuntimeError('Cassandra table snapshot was not found')
        restore_table_path = restore_fixture_root / keyspace / table
        restore_table_path.mkdir(parents=True)
        copied = subprocess.run([
            'docker', 'cp',
            f'{args.fixture_container}:{snapshot_path}/.',
            str(restore_table_path),
        ], check=False, capture_output=True, text=True)
        if copied.returncode != 0 or not any(restore_table_path.iterdir()):
            raise RuntimeError('Cassandra table snapshot copy failed')
        observe('snapshot', 'execute', {
            'action': 'clearsnapshot',
            'arguments': {
                'name': restore_snapshot, 'keyspace': keyspace,
            },
            'confirmation': 'clear-restore-source',
        }, snapshot_target)
        observe('restore', 'inspect', target=restore_target)
        shell_file = workspace / f'{prefix}_object.cql'
        shell_file.write_text(
            'SELECT release_version FROM system.local;\n',
            encoding='utf-8',
        )
        observe('shell', 'inspect', target=shell_target)
        observe('shell', 'execute', {
            'action': 'file', 'arguments': {'path': shell_file.name},
        }, shell_target)
        observe('table', 'truncate', {
            'confirmation': table,
        }, table_target)
        count_result = _execute(
            provider, session['session_id'],
            f'SELECT COUNT(*) AS row_count FROM "{keyspace}"."{table}"',
        )
        if _rows(count_result)[0]['row_count'] != 0:
            raise RuntimeError('table truncate did not remove all rows')
        observe('restore', 'execute', {
            'action': 'sstableloader',
            'arguments': {
                'path': str(restore_table_path.relative_to(workspace)),
            },
            'confirmation': 'restore-sstable-data',
        }, restore_target)
        restored_result = _execute(
            provider, session['session_id'],
            f'SELECT COUNT(*) AS row_count FROM "{keyspace}"."{table}"',
        )
        restored_count = _rows(restored_result)[0]['row_count']
        if restored_count < 1:
            raise RuntimeError('sstableloader restored no table rows')
        observe('identity', 'drop', {
            'confirmation': identity_name,
        }, identity_target)
        observe('aggregate', 'drop', {
            'confirmation': aggregate_name,
        }, aggregate_target)
        observe('function', 'drop', {
            'confirmation': function_name,
        }, function_target)
        observe('trigger', 'drop', {
            'confirmation': trigger_name,
        }, trigger_target)
        observe('index', 'drop', {
            'confirmation': index_name,
        }, index_target)
        observe('materialized-view', 'drop', {
            'confirmation': 'drop-view',
        }, view_target)
        observe('user-defined-type', 'drop', {
            'confirmation': 'drop-type',
        }, type_target)
        observe('table', 'drop', {
            'confirmation': 'drop-table',
        }, table_target)
        observe('keyspace', 'drop', {
            'confirmation': 'drop-keyspace',
        }, keyspace_target)
        observe('role', 'drop', {
            'confirmation': member_role_name,
        }, member_target)
        observe('role', 'drop', {
            'confirmation': role_name,
        }, role_target)
        return {
            'passed_resource_operations': {
                kind: sorted(operations)
                for kind, operations in observed_operations.items()
            },
            'restore_validation': {
                'method': 'native-sstableloader',
                'rows_after_truncate': 0,
                'rows_after_restore': restored_count,
                'passed': restored_count >= 1,
            },
        }

    category('object_operations', object_operations_gate)
    if restore_fixture_root.exists():
        shutil.rmtree(restore_fixture_root)

    def tls_gate():
        if args.tls_mode == 'disabled':
            raise RuntimeError('TLS mode was not selected for this run')
        identity = provider.discover_endpoint(base_request)[
            'verified_runtime'
        ]
        if identity['version'] != EXPECTED_SERVER:
            raise RuntimeError('TLS runtime identity did not match')
        return {'mode': args.tls_mode, 'server': identity['version']}

    category('tls', tls_gate)

    cleanup_failures = []
    try:
        cleanup_cluster, cleanup_session, cleanup_route = client._connect(
            {'route': route}
        )
        try:
            for statement in (
                f"DROP IDENTITY IF EXISTS '{identity_name}'",
                f'DROP ROLE IF EXISTS "{member_role_name}"',
                f'DROP ROLE IF EXISTS "{role_name}"',
                f'DROP KEYSPACE IF EXISTS "{keyspace}"',
            ):
                try:
                    cleanup_session.execute(
                        client._statement(statement, cleanup_route)
                    )
                except Exception as exc:
                    cleanup_failures.append(type(exc).__name__)
        finally:
            client._close_native(cleanup_cluster, cleanup_session)
    except Exception as exc:
        cleanup_failures.append(type(exc).__name__)
    finally:
        provider.close()
        role_password = ''
        secrets_by_reference.clear()

    if args.tls_mode == 'disabled':
        categories['tls'] = 'not_run'
        details.pop('tls', None)
        failures[:] = [item for item in failures
                       if item['category'] != 'tls']

    required_categories = [
        name for name in CATEGORIES
        if name != 'tls' or args.tls_mode != 'disabled'
    ]
    dialect_task_evidence = getattr(
        provider, '_qualification_dialect_task_evidence', []
    )
    expected_dialect_tasks = set(client.admin_dialect_task_ids())
    passed_dialect_tasks = {
        item['task_id'] for item in dialect_task_evidence
        if item.get('status') == 'passed'
    }
    missing_dialect_tasks = sorted(
        expected_dialect_tasks.difference(passed_dialect_tasks)
    )
    report = {
        'schema': 'cdeadmin.cassandra-live-gate.v1',
        'run_id': run_id,
        'server_expected': EXPECTED_SERVER,
        'driver_expected': EXPECTED_DRIVER,
        'route': {
            'host': args.host, 'port': args.port,
            'contact_points': route['contact_points'],
            'local_dc': args.local_dc, 'tls_mode': args.tls_mode,
            'protocol_version': 5,
        },
        'categories': categories,
        'details': details,
        'dialect_task_evidence': dialect_task_evidence,
        'dialect_task_summary': {
            'expected': len(expected_dialect_tasks),
            'passed': len(passed_dialect_tasks),
            'missing': missing_dialect_tasks,
        },
        'failures': failures,
        'cleanup_failures': cleanup_failures,
        'required_passed': all(
            categories[name] == 'passed' for name in required_categories
        ) and not cleanup_failures and not missing_dialect_tasks,
        'transaction_finality_interpreted_by_common_code': False,
        'credential_material_recorded': False,
    }
    passed_operations = {
        kind: sorted(operations)
        for kind, operations in observed_operations.items() if operations
    }
    missing_operations = {
        kind: sorted(required.difference(observed_operations[kind]))
        for kind, required in OBJECT_OPERATIONS.items()
        if required.difference(observed_operations[kind])
    }
    concepts = {}
    for concept, kinds in CONCEPT_BINDINGS.items():
        if all(
            OBJECT_OPERATIONS[kind].issubset(observed_operations[kind])
            for kind in kinds
        ):
            concepts[concept] = {
                'status': 'passed',
                'operations': {
                    kind: passed_operations[kind] for kind in kinds
                },
            }
    report['object_evidence'] = {
        'schema': 'cdeadmin.provider-object-live-evidence.v1',
        'engine_id': 'cassandra',
        'exact_profile': EXPECTED_SERVER,
        'run_id': run_id,
        'evidence_scope': (
            'wide-column-navigator-and-object-editor-operations'
        ),
        'raw_commands_used_for_provider_operations': False,
        'common_transaction_finality_interpreted': False,
        'automatic_mutation_retry': False,
        'passed_resource_operations': passed_operations,
        'missing_resource_operations': missing_operations,
        'operation_failures': [
            item for item in failures
            if item.get('category') == 'object_operations'
        ],
        'concepts': {'wide_column': concepts},
        'passed': report['required_passed'] and not missing_operations,
    }
    return report


def main(argv=None):
    args = parser().parse_args(argv)
    password = os.environ.get('CDEADMIN_CASSANDRA_PASSWORD')
    if password is None:
        parser().error('CDEADMIN_CASSANDRA_PASSWORD is required')
    report = verify(args, password)
    output = json.dumps(report, indent=2, sort_keys=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output + '\n', encoding='utf-8')
    if args.object_output:
        args.object_output.parent.mkdir(parents=True, exist_ok=True)
        args.object_output.write_text(
            json.dumps(
                report['object_evidence'], indent=2, sort_keys=True
            ) + '\n',
            encoding='utf-8',
        )
    print(output)
    return 0 if report['required_passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
