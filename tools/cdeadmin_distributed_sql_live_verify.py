#!/usr/bin/env python3
##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Verify provider-driven visual administration on distributed SQL."""

from __future__ import annotations

import argparse
import importlib
import json
import os
import sys
import time
import uuid
from pathlib import Path
from types import ModuleType, SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / 'web'
if str(WEB) not in sys.path:
    sys.path.insert(0, str(WEB))
if 'pgadmin' not in sys.modules:
    package = ModuleType('pgadmin')
    package.__path__ = [str(WEB / 'pgadmin')]
    sys.modules['pgadmin'] = package


ENGINES = {
    'cockroachdb': {
        'port': 26257, 'database': 'defaultdb', 'user': 'root',
        'parent': 'public', 'path': lambda database, table: [
            'public', table,
        ],
    },
    'dolt': {
        'port': 3306, 'database': None, 'user': 'root',
        'parent': None, 'path': lambda database, table: [database, table],
    },
    'tidb': {
        'port': 4000, 'database': 'test', 'user': 'root',
        'parent': None, 'path': lambda database, table: [database, table],
    },
    'vitess': {
        'port': 15306, 'database': 'test_keyspace', 'user': 'root',
        'parent': None, 'path': lambda database, table: [database, table],
    },
    'yugabytedb': {
        'port': 5433, 'database': 'yugabyte', 'user': 'yugabyte',
        'parent': 'public', 'path': lambda database, table: [
            'public', table,
        ],
    },
}


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
        if self.password is None:
            raise RuntimeError('qualification password is unavailable')
        return _Lease(self.password)


def parser():
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument('engine', choices=tuple(ENGINES))
    value.add_argument('--host', default='127.0.0.1')
    value.add_argument('--port', type=int)
    value.add_argument('--database')
    value.add_argument('--user')
    value.add_argument('--password-environment')
    value.add_argument('--sslmode')
    value.add_argument('--vtgate-http-host')
    value.add_argument('--vtgate-http-port', type=int, default=15001)
    value.add_argument('--vtgate-http-tls-mode', default='disable')
    value.add_argument('--allow-mutation', action='store_true')
    value.add_argument('--output', type=Path, required=True)
    return value


def _provider(engine, permissions):
    module = importlib.import_module(
        f'pgadmin.cdeadmin.providers.{engine}.provider'
    )
    context = SimpleNamespace(
        endpoint_id=f'{engine}-mutation-{uuid.uuid4()}',
        session_namespace=f'{engine}-mutation-session-{uuid.uuid4()}',
        mode='legacy_native',
        runtime_verification_state='verified',
        declared_runtime_family=engine,
        verified_runtime_family=engine,
    )
    return module.create_provider(context, permissions), module.PROFILE


def _route(args, database):
    specification = ENGINES[args.engine]
    value = {
        'route_id': f'{args.engine}-mutation-gate',
        'host': args.host,
        'port': args.port or specification['port'],
        'user': args.user or specification['user'],
    }
    if database:
        value['database'] = database
    if args.sslmode:
        value['sslmode'] = args.sslmode
    if args.password_environment:
        value.update({
            'credential_reference_id': f'{args.engine}-mutation-secret',
            'principal_reference': 'cdeadmin-live-qualifier',
        })
    if args.engine == 'vitess':
        value.update({
            'vtgate_http_host': args.vtgate_http_host or args.host,
            'vtgate_http_port': args.vtgate_http_port,
            'vtgate_http_tls_mode': args.vtgate_http_tls_mode,
        })
    return value


def _target(kind, path, name=None):
    display_path = list(path)
    display_name = name or display_path[-1]
    return {
        'resource_id': ':'.join([kind, *display_path]),
        'resource_kind': kind,
        'display_name': display_name,
        'display_path': display_path,
        'authority_path': [*display_path[:-1], kind, display_path[-1]],
        'generation': 'live-mutation-gate',
    }


def _apply(provider, route, kind, operation, draft, target=None):
    request = {
        'resource_kind': kind,
        'operation_id': operation,
        'draft': draft,
        '_provider_route': route,
    }
    if target is not None:
        request['target_resource'] = target
    validation = provider.validate_visual_admin(request)
    if not validation['valid']:
        raise RuntimeError(
            f'{kind}.{operation} validation failed: '
            f'{validation["errors"]}'
        )
    plan = provider.plan_visual_admin(request)
    if plan['state'] != 'ready' or not plan['execution_available']:
        raise RuntimeError(f'{kind}.{operation} plan is not executable')
    result = provider.apply_visual_admin({
        'plan_id': plan['plan_id'],
        'plan_digest': plan['plan_digest'],
        'confirmed': True,
    })
    native = result['provider_result']
    if result['transaction_finality_interpreted_by_common_code']:
        raise RuntimeError('common visual code interpreted finality')
    if native.get('transaction_finality_interpreted_by_common_code'):
        raise RuntimeError('common relational code interpreted finality')
    return {
        'operation': f'{kind}.{operation}',
        'provider_constructed': bool(
            plan.get('command_preview', {}).get('provider_constructed')
        ),
        'commit_requested': native.get('commit_requested'),
        'rollback_requested': native.get('rollback_requested'),
        'driver_observation_only': native.get('driver_observation_only'),
        'statement_count': len(native.get('statement_results', [])),
    }


