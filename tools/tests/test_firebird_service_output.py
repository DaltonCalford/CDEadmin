"""Maintenance output retention, bounded memory and native failure behavior."""

from unittest.mock import MagicMock

import pytest

from tools.cdeadmin_firebird_admin_mapping_gate import RelationalClientError
from tools import cdeadmin_firebird_repair_output_gate as output


@pytest.mark.parametrize('batches', [
    [[]], [['']], [['warning\n']], [[], ['late finding\n']],
    [['first\n'], [], ['last\n']], [['é漢字\n', 'Summary of validation errors']],
])
def test_complete_service_transcript_including_already_finished(batches):
    server = MagicMock()
    server.__iter__.side_effect = [iter(batch) for batch in batches]
    server.is_running.side_effect = [True] * (len(batches) - 1) + [False]
    lines, truncated = output.collect_service_output(server)
    assert lines == [line for batch in batches for line in batch]
    assert truncated is False
    assert server.is_running.call_count == len(batches)
    server.wait.assert_not_called()
    server._svc.start.assert_not_called()


@pytest.mark.parametrize('line_count', [0, 1, 1999, 2000, 2001, 4000])
@pytest.mark.parametrize('line_length', [0, 1, 8192, 8193])
def test_limits_do_not_stop_draining_native_output(line_count, line_length):
    consumed = []

    def records():
        for index in range(line_count):
            consumed.append(index)
            yield '漢' * line_length

    server = MagicMock()
    server.__iter__.return_value = records()
    server.is_running.return_value = False
    lines, truncated = output.collect_service_output(server)
    assert len(consumed) == line_count
    assert len(lines) <= output.MAX_LINES
    assert sum(map(len, lines)) <= output.MAX_CHARACTERS
    assert all(len(line) <= output.MAX_LINE_CHARACTERS for line in lines)
    assert truncated == (
        line_count > output.MAX_LINES or
        (line_count > 0 and line_length > output.MAX_LINE_CHARACTERS) or
        line_count * line_length > output.MAX_CHARACTERS)
    server.wait.assert_not_called()


@pytest.mark.parametrize('phase', ['read', 'running'])
def test_native_errors_propagate_without_replaying_or_claiming_success(phase):
    error = RelationalClientError('owned native failure')
    server = MagicMock()
    if phase == 'read':
        def records():
            yield 'Partial native finding'
            raise error
        server.__iter__.return_value = records()
    else:
        server.__iter__.return_value = iter(['Partial native finding'])
        server.is_running.side_effect = error
    with pytest.raises(RelationalClientError) as caught:
        output.collect_service_output(server)
    assert caught.value is error
    server._svc.start.assert_not_called()
    server.wait.assert_not_called()
