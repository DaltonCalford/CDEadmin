#!/usr/bin/env python3
##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Generate the exact SQLite 3.53.0 dialect inventory."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
from pathlib import Path


REFERENCE_VERSION = '3.53.0'
PARSE_RELATIVE = Path('src/parse.y')
KEYWORD_RELATIVE = Path('tool/mkkeywordhash.c')
HEADER_RELATIVE = Path('src/sqlite.h.in')


def _sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _record(item_id, native_name, source, **details):
    return {
        'item_id': item_id,
        'native_name': native_name,
        'source': source,
        **details,
    }


def _source_inventory(source_root):
    parse_path = source_root / PARSE_RELATIVE
    keyword_path = source_root / KEYWORD_RELATIVE
    header_path = source_root / HEADER_RELATIVE
    parse_text = parse_path.read_text(encoding='utf-8')
    keyword_text = keyword_path.read_text(encoding='utf-8')
    header_text = header_path.read_text(encoding='utf-8')

    lexical = []
    keyword_pattern = re.compile(
        r'\{\s*"(?P<name>[A-Z_]+)",\s*"(?P<token>TK_[A-Z_]+)",'
        r'\s*(?P<mask>[^,]+),\s*(?P<priority>\d+)\s*\}'
    )
    for position, match in enumerate(
            keyword_pattern.finditer(keyword_text), 1):
        lexical.append(_record(
            f'lexical_rules.{position:04d}', match.group('name'),
            KEYWORD_RELATIVE.as_posix(), token=match.group('token'),
            feature_mask=match.group('mask').strip(),
            priority=int(match.group('priority')),
        ))
    if not lexical:
        raise RuntimeError('SQLite keyword inventory is unavailable')

    statements = []
    commands = []
    production_pattern = re.compile(
        r'^(?P<lhs>[A-Za-z_][A-Za-z0-9_]*)'
        r'(?:\([^)]*\))?\s*::=\s*(?P<rhs>.*?)\.',
    )
    production_lines = 0
    parse_lines = parse_text.splitlines()
    line_index = 0
    while line_index < len(parse_lines):
        line_number = line_index + 1
        stripped = parse_lines[line_index].strip()
        line_index += 1
        if stripped.startswith(('//', '/*', '*')) or '::=' not in stripped:
            continue
        production_lines += 1
        production = stripped
        match = production_pattern.match(production)
        while match is None and line_index < len(parse_lines):
            continuation = parse_lines[line_index].strip()
            line_index += 1
            if continuation.startswith(('//', '/*', '*')):
                continue
            production += ' ' + continuation
            match = production_pattern.match(production)
        if match is None:
            raise RuntimeError(
                f'unparsed SQLite grammar production at line {line_number}'
            )
        lhs = match.group('lhs')
        rhs = match.group('rhs').strip() or '<empty>'
        production = f'{lhs} ::= {rhs}'
        record = _record(
            f'commands.{len(commands) + 1:04d}', production,
            f'{PARSE_RELATIVE.as_posix()}:{line_number}', lhs=lhs, rhs=rhs,
        )
        commands.append(record)
        if lhs == 'cmd':
            statements.append(_record(
                f'statements.{len(statements) + 1:04d}', rhs,
                record['source'], grammar_production=production,
            ))
    if len(commands) != production_lines or not statements:
        raise RuntimeError('SQLite grammar inventory is incomplete')

    operators = []
    for line_number, line in enumerate(parse_text.splitlines(), 1):
        match = re.match(
            r'^%(left|right|nonassoc)\s+(.+?)\.\s*$', line.strip()
        )
        if match is None:
            continue
        associativity, tokens = match.groups()
        for token in tokens.split():
            operators.append(_record(
                f'operators.{len(operators) + 1:04d}', token,
                f'{PARSE_RELATIVE.as_posix()}:{line_number}',
                associativity=associativity,
            ))

    diagnostic_roots = {
        'OK', 'ERROR', 'INTERNAL', 'PERM', 'ABORT', 'BUSY', 'LOCKED',
        'NOMEM', 'READONLY', 'INTERRUPT', 'IOERR', 'CORRUPT', 'NOTFOUND',
        'FULL', 'CANTOPEN', 'PROTOCOL', 'EMPTY', 'SCHEMA', 'TOOBIG',
        'CONSTRAINT', 'MISMATCH', 'MISUSE', 'NOLFS', 'AUTH', 'FORMAT',
        'RANGE', 'NOTADB', 'NOTICE', 'WARNING', 'ROW', 'DONE',
    }
    diagnostics = []
    for line_number, line in enumerate(header_text.splitlines(), 1):
        match = re.match(
            r'^#define\s+SQLITE_([A-Z][A-Z0-9_]*)\s+(.+?)\s*(?:/\*|$)',
            line,
        )
        if match is None:
            continue
        name, expression = match.groups()
        if name.split('_', 1)[0] not in diagnostic_roots:
            continue
        diagnostics.append(_record(
            f'diagnostics.{len(diagnostics) + 1:04d}', f'SQLITE_{name}',
            f'{HEADER_RELATIVE.as_posix()}:{line_number}',
            numeric_expression=expression.strip(),
        ))
    if not diagnostics:
        raise RuntimeError('SQLite diagnostic inventory is unavailable')
    return lexical, statements, commands, operators, diagnostics


