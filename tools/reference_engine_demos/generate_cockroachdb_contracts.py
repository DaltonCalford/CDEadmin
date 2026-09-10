#!/usr/bin/env python3
##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Generate exact CockroachDB 26.1.3 dialect and metrics contracts."""

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

from pgadmin.cdeadmin.providers.cockroachdb.provider import (  # noqa: E402
    ADMINISTRATION,
)


REFERENCE_VERSION = '26.1.3'
GRAMMAR = Path('pkg/sql/parser/sql.y')
DIAGNOSTICS = Path('pkg/sql/pgwire/pgcode/codes.go')


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
        raise RuntimeError(
            f'CockroachDB grammar production is absent: {name}')
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
    seen_commands = set()
    for match in re.finditer(
            r'(?m)^([a-z_][a-z0-9_]*)\s*:', grammar):
        name = match.group(1)
        if name in seen_commands:
            continue
        seen_commands.add(name)
        commands.append(_record(
            f'commands.{len(commands) + 1:04d}', name,
            f'{GRAMMAR.as_posix()}:'
            f'{grammar.count(chr(10), 0, match.start()) + 1}',
        ))
    statements = _alternatives(
        grammar, 'stmt_without_legacy_transaction', 'statements'
    )
    legacy = _alternatives(
        grammar, 'legacy_transaction_stmt', 'legacy_statements'
    )
    for record in legacy:
        record['item_id'] = f'statements.{len(statements) + 1:04d}'
        statements.append(record)

    operators = []
    for match in re.finditer(
            r'(?m)^%(?:left|right|nonassoc)\s+(.+)$', grammar):
        line = grammar.count('\n', 0, match.start()) + 1
        syntax = match.group(1).split('//', 1)[0].strip()
        for token in syntax.split():
            operators.append(_record(
                f'grammar_operators.{len(operators) + 1:04d}', token,
                f'{GRAMMAR.as_posix()}:{line}',
                registry='grammar_precedence',
            ))

    diagnostic_path = source_root / DIAGNOSTICS
    diagnostic_text = diagnostic_path.read_text(encoding='utf-8')
    diagnostics = []
    for match in re.finditer(
            r'(?m)^\s*([A-Za-z][A-Za-z0-9]+)\s*=\s*'
            r'MakeCode\("([0-9A-Z]{5})"\)', diagnostic_text):
        diagnostics.append(_record(
            f'diagnostics.{len(diagnostics) + 1:04d}', match.group(1),
            f'{DIAGNOSTICS.as_posix()}:'
            f'{diagnostic_text.count(chr(10), 0, match.start()) + 1}',
            sqlstate=match.group(2),
        ))
    if not commands or not statements or not operators or not diagnostics:
        raise RuntimeError('CockroachDB source inventory is incomplete')
    return {
        'statements': statements,
        'commands': commands,
        'grammar_operators': operators,
        'diagnostics': diagnostics,
    }


def _runtime_connection(profile_path):
    profile = next(
        item for item in json.loads(profile_path.read_text(
            encoding='utf-8'))['profiles']
        if item['engine'] == 'cockroachdb'
    )
    connection = psycopg.connect(
        host='127.0.0.1', port=profile['port'], user=profile['user'],
        dbname='defaultdb', sslmode=profile.get('sslmode', 'disable'),
        connect_timeout=10, autocommit=True,
    )
    return profile, connection


