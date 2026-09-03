#!/usr/bin/env python3
##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Verify typed Vitess tablet controls against an exact live topology."""

from __future__ import annotations

import argparse
import importlib
import json
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


class _Permissions:
    @staticmethod
    def require(_permission, _scope='endpoint'):
        return None

    @staticmethod
    def allows(_permission, _scope='endpoint'):
        return True

    @staticmethod
    def acquire_secret(*_args):
        raise RuntimeError('Vitess qualification has no credential')


def parser():
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument('--host', default='127.0.0.1')
    value.add_argument('--port', type=int, default=15306)
    value.add_argument('--http-port', type=int, default=15099)
    value.add_argument('--database', default='test_keyspace')
    value.add_argument('--tablet-alias', default='test-0000000102')
    value.add_argument('--vtctld-server', default='localhost:15999')
    value.add_argument('--vtctldclient-path', type=Path, required=True)
    value.add_argument('--allow-tablet-mutation', action='store_true')
    value.add_argument('--output', type=Path, required=True)
    return value


def _target(alias):
    return {
        'resource_id': f'tablet:{alias}',
        'resource_kind': 'tablet',
        'display_name': alias,
        'display_path': [alias],
        'authority_path': ['tablet', alias],
        'generation': 'vitess-tablet-control-live-gate',
    }


def _apply(provider, route, target, operation, draft):
    request = {
        'resource_kind': 'tablet',
        'operation_id': operation,
        'draft': draft,
        '_provider_route': route,
        'target_resource': target,
    }
    validation = provider.validate_visual_admin(request)
    if validation['valid'] is not True:
        raise RuntimeError(
            f'tablet.{operation} validation failed: '
            f'{validation["errors"]}'
        )
    plan = provider.plan_visual_admin(request)
    if plan['state'] != 'ready' or plan['execution_available'] is not True:
        raise RuntimeError(f'tablet.{operation} plan is not executable')
    result = provider.apply_visual_admin({
        'plan_id': plan['plan_id'],
        'plan_digest': plan['plan_digest'],
        'confirmed': True,
    })
    native = result['provider_result']
    response = native.get('provider_response') or {}
    if result['transaction_finality_interpreted_by_common_code']:
        raise RuntimeError('common visual code interpreted Vitess finality')
    if result.get('automatic_mutation_retry') is not False or response.get(
            'automatic_mutation_retry') is not False:
        raise RuntimeError('Vitess tablet mutation admitted retry')
    evidence = {
        'operation': f'tablet.{operation}',
        'operation_id': result['control_operation']['operation_id'],
        'provider_constructed': bool(
            plan.get('command_preview', {}).get('provider_constructed')
        ),
        'provider_response_observed': response.get(
            'provider_response_observed'
        ),
        'post_state_required': result.get('post_state_required'),
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
                f'tablet.{operation} post-state was not confirmed: '
                f'{post_state.get("reason")}'
            )
    else:
        observed = provider.refresh_visual_admin_operation({
            'operation_id': evidence['operation_id'],
        })
        evidence['observation_recorded'] = (
            observed.get('stage') == 'provider_observation_recorded'
        )
        if not evidence['observation_recorded']:
            raise RuntimeError(
                f'tablet.{operation} observation was not recorded'
            )
    return evidence


def verify(args):
    if not args.allow_tablet_mutation:
        raise RuntimeError(
            'live verification requires --allow-tablet-mutation'
        )
    executable = args.vtctldclient_path.expanduser().resolve()
    if not executable.is_file():
        raise RuntimeError('vtctldclient wrapper is unavailable')
    module = importlib.import_module(
        'pgadmin.cdeadmin.providers.vitess.provider'
    )
    context = SimpleNamespace(
        endpoint_id=f'vitess-tablet-control-{uuid.uuid4()}',
        session_namespace=f'vitess-tablet-session-{uuid.uuid4()}',
        mode='legacy_native', runtime_verification_state='verified',
        declared_runtime_family='vitess', verified_runtime_family='vitess',
    )
    provider = module.create_provider(context, _Permissions())
    route = {
        'route_id': 'vitess-tablet-control-live-gate',
        'host': args.host, 'port': args.port, 'user': 'root',
        'database': args.database,
        'vtgate_http_host': args.host,
        'vtgate_http_port': args.http_port,
        'vtgate_http_tls_mode': 'disable',
        'vtctldclient_path': str(executable),
        'vtctld_server': args.vtctld_server,
    }
    target = _target(args.tablet_alias)
    operations = []
    cleanup = []
    failures = []
    changed_type = False
    started = time.time()
    identity = provider.discover_endpoint({'route': route})[
        'verified_runtime'
    ]
    if identity['version'] != module.PROFILE.exact_version:
        raise RuntimeError('Vitess runtime version is not exact')
    try:
        for operation in ('ping', 'refresh_state', 'run_health_check'):
            operations.append(_apply(
                provider, route, target, operation, {}
            ))
        operations.append(_apply(
            provider, route, target, 'change_type', {
                'tablet_type': 'rdonly',
            }
        ))
        changed_type = True
        operations.append(_apply(
            provider, route, target, 'change_type', {
                'tablet_type': 'replica',
            }
        ))
        changed_type = False
    except Exception as exc:
        failures.append(f'{type(exc).__name__}: {exc}')
    finally:
        if changed_type:
            try:
                cleanup.append(_apply(
                    provider, route, target, 'change_type', {
                        'tablet_type': 'replica',
                    }
                ))
            except Exception as exc:
                failures.append(
                    f'tablet cleanup {type(exc).__name__}: {exc}'
                )
    evidence = {
        'schema': 'cdeadmin.vitess-tablet-control-live-verification.v1',
        'engine_id': 'vitess',
        'expected_runtime': module.PROFILE.exact_version,
        'verified_runtime': identity,
        'tablet_alias': args.tablet_alias,
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
