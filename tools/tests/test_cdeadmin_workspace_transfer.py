##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Authoritative workspace persistence and transfer protocol tests."""

from __future__ import annotations

import importlib.util
import sys
import unittest
from datetime import datetime, timedelta
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

from pgadmin.cdeadmin.workspace.transfer import (  # noqa: E402
    WorkspaceTransferConflict,
    WorkspaceTransferError,
    WorkspaceTransferExpired,
    WorkspaceTransferNotFound,
    WorkspaceTransferService,
    validate_descriptor,
)


MIGRATION_PATH = (
    ROOT / 'web/migrations/versions/cde_workspace_transfer_v1_.py'
)

try:
    import sqlalchemy as sa
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    MIGRATION_DEPENDENCIES = True
except ImportError:
    MIGRATION_DEPENDENCIES = False


class Row:
    def __init__(self, **values):
        self.__dict__.update(values)


class MemoryRepository:
    workspace_model = Row
    window_model = Row
    tool_model = Row
    checkpoint_model = Row
    move_model = Row

    def __init__(self):
        self.workspaces = []
        self.windows = []
        self.tools = []
        self.checkpoints = []
        self.moves = []
        self.rollback_count = 0

    def workspace(self, user_id, workspace_key):
        return self._find(
            self.workspaces, user_id=user_id, workspace_key=workspace_key
        )

    def workspace_by_id(self, user_id, workspace_id):
        return self._find(
            self.workspaces, user_id=user_id, id=workspace_id
        )

    def workspace_by_name(self, user_id, name):
        return self._find(self.workspaces, user_id=user_id, name=name)

    def window(self, user_id, workspace_id, window_key):
        return self._find(
            self.windows, user_id=user_id, workspace_id=workspace_id,
            window_key=window_key
        )

    def tool(self, user_id, workspace_id, tool_key):
        return self._find(
            self.tools, user_id=user_id, workspace_id=workspace_id,
            tool_key=tool_key
        )

    def tool_by_id(self, user_id, tool_id):
        return self._find(self.tools, user_id=user_id, id=tool_id)

    def move(self, user_id, move_id):
        return self._find(self.moves, user_id=user_id, id=move_id)

    def move_for_idempotency(self, user_id, idempotency_key):
        return self._find(
            self.moves, user_id=user_id, idempotency_key=idempotency_key
        )

    def active_move(self, user_id, tool_id):
        return next((
            row for row in reversed(self.moves)
            if row.user_id == user_id and row.tool_instance_id == tool_id
            and row.status in {'prepared', 'acknowledged'}
        ), None)

    def move_tool(self, user_id, tool_id, expected_revision, values):
        row = self._find(
            self.tools, user_id=user_id, id=tool_id,
            placement_revision=expected_revision
        )
        if row is None:
            return 0
        row.__dict__.update(values)
        return 1

    def add(self, row):
        if hasattr(row, 'workspace_key'):
            row.windows = []
            row.tools = []
            row.move_tokens = []
            self.workspaces.append(row)
        elif hasattr(row, 'window_key'):
            row.workspace = self._find(
                self.workspaces, id=row.workspace_id
            )
            row.workspace.windows.append(row)
            self.windows.append(row)
        elif hasattr(row, 'tool_key'):
            row.workspace = self._find(
                self.workspaces, id=row.workspace_id
            )
            row.checkpoints = []
            row.move_tokens = []
            row.workspace.tools.append(row)
            self.tools.append(row)
        elif hasattr(row, 'checkpoint_reference'):
            row.tool = self._find(
                self.tools, id=row.tool_instance_id
            )
            row.tool.checkpoints.append(row)
            self.checkpoints.append(row)
        elif hasattr(row, 'token_digest'):
            row.workspace = self._find(
                self.workspaces, id=row.workspace_id
            )
            row.tool = self._find(self.tools, id=row.tool_instance_id)
            row.destination_window_id = None
            row.acknowledged_at = None
            row.committed_at = None
            row.aborted_at = None
            row.failure_reason = None
            row.workspace.move_tokens.append(row)
            row.tool.move_tokens.append(row)
            self.moves.append(row)

    @staticmethod
    def _find(rows, **values):
        return next((
            row for row in rows
            if all(getattr(row, key, None) == value
                   for key, value in values.items())
        ), None)

    @staticmethod
    def flush():
        return None

    @staticmethod
    def commit():
        return None

    def rollback(self):
        self.rollback_count += 1


