##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Load and validate declarative engine-experience administration catalogs."""

from __future__ import annotations

import copy
import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

from .experience import enrich_engine_experience


CATALOG_PATH = Path(__file__).with_name('portfolio_catalog.json')
PORTFOLIO_ENGINE_IDS = (
    'apache_ignite', 'cassandra', 'clickhouse', 'cockroachdb', 'dolt',
    'duckdb', 'firebird', 'foundationdb', 'immudb', 'influxdb', 'mariadb',
    'milvus', 'mongodb', 'mysql', 'neo4j', 'opensearch',
    'opensearch_sql_ppl', 'postgresql', 'redis', 'scratchbird', 'sqlite',
    'tidb', 'tikv', 'vitess', 'xtdb', 'yugabytedb',
)
MUTATION_CLASSES = frozenset({
    'none', 'read', 'write', 'admin', 'destructive',
})
CONTROL_TYPES = frozenset({
    'text', 'number', 'boolean', 'select', 'multiline', 'code', 'json',
    'secret-reference', 'password', 'multiselect',
})


class VisualAdminCatalogError(RuntimeError):
    """The checked-in visual administration catalog is invalid."""


def _required_string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise VisualAdminCatalogError(f'{label} must not be empty')
    return value.strip()


def _unique(items, label):
    values = list(items)
    if len(values) != len(set(values)):
        raise VisualAdminCatalogError(f'{label} must contain unique values')
    return values


def _validate_field(field: Mapping[str, Any], label: str) -> dict[str, Any]:
    value = copy.deepcopy(dict(field))
    field_id = _required_string(value.get('field_id'), f'{label}.field_id')
    _required_string(value.get('label'), f'{label}.label')
    control = _required_string(value.get('control'), f'{label}.control')
    if control not in CONTROL_TYPES:
        raise VisualAdminCatalogError(f'{label}.control is unknown')
    if control in {'select', 'multiselect'}:
        options = value.get('options')
        if not isinstance(options, list) or not options:
            raise VisualAdminCatalogError(
                f'{label}.options must not be empty for a choice control'
            )
        option_values = []
        for option in options:
            if not isinstance(option, Mapping):
                raise VisualAdminCatalogError(f'{label}.options is invalid')
            option_values.append(_required_string(
                option.get('value'), f'{label}.option.value'
            ))
            _required_string(option.get('label'), f'{label}.option.label')
        _unique(option_values, f'{label}.options')
    value['field_id'] = field_id
    value['required'] = bool(value.get('required', False))
    return value


