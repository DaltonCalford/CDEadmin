##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Operation adapters separating local supervision from remote authority."""

from __future__ import annotations

import copy
from typing import Callable, Mapping

from pgadmin.cdeadmin.contracts.v1.runtime import validate_contract

from .models import (
    AdapterCancellation,
    AdapterObservation,
    AdapterStart,
    OperationBusError,
    OperationStateError,
    PostStateResult,
)


def _result_mapping(value, label):
    if not isinstance(value, Mapping):
        raise OperationBusError(f'{label} must return an object')
    return copy.deepcopy(dict(value))


class LocalProcessAdapter:
    """Adapter for pgAdmin-supervised OS work.

    A local handle and exit code are supervision facts only. This adapter
    deliberately cannot manufacture a provider Operation or remote receipt.
    """

    authority_kind = 'local_process'

    def __init__(
        self,
        launcher: Callable,
        inspector: Callable,
        canceller: Callable,
        validator: Callable | None = None,
        previewer: Callable | None = None,
        post_validator: Callable | None = None,
    ):
        for name, callback in (
            ('launcher', launcher), ('inspector', inspector),
            ('canceller', canceller),
        ):
            if not callable(callback):
                raise OperationBusError(f'local {name} must be callable')
        self._launcher = launcher
        self._inspector = inspector
        self._canceller = canceller
        self._validator = validator
        self._previewer = previewer
        self._post_validator = post_validator

    def validate(self, operation):
        if self._validator is None:
            return {'valid': True, 'authority_kind': self.authority_kind}
        return _result_mapping(
            self._validator(copy.deepcopy(operation)), 'local validator'
        )

    def preview(self, operation):
        if self._previewer is None:
            return {
                'authority_kind': self.authority_kind,
                'operation_kind': operation['request']['operation_kind'],
                'target_resource_id': operation['request'][
                    'target_resource_id'
                ],
            }
        return _result_mapping(
            self._previewer(copy.deepcopy(operation)), 'local previewer'
        )

    def start(self, operation):
        handle = _result_mapping(
            self._launcher(copy.deepcopy(operation)), 'local launcher'
        )
        return AdapterStart(local_process=handle)

    def inspect(self, operation):
        handle = operation.get('local_process')
        if not isinstance(handle, Mapping):
            raise OperationStateError('local process has not been started')
        observed = _result_mapping(
            self._inspector(copy.deepcopy(handle)), 'local inspector'
        )
        return AdapterObservation(local_process=observed)

    def cancel(self, operation):
        handle = operation.get('local_process')
        if not isinstance(handle, Mapping):
            raise OperationStateError('local process has not been started')
        result = _result_mapping(
            self._canceller(copy.deepcopy(handle)), 'local canceller'
        )
        return AdapterCancellation(
            accepted=result.get('accepted'), detail=result
        )

    def validate_post_state(self, operation):
        if self._post_validator is None:
            return PostStateResult(False, {
                'reason': 'local process exit is not remote post-state proof',
            })
        result = _result_mapping(
            self._post_validator(copy.deepcopy(operation)),
            'local post-state validator',
        )
        return PostStateResult(bool(result.get('confirmed')), result)


class RemoteProviderAdapter:
    """Adapter delegating remote semantics to an endpoint-bound provider."""

    authority_kind = 'remote_provider'

    def __init__(
        self,
        binding,
        starter: Callable,
        validator: Callable | None = None,
        previewer: Callable | None = None,
        post_validator: Callable | None = None,
    ):
        if not callable(starter):
            raise OperationBusError('remote starter must be callable')
        self.binding = binding
        self._starter = starter
        self._validator = validator
        self._previewer = previewer
        self._post_validator = post_validator

    def validate(self, operation):
        if self._validator is None:
            return {'valid': True, 'authority_kind': self.authority_kind}
        return _result_mapping(
            self._validator(self.binding, copy.deepcopy(operation)),
            'provider validator',
        )

    def preview(self, operation):
        if self._previewer is None:
            return {
                'authority_kind': self.authority_kind,
                'operation_kind': operation['request']['operation_kind'],
                'target_resource_id': operation['request'][
                    'target_resource_id'
                ],
            }
        return _result_mapping(
            self._previewer(self.binding, copy.deepcopy(operation)),
            'provider previewer',
        )

    def start(self, operation):
        provider_operation = self._starter(
            self.binding, copy.deepcopy(operation)
        )
        return AdapterStart(
            provider_operation=validate_contract(
                'Operation', _result_mapping(
                    provider_operation, 'provider starter'
                )
            )
        )

    def inspect(self, operation):
        provider_operation = operation.get('provider_operation')
        if not isinstance(provider_operation, Mapping):
            raise OperationStateError(
                'provider operation has not been started'
            )
        inspector = getattr(self.binding.instance, 'get_operation', None)
        if not callable(inspector):
            raise OperationBusError(
                'provider does not expose OperationProvider.get_operation'
            )
        observed = validate_contract(
            'Operation', _result_mapping(
                inspector(copy.deepcopy(provider_operation)),
                'provider operation inspector',
            )
        )
        return AdapterObservation(provider_operation=observed)

    def cancel(self, operation):
        provider_operation = operation.get('provider_operation')
        if not isinstance(provider_operation, Mapping):
            raise OperationStateError(
                'provider operation has not been started'
            )
        canceller = getattr(self.binding.instance, 'cancel', None)
        if not callable(canceller):
            raise OperationBusError(
                'provider does not expose cancellable operation semantics'
            )
        observed = validate_contract(
            'Operation', _result_mapping(
                canceller(copy.deepcopy(provider_operation)),
                'provider canceller',
            )
        )
        receipt = observed.get('provider_receipt')
        accepted = None
        if isinstance(receipt, Mapping):
            value = receipt.get('cancel_request_accepted')
            if isinstance(value, bool):
                accepted = value
        return AdapterCancellation(
            accepted=accepted,
            provider_operation=observed,
            detail={'provider_response_observed': True},
        )

    def validate_post_state(self, operation):
        if self._post_validator is None:
            return PostStateResult(False, {
                'reason': 'provider has no independent post-state validator',
            })
        result = _result_mapping(
            self._post_validator(self.binding, copy.deepcopy(operation)),
            'provider post-state validator',
        )
        return PostStateResult(bool(result.get('confirmed')), result)
