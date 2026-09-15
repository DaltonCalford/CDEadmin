"""Native filter fixture buffer boundaries, cleanup and failure evidence."""

import ctypes
import subprocess
from types import SimpleNamespace

import pytest

from tools import cdeadmin_firebird_blob_filters_gate as gate
from firebird.driver.types import StateResult


def fake_connection(*, write_failure=False, read_failure=False,
                    cleanup_failure=False):
    events = []

    def put(length, data):
        events.append(('put', bytes(data[:length])))
        if write_failure:
            raise ValueError('owned write failed')

    def close(which):
        events.append((which,))
        if cleanup_failure:
            raise RuntimeError('owned cleanup failed')

    blocks = iter([b'abc', b'def', None])

    def read(_size, buffer, count):
        if read_failure:
            raise ValueError('owned read failed')
        value = next(blocks)
        if value is None:
            return StateResult.NO_DATA
        ctypes.memmove(buffer, value, len(value))
        ctypes.cast(count, ctypes.POINTER(ctypes.c_uint))[0] = len(value)
        return StateResult.SEGMENT

    writer = SimpleNamespace(put_segment=put,
                             cancel=lambda: close('cancel-writer'),
                             close=lambda: events.append(('close-writer',)))
    reader = SimpleNamespace(get_segment=read,
                             close=lambda: close('close-reader'))

    def create(_transaction, _id, bpb):
        events.append(('create', bpb))
        return writer

    def open_(_transaction, _id, bpb):
        events.append(('open', bpb))
        return reader

    transaction = SimpleNamespace(is_active=lambda: False, _tra='owned')
    return SimpleNamespace(
        main_transaction=transaction,
        begin=lambda: events.append(('begin',)),
        _att=SimpleNamespace(create_blob=create, open_blob=open_)), events


@pytest.mark.parametrize('on_write', [False, True])
def test_native_bpb_direction_and_handles_are_closed(on_write):
    connection, events = fake_connection()
    result = gate.filtered_blob(connection, [b'abc', b'def'],
                                source=-71, target=1, on_write=on_write)
    assert result == b'abcdef'
    assert events[0] == ('begin',)
    bpb = b'\x01\x01\x02\xb9\xff\x02\x02\x01\x00'
    assert ('create', bpb if on_write else None) in events
    assert ('open', None if on_write else bpb) in events
    assert events.count(('close-writer',)) == 1
    assert events.count(('close-reader',)) == 1


@pytest.mark.parametrize('phase', ['write', 'read'])
@pytest.mark.parametrize('cleanup_failure', [False, True])
def test_primary_failure_survives_cleanup_failure(phase, cleanup_failure):
    connection, events = fake_connection(
        write_failure=phase == 'write', read_failure=phase == 'read',
        cleanup_failure=cleanup_failure)
    with pytest.raises(ValueError, match=phase + ' failed') as caught:
        gate.filtered_blob(connection, [b'owned'])
    if cleanup_failure:
        assert isinstance(caught.value.__cause__, RuntimeError)
    expected = 'cancel-writer' if phase == 'write' else 'close-reader'
    assert events.count((expected,)) == 1
    if phase == 'write':
        assert not any(item[0] == 'open' for item in events)


@pytest.mark.parametrize('size', [0, -1, 65536, True, '7', None])
def test_invalid_buffer_never_opens_a_transaction_or_blob(size):
    connection, events = fake_connection()
    with pytest.raises(ValueError, match='buffer'):
        gate.filtered_blob(connection, [], buffer_size=size)
    assert events == []


def test_compiler_failure_is_recorded_without_creating_a_container(
        tmp_path, monkeypatch):
    monkeypatch.setattr(
        gate, '_configure_client_library', lambda _native: None)

    def failed(command, **_kwargs):
        raise subprocess.CalledProcessError(1, command)

    monkeypatch.setattr(gate.subprocess, 'run', failed)
    result = gate.run('owned-image', tmp_path, tmp_path / 'build')
    assert result['complete'] is False
    assert result['checks'] == []
    assert result['failures'][0]['case'] == 'compile-owned-filter'
    assert 'container_id' not in result
