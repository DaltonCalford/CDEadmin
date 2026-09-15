"""The UI oracle reads the saved endpoint without escaping its fixture."""

import json
import sqlite3
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import pytest

from tools import cdeadmin_firebird_database_lifecycle_ui_gate as gate
from pgadmin.cdeadmin.providers.form_contracts import (
    _database_contract, _DATABASE_SPECS,
)


@pytest.fixture
def saved_profile(tmp_path):
    path = tmp_path / 'owned-config.db'
    with sqlite3.connect(path) as connection:
        connection.executescript('''
            CREATE TABLE cde_endpoint_route (
              id TEXT, endpoint_id TEXT, priority INTEGER, configuration TEXT);
            CREATE TABLE cde_endpoint_database_target (
              id TEXT, endpoint_id TEXT);
        ''')
        connection.execute('INSERT INTO cde_endpoint_database_target VALUES '
                           '(?, ?)', ('owned-target', 'owned-endpoint'))
        for name, endpoint, priority, host in (
                ('owned-first', 'owned-endpoint', 0, '127.0.0.1'),
                ('owned-second', 'owned-endpoint', 1, '127.0.0.2'),
                ('other-endpoint', 'other', 0, 'unrelated.invalid')):
            connection.execute('INSERT INTO cde_endpoint_route VALUES '
                               '(?, ?, ?, ?)', (
                                   name, endpoint, priority, json.dumps({
                                       'host': host, 'port': 53050,
                                       'user': 'SYSDBA',
                                       'decfloat_round': 'FLOOR'})))
    contract = _database_contract(
        'firebird-native', _DATABASE_SPECS['firebird-native'])
    return SimpleNamespace(config_db=path, host='127.0.0.1',
                           firebird_port=53050,
                           database_forms=contract['forms'])


def test_saved_route_is_scoped_to_the_target_and_priority(saved_profile):
    rows = gate._saved_route(saved_profile, 'owned-target')
    assert [row.id for row in rows] == ['owned-first', 'owned-second']
    with pytest.raises(RuntimeError, match='no saved route'):
        gate._saved_route(saved_profile, 'missing-target')


@pytest.mark.parametrize('choice,expected', [
    ('SERVER_DEFAULT', 'FLOOR'), ('NATIVE_DEFAULT', 'NATIVE_DEFAULT'),
    ('HALF_EVEN', 'HALF_EVEN'),
])
def test_native_oracle_uses_saved_parent_and_exact_target(
        monkeypatch, saved_profile, choice, expected):
    selected = {'target_id': 'owned-target', 'database': '/owned/test.fdb',
                'configuration': {'decfloat_round': choice}}
    arguments = Mock(return_value={'database': 'private-owned-config'})
    monkeypatch.setattr(gate, '_route_arguments', arguments)
    initialize = Mock()
    monkeypatch.setattr(gate, '_initialize_connection', initialize)
    observation = {'mode': 'native-observed-mode', 'values': []}
    observe = Mock(return_value=observation)
    monkeypatch.setattr(gate, 'observe_rounding', observe)
    module = SimpleNamespace(connect=MagicMock())
    result = gate._native_saved_rounding(
        saved_profile, module, 'owned-test-credential', selected)
    assert result is observation
    route = arguments.call_args.args[0]
    assert route['decfloat_round'] == expected
    assert route['database'] == '/owned/test.fdb'
    assert route['route_id'] == 'owned-first'
    assert route['user'] == 'SYSDBA'
    assert selected['configuration'] == {'decfloat_round': choice}
    module.connect.assert_called_once_with(
        password='owned-test-credential', database='private-owned-config')
    handle = module.connect.return_value.__enter__.return_value
    initialize.assert_called_once_with(handle, route, module)
    observe.assert_called_once_with(handle)


@pytest.mark.parametrize('field,value', [
    ('host', 'unrelated.invalid'), ('firebird_port', 12345),
])
def test_oracle_rejects_endpoint_escape_before_native_connection(
        saved_profile, field, value):
    setattr(saved_profile, field, value)
    module = SimpleNamespace(connect=Mock())
    with pytest.raises(RuntimeError, match='escaped'):
        gate._native_saved_rounding(
            saved_profile, module, 'owned-credential', {
                'target_id': 'owned-target', 'database': '/owned/test.fdb',
                'configuration': {'decfloat_round': 'SERVER_DEFAULT'}})
    module.connect.assert_not_called()


def test_server_properties_use_the_endpoint_category(monkeypatch):
    for name in ('ENGINE_NAME', 'ENGINE_ID', 'PROFILE_ID',
                 'ENDPOINT_PASSWORD', 'COMMANDS'):
        monkeypatch.setattr(gate.shared, name, getattr(gate.shared, name))
    gate._configure_shared('owned-fixture-secret')
    assert gate.shared.COMMANDS['server_edit'] == 'endpoint.firebird.properties'
    assert gate.shared.MENU_GROUPS['endpoint'] == 'Endpoint registration'
    action = {'command_id': 'endpoint.firebird.properties',
              'menu_group': 'endpoint', 'enabled': True}
    context = {'endpoint_actions': [action], 'selected_actions': []}
    monkeypatch.setattr(gate.shared, '_tree_context',
                        Mock(return_value=context))
    monkeypatch.setattr(gate.shared, 'WebDriverWait',
                        Mock(return_value=SimpleNamespace(
                            until=lambda _callback: True)))
    driver = Mock()
    driver.execute_async_script.return_value = True
    assert gate.shared._action(driver, 'server_edit') is action
    assert driver.execute_async_script.call_count == 1
