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
    default_registration_profile,
    registration_profile,
    registration_profile_for_endpoint,
    registration_profiles,
    provider_route_form_values,
    provider_route_options,
)
from .service import EndpointService, init_app, service_for_app


__all__ = (
    'EndpointRegistrationError',
    'EndpointService',
    'default_registration_profile',
    'registration_profile',
    'registration_profile_for_endpoint',
    'registration_profiles',
    'provider_route_form_values',
    'provider_route_options',
    'init_app',
    'service_for_app',
)