def _runtime_inventory():
    if sqlite3.sqlite_version != REFERENCE_VERSION:
        raise RuntimeError(
            f'SQLite runtime must be {REFERENCE_VERSION}, got '
            f'{sqlite3.sqlite_version}'
        )
    connection = sqlite3.connect(':memory:')
    try:
        functions = connection.execute(
            'PRAGMA function_list'
        ).fetchall()
        pragmas = connection.execute('PRAGMA pragma_list').fetchall()
        modules = sorted(
            row[0] for row in connection.execute('PRAGMA module_list')
        )
        compile_options = sorted(
            row[0] for row in connection.execute('PRAGMA compile_options')
        )
        storage_values = connection.execute(
            "SELECT typeof(NULL), typeof(1), typeof(1.5), typeof('text'), "
            "typeof(X'00')"
        ).fetchone()
    finally:
        connection.close()
    if not {'fts5', 'rtree'}.issubset(modules):
        raise RuntimeError('SQLite FTS5 and RTREE modules are required')

    data_types = [
        _record(
            f'data_types.{position:04d}', name.upper(), 'typeof()',
            storage_class=name, declaration_model='dynamic_type',
        )
        for position, name in enumerate(storage_values, 1)
    ]
    data_types.append(_record(
        f'data_types.{len(data_types) + 1:04d}', 'declared-type-name',
        PARSE_RELATIVE.as_posix(), free_form=True,
        note='Non-STRICT tables admit provider-defined declared type names.',
    ))
    for name in ('INT', 'INTEGER', 'REAL', 'TEXT', 'BLOB', 'ANY'):
        data_types.append(_record(
            f'data_types.{len(data_types) + 1:04d}', name,
            'SQLite 3.53.0 STRICT table runtime', strict_table_type=True,
        ))

    function_records = [
        _record(
            f'functions.{position:04d}',
            f'{name}/{arguments}', 'PRAGMA function_list',
            function_name=name, builtin=bool(builtin), function_type=kind,
            encoding=encoding, argument_count=arguments, flags=flags,
        )
        for position, (
            name, builtin, kind, encoding, arguments, flags,
        ) in enumerate(functions, 1)
    ]
    settings = [
        _record(
            f'session_settings.{position:04d}', row[0],
            'PRAGMA pragma_list', scope='connection_or_database',
        )
        for position, row in enumerate(pragmas, 1)
    ]
    return data_types, function_records, settings, modules, compile_options


def generate(source_root, source_archive):
    lexical, statements, commands, operators, diagnostics = (
        _source_inventory(source_root)
    )
    data_types, functions, settings, modules, compile_options = (
        _runtime_inventory()
    )
    inventories = {
        'lexical_rules': lexical,
        'statements': statements,
        'commands': commands,
        'data_types': data_types,
        'functions': functions,
        'operators': operators,
        'session_settings': settings,
        'diagnostics': diagnostics,
    }
    return {
        'schema': 'cdeadmin.engine-dialect-inventory.v1',
        'inventory_id': 'sqlite.dialect-inventory.3.53.0.v1',
        'engine_id': 'sqlite',
        'reference_version': REFERENCE_VERSION,
        'collection_authority': (
            'SQLite 3.53.0 Lemon grammar, generated-keyword source, '
            'public result-code header, and exact runtime catalogs'
        ),
        'source_evidence': {
            'archive': source_archive.name,
            'archive_sha256': _sha256(source_archive),
            'grammar': PARSE_RELATIVE.as_posix(),
            'keyword_catalog': KEYWORD_RELATIVE.as_posix(),
            'diagnostic_catalog': HEADER_RELATIVE.as_posix(),
        },
        'inventories': inventories,
        'completeness': {
            'runtime_version': sqlite3.sqlite_version,
            'inventory_counts': {
                name: len(records) for name, records in inventories.items()
            },
            'runtime_modules': modules,
            'runtime_compile_options': compile_options,
            'runtime_catalogs_exhausted': True,
            'grammar_files_exhausted': True,
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-root', type=Path, required=True)
    parser.add_argument('--source-archive', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    options = parser.parse_args()
    document = generate(
        options.source_root.resolve(), options.source_archive.resolve()
    )
    options.output.parent.mkdir(parents=True, exist_ok=True)
    options.output.write_text(
        json.dumps(document, indent=2, sort_keys=True) + '\n',
        encoding='utf-8',
    )
    print(json.dumps(document['completeness'], indent=2, sort_keys=True))


if __name__ == '__main__':
    main()
