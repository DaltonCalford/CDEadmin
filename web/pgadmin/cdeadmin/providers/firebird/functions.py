"""Standalone Firebird function replacement, prepared as one native DDL."""
from collections.abc import Mapping

from pgadmin.cdeadmin.sdk.relational import RelationalClientError
from pgadmin.cdeadmin.navigator import resource_native
from .character_metadata import identifier
from .packages import source

OPERATIONS = frozenset({'create_or_alter', 'recreate'})
WARNING = (
    'RECREATE drops and creates the standalone function. Existing grants and '
    'comments are not automatically restored. Native dependency and '
    'permission checks apply. Confirm the exact function name.')
NOTICE = (
    'Commit or roll back explicitly. Verify the committed replacement using '
    'a fresh connection. No connection is automatically reset.')


def compile_operation(operation, draft, target=None):
    if (not isinstance(operation, str) or operation not in OPERATIONS or
            not isinstance(draft, Mapping)):
        raise RelationalClientError('Unknown Firebird function task')
    key = 'name' if operation == 'create_or_alter' else 'confirmation'
    if set(draft) - {'declaration', key}:
        raise RelationalClientError('Unknown Firebird function form fields')
    if isinstance(target, Mapping):
        path = target.get('display_path')
        if resource_native(target).get('package') or (
                isinstance(path, (list, tuple)) and len(path) > 1):
            raise RelationalClientError(
                'Change this function through its owning package header/body')
    if operation == 'create_or_alter':
        name = draft.get('name')
    else:
        if (not isinstance(target, Mapping) or
                target.get('resource_kind') != 'function'):
            raise RelationalClientError('An inspected function is required')
        name = target.get('display_name')
        if draft.get('confirmation') != name:
            raise RelationalClientError('Confirm the exact function name')
    command = ('CREATE OR ALTER' if operation == 'create_or_alter'
               else 'RECREATE')
    return (command + ' FUNCTION ' + identifier(name) + '\n' +
            source(draft.get('declaration'), 'Function declaration'))


def form(operation, field):
    if not isinstance(operation, str) or operation not in OPERATIONS:
        raise RelationalClientError('Unknown Firebird function form')
    fields = []
    if operation == 'create_or_alter':
        fields.append(field('name', 'Function name', 'text', True))
    fields.append(field(
        'declaration', 'Complete function declaration and body', 'code', True,
        'Enter everything after the name: optional input parameters, '
        'RETURNS type, optional DETERMINISTIC, SQL SECURITY and AS body, '
        'or the native EXTERNAL declaration. Do not include CREATE FUNCTION, '
        'the function name or SET TERM. This replaces the full definition; '
        'omitted clauses are not implicitly preserved. ' + NOTICE))
    if operation == 'recreate':
        fields.append(field('confirmation', 'Confirm function name',
                            'text', True, WARNING))
    return {'form_id': 'firebird.function.' + operation,
            'title': ('Create or alter function'
                      if operation == 'create_or_alter'
                      else 'Recreate function'), 'fields': fields}
