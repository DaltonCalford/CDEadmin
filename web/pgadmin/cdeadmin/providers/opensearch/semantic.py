##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""OpenSearch-owned semantic model to Query DSL aggregation compiler."""

from __future__ import annotations

import copy
import json

from pgadmin.cdeadmin.semantic_models import (
    SemanticModelError, validate_model, validate_query,
)


def _filter(item, parameters):
    field = item['field']['field']
    value = item.get('value')
    if item.get('parameter_id') is not None:
        try:
            value = parameters[item['parameter_id']]
        except KeyError as exc:
            raise SemanticModelError(
                f'query parameter {item["parameter_id"]!r} has no value'
            ) from exc
    operation = item['operator']
    if operation == 'eq':
        return {'term': {field: value}}
    if operation == 'ne':
        return {'bool': {'must_not': [{'term': {field: value}}]}}
    if operation in {'lt', 'lte', 'gt', 'gte'}:
        return {'range': {field: {operation: value}}}
    if operation in {'in', 'not_in'}:
        clause = {'terms': {field: value}}
        return clause if operation == 'in' else {
            'bool': {'must_not': [clause]}
        }
    if operation == 'between':
        if not isinstance(value, list) or len(value) != 2:
            raise SemanticModelError('between filter requires two values')
        return {'range': {field: {'gte': value[0], 'lte': value[1]}}}
    if operation == 'is_null':
        return {'bool': {'must_not': [{'exists': {'field': field}}]}}
    if operation == 'is_not_null':
        return {'exists': {'field': field}}
    raise SemanticModelError('OpenSearch semantic filter is unsupported')


def _calculation(node, measures):
    paths = {}

    def visit(item):
        if 'measure' in item:
            name = item['measure']
            measure = measures[name]
            if measure.get('expression') is not None:
                return visit(measure['expression'])
            paths[name] = (
                '_count' if measure['aggregation'] == 'count' and
                measure.get('field') is None else name
            )
            return 'params.' + name
        if 'literal' in item:
            return str(item['literal'])
        operators = {
            'add': '+', 'subtract': '-', 'multiply': '*', 'divide': '/',
        }
        left = visit(item['left'])
        right = visit(item['right'])
        if item['operator'] == 'divide':
            return f'({right} == 0 ? null : {left} / {right})'
        return f'({left} {operators[item["operator"]]} {right})'

    return paths, visit(node)


