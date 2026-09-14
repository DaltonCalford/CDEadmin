##########################################################################
# CDEadmin - Multi-engine Database Administration
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
##########################################################################

"""Structured Firebird 5 ALTER COLUMN clauses with native execution."""

import re

from pgadmin.cdeadmin.sdk.relational import RelationalClientError
from ..firebird_expressions import index_expression
from .mappings import identifier, literal

ACTIONS = ('POSITION', 'SET NOT NULL', 'DROP NOT NULL', 'SET DEFAULT',
           'DROP DEFAULT', 'TYPE', 'COMPUTED', 'TYPE COMPUTED',
           'IDENTITY', 'DROP IDENTITY')
TYPES = ('SMALLINT', 'INTEGER', 'BIGINT', 'INT128', 'NUMERIC', 'DECIMAL',
         'FLOAT', 'DOUBLE PRECISION', 'DECFLOAT', 'BOOLEAN', 'DATE', 'TIME',
         'TIMESTAMP', 'CHAR', 'VARCHAR', 'NCHAR', 'NCHAR VARYING',
         'BINARY', 'VARBINARY', 'BLOB', 'DOMAIN')
DEFAULTS = ('TEXT', 'BINARY', 'NUMBER', 'NULL', 'TRUE', 'FALSE', 'DATE',
            'TIME', 'TIMESTAMP', 'CURRENT_USER', 'CURRENT_ROLE',
            'CURRENT_DATE',
            'CURRENT_TIME', 'CURRENT_TIMESTAMP', 'LOCALTIME',
            'LOCALTIMESTAMP', 'CURRENT_CONNECTION', 'CURRENT_TRANSACTION')
TIMED_DEFAULTS = ('CURRENT_TIME', 'CURRENT_TIMESTAMP', 'LOCALTIME',
                  'LOCALTIMESTAMP')
MODES = ('STORED', 'IDENTITY', 'COMPUTED', 'COMPUTED INFERRED')
CONSTRAINTS = ('NOT NULL', 'CHECK', 'UNIQUE', 'PRIMARY KEY', 'REFERENCES')
REFERENTIAL_ACTIONS = ('UNCHANGED', 'NO ACTION', 'CASCADE', 'SET DEFAULT',
                       'SET NULL')


def alteration_context(column, relation, *, primary_key=False, array=False):
    """Structural admission only; native data/dependency checks still apply."""
    editable = (not relation.get('system_object') and
                str(relation.get('relation_type')) in {'0', '2', '4', '5'})
    computed = column.get('computed_source') is not None
    identity = str(column.get('identity_type')) in {'0', '1'}
    allowed = []
    if editable:
        allowed = ['POSITION', 'SET NOT NULL']
        if not identity and not primary_key:
            allowed.append('DROP NOT NULL')
        if not computed and not identity and not array:
            allowed.append('SET DEFAULT')
        if column.get('default_source') is not None:
            allowed.append('DROP DEFAULT')
        if computed:
            allowed.extend(['COMPUTED', 'TYPE COMPUTED'])
        elif not array and str(column.get('field_type')) != '261':
            allowed.append('TYPE')
        if identity:
            allowed.extend(['IDENTITY', 'DROP IDENTITY'])
    result = {'allowed_actions': [item for item in ACTIONS if item in allowed],
              'primary_key_member': primary_key, 'array': array,
              'relation_type': relation.get('relation_type')}
    position = column.get('position')
    if position is not None and str(position).isdigit():
        result['position'] = int(position) + 1
    return result


def column_constraint(value):
    if not isinstance(value, dict) or value.get('kind') not in CONSTRAINTS:
        raise RelationalClientError('Choose a native column constraint')
    kind = value['kind']
    sql = ('CONSTRAINT ' + identifier(value['name']) + ' '
           if value.get('name') else '')
    if kind == 'CHECK':
        sql += 'CHECK (' + index_expression(
            value.get('expression'), 'Check expression') + '\n)'
    elif kind == 'REFERENCES':
        sql += 'REFERENCES ' + identifier(value.get('reference_table'))
        if value.get('reference_column'):
            sql += ' (' + identifier(value['reference_column']) + ')'
        for key, clause in (('on_update', 'ON UPDATE'),
                            ('on_delete', 'ON DELETE')):
            action = value.get(key, 'UNCHANGED')
            if action not in REFERENTIAL_ACTIONS:
                raise RelationalClientError('Invalid referential action')
            if action != 'UNCHANGED':
                sql += f' {clause} {action}'
    else:
        sql += kind
    if value.get('index_name'):
        if kind not in ('PRIMARY KEY', 'UNIQUE', 'REFERENCES'):
            raise RelationalClientError('This constraint has no backing index')
        direction = value.get('index_direction', 'ASCENDING')
        if direction not in ('ASCENDING', 'DESCENDING'):
            raise RelationalClientError('Invalid constraint index direction')
        sql += f' USING {direction} INDEX ' + identifier(value['index_name'])
    return sql


