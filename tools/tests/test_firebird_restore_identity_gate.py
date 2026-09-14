"""Restore-identity qualification can remove only its owned container."""
from unittest.mock import Mock

import pytest

from tools import cdeadmin_firebird_restore_identity_gate as gate


@pytest.mark.parametrize('value', ['', 'demo', 'a' * 63, 'a' * 65, 'G' * 64])
def test_cleanup_rejects_non_identity_before_docker(monkeypatch, value):
    docker = Mock()
    monkeypatch.setattr(gate, 'docker', docker)
    with pytest.raises(ValueError):
        gate.remove_owned(value)
    docker.assert_not_called()


@pytest.mark.parametrize('label', [
    b'', b'other', b'firebird-service-security'])
def test_cleanup_rejects_other_owners(monkeypatch, label):
    docker = Mock(return_value=label)
    monkeypatch.setattr(gate, 'docker', docker)
    with pytest.raises(RuntimeError):
        gate.remove_owned('a' * 64)
    assert docker.call_count == 1
    assert docker.call_args.args[0] == 'inspect'


def test_cleanup_uses_immutable_identity(monkeypatch):
    docker = Mock(return_value=(gate.OWNER + '\n').encode())
    monkeypatch.setattr(gate, 'docker', docker)
    gate.remove_owned('b' * 64)
    assert docker.call_args.args == ('rm', '--force', '--volumes', 'b' * 64)


@pytest.mark.parametrize('phase', ['create', 'start'])
def test_initial_failure_redacts_credentials_and_cleans_only_owned(
        monkeypatch, phase):
    monkeypatch.setattr(gate, '_configure_client_library', Mock())
    monkeypatch.setattr(gate.secrets, 'token_urlsafe',
                        Mock(return_value='SECRET'))
    calls = []

    def docker(*args, **kwargs):
        calls.append(args)
        assert 'SECRET' not in ' '.join(args)
        if args[0] == 'create':
            assert kwargs['env']['FIREBIRD_ROOT_PASSWORD'] == 'SECRET'
            if phase == 'create':
                raise RuntimeError('SECRET')
            return ('b' * 64).encode()
        raise RuntimeError('SECRET')

    monkeypatch.setattr(gate, 'docker', docker)
    remove = Mock()
    monkeypatch.setattr(gate, 'remove_owned', remove)
    result = gate.run('owned-image')
    assert result['complete'] is False
    assert 'SECRET' not in str(result)
    assert result['failures'] == [
        {'stage': phase + '-container', 'type': 'RuntimeError',
         'native_status_codes': []}]
    if phase == 'create':
        remove.assert_not_called()
    else:
        remove.assert_called_once_with('b' * 64)
        assert result['owned_container_removed'] is True
