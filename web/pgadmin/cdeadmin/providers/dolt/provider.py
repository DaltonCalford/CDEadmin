"""Dolt 1.86.6 versioned relational provider."""

import re
from dataclasses import replace
from urllib.parse import urlsplit

from pgadmin.cdeadmin.sdk import (
    ActualEnginePilotProvider,
    PilotProfile,
    RelationalClientError,
)
from ..distributed_sql import (
    create_sql_client,
    mysql_catalog,
    optional_rows,
    resource,
    sql_administration,
)
from ..distributed_control_plane import DistributedSQLControlPlane
from pgadmin.cdeadmin.visual_admin import (
    ControlPlaneOperation,
    control_plane_field as cp_field,
)


PROFILE = PilotProfile(
    'org.cdeadmin.dolt', 'dolt-native', 'dolt', 'Dolt', '1.86.6',
    'mysql_wire', 'versioned-relational', 'dolt-sql', 'Dolt SQL',
    'dolt-sql-and-version-control-native', 'tabular',
    (
        'cluster', 'database', 'table', 'column', 'index', 'constraint',
        'view', 'procedure', 'branch', 'tag', 'commit', 'remote',
        'working-set', 'merge', 'rebase', 'conflict', 'backup', 'user', 'role',
        'privilege',
    ),
    ('dolt-sql', 'dolt-log', 'dolt-diff', 'dolt-backup'),
    semantic_sql_dialect={
        'language_profile': 'dolt-sql', 'quote_open': '`',
        'quote_close': '`', 'supports_rollup': False,
    },
)


_BASE_ADMINISTRATION = sql_administration('dolt', 'mysql', (
    'procedure', 'branch', 'tag', 'commit', 'remote', 'working-set',
    'merge', 'rebase', 'conflict', 'backup',
))


def _choice(field_id, label, values, **options):
    return cp_field(
        field_id, label, 'select', options=[
            {'value': value, 'label': title} for value, title in values
        ], **options
    )


