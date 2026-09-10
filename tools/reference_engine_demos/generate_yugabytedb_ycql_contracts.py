#!/usr/bin/env python3
##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Generate exact YugabyteDB 2025.2.2.2 YCQL contracts."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import sys
import time
from pathlib import Path
from types import ModuleType
from urllib.request import urlopen

import cassandra


ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / 'web'
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(WEB) not in sys.path:
    sys.path.insert(0, str(WEB))
if 'pgadmin' not in sys.modules:
    package = ModuleType('pgadmin')
    package.__path__ = [str(WEB / 'pgadmin')]
    sys.modules['pgadmin'] = package

from pgadmin.cdeadmin.providers.cql_native import _RowIdentity  # noqa: E402
from pgadmin.cdeadmin.providers.yugabytedb_ycql.client import (  # noqa: E402
    YugabyteDBYCQLClient,
)


REFERENCE_VERSION = '2025.2.2.2'
GRAMMAR = Path('src/yb/yql/cql/ql/parser/parser_gram.y')
KEYWORDS = Path('src/yb/yql/cql/ql/kwlist.h')
DIAGNOSTICS = Path('src/yb/yql/cql/ql/util/errcodes.h')
FUNCTIONS = Path('src/yb/bfql/directory.cc')


def _sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _slug(value):
    return re.sub(r'[^a-z0-9]+', '_', value.lower()).strip('_')


def _line(text, offset):
    return text.count('\n', 0, offset) + 1


def _record(item_id, native_name, source, **details):
    return {
        'item_id': item_id,
        'native_name': str(native_name),
        'source': source,
        **details,
    }


def _evidence(evidence_id, evidence_kind, authority, artifact, digest,
              format_name, license_id):
    return {
        'evidence_id': evidence_id,
        'evidence_kind': evidence_kind,
        'authority': authority,
        'artifact': artifact,
        'sha256': digest,
        'format': format_name,
        'license_id': license_id,
    }


def _productions(grammar):
    return list(re.finditer(
        r'(?m)^([A-Za-z][A-Za-z0-9_]*)\s*:', grammar
    ))


def _production_block(grammar, name):
    matches = _productions(grammar)
    for position, match in enumerate(matches):
        if match.group(1) == name:
            end = (
                matches[position + 1].start()
                if position + 1 < len(matches) else len(grammar)
            )
            return match, grammar[match.end():end]
    raise RuntimeError(f'YCQL grammar production is absent: {name}')


def _alternatives(grammar, name, category, supported_only=False):
    match, block = _production_block(grammar, name)
    alternatives = re.split(r'(?m)^\s*\|', block)
    records = []
    for alternative in alternatives:
        syntax = alternative.strip().split('{', 1)[0].strip()
        syntax = re.sub(r'\s+', ' ', syntax).rstrip(';').strip()
        if not syntax or (
                supported_only and 'PARSER_UNSUPPORTED' in alternative):
            continue
        records.append(_record(
            f'{category}.{len(records) + 1:04d}', syntax,
            f'{GRAMMAR.as_posix()}:{_line(grammar, match.start())}',
            production=name,
        ))
    return records


