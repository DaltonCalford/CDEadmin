"""Native output metadata must preserve every column and its exact type."""

from enum import IntEnum
from types import SimpleNamespace

import pytest

from tools.tests.test_firebird_query_values import normalize_value  # noqa: F401
from pgadmin.cdeadmin.providers.firebird.query_columns import describe_columns
from pgadmin.cdeadmin.sdk.relational import RelationalClientError


def cursor(labels, native=None):
    return SimpleNamespace(
        description=[(label, None, 0, 16, 0, 0, True) for label in labels],
        statement=SimpleNamespace(_out_desc=native))


def metadata(code, subtype=0, charset=0):
    datatype = IntEnum('NativeType', {code: 100})[code]
    return SimpleNamespace(
        datatype=datatype, subtype=subtype, length=16, scale=0,
        charset=charset, nullable=True, relation='NATIVE_TABLE',
        field='NATIVE_FIELD', owner='NATIVE_OWNER')


@pytest.mark.parametrize('code,subtype,charset,expected,cell_type', [
    ('INT128', 0, 0, 'INT128', 'text'),
    ('INT128', 1, 0, 'NUMERIC', 'text'),
    ('INT64', 2, 0, 'DECIMAL', 'text'),
    ('DEC34', 0, 0, 'DECFLOAT(34)', 'text'),
    ('DEC16', 0, 0, 'DECFLOAT(16)', 'text'),
    ('TIMESTAMP_TZ', 0, 0, 'TIMESTAMP WITH TIME ZONE', 'text'),
    ('TIME_TZ', 0, 0, 'TIME WITH TIME ZONE', 'text'),
    ('ARRAY', 0, 0, 'ARRAY', 'json'),
    ('BLOB', 1, 4, 'BLOB', 'text'),
    ('BLOB', 0, 0, 'BLOB', 'json'),
    ('BLOB', 42, 0, 'BLOB', 'json'),
    ('VARYING', 0, 1, 'VARCHAR CHARACTER SET OCTETS', 'json'),
    ('TEXT', 0, 1, 'CHAR CHARACTER SET OCTETS', 'json'),
    ('BOOLEAN', 0, 0, 'BOOLEAN', 'boolean'),
    ('UNKNOWN_FUTURE_TYPE', 0, 0, None, 'text'),
])
def test_native_types_are_not_guessed_from_python_values(
        code, subtype, charset, expected, cell_type):
    columns = describe_columns(cursor(['ALIAS'], [
        metadata(code, subtype, charset)]))
    column = columns[0]
    assert column['name'] == column['native_name'] == 'ALIAS'
    assert column['native_type'] == expected
    assert column['native_type_name'] == code
    assert column['cell_type'] == cell_type
    assert column['source_field'] == 'NATIVE_FIELD'
    assert column['source_relation'] == 'NATIVE_TABLE'
    assert column['precision'] is None
    assert column['metadata_source'] == 'firebird-driver.IMessageMetadata'


def test_duplicate_and_whitespace_labels_remain_unique_before_row_mapping():
    columns = describe_columns(cursor(['X', 'X', 'X#2', ' X ', '']))
    assert [column['name'] for column in columns] == [
        'X', 'X#3', 'X#2', 'X#4', 'column_5']
    assert [column['native_name'] for column in columns] == [
        'X', 'X', 'X#2', ' X ', '']
    assert len(dict(zip([item['name'] for item in columns], range(5)))) == 5


def test_driver_without_internal_metadata_keeps_public_description():
    column = describe_columns(cursor(['PUBLIC']))[0]
    assert column['metadata_source'] == 'firebird-driver.DB-API-description'
    assert 'native_type_code' not in column


def test_mismatched_internal_descriptor_is_rejected():
    with pytest.raises(RelationalClientError, match='column count'):
        describe_columns(cursor(['ONE'], []))


def test_no_result_columns_are_not_fabricated():
    assert describe_columns(cursor([], [])) == []
