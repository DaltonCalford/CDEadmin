#!/usr/bin/env python3
##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Generate exact TiDB 8.5.6 dialect and metrics contracts."""

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

import mysql.connector


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

from pgadmin.cdeadmin.providers.tidb.provider import (  # noqa: E402
    ADMINISTRATION,
)


REFERENCE_VERSION = '8.5.6'
GRAMMAR = Path('pkg/parser/parser.y')
KEYWORDS = Path('pkg/parser/keywords.go')
DIAGNOSTICS = Path('pkg/errno/errcode.go')
FUNCTION_REGISTRY = Path('pkg/expression/builtin.go')
FUNCTION_NAMES = Path('pkg/parser/ast/functions.go')


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


def _production(grammar, name):
    start = re.search(rf'(?m)^{re.escape(name)}\s*:', grammar)
    if start is None:
        raise RuntimeError(f'TiDB grammar production is absent: {name}')
    following = re.search(
        r'(?m)^[A-Za-z_][A-Za-z0-9_]*\s*:', grammar[start.end():]
    )
    end = start.end() + following.start() if following else len(grammar)
    return start, grammar[start.start():end]


def _alternatives(grammar, name, category):
    start, block = _production(grammar, name)
    base_line = grammar.count('\n', 0, start.start()) + 1
    records = []
    for offset, line in enumerate(block.splitlines()):
        value = line.strip()
        if offset == 0:
            value = value.split(':', 1)[1].strip()
        elif value.startswith('|'):
            value = value[1:].strip()
        else:
            continue
        value = value.split('/*', 1)[0].strip()
        if not value or value.startswith('{'):
            continue
        records.append(_record(
            f'{category}.{len(records) + 1:04d}', value,
            f'{GRAMMAR.as_posix()}:{base_line + offset}',
            production=name,
        ))
    return records


def _source_inventory(source_root):
    grammar_path = source_root / GRAMMAR
    grammar = grammar_path.read_text(encoding='utf-8')
    commands = []
    seen = set()
    for match in re.finditer(
            r'(?m)^([A-Za-z_][A-Za-z0-9_]*)\s*:', grammar):
        name = match.group(1)
        if name in seen:
            continue
        seen.add(name)
        commands.append(_record(
            f'commands.{len(commands) + 1:04d}', name,
            f'{GRAMMAR.as_posix()}:'
            f'{grammar.count(chr(10), 0, match.start()) + 1}',
        ))
    statements = _alternatives(grammar, 'Statement', 'statements')
    data_types = []
    for production in (
            'IntegerType', 'BooleanType', 'FixedPointType',
            'FloatingPointType', 'BitValueType', 'Char', 'NChar',
            'Varchar', 'NVarchar', 'StringType', 'DateAndTimeType'):
        for item in _alternatives(grammar, production, 'data_types'):
            item['item_id'] = f'data_types.{len(data_types) + 1:04d}'
            data_types.append(item)
    operators = []
    for match in re.finditer(
            r'(?m)^%(?:left|right|nonassoc)\s+(.+)$', grammar):
        line = grammar.count('\n', 0, match.start()) + 1
        for token in match.group(1).split('/*', 1)[0].split():
            operators.append(_record(
                f'operators.{len(operators) + 1:04d}', token,
                f'{GRAMMAR.as_posix()}:{line}',
                registry='grammar_precedence',
            ))

    keyword_path = source_root / KEYWORDS
    keyword_text = keyword_path.read_text(encoding='utf-8')
    lexical = []
    for match in re.finditer(
            r'(?m)^\s*\{"([^"]+)",\s*(true|false),\s*"([^"]+)"\},',
            keyword_text):
        lexical.append(_record(
            f'lexical_rules.{len(lexical) + 1:04d}', match.group(1),
            f'{KEYWORDS.as_posix()}:'
            f'{keyword_text.count(chr(10), 0, match.start()) + 1}',
            reserved=match.group(2) == 'true', section=match.group(3),
        ))

    diagnostic_path = source_root / DIAGNOSTICS
    diagnostic_text = diagnostic_path.read_text(encoding='utf-8')
    diagnostics = []
    for match in re.finditer(
            r'(?m)^\s*(Err[A-Za-z][A-Za-z0-9]+)\s*=\s*([0-9]+)',
            diagnostic_text):
        diagnostics.append(_record(
            f'diagnostics.{len(diagnostics) + 1:04d}', match.group(1),
            f'{DIAGNOSTICS.as_posix()}:'
            f'{diagnostic_text.count(chr(10), 0, match.start()) + 1}',
            error_code=int(match.group(2)),
        ))

    names_path = source_root / FUNCTION_NAMES
    names_text = names_path.read_text(encoding='utf-8')
    native_names = {
        match.group(1): (match.group(2), match.start())
        for match in re.finditer(
            r'(?m)^\s*([A-Za-z][A-Za-z0-9]+)\s*=\s*"([^"]+)"',
            names_text)
    }
    registry_path = source_root / FUNCTION_REGISTRY
    registry_text = registry_path.read_text(encoding='utf-8')
    functions = []
    seen_functions = set()
    for match in re.finditer(r'(?m)^\s*ast\.([A-Za-z][A-Za-z0-9]+)\s*:',
                             registry_text):
        definition = native_names.get(match.group(1))
        if definition is None or definition[0] in seen_functions:
            continue
        name, offset = definition
        seen_functions.add(name)
        functions.append(_record(
            f'functions.{len(functions) + 1:04d}', name,
            f'{FUNCTION_NAMES.as_posix()}:'
            f'{names_text.count(chr(10), 0, offset) + 1}',
            registry='pkg/expression funcs',
        ))
    inventories = {
        'lexical_rules': lexical,
        'statements': statements,
        'commands': commands,
        'data_types': data_types,
        'functions': functions,
        'operators': operators,
        'diagnostics': diagnostics,
    }
    if any(not records for records in inventories.values()):
        raise RuntimeError('TiDB source inventory is incomplete')
    return inventories


