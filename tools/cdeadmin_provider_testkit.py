#!/usr/bin/env python3
"""Reusable structural, golden-corpus, and compatibility provider test kit."""

from __future__ import annotations

import argparse
import copy
import importlib.util
import json
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping
from unittest.mock import patch


DEFAULT_SCHEMA = Path(
    'web/pgadmin/cdeadmin/contracts/v1/contract.schema.json'
)
DEFAULT_RUNTIME = Path('web/pgadmin/cdeadmin/contracts/v1/runtime.py')
DEFAULT_CORPUS = Path(
    'tools/tests/fixtures/cdeadmin_contracts/golden_dto_corpus.json'
)
DEFAULT_MATRIX = Path('tools/cdeadmin_sdk_compatibility.json')


class ProviderTestKitError(RuntimeError):
    """Provider test-kit input or execution is invalid."""


class NetworkDeniedError(ProviderTestKitError):
    """A network operation was attempted inside a fixture boundary."""


def load_module(name: str, path: Path):
    """Load one Python source file without importing the pgAdmin app."""
    specification = importlib.util.spec_from_file_location(name, path)
    if specification is None or specification.loader is None:
        raise ProviderTestKitError(f'cannot load module from {path}')
    module = importlib.util.module_from_spec(specification)
    sys.modules[name] = module
    specification.loader.exec_module(module)
    return module


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ProviderTestKitError(f'cannot load JSON from {path}') from exc
    if not isinstance(value, dict):
        raise ProviderTestKitError(f'{path} must contain a JSON object')
    return value


def _deny_network(*_args, **_kwargs):
    raise NetworkDeniedError('fixture providers have no network authority')


@contextmanager
def network_denied():
    """Deny common Python network entry points for fixture execution."""
    targets = (
        'socket.socket',
        'socket.create_connection',
        'urllib.request.urlopen',
        'http.client.HTTPConnection.connect',
        'http.client.HTTPSConnection.connect',
    )
    with ExitStack() as stack:
        for target in targets:
            stack.enter_context(patch(target, side_effect=_deny_network))
        yield


def _contract_payload(payload, contract_name, contract_version):
    value = copy.deepcopy(payload)
    if contract_name == 'EnvelopeIdentity':
        value['contract_version'] = contract_version
    elif isinstance(value.get('identity'), dict):
        value['identity']['contract_version'] = contract_version
    return value


def evaluate_corpus(runtime, schema, corpus, contract_version):
    """Validate every golden DTO and prove lossless defensive copying."""
    errors = []
    entries = corpus.get('entries')
    if not isinstance(entries, list) or not entries:
        return ['golden corpus entries must be a non-empty array']
    for index, entry in enumerate(entries):
        if not isinstance(entry, Mapping):
            errors.append(f'entry {index} is not an object')
            continue
        contract_name = entry.get('contract')
        payload = entry.get('payload')
        if not isinstance(contract_name, str) or not isinstance(
            payload, Mapping
        ):
            errors.append(f'entry {index} lacks contract/payload')
            continue
        candidate = _contract_payload(
            payload, contract_name, contract_version
        )
        try:
            validated = runtime.validate_contract(
                contract_name, candidate, schema
            )
        except (TypeError, ValueError) as exc:
            errors.append(f'{contract_name}: {exc}')
            continue
        if validated != candidate:
            errors.append(f'{contract_name}: validation changed the DTO')
        if validated is candidate:
            errors.append(f'{contract_name}: validation returned input')
    return errors


def evaluate_compatibility(runtime, schema, corpus, matrix):
    """Exercise current, previous, and rejected contract-version rows."""
    errors = []
    results = []
    entries = [
        item for item in corpus.get('entries', [])
        if item.get('contract') == 'EnvelopeIdentity' or
        isinstance(item.get('payload', {}).get('identity'), Mapping)
    ]
    for row in matrix.get('contract_versions', []):
        version = row.get('contract_version')
        expected = row.get('expected')
        failures = 0
        for entry in entries:
            candidate = _contract_payload(
                entry['payload'], entry['contract'], version
            )
            try:
                runtime.validate_contract(
                    entry['contract'], candidate, schema
                )
            except (TypeError, ValueError):
                failures += 1
        compatible = failures == 0
        wanted = expected == 'compatible'
        if compatible != wanted:
            errors.append(
                f'contract {version!r} compatibility was {compatible}; '
                f'expected {wanted}'
            )
        results.append({
            'contract_version': version,
            'role': row.get('role'),
            'expected': expected,
            'payload_count': len(entries),
            'validation_failures': failures,
        })
    return results, errors


def structural_errors(schema, manifest, provider):
    """Return missing methods for every interface declared by a provider."""
    errors = []
    interfaces = schema.get('x-cdeadmin-provider-interfaces', {})
    for interface_name in manifest.get('contracts', []):
        methods = interfaces.get(interface_name)
        if methods is None:
            errors.append(f'unknown provider interface {interface_name!r}')
            continue
        for method_name in methods:
            if not callable(getattr(provider, method_name, None)):
                errors.append(
                    f'{interface_name}.{method_name} is not implemented'
                )
    return errors