def descriptor(workspace='workspace-one', window='main'):
    return {
        'schema': 'cdeadmin.tool-instance.v1',
        'schemaVersion': 1,
        'toolInstanceId': 'id-query-tool-42',
        'toolKind': 'query_editor',
        'restoreRef': 'query-session-42',
        'projectId': 'project-one',
        'context': {
            'providerId': 'org.example.engine',
            'endpointId': 17,
            'routeId': 'primary',
        },
        'presentation': {'title': 'Query', 'iconKey': 'tool.query'},
        'placement': {
            'mode': 'docked', 'workspaceId': workspace,
            'windowId': window, 'dockArea': 'main', 'tabOrder': 0,
            'revision': 0,
        },
        'state': {
            'dirty': False, 'transactionState': 'idle',
            'connectionState': 'connected', 'sharedSession': False,
        },
        'capabilities': {
            'detachable': True, 'duplicable': False,
            'requiresLiveSession': True,
        },
    }


class WorkspaceTransferTests(unittest.TestCase):
    def setUp(self):
        self.repository = MemoryRepository()
        self.instant = datetime(2026, 9, 5, 12, 0, 0)
        self.service = WorkspaceTransferService(
            self.repository, b'test-signing-authority',
            clock=lambda: self.instant, token_ttl=120,
        )
        self.service.ensure_workspace(
            7, 'workspace-one', {'name': 'Main workspace'}
        )
        self.service.register_window(
            7, 'workspace-one', 'main', {'role': 'main'}
        )
        self.service.register_window(
            7, 'workspace-one', 'detached-1',
            {'role': 'detached-tool'}
        )
        self.service.register_tool(
            7, 'workspace-one', 'id-query-tool-42',
            {'descriptor': descriptor()}
        )

    def prepare(self, key='move-one'):
        return self.service.prepare(
            7, 'workspace-one', 'id-query-tool-42', {
                'expected_revision': 0,
                'idempotency_key': key,
                'destination': {
                    'mode': 'detached', 'workspaceId': 'workspace-one',
                    'windowId': 'detached-1', 'dockArea': 'main',
                    'tabOrder': 0, 'revision': 0,
                },
                'checkpoint': {
                    'checkpoint_reference': 'query-session-42',
                    'view_state': {
                        'activePanel': 'results', 'scrollTop': 180,
                    },
                },
            }
        )

    def test_prepare_acknowledge_commit_is_atomic_and_idempotent(self):
        prepared = self.prepare()
        tool = self.repository.tools[0]
        self.assertEqual('prepared', prepared['status'])
        self.assertEqual('query-session-42', prepared['checkpoint_reference'])
        self.assertEqual('main', prepared['source_window_id'])
        self.assertEqual(
            'main', descriptor_from(tool)['placement']['windowId']
        )
        self.assertEqual(0, tool.placement_revision)
        replay = self.prepare()
        self.assertEqual(prepared['move_token'], replay['move_token'])

        acknowledged = self.service.acknowledge(
            7, prepared['move_token'], {
                'restored_tool_instance_id': 'id-query-tool-42',
                'checkpoint_revision': prepared['checkpoint_revision'],
            }
        )
        self.assertEqual('acknowledged', acknowledged['status'])
        committed = self.service.commit(7, prepared['move_token'])
        self.assertEqual('committed', committed['status'])
        self.assertEqual(1, tool.placement_revision)
        self.assertEqual(
            'detached-1', descriptor_from(tool)['placement']['windowId']
        )
        self.assertEqual(
            'committed',
            self.service.commit(7, prepared['move_token'])['status']
        )
        with self.assertRaisesRegex(
            WorkspaceTransferConflict, 'cannot be aborted'
        ):
            self.service.abort(7, prepared['move_token'])

    def test_expired_or_wrong_owner_proof_never_moves_source(self):
        prepared = self.prepare()
        self.instant += timedelta(seconds=121)
        with self.assertRaises(WorkspaceTransferExpired):
            self.service.acknowledge(
                7, prepared['move_token'], {
                    'restored_tool_instance_id': 'id-query-tool-42',
                    'checkpoint_revision': prepared['checkpoint_revision'],
                }
            )
        tool = self.repository.tools[0]
        self.assertEqual(0, tool.placement_revision)
        self.assertEqual(
            'main', descriptor_from(tool)['placement']['windowId']
        )
        with self.assertRaises(WorkspaceTransferNotFound):
            self.service.commit(8, prepared['move_token'])

    def test_concurrent_move_and_checkpoint_conflicts_are_rejected(self):
        prepared = self.prepare()
        with self.assertRaisesRegex(
            WorkspaceTransferConflict, 'active move'
        ):
            self.prepare('move-two')
        with self.assertRaisesRegex(
            WorkspaceTransferConflict, 'checkpoint has changed'
        ):
            self.service.checkpoint(
                7, 'workspace-one', 'id-query-tool-42', {
                    'expected_revision': 0,
                    'checkpoint_reference': 'query-session-42',
                }
            )
        aborted = self.service.abort(
            7, prepared['move_token'], 'destination closed'
        )
        self.assertEqual('aborted', aborted['status'])
        self.assertEqual(0, self.repository.tools[0].placement_revision)

    def test_descriptors_and_checkpoints_are_secret_free(self):
        unsafe = descriptor()
        unsafe['context']['password'] = 'must-never-persist'
        with self.assertRaisesRegex(
            WorkspaceTransferError, 'sensitive field'
        ):
            validate_descriptor(unsafe)
        with self.assertRaisesRegex(
            WorkspaceTransferError, 'unsupported fields'
        ):
            self.service.checkpoint(
                7, 'workspace-one', 'id-query-tool-42', {
                    'checkpoint_reference': 'query-session-42',
                    'view_state': {'queryText': 'select forbidden'},
                }
            )

    def test_workspace_state_contains_logical_ids_not_internal_ids(self):
        state = self.service.state(7, 'workspace-one')
        self.assertEqual('workspace-one', state['workspace_id'])
        self.assertEqual(
            {'main', 'detached-1'},
            {item['window_id'] for item in state['windows']}
        )
        self.assertEqual(
            'id-query-tool-42', state['tools'][0]['tool_instance_id']
        )
        self.assertEqual([], state['moves'])


