"""Owned UDF gates preserve credentials and collect scale failures."""

import io
import json
import stat
import subprocess
import tarfile
from types import SimpleNamespace

import pytest

from tools import cdeadmin_firebird_external_functions_gate as gate


def options(tmp_path):
    return SimpleNamespace(font_scale=None,
                           source_config_db=tmp_path / 'source',
                           desktop_user='owned@example.invalid')


def route():
    return {'host': '127.0.0.1', 'port': 54321, 'user': 'SYSDBA',
            'database': '/var/lib/firebird/data/owned_udf.fdb'}


@pytest.mark.parametrize('failed_scale', [None, 100, 200, 300])
@pytest.mark.parametrize('kind', ['external-functions', 'blob-filters',
                                  'object-privileges', 'packages', 'sequences'])
def test_private_credentials_cleanup_and_all_scale_failure_collection(
        tmp_path, monkeypatch, failed_scale, kind):
    monkeypatch.setenv('CDEADMIN_FIREBIRD_CLIENT_LIBRARY',
                       '/owned/libfbclient.so')
    password = 'owned-test-secret-not-a-real-credential'
    calls = []

    def execute(command, **kwargs):
        assert password not in ' '.join(command)
        profile = tmp_path / 'private-browser-profile.json'
        assert stat.S_IMODE(profile.stat().st_mode) == 0o600
        document = json.loads(profile.read_text())
        assert document['profiles'][0]['password'] == password
        assert document['profiles'][0]['owned_container_id'] == 'a' * 64
        assert document['profiles'][0]['fixture_kind'] == kind + '-owned-test'
        assert command[command.index('--gate-kind') + 1] == kind
        assert kwargs['env']['CDEADMIN_FIREBIRD_DEMO_PASSWORD'] == password
        scale = int(command[command.index('--font-scale') + 1])
        calls.append(scale)
        output = tmp_path / ('browser-' + str(scale)) / 'result.json'
        output.write_text(json.dumps({'complete': scale != failed_scale,
                                      'source_config_unchanged': True,
                                      'target_database': route()['database']}))
        return SimpleNamespace(returncode=1 if scale == failed_scale else 0)

    monkeypatch.setattr(gate.subprocess, 'run', execute)
    result = gate.browser_checks(options(tmp_path), route(), password,
                                 'a' * 64, tmp_path, gate_kind=kind,
                                 fixture_kind=kind + '-owned-test')
    assert calls == [100, 200, 300]
    assert [item['scale'] for item in result if not item['passed']] == (
        [] if failed_scale is None else [failed_scale])
    assert not (tmp_path / 'private-browser-profile.json').exists()
    assert password not in json.dumps(result)


def test_launch_failures_are_collected_and_private_profile_removed(
        tmp_path, monkeypatch):
    monkeypatch.setenv('CDEADMIN_FIREBIRD_CLIENT_LIBRARY',
                       '/owned/libfbclient.so')

    def failure(*_args, **_kwargs):
        raise OSError('Owned browser launch failure')

    monkeypatch.setattr(gate.subprocess, 'run', failure)
    result = gate.browser_checks(options(tmp_path), route(), 'owned',
                                 'a' * 64, tmp_path)
    assert [item['scale'] for item in result] == [100, 200, 300]
    assert all(not item['passed'] and item['error_type'] == 'OSError'
               for item in result)
    assert not (tmp_path / 'private-browser-profile.json').exists()


def test_existing_private_profile_is_never_overwritten_or_deleted(tmp_path):
    profile = tmp_path / 'private-browser-profile.json'
    profile.write_text('pre-existing artifact')
    with pytest.raises(FileExistsError):
        gate.browser_checks(options(tmp_path), route(), 'owned', 'a' * 64,
                            tmp_path)
    assert profile.read_text() == 'pre-existing artifact'


def test_native_build_failure_produces_structured_evidence_without_container(
        tmp_path, monkeypatch):
    monkeypatch.setattr(gate, '_configure_client_library',
                        lambda _native: None)

    def failure(command, **_kwargs):
        raise subprocess.CalledProcessError(1, command)

    monkeypatch.setattr(gate.subprocess, 'run', failure)
    result = gate.run('owned-image', tmp_path, tmp_path / 'build')
    assert result['complete'] is False
    assert result['checks'] == []
    assert result['failures'][0]['case'] == 'compile-owned-library'
    assert result['failures'][0]['error_type'] == 'CalledProcessError'
    assert 'container_id' not in result


def test_owned_archive_creates_required_library_directory():
    with tarfile.open(fileobj=io.BytesIO(gate.archive_files({
            'UDF/libowned.so': b'owned-library'}))) as archive:
        assert archive.getmember('UDF').isdir()
        library = archive.getmember('UDF/libowned.so')
        assert library.mode == 0o755
        assert archive.extractfile(library).read() == b'owned-library'
