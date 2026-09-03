#!/usr/bin/env python3
##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Qualify CDEadmin against exact ClickHouse 25.12.10.7 over HTTP/JSON."""

from __future__ import annotations

import argparse
import json
import os
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

from pgadmin.cdeadmin.providers.clickhouse.client import (  # noqa: E402
    ClickHouseClient,
    ClickHouseClientError,
)
from pgadmin.cdeadmin.providers.clickhouse.provider import (  # noqa: E402
    ClickHousePilotProvider,
)
from pgadmin.cdeadmin.semantic_models.service import (  # noqa: E402
    SemanticModelService,
)


EXPECTED_RUNTIME = '25.12.10.7'
CATEGORIES = (
    'runtime_identity', 'provider_facade', 'columnar_query',
    'database_table_ddl',
    'insert_and_aggregate', 'semantic_query', 'semantic_materialization',
    'editable_grid', 'columnar_objects',
    'views_and_functions', 'access_control', 'resource_discovery',
    'transaction_boundary', 'cleanup',
)


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
    def __init__(self, acquire_secret):
        self.acquire_secret = acquire_secret

    @staticmethod
    def require(_permission, _scope='endpoint'):
        return None

    @staticmethod
    def allows(_permission, _scope='endpoint'):
        return True


def parser():
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument('--host', default='127.0.0.1')
    value.add_argument('--port', type=int, default=8123)
    value.add_argument('--database', default='default')
    value.add_argument('--username', default='default')
    value.add_argument(
        '--password-environment', default='CDEADMIN_CLICKHOUSE_PASSWORD'
    )
    value.add_argument(
        '--tls-mode',
        choices=('disable', 'require', 'verify-ca', 'verify-full'),
        default='disable',
    )
    value.add_argument('--tls-ca-file')
    value.add_argument('--tls-certificate-file')
    value.add_argument('--tls-key-file')
    value.add_argument('--output', type=Path)
    value.add_argument(
        '--destructive-disposable-runtime', action='store_true',
        help='admit temporary databases and access-control objects',
    )
    return value


def _target(kind, **native):
    return {
        'resource_kind': kind,
        'extensions': {'clickhouse': {'native': native}},
    }


def _execute(client, session, source, parameters=None):
    token = client.execute(session, {
        'source': source, 'parameters': parameters or {},
    })
    return client.describe_result(token)


def _admin(client, route, kind, operation, draft, target=None):
    request = {
        'resource_kind': kind, 'operation_id': operation,
        'draft': draft, '_provider_route': route,
    }
    if target is not None:
        request['target_resource'] = target
    plan = client.plan_admin_operation(request)
    result = client.apply_admin_operation({
        'provider_payload': plan['provider_payload']
    })
    return {'plan': plan['command_preview'], 'result': result}


def _record(evidence, name, callback, failures):
    started = time.monotonic()
    try:
        details = callback() or {}
        evidence['categories'][name] = {
            'status': 'passed', 'details': details,
            'duration_seconds': round(time.monotonic() - started, 3),
        }
    except Exception as exc:
        evidence['categories'][name] = {
            'status': 'failed', 'error_type': type(exc).__name__,
            'duration_seconds': round(time.monotonic() - started, 3),
        }
        failures.append(f'{name}: {type(exc).__name__}: {exc}')


