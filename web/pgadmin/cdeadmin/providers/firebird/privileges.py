##########################################################################
# CDEadmin - Multi-engine Database Administration
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
##########################################################################

"""Firebird privilege grammar; role membership has its own native editor."""

from pgadmin.cdeadmin.sdk.relational import RelationalClientError
from .mappings import identifier

RELATION_PRIVILEGES = ('SELECT', 'INSERT', 'UPDATE', 'DELETE', 'REFERENCES')
OBJECT_PRIVILEGES = {
    'TABLE': RELATION_PRIVILEGES, 'VIEW': RELATION_PRIVILEGES,
    'PROCEDURE': ('EXECUTE',), 'FUNCTION': ('EXECUTE',),
    'PACKAGE': ('EXECUTE',), 'SEQUENCE': ('USAGE',),
    'GENERATOR': ('USAGE',), 'EXCEPTION': ('USAGE',),
}
DDL_CLASSES = ('TABLE', 'VIEW', 'PROCEDURE', 'FUNCTION', 'PACKAGE',
               'SEQUENCE', 'GENERATOR', 'DOMAIN', 'EXCEPTION', 'ROLE',
               'CHARACTER SET', 'COLLATION', 'FILTER')
PRINCIPAL_KINDS = ('USER', 'ROLE', 'PUBLIC', 'PROCEDURE', 'FUNCTION',
                   'PACKAGE', 'TRIGGER', 'VIEW', 'GROUP', 'SYSTEM PRIVILEGE')


def _boolean(draft, key):
    value = draft.get(key, False)
    if not isinstance(value, bool):
        raise RelationalClientError(key + ' must be boolean')
    return value


def _choices(value, allowed, label):
    if not isinstance(value, list) or not value or any(
            not isinstance(item, str) or item not in allowed
            for item in value):
        raise RelationalClientError('Choose native ' + label)
    if len(value) != len(set(value)):
        raise RelationalClientError('Duplicate ' + label)
    if 'ALL' in value and len(value) != 1:
        raise RelationalClientError('ALL cannot be combined with other '
                                    'privileges')
    return value


def _columns(value):
    if value is None:
        return []
    if not isinstance(value, list):
        raise RelationalClientError('Column privileges require a column list')
    names = []
    for item in value:
        if not isinstance(item, dict) or set(item) != {'name'}:
            raise RelationalClientError('Each column needs its exact name')
        identifier(item['name'])
        names.append(item['name'])
    if len(names) != len(set(names)):
        raise RelationalClientError('Duplicate privilege column')
    return names


def _grantee(kind, name):
    if kind not in PRINCIPAL_KINDS:
        raise RelationalClientError('Invalid Firebird grantee kind')
    if kind == 'PUBLIC':
        if name not in (None, '', 'PUBLIC'):
            raise RelationalClientError('PUBLIC has no separate grantee name')
        return 'PUBLIC', 'PUBLIC'
    return kind + ' ' + identifier(name), name


