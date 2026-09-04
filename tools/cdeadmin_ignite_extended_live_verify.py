#!/usr/bin/env python3
##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Qualify Ignite authenticated users, maintenance, and snapshots."""

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
if str(WEB) not in sys.path:
    sys.path.insert(0, str(WEB))
if 'pgadmin' not in sys.modules:
    package = ModuleType('pgadmin')
    package.__path__ = [str(WEB / 'pgadmin')]
    sys.modules['pgadmin'] = package

from pgadmin.cdeadmin.providers.apache_ignite.client import (  # noqa: E402
    IgniteBackend,
)
from pgadmin.cdeadmin.providers.apache_ignite.provider import (  # noqa: E402
    PROFILE,
)


class _Lease:
    def __init__(self, value):
        self.value = bytearray(value.encode('utf-8'))

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.value[:] = b'\x00' * len(self.value)

    def use(self, callback):
        return callback(memoryview(self.value))


class _Secrets:
    def __init__(self, values):
        self.values = values

    def acquire(self, reference, *_args):
        try:
            return _Lease(self.values[reference])
        except KeyError:
            raise RuntimeError('qualification secret is unavailable') from None


def parser():
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument('--host', default='127.0.0.1')
    value.add_argument('--port', type=int, required=True)
    value.add_argument('--rest-port', type=int, required=True)
    value.add_argument('--control-port', type=int, required=True)
    value.add_argument('--control-sh', type=Path, required=True)
    value.add_argument('--password-environment', required=True)
    value.add_argument('--allow-mutation', action='store_true')
    value.add_argument('--output', type=Path, required=True)
    return value


def _target(kind, *path):
    return {
        'resource_id': ':'.join((kind, *path)),
        'resource_kind': kind, 'display_name': path[-1],
        'display_path': list(path),
    }


def _admin(client, route, kind, operation, draft=None, target=None,
           post_state=True):
    plan_request = {
        'resource_kind': kind, 'operation_id': operation,
        'draft': draft or {}, '_provider_route': route,
    }
    if target is not None:
        plan_request['target_resource'] = target
    checked = client.validate_admin_operation(plan_request)
    if checked.get('errors'):
        raise RuntimeError(
            f'{kind}.{operation} validation failed: {checked["errors"]}')
    plan = client.plan_admin_operation(plan_request)
    handle = {
        'plan': plan_request, 'provider_payload': plan['provider_payload'],
    }
    result = client.apply_admin_operation(handle)
    record = {
        'resource_kind': kind, 'operation_id': operation,
        'accepted': result.get('accepted') is True,
        'provider_finality_authority': result.get(
            'provider_finality_authority', True),
        'automatic_mutation_retry': result.get(
            'automatic_mutation_retry', False),
    }
    if post_state:
        handle['provider_result'] = result
        observation = client.validate_admin_post_state(handle)
        record['post_state_confirmed'] = observation.get('confirmed') is True
        if not record['post_state_confirmed']:
            raise RuntimeError(
                f'{kind}.{operation} post-state was not confirmed')
    return record


