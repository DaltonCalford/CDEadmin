#!/usr/bin/env python3
##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Generate exact YugabyteDB 2025.2.2.2 YSQL contracts."""

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

from pgadmin.cdeadmin.providers.yugabytedb.provider import (  # noqa: E402
    ADMINISTRATION,
)


REFERENCE_VERSION = '2025.2.2.2'
GRAMMAR = Path('src/postgres/src/backend/parser/gram.y')
DIAGNOSTICS = Path('src/postgres/src/backend/utils/errcodes.txt')


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
        raise RuntimeError(f'YSQL grammar production is absent: {name}')
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
    statements = _alternatives(grammar, 'stmt', 'statements')
    grammar_operators = []
    for match in re.finditer(
            r'(?m)^%(?:left|right|nonassoc)\s+(.+)$', grammar):
        line = grammar.count('\n', 0, match.start()) + 1
        for token in match.group(1).split('/*', 1)[0].split():
            grammar_operators.append(_record(
                f'grammar_operators.{len(grammar_operators) + 1:04d}',
                token, f'{GRAMMAR.as_posix()}:{line}',
                registry='grammar_precedence',
            ))
    diagnostic_path = source_root / DIAGNOSTICS
    diagnostic_text = diagnostic_path.read_text(encoding='utf-8')
    diagnostics = []
    for match in re.finditer(
            r'(?m)^([0-9A-Z]{5})\s+[EWS]\s+([A-Z0-9_]+)',
            diagnostic_text):
        diagnostics.append(_record(
            f'diagnostics.{len(diagnostics) + 1:04d}', match.group(2),
            f'{DIAGNOSTICS.as_posix()}:'
            f'{diagnostic_text.count(chr(10), 0, match.start()) + 1}',
            sqlstate=match.group(1),
        ))
    if (
            not commands or not statements or not grammar_operators or
            not diagnostics):
        raise RuntimeError('YSQL source inventory is incomplete')
    return commands, statements, grammar_operators, diagnostics


def _profile(profile_path):
    return next(
        item for item in json.loads(profile_path.read_text(
            encoding='utf-8'))['profiles']
        if item['engine'] == 'yugabytedb' and item.get(
            'interface', 'YSQL') == 'YSQL'
    )


def _runtime_inventory(profile_path):
    profile = _profile(profile_path)
    connection = psycopg.connect(
        host='127.0.0.1', port=profile['port'], user=profile['user'],
        dbname=profile['database'], connect_timeout=10, autocommit=True,
    )
    try:
        with connection.cursor() as cursor:
            cursor.execute('SELECT version()')
            version = str(cursor.fetchone()[0])
            match = re.search(r'YB-([0-9]+\.[0-9]+\.[0-9]+\.[0-9]+)',
                              version)
            if match is None or match.group(1) != REFERENCE_VERSION:
                raise RuntimeError(
                    'YugabyteDB runtime must be ' + REFERENCE_VERSION)
            cursor.execute(
                'SELECT word, catcode, catdesc FROM pg_get_keywords() '
                'ORDER BY word')
            lexical = [
                _record(
                    f'lexical_rules.{position:04d}', word,
                    'pg_get_keywords()', category_code=category,
                    category_description=description,
                )
                for position, (word, category, description) in enumerate(
                    cursor.fetchall(), 1)
            ]
            cursor.execute(
                'SELECT t.oid, n.nspname, t.typname, t.typtype, '
                't.typcategory FROM pg_catalog.pg_type AS t JOIN '
                'pg_catalog.pg_namespace AS n ON n.oid=t.typnamespace '
                'ORDER BY t.oid')
            data_types = [
                _record(
                    f'data_types.{position:04d}', f'{namespace}.{name}',
                    'pg_catalog.pg_type', oid=int(oid), kind=kind,
                    category=category,
                )
                for position, (oid, namespace, name, kind, category)
                in enumerate(cursor.fetchall(), 1)
            ]
            cursor.execute(
                'SELECT p.oid, n.nspname, p.proname, p.prokind, '
                'p.proargtypes::TEXT, p.prorettype FROM pg_catalog.pg_proc '
                'AS p JOIN pg_catalog.pg_namespace AS n '
                'ON n.oid=p.pronamespace ORDER BY p.oid')
            functions = [
                _record(
                    f'functions.{position:04d}', f'{namespace}.{name}',
                    'pg_catalog.pg_proc', oid=int(oid), kind=kind,
                    argument_type_oids=arguments,
                    return_type_oid=int(return_type),
                )
                for position, (
                    oid, namespace, name, kind, arguments, return_type
                ) in enumerate(cursor.fetchall(), 1)
            ]
            cursor.execute(
                'SELECT o.oid, n.nspname, o.oprname, o.oprleft, '
                'o.oprright, o.oprresult FROM pg_catalog.pg_operator AS o '
                'JOIN pg_catalog.pg_namespace AS n '
                'ON n.oid=o.oprnamespace ORDER BY o.oid')
            operators = [
                _record(
                    f'operators.{position:04d}', f'{namespace}.{name}',
                    'pg_catalog.pg_operator', oid=int(oid),
                    left_type_oid=int(left), right_type_oid=int(right),
                    result_type_oid=int(result),
                )
                for position, (oid, namespace, name, left, right, result)
                in enumerate(cursor.fetchall(), 1)
            ]
            cursor.execute('SHOW ALL')
            settings = [
                _record(
                    f'session_settings.{position:04d}', name, 'SHOW ALL',
                    observed_value=str(value), description=str(description),
                )
                for position, (name, value, description) in enumerate(
                    cursor.fetchall(), 1)
            ]
    finally:
        connection.close()
    inventories = {
        'lexical_rules': lexical,
        'data_types': data_types,
        'functions': functions,
        'operators': operators,
        'session_settings': settings,
    }
    if any(not records for records in inventories.values()):
        raise RuntimeError('YSQL runtime inventory is incomplete')
    return inventories