def compile_opensearch_aggregation(model_value, query_value):
    """Compile one-index analytical intent to a composite aggregation."""
    model = validate_model(model_value)
    query = validate_query(model, query_value)
    if query['windows']:
        raise SemanticModelError(
            'OpenSearch does not admit generic analytical window operations'
        )
    if model['semantic_family'] != 'search':
        raise SemanticModelError('OpenSearch requires a search semantic model')
    if len(model['sources']) != 1 or model['joins'] or model['relationships']:
        raise SemanticModelError(
            'OpenSearch semantic execution currently requires one index'
        )
    if query['drill']['mode'] == 'through':
        raise SemanticModelError(
            'OpenSearch semantic drill-through requires a provider hit '
            'projection contract'
        )
    source = model['sources'][0]
    index = source['provider_config'].get('index') or source['relation'][-1]

    parameters = {
        item['id']: item['default'] for item in model['parameters']
        if 'default' in item
    }
    parameters.update(query['parameters'])
    filters = (
        model['default_filters'] + model['security']['row_filters'] +
        query['filters'] + query['cross_filters']
    )
    clauses = [_filter(item, parameters) for item in filters]
    time = query['time_intelligence']
    period_ranges = None
    if time:
        dimension = next(
            item for item in model['dimensions']
            if item['id'] == time['dimension_id']
        )
        time_field = dimension['field']['field']
        operation = time['operation']
        if operation == 'as_of':
            condition = {'lte': time['start']}
        elif operation == 'period_comparison':
            period_ranges = {
                'current': {'range': {time_field: {
                    'gte': time['start'], 'lte': time['end'],
                }}},
                'comparison': {'range': {time_field: {
                    'gte': time['comparison_start'],
                    'lte': time['comparison_end'],
                }}},
            }
            condition = None
        else:
            condition = {'gte': time['start'], 'lte': time['end']}
        if condition is not None:
            clauses.append({'range': {time_field: condition}})
    query_dsl = {'match_all': {}} if not clauses else {
        'bool': {'filter': clauses}
    }

    levels = {}
    for dimension in model['dimensions']:
        levels[dimension['id']] = dimension['field']
        for hierarchy in dimension['hierarchies']:
            for level in hierarchy['levels']:
                levels[level['id']] = level['field']
    axes = []
    for axis in ('pages', 'rows', 'columns'):
        for item in query['axes'][axis]:
            if item not in axes:
                axes.append(item)
    if query['drill']['mode'] == 'down' and query['drill'][
            'target_level'] not in axes:
        axes.append(query['drill']['target_level'])

    measures = {item['id']: item for item in model['measures']}

    def expression_dependencies(node):
        if 'measure' in node:
            dependency = measures[node['measure']]
            if dependency.get('expression') is not None:
                return expression_dependencies(dependency['expression'])
            return {node['measure']}
        if 'literal' in node:
            return set()
        return (
            expression_dependencies(node['left']) |
            expression_dependencies(node['right'])
        )

    selected = set(query['measures'])
    required_base = set()
    for measure_id in selected:
        expression = measures[measure_id].get('expression')
        if expression is None:
            required_base.add(measure_id)
        else:
            required_base.update(expression_dependencies(expression))
    aggregations = {}
    count_measures = []
    calculated = []
    for measure_id in sorted(selected | required_base):
        measure = measures[measure_id]
        if measure.get('expression') is not None:
            if measure_id in selected:
                calculated.append(measure)
            continue
        aggregation = measure['aggregation']
        if aggregation == 'count':
            if measure.get('field') is None:
                count_measures.append(measure_id)
            else:
                aggregations[measure_id] = {'value_count': {
                    'field': measure['field']['field'],
                }}
        elif aggregation == 'count_distinct':
            aggregations[measure_id] = {'cardinality': {
                'field': measure['field']['field'],
            }}
        elif aggregation in {'sum', 'min', 'max', 'avg'}:
            aggregations[measure_id] = {aggregation: {
                'field': measure['field']['field'],
            }}
        else:
            raise SemanticModelError(
                'OpenSearch semantic aggregation is unsupported'
            )
    for measure in calculated:
        paths, script = _calculation(measure['expression'], measures)
        aggregations[measure['id']] = {'bucket_script': {
            'buckets_path': paths, 'script': script,
        }}
    if axes:
        semantic_rows = {
            'composite': {
                'size': min(query['limit'], 10000),
                'sources': [{item: {'terms': {
                    'field': levels[item]['field'],
                }}} for item in axes],
            },
            'aggs': aggregations,
        }
    else:
        semantic_rows = {'filter': {'match_all': {}}, 'aggs': aggregations}
    if period_ranges is not None:
        semantic_rows = {
            'filters': {'filters': period_ranges},
            'aggs': {'period_values': semantic_rows},
        }
    native = {'size': 0, 'query': query_dsl,
              'aggs': {'semantic_rows': semantic_rows}}
    projection_levels = axes + (
        ['__semantic_period'] if period_ranges is not None else []
    )
    return {
        'contract_version': '1.0.0',
        'language_profile': 'opensearch-query-dsl',
        'source': json.dumps(
            native, ensure_ascii=False, separators=(',', ':')
        ),
        'parameters': {
            'index': index, 'semantic_axes': axes,
            'semantic_count_measures': count_measures,
        },
        'projection': {
            'axes': copy.deepcopy(query['axes']),
            'levels': projection_levels,
            'measures': copy.deepcopy(query['measures']),
            'drill': copy.deepcopy(query['drill']),
            'time_intelligence': copy.deepcopy(time),
            'windows': [],
        },
        'warnings': ([
            'OpenSearch composite aggregation does not emit SQL rollup totals.'
        ] if query['totals'] else []),
    }


__all__ = ('compile_opensearch_aggregation',)
