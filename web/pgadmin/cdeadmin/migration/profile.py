##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Plan and execute consented, non-mutating pgAdmin profile imports."""

from __future__ import annotations

import base64
import copy
import hashlib
import json
import sqlite3
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from urllib.parse import quote


MIGRATION_VERSION = '1.0.0'
MIGRATION_NAMESPACE = uuid.UUID('afdde9e6-42b3-5b3b-a171-b73215b98589')
SUPPORTED_CATEGORIES = frozenset({
    'server_groups', 'servers', 'preferences', 'workspaces',
    'query_history', 'saved_passwords',
})
WORKSPACE_SETTINGS = frozenset({
    'Browser/Layout',
    'Browser/ObjectExplorerVisible',
    'Debugger/Layout',
    'SQLEditor/Layout',
    'browser_tree_state',
})
SERVER_SAFE_COLUMNS = (
    'id', 'user_id', 'servergroup_id', 'name', 'host', 'port',
    'maintenance_db', 'username', 'role', 'comment', 'bgcolor', 'fgcolor',
    'service', 'use_ssh_tunnel', 'tunnel_host', 'tunnel_port',
    'tunnel_username', 'tunnel_authentication', 'tunnel_identity_file',
    'tunnel_prompt_password', 'tunnel_keep_alive', 'shared',
    'kerberos_conn', 'cloud_status', 'connection_params',
    'prepare_threshold', 'tags', 'is_adhoc', 'post_connection_sql',
    'save_password',
)


class MigrationError(RuntimeError):
    """Raised when a profile cannot be safely planned or imported."""


def _frozen(mapping):
    return MappingProxyType(dict(mapping))


def _canonical_value(value):
    if isinstance(value, bytes):
        return {'$bytes': base64.b64encode(value).decode('ascii')}
    if isinstance(value, dict):
        return {
            key: _canonical_value(item)
            for key, item in sorted(value.items())
        }
    if isinstance(value, (list, tuple)):
        return [_canonical_value(item) for item in value]
    return value


def _fingerprint(value):
    rendered = json.dumps(
        _canonical_value(value), sort_keys=True, separators=(',', ':'),
        ensure_ascii=True,
    )
    return hashlib.sha256(rendered.encode('utf-8')).hexdigest()


def _stable_id(*parts):
    return str(uuid.uuid5(MIGRATION_NAMESPACE, ':'.join(map(str, parts))))


