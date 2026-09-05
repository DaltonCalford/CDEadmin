##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Delegated semantic-report scheduler services."""

import json

from .crypto import WorkerKeyError, WorkerKeyRing
from .models import (
    ReportSchedulerAccessError,
    ReportSchedulerConflict,
    ReportSchedulerError,
    ReportSchedulerUnavailable,
)
from .service import ReportSchedulerService
from .worker import ReportSchedulerWorker


APP_EXTENSION_KEY = 'cdeadmin_report_scheduler_service'


def init_app(
    app, endpoint_service, security_service, semantic_service,
    delivery_service, studio_service=None, result_service=None,
):
    existing = app.extensions.get(APP_EXTENSION_KEY)
    if existing is not None:
        return existing
    key_ring = WorkerKeyRing(
        app.config.get('CDEADMIN_REPORT_WORKER_KEYS', {}),
        app.config.get('CDEADMIN_REPORT_WORKER_ACTIVE_KEY_ID'),
    )
    service = ReportSchedulerService(
        endpoint_service, security_service, semantic_service,
        delivery_service, key_ring=key_ring,
        lease_seconds=app.config.get(
            'CDEADMIN_REPORT_WORKER_LEASE_SECONDS', 300
        ),
        max_lateness_seconds=app.config.get(
            'CDEADMIN_REPORT_MAX_LATENESS_SECONDS', 3600
        ),
    )
    app.extensions[APP_EXTENSION_KEY] = service
    if studio_service is not None and result_service is not None:
        _register_worker_command(
            app, service, studio_service, result_service,
            semantic_service, delivery_service,
        )
    return service


def _register_worker_command(
    app, scheduler, studio, result_service, semantic_service,
    delivery_service,
):
    import click

    @app.cli.command('cde-report-worker')
    @click.option('--worker-id', required=True, type=str)
    @click.option('--max-occurrences', default=10, type=int)
    def report_worker(worker_id, max_occurrences):
        """Run one bounded report-scheduler polling cycle."""
        worker = ReportSchedulerWorker(
            scheduler, studio, result_service, semantic_service,
            delivery_service,
            poll_interval=app.config.get(
                'CDEADMIN_REPORT_WORKER_POLL_INTERVAL', 0.1
            ),
            max_polls=app.config.get(
                'CDEADMIN_REPORT_WORKER_MAX_POLLS', 600
            ),
        )
        click.echo(json.dumps(
            worker.run_once(worker_id, max_occurrences), sort_keys=True
        ))

    @app.cli.command('cde-report-rotate-keys')
    def rotate_report_keys():
        """Re-encrypt delegated credentials with the active worker key."""
        click.echo(json.dumps(scheduler.rotate_keys(), sort_keys=True))


def service_for_app(app):
    try:
        return app.extensions[APP_EXTENSION_KEY]
    except (AttributeError, KeyError) as exc:
        raise ReportSchedulerUnavailable(
            'CDEadmin report scheduler service is not initialized'
        ) from exc


__all__ = (
    'ReportSchedulerAccessError', 'ReportSchedulerConflict',
    'ReportSchedulerError', 'ReportSchedulerService',
    'ReportSchedulerUnavailable', 'ReportSchedulerWorker',
    'WorkerKeyError', 'WorkerKeyRing',
    'init_app', 'service_for_app',
)
