##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Add authoritative workspace and cross-window transfer state.

Revision ID: cde_workspace_transfer_v1
Revises: cde_endpoint_database_targets_v1
Create Date: 2026-09-05
"""

from alembic import op
import sqlalchemy as sa


revision = 'cde_workspace_transfer_v1'
down_revision = 'cde_endpoint_database_targets_v1'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'cde_workspace',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('workspace_key', sa.String(length=256), nullable=False),
        sa.Column('name', sa.String(length=128), nullable=False),
        sa.Column('revision', sa.Integer(), nullable=False,
                  server_default='0'),
        sa.Column('layout_reference', sa.String(length=256), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False,
                  server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), nullable=False,
                  server_default=sa.func.now()),
        sa.ForeignKeyConstraint(
            ['user_id'], ['user.id'], ondelete='CASCADE'
        ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'user_id', 'name', name='uq_cde_workspace_owner_name'
        ),
        sa.UniqueConstraint(
            'user_id', 'workspace_key', name='uq_cde_workspace_owner_key'
        ),
    )
    op.create_index(
        'ix_cde_workspace_owner', 'cde_workspace', ['user_id', 'updated_at']
    )
    op.create_table(
        'cde_workspace_window',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('workspace_id', sa.String(length=36), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('window_key', sa.String(length=256), nullable=False),
        sa.Column('role', sa.String(length=32), nullable=False,
                  server_default='main'),
        sa.Column('device_profile_id', sa.String(length=128), nullable=True),
        sa.Column('display_fingerprint', sa.String(length=256),
                  nullable=True),
        sa.Column('placement', sa.Text(), nullable=False,
                  server_default='{}'),
        sa.Column('revision', sa.Integer(), nullable=False,
                  server_default='0'),
        sa.Column('clean_close', sa.Boolean(), nullable=False,
                  server_default=sa.false()),
        sa.Column('last_seen_at', sa.DateTime(), nullable=False,
                  server_default=sa.func.now()),
        sa.Column('created_at', sa.DateTime(), nullable=False,
                  server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), nullable=False,
                  server_default=sa.func.now()),
        sa.CheckConstraint(
            "role IN ('main', 'detached-tool', 'secondary-workspace')",
            name='ck_cde_workspace_window_role'
        ),
        sa.ForeignKeyConstraint(
            ['user_id'], ['user.id'], ondelete='CASCADE'
        ),
        sa.ForeignKeyConstraint(
            ['workspace_id'], ['cde_workspace.id'], ondelete='CASCADE'
        ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'workspace_id', 'window_key',
            name='uq_cde_workspace_window_scope'
        ),
    )
    op.create_index(
        'ix_cde_workspace_window_owner', 'cde_workspace_window',
        ['user_id', 'workspace_id', 'last_seen_at']
    )
    op.create_table(
        'cde_tool_instance',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('workspace_id', sa.String(length=36), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('tool_key', sa.String(length=256), nullable=False),
        sa.Column('tool_kind', sa.String(length=64), nullable=False),
        sa.Column('descriptor_schema', sa.String(length=64), nullable=False),
        sa.Column('descriptor', sa.Text(), nullable=False),
        sa.Column('restore_reference', sa.String(length=256), nullable=False),
        sa.Column('window_id', sa.String(length=36), nullable=False),
        sa.Column('dock_area', sa.String(length=256), nullable=False),
        sa.Column('tab_order', sa.Integer(), nullable=False,
                  server_default='0'),
        sa.Column('placement_mode', sa.String(length=16), nullable=False),
        sa.Column('placement_revision', sa.Integer(), nullable=False,
                  server_default='0'),
        sa.Column('checkpoint_revision', sa.Integer(), nullable=False,
                  server_default='0'),
        sa.Column('dirty', sa.Boolean(), nullable=False,
                  server_default=sa.false()),
        sa.Column('transaction_state', sa.String(length=32), nullable=False,
                  server_default='unknown'),
        sa.Column('connection_state', sa.String(length=32), nullable=False,
                  server_default='unknown'),
        sa.Column('created_at', sa.DateTime(), nullable=False,
                  server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), nullable=False,
                  server_default=sa.func.now()),
        sa.CheckConstraint(
            "placement_mode IN ('docked', 'floating', 'detached')",
            name='ck_cde_tool_placement_mode'
        ),
        sa.ForeignKeyConstraint(
            ['user_id'], ['user.id'], ondelete='CASCADE'
        ),
        sa.ForeignKeyConstraint(
            ['window_id'], ['cde_workspace_window.id'], ondelete='CASCADE'
        ),
        sa.ForeignKeyConstraint(
            ['workspace_id'], ['cde_workspace.id'], ondelete='CASCADE'
        ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'workspace_id', 'restore_reference',
            name='uq_cde_tool_restore_reference'
        ),
        sa.UniqueConstraint(
            'workspace_id', 'tool_key', name='uq_cde_tool_scope'
        ),
    )
    op.create_index(
        'ix_cde_tool_owner_workspace', 'cde_tool_instance',
        ['user_id', 'workspace_id', 'window_id']
    )
    op.create_table(
        'cde_tool_checkpoint',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('tool_instance_id', sa.String(length=36), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('revision', sa.Integer(), nullable=False),
        sa.Column('checkpoint_reference', sa.String(length=256),
                  nullable=False),
        sa.Column('view_state', sa.Text(), nullable=False,
                  server_default='{}'),
        sa.Column('created_at', sa.DateTime(), nullable=False,
                  server_default=sa.func.now()),
        sa.ForeignKeyConstraint(
            ['tool_instance_id'], ['cde_tool_instance.id'],
            ondelete='CASCADE'
        ),
        sa.ForeignKeyConstraint(
            ['user_id'], ['user.id'], ondelete='CASCADE'
        ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'tool_instance_id', 'revision',
            name='uq_cde_tool_checkpoint_revision'
        ),
    )
    op.create_table(
        'cde_workspace_move_token',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('token_digest', sa.String(length=64), nullable=False),
        sa.Column('workspace_id', sa.String(length=36), nullable=False),
        sa.Column('tool_instance_id', sa.String(length=36), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('source_window_id', sa.String(length=36), nullable=False),
        sa.Column('destination_window_id', sa.String(length=36),
                  nullable=True),
        sa.Column('source_revision', sa.Integer(), nullable=False),
        sa.Column('checkpoint_revision', sa.Integer(), nullable=False),
        sa.Column('destination_placement', sa.Text(), nullable=False),
        sa.Column('idempotency_key', sa.String(length=128), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False,
                  server_default='prepared'),
        sa.Column('expires_at', sa.DateTime(), nullable=False),
        sa.Column('acknowledged_at', sa.DateTime(), nullable=True),
        sa.Column('committed_at', sa.DateTime(), nullable=True),
        sa.Column('aborted_at', sa.DateTime(), nullable=True),
        sa.Column('failure_reason', sa.String(length=256), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False,
                  server_default=sa.func.now()),
        sa.CheckConstraint(
            "status IN ('prepared', 'acknowledged', 'committed', "
            "'aborted', 'expired')",
            name='ck_cde_workspace_move_token_status'
        ),
        sa.ForeignKeyConstraint(
            ['tool_instance_id'], ['cde_tool_instance.id'],
            ondelete='CASCADE'
        ),
        sa.ForeignKeyConstraint(
            ['user_id'], ['user.id'], ondelete='CASCADE'
        ),
        sa.ForeignKeyConstraint(
            ['workspace_id'], ['cde_workspace.id'], ondelete='CASCADE'
        ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'token_digest', name='uq_cde_workspace_move_digest'
        ),
        sa.UniqueConstraint(
            'user_id', 'idempotency_key',
            name='uq_cde_workspace_move_idempotency'
        ),
    )
    op.create_index(
        'ix_cde_workspace_move_active', 'cde_workspace_move_token',
        ['user_id', 'tool_instance_id', 'status', 'expires_at']
    )


def downgrade():
    op.drop_index(
        'ix_cde_workspace_move_active',
        table_name='cde_workspace_move_token'
    )
    op.drop_table('cde_workspace_move_token')
    op.drop_table('cde_tool_checkpoint')
    op.drop_index(
        'ix_cde_tool_owner_workspace', table_name='cde_tool_instance'
    )
    op.drop_table('cde_tool_instance')
    op.drop_index(
        'ix_cde_workspace_window_owner',
        table_name='cde_workspace_window'
    )
    op.drop_table('cde_workspace_window')
    op.drop_index('ix_cde_workspace_owner', table_name='cde_workspace')
    op.drop_table('cde_workspace')
