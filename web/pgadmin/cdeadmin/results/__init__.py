##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""CDEadmin typed result descriptor and renderer service."""

from .models import (
    ExportPolicy,
    RendererContribution,
    RendererUnavailableError,
    RenderedResultPage,
    ResultAdapterContribution,
    ResultDescriptor,
    ResultDescriptorError,
    ResultLimitError,
    ResultLimits,
    ResultRegistryError,
    SamplingPolicy,
    WorkerIsolationError,
    WorkerPolicy,
)
from .service import (
    InlineRendererExecutor,
    ProcessRendererExecutor,
    ResultRendererRegistry,
    ResultService,
)


APP_EXTENSION_KEY = 'cdeadmin_result_service'


def init_app(app, provider_registry) -> ResultService:
    """Install one typed result service per application."""
    existing = app.extensions.get(APP_EXTENSION_KEY)
    if existing is not None:
        return existing
    service = ResultService(provider_registry)
    app.extensions[APP_EXTENSION_KEY] = service
    return service


def service_for_app(app) -> ResultService:
    try:
        return app.extensions[APP_EXTENSION_KEY]
    except (AttributeError, KeyError) as exc:
        raise ResultRegistryError(
            'CDEadmin result service is not initialized'
        ) from exc


__all__ = (
    'ExportPolicy',
    'InlineRendererExecutor',
    'ProcessRendererExecutor',
    'RendererContribution',
    'RendererUnavailableError',
    'RenderedResultPage',
    'ResultAdapterContribution',
    'ResultDescriptor',
    'ResultDescriptorError',
    'ResultLimitError',
    'ResultLimits',
    'ResultRegistryError',
    'ResultRendererRegistry',
    'ResultService',
    'SamplingPolicy',
    'WorkerIsolationError',
    'WorkerPolicy',
    'init_app',
    'service_for_app',
)
