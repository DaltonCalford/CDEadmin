"""Firebird trigger replacement with a single native declaration."""
from collections.abc import Mapping

from pgadmin.cdeadmin.sdk.relational import RelationalClientError
from .character_metadata import identifier
from .packages import source

OPERATIONS = frozenset({'create_or_alter', 'recreate'})
WARNING = (
    'RECREATE drops and creates the trigger. Comments and privileges granted '
    'to the trigger are not automatically restored. Native permission and '
    'dependency checks apply. Confirm the exact trigger name.')
NOTICE = ('Commit or roll back explicitly. Trigger events may run during '
          'database operations. No connection is automatically reset.')


def compile_operation(operation, draft, target=None):
    if (not isinstance(operation, str) or operation not in OPERATIONS or
            not isinstance(draft, Mapping)):
        raise RelationalClientError('Unknown Firebird trigger task')
    key = 'name' if operation == 'create_or_alter' else 'confirmation'
    if set(draft) - {'declaration', key}:
        raise RelationalClientError('Unknown Firebird trigger form fields')
    if operation == 'create_or_alter':
        name = draft.get('name')
    else:
        if (not isinstance(target, Mapping) or
                target.get('resource_kind') != 'trigger'):
            raise RelationalClientError('An inspected trigger is required')
        name = target.get('display_name')
        if draft.get('confirmation') != name:
            raise RelationalClientError('Confirm the exact trigger name')
    command = ('CREATE OR ALTER' if operation == 'create_or_alter'
               else 'RECREATE')
    return (command + ' TRIGGER ' + identifier(name) + '\n' +
            source(draft.get('declaration'), 'Trigger declaration'))


def form(operation, field):
    if not isinstance(operation, str) or operation not in OPERATIONS:
        raise RelationalClientError('Unknown Firebird trigger form')
    fields = []
    if operation == 'create_or_alter':
        fields.append(field('name', 'Trigger name', 'text', True))
    fields.append(field(
        'declaration', 'Trigger declaration and body', 'code', True,
        'Enter everything after the name: relation and DML events, ON '
        'database event, or BEFORE/AFTER DDL events; optional '
        'ACTIVE/INACTIVE, '
        'POSITION and SQL SECURITY; and AS body or native EXTERNAL '
        'declaration. Do not include CREATE TRIGGER, the trigger name or '
        'SET TERM. Firebird validates clause order and event combinations. '
        'Supply the intended definition explicitly. ' + NOTICE))
    if operation == 'recreate':
        fields.append(field('confirmation', 'Confirm trigger name',
                            'text', True, WARNING))
    return {'form_id': 'firebird.trigger.' + operation,
            'title': ('Create or alter trigger'
                      if operation == 'create_or_alter'
                      else 'Recreate trigger'), 'fields': fields}
