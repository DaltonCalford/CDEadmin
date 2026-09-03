##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Provider-neutral semantic-model contracts and validation."""

from __future__ import annotations

import copy
import json
import re
from typing import Any, Mapping


CONTRACT_VERSION = '1.0.0'
MODEL_STATUSES = frozenset({'draft', 'published', 'archived'})
AGGREGATIONS = frozenset({
    'sum', 'count', 'count_distinct', 'min', 'max', 'avg', 'none',
})
CALCULATION_OPERATORS = frozenset({
    'add', 'subtract', 'multiply', 'divide',
})
JOIN_TYPES = frozenset({'inner', 'left', 'right', 'full', 'cross'})
FILTER_OPERATORS = frozenset({
    'eq', 'ne', 'lt', 'lte', 'gt', 'gte', 'in', 'not_in', 'between',
    'is_null', 'is_not_null',
})
_ID = re.compile(r'^[A-Za-z][A-Za-z0-9_-]{0,127}$')


class SemanticModelError(RuntimeError):
    """A semantic-model request cannot be admitted safely."""


class SemanticModelConflict(SemanticModelError):
    """A model revision changed before a requested mutation."""


class SemanticCompilationUnavailable(SemanticModelError):
    """The endpoint provider has no semantic-query compiler."""


def required_text(value, label, maximum=512):
    if not isinstance(value, str) or not value.strip():
        raise SemanticModelError(f'{label} must not be empty')
    value = value.strip()
    if len(value) > maximum:
        raise SemanticModelError(f'{label} exceeds its length limit')
    return value


def identifier(value, label):
    value = required_text(value, label, 128)
    if not _ID.fullmatch(value):
        raise SemanticModelError(f'{label} is not a portable identifier')
    return value


def mapping(value, label):
    if not isinstance(value, Mapping):
        raise SemanticModelError(f'{label} must be an object')
    return copy.deepcopy(dict(value))


def array(value, label, maximum=500):
    if not isinstance(value, list):
        raise SemanticModelError(f'{label} must be an array')
    if len(value) > maximum:
        raise SemanticModelError(f'{label} exceeds its item limit')
    return copy.deepcopy(value)


def _unique(items, label):
    seen = set()
    for item in items:
        item_id = identifier(item.get('id'), f'{label}.id')
        if item_id in seen:
            raise SemanticModelError(f'{label} identifiers must be unique')
        seen.add(item_id)
    return seen


