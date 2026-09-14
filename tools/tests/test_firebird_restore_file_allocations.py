"""Secondary file capacities follow their files, not another array's index."""
from copy import deepcopy
from unittest.mock import Mock

import pytest

from tools.cdeadmin_firebird_admin_mapping_gate import RelationalClientError
from pgadmin.cdeadmin.providers.firebird.restore_files import (
    logical_restore_files, start_logical_restore)
from tools.tests.test_firebird_backup_volumes import native_fixture
from pgadmin.cdeadmin.providers.firebird.provider import (
    ADMINISTRATION, _firebird_service_operation)
from pgadmin.cdeadmin.visual_admin import ProviderVisualAdministration
from pgadmin.cdeadmin.visual_admin.catalog import catalog_for_engine


def paired():
    return {'restore_database': '/owned/main.fdb',
            'multiple_database_files': True, 'primary_file_pages': 256,
            'database_file_volumes': [
                {'filename': '/owned/東京.fdb', 'pages': 64},
                {'filename': '/owned/é.fdb', 'pages': 128},
                {'filename': '/owned/final.fdb'}]}


def restore_field(field_id):
    catalog = ADMINISTRATION.catalog(catalog_for_engine('firebird'))
    database = next(item for item in catalog['objects']
                    if item['resource_kind'] == 'database')
    operation = next(item for item in database['operations']
                     if item['operation_id'] == 'restore_logical')
    return next(item for item in operation['form']['fields']
                if item['field_id'] == field_id)


def test_visual_zero_primary_allocation_has_exact_accessible_error():
    admitted, error = ProviderVisualAdministration._validate_field(
        restore_field('primary_file_pages'), 0)
    assert admitted is None
    assert error['message'] == (
        'Primary database file allocation (pages) is below its minimum.')


def test_visual_secondary_error_names_its_record_and_bound():
    admitted, error = ProviderVisualAdministration._validate_field(
        restore_field('database_file_volumes'), [
            {'filename': 'second', 'pages': 0}, {'filename': 'last'}])
    assert admitted is None
    assert error['message'] == (
        'Secondary database files in order item 1: Page allocation '
        '(empty for last file) is below its minimum.')


@pytest.mark.parametrize('empty', [None, ''])
def test_visual_final_empty_page_count_is_omitted_before_native_dispatch(
        empty):
    admitted, error = ProviderVisualAdministration._validate_field(
        restore_field('database_file_volumes'), [
            {'filename': 'second', 'pages': 64},
            {'filename': 'last', 'pages': empty}])
    assert error is None
    assert admitted == [{'filename': 'second', 'pages': 64},
                        {'filename': 'last'}]
    assert logical_restore_files({**paired(), 'database_file_volumes':
                                  admitted})[1] == [256, 64]


def test_paired_allocations_reorder_with_files_without_mutating_draft():
    draft = paired()
    before = deepcopy(draft)
    assert logical_restore_files(draft) == (
        ['/owned/main.fdb', '/owned/東京.fdb', '/owned/é.fdb',
         '/owned/final.fdb'], [256, 64, 128])
    assert draft == before
    draft['database_file_volumes'][:2] = reversed(
        draft['database_file_volumes'][:2])
    assert logical_restore_files(draft) == (
        ['/owned/main.fdb', '/owned/é.fdb', '/owned/東京.fdb',
         '/owned/final.fdb'], [256, 128, 64])


@pytest.mark.parametrize('pages', [1, 254, 255, 256, 2147483647])
def test_native_primary_allocation_range_is_preserved(pages):
    draft = paired()
    draft['primary_file_pages'] = pages
    assert logical_restore_files(draft)[1][0] == pages


@pytest.mark.parametrize('value', [0, -1, True, False, 1.5, '256', None,
                                   2147483648])
@pytest.mark.parametrize('location', ['primary', 'secondary', 'legacy'])
def test_invalid_and_zero_allocations_are_never_encoded(value, location):
    draft = paired()
    if location == 'primary':
        draft['primary_file_pages'] = value
    elif location == 'secondary':
        draft['database_file_volumes'][0]['pages'] = value
    else:
        draft = {'restore_database': 'main',
                 'additional_database_files': ['second'],
                 'database_file_pages': [value]}
    with pytest.raises(RelationalClientError):
        logical_restore_files(draft)


@pytest.mark.parametrize('value', [None, ''])
def test_visual_final_empty_capacity_is_omitted(value):
    draft = paired()
    draft['database_file_volumes'][-1]['pages'] = value
    assert logical_restore_files(draft)[1] == [256, 64, 128]


