##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Move legacy route databases beneath their owning server endpoints.

Revision ID: cde_split_route_database_targets_v1
Revises: cde_workspace_transfer_v1
Create Date: 2026-09-06

An endpoint route identifies a server address.  A database is a separately
managed child target, even for providers whose client library normally accepts
both values in one DSN.  This data migration removes the last combined records
created before that distinction became mandatory.
"""

import json
from pathlib import PurePath
import uuid

from alembic import op
import sqlalchemy as sa


revision = 'cde_split_route_database_targets_v1'
down_revision = 'cde_workspace_transfer_v1'
branch_labels = None
depends_on = None


def _configuration(value):
    try:
        result = json.loads(value or '{}')
    except (TypeError, ValueError):
        return None
    return result if isinstance(result, dict) else None


def _display_name(database):
    value = str(database).strip()
    path_name = PurePath(value).name
    return (path_name or value)[-256:]


def upgrade():
    bind = op.get_bind()
    endpoints = sa.table(
        'cde_endpoint',
        sa.column('id', sa.String),
        sa.column('legacy_server_id', sa.Integer),
        sa.column('profile_generation', sa.String),
    )
    routes = sa.table(
        'cde_endpoint_route',
        sa.column('id', sa.String),
        sa.column('endpoint_id', sa.String),
        sa.column('priority', sa.Integer),
        sa.column('configuration', sa.Text),
    )
    targets = sa.table(
        'cde_endpoint_database_target',
        sa.column('id', sa.String),
        sa.column('endpoint_id', sa.String),
        sa.column('display_name', sa.String),
        sa.column('database', sa.Text),
        sa.column('configuration', sa.Text),
        sa.column('active', sa.Boolean),
    )
    servers = sa.table(
        'server',
        sa.column('id', sa.Integer),
        sa.column('maintenance_db', sa.String),
    )

    for endpoint in bind.execute(sa.select(endpoints)).mappings():
        endpoint_routes = list(bind.execute(
            sa.select(routes).where(
                routes.c.endpoint_id == endpoint['id']
            ).order_by(routes.c.priority, routes.c.id)
        ).mappings())
        existing_rows = list(bind.execute(
            sa.select(targets).where(
                targets.c.endpoint_id == endpoint['id']
            )
        ).mappings())
        existing = {row['database'] for row in existing_rows}
        has_active = any(bool(row['active']) for row in existing_rows)
        migrated = False

        for route in endpoint_routes:
            configuration = _configuration(route['configuration'])
            if configuration is None or 'database' not in configuration:
                continue
            database_value = configuration.pop('database')
            database = (
                str(database_value).strip()
                if database_value is not None else ''
            )
            if database and database not in existing:
                bind.execute(targets.insert().values(
                    id=str(uuid.uuid4()),
                    endpoint_id=endpoint['id'],
                    display_name=_display_name(database),
                    database=database,
                    configuration='{}',
                    active=not has_active,
                ))
                existing.add(database)
                has_active = True
            bind.execute(routes.update().where(
                routes.c.id == route['id']
            ).values(configuration=json.dumps(
                configuration, sort_keys=True, separators=(',', ':')
            )))
            migrated = True

        if migrated:
            bind.execute(endpoints.update().where(
                endpoints.c.id == endpoint['id']
            ).values(profile_generation=str(uuid.uuid4())))
            if endpoint['legacy_server_id'] is not None:
                bind.execute(servers.update().where(
                    servers.c.id == endpoint['legacy_server_id']
                ).values(maintenance_db=None))


def downgrade():
    # The split representation may have acquired multiple database children
    # after upgrade.  Recombining them into one route would be destructive and
    # ambiguous, so this data-only normalization is intentionally one-way.
    pass
