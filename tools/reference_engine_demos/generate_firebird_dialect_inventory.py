#!/usr/bin/env python3
##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Generate the exact Firebird 5.0.4 SQL grammar inventory.

The generator reads pinned, preserved Firebird release sources and never
modifies them.  It records every lexer token and every grammar production,
then derives named dialect facets only from explicitly selected productions.
It does not activate the provider: generated CDEadmin statements still need
parser-acceptance and live-execution evidence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path


REFERENCE_VERSION = '5.0.4'
EXPECTED_DIGESTS = {
    'src/dsql/parse.y': (
        'd421d94a593cd8d8a40346191def1b2db7adbcccce09e566c9ff2703758b671b'
    ),
    'src/common/ParserTokens.h': (
        'ba184f0ef4422a4e827da05fc173f553b77493406bd2959eb20976a56fd8477d'
    ),
    'src/jrd/SysFunction.cpp': (
        'ccd3df2fc1106bae57fdcb489f3accc31d81508b747dd98f1768845a632f3e3d'
    ),
}
TOKEN_PATTERN = re.compile(
    r'^PARSER_TOKEN\((?P<identifier>[^,]+),\s*"(?P<text>(?:[^"\\]|\\.)*)",'
    r'\s*(?P<non_reserved>true|false)\)',
)
PRODUCTION_HEADER = re.compile(
    r'^(?P<name>[a-z][A-Za-z0-9_]*)(?:\([^\n]*\))?\s*$',
)


FACET_PRODUCTIONS = {
    'statements': (
        'dml_statement', 'ddl_statement', 'tra_statement', 'mng_statement',
    ),
    'commands': (
        'create_clause', 'alter_clause', 'drop_clause', 'recreate_clause',
        'replace_clause', 'grant0', 'revoke0', 'comment', 'set_statistics',
    ),
    'data_types': (
        'data_type', 'non_array_type', 'array_type',
        'domain_or_non_array_type',
        'domain_or_non_array_type_name', 'data_type_descriptor',
        'alter_data_type_or_domain', 'udf_data_type',
    ),
    'functions': (
        'aggregate_function_prefix', 'window_function',
        'numeric_value_function', 'string_value_function',
        'system_function_std_syntax', 'system_function_special_syntax',
    ),
    'operators': (
        'comparison_operator', 'binary_pattern_operator',
        'concatenation', 'arithmetic', 'boolean_value', 'predicate',
    ),
    'session_settings': ('mng_statement',),
    'diagnostics': (
        'plan_clause', 'plan_expression', 'plan_item', 'exception_item',
    ),
}


def _sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _read_pinned(source_root):
    sources = {}
    for relative, expected in EXPECTED_DIGESTS.items():
        path = source_root / relative
        observed = _sha256(path)
        if observed != expected:
            raise RuntimeError(
                f'Firebird source digest mismatch for {relative}'
            )
        sources[relative] = {
            'path': path,
            'sha256': observed,
            'text': path.read_text(encoding='utf-8'),
        }
    return sources


def _tokens(text):
    records = []
    for line_number, line in enumerate(text.splitlines(), 1):
        match = TOKEN_PATTERN.match(line.strip())
        if match is None:
            continue
        records.append({
            'token_id': f'lexer.token.{len(records) + 1:04d}',
            'identifier': match.group('identifier').strip(),
            'text': bytes(
                match.group('text'), 'utf-8'
            ).decode('unicode_escape'),
            'non_reserved': match.group('non_reserved') == 'true',
            'source': f'src/common/ParserTokens.h:{line_number}',
        })
    if not records:
        raise RuntimeError('Firebird lexer token inventory is empty')
    return records


def _grammar_region(text):
    lines = text.splitlines()
    markers = [index for index, line in enumerate(lines) if line == '%%']
    if len(markers) != 2:
        raise RuntimeError('Firebird grammar delimiters are not exact')
    return lines, markers[0] + 1, markers[1]


