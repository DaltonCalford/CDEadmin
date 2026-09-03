##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

from flask import current_app

from pgadmin.cdeadmin.core import (
    ProviderUnavailableError,
    current_endpoint_context,
    registry_for_app,
)

from .registry import DriverRegistry


def _current_application(app=None):
    if app is not None:
        return app
    try:
        return current_app._get_current_object()
    except RuntimeError:
        return None


def get_driver(_type, app=None, endpoint_context=None):
    context = endpoint_context or current_endpoint_context()
    if context is None:
        if app is not None:
            DriverRegistry.load_modules(app)
        return DriverRegistry.get(_type)

    application = _current_application(app)
    registry = None
    if application is not None:
        try:
            registry = registry_for_app(application)
        except ProviderUnavailableError:
            registry = None
    if registry is not None and registry.has_registration(context):
        return registry.resolve(context).legacy_driver(_type)
    if context.legacy_driver_type != _type:
        raise ProviderUnavailableError(
            'endpoint has no matching provider or legacy driver binding'
        )
    if app is not None:
        DriverRegistry.load_modules(app)

    return DriverRegistry.get(_type)


def init_app(app):
    drivers = dict()

    setattr(app, '_pgadmin_server_drivers', drivers)
    DriverRegistry.load_modules(app)

    return drivers


def ping():
    for type in DriverRegistry._registry:
        DriverRegistry._objects[type].gc_timeout()
