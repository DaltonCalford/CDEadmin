"""Firebird 5 legacy UDF declarations, not PSQL or UDR function bodies."""

from collections.abc import Mapping

from pgadmin.cdeadmin.sdk.relational import RelationalClientError
from . import columns
from .character_metadata import identifier, literal, text


OPERATIONS = frozenset({'inspect', 'create', 'alter', 'comment', 'drop'})
TYPES = tuple(name for name in columns.TYPES if name !=
              'DOMAIN') + ('CSTRING',)
INPUT_MECHANISMS = ('REFERENCE', 'DESCRIPTOR', 'SCALAR_ARRAY', 'NULL')
RETURN_MECHANISMS = ('REFERENCE', 'VALUE', 'DESCRIPTOR', 'FREE_IT',
                     'DESCRIPTOR_FREE_IT')
TYPE_KEYS = {'data_type', 'length', 'precision', 'scale', 'time_zone',
             'character_set'}
WARNING = (
    'Legacy external functions execute native server code. The library must '
    'already be installed and permitted by Firebird UdfAccess; this task '
    'does not install code or change server security. Argument and return '
    'mechanisms must match the installed function ABI exactly. Prefer PSQL '
    'or UDR functions for new development.')


def external_name(value, label):
    value = text(value, label)
    if not value or len(value.encode('utf-8')) > 255:
        raise RelationalClientError(label + ' requires 1 to 255 UTF-8 bytes')
    return literal(value)


def type_sql(value):
    if not isinstance(value, Mapping) or set(value) - TYPE_KEYS:
        raise RelationalClientError('Unknown UDF data type fields')
    name = value.get('data_type')
    if not isinstance(name, str) or name not in TYPES:
        raise RelationalClientError('Choose a native UDF data type')
    allowed = {'data_type'}
    if name in ('CHAR', 'VARCHAR', 'NCHAR', 'NCHAR VARYING', 'BINARY',
                'VARBINARY', 'CSTRING'):
        allowed.add('length')
    if name in ('NUMERIC', 'DECIMAL', 'FLOAT', 'DECFLOAT'):
        allowed.add('precision')
    if name in ('NUMERIC', 'DECIMAL'):
        allowed.add('scale')
    if name in ('TIME', 'TIMESTAMP'):
        allowed.add('time_zone')
    if name in ('CHAR', 'VARCHAR', 'CSTRING'):
        allowed.add('character_set')
    if any(value.get(key) not in (None, '') for key in TYPE_KEYS - allowed):
        raise RelationalClientError('UDF data type fields conflict')
    if name == 'BLOB':
        return 'BLOB'
    if name == 'CSTRING':
        source = 'CSTRING(' + columns.integer(
            value.get('length'), 1, 32767, 'CSTRING length') + ')'
        if value.get('character_set'):
            source += ' CHARACTER SET ' + identifier(value['character_set'])
        return source
    return columns.data_type(value)


