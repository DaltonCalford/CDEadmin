#!/usr/bin/env python3
##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Generate the independent MariaDB 12.2.2 dialect activation contract."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import sys
from pathlib import Path
from types import ModuleType

import mariadb


ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / 'web'
if str(WEB) not in sys.path:
    sys.path.insert(0, str(WEB))
if 'pgadmin' not in sys.modules:
    package = ModuleType('pgadmin')
    package.__path__ = [str(WEB / 'pgadmin')]
    sys.modules['pgadmin'] = package

from pgadmin.cdeadmin.providers.mysql_family.provider import (  # noqa: E402
    MARIADB_ADMINISTRATION,
)


REFERENCE_VERSION = '12.2.2'
GRAMMAR = Path('sql/sql_yacc.yy')
FUNCTIONS = Path('sql/item_create.cc')
DIAGNOSTICS = Path('sql/share/errmsg-utf8.txt')
TYPE_PRODUCTIONS = (
    'field_type_numeric', 'field_type_temporal', 'field_type_string',
    'field_type_lob', 'field_type_misc',
)


def _sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _record(item_id, native_name, source, **details):
    return {
        'item_id': item_id,
        'native_name': native_name,
        'source': source,
        **details,
    }


def _production_bounds(text, name):
    start = re.search(rf'(?m)^{re.escape(name)}\s*:', text)
    if start is None:
        raise RuntimeError(f'MariaDB grammar production is absent: {name}')
    following = re.search(
        r'(?m)^[A-Za-z_][A-Za-z0-9_]*\s*:', text[start.end():]
    )
    end = (
        start.end() + following.start() if following is not None else len(text)
    )
    return start, text[start.start():end]


def _alternatives(text, name, category, start_position=1):
    start, block = _production_bounds(text, name)
    base_line = text.count('\n', 0, start.start()) + 1
    records = []
    for offset, line in enumerate(block.splitlines()):
        stripped = line.strip()
        if offset == 0:
            stripped = stripped.split(':', 1)[1].strip()
        elif not stripped.startswith('|'):
            continue
        else:
            stripped = stripped[1:].strip()
        stripped = stripped.split('{', 1)[0].strip()
        if not stripped or stripped in {'%empty', '/* empty */'}:
            continue
        records.append(_record(
            f'{category}.{start_position + len(records):04d}', stripped,
            f'{GRAMMAR.as_posix()}:{base_line + offset}', production=name,
        ))
    return records


