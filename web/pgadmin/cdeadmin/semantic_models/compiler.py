##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Provider-invoked SQL compilation for semantic logical queries."""

from __future__ import annotations

from decimal import Decimal

from .models import SemanticModelError, validate_model, validate_query


def compile_sql(model_value, query_value, dialect):
    """Compile a validated model only when invoked by an owning provider."""
    model = validate_model(model_value)
    query = validate_query(model, query_value)
    quote_open = dialect.get('quote_open', '"')
    quote_close = dialect.get('quote_close', quote_open)

    def quote(value):
        return quote_open + str(value).replace(
            quote_close, quote_close + quote_close
        ) + quote_close

    sources = {item['id']: item for item in model['sources']}
    source_sql = {
        item['id']: '.'.join(quote(part) for part in item['relation']) +
        ' AS ' + quote(item['alias'])
        for item in model['sources']
    }

    def field(reference):
        source = sources[reference['source_id']]
        return f"{quote(source['alias'])}.{quote(reference['field'])}"

    levels = {}
    for dimension in model['dimensions']:
        levels[dimension['id']] = {
            'id': dimension['id'], 'name': dimension['name'],
            'field': dimension['field'],
        }
        for hierarchy in dimension.get('hierarchies', []):
            for level in hierarchy['levels']:
                levels[level['id']] = level
    measures = {item['id']: item for item in model['measures']}
    axis_ids = []
    for axis in ('pages', 'rows', 'columns'):
        for item in query['axes'][axis]:
            if item not in axis_ids:
                axis_ids.append(item)
    selections = [
        f"{field(levels[item]['field'])} AS {quote(item)}"
        for item in axis_ids
    ]
    aggregate_names = {
        'sum': 'SUM', 'count': 'COUNT', 'count_distinct': 'COUNT',
        'min': 'MIN', 'max': 'MAX', 'avg': 'AVG', 'none': '',
    }

    def measure_expression(measure_id, stack=frozenset()):
        if measure_id in stack:
            raise SemanticModelError('calculated measure cycle is unsupported')
        measure = measures[measure_id]
        if measure.get('expression') is not None:
            return calculation(
                measure['expression'], stack | frozenset({measure_id})
            )
        aggregation = measure['aggregation']
        if aggregation == 'count' and measure.get('field') is None:
            return 'COUNT(*)'
        operand = field(measure['field'])
        if aggregation == 'count_distinct':
            return f'COUNT(DISTINCT {operand})'
        if aggregation == 'none':
            return operand
        return f'{aggregate_names[aggregation]}({operand})'

    def calculation(node, stack):
        if 'measure' in node:
            return measure_expression(node['measure'], stack)
        if 'literal' in node:
            return str(node['literal'])
        operators = {
            'add': '+', 'subtract': '-', 'multiply': '*', 'divide': '/',
        }
        left = calculation(node['left'], stack)
        right = calculation(node['right'], stack)
        if node['operator'] == 'divide':
            right = f'NULLIF({right}, 0)'
        return f'({left} {operators[node["operator"]]} {right})'

    for measure_id in query['measures']:
        expression = measure_expression(measure_id)
        selections.append(f'{expression} AS {quote(measure_id)}')
    if not selections:
        raise SemanticModelError('semantic query has no projection')

    first = model['sources'][0]['id']
    from_clause = source_sql[first]
    joined = {first}
    remaining = list(model['joins'])
    while remaining:
        progress = False
        for join in list(remaining):
            left = join['left_source']
            right = join['right_source']
            if left not in joined or right in joined:
                continue
            join_type = join.get('join_type', 'inner').upper()
            from_clause += f' {join_type} JOIN {source_sql[right]}'
            if join_type != 'CROSS':
                predicates = [
                    f"{field(item['left'])} = {field(item['right'])}"
                    for item in join['predicates']
                ]
                from_clause += ' ON ' + ' AND '.join(predicates)
            joined.add(right)
            remaining.remove(join)
            progress = True
        if not progress:
            raise SemanticModelError(
                'joins must form an ordered graph from the first source'
            )
    if joined != set(sources):
        raise SemanticModelError('every source must be connected by a join')

    filters = model['default_filters'] + query['filters']
    predicates = [_compile_filter(item, field, dialect) for item in filters]
    source = 'SELECT ' + ', '.join(selections) + ' FROM ' + from_clause
    if predicates:
        source += ' WHERE ' + ' AND '.join(predicates)
    group_fields = [field(levels[item]['field']) for item in axis_ids]
    if group_fields and any(
        measures[item]['aggregation'] != 'none' or
        measures[item].get('expression') is not None
        for item in query['measures']
    ):
        group_keyword = 'GROUP BY'
        if query['totals'] and dialect.get('supports_rollup'):
            source += ' GROUP BY ROLLUP (' + ', '.join(group_fields) + ')'
        else:
            source += ' ' + group_keyword + ' ' + ', '.join(group_fields)
    if group_fields:
        source += ' ORDER BY ' + ', '.join(group_fields)
    if dialect.get('limit_style') == 'rows':
        source += f" ROWS 1 TO {query['limit']}"
    else:
        source += f" LIMIT {query['limit']}"
    return {
        'contract_version': '1.0.0',
        'language_profile': dialect['language_profile'],
        'source': source,
        'parameters': {},
        'projection': {
            'axes': query['axes'], 'levels': axis_ids,
            'measures': query['measures'], 'totals': query['totals'],
        },
        'warnings': ([] if not query['totals'] or dialect.get(
            'supports_rollup'
        ) else [
            'Provider does not declare native rollup; totals are omitted.'
        ]),
    }


def _literal(value, dialect):
    if value is None:
        return 'NULL'
    if isinstance(value, bool):
        return dialect.get('true_literal', 'TRUE') if value else dialect.get(
            'false_literal', 'FALSE'
        )
    if (
        isinstance(value, (int, float, Decimal)) and
        not isinstance(value, bool)
    ):
        return str(value)
    if isinstance(value, str):
        return "'" + value.replace("'", "''") + "'"
    raise SemanticModelError('filter value type is not portable')


def _compile_filter(item, field, dialect):
    left = field(item['field'])
    operator = item.get('operator', 'eq')
    unary = {'is_null': 'IS NULL', 'is_not_null': 'IS NOT NULL'}
    if operator in unary:
        return f'{left} {unary[operator]}'
    if operator in {'in', 'not_in'}:
        values = item.get('value')
        if not isinstance(values, list) or not values:
            raise SemanticModelError(
                'set filter value must be a non-empty array'
            )
        keyword = 'IN' if operator == 'in' else 'NOT IN'
        literals = ', '.join(_literal(value, dialect) for value in values)
        return f'{left} {keyword} ({literals})'
    if operator == 'between':
        values = item.get('value')
        if not isinstance(values, list) or len(values) != 2:
            raise SemanticModelError('between filter requires two values')
        return (
            f'{left} BETWEEN {_literal(values[0], dialect)} '
            f'AND {_literal(values[1], dialect)}'
        )
    operators = {
        'eq': '=', 'ne': '<>', 'lt': '<', 'lte': '<=',
        'gt': '>', 'gte': '>=',
    }
    literal = _literal(item.get('value'), dialect)
    return f'{left} {operators[operator]} {literal}'
