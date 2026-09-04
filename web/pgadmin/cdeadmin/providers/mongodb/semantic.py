##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""MongoDB-owned semantic model to aggregation-pipeline compiler."""

from __future__ import annotations

import copy
import json

from pgadmin.cdeadmin.semantic_models import (
    SemanticModelError, validate_model, validate_query,
)


def _parameter_values(model, query):
    values = {
        item['id']: item['default'] for item in model['parameters']
        if 'default' in item
    }
    values.update(query['parameters'])
    return values


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
    operator = item['operator']
    if operator == 'eq':
        return {field: value}
    operators = {
        'ne': '$ne', 'lt': '$lt', 'lte': '$lte', 'gt': '$gt',
        'gte': '$gte', 'in': '$in', 'not_in': '$nin',
    }
    if operator in operators:
        return {field: {operators[operator]: value}}
    if operator == 'between':
        if not isinstance(value, list) or len(value) != 2:
            raise SemanticModelError('between filter requires two values')
        return {field: {'$gte': value[0], '$lte': value[1]}}
    if operator == 'is_null':
        return {field: None}
    if operator == 'is_not_null':
        return {field: {'$ne': None}}
    raise SemanticModelError('MongoDB semantic filter is unsupported')


def _calculation(node, measures):
    if 'measure' in node:
        measure = measures[node['measure']]
        if measure.get('expression') is not None:
            return _calculation(measure['expression'], measures)
        return '$' + node['measure']
    if 'literal' in node:
        return node['literal']
    operators = {
        'add': '$add', 'subtract': '$subtract',
        'multiply': '$multiply', 'divide': '$divide',
    }
    left = _calculation(node['left'], measures)
    right = _calculation(node['right'], measures)
    if node['operator'] == 'divide':
        return {'$cond': [
            {'$eq': [right, 0]}, None, {'$divide': [left, right]},
        ]}
    return {operators[node['operator']]: [left, right]}


def compile_mongodb_aggregation(model_value, query_value):
    """Compile one collection model to a bounded native pipeline."""
    model = validate_model(model_value)
    query = validate_query(model, query_value)
    if model['semantic_family'] != 'document':
        raise SemanticModelError('MongoDB requires a document semantic model')
    if len(model['sources']) != 1 or model['joins'] or model['relationships']:
        raise SemanticModelError(
            'MongoDB semantic execution currently requires one collection'
        )
    source = model['sources'][0]
    config = source['provider_config']
    relation = source['relation']
    database = config.get('database')
    collection = config.get('collection')
    if not database and len(relation) >= 2:
        database = relation[-2]
    if not collection and relation:
        collection = relation[-1]
    if not database or not collection:
        raise SemanticModelError(
            'MongoDB source must identify a database and collection'
        )
    if query['drill']['mode'] == 'through':
        raise SemanticModelError(
            'MongoDB semantic drill-through requires a provider detail '
            'projection contract'
        )

    parameters = _parameter_values(model, query)
    filters = (
        model['default_filters'] + model['security']['row_filters'] +
        query['filters'] + query['cross_filters']
    )
    matches = [_filter(item, parameters) for item in filters]
    time = query['time_intelligence']
    if time:
        dimension = next(
            item for item in model['dimensions']
            if item['id'] == time['dimension_id']
        )
        time_field = dimension['field']['field']
        if time['operation'] == 'as_of':
            condition = {'$lte': time['start']}
        else:
            condition = {'$gte': time['start'], '$lte': time['end']}
        matches.append({time_field: condition})

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

    group = {'_id': {
        item: '$' + levels[item]['field'] for item in axes
    } if axes else None}
    projection = {'_id': 0}
    projection.update({item: '$_id.' + item for item in axes})
    measures = {item['id']: item for item in model['measures']}

    def base_dependencies(measure_id):
        measure = measures[measure_id]
        expression = measure.get('expression')
        if expression is None:
            return {measure_id}
        if 'measure' in expression:
            return base_dependencies(expression['measure'])
        if 'literal' in expression:
            return set()
        return (
            expression_dependencies(expression['left']) |
            expression_dependencies(expression['right'])
        )

    def expression_dependencies(node):
        if 'measure' in node:
            return base_dependencies(node['measure'])
        if 'literal' in node:
            return set()
        return (
            expression_dependencies(node['left']) |
            expression_dependencies(node['right'])
        )

    selected = set(query['measures'])
    required_base = set().union(*(
        base_dependencies(item) for item in selected
    ))
    calculated = []
    for measure in model['measures']:
        if measure['id'] not in selected and measure[
                'id'] not in required_base:
            continue
        measure_id = measure['id']
        if measure.get('expression') is not None:
            calculated.append(measure)
            continue
        aggregation = measure['aggregation']
        if aggregation == 'count':
            if measure.get('field') is None:
                group[measure_id] = {'$sum': 1}
            else:
                operand = '$' + measure['field']['field']
                group[measure_id] = {'$sum': {'$cond': [{'$and': [
                    {'$ne': [{'$type': operand}, 'missing']},
                    {'$ne': [operand, None]},
                ]}, 1, 0]}}
            projection[measure_id] = 1
            continue
        operand = '$' + measure['field']['field']
        if aggregation == 'count_distinct':
            temporary = '__distinct_' + measure_id
            group[temporary] = {'$addToSet': operand}
            projection[measure_id] = {'$size': '$' + temporary}
        elif aggregation in {'sum', 'min', 'max', 'avg'}:
            group[measure_id] = {'$' + aggregation: operand}
            projection[measure_id] = 1
        else:
            raise SemanticModelError(
                'MongoDB semantic aggregation is unsupported'
            )

    pipeline = []
    if matches:
        pipeline.append({'$match': (
            matches[0] if len(matches) == 1 else {'$and': matches}
        )})
    pipeline.extend(({'$group': group}, {'$project': projection}))
    if calculated:
        pipeline.append({'$set': {
            item['id']: _calculation(item['expression'], measures)
            for item in calculated
        }})
    hidden = required_base.difference(selected)
    if hidden:
        pipeline.append({'$project': {
            '_id': 0, **{item: 1 for item in axes},
            **{item: 1 for item in query['measures']},
        }})
    if axes:
        pipeline.append({'$sort': {item: 1 for item in axes}})
    pipeline.append({'$limit': query['limit']})
    native = {
        'operation': 'aggregate', 'database': database,
        'collection': collection, 'pipeline': pipeline,
        'max_documents': query['limit'],
    }
    return {
        'contract_version': '1.0.0',
        'language_profile': 'mongodb-query-api-json',
        'source': json.dumps(
            native, ensure_ascii=False, separators=(',', ':')
        ),
        'parameters': {},
        'projection': {
            'axes': copy.deepcopy(query['axes']), 'levels': axes,
            'measures': copy.deepcopy(query['measures']),
            'drill': copy.deepcopy(query['drill']),
            'time_intelligence': copy.deepcopy(time),
        },
        'warnings': [],
    }


__all__ = ('compile_mongodb_aggregation',)