def _source_inventory(source_root):
    grammar_path = source_root / GRAMMAR
    grammar = grammar_path.read_text(encoding='utf-8')
    commands = [
        _record(
            f'commands.{position:04d}', match.group(1),
            f'{GRAMMAR.as_posix()}:'
            f'{grammar.count(chr(10), 0, match.start()) + 1}',
        )
        for position, match in enumerate(re.finditer(
            r'(?m)^([A-Za-z_][A-Za-z0-9_]*)\s*:', grammar
        ), 1)
    ]
    statements = _alternatives(grammar, 'verb_clause', 'statements')
    data_types = []
    for production in TYPE_PRODUCTIONS:
        data_types.extend(_alternatives(
            grammar, production, 'data_types', len(data_types) + 1
        ))
    data_types.append(_record(
        f'data_types.{len(data_types) + 1:04d}',
        'udt_name float_options srid_option',
        f'{GRAMMAR.as_posix()}:6548', production='field_type_all',
    ))
    operators = []
    for match in re.finditer(
            r'(?m)^%(?:left|right|nonassoc)\s+(.+)$', grammar):
        line = grammar.count('\n', 0, match.start()) + 1
        syntax = match.group(1).split('/*', 1)[0].strip()
        for token in syntax.split():
            operators.append(_record(
                f'operators.{len(operators) + 1:04d}', token,
                f'{GRAMMAR.as_posix()}:{line}',
            ))

    function_path = source_root / FUNCTIONS
    function_text = function_path.read_text(encoding='utf-8')
    array = re.search(
        r'func_array\[\]\s*=\s*\{(?P<body>.*?)\n\};', function_text,
        re.DOTALL,
    )
    if array is None:
        raise RuntimeError('MariaDB native function registry is unavailable')
    function_base = function_text.count('\n', 0, array.start('body')) + 1
    functions = []
    for match in re.finditer(
            r'STRING_WITH_LEN\("([A-Z0-9_]+)"\)', array.group('body')):
        line = function_base + array.group('body').count(
            '\n', 0, match.start()
        )
        functions.append(_record(
            f'functions.{len(functions) + 1:04d}', match.group(1),
            f'{FUNCTIONS.as_posix()}:{line}',
        ))

    diagnostic_path = source_root / DIAGNOSTICS
    diagnostic_text = diagnostic_path.read_text(encoding='utf-8')
    diagnostics = []
    for match in re.finditer(r'(?m)^(ER_[A-Z0-9_]+)\s*$', diagnostic_text):
        diagnostics.append(_record(
            f'diagnostics.{len(diagnostics) + 1:04d}', match.group(1),
            f'{DIAGNOSTICS.as_posix()}:'
            f'{diagnostic_text.count(chr(10), 0, match.start()) + 1}',
        ))
    return {
        'statements': statements,
        'commands': commands,
        'data_types': data_types,
        'functions': functions,
        'operators': operators,
        'diagnostics': diagnostics,
    }


def _runtime_inventory(profile_path):
    profile = next(
        item for item in json.loads(profile_path.read_text(
            encoding='utf-8'
        ))['profiles'] if item['engine'] == 'mariadb'
    )
    connection = mariadb.connect(
        host='127.0.0.1', port=profile['port'], user=profile['user'],
        password=profile['password'], database=profile['database'],
        connect_timeout=10,
    )
    try:
        cursor = connection.cursor()
        cursor.execute('SELECT VERSION()')
        version = str(cursor.fetchone()[0]).split('-', 1)[0]
        if version != REFERENCE_VERSION:
            raise RuntimeError(
                f'MariaDB runtime must be {REFERENCE_VERSION}, got {version}'
            )
        cursor.execute(
            'SELECT WORD FROM INFORMATION_SCHEMA.KEYWORDS ORDER BY WORD'
        )
        lexical = [
            _record(
                f'lexical_rules.{position:04d}', word,
                'INFORMATION_SCHEMA.KEYWORDS',
            )
            for position, (word,) in enumerate(cursor.fetchall(), 1)
        ]
        cursor.execute(
            'SELECT VARIABLE_NAME, VARIABLE_SCOPE, VARIABLE_TYPE, '
            'DEFAULT_VALUE, NUMERIC_MIN_VALUE, NUMERIC_MAX_VALUE, '
            'ENUM_VALUE_LIST, READ_ONLY, VARIABLE_COMMENT '
            'FROM INFORMATION_SCHEMA.SYSTEM_VARIABLES '
            'ORDER BY VARIABLE_NAME'
        )
        session_settings = [
            _record(
                f'session_settings.{position:04d}', row[0],
                'INFORMATION_SCHEMA.SYSTEM_VARIABLES',
                variable_scope=row[1], variable_type=row[2],
                default_value=row[3], numeric_minimum=row[4],
                numeric_maximum=row[5], enum_values=row[6],
                read_only=bool(row[7]), comment=row[8],
            )
            for position, row in enumerate(cursor.fetchall(), 1)
        ]
        cursor.close()
    finally:
        connection.close()
    return lexical, session_settings


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
    evidence = live['object_experience_evidence']['dialect_task_evidence']
    expected = set(MARIADB_ADMINISTRATION.dialect_task_ids())
    if set(evidence) != expected:
        raise RuntimeError(
            'MariaDB live evidence does not exactly cover dialect tasks'
        )
    result = []
    for task_id in sorted(evidence):
        preview = evidence[task_id]['command_preview']
        statements = preview['statements']
        if not statements or evidence[task_id]['live_execution'] != 'passed':
            raise RuntimeError(f'MariaDB task evidence is invalid: {task_id}')
        result.append({
            'task_id': task_id,
            'source': '\n;\n'.join(item['source'] for item in statements),
            'source_format': 'ordered_native_statements',
            'statements': [item['source'] for item in statements],
            'required_bindings': [
                f'parameter_{position}'
                for position in range(1, 1 + sum(
                    item['parameter_count'] for item in statements
                ))
            ],
            'binding_style': 'mariadb_connector_qmark',
            'proof_ids': [
                'mariadb-12.2.2-task-parser-acceptance',
                'mariadb-12.2.2-task-live-execution',
            ],
        })
    return result


