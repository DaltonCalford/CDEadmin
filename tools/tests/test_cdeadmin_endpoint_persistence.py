##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Upgrade/downgrade tests for additive CDEadmin endpoint persistence."""

from __future__ import annotations

import importlib.util
import io
import re
import sys
import tempfile
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MIGRATION_PATH = (
    ROOT / 'web/migrations/versions/cde_endpoint_persistence_v1_.py'
)
MODEL_PATH = ROOT / 'web/pgadmin/model/__init__.py'

try:
    import sqlalchemy as sa
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    MIGRATION_DEPENDENCIES = True
except ImportError:
    MIGRATION_DEPENDENCIES = False

try:
    from flask import Flask
    import flask_sqlalchemy  # noqa: F401
    MODEL_DEPENDENCIES = True
except ImportError:
    MODEL_DEPENDENCIES = False


def load_migration():
    specification = importlib.util.spec_from_file_location(
        'cde_endpoint_persistence_test_migration', MIGRATION_PATH
    )
    if specification is None or specification.loader is None:
        raise RuntimeError(f'cannot load migration {MIGRATION_PATH}')
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def load_model():
    """Load model definitions with minimal dependency stubs, not pgAdmin."""
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
    module_name = 'cdeadmin_endpoint_model_test'
    try:
        specification = importlib.util.spec_from_file_location(
            module_name, MODEL_PATH
        )
        if specification is None or specification.loader is None:
            raise RuntimeError(f'cannot load model {MODEL_PATH}')
        module = importlib.util.module_from_spec(specification)
        sys.modules[module_name] = module
        specification.loader.exec_module(module)
        return module
    finally:
        for name, previous in saved.items():
            if previous is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous


class EndpointPersistenceSourceTests(unittest.TestCase):
    def test_model_version_and_tables_are_declared(self):
        source = MODEL_PATH.read_text(encoding='utf-8')
        self.assertRegex(source, r'SCHEMA_VERSION\s*=\s*55\b')
        for table in (
            'cde_endpoint',
            'cde_endpoint_runtime_identity',
            'cde_endpoint_route',
            'cde_endpoint_secret_reference',
            'cde_endpoint_tls_profile',
            'cde_endpoint_evidence_snapshot',
            'cde_endpoint_extension_profile',
            'cde_semantic_model',
            'cde_semantic_model_revision',
            'cde_report_delivery_occurrence',
        ):
            self.assertIn(f"__tablename__ = '{table}'", source)

    def test_migration_is_linear_reversible_and_does_not_claim_version(self):
        source = MIGRATION_PATH.read_text(encoding='utf-8')
        self.assertIn(
            "down_revision = 'normalize_locked_text_default'", source
        )
        self.assertRegex(source, r'def downgrade\(\):[\s\S]+op\.drop_table')
        self.assertNotIn('18.3', source)
        self.assertNotRegex(source, r'UPDATE\s+(server|sharedserver)\b')
        self.assertNotIn('table.c.password', source)
        self.assertNotIn('table.c.tunnel_password', source)

    def test_endpoint_migration_is_the_only_graph_head(self):
        revisions = set()
        predecessors = set()
        for path in (ROOT / 'web/migrations/versions').glob('*.py'):
            source = path.read_text(encoding='utf-8')
            revision = re.search(r"^revision\s*=\s*'([^']+)'", source,
                                 re.MULTILINE)
            predecessor = re.search(
                r"^down_revision\s*=\s*'([^']+)'", source, re.MULTILINE
            )
            if revision:
                revisions.add(revision.group(1))
            if predecessor:
                predecessors.add(predecessor.group(1))
        self.assertEqual(
            {'cde_report_delivery_v1'}, revisions - predecessors
        )

    def test_model_keeps_legacy_passwords_out_of_endpoint_json(self):
        source = MODEL_PATH.read_text(encoding='utf-8')
        endpoint_start = source.index('class EndpointProfile')
        endpoint_source = source[endpoint_start:]
        self.assertNotRegex(endpoint_source, r'\bpassword\s*=\s*db\.Column')
        self.assertIn('class EndpointSecretReference', endpoint_source)

    def test_new_legacy_registrations_have_additive_endpoint_hooks(self):
        source = MODEL_PATH.read_text(encoding='utf-8')
        self.assertIn("@event.listens_for(Server, 'after_insert')", source)
        self.assertIn(
            "@event.listens_for(SharedServer, 'after_insert')", source
        )
        self.assertIn('def _create_legacy_endpoint(', source)


