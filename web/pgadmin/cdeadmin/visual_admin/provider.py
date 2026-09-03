##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Fail-closed provider authority for visual administration workflows."""

from __future__ import annotations

import copy
import hashlib
import json
import re
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

from .catalog import catalog_for_engine
from .experience import enrich_engine_experience


class VisualAdminError(RuntimeError):
    """A visual administration request cannot be handled safely."""


class VisualAdminValidationError(VisualAdminError):
    """A draft does not satisfy its provider-declared form."""


class VisualAdminAccessError(VisualAdminError):
    """An operation is unavailable or is not admitted for this endpoint."""


class VisualAdminExecutionError(VisualAdminError):
    """A provider operation action did not return a usable response."""

    def __init__(self, message, operation):
        super().__init__(message)
        self.operation = copy.deepcopy(operation)


@dataclass(frozen=True)
class _StoredPlan:
    digest: str
    provider_payload: object
    presentation: dict[str, Any]


@dataclass
class _StoredAdminOperation:
    public: dict[str, Any]
    provider_payload: object
    provider_result: object
    plan: dict[str, Any]


def _mapping(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise VisualAdminValidationError(f'{label} must be an object')
    return copy.deepcopy(dict(value))


def _required_string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise VisualAdminValidationError(f'{label} must not be empty')
    return value.strip()


class ProviderVisualAdministration:
    """Bind one declarative engine catalog to one provider/client instance.

    Catalogs are reusable experience-pack inputs. Validation, planning and
    execution remain provider-owned. Common code never generates native
    source, infers transaction outcome or treats a blocked plan as executable.
    """

    def __init__(
        self, context, permissions, engine_id, profile_version, client=None,
        planner=None, executor=None,
    ):
        self.context = context
        self.permissions = permissions
        self.engine_id = engine_id
        self.profile_version = profile_version
        self.client = client
        self._planner = planner
        self._executor = executor
        self._plans: dict[str, _StoredPlan] = {}
        self._admin_operations: dict[str, _StoredAdminOperation] = {}
        self._lock = threading.RLock()

    def descriptor(self) -> dict[str, Any]:
        catalog = self._catalog()
        planner = self._planner_callback()
        executor = self._executor_callback()
        runtime_verified = (
            self.context.runtime_verification_state == 'verified'
        )
        for resource in catalog['objects']:
            for operation in resource['operations']:
                mutation = operation['mutation_class']
                native_supported = self._operation_supported(
                    resource['resource_kind'], operation['operation_id']
                )
                operation['form_available'] = True
                operation['native_supported'] = native_supported
                operation['planning_available'] = (
                    planner is not None and native_supported
                )
                operation['execution_available'] = (
                    planner is not None and executor is not None and
                    native_supported and runtime_verified and
                    self._allows(mutation) and
                    all(self._allows_permission(permission)
                        for permission in operation.get(
                            'required_permissions', []
                    ))
                )
                blockers = []
                if planner is None:
                    blockers.append('provider_native_planner_unavailable')
                if executor is None:
                    blockers.append('target_adapter_executor_unavailable')
                if not native_supported:
                    blockers.append('provider_operation_unavailable')
                if not runtime_verified:
                    blockers.append('runtime_identity_not_verified')
                if not self._allows(mutation):
                    blockers.append('permission_not_granted')
                for permission in operation.get(
                    'required_permissions', []
                ):
                    if not self._allows_permission(permission):
                        blockers.append(
                            f'permission_{permission}_not_granted'
                        )
                operation['blockers'] = blockers
        catalog['profile_version'] = self.profile_version
        catalog['endpoint_mode'] = self.context.mode
        catalog['runtime_verification_state'] = (
            self.context.runtime_verification_state
        )
        catalog['target_runtime_family'] = (
            self.context.verified_runtime_family or
            self.context.declared_runtime_family
        )
        catalog['provider_driven'] = True
        catalog['common_code_generates_native_commands'] = False
        return catalog

    def validate(
        self, request: Mapping[str, Any], retain_sensitive: bool = False,
    ) -> dict[str, Any]:
        payload = _mapping(request, 'visual administration request')
        resource, operation = self._operation(payload)
        target = payload.get('target_resource')
        if operation['target_required'] and not isinstance(target, Mapping):
            raise VisualAdminValidationError(
                'the selected operation requires a target resource'
            )
        draft = _mapping(payload.get('draft', {}), 'draft')
        fields = operation['form']['fields']
        known = {field['field_id'] for field in fields}
        unknown = sorted(set(draft).difference(known))
        errors = []
        if unknown:
            errors.append({
                'field_id': None,
                'code': 'unknown_fields',
                'message': 'Unknown form fields: ' + ', '.join(unknown),
            })
        admitted = {}
        for field in fields:
            if not self._field_active(field, draft):
                continue
            field_id = field['field_id']
            value = draft.get(field_id, field.get('default'))
            if field['required'] and (
                value is None or
                (isinstance(value, str) and not value.strip()) or
                (isinstance(value, (list, tuple, dict)) and not value)
            ):
                errors.append({
                    'field_id': field_id,
                    'code': 'required',
                    'message': f"{field['label']} is required.",
                })
                continue
            if value is None:
                continue
            normalized, error = self._validate_field(field, value)
            if error:
                errors.append(error)
            else:
                admitted[field_id] = normalized
        callback = self._validation_callback()
        if not errors and callback is not None:
            provider_request = {
                'engine_id': self.engine_id,
                'profile_version': self.profile_version,
                'resource_kind': resource['resource_kind'],
                'operation_id': operation['operation_id'],
                'target_resource': copy.deepcopy(target),
                'draft': copy.deepcopy(admitted),
            }
            route = payload.get('_provider_route')
            if route is not None:
                provider_request['_provider_route'] = _mapping(
                    route, 'provider route'
                )
            native = callback(provider_request)
            if native is not None:
                native_value = _mapping(native, 'provider validation result')
                native_errors = native_value.get('errors', [])
                if not isinstance(native_errors, list):
                    raise VisualAdminValidationError(
                        'provider validation errors must be an array'
                    )
                errors.extend(copy.deepcopy(native_errors))
        return {
            'schema': 'cdeadmin.visual-admin.validation.v1',
            'valid': not errors,
            'engine_id': self.engine_id,
            'resource_kind': resource['resource_kind'],
            'operation_id': operation['operation_id'],
            'draft': (
                admitted if retain_sensitive
                else self._redact_sensitive(fields, admitted)
            ),
            'errors': errors,
        }

    def plan(self, request: Mapping[str, Any]) -> dict[str, Any]:
        payload = _mapping(request, 'visual administration request')
        resource, operation = self._operation(payload)
        self._require_mutation(operation['mutation_class'])
        for permission in operation.get('required_permissions', []):
            self.permissions.require(permission, 'endpoint')
        if not self._operation_supported(
            resource['resource_kind'], operation['operation_id']
        ):
            raise VisualAdminAccessError(
                'the target provider does not support this operation'
            )
        validation = self.validate(payload, retain_sensitive=True)
        if not validation['valid']:
            raise VisualAdminValidationError(
                'visual administration draft is invalid'
            )
        planner = self._planner_callback()
        plan_id = str(uuid.uuid4())
        admitted_draft = copy.deepcopy(validation['draft'])
        request_value = {
            'plan_id': plan_id,
            'engine_id': self.engine_id,
            'profile_version': self.profile_version,
            'endpoint_id': self.context.endpoint_id,
            'endpoint_mode': self.context.mode,
            'resource_kind': resource['resource_kind'],
            'operation_id': operation['operation_id'],
            'mutation_class': operation['mutation_class'],
            'confirmation_required': operation['confirmation_required'],
            'required_permissions': copy.deepcopy(
                operation.get('required_permissions', [])
            ),
            'target_resource': copy.deepcopy(payload.get('target_resource')),
            'draft': self._redact_sensitive(
                operation['form']['fields'], admitted_draft
            ),
        }
        if planner is None:
            return {
                'schema': 'cdeadmin.visual-admin.plan.v1',
                **request_value,
                'state': 'blocked',
                'execution_available': False,
                'blockers': ['provider_native_planner_unavailable'],
                'warnings': [],
                'command_preview': None,
                'provider_receipt': None,
            }
        provider_request = copy.deepcopy(request_value)
        provider_request['draft'] = admitted_draft
        route = payload.get('_provider_route')
        if route is not None:
            provider_request['_provider_route'] = _mapping(
                route, 'provider route'
            )
        native = _mapping(planner(provider_request), 'native plan')
        command_preview = native.get('command_preview')
        if command_preview is not None and not isinstance(
            command_preview, (str, Mapping, list)
        ):
            raise VisualAdminValidationError(
                'provider command preview has an invalid shape'
            )
        provider_payload = native.get('provider_payload')
        presentation = {
            'schema': 'cdeadmin.visual-admin.plan.v1',
            **request_value,
            'state': 'ready',
            'execution_available': self._executor_callback() is not None,
            'blockers': [],
            'warnings': copy.deepcopy(native.get('warnings', [])),
            'impact': copy.deepcopy(native.get('impact')),
            'command_preview': copy.deepcopy(command_preview),
            'provider_receipt': copy.deepcopy(native.get('receipt')),
            'control_plane': bool(operation.get('control_plane', False)),
            'long_running': bool(operation.get('long_running', False)),
            'cancellable': bool(operation.get('cancellable', False)),
            'post_state_required': bool(
                operation.get('post_state_required', False)
            ),
            'automatic_mutation_retry': False,
        }
        digest = self._digest(presentation)
        with self._lock:
            self._plans[plan_id] = _StoredPlan(
                digest, copy.deepcopy(provider_payload),
                copy.deepcopy(presentation),
            )
        presentation['plan_digest'] = digest
        return presentation

    def apply(self, request: Mapping[str, Any]) -> dict[str, Any]:
        payload = _mapping(request, 'visual administration apply request')
        plan_id = _required_string(payload.get('plan_id'), 'plan_id')
        digest = _required_string(payload.get('plan_digest'), 'plan_digest')
        confirmed = bool(payload.get('confirmed', False))
        with self._lock:
            stored = self._plans.pop(plan_id, None)
        if stored is None or stored.digest != digest:
            raise VisualAdminAccessError(
                'visual administration plan is absent, stale or changed'
            )
        presentation = stored.presentation
        mutation_class = presentation['mutation_class']
        self._require_mutation(mutation_class)
        for permission in presentation.get('required_permissions', []):
            self.permissions.require(permission, 'endpoint')
        if presentation.get('confirmation_required') and not confirmed:
            raise VisualAdminAccessError(
                'visual administration operation requires confirmation'
            )
        if self.context.runtime_verification_state != 'verified':
            raise VisualAdminAccessError(
                'visual administration requires verified runtime identity'
            )
        executor = self._executor_callback()
        if executor is None:
            raise VisualAdminAccessError(
                'target adapter has no visual administration executor'
            )
        operation_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        public_operation = {
            'schema': 'cdeadmin.visual-admin.operation.v1',
            'operation_id': operation_id,
            'plan_id': plan_id,
            'engine_id': self.engine_id,
            'resource_kind': presentation['resource_kind'],
            'operation_kind': presentation['operation_id'],
            'target_resource_id': (
                (presentation.get('target_resource') or {}).get(
                    'resource_id'
                )
            ),
            'stage': 'dispatch_started',
            'provider_result': None,
            'impact': copy.deepcopy(presentation.get('impact')),
            'long_running': presentation.get('long_running', False),
            'cancellable': presentation.get('cancellable', False),
            'cancel_request_dispatched': False,
            'cancel_response': None,
            'unknown_outcome': False,
            'post_state_required': presentation.get(
                'post_state_required', False
            ),
            'post_state': None,
            'provider_finality_authority': True,
            'automatic_mutation_retry': False,
            'created_at': now,
            'updated_at': now,
            'last_event_sequence': 1,
            'events': [{
                'sequence': 1,
                'event_kind': 'dispatch_started',
                'occurred_at': now,
                'provider_finality_inferred': False,
            }],
        }
        with self._lock:
            if len(self._admin_operations) >= 1024:
                self._admin_operations.pop(next(iter(
                    self._admin_operations
                )))
            self._admin_operations[operation_id] = _StoredAdminOperation(
                public=copy.deepcopy(public_operation),
                provider_payload=copy.deepcopy(stored.provider_payload),
                provider_result=None,
                plan=copy.deepcopy(presentation),
            )
            operation = self._admin_operations[operation_id]
        try:
            result = executor({
                'plan': copy.deepcopy(presentation),
                'provider_payload': copy.deepcopy(stored.provider_payload),
            })
        except Exception as exc:
            with self._lock:
                operation.public['unknown_outcome'] = True
                operation.public['stage'] = 'provider_response_unavailable'
                self._record_admin_event(
                    operation.public, 'provider_response_unavailable', {
                        'error_type': type(exc).__name__,
                        'automatic_retry': False,
                    },
                )
                failed = copy.deepcopy(operation.public)
            raise VisualAdminExecutionError(
                'provider mutation response is unavailable; the outcome is '
                'unknown and the mutation will not be retried',
                failed,
            ) from None
        provider_result = copy.deepcopy(result)
        with self._lock:
            operation.provider_result = provider_result
            operation.public['provider_result'] = provider_result
            operation.public['stage'] = 'provider_response_recorded'
            self._record_admin_event(
                operation.public, 'provider_response_recorded'
            )
            public_operation = copy.deepcopy(operation.public)
        return {
            'schema': 'cdeadmin.visual-admin.result.v1',
            'plan_id': plan_id,
            'engine_id': self.engine_id,
            'resource_kind': presentation['resource_kind'],
            'operation_id': presentation['operation_id'],
            'provider_result': copy.deepcopy(result),
            'transaction_finality_interpreted_by_common_code': False,
            'provider_finality_authority': True,
            'automatic_mutation_retry': False,
            'impact': copy.deepcopy(presentation.get('impact')),
            'long_running': presentation.get('long_running', False),
            'cancellable': presentation.get('cancellable', False),
            'post_state_required': presentation.get(
                'post_state_required', False
            ),
            'control_operation': copy.deepcopy(public_operation),
        }

    def list_operations(self):
        """List bounded public operation records for this endpoint."""
        with self._lock:
            return {
                'schema': 'cdeadmin.visual-admin.operation-list.v1',
                'items': [
                    copy.deepcopy(item.public)
                    for item in reversed(self._admin_operations.values())
                ],
            }

    def get_operation(self, request):
        payload = _mapping(request, 'visual administration operation request')
        operation_id = _required_string(
            payload.get('operation_id'), 'operation_id'
        )
        with self._lock:
            stored = self._admin_operations.get(operation_id)
            if stored is None:
                raise VisualAdminAccessError(
                    'visual administration operation is unavailable'
                )
            return copy.deepcopy(stored.public)

    def refresh_operation(self, request):
        """Ask the provider for a new observation without inferring state."""
        stored = self._stored_operation(request)
        callback = self._callback('inspect_admin_operation')
        if callback is None:
            with self._lock:
                stored.public['observation_blocker'] = (
                    'provider_observation_unavailable'
                )
                self._record_admin_event(
                    stored.public, 'provider_observation_unavailable'
                )
                return copy.deepcopy(stored.public)
        try:
            observed = _mapping(callback(self._private_operation_request(
                stored
            )), 'provider operation observation')
        except Exception as exc:
            with self._lock:
                stored.public['unknown_outcome'] = True
                stored.public['stage'] = 'observation_response_unavailable'
                self._record_admin_event(
                    stored.public, 'observation_response_unavailable',
                    {
                        'error_type': type(exc).__name__,
                        'automatic_retry': False,
                    },
                )
                failed = copy.deepcopy(stored.public)
            raise VisualAdminExecutionError(
                'provider operation observation is unavailable; no action '
                'was retried', failed,
            ) from None
        with self._lock:
            stored.public['provider_observation'] = copy.deepcopy(observed)
            stored.public['stage'] = 'provider_observation_recorded'
            self._record_admin_event(
                stored.public, 'provider_observation_recorded'
            )
            return copy.deepcopy(stored.public)

    def cancel_operation(self, request):
        """Dispatch cancellation once; a response is not operation finality."""
        stored = self._stored_operation(request)
        with self._lock:
            if not stored.public['cancellable']:
                raise VisualAdminAccessError(
                    'provider operation is not declared cancellable'
                )
            if stored.public['cancel_request_dispatched']:
                return copy.deepcopy(stored.public)
            stored.public['cancel_request_dispatched'] = True
            stored.public['stage'] = 'cancel_requested'
            self._record_admin_event(
                stored.public, 'cancel_request_dispatched',
                {'automatic_retry': False},
            )
        callback = self._callback('cancel_admin_operation')
        if callback is None:
            with self._lock:
                stored.public['unknown_outcome'] = True
                stored.public['stage'] = 'cancel_dispatch_unavailable'
                self._record_admin_event(
                    stored.public, 'cancel_dispatch_unavailable',
                    {'automatic_retry': False},
                )
                return copy.deepcopy(stored.public)
        try:
            response = _mapping(callback(self._private_operation_request(
                stored
            )), 'provider cancellation response')
        except Exception as exc:
            with self._lock:
                stored.public['unknown_outcome'] = True
                stored.public['stage'] = 'cancel_response_unavailable'
                self._record_admin_event(
                    stored.public, 'cancel_response_unavailable',
                    {
                        'error_type': type(exc).__name__,
                        'automatic_retry': False,
                    },
                )
                failed = copy.deepcopy(stored.public)
            raise VisualAdminExecutionError(
                'provider cancellation response is unavailable; the cancel '
                'request will not be retried', failed,
            ) from None
        with self._lock:
            stored.public['cancel_response'] = copy.deepcopy(response)
            stored.public['stage'] = 'cancel_response_recorded'
            self._record_admin_event(
                stored.public, 'cancel_response_recorded'
            )
            return copy.deepcopy(stored.public)

    def validate_operation_post_state(self, request):
        """Record an independent provider post-state observation."""
        stored = self._stored_operation(request)
        callback = self._callback('validate_admin_post_state')
        if callback is None:
            observed = {
                'confirmed': False,
                'reason': 'provider_post_state_validator_unavailable',
            }
        else:
            try:
                observed = _mapping(callback(
                    self._private_operation_request(stored)
                ), 'provider post-state observation')
            except Exception as exc:
                with self._lock:
                    stored.public['stage'] = (
                        'post_state_response_unavailable'
                    )
                    self._record_admin_event(
                        stored.public, 'post_state_response_unavailable', {
                            'error_type': type(exc).__name__,
                            'automatic_retry': False,
                        },
                    )
                    failed = copy.deepcopy(stored.public)
                raise VisualAdminExecutionError(
                    'provider post-state observation is unavailable; no '
                    'action was retried', failed,
                ) from None
            if not isinstance(observed.get('confirmed'), bool):
                raise VisualAdminValidationError(
                    'provider post-state confirmation must be boolean'
                )
        with self._lock:
            stored.public['post_state'] = copy.deepcopy(observed)
            stored.public['stage'] = (
                'post_state_confirmed' if observed['confirmed']
                else 'post_state_unconfirmed'
            )
            self._record_admin_event(
                stored.public, stored.public['stage'], {
                    'confirmed': observed['confirmed'],
                }
            )
            return copy.deepcopy(stored.public)

    def _stored_operation(self, request):
        payload = _mapping(request, 'visual administration operation request')
        operation_id = _required_string(
            payload.get('operation_id'), 'operation_id'
        )
        with self._lock:
            stored = self._admin_operations.get(operation_id)
        if stored is None:
            raise VisualAdminAccessError(
                'visual administration operation is unavailable'
            )
        return stored

    @staticmethod
    def _private_operation_request(stored):
        return {
            'plan': copy.deepcopy(stored.plan),
            'provider_payload': copy.deepcopy(stored.provider_payload),
            'provider_result': copy.deepcopy(stored.provider_result),
        }

    @staticmethod
    def _record_admin_event(operation, event_kind, detail=None):
        """Append bounded orchestration evidence without a finality claim."""
        sequence = int(operation.get('last_event_sequence', 0)) + 1
        now = datetime.now(timezone.utc).isoformat()
        event = {
            'sequence': sequence,
            'event_kind': event_kind,
            'occurred_at': now,
            'provider_finality_inferred': False,
        }
        if detail:
            event['detail'] = copy.deepcopy(detail)
        events = operation.setdefault('events', [])
        events.append(event)
        if len(events) > 200:
            del events[:-200]
        operation['last_event_sequence'] = sequence
        operation['updated_at'] = now

    def read_rows(self, request: Mapping[str, Any]) -> dict[str, Any]:
        """Return a provider-issued editable data page for one container."""
        payload = _mapping(request, 'visual administration row request')
        self._require_mutation('read')
        target = _mapping(payload.get('target_resource'), 'target resource')
        resource_kind = _required_string(
            target.get('resource_kind'), 'target resource kind'
        )
        callback = self._callback('read_admin_rows')
        if callback is None or not self._operation_supported(
            resource_kind, 'insert'
        ):
            raise VisualAdminAccessError(
                'the target provider has no editable row-page contract'
            )
        route = _mapping(payload.get('_provider_route'), 'provider route')
        result = callback({
            '_provider_route': route,
            'target_resource': target,
            'limit': payload.get('limit', 200),
            'continuation': payload.get('continuation'),
            'filter': copy.deepcopy(payload.get('filter', {})),
            'projection': copy.deepcopy(payload.get('projection')),
            'sort': copy.deepcopy(payload.get('sort')),
        })
        return _mapping(result, 'provider row page')

    def cancel_rows(self, request: Mapping[str, Any]) -> dict[str, Any]:
        """Cancel one provider-owned editable-data cursor."""
        payload = _mapping(request, 'visual administration row cancellation')
        self._require_mutation('read')
        callback = self._callback('cancel_admin_cursor')
        if callback is None:
            raise VisualAdminAccessError(
                'the target provider has no row-cursor cancellation contract'
            )
        route = _mapping(payload.get('_provider_route'), 'provider route')
        return _mapping(callback({
            '_provider_route': route,
            'continuation': _required_string(
                payload.get('continuation'), 'continuation'
            ),
        }), 'provider row cancellation')

    def _operation(self, payload):
        resource_kind = _required_string(
            payload.get('resource_kind'), 'resource_kind'
        )
        operation_id = _required_string(
            payload.get('operation_id'), 'operation_id'
        )
        catalog = self._catalog()
        resource = next((
            item for item in catalog['objects']
            if item['resource_kind'] == resource_kind
        ), None)
        if resource is None:
            raise VisualAdminAccessError(
                'resource kind is not admitted by this engine catalog'
            )
        operation = next((
            item for item in resource['operations']
            if item['operation_id'] == operation_id
        ), None)
        if operation is None:
            raise VisualAdminAccessError(
                'operation is not admitted for this resource kind'
            )
        return resource, operation

    @staticmethod
    def _field_active(field, draft):
        condition = field.get('visible_when')
        if condition is None:
            return True
        if not isinstance(condition, Mapping):
            raise VisualAdminValidationError(
                'field visibility condition is invalid'
            )
        controller = condition.get('field_id')
        if not isinstance(controller, str) or not controller:
            raise VisualAdminValidationError(
                'field visibility controller is invalid'
            )
        actual = draft.get(controller)
        if 'equals' in condition:
            return actual == condition['equals']
        if 'in' in condition and isinstance(condition['in'], list):
            return actual in condition['in']
        raise VisualAdminValidationError(
            'field visibility comparison is invalid'
        )

    @staticmethod
    def _validate_field(field, value):
        field_id = field['field_id']
        label = field['label']
        control = field['control']
        if control in {
            'text', 'multiline', 'code', 'secret-reference', 'password',
        }:
            if not isinstance(value, str):
                return None, {
                    'field_id': field_id, 'code': 'type',
                    'message': f'{label} must be text.',
                }
            maximum = field.get('max_length', 1024 * 1024)
            if len(value) > maximum:
                return None, {
                    'field_id': field_id, 'code': 'length',
                    'message': f'{label} exceeds its maximum length.',
                }
            pattern = field.get('pattern')
            if pattern is not None and (
                not isinstance(pattern, str) or
                re.fullmatch(pattern, value) is None
            ):
                return None, {
                    'field_id': field_id, 'code': 'pattern',
                    'message': f'{label} has an invalid format.',
                }
            return value, None
        if control == 'boolean':
            if not isinstance(value, bool):
                return None, {
                    'field_id': field_id, 'code': 'type',
                    'message': f'{label} must be true or false.',
                }
            return value, None
        if control == 'number':
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                return None, {
                    'field_id': field_id, 'code': 'type',
                    'message': f'{label} must be numeric.',
                }
            minimum = field.get('minimum')
            maximum = field.get('maximum')
            if minimum is not None and value < minimum:
                return None, {
                    'field_id': field_id, 'code': 'minimum',
                    'message': f'{label} is below its minimum.',
                }
            if maximum is not None and value > maximum:
                return None, {
                    'field_id': field_id, 'code': 'maximum',
                    'message': f'{label} exceeds its maximum.',
                }
            return value, None
        if control == 'select':
            choices = {item['value'] for item in field['options']}
            if value not in choices:
                return None, {
                    'field_id': field_id, 'code': 'choice',
                    'message': f'{label} has an unknown value.',
                }
            return value, None
        if control == 'multiselect':
            if not isinstance(value, list):
                return None, {
                    'field_id': field_id, 'code': 'type',
                    'message': f'{label} must be a list.',
                }
            choices = {item['value'] for item in field['options']}
            if len(value) != len(set(value)) or any(
                    item not in choices for item in value):
                return None, {
                    'field_id': field_id, 'code': 'choice',
                    'message': (
                        f'{label} contains an unknown or duplicate value.'
                    ),
                }
            return copy.deepcopy(value), None
        if control == 'json':
            if isinstance(value, str):
                try:
                    value = json.loads(value)
                except json.JSONDecodeError:
                    return None, {
                        'field_id': field_id, 'code': 'json',
                        'message': f'{label} must contain valid JSON.',
                    }
            if isinstance(value, (Mapping, list)):
                expected = field.get('json_type')
                if expected == 'object' and not isinstance(value, Mapping):
                    return None, {
                        'field_id': field_id, 'code': 'json_type',
                        'message': f'{label} must be a JSON object.',
                    }
                if expected == 'array' and not isinstance(value, list):
                    return None, {
                        'field_id': field_id, 'code': 'json_type',
                        'message': f'{label} must be a JSON array.',
                    }
                return copy.deepcopy(value), None
            return None, {
                'field_id': field_id, 'code': 'type',
                'message': f'{label} must be a JSON object or array.',
            }
        raise VisualAdminValidationError('form control is unsupported')

    @staticmethod
    def _redact_sensitive(fields, admitted):
        sensitive = {
            field['field_id'] for field in fields
            if field.get('sensitive') or field.get('control') == 'password'
        }
        return {
            key: '<redacted>' if key in sensitive else copy.deepcopy(value)
            for key, value in admitted.items()
        }

    def _allows(self, mutation_class):
        permission = {
            'none': None,
            'read': 'data_read',
            'write': 'data_write',
            'admin': 'administer',
            'destructive': 'administer',
        }[mutation_class]
        if permission is None:
            return True
        allows = getattr(self.permissions, 'allows', None)
        if callable(allows):
            return bool(allows(permission, 'resource'))
        effective = getattr(self.context, 'effective_permissions', ())
        return permission in effective

    def _allows_permission(self, permission):
        allows = getattr(self.permissions, 'allows', None)
        if callable(allows):
            return bool(allows(permission, 'endpoint'))
        return permission in getattr(
            self.context, 'effective_permissions', ()
        )

    def _require_mutation(self, mutation_class):
        permission = {
            'none': None,
            'read': 'data_read',
            'write': 'data_write',
            'admin': 'administer',
            'destructive': 'administer',
        }[mutation_class]
        if permission is not None:
            self.permissions.require(permission, 'resource')

    def _validation_callback(self):
        return self._callback('validate_admin_operation')

    def _planner_callback(self):
        return self._planner or self._callback('plan_admin_operation')

    def _executor_callback(self):
        return self._executor or self._callback('apply_admin_operation')

    def _callback(self, name):
        callback = getattr(self.client, name, None)
        return callback if callable(callback) else None

    def _catalog(self):
        catalog = catalog_for_engine(self.engine_id)
        callback = self._callback('visual_admin_catalog')
        if callback is None:
            return catalog
        adapted = callback(copy.deepcopy(catalog))
        if not isinstance(adapted, Mapping):
            raise VisualAdminValidationError(
                'provider visual administration catalog is invalid'
            )
        return enrich_engine_experience(copy.deepcopy(dict(adapted)))

    def _operation_supported(self, resource_kind, operation_id):
        callback = self._callback('supports_admin_operation')
        if callback is None:
            return True
        return bool(callback(resource_kind, operation_id))

    @staticmethod
    def _digest(presentation):
        serialized = json.dumps(
            presentation, separators=(',', ':'), sort_keys=True,
            ensure_ascii=True,
        ).encode('utf-8')
        return hashlib.sha256(serialized).hexdigest()
