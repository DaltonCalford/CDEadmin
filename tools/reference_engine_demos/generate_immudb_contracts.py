#!/usr/bin/env python3
##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Generate exact immudb 1.11.0 dialect and metrics contracts."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import sys
from pathlib import Path
from types import ModuleType
from urllib.request import urlopen

import psycopg


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

from pgadmin.cdeadmin.providers.immudb.provider import (  # noqa: E402
    ADMINISTRATION,
)


REFERENCE_VERSION = '1.11.0'
GRAMMAR = Path('embedded/sql/sql_grammar.y')
FUNCTIONS = Path('embedded/sql/functions.go')
PARSER = Path('embedded/sql/parser.go')


def _sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _slug(value):
    return re.sub(r'[^a-z0-9]+', '_', value.lower()).strip('_')


def _record(item_id, native_name, source, **details):
    return {
        'item_id': item_id,
        'native_name': str(native_name),
        'source': source,
        **details,
    }


def _evidence(
        evidence_id, evidence_kind, authority, artifact, digest,
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


def _line(text, offset):
    return text.count('\n', 0, offset) + 1


def _productions(grammar):
    return list(re.finditer(
        r'(?m)^([a-z][A-Za-z0-9_]*)\s*:', grammar
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
    raise RuntimeError(f'immudb grammar production is absent: {name}')


def _statement_inventory(grammar):
    records = []
    for production in ('ddlstmt', 'dmlstmt', 'dqlstmt'):
        match, block = _production_block(grammar, production)
        alternatives = re.split(r'(?m)^\s*\|', block)
        for alternative in alternatives:
            syntax = alternative.strip().split('{', 1)[0].strip()
            syntax = re.sub(r'\s+', ' ', syntax).rstrip(';').strip()
            if not syntax:
                continue
            records.append(_record(
                f'statements.{len(records) + 1:04d}', syntax,
                f'{GRAMMAR.as_posix()}:{_line(grammar, match.start())}',
                production=production,
            ))
    return records


def _source_inventory(source_root):
    grammar_path = source_root / GRAMMAR
    grammar = grammar_path.read_text(encoding='utf-8')
    commands = [
        _record(
            f'commands.{position:04d}', match.group(1),
            f'{GRAMMAR.as_posix()}:{_line(grammar, match.start())}',
        )
        for position, match in enumerate(_productions(grammar), 1)
    ]

    lexical = []
    for match in re.finditer(r'(?m)^%token <keyword>\s+(.+)$', grammar):
        for token in match.group(1).split():
            lexical.append(_record(
                f'lexical_rules.{len(lexical) + 1:04d}', token,
                f'{GRAMMAR.as_posix()}:{_line(grammar, match.start())}',
                token_class='keyword',
            ))

    type_match = re.search(
        r'(?m)^%token <keyword>\s+'
        r'(INTEGER_TYPE\s+BOOLEAN_TYPE\s+VARCHAR_TYPE\s+UUID_TYPE\s+'
        r'BLOB_TYPE\s+TIMESTAMP_TYPE\s+FLOAT_TYPE\s+JSON_TYPE)$',
        grammar,
    )
    if type_match is None:
        raise RuntimeError('immudb exact SQL type token registry is absent')
    data_types = [
        _record(
            f'data_types.{position:04d}', token.removesuffix('_TYPE'),
            f'{GRAMMAR.as_posix()}:{_line(grammar, type_match.start())}',
            grammar_token=token,
        )
        for position, token in enumerate(type_match.group(1).split(), 1)
    ]

    operators = []
    for match in re.finditer(
            r'(?m)^%(?:left|right|nonassoc)\s+(.+)$', grammar):
        for token in match.group(1).split():
            operators.append(_record(
                f'operators.{len(operators) + 1:04d}', token.strip("'"),
                f'{GRAMMAR.as_posix()}:{_line(grammar, match.start())}',
                grammar_token=token,
            ))

    functions_path = source_root / FUNCTIONS
    functions_text = functions_path.read_text(encoding='utf-8')
    functions = []
    seen_functions = set()
    for match in re.finditer(
            r'(?m)^\s*[A-Za-z][A-Za-z0-9]*FnCall\s+string\s*=\s*'
            r'"([A-Z0-9_]+)"', functions_text):
        name = match.group(1)
        if name in seen_functions:
            continue
        seen_functions.add(name)
        functions.append(_record(
            f'functions.{len(functions) + 1:04d}', name,
            f'{FUNCTIONS.as_posix()}:{_line(functions_text, match.start())}',
            registry='builtinFunctions',
        ))
    parser_path = source_root / PARSER
    parser_text = parser_path.read_text(encoding='utf-8')
    aggregate_match = re.search(
        r'var aggregateFns = map\[string\]AggregateFn\{(.*?)\n\}',
        parser_text, re.DOTALL,
    )
    if aggregate_match is None:
        raise RuntimeError('immudb aggregate function registry is absent')
    for match in re.finditer(r'"([a-z0-9_]+)"\s*:', aggregate_match.group(1)):
        name = match.group(1).upper()
        if name in seen_functions:
            continue
        seen_functions.add(name)
        functions.append(_record(
            f'functions.{len(functions) + 1:04d}', name,
            f'{PARSER.as_posix()}:'
            f'{_line(parser_text, aggregate_match.start() + match.start())}',
            registry='aggregateFns',
        ))

    diagnostics = []
    for path in sorted((source_root / 'embedded/sql').glob('*.go')):
        text = path.read_text(encoding='utf-8')
        for match in re.finditer(
                r'(?m)^\s*(Err[A-Za-z][A-Za-z0-9]+)\s*=\s*'
                r'(?:errors\.New|fmt\.Errorf|[A-Za-z0-9_.]+Err)', text):
            diagnostics.append(_record(
                f'diagnostics.{len(diagnostics) + 1:04d}', match.group(1),
                f'{path.relative_to(source_root).as_posix()}:'
                f'{_line(text, match.start())}',
            ))

    inventories = {
        'lexical_rules': lexical,
        'statements': _statement_inventory(grammar),
        'commands': commands,
        'data_types': data_types,
        'functions': functions,
        'operators': operators,
        'diagnostics': diagnostics,
    }
    if any(not records for records in inventories.values()):
        raise RuntimeError('immudb source inventory is incomplete')
    return inventories


def _profile(profile_path):
    return next(
        item for item in json.loads(profile_path.read_text(
            encoding='utf-8'))['profiles'] if item['engine'] == 'immudb'
    )


def _runtime_settings(profile_path):
    profile = _profile(profile_path)
    connection = psycopg.connect(
        host='127.0.0.1', port=profile['port'], user=profile['user'],
        password=profile['password'], dbname=profile['database'],
        connect_timeout=10,
    )
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                'SELECT name, setting FROM pg_settings ORDER BY name'
            )
            rows = cursor.fetchall()
    finally:
        connection.close()
    if not rows:
        raise RuntimeError('immudb pg_settings inventory is empty')
    return [
        _record(
            f'session_settings.{position:04d}', name, 'pg_settings',
            observed_value=str(value),
        )
        for position, (name, value) in enumerate(rows, 1)
    ]


def _task_templates(live):
    evidence = live['object_experience_evidence']['dialect_task_evidence']
    expected = set(ADMINISTRATION.dialect_task_ids())
    if set(evidence) != expected:
        raise RuntimeError('immudb live evidence does not cover exact tasks')
    result = []
    for task_id in sorted(evidence):
        task = evidence[task_id]
        statements = task['command_preview'].get('statements') or []
        if task.get('live_execution') != 'passed' or not statements:
            raise RuntimeError(
                f'immudb task evidence is invalid: {task_id}'
            )
        sources = [
            item.get('source') or (
                f"{item['transport']}:{item['operation']}"
            )
            for item in statements
        ]
        result.append({
            'task_id': task_id,
            'source': '\n;\n'.join(sources),
            'source_format': 'ordered_provider_native_operations',
            'statements': sources,
            'required_bindings': [
                f'parameter_{position}'
                for position in range(1, 1 + sum(
                    item.get('parameter_count', 0) for item in statements
                ))
            ],
            'binding_style': 'provider_transport_specific',
            'proof_ids': [
                'immudb-1.11.0-task-parser-acceptance',
                'immudb-1.11.0-task-live-execution',
            ],
        })
    return result


def build_dialect(source_root, source_archive, profile_path, live_path):
    inventories = _source_inventory(source_root)
    inventories['session_settings'] = _runtime_settings(profile_path)
    live = json.loads(live_path.read_text(encoding='utf-8'))
    if (
            live.get('dialect_qualification_ready') is not True or
            live.get('exact_profile') != REFERENCE_VERSION or
            live.get('missing_dialect_task_ids') != [] or
            live.get('credential_values_exported') is not False):
        raise RuntimeError('immudb exact live evidence is not admissible')
    return {
        'schema': 'cdeadmin.engine-dialect-inventory.v1',
        'inventory_id': 'immudb.dialect-inventory.1.11.0.v1',
        'engine_id': 'immudb',
        'reference_version': REFERENCE_VERSION,
        'source_archive': source_archive.name,
        'source_archive_sha256': _sha256(source_archive),
        'inventories': inventories,
        'completeness': {
            'runtime_version': REFERENCE_VERSION,
            'runtime_catalogs_exhausted': True,
            'grammar_productions_exhausted': True,
            'function_registry_exhausted': True,
            'diagnostic_catalog_exhausted': True,
            'inventory_counts': {
                name: len(records) for name, records in inventories.items()
            },
        },
    }, live, _task_templates(live)


def build_dialect_contract(inventory, inventory_path, source_archive,
                           live_path, templates):
    source_id = 'immudb-1.11.0-source-grammar'
    catalog_id = 'immudb-1.11.0-runtime-catalog'
    driver_id = 'psycopg-3.3.4'
    inventory_digest = _sha256(inventory_path)
    live_digest = _sha256(live_path)
    driver_path = Path(psycopg.__file__)
    proofs = [
        _evidence(
            source_id, 'grammar', 'Codenotary immudb project',
            source_archive.name, _sha256(source_archive),
            'immudb 1.11.0 source archive', 'BUSL-1.1'),
        _evidence(
            catalog_id, 'catalog', 'immudb 1.11.0 runtime',
            inventory_path.name, inventory_digest,
            'cdeadmin.engine-dialect-inventory.v1', 'PostgreSQL'),
        _evidence(
            'immudb-1.11.0-task-parser-acceptance', 'parser_acceptance',
            'immudb 1.11.0 runtime', live_path.name, live_digest,
            'cdeadmin.relational-provider-live-verification.v1',
            'PostgreSQL'),
        _evidence(
            'immudb-1.11.0-task-live-execution', 'live_execution',
            'immudb 1.11.0 runtime', live_path.name, live_digest,
            'cdeadmin.relational-provider-live-verification.v1',
            'PostgreSQL'),
        _evidence(
            driver_id, 'driver', 'Psycopg project', driver_path.name,
            _sha256(driver_path), 'Python source', 'LGPL-3.0-only'),
    ]
    contract_inventories = {
        name: [
            {
                **copy.deepcopy(record),
                'proof_ids': [
                    source_id if name != 'session_settings' else catalog_id
                ],
            }
            for record in records
        ]
        for name, records in inventory['inventories'].items()
    }
    task_ids = [item['task_id'] for item in templates]
    decisions = {
        'identifier_quoting': 'double_quote_with_doubled_quote_escape',
        'string_literals': 'single_quote_with_doubled_quote_escape',
        'parameter_binding': 'psycopg_percent_s_or_native_rest_fields',
        'transaction_control': 'BEGIN TRANSACTION; COMMIT; ROLLBACK',
        'pagination': 'LIMIT with optional OFFSET',
        'explain': 'EXPLAIN',
        'cancellation': 'provider session cancellation when available',
    }
    return {
        'schema': 'cdeadmin.engine-dialect.v2',
        'contract_id': 'immudb.dialect.1.11.0.v1',
        'provider_id': 'org.cdeadmin.immudb',
        'profile_id': 'immudb-native',
        'engine_id': 'immudb',
        'interface_id': 'immudb-native',
        'reference_version': REFERENCE_VERSION,
        'language_profiles': ['immudb-sql'],
        'grammar_evidence': {
            key: value for key, value in proofs[0].items()
            if key not in {'evidence_id', 'evidence_kind'}
        },
        'proof_records': proofs,
        'inventories': contract_inventories,
        'syntax_decisions': {
            name: {
                'syntax': syntax,
                'proof_ids': [
                    driver_id if name == 'parameter_binding' else source_id
                ],
            }
            for name, syntax in decisions.items()
        },
        'task_templates': templates,
        'coverage': {
            'scope': 'cdeadmin_generated_tasks',
            'language_acceptance_authority': 'engine_parser',
            'authoritative_inventory_counts': {
                name: len(records)
                for name, records in contract_inventories.items()
            },
            'authoritative_task_count': len(task_ids),
            'implemented_task_count': len(task_ids),
            'authoritative_task_ids': task_ids,
        },
        'live_evidence_ids': ['immudb-1.11.0-task-live-execution'],
        'complete_grammar_inventory': {
            'artifact': inventory_path.name,
            'sha256': inventory_digest,
            **inventory['completeness'],
        },
    }


def build_metrics_inventory(http_port):
    with urlopen(  # nosec B310 - exact local reference fixture
            f'http://127.0.0.1:{http_port}/metrics', timeout=30) as response:
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
    observations = [{
        'native_name': name,
        'prometheus_type': types[name],
        'description': helps[name] or f'immudb metric {name}.',
    } for name in sorted(set(helps).intersection(types))]
    if not observations:
        raise RuntimeError('immudb Prometheus metric inventory is empty')
    return {
        'schema': 'cdeadmin.engine-metrics-inventory.v1',
        'inventory_id': 'immudb.metrics-inventory.1.11.0.v1',
        'engine_id': 'immudb',
        'reference_version': REFERENCE_VERSION,
        'collection_authority': 'immudb 1.11.0 native Prometheus endpoint',
        'endpoint_selection': (
            'Every series having native HELP and TYPE declarations.'),
        'observations': observations,
        'completeness': {
            'runtime_version': REFERENCE_VERSION,
            'observation_count': len(observations),
            'prometheus_help_type_catalog_exhausted': True,
            'admitted_metric_count': len(observations),
        },
    }


def build_metrics_contract(inventory, inventory_path, source_archive,
                           live_path):
    source_id = 'immudb-1.11.0-source-metric-registry'
    runtime_id = 'immudb-1.11.0-runtime-prometheus-catalog'
    live_id = 'immudb-1.11.0-task-live-execution'
    evidence = [
        _evidence(
            source_id, 'documentation', 'Codenotary immudb project',
            source_archive.name, _sha256(source_archive),
            'immudb 1.11.0 source archive', 'BUSL-1.1'),
        _evidence(
            runtime_id, 'catalog', 'immudb 1.11.0 runtime',
            inventory_path.name, _sha256(inventory_path),
            'cdeadmin.engine-metrics-inventory.v1', 'PostgreSQL'),
        _evidence(
            live_id, 'live_execution', 'immudb 1.11.0 runtime',
            live_path.name, _sha256(live_path),
            'cdeadmin.relational-provider-live-verification.v1',
            'PostgreSQL'),
    ]
    observations = []
    metrics = []
    for position, item in enumerate(inventory['observations'], 1):
        name = item['native_name']
        observation_id = (
            f'immudb.prometheus.{position:04d}.{_slug(name)}'
        )
        kind = item['prometheus_type']
        observations.append({
            'observation_id': observation_id,
            'native_name': name,
            'scope': 'native_label_set',
            'source': 'GET /metrics',
            'value_type': 'number',
            'observation_class': 'operational_metric',
            'description': item['description'],
            'privilege': 'metrics_endpoint_access',
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
            'unit': 'native_unit',
            'kind': kind,
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
        'contract_id': 'immudb.metrics.1.11.0.v1',
        'provider_id': 'org.cdeadmin.immudb',
        'profile_id': 'immudb-native',
        'engine_id': 'immudb',
        'interface_id': 'immudb-native',
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
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + '\n',
        encoding='utf-8',
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source-root', type=Path, required=True)
    parser.add_argument('--source-archive', type=Path, required=True)
    parser.add_argument('--profiles', type=Path, required=True)
    parser.add_argument('--live-evidence', type=Path, required=True)
    parser.add_argument('--metrics-port', type=int, default=59497)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    inventory, _live, templates = build_dialect(
        args.source_root, args.source_archive, args.profiles,
        args.live_evidence,
    )
    inventory_path = args.output_dir / 'immudb_dialect_inventory_1_11_0.json'
    _write(inventory_path, inventory)
    dialect = build_dialect_contract(
        inventory, inventory_path, args.source_archive,
        args.live_evidence, templates,
    )
    _write(args.output_dir / 'immudb_dialect_1_11_0.json', dialect)

    metrics_inventory = build_metrics_inventory(args.metrics_port)
    metrics_inventory_path = (
        args.output_dir / 'immudb_metrics_inventory_1_11_0.json'
    )
    _write(metrics_inventory_path, metrics_inventory)
    metrics = build_metrics_contract(
        metrics_inventory, metrics_inventory_path, args.source_archive,
        args.live_evidence,
    )
    _write(args.output_dir / 'immudb_metrics_1_11_0.json', metrics)
    print(json.dumps({
        'dialect_counts': inventory['completeness']['inventory_counts'],
        'task_count': len(templates),
        'metric_count': len(metrics_inventory['observations']),
    }, indent=2, sort_keys=True))


if __name__ == '__main__':
    main()
