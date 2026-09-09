#!/usr/bin/env python3
##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Generate the exact DuckDB 1.5.2 dialect inventory from local evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

import duckdb


REFERENCE_VERSION = '1.5.2'
GRAMMAR_RELATIVE = Path('third_party/libpg_query/grammar')
EXCEPTION_RELATIVE = Path('src/include/duckdb/common/exception.hpp')


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


def _grammar_inventory(source_root):
    grammar_root = source_root / GRAMMAR_RELATIVE
    files = sorted(grammar_root.rglob('*.y'))
    if not files:
        raise RuntimeError('DuckDB grammar sources are unavailable')
    statements = []
    commands = []
    for file_position, path in enumerate(files, 1):
        relative = path.relative_to(source_root).as_posix()
        text = path.read_text(encoding='utf-8')
        if path.parent.name == 'statements':
            statements.append(_record(
                f'statements.{len(statements) + 1:04d}', path.stem,
                relative, sha256=_sha256(path),
            ))
        for match in re.finditer(
                r'(?m)^([A-Za-z_][A-Za-z0-9_]*)\s*:', text):
            line = text.count('\n', 0, match.start()) + 1
            commands.append(_record(
                f'commands.{len(commands) + 1:04d}', match.group(1),
                f'{relative}:{line}', grammar_file=file_position,
            ))
    return files, statements, commands


def _diagnostics(source_root):
    path = source_root / EXCEPTION_RELATIVE
    text = path.read_text(encoding='utf-8')
    match = re.search(
        r'enum class ExceptionType[^\{]*\{(?P<body>.*?)\};', text,
        re.DOTALL,
    )
    if match is None:
        raise RuntimeError('DuckDB ExceptionType inventory is unavailable')
    base_line = text.count('\n', 0, match.start('body')) + 1
    records = []
    for offset, line in enumerate(match.group('body').splitlines()):
        value = line.split('//', 1)[0].strip().rstrip(',')
        if not value:
            continue
        name = value.split('=', 1)[0].strip()
        if not re.fullmatch(r'[A-Z][A-Z0-9_]*', name):
            continue
        records.append(_record(
            f'diagnostics.{len(records) + 1:04d}', name,
            f'{EXCEPTION_RELATIVE.as_posix()}:{base_line + offset}',
        ))
    return records


def _runtime_inventory():
    if duckdb.__version__ != REFERENCE_VERSION:
        raise RuntimeError(
            f'DuckDB runtime must be {REFERENCE_VERSION}, got '
            f'{duckdb.__version__}'
        )
    connection = duckdb.connect(':memory:')
    try:
        keywords = connection.execute(
            'SELECT keyword_name, keyword_category '
            'FROM duckdb_keywords() ORDER BY keyword_name'
        ).fetchall()
        types = connection.execute(
            'SELECT database_name, schema_name, type_name, logical_type, '
            'type_category, internal FROM duckdb_types() '
            'ORDER BY database_name, schema_name, type_name, type_oid'
        ).fetchall()
        functions = connection.execute(
            'SELECT database_name, schema_name, function_name, '
            'function_type, return_type, parameter_types, varargs, '
            'has_side_effects, internal, function_oid '
            'FROM duckdb_functions() ORDER BY database_name, schema_name, '
            'function_name, function_oid'
        ).fetchall()
        settings = connection.execute(
            'SELECT name, input_type, scope, description '
            'FROM duckdb_settings() ORDER BY name'
        ).fetchall()
    finally:
        connection.close()

    lexical = [
        _record(
            f'lexical_rules.{position:04d}', name,
            'duckdb_keywords()', category=category,
        )
        for position, (name, category) in enumerate(keywords, 1)
    ]
    data_types = [
        _record(
            f'data_types.{position:04d}', name,
            'duckdb_types()', database=database, schema=schema,
            logical_type=logical_type, type_category=category,
            internal=bool(internal),
        )
        for position, (
            database, schema, name, logical_type, category, internal,
        ) in enumerate(types, 1)
    ]
    function_records = []
    operator_records = []
    for position, row in enumerate(functions, 1):
        (
            database, schema, name, function_type, return_type,
            parameter_types, varargs, side_effects, internal, function_oid,
        ) = row
        signature = (
            f'{name}({", ".join(str(item) for item in parameter_types)})'
            f' -> {return_type}'
        )
        function_records.append(_record(
            f'functions.{position:04d}', signature,
            f'duckdb_functions():{function_oid}', database=database,
            schema=schema, function_name=name, function_type=function_type,
            return_type=return_type, parameter_types=[
                str(item) for item in parameter_types
            ], varargs=str(varargs) if varargs is not None else None,
            has_side_effects=bool(side_effects), internal=bool(internal),
        ))
        if re.search(r'[^A-Za-z0-9_]', name):
            operator_records.append(_record(
                f'operators.{len(operator_records) + 1:04d}', signature,
                f'duckdb_functions():{function_oid}',
            ))
    session_settings = [
        _record(
            f'session_settings.{position:04d}', name,
            'duckdb_settings()', input_type=input_type, scope=scope,
            description=description,
        )
        for position, (name, input_type, scope, description) in enumerate(
            settings, 1
        )
    ]
    return {
        'lexical_rules': lexical,
        'data_types': data_types,
        'functions': function_records,
        'operators': operator_records,
        'session_settings': session_settings,
    }


def generate(source_root, source_archive):
    files, statements, commands = _grammar_inventory(source_root)
    inventories = _runtime_inventory()
    inventories.update({
        'statements': statements,
        'commands': commands,
        'diagnostics': _diagnostics(source_root),
    })
    ordered = {
        name: inventories[name] for name in (
            'lexical_rules', 'statements', 'commands', 'data_types',
            'functions', 'operators', 'session_settings', 'diagnostics',
        )
    }
    return {
        'schema': 'cdeadmin.engine-dialect-inventory.v1',
        'inventory_id': 'duckdb.dialect-inventory.1.5.2.v1',
        'engine_id': 'duckdb',
        'reference_version': REFERENCE_VERSION,
        'collection_authority': 'DuckDB 1.5.2 runtime and source parser',
        'source_evidence': {
            'archive': source_archive.name,
            'archive_sha256': _sha256(source_archive),
            'grammar_root': GRAMMAR_RELATIVE.as_posix(),
            'grammar_file_count': len(files),
            'exception_catalog': EXCEPTION_RELATIVE.as_posix(),
        },
        'inventories': ordered,
        'completeness': {
            'runtime_version': duckdb.__version__,
            'inventory_counts': {
                name: len(records) for name, records in ordered.items()
            },
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