def compile_operation(operation, draft, target=None):
    if (not isinstance(operation, str) or operation not in OPERATIONS or
            operation == 'inspect' or not isinstance(draft, Mapping)):
        raise RelationalClientError('Unknown legacy external function task')
    allowed = {
        'create': {'name', 'arguments', 'return_mode', 'return_parameter',
                   'return_mechanism', 'entrypoint', 'module_name',
                   'description'} | {'return_' + key for key in TYPE_KEYS},
        'alter': {'alter_target', 'entrypoint', 'module_name'},
        'comment': {'description'}, 'drop': {'confirmation'},
    }[operation]
    if set(draft) - allowed:
        raise RelationalClientError('Unknown external function form fields')
    name = (draft.get('name') if operation == 'create' else
            (target or {}).get('display_name'))
    quoted = identifier(name)
    if operation == 'drop':
        if draft.get('confirmation') != name:
            raise RelationalClientError('Confirm the exact function name')
        return ['DROP EXTERNAL FUNCTION ' + quoted]
    if operation == 'comment':
        comment = text(draft.get('description', ''), 'Comment')
        return ['COMMENT ON FUNCTION ' + quoted + ' IS ' + (
            literal(comment) if comment else 'NULL')]
    if operation == 'alter':
        selection = draft.get('alter_target', 'BOTH')
        if selection not in ('BOTH', 'ENTRY_POINT', 'MODULE_NAME'):
            raise RelationalClientError('Choose the library binding to alter')
        source = 'ALTER EXTERNAL FUNCTION ' + quoted
        for key, clause in (('entrypoint', 'ENTRY_POINT'),
                            ('module_name', 'MODULE_NAME')):
            if selection in ('BOTH', clause):
                source += ' ' + clause + ' ' + external_name(
                    draft.get(key), clause)
            elif draft.get(key) not in (None, ''):
                raise RelationalClientError('UDF alteration fields conflict')
        return [source]
    arguments = draft.get('arguments', [])
    if not isinstance(arguments, list) or len(arguments) > 15:
        raise RelationalClientError(
            'A legacy UDF accepts at most 15 arguments')
    rendered = []
    for argument in arguments:
        if (not isinstance(argument, Mapping) or
                set(argument) - TYPE_KEYS - {'mechanism'}):
            raise RelationalClientError('Unknown UDF argument fields')
        mechanism = argument.get('mechanism', 'REFERENCE')
        if mechanism not in INPUT_MECHANISMS:
            raise RelationalClientError('Invalid UDF input mechanism')
        source = type_sql({key: val for key, val in argument.items()
                           if key != 'mechanism'})
        if mechanism != 'REFERENCE':
            source += (' NULL' if mechanism == 'NULL' else
                       ' BY ' + mechanism)
        rendered.append(source)
    mode = draft.get('return_mode', 'TYPE')
    return_fields = {key: draft['return_' + key] for key in TYPE_KEYS
                     if 'return_' + key in draft}
    if mode == 'PARAMETER':
        if any(value not in (None, '') for value in return_fields.values()):
            raise RelationalClientError('Return parameter and type conflict')
        if draft.get('return_mechanism') not in (None, ''):
            raise RelationalClientError(
                'Return parameter has no mechanism clause')
        position = int(columns.integer(draft.get('return_parameter'), 1,
                                       len(arguments), 'Return parameter'))
        if arguments[position - 1].get('mechanism') == 'SCALAR_ARRAY':
            raise RelationalClientError('SCALAR_ARRAY cannot be returned')
        returned = 'PARAMETER ' + str(position)
    elif mode == 'TYPE':
        if draft.get('return_parameter') not in (None, ''):
            raise RelationalClientError('Return type and parameter conflict')
        returned = type_sql(return_fields)
        mechanism = draft.get('return_mechanism', 'REFERENCE')
        if mechanism not in RETURN_MECHANISMS:
            raise RelationalClientError('Invalid UDF return mechanism')
        if return_fields['data_type'] == 'BLOB' and len(arguments) > 14:
            raise RelationalClientError(
                'A BLOB return leaves 14 input arguments')
        forbidden_value = return_fields['data_type'] in (
            'CHAR', 'VARCHAR', 'NCHAR', 'NCHAR VARYING', 'BINARY',
            'VARBINARY', 'CSTRING', 'BLOB')
        forbidden_value |= (return_fields['data_type'] == 'TIMESTAMP' and
                            return_fields.get('time_zone',
                                              'WITHOUT TIME ZONE') ==
                            'WITHOUT TIME ZONE')
        if mechanism == 'VALUE' and forbidden_value:
            raise RelationalClientError('This UDF type cannot return BY VALUE')
        returned += {'REFERENCE': '', 'VALUE': ' BY VALUE',
                     'DESCRIPTOR': ' BY DESCRIPTOR', 'FREE_IT': ' FREE_IT',
                     'DESCRIPTOR_FREE_IT': ' BY DESCRIPTOR FREE_IT'}[mechanism]
    else:
        raise RelationalClientError('Choose a UDF return type or parameter')
    source = 'DECLARE EXTERNAL FUNCTION ' + quoted
    if rendered:
        source += ' ' + ', '.join(rendered)
    source += ' RETURNS ' + returned + ' ENTRY_POINT ' + external_name(
        draft.get('entrypoint'), 'Entry point')
    source += ' MODULE_NAME ' + external_name(
        draft.get('module_name'), 'Library')
    statements = [source]
    if draft.get('description') is not None:
        statements.extend(compile_operation('comment', {
            'description': draft['description']}, {'display_name': name}))
    return statements


