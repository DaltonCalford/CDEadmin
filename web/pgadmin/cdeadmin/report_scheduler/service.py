##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Durable, revocable authority for unattended semantic reports."""

from __future__ import annotations

import hashlib
import json
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from croniter import CroniterBadCronError, croniter

from pgadmin.cdeadmin.core import EndpointContext
from pgadmin.cdeadmin.security import SecretReference

from .crypto import WorkerKeyRing
from .models import (
    ReportSchedulerAccessError,
    ReportSchedulerConflict,
    ReportSchedulerError,
    ReportSchedulerUnavailable,
    TERMINAL_STATES,
)


RESOLVER_ID = 'cdeadmin.delegated-report-worker'
SCHEDULER_PERMISSIONS = frozenset({
    'network', 'secret_read', 'data_read', 'execute',
    'embedded_runtime', 'filesystem',
})


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _digest(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def _json(value):
    return json.dumps(
        value, sort_keys=True, separators=(',', ':'), ensure_ascii=False,
    )


def _aad(delegation, credential_id, secret_kind, generation=None):
    return {
        'schema': 'cdeadmin.report-worker-credential.v1',
        'delegation_id': delegation.id,
        'credential_id': credential_id,
        'endpoint_id': delegation.endpoint_id,
        'endpoint_mode': delegation.endpoint.endpoint_mode,
        'endpoint_generation': delegation.endpoint_generation,
        'secret_kind': secret_kind,
        'credential_generation': (
            delegation.credential_generation
            if generation is None else generation
        ),
    }


class DelegatedCredentialResolver:
    """Resolve only one active grant for its dedicated worker principal."""

    def __init__(self, key_ring):
        self.key_ring = key_ring

    def __call__(self, locator, context, _purpose, principal):
        from pgadmin.model import CDEReportDelegatedCredential

        row = CDEReportDelegatedCredential.query.filter_by(id=locator).first()
        delegation = row.delegation if row is not None else None
        now = _now()
        if row is None or delegation is None or (
            delegation.state != 'active' or
            delegation.expires_at <= now or
            principal != f'worker:{delegation.id}' or
            context.endpoint_id != delegation.endpoint_id or
            context.mode != delegation.endpoint.endpoint_mode or
            context.runtime_identity_generation != (
                delegation.endpoint_generation
            )
        ):
            raise ReportSchedulerAccessError(
                'delegated report credential is unavailable'
            )
        return self.key_ring.decrypt(
            row.encrypted_value,
            _aad(delegation, row.id, row.secret_kind),
        )


class DatabaseReportSchedulerRepository:
    """Database transitions for grants and at-most-once occurrences."""

    def __init__(self):
        from pgadmin.model import (
            CDEReportDelegatedCredential,
            CDEReportDelegation,
            CDEReportScheduleOccurrence,
            db,
        )
        self.db = db
        self.credential = CDEReportDelegatedCredential
        self.delegation = CDEReportDelegation
        self.occurrence = CDEReportScheduleOccurrence

    def get_grant(self, user_id, endpoint_id, report_id):
        return self.delegation.query.filter_by(
            user_id=user_id, endpoint_id=endpoint_id, report_id=report_id,
        ).first()

    def get_grant_id(self, delegation_id):
        return self.delegation.query.filter_by(id=delegation_id).first()

    def active_grants(self):
        return self.delegation.query.filter_by(state='active').all()

    def save_grant(self, row, credentials):
        self.db.session.add(row)
        self.db.session.flush()
        for old in list(row.credentials):
            self.db.session.delete(old)
        self.db.session.flush()
        for credential in credentials:
            self.db.session.add(self.credential(**credential))
        self.db.session.commit()
        return row

    def revoke(self, row, now):
        row.state = 'revoked'
        row.revoked_at = now
        row.updated_at = now
        for credential in list(row.credentials):
            self.db.session.delete(credential)
        self.occurrence.query.filter_by(
            delegation_id=row.id, state='scheduled'
        ).update({
            'state': 'cancelled', 'phase': 'revoked',
            'updated_at': now, 'completed_at': now,
        }, synchronize_session=False)
        self.db.session.commit()

    def expire(self, row, now):
        row.state = 'expired'
        row.updated_at = now
        for credential in list(row.credentials):
            self.db.session.delete(credential)
        self.occurrence.query.filter_by(
            delegation_id=row.id, state='scheduled'
        ).update({
            'state': 'cancelled', 'phase': 'delegation-expired',
            'updated_at': now, 'completed_at': now,
        }, synchronize_session=False)
        self.db.session.commit()

    def enqueue(self, delegation_id, scheduled_for, state='scheduled',
                error_type=None):
        from sqlalchemy.exc import IntegrityError

        now = _now()
        row = self.occurrence(
            id=str(uuid.uuid4()), delegation_id=delegation_id,
            scheduled_for=scheduled_for, state=state, phase='queued',
            progress=_json({'completed': 0, 'total': None}),
            error_type=error_type, created_at=now, updated_at=now,
            completed_at=now if state in TERMINAL_STATES else None,
        )
        try:
            self.db.session.add(row)
            self.db.session.commit()
            return row, True
        except IntegrityError:
            self.db.session.rollback()
            return self.occurrence.query.filter_by(
                delegation_id=delegation_id,
                scheduled_for=scheduled_for,
            ).first(), False

    def latest(self, delegation_id):
        return self.occurrence.query.filter_by(
            delegation_id=delegation_id
        ).order_by(self.occurrence.scheduled_for.desc()).first()

    def list(self, user_id, endpoint_id, report_id=None, limit=100):
        query = self.occurrence.query.join(self.delegation).filter(
            self.delegation.user_id == user_id,
            self.delegation.endpoint_id == endpoint_id,
        )
        if report_id is not None:
            query = query.filter(self.delegation.report_id == report_id)
        return query.order_by(
            self.occurrence.scheduled_for.desc()
        ).limit(limit).all()

    def claim(self, worker_id, lease_seconds):
        now = _now()
        row = self.occurrence.query.filter(
            self.occurrence.state == 'scheduled',
            self.occurrence.scheduled_for <= now,
        ).order_by(self.occurrence.scheduled_for).first()
        if row is None:
            return None
        token = secrets.token_urlsafe(32)
        changed = self.occurrence.query.filter_by(
            id=row.id, state='scheduled'
        ).update({
            'state': 'claimed', 'phase': 'claimed',
            'claim_token_digest': _digest(token), 'claimed_by': worker_id,
            'lease_expires_at': now + timedelta(seconds=lease_seconds),
            'updated_at': now,
        }, synchronize_session=False)
        self.db.session.commit()
        if changed != 1:
            return None
        return self.occurrence.query.filter_by(id=row.id).first(), token

    def transition(self, occurrence_id, token, state, phase, **values):
        if state not in TERMINAL_STATES | {
            'executing', 'delivering', 'cancel_requested'
        }:
            raise ReportSchedulerError('scheduler state is invalid')
        row = self.occurrence.query.filter_by(id=occurrence_id).first()
        if row is None or row.claim_token_digest != _digest(token):
            raise ReportSchedulerConflict('scheduler claim is unavailable')
        row.state = state
        row.phase = phase
        row.updated_at = _now()
        if state in TERMINAL_STATES:
            row.completed_at = row.updated_at
            row.lease_expires_at = None
        for key, value in values.items():
            setattr(row, key, value)
        self.db.session.commit()
        return row

    def heartbeat(self, occurrence_id, token, lease_seconds, progress):
        row = self.occurrence.query.filter_by(id=occurrence_id).first()
        if row is None or row.claim_token_digest != _digest(token) or (
            row.state not in {'claimed', 'executing', 'delivering'}
        ):
            raise ReportSchedulerConflict('scheduler claim is unavailable')
        row.lease_expires_at = _now() + timedelta(seconds=lease_seconds)
        row.progress = _json(progress)
        row.updated_at = _now()
        self.db.session.commit()
        return row

    def cancel(self, row):
        now = _now()
        if row.state == 'scheduled':
            row.state = 'cancelled'
            row.phase = 'cancelled-before-claim'
            row.completed_at = now
        elif row.state in {'claimed', 'executing', 'delivering'}:
            row.state = 'cancel_requested'
            row.phase = 'cancellation-requested'
        row.updated_at = now
        self.db.session.commit()
        return row

    def recover_stale(self):
        now = _now()
        rows = self.occurrence.query.filter(
            self.occurrence.state.in_((
                'claimed', 'executing', 'delivering', 'cancel_requested',
            )),
            self.occurrence.lease_expires_at < now,
        ).all()
        for row in rows:
            row.state = (
                'failed' if row.state == 'claimed' else 'outcome_unknown'
            )
            row.phase = 'lease-expired'
            row.error_type = 'WorkerLeaseExpired'
            row.updated_at = now
            row.completed_at = now
            row.lease_expires_at = None
        if rows:
            self.db.session.commit()
        return len(rows)


class ReportSchedulerService:
    """Create owner grants and construct exact worker endpoint authority."""

    def __init__(
        self, endpoint_service, security_service, semantic_service,
        delivery_service, key_ring=None, repository=None,
        lease_seconds=300, max_lateness_seconds=3600,
    ):
        self.endpoint_service = endpoint_service
        self.security_service = security_service
        self.semantic_service = semantic_service
        self.delivery_service = delivery_service
        self.key_ring = key_ring or WorkerKeyRing()
        self.repository = repository or DatabaseReportSchedulerRepository()
        self.lease_seconds = self._positive(
            lease_seconds, 'scheduler lease seconds', 86400
        )
        self.max_lateness_seconds = self._positive(
            max_lateness_seconds, 'scheduler lateness seconds', 604800
        )
        security_service.secrets.register_resolver(
            RESOLVER_ID, DelegatedCredentialResolver(self.key_ring)
        )

    @property
    def available(self):
        return self.key_ring.available

    def catalog(self):
        return {
            'schema': 'cdeadmin.report-scheduler.catalog.v1',
            'automatic_scheduling': self.available,
            'worker_authority': 'server-managed-key-ring',
            'credential_export': False,
            'lease_seconds': self.lease_seconds,
            'max_lateness_seconds': self.max_lateness_seconds,
        }

    def authorize(self, server, model_id, report_id, expires_in_days=30):
        if not self.available:
            raise ReportSchedulerUnavailable(
                'report worker key authority is not configured'
            )
        if isinstance(expires_in_days, bool) or not isinstance(
                expires_in_days, int) or not 1 <= expires_in_days <= 365:
            raise ReportSchedulerError(
                'report delegation expiry must be 1 to 365 days'
            )
        endpoint = server.endpoint_profile
        user_id = server.user_id
        record, model, report, schedule = self._definition(
            user_id, endpoint.id, model_id, report_id
        )
        context, endpoint_payload, _root = self.endpoint_service.workspace(
            server
        )
        route = endpoint_payload['route']
        endpoint_generation = (
            endpoint.profile_generation or endpoint.cache_namespace
        )
        existing = self.repository.get_grant(
            user_id, endpoint.id, report_id
        )
        now = _now()
        if existing is None:
            from pgadmin.model import CDEReportDelegation
            row = CDEReportDelegation(
                id=str(uuid.uuid4()), user_id=user_id,
                endpoint_id=endpoint.id, model_id=model_id,
                report_id=report_id, schedule_id=schedule['id'],
                state='active', credential_generation=1,
                created_at=now,
            )
            row.endpoint = endpoint
        else:
            row = existing
            row.credential_generation += 1
        row.route_id = str(route['route_id'])
        row.primary_secret_kind = route.get('credential_kind')
        if route.get('credential_reference_id') and not (
                row.primary_secret_kind):
            row.primary_secret_kind = 'database_password'
        row.endpoint_generation = endpoint_generation
        row.model_id = model_id
        row.model_revision = record['revision']
        row.definition_digest = self._definition_digest(model)
        row.delivery_scope = _json(schedule['delivery'])
        row.schedule_id = schedule['id']
        row.state = 'active'
        row.updated_at = now
        row.expires_at = now + timedelta(days=expires_in_days)
        row.revoked_at = None
        try:
            credentials = self._capture_credentials(
                server, context, route, row
            )
            self.repository.save_grant(row, credentials)
        except Exception:
            rollback = getattr(
                getattr(self.repository, 'db', None), 'session', None
            )
            if rollback is not None:
                rollback.rollback()
            raise
        return self.present_grant(row)

    def revoke(self, user_id, endpoint_id, report_id):
        row = self._owned_grant(user_id, endpoint_id, report_id)
        self.repository.revoke(row, _now())
        return self.present_grant(row)

    def list_grants(self, user_id, endpoint_id):
        from pgadmin.model import CDEReportDelegation
        rows = CDEReportDelegation.query.filter_by(
            user_id=user_id, endpoint_id=endpoint_id
        ).order_by(CDEReportDelegation.created_at.desc()).all()
        return [self.present_grant(row) for row in rows]

    def enqueue_due(self):
        now = _now()
        counts = {'scheduled': 0, 'missed': 0, 'expired': 0}
        for row in self.repository.active_grants():
            if row.expires_at <= now:
                self.repository.expire(row, now)
                counts['expired'] += 1
                continue
            try:
                _record, _model, _report, schedule = self._definition(
                    row.user_id, row.endpoint_id, row.model_id,
                    row.report_id, row=row,
                )
            except ReportSchedulerError:
                continue
            latest = self.repository.latest(row.id)
            base = latest.scheduled_for if latest else row.created_at
            for due in self._due_instants(schedule, base, now):
                late = (now - due).total_seconds()
                state = 'failed' if late > self.max_lateness_seconds else (
                    'scheduled'
                )
                _occurrence, created = self.repository.enqueue(
                    row.id, due, state=state,
                    error_type='SchedulerMisfire' if state == 'failed'
                    else None,
                )
                if created:
                    counts['missed' if state == 'failed' else 'scheduled'] += 1
        return counts

    def claim(self, worker_id):
        if not self.available:
            raise ReportSchedulerUnavailable(
                'report worker key authority is not configured'
            )
        if not isinstance(worker_id, str) or not worker_id.strip() or (
                len(worker_id) > 128):
            raise ReportSchedulerError('worker identity is invalid')
        return self.repository.claim(worker_id.strip(), self.lease_seconds)

    def occurrences(self, user_id, endpoint_id, report_id=None):
        return [self.present_occurrence(row) for row in self.repository.list(
            user_id, endpoint_id, report_id=report_id
        )]

    def cancel(self, user_id, endpoint_id, occurrence_id):
        from pgadmin.model import (
            CDEReportDelegation, CDEReportScheduleOccurrence,
        )
        row = CDEReportScheduleOccurrence.query.join(
            CDEReportDelegation
        ).filter(
            CDEReportScheduleOccurrence.id == occurrence_id,
            CDEReportDelegation.user_id == user_id,
            CDEReportDelegation.endpoint_id == endpoint_id,
        ).first()
        if row is None:
            raise ReportSchedulerAccessError(
                'report occurrence is unavailable'
            )
        return self.present_occurrence(self.repository.cancel(row))

    def delegated_workspace(self, row):
        """Rebuild a generation-bound endpoint with delegated references."""
        endpoint = row.endpoint
        now = _now()
        if row.state != 'active' or row.expires_at <= now:
            raise ReportSchedulerAccessError(
                'report delegation is inactive or expired'
            )
        generation = endpoint.profile_generation or endpoint.cache_namespace
        if generation != row.endpoint_generation:
            raise ReportSchedulerConflict(
                'endpoint changed after report authorization'
            )
        if endpoint.provider_version is None:
            return self._postgresql_workspace(row)
        runtime = endpoint.runtime_identity
        if runtime is None or runtime.verification_state != 'verified':
            raise ReportSchedulerConflict(
                'endpoint is no longer verified'
            )
        route_model = next(
            (item for item in endpoint.routes if item.id == row.route_id),
            None,
        )
        if route_model is None:
            raise ReportSchedulerConflict(
                'authorized endpoint route is unavailable'
            )
        route = self.endpoint_service._route_configuration(route_model)
        route['route_id'] = route_model.id
        context = self.endpoint_service._context(
            endpoint, SCHEDULER_PERMISSIONS
        )
        self._bind_delegated_references(row, context, route)
        binding = self.endpoint_service.provider_registry.resolve(context)
        identity = dict(binding.manifest['identity'])
        payload = {
            'identity': identity, 'endpoint_id': endpoint.id,
            'mode': endpoint.endpoint_mode,
            'declared_runtime': {
                'engine_id': runtime.declared_runtime_family,
                'version': runtime.declared_runtime_version,
            },
            'verified_runtime': {
                'engine_id': runtime.verified_runtime_family,
                'version': runtime.verified_runtime_version,
                'verification_state': runtime.verification_state,
                'evidence_reference': (
                    runtime.verification_evidence_reference
                ),
            },
            'route': route,
            'capability_generation': endpoint.cache_namespace,
            'extensions': {},
        }
        return context, payload

    def definition_for_worker(self, row):
        record, model, report, schedule = self._definition(
            row.user_id, row.endpoint_id, row.model_id, row.report_id,
            row=row,
        )
        return record, model, report, schedule

    def heartbeat(self, occurrence_id, token, progress):
        return self.repository.heartbeat(
            occurrence_id, token, self.lease_seconds, progress
        )

    def transition(self, occurrence_id, token, state, phase, **values):
        return self.repository.transition(
            occurrence_id, token, state, phase, **values
        )

    def recover_stale(self):
        return self.repository.recover_stale()

    def rotate_keys(self):
        """Re-encrypt every retained grant under the configured active key."""
        if not self.available:
            raise ReportSchedulerUnavailable(
                'report worker key authority is not configured'
            )
        from pgadmin.model import CDEReportDelegatedCredential, db
        rows = CDEReportDelegatedCredential.query.all()
        rotated = 0
        try:
            for item in rows:
                if item.key_id == self.key_ring.active_key_id:
                    continue
                item.encrypted_value = self.key_ring.rotate(
                    item.encrypted_value,
                    _aad(
                        item.delegation, item.id, item.secret_kind
                    ),
                )
                item.key_id = self.key_ring.active_key_id
                rotated += 1
            db.session.commit()
        except Exception:
            db.session.rollback()
            raise
        return {'rotated': rotated, 'active_key_id': (
            self.key_ring.active_key_id
        )}

    def _definition(self, user_id, endpoint_id, model_id, report_id,
                    row=None):
        record = self.semantic_service.get(user_id, endpoint_id, model_id)
        if record['status'] != 'published':
            raise ReportSchedulerError(
                'scheduled reports require a published semantic model'
            )
        model = record['definition']
        report = next((item for item in model['reports'] if item['id'] == (
            report_id)), None)
        if report is None or not report.get('schedule_id'):
            raise ReportSchedulerError(
                'report has no scheduled definition'
            )
        schedule = next((item for item in model['schedules'] if item['id'] == (
            report['schedule_id'])), None)
        if schedule is None or not schedule['enabled'] or not schedule[
                'delivery']:
            raise ReportSchedulerError(
                'report schedule is disabled or has no delivery'
            )
        self._validate_cron(schedule)
        if schedule['delivery']['format'] not in report['export_formats']:
            raise ReportSchedulerError(
                'scheduled delivery format is not enabled for the report'
            )
        self.delivery_service.profiles.get(
            schedule['delivery']['profile_id']
        )
        if row is not None and (
            row.model_revision != record['revision'] or
            row.definition_digest != self._definition_digest(model) or
            row.schedule_id != schedule['id'] or
            json.loads(row.delivery_scope) != schedule['delivery']
        ):
            raise ReportSchedulerConflict(
                'report definition changed after authorization'
            )
        return record, model, report, schedule

    def _capture_credentials(self, server, context, route, row):
        references = dict(route.get('credential_references', {}))
        primary = route.get('credential_reference_id')
        primary_kind = route.get('credential_kind', 'database_password')
        if primary is not None:
            references.setdefault(primary_kind, primary)
        credentials = []
        principal = f'user:{row.user_id}'
        for secret_kind, reference_id in sorted(references.items()):
            credential_id = str(uuid.uuid4())
            with self.security_service.secrets.acquire(
                reference_id, context, principal, 'connect',
                expected_kind=secret_kind,
            ) as lease:
                encrypted = lease.use(lambda value: self.key_ring.encrypt(
                    value, _aad(row, credential_id, secret_kind)
                ))
            credentials.append({
                'id': credential_id, 'delegation_id': row.id,
                'secret_kind': secret_kind,
                'key_id': self.key_ring.active_key_id,
                'encrypted_value': encrypted, 'created_at': _now(),
            })
        if (
            row.endpoint.provider_version is None and
            row.endpoint.provider_id == 'org.pgadmin.postgresql'
        ):
            credentials.extend(self._capture_postgresql(
                server, row, {item['secret_kind'] for item in credentials}
            ))
            if credentials and row.primary_secret_kind is None:
                row.primary_secret_kind = 'database_password'
        return credentials

    def _capture_postgresql(self, server, row, present):
        from flask_login import current_user
        from pgadmin.utils.crypto import decrypt
        from pgadmin.utils.master_password import get_crypt_key

        if not current_user.is_authenticated or current_user.id != row.user_id:
            raise ReportSchedulerAccessError(
                'PostgreSQL delegation requires its authenticated owner'
            )
        available, key = get_crypt_key()
        values = {
            'database_password': getattr(server, 'password', None),
            'tunnel_password': getattr(server, 'tunnel_password', None),
        }
        result = []
        for secret_kind, ciphertext in values.items():
            if secret_kind in present or ciphertext is None:
                continue
            if not available:
                raise ReportSchedulerAccessError(
                    'protected PostgreSQL credentials are unavailable'
                )
            decrypted = decrypt(ciphertext, key)
            if isinstance(decrypted, str):
                decrypted = decrypted.encode('utf-8')
            plaintext = bytearray(decrypted)
            credential_id = str(uuid.uuid4())
            try:
                envelope = self.key_ring.encrypt(
                    plaintext, _aad(row, credential_id, secret_kind)
                )
            finally:
                for index in range(len(plaintext)):
                    plaintext[index] = 0
            result.append({
                'id': credential_id, 'delegation_id': row.id,
                'secret_kind': secret_kind,
                'key_id': self.key_ring.active_key_id,
                'encrypted_value': envelope, 'created_at': _now(),
            })
        return result

    def _bind_delegated_references(self, row, context, route):
        references = {}
        for credential in row.credentials:
            reference = SecretReference(
                reference_id=credential.id,
                endpoint_id=row.endpoint_id,
                endpoint_mode=row.endpoint.endpoint_mode,
                secret_kind=credential.secret_kind,
                storage_kind='cdeadmin-worker-envelope',
                resolver_id=RESOLVER_ID,
                locator=credential.id,
                allowed_purposes=frozenset({'connect'}),
                authority_scope='legacy_engine_auth',
            )
            self.security_service.secrets.register_reference(reference)
            references[credential.secret_kind] = credential.id
        if references:
            route['credential_references'] = references
            primary_kind = row.primary_secret_kind or next(iter(references))
            if primary_kind in references:
                route['credential_reference_id'] = references[primary_kind]
                route['credential_kind'] = primary_kind
            route['principal_reference'] = f'worker:{row.id}'
        return route

    def _postgresql_workspace(self, row):
        from pgadmin.cdeadmin.providers.postgresql.provider import (
            PROFILE_ID, PROFILE_VERSION, PROVIDER_ID, PROVIDER_VERSION,
        )

        endpoint = row.endpoint
        server = endpoint.legacy_server or endpoint.legacy_shared_server
        if server is None:
            raise ReportSchedulerConflict(
                'authorized PostgreSQL server is unavailable'
            )
        identity = {
            'endpoint_id': endpoint.id,
            'endpoint_mode': 'legacy_native',
            'experience_family': 'postgresql',
            'provider_id': PROVIDER_ID,
            'provider_version': PROVIDER_VERSION,
            'profile_id': PROFILE_ID,
            'profile_version': PROFILE_VERSION,
            'target_adapter_id': 'legacy-pgadmin-server',
            'target_adapter_version': PROVIDER_VERSION,
            'pool_namespace': endpoint.pool_namespace,
            'session_namespace': endpoint.session_namespace,
            'cache_namespace': endpoint.cache_namespace,
            'diagnostic_namespace': endpoint.diagnostic_namespace,
            'declared_runtime_family': 'postgresql',
            'verified_runtime_family': 'postgresql',
            'verified_runtime_version': PROFILE_VERSION,
            'runtime_verification_state': 'verified',
            'runtime_evidence_reference': 'delegated-postgresql-profile',
            'runtime_identity_generation': row.endpoint_generation,
        }
        context = EndpointContext.from_identity(
            identity, effective_permissions=SCHEDULER_PERMISSIONS
        )
        route = {
            'route_id': row.route_id, 'server_id': server.id,
            'owner_id': row.user_id,
            'source_kind': (
                'server' if endpoint.legacy_server is not None
                else 'sharedserver'
            ),
        }
        self._bind_delegated_references(row, context, route)
        binding = self.endpoint_service.provider_registry.resolve(context)
        return context, {
            'identity': dict(binding.manifest['identity']),
            'endpoint_id': endpoint.id, 'mode': 'legacy_native',
            'declared_runtime': {
                'engine_id': 'postgresql', 'version': PROFILE_VERSION,
            },
            'verified_runtime': {
                'engine_id': 'postgresql', 'version': PROFILE_VERSION,
                'verification_state': 'verified',
                'evidence_reference': 'delegated-postgresql-profile',
            },
            'route': route,
            'capability_generation': endpoint.cache_namespace,
            'extensions': {},
        }

    def _owned_grant(self, user_id, endpoint_id, report_id):
        row = self.repository.get_grant(user_id, endpoint_id, report_id)
        if row is None:
            raise ReportSchedulerAccessError(
                'report delegation is unavailable'
            )
        return row

    @staticmethod
    def _definition_digest(model):
        return hashlib.sha256(_json(model).encode('utf-8')).hexdigest()

    @staticmethod
    def _validate_cron(schedule):
        if len(schedule['expression'].split()) != 5:
            raise ReportSchedulerError(
                'report schedules require a five-field cron expression'
            )
        try:
            zone = ZoneInfo(schedule['timezone'])
            croniter(schedule['expression'], datetime.now(zone)).get_next(
                datetime
            )
        except (
            CroniterBadCronError, ValueError, ZoneInfoNotFoundError,
        ) as exc:
            raise ReportSchedulerError(
                'report schedule expression or timezone is invalid'
            ) from exc

    @classmethod
    def _due_instants(cls, schedule, base, now):
        cls._validate_cron(schedule)
        zone = ZoneInfo(schedule['timezone'])
        base_aware = base.replace(tzinfo=timezone.utc).astimezone(zone)
        iterator = croniter(schedule['expression'], base_aware)
        result = []
        for _index in range(100):
            candidate = iterator.get_next(datetime)
            due = candidate.astimezone(timezone.utc).replace(tzinfo=None)
            if due > now:
                break
            result.append(due)
        return result

    @staticmethod
    def present_grant(row):
        state = row.state
        if state == 'active' and row.expires_at <= _now():
            state = 'expired'
        return {
            'schema': 'cdeadmin.report-delegation.v1',
            'delegation_id': row.id, 'endpoint_id': row.endpoint_id,
            'model_id': row.model_id, 'report_id': row.report_id,
            'schedule_id': row.schedule_id, 'state': state,
            'model_revision': row.model_revision,
            'endpoint_generation': row.endpoint_generation,
            'route_id': row.route_id,
            'credential_generation': row.credential_generation,
            'credential_count': len(row.credentials),
            'delivery': json.loads(row.delivery_scope),
            'created_at': row.created_at.isoformat(),
            'updated_at': row.updated_at.isoformat(),
            'expires_at': row.expires_at.isoformat(),
            'revoked_at': (
                row.revoked_at.isoformat() if row.revoked_at else None
            ),
            'credentials_exportable': False,
        }

    @staticmethod
    def present_occurrence(row):
        return {
            'schema': 'cdeadmin.report-schedule.occurrence.v1',
            'occurrence_id': row.id,
            'delegation_id': row.delegation_id,
            'report_id': row.delegation.report_id,
            'scheduled_for': row.scheduled_for.isoformat(),
            'state': row.state, 'phase': row.phase,
            'progress': json.loads(row.progress),
            'delivery_occurrence_ids': json.loads(
                row.delivery_occurrence_ids
            ) if row.delivery_occurrence_ids else [],
            'error_type': row.error_type,
            'claimed_by': row.claimed_by,
            'created_at': row.created_at.isoformat(),
            'updated_at': row.updated_at.isoformat(),
            'completed_at': (
                row.completed_at.isoformat() if row.completed_at else None
            ),
            'automatic_retry': False,
        }

    @staticmethod
    def _positive(value, label, maximum):
        if isinstance(value, bool) or not isinstance(value, int) or not (
                1 <= value <= maximum):
            raise ReportSchedulerError(f'{label} is invalid')
        return value