def _runtime_inventory(profile_path):
    _profile, connection = _runtime_connection(profile_path)
    try:
        with connection.cursor() as cursor:
            cursor.execute('SELECT version()')
            version = str(cursor.fetchone()[0])
            match = re.search(r'\bv([0-9]+\.[0-9]+\.[0-9]+)\b', version)
            if match is None or match.group(1) != REFERENCE_VERSION:
                raise RuntimeError(
                    'CockroachDB runtime must be ' + REFERENCE_VERSION)

            cursor.execute('SELECT word, catcode, catdesc '
                           'FROM pg_get_keywords() ORDER BY word')
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
                't.typcategory FROM pg_catalog.pg_type AS t '
                'JOIN pg_catalog.pg_namespace AS n ON n.oid=t.typnamespace '
                'ORDER BY t.oid'
            )
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
                'p.proargtypes::STRING, p.prorettype '
                'FROM pg_catalog.pg_proc AS p '
                'JOIN pg_catalog.pg_namespace AS n ON n.oid=p.pronamespace '
                'ORDER BY p.oid'
            )
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
                'JOIN pg_catalog.pg_namespace AS n ON n.oid=o.oprnamespace '
                'ORDER BY o.oid'
            )
            runtime_operators = [
                _record(
                    f'operators.{position:04d}', f'{namespace}.{name}',
                    'pg_catalog.pg_operator', oid=int(oid),
                    left_type_oid=int(left), right_type_oid=int(right),
                    result_type_oid=int(result),
                )
                for position, (
                    oid, namespace, name, left, right, result
                ) in enumerate(cursor.fetchall(), 1)
            ]

            cursor.execute('SHOW ALL')
            session_settings = [
                _record(
                    f'session_settings.{position:04d}', name, 'SHOW ALL',
                    observed_value=str(value),
                )
                for position, (name, value) in enumerate(
                    cursor.fetchall(), 1)
            ]
    finally:
        connection.close()
    inventories = {
        'lexical_rules': lexical,
        'data_types': data_types,
        'functions': functions,
        'operators': runtime_operators,
        'session_settings': session_settings,
    }
    if any(not values for values in inventories.values()):
        raise RuntimeError('CockroachDB runtime inventory is incomplete')
    return inventories


def _load_task_evidence(secure_path, full_path):
    secure = json.loads(secure_path.read_text(encoding='utf-8'))
    full = json.loads(full_path.read_text(encoding='utf-8'))
    if (
            secure.get('exact_profile') != REFERENCE_VERSION or
            secure.get('server_stopped') is not True or
            secure.get('credential_values_exported') is not False or
            full.get('exact_profile') != REFERENCE_VERSION or
            full.get('status') != 'passed' or
            full.get('containers_removed') is not True):
        raise RuntimeError('CockroachDB exact live evidence is not admissible')
    secure_tasks = secure['object_experience_evidence'][
        'dialect_task_evidence']
    full_tasks = full['dialect_task_evidence']
    expected = set(ADMINISTRATION.dialect_task_ids())
    merged = {}
    authorities = {}
    for task_id in expected:
        if task_id in secure_tasks:
            merged[task_id] = secure_tasks[task_id]
            authorities[task_id] = 'secure'
        elif task_id in full_tasks:
            merged[task_id] = full_tasks[task_id]
            authorities[task_id] = 'full'
    if set(merged) != expected:
        raise RuntimeError(
            'CockroachDB live evidence does not exactly cover dialect tasks')
    return secure, full, merged, authorities


def _task_templates(evidence, authorities):
    result = []
    for task_id in sorted(evidence):
        preview = evidence[task_id]['command_preview']
        statements = preview.get('statements') or []
        if not statements or evidence[task_id].get(
                'live_execution') != 'passed':
            raise RuntimeError(
                f'CockroachDB task evidence is invalid: {task_id}')
        prefix = 'cockroachdb-26.1.3-' + authorities[task_id]
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
                prefix + '-task-parser-acceptance',
                prefix + '-task-live-execution',
            ],
        })
    return result


def build_dialect(
        source_root, source_archive, profile_path, secure_path, full_path):
    source = _source_inventory(source_root)
    runtime = _runtime_inventory(profile_path)
    inventories = {
        'lexical_rules': runtime['lexical_rules'],
        'statements': source['statements'],
        'commands': source['commands'],
        'data_types': runtime['data_types'],
        'functions': runtime['functions'],
        'operators': runtime['operators'],
        'session_settings': runtime['session_settings'],
        'diagnostics': source['diagnostics'],
    }
    inventory = {
        'schema': 'cdeadmin.engine-dialect-inventory.v1',
        'inventory_id': 'cockroachdb.dialect-inventory.26.1.3.v1',
        'engine_id': 'cockroachdb',
        'reference_version': REFERENCE_VERSION,
        'source_archive': source_archive.name,
        'source_archive_sha256': _sha256(source_archive),
        'inventories': inventories,
        'grammar_precedence_operators': source['grammar_operators'],
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
    }
    secure, full, evidence, authorities = _load_task_evidence(
        secure_path, full_path)
    return inventory, secure, full, _task_templates(evidence, authorities)


