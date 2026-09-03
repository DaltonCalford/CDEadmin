##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""CDEadmin endpoint registration and verification services."""

from .profiles import (
    EndpointRegistrationError,
    active_secret_fields,
    default_registration_profile,
    registration_profile,
    registration_interface,
    registration_interfaces,
    registration_profile_for_endpoint,
    registration_profiles,
    provider_route_form_values,
    provider_route_options,
    provider_secret_values,
)
from .service import EndpointService, init_app, service_for_app
from .connection_capabilities import (
    CONNECTION_CAPABILITY_CATEGORIES,
    ConnectionCapabilityError,
    assert_connection_capabilities_complete,
    normalize_connection_capabilities,
)
from .routing import RouteHealthRegistry, RouteSelectionError


__all__ = (
    'EndpointRegistrationError',
    'active_secret_fields',
    'EndpointService',
    'CONNECTION_CAPABILITY_CATEGORIES',
    'ConnectionCapabilityError',
    'RouteHealthRegistry',
    'RouteSelectionError',
    'assert_connection_capabilities_complete',
    'default_registration_profile',
    'registration_profile',
    'registration_interface',
    'registration_interfaces',
    'registration_profile_for_endpoint',
    'registration_profiles',
    'provider_route_form_values',
    'provider_route_options',
    'provider_secret_values',
    'init_app',
    'service_for_app',
    'normalize_connection_capabilities',
)