def _source_inventory(source_root):
    grammar_path = source_root / GRAMMAR
    grammar = grammar_path.read_text(encoding='utf-8')
    commands = [
        _record(
            f'commands.{position:04d}', match.group(1),
            f'{GRAMMAR.as_posix()}:{_line(grammar, match.start())}',
            inventory_class='parser_production',
        )
        for position, match in enumerate(_productions(grammar), 1)
    ]
    statements = _alternatives(grammar, 'stmt', 'statements', True)

    keyword_path = source_root / KEYWORDS
    keyword_text = keyword_path.read_text(encoding='utf-8')
    lexical = []
    for match in re.finditer(
            r'PG_KEYWORD\("([^"]+)",\s*([A-Z0-9_]+),\s*'
            r'([A-Z_]+)\)', keyword_text):
        lexical.append(_record(
            f'lexical_rules.{len(lexical) + 1:04d}', match.group(1),
            f'{KEYWORDS.as_posix()}:{_line(keyword_text, match.start())}',
            grammar_token=match.group(2), category=match.group(3),
        ))

    data_types = []
    seen_types = set()
    for production in (
            'ParametricTypename', 'SimpleTypename', 'Numeric', 'Character',
            'ConstDatetime'):
        for item in _alternatives(
                grammar, production, 'data_types', True):
            name = item['native_name']
            if name in seen_types:
                continue
            seen_types.add(name)
            item['item_id'] = f'data_types.{len(data_types) + 1:04d}'
            data_types.append(item)

    operators = []
    for match in re.finditer(
            r'(?m)^%(?:left|right|nonassoc)\s+(.+)$', grammar):
        source = f'{GRAMMAR.as_posix()}:{_line(grammar, match.start())}'
        for token in match.group(1).split('//', 1)[0].split():
            operators.append(_record(
                f'operators.{len(operators) + 1:04d}', token.strip("'"),
                source, grammar_token=token,
            ))

    function_path = source_root / FUNCTIONS
    function_text = function_path.read_text(encoding='utf-8')
    functions = []
    seen_functions = set()
    for match in re.finditer(
            r'(?m)^\s*\{\s*"[^"]+",\s*"([^"]+)",', function_text):
        name = match.group(1)
        if not name or name in seen_functions:
            continue
        seen_functions.add(name)
        functions.append(_record(
            f'functions.{len(functions) + 1:04d}', name,
            f'{FUNCTIONS.as_posix()}:{_line(function_text, match.start())}',
            registry='BFOperator directory',
        ))

    diagnostic_path = source_root / DIAGNOSTICS
    diagnostic_text = diagnostic_path.read_text(encoding='utf-8')
    enum_match = re.search(
        r'enum class ErrorCode\s*:[^{]+\{(.*?)\n\};',
        diagnostic_text, re.DOTALL,
    )
    if enum_match is None:
        raise RuntimeError('YCQL error-code registry is absent')
    diagnostics = []
    for match in re.finditer(
            r'(?m)^\s*([A-Z][A-Z0-9_]+)\s*=\s*(-?[0-9]+)',
            enum_match.group(1)):
        diagnostics.append(_record(
            f'diagnostics.{len(diagnostics) + 1:04d}', match.group(1),
            f'{DIAGNOSTICS.as_posix()}:'
            f'{_line(diagnostic_text, enum_match.start() + match.start())}',
            error_code=int(match.group(2)),
        ))

    inventories = {
        'lexical_rules': lexical,
        'statements': statements,
        'commands': commands,
        'data_types': data_types,
        'functions': functions,
        'operators': operators,
        'session_settings': [],
        'diagnostics': diagnostics,
    }
    required = set(inventories) - {'session_settings'}
    if any(not inventories[name] for name in required):
        raise RuntimeError('YCQL source inventory is incomplete')
    return inventories


def _target(kind, native):
    return {
        'resource_id': f'contract:{kind}:target',
        'resource_kind': kind,
        'display_name': 'target',
        'extensions': {'yugabytedb': {'native': native}},
    }


def _route():
    return {
        'route_id': 'contract-generation',
        'host': '127.0.0.1', 'port': 59042,
        'version_api_host': '127.0.0.1', 'version_api_port': 57000,
        'version_api_scheme': 'http', 'local_dc': 'datacenter1',
        'tls_mode': 'disabled', 'compression': 'none',
        'consistency': 'LOCAL_ONE',
        'serial_consistency': 'LOCAL_SERIAL',
        'protocol_version': 4, 'request_timeout': 60,
        'connect_timeout': 15,
    }


def _request(kind, operation, draft, native=None):
    return {
        'resource_kind': kind,
        'operation_id': operation,
        'draft': draft,
        'target_resource': _target(kind, native) if native else None,
        '_provider_route': _route(),
    }


