"""Catalog helpers share a request-local dialect, including failure cleanup."""
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tools.cdeadmin_firebird_admin_mapping_gate import _resources
from pgadmin.cdeadmin.providers.firebird.ddl_dialect import (
    generated_dialect, identifier_sql,
)


def catalog(dialect, *, interrupt=False, close_failure=False):
    cursor = Mock()
    observed = []

    def execute(source):
        if 'FROM RDB$COLLATIONS' in source:
            observed.append(identifier_sql('T'))
            if interrupt:
                raise KeyboardInterrupt('owned catalog interruption')
    cursor.execute.side_effect = execute
    cursor.fetchall.return_value = []
    if close_failure:
        cursor.close.side_effect = RuntimeError('owned cursor close failure')
    connection = SimpleNamespace(cursor=lambda: cursor,
                                 info=SimpleNamespace(sql_dialect=dialect))
    return connection, cursor, observed


@pytest.mark.parametrize('dialect', [1, 3])
def test_all_catalog_helpers_see_observed_dialect(dialect):
    connection, cursor, observed = catalog(dialect)
    _resources(connection, {'route': {'database': 'fixture'}})
    assert observed
    assert set(observed) == {'T' if dialect == 1 else '"T"'}
    assert identifier_sql('T') == '"T"'
    cursor.close.assert_called_once()


@pytest.mark.parametrize('interrupt,close_failure,error', [
    (True, False, KeyboardInterrupt), (False, True, RuntimeError),
    (True, True, RuntimeError),
])
def test_dialect_is_reset_after_catalog_and_cleanup_failures(
        interrupt, close_failure, error):
    connection, cursor, _ = catalog(1, interrupt=interrupt,
                                    close_failure=close_failure)
    with pytest.raises(error):
        _resources(connection, {'route': {'database': 'fixture'}})
    assert identifier_sql('T') == '"T"'
    cursor.close.assert_called_once()


def test_simultaneous_catalogs_do_not_share_rendering_state():
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(test_all_catalog_helpers_see_observed_dialect,
                      [1, 3] * 5))


@pytest.mark.parametrize('dialect', [1, 3])
@pytest.mark.parametrize('name', ['MixedCase', "A'B", 'A;B', '東京'])
def test_external_collation_names_are_literals_not_identifiers(dialect, name):
    from pgadmin.cdeadmin.providers.firebird import character_metadata as chars
    with generated_dialect(dialect):
        compiled = chars.compile_operation('collation', 'create', {
            'name': 'C', 'character_set': 'UTF8', 'source_mode': 'EXTERNAL',
            'external_name': name})
        recreated = chars.recreation('collation', 'C', {
            'character_set': 'UTF8', 'attributes': 0, 'base_collation': name})
    literal = "FROM EXTERNAL ('" + name.replace("'", "''") + "')"
    assert literal in compiled[0]
    assert literal in recreated[0]
