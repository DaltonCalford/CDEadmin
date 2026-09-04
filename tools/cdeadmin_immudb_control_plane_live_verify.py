#!/usr/bin/env python3
##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Qualify immudb control-plane administration on a disposable namespace."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from pathlib import Path
from types import ModuleType


ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / 'web'
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(WEB) not in sys.path:
    sys.path.insert(0, str(WEB))
if 'pgadmin' not in sys.modules:
    package = ModuleType('pgadmin')
    package.__path__ = [str(WEB / 'pgadmin')]
    sys.modules['pgadmin'] = package

from pgadmin.cdeadmin.providers.immudb.provider import (  # noqa: E402
    PROFILE,
    create_provider,
)
from tools.cdeadmin_relational_provider_live_verify import (  # noqa: E402
    _context,
    _merge_object_evidence,
    _object_operation_evidence,
)


class _Lease:
    def __init__(self, value):
        self.value = bytearray(value.encode('utf-8'))

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        for index in range(len(self.value)):
            self.value[index] = 0

    def use(self, callback):
        return callback(memoryview(self.value))


class _Permissions:
    def __init__(self, password):
        self.password = password

    @staticmethod
    def require(_permission, _scope='endpoint'):
        return None

    @staticmethod
    def allows(_permission, _scope='endpoint'):
        return True

    def acquire_secret(self, *_args):
        return _Lease(self.password)


def parser():
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument('--host', default='127.0.0.1')
    value.add_argument('--port', type=int, default=5432)
    value.add_argument('--web-port', type=int, default=8080)
    value.add_argument('--database', default='defaultdb')
    value.add_argument('--user', default='immudb')
    value.add_argument('--password-environment', required=True)
    value.add_argument('--output', type=Path)
    value.add_argument('--object-output', type=Path)
    value.add_argument(
        '--base-object-evidence', type=Path, action='append', default=[],
        help='Merge earlier exact-runtime object evidence into object output.',
    )
    return value


def _target(kind, name):
    return {
        'resource_id': f'{kind}:{name}',
        'resource_kind': kind,
        'display_name': name,
        'display_path': [name],
    }


def _admin(client, route, kind, operation, draft, target=None,
           validate_post_state=True):
    request = {
        'resource_kind': kind,
        'operation_id': operation,
        'draft': draft,
        '_provider_route': route,
    }
    if target is not None:
        request['target_resource'] = target
    checked = client.validate_admin_operation(request)
    if checked.get('errors'):
        raise RuntimeError(
            f'{kind}.{operation} failed validation: {checked["errors"]}'
        )
    plan = client.plan_admin_operation(request)
    handle = {'plan': request, 'provider_payload': plan['provider_payload']}
    result = client.apply_admin_operation(handle)
    evidence = {
        'operation': f'{kind}.{operation}',
        'preview': plan['command_preview'],
        'accepted': result.get('accepted'),
        'provider_finality_authority': result.get(
            'provider_finality_authority'
        ),
        'automatic_mutation_retry': result.get(
            'automatic_mutation_retry'
        ),
    }
    if validate_post_state:
        post_state = client.validate_admin_post_state(handle)
        evidence['post_state_confirmed'] = post_state.get('confirmed')
        if post_state.get('confirmed') is not True:
            raise RuntimeError(
                f'{kind}.{operation} post-state was not confirmed'
            )
    return evidence


