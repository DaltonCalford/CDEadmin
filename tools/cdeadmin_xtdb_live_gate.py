#!/usr/bin/env python3
##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Run the fail-closed XTDB 2.1.0 provider activation suite."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
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

from pgadmin.cdeadmin.providers.xtdb.client import XTDBClient  # noqa: E402
from pgadmin.cdeadmin.providers.xtdb.provider import (  # noqa: E402
    XTDBPilotProvider,
)
from pgadmin.cdeadmin.semantic_models.service import (  # noqa: E402
    SemanticModelService,
)


class _Lease:
    def __init__(self, value):
        self.value = value.encode('utf-8')

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def use(self, callback):
        return callback(memoryview(self.value))


class _Permissions:
    def __init__(self, acquire_secret):
        self.acquire_secret = acquire_secret

    @staticmethod
    def require(_permission, _scope='endpoint'):
        return None

    @staticmethod
    def allows(_permission, _scope='endpoint'):
        return True


def _parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=5432)
    parser.add_argument('--database', default='xtdb')
    parser.add_argument('--username', default='xtdb')
    parser.add_argument('--password-env')
    parser.add_argument('--healthz-url', default='http://127.0.0.1:8080')
    parser.add_argument('--output', type=Path)
    parser.add_argument(
        '--object-evidence', type=Path,
        help='write strict provider-object operation evidence separately',
    )
    parser.add_argument(
        '--destructive-disposable-runtime', action='store_true',
        help='admit isolated ERASE, user, and attach/detach checks',
    )
    return parser


def _execute(client, session, source, parameters=()):
    result = client.describe_result(client.execute(session, {
        'source': source, 'parameters': list(parameters),
    }))
    return result['payload']


def _apply(provider, request):
    plan = provider.plan_visual_admin(request)
    if plan['state'] != 'ready':
        raise RuntimeError('XTDB visual administration plan is not ready')
    result = provider.apply_visual_admin({
        'plan_id': plan['plan_id'],
        'plan_digest': plan['plan_digest'],
        'confirmed': True,
    })
    if result['transaction_finality_interpreted_by_common_code']:
        raise RuntimeError('common code interpreted XTDB finality')
    return result


def _object_evidence(run_id, operations=None):
    operations = {
        kind: sorted(values) for kind, values in (operations or {}).items()
    }
    concepts = {}
    expected = {
        'cluster': ['inspect'],
        'node': ['inspect'],
        'database': ['create', 'drop', 'inspect'],
        'schema': ['inspect'],
        'table': ['create', 'delete', 'erase', 'insert', 'inspect', 'update'],
        'column': ['inspect'],
        'document': ['delete', 'erase', 'insert', 'inspect', 'update'],
        'entity': ['delete', 'erase', 'insert', 'inspect', 'update'],
        'valid-time': ['inspect'],
        'system-time': ['inspect'],
        'transaction': ['inspect'],
        'transaction-log': ['inspect'],
        'user': ['alter', 'create', 'inspect'],
    }
    passed = operations == expected
    if passed:
        concepts = {
            'document': {
                'databases': {'status': 'passed', 'operations': {
                    'database': operations['database'],
                }},
                'collections': {'status': 'passed', 'operations': {
                    'table': operations['table'],
                }},
                'documents': {'status': 'passed', 'operations': {
                    'document': operations['document'],
                }},
                'users_and_roles': {'status': 'passed', 'operations': {
                    'user': operations['user'],
                }},
            },
            'relational': {
                'servers': {'status': 'passed', 'operations': {
                    'cluster': operations['cluster'],
                    'node': operations['node'],
                }},
                'databases': {'status': 'passed', 'operations': {
                    'database': operations['database'],
                }},
                'schemas': {'status': 'passed', 'operations': {
                    'schema': operations['schema'],
                }},
                'tables': {'status': 'passed', 'operations': {
                    'table': operations['table'],
                }},
                'columns': {'status': 'passed', 'operations': {
                    'column': operations['column'],
                }},
            },
            'bitemporal': {
                'entities': {'status': 'passed', 'operations': {
                    'entity': operations['entity'],
                }},
                'valid_time_history': {
                    'status': 'passed', 'operations': {
                        'valid-time': operations['valid-time'],
                    },
                },
                'system_time_history': {
                    'status': 'passed', 'operations': {
                        'system-time': operations['system-time'],
                    },
                },
                'transactions': {'status': 'passed', 'operations': {
                    'transaction': operations['transaction'],
                }},
                'transaction_log': {'status': 'passed', 'operations': {
                    'transaction-log': operations['transaction-log'],
                }},
            },
        }
    missing = {
        kind: sorted(set(values).difference(operations.get(kind, [])))
        for kind, values in expected.items()
        if set(values).difference(operations.get(kind, []))
    }
    return {
        'schema': 'cdeadmin.provider-object-live-evidence.v1',
        'engine_id': 'xtdb', 'exact_profile': '2.1.0',
        'run_id': run_id,
        'evidence_scope': (
            'document-relational-bitemporal-navigator-and-editor-operations'
        ),
        'raw_commands_used': False,
        'common_transaction_finality_interpreted': False,
        'passed_resource_operations': operations,
        'operation_failures': missing,
        'concepts': concepts,
        'passed': passed,
    }