_NAME = r'[A-Za-z0-9_./:@+-]+'
CONTROL_OPERATIONS = (
    ControlPlaneOperation(
        'branch', 'create', 'Create branch', 'admin', 'topology_admin', (
            cp_field('name', 'Branch name', 'text', True,
                     max_length=512, pattern=_NAME),
            cp_field('start_point', 'Start point', 'text', False,
                     max_length=512, pattern=_NAME),
        ), target_required=False, impact_scope='resource'
    ),
    ControlPlaneOperation(
        'branch', 'set_default', 'Make default branch', 'admin',
        'topology_admin', (
            cp_field('persist', 'Persist across server restarts', 'boolean',
                     False, default=True),
        ), impact_scope='resource'
    ),
    ControlPlaneOperation(
        'branch', 'rename', 'Rename branch', 'admin', 'topology_admin', (
            cp_field('new_name', 'New branch name', 'text', True,
                     max_length=512, pattern=_NAME),
            cp_field('force', 'Replace destination branch', 'boolean', False,
                     default=False),
        ), impact_scope='resource'
    ),
    ControlPlaneOperation(
        'branch', 'copy', 'Copy branch', 'admin', 'topology_admin', (
            cp_field('new_name', 'New branch name', 'text', True,
                     max_length=512, pattern=_NAME),
            cp_field('force', 'Replace destination branch', 'boolean', False,
                     default=False),
        ), impact_scope='resource'
    ),
    ControlPlaneOperation(
        'branch', 'drop', 'Delete branch', 'destructive',
        'topology_admin', (
            cp_field('force', 'Force deletion', 'boolean', False,
                     default=False),
        ), impact_scope='resource'
    ),
    ControlPlaneOperation(
        'tag', 'create', 'Create tag', 'admin', 'topology_admin', (
            cp_field('name', 'Tag name', 'text', True, max_length=512,
                     pattern=_NAME),
            cp_field('start_point', 'Target revision', 'text', False,
                     max_length=512, pattern=_NAME),
            cp_field('message', 'Tag message', 'multiline', False,
                     max_length=8192),
        ), target_required=False, impact_scope='resource'
    ),
    ControlPlaneOperation(
        'tag', 'drop', 'Delete tag', 'destructive', 'topology_admin',
        impact_scope='resource'
    ),
    ControlPlaneOperation(
        'working-set', 'commit', 'Commit working set', 'admin',
        'maintenance_admin', (
            cp_field('message', 'Commit message', 'multiline', True,
                     max_length=65536),
            cp_field('stage_all', 'Stage all changes', 'boolean', False,
                     default=True),
            cp_field('allow_empty', 'Allow empty commit', 'boolean', False,
                     default=False),
            cp_field('author', 'Author (Name <email>)', 'text', False,
                     max_length=1024),
        ), impact_scope='resource'
    ),
    ControlPlaneOperation(
        'working-set', 'stage_tables', 'Stage table changes', 'admin',
        'maintenance_admin', (
            cp_field('table_names', 'Table names', 'json', True,
                     json_type='array'),
            cp_field('force_ignored', 'Include ignored tables', 'boolean',
                     False, default=False),
        ), impact_scope='resource'
    ),
    ControlPlaneOperation(
        'working-set', 'stage_all', 'Stage all changes', 'admin',
        'maintenance_admin', impact_scope='resource'
    ),
    ControlPlaneOperation(
        'working-set', 'unstage_tables', 'Unstage table changes', 'admin',
        'maintenance_admin', (
            cp_field('table_names', 'Table names', 'json', True,
                     json_type='array'),
        ), impact_scope='resource'
    ),
    ControlPlaneOperation(
        'working-set', 'reset_hard', 'Reset working set and staging',
        'destructive', 'maintenance_admin', (
            cp_field('revision', 'Target revision', 'text', False,
                     max_length=512, pattern=_NAME),
        ), impact_scope='resource', post_state_required=False
    ),
    ControlPlaneOperation(
        'working-set', 'reset_soft', 'Move HEAD without changing tables',
        'destructive', 'maintenance_admin', (
            cp_field('revision', 'Target revision', 'text', False,
                     max_length=512, pattern=_NAME),
        ), impact_scope='resource', post_state_required=False
    ),
    ControlPlaneOperation(
        'working-set', 'clean', 'Remove untracked tables', 'destructive',
        'maintenance_admin', (
            cp_field('table_names', 'Table names (empty means all)', 'json',
                     False, default=[], json_type='array'),
            cp_field('include_ignored', 'Include ignored tables', 'boolean',
                     False, default=False),
        ), impact_scope='resource', post_state_required=False
    ),
    ControlPlaneOperation(
        'merge', 'start', 'Merge revision', 'admin', 'topology_admin', (
            cp_field('revision', 'Revision', 'text', True,
                     max_length=512, pattern=_NAME),
            cp_field('message', 'Merge message', 'multiline', False,
                     max_length=65536),
            _choice('strategy', 'Merge mode', (
                ('normal', 'Normal'), ('squash', 'Squash'),
                ('no_ff', 'No fast-forward'),
            ), required=True, default='normal'),
            cp_field('no_commit', 'Do not commit', 'boolean', False,
                     default=False),
        ), target_required=False, impact_scope='resource',
        long_running=True, cancellable=True
    ),
    ControlPlaneOperation(
        'merge', 'abort', 'Abort merge', 'destructive',
        'maintenance_admin', target_required=False, impact_scope='resource'
    ),
    ControlPlaneOperation(
        'rebase', 'start', 'Start rebase', 'admin', 'topology_admin', (
            cp_field('upstream', 'Upstream revision', 'text', True,
                     max_length=512, pattern=_NAME),
            cp_field('interactive', 'Pause for plan editing', 'boolean',
                     False, default=False),
            _choice('empty_commits', 'Commits that become empty', (
                ('drop', 'Drop'), ('keep', 'Keep'),
            ), required=True, default='drop'),
            cp_field('skip_verification', 'Skip commit verification',
                     'boolean', False, default=False),
        ), impact_scope='resource', long_running=True, cancellable=True,
        post_state_required=False
    ),
    ControlPlaneOperation(
        'rebase', 'continue', 'Continue rebase', 'admin',
        'topology_admin', impact_scope='resource', long_running=True,
        cancellable=True, post_state_required=False
    ),
    ControlPlaneOperation(
        'rebase', 'abort', 'Abort rebase', 'destructive',
        'maintenance_admin', impact_scope='resource',
        post_state_required=False
    ),
    ControlPlaneOperation(
        'commit', 'cherry_pick', 'Cherry-pick commit', 'admin',
        'topology_admin', impact_scope='resource', long_running=True,
        cancellable=True
    ),
    ControlPlaneOperation(
        'commit', 'revert', 'Revert commit', 'destructive',
        'topology_admin', impact_scope='resource', long_running=True,
        cancellable=True
    ),
    ControlPlaneOperation(
        'remote', 'create', 'Add remote', 'admin', 'topology_admin', (
            cp_field('name', 'Remote name', 'text', True,
                     max_length=256, pattern=_NAME),
            cp_field('url', 'Approved remote URL', 'text', True,
                     max_length=4096, sensitive=True),
        ), target_required=False, impact_scope='resource'
    ),
    ControlPlaneOperation(
        'remote', 'drop', 'Remove remote', 'destructive',
        'topology_admin', impact_scope='resource'
    ),
    ControlPlaneOperation(
        'remote', 'fetch', 'Fetch remote', 'admin',
        'replication_admin', impact_scope='resource', long_running=True
    ),
    ControlPlaneOperation(
        'remote', 'pull', 'Pull remote', 'admin',
        'replication_admin', (
            cp_field('branch', 'Remote branch', 'text', False,
                     max_length=512, pattern=_NAME),
        ), impact_scope='resource', long_running=True
    ),
    ControlPlaneOperation(
        'remote', 'push', 'Push to remote', 'admin',
        'replication_admin', (
            cp_field('refspec', 'Refspec', 'text', True,
                     max_length=1024, pattern=_NAME),
            cp_field('set_upstream', 'Set upstream', 'boolean', False,
                     default=False),
        ), impact_scope='resource', long_running=True
    ),
    ControlPlaneOperation(
        'conflict', 'resolve', 'Resolve table conflicts', 'destructive',
        'maintenance_admin', (
            _choice('resolution', 'Resolution', (
                ('ours', 'Use ours'), ('theirs', 'Use theirs'),
            ), required=True),
        ), impact_scope='resource'
    ),
    ControlPlaneOperation(
        'backup', 'create', 'Register backup destination', 'admin',
        'backup_admin', (
            cp_field('name', 'Backup name', 'text', True, max_length=256,
                     pattern=_NAME),
            cp_field('url', 'Approved backup URL', 'text', True,
                     max_length=4096, sensitive=True),
        ), target_required=False, impact_scope='resource'
    ),
    ControlPlaneOperation(
        'backup', 'sync', 'Synchronize backup', 'admin', 'backup_admin',
        impact_scope='resource', long_running=True
    ),
    ControlPlaneOperation(
        'backup', 'drop', 'Remove backup registration', 'destructive',
        'backup_admin', impact_scope='resource'
    ),
    ControlPlaneOperation(
        'database', 'restore_backup', 'Restore backup as database',
        'destructive', 'restore_admin', (
            cp_field('url', 'Approved backup URL', 'text', True,
                     max_length=4096, sensitive=True),
            cp_field('new_database_name', 'New database name', 'text', True,
                     max_length=256,
                     pattern=r'[A-Za-z_][A-Za-z0-9_$-]*'),
            cp_field('force', 'Replace existing database', 'boolean', False,
                     default=False),
        ), impact_scope='resource', long_running=True
    ),
)


