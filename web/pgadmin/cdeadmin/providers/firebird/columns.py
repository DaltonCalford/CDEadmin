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
                        'existing data and dependent objects.',
                        'POSITION', options=ACTIONS)]

        def add(name, title, kind, actions, required=False, default=None,
                options=None, help_text=''):
            item = field(name, title, kind, required, help_text, default,
                         options=options)
            item['visible_when'] = {'field_id': 'action', 'in': actions}
            fields.append(item)

        add('position', 'Position (one-based)', 'number',
            ['POSITION'], True, 1)
        add('default_kind', 'Default value type', 'select', ['SET DEFAULT'],
            True, 'TEXT', DEFAULTS)
        add('default_value', 'Default value', 'text',
            ['SET DEFAULT'], False, '')
        add('time_precision', 'Time precision', 'number', ['SET DEFAULT'],
            help_text='Optional fractional digits, 0–3; '
            'empty uses the native default.')
        add('data_type', 'Data type', 'select', ['TYPE', 'TYPE COMPUTED'],
            True, 'INTEGER', TYPES)
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
