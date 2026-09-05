##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Authoritative persistence and transfer protocol for workspace tools.

This service moves only secret-free reconstruction descriptors and opaque
checkpoint references. Database credentials, query text, document bodies,
and provider transaction authority never enter workspace persistence.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import uuid
from datetime import datetime, timedelta, timezone


APP_EXTENSION_KEY = 'cdeadmin_workspace_transfer_service'
DESCRIPTOR_SCHEMA = 'cdeadmin.tool-instance.v1'
PLACEMENT_MODES = frozenset({'docked', 'floating', 'detached'})
WINDOW_ROLES = frozenset({
    'main', 'detached-tool', 'secondary-workspace',
})
ACTIVE_MOVE_STATES = frozenset({'prepared', 'acknowledged'})
TERMINAL_MOVE_STATES = frozenset({'committed', 'aborted', 'expired'})
SAFE_ID = re.compile(r'^[a-zA-Z0-9][a-zA-Z0-9._:-]{0,255}$')
FORBIDDEN_KEY = re.compile(
    r'(?:password|passwd|secret|credential|private.?key|access.?token|'
    r'refresh.?token|csrf|query|sql|document.?content|form.?params|'
    r'tool.?url)', re.IGNORECASE
)
ALLOWED_VIEW_STATE = frozenset({
    'activePanel', 'scrollTop', 'scrollLeft', 'selectionRef',
    'expandedRefs', 'focusRef', 'zoom', 'viewport',
})
DESCRIPTOR_FIELDS = frozenset({
    'schema', 'schemaVersion', 'toolInstanceId', 'toolKind', 'restoreRef',
    'projectId', 'context', 'presentation', 'placement', 'state',
    'capabilities',
})
CONTEXT_FIELDS = frozenset({
    'providerId', 'endpointId', 'routeId', 'instanceId',
    'databaseTargetId', 'sessionHandle', 'resultHandle', 'operationHandle',
})
PRESENTATION_FIELDS = frozenset({'title', 'iconKey'})
PLACEMENT_FIELDS = frozenset({
    'mode', 'workspaceId', 'windowId', 'dockArea', 'tabOrder', 'revision',
})
STATE_FIELDS = frozenset({
    'dirty', 'transactionState', 'connectionState', 'sharedSession',
})
CAPABILITY_FIELDS = frozenset({
    'detachable', 'duplicable', 'requiresLiveSession',
})


class WorkspaceTransferError(RuntimeError):
    """A workspace transfer request cannot be admitted."""

    status_code = 400


class WorkspaceTransferNotFound(WorkspaceTransferError):
    """An owner-scoped workspace resource does not exist."""

    status_code = 404


class WorkspaceTransferConflict(WorkspaceTransferError):
    """The requested change conflicts with authoritative state."""

    status_code = 409


class WorkspaceTransferExpired(WorkspaceTransferError):
    """A move proof has expired."""

    status_code = 410


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _json(value, label, max_bytes=32768):
    try:
        encoded = json.dumps(
            value, sort_keys=True, separators=(',', ':'), ensure_ascii=True
        )
    except (TypeError, ValueError) as exc:
        raise WorkspaceTransferError(f'{label} must be JSON serializable') \
            from exc
    if len(encoded.encode('utf-8')) > max_bytes:
        raise WorkspaceTransferError(f'{label} exceeds its size limit')
    return encoded


def _object(value, label):
    if not isinstance(value, dict):
        raise WorkspaceTransferError(f'{label} must be an object')
    return value


def _safe_id(value, label, *, required=True):
    normalized = '' if value is None else str(value).strip()
    if not normalized and not required:
        return ''
    if not SAFE_ID.fullmatch(normalized):
        raise WorkspaceTransferError(
            f'{label} must be a stable opaque identifier'
        )
    return normalized


def _integer(value, label):
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise WorkspaceTransferError(
            f'{label} must be a non-negative integer'
        )
    return value


def _assert_secret_free(value, path='descriptor'):
    if isinstance(value, list):
        for index, child in enumerate(value):
            _assert_secret_free(child, f'{path}[{index}]')
    elif isinstance(value, dict):
        for key, child in value.items():
            if FORBIDDEN_KEY.search(str(key)):
                raise WorkspaceTransferError(
                    f'sensitive field is forbidden: {path}.{key}'
                )
            _assert_secret_free(child, f'{path}.{key}')