def _representative_requests(client):
    keyspace = {'keyspace_name': 'contract_keyspace'}
    table = {**keyspace, 'table_name': 'contract_table'}
    column = {**table, 'column_name': 'event_id', 'kind': 'clustering'}
    index = {**table, 'index_name': 'contract_index'}
    user_type = {**keyspace, 'type_name': 'contract_type'}
    role = {'role': 'contract_role'}
    permission = {'role': 'contract_role'}
    requests = [
        _request('keyspace', 'create', {
            'name': 'contract_keyspace',
            'replication': {
                'class': 'NetworkTopologyStrategy', 'datacenter1': 1,
            }, 'durable_writes': True,
        }),
        _request('keyspace', 'alter', {
            'replication': {
                'class': 'NetworkTopologyStrategy', 'datacenter1': 1,
            }, 'durable_writes': True,
        }, keyspace),
        _request('keyspace', 'drop', {
            'confirmation': 'drop-keyspace',
        }, keyspace),
        _request('table', 'create', {
            'keyspace': 'contract_keyspace', 'name': 'contract_table',
            'columns': [
                {'name': 'tenant', 'type': 'text'},
                {'name': 'event_id', 'type': 'int'},
                {'name': 'value', 'type': 'text'},
            ],
            'partition_keys': ['tenant'],
            'clustering_keys': ['event_id'],
            'tablets': 1, 'transactions_enabled': True,
            'transaction_consistency': 'strong',
        }),
        _request('table', 'alter', {'changes': {
            'add_columns': [{'name': 'note', 'type': 'text'}],
        }}, table),
        _request('table', 'insert', {'values': {
            'tenant': 'tenant', 'event_id': 1, 'value': 'value',
        }}, table),
        _request('table', 'drop', {
            'confirmation': 'drop-table',
        }, table),
        _request('column', 'create', {
            'name': 'extra_value', 'type': 'int',
        }, table),
        _request('column', 'rename', {
            'new_name': 'event_sequence',
        }, column),
        _request('column', 'drop', {
            'confirmation': 'drop-column',
        }, column),
        _request('index', 'create', {
            'keyspace': 'contract_keyspace', 'table': 'contract_table',
            'name': 'contract_index', 'target': 'value',
        }),
        _request('index', 'drop', {
            'confirmation': 'drop-index',
        }, index),
        _request('user-defined-type', 'create', {
            'keyspace': 'contract_keyspace', 'name': 'contract_type',
            'fields': [{'name': 'city', 'type': 'text'}],
        }),
        _request('user-defined-type', 'drop', {
            'confirmation': 'drop-user-defined-type',
        }, user_type),
        _request('role', 'create', {
            'name': 'contract_role', 'login': False, 'superuser': False,
        }),
        _request('role', 'alter', {
            'login': False, 'superuser': False,
        }, role),
        _request('role', 'grant', {
            'principal': 'contract_member',
            'privileges': ['contract_role'],
        }, role),
        _request('role', 'revoke', {
            'principal': 'contract_member',
            'privileges': ['contract_role'],
        }, role),
        _request('role', 'drop', {
            'confirmation': 'drop-role',
        }, role),
        _request('permission', 'grant', {
            'principal': 'contract_role', 'privileges': ['SELECT'],
            'resource': {
                'kind': 'keyspace', 'keyspace': 'contract_keyspace',
            },
        }, permission),
        _request('permission', 'revoke', {
            'principal': 'contract_role', 'privileges': ['SELECT'],
            'resource': {
                'kind': 'keyspace', 'keyspace': 'contract_keyspace',
            },
        }, permission),
    ]
    normalized_route = client._route({'route': _route()})
    for operation in ('update', 'delete'):
        token = f'contract-{operation}'
        client._row_identities[token] = _RowIdentity(
            client._route_fingerprint(normalized_route),
            'contract_keyspace', 'contract_table',
            ('tenant', 'event_id'), ('tenant', 1),
            {'tenant': 'tenant', 'event_id': 1, 'value': 'before'},
            time.monotonic(),
        )
        draft = {'selector': {'identity_token': token}}
        if operation == 'update':
            draft['changes'] = {'value': 'after'}
        else:
            draft['confirmation'] = 'delete-row'
        requests.append(_request('table', operation, draft, table))
    return requests


def _task_templates(live):
    client = YugabyteDBYCQLClient()
    expected = set(client.admin_dialect_task_ids())
    proven = {
        f'visual_admin.{kind}.{operation}'
        for kind, operations in live['operation_evidence'].items()
        for operation in operations
        if client.admin_operation_requires_dialect(kind, operation)
    }
    if live.get('passed') is not True or proven != expected:
        raise RuntimeError('YCQL exact live task evidence is incomplete')
    templates = []
    for request in _representative_requests(client):
        plan = client.plan_admin_operation(request)
        preview = plan['command_preview']
        task_id = (
            f"visual_admin.{request['resource_kind']}."
            f"{request['operation_id']}"
        )
        statements = preview['statements']
        if task_id not in expected or not statements:
            raise RuntimeError(f'YCQL task plan is incomplete: {task_id}')
        templates.append({
            'task_id': task_id,
            'source': '\n;\n'.join(statements),
            'source_format': 'ordered_native_statements',
            'statements': statements,
            'required_bindings': [
                f'parameter_{position}'
                for position in range(
                    1, 1 + sum(item.count('%s') for item in statements)
                )
            ],
            'binding_style': 'cassandra_driver_percent_s',
            'proof_ids': [
                'yugabytedb-2025.2.2.2-ycql-parser-acceptance',
                'yugabytedb-2025.2.2.2-ycql-live-execution',
            ],
        })
    if {item['task_id'] for item in templates} != expected:
        raise RuntimeError('YCQL generated task inventory is incomplete')
    return sorted(templates, key=lambda item: item['task_id'])


