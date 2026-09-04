##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Authenticated report delivery and occurrence-finality tests."""

from __future__ import annotations

import copy
import importlib.util
import json
import sys
import unittest
import uuid
from datetime import datetime, timedelta, timezone
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

from pgadmin.cdeadmin.report_delivery import (  # noqa: E402
    DeliveryProfileRegistry,
    DeliveryTransportError,
    ReportDeliveryConflict,
    ReportDeliveryError,
    ReportDeliveryService,
    S3DeliveryAdapter,
    SMTPDeliveryAdapter,
)


ENDPOINT_ID = str(uuid.uuid4())
MIGRATION_PATH = (
    ROOT / 'web/migrations/versions/cde_report_delivery_v1_.py'
)

try:
    import sqlalchemy as sa
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    MIGRATION_DEPENDENCIES = True
except ImportError:
    MIGRATION_DEPENDENCIES = False


def smtp_profile():
    return {
        'kind': 'smtp', 'label': 'Operations mail',
        'host': 'smtp.example.test', 'port': 587,
        'use_ssl': False, 'use_starttls': True,
        'username': 'reporter', 'password': 'not-for-the-browser',
        'sender': 'reports@example.test',
        'allowed_domains': ['example.test'],
        'allowed_formats': ['pdf', 'csv'],
    }


def s3_profile():
    return {
        'kind': 's3', 'label': 'Archive', 'bucket': 'reports',
        'prefix': 'cdeadmin/published', 'region_name': 'ca-central-1',
        'role_arn': 'arn:aws:iam::123456789012:role/report-writer',
        'external_id': 'private-external-id',
        'server_side_encryption': 'aws:kms',
        'kms_key_id': 'alias/cdeadmin-reports',
        'allowed_formats': ['pdf'],
    }


class MemoryRepository:
    def __init__(self):
        self.rows = {}

    def get(self, user_id, endpoint_id, request_key):
        row = self.rows.get((user_id, endpoint_id, request_key))
        return row

    def begin(self, **values):
        key = (
            values['user_id'], values['endpoint_id'], values['request_key'],
        )
        if key in self.rows:
            return self.rows[key], False
        row = SimpleNamespace(**values)
        self.rows[key] = row
        return row, True

    @staticmethod
    def transition(row, state, **values):
        row.state = state
        for key, value in values.items():
            setattr(row, key, value)
        return row

    def list(self, user_id, endpoint_id, limit=100):
        return [
            row for row in self.rows.values()
            if row.user_id == user_id and row.endpoint_id == endpoint_id
        ][:limit]

    def recover_stale(self, user_id, endpoint_id, cutoff, now):
        count = 0
        for row in self.list(user_id, endpoint_id):
            if (
                row.state in {'prepared', 'delivering'} and
                row.created_at < cutoff
            ):
                row.state = (
                    'outcome_unknown' if row.state == 'delivering'
                    else 'failed'
                )
                row.error_type = 'InterruptedDeliveryAttempt'
                row.completed_at = now
                count += 1
        return count

    def prune(self, before):
        keys = [
            key for key, row in self.rows.items()
            if row.completed_at is not None and row.completed_at < before and
            row.state in {'delivered', 'failed', 'outcome_unknown'}
        ]
        for key in keys:
            self.rows.pop(key)
        return len(keys)


class SuccessfulAdapter:
    def __init__(self):
        self.calls = []

    def deliver(self, profile, target, payload):
        self.calls.append((profile, target, payload))
        return {'transport': profile['kind'], 'opaque_receipt': 'accepted'}


class FailingAdapter:
    def __init__(self, unknown):
        self.unknown = unknown

    def deliver(self, _profile, _target, _payload):
        raise DeliveryTransportError(
            'transport failed', outcome_unknown=self.unknown
        )


def request(profile_id='mail', target=None, request_key=None):
    return {
        'request_key': request_key or str(uuid.uuid4()),
        'result_id': str(uuid.uuid4()), 'format': 'pdf',
        'profile_id': profile_id,
        'target': target or {'recipients': ['owner@example.test']},
        'content': b'%PDF-report', 'filename': 'report.pdf',
        'media_type': 'application/pdf',
    }


class FakeSMTP:
    instances = []

    def __init__(self, **arguments):
        self.arguments = arguments
        self.started_tls = False
        self.login_value = None
        self.message = None
        self.__class__.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def starttls(self, context):
        self.started_tls = context is not None

    def login(self, username, password):
        self.login_value = (username, password)

    def send_message(self, message):
        self.message = message
        return {}


class FakeS3Client:
    def __init__(self):
        self.request = None

    def put_object(self, **request_value):
        self.request = request_value
        return {
            'ETag': 'etag', 'VersionId': 'version',
            'ChecksumSHA256': request_value['ChecksumSHA256'],
        }