def descriptor_from(tool):
    import json
    return json.loads(tool.descriptor)


@unittest.skipUnless(MIGRATION_DEPENDENCIES, 'migration dependencies missing')
class WorkspaceTransferMigrationTests(unittest.TestCase):
    def test_upgrade_and_downgrade_are_reversible(self):
        spec = importlib.util.spec_from_file_location(
            'cde_workspace_transfer_migration', MIGRATION_PATH
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        engine = sa.create_engine('sqlite://')
        with engine.begin() as connection:
            connection.execute(sa.text(
                'CREATE TABLE user (id INTEGER PRIMARY KEY)'
            ))
            context = MigrationContext.configure(connection)
            module.op = Operations(context)
            module.upgrade()
            inspector = sa.inspect(connection)
            expected = {
                'cde_workspace', 'cde_workspace_window',
                'cde_tool_instance', 'cde_tool_checkpoint',
                'cde_workspace_move_token',
            }
            self.assertTrue(expected.issubset(
                set(inspector.get_table_names())
            ))
            move_columns = {
                item['name'] for item in inspector.get_columns(
                    'cde_workspace_move_token'
                )
            }
            self.assertIn('token_digest', move_columns)
            self.assertNotIn('move_token', move_columns)
            module.downgrade()
            self.assertTrue(expected.isdisjoint(
                set(sa.inspect(connection).get_table_names())
            ))


if __name__ == '__main__':
    unittest.main()