def generate(source_root, source_archive, profile_path, live_path):
    source = _source_inventory(source_root)
    lexical, session_settings = _runtime_inventory(profile_path)
    inventories = {
        'lexical_rules': lexical,
        'statements': source['statements'],
        'commands': source['commands'],
        'data_types': source['data_types'],
        'functions': source['functions'],
        'operators': source['operators'],
        'session_settings': session_settings,
        'diagnostics': source['diagnostics'],
    }
    inventory = {
        'schema': 'cdeadmin.engine-dialect-inventory.v1',
        'inventory_id': 'mariadb.dialect-inventory.12.2.2.v1',
        'engine_id': 'mariadb',
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
    }
    live = json.loads(live_path.read_text(encoding='utf-8'))
    if (
            live.get('dialect_qualification_ready') is not True or
            live.get('exact_profile') != REFERENCE_VERSION or
            live.get('missing_dialect_task_ids') != [] or
            live.get('server_stopped') is not True or
            live.get('credential_values_exported') is not False):
        raise RuntimeError('MariaDB exact live evidence is not admissible')
    return inventory, live, _task_templates(live)


def build_contract(
        inventory, inventory_path, live_path, templates, driver_path):
    inventory_digest = _sha256(inventory_path)
    live_digest = _sha256(live_path)
    source_digest = inventory['source_archive_sha256']
    prefix = 'mariadb-12.2.2'
    proof_records = [
        _evidence(
            f'{prefix}-source-grammar', 'grammar', 'MariaDB Foundation',
            inventory['source_archive'], source_digest,
            'MariaDB 12.2.2 source archive', 'GPL-2.0-only',
        ),
        _evidence(
            f'{prefix}-runtime-catalog', 'catalog', 'MariaDB 12.2.2',
            inventory_path.name, inventory_digest,
            'cdeadmin.engine-dialect-inventory.v1', 'PostgreSQL',
        ),
        _evidence(
            f'{prefix}-task-parser-acceptance', 'parser_acceptance',
            'MariaDB 12.2.2', live_path.name, live_digest,
            'cdeadmin.relational-provider-live-verification.v1',
            'PostgreSQL',
        ),
        _evidence(
            f'{prefix}-task-live-execution', 'live_execution',
            'MariaDB 12.2.2', live_path.name, live_digest,
            'cdeadmin.relational-provider-live-verification.v1',
            'PostgreSQL',
        ),
        _evidence(
            'mariadb-connector-python', 'driver', 'MariaDB Foundation',
            driver_path.name, _sha256(driver_path), 'Python source',
            'LGPL-2.1-or-later',
        ),
    ]
    contract_inventories = {}
    for name, records in inventory['inventories'].items():
        proof = (
            [f'{prefix}-runtime-catalog']
            if name in {'lexical_rules', 'session_settings'} else
            [f'{prefix}-source-grammar']
        )
        contract_inventories[name] = [
            {**copy.deepcopy(record), 'proof_ids': proof}
            for record in records
        ]
    task_ids = [item['task_id'] for item in templates]
    return {
        'schema': 'cdeadmin.engine-dialect.v2',
        'contract_id': 'mariadb.dialect.12.2.2.v1',
        'provider_id': 'org.cdeadmin.mariadb',
        'profile_id': 'mariadb-native',
        'engine_id': 'mariadb',
        'interface_id': 'mariadb-native',
        'reference_version': REFERENCE_VERSION,
        'language_profiles': ['mariadb-sql'],
        'grammar_evidence': {
            key: value for key, value in proof_records[0].items()
            if key not in {'evidence_id', 'evidence_kind'}
        },
        'proof_records': proof_records,
        'inventories': contract_inventories,
        'complete_grammar_inventory': {
            'artifact': inventory_path.name,
            'sha256': inventory_digest,
            **inventory['completeness'],
        },
        'syntax_decisions': {
            'identifier_quoting': {
                'syntax': 'backtick_with_doubled_backtick_escape',
                'proof_ids': [f'{prefix}-source-grammar'],
            },
            'string_literals': {
                'syntax': 'single_quote_with_mariadb_escape_rules',
                'proof_ids': [f'{prefix}-source-grammar'],
            },
            'parameter_binding': {
                'syntax': 'mariadb_connector_qmark',
                'proof_ids': ['mariadb-connector-python'],
            },
            'transaction_control': {
                'syntax': 'START TRANSACTION; COMMIT; ROLLBACK; SAVEPOINT',
                'proof_ids': [f'{prefix}-source-grammar'],
            },
            'pagination': {
                'syntax': 'LIMIT with optional OFFSET',
                'proof_ids': [f'{prefix}-source-grammar'],
            },
            'explain': {
                'syntax': 'EXPLAIN and ANALYZE',
                'proof_ids': [f'{prefix}-source-grammar'],
            },
            'cancellation': {
                'syntax': 'KILL QUERY through a separate MariaDB connection',
                'proof_ids': [f'{prefix}-source-grammar'],
            },
        },
        'task_templates': templates,
        'coverage': {
            'scope': 'cdeadmin_generated_tasks',
            'language_acceptance_authority': 'engine_parser',
            'authoritative_inventory_counts': {
                name: len(records)
                for name, records in contract_inventories.items()
            },
            'authoritative_task_ids': task_ids,
            'authoritative_task_count': len(task_ids),
            'implemented_task_count': len(task_ids),
        },
        'live_evidence_ids': [f'{prefix}-task-live-execution'],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-root', type=Path, required=True)
    parser.add_argument('--source-archive', type=Path, required=True)
    parser.add_argument('--connection-profiles', type=Path, required=True)
    parser.add_argument('--live-evidence', type=Path, required=True)
    parser.add_argument('--driver', type=Path, required=True)
    parser.add_argument('--inventory-output', type=Path, required=True)
    parser.add_argument('--contract-output', type=Path, required=True)
    options = parser.parse_args()
    inventory, live, templates = generate(
        options.source_root.resolve(), options.source_archive.resolve(),
        options.connection_profiles.resolve(), options.live_evidence.resolve(),
    )
    options.inventory_output.parent.mkdir(parents=True, exist_ok=True)
    options.inventory_output.write_text(
        json.dumps(inventory, indent=2, sort_keys=True) + '\n',
        encoding='utf-8',
    )
    contract = build_contract(
        inventory, options.inventory_output.resolve(),
        options.live_evidence.resolve(), templates, options.driver.resolve(),
    )
    options.contract_output.parent.mkdir(parents=True, exist_ok=True)
    options.contract_output.write_text(
        json.dumps(contract, indent=2, sort_keys=True) + '\n',
        encoding='utf-8',
    )
    print(json.dumps({
        'task_count': len(templates),
        'inventory_counts': inventory['completeness']['inventory_counts'],
    }, indent=2, sort_keys=True))


if __name__ == '__main__':
    main()