def verify(args):
    password = os.environ.get(args.password_environment)
    if password is None:
        raise RuntimeError('immudb password environment variable is absent')
    context = _context(PROFILE)
    provider = create_provider(context, _Permissions(password))
    client = provider.client
    route = {
        'route_id': f'immudb-control-{uuid.uuid4()}',
        'host': args.host, 'port': args.port,
        'database': args.database, 'user': args.user,
        'web_host': args.host, 'web_port': args.web_port,
        'web_tls_mode': 'disable', 'web_timeout': 30,
        'credential_reference_id': 'immudb-control-secret',
        'principal_reference': 'cdeadmin-immudb-live-qualifier',
    }
    suffix = uuid.uuid4().hex[:10]
    database = f'cdeadmin_{suffix}'
    username = f'cdeuser_{suffix}'
    user_password = f'Cde-{suffix}-A9!'
    changed_password = f'Cde-{suffix}-B8!'
    database_target = _target('database', database)
    user_target = _target('user', username)
    server_target = _target('server', 'immudb')
    operations = []
    cleanup = []
    failures = []
    started = time.time()

    def run(kind, operation, draft, target=None, post_state=True,
            operation_route=None):
        try:
            operations.append(_admin(
                client, operation_route or route, kind, operation, draft,
                target, post_state
            ))
        except Exception as exc:
            failures.append(
                f'{kind}.{operation}: {type(exc).__name__}: {exc}'
            )

    def run_precondition(kind, operation, draft, expected, target=None):
        try:
            _admin(client, route, kind, operation, draft, target, False)
        except Exception as exc:
            if expected not in str(exc):
                failures.append(
                    f'{kind}.{operation}: {type(exc).__name__}: {exc}'
                )
                return
            operations.append({
                'operation': f'{kind}.{operation}',
                'accepted': False,
                'provider_precondition_confirmed': True,
                'provider_diagnostic': expected,
                'provider_finality_authority': True,
                'automatic_mutation_retry': False,
            })
        else:
            operations.append({
                'operation': f'{kind}.{operation}', 'accepted': True,
                'provider_precondition_confirmed': False,
                'provider_finality_authority': True,
                'automatic_mutation_retry': False,
            })

    try:
        identity = client.runtime_identity({'route': route})
        if identity['version'] != PROFILE.exact_version:
            raise RuntimeError('immudb exact runtime identity changed')
        run('server', 'health', {}, server_target)
        run('database', 'create', {
            'name': database, 'autoload': True,
            'max_concurrency': 4, 'read_tx_pool_size': 16,
            'index_flush_threshold': 1,
            'index_compaction_threshold': 1,
            'aht_sync_threshold': 32,
        })
        run('database', 'update_settings', {
            'max_io_concurrency': 2,
        }, database_target)
        run('user', 'create', {
            'name': username, 'password': user_password,
            'permission': 'read', 'database': database,
        })
        run('user', 'change_password', {
            'old_password': '', 'new_password': changed_password,
        }, user_target, post_state=False)
        run('permission', 'grant', {
            'username': username, 'database': database,
            'permission': 'readwrite',
        })
        run('permission', 'grant_sql', {
            'username': username, 'database': database,
            'privileges': ['SELECT', 'CREATE', 'INSERT', 'UPDATE',
                           'DELETE', 'DROP', 'ALTER'],
        })
        run('user', 'set_active', {'active': False}, user_target)
        run('user', 'set_active', {'active': True}, user_target)
        database_route = dict(route)
        database_route['database'] = database
        run('key', 'insert', {
            'key': 'compaction-qualification', 'key_encoding': 'utf8',
            'value': 'qualification', 'encoding': 'utf8',
        }, operation_route=database_route)
        run('database', 'flush_index', {
            'cleanup_percentage': 0, 'synced': True,
        }, database_target, post_state=False)
        run('database', 'unload', {}, database_target, post_state=False)
        run('database', 'load', {}, database_target)
        run('database', 'compact_index', {}, database_target,
            post_state=False)
        run_precondition(
            'database', 'truncate_history', {
                'retention_period_ms': 86400000,
            }, 'retention period has not been reached', database_target,
        )
        run('permission', 'revoke_sql', {
            'username': username, 'database': database,
            'privileges': ['SELECT', 'CREATE', 'INSERT', 'UPDATE',
                           'DELETE', 'DROP', 'ALTER'],
        })
        run('permission', 'revoke', {
            'username': username, 'database': database,
            'permission': 'readwrite',
        })
    finally:
        try:
            result = _admin(
                client, route, 'user', 'drop', {}, user_target,
                validate_post_state=True,
            )
            operations.append(result)
            cleanup.append({
                'resource': username,
                'confirmed_deactivated': result.get(
                    'post_state_confirmed'
                ),
            })
        except Exception as exc:
            failures.append(
                f'cleanup.user: {type(exc).__name__}: {exc}'
            )
        try:
            result = _admin(
                client, route, 'database', 'drop', {}, database_target,
                validate_post_state=True,
            )
            operations.append(result)
            cleanup.append({'resource': database, 'confirmed_absent': True})
        except Exception as exc:
            failures.append(
                f'cleanup.database: {type(exc).__name__}: {exc}'
            )
        passed_operations = {}
        for operation in operations:
            if not (
                operation.get('accepted') is True or
                operation.get('provider_precondition_confirmed') is True
            ):
                continue
            kind, operation_id = operation['operation'].split('.', 1)
            passed_operations.setdefault(kind, set()).add(operation_id)
        object_evidence = _object_operation_evidence(
            provider, passed_operations, 'immudb',
            scope='native-control-plane-operations',
        )
        object_evidence['conditionally_qualified_operations'] = [
            operation['operation'] for operation in operations
            if operation.get('provider_precondition_confirmed') is True
        ]
        provider.close()

    return {
        'schema': 'cdeadmin.immudb-control-plane-live.v1',
        'engine_id': 'immudb', 'expected_runtime': PROFILE.exact_version,
        'runtime_identity': identity,
        'disposable_database': database,
        'disposable_user': username,
        'operations': operations, 'cleanup': cleanup,
        'provider_finality_authority': True,
        'automatic_mutation_retry': False,
        'common_transaction_finality_interpretation': False,
        'object_experience_evidence': object_evidence,
        'started_at': started, 'completed_at': time.time(),
        'failures': failures, 'passed': not failures,
    }


def main():
    args = parser().parse_args()
    try:
        evidence = verify(args)
    except Exception as exc:
        evidence = {
            'schema': 'cdeadmin.immudb-control-plane-live.v1',
            'engine_id': 'immudb', 'passed': False,
            'failures': [f'setup: {type(exc).__name__}: {exc}'],
        }
    object_evidence = evidence.get('object_experience_evidence')
    if object_evidence and args.base_object_evidence:
        inputs = [
            json.loads(path.read_text(encoding='utf-8'))
            for path in args.base_object_evidence
        ]
        object_evidence = _merge_object_evidence(
            *inputs, object_evidence
        )
        conditional = evidence['object_experience_evidence'].get(
            'conditionally_qualified_operations', [])
        if conditional:
            object_evidence['conditionally_qualified_operations'] = (
                conditional
            )
        evidence['object_experience_evidence'] = object_evidence
    document = json.dumps(evidence, indent=2, sort_keys=True)
    print(document)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(document + '\n', encoding='utf-8')
    if args.object_output and object_evidence:
        args.object_output.parent.mkdir(parents=True, exist_ok=True)
        args.object_output.write_text(json.dumps(
            object_evidence, indent=2,
            sort_keys=True,
        ) + '\n', encoding='utf-8')
    return 0 if evidence['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
