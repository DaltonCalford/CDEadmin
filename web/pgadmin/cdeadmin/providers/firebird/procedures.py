"""Standalone Firebird procedure replacement, prepared as one native DDL."""
from collections.abc import Mapping

from pgadmin.cdeadmin.sdk.relational import RelationalClientError
from pgadmin.cdeadmin.navigator import resource_native
from .character_metadata import identifier
from .packages import source

OPERATIONS = frozenset({'create_or_alter', 'recreate'})
WARNING = (
    'RECREATE drops and creates the standalone procedure. Existing grants and '
    'comments are not automatically restored. Native dependency and '
    'permission checks apply. Confirm the exact procedure name.')
NOTICE = (
    'Commit or roll back explicitly. After committing a replacement, verify '
    'execution using a fresh connection: an existing attachment can retain '
    'earlier compiled procedure code. No connection is automatically reset.')


def compile_operation(operation, draft, target=None):
    if (not isinstance(operation, str) or operation not in OPERATIONS or
            not isinstance(draft, Mapping)):
        raise RelationalClientError('Unknown Firebird procedure task')
    key = 'name' if operation == 'create_or_alter' else 'confirmation'
    if set(draft) - {'declaration', key}:
        raise RelationalClientError('Unknown Firebird procedure form fields')
    if isinstance(target, Mapping):
        path = target.get('display_path')
        if resource_native(target).get('package') or (
                isinstance(path, (list, tuple)) and len(path) > 1):
            raise RelationalClientError(
                'Change this procedure through its owning package header/body')
    if operation == 'create_or_alter':
        name = draft.get('name')
    else:
        if (not isinstance(target, Mapping) or
                target.get('resource_kind') != 'procedure'):
            raise RelationalClientError('An inspected procedure is required')
        name = target.get('display_name')
        if draft.get('confirmation') != name:
            raise RelationalClientError('Confirm the exact procedure name')
    command = ('CREATE OR ALTER' if operation == 'create_or_alter'
               else 'RECREATE')
    return (command + ' PROCEDURE ' + identifier(name) + '\n' +
            source(draft.get('declaration'), 'Procedure declaration'))


def form(operation, field):
    if not isinstance(operation, str) or operation not in OPERATIONS:
        raise RelationalClientError('Unknown Firebird procedure form')
    fields = []
    if operation == 'create_or_alter':
        fields.append(field('name', 'Procedure name', 'text', True))
    fields.append(field(
        'declaration', 'Complete procedure declaration and body', 'code', True,
        'Enter everything after the name: optional input parameters, '
        'RETURNS parameters, SQL SECURITY and AS declarations/BEGIN ... END, '
        'or the native EXTERNAL declaration. Do not include CREATE PROCEDURE, '
        'the procedure name or SET TERM. This replaces the full definition; '
        'omitted clauses are not implicitly preserved. ' + NOTICE))
    if operation == 'recreate':
        fields.append(field('confirmation', 'Confirm procedure name',
                            'text', True, WARNING))
    return {'form_id': 'firebird.procedure.' + operation,
            'title': ('Create or alter procedure'
                      if operation == 'create_or_alter'
                      else 'Recreate procedure'), 'fields': fields}
