#!/usr/bin/env python3
##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Build the DuckDB 1.5.2 activation contract from exact evidence."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sys
from pathlib import Path
from types import ModuleType


ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / 'web'
if str(WEB) not in sys.path:
    sys.path.insert(0, str(WEB))
if 'pgadmin' not in sys.modules:
    package = ModuleType('pgadmin')
    package.__path__ = [str(WEB / 'pgadmin')]
    sys.modules['pgadmin'] = package

from pgadmin.cdeadmin.providers.duckdb.provider import (  # noqa: E402
    ADMINISTRATION,
)


REFERENCE_VERSION = '1.5.2'


def _sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


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


def _task_templates(live):
    evidence = live['object_experience_evidence'][
        'dialect_task_evidence'
    ]
    expected = set(ADMINISTRATION.dialect_task_ids())
    if set(evidence) != expected:
        raise RuntimeError(
            'DuckDB live evidence does not exactly cover dialect tasks'
        )
    templates = []
    for task_id in sorted(evidence):
        preview = evidence[task_id]['command_preview']
        statements = preview['statements']
        if not statements or evidence[task_id]['live_execution'] != 'passed':
            raise RuntimeError(f'DuckDB task evidence is invalid: {task_id}')
        parameter_count = sum(
            item['parameter_count'] for item in statements
        )
        templates.append({
            'task_id': task_id,
            'source': '\n;\n'.join(item['source'] for item in statements),
            'source_format': 'ordered_native_statements',
            'statements': [item['source'] for item in statements],
            'required_bindings': [
                f'parameter_{position}'
                for position in range(1, parameter_count + 1)
            ],
            'binding_style': 'positional_question_mark',
            'proof_ids': [
                'duckdb-1.5.2-task-parser-acceptance',
                'duckdb-1.5.2-task-live-execution',
            ],
        })
    return templates


def _contract_inventories(inventory):
    result = {}
    runtime_categories = {
        'lexical_rules', 'data_types', 'functions', 'operators',
        'session_settings',
    }
    for name, records in inventory['inventories'].items():
        proof_ids = (
            ['duckdb-1.5.2-runtime-catalog']
            if name in runtime_categories else
            ['duckdb-1.5.2-source-grammar']
        )
        result[name] = [
            {**copy.deepcopy(record), 'proof_ids': proof_ids}
            for record in records
        ]
    return result


