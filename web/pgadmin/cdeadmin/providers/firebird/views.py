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


def grid_update_identity(connection, name, *, operation='update'):
    """Admit direct PK-preserving views; prepare, never execute, probes.

    This is deliberately not a general native updatability classifier. Joins,
    nested views and trigger-backed views need separate row identity contracts.
    Return key aliases and writable columns (empty for DELETE). Prepare UPDATE
    and DELETE independently; neither implies permission for the other.
    Retain this connection/transaction for subsequent mutations.
    """
    import firebird.driver as native
    if operation not in {'update', 'delete'}:
        raise RelationalClientError('view row operation is unavailable')
    with connection.cursor() as cursor:
        cursor.execute(
            'SELECT TRIM(V.RDB$RELATION_NAME), V.RDB$VIEW_CONTEXT '
            'FROM RDB$VIEW_RELATIONS V JOIN RDB$RELATIONS R ON '
            'R.RDB$RELATION_NAME = V.RDB$RELATION_NAME '
            'WHERE V.RDB$VIEW_NAME = ?', (name,))
        sources = cursor.fetchall()
        if len(sources) != 1:
            return (), ()
        base, context = sources[0]
        cursor.execute('SELECT RDB$VIEW_BLR, RDB$RELATION_TYPE FROM '
                       'RDB$RELATIONS WHERE RDB$RELATION_NAME = ?', (base,))
        relation = cursor.fetchone()
        if not relation or relation[0] is not None or relation[1] != 0:
            return (), ()
        cursor.execute('SELECT COUNT(*) FROM RDB$TRIGGERS WHERE '
                       'RDB$RELATION_NAME = ? AND '
                       'COALESCE(RDB$TRIGGER_INACTIVE, 0) = 0', (name,))
        if cursor.fetchone()[0]:
            return (), ()
        cursor.execute('SELECT TRIM(S.RDB$FIELD_NAME) FROM '
                       'RDB$RELATION_CONSTRAINTS C JOIN RDB$INDEX_SEGMENTS S '
                       'ON S.RDB$INDEX_NAME = C.RDB$INDEX_NAME WHERE '
                       "C.RDB$CONSTRAINT_TYPE = 'PRIMARY KEY' AND "
                       'C.RDB$RELATION_NAME = ? ORDER BY S.RDB$FIELD_POSITION',
                       (base,))
        primary = [row[0] for row in cursor.fetchall()]
        if not primary:
            return (), ()
        cursor.execute('SELECT TRIM(RDB$FIELD_NAME), TRIM(RDB$BASE_FIELD), '
                       'RDB$VIEW_CONTEXT FROM RDB$RELATION_FIELDS WHERE '
                       'RDB$RELATION_NAME = ? ORDER BY RDB$FIELD_POSITION',
                       (name,))
        fields = cursor.fetchall()
        keys = []
        for key in primary:
            aliases = [field for field, source, field_context in fields
                       if source == key and field_context == context]
            if len(aliases) != 1:
                return (), ()
            keys.append(aliases[0])
        if operation == 'delete':
            statement = None
            try:
                statement = cursor.prepare(
                    f'DELETE FROM {identifier(name)} WHERE 1 = 0')
                return tuple(keys), ()
            except native.DatabaseError:
                return (), ()
            finally:
                if statement is not None:
                    statement.free()
        editable = []
        for field, _source, _context in fields:
            # Preparation invokes native shape, column and permission checks.
            # No statement runs, even with an always-false predicate.
            statement = None
            try:
                statement = cursor.prepare(
                    f'UPDATE {identifier(name)} SET {identifier(field)} = ? '
                    'WHERE 1 = 0')
                editable.append(field)
            except native.DatabaseError:
                continue
            finally:
                if statement is not None:
                    statement.free()
        return (tuple(keys), tuple(editable)) if editable else ((), ())


def metadata_warnings(columns):
    """Disclose observed noncanonical UTF8 CHAR lengths, not SQL guesses.

    Native UTF8 has a maximum width of four bytes per character. This checks
    catalog geometry only; a canonical length cannot prove a view is readable.
    In particular, a four-character literal incorrectly stored as CHAR(1)
    has internally consistent lengths and cannot be diagnosed here.
    """
    warnings = []
    if not isinstance(columns, list):
        return warnings
    for column in columns:
        if not isinstance(column, Mapping):
            continue
        if (str(column.get('field_type')) != '14' or
                column.get('character_set') != 'UTF8'):
            continue
        length = column.get('field_length')
        characters = column.get('character_length')
        if isinstance(length, str) and length.isascii() and length.isdigit():
            length = int(length)
        if (isinstance(characters, str) and characters.isascii() and
                characters.isdigit()):
            characters = int(characters)
        if type(length) is not int or length <= 0:
            continue
        if (type(characters) is int and characters > 0 and
                length == characters * 4):
            continue
        warnings.append(
            f'View column {column.get("name", "(unknown)")} has inconsistent '
            'or missing native UTF8 CHAR length metadata. Firebird may reject '
            'result fetching with string truncation. Review the stored view '
            'definition and native column properties; CDEadmin has not '
            'rewritten the SQL or changed the database.')
    return warnings


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
