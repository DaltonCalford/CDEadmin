"""Native exception replacement tasks; message text is never executable SQL."""
from collections.abc import Mapping

from pgadmin.cdeadmin.sdk.relational import RelationalClientError
from .character_metadata import identifier, text

OPERATIONS = frozenset({'create_or_alter', 'recreate'})
WARNING = (
    'RECREATE drops and creates the exception. Existing grants and comments '
    'are not automatically restored. Native dependency and permission checks '
    'apply. Confirm the exact exception name.')


def compile_operation(operation, draft, target=None):
    if (not isinstance(operation, str) or operation not in OPERATIONS or
            not isinstance(draft, Mapping)):
        raise RelationalClientError('Unknown Firebird exception task')
    key = 'name' if operation == 'create_or_alter' else 'confirmation'
    if set(draft) - {'message', key}:
        raise RelationalClientError('Unknown Firebird exception form fields')
    if operation == 'create_or_alter':
        name = draft.get('name')
    else:
        if (not isinstance(target, Mapping) or
                target.get('resource_kind') != 'exception'):
            raise RelationalClientError('An inspected exception is required')
        name = target.get('display_name')
        if draft.get('confirmation') != name:
            raise RelationalClientError('Confirm the exact exception name')
    name = identifier(name)
    message = text(draft.get('message'), 'Exception message')
    command = ('CREATE OR ALTER' if operation == 'create_or_alter'
               else 'RECREATE')
    return command + ' EXCEPTION ' + name + " '" + message.replace(
        "'", "''") + "'"


def form(operation, field):
    if not isinstance(operation, str) or operation not in OPERATIONS:
        raise RelationalClientError('Unknown Firebird exception form')
    fields = []
    if operation == 'create_or_alter':
        fields.append(field('name', 'Exception name', 'text', True))
    fields.append({**field('message', 'Exception message', 'text', True),
                   'initial_value_path': ['message']})
    if operation == 'recreate':
        fields.append(field('confirmation', 'Confirm exception name',
                            'text', True, WARNING))
    return {'form_id': 'firebird.exception.' + operation,
            'title': ('Create or alter exception'
                      if operation == 'create_or_alter'
                      else 'Recreate exception'),
            'fields': fields}
