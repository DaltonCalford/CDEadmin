#!/usr/bin/env python3
##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Generate exact Dolt 1.86.6 dialect and metrics contracts."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import sys
from pathlib import Path
from types import ModuleType

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

from pgadmin.cdeadmin.providers.dolt.provider import (  # noqa: E402
    ADMINISTRATION,
)


REFERENCE_VERSION = '1.86.6'
GRAMMAR = Path('go/vt/sqlparser/sql.y')
DIAGNOSTICS = Path('go/mysql/constants.go')
DOLT_FUNCTIONS = Path('go/libraries/doltcore/sqle/dfunctions')
DOLT_TABLE_FUNCTIONS = Path('go/libraries/doltcore/sqle/dtablefunctions')
GMS_FUNCTIONS = Path('sql/expression/function/registry.go')


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
        raise RuntimeError(f'Dolt parser production is absent: {name}')
    following = re.search(
        r'(?m)^[a-z_][a-z0-9_]*\s*:', grammar[start.end():]
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
        value = value.split('//', 1)[0].strip()
        if not value or value.startswith('{'):
            continue
        records.append(_record(
            f'{category}.{len(records) + 1:04d}', value,
            f'{GRAMMAR.as_posix()}:{base_line + offset}',
            production=name,
        ))
    return records


def _source_inventory(dolt_root, parser_root, gms_root):
    grammar_path = parser_root / GRAMMAR
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
            authority='dolt-go.mod-pinned-dolthub-vitess',
        ))
    statements = _alternatives(grammar, 'command', 'statements')
    data_types = []
    for production in (
            'int_type', 'decimal_type', 'char_type', 'time_type',
            'spatial_type'):
        for item in _alternatives(grammar, production, 'data_types'):
            item['item_id'] = f'data_types.{len(data_types) + 1:04d}'
            data_types.append(item)
    operators = []
    for match in re.finditer(
            r'(?m)^%(?:left|right|nonassoc)\s+(.+)$', grammar):
        line = grammar.count('\n', 0, match.start()) + 1
        for token in match.group(1).split('//', 1)[0].split():
            operators.append(_record(
                f'operators.{len(operators) + 1:04d}', token,
                f'{GRAMMAR.as_posix()}:{line}',
                registry='grammar_precedence',
            ))
    diagnostic_path = parser_root / DIAGNOSTICS
    diagnostic_text = diagnostic_path.read_text(encoding='utf-8')
    diagnostics = []
    for match in re.finditer(
            r'(?m)^\s*(ER[A-Za-z][A-Za-z0-9]+)\s*=\s*([0-9]+)',
            diagnostic_text):
        diagnostics.append(_record(
            f'diagnostics.{len(diagnostics) + 1:04d}', match.group(1),
            f'{DIAGNOSTICS.as_posix()}:'
            f'{diagnostic_text.count(chr(10), 0, match.start()) + 1}',
            error_code=int(match.group(2)),
        ))

    functions = []
    registry_path = gms_root / GMS_FUNCTIONS
    registry = registry_path.read_text(encoding='utf-8')
    for match in re.finditer(
            r'(?:Name:\s*|NewFunction[0-9N]?\()"([a-z0-9_]+)"',
            registry):
        name = match.group(1)
        if any(item['native_name'] == name for item in functions):
            continue
        functions.append(_record(
            f'functions.{len(functions) + 1:04d}', name,
            f'{GMS_FUNCTIONS.as_posix()}:'
            f'{registry.count(chr(10), 0, match.start()) + 1}',
            registry='go-mysql-server BuiltIns',
        ))

    constant_values = {}
    dolt_function_files = sorted(
        (dolt_root / DOLT_FUNCTIONS).glob('*.go'))
    for path in dolt_function_files:
        text = path.read_text(encoding='utf-8')
        for match in re.finditer(
                r'(?m)^\s*([A-Za-z][A-Za-z0-9]*FuncName)\s*='
                r'\s*"([^"]+)"', text):
            constant_values[match.group(1)] = (
                match.group(2), path, match.start(), text)
    init_path = dolt_root / DOLT_FUNCTIONS / 'init.go'
    init_text = init_path.read_text(encoding='utf-8')
    for match in re.finditer(r'Name:\s*([A-Za-z][A-Za-z0-9]*FuncName)',
                             init_text):
        definition = constant_values.get(match.group(1))
        if definition is None:
            continue
        name, path, offset, text = definition
        if any(item['native_name'] == name for item in functions):
            continue
        functions.append(_record(
            f'functions.{len(functions) + 1:04d}', name,
            f'{path.relative_to(dolt_root).as_posix()}:'
            f'{text.count(chr(10), 0, offset) + 1}',
            registry='DoltFunctions',
        ))

    table_root = dolt_root / DOLT_TABLE_FUNCTIONS
    for path in sorted(table_root.glob('*.go')):
        text = path.read_text(encoding='utf-8')
        for match in re.finditer(
                r'(?m)^\s*return\s+"(dolt_[a-z0-9_]+)"\s*$', text):
            name = match.group(1)
            if any(item['native_name'] == name for item in functions):
                continue
            functions.append(_record(
                f'functions.{len(functions) + 1:04d}', name,
                f'{path.relative_to(dolt_root).as_posix()}:'
                f'{text.count(chr(10), 0, match.start()) + 1}',
                registry='DoltTableFunctions', table_function=True,
            ))
    inventories = {
        'statements': statements,
        'commands': commands,
        'data_types': data_types,
        'functions': functions,
        'operators': operators,
        'diagnostics': diagnostics,
    }
    if any(not records for records in inventories.values()):
        raise RuntimeError('Dolt source inventory is incomplete')
    return inventories, grammar_path, diagnostic_path, registry_path


