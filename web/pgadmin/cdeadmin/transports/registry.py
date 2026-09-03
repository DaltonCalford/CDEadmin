##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Endpoint-isolated registration for opaque protocol client ports."""

from __future__ import annotations

import json
import threading
from collections import Counter, deque
from pathlib import Path
from typing import Mapping

from pgadmin.cdeadmin.security.redaction import redact

from .models import (
    ProtocolSelection,
    TransportError,
    TransportFault,
    TransportIsolationError,
    TransportRequest,
    TransportResponse,
    TransportSelectionError,
    TransportUnavailableError,
)


SELECTION_PATH = Path(__file__).with_name(
    'protocol_client_selections.json'
)


class EndpointFaultStore:
    """Bounded fault evidence partitioned by complete isolation identity."""

    def __init__(self, max_faults_per_endpoint=100):
        if max_faults_per_endpoint < 1:
            raise TransportError('fault retention limit must be positive')
        self._limit = max_faults_per_endpoint
        self._faults = {}
        self._lock = threading.RLock()

    def record(self, isolation_key, fault):
        if not isinstance(fault, TransportFault):
            raise TransportError('transport fault model is required')
        key = tuple(isolation_key)
        with self._lock:
            queue = self._faults.setdefault(
                key, deque(maxlen=self._limit)
            )
            queue.append(fault)

    def snapshot(self, isolation_key):
        with self._lock:
            return tuple(self._faults.get(tuple(isolation_key), ()))

    def counts(self):
        with self._lock:
            return Counter({
                key: len(value) for key, value in self._faults.items()
            })


class ProtocolBoundary:
    """Transport/authentication wrapper around one injected client port.

    The provider supplies already framed bytes and interprets returned bytes.
    This class owns neither query meaning nor transaction finality.
    """

    def __init__(
        self, context, selection, client_port, security_service,
        fault_store=None,
    ):
        if not isinstance(selection, ProtocolSelection):
            raise TransportSelectionError('protocol selection is required')
        exchange = getattr(client_port, 'exchange', None)
        if not callable(exchange):
            raise TransportUnavailableError(
                'client port must implement exchange'
            )
        self.context = context
        self.selection = selection
        self._client_port = client_port
        self._security = security_service
        self._faults = fault_store or EndpointFaultStore()

    def exchange(self, request):
        self._validate_request(request)
        isolation_key = self._security.isolation_key(
            self.context,
            'pool',
            request.principal_id,
            request.credential_reference_id,
            self.context.runtime_identity_generation,
        )
        lease = None
        try:
            if request.credential_reference_id is not None:
                lease = self._security.secrets.acquire(
                    request.credential_reference_id,
                    self.context,
                    request.principal_id,
                    'connect',
                )
            response = self._client_port.exchange(
                request, lease, isolation_key
            )
            if not isinstance(response, TransportResponse):
                raise TransportError(
                    'client port returned an invalid response model'
                )
            if response.endpoint_id != request.endpoint_id or (
                response.operation_id != request.operation_id
            ) or response.protocol_id != request.protocol_id:
                raise TransportIsolationError(
                    'client response crossed its request boundary'
                )
            return response
        except Exception as exc:
            fault = TransportFault(
                diagnostic_code='CDE_TRANSPORT_EXCHANGE_FAILED',
                endpoint_id=request.endpoint_id,
                operation_id=request.operation_id,
                protocol_id=request.protocol_id,
                error_type=type(exc).__name__,
            )
            self._faults.record(self.context.isolation_key, fault)
            raise TransportError(
                'protocol exchange failed for endpoint'
            ) from None
        finally:
            if lease is not None:
                lease.close()

    def faults(self):
        return self._faults.snapshot(self.context.isolation_key)

    def _validate_request(self, request):
        if not isinstance(request, TransportRequest):
            raise TransportError('transport request model is required')
        expected = (
            self.context.endpoint_id,
            self.context.mode,
            self.context.provider_id,
            self.selection.protocol_id,
        )
        observed = (
            request.endpoint_id,
            request.endpoint_mode,
            request.provider_id,
            request.protocol_id,
        )
        if observed != expected:
            raise TransportIsolationError(
                'transport request does not match its endpoint binding'
            )


class ProtocolBoundaryRegistry:
    """Register injected clients without selecting provider semantics."""

    def __init__(self, selections, security_service, fault_store=None):
        self._selections = dict(selections)
        self._security = security_service
        self._faults = fault_store or EndpointFaultStore()
        self._factories = {}
        self._bindings = {}
        self._lock = threading.RLock()

    def register_client(self, protocol_id, factory):
        selection = self._selections.get(protocol_id)
        if selection is None:
            raise TransportSelectionError('protocol is not selected')
        if selection.client_state == 'boundary_only_unselected':
            raise TransportUnavailableError(
                'protocol client has not been selected and approved'
            )
        if not callable(factory):
            raise TransportError('client factory must be callable')
        with self._lock:
            if protocol_id in self._factories:
                raise TransportError('protocol client is already registered')
            self._factories[protocol_id] = factory

    def bind(self, context, protocol_id):
        selection = self._selections.get(protocol_id)
        if selection is None:
            raise TransportSelectionError('protocol is not selected')
        key = context.isolation_key + (protocol_id,)
        with self._lock:
            existing = self._bindings.get(key)
            if existing is not None:
                return existing
            factory = self._factories.get(protocol_id)
            if factory is None:
                raise TransportUnavailableError(
                    'protocol client is unavailable'
                )
            client_port = factory(context, selection)
            boundary = ProtocolBoundary(
                context,
                selection,
                client_port,
                self._security,
                self._faults,
            )
            self._bindings[key] = boundary
            return boundary

    def unload_endpoint(self, context):
        prefix = context.isolation_key
        with self._lock:
            keys = [key for key in self._bindings if key[:-1] == prefix]
            for key in keys:
                boundary = self._bindings.pop(key)
                close = getattr(boundary._client_port, 'close', None)
                if callable(close):
                    close()
            return len(keys)


def load_selection_document(path=SELECTION_PATH):
    try:
        value = json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TransportSelectionError(
            'protocol selection document is unavailable or invalid'
        ) from exc
    if not isinstance(value, Mapping):
        raise TransportSelectionError(
            'protocol selection document must be an object'
        )
    return value


def load_protocol_selections(path=SELECTION_PATH):
    document = load_selection_document(path)
    rows = document.get('protocols')
    if not isinstance(rows, list):
        raise TransportSelectionError('protocol records must be an array')
    result = {}
    for row in rows:
        if not isinstance(row, Mapping):
            raise TransportSelectionError('protocol record must be an object')
        client = row.get('client')
        if not isinstance(client, Mapping):
            raise TransportSelectionError(
                'protocol client record must be an object'
            )
        selection = ProtocolSelection(
            protocol_id=row.get('protocol_id'),
            transport_kind=row.get('transport_kind'),
            framing_authority=row.get('framing_authority'),
            authentication_authority=row.get('authentication_authority'),
            client_state=client.get('state'),
            client_package=client.get('package'),
            client_versions=tuple(client.get('versions', ())),
            security_controls=frozenset(row.get('security_controls', ())),
        )
        if selection.protocol_id in result:
            raise TransportSelectionError('duplicate protocol selection')
        result[selection.protocol_id] = selection
    return result


def safe_selection_summary(path=SELECTION_PATH):
    """Return redacted selection metadata for diagnostics/evidence."""
    document = load_selection_document(path)
    return redact(document)