def compile_privilege(operation, draft):
    if operation not in ('grant', 'revoke') or not isinstance(draft, dict):
        raise RelationalClientError('Choose a Firebird grant or revoke')
    scope = draft.get('privilege_scope', 'object')
    if scope not in ('object', 'ddl_class', 'database', 'all_objects'):
        raise RelationalClientError('Invalid Firebird privilege scope')
    kind = draft.get('principal_kind', draft.get('ddl_principal_kind', 'USER'))
    if draft.get('ddl_principal_kind') and draft.get('principal_kind') and (
            draft['ddl_principal_kind'] != kind):
        raise RelationalClientError('Conflicting grantee kinds')
    name = draft.get('principal')
    principal, name = _grantee(kind, name)
    additional = draft.get('additional_grantees', [])
    if not isinstance(additional, list):
        raise RelationalClientError('Additional grantees must be a list')
    rendered = [principal]
    grantees = [(kind, name)]
    for item in additional:
        if not isinstance(item, dict) or set(item) - {'kind', 'name'}:
            raise RelationalClientError('Each grantee needs a kind and name')
        text, label = _grantee(item.get('kind'), item.get('name'))
        grantee = (item['kind'], label)
        if grantee in grantees:
            raise RelationalClientError('Duplicate native grantee')
        grantees.append(grantee)
        rendered.append(text)
    principal = ', '.join(rendered)
    grant_option = _boolean(draft, 'grant_option')
    option_only = _boolean(draft, 'grant_option_only')
    if operation == 'grant' and option_only or (
            operation == 'revoke' and grant_option):
        raise RelationalClientError('Grant and revoke options are distinct')
    if operation == 'revoke' and draft.get('confirmation') != ', '.join(
            item[1] for item in grantees):
        raise RelationalClientError('Confirm the exact grantee names in '
                                    'form order, separated by comma and space')
    grantor = draft.get('grantor')
    if grantor not in (None, ''):
        identifier(grantor)
    update_columns = _columns(draft.get('update_columns'))
    reference_columns = _columns(draft.get('reference_columns'))
    if scope != 'object' and (update_columns or reference_columns):
        raise RelationalClientError('Column lists apply only to relation '
                                    'privileges')
    if scope == 'all_objects':
        if operation != 'revoke' or option_only or grantor:
            raise RelationalClientError('REVOKE ALL ON ALL has no grant '
                                        'option or grantor clause')
        if any(draft.get(key) for key in (
                'object_type', 'object_name', 'privileges', 'ddl_class',
                'ddl_privileges', 'database_privileges')):
            raise RelationalClientError('ALL ON ALL cannot name another scope')
        return 'REVOKE ALL ON ALL FROM ' + principal
    if scope == 'object':
        target = draft.get('object_type')
        if not isinstance(target, str) or target not in OBJECT_PRIVILEGES:
            raise RelationalClientError('Choose a grantable Firebird object')
        allowed = OBJECT_PRIVILEGES[target]
        if target in ('TABLE', 'VIEW'):
            allowed = (*allowed, 'ALL')
        values = _choices(draft.get('privileges'), allowed,
                          'object privileges')
        if update_columns and 'UPDATE' not in values or (
                reference_columns and 'REFERENCES' not in values):
            raise RelationalClientError('Each column list requires its '
                                        'UPDATE or REFERENCES privilege')
        if any(draft.get(key) for key in (
                'ddl_class', 'ddl_privileges', 'database_privileges')):
            raise RelationalClientError('Do not mix privilege scopes')
        rendered = []
        for value in values:
            columns = update_columns if value == 'UPDATE' else (
                reference_columns if value == 'REFERENCES' else [])
            names = ', '.join(identifier(column) for column in columns)
            rendered.append(value + (' (' + names + ')' if columns else ''))
        # Views use relation privilege syntax: ON TABLE, never ON VIEW.
        target_sql = 'TABLE' if target == 'VIEW' else target
        clause = ', '.join(rendered) + ' ON ' + target_sql + ' ' + identifier(
            draft.get('object_name'))
    else:
        if any(draft.get(key) for key in (
                'object_type', 'object_name', 'privileges')):
            raise RelationalClientError('Do not mix privilege scopes')
        if scope == 'ddl_class':
            target = draft.get('ddl_class')
            if target not in DDL_CLASSES or draft.get('database_privileges'):
                raise RelationalClientError('Choose one native DDL class')
            values = _choices(draft.get('ddl_privileges'),
                              ('CREATE', 'ALTER ANY', 'DROP ANY', 'ALL'),
                              'DDL class privileges')
        else:
            target = 'DATABASE'
            if draft.get('ddl_class') or draft.get('ddl_privileges'):
                raise RelationalClientError('Do not mix privilege scopes')
            values = _choices(draft.get('database_privileges'),
                              ('CREATE', 'ALTER', 'DROP', 'ALL'),
                              'database privileges')
            # Native ALL DATABASE is ALTER + DROP, not CREATE DATABASE.
            if 'CREATE' in values and (
                    any(item[0] not in ('USER', 'ROLE')
                        for item in grantees) or
                    grant_option or option_only or grantor):
                raise RelationalClientError('CREATE DATABASE grants require '
                                            'USER or ROLE and prohibit grant '
                                            'options and GRANTED BY')
        clause = ', '.join(values) + ' ' + target
    source = operation.upper() + (' GRANT OPTION FOR' if option_only else '')
    source += ' ' + clause + (' TO ' if operation == 'grant' else ' FROM ')
    source += principal
    if grant_option:
        source += ' WITH GRANT OPTION'
    if grantor:
        source += ' GRANTED BY USER ' + identifier(grantor)
    return source


