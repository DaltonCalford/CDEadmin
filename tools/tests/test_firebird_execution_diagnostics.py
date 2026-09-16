"""Firebird query diagnostics admit codes, not arbitrary driver attributes."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tools.cdeadmin_firebird_admin_mapping_gate import ADMINISTRATION  # noqa
from pgadmin.cdeadmin.providers.firebird.provider import _create_client
from pgadmin.cdeadmin.sdk.relational import RelationalClientError


@pytest.mark.parametrize('errno,sqlstate,expected', [
    (-104, '42000', ('errno=-104', 'sqlstate=42000')),
    (0, 'HY000', ('errno=0', 'sqlstate=HY000')),
    (2147483647, '08001', ('errno=2147483647', 'sqlstate=08001')),
    (-2147483648, '23000', ('errno=-2147483648', 'sqlstate=23000')),
    ('private-password', 'private SQL path', ()),
    (True, '42000\n', ()), (2 ** 128, 'hy000', ()),
    ([], '４２０００', ()), ({}, 42000, ()), (None, None, ()),
])
@pytest.mark.parametrize('phase', ['execute', 'fetch'])
def test_execution_diagnostics_are_bounded(
        monkeypatch, errno, sqlstate, expected, phase):
    monkeypatch.setattr(
        'pgadmin.cdeadmin.providers.firebird.provider.'
        '_configure_client_library', Mock())
    client = _create_client(SimpleNamespace(acquire_secret=None))
    handle = Mock()
    client._connections.append(handle)
    error = RuntimeError('private password SQL path')
    error.errno, error.sqlstate = errno, sqlstate
    error.gds_codes = (335544569,)
    cursor = handle.cursor.return_value
    cursor.description = [('V', int)]
    if phase == 'execute':
        cursor.execute.side_effect = error
    else:
        cursor.fetchall.side_effect = error
    with pytest.raises(RelationalClientError) as caught:
        client.execute(handle, {'source': 'SELECT 1 FROM RDB$DATABASE'})
    message = str(caught.value)
    assert 'private' not in message
    assert caught.value.gds_codes == (335544569,)
    for item in expected:
        assert item in message
    assert ('errno=' in message) is any(
        x.startswith('errno=') for x in expected)
    assert ('sqlstate=' in message) is any(
        x.startswith('sqlstate=') for x in expected)
    cursor.close.assert_called_once_with()
    handle.commit.assert_not_called()


def test_broken_properties_do_not_replace_query_failure(monkeypatch):
    monkeypatch.setattr(
        'pgadmin.cdeadmin.providers.firebird.provider.'
        '_configure_client_library', Mock())

    class DriverError(Exception):
        @property
        def errno(self):
            raise ValueError('private attribute error')

        sqlstate = errno
        gds_codes = (335544569,)

    client = _create_client(SimpleNamespace(acquire_secret=None))
    handle = Mock()
    client._connections.append(handle)
    handle.cursor().execute.side_effect = DriverError('private query')
    with pytest.raises(RelationalClientError) as caught:
        client.execute(handle, {'source': 'SELECT 1 FROM RDB$DATABASE'})
    assert 'DriverError' in str(caught.value)
    assert 'private' not in str(caught.value)
    assert caught.value.gds_codes == (335544569,)
