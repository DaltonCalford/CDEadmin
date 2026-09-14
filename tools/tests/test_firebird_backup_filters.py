"""Firebird data filters are composable and use native SimilarTo semantics."""
from importlib.metadata import version
from unittest.mock import Mock

import pytest

from tools.cdeadmin_firebird_admin_mapping_gate import ADMINISTRATION
from tools.tests.test_firebird_driver_backup_volumes import (
    installed_backup)  # noqa: F401 (pytest fixture)
from tools.tests.test_firebird_backup_volumes import native_fixture
from pgadmin.cdeadmin.providers.firebird.provider import (
    _firebird_service_operation)
from pgadmin.cdeadmin.visual_admin.catalog import catalog_for_engine


@pytest.mark.parametrize('operation', ['backup_logical', 'restore_logical'])
def test_native_include_and_skip_filters_can_be_combined(operation):
    # burp.cpp skipRelation's truth table: skip wins on overlap. Neither
    # pattern is executed by Python; the native engine remains authoritative.
    assert not ADMINISTRATION._validate_firebird_service(operation, {
        'skip_data': 'PRIVATE%', 'include_data': '%',
        'backup_file': '/owned/backup.fbk',
        **({'restore_database': '/owned/restored.fdb'}
           if operation == 'restore_logical' else {})})


@pytest.mark.parametrize('operation', ['backup_logical', 'restore_logical'])
def test_both_task_forms_explain_native_filter_semantics(operation):
    catalog = ADMINISTRATION.catalog(catalog_for_engine('firebird'))
    database = next(item for item in catalog['objects']
                    if item['resource_kind'] == 'database')
    task = next(item for item in database['operations']
                if item['operation_id'] == operation)
    fields = {item['field_id']: item for item in task['form']['fields']}
    skip = fields['skip_data']
    include = fields['include_data']
    assert skip['control'] == include['control'] == 'text'
    assert 'Skip takes precedence over include' in skip['help']
    assert 'Table metadata is retained' in skip['help']
    assert 'not a Python regular expression' in include['help']
    assert 'Metadata-only mode excludes all data' in include['help']


@pytest.mark.parametrize('field', ['skip_data', 'include_data'])
def test_installed_driver_single_backup_ascii_option_defect_is_isolated(
        installed_backup, field):
    if version('firebird-driver') != '1.10.11':
        pytest.skip('Exact 1.10.11 driver evidence')
    core, service, server, builder = installed_backup
    # Encode only in memory. The mock native service cannot create any files.
    builder.insert_string.side_effect = (
        lambda _tag, value, **kwargs:
        value.encode(kwargs.get('encoding', 'ascii')))
    with pytest.raises(UnicodeEncodeError):
        core.ServerDbServices3.backup(
            service, database='/owned/source.fdb', backup='/owned/backup.fbk',
            **{field: '東京%'})
    server._svc.start.assert_not_called()


@pytest.mark.parametrize('field,tag', [
    ('skip_data', 'SKIP_DATA'), ('include_data', 'INCLUDE_DATA'),
    ('key_holder', 'KEYHOLDER'), ('key_name', 'KEYNAME'),
    ('crypt_plugin', 'CRYPT'),
])
def test_single_backup_provider_encodes_optional_unicode_once(field, tag):
    import firebird.driver as driver
    module, server, builder = native_fixture()
    module.SrvBackupFlag = driver.SrvBackupFlag
    builder.insert_string.side_effect = (
        lambda _tag, value, **kwargs:
        value.encode(kwargs.get('encoding', 'ascii')))
    result = _firebird_service_operation(
        server, 'backup_logical', '/owned/source.fdb', {
            'backup_file': '/owned/backup.fbk', field: '東京%',
            'backup_flags': ['ZIP'], 'verbose': False}, module)
    builder.insert_string.assert_any_call(
        module.core.SrvBackupOption[tag], '東京%', encoding='utf-8')
    builder.insert_int.assert_any_call(
        module.core.SPBItem.OPTIONS, driver.SrvBackupFlag.ZIP)
    assert all(call.args[0] != module.core.SrvBackupOption.LENGTH
               for call in builder.insert_int.call_args_list)
    server.database.backup.assert_not_called()
    server._svc.start.assert_called_once()
    assert result['output'] == ['line\r\n', 'last\n']


@pytest.mark.parametrize('phase', ['create', 'start'])
def test_filter_gate_initial_failure_redacts_and_cleans_owned_only(
        monkeypatch, phase):
    from tools import cdeadmin_firebird_logical_filters_gate as gate
    monkeypatch.setattr(gate, '_configure_client_library', Mock())
    monkeypatch.setattr(gate.secrets, 'token_urlsafe',
                        Mock(return_value='FILTER-SECRET'))

    def docker(*args, **kwargs):
        assert 'FILTER-SECRET' not in ' '.join(args)
        if args[0] == 'create':
            assert kwargs['env']['FIREBIRD_ROOT_PASSWORD'] == 'FILTER-SECRET'
            if phase == 'start':
                return ('a' * 64).encode()
        raise RuntimeError('FILTER-SECRET')

    monkeypatch.setattr(gate, 'docker', docker)
    remove = Mock()
    monkeypatch.setattr(gate, 'remove_owned', remove)
    result = gate.run('owned-image')
    assert not result['complete']
    assert 'FILTER-SECRET' not in str(result)
    assert result['failures'] == [{
        'stage': phase + '-container', 'type': 'RuntimeError',
        'native_status_codes': []}]
    if phase == 'create':
        remove.assert_not_called()
    else:
        remove.assert_called_once_with('a' * 64)
        assert result['owned_container_removed']