def build_dialect_contract(
        inventory, inventory_path, source_archive, secure_path, full_path,
        templates):
    source_id = 'cockroachdb-26.1.3-source-grammar'
    catalog_id = 'cockroachdb-26.1.3-runtime-catalog'
    driver_id = 'psycopg-3-driver'
    secure_digest = _sha256(secure_path)
    full_digest = _sha256(full_path)
    proof_records = [
        _evidence(
            source_id, 'grammar', 'Cockroach Labs', source_archive.name,
            _sha256(source_archive), 'CockroachDB 26.1.3 source archive',
            'CockroachDB-Software-License',
        ),
        _evidence(
            catalog_id, 'catalog', 'CockroachDB 26.1.3 runtime',
            inventory_path.name, _sha256(inventory_path),
            'cdeadmin.engine-dialect-inventory.v1', 'PostgreSQL',
        ),
        _evidence(
            'cockroachdb-26.1.3-secure-task-parser-acceptance',
            'parser_acceptance', 'CockroachDB 26.1.3 secure runtime',
            secure_path.name, secure_digest,
            'cdeadmin.relational-provider-live-verification.v1',
            'PostgreSQL',
        ),
        _evidence(
            'cockroachdb-26.1.3-secure-task-live-execution',
            'live_execution', 'CockroachDB 26.1.3 secure runtime',
            secure_path.name, secure_digest,
            'cdeadmin.relational-provider-live-verification.v1',
            'PostgreSQL',
        ),
        _evidence(
            'cockroachdb-26.1.3-full-task-parser-acceptance',
            'parser_acceptance', 'CockroachDB 26.1.3 five-node runtime',
            full_path.name, full_digest,
            'cdeadmin.provider-object-live-evidence.v1', 'PostgreSQL',
        ),
        _evidence(
            'cockroachdb-26.1.3-full-task-live-execution',
            'live_execution', 'CockroachDB 26.1.3 five-node runtime',
            full_path.name, full_digest,
            'cdeadmin.provider-object-live-evidence.v1', 'PostgreSQL',
        ),
        _evidence(
            driver_id, 'driver', 'Psycopg project',
            Path(psycopg.__file__).name, _sha256(Path(psycopg.__file__)),
            'Python source', 'LGPL-3.0-only',
        ),
    ]
    contract_inventories = {}
    for name, records in inventory['inventories'].items():
        proof_ids = [
            source_id if name in {'statements', 'commands', 'diagnostics'}
            else catalog_id
        ]
        contract_inventories[name] = [
            {**copy.deepcopy(record), 'proof_ids': proof_ids}
            for record in records
        ]
    task_ids = [item['task_id'] for item in templates]
    counts = {
        name: len(records) for name, records in contract_inventories.items()
    }
    return {
        'schema': 'cdeadmin.engine-dialect.v2',
        'contract_id': 'cockroachdb.dialect.26.1.3.v1',
        'provider_id': 'org.cdeadmin.cockroachdb',
        'profile_id': 'cockroachdb-native',
        'engine_id': 'cockroachdb',
        'interface_id': 'cockroachdb-native',
        'reference_version': REFERENCE_VERSION,
        'language_profiles': ['cockroachdb-sql'],
        'grammar_evidence': {
            key: value for key, value in proof_records[0].items()
            if key not in {'evidence_id', 'evidence_kind'}
        },
        'proof_records': proof_records,
        'inventories': contract_inventories,
        'syntax_decisions': {
            'identifier_quoting': {
                'syntax': 'double_quote_with_doubled_quote_escape',
                'proof_ids': [source_id],
            },
            'string_literals': {
                'syntax': 'single_quote_with_doubled_quote_escape',
                'proof_ids': [source_id],
            },
            'parameter_binding': {
                'syntax': 'psycopg_percent_s', 'proof_ids': [driver_id],
            },
            'transaction_control': {
                'syntax': 'BEGIN; COMMIT; ROLLBACK; SAVEPOINT',
                'proof_ids': [source_id],
            },
            'pagination': {
                'syntax': 'LIMIT with optional OFFSET',
                'proof_ids': [source_id],
            },
            'explain': {
                'syntax': 'EXPLAIN and EXPLAIN ANALYZE',
                'proof_ids': [source_id],
            },
            'cancellation': {
                'syntax': 'CANCEL QUERY through a separate SQL session',
                'proof_ids': [source_id],
            },
        },
        'task_templates': templates,
        'coverage': {
            'scope': 'cdeadmin_generated_tasks',
            'language_acceptance_authority': 'engine_parser',
            'authoritative_inventory_counts': counts,
            'authoritative_task_count': len(task_ids),
            'implemented_task_count': len(task_ids),
            'authoritative_task_ids': task_ids,
        },
        'live_evidence_ids': [
            'cockroachdb-26.1.3-secure-task-live-execution',
            'cockroachdb-26.1.3-full-task-live-execution',
        ],
        'complete_grammar_inventory': {
            'artifact': inventory_path.name,
            'sha256': _sha256(inventory_path),
            **inventory['completeness'],
        },
    }


