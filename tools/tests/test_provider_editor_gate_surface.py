"""Native browser gates must open the requested, bounded editor surface."""

from types import SimpleNamespace

import pytest

from tools.cdeadmin_provider_object_form_gate import _open_focused_form


@pytest.mark.parametrize('surface', [None, 'administration', 'object'])
def test_editor_surface_passes_exact_context(surface):
    calls = []
    driver = SimpleNamespace(execute_script=lambda *args: calls.append(args))
    operation = {'resource_kind': 'table', 'operation_id': 'drop'}
    target = {'resource_id': 'table:OWNED'}
    options = {} if surface is None else {'workspace': surface}
    _open_focused_form(driver, operation, target, 'db-owned', **options)
    assert len(calls) == 1
    assert calls[0][1:] == (
        operation, target, 'db-owned', surface or 'administration')
    assert 'const workspace = arguments[3]' in calls[0][0]
    assert 'workspace, {' in calls[0][0]


def test_unknown_surface_is_rejected_before_opening():
    calls = []
    driver = SimpleNamespace(execute_script=lambda *args: calls.append(args))
    with pytest.raises(ValueError):
        _open_focused_form(driver, {}, {}, 'db-owned', workspace='other')
    assert calls == []
