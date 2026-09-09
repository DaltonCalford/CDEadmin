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

import copy
import re
from decimal import Decimal
from typing import Mapping

from .models import SemanticModelError, validate_model, validate_query


def compile_sql(model_value, query_value, dialect):
    """Compile a validated model only when invoked by an owning provider."""
    if not isinstance(dialect, Mapping) or dialect.get(
            'contract_complete') is not True:
        raise SemanticModelError(
            'provider semantic SQL dialect contract is incomplete'
        )
    required = {
        'language_profile', 'quote_open', 'quote_close',
        'supports_rollup', 'limit_style', 'true_literal', 'false_literal',
        'time_operations', 'window_operations',
    }
    missing = sorted(required.difference(dialect))
    if missing:
        raise SemanticModelError(
            'provider semantic SQL dialect contract is missing: ' +
            ', '.join(missing)
        )
    if dialect['limit_style'] not in {'limit', 'rows'}:
        raise SemanticModelError(
            'provider semantic SQL limit style is unsupported'
        )
    rollup_style = dialect.get('rollup_style', 'function')
    if rollup_style not in {'function', 'with_rollup'}:
        raise SemanticModelError(
            'provider semantic SQL rollup style is unsupported'
        )
    rollup_allows_order_by = dialect.get(
        'rollup_allows_order_by', True
    )
    if not isinstance(rollup_allows_order_by, bool):
        raise SemanticModelError(
            'provider semantic SQL rollup ordering flag is invalid'
        )
    window_input_cast = dialect.get('window_input_cast')
    if window_input_cast is not None and (
        not isinstance(window_input_cast, str) or
        re.fullmatch(
            r'[A-Za-z][A-Za-z0-9_]*(?:\(\d+(?:,\s*\d+)?\))?',
            window_input_cast,
        ) is None
    ):
        raise SemanticModelError(
            'provider semantic SQL window input cast is invalid'
        )
    percent_change_result_cast = dialect.get(
        'percent_change_result_cast'
    )
    if percent_change_result_cast is not None and (
        not isinstance(percent_change_result_cast, str) or
        re.fullmatch(
            r'[A-Za-z][A-Za-z0-9_]*(?:\(\d+(?:,\s*\d+)?\))?',
            percent_change_result_cast,
        ) is None
    ):
        raise SemanticModelError(
            'provider semantic SQL percent-change result cast is invalid'
        )
    percent_change_numerator_cast = dialect.get(
        'percent_change_numerator_cast'
    )
    if percent_change_numerator_cast is not None and (
        not isinstance(percent_change_numerator_cast, str) or
        re.fullmatch(
            r'[A-Za-z][A-Za-z0-9_]*(?:\(\d+(?:,\s*\d+)?\))?',
            percent_change_numerator_cast,
        ) is None
    ):
        raise SemanticModelError(
            'provider semantic SQL percent-change numerator cast is invalid'
        )
    model = validate_model(model_value)
    query = validate_query(model, query_value)
    time_operations = frozenset(dialect['time_operations'])
    window_operations = frozenset(dialect['window_operations'])
    if query['time_intelligence'] and query['time_intelligence'][
            'operation'] not in time_operations:
        raise SemanticModelError(
            'provider does not admit the requested time-intelligence operation'
        )
    unavailable_windows = {
        item['operation'] for item in query['windows']
    }.difference(window_operations)
    if unavailable_windows:
        raise SemanticModelError(
            'provider does not admit requested analytical window operations: '
            + ', '.join(sorted(unavailable_windows))
        )
    quote_open = dialect['quote_open']
    quote_close = dialect['quote_close']

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
    drill = query['drill']
    if drill['mode'] == 'down' and drill.get('target_level') not in axis_ids:
        axis_ids.append(drill['target_level'])
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
        measure = measures[measure_id]
        if drill['mode'] == 'through':
            if measure.get('expression') is not None or not measure.get(
                    'field'):
                raise SemanticModelError(
                    'drill-through requires field-backed measures'
                )
            expression = field(measure['field'])
        else:
            expression = measure_expression(measure_id)
            if query['windows'] and window_input_cast and any(
                item['measure_id'] == measure_id
                for item in query['windows']
            ):
                expression = (
                    f'CAST({expression} AS {window_input_cast})'
                )
        selections.append(f'{expression} AS {quote(measure_id)}')
    detail_aliases = []
    for index, reference in enumerate(drill['detail_fields']):
        alias = f'detail_{index + 1}_{reference["field"]}'
        detail_aliases.append(alias)
        selections.append(f'{field(reference)} AS {quote(alias)}')
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

    parameter_values = {
        item['id']: item.get('default') for item in model['parameters']
        if 'default' in item
    }
    parameter_values.update(query['parameters'])
    filters = (
        model['default_filters'] + model['security']['row_filters'] +
        query['filters'] + query['cross_filters']
    )
    predicates = [
        _compile_filter(item, field, dialect, parameter_values)
        for item in filters
    ]
    time_intelligence = query['time_intelligence']
    comparison_expression = None
    if time_intelligence:
        dimension = next(
            item for item in model['dimensions']
            if item['id'] == time_intelligence['dimension_id']
        )
        time_field = field(dimension['field'])
        start = _literal(time_intelligence['start'], dialect)
        operation = time_intelligence['operation']
        if operation == 'as_of':
            predicates.append(f'{time_field} <= {start}')
        elif operation == 'period_comparison':
            end = _literal(time_intelligence['end'], dialect)
            previous_start = _literal(
                time_intelligence['comparison_start'], dialect
            )
            previous_end = _literal(
                time_intelligence['comparison_end'], dialect
            )
            current = f'{time_field} BETWEEN {start} AND {end}'
            previous = (
                f'{time_field} BETWEEN {previous_start} AND {previous_end}'
            )
            predicates.append(f'(({current}) OR ({previous}))')
            comparison_expression = (
                f"CASE WHEN {current} THEN 'current' "
                f"WHEN {previous} THEN 'comparison' END"
            )
            selections.append(
                f'{comparison_expression} AS {quote("__semantic_period")}'
            )
        else:
            end = _literal(time_intelligence['end'], dialect)
            predicates.append(f'{time_field} BETWEEN {start} AND {end}')
    source = 'SELECT ' + ', '.join(selections) + ' FROM ' + from_clause
    if predicates:
        source += ' WHERE ' + ' AND '.join(predicates)
    group_fields = [field(levels[item]['field']) for item in axis_ids]
    if comparison_expression is not None:
        group_fields.append(comparison_expression)
    if drill['mode'] != 'through' and group_fields and any(
        measures[item]['aggregation'] != 'none' or
        measures[item].get('expression') is not None
        for item in query['measures']
    ):
        group_keyword = 'GROUP BY'
        if query['totals'] and dialect['supports_rollup']:
            if rollup_style == 'with_rollup':
                source += (
                    ' GROUP BY ' + ', '.join(group_fields) + ' WITH ROLLUP'
                )
            else:
                source += (
                    ' GROUP BY ROLLUP (' + ', '.join(group_fields) + ')'
                )
        else:
            source += ' ' + group_keyword + ' ' + ', '.join(group_fields)
    output_order = [quote(item) for item in axis_ids]
    if comparison_expression is not None:
        output_order.append(quote('__semantic_period'))
    if query['windows']:
        source = _compile_sql_windows(
            source, query['windows'], axis_ids, quote,
            percent_change_result_cast=percent_change_result_cast,
            percent_change_numerator_cast=percent_change_numerator_cast,
        )
    native_rollup_without_order = bool(
        query['totals'] and dialect['supports_rollup'] and
        not rollup_allows_order_by
    )
    if output_order and not native_rollup_without_order:
        source += ' ORDER BY ' + ', '.join(output_order)
    if dialect['limit_style'] == 'rows':
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
            'drill': copy.deepcopy(drill),
            'detail_fields': detail_aliases,
            'time_intelligence': copy.deepcopy(time_intelligence),
            'windows': copy.deepcopy(query['windows']),
        },
        'warnings': (
            [
                'Provider does not declare native rollup; totals are omitted.'
            ] if query['totals'] and not dialect['supports_rollup'] else
            [
                'Provider native rollup does not admit ORDER BY; rollup '
                'order is provider-defined.'
            ] if native_rollup_without_order and output_order else []
        ),
    }


