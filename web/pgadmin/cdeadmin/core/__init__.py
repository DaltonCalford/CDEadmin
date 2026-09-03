##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""CDEadmin common provider registry and endpoint context."""

from .context import (
    EndpointContext,
    EndpointContextError,
    current_endpoint_context,
    endpoint_scope,
)
from .registry import (
    PermissionGuard,
    ProviderBinding,
    ProviderPermissionError,
    ProviderRegistrationError,
    ProviderRegistry,
    ProviderUnavailableError,
    init_app,
    registry_for_app,
    route_query_tool_execute,
    route_query_tool_fetch,
    route_query_tool_manager,
    route_query_tool_poll,
)


__all__ = (
    'EndpointContext',
    'EndpointContextError',
    'PermissionGuard',
    'ProviderBinding',
    'ProviderPermissionError',
    'ProviderRegistrationError',
    'ProviderRegistry',
    'ProviderUnavailableError',
    'current_endpoint_context',
    'endpoint_scope',
    'init_app',
    'registry_for_app',
    'route_query_tool_execute',
    'route_query_tool_fetch',
    'route_query_tool_manager',
    'route_query_tool_poll',
)