@unittest.skipUnless(
    MODEL_DEPENDENCIES,
    'Flask-SQLAlchemy is required for model lifecycle integration tests',
)
class EndpointPersistenceModelTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.model = load_model()
        self.app = Flask('cdeadmin-endpoint-model-test')
        database = Path(self.temporary.name) / 'config.db'
        self.app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{database}'
        self.app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
        self.model.db.init_app(self.app)
        self.context = self.app.app_context()
        self.context.push()
        self.model.db.create_all()
        self.model.db.session.execute(
            self.model.User.__table__.insert(),
            {
                'id': 1,
                'username': 'endpoint-test-user',
                'active': True,
                'auth_source': 'internal',
                'fs_uniquifier': 'endpoint-test-user-1',
                'locked': False,
            },
        )
        group = self.model.ServerGroup(
            id=1, user_id=1, name='Endpoint Test Group'
        )
        self.model.db.session.add(group)
        self.model.db.session.commit()

    def tearDown(self):
        self.model.db.session.remove()
        self.model.db.drop_all()
        self.context.pop()
        self.temporary.cleanup()

    def test_new_server_and_shared_server_receive_endpoint_records(self):
        server = self.model.Server(
            id=10,
            user_id=1,
            servergroup_id=1,
            name='Legacy PostgreSQL',
            host='localhost',
            port=5432,
            maintenance_db='postgres',
            username='postgres',
            save_password=0,
            use_ssh_tunnel=0,
            tunnel_authentication=0,
            tunnel_prompt_password=0,
            shared=True,
            kerberos_conn=False,
            cloud_status=0,
            is_adhoc=0,
        )
        self.model.db.session.add(server)
        self.model.db.session.commit()
        endpoint = self.model.EndpointProfile.query.filter_by(
            legacy_server_id=10
        ).one()
        self.assertEqual('legacy_native', endpoint.endpoint_mode)
        self.assertIsNone(endpoint.provider_version)
        self.assertIsNone(endpoint.profile_version)
        self.assertEqual(
            'unverified', endpoint.runtime_identity.verification_state
        )
        self.assertEqual(2, len(endpoint.secret_references))

        shared = self.model.SharedServer(
            id=20,
            osid=10,
            user_id=1,
            server_owner='endpoint-test-user',
            servergroup_id=1,
            name='Shared PostgreSQL',
            host='localhost',
            port=5432,
            maintenance_db='postgres',
            username='postgres',
            save_password=0,
            use_ssh_tunnel=0,
            tunnel_authentication=0,
            tunnel_prompt_password=0,
            shared=True,
            kerberos_conn=False,
        )
        self.model.db.session.add(shared)
        self.model.db.session.commit()
        shared_endpoint = self.model.EndpointProfile.query.filter_by(
            legacy_shared_server_id=20
        ).one()
        self.assertNotEqual(endpoint.id, shared_endpoint.id)
        namespace_fields = (
            'pool_namespace', 'session_namespace', 'cache_namespace',
            'diagnostic_namespace',
        )
        for field in namespace_fields:
            self.assertNotEqual(
                getattr(endpoint, field), getattr(shared_endpoint, field)
            )
        self.model.db.session.delete(shared)
        self.model.db.session.commit()
        self.assertIsNone(self.model.EndpointProfile.query.filter_by(
            legacy_shared_server_id=20
        ).first())
        self.model.db.session.delete(server)
        self.model.db.session.commit()
        self.assertIsNone(self.model.EndpointProfile.query.filter_by(
            legacy_server_id=10
        ).first())

    def test_provider_registration_persists_exact_profile_and_safe_route(self):
        server = self.model.Server(
            id=30,
            user_id=1,
            servergroup_id=1,
            name='Provider endpoint',
            host='db.example.test',
            port=3306,
            maintenance_db='application',
            username='application_user',
            save_password=0,
            use_ssh_tunnel=0,
            tunnel_authentication=0,
            tunnel_prompt_password=0,
            shared=False,
            kerberos_conn=False,
            cloud_status=0,
            is_adhoc=0,
        )
        server._cde_endpoint_registration = {
            'experience_family': 'qualified-relational',
            'provider_id': 'org.example.qualified',
            'provider_version': '1.2.3',
            'profile_id': 'qualified-native',
            'profile_version': '4.5.6',
            'target_adapter_id': 'qualified-wire-client',
            'target_adapter_version': '7.8.9',
        }
        server._cde_endpoint_route_configuration = {
            'host': 'db.example.test',
            'port': 3306,
            'user': 'application_user',
            'database': 'application',
        }
        self.model.db.session.add(server)
        self.model.db.session.commit()

        endpoint = self.model.EndpointProfile.query.filter_by(
            legacy_server_id=30
        ).one()
        self.assertEqual('qualified-relational', endpoint.experience_family)
        self.assertEqual('org.example.qualified', endpoint.provider_id)
        self.assertEqual('1.2.3', endpoint.provider_version)
        self.assertEqual('qualified-native', endpoint.profile_id)
        self.assertEqual('4.5.6', endpoint.profile_version)
        self.assertEqual('cde_server_create', endpoint.created_from)
        self.assertEqual(
            'qualified-relational',
            endpoint.runtime_identity.declared_runtime_family,
        )
        route = endpoint.routes[0]
        self.assertIn('db.example.test', route.configuration)
        self.assertNotIn('password', route.configuration.casefold())
        self.assertEqual(2, len(endpoint.secret_references))

    def test_provider_registration_creates_typed_bundle_references(self):
        server = self.model.Server(
            id=31, user_id=1, servergroup_id=1,
            name='Typed credentials', host='db.example.test', port=27017,
            maintenance_db='admin', username='operator', save_password=1,
            use_ssh_tunnel=0, tunnel_authentication=0,
            tunnel_prompt_password=0, shared=False, kerberos_conn=False,
            cloud_status=0, is_adhoc=0,
        )
        server._cde_endpoint_registration = {
            'experience_family': 'mongodb',
            'provider_id': 'org.example.mongodb',
            'provider_version': '1.0', 'profile_id': 'mongodb-native',
            'profile_version': '8.2.6',
            'target_adapter_id': 'mongodb-wire-client',
            'target_adapter_version': '4.17.0',
            'requires_secret': False,
            'secret_fields': [
                {'secret_kind': 'database_password'},
                {'secret_kind': 'cloud_session_token'},
            ],
        }
        server._cde_endpoint_route_configuration = {
            'host': 'db.example.test', 'port': 27017,
            'credential_kinds': ['database_password'],
        }
        self.model.db.session.add(server)
        self.model.db.session.commit()

        endpoint = self.model.EndpointProfile.query.filter_by(
            legacy_server_id=31
        ).one()
        references = {
            item.secret_kind: item.secret_reference
            for item in endpoint.secret_references
        }
        self.assertEqual(
            'server:31:password:database_password',
            references['database_password'],
        )
        self.assertEqual(
            'server:31:password:cloud_session_token',
            references['cloud_session_token'],
        )
        self.assertEqual(
            'server:31:tunnel_password', references['tunnel_password']
        )


