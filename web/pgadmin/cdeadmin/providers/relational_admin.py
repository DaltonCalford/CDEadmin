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
import json
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
from .firebird_expressions import index_expression
from .firebird import mappings as firebird_mappings
from .firebird import columns as firebird_columns
from .firebird.backup_guid import normalize_backup_guid
from .firebird.backup_level import MAX_BACKUP_LEVEL, normalize_backup_level
from .firebird.restore_policy import physical_restore_policy
from .firebird.physical_io import normalize_physical_io
from .firebird import privileges as firebird_privileges
from .firebird.error_diagnostics import status_codes as firebird_status_codes
from .firebird import tables as firebird_tables
from .firebird import identity as firebird_identity
from .firebird.task_savepoint import NativeTaskSavepoint


_FRAGMENT = re.compile(r'^[\w\s(),.+*/%<>=\'"-]+$', re.UNICODE)
FIREBIRD_SYSTEM_PRIVILEGES = (
    'USER_MANAGEMENT', 'READ_RAW_PAGES', 'CREATE_USER_TYPES',
    'USE_NBACKUP_UTILITY', 'CHANGE_SHUTDOWN_MODE', 'TRACE_ANY_ATTACHMENT',
    'MONITOR_ANY_ATTACHMENT', 'ACCESS_SHUTDOWN_DATABASE', 'CREATE_DATABASE',
    'DROP_DATABASE', 'USE_GBAK_UTILITY', 'USE_GSTAT_UTILITY',
    'USE_GFIX_UTILITY',
    'IGNORE_DB_TRIGGERS', 'CHANGE_HEADER_SETTINGS',
    'SELECT_ANY_OBJECT_IN_DATABASE', 'ACCESS_ANY_OBJECT_IN_DATABASE',
    'MODIFY_ANY_OBJECT_IN_DATABASE', 'CHANGE_MAPPING_RULES',
    'USE_GRANTED_BY_CLAUSE', 'GRANT_REVOKE_ON_ANY_OBJECT',
    'GRANT_REVOKE_ANY_DDL_RIGHT', 'CREATE_PRIVILEGED_ROLES',
    'GET_DBCRYPT_INFO', 'MODIFY_EXT_CONN_POOL', 'REPLICATE_INTO_DATABASE',
    'PROFILE_ANY_ATTACHMENT',
)
_DDL_PREFIX = re.compile(
    r'^\s*(?:create|alter|drop|grant|revoke|attach|detach)\b', re.I
)
_FIREBIRD_SERVICE_OPERATIONS = frozenset({
    'backup_logical', 'restore_logical', 'backup_physical',
    'restore_physical', 'validate_database', 'repair_database',
    'sweep_database', 'database_statistics', 'shutdown_database',
    'bring_online', 'set_page_cache_size', 'set_sweep_interval',
    'set_space_reservation', 'set_write_mode', 'set_access_mode',
    'set_sql_dialect', 'activate_shadow', 'remove_linger',
    'fixup_database', 'set_replica_mode', 'upgrade_database',
})
_SQLITE_DATABASE_OPERATIONS = frozenset({
    'backup', 'restore', 'integrity_check', 'quick_check',
    'foreign_key_check', 'vacuum', 'incremental_vacuum', 'optimize',
    'analyze', 'reindex', 'wal_checkpoint',
})
_DUCKDB_DATABASE_OPERATIONS = frozenset({
    'checkpoint', 'force_checkpoint', 'vacuum', 'analyze',
    'export_database', 'import_database',
})
_MYSQL_DATABASE_OPERATIONS = frozenset({
    'analyze_tables', 'check_tables', 'optimize_tables',
    'repair_tables', 'checksum_tables',
})
_MARIADB_DATABASE_OPERATIONS = frozenset({
    'analyze_tables', 'check_objects', 'optimize_tables',
    'repair_objects', 'checksum_tables',
})
_MARIADB_TOOL_DATABASE_OPERATIONS = frozenset({
    'backup_logical', 'restore_logical',
})
_MARIADB_TOOL_SERVER_OPERATIONS = frozenset({
    'check_upgrade_required',
})
_MYSQL_SHELL_DATABASE_OPERATIONS = frozenset({
    'backup_logical', 'restore_logical',
})


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
    database_forms: Mapping[str, Mapping[str, object]] = field(
        default_factory=dict
    )

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


@dataclass(frozen=True)
class _RowContinuation:
    route_fingerprint: tuple[tuple[str, str], ...]
    target_path: tuple[str, ...]
    offset: int
    limit: int
    issued_at: float


