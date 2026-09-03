#!/usr/bin/env python3
##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Verify Dolt control-plane administration against exact Dolt 1.86.6."""

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
    value.add_argument('--host', default='127.0.0.1')
    value.add_argument('--port', type=int, default=13306)
    value.add_argument('--user', default='root')
    value.add_argument('--password-environment')
    value.add_argument('--allow-mutation', action='store_true')
    value.add_argument('--output', type=Path, required=True)
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
        'generation': 'dolt-live-control-plane-gate',
    }


def _route(args, database=None):
    value = {
        'route_id': 'dolt-live-control-plane-gate',
        'host': args.host,
        'port': args.port,
        'user': args.user,
    }
    if database:
        value['database'] = database
    if args.password_environment:
        value.update({
            'credential_reference_id': 'dolt-live-control-plane-secret',
            'principal_reference': 'cdeadmin-live-qualifier',
        })
    return value


def _provider(password):
    module = importlib.import_module(
        'pgadmin.cdeadmin.providers.dolt.provider'
    )
    context = SimpleNamespace(
        endpoint_id=f'dolt-control-{uuid.uuid4()}',
        session_namespace=f'dolt-control-session-{uuid.uuid4()}',
        mode='legacy_native',
        runtime_verification_state='verified',
        declared_runtime_family='dolt',
        verified_runtime_family='dolt',
    )
    return module.create_provider(
        context, _Permissions(password)
    ), module.PROFILE


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
    if validation['valid'] is not True:
        raise RuntimeError(
            f'{kind}.{operation} validation failed: '
            f'{validation["errors"]}'
        )
    plan = provider.plan_visual_admin(request)
    if plan['state'] != 'ready' or plan['execution_available'] is not True:
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
    if result.get('automatic_mutation_retry') is not False:
        raise RuntimeError('common visual code admitted mutation retry')

    evidence = {
        'operation': f'{kind}.{operation}',
        'operation_id': result['control_operation']['operation_id'],
        'provider_constructed': bool(
            plan.get('command_preview', {}).get('provider_constructed')
        ),
        'post_state_required': bool(result.get('post_state_required')),
        'commit_requested': native.get('commit_requested'),
        'rollback_requested': native.get('rollback_requested'),
        'driver_observation_only': native.get('driver_observation_only'),
        'statement_count': len(native.get('statement_results', [])),
        'automatic_mutation_retry': False,
    }
    if result.get('post_state_required'):
        observed = provider.validate_visual_admin_post_state({
            'operation_id': evidence['operation_id'],
        })
        post_state = observed.get('post_state') or {}
        evidence['post_state_confirmed'] = post_state.get('confirmed')
        if post_state.get('confirmed') is not True:
            raise RuntimeError(
                f'{kind}.{operation} post-state was not confirmed: '
                f'{post_state.get("reason")}'
            )
    return evidence


def _row_ids(provider, route, target):
    page = provider.read_visual_admin_rows({
        '_provider_route': route,
        'target_resource': target,
        'limit': 100,
    })
    return sorted(row['values']['id'] for row in page['rows'])


