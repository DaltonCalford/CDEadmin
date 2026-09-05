##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Delegated report authority, scheduling and migration tests."""

from __future__ import annotations

import base64
import importlib.util
import json
import os
import sys
import unittest
import uuid
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType, SimpleNamespace


ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / 'web'
if str(WEB) not in sys.path:
    sys.path.insert(0, str(WEB))
if 'pgadmin' not in sys.modules:
    package = ModuleType('pgadmin')
    package.__path__ = [str(WEB / 'pgadmin')]
    sys.modules['pgadmin'] = package

from pgadmin.cdeadmin.report_scheduler import (  # noqa: E402
    ReportSchedulerError,
    ReportSchedulerUnavailable,
    ReportSchedulerWorker,
    WorkerKeyError,
    WorkerKeyRing,
)
from pgadmin.cdeadmin.report_scheduler.service import (  # noqa: E402
    ReportSchedulerService,
)


MIGRATION_PATH = (
    ROOT / 'web/migrations/versions/cde_report_scheduler_v1_.py'
)

try:
    import sqlalchemy as sa
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    MIGRATION_DEPENDENCIES = True
except ImportError:
    MIGRATION_DEPENDENCIES = False


class ReportSchedulerCryptoTests(unittest.TestCase):

    def setUp(self):
        self.first = base64.b64encode(os.urandom(32)).decode('ascii')
        self.second = base64.b64encode(os.urandom(32)).decode('ascii')
        self.scope = {
            'delegation_id': str(uuid.uuid4()),
            'endpoint_id': str(uuid.uuid4()),
        }

    def test_envelope_is_authenticated_and_rotatable(self):
        ring = WorkerKeyRing({'one': self.first}, 'one')
        envelope = ring.encrypt(b'database-secret', self.scope)
        self.assertNotIn('database-secret', envelope)
        self.assertEqual(
            b'database-secret', ring.decrypt(envelope, self.scope)
        )
        with self.assertRaises(WorkerKeyError):
            ring.decrypt(envelope, {**self.scope, 'endpoint_id': str(
                uuid.uuid4()
            )})
        rotated = WorkerKeyRing({
            'one': self.first, 'two': self.second,
        }, 'two').rotate(envelope, self.scope)
        self.assertEqual('two', json.loads(rotated)['key_id'])

    def test_key_authority_is_fail_closed(self):
        self.assertFalse(WorkerKeyRing().available)
        with self.assertRaises(WorkerKeyError):
            WorkerKeyRing({'short': base64.b64encode(b'x').decode()}, 'short')
        with self.assertRaises(WorkerKeyError):
            WorkerKeyRing({'one': self.first}, None)


class ReportSchedulerTimeTests(unittest.TestCase):

    def test_due_instants_are_timezone_aware_and_five_field_only(self):
        schedule = {
            'expression': '0 8 * * *', 'timezone': 'America/Toronto',
        }
        values = ReportSchedulerService._due_instants(
            schedule,
            datetime(2026, 7, 1, 11, 59),
            datetime(2026, 7, 2, 12, 1),
        )
        self.assertEqual([
            datetime(2026, 7, 1, 12, 0),
            datetime(2026, 7, 2, 12, 0),
        ], values)
        with self.assertRaises(ReportSchedulerError):
            ReportSchedulerService._validate_cron({
                'expression': '0 0 8 * * *', 'timezone': 'UTC',
            })

    def test_unconfigured_scheduler_refuses_claims(self):
        service = ReportSchedulerService.__new__(ReportSchedulerService)
        service.key_ring = WorkerKeyRing()
        with self.assertRaises(ReportSchedulerUnavailable):
            service.claim('worker-one')


class _Query:
    def __init__(self, row):
        self.row = row

    def filter_by(self, **_values):
        return self

    def first(self):
        return self.row


