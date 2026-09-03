"""YugabyteDB 2025.2.2.2 YSQL distributed provider."""

from dataclasses import replace
import json
import re

from pgadmin.cdeadmin.sdk import (
    ActualEnginePilotProvider,
    PilotProfile,
    RelationalClientError,
)
from ..distributed_control_plane import DistributedSQLControlPlane
from ..distributed_sql import (
    create_sql_client,
    optional_rows,
    postgresql_catalog,
    resource,
    sql_administration,
)
from ..relational_admin import RelationalAdministration
from .control_plane import (
    OPERATIONS as CONTROL_OPERATIONS,
    cancel_action,
    compile_action,
    execute_action,
    inspect_action,
    post_validate_action,
)


PROFILE = PilotProfile(
    'org.cdeadmin.yugabytedb', 'yugabytedb-native', 'yugabytedb',
    'YugabyteDB', '2025.2.2.2', 'postgresql_wire',
    'distributed-relational', 'ysql', 'Yugabyte SQL',
    'yugabytedb-distributed-transaction-native', 'tabular',
    (
        'universe', 'cluster', 'node', 'region', 'zone', 'database',
        'schema', 'table', 'column', 'index', 'constraint', 'sequence',
        'view', 'materialized-view', 'domain', 'type', 'function',
        'procedure', 'trigger', 'extension', 'partition', 'tablespace',
        'tablet', 'tserver', 'master', 'placement-policy', 'user', 'role',
        'privilege', 'changefeed', 'snapshot', 'schedule',
        'xcluster-replication',
    ),
    ('ysqlsh', 'yb-admin', 'backup-restore'),
    semantic_sql_dialect={
        'language_profile': 'ysql', 'quote_open': '"',
        'supports_rollup': True,
    },
)


_BASE_ADMINISTRATION = sql_administration('yugabytedb', 'postgresql', (
    'universe', 'node', 'region', 'zone', 'tablet', 'tserver', 'master',
    'placement-policy', 'changefeed', 'snapshot', 'schedule',
    'xcluster-replication',
))
_YSQL_SUPPORTED = dict(_BASE_ADMINISTRATION.dialect.supported)
_YSQL_SUPPORTED.update({
    'materialized-view': frozenset({
        'inspect', 'create', 'rename', 'drop',
    }),
    'domain': frozenset({'inspect', 'create', 'rename', 'drop'}),
    'type': frozenset({'inspect', 'create', 'rename', 'drop'}),
    'function': frozenset({'inspect', 'create', 'drop'}),
    'procedure': frozenset({'inspect', 'create', 'drop'}),
    'trigger': frozenset({'inspect', 'create', 'drop'}),
    'extension': frozenset({'inspect', 'create', 'drop'}),
    # Partition bounds are part of CREATE TABLE in YSQL. Until the table form
    # has a complete structured bound editor, partitions remain inspect-only.
    'partition': frozenset({'inspect'}),
    # YugabyteDB 2025.2.2.2 rejects ALTER TABLESPACE ... RENAME TO as not
    # supported. Keep the provider surface aligned with the exact runtime.
    'tablespace': frozenset({'inspect', 'create', 'drop'}),
})
_YSQL_DIALECT = replace(
    _BASE_ADMINISTRATION.dialect,
    supported=_YSQL_SUPPORTED,
    concept_resource_kinds={
        'servers': ('universe',),
        'replication_objects': ('changefeed', 'xcluster-replication'),
        'jobs_and_events': ('schedule',),
    },
)


_CONTROL_ADMINISTRATION = DistributedSQLControlPlane(
    _YSQL_DIALECT, CONTROL_OPERATIONS, compile_action,
    inspector=inspect_action, canceller=cancel_action,
    post_validator=post_validate_action, action_executor=execute_action,
)


