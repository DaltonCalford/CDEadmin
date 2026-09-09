#!/usr/bin/env python3
##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Build the SQLite 3.53.0 activation contract from exact evidence."""

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

from pgadmin.cdeadmin.providers.sqlite.provider import (  # noqa: E402
    ADMINISTRATION,
)


REFERENCE_VERSION = '3.53.0'


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
            'SQLite live evidence does not exactly cover dialect tasks'
        )
    templates = []
    for task_id in sorted(evidence):
        preview = evidence[task_id]['command_preview']
        statements = preview['statements']
        if not statements or evidence[task_id]['live_execution'] != 'passed':
            raise RuntimeError(f'SQLite task evidence is invalid: {task_id}')
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
                'sqlite-3.53.0-task-parser-acceptance',
                'sqlite-3.53.0-task-live-execution',
            ],
        })
    return templates


def _contract_inventories(inventory):
    runtime_categories = {
        'data_types', 'functions', 'session_settings',
    }
    result = {}
    for name, records in inventory['inventories'].items():
        proof_ids = (
            ['sqlite-3.53.0-runtime-catalog']
            if name in runtime_categories else
            ['sqlite-3.53.0-source-grammar']
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
            'sqlite.dialect-inventory.3.53.0.v1'):
        raise RuntimeError('SQLite dialect inventory identity is invalid')
    completeness = inventory.get('completeness', {})
    inventories = _contract_inventories(inventory)
    if (
            completeness.get('runtime_version') != REFERENCE_VERSION or
            completeness.get('runtime_catalogs_exhausted') is not True or
            completeness.get('grammar_files_exhausted') is not True or
            not {'fts5', 'rtree'}.issubset(
                completeness.get('runtime_modules', [])
            ) or
            completeness.get('inventory_counts') != {
                name: len(records) for name, records in inventories.items()
            }):
        raise RuntimeError('SQLite dialect inventory is incomplete')
    if (
            live.get('dialect_qualification_ready') is not True or
            live.get('exact_profile') != REFERENCE_VERSION or
            live.get('missing_dialect_task_ids') != [] or
            live.get('credential_values_exported') is not False):
        raise RuntimeError('SQLite dialect live evidence is not admissible')
    templates = _task_templates(live)
    inventory_digest = _sha256(inventory_path)
    live_digest = _sha256(live_path)
    source_digest = inventory['source_evidence']['archive_sha256']
    proof_records = [
        _evidence(
            'sqlite-3.53.0-source-grammar', 'grammar',
            'SQLite Consortium', inventory['source_evidence']['archive'],
            source_digest, 'SQLite 3.53.0 source archive', 'blessing',
        ),
        _evidence(
            'sqlite-3.53.0-runtime-catalog', 'catalog',
            'SQLite 3.53.0 runtime', inventory_path.name, inventory_digest,
            'cdeadmin.engine-dialect-inventory.v1', 'PostgreSQL',
        ),
        _evidence(
            'sqlite-3.53.0-task-parser-acceptance', 'parser_acceptance',
            'SQLite 3.53.0 runtime', live_path.name, live_digest,
            'cdeadmin.relational-provider-live-verification.v1',
            'PostgreSQL',
        ),
        _evidence(
            'sqlite-3.53.0-task-live-execution', 'live_execution',
            'SQLite 3.53.0 runtime', live_path.name, live_digest,
            'cdeadmin.relational-provider-live-verification.v1',
            'PostgreSQL',
        ),
        _evidence(
            'cpython-sqlite3-dbapi', 'driver', 'Python Software Foundation',
            driver_path.name, _sha256(driver_path),
            'CPython extension module dynamically linked to SQLite 3.53.0',
            'PSF-2.0',
        ),
    ]
    task_ids = [item['task_id'] for item in templates]
    return {
        'schema': 'cdeadmin.engine-dialect.v2',
        'contract_id': 'sqlite.dialect.3.53.0.v1',
        'provider_id': 'org.cdeadmin.sqlite',
        'profile_id': 'sqlite-native',
        'engine_id': 'sqlite',
        'interface_id': 'sqlite-native',
        'reference_version': REFERENCE_VERSION,
        'language_profiles': ['sqlite-sql'],
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
                'proof_ids': ['sqlite-3.53.0-source-grammar'],
            },
            'string_literals': {
                'syntax': 'single_quote_with_doubled_quote_escape',
                'proof_ids': ['sqlite-3.53.0-source-grammar'],
            },
            'parameter_binding': {
                'syntax': 'positional_question_mark',
                'proof_ids': ['cpython-sqlite3-dbapi'],
            },
            'transaction_control': {
                'syntax': (
                    'BEGIN [DEFERRED|IMMEDIATE|EXCLUSIVE]; COMMIT; '
                    'ROLLBACK; SAVEPOINT; RELEASE'
                ),
                'proof_ids': ['sqlite-3.53.0-source-grammar'],
            },
            'pagination': {
                'syntax': 'LIMIT and OFFSET clauses',
                'proof_ids': ['sqlite-3.53.0-source-grammar'],
            },
            'explain': {
                'syntax': 'EXPLAIN and EXPLAIN QUERY PLAN',
                'proof_ids': ['sqlite-3.53.0-source-grammar'],
            },
            'cancellation': {
                'syntax': 'sqlite3_interrupt through DB-API interrupt()',
                'proof_ids': ['cpython-sqlite3-dbapi'],
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
        'live_evidence_ids': ['sqlite-3.53.0-task-live-execution'],
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
    print(json.dumps(document['coverage'], indent=2, sort_keys=True))


if __name__ == '__main__':
    main()
