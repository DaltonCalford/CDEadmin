##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Provider-owned relational visual administration mechanics.

This module constructs native commands only after an engine provider selects
its dialect and admits an exact object/operation pair. Connection routes stay
in opaque, in-memory plan payloads. Transaction methods are explicit requests
to the driver; observations are never interpreted as finality by common code.
"""

from __future__ import annotations

import copy
import os
import posixpath
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from ..sdk.relational import RelationalClientError
from ..visual_admin.requirements import EXPERIENCE_REQUIREMENTS


_FRAGMENT = re.compile(r'^[\w\s(),.+*/%<>=\'"-]+$', re.UNICODE)
_DDL_PREFIX = re.compile(
    r'^\s*(?:create|alter|drop|grant|revoke|attach|detach)\b', re.I
)


@dataclass(frozen=True)
class RelationalAdminDialect:
    """Exact command and identity policy selected by one provider."""

    engine_id: str
    quote_open: str = '"'
    quote_close: str = '"'
    parameter: str = '?'
    supported: Mapping[str, frozenset[str]] = field(default_factory=dict)
    database_keyword: str = 'DATABASE'
    supports_cascade: bool = True
    embedded_database: bool = False
    database_create_mode: str = 'sql'
    database_extension: str = ''
    syntax_family: str | None = None
    not_applicable_concepts: frozenset[str] = frozenset()
    concept_resource_kinds: Mapping[str, tuple[str, ...]] = field(
        default_factory=dict
    )
    additional_concept_declarations: Mapping[
        str, Mapping[str, object]
    ] = field(default_factory=dict)

    @property
    def sql_family(self):
        family = self.syntax_family or self.engine_id
        return 'mysql' if family in {'mysql', 'mariadb'} else family


@dataclass(frozen=True)
class _RowIdentity:
    route_fingerprint: tuple[tuple[str, str], ...]
    target_path: tuple[str, ...]
    key_columns: tuple[str, ...]
    key_values: tuple[Any, ...]
    original: Mapping[str, Any]
    issued_at: float


class RelationalAdministration:
    """Compile, execute, and page rows for an admitted SQL dialect."""

    def __init__(self, dialect: RelationalAdminDialect):
        self.dialect = dialect
        self._row_identities: dict[str, _RowIdentity] = {}
        self._identity_lock = threading.RLock()

    def supports(self, resource_kind, operation_id):
        return operation_id in self.dialect.supported.get(
            resource_kind, frozenset()
        )

    def catalog(self, catalog):
        """Replace generic executable forms with structured dialect forms."""
        value = copy.deepcopy(dict(catalog))
        for resource in value.get('objects', []):
            kind = resource['resource_kind']
            for operation in resource.get('operations', []):
                operation_id = operation['operation_id']
                if self.supports(kind, operation_id):
                    operation['form'] = self._form(kind, operation_id)
                    if kind == 'privilege' and operation_id in {
                        'grant', 'revoke',
                    }:
                        operation['target_required'] = False
        value['relational_administration_contract'] = (
            'cdeadmin.relational-admin.v1'
        )
        value['complete_raw_commands_accepted'] = False
        value['row_identity_authority'] = 'provider'
        declarations = value.setdefault('concept_declarations', {})
        relational = declarations.setdefault('relational', {})
        for concept_id, requirement in EXPERIENCE_REQUIREMENTS[
                'relational'].items():
            aliases = self.dialect.concept_resource_kinds.get(
                concept_id, ()
            )
            kinds = set(requirement['resource_kinds']).union(aliases)
            operations = set().union(*(
                self.dialect.supported.get(kind, frozenset())
                for kind in kinds
            ))
            if operations:
                operation_obligations = {
                    kind: sorted(self.dialect.supported.get(
                        kind, frozenset()
                    ))
                    for kind in sorted(kinds)
                    if self.dialect.supported.get(kind)
                }
                status = (
                    'supported'
                    if operations.difference({'inspect'}) else 'read_only'
                )
                relational[concept_id] = {
                    'status': status,
                    'resource_kinds': sorted(aliases),
                    'reason': (
                        'Declared from the exact provider dialect operation '
                        'map; common code does not infer native support.'
                    ),
                    'evidence': [
                        f'provider-dialect:{self.dialect.engine_id}'
                    ],
                    'operation_obligations': operation_obligations,
                    'live_operations': {},
                }
            elif concept_id in self.dialect.not_applicable_concepts:
                relational[concept_id] = {
                    'status': 'not_applicable',
                    'reason': (
                        'The exact provider profile declares this relational '
                        'concept absent from its native object model.'
                    ),
                    'evidence': [
                        f'provider-profile:{self.dialect.engine_id}'
                    ],
                }
        for family_id, concepts in (
                self.dialect.additional_concept_declarations.items()):
            family = declarations.setdefault(family_id, {})
            provider_concepts = copy.deepcopy(dict(concepts))
            for declaration in provider_concepts.values():
                if not isinstance(declaration, dict) or declaration.get(
                        'status') not in {'supported', 'read_only'}:
                    continue
                kinds = declaration.get('resource_kinds', [])
                if not isinstance(kinds, list):
                    continue
                obligations = {
                    kind: sorted(self.dialect.supported.get(
                        kind, frozenset()
                    ))
                    for kind in sorted(kinds)
                    if self.dialect.supported.get(kind)
                }
                declaration.setdefault(
                    'operation_obligations', obligations
                )
                declaration.setdefault('live_operations', {})
            family.update(provider_concepts)
        return value

    def validate(self, request):
        errors = []
        resource_kind = request.get('resource_kind')
        operation_id = request.get('operation_id')
        if not self.supports(resource_kind, operation_id):
            errors.append({
                'field_id': None,
                'code': 'provider_operation_unavailable',
                'message': (
                    'This engine does not admit the selected operation.'
                ),
            })
            return {'errors': errors}
        draft = request.get('draft', {})
        definition = draft.get('definition')
        if isinstance(definition, str) and _DDL_PREFIX.match(definition):
            errors.append({
                'field_id': 'definition',
                'code': 'complete_native_command_forbidden',
                'message': (
                    'Enter only the object body or query; the provider builds '
                    'the complete native command.'
                ),
            })
        if operation_id in {'insert', 'update', 'delete'} and (
            resource_kind != 'table'
        ):
            errors.append({
                'field_id': None,
                'code': 'non_table_row_operation',
                'message': 'Grid row operations require a base table.',
            })
        return {'errors': errors}

    def plan(self, request):
        route = request.get('_provider_route')
        if not isinstance(route, Mapping) or not route:
            raise RelationalClientError(
                'relational administration requires a trusted endpoint route'
            )
        resource_kind = str(request['resource_kind'])
        operation_id = str(request['operation_id'])
        if not self.supports(resource_kind, operation_id):
            raise RelationalClientError(
                'relational administration operation is unavailable'
            )
        normalized_request = copy.deepcopy(dict(request))
        normalized_request['draft'] = self._normalize_draft(
            resource_kind, operation_id, request.get('draft', {})
        )
        compiled = self._compile(normalized_request)
        statements = compiled.get('statements', [])
        preview = []
        for statement in statements:
            preview.append({
                'source': statement.get(
                    'preview_source', statement['source']
                ),
                'parameter_count': len(statement.get('parameters', ())),
                'parameters_redacted': bool(statement.get('parameters')),
            })
        return {
            'command_preview': {
                'engine_id': self.dialect.engine_id,
                'operation': f'{operation_id}_{resource_kind}',
                'statements': preview,
                'provider_constructed': True,
                'driver_operation': compiled.get('driver_operation'),
            },
            'provider_payload': {
                'route': copy.deepcopy(dict(route)),
                'compiled': copy.deepcopy(compiled),
            },
            'warnings': copy.deepcopy(compiled.get('warnings', [])),
            'receipt': {
                'planner': 'cdeadmin.relational-admin.v1',
                'transaction_finality_interpreted_by_planner': False,
            },
        }

    def apply(self, client, request):
        payload = request.get('provider_payload')
        if not isinstance(payload, Mapping):
            raise RelationalClientError('relational native plan is invalid')
        route = payload.get('route')
        compiled = payload.get('compiled')
        if not isinstance(route, Mapping) or not isinstance(compiled, Mapping):
            raise RelationalClientError('relational native plan is incomplete')
        internal = compiled.get('internal_operation')
        if internal == 'inspect':
            target = compiled['target_resource']
            return {
                'accepted': True,
                'resource': client.inspect_resource({
                    'route': copy.deepcopy(dict(route)),
                    'resource_id': target['resource_id'],
                }),
                'commit_requested': False,
                'rollback_requested': False,
                'driver_observation_only': True,
            }
        driver_operation = compiled.get('driver_operation')
        if driver_operation:
            observation = client.create_database(
                {'route': route}, compiled['database'], driver_operation
            )
            return {
                'accepted': True,
                'commit_requested': False,
                'rollback_requested': False,
                'driver_observation_only': True,
                'driver_observation': observation,
                'transaction_finality_interpreted_by_common_code': False,
            }
        connection = client._connect({'route': route})
        cursor = None
        commit_requested = False
        rollback_requested = False
        results = []
        try:
            cursor = connection.cursor()
            for statement in compiled.get('statements', []):
                parameters = statement.get('parameters', ())
                if parameters:
                    cursor.execute(statement['source'], parameters)
                else:
                    cursor.execute(statement['source'])
                try:
                    rowcount = getattr(cursor, 'rowcount', None)
                except Exception:
                    # Some DB-API drivers execute DDL successfully but raise
                    # when asked for statement row statistics afterwards.
                    # Row counts are optional unless this statement declares
                    # an exact concurrency expectation below.
                    rowcount = None
                description = getattr(cursor, 'description', None)
                rows = list(cursor.fetchall()) if description else []
                expected = statement.get('expected_rowcount')
                observed_rowcount = rowcount
                if (
                    expected is not None and
                    (not isinstance(rowcount, int) or rowcount < 0) and
                    len(rows) == 1 and len(rows[0]) == 1 and
                    isinstance(rows[0][0], int)
                ):
                    observed_rowcount = rows[0][0]
                if expected is not None and observed_rowcount != expected:
                    raise RelationalClientError(
                        'row identity no longer identifies exactly one row'
                    )
                results.append({
                    'rowcount': (
                        observed_rowcount
                        if isinstance(observed_rowcount, int) else None
                    ),
                    'rows': copy.deepcopy(rows),
                })
            commit = getattr(connection, 'commit', None)
            if callable(commit):
                commit_requested = True
                commit()
        except RelationalClientError:
            rollback = getattr(connection, 'rollback', None)
            if callable(rollback):
                rollback_requested = True
                rollback()
            raise
        except Exception as exc:
            rollback = getattr(connection, 'rollback', None)
            if callable(rollback):
                rollback_requested = True
                try:
                    rollback()
                except Exception:
                    pass
            raise RelationalClientError(
                'relational administration execution failed '
                f'({type(exc).__name__})'
            ) from None
        finally:
            if cursor is not None:
                client._safe_close(cursor)
            client._forget_and_close(connection)
        return {
            'accepted': True,
            'statement_results': results,
            'commit_requested': commit_requested,
            'rollback_requested': rollback_requested,
            'driver_observation_only': True,
            'transaction_finality_interpreted_by_common_code': False,
        }

    def read_rows(self, client, request):
        route = request.get('_provider_route')
        target = request.get('target_resource')
        if not isinstance(route, Mapping) or not isinstance(target, Mapping):
            raise RelationalClientError(
                'row paging requires a trusted route and table resource'
            )
        if target.get('resource_kind') != 'table':
            raise RelationalClientError('row paging requires a base table')
        limit = request.get('limit', 200)
        if isinstance(limit, bool) or not isinstance(limit, int):
            raise RelationalClientError('row page limit must be an integer')
        limit = max(1, min(limit, 500))
        path = self._target_path(target)
        connection = client._connect({'route': route})
        cursor = None
        try:
            key_columns = tuple(self._primary_key(connection, path))
            cursor = connection.cursor()
            order = (
                ' ORDER BY ' + ', '.join(
                    self._quote(name) for name in key_columns
                ) if key_columns else ''
            )
            if self.dialect.engine_id == 'firebird':
                source = (
                    f'SELECT FIRST {limit} * FROM '
                    f'{self._qualified(path)}{order}'
                )
            else:
                source = (
                    f'SELECT * FROM {self._qualified(path)}{order} '
                    f'LIMIT {limit}'
                )
            cursor.execute(source)
            description = getattr(cursor, 'description', None) or ()
            columns = tuple(str(item[0]) for item in description)
            native_types = tuple(
                None if len(item) < 2 else str(item[1])
                for item in description
            )
            raw_rows = list(cursor.fetchall())
            result_rows = []
            fingerprint = self._route_fingerprint(route)
            for raw_row in raw_rows:
                values = dict(zip(columns, raw_row))
                identity_token = None
                if key_columns and all(key in values for key in key_columns):
                    identity_token = str(uuid.uuid4())
                    identity = _RowIdentity(
                        fingerprint, path, key_columns,
                        tuple(values[key] for key in key_columns),
                        copy.deepcopy(values),
                        time.monotonic(),
                    )
                    with self._identity_lock:
                        while len(self._row_identities) >= 5000:
                            oldest = next(iter(self._row_identities))
                            self._row_identities.pop(oldest, None)
                        self._row_identities[identity_token] = identity
                result_rows.append({
                    'values': copy.deepcopy(values),
                    'identity_token': identity_token,
                })
            return {
                'schema': 'cdeadmin.relational-row-page.v1',
                'columns': [
                    {
                        'name': name,
                        'native_type': native_types[index],
                        'key': name in key_columns,
                        'editable': bool(key_columns),
                    }
                    for index, name in enumerate(columns)
                ],
                'rows': result_rows,
                'editable': bool(key_columns),
                'identity_policy': (
                    'provider-primary-key-and-original-values'
                    if key_columns else 'read-only-no-primary-key'
                ),
                'limit': limit,
                'complete': len(raw_rows) < limit,
                'transaction_finality_interpreted_by_common_code': False,
            }
        except RelationalClientError:
            raise
        except Exception as exc:
            raise RelationalClientError(
                f'relational row paging failed ({type(exc).__name__})'
            ) from None
        finally:
            if cursor is not None:
                client._safe_close(cursor)
            client._forget_and_close(connection)

    def _compile(self, request):
        operation = request['operation_id']
        if operation == 'inspect':
            return {
                'internal_operation': 'inspect',
                'target_resource': copy.deepcopy(request['target_resource']),
                'statements': [],
            }
        if operation == 'create':
            if request['resource_kind'] == 'database' and (
                self.dialect.database_create_mode != 'sql'
            ):
                return self._compile_database_create(request)
            return {'statements': self._compile_create(request)}
        if operation == 'alter':
            return {'statements': self._compile_alter(request)}
        if operation == 'rename':
            return {'statements': [self._compile_rename(request)]}
        if operation == 'drop':
            return {'statements': [self._compile_drop(request)]}
        if operation == 'insert':
            return {'statements': [self._compile_insert(request)]}
        if operation in {'update', 'delete'}:
            return {'statements': [self._compile_identity_dml(request)]}
        if operation in {'grant', 'revoke'}:
            return {'statements': [self._compile_privilege(request)]}
        if operation == 'execute':
            return {'statements': [self._compile_execute(request)]}
        raise RelationalClientError(
            'relational operation has no provider compiler'
        )

    def _compile_database_create(self, request):
        name = request['draft'].get('name')
        if not isinstance(name, str) or not re.fullmatch(
            r'[A-Za-z0-9][A-Za-z0-9_.-]{0,254}', name
        ) or '..' in name:
            raise RelationalClientError(
                'database name must be a safe unqualified file name'
            )
        route = request.get('_provider_route')
        root = route.get('database_create_root') if isinstance(
            route, Mapping
        ) else None
        mode = self.dialect.database_create_mode
        if (not isinstance(root, str) or not root) and isinstance(
            route, Mapping
        ):
            current = route.get('database')
            if isinstance(current, str) and current not in {
                '', ':memory:', ':default:',
            }:
                if mode == 'embedded-file' and os.path.isabs(current):
                    root = os.path.dirname(current)
                elif mode == 'firebird-driver':
                    server_path = current.split(':', 1)[-1]
                    if server_path.startswith('/'):
                        root = posixpath.dirname(server_path)
        if not isinstance(root, str) or not root:
            raise RelationalClientError(
                'endpoint has no approved database creation root'
            )
        extension = self.dialect.database_extension
        filename = name if name.endswith(extension) else name + extension
        if mode == 'embedded-file':
            root_path = os.path.realpath(root)
            database = os.path.realpath(os.path.join(root_path, filename))
            if os.path.commonpath((root_path, database)) != root_path:
                raise RelationalClientError(
                    'database file escapes the approved creation root'
                )
            driver_operation = 'embedded-create-database'
        elif mode == 'firebird-driver':
            root_path = posixpath.normpath(root)
            database_path = posixpath.normpath(
                posixpath.join(root_path, filename)
            )
            if not (
                database_path == root_path or
                database_path.startswith(root_path.rstrip('/') + '/')
            ):
                raise RelationalClientError(
                    'database file escapes the approved creation root'
                )
            host = route.get('host')
            port = route.get('port')
            host_spec = (
                f'{host}/{port}' if isinstance(port, int) else host
            )
            database = (
                f'{host_spec}:{database_path}'
                if isinstance(host_spec, str) and host_spec
                else database_path
            )
            driver_operation = 'firebird-create-database'
        else:
            raise RelationalClientError(
                'database creation mode is unavailable'
            )
        return {
            'driver_operation': driver_operation,
            'database': database,
            'statements': [],
            'warnings': [
                'Database creation uses the endpoint-approved creation root.'
            ],
        }

    def _normalize_draft(self, kind, operation, draft):
        if not isinstance(draft, Mapping):
            raise RelationalClientError('administration draft is invalid')
        value = copy.deepcopy(dict(draft))
        if operation == 'create':
            options = copy.deepcopy(value.pop('options', {}) or {})
            for key in (
                'parent', 'table', 'columns', 'constraints', 'unique',
                'start', 'increment', 'minimum', 'maximum', 'cycle',
                'data_type', 'nullable', 'default', 'not_null', 'check',
                'primary_key',
                'parameters', 'returns', 'timing', 'events', 'host',
                'password', 'plugin', 'administrator', 'active',
                'system_privileges', 'drop_system_privileges', 'members',
                'position', 'return_parameters', 'header', 'message',
                'schedule', 'preserve', 'enabled',
                'type_kind', 'base_type', 'enum_values', 'fields',
                'expression', 'table_macro', 'secret_type', 'scope',
                'storage', 'persistent', 'module', 'library', 'database',
            ):
                if key in value:
                    options[key] = value.pop(key)
            if 'query' in value:
                value['definition'] = value.pop('query')
            if 'body' in value:
                value['definition'] = value.pop('body')
            if 'properties' in value:
                properties = value.pop('properties')
                if isinstance(properties, Mapping):
                    options.update(properties)
            value['options'] = options
        elif operation == 'alter':
            if kind == 'table':
                value['changes'] = {
                    key: value.pop(key)
                    for key in (
                        'add_columns', 'drop_columns', 'rename_columns'
                    ) if key in value
                }
            elif kind == 'database' and self.dialect.sql_family == 'mysql':
                value['changes'] = {
                    key: value.pop(key)
                    for key in ('character_set', 'collation')
                    if key in value and value[key] not in {None, ''}
                }
            elif kind == 'user':
                value['changes'] = {
                    key: value.pop(key)
                    for key in (
                        'password', 'plugin', 'administrator', 'active'
                    ) if key in value and value[key] not in {None, ''}
                }
            elif kind == 'role' and self.dialect.engine_id == 'firebird':
                value['changes'] = {
                    key: value.pop(key)
                    for key in (
                        'system_privileges', 'drop_system_privileges'
                    ) if key in value
                }
            elif kind == 'index' and self.dialect.engine_id == 'firebird':
                value['changes'] = {
                    'active': value.pop('active')
                }
            elif kind == 'sequence':
                value['changes'] = {
                    key: value.pop(key)
                    for key in ('restart', 'increment') if key in value and (
                        value[key] is not None and value[key] != ''
                    )
                }
            elif kind == 'domain' and self.dialect.engine_id == 'firebird':
                value['changes'] = {
                    key: value.pop(key)
                    for key in (
                        'data_type', 'default', 'drop_default', 'not_null',
                        'check', 'drop_constraint',
                    ) if key in value and value[key] not in {None, ''}
                }
            elif kind == 'exception' and (
                    self.dialect.engine_id == 'firebird'):
                value['changes'] = {'message': value.pop('message')}
            elif kind == 'publication' and (
                    self.dialect.engine_id == 'firebird'):
                value['changes'] = {
                    key: value.pop(key)
                    for key in (
                        'enabled', 'include_tables', 'exclude_tables'
                    ) if key in value
                }
            elif kind in {'macro', 'function'} and (
                self.dialect.engine_id == 'duckdb'
            ):
                value['changes'] = {
                    key: value.pop(key)
                    for key in ('parameters', 'table_macro', 'expression')
                    if key in value
                }
            elif kind == 'pragma' and self.dialect.engine_id == 'sqlite':
                value['changes'] = {'value': value.pop('value')}
            elif kind in {
                'trigger', 'procedure', 'function', 'package', 'view',
            }:
                value['changes'] = {
                    key: value.pop(key)
                    for key in (
                        'active', 'timing', 'events', 'position',
                        'parameters', 'return_parameters', 'returns',
                        'header',
                    ) if key in value
                }
                if 'body' in value:
                    value['definition'] = value.pop('body')
                if 'query' in value:
                    value['definition'] = value.pop('query')
            elif kind == 'event':
                value['changes'] = {
                    key: value.pop(key)
                    for key in ('schedule', 'preserve', 'enabled')
                    if key in value
                }
                if 'body' in value:
                    value['definition'] = value.pop('body')
            elif 'properties' in value:
                value['changes'] = value.pop('properties')
        return value

    def _form(self, kind, operation):
        title = operation.replace('_', ' ').title()
        if operation == 'inspect':
            return {'form_id': f'{kind}.inspect', 'title': title, 'fields': []}
        if kind == 'privilege' and operation in {'grant', 'revoke'}:
            object_types = (
                ('TABLE', 'VIEW', 'PROCEDURE', 'FUNCTION', 'SEQUENCE',
                 'DATABASE')
                if self.dialect.sql_family == 'mysql'
                else ('TABLE', 'VIEW', 'PROCEDURE', 'FUNCTION', 'SEQUENCE')
            )
            fields = [
                self._field('principal', 'Principal', 'text', True),
                self._field(
                    'object_type', 'Object type', 'select', True,
                    options=object_types,
                ),
                self._field('object_name', 'Object name', 'text', True),
                self._field('privileges', 'Privileges', 'json', True),
            ]
            if operation == 'grant':
                fields.append(self._field(
                    'grant_option', 'With grant option', 'boolean', False,
                    default=False,
                ))
            else:
                fields.append(self._field(
                    'confirmation', 'Confirmation', 'text', True
                ))
            return {
                'form_id': f'privilege.{operation}', 'title': title,
                'fields': fields,
            }
        if kind == 'extension' and operation == 'execute':
            return {
                'form_id': 'extension.execute',
                'title': 'Manage extension',
                'fields': [self._field(
                    'action', 'Action', 'select', True,
                    options=('INSTALL', 'LOAD'),
                )],
            }
        if operation == 'create':
            fields = [self._field('name', 'Name', 'text', True)]
            if kind in {
                'table', 'view', 'index', 'sequence', 'domain', 'column',
                'constraint', 'trigger', 'procedure', 'function', 'package',
                'event', 'materialization',
            }:
                fields.append(self._field(
                    'parent', 'Parent database/schema', 'text', False,
                    'Use a qualified database or schema name where needed.',
                ))
            elif kind == 'role' and self.dialect.engine_id == 'firebird':
                fields.append(self._field(
                    'system_privileges', 'System privileges', 'json', False,
                    'Array of Firebird system privilege names.', [],
                ))
            elif kind == 'role' and self.dialect.engine_id in {
                'mysql', 'dolt',
            }:
                fields.append(self._field(
                    'members', 'Initial members', 'json', False,
                    'Account names that receive this role. MySQL persists '
                    'role identity through role membership edges.', [],
                ))
            if kind == 'table':
                fields.extend((
                    self._field(
                        'columns', 'Columns', 'json', True,
                        'Array of name, type, nullable, default, unique and '
                        'primary_key properties.',
                    ),
                    self._field(
                        'constraints', 'Table constraints', 'json', False,
                        'Structured PRIMARY KEY, UNIQUE, FOREIGN KEY or CHECK '
                        'constraints.', [],
                    ),
                ))
            elif kind == 'view':
                fields.append(self._field(
                    'query', 'View query', 'code', True,
                    'One SELECT or WITH query; do not include CREATE VIEW.',
                ))
            elif kind == 'index':
                fields.extend((
                    self._field('table', 'Table', 'text', True),
                    self._field('columns', 'Indexed columns', 'json', True),
                    self._field('unique', 'Unique index', 'boolean', False,
                                default=False),
                ))
            elif kind == 'sequence':
                sequence_fields = [
                    self._field('start', 'Start value', 'number', False),
                    self._field(
                        'increment', 'Increment', 'number', False
                    ),
                ]
                if self.dialect.engine_id != 'firebird':
                    sequence_fields.extend((
                        self._field(
                            'minimum', 'Minimum', 'number', False
                        ),
                        self._field(
                            'maximum', 'Maximum', 'number', False
                        ),
                        self._field(
                            'cycle', 'Cycle', 'boolean', False,
                            default=False,
                        ),
                    ))
                fields.extend(sequence_fields)
            elif kind == 'domain':
                fields.append(self._field(
                    'data_type', 'Base data type', 'text', True
                ))
                if self.dialect.engine_id == 'firebird':
                    fields.extend((
                        self._field(
                            'default', 'Default expression', 'text'
                        ),
                        self._field(
                            'not_null', 'Not null', 'boolean', False,
                            default=False,
                        ),
                        self._field(
                            'check', 'Check expression', 'text'
                        ),
                    ))
            elif kind == 'column':
                fields.extend((
                    self._field('table', 'Table', 'text', True),
                    self._field('data_type', 'Data type', 'text', True),
                    self._field('nullable', 'Nullable', 'boolean', False,
                                default=True),
                    self._field('default', 'Default expression', 'text'),
                    self._field('primary_key', 'Primary key', 'boolean',
                                default=False),
                ))
            elif kind == 'constraint':
                fields.extend((
                    self._field('table', 'Table', 'text', True),
                    self._field(
                        'properties', 'Constraint properties', 'json', True
                    ),
                ))
            elif kind == 'trigger':
                fields.extend((
                    self._field('table', 'Table', 'text', True),
                    self._field('timing', 'Timing', 'select', True,
                                options=('BEFORE', 'AFTER', 'INSTEAD OF')),
                    self._field('events', 'Events', 'json', True,
                                default=['INSERT']),
                    self._field('active', 'Active', 'boolean', False,
                                default=True),
                    self._field('position', 'Position', 'number', False,
                                default=0),
                    self._field(
                        'body', 'Trigger body', 'code', True,
                        'Enter only the trigger body, not CREATE TRIGGER.',
                    ),
                ))
            elif kind in {'procedure', 'function', 'package'} and not (
                kind == 'function' and self.dialect.engine_id == 'duckdb'
            ):
                if kind == 'package' and self.dialect.engine_id in {
                    'firebird', 'mariadb',
                }:
                    prefix = (
                        'Package header'
                        if self.dialect.engine_id == 'firebird'
                        else 'Package specification'
                    )
                    fields.extend((
                        self._field(
                            'header', prefix, 'code', True,
                            'Declarations only; do not include the CREATE '
                            'PACKAGE prefix.',
                        ),
                        self._field(
                            'body', 'Package body', 'code', True,
                            'Implementations only; do not include the CREATE '
                            'PACKAGE BODY prefix.',
                        ),
                    ))
                else:
                    fields.extend((
                        self._field(
                            'parameters', 'Input parameters', 'json', False,
                            default=[],
                        ),
                        self._field(
                            'return_parameters', 'Output parameters', 'json',
                            False, default=[],
                        ),
                        self._field(
                            'returns', 'Return type', 'text', False
                        ),
                        self._field(
                            'body', f'{kind.title()} body', 'code', True,
                            f'Enter only the {kind} body, not a CREATE '
                            'command.',
                        ),
                    ))
            elif kind == 'exception' and (
                self.dialect.engine_id == 'firebird'
            ):
                fields.append(self._field(
                    'message', 'Exception message', 'text', True
                ))
            elif kind == 'event' and self.dialect.engine_id in {
                'mysql', 'mariadb', 'dolt',
            }:
                fields.extend((
                    self._field(
                        'schedule', 'Schedule expression', 'text', True,
                        'For example: EVERY 1 DAY or AT CURRENT_TIMESTAMP.',
                    ),
                    self._field('preserve', 'Preserve after completion',
                                'boolean', default=False),
                    self._field('enabled', 'Enabled', 'boolean',
                                default=True),
                    self._field(
                        'body', 'Event body', 'code', True,
                        'Enter only the statement run by the event.',
                    ),
                ))
            elif kind == 'plugin' and self.dialect.sql_family == 'mysql':
                fields.append(self._field(
                    'library', 'Shared library', 'text', True,
                    'Enter the provider library filename, without a path.',
                ))
            elif kind == 'materialization' and (
                    self.dialect.engine_id == 'duckdb'):
                fields.append(self._field(
                    'database', 'Target database/schema', 'text', False,
                ))
                fields.append(self._field(
                    'select', 'Provider-compiled SELECT', 'code', True,
                    'Generated by the semantic model workspace.',
                ))
            elif kind == 'type' and self.dialect.engine_id == 'duckdb':
                fields.extend((
                    self._field(
                        'type_kind', 'Type kind', 'select', True,
                        options=('ALIAS', 'ENUM', 'STRUCT', 'UNION'),
                    ),
                    self._field('base_type', 'Alias base type', 'text'),
                    self._field('enum_values', 'Enum values', 'json', False,
                                default=[]),
                    self._field('fields', 'Struct/union fields', 'json',
                                False, default=[]),
                ))
            elif kind in {'macro', 'function'} and (
                self.dialect.engine_id == 'duckdb'
            ):
                fields.extend((
                    self._field('parameters', 'Parameters', 'json', False,
                                default=[]),
                    self._field('table_macro', 'Table-returning', 'boolean',
                                default=False),
                    self._field(
                        'expression', 'Expression/query', 'code', True,
                        'Enter the expression or SELECT, not CREATE.',
                    ),
                ))
            elif kind == 'secret' and self.dialect.engine_id == 'duckdb':
                fields.extend((
                    self._field('secret_type', 'Secret type', 'text', True),
                    self._field('scope', 'Scope', 'text'),
                    self._field('storage', 'Storage', 'text'),
                    self._field('persistent', 'Persistent', 'boolean',
                                default=False),
                    self._field(
                        'properties', 'Secret properties', 'json', True,
                        'Property values are redacted from the plan.',
                        sensitive=True,
                    ),
                ))
            elif kind in {'virtual-table', 'fts-table'} and (
                self.dialect.engine_id == 'sqlite'
            ):
                fields.extend((
                    self._field('module', 'Module', 'select', True,
                                options=('fts5', 'rtree')),
                    self._field('columns', 'Module columns', 'json', True),
                ))
            elif kind == 'user':
                user_fields = [
                    self._field('host', 'Host', 'text', False,
                                default='%'),
                    self._field(
                        'password', 'Password', 'password', True,
                        'The value is redacted from validation and plans.',
                        sensitive=True,
                    ),
                ]
                if self.dialect.engine_id != 'dolt':
                    user_fields.extend((
                        self._field(
                            'plugin', 'Authentication plugin', 'text'
                        ),
                        self._field('active', 'Account active', 'boolean',
                                    default=True),
                        self._field(
                            'administrator', 'Administrator', 'boolean',
                            default=False,
                        ),
                    ))
                fields.extend(user_fields)
            elif kind not in {'database', 'schema', 'role'}:
                fields.append(self._field(
                    'properties', 'Object properties', 'json', False,
                    default={},
                ))
            return {
                'form_id': f'{kind}.create',
                'title': f'Create {kind.replace("-", " ")}',
                'fields': fields,
            }
        if operation == 'alter' and kind == 'table':
            return {
                'form_id': 'table.alter', 'title': 'Alter table',
                'fields': [
                    self._field('add_columns', 'Add columns', 'json', False,
                                default=[]),
                    self._field('drop_columns', 'Drop columns', 'json', False,
                                default=[]),
                    self._field(
                        'rename_columns', 'Rename columns', 'json', False,
                        default=[],
                    ),
                ],
            }
        if operation == 'alter' and kind == 'database' and (
            self.dialect.sql_family == 'mysql'
        ):
            return {
                'form_id': 'database.alter', 'title': 'Alter database',
                'fields': [
                    self._field(
                        'character_set', 'Default character set', 'text'
                    ),
                    self._field(
                        'collation', 'Default collation', 'text'
                    ),
                ],
            }
        if operation == 'alter' and kind == 'user':
            fields = [
                self._field(
                    'password', 'New password', 'password', False,
                    'Leave blank to retain the current password.',
                    sensitive=True,
                ),
            ]
            if self.dialect.engine_id != 'dolt':
                fields.extend((
                    self._field('plugin', 'Authentication plugin', 'text'),
                    self._field('active', 'Account active', 'boolean',
                                default=True),
                    self._field('administrator', 'Administrator', 'boolean',
                                default=False),
                ))
            return {
                'form_id': 'user.alter', 'title': 'Alter user',
                'fields': fields,
            }
        if operation == 'alter' and kind == 'role' and (
            self.dialect.engine_id == 'firebird'
        ):
            return {
                'form_id': 'role.alter', 'title': 'Alter role',
                'fields': [
                    self._field(
                        'system_privileges', 'System privileges', 'json',
                        False, 'Array of Firebird system privilege names.',
                        [],
                    ),
                    self._field(
                        'drop_system_privileges',
                        'Drop all system privileges', 'boolean', False,
                        default=False,
                    ),
                ],
            }
        if operation == 'alter' and kind == 'index' and (
            self.dialect.engine_id == 'firebird'
        ):
            return {
                'form_id': 'index.alter', 'title': 'Alter index',
                'fields': [self._field(
                    'active', 'Active', 'boolean', False, default=True
                )],
            }
        if operation == 'alter' and kind == 'publication' and (
            self.dialect.engine_id == 'firebird'
        ):
            return {
                'form_id': 'publication.alter',
                'title': 'Alter publication',
                'fields': [
                    self._field(
                        'enabled', 'Publication enabled', 'boolean', False,
                        default=True,
                    ),
                    self._field(
                        'include_tables', 'Include tables', 'json', False,
                        'Array of table names to include.', [],
                    ),
                    self._field(
                        'exclude_tables', 'Exclude tables', 'json', False,
                        'Array of table names to exclude.', [],
                    ),
                ],
            }
        if operation == 'alter' and kind == 'sequence':
            return {
                'form_id': 'sequence.alter', 'title': 'Alter sequence',
                'fields': [
                    self._field('restart', 'Restart with', 'number'),
                    self._field('increment', 'Increment by', 'number'),
                ],
            }
        if operation == 'alter' and kind == 'domain' and (
            self.dialect.engine_id == 'firebird'
        ):
            return {
                'form_id': 'domain.alter', 'title': 'Alter domain',
                'fields': [
                    self._field('data_type', 'Replacement type', 'text'),
                    self._field('default', 'Default expression', 'text'),
                    self._field(
                        'drop_default', 'Drop default', 'boolean', False,
                        default=False,
                    ),
                    self._field('not_null', 'Not null', 'boolean'),
                    self._field('check', 'Check expression', 'text'),
                    self._field(
                        'drop_constraint', 'Drop check constraint',
                        'boolean', False, default=False,
                    ),
                ],
            }
        if operation == 'alter' and kind == 'exception' and (
            self.dialect.engine_id == 'firebird'
        ):
            return {
                'form_id': 'exception.alter', 'title': 'Alter exception',
                'fields': [self._field(
                    'message', 'Replacement message', 'text', True
                )],
            }
        if operation == 'alter' and kind == 'event' and (
            self.dialect.engine_id in {'mysql', 'mariadb', 'dolt'}
        ):
            return {
                'form_id': 'event.alter', 'title': 'Alter event',
                'fields': [
                    self._field('schedule', 'Schedule expression', 'text'),
                    self._field('preserve', 'Preserve after completion',
                                'boolean', default=False),
                    self._field('enabled', 'Enabled', 'boolean',
                                default=True),
                    self._field('body', 'Replacement event body', 'code'),
                ],
            }
        if operation == 'alter' and kind in {'macro', 'function'} and (
            self.dialect.engine_id == 'duckdb'
        ):
            return {
                'form_id': f'{kind}.alter', 'title': f'Alter {kind}',
                'fields': [
                    self._field('parameters', 'Parameters', 'json', False,
                                default=[]),
                    self._field('table_macro', 'Table-returning', 'boolean',
                                default=False),
                    self._field('expression', 'Replacement expression/query',
                                'code', True),
                ],
            }
        if operation == 'alter' and kind == 'pragma' and (
            self.dialect.engine_id == 'sqlite'
        ):
            return {
                'form_id': 'pragma.alter', 'title': 'Set PRAGMA',
                'fields': [self._field('value', 'Value', 'text', True)],
            }
        if operation == 'alter' and kind == 'trigger' and (
            self.dialect.engine_id == 'firebird'
        ):
            return {
                'form_id': 'trigger.alter', 'title': 'Alter trigger',
                'fields': [
                    self._field('timing', 'Timing', 'select', True,
                                options=('BEFORE', 'AFTER')),
                    self._field('events', 'Events', 'json', True,
                                default=['INSERT']),
                    self._field('active', 'Active', 'boolean', False,
                                default=True),
                    self._field('position', 'Position', 'number', False,
                                default=0),
                    self._field(
                        'body', 'Trigger body', 'code', True,
                        'Enter only the body following AS.',
                    ),
                ],
            }
        if operation == 'alter' and kind in {'procedure', 'function'} and (
            self.dialect.engine_id == 'firebird'
        ):
            return {
                'form_id': f'{kind}.alter', 'title': f'Alter {kind}',
                'fields': [
                    self._field('parameters', 'Input parameters', 'json',
                                False, default=[]),
                    self._field('return_parameters', 'Output parameters',
                                'json', False, default=[]),
                    self._field('returns', 'Return type', 'text'),
                    self._field(
                        'body', f'{kind.title()} body', 'code', True,
                        'Enter only the body following AS.',
                    ),
                ],
            }
        if operation == 'alter' and kind == 'package' and (
            self.dialect.engine_id in {'firebird', 'mariadb'}
        ):
            return {
                'form_id': 'package.alter', 'title': 'Alter package',
                'fields': [
                    self._field('header', 'Package header', 'code', True),
                    self._field('body', 'Package body', 'code', True),
                ],
            }
        if operation == 'alter' and kind == 'view':
            return {
                'form_id': f'{kind}.alter', 'title': f'Alter {kind}',
                'fields': [self._field(
                    'query', 'Replacement query', 'code', True,
                    'Enter one SELECT or WITH query.',
                )],
            }
        if operation == 'alter':
            return {
                'form_id': f'{kind}.alter', 'title': f'Alter {kind}',
                'fields': [self._field(
                    'properties', 'Changed properties', 'json', True
                )],
            }
        return self._existing_operation_form(kind, operation)

    @staticmethod
    def _field(field_id, label, control, required=False, help_text='',
               default=None, options=None, sensitive=False):
        value = {
            'field_id': field_id, 'label': label, 'control': control,
            'required': required,
        }
        if help_text:
            value['help'] = help_text
        if default is not None:
            value['default'] = default
        if options is not None:
            value['options'] = [
                {'value': item, 'label': item.replace('_', ' ').title()}
                for item in options
            ]
        if sensitive:
            value['sensitive'] = True
        return value

    def _existing_operation_form(self, kind, operation):
        fields = {
            'rename': [self._field('new_name', 'New name', 'text', True)],
            'drop': [
                self._field('cascade', 'Include dependent objects',
                            'boolean', default=False),
                self._field('confirmation', 'Confirmation', 'text', True),
            ],
            'insert': [
                self._field('values', 'Column values', 'json', True),
                self._field('options', 'Insert options', 'json', False,
                            default={}),
            ],
            'update': [
                self._field('selector', 'Provider row identity', 'json', True),
                self._field('changes', 'Changed column values', 'json', True),
                self._field('concurrency_token', 'Concurrency token', 'text'),
            ],
            'delete': [
                self._field('selector', 'Provider row identity', 'json', True),
                self._field('concurrency_token', 'Concurrency token', 'text'),
                self._field('confirmation', 'Confirmation', 'text', True),
            ],
            'grant': [
                self._field('principal', 'Principal', 'text', True),
                self._field('privileges', 'Privileges', 'json', True),
                self._field('options', 'Grant options', 'json', False,
                            default={}),
            ],
            'revoke': [
                self._field('principal', 'Principal', 'text', True),
                self._field('privileges', 'Privileges', 'json', True),
                self._field('confirmation', 'Confirmation', 'text', True),
            ],
        }.get(operation, [])
        return {
            'form_id': f'{kind}.{operation}',
            'title': operation.replace('_', ' ').title(),
            'fields': fields,
        }

    def _compile_create(self, request):
        kind = request['resource_kind']
        draft = request['draft']
        name = self._identifier(draft['name'])
        options = draft.get('options') or {}
        if not isinstance(options, Mapping):
            raise RelationalClientError('create options must be an object')
        qualified = self._new_object_name(name, options)
        if kind == 'table':
            columns = options.get('columns')
            if not isinstance(columns, list) or not columns:
                raise RelationalClientError(
                    'table creation requires at least one structured column'
                )
            definitions = [self._column_definition(item) for item in columns]
            for constraint in options.get('constraints', []):
                definitions.append(self._constraint_definition(constraint))
            source = f'CREATE TABLE {qualified} ({", ".join(definitions)})'
        elif kind == 'view':
            query = self._query_body(draft.get('definition'))
            source = f'CREATE VIEW {qualified} AS {query}'
        elif kind == 'index':
            table = self._option_path(options, 'table')
            columns = self._identifier_list(options.get('columns'))
            unique = 'UNIQUE ' if options.get('unique') else ''
            index_name = qualified
            table_name = self._qualified(table)
            if self.dialect.engine_id == 'sqlite':
                table_name = self._quote(table[-1])
            elif (
                self.dialect.sql_family == 'postgresql' or
                self.dialect.engine_id in {
                    'duckdb', 'firebird', 'mysql', 'mariadb', 'dolt',
                    'tidb', 'vitess',
                }
            ):
                index_name = self._quote(name)
            source = (
                f'CREATE {unique}INDEX {index_name} ON '
                f'{table_name} ({columns})'
            )
        elif kind == 'sequence':
            source = f'CREATE SEQUENCE {qualified}'
            for key, phrase in (
                ('start', ' START WITH '), ('increment', ' INCREMENT BY '),
                ('minimum', ' MINVALUE '), ('maximum', ' MAXVALUE '),
            ):
                if key in options:
                    source += phrase + str(self._integer(options[key], key))
            if options.get('cycle'):
                source += ' CYCLE'
        elif kind == 'package' and self.dialect.engine_id == 'firebird':
            header = self._safe_definition(options.get('header'))
            body = self._safe_definition(draft.get('definition'))
            if not header or not body:
                raise RelationalClientError(
                    'Firebird package requires a header and body'
                )
            return [
                {
                    'source': f'CREATE PACKAGE {qualified} AS {header}',
                    'parameters': (),
                },
                {
                    'source': f'CREATE PACKAGE BODY {qualified} AS {body}',
                    'parameters': (),
                },
            ]
        elif kind == 'package' and self.dialect.engine_id == 'mariadb':
            specification = self._safe_definition(options.get('header'))
            body = self._safe_definition(draft.get('definition'))
            if not specification or not body:
                raise RelationalClientError(
                    'MariaDB package requires a specification and body'
                )
            return [
                {
                    'source': (
                        f'CREATE PACKAGE {qualified} {specification}'
                    ),
                    'parameters': (),
                },
                {
                    'source': f'CREATE PACKAGE BODY {qualified} {body}',
                    'parameters': (),
                },
            ]
        elif kind == 'user':
            source, preview = self._create_user(name, options)
            statements = [{
                'source': source,
                'preview_source': preview,
                'parameters': (),
            }]
            if self.dialect.engine_id == 'cockroachdb' and options.get(
                    'administrator'):
                statements.append({
                    'source': f'GRANT admin TO {self._quote(name)}',
                    'parameters': (),
                })
            return statements
        elif kind == 'role':
            role = (
                self._quote(name)
                if self.dialect.engine_id == 'mariadb'
                else self._account(name)
            )
            source = f'CREATE ROLE {role}'
            privileges = options.get('system_privileges')
            if privileges:
                source += ' SET SYSTEM PRIVILEGES TO ' + (
                    self._privilege_names(privileges)
                )
            members = options.get('members') or []
            if members:
                if self.dialect.engine_id not in {'mysql', 'dolt'}:
                    raise RelationalClientError(
                        'initial role members are unavailable for this engine'
                    )
                if not isinstance(members, list):
                    raise RelationalClientError(
                        'initial role members must be an array'
                    )
                return [
                    {'source': source, 'parameters': ()},
                    {
                        'source': (
                            f'GRANT {role} TO ' + ', '.join(
                                self._account(member) for member in members
                            )
                        ),
                        'parameters': (),
                    },
                ]
        elif kind in {'database', 'schema'}:
            keyword = self._keyword(kind)
            source = f'CREATE {keyword} {qualified}'
        elif kind == 'domain':
            data_type = self._safe_fragment(options.get('data_type'), 'type')
            source = f'CREATE DOMAIN {qualified} AS {data_type}'
            if self.dialect.engine_id == 'firebird':
                if options.get('default') not in {None, ''}:
                    source += ' DEFAULT ' + self._safe_fragment(
                        options['default'], 'domain default'
                    )
                if options.get('not_null'):
                    source += ' NOT NULL'
                if options.get('check') not in {None, ''}:
                    source += ' CHECK (' + self._safe_fragment(
                        options['check'], 'domain check'
                    ) + ')'
        elif kind == 'exception' and self.dialect.engine_id == 'firebird':
            message = options.get('message')
            source = (
                f'CREATE EXCEPTION {qualified} {self._literal(message)}'
            )
        elif kind == 'event' and self.dialect.engine_id in {
            'mysql', 'mariadb', 'dolt',
        }:
            schedule = self._safe_fragment(
                options.get('schedule'), 'event schedule'
            )
            body = self._safe_definition(draft.get('definition'))
            preserve = (
                ' ON COMPLETION PRESERVE' if options.get('preserve') else ''
            )
            state = ' ENABLE' if options.get('enabled', True) else ' DISABLE'
            source = (
                f'CREATE EVENT {qualified} ON SCHEDULE {schedule}'
                f'{preserve}{state} DO {body}'
            )
        elif kind == 'plugin' and self.dialect.sql_family == 'mysql':
            library = options.get('library')
            if not isinstance(library, str) or not re.fullmatch(
                    r'[A-Za-z0-9][A-Za-z0-9_.+-]{0,254}', library):
                raise RelationalClientError(
                    'plugin library must be a safe unqualified filename'
                )
            source = (
                f'INSTALL PLUGIN {self._quote(name)} '
                f'SONAME {self._literal(library)}'
            )
        elif kind == 'materialization' and (
                self.dialect.engine_id == 'duckdb'):
            query = self._query_body(draft.get('select'))
            source = f'CREATE TABLE {qualified} AS {query}'
        elif kind == 'type' and self.dialect.engine_id == 'duckdb':
            type_kind = str(options.get('type_kind', '')).upper()
            if type_kind == 'ALIAS':
                definition = self._safe_fragment(
                    options.get('base_type'), 'alias base type'
                )
            elif type_kind == 'ENUM':
                values = options.get('enum_values')
                if not isinstance(values, list) or not values:
                    raise RelationalClientError(
                        'enum type requires a non-empty value array'
                    )
                definition = 'ENUM (' + ', '.join(
                    self._literal(str(item)) for item in values
                ) + ')'
            elif type_kind in {'STRUCT', 'UNION'}:
                fields = options.get('fields')
                if not isinstance(fields, list) or not fields:
                    raise RelationalClientError(
                        'structured type requires a non-empty field array'
                    )
                definition = type_kind + ' (' + ', '.join(
                    f'{self._quote(item["name"])} '
                    f'{self._safe_fragment(item["type"], "field type")}'
                    for item in fields
                ) + ')'
            else:
                raise RelationalClientError('DuckDB type kind is invalid')
            source = f'CREATE TYPE {qualified} AS {definition}'
        elif kind in {'macro', 'function'} and (
            self.dialect.engine_id == 'duckdb'
        ):
            parameters = self._macro_parameters(
                options.get('parameters') or []
            )
            expression = self._safe_definition(options.get('expression'))
            table = 'TABLE ' if options.get('table_macro') else ''
            source = (
                f'CREATE {self._keyword(kind)} {qualified} '
                f'({parameters}) AS {table}{expression}'
            )
        elif kind == 'secret' and self.dialect.engine_id == 'duckdb':
            source, preview = self._duckdb_secret(qualified, options)
            return [{
                'source': source, 'preview_source': preview,
                'parameters': (),
            }]
        elif kind in {'virtual-table', 'fts-table'} and (
            self.dialect.engine_id == 'sqlite'
        ):
            module = options.get('module')
            if module not in {'fts5', 'rtree'}:
                raise RelationalClientError(
                    'SQLite virtual table module is not admitted'
                )
            columns = self._identifier_list(options.get('columns'))
            source = (
                f'CREATE VIRTUAL TABLE {qualified} USING {module} '
                f'({columns})'
            )
        elif kind == 'column':
            table = self._qualified(self._option_path(options, 'table'))
            item = {
                'name': draft['name'],
                'type': options.get('data_type'),
                **dict(options),
            }
            source = f'ALTER TABLE {table} ADD {self._column_definition(item)}'
        elif kind == 'constraint':
            table = self._qualified(self._option_path(options, 'table'))
            item = {'name': draft['name'], **dict(options)}
            source = (
                f'ALTER TABLE {table} ADD '
                f'{self._constraint_definition(item)}'
            )
        elif kind in {'trigger', 'procedure', 'function', 'package'}:
            object_name = qualified
            if kind == 'trigger' and (
                    self.dialect.sql_family == 'postgresql'):
                object_name = self._quote(name)
            source = self._programmable_create(
                kind, object_name, draft, options
            )
        else:
            definition = self._safe_definition(draft.get('definition'))
            source = f'CREATE {self._keyword(kind)} {qualified}'
            if definition:
                source += f' {definition}'
        return [{'source': source, 'parameters': ()}]

    def _compile_alter(self, request):
        kind = request['resource_kind']
        target = self._qualified(self._target_path(request['target_resource']))
        draft = request['draft']
        changes = draft.get('changes')
        if not isinstance(changes, Mapping):
            raise RelationalClientError('alter changes must be an object')
        if kind == 'user':
            user_changes = dict(changes)
            administrator = user_changes.pop('administrator', None)
            statements = []
            if user_changes:
                source, preview = self._alter_user(
                    request['target_resource'], user_changes
                )
                statements.append({
                    'source': source,
                    'preview_source': preview,
                    'parameters': (),
                })
            if self.dialect.engine_id == 'cockroachdb' and (
                    administrator is not None):
                verb = 'GRANT' if administrator else 'REVOKE'
                preposition = 'TO' if administrator else 'FROM'
                name = request['target_resource'].get('display_name')
                statements.append({
                    'source': (
                        f'{verb} admin {preposition} {self._quote(name)}'
                    ),
                    'parameters': (),
                })
            elif administrator is not None:
                user_changes['administrator'] = administrator
                source, preview = self._alter_user(
                    request['target_resource'], user_changes
                )
                statements = [{
                    'source': source,
                    'preview_source': preview,
                    'parameters': (),
                }]
            if not statements:
                raise RelationalClientError('user alteration has no changes')
            return statements
        if kind == 'database' and self.dialect.sql_family == 'mysql':
            clauses = []
            if changes.get('character_set'):
                clauses.append(
                    'DEFAULT CHARACTER SET ' + self._quote(
                        changes['character_set']
                    )
                )
            if changes.get('collation'):
                clauses.append(
                    'DEFAULT COLLATE ' + self._quote(
                        changes['collation']
                    )
                )
            if not clauses:
                raise RelationalClientError(
                    'database alteration has no structured changes'
                )
            return [{
                'source': f'ALTER DATABASE {target} {" ".join(clauses)}',
                'parameters': (),
            }]
        if kind == 'role' and self.dialect.engine_id == 'firebird':
            if changes.get('drop_system_privileges'):
                clause = 'DROP SYSTEM PRIVILEGES'
            else:
                clause = 'SET SYSTEM PRIVILEGES TO ' + (
                    self._privilege_names(changes.get('system_privileges'))
                )
            return [{
                'source': f'ALTER ROLE {target} {clause}',
                'parameters': (),
            }]
        if kind == 'index' and self.dialect.engine_id == 'firebird':
            state = 'ACTIVE' if changes.get('active') else 'INACTIVE'
            target = self._quote(
                request['target_resource'].get('display_name')
            )
            return [{
                'source': f'ALTER INDEX {target} {state}',
                'parameters': (),
            }]
        if kind == 'publication' and self.dialect.engine_id == 'firebird':
            include = changes.get('include_tables') or []
            exclude = changes.get('exclude_tables') or []
            if not isinstance(include, list) or not isinstance(exclude, list):
                raise RelationalClientError(
                    'publication table selections must be arrays'
                )
            overlap = set(include).intersection(exclude)
            if overlap:
                raise RelationalClientError(
                    'a publication table cannot be included and excluded'
                )
            statements = [{
                'source': (
                    'ALTER DATABASE ENABLE PUBLICATION'
                    if changes.get('enabled', True)
                    else 'ALTER DATABASE DISABLE PUBLICATION'
                ),
                'parameters': (),
            }]
            if include:
                statements.append({
                    'source': (
                        'ALTER DATABASE INCLUDE TABLE '
                        f'{self._identifier_list(include)} TO PUBLICATION'
                    ),
                    'parameters': (),
                })
            if exclude:
                statements.append({
                    'source': (
                        'ALTER DATABASE EXCLUDE TABLE '
                        f'{self._identifier_list(exclude)} FROM PUBLICATION'
                    ),
                    'parameters': (),
                })
            return statements
        if kind == 'sequence':
            clauses = []
            if 'restart' in changes:
                clauses.append(
                    'RESTART WITH ' + str(self._integer(
                        changes['restart'], 'restart'
                    ))
                )
            if 'increment' in changes:
                clauses.append(
                    'INCREMENT BY ' + str(self._integer(
                        changes['increment'], 'increment'
                    ))
                )
            if not clauses:
                raise RelationalClientError(
                    'sequence alteration has no structured changes'
                )
            return [{
                'source': f'ALTER SEQUENCE {target} {" ".join(clauses)}',
                'parameters': (),
            }]
        if kind == 'domain' and self.dialect.engine_id == 'firebird':
            clauses = []
            if changes.get('data_type'):
                clauses.append(
                    'TYPE ' + self._safe_fragment(
                        changes['data_type'], 'domain type'
                    )
                )
            if changes.get('drop_default'):
                clauses.append('DROP DEFAULT')
            elif changes.get('default') not in {None, ''}:
                clauses.append(
                    'SET DEFAULT ' + self._safe_fragment(
                        changes['default'], 'domain default'
                    )
                )
            if 'not_null' in changes:
                clauses.append(
                    'SET NOT NULL' if changes['not_null']
                    else 'DROP NOT NULL'
                )
            if changes.get('drop_constraint'):
                clauses.append('DROP CONSTRAINT')
            elif changes.get('check') not in {None, ''}:
                clauses.append(
                    'ADD CHECK (' + self._safe_fragment(
                        changes['check'], 'domain check'
                    ) + ')'
                )
            if not clauses:
                raise RelationalClientError(
                    'domain alteration has no structured changes'
                )
            return [{
                'source': f'ALTER DOMAIN {target} {" ".join(clauses)}',
                'parameters': (),
            }]
        if kind == 'exception' and self.dialect.engine_id == 'firebird':
            message = changes.get('message')
            return [{
                'source': (
                    f'ALTER EXCEPTION {target} {self._literal(message)}'
                ),
                'parameters': (),
            }]
        if kind == 'trigger' and self.dialect.engine_id == 'firebird':
            body = self._safe_definition(draft.get('definition'))
            timing = self._safe_fragment(
                changes.get('timing'), 'trigger timing'
            ).upper()
            events = changes.get('events')
            if not isinstance(events, list) or not events:
                raise RelationalClientError('trigger events are required')
            event_sql = ' OR '.join(
                self._safe_fragment(item, 'trigger event').upper()
                for item in events
            )
            active = 'ACTIVE' if changes.get('active', True) else 'INACTIVE'
            position = self._integer(
                changes.get('position', 0), 'position'
            )
            return [{
                'source': (
                    f'ALTER TRIGGER {target} {active} {timing} {event_sql} '
                    f'POSITION {position} AS {body}'
                ),
                'parameters': (),
            }]
        if kind in {'procedure', 'function'} and (
            self.dialect.engine_id == 'firebird'
        ):
            return [{
                'source': self._firebird_routine(
                    'ALTER', kind, target, draft.get('definition'), changes
                ),
                'parameters': (),
            }]
        if kind == 'package' and self.dialect.engine_id == 'firebird':
            header = self._safe_definition(changes.get('header'))
            body = self._safe_definition(draft.get('definition'))
            return [
                {
                    'source': f'ALTER PACKAGE {target} AS {header}',
                    'parameters': (),
                },
                {
                    'source': f'RECREATE PACKAGE BODY {target} AS {body}',
                    'parameters': (),
                },
            ]
        if kind == 'package' and self.dialect.engine_id == 'mariadb':
            specification = self._safe_definition(changes.get('header'))
            body = self._safe_definition(draft.get('definition'))
            if not specification or not body:
                raise RelationalClientError(
                    'MariaDB package requires a specification and body'
                )
            return [
                {
                    'source': (
                        f'CREATE OR REPLACE PACKAGE {target} '
                        f'{specification}'
                    ),
                    'parameters': (),
                },
                {
                    'source': (
                        f'CREATE OR REPLACE PACKAGE BODY {target} {body}'
                    ),
                    'parameters': (),
                },
            ]
        if kind == 'view':
            query = self._query_body(draft.get('definition'))
            command = 'ALTER VIEW'
            if (
                self.dialect.sql_family == 'postgresql' or
                self.dialect.engine_id in {'dolt', 'tidb'}
            ):
                command = 'CREATE OR REPLACE VIEW'
            return [{
                'source': f'{command} {target} AS {query}',
                'parameters': (),
            }]
        if kind == 'event' and self.dialect.engine_id in {
            'mysql', 'mariadb', 'dolt',
        }:
            clauses = []
            if changes.get('schedule'):
                clauses.append(
                    'ON SCHEDULE ' + self._safe_fragment(
                        changes['schedule'], 'event schedule'
                    )
                )
            if 'preserve' in changes:
                clauses.append(
                    'ON COMPLETION ' + (
                        'PRESERVE' if changes['preserve'] else 'NOT PRESERVE'
                    )
                )
            if 'enabled' in changes:
                clauses.append('ENABLE' if changes['enabled'] else 'DISABLE')
            if draft.get('definition'):
                clauses.append(
                    'DO ' + self._safe_definition(draft['definition'])
                )
            if not clauses:
                raise RelationalClientError(
                    'event alteration has no structured changes'
                )
            return [{
                'source': f'ALTER EVENT {target} {" ".join(clauses)}',
                'parameters': (),
            }]
        if kind in {'macro', 'function'} and (
            self.dialect.engine_id == 'duckdb'
        ):
            parameters = self._macro_parameters(
                changes.get('parameters') or []
            )
            expression = self._safe_definition(changes.get('expression'))
            table = 'TABLE ' if changes.get('table_macro') else ''
            return [{
                'source': (
                    f'CREATE OR REPLACE {self._keyword(kind)} {target} '
                    f'({parameters}) AS {table}{expression}'
                ),
                'parameters': (),
            }]
        if kind == 'pragma' and self.dialect.engine_id == 'sqlite':
            value = self._safe_fragment(changes.get('value'), 'PRAGMA value')
            name = request['target_resource'].get('display_name')
            return [{
                'source': f'PRAGMA {self._quote(name)} = {value}',
                'parameters': (),
            }]
        if kind == 'table':
            statements = []
            for item in changes.get('add_columns', []):
                statements.append({
                    'source': (
                        f'ALTER TABLE {target} ADD '
                        f'{self._column_definition(item)}'
                    ),
                    'parameters': (),
                })
            for name in changes.get('drop_columns', []):
                statements.append({
                    'source': (
                        f'ALTER TABLE {target} DROP COLUMN '
                        f'{self._quote(name)}'
                    ),
                    'parameters': (),
                })
            for item in changes.get('rename_columns', []):
                rename = (
                    f'ALTER COLUMN {self._quote(item["from"])} TO '
                    f'{self._quote(item["to"])}'
                    if self.dialect.engine_id == 'firebird' else
                    f'RENAME COLUMN {self._quote(item["from"])} TO '
                    f'{self._quote(item["to"])}'
                )
                statements.append({
                    'source': (
                        f'ALTER TABLE {target} {rename}'
                    ),
                    'parameters': (),
                })
            if not statements:
                raise RelationalClientError(
                    'table alteration has no structured changes'
                )
            return statements
        if kind == 'column':
            path = self._target_path(request['target_resource'])
            table = self._qualified(path[:-1])
            column = self._quote(path[-1])
            definition = self._safe_definition(draft.get('definition'))
            if not definition:
                definition = self._changes_fragment(changes)
            return [{
                'source': (
                    f'ALTER TABLE {table} ALTER COLUMN {column} '
                    f'{definition}'
                ),
                'parameters': (),
            }]
        definition = self._safe_definition(draft.get('definition'))
        if not definition:
            definition = self._changes_fragment(changes)
        return [{
            'source': f'ALTER {self._keyword(kind)} {target} {definition}',
            'parameters': (),
        }]

    def _compile_rename(self, request):
        kind = request['resource_kind']
        if kind == 'user' and self.dialect.sql_family == 'mysql':
            current = request['target_resource'].get('display_name')
            new_name = request['draft']['new_name']
            host = self._account_parts(current)[1]
            return {
                'source': (
                    f'RENAME USER {self._account(current)} TO '
                    f'{self._account(new_name, host)}'
                ),
                'parameters': (),
            }
        if kind == 'column':
            path = self._target_path(request['target_resource'])
            table = self._qualified(path[:-1])
            action = (
                'ALTER COLUMN' if self.dialect.engine_id == 'firebird'
                else 'RENAME COLUMN'
            )
            return {
                'source': (
                    f'ALTER TABLE {table} {action} '
                    f'{self._quote(path[-1])} TO '
                    f'{self._quote(request["draft"]["new_name"])}'
                ),
                'parameters': (),
            }
        if kind == 'domain' and self.dialect.engine_id == 'firebird':
            target = self._qualified(
                self._target_path(request['target_resource'])
            )
            new_name = self._quote(request['draft']['new_name'])
            return {
                'source': f'ALTER DOMAIN {target} TO {new_name}',
                'parameters': (),
            }
        if kind == 'sequence' and self.dialect.engine_id == 'mariadb':
            target = self._qualified(
                self._target_path(request['target_resource'])
            )
            path = self._target_path(request['target_resource'])
            new_path = (*path[:-1], request['draft']['new_name'])
            return {
                'source': (
                    f'RENAME TABLE {target} TO '
                    f'{self._qualified(new_path)}'
                ),
                'parameters': (),
            }
        if kind in {'virtual-table', 'fts-table'} and (
            self.dialect.engine_id == 'sqlite'
        ):
            target = self._qualified(
                self._target_path(request['target_resource'])
            )
            new_name = self._quote(request['draft']['new_name'])
            return {
                'source': f'ALTER TABLE {target} RENAME TO {new_name}',
                'parameters': (),
            }
        target = self._qualified(self._target_path(request['target_resource']))
        new_name = self._quote(request['draft']['new_name'])
        return {
            'source': (
                f'ALTER {self._keyword(kind)} {target} RENAME TO {new_name}'
            ),
            'parameters': (),
        }

    def _compile_drop(self, request):
        kind = request['resource_kind']
        if kind == 'plugin' and self.dialect.sql_family == 'mysql':
            name = request['target_resource'].get('display_name')
            return {
                'source': f'UNINSTALL PLUGIN {self._quote(name)}',
                'parameters': (),
            }
        if kind == 'role':
            name = request['target_resource'].get('display_name')
            role = (
                self._quote(name)
                if self.dialect.engine_id == 'mariadb'
                else self._account(name)
            )
            return {
                'source': f'DROP ROLE {role}',
                'parameters': (),
            }
        if kind == 'user':
            name = request['target_resource'].get('display_name')
            return {
                'source': f'DROP USER {self._account(name)}',
                'parameters': (),
            }
        if kind in {'column', 'constraint'}:
            path = self._target_path(request['target_resource'])
            table = self._qualified(path[:-1])
            keyword = self._keyword(kind)
            if kind == 'column' and self.dialect.engine_id == 'firebird':
                keyword = ''
            if kind == 'constraint' and (
                    self.dialect.engine_id == 'cockroachdb'):
                extensions = request['target_resource'].get(
                    'extensions', {}
                )
                native = extensions.get('cockroachdb', {}).get('native', {})
                constraint_type = str(
                    native.get('constraint_type', '')
                ).upper()
                if constraint_type in {'PRIMARY KEY', 'UNIQUE'}:
                    index = self._qualified((*path[:-2], path[-1]))
                    return {
                        'source': f'DROP INDEX {index} CASCADE',
                        'parameters': (),
                    }
            return {
                'source': (
                    f'ALTER TABLE {table} DROP {keyword} '
                    f'{self._quote(path[-1])}'
                ).replace('DROP  ', 'DROP '),
                'parameters': (),
            }
        if kind == 'index':
            path = self._target_path(request['target_resource'])
            if self.dialect.sql_family == 'mysql':
                source = (
                    f'DROP INDEX {self._quote(path[-1])} ON '
                    f'{self._qualified(path[:-1])}'
                )
            elif (
                self.dialect.sql_family == 'postgresql' or
                self.dialect.engine_id in {'duckdb', 'sqlite'}
            ) and len(path) >= 3:
                source = 'DROP INDEX ' + self._qualified(
                    (*path[:-2], path[-1])
                )
            elif self.dialect.engine_id == 'firebird':
                source = f'DROP INDEX {self._quote(path[-1])}'
            else:
                source = f'DROP INDEX {self._qualified(path)}'
            return {'source': source, 'parameters': ()}
        if kind == 'trigger' and self.dialect.sql_family == 'mysql':
            path = self._target_path(request['target_resource'])
            target = self._qualified((*path[:-2], path[-1]))
            return {'source': f'DROP TRIGGER {target}', 'parameters': ()}
        if kind == 'trigger' and self.dialect.sql_family == 'postgresql':
            path = self._target_path(request['target_resource'])
            trigger = self._quote(path[-1])
            table = self._qualified(path[:-1])
            return {
                'source': f'DROP TRIGGER {trigger} ON {table}',
                'parameters': (),
            }
        if kind in {'virtual-table', 'fts-table'} and (
            self.dialect.engine_id == 'sqlite'
        ):
            target = self._qualified(
                self._target_path(request['target_resource'])
            )
            return {'source': f'DROP TABLE {target}', 'parameters': ()}
        if kind == 'secret' and self.dialect.engine_id == 'duckdb':
            target_resource = request['target_resource']
            extensions = target_resource.get('extensions', {})
            native = extensions.get('duckdb', {}).get('native', {})
            persistent = (
                'PERSISTENT ' if native.get('persistent') else ''
            )
            target = self._quote(target_resource.get('display_name'))
            return {
                'source': f'DROP {persistent}SECRET {target}',
                'parameters': (),
            }
        target = self._qualified(self._target_path(request['target_resource']))
        cascade = bool(request['draft'].get('cascade'))
        suffix = (
            ' CASCADE'
            if cascade and self.dialect.supports_cascade else ''
        )
        return {
            'source': f'DROP {self._keyword(kind)} {target}{suffix}',
            'parameters': (),
        }

    def _compile_insert(self, request):
        target = self._qualified(self._target_path(request['target_resource']))
        values = request['draft'].get('values')
        if not isinstance(values, Mapping) or not values:
            raise RelationalClientError('insert values must be an object')
        columns = list(values)
        source = (
            f'INSERT INTO {target} '
            f'({", ".join(self._quote(item) for item in columns)}) VALUES '
            f'({", ".join(self.dialect.parameter for _item in columns)})'
        )
        return {
            'source': source,
            'parameters': tuple(values[item] for item in columns),
        }

    def _compile_identity_dml(self, request):
        draft = request['draft']
        selector = draft.get('selector')
        if not isinstance(selector, Mapping):
            raise RelationalClientError(
                'row selector must contain a provider identity token'
            )
        token = selector.get('identity_token')
        if not isinstance(token, str) or not token:
            raise RelationalClientError(
                'provider-issued row identity token is required'
            )
        with self._identity_lock:
            identity = self._row_identities.pop(token, None)
        if identity is None:
            raise RelationalClientError(
                'row identity token is stale or invalid'
            )
        if time.monotonic() - identity.issued_at > 600:
            raise RelationalClientError('row identity token has expired')
        route = request.get('_provider_route')
        if self._route_fingerprint(route) != identity.route_fingerprint:
            raise RelationalClientError(
                'row identity belongs to another route'
            )
        target_path = self._target_path(request['target_resource'])
        if target_path != identity.target_path:
            raise RelationalClientError(
                'row identity belongs to another table'
            )
        where, parameters = self._identity_predicate(identity)
        target = self._qualified(target_path)
        if request['operation_id'] == 'delete':
            source = f'DELETE FROM {target} WHERE {where}'
            return {
                'source': source, 'parameters': parameters,
                'expected_rowcount': 1,
            }
        changes = draft.get('changes')
        if not isinstance(changes, Mapping) or not changes:
            raise RelationalClientError('row update changes must be an object')
        assignments = ', '.join(
            f'{self._quote(name)} = {self.dialect.parameter}'
            for name in changes
        )
        source = f'UPDATE {target} SET {assignments} WHERE {where}'
        return {
            'source': source,
            'parameters': tuple(changes.values()) + parameters,
            'expected_rowcount': 1,
        }

    def _compile_privilege(self, request):
        operation = request['operation_id'].upper()
        draft = request['draft']
        principal = self._quote(draft['principal'])
        privileges = draft.get('privileges')
        if not isinstance(privileges, list) or not privileges:
            raise RelationalClientError('privileges must be a non-empty array')
        privilege_list = ', '.join(
            self._safe_fragment(item, 'privilege').upper()
            for item in privileges
        )
        target_kind = self._safe_fragment(
            draft.get('object_type'), 'privilege object type'
        ).upper()
        target_name = self._qualified(
            self._path_value(draft.get('object_name'))
        )
        preposition = 'TO' if operation == 'GRANT' else 'FROM'
        if self.dialect.sql_family == 'mysql':
            principal = self._account(draft['principal'])
            object_prefix = ''
            if target_kind == 'DATABASE':
                target_name = self._quote(draft.get('object_name')) + '.*'
        else:
            object_prefix = f'{target_kind} '
        suffix = (
            ' WITH GRANT OPTION'
            if operation == 'GRANT' and draft.get('grant_option') else ''
        )
        return {
            'source': (
                f'{operation} {privilege_list} ON {object_prefix}'
                f'{target_name} {preposition} {principal}{suffix}'
            ),
            'parameters': (),
        }

    def _compile_execute(self, request):
        kind = request['resource_kind']
        if kind == 'extension' and self.dialect.engine_id == 'duckdb':
            action = request['draft'].get('action')
            if action not in {'INSTALL', 'LOAD'}:
                raise RelationalClientError('extension action is invalid')
            target = request['target_resource'].get('display_name')
            return {
                'source': f'{action} {self._quote(target)}',
                'parameters': (),
            }
        raise RelationalClientError(
            'provider operational action is unavailable'
        )

    def _primary_key(self, connection, path):
        engine = self.dialect.engine_id
        family = self.dialect.sql_family
        cursor = connection.cursor()
        try:
            if engine == 'sqlite':
                schema, table = self._schema_table(path, 'main')
                cursor.execute(
                    f'PRAGMA {self._quote(schema)}.table_info('
                    f'{self._quote(table)})'
                )
                return [
                    str(row[1]) for row in sorted(
                        cursor.fetchall(), key=lambda item: int(item[5] or 0)
                    ) if int(row[5] or 0) > 0
                ]
            if family == 'mysql':
                schema, table = self._schema_table(path)
                cursor.execute(
                    'SELECT COLUMN_NAME FROM information_schema.'
                    'KEY_COLUMN_USAGE WHERE TABLE_SCHEMA = '
                    f'{self.dialect.parameter} AND TABLE_NAME = '
                    f'{self.dialect.parameter} AND CONSTRAINT_NAME = '
                    "'PRIMARY' ORDER BY ORDINAL_POSITION",
                    (schema, table),
                )
                return [str(row[0]) for row in cursor.fetchall()]
            if engine == 'firebird':
                table = path[-1]
                cursor.execute(
                    'SELECT TRIM(S.RDB$FIELD_NAME) FROM '
                    'RDB$RELATION_CONSTRAINTS C JOIN RDB$INDEX_SEGMENTS S '
                    'ON S.RDB$INDEX_NAME = C.RDB$INDEX_NAME WHERE '
                    "C.RDB$CONSTRAINT_TYPE = 'PRIMARY KEY' AND "
                    f'C.RDB$RELATION_NAME = {self.dialect.parameter} '
                    'ORDER BY S.RDB$FIELD_POSITION',
                    (table,),
                )
                return [str(row[0]).strip() for row in cursor.fetchall()]
            if engine == 'duckdb':
                schema, table = self._schema_table(path, 'main')
                cursor.execute(
                    'SELECT kcu.column_name FROM information_schema.'
                    'table_constraints tc JOIN information_schema.'
                    'key_column_usage kcu ON tc.constraint_catalog = '
                    'kcu.constraint_catalog AND tc.constraint_schema = '
                    'kcu.constraint_schema AND tc.constraint_name = '
                    'kcu.constraint_name WHERE tc.constraint_type = '
                    "'PRIMARY KEY' AND tc.table_schema = ? AND "
                    'tc.table_name = ? ORDER BY kcu.ordinal_position',
                    (schema, table),
                )
                return [str(row[0]) for row in cursor.fetchall()]
            if family == 'postgresql':
                schema, table = self._schema_table(path, 'public')
                cursor.execute(
                    'SELECT kcu.column_name FROM information_schema.'
                    'table_constraints tc JOIN information_schema.'
                    'key_column_usage kcu ON tc.constraint_catalog = '
                    'kcu.constraint_catalog AND tc.constraint_schema = '
                    'kcu.constraint_schema AND tc.constraint_name = '
                    'kcu.constraint_name WHERE tc.constraint_type = '
                    "'PRIMARY KEY' AND tc.table_schema = %s AND "
                    'tc.table_name = %s ORDER BY kcu.ordinal_position',
                    (schema, table),
                )
                return [str(row[0]) for row in cursor.fetchall()]
            return []
        finally:
            cursor.close()

    def _identity_predicate(self, identity):
        clauses = []
        parameters = []
        for name, value in zip(identity.key_columns, identity.key_values):
            clauses.append(
                f'{self._quote(name)} = {self.dialect.parameter}'
            )
            parameters.append(value)
        for name, value in identity.original.items():
            if name in identity.key_columns:
                continue
            quoted = self._quote(name)
            if value is None:
                clauses.append(f'{quoted} IS NULL')
            else:
                clauses.append(
                    f'{quoted} = {self.dialect.parameter}'
                )
                parameters.append(value)
        return ' AND '.join(clauses), tuple(parameters)

    def _programmable_create(self, kind, name, draft, options):
        body = self._safe_definition(draft.get('definition'))
        if not body:
            raise RelationalClientError(
                f'{kind} creation requires an object body'
            )
        if kind == 'trigger':
            table = self._qualified(self._option_path(options, 'table'))
            timing = self._safe_fragment(
                options.get('timing', 'BEFORE'), 'trigger timing'
            ).upper()
            events = options.get('events', ['INSERT'])
            event_sql = ' OR '.join(
                self._safe_fragment(item, 'trigger event').upper()
                for item in events
            )
            if self.dialect.engine_id == 'firebird':
                active = (
                    'ACTIVE' if options.get('active', True) else 'INACTIVE'
                )
                position = self._integer(
                    options.get('position', 0), 'position'
                )
                return (
                    f'CREATE TRIGGER {name} FOR {table} {active} {timing} '
                    f'{event_sql} POSITION {position} AS {body}'
                )
            if self.dialect.sql_family == 'mysql':
                return (
                    f'CREATE TRIGGER {name} {timing} {event_sql} ON {table} '
                    f'FOR EACH ROW {body}'
                )
            return (
                f'CREATE TRIGGER {name} {timing} {event_sql} ON {table} '
                f'{body}'
            )
        parameters = options.get('parameters', '')
        if isinstance(parameters, list):
            parameters = ', '.join(
                f'{self._quote(item["name"])} '
                f'{self._safe_fragment(item["type"], "parameter type")}'
                for item in parameters
            )
        parameters = str(parameters)
        returns = options.get('returns')
        if self.dialect.engine_id == 'firebird' and kind in {
            'procedure', 'function',
        }:
            return self._firebird_routine(
                'CREATE', kind, name, body, options
            )
        return_sql = (
            f' RETURNS {self._safe_fragment(returns, "return type")}'
            if returns else ''
        )
        return (
            f'CREATE {self._keyword(kind)} {name} ({parameters})'
            f'{return_sql} {body}'
        )

    def _firebird_routine(self, action, kind, name, body, options):
        body = self._safe_definition(body)
        parameters = self._typed_parameters(
            options.get('parameters') or [], 'parameter'
        )
        input_sql = f' ({parameters})' if parameters else ''
        if kind == 'procedure':
            outputs = self._typed_parameters(
                options.get('return_parameters') or [], 'output'
            )
            return_sql = f' RETURNS ({outputs})' if outputs else ''
        else:
            returns = options.get('returns')
            if not returns:
                raise RelationalClientError(
                    'Firebird function return type is required'
                )
            return_sql = (
                ' RETURNS ' + self._safe_fragment(
                    returns, 'function return type'
                )
            )
        return (
            f'{action} {kind.upper()} {name}{input_sql}{return_sql} AS {body}'
        )

    def _typed_parameters(self, values, label):
        if not isinstance(values, list):
            raise RelationalClientError(f'{label}s must be an array')
        return ', '.join(
            f'{self._quote(item["name"])} '
            f'{self._safe_fragment(item["type"], f"{label} type")}'
            for item in values
        )

    def _create_user(self, name, options):
        password = options.get('password')
        if not isinstance(password, str) or not password:
            raise RelationalClientError('user password is required')
        engine = self.dialect.engine_id
        family = self.dialect.sql_family
        if family == 'mysql':
            account = self._account(name, options.get('host', '%'))
            source = (
                f'CREATE USER {account} IDENTIFIED BY '
                f'{self._literal(password)}'
            )
            preview = f'CREATE USER {account} IDENTIFIED BY <redacted>'
            if options.get('active') is False:
                source += ' ACCOUNT LOCK'
                preview += ' ACCOUNT LOCK'
            return source, preview
        if family == 'postgresql':
            user = self._quote(name)
            source = (
                f'CREATE USER {user} PASSWORD {self._literal(password)}'
            )
            preview = f'CREATE USER {user} PASSWORD <redacted>'
            if options.get('administrator') and engine != 'cockroachdb':
                source += ' SUPERUSER'
                preview += ' SUPERUSER'
            if options.get('active') is False:
                source += ' NOLOGIN'
                preview += ' NOLOGIN'
            return source, preview
        if engine == 'firebird':
            user = self._quote(name)
            source = (
                f'CREATE USER {user} PASSWORD {self._literal(password)}'
            )
            preview = f'CREATE USER {user} PASSWORD <redacted>'
            plugin = options.get('plugin')
            if plugin:
                plugin_sql = self._quote(plugin)
                source += f' USING PLUGIN {plugin_sql}'
                preview += f' USING PLUGIN {plugin_sql}'
            if options.get('administrator'):
                source += ' GRANT ADMIN ROLE'
                preview += ' GRANT ADMIN ROLE'
            active = 'ACTIVE' if options.get('active', True) else 'INACTIVE'
            return f'{source} {active}', f'{preview} {active}'
        raise RelationalClientError('user creation is unavailable')

    def _alter_user(self, target, changes):
        name = target.get('display_name')
        engine = self.dialect.engine_id
        family = self.dialect.sql_family
        user = self._account(name)
        if family == 'mysql':
            clauses = []
            previews = []
            password = changes.get('password')
            if password:
                clauses.append(f'IDENTIFIED BY {self._literal(password)}')
                previews.append('IDENTIFIED BY <redacted>')
            if 'active' in changes:
                state = 'UNLOCK' if changes['active'] else 'LOCK'
                clauses.append(f'ACCOUNT {state}')
                previews.append(f'ACCOUNT {state}')
            if not clauses:
                raise RelationalClientError('user alteration has no changes')
            return (
                f'ALTER USER {user} {" ".join(clauses)}',
                f'ALTER USER {user} {" ".join(previews)}',
            )
        if family == 'postgresql':
            clauses = []
            previews = []
            password = changes.get('password')
            if password:
                clauses.append(f'PASSWORD {self._literal(password)}')
                previews.append('PASSWORD <redacted>')
            if 'administrator' in changes:
                clause = (
                    'SUPERUSER' if changes['administrator'] else 'NOSUPERUSER'
                )
                clauses.append(clause)
                previews.append(clause)
            if 'active' in changes:
                clause = 'LOGIN' if changes['active'] else 'NOLOGIN'
                clauses.append(clause)
                previews.append(clause)
            if not clauses:
                raise RelationalClientError('user alteration has no changes')
            return (
                f'ALTER USER {user} {" ".join(clauses)}',
                f'ALTER USER {user} {" ".join(previews)}',
            )
        if engine == 'firebird':
            clauses = []
            previews = []
            password = changes.get('password')
            if password:
                clauses.append(f'PASSWORD {self._literal(password)}')
                previews.append('PASSWORD <redacted>')
            plugin = changes.get('plugin')
            if plugin:
                clause = f'USING PLUGIN {self._quote(plugin)}'
                clauses.append(clause)
                previews.append(clause)
            if 'administrator' in changes:
                clause = (
                    'GRANT ADMIN ROLE' if changes['administrator']
                    else 'REVOKE ADMIN ROLE'
                )
                clauses.append(clause)
                previews.append(clause)
            if 'active' in changes:
                clause = 'ACTIVE' if changes['active'] else 'INACTIVE'
                clauses.append(clause)
                previews.append(clause)
            if not clauses:
                raise RelationalClientError('user alteration has no changes')
            return (
                f'ALTER USER {user} {" ".join(clauses)}',
                f'ALTER USER {user} {" ".join(previews)}',
            )
        raise RelationalClientError('user alteration is unavailable')

    def _account(self, value, default_host='%'):
        if self.dialect.sql_family != 'mysql':
            return self._quote(value)
        user, host = self._account_parts(value, default_host)
        return f'{self._literal(user)}@{self._literal(host)}'

    def _privilege_names(self, values):
        if not isinstance(values, list) or not values:
            raise RelationalClientError(
                'system privileges must be a non-empty array'
            )
        return ', '.join(
            self._safe_fragment(value, 'system privilege').upper()
            for value in values
        )

    @staticmethod
    def _account_parts(value, default_host='%'):
        if not isinstance(value, str) or not value:
            raise RelationalClientError('account name must not be empty')
        if '@' in value:
            user, host = value.rsplit('@', 1)
        else:
            user, host = value, default_host
        user = user.strip("'\"")
        host = host.strip("'\"")
        if not user or not host:
            raise RelationalClientError('account name is invalid')
        return user, host

    @staticmethod
    def _literal(value):
        if not isinstance(value, str) or '\x00' in value:
            raise RelationalClientError('literal value is invalid')
        return "'" + value.replace("'", "''") + "'"

    def _macro_parameters(self, values):
        if not isinstance(values, list):
            raise RelationalClientError('macro parameters must be an array')
        result = []
        for item in values:
            if isinstance(item, str):
                result.append(self._quote(item))
            elif isinstance(item, Mapping):
                name = self._quote(item.get('name'))
                if 'default' in item:
                    default = self._safe_definition(str(item['default']))
                    result.append(f'{name} := {default}')
                else:
                    result.append(name)
            else:
                raise RelationalClientError(
                    'macro parameter entry is invalid'
                )
        return ', '.join(result)

    def _duckdb_secret(self, name, options):
        secret_type = self._safe_fragment(
            options.get('secret_type'), 'secret type'
        )
        persistent = 'PERSISTENT ' if options.get('persistent') else ''
        storage = options.get('storage')
        storage_sql = (
            f' IN {self._quote(storage)}' if storage else ''
        )
        properties = [f'TYPE {secret_type}']
        preview_properties = [f'TYPE {secret_type}']
        scope = options.get('scope')
        if scope:
            properties.append(f'SCOPE {self._literal(scope)}')
            preview_properties.append('SCOPE <redacted>')
        reserved = {
            'secret_type', 'scope', 'storage', 'persistent', 'parent',
        }
        for key, value in options.items():
            if key in reserved:
                continue
            key_sql = self._safe_fragment(key, 'secret property').upper()
            properties.append(f'{key_sql} {self._literal(str(value))}')
            preview_properties.append(f'{key_sql} <redacted>')
        source = (
            f'CREATE {persistent}SECRET {name}{storage_sql} '
            f'({", ".join(properties)})'
        )
        preview = (
            f'CREATE {persistent}SECRET {name}{storage_sql} '
            f'({", ".join(preview_properties)})'
        )
        return source, preview

    def _column_definition(self, item):
        if not isinstance(item, Mapping):
            raise RelationalClientError('column definition must be an object')
        name = self._quote(item.get('name'))
        data_type = self._safe_fragment(item.get('type'), 'column type')
        parts = [name, data_type]
        if not item.get('nullable', True):
            parts.append('NOT NULL')
        if 'default' in item and item['default'] not in {None, ''}:
            parts.extend((
                'DEFAULT', self._safe_fragment(item['default'], 'default'),
            ))
        if item.get('unique'):
            parts.append('UNIQUE')
        if item.get('primary_key'):
            parts.append('PRIMARY KEY')
        return ' '.join(parts)

    def _constraint_definition(self, item):
        if not isinstance(item, Mapping):
            raise RelationalClientError(
                'constraint definition must be an object'
            )
        name = item.get('name')
        prefix = f'CONSTRAINT {self._quote(name)} ' if name else ''
        kind = str(item.get('kind', '')).upper()
        if kind in {'PRIMARY KEY', 'UNIQUE'}:
            return prefix + kind + ' (' + self._identifier_list(
                item.get('columns')
            ) + ')'
        if kind == 'FOREIGN KEY':
            columns = self._identifier_list(item.get('columns'))
            references = self._qualified(
                self._option_path(item, 'references_table')
            )
            reference_columns = self._identifier_list(
                item.get('references_columns')
            )
            return (
                f'{prefix}FOREIGN KEY ({columns}) REFERENCES '
                f'{references} ({reference_columns})'
            )
        if kind == 'CHECK':
            expression = self._safe_fragment(
                item.get('expression'), 'check expression'
            )
            return f'{prefix}CHECK ({expression})'
        raise RelationalClientError('constraint kind is unsupported')

    def _target_path(self, target):
        if not isinstance(target, Mapping):
            raise RelationalClientError('target resource is required')
        raw = target.get('display_path') or [target.get('display_name')]
        if not isinstance(raw, Sequence) or isinstance(raw, str) or not raw:
            raise RelationalClientError('target resource path is invalid')
        path = tuple(
            self._identifier(item) for item in raw if item != 'current'
        )
        if not path:
            raise RelationalClientError('target resource path is empty')
        return path

    def _new_object_name(self, name, options):
        parent = options.get('parent')
        if parent is None:
            parent = options.get('schema') or options.get('database')
        if parent:
            path = self._path_value(parent) + (name,)
        else:
            path = (name,)
        return self._qualified(path)

    def _option_path(self, options, name):
        value = options.get(name)
        if value is None:
            raise RelationalClientError(f'{name} is required')
        return self._path_value(value)

    def _path_value(self, value):
        if isinstance(value, str):
            raw = value.split('.')
        elif isinstance(value, Sequence):
            raw = value
        else:
            raise RelationalClientError('object path is invalid')
        path = tuple(self._identifier(item) for item in raw)
        if not path:
            raise RelationalClientError('object path is empty')
        return path

    def _qualified(self, path):
        return '.'.join(self._quote(item) for item in path)

    def _quote(self, value):
        value = self._identifier(value)
        escaped = value.replace(
            self.dialect.quote_close, self.dialect.quote_close * 2
        )
        return f'{self.dialect.quote_open}{escaped}{self.dialect.quote_close}'

    @staticmethod
    def _identifier(value):
        if not isinstance(value, str) or not value or '\x00' in value:
            raise RelationalClientError('identifier must not be empty')
        if len(value) > 1024:
            raise RelationalClientError('identifier exceeds provider limit')
        return value

    def _identifier_list(self, values):
        if not isinstance(values, list) or not values:
            raise RelationalClientError('identifier list must not be empty')
        return ', '.join(self._quote(item) for item in values)

    @staticmethod
    def _safe_fragment(value, label):
        if not isinstance(value, str) or not value.strip():
            raise RelationalClientError(f'{label} must not be empty')
        value = value.strip()
        if (
            not _FRAGMENT.fullmatch(value) or ';' in value or
            '--' in value or '/*' in value
        ):
            raise RelationalClientError(f'{label} contains unsafe syntax')
        return value

    @staticmethod
    def _safe_definition(value):
        if value is None:
            return ''
        if not isinstance(value, str) or '\x00' in value:
            raise RelationalClientError('object body must be text')
        value = value.strip()
        if _DDL_PREFIX.match(value):
            raise RelationalClientError(
                'complete native commands are not accepted as object bodies'
            )
        return value

    def _query_body(self, value):
        value = self._safe_definition(value)
        if not re.match(r'^(?:select|with)\b', value, re.I):
            raise RelationalClientError(
                'view query must begin with SELECT or WITH'
            )
        if ';' in value:
            raise RelationalClientError('view query must be one statement')
        return value

    def _changes_fragment(self, changes):
        if len(changes) != 1:
            raise RelationalClientError(
                'alteration requires one admitted structured change'
            )
        key, value = next(iter(changes.items()))
        key_sql = self._safe_fragment(
            str(key).replace('_', ' '), 'change name'
        ).upper()
        value_sql = self._safe_fragment(str(value), 'change value')
        return f'{key_sql} {value_sql}'

    @staticmethod
    def _integer(value, label):
        if isinstance(value, bool) or not isinstance(value, int):
            raise RelationalClientError(f'{label} must be an integer')
        return value

    @staticmethod
    def _schema_table(path, default_schema=None):
        if len(path) >= 2:
            return path[-2], path[-1]
        if default_schema is not None:
            return default_schema, path[-1]
        raise RelationalClientError('table path requires a schema/database')

    def _keyword(self, kind):
        aliases = {
            'attached-database': 'DATABASE',
            'character-set': 'CHARACTER SET',
            'external-function': 'EXTERNAL FUNCTION',
            'fts-table': 'TABLE',
            'materialized-view': 'MATERIALIZED VIEW',
            'replication-channel': 'REPLICATION CHANNEL',
            'resource-group': 'RESOURCE GROUP',
            'row-policy': 'POLICY',
            'server-link': 'SERVER',
            'virtual-table': 'VIRTUAL TABLE',
        }
        if kind == 'database':
            return self.dialect.database_keyword
        return aliases.get(kind, kind.replace('-', ' ').upper())

    @staticmethod
    def _route_fingerprint(route):
        if not isinstance(route, Mapping):
            raise RelationalClientError('trusted endpoint route is required')
        return tuple(sorted(
            (str(key), repr(value)) for key, value in route.items()
            if key not in {'credential_reference_id', 'principal_reference'}
        ))