def _prometheus_inventory(profile_path):
    profile = next(
        item for item in json.loads(profile_path.read_text(
            encoding='utf-8'))['profiles']
        if item['engine'] == 'cockroachdb'
    )
    url = f"http://127.0.0.1:{profile['http_port']}/_status/vars"
    with urlopen(url, timeout=30) as response:  # nosec B310 - local fixture
        text = response.read().decode('utf-8')
    helps = {}
    types = {}
    for line in text.splitlines():
        help_match = re.match(r'# HELP ([A-Za-z_:][A-Za-z0-9_:]*) (.*)', line)
        if help_match:
            helps[help_match.group(1)] = help_match.group(2).strip()
            continue
        type_match = re.match(
            r'# TYPE ([A-Za-z_:][A-Za-z0-9_:]*) ([a-z]+)', line)
        if type_match:
            types[type_match.group(1)] = type_match.group(2)
    names = sorted(set(helps).intersection(types))
    if not names:
        raise RuntimeError('CockroachDB Prometheus metric catalog is empty')
    observations = [{
        'native_name': name,
        'prometheus_type': types[name],
        'description': helps[name] or f'CockroachDB metric {name}.',
    } for name in names]
    return {
        'schema': 'cdeadmin.engine-metrics-inventory.v1',
        'inventory_id': 'cockroachdb.metrics-inventory.26.1.3.v1',
        'engine_id': 'cockroachdb',
        'reference_version': REFERENCE_VERSION,
        'collection_authority': (
            'CockroachDB 26.1.3 native Prometheus /_status/vars endpoint'
        ),
        'endpoint_selection': (
            'Every metric having both native HELP and TYPE declarations in '
            'the exact reference runtime snapshot.'
        ),
        'observations': observations,
        'completeness': {
            'runtime_version': REFERENCE_VERSION,
            'observation_count': len(observations),
            'prometheus_help_type_catalog_exhausted': True,
            'admitted_metric_count': len(observations),
        },
    }