def _validate_document(document: Mapping[str, Any]) -> dict[str, Any]:
    if document.get('schema') != 'cdeadmin.visual-admin.catalog.v1':
        raise VisualAdminCatalogError('catalog schema is unsupported')
    profiles = document.get('operation_profiles')
    forms = document.get('forms')
    engines = document.get('engines')
    if not isinstance(profiles, Mapping) or not isinstance(forms, Mapping):
        raise VisualAdminCatalogError(
            'catalog profiles and forms are required'
        )
    if not isinstance(engines, list):
        raise VisualAdminCatalogError('catalog engines must be an array')

    normalized_forms = {}
    for form_id, form in forms.items():
        _required_string(form_id, 'form_id')
        if not isinstance(form, Mapping):
            raise VisualAdminCatalogError(f'form {form_id} must be an object')
        fields = form.get('fields')
        if not isinstance(fields, list):
            raise VisualAdminCatalogError(f'form {form_id}.fields is invalid')
        admitted = [
            _validate_field(field, f'form {form_id}') for field in fields
        ]
        _unique((field['field_id'] for field in admitted), f'form {form_id}')
        normalized_forms[form_id] = {
            'form_id': form_id,
            'title': _required_string(form.get('title'), f'form {form_id}'),
            'fields': admitted,
        }

    normalized_profiles = {}
    for profile_id, operations in profiles.items():
        if not isinstance(operations, list) or not operations:
            raise VisualAdminCatalogError(
                f'operation profile {profile_id} must not be empty'
            )
        admitted = []
        for operation in operations:
            if not isinstance(operation, Mapping):
                raise VisualAdminCatalogError(
                    f'operation profile {profile_id} is invalid'
                )
            item = copy.deepcopy(dict(operation))
            operation_id = _required_string(
                item.get('operation_id'), f'{profile_id}.operation_id'
            )
            _required_string(item.get('title'), f'{profile_id}.title')
            mutation = _required_string(
                item.get('mutation_class'), f'{profile_id}.mutation_class'
            )
            if mutation not in MUTATION_CLASSES:
                raise VisualAdminCatalogError(
                    f'{profile_id}.{operation_id} mutation class is unknown'
                )
            form_id = _required_string(
                item.get('form_id'), f'{profile_id}.{operation_id}.form_id'
            )
            if form_id not in normalized_forms:
                raise VisualAdminCatalogError(
                    f'{profile_id}.{operation_id} references an unknown form'
                )
            item['operation_id'] = operation_id
            item['target_required'] = bool(
                item.get('target_required', operation_id != 'create')
            )
            item['confirmation_required'] = bool(
                item.get('confirmation_required', mutation == 'destructive')
            )
            admitted.append(item)
        _unique(
            (item['operation_id'] for item in admitted),
            f'operation profile {profile_id}',
        )
        normalized_profiles[profile_id] = admitted

    normalized_engines = {}
    for engine in engines:
        if not isinstance(engine, Mapping):
            raise VisualAdminCatalogError('engine catalog entry is invalid')
        item = copy.deepcopy(dict(engine))
        engine_id = _required_string(item.get('engine_id'), 'engine_id')
        objects = item.get('objects')
        if not isinstance(objects, list) or not objects:
            raise VisualAdminCatalogError(
                f'{engine_id}.objects must not be empty'
            )
        admitted_objects = []
        for descriptor in objects:
            if not isinstance(descriptor, Mapping):
                raise VisualAdminCatalogError(
                    f'{engine_id}.objects contains an invalid descriptor'
                )
            resource = copy.deepcopy(dict(descriptor))
            kind = _required_string(
                resource.get('resource_kind'), f'{engine_id}.resource_kind'
            )
            profile_id = _required_string(
                resource.get('operation_profile'),
                f'{engine_id}.{kind}.operation_profile',
            )
            if profile_id not in normalized_profiles:
                raise VisualAdminCatalogError(
                    f'{engine_id}.{kind} has an unknown operation profile'
                )
            resource['resource_kind'] = kind
            resource['title'] = _required_string(
                resource.get('title'), f'{engine_id}.{kind}.title'
            )
            resource['operations'] = []
            excluded = frozenset(resource.pop('exclude_operations', ()))
            for operation in normalized_profiles[profile_id]:
                if operation['operation_id'] in excluded:
                    continue
                expanded = copy.deepcopy(operation)
                expanded['form'] = copy.deepcopy(
                    normalized_forms[expanded['form_id']]
                )
                resource['operations'].append(expanded)
            admitted_objects.append(resource)
        _unique(
            (resource['resource_kind'] for resource in admitted_objects),
            f'{engine_id}.objects',
        )
        item['objects'] = admitted_objects
        item['experience_type'] = item.get(
            'experience_type', 'reference-engine'
        )
        normalized_engines[engine_id] = item

    expected = set(PORTFOLIO_ENGINE_IDS)
    actual = set(normalized_engines)
    if actual != expected:
        missing = ', '.join(sorted(expected.difference(actual)))
        extra = ', '.join(sorted(actual.difference(expected)))
        raise VisualAdminCatalogError(
            f'portfolio mismatch; missing=[{missing}] extra=[{extra}]'
        )
    return {
        'schema': document['schema'],
        'catalog_version': _required_string(
            document.get('catalog_version'), 'catalog_version'
        ),
        'engines': normalized_engines,
    }


@lru_cache(maxsize=1)
def _catalog_document() -> dict[str, Any]:
    try:
        document = json.loads(CATALOG_PATH.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError) as exc:
        raise VisualAdminCatalogError(
            'visual administration catalog cannot be loaded'
        ) from exc
    if not isinstance(document, Mapping):
        raise VisualAdminCatalogError('catalog root must be an object')
    return _validate_document(document)


def catalog_for_engine(engine_id: str) -> dict[str, Any]:
    """Return one isolated, fully expanded engine-experience catalog."""
    try:
        engine = _catalog_document()['engines'][engine_id]
    except KeyError as exc:
        raise VisualAdminCatalogError(
            f'no visual administration catalog exists for {engine_id!r}'
        ) from exc
    return enrich_engine_experience({
        'schema': 'cdeadmin.visual-admin.descriptor.v1',
        'catalog_version': _catalog_document()['catalog_version'],
        **copy.deepcopy(engine),
    })


def portfolio_summary() -> dict[str, Any]:
    """Return deterministic portfolio coverage for gates and reports."""
    document = _catalog_document()
    return {
        'schema': document['schema'],
        'catalog_version': document['catalog_version'],
        'engine_count': len(document['engines']),
        'reference_engine_count': sum(
            item['experience_type'] == 'reference-engine'
            for item in document['engines'].values()
        ),
        'native_engine_count': sum(
            item['experience_type'] == 'native-engine'
            for item in document['engines'].values()
        ),
        'engine_ids': sorted(document['engines']),
        'object_count': sum(
            len(item['objects']) for item in document['engines'].values()
        ),
        'operation_count': sum(
            len(resource['operations'])
            for item in document['engines'].values()
            for resource in item['objects']
        ),
    }
