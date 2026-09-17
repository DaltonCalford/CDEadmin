"""The legacy view matrix keeps its explicit text scope and query shapes."""
from tools.cdeadmin_firebird_legacy_views_gate import cases


def test_query_shapes_have_distinct_native_expected_results():
    samples = cases()
    assert [label for label, _, _ in samples] == [
        'ordered_aliases', 'cte', 'literal_comment', 'check_option']
    assert [rows for _, _, rows in samples] == [
        [(1, 2)], [(7, 8)], [('A"B', 'tail')], [(5, 6)]]


def test_text_is_explicitly_typed_and_check_option_uses_real_columns():
    samples = {label: source for label, source, _ in cases()}
    assert samples['literal_comment'].count('AS VARCHAR(20)') == 2
    assert samples['literal_comment'].endswith('-- trailing comment')
    assert samples['check_option'] == (
        'SELECT X, Y FROM T WHERE X > 0 WITH CHECK OPTION')
