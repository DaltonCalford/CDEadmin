"""Owned browser fixtures must not touch existing data or export secrets."""
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tools import cdeadmin_firebird_backup_restore_ui_matrix as gate


CID = 'a' * 64


@pytest.mark.parametrize('name', ['', '-name', 'a/b', 'a;true', 'a\n'])
def test_container_names_rejected_before_docker(monkeypatch, name):
    run = Mock()
    monkeypatch.setattr(gate.subprocess, 'run', run)
    with pytest.raises(ValueError):
        gate.resolve_container(name)
    run.assert_not_called()


@pytest.mark.parametrize('code,identity', [(1, CID), (0, 'short'), (0, '')])
def test_container_identity_must_be_resolved(monkeypatch, code, identity):
    response = SimpleNamespace(returncode=code, stdout=identity.encode())
    monkeypatch.setattr(gate.subprocess, 'run', Mock(return_value=response))
    with pytest.raises(RuntimeError):
        gate.resolve_container('demo')


@pytest.mark.parametrize('change', [
    {'host': 'remote'}, {'database': 'alias'}, {'database': None},
    {'database': '/data/a\n'}, {'port': True}, {'port': 0},
    {'port': 65536}, {'port': '53050'}])
def test_invalid_profile_has_no_docker_operations(monkeypatch, change):
    docker = Mock()
    monkeypatch.setattr(gate, 'docker', docker)
    profile = {'host': 'localhost', 'database': '/data/demo.fdb',
               'port': 53050, **change}
    with pytest.raises(ValueError):
        gate.validate_profile(profile, CID)
    docker.assert_not_called()


@pytest.mark.parametrize('published,valid', [
    ('127.0.0.1:53050\n', True), ('0.0.0.0:53050\n', False),
    ('127.0.0.1:3050\n', False)])
def test_port_binding_matches_owned_endpoint(monkeypatch, published, valid):
    docker = Mock(return_value=SimpleNamespace(stdout=published.encode()))
    monkeypatch.setattr(gate, 'docker', docker)
    profile = {'host': 'localhost', 'database': '/data/demo.fdb',
               'port': 53050}
    if valid:
        gate.validate_profile(profile, CID)
    else:
        with pytest.raises(ValueError):
            gate.validate_profile(profile, CID)
    docker.assert_called_once_with(CID, 'port', '3050/tcp')


@pytest.mark.parametrize('failure', [
    None, 'exists', 'create', 'browser', 'target', 'configuration', 'drop'])
@pytest.mark.parametrize('gate_kind', ['backup-history', 'logical-volumes'])
def test_fixture_lifecycle_and_failure_barriers(
        tmp_path, monkeypatch, failure, gate_kind):
    options = SimpleNamespace(container=CID, output_root=tmp_path,
                              source_config_db=tmp_path / 'source.db',
                              desktop_user='test@example.invalid',
                              client_library=tmp_path / 'libfbclient.so',
                              profiles=tmp_path / 'profiles.json',
                              timeout=30, browser_binary=None,
                              gate_kind=gate_kind)
    profile = {'database': '/data/sample.fdb', 'port': 53050}
    present = set()
    calls = []
    created = []

    def docker(container, *args, absent_ok=False):
        assert container == CID
        calls.append(args)
        candidate = args[-1]
        if args[:3] == ('exec', 'rm', '-f'):
            assert candidate.endswith(
                '.fbk' if gate_kind == 'logical-volumes' else '.nbk')
            present.discard(candidate)
            return SimpleNamespace(returncode=0)
        exists = candidate in present
        if failure == 'exists' and not created:
            exists = True
        expected = not exists if '!' in args else exists
        if not expected and not absent_ok:
            raise RuntimeError('test fixture operation failed')
        return SimpleNamespace(returncode=0 if expected else 1)

    def connection(native, route, password, path, create=False):
        assert password == 'DO-NOT-EXPORT'
        assert path != profile['database']
        assert create
        present.add(path)
        created.append(path)
        if failure == 'create':
            raise RuntimeError('DO-NOT-EXPORT')
        return SimpleNamespace(info=SimpleNamespace(firebird_version='5.0.4'),
                               close=Mock())

    def run(command, **kwargs):
        assert 'DO-NOT-EXPORT' not in ' '.join(command)
        assert kwargs['env']['CDEADMIN_FIREBIRD_DEMO_PASSWORD'] == (
            'DO-NOT-EXPORT')
        path = command[command.index('--database') + 1]
        present.add(path + '.RESTORED.fdb')
        present.add(path + '.RESTORED.PRESERVE.fdb')
        assert command[command.index('--gate-kind') + 1] == gate_kind
        present.update(
            [path + '.single.fbk', *[
                path + f'.part-{part}.fbk' for part in range(1, 4)]]
            if gate_kind == 'logical-volumes' else
            [path + '.' + name + '.nbk' for name in ('ROWS', 'DAYS', 'GUID')])
        output = command[command.index('--output') + 1]
        gate.Path(output).write_text(json.dumps({
            'complete': True, 'source_config_unchanged':
            failure != 'configuration', 'target_database':
            'wrong' if failure == 'target' else path}))
        return SimpleNamespace(returncode=1 if failure == 'browser' else 0)

    def drop(native, route, password, path):
        if failure == 'drop':
            raise RuntimeError('DO-NOT-EXPORT')
        present.remove(path)

    monkeypatch.setattr(gate, 'docker', docker)
    monkeypatch.setattr(gate, 'database_connection', connection)
    monkeypatch.setattr(gate, 'drop_owned_database', drop)
    monkeypatch.setattr(gate.subprocess, 'run', run)
    result = gate.run_scale(options, object(), profile, 'DO-NOT-EXPORT', 100)
    assert result['complete'] == (failure is None)
    assert 'DO-NOT-EXPORT' not in json.dumps(result)
    assert json.loads((tmp_path / 'scale-100/result.json').read_text()) == (
        result)
    if failure == 'exists':
        assert not created
        assert len(calls) == 1
    elif failure == 'drop':
        assert len(present) == (7 if gate_kind == 'logical-volumes' else 6)
        assert not any(args[:3] == ('exec', 'rm', '-f') for args in calls)
        assert result['failures'][0]['stage'] == 'cleanup'
    else:
        assert not present
        assert len(result['backup_files_absent']) == (
            4 if gate_kind == 'logical-volumes' else 3)
        assert len(result['databases_removed']) == (
            1 if failure == 'create' else 3)


def test_existing_evidence_is_never_overwritten(tmp_path):
    (tmp_path / 'scale-100').mkdir()
    options = SimpleNamespace(container=CID, output_root=tmp_path)
    with pytest.raises(FileExistsError):
        gate.run_scale(options, None, {}, 'secret', 100)


def test_failed_drop_closes_attachment_without_unlink(monkeypatch):
    connection = Mock()
    connection.drop_database.side_effect = RuntimeError('drop failed')
    monkeypatch.setattr(gate, 'database_connection', Mock(
        return_value=connection))
    docker = Mock()
    monkeypatch.setattr(gate, 'docker', docker)
    with pytest.raises(RuntimeError, match='drop failed'):
        gate.drop_owned_database(None, {}, 'secret', '/owned/test.fdb')
    connection.close.assert_called_once_with()
    docker.assert_not_called()
