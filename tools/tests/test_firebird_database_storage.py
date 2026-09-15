"""Exact Firebird database storage statements, without invented file DDL."""

import pytest

from tools.cdeadmin_firebird_admin_mapping_gate import ADMINISTRATION
from pgadmin.cdeadmin.providers.firebird import database_storage as storage
from pgadmin.cdeadmin.sdk.relational import RelationalClientError
from tools.cdeadmin_firebird_database_storage_gate import (
    ADMINISTRATION as STORAGE_ADMINISTRATION,
)


DATABASE = '/owned/database.fdb'


class StorageCursor:
    def __init__(self, records=(), primary=DATABASE):
        self.records = records
        self.primary = primary
        self.statements = []

    def execute(self, statement):
        self.statements.append(statement)

    def fetchone(self):
        return (self.primary,)

    def fetchall(self):
        return self.records


@pytest.mark.parametrize('operation', ['add_difference_file', 'begin_backup'])
@pytest.mark.parametrize('protected', [DATABASE, '/owned/extra',
                                       '/owned/shadow'])
def test_execution_guard_rejects_native_file_collisions(operation, protected):
    records = [('/owned/extra', 0, 0), ('/owned/shadow', 1, 32)]
    if operation == 'begin_backup':
        records.append((protected, 0, 32))
    with pytest.raises(RelationalClientError,
                       match='known database or shadow'):
        storage.verify_difference_path(StorageCursor(records), operation,
                                       protected)


@pytest.mark.parametrize('records,expected', [
    ([], DATABASE + '.delta'), ([(None, 0, 96)], DATABASE + '.delta'),
    ([('/owned/custom', 0, 32)], '/owned/custom'),
    ([('/owned/custom', 0, 96)], '/owned/custom'),
])
def test_execution_guard_uses_native_default_or_named_difference(records,
                                                                 expected):
    assert storage.verify_difference_path(
        StorageCursor(records), 'begin_backup') == expected


@pytest.mark.parametrize('records', [
    [('/owned/unsafe ', 0, 32)],
    [('/owned/one', 0, 32), ('/owned/two', 0, 32)],
])
def test_execution_guard_fails_closed_on_unsafe_native_metadata(records):
    with pytest.raises(RelationalClientError):
        storage.verify_difference_path(StorageCursor(records), 'begin_backup')


def test_execution_guard_does_not_guess_an_unavailable_native_filename():
    with pytest.raises(RelationalClientError, match='unavailable'):
        storage.verify_difference_path(StorageCursor(primary=None),
                                       'begin_backup')


def test_multifile_storage_does_not_invent_a_blanket_backup_restriction():
    cursor = StorageCursor([('/owned/extra', 0, 0)])
    assert storage.verify_difference_path(cursor, 'begin_backup') == (
        DATABASE + '.delta')
    assert storage.verify_difference_path(
        cursor, 'add_difference_file', '/owned/difference') == (
            '/owned/difference')


def test_shadow_files_do_not_invent_a_multifile_database_restriction():
    assert storage.verify_difference_path(
        StorageCursor([('/owned/shadow', 1, 1)]), 'begin_backup') == (
            DATABASE + '.delta')


def request_for(operation):
    draft = {'confirmation': DATABASE}
    if operation == 'add_files':
        draft['files'] = [{'filename': '/owned/extra', 'start': 8192}]
    elif operation == 'add_difference_file':
        draft['filename'] = '/owned/difference'
    elif operation == 'begin_backup':
        draft['confirm_difference_overwrite'] = True
    return {'resource_kind': 'database', 'operation_id': operation,
            '_provider_route': {'database': DATABASE}, 'draft': draft,
            'target_resource': {'resource_kind': 'database',
                                'display_name': 'database.fdb'}}


@pytest.mark.parametrize('operation', sorted(storage.OPERATIONS))
def test_scoped_provider_validation_normalization_and_preview(operation):
    request = request_for(operation)
    assert not STORAGE_ADMINISTRATION.validate(request)['errors']
    plan = STORAGE_ADMINISTRATION.plan(request)
    statements = [item['source'] for item in
                  plan['command_preview']['statements']]
    assert statements == storage.compile_operation(
        operation, request['draft'], DATABASE)
    assert STORAGE_ADMINISTRATION._normalize_draft(
        'database', operation, request['draft']) == request['draft']
    assert STORAGE_ADMINISTRATION._form('database', operation) == (
        storage.form(operation, STORAGE_ADMINISTRATION._field))


@pytest.mark.parametrize('operation', sorted(storage.OPERATIONS))
@pytest.mark.parametrize('route', [None, {}, [], {'database': ''},
                                   {'database': '/owned/other.fdb'}])
def test_preview_cannot_retarget_by_confirmation_or_display_name(
        operation, route):
    request = request_for(operation)
    request['_provider_route'] = route
    assert STORAGE_ADMINISTRATION.validate(request)['errors']
    with pytest.raises(RelationalClientError):
        STORAGE_ADMINISTRATION.plan(request)


def test_catalog_additions_preserve_existing_tasks_and_require_targets():
    catalog = {'objects': [{'resource_kind': 'database', 'operations': [
        {'operation_id': 'inspect', 'title': 'Inspect',
         'mutation_class': 'read', 'target_required': True,
         'confirmation_required': False}]}]}
    result = STORAGE_ADMINISTRATION.catalog(catalog)
    operations = {item['operation_id']: item for item in
                  result['objects'][0]['operations']}
    assert 'inspect' in operations
    for operation in storage.OPERATIONS:
        assert operations[operation]['target_required'] is True
        assert operations[operation]['confirmation_required'] is True
        assert operations[operation]['mutation_class'] == 'admin'