def definition(draft):
    """One native ADD/CREATE TABLE column, without a statement wrapper."""
    mode = draft.get('column_mode')
    if mode not in MODES:
        raise RelationalClientError('Choose a native column definition mode')
    sql = identifier(draft.get('name'))
    dimensions = draft.get('dimensions', [])
    if not isinstance(dimensions, list) or len(dimensions) > 16:
        raise RelationalClientError('An array has at most 16 dimensions')
    if dimensions and (mode != 'STORED' or
                       draft.get('data_type') in ('DOMAIN', 'BLOB')):
        raise RelationalClientError('Array dimensions require a stored '
                                    'non-BLOB base type')
    if mode != 'COMPUTED INFERRED':
        if mode == 'COMPUTED' and draft.get('data_type') == 'DOMAIN':
            raise RelationalClientError('Computed columns require a native '
                                        'type, not a domain')
        rendered = data_type(draft)
        if dimensions:
            ranges = []
            for dimension in dimensions:
                if not isinstance(dimension, dict):
                    raise RelationalClientError('Invalid array dimension')
                lower = integer(dimension.get('lower'), -2147483648,
                                2147483647, 'Lower bound')
                upper = integer(dimension.get('upper'), -2147483648,
                                2147483647, 'Upper bound')
                if int(lower) > int(upper):
                    raise RelationalClientError('Lower bound exceeds upper '
                                                'bound')
                ranges.append(lower + ':' + upper)
            base, separator, charset = rendered.partition(' CHARACTER SET ')
            rendered = base + '[' + ', '.join(ranges) + ']' + (
                separator + charset if separator else '')
        sql += ' ' + rendered
    constraints = draft.get('constraints', [])
    if not isinstance(constraints, list):
        raise RelationalClientError('Column constraints must be a list')
    enabled = draft.get('has_default', False)
    if not isinstance(enabled, bool):
        raise RelationalClientError('Default enabled must be boolean')
    if mode in ('COMPUTED', 'COMPUTED INFERRED'):
        if enabled or constraints or draft.get('collation'):
            raise RelationalClientError('Computed column grammar has no '
                                        'default, constraint or '
                                        'COLLATE clause')
        return sql + ' COMPUTED BY (' + index_expression(
            draft.get('expression'), 'Computed expression') + '\n)'
    if mode == 'IDENTITY':
        if enabled:
            raise RelationalClientError('Identity columns cannot have '
                                        'DEFAULT')
        generation = draft.get('generation', 'BY DEFAULT')
        if generation not in ('ALWAYS', 'BY DEFAULT'):
            raise RelationalClientError('Invalid identity generation mode')
        sql += ' GENERATED ' + generation + ' AS IDENTITY'
        settings = []
        if draft.get('start_value') not in (None, ''):
            settings.append('START WITH ' + integer(
                draft['start_value'], -9223372036854775808,
                9223372036854775807, 'Identity start'))
        if draft.get('increment') not in (None, ''):
            increment = integer(draft['increment'], -2147483648,
                                2147483647, 'Identity increment')
            if increment == '0':
                raise RelationalClientError('Identity increment cannot '
                                            'be zero')
            settings.append('INCREMENT BY ' + increment)
        if settings:
            sql += ' (' + ' '.join(settings) + ')'
    elif enabled:
        sql += ' DEFAULT ' + default_value(draft)
    for constraint in constraints:
        sql += ' ' + column_constraint(constraint)
    if draft.get('collation'):
        sql += ' COLLATE ' + identifier(draft['collation'])
    return sql


def compile_create(draft):
    return ('ALTER TABLE ' + identifier(draft.get('table')) + ' ADD ' +
            definition(draft))


