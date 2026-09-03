##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Non-mutating pgAdmin profile migration tests for CDE-PREP-130."""

from __future__ import annotations

import importlib.util
import json
import re
import sqlite3
import sys
import tempfile
import types
import unittest
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[2]
PROFILE_PATH = ROOT / 'web/pgadmin/cdeadmin/migration/profile.py'
ORM_TARGET_PATH = ROOT / 'web/pgadmin/cdeadmin/migration/orm_target.py'
MIGRATION_PATH = (
    ROOT / 'web/migrations/versions/cde_profile_migration_v1_.py'
)
MODEL_PATH = ROOT / 'web/pgadmin/model/__init__.py'

try:
    import sqlalchemy as sa
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from flask import Flask
    import flask_sqlalchemy  # noqa: F401
    INTEGRATION_DEPENDENCIES = True
except ImportError:
    INTEGRATION_DEPENDENCIES = False


def load_module(name, path):
    specification = importlib.util.spec_from_file_location(name, path)
    if specification is None or specification.loader is None:
        raise RuntimeError(f'cannot load {path}')
    module = importlib.util.module_from_spec(specification)
    sys.modules[name] = module
    specification.loader.exec_module(module)
    return module


PROFILE = load_module('cdeadmin_profile_migration_test_profile', PROFILE_PATH)


def load_orm_target():
    packages = (
        ('pgadmin', ROOT / 'web/pgadmin'),
        ('pgadmin.cdeadmin', ROOT / 'web/pgadmin/cdeadmin'),
        ('pgadmin.cdeadmin.migration',
         ROOT / 'web/pgadmin/cdeadmin/migration'),
    )
    for name, path in packages:
        if name not in sys.modules:
            package = types.ModuleType(name)
            package.__path__ = [str(path)]
            sys.modules[name] = package
    sys.modules['pgadmin.cdeadmin.migration.profile'] = PROFILE
    return load_module(
        'pgadmin.cdeadmin.migration.orm_target', ORM_TARGET_PATH
    )


def load_model():
    stubs = {}
    babel = types.ModuleType('flask_babel')
    babel.gettext = lambda value, *args, **kwargs: value
    stubs['flask_babel'] = babel
    security = types.ModuleType('flask_security')
    security.UserMixin = type('UserMixin', (), {})
    security.RoleMixin = type('RoleMixin', (), {})
    stubs['flask_security'] = security
    config = types.ModuleType('config')
    config.CONFIG_DATABASE_CONNECTION_POOL_SIZE = 5
    config.CONFIG_DATABASE_CONNECTION_MAX_OVERFLOW = 10
    stubs['config'] = config
    saved = {name: sys.modules.get(name) for name in stubs}
    sys.modules.update(stubs)
    try:
        return load_module('cdeadmin_profile_migration_test_model', MODEL_PATH)
    finally:
        for name, previous in saved.items():
            if previous is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous


