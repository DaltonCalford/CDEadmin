"""Ordered, paired logical backup volumes never coerce unsafe sizes."""
import copy
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import pytest

from tools.cdeadmin_firebird_admin_mapping_gate import RelationalClientError
from pgadmin.cdeadmin.providers.firebird.backup_volumes import (
    logical_backup_volumes, start_logical_backup)
from pgadmin.cdeadmin.providers.firebird.provider import (
    ADMINISTRATION, _firebird_service_operation)
from pgadmin.cdeadmin.visual_admin import ProviderVisualAdministration
from pgadmin.cdeadmin.visual_admin.catalog import catalog_for_engine


def visual_volume_field():
    catalog = ADMINISTRATION.catalog(catalog_for_engine('firebird'))
    database = next(item for item in catalog['objects']
                    if item['resource_kind'] == 'database')
    operation = next(item for item in database['operations']
                     if item['operation_id'] == 'backup_logical')
    return next(item for item in operation['form']['fields']
                if item['field_id'] == 'backup_volumes')


def test_backup_and_restore_have_distinct_confirmation_contracts():
    catalog = ADMINISTRATION.catalog(catalog_for_engine('firebird'))
    database = next(item for item in catalog['objects']
                    if item['resource_kind'] == 'database')
    operations = {item['operation_id']: item
                  for item in database['operations']}
    assert not operations['backup_logical'].get('confirmation_required', False)
    assert operations['restore_logical']['confirmation_required'] is True


@pytest.mark.parametrize('count,valid', [(9999, True), (10000, False)])
def test_split_header_four_digit_volume_count_boundary(count, valid):
    # mvol.cpp writes %4d into fixed four-character sequence/total fields.
    # This is a format boundary check, never a 10,000-file native operation.
    rows = [{'filename': f'part-{index}', 'size_bytes': 2048}
            for index in range(count - 1)] + [{'filename': 'last'}]
    options = {'split_backup': True, 'backup_volumes': rows}
    if valid:
        files, sizes = logical_backup_volumes(options)
        assert len(files) == count
        assert len(sizes) == count - 1
    else:
        with pytest.raises(RelationalClientError):
            logical_backup_volumes(options)
        module, server, _builder = native_fixture()
        with pytest.raises(RelationalClientError):
            _firebird_service_operation(server, 'backup_logical', 'owned',
                                        options, module)
        assert not server.mock_calls
        module.get_api.assert_not_called()


@pytest.mark.parametrize('empty', [None, ''])
def test_visual_admission_omits_empty_final_capacity_before_native_plan(empty):
    value = [{'filename': 'first', 'size_bytes': 2048},
             {'filename': 'last', 'size_bytes': empty}]
    admitted, error = ProviderVisualAdministration._validate_field(
        visual_volume_field(), value)
    assert error is None
    assert admitted == [{'filename': 'first', 'size_bytes': 2048},
                        {'filename': 'last'}]
    assert logical_backup_volumes({'split_backup': True,
                                   'backup_volumes': admitted}) == (
                                       ['first', 'last'], [2048])


def test_visual_capacity_error_names_the_exact_row_and_bound():
    admitted, error = ProviderVisualAdministration._validate_field(
        visual_volume_field(), [{'filename': 'first', 'size_bytes': 2047},
                                {'filename': 'last'}])
    assert admitted is None
    assert error['field_id'] == 'backup_volumes'
    assert error['message'] == (
        'Ordered backup volumes item 1: Capacity in bytes (empty for last '
        'volume) is below its minimum.')


@pytest.mark.parametrize('size', [2048, 4096, 2147483647])
def test_native_shape_and_server_paths_are_preserved(size):
    options = {'split_backup': True, 'backup_volumes': [
        {'filename': 'C:\\owned\\é.fbk', 'size_bytes': size},
        {'filename': '/owned/東京.fbk', 'size_bytes': 8192},
        {'filename': '/owned/last.fbk'}]}
    original = copy.deepcopy(options)
    assert logical_backup_volumes(options) == (
        ['C:\\owned\\é.fbk', '/owned/東京.fbk', '/owned/last.fbk'],
        [size, 8192])
    assert options == original


@pytest.mark.parametrize('size', [
    None, 0, -1, 2047, 2147483648, True, False, 2048.0, '2048', [], {}])
