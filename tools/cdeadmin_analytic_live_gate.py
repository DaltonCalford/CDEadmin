#!/usr/bin/env python3
##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Qualify exact analytic engines through their CDEadmin provider clients."""

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

from pgadmin.cdeadmin.providers.influxdb.client import (  # noqa: E402
    InfluxDBClient,
)
from pgadmin.cdeadmin.providers.influxdb.provider import (  # noqa: E402
    InfluxDBPilotProvider,
)
from pgadmin.cdeadmin.providers.milvus.client import (  # noqa: E402
    MilvusClientAdapter,
)
from pgadmin.cdeadmin.providers.opensearch.client import (  # noqa: E402
    OpenSearchClient,
)
from pgadmin.cdeadmin.providers.opensearch_sql_ppl.client import (  # noqa: E402
    OpenSearchSQLPPLClient,
)
from pgadmin.cdeadmin.semantic_models.service import (  # noqa: E402
    SemanticModelService,
)


EXPECTED = {
    'influxdb': '3.9.0',
    'milvus': '2.6.5',
    'opensearch': '3.6.0',
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
    value.add_argument(
        '--engine', required=True,
        choices=('influxdb', 'milvus', 'opensearch'),
    )
    value.add_argument('--host', default='127.0.0.1')
    value.add_argument('--port', type=int)
    value.add_argument('--database')
    value.add_argument('--username')
    value.add_argument('--auth-kind', choices=('none', 'basic', 'bearer'))
    value.add_argument('--password-environment')
    value.add_argument(
        '--tls-mode',
        choices=('disable', 'require', 'verify-ca', 'verify-full'),
        default='disable',
    )
    value.add_argument('--tls-ca-file')
    value.add_argument('--tls-certificate-file')
    value.add_argument('--tls-key-file')
    value.add_argument('--output', type=Path)
    value.add_argument('--object-output', type=Path)
    value.add_argument(
        '--destructive-disposable-runtime', action='store_true',
        help='admit temporary databases, collections, indexes, and data',
    )
    return value


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


def _route(args, reference, password):
    defaults = {
        'influxdb': (8181, 'default', None, 'none'),
        'milvus': (19530, 'default', None, None),
        'opensearch': (9200, None, None, 'none'),
    }
    port, database, username, auth_kind = defaults[args.engine]
    route = {
        'route_id': f'{args.engine}-live-{uuid.uuid4().hex[:12]}',
        'host': args.host, 'port': args.port or port,
        'tls_mode': args.tls_mode, 'connect_timeout': 30,
    }
    if args.engine == 'milvus':
        route['operation_timeout'] = 120
        route['consistency_level'] = 'Strong'
        route['auth_kind'] = 'basic' if password is not None else 'none'
    else:
        route['statement_timeout'] = 120
        route['auth_kind'] = args.auth_kind or auth_kind
    if args.database or database:
        route['database'] = args.database or database
    if args.username or username:
        route['username'] = args.username or username
    if password is not None:
        route.update({
            'credential_reference_id': reference,
            'principal_reference': 'cdeadmin-analytic-live-gate',
        })
        if route.get('auth_kind') == 'none' and args.engine != 'milvus':
            route['auth_kind'] = 'basic'
    for field in (
        'tls_ca_file', 'tls_certificate_file', 'tls_key_file'
    ):
        selected = getattr(args, field)
        if selected:
            route[field] = str(Path(selected).resolve())
    return route


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
    return {'preview': plan['command_preview'], 'result': result}


def _target(engine, kind, **native):
    return {
        'resource_kind': kind,
        'extensions': {engine: {'native': native}},
    }


def _influxdb(client, route, run_id, destructive, acquire):
    evidence = {}
    required_operations = {
        'table': {'inspect', 'create', 'insert', 'drop'},
        'tag': {'inspect'}, 'field': {'inspect'},
        'retention-policy': {'inspect', 'alter'},
        'last-cache': {'inspect', 'create', 'drop'},
        'distinct-cache': {'inspect', 'create', 'drop'},
        'processing-engine': {'inspect', 'execute'},
        'trigger': {'inspect', 'execute'},
        'plugin': {'inspect', 'execute'},
    }
    observed = {kind: set() for kind in required_operations}

    def admin(kind, operation, draft, target=None):
        result = _admin(client, route, kind, operation, draft, target)
        if kind in observed:
            observed[kind].add(operation)
        return result

    identity = client.runtime_identity({'route': route})
    if identity['version'] != EXPECTED['influxdb']:
        raise RuntimeError('InfluxDB exact identity changed')
    evidence['runtime_identity'] = identity
    resources = client.list_resources({'route': route})
    evidence['resource_kinds'] = sorted({
        item['resource_kind'] for item in resources
    })
    if not destructive:
        evidence['destructive_scope'] = 'not_admitted'
        return evidence
    database = f'cdeadmin_{run_id}'
    table = f'metrics_{run_id}'
    last_cache = f'last_{run_id}'
    distinct_cache = f'distinct_{run_id}'
    plugin_file = f'cdeadmin_{run_id}.py'
    trigger = f'trigger_{run_id}'
    created_database = False
    created_table = False
    created_last_cache = False
    created_distinct_cache = False
    created_trigger = False
    try:
        _admin(client, route, 'database', 'create', {'name': database})
        created_database = True
        changed = dict(route, database=database)
        session = client.open_session({'route': changed})
        try:
            sql = client.describe_result(client.execute(session, {
                'source': 'SELECT 42 AS answer', 'language': 'sql',
            }))
            evidence['sql'] = {
                'kind': sql['result_kind'],
                'rows': len(sql['payload']['points']),
            }
        finally:
            session.close()
        admin('table', 'create', {
            'database': database, 'name': table, 'tags': ['host'],
            'fields': [{'name': 'value', 'type': 'float64'}],
        })
        created_table = True
        admin(
            'table', 'insert', {
                'line_protocol': f'{table},host=gate value=42.5',
                'precision': 'nanosecond', 'accept_partial': False,
            }, _target('influxdb', 'table', db=database, name=table),
        )
        table_target = _target(
            'influxdb', 'table', database=database,
            table_name=table, name=table,
        )
        admin('table', 'inspect', {}, table_target)
        tag_target = _target(
            'influxdb', 'tag', database=database, table=table,
            column_name='host', name='host',
        )
        field_target = _target(
            'influxdb', 'field', database=database, table=table,
            column_name='value', name='value',
        )
        admin('tag', 'inspect', {}, tag_target)
        admin('field', 'inspect', {}, field_target)
        retention_target = _target(
            'influxdb', 'retention-policy', database=database,
            name='retention',
        )
        admin('retention-policy', 'inspect', {}, retention_target)
        admin('retention-policy', 'alter', {
            'retention_period': '7d', 'retain_forever': False,
        }, retention_target)

        admin('last-cache', 'create', {
            'database': database, 'table': table, 'name': last_cache,
            'key_columns': ['host'], 'value_columns': ['value'],
            'count': 2, 'ttl': 300,
        })
        created_last_cache = True
        last_target = _target(
            'influxdb', 'last-cache', database=database, table=table,
            name=last_cache,
        )
        admin('last-cache', 'inspect', {}, last_target)
        admin('distinct-cache', 'create', {
            'database': database, 'table': table, 'name': distinct_cache,
            'columns': ['host'], 'max_cardinality': 100,
            'max_age_seconds': 300,
        })
        created_distinct_cache = True
        distinct_target = _target(
            'influxdb', 'distinct-cache', database=database, table=table,
            name=distinct_cache,
        )
        admin('distinct-cache', 'inspect', {}, distinct_target)

        plugin_source = (
            'def process_writes(influxdb3_local, table_batches, args=None):\n'
            '    influxdb3_local.info("CDEadmin qualified")\n'
        )
        admin('plugin', 'execute', {
            'action': 'upload-file', 'database': database,
            'plugin_name': plugin_file, 'content': plugin_source,
            'acknowledge_operation': True,
        })
        admin('trigger', 'execute', {
            'action': 'create', 'database': database,
            'trigger_name': trigger, 'plugin_filename': plugin_file,
            'trigger_kind': 'table', 'trigger_value': table,
            'arguments': {'qualification': 'true'},
            'run_async': False, 'error_behavior': 'log',
            'disabled': True, 'acknowledge_operation': True,
        })
        created_trigger = True
        admin('plugin', 'execute', {
            'action': 'update-file', 'database': database,
            # The update endpoint resolves an installed plugin through its
            # catalogued trigger name; uploads are addressed by filename.
            'plugin_name': trigger, 'content': plugin_source,
            'acknowledge_operation': True,
        })
        trigger_target = _target(
            'influxdb', 'trigger', database=database,
            trigger_name=trigger, name=trigger,
        )
        admin('trigger', 'inspect', {}, trigger_target)
        plugin_target = _target(
            'influxdb', 'plugin', database='_internal',
            plugin_name=trigger, name=trigger,
        )
        admin('plugin', 'inspect', {}, plugin_target)
        admin('trigger', 'execute', {
            'action': 'enable', 'database': database,
            'trigger_name': trigger, 'acknowledge_operation': True,
        })
        admin('trigger', 'execute', {
            'action': 'disable', 'database': database,
            'trigger_name': trigger, 'acknowledge_operation': True,
        })
        admin('processing-engine', 'inspect', {}, _target(
            'influxdb', 'processing-engine', database=database,
            name='processing-engine',
        ))
        tested = admin('processing-engine', 'execute', {
            'action': 'test-wal', 'database': database,
            'plugin_filename': plugin_file,
            'input_line_protocol': f'{table},host=test value=1.5',
            'arguments': {'qualification': 'true'},
            'acknowledge_operation': True,
        })
        errors = tested['result']['native_response'].get('errors', [])
        if errors:
            raise RuntimeError(f'InfluxDB plugin test failed: {errors!r}')
        session = client.open_session({'route': changed})
        try:
            result = client.describe_result(client.execute(session, {
                'source': f'SELECT * FROM "{table}"', 'language': 'sql',
            }))
        finally:
            session.close()
        if not result['payload']['points']:
            raise RuntimeError('InfluxDB did not return the inserted point')
        evidence['mutation_round_trip'] = True

        context = SimpleNamespace(
            endpoint_id=f'influxdb-semantic-{run_id}',
            session_namespace=f'influxdb-semantic-session-{run_id}',
            mode='legacy_native', runtime_verification_state='verified',
            verified_runtime_family='influxdb',
            declared_runtime_family='influxdb',
        )
        provider = InfluxDBPilotProvider(
            context, _Permissions(acquire), InfluxDBClient(acquire)
        )
        model = {
            'contract_version': '1.0.0',
            'name': 'InfluxDB live semantic model',
            'description': 'Disposable live qualification model',
            'sources': [{
                'id': 'metrics',
                'resource_id': f'influxdb:{database}:{table}',
                'relation': [table], 'alias': 'metrics',
            }],
            'joins': [],
            'dimensions': [{
                'id': 'host', 'name': 'Host',
                'field': {'source_id': 'metrics', 'field': 'host'},
                'hierarchies': [{
                    'id': 'host_hierarchy', 'name': 'Host',
                    'levels': [{
                        'id': 'host_level', 'name': 'Host',
                        'field': {'source_id': 'metrics', 'field': 'host'},
                    }],
                }],
            }],
            'measures': [{
                'id': 'total_value', 'name': 'Total value',
                'aggregation': 'sum',
                'field': {'source_id': 'metrics', 'field': 'value'},
                'format': '0.0',
            }],
            'default_filters': [], 'materializations': [],
            'security': {}, 'annotations': {'qualification': True},
        }
        query = {
            'axes': {
                'rows': ['host_level'], 'columns': [], 'pages': [],
            },
            'measures': ['total_value'], 'filters': [],
            'totals': False, 'limit': 100,
        }
        try:
            provider_session = provider.open_session({'route': changed})
            compiled = provider.compile_semantic_query(model, query)
            operation = provider.execute_analysis({
                'session_id': provider_session['session_id'],
                'execution_id': f'influxdb-semantic-{run_id}',
                'semantic_model': model, 'semantic_query': query,
            })
            described = provider.describe_result(operation)
            points = described['extensions']['influxdb']['payload']['points']
            if len(points) != 1 or points[0].get('host_level') != 'gate':
                raise RuntimeError(
                    f'InfluxDB semantic dimension changed: {points!r}'
                )
            if float(points[0].get('total_value')) != 42.5:
                raise RuntimeError(
                    f'InfluxDB semantic aggregate changed: {points!r}'
                )
            cellset = SemanticModelService.cellset(model, query, points)
            if cellset['family'] != 'cellset' or len(
                cellset['cells']
            ) != 1:
                raise RuntimeError(
                    'InfluxDB semantic points did not become a cellset'
                )
            evidence['semantic_query'] = {
                'compiled_source': compiled['source'],
                'language_profile': compiled['language_profile'],
                'cellset_family': cellset['family'],
                'cell_count': len(cellset['cells']),
            }
        finally:
            provider.close()
    finally:
        changed = dict(route, database=database)
        if created_trigger:
            admin('trigger', 'execute', {
                'action': 'delete', 'database': database,
                'trigger_name': trigger, 'force': True,
                'acknowledge_operation': True,
            })
        if created_distinct_cache:
            admin(
                'distinct-cache', 'drop', {'acknowledge_drop': True},
                _target(
                    'influxdb', 'distinct-cache', database=database,
                    table=table, name=distinct_cache,
                ),
            )
        if created_last_cache:
            admin(
                'last-cache', 'drop', {'acknowledge_drop': True},
                _target(
                    'influxdb', 'last-cache', database=database,
                    table=table, name=last_cache,
                ),
            )
        if created_table:
            admin(
                'table', 'drop', {
                    'acknowledge_drop': True, 'hard_delete_mode': 'now',
                },
                _target('influxdb', 'table', db=database, name=table),
            )
        if created_database:
            _admin(
                client, route, 'database', 'drop',
                {
                    'acknowledge_drop': True, 'hard_delete_mode': 'now',
                },
                _target('influxdb', 'database', name=database),
            )
    semantic_passed = 'semantic_query' in evidence
    concepts = {
        'time_series': {
            'measurements_or_tables': {
                'status': 'passed',
                'operations': {'table': sorted(observed['table'])},
            },
            'tags': {
                'status': 'passed',
                'operations': {'tag': sorted(observed['tag'])},
            },
            'fields': {
                'status': 'passed',
                'operations': {'field': sorted(observed['field'])},
            },
            'retention': {
                'status': 'passed',
                'operations': {'retention-policy': sorted(
                    observed['retention-policy']
                )},
            },
            'processing': {
                'status': 'passed',
                'operations': {
                    kind: sorted(observed[kind])
                    for kind in ('processing-engine', 'trigger', 'plugin')
                },
            },
            'caches': {
                'status': 'passed',
                'operations': {
                    kind: sorted(observed[kind])
                    for kind in ('last-cache', 'distinct-cache')
                },
            },
        },
        'semantic': {
            concept: {
                'status': 'passed' if semantic_passed else 'failed',
                'operations': {},
            }
            for concept in (
                'cubes', 'dimensions', 'hierarchies', 'levels', 'measures',
                'materializations',
            )
        },
    }
    missing = {
        kind: sorted(required.difference(observed[kind]))
        for kind, required in required_operations.items()
        if required.difference(observed[kind])
    }
    if missing:
        raise RuntimeError(
            f'InfluxDB object operations are missing: {missing}'
        )
    evidence['object_operations'] = {
        kind: sorted(operations)
        for kind, operations in observed.items()
    }
    evidence['object_evidence'] = {
        'schema': 'cdeadmin.provider-object-live-evidence.v1',
        'engine_id': 'influxdb', 'exact_profile': '3.9.0',
        'evidence_scope': 'time-series-and-semantic-object-operations',
        'concepts': concepts,
        'passed_resource_operations': {
            kind: sorted(operations)
            for kind, operations in observed.items()
        },
        'missing_resource_operations': missing,
        'operation_failures': [],
        'raw_commands_used_for_provider_operations': False,
        'automatic_mutation_retry': False,
        'common_transaction_finality_interpreted': False,
        'passed': not missing and semantic_passed,
    }
    return evidence


def _opensearch(client, sql_client, route, run_id, destructive):
    evidence = {}
    identity = client.runtime_identity({'route': route})
    plugin_identity = sql_client.runtime_identity({'route': route})
    if identity['version'] != EXPECTED['opensearch'] or (
        plugin_identity['version'] != '3.6.0-sql-ppl'
    ):
        raise RuntimeError('OpenSearch exact identity changed')
    evidence['runtime_identity'] = identity
    evidence['sql_ppl_identity'] = plugin_identity
    session = client.open_session({'route': route})
    try:
        result = client.describe_result(client.execute(session, {
            'source': {'query': {'match_all': {}}, 'size': 1},
        }))
        evidence['query_dsl'] = {
            'kind': result['result_kind'],
            'observed_hits': len(result['payload']['hits']),
        }
    finally:
        session.close()
    evidence['resource_kinds'] = sorted({
        item['resource_kind']
        for item in client.list_resources({'route': route})
    })
    if not destructive:
        evidence['destructive_scope'] = 'not_admitted'
        return evidence
    index = f'cdeadmin-{run_id}'
    document_target = _target(
        'opensearch', 'document', index=index, _id='one'
    )
    created = False
    try:
        _admin(client, route, 'index', 'create', {
            'name': index,
            'definition': {'mappings': {'properties': {
                'title': {'type': 'text'}, 'value': {'type': 'integer'},
            }}},
        })
        created = True
        _admin(client, route, 'document', 'insert', {
            'index': index, 'document_id': 'one',
            'document': {'title': 'analytic gate', 'value': 42},
        })
        _admin(client, route, 'document', 'update', {
            'index': index, 'document_id': 'one',
            'document': {'value': 43},
        }, document_target)
        client._request(
            client._route({'route': route}),
            f'/{index}/_refresh', method='POST',
        )
        selected = dict(route, index=index)
        session = client.open_session({'route': selected})
        try:
            found = client.describe_result(client.execute(session, {
                'source': {'query': {'term': {'value': 43}}},
            }))
        finally:
            session.close()
        if not found['payload']['hits']:
            raise RuntimeError(
                'OpenSearch did not return the updated document')
        sql_session = sql_client.open_session({'route': route})
        try:
            sql = sql_client.describe_result(sql_client.execute(
                sql_session, {'source': f'SELECT value FROM `{index}`'}
            ))
        finally:
            sql_session.close()
        if not sql['payload']['rows']:
            raise RuntimeError('OpenSearch SQL did not return the document')
        evidence['mutation_query_sql_round_trip'] = True
    finally:
        if created:
            _admin(
                client, route, 'index', 'drop',
                {'acknowledge_drop': True},
                _target('opensearch', 'index', name=index, index=index),
            )
    return evidence


def _milvus(client, route, run_id, destructive):
    evidence = {}
    identity = client.runtime_identity({'route': route})
    if identity['version'] != EXPECTED['milvus']:
        raise RuntimeError('Milvus exact identity changed')
    evidence['runtime_identity'] = identity
    evidence['resource_kinds'] = sorted({
        item['resource_kind']
        for item in client.list_resources({'route': route})
    })
    if not destructive:
        evidence['destructive_scope'] = 'not_admitted'
        return evidence
    collection = f'cdeadmin_{run_id}'
    target = _target('milvus', 'collection', name=collection)
    created = False
    try:
        _admin(client, route, 'collection', 'create', {
            'name': collection, 'dimension': 4,
            'primary_field_name': 'id', 'vector_field_name': 'vector',
            'metric_type': 'COSINE', 'auto_id': False,
            'enable_dynamic_field': True,
        })
        created = True
        _admin(client, route, 'collection', 'insert', {
            'collection_name': collection,
            'data': [{'id': 1, 'vector': [0.1, 0.2, 0.3, 0.4],
                      'title': 'analytic gate'}],
        }, target)
        _admin(client, route, 'load-state', 'execute', {
            'action': 'load', 'replica_number': 1,
            'acknowledge_operation': True,
        }, _target('milvus', 'load-state', collection_name=collection))
        session = client.open_session({'route': route})
        try:
            found = client.describe_result(client.execute(session, {
                'source': {
                    'operation': 'search', 'collection_name': collection,
                    'data': [[0.1, 0.2, 0.3, 0.4]], 'limit': 1,
                    'output_fields': ['title'],
                },
            }))
        finally:
            session.close()
        if not found['payload']['matches']:
            raise RuntimeError('Milvus did not return the inserted vector')
        evidence['mutation_vector_round_trip'] = True
    finally:
        if created:
            _admin(
                client, route, 'collection', 'drop',
                {'acknowledge_drop': True}, target,
            )
    return evidence


def verify(args):
    environment = args.password_environment or (
        f'CDEADMIN_{args.engine.upper()}_PASSWORD'
    )
    password = os.environ.get(environment)
    reference = f'{args.engine}-live-credential'

    def acquire(selected, _principal, _purpose, _kind):
        if selected != reference or password is None:
            raise RuntimeError('qualification secret is unavailable')
        return _Lease(password)

    route = _route(args, reference, password)
    run_id = uuid.uuid4().hex[:12]
    evidence = {
        'schema': 'cdeadmin.analytic-live-gate.v1',
        'engine': args.engine, 'expected_runtime': EXPECTED[args.engine],
        'started_at': time.time(), 'categories': {},
        'automatic_mutation_retry': False,
        'common_transaction_finality_interpretation': False,
        'destructive_disposable_runtime': (
            args.destructive_disposable_runtime
        ),
    }
    failures = []

    def qualification():
        if args.engine == 'influxdb':
            client = InfluxDBClient(acquire)
            try:
                return _influxdb(
                    client, route, run_id,
                    args.destructive_disposable_runtime,
                    acquire,
                )
            finally:
                client.close()
        if args.engine == 'opensearch':
            client = OpenSearchClient(acquire)
            sql_client = OpenSearchSQLPPLClient(acquire)
            try:
                return _opensearch(
                    client, sql_client, route, run_id,
                    args.destructive_disposable_runtime,
                )
            finally:
                client.close()
                sql_client.close()
        client = MilvusClientAdapter(acquire)
        try:
            return _milvus(
                client, route, run_id,
                args.destructive_disposable_runtime,
            )
        finally:
            client.close()

    _record(evidence, 'exact_provider_round_trip', qualification, failures)
    evidence['finished_at'] = time.time()
    evidence['passed'] = not failures
    evidence['failures'] = failures
    return evidence


def main():
    args = parser().parse_args()
    evidence = verify(args)
    rendered = json.dumps(evidence, indent=2, sort_keys=True)
    print(rendered)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + '\n', encoding='utf-8')
    if args.object_output:
        details = evidence['categories'].get(
            'exact_provider_round_trip', {}
        ).get('details', {})
        object_evidence = details.get('object_evidence')
        if object_evidence is None:
            object_evidence = {
                'schema': 'cdeadmin.provider-object-live-evidence.v1',
                'engine_id': args.engine, 'passed': False,
                'concepts': {}, 'passed_resource_operations': {},
                'missing_resource_operations': {},
                'operation_failures': evidence['failures'],
                'raw_commands_used_for_provider_operations': False,
                'automatic_mutation_retry': False,
                'common_transaction_finality_interpreted': False,
            }
        args.object_output.parent.mkdir(parents=True, exist_ok=True)
        args.object_output.write_text(
            json.dumps(object_evidence, indent=2, sort_keys=True) + '\n',
            encoding='utf-8',
        )
    return 0 if evidence['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