def _target_name(request):
    target = request.get('target_resource')
    if not isinstance(target, dict) or not isinstance(
            target.get('display_name'), str):
        raise RelationalClientError('Dolt control target is required')
    return target['display_name']


def _approved_url(request, field_id='url', allowlist='remote_url_allowlist'):
    value = request.get('draft', {}).get(field_id)
    if not isinstance(value, str) or not value.strip():
        raise RelationalClientError('Dolt remote URL is required')
    value = value.strip()
    if urlsplit(value).scheme.lower() not in {
            'https', 'ssh', 'file', 'aws', 'gs'}:
        raise RelationalClientError('Dolt remote URL scheme is not admitted')
    prefixes = (request.get('_provider_route') or {}).get(allowlist)
    if isinstance(prefixes, str):
        prefixes = [item.strip() for item in prefixes.split(',')]
    if not isinstance(prefixes, list) or not prefixes or not any(
            isinstance(prefix, str) and value.startswith(prefix)
            for prefix in prefixes):
        raise RelationalClientError(
            'Dolt remote URL is outside the endpoint allowlist')
    return value


def _call(name, arguments):
    return {
        'source': 'CALL ' + name + '(' + ', '.join(
            '%s' for _argument in arguments
        ) + ')',
        'parameters': tuple(arguments),
    }


