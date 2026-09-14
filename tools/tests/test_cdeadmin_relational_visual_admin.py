##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Executable relational administration and row-identity contract tests."""

from __future__ import annotations

import json
import io

import sqlite3
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from types import ModuleType, SimpleNamespace


ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / 'web'
if str(WEB) not in sys.path:
    sys.path.insert(0, str(WEB))
if 'pgadmin' not in sys.modules:
    package = ModuleType('pgadmin')
    package.__path__ = [str(WEB / 'pgadmin')]
    sys.modules['pgadmin'] = package

from pgadmin.cdeadmin.providers.sqlite.provider import (  # noqa: E402
    ADMINISTRATION as SQLITE_ADMINISTRATION,
    PROFILE,
    _drop_database_file,
    _initialize_database,
    _resources,
    _route_arguments,
    _sqlite_database_operation,
)
from pgadmin.cdeadmin.providers.firebird.provider import (  # noqa: E402
    ADMINISTRATION as FIREBIRD_ADMINISTRATION,
    _sequence_state,
    _catalog_detail,
    _role_privileges,
)
from pgadmin.cdeadmin.providers.duckdb.provider import (  # noqa: E402
    ADMINISTRATION as DUCKDB_ADMINISTRATION,
    PROFILE as DUCKDB_PROFILE,
    _create_client as duckdb_client,
    _drop_database_file as drop_duckdb_database_file,
    _resources as duckdb_resources,
)
from pgadmin.cdeadmin.providers.mysql_family.provider import (  # noqa: E402
    MARIADB_ADMINISTRATION,
    MYSQL_ADMINISTRATION,
)
from pgadmin.cdeadmin.providers.relational_admin import (  # noqa: E402
    RelationalAdministration,
    RelationalAdminDialect,
)
from pgadmin.cdeadmin.sdk import (  # noqa: E402
    PilotProfile,
    RelationalClientConfig,
    RelationalClientError,
    RelationalDBAPIClient,
)
from pgadmin.cdeadmin.visual_admin import (  # noqa: E402
    ProviderVisualAdministration,
)
from pgadmin.cdeadmin.visual_admin.catalog import (  # noqa: E402
    catalog_for_engine,
)


class Permissions:
    @staticmethod
    def allows(_permission, _scope='resource'):
        return True

    @staticmethod
    def require(_permission, _scope='resource'):
        return None


class AdministrationClient:
    def __init__(self, administration):
        self.administration = administration

    def supports_admin_operation(self, resource_kind, operation_id):
        return self.administration.supports(resource_kind, operation_id)

    def visual_admin_catalog(self, catalog):
        return self.administration.catalog(catalog)

    def validate_admin_operation(self, request):
        return self.administration.validate(request)

    def plan_admin_operation(self, request):
        return self.administration.plan(request)

    @staticmethod
    def apply_admin_operation(_request):
        return {'accepted': True}


def client_and_admin():
    profile = PilotProfile(
        'org.cdeadmin.sqlite.admin-test', 'sqlite-admin-test',
        'sqlite', 'SQLite', sqlite3.sqlite_version, 'embedded_sqlite',
        'relational', 'sqlite-sql', 'SQLite SQL',
        'sqlite-native-transaction', 'tabular', PROFILE.resource_kinds,
        PROFILE.admin_tools, PROFILE.required_permissions,
    )
    admin = RelationalAdministration(RelationalAdminDialect(
        engine_id='sqlite', supports_cascade=False,
        embedded_database=True,
        database_create_mode='embedded-file',
        database_extension='.sqlite',
        supported={
            'database': frozenset({
                'inspect', 'create', 'alter', 'drop', 'backup', 'restore',
                'integrity_check', 'quick_check', 'foreign_key_check',
                'vacuum', 'incremental_vacuum', 'optimize', 'analyze',
                'reindex', 'wal_checkpoint',
            }),
            'column': frozenset({'inspect', 'create', 'rename', 'drop'}),
            'index': frozenset({'inspect', 'create', 'drop'}),
            'virtual-table': frozenset({
                'inspect', 'create', 'rename', 'drop',
            }),
            'fts-table': frozenset({
                'inspect', 'create', 'rename', 'drop',
            }),
            'table': frozenset({
                'inspect', 'create', 'alter', 'rename', 'drop',
                'insert', 'update', 'delete',
            }),
        },
    ))
    client = RelationalDBAPIClient(RelationalClientConfig(
        profile=profile,
        module_name='sqlite3',
        version_query='SELECT sqlite_version()',
        connect_arguments=_route_arguments,
        metadata_reader=_resources,
        administration=admin,
        database_initializer=_initialize_database,
        database_dropper=_drop_database_file,
        database_operation_runner=_sqlite_database_operation,
    ), sqlite3)
    return client, admin


def request(route, operation, draft, target=None):
    return {
        'engine_id': 'sqlite',
        'resource_kind': 'table',
        'operation_id': operation,
        'target_resource': target,
        'draft': draft,
        '_provider_route': route,
    }