def verify(args):
    if not args.allow_mutation:
        raise RuntimeError('qualification requires --allow-mutation')
    root_password = os.environ.get(args.password_environment)
    if root_password is None:
        raise RuntimeError('Ignite root password environment is absent')
    suffix = uuid.uuid4().hex[:10].upper()
    username = 'U' + suffix
    cache_name = 'C' + suffix
    snapshot_name = 'S' + suffix
    password_one = 'Cde-' + suffix + '-A9!'
    password_two = 'Cde-' + suffix + '-B8!'
    secrets = _Secrets({
        'root': root_password, 'user-one': password_one,
        'user-two': password_two,
    })
    client = IgniteBackend(secrets.acquire)
    route = {
        'route_id': f'ignite-extended-{uuid.uuid4()}',
        'host': args.host, 'port': args.port,
        'rest_port': args.rest_port, 'control_port': args.control_port,
        'control_sh_path': str(args.control_sh.resolve()),
        'auth_mode': 'username-password', 'username': 'ignite',
        'principal_reference': 'cdeadmin-ignite-extended-gate',
        'credential_references': {'database_password': 'root'},
    }
    cache = _target('cache', cache_name)
    user = _target('user', username)
    snapshot = _target('snapshot', snapshot_name)
    operations = []
    cleanup = []
    failures = []
    cache_present = False
    user_present = False
    identity = None
    started = time.time()

    def run(kind, operation, draft=None, target=None, post_state=True):
        record = _admin(
            client, route, kind, operation, draft, target, post_state)
        operations.append(record)
        return record

    try:
        identity = client.runtime_identity({'route': route})
        if identity.get('version') != PROFILE.exact_version:
            raise RuntimeError('Ignite exact runtime identity changed')
        run('user', 'create', {
            'name': username, 'password_reference': 'user-one',
        })
        user_present = True
        run('user', 'alter', {
            'password_reference': 'user-two',
        }, user)
        run('user', 'drop', {
            'password_reference': 'user-two', 'confirmation': username,
        }, user)
        user_present = False

        run('cache', 'create', {
            'name': cache_name, 'backups_number': 1,
        })
        cache_present = True
        run('cache', 'insert', {
            'key': 'snapshot-key', 'value': 'snapshot-value',
        }, cache)
        run('cache', 'validate_indexes', {}, cache, post_state=False)
        run('cache', 'idle_verify', {}, cache, post_state=False)
        run('cache', 'rebuild_indexes', {}, cache)
        run('cache', 'reset_lost_partitions', {}, cache)
        run('cache', 'clear', {'confirmation': cache_name}, cache)
        run('cache', 'insert', {
            'key': 'snapshot-key', 'value': 'snapshot-value',
        }, cache)

        run('snapshot', 'create', {
            'name': snapshot_name, 'synchronous': True,
        })
        run('snapshot', 'check', {}, snapshot, post_state=False)
        run('cache', 'drop', {'confirmation': cache_name}, cache)
        cache_present = False
        run('snapshot', 'restore', {
            'groups': [], 'synchronous': True,
        }, snapshot)
        cache_present = True
        handle = client.open_session({'route': route})
        restored = handle.get_cache(cache_name).get('snapshot-key')
        if restored != 'snapshot-value':
            raise RuntimeError('Ignite snapshot did not restore cache data')
        operations.append({
            'resource_kind': 'snapshot',
            'operation_id': 'restore-data-observation',
            'post_state_confirmed': True,
            'restored_value_matched': True,
        })
    except Exception as exc:
        failures.append(f'{type(exc).__name__}: {exc}')
    finally:
        if user_present:
            for reference in ('user-two', 'user-one'):
                try:
                    cleanup.append(_admin(
                        client, route, 'user', 'drop', {
                            'password_reference': reference,
                            'confirmation': username,
                        }, user))
                    user_present = False
                    break
                except Exception:
                    continue
            if user_present:
                failures.append('cleanup.user: user remains present')
        if cache_present:
            try:
                cleanup.append(_admin(
                    client, route, 'cache', 'drop', {
                        'confirmation': cache_name,
                    }, cache))
            except Exception as exc:
                failures.append(
                    f'cleanup.cache: {type(exc).__name__}: {exc}')
        client.close()

    evidence = {
        'schema': 'cdeadmin.apache-ignite-extended-live.v1',
        'engine_id': 'apache_ignite',
        'exact_profile': PROFILE.exact_version,
        'runtime_identity': identity,
        'operations': operations, 'cleanup': cleanup,
        'snapshot_artifact_scope': 'disposable-qualification-container',
        'provider_finality_authority': True,
        'automatic_mutation_retry': False,
        'common_transaction_finality_interpretation': False,
        'failures': failures,
        'status': 'passed' if not failures else 'failed',
        'started_at': started, 'completed_at': time.time(),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(evidence, indent=2, sort_keys=True) + '\n',
        encoding='utf-8')
    return evidence


def main():
    args = parser().parse_args()
    try:
        result = verify(args)
    except Exception as exc:
        print(f'{type(exc).__name__}: {exc}', file=sys.stderr)
        return 1
    print(json.dumps({
        'engine_id': result['engine_id'], 'status': result['status'],
        'output': str(args.output.resolve()),
    }, sort_keys=True))
    return 0 if result['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
