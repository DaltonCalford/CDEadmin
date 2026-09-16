"""Native Firebird view replacement tasks, distinct from ALTER and DROP."""

from collections.abc import Mapping

from pgadmin.cdeadmin.sdk.relational import RelationalClientError
from .character_metadata import identifier
from .packages import source


OPERATIONS = frozenset({'create_or_alter', 'recreate'})
WARNING = (
    'RECREATE drops and creates the view. Existing grants, comments and '
    'view triggers are not automatically restored. Native dependency and '
    'permission checks apply. Review the definition and ordered column names.')


def catalog_columns(columns):
    """Order catalog columns numerically, including text positions."""
    if not isinstance(columns, list) or not columns:
        raise RelationalClientError('View column metadata is unavailable')
    normalized = []
    for column in columns:
        if not isinstance(column, Mapping):
            raise RelationalClientError(
                'View column positions are unavailable')
        position = column.get('position')
        if isinstance(position, str) and position.isascii() and (
                position.isdigit()):
            position = int(position)
        if type(position) is not int:
            raise RelationalClientError(
                'View column positions are unavailable')
        normalized.append({**column, 'position': position})
    ordered = sorted(normalized, key=lambda column: column['position'])
    if [column['position'] for column in ordered] != list(range(len(ordered))):
        raise RelationalClientError('View column positions are incomplete')
    names = [identifier(column.get('name')) for column in ordered]
    if len(set(names)) != len(names):
        raise RelationalClientError('Duplicate view column metadata')
    return ordered


def recreation_sql(name, definition, columns):
    """Render column identity separately from the native query source."""
    names = [identifier(column['name']) for column in catalog_columns(columns)]
    # A newline before the terminator keeps it outside a trailing SQL comment.
    return ('CREATE VIEW ' + identifier(name) + ' (' + ', '.join(names) +
            ') AS\n' + source(definition, 'View query') + '\n;')


def compile_operation(operation, draft, target=None):
    if (not isinstance(operation, str) or operation not in OPERATIONS or
            not isinstance(draft, Mapping)):
        raise RelationalClientError('Unknown Firebird view replacement task')
    allowed = {'definition', 'columns'} | (
        {'name'} if operation == 'create_or_alter' else {'confirmation'})
    if set(draft) - allowed:
        raise RelationalClientError('Unknown Firebird view form fields')
    if operation == 'create_or_alter':
        name = draft.get('name')
    else:
        if (not isinstance(target, Mapping) or
                target.get('resource_kind') != 'view'):
            raise RelationalClientError('An inspected view is required')
        name = target.get('display_name')
        if draft.get('confirmation') != name:
            raise RelationalClientError('Confirm the exact view name')
    quoted = identifier(name)
    columns = draft.get('columns', [])
    if not isinstance(columns, list):
        raise RelationalClientError('View columns must be an ordered list')
    names = []
    for column in columns:
        if not isinstance(column, Mapping) or set(column) != {'name'}:
            raise RelationalClientError(
                'Each view column needs its exact name')
        names.append(identifier(column['name']))
    if len(set(names)) != len(names):
        raise RelationalClientError('Duplicate view column names')
    column_sql = ' (' + ', '.join(names) + ')' if names else ''
    definition = source(draft.get('definition'), 'View query')
    # One native statement is prepared; Firebird owns SELECT/CTE/check-option
    # syntax and errors. Never run this input as a multi-statement script.
    command = ('CREATE OR ALTER' if operation == 'create_or_alter'
               else 'RECREATE')
    return command + ' VIEW ' + quoted + column_sql + ' AS\n' + definition


def form(operation, field):
    if operation not in OPERATIONS:
        raise RelationalClientError('Unknown Firebird view form')
    fields = []
    if operation == 'create_or_alter':
        fields.append(field('name', 'View name', 'text', True,
                            'Create if absent; alter if already present.'))
    fields.extend([
        {**field('definition', 'View query', 'code', True,
                 'Complete SELECT or WITH query, including WITH CHECK OPTION '
                 'when wanted. Do not include CREATE VIEW, AS or SET TERM.'),
         'initial_value_path': ['definition']},
        {**field('columns', 'Ordered view column names', 'json', False,
                 'Empty derives names from the query. Preserve explicit '
                 'names when the query has unnamed or differently named '
                 'expressions.', []),
         'json_type': 'array', 'initial_value_path': ['view_columns'],
         'submit_unchanged': True,
         'array_editor': {'item_kind': 'object', 'fields': [
             field('name', 'Column name', 'text', True)]}},
    ])
    if operation == 'recreate':
        fields.append(field('confirmation', 'Confirm view name', 'text', True,
                            WARNING))
    return {'form_id': 'firebird.view.' + operation,
            'title': 'Create or alter view' if operation == 'create_or_alter'
            else 'Recreate view', 'fields': fields}
