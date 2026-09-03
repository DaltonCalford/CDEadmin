##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""SQLAlchemy target for applying profile plans to CDEadmin-owned state."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .profile import MIGRATION_VERSION, MigrationError, _stable_id


def _utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _json_value(value, default=None):
    if value is None or isinstance(value, (dict, list)):
        return value if value is not None else default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


class OrmMigrationTarget:
    """Apply imports through pgAdmin models and durable migration markers."""

    def __init__(self, model_module):
        self.model = model_module
        self.session = model_module.db.session

    def assert_source_is_distinct(self, source_path):
        bind = self.session.get_bind()
        database = getattr(bind.url, 'database', None)
        if not database or database == ':memory:':
            return
        if Path(database).resolve() == Path(source_path).resolve():
            raise MigrationError(
                'source pgAdmin profile and CDEadmin target must be distinct'
            )

    def begin(self, plan, consent_reference):
        run = self.model.ProfileMigrationRun.query.filter_by(
            id=plan.run_id, user_id=plan.target_user_id
        ).first()
        if run is None:
            run = self.model.ProfileMigrationRun(
                id=plan.run_id,
                user_id=plan.target_user_id,
                source_profile_id=plan.source_profile_id,
                migration_version=MIGRATION_VERSION,
            )
            self.session.add(run)
        run.source_snapshot_sha256 = plan.source_snapshot_sha256
        run.source_schema_version = plan.source_schema_version
        run.selected_categories = json.dumps(
            sorted(plan.selection.categories()), separators=(',', ':')
        )
        run.status = 'applying'
        run.consent_reference = consent_reference
        run.summary = '{}'
        run.incompatibility_report = json.dumps(
            [dict(item) for item in plan.incompatibilities],
            sort_keys=True, separators=(',', ':'),
        )
        run.completed_at = None
        run.rolled_back_at = None
        self.session.flush()

    def _item(self, plan, action):
        return self.model.ProfileMigrationItem.query.filter_by(
            user_id=plan.target_user_id,
            source_profile_id=plan.source_profile_id,
            item_kind=action.kind,
            source_key=action.source_key,
        ).first()

    def apply_action(self, plan, action, secret_transfer=None):
        item = self._item(plan, action)
        if item is not None and item.status == 'applied':
            status = (
                'already-applied'
                if item.item_fingerprint == action.fingerprint
                else 'source-changed-skipped'
            )
            return {
                'status': status,
                'target_reference': item.target_reference,
            }
        handler = getattr(self, f'_apply_{action.kind}', None)
        if handler is None:
            outcome = {
                'status': 'unsupported-kind',
                'target_reference': 'none',
                'created_target': False,
            }
        else:
            outcome = handler(
                plan, action, secret_transfer=secret_transfer
            )
        if item is None:
            item = self.model.ProfileMigrationItem(
                id=_stable_id(
                    'item', plan.target_user_id, plan.source_profile_id,
                    action.kind, action.source_key,
                ),
                user_id=plan.target_user_id,
                source_profile_id=plan.source_profile_id,
                item_kind=action.kind,
                source_key=action.source_key,
            )
            self.session.add(item)
        item.run_id = plan.run_id
        item.item_fingerprint = action.fingerprint
        item.target_reference = outcome['target_reference']
        item.created_target = outcome.get('created_target', False)
        item.status = (
            'applied'
            if outcome['status'] in {'created', 'adopted-existing'}
            else outcome['status']
        )
        item.rolled_back_at = None
        self.session.flush()
        return {
            'status': outcome['status'],
            'target_reference': outcome['target_reference'],
        }

    def _apply_server_group(self, plan, action, secret_transfer=None):
        group = self.model.ServerGroup.query.filter_by(
            user_id=plan.target_user_id,
            name=action.payload['name'],
        ).first()
        created = group is None
        if created:
            group = self.model.ServerGroup(
                user_id=plan.target_user_id, name=action.payload['name']
            )
            self.session.add(group)
            self.session.flush()
        return {
            'status': 'created' if created else 'adopted-existing',
            'target_reference': f'servergroup:{group.id}',
            'created_target': created,
        }

    def _source_group(self, plan, source_key):
        item = self.model.ProfileMigrationItem.query.filter_by(
            user_id=plan.target_user_id,
            source_profile_id=plan.source_profile_id,
            item_kind='server_group',
            source_key=source_key,
            status='applied',
        ).first()
        if item is None or not item.target_reference.startswith(
                'servergroup:'):
            raise MigrationError(
                f'imported server group {source_key!r} is unavailable'
            )
        return int(item.target_reference.split(':', 1)[1])

    def _apply_server(self, plan, action, secret_transfer=None):
        payload = dict(action.payload)
        group_id = self._source_group(
            plan, payload.pop('servergroup_source_key')
        )
        payload.pop('runtime_identity', None)
        disposition = payload.pop('saved_password_disposition')
        locator = payload.pop('secret_locator')
        query = self.model.Server.query.filter_by(
            user_id=plan.target_user_id,
            name=payload.get('name'),
            host=payload.get('host'),
            port=payload.get('port') or 5432,
            maintenance_db=payload.get('maintenance_db'),
            username=payload.get('username'),
            service=payload.get('service'),
        )
        server = query.first()
        created = server is None
        if created:
            columns = set(self.model.Server.__table__.columns.keys())
            values = {
                key: value for key, value in payload.items()
                if key in columns
            }
            values.update({
                'user_id': plan.target_user_id,
                'servergroup_id': group_id,
                'port': payload.get('port') or 5432,
                'username': payload.get('username') or '',
                'save_password': 0,
                'password': None,
                'tunnel_password': None,
                'use_ssh_tunnel': int(bool(
                    payload.get('use_ssh_tunnel', 0)
                )),
                'tunnel_authentication': int(
                    payload.get('tunnel_authentication') or 0
                ),
                'tunnel_prompt_password': int(
                    payload.get('tunnel_prompt_password') or 0
                ),
                'shared': False,
                'kerberos_conn': bool(payload.get('kerberos_conn', False)),
                'cloud_status': int(payload.get('cloud_status') or 0),
                'is_adhoc': 0,
                'connection_params': _json_value(
                    payload.get('connection_params'), {}
                ),
                'tags': _json_value(payload.get('tags')),
            })
            server = self.model.Server(**values)
            self.session.add(server)
            self.session.flush()
            if disposition == 'trusted-reencrypt-required':
                transferred = secret_transfer(dict(locator))
                if not transferred.get('reencrypted'):
                    raise MigrationError(
                        'secret adapter did not attest target re-encryption'
                    )
                database_secret = transferred.get('database_ciphertext')
                tunnel_secret = transferred.get('tunnel_ciphertext')
                if database_secret is not None and not isinstance(
                        database_secret, bytes):
                    raise MigrationError(
                        'target database ciphertext must be bytes'
                    )
                if tunnel_secret is not None and not isinstance(
                        tunnel_secret, bytes):
                    raise MigrationError(
                        'target tunnel ciphertext must be bytes'
                    )
                server.password = database_secret
                server.tunnel_password = tunnel_secret
                server.save_password = int(database_secret is not None)
        return {
            'status': 'created' if created else 'adopted-existing',
            'target_reference': f'server:{server.id}',
            'created_target': created,
        }

    def _apply_preference(self, plan, action, secret_transfer=None):
        payload = action.payload
        preference = self.session.query(self.model.Preferences).join(
            self.model.PreferenceCategory,
            self.model.Preferences.cid == self.model.PreferenceCategory.id,
        ).join(
            self.model.ModulePreference,
            self.model.PreferenceCategory.mid ==
            self.model.ModulePreference.id,
        ).filter(
            self.model.ModulePreference.name == payload['module_name'],
            self.model.PreferenceCategory.name == payload['category_name'],
            self.model.Preferences.name == payload['preference_name'],
        ).first()
        if preference is None:
            return {
                'status': 'incompatible-target-preference',
                'target_reference': 'none',
                'created_target': False,
            }
        existing = self.model.UserPreference.query.filter_by(
            uid=plan.target_user_id, pid=preference.id
        ).first()
        created = existing is None
        if created:
            self.session.add(self.model.UserPreference(
                uid=plan.target_user_id,
                pid=preference.id,
                value=payload['value'],
            ))
        return {
            'status': 'created' if created else 'adopted-existing',
            'target_reference': f'preference:{preference.id}',
            'created_target': created,
        }

    def _apply_workspace_setting(self, plan, action, secret_transfer=None):
        key = action.payload['setting']
        existing = self.model.Setting.query.filter_by(
            user_id=plan.target_user_id, setting=key
        ).first()
        created = existing is None
        if created:
            self.session.add(self.model.Setting(
                user_id=plan.target_user_id,
                setting=key,
                value=action.payload.get('value'),
            ))
        return {
            'status': 'created' if created else 'adopted-existing',
            'target_reference': f'setting:{key}',
            'created_target': created,
        }

    def _target_server_id(self, plan, source_server_id):
        item = self.model.ProfileMigrationItem.query.filter_by(
            user_id=plan.target_user_id,
            source_profile_id=plan.source_profile_id,
            item_kind='server',
            source_key=str(source_server_id),
            status='applied',
        ).first()
        if item is None or not item.target_reference.startswith('server:'):
            return None
        return int(item.target_reference.split(':', 1)[1])

    def _apply_query_history(self, plan, action, secret_transfer=None):
        payload = action.payload
        server_id = self._target_server_id(plan, payload.get('sid'))
        if server_id is None:
            return {
                'status': 'history-server-unavailable',
                'target_reference': 'none',
                'created_target': False,
            }
        maximum = self.session.query(
            self.model.db.func.max(self.model.QueryHistoryModel.srno)
        ).filter_by(
            uid=plan.target_user_id,
            sid=server_id,
            dbname=payload['dbname'],
        ).scalar()
        serial = (maximum or 0) + 1
        record = self.model.QueryHistoryModel(
            srno=serial,
            uid=plan.target_user_id,
            sid=server_id,
            dbname=payload['dbname'],
            query_info=payload['query_info'],
            last_updated_flag=payload.get('last_updated_flag') or 'N',
        )
        self.session.add(record)
        return {
            'status': 'created',
            'target_reference': (
                f'history:{server_id}:{payload["dbname"]}:{serial}'
            ),
            'created_target': True,
        }

    def complete(self, plan, outcomes):
        run = self.model.ProfileMigrationRun.query.filter_by(
            id=plan.run_id, user_id=plan.target_user_id
        ).one()
        summary = plan.summary(dry_run=False)
        summary['outcome_counts'] = {}
        for outcome in outcomes:
            status = outcome['status']
            summary['outcome_counts'][status] = \
                summary['outcome_counts'].get(status, 0) + 1
        run.summary = json.dumps(
            summary, sort_keys=True, separators=(',', ':')
        )
        run.status = 'applied'
        run.completed_at = _utcnow()
        self.session.commit()

    def abort(self, plan):
        self.session.rollback()

    def rollback(self, run_id):
        run = self.model.ProfileMigrationRun.query.filter_by(id=run_id).first()
        if run is None:
            raise MigrationError(f'unknown migration run {run_id}')
        if run.status == 'rolled-back':
            return {'status': 'already-rolled-back', 'removed': 0}
        removed = 0
        items = self.model.ProfileMigrationItem.query.filter_by(
            run_id=run_id, created_target=True, status='applied'
        ).all()
        items.sort(
            key=lambda item: item.item_kind == 'server_group'
        )
        for item in items:
            if self._remove_item(run, item):
                removed += 1
            item.status = 'rolled-back'
            item.rolled_back_at = _utcnow()
        run.status = 'rolled-back'
        run.rolled_back_at = _utcnow()
        self.session.commit()
        return {'status': 'rolled-back', 'removed': removed}

    def _remove_item(self, run, item):
        reference = item.target_reference
        if item.item_kind == 'server' and reference.startswith('server:'):
            target_id = int(reference.split(':', 1)[1])
            target = self.model.Server.query.filter_by(
                id=target_id, user_id=run.user_id
            ).first()
        elif item.item_kind == 'server_group' and reference.startswith(
                'servergroup:'):
            target_id = int(reference.split(':', 1)[1])
            target = self.model.ServerGroup.query.filter_by(
                id=target_id, user_id=run.user_id
            ).first()
            if target is not None and target.servers:
                return False
        elif item.item_kind == 'workspace_setting' and reference.startswith(
                'setting:'):
            target = self.model.Setting.query.filter_by(
                user_id=run.user_id,
                setting=reference.split(':', 1)[1],
            ).first()
        elif item.item_kind == 'preference' and reference.startswith(
                'preference:'):
            target = self.model.UserPreference.query.filter_by(
                uid=run.user_id,
                pid=int(reference.split(':', 1)[1]),
            ).first()
        elif item.item_kind == 'query_history' and reference.startswith(
                'history:'):
            _prefix, server_id, database, serial = reference.split(':', 3)
            target = self.model.QueryHistoryModel.query.filter_by(
                uid=run.user_id, sid=int(server_id), dbname=database,
                srno=int(serial),
            ).first()
        else:
            target = None
        if target is None:
            return False
        self.session.delete(target)
        self.session.flush()
        return True
