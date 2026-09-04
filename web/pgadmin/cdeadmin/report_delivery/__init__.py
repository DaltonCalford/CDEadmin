##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Authenticated report delivery services."""

from .service import (
    DeliveryProfileRegistry,
    DeliveryTransportError,
    ReportDeliveryConflict,
    ReportDeliveryError,
    ReportDeliveryService,
    S3DeliveryAdapter,
    SMTPDeliveryAdapter,
)


APP_EXTENSION_KEY = 'cdeadmin_report_delivery_service'


def init_app(app):
    existing = app.extensions.get(APP_EXTENSION_KEY)
    if existing is not None:
        return existing
    service = ReportDeliveryService(
        profiles=app.config.get('CDEADMIN_REPORT_DELIVERY_PROFILES', {}),
        retention_days=app.config.get(
            'CDEADMIN_REPORT_DELIVERY_RETENTION_DAYS', 90
        ),
        stale_attempt_seconds=app.config.get(
            'CDEADMIN_REPORT_DELIVERY_STALE_SECONDS', 600
        ),
    )
    app.extensions[APP_EXTENSION_KEY] = service
    return service


__all__ = (
    'DeliveryProfileRegistry', 'DeliveryTransportError',
    'ReportDeliveryConflict', 'ReportDeliveryError',
    'ReportDeliveryService', 'S3DeliveryAdapter', 'SMTPDeliveryAdapter',
    'init_app',
)
