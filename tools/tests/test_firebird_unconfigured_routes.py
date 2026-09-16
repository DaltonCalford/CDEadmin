"""Minimal provider routes must not fall through to global driver defaults."""

from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock

import firebird.driver as native
from firebird.driver.config import DriverConfig
import pytest

from tools.cdeadmin_firebird_admin_mapping_gate import _route_arguments


@pytest.mark.parametrize('host', [None, '127.0.0.10', 'db.example', '::1'])
@pytest.mark.parametrize('interference', ['dsn', 'host', 'named', 'session'])
def test_minimal_route_reaches_driver_without_unselected_defaults(
        monkeypatch, host, interference):
    registry = DriverConfig('owned-isolation')
    monkeypatch.setattr(native, 'driver_config', registry)
    monkeypatch.setattr(native.core, 'driver_config', registry)
    if interference == 'dsn':
        registry.db_defaults.dsn.value = 'unselected:other'
    elif interference == 'host':
        registry.server_defaults.host.value = 'unselected'
        registry.server_defaults.port.value = '3059'
    elif interference == 'named':
        registry.register_database('inventory').database.value = 'other'
    else:
        registry.db_defaults.role.value = 'UNSELECTED'
        registry.db_defaults.session_time_zone.value = 'Pacific/Honolulu'
        registry.db_defaults.parallel_workers.value = 7
    registry.db_defaults.password.value = 'unselected-password'
    registry.server_defaults.password.value = 'unselected-server-password'
    defaults = (registry.db_defaults.get_config(),
                registry.server_defaults.get_config())
    route = {'database': 'inventory', 'user': 'selected'}
    if host:
        route.update(host=host, port=53050)
    args = _route_arguments(route, native)
    connector = Mock()
    dpb = Mock()
    monkeypatch.setattr(native.core, '__make_connection', connector)
    monkeypatch.setattr(native.core, 'DPB', dpb)
    native.connect(**args, password='selected-password')
    address = '[::1]' if host == '::1' else host
    expected = f'{address}/53050:inventory' if host else 'inventory'
    assert connector.call_args.args[1] == expected
    options = dpb.call_args.kwargs
    assert options['user'] == 'selected'
    assert options['password'] == 'selected-password'
    assert options['role'] is None
    assert options['session_time_zone'] is None
    assert options['parallel_workers'] is None
    assert defaults == (registry.db_defaults.get_config(),
                        registry.server_defaults.get_config())


def test_minimal_routes_keep_stable_separate_private_identities(monkeypatch):
    registry = DriverConfig('owned-concurrency')
    monkeypatch.setattr(native, 'driver_config', registry)

    def map_route(index):
        return _route_arguments({
            'host': '127.0.0.10', 'port': 53050,
            'database': 'inventory', 'user': f'user{index}'},
            native)['database']

    with ThreadPoolExecutor(max_workers=8) as executor:
        names = list(executor.map(map_route, list(range(8)) * 4))
    assert len(set(names)) == 8
    assert names[:8] == names[8:16] == names[16:24] == names[24:]
    assert all(registry.get_database(name) is not None for name in names)