def test_invalid_non_final_capacity(size):
    with pytest.raises(RelationalClientError):
        logical_backup_volumes({'split_backup': True, 'backup_volumes': [
            {'filename': 'first', 'size_bytes': size}, {'filename': 'last'}]})


@pytest.mark.parametrize('size', [0, 2048, False, [], {}])
def test_last_volume_must_be_unbounded(size):
    with pytest.raises(RelationalClientError):
        logical_backup_volumes({'split_backup': True, 'backup_volumes': [
            {'filename': 'first', 'size_bytes': 2048},
            {'filename': 'last', 'size_bytes': size}]})


@pytest.mark.parametrize('name', [None, '', ' ', 'a\nb', 'a\rb', 'a\x00b', 1])
def test_invalid_filename(name):
    with pytest.raises(RelationalClientError):
        logical_backup_volumes({'backup_file': name})


@pytest.mark.parametrize('options', [
    None, [], {}, {'split_backup': 'yes'}, {'split_backup': 1},
    {'split_backup': True}, {'split_backup': True, 'backup_volumes': []},
    {'split_backup': True, 'backup_volumes': [{'filename': 'only'}]},
    {'backup_file': 'one', 'backup_volumes': [{'filename': 'other'}]},
    {'split_backup': True, 'backup_file': 'one', 'backup_volumes': [
        {'filename': 'first', 'size_bytes': 2048}, {'filename': 'last'}]},
    {'split_backup': True, 'backup_volumes': [None, None]},
    {'split_backup': True, 'backup_volumes': [
        {'filename': 'same', 'size_bytes': 2048}, {'filename': 'same'}]},
    {'split_backup': True, 'backup_volumes': [
        {'filename': 'first', 'size_bytes': 2048, 'unknown': 1},
        {'filename': 'last'}]},
])
def test_malformed_and_ambiguous_inputs_are_rejected(options):
    with pytest.raises(RelationalClientError):
        logical_backup_volumes(options)


@pytest.mark.parametrize('extras', [{}, {'split_backup': False},
                                    {'backup_volumes': []},
                                    {'backup_volumes': None}])
def test_single_file_compatibility(extras):
    assert logical_backup_volumes({'backup_file': 'owned.fbk', **extras}) == (
        ['owned.fbk'], [])


@pytest.mark.parametrize('empty', [None, ''])
def test_visual_empty_final_capacity_is_unbounded(empty):
    assert logical_backup_volumes({'split_backup': True, 'backup_volumes': [
        {'filename': 'first', 'size_bytes': 2048},
        {'filename': 'last', 'size_bytes': empty}]}) == (
            ['first', 'last'], [2048])


def native_fixture():
    import firebird.driver as driver
    builder = Mock()
    module = SimpleNamespace(core=driver.core, get_api=Mock())
    module.get_api.return_value.util.get_xpb_builder.return_value = MagicMock(
        __enter__=Mock(return_value=builder))
    server = MagicMock(encoding='utf-8')
    server.__iter__.return_value = iter(['line\r\n', 'last\n'])
    return module, server, builder


