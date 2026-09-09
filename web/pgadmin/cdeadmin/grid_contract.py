##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Provider-neutral grid metadata and fail-closed adoption gates.

This module describes presentation mechanics only.  It never interprets an
engine value, rewrites a query, invents a row identity, or changes provider
transaction state.  Native workspaces remain authoritative for non-tabular
models; the grid contract supplies their tabular inspection surfaces.
"""

from __future__ import annotations

import copy
import re
from typing import Any, Iterable, Mapping


GRID_WORKSPACE_SCHEMA = 'cdeadmin.provider-grid-workspace.v1'
GRID_RESULT_SCHEMA = 'cdeadmin.provider-grid-result.v1'

_NATIVE_VIEWS = {
    'document': 'cdeadmin/results/DocumentTreeView',
    'bitemporal_document': 'cdeadmin/results/BitemporalDocumentView',
    'graph': 'cdeadmin/results/GraphView',
    'key_value': 'cdeadmin/results/KeyValueView',
    'wide_column': 'cdeadmin/results/WideColumnView',
    'columnar': 'cdeadmin/results/ColumnarView',
    'time_series': 'cdeadmin/results/TimeSeriesView',
    'vector': 'cdeadmin/results/VectorView',
    'search': 'cdeadmin/results/SearchView',
    'cellset': 'cdeadmin/results/CubePivotView',
    'plan': 'cdeadmin/results/PlanView',
    'tabular': 'SchemaView/DataGridView',
}

_TABULAR_MODELS = frozenset({
    'relational', 'postgresql', 'distributed-relational', 'distributed-sql',
    'versioned-relational', 'immutable-multimodel',
    'columnar-analytic', 'wide-column', 'distributed-wide-column',
    'search-relational-analytic',
})

_NATIVE_VIEW_MODELS = frozenset({
    'document', 'graph', 'data-structure-key-value',
    'time-series-analytic', 'vector-analytic', 'search-analytic',
    'search-document-analytic', 'columnar-analytic', 'wide-column',
    'distributed-wide-column', 'bitemporal-document-relational',
    'distributed', 'ordered-key-value', 'distributed-key-value',
})

_SENSITIVE_PARTS = frozenset({
    'password', 'passwd', 'secret', 'token', 'credential', 'credentials',
    'privatekey', 'private_key', 'accesskey', 'access_key', 'clientsecret',
    'client_secret', 'apikey', 'api_key',
})

_NUMBER_TYPES = frozenset({
    'bigint', 'decimal', 'double', 'float', 'int', 'integer', 'number',
    'numeric', 'real', 'smallint', 'tinyint', 'uint', 'uint8', 'uint16',
    'uint32', 'uint64', 'int8', 'int16', 'int32', 'int64', 'float32',
    'float64', 'decimal32', 'decimal64', 'decimal128', 'decimal256',
    'int128', 'decfloat', 'varint', 'counter', 'serial', 'bigserial',
})
_BOOLEAN_TYPES = frozenset({'bool', 'boolean'})
_JSON_TYPES = frozenset({
    'json', 'jsonb', 'document', 'object', 'map', 'array', 'list', 'struct',
})
_DATE_TYPES = frozenset({'date'})
_DATETIME_TYPES = frozenset({
    'datetime', 'timestamp', 'timestamp with time zone', 'timestamptz',
    'datetime64', 'instant',
})


class GridContractError(ValueError):
    """A provider supplied unsafe or unusable grid metadata."""


def _identifier(value: object, fallback: str) -> str:
    source = value.strip() if isinstance(value, str) else ''
    source = source or fallback
    normalized = re.sub(r'[^A-Za-z0-9_.:-]+', '-', source).strip('-')
    return normalized or fallback


def _engine_type(column: Mapping[str, Any]) -> str:
    for key in ('engine_type', 'engineType', 'type', 'data_type', 'dataType'):
        value = column.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ''


def _base_type(engine_type: str) -> str:
    value = engine_type.lower().strip()
    wrappers = ('nullable', 'lowcardinality')
    while any(value.startswith(f'{wrapper}(') for wrapper in wrappers):
        value = value.split('(', 1)[1].rsplit(')', 1)[0].strip()
    if value.startswith(('simpleaggregatefunction(', 'aggregatefunction(')):
        value = value.rsplit(',', 1)[-1].rsplit(')', 1)[0].strip()
    value = re.sub(r'\([^)]*\)', '', value).strip()
    for separator in ('<', '[', ' '):
        value = value.split(separator, 1)[0]
    return value


def infer_cell_type(column: Mapping[str, Any]) -> str:
    """Infer only a renderer class; preserve the provider's native type."""
    explicit = column.get('cell_type', column.get('cellType'))
    if explicit in {
        'text', 'number', 'boolean', 'date', 'datetime', 'json',
        'status', 'progress', 'comment',
    }:
        return str(explicit)
    engine_type = _base_type(_engine_type(column))
    if engine_type in _NUMBER_TYPES or re.fullmatch(
        r'u?int(8|16|32|64|128|256)', engine_type
    ):
        return 'number'
    if engine_type in _BOOLEAN_TYPES:
        return 'boolean'
    if engine_type in _JSON_TYPES or engine_type.startswith((
        'array', 'list', 'set', 'map', 'tuple', 'nested', 'vector',
        'floatvector', 'binaryvector', 'sparsefloatvector',
    )):
        return 'json'
    if engine_type in _DATE_TYPES:
        return 'date'
    if engine_type in _DATETIME_TYPES:
        return 'datetime'
    return 'text'