def build_metrics_contract(
        inventory, inventory_path, source_archive, full_path):
    source_id = 'cockroachdb-26.1.3-source-metric-registry'
    runtime_id = 'cockroachdb-26.1.3-runtime-prometheus-catalog'
    live_id = 'cockroachdb-26.1.3-full-live-gate'
    evidence = [
        _evidence(
            source_id, 'documentation', 'Cockroach Labs',
            source_archive.name, _sha256(source_archive),
            'CockroachDB 26.1.3 source archive',
            'CockroachDB-Software-License',
        ),
        _evidence(
            runtime_id, 'catalog', 'CockroachDB 26.1.3 runtime',
            inventory_path.name, _sha256(inventory_path),
            'cdeadmin.engine-metrics-inventory.v1', 'PostgreSQL',
        ),
        _evidence(
            live_id, 'live_execution',
            'CockroachDB 26.1.3 five-node runtime', full_path.name,
            _sha256(full_path), 'cdeadmin.provider-object-live-evidence.v1',
            'PostgreSQL',
        ),
    ]
    observations = []
    metrics = []
    for item in inventory['observations']:
        name = item['native_name']
        observation_id = f'cockroachdb.prometheus.{_slug(name)}'
        native_type = item['prometheus_type']
        observations.append({
            'observation_id': observation_id,
            'native_name': name,
            'scope': 'native_label_set',
            'source': 'GET /_status/vars',
            'value_type': 'number',
            'observation_class': 'operational_metric',
            'description': item['description'],
            'privilege': 'node_status_endpoint_access',
            'version_condition': REFERENCE_VERSION,
            'collection_cost': 'single_prometheus_catalog_snapshot',
            'poll_interval_seconds': 15,
            'cardinality': 'one_per_native_label_set',
            'redaction': 'provider_label_redaction_policy',
            'evidence_ids': [source_id, runtime_id, live_id],
            'prometheus_type': native_type,
        })
        metrics.append({
            'metric_id': observation_id,
            'observation_id': observation_id,
            'unit': 'native_unit',
            'kind': native_type,
            'reset_behavior': (
                'server_restart' if native_type in {'counter', 'histogram'}
                else 'current_observation'
            ),
            'aggregation': (
                'sum_by_native_labels' if native_type == 'counter'
                else 'preserve_native_labels'
            ),
            'evidence_ids': [source_id, runtime_id, live_id],
        })
    observation_ids = [item['observation_id'] for item in observations]
    metric_ids = [item['metric_id'] for item in metrics]
    return {
        'schema': 'cdeadmin.engine-metrics.v2',
        'contract_id': 'cockroachdb.metrics.26.1.3.v1',
        'provider_id': 'org.cdeadmin.cockroachdb',
        'profile_id': 'cockroachdb-native',
        'engine_id': 'cockroachdb',
        'interface_id': 'cockroachdb-native',
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
    parser.add_argument('--secure-live-evidence', type=Path, required=True)
    parser.add_argument('--full-live-evidence', type=Path, required=True)
    parser.add_argument('--dialect-inventory-output', type=Path, required=True)
    parser.add_argument('--dialect-contract-output', type=Path, required=True)
    parser.add_argument('--metrics-inventory-output', type=Path, required=True)
    parser.add_argument('--metrics-contract-output', type=Path, required=True)
    options = parser.parse_args()
    source_root = options.source_root.resolve()
    source_archive = options.source_archive.resolve()
    profile_path = options.connection_profiles.resolve()
    secure_path = options.secure_live_evidence.resolve()
    full_path = options.full_live_evidence.resolve()

    dialect_inventory, _secure, _full, templates = build_dialect(
        source_root, source_archive, profile_path, secure_path, full_path)
    _write(options.dialect_inventory_output, dialect_inventory)
    dialect_contract = build_dialect_contract(
        dialect_inventory, options.dialect_inventory_output.resolve(),
        source_archive, secure_path, full_path, templates)
    _write(options.dialect_contract_output, dialect_contract)

    metrics_inventory = _prometheus_inventory(profile_path)
    _write(options.metrics_inventory_output, metrics_inventory)
    metrics_contract = build_metrics_contract(
        metrics_inventory, options.metrics_inventory_output.resolve(),
        source_archive, full_path)
    _write(options.metrics_contract_output, metrics_contract)
    print(json.dumps({
        'dialect_inventory_counts': dialect_inventory['completeness'][
            'inventory_counts'],
        'dialect_task_count': len(templates),
        'metric_count': len(metrics_contract['metrics']),
        'native_observation_count': len(
            metrics_contract['native_observations']),
    }, indent=2, sort_keys=True))


if __name__ == '__main__':
    main()
