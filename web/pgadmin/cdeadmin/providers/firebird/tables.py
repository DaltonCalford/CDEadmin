##########################################################################
# CDEadmin - Multi-engine Database Administration
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
##########################################################################

"""Firebird table kind and native security/publication attributes."""

from collections.abc import Mapping

from pgadmin.cdeadmin.sdk.relational import RelationalClientError
from .mappings import identifier, literal

TABLE_TYPES = ('PERSISTENT', 'GLOBAL TEMPORARY', 'EXTERNAL')
CREATE_KEYS = ('table_type', 'on_commit', 'external_file', 'sql_security',
               'publication')
ALTER_KEYS = ('sql_security', 'publication')


def warnings(request):
    """Explain external-file effects and native transaction limitations."""
    draft = request.get('draft', {})
    options = draft.get('options', {})
    target = request.get('target_resource') or {}
    native = target.get('native')
    if not isinstance(native, Mapping):
        extensions = target.get('extensions')
        extension = (extensions.get('firebird') if
                     isinstance(extensions, Mapping) else None)
        native = (extension.get('native') if
                  isinstance(extension, Mapping) else None)
    external = (options.get('table_type') == 'EXTERNAL' or
                draft.get('table_type') == 'EXTERNAL' or
                isinstance(native, Mapping) and
                str(native.get('relation_type')) == '2')
    if not external or request.get('resource_kind') != 'table':
        return []
    return [
        'Firebird external-table inserts write to a server file and are not '
        'undone by rollback. UPDATE and DELETE are not supported. '
        'Dropping the table removes its database definition, not the file.']


def create(name, definitions, options):
    kind = options.get('table_type', 'PERSISTENT')
    if kind not in TABLE_TYPES:
        raise RelationalClientError('Choose a native Firebird table kind')
    security = options.get('sql_security', 'INHERIT')
    if security not in ('INHERIT', 'DEFINER', 'INVOKER'):
        raise RelationalClientError('Invalid Firebird SQL security mode')
    publication = options.get('publication', 'DEFAULT')
    if publication not in ('DEFAULT', 'ENABLE', 'DISABLE'):
        raise RelationalClientError('Invalid Firebird publication state')
    temporary = kind == 'GLOBAL TEMPORARY'
    if temporary and publication != 'DEFAULT':
        raise RelationalClientError(
            'CREATE GLOBAL TEMPORARY TABLE has no publication clause; '
            'initial membership follows the database publication policy')
    filename = options.get('external_file')
    if kind != 'EXTERNAL' and filename:
        raise RelationalClientError('External files require an external table')
    prefix = 'CREATE GLOBAL TEMPORARY TABLE' if temporary else 'CREATE TABLE'
    sql = prefix + ' ' + identifier(name)
    if kind == 'EXTERNAL':
        if not isinstance(filename, str) or not filename:
            raise RelationalClientError('External server filename is required')
        sql += ' EXTERNAL FILE ' + literal(filename)
    sql += ' (\n  ' + ',\n  '.join(definitions) + '\n)'
    if temporary:
        retention = options.get('on_commit', 'DELETE ROWS')
        if retention not in ('DELETE ROWS', 'PRESERVE ROWS'):
            raise RelationalClientError('Invalid temporary row retention')
        sql += ' ON COMMIT ' + retention
    elif options.get('on_commit'):
        raise RelationalClientError('ON COMMIT applies to temporary tables')
    if security != 'INHERIT':
        sql += (', ' if temporary else ' ') + 'SQL SECURITY ' + security
    if publication != 'DEFAULT':
        sql += ' ' + publication + ' PUBLICATION'
    return sql


def alterations(name, changes):
    clauses = []
    security = changes.get('sql_security', 'UNCHANGED')
    if security not in ('UNCHANGED', 'INHERIT', 'DEFINER', 'INVOKER'):
        raise RelationalClientError('Invalid Firebird SQL security mode')
    if security == 'INHERIT':
        clauses.append('DROP SQL SECURITY')
    elif security != 'UNCHANGED':
        clauses.append('ALTER SQL SECURITY ' + security)
    publication = changes.get('publication', 'UNCHANGED')
    if publication not in ('UNCHANGED', 'ENABLE', 'DISABLE'):
        raise RelationalClientError('Invalid Firebird publication state')
    if publication != 'UNCHANGED':
        clauses.append(publication + ' PUBLICATION')
    return ['ALTER TABLE ' + identifier(name) + ' ' + clause
            for clause in clauses]


def fields(field, creating):
    fields = []
    if creating:
        fields.extend([
            field('table_type', 'Table kind', 'select', True,
                  default='PERSISTENT', options=TABLE_TYPES),
            {**field('on_commit', 'Temporary row retention', 'select', True,
                     default='DELETE ROWS',
                     options=('DELETE ROWS', 'PRESERVE ROWS')),
             'visible_when': {'field_id': 'table_type',
                              'equals': 'GLOBAL TEMPORARY'}},
            {**field('external_file', 'External filename on the server',
                     'text', True, 'The server must permit this path through '
                     'ExternalFileAccess. This is not a client file upload. '
                     'External row inserts cannot be rolled back. UPDATE and '
                     'DELETE are unavailable; DROP leaves the file intact.'),
             'visible_when': {'field_id': 'table_type', 'equals': 'EXTERNAL'}},
        ])
    security = ('INHERIT', 'DEFINER', 'INVOKER')
    fields.append(field('sql_security', 'SQL security', 'select', False,
                        'INHERIT uses the database SQL security setting.',
                        'INHERIT' if creating else 'UNCHANGED',
                        options=security if creating else
                        ('UNCHANGED', *security)))
    publication = field('publication', 'Publication membership', 'select',
                        False, 'Controls table membership in the default '
                        'publication, not the database replica mode. '
                        'DEFAULT follows the database auto-enable policy. '
                        'Membership alone does not prove row replication.',
                        'DEFAULT' if creating else 'UNCHANGED',
                        options=(('DEFAULT' if creating else 'UNCHANGED'),
                                 'ENABLE', 'DISABLE'))
    if creating:
        publication['visible_when'] = {'field_id': 'table_type',
                                       'in': ['PERSISTENT', 'EXTERNAL']}
    fields.append(publication)
    return fields