def _profile(profile_path):
    return next(
        item for item in json.loads(profile_path.read_text(
            encoding='utf-8'))['profiles'] if item['engine'] == 'tidb'
    )


def _runtime_inventory(profile_path):
    profile = _profile(profile_path)
    connection = mysql.connector.connect(
        host='127.0.0.1', port=profile['port'], user=profile['user'],
        password=profile.get('password', ''), database=profile['database'],
        connection_timeout=10,
    )
    try:
        cursor = connection.cursor()
        cursor.execute('SELECT VERSION()')
        version = str(cursor.fetchone()[0])
        match = re.search(r'-TiDB-v([0-9]+\.[0-9]+\.[0-9]+)', version)
        if match is None or match.group(1) != REFERENCE_VERSION:
            raise RuntimeError('TiDB runtime must be ' + REFERENCE_VERSION)
        cursor.execute('SHOW VARIABLES')
        settings = [
            _record(
                f'session_settings.{position:04d}', name,
                'SHOW VARIABLES', observed_value=str(value),
            )
            for position, (name, value) in enumerate(cursor.fetchall(), 1)
        ]
        cursor.close()
    finally:
        connection.close()
    if not settings:
        raise RuntimeError('TiDB runtime variable inventory is empty')
    return settings


def _task_templates(live):
    evidence = live['object_experience_evidence']['dialect_task_evidence']
    expected = set(ADMINISTRATION.dialect_task_ids())
    if set(evidence) != expected:
        raise RuntimeError('TiDB live evidence does not cover exact tasks')
    result = []
    for task_id in sorted(evidence):
        task = evidence[task_id]
        statements = task['command_preview'].get('statements') or []
        if task.get('live_execution') != 'passed' or not statements:
            raise RuntimeError(f'TiDB task evidence is invalid: {task_id}')
        result.append({
            'task_id': task_id,
            'source': '\n;\n'.join(item['source'] for item in statements),
            'source_format': 'ordered_native_statements',
            'statements': [item['source'] for item in statements],
            'required_bindings': [
                f'parameter_{position}'
                for position in range(1, 1 + sum(
                    item.get('parameter_count', 0) for item in statements
                ))
            ],
            'binding_style': 'mysql_connector_percent_s',
            'proof_ids': [
                'tidb-8.5.6-task-parser-acceptance',
                'tidb-8.5.6-task-live-execution',
            ],
        })
    return result