def _productions(text):
    lines, start, end = _grammar_region(text)
    records = []
    index = start
    while index < end:
        header = PRODUCTION_HEADER.match(lines[index])
        if header is None:
            index += 1
            continue
        colon = index + 1
        while colon < end and not lines[colon].strip():
            colon += 1
        if colon >= end or not lines[colon].lstrip().startswith(':'):
            index += 1
            continue
        finish = colon
        while finish < end and lines[finish].strip() != ';':
            finish += 1
        if finish >= end:
            raise RuntimeError(
                'unterminated Firebird grammar production '
                f'{header.group("name")}'
            )
        block = lines[index:finish + 1]
        alternative_starts = [
            position for position, line in enumerate(block)
            if line.lstrip().startswith((':', '|'))
        ]
        alternatives = []
        for position, offset in enumerate(alternative_starts):
            next_offset = (
                alternative_starts[position + 1]
                if position + 1 < len(alternative_starts)
                else len(block) - 1
            )
            alternative = '\n'.join(block[offset:next_offset]).rstrip()
            alternatives.append({
                'alternative_id': (
                    f'{header.group("name")}.{position + 1:03d}'
                ),
                'grammar_source': alternative,
                'source': (
                    f'src/dsql/parse.y:{index + offset + 1}-'
                    f'{index + next_offset}'
                ),
            })
        records.append({
            'production_id': f'grammar.production.{len(records) + 1:04d}',
            'name': header.group('name'),
            'source': f'src/dsql/parse.y:{index + 1}-{finish + 1}',
            'grammar_source': '\n'.join(block),
            'alternatives': alternatives,
        })
        index = finish + 1
    names = [item['name'] for item in records]
    if not records or len(names) != len(set(names)):
        raise RuntimeError('Firebird grammar production inventory is invalid')
    return records


def _facet_inventory(productions):
    by_name = {item['name']: item for item in productions}
    result = {}
    for facet, names in FACET_PRODUCTIONS.items():
        items = []
        for name in names:
            production = by_name.get(name)
            if production is None:
                continue
            for alternative in production['alternatives']:
                items.append({
                    'item_id': f'{facet}.{len(items) + 1:04d}',
                    'production': name,
                    'native_name': alternative['alternative_id'],
                    'source': alternative['source'],
                    'grammar_source': alternative['grammar_source'],
                })
        result[facet] = items
    return result


def generate(source_root):
    sources = _read_pinned(source_root)
    tokens = _tokens(sources['src/common/ParserTokens.h']['text'])
    productions = _productions(sources['src/dsql/parse.y']['text'])
    facets = _facet_inventory(productions)
    return {
        'schema': 'cdeadmin.engine-dialect-inventory.v1',
        'inventory_id': 'firebird.dialect-inventory.5.0.4.v1',
        'engine_id': 'firebird',
        'reference_version': REFERENCE_VERSION,
        'language_profile': 'firebird-sql',
        'completeness': {
            'lexer_authority': 'every PARSER_TOKEN record',
            'grammar_authority': 'every production between parse.y delimiters',
            'lexer_token_count': len(tokens),
            'grammar_production_count': len(productions),
            'grammar_alternative_count': sum(
                len(item['alternatives']) for item in productions
            ),
            'facet_counts': {
                name: len(items) for name, items in facets.items()
            },
            'cdeadmin_generated_tasks_qualified': False,
        },
        'source_evidence': [{
            'artifact': relative,
            'sha256': record['sha256'],
            'format': 'Firebird release source',
            'license_id': 'IPL-1.0',
        } for relative, record in sources.items()],
        'lexer_tokens': tokens,
        'grammar_productions': productions,
        'dialect_facets': facets,
    }


def _report(document):
    completeness = document['completeness']
    rows = [
        '# Firebird 5.0.4 Exact Dialect Inventory', '',
        'This inventory is generated from digest-pinned Firebird 5.0.4 '
        'release parser sources. It contains every lexer token and every '
        'grammar production. No PostgreSQL syntax or shared SQL defaults are '
        'used.', '',
        f"- Lexer tokens: {completeness['lexer_token_count']}",
        f"- Grammar productions: {completeness['grammar_production_count']}",
        f"- Grammar alternatives: {completeness['grammar_alternative_count']}",
        '- Provider activation: blocked until every CDEadmin-generated task '
        'has parser-acceptance and live-execution evidence.', '',
        '## Derived dialect facets', '',
    ]
    rows.extend(
        f'- {name.replace("_", " ")}: {count}'
        for name, count in completeness['facet_counts'].items()
    )
    rows.extend(['', '## Source evidence', ''])
    rows.extend(
        f"- `{item['artifact']}` — SHA-256 `{item['sha256']}`"
        for item in document['source_evidence']
    )
    rows.extend([
        '',
        'The derived facets are indexes into the complete production '
        'inventory; they do not replace or truncate the authoritative '
        'grammar.',
    ])
    return '\n'.join(rows) + '\n'


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    options = parser.parse_args(argv)
    document = generate(options.source_root)
    options.output.parent.mkdir(parents=True, exist_ok=True)
    options.output.write_text(
        json.dumps(document, indent=2, sort_keys=True) + '\n',
        encoding='utf-8',
    )
    options.report.parent.mkdir(parents=True, exist_ok=True)
    options.report.write_text(_report(document), encoding='utf-8')
    print(json.dumps(document['completeness'], indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
