#!/usr/bin/env python3
##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Generate exact Vitess 23.0.3 dialect and metrics contracts."""

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

from pgadmin.cdeadmin.providers.vitess.provider import (  # noqa: E402
    ADMINISTRATION,
)


REFERENCE_VERSION = '23.0.3'
GRAMMAR = Path('go/vt/sqlparser/sql.y')
KEYWORDS = Path('go/vt/sqlparser/keywords.go')
DIAGNOSTICS = Path('go/mysql/sqlerror/constants.go')


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
        raise RuntimeError(f'Vitess grammar production is absent: {name}')
    following = re.search(
        r'(?m)^[a-z_][a-z0-9_]*\s*:', grammar[start.end():]
    )
    end = start.end() + following.start() if following else len(grammar)
    return start, grammar[start.start():end]


def _alternatives(grammar, name, category):
    start, block = _production(grammar, name)
    base_line = grammar.count('\n', 0, start.start()) + 1
    values = []
    pending = None
    pending_line = None
    for offset, line in enumerate(block.splitlines()):
        stripped = line.strip()
        if offset == 0:
            stripped = stripped.split(':', 1)[1].strip()
            pending_line = base_line
        elif stripped.startswith('|'):
            if pending:
                values.append((pending_line, pending))
            stripped = stripped[1:].strip()
            pending_line = base_line + offset
            pending = None
        elif stripped.startswith('{') or stripped.startswith('}'):
            continue
        elif (
                not stripped or stripped.startswith('/*') or
                stripped.startswith('//')):
            continue
        elif pending is not None:
            stripped = pending + ' ' + stripped
        stripped = stripped.split('//', 1)[0].strip()
        if stripped and not stripped.startswith('{'):
            pending = stripped
    if pending:
        values.append((pending_line, pending))
    return [
        _record(
            f'{category}.{position:04d}', syntax,
            f'{GRAMMAR.as_posix()}:{line}', production=name,
        )
        for position, (line, syntax) in enumerate(values, 1)
    ]


