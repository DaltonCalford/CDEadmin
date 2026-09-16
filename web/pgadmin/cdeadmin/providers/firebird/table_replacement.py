"""Destructive Firebird table recreation with structured definitions."""
from collections.abc import Mapping

from pgadmin.cdeadmin.sdk.relational import RelationalClientError
from . import tables
from .character_metadata import identifier

WARNING = (
    'RECREATE drops and creates the table. Existing rows, grants, comments '
    'and child objects are not restored. Supply the full new definition. '
    'Native dependency and permission checks apply. External files are not '
    'deleted or made transactional. Commit or roll back explicitly.')


def compile_operation(draft, target, column_definition, constraint_definition):
    if (not isinstance(draft, Mapping) or
            set(draft) - {*tables.CREATE_KEYS, 'columns', 'constraints',
                          'confirmation'}):
        raise RelationalClientError('Unknown Firebird table recreation fields')
    if (not isinstance(target, Mapping) or
            target.get('resource_kind') != 'table'):
        raise RelationalClientError('An inspected table is required')
    name = target.get('display_name')
    if not isinstance(name, str) or draft.get('confirmation') != name:
        raise RelationalClientError('Confirm the exact table name')
    identifier(name)
    columns = draft.get('columns')
    constraints = draft.get('constraints', [])
    if not isinstance(columns, list) or not columns:
        raise RelationalClientError('Define at least one structured column')
    if not isinstance(constraints, list):
        raise RelationalClientError('Table constraints must be a list')
    for item in columns:
        if (isinstance(item, Mapping) and 'column_mode' in item and
                set(item) & {'type', 'nullable', 'default', 'primary_key',
                             'unique'}):
            raise RelationalClientError(
                'Native columns use constraints and has_default/'
                'default_kind/default_value, not generic column flags')
    definitions = [column_definition(item) for item in columns]
    definitions.extend(constraint_definition(item) for item in constraints)
    # The shared builder emits CREATE TABLE or CREATE GLOBAL TEMPORARY TABLE.
    # Replace only that fixed generated prefix, never user-supplied SQL text.
    statement = tables.create(name, definitions, draft)
    return 'RECREATE ' + statement[len('CREATE '):]


def form(field):
    return {
        'form_id': 'firebird.table.recreate', 'title': 'Recreate table',
        'fields': tables.fields(field, True) + [
            field('columns', 'Columns', 'json', True,
                  'Full definitions. Native columns use column_mode, '
                  'data_type, constraints and has_default/default_kind/'
                  'default_value. Do not mix native and generic flags.'),
            field('constraints', 'Table constraints', 'json', False,
                  'PRIMARY KEY, UNIQUE, FOREIGN KEY or CHECK definitions.',
                  []),
            field('confirmation', 'Confirm table name', 'text', True,
                  WARNING),
        ],
    }
