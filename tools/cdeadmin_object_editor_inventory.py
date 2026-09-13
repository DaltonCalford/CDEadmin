#!/usr/bin/env python3
"""Inventory declared object editors without connecting or claiming parity.

This is the checked-in portfolio baseline, not the runtime-adapted catalog.
Provider adapters can replace these forms. Live, native-option completeness
and lossless edit round trips require separate evidence.
"""

import argparse
import json
import sys
from pathlib import Path
from types import ModuleType


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'web'))
if 'pgadmin' not in sys.modules:
    package = ModuleType('pgadmin')
    package.__path__ = [str(ROOT / 'web/pgadmin')]
    sys.modules['pgadmin'] = package

from pgadmin.cdeadmin.visual_admin.catalog import (  # noqa: E402
    PORTFOLIO_ENGINE_IDS, catalog_for_engine,
)


def inventory():
    engines = []
    for engine_id in PORTFOLIO_ENGINE_IDS:
        if engine_id == 'scratchbird':
            continue
        catalog = catalog_for_engine(engine_id)
        objects = []
        for resource in catalog['objects']:
            operations = []
            for operation in resource['operations']:
                fields = operation['form']['fields']
                operations.append({
                    'operation_id': operation['operation_id'],
                    'form_id': operation['form']['form_id'],
                    'form_authority': operation.get('form_authority'),
                    'fields': [{key: field[key] for key in (
                        'field_id', 'label', 'control', 'required'
                    ) if key in field} for field in fields],
                    'structured_text_fields': [
                        field['field_id'] for field in fields
                        if field['control'] in {'json', 'code'}
                    ],
                    'native_option_completeness': 'not-verified',
                    'lossless_edit_round_trip': 'not-verified',
                })
            objects.append({
                'resource_kind': resource['resource_kind'],
                'title': resource['title'],
                'editor': resource['editor'],
                'operations': operations,
            })
        engines.append({'engine_id': engine_id,
                        'reference_profile': catalog['reference_profile'],
                        'objects': objects})
    return {'scope': 'checked-in-portfolio-baseline',
            'excluded_engines': ['scratchbird'],
            'runtime_adapters_evaluated': False,
            'native_inventory_completeness_verified': False,
            'engines': engines}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-directory', required=True, type=Path)
    args = parser.parse_args()
    result = inventory()
    args.output_directory.mkdir(parents=True, exist_ok=True)
    (args.output_directory / 'EDITOR_INVENTORY.json').write_text(
        json.dumps(result, indent=2) + '\n', encoding='utf-8')
    lines = [
        '# Object editor baseline inventory', '',
        'This inventories declared forms, not complete native engine support.',
        'Runtime provider replacements and live mutations are not evaluated.',
        'SQL/code and JSON controls require review: code is appropriate for',
        'programmable bodies, but does not prove structural visual editing.',
        'All option-completeness and lossless-edit checks remain unverified.',
        'ScratchBird native is excluded by request.', '',
        '| Engine | Object kinds | Operations | Code/JSON forms |',
        '| --- | ---: | ---: | ---: |',
    ]
    for engine in result['engines']:
        operations = [op for obj in engine['objects']
                      for op in obj['operations']]
        text_count = sum(bool(op['structured_text_fields'])
                         for op in operations)
        lines.append(f"| {engine['engine_id']} | {len(engine['objects'])} | "
                     f"{len(operations)} | {text_count} |")
    lines += ['', 'See EDITOR_INVENTORY.json for every object, operation,',
              'form identity, field control and outstanding verification.', '']
    (args.output_directory / 'EDITOR_INVENTORY.md').write_text(
        '\n'.join(lines), encoding='utf-8')


if __name__ == '__main__':
    main()
