"""Native repair modifiers, honest previews, and invalid draft rejection."""

import itertools
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock, Mock, call

import pytest

WEB = Path(__file__).resolve().parents[2] / 'web'
sys.path.insert(0, str(WEB))
if 'pgadmin' not in sys.modules:
    package = ModuleType('pgadmin')
    package.__path__ = [str(WEB / 'pgadmin')]
    sys.modules['pgadmin'] = package

from pgadmin.cdeadmin.providers.firebird import repair  # noqa: E402
from pgadmin.cdeadmin.sdk.relational import RelationalClientError  # noqa: E402
from pgadmin.cdeadmin.providers.firebird.provider import (  # noqa: E402
    ADMINISTRATION, _firebird_service_operation,
    _service_parallel_repair, _service_parallel_sweep,
)


MODIFIERS = [list(items) for count in range(4)
             for items in itertools.combinations(repair.MODIFIERS, count)]


@pytest.mark.parametrize('action', list(repair.ACTIONS))
@pytest.mark.parametrize('modifiers', MODIFIERS)
def test_repair_modifier_combinations(action, modifiers):
    draft = {'repair_action': action, 'repair_modifiers': modifiers}
    expected = list(repair.ACTIONS[action])
    expected.extend(item for item in repair.MODIFIERS
                    if item in modifiers and item not in expected)
    if action in ('KILL_SHADOWS', 'ICU', 'UPGRADE_DB') and modifiers:
        with pytest.raises(RelationalClientError):
            repair.flags(draft)
    else:
        assert repair.flags(draft) == expected
        selected = repair.selection('/data/exact.fdb ', draft)
        assert selected['database'] == '/data/exact.fdb '
        assert selected['native_flags'] == expected
        assert selected['ignore_checksums'] == ('IGNORE_CHECKSUM' in expected)
        assert selected['no_update'] == ('CHECK_DB' in expected)
        assert selected['mend_requested'] == ('MEND_DB' in expected)
        assert selected['full_validation'] == (
            'FULL' in expected or 'MEND_DB' in expected)


@pytest.mark.parametrize('modifiers', [
    None, '', 'FULL', {}, 1, True, [None], [[]], [{}], [False],
    ['FULL', 'FULL'], ['UNKNOWN'], ['MEND_DB'], ['VALIDATE_DB'],
])
def test_repair_rejects_invalid_modifiers(modifiers):
    with pytest.raises(RelationalClientError):
        repair.flags({'repair_action': 'VALIDATE_DB',
                      'repair_modifiers': modifiers})


@pytest.mark.parametrize('action', [None, [], {}, '', 'CHECK_DB', 'UNKNOWN'])
def test_repair_rejects_invalid_actions(action):
    with pytest.raises(RelationalClientError):
        repair.flags({'repair_action': action})


@pytest.mark.parametrize('options', [None, [], '', 'VALIDATE_DB', 1, True])
def test_repair_requires_an_options_mapping(options):
    with pytest.raises(RelationalClientError):
        repair.flags(options)


@pytest.mark.parametrize('database', [None, '', ' ', [], {}, 1, True])
def test_repair_selection_requires_an_explicit_database(database):
    with pytest.raises(RelationalClientError):
        repair.selection(database, {'repair_action': 'VALIDATE_DB'})


def test_repair_selection_does_not_mutate_presets_or_callers():
    modifiers = ['CHECK_DB', 'FULL']
    options = {'repair_action': 'MEND_DB', 'repair_modifiers': modifiers}
    selected = repair.selection('/data/exact.fdb', options)
    selected['native_flags'].append('IGNORE_CHECKSUM')
    assert modifiers == ['CHECK_DB', 'FULL']
    assert repair.ACTIONS['MEND_DB'] == ('MEND_DB',)
    assert repair.flags(options) == ['MEND_DB', 'FULL', 'CHECK_DB']


def test_legacy_presets_disclose_checksum_and_mend_implications():
    check = {'repair_action': 'CORRUPTION_CHECK'}
    mend = {'repair_action': 'REPAIR'}
    assert repair.flags(check) == [
        'VALIDATE_DB', 'CHECK_DB', 'FULL', 'IGNORE_CHECKSUM']
    assert repair.flags(mend) == ['MEND_DB', 'FULL', 'IGNORE_CHECKSUM']
    assert 'Checksum errors will be ignored' in ' '.join(
        repair.warnings(check))
    assert 'No-update validation' in ' '.join(repair.warnings(check))
    assert 'discard damaged records' in ' '.join(repair.warnings(mend))
    assert 'not a complete repair' in ' '.join(repair.warnings(mend))


def test_repair_preview_never_contains_credentials():
    selected = repair.selection('/data/test.fdb', {
        'repair_action': 'VALIDATE_DB', 'password': 'do-not-export',
        'role': 'MAINTENANCE'})
    assert selected['sql_role'] == 'MAINTENANCE'
    assert 'do-not-export' not in str(selected)


