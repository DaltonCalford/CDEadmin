"""Exact native value representation and native BLOB ownership."""

from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
import json
from unittest.mock import Mock

import pytest

from tools.tests.test_firebird_async_queries import rig  # noqa: F401
from pgadmin.cdeadmin.providers.firebird.query_values import normalize_value
from pgadmin.cdeadmin.sdk.relational import RelationalClientError


@pytest.mark.parametrize('value,expected', [
    (None, None), (True, True), (False, False), ('é\x00text', 'é\x00text'),
    (0, 0), (2 ** 53 - 1, 2 ** 53 - 1),
    (-(2 ** 53 - 1), -(2 ** 53 - 1)),
    (2 ** 53, '9007199254740992'), (-(2 ** 53), '-9007199254740992'),
    (2 ** 127 - 1, '170141183460469231731687303715884105727'),
    (Decimal('12.3400'), '12.3400'), (Decimal('NaN'), 'NaN'),
    (Decimal('-Infinity'), '-Infinity'), (1.25, 1.25),
    (float('nan'), 'nan'), (float('inf'), 'inf'),
    (date(2026, 9, 14), '2026-09-14'),
    (time(12, 34, 56, 123400), '12:34:56.123400'),
    (datetime(2026, 9, 14, 12, 34, 56, 123400),
     '2026-09-14 12:34:56.123400'),
    (time(12, 34, tzinfo=timezone(timedelta(hours=2))),
     '12:34:00+02:00'),
    (datetime(2026, 9, 14, tzinfo=timezone.utc),
     '2026-09-14 00:00:00+00:00'),
    (b'\x00\xff\x7f',
     {'encoding': 'base64', 'data': 'AP9/', 'byte_length': 3}),
    (bytearray(), {'encoding': 'base64', 'data': '', 'byte_length': 0}),
    (memoryview(b'x'),
     {'encoding': 'base64', 'data': 'eA==', 'byte_length': 1}),
    ([[Decimal('1.00'), None], (2 ** 63, True)],
     [['1.00', None], ['9223372036854775808', True]]),
])
def test_lossless_strict_json_representation(value, expected):
    result = normalize_value(value)
    assert result == expected
    assert json.loads(json.dumps(result, allow_nan=False)) == expected


class OwnedBlob:
    def __init__(self, value):
        self.read = Mock(return_value=value)
        self.close = Mock()


@pytest.mark.parametrize('value', ['λ' * 100000, b'\x00\xff' * 100000])
def test_blob_is_materialized_before_it_is_closed(value):
    blob = OwnedBlob(value)
    assert normalize_value(blob, OwnedBlob) == normalize_value(value)
    blob.read.assert_called_once_with()
    blob.close.assert_called_once_with()


def test_blob_read_failure_still_closes_its_native_handle():
    blob = OwnedBlob(None)
    blob.read.side_effect = RuntimeError('owned read failure')
    with pytest.raises(RuntimeError, match='owned read failure'):
        normalize_value(blob, OwnedBlob)
    blob.close.assert_called_once_with()


def test_blob_close_failure_is_not_hidden_by_successful_read():
    blob = OwnedBlob('value')
    blob.close.side_effect = RuntimeError('owned close failure')
    with pytest.raises(RuntimeError, match='owned close failure'):
        normalize_value(blob, OwnedBlob)


def test_unknown_objects_are_not_serialized_as_private_repr():
    class PrivateValue:
        def __repr__(self):
            return 'private-canary'
    with pytest.raises(RelationalClientError) as error:
        normalize_value(PrivateValue())
    assert 'PrivateValue' in str(error.value)
    assert 'private-canary' not in str(error.value)


def test_query_materializes_values_before_describing_and_closing_cursor(rig):
    from dataclasses import replace
    rig.client.config = replace(
        rig.client.config,
        query_value_normalizer=lambda value: normalize_value(value, OwnedBlob))
    blob = OwnedBlob('λ' * 100000)
    rig.cursor.fetchall.return_value = [(blob,)]
    rig.finish.set()
    token = rig.client.execute(rig.handle, {'source': 'SELECT OWNED'})
    blob.close.assert_called_once_with()
    rig.cursor.close.assert_not_called()
    result = rig.client.describe_result(token)
    assert result['payload']['rows'] == [('λ' * 100000,)]
    rig.cursor.close.assert_called_once_with()


def test_invalid_normalizer_is_rejected_before_connection(rig):
    from dataclasses import replace
    with pytest.raises(RelationalClientError, match='must be callable'):
        replace(rig.client.config, query_value_normalizer=True)
