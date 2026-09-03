##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Add endpoint-scoped semantic models and immutable revisions.

Revision ID: cde_semantic_models_v1
Revises: cde_embedded_endpoint_paths_v1
Create Date: 2026-09-02
"""

from alembic import op
import sqlalchemy as sa


revision = 'cde_semantic_models_v1'
down_revision = 'cde_embedded_endpoint_paths_v1'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'cde_semantic_model',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('endpoint_id', sa.String(length=36), nullable=False),
        sa.Column('name', sa.String(length=256), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('revision', sa.Integer(), nullable=False),
        sa.Column('definition', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "status IN ('draft', 'published', 'archived')",
            name='ck_cde_semantic_model_status',
        ),
        sa.ForeignKeyConstraint(['endpoint_id'], ['cde_endpoint.id'],
                                ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['user.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'endpoint_id', 'name',
                            name='uq_cde_semantic_model_name'),
    )
    op.create_table(
        'cde_semantic_model_revision',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('model_id', sa.String(length=36), nullable=False),
        sa.Column('revision', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('definition', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['model_id'], ['cde_semantic_model.id'],
                                ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('model_id', 'revision',
                            name='uq_cde_semantic_model_revision'),
    )


def downgrade():
    op.drop_table('cde_semantic_model_revision')
    op.drop_table('cde_semantic_model')