def creation_form(field):
    fields = [field('table', 'Table', 'text', True),
              field('name', 'Column name', 'text', True),
              field('column_mode', 'Column mode', 'select', True,
                    default='STORED', options=MODES)]
    existing = form('alter', field)['fields']
    type_fields = {'data_type', 'domain', 'length', 'precision', 'scale',
                   'blob_subtype', 'segment_size', 'character_set',
                   'time_zone'}
    default_fields = {'default_kind', 'default_value', 'time_precision'}
    for item in existing:
        name = item['field_id']
        if name not in type_fields | default_fields | {'expression'}:
            continue
        if name in type_fields:
            primary = {'field_id': 'column_mode',
                       'in': ['STORED', 'IDENTITY', 'COMPUTED']}
        elif name in default_fields:
            primary = {'all': [
                {'field_id': 'column_mode', 'equals': 'STORED'},
                {'field_id': 'has_default', 'equals': True}]}
        else:
            primary = {'field_id': 'column_mode',
                       'in': ['COMPUTED', 'COMPUTED INFERRED']}
        old = item.get('visible_when', {})
        item['visible_when'] = {'all': [primary, old['all'][1]]} if (
            'all' in old) else primary
        if name == 'data_type':
            for option in item['options']:
                option.pop('visible_when', None)
                if option['value'] == 'DOMAIN':
                    option['visible_when'] = {'field_id': 'column_mode',
                                              'in': ['STORED', 'IDENTITY']}
                elif option['value'] not in ('SMALLINT', 'INTEGER', 'BIGINT',
                                             'INT128', 'NUMERIC', 'DECIMAL'):
                    option['visible_when'] = {'field_id': 'column_mode',
                                              'in': ['STORED', 'COMPUTED']}
        if name == 'blob_subtype':
            item['help'] = '0 binary, 1 text, or a native custom subtype.'
        fields.append(item)
    fields.insert(3, {**field('has_default', 'Set column default', 'boolean',
                              default=False),
                      'visible_when': {'field_id': 'column_mode',
                                       'equals': 'STORED'}})
    identity = {'field_id': 'column_mode', 'equals': 'IDENTITY'}
    for item in [field('generation', 'Identity generation', 'select', True,
                       default='BY DEFAULT',
                       options=('ALWAYS', 'BY DEFAULT')),
                 field('start_value', 'Identity start (signed 64-bit)',
                       'text'),
                 field('increment', 'Identity increment (signed 32-bit)',
                       'text')]:
        fields.append({**item, 'visible_when': identity})
    stored = {'field_id': 'column_mode', 'in': ['STORED', 'IDENTITY']}
    fields.append({**field('collation', 'Collation', 'text'),
                   'visible_when': stored})
    dimensions = field('dimensions', 'Array dimensions', 'json',
                       default=[])
    dimensions.update(json_type='array', array_editor={
        'item_kind': 'object', 'fields': [
            field('lower', 'Lower bound', 'number', True, default=1),
            field('upper', 'Upper bound', 'number', True, default=1)]},
        visible_when={'all': [
            {'field_id': 'column_mode', 'equals': 'STORED'},
            {'field_id': 'data_type', 'in': [
                name for name in TYPES if name not in ('DOMAIN', 'BLOB')]}]})
    fields.append(dimensions)
    children = [field('kind', 'Constraint kind', 'select', True,
                      default='NOT NULL', options=CONSTRAINTS),
                field('name', 'Constraint name (optional)', 'text')]
    for name, title, kind, kinds, options, default in (
            ('expression', 'Check expression', 'code', ['CHECK'], None, None),
            ('reference_table', 'Referenced table', 'text', ['REFERENCES'],
             None, None),
            ('reference_column', 'Referenced column (empty uses primary key)',
             'text', ['REFERENCES'], None, None),
            ('on_update', 'On update', 'select', ['REFERENCES'],
             REFERENTIAL_ACTIONS, 'UNCHANGED'),
            ('on_delete', 'On delete', 'select', ['REFERENCES'],
             REFERENTIAL_ACTIONS, 'UNCHANGED'),
            ('index_name', 'Backing index name (optional)', 'text',
             ['UNIQUE', 'PRIMARY KEY', 'REFERENCES'], None, None),
            ('index_direction', 'Backing index direction', 'select',
             ['UNIQUE', 'PRIMARY KEY', 'REFERENCES'],
             ('ASCENDING', 'DESCENDING'), 'ASCENDING')):
        children.append({**field(name, title, kind,
                                 name in ('expression', 'reference_table'),
                                 default=default, options=options),
                         'visible_when': {'field_id': 'kind', 'in': kinds}})
    constraints = field('constraints', 'Column constraints', 'json',
                        default=[])
    constraints.update(json_type='array', array_editor={
        'item_kind': 'object', 'fields': children}, visible_when=stored)
    fields.append(constraints)
    return {'form_id': 'firebird.column.create',
            'title': 'Create Firebird column', 'fields': fields}