def _task_templates(live):
    evidence = live['object_experience_evidence']['dialect_task_evidence']
    expected = set(ADMINISTRATION.dialect_task_ids())
    if set(evidence) != expected:
        raise RuntimeError('YSQL live evidence does not cover exact tasks')
    result = []
    for task_id in sorted(evidence):
        task = evidence[task_id]
        statements = task['command_preview'].get('statements') or []
        if task.get('live_execution') != 'passed' or not statements:
            raise RuntimeError(f'YSQL task evidence is invalid: {task_id}')
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
            'binding_style': 'psycopg_percent_s',
            'proof_ids': [
                'yugabytedb-2025.2.2.2-ysql-task-parser-acceptance',
                'yugabytedb-2025.2.2.2-ysql-task-live-execution',
            ],
        })
    return result


def build_dialect(source_root, source_archive, profile_path, live_path):
    commands, statements, grammar_operators, diagnostics = (
        _source_inventory(source_root))
    inventories = _runtime_inventory(profile_path)
    inventories.update({
        'statements': statements,
        'commands': commands,
        'diagnostics': diagnostics,
    })
    live = json.loads(live_path.read_text(encoding='utf-8'))
    if (
            live.get('dialect_qualification_ready') is not True or
            live.get('exact_profile') != REFERENCE_VERSION or
            live.get('missing_dialect_task_ids') != [] or
            live.get('credential_values_exported') is not False):
        raise RuntimeError('YSQL exact live evidence is not admissible')
    return {
        'schema': 'cdeadmin.engine-dialect-inventory.v1',
        'inventory_id': 'yugabytedb.ysql-dialect-inventory.2025.2.2.2.v1',
        'engine_id': 'yugabytedb',
        'reference_version': REFERENCE_VERSION,
        'source_archive': source_archive.name,
        'source_archive_sha256': _sha256(source_archive),
        'inventories': inventories,
        'grammar_precedence_operators': grammar_operators,
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
    source_id = 'yugabytedb-2025.2.2.2-ysql-source-grammar'
    catalog_id = 'yugabytedb-2025.2.2.2-ysql-runtime-catalog'
    driver_id = 'psycopg-3-driver'
    source_digest = _sha256(source_archive)
    inventory_digest = _sha256(inventory_path)
    live_digest = _sha256(live_path)
    driver_path = Path(psycopg.__file__)
    proofs = [
        _evidence(
            source_id, 'grammar', 'YugabyteDB project', source_archive.name,
            source_digest, 'YugabyteDB 2025.2.2.2 source archive',
            'Apache-2.0'),
        _evidence(
            catalog_id, 'catalog', 'YugabyteDB 2025.2.2.2 YSQL runtime',
            inventory_path.name, inventory_digest,
            'cdeadmin.engine-dialect-inventory.v1', 'PostgreSQL'),
        _evidence(
            'yugabytedb-2025.2.2.2-ysql-task-parser-acceptance',
            'parser_acceptance', 'YugabyteDB 2025.2.2.2 YSQL runtime',
            live_path.name, live_digest,
            'cdeadmin.relational-provider-live-verification.v1',
            'PostgreSQL'),
        _evidence(
            'yugabytedb-2025.2.2.2-ysql-task-live-execution',
            'live_execution', 'YugabyteDB 2025.2.2.2 YSQL runtime',
            live_path.name, live_digest,
            'cdeadmin.relational-provider-live-verification.v1',
            'PostgreSQL'),
        _evidence(
            driver_id, 'driver', 'Psycopg project', driver_path.name,
            _sha256(driver_path), 'Python source', 'LGPL-3.0-only'),
    ]
    source_categories = {'statements', 'commands', 'diagnostics'}
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
        'identifier_quoting': 'double_quote_with_doubled_quote_escape',
        'string_literals': 'single_quote_with_doubled_quote_escape',
        'parameter_binding': 'psycopg_percent_s',
        'transaction_control': 'BEGIN; COMMIT; ROLLBACK; SAVEPOINT',
        'pagination': 'LIMIT with optional OFFSET',
        'explain': 'EXPLAIN and EXPLAIN ANALYZE',
        'cancellation': 'pg_cancel_backend through a separate YSQL session',
    }
    return {
        'schema': 'cdeadmin.engine-dialect.v2',
        'contract_id': 'yugabytedb.ysql-dialect.2025.2.2.2.v1',
        'provider_id': 'org.cdeadmin.yugabytedb',
        'profile_id': 'yugabytedb-native',
        'engine_id': 'yugabytedb',
        'interface_id': 'yugabytedb-ysql',
        'reference_version': REFERENCE_VERSION,
        'language_profiles': ['ysql'],
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
        'live_evidence_ids': [
            'yugabytedb-2025.2.2.2-ysql-task-live-execution'],
        'complete_grammar_inventory': {
            'artifact': inventory_path.name,
            'sha256': inventory_digest,
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
        raise RuntimeError('YugabyteDB metric inventory is empty')
    return {
        'schema': 'cdeadmin.engine-metrics-inventory.v1',
        'inventory_id': 'yugabytedb.metrics-inventory.2025.2.2.2.v1',
        'engine_id': 'yugabytedb',
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


def build_metrics_contract(
        inventory, inventory_path, source_archive, live_path):
    source_id = 'yugabytedb-2025.2.2.2-source-metric-registry'
    runtime_id = 'yugabytedb-2025.2.2.2-runtime-prometheus-catalog'
    live_id = 'yugabytedb-2025.2.2.2-ysql-task-live-execution'
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
            'YugabyteDB 2025.2.2.2 YSQL runtime', live_path.name,
            _sha256(live_path),
            'cdeadmin.relational-provider-live-verification.v1',
            'PostgreSQL'),
    ]
    observations = []
    metrics = []
    for item in inventory['observations']:
        name = item['native_name']
        component = item['component']
        observation_id = f'yugabytedb.{component}.{_slug(name)}'
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
        'contract_id': 'yugabytedb.metrics.2025.2.2.2.v1',
        'provider_id': 'org.cdeadmin.yugabytedb',
        'profile_id': 'yugabytedb-native',
        'engine_id': 'yugabytedb',
        'interface_id': 'yugabytedb-native',
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
    parser.add_argument('--master-http-port', type=int, default=57000)
    parser.add_argument('--tserver-http-port', type=int, default=59000)
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
    metrics_inventory = build_metrics_inventory(
        options.master_http_port, options.tserver_http_port)
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
