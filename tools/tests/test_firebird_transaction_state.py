"""Native transaction observations do not create or finalize transactions."""

from enum import IntEnum
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, PropertyMock, patch

import pytest

WEB = Path(__file__).resolve().parents[2] / 'web'
if str(WEB) not in sys.path:
    sys.path.insert(0, str(WEB))
if 'pgadmin' not in sys.modules:
    package = ModuleType('pgadmin')
    package.__path__ = [str(WEB / 'pgadmin')]
    sys.modules['pgadmin'] = package

from pgadmin.cdeadmin.providers.firebird.transaction_state import (  # noqa: E402
    observe_transaction,
)


class Isolation(IntEnum):
    SNAPSHOT = 2


def connection(active=True, closed=False):
    info = SimpleNamespace(
        id=321, isolation=Isolation.SNAPSHOT, lock_timeout=12,
        oit=100, oat=101, ost=102, snapshot_number=456,
        is_read_only=lambda: False)
    transaction = Mock()
    transaction.is_active.return_value = active
    transaction.is_closed.return_value = closed
    transaction.info = info
    return SimpleNamespace(main_transaction=transaction)


def test_active_transaction_information_is_native_and_enum_is_serializable():
    value = connection()
    result = observe_transaction(value)
    assert result['state'] == 'active'
    assert result['in_transaction'] is True
    assert len(result['fields']) == 8
    assert all(field['available'] for field in result['fields'].values())
    assert result['fields']['isolation']['value'] == 'SNAPSHOT'
    assert result['fields']['transaction_id']['value'] == 321
    assert result['fields']['read_only']['value'] is False
    assert value.main_transaction.method_calls == [
        ('is_active', (), {}), ('is_closed', (), {})]


@pytest.mark.parametrize('closed,state', [(False, 'idle'), (True, 'closed')])
def test_inactive_observation_never_requests_native_transaction_info(
        closed, state):
    value = connection(active=False, closed=closed)
    with patch.object(type(value.main_transaction), 'info',
                      new_callable=PropertyMock, create=True) as info:
        info.side_effect = AssertionError('Must not start a transaction')
        result = observe_transaction(value)
        info.assert_not_called()
    assert result['state'] == state
    assert result['fields'] == {}
    assert result['in_transaction'] is False


def test_failed_activity_observation_is_unknown_not_idle_and_does_not_leak():
    value = connection()
    value.main_transaction.is_active.side_effect = RuntimeError('secret')
    result = observe_transaction(value)
    assert result['state'] == 'unknown'
    assert 'in_transaction' not in result
    assert 'secret' not in str(result)


def test_one_missing_native_field_does_not_hide_the_remaining_observations():
    value = connection()
    del value.main_transaction.info.snapshot_number
    result = observe_transaction(value)
    assert result['fields']['snapshot_number'] == {
        'available': False, 'error_type': 'AttributeError'}
    assert result['fields']['transaction_id']['value'] == 321


def test_failed_info_acquisition_keeps_activity_without_inventing_fields():
    value = connection()
    with patch.object(type(value.main_transaction), 'info',
                      new_callable=PropertyMock, create=True) as info:
        info.side_effect = RuntimeError('secret')
        result = observe_transaction(value)
    assert result['state'] == 'active'
    assert result['fields'] == {}
    assert result['information_error_type'] == 'RuntimeError'
    assert 'secret' not in str(result)
