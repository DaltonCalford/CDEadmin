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

from .profiles import analytical_profile


CONTRACT_VERSION = '1.0.0'
MODEL_STATUSES = frozenset({'draft', 'published', 'archived'})
AGGREGATIONS = frozenset({
    'sum', 'count', 'count_distinct', 'min', 'max', 'avg', 'none',
})
CALCULATION_OPERATORS = frozenset({
    'add', 'subtract', 'multiply', 'divide',
})
JOIN_TYPES = frozenset({'inner', 'left', 'right', 'full', 'cross'})
SOURCE_CLASSIFICATIONS = frozenset({
    'fact', 'dimension', 'bridge', 'lookup', 'node-set',
    'relationship-set', 'path-set', 'projection', 'document-set',
    'embedded-array', 'event', 'indexed-document-set', 'data-stream',
    'search-view', 'vector-set', 'partition', 'metadata-set', 'measurement',
    'event-series', 'metric-series', 'dictionary', 'partitioned-table',
    'materialized-view', 'keyspace', 'data-structure', 'stream',
    'ordered-range', 'entity-history', 'transaction-set',
})
PARAMETER_TYPES = frozenset({
    'string', 'integer', 'number', 'boolean', 'date', 'datetime', 'array',
})
CARDINALITIES = frozenset({
    'one-to-one', 'one-to-many', 'many-to-one', 'many-to-many',
})
TIME_ROLES = frozenset({
    'event-time', 'processing-time', 'valid-time', 'system-time',
    'calendar-time', 'fiscal-time',
})
CERTIFICATION_STATUSES = frozenset({
    'uncertified', 'candidate', 'certified', 'deprecated',
})
CHART_TYPES = frozenset({
    'table', 'pivot', 'bar', 'line', 'area', 'scatter', 'pie', 'metric',
    'histogram', 'timeline', 'graph', 'vector-neighbors',
})
EXPORT_FORMATS = frozenset({
    'csv', 'json', 'jsonl',
})
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
        if not isinstance(item, Mapping):
            raise SemanticModelError(f'{label} items must be objects')
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
    model['semantic_family'] = identifier(
        model.get('semantic_family', 'relational'), 'model.semantic_family'
    )
    profile = analytical_profile(model['semantic_family'])
    if not profile['recognized_model_family']:
        raise SemanticModelError('model.semantic_family is unsupported')
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
        source['source_kind'] = identifier(
            source.get('source_kind', profile['source_kinds'][0]),
            'source.source_kind'
        )
        if source['source_kind'] not in profile['source_kinds']:
            raise SemanticModelError('source.source_kind is unsupported')
        classification = source.get(
            'classification', profile['source_classifications'][0]
        )
        if classification not in SOURCE_CLASSIFICATIONS or (
                classification not in profile['source_classifications']):
            raise SemanticModelError(
                'source.classification is unsupported'
            )
        source['classification'] = classification
        grain = array(source.get('grain', []), 'source.grain', 32)
        for reference in grain:
            _validate_field_reference(reference, source_ids)
        source['grain'] = grain
        source['provider_config'] = mapping(
            source.get('provider_config', {}), 'source.provider_config'
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
        cardinality = join.get('cardinality', 'many-to-one')
        if cardinality not in CARDINALITIES:
            raise SemanticModelError('join.cardinality is unsupported')
        join['cardinality'] = cardinality
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

    relationships = array(
        model.get('relationships', []), 'model.relationships', 256
    )
    _unique(relationships, 'model.relationships')
    for relationship in relationships:
        relationship['name'] = required_text(
            relationship.get('name'), 'relationship.name', 256
        )
        if relationship.get('from_source') not in source_ids or (
                relationship.get('to_source') not in source_ids):
            raise SemanticModelError(
                'relationship references an unknown source'
            )
        relationship['relationship_kind'] = identifier(
            relationship.get(
                'relationship_kind', profile['relationship_kinds'][0]
            ),
            'relationship.relationship_kind',
        )
        if relationship['relationship_kind'] not in profile[
                'relationship_kinds']:
            raise SemanticModelError(
                'relationship.relationship_kind is unsupported'
            )
        relationship['provider_config'] = mapping(
            relationship.get('provider_config', {}),
            'relationship.provider_config',
        )
    model['relationships'] = relationships

    dimensions = array(model.get('dimensions', []), 'model.dimensions')
    dimension_ids = _unique(dimensions, 'model.dimensions')
    level_ids = set()
    for dimension in dimensions:
        identifier(dimension.get('id'), 'dimension.id')
        dimension['name'] = required_text(
            dimension.get('name'), 'dimension.name', 256
        )
        dimension['dimension_kind'] = identifier(
            dimension.get('dimension_kind', profile['dimension_kinds'][0]),
            'dimension.dimension_kind',
        )
        if dimension['dimension_kind'] not in profile['dimension_kinds']:
            raise SemanticModelError('dimension.dimension_kind is unsupported')
        _validate_field_reference(dimension.get('field'), source_ids)
        time = dimension.get('time_intelligence')
        if time is not None:
            time = mapping(time, 'dimension.time_intelligence')
            if time.get('role', 'calendar-time') not in TIME_ROLES:
                raise SemanticModelError(
                    'dimension time-intelligence role is unsupported'
                )
            time['role'] = time.get('role', 'calendar-time')
            time['calendar'] = identifier(
                time.get('calendar', 'gregorian'),
                'dimension.time_intelligence.calendar',
            )
            time['timezone'] = required_text(
                time.get('timezone', 'UTC'),
                'dimension.time_intelligence.timezone', 128,
            )
            fiscal_month = time.get('fiscal_year_start_month', 1)
            if isinstance(fiscal_month, bool) or not isinstance(
                    fiscal_month, int) or not 1 <= fiscal_month <= 12:
                raise SemanticModelError(
                    'fiscal year start month must be between 1 and 12'
                )
            time['fiscal_year_start_month'] = fiscal_month
        dimension['time_intelligence'] = time
        dimension['provider_config'] = mapping(
            dimension.get('provider_config', {}),
            'dimension.provider_config',
        )
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
        measure['measure_kind'] = identifier(
            measure.get('measure_kind', (
                'calculated' if expression is not None else
                profile['measure_kinds'][0]
            )), 'measure.measure_kind'
        )
        if measure['measure_kind'] not in profile['measure_kinds'] and not (
                expression is not None and
                measure['measure_kind'] == 'calculated'):
            raise SemanticModelError('measure.measure_kind is unsupported')
        certification = mapping(
            measure.get('certification', {}), 'measure.certification'
        )
        status = certification.get('status', 'uncertified')
        if status not in CERTIFICATION_STATUSES:
            raise SemanticModelError(
                'measure certification status is unsupported'
            )
        certification['status'] = status
        certification['owner'] = str(
            certification.get('owner') or ''
        )[:256]
        certification['definition'] = str(
            certification.get('definition') or ''
        )[:4096]
        measure['certification'] = certification
    dependencies = {
        item['id']: _expression_references(item.get('expression'))
        for item in measures
    }
    visited = set()
    visiting = set()

    def visit_measure(measure_id):
        if measure_id in visiting:
            raise SemanticModelError('calculated measure cycle is unsupported')
        if measure_id in visited:
            return
        visiting.add(measure_id)
        for dependency in dependencies[measure_id]:
            visit_measure(dependency)
        visiting.remove(measure_id)
        visited.add(measure_id)

    for measure_id in measure_ids:
        visit_measure(measure_id)
    model['measures'] = measures

    parameters = array(model.get('parameters', []), 'model.parameters', 128)
    parameter_ids = _unique(parameters, 'model.parameters')
    for parameter in parameters:
        parameter['name'] = required_text(
            parameter.get('name'), 'parameter.name', 256
        )
        parameter_type = parameter.get('type', 'string')
        if parameter_type not in PARAMETER_TYPES:
            raise SemanticModelError('parameter.type is unsupported')
        parameter['type'] = parameter_type
        parameter['required'] = bool(parameter.get('required', False))
        parameter['allowed_values'] = array(
            parameter.get('allowed_values', []),
            'parameter.allowed_values', 500,
        )
        for allowed in parameter['allowed_values']:
            _validate_parameter_value(allowed, parameter_type)
        if 'default' in parameter:
            _validate_parameter_value(parameter['default'], parameter_type)
            if parameter['allowed_values'] and parameter['default'] not in (
                    parameter['allowed_values']):
                raise SemanticModelError(
                    'parameter.default is not an allowed value'
                )
    model['parameters'] = parameters

    parameter_definitions = {item['id']: item for item in parameters}
    model['default_filters'] = _validate_filters(
        model.get('default_filters', []), source_ids, parameter_definitions
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
    security = mapping(model.get('security', {}), 'model.security')
    security['row_filters'] = _validate_filters(
        security.get('row_filters', []), source_ids, parameter_definitions
    )
    tenant = security.get('tenant_filter')
    if tenant is not None:
        tenant = mapping(tenant, 'security.tenant_filter')
        _validate_field_reference(tenant.get('field'), source_ids)
        tenant['principal_claim'] = identifier(
            tenant.get('principal_claim', 'tenant_id'),
            'security.tenant_filter.principal_claim',
        )
        tenant['required'] = bool(tenant.get('required', True))
    security['tenant_filter'] = tenant
    roles = array(security.get('roles', []), 'security.roles', 128)
    _unique(roles, 'security.roles')
    for role in roles:
        role['name'] = required_text(role.get('name'), 'security.role.name')
        role['principal_claim'] = identifier(
            role.get('principal_claim', 'role'),
            'security.role.principal_claim',
        )
        role['filters'] = _validate_filters(
            role.get('filters', []), source_ids, parameter_definitions
        )
    security['roles'] = roles
    model['security'] = security

    visualizations = array(
        model.get('visualizations', []), 'model.visualizations', 256
    )
    visualization_ids = _unique(visualizations, 'model.visualizations')
    for chart in visualizations:
        if not isinstance(chart, Mapping):
            raise SemanticModelError('visualization must be an object')
        chart['name'] = required_text(chart.get('name'), 'visualization.name')
        if chart.get('chart_type', 'table') not in CHART_TYPES:
            raise SemanticModelError('visualization.chart_type is unsupported')
        chart['chart_type'] = chart.get('chart_type', 'table')
        chart['query'] = mapping(chart.get('query', {}), 'visualization.query')
        chart['encodings'] = mapping(
            chart.get('encodings', {}), 'visualization.encodings'
        )
    model['visualizations'] = visualizations

    dashboards = array(model.get('dashboards', []), 'model.dashboards', 64)
    dashboard_ids = _unique(dashboards, 'model.dashboards')
    for dashboard in dashboards:
        dashboard['name'] = required_text(
            dashboard.get('name'), 'dashboard.name'
        )
        tiles = array(dashboard.get('tiles', []), 'dashboard.tiles', 256)
        normalized_tiles = []
        for tile in tiles:
            tile = mapping(tile, 'dashboard.tile')
            if tile.get('visualization_id') not in visualization_ids:
                raise SemanticModelError(
                    'dashboard tile references an unknown visualization'
                )
            tile['layout'] = mapping(
                tile.get('layout', {}), 'dashboard.tile.layout'
            )
            normalized_tiles.append(tile)
        dashboard['tiles'] = normalized_tiles
        dashboard['cross_filtering'] = bool(
            dashboard.get('cross_filtering', True)
        )
    model['dashboards'] = dashboards

    schedules = array(model.get('schedules', []), 'model.schedules', 64)
    schedule_ids = _unique(schedules, 'model.schedules')
    for schedule in schedules:
        schedule['name'] = required_text(
            schedule.get('name'), 'schedule.name'
        )
        schedule['expression'] = required_text(
            schedule.get('expression'), 'schedule.expression', 256
        )
        schedule['timezone'] = required_text(
            schedule.get('timezone', 'UTC'), 'schedule.timezone', 128
        )
        schedule['enabled'] = bool(schedule.get('enabled', False))
        schedule['delivery'] = mapping(
            schedule.get('delivery', {}), 'schedule.delivery'
        )
    model['schedules'] = schedules

    reports = array(model.get('reports', []), 'model.reports', 128)
    _unique(reports, 'model.reports')
    for report in reports:
        report['name'] = required_text(report.get('name'), 'report.name')
        dashboard_id = report.get('dashboard_id')
        if dashboard_id is not None and dashboard_id not in dashboard_ids:
            raise SemanticModelError(
                'report references an unknown dashboard'
            )
        schedule_id = report.get('schedule_id')
        if schedule_id is not None and schedule_id not in schedule_ids:
            raise SemanticModelError(
                'report references an unknown schedule'
            )
        formats = array(
            report.get('export_formats', ['json']),
            'report.export_formats', 8,
        )
        if not formats or any(item not in EXPORT_FORMATS for item in formats):
            raise SemanticModelError('report export format is unsupported')
        report['export_formats'] = formats
        report['parameters'] = mapping(
            report.get('parameters', {}), 'report.parameters'
        )
    model['reports'] = reports
    model['annotations'] = mapping(
        model.get('annotations', {}), 'model.annotations'
    )
    model['_symbols'] = {
        'source_ids': sorted(source_ids),
        'dimension_ids': sorted(dimension_ids),
        'level_ids': sorted(level_ids),
        'measure_ids': sorted(measure_ids),
        'parameter_ids': sorted(parameter_ids),
        'visualization_ids': sorted(visualization_ids),
        'dashboard_ids': sorted(dashboard_ids),
        'schedule_ids': sorted(schedule_ids),
    }
    for chart in model['visualizations']:
        chart['query'] = validate_query(model, chart['query'])
    for report in model['reports']:
        for parameter_id, parameter_value in report['parameters'].items():
            if parameter_id not in parameter_ids:
                raise SemanticModelError(
                    'report contains an unknown parameter'
                )
            parameter = next(
                item for item in model['parameters']
                if item['id'] == parameter_id
            )
            _validate_parameter_value(parameter_value, parameter['type'])
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


def _expression_references(value):
    if value is None or 'literal' in value:
        return set()
    if 'measure' in value:
        return {value['measure']}
    return (
        _expression_references(value['left']) |
        _expression_references(value['right'])
    )


def _validate_parameter_value(value, parameter_type):
    """Enforce declared parameter types without coercing caller input."""
    valid = {
        'string': lambda item: isinstance(item, str),
        'date': lambda item: isinstance(item, str) and bool(item.strip()),
        'datetime': lambda item: isinstance(item, str) and bool(item.strip()),
        'integer': lambda item: isinstance(item, int) and not isinstance(
            item, bool
        ),
        'number': lambda item: (
            isinstance(item, (int, float)) and not isinstance(item, bool)
        ),
        'boolean': lambda item: isinstance(item, bool),
        'array': lambda item: isinstance(item, list),
    }[parameter_type]
    if not valid(value):
        raise SemanticModelError(
            f'parameter value does not match type {parameter_type}'
        )


def _validate_field_reference(value, source_ids):
    value = mapping(value, 'field reference')
    if value.get('source_id') not in source_ids:
        raise SemanticModelError('field reference uses an unknown source')
    field = required_text(value.get('field'), 'field reference.field', 512)
    if '\x00' in field:
        raise SemanticModelError('field reference contains a null character')


def _validate_filters(value, source_ids, parameters=None):
    parameters = parameters or {}
    parameter_ids = set(parameters)
    filters = array(value, 'filters', 128)
    normalized = []
    for raw in filters:
        item = mapping(raw, 'filter')
        _validate_field_reference(item.get('field'), source_ids)
        operator = item.get('operator', 'eq')
        if operator not in FILTER_OPERATORS:
            raise SemanticModelError('filter.operator is unsupported')
        item['operator'] = operator
        parameter_id = item.get('parameter_id')
        if parameter_id is not None:
            if operator in {'is_null', 'is_not_null'}:
                raise SemanticModelError(
                    f'{operator} filter must not use a parameter'
                )
            if parameter_id not in parameter_ids:
                raise SemanticModelError(
                    'filter references an unknown parameter'
                )
            if 'value' in item:
                raise SemanticModelError(
                    'filter cannot contain both value and parameter_id'
                )
            if operator in {'in', 'not_in', 'between'} and parameters[
                    parameter_id]['type'] != 'array':
                raise SemanticModelError(
                    f'{operator} filter parameter must have array type'
                )
        elif operator in {'is_null', 'is_not_null'}:
            if 'value' in item:
                raise SemanticModelError(
                    f'{operator} filter must not contain a value'
                )
        elif 'value' not in item:
            raise SemanticModelError('filter requires a value or parameter')
        elif operator in {'in', 'not_in'} and not isinstance(
                item['value'], list):
            raise SemanticModelError(f'{operator} filter requires an array')
        elif operator == 'between' and (
                not isinstance(item['value'], list) or
                len(item['value']) != 2):
            raise SemanticModelError(
                'between filter requires an array of two values'
            )
        normalized.append(item)
    return normalized


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
        query.get('filters', []), set(symbols['source_ids']),
        {item['id']: item for item in model['parameters']}
    )
    query['cross_filters'] = _validate_filters(
        query.get('cross_filters', []), set(symbols['source_ids']),
        {item['id']: item for item in model['parameters']}
    )
    parameters = mapping(query.get('parameters', {}), 'query.parameters')
    if set(parameters).difference(symbols['parameter_ids']):
        raise SemanticModelError('query contains an unknown parameter')
    definitions = {item['id']: item for item in model['parameters']}
    for parameter_id, parameter_value in parameters.items():
        definition = definitions[parameter_id]
        _validate_parameter_value(parameter_value, definition['type'])
        if definition['allowed_values'] and parameter_value not in (
                definition['allowed_values']):
            raise SemanticModelError('query parameter value is not allowed')
    required_parameters = {
        item['id'] for item in model['parameters']
        if item['required'] and 'default' not in item
    }
    if required_parameters.difference(parameters):
        raise SemanticModelError('query omits a required parameter')
    query['parameters'] = parameters
    drill = mapping(query.get('drill', {}), 'query.drill')
    mode = drill.get('mode', 'summary')
    if mode not in {'summary', 'down', 'through'}:
        raise SemanticModelError('query drill mode is unsupported')
    target = drill.get('target_level')
    if mode == 'down' and target is None:
        raise SemanticModelError(
            'drill-down requires a target level'
        )
    if target is not None and target not in admitted_levels:
        raise SemanticModelError('query drill target is unknown')
    detail_fields = array(
        drill.get('detail_fields', []), 'query.drill.detail_fields', 64
    )
    for reference in detail_fields:
        _validate_field_reference(reference, set(symbols['source_ids']))
    drill['mode'] = mode
    drill['target_level'] = target
    drill['detail_fields'] = detail_fields
    query['drill'] = drill
    time_intelligence = mapping(
        query.get('time_intelligence', {}), 'query.time_intelligence'
    )
    if time_intelligence:
        dimension_id = time_intelligence.get('dimension_id')
        dimensions = {item['id']: item for item in model['dimensions']}
        if dimension_id not in dimensions or not dimensions[
                dimension_id].get('time_intelligence'):
            raise SemanticModelError(
                'query time intelligence requires a declared time dimension'
            )
        operation = time_intelligence.get('operation')
        if operation not in {'as_of', 'range'}:
            raise SemanticModelError(
                'query time-intelligence operation is unsupported'
            )
        time_intelligence['start'] = required_text(
            time_intelligence.get('start'),
            'query.time_intelligence.start', 128,
        )
        if operation == 'range':
            time_intelligence['end'] = required_text(
                time_intelligence.get('end'),
                'query.time_intelligence.end', 128,
            )
        else:
            time_intelligence.pop('end', None)
    query['time_intelligence'] = time_intelligence
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
