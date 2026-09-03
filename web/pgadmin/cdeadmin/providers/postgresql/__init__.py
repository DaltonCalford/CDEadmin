##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Preserved PostgreSQL provider for CDEadmin."""

from .provider import PostgreSQLProvider, create_provider


__all__ = ('PostgreSQLProvider', 'create_provider')