@dataclass(frozen=True)
class EndpointProbe:
    """One endpoint-local operation for the concurrency/fault harness."""

    endpoint_id: str
    operation: Callable[[], Any]


@dataclass(frozen=True)
class EndpointProbeResult:
    endpoint_id: str
    value: Any = None
    error_type: str | None = None


class EndpointConcurrencyHarness:
    """Start endpoint probes together and retain failures per endpoint."""

    def run(self, probes):
        probes = tuple(probes)
        if not probes:
            raise ProviderTestKitError('at least one endpoint probe is needed')
        endpoint_ids = [item.endpoint_id for item in probes]
        if len(endpoint_ids) != len(set(endpoint_ids)):
            raise ProviderTestKitError('endpoint probe IDs must be unique')
        if any(not callable(item.operation) for item in probes):
            raise ProviderTestKitError('endpoint probes must be callable')
        barrier = threading.Barrier(len(probes))

        def invoke(probe):
            barrier.wait(timeout=5)
            try:
                return EndpointProbeResult(
                    probe.endpoint_id, copy.deepcopy(probe.operation())
                )
            except Exception as exc:
                return EndpointProbeResult(
                    probe.endpoint_id, error_type=type(exc).__name__
                )

        with ThreadPoolExecutor(max_workers=len(probes)) as executor:
            futures = [executor.submit(invoke, item) for item in probes]
            return tuple(item.result(timeout=10) for item in futures)


def evaluate(
    manifest_path: Path,
    provider_path: Path,
    schema_path: Path = DEFAULT_SCHEMA,
    runtime_path: Path = DEFAULT_RUNTIME,
    corpus_path: Path = DEFAULT_CORPUS,
    matrix_path: Path = DEFAULT_MATRIX,
    deny_network: bool = False,
):
    """Return a stable provider SDK qualification result."""
    schema = load_json(schema_path)
    manifest = load_json(manifest_path)
    corpus = load_json(corpus_path)
    matrix = load_json(matrix_path)
    runtime = load_module('cdeadmin_testkit_runtime', runtime_path)
    provider_module = load_module('cdeadmin_testkit_provider', provider_path)
    errors = []
    try:
        runtime.validate_contract('ProviderManifest', manifest, schema)
    except (TypeError, ValueError) as exc:
        errors.append(str(exc))
    factory = getattr(provider_module, 'create_provider', None)
    if not callable(factory):
        errors.append('provider module has no create_provider factory')
        provider = None
    else:
        try:
            boundary = network_denied() if deny_network else ExitStack()
            with boundary:
                provider = factory()
        except Exception as exc:
            errors.append(f'provider factory failed: {type(exc).__name__}')
            provider = None
    if provider is not None:
        errors.extend(structural_errors(schema, manifest, provider))
        endpoint_entry = next(
            (
                item['payload'] for item in corpus['entries']
                if item['contract'] == 'Endpoint'
            ),
            None,
        )
        if deny_network and endpoint_entry is not None:
            try:
                with network_denied():
                    provider.validate_endpoint(copy.deepcopy(endpoint_entry))
                    provider.discover_endpoint(copy.deepcopy(endpoint_entry))
            except Exception as exc:
                errors.append(
                    f'network-denied smoke failed: {type(exc).__name__}'
                )
    current = schema['contract_version']
    errors.extend(evaluate_corpus(runtime, schema, corpus, current))
    rows, matrix_errors = evaluate_compatibility(
        runtime, schema, corpus, matrix
    )
    errors.extend(matrix_errors)
    return {
        'schema': 'cdeadmin.provider-test-kit-result.v1',
        'contract_version': current,
        'provider_id': manifest.get('identity', {}).get('provider_id'),
        'golden_dto_count': len(corpus.get('entries', [])),
        'compatibility_results': rows,
        'network_denied': deny_network,
        'valid': not errors,
        'errors': errors,
    }


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--provider', type=Path, required=True)
    parser.add_argument('--schema', type=Path, default=DEFAULT_SCHEMA)
    parser.add_argument('--runtime', type=Path, default=DEFAULT_RUNTIME)
    parser.add_argument('--corpus', type=Path, default=DEFAULT_CORPUS)
    parser.add_argument('--matrix', type=Path, default=DEFAULT_MATRIX)
    parser.add_argument('--deny-network', action='store_true')
    parser.add_argument('--output', type=Path)
    return parser


def main():
    args = build_parser().parse_args()
    result = evaluate(
        args.manifest,
        args.provider,
        args.schema,
        args.runtime,
        args.corpus,
        args.matrix,
        args.deny_network,
    )
    rendered = json.dumps(result, indent=2, sort_keys=True) + '\n'
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding='utf-8')
    else:
        print(rendered, end='')
    return 0 if result['valid'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
