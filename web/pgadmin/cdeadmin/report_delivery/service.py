##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Bounded, authenticated report delivery with durable attempt records."""

from __future__ import annotations

import base64
import copy
import hashlib
import json
import re
import smtplib
import ssl
import uuid
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import make_msgid, parseaddr
from pathlib import PurePosixPath

from pgadmin.cdeadmin.security.redaction import redact


MAX_DELIVERY_BYTES = 8 * 1024 * 1024
MAX_RECIPIENTS = 50
DELIVERY_STATES = frozenset({
    'prepared', 'delivering', 'delivered', 'failed', 'outcome_unknown',
})
MEDIA_TYPES = {
    'csv': 'text/csv',
    'json': 'application/json',
    'jsonl': 'application/x-ndjson',
    'xlsx': (
        'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    ),
    'svg': 'image/svg+xml',
    'pdf': 'application/pdf',
}
PROFILE_ID = re.compile(r'^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}$')


class ReportDeliveryError(RuntimeError):
    """A report delivery request is invalid or unavailable."""


class ReportDeliveryConflict(ReportDeliveryError):
    """An idempotency key was reused for different delivery intent."""


class DeliveryTransportError(ReportDeliveryError):
    """A transport failed, with explicit remote-outcome uncertainty."""

    def __init__(self, message, *, outcome_unknown):
        super().__init__(message)
        self.outcome_unknown = bool(outcome_unknown)


def _required_text(value, name, maximum=256):
    if not isinstance(value, str) or not value.strip():
        raise ReportDeliveryError(f'{name} must not be empty')
    value = value.strip()
    if len(value) > maximum:
        raise ReportDeliveryError(f'{name} exceeds its length limit')
    return value


def _positive_int(value, name, maximum):
    if isinstance(value, bool) or not isinstance(value, int):
        raise ReportDeliveryError(f'{name} must be an integer')
    if not 1 <= value <= maximum:
        raise ReportDeliveryError(f'{name} is outside its allowed range')
    return value


def _uuid(value, name):
    try:
        return str(uuid.UUID(value))
    except (AttributeError, TypeError, ValueError) as exc:
        raise ReportDeliveryError(f'{name} must be a UUID') from exc


def _string_list(value, name, maximum=128):
    if not isinstance(value, (list, tuple)) or len(value) > maximum:
        raise ReportDeliveryError(f'{name} must be a bounded array')
    result = []
    for item in value:
        item = _required_text(item, name, 320)
        if item in result:
            raise ReportDeliveryError(f'{name} contains a duplicate')
        result.append(item)
    return result


class DeliveryProfileRegistry:
    """Validate private server profiles and expose secret-free metadata."""

    def __init__(self, profiles=None):
        if profiles is None:
            profiles = {}
        if not isinstance(profiles, dict) or len(profiles) > 64:
            raise ReportDeliveryError('delivery profiles must be an object')
        self._profiles = {}
        for profile_id, raw in profiles.items():
            self._profiles[profile_id] = self._profile(profile_id, raw)

    def get(self, profile_id):
        profile_id = _required_text(profile_id, 'profile_id', 128)
        try:
            return copy.deepcopy(self._profiles[profile_id])
        except KeyError as exc:
            raise ReportDeliveryError(
                'report delivery profile is unavailable'
            ) from exc

    def catalog(self):
        return {
            'schema': 'cdeadmin.report-delivery.catalog.v1',
            'automatic_scheduling': False,
            'automatic_scheduling_reason': (
                'delegated-worker-credential-authority-unavailable'
            ),
            'manual_delivery': bool(self._profiles),
            'profiles': [self._public(value) for value in
                         self._profiles.values()],
        }

    @staticmethod
    def _public(profile):
        return {
            'profile_id': profile['profile_id'],
            'label': profile['label'],
            'kind': profile['kind'],
            'allowed_formats': list(profile['allowed_formats']),
            'max_bytes': profile['max_bytes'],
            'target_field': (
                'recipients' if profile['kind'] == 'smtp' else 'object_name'
            ),
            'authenticated': True,
            'encrypted_transport_required': True,
            'server_side_encryption': profile.get(
                'server_side_encryption'
            ),
            'destination_scope_fixed_server_side': True,
        }

    @staticmethod
    def _profile(profile_id, raw):
        if not isinstance(profile_id, str) or not PROFILE_ID.fullmatch(
                profile_id):
            raise ReportDeliveryError('delivery profile ID is invalid')
        if not isinstance(raw, dict):
            raise ReportDeliveryError('delivery profile must be an object')
        kind = raw.get('kind')
        if kind not in {'smtp', 's3'}:
            raise ReportDeliveryError('delivery profile kind is unsupported')
        formats = _string_list(
            raw.get('allowed_formats', sorted(MEDIA_TYPES)),
            'allowed_formats', len(MEDIA_TYPES),
        )
        if not formats or any(item not in MEDIA_TYPES for item in formats):
            raise ReportDeliveryError(
                'delivery profile contains an unsupported format'
            )
        profile = copy.deepcopy(raw)
        profile.update({
            'profile_id': profile_id,
            'kind': kind,
            'label': _required_text(
                raw.get('label', profile_id), 'profile label', 128
            ),
            'allowed_formats': tuple(formats),
            'max_bytes': _positive_int(
                raw.get('max_bytes', MAX_DELIVERY_BYTES), 'max_bytes',
                MAX_DELIVERY_BYTES,
            ),
        })
        if kind == 'smtp':
            DeliveryProfileRegistry._smtp(profile)
        else:
            DeliveryProfileRegistry._s3(profile)
        return profile

    @staticmethod
    def _smtp(profile):
        profile['host'] = _required_text(profile.get('host'), 'SMTP host')
        profile['port'] = _positive_int(
            profile.get('port', 465), 'SMTP port', 65535
        )
        profile['username'] = _required_text(
            profile.get('username'), 'SMTP username', 320
        )
        profile['password'] = _required_text(
            profile.get('password'), 'SMTP password', 4096
        )
        profile['sender'] = DeliveryProfileRegistry._email(
            profile.get('sender'), 'SMTP sender'
        )
        profile['use_ssl'] = bool(profile.get('use_ssl', True))
        profile['use_starttls'] = bool(profile.get('use_starttls', False))
        if profile['use_ssl'] and profile['use_starttls']:
            raise ReportDeliveryError(
                'SMTP SSL and STARTTLS are mutually exclusive'
            )
        if not (profile['use_ssl'] or profile['use_starttls']):
            raise ReportDeliveryError(
                'SMTP profile requires encrypted transport'
            )
        profile['timeout_seconds'] = _positive_int(
            profile.get('timeout_seconds', 30), 'SMTP timeout', 300
        )
        profile['allowed_recipients'] = tuple(
            item.casefold() for item in _string_list(
                profile.get('allowed_recipients', []),
                'allowed_recipients', 500,
            )
        )
        profile['allowed_domains'] = tuple(
            _required_text(item, 'allowed domain', 253).casefold().lstrip('@')
            for item in _string_list(
                profile.get('allowed_domains', []), 'allowed_domains', 100,
            )
        )
        if not profile['allowed_recipients'] and not profile[
                'allowed_domains']:
            raise ReportDeliveryError(
                'SMTP profile requires a recipient allowlist'
            )

    @staticmethod
    def _s3(profile):
        profile['bucket'] = _required_text(
            profile.get('bucket'), 'object-storage bucket', 255
        )
        prefix = profile.get('prefix', '')
        if not isinstance(prefix, str) or prefix.startswith('/') or '..' in (
                PurePosixPath(prefix).parts if prefix else ()):
            raise ReportDeliveryError('object-storage prefix is invalid')
        profile['prefix'] = prefix.strip('/')
        profile['region_name'] = profile.get('region_name')
        profile['endpoint_url'] = profile.get('endpoint_url')
        profile['profile_name'] = profile.get('profile_name')
        profile['role_arn'] = profile.get('role_arn')
        profile['external_id'] = profile.get('external_id')
        for name in (
            'region_name', 'endpoint_url', 'profile_name', 'role_arn',
            'external_id',
        ):
            if profile[name] is not None:
                profile[name] = _required_text(profile[name], name, 2048)
        if profile['endpoint_url'] is not None and not profile[
                'endpoint_url'].casefold().startswith('https://'):
            raise ReportDeliveryError(
                'object-storage endpoint URL must use HTTPS'
            )
        sse = profile.get('server_side_encryption', 'AES256')
        if sse not in {'AES256', 'aws:kms'}:
            raise ReportDeliveryError(
                'object-storage encryption mode is unsupported'
            )
        profile['server_side_encryption'] = sse
        profile['kms_key_id'] = profile.get('kms_key_id')
        if sse == 'aws:kms':
            profile['kms_key_id'] = _required_text(
                profile.get('kms_key_id'), 'KMS key ID', 2048
            )

    @staticmethod
    def _email(value, name):
        value = _required_text(value, name, 320)
        if '\r' in value or '\n' in value:
            raise ReportDeliveryError(f'{name} is invalid')
        display, address = parseaddr(value)
        if display or address != value or address.count('@') != 1:
            raise ReportDeliveryError(f'{name} is invalid')
        local, domain = address.rsplit('@', 1)
        if (
            not local or not domain or
            ('.' not in domain and domain != 'localhost')
        ):
            raise ReportDeliveryError(f'{name} is invalid')
        return address

    def normalize_target(self, profile, target):
        if not isinstance(target, dict):
            raise ReportDeliveryError('delivery target must be an object')
        if profile['kind'] == 'smtp':
            if set(target) != {'recipients'}:
                raise ReportDeliveryError('SMTP target fields are invalid')
            recipients = _string_list(
                target.get('recipients'), 'recipients', MAX_RECIPIENTS
            )
            if not recipients:
                raise ReportDeliveryError('at least one recipient is required')
            normalized = []
            for recipient in recipients:
                recipient = self._email(recipient, 'recipient').casefold()
                domain = recipient.rsplit('@', 1)[1]
                if recipient not in profile['allowed_recipients'] and domain \
                        not in profile['allowed_domains']:
                    raise ReportDeliveryError(
                        'recipient is outside the configured allowlist'
                    )
                normalized.append(recipient)
            if len(set(normalized)) != len(normalized):
                raise ReportDeliveryError('recipients contain a duplicate')
            return {'recipients': normalized}
        if set(target) != {'object_name'}:
            raise ReportDeliveryError(
                'object-storage target fields are invalid'
            )
        object_name = _required_text(
            target.get('object_name'), 'object name', 255
        )
        path = PurePosixPath(object_name)
        if (
            path.name != object_name or object_name in {'.', '..'} or
            '\\' in object_name or any(ord(item) < 32 for item in object_name)
        ):
            raise ReportDeliveryError('object name must be a single filename')
        return {'object_name': object_name}

    @staticmethod
    def target_summary(profile, target):
        if profile['kind'] == 'smtp':
            domains = sorted({
                item.rsplit('@', 1)[1] for item in target['recipients']
            })
            return {
                'recipient_count': len(target['recipients']),
                'recipient_domains': domains,
            }
        prefix = profile['prefix']
        key = f"{prefix}/{target['object_name']}" if prefix else target[
            'object_name']
        return {
            'bucket': profile['bucket'], 'object_key': key,
        }


class SMTPDeliveryAdapter:
    """Submit one MIME attachment over authenticated SMTP."""

    def __init__(self, smtp_factory=None, smtp_ssl_factory=None):
        self.smtp_factory = smtp_factory or smtplib.SMTP
        self.smtp_ssl_factory = smtp_ssl_factory or smtplib.SMTP_SSL

    def deliver(self, profile, target, payload):
        message = EmailMessage()
        message['From'] = profile['sender']
        message['To'] = ', '.join(target['recipients'])
        message['Subject'] = f"CDEadmin report: {payload['filename']}"
        message['Message-ID'] = make_msgid(domain='cdeadmin.local')
        message.set_content(
            'CDEadmin generated the attached bounded, redacted report export.'
        )
        maintype, subtype = payload['media_type'].split('/', 1)
        message.add_attachment(
            payload['content'], maintype=maintype, subtype=subtype,
            filename=payload['filename'],
        )
        submitted = False
        try:
            factory = (
                self.smtp_ssl_factory if profile['use_ssl']
                else self.smtp_factory
            )
            arguments = {
                'host': profile['host'], 'port': profile['port'],
                'timeout': profile['timeout_seconds'],
            }
            if profile['use_ssl']:
                arguments['context'] = ssl.create_default_context()
            with factory(**arguments) as client:
                if profile['use_starttls']:
                    client.starttls(context=ssl.create_default_context())
                client.login(profile['username'], profile['password'])
                submitted = True
                refused = client.send_message(message)
            if refused:
                raise DeliveryTransportError(
                    'SMTP server refused one or more recipients',
                    outcome_unknown=True,
                )
        except DeliveryTransportError:
            raise
        except Exception as exc:
            raise DeliveryTransportError(
                'SMTP delivery failed', outcome_unknown=submitted,
            ) from exc
        return {
            'transport': 'smtp',
            'message_id': message.get('Message-ID'),
            'recipient_count': len(target['recipients']),
        }


class S3DeliveryAdapter:
    """Put one immutable-by-request report object using boto3 authority."""

    def __init__(self, session_factory=None):
        self.session_factory = session_factory

    def deliver(self, profile, target, payload):
        try:
            session = self._session(profile)
            credentials = None
            if profile.get('role_arn'):
                assume = {
                    'RoleArn': profile['role_arn'],
                    'RoleSessionName': 'cdeadmin-report-delivery',
                }
                if profile.get('external_id'):
                    assume['ExternalId'] = profile['external_id']
                credentials = session.client('sts').assume_role(
                    **assume
                )['Credentials']
            elif session.get_credentials() is None:
                raise ReportDeliveryError(
                    'object-storage credentials are unavailable'
                )
            client_args = {
                'region_name': profile.get('region_name'),
                'endpoint_url': profile.get('endpoint_url'),
            }
            if credentials:
                client_args.update({
                    'aws_access_key_id': credentials['AccessKeyId'],
                    'aws_secret_access_key': credentials['SecretAccessKey'],
                    'aws_session_token': credentials['SessionToken'],
                })
            client = session.client(
                's3', **{key: value for key, value in client_args.items()
                         if value is not None}
            )
        except ReportDeliveryError as exc:
            raise DeliveryTransportError(
                'object-storage authentication is unavailable',
                outcome_unknown=False,
            ) from exc
        except Exception as exc:
            raise DeliveryTransportError(
                'object-storage authentication failed',
                outcome_unknown=False,
            ) from exc
        try:
            prefix = profile['prefix']
            key = f"{prefix}/{target['object_name']}" if prefix else target[
                'object_name']
            request = {
                'Bucket': profile['bucket'], 'Key': key,
                'Body': payload['content'],
                'ContentType': payload['media_type'],
                'ChecksumSHA256': base64.b64encode(
                    bytes.fromhex(payload['sha256'])
                ).decode('ascii'),
                'ServerSideEncryption': profile['server_side_encryption'],
                'Metadata': {
                    'cdeadmin-sha256': payload['sha256'],
                    'cdeadmin-request-id': payload['request_key'],
                },
            }
            if profile.get('kms_key_id'):
                request['SSEKMSKeyId'] = profile['kms_key_id']
            response = client.put_object(**request)
        except Exception as exc:
            raise DeliveryTransportError(
                'object-storage delivery outcome is unknown',
                outcome_unknown=True,
            ) from exc
        return {
            'transport': 's3', 'bucket': profile['bucket'],
            'object_key': key, 'etag': response.get('ETag'),
            'version_id': response.get('VersionId'),
            'checksum_sha256': response.get('ChecksumSHA256'),
        }

    def _session(self, profile):
        if self.session_factory is not None:
            return self.session_factory(profile.get('profile_name'))
        try:
            import boto3
        except ImportError as exc:
            raise ReportDeliveryError(
                'boto3 is required for object-storage delivery'
            ) from exc
        return boto3.Session(profile_name=profile.get('profile_name'))


class DatabaseDeliveryOccurrenceRepository:
    """Persist delivery identity/transitions, never report bytes/secrets."""

    def __init__(self):
        from pgadmin.model import CDEReportDeliveryOccurrence, db
        self.model = CDEReportDeliveryOccurrence
        self.db = db

    def get(self, user_id, endpoint_id, request_key):
        return self.model.query.filter_by(
            user_id=user_id, endpoint_id=endpoint_id,
            request_key=request_key,
        ).first()

    def begin(self, **values):
        from sqlalchemy.exc import IntegrityError
        row = self.model(**values)
        try:
            self.db.session.add(row)
            self.db.session.commit()
            return row, True
        except IntegrityError:
            self.db.session.rollback()
            existing = self.get(
                values['user_id'], values['endpoint_id'],
                values['request_key'],
            )
            if existing is None:
                raise ReportDeliveryError(
                    'delivery occurrence could not be persisted'
                ) from None
            return existing, False

    def transition(self, row, state, **values):
        if state not in DELIVERY_STATES:
            raise ReportDeliveryError('delivery occurrence state is invalid')
        row.state = state
        for key, value in values.items():
            setattr(row, key, value)
        self.db.session.commit()
        return row

    def list(self, user_id, endpoint_id, limit=100):
        return self.model.query.filter_by(
            user_id=user_id, endpoint_id=endpoint_id,
        ).order_by(self.model.created_at.desc()).limit(limit).all()

    def recover_stale(self, user_id, endpoint_id, cutoff, now):
        rows = self.model.query.filter(
            self.model.user_id == user_id,
            self.model.endpoint_id == endpoint_id,
            self.model.state.in_(('prepared', 'delivering')),
            self.model.created_at < cutoff,
        ).all()
        for row in rows:
            row.state = (
                'outcome_unknown' if row.state == 'delivering' else 'failed'
            )
            row.error_type = 'InterruptedDeliveryAttempt'
            row.completed_at = now
        if rows:
            self.db.session.commit()
        return len(rows)

    def prune(self, before):
        count = self.model.query.filter(
            self.model.state.in_(('delivered', 'failed', 'outcome_unknown')),
            self.model.completed_at.isnot(None),
            self.model.completed_at < before,
        ).delete(synchronize_session=False)
        if count:
            self.db.session.commit()
        return count


class ReportDeliveryService:
    """Attempt each idempotent delivery once with explicit finality limits."""

    def __init__(
        self, profiles=None, repository=None, adapters=None,
        retention_days=90, stale_attempt_seconds=600,
    ):
        self.retention_days = _positive_int(
            retention_days, 'delivery retention days', 3650
        )
        self.stale_attempt_seconds = _positive_int(
            stale_attempt_seconds, 'stale delivery seconds', 86400
        )
        self.profiles = (
            profiles if isinstance(profiles, DeliveryProfileRegistry)
            else DeliveryProfileRegistry(profiles)
        )
        self.repository = repository or DatabaseDeliveryOccurrenceRepository()
        self.adapters = adapters or {
            'smtp': SMTPDeliveryAdapter(), 's3': S3DeliveryAdapter(),
        }

    def catalog(self):
        value = self.profiles.catalog()
        value['retention_days'] = self.retention_days
        value['stale_attempt_seconds'] = self.stale_attempt_seconds
        return value

    def deliver(self, user_id, endpoint_id, request):
        if not isinstance(user_id, int) or isinstance(user_id, bool):
            raise ReportDeliveryError('delivery owner is invalid')
        endpoint_id = _uuid(endpoint_id, 'endpoint_id')
        self._prune()
        if not isinstance(request, dict) or set(request) != {
            'request_key', 'result_id', 'format', 'profile_id', 'target',
            'content', 'filename', 'media_type',
        }:
            raise ReportDeliveryError('delivery request fields are invalid')
        request_key = _uuid(request['request_key'], 'request_key')
        result_id = _required_text(request['result_id'], 'result_id', 128)
        export_format = _required_text(request['format'], 'format', 16)
        profile = self.profiles.get(request['profile_id'])
        adapter = self.adapters.get(profile['kind'])
        if adapter is None or not callable(getattr(adapter, 'deliver', None)):
            raise ReportDeliveryError(
                'report delivery transport is unavailable'
            )
        if export_format not in profile['allowed_formats']:
            raise ReportDeliveryError(
                'export format is not admitted by the delivery profile'
            )
        content = request['content']
        if not isinstance(content, bytes) or not content:
            raise ReportDeliveryError(
                'delivery content must be non-empty bytes'
            )
        if len(content) > profile['max_bytes']:
            raise ReportDeliveryError(
                'report export exceeds the delivery profile byte limit'
            )
        media_type = _required_text(
            request['media_type'], 'media_type', 255
        )
        if media_type != MEDIA_TYPES.get(export_format):
            raise ReportDeliveryError('delivery media type is inconsistent')
        filename = _required_text(request['filename'], 'filename', 255)
        if PurePosixPath(filename).name != filename:
            raise ReportDeliveryError('delivery filename is invalid')
        target = self.profiles.normalize_target(profile, request['target'])
        if profile['kind'] == 's3' and not target[
                'object_name'].casefold().endswith(f'.{export_format}'):
            raise ReportDeliveryError(
                'object filename extension must match the export format'
            )
        target_summary = self.profiles.target_summary(profile, target)
        intent_digest = hashlib.sha256(json.dumps({
            'endpoint_id': endpoint_id, 'result_id': result_id,
            'profile_id': profile['profile_id'], 'format': export_format,
            'target': target, 'content_sha256': hashlib.sha256(
                content
            ).hexdigest(),
        }, sort_keys=True, separators=(',', ':')).encode('utf-8')).hexdigest()
        now = datetime.now(timezone.utc)
        row, created = self.repository.begin(
            id=str(uuid.uuid4()), request_key=request_key, user_id=user_id,
            endpoint_id=endpoint_id, result_id=result_id,
            profile_id=profile['profile_id'], channel=profile['kind'],
            export_format=export_format, intent_digest=intent_digest,
            target_summary=json.dumps(
                target_summary, sort_keys=True, separators=(',', ':')
            ),
            state='prepared', provider_receipt=None, error_type=None,
            created_at=now, started_at=None, completed_at=None,
        )
        if not created:
            if row.intent_digest != intent_digest:
                raise ReportDeliveryConflict(
                    'delivery request key was reused for different intent'
                )
            value = self._present(row)
            value['replayed_request'] = True
            return value
        self.repository.transition(
            row, 'delivering', started_at=datetime.now(timezone.utc)
        )
        payload = {
            'content': content, 'filename': filename,
            'media_type': media_type, 'request_key': request_key,
            'sha256': hashlib.sha256(content).hexdigest(),
        }
        try:
            receipt = adapter.deliver(profile, target, payload)
        except DeliveryTransportError as exc:
            state = 'outcome_unknown' if exc.outcome_unknown else 'failed'
            self.repository.transition(
                row, state, error_type=type(exc).__name__,
                completed_at=datetime.now(timezone.utc),
            )
        except Exception as exc:
            self.repository.transition(
                row, 'outcome_unknown', error_type=type(exc).__name__,
                completed_at=datetime.now(timezone.utc),
            )
        else:
            receipt = redact(receipt)
            self.repository.transition(
                row, 'delivered', provider_receipt=json.dumps(
                    receipt, sort_keys=True, separators=(',', ':')
                ), completed_at=datetime.now(timezone.utc),
            )
        return self._present(row)

    def list(self, user_id, endpoint_id):
        endpoint_id = _uuid(endpoint_id, 'endpoint_id')
        now = datetime.now(timezone.utc)
        recover = getattr(self.repository, 'recover_stale', None)
        if callable(recover):
            recover(
                user_id, endpoint_id,
                now - timedelta(seconds=self.stale_attempt_seconds), now,
            )
        self._prune(now)
        return [self._present(row) for row in self.repository.list(
            user_id, endpoint_id
        )]

    def _prune(self, now=None):
        callback = getattr(self.repository, 'prune', None)
        if callable(callback):
            now = now or datetime.now(timezone.utc)
            callback(now - timedelta(days=self.retention_days))

    @staticmethod
    def _present(row):
        def iso(value):
            return value.isoformat() if value is not None else None

        return {
            'schema': 'cdeadmin.report-delivery.occurrence.v1',
            'occurrence_id': row.id, 'request_key': row.request_key,
            'endpoint_id': row.endpoint_id, 'result_id': row.result_id,
            'profile_id': row.profile_id, 'channel': row.channel,
            'format': row.export_format, 'state': row.state,
            'target_summary': json.loads(row.target_summary),
            'provider_receipt': json.loads(row.provider_receipt)
            if row.provider_receipt else None,
            'error_type': row.error_type,
            'created_at': iso(row.created_at),
            'started_at': iso(row.started_at),
            'completed_at': iso(row.completed_at),
            'automatic_retry': False,
            'replayed_request': False,
        }
