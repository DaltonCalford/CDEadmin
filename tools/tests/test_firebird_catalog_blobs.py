##########################################################################
# CDEadmin - Multi-engine Database Administration
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
##########################################################################

from unittest.mock import Mock

import pytest

from tools.cdeadmin_firebird_admin_mapping_gate import _resources  # noqa
from pgadmin.cdeadmin.providers.firebird.provider import (
    _catalog_detail, _materialize_catalog_value,
)


@pytest.mark.parametrize('field', [
    'description', 'expression_source', 'condition_source', 'metadata_source',
    'header_source', 'body_source', 'default_source', 'validation_source',
    'computed_source', 'definition',
])
def test_full_source_text_survives_reader_lifetime(field):
    text = '  ' + 'metadata é\n' * 10000 + '  '
    reader = Mock()
    reader.read.return_value = text
    assert _catalog_detail(field, reader) == text
    reader.read.assert_called_once_with()
    reader.close.assert_called_once_with()


def test_binary_catalog_blob_is_not_stringified():
    reader = Mock()
    reader.read.return_value = b'\x00\xff\x01'
    assert _materialize_catalog_value(reader) == b'\x00\xff\x01'
    reader.close.assert_called_once_with()


def test_reader_closes_when_read_fails():
    reader = Mock()
    reader.read.side_effect = OSError('read failed')
    with pytest.raises(OSError, match='read failed'):
        _materialize_catalog_value(reader)
    reader.close.assert_called_once_with()


def test_scalar_metadata_keeps_existing_normalization():
    assert _catalog_detail('owner', 'SYSDBA  ') == 'SYSDBA'
    assert _catalog_detail('description', None) is None
    assert _materialize_catalog_value(1) == 1