def main():
    args = _parser().parse_args()
    password = None
    if args.password_env:
        password = os.environ.get(args.password_env)
        if password is None:
            raise SystemExit('password environment variable is absent')

    def acquire(reference, _principal, _purpose, _secret_type):
        if reference == 'xtdb-live-new-user-password':
            return _Lease('temporary-live-gate-password')
        return _Lease(password or '')

    route = {
        'route_id': 'xtdb-live-gate', 'host': args.host, 'port': args.port,
        'database': args.database, 'username': args.username,
        'tls_mode': 'disable', 'connect_timeout': 20,
        'statement_timeout': 60, 'application_name': 'CDEadmin-live-gate',
        'healthz_url': args.healthz_url,
        'principal_reference': 'xtdb-live-gate-principal',
    }
    if password is not None:
        route.update({
            'credential_reference_id': 'live-gate-secret',
        })
    client = XTDBClient(acquire)
    evidence = {
        'schema': 'cdeadmin.xtdb-live-gate.v1',
        'expected_version': '2.1.0', 'driver_version': '3.3.4',
        'started_at': time.time(), 'categories': {},
        'automatic_mutation_retry': False,
        'common_finality_interpretation': False,
    }

    def category(name, callback):
        started = time.monotonic()
        try:
            details = callback() or {}
            evidence['categories'][name] = {
                'status': 'passed', 'details': details,
                'duration_seconds': round(time.monotonic() - started, 3),
            }
        except Exception as exc:
            evidence['categories'][name] = {
                'status': 'failed', 'exception_type': type(exc).__name__,
                'error_message': str(exc),
                'duration_seconds': round(time.monotonic() - started, 3),
            }

    category('runtime_identity', lambda: client.runtime_identity({
        'route': route
    }))

    def health():
        states = {}
        for check in ('started', 'alive', 'ready'):
            url = args.healthz_url.rstrip('/') + '/healthz/' + check
            with urllib.request.urlopen(url, timeout=10) as response:
                states[check] = {
                    'status': response.status,
                    'body': response.read(256).decode('utf-8', 'replace'),
                }
        return states

    category('health', health)

    def finish_block():
        plan = client.plan_admin_operation({
            'resource_kind': 'health',
            'operation_id': 'execute',
            'target_resource': {
                'native': {'healthz_url': args.healthz_url},
            },
            'draft': {
                'action': 'finish-block',
                'acknowledge_operation': True,
            },
            '_provider_route': route,
        })
        result = client.apply_admin_operation({
            'provider_payload': plan['provider_payload'],
        })
        if result.get('http_status') != 200:
            raise RuntimeError('XTDB finish-block did not return HTTP 200')
        return result

    category('finish_block', finish_block)
    session = None
    passed_visual_operations = {}
    suffix = uuid.uuid4().hex[:12]
    table = f'cdeadmin_xtdb_{suffix}'
    attached = f'cdeadmin_db_{suffix}'
    username = f'cdeadmin_user_{suffix}'
    try:
        session = client.open_session({'route': route})
        category('sql', lambda: _execute(
            client, session, 'SELECT %s AS value', ('connected',)
        ))

        def documents():
            _execute(
                client, session,
                f'INSERT INTO "public"."{table}" '
                '(_id, name, nested, region, amount) '
                'VALUES (%s, %s, %s, %s, %s)',
                ('one', 'Ada', {'language': 'analytical engine'},
                 'North', 42.5),
            )
            payload = _execute(
                client, session,
                f'SELECT * FROM "public"."{table}" WHERE _id = %s',
                ('one',),
            )
            if not payload['rows']:
                raise RuntimeError('inserted XTDB document was not observed')
            return {'rows_observed': len(payload['rows'])}

        category('document_crud', documents)

        def bitemporal():
            _execute(
                client, session,
                f'UPDATE "public"."{table}" SET name = %s '
                'WHERE _id = %s', ('Grace', 'one'),
            )
            payload = _execute(
                client, session,
                f'SELECT *, _valid_from, _system_from '
                f'FROM "public"."{table}" FOR ALL VALID_TIME '
                'FOR ALL SYSTEM_TIME WHERE _id = %s', ('one',),
            )
            if len(payload['rows']) < 2:
                raise RuntimeError('XTDB system-time history was not observed')
            return {'history_rows_observed': len(payload['rows'])}

        category('bitemporal_history', bitemporal)

        def semantic_query():
            context = SimpleNamespace(
                endpoint_id=f'xtdb-semantic-{suffix}',
                session_namespace=f'xtdb-semantic-session-{suffix}',
                mode='legacy_native', runtime_verification_state='verified',
                verified_runtime_family='xtdb',
                declared_runtime_family='xtdb',
            )
            provider = XTDBPilotProvider(
                context, _Permissions(acquire), XTDBClient(acquire)
            )
            model = {
                'contract_version': '1.0.0',
                'name': 'XTDB live semantic model',
                'description': 'Disposable live qualification model',
                'sources': [{
                    'id': 'documents',
                    'resource_id': f'xtdb:public:{table}',
                    'relation': ['public', table], 'alias': 'documents',
                }],
                'joins': [],
                'dimensions': [{
                    'id': 'region', 'name': 'Region',
                    'field': {'source_id': 'documents', 'field': 'region'},
                    'hierarchies': [{
                        'id': 'region_hierarchy', 'name': 'Region',
                        'levels': [{
                            'id': 'region_level', 'name': 'Region',
                            'field': {
                                'source_id': 'documents', 'field': 'region',
                            },
                        }],
                    }],
                }],
                'measures': [{
                    'id': 'total_amount', 'name': 'Total amount',
                    'aggregation': 'sum',
                    'field': {'source_id': 'documents', 'field': 'amount'},
                    'format': '0',
                }],
                'default_filters': [], 'materializations': [],
                'security': {}, 'annotations': {'qualification': True},
            }
            query = {
                'axes': {
                    'rows': ['region_level'],
                    'columns': [], 'pages': [],
                },
                'measures': ['total_amount'], 'filters': [],
                'totals': False, 'limit': 100,
            }
            try:
                provider_session = provider.open_session({'route': route})
                compiled = provider.compile_semantic_query(model, query)
                operation = provider.execute_analysis({
                    'session_id': provider_session['session_id'],
                    'execution_id': f'xtdb-semantic-{suffix}',
                    'semantic_model': model, 'semantic_query': query,
                })
                described = provider.describe_result(operation)
                rows = described['extensions']['xtdb']['payload']['rows']
                if len(rows) != 1 or rows[0].get('region_level') != 'North':
                    raise RuntimeError(
                        f'XTDB semantic dimension changed: {rows!r}'
                    )
                if float(rows[0].get('total_amount')) != 42.5:
                    raise RuntimeError(
                        f'XTDB semantic aggregate changed: {rows!r}'
                    )
                cellset = SemanticModelService.cellset(model, query, rows)
                if cellset['family'] != 'cellset' or len(
                    cellset['cells']
                ) != 1:
                    raise RuntimeError(
                        'XTDB semantic rows were not preserved as a cellset'
                    )
                return {
                    'compiled_source': compiled['source'],
                    'language_profile': compiled['language_profile'],
                    'cellset_family': cellset['family'],
                    'cell_count': len(cellset['cells']),
                }
            finally:
                provider.close()

        category('semantic_query', semantic_query)

        def transaction():
            _execute(client, session, 'BEGIN READ ONLY')
            _execute(client, session, 'SELECT 1 AS value')
            _execute(client, session, 'COMMIT')
            state = client.describe_transaction(session)
            if state['finality_interpreted_by_common_code']:
                raise RuntimeError('common code interpreted XTDB finality')
            return state

        category('transaction_observation', transaction)
        category('resources', lambda: {
            'resource_count': len(client.list_resources({'route': route})),
            'security': client.describe_security({'route': route})['native'],
        })

        if args.destructive_disposable_runtime:
            def databases():
                _execute(client, session, f'ATTACH DATABASE "{attached}"')
                _execute(client, session, f'DETACH DATABASE "{attached}"')
                return {'attach_and_detach_observed': True}

            category('multi_database', databases)

            def users():
                _execute(
                    client, session,
                    f'CREATE USER {username} WITH PASSWORD '
                    "'temporary-live-gate-password'",
                )
                _execute(
                    client, session,
                    f'ALTER USER {username} WITH PASSWORD '
                    "'temporary-live-gate-password-two'",
                )
                observed = _execute(
                    client, session,
                    'SELECT username, usesuper FROM pg_catalog.pg_user '
                    'WHERE username = %s', (username,),
                )
                if len(observed['rows']) != 1 or (
                    observed['rows'][0].get('username') != username
                ):
                    raise RuntimeError(
                        'XTDB user name was not stored exactly as submitted'
                    )
                return {
                    'create_and_alter_observed': True,
                    'exact_username_observed': True,
                    'password_recorded': False,
                }

            category('user_administration', users)

            def visual_administration():
                context = SimpleNamespace(
                    endpoint_id=f'xtdb-visual-{suffix}',
                    session_namespace=f'xtdb-visual-session-{suffix}',
                    mode='legacy_native',
                    runtime_verification_state='verified',
                    verified_runtime_family='xtdb',
                    declared_runtime_family='xtdb',
                    effective_permissions=frozenset({
                        'data_read', 'data_write', 'administer',
                    }),
                )
                provider = XTDBPilotProvider(
                    context, _Permissions(acquire), XTDBClient(acquire)
                )
                request = {'route': route}
                observed = {}

                def record(kind, operation, target, draft=None):
                    result = _apply(provider, {
                        'resource_kind': kind,
                        'operation_id': operation,
                        'target_resource': target,
                        'draft': draft or {},
                        '_provider_route': route,
                    })
                    observed.setdefault(kind, set()).add(operation)
                    passed_visual_operations[kind] = sorted(observed[kind])
                    return result

                def find(resources, kind, name=None):
                    matches = [
                        resource for resource in resources
                        if resource['resource_kind'] == kind and (
                            name is None or resource['display_name'] == name
                        )
                    ]
                    if not matches:
                        raise RuntimeError(
                            f'XTDB {kind} resource was not discovered'
                        )
                    return matches[0]

                def row_record(target, document_id, history=False):
                    page = provider.read_visual_admin_rows({
                        'target_resource': target,
                        '_provider_route': route,
                        'limit': 200,
                        'filter': ({
                            'valid_time_mode': 'all',
                            'system_time_mode': 'all',
                        } if history else {}),
                    })
                    return next((
                        item for item in page['rows']
                        if item['values'].get('_id') == document_id
                    ), None)

                def row_identity(target, document_id):
                    row = row_record(target, document_id)
                    if row is None:
                        raise RuntimeError(
                            f'XTDB document {document_id!r} was not observed'
                        )
                    return row['identity_token']

                def exercise_documents(kind, target, prefix):
                    record(kind, 'inspect', target, {'limit': 20})
                    update_id = f'{prefix}-update'
                    erase_id = f'{prefix}-erase'
                    record(kind, 'insert', target, {
                        'values': {'_id': update_id, 'name': 'before'},
                        'options': {},
                    })
                    token = row_identity(target, update_id)
                    record(kind, 'update', target, {
                        'selector': {'identity_token': token},
                        'changes': {'name': 'after'},
                        'concurrency_token': token,
                        'options': {},
                    })
                    updated = row_record(target, update_id)
                    if updated is None or updated['values'].get(
                            'name') != 'after':
                        raise RuntimeError(
                            'XTDB visual update post-state was not observed'
                        )
                    token = row_identity(target, update_id)
                    record(kind, 'delete', target, {
                        'selector': {'identity_token': token},
                        'concurrency_token': token,
                        'confirmation': 'delete XTDB qualification row',
                        'options': {},
                    })
                    if row_record(target, update_id) is not None:
                        raise RuntimeError(
                            'XTDB visual delete post-state was not observed'
                        )
                    record(kind, 'insert', target, {
                        'values': {'_id': erase_id, 'name': 'erase me'},
                        'options': {},
                    })
                    token = row_identity(target, erase_id)
                    record(kind, 'erase', target, {
                        'row_identity': token,
                        'acknowledge_irreversible': True,
                    })
                    if row_record(target, erase_id, history=True) is not None:
                        raise RuntimeError(
                            'XTDB visual erase post-state was not observed'
                        )

                try:
                    resources = provider.list_resources(request)
                    for kind in ('cluster', 'node', 'database', 'schema'):
                        record(kind, 'inspect', find(resources, kind))

                    visual_table = f'cdeadmin_visual_{suffix}'
                    record('table', 'create', None, {
                        'schema': 'public', 'name': visual_table,
                        'values': {
                            '_id': 'seed', 'name': 'visual qualification',
                        },
                        'options': {},
                    })
                    resources = provider.list_resources(request)
                    table_name = f'public.{visual_table}'
                    table_target = find(resources, 'table', table_name)
                    column_target = next((
                        resource for resource in resources
                        if resource['resource_kind'] == 'column' and
                        resource['display_name'].startswith(table_name + '.')
                    ), None)
                    if column_target is None:
                        raise RuntimeError('XTDB column was not discovered')
                    record('column', 'inspect', column_target)
                    exercise_documents('table', table_target, 'table')

                    resources = provider.list_resources(request)
                    exercise_documents(
                        'document', find(resources, 'document', table_name),
                        'document',
                    )
                    resources = provider.list_resources(request)
                    exercise_documents(
                        'entity', find(resources, 'entity', table_name),
                        'entity',
                    )

                    resources = provider.list_resources(request)
                    record(
                        'valid-time', 'inspect',
                        find(resources, 'valid-time', table_name),
                    )
                    record(
                        'system-time', 'inspect',
                        find(resources, 'system-time', table_name),
                    )
                    record(
                        'transaction', 'inspect',
                        find(resources, 'transaction'),
                    )
                    record(
                        'transaction-log', 'inspect',
                        find(resources, 'transaction-log'),
                    )

                    created_user = f'visual_user_{suffix}'
                    record('user', 'create', None, {
                        'username': created_user,
                        'password_reference': (
                            'xtdb-live-new-user-password'
                        ),
                    })
                    resources = provider.list_resources(request)
                    user_target = find(resources, 'user', created_user)
                    record('user', 'inspect', user_target)
                    record('user', 'alter', user_target, {
                        'password_reference': (
                            'xtdb-live-new-user-password'
                        ),
                    })

                    record('database', 'create', None, {
                        'name': attached + '_visual',
                        'config_yaml': (
                            'log: !InMemory\n'
                            'storage: !InMemory\n'
                        ),
                    })
                    database_target = {
                        'resource_id': 'xtdb:database:' + attached + '_visual',
                        'resource_kind': 'database',
                        'display_name': attached + '_visual',
                        'extensions': {'xtdb': {'native': {
                            'database': attached + '_visual',
                        }}},
                    }
                    record('database', 'drop', database_target, {
                        'acknowledge_detach': True,
                    })
                finally:
                    provider.close()

                strict = _object_evidence(
                    f'xtdb-2.1.0-{suffix}', passed_visual_operations
                )
                if not strict['passed']:
                    raise RuntimeError(
                        'XTDB visual operation matrix is incomplete: ' +
                        repr(strict['operation_failures'])
                    )
                return {
                    'resource_operation_count': sum(
                        len(values) for values in observed.values()
                    ),
                    'resource_kinds': sorted(observed),
                    'raw_commands_used': False,
                    'common_finality_interpreted': False,
                }

            category('visual_administration', visual_administration)
        else:
            for name in (
                'multi_database', 'user_administration',
                'visual_administration',
            ):
                evidence['categories'][name] = {
                    'status': 'blocked',
                    'reason': 'disposable runtime admission was not supplied',
                }
    finally:
        if session is not None and args.destructive_disposable_runtime:
            category('isolated_cleanup', lambda: _execute(
                client, session,
                f'ERASE FROM "public"."{table}" WHERE _id = %s', ('one',),
            ))
        client.close()

    required = {
        'runtime_identity', 'health', 'finish_block', 'sql', 'document_crud',
        'bitemporal_history', 'semantic_query', 'transaction_observation',
        'resources',
        'multi_database', 'user_administration', 'isolated_cleanup',
        'visual_administration',
    }
    evidence['completed_at'] = time.time()
    evidence['passed'] = all(
        evidence['categories'].get(name, {}).get('status') == 'passed'
        for name in required
    )
    evidence['object_experience_evidence'] = _object_evidence(
        f'xtdb-2.1.0-{suffix}', passed_visual_operations
    )
    document = json.dumps(
        evidence, indent=2, sort_keys=True, default=str
    ) + '\n'
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(document, encoding='utf-8')
    if args.object_evidence:
        args.object_evidence.parent.mkdir(parents=True, exist_ok=True)
        args.object_evidence.write_text(
            json.dumps(
                evidence['object_experience_evidence'], indent=2,
                sort_keys=True,
            ) + '\n',
            encoding='utf-8',
        )
    print(document, end='')
    return 0 if evidence['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
