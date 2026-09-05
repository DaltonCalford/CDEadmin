##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Add delegated report scheduler grants, credentials, and occurrences.

Revision ID: cde_report_scheduler_v1
Revises: cde_report_delivery_v1
Create Date: 2026-09-04
"""

from alembic import op
import sqlalchemy as sa


revision = 'cde_report_scheduler_v1'
down_revision = 'cde_report_delivery_v1'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'cde_report_delegation',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('endpoint_id', sa.String(length=36), nullable=False),
        sa.Column('model_id', sa.String(length=36), nullable=False),
        sa.Column('report_id', sa.String(length=128), nullable=False),
        sa.Column('schedule_id', sa.String(length=128), nullable=False),
        sa.Column('route_id', sa.String(length=128), nullable=False),
        sa.Column('primary_secret_kind', sa.String(length=128), nullable=True),
        sa.Column('endpoint_generation', sa.String(length=64), nullable=False),
        sa.Column('model_revision', sa.Integer(), nullable=False),
        sa.Column('definition_digest', sa.String(length=64), nullable=False),
        sa.Column('delivery_scope', sa.Text(), nullable=False),
        sa.Column('state', sa.String(length=16), nullable=False),
        sa.Column('credential_generation', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.Column('expires_at', sa.DateTime(), nullable=False),
        sa.Column('revoked_at', sa.DateTime(), nullable=True),
        sa.CheckConstraint(
            "state IN ('active', 'revoked', 'expired')",
            name='ck_cde_report_delegation_state',
        ),
        sa.ForeignKeyConstraint(
            ['endpoint_id'], ['cde_endpoint.id'], ondelete='CASCADE'
        ),
        sa.ForeignKeyConstraint(
            ['model_id'], ['cde_semantic_model.id'], ondelete='CASCADE'
        ),
        sa.ForeignKeyConstraint(
            ['user_id'], ['user.id'], ondelete='CASCADE'
        ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'user_id', 'endpoint_id', 'model_id', 'report_id',
            name='uq_cde_report_delegation_scope',
        ),
    )
    op.create_table(
        'cde_report_delegated_credential',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('delegation_id', sa.String(length=36), nullable=False),
        sa.Column('secret_kind', sa.String(length=128), nullable=False),
        sa.Column('key_id', sa.String(length=64), nullable=False),
        sa.Column('encrypted_value', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ['delegation_id'], ['cde_report_delegation.id'],
            ondelete='CASCADE'
        ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'delegation_id', 'secret_kind',
            name='uq_cde_report_delegated_secret_kind',
        ),
    )
    op.create_table(
        'cde_report_schedule_occurrence',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('delegation_id', sa.String(length=36), nullable=False),
        sa.Column('scheduled_for', sa.DateTime(), nullable=False),
        sa.Column('state', sa.String(length=32), nullable=False),
        sa.Column('claim_token_digest', sa.String(length=64), nullable=True),
        sa.Column('claimed_by', sa.String(length=128), nullable=True),
        sa.Column('lease_expires_at', sa.DateTime(), nullable=True),
        sa.Column('phase', sa.String(length=32), nullable=False),
        sa.Column('progress', sa.Text(), nullable=False),
        sa.Column('delivery_occurrence_ids', sa.Text(), nullable=True),
        sa.Column('error_type', sa.String(length=128), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.Column('completed_at', sa.DateTime(), nullable=True),
        sa.CheckConstraint(
            "state IN ('scheduled', 'claimed', 'executing', 'delivering', "
            "'delivered', 'failed', 'cancel_requested', 'cancelled', "
            "'outcome_unknown')",
            name='ck_cde_report_schedule_state',
        ),
        sa.ForeignKeyConstraint(
            ['delegation_id'], ['cde_report_delegation.id'],
            ondelete='CASCADE'
        ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'delegation_id', 'scheduled_for',
            name='uq_cde_report_schedule_instant',
        ),
    )


def downgrade():
    op.drop_table('cde_report_schedule_occurrence')
    op.drop_table('cde_report_delegated_credential')
    op.drop_table('cde_report_delegation')
