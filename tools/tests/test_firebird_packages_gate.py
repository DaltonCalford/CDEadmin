"""Lifecycle qualification failures retain evidence and owned cleanup."""

import json

import pytest

from tools import cdeadmin_firebird_packages_gate
from tools import cdeadmin_firebird_sequences_gate
from tools import cdeadmin_firebird_shadows_gate


@pytest.fixture(params=[cdeadmin_firebird_packages_gate,
                        cdeadmin_firebird_sequences_gate,
                        cdeadmin_firebird_shadows_gate])
def gate(request):
    return request.param


@pytest.mark.parametrize('phase', ['create', 'start', 'port'])
@pytest.mark.parametrize('cleanup_failure', [False, True])
def test_fixture_failure_is_collected_and_only_owned_identity_is_removed(
        tmp_path, monkeypatch, phase, cleanup_failure, gate):
    monkeypatch.setattr(gate, '_configure_client_library',
                        lambda _native: None)
    removed = []
    secret = 'not-a-real-password-owned-test'
    monkeypatch.setattr(gate.secrets, 'token_urlsafe', lambda _length: secret)

    def docker(command, *args, **kwargs):
        assert secret not in args
        if command == 'create':
            assert kwargs['env']['FIREBIRD_ROOT_PASSWORD'] == secret
            if phase == 'create':
                raise RuntimeError('owned create failure')
            return ('a' * 64).encode()
        assert command == 'start' and args == ('a' * 64,)
        if phase == 'start':
            raise RuntimeError('owned start failure')
        return b''

    def port(_container):
        raise RuntimeError('owned port failure')

    def cleanup(container):
        removed.append(container)
        if cleanup_failure:
            raise RuntimeError('owned cleanup failure')

    monkeypatch.setattr(gate, 'docker', docker)
    monkeypatch.setattr(gate, 'published_port', port)
    monkeypatch.setattr(gate, 'remove_owned', cleanup)
    result = gate.run('owned-image', tmp_path / 'build')
    assert not result['complete']
    assert not result['checks']
    assert secret not in json.dumps(result)
    assert removed == ([] if phase == 'create' else ['a' * 64])
    assert result['owned_container_removed'] is (
        phase != 'create' and not cleanup_failure)
    assert len(result['failures']) == (
        2 if phase != 'create' and cleanup_failure else 1)


def test_existing_evidence_directory_is_not_reused(
        tmp_path, monkeypatch, gate):
    monkeypatch.setattr(gate, '_configure_client_library',
                        lambda _native: None)
    monkeypatch.setattr(gate, 'docker', lambda *_args, **_kwargs:
                        pytest.fail('must not create a fixture'))
    with pytest.raises(FileExistsError):
        gate.run('owned-image', tmp_path)
