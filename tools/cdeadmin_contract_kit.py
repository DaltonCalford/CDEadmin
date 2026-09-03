#!/usr/bin/env python3
"""Validate a CDEadmin provider manifest against the versioned SDK."""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any


SCHEMA_PATH = Path(
    'web/pgadmin/cdeadmin/contracts/v1/contract.schema.json'
)
RUNTIME_PATH = Path('web/pgadmin/cdeadmin/contracts/v1/runtime.py')


def load_runtime(path: Path):
    """Load the contract runtime without importing the pgAdmin application."""
    specification = importlib.util.spec_from_file_location(
        'cdeadmin_contract_runtime', path
    )
    if specification is None or specification.loader is None:
        raise RuntimeError(f'cannot load contract runtime from {path}')
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def version_parts(value: str) -> tuple[int, ...]:
    """Parse the numeric compatibility portion of a semantic version."""
    try:
        return tuple(int(item) for item in value.split('-', 1)[0].split('.'))
    except ValueError as exc:
        raise ValueError(f'invalid SDK version {value!r}') from exc


def evaluate(
    manifest_path: Path, schema_path: Path, runtime_path: Path
) -> dict[str, Any]:
    """Return a stable structural contract evaluation."""
    schema = json.loads(schema_path.read_text(encoding='utf-8'))
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    runtime = load_runtime(runtime_path)
    errors = []
    try:
        runtime.validate_contract('ProviderManifest', manifest, schema)
    except (ValueError, TypeError) as exc:
        errors.append(str(exc))
    available = schema.get('x-cdeadmin-provider-interfaces', {})
    unknown = sorted(set(manifest.get('contracts', [])) - set(available))
    if unknown:
        errors.append(f'unknown provider contracts: {", ".join(unknown)}')
    compatibility = manifest.get('sdk_compatibility', {})
    try:
        current = version_parts(schema['contract_version'])
        minimum = version_parts(compatibility['minimum'])
        maximum = version_parts(compatibility['maximum_exclusive'])
        if not minimum <= current < maximum:
            errors.append('contract version is outside the provider SDK range')
    except (KeyError, TypeError, ValueError) as exc:
        errors.append(str(exc))
    return {
        'schema': 'cdeadmin.provider-contract-kit-result.v1',
        'contract_version': schema['contract_version'],
        'manifest': manifest_path.as_posix(),
        'provider_id': manifest.get('identity', {}).get('provider_id'),
        'valid': not errors,
        'errors': errors,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--schema', type=Path, default=SCHEMA_PATH)
    parser.add_argument('--runtime', type=Path, default=RUNTIME_PATH)
    parser.add_argument('--output', type=Path)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    result = evaluate(args.manifest, args.schema, args.runtime)
    rendered = json.dumps(result, indent=2, sort_keys=True) + '\n'
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding='utf-8')
    else:
        print(rendered, end='')
    return 0 if result['valid'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