def generate(inventory_path, live_path, driver_path):
    inventory = json.loads(inventory_path.read_text(encoding='utf-8'))
    live = json.loads(live_path.read_text(encoding='utf-8'))
    if inventory.get('inventory_id') != (
            'duckdb.dialect-inventory.1.5.2.v1'):
        raise RuntimeError('DuckDB dialect inventory identity is invalid')
    completeness = inventory.get('completeness', {})
    inventories = _contract_inventories(inventory)
    if (
            completeness.get('runtime_version') != REFERENCE_VERSION or
            completeness.get('runtime_catalogs_exhausted') is not True or
            completeness.get('grammar_files_exhausted') is not True or
            completeness.get('inventory_counts') != {
                name: len(records) for name, records in inventories.items()
            }):
        raise RuntimeError('DuckDB dialect inventory is incomplete')
    if (
            live.get('dialect_qualification_ready') is not True or
            live.get('exact_profile') != REFERENCE_VERSION or
            live.get('missing_dialect_task_ids') != [] or
            live.get('credential_values_exported') is not False):
        raise RuntimeError('DuckDB dialect live evidence is not admissible')
    templates = _task_templates(live)
    inventory_digest = _sha256(inventory_path)
    live_digest = _sha256(live_path)
    source_digest = inventory['source_evidence']['archive_sha256']
    proof_records = [
        _evidence(
            'duckdb-1.5.2-source-grammar', 'grammar', 'DuckDB Foundation',
            inventory['source_evidence']['archive'], source_digest,
            'DuckDB 1.5.2 source archive', 'MIT',
        ),
        _evidence(
            'duckdb-1.5.2-runtime-catalog', 'catalog',
            'DuckDB 1.5.2 runtime', inventory_path.name, inventory_digest,
            'cdeadmin.engine-dialect-inventory.v1', 'PostgreSQL',
        ),
        _evidence(
            'duckdb-1.5.2-task-parser-acceptance', 'parser_acceptance',
            'DuckDB 1.5.2 runtime', live_path.name, live_digest,
            'cdeadmin.relational-provider-live-verification.v1',
            'PostgreSQL',
        ),
        _evidence(
            'duckdb-1.5.2-task-live-execution', 'live_execution',
            'DuckDB 1.5.2 runtime', live_path.name, live_digest,
            'cdeadmin.relational-provider-live-verification.v1',
            'PostgreSQL',
        ),
        _evidence(
            'duckdb-python-1.5.2', 'driver', 'DuckDB Foundation',
            driver_path.name, _sha256(driver_path),
            'CPython extension module', 'MIT',
        ),
    ]
    task_ids = [item['task_id'] for item in templates]
    return {
        'schema': 'cdeadmin.engine-dialect.v2',
        'contract_id': 'duckdb.dialect.1.5.2.v1',
        'provider_id': 'org.cdeadmin.duckdb',
        'profile_id': 'duckdb-native',
        'engine_id': 'duckdb',
        'interface_id': 'duckdb-native',
        'reference_version': REFERENCE_VERSION,
        'language_profiles': ['duckdb-sql'],
        'grammar_evidence': {
            key: value for key, value in proof_records[0].items()
            if key not in {'evidence_id', 'evidence_kind'}
        },
        'proof_records': proof_records,
        'inventories': inventories,
        'complete_grammar_inventory': {
            'artifact': inventory_path.name,
            'sha256': inventory_digest,
            **completeness,
        },
        'syntax_decisions': {
            'identifier_quoting': {
                'syntax': 'double_quote_with_doubled_quote_escape',
                'proof_ids': ['duckdb-1.5.2-source-grammar'],
            },
            'string_literals': {
                'syntax': 'single_quote_with_doubled_quote_escape',
                'proof_ids': ['duckdb-1.5.2-source-grammar'],
            },
            'parameter_binding': {
                'syntax': 'positional_question_mark',
                'proof_ids': ['duckdb-python-1.5.2'],
            },
            'transaction_control': {
                'syntax': 'BEGIN TRANSACTION; COMMIT; ROLLBACK',
                'proof_ids': ['duckdb-1.5.2-source-grammar'],
            },
            'pagination': {
                'syntax': 'LIMIT and OFFSET clauses',
                'proof_ids': ['duckdb-1.5.2-source-grammar'],
            },
            'explain': {
                'syntax': 'EXPLAIN and EXPLAIN ANALYZE statements',
                'proof_ids': ['duckdb-1.5.2-source-grammar'],
            },
            'cancellation': {
                'syntax': 'DuckDBPyConnection interrupt API',
                'proof_ids': ['duckdb-python-1.5.2'],
            },
        },
        'task_templates': templates,
        'coverage': {
            'scope': 'cdeadmin_generated_tasks',
            'language_acceptance_authority': 'engine_parser',
            'authoritative_inventory_counts': {
                name: len(records) for name, records in inventories.items()
            },
            'authoritative_task_ids': task_ids,
            'authoritative_task_count': len(task_ids),
            'implemented_task_count': len(task_ids),
        },
        'live_evidence_ids': ['duckdb-1.5.2-task-live-execution'],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inventory', type=Path, required=True)
    parser.add_argument('--live-evidence', type=Path, required=True)
    parser.add_argument('--driver', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    options = parser.parse_args()
    document = generate(
        options.inventory.resolve(), options.live_evidence.resolve(),
        options.driver.resolve(),
    )
    options.output.parent.mkdir(parents=True, exist_ok=True)
    options.output.write_text(
        json.dumps(document, indent=2, sort_keys=True) + '\n',
        encoding='utf-8',
    )
    print(json.dumps({
        'task_count': len(document['task_templates']),
        'inventory_counts': document['coverage'][
            'authoritative_inventory_counts'
        ],
    }, indent=2, sort_keys=True))


if __name__ == '__main__':
    main()