def _only_fields(value, allowed, label):
    unsupported = set(value).difference(allowed)
    if unsupported:
        raise WorkspaceTransferError(
            f'{label} contains unsupported fields: ' +
            ', '.join(sorted(str(item) for item in unsupported))
        )


def validate_descriptor(value, workspace_key=None, tool_key=None):
    """Validate the browser descriptor at the persistence boundary."""
    descriptor = _object(value, 'tool descriptor')
    _assert_secret_free(descriptor)
    _only_fields(descriptor, DESCRIPTOR_FIELDS, 'tool descriptor')
    if descriptor.get('schema') != DESCRIPTOR_SCHEMA:
        raise WorkspaceTransferError('tool descriptor schema is unsupported')
    if descriptor.get('schemaVersion') != 1:
        raise WorkspaceTransferError(
            'tool descriptor schema version is unsupported'
        )
    actual_tool = _safe_id(
        descriptor.get('toolInstanceId'), 'tool instance ID'
    )
    if tool_key is not None and actual_tool != tool_key:
        raise WorkspaceTransferError(
            'tool descriptor does not match the requested tool'
        )
    _safe_id(descriptor.get('toolKind'), 'tool kind')
    _safe_id(descriptor.get('restoreRef'), 'restore reference')
    context = _object(descriptor.get('context'), 'tool context')
    presentation = _object(
        descriptor.get('presentation'), 'tool presentation'
    )
    placement = _object(descriptor.get('placement'), 'tool placement')
    state = _object(descriptor.get('state'), 'tool state')
    capabilities = _object(
        descriptor.get('capabilities'), 'tool capabilities'
    )
    _only_fields(context, CONTEXT_FIELDS, 'tool context')
    _only_fields(presentation, PRESENTATION_FIELDS, 'tool presentation')
    _only_fields(placement, PLACEMENT_FIELDS, 'tool placement')
    _only_fields(state, STATE_FIELDS, 'tool state')
    _only_fields(capabilities, CAPABILITY_FIELDS, 'tool capabilities')
    if len(str(presentation.get('title', ''))) > 512:
        raise WorkspaceTransferError('tool presentation title is too long')
    if len(str(presentation.get('iconKey', ''))) > 128:
        raise WorkspaceTransferError('tool presentation icon is too long')
    actual_workspace = _safe_id(
        placement.get('workspaceId'), 'workspace ID'
    )
    if workspace_key is not None and actual_workspace != workspace_key:
        raise WorkspaceTransferError(
            'tool descriptor does not match the requested workspace'
        )
    _validate_placement(placement, actual_workspace)
    _json(descriptor, 'tool descriptor')
    return descriptor


def _validate_placement(value, workspace_key):
    placement = _object(value, 'tool placement')
    _only_fields(placement, PLACEMENT_FIELDS, 'tool placement')
    mode = str(placement.get('mode', '')).strip()
    if mode not in PLACEMENT_MODES:
        raise WorkspaceTransferError('tool placement mode is invalid')
    if _safe_id(placement.get('workspaceId'), 'workspace ID') != (
        workspace_key
    ):
        raise WorkspaceTransferError('tool placement workspace is invalid')
    return {
        'mode': mode,
        'workspaceId': workspace_key,
        'windowId': _safe_id(placement.get('windowId'), 'window ID'),
        'dockArea': _safe_id(placement.get('dockArea'), 'dock area'),
        'tabOrder': _integer(placement.get('tabOrder'), 'tab order'),
        'revision': _integer(
            placement.get('revision'), 'placement revision'
        ),
    }


def _view_state(value):
    state = _object(value or {}, 'view state')
    unsupported = set(state).difference(ALLOWED_VIEW_STATE)
    if unsupported:
        raise WorkspaceTransferError(
            'view state contains unsupported fields: ' +
            ', '.join(sorted(unsupported))
        )
    _assert_secret_free(state, 'view state')
    return _json(state, 'view state', max_bytes=16384)