class RelationalAdministration:
    """Compile, execute, and page rows for an admitted SQL dialect."""

    def __init__(self, dialect: RelationalAdminDialect):
        self.dialect = dialect
        self._row_identities: dict[str, _RowIdentity] = {}
        self._row_continuations: dict[str, _RowContinuation] = {}
        self._identity_lock = threading.RLock()

    def supports(self, resource_kind, operation_id):
        return operation_id in self.dialect.supported.get(
            resource_kind, frozenset()
        )

    def requires_dialect(self, resource_kind, operation_id):
        """Return whether this task relies on provider-generated SQL.

        Firebird service-manager operations and driver-level database
        creation are explicit native API calls. All other relational visual
        tasks execute generated dialect text and therefore remain fail-closed
        until the exact-version dialect contract passes.
        """
        if operation_id == 'inspect':
            return False
        if self.dialect.engine_id == 'firebird' and (
                resource_kind == 'database' and operation_id == 'drop'):
            return False
        if self.dialect.embedded_database and (
                resource_kind == 'database' and operation_id == 'drop'):
            return False
        if self.dialect.engine_id == 'sqlite' and (
                resource_kind == 'database' and operation_id in {
                    'backup', 'restore',
                }):
            # These tasks call sqlite3_backup through the provider DB-API
            # adapter and do not generate SQL text.
            return False
        if self.dialect.engine_id == 'firebird' and (
                resource_kind == 'database' and operation_id in
                _FIREBIRD_SERVICE_OPERATIONS):
            return False
        if self.dialect.engine_id == 'mysql' and (
                resource_kind == 'database' and operation_id in
                _MYSQL_SHELL_DATABASE_OPERATIONS):
            return False
        if self.dialect.engine_id == 'mariadb' and (
                resource_kind == 'database' and operation_id in
                _MARIADB_TOOL_DATABASE_OPERATIONS):
            return False
        if self.dialect.engine_id == 'mariadb' and (
                resource_kind == 'server' and operation_id in
                _MARIADB_TOOL_SERVER_OPERATIONS):
            return False
        if resource_kind == 'database' and operation_id == 'create' and (
                self.dialect.database_create_mode != 'sql'):
            return False
        return True

    def dialect_task_ids(self):
        """Return every executable visual task that depends on SQL text."""
        return tuple(sorted(
            f'visual_admin.{resource_kind}.{operation_id}'
            for resource_kind, operations in self.dialect.supported.items()
            for operation_id in operations
            if self.requires_dialect(resource_kind, operation_id)
        ))

    def catalog(self, catalog):
        """Replace generic executable forms with structured dialect forms."""
        value = copy.deepcopy(dict(catalog))
        for resource in value.get('objects', []):
            kind = resource['resource_kind']
            if self.dialect.engine_id == 'firebird' and kind == 'column':
                resource['operations'] = [
                    item for item in resource['operations']
                    if item['operation_id'] not in {'alter', 'comment'}
                ] + [{
                    'operation_id': operation, 'title': title,
                    'mutation_class': 'admin', 'target_required': True,
                    'confirmation_required': True,
                } for operation, title in (
                    ('alter', 'Alter column'), ('comment', 'Edit comment'))]
            if (self.dialect.engine_id == 'firebird' and
                    kind in firebird_mappings.KINDS):
                resource['operations'] = [
                    item for item in resource['operations']
                    if item['operation_id'] not in {
                        'create_or_alter', 'comment'}
                ] + [{
                    'operation_id': operation,
                    'title': title, 'mutation_class': 'admin',
                    'target_required': True,
                    'confirmation_required': True,
                } for operation, title in (
                    ('create_or_alter', 'Create or alter mapping'),
                    ('comment', 'Edit mapping comment'))]
            if self.dialect.engine_id == 'firebird' and kind == 'role':
                resource['operations'] = [
                    item for item in resource.get('operations', [])
                    if item['operation_id'] != 'configure_admin_mapping'
                ] + [{
                        'operation_id': 'configure_admin_mapping',
                        'title': 'Configure Windows administrator mapping',
                        'mutation_class': 'admin',
                        'target_required': True,
                        'confirmation_required': True,
                        'target_resource_names': ['RDB$ADMIN'],
                        'allow_system_target': True,
                    }]
            resource['operations'] = [
                operation for operation in resource.get('operations', [])
                if self.supports(kind, operation['operation_id'])
            ]
            for operation in resource.get('operations', []):
                operation_id = operation['operation_id']
                if self.supports(kind, operation_id):
                    if (
                        self.dialect.engine_id == 'firebird' and
                        kind == 'database' and
                        operation_id in _FIREBIRD_SERVICE_OPERATIONS
                    ):
                        # These operations are executed by Firebird's service
                        # manager.  Their forms must remain available while
                        # the database itself rejects ordinary attachments
                        # (most importantly after shutdown, so Bring online
                        # is not stranded behind a database connection).
                        operation['workspace_scope'] = 'server_service'
                    database_form = (
                        self.dialect.database_forms.get(operation_id)
                        if kind == 'database' else None
                    )
                    if (
                        self.dialect.engine_id == 'mariadb' and
                        kind == 'replication-channel'
                    ):
                        # The portfolio profile only declares which native
                        # operations exist. MariaDB owns the complete CHANGE
                        # MASTER/START/STOP/RESET field contract here; the
                        # one-field portfolio layout is not executable
                        # replication administration.
                        operation['form'] = self._form(kind, operation_id)
                    elif database_form is not None:
                        operation['form'] = copy.deepcopy(database_form)
                    elif operation.get('form_authority') != 'engine-profile':
                        operation['form'] = self._form(kind, operation_id)
                    if (self.dialect.engine_id == 'firebird' and
                            kind in (*firebird_mappings.KINDS, 'column')):
                        operation['title'] = operation['form']['title']
                    self._structured_record_controls(operation['form'])
                    self._routine_record_controls(operation['form'])
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
        if (self.dialect.engine_id == 'firebird' and
                resource_kind == 'privilege' and
                operation_id in {'grant', 'revoke'}):
            try:
                firebird_privileges.compile_privilege(operation_id, draft)
            except RelationalClientError as error:
                errors.append({'field_id': None, 'code': 'invalid_privilege',
                               'message': str(error)})
            return {'errors': errors}
        if (self.dialect.engine_id == 'firebird' and
                resource_kind == 'column' and operation_id == 'create' and
                'column_mode' in draft):
            try:
                firebird_columns.compile_create(draft)
            except RelationalClientError as error:
                errors.append({'field_id': None, 'code': 'invalid_column',
                               'message': str(error)})
            return {'errors': errors}
        if (self.dialect.engine_id == 'firebird' and
                resource_kind == 'column' and
                operation_id in {'alter', 'comment'}):
            try:
                firebird_columns.validate_target(
                    operation_id, draft, request.get('target_resource'))
                firebird_columns.compile_column(
                    operation_id, draft,
                    self._target_path(request.get('target_resource')))
            except RelationalClientError as error:
                errors.append({'field_id': None, 'code': 'invalid_column',
                               'message': str(error)})
            return {'errors': errors}
        if (self.dialect.engine_id == 'firebird' and
                resource_kind in firebird_mappings.KINDS and
                operation_id != 'inspect'):
            try:
                firebird_mappings.compile_mapping(
                    resource_kind, operation_id, draft,
                    request.get('target_resource'))
            except RelationalClientError as error:
                errors.append({'field_id': None, 'code': 'invalid_mapping',
                               'message': str(error)})
            return {'errors': errors}
        if (self.dialect.engine_id == 'firebird' and
                operation_id == 'drop' and draft.get('cascade')):
            errors.append({
                'field_id': 'cascade',
                'code': 'firebird_drop_cascade_unsupported',
                'message': 'Firebird does not support DROP CASCADE. '
                'Review and resolve dependent objects explicitly.',
            })
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
        if (
            self.dialect.engine_id == 'firebird' and
            resource_kind == 'database' and operation_id == 'create'
        ):
            page_size = draft.get('page_size', '8192')
            if page_size not in {'4096', '8192', '16384', '32768'}:
                errors.append({
                    'field_id': 'page_size',
                    'code': 'invalid_firebird_page_size',
                    'message': 'Firebird page size is not supported.',
                })
            dialect = draft.get('sql_dialect', '3')
            if dialect not in {'1', '3'}:
                errors.append({
                    'field_id': 'sql_dialect',
                    'code': 'invalid_firebird_sql_dialect',
                    'message': 'Firebird database SQL dialect must be 1 or 3.',
                })
            charset = draft.get('default_charset', 'UTF8')
            if not isinstance(charset, str) or not re.fullmatch(
                r'[A-Za-z][A-Za-z0-9_$]{0,62}', charset
            ):
                errors.append({
                    'field_id': 'default_charset',
                    'code': 'invalid_firebird_character_set',
                    'message': 'Firebird default character set is invalid.',
                })
        if (
            self.dialect.engine_id == 'firebird' and
            resource_kind == 'database' and operation_id == 'alter'
        ):
            admitted = {
                'default_charset', 'linger_seconds', 'drop_linger',
                'default_sql_security',
            }
            supplied = {
                key for key, item in draft.items()
                if key in admitted and item not in {None, ''}
            }
            if not supplied:
                errors.append({
                    'field_id': None,
                    'code': 'firebird_database_change_required',
                    'message': 'Select at least one Firebird database change.',
                })
            unknown = set(draft).difference(admitted)
            if unknown:
                errors.append({
                    'field_id': None,
                    'code': 'unknown_firebird_database_change',
                    'message': 'The Firebird database change is unsupported.',
                })
            charset = draft.get('default_charset')
            if charset not in {None, ''} and (
                    not isinstance(charset, str) or not re.fullmatch(
                        r'[A-Za-z][A-Za-z0-9_$]{0,62}', charset
                    )):
                errors.append({
                    'field_id': 'default_charset',
                    'code': 'invalid_firebird_character_set',
                    'message': 'Firebird default character set is invalid.',
                })
            linger = draft.get('linger_seconds')
            if linger not in {None, ''} and (
                    isinstance(linger, bool) or not isinstance(linger, int) or
                    linger < 0 or linger > 2147483647):
                errors.append({
                    'field_id': 'linger_seconds',
                    'code': 'invalid_firebird_linger',
                    'message': 'Firebird linger time is invalid.',
                })
            if draft.get('drop_linger') and linger not in {None, ''}:
                errors.append({
                    'field_id': 'drop_linger',
                    'code': 'conflicting_firebird_linger_change',
                    'message': 'Set or drop linger, but do not request both.',
                })
            if draft.get('default_sql_security') not in {
                    None, '', 'DEFINER', 'INVOKER'}:
                errors.append({
                    'field_id': 'default_sql_security',
                    'code': 'invalid_firebird_sql_security',
                    'message': 'Firebird SQL security mode is invalid.',
                })
        if (
            self.dialect.engine_id == 'firebird' and
            resource_kind == 'database' and operation_id == 'drop'
        ):
            route = request.get('_provider_route', {})
            expected = route.get('database') if isinstance(
                route, Mapping
            ) else None
            if set(draft).difference({'confirmation'}):
                errors.append({
                    'field_id': None,
                    'code': 'unknown_firebird_database_drop_option',
                    'message': (
                        'A Firebird database drop option is unknown.'
                    ),
                })
            if not isinstance(expected, str) or not expected:
                errors.append({
                    'field_id': None,
                    'code': 'firebird_database_target_required',
                    'message': (
                        'Firebird database deletion requires a retained '
                        'database route.'
                    ),
                })
            elif draft.get('confirmation') != expected:
                errors.append({
                    'field_id': 'confirmation',
                    'code': 'firebird_database_confirmation_mismatch',
                    'message': (
                        'Firebird database deletion confirmation must '
                        'exactly match the database filename or alias.'
                    ),
                })
        if (
            self.dialect.engine_id == 'firebird' and
            resource_kind == 'database' and
            operation_id in _FIREBIRD_SERVICE_OPERATIONS
        ):
            errors.extend(self._validate_firebird_service(
                operation_id, draft
            ))
        if (
            self.dialect.engine_id == 'sqlite' and
            resource_kind == 'database' and operation_id == 'create'
        ):
            errors.extend(self._validate_sqlite_database_create(draft))
        if (
            self.dialect.engine_id == 'sqlite' and
            resource_kind == 'database' and operation_id == 'alter'
        ):
            errors.extend(self._validate_sqlite_database_alter(draft))
        if (
            self.dialect.engine_id == 'sqlite' and
            resource_kind == 'database' and operation_id == 'drop'
        ):
            target = request.get('target_resource') or {}
            extensions = target.get('extensions', {})
            native_target = extensions.get('cdeadmin', {})
            expected = native_target.get('native_name')
            if not isinstance(expected, str) or not expected:
                errors.append({
                    'field_id': None,
                    'code': 'sqlite_database_target_required',
                    'message': (
                        'SQLite file deletion requires a retained database '
                        'target.'
                    ),
                })
            elif draft.get('confirmation') != expected:
                errors.append({
                    'field_id': 'confirmation',
                    'code': 'sqlite_database_confirmation_mismatch',
                    'message': (
                        'SQLite file deletion confirmation must exactly '
                        'match the database path.'
                    ),
                })
        if (
            self.dialect.engine_id == 'sqlite' and
            resource_kind == 'database' and
            operation_id in _SQLITE_DATABASE_OPERATIONS
        ):
            errors.extend(self._validate_sqlite_database_operation(
                operation_id, draft, request.get('_provider_route', {})
            ))
        if (
            self.dialect.engine_id == 'duckdb' and
            resource_kind == 'database' and operation_id == 'create'
        ):
            unknown = set(draft).difference({'name', 'config'})
            config = draft.get('config', {})
            if unknown:
                errors.append({
                    'field_id': None,
                    'code': 'unknown_duckdb_database_create_option',
                    'message': 'A DuckDB creation option is unknown.',
                })
            if not isinstance(config, Mapping) or not all(
                isinstance(key, str) and key.strip() and
                isinstance(value, (str, int, float, bool)) and
                value is not None for key, value in config.items()
            ):
                errors.append({
                    'field_id': 'config',
                    'code': 'invalid_duckdb_creation_config',
                    'message': (
                        'DuckDB creation configuration must contain named '
                        'scalar values.'
                    ),
                })
        if (
            self.dialect.engine_id == 'duckdb' and
            resource_kind == 'database' and operation_id == 'drop'
        ):
            route = request.get('_provider_route', {})
            expected = route.get('database') if isinstance(
                route, Mapping) else None
            if not isinstance(expected, str) or not expected:
                errors.append({
                    'field_id': None,
                    'code': 'duckdb_database_target_required',
                    'message': (
                        'DuckDB file deletion requires a retained database '
                        'route.'
                    ),
                })
            elif draft.get('confirmation') != expected:
                errors.append({
                    'field_id': 'confirmation',
                    'code': 'duckdb_database_confirmation_mismatch',
                    'message': (
                        'DuckDB file deletion confirmation must exactly '
                        'match the database path.'
                    ),
                })
        if (
            self.dialect.engine_id == 'duckdb' and
            resource_kind == 'database' and
            operation_id in _DUCKDB_DATABASE_OPERATIONS
        ):
            errors.extend(self._validate_duckdb_database_operation(
                operation_id, draft, request.get('_provider_route', {})
            ))
        if (
            self.dialect.engine_id == 'mysql' and
            resource_kind == 'database' and operation_id in {'create', 'alter'}
        ):
            errors.extend(self._validate_mysql_database(operation_id, draft))
        if (
            self.dialect.engine_id == 'mariadb' and
            resource_kind == 'database' and operation_id in {'create', 'alter'}
        ):
            errors.extend(self._validate_mariadb_database(
                operation_id, draft
            ))
        if (
            self.dialect.engine_id == 'mysql' and
            resource_kind == 'database' and operation_id == 'drop'
        ):
            target = request.get('target_resource') or {}
            expected = target.get('display_name')
            if set(draft).difference({'confirmation'}):
                errors.append({
                    'field_id': None,
                    'code': 'unknown_mysql_database_drop_option',
                    'message': 'A MySQL database drop option is unknown.',
                })
            if not isinstance(expected, str) or not expected:
                errors.append({
                    'field_id': None,
                    'code': 'mysql_database_target_required',
                    'message': 'MySQL database deletion requires a target.',
                })
            elif draft.get('confirmation') != expected:
                errors.append({
                    'field_id': 'confirmation',
                    'code': 'mysql_database_confirmation_mismatch',
                    'message': (
                        'MySQL database deletion confirmation must exactly '
                        'match the database name.'
                    ),
                })
        if (
            self.dialect.engine_id == 'mariadb' and
            resource_kind == 'database' and operation_id == 'drop'
        ):
            target = request.get('target_resource') or {}
            expected = target.get('display_name')
            if set(draft).difference({'confirmation'}):
                errors.append({
                    'field_id': None,
                    'code': 'unknown_mariadb_database_drop_option',
                    'message': 'A MariaDB database drop option is unknown.',
                })
            if not isinstance(expected, str) or not expected:
                errors.append({
                    'field_id': None,
                    'code': 'mariadb_database_target_required',
                    'message': 'MariaDB database deletion requires a target.',
                })
            elif draft.get('confirmation') != expected:
                errors.append({
                    'field_id': 'confirmation',
                    'code': 'mariadb_database_confirmation_mismatch',
                    'message': (
                        'MariaDB database deletion confirmation must exactly '
                        'match the database name.'
                    ),
                })
        if (
            self.dialect.engine_id == 'mysql' and
            resource_kind == 'database' and
            operation_id in _MYSQL_DATABASE_OPERATIONS
        ):
            errors.extend(self._validate_mysql_database_operation(
                operation_id, draft, request.get('_provider_route', {})
            ))
        if (
            self.dialect.engine_id == 'mariadb' and
            resource_kind == 'database' and
            operation_id in _MARIADB_DATABASE_OPERATIONS
        ):
            errors.extend(self._validate_mariadb_database_operation(
                operation_id, draft, request.get('_provider_route', {})
            ))
        if (
            self.dialect.engine_id == 'mariadb' and
            resource_kind == 'database' and
            operation_id in _MARIADB_TOOL_DATABASE_OPERATIONS
        ):
            errors.extend(self._validate_mariadb_tool_operation(
                operation_id, draft, request.get('_provider_route', {})
            ))
        if self.dialect.engine_id == 'mariadb' and resource_kind in {
                'user', 'role', 'privilege'}:
            errors.extend(self._validate_mariadb_security(
                resource_kind, operation_id, draft
            ))
        if self.dialect.engine_id == 'mariadb' and (
                resource_kind == 'replication-channel'):
            errors.extend(self._validate_mariadb_replication(
                operation_id, draft, request.get('target_resource')
            ))
        if (
            self.dialect.engine_id == 'mariadb' and
            resource_kind == 'system-variable' and
            operation_id == 'set_global'
        ):
            errors.extend(self._validate_mariadb_system_variable(
                draft, request.get('target_resource')
            ))
        if (
            self.dialect.engine_id == 'mariadb' and
            resource_kind == 'session' and
            operation_id in {'terminate_query', 'terminate_connection'}
        ):
            errors.extend(self._validate_mariadb_session_termination(
                draft, request.get('target_resource')
            ))
        if (
            self.dialect.engine_id == 'mariadb' and
            resource_kind == 'binary-log' and
            operation_id == 'purge_before'
        ):
            target = request.get('target_resource') or {}
            name = target.get('display_name')
            if not isinstance(name, str) or re.fullmatch(
                    r'[A-Za-z0-9_.-]+', name) is None:
                errors.append({
                    'field_id': None,
                    'code': 'invalid_mariadb_binary_log_target',
                    'message': 'MariaDB binary-log identity is invalid.',
                })
            if draft.get('confirmation') != name:
                errors.append({
                    'field_id': 'confirmation',
                    'code': 'mariadb_binary_log_confirmation_mismatch',
                    'message': (
                        'Confirmation must exactly match the retained '
                        'binary-log name.'
                    ),
                })
        if (
            self.dialect.engine_id == 'mysql' and
            resource_kind == 'database' and
            operation_id in _MYSQL_SHELL_DATABASE_OPERATIONS
        ):
            errors.extend(self._validate_mysql_shell_operation(
                operation_id, draft, request.get('_provider_route', {})
            ))
        return {'errors': errors}

    @classmethod
    def _validate_mariadb_security(cls, kind, operation, draft):
        """Validate only MariaDB 12.2 account, role, and grant clauses."""
        errors = []
        if kind == 'user' and operation in {'create', 'alter'}:
            mode = draft.get(
                'authentication_mode',
                'PASSWORD' if operation == 'create' else 'UNCHANGED',
            )
            allowed_modes = {
                'PASSWORD', 'PLUGIN_PASSWORD', 'PLUGIN_STRING',
                'PLUGIN_ONLY', 'NONE', 'UNCHANGED',
            }
            if mode not in allowed_modes or (
                    operation == 'create' and mode == 'UNCHANGED') or (
                    operation == 'alter' and mode == 'NONE'):
                errors.append({
                    'field_id': 'authentication_mode',
                    'code': 'invalid_mariadb_authentication_mode',
                    'message': 'MariaDB authentication mode is invalid.',
                })
            if mode in {'PASSWORD', 'PLUGIN_PASSWORD'} and not draft.get(
                    'password'):
                errors.append({
                    'field_id': 'password',
                    'code': 'mariadb_password_required',
                    'message': 'The selected MariaDB authentication requires '
                               'a password.',
                })
            if mode in {'PLUGIN_PASSWORD', 'PLUGIN_STRING', 'PLUGIN_ONLY'}:
                plugin = draft.get('plugin')
                if not isinstance(plugin, str) or re.fullmatch(
                        r'[A-Za-z0-9_$]+', plugin) is None:
                    errors.append({
                        'field_id': 'plugin',
                        'code': 'invalid_mariadb_authentication_plugin',
                        'message': (
                            'A MariaDB authentication plugin identifier is '
                            'required.'
                        ),
                    })
            if mode == 'PLUGIN_STRING' and not draft.get(
                    'authentication_string'):
                errors.append({
                    'field_id': 'authentication_string',
                    'code': 'mariadb_authentication_string_required',
                    'message': 'The selected MariaDB plugin requires an '
                               'authentication string.',
                })
            additional = draft.get('additional_authentication', [])
            if not isinstance(additional, list):
                errors.append({
                    'field_id': 'additional_authentication',
                    'code': 'invalid_mariadb_authentication_chain',
                    'message': 'Additional MariaDB authentication methods '
                               'must be an array.',
                })
            else:
                for item in additional:
                    if not isinstance(item, Mapping) or not isinstance(
                            item.get('plugin'), str) or re.fullmatch(
                                r'[A-Za-z0-9_$]+', item.get('plugin', '')
                            ) is None or set(item).difference({
                                'plugin', 'password',
                                'authentication_string',
                            }) or ('password' in item and
                                   'authentication_string' in item):
                        errors.append({
                            'field_id': 'additional_authentication',
                            'code': 'invalid_mariadb_authentication_chain',
                            'message': (
                                'Each additional MariaDB authentication '
                                'method must declare one plugin and at most '
                                'one credential.'
                            ),
                        })
                        break
            tls = draft.get(
                'tls_requirement',
                'NONE' if operation == 'create' else 'UNCHANGED',
            )
            if tls not in {'UNCHANGED', 'NONE', 'SSL', 'X509', 'SPECIFIED'}:
                errors.append({
                    'field_id': 'tls_requirement',
                    'code': 'invalid_mariadb_tls_requirement',
                    'message': 'MariaDB TLS requirement is invalid.',
                })
            specified = any(draft.get(key) for key in (
                'tls_cipher', 'x509_issuer', 'x509_subject'
            ))
            if tls == 'SPECIFIED' and not specified:
                errors.append({
                    'field_id': 'tls_requirement',
                    'code': 'mariadb_tls_attribute_required',
                    'message': 'Specified MariaDB TLS requirements need a '
                               'cipher, issuer, or subject.',
                })
            if tls != 'SPECIFIED' and specified:
                errors.append({
                    'field_id': 'tls_requirement',
                    'code': 'mariadb_tls_attribute_not_admitted',
                    'message': 'TLS attributes require the SPECIFIED mode.',
                })
            account_lock = draft.get(
                'account_lock', 'UNLOCK' if operation == 'create'
                else 'UNCHANGED'
            )
            if account_lock not in {'UNCHANGED', 'LOCK', 'UNLOCK'} or (
                    operation == 'create' and account_lock == 'UNCHANGED'):
                errors.append({
                    'field_id': 'account_lock',
                    'code': 'invalid_mariadb_account_lock',
                    'message': 'MariaDB account lock state is invalid.',
                })
            for field in (
                    'max_queries_per_hour', 'max_updates_per_hour',
                    'max_connections_per_hour', 'max_user_connections'):
                value = draft.get(field)
                if value not in {None, ''} and (
                        isinstance(value, bool) or not isinstance(value, int)
                        or value < 0 or value > 4294967295):
                    errors.append({
                        'field_id': field,
                        'code': 'invalid_mariadb_account_limit',
                        'message': 'MariaDB account limits must be unsigned '
                                   '32-bit integers.',
                    })
            statement_time = draft.get('max_statement_time')
            if statement_time not in {None, ''} and (
                    isinstance(statement_time, bool) or not isinstance(
                        statement_time, (int, float)) or statement_time < 0):
                errors.append({
                    'field_id': 'max_statement_time',
                    'code': 'invalid_mariadb_statement_time',
                    'message': 'MariaDB maximum statement time must be '
                               'non-negative.',
                })
            expiration = draft.get(
                'password_expiration',
                'DEFAULT' if operation == 'create' else 'UNCHANGED',
            )
            if expiration not in {
                    'UNCHANGED', 'DEFAULT', 'NEVER', 'NOW', 'INTERVAL'}:
                errors.append({
                    'field_id': 'password_expiration',
                    'code': 'invalid_mariadb_password_expiration',
                    'message': 'MariaDB password expiration is invalid.',
                })
            days = draft.get('password_expiration_days')
            if expiration == 'INTERVAL' and (
                    isinstance(days, bool) or not isinstance(days, int) or
                    days < 1 or days > 4294967295):
                errors.append({
                    'field_id': 'password_expiration_days',
                    'code': 'invalid_mariadb_password_expiration_days',
                    'message': 'MariaDB password lifetime must be a positive '
                               'integer.',
                })
            if operation == 'alter' and all(
                    value is None or value == '' or value == [] or
                    value == 'UNCHANGED'
                    for value in draft.values()):
                errors.append({
                    'field_id': None,
                    'code': 'mariadb_user_change_required',
                    'message': 'Select at least one MariaDB account change.',
                })
        elif kind == 'role' and operation in {
                'grant', 'revoke', 'set_default'}:
            if draft.get('member_kind') not in {'USER', 'ROLE'}:
                errors.append({
                    'field_id': 'member_kind',
                    'code': 'invalid_mariadb_role_member_kind',
                    'message': 'MariaDB role members are users or roles.',
                })
        elif kind == 'privilege' and operation in {'grant', 'revoke'}:
            if draft.get('principal_kind') not in {'USER', 'ROLE'}:
                errors.append({
                    'field_id': 'principal_kind',
                    'code': 'invalid_mariadb_privilege_principal_kind',
                    'message': 'MariaDB privilege principals are users or '
                               'roles.',
                })
            if draft.get('object_type') not in {
                    'GLOBAL', 'DATABASE', 'TABLE', 'FUNCTION', 'PROCEDURE',
                    'SEQUENCE'}:
                errors.append({
                    'field_id': 'object_type',
                    'code': 'invalid_mariadb_privilege_scope',
                    'message': 'MariaDB privilege scope is invalid.',
                })
        return errors

    @staticmethod
    def _validate_mariadb_replication(operation, draft, target):
        errors = []
        if operation in {'create', 'alter'}:
            if operation == 'create' and not draft.get('name'):
                errors.append({
                    'field_id': 'name',
                    'code': 'mariadb_replication_name_required',
                    'message': 'A MariaDB replication connection name is '
                               'required.',
                })
            changed = {
                key: value for key, value in draft.items()
                if key != 'name' and value is not None and value != '' and
                value != 'UNCHANGED' and value != []
            }
            if not changed:
                errors.append({
                    'field_id': None,
                    'code': 'mariadb_replication_change_required',
                    'message': 'Select at least one MariaDB replication '
                               'connection change.',
                })
            if operation == 'create' and not all(
                    draft.get(key) for key in ('master_host', 'master_user')):
                errors.append({
                    'field_id': 'master_host',
                    'code': 'mariadb_replication_source_required',
                    'message': 'A new MariaDB replication connection requires '
                               'the primary host and replication user.',
                })
            for key in (
                    'master_port', 'connect_retry', 'retry_count',
                    'replication_delay', 'master_log_position',
                    'relay_log_position'):
                value = draft.get(key)
                if value not in {None, ''} and (
                        isinstance(value, bool) or not isinstance(value, int)
                        or value < 0 or value > 4294967295):
                    errors.append({
                        'field_id': key,
                        'code': 'invalid_mariadb_replication_integer',
                        'message': 'MariaDB replication integer values must '
                                   'be unsigned 32-bit values.',
                    })
            heartbeat = draft.get('heartbeat_period')
            if heartbeat not in {None, ''} and (
                    isinstance(heartbeat, bool) or not isinstance(
                        heartbeat, (int, float)) or heartbeat < 0):
                errors.append({
                    'field_id': 'heartbeat_period',
                    'code': 'invalid_mariadb_replication_heartbeat',
                    'message': 'MariaDB replication heartbeat must be '
                               'non-negative.',
                })
            for key in ('ignore_server_ids', 'do_domain_ids',
                        'ignore_domain_ids'):
                value = draft.get(key, [])
                if not isinstance(value, list) or any(
                        isinstance(item, bool) or not isinstance(item, int) or
                        item < 0 or item > 4294967295 for item in value):
                    errors.append({
                        'field_id': key,
                        'code': 'invalid_mariadb_replication_id_list',
                        'message': 'MariaDB replication ID lists contain only '
                                   'unsigned 32-bit integers.',
                    })
        elif operation in {'start', 'stop'}:
            if draft.get('thread', 'ALL') not in {
                    'ALL', 'IO_THREAD', 'SQL_THREAD'}:
                errors.append({
                    'field_id': 'thread',
                    'code': 'invalid_mariadb_replication_thread',
                    'message': 'MariaDB replication thread selection is '
                               'invalid.',
                })
            if operation == 'start':
                until = draft.get('until_mode', 'NONE')
                if until not in {
                        'NONE', 'MASTER_POSITION', 'RELAY_POSITION',
                        'MASTER_GTID_POS', 'SQL_AFTER_GTIDS',
                        'SQL_BEFORE_GTIDS'}:
                    errors.append({
                        'field_id': 'until_mode',
                        'code': 'invalid_mariadb_replication_until',
                        'message': 'MariaDB replication stop condition is '
                                   'invalid.',
                    })
                if until in {'MASTER_POSITION', 'RELAY_POSITION'} and (
                        not draft.get('until_log_file') or
                        isinstance(draft.get('until_log_position'), bool) or
                        not isinstance(draft.get('until_log_position'), int)):
                    errors.append({
                        'field_id': 'until_log_file',
                        'code': 'mariadb_replication_position_required',
                        'message': 'MariaDB file positioning requires a log '
                                   'file and integer position.',
                    })
                if until in {
                        'MASTER_GTID_POS', 'SQL_AFTER_GTIDS',
                        'SQL_BEFORE_GTIDS'} and not draft.get('until_gtid'):
                    errors.append({
                        'field_id': 'until_gtid',
                        'code': 'mariadb_replication_gtid_required',
                        'message': 'MariaDB GTID positioning requires a GTID '
                                   'value.',
                    })
        elif operation == 'reset':
            expected = target.get('display_name') if isinstance(
                target, Mapping) else None
            if draft.get('confirmation') != expected:
                errors.append({
                    'field_id': 'confirmation',
                    'code': 'mariadb_replication_confirmation_mismatch',
                    'message': 'Type the exact MariaDB replication connection '
                               'name to confirm reset.',
                })
        return errors

    @staticmethod
    def _validate_mariadb_system_variable(draft, target):
        errors = []
        target = target if isinstance(target, Mapping) else {}
        native = RelationalAdministration._mariadb_target_native(
            target
        )
        name = target.get('display_name')
        if not isinstance(name, str) or not re.fullmatch(
                r'[A-Za-z][A-Za-z0-9_]{0,63}', name):
            errors.append({
                'field_id': None,
                'code': 'invalid_mariadb_system_variable_target',
                'message': 'The MariaDB system-variable target is invalid.',
            })
        if str(native.get('read_only', '')).upper() != 'NO':
            errors.append({
                'field_id': None,
                'code': 'mariadb_system_variable_read_only',
                'message': (
                    'MariaDB reports this system variable as read-only.'
                ),
            })
        if 'GLOBAL' not in str(native.get('variable_scope', '')).upper():
            errors.append({
                'field_id': None,
                'code': 'mariadb_system_variable_not_global',
                'message': (
                    'MariaDB does not expose a global scope for this '
                    'variable.'
                ),
            })
        mode = draft.get('value_mode')
        if mode not in {'VALUE', 'DEFAULT'}:
            errors.append({
                'field_id': 'value_mode',
                'code': 'invalid_mariadb_system_variable_value_mode',
                'message': 'Select a specified value or the compiled default.',
            })
        if mode == 'VALUE':
            if 'value' not in draft:
                errors.append({
                    'field_id': 'value',
                    'code': 'mariadb_system_variable_value_required',
                    'message': 'A MariaDB global value is required.',
                })
            else:
                value = draft.get('value')
                variable_type = str(native.get(
                    'variable_type', ''
                )).upper()
                try:
                    if variable_type in {
                            'INT', 'INT UNSIGNED', 'BIGINT UNSIGNED'}:
                        parsed = int(str(value), 10)
                        minimum = native.get('numeric_min_value')
                        maximum = native.get('numeric_max_value')
                        if minimum not in {None, ''} and parsed < int(minimum):
                            raise ValueError
                        if maximum not in {None, ''} and parsed > int(maximum):
                            raise ValueError
                    elif variable_type == 'DOUBLE':
                        parsed = float(str(value))
                        if parsed != parsed or parsed in {
                                float('inf'), float('-inf')}:
                            raise ValueError
                        minimum = native.get('numeric_min_value')
                        maximum = native.get('numeric_max_value')
                        if minimum not in {None, ''} and parsed < float(
                                minimum):
                            raise ValueError
                        if maximum not in {None, ''} and parsed > float(
                                maximum):
                            raise ValueError
                    elif variable_type == 'BOOLEAN':
                        if str(value).upper() not in {
                                'ON', 'OFF', 'TRUE', 'FALSE', '1', '0'}:
                            raise ValueError
                    elif variable_type in {'ENUM', 'SET'}:
                        choices = {
                            item for item in str(native.get(
                                'enum_value_list', '')
                            ).split(',') if item
                        }
                        selected = (
                            {str(value)} if variable_type == 'ENUM' else
                            {item for item in str(value).split(',') if item}
                        )
                        if not selected or not selected.issubset(choices):
                            raise ValueError
                    elif variable_type != 'VARCHAR':
                        raise ValueError
                except (TypeError, ValueError):
                    errors.append({
                        'field_id': 'value',
                        'code': 'invalid_mariadb_system_variable_value',
                        'message': (
                            'The value does not match MariaDB '
                            'system-variable metadata.'
                        ),
                    })
        return errors

    @staticmethod
    def _mariadb_target_native(target):
        native = target.get('native')
        if isinstance(native, Mapping):
            return native
        extensions = target.get('extensions')
        extensions = extensions if isinstance(extensions, Mapping) else {}
        provider_extension = extensions.get('mariadb')
        provider_extension = (
            provider_extension
            if isinstance(provider_extension, Mapping) else {}
        )
        native = provider_extension.get('native')
        return native if isinstance(native, Mapping) else {}

    @staticmethod
    def _validate_mariadb_session_termination(draft, target):
        target = target if isinstance(target, Mapping) else {}
        native = RelationalAdministration._mariadb_target_native(target)
        process_id = native.get('id')
        errors = []
        if isinstance(process_id, bool) or not isinstance(process_id, int) or (
                process_id <= 0):
            errors.append({
                'field_id': None,
                'code': 'invalid_mariadb_process_target',
                'message': 'MariaDB did not provide a valid process ID.',
            })
        if draft.get('termination_mode') not in {'SOFT', 'HARD'}:
            errors.append({
                'field_id': 'termination_mode',
                'code': 'invalid_mariadb_termination_mode',
                'message': 'Select MariaDB soft or hard termination.',
            })
        if draft.get('confirmation') != str(process_id):
            errors.append({
                'field_id': 'confirmation',
                'code': 'mariadb_process_confirmation_mismatch',
                'message': 'Confirmation must exactly match the process ID.',
            })
        return errors

    @staticmethod
    def _validate_mysql_shell_operation(operation, draft, route):
        backup_fields = {
            'path', 'consistent', 'skip_consistency_checks', 'ddl_only',
            'data_only', 'checksum', 'chunking', 'bytes_per_chunk',
            'threads', 'max_rate', 'show_progress',
            'default_character_set', 'compression', 'tz_utc', 'events',
            'routines', 'libraries', 'triggers', 'include_tables',
            'exclude_tables', 'include_events', 'exclude_events',
            'include_routines', 'exclude_routines', 'include_libraries',
            'exclude_libraries', 'include_triggers', 'exclude_triggers',
            'partitions', 'where', 'compatibility', 'target_version',
            'skip_upgrade_checks', 'dry_run',
        }
        restore_fields = {
            'path', 'confirmation', 'analyze_tables',
            'background_threads', 'character_set', 'checksum',
            'create_invisible_pks', 'defer_table_indexes',
            'disable_bulk_load', 'drop_existing_objects',
            'enable_local_infile', 'dry_run',
            'exclude_events', 'exclude_libraries', 'exclude_routines',
            'exclude_schemas', 'exclude_tables', 'exclude_triggers',
            'exclude_users', 'handle_grant_errors',
            'ignore_existing_objects', 'ignore_version', 'include_events',
            'include_libraries', 'include_routines', 'include_schemas',
            'include_tables', 'include_triggers', 'include_users',
            'load_data', 'load_ddl', 'load_indexes', 'load_users',
            'max_bytes_per_transaction', 'progress_file',
            'reset_progress', 'schema', 'session_init_sql',
            'show_metadata', 'show_progress', 'skip_binlog', 'threads',
            'update_gtid_set', 'wait_dump_timeout',
        }
        fields = backup_fields if operation == 'backup_logical' else (
            restore_fields
        )
        errors = []
        unknown = set(draft).difference(fields)
        if unknown:
            errors.append({
                'field_id': None, 'code': 'unknown_mysql_shell_option',
                'message': 'A MySQL Shell option is unknown.',
            })
        for field in ('tool_workspace', 'host'):
            if not isinstance(route.get(field), str) or not route[field]:
                errors.append({
                    'field_id': None,
                    'code': f'mysql_shell_{field}_required',
                    'message': f'MySQL Shell {field.replace("_", " ")} '
                               'is required.',
                })
        if not isinstance(route.get('database'), str) or not route['database']:
            errors.append({
                'field_id': None, 'code': 'mysql_shell_database_required',
                'message': 'MySQL Shell requires a database target.',
            })
        path = draft.get('path')
        if not isinstance(path, str) or not path or '\x00' in path:
            errors.append({
                'field_id': 'path', 'code': 'invalid_mysql_shell_path',
                'message': 'MySQL Shell dump path is required.',
            })
        boolean_fields = {
            'consistent', 'skip_consistency_checks', 'ddl_only', 'data_only',
            'checksum', 'chunking', 'show_progress', 'tz_utc', 'events',
            'routines', 'libraries', 'triggers', 'skip_upgrade_checks',
            'dry_run', 'create_invisible_pks', 'disable_bulk_load',
            'drop_existing_objects', 'enable_local_infile',
            'ignore_existing_objects',
            'ignore_version', 'load_data', 'load_ddl', 'load_indexes',
            'load_users', 'reset_progress', 'show_metadata', 'skip_binlog',
        }
        for field in sorted(boolean_fields.intersection(draft)):
            if not isinstance(draft[field], bool):
                errors.append({
                    'field_id': field, 'code': 'invalid_mysql_shell_boolean',
                    'message': 'MySQL Shell boolean option is invalid.',
                })
        integer_limits = {
            'threads': (1, 1024), 'background_threads': (0, 1024),
        }
        for field, (minimum, maximum) in integer_limits.items():
            if field in draft and (
                isinstance(draft[field], bool) or
                not isinstance(draft[field], int) or
                not minimum <= draft[field] <= maximum
            ):
                errors.append({
                    'field_id': field, 'code': 'invalid_mysql_shell_integer',
                    'message': 'MySQL Shell numeric option is invalid.',
                })
        if 'wait_dump_timeout' in draft and (
            isinstance(draft['wait_dump_timeout'], bool) or
            not isinstance(draft['wait_dump_timeout'], (int, float)) or
            not 0 <= draft['wait_dump_timeout'] <= 86400
        ):
            errors.append({
                'field_id': 'wait_dump_timeout',
                'code': 'invalid_mysql_shell_number',
                'message': 'MySQL Shell wait timeout is invalid.',
            })
        array_fields = {
            name for name in fields
            if name.startswith(('include_', 'exclude_'))
        } | {'compatibility', 'session_init_sql'}
        for field in sorted(array_fields.intersection(draft)):
            value = draft[field]
            if not isinstance(value, list) or not all(
                    isinstance(item, str) and item and '\x00' not in item
                    for item in value):
                errors.append({
                    'field_id': field, 'code': 'invalid_mysql_shell_array',
                    'message': 'MySQL Shell list option is invalid.',
                })
        for field in ('partitions', 'where'):
            if field in draft and not isinstance(draft[field], Mapping):
                errors.append({
                    'field_id': field, 'code': 'invalid_mysql_shell_mapping',
                    'message': 'MySQL Shell mapping option is invalid.',
                })
        if draft.get('ddl_only') and draft.get('data_only'):
            errors.append({
                'field_id': 'data_only', 'code': 'mysql_shell_dump_mode',
                'message': 'DDL-only and data-only cannot both be enabled.',
            })
        if draft.get('skip_consistency_checks') and not draft.get(
                'consistent', True):
            errors.append({
                'field_id': 'skip_consistency_checks',
                'code': 'mysql_shell_consistency_mode',
                'message': 'Consistency checks require a consistent dump.',
            })
        enum_values = {
            'analyze_tables': {'off', 'on', 'histogram'},
            'defer_table_indexes': {'off', 'fulltext', 'all'},
            'handle_grant_errors': {'abort', 'drop_account', 'ignore'},
            'update_gtid_set': {'off', 'replace', 'append'},
        }
        for field, allowed in enum_values.items():
            if field in draft and draft[field] not in allowed:
                errors.append({
                    'field_id': field, 'code': 'invalid_mysql_shell_enum',
                    'message': 'MySQL Shell enumerated option is invalid.',
                })
        compression = draft.get('compression')
        if compression is not None and (
            not isinstance(compression, str) or re.fullmatch(
                r'(?:none|gzip(?:;level=[0-9])?|'
                r'zstd(?:;level=(?:[1-9]|1[0-9]|2[0-2]))?)',
                compression,
            ) is None
        ):
            errors.append({
                'field_id': 'compression',
                'code': 'invalid_mysql_shell_compression',
                'message': 'MySQL Shell compression is invalid.',
            })
        if draft.get('drop_existing_objects') and draft.get(
                'ignore_existing_objects'):
            errors.append({
                'field_id': 'ignore_existing_objects',
                'code': 'mysql_shell_existing_object_conflict',
                'message': 'Drop-existing and ignore-existing are mutually '
                           'exclusive.',
            })
        if operation == 'restore_logical':
            expected = route.get('database')
            if draft.get('confirmation') != expected:
                errors.append({
                    'field_id': 'confirmation',
                    'code': 'mysql_shell_restore_confirmation_mismatch',
                    'message': 'Restore confirmation must exactly match the '
                               'target database.',
                })
        return errors

    @classmethod
    def _validate_mysql_database_operation(cls, operation, draft, route):
        fields = {
            'analyze_tables': {
                'tables', 'no_write_to_binlog', 'histogram_action',
                'histogram_columns', 'histogram_buckets',
                'histogram_auto_update', 'histogram_data',
            },
            'check_tables': {'tables', 'check_options'},
            'optimize_tables': {'tables', 'no_write_to_binlog'},
            'repair_tables': {
                'tables', 'no_write_to_binlog', 'repair_options',
            },
            'checksum_tables': {'tables', 'checksum_type'},
        }[operation]
        errors = []
        if set(draft).difference(fields):
            errors.append({
                'field_id': None,
                'code': 'unknown_mysql_maintenance_option',
                'message': 'A MySQL maintenance option is unknown.',
            })
        database = (
            route.get('database') if isinstance(route, Mapping) else None
        )
        if not isinstance(database, str) or not database.strip():
            errors.append({
                'field_id': None,
                'code': 'mysql_maintenance_database_required',
                'message': 'MySQL maintenance requires a database target.',
            })
        tables = draft.get('tables')
        if (
            not isinstance(tables, list) or not 1 <= len(tables) <= 500 or
            not all(isinstance(item, str) and item and '\x00' not in item and
                    '.' not in item and len(item) <= 64 for item in tables)
        ):
            errors.append({
                'field_id': 'tables',
                'code': 'invalid_mysql_maintenance_tables',
                'message': (
                    'MySQL maintenance tables must be a JSON array of 1 to '
                    '500 unqualified native table names.'
                ),
            })
        if not isinstance(draft.get('no_write_to_binlog', False), bool):
            errors.append({
                'field_id': 'no_write_to_binlog',
                'code': 'invalid_mysql_no_write_to_binlog',
                'message': 'MySQL local maintenance must be true or false.',
            })
        options = {
            'check_options': {
                'QUICK', 'FAST', 'MEDIUM', 'EXTENDED', 'CHANGED',
                'FOR UPGRADE',
            },
            'repair_options': {'QUICK', 'EXTENDED', 'USE_FRM'},
        }
        for field_id, allowed in options.items():
            value = draft.get(field_id, [])
            if field_id not in fields:
                continue
            if not isinstance(value, list) or not all(
                    item in allowed for item in value):
                errors.append({
                    'field_id': field_id,
                    'code': f'invalid_mysql_{field_id}',
                    'message': (
                        f'MySQL {field_id.replace("_", " ")} is invalid.'
                    ),
                })
        if operation == 'checksum_tables' and draft.get(
                'checksum_type', 'DEFAULT') not in {
                    'DEFAULT', 'QUICK', 'EXTENDED'}:
            errors.append({
                'field_id': 'checksum_type',
                'code': 'invalid_mysql_checksum_type',
                'message': 'MySQL checksum type is invalid.',
            })
        if operation == 'analyze_tables':
            errors.extend(cls._validate_mysql_histogram(draft, tables))
        return errors

    @staticmethod
    def _validate_mysql_histogram(draft, tables):
        errors = []
        action = draft.get('histogram_action', 'NONE')
        if action not in {'NONE', 'UPDATE', 'DROP'}:
            errors.append({
                'field_id': 'histogram_action',
                'code': 'invalid_mysql_histogram_action',
                'message': 'MySQL histogram action is invalid.',
            })
        columns = draft.get('histogram_columns', [])
        if (
            not isinstance(columns, list) or not all(
                isinstance(item, str) and item and '\x00' not in item and
                '.' not in item and len(item) <= 64 for item in columns
            ) or (action != 'NONE' and not columns)
        ):
            errors.append({
                'field_id': 'histogram_columns',
                'code': 'invalid_mysql_histogram_columns',
                'message': 'MySQL histogram columns are invalid.',
            })
        if action != 'NONE' and isinstance(tables, list) and len(tables) != 1:
            errors.append({
                'field_id': 'tables',
                'code': 'mysql_histogram_requires_one_table',
                'message': 'MySQL histogram maintenance requires one table.',
            })
        buckets = draft.get('histogram_buckets', 100)
        if (
            isinstance(buckets, bool) or not isinstance(buckets, int) or
            not 1 <= buckets <= 1024
        ):
            errors.append({
                'field_id': 'histogram_buckets',
                'code': 'invalid_mysql_histogram_buckets',
                'message': 'MySQL histogram buckets must be from 1 to 1024.',
            })
        if not isinstance(draft.get('histogram_auto_update', False), bool):
            errors.append({
                'field_id': 'histogram_auto_update',
                'code': 'invalid_mysql_histogram_auto_update',
                'message': 'MySQL histogram auto-update must be boolean.',
            })
        histogram_data = draft.get('histogram_data')
        if histogram_data is not None and not isinstance(
                histogram_data, Mapping):
            errors.append({
                'field_id': 'histogram_data',
                'code': 'invalid_mysql_histogram_data',
                'message': 'MySQL histogram data must be a JSON object.',
            })
        return errors

    @classmethod
    def _validate_mariadb_database_operation(
            cls, operation, draft, route):
        fields = {
            'analyze_tables': {
                'tables', 'binlog_mode', 'persistent_for',
                'persistent_columns', 'persistent_indexes',
            },
            'check_objects': {'object_type', 'objects', 'check_options'},
            'optimize_tables': {
                'tables', 'binlog_mode', 'lock_wait_mode',
                'lock_wait_seconds',
            },
            'repair_objects': {
                'object_type', 'objects', 'binlog_mode', 'repair_options',
            },
            'checksum_tables': {'tables', 'checksum_type'},
        }[operation]
        errors = []
        if set(draft).difference(fields):
            errors.append({
                'field_id': None,
                'code': 'unknown_mariadb_maintenance_option',
                'message': 'A MariaDB maintenance option is unknown.',
            })
        database = (
            route.get('database') if isinstance(route, Mapping) else None
        )
        if not isinstance(database, str) or not database.strip():
            errors.append({
                'field_id': None,
                'code': 'mariadb_maintenance_database_required',
                'message': 'MariaDB maintenance requires a database target.',
            })
        names_field = (
            'objects' if operation in {'check_objects', 'repair_objects'}
            else 'tables'
        )
        names = draft.get(names_field)
        if not cls._valid_mariadb_name_list(names):
            errors.append({
                'field_id': names_field,
                'code': 'invalid_mariadb_maintenance_objects',
                'message': (
                    'MariaDB maintenance targets must be a JSON array of 1 '
                    'to 500 unqualified native names.'
                ),
            })
        binlog_mode = draft.get('binlog_mode', 'DEFAULT')
        if operation in {
                'analyze_tables', 'optimize_tables', 'repair_objects'} and (
                binlog_mode not in {
                    'DEFAULT', 'NO_WRITE_TO_BINLOG', 'LOCAL'}):
            errors.append({
                'field_id': 'binlog_mode',
                'code': 'invalid_mariadb_binlog_mode',
                'message': 'MariaDB binary-log mode is invalid.',
            })
        if operation == 'analyze_tables':
            persistent_for = draft.get('persistent_for', 'NONE')
            columns = draft.get('persistent_columns', [])
            indexes = draft.get('persistent_indexes', [])
            if persistent_for not in {'NONE', 'ALL', 'SPECIFIED'}:
                errors.append({
                    'field_id': 'persistent_for',
                    'code': 'invalid_mariadb_persistent_mode',
                    'message': (
                        'MariaDB persistent-statistics mode is invalid.'
                    ),
                })
            for field_id, value in (
                    ('persistent_columns', columns),
                    ('persistent_indexes', indexes)):
                if not cls._valid_mariadb_name_list(
                        value, minimum=0, allow_primary=(
                            field_id == 'persistent_indexes')):
                    errors.append({
                        'field_id': field_id,
                        'code': f'invalid_mariadb_{field_id}',
                        'message': (
                            'MariaDB persistent-statistics names are '
                            'invalid.'
                        ),
                    })
            if persistent_for != 'SPECIFIED' and (columns or indexes):
                errors.append({
                    'field_id': 'persistent_for',
                    'code': 'mariadb_persistent_names_without_specified',
                    'message': (
                        'Select specified persistent statistics before '
                        'choosing columns or indexes.'
                    ),
                })
        if operation == 'check_objects':
            object_type = draft.get('object_type', 'TABLE')
            options = draft.get('check_options', [])
            allowed = {
                'TABLE': {
                    'QUICK', 'FAST', 'MEDIUM', 'EXTENDED', 'CHANGED',
                    'FOR UPGRADE',
                },
                'VIEW': {'FOR UPGRADE'},
            }
            if object_type not in allowed:
                errors.append({
                    'field_id': 'object_type',
                    'code': 'invalid_mariadb_check_object_type',
                    'message': 'MariaDB check object type is invalid.',
                })
            elif not isinstance(options, list) or not all(
                    item in allowed[object_type] for item in options) or (
                    object_type == 'VIEW' and len(options) > 1):
                errors.append({
                    'field_id': 'check_options',
                    'code': 'invalid_mariadb_check_options',
                    'message': (
                        'MariaDB check options are invalid for this object '
                        'type.'
                    ),
                })
        if operation == 'repair_objects':
            object_type = draft.get('object_type', 'TABLE')
            options = draft.get('repair_options', [])
            allowed = {
                'TABLE': {'QUICK', 'EXTENDED', 'USE_FRM', 'FORCE'},
                'VIEW': {'FOR UPGRADE', 'FROM MYSQL'},
            }
            if object_type not in allowed:
                errors.append({
                    'field_id': 'object_type',
                    'code': 'invalid_mariadb_repair_object_type',
                    'message': 'MariaDB repair object type is invalid.',
                })
            elif not isinstance(options, list) or not all(
                    item in allowed[object_type] for item in options) or (
                    object_type == 'VIEW' and len(options) > 1):
                errors.append({
                    'field_id': 'repair_options',
                    'code': 'invalid_mariadb_repair_options',
                    'message': (
                        'MariaDB repair options are invalid for this object '
                        'type.'
                    ),
                })
        if operation == 'optimize_tables':
            wait_mode = draft.get('lock_wait_mode', 'DEFAULT')
            wait_seconds = draft.get('lock_wait_seconds', 0)
            if wait_mode not in {'DEFAULT', 'WAIT', 'NOWAIT'}:
                errors.append({
                    'field_id': 'lock_wait_mode',
                    'code': 'invalid_mariadb_lock_wait_mode',
                    'message': 'MariaDB metadata-lock wait mode is invalid.',
                })
            if (
                isinstance(wait_seconds, bool) or
                not isinstance(wait_seconds, int) or
                not 0 <= wait_seconds <= 4294967295
            ):
                errors.append({
                    'field_id': 'lock_wait_seconds',
                    'code': 'invalid_mariadb_lock_wait_seconds',
                    'message': (
                        'MariaDB metadata-lock wait must be an unsigned '
                        '32-bit number of seconds.'
                    ),
                })
            elif wait_mode != 'WAIT' and wait_seconds != 0:
                errors.append({
                    'field_id': 'lock_wait_seconds',
                    'code': 'mariadb_lock_wait_seconds_without_wait',
                    'message': 'Lock-wait seconds require WAIT mode.',
                })
        if operation == 'checksum_tables' and draft.get(
                'checksum_type', 'DEFAULT') not in {
                    'DEFAULT', 'QUICK', 'EXTENDED'}:
            errors.append({
                'field_id': 'checksum_type',
                'code': 'invalid_mariadb_checksum_type',
                'message': 'MariaDB checksum type is invalid.',
            })
        return errors

    @staticmethod
    def _valid_mariadb_name_list(
            value, *, minimum=1, allow_primary=False):
        return (
            isinstance(value, list) and minimum <= len(value) <= 500 and
            all(
                isinstance(item, str) and item and '\x00' not in item and
                '.' not in item and len(item) <= 64 and
                (allow_primary or item.upper() != 'PRIMARY')
                for item in value
            )
        )

    @staticmethod
    def _validate_mariadb_tool_operation(operation, draft, route):
        backup_fields = {
            'path', 'include_schema', 'include_data',
            'single_transaction', 'lock_all_tables', 'add_drop_database',
            'add_drop_table', 'routines', 'events', 'triggers',
            'dump_history', 'as_of', 'hex_blob', 'order_by_primary',
            'extended_insert', 'complete_insert', 'comments', 'dump_date',
            'default_character_set', 'tz_utc', 'flush_logs',
            'replication_position', 'gtid', 'compress_connection',
            'max_allowed_packet',
        }
        restore_fields = {
            'path', 'confirmation', 'abort_on_error', 'binary_mode',
            'default_character_set', 'compress_connection',
            'max_allowed_packet', 'show_warnings', 'dry_run',
        }
        fields = backup_fields if operation == 'backup_logical' else (
            restore_fields
        )
        errors = []
        if set(draft).difference(fields):
            errors.append({
                'field_id': None,
                'code': 'unknown_mariadb_tool_option',
                'message': 'A MariaDB backup or restore option is unknown.',
            })
        database = (
            route.get('database') if isinstance(route, Mapping) else None
        )
        if not isinstance(database, str) or not database.strip():
            errors.append({
                'field_id': None,
                'code': 'mariadb_tool_database_required',
                'message': 'MariaDB backup and restore require a database.',
            })
        workspace = (
            route.get('tool_workspace')
            if isinstance(route, Mapping) else None
        )
        if not isinstance(workspace, str) or not os.path.isabs(workspace) or (
                os.path.realpath(workspace) in {
                    '/', os.path.realpath(os.path.expanduser('~'))}):
            errors.append({
                'field_id': None,
                'code': 'mariadb_tool_workspace_required',
                'message': (
                    'MariaDB backup and restore require a safe absolute tool '
                    'workspace.'
                ),
            })
        path = draft.get('path')
        if not isinstance(path, str) or not path.strip() or '\x00' in path:
            errors.append({
                'field_id': 'path',
                'code': 'invalid_mariadb_tool_path',
                'message': 'MariaDB backup or restore path is invalid.',
            })
        boolean_fields = (
            backup_fields.difference({
                'path', 'as_of', 'default_character_set',
                'replication_position', 'max_allowed_packet',
            }) if operation == 'backup_logical' else
            restore_fields.difference({
                'path', 'confirmation', 'default_character_set',
                'max_allowed_packet',
            })
        )
        for field_id in boolean_fields:
            if field_id in draft and not isinstance(draft[field_id], bool):
                errors.append({
                    'field_id': field_id,
                    'code': 'invalid_mariadb_tool_boolean',
                    'message': 'MariaDB tool switches must be true or false.',
                })
        character_set = draft.get('default_character_set', 'utf8mb4')
        if not isinstance(character_set, str) or re.fullmatch(
                r'[A-Za-z0-9_$-]{1,64}', character_set) is None:
            errors.append({
                'field_id': 'default_character_set',
                'code': 'invalid_mariadb_tool_character_set',
                'message': 'MariaDB tool character set is invalid.',
            })
        packet = draft.get('max_allowed_packet', 1073741824)
        if isinstance(packet, bool) or not isinstance(packet, int) or not (
                4096 <= packet <= 1073741824):
            errors.append({
                'field_id': 'max_allowed_packet',
                'code': 'invalid_mariadb_tool_packet_size',
                'message': 'MariaDB maximum packet size is invalid.',
            })
        if operation == 'backup_logical':
            if draft.get('include_schema', True) is False and draft.get(
                    'include_data', True) is False:
                errors.append({
                    'field_id': 'include_data',
                    'code': 'empty_mariadb_backup',
                    'message': 'A MariaDB backup must include schema or data.',
                })
            if draft.get('single_transaction', True) and draft.get(
                    'lock_all_tables', False):
                errors.append({
                    'field_id': 'lock_all_tables',
                    'code': 'mariadb_backup_lock_mode_conflict',
                    'message': (
                        'Single-transaction and global-lock backup modes are '
                        'mutually exclusive.'
                    ),
                })
            as_of = draft.get('as_of')
            if as_of not in {None, ''} and (
                    not isinstance(as_of, str) or '\x00' in as_of or
                    len(as_of) > 128):
                errors.append({
                    'field_id': 'as_of',
                    'code': 'invalid_mariadb_backup_as_of',
                    'message': 'MariaDB backup AS OF value is invalid.',
                })
            if as_of not in {None, ''} and draft.get('dump_history', False):
                errors.append({
                    'field_id': 'dump_history',
                    'code': 'mariadb_backup_history_mode_conflict',
                    'message': 'AS OF and full history cannot be combined.',
                })
            position = draft.get('replication_position', 'NONE')
            if position not in {'NONE', 'COMMENTED', 'EXECUTABLE'}:
                errors.append({
                    'field_id': 'replication_position',
                    'code': 'invalid_mariadb_replication_position',
                    'message': 'MariaDB replication position mode is invalid.',
                })
            if draft.get('gtid', False) and position == 'NONE':
                errors.append({
                    'field_id': 'gtid',
                    'code': 'mariadb_gtid_requires_replication_position',
                    'message': 'GTID output requires replication coordinates.',
                })
        elif draft.get('confirmation') != database:
            errors.append({
                'field_id': 'confirmation',
                'code': 'mariadb_restore_confirmation_mismatch',
                'message': (
                    'Restore confirmation must exactly match the target '
                    'database.'
                ),
            })
        return errors

    @staticmethod
    def _validate_mysql_database(operation, draft):
        fields = (
            {'name', 'if_not_exists', 'character_set', 'collation',
             'encryption'}
            if operation == 'create' else
            {'character_set', 'collation', 'encryption', 'read_only'}
        )
        errors = []
        if set(draft).difference(fields):
            errors.append({
                'field_id': None,
                'code': 'unknown_mysql_database_option',
                'message': 'A MySQL database option is unknown.',
            })
        identifier = re.compile(r'^[A-Za-z0-9_$-]{1,64}$')
        for field_id in ('character_set', 'collation'):
            value = draft.get(field_id)
            if value not in {None, ''} and (
                    not isinstance(value, str) or
                    identifier.fullmatch(value) is None):
                errors.append({
                    'field_id': field_id,
                    'code': f'invalid_mysql_database_{field_id}',
                    'message': (
                        f'MySQL database {field_id.replace("_", " ")} '
                        'is invalid.'
                    ),
                })
        if draft.get('encryption') not in {None, '', 'Y', 'N'}:
            errors.append({
                'field_id': 'encryption',
                'code': 'invalid_mysql_database_encryption',
                'message': 'MySQL default encryption must be Y or N.',
            })
        if operation == 'create' and not isinstance(
                draft.get('if_not_exists', False), bool):
            errors.append({
                'field_id': 'if_not_exists',
                'code': 'invalid_mysql_database_if_not_exists',
                'message': 'MySQL IF NOT EXISTS must be true or false.',
            })
        if operation == 'alter':
            if draft.get('read_only') not in {None, '', 'ON', 'OFF'}:
                errors.append({
                    'field_id': 'read_only',
                    'code': 'invalid_mysql_database_read_only',
                    'message': 'MySQL read-only state must be ON or OFF.',
                })
            if not any(draft.get(field_id) not in {None, ''} for field_id in (
                    'character_set', 'collation', 'encryption',
                    'read_only')):
                errors.append({
                    'field_id': None,
                    'code': 'mysql_database_change_required',
                    'message': 'Select at least one MySQL database change.',
                })
        return errors

    @staticmethod
    def _validate_mariadb_database(operation, draft):
        fields = (
            {'name', 'or_replace', 'if_not_exists', 'character_set',
             'collation', 'comment'}
            if operation == 'create' else
            {'character_set', 'collation', 'comment'}
        )
        errors = []
        if set(draft).difference(fields):
            errors.append({
                'field_id': None,
                'code': 'unknown_mariadb_database_option',
                'message': 'A MariaDB database option is unknown.',
            })
        identifier = re.compile(r'^[A-Za-z0-9_$-]{1,64}$')
        for field_id in ('character_set', 'collation'):
            value = draft.get(field_id)
            if value not in {None, ''} and (
                    not isinstance(value, str) or
                    identifier.fullmatch(value) is None):
                errors.append({
                    'field_id': field_id,
                    'code': f'invalid_mariadb_database_{field_id}',
                    'message': (
                        f'MariaDB database {field_id.replace("_", " ")} '
                        'is invalid.'
                    ),
                })
        comment = draft.get('comment')
        if comment is not None and (
                not isinstance(comment, str) or '\x00' in comment or
                len(comment) > 1024):
            errors.append({
                'field_id': 'comment',
                'code': 'invalid_mariadb_database_comment',
                'message': (
                    'MariaDB database comments are limited to 1024 '
                    'characters and cannot contain NUL.'
                ),
            })
        if operation == 'create':
            for field_id in ('or_replace', 'if_not_exists'):
                if not isinstance(draft.get(field_id, False), bool):
                    errors.append({
                        'field_id': field_id,
                        'code': 'invalid_mariadb_database_create_mode',
                        'message': (
                            'MariaDB database creation modes must be boolean.'
                        ),
                    })
            if draft.get('or_replace') and draft.get('if_not_exists'):
                errors.append({
                    'field_id': 'if_not_exists',
                    'code': 'mariadb_database_create_mode_conflict',
                    'message': (
                        'MariaDB OR REPLACE and IF NOT EXISTS cannot be used '
                        'together.'
                    ),
                })
        elif not any(
            field_id in draft and draft[field_id] is not None
            for field_id in ('character_set', 'collation', 'comment')
        ):
            errors.append({
                'field_id': None,
                'code': 'mariadb_database_change_required',
                'message': 'Select at least one MariaDB database change.',
            })
        return errors

    @classmethod
    def _validate_duckdb_database_operation(cls, operation, draft, route):
        fields = {
            'checkpoint': set(), 'force_checkpoint': set(),
            'vacuum': set(), 'analyze': set(),
            'export_database': {'directory', 'format'},
            'import_database': {'directory'},
        }[operation]
        errors = []
        if set(draft).difference(fields):
            errors.append({
                'field_id': None,
                'code': 'unknown_duckdb_database_operation_option',
                'message': 'A DuckDB database operation option is unknown.',
            })
        if operation in {'export_database', 'import_database'}:
            directory = draft.get('directory')
            try:
                cls._duckdb_operation_directory(
                    route, directory, importing=operation == 'import_database'
                )
            except RelationalClientError as exc:
                errors.append({
                    'field_id': 'directory',
                    'code': 'invalid_duckdb_database_directory',
                    'message': str(exc),
                })
        if operation == 'export_database' and draft.get(
                'format', 'PARQUET') not in {'CSV', 'PARQUET'}:
            errors.append({
                'field_id': 'format',
                'code': 'invalid_duckdb_export_format',
                'message': 'DuckDB export format must be CSV or PARQUET.',
            })
        return errors

    @staticmethod
    def _duckdb_operation_directory(route, value, importing=False):
        if (
            not isinstance(value, str) or not value.strip() or
            not os.path.isabs(value.strip())
        ):
            raise RelationalClientError(
                'DuckDB import/export directory must be absolute.'
            )
        root = route.get('filesystem_root') if isinstance(
            route, Mapping
        ) else None
        if not isinstance(root, str) or not os.path.isabs(root):
            raise RelationalClientError(
                'DuckDB endpoint has no absolute filesystem root.'
            )
        root_path = os.path.realpath(root)
        directory = os.path.realpath(value.strip())
        try:
            contained = os.path.commonpath(
                (root_path, directory)
            ) == root_path
        except ValueError:
            contained = False
        if not contained:
            raise RelationalClientError(
                'DuckDB import/export directory escapes the endpoint root.'
            )
        if importing:
            if not os.path.isdir(directory):
                raise RelationalClientError(
                    'DuckDB import directory does not exist.'
                )
            if not all(
                os.path.isfile(os.path.join(directory, name))
                for name in ('schema.sql', 'load.sql')
            ):
                raise RelationalClientError(
                    'DuckDB import directory lacks schema.sql or load.sql.'
                )
        else:
            parent = os.path.dirname(directory)
            if not os.path.isdir(parent):
                raise RelationalClientError(
                    'DuckDB export parent directory does not exist.'
                )
            if os.path.lexists(directory) and (
                    not os.path.isdir(directory) or os.listdir(directory)):
                raise RelationalClientError(
                    'DuckDB export directory must be absent or empty.'
                )
        return directory

    @staticmethod
    def _validate_sqlite_database_operation(operation, draft, route):
        fields = {
            'backup': {'backup_path', 'overwrite'},
            'restore': {'backup_path', 'confirmation'},
            'integrity_check': {'max_errors'},
            'quick_check': {'max_errors'},
            'foreign_key_check': {'table'},
            'vacuum': set(),
            'incremental_vacuum': {'pages'},
            'optimize': set(),
            'analyze': {'target'},
            'reindex': {'target'},
            'wal_checkpoint': {'mode'},
        }[operation]
        errors = []
        unknown = set(draft).difference(fields)
        if unknown:
            errors.append({
                'field_id': None,
                'code': 'unknown_sqlite_database_operation_option',
                'message': 'A SQLite database operation option is unknown.',
            })
        if operation in {'backup', 'restore'}:
            path = draft.get('backup_path')
            if not isinstance(path, str) or not path.strip() or not (
                    os.path.isabs(path.strip())):
                errors.append({
                    'field_id': 'backup_path',
                    'code': 'invalid_sqlite_backup_path',
                    'message': 'SQLite backup filename must be absolute.',
                })
            if operation == 'backup' and not isinstance(
                    draft.get('overwrite', False), bool):
                errors.append({
                    'field_id': 'overwrite',
                    'code': 'invalid_sqlite_backup_overwrite',
                    'message': (
                        'SQLite backup overwrite must be true or false.'
                    ),
                })
            if operation == 'restore' and draft.get('confirmation') != (
                    route.get('database')):
                errors.append({
                    'field_id': 'confirmation',
                    'code': 'sqlite_restore_confirmation_mismatch',
                    'message': (
                        'SQLite restore confirmation must exactly match the '
                        'active database path.'
                    ),
                })
        if operation in {'integrity_check', 'quick_check'}:
            limit = draft.get('max_errors', 100)
            if isinstance(limit, bool) or not isinstance(limit, int) or not (
                    1 <= limit <= 10000):
                errors.append({
                    'field_id': 'max_errors',
                    'code': 'invalid_sqlite_check_limit',
                    'message': 'SQLite check limit must be from 1 to 10000.',
                })
        if operation == 'incremental_vacuum':
            pages = draft.get('pages', 0)
            if isinstance(pages, bool) or not isinstance(pages, int) or not (
                    0 <= pages <= 2147483647):
                errors.append({
                    'field_id': 'pages',
                    'code': 'invalid_sqlite_incremental_vacuum_pages',
                    'message': 'SQLite vacuum page count is invalid.',
                })
        for field_id in ('table', 'target'):
            name = draft.get(field_id)
            if name not in {None, ''} and (
                    not isinstance(name, str) or any(
                        not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', part)
                        for part in name.split('.')
                    )):
                errors.append({
                    'field_id': field_id,
                    'code': 'invalid_sqlite_qualified_name',
                    'message': 'SQLite object name is invalid.',
                })
        if operation == 'wal_checkpoint' and draft.get(
                'mode', 'PASSIVE') not in {
                    'PASSIVE', 'FULL', 'RESTART', 'TRUNCATE'}:
            errors.append({
                'field_id': 'mode',
                'code': 'invalid_sqlite_checkpoint_mode',
                'message': 'SQLite checkpoint mode is invalid.',
            })
        return errors

    @staticmethod
    def _validate_sqlite_database_create(draft):
        errors = []
        page_size = draft.get('page_size', '4096')
        if str(page_size) not in {
            '512', '1024', '2048', '4096', '8192', '16384', '32768',
            '65536',
        }:
            errors.append({
                'field_id': 'page_size',
                'code': 'invalid_sqlite_page_size',
                'message': 'SQLite page size is invalid.',
            })
        if draft.get('encoding', 'UTF-8') not in {
            'UTF-8', 'UTF-16', 'UTF-16le', 'UTF-16be',
        }:
            errors.append({
                'field_id': 'encoding',
                'code': 'invalid_sqlite_encoding',
                'message': 'SQLite database encoding is invalid.',
            })
        if draft.get('auto_vacuum', 'NONE') not in {
            'NONE', 'FULL', 'INCREMENTAL',
        }:
            errors.append({
                'field_id': 'auto_vacuum',
                'code': 'invalid_sqlite_auto_vacuum',
                'message': 'SQLite auto-vacuum mode is invalid.',
            })
        errors.extend(RelationalAdministration._sqlite_integer_errors(draft))
        return errors

    @staticmethod
    def _validate_sqlite_database_alter(draft):
        errors = []
        supplied = {
            key for key in (
                'journal_mode', 'synchronous', 'auto_vacuum', 'page_size',
                'application_id', 'user_version',
            ) if draft.get(key) not in {None, ''}
        }
        if not supplied:
            errors.append({
                'field_id': None,
                'code': 'sqlite_database_change_required',
                'message': 'Select at least one SQLite database setting.',
            })
        admitted = supplied.union({'run_vacuum'})
        unknown = set(draft).difference(admitted)
        if unknown:
            errors.append({
                'field_id': None,
                'code': 'unknown_sqlite_database_change',
                'message': 'The SQLite database setting is unsupported.',
            })
        if draft.get('journal_mode') not in {
            None, '', 'DELETE', 'TRUNCATE', 'PERSIST', 'MEMORY', 'WAL', 'OFF',
        }:
            errors.append({
                'field_id': 'journal_mode',
                'code': 'invalid_sqlite_journal_mode',
                'message': 'SQLite journal mode is invalid.',
            })
        if draft.get('synchronous') not in {
            None, '', 'OFF', 'NORMAL', 'FULL', 'EXTRA',
        }:
            errors.append({
                'field_id': 'synchronous',
                'code': 'invalid_sqlite_synchronous',
                'message': 'SQLite synchronous mode is invalid.',
            })
        if draft.get('auto_vacuum') not in {
            None, '', 'NONE', 'FULL', 'INCREMENTAL',
        }:
            errors.append({
                'field_id': 'auto_vacuum',
                'code': 'invalid_sqlite_auto_vacuum',
                'message': 'SQLite auto-vacuum mode is invalid.',
            })
        page_size = draft.get('page_size')
        if page_size not in {None, ''} and str(page_size) not in {
            '512', '1024', '2048', '4096', '8192', '16384', '32768',
            '65536',
        }:
            errors.append({
                'field_id': 'page_size',
                'code': 'invalid_sqlite_page_size',
                'message': 'SQLite page size is invalid.',
            })
        if {'page_size', 'auto_vacuum'}.intersection(supplied) and (
                draft.get('run_vacuum') is not True):
            errors.append({
                'field_id': 'run_vacuum',
                'code': 'sqlite_vacuum_required',
                'message': (
                    'Changing SQLite page size or auto-vacuum mode requires '
                    'a database rebuild with VACUUM.'
                ),
            })
        if page_size not in {None, ''} and draft.get('journal_mode') in {
                None, '', 'WAL'}:
            errors.append({
                'field_id': 'journal_mode',
                'code': 'sqlite_page_size_requires_rollback_journal',
                'message': (
                    'Changing SQLite page size requires selecting a '
                    'non-WAL journal mode for the rebuild.'
                ),
            })
        errors.extend(RelationalAdministration._sqlite_integer_errors(draft))
        return errors

    @staticmethod
    def _sqlite_integer_errors(draft):
        errors = []
        for field_id in ('application_id', 'user_version'):
            value = draft.get(field_id)
            if value in {None, ''}:
                continue
            if isinstance(value, bool) or not isinstance(value, int) or not (
                    -2147483648 <= value <= 2147483647):
                errors.append({
                    'field_id': field_id,
                    'code': f'invalid_sqlite_{field_id}',
                    'message': (
                        f'SQLite {field_id.replace("_", " ")} must be a '
                        'signed 32-bit integer.'
                    ),
                })
        return errors

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
                **({'backup_selection': copy.deepcopy(
                    compiled['backup_selection'])}
                   if 'backup_selection' in compiled else {}),
                **({'backup_io_requested': compiled['backup_io_requested']}
                   if 'backup_io_requested' in compiled else {}),
                **({'restore_policy_requested': copy.deepcopy(
                    compiled['restore_policy_requested'])}
                   if 'restore_policy_requested' in compiled else {}),
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

    def apply(self, client, request, connection=None):
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
        if driver_operation == 'firebird-service':
            observation = client.run_server_operation(
                {'route': route}, compiled['operation_id'],
                compiled['database'], compiled.get('options', {}),
            )
            return {
                'accepted': True,
                'commit_requested': False,
                'rollback_requested': False,
                'driver_observation_only': True,
                'driver_observation': observation,
                'transaction_finality_interpreted_by_common_code': False,
            }
        if driver_operation == 'firebird-drop-database':
            observation = client.drop_database(
                {'route': route}, compiled['database'], driver_operation
            )
            response = {
                'accepted': True,
                'commit_requested': False,
                'rollback_requested': False,
                'driver_observation_only': True,
                'driver_observation': observation,
                'transaction_finality_interpreted_by_common_code': False,
            }
            if isinstance(compiled.get('database_target'), Mapping):
                response['dropped_endpoint_database_target'] = copy.deepcopy(
                    compiled['database_target']
                )
            return response
        if driver_operation == 'embedded-drop-database':
            observation = client.drop_database(
                {'route': route}, compiled['database'], driver_operation
            )
            return {
                'accepted': True,
                'commit_requested': False,
                'rollback_requested': False,
                'driver_observation_only': True,
                'driver_observation': observation,
                'dropped_endpoint_database_target': copy.deepcopy(
                    compiled['database_target']
                ),
                'transaction_finality_interpreted_by_common_code': False,
            }
        if driver_operation in {
                'embedded-sqlite-backup', 'embedded-sqlite-restore'}:
            owns_connection = connection is None
            operation_connection = connection or client._connect({
                'route': route
            })
            try:
                observation = client.run_database_operation(
                    operation_connection, route,
                    compiled['operation_id'], compiled.get('options', {}),
                )
            finally:
                if owns_connection:
                    client._forget_and_close(operation_connection)
            return {
                'accepted': True,
                'commit_requested': False,
                'rollback_requested': False,
                'driver_observation_only': True,
                'driver_observation': observation,
                'transaction_finality_interpreted_by_common_code': False,
            }
        if driver_operation in {'mysql-shell', 'mariadb-tools'}:
            observation = client.run_database_operation(
                connection, route, compiled['operation_id'],
                compiled.get('options', {}),
            )
            return {
                'accepted': True,
                'commit_requested': False,
                'rollback_requested': False,
                'driver_observation_only': True,
                'driver_observation': observation,
                'transaction_finality_interpreted_by_common_code': False,
            }
        if driver_operation:
            observation = client.create_database(
                {
                    'route': route,
                    'create_options': copy.deepcopy(
                        compiled.get('create_options', {})
                    ),
                }, compiled['database'], driver_operation
            )
            return {
                'accepted': True,
                'commit_requested': False,
                'rollback_requested': False,
                'driver_observation_only': True,
                'driver_observation': observation,
                'endpoint_database_target': {
                    'database': compiled['endpoint_database'],
                    'display_name': compiled[
                        'endpoint_database'
                    ].rsplit('/', 1)[-1],
                },
                'transaction_finality_interpreted_by_common_code': False,
            }
        owns_connection = connection is None
        borrowed_firebird = (not owns_connection and
                             self.dialect.engine_id == 'firebird')
        connection = connection or client._connect({'route': route})
        cursor = None
        task_savepoint = None
        commit_requested = False
        rollback_requested = False
        results = []
        identity_change = None
        try:
            cursor = (
                connection if getattr(
                    getattr(client, 'config', None),
                    'execute_on_connection', False,
                )
                else connection.cursor()
            )
            if borrowed_firebird:
                task_savepoint = NativeTaskSavepoint(cursor)
                task_savepoint.begin()
            transition = (compiled.get('firebird_column_rename') or
                          compiled.get('firebird_domain_rename')) if (
                self.dialect.engine_id == 'firebird') else None
            previous_identity = firebird_identity.rename_identity(
                cursor, transition, transition['old_name']
            ) if transition else None
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
            if transition:
                identity_change = firebird_identity.verify_rename(
                    cursor, transition, previous_identity)
            if task_savepoint is not None:
                task_savepoint.release()
            if owns_connection:
                commit = getattr(connection, 'commit', None)
                if callable(commit):
                    commit_requested = True
                    commit()
        except RelationalClientError as exc:
            if borrowed_firebird:
                try:
                    if task_savepoint is not None:
                        task_savepoint.rollback()
                except Exception:
                    raise RelationalClientError(
                        str(exc) + '; Firebird task rollback could not be '
                        'verified; the transaction remains caller-owned'
                    ) from None
            else:
                rollback = getattr(connection, 'rollback', None)
                if callable(rollback):
                    rollback_requested = True
                    rollback()
            raise
        except Exception as exc:
            task_rollback_failed = False
            if borrowed_firebird:
                try:
                    if task_savepoint is not None:
                        task_savepoint.rollback()
                except Exception:
                    task_rollback_failed = True
            else:
                rollback = getattr(connection, 'rollback', None)
                if callable(rollback):
                    rollback_requested = True
                    try:
                        rollback()
                    except Exception:
                        pass
            codes = firebird_status_codes(exc) if (
                self.dialect.engine_id == 'firebird') else ()
            detail = ('; Firebird status codes: ' + ', '.join(map(str, codes))
                      if codes else '')
            if task_rollback_failed:
                detail += ('; Firebird task rollback could not be verified; '
                           'the transaction remains caller-owned')
            raise RelationalClientError(
                'relational administration execution failed '
                f'({type(exc).__name__}){detail}'
            ) from None
        finally:
            if cursor is not None and cursor is not connection:
                client._safe_close(cursor)
            if owns_connection:
                client._forget_and_close(connection)
        response = {
            'accepted': True,
            'statement_results': results,
            'commit_requested': commit_requested,
            'rollback_requested': rollback_requested,
            'driver_observation_only': True,
            'transaction_finality_interpreted_by_common_code': False,
            'staged_in_provider_session': not owns_connection,
        }
        if identity_change is not None:
            response['resource_identity_change'] = {
                **identity_change,
                'committed_by_provider': owns_connection and commit_requested,
                'staged_in_provider_session': not owns_connection,
            }
        if task_savepoint is not None:
            response['native_task_scope'] = {
                'kind': 'firebird_savepoint', 'released': True,
                'transaction_owner': 'caller',
                'driver_observation_only': True,
                'covers_nontransactional_effects': False,
            }
        if isinstance(compiled.get('database_target'), Mapping):
            response['dropped_endpoint_database_target'] = copy.deepcopy(
                compiled['database_target']
            )
        created_database = compiled.get('endpoint_database')
        if isinstance(created_database, str) and created_database:
            response['endpoint_database_target'] = {
                'database': created_database,
                'display_name': created_database.rsplit('/', 1)[-1],
            }
        return response

    def read_rows(self, client, request, connection=None):
        route = request.get('_provider_route')
        target = request.get('target_resource')
        if not isinstance(route, Mapping) or not isinstance(target, Mapping):
            raise RelationalClientError(
                'row paging requires a trusted route and table resource'
            )
        resource_kind = target.get('resource_kind')
        if resource_kind not in {'table', 'view', 'materialized-view'}:
            raise RelationalClientError(
                'row paging requires a relation that supports row reads'
            )
        limit = request.get('limit', 200)
        if isinstance(limit, bool) or not isinstance(limit, int):
            raise RelationalClientError('row page limit must be an integer')
        limit = max(1, min(limit, 500))
        path = self._target_path(target)
        fingerprint = self._route_fingerprint(route)
        offset = 0
        continuation = request.get('continuation')
        if continuation is not None:
            if not isinstance(continuation, str) or not continuation:
                raise RelationalClientError(
                    'row continuation token is invalid'
                )
            with self._identity_lock:
                retained = self._row_continuations.pop(
                    continuation, None
                )
            if retained is None or (
                    retained.route_fingerprint != fingerprint or
                    retained.target_path != path or
                    retained.limit != limit):
                raise RelationalClientError(
                    'row continuation token is unavailable or mismatched'
                )
            offset = retained.offset
        owns_connection = connection is None
        connection = connection or client._connect({'route': route})
        cursor = None
        try:
            # Views are browsable relations, but CDEadmin must not infer that
            # they are updatable or manufacture row identities for them.
            # Provider-native view mutation belongs to a separate admitted
            # operation contract.
            key_columns = (
                tuple(self._primary_key(connection, path))
                if resource_kind == 'table' else ()
            )
            cursor = (
                connection if getattr(
                    getattr(client, 'config', None),
                    'execute_on_connection', False,
                )
                else connection.cursor()
            )
            order = (
                ' ORDER BY ' + ', '.join(
                    self._quote(name) for name in key_columns
                ) if key_columns else ''
            )
            if self.dialect.engine_id == 'firebird':
                source = (
                    f'SELECT FIRST {limit + 1} SKIP {offset} * FROM '
                    f'{self._qualified(path)}{order}'
                )
            else:
                source = (
                    f'SELECT * FROM {self._qualified(path)}{order} '
                    f'LIMIT {limit + 1} OFFSET {offset}'
                )
            cursor.execute(source)
            description = getattr(cursor, 'description', None) or ()
            columns = tuple(str(item[0]) for item in description)
            native_types = tuple(
                None if len(item) < 2 else str(item[1])
                for item in description
            )
            raw_rows = list(cursor.fetchall())
            has_more = len(raw_rows) > limit
            raw_rows = raw_rows[:limit]
            result_rows = []
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
            next_continuation = None
            if has_more:
                next_continuation = str(uuid.uuid4())
                retained = _RowContinuation(
                    fingerprint, path, offset + limit, limit,
                    time.monotonic(),
                )
                with self._identity_lock:
                    while len(self._row_continuations) >= 1000:
                        oldest = next(iter(self._row_continuations))
                        self._row_continuations.pop(oldest, None)
                    self._row_continuations[next_continuation] = retained
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
                    if key_columns else
                    'read-only-view' if resource_kind != 'table' else
                    'read-only-no-primary-key'
                ),
                'limit': limit,
                'continuation': next_continuation,
                'complete': not has_more,
                'transaction_finality_interpreted_by_common_code': False,
            }
        except RelationalClientError:
            raise
        except Exception as exc:
            raise RelationalClientError(
                f'relational row paging failed ({type(exc).__name__})'
            ) from None
        finally:
            if cursor is not None and cursor is not connection:
                client._safe_close(cursor)
            if owns_connection:
                client._forget_and_close(connection)

    def cancel_rows(self, request):
        """Release one provider-issued relational row continuation."""
        route = request.get('_provider_route')
        token = request.get('continuation')
        if not isinstance(route, Mapping) or not isinstance(token, str) or (
                not token):
            raise RelationalClientError(
                'row cursor cancellation request is invalid'
            )
        fingerprint = self._route_fingerprint(route)
        with self._identity_lock:
            retained = self._row_continuations.get(token)
            if retained is None or retained.route_fingerprint != fingerprint:
                return {'cancelled': False, 'continuation': token}
            self._row_continuations.pop(token, None)
        return {
            'cancelled': True,
            'continuation': token,
            'provider_cursor_released': True,
        }

    def _compile(self, request):
        operation = request['operation_id']
        if (self.dialect.engine_id == 'firebird' and
                request['resource_kind'] == 'column' and
                operation == 'create' and 'column_mode' in request['draft']):
            return {'statements': [{
                'source': firebird_columns.compile_create(request['draft']),
                'parameters': ()}]}
        if (self.dialect.engine_id == 'firebird' and
                request['resource_kind'] == 'column' and
                operation in {'alter', 'comment'}):
            firebird_columns.validate_target(
                operation, request['draft'], request.get('target_resource'))
            sql = firebird_columns.compile_column(
                operation, request['draft'],
                self._target_path(request.get('target_resource')))
            return {'statements': [{'source': sql, 'parameters': ()}],
                    'warnings': [
                        'Firebird identity generator state is not ordinary '
                        'transactional row data. Changing an increment can '
                        'affect subsequent generated values even after '
                        'rollback; review concurrent inserts before applying.'
                    ] if request['draft'].get('action') == 'IDENTITY' else []}
        if (self.dialect.engine_id == 'firebird' and
                request['resource_kind'] in firebird_mappings.KINDS and
                operation != 'inspect'):
            statements = firebird_mappings.compile_mapping(
                request['resource_kind'], operation, request['draft'],
                request.get('target_resource'))
            return {
                'statements': [{'source': sql, 'parameters': ()}
                               for sql in statements],
                'warnings': ['Mapping changes affect authentication on new '
                             'attachments. Verify native access separately.'],
            }
        if (self.dialect.engine_id == 'firebird' and
                request['resource_kind'] == 'role' and
                operation == 'configure_admin_mapping'):
            target = request.get('target_resource') or {}
            if target.get('display_name') != 'RDB$ADMIN':
                raise RelationalClientError(
                    'AUTO ADMIN MAPPING applies only to RDB$ADMIN')
            action = request.get('draft', {}).get('mapping_action')
            if action not in ('SET', 'DROP'):
                raise RelationalClientError('Choose SET or DROP admin mapping')
            return {'statements': [{
                'source': (f'ALTER ROLE "RDB$ADMIN" '
                           f'{action} AUTO ADMIN MAPPING'),
                'parameters': (),
            }]}
        if (
            self.dialect.engine_id == 'mariadb' and
            request['resource_kind'] == 'system-variable' and
            operation == 'set_global'
        ):
            target = request.get('target_resource') or {}
            name = self._identifier(target.get('display_name'))
            draft = request.get('draft', {})
            if draft.get('value_mode') == 'DEFAULT':
                value = 'DEFAULT'
            else:
                raw_value = draft.get('value')
                variable_type = str(self._mariadb_target_native(
                    target
                ).get('variable_type', '')).upper()
                if variable_type in {
                        'INT', 'INT UNSIGNED', 'BIGINT UNSIGNED'}:
                    value = str(int(str(raw_value), 10))
                elif variable_type == 'DOUBLE':
                    value = str(float(str(raw_value)))
                elif variable_type == 'BOOLEAN':
                    value = {
                        'TRUE': '1', 'ON': '1', '1': '1',
                        'FALSE': '0', 'OFF': '0', '0': '0',
                    }[str(raw_value).upper()]
                else:
                    value = self._literal(str(raw_value))
            return {'statements': [{
                'source': f'SET GLOBAL {self._quote(name)} = {value}',
                'parameters': (),
            }]}
        if (
            self.dialect.engine_id == 'mariadb' and
            request['resource_kind'] == 'session' and
            operation in {'terminate_query', 'terminate_connection'}
        ):
            target = request.get('target_resource') or {}
            process_id = self._integer(
                self._mariadb_target_native(target).get('id'), 'process ID'
            )
            mode = request.get('draft', {}).get('termination_mode')
            subject = (
                'QUERY' if operation == 'terminate_query' else 'CONNECTION'
            )
            return {'statements': [{
                'source': f'KILL {mode} {subject} {process_id}',
                'parameters': (),
            }]}
        if (
            self.dialect.engine_id == 'mariadb' and
            request['resource_kind'] == 'binary-log'
            and operation == 'purge_before'
        ):
            target = request.get('target_resource') or {}
            return {'statements': [{
                'source': (
                    'PURGE BINARY LOGS TO ' +
                    self._literal(target.get('display_name'))
                ),
                'parameters': (),
            }]}
        if (
            self.dialect.engine_id == 'mariadb' and
            request['resource_kind'] == 'binary-log-status' and
            operation == 'rotate'
        ):
            return {'statements': [{
                'source': 'FLUSH BINARY LOGS', 'parameters': (),
            }]}
        if (
                self.dialect.engine_id == 'mariadb' and
                request['resource_kind'] == 'replication-channel' and
                operation != 'inspect'):
            return {
                'statements': [self._compile_mariadb_replication(request)]
            }
        if (
            self.dialect.engine_id == 'firebird' and
            request['resource_kind'] == 'database' and
            operation in _FIREBIRD_SERVICE_OPERATIONS
        ):
            route = request['_provider_route']
            database = route.get('database')
            if not isinstance(database, str) or not database.strip():
                raise RelationalClientError(
                    'Firebird service operation requires a database route'
                )
            return {
                'driver_operation': 'firebird-service',
                'operation_id': operation,
                'database': database.strip(),
                'options': copy.deepcopy(request.get('draft', {})),
                'statements': [],
                **({'restore_policy_requested': physical_restore_policy(
                    operation, request.get('draft', {}))}
                   if operation in {'restore_physical', 'fixup_database'}
                   else {}),
                **({'backup_io_requested': (
                    'NATIVE' if request['draft']['direct_io'] is None else
                    'ON' if request['draft']['direct_io'] else 'OFF')}
                   if operation == 'backup_physical' else {}),
                **({'backup_selection': ({
                    'mode': 'guid',
                    'guid': request['draft']['database_guid'],
                } if request.get('draft', {}).get('database_guid') else {
                    'mode': 'level',
                    'level': request.get('draft', {}).get('backup_level', 0),
                })} if operation == 'backup_physical' else {}),
                'warnings': [
                    'Firebird will remove backup-history records after the '
                    'physical backup. This does not delete backup files. '
                    'Older level/GUID lookup can become unavailable; '
                    'independently verify and preserve the restore chain.'
                ] if operation == 'backup_physical' and request.get(
                    'draft', {}).get('clean_history') is True else [],
            }
        if (
            self.dialect.engine_id == 'sqlite' and
            request['resource_kind'] == 'database' and
            operation in _SQLITE_DATABASE_OPERATIONS
        ):
            return self._compile_sqlite_database_operation(request)
        if (
                self.dialect.engine_id == 'duckdb' and
                request['resource_kind'] == 'database' and
                operation in _DUCKDB_DATABASE_OPERATIONS
        ):
            return self._compile_duckdb_database_operation(request)
        if (
                self.dialect.engine_id == 'mysql' and
                request['resource_kind'] == 'database' and
                operation in _MYSQL_DATABASE_OPERATIONS
        ):
            return self._compile_mysql_database_operation(request)
        if (
                self.dialect.engine_id == 'mariadb' and
                request['resource_kind'] == 'database' and
                operation in _MARIADB_DATABASE_OPERATIONS
        ):
            return self._compile_mariadb_database_operation(request)
        if (
                self.dialect.engine_id == 'mariadb' and
                request['resource_kind'] == 'database' and
                operation in _MARIADB_TOOL_DATABASE_OPERATIONS
        ):
            return {
                'driver_operation': 'mariadb-tools',
                'operation_id': operation,
                'options': copy.deepcopy(request.get('draft', {})),
                'statements': [],
            }
        if (
                self.dialect.engine_id == 'mariadb' and
                request['resource_kind'] == 'server' and
                operation in _MARIADB_TOOL_SERVER_OPERATIONS
        ):
            return {
                'driver_operation': 'mariadb-tools',
                'operation_id': operation,
                'options': copy.deepcopy(request.get('draft', {})),
                'statements': [],
            }
        if (
                self.dialect.engine_id == 'mysql' and
                request['resource_kind'] == 'database' and
                operation in _MYSQL_SHELL_DATABASE_OPERATIONS
        ):
            return {
                'driver_operation': 'mysql-shell',
                'operation_id': operation,
                'options': copy.deepcopy(request.get('draft', {})),
                'statements': [],
            }
        if operation == 'inspect':
            return {
                'internal_operation': 'inspect',
                'target_resource': copy.deepcopy(request['target_resource']),
                'statements': [],
            }
        if (
                self.dialect.engine_id == 'firebird' and
                request['resource_kind'] == 'database' and
                operation == 'drop'):
            route = request['_provider_route']
            database = route.get('database')
            if not isinstance(database, str) or not database.strip():
                raise RelationalClientError(
                    'Firebird database drop requires a database route'
                )
            compiled = {
                'driver_operation': 'firebird-drop-database',
                'database': database.strip(),
                'statements': [],
            }
            database_target = self._database_target_deletion(request)
            if database_target is not None:
                compiled['database_target'] = database_target
            return compiled
        if (
                self.dialect.embedded_database and
                request['resource_kind'] == 'database' and
                operation == 'drop'):
            route = request['_provider_route']
            database = route.get('database')
            if not isinstance(database, str) or not database.strip():
                raise RelationalClientError(
                    'embedded database deletion requires a database route'
                )
            extensions = request['target_resource'].get('extensions', {})
            target = extensions.get('cdeadmin', {})
            target_id = target.get('database_target_id')
            if not isinstance(target_id, str) or not target_id:
                raise RelationalClientError(
                    'embedded database deletion requires a retained target'
                )
            return {
                'driver_operation': 'embedded-drop-database',
                'database': database.strip(),
                'database_target': {
                    'target_id': target_id,
                    'confirmation': database.strip(),
                },
                'statements': [],
            }
        if operation == 'create':
            if request['resource_kind'] == 'database' and (
                self.dialect.database_create_mode != 'sql'
            ):
                return self._compile_database_create(request)
            compiled = {'statements': self._compile_create(request)}
            if self.dialect.engine_id == 'firebird':
                compiled['warnings'] = firebird_tables.warnings(request)
            if request['resource_kind'] == 'database':
                compiled['endpoint_database'] = request['draft']['name']
            return compiled
        if operation == 'alter':
            return {'statements': self._compile_alter(request)}
        if operation == 'rename':
            compiled = {'statements': [self._compile_rename(request)]}
            if (self.dialect.engine_id == 'firebird' and
                    request['resource_kind'] == 'column'):
                compiled['firebird_column_rename'] = (
                    firebird_identity.column_rename(
                        self._target_path(request['target_resource']),
                        request['draft']['new_name']))
                compiled['warnings'] = [
                    'Firebird may retain the old column name in privilege '
                    'catalog rows after a rename. Review grants and effective '
                    'access; this operation does not rewrite or revoke grants.'
                ]
            elif (self.dialect.engine_id == 'firebird' and
                    request['resource_kind'] == 'domain'):
                compiled['firebird_domain_rename'] = (
                    firebird_identity.domain_rename(
                        self._target_path(request['target_resource']),
                        request['draft']['new_name']))
                compiled['warnings'] = [
                    'Firebird can reject a domain rename at commit when '
                    'routine parameters depend on that domain. A staged '
                    'rename is not a committed change. This operation does '
                    'not rewrite dependent routines or system catalogs.'
                ]
            return compiled
        if operation == 'drop':
            compiled = {'statements': [self._compile_drop(request)]}
            if self.dialect.engine_id == 'firebird':
                compiled['warnings'] = firebird_tables.warnings(request)
            if request['resource_kind'] == 'database':
                database_target = self._database_target_deletion(request)
                if database_target is not None:
                    compiled['database_target'] = database_target
            return compiled
        if operation == 'insert':
            compiled = {'statements': [self._compile_insert(request)]}
            if self.dialect.engine_id == 'firebird':
                compiled['warnings'] = firebird_tables.warnings(request)
            return compiled
        if operation in {'update', 'delete'}:
            return {'statements': [self._compile_identity_dml(request)]}
        if (
                self.dialect.engine_id == 'mariadb' and
                request['resource_kind'] == 'role' and
                operation in {'grant', 'revoke', 'set_default'}):
            return {'statements': [self._compile_mariadb_role(request)]}
        if (self.dialect.engine_id == 'firebird' and
                request['resource_kind'] == 'role' and
                operation in {'grant', 'revoke'}):
            return {'statements': [self._compile_firebird_role(request)]}
        if operation in {'grant', 'revoke'}:
            return {'statements': [self._compile_privilege(request)]}
        if operation == 'execute':
            return {'statements': [self._compile_execute(request)]}
        raise RelationalClientError(
            'relational operation has no provider compiler'
        )

    @staticmethod
    def _database_target_deletion(request):
        """Describe a retained target that a successful native drop removes."""
        target = request.get('target_resource')
        extensions = target.get('extensions', {}) if isinstance(
            target, Mapping
        ) else {}
        cdeadmin = extensions.get('cdeadmin', {}) if isinstance(
            extensions, Mapping
        ) else {}
        target_id = cdeadmin.get('database_target_id') if isinstance(
            cdeadmin, Mapping
        ) else None
        if not isinstance(target_id, str) or not target_id:
            return None
        confirmation = request.get('draft', {}).get('confirmation')
        if not isinstance(confirmation, str) or not confirmation:
            return None
        return {'target_id': target_id, 'confirmation': confirmation}

    def _compile_mysql_database_operation(self, request):
        operation = request['operation_id']
        draft = request.get('draft', {})
        route = request['_provider_route']
        database = self._identifier(route['database'])
        tables = ', '.join(
            self._qualified((database, self._identifier(table)))
            for table in draft['tables']
        )
        local = (
            ' NO_WRITE_TO_BINLOG'
            if draft.get('no_write_to_binlog') else ''
        )
        if operation == 'analyze_tables':
            source = f'ANALYZE{local} TABLE {tables}'
            action = draft.get('histogram_action', 'NONE')
            columns = ', '.join(
                self._quote(column)
                for column in draft.get('histogram_columns', [])
            )
            if action == 'UPDATE':
                source += f' UPDATE HISTOGRAM ON {columns}'
                histogram_data = draft.get('histogram_data')
                if histogram_data:
                    source += ' USING DATA ' + self._literal(
                        json.dumps(
                            histogram_data, sort_keys=True,
                            separators=(',', ':'),
                        )
                    )
                else:
                    update_mode = (
                        'AUTO' if draft.get('histogram_auto_update')
                        else 'MANUAL'
                    )
                    source += (
                        f' WITH {int(draft.get("histogram_buckets", 100))} '
                        'BUCKETS '
                        f'{update_mode} UPDATE'
                    )
            elif action == 'DROP':
                source += f' DROP HISTOGRAM ON {columns}'
        elif operation == 'check_tables':
            options = ' '.join(draft.get('check_options', []))
            source = (
                f'CHECK TABLE {tables}' + (f' {options}' if options else '')
            )
        elif operation == 'optimize_tables':
            source = f'OPTIMIZE{local} TABLE {tables}'
        elif operation == 'repair_tables':
            options = ' '.join(draft.get('repair_options', []))
            source = (
                f'REPAIR{local} TABLE {tables}' +
                (f' {options}' if options else '')
            )
        elif operation == 'checksum_tables':
            checksum_type = draft.get('checksum_type', 'DEFAULT')
            source = (
                f'CHECKSUM TABLE {tables}' +
                (f' {checksum_type}' if checksum_type != 'DEFAULT' else '')
            )
        else:
            raise RelationalClientError(
                'MySQL database operation has no native compiler'
            )
        return {
            'operation_id': operation,
            'statements': [{'source': source}],
        }

    def _compile_mariadb_database_operation(self, request):
        operation = request['operation_id']
        draft = request.get('draft', {})
        route = request['_provider_route']
        database = self._identifier(route['database'])
        names_field = (
            'objects' if operation in {'check_objects', 'repair_objects'}
            else 'tables'
        )
        targets = [
            self._qualified((database, self._identifier(name)))
            for name in draft[names_field]
        ]
        binlog_mode = draft.get('binlog_mode', 'DEFAULT')
        local = '' if binlog_mode == 'DEFAULT' else f' {binlog_mode}'
        if operation == 'analyze_tables':
            persistent_for = draft.get('persistent_for', 'NONE')
            if persistent_for == 'ALL':
                suffix = ' PERSISTENT FOR ALL'
            elif persistent_for == 'SPECIFIED':
                columns = ', '.join(
                    self._quote(name)
                    for name in draft.get('persistent_columns', [])
                )
                indexes = ', '.join(
                    'PRIMARY' if name.upper() == 'PRIMARY'
                    else self._quote(name)
                    for name in draft.get('persistent_indexes', [])
                )
                suffix = (
                    ' PERSISTENT FOR COLUMNS '
                    f'({columns}) INDEXES ({indexes})'
                )
            else:
                suffix = ''
            source = (
                f'ANALYZE{local} TABLE ' +
                ', '.join(target + suffix for target in targets)
            )
        elif operation == 'check_objects':
            object_type = draft.get('object_type', 'TABLE')
            options = ' '.join(draft.get('check_options', []))
            source = f'CHECK {object_type} ' + ', '.join(targets)
            if options:
                source += f' {options}'
        elif operation == 'optimize_tables':
            source = f'OPTIMIZE{local} TABLE ' + ', '.join(targets)
            wait_mode = draft.get('lock_wait_mode', 'DEFAULT')
            if wait_mode == 'WAIT':
                source += f' WAIT {int(draft.get("lock_wait_seconds", 0))}'
            elif wait_mode == 'NOWAIT':
                source += ' NOWAIT'
        elif operation == 'repair_objects':
            object_type = draft.get('object_type', 'TABLE')
            options = ' '.join(draft.get('repair_options', []))
            source = (
                f'REPAIR{local} {object_type} ' + ', '.join(targets)
            )
            if options:
                source += f' {options}'
        elif operation == 'checksum_tables':
            source = 'CHECKSUM TABLE ' + ', '.join(targets)
            checksum_type = draft.get('checksum_type', 'DEFAULT')
            if checksum_type != 'DEFAULT':
                source += f' {checksum_type}'
        else:
            raise RelationalClientError(
                'MariaDB database operation has no native compiler'
            )
        return {
            'operation_id': operation,
            'statements': [{'source': source}],
        }

    def _compile_sqlite_database_operation(self, request):
        operation = request['operation_id']
        draft = request.get('draft', {})
        if operation in {'backup', 'restore'}:
            return {
                'driver_operation': f'embedded-sqlite-{operation}',
                'operation_id': operation,
                'options': copy.deepcopy(dict(draft)),
                'statements': [],
                'warnings': ([
                    'Restore replaces the active database contents using '
                    'SQLite\'s native backup API.'
                ] if operation == 'restore' else []),
            }
        if operation in {'integrity_check', 'quick_check'}:
            limit = int(draft.get('max_errors', 100))
            source = f'PRAGMA {operation}({limit})'
        elif operation == 'foreign_key_check':
            table = draft.get('table')
            source = 'PRAGMA foreign_key_check'
            if table:
                source += f'({self._quote(table)})'
        elif operation == 'vacuum':
            source = 'VACUUM'
        elif operation == 'incremental_vacuum':
            source = f'PRAGMA incremental_vacuum({int(draft.get("pages", 0))})'
        elif operation == 'optimize':
            source = 'PRAGMA optimize'
        elif operation in {'analyze', 'reindex'}:
            source = operation.upper()
            target = draft.get('target')
            if target:
                source += ' ' + self._qualified(tuple(target.split('.')))
        elif operation == 'wal_checkpoint':
            source = (
                'PRAGMA wal_checkpoint('
                f'{draft.get("mode", "PASSIVE")})'
            )
        else:
            raise RelationalClientError(
                'SQLite database operation has no native compiler'
            )
        return {
            'operation_id': operation,
            'statements': [{'source': source}],
        }

    def _compile_duckdb_database_operation(self, request):
        operation = request['operation_id']
        draft = request.get('draft', {})
        if operation in {'checkpoint', 'force_checkpoint'}:
            database = request['target_resource'].get('display_name')
            source = (
                'FORCE CHECKPOINT' if operation == 'force_checkpoint'
                else 'CHECKPOINT'
            ) + ' ' + self._quote(database)
        elif operation == 'vacuum':
            source = 'VACUUM'
        elif operation == 'analyze':
            source = 'ANALYZE'
        elif operation in {'export_database', 'import_database'}:
            directory = self._duckdb_operation_directory(
                request['_provider_route'], draft.get('directory'),
                importing=operation == 'import_database',
            )
            if operation == 'export_database':
                source = (
                    f'EXPORT DATABASE {self._literal(directory)} '
                    f'(FORMAT {draft.get("format", "PARQUET")})'
                )
            else:
                source = f'IMPORT DATABASE {self._literal(directory)}'
        else:
            raise RelationalClientError(
                'DuckDB database operation has no native compiler'
            )
        warnings = []
        if operation == 'import_database':
            warnings.append(
                'DuckDB IMPORT DATABASE loads objects into the active '
                'database; it does not replace the database file.'
            )
        return {
            'operation_id': operation,
            'statements': [{'source': source}],
            'warnings': warnings,
        }

    @staticmethod
    def _validate_firebird_service(operation, draft):
        errors = []

        def path(field_id):
            value = draft.get(field_id)
            if value is None:
                return
            if not isinstance(value, str) or not value.strip() or any(
                character in value for character in ('\x00', '\r', '\n')
            ):
                errors.append({
                    'field_id': field_id,
                    'code': 'invalid_firebird_server_path',
                    'message': (
                        'Firebird service file paths must be non-empty '
                        'server-side paths without control characters.'
                    ),
                })

        if operation in {'backup_logical', 'backup_physical'}:
            path('backup_file')
        if operation in {'backup_physical', 'restore_physical'}:
            try:
                normalize_physical_io(operation, draft)
            except RelationalClientError as error:
                errors.append({
                    'field_id': ('direct_io_mode' if 'direct_io_mode' in draft
                                 else 'direct_io'),
                    'code': 'invalid_firebird_physical_io',
                    'message': str(error),
                })
        if operation == 'backup_physical':
            try:
                normalize_backup_guid(draft.get('database_guid'))
            except RelationalClientError as error:
                errors.append({
                    'field_id': 'database_guid',
                    'code': 'invalid_firebird_backup_guid',
                    'message': str(error),
                })
            clean = draft.get('clean_history', False)
            unit = draft.get('history_keep_unit')
            count = draft.get('history_keep_value')
            if not isinstance(clean, bool):
                errors.append({
                    'field_id': 'clean_history',
                    'code': 'invalid_firebird_history_selection',
                    'message': 'History cleanup must be enabled or disabled.',
                })
            if clean is True:
                if not isinstance(unit, str) or unit not in {'DAYS', 'ROWS'}:
                    errors.append({
                        'field_id': 'history_keep_unit',
                        'code': 'invalid_firebird_history_unit',
                        'message': 'Choose native history retention in days '
                                   'or rows.',
                    })
                if (isinstance(count, bool) or not isinstance(count, int) or
                        not 1 <= count <= 2147483647):
                    errors.append({
                        'field_id': 'history_keep_value',
                        'code': 'invalid_firebird_history_count',
                        'message': 'History retention must be a positive '
                                   'signed 32-bit integer.',
                    })
            elif unit is not None or count is not None:
                errors.append({
                    'field_id': 'clean_history',
                    'code': 'firebird_history_cleanup_required',
                    'message': 'Retention settings require history cleanup.',
                })
        if operation in {'restore_logical', 'restore_physical'}:
            path('restore_database')
        if operation == 'restore_logical':
            path('backup_file')
        if operation == 'restore_physical':
            backups = draft.get('backup_files')
            if not isinstance(backups, list) or not backups or any(
                not isinstance(item, str) or not item.strip() or any(
                    character in item for character in ('\x00', '\r', '\n')
                ) for item in backups
            ):
                errors.append({
                    'field_id': 'backup_files',
                    'code': 'invalid_firebird_backup_files',
                    'message': (
                        'Physical restore requires one or more exact '
                        'server-side backup file paths.'
                    ),
                })
        for field_id, minimum, maximum in (
            ('parallel_workers', 1, 128),
            ('backup_level', 0, MAX_BACKUP_LEVEL),
            ('lock_timeout', -1, 86400),
            ('shutdown_timeout', 0, 86400),
            ('page_buffers', 0, 2147483647),
            ('sweep_interval', 0, 2147483647),
            ('verbose_interval', 1, 2147483647),
        ):
            value = draft.get(field_id)
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, int) or
                value < minimum or value > maximum
            ):
                errors.append({
                    'field_id': field_id,
                    'code': 'invalid_firebird_numeric_option',
                    'message': f'{field_id} is outside its Firebird range.',
                })
        page_size = draft.get('page_size')
        if page_size is not None and (
            not isinstance(page_size, str) or page_size not in {
                '4096', '8192', '16384', '32768'
            }
        ):
            errors.append({
                'field_id': 'page_size',
                'code': 'invalid_firebird_page_size',
                'message': 'The restored Firebird page size is invalid.',
            })
        statistics = draft.get('statistics')
        if statistics is not None and statistics != '' and (
            not isinstance(statistics, str) or
            re.fullmatch(r'[TDWR]{1,4}', statistics.upper()) is None or
            len(set(statistics.upper())) != len(statistics)
        ):
            errors.append({
                'field_id': 'statistics',
                'code': 'invalid_firebird_statistics',
                'message': (
                    'Firebird service statistics may contain each of '
                    'T, D, W, and R at most once.'
                ),
            })
        if draft.get('skip_data') and draft.get('include_data'):
            errors.append({
                'field_id': 'include_data',
                'code': 'conflicting_firebird_data_filters',
                'message': (
                    'Firebird backup include-data and skip-data filters '
                    'cannot be used together.'
                ),
            })
        for field_id in (
            'additional_backup_files', 'additional_database_files',
        ):
            values = draft.get(field_id)
            if values is not None and (
                not isinstance(values, list) or any(
                    not isinstance(item, str) or not item.strip() or any(
                        character in item
                        for character in ('\x00', '\r', '\n')
                    )
                    for item in values
                )
            ):
                errors.append({
                    'field_id': field_id,
                    'code': 'invalid_firebird_server_paths',
                    'message': (
                        'Firebird service file lists must contain only '
                        'non-empty server-side paths.'
                    ),
                })
        database_pages = draft.get('database_file_pages')
        additional_databases = draft.get('additional_database_files')
        if database_pages is not None and (
            not isinstance(database_pages, list) or any(
                isinstance(item, bool) or not isinstance(item, int) or
                item < 0 or item > 2147483647
                for item in database_pages
            )
        ):
            errors.append({
                'field_id': 'database_file_pages',
                'code': 'invalid_firebird_database_file_pages',
                'message': (
                    'Firebird database file page allocations must be '
                    'non-negative integers.'
                ),
            })
        elif isinstance(additional_databases, list) and len(
                database_pages or []) != len(additional_databases):
            errors.append({
                'field_id': 'database_file_pages',
                'code': 'invalid_firebird_database_file_page_count',
                'message': (
                    'Supply one page allocation for each additional '
                    'Firebird database file.'
                ),
            })
        flag_fields = {
            'backup_logical': ('backup_flags', {
                'IGNORE_CHECKSUMS', 'IGNORE_LIMBO', 'METADATA_ONLY',
                'NO_GARBAGE_COLLECT', 'OLD_DESCRIPTIONS',
                'NON_TRANSPORTABLE', 'CONVERT', 'EXPAND', 'NO_TRIGGERS',
                'ZIP', 'DIRECT_IO',
            }),
            'restore_logical': ('restore_flags', {
                'METADATA_ONLY', 'DEACTIVATE_IDX', 'NO_SHADOW',
                'NO_VALIDITY', 'ONE_AT_A_TIME', 'USE_ALL_SPACE',
                'NO_TRIGGERS', 'DIRECT_IO',
            }),
            'backup_physical': ('backup_flags', {
                'NO_TRIGGERS',
            }),
            'restore_physical': ('restore_flags', {
                'IN_PLACE', 'SEQUENCE',
            }),
            'database_statistics': ('statistics_flags', {
                'DATA_PAGES', 'DB_LOG', 'HDR_PAGES', 'IDX_PAGES',
                'SYS_RELATIONS', 'RECORD_VERSIONS', 'NOCREATION',
                'ENCRYPTION',
            }),
            'fixup_database': ('fixup_flags', {
                'SEQUENCE',
            }),
        }
        flag_field = flag_fields.get(operation)
        if flag_field:
            values = draft.get(flag_field[0], [])
            if not isinstance(values, list) or any(
                    not isinstance(item, str) or item not in flag_field[1]
                    for item in values):
                errors.append({
                    'field_id': flag_field[0],
                    'code': 'invalid_firebird_service_flags',
                    'message': 'The Firebird service flags are invalid.',
                })
        enum_fields = {
            'set_space_reservation': (
                'mode', {'USE_FULL', 'RESERVE'},
            ),
            'set_write_mode': ('mode', {'ASYNC', 'SYNC'}),
            'set_access_mode': ('mode', {'READ_ONLY', 'READ_WRITE'}),
            'set_replica_mode': (
                'mode', {'NONE', 'READ_ONLY', 'READ_WRITE'},
            ),
            'repair_database': (
                'repair_action', {
                    'VALIDATE_DB', 'CORRUPTION_CHECK', 'REPAIR',
                    'KILL_SHADOWS', 'ICU', 'UPGRADE_DB',
                },
            ),
            'shutdown_database': (
                'mode', {'MULTI', 'SINGLE', 'FULL'},
            ),
            'bring_online': ('mode', {'NORMAL', 'MULTI', 'SINGLE'}),
        }
        enum_field = enum_fields.get(operation)
        if enum_field and (
            not isinstance(draft.get(enum_field[0]), str) or
            draft[enum_field[0]] not in enum_field[1]
        ):
            errors.append({
                'field_id': enum_field[0],
                'code': 'invalid_firebird_service_option',
                'message': 'The Firebird service option is invalid.',
            })
        if operation == 'shutdown_database' and (
            not isinstance(draft.get('method'), str) or
            draft['method'] not in {
                'FORCED', 'DENY_ATTACHMENTS', 'DENY_TRANSACTIONS'}
        ):
            errors.append({
                'field_id': 'method',
                'code': 'invalid_firebird_shutdown_method',
                'message': 'The Firebird shutdown method is invalid.',
            })
        if operation == 'set_sql_dialect' and (
            not isinstance(draft.get('sql_dialect'), str) or
            draft['sql_dialect'] not in {'1', '3'}
        ):
            errors.append({
                'field_id': 'sql_dialect',
                'code': 'invalid_firebird_sql_dialect',
                'message': 'Firebird database SQL dialect must be 1 or 3.',
            })
        replica_mode = draft.get('replica_mode')
        if operation == 'restore_logical' and replica_mode is not None and (
            not isinstance(replica_mode, str) or replica_mode not in {
                '', 'NONE', 'READ_ONLY', 'READ_WRITE'}
        ):
            errors.append({
                'field_id': 'replica_mode',
                'code': 'invalid_firebird_replica_mode',
                'message': 'The Firebird replica mode is invalid.',
            })
        return errors

    def _compile_database_create(self, request):
        name = request['draft'].get('name')
        requested_path = request['draft'].get('database_path')
        if requested_path is None and (
            not isinstance(name, str) or not re.fullmatch(
                r'[A-Za-z0-9][A-Za-z0-9_.-]{0,254}', name
            ) or '..' in name
        ):
            raise RelationalClientError(
                'database name must be a safe unqualified file name'
            )
        if requested_path is not None and (
            not isinstance(requested_path, str) or
            not requested_path.strip() or len(requested_path) > 4096 or
            any(
                character in requested_path
                for character in ('\x00', '\r', '\n')
            )
        ):
            raise RelationalClientError('database path is invalid')
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
                    from .firebird.connection_strings import target_path
                    server_path = target_path(
                        current, route.get('host'), route.get('port'))
                    if server_path.startswith('/'):
                        root = posixpath.dirname(server_path)
        if not isinstance(root, str) or not root:
            raise RelationalClientError(
                'endpoint has no approved database creation root'
            )
        extension = self.dialect.database_extension
        filename = None
        if requested_path is None:
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
            if not posixpath.isabs(root_path):
                raise RelationalClientError(
                    'Firebird database creation root must be absolute'
                )
            requested = requested_path.strip() if requested_path else filename
            database_path = posixpath.normpath(
                requested if posixpath.isabs(requested)
                else posixpath.join(root_path, requested)
            )
            if not (
                database_path == root_path or
                database_path.startswith(root_path.rstrip('/') + '/')
            ):
                raise RelationalClientError(
                    'database file escapes the approved creation root'
                )
            from .firebird.connection_strings import database_dsn
            protocol = route.get('protocol')
            database = database_dsn(
                database_path, route.get('host'), route.get('port'),
                protocol if protocol in {'INET', 'INET4', 'INET6'} else None)
            driver_operation = 'firebird-create-database'
        else:
            raise RelationalClientError(
                'database creation mode is unavailable'
            )
        return {
            'driver_operation': driver_operation,
            'database': database,
            'endpoint_database': (
                database if mode == 'embedded-file' else database_path
            ),
            'create_options': (
                {
                    'page_size': int(request['draft'].get(
                        'page_size', '8192'
                    )),
                    'default_charset': request['draft'].get(
                        'default_charset', 'UTF8'
                    ),
                    'sql_dialect': int(request['draft'].get(
                        'sql_dialect', '3'
                    )),
                    'forced_writes': request['draft'].get(
                        'forced_writes', True
                    ),
                    'reserve_space': request['draft'].get(
                        'reserve_space', True
                    ),
                } if mode == 'firebird-driver' else {
                    **copy.deepcopy(request['draft'].get('options', {})),
                }
            ),
            'statements': [],
            'warnings': [
                'Database creation uses the endpoint-approved creation root.'
            ] + ([
                'Firebird does not disable cooperative garbage collection '
                'on the initial creation attachment. The selected setting '
                'applies to subsequent database attachments.'
            ] if mode == 'firebird-driver' and route.get('no_gc') else []),
        }

    def _normalize_draft(self, kind, operation, draft):
        if not isinstance(draft, Mapping):
            raise RelationalClientError('administration draft is invalid')
        value = copy.deepcopy(dict(draft))
        if (self.dialect.engine_id == 'firebird' and kind == 'database' and
                operation in {'backup_physical', 'restore_physical'}):
            value = normalize_physical_io(operation, value)
        if (self.dialect.engine_id == 'firebird' and kind == 'database' and
                operation == 'backup_physical'):
            value['backup_level'] = normalize_backup_level(
                value.get('backup_level'))
        if (self.dialect.engine_id == 'firebird' and kind == 'database' and
                operation == 'backup_physical' and 'database_guid' in value):
            guid = normalize_backup_guid(value['database_guid'])
            if guid is None:
                value.pop('database_guid')
            else:
                value['database_guid'] = guid
        if (self.dialect.engine_id == 'firebird' and kind == 'column' and
                operation == 'create' and 'column_mode' in value):
            return value
        if (self.dialect.engine_id == 'firebird' and kind == 'column' and
                operation in {'alter', 'comment'}):
            return value
        if (self.dialect.engine_id == 'firebird' and
                kind in firebird_mappings.KINDS):
            return value
        if operation == 'create':
            options = copy.deepcopy(value.pop('options', {}) or {})
            if kind == 'table' and self.dialect.engine_id == 'firebird':
                for key in firebird_tables.CREATE_KEYS:
                    if key in value:
                        options[key] = value.pop(key)
            if kind == 'index' and self.dialect.engine_id == 'firebird':
                for key in ('index_kind', 'direction', 'condition'):
                    if key in value:
                        options[key] = value.pop(key)
            if kind == 'database' and self.dialect.engine_id in {
                    'mysql', 'mariadb'}:
                database_keys = (
                    ('if_not_exists', 'character_set', 'collation',
                     'encryption')
                    if self.dialect.engine_id == 'mysql' else
                    ('or_replace', 'if_not_exists', 'character_set',
                     'collation', 'comment')
                )
                for key in database_keys:
                    if key in value:
                        options[key] = value.pop(key)
            if kind == 'database' and self.dialect.engine_id == 'sqlite':
                for key in (
                    'page_size', 'encoding', 'auto_vacuum',
                    'application_id', 'user_version',
                ):
                    if key in value:
                        options[key] = value.pop(key)
            if kind == 'database' and self.dialect.engine_id == 'duckdb':
                if 'config' in value:
                    options['config'] = value.pop('config')
            if (kind == 'role' and self.dialect.engine_id == 'firebird' and
                    'description' in value):
                options['description'] = value.pop('description')
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
                'with_data', 'replica_placement', 'schema', 'version',
                'cascade', 'admin', 'admin_kind', 'authentication_mode',
                'authentication_string', 'additional_authentication',
                'tls_requirement', 'tls_cipher', 'x509_issuer',
                'x509_subject', 'max_queries_per_hour',
                'max_updates_per_hour', 'max_connections_per_hour',
                'max_user_connections', 'max_statement_time',
                'account_lock', 'password_expiration',
                'password_expiration_days',
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
                keys = ('add_columns', 'drop_columns', 'rename_columns')
                if self.dialect.engine_id == 'firebird':
                    keys += firebird_tables.ALTER_KEYS
                value['changes'] = {
                    key: value.pop(key)
                    for key in keys if key in value
                }
            elif kind == 'database' and self.dialect.engine_id == 'firebird':
                value['changes'] = {
                    key: value.pop(key)
                    for key in (
                        'default_charset', 'linger_seconds', 'drop_linger',
                        'default_sql_security',
                    ) if key in value and value[key] not in {None, ''}
                }
            elif kind == 'database' and self.dialect.engine_id == 'sqlite':
                value['changes'] = {
                    key: value.pop(key)
                    for key in (
                        'journal_mode', 'synchronous', 'auto_vacuum',
                        'page_size', 'application_id', 'user_version',
                        'run_vacuum',
                    ) if key in value and value[key] not in {None, ''}
                }
            elif kind == 'database' and self.dialect.sql_family == 'mysql':
                database_keys = (
                    ('character_set', 'collation', 'encryption', 'read_only')
                    if self.dialect.engine_id == 'mysql' else
                    ('character_set', 'collation', 'comment')
                )
                value['changes'] = {
                    key: value.pop(key)
                    for key in database_keys
                    if key in value and (
                        value[key] is not None and
                        (key == 'comment' or value[key] != '')
                    )
                }
            elif kind == 'user':
                user_keys = (
                    'password', 'plugin', 'administrator', 'active'
                )
                if self.dialect.engine_id == 'mariadb':
                    user_keys = (
                        'authentication_mode', 'password', 'plugin',
                        'authentication_string', 'additional_authentication',
                        'tls_requirement', 'tls_cipher', 'x509_issuer',
                        'x509_subject', 'max_queries_per_hour',
                        'max_updates_per_hour',
                        'max_connections_per_hour',
                        'max_user_connections', 'max_statement_time',
                        'account_lock', 'password_expiration',
                        'password_expiration_days',
                    )
                value['changes'] = {
                    key: value.pop(key)
                    for key in user_keys
                    if key in value and value[key] is not None and
                    value[key] != ''
                }
            elif kind == 'role' and self.dialect.engine_id == 'firebird':
                value['changes'] = {
                    key: value.pop(key)
                    for key in (
                        'system_privileges', 'drop_system_privileges',
                        'description', 'clear_description',
                    ) if key in value
                }
            elif kind == 'index' and self.dialect.engine_id == 'firebird':
                value['changes'] = {
                    key: value.pop(key) for key in
                    ('active', 'refresh_statistics') if key in value
                }
            elif kind == 'sequence':
                value['changes'] = {
                    key: value.pop(key)
                    for key in ('restart', 'increment', 'restart_initial',
                                'description', 'clear_description')
                    if key in value and (
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
                'materialized-view',
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
        if self.dialect.engine_id == 'firebird' and kind == 'column':
            if operation == 'create':
                return firebird_columns.creation_form(self._field)
            if operation in {'alter', 'comment'}:
                return firebird_columns.form(operation, self._field)
        if (self.dialect.engine_id == 'firebird' and
                kind in firebird_mappings.KINDS):
            return firebird_mappings.form(kind, operation, self._field)
        if (self.dialect.engine_id == 'firebird' and kind == 'role' and
                operation == 'configure_admin_mapping'):
            return {
                'form_id': 'firebird.role.configure_admin_mapping',
                'title': 'Configure Windows administrator mapping',
                'fields': [self._field(
                    'mapping_action', 'Windows administrator mapping',
                    'select', True,
                    'RDB$ADMIN only. SET creates or replaces the reserved '
                    'AutoAdminImplementationMapping using Win_Sspi in this '
                    'database; DROP removes that mapping. '
                    'This does not enable the authentication plugin or '
                    'verify Windows login. No global mapping is changed.',
                    'SET', options=('SET', 'DROP'))],
            }
        if operation == 'inspect':
            return {'form_id': f'{kind}.inspect', 'title': title, 'fields': []}
        if self.dialect.engine_id == 'mariadb' and (
                kind == 'replication-channel'):
            if operation in {'create', 'alter'}:
                fields = self._mariadb_replication_change_fields(
                    creating=operation == 'create'
                )
            elif operation == 'start':
                fields = [
                    self._field(
                        'thread', 'Replication thread', 'select', False,
                        default='ALL', options=('ALL', 'IO_THREAD',
                                                'SQL_THREAD'),
                    ),
                    self._field(
                        'until_mode', 'Stop condition', 'select', False,
                        default='NONE', options=(
                            'NONE', 'MASTER_POSITION', 'RELAY_POSITION',
                            'MASTER_GTID_POS', 'SQL_AFTER_GTIDS',
                            'SQL_BEFORE_GTIDS',
                        ),
                    ),
                    self._field('until_log_file', 'Log file', 'text'),
                    self._field('until_log_position', 'Log position',
                                'number'),
                    self._field('until_gtid', 'GTID position', 'text'),
                ]
            elif operation == 'stop':
                fields = [self._field(
                    'thread', 'Replication thread', 'select', False,
                    default='ALL', options=('ALL', 'IO_THREAD', 'SQL_THREAD'),
                )]
            elif operation == 'reset':
                fields = [
                    self._field(
                        'delete_connection',
                        'Delete the channel connection definition',
                        'boolean', default=True,
                    ),
                    self._field('confirmation', 'Confirmation', 'text', True),
                ]
            else:
                return self._existing_operation_form(kind, operation)
            return {
                'form_id': f'mariadb.replication-channel.{operation}',
                'title': f'{title} MariaDB replication channel',
                'fields': fields,
            }
        if self.dialect.engine_id == 'firebird' and kind == 'role' and (
                operation in {'grant', 'revoke'}):
            fields = [
                self._field('member', 'Member name', 'text', True),
                self._field('member_kind', 'Member type', 'select', True,
                            default='USER', options=('USER', 'ROLE')),
                self._field(
                    'default_role', 'Default role' if operation == 'grant'
                    else 'Remove default status only', 'boolean',
                    default=False, help_text=(
                        'Makes the role effective without selecting it.'
                        if operation == 'grant' else
                        'Keeps membership. Select both removal options to '
                        'remove default status and delegation together.')),
                self._field(
                    'admin_option' if operation == 'grant'
                    else 'admin_option_only',
                    'Allow delegation' if operation == 'grant'
                    else 'Revoke admin option only', 'boolean',
                    default=False, help_text=(
                        'Allows the member to grant this role to others.'
                        if operation == 'grant' else
                        'Keeps membership. Leave both removal options '
                        'unchecked to revoke membership itself.')),
                self._field('grantor', 'Grantor (optional)', 'text',
                            help_text='Uses GRANTED BY USER. Role admin '
                            'permission alone does not authorize this clause; '
                            'Firebird checks administrative or '
                            'USE_GRANTED_BY_CLAUSE authority and the '
                            'named grantor\'s permission.'),
            ]
            if operation == 'revoke':
                fields.append(self._field(
                    'confirmation', 'Confirmation', 'text', True))
            return {'form_id': f'firebird.role.{operation}',
                    'title': f'{title} Firebird role membership',
                    'fields': fields}
        if self.dialect.engine_id == 'mariadb' and kind == 'role' and (
                operation in {'grant', 'revoke', 'set_default'}):
            fields = [
                self._field('member', 'User or role receiving the role',
                            'text', True),
                self._field('member_kind', 'Member type', 'select', True,
                            default='USER', options=('USER', 'ROLE')),
            ]
            if operation == 'grant':
                fields.append(self._field(
                    'admin_option', 'Allow the member to grant this role',
                    'boolean', default=False,
                ))
            elif operation == 'revoke':
                fields.extend((
                    self._field(
                        'admin_option_only', 'Revoke only the admin option',
                        'boolean', default=False,
                    ),
                    self._field('confirmation', 'Confirmation', 'text', True),
                ))
            return {
                'form_id': f'mariadb.role.{operation}',
                'title': {
                    'grant': 'Grant MariaDB role membership',
                    'revoke': 'Revoke MariaDB role membership',
                    'set_default': 'Set default MariaDB role',
                }[operation],
                'fields': fields,
            }
        if self.dialect.engine_id == 'mariadb' and kind == 'privilege' and (
                operation in {'grant', 'revoke'}):
            fields = [
                self._field('principal', 'User or role', 'text', True),
                self._field('principal_kind', 'Principal type', 'select', True,
                            default='USER', options=('USER', 'ROLE')),
                self._field(
                    'object_type', 'Privilege scope', 'select', True,
                    options=('GLOBAL', 'DATABASE', 'TABLE', 'FUNCTION',
                             'PROCEDURE', 'SEQUENCE'),
                ),
                self._field(
                    'object_name', 'Native object name', 'text', True,
                    'Use * for global scope, a database name for database '
                    'scope, or a qualified database.object name.',
                ),
                self._field('privileges', 'MariaDB privileges', 'json', True),
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
                'form_id': f'mariadb.privilege.{operation}',
                'title': f'{title} MariaDB privileges', 'fields': fields,
            }
        if kind == 'privilege' and operation in {'grant', 'revoke'}:
            if self.dialect.engine_id == 'firebird':
                return firebird_privileges.form(operation, self._field)
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
                'table', 'view', 'materialized-view', 'index', 'sequence',
                'domain', 'column',
                'constraint', 'trigger', 'procedure', 'function', 'package',
                'event', 'materialization',
            } and self.dialect.engine_id != 'firebird':
                fields.append(self._field(
                    'parent', 'Parent database/schema', 'text', False,
                    'Use a qualified database or schema name where needed.',
                ))
            elif kind == 'role' and self.dialect.engine_id == 'firebird':
                fields.append(self._field(
                    'description', 'Comment', 'multiline', False,
                    'Firebird stores an empty comment as no comment.'))
                fields.append(self._field(
                    'system_privileges', 'System privileges', 'multiselect',
                    False, 'Select Firebird 5.0 system privileges.', [],
                    options=FIREBIRD_SYSTEM_PRIVILEGES,
                ))
            elif kind == 'role' and self.dialect.engine_id == 'mariadb':
                fields.extend((
                    self._field(
                        'admin', 'Role administrator', 'text', False,
                        'Optional MariaDB user or role named by WITH ADMIN.',
                    ),
                    self._field(
                        'admin_kind', 'Administrator type', 'select', False,
                        default='USER', options=('USER', 'ROLE'),
                    ),
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
                if self.dialect.engine_id == 'firebird':
                    fields.extend(firebird_tables.fields(self._field, True))
                fields.extend((
                    self._field(
                        'columns', 'Columns', 'json', True,
                        'Define each native column and its constraints.' if
                        self.dialect.engine_id == 'firebird' else
                        'Array of name, type, nullable, default, unique and '
                        'primary_key properties.',
                    ),
                    self._field(
                        'constraints', 'Table constraints', 'json', False,
                        'Structured PRIMARY KEY, UNIQUE, FOREIGN KEY or CHECK '
                        'constraints.', [],
                    ),
                ))
            elif kind in {'view', 'materialized-view'}:
                fields.append(self._field(
                    'query', (
                        'Materialized view query'
                        if kind == 'materialized-view' else 'View query'
                    ), 'code', True,
                    'One SELECT or WITH query; do not include the CREATE '
                    'command.',
                ))
            elif kind == 'index':
                fields.extend((
                    self._field('table', 'Table', 'text', True),
                    self._field('columns', 'Indexed columns', 'json', True),
                    self._field('unique', 'Unique index', 'boolean', False,
                                default=False),
                ))
                if self.dialect.engine_id == 'firebird':
                    for field in fields:
                        if field['field_id'] == 'columns':
                            field['visible_when'] = {
                                'field_id': 'index_kind', 'equals': 'columns'}
                    fields.extend((
                        self._field('index_kind', 'Index definition', 'select',
                                    default='columns',
                                    options=('columns', 'expression')),
                        self._field('direction', 'Index direction', 'select',
                                    default='ASCENDING',
                                    options=('ASCENDING', 'DESCENDING')),
                        {**self._field('expression', 'Computed expression',
                                       'text', True),
                         'visible_when': {'field_id': 'index_kind',
                                          'equals': 'expression'}},
                        self._field('condition', 'Partial index predicate',
                                    'text', False,
                                    'Optional expression after WHERE.'),
                    ))
            elif kind == 'sequence':
                sequence_fields = [
                    self._field('start', 'Start value',
                                'text' if self.dialect.engine_id ==
                                'firebird' else 'number', False),
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
                if (
                    self.dialect.engine_id == 'firebird' or
                    self.dialect.sql_family == 'postgresql'
                ):
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
                        default='ALIAS',
                        options=('ALIAS', 'ENUM', 'STRUCT', 'UNION'),
                    ),
                    self._field('base_type', 'Alias base type', 'text'),
                    self._field('enum_values', 'Enum values', 'json', False,
                                default=[]),
                    self._field('fields', 'Struct/union fields', 'json',
                                False, default=[]),
                ))
                for item in fields:
                    key = item['field_id']
                    if key == 'base_type':
                        item.update(required=True, visible_when={
                            'field_id': 'type_kind', 'equals': 'ALIAS'})
                    elif key == 'enum_values':
                        item.update(required=True, json_type='array',
                                    array_editor={'item_kind': 'string',
                                                  'allow_empty': True,
                                                  'unique_items': True},
                                    visible_when={'field_id': 'type_kind',
                                                  'equals': 'ENUM'})
                    elif key == 'fields':
                        item.update(required=True, json_type='array',
                                    array_editor={
                                        'item_kind': 'object', 'fields': [
                                            self._field('name', 'Member name',
                                                        'text', True),
                                            self._field('type', 'Native type',
                                                        'text', True),
                                        ]}, visible_when={
                                            'field_id': 'type_kind',
                                            'in': ['STRUCT', 'UNION']})
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
            elif kind == 'user' and self.dialect.engine_id == 'mariadb':
                fields.extend(self._mariadb_user_fields(creating=True))
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
                'fields': (firebird_tables.fields(self._field, False) if
                           self.dialect.engine_id == 'firebird' else []) + [
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
            if self.dialect.engine_id == 'mariadb':
                return {
                    'form_id': 'mariadb.user.alter',
                    'title': 'Alter MariaDB user',
                    'fields': self._mariadb_user_fields(creating=False),
                }
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
                    {**self._field('description', 'Comment', 'multiline',
                                   help_text='An empty comment removes the '
                                   'stored comment.'),
                     'initial_value_path': ['description'],
                     'visible_when': {'field_id': 'clear_description',
                                      'equals': False}},
                    self._field('clear_description', 'Remove comment',
                                'boolean', default=False),
                    {**self._field(
                        'system_privileges', 'Replacement system privileges',
                        'multiselect', False,
                        'Replaces the entire privilege set, not an additive '
                        'grant. Select every privilege to retain.', [],
                        options=FIREBIRD_SYSTEM_PRIVILEGES),
                     'initial_value_path': ['system_privileges'],
                     'visible_when': {'field_id': 'drop_system_privileges',
                                      'equals': False}},
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
                'fields': [
                    {**self._field('active', 'Active', 'boolean', False),
                     'initial_value_path': ['state', 'active']},
                    self._field('refresh_statistics',
                                'Recalculate index statistics', 'boolean',
                                False, default=False),
                ],
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
            if self.dialect.engine_id == 'firebird':
                return {
                    'form_id': 'firebird.sequence.alter',
                    'title': 'Alter Firebird sequence',
                    'fields': [
                        self._field(
                            'restart', 'Next generated value', 'text', False,
                            'Signed 64-bit integer. Leave empty to preserve '
                            'the current generator position. Concurrent '
                            'consumers can advance the sequence.',
                        ),
                        self._field(
                            'restart_initial', 'Restart at original start',
                            'boolean', False, default=False,
                        ),
                        {**self._field(
                            'increment', 'Increment by', 'number'),
                         'initial_value_path': ['increment']},
                        {**self._field(
                            'description', 'Comment', 'multiline'),
                         'initial_value_path': ['description']},
                        self._field(
                            'clear_description', 'Remove comment',
                            'boolean', False, default=False,
                        ),
                    ],
                }
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
        if operation == 'alter' and kind in {'view', 'materialized-view'}:
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

    def _mariadb_user_fields(self, creating):
        """Return MariaDB 12.2 account clauses as explicit form fields."""
        fields = []
        if creating:
            fields.append(self._field(
                'host', 'Host match', 'text', False, default='%',
            ))
        fields.extend((
            self._field(
                'authentication_mode', 'Primary authentication', 'select',
                creating, default=('PASSWORD' if creating else 'UNCHANGED'),
                options=(
                    ('PASSWORD', 'PLUGIN_PASSWORD', 'PLUGIN_STRING',
                     'PLUGIN_ONLY', 'NONE') if creating else
                    ('UNCHANGED', 'PASSWORD', 'PLUGIN_PASSWORD',
                     'PLUGIN_STRING', 'PLUGIN_ONLY')
                ),
            ),
            self._field(
                'password', 'Password', 'password', False,
                'Used by IDENTIFIED BY or by an authentication plugin '
                'through USING PASSWORD(...).', sensitive=True,
            ),
            self._field(
                'plugin', 'Authentication plugin', 'text', False,
                'MariaDB plugin identifier used by IDENTIFIED VIA.',
            ),
            self._field(
                'authentication_string', 'Plugin authentication string',
                'password', False,
                'Opaque MariaDB plugin authentication string used by USING.',
                sensitive=True,
            ),
            self._field(
                'additional_authentication',
                'Additional authentication methods', 'json', False,
                'Ordered OR-authentication methods. Each item declares a '
                'plugin and optionally password or authentication_string.',
                [], sensitive=True,
            ),
            self._field(
                'tls_requirement', 'TLS requirement', 'select', False,
                default='UNCHANGED' if not creating else 'NONE',
                options=(
                    ('NONE', 'SSL', 'X509', 'SPECIFIED') if creating else
                    ('UNCHANGED', 'NONE', 'SSL', 'X509', 'SPECIFIED')
                ),
            ),
            self._field('tls_cipher', 'Required TLS cipher', 'text'),
            self._field('x509_issuer', 'Required X.509 issuer', 'text'),
            self._field('x509_subject', 'Required X.509 subject', 'text'),
            self._field('max_queries_per_hour', 'Queries per hour', 'number'),
            self._field('max_updates_per_hour', 'Updates per hour', 'number'),
            self._field(
                'max_connections_per_hour', 'Connections per hour', 'number'
            ),
            self._field(
                'max_user_connections', 'Concurrent user connections',
                'number',
            ),
            self._field(
                'max_statement_time', 'Maximum statement time (seconds)',
                'number',
            ),
            self._field(
                'account_lock', 'Account lock state', 'select', False,
                default='UNCHANGED' if not creating else 'UNLOCK',
                options=(
                    ('LOCK', 'UNLOCK') if creating else
                    ('UNCHANGED', 'LOCK', 'UNLOCK')
                ),
            ),
            self._field(
                'password_expiration', 'Password expiration', 'select',
                False, default='UNCHANGED' if not creating else 'DEFAULT',
                options=(
                    ('DEFAULT', 'NEVER', 'NOW', 'INTERVAL') if creating else
                    ('UNCHANGED', 'DEFAULT', 'NEVER', 'NOW', 'INTERVAL')
                ),
            ),
            self._field(
                'password_expiration_days', 'Password lifetime (days)',
                'number',
            ),
        ))
        return fields

    def _mariadb_replication_change_fields(self, creating):
        fields = []
        if creating:
            fields.append(self._field(
                'name', 'Connection name', 'text', True,
                'MariaDB named replication connection.',
            ))
        fields.extend((
            self._field('master_host', 'Primary host', 'text', creating),
            self._field('master_user', 'Replication user', 'text', creating),
            self._field(
                'master_password', 'Replication password', 'password', False,
                sensitive=True,
            ),
            self._field('master_port', 'Primary port', 'number'),
            self._field('connect_retry', 'Connect retry (seconds)', 'number'),
            self._field('retry_count', 'Connection retry count', 'number'),
            self._field('replication_delay', 'SQL delay (seconds)', 'number'),
            self._field(
                'use_gtid', 'GTID positioning', 'select', False,
                default='UNCHANGED', options=(
                    'UNCHANGED', 'CURRENT_POS', 'SLAVE_POS', 'NO',
                ),
            ),
            self._field('master_log_file', 'Primary binary log file', 'text'),
            self._field('master_log_position', 'Primary log position',
                        'number'),
            self._field('relay_log_file', 'Relay log file', 'text'),
            self._field('relay_log_position', 'Relay log position', 'number'),
            self._field(
                'master_ssl', 'Use TLS for replication', 'select', False,
                default='UNCHANGED', options=('UNCHANGED', 'ON', 'OFF'),
            ),
            self._field('ssl_ca', 'TLS CA file', 'text'),
            self._field('ssl_ca_path', 'TLS CA directory', 'text'),
            self._field('ssl_certificate', 'TLS client certificate', 'text'),
            self._field('ssl_key', 'TLS client key', 'text'),
            self._field('ssl_cipher', 'TLS cipher', 'text'),
            self._field('ssl_crl', 'TLS certificate revocation list', 'text'),
            self._field('ssl_crl_path', 'TLS CRL directory', 'text'),
            self._field(
                'verify_server_certificate', 'Verify server certificate',
                'select', False, default='UNCHANGED',
                options=('UNCHANGED', 'ON', 'OFF'),
            ),
            self._field('heartbeat_period', 'Heartbeat period', 'number'),
            self._field('ignore_server_ids', 'Ignored server IDs', 'json',
                        False, default=[]),
            self._field('do_domain_ids', 'Included GTID domain IDs', 'json',
                        False, default=[]),
            self._field('ignore_domain_ids', 'Ignored GTID domain IDs',
                        'json', False, default=[]),
            self._field(
                'demote_to_slave', 'Demote current primary to replica',
                'select', False, default='UNCHANGED',
                options=('UNCHANGED', 'ON', 'OFF'),
            ),
        ))
        return fields

    def _routine_record_controls(self, form):
        """Expose the named/type records consumed by routine planners."""
        if self.dialect.engine_id == 'duckdb' or form.get('form_id') not in {
                'procedure.create', 'function.create',
                'procedure.alter', 'function.alter'}:
            return
        kind = form['form_id'].split('.')[0]
        if self.dialect.engine_id == 'firebird':
            unused = 'returns' if kind == 'procedure' else 'return_parameters'
            form['fields'] = [item for item in form['fields']
                              if item['field_id'] != unused]
        for item in form['fields']:
            key = item['field_id']
            if key == 'parameters' or (
                    key == 'return_parameters' and
                    self.dialect.engine_id == 'firebird'):
                item.update(json_type='array', default=[], array_editor={
                    'item_kind': 'object', 'fields': [
                        self._field('name', 'Parameter name', 'text', True),
                        self._field('type', 'Native data type', 'text', True),
                    ]})
            if self.dialect.engine_id == 'firebird' and key == 'returns':
                item['required'] = True

    def _structured_record_controls(self, form):
        """Visual schemas for this adapter's existing structured compiler."""
        field = RelationalAdministration._field

        def strings():
            return {'item_kind': 'string'}

        columns = {'item_kind': 'object', 'fields': [
            field('name', 'Column name', 'text', True),
            field('type', 'Native data type', 'text', True),
            field('nullable', 'Nullable', 'boolean', default=True),
            field('default', 'Default expression', 'text'),
            field('unique', 'Unique', 'boolean', default=False),
            field('primary_key', 'Primary key', 'boolean', default=False),
        ]}
        if self.dialect.engine_id == 'firebird':
            columns['fields'] = [
                item for item in
                firebird_columns.creation_form(field)['fields']
                if item['field_id'] != 'table']

        def column_names(name, label):
            return {**field(name, label, 'json', default=[]),
                    'json_type': 'array', 'array_editor': strings()}

        constraints = {'item_kind': 'object', 'fields': [
            field('name', 'Constraint name', 'text'),
            field('kind', 'Constraint type', 'select', True,
                  default='PRIMARY KEY',
                  options=('PRIMARY KEY', 'UNIQUE', 'FOREIGN KEY', 'CHECK')),
            column_names('columns', 'Columns'),
            field('references_table', 'Referenced table', 'text'),
            column_names('references_columns', 'Referenced columns'),
            field('expression', 'Check expression', 'text'),
        ]}
        for item in constraints['fields']:
            if item['field_id'] == 'columns':
                item['required'] = True
                item['visible_when'] = {'field_id': 'kind', 'in': [
                    'PRIMARY KEY', 'UNIQUE', 'FOREIGN KEY']}
            elif item['field_id'] in {
                    'references_table', 'references_columns'}:
                item['required'] = True
                item['visible_when'] = {'field_id': 'kind',
                                        'equals': 'FOREIGN KEY'}
            elif item['field_id'] == 'expression':
                item['required'] = True
                item['visible_when'] = {'field_id': 'kind', 'equals': 'CHECK'}
        schemas = {
            'table.create': {'columns': columns, 'constraints': constraints},
            'table.alter': {
                'add_columns': columns, 'drop_columns': strings(),
                'rename_columns': {'item_kind': 'object', 'fields': [
                    field('from', 'Existing name', 'text', True),
                    field('to', 'New name', 'text', True),
                ]},
            },
            'index.create': {'columns': strings()},
        }.get(form.get('form_id'), {})
        for item in form.get('fields', []):
            if form.get('form_id') == 'constraint.create' and (
                    item['field_id'] == 'properties'):
                children = copy.deepcopy([
                    child for child in constraints['fields']
                    if child['field_id'] != 'name'])
                item.update(object_editor={'fields': children},
                            json_type='object', default={
                                child['field_id']: child.get('default', '')
                                for child in children})
            schema = schemas.get(item['field_id'])
            if schema is not None:
                item.update(array_editor=copy.deepcopy(schema),
                            json_type='array', default=[])

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
        if operation == 'drop' and not self.dialect.supports_cascade:
            fields = [field for field in fields
                      if field['field_id'] != 'cascade']
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
            source = (firebird_tables.create(name, definitions, options) if
                      self.dialect.engine_id == 'firebird' else
                      f'CREATE TABLE {qualified} ({", ".join(definitions)})')
        elif kind in {'view', 'materialized-view'}:
            query = self._query_body(draft.get('definition'))
            command = (
                'CREATE MATERIALIZED VIEW'
                if kind == 'materialized-view' else 'CREATE VIEW'
            )
            source = f'{command} {qualified} AS {query}'
        elif kind == 'index':
            table = self._option_path(options, 'table')
            if self.dialect.engine_id == 'firebird':
                index_kind = options.get('index_kind', 'columns')
                direction = options.get('direction', 'ASCENDING')
                if index_kind not in ('columns', 'expression'):
                    raise RelationalClientError('Invalid Firebird index kind')
                if direction not in ('ASCENDING', 'DESCENDING'):
                    raise RelationalClientError('Invalid index direction')
                if index_kind == 'expression':
                    expression = index_expression(
                        options.get('expression'), 'index expression')
                    definition = f'COMPUTED BY ({expression}\n)'
                else:
                    columns = self._identifier_list(options.get('columns'))
                    definition = f'({columns})'
                condition = options.get('condition')
                if condition is not None and condition != '':
                    condition = index_expression(condition, 'index predicate')
                    definition += f' WHERE ({condition}\n)'
            else:
                columns = self._identifier_list(options.get('columns'))
                direction = ''
                definition = f'({columns})'
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
                f'CREATE {unique}{direction + " " if direction else ""}'
                f'INDEX {index_name} ON {table_name} {definition}'
            )
        elif kind == 'sequence':
            source = f'CREATE SEQUENCE {qualified}'
            for key, phrase in (
                ('start', ' START WITH '), ('increment', ' INCREMENT BY '),
                ('minimum', ' MINVALUE '), ('maximum', ' MAXVALUE '),
            ):
                if key in options:
                    value = (self._firebird_sequence_integer(options[key], key)
                             if self.dialect.engine_id == 'firebird' else
                             self._integer(options[key], key))
                    source += phrase + str(value)
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
            if self.dialect.engine_id == 'mariadb' and options.get('admin'):
                administrator = (
                    self._quote(options['admin'])
                    if options.get('admin_kind') == 'ROLE'
                    else self._account(options['admin'])
                )
                source += f' WITH ADMIN {administrator}'
            privileges = options.get('system_privileges')
            if privileges:
                source += ' SET SYSTEM PRIVILEGES TO ' + (
                    self._privilege_names(privileges)
                )
            members = options.get('members') or []
            if self.dialect.engine_id == 'firebird' and (
                    'description' in options):
                if members:
                    raise RelationalClientError(
                        'initial role members are unavailable for this engine')
                return [{'source': source, 'parameters': ()}] + (
                    self._firebird_role_comment(role, options))
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
        elif kind == 'database' and self.dialect.engine_id in {
                'mysql', 'mariadb'}:
            prefix = 'CREATE DATABASE'
            if (
                self.dialect.engine_id == 'mariadb' and
                options.get('or_replace')
            ):
                prefix = 'CREATE OR REPLACE DATABASE'
            if options.get('if_not_exists'):
                prefix += ' IF NOT EXISTS'
            source = f'{prefix} {qualified}'
            if options.get('character_set'):
                source += ' DEFAULT CHARACTER SET ' + self._quote(
                    options['character_set']
                )
            if options.get('collation'):
                source += ' DEFAULT COLLATE ' + self._quote(
                    options['collation']
                )
            if options.get('encryption'):
                source += ' DEFAULT ENCRYPTION ' + self._literal(
                    options['encryption']
                )
            if (
                self.dialect.engine_id == 'mariadb' and
                options.get('comment') is not None
            ):
                source += ' COMMENT = ' + self._literal(options['comment'])
        elif kind in {'database', 'schema'}:
            keyword = self._keyword(kind)
            source = f'CREATE {keyword} {qualified}'
        elif kind == 'domain':
            data_type = self._safe_fragment(options.get('data_type'), 'type')
            source = f'CREATE DOMAIN {qualified} AS {data_type}'
            if (
                self.dialect.engine_id == 'firebird' or
                self.dialect.sql_family == 'postgresql'
            ):
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
            add_keyword = (
                'ADD COLUMN'
                if self.dialect.engine_id == 'immudb' else 'ADD'
            )
            source = (
                f'ALTER TABLE {table} {add_keyword} '
                f'{self._column_definition(item)}'
            )
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
        if kind == 'database' and self.dialect.engine_id == 'sqlite':
            statements = []
            enums = {
                'journal_mode': {
                    'DELETE', 'TRUNCATE', 'PERSIST', 'MEMORY', 'WAL', 'OFF',
                },
                'synchronous': {'OFF', 'NORMAL', 'FULL', 'EXTRA'},
                'auto_vacuum': {'NONE', 'FULL', 'INCREMENTAL'},
            }
            for setting in ('journal_mode', 'synchronous', 'auto_vacuum'):
                value = changes.get(setting)
                if value in {None, ''}:
                    continue
                if value not in enums[setting]:
                    raise RelationalClientError(
                        f'SQLite {setting.replace("_", " ")} is invalid'
                    )
                statements.append({
                    'source': f'PRAGMA {setting} = {value}',
                    'parameters': (),
                })
            page_size = changes.get('page_size')
            if page_size not in {None, ''}:
                allowed_page_sizes = {
                    '512', '1024', '2048', '4096', '8192', '16384',
                    '32768', '65536',
                }
                if isinstance(page_size, bool) or str(page_size) not in (
                        allowed_page_sizes):
                    raise RelationalClientError(
                        'SQLite page size is invalid'
                    )
                page_size = int(page_size)
                statements.append({
                    'source': f'PRAGMA page_size = {page_size}',
                    'parameters': (),
                })
            for setting in ('application_id', 'user_version'):
                value = changes.get(setting)
                if value in {None, ''}:
                    continue
                value = self._integer(value, setting)
                if not -2147483648 <= value <= 2147483647:
                    raise RelationalClientError(
                        f'SQLite {setting.replace("_", " ")} is invalid'
                    )
                statements.append({
                    'source': f'PRAGMA {setting} = {value}',
                    'parameters': (),
                })
            if changes.get('run_vacuum') is True:
                statements.append({'source': 'VACUUM', 'parameters': ()})
            if not statements:
                raise RelationalClientError(
                    'SQLite database configuration has no changes'
                )
            return statements
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
            if changes.get('encryption'):
                clauses.append(
                    'DEFAULT ENCRYPTION ' + self._literal(
                        changes['encryption']
                    )
                )
            if changes.get('read_only'):
                clauses.append(
                    'READ ONLY = ' + (
                        '1' if changes['read_only'] == 'ON' else '0'
                    )
                )
            if (
                self.dialect.engine_id == 'mariadb' and
                changes.get('comment') is not None
            ):
                clauses.append(
                    'COMMENT = ' + self._literal(changes['comment'])
                )
            if not clauses:
                raise RelationalClientError(
                    'database alteration has no structured changes'
                )
            return [{
                'source': f'ALTER DATABASE {target} {" ".join(clauses)}',
                'parameters': (),
            }]
        if kind == 'database' and self.dialect.engine_id == 'firebird':
            clauses = []
            if changes.get('default_charset'):
                clauses.append(
                    'SET DEFAULT CHARACTER SET ' +
                    self._quote(changes['default_charset'])
                )
            if changes.get('drop_linger'):
                clauses.append('DROP LINGER')
            elif changes.get('linger_seconds') is not None:
                clauses.append(
                    'SET LINGER TO ' + str(self._integer(
                        changes['linger_seconds'], 'linger_seconds'
                    ))
                )
            if changes.get('default_sql_security'):
                security = changes['default_sql_security']
                if security not in {'DEFINER', 'INVOKER'}:
                    raise RelationalClientError(
                        'Firebird SQL security mode is invalid'
                    )
                clauses.append(f'SET DEFAULT SQL SECURITY {security}')
            if not clauses:
                raise RelationalClientError(
                    'Firebird database alteration has no structured changes'
                )
            return [{
                'source': 'ALTER DATABASE ' + ' '.join(clauses),
                'parameters': (),
            }]
        if kind == 'role' and self.dialect.engine_id == 'firebird':
            if ('drop_system_privileges' in changes and not isinstance(
                    changes['drop_system_privileges'], bool)):
                raise RelationalClientError(
                    'drop_system_privileges must be boolean')
            if changes.get('drop_system_privileges') and changes.get(
                    'system_privileges'):
                raise RelationalClientError(
                    'cannot replace and drop system privileges together')
            statements = []
            if changes.get('drop_system_privileges'):
                clause = 'DROP SYSTEM PRIVILEGES'
            elif changes.get('system_privileges'):
                clause = 'SET SYSTEM PRIVILEGES TO ' + (
                    self._privilege_names(changes.get('system_privileges'))
                )
            else:
                clause = None
            if clause:
                statements.append({'source': f'ALTER ROLE {target} {clause}',
                                   'parameters': ()})
            statements.extend(self._firebird_role_comment(target, changes))
            if not statements:
                raise RelationalClientError('Choose a role change')
            return statements
        if kind == 'index' and self.dialect.engine_id == 'firebird':
            target = self._quote(
                request['target_resource'].get('display_name')
            )
            statements = []
            for key in ('active', 'refresh_statistics'):
                if key in changes and not isinstance(changes[key], bool):
                    raise RelationalClientError(f'{key} must be boolean')
            if 'active' in changes:
                state = 'ACTIVE' if changes['active'] else 'INACTIVE'
                statements.append({'source': f'ALTER INDEX {target} {state}',
                                   'parameters': ()})
            if changes.get('refresh_statistics'):
                statements.append({'source': f'SET STATISTICS INDEX {target}',
                                   'parameters': ()})
            if not statements:
                raise RelationalClientError('Choose an index change')
            return statements
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
            firebird = self.dialect.engine_id == 'firebird'
            if firebird and changes.get('restart_initial'):
                if 'restart' in changes:
                    raise RelationalClientError(
                        'choose a next value or original start, not both')
                clauses.append('RESTART')
            if 'restart' in changes:
                value = (self._firebird_sequence_integer(
                    changes['restart'], 'restart') if firebird else
                    self._integer(changes['restart'], 'restart'))
                clauses.append(
                    'RESTART WITH ' + str(value)
                )
            if 'increment' in changes:
                value = (self._firebird_sequence_integer(
                    changes['increment'], 'increment') if firebird else
                    self._integer(changes['increment'], 'increment'))
                clauses.append(
                    'INCREMENT BY ' + str(value)
                )
            statements = [{
                'source': f'ALTER SEQUENCE {target} {" ".join(clauses)}',
                'parameters': (),
            }] if clauses else []
            if firebird and ('description' in changes or
                             changes.get('clear_description')):
                if changes.get('clear_description') and changes.get(
                        'description'):
                    raise RelationalClientError(
                        'choose a comment or remove comment, not both')
                description = (None if changes.get('clear_description') else
                               changes['description'])
                if description is not None and not isinstance(
                        description, str):
                    raise RelationalClientError('comment must be text')
                statements.append({
                    'source': f'COMMENT ON SEQUENCE {target} IS ' +
                    ('NULL' if description is None else
                     self._literal(description)),
                    'parameters': (),
                })
            if not statements:
                raise RelationalClientError(
                    'sequence alteration has no structured changes'
                )
            return statements
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
        if kind in {'view', 'materialized-view'}:
            query = self._query_body(draft.get('definition'))
            command = (
                'ALTER MATERIALIZED VIEW'
                if kind == 'materialized-view' else 'ALTER VIEW'
            )
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
            if self.dialect.engine_id == 'firebird':
                path = self._target_path(request['target_resource'])
                if len(path) != 1:
                    raise RelationalClientError('Firebird tables have no '
                                                'schema-qualified name')
                statements.extend(
                    {'source': sql, 'parameters': ()} for sql in
                    firebird_tables.alterations(path[0], changes))
            for item in changes.get('add_columns', []):
                add_keyword = (
                    'ADD COLUMN'
                    if self.dialect.engine_id == 'immudb' else 'ADD'
                )
                statements.append({
                    'source': (
                        f'ALTER TABLE {target} {add_keyword} '
                        f'{self._column_definition(item)}'
                    ),
                    'parameters': (),
                })
            for name in changes.get('drop_columns', []):
                drop_keyword = ('DROP' if
                                self.dialect.engine_id == 'firebird' else
                                'DROP COLUMN')
                statements.append({
                    'source': (
                        f'ALTER TABLE {target} {drop_keyword} '
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
        if kind == 'materialized-view' and (
                self.dialect.engine_id == 'mysql'):
            target = self._qualified(
                self._target_path(request['target_resource'])
            )
            return {'source': f'DROP VIEW {target}', 'parameters': ()}
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
        if cascade and self.dialect.engine_id == 'firebird':
            raise RelationalClientError(
                'Firebird does not support DROP CASCADE')
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
        if self.dialect.engine_id == 'firebird':
            return {'source': firebird_privileges.compile_privilege(
                request['operation_id'], draft), 'parameters': ()}
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
            principal = (
                self._quote(draft['principal'])
                if self.dialect.engine_id == 'mariadb' and
                draft.get('principal_kind') == 'ROLE'
                else self._account(draft['principal'])
            )
            object_prefix = ''
            if target_kind == 'GLOBAL':
                target_name = '*.*'
            elif target_kind == 'DATABASE':
                target_name = self._quote(draft.get('object_name')) + '.*'
            elif target_kind in {'FUNCTION', 'PROCEDURE'}:
                object_prefix = f'{target_kind} '
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

    def _firebird_role_comment(self, target, changes):
        clear = changes.get('clear_description', False)
        if not isinstance(clear, bool):
            raise RelationalClientError('clear_description must be boolean')
        if clear and changes.get('description'):
            raise RelationalClientError('Cannot set and remove a comment')
        if not clear and 'description' not in changes:
            return []
        value = None if clear else changes['description']
        if value is not None and not isinstance(value, str):
            raise RelationalClientError('Comment must be text')
        return [{'source': f'COMMENT ON ROLE {target} IS ' +
                 ('NULL' if value is None else self._literal(value)),
                 'parameters': ()}]

    def _compile_firebird_role(self, request):
        operation = request['operation_id']
        draft = request['draft']
        kind = draft.get('member_kind', 'USER')
        if kind not in {'USER', 'ROLE'}:
            raise RelationalClientError('Firebird member type is invalid')
        for field in ('default_role', 'admin_option', 'admin_option_only'):
            if field in draft and not isinstance(draft[field], bool):
                raise RelationalClientError(f'{field} must be boolean')
        role = self._quote((request.get('target_resource') or {}).get(
            'display_name'))
        member = self._quote(draft.get('member'))
        default = 'DEFAULT ' if draft.get('default_role') else ''
        if operation == 'grant':
            suffix = ' WITH ADMIN OPTION' if draft.get('admin_option') else ''
            source = f'GRANT {default}{role} TO {kind} {member}{suffix}'
        else:
            prefix = 'ADMIN OPTION FOR ' if draft.get(
                'admin_option_only') else ''
            source = f'REVOKE {prefix}{default}{role} FROM {kind} {member}'
        if draft.get('grantor'):
            source += f' GRANTED BY USER {self._quote(draft["grantor"])}'
        return {'source': source, 'parameters': ()}

    def _compile_mariadb_role(self, request):
        operation = request['operation_id']
        draft = request['draft']
        target = request.get('target_resource') or {}
        role = self._quote(target.get('display_name'))
        member = (
            self._quote(draft['member'])
            if draft.get('member_kind') == 'ROLE'
            else self._account(draft['member'])
        )
        if operation == 'grant':
            suffix = ' WITH ADMIN OPTION' if draft.get(
                'admin_option') else ''
            source = f'GRANT {role} TO {member}{suffix}'
        elif operation == 'revoke':
            prefix = 'ADMIN OPTION FOR ' if draft.get(
                'admin_option_only') else ''
            source = f'REVOKE {prefix}{role} FROM {member}'
        elif operation == 'set_default':
            source = f'SET DEFAULT ROLE {role} FOR {member}'
        else:
            raise RelationalClientError(
                'MariaDB role operation is unavailable'
            )
        return {'source': source, 'parameters': ()}

    def _compile_mariadb_replication(self, request):
        operation = request['operation_id']
        draft = request.get('draft', {})
        target = request.get('target_resource') or {}
        name = (
            draft.get('name') if operation == 'create'
            else target.get('display_name')
        )
        connection_name = self._literal(self._identifier(name))
        if operation in {'create', 'alter'}:
            clauses = []
            previews = []

            def add(keyword, value, formatter):
                if value is None or value == '' or value == 'UNCHANGED' or (
                        value == []):
                    return
                rendered = formatter(value)
                clauses.append(f'{keyword}={rendered}')
                previews.append(f'{keyword}={rendered}')

            for key, keyword in (
                ('master_host', 'MASTER_HOST'),
                ('master_user', 'MASTER_USER'),
                ('master_log_file', 'MASTER_LOG_FILE'),
                ('relay_log_file', 'RELAY_LOG_FILE'),
                ('ssl_ca', 'MASTER_SSL_CA'),
                ('ssl_ca_path', 'MASTER_SSL_CAPATH'),
                ('ssl_certificate', 'MASTER_SSL_CERT'),
                ('ssl_key', 'MASTER_SSL_KEY'),
                ('ssl_cipher', 'MASTER_SSL_CIPHER'),
                ('ssl_crl', 'MASTER_SSL_CRL'),
                ('ssl_crl_path', 'MASTER_SSL_CRLPATH'),
            ):
                add(keyword, draft.get(key), self._literal)
            for key, keyword in (
                ('master_port', 'MASTER_PORT'),
                ('connect_retry', 'MASTER_CONNECT_RETRY'),
                ('retry_count', 'MASTER_RETRY_COUNT'),
                ('replication_delay', 'MASTER_DELAY'),
                ('master_log_position', 'MASTER_LOG_POS'),
                ('relay_log_position', 'RELAY_LOG_POS'),
            ):
                add(keyword, draft.get(key), lambda value, label=key: str(
                    self._integer(value, label)
                ))
            heartbeat = draft.get('heartbeat_period')
            if heartbeat not in {None, ''}:
                if isinstance(heartbeat, bool) or not isinstance(
                        heartbeat, (int, float)):
                    raise RelationalClientError(
                        'MariaDB replication heartbeat is invalid'
                    )
                add('MASTER_HEARTBEAT_PERIOD', heartbeat, str)
            for key, keyword in (
                ('ignore_server_ids', 'IGNORE_SERVER_IDS'),
                ('do_domain_ids', 'DO_DOMAIN_IDS'),
                ('ignore_domain_ids', 'IGNORE_DOMAIN_IDS'),
            ):
                add(keyword, draft.get(key), lambda values: '(' + ', '.join(
                    str(self._integer(value, key)) for value in values
                ) + ')')
            for key, keyword in (
                ('master_ssl', 'MASTER_SSL'),
                ('verify_server_certificate',
                 'MASTER_SSL_VERIFY_SERVER_CERT'),
                ('demote_to_slave', 'MASTER_DEMOTE_TO_SLAVE'),
            ):
                add(keyword, draft.get(key), lambda value: (
                    '1' if value == 'ON' else '0'
                ))
            use_gtid = draft.get('use_gtid')
            if use_gtid not in {None, '', 'UNCHANGED'}:
                add('MASTER_USE_GTID', use_gtid, str)
            password = draft.get('master_password')
            if password not in {None, ''}:
                clauses.append('MASTER_PASSWORD=' + self._literal(password))
                previews.append('MASTER_PASSWORD=<redacted>')
            if not clauses:
                raise RelationalClientError(
                    'MariaDB replication change has no clauses'
                )
            prefix = f'CHANGE MASTER {connection_name} TO '
            return {
                'source': prefix + ', '.join(clauses),
                'preview_source': prefix + ', '.join(previews),
                'parameters': (),
            }
        if operation in {'start', 'stop'}:
            source = f'{operation.upper()} SLAVE {connection_name}'
            thread = draft.get('thread', 'ALL')
            if thread != 'ALL':
                source += f' {thread}'
            if operation == 'start':
                until = draft.get('until_mode', 'NONE')
                if until == 'MASTER_POSITION':
                    source += (
                        ' UNTIL MASTER_LOG_FILE=' +
                        self._literal(draft['until_log_file']) +
                        ', MASTER_LOG_POS=' + str(self._integer(
                            draft['until_log_position'], 'log position'
                        ))
                    )
                elif until == 'RELAY_POSITION':
                    source += (
                        ' UNTIL RELAY_LOG_FILE=' +
                        self._literal(draft['until_log_file']) +
                        ', RELAY_LOG_POS=' + str(self._integer(
                            draft['until_log_position'], 'log position'
                        ))
                    )
                elif until in {
                        'MASTER_GTID_POS', 'SQL_AFTER_GTIDS',
                        'SQL_BEFORE_GTIDS'}:
                    source += (
                        f' UNTIL {until}=' +
                        self._literal(draft['until_gtid'])
                    )
            return {'source': source, 'parameters': ()}
        if operation == 'reset':
            suffix = ' ALL' if draft.get('delete_connection', True) else ''
            return {
                'source': f'RESET SLAVE {connection_name}{suffix}',
                'parameters': (),
            }
        raise RelationalClientError(
            'MariaDB replication operation is unavailable'
        )

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
        engine = self.dialect.engine_id
        family = self.dialect.sql_family
        if engine == 'mariadb':
            account = self._account(name, options.get('host', '%'))
            clauses, previews = self._mariadb_account_clauses(
                options, creating=True
            )
            source = f'CREATE USER {account}'
            preview = source
            if clauses:
                source += ' ' + ' '.join(clauses)
                preview += ' ' + ' '.join(previews)
            return source, preview
        password = options.get('password')
        if not isinstance(password, str) or not password:
            raise RelationalClientError('user password is required')
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
        if engine == 'mariadb':
            clauses, previews = self._mariadb_account_clauses(
                changes, creating=False
            )
            if not clauses:
                raise RelationalClientError(
                    'MariaDB user alteration has no changes'
                )
            return (
                f'ALTER USER {user} {" ".join(clauses)}',
                f'ALTER USER {user} {" ".join(previews)}',
            )
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

    def _mariadb_account_clauses(self, options, creating):
        mode = options.get(
            'authentication_mode', 'PASSWORD' if creating else 'UNCHANGED'
        )
        clauses = []
        previews = []
        if mode != 'UNCHANGED':
            auth, auth_preview = self._mariadb_authentication(options, mode)
            if auth:
                clauses.append(auth)
                previews.append(auth_preview)
        tls = options.get(
            'tls_requirement', 'NONE' if creating else 'UNCHANGED'
        )
        if tls != 'UNCHANGED':
            if tls in {'NONE', 'SSL', 'X509'}:
                tls_clause = f'REQUIRE {tls}'
            elif tls == 'SPECIFIED':
                parts = []
                for key, keyword in (
                    ('x509_subject', 'SUBJECT'),
                    ('x509_issuer', 'ISSUER'),
                    ('tls_cipher', 'CIPHER'),
                ):
                    if options.get(key):
                        parts.append(
                            f'{keyword} {self._literal(options[key])}'
                        )
                tls_clause = 'REQUIRE ' + ' AND '.join(parts)
            else:
                raise RelationalClientError(
                    'MariaDB TLS requirement is invalid'
                )
            clauses.append(tls_clause)
            previews.append(tls_clause)
        limits = []
        for key, keyword in (
            ('max_queries_per_hour', 'MAX_QUERIES_PER_HOUR'),
            ('max_updates_per_hour', 'MAX_UPDATES_PER_HOUR'),
            ('max_connections_per_hour', 'MAX_CONNECTIONS_PER_HOUR'),
            ('max_user_connections', 'MAX_USER_CONNECTIONS'),
        ):
            if options.get(key) not in {None, ''}:
                limits.append(
                    f'{keyword} {self._integer(options[key], key)}'
                )
        if options.get('max_statement_time') not in {None, ''}:
            value = options['max_statement_time']
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise RelationalClientError(
                    'MariaDB maximum statement time is invalid'
                )
            limits.append(f'MAX_STATEMENT_TIME {value}')
        if limits:
            limit_clause = 'WITH ' + ' '.join(limits)
            clauses.append(limit_clause)
            previews.append(limit_clause)
        account_lock = options.get(
            'account_lock', 'UNLOCK' if creating else 'UNCHANGED'
        )
        if account_lock != 'UNCHANGED':
            clauses.append(f'ACCOUNT {account_lock}')
            previews.append(f'ACCOUNT {account_lock}')
        expiration = options.get(
            'password_expiration', 'DEFAULT' if creating else 'UNCHANGED'
        )
        if expiration != 'UNCHANGED':
            if expiration == 'NOW':
                expiry = 'PASSWORD EXPIRE'
            elif expiration in {'DEFAULT', 'NEVER'}:
                expiry = f'PASSWORD EXPIRE {expiration}'
            elif expiration == 'INTERVAL':
                days = self._integer(
                    options.get('password_expiration_days'),
                    'password expiration days',
                )
                expiry = f'PASSWORD EXPIRE INTERVAL {days} DAY'
            else:
                raise RelationalClientError(
                    'MariaDB password expiration is invalid'
                )
            clauses.append(expiry)
            previews.append(expiry)
        return clauses, previews

    def _mariadb_authentication(self, options, mode):
        if mode == 'NONE':
            primary = ''
            preview = ''
        elif mode == 'PASSWORD':
            password = options.get('password')
            primary = f'IDENTIFIED BY {self._literal(password)}'
            preview = 'IDENTIFIED BY <redacted>'
        else:
            plugin = self._quote(options.get('plugin'))
            primary = f'IDENTIFIED VIA {plugin}'
            preview = primary
            if mode == 'PLUGIN_PASSWORD':
                primary += (
                    ' USING PASSWORD('
                    f'{self._literal(options.get("password"))})'
                )
                preview += ' USING PASSWORD(<redacted>)'
            elif mode == 'PLUGIN_STRING':
                primary += ' USING ' + self._literal(
                    options.get('authentication_string')
                )
                preview += ' USING <redacted>'
            elif mode != 'PLUGIN_ONLY':
                raise RelationalClientError(
                    'MariaDB authentication mode is invalid'
                )
        additional = options.get('additional_authentication') or []
        for item in additional:
            plugin = self._quote(item['plugin'])
            method = plugin
            method_preview = plugin
            if item.get('password') is not None:
                method += (
                    ' USING PASSWORD(' + self._literal(item['password']) + ')'
                )
                method_preview += ' USING PASSWORD(<redacted>)'
            elif item.get('authentication_string') is not None:
                method += ' USING ' + self._literal(
                    item['authentication_string']
                )
                method_preview += ' USING <redacted>'
            if not primary:
                primary = f'IDENTIFIED VIA {method}'
                preview = f'IDENTIFIED VIA {method_preview}'
            else:
                primary += f' OR {method}'
                preview += f' OR {method_preview}'
        return primary, preview

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
        if self.dialect.engine_id == 'firebird':
            if any(not isinstance(value, str) or value not in
                   FIREBIRD_SYSTEM_PRIVILEGES for value in values):
                raise RelationalClientError(
                    'unknown Firebird 5.0 system privilege')
            if len(values) != len(set(values)):
                raise RelationalClientError('duplicate system privilege')
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
        if self.dialect.engine_id == 'firebird' and 'column_mode' in item:
            return firebird_columns.definition(item)
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
            self._identifier(item) for item in raw
            if self.dialect.sql_family == 'firebird' or item != 'current'
        )
        if not path:
            raise RelationalClientError('target resource path is empty')
        return path

    def _new_object_name(self, name, options):
        if self.dialect.engine_id == 'firebird':
            qualifiers = ('parent', 'schema', 'database')
            if any(options.get(key) for key in qualifiers):
                raise RelationalClientError(
                    'Firebird objects belong to the selected database; '
                    'database/schema qualifiers are not supported')
            return self._quote(name)
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
    def _firebird_sequence_integer(value, label):
        # Keep int64 values as decimal strings across the browser boundary.
        if isinstance(value, str) and re.fullmatch(r'-?[0-9]+', value):
            if len(value) > 21:
                raise RelationalClientError(f'{label} is outside its range')
            value = int(value)
        if isinstance(value, bool) or not isinstance(value, int):
            raise RelationalClientError(f'{label} must be an integer')
        bits = 32 if label == 'increment' else 64
        if not -(2 ** (bits - 1)) <= value < 2 ** (bits - 1):
            raise RelationalClientError(f'{label} is outside its range')
        if label == 'increment' and value == 0:
            raise RelationalClientError('increment must not be zero')
        return value

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