def create_source_profile(path):
    connection = sqlite3.connect(path)
    connection.executescript('''
        CREATE TABLE version (name TEXT PRIMARY KEY, value INTEGER NOT NULL);
        CREATE TABLE user (
            id INTEGER PRIMARY KEY, username TEXT, auth_source TEXT
        );
        CREATE TABLE servergroup (
            id INTEGER PRIMARY KEY, user_id INTEGER, name TEXT
        );
        CREATE TABLE server (
            id INTEGER PRIMARY KEY, user_id INTEGER, servergroup_id INTEGER,
            name TEXT, host TEXT, port INTEGER, maintenance_db TEXT,
            username TEXT, role TEXT, comment TEXT, service TEXT,
            use_ssh_tunnel INTEGER, tunnel_host TEXT, tunnel_port INTEGER,
            tunnel_username TEXT, tunnel_authentication INTEGER,
            tunnel_prompt_password INTEGER, tunnel_keep_alive INTEGER,
            shared INTEGER, kerberos_conn INTEGER, cloud_status INTEGER,
            connection_params TEXT, prepare_threshold INTEGER, tags TEXT,
            is_adhoc INTEGER, post_connection_sql TEXT,
            save_password INTEGER, password BLOB, tunnel_password BLOB
        );
        CREATE TABLE module_preference (id INTEGER PRIMARY KEY, name TEXT);
        CREATE TABLE preference_category (
            id INTEGER PRIMARY KEY, mid INTEGER, name TEXT
        );
        CREATE TABLE preferences (
            id INTEGER PRIMARY KEY, cid INTEGER, name TEXT
        );
        CREATE TABLE user_preferences (
            pid INTEGER, uid INTEGER, value TEXT
        );
        CREATE TABLE setting (
            user_id INTEGER, setting TEXT, value TEXT
        );
        CREATE TABLE application_state (
            uid INTEGER, id INTEGER, connection_info TEXT, tool_data BLOB
        );
        CREATE TABLE query_history (
            srno INTEGER, uid INTEGER, sid INTEGER, dbname TEXT,
            query_info BLOB, last_updated_flag TEXT
        );
    ''')
    connection.execute(
        "INSERT INTO version VALUES ('ConfigDB', 53)"
    )
    connection.execute(
        "INSERT INTO user VALUES (1, 'source-user', 'internal')"
    )
    connection.execute(
        "INSERT INTO servergroup VALUES (10, 1, 'Imported PostgreSQL')"
    )
    servers = (
        (
            20, 1, 10, 'Legacy PostgreSQL', 'db.example.test', 5432,
            'postgres', 'operator', None, 'source registration', None,
            0, None, None, None, 0, 0, 0, 1, 0, 0,
            '{"sslmode":"require"}', None, '["production"]', 0,
            None, 1, b'DATABASE-SECRET-SENTINEL',
            b'TUNNEL-SECRET-SENTINEL',
        ),
        (
            21, 1, 10, 'Adhoc PostgreSQL', 'adhoc.example.test', 5432,
            'postgres', 'operator', None, None, None, 0, None, None,
            None, 0, 0, 0, 0, 0, 0, '{}', None, None, 1, None, 0,
            None, None,
        ),
    )
    connection.executemany(
        'INSERT INTO server VALUES (' + ','.join('?' * 29) + ')', servers
    )
    connection.execute(
        "INSERT INTO module_preference VALUES (1, 'browser')"
    )
    connection.execute(
        "INSERT INTO preference_category VALUES (2, 1, 'display')"
    )
    connection.execute(
        "INSERT INTO preferences VALUES (3, 2, 'theme')"
    )
    connection.execute(
        "INSERT INTO user_preferences VALUES (3, 1, 'dark')"
    )
    connection.executemany(
        'INSERT INTO setting VALUES (1, ?, ?)',
        (
            ('Browser/Layout', '{"layout":"workspace"}'),
            ('Workspace/Layout/main', '{"panels":[]}'),
            ('unsupported/private-setting', 'do-not-import'),
        ),
    )
    connection.execute(
        'INSERT INTO application_state VALUES (1, 5, ?, ?)',
        ('{"server_id":20}', b'WORKSPACE-SECRET-SENTINEL'),
    )
    connection.execute(
        'INSERT INTO query_history VALUES (1, 1, 20, ?, ?, ?)',
        ('postgres', b'{"query":"select 1"}', 'Y'),
    )
    connection.commit()
    connection.close()


class ProfileMigrationPlanTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.source = Path(self.temporary.name) / 'pgadmin4.db'
        create_source_profile(self.source)
        self.reader = PROFILE.PgAdminProfileReader(self.source)
        self.service = PROFILE.ProfileMigrationService()

    def tearDown(self):
        self.temporary.cleanup()

    def test_reader_connection_is_query_only(self):
        connection = self.reader.connect()
        with self.assertRaises(sqlite3.OperationalError):
            connection.execute("UPDATE user SET username='changed'")
        connection.close()

    def test_default_dry_run_is_redacted_and_complete(self):
        summary = self.service.dry_run(self.reader, 1, 7)
        self.assertTrue(summary['dry_run'])
        self.assertEqual({
            'preference': 1,
            'server': 1,
            'server_group': 1,
            'workspace_setting': 2,
        }, summary['action_counts'])
        self.assertFalse(summary['secret_payloads_in_summary'])
        self.assertFalse(summary['source_path_in_summary'])

    def test_incompatibility_report_is_counted_without_payloads(self):
        plan = self.service.plan(self.reader, 1, 7)
        summary = plan.summary()
        self.assertEqual(1, summary['incompatibility_counts'][
            'adhoc-server-not-imported'
        ])
        self.assertEqual(1, summary['incompatibility_counts'][
            'encrypted-workspace-state-requires-rekey'
        ])
        self.assertEqual(1, summary['incompatibility_counts'][
            'unsupported-setting'
        ])

    def test_plan_never_reads_or_contains_protected_secret_values(self):
        plan = self.service.plan(self.reader, 1, 7)
        rendered = repr(plan)
        self.assertNotIn('DATABASE-SECRET-SENTINEL', rendered)
        self.assertNotIn('TUNNEL-SECRET-SENTINEL', rendered)
        self.assertNotIn('WORKSPACE-SECRET-SENTINEL', rendered)
        self.assertNotIn(str(self.source), json.dumps(plan.summary()))

    def test_postgresql_runtime_remains_unverified(self):
        plan = self.service.plan(self.reader, 1, 7)
        server = next(item for item in plan.actions
                      if item.kind == 'server')
        runtime = server.payload['runtime_identity']
        self.assertEqual('postgresql', runtime['declared_runtime_family'])
        self.assertEqual('unverified', runtime['verification_state'])
        self.assertIsNone(runtime['verified_runtime_version'])
        self.assertIsNone(runtime['declared_runtime_version'])

    def test_query_history_requires_explicit_selection(self):
        default = self.service.plan(self.reader, 1, 7)
        selected = self.service.plan(
            self.reader, 1, 7,
            PROFILE.MigrationSelection(query_history=True),
        )
        self.assertFalse(any(
            action.kind == 'query_history' for action in default.actions
        ))
        self.assertEqual(1, sum(
            action.kind == 'query_history' for action in selected.actions
        ))

    def test_preference_identity_uses_names_not_source_ids(self):
        plan = self.service.plan(self.reader, 1, 7)
        preference = next(item for item in plan.actions
                          if item.kind == 'preference')
        self.assertEqual('browser/display/theme', preference.source_key)
        self.assertNotIn('pid', preference.payload)

    def test_missing_user_is_refused(self):
        with self.assertRaisesRegex(PROFILE.MigrationError, 'does not exist'):
            self.service.plan(self.reader, 999, 7)

    def test_saved_password_selection_requires_server_selection(self):
        selection = PROFILE.MigrationSelection(
            servers=False, saved_passwords=True
        )
        with self.assertRaisesRegex(
                PROFILE.MigrationError, 'requires server migration'):
            self.service.plan(self.reader, 1, 7, selection)

    def test_plan_and_item_identity_are_deterministic(self):
        first = self.service.plan(self.reader, 1, 7)
        second = self.service.plan(self.reader, 1, 7)
        self.assertEqual(first.run_id, second.run_id)
        self.assertEqual(
            [item.fingerprint for item in first.actions],
            [item.fingerprint for item in second.actions],
        )


class ProfileMigrationExecutionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.source = Path(self.temporary.name) / 'pgadmin4.db'
        create_source_profile(self.source)
        self.reader = PROFILE.PgAdminProfileReader(self.source)
        self.service = PROFILE.ProfileMigrationService()
        self.target = PROFILE.InMemoryMigrationTarget()
        self.selection = PROFILE.MigrationSelection()
        self.consent = PROFILE.MigrationConsent(
            approved=True,
            categories=self.selection.categories(),
            reference='consent:test:profile-import',
        )

    def tearDown(self):
        self.temporary.cleanup()

    def test_apply_requires_explicit_consent(self):
        consent = PROFILE.MigrationConsent(
            approved=False,
            categories=self.selection.categories(),
            reference='consent:denied',
        )
        with self.assertRaisesRegex(PROFILE.MigrationError, 'consent'):
            self.service.apply(
                self.reader, 1, 7, self.target, consent, self.selection
            )

    def test_consent_must_cover_every_selected_category(self):
        consent = PROFILE.MigrationConsent(
            approved=True,
            categories=frozenset({'servers'}),
            reference='consent:incomplete',
        )
        with self.assertRaisesRegex(PROFILE.MigrationError, 'does not cover'):
            self.service.apply(
                self.reader, 1, 7, self.target, consent, self.selection
            )

    def test_apply_keeps_source_byte_identical_and_passwords_empty(self):
        before = self.reader.digest()
        summary = self.service.apply(
            self.reader, 1, 7, self.target, self.consent, self.selection
        )
        self.assertEqual(before, self.reader.digest())
        self.assertTrue(summary['source_unchanged'])
        server = next(
            value for (kind, _target), value in self.target.objects.items()
            if kind == 'server'
        )
        self.assertEqual(0, server['payload']['save_password'])
        self.assertIsNone(server['payload']['password'])
        self.assertFalse(server['payload']['shared'])

    def test_repeated_import_does_not_duplicate_any_target(self):
        first = self.service.apply(
            self.reader, 1, 7, self.target, self.consent, self.selection
        )
        count = len(self.target.objects)
        second = self.service.apply(
            self.reader, 1, 7, self.target, self.consent, self.selection
        )
        self.assertEqual(count, len(self.target.objects))
        self.assertEqual(
            sum(first['action_counts'].values()),
            second['outcome_counts']['already-applied'],
        )

    def test_rollback_is_marked_idempotent_and_allows_reimport(self):
        applied = self.service.apply(
            self.reader, 1, 7, self.target, self.consent, self.selection
        )
        rolled_back = self.target.rollback(applied['run_id'])
        self.assertEqual('rolled-back', rolled_back['status'])
        self.assertEqual({}, self.target.objects)
        again = self.target.rollback(applied['run_id'])
        self.assertEqual('already-rolled-back', again['status'])
        reapplied = self.service.apply(
            self.reader, 1, 7, self.target, self.consent, self.selection
        )
        self.assertGreater(reapplied['outcome_counts']['created'], 0)

    def test_target_failure_rolls_back_partial_import(self):
        class FailingTarget(PROFILE.InMemoryMigrationTarget):
            def apply_action(self, plan, action, secret_transfer=None):
                if action.kind == 'server':
                    raise RuntimeError('injected target failure')
                return super().apply_action(
                    plan, action, secret_transfer=secret_transfer
                )

        target = FailingTarget()
        with self.assertRaisesRegex(RuntimeError, 'injected'):
            self.service.apply(
                self.reader, 1, 7, target, self.consent, self.selection
            )
        self.assertEqual({}, target.objects)
        self.assertEqual({}, target.items)
        self.assertEqual({}, target.runs)

    def test_saved_password_import_requires_trusted_reencryption(self):
        selection = PROFILE.MigrationSelection(saved_passwords=True)
        consent = PROFILE.MigrationConsent(
            approved=True,
            categories=selection.categories(),
            reference='consent:secret-transfer',
        )
        with self.assertRaisesRegex(
                PROFILE.MigrationError, 're-encryption adapter'):
            self.service.apply(
                self.reader, 1, 7, self.target, consent, selection
            )

    def test_trusted_secret_adapter_exposes_only_opaque_reference(self):
        selection = PROFILE.MigrationSelection(saved_passwords=True)
        consent = PROFILE.MigrationConsent(
            approved=True,
            categories=selection.categories(),
            reference='consent:secret-transfer',
        )
        locators = []

        def transfer(locator):
            locators.append(locator)
            return {
                'reencrypted': True,
                'database_ciphertext': b'TARGET-CIPHERTEXT',
                'secret_reference': 'keyring:cdeadmin:server-20',
            }

        summary = self.service.apply(
            self.reader, 1, 7, self.target, consent, selection,
            secret_transfer=transfer,
        )
        self.assertEqual(1, len(locators))
        self.assertNotIn('TARGET-CIPHERTEXT', json.dumps(summary))
        self.assertNotIn('DATABASE-SECRET-SENTINEL', repr(self.target.items))

    def test_secret_adapter_must_attest_reencryption(self):
        selection = PROFILE.MigrationSelection(saved_passwords=True)
        consent = PROFILE.MigrationConsent(
            approved=True, categories=selection.categories(),
            reference='consent:secret-transfer',
        )
        with self.assertRaisesRegex(PROFILE.MigrationError, 'did not attest'):
            self.service.apply(
                self.reader, 1, 7, self.target, consent, selection,
                secret_transfer=lambda locator: {'reencrypted': False},
            )


class ProfileMigrationSourceTests(unittest.TestCase):
    def test_schema_version_models_and_linear_migration_are_declared(self):
        model_source = MODEL_PATH.read_text(encoding='utf-8')
        migration_source = MIGRATION_PATH.read_text(encoding='utf-8')
        self.assertRegex(model_source, r'SCHEMA_VERSION\s*=\s*55\b')
        self.assertIn(
            "__tablename__ = 'cde_profile_migration_run'", model_source
        )
        self.assertIn(
            "__tablename__ = 'cde_profile_migration_item'", model_source
        )
        self.assertIn(
            "down_revision = 'cde_endpoint_persistence_v1'",
            migration_source,
        )
        self.assertNotRegex(
            migration_source.casefold(), r'attach\s+database|update\s+server'
        )

    def test_profile_migration_is_only_revision_graph_head(self):
        revisions = set()
        predecessors = set()
        for path in (ROOT / 'web/migrations/versions').glob('*.py'):
            source = path.read_text(encoding='utf-8')
            revision = re.search(
                r"^revision\s*=\s*'([^']+)'", source, re.MULTILINE
            )
            predecessor = re.search(
                r"^down_revision\s*=\s*'([^']+)'", source, re.MULTILINE
            )
            if revision:
                revisions.add(revision.group(1))
            if predecessor:
                predecessors.add(predecessor.group(1))
        self.assertEqual(
            {'cde_semantic_models_v1'}, revisions - predecessors
        )