def _compile_sql_windows(
        source, windows, axis_ids, quote,
        percent_change_result_cast=None,
        percent_change_numerator_cast=None):
    """Wrap an aggregate query with provider-admitted native windows."""
    admitted = set(axis_ids)
    for window in windows:
        required = set(window['partition_by']) | {
            window['order_by']['level_id']
        }
        if not required.issubset(admitted):
            raise SemanticModelError(
                'window partition and order levels must be query axes'
            )
    alias = quote('semantic_base')

    def column(name):
        return f'{alias}.{quote(name)}'

    expressions = []
    for window in windows:
        measure = column(window['measure_id'])
        partitions = ', '.join(
            column(item) for item in window['partition_by']
        )
        order = column(window['order_by']['level_id']) + ' ' + window[
            'order_by']['direction'].upper()
        clause = (
            ('PARTITION BY ' + partitions + ' ') if partitions else ''
        ) + 'ORDER BY ' + order
        operation = window['operation']
        size = window['frame_size']
        if operation == 'running_sum':
            value = (
                f'SUM({measure}) OVER ({clause} ROWS BETWEEN UNBOUNDED '
                'PRECEDING AND CURRENT ROW)'
            )
        elif operation in {'moving_sum', 'moving_average'}:
            function = 'SUM' if operation == 'moving_sum' else 'AVG'
            value = (
                f'{function}({measure}) OVER ({clause} ROWS BETWEEN '
                f'{size - 1} PRECEDING AND CURRENT ROW)'
            )
        elif operation == 'lag':
            value = f'LAG({measure}, {size}) OVER ({clause})'
        elif operation in {'delta', 'percent_change'}:
            previous = f'LAG({measure}, {size}) OVER ({clause})'
            if operation == 'delta':
                value = f'({measure} - {previous})'
            else:
                numerator = f'({measure} - {previous})'
                if percent_change_numerator_cast:
                    numerator = (
                        f'CAST({numerator} AS '
                        f'{percent_change_numerator_cast})'
                    )
                value = (
                    f'(CASE WHEN {previous} IS NULL OR {previous} = 0 '
                    f'THEN NULL ELSE {numerator} / '
                    f'{previous} END)'
                )
                if percent_change_result_cast:
                    value = (
                        f'CAST({value} AS {percent_change_result_cast})'
                    )
        else:
            function = 'RANK' if operation == 'rank' else 'DENSE_RANK'
            rank_clause = (
                ('PARTITION BY ' + partitions + ' ') if partitions else ''
            ) + f'ORDER BY {measure} DESC'
            value = f'{function}() OVER ({rank_clause})'
        expressions.append(f'{value} AS {quote(window["id"])}')
    return (
        f'SELECT {alias}.*, ' + ', '.join(expressions) +
        f' FROM ({source}) AS {alias}'
    )


def _literal(value, dialect):
    if value is None:
        return 'NULL'
    if isinstance(value, bool):
        return dialect['true_literal'] if value else dialect['false_literal']
    if (
        isinstance(value, (int, float, Decimal)) and
        not isinstance(value, bool)
    ):
        return str(value)
    if isinstance(value, str):
        return "'" + value.replace("'", "''") + "'"
    raise SemanticModelError('filter value type is not portable')


def _compile_filter(item, field, dialect, parameters=None):
    left = field(item['field'])
    operator = item.get('operator', 'eq')
    if item.get('parameter_id') is not None:
        parameter_id = item['parameter_id']
        if parameter_id not in (parameters or {}):
            raise SemanticModelError(
                f'query parameter {parameter_id!r} has no value'
            )
        item = dict(item)
        item['value'] = parameters[parameter_id]
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