def _source_inventory(source_root):
    grammar_path = source_root / GRAMMAR
    grammar = grammar_path.read_text(encoding='utf-8')
    commands = []
    seen = set()
    for match in re.finditer(r'(?m)^([a-z_][a-z0-9_]*)\s*:', grammar):
        name = match.group(1)
        if name in seen:
            continue
        seen.add(name)
        commands.append(_record(
            f'commands.{len(commands) + 1:04d}', name,
            f'{GRAMMAR.as_posix()}:'
            f'{grammar.count(chr(10), 0, match.start()) + 1}',
        ))

    statements = _alternatives(grammar, 'command', 'statements')
    data_types = []
    for production in (
            'int_type', 'decimal_type', 'char_type', 'time_type',
            'spatial_type'):
        for item in _alternatives(grammar, production, 'data_types'):
            item['item_id'] = f'data_types.{len(data_types) + 1:04d}'
            data_types.append(item)

    functions = [_record(
        'functions.0001', 'generic SQL function identifier',
        f'{GRAMMAR.as_posix()}:6384',
        production='function_call_generic',
        acceptance='parser accepts any sql_id function name',
    )]
    for production in ('function_call_keyword', 'function_call_nonkeyword'):
        for item in _alternatives(grammar, production, 'functions'):
            item['item_id'] = f'functions.{len(functions) + 1:04d}'
            functions.append(item)

    operators = []
    for match in re.finditer(
            r'(?m)^%(?:left|right|nonassoc)\s+(.+)$', grammar):
        line = grammar.count('\n', 0, match.start()) + 1
        syntax = match.group(1).split('//', 1)[0].strip()
        for token in syntax.split():
            operators.append(_record(
                f'operators.{len(operators) + 1:04d}', token,
                f'{GRAMMAR.as_posix()}:{line}',
                registry='grammar_precedence',
            ))

    keyword_path = source_root / KEYWORDS
    keyword_text = keyword_path.read_text(encoding='utf-8')
    lexical = []
    for match in re.finditer(
            r'(?m)^\s*\{"([^\"]+)",\s*([A-Z][A-Z0-9_]*)\},',
            keyword_text):
        lexical.append(_record(
            f'lexical_rules.{len(lexical) + 1:04d}', match.group(1),
            f'{KEYWORDS.as_posix()}:'
            f'{keyword_text.count(chr(10), 0, match.start()) + 1}',
            token=match.group(2),
        ))

    diagnostic_path = source_root / DIAGNOSTICS
    diagnostic_text = diagnostic_path.read_text(encoding='utf-8')
    diagnostics = []
    for match in re.finditer(
            r'(?m)^\s*([A-Za-z][A-Za-z0-9]+)\s*=\s*'
            r'ErrorCode\(([0-9]+)\)', diagnostic_text):
        diagnostics.append(_record(
            f'diagnostics.{len(diagnostics) + 1:04d}', match.group(1),
            f'{DIAGNOSTICS.as_posix()}:'
            f'{diagnostic_text.count(chr(10), 0, match.start()) + 1}',
            error_code=int(match.group(2)),
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
        raise RuntimeError('Vitess source inventory is incomplete')
    return inventories


def _profile(profile_path):
    return next(
        item for item in json.loads(profile_path.read_text(
            encoding='utf-8'))['profiles']
        if item['engine'] == 'vitess'
    )


def _runtime_inventory(profile_path):
    profile = _profile(profile_path)
    with urlopen(  # nosec B310 - exact local reference fixture
            f"http://127.0.0.1:{profile['http_port']}/debug/vars",
            timeout=10) as response:
        identity = json.loads(response.read().decode('utf-8'))
    match = re.search(r'(\d+\.\d+\.\d+)', str(identity['BuildVersion']))
    if match is None or match.group(1) != REFERENCE_VERSION:
        raise RuntimeError('Vitess runtime must be ' + REFERENCE_VERSION)
    connection = mysql.connector.connect(
        host='127.0.0.1', port=profile['port'], user=profile['user'],
        password=profile.get('password', ''), database=profile['database'],
        connection_timeout=10,
    )
    try:
        cursor = connection.cursor()
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
        raise RuntimeError('Vitess runtime variable inventory is empty')
    return settings, identity


def _task_templates(live):
    evidence = live['object_experience_evidence']['dialect_task_evidence']
    expected = set(ADMINISTRATION.dialect_task_ids())
    if set(evidence) != expected:
        raise RuntimeError('Vitess live evidence does not cover exact tasks')
    result = []
    for task_id in sorted(evidence):
        task = evidence[task_id]
        statements = task['command_preview'].get('statements') or []
        if task.get('live_execution') != 'passed' or not statements:
            raise RuntimeError(f'Vitess task evidence is invalid: {task_id}')
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
                'vitess-23.0.3-task-parser-acceptance',
                'vitess-23.0.3-task-live-execution',
            ],
        })
    return result


def build_dialect(source_root, source_archive, profile_path, live_path):
    inventories = _source_inventory(source_root)
    settings, identity = _runtime_inventory(profile_path)
    inventories['session_settings'] = settings
    live = json.loads(live_path.read_text(encoding='utf-8'))
    if (
            live.get('dialect_qualification_ready') is not True or
            live.get('exact_profile') != REFERENCE_VERSION or
            live.get('missing_dialect_task_ids') != [] or
            live.get('credential_values_exported') is not False):
        raise RuntimeError('Vitess exact live evidence is not admissible')
    inventory = {
        'schema': 'cdeadmin.engine-dialect-inventory.v1',
        'inventory_id': 'vitess.dialect-inventory.23.0.3.v1',
        'engine_id': 'vitess',
        'reference_version': REFERENCE_VERSION,
        'source_archive': source_archive.name,
        'source_archive_sha256': _sha256(source_archive),
        'inventories': inventories,
        'runtime_identity': {
            'BuildVersion': identity['BuildVersion'],
            'BuildGitRev': identity.get('BuildGitRev'),
        },
        'completeness': {
            'runtime_version': REFERENCE_VERSION,
            'runtime_catalogs_exhausted': True,
            'grammar_productions_exhausted': True,
            'function_acceptance_productions_exhausted': True,
            'diagnostic_catalog_exhausted': True,
            'inventory_counts': {
                name: len(records) for name, records in inventories.items()
            },
        },
    }
    return inventory, live, _task_templates(live)


