##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Versioned support SDK for independently packaged CDEadmin providers."""

from .actual_engine import (
    ActualEnginePilotProvider,
    PilotProfile,
    PilotProviderError,
    RuntimeIdentityError,
)
from .tooling import ProviderToolError, ProviderToolGrant, ProviderToolRunner
from .relational import (
    RelationalClientConfig,
    RelationalClientError,
    RelationalDBAPIClient,
    RelationalDependencyError,
    first_value,
    load_optional_module,
)

__all__ = (
    'ActualEnginePilotProvider',
    'PilotProfile',
    'PilotProviderError',
    'RuntimeIdentityError',
    'ProviderToolError',
    'ProviderToolGrant',
    'ProviderToolRunner',
    'RelationalClientConfig',
    'RelationalClientError',
    'RelationalDBAPIClient',
    'RelationalDependencyError',
    'first_value',
    'load_optional_module',
)
