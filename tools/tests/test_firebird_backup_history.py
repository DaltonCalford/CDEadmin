"""Exact Firebird 5 backup-history SPB and visual admission contracts."""
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import pytest

from tools.cdeadmin_firebird_admin_mapping_gate import RelationalClientError
from pgadmin.cdeadmin.providers.firebird.provider import (
    ADMINISTRATION, _service_backup_with_history,
)


def fixture():
    import firebird.driver as driver
    builder = Mock()
    builder.get_buffer.return_value = b'owned-start'
    module = SimpleNamespace(core=driver.core,
                             SrvNBackupFlag=driver.SrvNBackupFlag,
                             get_api=Mock())
    module.get_api.return_value.util.get_xpb_builder.return_value = (
        MagicMock(__enter__=Mock(return_value=builder)))
    server = Mock(encoding='utf-8')
    return module, server, builder


@pytest.mark.parametrize('unit', ['ROWS', 'DAYS'])
@pytest.mark.parametrize('guid', [
    None, '{00112233-4455-6677-8899-AABBCCDDEEFF}'])
@pytest.mark.parametrize('direct', [None, False, True])
def test_native_spb_retention_preserves_backup_identity(unit, guid, direct):
    module, server, builder = fixture()
    options = {'clean_history': True, 'history_keep_unit': unit,
               'history_keep_value': 7, 'backup_file': '/owned/東京.nbk',
               'backup_level': 3, 'backup_flags': ['NO_TRIGGERS'],
               'database_guid': guid, 'direct_io': direct, 'role': 'rôle'}
    _service_backup_with_history(server, '/owned/é.fdb', options, module)
    core = module.core
    assert builder.method_calls[0] == (
        'insert_tag', (core.ServerAction.NBAK,), {})
    builder.insert_tag.assert_any_call(core.SrvNBackupOption.CLEAN_HISTORY)
    builder.insert_int.assert_any_call(
        core.SrvNBackupOption['KEEP_' + unit], 7)
    builder.insert_string.assert_any_call(core.SPBItem.DBNAME, '/owned/é.fdb',
                                          encoding='utf-8')
    builder.insert_string.assert_any_call(core.SrvNBackupOption.FILE,
                                          '/owned/東京.nbk', encoding='utf-8')
    builder.insert_string.assert_any_call(core.SPBItem.SQL_ROLE_NAME, 'rôle',
                                          encoding='utf-8')
    levels = [call for call in builder.insert_int.call_args_list
              if call.args[0] == core.SrvNBackupOption.LEVEL]
    assert bool(levels) is (guid is None)
    if guid:
        builder.insert_string.assert_any_call(core.SrvNBackupOption.GUID, guid)
    directs = [call for call in builder.insert_string.call_args_list
               if call.args[0] == core.SrvNBackupOption.DIRECT]
    assert bool(directs) is (direct is not None)
    if direct is not None:
        assert directs[0].args[1] == ('ON' if direct else 'OFF')
    server._svc.start.assert_called_once_with(b'owned-start')
    server.wait.assert_called_once_with()


@pytest.mark.parametrize('unit,count', [
    ('ROWS', 0), ('DAYS', -1), ('ROWS', 2147483648), ('ROWS', True),
    ('DAYS', 1.5), ('ROWS', '1'), ('DAYS', None), ('ROWS', []),
    ('WEEKS', 1), ([], 1), ({}, 1), (None, 1),
])
def test_invalid_history_never_starts_native_service(unit, count):
    module, server, _builder = fixture()
    draft = {'clean_history': True, 'history_keep_unit': unit,
             'history_keep_value': count}
    assert ADMINISTRATION._validate_firebird_service('backup_physical', draft)
    with pytest.raises(RelationalClientError):
        _service_backup_with_history(server, '/owned/x.fdb', draft, module)
    server._reset_output.assert_not_called()
    server._svc.start.assert_not_called()


@pytest.mark.parametrize('unit', ['ROWS', 'DAYS'])
@pytest.mark.parametrize('count', [1, 2147483647])
def test_retention_integer_boundaries(unit, count):
    assert not ADMINISTRATION._validate_firebird_service('backup_physical', {
        'clean_history': True, 'history_keep_unit': unit,
        'history_keep_value': count})


@pytest.mark.parametrize('clean', [False, None, 'true', 1, [], {}])
def test_retention_requires_explicit_cleanup(clean):
    assert ADMINISTRATION._validate_firebird_service('backup_physical', {
        'clean_history': clean, 'history_keep_unit': 'ROWS',
        'history_keep_value': 1})


def test_native_start_failure_is_not_reported_as_completion():
    module, server, _builder = fixture()
    server._svc.start.side_effect = RuntimeError('native failure')
    with pytest.raises(RuntimeError, match='native failure'):
        _service_backup_with_history(server, '/owned/x.fdb', {
            'clean_history': True, 'history_keep_unit': 'ROWS',
            'history_keep_value': 1, 'backup_file': '/owned/x.nbk'}, module)
    server.wait.assert_not_called()