@pytest.mark.parametrize('task_role', [None, '', 'TASK_ROLE'])
def test_repair_preview_inherits_or_overrides_default_role(task_role):
    planned = ADMINISTRATION.plan({
        'resource_kind': 'database', 'operation_id': 'repair_database',
        'draft': {'repair_action': 'VALIDATE_DB', 'role': task_role},
        '_provider_route': {'database': '/data/exact.fdb',
                            'role': 'DEFAULT_ROLE'}})
    assert planned['command_preview']['repair_selection']['sql_role'] == (
        task_role or 'DEFAULT_ROLE')


@pytest.mark.parametrize('draft', [
    {'repair_action': 'ICU', 'repair_modifiers': ['FULL']},
    {'repair_action': 'ICU', 'repair_modifiers': ['IGNORE_CHECKSUM']},
    {'repair_action': 'KILL_SHADOWS', 'repair_modifiers': ['IGNORE_CHECKSUM']},
    {'repair_action': 'UPGRADE_DB', 'repair_modifiers': ['IGNORE_CHECKSUM']},
    {'repair_action': 'VALIDATE_DB', 'repair_modifiers': [['FULL']]},
    {'repair_action': 'VALIDATE_DB', 'repair_modifiers': None},
    {'repair_action': 'CHECK_DB'},
])
def test_invalid_repair_never_dispatches_a_native_service(draft):
    server = SimpleNamespace(database=Mock())
    with pytest.raises(RelationalClientError):
        _firebird_service_operation(
            server, 'repair_database', '/data/exact.fdb', draft, Mock())
    assert not server.database.mock_calls
    assert ADMINISTRATION.validate({
        'resource_kind': 'database', 'operation_id': 'repair_database',
        'draft': draft})['errors']


@pytest.mark.parametrize('action', list(repair.ACTIONS))
def test_plan_binds_repair_flags_without_native_io(action):
    path = '/data/exact database.fdb '
    draft = {'repair_action': action}
    plan = ADMINISTRATION.plan({
        'resource_kind': 'database', 'operation_id': 'repair_database',
        'draft': draft, '_provider_route': {'database': path}})
    assert plan['command_preview']['repair_selection'] == (
        repair.selection(path, draft))
    assert plan['warnings'] == repair.warnings(draft)
    compiled = plan['provider_payload']['compiled']
    assert compiled['database'] == path
    assert compiled['options'] == draft
    assert compiled['statements'] == []


@pytest.mark.parametrize('workers', [0, 1, 2, 128, 32767])
def test_icu_worker_preview_and_native_spb_order(workers):
    import firebird.driver as driver
    draft = {'repair_action': 'ICU', 'parallel_workers': workers}
    plan = ADMINISTRATION.plan({
        'resource_kind': 'database', 'operation_id': 'repair_database',
        'draft': draft, '_provider_route': {'database': '/owned/é.fdb'}})
    assert plan['command_preview']['repair_selection'][
        'parallel_workers_requested'] == workers
    assert 'not an observed worker count' in ' '.join(plan['warnings'])
    builder = Mock()
    builder.get_buffer.return_value = b'owned-start'
    module = SimpleNamespace(core=driver.core,
                             SrvRepairFlag=driver.SrvRepairFlag,
                             get_api=Mock())
    module.get_api.return_value.util.get_xpb_builder.return_value = (
        MagicMock(__enter__=Mock(return_value=builder)))
    server = Mock(encoding='utf-8')
    service = Mock()
    service._srv.return_value = server
    for flags, callback in (
            (driver.SrvRepairFlag.ICU, lambda: _service_parallel_repair(
                service, '/owned/é.fdb', workers, 'rôle', module,
                driver.SrvRepairFlag.ICU)),
            (driver.SrvRepairFlag.SWEEP_DB, lambda: _service_parallel_sweep(
                service, '/owned/é.fdb', workers, 'rôle', module))):
        builder.reset_mock()
        server.reset_mock()
        callback()
        core = driver.core
        assert builder.method_calls == [
            call.insert_tag(core.ServerAction.REPAIR),
            call.insert_string(core.SPBItem.DBNAME, '/owned/é.fdb',
                               encoding='utf-8'),
            call.insert_string(core.SPBItem.SQL_ROLE_NAME, 'rôle',
                               encoding='utf-8'),
            call.insert_int(core.SPBItem.OPTIONS, flags),
            call.insert_int(core.SrvRepairOption.PARALLEL_WORKERS, workers),
            call.get_buffer()]
        server._svc.start.assert_called_once_with(b'owned-start')
        server.wait.assert_called_once_with()


@pytest.mark.parametrize('workers', [
    -1, 32768, 65536, True, False, 1.5, '2', [], {}])
def test_invalid_icu_worker_request_never_dispatches(workers):
    test_invalid_repair_never_dispatches_a_native_service({
        'repair_action': 'ICU', 'parallel_workers': workers})


@pytest.mark.parametrize('action', [a for a in repair.ACTIONS if a != 'ICU'])
def test_worker_option_is_not_invented_for_other_repair_actions(action):
    test_invalid_repair_never_dispatches_a_native_service({
        'repair_action': action, 'parallel_workers': 1})