def build_dialect(source_root, source_archive, live_path):
    inventories = _source_inventory(source_root)
    live = json.loads(live_path.read_text(encoding='utf-8'))
    if live.get('profile') != REFERENCE_VERSION:
        raise RuntimeError('YCQL live evidence has the wrong profile')
    templates = _task_templates(live)
    return {
        'schema': 'cdeadmin.engine-dialect-inventory.v1',
        'inventory_id': (
            'yugabytedb.ycql-dialect-inventory.2025.2.2.2.v1'),
        'engine_id': 'yugabytedb',
        'interface_id': 'ycql',
        'reference_version': REFERENCE_VERSION,
        'source_archive': source_archive.name,
        'source_archive_sha256': _sha256(source_archive),
        'inventories': inventories,
        'completeness': {
            'runtime_version': REFERENCE_VERSION,
            'runtime_interface': 'YCQL native protocol v4',
            'grammar_productions_exhausted': True,
            'keyword_catalog_exhausted': True,
            'function_registry_exhausted': True,
            'diagnostic_catalog_exhausted': True,
            'session_setting_catalog': 'not_exposed_by_ycql',
            'inventory_counts': {
                name: len(records) for name, records in inventories.items()
            },
        },
    }, templates


def build_dialect_contract(inventory, inventory_path, source_archive,
                           live_path, templates):
    source_id = 'yugabytedb-2025.2.2.2-ycql-source-grammar'
    catalog_id = 'yugabytedb-2025.2.2.2-ycql-source-catalog'
    driver_id = 'cassandra-driver-3.30.1'
    live_digest = _sha256(live_path)
    driver_path = Path(cassandra.__file__)
    proofs = [
        _evidence(
            source_id, 'grammar', 'YugabyteDB project',
            source_archive.name, _sha256(source_archive),
            'YugabyteDB 2025.2.2.2 source archive', 'Apache-2.0'),
        _evidence(
            catalog_id, 'catalog', 'YugabyteDB YCQL source registries',
            inventory_path.name, _sha256(inventory_path),
            'cdeadmin.engine-dialect-inventory.v1', 'PostgreSQL'),
        _evidence(
            'yugabytedb-2025.2.2.2-ycql-parser-acceptance',
            'parser_acceptance', 'YugabyteDB 2025.2.2.2 YCQL runtime',
            live_path.name, live_digest,
            'cdeadmin.yugabytedb-ycql-live-gate.v1', 'PostgreSQL'),
        _evidence(
            'yugabytedb-2025.2.2.2-ycql-live-execution',
            'live_execution', 'YugabyteDB 2025.2.2.2 YCQL runtime',
            live_path.name, live_digest,
            'cdeadmin.yugabytedb-ycql-live-gate.v1', 'PostgreSQL'),
        _evidence(
            driver_id, 'driver', 'DataStax Python Driver project',
            driver_path.name, _sha256(driver_path), 'Python package',
            'Apache-2.0'),
    ]
    inventories = {
        name: [{
            **copy.deepcopy(item),
            'proof_ids': [source_id if name != 'session_settings'
                          else catalog_id],
        } for item in records]
        for name, records in inventory['inventories'].items()
    }
    task_ids = [item['task_id'] for item in templates]
    decisions = {
        'identifier_quoting': 'double_quote_with_doubled_quote_escape',
        'string_literals': 'single_quote_with_doubled_quote_escape',
        'parameter_binding': 'cassandra_driver_percent_s',
        'transaction_control': (
            'no_general_multi_statement_transaction; '
            'engine-owned YCQL transaction outcomes'),
        'pagination': 'driver paging state with SELECT LIMIT',
        'explain': 'not exposed by the exact YCQL profile',
        'cancellation': 'driver ResponseFuture.cancel',
    }
    return {
        'schema': 'cdeadmin.engine-dialect.v2',
        'contract_id': 'yugabytedb.ycql-dialect.2025.2.2.2.v1',
        'provider_id': 'org.cdeadmin.yugabytedb.ycql',
        'profile_id': 'yugabytedb-ycql',
        'engine_id': 'yugabytedb',
        'interface_id': 'ycql',
        'reference_version': REFERENCE_VERSION,
        'language_profiles': ['ycql'],
        'grammar_evidence': {
            key: value for key, value in proofs[0].items()
            if key not in {'evidence_id', 'evidence_kind'}
        },
        'proof_records': proofs,
        'inventories': inventories,
        'syntax_decisions': {
            name: {
                'syntax': syntax,
                'proof_ids': [
                    driver_id if name in {'parameter_binding', 'cancellation'}
                    else source_id
                ],
            } for name, syntax in decisions.items()
        },
        'task_templates': templates,
        'coverage': {
            'scope': 'cdeadmin_generated_tasks',
            'language_acceptance_authority': 'engine_parser',
            'authoritative_inventory_counts': {
                name: len(records) for name, records in inventories.items()
            },
            'authoritative_task_count': len(task_ids),
            'implemented_task_count': len(task_ids),
            'authoritative_task_ids': task_ids,
        },
        'live_evidence_ids': [
            'yugabytedb-2025.2.2.2-ycql-live-execution'
        ],
        'complete_grammar_inventory': {
            'artifact': inventory_path.name,
            'sha256': _sha256(inventory_path),
            **inventory['completeness'],
        },
    }


