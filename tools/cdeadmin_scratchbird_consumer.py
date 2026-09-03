##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Versioned CDEadmin consumer boundary for a future ScratchBird driver."""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol, runtime_checkable


CONTRACT = Path('tools/cdeadmin_scratchbird_consumer_contract.json')
FIXTURE_MANIFEST = Path(
    'tools/tests/fixtures/cdeadmin_scratchbird_consumer/'
    'handoff_manifest.json'
)
METHODS = (
    'validate_configuration',
    'connect',
    'authenticate',
    'close_connection',
    'capabilities',
    'navigate',
    'execute',
    'open_cursor',
    'close_cursor',
    'read_results',
    'diagnostics',
    'cancel',
    'transaction_presentation',
)
MGA_PRESENTATION = {
    'engine_execution': 'sblr_and_internal_procedures_only',
    'internal_identity': 'uuid_backed',
    'transaction_authority': 'scratchbird_mga',
    'finality_source': 'durable_transaction_inventory',
    'driver_is_finality_authority': False,
    'consumer_interprets_finality': False,
    'cancellation_implies_rollback': False,
}
SEMVER = re.compile(r'^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$')


class ConsumerPortError(RuntimeError):
    """The driver handoff cannot be admitted safely."""

    def __init__(self, code: str, message: str):
        super().__init__(f'{code}: {message}')
        self.code = code
        self.message = message


class FixtureAdapterRefusal(ConsumerPortError):
    """An inert fixture operation was deliberately refused."""