class RelationalVisualAdministrationTests(unittest.TestCase):

    def test_firebird_role_comments_are_independent_changes(self):
        def plan(operation, draft):
            return FIREBIRD_ADMINISTRATION.plan({
                '_provider_route': {'database': 'test'},
                'resource_kind': 'role', 'operation_id': operation,
                'target_resource': {'display_name': 'r'},
                'draft': {'name': 'r', **draft}
            })['command_preview']['statements']
        for operation in ('create', 'alter'):
            statements = plan(operation, {'description': "  It's a role\n"})
            self.assertEqual(statements[-1]['source'],
                             'COMMENT ON ROLE "r" IS \'  It\'\'s a role\n\'')
            self.assertEqual(len(statements),
                             2 if operation == 'create' else 1)
        self.assertEqual(plan('alter', {'clear_description': True})[0][
            'source'], 'COMMENT ON ROLE "r" IS NULL')
        for draft in ({'clear_description': 'false'}, {'description': 12},
                      {'description': 'x', 'clear_description': True}, {}):
            with self.assertRaises(RelationalClientError):
                plan('alter', draft)

    def test_firebird_role_privilege_bitmap(self):
        from pgadmin.cdeadmin.providers.relational_admin import (
            FIREBIRD_SYSTEM_PRIVILEGES)
        for index, name in enumerate(FIREBIRD_SYSTEM_PRIVILEGES, 1):
            raw = (1 << index).to_bytes(8, 'little')
            for value in (raw, bytearray(raw), memoryview(raw)):
                decoded = _role_privileges(value)
                self.assertEqual(decoded['system_privileges'], [name])
                self.assertEqual(decoded['unknown_system_privilege_bits'], [])
                self.assertEqual(decoded['system_privileges_hex'], raw.hex())
        self.assertEqual(_role_privileges(None)['system_privileges'], [])
        self.assertEqual(_role_privileges(b'\x01\x00\x00\x80')[
            'unknown_system_privilege_bits'], [0, 31])
        for invalid in ('0', 1, [], {}):
            with self.assertRaises(RelationalClientError):
                _role_privileges(invalid)

    def test_firebird_native_system_privilege_selector(self):
        from pgadmin.cdeadmin.providers.relational_admin import (
            FIREBIRD_SYSTEM_PRIVILEGES)
        self.assertEqual(len(FIREBIRD_SYSTEM_PRIVILEGES), 27)
        for operation in ('create', 'alter'):
            form = FIREBIRD_ADMINISTRATION._form('role', operation)
            field = next(item for item in form['fields']
                         if item['field_id'] == 'system_privileges')
            self.assertEqual(field['control'], 'multiselect')
            if operation == 'alter':
                self.assertEqual(field['initial_value_path'],
                                 ['system_privileges'])
            self.assertEqual({item['value'] for item in field['options']},
                             set(FIREBIRD_SYSTEM_PRIVILEGES))
            for privilege in FIREBIRD_SYSTEM_PRIVILEGES:
                plan = FIREBIRD_ADMINISTRATION.plan({
                    '_provider_route': {'database': 'test'},
                    'resource_kind': 'role', 'operation_id': operation,
                    'target_resource': {'display_name': 'audit_role'},
                    'draft': {'name': 'audit_role',
                              'system_privileges': [privilege]}})
                self.assertIn('SET SYSTEM PRIVILEGES TO ' + privilege,
                              plan['command_preview']['statements'][0][
                                  'source'])
        for value in (['SUPERUSER'], ['USER_MANAGEMENT; DROP ROLE X'],
                      ['USER_MANAGEMENT', 'USER_MANAGEMENT'], [None]):
            with self.assertRaises(RelationalClientError):
                FIREBIRD_ADMINISTRATION._privilege_names(value)
        for changes in ({'drop_system_privileges': 'false'},
                        {'drop_system_privileges': True,
                         'system_privileges': ['USER_MANAGEMENT']}):
            with self.assertRaises(RelationalClientError):
                FIREBIRD_ADMINISTRATION.plan({
                    '_provider_route': {'database': 'test'},
                    'resource_kind': 'role', 'operation_id': 'alter',
                    'target_resource': {'display_name': 'audit_role'},
                    'draft': changes})

    def test_firebird_role_membership_forms_and_commands(self):
        catalog = FIREBIRD_ADMINISTRATION.catalog(
            catalog_for_engine('firebird'))
        role = next(item for item in catalog['objects']
                    if item['resource_kind'] == 'role')
        operations = {item['operation_id']: item
                      for item in role['operations']}
        for operation in ('grant', 'revoke'):
            self.assertEqual(f'firebird.role.{operation}',
                             operations[operation]['form']['form_id'])

        def plan(operation, **options):
            return FIREBIRD_ADMINISTRATION.plan({
                '_provider_route': {'database': 'test'},
                'resource_kind': 'role', 'operation_id': operation,
                'target_resource': {'display_name': 'readers'},
                'draft': {'member': 'operator', **options}
            })['command_preview']['statements'][0]['source']

        self.assertEqual('GRANT "readers" TO USER "operator"', plan('grant'))
        self.assertEqual('REVOKE "readers" FROM USER "operator"',
                         plan('revoke'))
        self.assertEqual('GRANT DEFAULT "readers" TO ROLE "operator" '
                         'WITH ADMIN OPTION GRANTED BY USER "SYSDBA"',
                         plan('grant', default_role=True, member_kind='ROLE',
                              admin_option=True, grantor='SYSDBA'))
        self.assertEqual('REVOKE ADMIN OPTION FOR DEFAULT "readers" '
                         'FROM USER "operator"', plan(
                             'revoke', default_role=True,
                             admin_option_only=True))
        for invalid in ({'member_kind': 'GROUP'}, {'member': ''},
                        {'default_role': 'false'}, {'admin_option': 1},
                        {'admin_option_only': 'true'}):
            with self.subTest(invalid=invalid):
                with self.assertRaises(RelationalClientError):
                    plan('grant', **invalid)

    def test_firebird_class_wide_privilege_planning(self):
        base = {'principal': 'operator', 'privilege_scope': 'ddl_class',
                'ddl_class': 'TABLE', 'ddl_privileges': ['ALTER ANY'],
                'ddl_principal_kind': 'USER'}

        def plan(operation, draft):
            return FIREBIRD_ADMINISTRATION.plan({
                '_provider_route': {'database': 'test'},
                'resource_kind': 'privilege', 'operation_id': operation,
                'draft': draft})['command_preview']['statements'][0]['source']
        self.assertEqual('GRANT ALTER ANY TABLE TO USER "operator"',
                         plan('grant', base))
        self.assertEqual('REVOKE ALTER ANY TABLE FROM USER "operator"',
                         plan('revoke', {**base, 'confirmation': 'operator'}))
        self.assertEqual('GRANT CREATE, DROP ANY VIEW TO ROLE "operator" '
                         'WITH GRANT OPTION', plan('grant', {
                             **base, 'ddl_class': 'VIEW',
                             'ddl_privileges': ['CREATE', 'DROP ANY'],
                             'ddl_principal_kind': 'ROLE',
                             'grant_option': True}))
        for invalid in ({'ddl_class': 'INDEX'},
                        {'ddl_privileges': ['SELECT']},
                        {'grant_option': 'false'},
                        {'ddl_privileges': []}, {'ddl_principal_kind': 'ALL'},
                        {'object_name': 'individual_table'},
                        {'privilege_scope': 'other'}):
            with self.assertRaises(RelationalClientError):
                plan('grant', {**base, **invalid})
        form = FIREBIRD_ADMINISTRATION._form('privilege', 'grant')
        fields = {f['field_id']: f for f in form['fields']}
        self.assertEqual('object',
                         fields['object_name']['visible_when']['equals'])
        self.assertEqual('multiselect', fields['ddl_privileges']['control'])

    def test_firebird_index_statistics_and_state_are_independent(self):
        def plan(draft):
            return FIREBIRD_ADMINISTRATION.plan({
                'resource_kind': 'index', 'operation_id': 'alter',
                '_provider_route': {'database': 'test'}, 'draft': draft,
                'target_resource': {'display_name': 'IX',
                                    'display_path': ['T', 'IX']},
            })['command_preview']['statements']
        self.assertEqual(['SET STATISTICS INDEX "IX"'], [
            s['source'] for s in plan({'refresh_statistics': True})])
        self.assertEqual(['ALTER INDEX "IX" INACTIVE'], [
            s['source'] for s in plan({'active': False})])
        self.assertEqual(['ALTER INDEX "IX" ACTIVE',
                          'SET STATISTICS INDEX "IX"'], [s['source'] for s in
                         plan({'active': True, 'refresh_statistics': True})])
        for draft in ({}, {'refresh_statistics': False}, {'active': 1},
                      {'refresh_statistics': 'yes'}):
            with self.subTest(draft=draft):
                with self.assertRaises(RelationalClientError):
                    plan(draft)
        fields = {f['field_id']: f for f in
                  FIREBIRD_ADMINISTRATION._form('index', 'alter')['fields']}
        self.assertEqual(['state', 'active'],
                         fields['active']['initial_value_path'])
        self.assertNotIn('default', fields['active'])

    def test_firebird_catalog_blob_text_preserved_and_closed(self):
        text = '  comment; with apostrophe \' and newline\n' + 'x' * 100000
        for field in ('description', 'expression_source', 'condition_source'):
            stream = io.StringIO(text)
            self.assertEqual(text, _catalog_detail(field, stream))
            self.assertTrue(stream.closed)
            self.assertEqual(text, _catalog_detail(field, text))
            self.assertIsNone(_catalog_detail(field, None))
        self.assertEqual('42', _catalog_detail('index_type', '42 '))
        self.assertEqual(' leading domain',
                         _catalog_detail('domain', ' leading domain   '))
        self.assertEqual('\tleading',
                         _catalog_detail('domain', '\tleading   '))

    def test_firebird_catalog_failed_blob_read_closes_stream(self):
        class FailingReader(io.StringIO):
            def read(self):
                raise OSError('read failed')
        stream = FailingReader()
        with self.assertRaises(OSError):
            _catalog_detail('condition_source', stream)
        self.assertTrue(stream.closed)

    def test_firebird_index_variants_compile(self):
        admin = FIREBIRD_ADMINISTRATION
        for kind in ('columns', 'expression'):
            for direction in ('ASCENDING', 'DESCENDING'):
                for partial in (False, True):
                    draft = {'name': 'IX_TEST', 'table': 'ITEMS',
                             'index_kind': kind, 'direction': direction,
                             'unique': True}
                    if kind == 'columns':
                        draft['columns'] = ['ID', 'VALUE']
                    else:
                        draft['expression'] = 'UPPER("VALUE")'
                    if partial:
                        draft['condition'] = '"ID" > 0'
                    plan = admin.plan({'resource_kind': 'index',
                                       'operation_id': 'create',
                                       '_provider_route': {'database': 'test'},
                                       'draft': draft})
                    source = plan['command_preview']['statements'][0]['source']
                    self.assertIn(f'CREATE UNIQUE {direction} INDEX', source)
                    self.assertEqual(partial, ' WHERE ' in source)
                    self.assertEqual(kind == 'expression',
                                     'COMPUTED BY' in source)

    def test_firebird_index_controls_and_invalid_inputs(self):
        admin = FIREBIRD_ADMINISTRATION
        form = admin._form('index', 'create')
        admin._structured_record_controls(form)
        fields = {f['field_id']: f for f in form['fields']}
        self.assertEqual('expression',
                         fields['expression']['visible_when']['equals'])
        self.assertEqual('columns',
                         fields['columns']['visible_when']['equals'])
        self.assertIn('array_editor', fields['columns'])
        for changes in ({'index_kind': 'other'}, {'direction': 'sideways'},
                        {'index_kind': 'expression', 'expression': ''},
                        {'condition': '1=1; DROP TABLE ITEMS'},
                        {'condition': '1=1 /* unclosed comment'}):
            with self.subTest(changes=changes):
                with self.assertRaises(RelationalClientError):
                    admin.plan({'resource_kind': 'index',
                                '_provider_route': {'database': 'test'},
                                'operation_id': 'create', 'draft': {
                                    'name': 'IX', 'table': 'ITEMS',
                                    'columns': ['ID'], **changes}})

    def test_routine_parameter_forms_follow_native_compiler_records(self):
        for administration in (FIREBIRD_ADMINISTRATION,
                               MYSQL_ADMINISTRATION,
                               MARIADB_ADMINISTRATION):
            catalog = administration.catalog(catalog_for_engine(
                administration.dialect.engine_id))
            for obj in catalog['objects']:
                if obj['resource_kind'] not in {'procedure', 'function'}:
                    continue
                for operation in obj['operations']:
                    if operation['operation_id'] not in {'create', 'alter'}:
                        continue
                    form = operation['form']
                    if form['form_id'] not in {
                            'procedure.create', 'procedure.alter',
                            'function.create', 'function.alter'}:
                        continue
                    fields = {f['field_id']: f for f in form['fields']}
                    if 'parameters' not in fields:
                        continue
                    field = fields['parameters']
                    self.assertIn('array_editor', field)
                    params = [{'name': 'P_ID', 'type': 'INTEGER'}]
                    admitted, error = (
                        ProviderVisualAdministration._validate_field(
                            field, params))
                    self.assertIsNone(error)
                    self.assertEqual(params, admitted)
                    for invalid in ([{'name': 'P_ID'}],
                                    [{'name': 'P_ID', 'type': 'INTEGER',
                                      'ignored_mode': 'OUT'}], [42]):
                        _, error = (
                            ProviderVisualAdministration._validate_field(
                                field, invalid))
                        self.assertIsNotNone(error)
                    if administration.dialect.engine_id == 'firebird':
                        if obj['resource_kind'] == 'function':
                            self.assertNotIn('return_parameters', fields)
                            self.assertTrue(fields['returns']['required'])
                        else:
                            self.assertNotIn('returns', fields)
                            self.assertIn('array_editor',
                                          fields['return_parameters'])

    def test_duckdb_macro_parameters_are_not_typed_routine_records(self):
        for kind in ('macro', 'function'):
            form = DUCKDB_ADMINISTRATION._form(kind, 'create')
            DUCKDB_ADMINISTRATION._routine_record_controls(form)
            params = next(f for f in form['fields']
                          if f['field_id'] == 'parameters')
            self.assertNotIn('array_editor', params)

    def record_field(self, form_id, field_id, required=False):
        form = {'form_id': form_id, 'fields': [{
            'field_id': field_id, 'label': field_id, 'control': 'json',
            'required': required}]}
        DUCKDB_ADMINISTRATION._structured_record_controls(form)
        return form['fields'][0]

    def test_record_columns_validate_native_compiler_keys(self):
        field = self.record_field('table.create', 'columns', True)
        record = {'name': 'ID', 'type': 'BIGINT', 'nullable': False,
                  'primary_key': True, 'default': '42'}
        for source in ([record], json.dumps([record])):
            with self.subTest(source=source):
                admitted, error = ProviderVisualAdministration._validate_field(
                    field, source)
                self.assertIsNone(error)
                self.assertEqual([record], admitted)
        for invalid in ([], [{}], [{'name': 'ID'}],
                        [{**record, 'invented_option': True}],
                        [{**record, 'nullable': 'maybe'}], [42], {}):
            with self.subTest(invalid=invalid):
                _, error = ProviderVisualAdministration._validate_field(
                    field, invalid)
                self.assertIsNotNone(error)

    def test_record_constraints_hide_inapplicable_fields(self):
        field = self.record_field('table.create', 'constraints')
        record = {'kind': 'CHECK', 'expression': 'ID > 0',
                  'columns': [], 'references_table': '',
                  'references_columns': []}
        admitted, error = ProviderVisualAdministration._validate_field(
            field, [record])
        self.assertIsNone(error)
        self.assertEqual([{'kind': 'CHECK', 'expression': 'ID > 0'}], admitted)
        foreign = {'kind': 'FOREIGN KEY', 'columns': ['PARENT_ID'],
                   'references_table': 'PARENT', 'references_columns': ['ID']}
        admitted, error = ProviderVisualAdministration._validate_field(
            field, [foreign])
        self.assertIsNone(error)
        self.assertEqual([foreign], admitted)
        for invalid in ({**foreign, 'references_columns': [3]},
                        {**foreign, 'kind': 'INVENTED'}):
            _, error = ProviderVisualAdministration._validate_field(
                field, [invalid])
            self.assertIsNotNone(error)

    def test_constraint_object_control_validates_and_plans(self):
        field = self.record_field('constraint.create', 'properties', True)
        self.assertIn('object_editor', field)
        props = {'kind': 'FOREIGN KEY', 'columns': ['parent_id'],
                 'references_table': 'parents', 'references_columns': ['id']}
        for source in (props, json.dumps(props)):
            admitted, error = ProviderVisualAdministration._validate_field(
                field, source)
            self.assertIsNone(error)
            self.assertEqual(props, admitted)
        for invalid in ([], None, {}, {'kind': 'CHECK', 'expression': ''},
                        {'kind': 'UNIQUE', 'columns': []},
                        {**props, 'references_table': ''},
                        {**props, 'name': 'hidden_override'},
                        {**props, 'unsupported': True}):
            with self.subTest(invalid=invalid):
                _, error = ProviderVisualAdministration._validate_field(
                    field, invalid)
                self.assertIsNotNone(error)
        for administration, expected_quote in (
                (FIREBIRD_ADMINISTRATION, '"'),
                (MYSQL_ADMINISTRATION, '`'),
                (MARIADB_ADMINISTRATION, '`')):
            with self.subTest(engine=administration.dialect.engine_id):
                planned = administration.plan({
                    'resource_kind': 'constraint', 'operation_id': 'create',
                    'target_resource': None,
                    'draft': {'name': 'fk_parent', 'table': 'children',
                              'properties': props},
                    '_provider_route': self.route,
                })
                q = expected_quote
                self.assertIn(
                    f'ALTER TABLE {q}children{q} ADD CONSTRAINT '
                    f'{q}fk_parent{q} FOREIGN KEY ({q}parent_id{q}) '
                    f'REFERENCES {q}parents{q} ({q}id{q})',
                    str(planned['command_preview']))

    def test_record_rename_and_index_lists(self):
        for form_id, name, value in (
                ('table.alter', 'rename_columns', [{'from': 'A', 'to': 'B'}]),
                ('table.alter', 'drop_columns', ['A']),
                ('index.create', 'columns', ['A', 'B'])):
            with self.subTest(name=name):
                field = self.record_field(form_id, name)
                admitted, error = ProviderVisualAdministration._validate_field(
                    field, value)
                self.assertIsNone(error)
                self.assertEqual(value, admitted)
        field = self.record_field('table.alter', 'rename_columns')
        _, error = ProviderVisualAdministration._validate_field(
            field, [{'from': 'A', 'to': ''}])
        self.assertEqual('required', error['code'])

    def test_visual_records_reach_sqlite_native_ddl_and_constraints(self):
        field = self.record_field('table.create', 'columns', True)
        columns, error = ProviderVisualAdministration._validate_field(field, [
            {'name': 'id', 'type': 'INTEGER', 'primary_key': True},
            {'name': 'value', 'type': 'INTEGER', 'nullable': False},
        ])
        self.assertIsNone(error)
        constraints, error = ProviderVisualAdministration._validate_field(
            self.record_field('table.create', 'constraints'), [
                {'name': 'positive', 'kind': 'CHECK',
                 'expression': 'value > 0'},
            ])
        self.assertIsNone(error)
        created = self.admin.plan(request(self.route, 'create', {
            'name': 'visual_records', 'definition': '',
            'options': {'columns': columns, 'constraints': constraints},
        }))
        self.apply(created)
        connection = sqlite3.connect(self.route['database'])
        try:
            columns = connection.execute(
                'PRAGMA table_info(visual_records)').fetchall()
            self.assertEqual(['id', 'value'], [row[1] for row in columns])
            connection.execute('INSERT INTO visual_records VALUES (1, 5)')
            connection.commit()
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute('INSERT INTO visual_records VALUES (2, 0)')
            connection.rollback()
            self.assertEqual([(1, 5)], connection.execute(
                'SELECT * FROM visual_records').fetchall())
        finally:
            connection.close()

    def test_sequence_observation_quotes_names_and_never_advances(self):
        statements = []
        cursor = SimpleNamespace(execute=statements.append,
                                 fetchone=lambda: (9223372036854775807,))
        observed = _sequence_state(cursor, 'quoted"sequence')
        self.assertEqual('9223372036854775807', observed['current_value'])
        self.assertEqual([
            'SELECT GEN_ID("quoted""sequence", 0) FROM RDB$DATABASE',
        ], statements)
        cursor.fetchone = lambda: None
        self.assertFalse(_sequence_state(cursor, 'missing')['available'])

        def denied(_source):
            raise PermissionError('private server details')

        cursor.execute = denied
        observed = _sequence_state(cursor, 'restricted')
        self.assertFalse(observed['available'])
        self.assertEqual('PermissionError', observed['error_type'])
        self.assertNotIn('private server details', str(observed))

    def test_firebird_sequence_forms_preserve_decimal_int64(self):
        sequence = next(item for item in FIREBIRD_ADMINISTRATION.catalog(
            catalog_for_engine('firebird'))['objects']
            if item['resource_kind'] == 'sequence')
        forms = {item['operation_id']: item['form']['fields']
                 for item in sequence['operations']}
        create = {field['field_id']: field for field in forms['create']}
        alter = {field['field_id']: field for field in forms['alter']}
        self.assertEqual('text', create['start']['control'])
        self.assertEqual('text', alter['restart']['control'])
        self.assertEqual(['increment'],
                         alter['increment']['initial_value_path'])
        self.assertNotIn('cycle', create)
        self.assertNotIn('minimum', create)
        self.assertNotIn('maximum', create)

    def test_firebird_sequence_plan_validation_and_comments(self):
        target = {'resource_id': 'sequence:sample',
                  'resource_kind': 'sequence', 'display_name': 'sample',
                  'display_path': ['sample']}

        def plan(draft):
            return FIREBIRD_ADMINISTRATION.plan({
                'resource_kind': 'sequence', 'operation_id': 'alter',
                'target_resource': target, 'draft': draft,
                '_provider_route': {'database': '/sample.fdb'},
            })['command_preview']['statements']

        statements = plan({'restart': '9223372036854775807', 'increment': -2})
        self.assertEqual('ALTER SEQUENCE "sample" RESTART WITH '
                         '9223372036854775807 INCREMENT BY -2',
                         statements[0]['source'])
        self.assertEqual('ALTER SEQUENCE "sample" RESTART',
                         plan({'restart_initial': True})[0]['source'])
        self.assertEqual("COMMENT ON SEQUENCE \"sample\" IS 'Owner''s note'",
                         plan({'description': "Owner's note"})[0]['source'])
        self.assertEqual('COMMENT ON SEQUENCE "sample" IS NULL',
                         plan({'clear_description': True})[0]['source'])
        for draft in ({'restart': '9223372036854775808'},
                      {'restart': '-9223372036854775809'},
                      {'restart': '1.5'}, {'restart': True},
                      {'increment': 0}, {'increment': 2147483648},
                      {'increment': -2147483649},
                      {'restart_initial': True, 'restart': '10'},
                      {'clear_description': True, 'description': 'x'},
                      {'restart_initial': False}):
            with self.subTest(draft=draft):
                with self.assertRaises(RelationalClientError):
                    plan(draft)

    def test_firebird_database_create_form_requires_server_path(self):
        database = next(
            item for item in FIREBIRD_ADMINISTRATION.catalog(
                catalog_for_engine('firebird')
            )['objects']
            if item['resource_kind'] == 'database'
        )
        create = next(
            item for item in database['operations']
            if item['operation_id'] == 'create'
        )
        self.assertEqual('firebird_database_create', create['form_id'])
        self.assertEqual(
            [
                'database_path', 'page_size', 'default_charset',
                'sql_dialect', 'forced_writes', 'reserve_space',
            ],
            [field['field_id'] for field in create['form']['fields']],
        )

    def test_firebird_database_service_tasks_have_distinct_exact_forms(self):
        database = next(
            item for item in FIREBIRD_ADMINISTRATION.catalog(
                catalog_for_engine('firebird')
            )['objects']
            if item['resource_kind'] == 'database'
        )
        expected = {
            'backup_logical': 'firebird_backup_logical',
            'restore_logical': 'firebird_restore_logical',
            'backup_physical': 'firebird_backup_physical',
            'restore_physical': 'firebird_restore_physical',
            'validate_database': 'firebird_validate_database',
            'repair_database': 'firebird_repair_database',
            'sweep_database': 'firebird_sweep_database',
            'database_statistics': 'firebird_database_statistics',
            'shutdown_database': 'firebird_shutdown_database',
            'bring_online': 'firebird_bring_online',
            'set_page_cache_size': 'firebird_set_page_cache_size',
            'set_sweep_interval': 'firebird_set_sweep_interval',
            'set_space_reservation': 'firebird_set_space_reservation',
            'set_write_mode': 'firebird_set_write_mode',
            'set_access_mode': 'firebird_set_access_mode',
            'set_sql_dialect': 'firebird_set_sql_dialect',
            'activate_shadow': 'firebird_activate_shadow',
            'remove_linger': 'firebird_remove_linger',
            'fixup_database': 'firebird_fixup_database',
            'set_replica_mode': 'firebird_set_replica_mode',
            'upgrade_database': 'firebird_upgrade_database',
        }
        operations = {
            item['operation_id']: item for item in database['operations']
        }
        for operation_id, form_id in expected.items():
            self.assertEqual(form_id, operations[operation_id]['form_id'])
            self.assertEqual(
                'server_service', operations[operation_id]['workspace_scope']
            )
            self.assertTrue(FIREBIRD_ADMINISTRATION.supports(
                'database', operation_id
            ))

    def test_firebird_repair_form_exposes_only_valid_gfix_actions(self):
        database = next(
            item for item in FIREBIRD_ADMINISTRATION.catalog(
                catalog_for_engine('firebird')
            )['objects']
            if item['resource_kind'] == 'database'
        )
        operation = next(
            item for item in database['operations']
            if item['operation_id'] == 'repair_database'
        )
        field = operation['form']['fields'][0]
        self.assertEqual('repair_action', field['field_id'])
        self.assertEqual('select', field['control'])
        self.assertEqual({
            'VALIDATE_DB', 'CORRUPTION_CHECK', 'REPAIR',
            'KILL_SHADOWS', 'ICU', 'UPGRADE_DB',
        }, {item['value'] for item in field['options']})
        invalid = FIREBIRD_ADMINISTRATION.validate({
            'engine_id': 'firebird', 'resource_kind': 'database',
            'operation_id': 'repair_database',
            'draft': {'repair_action': 'CHECK_DB'},
        })
        self.assertEqual(
            'invalid_firebird_service_option',
            invalid['errors'][0]['code'],
        )

    def test_firebird_database_alter_has_exact_structured_form_and_sql(self):
        database = next(
            item for item in FIREBIRD_ADMINISTRATION.catalog(
                catalog_for_engine('firebird')
            )['objects']
            if item['resource_kind'] == 'database'
        )
        alter = next(
            item for item in database['operations']
            if item['operation_id'] == 'alter'
        )
        self.assertEqual('firebird_database_alter', alter['form_id'])
        self.assertEqual('engine-profile', alter['form_authority'])
        self.assertEqual([
            'default_charset', 'linger_seconds', 'drop_linger',
            'default_sql_security',
        ], [field['field_id'] for field in alter['form']['fields']])
        target = {
            'resource_id': 'database:inventory.fdb',
            'resource_kind': 'database',
            'display_name': 'inventory.fdb',
            'display_path': ['inventory.fdb'],
        }
        plan = FIREBIRD_ADMINISTRATION.plan({
            'resource_kind': 'database',
            'operation_id': 'alter',
            'target_resource': target,
            'draft': {
                'default_charset': 'UTF8',
                'linger_seconds': 15,
                'default_sql_security': 'INVOKER',
            },
            '_provider_route': {
                'host': 'firebird.example', 'port': 3050,
                'database': '/srv/firebird/inventory.fdb',
            },
        })
        self.assertEqual(
            'ALTER DATABASE SET DEFAULT CHARACTER SET "UTF8" '
            'SET LINGER TO 15 SET DEFAULT SQL SECURITY INVOKER',
            plan['command_preview']['statements'][0]['source'],
        )

    def test_firebird_database_alter_rejects_conflicting_linger_changes(self):
        result = FIREBIRD_ADMINISTRATION.validate({
            'resource_kind': 'database',
            'operation_id': 'alter',
            'draft': {'linger_seconds': 10, 'drop_linger': True},
        })
        self.assertIn(
            'conflicting_firebird_linger_change',
            {item['code'] for item in result['errors']},
        )

    def test_firebird_generated_sql_tasks_are_exact_contract_obligations(self):
        tasks = set(FIREBIRD_ADMINISTRATION.dialect_task_ids())
        self.assertIn('visual_admin.table.create', tasks)
        self.assertIn('visual_admin.table.update', tasks)
        self.assertIn('visual_admin.user.alter', tasks)
        self.assertNotIn('visual_admin.database.backup_logical', tasks)
        self.assertNotIn('visual_admin.database.set_page_cache_size', tasks)
        self.assertNotIn('visual_admin.database.upgrade_database', tasks)
        self.assertNotIn('visual_admin.database.create', tasks)
        self.assertNotIn('visual_admin.database.drop', tasks)

    def test_firebird_database_drop_uses_native_driver_not_sql(self):
        route = {
            'host': 'firebird.example', 'port': 3050,
            'database': '/srv/firebird/inventory.fdb',
            'route_id': 'firebird-drop-test',
        }
        target = {
            'resource_id': 'database:inventory.fdb',
            'resource_kind': 'database',
            'display_name': 'inventory.fdb',
            'display_path': ['inventory.fdb'],
        }
        plan = FIREBIRD_ADMINISTRATION.plan({
            'resource_kind': 'database',
            'operation_id': 'drop',
            'target_resource': target,
            'draft': {
                'confirmation': '/srv/firebird/inventory.fdb',
            },
            '_provider_route': route,
        })
        self.assertEqual(
            'firebird-drop-database',
            plan['command_preview']['driver_operation'],
        )
        self.assertEqual([], plan['command_preview']['statements'])

        class DropClient:
            @staticmethod
            def drop_database(request, database, operation):
                return {
                    'route': request['route']['route_id'],
                    'operation': operation,
                    'database': database,
                }

        result = FIREBIRD_ADMINISTRATION.apply(DropClient(), {
            'provider_payload': plan['provider_payload'],
        })
        self.assertEqual(
            'firebird-drop-database',
            result['driver_observation']['operation'],
        )
        self.assertEqual(
            '/srv/firebird/inventory.fdb',
            result['driver_observation']['database'],
        )

    def test_firebird_database_drop_requires_exact_routed_database(self):
        route = {
            'host': 'firebird.example', 'port': 3050,
            'database': '/srv/firebird/inventory.fdb',
        }
        valid = FIREBIRD_ADMINISTRATION.validate({
            'resource_kind': 'database',
            'operation_id': 'drop',
            'target_resource': {
                'resource_kind': 'database',
                'display_name': 'inventory.fdb',
            },
            'draft': {'confirmation': '/srv/firebird/inventory.fdb'},
            '_provider_route': route,
        })
        self.assertEqual([], valid['errors'])

        invalid = FIREBIRD_ADMINISTRATION.validate({
            'resource_kind': 'database',
            'operation_id': 'drop',
            'target_resource': {
                'resource_kind': 'database',
                'display_name': 'inventory.fdb',
            },
            'draft': {
                'confirmation': 'inventory.fdb', 'cascade': False,
            },
            '_provider_route': route,
        })
        self.assertEqual(
            {
                'firebird_database_confirmation_mismatch',
                'unknown_firebird_database_drop_option',
            },
            {item['code'] for item in invalid['errors']},
        )

    def test_firebird_logical_backup_plan_uses_service_runner_not_sql(self):
        route = {
            'host': 'firebird.example', 'port': 3050,
            'database': '/srv/firebird/inventory.fdb',
            'route_id': 'firebird-service-test',
        }
        target = {
            'resource_id': 'database:inventory.fdb',
            'resource_kind': 'database',
            'display_name': 'inventory.fdb',
            'display_path': ['inventory.fdb'],
        }
        plan = FIREBIRD_ADMINISTRATION.plan({
            'resource_kind': 'database',
            'operation_id': 'backup_logical',
            'target_resource': target,
            'draft': {
                'backup_file': '/srv/backups/inventory.fbk',
                'backup_flags': ['ZIP'], 'verbose': True,
            },
            '_provider_route': route,
        })
        self.assertEqual(
            'firebird-service',
            plan['command_preview']['driver_operation'],
        )
        self.assertEqual([], plan['command_preview']['statements'])

        class ServiceClient:
            @staticmethod
            def run_server_operation(request, operation, database, options):
                return {
                    'route': request['route']['route_id'],
                    'operation': operation, 'database': database,
                    'backup_file': options['backup_file'],
                }

        result = FIREBIRD_ADMINISTRATION.apply(ServiceClient(), {
            'provider_payload': plan['provider_payload'],
        })
        observation = result['driver_observation']
        self.assertEqual('backup_logical', observation['operation'])
        self.assertEqual(
            '/srv/firebird/inventory.fdb', observation['database']
        )

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.route = {
            'database': str(Path(self.temporary.name) / 'admin.sqlite'),
            'filesystem_root': self.temporary.name,
            'route_id': 'local-test',
        }
        self.client, self.admin = client_and_admin()
        self.target = {
            'resource_id': 'table:main:widgets',
            'resource_kind': 'table',
            'display_name': 'widgets',
            'display_path': ['main', 'widgets'],
        }

    def tearDown(self):
        self.client.close()
        self.temporary.cleanup()

    def apply(self, native_plan):
        return self.admin.apply(self.client, {
            'provider_payload': native_plan['provider_payload'],
        })

    def test_view_rows_are_browsable_but_never_given_edit_identities(self):
        connection = sqlite3.connect(self.route['database'])
        try:
            connection.executescript(
                'CREATE TABLE source_rows(id INTEGER PRIMARY KEY, '
                'name TEXT NOT NULL);'
                "INSERT INTO source_rows VALUES (1, 'visible');"
                'CREATE VIEW visible_rows AS '
                'SELECT id, name FROM source_rows;'
            )
            connection.commit()
        finally:
            connection.close()
        page = self.admin.read_rows(self.client, {
            '_provider_route': self.route,
            'target_resource': {
                'resource_id': 'view:main:visible_rows',
                'resource_kind': 'view',
                'display_name': 'visible_rows',
                'display_path': ['main', 'visible_rows'],
            },
            'limit': 50,
        })
        self.assertEqual('read-only-view', page['identity_policy'])
        self.assertFalse(page['editable'])
        self.assertEqual('visible', page['rows'][0]['values']['name'])
        self.assertIsNone(page['rows'][0]['identity_token'])

    def test_structured_ddl_and_grid_crud_execute_without_raw_commands(self):
        created = self.admin.plan(request(
            self.route, 'create', {
                'name': 'widgets',
                'definition': '',
                'options': {'columns': [
                    {
                        'name': 'id', 'type': 'INTEGER',
                        'nullable': False, 'primary_key': True,
                    },
                    {'name': 'name', 'type': 'TEXT', 'nullable': False},
                ]},
            },
        ))
        self.assertNotIn(str(self.route['database']), str(
            created['command_preview']
        ))
        self.assertTrue(self.apply(created)['commit_requested'])

        column = self.admin.plan({
            'resource_kind': 'column', 'operation_id': 'create',
            'target_resource': None,
            'draft': {
                'name': 'note', 'table': 'main.widgets',
                'data_type': 'TEXT', 'nullable': True,
                'default': '', 'primary_key': False,
            },
            '_provider_route': self.route,
        })
        self.apply(column)
        column_target = {
            'resource_id': 'column:main:widgets:note',
            'resource_kind': 'column', 'display_name': 'note',
            'display_path': ['main', 'widgets', 'note'],
        }
        renamed = self.admin.plan({
            'resource_kind': 'column', 'operation_id': 'rename',
            'target_resource': column_target,
            'draft': {'new_name': 'comment'},
            '_provider_route': self.route,
        })
        self.apply(renamed)
        column_target['display_name'] = 'comment'
        column_target['display_path'][-1] = 'comment'
        dropped = self.admin.plan({
            'resource_kind': 'column', 'operation_id': 'drop',
            'target_resource': column_target,
            'draft': {'cascade': False, 'confirmation': 'drop-column'},
            '_provider_route': self.route,
        })
        self.apply(dropped)

        virtual = self.admin.plan({
            'resource_kind': 'fts-table', 'operation_id': 'create',
            'target_resource': None,
            'draft': {
                'name': 'widget_search', 'module': 'fts5',
                'columns': ['title', 'content'],
            },
            '_provider_route': self.route,
        })
        self.apply(virtual)
        virtual_target = {
            'resource_id': 'fts-table:main:widget_search',
            'resource_kind': 'fts-table', 'display_name': 'widget_search',
            'display_path': ['main', 'widget_search'],
        }
        dropped_virtual = self.admin.plan({
            'resource_kind': 'fts-table', 'operation_id': 'drop',
            'target_resource': virtual_target,
            'draft': {'cascade': False, 'confirmation': 'drop-virtual'},
            '_provider_route': self.route,
        })
        self.apply(dropped_virtual)

        inserted = self.admin.plan(request(
            self.route, 'insert', {
                'values': {'id': 1, 'name': 'first'}, 'options': {},
            }, self.target,
        ))
        self.assertTrue(self.apply(inserted)['accepted'])

        page = self.admin.read_rows(self.client, {
            '_provider_route': self.route,
            'target_resource': self.target,
            'limit': 50,
        })
        self.assertTrue(page['editable'])
        self.assertEqual('first', page['rows'][0]['values']['name'])
        token = page['rows'][0]['identity_token']

        updated = self.admin.plan(request(
            self.route, 'update', {
                'selector': {'identity_token': token},
                'changes': {'name': 'second'},
                'concurrency_token': token,
            }, self.target,
        ))
        self.assertEqual(1, updated['provider_payload']['compiled'][
            'statements'
        ][0]['expected_rowcount'])
        self.assertEqual(
            ('second', 1, 'first'),
            updated['provider_payload']['compiled']['statements'][0][
                'parameters'
            ],
        )
        self.apply(updated)

        page = self.admin.read_rows(self.client, {
            '_provider_route': self.route,
            'target_resource': self.target,
        })
        self.assertEqual('second', page['rows'][0]['values']['name'])
        token = page['rows'][0]['identity_token']
        deleted = self.admin.plan(request(
            self.route, 'delete', {
                'selector': {'identity_token': token},
                'concurrency_token': token,
                'confirmation': 'provider-row-delete',
            }, self.target,
        ))
        self.apply(deleted)
        page = self.admin.read_rows(self.client, {
            '_provider_route': self.route,
            'target_resource': self.target,
        })
        self.assertEqual([], page['rows'])

    def test_session_bound_grid_mutation_waits_for_provider_commit(self):
        created = self.admin.plan(request(
            self.route, 'create', {
                'name': 'widgets', 'definition': '',
                'options': {'columns': [
                    {
                        'name': 'id', 'type': 'INTEGER',
                        'nullable': False, 'primary_key': True,
                    },
                    {'name': 'name', 'type': 'TEXT', 'nullable': False},
                ]},
            },
        ))
        self.apply(created)
        writer = self.client._connect({'route': self.route})
        observer = sqlite3.connect(self.route['database'])
        try:
            inserted = self.admin.plan(request(
                self.route, 'insert', {
                    'values': {'id': 1, 'name': 'staged'}, 'options': {},
                }, self.target,
            ))
            result = self.admin.apply(self.client, {
                'provider_payload': inserted['provider_payload'],
            }, connection=writer)

            self.assertTrue(result['staged_in_provider_session'])
            self.assertFalse(result['commit_requested'])
            self.assertEqual(
                0,
                observer.execute(
                    'SELECT COUNT(*) FROM widgets WHERE id = 1'
                ).fetchone()[0],
            )
            writer.commit()
            self.assertEqual(
                1,
                observer.execute(
                    'SELECT COUNT(*) FROM widgets WHERE id = 1'
                ).fetchone()[0],
            )
        finally:
            observer.close()
            self.client._forget_and_close(writer)

    def test_row_pages_use_opaque_provider_continuations_and_cancel(self):
        with sqlite3.connect(self.route['database']) as connection:
            connection.execute(
                'CREATE TABLE widgets(id INTEGER PRIMARY KEY, name TEXT)'
            )
            connection.executemany(
                'INSERT INTO widgets(id, name) VALUES(?, ?)',
                ((value, f'row-{value}') for value in range(1, 7)),
            )
        request_value = {
            '_provider_route': self.route,
            'target_resource': self.target,
            'limit': 2,
        }
        first = self.admin.read_rows(self.client, request_value)
        self.assertEqual([1, 2], [
            row['values']['id'] for row in first['rows']
        ])
        self.assertFalse(first['complete'])
        self.assertIsInstance(first['continuation'], str)
        self.assertEqual(
            first['continuation'], str(uuid.UUID(first['continuation']))
        )

        second = self.admin.read_rows(self.client, {
            **request_value, 'continuation': first['continuation'],
        })
        self.assertEqual([3, 4], [
            row['values']['id'] for row in second['rows']
        ])
        self.assertTrue(self.admin.cancel_rows({
            '_provider_route': self.route,
            'continuation': second['continuation'],
        })['cancelled'])
        with self.assertRaisesRegex(
                RelationalClientError, 'unavailable or mismatched'):
            self.admin.read_rows(self.client, {
                **request_value, 'continuation': second['continuation'],
            })

    def test_sqlite_database_maintenance_forms_are_provider_specific(self):
        database = next(
            item for item in self.admin.catalog(
                catalog_for_engine('sqlite')
            )['objects'] if item['resource_kind'] == 'database'
        )
        operations = {
            item['operation_id']: item for item in database['operations']
        }
        expected = {
            'backup', 'restore', 'integrity_check', 'quick_check',
            'foreign_key_check', 'vacuum', 'incremental_vacuum',
            'optimize', 'analyze', 'reindex', 'wal_checkpoint',
        }
        self.assertTrue(expected.issubset(operations))
        self.assertEqual(
            'sqlite_database_backup', operations['backup']['form']['form_id']
        )
        self.assertEqual(
            'sqlite_wal_checkpoint',
            operations['wal_checkpoint']['form']['form_id'],
        )

    def test_sqlite_native_backup_restore_and_maintenance_execute(self):
        with sqlite3.connect(self.route['database']) as connection:
            connection.execute(
                'CREATE TABLE restore_probe('
                'id INTEGER PRIMARY KEY, value TEXT)'
            )
            connection.execute(
                "INSERT INTO restore_probe VALUES(1, 'before-backup')"
            )
        target = {
            'resource_id': 'database:main',
            'resource_kind': 'database',
            'display_name': 'main',
            'display_path': ['main'],
        }

        def plan(operation, draft=None):
            request_value = {
                'resource_kind': 'database',
                'operation_id': operation,
                'target_resource': target,
                'draft': draft or {},
                '_provider_route': self.route,
            }
            self.assertEqual([], self.admin.validate(request_value)['errors'])
            return self.admin.plan(request_value)

        backup = Path(self.temporary.name) / 'admin-backup.sqlite'
        backed_up = self.apply(plan('backup', {
            'backup_path': str(backup), 'overwrite': False,
        }))
        self.assertEqual(
            'sqlite-online-backup',
            backed_up['driver_observation']['operation'],
        )
        with sqlite3.connect(self.route['database']) as connection:
            connection.execute(
                "UPDATE restore_probe SET value = 'after-backup' WHERE id = 1"
            )
        restored = self.apply(plan('restore', {
            'backup_path': str(backup),
            'confirmation': self.route['database'],
        }))
        self.assertEqual(
            'sqlite-online-restore',
            restored['driver_observation']['operation'],
        )
        with sqlite3.connect(self.route['database']) as connection:
            value = connection.execute(
                'SELECT value FROM restore_probe WHERE id = 1'
            ).fetchone()[0]
        self.assertEqual('before-backup', value)

        native_sources = {
            'integrity_check': ('PRAGMA integrity_check(10)', {
                'max_errors': 10,
            }),
            'quick_check': ('PRAGMA quick_check(10)', {'max_errors': 10}),
            'foreign_key_check': ('PRAGMA foreign_key_check', {}),
            'vacuum': ('VACUUM', {}),
            'incremental_vacuum': (
                'PRAGMA incremental_vacuum(0)', {'pages': 0},
            ),
            'optimize': ('PRAGMA optimize', {}),
            'analyze': ('ANALYZE', {}),
            'reindex': ('REINDEX', {}),
            'wal_checkpoint': (
                'PRAGMA wal_checkpoint(PASSIVE)', {'mode': 'PASSIVE'},
            ),
        }
        for operation, (source, draft) in native_sources.items():
            native_plan = plan(operation, draft)
            self.assertEqual(
                source,
                native_plan['provider_payload']['compiled'][
                    'statements'
                ][0]['source'],
            )
            self.assertTrue(self.apply(native_plan)['accepted'])

    def test_complete_raw_ddl_and_unissued_row_selectors_are_rejected(self):
        validation = self.admin.validate(request(
            self.route, 'create', {
                'name': 'widgets',
                'definition': 'CREATE TABLE bypass(id INTEGER)',
                'options': {},
            },
        ))
        self.assertEqual(
            'complete_native_command_forbidden',
            validation['errors'][0]['code'],
        )
        with self.assertRaisesRegex(
            RelationalClientError, 'provider-issued row identity token'
        ):
            self.admin.plan(request(
                self.route, 'update', {
                    'selector': {'id': 1}, 'changes': {'name': 'unsafe'},
                }, self.target,
            ))

    def test_embedded_database_creation_stays_in_approved_root(self):
        route = {
            **self.route,
            'database_create_root': self.temporary.name,
        }
        created = self.admin.plan({
            'engine_id': 'sqlite',
            'resource_kind': 'database',
            'operation_id': 'create',
            'target_resource': None,
            'draft': {'name': 'created', 'options': {}},
            '_provider_route': route,
        })
        self.assertEqual(
            'embedded-create-database',
            created['command_preview']['driver_operation'],
        )
        self.assertNotIn(self.temporary.name, str(
            created['command_preview']
        ))
        result = self.apply(created)
        self.assertEqual(
            str(Path(self.temporary.name) / 'created.sqlite'),
            result['endpoint_database_target']['database'],
        )
        self.assertTrue(
            (Path(self.temporary.name) / 'created.sqlite').exists()
        )
        with self.assertRaisesRegex(
            RelationalClientError, 'safe unqualified file name'
        ):
            self.admin.plan({
                'engine_id': 'sqlite',
                'resource_kind': 'database',
                'operation_id': 'create',
                'target_resource': None,
                'draft': {'name': '../escape', 'options': {}},
                '_provider_route': route,
            })

    def test_sqlite_database_file_settings_and_delete_are_provider_owned(
            self):
        route = {
            **self.route,
            'database_create_root': self.temporary.name,
        }
        created = self.admin.plan({
            'engine_id': 'sqlite',
            'resource_kind': 'database',
            'operation_id': 'create',
            'target_resource': None,
            'draft': {
                'name': 'configured', 'page_size': '8192',
                'encoding': 'UTF-8', 'auto_vacuum': 'FULL',
                'application_id': 1732526414, 'user_version': 7,
            },
            '_provider_route': route,
        })
        created_result = self.apply(created)
        database = Path(self.temporary.name) / 'configured.sqlite'
        self.assertEqual(database, Path(
            created_result['endpoint_database_target']['database']
        ))
        with sqlite3.connect(database) as connection:
            self.assertEqual(8192, connection.execute(
                'PRAGMA page_size'
            ).fetchone()[0])
            self.assertEqual(1, connection.execute(
                'PRAGMA auto_vacuum'
            ).fetchone()[0])
            self.assertEqual(7, connection.execute(
                'PRAGMA user_version'
            ).fetchone()[0])

        target = {
            'resource_id': 'database-target:configured',
            'resource_kind': 'database',
            'display_name': 'configured.sqlite',
            'display_path': ['configured.sqlite'],
            'extensions': {'cdeadmin': {
                'database_target_id': 'configured-target',
                'native_name': str(database),
            }},
        }
        configured_route = {
            **route,
            'database': str(database),
        }
        altered = self.admin.plan({
            'engine_id': 'sqlite',
            'resource_kind': 'database',
            'operation_id': 'alter',
            'target_resource': target,
            'draft': {
                'journal_mode': 'DELETE', 'synchronous': 'FULL',
                'user_version': 8,
            },
            '_provider_route': configured_route,
        })
        self.assertEqual([
            'PRAGMA journal_mode = DELETE',
            'PRAGMA synchronous = FULL',
            'PRAGMA user_version = 8',
        ], [item['source'] for item in altered[
            'provider_payload'
        ]['compiled']['statements']])
        self.apply(altered)
        with sqlite3.connect(database) as connection:
            self.assertEqual(8, connection.execute(
                'PRAGMA user_version'
            ).fetchone()[0])

        dropped = self.admin.plan({
            'engine_id': 'sqlite',
            'resource_kind': 'database',
            'operation_id': 'drop',
            'target_resource': target,
            'draft': {'confirmation': str(database)},
            '_provider_route': configured_route,
        })
        self.assertEqual(
            'embedded-drop-database',
            dropped['command_preview']['driver_operation'],
        )
        dropped_result = self.apply(dropped)
        self.assertFalse(database.exists())
        self.assertEqual(
            'configured-target',
            dropped_result['dropped_endpoint_database_target']['target_id'],
        )

    def test_user_password_is_redacted_from_validation_and_plan(self):
        context = SimpleNamespace(
            endpoint_id='endpoint-test', mode='legacy_native',
            runtime_verification_state='verified',
            verified_runtime_family='mysql',
            declared_runtime_family='mysql',
            effective_permissions=frozenset({
                'data_read', 'data_write', 'administer',
            }),
        )
        visual = ProviderVisualAdministration(
            context, Permissions(), 'mysql', '9.7.0',
            AdministrationClient(MYSQL_ADMINISTRATION),
        )
        secret = "never-render-this'password"
        value = {
            'resource_kind': 'user',
            'operation_id': 'create',
            'target_resource': None,
            'draft': {
                'name': 'operator', 'host': 'localhost',
                'password': secret, 'plugin': '', 'active': True,
                'administrator': False,
            },
            '_provider_route': {'route_id': 'trusted'},
        }
        validation = visual.validate(value)
        self.assertEqual('<redacted>', validation['draft']['password'])
        plan = visual.plan(value)
        self.assertNotIn(secret, str(plan))
        self.assertIn('<redacted>', str(plan))

    def test_visual_row_plan_is_bound_to_native_provider_session(self):
        context = SimpleNamespace(
            endpoint_id='endpoint-test', mode='legacy_native',
            runtime_verification_state='verified',
            verified_runtime_family='sqlite',
            declared_runtime_family='sqlite',
            effective_permissions=frozenset({
                'data_read', 'data_write', 'administer', 'filesystem',
            }),
        )
        native_handle = object()

        class SessionClient(AdministrationClient):
            def __init__(self):
                super().__init__(SQLITE_ADMINISTRATION)
                self.received_handle = None

            def apply_admin_operation(self, request):
                self.received_handle = request.get(
                    '_provider_session_handle'
                )
                return {
                    'accepted': True,
                    'staged_in_provider_session': True,
                }

        client = SessionClient()
        visual = ProviderVisualAdministration(
            context, Permissions(), 'sqlite', sqlite3.sqlite_version, client,
        )
        value = {
            'resource_kind': 'table', 'operation_id': 'insert',
            'target_resource': self.target,
            'draft': {'values': {'id': 1, 'name': 'staged'}, 'options': {}},
            'session_id': 'grid-session',
            '_provider_route': self.route,
        }

        plan = visual.plan(value)
        result = visual.apply({
            'plan_id': plan['plan_id'],
            'plan_digest': plan['plan_digest'],
            'session_id': 'grid-session',
        }, {
            'session_id': 'grid-session',
            'session_handle': native_handle,
        })

        self.assertIs(native_handle, client.received_handle)
        self.assertTrue(
            result['provider_result']['staged_in_provider_session']
        )
        self.assertNotIn('session_handle', str(result))

    def test_firebird_user_and_role_plans_use_native_structured_syntax(self):
        route = {
            'database': 'server:/srv/firebird/current.fdb',
            'route_id': 'firebird-test',
        }
        user = FIREBIRD_ADMINISTRATION.plan({
            'resource_kind': 'user', 'operation_id': 'create',
            'target_resource': None,
            'draft': {
                'name': 'OPERATOR', 'password': 'secret-value',
                'host': '%', 'plugin': 'Srp256', 'active': True,
                'administrator': True,
            },
            '_provider_route': route,
        })
        preview = str(user['command_preview'])
        self.assertIn('GRANT ADMIN ROLE', preview)
        self.assertNotIn('secret-value', preview)
        role = FIREBIRD_ADMINISTRATION.plan({
            'resource_kind': 'role', 'operation_id': 'create',
            'target_resource': None,
            'draft': {
                'name': 'DATA_ADMIN',
                'system_privileges': ['CREATE_DATABASE', 'DROP_DATABASE'],
            },
            '_provider_route': route,
        })
        self.assertIn('SET SYSTEM PRIVILEGES TO', str(
            role['command_preview']
        ))
        trigger = FIREBIRD_ADMINISTRATION.plan({
            'resource_kind': 'trigger', 'operation_id': 'create',
            'target_resource': None,
            'draft': {
                'name': 'BI_WIDGETS', 'parent': '', 'table': 'WIDGETS',
                'timing': 'BEFORE', 'events': ['INSERT', 'UPDATE'],
                'active': True, 'position': 10,
                'body': 'BEGIN NEW.ID = NEXT VALUE FOR WIDGET_SEQ; END',
            },
            '_provider_route': route,
        })
        trigger_source = trigger['command_preview']['statements'][0]['source']
        self.assertIn(
            'FOR "WIDGETS" ACTIVE BEFORE INSERT OR UPDATE POSITION 10 AS',
            trigger_source,
        )
        procedure = FIREBIRD_ADMINISTRATION.plan({
            'resource_kind': 'procedure', 'operation_id': 'create',
            'target_resource': None,
            'draft': {
                'name': 'GET_WIDGET', 'parent': '',
                'parameters': [{'name': 'P_ID', 'type': 'BIGINT'}],
                'return_parameters': [
                    {'name': 'P_NAME', 'type': 'VARCHAR(100)'},
                ],
                'returns': '', 'body': 'BEGIN SUSPEND; END',
            },
            '_provider_route': route,
        })
        self.assertIn('RETURNS ("P_NAME" VARCHAR(100)) AS', str(
            procedure['command_preview']
        ))
        package = FIREBIRD_ADMINISTRATION.plan({
            'resource_kind': 'package', 'operation_id': 'create',
            'target_resource': None,
            'draft': {
                'name': 'WIDGET_API', 'parent': '',
                'header': 'BEGIN PROCEDURE P; END',
                'body': 'BEGIN PROCEDURE P AS BEGIN END END',
            },
            '_provider_route': route,
        })
        self.assertEqual(2, len(
            package['command_preview']['statements']
        ))
        publication = FIREBIRD_ADMINISTRATION.plan({
            'resource_kind': 'publication', 'operation_id': 'alter',
            'target_resource': {
                'resource_kind': 'publication',
                'resource_id': 'publication:RDB$DEFAULT',
                'display_name': 'RDB$DEFAULT',
                'display_path': ['RDB$DEFAULT'],
            },
            'draft': {
                'enabled': True,
                'include_tables': ['WIDGETS'],
                'exclude_tables': ['AUDIT_LOG'],
            },
            '_provider_route': route,
        })
        publication_sources = [
            item['source']
            for item in publication['command_preview']['statements']
        ]
        self.assertEqual(
            'ALTER DATABASE ENABLE PUBLICATION', publication_sources[0]
        )
        self.assertIn(
            'INCLUDE TABLE "WIDGETS" TO PUBLICATION',
            publication_sources[1],
        )
        self.assertIn(
            'EXCLUDE TABLE "AUDIT_LOG" FROM PUBLICATION',
            publication_sources[2],
        )
        with self.assertRaisesRegex(
            RelationalClientError, 'cannot be included and excluded'
        ):
            FIREBIRD_ADMINISTRATION.plan({
                'resource_kind': 'publication', 'operation_id': 'alter',
                'target_resource': {
                    'resource_kind': 'publication',
                    'resource_id': 'publication:RDB$DEFAULT',
                    'display_name': 'RDB$DEFAULT',
                    'display_path': ['RDB$DEFAULT'],
                },
                'draft': {
                    'enabled': True,
                    'include_tables': ['WIDGETS'],
                    'exclude_tables': ['WIDGETS'],
                },
                '_provider_route': route,
            })

    def test_firebird_specialized_metadata_plans_match_native_ddl(self):
        route = {
            'host': 'firebird.example', 'port': 3060,
            'database': '/srv/firebird/current.fdb',
            'route_id': 'firebird-metadata-test',
        }
        database = FIREBIRD_ADMINISTRATION.plan({
            'resource_kind': 'database', 'operation_id': 'create',
            'target_resource': None, 'draft': {'name': 'inventory'},
            '_provider_route': route,
        })
        self.assertEqual(
            'firebird.example/3060:/srv/firebird/inventory.fdb',
            database['provider_payload']['compiled']['database'],
        )
        self.assertEqual({
            'page_size': 8192,
            'default_charset': 'UTF8',
            'sql_dialect': 3,
            'forced_writes': True,
            'reserve_space': True,
        }, database['provider_payload']['compiled']['create_options'])
        domain_target = {
            'resource_kind': 'domain', 'display_name': 'D_QUANTITY',
            'display_path': ['D_QUANTITY'],
        }
        domain = FIREBIRD_ADMINISTRATION.plan({
            'resource_kind': 'domain', 'operation_id': 'alter',
            'target_resource': domain_target,
            'draft': {'data_type': 'BIGINT'},
            '_provider_route': route,
        })
        self.assertIn(
            'ALTER DOMAIN "D_QUANTITY" TYPE BIGINT',
            str(domain['command_preview']),
        )
        renamed = FIREBIRD_ADMINISTRATION.plan({
            'resource_kind': 'domain', 'operation_id': 'rename',
            'target_resource': domain_target,
            'draft': {'new_name': 'D_AMOUNT'},
            '_provider_route': route,
        })
        self.assertIn(
            'ALTER DOMAIN "D_QUANTITY" TO "D_AMOUNT"',
            str(renamed['command_preview']),
        )
        exception = FIREBIRD_ADMINISTRATION.plan({
            'resource_kind': 'exception', 'operation_id': 'alter',
            'target_resource': {
                'resource_kind': 'exception',
                'display_name': 'E_INVALID',
                'display_path': ['E_INVALID'],
            },
            'draft': {'message': 'Replacement message'},
            '_provider_route': route,
        })
        self.assertEqual(
            "ALTER EXCEPTION \"E_INVALID\" 'Replacement message'",
            exception['command_preview']['statements'][0]['source'],
        )
        index = FIREBIRD_ADMINISTRATION.plan({
            'resource_kind': 'index', 'operation_id': 'alter',
            'target_resource': {
                'resource_kind': 'index', 'display_name': 'IX_WIDGETS',
                'display_path': ['WIDGETS', 'IX_WIDGETS'],
            },
            'draft': {'active': False}, '_provider_route': route,
        })
        self.assertIn(
            'ALTER INDEX "IX_WIDGETS" INACTIVE',
            str(index['command_preview']),
        )
        self.assertFalse(FIREBIRD_ADMINISTRATION.supports(
            'table', 'rename'
        ))
        self.assertFalse(FIREBIRD_ADMINISTRATION.supports(
            'sequence', 'rename'
        ))

    def test_optional_rowcount_failure_does_not_overturn_successful_ddl(self):
        class Cursor:
            description = None

            def execute(self, _source, _parameters=()):
                return self

            @property
            def rowcount(self):
                raise RuntimeError('optional statement statistics refused')

            @staticmethod
            def close():
                return None

        class Connection:
            def cursor(self):
                return Cursor()

            @staticmethod
            def commit():
                return None

            @staticmethod
            def close():
                return None

        connection = Connection()
        client = SimpleNamespace(
            _connect=lambda _request: connection,
            _safe_close=lambda value: value.close(),
            _forget_and_close=lambda value: value.close(),
        )
        result = FIREBIRD_ADMINISTRATION.apply(client, {
            'provider_payload': {
                'route': {'database': 'qualification.fdb'},
                'compiled': {
                    'statements': [{
                        'source': 'CREATE TABLE T (ID INTEGER)',
                        'parameters': (),
                    }],
                },
            },
        })
        self.assertTrue(result['accepted'])
        self.assertIsNone(result['statement_results'][0]['rowcount'])

    def test_mysql_trigger_event_and_privilege_plans_are_dialect_owned(self):
        route = {'host': 'mysql.example', 'route_id': 'mysql-test'}
        trigger = MYSQL_ADMINISTRATION.plan({
            'resource_kind': 'trigger', 'operation_id': 'create',
            'target_resource': None,
            'draft': {
                'name': 'bi_widgets', 'parent': 'app',
                'table': 'app.widgets', 'timing': 'BEFORE',
                'events': ['INSERT'], 'active': True, 'position': 0,
                'body': 'SET NEW.created_at = CURRENT_TIMESTAMP',
            },
            '_provider_route': route,
        })
        self.assertIn('FOR EACH ROW', str(trigger['command_preview']))
        event = MYSQL_ADMINISTRATION.plan({
            'resource_kind': 'event', 'operation_id': 'create',
            'target_resource': None,
            'draft': {
                'name': 'purge_widgets', 'parent': 'app',
                'schedule': 'EVERY 1 DAY', 'preserve': True,
                'enabled': True,
                'body': 'DELETE FROM app.widgets WHERE expired = 1',
            },
            '_provider_route': route,
        })
        self.assertIn('ON SCHEDULE EVERY 1 DAY', str(
            event['command_preview']
        ))
        privilege = MYSQL_ADMINISTRATION.plan({
            'resource_kind': 'privilege', 'operation_id': 'grant',
            'target_resource': None,
            'draft': {
                'principal': 'operator@localhost',
                'object_type': 'TABLE', 'object_name': 'app.widgets',
                'privileges': ['SELECT', 'UPDATE'], 'grant_option': False,
            },
            '_provider_route': route,
        })
        privilege_source = privilege['command_preview']['statements'][0][
            'source'
        ]
        self.assertIn("TO 'operator'@'localhost'", privilege_source)
        plugin = MYSQL_ADMINISTRATION.plan({
            'resource_kind': 'plugin', 'operation_id': 'create',
            'target_resource': None,
            'draft': {
                'name': 'auth_example',
                'library': 'auth_example.so',
            },
            '_provider_route': route,
        })
        self.assertEqual(
            "INSTALL PLUGIN `auth_example` SONAME 'auth_example.so'",
            plugin['command_preview']['statements'][0]['source'],
        )
        uninstall = MYSQL_ADMINISTRATION.plan({
            'resource_kind': 'plugin', 'operation_id': 'drop',
            'target_resource': {
                'resource_kind': 'plugin', 'resource_id': 'plugin:example',
                'display_name': 'auth_example',
                'display_path': ['auth_example'],
            },
            'draft': {'cascade': False, 'confirmation': 'drop-plugin'},
            '_provider_route': route,
        })
        self.assertEqual(
            'UNINSTALL PLUGIN `auth_example`',
            uninstall['command_preview']['statements'][0]['source'],
        )
        with self.assertRaisesRegex(
            RelationalClientError, 'safe unqualified filename'
        ):
            MYSQL_ADMINISTRATION.plan({
                'resource_kind': 'plugin', 'operation_id': 'create',
                'target_resource': None,
                'draft': {
                    'name': 'unsafe_plugin',
                    'library': '../../unsafe.so',
                },
                '_provider_route': route,
            })

    def test_mariadb_package_specification_and_body_are_provider_built(self):
        route = {'host': 'mariadb.example', 'route_id': 'mariadb-test'}
        package = MARIADB_ADMINISTRATION.plan({
            'resource_kind': 'package', 'operation_id': 'create',
            'target_resource': None,
            'draft': {
                'name': 'widget_api', 'parent': 'app',
                'header': 'PROCEDURE get_widget(); END',
                'body': (
                    'PROCEDURE get_widget() BEGIN SELECT 1; END; END'
                ),
            },
            '_provider_route': route,
        })
        statements = package['command_preview']['statements']
        self.assertEqual(2, len(statements))
        self.assertIn(
            'CREATE PACKAGE `app`.`widget_api`', statements[0]['source']
        )
        self.assertIn(
            'CREATE PACKAGE BODY `app`.`widget_api`',
            statements[1]['source'],
        )

        sequence_target = {
            'resource_kind': 'sequence', 'display_name': 'widget_seq',
            'display_path': ['app', 'widget_seq'],
        }
        renamed = MARIADB_ADMINISTRATION.plan({
            'resource_kind': 'sequence', 'operation_id': 'rename',
            'target_resource': sequence_target,
            'draft': {'new_name': 'widget_seq_next'},
            '_provider_route': route,
        })
        self.assertEqual(
            'RENAME TABLE `app`.`widget_seq` TO `app`.`widget_seq_next`',
            renamed['command_preview']['statements'][0]['source'],
        )

        role = MARIADB_ADMINISTRATION.plan({
            'resource_kind': 'role', 'operation_id': 'create',
            'target_resource': None, 'draft': {'name': 'data_reader'},
            '_provider_route': route,
        })
        self.assertEqual(
            'CREATE ROLE `data_reader`',
            role['command_preview']['statements'][0]['source'],
        )

    def test_mariadb_security_forms_and_commands_are_provider_owned(self):
        route = {'host': 'mariadb.example', 'route_id': 'mariadb-security'}
        created = MARIADB_ADMINISTRATION.plan({
            'resource_kind': 'user', 'operation_id': 'create',
            'target_resource': None,
            'draft': {
                'name': 'analyst', 'host': '10.%',
                'authentication_mode': 'PLUGIN_PASSWORD',
                'plugin': 'ed25519', 'password': 'primary-secret',
                'additional_authentication': [{
                    'plugin': 'unix_socket',
                }],
                'tls_requirement': 'SPECIFIED',
                'x509_subject': '/CN=analyst',
                'x509_issuer': '/CN=cdeadmin-ca',
                'max_queries_per_hour': 120,
                'max_statement_time': 2.5,
                'account_lock': 'LOCK',
                'password_expiration': 'INTERVAL',
                'password_expiration_days': 30,
            },
            '_provider_route': route,
        })
        source = created['provider_payload']['compiled']['statements'][0][
            'source'
        ]
        preview = created['command_preview']['statements'][0]['source']
        self.assertIn(
            "CREATE USER 'analyst'@'10.%' IDENTIFIED VIA `ed25519` ",
            source,
        )
        self.assertIn('OR `unix_socket`', source)
        self.assertIn("REQUIRE SUBJECT '/CN=analyst' AND ISSUER ", source)
        self.assertIn('WITH MAX_QUERIES_PER_HOUR 120 ', source)
        self.assertIn('MAX_STATEMENT_TIME 2.5', source)
        self.assertIn('ACCOUNT LOCK PASSWORD EXPIRE INTERVAL 30 DAY', source)
        self.assertNotIn('primary-secret', preview)

        role_target = {
            'resource_kind': 'role', 'resource_id': 'role:data_reader',
            'display_name': 'data_reader', 'display_path': ['data_reader'],
        }
        role_commands = {
            operation: MARIADB_ADMINISTRATION.plan({
                'resource_kind': 'role', 'operation_id': operation,
                'target_resource': role_target,
                'draft': {
                    'member': 'analyst@10.%', 'member_kind': 'USER',
                    **({'admin_option': True} if operation == 'grant' else {}),
                    **({'admin_option_only': True,
                        'confirmation': 'revoke-role'}
                       if operation == 'revoke' else {}),
                },
                '_provider_route': route,
            })['command_preview']['statements'][0]['source']
            for operation in ('grant', 'revoke', 'set_default')
        }
        self.assertEqual(
            "GRANT `data_reader` TO 'analyst'@'10.%' WITH ADMIN OPTION",
            role_commands['grant'],
        )
        self.assertEqual(
            "REVOKE ADMIN OPTION FOR `data_reader` FROM 'analyst'@'10.%'",
            role_commands['revoke'],
        )
        self.assertEqual(
            "SET DEFAULT ROLE `data_reader` FOR 'analyst'@'10.%'",
            role_commands['set_default'],
        )

        catalog = MARIADB_ADMINISTRATION.catalog(
            catalog_for_engine('mariadb')
        )
        operations = {
            item['operation_id']: item
            for descriptor in catalog['objects']
            if descriptor['resource_kind'] == 'role'
            for item in descriptor['operations']
        }
        self.assertEqual(
            {'inspect', 'create', 'grant', 'revoke', 'set_default', 'drop'},
            set(operations),
        )
        self.assertEqual(
            'mariadb.role.set_default',
            operations['set_default']['form']['form_id'],
        )

    def test_mariadb_security_validation_rejects_cross_engine_assumptions(
        self,
    ):
        def codes(kind, operation, draft):
            return {
                item['code'] for item in MARIADB_ADMINISTRATION.validate({
                    'resource_kind': kind, 'operation_id': operation,
                    'target_resource': {
                        'resource_kind': kind, 'display_name': 'target',
                        'display_path': ['target'],
                    },
                    'draft': draft,
                    '_provider_route': {
                        'host': 'mariadb.example',
                        'route_id': 'mariadb-security-validation',
                    },
                })['errors']
            }

        self.assertIn('mariadb_password_required', codes(
            'user', 'create', {
                'name': 'analyst', 'authentication_mode': 'PASSWORD',
            }
        ))
        self.assertIn('mariadb_tls_attribute_required', codes(
            'user', 'alter', {
                'authentication_mode': 'UNCHANGED',
                'tls_requirement': 'SPECIFIED',
            }
        ))
        self.assertIn('invalid_mariadb_privilege_scope', codes(
            'privilege', 'grant', {
                'principal': 'analyst@%', 'principal_kind': 'USER',
                'object_type': 'SCHEMA', 'object_name': 'app',
                'privileges': ['SELECT'],
            }
        ))

    def test_mariadb_named_replication_forms_compile_native_12_2_syntax(self):
        route = {'host': 'mariadb.example', 'route_id': 'mariadb-replication'}
        created = MARIADB_ADMINISTRATION.plan({
            'resource_kind': 'replication-channel', 'operation_id': 'create',
            'target_resource': None,
            'draft': {
                'name': 'analytics', 'master_host': 'primary.example',
                'master_user': 'replicator',
                'master_password': 'replication-secret', 'master_port': 3306,
                'connect_retry': 5, 'use_gtid': 'SLAVE_POS',
                'master_ssl': 'ON', 'ssl_ca': '/etc/ssl/ca.pem',
                'verify_server_certificate': 'ON',
                'ignore_server_ids': [7, 9],
                'do_domain_ids': [1], 'ignore_domain_ids': [],
                'demote_to_slave': 'UNCHANGED',
            },
            '_provider_route': route,
        })
        source = created['provider_payload']['compiled']['statements'][0][
            'source'
        ]
        preview = created['command_preview']['statements'][0]['source']
        self.assertTrue(source.startswith("CHANGE MASTER 'analytics' TO "))
        self.assertIn("MASTER_HOST='primary.example'", source)
        self.assertIn('MASTER_USE_GTID=SLAVE_POS', source)
        self.assertIn('IGNORE_SERVER_IDS=(7, 9)', source)
        self.assertIn('DO_DOMAIN_IDS=(1)', source)
        self.assertNotIn('replication-secret', preview)

        target = {
            'resource_kind': 'replication-channel',
            'resource_id': 'replication-channel:analytics',
            'display_name': 'analytics', 'display_path': ['analytics'],
        }
        commands = {}
        inspected = MARIADB_ADMINISTRATION.plan({
            'resource_kind': 'replication-channel',
            'operation_id': 'inspect', 'target_resource': target,
            'draft': {}, '_provider_route': route,
        })
        self.assertEqual(
            'inspect',
            inspected['provider_payload']['compiled']['internal_operation'],
        )
        for operation, draft in (
            ('alter', {'connect_retry': 10}),
            ('start', {
                'thread': 'SQL_THREAD', 'until_mode': 'MASTER_GTID_POS',
                'until_gtid': '1-2-3',
            }),
            ('stop', {'thread': 'IO_THREAD'}),
            ('reset', {
                'delete_connection': True, 'confirmation': 'analytics',
            }),
        ):
            commands[operation] = MARIADB_ADMINISTRATION.plan({
                'resource_kind': 'replication-channel',
                'operation_id': operation, 'target_resource': target,
                'draft': draft, '_provider_route': route,
            })['command_preview']['statements'][0]['source']
        self.assertEqual(
            "CHANGE MASTER 'analytics' TO MASTER_CONNECT_RETRY=10",
            commands['alter'],
        )
        self.assertEqual(
            "START SLAVE 'analytics' SQL_THREAD UNTIL "
            "MASTER_GTID_POS='1-2-3'", commands['start'],
        )
        self.assertEqual("STOP SLAVE 'analytics' IO_THREAD", commands['stop'])
        self.assertEqual("RESET SLAVE 'analytics' ALL", commands['reset'])

        catalog = MARIADB_ADMINISTRATION.catalog(
            catalog_for_engine('mariadb')
        )
        replication = next(
            item for item in catalog['objects']
            if item['resource_kind'] == 'replication-channel'
        )
        self.assertEqual(
            {'inspect', 'create', 'alter', 'start', 'stop', 'reset'},
            {item['operation_id'] for item in replication['operations']},
        )
        operational_kinds = {
            'session', 'system-variable', 'lock', 'lock-wait',
            'table-storage', 'binary-log', 'binary-log-status',
            'binary-log-event', 'log-configuration', 'general-log-entry',
            'slow-query', 'tls-configuration',
        }
        objects = {
            item['resource_kind']: item for item in catalog['objects']
        }
        self.assertTrue(operational_kinds.issubset(objects))
        expected_operations = {
            'system-variable': {'inspect', 'set_global'},
            'session': {
                'inspect', 'terminate_query', 'terminate_connection',
            },
            'binary-log': {'inspect', 'purge_before'},
            'binary-log-status': {'inspect', 'rotate'},
        }
        for kind in operational_kinds:
            self.assertEqual(
                expected_operations.get(kind, {'inspect'}),
                {item['operation_id'] for item in objects[kind]['operations']},
            )

    def test_mariadb_system_variable_form_is_runtime_global_and_fail_closed(
        self,
    ):
        route = {'host': 'mariadb.example', 'route_id': 'mariadb-variables'}
        target = {
            'resource_kind': 'system-variable',
            'resource_id': 'system-variable:Configuration:MAX_CONNECTIONS',
            'display_name': 'MAX_CONNECTIONS',
            'display_path': ['Configuration', 'MAX_CONNECTIONS'],
            'native': {
                'read_only': 'NO', 'variable_scope': 'GLOBAL',
                'variable_type': 'INT UNSIGNED', 'global_value': '151',
            },
        }
        planned = MARIADB_ADMINISTRATION.plan({
            'resource_kind': 'system-variable',
            'operation_id': 'set_global', 'target_resource': target,
            'draft': {'value_mode': 'VALUE', 'value': '151'},
            '_provider_route': route,
        })
        self.assertEqual(
            'SET GLOBAL `MAX_CONNECTIONS` = 151',
            planned['command_preview']['statements'][0]['source'],
        )
        defaulted = MARIADB_ADMINISTRATION.plan({
            'resource_kind': 'system-variable',
            'operation_id': 'set_global', 'target_resource': target,
            'draft': {'value_mode': 'DEFAULT'},
            '_provider_route': route,
        })
        self.assertEqual(
            'SET GLOBAL `MAX_CONNECTIONS` = DEFAULT',
            defaulted['command_preview']['statements'][0]['source'],
        )
        read_only = {
            **target, 'display_name': 'PERFORMANCE_SCHEMA',
            'native': {
                **target['native'], 'read_only': 'YES',
            },
        }
        errors = MARIADB_ADMINISTRATION.validate({
            'resource_kind': 'system-variable',
            'operation_id': 'set_global', 'target_resource': read_only,
            'draft': {'value_mode': 'VALUE', 'value': 'ON'},
            '_provider_route': route,
        })['errors']
        self.assertIn(
            'mariadb_system_variable_read_only',
            {item['code'] for item in errors},
        )

    def test_mariadb_session_termination_uses_native_process_identity(self):
        route = {'host': 'mariadb.example', 'route_id': 'mariadb-sessions'}
        target = {
            'resource_kind': 'session',
            'resource_id': 'session:Sessions:812',
            'display_name': '812', 'display_path': ['Sessions', '812'],
            'extensions': {'mariadb': {'native': {
                'id': 812, 'user': 'analyst', 'command': 'Query',
            }}},
        }
        commands = {}
        for operation, mode in (
                ('terminate_query', 'SOFT'),
                ('terminate_connection', 'HARD')):
            plan = MARIADB_ADMINISTRATION.plan({
                'resource_kind': 'session', 'operation_id': operation,
                'target_resource': target,
                'draft': {
                    'termination_mode': mode, 'confirmation': '812',
                },
                '_provider_route': route,
            })
            commands[operation] = plan[
                'command_preview'
            ]['statements'][0]['source']
        self.assertEqual('KILL SOFT QUERY 812', commands['terminate_query'])
        self.assertEqual(
            'KILL HARD CONNECTION 812', commands['terminate_connection']
        )
        errors = MARIADB_ADMINISTRATION.validate({
            'resource_kind': 'session',
            'operation_id': 'terminate_connection',
            'target_resource': target,
            'draft': {'termination_mode': 'SOFT', 'confirmation': '811'},
            '_provider_route': route,
        })['errors']
        self.assertIn(
            'mariadb_process_confirmation_mismatch',
            {item['code'] for item in errors},
        )

    def test_mariadb_binary_log_forms_use_native_12_2_commands(self):
        route = {'host': 'mariadb.example', 'route_id': 'mariadb-binlog'}
        log = {
            'resource_kind': 'binary-log',
            'resource_id': 'binary-log:Binary logs:mariadb-bin.000002',
            'display_name': 'mariadb-bin.000002',
            'display_path': ['Binary logs', 'mariadb-bin.000002'],
        }
        purged = MARIADB_ADMINISTRATION.plan({
            'resource_kind': 'binary-log',
            'operation_id': 'purge_before', 'target_resource': log,
            'draft': {'confirmation': 'mariadb-bin.000002'},
            '_provider_route': route,
        })
        self.assertEqual(
            "PURGE BINARY LOGS TO 'mariadb-bin.000002'",
            purged['command_preview']['statements'][0]['source'],
        )
        status = {
            'resource_kind': 'binary-log-status',
            'resource_id': 'binary-log-status:Binary logs:mariadb-bin.000002',
            'display_name': 'mariadb-bin.000002',
            'display_path': ['Binary logs', 'mariadb-bin.000002'],
        }
        rotated = MARIADB_ADMINISTRATION.plan({
            'resource_kind': 'binary-log-status',
            'operation_id': 'rotate', 'target_resource': status,
            'draft': {}, '_provider_route': route,
        })
        self.assertEqual(
            'FLUSH BINARY LOGS',
            rotated['command_preview']['statements'][0]['source'],
        )
        errors = MARIADB_ADMINISTRATION.validate({
            'resource_kind': 'binary-log',
            'operation_id': 'purge_before', 'target_resource': log,
            'draft': {'confirmation': 'mariadb-bin.000001'},
            '_provider_route': route,
        })['errors']
        self.assertIn(
            'mariadb_binary_log_confirmation_mismatch',
            {item['code'] for item in errors},
        )

    def test_mariadb_database_forms_and_plans_are_exact_12_2_2(self):
        route = {
            'host': 'mariadb.example', 'route_id': 'mariadb-database-test',
            'database': 'inventory',
        }
        target = {
            'resource_kind': 'database', 'display_name': 'inventory',
            'display_path': ['inventory'],
        }
        created = MARIADB_ADMINISTRATION.plan({
            'resource_kind': 'database', 'operation_id': 'create',
            'target_resource': None,
            'draft': {
                'name': 'inventory_next', 'or_replace': True,
                'if_not_exists': False, 'character_set': 'utf8mb4',
                'collation': 'utf8mb4_uca1400_ai_ci',
                'comment': "operator's inventory",
            },
            '_provider_route': route,
        })
        self.assertEqual(
            'CREATE OR REPLACE DATABASE `inventory_next` DEFAULT CHARACTER '
            "SET `utf8mb4` DEFAULT COLLATE `utf8mb4_uca1400_ai_ci` COMMENT "
            "= 'operator''s inventory'",
            created['command_preview']['statements'][0]['source'],
        )
        altered = MARIADB_ADMINISTRATION.plan({
            'resource_kind': 'database', 'operation_id': 'alter',
            'target_resource': target,
            'draft': {
                'character_set': 'utf8mb4',
                'collation': 'utf8mb4_uca1400_ai_ci', 'comment': '',
            },
            '_provider_route': route,
        })
        self.assertEqual(
            'ALTER DATABASE `inventory` DEFAULT CHARACTER SET `utf8mb4` '
            "DEFAULT COLLATE `utf8mb4_uca1400_ai_ci` COMMENT = ''",
            altered['command_preview']['statements'][0]['source'],
        )

        catalog = MARIADB_ADMINISTRATION.catalog(
            catalog_for_engine('mariadb')
        )
        database = next(
            item for item in catalog['objects']
            if item['resource_kind'] == 'database'
        )
        operations = {
            item['operation_id']: item for item in database['operations']
        }
        self.assertEqual(
            ['name', 'or_replace', 'if_not_exists', 'character_set',
             'collation', 'comment'],
            [item['field_id'] for item in operations['create']['form'][
                'fields']],
        )
        self.assertEqual(
            ['character_set', 'collation', 'comment'],
            [item['field_id'] for item in operations['alter']['form'][
                'fields']],
        )
        self.assertEqual(
            ['confirmation'],
            [item['field_id'] for item in operations['drop']['form'][
                'fields']],
        )

    def test_mariadb_database_validation_fails_closed(self):
        route = {
            'host': 'mariadb.example', 'route_id': 'mariadb-database-test',
            'database': 'inventory',
        }
        target = {
            'resource_kind': 'database', 'display_name': 'inventory',
            'display_path': ['inventory'],
        }

        def codes(operation, draft):
            return {
                item['code'] for item in MARIADB_ADMINISTRATION.validate({
                    'resource_kind': 'database',
                    'operation_id': operation,
                    'target_resource': (
                        None if operation == 'create' else target
                    ),
                    'draft': draft, '_provider_route': route,
                })['errors']
            }

        self.assertIn('mariadb_database_create_mode_conflict', codes(
            'create', {
                'name': 'inventory_next', 'or_replace': True,
                'if_not_exists': True,
            }
        ))
        self.assertIn('invalid_mariadb_database_comment', codes(
            'alter', {'comment': 'x' * 1025}
        ))
        self.assertIn('mariadb_database_change_required', codes(
            'alter', {}
        ))
        self.assertIn('mariadb_database_confirmation_mismatch', codes(
            'drop', {'confirmation': 'wrong'}
        ))
        self.assertIn('unknown_mariadb_database_drop_option', codes(
            'drop', {'confirmation': 'inventory', 'cascade': False}
        ))

    def test_mysql_role_creation_can_establish_native_membership_edge(self):
        plan = MYSQL_ADMINISTRATION.plan({
            'resource_kind': 'role', 'operation_id': 'create',
            'target_resource': None,
            'draft': {
                'name': 'data_reader',
                'members': ['operator@localhost'],
            },
            '_provider_route': {
                'host': 'mysql.example', 'route_id': 'mysql-role-test',
            },
        })
        statements = plan['command_preview']['statements']
        self.assertEqual(2, len(statements))
        self.assertEqual(
            "CREATE ROLE 'data_reader'@'%'", statements[0]['source']
        )
        self.assertEqual(
            "GRANT 'data_reader'@'%' TO 'operator'@'localhost'",
            statements[1]['source'],
        )

    def test_mysql_database_alter_uses_structured_charset_fields(self):
        plan = MYSQL_ADMINISTRATION.plan({
            'resource_kind': 'database', 'operation_id': 'alter',
            'target_resource': {
                'resource_kind': 'database', 'display_name': 'inventory',
                'display_path': ['inventory'],
            },
            'draft': {
                'character_set': 'utf8mb4',
                'collation': 'utf8mb4_0900_ai_ci',
            },
            '_provider_route': {
                'host': 'mysql.example', 'route_id': 'mysql-database-test',
            },
        })
        self.assertEqual(
            'ALTER DATABASE `inventory` DEFAULT CHARACTER SET `utf8mb4` '
            'DEFAULT COLLATE `utf8mb4_0900_ai_ci`',
            plan['command_preview']['statements'][0]['source'],
        )

    def test_mysql_database_create_and_extended_alter_are_structured(self):
        route = {'host': 'mysql.example', 'route_id': 'mysql-database-test'}
        created = MYSQL_ADMINISTRATION.plan({
            'resource_kind': 'database', 'operation_id': 'create',
            'target_resource': None,
            'draft': {
                'name': 'inventory', 'if_not_exists': True,
                'character_set': 'utf8mb4',
                'collation': 'utf8mb4_0900_ai_ci', 'encryption': 'Y',
            },
            '_provider_route': route,
        })
        self.assertEqual(
            'CREATE DATABASE IF NOT EXISTS `inventory` DEFAULT CHARACTER '
            "SET `utf8mb4` DEFAULT COLLATE `utf8mb4_0900_ai_ci` DEFAULT "
            "ENCRYPTION 'Y'",
            created['command_preview']['statements'][0]['source'],
        )
        altered = MYSQL_ADMINISTRATION.plan({
            'resource_kind': 'database', 'operation_id': 'alter',
            'target_resource': {
                'resource_kind': 'database', 'display_name': 'inventory',
                'display_path': ['inventory'],
            },
            'draft': {'encryption': 'N', 'read_only': 'ON'},
            '_provider_route': route,
        })
        self.assertEqual(
            "ALTER DATABASE `inventory` DEFAULT ENCRYPTION 'N' READ ONLY = 1",
            altered['command_preview']['statements'][0]['source'],
        )
        rejected = MYSQL_ADMINISTRATION.validate({
            'resource_kind': 'database', 'operation_id': 'drop',
            'target_resource': {
                'resource_kind': 'database', 'display_name': 'inventory',
                'display_path': ['inventory'],
            },
            'draft': {'confirmation': 'wrong-database'},
            '_provider_route': route,
        })
        self.assertEqual(
            ['mysql_database_confirmation_mismatch'],
            [item['code'] for item in rejected['errors']],
        )
        dropped = MYSQL_ADMINISTRATION.plan({
            'resource_kind': 'database', 'operation_id': 'drop',
            'target_resource': {
                'resource_kind': 'database', 'display_name': 'inventory',
                'display_path': ['inventory'],
                'extensions': {'cdeadmin': {
                    'database_target_id': 'inventory-target',
                }},
            },
            'draft': {'confirmation': 'inventory'},
            '_provider_route': route,
        })
        self.assertEqual(
            {'target_id': 'inventory-target', 'confirmation': 'inventory'},
            dropped['provider_payload']['compiled']['database_target'],
        )

    def test_mysql_materialized_view_plans_use_exact_9_7_syntax(self):
        route = {'host': 'mysql.example', 'route_id': 'mysql-mv-test'}
        created = MYSQL_ADMINISTRATION.plan({
            'resource_kind': 'materialized-view',
            'operation_id': 'create',
            'target_resource': None,
            'draft': {
                'name': 'inventory_summary', 'parent': 'app',
                'query': 'SELECT id, value FROM app.inventory',
            },
            '_provider_route': route,
        })
        self.assertEqual(
            'CREATE MATERIALIZED VIEW `app`.`inventory_summary` AS '
            'SELECT id, value FROM app.inventory',
            created['command_preview']['statements'][0]['source'],
        )
        target = {
            'resource_kind': 'materialized-view',
            'display_name': 'inventory_summary',
            'display_path': ['app', 'inventory_summary'],
        }
        altered = MYSQL_ADMINISTRATION.plan({
            'resource_kind': 'materialized-view',
            'operation_id': 'alter', 'target_resource': target,
            'draft': {'query': 'SELECT id FROM app.inventory'},
            '_provider_route': route,
        })
        self.assertEqual(
            'ALTER MATERIALIZED VIEW `app`.`inventory_summary` AS '
            'SELECT id FROM app.inventory',
            altered['command_preview']['statements'][0]['source'],
        )
        dropped = MYSQL_ADMINISTRATION.plan({
            'resource_kind': 'materialized-view',
            'operation_id': 'drop', 'target_resource': target,
            'draft': {'cascade': False,
                      'confirmation': 'inventory_summary'},
            '_provider_route': route,
        })
        self.assertEqual(
            'DROP VIEW `app`.`inventory_summary`',
            dropped['command_preview']['statements'][0]['source'],
        )

    def test_mysql_database_maintenance_forms_compile_exact_9_7_sql(self):
        route = {
            'host': 'mysql.example', 'route_id': 'mysql-maintenance-test',
            'database': 'inventory',
        }
        target = {
            'resource_kind': 'database', 'display_name': 'inventory',
            'display_path': ['inventory'],
        }
        cases = (
            ('analyze_tables', {
                'tables': ['widgets'], 'no_write_to_binlog': True,
                'histogram_action': 'UPDATE',
                'histogram_columns': ['category'],
                'histogram_buckets': 64, 'histogram_auto_update': True,
            }, 'ANALYZE NO_WRITE_TO_BINLOG TABLE `inventory`.`widgets` '
               'UPDATE HISTOGRAM ON `category` WITH 64 BUCKETS AUTO UPDATE'),
            ('check_tables', {
                'tables': ['widgets'], 'check_options': ['QUICK'],
            }, 'CHECK TABLE `inventory`.`widgets` QUICK'),
            ('optimize_tables', {
                'tables': ['widgets'], 'no_write_to_binlog': True,
            }, 'OPTIMIZE NO_WRITE_TO_BINLOG TABLE `inventory`.`widgets`'),
            ('repair_tables', {
                'tables': ['archive'], 'no_write_to_binlog': True,
                'repair_options': ['QUICK'],
            }, 'REPAIR NO_WRITE_TO_BINLOG TABLE `inventory`.`archive` QUICK'),
            ('checksum_tables', {
                'tables': ['widgets'], 'checksum_type': 'EXTENDED',
            }, 'CHECKSUM TABLE `inventory`.`widgets` EXTENDED'),
        )
        for operation, draft, expected in cases:
            self.assertEqual([], MYSQL_ADMINISTRATION.validate({
                'resource_kind': 'database', 'operation_id': operation,
                'target_resource': target, 'draft': draft,
                '_provider_route': route,
            })['errors'])
            plan = MYSQL_ADMINISTRATION.plan({
                'resource_kind': 'database', 'operation_id': operation,
                'target_resource': target, 'draft': draft,
                '_provider_route': route,
            })
            self.assertEqual(
                expected,
                plan['command_preview']['statements'][0]['source'],
            )

        database = next(
            item for item in MYSQL_ADMINISTRATION.catalog(
                catalog_for_engine('mysql')
            )['objects'] if item['resource_kind'] == 'database'
        )
        forms = {
            item['operation_id']: item['form']['form_id']
            for item in database['operations']
        }
        self.assertEqual({
            'analyze_tables': 'mysql_analyze_tables',
            'check_tables': 'mysql_check_tables',
            'optimize_tables': 'mysql_optimize_tables',
            'repair_tables': 'mysql_repair_tables',
            'checksum_tables': 'mysql_checksum_tables',
        }, {key: forms[key] for key in {
            'analyze_tables', 'check_tables', 'optimize_tables',
            'repair_tables', 'checksum_tables',
        }})

    def test_mysql_shell_backup_and_restore_are_driver_owned_plans(self):
        route = {
            'host': 'mysql.example', 'port': 3306,
            'route_id': 'mysql-shell-test', 'database': 'inventory',
            'tool_workspace': '/srv/cdeadmin/mysql-shell',
        }
        target = {
            'resource_kind': 'database', 'display_name': 'inventory',
            'display_path': ['inventory'],
        }
        backup_options = {
            'path': 'inventory-backup', 'consistent': True,
            'checksum': True, 'threads': 8,
            'compression': 'zstd;level=22',
            'include_tables': ['inventory.widgets'],
        }
        backup = MYSQL_ADMINISTRATION.plan({
            'resource_kind': 'database', 'operation_id': 'backup_logical',
            'target_resource': target, 'draft': backup_options,
            '_provider_route': route,
        })
        self.assertEqual(
            'mysql-shell',
            backup['provider_payload']['compiled']['driver_operation'],
        )
        self.assertEqual(
            'backup_logical',
            backup['provider_payload']['compiled']['operation_id'],
        )
        self.assertEqual(
            backup_options,
            backup['provider_payload']['compiled']['options'],
        )
        self.assertEqual(
            [], backup['provider_payload']['compiled']['statements']
        )

        restore_options = {
            'path': 'inventory-backup', 'confirmation': 'inventory',
            'drop_existing_objects': True, 'enable_local_infile': True,
            'wait_dump_timeout': 0.5, 'schema': 'inventory_restored',
        }
        restore = MYSQL_ADMINISTRATION.plan({
            'resource_kind': 'database', 'operation_id': 'restore_logical',
            'target_resource': target, 'draft': restore_options,
            '_provider_route': route,
        })
        self.assertEqual(
            'mysql-shell',
            restore['provider_payload']['compiled']['driver_operation'],
        )
        self.assertEqual(
            restore_options,
            restore['provider_payload']['compiled']['options'],
        )
        self.assertEqual(
            [], restore['provider_payload']['compiled']['statements']
        )
        tasks = set(MYSQL_ADMINISTRATION.dialect_task_ids())
        self.assertNotIn('visual_admin.database.backup_logical', tasks)
        self.assertNotIn('visual_admin.database.restore_logical', tasks)

    def test_mariadb_database_maintenance_forms_compile_exact_12_2_sql(self):
        route = {
            'host': 'mariadb.example',
            'route_id': 'mariadb-maintenance-test',
            'database': 'inventory',
        }
        target = {
            'resource_kind': 'database', 'display_name': 'inventory',
            'display_path': ['inventory'],
        }
        cases = (
            ('analyze_tables', {
                'tables': ['widgets', 'events'], 'binlog_mode': 'LOCAL',
                'persistent_for': 'SPECIFIED',
                'persistent_columns': ['category'],
                'persistent_indexes': ['PRIMARY', 'category_index'],
            }, 'ANALYZE LOCAL TABLE `inventory`.`widgets` PERSISTENT FOR '
               'COLUMNS (`category`) INDEXES (PRIMARY, `category_index`), '
               '`inventory`.`events` PERSISTENT FOR COLUMNS (`category`) '
               'INDEXES (PRIMARY, `category_index`)'),
            ('check_objects', {
                'object_type': 'VIEW', 'objects': ['active_widgets'],
                'check_options': ['FOR UPGRADE'],
            }, 'CHECK VIEW `inventory`.`active_widgets` FOR UPGRADE'),
            ('optimize_tables', {
                'tables': ['widgets'],
                'binlog_mode': 'NO_WRITE_TO_BINLOG',
                'lock_wait_mode': 'WAIT', 'lock_wait_seconds': 12,
            }, 'OPTIMIZE NO_WRITE_TO_BINLOG TABLE '
               '`inventory`.`widgets` WAIT 12'),
            ('repair_objects', {
                'object_type': 'TABLE', 'objects': ['archive'],
                'binlog_mode': 'LOCAL',
                'repair_options': ['QUICK', 'FORCE'],
            }, 'REPAIR LOCAL TABLE `inventory`.`archive` QUICK FORCE'),
            ('checksum_tables', {
                'tables': ['widgets'], 'checksum_type': 'EXTENDED',
            }, 'CHECKSUM TABLE `inventory`.`widgets` EXTENDED'),
        )
        for operation, draft, expected in cases:
            request = {
                'resource_kind': 'database', 'operation_id': operation,
                'target_resource': target, 'draft': draft,
                '_provider_route': route,
            }
            self.assertEqual([], MARIADB_ADMINISTRATION.validate(request)[
                'errors'])
            plan = MARIADB_ADMINISTRATION.plan(request)
            self.assertEqual(
                expected,
                plan['command_preview']['statements'][0]['source'],
            )

        database = next(
            item for item in MARIADB_ADMINISTRATION.catalog(
                catalog_for_engine('mariadb')
            )['objects'] if item['resource_kind'] == 'database'
        )
        forms = {
            item['operation_id']: item['form']['form_id']
            for item in database['operations']
        }
        self.assertEqual({
            'analyze_tables': 'mariadb_analyze_tables',
            'check_objects': 'mariadb_check_objects',
            'optimize_tables': 'mariadb_optimize_tables',
            'repair_objects': 'mariadb_repair_objects',
            'checksum_tables': 'mariadb_checksum_tables',
        }, {key: forms[key] for key in {
            'analyze_tables', 'check_objects', 'optimize_tables',
            'repair_objects', 'checksum_tables',
        }})

    def test_mariadb_database_maintenance_validation_is_exact(self):
        route = {
            'host': 'mariadb.example', 'route_id': 'mariadb-validation',
            'database': 'inventory',
        }
        target = {
            'resource_kind': 'database', 'display_name': 'inventory',
            'display_path': ['inventory'],
        }

        def codes(operation, draft):
            return {
                item['code'] for item in MARIADB_ADMINISTRATION.validate({
                    'resource_kind': 'database',
                    'operation_id': operation,
                    'target_resource': target, 'draft': draft,
                    '_provider_route': route,
                })['errors']
            }

        self.assertIn('invalid_mariadb_check_options', codes(
            'check_objects', {
                'object_type': 'VIEW', 'objects': ['active_widgets'],
                'check_options': ['QUICK'],
            }
        ))
        self.assertIn('invalid_mariadb_repair_options', codes(
            'repair_objects', {
                'object_type': 'VIEW', 'objects': ['active_widgets'],
                'binlog_mode': 'DEFAULT',
                'repair_options': ['FOR UPGRADE', 'FROM MYSQL'],
            }
        ))
        self.assertIn('mariadb_lock_wait_seconds_without_wait', codes(
            'optimize_tables', {
                'tables': ['widgets'], 'binlog_mode': 'DEFAULT',
                'lock_wait_mode': 'NOWAIT', 'lock_wait_seconds': 5,
            }
        ))
        self.assertIn('mariadb_persistent_names_without_specified', codes(
            'analyze_tables', {
                'tables': ['widgets'], 'binlog_mode': 'DEFAULT',
                'persistent_for': 'ALL',
                'persistent_columns': ['category'],
                'persistent_indexes': [],
            }
        ))

    def test_mariadb_backup_restore_plans_are_native_tool_owned(self):
        with tempfile.TemporaryDirectory() as workspace:
            route = {
                'host': 'mariadb.example', 'port': 3306,
                'route_id': 'mariadb-tool-test', 'database': 'inventory',
                'tool_workspace': workspace,
            }
            target = {
                'resource_kind': 'database', 'display_name': 'inventory',
                'display_path': ['inventory'],
            }
            backup_options = {
                'path': 'inventory.sql', 'include_schema': True,
                'include_data': True, 'single_transaction': True,
                'lock_all_tables': False, 'replication_position': 'COMMENTED',
                'gtid': True,
            }
            backup_request = {
                'resource_kind': 'database',
                'operation_id': 'backup_logical',
                'target_resource': target, 'draft': backup_options,
                '_provider_route': route,
            }
            self.assertEqual(
                [], MARIADB_ADMINISTRATION.validate(backup_request)['errors']
            )
            backup = MARIADB_ADMINISTRATION.plan(backup_request)
            self.assertEqual(
                'mariadb-tools',
                backup['provider_payload']['compiled']['driver_operation'],
            )
            self.assertEqual(
                [], backup['provider_payload']['compiled']['statements']
            )

            restore_options = {
                'path': 'inventory.sql', 'confirmation': 'inventory',
                'abort_on_error': True, 'binary_mode': True,
            }
            restore_request = {
                'resource_kind': 'database',
                'operation_id': 'restore_logical',
                'target_resource': target, 'draft': restore_options,
                '_provider_route': route,
            }
            self.assertEqual(
                [], MARIADB_ADMINISTRATION.validate(restore_request)['errors']
            )
            restore = MARIADB_ADMINISTRATION.plan(restore_request)
            self.assertEqual(
                'mariadb-tools',
                restore['provider_payload']['compiled']['driver_operation'],
            )
            tasks = set(MARIADB_ADMINISTRATION.dialect_task_ids())
            self.assertNotIn(
                'visual_admin.database.backup_logical', tasks
            )
            self.assertNotIn(
                'visual_admin.database.restore_logical', tasks
            )

            forms = {
                item['operation_id']: item['form']['form_id']
                for item in next(
                    item for item in MARIADB_ADMINISTRATION.catalog(
                        catalog_for_engine('mariadb')
                    )['objects'] if item['resource_kind'] == 'database'
                )['operations']
            }
            self.assertEqual(
                'mariadb_backup_logical', forms['backup_logical']
            )
            self.assertEqual(
                'mariadb_restore_logical', forms['restore_logical']
            )

    def test_mysql_shell_restore_validation_fails_closed(self):
        route = {
            'host': 'mysql.example', 'route_id': 'mysql-shell-test',
            'database': 'inventory',
            'tool_workspace': '/srv/cdeadmin/mysql-shell',
        }
        target = {
            'resource_kind': 'database', 'display_name': 'inventory',
            'display_path': ['inventory'],
        }

        def error_codes(draft):
            result = MYSQL_ADMINISTRATION.validate({
                'resource_kind': 'database',
                'operation_id': 'restore_logical',
                'target_resource': target, 'draft': draft,
                '_provider_route': route,
            })
            return {item['code'] for item in result['errors']}

        self.assertEqual(set(), error_codes({
            'path': 'inventory-backup', 'confirmation': 'inventory',
            'wait_dump_timeout': 0.5,
        }))
        self.assertIn(
            'mysql_shell_restore_confirmation_mismatch', error_codes({
                'path': 'inventory-backup', 'confirmation': 'wrong',
            })
        )
        self.assertIn('mysql_shell_existing_object_conflict', error_codes({
            'path': 'inventory-backup', 'confirmation': 'inventory',
            'drop_existing_objects': True,
            'ignore_existing_objects': True,
        }))

        backup_base = {
            'resource_kind': 'database', 'operation_id': 'backup_logical',
            'target_resource': target, '_provider_route': route,
        }
        accepted = MYSQL_ADMINISTRATION.validate({
            **backup_base,
            'draft': {
                'path': 'inventory-backup',
                'compression': 'zstd;level=22',
            },
        })
        self.assertEqual([], accepted['errors'])
        rejected = MYSQL_ADMINISTRATION.validate({
            **backup_base,
            'draft': {
                'path': 'inventory-backup',
                'compression': 'zstd;level=23',
            },
        })
        self.assertIn(
            'invalid_mysql_shell_compression',
            {item['code'] for item in rejected['errors']},
        )

    def test_relational_dialects_publish_fail_closed_concept_status(self):
        cases = (
            ('mysql', '9.7.0', MYSQL_ADMINISTRATION, {
                'servers': 'read_only',
                'schemas': 'supported',
                'materialized_views': 'supported',
                'extensions_and_plugins': 'supported',
            }),
            ('mariadb', '12.2.2', MARIADB_ADMINISTRATION, {
                'sequences': 'supported',
                'schemas': 'supported',
                'materialized_views': 'not_applicable',
                'extensions_and_plugins': 'supported',
            }),
            ('firebird', '5.0.4', FIREBIRD_ADMINISTRATION, {
                'servers': 'read_only',
                'schemas': 'not_applicable',
                'partitions': 'not_applicable',
                'replication_objects': 'supported',
                'extensions_and_plugins': 'read_only',
            }),
            ('duckdb', '1.5.2', DUCKDB_ADMINISTRATION, {
                'servers': 'not_applicable',
                'schemas': 'supported',
                'extensions_and_plugins': 'supported',
                'roles_and_grants': 'not_applicable',
            }),
            ('sqlite', '3.53.0', SQLITE_ADMINISTRATION, {
                'servers': 'not_applicable',
                'schemas': 'supported',
                'triggers': 'supported',
                'roles_and_grants': 'not_applicable',
            }),
        )
        for engine_id, version, administration, expected in cases:
            provider_context = SimpleNamespace(
                endpoint_id='endpoint-test', mode='legacy_native',
                runtime_verification_state='verified',
                verified_runtime_family=engine_id,
                declared_runtime_family=engine_id,
                effective_permissions=frozenset({
                    'data_read', 'data_write', 'administer',
                }),
            )
            descriptor = ProviderVisualAdministration(
                provider_context, Permissions(), engine_id, version,
                AdministrationClient(administration),
            ).descriptor()
            concepts = {
                item['concept_id']: item['activation_state']
                for item in descriptor['concept_coverage']['families'][0][
                    'concepts'
                ]
            }
            self.assertEqual(expected, {
                concept_id: concepts[concept_id]
                for concept_id in expected
            })

    def test_mysql_family_catalogs_preserve_engine_unique_capabilities(self):
        mysql = MYSQL_ADMINISTRATION.catalog(catalog_for_engine('mysql'))
        mariadb = MARIADB_ADMINISTRATION.catalog(
            catalog_for_engine('mariadb')
        )

        def executable_kinds(catalog):
            return {
                item['resource_kind']
                for item in catalog['objects']
                if item.get('operations')
            }

        mysql_kinds = executable_kinds(mysql)
        mariadb_kinds = executable_kinds(mariadb)
        self.assertIn('materialized-view', mysql_kinds)
        self.assertNotIn('materialized-view', mariadb_kinds)
        self.assertNotIn('sequence', mysql_kinds)
        self.assertNotIn('package', mysql_kinds)
        self.assertIn('sequence', mariadb_kinds)
        self.assertIn('package', mariadb_kinds)


