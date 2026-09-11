##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Dependency-light compiler for PostgreSQL provider-native admin forms."""

from __future__ import annotations

import re
from typing import Any, Mapping

from .preserved_surface import preserved_operations


POSTGRESQL_ADMIN_OPERATIONS = preserved_operations()
POSTGRESQL_NATIVE_FORM_KINDS = frozenset({
    'conversion', 'operator-class', 'operator-family',
})
_PG_REFERENCE = re.compile(
    r'^[A-Za-z_][A-Za-z0-9_$]*(?:\.[A-Za-z_][A-Za-z0-9_$]*)?'
    r'(?:\[\])?$'
)
_PG_OPERATOR = re.compile(
    r'^(?:[A-Za-z_][A-Za-z0-9_$]*\.)?[+\-*/<>=~!@#%^&|`?]+$'
)


class PostgreSQLNativeAdminError(RuntimeError):
    """A PostgreSQL provider-native operation cannot be compiled safely."""


def _required_text(value, label):
    if not isinstance(value, str) or not value.strip() or '\x00' in value:
        raise PostgreSQLNativeAdminError(f'{label} must not be empty')
    return value.strip()


def _identifier(value, label):
    value = _required_text(value, label)
    if len(value.encode('utf-8')) > 63:
        raise PostgreSQLNativeAdminError(f'{label} exceeds 63 bytes')
    return '"' + value.replace('"', '""') + '"'


def _qualified(schema, name):
    return f'{_identifier(schema, "schema")}.{_identifier(name, "name")}'


def _literal(value, label):
    value = _required_text(value, label)
    return "'" + value.replace("'", "''") + "'"


def _reference(value, label, operator=False):
    value = _required_text(value, label)
    pattern = _PG_OPERATOR if operator else _PG_REFERENCE
    if pattern.fullmatch(value) is None:
        raise PostgreSQLNativeAdminError(
            f'{label} is not a catalog reference'
        )
    return value


def _native_target(request):
    target = request.get('target_resource') or {}
    extensions = target.get('extensions') or {}
    value = extensions.get('postgresql') or {}
    native = value.get('native', value)
    if not isinstance(native, Mapping):
        raise PostgreSQLNativeAdminError(
            'PostgreSQL native target identity is unavailable'
        )
    return dict(native)


def _native_identity(native, draft, creating=False):
    source = draft if creating else native
    return (
        _required_text(source.get('schema'), 'schema'),
        _required_text(source.get('name'), 'name'),
    )


def _alter_statement(kind, qualified_name, draft, native):
    action = _required_text(draft.get('action'), 'alter action').lower()
    value = _required_text(draft.get('value'), 'alter value')
    keyword = {
        'conversion': 'CONVERSION',
        'operator-class': 'OPERATOR CLASS',
        'operator-family': 'OPERATOR FAMILY',
    }[kind]
    using = ''
    if kind in {'operator-class', 'operator-family'}:
        method = _reference(native.get('index_method'), 'index access method')
        using = f' USING {method}'
    if action == 'owner':
        return (
            f'ALTER {keyword} {qualified_name}{using} OWNER TO '
            f'{_identifier(value, "owner")}'
        )
    if action == 'rename':
        return (
            f'ALTER {keyword} {qualified_name}{using} RENAME TO '
            f'{_identifier(value, "new name")}'
        )
    if action == 'schema':
        return (
            f'ALTER {keyword} {qualified_name}{using} SET SCHEMA '
            f'{_identifier(value, "new schema")}'
        )
    raise PostgreSQLNativeAdminError(
        'PostgreSQL alter action is unavailable'
    )


def _operator_class_items(draft):
    items = []
    operators = draft.get('operators')
    functions = draft.get('functions')
    if not isinstance(operators, list) or not operators:
        raise PostgreSQLNativeAdminError(
            'operator class requires strategy operators'
        )
    if not isinstance(functions, list) or not functions:
        raise PostgreSQLNativeAdminError(
            'operator class requires support functions'
        )
    for operator in operators:
        if not isinstance(operator, Mapping):
            raise PostgreSQLNativeAdminError(
                'strategy operator must be an object'
            )
        strategy = operator.get('strategy')
        if isinstance(strategy, bool) or not isinstance(strategy, int) or (
                strategy < 1 or strategy > 32767):
            raise PostgreSQLNativeAdminError(
                'operator strategy number is invalid'
            )
        reference = _reference(
            operator.get('operator'), 'strategy operator', operator=True
        )
        items.append(f'OPERATOR {strategy} {reference}')
    for function in functions:
        if not isinstance(function, Mapping):
            raise PostgreSQLNativeAdminError(
                'support function must be an object'
            )
        support = function.get('support')
        if isinstance(support, bool) or not isinstance(support, int) or (
                support < 1 or support > 32767):
            raise PostgreSQLNativeAdminError(
                'support function number is invalid'
            )
        reference = _reference(function.get('function'), 'support function')
        items.append(f'FUNCTION {support} {reference}')
    return items


