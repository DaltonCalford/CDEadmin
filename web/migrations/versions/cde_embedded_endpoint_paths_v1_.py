##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Allow absolute embedded database paths in endpoint registrations.

Revision ID: cde_embedded_endpoint_paths_v1
Revises: cde_neutral_relational_mode_v1
Create Date: 2026-09-01
"""

from alembic import op
import sqlalchemy as sa


revision = 'cde_embedded_endpoint_paths_v1'
down_revision = 'cde_neutral_relational_mode_v1'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('server') as batch_op:
        batch_op.alter_column(
            'maintenance_db',
            existing_type=sa.String(length=64),
            type_=sa.String(length=1024),
            existing_nullable=True,
        )


def downgrade():
    with op.batch_alter_table('server') as batch_op:
        batch_op.alter_column(
            'maintenance_db',
            existing_type=sa.String(length=1024),
            type_=sa.String(length=64),
            existing_nullable=True,
        )
