##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Strict common mechanics for provider-owned native distributed clients."""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from typing import Any, Mapping

from pgadmin.cdeadmin.sdk import PilotProviderError


class NativeDistributedError(PilotProviderError):
    """A native distributed adapter operation failed safely."""


@dataclass
class NativeResult:
    kind: str
    records_field: str
    records: list[Any]
    schema: dict[str, Any]
    native: dict[str, Any]
    cancelled: bool = False


class NativeDistributedClient:
    """Bound one engine backend to the common provider contract.

    The backend owns transaction, retry, consistency, and finality behavior.
    This adapter forwards requests and reports observations only.
    """

    MAX_SOURCE_BYTES = 2 * 1024 * 1024
    MAX_RECORDS = 10000

    def __init__(self, profile, backend, supported_operations):
        self.profile = profile
        self.backend = backend
        self.supported_operations = {
            str(kind): frozenset(operations)
            for kind, operations in supported_operations.items()
        }
        self._sessions = []
        self._results = []

    def runtime_identity(self, request, handle=None):
        value = self.backend.runtime_identity(request, handle)
        if not isinstance(value, Mapping):
            raise NativeDistributedError('runtime identity is invalid')
        return copy.deepcopy(dict(value))

    def list_resources(self, request):
        values = self.backend.list_resources(request)
        if not isinstance(values, list) or len(values) > self.MAX_RECORDS:
            raise NativeDistributedError('resource result exceeds bounds')
        return copy.deepcopy(values)

    def inspect_resource(self, request):
        identifier = request.get('resource_id')
        for item in self.list_resources(request):
            if item.get('resource_id') == identifier:
                return item
        raise NativeDistributedError('native resource is unavailable')

    def open_session(self, request):
        handle = self.backend.open_session(request)
        self._sessions.append(handle)
        return handle

    def describe_transaction(self, handle):
        value = self.backend.describe_transaction(handle)
        if not isinstance(value, Mapping):
            value = {'native_observation': value}
        return {
            **copy.deepcopy(dict(value)),
            'driver_observation_only': True,
            'finality_interpreted_by_common_code': False,
        }

    def execute(self, handle, request):
        source = request.get('source')
        if not isinstance(source, str) or not source.strip():
            raise NativeDistributedError('native command source is required')
        if len(source.encode('utf-8')) > self.MAX_SOURCE_BYTES:
            raise NativeDistributedError('native command exceeds size limit')
        try:
            command = json.loads(source)
        except json.JSONDecodeError:
            command = source
        result = self.backend.execute(
            handle, command, copy.deepcopy(request.get('parameters') or {})
        )
        if not isinstance(result, NativeResult):
            raise NativeDistributedError('native result is invalid')
        if len(result.records) > self.MAX_RECORDS:
            raise NativeDistributedError('native result exceeds record limit')
        self._results.append(result)
        return result

    def describe_result(self, token):
        if token not in self._results:
            raise NativeDistributedError('native result token is invalid')
        return {
            'result_kind': token.kind,
            'schema': copy.deepcopy(token.schema),
            'payload': {
                token.records_field: copy.deepcopy(token.records),
                'native': copy.deepcopy(token.native),
                'cancelled': token.cancelled,
            },
            'stream_reference': None,
            'complete': True,
        }

    def cancel(self, token):
        if token not in self._results:
            raise NativeDistributedError('native result token is invalid')
        token.cancelled = bool(self.backend.cancel(token))
        return token.cancelled

    def describe_security(self, request):
        value = self.backend.describe_security(request)
        if not isinstance(value, Mapping):
            raise NativeDistributedError('security descriptor is invalid')
        return copy.deepcopy(dict(value))

    def supports_admin_operation(self, kind, operation):
        return operation in self.supported_operations.get(kind, ())

    def visual_admin_catalog(self, catalog):
        value = copy.deepcopy(dict(catalog))
        callback = getattr(self.backend, 'visual_admin_catalog', None)
        if callable(callback):
            native = callback(value)
            if not isinstance(native, Mapping):
                raise NativeDistributedError(
                    'native visual administration catalog is invalid'
                )
            value = copy.deepcopy(dict(native))
        value['native_distributed_contract'] = (
            'cdeadmin.native-distributed-admin.v1'
        )
        value['common_code_generates_native_commands'] = False
        return value

    def validate_admin_operation(self, request):
        return self.backend.validate_admin_operation(request)

    def plan_admin_operation(self, request):
        return self.backend.plan_admin_operation(request)

    def apply_admin_operation(self, request):
        value = self.backend.apply_admin_operation(request)
        if not isinstance(value, Mapping):
            raise NativeDistributedError('native admin result is invalid')
        return {
            **copy.deepcopy(dict(value)),
            'transaction_finality_interpreted_by_common_code': False,
        }

    def read_admin_rows(self, request):
        return self.backend.read_admin_rows(request)

    def inspect_admin_operation(self, request):
        callback = getattr(self.backend, 'inspect_admin_operation', None)
        if not callable(callback):
            raise NativeDistributedError(
                'native provider operation observation is unavailable'
            )
        value = callback(request)
        if not isinstance(value, Mapping):
            raise NativeDistributedError(
                'native provider operation observation is invalid'
            )
        return copy.deepcopy(dict(value))

    def cancel_admin_operation(self, request):
        callback = getattr(self.backend, 'cancel_admin_operation', None)
        if not callable(callback):
            raise NativeDistributedError(
                'native provider operation cancellation is unavailable'
            )
        value = callback(request)
        if not isinstance(value, Mapping):
            raise NativeDistributedError(
                'native provider cancellation response is invalid'
            )
        return copy.deepcopy(dict(value))

    def validate_admin_post_state(self, request):
        callback = getattr(self.backend, 'validate_admin_post_state', None)
        if not callable(callback):
            return {
                'confirmed': False,
                'reason': 'provider_post_state_validator_unavailable',
            }
        value = callback(request)
        if not isinstance(value, Mapping) or not isinstance(
            value.get('confirmed'), bool
        ):
            raise NativeDistributedError(
                'native provider post-state result is invalid'
            )
        return copy.deepcopy(dict(value))

    def complete(self, request):
        value = self.backend.complete(request)
        return copy.deepcopy(value if isinstance(value, list) else [])

    def close(self):
        self.backend.close()
        self._sessions.clear()
        self._results.clear()