def _sensitive(name: str, redacted: frozenset[str]) -> bool:
    lowered = name.lower()
    compact = re.sub(r'[^a-z0-9]+', '_', lowered).strip('_')
    parts = frozenset(part for part in compact.split('_') if part)
    return (
        lowered in redacted or compact in redacted or
        bool(parts.intersection(_SENSITIVE_PARTS)) or
        compact in _SENSITIVE_PARTS
    )


def normalize_columns(
    columns: Iterable[object] | None,
    records: Iterable[object] = (), *,
    redact_keys: Iterable[str] = (),
    read_only: bool = True,
) -> list[dict[str, Any]]:
    """Return stable, unique, serializable columns for the public grid API."""
    source = list(columns or ())
    records = list(records or ())
    if not source and records:
        if isinstance(records[0], Mapping):
            source = [{'name': str(name)} for name in records[0].keys()]
        else:
            source = [{
                'name': 'value',
                'cell_type': 'json' if isinstance(
                    records[0], (list, tuple)
                ) else 'text',
            }]
    redacted = frozenset(str(item).lower() for item in redact_keys)
    used: set[str] = set()
    normalized = []
    for ordinal, raw in enumerate(source):
        if isinstance(raw, str):
            column = {'name': raw}
        elif isinstance(raw, Mapping):
            column = copy.deepcopy(dict(raw))
        else:
            raise GridContractError('grid columns must be strings or objects')
        name_value = column.get('name', column.get('label'))
        if not isinstance(name_value, str) or not name_value.strip():
            name_value = f'column_{ordinal + 1}'
        name = name_value.strip()
        proposed = _identifier(column.get('key'), name)
        key = proposed
        suffix = 2
        while key in used:
            key = f'{proposed}#{suffix}'
            suffix += 1
        used.add(key)
        engine_type = _engine_type(column)
        sensitive = _sensitive(name, redacted) or _sensitive(key, redacted)
        editable = (
            bool(column.get('editable', not read_only)) and not read_only
        )
        normalized.append({
            **column,
            'key': key,
            'name': name,
            'identity_key': bool(column.get('key') is True or
                                 column.get('identity_key') is True),
            'ordinal': ordinal,
            'engine_type': engine_type,
            'cell_type': infer_cell_type(column),
            'editable': editable,
            'read_only': not editable,
            'sensitive': sensitive,
            'exportable': (
                bool(column.get('exportable', True)) and not sensitive
            ),
        })
    return normalized


def _gate(gate_id: str, passed: bool, evidence: str) -> dict[str, Any]:
    return {
        'gate_id': gate_id,
        'state': 'passed' if passed else 'blocked',
        'evidence': evidence,
    }


def workspace_grid_contract(context, provider, visual_admin=None):
    """Describe the grid/native-view contract mounted for one provider."""
    profile = getattr(provider, 'profile', None)
    model_family = (
        getattr(profile, 'model_family', None) or
        context.experience_family
    )
    result_kind = getattr(profile, 'result_kind', 'tabular')
    component = getattr(
        profile, 'result_component_reference',
        _NATIVE_VIEWS.get(result_kind, 'SchemaView/DataGridView'),
    )
    native_required = model_family in _NATIVE_VIEW_MODELS
    native_available = (
        isinstance(component, str) and bool(component.strip()) and
        (not native_required or component != 'SchemaView/DataGridView')
    )
    admin_available = (
        isinstance(visual_admin, Mapping) and
        isinstance(visual_admin.get('objects'), list) and
        bool(visual_admin['objects'])
    )
    gates = [
        _gate('public_grid_boundary', True, 'cdeadmin_ui/data/DataGrid'),
        _gate('stable_provider_columns', True,
              'provider result and row pages are normalized centrally'),
        _gate('accessible_grid_identity', True,
              'provider/profile grid IDs and accessible names are required'),
        _gate('interaction_authority', True,
              'client/provider sort, filter and paging modes are explicit'),
        _gate('mutation_lifecycle', admin_available,
              'provider validate/plan/apply and transaction state remain '
              'authoritative'),
        _gate('sensitive_export_policy', True,
              'sensitive columns are marked non-exportable'),
        _gate('persisted_layout_scope', True,
              'layout state is scoped by provider and profile'),
        _gate('native_model_view', not native_required or native_available,
              component if native_available else 'native view unavailable'),
        _gate('keyboard_and_screen_reader', True,
              'public grid and native views expose accessible names'),
    ]
    runtime_verified = context.runtime_verification_state == 'verified'
    return {
        'schema': GRID_WORKSPACE_SCHEMA,
        'provider_id': context.provider_id,
        'profile_id': context.profile_id,
        'model_family': model_family,
        'result_kind': result_kind,
        'component_reference': component,
        'grid_id_prefix': _identifier(
            f'{context.provider_id}/{context.profile_id}', 'provider/workspace'
        ),
        'selection_mode': (
            'range' if model_family in _TABULAR_MODELS else 'row'
        ),
        'native_view_required': native_required,
        'native_view_available': native_available,
        'adoption_state': (
            'passed' if all(item['state'] == 'passed' for item in gates)
            else 'blocked'
        ),
        'activation_gates': gates,
        'runtime_gate': _gate(
            'live_provider_verification', runtime_verified,
            context.runtime_verification_state,
        ),
    }


