"""YugabyteDB 2025.2.2.2 YSQL distributed provider."""

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
    'distributed-multimodel-relational', 'ysql', 'Yugabyte SQL',
    'yugabytedb-distributed-transaction-native', 'tabular',
    (
        'universe', 'cluster', 'node', 'region', 'zone', 'database',
        'keyspace', 'schema', 'table', 'column', 'index', 'constraint',
        'sequence', 'view', 'type', 'tablet', 'tserver', 'master',
        'placement-policy', 'user', 'role', 'privilege', 'changefeed',
        'snapshot', 'schedule', 'xcluster-replication',
    ),
    ('ysqlsh', 'ycqlsh', 'yb-admin', 'backup-restore'),
    semantic_sql_dialect={
        'language_profile': 'ysql', 'quote_open': '"',
        'supports_rollup': True,
    },
)


_BASE_ADMINISTRATION = sql_administration('yugabytedb', 'postgresql', (
    'universe', 'node', 'region', 'zone', 'keyspace', 'type', 'tablet',
    'tserver', 'master', 'placement-policy', 'changefeed', 'snapshot',
    'schedule', 'xcluster-replication',
))


_CONTROL_ADMINISTRATION = DistributedSQLControlPlane(
    _BASE_ADMINISTRATION.dialect, CONTROL_OPERATIONS, compile_action,
    inspector=inspect_action, canceller=cancel_action,
    post_validator=post_validate_action, action_executor=execute_action,
)


class YugabyteDBAdministration(RelationalAdministration):
    """Combine YSQL object administration with native cluster controls."""

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


ADMINISTRATION = YugabyteDBAdministration(_BASE_ADMINISTRATION.dialect)


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