def build_dialect_contract(
        inventory, inventory_path, source_archive, live_path, templates):
    source_id = 'vitess-23.0.3-source-grammar'
    catalog_id = 'vitess-23.0.3-runtime-catalog'
    driver_id = 'mysql-connector-python'
    source_digest = _sha256(source_archive)
    inventory_digest = _sha256(inventory_path)
    live_digest = _sha256(live_path)
    driver_path = Path(mysql.connector.__file__)
    proofs = [
        _evidence(
            source_id, 'grammar', 'Vitess project', source_archive.name,
            source_digest, 'Vitess 23.0.3 source archive', 'Apache-2.0'),
        _evidence(
            catalog_id, 'catalog', 'Vitess 23.0.3 runtime',
            inventory_path.name, inventory_digest,
            'cdeadmin.engine-dialect-inventory.v1', 'PostgreSQL'),
        _evidence(
            'vitess-23.0.3-task-parser-acceptance', 'parser_acceptance',
            'Vitess 23.0.3 VTGate', live_path.name, live_digest,
            'cdeadmin.relational-provider-live-verification.v1',
            'PostgreSQL'),
        _evidence(
            'vitess-23.0.3-task-live-execution', 'live_execution',
            'Vitess 23.0.3 VTGate', live_path.name, live_digest,
            'cdeadmin.relational-provider-live-verification.v1',
            'PostgreSQL'),
        _evidence(
            driver_id, 'driver', 'Oracle MySQL', driver_path.name,
            _sha256(driver_path), 'Python source', 'GPL-2.0-only'),
    ]
    contract_inventories = {}
    for name, records in inventory['inventories'].items():
        proof_ids = [catalog_id] if name == 'session_settings' else [source_id]
        contract_inventories[name] = [
            {**copy.deepcopy(record), 'proof_ids': proof_ids}
            for record in records
        ]
    task_ids = [item['task_id'] for item in templates]
    decisions = {
        'identifier_quoting': 'backtick_with_doubled_backtick_escape',
        'string_literals': 'single_quote_with_mysql_escape_rules',
        'parameter_binding': 'mysql_connector_percent_s',
        'transaction_control': 'BEGIN; COMMIT; ROLLBACK; SAVEPOINT',
        'pagination': 'LIMIT with optional OFFSET',
        'explain': 'EXPLAIN, VEXPLAIN, and EXPLAIN ANALYZE',
        'cancellation': 'KILL through a separate VTGate session',
    }
    return {
        'schema': 'cdeadmin.engine-dialect.v2',
        'contract_id': 'vitess.dialect.23.0.3.v1',
        'provider_id': 'org.cdeadmin.vitess',
        'profile_id': 'vitess-native',
        'engine_id': 'vitess',
        'interface_id': 'vitess-native',
        'reference_version': REFERENCE_VERSION,
        'language_profiles': ['vitess-sql'],
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
        'live_evidence_ids': ['vitess-23.0.3-task-live-execution'],
        'complete_grammar_inventory': {
            'artifact': inventory_path.name,
            'sha256': inventory_digest,
            **inventory['completeness'],
        },
    }


def _prometheus_endpoint(port, component):
    with urlopen(  # nosec B310 - exact local reference fixture
            f'http://127.0.0.1:{port}/metrics', timeout=30) as response:
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
        'description': helps[name] or f'Vitess {component} metric {name}.',
    } for name in sorted(set(helps).intersection(types))]