def compile_native_admin(request: Mapping[str, Any]) -> dict[str, Any]:
    """Compile one exact PostgreSQL provider-native administration action."""
    kind = request.get('resource_kind')
    operation = request.get('operation_id')
    if kind not in POSTGRESQL_NATIVE_FORM_KINDS:
        raise PostgreSQLNativeAdminError(
            'operation is owned by the preserved PostgreSQL UI'
        )
    if operation not in POSTGRESQL_ADMIN_OPERATIONS[kind]:
        raise PostgreSQLNativeAdminError(
            'PostgreSQL native administration operation is unavailable'
        )
    draft = request.get('draft') or {}
    if not isinstance(draft, Mapping):
        raise PostgreSQLNativeAdminError('PostgreSQL draft is invalid')
    native = _native_target(request)
    schema, name = _native_identity(
        native, draft, creating=operation == 'create'
    )
    qualified_name = _qualified(schema, name)
    if operation == 'inspect':
        queries = {
            'conversion': (
                'SELECT n.nspname AS schema, c.conname AS name, '
                'pg_encoding_to_char(c.conforencoding) AS source_encoding, '
                'pg_encoding_to_char(c.contoencoding) AS target_encoding, '
                'c.conproc::regproc::text AS function, c.condefault '
                'AS is_default FROM pg_conversion c JOIN pg_namespace n '
                'ON n.oid=c.connamespace WHERE n.nspname={schema} '
                'AND c.conname={name}'
            ),
            'operator-class': (
                'SELECT n.nspname AS schema, c.opcname AS name, '
                'a.amname AS index_method, c.opcintype::regtype::text '
                'AS data_type, c.opcdefault AS is_default '
                'FROM pg_opclass c JOIN pg_namespace n '
                'ON n.oid=c.opcnamespace JOIN pg_am a '
                'ON a.oid=c.opcmethod WHERE n.nspname={schema} '
                'AND c.opcname={name}'
            ),
            'operator-family': (
                'SELECT n.nspname AS schema, f.opfname AS name, '
                'a.amname AS index_method FROM pg_opfamily f '
                'JOIN pg_namespace n ON n.oid=f.opfnamespace '
                'JOIN pg_am a ON a.oid=f.opfmethod '
                'WHERE n.nspname={schema} AND f.opfname={name}'
            ),
        }
        return {
            'statement': queries[kind].format(
                schema=_literal(schema, 'schema'),
                name=_literal(name, 'name'),
            ),
            'read_only': True,
        }
    if operation == 'drop':
        confirmation = _required_text(
            draft.get('confirmation'), 'confirmation'
        )
        if confirmation != name:
            raise PostgreSQLNativeAdminError(
                'drop confirmation does not match object name'
            )
        keyword = {
            'conversion': 'CONVERSION',
            'operator-class': 'OPERATOR CLASS',
            'operator-family': 'OPERATOR FAMILY',
        }[kind]
        using = ''
        if kind in {'operator-class', 'operator-family'}:
            using = ' USING ' + _reference(
                native.get('index_method'), 'index access method'
            )
        cascade = ' CASCADE' if draft.get('cascade') else ' RESTRICT'
        return {
            'statement': f'DROP {keyword} {qualified_name}{using}{cascade}',
            'read_only': False,
        }
    if operation == 'alter':
        return {
            'statement': _alter_statement(
                kind, qualified_name, draft, native
            ),
            'read_only': False,
        }
    if kind == 'conversion':
        default = 'DEFAULT ' if draft.get('default') else ''
        statement = (
            f'CREATE {default}CONVERSION {qualified_name} FOR '
            f'{_literal(draft.get("source_encoding"), "source encoding")} '
            'TO '
            f'{_literal(draft.get("target_encoding"), "target encoding")} '
            f'FROM {_reference(draft.get("function"), "function")}'
        )
    elif kind == 'operator-family':
        method = _reference(draft.get('index_method'), 'index access method')
        statement = f'CREATE OPERATOR FAMILY {qualified_name} USING {method}'
    else:
        method = _reference(draft.get('index_method'), 'index access method')
        data_type = _reference(draft.get('data_type'), 'data type')
        default = 'DEFAULT ' if draft.get('default') else ''
        family = draft.get('family')
        family_clause = '' if not family else (
            f' FAMILY {_reference(family, "operator family")}'
        )
        storage = draft.get('storage_type')
        items = _operator_class_items(draft)
        if storage:
            items.append(f'STORAGE {_reference(storage, "storage type")}')
        statement = (
            f'CREATE OPERATOR CLASS {qualified_name} {default}'
            f'FOR TYPE {data_type} USING {method}{family_clause} AS ' +
            ', '.join(items)
        )
    return {'statement': statement, 'read_only': False}