class FakeSTSClient:
    def __init__(self):
        self.request = None

    def assume_role(self, **request_value):
        self.request = request_value
        return {'Credentials': {
            'AccessKeyId': 'temporary-key',
            'SecretAccessKey': 'temporary-secret',
            'SessionToken': 'temporary-token',
        }}


class FakeSession:
    def __init__(self):
        self.s3 = FakeS3Client()
        self.sts = FakeSTSClient()
        self.s3_arguments = None

    def client(self, name, **arguments):
        if name == 'sts':
            return self.sts
        self.s3_arguments = arguments
        return self.s3

    @staticmethod
    def get_credentials():
        return object()


class ReportDeliveryTests(unittest.TestCase):

    def test_catalog_is_secret_free_and_scheduler_is_explicitly_disabled(self):
        registry = DeliveryProfileRegistry({
            'mail': smtp_profile(), 'archive': s3_profile(),
        })
        catalog = registry.catalog()
        encoded = json.dumps(catalog)
        self.assertNotIn('not-for-the-browser', encoded)
        self.assertNotIn('private-external-id', encoded)
        self.assertNotIn('role/report-writer', encoded)
        self.assertTrue(catalog['manual_delivery'])
        self.assertFalse(catalog['automatic_scheduling'])
        self.assertEqual(2, len(catalog['profiles']))

    def test_smtp_requires_auth_encryption_and_recipient_allowlist(self):
        invalid = smtp_profile()
        invalid.update({
            'use_starttls': False, 'username': '', 'password': '',
            'allowed_domains': [],
        })
        with self.assertRaises(ReportDeliveryError):
            DeliveryProfileRegistry({'mail': invalid})
        registry = DeliveryProfileRegistry({'mail': smtp_profile()})
        profile = registry.get('mail')
        with self.assertRaisesRegex(ReportDeliveryError, 'allowlist'):
            registry.normalize_target(
                profile, {'recipients': ['attacker@elsewhere.test']}
            )

    def test_smtp_adapter_authenticates_and_sends_bounded_attachment(self):
        FakeSMTP.instances.clear()
        profile = DeliveryProfileRegistry({
            'mail': smtp_profile()
        }).get('mail')
        adapter = SMTPDeliveryAdapter(
            smtp_factory=FakeSMTP, smtp_ssl_factory=FakeSMTP
        )
        receipt = adapter.deliver(profile, {
            'recipients': ['owner@example.test'],
        }, {
            'content': b'%PDF-report', 'filename': 'report.pdf',
            'media_type': 'application/pdf',
        })
        client = FakeSMTP.instances[-1]
        self.assertTrue(client.started_tls)
        self.assertEqual(
            ('reporter', 'not-for-the-browser'), client.login_value
        )
        self.assertEqual('owner@example.test', client.message['To'])
        self.assertEqual(1, receipt['recipient_count'])

    def test_delivery_is_attempted_once_by_idempotency_key(self):
        adapter = SuccessfulAdapter()
        service = ReportDeliveryService(
            {'mail': smtp_profile()}, MemoryRepository(), {'smtp': adapter}
        )
        value = request()
        first = service.deliver(7, ENDPOINT_ID, value)
        second = service.deliver(7, ENDPOINT_ID, copy.deepcopy(value))
        self.assertEqual('delivered', first['state'])
        self.assertTrue(second['replayed_request'])
        self.assertEqual(1, len(adapter.calls))
        changed = copy.deepcopy(value)
        changed['target'] = {'recipients': ['other@example.test']}
        with self.assertRaises(ReportDeliveryConflict):
            service.deliver(7, ENDPOINT_ID, changed)

    def test_transport_finality_is_never_inferred_or_retried(self):
        states = ((False, 'failed'), (True, 'outcome_unknown'))
        for unknown, expected in states:
            with self.subTest(unknown=unknown):
                service = ReportDeliveryService(
                    {'mail': smtp_profile()}, MemoryRepository(),
                    {'smtp': FailingAdapter(unknown)},
                )
                occurrence = service.deliver(7, ENDPOINT_ID, request())
                self.assertEqual(expected, occurrence['state'])
                self.assertFalse(occurrence['automatic_retry'])
                self.assertEqual(
                    'DeliveryTransportError', occurrence['error_type']
                )

    def test_s3_adapter_uses_fixed_scope_checksum_encryption_and_role(self):
        session = FakeSession()
        profile = DeliveryProfileRegistry({
            'archive': s3_profile()
        }).get('archive')
        adapter = S3DeliveryAdapter(
            session_factory=lambda _profile_name: session
        )
        receipt = adapter.deliver(profile, {'object_name': 'report.pdf'}, {
            'content': b'%PDF-report', 'filename': 'report.pdf',
            'media_type': 'application/pdf',
            'request_key': str(uuid.uuid4()),
            'sha256': 'f' * 64,
        })
        self.assertEqual('reports', session.s3.request['Bucket'])
        self.assertEqual(
            'cdeadmin/published/report.pdf', session.s3.request['Key']
        )
        self.assertEqual(
            'aws:kms', session.s3.request['ServerSideEncryption']
        )
        self.assertEqual(
            'alias/cdeadmin-reports', session.s3.request['SSEKMSKeyId']
        )
        self.assertEqual('version', receipt['version_id'])
        self.assertEqual(
            'arn:aws:iam::123456789012:role/report-writer',
            session.sts.request['RoleArn'],
        )
        self.assertEqual(
            'temporary-key', session.s3_arguments['aws_access_key_id']
        )

    def test_object_name_cannot_escape_fixed_profile_prefix(self):
        registry = DeliveryProfileRegistry({'archive': s3_profile()})
        profile = registry.get('archive')
        for value in ('../report.pdf', 'nested/report.pdf', '..'):
            with self.subTest(value=value), self.assertRaises(
                    ReportDeliveryError):
                registry.normalize_target(
                    profile, {'object_name': value}
                )

    def test_listing_recovers_stale_attempts_and_applies_retention(self):
        repository = MemoryRepository()
        service = ReportDeliveryService(
            {'mail': smtp_profile()}, repository,
            {'smtp': SuccessfulAdapter()}, retention_days=30,
            stale_attempt_seconds=60,
        )
        stale_key = (7, ENDPOINT_ID, str(uuid.uuid4()))
        repository.rows[stale_key] = SimpleNamespace(
            id=str(uuid.uuid4()), request_key=stale_key[2], user_id=7,
            endpoint_id=ENDPOINT_ID, result_id='result-stale',
            profile_id='mail', channel='smtp', export_format='pdf',
            intent_digest='a' * 64, target_summary='{}',
            state='delivering', provider_receipt=None, error_type=None,
            created_at=datetime.now(timezone.utc) - timedelta(minutes=5),
            started_at=datetime.now(timezone.utc) - timedelta(minutes=5),
            completed_at=None,
        )
        retained = service.list(7, ENDPOINT_ID)
        self.assertEqual('outcome_unknown', retained[0]['state'])
        self.assertEqual(
            'InterruptedDeliveryAttempt', retained[0]['error_type']
        )
        repository.rows[stale_key].completed_at = (
            datetime.now(timezone.utc) - timedelta(days=31)
        )
        self.assertEqual([], service.list(7, ENDPOINT_ID))


