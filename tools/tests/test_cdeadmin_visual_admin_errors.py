"""Native diagnostics, redaction and the real workspace route."""

import ast
import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

import pytest
from flask import Flask, request

WEB = Path(__file__).resolve().parents[2] / 'web'
if str(WEB) not in sys.path:
    sys.path.insert(0, str(WEB))
if 'pgadmin' not in sys.modules:
    package = ModuleType('pgadmin')
    package.__path__ = [str(WEB / 'pgadmin')]
    sys.modules['pgadmin'] = package

from pgadmin.cdeadmin.visual_admin import (  # noqa: E402
    VisualAdminError, VisualAdminExecutionError,
)
from pgadmin.cdeadmin.visual_admin.provider import (  # noqa: E402
    _native_status_codes,
)
from pgadmin.cdeadmin.visual_admin.execution_errors import (  # noqa: E402
    execution_failure_response,
)


@pytest.mark.parametrize('codes', [
    None, 'credential-canary', [True], [False], [0], [-1], [2147483648],
    [1.5], [335544788, 'credential-canary'], [1] * 33, {1}, iter([1]),
])
def test_invalid_diagnostics_are_not_exported(codes):
    assert _native_status_codes(
        SimpleNamespace(native_status_codes=codes)) == []


def test_diagnostic_property_cannot_break_error_response():
    class Fault:
        @property
        def native_status_codes(self):
            raise RuntimeError('credential-canary')
    assert _native_status_codes(Fault()) == []


@pytest.mark.parametrize('stage, phrase', [
    ('provider_response_unavailable', 'outcome is unknown'),
    ('observation_response_unavailable', 'No mutation was retried'),
    ('cancel_response_unavailable', 'Cancellation is not confirmed'),
    ('post_state_response_unavailable', 'post-state check failed'),
    ('credential-canary', 'Inspect native state'),
])
def test_public_receipt_is_allowlisted_and_stage_specific(stage, phrase):
    error = VisualAdminExecutionError('credential-canary', {
        'stage': stage, 'operation_id': 'ea4a5eb3-bcee-4348-8629-bc489961cf2c',
        'unknown_outcome': True, 'native_status_codes': [335544788, 335545112],
        'provider_payload': {'password': 'credential-canary'},
        'provider_result': 'credential-canary',
        'events': ['credential-canary'],
        'automatic_mutation_retry': True,
    })
    result = execution_failure_response(error, lambda text: text)
    assert result['status'] == 502
    assert result['success'] == 0
    assert phrase in result['errormsg']
    assert '335544788, 335545112' in result['errormsg']
    assert 'credential-canary' not in json.dumps(result)
    receipt = result['data']['control_operation']
    assert receipt['unknown_outcome'] is True
    assert receipt['automatic_mutation_retry'] is False
    assert receipt['operation_id'] == error.operation['operation_id']
    assert set(receipt) == {
        'stage', 'unknown_outcome', 'automatic_mutation_retry',
        'native_status_codes', 'operation_id'}


@pytest.mark.parametrize('identifier', [None, 1, 'credential-canary', {}, []])
def test_non_uuid_operation_id_is_not_exported(identifier):
    error = VisualAdminExecutionError('private', {
        'operation_id': identifier, 'unknown_outcome': 'private'})
    receipt = execution_failure_response(
        error, str)['data']['control_operation']
    assert 'operation_id' not in receipt
    assert receipt['unknown_outcome'] is False


@pytest.mark.parametrize('execution_error', [False, True])
def test_actual_workspace_handler_distinguishes_execution_and_validation(
        execution_error):
    source = WEB / 'pgadmin/browser/server_groups/servers/__init__.py'
    syntax = ast.parse(source.read_text())
    function = next(node for node in ast.walk(syntax)
                    if isinstance(node, ast.FunctionDef)
                    and node.name == 'cde_workspace')
    function.decorator_list = []
    exception = (VisualAdminExecutionError('credential-canary', {
        'stage': 'provider_response_unavailable', 'unknown_outcome': True,
        'native_status_codes': [335544788, 335545112],
    }) if execution_error else VisualAdminError('credential-canary'))
    service = Mock()
    service.apply_visual_admin.side_effect = exception
    app = Flask(__name__)
    namespace = {
        'get_server': Mock(return_value=object()),
        '_is_non_owner': lambda _: False,
        '_cde_registration': lambda _: {'workflow': 'provider_endpoint'},
        'provider_workspace_for_app': lambda _: service,
        'current_app': app, 'request': request,
        'gettext': lambda text: text,
        'make_json_response': lambda **value: value,
        'execution_failure_response': execution_failure_response,
        'VisualAdminExecutionError': VisualAdminExecutionError,
        'VisualAdminError': VisualAdminError,
    }
    # Resolve unrelated exception clauses without importing the PostgreSQL
    # server module or replacing the actual handler's control flow.
    for node in ast.walk(function):
        if isinstance(node, ast.ExceptHandler) and node.type is not None:
            for name in ast.walk(node.type):
                if isinstance(name, ast.Name) and name.id not in namespace:
                    namespace[name.id] = type(name.id, (Exception,), {})
    exec(compile(ast.Module(body=[function], type_ignores=[]),
                 str(source), 'exec'), namespace)
    with app.test_request_context('/', method='POST', json={
            'action': 'visual_admin_apply', 'request': {'plan_id': 'owned'}}):
        response = namespace['cde_workspace'](Mock(), 1, 20)
    service.apply_visual_admin.assert_called_once()
    assert 'credential-canary' not in json.dumps(response)
    if execution_error:
        assert response['status'] == 502
        assert 'outcome is unknown' in response['errormsg']
        receipt = response['data']['control_operation']
        assert receipt['native_status_codes'] == [335544788, 335545112]
    else:
        assert response['status'] == 400
        assert 'unknown' not in response['errormsg']
        assert 'data' not in response
