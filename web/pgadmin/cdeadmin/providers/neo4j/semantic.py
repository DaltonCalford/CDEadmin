##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Neo4j-owned semantic model to Cypher compiler."""

from __future__ import annotations

import copy

from pgadmin.cdeadmin.semantic_models import (
    SemanticModelError, validate_model, validate_query,
)


def _quote(value):
    return '`' + str(value).replace('`', '``') + '`'


def compile_neo4j_cypher(model_value, query_value):
    """Compile graph semantic intent to parameterized read-only Cypher."""
    model = validate_model(model_value)
    query = validate_query(model, query_value)
    if model['semantic_family'] != 'graph':
        raise SemanticModelError('Neo4j requires a graph semantic model')
    if model['joins']:
        raise SemanticModelError('Neo4j semantic models use native edges')
    sources = {item['id']: item for item in model['sources']}

    def field(reference):
        return f"{_quote(sources[reference['source_id']]['alias'])}.{
            _quote(reference['field'])}"

    patterns = []
    for source in model['sources']:
        label = source['provider_config'].get('label')
        if not label and source['source_kind'] in {'node', 'label'}:
            label = source['relation'][-1]
        suffix = ':' + _quote(label) if label else ''
        patterns.append(f"({_quote(source['alias'])}{suffix})")
    matches = ['MATCH ' + ', '.join(patterns)]
    for index, relationship in enumerate(model['relationships']):
        left = _quote(sources[relationship['from_source']]['alias'])
        right = _quote(sources[relationship['to_source']]['alias'])
        config = relationship['provider_config']
        relation_type = config.get('type')
        typed = ':' + _quote(relation_type) if relation_type else ''
        variable = _quote('semantic_edge_' + str(index + 1))
        edge = f'[{variable}{typed}]'
        direction = config.get('direction', 'out')
        if direction == 'in':
            pattern = f'({left})<-{edge}-({right})'
        elif direction == 'undirected':
            pattern = f'({left})-{edge}-({right})'
        elif direction == 'out':
            pattern = f'({left})-{edge}->({right})'
        else:
            raise SemanticModelError(
                'Neo4j relationship direction is unsupported'
            )
        matches.append('MATCH ' + pattern)

    values = {
        item['id']: item['default'] for item in model['parameters']
        if 'default' in item
    }
    values.update(query['parameters'])
    bound = {}

    def bind(value):
        name = 'semantic_' + str(len(bound) + 1)
        bound[name] = value
        return '$' + name

    def predicate(item):
        value = item.get('value')
        if item.get('parameter_id') is not None:
            try:
                value = values[item['parameter_id']]
            except KeyError as exc:
                raise SemanticModelError(
                    f'query parameter {item["parameter_id"]!r} has no value'
                ) from exc
        left = field(item['field'])
        operator = item['operator']
        if operator == 'is_null':
            return left + ' IS NULL'
        if operator == 'is_not_null':
            return left + ' IS NOT NULL'
        if operator == 'between':
            if not isinstance(value, list) or len(value) != 2:
                raise SemanticModelError('between filter requires two values')
            return f'{left} >= {bind(value[0])} AND {left} <= {bind(value[1])}'
        operators = {
            'eq': '=', 'ne': '<>', 'lt': '<', 'lte': '<=',
            'gt': '>', 'gte': '>=', 'in': 'IN', 'not_in': 'NOT IN',
        }
        return f'{left} {operators[operator]} {bind(value)}'

    filters = (
        model['default_filters'] + model['security']['row_filters'] +
        query['filters'] + query['cross_filters']
    )
    predicates = [predicate(item) for item in filters]
    time = query['time_intelligence']
    if time:
        dimension = next(
            item for item in model['dimensions']
            if item['id'] == time['dimension_id']
        )
        left = field(dimension['field'])
        if time['operation'] == 'as_of':
            predicates.append(f'{left} <= {bind(time["start"])}')
        else:
            predicates.append(
                f'{left} >= {bind(time["start"])} AND '
                f'{left} <= {bind(time["end"])}'
            )

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

    def calculation(node):
        if 'measure' in node:
            return measure_expression(node['measure'])
        if 'literal' in node:
            return str(node['literal'])
        operators = {
            'add': '+', 'subtract': '-', 'multiply': '*', 'divide': '/',
        }
        left = calculation(node['left'])
        right = calculation(node['right'])
        if node['operator'] == 'divide':
            return (
                f'(CASE WHEN {right} = 0 THEN null '
                f'ELSE {left} / {right} END)'
            )
        return f'({left} {operators[node["operator"]]} {right})'

    def measure_expression(measure_id):
        measure = measures[measure_id]
        if measure.get('expression') is not None:
            return calculation(measure['expression'])
        aggregation = measure['aggregation']
        if aggregation == 'count' and measure.get('field') is None:
            return 'count(*)'
        operand = field(measure['field'])
        if query['drill']['mode'] == 'through':
            return operand
        if aggregation == 'count_distinct':
            return f'count(DISTINCT {operand})'
        if aggregation in {'sum', 'min', 'max', 'avg', 'count'}:
            return f'{aggregation}({operand})'
        if aggregation == 'none':
            return operand
        raise SemanticModelError('Neo4j semantic aggregation is unsupported')

    if query['drill']['mode'] == 'through':
        for measure_id in query['measures']:
            if measures[measure_id].get('expression') is not None or not (
                    measures[measure_id].get('field')):
                raise SemanticModelError(
                    'Neo4j drill-through requires field-backed measures'
                )
    selections = [
        f'{field(levels[item])} AS {_quote(item)}' for item in axes
    ]
    selections.extend(
        f'{measure_expression(item)} AS {_quote(item)}'
        for item in query['measures']
    )
    for index, reference in enumerate(query['drill']['detail_fields']):
        selections.append(
            f'{field(reference)} AS {_quote("detail_" + str(index + 1))}'
        )
    source = ' '.join(matches)
    if predicates:
        source += ' WHERE ' + ' AND '.join(f'({item})' for item in predicates)
    source += ' RETURN ' + ', '.join(selections)
    if axes:
        source += ' ORDER BY ' + ', '.join(_quote(item) for item in axes)
    source += f' LIMIT {query["limit"]}'
    return {
        'contract_version': '1.0.0', 'language_profile': 'cypher',
        'source': source, 'parameters': bound,
        'projection': {
            'axes': copy.deepcopy(query['axes']), 'levels': axes,
            'measures': copy.deepcopy(query['measures']),
            'drill': copy.deepcopy(query['drill']),
            'time_intelligence': copy.deepcopy(time),
        },
        'warnings': ([
            'Neo4j has no common SQL rollup; totals were not generated.'
        ] if query['totals'] else []),
    }


__all__ = ('compile_neo4j_cypher',)