def _default_branch_variable(request):
    route = request.get('_provider_route') or {}
    database = route.get('database')
    if not isinstance(database, str) or not database.strip():
        raise RelationalClientError(
            'Dolt default-branch control requires a database route'
        )
    database = database.split('/', 1)[0]
    if not database or len(database) > 256 or any(
            character in database for character in '\x00\r\n'):
        raise RelationalClientError('Dolt database name is invalid')
    return '`' + database.replace('`', '``') + '_default_branch`'


def _table_names(draft, required=False):
    names = draft.get('table_names', [])
    if not isinstance(names, list) or len(names) > 1000 or (
            required and not names):
        raise RelationalClientError('Dolt table-name list is invalid')
    if not all(
        isinstance(name, str) and 0 < len(name) <= 256 and
        not any(character in name for character in '\x00\r\n')
        for name in names
    ):
        raise RelationalClientError('Dolt table-name list is invalid')
    return list(names)


def _compile_control_plane(request):
    kind = request['resource_kind']
    operation = request['operation_id']
    draft = request.get('draft') or {}
    arguments = []
    procedure = None
    if kind == 'branch':
        if operation == 'create':
            name = draft['name']
            procedure, arguments = 'DOLT_BRANCH', [name]
            if draft.get('start_point'):
                arguments.append(draft['start_point'])
        elif operation == 'set_default':
            assignment = (
                'SET PERSIST ' if draft.get('persist', True)
                else 'SET @@GLOBAL.'
            )
            return {
                'statements': [{
                    'source': (
                        assignment +
                        _default_branch_variable(request) + ' = %s'
                    ),
                    'parameters': (_target_name(request),),
                }],
                'impact': {
                    'scope': 'resource',
                    'target_resource_id': request[
                        'target_resource'
                    ].get('resource_id'),
                    'availability_risk': 'medium',
                    'data_movement_possible': False,
                },
            }
        elif operation in {'rename', 'copy'}:
            new_name = draft.get('new_name')
            if not isinstance(new_name, str) or not re.fullmatch(
                    _NAME, new_name):
                raise RelationalClientError(
                    'Dolt destination branch name is invalid')
            flag = '-m' if operation == 'rename' else '-c'
            if draft.get('force'):
                flag += 'f'
            procedure, arguments = 'DOLT_BRANCH', [
                flag, _target_name(request), new_name,
            ]
        elif operation == 'drop':
            procedure = 'DOLT_BRANCH'
            arguments = [
                '-D' if draft.get('force') else '-d', _target_name(request)
            ]
    elif kind == 'tag':
        if operation == 'create':
            procedure, arguments = 'DOLT_TAG', []
            if draft.get('message'):
                arguments.extend(['-m', draft['message']])
            arguments.append(draft['name'])
            if draft.get('start_point'):
                arguments.append(draft['start_point'])
        elif operation == 'drop':
            procedure, arguments = 'DOLT_TAG', ['-d', _target_name(request)]
    elif kind == 'working-set':
        if operation == 'commit':
            procedure, arguments = 'DOLT_COMMIT', []
            if draft.get('stage_all', True):
                arguments.append('-A')
            if draft.get('allow_empty'):
                arguments.append('--allow-empty')
            arguments.extend(['-m', draft['message']])
            if draft.get('author'):
                arguments.extend(['--author', draft['author']])
        elif operation in {'stage_tables', 'stage_all'}:
            procedure = 'DOLT_ADD'
            arguments = ['-A'] if operation == 'stage_all' else []
            if draft.get('force_ignored'):
                arguments.append('-f')
            if operation == 'stage_tables':
                arguments.extend(_table_names(draft, required=True))
        elif operation == 'unstage_tables':
            procedure, arguments = 'DOLT_RESET', _table_names(
                draft, required=True
            )
        elif operation in {'reset_hard', 'reset_soft'}:
            procedure = 'DOLT_RESET'
            arguments = [
                '--hard' if operation == 'reset_hard' else '--soft'
            ]
            if draft.get('revision'):
                arguments.append(draft['revision'])
        elif operation == 'clean':
            procedure, arguments = 'DOLT_CLEAN', []
            if draft.get('include_ignored'):
                arguments.append('-x')
            arguments.extend(_table_names(draft))
    elif kind == 'merge':
        procedure = 'DOLT_MERGE'
        if operation == 'abort':
            arguments = ['--abort']
        else:
            arguments = [draft['revision']]
            strategy = draft.get('strategy', 'normal')
            if strategy == 'squash':
                arguments.append('--squash')
            elif strategy == 'no_ff':
                arguments.append('--no-ff')
            elif strategy != 'normal':
                raise RelationalClientError('Dolt merge mode is invalid')
            if draft.get('no_commit'):
                arguments.append('--no-commit')
            if draft.get('message'):
                arguments.extend(['-m', draft['message']])
    elif kind == 'rebase':
        procedure = 'DOLT_REBASE'
        if operation == 'start':
            arguments = [draft['upstream']]
            if draft.get('interactive'):
                arguments.append('--interactive')
            empty = draft.get('empty_commits', 'drop')
            if empty not in {'drop', 'keep'}:
                raise RelationalClientError(
                    'Dolt empty-commit rebase policy is invalid')
            arguments.extend(['--empty', empty])
            if draft.get('skip_verification'):
                arguments.append('--skip-verification')
        elif operation == 'continue':
            arguments = ['--continue']
        elif operation == 'abort':
            arguments = ['--abort']
    elif kind == 'commit' and operation in {'cherry_pick', 'revert'}:
        procedure = (
            'DOLT_CHERRY_PICK' if operation == 'cherry_pick'
            else 'DOLT_REVERT'
        )
        arguments = [_target_name(request)]
    elif kind == 'remote':
        name = draft.get('name') if operation == 'create' else (
            _target_name(request)
        )
        if operation == 'create':
            procedure, arguments = 'DOLT_REMOTE', [
                'add', name, _approved_url(request)
            ]
        elif operation == 'drop':
            procedure, arguments = 'DOLT_REMOTE', ['remove', name]
        elif operation == 'fetch':
            procedure, arguments = 'DOLT_FETCH', [name]
        elif operation == 'pull':
            procedure, arguments = 'DOLT_PULL', [name]
            if draft.get('branch'):
                arguments.append(draft['branch'])
        elif operation == 'push':
            procedure, arguments = 'DOLT_PUSH', [name]
            if draft.get('set_upstream'):
                arguments.append('--set-upstream')
            arguments.append(draft['refspec'])
    elif kind == 'conflict' and operation == 'resolve':
        if draft.get('resolution') not in {'ours', 'theirs'}:
            raise RelationalClientError('Dolt conflict resolution is invalid')
        procedure, arguments = 'DOLT_CONFLICTS_RESOLVE', [
            '--' + draft['resolution'], _target_name(request)
        ]
    elif kind == 'backup':
        if operation == 'create':
            procedure, arguments = 'DOLT_BACKUP', [
                'add', draft['name'], _approved_url(
                    request, allowlist='backup_url_allowlist')
            ]
        elif operation == 'sync':
            procedure, arguments = 'DOLT_BACKUP', [
                'sync', _target_name(request)
            ]
        elif operation == 'drop':
            procedure, arguments = 'DOLT_BACKUP', [
                'remove', _target_name(request)
            ]
    elif kind == 'database' and operation == 'restore_backup':
        procedure, arguments = 'DOLT_BACKUP', [
            'restore', _approved_url(
                request, allowlist='backup_url_allowlist'),
            draft['new_database_name'],
        ]
        if draft.get('force'):
            arguments.append('--force')
    if procedure is None:
        raise RelationalClientError(
            'Dolt control-plane operation is unavailable')
    return {
        'statements': [_call(procedure, arguments)],
        'impact': {
            'scope': 'resource',
            'target_resource_id': (
                request.get('target_resource') or {}
            ).get('resource_id'),
            'availability_risk': (
                'high' if operation in {
                    'drop', 'abort', 'resolve', 'reset_hard', 'clean'
                }
                else 'medium'
            ),
            'data_movement_possible': operation in {
                'fetch', 'pull', 'push', 'start', 'cherry_pick', 'revert',
                'sync', 'restore_backup', 'continue', 'reset_hard',
            },
        },
    }