def _file_digest(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class MigrationSelection:
    """User-selected categories; secrets and history require opt-in."""

    server_groups: bool = True
    servers: bool = True
    preferences: bool = True
    workspaces: bool = True
    query_history: bool = False
    saved_passwords: bool = False

    def categories(self):
        categories = {
            name for name in SUPPORTED_CATEGORIES if getattr(self, name)
        }
        if self.servers:
            categories.add('server_groups')
        return frozenset(categories)

    def validate(self):
        if self.saved_passwords and not self.servers:
            raise MigrationError(
                'saved-password migration requires server migration'
            )


@dataclass(frozen=True)
class MigrationConsent:
    """Explicit approval bound to selected categories and a UI receipt."""

    approved: bool
    categories: frozenset[str]
    reference: str

    def validate(self, selection):
        if not self.approved:
            raise MigrationError('explicit user consent is required')
        if not self.reference.strip():
            raise MigrationError('consent reference is required')
        missing = selection.categories() - set(self.categories)
        if missing:
            raise MigrationError(
                f'consent does not cover selected categories: '
                f'{sorted(missing)!r}'
            )


@dataclass(frozen=True)
class MigrationAction:
    kind: str
    source_key: str
    fingerprint: str
    payload: MappingProxyType


@dataclass(frozen=True)
class MigrationPlan:
    run_id: str
    source_profile_id: str
    source_snapshot_sha256: str
    source_schema_version: int | None
    source_user_id: int
    target_user_id: int
    selection: MigrationSelection
    actions: tuple[MigrationAction, ...]
    incompatibilities: tuple[MappingProxyType, ...]

    def summary(self, dry_run=True):
        counts = {}
        for action in self.actions:
            counts[action.kind] = counts.get(action.kind, 0) + 1
        incompatibility_counts = {}
        for item in self.incompatibilities:
            code = item['code']
            incompatibility_counts[code] = \
                incompatibility_counts.get(code, 0) + item.get('count', 1)
        return {
            'migration_version': MIGRATION_VERSION,
            'run_id': self.run_id,
            'source_profile_id': self.source_profile_id,
            'source_snapshot_sha256': self.source_snapshot_sha256,
            'source_schema_version': self.source_schema_version,
            'source_user_id': self.source_user_id,
            'target_user_id': self.target_user_id,
            'dry_run': dry_run,
            'selected_categories': sorted(self.selection.categories()),
            'action_counts': dict(sorted(counts.items())),
            'incompatibility_counts': dict(
                sorted(incompatibility_counts.items())
            ),
            'secret_payloads_in_summary': False,
            'source_path_in_summary': False,
        }


class PgAdminProfileReader:
    """Read a SQLite pgAdmin profile through an immutable read-only URI."""

    def __init__(self, path):
        self.path = Path(path).resolve()
        if not self.path.is_file():
            raise MigrationError(f'pgAdmin profile does not exist: {path}')

    def digest(self):
        return _file_digest(self.path)

    def source_profile_id(self, source_user_id):
        identity = f'{self.path}:{source_user_id}'
        return hashlib.sha256(identity.encode('utf-8')).hexdigest()

    def connect(self):
        encoded = quote(str(self.path), safe='/')
        connection = sqlite3.connect(
            f'file:{encoded}?mode=ro&immutable=1', uri=True
        )
        connection.row_factory = sqlite3.Row
        connection.execute('PRAGMA query_only=ON')
        return connection

    @staticmethod
    def _tables(connection):
        return {
            row['name'] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }

    @staticmethod
    def _columns(connection, table):
        return {
            row['name'] for row in connection.execute(
                f'PRAGMA table_info("{table}")'
            )
        }

    @classmethod
    def _rows(cls, connection, table, columns, where='', parameters=()):
        tables = cls._tables(connection)
        if table not in tables:
            return None
        available = cls._columns(connection, table)
        selected = [column for column in columns if column in available]
        if not selected:
            return []
        quoted = ', '.join(f'"{column}"' for column in selected)
        statement = f'SELECT {quoted} FROM "{table}"'
        if where:
            statement += f' WHERE {where}'
        return [
            dict(row) for row in connection.execute(statement, parameters)
        ]

    def snapshot(self, source_user_id, selection):
        before = self.digest()
        incompatibilities = []
        connection = self.connect()
        try:
            users = self._rows(
                connection, 'user', ('id', 'username', 'auth_source'),
                'id = ?', (source_user_id,),
            )
            if not users:
                raise MigrationError(
                    f'source user {source_user_id} does not exist'
                )
            schema_version = None
            versions = self._rows(
                connection, 'version', ('name', 'value'),
                'name = ?', ('ConfigDB',),
            )
            if versions:
                schema_version = versions[0].get('value')
            else:
                incompatibilities.append(_frozen({
                    'code': 'source-schema-version-unavailable',
                    'count': 1,
                }))

            groups = []
            if selection.server_groups or selection.servers:
                groups = self._rows(
                    connection, 'servergroup', ('id', 'user_id', 'name'),
                    'user_id = ?', (source_user_id,),
                )
                if groups is None:
                    groups = []
                    incompatibilities.append(_frozen({
                        'code': 'server-groups-table-unavailable',
                        'count': 1,
                    }))

            servers = []
            if selection.servers:
                servers = self._rows(
                    connection, 'server', SERVER_SAFE_COLUMNS,
                    'user_id = ?', (source_user_id,),
                )
                if servers is None:
                    servers = []
                    incompatibilities.append(_frozen({
                        'code': 'servers-table-unavailable',
                        'count': 1,
                    }))

            preferences = []
            if selection.preferences:
                preferences = self._preference_rows(
                    connection, source_user_id, incompatibilities
                )

            settings = []
            if selection.workspaces:
                settings = self._rows(
                    connection, 'setting', ('setting', 'value'),
                    'user_id = ?', (source_user_id,),
                )
                if settings is None:
                    settings = []
                    incompatibilities.append(_frozen({
                        'code': 'workspace-settings-table-unavailable',
                        'count': 1,
                    }))
                settings, ignored = self._workspace_settings(settings)
                if ignored:
                    incompatibilities.append(_frozen({
                        'code': 'unsupported-setting',
                        'count': ignored,
                    }))
                app_state_count = self._count_rows(
                    connection, 'application_state', 'uid = ?',
                    (source_user_id,),
                )
                if app_state_count:
                    incompatibilities.append(_frozen({
                        'code': 'encrypted-workspace-state-requires-rekey',
                        'count': app_state_count,
                    }))

            history = []
            if selection.query_history:
                history = self._rows(
                    connection, 'query_history',
                    ('srno', 'uid', 'sid', 'dbname', 'query_info',
                     'last_updated_flag'),
                    'uid = ?', (source_user_id,),
                )
                if history is None:
                    history = []
                    incompatibilities.append(_frozen({
                        'code': 'query-history-table-unavailable',
                        'count': 1,
                    }))
        finally:
            connection.close()

        after = self.digest()
        if before != after:
            raise MigrationError('source profile changed while being read')
        return {
            'source_snapshot_sha256': before,
            'source_schema_version': schema_version,
            'groups': groups,
            'servers': servers,
            'preferences': preferences,
            'settings': settings,
            'history': history,
            'incompatibilities': incompatibilities,
        }

    @classmethod
    def _count_rows(cls, connection, table, where='', parameters=()):
        if table not in cls._tables(connection):
            return 0
        statement = f'SELECT COUNT(*) FROM "{table}"'
        if where:
            statement += f' WHERE {where}'
        return connection.execute(statement, parameters).fetchone()[0]

    @classmethod
    def _preference_rows(cls, connection, user_id, incompatibilities):
        required = {
            'user_preferences', 'preferences', 'preference_category',
            'module_preference',
        }
        if not required.issubset(cls._tables(connection)):
            incompatibilities.append(_frozen({
                'code': 'preferences-catalog-unavailable',
                'count': 1,
            }))
            return []
        statement = '''
            SELECT m.name AS module_name, c.name AS category_name,
                   p.name AS preference_name, u.value AS value
              FROM user_preferences AS u
              JOIN preferences AS p ON p.id = u.pid
              JOIN preference_category AS c ON c.id = p.cid
              JOIN module_preference AS m ON m.id = c.mid
             WHERE u.uid = ?
             ORDER BY m.name, c.name, p.name
        '''
        return [
            dict(row) for row in connection.execute(statement, (user_id,))
        ]

    @staticmethod
    def _workspace_settings(settings):
        compatible = []
        ignored = 0
        for record in settings:
            key = record.get('setting', '')
            if key in WORKSPACE_SETTINGS or key.startswith('Workspace/Layout'):
                compatible.append(record)
            else:
                ignored += 1
        return compatible, ignored


class ProfileMigrationService:
    """Create redacted plans and apply them through a transactional target."""

    def plan(self, reader, source_user_id, target_user_id,
             selection=None):
        selection = selection or MigrationSelection()
        selection.validate()
        snapshot = reader.snapshot(source_user_id, selection)
        source_profile_id = reader.source_profile_id(source_user_id)
        run_id = _stable_id(
            'run', source_profile_id, target_user_id, MIGRATION_VERSION
        )
        actions = []
        incompatibilities = list(snapshot['incompatibilities'])
        group_ids = {record['id'] for record in snapshot['groups']}

        if selection.server_groups or selection.servers:
            for record in snapshot['groups']:
                payload = {'name': record.get('name')}
                actions.append(self._action(
                    'server_group', str(record['id']), payload
                ))

        if selection.servers:
            for record in snapshot['servers']:
                if record.get('is_adhoc'):
                    incompatibilities.append(_frozen({
                        'code': 'adhoc-server-not-imported', 'count': 1,
                    }))
                    continue
                group_id = record.get('servergroup_id')
                if group_id not in group_ids:
                    incompatibilities.append(_frozen({
                        'code': 'server-group-reference-missing', 'count': 1,
                    }))
                    continue
                payload = {
                    key: value for key, value in record.items()
                    if key not in {'id', 'user_id'}
                }
                payload['servergroup_source_key'] = str(group_id)
                payload['runtime_identity'] = {
                    'declared_runtime_family': 'postgresql',
                    'declared_runtime_version': None,
                    'verified_runtime_family': None,
                    'verified_runtime_version': None,
                    'verification_state': 'unverified',
                    'verification_evidence_reference': None,
                }
                payload['saved_password_disposition'] = (
                    'trusted-reencrypt-required'
                    if selection.saved_passwords and
                    record.get('save_password')
                    else 'reauthentication-required'
                    if record.get('save_password')
                    else 'not-present-or-not-requested'
                )
                payload['secret_locator'] = {
                    'source_profile_id': source_profile_id,
                    'source_server_id': record['id'],
                }
                payload.pop('save_password', None)
                actions.append(self._action(
                    'server', str(record['id']), payload
                ))
                if record.get('save_password') and \
                        not selection.saved_passwords:
                    incompatibilities.append(_frozen({
                        'code': 'saved-password-requires-reauthentication',
                        'count': 1,
                    }))
                if record.get('shared'):
                    incompatibilities.append(_frozen({
                        'code': 'shared-registration-imported-private',
                        'count': 1,
                    }))

        for record in snapshot['preferences']:
            key = '/'.join((
                record['module_name'], record['category_name'],
                record['preference_name'],
            ))
            actions.append(self._action('preference', key, record))

        for record in snapshot['settings']:
            actions.append(self._action(
                'workspace_setting', record['setting'], record
            ))

        for record in snapshot['history']:
            key = ':'.join(map(str, (
                record.get('sid'), record.get('dbname'), record.get('srno'),
            )))
            actions.append(self._action('query_history', key, record))

        return MigrationPlan(
            run_id=run_id,
            source_profile_id=source_profile_id,
            source_snapshot_sha256=snapshot['source_snapshot_sha256'],
            source_schema_version=snapshot['source_schema_version'],
            source_user_id=source_user_id,
            target_user_id=target_user_id,
            selection=selection,
            actions=tuple(actions),
            incompatibilities=tuple(incompatibilities),
        )

    @staticmethod
    def _action(kind, source_key, payload):
        return MigrationAction(
            kind=kind,
            source_key=source_key,
            fingerprint=_fingerprint(payload),
            payload=_frozen(payload),
        )

    def dry_run(self, reader, source_user_id, target_user_id,
                selection=None):
        return self.plan(
            reader, source_user_id, target_user_id, selection
        ).summary(dry_run=True)

    def apply(self, reader, source_user_id, target_user_id, target, consent,
              selection=None, secret_transfer=None):
        selection = selection or MigrationSelection()
        consent.validate(selection)
        if selection.saved_passwords and secret_transfer is None:
            raise MigrationError(
                'saved-password import requires a trusted re-encryption '
                'adapter'
            )
        plan = self.plan(
            reader, source_user_id, target_user_id, selection
        )
        before = plan.source_snapshot_sha256
        if hasattr(target, 'assert_source_is_distinct'):
            target.assert_source_is_distinct(reader.path)
        target.begin(plan, consent.reference)
        outcomes = []
        try:
            for action in plan.actions:
                outcomes.append(target.apply_action(
                    plan, action, secret_transfer=secret_transfer
                ))
            target.complete(plan, outcomes)
        except Exception:
            target.abort(plan)
            raise
        if reader.digest() != before:
            target.rollback(plan.run_id)
            raise MigrationError('source profile changed during import')
        summary = plan.summary(dry_run=False)
        summary['outcome_counts'] = self._outcome_counts(outcomes)
        summary['consent_reference'] = consent.reference
        summary['source_unchanged'] = True
        return summary

    @staticmethod
    def _outcome_counts(outcomes):
        counts = {}
        for outcome in outcomes:
            status = outcome['status']
            counts[status] = counts.get(status, 0) + 1
        return dict(sorted(counts.items()))


@dataclass
class InMemoryMigrationTarget:
    """Transactional reference target used by providers and qualification."""

    objects: dict = field(default_factory=dict)
    items: dict = field(default_factory=dict)
    runs: dict = field(default_factory=dict)
    _snapshot: tuple | None = None

    def begin(self, plan, consent_reference):
        self._snapshot = (
            copy.deepcopy(self.objects), copy.deepcopy(self.items),
            copy.deepcopy(self.runs),
        )
        created = copy.deepcopy(
            self.runs.get(plan.run_id, {}).get('created', [])
        )
        self.runs[plan.run_id] = {
            'status': 'applying',
            'consent_reference': consent_reference,
            'source_profile_id': plan.source_profile_id,
            'created': created,
        }

    def apply_action(self, plan, action, secret_transfer=None):
        ledger_key = (
            plan.target_user_id, plan.source_profile_id,
            action.kind, action.source_key,
        )
        existing = self.items.get(ledger_key)
        if existing and existing['status'] == 'applied':
            status = (
                'already-applied'
                if existing['fingerprint'] == action.fingerprint
                else 'source-changed-skipped'
            )
            return {'status': status, 'target_reference': existing['target']}
        target_id = _stable_id('target', *ledger_key)
        object_key = (action.kind, target_id)
        stored = {
            'target_user_id': plan.target_user_id,
            'payload': copy.deepcopy(dict(action.payload)),
        }
        stored['payload'].pop('secret_locator', None)
        if action.kind == 'server':
            stored['payload']['runtime_identity'] = copy.deepcopy(
                action.payload['runtime_identity']
            )
            stored['payload']['save_password'] = 0
            stored['payload']['shared'] = False
            stored['payload']['password'] = None
            stored['payload']['tunnel_password'] = None
            if action.payload['saved_password_disposition'] == \
                    'trusted-reencrypt-required':
                transferred = secret_transfer(dict(
                    action.payload['secret_locator']
                ))
                if not transferred.get('reencrypted'):
                    raise MigrationError(
                        'secret adapter did not attest target re-encryption'
                    )
                stored['payload']['secret_reference'] = transferred.get(
                    'secret_reference'
                )
                stored['payload']['save_password'] = int(
                    bool(transferred.get('database_ciphertext'))
                )
        self.objects[object_key] = stored
        self.items[ledger_key] = {
            'status': 'applied',
            'fingerprint': action.fingerprint,
            'target': target_id,
            'run_id': plan.run_id,
            'created_target': True,
        }
        self.runs[plan.run_id]['created'].append((object_key, ledger_key))
        return {'status': 'created', 'target_reference': target_id}

    def complete(self, plan, outcomes):
        self.runs[plan.run_id]['status'] = 'applied'
        self.runs[plan.run_id]['outcomes'] = copy.deepcopy(outcomes)
        self._snapshot = None

    def abort(self, plan):
        if self._snapshot is not None:
            self.objects, self.items, self.runs = self._snapshot
        self._snapshot = None

    def rollback(self, run_id):
        run = self.runs.get(run_id)
        if not run:
            raise MigrationError(f'unknown migration run {run_id}')
        if run['status'] == 'rolled-back':
            return {'status': 'already-rolled-back', 'removed': 0}
        removed = 0
        for object_key, ledger_key in reversed(run.get('created', ())):
            if self.objects.pop(object_key, None) is not None:
                removed += 1
            item = self.items.get(ledger_key)
            if item and item.get('run_id') == run_id:
                item['status'] = 'rolled-back'
        run['status'] = 'rolled-back'
        return {'status': 'rolled-back', 'removed': removed}
