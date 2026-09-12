##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Add project, membership, asset, and immutable asset revisions.

Revision ID: cde_project_assets_v1
Revises: cde_split_route_database_targets_v1
Create Date: 2026-09-11
"""

from alembic import op
import sqlalchemy as sa


revision = 'cde_project_assets_v1'
down_revision = 'cde_split_route_database_targets_v1'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'cde_project',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('project_key', sa.String(length=256), nullable=False),
        sa.Column('name', sa.String(length=128), nullable=False),
        sa.Column('description', sa.Text(), nullable=False,
                  server_default=''),
        sa.Column('classification_reference', sa.String(length=256),
                  nullable=True),
        sa.Column('permission_reference', sa.String(length=256),
                  nullable=True),
        sa.Column('source_control_eligible', sa.Boolean(), nullable=False,
                  server_default=sa.true()),
        sa.Column('revision', sa.Integer(), nullable=False,
                  server_default='0'),
        sa.Column('created_at', sa.DateTime(), nullable=False,
                  server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), nullable=False,
                  server_default=sa.func.now()),
        sa.ForeignKeyConstraint(['user_id'], ['user.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('project_key', name='uq_cde_project_key'),
        sa.UniqueConstraint('user_id', 'name',
                            name='uq_cde_project_owner_name'),
    )
    op.create_index('ix_cde_project_owner', 'cde_project',
                    ['user_id', 'updated_at'])

    op.create_table(
        'cde_project_member',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('project_id', sa.String(length=36), nullable=False),
        sa.Column('principal_type', sa.String(length=16), nullable=False),
        sa.Column('principal_id', sa.Integer(), nullable=False),
        sa.Column('access_level', sa.String(length=16), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False,
                  server_default=sa.func.now()),
        sa.CheckConstraint("principal_type IN ('user', 'role')",
                           name='ck_cde_project_member_principal_type'),
        sa.CheckConstraint("access_level IN ('viewer', 'editor', 'manager')",
                           name='ck_cde_project_member_access_level'),
        sa.ForeignKeyConstraint(['project_id'], ['cde_project.id'],
                                ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('project_id', 'principal_type', 'principal_id',
                            name='uq_cde_project_member_principal'),
    )
    op.create_index('ix_cde_project_member_principal', 'cde_project_member',
                    ['principal_type', 'principal_id', 'project_id'])

    op.create_table(
        'cde_project_asset',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('project_id', sa.String(length=36), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('asset_key', sa.String(length=256), nullable=False),
        sa.Column('asset_type', sa.String(length=64), nullable=False),
        sa.Column('name', sa.String(length=256), nullable=False),
        sa.Column('path', sa.String(length=1024), nullable=False),
        sa.Column('schema_name', sa.String(length=128), nullable=False),
        sa.Column('schema_version', sa.Integer(), nullable=False,
                  server_default='1'),
        sa.Column('version', sa.Integer(), nullable=False,
                  server_default='0'),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('asset_metadata', sa.Text(), nullable=False,
                  server_default='{}'),
        sa.Column('dependency_references', sa.Text(), nullable=False,
                  server_default='[]'),
        sa.Column('resource_bindings', sa.Text(), nullable=False,
                  server_default='[]'),
        sa.Column('permission_reference', sa.String(length=256),
                  nullable=True),
        sa.Column('classification_reference', sa.String(length=256),
                  nullable=True),
        sa.Column('source_control_eligible', sa.Boolean(), nullable=False,
                  server_default=sa.true()),
        sa.Column('editor_capable', sa.Boolean(), nullable=False,
                  server_default=sa.true()),
        sa.Column('viewer_capable', sa.Boolean(), nullable=False,
                  server_default=sa.true()),
        sa.Column('validation_state', sa.String(length=16), nullable=False,
                  server_default='unknown'),
        sa.Column('validation_details', sa.Text(), nullable=False,
                  server_default='[]'),
        sa.Column('created_at', sa.DateTime(), nullable=False,
                  server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), nullable=False,
                  server_default=sa.func.now()),
        sa.CheckConstraint(
            "validation_state IN ('unknown', 'valid', 'warning', 'invalid')",
            name='ck_cde_project_asset_validation_state'),
        sa.ForeignKeyConstraint(['project_id'], ['cde_project.id'],
                                ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['user.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('project_id', 'asset_key',
                            name='uq_cde_project_asset_key'),
        sa.UniqueConstraint('project_id', 'path',
                            name='uq_cde_project_asset_path'),
    )
    op.create_index('ix_cde_project_asset_owner', 'cde_project_asset',
                    ['user_id', 'project_id', 'asset_type', 'updated_at'])

    op.create_table(
        'cde_project_asset_revision',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('asset_id', sa.String(length=36), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('schema_name', sa.String(length=128), nullable=False),
        sa.Column('schema_version', sa.Integer(), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('asset_metadata', sa.Text(), nullable=False),
        sa.Column('dependency_references', sa.Text(), nullable=False),
        sa.Column('resource_bindings', sa.Text(), nullable=False),
        sa.Column('validation_state', sa.String(length=16), nullable=False),
        sa.Column('validation_details', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False,
                  server_default=sa.func.now()),
        sa.ForeignKeyConstraint(['asset_id'], ['cde_project_asset.id'],
                                ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['user.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('asset_id', 'version',
                            name='uq_cde_project_asset_revision'),
    )
    op.create_index('ix_cde_project_asset_revision_owner',
                    'cde_project_asset_revision',
                    ['user_id', 'asset_id', 'version'])


def downgrade():
    op.drop_index('ix_cde_project_asset_revision_owner',
                  table_name='cde_project_asset_revision')
    op.drop_table('cde_project_asset_revision')
    op.drop_index('ix_cde_project_asset_owner',
                  table_name='cde_project_asset')
    op.drop_table('cde_project_asset')
    op.drop_index('ix_cde_project_member_principal',
                  table_name='cde_project_member')
    op.drop_table('cde_project_member')
    op.drop_index('ix_cde_project_owner', table_name='cde_project')
    op.drop_table('cde_project')