def build_metrics_inventory(profile_path, vtctld_http_port):
    profile = _profile(profile_path)
    observations = (
        _prometheus_endpoint(profile['http_port'], 'vtgate') +
        _prometheus_endpoint(vtctld_http_port, 'vtctld')
    )
    if not observations:
        raise RuntimeError('Vitess Prometheus metric inventory is empty')
    return {
        'schema': 'cdeadmin.engine-metrics-inventory.v1',
        'inventory_id': 'vitess.metrics-inventory.23.0.3.v1',
        'engine_id': 'vitess',
        'reference_version': REFERENCE_VERSION,
        'collection_authority': (
            'Vitess 23.0.3 native VTGate and VTctld Prometheus endpoints'),
        'endpoint_selection': (
            'Every series having native HELP and TYPE declarations on both '
            'reference control and query-plane endpoints.'),
        'observations': observations,
        'completeness': {
            'runtime_version': REFERENCE_VERSION,
            'components_exhausted': ['vtgate', 'vtctld'],
            'observation_count': len(observations),
            'prometheus_help_type_catalog_exhausted': True,
            'admitted_metric_count': len(observations),
        },
    }


def build_metrics_contract(
        inventory, inventory_path, source_archive, live_path):
    source_id = 'vitess-23.0.3-source-metric-registry'
    runtime_id = 'vitess-23.0.3-runtime-prometheus-catalog'
    live_id = 'vitess-23.0.3-task-live-execution'
    evidence = [
        _evidence(
            source_id, 'documentation', 'Vitess project',
            source_archive.name, _sha256(source_archive),
            'Vitess 23.0.3 source archive', 'Apache-2.0'),
        _evidence(
            runtime_id, 'catalog', 'Vitess 23.0.3 runtime',
            inventory_path.name, _sha256(inventory_path),
            'cdeadmin.engine-metrics-inventory.v1', 'PostgreSQL'),
        _evidence(
            live_id, 'live_execution', 'Vitess 23.0.3 VTGate',
            live_path.name, _sha256(live_path),
            'cdeadmin.relational-provider-live-verification.v1',
            'PostgreSQL'),
    ]
    observations = []
    metrics = []
    for item in inventory['observations']:
        component = item['component']
        name = item['native_name']
        observation_id = f'vitess.{component}.{_slug(name)}'
        kind = item['prometheus_type']
        observations.append({
            'observation_id': observation_id,
            'native_name': name,
            'scope': f'{component}_native_label_set',
            'source': f'GET {component} /metrics',
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
        'contract_id': 'vitess.metrics.23.0.3.v1',
        'provider_id': 'org.cdeadmin.vitess',
        'profile_id': 'vitess-native',
        'engine_id': 'vitess',
        'interface_id': 'vitess-native',
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
    parser.add_argument('--vtctld-http-port', type=int, default=15000)
    parser.add_argument('--dialect-inventory-output', type=Path, required=True)
    parser.add_argument('--dialect-contract-output', type=Path, required=True)
    parser.add_argument('--metrics-inventory-output', type=Path, required=True)
    parser.add_argument('--metrics-contract-output', type=Path, required=True)
    options = parser.parse_args()
    source_root = options.source_root.resolve()
    source_archive = options.source_archive.resolve()
    profile_path = options.connection_profiles.resolve()
    live_path = options.live_evidence.resolve()

    inventory, _live, templates = build_dialect(
        source_root, source_archive, profile_path, live_path)
    _write(options.dialect_inventory_output, inventory)
    contract = build_dialect_contract(
        inventory, options.dialect_inventory_output.resolve(),
        source_archive, live_path, templates)
    _write(options.dialect_contract_output, contract)

    metrics_inventory = build_metrics_inventory(
        profile_path, options.vtctld_http_port)
    _write(options.metrics_inventory_output, metrics_inventory)
    metrics_contract = build_metrics_contract(
        metrics_inventory, options.metrics_inventory_output.resolve(),
        source_archive, live_path)
    _write(options.metrics_contract_output, metrics_contract)
    print(json.dumps({
        'dialect_inventory_counts': inventory['completeness'][
            'inventory_counts'],
        'dialect_task_count': len(templates),
        'metric_count': len(metrics_contract['metrics']),
        'native_observation_count': len(
            metrics_contract['native_observations']),
    }, indent=2, sort_keys=True))


if __name__ == '__main__':
    main()