class WorkspaceTransferRepository:
    """SQLAlchemy repository with explicit owner scoping."""

    def __init__(self):
        from pgadmin.model import (
            CDEToolCheckpoint,
            CDEToolInstance,
            CDEWorkspace,
            CDEWorkspaceMoveToken,
            CDEWorkspaceWindow,
            db,
        )
        self.db = db
        self.workspace_model = CDEWorkspace
        self.window_model = CDEWorkspaceWindow
        self.tool_model = CDEToolInstance
        self.checkpoint_model = CDEToolCheckpoint
        self.move_model = CDEWorkspaceMoveToken

    def workspace(self, user_id, workspace_key):
        return self.workspace_model.query.filter_by(
            user_id=user_id, workspace_key=workspace_key
        ).first()

    def workspace_by_name(self, user_id, name):
        return self.workspace_model.query.filter_by(
            user_id=user_id, name=name
        ).first()

    def window(self, user_id, workspace_id, window_key):
        return self.window_model.query.filter_by(
            user_id=user_id, workspace_id=workspace_id,
            window_key=window_key
        ).first()

    def tool(self, user_id, workspace_id, tool_key):
        return self.tool_model.query.filter_by(
            user_id=user_id, workspace_id=workspace_id, tool_key=tool_key
        ).first()

    def move(self, user_id, move_id):
        return self.move_model.query.filter_by(
            user_id=user_id, id=move_id
        ).first()

    def workspace_by_id(self, user_id, workspace_id):
        return self.workspace_model.query.filter_by(
            user_id=user_id, id=workspace_id
        ).first()

    def tool_by_id(self, user_id, tool_id):
        return self.tool_model.query.filter_by(
            user_id=user_id, id=tool_id
        ).first()

    def move_for_idempotency(self, user_id, idempotency_key):
        return self.move_model.query.filter_by(
            user_id=user_id, idempotency_key=idempotency_key
        ).first()

    def active_move(self, user_id, tool_id):
        return self.move_model.query.filter(
            self.move_model.user_id == user_id,
            self.move_model.tool_instance_id == tool_id,
            self.move_model.status.in_(ACTIVE_MOVE_STATES),
        ).order_by(self.move_model.created_at.desc()).first()

    def move_tool(self, user_id, tool_id, expected_revision, values):
        return self.tool_model.query.filter_by(
            user_id=user_id, id=tool_id,
            placement_revision=expected_revision
        ).update(values, synchronize_session=False)

    def add(self, row):
        self.db.session.add(row)

    def flush(self):
        self.db.session.flush()

    def commit(self):
        self.db.session.commit()

    def rollback(self):
        self.db.session.rollback()


