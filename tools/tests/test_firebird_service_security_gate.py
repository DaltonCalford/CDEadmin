"""Owned alternate-security fixture addressing, archives and cleanup scope."""

import io
import tarfile
from unittest.mock import patch

import pytest

from tools import cdeadmin_firebird_service_security_gate as gate


@pytest.mark.parametrize('value', [
    '0.0.0.0:53050', '[::]:53050', '127.0.0.1:0', '127.0.0.1:65536',
    '127.0.0.1:53050\n[::]:53050', '',
])
def test_published_port_rejects_nonlocal_or_invalid_listeners(value):
    with patch.object(gate, 'docker', return_value=value.encode()):
        with pytest.raises(RuntimeError):
            gate.published_port('owned')


def test_port_is_resolved_again_after_container_restart():
    with patch.object(gate, 'docker', side_effect=[
            b'127.0.0.1:50100\n', b'127.0.0.1:50101\n']) as docker:
        assert gate.published_port('owned') == 50100
        assert gate.published_port('owned') == 50101
        assert docker.call_count == 2


@pytest.mark.parametrize('name', ['../secret', '/secret', '.', '..'])
def test_archive_write_rejects_path_components(name):
    with patch.object(gate, 'docker') as docker:
        with pytest.raises(ValueError):
            gate.write_container_file('owned', '/data', name, b'', 84, 84,
                                      0o600)
        docker.assert_not_called()


def test_offline_file_copy_preserves_only_requested_file_and_ownership():
    with patch.object(gate, 'docker') as docker:
        gate.write_container_file('owned', '/data', 'alternate.fdb',
                                  b'owned-file', 84, 84, 0o600)
    args, kwargs = docker.call_args
    assert args == ('cp', '-a', '-', 'owned:/data')
    with tarfile.open(fileobj=io.BytesIO(kwargs['input_data'])) as archive:
        members = archive.getmembers()
        assert len(members) == 1
        item = members[0]
        assert (item.name, item.uid, item.gid, item.mode) == (
            'alternate.fdb', 84, 84, 0o600)
        assert item.mtime > 0
        assert archive.extractfile(item).read() == b'owned-file'


def test_cleanup_requires_exact_owned_container_id_and_label():
    with patch.object(gate, 'docker') as docker:
        with pytest.raises(ValueError):
            gate.remove_owned_container('cdeadmin-demo-firebird')
        docker.assert_not_called()
    with patch.object(gate, 'docker', return_value=b'other-owner') as docker:
        with pytest.raises(RuntimeError):
            gate.remove_owned_container('a' * 64)
        assert docker.call_count == 1
        assert docker.call_args.args[0] == 'inspect'
    with patch.object(gate, 'docker', return_value=(
            b'firebird-service-security\n')) as docker:
        gate.remove_owned_container('b' * 64)
        assert docker.call_args.args == (
            'rm', '--force', '--volumes', 'b' * 64)
