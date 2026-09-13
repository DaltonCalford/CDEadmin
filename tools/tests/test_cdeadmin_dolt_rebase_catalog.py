"""Dolt conditional catalog visibility and error propagation regressions."""
from unittest.mock import Mock

from test_cdeadmin_actual_engine_pilots import DOLT  # noqa: F401
from pgadmin.cdeadmin.providers.dolt.provider import _rebase_plan


def test_absent_plan_restores_hidden_system_tables():
    cursor = Mock()
    cursor.fetchone.return_value = (0,)
    cursor.fetchall.return_value = []
    assert _rebase_plan(cursor) == (False, [])
    assert cursor.execute.call_args.args == (
        'SET @@SESSION.dolt_show_system_tables = 0',)
    assert not any('FROM dolt_rebase' in c.args[0]
                   for c in cursor.execute.call_args_list)


def test_active_plan_preserves_existing_visibility():
    cursor = Mock()
    cursor.fetchone.return_value = (1,)
    rows = [(1, 'pick', 'abc', 'QA')]
    cursor.fetchall.side_effect = [[('dolt_rebase',)], rows]
    assert _rebase_plan(cursor) == (True, rows)
    assert cursor.execute.call_args_list[-2].args == (
        'SET @@SESSION.dolt_show_system_tables = 1',)


def test_catalog_denial_is_not_reported_as_absent():
    cursor = Mock()
    cursor.fetchone.return_value = (0,)
    cursor.fetchall.side_effect = PermissionError('catalog denied')
    try:
        _rebase_plan(cursor)
    except PermissionError:
        pass
    else:
        raise AssertionError('Catalog denial was swallowed')
    assert cursor.execute.call_args.args == (
        'SET @@SESSION.dolt_show_system_tables = 0',)