class DuckDBVisualAdministrationTests(unittest.TestCase):

    def test_duckdb_type_validation_omits_inactive_values(self):
        context = SimpleNamespace(
            endpoint_id='duckdb-type-test', mode='legacy_native',
            runtime_verification_state='verified',
            verified_runtime_family='duckdb',
            declared_runtime_family='duckdb',
            effective_permissions=frozenset({'administer'}))
        visual = ProviderVisualAdministration(
            context, Permissions(), 'duckdb', '1.5.2',
            AdministrationClient(DUCKDB_ADMINISTRATION))
        for kind, value in (
                ('ALIAS', {'base_type': 'INTEGER'}),
                ('ENUM', {'enum_values': ['a', 'b']}),
                ('STRUCT', {'fields': [{'name': 'a', 'type': 'INTEGER'}]}),
                ('UNION', {'fields': [{'name': 'a', 'type': 'INTEGER'}]})):
            draft = {'name': 'qa_type', 'type_kind': kind,
                     'base_type': 'ignored', 'enum_values': ['ignored'],
                     'fields': [{'ignored': True}], **value}
            result = visual.validate({'resource_kind': 'type',
                                      'operation_id': 'create',
                                      'draft': draft})
            self.assertTrue(result['valid'], result['errors'])
            inactive = {'base_type', 'enum_values', 'fields'} - set(value)
            for field_id in inactive:
                self.assertNotIn(field_id, result['draft'])
        invalid = visual.validate({'resource_kind': 'type',
                                   'operation_id': 'create',
                                   'draft': {
                                       'name': 'bad', 'type_kind': 'ENUM',
                                       'enum_values': []}})
        self.assertFalse(invalid['valid'])

    def test_duckdb_type_forms_select_only_native_kind_fields(self):
        form = DUCKDB_ADMINISTRATION._form('type', 'create')
        fields = {item['field_id']: item for item in form['fields']}
        self.assertEqual('ALIAS', fields['type_kind']['default'])
        for kind, expected in (('ALIAS', {'base_type'}),
                               ('ENUM', {'enum_values'}),
                               ('STRUCT', {'fields'}),
                               ('UNION', {'fields'})):
            with self.subTest(kind=kind):
                active = {key for key in ('base_type', 'enum_values', 'fields')
                          if ProviderVisualAdministration._field_active(
                              fields[key], {'type_kind': kind})}
                self.assertEqual(expected, active)
                self.assertTrue(all(fields[key]['required'] for key in active))
        enum = fields['enum_values']
        for value in (['', ' ', "it's", '序列'],):
            admitted, error = ProviderVisualAdministration._validate_field(
                enum, value)
            self.assertIsNone(error)
            self.assertEqual(value, admitted)
        for value in ([], ['same', 'same'], [1]):
            _, error = ProviderVisualAdministration._validate_field(
                enum, value)
            self.assertIsNotNone(error)
        invalid_members = [[], [{'name': 'a'}], [
            {'name': 'a', 'type': 'INTEGER', 'unsupported': True}]]
        for value in invalid_members:
            _, error = ProviderVisualAdministration._validate_field(
                fields['fields'], value)
            self.assertIsNotNone(error)

    def test_duckdb_visual_types_execute_all_four_native_kinds(self):
        import duckdb
        validate = ProviderVisualAdministration._validate_field
        form = DUCKDB_ADMINISTRATION._form('type', 'create')
        with tempfile.TemporaryDirectory() as temporary:
            route = {'database': str(Path(temporary) / 'types.duckdb'),
                     'filesystem_root': temporary, 'route_id': 'type-test'}
            client = duckdb_client()
            try:
                cases = [
                    ('ALIAS', {'base_type': 'INTEGER'},
                     'SELECT 42::qa_alias', 42),
                    ('ENUM', {'enum_values': ['', "it's", '序列']},
                     "SELECT ''::qa_enum::VARCHAR", ''),
                    ('STRUCT', {'fields': [
                        {'name': 'a', 'type': 'INTEGER'},
                        {'name': 'b', 'type': 'VARCHAR'}]},
                     "SELECT ({'a': 42, 'b': 'ok'}::qa_struct).a", 42),
                    ('UNION', {'fields': [
                        {'name': 'n', 'type': 'INTEGER'},
                        {'name': 's', 'type': 'VARCHAR'}]},
                     'SELECT (union_value(n := 42)::qa_union).n', 42),
                ]
                for kind, values, query, expected in cases:
                    with self.subTest(kind=kind):
                        draft = {'name': 'qa_' + kind.lower(),
                                 'type_kind': kind, **values}
                        for field in form['fields']:
                            key = field['field_id']
                            if key in draft:
                                draft[key], error = validate(field, draft[key])
                                self.assertIsNone(error)
                        plan = DUCKDB_ADMINISTRATION.plan({
                            'resource_kind': 'type', 'operation_id': 'create',
                            'target_resource': None, 'draft': draft,
                            '_provider_route': route})
                        DUCKDB_ADMINISTRATION.apply(client, {
                            'provider_payload': plan['provider_payload']})
                        connection = duckdb.connect(route['database'])
                        try:
                            observed = connection.execute(query).fetchone()[0]
                            self.assertEqual(expected, observed)
                            connection.execute('BEGIN TRANSACTION')
                            connection.execute('DROP TYPE ' + draft['name'])
                            connection.execute('ROLLBACK')
                            observed = connection.execute(query).fetchone()[0]
                            self.assertEqual(expected, observed)
                        finally:
                            connection.close()
            finally:
                client.close()

    def test_duckdb_profile_contributes_exact_native_query_plans(self):
        self.assertEqual((
            ('DuckDB physical query plan', 'EXPLAIN {source}'),
            ('DuckDB profiled query plan', 'EXPLAIN ANALYZE {source}'),
        ), DUCKDB_PROFILE.query_plan_templates)

    def test_duckdb_database_forms_are_exact_and_not_generic(self):
        database = next(
            item for item in DUCKDB_ADMINISTRATION.catalog(
                catalog_for_engine('duckdb')
            )['objects'] if item['resource_kind'] == 'database'
        )
        operations = {
            item['operation_id']: item for item in database['operations']
        }
        self.assertNotIn('alter', operations)
        self.assertEqual(
            ['name', 'config'],
            [
                field['field_id']
                for field in operations['create']['form']['fields']
            ],
        )
        self.assertEqual(
            ['directory', 'format'],
            [
                field['field_id']
                for field in operations['export_database']['form']['fields']
            ],
        )

    def test_duckdb_file_deletion_rejects_foreign_files_and_live_wal(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            foreign = root / 'foreign.duckdb'
            foreign.write_text('not DuckDB', encoding='utf-8')
            route = {
                'database': str(foreign), 'filesystem_root': temporary,
            }
            with self.assertRaisesRegex(
                    RelationalClientError, 'no DuckDB database header'):
                drop_duckdb_database_file(route, str(foreign))
            self.assertTrue(foreign.exists())

            try:
                import duckdb
            except ImportError:
                self.skipTest('DuckDB driver is not installed')
            database = root / 'guarded.duckdb'
            duckdb.connect(str(database)).close()
            wal = Path(str(database) + '.wal')
            wal.touch()
            route['database'] = str(database)
            with self.assertRaisesRegex(
                    RelationalClientError, 'WAL sidecar'):
                drop_duckdb_database_file(route, str(database))
            self.assertTrue(database.exists())

    def test_duckdb_database_lifecycle_properties_and_native_operations(self):
        try:
            import duckdb
        except ImportError:
            self.skipTest('DuckDB driver is not installed')
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = root / 'lifecycle.duckdb'
            route = {
                'database': str(database), 'filesystem_root': temporary,
                'database_create_root': temporary,
                'route_id': 'duckdb-lifecycle-test',
            }
            client = duckdb_client()
            created = DUCKDB_ADMINISTRATION.plan({
                'resource_kind': 'database', 'operation_id': 'create',
                'target_resource': None,
                'draft': {'name': 'lifecycle', 'config': {'threads': 2}},
                '_provider_route': route,
            })
            result = DUCKDB_ADMINISTRATION.apply(client, {
                'provider_payload': created['provider_payload'],
            })
            self.assertEqual(str(database), result[
                'endpoint_database_target'
            ]['database'])
            self.assertTrue(database.is_file())
            connection = duckdb.connect(str(database))
            try:
                connection.execute(
                    'CREATE TABLE inventory(id INTEGER, name VARCHAR)'
                )
                connection.execute("INSERT INTO inventory VALUES (1, 'one')")
                resources = duckdb_resources(connection, {})
            finally:
                connection.close()
            native = next(
                item['native'] for item in resources
                if item['resource_kind'] == 'database'
            )
            self.assertEqual('duckdb', native['database_type'])
            self.assertEqual('v1.5.2', native['runtime_version'])
            self.assertGreater(native['file_bytes'], 0)

            target = {
                'resource_id': 'database:lifecycle',
                'resource_kind': 'database',
                'display_name': 'lifecycle',
                'display_path': ['lifecycle'],
                'extensions': {'cdeadmin': {
                    'database_target_id': 'duckdb-lifecycle-target',
                    'native_name': str(database),
                }},
            }
            for operation in (
                    'checkpoint', 'force_checkpoint', 'vacuum', 'analyze'):
                planned = DUCKDB_ADMINISTRATION.plan({
                    'resource_kind': 'database',
                    'operation_id': operation,
                    'target_resource': target, 'draft': {},
                    '_provider_route': route,
                })
                DUCKDB_ADMINISTRATION.apply(client, {
                    'provider_payload': planned['provider_payload'],
                })

            export_directory = root / 'logical-export'
            exported = DUCKDB_ADMINISTRATION.plan({
                'resource_kind': 'database',
                'operation_id': 'export_database',
                'target_resource': target,
                'draft': {
                    'directory': str(export_directory), 'format': 'PARQUET',
                },
                '_provider_route': route,
            })
            DUCKDB_ADMINISTRATION.apply(client, {
                'provider_payload': exported['provider_payload'],
            })
            self.assertTrue((export_directory / 'schema.sql').is_file())
            self.assertTrue((export_directory / 'load.sql').is_file())

            imported_database = root / 'imported.duckdb'
            imported_route = {**route, 'database': str(imported_database)}
            duckdb.connect(str(imported_database)).close()
            imported_target = {
                **target, 'resource_id': 'database:imported',
                'display_name': 'imported', 'display_path': ['imported'],
            }
            imported = DUCKDB_ADMINISTRATION.plan({
                'resource_kind': 'database',
                'operation_id': 'import_database',
                'target_resource': imported_target,
                'draft': {'directory': str(export_directory)},
                '_provider_route': imported_route,
            })
            DUCKDB_ADMINISTRATION.apply(client, {
                'provider_payload': imported['provider_payload'],
            })
            with duckdb.connect(str(imported_database)) as connection:
                self.assertEqual((1, 'one'), connection.execute(
                    'SELECT * FROM inventory'
                ).fetchone())

            dropped = DUCKDB_ADMINISTRATION.plan({
                'resource_kind': 'database', 'operation_id': 'drop',
                'target_resource': target,
                'draft': {'confirmation': str(database)},
                '_provider_route': route,
            })
            DUCKDB_ADMINISTRATION.apply(client, {
                'provider_payload': dropped['provider_payload'],
            })
            self.assertFalse(database.exists())
            client.close()

    def test_duckdb_retained_session_owns_commit_and_rollback(self):
        try:
            import duckdb
        except ImportError:
            self.skipTest('DuckDB driver is not installed')
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / 'transactions.duckdb'
            with duckdb.connect(str(database)) as connection:
                connection.execute(
                    "CREATE TABLE state(id INTEGER PRIMARY KEY, value VARCHAR)"
                )
                connection.execute("INSERT INTO state VALUES (1, 'original')")
            route = {
                'database': str(database), 'filesystem_root': temporary,
                'route_id': 'duckdb-transaction-test',
            }
            client = duckdb_client()
            session = client.open_session({'route': route})
            try:
                token = client.execute(session, {
                    'source': "UPDATE state SET value = 'rolled-back' "
                              'WHERE id = 1',
                })
                client.describe_result(token)
                client.control_transaction(session, 'rollback')
                self.assertEqual(
                    ('original',), session.execute(
                        'SELECT value FROM state WHERE id = 1'
                    ).fetchone(),
                )
                token = client.execute(session, {
                    'source': "UPDATE state SET value = 'committed' "
                              'WHERE id = 1',
                })
                client.describe_result(token)
                client.control_transaction(session, 'commit')
                self.assertEqual(
                    ('committed',), session.execute(
                        'SELECT value FROM state WHERE id = 1'
                    ).fetchone(),
                )
            finally:
                client.close_session(session)
                client.close()
            with duckdb.connect(str(database), read_only=True) as observer:
                self.assertEqual(
                    ('committed',), observer.execute(
                        'SELECT value FROM state WHERE id = 1'
                    ).fetchone(),
                )

    def test_exact_duckdb_driver_executes_structured_table_and_row_workflow(
        self,
    ):
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / 'exact.duckdb'
            route = {
                'database': str(database), 'filesystem_root': temporary,
                'route_id': 'duckdb-test',
            }
            target = {
                'resource_id': 'table:exact:main:widgets',
                'resource_kind': 'table', 'display_name': 'widgets',
                'display_path': ['exact', 'main', 'widgets'],
            }
            client = duckdb_client()
            try:
                created = DUCKDB_ADMINISTRATION.plan({
                    'resource_kind': 'table', 'operation_id': 'create',
                    'target_resource': None,
                    'draft': {
                        'name': 'widgets', 'parent': 'main',
                        'columns': [
                            {
                                'name': 'id', 'type': 'INTEGER',
                                'nullable': False, 'primary_key': True,
                            },
                            {'name': 'name', 'type': 'VARCHAR'},
                        ],
                        'constraints': [],
                    },
                    '_provider_route': route,
                })
                DUCKDB_ADMINISTRATION.apply(client, {
                    'provider_payload': created['provider_payload'],
                })
                inserted = DUCKDB_ADMINISTRATION.plan({
                    'resource_kind': 'table', 'operation_id': 'insert',
                    'target_resource': target,
                    'draft': {
                        'values': {'id': 7, 'name': 'duck'}, 'options': {},
                    },
                    '_provider_route': route,
                })
                DUCKDB_ADMINISTRATION.apply(client, {
                    'provider_payload': inserted['provider_payload'],
                })
                page = DUCKDB_ADMINISTRATION.read_rows(client, {
                    '_provider_route': route,
                    'target_resource': target,
                })
                self.assertTrue(page['editable'])
                self.assertEqual('duck', page['rows'][0]['values']['name'])
                enum_plan = DUCKDB_ADMINISTRATION.plan({
                    'resource_kind': 'type', 'operation_id': 'create',
                    'target_resource': None,
                    'draft': {
                        'name': 'mood', 'type_kind': 'ENUM',
                        'base_type': '', 'enum_values': ['ok', 'great'],
                        'fields': [],
                    },
                    '_provider_route': route,
                })
                DUCKDB_ADMINISTRATION.apply(client, {
                    'provider_payload': enum_plan['provider_payload'],
                })
                macro_plan = DUCKDB_ADMINISTRATION.plan({
                    'resource_kind': 'macro', 'operation_id': 'create',
                    'target_resource': None,
                    'draft': {
                        'name': 'double_value', 'parameters': ['value'],
                        'table_macro': False, 'expression': 'value * 2',
                    },
                    '_provider_route': route,
                })
                DUCKDB_ADMINISTRATION.apply(client, {
                    'provider_payload': macro_plan['provider_payload'],
                })
                materialization_plan = DUCKDB_ADMINISTRATION.plan({
                    'resource_kind': 'materialization',
                    'operation_id': 'create',
                    'target_resource': None,
                    'draft': {
                        'name': 'widget_rollup', 'database': 'main',
                        'select': (
                            'SELECT name, count(*) AS item_count '
                            'FROM widgets GROUP BY name'
                        ),
                    },
                    '_provider_route': route,
                })
                self.assertIn(
                    'CREATE TABLE "main"."widget_rollup" AS SELECT',
                    materialization_plan['command_preview']['statements'][0][
                        'source'
                    ],
                )
                DUCKDB_ADMINISTRATION.apply(client, {
                    'provider_payload': materialization_plan[
                        'provider_payload'
                    ],
                })
                connection = client.open_session({'route': route})
                try:
                    cursor = connection.execute(
                        'SELECT double_value(4), CAST(\'ok\' AS mood), '
                        '(SELECT item_count FROM widget_rollup)'
                    )
                    self.assertEqual((8, 'ok', 1), cursor.fetchone())
                finally:
                    client.close()
            finally:
                client.close()


if __name__ == '__main__':
    unittest.main()
