"""Keep the stored-dialect matrix exhaustive over declared scalar types."""
import pytest

from tools.cdeadmin_firebird_legacy_editor_gate import (
    type_cases, native_oracle_type,
)
from pgadmin.cdeadmin.providers.firebird import columns


def test_every_declared_type_has_an_independent_native_fixture():
    cases = type_cases()
    assert {draft['data_type'] for _, draft, _ in cases} == set(columns.TYPES)
    assert len(cases) == 30
    assert len({label for label, _, _ in cases}) == len(cases)


@pytest.mark.parametrize('label,draft,native_sql', type_cases())
def test_compiler_matches_independently_spelled_native_fixture(
        label, draft, native_sql):
    assert columns.data_type(draft) == native_sql, label


def test_both_sides_of_native_precision_storage_transitions_are_included():
    cases = type_cases()
    for kind in ('NUMERIC', 'DECIMAL'):
        assert {draft['precision'] for _, draft, _ in cases
                if draft['data_type'] == kind} == {9, 18, 38}
    for kind in ('TIME', 'TIMESTAMP'):
        assert {draft['time_zone'] for _, draft, _ in cases
                if draft['data_type'] == kind} == {
                    'WITH TIME ZONE', 'WITHOUT TIME ZONE'}


def test_native_baseline_contains_no_quoted_identifiers():
    for label, _, definition in type_cases():
        assert '"' not in native_oracle_type(label, definition)
    assert native_oracle_type('DOMAIN', '"D"') == 'D'