def verify(args):
    if not args.allow_mutation:
        raise RuntimeError('live verification requires --allow-mutation')
    password = (
        os.environ.get(args.password_environment)
        if args.password_environment else None
    )
    provider, profile = _provider(password)
    base_route = _route(args)
    suffix = uuid.uuid4().hex[:12]
    database = f'cdeadmin_dolt_gate_{suffix}'
    table_name = f'gate_rows_{suffix}'
    clean_name = f'gate_clean_{suffix}'
    branch_name = f'gate_branch_{suffix}'
    route = _route(args, database)
    feature_route = _route(args, f'{database}/{branch_name}')
    database_target = _target('database', [database])
    table = _target('table', [database, table_name], table_name)
    feature_table = _target(
        'table', [f'{database}/{branch_name}', table_name], table_name
    )
    clean_table = _target('table', [database, clean_name], clean_name)
    branch = _target('branch', [branch_name])
    working_set = _target('working-set', ['current'])
    rebase = _target('rebase', ['current'])
    operations = []
    observations = []
    cleanup = []
    failures = []
    database_created = False
    branch_created = False
    started = time.time()

    identity = provider.discover_endpoint({'route': base_route})[
        'verified_runtime'
    ]
    if identity['version'] != profile.exact_version:
        raise RuntimeError(
            'runtime version is not the exact reference profile'
        )

    try:
        operations.append(_apply(
            provider, base_route, 'database', 'create', {'name': database}
        ))
        database_created = True
        operations.append(_apply(
            provider, route, 'table', 'create', {
                'name': table_name,
                'parent': database,
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
            }
        ))
        operations.append(_apply(
            provider, route, 'working-set', 'stage_all', {}, working_set
        ))
        operations.append(_apply(
            provider, route, 'working-set', 'commit', {
                'message': 'CDEadmin Dolt gate baseline',
                'stage_all': True,
                'allow_empty': False,
            }, working_set
        ))
        operations.append(_apply(
            provider, route, 'branch', 'create', {
                'name': branch_name,
            }
        ))
        branch_created = True
        operations.append(_apply(
            provider, feature_route, 'table', 'insert', {
                'values': {'id': 1, 'note': 'feature-row'},
            }, feature_table
        ))
        operations.append(_apply(
            provider, feature_route, 'working-set', 'stage_tables', {
                'table_names': [table_name], 'force_ignored': False,
            }, working_set
        ))
        operations.append(_apply(
            provider, feature_route, 'working-set', 'unstage_tables', {
                'table_names': [table_name],
            }, working_set
        ))
        operations.append(_apply(
            provider, feature_route, 'working-set', 'stage_tables', {
                'table_names': [table_name], 'force_ignored': False,
            }, working_set
        ))
        operations.append(_apply(
            provider, feature_route, 'working-set', 'commit', {
                'message': 'CDEadmin Dolt gate feature',
                'stage_all': False,
                'allow_empty': False,
            }, working_set
        ))
        operations.append(_apply(
            provider, route, 'table', 'insert', {
                'values': {'id': 2, 'note': 'main-row'},
            }, table
        ))
        operations.append(_apply(
            provider, route, 'working-set', 'stage_all', {}, working_set
        ))
        operations.append(_apply(
            provider, route, 'working-set', 'commit', {
                'message': 'CDEadmin Dolt gate main',
                'stage_all': True,
                'allow_empty': False,
            }, working_set
        ))
        operations.append(_apply(
            provider, feature_route, 'rebase', 'start', {
                'upstream': 'main',
                'interactive': False,
                'empty_commits': 'drop',
                'skip_verification': False,
            }, rebase
        ))
        ids_after_rebase = _row_ids(provider, feature_route, feature_table)
        observations.append({
            'observation': 'rebase-result-rows',
            'expected_ids': [1, 2],
            'observed_ids': ids_after_rebase,
            'confirmed': ids_after_rebase == [1, 2],
        })
        if ids_after_rebase != [1, 2]:
            raise RuntimeError('rebased branch did not contain both histories')

        operations.append(_apply(
            provider, feature_route, 'table', 'insert', {
                'values': {'id': 3, 'note': 'reset-me'},
            }, feature_table
        ))
        if _row_ids(provider, feature_route, feature_table) != [1, 2, 3]:
            raise RuntimeError('reset fixture row was not observed')
        operations.append(_apply(
            provider, feature_route, 'working-set', 'reset_hard', {},
            working_set
        ))
        ids_after_reset = _row_ids(
            provider, feature_route, feature_table
        )
        observations.append({
            'observation': 'hard-reset-result-rows',
            'expected_ids': [1, 2],
            'observed_ids': ids_after_reset,
            'confirmed': ids_after_reset == [1, 2],
        })
        if ids_after_reset != [1, 2]:
            raise RuntimeError('hard reset did not remove the fixture row')

        operations.append(_apply(
            provider, route, 'branch', 'set_default', {
                'persist': False,
            }, branch
        ))
        default_ids = _row_ids(provider, route, table)
        observations.append({
            'observation': 'default-branch-routing',
            'expected_ids': [1, 2],
            'observed_ids': default_ids,
            'confirmed': default_ids == [1, 2],
        })
        if default_ids != [1, 2]:
            raise RuntimeError('default branch route did not select feature')
        operations.append(_apply(
            provider, route, 'branch', 'set_default', {
                'persist': False,
            },
            _target('branch', ['main'])
        ))

        operations.append(_apply(
            provider, route, 'table', 'create', {
                'name': clean_name,
                'parent': database,
                'columns': [{
                    'name': 'id', 'type': 'INTEGER', 'nullable': False,
                    'primary_key': True,
                }],
                'constraints': [],
            }
        ))
        operations.append(_apply(
            provider, route, 'working-set', 'clean', {
                'table_names': [clean_name], 'include_ignored': False,
            }, working_set
        ))
        clean_removed = False
        try:
            provider.read_visual_admin_rows({
                '_provider_route': route,
                'target_resource': clean_table,
                'limit': 1,
            })
        except Exception:
            clean_removed = True
        observations.append({
            'observation': 'clean-untracked-table',
            'table': clean_name,
            'confirmed': clean_removed,
        })
        if not clean_removed:
            raise RuntimeError('clean did not remove the untracked table')
    except Exception as exc:
        failures.append(f'{type(exc).__name__}: {exc}')
    finally:
        if database_created:
            if branch_created:
                try:
                    cleanup.append(_apply(
                        provider, route, 'branch', 'drop', {'force': True},
                        branch
                    ))
                except Exception as exc:
                    failures.append(
                        f'branch removal cleanup {type(exc).__name__}: {exc}'
                    )
            try:
                cleanup.append(_apply(
                    provider, base_route, 'database', 'drop', {
                        'cascade': False, 'confirmation': database,
                    }, database_target
                ))
            except Exception as exc:
                failures.append(
                    f'database cleanup {type(exc).__name__}: {exc}'
                )

    evidence = {
        'schema': 'cdeadmin.dolt-control-plane-native-live-verification.v1',
        'engine_id': 'dolt',
        'expected_runtime': profile.exact_version,
        'verified_runtime': identity,
        'visual_administration_only': True,
        'raw_sql_supplied_by_gate': False,
        'automatic_mutation_retry': False,
        'common_transaction_finality_interpretation': False,
        'operations': operations,
        'observations': observations,
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
