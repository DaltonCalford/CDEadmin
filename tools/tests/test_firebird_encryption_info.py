"""Header names use native UTF-8 framing and preserve access-denial status."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tools.cdeadmin_firebird_admin_mapping_gate import RelationalClientError
from pgadmin.cdeadmin.providers.firebird.encryption_info import (
    INFO_FIELDS, read_encryption_text)


def response(raw):
    return SimpleNamespace(response=SimpleNamespace(raw=raw, clear=Mock()),
                           _get_data=Mock())


@pytest.mark.parametrize('field', list(INFO_FIELDS))
@pytest.mark.parametrize('name', ['', 'OWNED', '東京', '暗号', 'clé'])
def test_native_names_are_lossless_and_do_not_need_sql(field, name):
    code = INFO_FIELDS[field]
    payload = name.encode('utf-8')
    info = response(bytes([code]) + len(payload).to_bytes(2, 'little') +
                    payload + b'\x01' + bytes(1024))
    assert read_encryption_text(info, field) == name
    info.response.clear.assert_called_once()
    info._get_data.assert_called_once_with(bytes([code]))


def test_access_denial_is_not_an_empty_header_or_discarded_native_error():
    code = INFO_FIELDS['encryption_plugin']
    status = 335545094  # An example bounded native status for framing only.
    payload = bytes([code]) + status.to_bytes(4, 'little')
    info = response(b'\x03\x05\x00' + payload + b'\x01')
    with pytest.raises(RelationalClientError,
                       match='denied or unavailable') as error:
        read_encryption_text(info, 'encryption_plugin')
    assert error.value.gds_codes == (status,)


@pytest.mark.parametrize('raw', [
    b'', b'\x85', b'\x85\x00\x00', b'\x85\xff\xff\x01',
    b'\x85\x01\x00X', b'\x85\x01\x00X\x02',
    b'\x8a\x00\x00\x01', b'\x02\x00\x00\x01',
    b'\x85\x01\x00\xff\x01',
])
def test_malformed_wrong_tag_and_invalid_utf8_are_never_lossy_strings(raw):
    with pytest.raises(RelationalClientError):
        read_encryption_text(response(raw), 'encryption_key_name')


@pytest.mark.parametrize('payload', [
    b'', b'\x85', b'\x86\x01\x00\x00\x00',
    b'\x85\x00\x00\x00\x00', b'\x85\xff\xff\xff\xff',
])
def test_malformed_error_response_never_invents_a_native_status(payload):
    raw = b'\x03' + len(payload).to_bytes(2, 'little') + payload + b'\x01'
    with pytest.raises(RelationalClientError) as error:
        read_encryption_text(response(raw), 'encryption_key_name')
    assert not getattr(error.value, 'gds_codes', ())


def test_unknown_field_never_issues_info_request():
    info = Mock()
    with pytest.raises(ValueError):
        read_encryption_text(info, 'key_material')
    assert not info.mock_calls


def test_native_acquisition_failure_is_not_replayed_or_swallowed():
    info = response(b'')
    info._get_data.side_effect = RuntimeError('owned acquisition error')
    with pytest.raises(RuntimeError, match='owned acquisition error'):
        read_encryption_text(info, 'encryption_plugin')
    info._get_data.assert_called_once()