def _prometheus_endpoint(port, component):
    with urlopen(  # nosec B310 - exact local reference fixture
            f'http://127.0.0.1:{port}/prometheus-metrics',
            timeout=30) as response:
        text = response.read().decode('utf-8')
    helps = {}
    types = {}
    for line in text.splitlines():
        match = re.match(r'# HELP ([A-Za-z_:][A-Za-z0-9_:]*) (.*)', line)
        if match:
            helps[match.group(1)] = match.group(2).strip()
            continue
        match = re.match(
            r'# TYPE ([A-Za-z_:][A-Za-z0-9_:]*) ([a-z]+)', line)
        if match:
            types[match.group(1)] = match.group(2)
    return [{
        'component': component,
        'native_name': name,
        'prometheus_type': types[name],
        'description': helps[name] or (
            f'YugabyteDB {component} metric {name}.'),
    } for name in sorted(set(helps).intersection(types))]


def build_metrics_inventory(master_port, tserver_port):
    observations = (
        _prometheus_endpoint(master_port, 'master') +
        _prometheus_endpoint(tserver_port, 'tserver')
    )
    if not observations:
        raise RuntimeError('YugabyteDB YCQL metric inventory is empty')
    return {
        'schema': 'cdeadmin.engine-metrics-inventory.v1',
        'inventory_id': (
            'yugabytedb.ycql-metrics-inventory.2025.2.2.2.v1'),
        'engine_id': 'yugabytedb',
        'interface_id': 'ycql',
        'reference_version': REFERENCE_VERSION,
        'collection_authority': (
            'YugabyteDB 2025.2.2.2 native master and tserver Prometheus '
            'endpoints'),
        'endpoint_selection': (
            'Every series having native HELP and TYPE declarations on both '
            'reference endpoints.'),
        'observations': observations,
        'completeness': {
            'runtime_version': REFERENCE_VERSION,
            'components_exhausted': ['master', 'tserver'],
            'observation_count': len(observations),
            'prometheus_help_type_catalog_exhausted': True,
            'admitted_metric_count': len(observations),
        },
    }


