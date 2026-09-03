##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Provider-driven visual administration contracts and catalog loader."""

from .catalog import (
    PORTFOLIO_ENGINE_IDS,
    VisualAdminCatalogError,
    catalog_for_engine,
    portfolio_summary,
)
from .provider import (
    ProviderVisualAdministration,
    VisualAdminAccessError,
    VisualAdminError,
    VisualAdminExecutionError,
    VisualAdminValidationError,
)
from .control_plane import (
    CONTROL_PLANE_PERMISSIONS,
    ControlPlaneCatalog,
    ControlPlaneCatalogError,
    ControlPlaneOperation,
    field as control_plane_field,
)


__all__ = (
    'PORTFOLIO_ENGINE_IDS',
    'ProviderVisualAdministration',
    'VisualAdminAccessError',
    'VisualAdminCatalogError',
    'VisualAdminError',
    'VisualAdminExecutionError',
    'VisualAdminValidationError',
    'CONTROL_PLANE_PERMISSIONS',
    'ControlPlaneCatalog',
    'ControlPlaneCatalogError',
    'ControlPlaneOperation',
    'control_plane_field',
    'catalog_for_engine',
    'portfolio_summary',
)