@pytest.mark.parametrize('change', [
    {'multiple_database_files': 'true'},
    {'multiple_database_files': False},
    {'database_file_volumes': []},
    {'database_file_volumes': {}},
    {'database_file_volumes': [None]},
    {'database_file_volumes': [{'filename': 'last', 'unknown': 1}]},
    {'database_file_volumes': [{'filename': 'last', 'pages': 1}]},
    {'database_file_volumes': [{'filename': '/owned/main.fdb'}]},
    {'database_file_volumes': [{'filename': ''}]},
    {'database_file_volumes': [{'filename': 'bad\nname'}]},
    {'additional_database_files': ['old']},
    {'database_file_pages': [100]},
])
def test_ambiguous_or_malformed_visual_drafts_fail(change):
    with pytest.raises(RelationalClientError):
        logical_restore_files({**paired(), **change})


def test_legacy_and_single_file_paths_remain_supported():
    assert logical_restore_files({'restore_database': 'main'}) == (
        ['main'], [])
    assert logical_restore_files({
        'restore_database': r'C:\data\main.fdb',
        'additional_database_files': [r'D:\data\second.fdb'],
        'database_file_pages': [256]}) == (
            [r'C:\data\main.fdb', r'D:\data\second.fdb'], [256])


def test_native_page_address_boundary_is_not_silently_wrapped():
    draft = paired()
    draft['primary_file_pages'] = 2147483647
    draft['database_file_volumes'][0]['pages'] = 2147483647
    draft['database_file_volumes'].pop(1)
    assert logical_restore_files(draft)[1] == [2147483647, 2147483647]
    draft['database_file_volumes'].insert(1, {'filename': 'extra', 'pages': 1})
    with pytest.raises(RelationalClientError, match='32-bit'):
        logical_restore_files(draft)


@pytest.mark.parametrize('draft', [
    None, [], {},
    {'restore_database': 'main', 'additional_database_files': ['second']},
    {'restore_database': 'main', 'database_file_pages': [1]},
    {'restore_database': 'main', 'additional_database_files': ('second',),
     'database_file_pages': [1]},
    {'restore_database': 'main', 'additional_database_files': ['main'],
     'database_file_pages': [1]},
    {'restore_database': 'main', 'primary_file_pages': 1},
])
def test_invalid_legacy_shapes_are_rejected(draft):
    with pytest.raises(RelationalClientError):
        logical_restore_files(draft)


@pytest.mark.parametrize('total', [65536, 65537])
def test_header_sequence_width_is_a_shape_bound_not_a_live_file_claim(total):
    draft = {'restore_database': 'primary', 'multiple_database_files': True,
             'primary_file_pages': 1,
             'database_file_volumes': [
                 {'filename': f'file-{index}', **(
                     {'pages': 1} if index < total - 2 else {})}
                 for index in range(total - 1)]}
    if total == 65536:
        files, pages = logical_restore_files(draft)
        assert len(files) == total and len(pages) == total - 1
    else:
        with pytest.raises(RelationalClientError, match='65535'):
            logical_restore_files(draft)


def test_restore_spb_preserves_all_options_and_unicode_without_phantom_file():
    import firebird.driver as driver
    module, server, builder = native_fixture()
    module.DbAccessMode = driver.DbAccessMode
    module.ReplicaMode = driver.ReplicaMode
    options = {**paired(), 'backup_file': 'first',
               'additional_backup_files': ['最後'], 'role': 'rôle',
               'page_size': '8192', 'page_buffers': 0, 'parallel_workers': 2,
               'access_mode': 'READ_ONLY', 'replica_mode': 'READ_ONLY',
               'skip_data': 'NEVER%', 'include_data': '東京%',
               'key_holder': '鍵保持', 'key_name': 'OWNED',
               'crypt_plugin': '暗号', 'statistics': 'TDWR',
               'verbose': True, 'verbose_interval': 500}
    builder.insert_string.side_effect = (
        lambda _tag, value, **kwargs:
        value.encode(kwargs.get('encoding', 'ascii')))
    output = []
    start_logical_restore(server, options, 8192, module, output.append)
    core = module.core
    assert builder.method_calls[0] == (
        'insert_tag', (core.ServerAction.RESTORE,), {})
    filenames = [call.args[1] for call in builder.insert_string.call_args_list
                 if call.args[0] == core.SPBItem.DBNAME]
    assert filenames == logical_restore_files(options)[0]
    capacities = [call.args[1] for call in builder.insert_int.call_args_list
                  if call.args[0] == core.SrvRestoreOption.LENGTH]
    assert capacities == [256, 64, 128]
    for field, tag in (
        ('skip_data', 'SKIP_DATA'), ('include_data', 'INCLUDE_DATA'),
        ('key_holder', 'KEYHOLDER'), ('key_name', 'KEYNAME'),
        ('crypt_plugin', 'CRYPT'), ('statistics', 'STAT'),
    ):
        builder.insert_string.assert_any_call(
            core.SrvRestoreOption[tag], options[field], encoding='utf-8')
    builder.insert_string.assert_any_call(
        core.SPBItem.SQL_ROLE_NAME, 'rôle', encoding='utf-8')
    for tag, value in (('PAGE_SIZE', 8192), ('BUFFERS', 0),
                       ('PARALLEL_WORKERS', 2)):
        builder.insert_int.assert_any_call(core.SrvRestoreOption[tag], value)
    builder.insert_int.assert_any_call(
        core.SrvRestoreOption.REPLICA_MODE, driver.ReplicaMode.READ_ONLY.value)
    builder.insert_bytes.assert_called_once_with(
        core.SrvRestoreOption.ACCESS_MODE,
        bytes([driver.DbAccessMode.READ_ONLY]))
    builder.insert_int.assert_any_call(core.SPBItem.OPTIONS, 8192)
    builder.insert_int.assert_any_call(core.SPBItem.VERBINT, 500)
    server._svc.start.assert_called_once()
    assert output == ['line\r\n', 'last\n']