class YugabyteDBAdministration(RelationalAdministration):
    """Combine YSQL object administration with native cluster controls."""

    def _form(self, kind, operation):
        if operation == 'create' and kind == 'materialized-view':
            return {
                'form_id': 'materialized-view.create',
                'title': 'Create materialized view',
                'fields': [
                    self._field('name', 'Name', 'text', True),
                    self._field('parent', 'Parent schema', 'text', False),
                    self._field(
                        'query', 'Materialized query', 'code', True,
                        'Enter one SELECT or WITH query.',
                    ),
                    self._field('with_data', 'Populate now', 'boolean',
                                default=True),
                ],
            }
        if operation == 'create' and kind == 'type':
            return {
                'form_id': 'type.create', 'title': 'Create YSQL type',
                'fields': [
                    self._field('name', 'Name', 'text', True),
                    self._field('parent', 'Parent schema', 'text', False),
                    self._field('type_kind', 'Type kind', 'select', True,
                                options=('ENUM', 'COMPOSITE')),
                    self._field('enum_values', 'Enum values', 'json', False,
                                default=[]),
                    self._field('fields', 'Composite fields', 'json', False,
                                default=[]),
                ],
            }
        if operation == 'create' and kind == 'extension':
            return {
                'form_id': 'extension.create',
                'title': 'Install YSQL extension',
                'fields': [
                    self._field('name', 'Extension name', 'text', True),
                    self._field('schema', 'Install schema', 'text'),
                    self._field('version', 'Version', 'text'),
                    self._field('cascade', 'Install dependencies', 'boolean',
                                default=False),
                ],
            }
        if operation == 'create' and kind == 'tablespace':
            return {
                'form_id': 'tablespace.create',
                'title': 'Create YSQL placement tablespace',
                'fields': [
                    self._field('name', 'Name', 'text', True),
                    self._field(
                        'replica_placement', 'Replica placement', 'json',
                        True, 'YSQL num_replicas and placement_blocks object.',
                    ),
                ],
            }
        return super()._form(kind, operation)

    def _compile_create(self, request):
        kind = request['resource_kind']
        draft = request['draft']
        options = draft.get('options') or {}
        name = self._identifier(draft['name'])
        qualified = self._new_object_name(name, options)
        if kind == 'materialized-view':
            query = self._query_body(draft.get('definition'))
            suffix = '' if options.get('with_data', True) else ' WITH NO DATA'
            return [{
                'source': f'CREATE MATERIALIZED VIEW {qualified} AS '
                          f'{query}{suffix}',
                'parameters': (),
            }]
        if kind == 'type':
            type_kind = str(options.get('type_kind', '')).upper()
            if type_kind == 'ENUM':
                values = options.get('enum_values')
                if not isinstance(values, list) or not values:
                    raise RelationalClientError(
                        'YSQL enum type requires values'
                    )
                definition = 'ENUM (' + ', '.join(
                    self._literal(str(item)) for item in values
                ) + ')'
            elif type_kind == 'COMPOSITE':
                fields = options.get('fields')
                if not isinstance(fields, list) or not fields:
                    raise RelationalClientError(
                        'YSQL composite type requires fields'
                    )
                definition = '(' + ', '.join(
                    f'{self._quote(self._identifier(item["name"]))} '
                    f'{self._safe_fragment(item["type"], "field type")}'
                    for item in fields
                ) + ')'
            else:
                raise RelationalClientError('YSQL type kind is invalid')
            return [{
                'source': f'CREATE TYPE {qualified} AS {definition}',
                'parameters': (),
            }]
        if kind == 'extension':
            source = f'CREATE EXTENSION {self._quote(name)}'
            schema = options.get('schema')
            version = options.get('version')
            clauses = []
            if schema:
                clauses.append('SCHEMA ' + self._quote(
                    self._identifier(schema)
                ))
            if version:
                clauses.append('VERSION ' + self._literal(str(version)))
            if options.get('cascade'):
                clauses.append('CASCADE')
            if clauses:
                source += ' WITH ' + ' '.join(clauses)
            return [{'source': source, 'parameters': ()}]
        if kind == 'tablespace':
            placement = options.get('replica_placement')
            if not isinstance(placement, dict):
                raise RelationalClientError(
                    'YSQL replica placement must be an object'
                )
            replicas = placement.get('num_replicas')
            blocks = placement.get('placement_blocks')
            if (
                isinstance(replicas, bool) or not isinstance(replicas, int) or
                replicas < 1 or not isinstance(blocks, list) or not blocks
            ):
                raise RelationalClientError(
                    'YSQL replica placement is incomplete'
                )
            for block in blocks:
                if not isinstance(block, dict) or not all(
                    isinstance(block.get(key), str) and block[key]
                    for key in ('cloud', 'region', 'zone')
                ):
                    raise RelationalClientError(
                        'YSQL placement block is invalid'
                    )
                minimum = block.get('min_num_replicas')
                if (
                    isinstance(minimum, bool) or
                    not isinstance(minimum, int) or minimum < 1
                ):
                    raise RelationalClientError(
                        'YSQL placement block replica count is invalid'
                    )
            serialized = json.dumps(
                placement, separators=(',', ':'), sort_keys=True,
            )
            return [{
                'source': (
                    f'CREATE TABLESPACE {self._quote(name)} WITH '
                    f'(replica_placement={self._literal(serialized)})'
                ),
                'parameters': (),
            }]
        return super()._compile_create(request)

    def supports(self, resource_kind, operation_id):
        return _CONTROL_ADMINISTRATION.control_plane.supports(
            resource_kind, operation_id
        ) or super().supports(resource_kind, operation_id)

    def catalog(self, catalog):
        return _CONTROL_ADMINISTRATION.control_plane.apply(
            super().catalog(catalog)
        )

    def validate(self, request):
        if _CONTROL_ADMINISTRATION.control_plane.supports(
                request.get('resource_kind'), request.get('operation_id')):
            return _CONTROL_ADMINISTRATION.validate(request)
        return super().validate(request)

    def plan(self, request):
        if _CONTROL_ADMINISTRATION.control_plane.supports(
                request.get('resource_kind'), request.get('operation_id')):
            return _CONTROL_ADMINISTRATION.plan(request)
        return super().plan(request)

    def apply(self, client, request):
        plan = request.get('plan') or {}
        if _CONTROL_ADMINISTRATION.control_plane.supports(
                plan.get('resource_kind'), plan.get('operation_id')):
            return _CONTROL_ADMINISTRATION.apply(client, request)
        return super().apply(client, request)

    def inspect_operation(self, client, request):
        return _CONTROL_ADMINISTRATION.inspect_operation(client, request)

    def cancel_operation(self, client, request):
        return _CONTROL_ADMINISTRATION.cancel_operation(client, request)

    def validate_operation_post_state(self, client, request):
        return _CONTROL_ADMINISTRATION.validate_operation_post_state(
            client, request
        )


