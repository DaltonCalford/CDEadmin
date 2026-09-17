"""Fail-closed identity admission for direct Firebird views."""
from unittest.mock import MagicMock
import json

import firebird.driver as native
import pytest

from tools.cdeadmin_firebird_admin_mapping_gate import ADMINISTRATION
from pgadmin.cdeadmin.providers.firebird.views import grid_update_identity


def connection():
    handle = MagicMock()
    cursor = handle.cursor.return_value.__enter__.return_value
    cursor.fetchall.side_effect = [
        [('BASE', 0)], [('ID',)],
        [('KEY_ALIAS', 'ID', 0), ('VALUE', 'V', 0), ('CALC', None, None)]]
    cursor.fetchone.side_effect = [(None, 0), (0,)]
    return handle, cursor


def test_prepare_only_and_release_statements():
    assert ADMINISTRATION.supports('view', 'update')
    assert not ADMINISTRATION.supports('view', 'insert')
    assert not ADMINISTRATION.supports('view', 'delete')
    handle, cursor = connection()
    first, second = MagicMock(), MagicMock()
    cursor.prepare.side_effect = [first, second,
                                  native.DatabaseError('denied')]
    assert grid_update_identity(handle, 'V"東京') == (
        ('KEY_ALIAS',), ('KEY_ALIAS', 'VALUE'))
    first.free.assert_called_once_with()
    second.free.assert_called_once_with()
    assert cursor.prepare.call_args_list[0].args[0] == (
        'UPDATE "V""東京" SET "KEY_ALIAS" = ? WHERE 1 = 0')
    assert all(call.args[0].startswith('SELECT')
               for call in cursor.execute.call_args_list)
    handle.commit.assert_not_called()
    handle.rollback.assert_not_called()


@pytest.mark.parametrize('sources', [[], [('A', 0), ('B', 1)]])
def test_no_single_source(sources):
    handle, cursor = connection()
    cursor.fetchall.side_effect = [sources]
    assert grid_update_identity(handle, 'V') == ((), ())
    cursor.prepare.assert_not_called()


@pytest.mark.parametrize('relation', [
    None, (b'view', 1), (None, 2), (None, 4)])
def test_not_a_persistent_base_table(relation):
    handle, cursor = connection()
    cursor.fetchone.side_effect = [relation]
    assert grid_update_identity(handle, 'V') == ((), ())
    cursor.prepare.assert_not_called()


def test_active_view_triggers_need_separate_identity_contract():
    handle, cursor = connection()
    cursor.fetchone.side_effect = [(None, 0), (1,)]
    assert grid_update_identity(handle, 'V') == ((), ())
    cursor.prepare.assert_not_called()


@pytest.mark.parametrize('keys,fields', [
    ([], []),
    ([('ID',)], [('VALUE', 'V', 0)]),
    ([('ID',)], [('A', 'ID', 0), ('B', 'ID', 0)]),
    ([('ID',)], [('A', 'ID', 1)]),
    ([('ID',), ('OTHER',)], [('A', 'ID', 0)]),
])
def test_missing_or_ambiguous_key_projection(keys, fields):
    handle, cursor = connection()
    cursor.fetchall.side_effect = [[('BASE', 0)], keys, fields]
    assert grid_update_identity(handle, 'V') == ((), ())
    cursor.prepare.assert_not_called()


def test_no_native_updatable_columns():
    handle, cursor = connection()
    cursor.prepare.side_effect = native.DatabaseError('not updatable')
    assert grid_update_identity(handle, 'V') == ((), ())


def test_composite_key_alias_order():
    handle, cursor = connection()
    cursor.fetchall.side_effect = [
        [('BASE', 3)], [('B',), ('A',)],
        [('FIRST', 'A', 3), ('SECOND', 'B', 3)]]
    assert grid_update_identity(handle, 'V') == (
        ('SECOND', 'FIRST'), ('FIRST', 'SECOND'))


def evidence():
    checks = []
    for api in ('native', 'provider', 'grid'):
        targets = [('VM_SIMPLE', 'V'), ('VM_CALCULATED', 'V'),
                   ('VM_CALCULATED', 'DOUBLED')]
        if api != 'grid':
            targets += [('VM_AGGREGATE', 'V'), ('VM_TRIGGERED', 'V')]
        checks.extend({'case': f'{api}:{view}:{column}:{action}',
                       'passed': True}
                      for view, column in targets
                      for action in ('commit', 'rollback'))
    return {
        'schema': 'cdeadmin.firebird-views.v1', 'engine_version': '5.0.4',
        'complete': True, 'owned_container_removed': True, 'failures': [],
        'view_mutability_checks': checks,
        'task_evidence': {'visual_admin.view.update': {
            'live_execution': 'passed',
            'statements': ['UPDATE "VM_SIMPLE" SET "V" = ? WHERE "ID" = ?'],
        }},
    }


@pytest.mark.parametrize('change', [
    {}, {'complete': False}, {'owned_container_removed': False},
    {'engine_version': '5.0.3'}, {'failures': ['failed']},
    {'view_mutability_checks': []}, {'schema': 'unknown'},
])
def test_contract_requires_complete_native_evidence(change):
    from tools.reference_engine_demos import generate_firebird_dialect_contract
    WEB = generate_firebird_dialect_contract.WEB
    supplement_view_grid = (
        generate_firebird_dialect_contract.supplement_view_grid)
    document = json.loads((WEB / 'pgadmin/cdeadmin/providers/firebird/'
                           'firebird_dialect_5_0_4.json').read_text())
    proof = evidence()
    proof.update(change)
    if change:
        with pytest.raises(ValueError, match='evidence is incomplete'):
            supplement_view_grid(document, proof, 'a' * 64, 'owned.json')
    else:
        result = supplement_view_grid(document, proof, 'a' * 64, 'owned.json')
        assert result == supplement_view_grid(
            result, proof, 'a' * 64, 'owned.json')
        assert len(result['task_templates']) == len(document['task_templates'])