class _WorkerScheduler:
    def __init__(self):
        endpoint_id = str(uuid.uuid4())
        self.delegation = SimpleNamespace(
            id=str(uuid.uuid4()), user_id=7, endpoint_id=endpoint_id,
        )
        self.occurrence = SimpleNamespace(
            id=str(uuid.uuid4()), delegation=self.delegation,
            state='scheduled', delivery_occurrence_ids=None,
        )
        self.claimed = False
        self.repository = SimpleNamespace(
            occurrence=SimpleNamespace(query=_Query(self.occurrence))
        )
        self.endpoint_service = SimpleNamespace(
            provider_registry=SimpleNamespace(resolve=self._resolve)
        )

    @staticmethod
    def _resolve(_context):
        return SimpleNamespace(instance=_Provider())

    @staticmethod
    def recover_stale():
        return 0

    @staticmethod
    def enqueue_due():
        return {'scheduled': 1, 'missed': 0, 'expired': 0}

    def claim(self, _worker_id):
        if self.claimed:
            return None
        self.claimed = True
        self.occurrence.state = 'claimed'
        return self.occurrence, 'claim-token'

    @staticmethod
    def definition_for_worker(_delegation):
        model = {
            'visualizations': [{
                'id': 'revenue', 'name': 'Revenue',
                'query': {'parameters': {}},
            }],
            'dashboards': [{
                'id': 'daily',
                'tiles': [{'visualization_id': 'revenue'}],
            }],
        }
        report = {
            'id': 'daily-report', 'dashboard_id': 'daily', 'parameters': {},
        }
        schedule = {'delivery': {
            'format': 'pdf', 'profile_id': 'mail',
            'target': {'recipients': ['owner@example.test']},
        }}
        return {'revision': 1}, model, report, schedule

    def delegated_workspace(self, _delegation):
        return SimpleNamespace(endpoint_id=self.delegation.endpoint_id), {
            'route': {},
        }

    def transition(self, _occurrence_id, _token, state, phase, **values):
        self.occurrence.state = state
        self.occurrence.phase = phase
        for key, value in values.items():
            setattr(self.occurrence, key, value)
        return self.occurrence

    def heartbeat(self, _occurrence_id, _token, progress):
        self.occurrence.progress = progress
        return self.occurrence


class _Provider:
    @staticmethod
    def select_renderer(_result):
        return {'capability_ids': ['tabular.render']}


class _Studio:
    @staticmethod
    def open_session(_context, _endpoint, _language):
        return {'provider_session': {'session_id': 'session-one'}}

    @staticmethod
    def execute(_context, _session_id, _source, **_values):
        return {'occurrence_id': 'provider-occurrence'}

    @staticmethod
    def poll(_context, _occurrence_id):
        return {
            'operation': {'terminal': True},
            'result': {'result_kind': 'tabular'},
        }

    @staticmethod
    def admit_result(_context, _occurrence_id, _capabilities):
        return {'result_id': 'result-one'}


class _ResultService:
    released = False

    @staticmethod
    def export(_result_id, _format, **_values):
        return {'content': b'%PDF-report'}

    def release(self, _result_id, **_values):
        self.released = True


class _SemanticService:
    @staticmethod
    def compile(_provider, _model, _query, **_values):
        return {
            'language_profile': 'test-sql', 'source': 'SELECT 42',
            'parameters': {},
        }


class _DeliveryService:
    request = None

    def deliver(self, _user_id, _endpoint_id, request):
        self.request = request
        return {'state': 'delivered', 'occurrence_id': 'delivery-one'}


class ReportSchedulerWorkerTests(unittest.TestCase):

    def test_one_shot_worker_executes_exports_and_delivers_once(self):
        scheduler = _WorkerScheduler()
        result_service = _ResultService()
        delivery_service = _DeliveryService()
        worker = ReportSchedulerWorker(
            scheduler, _Studio(), result_service, _SemanticService(),
            delivery_service,
        )
        summary = worker.run_once('worker-one')
        self.assertEqual(1, summary['claimed'])
        self.assertEqual(1, summary['delivered'])
        self.assertEqual('delivered', scheduler.occurrence.state)
        self.assertEqual(
            ['delivery-one'],
            json.loads(scheduler.occurrence.delivery_occurrence_ids),
        )
        self.assertEqual('pdf', delivery_service.request['format'])
        self.assertTrue(result_service.released)