def test_split_spb_pairs_sizes_without_phantom_final_filename():
    module, server, builder = native_fixture()
    options = {'split_backup': True, 'backup_volumes': [
        {'filename': '/owned/é.fbk', 'size_bytes': 2048},
        {'filename': '/owned/東京.fbk'}],
        'role': 'rôle', 'skip_data': 'É.*', 'include_data': '試.*',
        'key_holder': 'holder', 'key_name': 'key', 'crypt_plugin': 'crypt',
        'statistics': 'TDWR', 'verbose': True, 'verbose_interval': 500,
        'parallel_workers': 2}
    output = []
    start_logical_backup(server, '/owned/source.fdb', options, 8, module,
                         output.append)
    core = module.core
    assert builder.method_calls[:5] == [
        ('insert_tag', (core.ServerAction.BACKUP,), {}),
        ('insert_string', (core.SPBItem.DBNAME, '/owned/source.fdb'),
         {'encoding': 'utf-8'}),
        ('insert_string', (core.SrvBackupOption.FILE, '/owned/é.fbk'),
         {'encoding': 'utf-8'}),
        ('insert_int', (core.SrvBackupOption.LENGTH, 2048), {}),
        ('insert_string', (core.SrvBackupOption.FILE, '/owned/東京.fbk'),
         {'encoding': 'utf-8'}),
    ]
    builder.insert_string.assert_any_call(core.SPBItem.SQL_ROLE_NAME,
                                          'rôle', encoding='utf-8')
    for field, tag in [
        ('skip_data', 'SKIP_DATA'), ('include_data', 'INCLUDE_DATA'),
        ('key_holder', 'KEYHOLDER'), ('key_name', 'KEYNAME'),
        ('crypt_plugin', 'CRYPT'), ('statistics', 'STAT'),
    ]:
        builder.insert_string.assert_any_call(core.SrvBackupOption[tag],
                                              options[field], encoding='utf-8')
    builder.insert_int.assert_any_call(core.SPBItem.OPTIONS, 8)
    builder.insert_int.assert_any_call(core.SPBItem.VERBINT, 500)
    builder.insert_int.assert_any_call(
        core.SrvBackupOption.PARALLEL_WORKERS, 2)
    builder.insert_tag.assert_any_call(core.SPBItem.VERBOSE)
    server._svc.start.assert_called_once_with(builder.get_buffer.return_value)
    assert output == ['line\r\n', 'last\n']


def test_optional_fields_and_silent_mode_do_not_insert_spurious_options():
    module, server, builder = native_fixture()
    options = {'split_backup': True, 'verbose': False, 'backup_volumes': [
        {'filename': 'first', 'size_bytes': 2048}, {'filename': 'last'}]}
    start_logical_backup(server, 'owned', options, 0, module, Mock())
    assert builder.insert_tag.call_count == 1
    assert builder.insert_string.call_count == 3
    assert builder.insert_int.call_count == 2


@pytest.mark.parametrize('options', [
    {'backup_file': ''}, {'split_backup': True, 'backup_volumes': []},
    {'split_backup': True, 'backup_volumes': [
        {'filename': 'first', 'size_bytes': True}, {'filename': 'last'}]},
])
def test_bad_volume_shape_cannot_start_or_reset_native_service(options):
    module, server, _builder = native_fixture()
    with pytest.raises(RelationalClientError):
        start_logical_backup(server, 'owned', options, 0, module, Mock())
    assert not server.mock_calls
    module.get_api.assert_not_called()


def test_native_start_failure_is_not_swallowed_or_replayed():
    module, server, _builder = native_fixture()
    server._svc.start.side_effect = RuntimeError('native start failed')
    callback = Mock()
    options = {'split_backup': True, 'backup_volumes': [
        {'filename': 'first', 'size_bytes': 2048}, {'filename': 'last'}]}
    with pytest.raises(RuntimeError, match='native start failed'):
        start_logical_backup(server, 'owned', options, 0, module, callback)
    server._svc.start.assert_called_once()
    callback.assert_not_called()


@pytest.mark.parametrize('split', [False, True])
def test_plan_retains_exact_native_volume_options(split):
    draft = ({'split_backup': True, 'backup_volumes': [
        {'filename': 'first', 'size_bytes': 2048}, {'filename': 'last'}]}
        if split else {'backup_file': 'single'})
    request = {'engine_id': 'firebird', 'resource_kind': 'database',
               'operation_id': 'backup_logical', 'draft': draft,
               '_provider_route': {'database': '/owned/source.fdb'}}
    assert not ADMINISTRATION.validate(request)['errors']
    planned = ADMINISTRATION.plan(request)['provider_payload']['compiled']
    assert planned['options'] == draft
    assert planned['operation_id'] == 'backup_logical'
    assert planned['database'] == '/owned/source.fdb'


@pytest.mark.parametrize('size', [0, True, 2047, 2147483648])
def test_bad_split_draft_is_rejected_by_validator_and_before_native_access(
        size):
    draft = {'split_backup': True, 'backup_volumes': [
        {'filename': 'first', 'size_bytes': size}, {'filename': 'last'}]}
    assert ADMINISTRATION._validate_firebird_service('backup_logical', draft)
    server = Mock()
    with pytest.raises(RelationalClientError):
        _firebird_service_operation(server, 'backup_logical', 'owned',
                                    draft, Mock())
    assert not server.mock_calls
