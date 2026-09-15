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
import copy
import hashlib
import itertools
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
    ADMINISTRATION, PROFILE,
)
from pgadmin.cdeadmin.engine_contracts import (  # noqa: E402
    validate_dialect_contract,
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


def supplement_roles(document, evidence, digest, artifact):
    """Add exact live role membership tasks without replacing earlier proof."""
    validate_dialect_contract(document, PROFILE)
    task_ids = {'visual_admin.role.grant', 'visual_admin.role.revoke'}
    records = evidence.get('role_dialect_task_evidence', {})
    if (evidence.get('status') != 'passed' or
            not str(evidence.get('engine_version', '')).startswith('5.0.4') or
            set(records) != task_ids or
            evidence.get('role_memberships_replayed') != 3 or
            any(evidence.get(key) is not True for key in (
                'temporary_user_removed', 'temporary_role_removed',
                'temporary_owned_role_removed', 'temporary_table_removed'))):
        raise ValueError('Role evidence is incomplete or fixtures remain')
    value = copy.deepcopy(document)
    prefix = 'firebird-5.0.4-role-membership'
    proof_ids = [prefix + '-parser-acceptance', prefix + '-live-execution']
    value['proof_records'] = [record for record in value['proof_records']
                              if record['evidence_id'] not in proof_ids]
    for proof_id, kind in zip(proof_ids, (
            'parser_acceptance', 'live_execution')):
        value['proof_records'].append(_evidence(
            proof_id, kind, 'Firebird 5.0.4 runtime', artifact, digest,
            'cdeadmin.firebird-role-membership-live.v1', 'PostgreSQL'))
    value['task_templates'] = [task for task in value['task_templates']
                               if task['task_id'] not in task_ids]
    for task_id in sorted(task_ids):
        record = records[task_id]
        statements = record.get('command_preview', {}).get('statements', [])
        if (record.get('live_execution') != 'passed' or not statements or
                any(not item.get('source') or item.get('parameter_count') != 0
                    for item in statements)):
            raise ValueError('Role task lacks successful native statements')
        sources = [item['source'] for item in statements]
        value['task_templates'].append({
            'task_id': task_id, 'source': '\n;\n'.join(sources),
            'source_format': 'ordered_native_statements',
            'statements': sources, 'required_bindings': [],
            'binding_style': 'positional_question_mark',
            'proof_ids': proof_ids,
        })
    value['task_templates'].sort(key=lambda task: task['task_id'])
    ids = [task['task_id'] for task in value['task_templates']]
    value['coverage'].update({
        'authoritative_task_ids': ids,
        'authoritative_task_count': len(ids),
        'implemented_task_count': len(ids),
    })
    value['live_evidence_ids'] = sorted(set(
        value['live_evidence_ids'] + [proof_ids[1]]))
    validate_dialect_contract(
        value, PROFILE, ADMINISTRATION.dialect_task_ids())
    return value


def supplement_admin_mapping(document, evidence, digest, artifact):
    """Admit the local mapping task only after exact clean native evidence."""
    validate_dialect_contract(document, PROFILE)
    required = {
        'set-rollback-restores-absence', 'set-commit-and-replace-idempotent',
        'system-role-inspector-no-fabricated-create',
        'drop-rollback-restores-mapping', 'drop-commit-removes-mapping',
        'absent-drop-native-error-no-state-change',
        'uncommitted-close-discards-mapping',
        'unprivileged-SET-denied', 'unprivileged-DROP-denied',
    }
    statements = {action: f'ALTER ROLE "RDB$ADMIN" {action} AUTO ADMIN MAPPING'
                  for action in ('SET', 'DROP')}
    if (evidence.get('status') != 'passed' or
            evidence.get('engine_version') != '5.0.4' or
            evidence.get('fixture_removed') is not True or
            evidence.get('temporary_user_removed') is not True or
            evidence.get('failures') != [] or
            not required.issubset(evidence.get('checks', [])) or
            evidence.get('statements') != statements):
        raise ValueError('Admin mapping evidence is incomplete or unclean')
    value = copy.deepcopy(document)
    task_id = 'visual_admin.role.configure_admin_mapping'
    proof_id = 'firebird-5.0.4-local-admin-mapping-live'
    parser_id = 'firebird-5.0.4-local-admin-mapping-parser'
    value['proof_records'] = [item for item in value['proof_records']
                              if item['evidence_id'] not in
                              {proof_id, parser_id}]
    value['proof_records'].append(_evidence(
        proof_id, 'live_execution', 'Firebird 5.0.4 runtime', artifact, digest,
        'cdeadmin.firebird-admin-mapping-live.v1', 'PostgreSQL'))
    value['proof_records'].append(_evidence(
        parser_id, 'parser_acceptance', 'Firebird 5.0.4 runtime', artifact,
        digest, 'cdeadmin.firebird-admin-mapping-live.v1', 'PostgreSQL'))
    value['task_templates'] = [item for item in value['task_templates']
                               if item['task_id'] != task_id]
    value['task_templates'].append({
        'task_id': task_id, 'source': '\n;\n'.join(statements.values()),
        'source_format': 'ordered_native_statements',
        'statements': list(statements.values()), 'required_bindings': [],
        'binding_style': 'positional_question_mark',
        'proof_ids': ['firebird-5.0.4-grammar', parser_id, proof_id],
    })
    value['task_templates'].sort(key=lambda item: item['task_id'])
    ids = [item['task_id'] for item in value['task_templates']]
    value['coverage'].update(authoritative_task_ids=ids,
                             authoritative_task_count=len(ids),
                             implemented_task_count=len(ids))
    value['live_evidence_ids'] = sorted(set(
        value['live_evidence_ids'] + [proof_id]))
    validate_dialect_contract(value, PROFILE,
                              ADMINISTRATION.dialect_task_ids())
    return value


def supplement_mappings(document, evidence, digest, artifact):
    from pgadmin.cdeadmin.providers.firebird import mappings
    validate_dialect_contract(document, PROFILE)
    required = {
        f'{kind}:{mode}:any={any_name}:to={to_type}'
        for kind, mode, any_name, to_type in itertools.product(
            mappings.KINDS, mappings.MODES, (False, True), ('USER', 'ROLE'))
    } | {'scope-separation'} | {
        f'{kind}:{case}' for kind in mappings.KINDS for case in (
            'permission-denial', 'native-user-mapping-authentication')}
    tasks = {f'visual_admin.{kind}.{operation}' for kind in mappings.KINDS
             for operation in mappings.OPERATIONS - {'inspect'}}
    if (evidence.get('status') != 'passed' or
            evidence.get('engine_version') != '5.0.4' or
            evidence.get('container_removed') is not True or
            evidence.get('failures') != [] or
            not required.issubset(evidence.get('checks', [])) or
            set(evidence.get('task_evidence', {})) != tasks):
        raise ValueError('Authentication mapping evidence is incomplete')
    value = copy.deepcopy(document)
    proof_id = 'firebird-5.0.4-authentication-mappings-live'
    parser_id = 'firebird-5.0.4-authentication-mappings-parser'
    value['proof_records'] = [item for item in value['proof_records']
                              if item['evidence_id'] not in
                              {proof_id, parser_id}]
    for identity, kind in ((proof_id, 'live_execution'),
                           (parser_id, 'parser_acceptance')):
        value['proof_records'].append(_evidence(
            identity, kind, 'Firebird 5.0.4 runtime', artifact, digest,
            'cdeadmin.firebird-mapping-matrix.v1', 'PostgreSQL'))
    value['task_templates'] = [item for item in value['task_templates']
                               if item['task_id'] not in tasks]
    for task_id, record in evidence['task_evidence'].items():
        statements = record.get('statements')
        if (record.get('live_execution') != 'passed' or
                not isinstance(statements, list) or not statements or
                not all(isinstance(sql, str) and sql for sql in statements)):
            raise ValueError('Authentication mapping task proof is missing')
        value['task_templates'].append({
            'task_id': task_id, 'source': '\n;\n'.join(statements),
            'source_format': 'ordered_native_statements',
            'statements': statements, 'required_bindings': [],
            'binding_style': 'positional_question_mark',
            'proof_ids': ['firebird-5.0.4-grammar', parser_id, proof_id],
        })
    value['task_templates'].sort(key=lambda item: item['task_id'])
    ids = [item['task_id'] for item in value['task_templates']]
    value['coverage'].update(authoritative_task_ids=ids,
                             authoritative_task_count=len(ids),
                             implemented_task_count=len(ids))
    value['live_evidence_ids'] = sorted(set(
        value['live_evidence_ids'] + [proof_id]))
    validate_dialect_contract(value, PROFILE,
                              ADMINISTRATION.dialect_task_ids())
    return value


def supplement_columns(document, evidence, digest, artifact):
    from pgadmin.cdeadmin.providers.firebird import columns
    validate_dialect_contract(document, PROFILE)
    required = {'position', 'set-not-null', 'drop-not-null', 'drop-default',
                'COMPUTED', 'TYPE COMPUTED', 'identity-always',
                'identity-by-default', 'drop-identity', 'comment-set',
                'comment-clear', 'permission-denied-alter',
                'permission-denied-comment', 'existing-null-rejected',
                'type-narrowing-rejected', 'computed-line-comment'} | {
        'default-' + kind for kind in columns.DEFAULTS
    } | {f'default-{kind}-{precision}' for kind in columns.TIMED_DEFAULTS
         for precision in range(4)} | {
        'type-' + kind for kind in columns.TYPES} | {
        'identity-state-' + str(number) for number in range(5)}
    required |= {'create-type-' + kind for kind in columns.TYPES} | {
        'create-' + name for name in (
            'identity-always', 'identity-default', 'computed-explicit',
            'computed-inferred', 'array-integer', 'array-character',
            'default-not-null', 'check', 'unique', 'primary-key', 'collation')
    } | {'create-reference-' + action + '-' + str(explicit)
         for action in columns.REFERENTIAL_ACTIONS
         for explicit in (False, True)}
    tasks = {'visual_admin.column.alter', 'visual_admin.column.comment',
             'visual_admin.column.create'}
    table_tasks = {'visual_admin.table.create', 'visual_admin.table.alter'}
    table_proof = evidence.get('table_task_evidence', {})
    required |= {'table-structured-definition-recreation',
                 'table-structured-add', 'table-structured-rename',
                 'table-structured-drop', 'table-add-failure-atomicity'}
    cases = {item.get('case') for item in evidence.get('checks', [])}
    if (evidence.get('passed') is not True or
            evidence.get('engine_version') != '5.0.4' or
            evidence.get('fixture_removed') is not True or
            evidence.get('temporary_user_removed') is not True or
            evidence.get('failures') != [] or not required.issubset(cases) or
            set(evidence.get('task_evidence', {})) != tasks or
            not isinstance(table_proof, dict) or
            set(table_proof) != table_tasks):
        raise ValueError('Column definition/alteration evidence is incomplete')
    tasks |= table_tasks
    task_proof = {**evidence['task_evidence'], **table_proof}
    value = copy.deepcopy(document)
    proof_id = 'firebird-5.0.4-column-alterations-live'
    parser_id = 'firebird-5.0.4-column-alterations-parser'
    value['proof_records'] = [item for item in value['proof_records']
                              if item['evidence_id'] not in
                              {proof_id, parser_id}]
    for identity, kind in ((proof_id, 'live_execution'),
                           (parser_id, 'parser_acceptance')):
        value['proof_records'].append(_evidence(
            identity, kind, 'Firebird 5.0.4 runtime', artifact, digest,
            'cdeadmin.firebird-columns.v1', 'PostgreSQL'))
    value['task_templates'] = [item for item in value['task_templates']
                               if item['task_id'] not in tasks]
    for task_id, record in task_proof.items():
        statements = record.get('statements')
        if (record.get('live_execution') != 'passed' or
                not isinstance(statements, list) or not statements or
                not all(isinstance(sql, str) and sql for sql in statements)):
            raise ValueError('Column alteration task proof is missing')
        value['task_templates'].append({
            'task_id': task_id, 'source': '\n;\n'.join(statements),
            'source_format': 'ordered_native_statements',
            'statements': statements, 'required_bindings': [],
            'binding_style': 'positional_question_mark',
            'proof_ids': ['firebird-5.0.4-grammar', parser_id, proof_id],
        })
    value['task_templates'].sort(key=lambda item: item['task_id'])
    ids = [item['task_id'] for item in value['task_templates']]
    value['coverage'].update(authoritative_task_ids=ids,
                             authoritative_task_count=len(ids),
                             implemented_task_count=len(ids))
    value['live_evidence_ids'] = sorted(set(
        value['live_evidence_ids'] + [proof_id]))
    validate_dialect_contract(value, PROFILE,
                              ADMINISTRATION.dialect_task_ids())
    return value


def supplement_character_metadata(document, evidence, digest, artifact):
    validate_dialect_contract(document, PROFILE)
    required = {f'flags-create-drop-rollback-recreation-{mask}'
                for mask in range(8)} | {
        'collation-comment-set-clear-rollback',
        'character-set-comment-set-clear-rollback',
        'charset-default-commit-rollback', 'inherited-flags',
        'external-implementation', 'numeric-sort', 'inherited-numeric-sort',
        'duplicate-last-wins', 'empty-removes-inherited',
        'invalid-numeric-sort', 'mismatched-character-set',
        'missing-external-implementation', 'missing-same-name-implementation',
        'dependent-column-drop-denied', 'unprivileged-create-denied',
        'unprivileged-default-change-denied',
        'unprivileged-charset-comment-denied',
        'unprivileged-collation-comment-denied', 'unprivileged-drop-denied',
        'granted-create-owner-comment-drop', 'revoked-create-denied',
        'same-name-installed-implementation',
        'default-applies-only-new-columns',
        'invalid-default-preserves-current-ASCII',
        'invalid-default-preserves-current-OWNED_ABSENT'}
    tasks = {f'visual_admin.{kind}.{action}' for kind, actions in (
        ('collation', ('create', 'drop', 'comment')),
        ('character-set', ('alter', 'comment'))) for action in actions}
    if (evidence.get('complete') is not True or
            evidence.get('engine_version') != '5.0.4' or
            evidence.get('schema') != 'cdeadmin.firebird-character-metadata.v1'
            or evidence.get('owned_container_removed') is not True or
            evidence.get('failures') != [] or
            set(evidence.get('task_evidence', {})) != tasks or
            not required.issubset({item.get('case') for item in
                                   evidence.get('checks', [])})):
        raise ValueError('Character metadata native evidence is incomplete')
    by_case = {item['case']: item for item in evidence['checks']}
    for mask in (4, 5):
        rejection = by_case[f'flags-create-drop-rollback-recreation-{mask}']
        if (rejection.get('expected_native_rejection') != 336068830 or
                rejection.get('created') is not False):
            raise ValueError(
                'Character metadata native evidence is incomplete')
    value = copy.deepcopy(document)
    proof_id = 'firebird-5.0.4-character-metadata-live'
    parser_id = 'firebird-5.0.4-character-metadata-parser'
    value['proof_records'] = [item for item in value['proof_records']
                              if item['evidence_id'] not in
                              {proof_id, parser_id}]
    for identity, kind in ((proof_id, 'live_execution'),
                           (parser_id, 'parser_acceptance')):
        value['proof_records'].append(_evidence(
            identity, kind, 'Firebird 5.0.4 runtime', artifact, digest,
            evidence['schema'], 'PostgreSQL'))
    value['task_templates'] = [item for item in value['task_templates']
                               if item['task_id'] not in tasks]
    for task_id, record in evidence['task_evidence'].items():
        statements = record.get('statements')
        if (record.get('live_execution') != 'passed' or
                not isinstance(statements, list) or not statements or
                not all(isinstance(sql, str) and sql for sql in statements)):
            raise ValueError(
                'Character metadata task has no native statements')
        value['task_templates'].append({
            'task_id': task_id, 'source': '\n;\n'.join(statements),
            'source_format': 'ordered_native_statements',
            'statements': statements, 'required_bindings': [],
            'binding_style': 'positional_question_mark',
            'proof_ids': ['firebird-5.0.4-grammar', parser_id, proof_id],
        })
    value['task_templates'].sort(key=lambda item: item['task_id'])
    ids = [item['task_id'] for item in value['task_templates']]
    value['coverage'].update(authoritative_task_ids=ids,
                             authoritative_task_count=len(ids),
                             implemented_task_count=len(ids))
    value['live_evidence_ids'] = sorted(set(
        value['live_evidence_ids'] + [proof_id]))
    validate_dialect_contract(value, PROFILE,
                              ADMINISTRATION.dialect_task_ids())
    return value


def supplement_external_functions(document, evidence, digest, artifact):
    validate_dialect_contract(document, PROFILE)
    required = {'create-alter-comment-drop-rollback-recreation',
                'dependent-view-drop-denied',
                'restricted-lifecycle-execute-grant-revoke',
                'argument-boundary-0', 'argument-boundary-15',
                'blob-return-argument-boundary',
                'missing-module_name', 'missing-entrypoint'}
    required.update('declaration-recreation-' + str(index)
                    for index in range(25))
    required.update('native-mechanism-' + name for name in (
        'REFERENCE', 'NULL', 'DESCRIPTOR', 'RETURN_DESCRIPTOR', 'FREE_IT',
        'DESCRIPTOR_FREE_IT', 'PARAMETER', 'PARAMETER_REFERENCE_NATIVE_LIMIT',
        'CSTRING', 'SCALAR_ARRAY'))
    tasks = {'visual_admin.external-function.' + action
             for action in ('create', 'alter', 'comment', 'drop')}
    if (evidence.get('complete') is not True or
            evidence.get('engine_version') != '5.0.4' or
            evidence.get('schema') != 'cdeadmin.firebird-external-functions.v1'
            or evidence.get('owned_container_removed') is not True or
            evidence.get('failures') != [] or
            not tasks.issubset(evidence.get('task_evidence', {})) or
            not required.issubset({item.get('case') for item in
                                   evidence.get('checks', [])})):
        raise ValueError('External function native evidence is incomplete')
    value = copy.deepcopy(document)
    proof_id = 'firebird-5.0.4-external-functions-live'
    parser_id = 'firebird-5.0.4-external-functions-parser'
    value['proof_records'] = [item for item in value['proof_records']
                              if item['evidence_id'] not in
                              {proof_id, parser_id}]
    for identity, kind in ((proof_id, 'live_execution'),
                           (parser_id, 'parser_acceptance')):
        value['proof_records'].append(_evidence(
            identity, kind, 'Firebird 5.0.4 runtime', artifact, digest,
            evidence['schema'], 'PostgreSQL'))
    value['task_templates'] = [item for item in value['task_templates']
                               if item['task_id'] not in tasks]
    for task_id in sorted(tasks):
        record = evidence['task_evidence'][task_id]
        statements = record.get('statements')
        if (record.get('live_execution') != 'passed' or
                not isinstance(statements, list) or not statements or
                not all(isinstance(sql, str) and sql for sql in statements)):
            raise ValueError('External function task has no native statements')
        value['task_templates'].append({
            'task_id': task_id, 'source': '\n;\n'.join(statements),
            'source_format': 'ordered_native_statements',
            'statements': statements, 'required_bindings': [],
            'binding_style': 'positional_question_mark',
            'proof_ids': ['firebird-5.0.4-grammar', parser_id, proof_id],
        })
    value['task_templates'].sort(key=lambda item: item['task_id'])
    ids = [item['task_id'] for item in value['task_templates']]
    value['coverage'].update(authoritative_task_ids=ids,
                             authoritative_task_count=len(ids),
                             implemented_task_count=len(ids))
    value['live_evidence_ids'] = sorted(set(
        value['live_evidence_ids'] + [proof_id]))
    validate_dialect_contract(value, PROFILE,
                              ADMINISTRATION.dialect_task_ids())
    return value


def supplement_blob_filters(document, evidence, digest, artifact):
    validate_dialect_contract(document, PROFILE)
    required = {
        'declaration-comment-drop-commit-rollback', 'registered-mnemonic',
        'registered-custom-mnemonic',
        'catalog-recreation-round-trip', 'unknown-mnemonic',
        'below-subtype-range', 'above-subtype-range', 'unsupported-alter',
        'unsupported-execute-grant', 'duplicate-name',
        'duplicate-subtype-pair',
        'native-filter-read', 'native-filter-write', 'missing-entrypoint',
        'missing-module_name', 'loaded-filter-survives-declaration-drop',
        'restricted-grant-owner-revoke',
    } | {'numeric-subtype-' + str(number)
         for number in (-32768, -1, 0, 1, 32767)}
    tasks = {'visual_admin.blob-filter.' + action
             for action in ('create', 'comment', 'drop')}
    if (evidence.get('complete') is not True or
            evidence.get('engine_version') != '5.0.4' or
            evidence.get('schema') != 'cdeadmin.firebird-blob-filters.v1' or
            evidence.get('owned_container_removed') is not True or
            evidence.get('failures') != [] or
            not tasks.issubset(evidence.get('task_evidence', {})) or
            not required.issubset({item.get('case') for item in
                                   evidence.get('checks', [])})):
        raise ValueError('BLOB filter native evidence is incomplete')
    observations = {item['case']: item for item in evidence['checks']}
    for direction in ('read', 'write'):
        item = observations['native-filter-' + direction]
        if (item.get('bytes_verified') != 148 or
                item.get('small_buffer_segment_continuation') is not True):
            raise ValueError('BLOB filter native invocation was not verified')
    permissions = observations['restricted-grant-owner-revoke']
    if (permissions.get('denied_without_grant') !=
            ['create', 'comment', 'drop']
            or permissions.get('granted_owner_comment_drop') is not True or
            permissions.get('revoke_fresh_attachment_denied') is not True):
        raise ValueError('BLOB filter native permissions were not verified')
    cached = observations['loaded-filter-survives-declaration-drop']
    if (cached.get('catalog_declaration_removed') is not True or
            cached.get('database_filter_cache_retains_loaded_code')
            is not True):
        raise ValueError('BLOB filter native cache behavior was not verified')
    custom = observations['registered-custom-mnemonic']
    if (custom.get('registered_custom_subtype') != -79 or
            custom.get('quoted_unicode_mnemonic') is not True or
            custom.get('native_invocation_verified') is not True):
        raise ValueError('BLOB filter custom mnemonic was not verified')
    value = copy.deepcopy(document)
    proof_id = 'firebird-5.0.4-blob-filters-live'
    parser_id = 'firebird-5.0.4-blob-filters-parser'
    value['proof_records'] = [item for item in value['proof_records']
                              if item['evidence_id'] not in
                              {proof_id, parser_id}]
    for identity, kind in ((proof_id, 'live_execution'),
                           (parser_id, 'parser_acceptance')):
        value['proof_records'].append(_evidence(
            identity, kind, 'Firebird 5.0.4 runtime', artifact, digest,
            evidence['schema'], 'PostgreSQL'))
    value['task_templates'] = [item for item in value['task_templates']
                               if item['task_id'] not in tasks]
    for task_id in sorted(tasks):
        record = evidence['task_evidence'][task_id]
        statements = record.get('statements')
        if (record.get('live_execution') != 'passed' or
                not isinstance(statements, list) or not statements or
                not all(isinstance(sql, str) and sql for sql in statements)):
            raise ValueError('BLOB filter task has no native statements')
        value['task_templates'].append({
            'task_id': task_id, 'source': '\n;\n'.join(statements),
            'source_format': 'ordered_native_statements',
            'statements': statements, 'required_bindings': [],
            'binding_style': 'positional_question_mark',
            'proof_ids': ['firebird-5.0.4-grammar', parser_id, proof_id],
        })
    value['task_templates'].sort(key=lambda item: item['task_id'])
    ids = [item['task_id'] for item in value['task_templates']]
    value['coverage'].update(authoritative_task_ids=ids,
                             authoritative_task_count=len(ids),
                             implemented_task_count=len(ids))
    value['live_evidence_ids'] = sorted(set(
        value['live_evidence_ids'] + [proof_id]))
    validate_dialect_contract(value, PROFILE,
                              ADMINISTRATION.dialect_task_ids())
    return value


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
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--inventory', type=Path)
    source.add_argument('--existing-contract', type=Path)
    parser.add_argument('--live-evidence', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--task-report', type=Path, required=True)
    parser.add_argument('--supplement', choices=(
        'roles', 'admin-mapping', 'mappings', 'columns', 'character-metadata',
        'external-functions', 'blob-filters'),
                        default='roles')
    options = parser.parse_args(argv)
    if options.existing_contract:
        supplement = {'admin-mapping': supplement_admin_mapping,
                      'roles': supplement_roles,
                      'mappings': supplement_mappings,
                      'character-metadata': supplement_character_metadata,
                      'external-functions': supplement_external_functions,
                      'blob-filters': supplement_blob_filters,
                      'columns': supplement_columns}[options.supplement]
        document = supplement(
            json.loads(options.existing_contract.read_text(encoding='utf-8')),
            json.loads(options.live_evidence.read_text(encoding='utf-8')),
            _sha256(options.live_evidence), options.live_evidence.name,
        )
    else:
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
