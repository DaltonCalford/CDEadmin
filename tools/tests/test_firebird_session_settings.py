"""Firebird timeout forms, units, validation and transaction scope."""

import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, call

import pytest

WEB = Path(__file__).resolve().parents[2] / 'web'
if str(WEB) not in sys.path:
    sys.path.insert(0, str(WEB))
if 'pgadmin' not in sys.modules:
    package = ModuleType('pgadmin')
    package.__path__ = [str(WEB / 'pgadmin')]
    sys.modules['pgadmin'] = package

from pgadmin.cdeadmin.providers.firebird.session_settings import (  # noqa: E402
    TIMEOUT_SETTINGS, initialize_timeouts,
)
from pgadmin.cdeadmin.sdk.relational import RelationalClientError  # noqa: E402

MODULE = SimpleNamespace(DefaultAction=SimpleNamespace(ROLLBACK='rollback'))


@pytest.mark.parametrize('route', [{}, {'statement_timeout_ms': None},
                                   {'session_idle_timeout_seconds': None}])
def test_unspecified_timeout_does_not_touch_attachment(route):
    connection = Mock()
    initialize_timeouts(connection, route, MODULE)
    assert not connection.mock_calls


@pytest.mark.parametrize('key,prefix,unit', TIMEOUT_SETTINGS)
@pytest.mark.parametrize('value', [-1, 2147483648, True, False, '10', 1.5])
def test_invalid_timeout_is_rejected_before_native_work(
        key, prefix, unit, value):
    connection = Mock()
    with pytest.raises(RelationalClientError, match='2147483647'):
        initialize_timeouts(connection, {key: value}, MODULE)
    assert not connection.mock_calls


@pytest.mark.parametrize('key,prefix,unit', TIMEOUT_SETTINGS)
@pytest.mark.parametrize('value', [0, 1, 1234, 2147483647])
def test_timeout_uses_explicit_native_units_and_separate_rollback_manager(
        key, prefix, unit, value):
    connection = Mock()
    manager = connection.transaction_manager.return_value
    manager.is_active.return_value = True
    initialize_timeouts(connection, {key: value}, MODULE)
    connection.transaction_manager.assert_called_once_with(
        default_action='rollback')
    assert manager.mock_calls == [
        call.execute_immediate(f'{prefix} {value} {unit}'),
        call.is_active(), call.rollback(), call.close()]
    assert not connection.main_transaction.mock_calls
    connection.commit.assert_not_called()
    connection.rollback.assert_not_called()


def test_all_values_validated_before_first_setting_is_applied():
    connection = Mock()
    with pytest.raises(RelationalClientError):
        initialize_timeouts(connection, {
            'statement_timeout_ms': 1000,
            'session_idle_timeout_seconds': -1}, MODULE)
    assert not connection.mock_calls


def test_native_failure_releases_only_owned_transaction():
    connection = Mock()
    manager = connection.transaction_manager.return_value
    manager.execute_immediate.side_effect = RuntimeError('native failure')
    manager.is_active.return_value = True
    with pytest.raises(RuntimeError, match='native failure'):
        initialize_timeouts(connection, {'statement_timeout_ms': 100}, MODULE)
    manager.rollback.assert_called_once_with()
    manager.close.assert_called_once_with()
    connection.rollback.assert_not_called()
    connection.commit.assert_not_called()


def test_rollback_failure_closes_with_rollback_default_never_commit():
    connection = Mock()
    manager = connection.transaction_manager.return_value
    manager.is_active.return_value = True
    manager.rollback.side_effect = RuntimeError('rollback failure')
    with pytest.raises(RuntimeError, match='rollback failure'):
        initialize_timeouts(connection, {'statement_timeout_ms': 100}, MODULE)
    connection.transaction_manager.assert_called_once_with(
        default_action='rollback')
    manager.close.assert_called_once_with()
    manager.commit.assert_not_called()


def test_inactive_owned_manager_is_not_rolled_back():
    connection = Mock()
    manager = connection.transaction_manager.return_value
    manager.is_active.return_value = False
    initialize_timeouts(connection, {'statement_timeout_ms': 0}, MODULE)
    manager.rollback.assert_not_called()
    manager.close.assert_called_once_with()


@pytest.mark.parametrize('key,prefix,unit', TIMEOUT_SETTINGS)
def test_server_and_database_forms_expose_native_units_and_bounds(
        key, prefix, unit):
    from pgadmin.cdeadmin.providers.form_contracts import (
        provider_form_contract,
    )
    manifest = json.loads((WEB / 'pgadmin/cdeadmin/providers/firebird/'
                           'provider_manifest.json').read_text())
    contract = provider_form_contract({
        **manifest['registration'], 'profile_id': 'firebird-native',
        'engine_id': 'firebird'})

    def find(value):
        if isinstance(value, dict):
            if value.get('field_id') == key:
                yield value
            for child in value.values():
                yield from find(child)
        elif isinstance(value, list):
            for child in value:
                yield from find(child)

    fields = list(find(contract))
    assert len(fields) >= 4
    assert all(field['minimum'] == 0 and field['maximum'] == 2147483647
               and unit.lower() in field['label'].lower() for field in fields)
