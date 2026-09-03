##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Add versioned CDEadmin endpoint persistence beside legacy servers.

Revision ID: cde_endpoint_persistence_v1
Revises: normalize_locked_text_default
Create Date: 2026-08-31

The migration is deliberately additive. It does not modify Server or
SharedServer rows and never reads protected secret values or their presence.
A legacy registration becomes an unverified PostgreSQL/legacy-native endpoint;
runtime and provider versions remain NULL until a provider verifies them.
"""

from __future__ import annotations

import json
import uuid

from alembic import op
import sqlalchemy as sa


revision = 'cde_endpoint_persistence_v1'
down_revision = 'normalize_locked_text_default'
branch_labels = None
depends_on = None

MIGRATION_NAMESPACE = uuid.UUID('9001b03f-b5c4-5d91-a20e-bfea16b57438')
BASELINE_EVIDENCE = 'pgadmin4-9.17-bc58657d3d3a-20260831'
LEGACY_MODE = 'legacy_native'


def _stable_id(*parts) -> str:
    """Return a deterministic UUID without exposing legacy field values."""
    return str(uuid.uuid5(MIGRATION_NAMESPACE, ':'.join(map(str, parts))))


def _namespace_id(endpoint_id: str, endpoint_mode: str, purpose: str) -> str:
    """Derive isolated namespaces from endpoint identity, mode and purpose."""
    return _stable_id('namespace', endpoint_id, endpoint_mode, purpose)


def _legacy_rows(connection, table_name):
    """Read legacy identity only, never protected fields or their presence."""
    inspector = sa.inspect(connection)
    if not inspector.has_table(table_name):
        return []
    table = sa.Table(table_name, sa.MetaData(), autoload_with=connection)
    statement = sa.select(
        table.c.id,
        table.c.user_id,
    ).order_by(table.c.id)
    return list(connection.execute(statement).mappings())


def _insert_rows(connection, table_name, rows):
    if not rows:
        return
    table = sa.table(
        table_name,
        *(sa.column(name) for name in rows[0])
    )
    connection.execute(table.insert(), rows)


def _migrated_rows(source_kind, source_rows):
    endpoints = []
    runtimes = []
    routes = []
    secret_references = []
    tls_profiles = []
    evidence = []
    extensions = []
    legacy_server_field = (
        'legacy_server_id'
        if source_kind == 'server' else 'legacy_shared_server_id'
    )
    for source in source_rows:
        legacy_id = source['id']
        endpoint_id = _stable_id('endpoint', source_kind, legacy_id)
        source_reference = f'{source_kind}:{legacy_id}'
        endpoint = {
            'id': endpoint_id,
            'user_id': source['user_id'],
            'legacy_server_id': None,
            'legacy_shared_server_id': None,
            'experience_family': 'postgresql',
            'endpoint_mode': LEGACY_MODE,
            'provider_id': 'org.pgadmin.postgresql',
            'provider_version': None,
            'profile_id': 'postgresql-unverified-migrated',
            'profile_version': None,
            'profile_generation': None,
            'target_adapter_id': 'legacy-pgadmin-server',
            'target_adapter_version': None,
            'pool_namespace': _namespace_id(
                endpoint_id, LEGACY_MODE, 'pool'
            ),
            'session_namespace': _namespace_id(
                endpoint_id, LEGACY_MODE, 'session'
            ),
            'cache_namespace': _namespace_id(
                endpoint_id, LEGACY_MODE, 'cache'
            ),
            'diagnostic_namespace': _namespace_id(
                endpoint_id, LEGACY_MODE, 'diagnostic'
            ),
            'created_from': f'legacy_{source_kind}',
        }
        endpoint[legacy_server_field] = legacy_id
        endpoints.append(endpoint)
        runtimes.append({
            'endpoint_id': endpoint_id,
            'declared_runtime_family': 'postgresql',
            'declared_runtime_version': None,
            'verified_runtime_family': None,
            'verified_runtime_version': None,
            'verification_state': 'unverified',
            'verified_at': None,
            'verification_evidence_reference': None,
        })
        routes.append({
            'id': _stable_id('route', endpoint_id, 0),
            'endpoint_id': endpoint_id,
            'route_kind': 'legacy_registration',
            'route_reference': source_reference,
            'priority': 0,
            'configuration': '{}',
        })
        tls_profiles.append({
            'endpoint_id': endpoint_id,
            'tls_mode': 'legacy_inherited',
            'configuration_reference': (
                f'{source_reference}:connection_params'
            ),
        })
        evidence.append({
            'id': _stable_id('evidence', endpoint_id, 'migration'),
            'endpoint_id': endpoint_id,
            'evidence_kind': 'migration_snapshot',
            'evidence_reference': BASELINE_EVIDENCE,
            'snapshot_data': json.dumps({
                'legacy_id': legacy_id,
                'legacy_kind': source_kind,
                'runtime_verification': 'unverified',
            }, sort_keys=True, separators=(',', ':')),
            'expires_at': None,
        })
        extensions.append({
            'endpoint_id': endpoint_id,
            'schema_reference': 'cdeadmin.endpoint.extensions.v1',
            'profile_data': '{}',
            'redaction_state': 'no_legacy_payload_copied',
        })
        secret_columns = (
            ('database_password', 'password'),
            ('tunnel_password', 'tunnel_password'),
        )
        for secret_kind, column in secret_columns:
            secret_references.append({
                'id': _stable_id(
                    'secret-reference', endpoint_id, secret_kind
                ),
                'endpoint_id': endpoint_id,
                'secret_kind': secret_kind,
                'storage_kind': 'legacy_protected_column',
                'secret_reference': f'{source_reference}:{column}',
            })
    return {
        'cde_endpoint': endpoints,
        'cde_endpoint_runtime_identity': runtimes,
        'cde_endpoint_route': routes,
        'cde_endpoint_secret_reference': secret_references,
        'cde_endpoint_tls_profile': tls_profiles,
        'cde_endpoint_evidence_snapshot': evidence,
        'cde_endpoint_extension_profile': extensions,
    }


def _create_tables():
    op.create_table(
        'cde_endpoint',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('user.id')),
        sa.Column(
            'legacy_server_id', sa.Integer(),
            sa.ForeignKey('server.id', ondelete='CASCADE'), unique=True
        ),
        sa.Column(
            'legacy_shared_server_id', sa.Integer(),
            sa.ForeignKey('sharedserver.id', ondelete='CASCADE'), unique=True
        ),
        sa.Column('experience_family', sa.String(128), nullable=False),
        sa.Column('endpoint_mode', sa.String(40), nullable=False),
        sa.Column('provider_id', sa.String(128), nullable=False),
        sa.Column('provider_version', sa.String(64)),
        sa.Column('profile_id', sa.String(128), nullable=False),
        sa.Column('profile_version', sa.String(64)),
        sa.Column('profile_generation', sa.String(64)),
        sa.Column('target_adapter_id', sa.String(128), nullable=False),
        sa.Column('target_adapter_version', sa.String(64)),
        sa.Column('pool_namespace', sa.String(36), nullable=False,
                  unique=True),
        sa.Column('session_namespace', sa.String(36), nullable=False,
                  unique=True),
        sa.Column('cache_namespace', sa.String(36), nullable=False,
                  unique=True),
        sa.Column('diagnostic_namespace', sa.String(36), nullable=False,
                  unique=True),
        sa.Column('created_from', sa.String(32), nullable=False),
        sa.CheckConstraint(
            "endpoint_mode IN ('legacy_native', 'scratchbird_native')",
            name='ck_cde_endpoint_mode'
        ),
        sa.CheckConstraint(
            'NOT (legacy_server_id IS NOT NULL AND '
            'legacy_shared_server_id IS NOT NULL)',
            name='ck_cde_endpoint_legacy_source'
        ),
    )
    op.create_index('ix_cde_endpoint_user_id', 'cde_endpoint', ['user_id'])
    op.create_index(
        'ix_cde_endpoint_provider_mode', 'cde_endpoint',
        ['provider_id', 'endpoint_mode']
    )
    op.create_table(
        'cde_endpoint_runtime_identity',
        sa.Column(
            'endpoint_id', sa.String(36),
            sa.ForeignKey('cde_endpoint.id', ondelete='CASCADE'),
            primary_key=True
        ),
        sa.Column('declared_runtime_family', sa.String(128), nullable=False),
        sa.Column('declared_runtime_version', sa.String(64)),
        sa.Column('verified_runtime_family', sa.String(128)),
        sa.Column('verified_runtime_version', sa.String(64)),
        sa.Column('verification_state', sa.String(32), nullable=False),
        sa.Column('verified_at', sa.DateTime()),
        sa.Column('verification_evidence_reference', sa.String(256)),
    )
    op.create_table(
        'cde_endpoint_route',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column(
            'endpoint_id', sa.String(36),
            sa.ForeignKey('cde_endpoint.id', ondelete='CASCADE'),
            nullable=False
        ),
        sa.Column('route_kind', sa.String(64), nullable=False),
        sa.Column('route_reference', sa.String(256), nullable=False),
        sa.Column('priority', sa.Integer(), nullable=False),
        sa.Column('configuration', sa.Text(), nullable=False),
        sa.UniqueConstraint(
            'endpoint_id', 'priority', name='uq_cde_endpoint_route_priority'
        ),
    )
    op.create_table(
        'cde_endpoint_secret_reference',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column(
            'endpoint_id', sa.String(36),
            sa.ForeignKey('cde_endpoint.id', ondelete='CASCADE'),
            nullable=False
        ),
        sa.Column('secret_kind', sa.String(64), nullable=False),
        sa.Column('storage_kind', sa.String(64), nullable=False),
        sa.Column('secret_reference', sa.String(256), nullable=False),
        sa.UniqueConstraint(
            'endpoint_id', 'secret_kind',
            name='uq_cde_endpoint_secret_kind'
        ),
    )
    op.create_table(
        'cde_endpoint_tls_profile',
        sa.Column(
            'endpoint_id', sa.String(36),
            sa.ForeignKey('cde_endpoint.id', ondelete='CASCADE'),
            primary_key=True
        ),
        sa.Column('tls_mode', sa.String(64), nullable=False),
        sa.Column('configuration_reference', sa.String(256), nullable=False),
    )
    op.create_table(
        'cde_endpoint_evidence_snapshot',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column(
            'endpoint_id', sa.String(36),
            sa.ForeignKey('cde_endpoint.id', ondelete='CASCADE'),
            nullable=False
        ),
        sa.Column('evidence_kind', sa.String(64), nullable=False),
        sa.Column('evidence_reference', sa.String(256), nullable=False),
        sa.Column('snapshot_data', sa.Text(), nullable=False),
        sa.Column('expires_at', sa.DateTime()),
    )
    op.create_table(
        'cde_endpoint_extension_profile',
        sa.Column(
            'endpoint_id', sa.String(36),
            sa.ForeignKey('cde_endpoint.id', ondelete='CASCADE'),
            primary_key=True
        ),
        sa.Column('schema_reference', sa.String(256), nullable=False),
        sa.Column('profile_data', sa.Text(), nullable=False),
        sa.Column('redaction_state', sa.String(64), nullable=False),
    )


def _create_compatibility_view():
    op.execute(
        'CREATE VIEW cde_endpoint_legacy_compat AS '
        "SELECT id AS endpoint_id, 'server' AS legacy_kind, "
        'legacy_server_id AS legacy_id, endpoint_mode, provider_id, '
        'profile_id, target_adapter_id, pool_namespace, session_namespace, '
        'cache_namespace, diagnostic_namespace FROM cde_endpoint '
        'WHERE legacy_server_id IS NOT NULL UNION ALL '
        "SELECT id AS endpoint_id, 'sharedserver' AS legacy_kind, "
        'legacy_shared_server_id AS legacy_id, endpoint_mode, provider_id, '
        'profile_id, target_adapter_id, pool_namespace, session_namespace, '
        'cache_namespace, diagnostic_namespace FROM cde_endpoint '
        'WHERE legacy_shared_server_id IS NOT NULL'
    )


def upgrade():
    _create_tables()
    connection = op.get_bind()
    combined = {}
    for source_kind in ('server', 'sharedserver'):
        migrated = _migrated_rows(
            source_kind, _legacy_rows(connection, source_kind)
        )
        for table_name, rows in migrated.items():
            combined.setdefault(table_name, []).extend(rows)
    table_order = (
        'cde_endpoint',
        'cde_endpoint_runtime_identity',
        'cde_endpoint_route',
        'cde_endpoint_secret_reference',
        'cde_endpoint_tls_profile',
        'cde_endpoint_evidence_snapshot',
        'cde_endpoint_extension_profile',
    )
    for table_name in table_order:
        _insert_rows(connection, table_name, combined.get(table_name, []))
    _create_compatibility_view()


def downgrade():
    op.execute('DROP VIEW IF EXISTS cde_endpoint_legacy_compat')
    for table_name in (
        'cde_endpoint_extension_profile',
        'cde_endpoint_evidence_snapshot',
        'cde_endpoint_tls_profile',
        'cde_endpoint_secret_reference',
        'cde_endpoint_route',
        'cde_endpoint_runtime_identity',
        'cde_endpoint',
    ):
        op.drop_table(table_name)
