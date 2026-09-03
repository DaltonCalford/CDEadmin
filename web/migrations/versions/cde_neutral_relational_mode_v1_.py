##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Collapse compatibility-listener rows onto advertised engine profiles.

Revision ID: cde_neutral_relational_mode_v1
Revises: cde_profile_migration_v1
Create Date: 2026-09-01

The former compatibility-listener mode exposed server implementation in
product identity. Existing development rows are normalized to legacy-native,
which now means an endpoint presenting a non-ScratchBird engine profile. The
provider cannot distinguish a reference server from another implementation
presenting the same protocol and profile.
"""

from alembic import op
import sqlalchemy as sa


revision = 'cde_neutral_relational_mode_v1'
down_revision = 'cde_profile_migration_v1'
branch_labels = None
depends_on = None

OLD_COMPATIBILITY_MODE = 'scratchbird_emulated_legacy'
PROFILE_MODE = 'legacy_native'


def upgrade():
    connection = op.get_bind()
    endpoint = sa.table(
        'cde_endpoint',
        sa.column('endpoint_mode', sa.String(40)),
    )
    connection.execute(
        endpoint.update().where(
            endpoint.c.endpoint_mode == OLD_COMPATIBILITY_MODE
        ).values(endpoint_mode=PROFILE_MODE)
    )


def downgrade():
    # The original implementation distinction is intentionally not
    # reconstructed. Older schemas already admit the former value, while new
    # schemas retain the stricter two-mode constraint.
    pass