def build_metrics_contract(inventory, inventory_path, source_archive,
                           live_path):
    source_id = 'yugabytedb-2025.2.2.2-ycql-source-metric-registry'
    runtime_id = 'yugabytedb-2025.2.2.2-ycql-prometheus-catalog'
    live_id = 'yugabytedb-2025.2.2.2-ycql-live-execution'
    evidence = [
        _evidence(
            source_id, 'documentation', 'YugabyteDB project',
            source_archive.name, _sha256(source_archive),
            'YugabyteDB 2025.2.2.2 source archive', 'Apache-2.0'),
        _evidence(
            runtime_id, 'catalog', 'YugabyteDB 2025.2.2.2 runtime',
            inventory_path.name, _sha256(inventory_path),
            'cdeadmin.engine-metrics-inventory.v1', 'PostgreSQL'),
        _evidence(
            live_id, 'live_execution',
            'YugabyteDB 2025.2.2.2 YCQL runtime', live_path.name,
            _sha256(live_path),
            'cdeadmin.yugabytedb-ycql-live-gate.v1', 'PostgreSQL'),
    ]
    observations = []
    metrics = []
    for position, item in enumerate(inventory['observations'], 1):
        name = item['native_name']
        component = item['component']
        observation_id = (
            f'yugabytedb.ycql.{component}.{position:05d}.{_slug(name)}'
        )
        kind = item['prometheus_type']
        observations.append({
            'observation_id': observation_id,
            'native_name': name,
            'scope': f'{component}_native_label_set',
            'source': f'GET {component} /prometheus-metrics',
            'value_type': 'number',
            'observation_class': 'operational_metric',
            'description': item['description'],
            'privilege': f'{component}_metrics_endpoint_access',
            'version_condition': REFERENCE_VERSION,
            'collection_cost': 'single_prometheus_catalog_snapshot',
            'poll_interval_seconds': 15,
            'cardinality': 'one_per_native_label_set',
            'redaction': 'provider_label_redaction_policy',
            'evidence_ids': [source_id, runtime_id, live_id],
            'prometheus_type': kind,
        })
        metrics.append({
            'metric_id': observation_id,
            'observation_id': observation_id,
            'unit': 'native_unit', 'kind': kind,
            'reset_behavior': (
                'process_restart' if kind in {'counter', 'histogram'}
                else 'current_observation'),
            'aggregation': (
                'sum_by_native_labels' if kind == 'counter'
                else 'preserve_native_labels'),
            'evidence_ids': [source_id, runtime_id, live_id],
        })
    observation_ids = [item['observation_id'] for item in observations]
    metric_ids = [item['metric_id'] for item in metrics]
    return {
        'schema': 'cdeadmin.engine-metrics.v2',
        'contract_id': 'yugabytedb.ycql-metrics.2025.2.2.2.v1',
        'provider_id': 'org.cdeadmin.yugabytedb.ycql',
        'profile_id': 'yugabytedb-ycql',
        'engine_id': 'yugabytedb', 'interface_id': 'ycql',
        'reference_version': REFERENCE_VERSION,
        'catalog_evidence': {
            key: value for key, value in evidence[1].items()
            if key not in {'evidence_id', 'evidence_kind'}
        },
        'classification_evidence': evidence,
        'native_observations': observations,
        'metrics': metrics,
        'authoritative_observation_count': len(observation_ids),
        'authoritative_observation_ids': observation_ids,
        'authoritative_metric_count': len(metric_ids),
        'authoritative_metric_ids': metric_ids,
        'live_evidence_ids': [runtime_id, live_id],
    }


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + '\n',
        encoding='utf-8',
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-root', type=Path, required=True)
    parser.add_argument('--source-archive', type=Path, required=True)
    parser.add_argument('--live-evidence', type=Path, required=True)
    parser.add_argument('--master-http-port', type=int, default=57000)
    parser.add_argument('--tserver-http-port', type=int, default=59000)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    inventory, templates = build_dialect(
        args.source_root, args.source_archive, args.live_evidence
    )
    inventory_path = (
        args.output_dir /
        'yugabytedb_ycql_dialect_inventory_2025_2_2_2.json'
    )
    _write(inventory_path, inventory)
    _write(
        args.output_dir / 'yugabytedb_ycql_dialect_2025_2_2_2.json',
        build_dialect_contract(
            inventory, inventory_path, args.source_archive,
            args.live_evidence, templates,
        ),
    )
    metrics_inventory = build_metrics_inventory(
        args.master_http_port, args.tserver_http_port
    )
    metrics_inventory_path = (
        args.output_dir /
        'yugabytedb_ycql_metrics_inventory_2025_2_2_2.json'
    )
    _write(metrics_inventory_path, metrics_inventory)
    metrics = build_metrics_contract(
        metrics_inventory, metrics_inventory_path, args.source_archive,
        args.live_evidence,
    )
    _write(
        args.output_dir / 'yugabytedb_ycql_metrics_2025_2_2_2.json',
        metrics,
    )
    print(json.dumps({
        'dialect_counts': inventory['completeness']['inventory_counts'],
        'task_count': len(templates),
        'metric_count': len(metrics['metrics']),
    }, indent=2, sort_keys=True))


if __name__ == '__main__':
    main()