@pytest.mark.parametrize('operation,sql', [
    ('drop_difference_file', 'DROP DIFFERENCE FILE'),
    ('begin_backup', 'BEGIN BACKUP'), ('end_backup', 'END BACKUP'),
])
def test_database_state_tasks_are_individual_native_statements(operation, sql):
    assert storage.compile_operation(operation, {
        'confirmation': DATABASE,
        **({'confirm_difference_overwrite': True}
           if operation == 'begin_backup' else {})}, DATABASE) == [
        'ALTER DATABASE ' + sql]


def test_add_files_retains_order_page_units_and_literal_paths():
    assert storage.compile_operation('add_files', {
        'confirmation': DATABASE, 'files': [
            {'filename': "/owned/影's", 'start': 8192, 'length': 1024},
            {'filename': '/owned/second'},
        ]}, DATABASE) == [
        "ALTER DATABASE ADD FILE '/owned/影''s' "
        "STARTING AT PAGE 8192 LENGTH 1024 PAGES FILE '/owned/second'"]


@pytest.mark.parametrize('value', [0, 2147483647, '2147483647'])
@pytest.mark.parametrize('field', ['start', 'length'])
def test_page_boundaries_follow_native_number32bit(value, field):
    sql = storage.compile_operation('add_files', {
        'confirmation': DATABASE, 'files': [
            {'filename': '/owned/file', field: value}]}, DATABASE)[0]
    assert str(value) in sql


@pytest.mark.parametrize('value', [False, -1, 2147483648, '9' * 10000, 1.5])
@pytest.mark.parametrize('field', ['start', 'length'])
def test_invalid_pages_cannot_become_sql(value, field):
    with pytest.raises(RelationalClientError):
        storage.compile_operation('add_files', {
            'confirmation': DATABASE, 'files': [
                {'filename': '/owned/file', field: value}]}, DATABASE)


@pytest.mark.parametrize('operation', sorted(storage.OPERATIONS))
@pytest.mark.parametrize('confirmation', [None, '', 'database.fdb', False])
def test_every_storage_task_requires_the_exact_database_target(
        operation, confirmation):
    with pytest.raises(RelationalClientError, match='Confirm'):
        storage.compile_operation(operation, {
            'confirmation': confirmation}, DATABASE)


@pytest.mark.parametrize('files', [None, [], {}, ['file'], [None],
                                   [{'filename': '/owned/file', 'drop': True}],
                                   [{'filename': '/owned/file'}] * 2])
def test_file_list_is_nonempty_structured_and_unambiguous(files):
    with pytest.raises(RelationalClientError):
        storage.compile_operation('add_files', {
            'confirmation': DATABASE, 'files': files}, DATABASE)


def test_difference_path_is_literal_but_native_trim_hazard_is_blocked():
    assert storage.compile_operation('add_difference_file', {
        'confirmation': DATABASE, 'filename': "/owned/影's.delta"}, DATABASE
    ) == ["ALTER DATABASE ADD DIFFERENCE FILE '/owned/影''s.delta'"]
    with pytest.raises(RelationalClientError, match='trims trailing spaces'):
        storage.compile_operation('add_difference_file', {
            'confirmation': DATABASE, 'filename': '/owned/file '}, DATABASE)


@pytest.mark.parametrize('operation', ['drop_file', 'rename_file',
                                       'move_file', 'set_file_length', None])
def test_nonexistent_native_file_tasks_are_not_fabricated(operation):
    with pytest.raises(RelationalClientError):
        storage.compile_operation(operation, {'confirmation': DATABASE},
                                  DATABASE)


@pytest.mark.parametrize('operation', sorted(storage.OPERATIONS))
def test_forms_are_individual_tasks_with_exact_controls(operation):
    form = storage.form(operation, ADMINISTRATION._field)
    assert form['form_id'] == 'firebird.database.' + operation
    fields = {item['field_id']: item for item in form['fields']}
    assert fields['confirmation']['required'] is True
    assert storage.WARNINGS[operation] in fields['confirmation']['help']
    assert set(fields) == {'confirmation'} | (
        {'files'} if operation == 'add_files' else
        {'filename'} if operation == 'add_difference_file' else
        {'confirm_difference_overwrite'} if operation == 'begin_backup'
        else set())
    if operation == 'add_files':
        assert fields['files']['array_editor']['fields'][0]['control'] == (
            'multiline')


@pytest.mark.parametrize('filename', ['/owned/file\nname', '/owned/file ',
                                      '/owned/\tfile'])
def test_file_observations_are_not_normalized_as_sql_identifiers(filename):
    sql = storage.compile_operation('add_files', {
        'confirmation': DATABASE, 'files': [{'filename': filename}]},
        DATABASE)[0]
    assert "'" + filename + "'" in sql


@pytest.mark.parametrize('operation', sorted(storage.OPERATIONS))
def test_unknown_options_are_rejected_instead_of_ignored(operation):
    with pytest.raises(RelationalClientError, match='Unknown'):
        storage.compile_operation(operation, {
            'confirmation': DATABASE, 'cascade': True}, DATABASE)


@pytest.mark.parametrize('ack', [None, False, 1, 'yes', 'true'])
def test_backup_mode_requires_explicit_overwrite_acknowledgment(ack):
    with pytest.raises(RelationalClientError, match='may be overwritten'):
        storage.compile_operation('begin_backup', {
            'confirmation': DATABASE, 'confirm_difference_overwrite': ack},
            DATABASE)


def test_difference_file_cannot_be_the_exact_selected_database():
    with pytest.raises(RelationalClientError, match='selected database'):
        storage.compile_operation('add_difference_file', {
            'confirmation': DATABASE, 'filename': DATABASE}, DATABASE)