def _operation_parts(request):
    plan = request.get('plan')
    payload = request.get('provider_payload')
    if not isinstance(plan, dict) or not isinstance(payload, dict) or not (
            isinstance(payload.get('route'), dict)):
        raise RelationalClientError('Dolt provider operation is invalid')
    return plan, payload['route']


def _query_once(client, route, source, parameters=()):
    connection = client._connect({'route': route})
    cursor = None
    try:
        cursor = connection.cursor()
        cursor.execute(source, parameters)
        description = getattr(cursor, 'description', None)
        return {
            'columns': [str(item[0]) for item in description or []],
            'rows': list(cursor.fetchall()) if description else [],
            'provider_observation_only': True,
            'finality_interpreted_by_common_code': False,
        }
    finally:
        if cursor is not None:
            client._safe_close(cursor)
        client._forget_and_close(connection)


def _mutate_once(client, route, statement):
    connection = client._connect({'route': route})
    cursor = None
    commit_requested = False
    try:
        cursor = connection.cursor()
        cursor.execute(statement['source'], statement['parameters'])
        commit = getattr(connection, 'commit', None)
        if callable(commit):
            commit_requested = True
            commit()
        return {
            'request_accepted_by_driver': True,
            'commit_requested': commit_requested,
            'provider_finality_authority': True,
            'automatic_mutation_retry': False,
        }
    except Exception as exc:
        rollback = getattr(connection, 'rollback', None)
        if callable(rollback):
            try:
                rollback()
            except Exception:
                pass
        raise RelationalClientError(
            f'Dolt cancellation request failed ({type(exc).__name__})'
        ) from None
    finally:
        if cursor is not None:
            client._safe_close(cursor)
        client._forget_and_close(connection)


