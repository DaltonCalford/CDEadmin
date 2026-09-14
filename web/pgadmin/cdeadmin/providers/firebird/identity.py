##########################################################################
# CDEadmin - Multi-engine Database Administration
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
##########################################################################

"""Native catalog identity and checked column rename transitions."""

from pgadmin.cdeadmin.sdk.relational import RelationalClientError
from .mappings import identifier


def catalog_resource_id(kind, path, name):
    """Escape delimiters, retaining existing IDs for ordinary identifiers."""
    return ':'.join(str(value).replace('%', '%25').replace(':', '%3A')
                    for value in (kind, *path, name))


def column_rename(path, new_name):
    if len(path) != 2:
        raise RelationalClientError('A Firebird column belongs to one table')
    for name in (*path, new_name):
        identifier(name)
    if path[-1] == new_name:
        raise RelationalClientError('The new column name must be different')
    return {
        'resource_kind': 'column', 'table': path[0],
        'old_name': path[1], 'new_name': new_name,
        'previous_resource_id': catalog_resource_id(
            'column', path[:1], path[1]),
        'resource_id': catalog_resource_id('column', path[:1], new_name),
    }


def column_identity(cursor, transition, name):
    cursor.execute(
        'SELECT R.RDB$RELATION_ID, F.RDB$FIELD_ID '
        'FROM RDB$RELATION_FIELDS F JOIN RDB$RELATIONS R '
        'ON R.RDB$RELATION_NAME = F.RDB$RELATION_NAME '
        'WHERE F.RDB$RELATION_NAME = ? AND F.RDB$FIELD_NAME = ?',
        (transition['table'], name),
    )
    rows = list(cursor.fetchall())
    if (len(rows) != 1 or len(rows[0]) != 2 or
            any(type(value) is not int for value in rows[0])):
        raise RelationalClientError(
            'Firebird column identity could not be verified')
    return tuple(rows[0])


def verify_column_rename(cursor, transition, previous):
    current = column_identity(cursor, transition, transition['new_name'])
    if current != previous:
        raise RelationalClientError('Firebird column identity changed')
    cursor.execute(
        'SELECT COUNT(*) FROM RDB$RELATION_FIELDS '
        'WHERE RDB$RELATION_NAME = ? AND RDB$FIELD_NAME = ?',
        (transition['table'], transition['old_name']),
    )
    if cursor.fetchone()[0] != 0:
        raise RelationalClientError(
            'The previous Firebird column still exists')
    return {**transition, 'relation_id': current[0], 'field_id': current[1],
            'native_identity_verified': True}
