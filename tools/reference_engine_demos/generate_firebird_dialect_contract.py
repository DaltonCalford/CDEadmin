#!/usr/bin/env python3
##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Build the Firebird 5.0.4 activation contract from exact evidence."""

from __future__ import annotations

import argparse
import csv
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

from pgadmin.cdeadmin.providers.firebird.provider import (  # noqa: E402
    ADMINISTRATION,
)


REFERENCE_VERSION = '5.0.4'


def _sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _evidence(
        evidence_id, evidence_kind, authority, artifact, digest, format_name,
        license_id):
    return {
        'evidence_id': evidence_id,
        'evidence_kind': evidence_kind,
        'authority': authority,
        'artifact': artifact,
        'sha256': digest,
        'format': format_name,
        'license_id': license_id,
    }


def _inventory_records(items, proof_ids):
    return [{
        'item_id': item['item_id'],
        'native_name': item['native_name'],
        'source': item['source'],
        'proof_ids': list(proof_ids),
        'production': item.get('production'),
        'grammar_source': item.get('grammar_source'),
    } for item in items]


def _lexical_records(items):
    return [{
        'item_id': f'lexical_rules.{position:04d}',
        'native_name': item['text'],
        'source': item['source'],
        'proof_ids': ['firebird-5.0.4-lexer'],
        'token_identifier': item['identifier'],
        'non_reserved': item['non_reserved'],
    } for position, item in enumerate(items, 1)]


def _task_templates(live):
    evidence = live['object_experience_evidence'][
        'dialect_task_evidence'
    ]
    expected = set(ADMINISTRATION.dialect_task_ids())
    if set(evidence) != expected:
        raise RuntimeError(
            'Firebird live evidence does not exactly cover dialect tasks'
        )
    templates = []
    for task_id in sorted(evidence):
        preview = evidence[task_id]['command_preview']
        statements = preview['statements']
        if not statements or evidence[task_id]['live_execution'] != 'passed':
            raise RuntimeError(f'Firebird task evidence is invalid: {task_id}')
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
                'firebird-5.0.4-task-parser-acceptance',
                'firebird-5.0.4-task-live-execution',
            ],
        })
    return templates


