"""Shadow forms encode native numbered/file tasks, not tablespaces."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tools.cdeadmin_firebird_admin_mapping_gate import ADMINISTRATION
from pgadmin.cdeadmin.providers.firebird import shadows
from pgadmin.cdeadmin.sdk.relational import RelationalClientError
from pgadmin.cdeadmin.visual_admin.catalog import catalog_for_engine
from pgadmin.cdeadmin.resources.properties import normalize_resource_properties
from pgadmin.cdeadmin.providers.firebird.provider import PROFILE
from pgadmin.cdeadmin.sdk.actual_engine import ActualEnginePilotProvider


TARGET = {'resource_kind': 'shadow', 'display_name': '7'}


@pytest.mark.parametrize('mode', ['AUTO', 'MANUAL'])
@pytest.mark.parametrize('conditional', [False, True])
def test_native_modes_and_utf8_quoted_server_paths(mode, conditional):
    assert shadows.compile_operation('create', {
        'number': 7, 'mode': mode, 'conditional': conditional,
        'filename': "/owned/影's.shd"}) == [
        'CREATE SHADOW 7 ' + mode +
        (' CONDITIONAL' if conditional else '') + " '/owned/影''s.shd'"]


@pytest.mark.parametrize('number', [1, 32767, '1', '32767'])
def test_positive_short_shadow_identity(number):
    assert shadows.compile_operation('create', {
        'number': number, 'filename': '/owned/file'})[0].startswith(
            'CREATE SHADOW ' + str(number) + ' AUTO ')


@pytest.mark.parametrize('number', [0, -1, 32768, False, None, [], 1.5,
                                    '1; DROP DATABASE', '9' * 10000])
def test_invalid_number_never_becomes_sql(number):
    with pytest.raises(RelationalClientError):
        shadows.compile_operation('create', {
            'number': number, 'filename': '/owned/file'})


def test_file_rows_preserve_order_lengths_and_explicit_starting_pages():
    assert shadows.compile_operation('create', {
        'number': 1, 'filename': '/owned/one', 'length': 256,
        'secondary_files': [
            {'filename': '/owned/two', 'length': 256},
            {'filename': '/owned/three', 'start': 700},
            {'filename': '/owned/four', 'start': 1000},
        ]}) == [
        "CREATE SHADOW 1 AUTO '/owned/one' LENGTH 256 PAGES "
        "FILE '/owned/two' LENGTH 256 PAGES "
        "FILE '/owned/three' STARTING AT PAGE 700 "
        "FILE '/owned/four' STARTING AT PAGE 1000"]


@pytest.mark.parametrize('length', [None, '', 0, '0'])
@pytest.mark.parametrize('start', [None, '', 0, '0'])
def test_missing_predecessor_length_requires_a_positive_file_start(
        length, start):
    with pytest.raises(RelationalClientError, match='predecessor'):
        shadows.compile_operation('create', {
            'number': 1, 'filename': '/owned/one', 'length': length,
            'secondary_files': [{'filename': '/owned/two', 'start': start}]})


@pytest.mark.parametrize('value', [-1, 2147483648, 1.5, False, '9' * 10000])
def test_page_count_bounds_follow_sql_grammar(value):
    with pytest.raises(RelationalClientError):
        shadows.compile_operation('create', {
            'number': 1, 'filename': '/owned/one', 'length': value})


@pytest.mark.parametrize('value', ['', ' ', None, False, '/owned/\x00',
                                   '/owned/\nfile', '/owned/\ud800'])
def test_invalid_filenames_are_not_encoded(value):
    with pytest.raises(RelationalClientError):
        shadows.compile_operation('create', {'number': 1, 'filename': value})


@pytest.mark.parametrize('records', [
    {}, None, ['file'], [None], [{'filename': '/owned/one'}],
    [{'filename': '/owned/two', 'owner': 'x'}],
])
def test_secondary_files_reject_wrong_shapes_duplicates_and_unknown_fields(
        records):
    with pytest.raises(RelationalClientError):
        shadows.compile_operation('create', {
            'number': 1, 'filename': '/owned/one', 'length': 256,
            'secondary_files': records})


@pytest.mark.parametrize('preserve,clause', [(True, 'PRESERVE'),
                                             (False, 'DELETE')])
def test_drop_file_policy_is_explicit_and_bound_to_number(preserve, clause):
    assert shadows.compile_operation('drop', {
        'confirmation': '7', 'preserve_files': preserve}, TARGET) == [
        'DROP SHADOW 7 ' + clause + ' FILE']


@pytest.mark.parametrize('confirmation', [
    7, '8', '', None, '7; DROP DATABASE'])
def test_drop_requires_exact_text_confirmation(confirmation):
    with pytest.raises(RelationalClientError, match='Confirm'):
        shadows.compile_operation(
            'drop', {'confirmation': confirmation}, TARGET)


@pytest.mark.parametrize('operation,draft', [
    ([], {}), ('alter', {}), ('create', []),
    ('create', {'mode': []}), ('create', {'cascade': True}),
    ('drop', {'rename': 8}), ('drop', {'confirmation': '7',
                                       'preserve_files': 'yes'}),
])
def test_unavailable_tasks_and_invalid_drafts_are_rejected(operation, draft):
    with pytest.raises(RelationalClientError):
        shadows.compile_operation(operation, draft, TARGET)


def test_flag_32_is_decoded_in_shadow_or_difference_file_context():
    assert shadows.file_flags(1 | 4 | 16 | 32, shadow=True) == {
        'shadow': True, 'inactive': False, 'manual': True,
        'conditional': True, 'preserve_file': True}
    assert shadows.file_flags(32 | 64, shadow=False) == {
        'difference_file': True, 'backup_active': True}
    assert shadows.file_flags(0, shadow=False) == {
        'difference_file': False, 'backup_active': False}


def test_native_task_forms_have_no_fabricated_alter_or_privilege_fields():
    create = shadows.form('create', ADMINISTRATION._field)
    fields = {item['field_id']: item for item in create['fields']}
    assert set(fields) == {'number', 'mode', 'conditional', 'filename',
                           'length', 'secondary_files'}
    assert fields['number']['minimum'] == 1
    assert fields['number']['maximum'] == 32767
    drop = shadows.form('drop', ADMINISTRATION._field)
    assert drop['fields'][1]['default'] is True
    with pytest.raises(RelationalClientError):
        shadows.form('alter', ADMINISTRATION._field)


def test_metadata_keeps_order_flags_and_recreates_native_layout():
    value = shadows.metadata(7, [
        ('/owned/two', 1, 256, 0, 1),
        ("/owned/one's", 0, 0, 256, 1 | 4 | 16),
    ])
    assert value['mode'] == 'MANUAL'
    assert value['conditional'] is True
    assert value['inactive'] is False
    assert value['registered'] is True
    assert value['required_database_privilege'] == 'ALTER DATABASE'
    assert [item['sequence'] for item in value['files']] == [0, 1]
    assert value['ddl'] == (
        "CREATE SHADOW 7 MANUAL CONDITIONAL '/owned/one''s' LENGTH 256 PAGES "
        "FILE '/owned/two' STARTING AT PAGE 256 LENGTH 0 PAGES;")


@pytest.mark.parametrize('rows', [[], None, [()], [('file', 0, 0, 0)],
                                  [('file', 1, 0, 0, 1)],
                                  [('file', 0, False, 0, 1)],
                                  [('file', 0, 0, 0, 1)] * 2])
def test_incomplete_native_file_catalog_is_not_silently_fabricated(rows):
    with pytest.raises(RelationalClientError):
        shadows.metadata(7, rows)


def test_unrepresentable_sql_layout_retains_native_metadata_and_explanation():
    value = shadows.metadata(7, [('file', 0, 0, 2 ** 31, 1)])
    assert value['files'][0]['length'] == 2 ** 31
    assert value['ddl'] is None
    assert value['ddl_unavailable']


@pytest.mark.parametrize('operation,draft', [
    ('create', {'number': 7, 'filename': '/owned/one', 'length': 256,
                'secondary_files': [{'filename': '/owned/two'}]}),
    ('drop', {'confirmation': '7', 'preserve_files': True}),
    ('drop', {'confirmation': '7', 'preserve_files': False}),
])
def test_provider_plans_use_exact_native_file_policy(operation, draft):
    request = {'resource_kind': 'shadow', 'operation_id': operation,
               'target_resource': TARGET, 'draft': draft,
               '_provider_route': {'database': 'owned'}}
    assert ADMINISTRATION.validate(request) == {'errors': []}
    plan = ADMINISTRATION.plan(request)
    assert [item['source'] for item in
            plan['command_preview']['statements']] == (
                shadows.compile_operation(operation, draft, TARGET))
    request['draft'] = {**draft, 'cascade': True}
    assert ADMINISTRATION.validate(request)['errors']
    with pytest.raises(RelationalClientError):
        ADMINISTRATION.plan(request)


def test_catalog_exposes_only_native_shadow_tasks_and_readonly_file_details():
    catalog = ADMINISTRATION.catalog(catalog_for_engine('firebird'))
    objects = {item['resource_kind']: item for item in catalog['objects']}
    operations = {item['operation_id']: item
                  for item in objects['shadow']['operations']}
    assert set(operations) == shadows.OPERATIONS
    for operation in ('create', 'drop'):
        record = operations[operation]
        assert record['form']['form_id'] == 'firebird.shadow.' + operation
        assert record['target_required'] is (operation == 'drop')
        assert record['confirmation_required'] is True
    assert operations['drop']['mutation_class'] == 'destructive'
    assert {item['operation_id'] for item in
            objects['storage-file']['operations']} == {'inspect'}


@pytest.mark.parametrize('target', [None, {}, {
    'resource_kind': 'table', 'display_name': '7'}])
def test_drop_cannot_be_retargeted_to_another_object_kind(target):
    with pytest.raises(RelationalClientError):
        shadows.compile_operation('drop', {'confirmation': '7'}, target)


@pytest.mark.parametrize('declared', [False, True])
def test_native_files_cross_the_workspace_properties_contract(declared):
    metadata = shadows.metadata(7, [('/owned/file', 0, 0, 256, 1)])
    if declared:
        metadata['property_sections'] = [
            'properties', 'ddl', 'files', 'operations']
    result = normalize_resource_properties(metadata)
    assert result['files'] == metadata['files']
    assert 'files' in result['property_sections']
    assert result['ddl'] == metadata['ddl']


@pytest.mark.parametrize('suffix', [' ', '  ', '\n'])
def test_catalog_observations_keep_significant_filename_characters(suffix):
    path = '/owned/file' + suffix
    metadata = shadows.metadata(7, [(path, 0, 0, 256, 1)])
    assert metadata['files'][0]['filename'] == path
    if suffix == '\n':
        assert metadata['ddl'] is None
        assert metadata['ddl_unavailable']
    else:
        assert shadows.literal(path) in metadata['ddl']


def test_resource_adapter_preserves_opaque_file_identity_and_display_name():
    adapter = ActualEnginePilotProvider.__new__(ActualEnginePilotProvider)
    adapter.profile = PROFILE
    adapter.context = SimpleNamespace(endpoint_id='owned')
    value = {'resource_kind': 'storage-file',
             'resource_id': 'storage-file:/owned/file ',
             'display_name': '/owned/file ',
             'authority_path': ['storage-file', '/owned/file '],
             'generation': 'owned',
             'native': {'filename': '/owned/file '}}
    result = adapter._resource(value)
    assert result['resource_id'] == value['resource_id']
    assert result['display_name'] == value['display_name']
    assert result['authority_path'] == value['authority_path']


def test_delete_preview_blocks_the_native_trailing_space_hazard():
    target = {**TARGET, 'native': shadows.metadata(
        7, [('/owned/file ', 0, 0, 256, 1)])}
    assert target['native']['catalog_warnings'] == [shadows.DELETE_WARNING]
    with pytest.raises(RelationalClientError, match='different file'):
        shadows.compile_operation('drop', {
            'confirmation': '7', 'preserve_files': False}, target)
    assert shadows.compile_operation('drop', {
        'confirmation': '7', 'preserve_files': True}, target) == [
            'DROP SHADOW 7 PRESERVE FILE']


@pytest.mark.parametrize('preserve', [False, True])
@pytest.mark.parametrize('path', ['/owned/file', '/owned/file '])
def test_executor_checks_native_paths_not_client_supplied_metadata(
        preserve, path):
    cursor = Mock()
    cursor.fetchall.return_value = [(path,)]
    if not preserve and path.endswith(' '):
        with pytest.raises(RelationalClientError, match='different file'):
            shadows.verify_drop(cursor, 7, preserve)
    else:
        shadows.verify_drop(cursor, 7, preserve)
    if preserve:
        cursor.execute.assert_not_called()
    else:
        cursor.execute.assert_called_once_with(
            'SELECT RDB$FILE_NAME FROM RDB$FILES '
            'WHERE RDB$SHADOW_NUMBER = ?', (7,))


def test_shadow_delete_guard_is_carried_in_the_provider_sealed_plan():
    plan = ADMINISTRATION.plan({
        'resource_kind': 'shadow', 'operation_id': 'drop',
        'target_resource': TARGET,
        'draft': {'confirmation': '7', 'preserve_files': False},
        '_provider_route': {'database': 'owned'}})
    assert plan['provider_payload']['compiled']['firebird_shadow_drop'] == {
        'number': 7, 'preserve': False}
