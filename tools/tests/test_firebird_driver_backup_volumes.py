"""Isolate the installed 1.10.11 backup-volume bug without native I/O.

These are compatibility evidence, not assertions that the buggy API should
remain in use. The malformed shape must never reach a real service handle.
"""
from importlib.metadata import version
from unittest.mock import MagicMock, Mock

import pytest


@pytest.fixture
def installed_backup(monkeypatch):
    import firebird.driver.core as core
    if version('firebird-driver') != '1.10.11':
        pytest.skip('Exact installed-driver compatibility evidence: 1.10.11')
    builder = Mock()
    api = Mock()
    api.util.get_xpb_builder.return_value = MagicMock(
        __enter__=Mock(return_value=builder))
    monkeypatch.setattr(core.a, 'get_api', Mock(return_value=api))
    server = Mock(encoding='utf-8')
    service = Mock()
    service._srv.return_value = server
    return core, service, server, builder


@pytest.mark.parametrize('files,sizes', [
    (['first.fbk', 'last.fbk'], [2048]),
    (['first.fbk', 'middle.fbk', 'last.fbk'], [2048, 4096]),
])
def test_driver_rejects_native_valid_volume_shape_before_start(
        installed_backup, files, sizes):
    core, service, server, _builder = installed_backup
    with pytest.raises(AssertionError):
        core.ServerDbServices3.backup(
            service, database='owned.fdb', backup=files,
            backup_file_sizes=sizes)
    server._svc.start.assert_not_called()


def test_reversed_assertion_can_serialize_phantom_filename_only_to_mock(
        installed_backup):
    core, service, server, builder = installed_backup
    core.ServerDbServices3.backup(
        service, database='owned.fdb', backup=['first.fbk', 'last.fbk'],
        backup_file_sizes=[2048, 4096, 8192])
    filenames = [call.args[1] for call in builder.insert_string.call_args_list
                 if call.args[0] == core.SrvBackupOption.FILE]
    assert filenames == ['first.fbk', 'last.fbk', 'None']
    server._svc.start.assert_called_once_with(builder.get_buffer.return_value)