def type_fields(field, prefix=''):
    title_prefix = 'Return ' if prefix else ''
    fields = [field(prefix + 'data_type', title_prefix + 'data type'
                    if prefix else 'Data type', 'select', True,
                    default='INTEGER', options=TYPES)]
    for key, title, kind, types, required, default, options in (
            ('length', 'Length', 'number',
             ['CHAR', 'VARCHAR', 'NCHAR', 'NCHAR VARYING', 'BINARY',
              'VARBINARY', 'CSTRING'], True, 32, None),
            ('precision', 'Precision', 'number',
             ['NUMERIC', 'DECIMAL', 'FLOAT', 'DECFLOAT'], False, None, None),
            ('scale', 'Scale', 'number', [
             'NUMERIC', 'DECIMAL'], False, 0, None),
            ('character_set', 'Character set', 'text',
             ['CHAR', 'VARCHAR', 'CSTRING'], False, None, None),
            ('time_zone', 'Time zone', 'select', ['TIME', 'TIMESTAMP'], True,
             'WITHOUT TIME ZONE', ('WITHOUT TIME ZONE', 'WITH TIME ZONE'))):
        fields.append({**field(prefix + key, title_prefix + title.lower()
                               if prefix else title, kind, required,
                               default=default, options=options),
                       'visible_when': {'field_id': prefix + 'data_type',
                                        'in': types}})
    return fields


def form(operation, field):
    if operation == 'create':
        fields = [field('name', 'Function name', 'text', True, WARNING),
                  {**field('arguments', 'Ordered input arguments', 'json',
                           False, 'The order and mechanisms must match the '
                           'installed function. At most 15 inputs, or 14 '
                           'with a BLOB return.', []),
                   'json_type': 'array', 'array_editor': {
                       'item_kind': 'object', 'fields': [
                           *type_fields(field),
                           field('mechanism', 'Input mechanism', 'select',
                                 True, default='REFERENCE',
                                 options=INPUT_MECHANISMS)]}},
                  field('return_mode', 'Return declaration', 'select', True,
                        default='TYPE', options=('TYPE', 'PARAMETER'))]
        for item in [*type_fields(field, 'return_'),
                     field('return_mechanism', 'Return mechanism', 'select',
                           True, default='REFERENCE',
                           options=RETURN_MECHANISMS)]:
            condition = {'field_id': 'return_mode', 'equals': 'TYPE'}
            if item.get('visible_when'):
                condition = {'all': [condition, item['visible_when']]}
            fields.append({**item, 'visible_when': condition})
        fields.append({**field('return_parameter', 'Return parameter position',
                               'number', True,
                               'One-based input position. For numeric output '
                               'parameters use a matching BY DESCRIPTOR ABI: '
                               'Firebird 5.0.4 does not copy ordinary numeric '
                               'reference output back into the result.'),
                       'visible_when': {'field_id': 'return_mode',
                                        'equals': 'PARAMETER'}})
    elif operation == 'alter':
        fields = [field(
            'alter_target', 'Alter library binding', 'select', True,
            WARNING + ' Native ALTER cannot change the signature.',
            'BOTH', options=('BOTH', 'ENTRY_POINT', 'MODULE_NAME'))]
    elif operation == 'comment':
        fields = [{**field('description', 'Comment', 'multiline', False,
                           'Empty removes the comment.', ''),
                   'initial_value_path': ['description'],
                   'submit_unchanged': True}]
    elif operation == 'drop':
        fields = [field('confirmation', 'Confirm function name', 'text', True,
                        'Enter the exact name. Dependencies and permissions '
                        'remain enforced by Firebird.')]
    else:
        raise RelationalClientError('Unknown external function form')
    if operation in ('create', 'alter'):
        for key, title, clause in (('entrypoint', 'Exported entry point',
                                    'ENTRY_POINT'),
                                   ('module_name', 'Installed library',
                                    'MODULE_NAME')):
            item = field(key, title, 'text', True, 'At most 255 UTF-8 bytes.')
            if operation == 'alter':
                item.update(initial_value_path=[key], submit_unchanged=True,
                            visible_when={'field_id': 'alter_target',
                                          'in': ['BOTH', clause]})
            fields.append(item)
        if operation == 'create':
            fields.append(field('description', 'Comment', 'multiline'))
    return {'form_id': 'firebird.external-function.' + operation,
            'title': operation.title() + ' legacy external function',
            'fields': fields}
