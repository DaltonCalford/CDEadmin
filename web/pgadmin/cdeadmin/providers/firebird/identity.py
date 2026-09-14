##########################################################################
# CDEadmin - Multi-engine Database Administration
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
##########################################################################

"""Native catalog identity and checked rename transitions."""

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


def domain_rename(path, new_name):
    if len(path) != 1:
        raise RelationalClientError(
            'A Firebird domain has no schema qualifier')
    identifier(path[0])
    identifier(new_name)
    if path[0] == new_name:
        raise RelationalClientError('The new domain name must be different')
    return {
        'resource_kind': 'domain', 'old_name': path[0], 'new_name': new_name,
        'previous_resource_id': catalog_resource_id('domain', [], path[0]),
        'resource_id': catalog_resource_id('domain', [], new_name),
    }


def domain_identity(cursor, transition, name):
    # This key is compared only inside one native transaction. It is not a
    # durable domain ID, is never serialized, and is discarded before return.
    cursor.execute('SELECT F.RDB$DB_KEY FROM RDB$FIELDS F '
                   'WHERE F.RDB$FIELD_NAME = ?', (name,))
    rows = list(cursor.fetchall())
    if (len(rows) != 1 or len(rows[0]) != 1 or
            not isinstance(rows[0][0], (bytes, bytearray, memoryview)) or
            len(rows[0][0]) != 8):
        raise RelationalClientError(
            'Firebird domain record identity could not be verified')
    return bytes(rows[0][0])


def rename_identity(cursor, transition, name):
    if transition['resource_kind'] == 'column':
        return column_identity(cursor, transition, name)
    if transition['resource_kind'] == 'domain':
        return domain_identity(cursor, transition, name)
    raise RelationalClientError('Unsupported Firebird rename identity')


def verify_rename(cursor, transition, previous):
    if transition['resource_kind'] == 'column':
        return verify_column_rename(cursor, transition, previous)
    if transition['resource_kind'] != 'domain':
        raise RelationalClientError('Unsupported Firebird rename identity')
    current = domain_identity(cursor, transition, transition['new_name'])
    if current != previous:
        raise RelationalClientError('Firebird domain record identity changed')
    cursor.execute('SELECT COUNT(*) FROM RDB$FIELDS WHERE RDB$FIELD_NAME = ?',
                   (transition['old_name'],))
    if cursor.fetchone()[0] != 0:
        raise RelationalClientError(
            'The previous Firebird domain still exists')
    return {**transition, 'native_identity_verified': True,
            'native_identity_kind': 'record-key-within-transaction',
            'record_key_retained': False}


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
