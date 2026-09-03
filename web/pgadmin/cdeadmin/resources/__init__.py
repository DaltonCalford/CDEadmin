##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""CDEadmin common resource graph and explorer services."""

from __future__ import annotations

from .graph import (
    GenerationAwareResourceCache,
    ResourceContributionRegistry,
    ResourceExplorerService,
)
from .models import (
    ExplorerAction,
    ExplorerBadge,
    ExplorerNode,
    ResourceAccessError,
    ResourceCommandContribution,
    ResourceGraphError,
    ResourceInspectorContribution,
    ResourcePage,
    ResourceRef,
    StaleResourceGenerationError,
)


APP_EXTENSION_KEY = 'cdeadmin_resource_explorer'


def init_app(
    app, provider_registry, security_service=None
) -> ResourceExplorerService:
    """Install one common resource/explorer service per application."""
    existing = app.extensions.get(APP_EXTENSION_KEY)
    if existing is not None:
        return existing
    service = ResourceExplorerService(
        provider_registry, security_service=security_service
    )
    app.extensions[APP_EXTENSION_KEY] = service
    return service


def service_for_app(app) -> ResourceExplorerService:
    try:
        return app.extensions[APP_EXTENSION_KEY]
    except (AttributeError, KeyError) as exc:
        raise ResourceGraphError(
            'CDEadmin resource explorer is not initialized'
        ) from exc


__all__ = (
    'ExplorerAction',
    'ExplorerBadge',
    'ExplorerNode',
    'GenerationAwareResourceCache',
    'ResourceAccessError',
    'ResourceCommandContribution',
    'ResourceContributionRegistry',
    'ResourceExplorerService',
    'ResourceGraphError',
    'ResourceInspectorContribution',
    'ResourcePage',
    'ResourceRef',
    'StaleResourceGenerationError',
    'init_app',
    'service_for_app',
)
