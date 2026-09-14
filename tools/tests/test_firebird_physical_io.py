"""Native default, explicit backup policies, and absent restore capability."""
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from tools.cdeadmin_firebird_admin_mapping_gate import RelationalClientError
from pgadmin.cdeadmin.providers.firebird.physical_io import (
    normalize_physical_io,
)
from pgadmin.cdeadmin.providers.firebird.provider import (
    ADMINISTRATION, _firebird_service_operation,
)
from tools.tests.test_firebird_backup_history import fixture


@pytest.mark.parametrize('draft,direct', [
    ({}, None), ({'direct_io': None}, None),
    ({'direct_io': True}, True), ({'direct_io': False}, False),
    ({'direct_io_mode': 'NATIVE'}, None),
    ({'direct_io_mode': 'ON'}, True), ({'direct_io_mode': 'OFF'}, False),
])
@pytest.mark.parametrize('history', [False, True])
def test_backup_policy_survives_plan_and_exact_native_dispatch(
        draft, direct, history):
    options = {**draft, 'backup_file': '/owned/x.nbk'}
    if history:
        options.update(clean_history=True, history_keep_unit='ROWS',
                       history_keep_value=1)
    request = {'engine_id': 'firebird', 'resource_kind': 'database',
               'operation_id': 'backup_physical', 'draft': options,
               '_provider_route': {'database': '/owned/x.fdb'}}
    assert not ADMINISTRATION.validate(request)['errors']
    plan = ADMINISTRATION.plan(request)
    assert plan['command_preview']['backup_io_requested'] == (
        'NATIVE' if direct is None else 'ON' if direct else 'OFF')
    compiled = plan['provider_payload']['compiled']['options']
    assert compiled['direct_io'] is direct
    assert 'direct_io_mode' not in compiled
    assert request['draft'] == options
    module, server, builder = fixture()
    observed = _firebird_service_operation(
        server, 'backup_physical', '/owned/x.fdb', options, module)
    assert observed['backup_io_requested'] == (
        'NATIVE' if direct is None else 'ON' if direct else 'OFF')
    if history:
        calls = [call for call in builder.insert_string.call_args_list
                 if call.args[0] == module.core.SrvNBackupOption.DIRECT]
        assert bool(calls) is (direct is not None)
        if calls:
            assert calls[0].args[1] == ('ON' if direct else 'OFF')
    else:
        assert server.database.nbackup.call_args.kwargs['direct'] is direct


@pytest.mark.parametrize('options', [
    {}, {'direct_io': None}, {'direct_io': False},
    {'direct_io_mode': 'NATIVE'},
])
def test_restore_omits_ineffective_direct_flag(options):
    server = Mock()
    module = SimpleNamespace(SrvNBackupFlag=Mock(return_value=0))
    draft = {**options, 'backup_files': ['/owned/x.nbk'],
             'restore_database': '/owned/restored.fdb'}
    request = {'engine_id': 'firebird', 'resource_kind': 'database',
               'operation_id': 'restore_physical', 'draft': draft,
               '_provider_route': {'database': '/owned/x.fdb'}}
    assert not ADMINISTRATION.validate(request)['errors']
    compiled = ADMINISTRATION.plan(request)['provider_payload']['compiled']
    assert compiled['options']['direct_io'] is None
    assert 'backup_io_requested' not in compiled
    result = _firebird_service_operation(
        server, 'restore_physical', '/owned/x.fdb', draft, module)
    assert server.database.nrestore.call_args.kwargs['direct'] is None
    assert 'backup_io_requested' not in result


@pytest.mark.parametrize('operation', ['backup_physical', 'restore_physical'])
@pytest.mark.parametrize('direct', [None, False, True])
def test_installed_driver_encodes_native_default_without_a_direct_clumplet(
        operation, direct):
    module, server, builder = fixture()
    receiver = SimpleNamespace(_srv=lambda: server)
    with patch('firebird.driver.core.a.get_api', module.get_api):
        if operation == 'backup_physical':
            module.core.ServerDbServices3.nbackup(
                receiver, database='/owned/x.fdb', backup='/owned/x.nbk',
                direct=direct)
        else:
            # The driver accepts the flag; the provider intentionally omits
            # it for restore because the engine never uses it on that path.
            module.core.ServerDbServices3.nrestore(
                receiver, database='/owned/y.fdb', backups=['/owned/x.nbk'],
                direct=direct)
    calls = [call for call in builder.insert_string.call_args_list
             if call.args[0] == module.core.SrvNBackupOption.DIRECT]
    assert bool(calls) is (direct is not None)
    if calls:
        assert calls[0].args[1] == ('ON' if direct else 'OFF')
    server._svc.start.assert_called_once_with(b'owned-start')
    server.wait.assert_called_once_with()


@pytest.mark.parametrize('operation,options', [
    *[('backup_physical', {'direct_io_mode': item})
      for item in (None, True, 0, [], {}, 'on', '', 'UNKNOWN')],
    *[('backup_physical', {'direct_io': item})
      for item in (0, 1, 'OFF', [], {})],
    ('backup_physical', {'direct_io_mode': 'ON', 'direct_io': True}),
    ('backup_physical', {'direct_io_mode': 'NATIVE', 'direct_io': None}),
    ('restore_physical', {'direct_io': True}),
    ('restore_physical', {'direct_io_mode': 'ON'}),
    ('restore_physical', {'direct_io_mode': 'OFF'}),
])
def test_invalid_or_ineffective_policy_is_rejected_before_native_access(
        operation, options):
    with pytest.raises(RelationalClientError):
        normalize_physical_io(operation, options)
    errors = ADMINISTRATION._validate_firebird_service(operation, options)
    assert any(item['code'] == 'invalid_firebird_physical_io'
               for item in errors)
    server = Mock()
    with pytest.raises(RelationalClientError):
        _firebird_service_operation(server, operation, '/owned/x.fdb',
                                    options, Mock())
    assert not server.mock_calls