def _profile(profile_path):
    return next(
        item for item in json.loads(profile_path.read_text(
            encoding='utf-8'))['profiles'] if item['engine'] == 'dolt'
    )


def _connect(profile):
    return mysql.connector.connect(
        host='127.0.0.1', port=profile['port'], user=profile['user'],
        password=profile.get('password', ''), database=profile['database'],
        connection_timeout=10,
    )


def _runtime_inventory(profile_path):
    profile = _profile(profile_path)
    connection = _connect(profile)
    try:
        cursor = connection.cursor()
        cursor.execute('SELECT DOLT_VERSION()')
        if str(cursor.fetchone()[0]) != REFERENCE_VERSION:
            raise RuntimeError('Dolt runtime must be ' + REFERENCE_VERSION)
        cursor.execute(
            'SELECT WORD, RESERVED FROM information_schema.KEYWORDS '
            'ORDER BY WORD')
        lexical = [
            _record(
                f'lexical_rules.{position:04d}',
                word or '<empty-keyword-sentinel>',
                'information_schema.KEYWORDS', reserved=bool(reserved),
                raw_value_empty=not bool(word),
            )
            for position, (word, reserved) in enumerate(
                cursor.fetchall(), 1)
        ]
        cursor.execute('SHOW VARIABLES')
        settings = [
            _record(
                f'session_settings.{position:04d}', name,
                'SHOW VARIABLES', observed_value=str(value),
            )
            for position, (name, value) in enumerate(cursor.fetchall(), 1)
        ]
        cursor.execute('SHOW STATUS')
        status = [(str(name), str(value)) for name, value in cursor.fetchall()]
        cursor.close()
    finally:
        connection.close()
    if not lexical or not settings or not status:
        raise RuntimeError('Dolt runtime inventory is incomplete')
    return lexical, settings, status


def _task_templates(live):
    evidence = live['object_experience_evidence']['dialect_task_evidence']
    expected = set(ADMINISTRATION.dialect_task_ids())
    if set(evidence) != expected:
        raise RuntimeError('Dolt live evidence does not cover exact tasks')
    result = []
    for task_id in sorted(evidence):
        task = evidence[task_id]
        preview = task['command_preview']
        statements = preview.get('statements') or []
        driver_operation = preview.get('driver_operation')
        if task.get('live_execution') != 'passed' or (
                not statements and not driver_operation):
            raise RuntimeError(f'Dolt task evidence is invalid: {task_id}')
        sources = [item['source'] for item in statements]
        if driver_operation:
            sources.append(f'DOLT DRIVER OPERATION {driver_operation}')
        result.append({
            'task_id': task_id,
            'source': '\n;\n'.join(sources),
            'source_format': (
                'provider_native_driver_operation' if driver_operation
                else 'ordered_native_statements'),
            'statements': [item['source'] for item in statements],
            'required_bindings': [
                f'parameter_{position}'
                for position in range(1, 1 + sum(
                    item.get('parameter_count', 0) for item in statements
                ))
            ],
            'binding_style': 'mysql_connector_percent_s',
            'proof_ids': [
                'dolt-1.86.6-task-parser-acceptance',
                'dolt-1.86.6-task-live-execution',
            ],
        })
    return result


