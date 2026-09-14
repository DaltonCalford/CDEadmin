"""Level-chain qualification preserves recovery files on release failure."""
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import pytest

from tools import cdeadmin_firebird_backup_level_gate as gate


@pytest.mark.parametrize('failure', [None, 'backup', 'release', 'drop'])
@pytest.mark.parametrize('through_plan', [False, True])
def test_chain_and_cleanup_barriers(
        tmp_path, monkeypatch, failure, through_plan):
    monkeypatch.setattr(gate, '_configure_client_library', Mock())
    monkeypatch.setattr(gate, '_load_profile', Mock(return_value={
        'database': '/owned/sample.fdb', 'host': 'localhost', 'port': 53050,
        'password': 'SECRET'}))
    monkeypatch.setattr(gate, 'resolve_container', Mock(return_value='a' * 64))
    monkeypatch.setattr(gate, 'validate_profile', Mock())
    calls = []
    present = set()
    backups = []

    def docker(container, *args, absent_ok=False):
        calls.append(args)
        path = args[-1]
        if args[:3] == ('exec', 'rm', '-f'):
            assert path.endswith('.nbk')
            present.discard(path)
            return SimpleNamespace(returncode=0)
        expected = path not in present if '!' in args else path in present
        if not expected and not absent_ok:
            raise RuntimeError('fixture Docker failure')
        return SimpleNamespace(returncode=0 if expected else 1)

    def connect(native, profile, password, path, create=False):
        assert password == 'SECRET'
        assert path != '/owned/sample.fdb'
        if create:
            present.add(path)
        connection = MagicMock()
        connection.info.firebird_version = '5.0.4'
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.fetchall.return_value = (
            [(i,) for i in range(257)] if create else
            [(i, i * 7) for i in range(257)])
        return connection

    def operation(context, name, database, options):
        if name == 'backup_physical':
            level = options['backup_level']
            assert level == len(backups)
            if failure == 'backup' and level == 2:
                raise RuntimeError('SECRET')
            backups.append(options['backup_file'])
            present.add(options['backup_file'])
        else:
            assert name == 'restore_physical'
            assert options['backup_files'] == backups
            assert len(backups) == 257
            present.add(options['restore_database'])
        return {'server_completed': True,
                'service_release': {'service_handle_released': True}}

    client = Mock()
    client.run_server_operation.side_effect = operation
    if failure == 'release':
        client.close.side_effect = RuntimeError('SECRET')

    def drop(native, profile, password, path):
        if failure == 'drop':
            raise RuntimeError('SECRET')
        present.remove(path)

    monkeypatch.setattr(gate, 'docker', docker)
    monkeypatch.setattr(gate, 'database_connection', connect)
    monkeypatch.setattr(gate, 'drop_owned_database', drop)
    monkeypatch.setattr(gate, '_create_client', Mock(return_value=client))
    result = gate.run(tmp_path / 'profiles.json', 'demo',
                      through_plan=through_plan)
    assert result['through_visual_plan'] is through_plan
    assert result['complete'] == (failure is None)
    assert 'SECRET' not in str(result)
    if failure in ('release', 'drop'):
        assert len(present) == 259
        assert not any(args[:3] == ('exec', 'rm', '-f') for args in calls)
    else:
        assert not present
    if failure != 'backup':
        assert result['levels_completed'] == list(range(257))
        assert result['history_verified'] is True
        assert result['restored_payload_verified'] is True
