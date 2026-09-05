##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Separate server routes from endpoint database targets.

Revision ID: cde_endpoint_database_targets_v1
Revises: cde_report_scheduler_v1
"""

from alembic import op
import sqlalchemy as sa


revision = 'cde_endpoint_database_targets_v1'
down_revision = 'cde_report_scheduler_v1'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'cde_endpoint_database_target',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('endpoint_id', sa.String(length=36), nullable=False),
        sa.Column('display_name', sa.String(length=256), nullable=False),
        sa.Column('database', sa.Text(), nullable=False),
        sa.Column('configuration', sa.Text(), nullable=False,
                  server_default='{}'),
        sa.Column('active', sa.Boolean(), nullable=False,
                  server_default=sa.false()),
        sa.Column('created_at', sa.DateTime(), nullable=False,
                  server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), nullable=False,
                  server_default=sa.func.now()),
        sa.CheckConstraint(
            'active >= 0 AND active <= 1',
            name='ck_cde_endpoint_database_target_active'
        ),
        sa.ForeignKeyConstraint(
            ['endpoint_id'], ['cde_endpoint.id'], ondelete='CASCADE'
        ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'endpoint_id', 'database',
            name='uq_cde_endpoint_database_target'
        ),
    )
    op.create_index(
        'ix_cde_endpoint_database_target_endpoint',
        'cde_endpoint_database_target', ['endpoint_id', 'active']
    )


def downgrade():
    op.drop_index(
        'ix_cde_endpoint_database_target_endpoint',
        table_name='cde_endpoint_database_target'
    )
    op.drop_table('cde_endpoint_database_target')
