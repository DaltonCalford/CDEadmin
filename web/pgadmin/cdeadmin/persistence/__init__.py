##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Additive endpoint persistence and legacy compatibility helpers."""

from .legacy import endpoint_identity_for_legacy, endpoint_profile_for_legacy

__all__ = [
    'endpoint_identity_for_legacy',
    'endpoint_profile_for_legacy',
]
