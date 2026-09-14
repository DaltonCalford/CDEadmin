##########################################################################
# CDEadmin - Multi-engine Database Administration
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
##########################################################################

"""Firebird 5 mapping DDL and scope-specific native catalog projection."""

from pgadmin.cdeadmin.sdk.relational import RelationalClientError

KINDS = ('authentication-mapping', 'global-authentication-mapping')
MODES = ('PLUGIN', 'ANY_PLUGIN', 'SERVERWIDE', 'MAPPING', 'ANY')
OPERATIONS = frozenset({
    'inspect', 'create', 'alter', 'create_or_alter', 'comment', 'drop',
})


def identifier(value):
    if not isinstance(value, str) or not value or '\x00' in value:
        raise RelationalClientError('Mapping identifier must not be empty')
    if len(value) > 63:
        raise RelationalClientError('Firebird identifiers have at most 63 '
                                    'characters')
    return '"' + value.replace('"', '""') + '"'


def literal(value):
    if not isinstance(value, str) or '\x00' in value:
        raise RelationalClientError('Mapping text must be a string '
                                    'without NUL')
    return "'" + value.replace("'", "''") + "'"


def compile_mapping(kind, operation, draft, target=None):
    if kind not in KINDS or operation not in OPERATIONS - {'inspect'}:
        raise RelationalClientError('Unknown Firebird mapping operation')
    creating = operation in {'create', 'create_or_alter'}
    name = (draft.get('name') if creating else
            (target or {}).get('display_name'))
    quoted = identifier(name)
    scope = 'GLOBAL ' if kind == KINDS[1] else ''
    object_sql = f'{scope}MAPPING {quoted}'
    if operation == 'drop':
        if draft.get('confirmation') != name:
            raise RelationalClientError('Confirm the exact mapping name')
        return [f'DROP {object_sql}']
    if operation == 'comment':
        value = draft.get('description', '')
        if (kind == KINDS[1] and isinstance(value, str) and
                len(value.encode('utf-8')) >= 32767):
            raise RelationalClientError(
                'Firebird 5.0.4 exposes only the first 32767 bytes of a '
                'global mapping comment. Use fewer than 32767 ASCII bytes '
                'so complete metadata can be verified.')
        if (kind == KINDS[1] and isinstance(value, str) and
                not value.isascii()):
            raise RelationalClientError(
                'Firebird 5.0.4 global mapping comments do not safely '
                'round-trip non-ASCII text through the security database. '
                'Use ASCII text; local mapping comments support Unicode.')
        return [f'COMMENT ON {object_sql} IS ' + (
            'NULL' if value == '' else literal(value))]
    mode = draft.get('using_mode', 'PLUGIN')
    if mode not in MODES:
        raise RelationalClientError('Invalid mapping authentication source')
    plugin = draft.get('plugin', '')
    database = draft.get('source_database', '')
    if mode != 'PLUGIN' and plugin:
        raise RelationalClientError('Plugin name applies only to PLUGIN')
    if mode == 'SERVERWIDE' and database:
        raise RelationalClientError('SERVERWIDE cannot have an IN database')
    using = {
        'ANY_PLUGIN': 'ANY PLUGIN', 'SERVERWIDE': 'ANY PLUGIN SERVERWIDE',
        'MAPPING': 'MAPPING', 'ANY': '*',
    }.get(mode)
    if mode == 'PLUGIN':
        using = 'PLUGIN ' + identifier(plugin)
    if database:
        using += ' IN ' + identifier(database)
    from_type = identifier(draft.get('from_type', 'USER'))
    any_name = draft.get('from_any', False)
    if not isinstance(any_name, bool):
        raise RelationalClientError('Any source name must be boolean')
    if any_name:
        source = 'ANY ' + from_type
    else:
        source_name = draft.get('from_name')
        if not isinstance(source_name, str) or not source_name:
            raise RelationalClientError('Source name is required')
        source = from_type + ' ' + literal(source_name)
    to_type = draft.get('to_type', 'USER')
    if to_type not in ('USER', 'ROLE'):
        raise RelationalClientError('Mapping target must be USER or ROLE')
    destination = to_type
    if draft.get('to_name'):
        destination += ' ' + identifier(draft['to_name'])
    verb = {'create': 'CREATE', 'alter': 'ALTER',
            'create_or_alter': 'CREATE OR ALTER'}[operation]
    return [f'{verb} {object_sql} USING {using} FROM {source} '
            f'TO {destination}']