@unittest.skipUnless(
    MIGRATION_DEPENDENCIES,
    'SQLAlchemy and Alembic are required for migration verification',
)
class ReportDeliveryMigrationTests(unittest.TestCase):

    def test_migration_enforces_identity_state_and_reversibility(self):
        specification = importlib.util.spec_from_file_location(
            'cde_report_delivery_test_migration', MIGRATION_PATH
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
            metadata.create_all(connection)
            module.op = Operations(MigrationContext.configure(connection))
            module.upgrade()
            table_names = sa.inspect(connection).get_table_names()
            self.assertIn('cde_report_delivery_occurrence', table_names)
            table = sa.Table(
                'cde_report_delivery_occurrence', sa.MetaData(),
                autoload_with=connection,
            )
            connection.execute(sa.text(
                'PRAGMA foreign_keys=ON'
            ))
            connection.execute(sa.text(
                "INSERT INTO user (id) VALUES (7)"
            ))
            connection.execute(sa.text(
                "INSERT INTO cde_endpoint (id) VALUES (:endpoint_id)"
            ), {'endpoint_id': ENDPOINT_ID})
            values = {
                'id': str(uuid.uuid4()),
                'request_key': str(uuid.uuid4()), 'user_id': 7,
                'endpoint_id': ENDPOINT_ID, 'result_id': 'result-one',
                'profile_id': 'archive', 'channel': 's3',
                'export_format': 'pdf', 'intent_digest': 'a' * 64,
                'target_summary': '{}', 'state': 'prepared',
                'created_at': datetime.now(timezone.utc),
            }
            connection.execute(table.insert(), values)
            duplicate = dict(values, id=str(uuid.uuid4()))
            with self.assertRaises(sa.exc.IntegrityError):
                connection.execute(table.insert(), duplicate)
            invalid = dict(
                values, id=str(uuid.uuid4()),
                request_key=str(uuid.uuid4()), state='invented',
            )
            with self.assertRaises(sa.exc.IntegrityError):
                connection.execute(table.insert(), invalid)
            module.downgrade()
            self.assertNotIn(
                'cde_report_delivery_occurrence',
                sa.inspect(connection).get_table_names(),
            )


if __name__ == '__main__':
    unittest.main()