def build_dialect(
        dolt_root, source_archive, parser_root, gms_root, profile_path,
        live_path):
    inventories, grammar_path, diagnostic_path, registry_path = (
        _source_inventory(dolt_root, parser_root, gms_root))
    lexical, settings, status = _runtime_inventory(profile_path)
    inventories['lexical_rules'] = lexical
    inventories['session_settings'] = settings
    live = json.loads(live_path.read_text(encoding='utf-8'))
    if (
            live.get('dialect_qualification_ready') is not True or
            live.get('exact_profile') != REFERENCE_VERSION or
            live.get('missing_dialect_task_ids') != [] or
            live.get('credential_values_exported') is not False):
        raise RuntimeError('Dolt exact live evidence is not admissible')
    inventory = {
        'schema': 'cdeadmin.engine-dialect-inventory.v1',
        'inventory_id': 'dolt.dialect-inventory.1.86.6.v1',
        'engine_id': 'dolt',
        'reference_version': REFERENCE_VERSION,
        'source_archive': source_archive.name,
        'source_archive_sha256': _sha256(source_archive),
        'pinned_parser_artifact': grammar_path.name,
        'pinned_parser_sha256': _sha256(grammar_path),
        'pinned_diagnostic_artifact': diagnostic_path.name,
        'pinned_diagnostic_sha256': _sha256(diagnostic_path),
        'pinned_function_registry_artifact': registry_path.name,
        'pinned_function_registry_sha256': _sha256(registry_path),
        'inventories': inventories,
        'completeness': {
            'runtime_version': REFERENCE_VERSION,
            'runtime_catalogs_exhausted': True,
            'pinned_parser_productions_exhausted': True,
            'function_registries_exhausted': True,
            'diagnostic_catalog_exhausted': True,
            'inventory_counts': {
                name: len(records) for name, records in inventories.items()
            },
        },
    }
    return inventory, live, status, _task_templates(live)


def build_dialect_contract(
        inventory, inventory_path, source_archive, live_path, templates):
    source_id = 'dolt-1.86.6-source-and-pinned-parser'
    catalog_id = 'dolt-1.86.6-runtime-catalog'
    driver_id = 'mysql-connector-python'
    source_digest = _sha256(source_archive)
    inventory_digest = _sha256(inventory_path)
    live_digest = _sha256(live_path)
    driver_path = Path(mysql.connector.__file__)
    proofs = [
        _evidence(
            source_id, 'grammar', 'Dolt and pinned parser projects',
            source_archive.name, source_digest,
            'Dolt 1.86.6 source plus go.mod-pinned parser digests',
            'Apache-2.0'),
        _evidence(
            catalog_id, 'catalog', 'Dolt 1.86.6 runtime',
            inventory_path.name, inventory_digest,
            'cdeadmin.engine-dialect-inventory.v1', 'PostgreSQL'),
        _evidence(
            'dolt-1.86.6-task-parser-acceptance', 'parser_acceptance',
            'Dolt 1.86.6 sql-server', live_path.name, live_digest,
            'cdeadmin.relational-provider-live-verification.v1',
            'PostgreSQL'),
        _evidence(
            'dolt-1.86.6-task-live-execution', 'live_execution',
            'Dolt 1.86.6 sql-server', live_path.name, live_digest,
            'cdeadmin.relational-provider-live-verification.v1',
            'PostgreSQL'),
        _evidence(
            driver_id, 'driver', 'Oracle MySQL', driver_path.name,
            _sha256(driver_path), 'Python source', 'GPL-2.0-only'),
    ]
    source_categories = {
        'statements', 'commands', 'data_types', 'functions', 'operators',
        'diagnostics',
    }
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
        'explain': 'EXPLAIN',
        'cancellation': 'KILL QUERY through a separate Dolt session',
    }
    return {
        'schema': 'cdeadmin.engine-dialect.v2',
        'contract_id': 'dolt.dialect.1.86.6.v1',
        'provider_id': 'org.cdeadmin.dolt',
        'profile_id': 'dolt-native',
        'engine_id': 'dolt',
        'interface_id': 'dolt-native',
        'reference_version': REFERENCE_VERSION,
        'language_profiles': ['dolt-sql'],
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
        'live_evidence_ids': ['dolt-1.86.6-task-live-execution'],
        'complete_grammar_inventory': {
            'artifact': inventory_path.name,
            'sha256': inventory_digest,
            **inventory['completeness'],
        },
    }


