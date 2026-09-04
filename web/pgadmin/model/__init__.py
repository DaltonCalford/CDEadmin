##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Defines the models for the configuration database.

If any of the models are updated, you (yes, you, the developer) MUST do two
things:

1) Increment SCHEMA_VERSION below

2) Create an Alembic migratio to ensure that the appropriate changes are
   made to the config database to upgrade it to the new version.
"""

from flask_babel import gettext
from flask_security import UserMixin, RoleMixin
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import event
from sqlalchemy.ext.mutable import MutableDict
import sqlalchemy.types as types
import json
import uuid
import config

##########################################################################
#
# The schema version is used to track when upgrades are needed to the
# configuration database. Increment this whenever changes are made to the
# model or data, AND ensure the upgrade code is added to setup.py
#
##########################################################################

SCHEMA_VERSION = 55

##########################################################################
#
# And now we return to our regularly scheduled programming:
#
##########################################################################

db = SQLAlchemy(
    engine_options={
        'pool_size': config.CONFIG_DATABASE_CONNECTION_POOL_SIZE,
        'max_overflow': config.CONFIG_DATABASE_CONNECTION_MAX_OVERFLOW})


USER_ID = 'user.id'
SERVER_ID = 'server.id'
ENDPOINT_ID = 'cde_endpoint.id'
CASCADE_STR = "all, delete-orphan"


class UserScopedMixin:
    """Mixin for models that store per-user data.

    Provides for_user() as the default scoped query entry point.
    Models with a 'user_id' column or a 'uid' column are supported
    automatically — the mixin detects which column name is used.

    Usage:
        # Instead of:
        Process.query.filter_by(user_id=current_user.id, pid=pid)
        # Use:
        Process.for_user(pid=pid)
    """

    @classmethod
    def _user_column(cls):
        """Return the user-scoping column for this model."""
        if hasattr(cls, 'user_id'):
            return cls.user_id
        if hasattr(cls, 'uid'):
            return cls.uid
        raise AttributeError(
            f"{cls.__name__} has no user_id or uid column"
        )

    @classmethod
    def _user_column_name(cls):
        """Return the column name string ('user_id' or 'uid')."""
        if hasattr(cls, 'user_id'):
            return 'user_id'
        if hasattr(cls, 'uid'):
            return 'uid'
        raise AttributeError(
            f"{cls.__name__} has no user_id or uid column"
        )

    @classmethod
    def for_user(cls, user_id=None, **kwargs):
        """Query scoped to a specific user (defaults to current_user).

        Args:
            user_id: Explicit user ID. If None, uses current_user.id.
            **kwargs: Additional filter_by arguments.

        Returns:
            A SQLAlchemy query filtered by the user's ID.
        """
        from flask_security import current_user as cu
        uid = user_id if user_id is not None else cu.id
        kwargs[cls._user_column_name()] = uid
        return cls.query.filter_by(**kwargs)


# Define models
roles_users = db.Table(
    'roles_users',
    db.Column('user_id', db.Integer(), db.ForeignKey(USER_ID)),
    db.Column('role_id', db.Integer(), db.ForeignKey('role.id'))
)


class PgAdminDbArrayString(types.TypeDecorator):
    cache_ok = True
    impl = types.String

    def process_bind_param(self, value, dialect):
        try:
            if len(value) == 0:
                return None

            return ",".join(value)
        except Exception as _:
            return None

    def process_result_value(self, value, dialect):
        try:
            if value == '':
                return []

            return value.split(',')
        except Exception as _:
            return []


class PgAdminDbBinaryString(types.TypeDecorator):
    """
    To make binary string storing compatible with both
    SQLite and PostgreSQL, convert the bin data to hex
    to store and convert hex back to binary to get
    """
    cache_ok = True
    impl = types.String

    def process_bind_param(self, value, dialect):
        return value.hex() if hasattr(value, 'hex') \
            else value

    def process_result_value(self, value, dialect):
        try:
            return bytes.fromhex(value)
        except Exception as _:
            return value


class Version(db.Model):
    """Version numbers for reference/upgrade purposes"""
    __tablename__ = 'version'
    name = db.Column(db.String(32), primary_key=True)
    value = db.Column(db.Integer(), nullable=False)


class Role(db.Model, RoleMixin):
    """Define a security role"""
    __tablename__ = 'role'
    id = db.Column(db.Integer(), primary_key=True)
    name = db.Column(db.String(128), unique=True, nullable=False)
    description = db.Column(db.String(256), nullable=False)
    # permissions needs to be an array, use custom type to support
    # both SQLite and PostgreSQL
    permissions = db.Column(PgAdminDbArrayString())

    def get_permissions(self):
        from pgadmin.tools.user_management.PgAdminPermissions \
            import AllPermissionTypes
        if self.name == 'Administrator':
            return AllPermissionTypes.list()

        return super().get_permissions()


# We override the default UserMixin to change behaviour of has_permission
# Administrator has all permissions
class CustomUserMixin(UserMixin):
    def has_permission(self, permission: str) -> bool:
        if 'Administrator' in self.roles:
            return True

        return super().has_permission(permission)


class User(db.Model, UserMixin):
    """Define a user object"""
    __tablename__ = 'user'
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(256), nullable=True)
    username = db.Column(db.String(64), unique=True, nullable=False)
    password = db.Column(PgAdminDbBinaryString())
    active = db.Column(db.Boolean(), nullable=False)
    confirmed_at = db.Column(db.DateTime())
    masterpass_check = db.Column(PgAdminDbBinaryString())
    roles = db.relationship('Role', secondary=roles_users,
                            backref=db.backref('users', lazy='dynamic'))
    auth_source = db.Column(db.String(16), unique=True, nullable=False)
    # fs_uniquifier is required by flask-security-too >= 4.
    fs_uniquifier = db.Column(db.String(255), unique=True, nullable=False,
                              default=(lambda _: uuid.uuid4().hex))
    login_attempts = db.Column(db.Integer, default=0)
    locked = db.Column(db.Boolean(), default=False)

    @property
    def is_active(self):
        # Treat a locked account as inactive so Flask-Login's login_user()
        # refuses to mint a session, regardless of which view authenticated
        # the password. Without this, a lockout set by /authenticate/login
        # is bypassed by a direct POST to Flask-Security's /login.
        return self.active and not self.locked

    def is_locked(self, form_error=None):
        # Flask-Security's LoginForm.validate() calls this after password
        # verification and fails validation when it returns True, so True
        # means "locked, refuse the login". Flask-Security-Too up to and
        # including 5.8.1 had that test inverted (fixed upstream in 5.8.2 by
        # pallets-eco/flask-security#1267), which is why requirements.txt
        # floors the dependency at 5.8.2: on an older release the value
        # below would be read backwards and every unlocked user would be
        # refused a session.
        if self.locked:
            if form_error is not None:
                form_error.append(gettext(
                    'Your account is locked. Please contact the '
                    'Administrator.'))
            return True
        return False


class Setting(db.Model, UserScopedMixin):
    """Define a setting object"""
    __tablename__ = 'setting'
    user_id = db.Column(db.Integer, db.ForeignKey(USER_ID), primary_key=True)
    setting = db.Column(db.String(256), primary_key=True)
    value = db.Column(db.Text())


class ServerGroup(db.Model, UserScopedMixin):
    """Define a server group for the treeview"""
    __tablename__ = 'servergroup'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey(USER_ID), nullable=False)

    name = db.Column(db.String(128), nullable=False)
    __table_args__ = (db.UniqueConstraint('user_id', 'name'),)

    servers = db.relationship(
        'Server',
        back_populates='servergroup',
        lazy='select',
        cascade=CASCADE_STR
    )

    sharedservers = db.relationship(
        'SharedServer',
        back_populates='servergroup',
        lazy='select',
        cascade=CASCADE_STR
    )

    @property
    def serialize(self):
        """Return object data in easily serializable format"""
        return {
            'id': self.id,
            'user_id': self.user_id,
            'name': self.name,
        }


class Server(db.Model, UserScopedMixin):
    """Define a registered Postgres server"""
    __tablename__ = 'server'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey(USER_ID),
        nullable=False
    )
    servergroup_id = db.Column(
        db.Integer,
        db.ForeignKey('servergroup.id'),
        nullable=False
    )
    servergroup = db.relationship(
        'ServerGroup',
        back_populates='servers',
        lazy='joined'
    )
    name = db.Column(db.String(128), nullable=False)
    host = db.Column(db.String(128), nullable=True)
    port = db.Column(
        db.Integer(),
        db.CheckConstraint('port >= 1 AND port <= 65534'),
        nullable=False)
    maintenance_db = db.Column(db.String(1024), nullable=True)
    username = db.Column(db.String(64), nullable=False)
    password = db.Column(PgAdminDbBinaryString())
    save_password = db.Column(
        db.Integer(),
        db.CheckConstraint('save_password >= 0 AND save_password <= 1'),
        nullable=False
    )
    role = db.Column(db.String(64), nullable=True)
    comment = db.Column(db.String(1024), nullable=True)
    discovery_id = db.Column(db.String(128), nullable=True)
    db_res = db.Column(db.Text(), nullable=True)
    db_res_type = db.Column(db.String(32), default='databases')
    passexec_cmd = db.Column(db.Text(), nullable=True)
    passexec_expiration = db.Column(db.Integer(), nullable=True)
    bgcolor = db.Column(db.String(10), nullable=True)
    fgcolor = db.Column(db.String(10), nullable=True)
    service = db.Column(db.Text(), nullable=True)
    use_ssh_tunnel = db.Column(
        db.Integer(),
        db.CheckConstraint('use_ssh_tunnel >= 0 AND use_ssh_tunnel <= 1'),
        nullable=False
    )
    tunnel_host = db.Column(db.String(128), nullable=True)
    tunnel_port = db.Column(
        db.Integer(),
        db.CheckConstraint('port <= 65534'),
        nullable=True, default=22)
    tunnel_username = db.Column(db.String(64), nullable=True)
    tunnel_authentication = db.Column(
        db.Integer(),
        db.CheckConstraint('tunnel_authentication >= 0 AND '
                           'tunnel_authentication <= 1'),
        nullable=False
    )
    tunnel_identity_file = db.Column(db.String(64), nullable=True)
    tunnel_prompt_password = db.Column(
        db.Integer(), db.CheckConstraint(
            'tunnel_prompt_password >= 0 AND tunnel_prompt_password <= 1'),
        nullable=False
    )
    tunnel_password = db.Column(PgAdminDbBinaryString())
    tunnel_keep_alive = db.Column(db.Integer(), nullable=True, default=0)
    shared = db.Column(db.Boolean(), nullable=False)
    shared_username = db.Column(db.String(64), nullable=True)
    kerberos_conn = db.Column(db.Boolean(), nullable=False, default=0)
    cloud_status = db.Column(db.Integer(), nullable=False, default=0)
    connection_params = db.Column(MutableDict.as_mutable(types.JSON))
    prepare_threshold = db.Column(db.Integer(), nullable=True)
    tags = db.Column(types.JSON)
    is_adhoc = db.Column(
        db.Integer(),
        db.CheckConstraint('is_adhoc >= 0 AND is_adhoc <= 1'),
        nullable=False, default=0
    )
    post_connection_sql = db.Column(db.String(), nullable=True)
    endpoint_profile = db.relationship(
        'EndpointProfile',
        back_populates='legacy_server',
        uselist=False,
        cascade=CASCADE_STR
    )

    def clone(self):
        d = dict(self.__dict__)
        d.pop("id")  # get rid of id
        d.pop("_sa_instance_state")  # get rid of SQLAlchemy special attr
        copy = self.__class__(**d)
        return copy


class ModulePreference(db.Model):
    """Define a preferences table for any modules."""
    __tablename__ = 'module_preference'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(256), nullable=False)


class PreferenceCategory(db.Model):
    """Define a preferences category for each modules."""
    __tablename__ = 'preference_category'
    id = db.Column(db.Integer, primary_key=True)
    mid = db.Column(
        db.Integer,
        db.ForeignKey('module_preference.id'),
        nullable=False
    )
    name = db.Column(db.String(256), nullable=False)


class Preferences(db.Model):
    """Define a particular preference."""
    __tablename__ = 'preferences'
    id = db.Column(db.Integer, primary_key=True)
    cid = db.Column(
        db.Integer,
        db.ForeignKey('preference_category.id'),
        nullable=False
    )
    name = db.Column(db.String(1024), nullable=False)


class UserPreference(db.Model, UserScopedMixin):
    """Define the preference for a particular user."""
    __tablename__ = 'user_preferences'
    pid = db.Column(
        db.Integer, db.ForeignKey('preferences.id'), primary_key=True
    )
    uid = db.Column(
        db.Integer, db.ForeignKey(USER_ID), primary_key=True
    )
    value = db.Column(db.String(1024), nullable=False)


class DebuggerFunctionArguments(db.Model, UserScopedMixin):
    """Define the debugger input function arguments."""
    __tablename__ = 'debugger_function_arguments'
    user_id = db.Column(
        db.Integer, db.ForeignKey(USER_ID),
        nullable=False, primary_key=True
    )
    server_id = db.Column(db.Integer(), nullable=False, primary_key=True)
    database_id = db.Column(db.Integer(), nullable=False, primary_key=True)
    schema_id = db.Column(db.Integer(), nullable=False, primary_key=True)
    function_id = db.Column(db.Integer(), nullable=False, primary_key=True)
    arg_id = db.Column(db.Integer(), nullable=False, primary_key=True)
    is_null = db.Column(
        db.Integer(),
        db.CheckConstraint('is_null >= 0 AND is_null <= 1'),
        nullable=False
    )
    is_expression = db.Column(
        db.Integer(),
        db.CheckConstraint(
            'is_expression >= 0 AND is_expression <= 1'
        ),
        nullable=False
    )
    use_default = db.Column(
        db.Integer(),
        db.CheckConstraint(
            'use_default >= 0 AND use_default <= 1'
        ),
        nullable=False
    )

    value = db.Column(db.String(), nullable=True)


class Process(db.Model, UserScopedMixin):
    """Define the Process table."""
    __tablename__ = 'process'
    pid = db.Column(db.String(), nullable=False, primary_key=True)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey(USER_ID),
        nullable=False
    )
    command = db.Column(db.String(), nullable=False)
    desc = db.Column(db.String(), nullable=False)
    arguments = db.Column(db.String(), nullable=True)
    logdir = db.Column(db.String(), nullable=True)
    start_time = db.Column(db.String(), nullable=True)
    end_time = db.Column(db.String(), nullable=True)
    exit_code = db.Column(db.Integer(), nullable=True)
    acknowledge = db.Column(db.String(), nullable=True)
    utility_pid = db.Column(db.Integer, nullable=False)
    process_state = db.Column(db.Integer, nullable=False)
    server_id = db.Column(
        db.Integer,
        db.ForeignKey('server.id'),
        nullable=True
    )


class Keys(db.Model):
    """Define the keys table."""
    __tablename__ = 'keys'
    name = db.Column(db.String(), nullable=False, primary_key=True)
    value = db.Column(db.String(), nullable=False)


class QueryHistoryModel(db.Model, UserScopedMixin):
    """Define the history SQL table."""
    __tablename__ = 'query_history'
    srno = db.Column(db.Integer(), nullable=False, primary_key=True)
    uid = db.Column(
        db.Integer, db.ForeignKey(USER_ID), nullable=False, primary_key=True
    )
    sid = db.Column(
        db.Integer(), db.ForeignKey(SERVER_ID), nullable=False,
        primary_key=True)
    dbname = db.Column(db.String(), nullable=False, primary_key=True)
    query_info = db.Column(PgAdminDbBinaryString(), nullable=False)
    last_updated_flag = db.Column(db.String(), nullable=False)


class ApplicationState(db.Model, UserScopedMixin):
    """Define the application state SQL table."""
    __tablename__ = 'application_state'
    uid = db.Column(db.Integer(), db.ForeignKey(USER_ID), nullable=False,
                    primary_key=True)
    id = db.Column(db.Integer(), nullable=False, primary_key=True)
    connection_info = db.Column(MutableDict.as_mutable(types.JSON))
    tool_data = db.Column(PgAdminDbBinaryString())


class Database(db.Model):
    """
    Define a Database.
    """
    __tablename__ = 'database'
    id = db.Column(db.BigInteger, primary_key=True)
    schema_res = db.Column(db.String(256), nullable=True)
    server = db.Column(
        db.Integer,
        db.ForeignKey(SERVER_ID),
        nullable=False,
        primary_key=True
    )


class SharedServer(db.Model, UserScopedMixin):
    """Define a shared Postgres server"""

    __tablename__ = 'sharedserver'
    __table_args__ = (
        db.UniqueConstraint('osid', 'user_id',
                            name='uq_sharedserver_osid_user'),
    )
    id = db.Column(db.Integer, primary_key=True)
    osid = db.Column(
        db.Integer,
        db.ForeignKey(SERVER_ID),
        nullable=False
    )
    user_id = db.Column(
        db.Integer,
        db.ForeignKey(USER_ID)
    )
    server_owner = db.Column(
        db.String(128),
        db.ForeignKey('user.username')
    )
    servergroup_id = db.Column(
        db.Integer,
        db.ForeignKey('servergroup.id'),
        nullable=False
    )
    servergroup = db.relationship(
        'ServerGroup',
        back_populates='sharedservers',
        lazy='joined'
    )
    name = db.Column(db.String(128), nullable=False)
    host = db.Column(db.String(128), nullable=True)
    port = db.Column(
        db.Integer(),
        nullable=True)
    maintenance_db = db.Column(db.String(64), nullable=True)
    username = db.Column(db.String(64), nullable=False)
    password = db.Column(PgAdminDbBinaryString())
    save_password = db.Column(
        db.Integer(),
        db.CheckConstraint('save_password >= 0 AND save_password <= 1'),
        nullable=False
    )
    role = db.Column(db.String(64), nullable=True)
    comment = db.Column(db.String(1024), nullable=True)
    discovery_id = db.Column(db.String(128), nullable=True)
    bgcolor = db.Column(db.String(10), nullable=True)
    fgcolor = db.Column(db.String(10), nullable=True)
    service = db.Column(db.Text(), nullable=True)
    use_ssh_tunnel = db.Column(
        db.Integer(),
        db.CheckConstraint('use_ssh_tunnel >= 0 AND use_ssh_tunnel <= 1'),
        nullable=False
    )
    tunnel_host = db.Column(db.String(128), nullable=True)
    tunnel_port = db.Column(
        db.Integer(),
        db.CheckConstraint('port <= 65534'),
        nullable=True)
    tunnel_username = db.Column(db.String(64), nullable=True)
    tunnel_authentication = db.Column(
        db.Integer(),
        db.CheckConstraint('tunnel_authentication >= 0 AND '
                           'tunnel_authentication <= 1'),
        nullable=False
    )
    tunnel_identity_file = db.Column(db.String(64), nullable=True)
    tunnel_prompt_password = db.Column(
        db.Integer(), db.CheckConstraint(
            'tunnel_prompt_password >= 0 AND tunnel_prompt_password <= 1'),
        nullable=False
    )
    tunnel_password = db.Column(PgAdminDbBinaryString())
    tunnel_keep_alive = db.Column(db.Integer(), nullable=True)
    shared = db.Column(db.Boolean(), nullable=False)
    connection_params = db.Column(MutableDict.as_mutable(types.JSON))
    prepare_threshold = db.Column(db.Integer(), nullable=True)
    passexec_cmd = db.Column(db.Text(), nullable=True)
    passexec_expiration = db.Column(db.Integer(), nullable=True)
    kerberos_conn = db.Column(
        db.Boolean(), nullable=False, default=False
    )
    tags = db.Column(types.JSON)
    post_connection_sql = db.Column(db.String(), nullable=True)
    endpoint_profile = db.relationship(
        'EndpointProfile',
        back_populates='legacy_shared_server',
        uselist=False,
        cascade=CASCADE_STR
    )


class EndpointProfile(db.Model):
    """Additive multi-engine identity for a registered endpoint."""
    __tablename__ = 'cde_endpoint'
    __table_args__ = (
        db.CheckConstraint(
            "endpoint_mode IN ('legacy_native', 'scratchbird_native')",
            name='ck_cde_endpoint_mode'
        ),
        db.CheckConstraint(
            '(legacy_server_id IS NOT NULL AND '
            'legacy_shared_server_id IS NULL) OR '
            '(legacy_server_id IS NULL)',
            name='ck_cde_endpoint_legacy_source'
        ),
    )
    id = db.Column(db.String(36), primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey(USER_ID), nullable=True)
    legacy_server_id = db.Column(
        db.Integer,
        db.ForeignKey(SERVER_ID, ondelete='CASCADE'),
        nullable=True,
        unique=True
    )
    legacy_shared_server_id = db.Column(
        db.Integer,
        db.ForeignKey('sharedserver.id', ondelete='CASCADE'),
        nullable=True,
        unique=True
    )
    experience_family = db.Column(db.String(128), nullable=False)
    endpoint_mode = db.Column(db.String(40), nullable=False)
    provider_id = db.Column(db.String(128), nullable=False)
    provider_version = db.Column(db.String(64), nullable=True)
    profile_id = db.Column(db.String(128), nullable=False)
    profile_version = db.Column(db.String(64), nullable=True)
    profile_generation = db.Column(db.String(64), nullable=True)
    target_adapter_id = db.Column(db.String(128), nullable=False)
    target_adapter_version = db.Column(db.String(64), nullable=True)
    pool_namespace = db.Column(
        db.String(36), nullable=False, unique=True
    )
    session_namespace = db.Column(
        db.String(36), nullable=False, unique=True
    )
    cache_namespace = db.Column(
        db.String(36), nullable=False, unique=True
    )
    diagnostic_namespace = db.Column(
        db.String(36), nullable=False, unique=True
    )
    created_from = db.Column(db.String(32), nullable=False)

    legacy_server = db.relationship(
        'Server', back_populates='endpoint_profile'
    )
    legacy_shared_server = db.relationship(
        'SharedServer', back_populates='endpoint_profile'
    )
    runtime_identity = db.relationship(
        'EndpointRuntimeIdentity',
        back_populates='endpoint',
        uselist=False,
        cascade=CASCADE_STR
    )
    routes = db.relationship(
        'EndpointRoute', back_populates='endpoint', cascade=CASCADE_STR
    )
    secret_references = db.relationship(
        'EndpointSecretReference',
        back_populates='endpoint',
        cascade=CASCADE_STR
    )
    tls_profile = db.relationship(
        'EndpointTLSProfile',
        back_populates='endpoint',
        uselist=False,
        cascade=CASCADE_STR
    )
    evidence_snapshots = db.relationship(
        'EndpointEvidenceSnapshot',
        back_populates='endpoint',
        cascade=CASCADE_STR
    )
    extension_profile = db.relationship(
        'EndpointExtensionProfile',
        back_populates='endpoint',
        uselist=False,
        cascade=CASCADE_STR
    )
    semantic_models = db.relationship(
        'SemanticModelDefinition',
        back_populates='endpoint',
        cascade=CASCADE_STR
    )
    report_delivery_occurrences = db.relationship(
        'CDEReportDeliveryOccurrence',
        back_populates='endpoint',
        cascade=CASCADE_STR
    )


class SemanticModelDefinition(db.Model):
    """Current endpoint-scoped semantic model definition."""
    __tablename__ = 'cde_semantic_model'
    __table_args__ = (
        db.UniqueConstraint(
            'user_id', 'endpoint_id', 'name',
            name='uq_cde_semantic_model_name'
        ),
        db.CheckConstraint(
            "status IN ('draft', 'published', 'archived')",
            name='ck_cde_semantic_model_status'
        ),
    )
    id = db.Column(db.String(36), primary_key=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey(USER_ID, ondelete='CASCADE'), nullable=False
    )
    endpoint_id = db.Column(
        db.String(36), db.ForeignKey(ENDPOINT_ID, ondelete='CASCADE'),
        nullable=False
    )
    name = db.Column(db.String(256), nullable=False)
    status = db.Column(db.String(16), nullable=False, default='draft')
    revision = db.Column(db.Integer, nullable=False, default=1)
    __mapper_args__ = {
        'version_id_col': revision,
        'version_id_generator': False,
    }
    definition = db.Column(db.Text(), nullable=False)
    created_at = db.Column(db.DateTime(), nullable=False)
    updated_at = db.Column(db.DateTime(), nullable=False)
    endpoint = db.relationship(
        'EndpointProfile', back_populates='semantic_models'
    )
    revisions = db.relationship(
        'SemanticModelRevision', back_populates='model',
        cascade=CASCADE_STR
    )


class SemanticModelRevision(db.Model):
    """Immutable semantic-model revision snapshot."""
    __tablename__ = 'cde_semantic_model_revision'
    __table_args__ = (
        db.UniqueConstraint(
            'model_id', 'revision', name='uq_cde_semantic_model_revision'
        ),
    )
    id = db.Column(db.String(36), primary_key=True)
    model_id = db.Column(
        db.String(36),
        db.ForeignKey('cde_semantic_model.id', ondelete='CASCADE'),
        nullable=False
    )
    revision = db.Column(db.Integer, nullable=False)
    status = db.Column(db.String(16), nullable=False)
    definition = db.Column(db.Text(), nullable=False)
    created_at = db.Column(db.DateTime(), nullable=False)
    model = db.relationship(
        'SemanticModelDefinition', back_populates='revisions'
    )


class CDEReportDeliveryOccurrence(db.Model):
    """Durable, secret-free record of one report delivery attempt."""
    __tablename__ = 'cde_report_delivery_occurrence'
    __table_args__ = (
        db.UniqueConstraint(
            'user_id', 'endpoint_id', 'request_key',
            name='uq_cde_report_delivery_request'
        ),
        db.CheckConstraint(
            "state IN ('prepared', 'delivering', 'delivered', 'failed', "
            "'outcome_unknown')",
            name='ck_cde_report_delivery_state'
        ),
        db.CheckConstraint(
            "channel IN ('smtp', 's3')",
            name='ck_cde_report_delivery_channel'
        ),
        db.CheckConstraint(
            "export_format IN ('csv', 'json', 'jsonl', 'xlsx', 'svg', "
            "'pdf')",
            name='ck_cde_report_delivery_format'
        ),
    )
    id = db.Column(db.String(36), primary_key=True)
    request_key = db.Column(db.String(36), nullable=False)
    user_id = db.Column(
        db.Integer, db.ForeignKey(USER_ID, ondelete='CASCADE'), nullable=False
    )
    endpoint_id = db.Column(
        db.String(36), db.ForeignKey(ENDPOINT_ID, ondelete='CASCADE'),
        nullable=False
    )
    result_id = db.Column(db.String(128), nullable=False)
    profile_id = db.Column(db.String(128), nullable=False)
    channel = db.Column(db.String(16), nullable=False)
    export_format = db.Column(db.String(16), nullable=False)
    intent_digest = db.Column(db.String(64), nullable=False)
    target_summary = db.Column(db.Text(), nullable=False)
    state = db.Column(db.String(32), nullable=False)
    provider_receipt = db.Column(db.Text(), nullable=True)
    error_type = db.Column(db.String(128), nullable=True)
    created_at = db.Column(db.DateTime(), nullable=False)
    started_at = db.Column(db.DateTime(), nullable=True)
    completed_at = db.Column(db.DateTime(), nullable=True)
    endpoint = db.relationship(
        'EndpointProfile', back_populates='report_delivery_occurrences'
    )


class EndpointRuntimeIdentity(db.Model):
    """Declared target and separately verified runtime identity."""
    __tablename__ = 'cde_endpoint_runtime_identity'
    endpoint_id = db.Column(
        db.String(36),
        db.ForeignKey(ENDPOINT_ID, ondelete='CASCADE'),
        primary_key=True
    )
    declared_runtime_family = db.Column(db.String(128), nullable=False)
    declared_runtime_version = db.Column(db.String(64), nullable=True)
    verified_runtime_family = db.Column(db.String(128), nullable=True)
    verified_runtime_version = db.Column(db.String(64), nullable=True)
    verification_state = db.Column(db.String(32), nullable=False)
    verified_at = db.Column(db.DateTime(), nullable=True)
    verification_evidence_reference = db.Column(
        db.String(256), nullable=True
    )
    endpoint = db.relationship(
        'EndpointProfile', back_populates='runtime_identity'
    )


class EndpointRoute(db.Model):
    """Provider-owned route reference without embedded credentials."""
    __tablename__ = 'cde_endpoint_route'
    __table_args__ = (
        db.UniqueConstraint(
            'endpoint_id', 'priority', name='uq_cde_endpoint_route_priority'
        ),
    )
    id = db.Column(db.String(36), primary_key=True)
    endpoint_id = db.Column(
        db.String(36),
        db.ForeignKey(ENDPOINT_ID, ondelete='CASCADE'),
        nullable=False
    )
    route_kind = db.Column(db.String(64), nullable=False)
    route_reference = db.Column(db.String(256), nullable=False)
    priority = db.Column(db.Integer(), nullable=False)
    configuration = db.Column(db.Text(), nullable=False, default='{}')
    endpoint = db.relationship('EndpointProfile', back_populates='routes')


class EndpointSecretReference(db.Model):
    """Opaque reference to a protected secret owned outside endpoint JSON."""
    __tablename__ = 'cde_endpoint_secret_reference'
    __table_args__ = (
        db.UniqueConstraint(
            'endpoint_id', 'secret_kind',
            name='uq_cde_endpoint_secret_kind'
        ),
    )
    id = db.Column(db.String(36), primary_key=True)
    endpoint_id = db.Column(
        db.String(36),
        db.ForeignKey(ENDPOINT_ID, ondelete='CASCADE'),
        nullable=False
    )
    secret_kind = db.Column(db.String(64), nullable=False)
    storage_kind = db.Column(db.String(64), nullable=False)
    secret_reference = db.Column(db.String(256), nullable=False)
    endpoint = db.relationship(
        'EndpointProfile', back_populates='secret_references'
    )


class EndpointTLSProfile(db.Model):
    """TLS configuration reference with no copied paths or secret material."""
    __tablename__ = 'cde_endpoint_tls_profile'
    endpoint_id = db.Column(
        db.String(36),
        db.ForeignKey(ENDPOINT_ID, ondelete='CASCADE'),
        primary_key=True
    )
    tls_mode = db.Column(db.String(64), nullable=False)
    configuration_reference = db.Column(db.String(256), nullable=False)
    endpoint = db.relationship(
        'EndpointProfile', back_populates='tls_profile'
    )


class EndpointEvidenceSnapshot(db.Model):
    """Evidence reference and redacted migration state for an endpoint."""
    __tablename__ = 'cde_endpoint_evidence_snapshot'
    id = db.Column(db.String(36), primary_key=True)
    endpoint_id = db.Column(
        db.String(36),
        db.ForeignKey(ENDPOINT_ID, ondelete='CASCADE'),
        nullable=False
    )
    evidence_kind = db.Column(db.String(64), nullable=False)
    evidence_reference = db.Column(db.String(256), nullable=False)
    snapshot_data = db.Column(db.Text(), nullable=False)
    expires_at = db.Column(db.DateTime(), nullable=True)
    endpoint = db.relationship(
        'EndpointProfile', back_populates='evidence_snapshots'
    )


class EndpointExtensionProfile(db.Model):
    """Namespaced provider extension data governed by a schema reference."""
    __tablename__ = 'cde_endpoint_extension_profile'
    endpoint_id = db.Column(
        db.String(36),
        db.ForeignKey(ENDPOINT_ID, ondelete='CASCADE'),
        primary_key=True
    )
    schema_reference = db.Column(db.String(256), nullable=False)
    profile_data = db.Column(db.Text(), nullable=False, default='{}')
    redaction_state = db.Column(db.String(64), nullable=False)
    endpoint = db.relationship(
        'EndpointProfile', back_populates='extension_profile'
    )


class ProfileMigrationRun(db.Model, UserScopedMixin):
    """Consent receipt and rollback marker for a profile import."""
    __tablename__ = 'cde_profile_migration_run'
    __table_args__ = (
        db.UniqueConstraint(
            'user_id', 'source_profile_id', 'migration_version',
            name='uq_cde_profile_migration_source'
        ),
    )
    id = db.Column(db.String(36), primary_key=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey(USER_ID), nullable=False
    )
    source_profile_id = db.Column(db.String(64), nullable=False)
    source_snapshot_sha256 = db.Column(db.String(64), nullable=False)
    source_schema_version = db.Column(db.Integer(), nullable=True)
    migration_version = db.Column(db.String(32), nullable=False)
    selected_categories = db.Column(db.Text(), nullable=False)
    status = db.Column(db.String(32), nullable=False)
    consent_reference = db.Column(db.String(256), nullable=False)
    summary = db.Column(db.Text(), nullable=False, default='{}')
    incompatibility_report = db.Column(
        db.Text(), nullable=False, default='{}'
    )
    created_at = db.Column(
        db.DateTime(), nullable=False, server_default=db.func.now()
    )
    completed_at = db.Column(db.DateTime(), nullable=True)
    rolled_back_at = db.Column(db.DateTime(), nullable=True)
    items = db.relationship(
        'ProfileMigrationItem', back_populates='run', cascade=CASCADE_STR
    )


class ProfileMigrationItem(db.Model, UserScopedMixin):
    """Idempotency and target-ownership marker for one imported item."""
    __tablename__ = 'cde_profile_migration_item'
    __table_args__ = (
        db.UniqueConstraint(
            'user_id', 'source_profile_id', 'item_kind', 'source_key',
            name='uq_cde_profile_migration_item_source'
        ),
    )
    id = db.Column(db.String(36), primary_key=True)
    run_id = db.Column(
        db.String(36),
        db.ForeignKey('cde_profile_migration_run.id', ondelete='CASCADE'),
        nullable=False
    )
    user_id = db.Column(
        db.Integer, db.ForeignKey(USER_ID), nullable=False
    )
    source_profile_id = db.Column(db.String(64), nullable=False)
    item_kind = db.Column(db.String(64), nullable=False)
    source_key = db.Column(db.String(256), nullable=False)
    item_fingerprint = db.Column(db.String(64), nullable=False)
    target_reference = db.Column(db.String(256), nullable=False)
    created_target = db.Column(db.Boolean(), nullable=False, default=False)
    status = db.Column(db.String(32), nullable=False)
    created_at = db.Column(
        db.DateTime(), nullable=False, server_default=db.func.now()
    )
    rolled_back_at = db.Column(db.DateTime(), nullable=True)
    run = db.relationship('ProfileMigrationRun', back_populates='items')


def _create_legacy_endpoint(connection, source_kind, source):
    """Create additive endpoint records for a new legacy registration."""
    endpoint_id = str(uuid.uuid4())
    endpoint_uuid = uuid.UUID(endpoint_id)
    endpoint_mode = 'legacy_native'

    def child_id(kind):
        return str(uuid.uuid5(endpoint_uuid, kind))

    def namespace_id(purpose):
        return child_id(f'namespace:{endpoint_mode}:{purpose}')

    source_reference = f'{source_kind}:{source.id}'
    registration = getattr(source, '_cde_endpoint_registration', None)
    provider_managed = registration is not None
    route_configuration = getattr(
        source, '_cde_endpoint_route_configuration', {}
    )
    if registration is None:
        registration = {
            'experience_family': 'postgresql',
            'provider_id': 'org.pgadmin.postgresql',
            'provider_version': None,
            'profile_id': 'postgresql-unverified-registration',
            'profile_version': None,
            'target_adapter_id': 'legacy-pgadmin-server',
            'target_adapter_version': None,
        }
        created_from = f'legacy_{source_kind}_create'
    else:
        created_from = f'cde_{source_kind}_create'
    legacy_ids = {
        'legacy_server_id': None,
        'legacy_shared_server_id': None,
    }
    legacy_ids[
        'legacy_server_id'
        if source_kind == 'server' else 'legacy_shared_server_id'
    ] = source.id
    connection.execute(EndpointProfile.__table__.insert().values(
        id=endpoint_id,
        user_id=source.user_id,
        experience_family=registration['experience_family'],
        endpoint_mode=endpoint_mode,
        provider_id=registration['provider_id'],
        provider_version=registration['provider_version'],
        profile_id=registration['profile_id'],
        profile_version=registration['profile_version'],
        profile_generation=child_id('profile-generation:initial'),
        target_adapter_id=registration['target_adapter_id'],
        target_adapter_version=registration['target_adapter_version'],
        pool_namespace=namespace_id('pool'),
        session_namespace=namespace_id('session'),
        cache_namespace=namespace_id('cache'),
        diagnostic_namespace=namespace_id('diagnostic'),
        created_from=created_from,
        **legacy_ids
    ))
    connection.execute(EndpointRuntimeIdentity.__table__.insert().values(
        endpoint_id=endpoint_id,
        declared_runtime_family=registration['experience_family'],
        declared_runtime_version=registration['profile_version'],
        verified_runtime_family=None,
        verified_runtime_version=None,
        verification_state='unverified',
        verified_at=None,
        verification_evidence_reference=None,
    ))
    connection.execute(EndpointRoute.__table__.insert().values(
        id=child_id('route:0'),
        endpoint_id=endpoint_id,
        route_kind=registration.get('route_kind', 'legacy_registration'),
        route_reference=source_reference,
        priority=0,
        configuration=json.dumps(
            route_configuration, sort_keys=True, separators=(',', ':')
        ),
    ))
    primary_secret_kind = (
        'api_token'
        if route_configuration.get('auth_kind') == 'bearer'
        else 'database_password'
    )
    declared_secret_fields = registration.get('secret_fields', [])
    if declared_secret_fields:
        secret_columns = tuple(
            (
                field['secret_kind'], 'password',
                f":{field['secret_kind']}",
            )
            for field in declared_secret_fields
        ) + (('tunnel_password', 'tunnel_password', ''),)
    else:
        secret_columns = (
            (primary_secret_kind, 'password', ''),
            ('tunnel_password', 'tunnel_password', ''),
        )
    if declared_secret_fields or registration.get(
        'supports_secret', registration.get('requires_secret', True)
    ):
        connection.execute(EndpointSecretReference.__table__.insert(), [
            {
                'id': child_id(f'secret-reference:{secret_kind}'),
                'endpoint_id': endpoint_id,
                'secret_kind': secret_kind,
                'storage_kind': 'legacy_protected_column',
                'secret_reference': (
                    f'{source_reference}:{column}{kind_suffix}'
                ),
            }
            for secret_kind, column, kind_suffix in secret_columns
        ])
    connection.execute(EndpointTLSProfile.__table__.insert().values(
        endpoint_id=endpoint_id,
        tls_mode=(
            'per_route'
            if provider_managed else 'legacy_inherited'
        ),
        configuration_reference=(
            f'cde-endpoint-routes:{endpoint_id}'
            if provider_managed
            else f'{source_reference}:connection_params'
        ),
    ))
    connection.execute(EndpointEvidenceSnapshot.__table__.insert().values(
        id=child_id('evidence:registration'),
        endpoint_id=endpoint_id,
        evidence_kind='registration_snapshot',
        evidence_reference='cdeadmin:endpoint-registration:v1',
        snapshot_data=json.dumps({
            'legacy_id': source.id,
            'legacy_kind': source_kind,
            'profile_id': registration['profile_id'],
            'runtime_verification': 'unverified',
        }, sort_keys=True, separators=(',', ':')),
        expires_at=None,
    ))
    connection.execute(EndpointExtensionProfile.__table__.insert().values(
        endpoint_id=endpoint_id,
        schema_reference='cdeadmin.endpoint.extensions.v1',
        profile_data='{}',
        redaction_state='no_legacy_payload_copied',
    ))


@event.listens_for(Server, 'after_insert')
def _create_server_endpoint(mapper, connection, source):
    """Ensure every newly registered Server receives endpoint identity."""
    _create_legacy_endpoint(connection, 'server', source)


@event.listens_for(SharedServer, 'after_insert')
def _create_shared_server_endpoint(mapper, connection, source):
    """Ensure every per-user SharedServer receives endpoint identity."""
    _create_legacy_endpoint(connection, 'sharedserver', source)


class Macros(db.Model):
    """Define a particular macro."""
    __tablename__ = 'macros'
    id = db.Column(db.Integer, primary_key=True)
    alt = db.Column(db.Boolean(), nullable=False)
    control = db.Column(db.Boolean(), nullable=False)
    key = db.Column(db.String(32), nullable=False)
    key_code = db.Column(db.Integer, nullable=False)


class UserMacros(db.Model, UserScopedMixin):
    """Define the macro for a particular user."""
    __tablename__ = 'user_macros'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    mid = db.Column(
        db.Integer, db.ForeignKey('macros.id'), nullable=True
    )
    uid = db.Column(
        db.Integer, db.ForeignKey(USER_ID)
    )
    name = db.Column(db.String(1024), nullable=False)
    sql = db.Column(db.Text(), nullable=False)


class UserMFA(db.Model, UserScopedMixin):
    """Stores the options for the MFA for a particular user."""
    __tablename__ = 'user_mfa'
    user_id = db.Column(db.Integer, db.ForeignKey(USER_ID), primary_key=True)
    mfa_auth = db.Column(db.String(64), primary_key=True)
    options = db.Column(db.Text(), nullable=True)
    user = db.relationship(
        'User',
        backref=db.backref('user', cascade=CASCADE_STR)
    )
