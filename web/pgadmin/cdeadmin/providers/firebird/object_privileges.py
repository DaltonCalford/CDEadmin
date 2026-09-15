##########################################################################
# CDEadmin - Multi-engine Database Administration
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
##########################################################################

"""Object-bound Firebird grants; the inspected identity owns the target."""

from collections.abc import Mapping

from pgadmin.cdeadmin.navigator import resource_native
from pgadmin.cdeadmin.sdk.relational import RelationalClientError
from . import privileges
from .mappings import identifier


KINDS = {
    'table': 'TABLE', 'view': 'VIEW', 'procedure': 'PROCEDURE',
    'function': 'FUNCTION', 'external-function': 'FUNCTION',
    'package': 'PACKAGE', 'sequence': 'SEQUENCE', 'exception': 'EXCEPTION',
    'column': 'TABLE',
}
OPERATIONS = frozenset({'grant', 'revoke'})
COMMON_FIELDS = frozenset({
    'principal_kind', 'principal', 'additional_grantees', 'privileges',
    'grantor',
})
COLUMN_FIELDS = frozenset({'update_columns', 'reference_columns'})


def allowed_privileges(kind):
    if not isinstance(kind, str) or kind not in KINDS:
        raise RelationalClientError('This object has no native object grants')
    if kind == 'column':
        return ('UPDATE', 'REFERENCES')
    values = privileges.OBJECT_PRIVILEGES[KINDS[kind]]
    return (*values, 'ALL') if kind in {'table', 'view'} else values


def target_identity(kind, target):
    """Resolve server-owned identity, never a name supplied in the draft."""
    allowed_privileges(kind)
    if not isinstance(target, Mapping) or target.get('resource_kind') != kind:
        raise RelationalClientError('A matching inspected object is required')
    name = target.get('display_name')
    identifier(name)
    native = resource_native(target)
    object_type = KINDS[kind]
    if kind == 'column':
        # Columns have relation ownership, not independent SELECT privileges.
        path = target.get('display_path')
        if (not isinstance(path, (list, tuple)) or len(path) != 2 or
                path[-1] != name):
            raise RelationalClientError('The column relation is unavailable')
        relation = path[0]
        identifier(relation)
        return object_type, relation, name
    if kind in {'function', 'procedure'} and native.get('package'):
        # Packaged routines are executable through their package privilege.
        # Never emit GRANT EXECUTE ON FUNCTION "package.member".
        name = native['package']
        identifier(name)
        object_type = 'PACKAGE'
    return object_type, name, None


def compile_operation(kind, operation, draft, target):
    allowed = allowed_privileges(kind)
    if (not isinstance(operation, str) or operation not in OPERATIONS or
            not isinstance(draft, Mapping)):
        raise RelationalClientError('Choose an object grant or revoke')
    fields = COMMON_FIELDS | ({'grant_option'} if operation == 'grant' else
                              {'grant_option_only', 'confirmation'})
    if kind in {'table', 'view'}:
        fields |= COLUMN_FIELDS
    if set(draft) - fields:
        raise RelationalClientError('Unknown object privilege form fields')
    privileges._choices(draft.get('privileges'), allowed,
                        'object privileges')
    object_type, name, column = target_identity(kind, target)
    value = dict(draft, privilege_scope='object', object_type=object_type,
                 object_name=name)
    if column is not None:
        for privilege, key in (('UPDATE', 'update_columns'),
                               ('REFERENCES', 'reference_columns')):
            if privilege in draft['privileges']:
                value[key] = [{'name': column}]
    return privileges.compile_privilege(operation, value)


def form(kind, operation, field):
    allowed = allowed_privileges(kind)
    if not isinstance(operation, str) or operation not in OPERATIONS:
        raise RelationalClientError('Choose an object grant or revoke')
    base = privileges.form(operation, field)
    fields = COMMON_FIELDS | ({'grant_option'} if operation == 'grant' else
                              {'grant_option_only', 'confirmation'})
    if kind in {'table', 'view'}:
        fields |= COLUMN_FIELDS
    result = []
    for item in base['fields']:
        key = item['field_id']
        if key not in fields:
            continue
        # Keep only grantee-specific visibility. Scope and object identity
        # are fixed by the provider, so neither is an editable draft field.
        if key != 'principal':
            item.pop('visible_when', None)
        if key == 'privileges':
            item['options'] = [{'value': value, 'label': value}
                               for value in allowed]
            item['help'] = (
                'Applies to the selected object. The complete native target '
                'is shown in the statement preview; it cannot be redirected '
                'by editing this form.')
            if kind in {'procedure', 'function'}:
                item['help'] += (
                    ' For a packaged routine, EXECUTE applies to the entire '
                    'package, not only the selected member.')
            if kind == 'column':
                item['help'] += (
                    ' UPDATE and REFERENCES apply only to this column. '
                    'Firebird does not support column-specific SELECT.')
        result.append(item)
    return {'form_id': f'firebird.{kind}.{operation}',
            'title': operation.title() + ' ' + kind.replace('-', ' ') +
            ' privileges', 'fields': result}