def integer(value, minimum, maximum, label):
    if isinstance(value, bool) or not re.fullmatch(r'[+-]?\d+', str(value)):
        raise RelationalClientError(f'{label} must be an integer')
    result = int(value)
    if not minimum <= result <= maximum:
        raise RelationalClientError(
            f'{label} must be between {minimum} and {maximum}')
    return str(result)


def data_type(draft):
    name = draft.get('data_type')
    if name not in TYPES:
        raise RelationalClientError('Choose a native Firebird data type')
    if name == 'DOMAIN':
        return identifier(draft.get('domain'))
    sql = name
    if name in ('CHAR', 'VARCHAR', 'NCHAR', 'NCHAR VARYING',
                'BINARY', 'VARBINARY'):
        sql += '(' + integer(draft.get('length'), 1, 32767, 'Length') + ')'
    elif name in ('NUMERIC', 'DECIMAL'):
        precision = integer(optional_value(draft, 'precision', 9),
                            1, 38, 'Precision')
        scale = integer(optional_value(draft, 'scale', 0),
                        0, int(precision), 'Scale')
        sql += f'({precision}, {scale})'
    elif name == 'FLOAT' and draft.get('precision') not in (None, ''):
        sql += '(' + integer(draft['precision'], 1, 53, 'Precision') + ')'
    elif name == 'DECFLOAT':
        precision = optional_value(draft, 'precision', 34)
        if str(precision) not in ('16', '34'):
            raise RelationalClientError('DECFLOAT precision is 16 or 34')
        sql += '(' + str(precision) + ')'
    elif name in ('TIME', 'TIMESTAMP'):
        zone = draft.get('time_zone', 'WITHOUT TIME ZONE')
        if zone not in ('WITH TIME ZONE', 'WITHOUT TIME ZONE'):
            raise RelationalClientError('Invalid time zone type')
        sql += ' ' + zone
    elif name == 'BLOB':
        sql += ' SUB_TYPE ' + integer(optional_value(draft, 'blob_subtype', 0),
                                      -32768, 32767, 'BLOB subtype')
        if draft.get('segment_size') not in (None, ''):
            sql += ' SEGMENT SIZE ' + integer(
                draft['segment_size'], 0, 65535, 'Segment size')
    if draft.get('character_set'):
        if name not in ('CHAR', 'VARCHAR', 'BLOB'):
            raise RelationalClientError('Character set does not apply here')
        sql += ' CHARACTER SET ' + identifier(draft['character_set'])
    return sql


def optional_value(draft, name, default):
    value = draft.get(name)
    return default if value in (None, '') else value


