##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Add consent, idempotency, and rollback markers for profile imports.

Revision ID: cde_profile_migration_v1
Revises: cde_endpoint_persistence_v1
Create Date: 2026-08-31

The migration creates empty CDEadmin-owned ledger tables. It never opens,
updates, attaches, or copies a pgAdmin source profile.
"""

from alembic import op
import sqlalchemy as sa


revision = 'cde_profile_migration_v1'
down_revision = 'cde_endpoint_persistence_v1'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'cde_profile_migration_run',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column(
            'user_id', sa.Integer(), sa.ForeignKey('user.id'),
            nullable=False
        ),
        sa.Column('source_profile_id', sa.String(64), nullable=False),
        sa.Column('source_snapshot_sha256', sa.String(64), nullable=False),
        sa.Column('source_schema_version', sa.Integer()),
        sa.Column('migration_version', sa.String(32), nullable=False),
        sa.Column('selected_categories', sa.Text(), nullable=False),
        sa.Column('status', sa.String(32), nullable=False),
        sa.Column('consent_reference', sa.String(256), nullable=False),
        sa.Column('summary', sa.Text(), nullable=False, server_default='{}'),
        sa.Column(
            'incompatibility_report', sa.Text(), nullable=False,
            server_default='{}'
        ),
        sa.Column(
            'created_at', sa.DateTime(), nullable=False,
            server_default=sa.func.now()
        ),
        sa.Column('completed_at', sa.DateTime()),
        sa.Column('rolled_back_at', sa.DateTime()),
        sa.UniqueConstraint(
            'user_id', 'source_profile_id', 'migration_version',
            name='uq_cde_profile_migration_source'
        ),
    )
    op.create_index(
        'ix_cde_profile_migration_run_status',
        'cde_profile_migration_run', ['user_id', 'status']
    )
    op.create_table(
        'cde_profile_migration_item',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column(
            'run_id', sa.String(36),
            sa.ForeignKey(
                'cde_profile_migration_run.id', ondelete='CASCADE'
            ),
            nullable=False
        ),
        sa.Column(
            'user_id', sa.Integer(), sa.ForeignKey('user.id'),
            nullable=False
        ),
        sa.Column('source_profile_id', sa.String(64), nullable=False),
        sa.Column('item_kind', sa.String(64), nullable=False),
        sa.Column('source_key', sa.String(256), nullable=False),
        sa.Column('item_fingerprint', sa.String(64), nullable=False),
        sa.Column('target_reference', sa.String(256), nullable=False),
        sa.Column(
            'created_target', sa.Boolean(), nullable=False,
            server_default=sa.false()
        ),
        sa.Column('status', sa.String(32), nullable=False),
        sa.Column(
            'created_at', sa.DateTime(), nullable=False,
            server_default=sa.func.now()
        ),
        sa.Column('rolled_back_at', sa.DateTime()),
        sa.UniqueConstraint(
            'user_id', 'source_profile_id', 'item_kind', 'source_key',
            name='uq_cde_profile_migration_item_source'
        ),
    )
    op.create_index(
        'ix_cde_profile_migration_item_run',
        'cde_profile_migration_item', ['run_id', 'status']
    )


def downgrade():
    op.drop_index(
        'ix_cde_profile_migration_item_run',
        table_name='cde_profile_migration_item'
    )
    op.drop_table('cde_profile_migration_item')
    op.drop_index(
        'ix_cde_profile_migration_run_status',
        table_name='cde_profile_migration_run'
    )
    op.drop_table('cde_profile_migration_run')