def form(operation, field):
    if operation not in ('grant', 'revoke'):
        raise RelationalClientError('Unsupported privilege form')
    scopes = ('object', 'ddl_class', 'database') + (
        ('all_objects',) if operation == 'revoke' else ())
    fields = [field('privilege_scope', 'Privilege scope', 'select', True,
                    default='object', options=scopes),
              field('principal_kind', 'Grantee kind', 'select', True,
                    default='USER', options=PRINCIPAL_KINDS),
              field('principal', 'Grantee name', 'text', True)]
    fields[-1]['visible_when'] = {
        'field_id': 'principal_kind',
        'in': [kind for kind in PRINCIPAL_KINDS if kind != 'PUBLIC']}
    additional = field('additional_grantees', 'Additional grantees', 'json',
                       False, 'All listed grantees are part of one native '
                       'statement and receive the selected privileges.',
                       default=[])
    child_name = field('name', 'Grantee name', 'text', True)
    child_name['visible_when'] = {
        'field_id': 'kind',
        'in': [kind for kind in PRINCIPAL_KINDS if kind != 'PUBLIC']}
    additional.update(json_type='array', array_editor={
        'item_kind': 'object', 'fields': [
            field('kind', 'Grantee kind', 'select', True, default='USER',
                  options=PRINCIPAL_KINDS), child_name]})
    fields.append(additional)

    def add(item, scope):
        item['visible_when'] = {'field_id': 'privilege_scope', 'equals': scope}
        fields.append(item)
        return item

    add(field('object_type', 'Object type', 'select', True,
              default='TABLE', options=tuple(OBJECT_PRIVILEGES)), 'object')
    add(field('object_name', 'Exact object name', 'text', True,
              'Dots belong to the Firebird identifier; they do not name a '
              'schema.'), 'object')
    choices = tuple(dict.fromkeys(
        value for values in OBJECT_PRIVILEGES.values()
        for value in values)) + ('ALL',)
    item = add(field('privileges', 'Object privileges', 'multiselect', True,
                     default=[], options=choices), 'object')
    for option in item['options']:
        option['visible_when'] = {'field_id': 'object_type', 'in': [
            kind for kind, values in OBJECT_PRIVILEGES.items()
            if option['value'] in values or (
                option['value'] == 'ALL' and kind in ('TABLE', 'VIEW'))]}
    for key, title in (
            ('update_columns', 'UPDATE columns'),
            ('reference_columns', 'REFERENCES columns')):
        item = field(key, title, 'json', False,
                     'Empty means the whole relation. Column lists apply '
                     'only to UPDATE or REFERENCES, never SELECT.', default=[])
        item.update(json_type='array', array_editor={
            'item_kind': 'object', 'fields': [
                field('name', 'Exact column name', 'text', True)]},
            visible_when={'all': [
                {'field_id': 'privilege_scope', 'equals': 'object'},
                {'field_id': 'object_type', 'in': ['TABLE', 'VIEW']}]})
        fields.append(item)
    add(field('ddl_class', 'DDL object class', 'select', True,
              default='TABLE', options=DDL_CLASSES), 'ddl_class')
    add(field('ddl_privileges', 'DDL privileges', 'multiselect', True,
              default=[], options=('CREATE', 'ALTER ANY', 'DROP ANY',
                                   'ALL')),
        'ddl_class')
    add(field('database_privileges', 'Database privileges', 'multiselect',
              True,
              'ALL means ALTER and DROP, not CREATE DATABASE. CREATE DATABASE '
              'uses the security database and requires a USER or ROLE without '
              'grant options or a GRANTED BY clause. A ROLE must exist in '
              'the server security database, not only this database.',
              default=[],
              options=('CREATE', 'ALTER', 'DROP', 'ALL')), 'database')
    option = field(
        'grant_option' if operation == 'grant' else 'grant_option_only',
        'With grant option' if operation == 'grant' else
        'Revoke only the grant option', 'boolean', default=False)
    grantor = field('grantor', 'Explicit grantor', 'text', False,
                    'Optional GRANTED BY USER. Native authority is required.')
    for item in (option, grantor):
        item['visible_when'] = {'field_id': 'privilege_scope',
                                'in': ['object', 'ddl_class', 'database']}
        fields.append(item)
    if operation == 'revoke':
        fields.append(field('confirmation', 'Confirm exact grantee names',
                            'text', True, 'Enter the names in form order, '
                            'separated by comma and space. For PUBLIC, enter '
                            'PUBLIC. Review the complete preview.'))
    return {'form_id': 'firebird.privilege.' + operation,
            'title': operation.title() + ' Firebird privileges',
            'fields': fields}
