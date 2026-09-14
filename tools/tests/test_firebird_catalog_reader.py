##########################################################################
# CDEadmin - Multi-engine Database Administration
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
##########################################################################

import json
from unittest.mock import Mock

import pytest

from tools.cdeadmin_firebird_admin_mapping_gate import ADMINISTRATION  # noqa
from pgadmin.cdeadmin.providers.firebird.catalog_reader import CatalogReader
from pgadmin.cdeadmin.sdk.relational import RelationalClientError


@pytest.mark.parametrize('rows', [[], [(1, 'text')], [(None,)], [(1,), (2,)]])
def test_successful_empty_is_distinct_from_unavailable(rows):
    cursor = Mock()
    cursor.fetchall.return_value = rows
    reader = CatalogReader(cursor, lambda value: value)
    assert reader.rows('native catalog', 'columns') == rows
    assert reader.observations == [{
        'section': 'columns', 'required_for_structural_catalog': True,
        'visibility': 'current_attachment', 'available': True,
        'row_count': len(rows)}]
    assert reader.warnings == []


@pytest.mark.parametrize('stage', ['execute', 'fetch', 'materialize'])
@pytest.mark.parametrize('required', [False, True])
def test_failed_reads_never_claim_empty_or_leak_query_and_arguments(
        stage, required):
    error = RuntimeError('private principal or provider message')
    error.gds_codes = (335544352,)
    cursor = Mock()
    cursor.fetchall.return_value = [(1,), (2,)]
    materialize = Mock(side_effect=lambda value: value)
    if stage == 'execute':
        cursor.execute.side_effect = error
    elif stage == 'fetch':
        cursor.fetchall.side_effect = error
    else:
        materialize.side_effect = [1, error]
    reader = CatalogReader(cursor, materialize)
    if required:
        with pytest.raises(RelationalClientError, match='335544352') as caught:
            reader.rows('private native SQL', 'columns', required=required)
        assert 'private' not in str(caught.value)
    else:
        assert reader.rows('private native SQL', 'users', required=False) == []
    observation = reader.observations[0]
    assert observation['available'] is False
    assert observation['row_count'] is None
    assert observation['native_status_codes'] == [335544352]
    assert 'private' not in json.dumps(reader.observations)
    assert 'unknown, not absent' in reader.warnings[0]


def test_optional_failure_does_not_prevent_subsequent_successful_read():
    cursor = Mock()
    cursor.execute.side_effect = [RuntimeError('unavailable'), None]
    cursor.fetchall.return_value = [(1,)]
    reader = CatalogReader(cursor, lambda value: value)
    assert reader.rows('query-one', 'users', required=False) == []
    assert reader.rows('query-two', 'columns') == [(1,)]
    assert [item['available'] for item in reader.observations] == [False, True]
