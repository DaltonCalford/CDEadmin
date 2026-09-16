"""Firebird user replacement with secret-safe previews and plugin identity."""
from collections.abc import Mapping

from pgadmin.cdeadmin.navigator import resource_native
from pgadmin.cdeadmin.sdk.relational import RelationalClientError
from .character_metadata import identifier, literal, text

OPERATIONS = frozenset({'create_or_alter', 'recreate'})
WARNING = (
    'RECREATE replaces the account in its security database and plugin. '
    'Supply the intended credentials and attributes. Database privileges '
    'are name-based: recreation is not a substitute for revoking grants. '
    'Commit or roll back explicitly; existing connections are not reset.')


def compile_operation(operation, draft, target=None):
    if (not isinstance(operation, str) or operation not in OPERATIONS or
            not isinstance(draft, Mapping)):
        raise RelationalClientError('Unknown Firebird user replacement task')
    key = 'name' if operation == 'create_or_alter' else 'confirmation'
    allowed = {key, 'password', 'plugin', 'first_name', 'middle_name',
               'last_name', 'admin_role', 'active_state', 'tags'}
    if set(draft) - allowed:
        raise RelationalClientError('Unknown Firebird user replacement fields')
    plugin = draft.get('plugin')
    if operation == 'recreate':
        if (not isinstance(target, Mapping) or
                target.get('resource_kind') != 'user'):
            raise RelationalClientError('An inspected user is required')
        name = target.get('display_name')
        if draft.get('confirmation') != name:
            raise RelationalClientError('Confirm the exact user name')
        inspected_plugin = resource_native(target).get('plugin')
        if inspected_plugin:
            if plugin and plugin != inspected_plugin:
                raise RelationalClientError('Cannot redirect the user plugin')
            plugin = inspected_plugin
        if not plugin:
            raise RelationalClientError(
                'The user management plugin is required')
    else:
        name = draft.get('name')
    command = ('CREATE OR ALTER' if operation == 'create_or_alter'
               else 'RECREATE') + ' USER ' + identifier(name)
    clauses, previews = [], []
    password = draft.get('password')
    if password is not None:
        text(password, 'Password')
        if not password:
            raise RelationalClientError('Password must not be empty')
        clauses.append('PASSWORD ' + literal(password))
        previews.append('PASSWORD <redacted>')
    elif operation == 'recreate':
        raise RelationalClientError('Recreation requires a new password')
    for field, keyword in (('first_name', 'FIRSTNAME'),
                           ('middle_name', 'MIDDLENAME'),
                           ('last_name', 'LASTNAME')):
        if field in draft:
            value = text(draft[field], 'User name component')
            clauses.append(keyword + ' ' + literal(value))
            previews.append(keyword + ' <redacted>')
    for field, options in (
        ('admin_role', {'GRANT': 'GRANT ADMIN ROLE',
                        'REVOKE': 'REVOKE ADMIN ROLE'}),
        ('active_state', {'ACTIVE': 'ACTIVE', 'INACTIVE': 'INACTIVE'}),
    ):
        value = draft.get(field, 'UNCHANGED')
        if value == 'UNCHANGED':
            continue
        if not isinstance(value, str) or value not in options:
            raise RelationalClientError('Invalid user ' + field)
        clauses.append(options[value])
        previews.append(options[value])
    tags = draft.get('tags', [])
    if not isinstance(tags, list):
        raise RelationalClientError('User tags must be a list')
    rendered, redacted = [], []
    for tag in tags:
        if (not isinstance(tag, Mapping) or
                set(tag) - {'name', 'value', 'drop'} or
                not isinstance(tag.get('drop', False), bool)):
            raise RelationalClientError('Invalid user tag')
        tag_name = tag.get('name')
        quoted = identifier(tag_name)
        if any(char in tag_name for char in '\r\n='):
            raise RelationalClientError('Tag names cannot contain CR, LF or =')
        if tag.get('drop', False):
            if 'value' in tag:
                raise RelationalClientError('Dropped tags cannot have a value')
            rendered.append('DROP ' + quoted)
            redacted.append('DROP ' + quoted)
        else:
            value = text(tag.get('value'), 'Tag value')
            if '\n' in value or '\r' in value:
                raise RelationalClientError(
                    'Tag values cannot contain CR or LF')
            rendered.append(quoted + ' = ' + literal(value))
            redacted.append(quoted + ' = <redacted>')
    if tags:
        clauses.append('TAGS (' + ', '.join(rendered) + ')')
        previews.append('TAGS (' + ', '.join(redacted) + ')')
    if not clauses:
        raise RelationalClientError('Specify at least one user attribute')
    if plugin:
        clauses.append('USING PLUGIN ' + identifier(plugin))
        previews.append('USING PLUGIN ' + identifier(plugin))
    return {'source': command + ' ' + ' '.join(clauses),
            'preview_source': command + ' ' + ' '.join(previews),
            'parameters': ()}


def form(operation, field):
    if not isinstance(operation, str) or operation not in OPERATIONS:
        raise RelationalClientError('Unknown Firebird user replacement form')
    fields = []
    if operation == 'create_or_alter':
        fields.append(field('name', 'User name', 'text', True))
    fields.extend([
        field('password', 'New password', 'password', operation == 'recreate',
              'Required for recreation and creation by password plugins. '
              'Omit to retain an existing password when altering.',
              sensitive=True),
        field('plugin', 'User management plugin', 'text', False,
              'Recreation uses the inspected plugin; no redirection.'),
    ])
    for name in ('first_name', 'middle_name', 'last_name'):
        fields.append(field(name, name.replace('_', ' ').title(),
                            'text', False))
    fields.extend([
        field('admin_role', 'Security database administrator role', 'select',
              False, default='UNCHANGED',
              options=('UNCHANGED', 'GRANT', 'REVOKE')),
        field('active_state', 'Account state', 'select', False,
              default='UNCHANGED',
              options=('UNCHANGED', 'ACTIVE', 'INACTIVE')),
        field('tags', 'User tags', 'json', False,
              'Entries with name/value, or name/drop:true. Values are '
              'redacted in previews. CR/LF cannot be encoded safely by the '
              'native user-management attribute protocol.', [],
              sensitive=True),
    ])
    if operation == 'recreate':
        fields.append(field('confirmation', 'Confirm user name', 'text', True,
                            WARNING))
    return {'form_id': 'firebird.user.' + operation,
            'title': 'Create or alter user' if operation == 'create_or_alter'
            else 'Recreate user', 'fields': fields}
