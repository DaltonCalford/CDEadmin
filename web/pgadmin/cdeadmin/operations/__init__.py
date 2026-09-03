##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""CDEadmin operation, event, receipt, and evidence services."""

from pathlib import Path

from .adapters import LocalProcessAdapter, RemoteProviderAdapter
from .bus import OperationBus, redact
from .models import (
    AccessPolicy,
    AdapterCancellation,
    AdapterObservation,
    AdapterRegistrationError,
    AdapterStart,
    EventReplay,
    EvidenceUnavailableError,
    IdempotencyConflictError,
    OperationAccessError,
    OperationBusError,
    OperationRequest,
    OperationStateError,
    PostStateResult,
    RetentionPolicy,
)
from .store import JsonOperationStore, MemoryOperationStore


APP_EXTENSION_KEY = 'cdeadmin_operation_bus'


def init_app(app) -> OperationBus:
    """Install one common operation bus per application."""
    existing = app.extensions.get(APP_EXTENSION_KEY)
    if existing is not None:
        return existing
    configured_path = app.config.get('CDEADMIN_OPERATION_STORE_PATH')
    store = (
        JsonOperationStore(Path(configured_path))
        if configured_path else MemoryOperationStore()
    )
    service = OperationBus(store=store)
    app.extensions[APP_EXTENSION_KEY] = service
    return service


def service_for_app(app) -> OperationBus:
    try:
        return app.extensions[APP_EXTENSION_KEY]
    except (AttributeError, KeyError) as exc:
        raise OperationBusError(
            'CDEadmin operation bus is not initialized'
        ) from exc


__all__ = (
    'AccessPolicy',
    'AdapterCancellation',
    'AdapterObservation',
    'AdapterRegistrationError',
    'AdapterStart',
    'EventReplay',
    'EvidenceUnavailableError',
    'IdempotencyConflictError',
    'JsonOperationStore',
    'LocalProcessAdapter',
    'MemoryOperationStore',
    'OperationAccessError',
    'OperationBus',
    'OperationBusError',
    'OperationRequest',
    'OperationStateError',
    'PostStateResult',
    'RemoteProviderAdapter',
    'RetentionPolicy',
    'init_app',
    'redact',
    'service_for_app',
)
