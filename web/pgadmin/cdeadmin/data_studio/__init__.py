##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""CDEadmin common Data Studio service."""

from .models import (
    CHANNELS,
    CANCELLATION_STATES,
    OCCURRENCE_STATES,
    ChannelMessage,
    CompletionContribution,
    DataStudioAccessError,
    DataStudioError,
    ExecutionContribution,
    ExecutionOccurrence,
    FixtureExecutionError,
    LanguageContribution,
    SessionContribution,
    StudioSession,
)
from .studio import (
    BoundedHistory,
    DataStudioContributionRegistry,
    DataStudioService,
    redact,
)


APP_EXTENSION_KEY = 'cdeadmin_data_studio'


def init_app(
    app, provider_registry, result_service=None
) -> DataStudioService:
    """Install one common Data Studio service per application."""
    existing = app.extensions.get(APP_EXTENSION_KEY)
    if existing is not None:
        return existing
    service = DataStudioService(
        provider_registry, result_service=result_service
    )
    app.extensions[APP_EXTENSION_KEY] = service
    return service


def service_for_app(app) -> DataStudioService:
    try:
        return app.extensions[APP_EXTENSION_KEY]
    except (AttributeError, KeyError) as exc:
        raise DataStudioError(
            'CDEadmin Data Studio is not initialized'
        ) from exc


__all__ = (
    'BoundedHistory',
    'CANCELLATION_STATES',
    'CHANNELS',
    'ChannelMessage',
    'CompletionContribution',
    'DataStudioAccessError',
    'DataStudioContributionRegistry',
    'DataStudioError',
    'DataStudioService',
    'ExecutionContribution',
    'ExecutionOccurrence',
    'FixtureExecutionError',
    'LanguageContribution',
    'OCCURRENCE_STATES',
    'SessionContribution',
    'StudioSession',
    'init_app',
    'redact',
    'service_for_app',
)