def form(kind, operation, field):
    fields = []
    if operation in {'create', 'create_or_alter'}:
        fields.append(field('name', 'Name', 'text', True))
        if operation == 'create_or_alter':
            fields[-1]['initial_value_path'] = ['name']
    if operation == 'inspect':
        pass
    elif operation == 'drop':
        fields.append(field('confirmation', 'Confirmation', 'text', True,
                            'Type the exact mapping name.'))
    elif operation == 'comment':
        help_text = 'Empty text removes the comment.'
        if kind == KINDS[1]:
            help_text += (' Firebird 5.0.4 global comments require ASCII '
                          'and fewer than 32767 bytes here to avoid native '
                          'text loss or unverifiable truncation.')
        fields.append({**field('description', 'Comment', 'multiline', False,
                               help_text, ''),
                       'initial_value_path': ['description'],
                       'submit_unchanged': True})
    else:
        fields.extend([
            field('using_mode', 'Authentication source', 'select', True,
                  'Global mappings affect all databases using the security '
                  'database; local mappings affect this database only. '
                  'A mapping does not install or enable an auth plugin.',
                  'PLUGIN', options=MODES),
            field('plugin', 'Plugin name', 'text', True,
                  'Required for PLUGIN; empty for other source modes.', ''),
            field('source_database', 'Source database or alias', 'text', False,
                  'Optional IN identifier, not allowed with SERVERWIDE.', ''),
            field('from_type', 'Source identity type', 'text', True,
                  'Native plugin identity type, for example USER, GROUP or '
                  'Predefined_Group. Case is preserved.', 'USER'),
            field('from_any', 'Any source name', 'boolean', False, '', False),
            field('from_name', 'Source name', 'text', True,
                  'Required unless Any source name is selected.', ''),
            field('to_type', 'Target identity type', 'select', True, '',
                  'USER', options=('USER', 'ROLE')),
            field('to_name', 'Target name', 'text', False,
                  'Empty preserves the original source name.', ''),
        ])
        for item in fields:
            if item['field_id'] == 'plugin':
                item['visible_when'] = {'field_id': 'using_mode',
                                        'equals': 'PLUGIN'}
            elif item['field_id'] == 'source_database':
                item['visible_when'] = {'field_id': 'using_mode',
                                        'in': ['PLUGIN', 'ANY_PLUGIN',
                                               'MAPPING', 'ANY']}
            elif item['field_id'] == 'from_name':
                item['visible_when'] = {
                    'field_id': 'from_any', 'equals': False}
        if operation == 'alter':
            for item in fields:
                item['initial_value_path'] = [
                    'mapping_draft', item['field_id']]
                item['submit_unchanged'] = True
    return {'form_id': f'firebird.{kind}.{operation}',
            'title': operation.replace('_', ' ').title() + ' ' + (
                'global mapping' if kind == KINDS[1] else 'local mapping'),
            'fields': fields}


def catalog_rows(cursor, global_scope=False):
    prefix = 'SEC$' if global_scope else 'RDB$'
    relation = ('SEC$GLOBAL_AUTH_MAPPING' if global_scope else
                'RDB$AUTH_MAPPING')
    columns = ('MAP_NAME', 'MAP_USING', 'MAP_PLUGIN', 'MAP_DB',
               'MAP_FROM_TYPE', 'MAP_FROM', 'MAP_TO_TYPE', 'MAP_TO',
               'DESCRIPTION')
    cursor.execute('SELECT ' + ', '.join(prefix + name for name in columns) +
                   f' FROM {relation} ORDER BY {prefix}MAP_NAME')
    # Materialize BLOBs before issuing another statement on this cursor.
    for row in cursor.fetchall():
        values = []
        for index, value in enumerate(row):
            if callable(getattr(value, 'read', None)):
                reader = value
                try:
                    value = reader.read()
                finally:
                    reader.close()
            if isinstance(value, str) and index != 8:
                value = value.rstrip()
            values.append(value)
        yield values


def metadata(kind, row):
    name, mode, plugin, database, from_type, source, to_type, to, comment = row
    mode_name = {'P': 'PLUGIN' if plugin else 'ANY_PLUGIN',
                 'S': 'SERVERWIDE', 'M': 'MAPPING', '*': 'ANY'}.get(mode)
    draft = {
        'using_mode': mode_name, 'plugin': plugin or '',
        'source_database': database or '', 'from_type': from_type,
        'from_any': source in (None, '*'), 'from_name': source or '',
        'to_type': {0: 'USER', 1: 'ROLE'}.get(to_type), 'to_name': to or '',
    }
    native = {
        'name': name,
        'scope': 'security-database' if kind == KINDS[1] else 'database',
        'mapping_draft': draft, 'description': comment,
        'using': mode, 'plugin': plugin, 'source_database': database,
        'from_type': from_type, 'from': source, 'to_type': to_type, 'to': to,
        'authentication_verified': False,
        'description_source': ('SEC$GLOBAL_AUTH_MAPPING' if kind == KINDS[1]
                               else 'RDB$AUTH_MAPPING'),
        'privileges_unavailable_reason': 'Mappings are administered through '
        'CHANGE_MAPPING_RULES authority, not per-mapping GRANT/REVOKE.',
    }
    if kind == KINDS[1] and comment is not None:
        native['description_completeness'] = (
            'unverified-at-native-projection-limit'
            if len(comment.encode('utf-8')) >= 32767 else
            'within-native-projection-limit')
    try:
        statements = compile_mapping(kind, 'create', {'name': name, **draft})
        if comment:
            statements += compile_mapping(kind, 'comment', {
                'description': comment}, {'display_name': name})
        native['ddl'] = ';\n'.join(statements) + ';'
    except RelationalClientError as error:
        native['ddl_unavailable_reason'] = str(error)
    return native
