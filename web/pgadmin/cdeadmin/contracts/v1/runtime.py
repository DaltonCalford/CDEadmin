##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Dependency-free validation and safe admission for contract v1."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Collection, Mapping


SCHEMA_PATH = Path(__file__).with_name('contract.schema.json')
ACTIVE_SUPPORT_STATES = {
    'implemented',
    'compatibility_mapped',
    'connector_managed',
    'experimental',
}
NON_MUTATING_CLASSES = {'none', 'read'}
KNOWN_MUTATING_CLASSES = {'write', 'admin', 'destructive'}


class ContractValidationError(ValueError):
    """A contract payload is structurally or semantically invalid."""


def load_contract_schema() -> dict[str, Any]:
    """Load the canonical v1 schema."""
    return json.loads(SCHEMA_PATH.read_text(encoding='utf-8'))


def _matches_type(value: Any, expected: str) -> bool:
    type_checks = {
        'array': lambda item: isinstance(item, list),
        'boolean': lambda item: isinstance(item, bool),
        'integer': lambda item: (
            isinstance(item, int) and not isinstance(item, bool)
        ),
        'null': lambda item: item is None,
        'number': lambda item: (
            isinstance(item, (int, float)) and not isinstance(item, bool)
        ),
        'object': lambda item: isinstance(item, Mapping),
        'string': lambda item: isinstance(item, str),
    }
    return expected in type_checks and type_checks[expected](value)


def _resolve(schema: Mapping[str, Any], node: Mapping[str, Any]):
    reference = node.get('$ref')
    if not reference:
        return node
    prefix = '#/$defs/'
    if not isinstance(reference, str) or not reference.startswith(prefix):
        raise ContractValidationError(
            f'unsupported schema reference {reference}'
        )
    name = reference[len(prefix):]
    try:
        return schema['$defs'][name]
    except KeyError as exc:
        raise ContractValidationError(
            f'unknown schema reference {reference}'
        ) from exc


def _validate_node(
        schema: Mapping[str, Any], node: Mapping[str, Any], value: Any,
        path: str) -> None:
    node = _resolve(schema, node)
    expected = node.get('type')
    if expected is not None:
        accepted = expected if isinstance(expected, list) else [expected]
        if not any(_matches_type(value, item) for item in accepted):
            labels = ', '.join(accepted)
            raise ContractValidationError(
                f'{path} must have type {labels}; got '
                f'{type(value).__name__}'
            )
    if value is None:
        return
    if isinstance(value, Mapping):
        required = node.get('required', [])
        missing = [item for item in required if item not in value]
        if missing:
            raise ContractValidationError(
                f'{path} is missing required fields: {", ".join(missing)}'
            )
        properties = node.get('properties', {})
        for key, child in properties.items():
            if key in value:
                _validate_node(
                    schema, child, value[key], f'{path}.{key}'
                )
    if isinstance(value, list) and 'items' in node:
        for index, item in enumerate(value):
            _validate_node(
                schema, node['items'], item, f'{path}[{index}]'
            )


def _major(version: str) -> str:
    return version.split('.', 1)[0]


def _validate_semantics(
    name: str, payload: Mapping[str, Any], schema: Mapping[str, Any]
) -> None:
    identity = payload if name == 'EnvelopeIdentity' else payload.get(
        'identity'
    )
    if isinstance(identity, Mapping):
        version = identity.get('contract_version', '')
        if _major(version) != _major(schema['contract_version']):
            raise ContractValidationError(
                f'unsupported contract major version {version!r}'
            )
        if not identity.get('evidence_reference'):
            raise ContractValidationError(
                'identity.evidence_reference must not be empty'
            )
    if name == 'ProviderManifest':
        state = payload.get('support_state')
        if state in {'deferred', 'unsupported'} and payload.get('enabled'):
            raise ContractValidationError(
                f'{state} provider manifests must be disabled'
            )
        if payload.get('fixture'):
            permissions = payload.get('permissions', [])
            registered = payload.get('production_registration')
            if payload.get('enabled') or registered:
                raise ContractValidationError(
                    'fixture providers cannot be enabled or registered'
                )
            if any(item.get('granted') for item in permissions):
                raise ContractValidationError(
                    'fixture providers cannot receive permissions'
                )


def validate_contract(
        name: str, payload: Mapping[str, Any],
        schema: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Validate and return a defensive copy without dropping extensions."""
    contract = schema or load_contract_schema()
    definitions = contract.get('$defs', {})
    if name not in definitions:
        raise ContractValidationError(f'unknown contract type {name!r}')
    _validate_node(contract, definitions[name], payload, name)
    _validate_semantics(name, payload, contract)
    return copy.deepcopy(dict(payload))


def admit_capability(
    payload: Mapping[str, Any], known_capability_ids: Collection[str]
) -> bool:
    """Return whether an old client may activate a capability safely.

    Unknown enum values never activate behavior. Unknown read capabilities may
    be displayed, but only known capability IDs are executable. Any unfamiliar
    mutating or destructive capability therefore fails closed.
    """
    try:
        value = validate_contract('Capability', payload)
    except ContractValidationError:
        return False
    if not value['enabled']:
        return False
    if value['support_state'] not in ACTIVE_SUPPORT_STATES:
        return False
    mutation_class = value['mutation_class']
    known_classes = NON_MUTATING_CLASSES | KNOWN_MUTATING_CLASSES
    if mutation_class not in known_classes:
        return False
    if value['capability_id'] not in known_capability_ids:
        return False
    if mutation_class in KNOWN_MUTATING_CLASSES:
        return bool(value['required_permissions'])
    return True
