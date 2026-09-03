##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Authenticated CDEadmin Resource Explorer and Data Studio facade."""

from .service import (
    APP_EXTENSION_KEY,
    ProviderWorkspaceError,
    ProviderWorkspaceService,
    init_app,
    service_for_app,
)


__all__ = (
    'APP_EXTENSION_KEY',
    'ProviderWorkspaceError',
    'ProviderWorkspaceService',
    'init_app',
    'service_for_app',
)