def verify(args):
    if not args.allow_mutation:
        raise RuntimeError(
            'live mutation verification requires --allow-mutation'
        )
    password = (
        os.environ.get(args.password_environment)
        if args.password_environment else None
    )
    permissions = _Permissions(password)
    provider, profile = _provider(args.engine, permissions)
    specification = ENGINES[args.engine]
    database = args.database or specification['database']
    base_route = _route(args, None)
    route = _route(args, database)
    suffix = uuid.uuid4().hex[:12]
    table_name = f'cdeadmin_gate_{suffix}'
    disposable_database = None
    operations = []
    cleanup = []
    failures = []
    started = time.time()

    identity = provider.discover_endpoint({'route': base_route})[
        'verified_runtime'
    ]
    if identity['version'] != profile.exact_version:
        raise RuntimeError(
            'runtime version is not the exact reference profile'
        )

    if args.engine == 'dolt' and database is None:
        disposable_database = f'cdeadmin_gate_{suffix}'
        operations.append(_apply(
            provider, base_route, 'database', 'create',
            {'name': disposable_database},
        ))
        database = disposable_database
        route = _route(args, database)

    parent = specification['parent'] or database
    path = specification['path'](database, table_name)
    table = _target('table', path, table_name)
    table_created = False
    try:
        operations.append(_apply(
            provider, route, 'table', 'create', {
                'name': table_name,
                'parent': parent,
                'columns': [
                    {
                        'name': 'id', 'type': 'INTEGER', 'nullable': False,
                        'primary_key': True,
                    },
                    {
                        'name': 'note', 'type': 'VARCHAR(255)',
                        'nullable': False,
                    },
                ],
                'constraints': [],
                **({
                    'register_in_vschema': True,
                    'vindex_name': 'xxhash',
                    'vindex_columns': ['id'],
                } if args.engine == 'vitess' else {}),
            },
        ))
        table_created = True
        if args.engine == 'vitess':
            readiness_started = time.monotonic()
            readiness_attempts = 0
            last_error = None
            for _attempt in range(75):
                readiness_attempts += 1
                try:
                    provider.read_visual_admin_rows({
                        '_provider_route': route,
                        'target_resource': table,
                        'limit': 1,
                    })
                    last_error = None
                    break
                except Exception as exc:
                    last_error = exc
                    time.sleep(1)
            if last_error is not None:
                raise RuntimeError(
                    'Vitess did not publish the created table to VTGate'
                ) from last_error
            operations.append({
                'operation': 'table.readiness-observation',
                'read_only_poll': True,
                'mutation_retried': False,
                'attempts': readiness_attempts,
                'duration_seconds': round(
                    time.monotonic() - readiness_started, 3
                ),
            })
        operations.append(_apply(
            provider, route, 'table', 'insert',
            {'values': {'id': 1, 'note': 'provider-created'}}, table,
        ))
        page = provider.read_visual_admin_rows({
            '_provider_route': route,
            'target_resource': table,
            'limit': 20,
        })
        row = next(item for item in page['rows'] if item['values']['id'] == 1)
        token = row['identity_token']
        if not page['editable'] or not token:
            raise RuntimeError(
                'provider did not issue an editable row identity'
            )
        operations.append(_apply(
            provider, route, 'table', 'update', {
                'selector': {'identity_token': token},
                'changes': {'note': 'provider-updated'},
            }, table,
        ))
        updated_page = provider.read_visual_admin_rows({
            '_provider_route': route,
            'target_resource': table,
            'limit': 20,
        })
        updated = next(
            item for item in updated_page['rows']
            if item['values']['id'] == 1
        )
        if updated['values']['note'] != 'provider-updated':
            raise RuntimeError('provider row update was not observed')
        operations.append(_apply(
            provider, route, 'table', 'delete', {
                'selector': {'identity_token': updated['identity_token']},
                'confirmation': table_name,
            }, table,
        ))
        empty_page = provider.read_visual_admin_rows({
            '_provider_route': route,
            'target_resource': table,
            'limit': 20,
        })
        if empty_page['rows']:
            raise RuntimeError('provider row delete was not observed')
    except Exception as exc:
        failures.append(f'{type(exc).__name__}: {exc}')
    finally:
        if table_created:
            try:
                cleanup.append(_apply(
                    provider, route, 'table', 'drop',
                    {
                        'cascade': False,
                        'confirmation': table_name,
                        **({
                            'drop_vschema_registration': True,
                        } if args.engine == 'vitess' else {}),
                    }, table,
                ))
            except Exception as exc:
                failures.append(
                    f'table cleanup {type(exc).__name__}: {exc}'
                )
        if disposable_database:
            try:
                cleanup.append(_apply(
                    provider, base_route, 'database', 'drop',
                    {
                        'cascade': False,
                        'confirmation': disposable_database,
                    },
                    _target('database', [disposable_database]),
                ))
            except Exception as exc:
                failures.append(
                    f'database cleanup {type(exc).__name__}: {exc}'
                )

    evidence = {
        'schema': 'cdeadmin.distributed-sql-native-live-verification.v1',
        'engine_id': args.engine,
        'expected_runtime': profile.exact_version,
        'verified_runtime': identity,
        'visual_administration_only': True,
        'raw_sql_supplied_by_gate': False,
        'automatic_mutation_retry': False,
        'common_transaction_finality_interpretation': False,
        'operations': operations,
        'cleanup': cleanup,
        'failures': failures,
        'status': 'passed' if not failures else 'failed',
        'duration_seconds': round(time.time() - started, 3),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(evidence, indent=2, sort_keys=True) + '\n',
        encoding='utf-8',
    )
    return evidence


def main():
    args = parser().parse_args()
    try:
        evidence = verify(args)
    except Exception as exc:
        print(f'{type(exc).__name__}: {exc}', file=sys.stderr)
        return 1
    print(json.dumps({
        'engine_id': evidence['engine_id'],
        'status': evidence['status'],
        'output': str(args.output.resolve()),
    }, sort_keys=True))
    return 0 if evidence['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