ADMINISTRATION = YugabyteDBAdministration(_YSQL_DIALECT)


def _version(row):
    value = str(row[0] if row else '')
    match = re.search(
        r'(?:YugabyteDB[^\n]*?v?|YB-)(\d+\.\d+\.\d+\.\d+)',
        value,
        re.I,
    )
    if match is None:
        raise RelationalClientError('YugabyteDB version is unavailable')
    return match.group(1)


def _extras(cursor, _request, generation):
    values = [resource('universe', [], 'YugabyteDB', generation)]
    excluded = "('pg_catalog', 'information_schema')"
    for schema, table, name, constraint_type in optional_rows(
        cursor,
        'SELECT constraint_schema, table_name, constraint_name, '
        'constraint_type FROM information_schema.table_constraints '
        f'WHERE constraint_schema NOT IN {excluded} ORDER BY 1, 2, 3',
    ):
        values.append(resource(
            'constraint', [schema, table], name, generation,
            {'constraint_type': str(constraint_type)},
        ))
    for schema, name in optional_rows(
        cursor,
        'SELECT sequence_schema, sequence_name '
        'FROM information_schema.sequences ORDER BY 1, 2',
    ):
        values.append(resource('sequence', [schema], name, generation))
    for schema, name in optional_rows(
        cursor,
        'SELECT schemaname, matviewname FROM pg_catalog.pg_matviews '
        f'WHERE schemaname NOT IN {excluded} ORDER BY 1, 2',
    ):
        values.append(resource(
            'materialized-view', [schema], name, generation
        ))
    for schema, name, type_kind in optional_rows(
        cursor,
        'SELECT n.nspname, t.typname, t.typtype '
        'FROM pg_catalog.pg_type t JOIN pg_catalog.pg_namespace n '
        'ON n.oid = t.typnamespace LEFT JOIN pg_catalog.pg_class c '
        'ON c.oid = t.typrelid WHERE t.typisdefined '
        f'AND n.nspname NOT IN {excluded} AND ('
        "t.typtype IN ('d', 'e') OR (t.typtype = 'c' AND "
        "c.relkind = 'c')) ORDER BY 1, 2",
    ):
        kind = 'domain' if str(type_kind) == 'd' else 'type'
        values.append(resource(
            kind, [schema], name, generation,
            {'type_kind': str(type_kind)},
        ))
    for schema, name, routine_kind, arguments in optional_rows(
        cursor,
        'SELECT n.nspname, p.proname, p.prokind, '
        'pg_catalog.pg_get_function_identity_arguments(p.oid) '
        'FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n '
        'ON n.oid = p.pronamespace '
        f'WHERE n.nspname NOT IN {excluded} '
        "AND p.prokind IN ('f', 'p') ORDER BY 1, 2, 4",
    ):
        kind = 'procedure' if str(routine_kind) == 'p' else 'function'
        values.append(resource(kind, [schema], name, generation, {
            'identity_arguments': str(arguments),
        }))
    for schema, table, name in optional_rows(
        cursor,
        'SELECT event_object_schema, event_object_table, trigger_name '
        'FROM information_schema.triggers '
        f'WHERE event_object_schema NOT IN {excluded} ORDER BY 1, 2, 3',
    ):
        values.append(resource(
            'trigger', [schema, table], name, generation
        ))
    for name, version, schema in optional_rows(
        cursor,
        'SELECT e.extname, e.extversion, n.nspname '
        'FROM pg_catalog.pg_extension e JOIN pg_catalog.pg_namespace n '
        'ON n.oid = e.extnamespace ORDER BY e.extname',
    ):
        values.append(resource('extension', [], name, generation, {
            'version': str(version), 'schema': str(schema),
        }))
    for schema, parent, child, bound in optional_rows(
        cursor,
        'SELECT pn.nspname, pc.relname, cc.relname, '
        'pg_catalog.pg_get_expr(cc.relpartbound, cc.oid) '
        'FROM pg_catalog.pg_inherits i '
        'JOIN pg_catalog.pg_class pc ON pc.oid = i.inhparent '
        'JOIN pg_catalog.pg_class cc ON cc.oid = i.inhrelid '
        'JOIN pg_catalog.pg_namespace pn ON pn.oid = pc.relnamespace '
        f'WHERE pn.nspname NOT IN {excluded} ORDER BY 1, 2, 3',
    ):
        values.append(resource(
            'partition', [schema, parent], child, generation,
            {'bound': str(bound)},
        ))
    for name, owner, options in optional_rows(
        cursor,
        'SELECT s.spcname, r.rolname, s.spcoptions '
        'FROM pg_catalog.pg_tablespace s JOIN pg_catalog.pg_roles r '
        'ON r.oid = s.spcowner '
        "WHERE s.spcname NOT IN ('pg_default', 'pg_global') ORDER BY 1",
    ):
        values.append(resource('tablespace', [], name, generation, {
            'owner': str(owner), 'options': list(options or ()),
        }))
    for name, can_login in optional_rows(
        cursor,
        'SELECT rolname, rolcanlogin FROM pg_catalog.pg_roles ORDER BY 1',
    ):
        values.append(resource('role', [], name, generation, {
            'can_login': bool(can_login),
        }))
        if can_login:
            values.append(resource('user', [], name, generation, {
                'can_login': True,
            }))
    for grantee, schema, table, privilege in optional_rows(
        cursor,
        'SELECT grantee, table_schema, table_name, privilege_type '
        'FROM information_schema.table_privileges '
        f'WHERE table_schema NOT IN {excluded} ORDER BY 1, 2, 3, 4',
    ):
        display = f'{grantee}:{privilege}:{schema}.{table}'
        values.append(resource(
            'privilege', [schema, table, str(grantee)], display,
            generation, {'privilege_type': str(privilege)},
        ))
    rows = optional_rows(
        cursor,
        'SELECT host, port, num_connections, node_type, cloud, region, zone '
        'FROM yb_servers() ORDER BY host, port',
    )
    for host, port, connections, node_type, cloud, region, zone in rows:
        name = f'{host}:{port}'
        native = {
            'connections': connections, 'node_type': str(node_type),
            'cloud': str(cloud), 'region': str(region), 'zone': str(zone),
        }
        values.append(resource('tserver', [], name, generation, native))
        values.append(resource('node', [], name, generation, native))
        if region:
            values.append(resource('region', [], region, generation))
        if zone:
            values.append(resource('zone', [str(region)], zone, generation))
    return values


class YugabyteDBProvider(ActualEnginePilotProvider):
    def __init__(self, context, permissions, client):
        super().__init__(context, permissions, client, PROFILE)


def create_provider(context, permissions, client=None):
    return YugabyteDBProvider(
        context,
        permissions,
        client or create_sql_client(
            PROFILE,
            permissions,
            wire='postgresql',
            version_query='SELECT version()',
            version_parser=_version,
            metadata_reader=lambda connection, request: postgresql_catalog(
                connection, request, 'YugabyteDB', _extras
            ),
            administration=ADMINISTRATION,
            pool_namespace=context.pool_namespace,
        ),
    )