@unittest.skipUnless(
    INTEGRATION_DEPENDENCIES,
    'SQLAlchemy, Alembic, and Flask-SQLAlchemy are required',
)
class ProfileMigrationIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.source = root / 'source-pgadmin4.db'
        self.target_path = root / 'target-cdeadmin.db'
        create_source_profile(self.source)
        self.model = load_model()
        self.app = Flask('cdeadmin-profile-migration-test')
        self.app.config['SQLALCHEMY_DATABASE_URI'] = \
            f'sqlite:///{self.target_path}'
        self.app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
        self.model.db.init_app(self.app)
        self.context = self.app.app_context()
        self.context.push()
        self.model.db.create_all()
        self.model.db.session.add(self.model.User(
            id=7,
            username='target-user',
            active=True,
            auth_source='internal',
            fs_uniquifier='target-user-7',
            locked=False,
        ))
        module = self.model.ModulePreference(id=1, name='browser')
        category = self.model.PreferenceCategory(
            id=2, mid=1, name='display'
        )
        preference = self.model.Preferences(
            id=3, cid=2, name='theme'
        )
        self.model.db.session.add_all([module, category, preference])
        self.model.db.session.commit()
        self.orm_module = load_orm_target()
        self.reader = PROFILE.PgAdminProfileReader(self.source)
        self.service = PROFILE.ProfileMigrationService()
        self.target = self.orm_module.OrmMigrationTarget(self.model)
        self.selection = PROFILE.MigrationSelection(query_history=True)
        self.consent = PROFILE.MigrationConsent(
            approved=True,
            categories=self.selection.categories(),
            reference='consent:integration',
        )

    def tearDown(self):
        self.model.db.session.remove()
        self.model.db.drop_all()
        self.context.pop()
        self.temporary.cleanup()

    def test_orm_import_is_idempotent_unverified_and_reversible(self):
        before = self.reader.digest()
        first = self.service.apply(
            self.reader, 1, 7, self.target, self.consent, self.selection
        )
        self.assertEqual(before, self.reader.digest())
        self.assertEqual(1, self.model.Server.query.count())
        server = self.model.Server.query.one()
        self.assertIsNone(server.password)
        self.assertFalse(server.shared)
        endpoint = self.model.EndpointProfile.query.filter_by(
            legacy_server_id=server.id
        ).one()
        self.assertEqual(
            'unverified', endpoint.runtime_identity.verification_state
        )
        self.assertIsNone(
            endpoint.runtime_identity.verified_runtime_version
        )
        self.assertEqual(1, self.model.UserPreference.query.count())
        self.assertEqual(2, self.model.Setting.query.count())
        self.assertEqual(1, self.model.QueryHistoryModel.query.count())
        self.service.apply(
            self.reader, 1, 7, self.target, self.consent, self.selection
        )
        self.assertEqual(1, self.model.Server.query.count())
        self.assertEqual(1, self.model.QueryHistoryModel.query.count())
        rolled_back = self.target.rollback(first['run_id'])
        self.assertEqual('rolled-back', rolled_back['status'])
        self.assertEqual(0, self.model.Server.query.count())
        self.assertEqual(0, self.model.Setting.query.count())
        self.assertEqual(0, self.model.QueryHistoryModel.query.count())

    def test_orm_target_refuses_source_target_alias(self):
        original = self.target.session
        self.target.session = SimpleNamespace(
            get_bind=lambda: SimpleNamespace(
                url=SimpleNamespace(database=str(self.source))
            )
        )
        try:
            with self.assertRaisesRegex(
                    PROFILE.MigrationError, 'must be distinct'):
                self.target.assert_source_is_distinct(self.source)
        finally:
            self.target.session = original

    def test_ledger_migration_upgrades_and_downgrades_cleanly(self):
        engine = sa.create_engine('sqlite:///:memory:')
        connection = engine.connect()
        metadata = sa.MetaData()
        sa.Table(
            'user', metadata,
            sa.Column('id', sa.Integer(), primary_key=True),
        )
        metadata.create_all(connection)
        context = MigrationContext.configure(connection)
        operations = Operations(context)
        migration = load_module(
            'cdeadmin_profile_migration_test_revision', MIGRATION_PATH
        )
        migration.op = operations
        migration.upgrade()
        inspector = sa.inspect(connection)
        self.assertTrue(inspector.has_table('cde_profile_migration_run'))
        self.assertTrue(inspector.has_table('cde_profile_migration_item'))
        migration.downgrade()
        inspector = sa.inspect(connection)
        self.assertFalse(inspector.has_table('cde_profile_migration_run'))
        self.assertFalse(inspector.has_table('cde_profile_migration_item'))
        connection.close()
        engine.dispose()


if __name__ == '__main__':
    unittest.main()