def _logical_name(plan):
    draft = plan.get('draft') or {}
    if plan['operation_id'] == 'create':
        return draft.get('name')
    if plan['resource_kind'] == 'branch' and plan[
            'operation_id'] in {'rename', 'copy'}:
        return draft.get('new_name')
    target = plan.get('target_resource') or {}
    return target.get('display_name')


def _inspect_control_plane(client, request):
    plan, route = _operation_parts(request)
    kind = plan['resource_kind']
    operation = plan['operation_id']
    name = _logical_name(plan)
    if kind == 'branch':
        if operation == 'set_default':
            return _query_once(
                client, route,
                'SELECT @@GLOBAL.' + _default_branch_variable({
                    '_provider_route': route,
                }),
            )
        return _query_once(
            client, route,
            'SELECT name, hash, latest_committer, latest_commit_date '
            'FROM dolt_branches WHERE name = %s', (name,),
        )
    if kind == 'tag':
        return _query_once(
            client, route,
            'SELECT tag_name, tag_hash, tagger, date FROM dolt_tags '
            'WHERE tag_name = %s', (name,),
        )
    if kind == 'remote':
        return _query_once(
            client, route,
            'SELECT name, url, fetch_specs FROM dolt_remotes WHERE name = %s',
            (name,),
        )
    if kind == 'backup':
        return _query_once(
            client, route,
            'SELECT name, url, params FROM dolt_backups WHERE name = %s',
            (name,),
        )
    if kind == 'database' and operation == 'restore_backup':
        return _query_once(
            client, route,
            'SELECT schema_name FROM information_schema.schemata '
            'WHERE schema_name = %s',
            (plan.get('draft', {}).get('new_database_name'),),
        )
    if kind == 'conflict':
        return _query_once(
            client, route,
            'SELECT table_name, num_conflicts FROM dolt_conflicts '
            'WHERE table_name = %s', (name,),
        )
    if kind == 'working-set':
        if operation == 'commit':
            return _query_once(
                client, route,
                'SELECT commit_hash, message, date FROM dolt_log '
                'ORDER BY date DESC LIMIT 1',
            )
        return _query_once(
            client, route,
            'SELECT table_name, staged, status FROM dolt_status '
            'ORDER BY table_name',
        )
    if kind == 'rebase':
        return _query_once(
            client, route,
            'SELECT rebase_order, action, commit_hash, commit_message '
            'FROM dolt_rebase ORDER BY rebase_order',
        )
    return _query_once(
        client, route,
        'SELECT active_branch(), table_name, staged, status '
        'FROM dolt_status ORDER BY table_name',
    )


def _cancel_control_plane(client, request):
    plan, route = _operation_parts(request)
    if plan['resource_kind'] == 'merge' and plan['operation_id'] == 'start':
        statement = _call('DOLT_MERGE', ['--abort'])
    elif plan['resource_kind'] == 'rebase' and plan[
            'operation_id'] in {'start', 'continue'}:
        statement = _call('DOLT_REBASE', ['--abort'])
    elif plan['resource_kind'] == 'commit' and plan[
            'operation_id'] in {'cherry_pick', 'revert'}:
        statement = _call(
            'DOLT_CHERRY_PICK' if plan['operation_id'] == 'cherry_pick'
            else 'DOLT_REVERT',
            ['--abort'],
        )
    else:
        raise RelationalClientError(
            'Dolt operation is not declared cancellable')
    return _mutate_once(client, route, statement)