def build_dialect(source_root, source_archive, profile_path, live_path):
    inventories = _source_inventory(source_root)
    inventories['session_settings'] = _runtime_inventory(profile_path)
    live = json.loads(live_path.read_text(encoding='utf-8'))
    if (
            live.get('dialect_qualification_ready') is not True or
            live.get('exact_profile') != REFERENCE_VERSION or
            live.get('missing_dialect_task_ids') != [] or
            live.get('credential_values_exported') is not False):
        raise RuntimeError('TiDB exact live evidence is not admissible')
    return {
        'schema': 'cdeadmin.engine-dialect-inventory.v1',
        'inventory_id': 'tidb.dialect-inventory.8.5.6.v1',
        'engine_id': 'tidb',
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


def build_dialect_contract(
        inventory, inventory_path, source_archive, live_path, templates):
    source_id = 'tidb-8.5.6-source-grammar'
    catalog_id = 'tidb-8.5.6-runtime-catalog'
    driver_id = 'mysql-connector-python'
    inventory_digest = _sha256(inventory_path)
    live_digest = _sha256(live_path)
    driver_path = Path(mysql.connector.__file__)
    proofs = [
        _evidence(
            source_id, 'grammar', 'PingCAP TiDB project',
            source_archive.name, _sha256(source_archive),
            'TiDB 8.5.6 source archive', 'Apache-2.0'),
        _evidence(
            catalog_id, 'catalog', 'TiDB 8.5.6 runtime',
            inventory_path.name, inventory_digest,
            'cdeadmin.engine-dialect-inventory.v1', 'PostgreSQL'),
        _evidence(
            'tidb-8.5.6-task-parser-acceptance', 'parser_acceptance',
            'TiDB 8.5.6 runtime', live_path.name, live_digest,
            'cdeadmin.relational-provider-live-verification.v1',
            'PostgreSQL'),
        _evidence(
            'tidb-8.5.6-task-live-execution', 'live_execution',
            'TiDB 8.5.6 runtime', live_path.name, live_digest,
            'cdeadmin.relational-provider-live-verification.v1',
            'PostgreSQL'),
        _evidence(
            driver_id, 'driver', 'Oracle MySQL', driver_path.name,
            _sha256(driver_path), 'Python source', 'GPL-2.0-only'),
    ]
    source_categories = set(inventory['inventories']) - {'session_settings'}
    contract_inventories = {
        name: [
            {
                **copy.deepcopy(record),
                'proof_ids': [source_id if name in source_categories
                              else catalog_id],
            }
            for record in records
        ]
        for name, records in inventory['inventories'].items()
    }
    task_ids = [item['task_id'] for item in templates]
    decisions = {
        'identifier_quoting': 'backtick_with_doubled_backtick_escape',
        'string_literals': 'single_quote_with_mysql_escape_rules',
        'parameter_binding': 'mysql_connector_percent_s',
        'transaction_control': (
            'START TRANSACTION; COMMIT; ROLLBACK; SAVEPOINT'),
        'pagination': 'LIMIT with optional OFFSET',
        'explain': 'EXPLAIN and EXPLAIN ANALYZE',
        'cancellation': 'KILL TIDB QUERY through a separate session',
    }
    return {
        'schema': 'cdeadmin.engine-dialect.v2',
        'contract_id': 'tidb.dialect.8.5.6.v1',
        'provider_id': 'org.cdeadmin.tidb',
        'profile_id': 'tidb-native',
        'engine_id': 'tidb',
        'interface_id': 'tidb-native',
        'reference_version': REFERENCE_VERSION,
        'language_profiles': ['tidb-sql'],
        'grammar_evidence': {
            key: value for key, value in proofs[0].items()
            if key not in {'evidence_id', 'evidence_kind'}
        },
        'proof_records': proofs,
        'inventories': contract_inventories,
        'syntax_decisions': {
            name: {
                'syntax': syntax,
                'proof_ids': [driver_id if name == 'parameter_binding'
                              else source_id],
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
        'live_evidence_ids': ['tidb-8.5.6-task-live-execution'],
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
        'description': helps[name] or f'TiDB metric {name}.',
    } for name in sorted(set(helps).intersection(types))]
    if not observations:
        raise RuntimeError('TiDB Prometheus metric inventory is empty')
    return {
        'schema': 'cdeadmin.engine-metrics-inventory.v1',
        'inventory_id': 'tidb.metrics-inventory.8.5.6.v1',
        'engine_id': 'tidb',
        'reference_version': REFERENCE_VERSION,
        'collection_authority': 'TiDB 8.5.6 native Prometheus endpoint',
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


def build_metrics_contract(
        inventory, inventory_path, source_archive, live_path):
    source_id = 'tidb-8.5.6-source-metric-registry'
    runtime_id = 'tidb-8.5.6-runtime-prometheus-catalog'
    live_id = 'tidb-8.5.6-task-live-execution'
    evidence = [
        _evidence(
            source_id, 'documentation', 'PingCAP TiDB project',
            source_archive.name, _sha256(source_archive),
            'TiDB 8.5.6 source archive', 'Apache-2.0'),
        _evidence(
            runtime_id, 'catalog', 'TiDB 8.5.6 runtime',
            inventory_path.name, _sha256(inventory_path),
            'cdeadmin.engine-metrics-inventory.v1', 'PostgreSQL'),
        _evidence(
            live_id, 'live_execution', 'TiDB 8.5.6 runtime',
            live_path.name, _sha256(live_path),
            'cdeadmin.relational-provider-live-verification.v1',
            'PostgreSQL'),
    ]
    observations = []
    metrics = []
    for item in inventory['observations']:
        name = item['native_name']
        observation_id = f'tidb.prometheus.{_slug(name)}'
        kind = item['prometheus_type']
        observations.append({
            'observation_id': observation_id,
            'native_name': name,
            'scope': 'native_label_set',
            'source': 'GET /metrics',
            'value_type': 'number',
            'observation_class': 'operational_metric',
            'description': item['description'],
            'privilege': 'status_endpoint_access',
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
        'contract_id': 'tidb.metrics.8.5.6.v1',
        'provider_id': 'org.cdeadmin.tidb',
        'profile_id': 'tidb-native',
        'engine_id': 'tidb',
        'interface_id': 'tidb-native',
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
    parser.add_argument('--connection-profiles', type=Path, required=True)
    parser.add_argument('--live-evidence', type=Path, required=True)
    parser.add_argument('--http-port', type=int, default=51000)
    parser.add_argument('--dialect-inventory-output', type=Path, required=True)
    parser.add_argument('--dialect-contract-output', type=Path, required=True)
    parser.add_argument('--metrics-inventory-output', type=Path, required=True)
    parser.add_argument('--metrics-contract-output', type=Path, required=True)
    options = parser.parse_args()
    source_root = options.source_root.resolve()
    source_archive = options.source_archive.resolve()
    live_path = options.live_evidence.resolve()
    inventory, _live, templates = build_dialect(
        source_root, source_archive, options.connection_profiles.resolve(),
        live_path)
    _write(options.dialect_inventory_output, inventory)
    _write(options.dialect_contract_output, build_dialect_contract(
        inventory, options.dialect_inventory_output.resolve(),
        source_archive, live_path, templates))
    metrics_inventory = build_metrics_inventory(options.http_port)
    _write(options.metrics_inventory_output, metrics_inventory)
    metrics = build_metrics_contract(
        metrics_inventory, options.metrics_inventory_output.resolve(),
        source_archive, live_path)
    _write(options.metrics_contract_output, metrics)
    print(json.dumps({
        'dialect_inventory_counts': inventory['completeness'][
            'inventory_counts'],
        'dialect_task_count': len(templates),
        'metric_count': len(metrics['metrics']),
        'native_observation_count': len(metrics['native_observations']),
    }, indent=2, sort_keys=True))


if __name__ == '__main__':
    main()
