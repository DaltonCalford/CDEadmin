##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Exact provider-owned server and database form contract tests."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import ModuleType


ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / 'web'
if str(WEB) not in sys.path:
    sys.path.insert(0, str(WEB))
if 'pgadmin' not in sys.modules:
    package = ModuleType('pgadmin')
    package.__path__ = [str(WEB / 'pgadmin')]
    sys.modules['pgadmin'] = package

from pgadmin.cdeadmin.endpoints import (  # noqa: E402
    EndpointRegistrationError,
    EndpointService,
    registration_profile,
    registration_profiles,
)
from pgadmin.cdeadmin.providers.form_contracts import (  # noqa: E402
    ProviderFormContractError,
    assert_form_contract_coverage,
    provider_form_contract,
)


class ProviderFormContractTests(unittest.TestCase):

    def test_every_active_profile_owns_all_required_forms(self):
        profiles = registration_profiles()
        self.assertEqual(26, len(profiles))
        self.assertTrue(assert_form_contract_coverage(profiles))
        expected_database_operations = {
            'define', 'connect', 'create', 'edit', 'alter', 'drop',
            'remove',
        }
        form_ids = set()
        for profile in profiles:
            contract = profile['form_contract']
            self.assertEqual(profile['profile_id'], contract['profile_id'])
            self.assertEqual(
                {'define', 'edit', 'remove'},
                set(contract['server']['forms']),
            )
            self.assertEqual(
                expected_database_operations,
                set(contract['database']['forms']),
            )
            for scope in ('server', 'database'):
                for form in contract[scope]['forms'].values():
                    self.assertNotIn(form['form_id'], form_ids)
                    form_ids.add(form['form_id'])
        self.assertEqual(260, len(form_ids))

    def test_provider_forms_expose_native_database_identity_and_options(self):
        firebird = registration_profile(
            'firebird-native'
        )['form_contract']['database']
        mongo = registration_profile(
            'mongodb-native'
        )['form_contract']['database']
        redis = registration_profile(
            'redis-native'
        )['form_contract']['database']
        ycql = registration_profile(
            'yugabytedb-ycql'
        )['form_contract']['database']

        self.assertEqual('database_path', firebird['forms']['create'][
            'fields'
        ][0]['field_id'])
        self.assertIn('no_db_triggers', {
            field['field_id'] for field in firebird['forms']['define'][
                'fields'
            ]
        })
        self.assertIn('read_concern_level', {
            field['field_id'] for field in mongo['forms']['connect']['fields']
        })
        self.assertEqual('number', redis['forms']['define']['fields'][0][
            'control'
        ])
        self.assertFalse(redis['forms']['create']['supported'])
        self.assertEqual('keyspace', ycql['lifecycle_resource_kind'])
        self.assertIn('replication', {
            field['field_id'] for field in ycql['forms']['create']['fields']
        } | {
            field['field_id'] for field in ycql['forms']['define']['fields']
        })

    def test_firebird_database_create_contract_matches_execution_form(self):
        from pgadmin.cdeadmin.visual_admin.catalog import catalog_for_engine

        contract = registration_profile(
            'firebird-native'
        )['form_contract']['database']['forms']['create']
        database = next(
            item for item in catalog_for_engine('firebird')['objects']
            if item['resource_kind'] == 'database'
        )
        operation = next(
            item for item in database['operations']
            if item['operation_id'] == 'create'
        )
        self.assertEqual(
            [field['field_id'] for field in contract['fields']],
            [field['field_id'] for field in operation['form']['fields']],
        )

    def test_firebird_database_drop_contract_is_not_generic(self):
        from pgadmin.cdeadmin.visual_admin.catalog import catalog_for_engine

        contract = registration_profile(
            'firebird-native'
        )['form_contract']['database']['forms']['drop']
        database = next(
            item for item in catalog_for_engine('firebird')['objects']
            if item['resource_kind'] == 'database'
        )
        operation = next(
            item for item in database['operations']
            if item['operation_id'] == 'drop'
        )
        self.assertEqual('firebird_database_drop', operation['form_id'])
        self.assertEqual(
            ['confirmation'],
            [field['field_id'] for field in operation['form']['fields']],
        )
        self.assertEqual(
            [field['field_id'] for field in contract['fields']],
            [field['field_id'] for field in operation['form']['fields']],
        )

    def test_mysql_database_forms_expose_only_native_9_7_options(self):
        from pgadmin.cdeadmin.visual_admin.catalog import catalog_for_engine

        contract = registration_profile(
            'mysql-native'
        )['form_contract']['database']['forms']
        self.assertEqual(
            ['name', 'if_not_exists', 'character_set', 'collation',
             'encryption'],
            [field['field_id'] for field in contract['create']['fields']],
        )
        self.assertEqual(
            ['character_set', 'collation', 'encryption', 'read_only'],
            [field['field_id'] for field in contract['alter']['fields']],
        )
        self.assertEqual(
            ['confirmation'],
            [field['field_id'] for field in contract['drop']['fields']],
        )
        from pgadmin.cdeadmin.providers.mysql_family.provider import (
            MYSQL_ADMINISTRATION,
        )
        database = next(
            item for item in MYSQL_ADMINISTRATION.catalog(
                catalog_for_engine('mysql')
            )['objects']
            if item['resource_kind'] == 'database'
        )
        operations = {
            item['operation_id']: item for item in database['operations']
        }
        for operation_id in ('create', 'alter', 'drop'):
            self.assertEqual(
                [field['field_id'] for field in contract[
                    operation_id
                ]['fields']],
                [field['field_id'] for field in operations[
                    operation_id
                ]['form']['fields']],
            )

    def test_sqlite_file_lifecycle_contract_matches_execution_forms(self):
        from pgadmin.cdeadmin.visual_admin.catalog import catalog_for_engine

        contract = registration_profile(
            'sqlite-native'
        )['form_contract']['database']['forms']
        database = next(
            item for item in catalog_for_engine('sqlite')['objects']
            if item['resource_kind'] == 'database'
        )
        operations = {
            item['operation_id']: item for item in database['operations']
        }
        for operation_id in ('create', 'alter', 'drop'):
            self.assertEqual(
                [field['field_id'] for field in contract[
                    operation_id
                ]['fields']],
                [field['field_id'] for field in operations[
                    operation_id
                ]['form']['fields']],
            )

    def test_profiles_do_not_fall_back_to_a_generic_form(self):
        with self.assertRaisesRegex(
            ProviderFormContractError, 'no database form contract'
        ):
            provider_form_contract({
                'profile_id': 'unknown-native',
                'engine_id': 'unknown',
            })

    def test_server_edit_schema_locks_to_the_exact_interface_profile(self):
        ui_source = (
            WEB / 'pgadmin/browser/server_groups/servers/static/js/'
            'server.ui.js'
        ).read_text(encoding='utf-8')
        node_source = (
            WEB / 'pgadmin/browser/server_groups/servers/static/js/server.js'
        ).read_text(encoding='utf-8')
        self.assertIn(
            'this.registrationProfiles = exactProfile ? [exactProfile]',
            ui_source,
        )
        self.assertIn(
            'this.providerFormContract = exactProfile?.form_contract?.server',
            ui_source,
        )
        self.assertIn(
            '{engineId, profileId: initialProfile?.profile_id}', node_source
        )

    def test_database_form_validation_rejects_cross_provider_fields(self):
        firebird = registration_profile('firebird-native')
        values = EndpointService._database_form_values(firebird, 'define', {
            'database': '/firebird/data/example.fdb',
            'display_name': 'Example Firebird',
            'charset': 'UTF8',
            'no_db_triggers': False,
        })
        self.assertEqual({
            'charset': 'UTF8',
            'dbkey_scope': 'TRANSACTION',
            'no_db_triggers': False,
            'no_gc': False,
            'transaction_access': 'WRITE',
            'transaction_isolation': 'SNAPSHOT',
            'transaction_lock_timeout': -1,
        }, values)
        with self.assertRaisesRegex(
            EndpointRegistrationError, 'not owned by this provider'
        ):
            EndpointService._database_form_values(firebird, 'define', {
                'database': '/firebird/data/example.fdb',
                'aws_secret_access_key': 'must-not-cross-forms',
            })

    def test_database_form_validation_enforces_native_select_options(self):
        mongodb = registration_profile('mongodb-native')
        with self.assertRaisesRegex(
            EndpointRegistrationError, 'not an admitted option'
        ):
            EndpointService._database_form_values(mongodb, 'connect', {
                'read_preference': 'firebird',
            })

    def test_new_registrations_persist_database_as_a_server_child(self):
        source = (
            WEB / 'pgadmin/browser/server_groups/servers/__init__.py'
        ).read_text(encoding='utf-8')
        self.assertIn('.retain_registered_database(server, {', source)
        registration_block = source[source.index(
            "if provider_endpoint and data.get('db')"
        ):source.index('connected = False')]
        self.assertNotIn(".get('multiple')", registration_block)


if __name__ == '__main__':
    unittest.main()