def validate_model(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and normalize a complete semantic model document."""
    model = mapping(value, 'model')
    model['contract_version'] = CONTRACT_VERSION
    model['name'] = required_text(model.get('name'), 'model.name', 256)
    model['description'] = str(model.get('description') or '')[:4096]
    sources = array(model.get('sources', []), 'model.sources', 64)
    if not sources:
        raise SemanticModelError('model.sources must contain a data source')
    source_ids = _unique(sources, 'model.sources')
    normalized_sources = []
    for source in sources:
        source = mapping(source, 'source')
        source['id'] = identifier(source['id'], 'source.id')
        source['resource_id'] = required_text(
            source.get('resource_id'), 'source.resource_id', 1024
        )
        source['relation'] = array(
            source.get('relation', []), 'source.relation', 8
        )
        if not source['relation'] or not all(
            isinstance(part, str) and part.strip()
            for part in source['relation']
        ):
            raise SemanticModelError(
                'source.relation must contain native identifier parts'
            )
        source['relation'] = [part.strip() for part in source['relation']]
        source['alias'] = identifier(
            source.get('alias', source['id']), 'source.alias'
        )
        normalized_sources.append(source)
    model['sources'] = normalized_sources

    joins = array(model.get('joins', []), 'model.joins', 128)
    _unique(joins, 'model.joins')
    for join in joins:
        identifier(join.get('id'), 'join.id')
        if join.get('left_source') not in source_ids or (
            join.get('right_source') not in source_ids
        ):
            raise SemanticModelError('join references an unknown source')
        if join.get('join_type', 'inner') not in JOIN_TYPES:
            raise SemanticModelError('join.join_type is unsupported')
        predicates = array(join.get('predicates', []), 'join.predicates', 32)
        if join.get('join_type', 'inner') != 'cross' and not predicates:
            raise SemanticModelError('non-cross joins require predicates')
        for predicate in predicates:
            _validate_field_reference(predicate.get('left'), source_ids)
            _validate_field_reference(predicate.get('right'), source_ids)
            if predicate.get('operator', 'eq') != 'eq':
                raise SemanticModelError(
                    'join predicate operator is unsupported'
                )
    model['joins'] = joins

    dimensions = array(model.get('dimensions', []), 'model.dimensions')
    dimension_ids = _unique(dimensions, 'model.dimensions')
    level_ids = set()
    for dimension in dimensions:
        identifier(dimension.get('id'), 'dimension.id')
        dimension['name'] = required_text(
            dimension.get('name'), 'dimension.name', 256
        )
        _validate_field_reference(dimension.get('field'), source_ids)
        hierarchies = array(
            dimension.get('hierarchies', []), 'dimension.hierarchies', 64
        )
        _unique(hierarchies, 'dimension.hierarchies')
        for hierarchy in hierarchies:
            hierarchy['name'] = required_text(
                hierarchy.get('name'), 'hierarchy.name', 256
            )
            levels = array(
                hierarchy.get('levels', []), 'hierarchy.levels', 64
            )
            if not levels:
                raise SemanticModelError('hierarchy.levels must not be empty')
            for level in levels:
                level_id = identifier(level.get('id'), 'level.id')
                if level_id in level_ids:
                    raise SemanticModelError(
                        'level identifiers must be unique'
                    )
                level_ids.add(level_id)
                level['name'] = required_text(level.get('name'), 'level.name')
                _validate_field_reference(level.get('field'), source_ids)
    model['dimensions'] = dimensions

    measures = array(model.get('measures', []), 'model.measures')
    measure_ids = _unique(measures, 'model.measures')
    for measure in measures:
        identifier(measure.get('id'), 'measure.id')
        measure['name'] = required_text(measure.get('name'), 'measure.name')
        aggregation = measure.get('aggregation', 'sum')
        if aggregation not in AGGREGATIONS:
            raise SemanticModelError('measure.aggregation is unsupported')
        measure['aggregation'] = aggregation
        expression = measure.get('expression')
        if expression is not None:
            measure['aggregation'] = 'none'
            measure['field'] = None
            _validate_expression(expression, measure_ids, measure['id'])
        elif aggregation == 'count' and measure.get('field') is None:
            measure['field'] = None
        else:
            _validate_field_reference(measure.get('field'), source_ids)
        measure['format'] = str(measure.get('format') or '')[:128]
    model['measures'] = measures

    model['default_filters'] = _validate_filters(
        model.get('default_filters', []), source_ids
    )
    materializations = array(
        model.get('materializations', []), 'model.materializations', 64
    )
    _unique(materializations, 'model.materializations')
    for materialization in materializations:
        materialization['name'] = required_text(
            materialization.get('name'), 'materialization.name'
        )
        materialization['strategy'] = identifier(
            materialization.get('strategy', 'provider_managed'),
            'materialization.strategy',
        )
        materialization['enabled'] = bool(
            materialization.get('enabled', False)
        )
    model['materializations'] = materializations
    model['security'] = mapping(model.get('security', {}), 'model.security')
    model['annotations'] = mapping(
        model.get('annotations', {}), 'model.annotations'
    )
    model['_symbols'] = {
        'source_ids': sorted(source_ids),
        'dimension_ids': sorted(dimension_ids),
        'level_ids': sorted(level_ids),
        'measure_ids': sorted(measure_ids),
    }
    encoded = json.dumps(
        model, ensure_ascii=False, separators=(',', ':'), default=repr
    ).encode('utf-8')
    if len(encoded) > 1024 * 1024:
        raise SemanticModelError('semantic model exceeds its byte limit')
    return model


def _validate_expression(value, measure_ids, current_id, depth=0):
    if depth > 16:
        raise SemanticModelError('calculated measure expression is too deep')
    value = mapping(value, 'calculated measure expression')
    if set(value) == {'measure'}:
        measure_id = identifier(value['measure'], 'expression.measure')
        if measure_id not in measure_ids or measure_id == current_id:
            raise SemanticModelError(
                'calculated measure references an unavailable measure'
            )
        return
    if set(value) == {'literal'}:
        if isinstance(value['literal'], bool) or not isinstance(
            value['literal'], (int, float)
        ):
            raise SemanticModelError(
                'calculated measure literal must be numeric'
            )
        return
    if set(value) != {'operator', 'left', 'right'} or (
        value['operator'] not in CALCULATION_OPERATORS
    ):
        raise SemanticModelError(
            'calculated measure expression node is unsupported'
        )
    _validate_expression(value['left'], measure_ids, current_id, depth + 1)
    _validate_expression(value['right'], measure_ids, current_id, depth + 1)


def _validate_field_reference(value, source_ids):
    value = mapping(value, 'field reference')
    if value.get('source_id') not in source_ids:
        raise SemanticModelError('field reference uses an unknown source')
    identifier(value.get('field'), 'field reference.field')


def _validate_filters(value, source_ids):
    filters = array(value, 'filters', 128)
    for item in filters:
        item = mapping(item, 'filter')
        _validate_field_reference(item.get('field'), source_ids)
        if item.get('operator', 'eq') not in FILTER_OPERATORS:
            raise SemanticModelError('filter.operator is unsupported')
    return filters


def validate_query(model, value):
    query = mapping(value, 'query')
    symbols = model['_symbols']
    axes = mapping(query.get('axes', {}), 'query.axes')
    admitted_levels = set(symbols['level_ids']) | set(symbols['dimension_ids'])
    for axis in ('rows', 'columns', 'pages'):
        items = array(axes.get(axis, []), f'query.axes.{axis}', 32)
        if any(item not in admitted_levels for item in items):
            raise SemanticModelError(f'query.axes.{axis} has an unknown level')
        axes[axis] = items
    selected_measures = array(query.get('measures', []), 'query.measures', 64)
    if not selected_measures:
        raise SemanticModelError('query.measures must not be empty')
    if any(item not in symbols['measure_ids'] for item in selected_measures):
        raise SemanticModelError('query.measures has an unknown measure')
    query['axes'] = axes
    query['measures'] = selected_measures
    query['filters'] = _validate_filters(
        query.get('filters', []), set(symbols['source_ids'])
    )
    query['totals'] = bool(query.get('totals', False))
    query['limit'] = query.get('limit', 500)
    if isinstance(query['limit'], bool) or not isinstance(query['limit'], int):
        raise SemanticModelError('query.limit must be an integer')
    if not 1 <= query['limit'] <= 10000:
        raise SemanticModelError('query.limit is outside platform limits')
    return query


def public_model(model):
    value = copy.deepcopy(model)
    value.pop('_symbols', None)
    return value
