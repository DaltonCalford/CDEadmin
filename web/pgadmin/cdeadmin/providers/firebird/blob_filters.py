"""Firebird BLOB filter declarations; distinct from legacy UDF signatures."""

from collections.abc import Mapping

from pgadmin.cdeadmin.sdk.relational import RelationalClientError
from .character_metadata import identifier, literal, text
from .columns import integer
from .external_functions import external_name


OPERATIONS = frozenset({'inspect', 'create', 'comment', 'drop'})
WARNING = (
    'BLOB filters execute native server code. The library and exported '
    'filter must already be installed and allowed by Firebird UdfAccess. '
    'This task does not install libraries or change server security. '
    'Firebird has no ALTER FILTER statement; changing a declaration '
    'requires an explicit drop and a new declaration. Loaded filters can '
    'remain in the native database cache after their declaration is dropped, '
    'until the database is unloaded. This task does not unload the database '
    'or disconnect other users. The library must match the exact server ABI.')


def subtype(draft, prefix):
    mode = draft.get(prefix + '_mode', 'NUMBER')
    number, mnemonic = (draft.get(prefix + '_subtype'),
                        draft.get(prefix + '_mnemonic'))
    if mode == 'NUMBER':
        if mnemonic not in (None, ''):
            raise RelationalClientError('Subtype number and mnemonic conflict')
        # parse.y signed_short_integer. Non-negative values are reserved;
        # custom subtype numbers are negative. Do not forbid native TEXT (1).
        return integer(number, -32768, 32767, prefix.title() + ' subtype')
    if mode == 'MNEMONIC':
        if number not in (None, ''):
            raise RelationalClientError(
                'Subtype mnemonic and number conflict')
        return identifier(mnemonic)
    raise RelationalClientError(
        'Choose a subtype number or registered mnemonic')


def compile_operation(operation, draft, target=None):
    if (not isinstance(operation, str) or operation not in OPERATIONS or
            operation == 'inspect' or not isinstance(draft, Mapping)):
        raise RelationalClientError('Unknown BLOB filter task')
    allowed = {
        'create': {'name', 'input_mode', 'input_subtype', 'input_mnemonic',
                   'output_mode', 'output_subtype', 'output_mnemonic',
                   'entrypoint', 'module_name', 'description'},
        'comment': {'description'}, 'drop': {'confirmation'},
    }[operation]
    if set(draft) - allowed:
        raise RelationalClientError('Unknown BLOB filter form fields')
    name = (draft.get('name') if operation == 'create' else
            (target or {}).get('display_name'))
    quoted = identifier(name)
    if operation == 'drop':
        if draft.get('confirmation') != name:
            raise RelationalClientError('Confirm the exact BLOB filter name')
        return ['DROP FILTER ' + quoted]
    if operation == 'comment':
        comment = text(draft.get('description', ''), 'Comment')
        return ['COMMENT ON FILTER ' + quoted + ' IS ' + (
            literal(comment) if comment else 'NULL')]
    source = ('DECLARE FILTER ' + quoted + ' INPUT_TYPE ' +
              subtype(draft, 'input') + ' OUTPUT_TYPE ' +
              subtype(draft, 'output') + ' ENTRY_POINT ' +
              external_name(draft.get('entrypoint'), 'Entry point') +
              ' MODULE_NAME ' +
              external_name(draft.get('module_name'), 'Library'))
    statements = [source]
    if draft.get('description') is not None:
        statements.extend(compile_operation('comment', {
            'description': draft['description']}, {'display_name': name}))
    return statements


def form(operation, field):
    if operation == 'create':
        fields = [field('name', 'BLOB filter name', 'text', True, WARNING)]
        for prefix in ('input', 'output'):
            fields.append(field(prefix + '_mode', prefix.title() + ' subtype '
                                'notation', 'select', True, default='NUMBER',
                                options=('NUMBER', 'MNEMONIC')))
            fields.extend([
                {**field(prefix + '_subtype', prefix.title() + ' subtype',
                         'number', True, 'Signed 16-bit subtype. Custom '
                         'subtypes are negative; non-negative values are '
                         'reserved for the engine.'),
                 'visible_when': {'field_id': prefix + '_mode',
                                  'equals': 'NUMBER'}},
                {**field(prefix + '_mnemonic', prefix.title() + ' mnemonic',
                         'text', True, 'Exact registered RDB$TYPES name '
                         '(for example TEXT). This task does not create '
                         'a subtype or modify the system catalog.'),
                 'visible_when': {'field_id': prefix + '_mode',
                                  'equals': 'MNEMONIC'}},
            ])
        fields.extend([
            field('entrypoint', 'Exported entry point', 'text', True,
                  'At most 255 UTF-8 bytes.'),
            field('module_name', 'Installed library', 'text', True,
                  'At most 255 UTF-8 bytes.'),
            field('description', 'Comment', 'multiline'),
        ])
    elif operation == 'comment':
        fields = [{**field('description', 'Comment', 'multiline', False,
                           'Empty removes the comment.', ''),
                   'initial_value_path': ['description'],
                   'submit_unchanged': True}]
    elif operation == 'drop':
        fields = [field('confirmation', 'Confirm BLOB filter name', 'text',
                        True, 'Removes this database declaration only, not '
                        'the installed library or already cached filter code. '
                        'Enter the exact name.')]
    else:
        raise RelationalClientError('Unknown BLOB filter form')
    return {'form_id': 'firebird.blob-filter.' + operation,
            'title': operation.title() + ' BLOB filter', 'fields': fields}