def verify(args):
    password = os.environ.get(args.password_environment)
    secret_reference = 'clickhouse-live-connection'
    new_user_reference = 'clickhouse-live-created-user'

    def acquire(reference, _principal, _purpose, _kind):
        if reference == secret_reference and password is not None:
            return _Lease(password)
        if reference == new_user_reference:
            return _Lease('temporary-clickhouse-live-gate-password')
        raise ClickHouseClientError('qualification secret is unavailable')

    run_id = uuid.uuid4().hex[:12]
    database = f'cdeadmin_{run_id}'
    username = f'cdeadmin_user_{run_id}'
    role = f'cdeadmin_role_{run_id}'
    quota = f'cdeadmin_quota_{run_id}'
    profile = f'cdeadmin_profile_{run_id}'
    policy = f'cdeadmin_policy_{run_id}'
    function = f'cdeadmin_fn_{run_id}'
    semantic_view = f'cdeadmin_semantic_{run_id}'
    route = {
        'route_id': f'clickhouse-live-{run_id}',
        'host': args.host, 'port': args.port,
        'database': args.database, 'username': args.username,
        'tls_mode': args.tls_mode,
        'connect_timeout': 20, 'statement_timeout': 120,
    }
    if password is not None:
        route.update({
            'credential_reference_id': secret_reference,
            'principal_reference': 'cdeadmin-live-qualifier',
        })
    for key in ('tls_ca_file', 'tls_certificate_file', 'tls_key_file'):
        value = getattr(args, key)
        if value:
            route[key] = str(Path(value).resolve())
    client = ClickHouseClient(acquire)
    evidence = {
        'schema': 'cdeadmin.clickhouse-live-gate.v1',
        'expected_runtime': EXPECTED_RUNTIME,
        'protocol': 'http_json', 'started_at': time.time(),
        'categories': {}, 'automatic_mutation_retry': False,
        'common_transaction_finality_interpretation': False,
        'destructive_disposable_runtime': (
            args.destructive_disposable_runtime
        ),
    }
    failures = []
    session = None

    _record(evidence, 'runtime_identity', lambda: client.runtime_identity({
        'route': route
    }), failures)

    def provider_facade():
        facade_client = ClickHouseClient(acquire)
        context = SimpleNamespace(
            endpoint_id=f'clickhouse-live-{run_id}',
            session_namespace=f'clickhouse-live-session-{run_id}',
            mode='legacy_native',
        )
        provider = ClickHousePilotProvider(
            context, _Permissions(acquire), facade_client
        )
        try:
            provider_session = provider.open_session({'route': route})
            operation = provider.execute({
                'session_id': provider_session['session_id'],
                'execution_id': f'clickhouse-live-execution-{run_id}',
                'source': 'SELECT 42 AS answer',
            })
            described = provider.describe_result(operation)
            rows = described['extensions']['clickhouse']['payload']['rows']
            if rows != [{'answer': 42}]:
                raise RuntimeError('provider facade changed columnar rows')
            if not described['complete']:
                raise RuntimeError('provider facade result was not terminal')
            return {
                'provider_id': described['identity']['provider_id'],
                'result_kind': described['result_kind'],
                'exact_identity_checked_on_session_open': True,
            }
        finally:
            provider.close()

    _record(evidence, 'provider_facade', provider_facade, failures)
    try:
        session = client.open_session({'route': route})

        def columnar_query():
            value = _execute(
                client, session,
                "SELECT 'connected' AS state, 42 AS answer",
            )
            if value['payload']['rows'] != [{
                'state': 'connected', 'answer': 42,
            }]:
                raise RuntimeError('columnar values were not preserved')
            if len(value['schema']['columns']) != 2:
                raise RuntimeError('column metadata was not preserved')
            return {
                'result_kind': value['result_kind'],
                'columns': value['schema']['columns'],
            }

        _record(evidence, 'columnar_query', columnar_query, failures)

        if not args.destructive_disposable_runtime:
            for name in CATEGORIES[3:12]:
                evidence['categories'][name] = {
                    'status': 'blocked',
                    'reason': 'destructive_disposable_runtime_not_admitted',
                }
                failures.append(f'{name}: destructive runtime not admitted')
        else:
            def database_table_ddl():
                _admin(client, route, 'database', 'create', {
                    'name': database, 'engine': 'Atomic',
                    'comment': 'CDEadmin exact-profile qualification',
                })
                _admin(client, route, 'table', 'create', {
                    'database': database, 'name': 'events',
                    'columns': [
                        {'name': 'id', 'type': 'UInt64'},
                        {'name': 'category', 'type': 'LowCardinality(String)'},
                        {'name': 'value', 'type': 'Int64'},
                        {'name': 'event_time', 'type': 'DateTime'},
                    ],
                    'engine': 'MergeTree',
                    'partition_by': 'toYYYYMM(event_time)',
                    'primary_key': 'id', 'order_by': 'id',
                })
                return {'database': database, 'table': 'events'}

            _record(
                evidence, 'database_table_ddl', database_table_ddl, failures
            )
            table_target = _target(
                'table', database=database, table='events', name='events'
            )

            def insert_and_aggregate():
                for row in (
                    {'id': 1, 'category': 'one', 'value': 10,
                     'event_time': '2026-09-02 12:00:00'},
                    {'id': 2, 'category': 'one', 'value': 20,
                     'event_time': '2026-09-02 12:01:00'},
                    {'id': 3, 'category': 'two', 'value': 5,
                     'event_time': '2026-09-02 12:02:00'},
                ):
                    _admin(
                        client, route, 'table', 'insert', {'values': row},
                        table_target,
                    )
                value = _execute(
                    client, session,
                    f'SELECT category, sum(value) AS total FROM `{database}`.'
                    '`events` GROUP BY category ORDER BY category',
                )
                if len(value['payload']['rows']) != 2:
                    raise RuntimeError('aggregate rows were not observed')
                return {'aggregate_rows': value['payload']['rows']}

            _record(
                evidence, 'insert_and_aggregate', insert_and_aggregate,
                failures,
            )

            semantic_context = SimpleNamespace(
                endpoint_id=f'clickhouse-semantic-live-{run_id}',
                session_namespace=f'clickhouse-semantic-session-{run_id}',
                mode='legacy_native', runtime_verification_state='verified',
                verified_runtime_family='clickhouse',
                declared_runtime_family='clickhouse',
            )
            semantic_provider = ClickHousePilotProvider(
                semantic_context, _Permissions(acquire),
                ClickHouseClient(acquire),
            )
            semantic_model = {
                'contract_version': '1.0.0',
                'name': 'ClickHouse live semantic model',
                'description': 'Disposable live qualification model',
                'sources': [{
                    'id': 'events',
                    'resource_id': f'clickhouse:{database}:events',
                    'relation': [database, 'events'], 'alias': 'events',
                }],
                'joins': [],
                'dimensions': [{
                    'id': 'category', 'name': 'Category',
                    'field': {'source_id': 'events', 'field': 'category'},
                    'hierarchies': [{
                        'id': 'category_hierarchy', 'name': 'Category',
                        'levels': [{
                            'id': 'category_level', 'name': 'Category',
                            'field': {
                                'source_id': 'events', 'field': 'category',
                            },
                        }],
                    }],
                }],
                'measures': [{
                    'id': 'total_value', 'name': 'Total value',
                    'aggregation': 'sum',
                    'field': {'source_id': 'events', 'field': 'value'},
                    'format': '0',
                }],
                'default_filters': [], 'materializations': [],
                'security': {}, 'annotations': {'qualification': True},
            }
            semantic_query = {
                'axes': {
                    'rows': ['category_level'],
                    'columns': [], 'pages': [],
                },
                'measures': ['total_value'], 'filters': [],
                'totals': False, 'limit': 100,
            }
            semantic_compiled = None

            def semantic_query_check():
                nonlocal semantic_compiled
                provider_session = semantic_provider.open_session({
                    'route': route
                })
                semantic_compiled = semantic_provider.compile_semantic_query(
                    semantic_model, semantic_query
                )
                operation = semantic_provider.execute_analysis({
                    'session_id': provider_session['session_id'],
                    'execution_id': f'clickhouse-semantic-{run_id}',
                    'semantic_model': semantic_model,
                    'semantic_query': semantic_query,
                })
                described = semantic_provider.describe_result(operation)
                rows = described['extensions']['clickhouse']['payload'][
                    'rows'
                ]
                expected = [
                    {'category_level': 'one', 'total_value': 30},
                    {'category_level': 'two', 'total_value': 5},
                ]
                if rows != expected:
                    raise RuntimeError(
                        f'semantic aggregate rows changed: {rows!r}'
                    )
                cellset = SemanticModelService.cellset(
                    semantic_model, semantic_query, rows
                )
                if len(cellset['cells']) != 2 or (
                    cellset['cells'][0]['coordinates']['category_level'] !=
                    'one'
                ):
                    raise RuntimeError(
                        'semantic rows were not preserved as a cellset'
                    )
                return {
                    'compiled_source': semantic_compiled['source'],
                    'language_profile': semantic_compiled[
                        'language_profile'
                    ],
                    'cellset_family': cellset['family'],
                    'cell_count': len(cellset['cells']),
                }

            _record(evidence, 'semantic_query', semantic_query_check, failures)

            def semantic_materialization():
                if semantic_compiled is None:
                    raise RuntimeError(
                        'semantic query did not compile before materialization'
                    )
                plan = semantic_provider.plan_semantic_materialization({
                    'materialization': {
                        'name': semantic_view, 'target': [database],
                    },
                    'compiled': semantic_compiled,
                    '_provider_route': route,
                })
                if plan['state'] != 'ready' or not plan[
                    'execution_available'
                ]:
                    raise RuntimeError(
                        'semantic materialization plan is not executable'
                    )
                result = semantic_provider.apply_visual_admin({
                    'plan_id': plan['plan_id'],
                    'plan_digest': plan['plan_digest'], 'confirmed': True,
                })
                observed = _execute(
                    client, session,
                    'SELECT name, engine FROM system.tables WHERE '
                    f"database = '{database}' AND name = '{semantic_view}'",
                )
                rows = observed['payload']['rows']
                if rows != [{
                    'name': semantic_view, 'engine': 'MaterializedView',
                }]:
                    raise RuntimeError(
                        'semantic materialized view was not observed'
                    )
                return {
                    'plan_schema': plan['schema'],
                    'provider_result_schema': result['schema'],
                    'storage_engine_declared_by_provider': 'MergeTree',
                    'materialized_view': semantic_view,
                }

            _record(
                evidence, 'semantic_materialization',
                semantic_materialization, failures,
            )

            def editable_grid():
                page = client.read_admin_rows({
                    '_provider_route': route,
                    'target_resource': table_target, 'limit': 20,
                })
                if not page['editable'] or len(page['rows']) != 3:
                    raise RuntimeError('editable table page is unavailable')
                row = next(
                    item for item in page['rows']
                    if item['values']['id'] == 1
                )
                _admin(client, route, 'table', 'update', {
                    'selector': {'id': 1},
                    'concurrency_token': row['concurrency_token'],
                    'changes': {'value': 11},
                }, table_target)
                page = client.read_admin_rows({
                    '_provider_route': route,
                    'target_resource': table_target, 'limit': 20,
                })
                row = next(
                    item for item in page['rows']
                    if item['values']['id'] == 3
                )
                _admin(client, route, 'table', 'delete', {
                    'selector': {'id': 3},
                    'concurrency_token': row['concurrency_token'],
                    'acknowledge_delete': True,
                }, table_target)
                value = _execute(
                    client, session,
                    f'SELECT id, value FROM `{database}`.`events` '
                    'ORDER BY id',
                )
                if value['payload']['rows'] != [
                    {'id': 1, 'value': 11}, {'id': 2, 'value': 20},
                ]:
                    raise RuntimeError('grid mutations were not observed')
                return {
                    'primary_key_identity': page['row_identity_columns'],
                    'synchronous_mutations_observed': True,
                }

            _record(evidence, 'editable_grid', editable_grid, failures)

            def columnar_objects():
                index_target = _target(
                    'data-skipping-index', database=database,
                    table='events', name='value_index',
                )
                projection_target = _target(
                    'projection', database=database, table='events',
                    name='category_projection',
                )
                _admin(client, route, 'data-skipping-index', 'create', {
                    'name': 'value_index', 'expression': 'value',
                    'type': 'minmax', 'granularity': 1,
                }, index_target)
                _admin(client, route, 'projection', 'create', {
                    'name': 'category_projection',
                    'expression': 'SELECT category, sum(value) GROUP BY '
                    'category',
                }, projection_target)
                observed = _execute(
                    client, session,
                    'SELECT name FROM system.projections WHERE database = '
                    f"'{database}' AND table = 'events'",
                )
                if not observed['payload']['rows']:
                    raise RuntimeError('projection metadata was not observed')
                return {'projection_and_index_created': True}

            _record(
                evidence, 'columnar_objects', columnar_objects, failures
            )

            def views_and_functions():
                _admin(client, route, 'view', 'create', {
                    'database': database, 'name': 'event_totals',
                    'select': f'SELECT category, sum(value) AS total FROM '
                    f'`{database}`.`events` GROUP BY category',
                })
                _admin(client, route, 'function', 'create', {
                    'name': function, 'lambda': '(x) -> x + 1',
                })
                value = _execute(
                    client, session,
                    f'SELECT {function}(41) AS answer FROM '
                    f'`{database}`.`event_totals` LIMIT 1',
                )
                if value['payload']['rows'][0]['answer'] != 42:
                    raise RuntimeError('user-defined function failed')
                return {'view_and_function_observed': True}

            _record(
                evidence, 'views_and_functions', views_and_functions,
                failures,
            )

            def access_control():
                _admin(client, route, 'role', 'create', {'name': role})
                _admin(client, route, 'user', 'create', {
                    'name': username,
                    'password_reference': new_user_reference,
                })
                _admin(client, route, 'role', 'grant', {
                    'privileges': 'SELECT',
                    'scope': f'`{database}`.*',
                }, _target('role', name=role))
                _execute(client, session, f'GRANT `{role}` TO `{username}`')
                _admin(client, route, 'settings-profile', 'create', {
                    'name': profile,
                    'definition': 'SETTINGS max_threads = 2 TO '
                    f'`{role}`',
                })
                _admin(client, route, 'quota', 'create', {
                    'name': quota,
                    'definition': 'FOR INTERVAL 1 hour MAX queries = 1000 '
                    f'TO `{role}`',
                })
                _admin(client, route, 'row-policy', 'create', {
                    'name': policy,
                    'definition': f'ON `{database}`.`events` USING 1 TO '
                    f'`{role}`',
                })
                security = client.describe_security({'route': route})
                names = {item['name'] for item in security['native']['users']}
                if username not in names:
                    raise RuntimeError('created user was not discovered')
                return {
                    'user': username, 'role': role,
                    'password_recorded': False,
                }

            _record(evidence, 'access_control', access_control, failures)

            def resources():
                values = client.list_resources({'route': route})
                kinds = sorted({item['resource_kind'] for item in values})
                required = {
                    'server', 'database', 'table', 'column', 'partition',
                    'projection', 'data-skipping-index', 'function', 'view',
                    'user', 'role', 'quota', 'settings-profile', 'row-policy',
                }
                if not required.issubset(kinds):
                    missing = sorted(required.difference(kinds))
                    raise RuntimeError(f'resource kinds missing: {missing}')
                return {'count': len(values), 'kinds': kinds}

            _record(evidence, 'resource_discovery', resources, failures)

        def transaction_boundary():
            observed = client.describe_transaction(session)
            if observed['multi_statement_transaction_supported']:
                raise RuntimeError('unsupported common transactions claimed')
            if observed['finality_interpreted_by_common_code']:
                raise RuntimeError('common code interpreted finality')
            if observed['automatic_replay']:
                raise RuntimeError('automatic mutation replay was enabled')
            return observed

        _record(
            evidence, 'transaction_boundary', transaction_boundary, failures
        )
    finally:
        def cleanup():
            if not args.destructive_disposable_runtime or session is None:
                return {'required': False}
            statements = (
                f'DROP ROW POLICY IF EXISTS `{policy}` ON '
                f'`{database}`.`events`',
                f'DROP QUOTA IF EXISTS `{quota}`',
                f'DROP SETTINGS PROFILE IF EXISTS `{profile}`',
                f'DROP USER IF EXISTS `{username}`',
                f'DROP ROLE IF EXISTS `{role}`',
                f'DROP FUNCTION IF EXISTS `{function}`',
                f'DROP VIEW IF EXISTS '
                f'`{database}`.`{semantic_view}`',
                f'DROP DATABASE IF EXISTS `{database}`',
            )
            errors = []
            for source in statements:
                try:
                    _execute(client, session, source)
                except Exception as exc:
                    errors.append(type(exc).__name__)
            if errors:
                raise RuntimeError(f'cleanup failures: {errors}')
            return {'temporary_objects_removed': True}

        _record(evidence, 'cleanup', cleanup, failures)
        if 'semantic_provider' in locals():
            semantic_provider.close()
        client.close()

    for category in CATEGORIES:
        if category not in evidence['categories']:
            evidence['categories'][category] = {'status': 'not_run'}
            failures.append(f'{category}: not run')
    evidence['finished_at'] = time.time()
    evidence['status'] = 'passed' if not failures else 'failed'
    evidence['failure_count'] = len(failures)
    evidence['failures'] = failures
    return evidence


def main():
    args = parser().parse_args()
    evidence = verify(args)
    document = json.dumps(evidence, indent=2, sort_keys=True) + '\n'
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(document, encoding='utf-8')
    sys.stdout.write(document)
    return 0 if evidence['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