@runtime_checkable
class ScratchBirdConsumerPort(Protocol):
    """Public handoff surface; implementations must not expose internals."""

    def adapter_manifest(self) -> Mapping[str, Any]:
        ...

    def validate_configuration(
        self, request: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        ...

    def connect(
        self, request: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        ...

    def authenticate(
        self, request: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        ...

    def close_connection(
        self, request: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        ...

    def capabilities(
        self, request: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        ...

    def navigate(
        self, request: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        ...

    def execute(
        self, request: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        ...

    def open_cursor(
        self, request: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        ...

    def close_cursor(
        self, request: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        ...

    def read_results(
        self, request: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        ...

    def diagnostics(
        self, request: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        ...

    def cancel(
        self, request: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        ...

    def transaction_presentation(
        self, request: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        ...


@dataclass(frozen=True)
class NegotiatedAdapter:
    """Immutable result of successful manifest negotiation."""

    adapter_id: str
    adapter_version: str
    consumer_contract_version: str
    methods: tuple[str, ...]


def _load_json(path: Path, label: str) -> dict:
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConsumerPortError(
            'CDE_SB_HANDOFF_DOCUMENT_INVALID', f'{label}: {exc}'
        ) from exc
    if not isinstance(value, dict):
        raise ConsumerPortError(
            'CDE_SB_HANDOFF_DOCUMENT_INVALID', f'{label} must be an object'
        )
    return value


def load_contract(source: Path) -> dict:
    """Load the repository-owned consumer contract."""
    return _load_json(source / CONTRACT, 'consumer contract')


def load_fixture_manifest(source: Path) -> dict:
    """Load the explicitly non-production fixture manifest."""
    return _load_json(source / FIXTURE_MANIFEST, 'fixture manifest')


def _version(value: Any, field: str) -> tuple[int, int, int]:
    if not isinstance(value, str):
        raise ConsumerPortError(
            'CDE_SB_ADAPTER_VERSION_UNSUPPORTED', f'{field} is not text'
        )
    match = SEMVER.fullmatch(value)
    if match is None:
        raise ConsumerPortError(
            'CDE_SB_ADAPTER_VERSION_UNSUPPORTED',
            f'{field} is not canonical semantic versioning',
        )
    return tuple(int(part) for part in match.groups())


def negotiate_manifest(manifest: Mapping[str, Any],
                       contract: Mapping[str, Any]) -> NegotiatedAdapter:
    """Admit only an exact, version-compatible public adapter manifest."""
    if manifest.get('schema') != contract.get('adapter_manifest_schema'):
        raise ConsumerPortError(
            'CDE_SB_ADAPTER_MANIFEST_INVALID', 'manifest schema is unknown'
        )
    if manifest.get('provider_mode') != 'scratchbird_native':
        raise ConsumerPortError(
            'CDE_SB_ADAPTER_MODE_REFUSED', 'provider mode is not native'
        )
    policy = contract.get('adapter_version_policy', {})
    adapter_version = _version(
        manifest.get('adapter_version'), 'adapter_version'
    )
    minimum = _version(policy.get('minimum'), 'minimum adapter version')
    maximum = _version(
        policy.get('maximum_exclusive'), 'maximum adapter version'
    )
    if not minimum <= adapter_version < maximum:
        raise ConsumerPortError(
            'CDE_SB_ADAPTER_VERSION_UNSUPPORTED',
            'adapter version is outside the admitted range',
        )
    contract_version = manifest.get('consumer_contract_version')
    if contract_version != contract.get('contract_version'):
        raise ConsumerPortError(
            'CDE_SB_CONSUMER_CONTRACT_UNSUPPORTED',
            'consumer contract version does not match exactly',
        )
    methods = manifest.get('methods')
    if not isinstance(methods, list) or tuple(methods) != METHODS:
        raise ConsumerPortError(
            'CDE_SB_ADAPTER_METHOD_SET_INVALID',
            'adapter method set or ordering is not exact',
        )
    authority = manifest.get('authority_invariants')
    if authority != MGA_PRESENTATION:
        raise ConsumerPortError(
            'CDE_SB_ADAPTER_AUTHORITY_INVALID',
            'adapter does not preserve the required authority boundary',
        )
    adapter_id = manifest.get('adapter_id')
    if not isinstance(adapter_id, str) or not adapter_id.strip():
        raise ConsumerPortError(
            'CDE_SB_ADAPTER_MANIFEST_INVALID', 'adapter_id is missing'
        )
    return NegotiatedAdapter(
        adapter_id=adapter_id,
        adapter_version=manifest['adapter_version'],
        consumer_contract_version=contract_version,
        methods=tuple(methods),
    )


def bind_adapter(adapter: Any, contract: Mapping[str, Any]) \
        -> NegotiatedAdapter:
    """Verify the object and its own manifest before any method dispatch."""
    manifest_method = getattr(adapter, 'adapter_manifest', None)
    if not callable(manifest_method):
        raise ConsumerPortError(
            'CDE_SB_ADAPTER_BINDING_INVALID', 'adapter_manifest is absent'
        )
    manifest = manifest_method()
    if not isinstance(manifest, Mapping):
        raise ConsumerPortError(
            'CDE_SB_ADAPTER_BINDING_INVALID', 'manifest is not a mapping'
        )
    negotiated = negotiate_manifest(manifest, contract)
    for method in negotiated.methods:
        if not callable(getattr(adapter, method, None)):
            raise ConsumerPortError(
                'CDE_SB_ADAPTER_BINDING_INVALID',
                f'advertised method {method} is not callable',
            )
    return negotiated


def _validate_request(request: Mapping[str, Any]) -> str:
    if not isinstance(request, Mapping):
        raise ConsumerPortError(
            'CDE_SB_REQUEST_INVALID', 'request must be a mapping'
        )
    request_id = request.get('request_id')
    if not isinstance(request_id, str) or not request_id.strip():
        raise ConsumerPortError(
            'CDE_SB_REQUEST_INVALID', 'request_id is required'
        )
    return request_id


def _validate_response(method: str, request_id: str,
                       response: Any) -> Mapping[str, Any]:
    if not isinstance(response, Mapping):
        raise ConsumerPortError(
            'CDE_SB_RESPONSE_INVALID', f'{method} response is not a mapping'
        )
    if response.get('schema') != 'cdeadmin.scratchbird-response.v1':
        raise ConsumerPortError(
            'CDE_SB_RESPONSE_INVALID', f'{method} response schema is invalid'
        )
    if response.get('method') != method:
        raise ConsumerPortError(
            'CDE_SB_RESPONSE_IDENTITY_MISMATCH', 'method identity changed'
        )
    if response.get('request_id') != request_id:
        raise ConsumerPortError(
            'CDE_SB_RESPONSE_IDENTITY_MISMATCH', 'request identity changed'
        )
    if response.get('outcome') not in {'ok', 'refused', 'unknown'}:
        raise ConsumerPortError(
            'CDE_SB_RESPONSE_INVALID', f'{method} outcome is invalid'
        )
    if not isinstance(response.get('payload'), Mapping):
        raise ConsumerPortError(
            'CDE_SB_RESPONSE_INVALID', f'{method} payload is invalid'
        )
    diagnostics = response.get('diagnostics')
    if not isinstance(diagnostics, list):
        raise ConsumerPortError(
            'CDE_SB_RESPONSE_INVALID', f'{method} diagnostics are invalid'
        )
    if method == 'transaction_presentation' and \
            response.get('outcome') == 'ok':
        authority = response['payload'].get('authority_invariants')
        if authority != MGA_PRESENTATION:
            raise ConsumerPortError(
                'CDE_SB_TRANSACTION_AUTHORITY_INVALID',
                'transaction presentation is not MGA-authoritative',
            )
    return response


class ConsumerFacade:
    """Identity-checking dispatch facade over a negotiated adapter."""

    def __init__(self, adapter: ScratchBirdConsumerPort,
                 contract: Mapping[str, Any]):
        self._adapter = adapter
        self.negotiated = bind_adapter(adapter, contract)

    def invoke(self, method: str, request: Mapping[str, Any]) \
            -> Mapping[str, Any]:
        if method not in self.negotiated.methods:
            raise ConsumerPortError(
                'CDE_SB_METHOD_REFUSED', f'{method} is not admitted'
            )
        request_id = _validate_request(request)
        response = getattr(self._adapter, method)(request)
        return _validate_response(method, request_id, response)


def evaluate(source: Path) -> dict:
    """Evaluate the checked-in contract and inert fixture declaration."""
    errors = []
    try:
        contract = load_contract(source)
        manifest = load_fixture_manifest(source)
        negotiated = negotiate_manifest(manifest, contract)
    except ConsumerPortError as exc:
        errors.append(str(exc))
        negotiated = None
        contract = {}
        manifest = {}
    if contract.get('schema') != 'cdeadmin.scratchbird-consumer-contract.v1':
        errors.append('consumer contract schema is invalid')
    if tuple(contract.get('methods', ())) != METHODS:
        errors.append('consumer method inventory is incomplete')
    fixture_expected = {
        'production': False,
        'network_enabled': False,
        'authentication_enabled': False,
        'execution_enabled': False,
        'driver_package_import': None,
        'handoff_state': 'pending_upstream_handoff',
    }
    for field, expected in fixture_expected.items():
        if manifest.get(field) != expected:
            errors.append(f'fixture {field} must be {expected!r}')
    forbidden = {'begin', 'commit', 'rollback', 'savepoint', 'replay'}
    if forbidden & set(contract.get('methods', [])):
        errors.append('consumer port exposes transaction authority commands')
    return {
        'valid': not errors,
        'errors': errors,
        'method_count': len(METHODS),
        'adapter_version': (
            negotiated.adapter_version if negotiated else None
        ),
        'production_ready': False,
        'network_enabled': manifest.get('network_enabled'),
        'authentication_enabled': manifest.get('authentication_enabled'),
        'execution_enabled': manifest.get('execution_enabled'),
        'handoff_state': manifest.get('handoff_state'),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description='Check the ScratchBird consumer handoff boundary.'
    )
    parser.add_argument('--source', type=Path, default=Path.cwd())
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args()
    result = evaluate(args.source.resolve())
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    elif result['valid']:
        print(
            'ScratchBird consumer port valid: '
            f"{result['method_count']} methods, "
            f"handoff={result['handoff_state']}"
        )
    else:
        for error in result['errors']:
            print(f'ERROR: {error}')
    return 0 if result['valid'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