@pytest.mark.parametrize('change', [
    {'primary_file_pages': 0}, {'backup_file': ''},
    {'additional_backup_files': ['first']},
    {'additional_backup_files': 'second'},
])
def test_restore_bad_file_shape_cannot_reset_or_start_service(change):
    module, server, _builder = native_fixture()
    with pytest.raises(RelationalClientError):
        start_logical_restore(server, {
            **paired(), 'backup_file': 'first', **change}, 0, module, Mock())
    assert not server.mock_calls
    module.get_api.assert_not_called()


def test_restore_native_failure_is_not_replayed():
    import firebird.driver as driver
    module, server, _builder = native_fixture()
    module.DbAccessMode = driver.DbAccessMode
    server._svc.start.side_effect = RuntimeError('owned native failure')
    callback = Mock()
    with pytest.raises(RuntimeError, match='owned native failure'):
        start_logical_restore(server, {
            'restore_database': 'main', 'backup_file': 'first',
            'verbose': False}, 8192, module, callback)
    server._svc.start.assert_called_once()
    callback.assert_not_called()


@pytest.mark.parametrize('field', ['key_holder', 'key_name', 'crypt_plugin'])
def test_unicode_restore_dispatch_keeps_completion_and_exact_target(field):
    import firebird.driver as driver
    module, server, builder = native_fixture()
    module.DbAccessMode = driver.DbAccessMode
    module.SrvRestoreFlag = driver.SrvRestoreFlag
    result = _firebird_service_operation(server, 'restore_logical', 'source', {
        'restore_database': 'destination', 'backup_file': 'backup',
        field: '東京'}, module)
    server.database.restore.assert_not_called()
    server._svc.start.assert_called_once()
    builder.insert_int.assert_any_call(
        module.core.SPBItem.OPTIONS, driver.SrvRestoreFlag.CREATE)
    assert result['server_completed'] is True
    assert result['database'] == 'destination'
    assert result['output'] == ['line\r\n', 'last\n']


def test_paired_files_reach_the_standard_driver_without_reordering():
    import firebird.driver as driver
    module, server, _builder = native_fixture()
    module.DbAccessMode = driver.DbAccessMode
    module.SrvRestoreFlag = driver.SrvRestoreFlag
    result = _firebird_service_operation(server, 'restore_logical', 'source', {
        **paired(), 'backup_file': 'backup'}, module)
    request = server.database.restore.call_args.kwargs
    assert request['database'] == logical_restore_files(paired())[0]
    assert request['db_file_pages'] == [256, 64, 128]
    assert result['server_completed'] is True
    server._svc.start.assert_not_called()


@pytest.mark.parametrize('visual', [True, False])
def test_zero_pages_fail_admission_and_dispatch_before_native_access(visual):
    options = ({**paired(), 'primary_file_pages': 0} if visual else {
        'restore_database': 'primary', 'additional_database_files': ['second'],
        'database_file_pages': [0]})
    options['backup_file'] = 'backup'
    errors = ADMINISTRATION._validate_firebird_service(
        'restore_logical', options)
    assert any(error['code'] == 'invalid_firebird_restore_files'
               for error in errors)
    server = Mock()
    with pytest.raises(RelationalClientError):
        _firebird_service_operation(server, 'restore_logical', 'source',
                                    options, Mock())
    assert not server.mock_calls
