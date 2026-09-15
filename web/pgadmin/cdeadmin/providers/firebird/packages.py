"""Firebird package headers and bodies are separate native DDL targets."""

from collections.abc import Mapping

from pgadmin.cdeadmin.sdk.relational import RelationalClientError
from pgadmin.cdeadmin.navigator import resource_native
from .character_metadata import identifier, literal, text


OPERATIONS = frozenset({'inspect', 'create', 'alter', 'create_or_alter',
                        'recreate', 'create_body',
                        'replace_body', 'drop_body', 'comment', 'drop'})
HEADER_WARNING = (
    'This replaces the complete public package header. Omitted declarations '
    'are removed and an existing body is marked invalid. Recreate the body '
    'after changing the header. Inherit means the database SQL SECURITY '
    'default, not preservation of the previous package setting.')
BODY_WARNING = (
    'Enter the complete BEGIN ... END package body, including any private '
    'declarations and routine implementations. This changes the package '
    'body, not its public header. Do not include CREATE, AS or SET TERM.')


def validate_member_operation(kind, operation, target):
    """A routine inside a package is not a standalone ALTER/DROP target."""
    if kind not in {'function', 'procedure', 'external-function'} or (
            operation not in {'alter', 'drop'} or
            not isinstance(target, Mapping)):
        return
    path = target.get('display_path')
    if resource_native(target).get('package') or (
            isinstance(path, (list, tuple)) and len(path) > 1):
        raise RelationalClientError(
            'Change this routine through its owning package header/body. '
            'Firebird has no standalone ALTER/DROP for a package member.')


def source(value, label):
    value = text(value, label).strip()
    # The provider prefixes this with one specific DDL operation and sends
    # it to native statement preparation, not a multi-statement script
    # runner. Leave comments, quoted literals and PSQL grammar to Firebird.
    if not value:
        raise RelationalClientError(label + ' cannot be empty')
    return value


def compile_operation(operation, draft, target=None):
    if (not isinstance(operation, str) or
            operation not in OPERATIONS - {'inspect'} or
            not isinstance(draft, Mapping)):
        raise RelationalClientError('Unknown Firebird package task')
    allowed = {
        'create': {'name', 'header', 'sql_security', 'body', 'description'},
        'create_or_alter': {'name', 'header', 'sql_security'},
        'recreate': {'header', 'sql_security', 'body', 'description',
                     'confirmation'},
        'alter': {'header', 'sql_security'},
        'create_body': {'body'}, 'replace_body': {'body'},
        'drop_body': {'confirmation'}, 'drop': {'confirmation'},
        'comment': {'description'},
    }[operation]
    if set(draft) - allowed:
        raise RelationalClientError('Unknown Firebird package form fields')
    if operation in {'create', 'create_or_alter'}:
        name = draft.get('name')
    else:
        if (not isinstance(target, Mapping) or
                target.get('resource_kind') != 'package'):
            raise RelationalClientError('An inspected package is required')
        name = target.get('display_name')
    quoted = identifier(name)
    if operation == 'recreate' and draft.get('confirmation') != name:
        raise RelationalClientError('Confirm the exact package name')
    if operation in {'drop', 'drop_body'}:
        if draft.get('confirmation') != name:
            raise RelationalClientError('Confirm the exact package name')
        return ['DROP PACKAGE ' + ('BODY ' if operation == 'drop_body'
                                   else '') + quoted]
    if operation == 'comment':
        value = text(draft.get('description', ''), 'Package comment')
        return ['COMMENT ON PACKAGE ' + quoted + ' IS ' + (
            literal(value) if value else 'NULL')]
    if operation in {'create_body', 'replace_body'}:
        prefix = 'CREATE' if operation == 'create_body' else 'RECREATE'
        return [prefix + ' PACKAGE BODY ' + quoted + ' AS ' +
                source(draft.get('body'), 'Package body')]
    security = draft.get('sql_security', 'INHERIT')
    if security not in ('INHERIT', 'INVOKER', 'DEFINER'):
        raise RelationalClientError('Choose a native SQL SECURITY setting')
    prefix = {'create': 'CREATE', 'alter': 'ALTER',
              'create_or_alter': 'CREATE OR ALTER',
              'recreate': 'RECREATE'}[operation]
    result = [prefix + ' PACKAGE ' + quoted + (
        '' if security == 'INHERIT' else ' SQL SECURITY ' + security) +
        ' AS ' + source(draft.get('header'), 'Package header')]
    if operation in {'create', 'recreate'}:
        selected = {'resource_kind': 'package', 'display_name': name}
        if draft.get('body') not in (None, ''):
            result.extend(compile_operation('create_body', {
                'body': draft['body']}, selected))
        if 'description' in draft:
            result.extend(compile_operation('comment', {
                'description': draft['description']}, selected))
    return result


def form(operation, field):
    if (not isinstance(operation, str) or
            operation not in OPERATIONS - {'inspect'}):
        raise RelationalClientError('Unknown Firebird package form')
    fields = []
    if operation in {'create', 'create_or_alter'}:
        fields.append(field('name', 'Package name', 'text', True))
    if operation in {'create', 'alter', 'create_or_alter', 'recreate'}:
        fields.extend([
            {**field('header', 'Public header', 'code', True,
                     HEADER_WARNING if operation in {
                         'alter', 'create_or_alter'} else
                     'Complete BEGIN ... END public declarations. The body '
                     'can be created separately.'),
             'initial_value_path': ['header_source']},
            {**field('sql_security', 'SQL SECURITY', 'select', True,
                     'Inherit uses the database default.', default='INHERIT',
                     options=('INHERIT', 'INVOKER', 'DEFINER')),
             'initial_value_path': ['package_sql_security']},
        ])
    if operation in {'create', 'recreate', 'create_body', 'replace_body'}:
        fields.append({**field(
            'body', 'Package body', 'code', operation not in {
                'create', 'recreate'},
            BODY_WARNING + (' Leave empty to create only the header.'
                            if operation in {'create', 'recreate'} else '')),
                       'initial_value_path': ['body_source']})
    if operation in {'create', 'recreate', 'comment'}:
        fields.append({**field('description', 'Comment', 'multiline', False,
                               'Empty removes the comment.', ''),
                       'initial_value_path': ['description'],
                       'submit_unchanged': True})
    if operation in {'drop', 'drop_body', 'recreate'}:
        fields.append(field(
            'confirmation', 'Confirm package name', 'text', True,
            'Only the body is removed; public declarations remain.'
            if operation == 'drop_body' else
            ('Drop and recreate the entire package. Grants and its existing '
             'body are not preserved by RECREATE. Native dependency checks '
             'apply.' if operation == 'recreate' else
             'Remove the entire package. Native dependency checks apply.')))
    titles = {'create': 'Create package', 'alter': 'Alter package header',
              'create_or_alter': 'Create or alter package header',
              'recreate': 'Recreate package',
              'create_body': 'Create package body',
              'replace_body': 'Recreate package body',
              'drop_body': 'Drop package body', 'drop': 'Drop package',
              'comment': 'Edit package comment'}
    return {'form_id': 'firebird.package.' + operation,
            'title': titles[operation], 'fields': fields}
