#!/usr/bin/env python3
"""Generate CDEadmin Python and TypeScript DTOs from the v1 schema."""

from __future__ import annotations

import argparse
import json
import re
import sys
import textwrap
from pathlib import Path
from typing import Any, Mapping


SOURCE = Path('web/pgadmin/cdeadmin/contracts/v1/contract.schema.json')
PYTHON_OUTPUT = Path('web/pgadmin/cdeadmin/contracts/v1/generated.py')
TYPESCRIPT_OUTPUT = Path(
    'web/pgadmin/cdeadmin/static/js/contracts/v1/generated.ts'
)


def class_name(value: str) -> str:
    """Convert a property name to a stable generated symbol."""
    return ''.join(item.capitalize() for item in re.split(r'[_-]+', value))


def py_type(node: Mapping[str, Any]) -> str:
    """Map the supported JSON Schema subset to Python annotations."""
    if '$ref' in node:
        return node['$ref'].rsplit('/', 1)[-1]
    expected = node.get('type', 'object')
    if isinstance(expected, list):
        concrete = [item for item in expected if item != 'null']
        base = py_type({**node, 'type': concrete[0]})
        return f'{base} | None'
    if expected == 'array':
        return f"list[{py_type(node.get('items', {}))}]"
    return {
        'boolean': 'bool',
        'integer': 'int',
        'number': 'float',
        'object': 'dict[str, Any]',
        'string': 'str',
    }.get(expected, 'Any')


def ts_type(node: Mapping[str, Any]) -> str:
    """Map the supported JSON Schema subset to TypeScript types."""
    if '$ref' in node:
        return node['$ref'].rsplit('/', 1)[-1]
    expected = node.get('type', 'object')
    if isinstance(expected, list):
        values = [ts_type({**node, 'type': item}) for item in expected]
        return ' | '.join(values)
    if expected == 'array':
        return f"Array<{ts_type(node.get('items', {}))}>"
    return {
        'boolean': 'boolean',
        'integer': 'number',
        'null': 'null',
        'number': 'number',
        'object': 'Record<string, unknown>',
        'string': 'string',
    }.get(expected, 'unknown')


def interface_py_type(value: str) -> str:
    if value.startswith('array:'):
        return f"list[{value.split(':', 1)[1]}]"
    return value


def interface_ts_type(value: str) -> str:
    if value.startswith('array:'):
        return f"Array<{value.split(':', 1)[1]}>"
    return value


def open_enums(schema: Mapping[str, Any]) -> dict[str, list[str]]:
    """Collect known values while intentionally retaining string openness."""
    values: dict[str, set[str]] = {}
    for definition in schema['$defs'].values():
        for name, prop in definition.get('properties', {}).items():
            known = prop.get('x-known-values')
            if known:
                values.setdefault(name, set()).update(known)
    return {
        name: sorted(items)
        for name, items in sorted(values.items())
    }


def python_docstring(value: str, indent: str = '    ') -> list[str]:
    """Render a generated class docstring within the Python style limit."""
    wrapped = textwrap.wrap(value, width=70) or ['Generated contract.']
    if len(wrapped) == 1:
        return [f'{indent}"""{wrapped[0]}"""']
    return [
        f'{indent}"""{wrapped[0]}',
        *(f'{indent}{item}' for item in wrapped[1:]),
        f'{indent}"""',
    ]