def default_value(draft):
    kind = draft.get('default_kind')
    if kind not in DEFAULTS:
        raise RelationalClientError('Choose a default value type')
    value = draft.get('default_value', '')
    if kind in ('TEXT', 'DATE', 'TIME', 'TIMESTAMP'):
        return ('' if kind == 'TEXT' else kind + ' ') + literal(value)
    if kind == 'BINARY':
        if not isinstance(value, str) or not re.fullmatch(
                r'(?:[0-9a-fA-F]{2})*', value):
            raise RelationalClientError(
                'Enter complete hexadecimal byte pairs')
        return "X'" + value.upper() + "'"
    if kind == 'NUMBER':
        if not isinstance(value, str) or not re.fullmatch(
                r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?', value):
            raise RelationalClientError('Enter a numeric constant')
        return value.removeprefix('+')
    if (kind in TIMED_DEFAULTS and
            draft.get('time_precision') not in (None, '')):
        precision = integer(draft['time_precision'], 0, 3, 'Time precision')
        return kind + '(' + precision + ')'
    return kind


def validate_target(operation, draft, target):
    target = target or {}
    native = target.get('extensions', {}).get('firebird', {}).get(
        'native', target.get('native', {}))
    context = native.get('alteration')
    if (operation == 'alter' and context is not None and
            draft.get('action') not in context.get('allowed_actions', [])):
        raise RelationalClientError(
            'This alteration is not applicable to the selected '
            'Firebird column; refresh its metadata.')


def compile_column(operation, draft, path):
    if len(path) != 2:
        raise RelationalClientError('A Firebird column belongs to one table')
    table, column = (identifier(item) for item in path)
    if operation == 'comment':
        value = draft.get('description', '')
        return (f'COMMENT ON COLUMN {table}.{column} IS ' +
                ('NULL' if value == '' else literal(value)))
    action = draft.get('action')
    if operation != 'alter' or action not in ACTIONS:
        raise RelationalClientError('Choose a Firebird column alteration')
    if action == 'POSITION':
        clause = 'POSITION ' + integer(draft.get('position'), 1, 32767,
                                       'Column position')
    elif action == 'SET DEFAULT':
        clause = 'SET DEFAULT ' + default_value(draft)
    elif action in ('TYPE', 'COMPUTED', 'TYPE COMPUTED'):
        clauses = []
        if action != 'COMPUTED':
            if action == 'TYPE' and draft.get('data_type') == 'BLOB':
                raise RelationalClientError(
                    'Firebird does not allow ordinary BLOB type changes; '
                    'a computed result type is a separate operation.')
            if (action == 'TYPE COMPUTED' and
                    draft.get('data_type') == 'DOMAIN'):
                raise RelationalClientError(
                    'TYPE with COMPUTED requires a native type, not a domain')
            clauses.append('TYPE ' + data_type(draft))
        if action != 'TYPE':
            expression = index_expression(draft.get('expression'),
                                          'Computed expression')
            clauses.append('COMPUTED BY (' + expression + '\n)')
        clause = ' '.join(clauses)
    elif action == 'IDENTITY':
        clauses = []
        generation = draft.get('generation', 'UNCHANGED')
        if generation not in ('UNCHANGED', 'ALWAYS', 'BY DEFAULT'):
            raise RelationalClientError('Invalid identity generation mode')
        if generation != 'UNCHANGED':
            clauses.append('SET GENERATED ' + generation)
        restart = draft.get('restart', 'UNCHANGED')
        if restart not in ('UNCHANGED', 'ORIGINAL', 'WITH VALUE'):
            raise RelationalClientError('Invalid identity restart mode')
        if restart == 'ORIGINAL':
            clauses.append('RESTART')
        elif restart == 'WITH VALUE':
            clauses.append('RESTART WITH ' + integer(
                draft.get('restart_value'), -(2 ** 63), 2 ** 63 - 1,
                'Restart value'))
        if draft.get('increment') not in (None, ''):
            increment = integer(draft['increment'], -(2 ** 31), 2 ** 31 - 1,
                                'Increment')
            if increment == '0':
                raise RelationalClientError(
                    'Identity increment cannot be zero')
            clauses.append('SET INCREMENT BY ' + increment)
        if not clauses:
            raise RelationalClientError('Select an identity change')
        clause = ' '.join(clauses)
    else:
        clause = action
    return f'ALTER TABLE {table} ALTER COLUMN {column} {clause}'


def form(operation, field):
    if operation == 'comment':
        fields = [{**field('description', 'Comment', 'multiline', False,
                           'Empty text removes the comment.', ''),
                   'initial_value_path': ['description'],
                   'submit_unchanged': True}]
    else:
        fields = [field('action', 'Alteration', 'select', True,
                        'Choose one native alteration. Firebird validates '
                        'existing data and dependent objects. Dropping a '
                        'column NOT NULL does not change its domain.',
                        'POSITION', options=ACTIONS)]
        fields[0]['option_values_path'] = ['alteration', 'allowed_actions']

        def add(name, title, kind, actions, required=False, default=None,
                options=None, help_text=''):
            item = field(name, title, kind, required, help_text, default,
                         options=options)
            item['visible_when'] = {'field_id': 'action', 'in': actions}
            fields.append(item)

        add('position', 'Position (one-based)', 'number',
            ['POSITION'], True, 1)
        fields[-1]['initial_value_path'] = ['alteration', 'position']
        add('default_kind', 'Default value type', 'select', ['SET DEFAULT'],
            True, 'TEXT', DEFAULTS)
        add('default_value', 'Default value', 'text',
            ['SET DEFAULT'], False, '')
        add('time_precision', 'Time precision', 'number', ['SET DEFAULT'],
            help_text='Optional fractional digits, 0–3; '
            'empty uses the native default.')
        add('data_type', 'Data type', 'select', ['TYPE', 'TYPE COMPUTED'],
            True, None, TYPES,
            help_text='Prefilled from native metadata when known. Otherwise '
            'choose the intended type explicitly; no type is assumed.')
        fields[-1]['require_explicit_choice'] = True
        for name, title, kind, default, help_text in (
                ('domain', 'Domain name', 'text', '', 'For DOMAIN only.'),
                ('length', 'Length', 'number', 1,
                 'Character/binary types only.'),
                ('precision', 'Precision', 'number', None,
                 'NUMERIC/DECIMAL: 1–38; FLOAT: 1–53; DECFLOAT: 16 or 34.'),
                ('scale', 'Scale', 'number', 0, 'NUMERIC/DECIMAL only.'),
                ('blob_subtype', 'BLOB subtype', 'number', 0,
                 'For computed BLOB results: 0 binary, 1 text, '
                 'or a native custom subtype.'),
                ('segment_size', 'BLOB segment size', 'number', None,
                 'Optional segment size, 0–65535.'),
                ('character_set', 'Character set', 'text', '',
                 'Optional for CHAR, VARCHAR and text BLOB.')):
            add(name, title, kind, ['TYPE', 'TYPE COMPUTED'], False,
                default, help_text=help_text)
        add('time_zone', 'Time zone type', 'select', ['TYPE', 'TYPE COMPUTED'],
            False, 'WITHOUT TIME ZONE',
            ('WITHOUT TIME ZONE', 'WITH TIME ZONE'))
        add('expression', 'Computed expression', 'code',
            ['COMPUTED', 'TYPE COMPUTED'], True)
        fields[-1]['initial_value_path'] = ['computed_source']
        add('generation', 'Identity generation', 'select', ['IDENTITY'],
            False, 'UNCHANGED', ('UNCHANGED', 'ALWAYS', 'BY DEFAULT'))
        add('restart', 'Restart identity', 'select', ['IDENTITY'],
            False, 'UNCHANGED', ('UNCHANGED', 'ORIGINAL', 'WITH VALUE'))
        add('restart_value', 'Restart value (signed 64-bit)', 'text',
            ['IDENTITY'], help_text='Used only with WITH VALUE.')
        add('increment', 'Identity increment (signed 32-bit)', 'text',
            ['IDENTITY'], help_text='Empty preserves the current increment.')
        secondary = {
            'domain': ('data_type', ['DOMAIN']),
            'length': ('data_type', ['CHAR', 'VARCHAR', 'NCHAR',
                                     'NCHAR VARYING', 'BINARY', 'VARBINARY']),
            'precision': ('data_type', ['NUMERIC', 'DECIMAL', 'FLOAT',
                                        'DECFLOAT']),
            'scale': ('data_type', ['NUMERIC', 'DECIMAL']),
            'character_set': ('data_type', ['CHAR', 'VARCHAR', 'BLOB']),
            'blob_subtype': ('data_type', ['BLOB']),
            'segment_size': ('data_type', ['BLOB']),
            'time_zone': ('data_type', ['TIME', 'TIMESTAMP']),
            'default_value': ('default_kind', [
                'TEXT', 'BINARY', 'NUMBER', 'DATE', 'TIME', 'TIMESTAMP']),
            'time_precision': ('default_kind', list(TIMED_DEFAULTS)),
            'restart_value': ('restart', ['WITH VALUE']),
        }
        for item in fields:
            if item['field_id'] in {
                    'data_type', 'domain', 'length', 'precision', 'scale',
                    'blob_subtype', 'segment_size', 'character_set',
                    'time_zone'}:
                item['initial_value_path'] = ['type_editor', item['field_id']]
                item['submit_unchanged'] = True
            if item['field_id'] in ('length', 'domain'):
                item['required'] = True
            if item['field_id'] == 'data_type':
                for option in item['options']:
                    if option['value'] in ('BLOB', 'DOMAIN'):
                        option['visible_when'] = {
                            'field_id': 'action', 'equals':
                            'TYPE COMPUTED' if option['value'] == 'BLOB'
                            else 'TYPE'}
            if item['field_id'] in secondary:
                controller, choices = secondary[item['field_id']]
                item['visible_when'] = {'all': [item['visible_when'], {
                    'field_id': controller, 'in': choices}]}
    return {'form_id': 'firebird.column.' + operation,
            'title': 'Edit column comment' if operation == 'comment' else
            'Alter Firebird column', 'fields': fields}