def generate(inventory_path, live_path):
    inventory = json.loads(inventory_path.read_text(encoding='utf-8'))
    live = json.loads(live_path.read_text(encoding='utf-8'))
    if inventory.get('inventory_id') != (
            'firebird.dialect-inventory.5.0.4.v1'):
        raise RuntimeError('Firebird dialect inventory identity is invalid')
    completeness = inventory.get('completeness', {})
    if (
            completeness.get('lexer_token_count') != len(
                inventory.get('lexer_tokens', [])) or
            completeness.get('grammar_production_count') != len(
                inventory.get('grammar_productions', [])) or
            completeness.get('grammar_alternative_count') != sum(
                len(item['alternatives'])
                for item in inventory.get('grammar_productions', [])
            )):
        raise RuntimeError('Firebird dialect inventory is incomplete')
    if not live.get('dialect_qualification_ready') or (
            live.get('exact_profile') != REFERENCE_VERSION or
            live.get('missing_dialect_task_ids') != [] or
            live.get('credential_values_exported') is not False or
            live.get('source_runtime_modified') is not False):
        raise RuntimeError('Firebird dialect live evidence is not admissible')
    templates = _task_templates(live)
    source_digests = {
        item['artifact']: item['sha256']
        for item in inventory['source_evidence']
    }
    live_digest = _sha256(live_path)
    inventory_digest = _sha256(inventory_path)
    proof_records = [
        _evidence(
            'firebird-5.0.4-grammar', 'grammar', 'Firebird Project',
            'src/dsql/parse.y', source_digests['src/dsql/parse.y'],
            'BTYACC grammar source', 'IPL-1.0',
        ),
        _evidence(
            'firebird-5.0.4-lexer', 'grammar', 'Firebird Project',
            'src/common/ParserTokens.h',
            source_digests['src/common/ParserTokens.h'],
            'Firebird parser-token source', 'IPL-1.0',
        ),
        _evidence(
            'firebird-5.0.4-functions', 'catalog', 'Firebird Project',
            'src/jrd/SysFunction.cpp',
            source_digests['src/jrd/SysFunction.cpp'],
            'Firebird system-function source', 'IPL-1.0',
        ),
        _evidence(
            'firebird-5.0.4-inventory', 'documentation', 'CDEadmin',
            'firebird_dialect_inventory_5_0_4.json', inventory_digest,
            'cdeadmin.engine-dialect-inventory.v1', 'PostgreSQL',
        ),
        _evidence(
            'firebird-5.0.4-task-parser-acceptance',
            'parser_acceptance', 'Firebird 5.0.4 runtime',
            'firebird-live-verification.json', live_digest,
            'cdeadmin.relational-provider-live-verification.v1',
            'PostgreSQL',
        ),
        _evidence(
            'firebird-5.0.4-task-live-execution', 'live_execution',
            'Firebird 5.0.4 runtime', 'firebird-live-verification.json',
            live_digest,
            'cdeadmin.relational-provider-live-verification.v1',
            'PostgreSQL',
        ),
        _evidence(
            'firebird-driver-1.10.11', 'driver',
            'FirebirdSQL python3-driver project',
            'firebird/driver/core.py',
            '3024a04558e70629e7dd7980cde953cc068be751ccbf016193bb0c19b6aa5c1b',
            'Python source', 'MIT',
        ),
    ]
    facets = inventory['dialect_facets']
    inventories = {
        'lexical_rules': _lexical_records(inventory['lexer_tokens']),
        'statements': _inventory_records(
            facets['statements'], ['firebird-5.0.4-grammar']
        ),
        'commands': _inventory_records(
            facets['commands'], ['firebird-5.0.4-grammar']
        ),
        'data_types': _inventory_records(
            facets['data_types'], ['firebird-5.0.4-grammar']
        ),
        'functions': _inventory_records(facets['functions'], [
            'firebird-5.0.4-grammar', 'firebird-5.0.4-functions',
        ]),
        'operators': _inventory_records(
            facets['operators'], ['firebird-5.0.4-grammar']
        ),
        'session_settings': _inventory_records(
            facets['session_settings'], ['firebird-5.0.4-grammar']
        ),
        'diagnostics': _inventory_records(
            facets['diagnostics'], ['firebird-5.0.4-grammar']
        ),
    }
    task_ids = [item['task_id'] for item in templates]
    return {
        'schema': 'cdeadmin.engine-dialect.v2',
        'contract_id': 'firebird.dialect.5.0.4.v2',
        'provider_id': 'org.cdeadmin.firebird',
        'profile_id': 'firebird-native',
        'engine_id': 'firebird',
        'interface_id': 'firebird-native',
        'reference_version': REFERENCE_VERSION,
        'language_profiles': ['firebird-sql'],
        'grammar_evidence': {
            key: value for key, value in proof_records[0].items()
            if key not in {'evidence_id', 'evidence_kind'}
        },
        'proof_records': proof_records,
        'inventories': inventories,
        'complete_grammar_inventory': {
            'artifact': 'firebird_dialect_inventory_5_0_4.json',
            'sha256': inventory_digest,
            **completeness,
        },
        'syntax_decisions': {
            'identifier_quoting': {
                'syntax': 'double_quote_with_doubled_quote_escape',
                'proof_ids': ['firebird-5.0.4-grammar'],
            },
            'string_literals': {
                'syntax': 'single_quote_with_doubled_quote_escape',
                'proof_ids': ['firebird-5.0.4-grammar'],
            },
            'parameter_binding': {
                'syntax': 'positional_question_mark',
                'proof_ids': [
                    'firebird-5.0.4-grammar',
                    'firebird-driver-1.10.11',
                ],
            },
            'transaction_control': {
                'syntax': 'SET TRANSACTION; COMMIT; ROLLBACK; SAVEPOINT',
                'proof_ids': ['firebird-5.0.4-grammar'],
            },
            'pagination': {
                'syntax': 'FIRST/SKIP and ROWS clauses',
                'proof_ids': ['firebird-5.0.4-grammar'],
            },
            'explain': {
                'syntax': 'PLAN clause; no EXPLAIN top-level statement',
                'proof_ids': ['firebird-5.0.4-grammar'],
            },
            'cancellation': {
                'syntax': 'firebird-driver attachment cancellation API',
                'proof_ids': ['firebird-driver-1.10.11'],
            },
        },
        'task_templates': templates,
        'coverage': {
            'scope': 'cdeadmin_generated_tasks',
            'language_acceptance_authority': 'engine_parser',
            'authoritative_inventory_counts': {
                name: len(items) for name, items in inventories.items()
            },
            'authoritative_task_ids': task_ids,
            'authoritative_task_count': len(task_ids),
            'implemented_task_count': len(task_ids),
        },
        'live_evidence_ids': [
            'firebird-5.0.4-task-live-execution',
        ],
    }


def _write_csv(path, document):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', encoding='utf-8', newline='') as output:
        writer = csv.DictWriter(output, fieldnames=(
            'task_id', 'statement_count', 'parameter_count', 'source',
            'parser_acceptance', 'live_execution',
        ))
        writer.writeheader()
        for task in document['task_templates']:
            writer.writerow({
                'task_id': task['task_id'],
                'statement_count': len(task['statements']),
                'parameter_count': len(task['required_bindings']),
                'source': task['source'],
                'parser_acceptance': 'passed',
                'live_execution': 'passed',
            })


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inventory', type=Path, required=True)
    parser.add_argument('--live-evidence', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--task-report', type=Path, required=True)
    options = parser.parse_args(argv)
    document = generate(options.inventory, options.live_evidence)
    options.output.parent.mkdir(parents=True, exist_ok=True)
    options.output.write_text(
        json.dumps(document, indent=2, sort_keys=True) + '\n',
        encoding='utf-8',
    )
    _write_csv(options.task_report, document)
    print(json.dumps({
        'contract_id': document['contract_id'],
        'task_count': len(document['task_templates']),
        'inventory_counts': document['coverage'][
            'authoritative_inventory_counts'
        ],
    }, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