@unittest.skipUnless(
    MIGRATION_DEPENDENCIES,
    'SQLAlchemy and Alembic are required for migration integration tests',
)
class EndpointPersistenceMigrationTests(unittest.TestCase):
    def setUp(self):
        self.engine = sa.create_engine('sqlite:///:memory:')
        self.connection = self.engine.connect()
        self.connection.exec_driver_sql('PRAGMA foreign_keys=ON')
        metadata = sa.MetaData()
        self.user = sa.Table(
            'user', metadata,
            sa.Column('id', sa.Integer(), primary_key=True),
        )
        self.server = sa.Table(
            'server', metadata,
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('user_id', sa.Integer(), sa.ForeignKey('user.id'),
                      nullable=False),
            sa.Column('password', sa.LargeBinary()),
            sa.Column('tunnel_password', sa.LargeBinary()),
        )
        self.sharedserver = sa.Table(
            'sharedserver', metadata,
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('osid', sa.Integer(), sa.ForeignKey('server.id'),
                      nullable=False),
            sa.Column('user_id', sa.Integer(), sa.ForeignKey('user.id')),
            sa.Column('password', sa.LargeBinary()),
            sa.Column('tunnel_password', sa.LargeBinary()),
        )
        metadata.create_all(self.connection)
        self.database_secret = b'database-secret-sentinel'
        self.tunnel_secret = b'tunnel-secret-sentinel'
        self.shared_secret = b'shared-secret-sentinel'
        self.connection.execute(self.user.insert(), [{'id': 1}, {'id': 2}])
        self.connection.execute(self.server.insert(), {
            'id': 10,
            'user_id': 1,
            'password': self.database_secret,
            'tunnel_password': self.tunnel_secret,
        })
        self.connection.execute(self.sharedserver.insert(), {
            'id': 20,
            'osid': 10,
            'user_id': 2,
            'password': self.shared_secret,
            'tunnel_password': None,
        })
        self.connection.commit()
        context = MigrationContext.configure(self.connection)
        self.operations = Operations(context)
        self.migration = load_migration()
        self.migration.op = self.operations

    def tearDown(self):
        self.connection.close()
        self.engine.dispose()

    def rows(self, statement):
        return self.connection.exec_driver_sql(statement).mappings().all()

    def test_upgrade_is_additive_unverified_and_redacted(self):
        self.migration.upgrade()
        endpoints = self.rows('SELECT * FROM cde_endpoint ORDER BY id')
        self.assertEqual(2, len(endpoints))
        for endpoint in endpoints:
            self.assertEqual('postgresql', endpoint['experience_family'])
            self.assertEqual('legacy_native', endpoint['endpoint_mode'])
            self.assertEqual(
                'org.pgadmin.postgresql', endpoint['provider_id']
            )
            self.assertIsNone(endpoint['provider_version'])
            self.assertEqual(
                'postgresql-unverified-migrated', endpoint['profile_id']
            )
            self.assertIsNone(endpoint['profile_version'])
            self.assertIsNone(endpoint['target_adapter_version'])
            namespaces = {
                endpoint['pool_namespace'],
                endpoint['session_namespace'],
                endpoint['cache_namespace'],
                endpoint['diagnostic_namespace'],
            }
            self.assertEqual(4, len(namespaces))
        all_namespaces = {
            value
            for endpoint in endpoints
            for value in (
                endpoint['pool_namespace'],
                endpoint['session_namespace'],
                endpoint['cache_namespace'],
                endpoint['diagnostic_namespace'],
            )
        }
        self.assertEqual(8, len(all_namespaces))

        runtimes = self.rows(
            'SELECT * FROM cde_endpoint_runtime_identity'
        )
        self.assertEqual(2, len(runtimes))
        for runtime in runtimes:
            self.assertEqual('postgresql', runtime['declared_runtime_family'])
            self.assertIsNone(runtime['declared_runtime_version'])
            self.assertIsNone(runtime['verified_runtime_family'])
            self.assertIsNone(runtime['verified_runtime_version'])
            self.assertEqual('unverified', runtime['verification_state'])

        references = self.rows(
            'SELECT * FROM cde_endpoint_secret_reference ORDER BY secret_kind'
        )
        self.assertEqual(4, len(references))
        serialized = repr(references)
        for secret in (
            self.database_secret, self.tunnel_secret, self.shared_secret
        ):
            self.assertNotIn(secret.decode(), serialized)

        compatibility = self.rows(
            'SELECT legacy_kind, legacy_id FROM '
            'cde_endpoint_legacy_compat ORDER BY legacy_kind'
        )
        self.assertEqual(
            [('server', 10), ('sharedserver', 20)],
            [(row['legacy_kind'], row['legacy_id'])
             for row in compatibility],
        )
        self.assertEqual(2, len(self.rows('SELECT * FROM cde_endpoint_route')))
        self.assertEqual(
            2, len(self.rows('SELECT * FROM cde_endpoint_tls_profile'))
        )
        self.assertEqual(
            2,
            len(self.rows('SELECT * FROM cde_endpoint_evidence_snapshot')),
        )
        self.assertEqual(
            2,
            len(self.rows('SELECT * FROM cde_endpoint_extension_profile')),
        )
        legacy = self.rows('SELECT * FROM server WHERE id = 10')[0]
        shared = self.rows('SELECT * FROM sharedserver WHERE id = 20')[0]
        self.assertEqual(self.database_secret, legacy['password'])
        self.assertEqual(self.tunnel_secret, legacy['tunnel_password'])
        self.assertEqual(self.shared_secret, shared['password'])

    def test_mode_is_part_of_every_derived_namespace(self):
        endpoint_id = self.migration._stable_id('endpoint', 'test', 1)
        purposes = ('pool', 'session', 'cache', 'diagnostic')
        for purpose in purposes:
            legacy = self.migration._namespace_id(
                endpoint_id, 'legacy_native', purpose
            )
            native = self.migration._namespace_id(
                endpoint_id, 'scratchbird_native', purpose
            )
            self.assertNotEqual(legacy, native)

    def test_downgrade_preserves_legacy_rows_and_protected_secrets(self):
        self.migration.upgrade()
        self.migration.downgrade()
        inspector = sa.inspect(self.connection)
        self.assertFalse(any(
            table.startswith('cde_endpoint')
            for table in inspector.get_table_names()
        ))
        self.assertNotIn(
            'cde_endpoint_legacy_compat', inspector.get_view_names()
        )
        legacy = self.rows('SELECT * FROM server WHERE id = 10')[0]
        shared = self.rows('SELECT * FROM sharedserver WHERE id = 20')[0]
        self.assertEqual(self.database_secret, legacy['password'])
        self.assertEqual(self.tunnel_secret, legacy['tunnel_password'])
        self.assertEqual(self.shared_secret, shared['password'])
        self.assertEqual(10, shared['osid'])

    def test_schema_ddl_compiles_for_postgresql(self):
        output = io.StringIO()
        context = MigrationContext.configure(
            dialect_name='postgresql',
            opts={'as_sql': True, 'output_buffer': output},
        )
        migration = load_migration()
        migration.op = Operations(context)
        migration._create_tables()
        migration._create_compatibility_view()
        migration.downgrade()
        statements = output.getvalue()
        self.assertIn('CREATE TABLE cde_endpoint', statements)
        self.assertIn('CREATE VIEW cde_endpoint_legacy_compat', statements)
        self.assertIn('DROP TABLE cde_endpoint', statements)


if __name__ == '__main__':
    unittest.main()