def result_grid_contract(
    result: Mapping[str, Any], component_reference: str,
    columns: Iterable[object], records: Iterable[object], *,
    redact_keys: Iterable[str] = (), read_only: bool = True,
) -> dict[str, Any]:
    """Build a data-bearing contract for one bounded result/page."""
    identity = result.get('identity', {})
    normalized = normalize_columns(
        columns, records, redact_keys=redact_keys, read_only=read_only,
    )
    complete = bool(result.get('complete'))
    continuation = result.get('continuation')
    mode = 'client' if complete and continuation is None else 'provider'
    return {
        'schema': GRID_RESULT_SCHEMA,
        'grid_id': _identifier(
            f"{identity.get('provider_id', 'provider')}/"
            f"{identity.get('profile_id', 'profile')}/"
            f"{result.get('result_kind', 'result')}",
            'provider/result',
        ),
        'accessible_name': (
            f"{result.get('result_kind', 'provider')} result grid"
        ),
        'columns': normalized,
        'selection_mode': (
            'range' if result.get('result_kind') in {
                'tabular', 'columnar', 'wide_column', 'cellset'
            } else 'row'
        ),
        'interactions': {
            'sort': mode, 'filter': mode, 'page': mode,
            'large_result_streaming': mode == 'provider',
            'cancellation': mode == 'provider',
        },
        'read_only': read_only,
        'component_reference': component_reference,
        'native_view': component_reference != 'SchemaView/DataGridView',
    }


def normalize_admin_page(
    page: Mapping[str, Any], context, provider, resource_kind: str,
) -> dict[str, Any]:
    """Add grid metadata without changing provider-owned native page data."""
    normalized_page = copy.deepcopy(dict(page))
    rows = (
        normalized_page.get('rows') or
        normalized_page.get('records') or
        normalized_page.get('documents') or []
    )
    values = [
        row.get('values', row) if isinstance(row, Mapping) else row
        for row in rows if isinstance(row, Mapping)
    ]
    columns = normalize_columns(
        normalized_page.get('columns'), values,
        read_only=not bool(normalized_page.get('editable')),
    )
    normalized_page['columns'] = columns
    profile = getattr(provider, 'profile', None)
    model_family = getattr(
        profile, 'model_family', context.experience_family
    )
    continuation = normalized_page.get('continuation')
    mode = 'provider' if continuation else 'client'
    transaction_actions = frozenset(getattr(
        getattr(provider, 'client', None), 'transaction_actions', ()
    ))
    normalized_page['grid'] = {
        'schema': GRID_RESULT_SCHEMA,
        'grid_id': _identifier(
            f'{context.provider_id}/{context.profile_id}/admin/'
            f'{resource_kind}',
            'provider/admin',
        ),
        'accessible_name': f'{resource_kind} data grid',
        'columns': copy.deepcopy(columns),
        'selection_mode': (
            'range' if model_family in _TABULAR_MODELS else 'row'
        ),
        'interactions': {
            'sort': mode, 'filter': mode, 'page': mode,
            'large_result_streaming': bool(continuation),
            'cancellation': bool(continuation),
        },
        'read_only': not bool(normalized_page.get('editable')),
        'mutation_lifecycle': {
            'preview': True,
            'validation': True,
            'commit': 'provider',
            'rollback': (
                'provider-session' if 'rollback' in transaction_actions
                else 'not-supported-by-provider'
            ),
            'safe_delete_confirmation': True,
        },
    }
    return normalized_page
