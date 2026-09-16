"""Recreation retains view column identity instead of inferring query names."""
import pytest

from tools.cdeadmin_firebird_admin_mapping_gate import ADMINISTRATION  # noqa
from pgadmin.cdeadmin.providers.firebird.views import recreation_sql
from pgadmin.cdeadmin.sdk.relational import RelationalClientError


@pytest.mark.parametrize('definition', [
    'SELECT 1, 2 FROM RDB$DATABASE',
    'WITH Q AS (SELECT 1 X FROM RDB$DATABASE) SELECT X, X+1 FROM Q',
    'SELECT A, B FROM T WHERE A > 0 WITH CHECK OPTION',
    "SELECT ';', 'quoted' FROM RDB$DATABASE -- trailing comment",
])
def test_ordered_aliases_and_exact_source(definition):
    assert recreation_sql('V"東京', definition, [
        {'name': 'A"東京', 'position': 1}, {'name': 'B', 'position': 0},
    ]) == 'CREATE VIEW "V""東京" ("B", "A""東京") AS\n' + definition + '\n;'


@pytest.mark.parametrize('columns', [
    None, [], 'columns', [None], [{'name': 'A'}],
    [{'name': 'A', 'position': True}],
    [{'name': 'A', 'position': '0.0'}],
    [{'name': 'A', 'position': 1}],
    [{'name': 'A', 'position': 0}, {'name': 'B', 'position': 2}],
    [{'name': 'A', 'position': 0}, {'name': 'B', 'position': 0}],
    [{'name': 'A', 'position': 0}, {'name': 'A', 'position': 1}],
    [{'name': '', 'position': 0}],
])
def test_does_not_fabricate_ddl_from_missing_or_invalid_columns(columns):
    with pytest.raises(RelationalClientError):
        recreation_sql('V', 'SELECT 1 FROM RDB$DATABASE', columns)


@pytest.mark.parametrize('definition', [None, '', 12])
def test_missing_source_is_not_stringified_into_sql(definition):
    with pytest.raises(RelationalClientError):
        recreation_sql('V', definition, [{'name': 'A', 'position': 0}])


def test_text_positions_are_sorted_numerically_without_mutating_input():
    from pgadmin.cdeadmin.providers.firebird.views import catalog_columns
    columns = [{'name': 'C' + str(i), 'position': str(i)}
               for i in reversed(range(12))]
    ordered = catalog_columns(columns)
    assert [c['name'] for c in ordered] == ['C' + str(i) for i in range(12)]
    assert columns[0]['position'] == '11'