class WorkspaceTransferService:
    """Persist and atomically transfer reconstructable tool instances."""

    def __init__(self, repository, signing_key, clock=None, token_ttl=120):
        if not signing_key:
            raise WorkspaceTransferError(
                'workspace transfer signing authority is unavailable'
            )
        self.repository = repository
        self.signing_key = (
            signing_key.encode('utf-8')
            if isinstance(signing_key, str) else bytes(signing_key)
        )
        self.clock = clock or _now
        self.token_ttl = int(token_ttl)
        if self.token_ttl < 15 or self.token_ttl > 600:
            raise WorkspaceTransferError(
                'workspace move token lifetime must be 15 to 600 seconds'
            )

    def ensure_workspace(self, user_id, workspace_key, request):
        workspace_key = _safe_id(workspace_key, 'workspace ID')
        request = _object(request or {}, 'workspace request')
        name = str(request.get('name') or workspace_key).strip()
        if not name or len(name) > 128:
            raise WorkspaceTransferError('workspace name is invalid')
        has_layout = 'layout_reference' in request
        layout_reference = None
        if has_layout:
            layout_reference = _safe_id(
                request.get('layout_reference'), 'layout reference',
                required=False
            ) or None
        row = self.repository.workspace(user_id, workspace_key)
        if row is None:
            named = self.repository.workspace_by_name(user_id, name)
            if named is not None:
                raise WorkspaceTransferConflict(
                    'workspace name is already in use'
                )
            row = self.repository.workspace_model(
                id=str(uuid.uuid4()), user_id=user_id,
                workspace_key=workspace_key, name=name,
                layout_reference=layout_reference, revision=0,
            )
            self.repository.add(row)
        else:
            changed = row.name != name or (
                has_layout and row.layout_reference != layout_reference
            )
            if changed:
                expected = _integer(
                    request.get('expected_revision'),
                    'expected workspace revision'
                )
                if expected != row.revision:
                    raise WorkspaceTransferConflict(
                        'workspace revision has changed'
                    )
                named = self.repository.workspace_by_name(user_id, name)
                if named is not None and named.id != row.id:
                    raise WorkspaceTransferConflict(
                        'workspace name is already in use'
                    )
                row.name = name
                if has_layout:
                    row.layout_reference = layout_reference
                row.revision += 1
        self.repository.commit()
        return self._workspace(row, include_children=False)

    def register_window(self, user_id, workspace_key, window_key, request):
        workspace = self._workspace_owned(user_id, workspace_key)
        window_key = _safe_id(window_key, 'window ID')
        request = _object(request or {}, 'window request')
        role = str(request.get('role') or 'main')
        if role not in WINDOW_ROLES:
            raise WorkspaceTransferError('workspace window role is invalid')
        expected = request.get('expected_revision')
        window_placement = _object(
            request.get('placement') or {}, 'window placement'
        )
        _assert_secret_free(window_placement, 'window placement')
        placement = _json(
            window_placement, 'window placement', max_bytes=8192
        )
        row = self.repository.window(user_id, workspace.id, window_key)
        if row is None:
            if expected not in {None, 0}:
                raise WorkspaceTransferConflict(
                    'workspace window revision has changed'
                )
            row = self.repository.window_model(
                id=str(uuid.uuid4()), workspace_id=workspace.id,
                user_id=user_id, window_key=window_key, role=role,
                device_profile_id=_safe_id(
                    request.get('device_profile_id'), 'device profile ID',
                    required=False
                ) or None,
                display_fingerprint=str(
                    request.get('display_fingerprint') or ''
                )[:256] or None,
                placement=placement, revision=0, clean_close=False,
                last_seen_at=self.clock(),
            )
            self.repository.add(row)
        else:
            if expected is not None and _integer(
                expected, 'expected window revision'
            ) != row.revision:
                raise WorkspaceTransferConflict(
                    'workspace window revision has changed'
                )
            row.role = role
            row.device_profile_id = _safe_id(
                request.get('device_profile_id'), 'device profile ID',
                required=False
            ) or None
            row.display_fingerprint = str(
                request.get('display_fingerprint') or ''
            )[:256] or None
            row.placement = placement
            row.clean_close = bool(request.get('clean_close', False))
            row.last_seen_at = self.clock()
            row.revision += 1
        workspace.revision += 1
        self.repository.commit()
        return self._window(row)

    def register_tool(self, user_id, workspace_key, tool_key, request):
        workspace = self._workspace_owned(user_id, workspace_key)
        tool_key = _safe_id(tool_key, 'tool instance ID')
        request = _object(request or {}, 'tool request')
        descriptor = validate_descriptor(
            request.get('descriptor'), workspace_key, tool_key
        )
        placement = _validate_placement(
            descriptor['placement'], workspace_key
        )
        window = self._window_owned(
            user_id, workspace.id, placement['windowId']
        )
        row = self.repository.tool(user_id, workspace.id, tool_key)
        if row is None:
            if placement['revision'] != 0:
                raise WorkspaceTransferConflict(
                    'a new tool must start at placement revision zero'
                )
            row = self.repository.tool_model(
                id=str(uuid.uuid4()), workspace_id=workspace.id,
                user_id=user_id, tool_key=tool_key,
                tool_kind=descriptor['toolKind'],
                descriptor_schema=descriptor['schema'],
                descriptor=_json(descriptor, 'tool descriptor'),
                restore_reference=descriptor['restoreRef'],
                window_id=window.id, dock_area=placement['dockArea'],
                tab_order=placement['tabOrder'],
                placement_mode=placement['mode'], placement_revision=0,
                checkpoint_revision=0,
                dirty=bool(descriptor.get('state', {}).get('dirty')),
                transaction_state=str(
                    descriptor.get('state', {}).get(
                        'transactionState', 'unknown'
                    )
                )[:32],
                connection_state=str(
                    descriptor.get('state', {}).get(
                        'connectionState', 'unknown'
                    )
                )[:32],
            )
            self.repository.add(row)
        else:
            self._assert_tool_placement(row, window, placement)
            row.descriptor = _json(descriptor, 'tool descriptor')
            row.dirty = bool(descriptor.get('state', {}).get('dirty'))
            row.transaction_state = str(
                descriptor.get('state', {}).get(
                    'transactionState', 'unknown'
                )
            )[:32]
            row.connection_state = str(
                descriptor.get('state', {}).get(
                    'connectionState', 'unknown'
                )
            )[:32]
        workspace.revision += 1
        self.repository.commit()
        return self._tool(row, {window.id: window.window_key})

    def checkpoint(self, user_id, workspace_key, tool_key, request):
        workspace = self._workspace_owned(user_id, workspace_key)
        tool = self._tool_owned(user_id, workspace.id, tool_key)
        result = self._create_checkpoint(user_id, tool, request)
        workspace.revision += 1
        self.repository.commit()
        return result

    def prepare(self, user_id, workspace_key, tool_key, request):
        workspace = self._workspace_owned(user_id, workspace_key)
        tool = self._tool_owned(user_id, workspace.id, tool_key)
        request = _object(request or {}, 'move request')
        expected = _integer(
            request.get('expected_revision'), 'expected placement revision'
        )
        if tool.placement_revision != expected:
            raise WorkspaceTransferConflict('tool placement has changed')
        idempotency_key = _safe_id(
            request.get('idempotency_key'), 'idempotency key'
        )
        existing = self.repository.move_for_idempotency(
            user_id, idempotency_key
        )
        if existing is not None:
            if existing.tool_instance_id != tool.id:
                raise WorkspaceTransferConflict(
                    'idempotency key belongs to another tool'
                )
            self._expire(existing)
            self.repository.commit()
            return self._move(existing, include_proof=True)
        active = self.repository.active_move(user_id, tool.id)
        if active is not None:
            self._expire(active)
            if active.status in ACTIVE_MOVE_STATES:
                raise WorkspaceTransferConflict(
                    'tool already has an active move'
                )
        destination = _validate_placement(
            request.get('destination'), workspace_key
        )
        if destination['revision'] != expected:
            raise WorkspaceTransferConflict(
                'destination placement revision must match the source'
            )
        checkpoint = self._create_checkpoint(
            user_id, tool, request.get('checkpoint') or {}
        )
        move_id = str(uuid.uuid4())
        expires_at = self.clock() + timedelta(seconds=self.token_ttl)
        proof = self._proof(move_id, user_id, expires_at)
        token = f'{move_id}.{proof}'
        row = self.repository.move_model(
            id=move_id,
            token_digest=hashlib.sha256(token.encode('ascii')).hexdigest(),
            workspace_id=workspace.id, tool_instance_id=tool.id,
            user_id=user_id, source_window_id=tool.window_id,
            source_revision=expected,
            checkpoint_revision=checkpoint['revision'],
            destination_placement=_json(
                destination, 'destination placement'
            ),
            idempotency_key=idempotency_key, status='prepared',
            expires_at=expires_at,
        )
        self.repository.add(row)
        workspace.revision += 1
        self.repository.commit()
        return self._move(row, include_proof=True)

    def acknowledge(self, user_id, token, request):
        row = self._move_from_token(user_id, token)
        self._require_active(row)
        request = _object(request or {}, 'move acknowledgement')
        tool_key = _safe_id(
            request.get('restored_tool_instance_id'), 'restored tool ID'
        )
        tool = self.repository.tool_by_id(user_id, row.tool_instance_id)
        if tool is None or tool.tool_key != tool_key:
            raise WorkspaceTransferConflict(
                'restored tool does not match the move authorization'
            )
        checkpoint_revision = _integer(
            request.get('checkpoint_revision'), 'checkpoint revision'
        )
        if checkpoint_revision != row.checkpoint_revision:
            raise WorkspaceTransferConflict(
                'restored checkpoint does not match the move authorization'
            )
        destination = json.loads(row.destination_placement)
        window = self._window_owned(
            user_id, row.workspace_id, destination['windowId']
        )
        if row.status == 'acknowledged':
            if row.destination_window_id != window.id:
                raise WorkspaceTransferConflict(
                    'move was acknowledged by another window'
                )
            return self._move(row)
        if row.status != 'prepared':
            raise WorkspaceTransferConflict(
                f'move cannot be acknowledged from {row.status}'
            )
        row.destination_window_id = window.id
        row.status = 'acknowledged'
        row.acknowledged_at = self.clock()
        self.repository.commit()
        return self._move(row)

    def commit(self, user_id, token):
        row = self._move_from_token(user_id, token)
        if row.status == 'committed':
            return self._move(row)
        self._require_active(row)
        if row.status != 'acknowledged' or not row.destination_window_id:
            raise WorkspaceTransferConflict(
                'destination restoration must be acknowledged before commit'
            )
        tool = self.repository.tool_by_id(user_id, row.tool_instance_id)
        if tool is None:
            raise WorkspaceTransferNotFound('tool instance was not found')
        descriptor = json.loads(tool.descriptor)
        destination = json.loads(row.destination_placement)
        destination['revision'] = row.source_revision + 1
        descriptor['placement'] = destination
        changed = self.repository.move_tool(
            user_id, tool.id, row.source_revision, {
                'window_id': row.destination_window_id,
                'dock_area': destination['dockArea'],
                'tab_order': destination['tabOrder'],
                'placement_mode': destination['mode'],
                'placement_revision': row.source_revision + 1,
                'descriptor': _json(descriptor, 'tool descriptor'),
                'updated_at': self.clock(),
            }
        )
        if changed != 1:
            raise WorkspaceTransferConflict(
                'tool placement changed before move commit'
            )
        row.status = 'committed'
        row.committed_at = self.clock()
        workspace = self.repository.workspace_by_id(
            user_id, row.workspace_id
        )
        if workspace is not None:
            workspace.revision += 1
        self.repository.commit()
        return self._move(row)

    def abort(self, user_id, token, reason=''):
        row = self._move_from_token(user_id, token)
        if row.status == 'aborted':
            return self._move(row)
        if row.status == 'committed':
            raise WorkspaceTransferConflict(
                'a committed move cannot be aborted'
            )
        if row.status == 'expired':
            return self._move(row)
        row.status = 'aborted'
        row.aborted_at = self.clock()
        row.failure_reason = str(reason or '')[:256] or None
        self.repository.commit()
        return self._move(row)

    def state(self, user_id, workspace_key):
        workspace = self._workspace_owned(user_id, workspace_key)
        changed = False
        for move in workspace.move_tokens:
            before = move.status
            self._expire(move)
            changed = changed or before != move.status
        if changed:
            self.repository.commit()
        windows = {
            row.id: row.window_key for row in workspace.windows
            if row.user_id == user_id
        }
        return self._workspace(
            workspace, include_children=True, windows=windows
        )

    def _workspace_owned(self, user_id, workspace_key):
        workspace_key = _safe_id(workspace_key, 'workspace ID')
        row = self.repository.workspace(user_id, workspace_key)
        if row is None:
            raise WorkspaceTransferNotFound('workspace was not found')
        return row

    def _window_owned(self, user_id, workspace_id, window_key):
        window_key = _safe_id(window_key, 'window ID')
        row = self.repository.window(user_id, workspace_id, window_key)
        if row is None:
            raise WorkspaceTransferNotFound('workspace window was not found')
        return row

    def _tool_owned(self, user_id, workspace_id, tool_key):
        tool_key = _safe_id(tool_key, 'tool instance ID')
        row = self.repository.tool(user_id, workspace_id, tool_key)
        if row is None:
            raise WorkspaceTransferNotFound('tool instance was not found')
        return row

    @staticmethod
    def _assert_tool_placement(tool, window, placement):
        current = (
            tool.window_id, tool.dock_area, tool.tab_order,
            tool.placement_mode, tool.placement_revision,
        )
        requested = (
            window.id, placement['dockArea'], placement['tabOrder'],
            placement['mode'], placement['revision'],
        )
        if current != requested:
            raise WorkspaceTransferConflict(
                'tool placement changes require the move protocol'
            )

    def _create_checkpoint(self, user_id, tool, request):
        request = _object(request or {}, 'checkpoint request')
        expected = _integer(
            request.get('expected_revision', tool.checkpoint_revision),
            'expected checkpoint revision'
        )
        if expected != tool.checkpoint_revision:
            raise WorkspaceTransferConflict('tool checkpoint has changed')
        reference = _safe_id(
            request.get('checkpoint_reference') or tool.restore_reference,
            'checkpoint reference'
        )
        revision = tool.checkpoint_revision + 1
        row = self.repository.checkpoint_model(
            id=str(uuid.uuid4()), tool_instance_id=tool.id,
            user_id=user_id, revision=revision,
            checkpoint_reference=reference,
            view_state=_view_state(request.get('view_state') or {}),
        )
        tool.checkpoint_revision = revision
        self.repository.add(row)
        self.repository.flush()
        return {
            'checkpoint_id': row.id,
            'tool_instance_id': tool.tool_key,
            'revision': revision,
            'checkpoint_reference': reference,
        }

    def _proof(self, move_id, user_id, expires_at):
        message = (
            f'{move_id}:{user_id}:{expires_at.isoformat()}'
        ).encode('ascii')
        digest = hmac.new(
            self.signing_key, message, hashlib.sha256
        ).digest()
        return base64.urlsafe_b64encode(digest).rstrip(b'=').decode('ascii')

    def _move_from_token(self, user_id, token):
        if not isinstance(token, str) or token.count('.') != 1:
            raise WorkspaceTransferNotFound('move authorization was not found')
        move_id, proof = token.split('.', 1)
        try:
            uuid.UUID(move_id)
        except (TypeError, ValueError) as exc:
            raise WorkspaceTransferNotFound(
                'move authorization was not found'
            ) from exc
        row = self.repository.move(user_id, move_id)
        if row is None:
            raise WorkspaceTransferNotFound('move authorization was not found')
        expected = self._proof(row.id, user_id, row.expires_at)
        digest = hashlib.sha256(token.encode('ascii')).hexdigest()
        if not hmac.compare_digest(proof, expected) or not hmac.compare_digest(
            digest, row.token_digest
        ):
            raise WorkspaceTransferNotFound('move authorization was not found')
        self._expire(row)
        return row

    def _expire(self, row):
        if row.status in ACTIVE_MOVE_STATES and row.expires_at <= self.clock():
            row.status = 'expired'
            row.failure_reason = 'move authorization expired'

    def _require_active(self, row):
        self._expire(row)
        if row.status == 'expired':
            self.repository.commit()
            raise WorkspaceTransferExpired('move authorization expired')
        if row.status not in ACTIVE_MOVE_STATES:
            raise WorkspaceTransferConflict(
                f'move is already {row.status}'
            )

    def _token(self, row):
        return f'{row.id}.{self._proof(row.id, row.user_id, row.expires_at)}'

    def _move(self, row, include_proof=False):
        windows = {
            item.id: item.window_key for item in row.workspace.windows
        }
        checkpoint = next((
            item for item in row.tool.checkpoints
            if item.revision == row.checkpoint_revision
        ), None)
        value = {
            'move_id': row.id,
            'workspace_id': row.workspace.workspace_key,
            'tool_instance_id': row.tool.tool_key,
            'source_revision': row.source_revision,
            'checkpoint_revision': row.checkpoint_revision,
            'checkpoint_reference': (
                checkpoint.checkpoint_reference
                if checkpoint is not None else row.tool.restore_reference
            ),
            'source_window_id': windows.get(row.source_window_id),
            'destination_window_id': windows.get(
                row.destination_window_id
            ),
            'destination': json.loads(row.destination_placement),
            'status': row.status,
            'expires_at': row.expires_at.isoformat() + 'Z',
        }
        if include_proof:
            value['move_token'] = self._token(row)
        return value

    def _workspace(self, row, include_children, windows=None):
        value = {
            'workspace_id': row.workspace_key,
            'name': row.name,
            'revision': row.revision,
            'layout_reference': row.layout_reference,
        }
        if include_children:
            windows = windows or {}
            value['windows'] = [
                self._window(item) for item in row.windows
                if item.user_id == row.user_id
            ]
            value['tools'] = [
                self._tool(item, windows) for item in row.tools
                if item.user_id == row.user_id
            ]
            value['moves'] = [
                self._move(item, include_proof=True)
                for item in row.move_tokens
                if item.user_id == row.user_id and
                item.status in ACTIVE_MOVE_STATES
            ]
        return value

    @staticmethod
    def _window(row):
        return {
            'window_id': row.window_key,
            'role': row.role,
            'device_profile_id': row.device_profile_id,
            'display_fingerprint': row.display_fingerprint,
            'placement': json.loads(row.placement),
            'revision': row.revision,
            'clean_close': row.clean_close,
        }

    @staticmethod
    def _tool(row, windows):
        descriptor = json.loads(row.descriptor)
        descriptor['placement']['windowId'] = windows.get(
            row.window_id, descriptor['placement']['windowId']
        )
        descriptor['placement']['revision'] = row.placement_revision
        return {
            'tool_instance_id': row.tool_key,
            'descriptor': descriptor,
            'checkpoint_revision': row.checkpoint_revision,
        }


