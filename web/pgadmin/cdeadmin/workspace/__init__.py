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
from .transfer import (
    APP_EXTENSION_KEY as TRANSFER_APP_EXTENSION_KEY,
    WorkspaceTransferConflict,
    WorkspaceTransferError,
    WorkspaceTransferExpired,
    WorkspaceTransferNotFound,
    WorkspaceTransferRepository,
    WorkspaceTransferService,
    service_for_app as transfer_service_for_app,
    validate_descriptor,
)
from .assets import (
    APP_EXTENSION_KEY as PROJECT_ASSET_APP_EXTENSION_KEY,
    ProjectAssetConflict,
    ProjectAssetError,
    ProjectAssetForbidden,
    ProjectAssetNotFound,
    ProjectAssetRepository,
    ProjectAssetService,
)


__all__ = (
    'APP_EXTENSION_KEY',
    'TRANSFER_APP_EXTENSION_KEY',
    'PROJECT_ASSET_APP_EXTENSION_KEY',
    'ProviderWorkspaceError',
    'ProviderWorkspaceService',
    'ProjectAssetConflict',
    'ProjectAssetError',
    'ProjectAssetForbidden',
    'ProjectAssetNotFound',
    'ProjectAssetRepository',
    'ProjectAssetService',
    'WorkspaceTransferConflict',
    'WorkspaceTransferError',
    'WorkspaceTransferExpired',
    'WorkspaceTransferNotFound',
    'WorkspaceTransferRepository',
    'WorkspaceTransferService',
    'init_app',
    'service_for_app',
    'transfer_service_for_app',
    'validate_descriptor',
)
