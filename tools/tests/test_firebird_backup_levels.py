"""Numeric nbackup levels follow the signed incremental-header format."""
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from tools.cdeadmin_firebird_admin_mapping_gate import RelationalClientError
from pgadmin.cdeadmin.providers.firebird.provider import (
    ADMINISTRATION, _firebird_service_operation)
from pgadmin.cdeadmin.visual_admin.catalog import catalog_for_engine
from tools.tests.test_firebird_backup_history import fixture


@pytest.mark.parametrize('level', [0, 255, 256, 32767])
def test_installed_driver_encodes_exact_level_integer(level):
    module, server, builder = fixture()
    receiver = SimpleNamespace(_srv=lambda: server)
    with patch('firebird.driver.core.a.get_api', module.get_api):
        module.core.ServerDbServices3.nbackup(
            receiver, database='/owned/x.fdb', backup='/owned/x.nbk',
            level=level)
    builder.insert_int.assert_any_call(module.core.SrvNBackupOption.LEVEL,
                                       level)
    server._svc.start.assert_called_once_with(b'owned-start')
    server.wait.assert_called_once_with()


@pytest.mark.parametrize('level', [0, 1, 255, 256, 32767, None])
@pytest.mark.parametrize('history', [False, True])
def test_valid_levels_preserve_exact_plan_and_service_value(level, history):
    draft = {'backup_file': '/owned/proof.nbk', 'backup_level': level}
    if history:
        draft.update(clean_history=True, history_keep_unit='ROWS',
                     history_keep_value=1)
    request = {'engine_id': 'firebird', 'resource_kind': 'database',
               'operation_id': 'backup_physical', 'draft': draft,
               '_provider_route': {'database': '/owned/source.fdb'}}
    assert not ADMINISTRATION.validate(request)['errors']
    expected = 0 if level is None else level
    plan = ADMINISTRATION.plan(request)
    assert plan['provider_payload']['compiled']['options'][
        'backup_level'] == expected
    assert plan['command_preview']['backup_selection'] == {
        'mode': 'level', 'level': expected}
    module, server, builder = fixture()
    observed = _firebird_service_operation(
        server, 'backup_physical', '/owned/source.fdb', draft, module)
    assert observed['backup_selection_requested']['level'] == expected
    if history:
        builder.insert_int.assert_any_call(
            module.core.SrvNBackupOption.LEVEL, expected)
    else:
        assert server.database.nbackup.call_args.kwargs['level'] == expected
    assert draft['backup_level'] is level


@pytest.mark.parametrize('level', [-1, 32768, 65536, 2147483647, True, False,
                                   1.0, '256', [], {}])
def test_unsafe_levels_fail_before_native_dispatch(level):
    draft = {'backup_file': '/owned/proof.nbk', 'backup_level': level}
    errors = ADMINISTRATION.validate({
        'engine_id': 'firebird', 'resource_kind': 'database',
        'operation_id': 'backup_physical', 'draft': draft})['errors']
    assert any(error['field_id'] == 'backup_level' for error in errors)
    server = Mock()
    with pytest.raises(RelationalClientError):
        _firebird_service_operation(server, 'backup_physical',
                                    '/owned/source.fdb', draft, Mock())
    assert not server.mock_calls


def test_visual_range_matches_incremental_header_representation():
    catalog = ADMINISTRATION.catalog(catalog_for_engine('firebird'))
    database = next(item for item in catalog['objects']
                    if item['resource_kind'] == 'database')
    backup = next(item for item in database['operations']
                  if item['operation_id'] == 'backup_physical')
    field = next(item for item in backup['form']['fields']
                 if item['field_id'] == 'backup_level')
    assert field['minimum'] == 0
    assert field['maximum'] == 32767
    assert 'preceding' in field['help']
