"""Sample crypto qualification cannot install into an existing demo server."""
from unittest.mock import Mock

import pytest

from tools import cdeadmin_firebird_sample_crypt_gate as gate


@pytest.fixture
def plugins(tmp_path):
    # Never loaded: all container operations in these tests are mocked.
    for name in ('fbSampleDbCrypt', 'fbSampleKeyHolder'):
        file = tmp_path / ('lib' + name + '.so')
        file.write_bytes(b'mocked sample plugin')
    return tmp_path


def test_missing_plugin_prevents_any_container_operation(
        monkeypatch, tmp_path):
    monkeypatch.setattr(gate, '_configure_client_library', Mock())
    docker = Mock()
    monkeypatch.setattr(gate, 'docker', docker)
    with pytest.raises(ValueError, match='NON-SECURE'):
        gate.run('owned-image', tmp_path)
    docker.assert_not_called()


@pytest.mark.parametrize('failure', [
    'create-container', 'install-owned-sample-plugins', 'start-container'])
def test_startup_failure_redacts_credentials_and_removes_only_owned_id(
        monkeypatch, plugins, failure):
    monkeypatch.setattr(gate, '_configure_client_library', Mock())
    monkeypatch.setattr(gate.secrets, 'token_urlsafe',
                        Mock(return_value='SAMPLE-SECRET'))

    def docker(*args, **kwargs):
        assert 'SAMPLE-SECRET' not in ' '.join(args)
        if args[0] == 'create':
            assert kwargs['env']['FIREBIRD_ROOT_PASSWORD'] == 'SAMPLE-SECRET'
            if failure == 'create-container':
                raise RuntimeError('SAMPLE-SECRET')
            return ('a' * 64).encode()
        assert args == ('start', 'a' * 64)
        raise RuntimeError('SAMPLE-SECRET')

    def write(container, *_args):
        assert container == 'a' * 64
        if failure == 'install-owned-sample-plugins':
            raise RuntimeError('SAMPLE-SECRET')

    monkeypatch.setattr(gate, 'docker', docker)
    monkeypatch.setattr(gate, 'write_container_file', write)
    monkeypatch.setattr(gate, 'read_container_file',
                        Mock(return_value=(b'owned config', 0, 0)))
    remove = Mock()
    monkeypatch.setattr(gate, 'remove_owned', remove)
    result = gate.run('owned-image', plugins)
    assert not result['complete']
    assert result['sample_plugins_are_not_secure']
    assert 'SAMPLE-SECRET' not in str(result)
    assert result['failures'] == [{
        'stage': failure, 'type': 'RuntimeError', 'native_status_codes': []}]
    if failure == 'create-container':
        remove.assert_not_called()
    else:
        remove.assert_called_once_with('a' * 64)
        assert result['owned_container_removed']


def test_mutable_container_name_cannot_receive_plugins_or_be_removed(
        monkeypatch, plugins):
    monkeypatch.setattr(gate, '_configure_client_library', Mock())
    docker = Mock(return_value=b'cdeadmin-demo-firebird')
    monkeypatch.setattr(gate, 'docker', docker)
    write = Mock()
    monkeypatch.setattr(gate, 'write_container_file', write)
    result = gate.run('owned-image', plugins)
    write.assert_not_called()
    assert docker.call_count == 1
    assert not result['complete']
    assert not result['owned_container_removed']
    assert result['failures'][-1] == {'stage': 'cleanup', 'type': 'ValueError'}