def render_python(schema: Mapping[str, Any]) -> str:
    """Render deterministic Python dataclasses and provider protocols."""
    lines = [
        '#' * 74,
        '#',
        '# CDEadmin - Multi-engine Database Administration',
        '#',
        '# Copyright (C) 2013 - 2026, The pgAdmin Development Team',
        '# This software is released under the PostgreSQL Licence',
        '#',
        '#' * 74,
        '',
        '"""Generated CDEadmin provider contract DTOs. Do not edit."""',
        '',
        'from __future__ import annotations',
        '',
        'from dataclasses import dataclass, field, fields',
        'from typing import Any, Protocol',
        '',
        f"CONTRACT_VERSION = {schema['contract_version']!r}",
        '',
    ]
    for name, values in open_enums(schema).items():
        symbol = f'KNOWN_{name.upper()}_VALUES'
        lines.append(f'{symbol} = (')
        lines.extend(f'    {value!r},' for value in values)
        lines.append(')')
    lines.extend([
        '',
        '',
        'class ContractDTO:',
        '    """Generated DTO with lossless extension round trips."""',
        '',
        '    def to_dict(self) -> dict[str, Any]:',
        '        """Return all fields without interpretation."""',
        "        result = dict(getattr(self, 'additional_fields', {}))",
        '        for item in fields(self):',
        "            if item.name != 'additional_fields':",
        '                value = getattr(self, item.name)',
        '                result[item.name] = _to_value(value)',
        '        return result',
        '',
        '',
        'def _to_value(value: Any) -> Any:',
        '    if isinstance(value, ContractDTO):',
        '        return value.to_dict()',
        '    if isinstance(value, list):',
        '        return [_to_value(item) for item in value]',
        '    if isinstance(value, dict):',
        '        return {key: _to_value(item) for key, item in value.items()}',
        '    return value',
    ])
    for name, definition in schema['$defs'].items():
        properties = definition.get('properties', {})
        required = set(definition.get('required', []))
        ordered = [
            (key, value) for key, value in properties.items()
            if key in required
        ] + [
            (key, value) for key, value in properties.items()
            if key not in required
        ]
        lines.extend([
            '',
            '',
            '@dataclass(frozen=True)',
            f'class {name}(ContractDTO):',
        ])
        lines.extend(python_docstring(definition.get('description', name)))
        for key, prop in ordered:
            annotation = py_type(prop)
            if key in required:
                lines.append(f'    {key}: {annotation}')
            elif annotation.startswith('list['):
                lines.append(
                    f'    {key}: {annotation} = field(default_factory=list)'
                )
            elif annotation.startswith('dict['):
                lines.append(
                    f'    {key}: {annotation} = field(default_factory=dict)'
                )
            else:
                optional = annotation if annotation.endswith(' | None') \
                    else f'{annotation} | None'
                lines.append(f'    {key}: {optional} = None')
        lines.append(
            '    additional_fields: dict[str, Any] = '
            'field(default_factory=dict, repr=False)'
        )
    for name, methods in schema['x-cdeadmin-provider-interfaces'].items():
        lines.extend([
            '',
            '',
            f'class {name}(Protocol):',
            f'    """Structural {name} SDK contract."""',
            '',
        ])
        method_rows = list(methods.items())
        for index, (method, signature) in enumerate(method_rows):
            request, response = map(interface_py_type, signature)
            signature_line = (
                f'    def {method}(self, request: {request}) -> {response}:'
            )
            if len(signature_line) <= 79:
                lines.append(signature_line)
            else:
                lines.extend([
                    f'    def {method}(',
                    f'            self, request: {request}) -> {response}:',
                ])
            lines.append('        ...')
            if index < len(method_rows) - 1:
                lines.append('')
    return '\n'.join(lines).rstrip() + '\n'


def render_typescript(schema: Mapping[str, Any]) -> str:
    """Render deterministic TypeScript DTO and provider interfaces."""
    lines = [
        '/' * 75,
        '//',
        '// CDEadmin - Multi-engine Database Administration',
        '//',
        '// Copyright (C) 2013 - 2026, The pgAdmin Development Team',
        '// This software is released under the PostgreSQL Licence',
        '//',
        '/' * 75,
        '',
        '/** Generated CDEadmin provider contract DTOs. Do not edit. */',
        f"export const CONTRACT_VERSION = '{schema['contract_version']}';",
        '',
    ]
    for name, values in open_enums(schema).items():
        symbol = f'KNOWN_{name.upper()}_VALUES'
        lines.append(f'export const {symbol} = [')
        lines.extend(f'  {value!r},' for value in values)
        lines.append('] as const;')
    lines.append('')
    for name, definition in schema['$defs'].items():
        required = set(definition.get('required', []))
        lines.append(f'export interface {name} {{')
        for key, prop in definition.get('properties', {}).items():
            marker = '' if key in required else '?'
            lines.append(f'  {key}{marker}: {ts_type(prop)};')
        lines.extend([
            '  [key: string]: unknown;',
            '}',
            '',
        ])
    for name, methods in schema['x-cdeadmin-provider-interfaces'].items():
        lines.append(f'export interface {name} {{')
        for method, signature in methods.items():
            request = interface_ts_type(signature[0])
            response = interface_ts_type(signature[1])
            lines.append(
                f'  {method}(request: {request}): Promise<{response}>;'
            )
        lines.extend(['}', ''])
    return '\n'.join(lines).rstrip() + '\n'


def write_or_check(path: Path, expected: str, check: bool) -> bool:
    """Write generated content or report whether it is current."""
    if check:
        actual = path.read_text(encoding='utf-8') if path.exists() else None
        if actual != expected:
            print(f'out of date: {path}', file=sys.stderr)
            return False
        return True
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(expected, encoding='utf-8')
    print(f'generated: {path}')
    return True


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=SOURCE)
    parser.add_argument('--python-output', type=Path, default=PYTHON_OUTPUT)
    parser.add_argument(
        '--typescript-output', type=Path, default=TYPESCRIPT_OUTPUT
    )
    parser.add_argument('--check', action='store_true')
    return parser


def main() -> int:
    args = build_parser().parse_args()
    schema = json.loads(args.source.read_text(encoding='utf-8'))
    results = [
        write_or_check(
            args.python_output, render_python(schema), args.check
        ),
        write_or_check(
            args.typescript_output, render_typescript(schema), args.check
        ),
    ]
    return 0 if all(results) else 1


if __name__ == '__main__':
    raise SystemExit(main())