@unittest.skipUnless(
    MIGRATION_DEPENDENCIES,
    'SQLAlchemy and Alembic are required for migration verification',
)
class ReportSchedulerMigrationTests(unittest.TestCase):

    def test_migration_enforces_claim_identity_and_reversibility(self):
        specification = importlib.util.spec_from_file_location(
            'cde_report_scheduler_test_migration', MIGRATION_PATH
        )
        module = importlib.util.module_from_spec(specification)
        specification.loader.exec_module(module)
        engine = sa.create_engine('sqlite:///:memory:')
        with engine.connect() as connection:
            metadata = sa.MetaData()
            sa.Table(
                'user', metadata,
                sa.Column('id', sa.Integer(), primary_key=True),
            )
            sa.Table(
                'cde_endpoint', metadata,
                sa.Column('id', sa.String(36), primary_key=True),
            )
            sa.Table(
                'cde_semantic_model', metadata,
                sa.Column('id', sa.String(36), primary_key=True),
            )
            metadata.create_all(connection)
            module.op = Operations(MigrationContext.configure(connection))
            module.upgrade()
            names = sa.inspect(connection).get_table_names()
            self.assertIn('cde_report_delegation', names)
            self.assertIn('cde_report_delegated_credential', names)
            self.assertIn('cde_report_schedule_occurrence', names)
            connection.execute(sa.text('PRAGMA foreign_keys=ON'))
            endpoint_id = str(uuid.uuid4())
            model_id = str(uuid.uuid4())
            delegation_id = str(uuid.uuid4())
            connection.execute(sa.text('INSERT INTO user (id) VALUES (7)'))
            connection.execute(sa.text(
                'INSERT INTO cde_endpoint (id) VALUES (:id)'
            ), {'id': endpoint_id})
            connection.execute(sa.text(
                'INSERT INTO cde_semantic_model (id) VALUES (:id)'
            ), {'id': model_id})
            delegation = sa.Table(
                'cde_report_delegation', sa.MetaData(),
                autoload_with=connection,
            )
            connection.execute(delegation.insert(), {
                'id': delegation_id, 'user_id': 7,
                'endpoint_id': endpoint_id, 'model_id': model_id,
                'report_id': 'daily', 'schedule_id': 'at-eight',
                'route_id': 'route-one',
                'primary_secret_kind': 'database_password',
                'endpoint_generation': str(uuid.uuid4()),
                'model_revision': 2, 'definition_digest': 'a' * 64,
                'delivery_scope': '{}', 'state': 'active',
                'credential_generation': 1,
                'created_at': datetime.now(timezone.utc),
                'updated_at': datetime.now(timezone.utc),
                'expires_at': datetime.now(timezone.utc),
            })
            occurrence = sa.Table(
                'cde_report_schedule_occurrence', sa.MetaData(),
                autoload_with=connection,
            )
            values = {
                'id': str(uuid.uuid4()),
                'delegation_id': delegation_id,
                'scheduled_for': datetime.now(timezone.utc),
                'state': 'scheduled', 'phase': 'queued',
                'progress': '{}',
                'created_at': datetime.now(timezone.utc),
                'updated_at': datetime.now(timezone.utc),
            }
            connection.execute(occurrence.insert(), values)
            with self.assertRaises(sa.exc.IntegrityError):
                connection.execute(occurrence.insert(), {
                    **values, 'id': str(uuid.uuid4()),
                })
            module.downgrade()
            self.assertNotIn(
                'cde_report_delegation',
                sa.inspect(connection).get_table_names(),
            )


if __name__ == '__main__':
    unittest.main()