def build_metrics_inventory(status):
    observations = []
    for name, value in status:
        try:
            float(value)
            value_type = 'number'
        except ValueError:
            value_type = 'text'
        observations.append({
            'native_name': name,
            'observed_value_type': value_type,
            'observed_value': value,
            'description': f'Dolt SHOW STATUS variable {name}.',
        })
    return {
        'schema': 'cdeadmin.engine-metrics-inventory.v1',
        'inventory_id': 'dolt.metrics-inventory.1.86.6.v1',
        'engine_id': 'dolt',
        'reference_version': REFERENCE_VERSION,
        'collection_authority': 'Dolt 1.86.6 exhaustive SHOW STATUS',
        'endpoint_selection': 'Every native SHOW STATUS row.',
        'observations': observations,
        'completeness': {
            'runtime_version': REFERENCE_VERSION,
            'observation_count': len(observations),
            'show_status_catalog_exhausted': True,
            'admitted_metric_count': sum(
                item['observed_value_type'] == 'number'
                for item in observations),
        },
    }


def build_metrics_contract(
        inventory, inventory_path, source_archive, live_path):
    source_id = 'dolt-1.86.6-source-status-implementation'
    runtime_id = 'dolt-1.86.6-runtime-status-catalog'
    live_id = 'dolt-1.86.6-task-live-execution'
    evidence = [
        _evidence(
            source_id, 'documentation', 'Dolt project',
            source_archive.name, _sha256(source_archive),
            'Dolt 1.86.6 source archive', 'Apache-2.0'),
        _evidence(
            runtime_id, 'catalog', 'Dolt 1.86.6 runtime',
            inventory_path.name, _sha256(inventory_path),
            'cdeadmin.engine-metrics-inventory.v1', 'PostgreSQL'),
        _evidence(
            live_id, 'live_execution', 'Dolt 1.86.6 sql-server',
            live_path.name, _sha256(live_path),
            'cdeadmin.relational-provider-live-verification.v1',
            'PostgreSQL'),
    ]
    observations = []
    metrics = []
    for position, item in enumerate(inventory['observations'], 1):
        observation_id = (
            f"dolt.status.{position:04d}.{_slug(item['native_name'])}")
        observations.append({
            'observation_id': observation_id,
            'native_name': item['native_name'],
            'scope': 'server',
            'source': 'SHOW STATUS',
            'value_type': item['observed_value_type'],
            'observation_class': 'operational_status',
            'description': item['description'],
            'privilege': 'server_status_read',
            'version_condition': REFERENCE_VERSION,
            'collection_cost': 'single_show_status_snapshot',
            'poll_interval_seconds': 15,
            'cardinality': 'one_per_server',
            'redaction': 'none',
            'evidence_ids': [source_id, runtime_id, live_id],
        })
        if item['observed_value_type'] != 'number':
            continue
        metrics.append({
            'metric_id': observation_id,
            'observation_id': observation_id,
            'unit': 'native_unit',
            'kind': 'gauge_or_counter_as_declared_by_server',
            'reset_behavior': 'server_restart_or_status_reset',
            'aggregation': 'latest_server_value',
            'evidence_ids': [source_id, runtime_id, live_id],
        })
    observation_ids = [item['observation_id'] for item in observations]
    metric_ids = [item['metric_id'] for item in metrics]
    if not metric_ids:
        raise RuntimeError('Dolt numeric metric inventory is empty')
    return {
        'schema': 'cdeadmin.engine-metrics.v2',
        'contract_id': 'dolt.metrics.1.86.6.v1',
        'provider_id': 'org.cdeadmin.dolt',
        'profile_id': 'dolt-native',
        'engine_id': 'dolt',
        'interface_id': 'dolt-native',
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
    parser.add_argument('--dolt-source-root', type=Path, required=True)
    parser.add_argument('--source-archive', type=Path, required=True)
    parser.add_argument('--parser-root', type=Path, required=True)
    parser.add_argument('--gms-root', type=Path, required=True)
    parser.add_argument('--connection-profiles', type=Path, required=True)
    parser.add_argument('--live-evidence', type=Path, required=True)
    parser.add_argument('--dialect-inventory-output', type=Path, required=True)
    parser.add_argument('--dialect-contract-output', type=Path, required=True)
    parser.add_argument('--metrics-inventory-output', type=Path, required=True)
    parser.add_argument('--metrics-contract-output', type=Path, required=True)
    options = parser.parse_args()
    source_root = options.dolt_source_root.resolve()
    source_archive = options.source_archive.resolve()
    live_path = options.live_evidence.resolve()
    inventory, _live, status, templates = build_dialect(
        source_root, source_archive, options.parser_root.resolve(),
        options.gms_root.resolve(), options.connection_profiles.resolve(),
        live_path)
    _write(options.dialect_inventory_output, inventory)
    _write(options.dialect_contract_output, build_dialect_contract(
        inventory, options.dialect_inventory_output.resolve(),
        source_archive, live_path, templates))
    metrics_inventory = build_metrics_inventory(status)
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
