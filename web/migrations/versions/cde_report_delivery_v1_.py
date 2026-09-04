##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Add durable report delivery occurrences.

Revision ID: cde_report_delivery_v1
Revises: cde_semantic_models_v1
Create Date: 2026-09-04
"""

from alembic import op
import sqlalchemy as sa


revision = 'cde_report_delivery_v1'
down_revision = 'cde_semantic_models_v1'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'cde_report_delivery_occurrence',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('request_key', sa.String(length=36), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('endpoint_id', sa.String(length=36), nullable=False),
        sa.Column('result_id', sa.String(length=128), nullable=False),
        sa.Column('profile_id', sa.String(length=128), nullable=False),
        sa.Column('channel', sa.String(length=16), nullable=False),
        sa.Column('export_format', sa.String(length=16), nullable=False),
        sa.Column('intent_digest', sa.String(length=64), nullable=False),
        sa.Column('target_summary', sa.Text(), nullable=False),
        sa.Column('state', sa.String(length=32), nullable=False),
        sa.Column('provider_receipt', sa.Text(), nullable=True),
        sa.Column('error_type', sa.String(length=128), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('started_at', sa.DateTime(), nullable=True),
        sa.Column('completed_at', sa.DateTime(), nullable=True),
        sa.CheckConstraint(
            "state IN ('prepared', 'delivering', 'delivered', 'failed', "
            "'outcome_unknown')",
            name='ck_cde_report_delivery_state',
        ),
        sa.CheckConstraint(
            "channel IN ('smtp', 's3')",
            name='ck_cde_report_delivery_channel',
        ),
        sa.CheckConstraint(
            "export_format IN ('csv', 'json', 'jsonl', 'xlsx', 'svg', "
            "'pdf')",
            name='ck_cde_report_delivery_format',
        ),
        sa.ForeignKeyConstraint(
            ['endpoint_id'], ['cde_endpoint.id'], ondelete='CASCADE'
        ),
        sa.ForeignKeyConstraint(
            ['user_id'], ['user.id'], ondelete='CASCADE'
        ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'user_id', 'endpoint_id', 'request_key',
            name='uq_cde_report_delivery_request',
        ),
    )


def downgrade():
    op.drop_table('cde_report_delivery_occurrence')
