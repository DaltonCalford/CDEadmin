##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Endpoint-scoped secret references and short-lived in-memory leases."""

from __future__ import annotations

import hashlib
import inspect
import threading
from collections import deque
from typing import Callable

from .models import SecretAccessError, SecretReference, required_string
from .redaction import redact


class SecretLease:
    """Best-effort zeroizing byte buffer with no value-bearing repr."""

    def __init__(self, value):
        if isinstance(value, str):
            value = value.encode('utf-8')
        if not isinstance(value, (bytes, bytearray, memoryview)):
            raise SecretAccessError('secret resolver must return bytes')
        if len(value) == 0:
            raise SecretAccessError('secret resolver returned an empty value')
        self._buffer = bytearray(value)
        self._closed = False

    @property
    def closed(self):
        return self._closed

    def use(self, callback: Callable):
        if self._closed:
            raise SecretAccessError('secret lease is closed')
        if not callable(callback):
            raise SecretAccessError('secret consumer must be callable')
        return callback(memoryview(self._buffer))

    def close(self):
        for index in range(len(self._buffer)):
            self._buffer[index] = 0
        self._closed = True

    def __enter__(self):
        if self._closed:
            raise SecretAccessError('secret lease is closed')
        return self

    def __exit__(self, _exc_type, _exc, _traceback):
        self.close()

    def __repr__(self):
        return '<SecretLease closed={} bytes={}>'.format(
            self._closed, len(self._buffer)
        )


class EndpointSecretService:
    """Resolve secret material only for its exact endpoint and mode."""

    def __init__(self, max_audit_events=1000):
        if max_audit_events < 1:
            raise SecretAccessError('secret audit limit must be positive')
        self._references = {}
        self._resolvers = {}
        self._audit = deque(maxlen=max_audit_events)
        self._lock = threading.RLock()

    def register_resolver(self, resolver_id, resolver):
        resolver_id = required_string(resolver_id, 'resolver_id')
        if not callable(resolver):
            raise SecretAccessError('secret resolver must be callable')
        with self._lock:
            if resolver_id in self._resolvers:
                raise SecretAccessError(
                    'secret resolver is already registered'
                )
            self._resolvers[resolver_id] = resolver

    def register_reference(self, reference):
        if not isinstance(reference, SecretReference):
            raise SecretAccessError('secret reference model is required')
        with self._lock:
            existing = self._references.get(reference.reference_id)
            if existing is not None and existing != reference:
                raise SecretAccessError(
                    'secret reference ID is already bound'
                )
            self._references[reference.reference_id] = reference

    def acquire(
        self, reference_id, context, principal, purpose,
        expected_kind=None,
    ):
        reference_id = required_string(reference_id, 'reference_id')
        principal = required_string(principal, 'principal')
        purpose = required_string(purpose, 'purpose')
        if expected_kind is not None:
            expected_kind = required_string(expected_kind, 'expected_kind')
        with self._lock:
            reference = self._references.get(reference_id)
            if reference is None:
                self._record('secret.access_denied', reference_id, context, {
                    'reason': 'unknown_reference', 'purpose': purpose,
                })
                raise SecretAccessError('secret reference is unavailable')
            denial = self._denial(
                reference, context, purpose, expected_kind
            )
            if denial is not None:
                self._record('secret.access_denied', reference_id, context, {
                    'reason': denial, 'purpose': purpose,
                })
                raise SecretAccessError('secret access is not authorized')
            resolver = self._resolvers.get(reference.resolver_id)
            if resolver is None:
                self._record('secret.access_denied', reference_id, context, {
                    'reason': 'resolver_unavailable', 'purpose': purpose,
                })
                raise SecretAccessError('secret resolver is unavailable')
        try:
            try:
                inspect.signature(resolver).bind(
                    reference.locator, context, purpose, principal
                )
            except (TypeError, ValueError):
                value = resolver(reference.locator, context, purpose)
            else:
                value = resolver(
                    reference.locator, context, purpose, principal
                )
            lease = SecretLease(value)
        except Exception as exc:
            self._record('secret.resolve_failed', reference_id, context, {
                'error_type': type(exc).__name__, 'purpose': purpose,
            })
            raise SecretAccessError('secret resolution failed') from None
        self._record('secret.access_granted', reference_id, context, {
            'principal_digest': hashlib.sha256(
                principal.encode('utf-8')
            ).hexdigest(),
            'purpose': purpose,
        })
        return lease

    @staticmethod
    def _denial(reference, context, purpose, expected_kind=None):
        if reference.endpoint_id != context.endpoint_id:
            return 'endpoint_mismatch'
        if reference.endpoint_mode != context.mode:
            return 'mode_mismatch'
        if purpose not in reference.allowed_purposes:
            return 'purpose_not_granted'
        if expected_kind is not None and (
            reference.secret_kind != expected_kind
        ):
            return 'secret_kind_mismatch'
        if reference.required_permission not in context.effective_permissions:
            return 'permission_not_granted'
        expected_mode = {
            'legacy_engine_auth': 'legacy_native',
            'scratchbird_native_auth': 'scratchbird_native',
        }[reference.authority_scope]
        if context.mode != expected_mode:
            return 'authority_scope_mismatch'
        return None

    def snapshot(self):
        """Return secret-free operational metadata."""
        with self._lock:
            return tuple(
                {
                    'reference_id': reference.reference_id,
                    'endpoint_id': reference.endpoint_id,
                    'endpoint_mode': reference.endpoint_mode,
                    'secret_kind': reference.secret_kind,
                    'storage_kind': reference.storage_kind,
                    'resolver_id': reference.resolver_id,
                    'locator_digest': hashlib.sha256(
                        reference.locator.encode('utf-8')
                    ).hexdigest(),
                    'allowed_purposes': sorted(
                        reference.allowed_purposes
                    ),
                    'required_permission': reference.required_permission,
                    'authority_scope': reference.authority_scope,
                }
                for reference in self._references.values()
            )

    def audit_events(self):
        with self._lock:
            return tuple(redact(item) for item in self._audit)

    def _record(self, event_kind, reference_id, context, payload):
        with self._lock:
            self._audit.append({
                'event_kind': event_kind,
                'reference_id': reference_id,
                'endpoint_id': getattr(context, 'endpoint_id', None),
                'endpoint_mode': getattr(context, 'mode', None),
                'payload': redact(payload),
            })