def init_app(app):
    """Initialize the service and its authenticated owner-scoped API."""
    existing = app.extensions.get(APP_EXTENSION_KEY)
    if existing is not None:
        return existing
    service = WorkspaceTransferService(
        WorkspaceTransferRepository(), app.config.get('SECRET_KEY'),
        token_ttl=app.config.get('CDE_WORKSPACE_MOVE_TOKEN_TTL', 120),
    )
    app.extensions[APP_EXTENSION_KEY] = service
    _register_routes(app)
    return service


def service_for_app(app):
    try:
        return app.extensions[APP_EXTENSION_KEY]
    except (AttributeError, KeyError) as exc:
        raise WorkspaceTransferError(
            'CDEadmin workspace transfer service is not initialized'
        ) from exc


def _register_routes(app):
    from flask import Blueprint, current_app, jsonify, request
    from flask_security import current_user
    from pgadmin.user_login_check import pga_login_required

    api = Blueprint('cdeadmin_workspace_transfer', __name__)

    def invoke(callback):
        service = service_for_app(current_app)
        try:
            payload = callback(service, current_user.id)
            return jsonify(success=1, data=payload)
        except WorkspaceTransferError as exc:
            service.repository.rollback()
            return jsonify(success=0, errormsg=str(exc)), exc.status_code
        except Exception as exc:
            from sqlalchemy.exc import IntegrityError

            if isinstance(exc, IntegrityError):
                service.repository.rollback()
                return jsonify(
                    success=0,
                    errormsg='Workspace state changed concurrently.'
                ), 409
            service.repository.rollback()
            current_app.logger.exception(
                'Unexpected workspace transfer service failure'
            )
            return jsonify(
                success=0,
                errormsg='Workspace transfer request could not be completed.'
            ), 500

    @api.route('/cdeadmin/api/workspaces/<workspace_id>',
               methods=['GET', 'PUT'])
    @pga_login_required
    def workspace(workspace_id):
        if request.method == 'GET':
            return invoke(lambda service, user_id: service.state(
                user_id, workspace_id
            ))
        return invoke(lambda service, user_id: service.ensure_workspace(
            user_id, workspace_id, request.get_json(silent=True) or {}
        ))

    @api.route(
        '/cdeadmin/api/workspaces/<workspace_id>/windows/<window_id>',
        methods=['PUT']
    )
    @pga_login_required
    def window(workspace_id, window_id):
        return invoke(lambda service, user_id: service.register_window(
            user_id, workspace_id, window_id,
            request.get_json(silent=True) or {}
        ))

    @api.route(
        '/cdeadmin/api/workspaces/<workspace_id>/tools/<tool_id>',
        methods=['PUT']
    )
    @pga_login_required
    def tool(workspace_id, tool_id):
        return invoke(lambda service, user_id: service.register_tool(
            user_id, workspace_id, tool_id,
            request.get_json(silent=True) or {}
        ))

    @api.route(
        '/cdeadmin/api/workspaces/<workspace_id>/tools/<tool_id>/'
        'checkpoints', methods=['POST']
    )
    @pga_login_required
    def checkpoint(workspace_id, tool_id):
        return invoke(lambda service, user_id: service.checkpoint(
            user_id, workspace_id, tool_id,
            request.get_json(silent=True) or {}
        ))

    @api.route(
        '/cdeadmin/api/workspaces/<workspace_id>/tools/<tool_id>/'
        'moves/prepare', methods=['POST']
    )
    @pga_login_required
    def prepare(workspace_id, tool_id):
        return invoke(lambda service, user_id: service.prepare(
            user_id, workspace_id, tool_id,
            request.get_json(silent=True) or {}
        ))

    def move_token(move_id):
        token = request.headers.get('X-CDEadmin-Workspace-Move', '')
        if not token.startswith(f'{move_id}.'):
            raise WorkspaceTransferNotFound(
                'move authorization was not found'
            )
        return token

    @api.route('/cdeadmin/api/workspace-moves/<move_id>/acknowledge',
               methods=['POST'])
    @pga_login_required
    def acknowledge(move_id):
        return invoke(lambda service, user_id: service.acknowledge(
            user_id, move_token(move_id), request.get_json(silent=True) or {}
        ))

    @api.route('/cdeadmin/api/workspace-moves/<move_id>/commit',
               methods=['POST'])
    @pga_login_required
    def commit(move_id):
        return invoke(
            lambda service, user_id: service.commit(
                user_id, move_token(move_id)
            )
        )

    @api.route('/cdeadmin/api/workspace-moves/<move_id>/abort',
               methods=['POST'])
    @pga_login_required
    def abort(move_id):
        body = request.get_json(silent=True) or {}
        return invoke(lambda service, user_id: service.abort(
            user_id, move_token(move_id), body.get('reason')
        ))

    app.register_blueprint(api)


__all__ = (
    'APP_EXTENSION_KEY',
    'WorkspaceTransferConflict',
    'WorkspaceTransferError',
    'WorkspaceTransferExpired',
    'WorkspaceTransferNotFound',
    'WorkspaceTransferRepository',
    'WorkspaceTransferService',
    'init_app',
    'service_for_app',
    'validate_descriptor',
)
