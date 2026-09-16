"""Interrupted calls preserve uncertainty without suppressing shutdown."""

import pytest

from tools.tests.test_cdeadmin_visual_admin import (
    NativeAdapter, Permissions, context,
    ProviderVisualAdministration, VisualAdminAccessError,
    VisualAdminExecutionError,
)


@pytest.mark.parametrize('interruption', [
    KeyboardInterrupt, SystemExit, GeneratorExit])
@pytest.mark.parametrize('method,callback,stage', [
    ('apply', 'apply_admin_operation', 'provider_response_unavailable'),
    ('refresh_operation', 'inspect_admin_operation',
     'observation_response_unavailable'),
    ('cancel_operation', 'cancel_admin_operation',
     'cancel_response_unavailable'),
    ('validate_operation_post_state', 'validate_admin_post_state',
     'post_state_response_unavailable'),
])
def test_interruption_preserves_record_without_replay(
        interruption, method, callback, stage):
    class Adapter(NativeAdapter):
        @staticmethod
        def visual_admin_catalog(catalog):
            value = NativeAdapter.visual_admin_catalog(catalog)
            for item in value['objects']:
                if item['resource_kind'] == 'database':
                    for operation in item['operations']:
                        if operation['operation_id'] == 'create':
                            operation['cancellable'] = True
            return value

    adapter = Adapter()
    provider = ProviderVisualAdministration(
        context(), Permissions(), 'mysql', '9.7.0', adapter)
    plan = provider.plan({
        'resource_kind': 'database', 'operation_id': 'create',
        'draft': {'name': 'owned', 'options': {}},
    })
    apply_request = {'plan_id': plan['plan_id'],
                     'plan_digest': plan['plan_digest']}
    if method == 'apply':
        request = apply_request
    else:
        result = provider.apply(apply_request)
        request = {'operation_id': result['control_operation']['operation_id']}
    error = interruption('private-credential-canary')
    error.native_status_codes = (335544788,)
    # A shutdown interruption must not restore a mutation preview, even if
    # it carries the ordinary credential exception's retry marker.
    error.credential_required_before_dispatch = True
    calls = []

    def interrupt(request):
        calls.append(request)
        raise error

    setattr(adapter, callback, interrupt)
    with pytest.raises(interruption) as caught:
        getattr(provider, method)(request)
    assert caught.value is error
    assert len(calls) == 1
    record, = provider.list_operations()['items']
    assert record['stage'] == stage
    assert record['unknown_outcome'] is True
    assert record['automatic_mutation_retry'] is False
    assert record['native_status_codes'] == [335544788]
    assert record['events'][-1]['event_kind'] == stage
    assert 'private-credential-canary' not in repr(record)
    assert provider.get_operation({
        'operation_id': record['operation_id']}) == record
    with pytest.raises(VisualAdminAccessError):
        provider.apply(apply_request)
    if method == 'cancel_operation':
        assert provider.cancel_operation(request) == record
    assert len(calls) == 1


@pytest.mark.parametrize('failure', [
    'none', 'list', 'text', 'copy-error', 'copy-interrupt',
    'credential-marker'])
def test_invalid_mutation_response_is_unknown_and_never_retryable(failure):
    error = (KeyboardInterrupt('private-response-canary')
             if failure == 'copy-interrupt' else
             RuntimeError('private-response-canary'))
    error.credential_required_before_dispatch = failure == 'credential-marker'

    class Uncopyable:
        def __deepcopy__(self, memo):
            raise error

    class Adapter(NativeAdapter):
        def apply_admin_operation(self, request):
            self.applied.append(request)
            return {'none': None, 'list': [], 'text': 'not a response'}.get(
                failure, {'value': Uncopyable()})

    adapter = Adapter()
    provider = ProviderVisualAdministration(
        context(), Permissions(), 'mysql', '9.7.0', adapter)
    plan = provider.plan({
        'resource_kind': 'database', 'operation_id': 'create',
        'draft': {'name': 'owned', 'options': {}},
    })
    request = {'plan_id': plan['plan_id'], 'plan_digest': plan['plan_digest']}
    expected = (KeyboardInterrupt if failure == 'copy-interrupt'
                else VisualAdminExecutionError)
    with pytest.raises(expected):
        provider.apply(request)
    record, = provider.list_operations()['items']
    assert record['stage'] == 'provider_response_unavailable'
    assert record['unknown_outcome'] is True
    assert record['provider_result'] is None
    assert 'private-response-canary' not in repr(record)
    with pytest.raises(VisualAdminAccessError):
        provider.apply(request)
    assert len(adapter.applied) == 1