def _post_validate_control_plane(client, request):
    plan, _route = _operation_parts(request)
    observation = _inspect_control_plane(client, request)
    rows = observation.get('rows') or []
    kind = plan['resource_kind']
    operation = plan['operation_id']
    confirmed = False
    if kind in {'branch', 'tag', 'remote', 'backup'}:
        if operation == 'set_default':
            confirmed = bool(rows) and str(rows[0][0]) == _logical_name(plan)
        else:
            confirmed = bool(rows) != (operation == 'drop')
    elif kind == 'conflict' and operation == 'resolve':
        confirmed = not rows
    elif kind == 'working-set' and operation == 'commit':
        confirmed = bool(rows) and str(rows[0][1]) == str(
            plan.get('draft', {}).get('message'))
    elif kind == 'working-set' and operation in {
            'stage_tables', 'stage_all', 'unstage_tables'}:
        staged = {str(row[0]): bool(row[1]) for row in rows}
        if operation == 'stage_all':
            confirmed = all(staged.values())
        else:
            names = _table_names(
                plan.get('draft') or {}, required=True
            )
            confirmed = all(
                staged.get(name, False) == (operation == 'stage_tables')
                for name in names
            )
    elif kind == 'database' and operation == 'restore_backup':
        confirmed = bool(rows)
    return {
        'confirmed': confirmed,
        'reason': None if confirmed else (
            'provider_state_does_not_match_or_requires_manual_review'
        ),
        'observation': observation,
        'provider_finality_authority': True,
    }


ADMINISTRATION = DistributedSQLControlPlane(
    replace(_BASE_ADMINISTRATION.dialect), CONTROL_OPERATIONS,
    _compile_control_plane, inspector=_inspect_control_plane,
    canceller=_cancel_control_plane,
    post_validator=_post_validate_control_plane,
)


def _version(row):
    value = str(row[0] if row else '')
    match = re.search(r'(\d+\.\d+\.\d+)', value)
    if match is None:
        raise RelationalClientError('Dolt version is unavailable')
    return match.group(1)


def _extras(cursor, _request, generation):
    values = []
    tables = (
        ('branch', 'SELECT name, hash, latest_committer, latest_commit_date '
         'FROM dolt_branches ORDER BY name'),
        ('tag', 'SELECT tag_name, tag_hash, tagger, date '
         'FROM dolt_tags ORDER BY tag_name'),
        ('commit', 'SELECT commit_hash, committer, date, message '
         'FROM dolt_log ORDER BY date DESC LIMIT 200'),
        ('remote', 'SELECT name, url, fetch_specs FROM dolt_remotes '
         'ORDER BY name'),
        ('backup', 'SELECT name, url, params FROM dolt_backups '
         'ORDER BY name'),
    )
    for kind, source in tables:
        for row in optional_rows(cursor, source):
            values.append(resource(kind, [], row[0], generation, {
                'details': [None if item is None else str(item)
                            for item in row[1:]],
            }))
    for table_name, conflicts in optional_rows(
        cursor,
        'SELECT table_name, num_conflicts FROM dolt_conflicts '
        'ORDER BY table_name',
    ):
        values.append(resource('conflict', [], table_name, generation, {
            'count': int(conflicts),
        }))
    values.append(resource(
        'working-set', [], 'current', generation,
        {'provider_virtual_control_target': True},
    ))
    values.append(resource(
        'rebase', [], 'current', generation,
        {'provider_virtual_control_target': True},
    ))
    return values


class DoltProvider(ActualEnginePilotProvider):
    def __init__(self, context, permissions, client):
        super().__init__(context, permissions, client, PROFILE)


def create_provider(context, permissions, client=None):
    return DoltProvider(
        context,
        permissions,
        client or create_sql_client(
            PROFILE,
            permissions,
            wire='mysql',
            version_query='SELECT DOLT_VERSION()',
            version_parser=_version,
            metadata_reader=lambda connection, request: mysql_catalog(
                connection, request, 'Dolt', _extras
            ),
            administration=ADMINISTRATION,
        ),
    )
