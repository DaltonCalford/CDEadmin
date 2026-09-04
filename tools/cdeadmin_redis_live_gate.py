#!/usr/bin/env python3
##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Qualify CDEadmin against Redis 8.6 or a newer stable deployment.

The gate exercises Redis through the provider client over RESP3. It does not
infer transaction finality, retry uncertain mutations, or interpret Redis
persistence as recovery authority for another engine.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import subprocess
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

from pgadmin.cdeadmin.providers.redis.client import (  # noqa: E402
    QUALIFIED_DRIVER_VERSION,
    RedisClient,
    RedisClientError,
    RedisUnknownOutcomeError,
)
from pgadmin.cdeadmin.providers.redis.provider import (  # noqa: E402
    PROFILE,
    create_provider,
)


REFERENCE_SERVER = '8.6.2'
CATEGORIES = (
    'dependency', 'runtime', 'resp3', 'topology', 'resources', 'strings',
    'hashes', 'lists', 'sets', 'sorted_sets', 'streams', 'geospatial',
    'bitmaps', 'hyperloglog', 'vector_sets', 'transactions', 'acl',
    'fault', 'tooling', 'visual_objects', 'cleanup',
)
OBJECT_RESOURCE_KINDS = (
    'key', 'string', 'hash', 'list', 'set', 'sorted-set', 'geospatial',
    'bitmap', 'hyperloglog', 'vector-set', 'ttl', 'stream',
    'pubsub-channel', 'consumer-group', 'consumer', 'module', 'acl-user',
    'replica', 'sentinel', 'cluster-slot',
)
FULL_OBJECT_OPERATIONS = {
    kind: sorted(RedisClient.ADMIN_OPERATIONS[kind])
    for kind in OBJECT_RESOURCE_KINDS
}
CONCEPT_BINDINGS = {
    'key_browsing': ('key',),
    'data_type_editing': (
        'string', 'hash', 'list', 'set', 'sorted-set', 'geospatial',
        'bitmap', 'hyperloglog', 'vector-set',
    ),
    'ttl_inspection': ('ttl',),
    'expiration_management': ('ttl',),
    'streams': ('stream',),
    'pubsub': ('pubsub-channel',),
    'consumer_groups': ('consumer-group', 'consumer'),
    'modules': ('module',),
    'acls': ('acl-user',),
    'replication': ('replica',),
    'sentinel_or_cluster_state': ('sentinel', 'cluster-slot'),
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
    def __init__(self, acquire_secret):
        self.acquire_secret = acquire_secret

    @staticmethod
    def require(_permission, _scope='endpoint'):
        return None

    @staticmethod
    def allows(_permission, _scope='endpoint'):
        return True


def parser():
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument('--host', default='127.0.0.1')
    value.add_argument('--port', type=int, default=16379)
    value.add_argument(
        '--topology-mode', choices=('standalone', 'sentinel', 'cluster'),
        default='standalone',
    )
    value.add_argument(
        '--object-scope',
        choices=('none', 'standalone', 'replica', 'sentinel', 'cluster'),
        default='none',
        help='visual object-operation scope exercised by this invocation',
    )
    value.add_argument(
        '--object-cluster-slot', type=int, default=0,
        help='disposable slot used by the cluster-slot object lifecycle',
    )
    value.add_argument('--replication-primary-host', default='127.0.0.1')
    value.add_argument('--replication-primary-port', type=int)
    value.add_argument('--contact-points', default='')
    value.add_argument('--sentinel-service')
    value.add_argument('--database', type=int, default=0)
    value.add_argument('--username')
    value.add_argument(
        '--password-environment', default='CDEADMIN_REDIS_PASSWORD'
    )
    value.add_argument(
        '--tls-mode', choices=('disabled', 'system-ca', 'self-signed'),
        default='disabled',
    )
    value.add_argument('--tls-ca-file')
    value.add_argument('--tls-certificate-file')
    value.add_argument('--tls-key-file')
    value.add_argument('--redis-cli', type=Path, required=True)
    value.add_argument('--workspace', type=Path, required=True)
    value.add_argument('--output', type=Path)
    value.add_argument(
        '--object-evidence', type=Path,
        help='write strict provider-object operation evidence separately',
    )
    value.add_argument(
        '--require-acl-admin', action='store_true',
        help='Fail unless temporary ACL user create/inspect/delete succeeds.',
    )
    value.add_argument(
        '--require-unknown-outcome', action='store_true',
        help=(
            'Prove a timed-out mutation is reported as unknown and is not '
            'automatically replayed (isolated standalone targets only).'
        ),
    )
    return value


def _record(categories, name, callback, details, failures):
    try:
        details[name] = callback()
        categories[name] = 'passed'
    except Exception as exc:
        categories[name] = 'failed'
        details[name] = {'error_type': type(exc).__name__}
        failures.append(f'{name}: {type(exc).__name__}: {exc}')


def _apply(provider, request):
    plan = provider.plan_visual_admin(request)
    if plan['state'] != 'ready':
        raise RuntimeError('Redis visual administration plan is not ready')
    result = provider.apply_visual_admin({
        'plan_id': plan['plan_id'],
        'plan_digest': plan['plan_digest'],
        'confirmed': True,
    })
    if result['transaction_finality_interpreted_by_common_code']:
        raise RuntimeError('common code interpreted Redis finality')
    if result['automatic_mutation_retry']:
        raise RuntimeError('common code enabled Redis mutation replay')
    return result


def _object_evidence(
    run_id, operations=None, operation_failures=None, object_scope=None,
):
    operations = {
        kind: sorted(set(values))
        for kind, values in (operations or {}).items()
    }
    concepts = {}
    family = {}
    for concept_id, resource_kinds in CONCEPT_BINDINGS.items():
        observed = {
            kind: operations[kind]
            for kind in resource_kinds if operations.get(kind)
        }
        if observed:
            family[concept_id] = {
                'status': 'passed', 'operations': observed,
            }
    if family:
        concepts['key_value'] = family
    missing = {
        kind: sorted(set(expected).difference(operations.get(kind, [])))
        for kind, expected in FULL_OBJECT_OPERATIONS.items()
        if set(expected).difference(operations.get(kind, []))
    }
    return {
        'schema': 'cdeadmin.provider-object-live-evidence.v1',
        'engine_id': 'redis', 'exact_profile': REFERENCE_SERVER,
        'run_id': run_id,
        'qualification_scope': object_scope,
        'evidence_scope': 'key-value-navigator-and-object-editor-operations',
        'raw_commands_used_for_provider_operations': False,
        'common_transaction_finality_interpreted': False,
        'automatic_mutation_retry': False,
        'passed_resource_operations': operations,
        'operation_failures': operation_failures or missing,
        'missing_resource_operations': missing,
        'concepts': concepts,
        'passed': not missing and not operation_failures,
    }


def _visual_objects(
    provider, native, route, prefix, object_scope='standalone',
    cluster_slot=0, replication_primary_host=None,
    replication_primary_port=None,
):
    passed = {}
    failures = {}

    def target(kind, name, **native_identity):
        return {
            'resource_id': f'redis:{kind}:{name}',
            'resource_kind': kind,
            'display_name': str(name),
            'extensions': {'redis': {'native': native_identity}},
        }

    def remember(kind, operation):
        passed.setdefault(kind, set()).add(operation)

    def apply(kind, operation, item, draft=None):
        try:
            if operation == 'inspect':
                inspection = copy.deepcopy(item)
                inspection['route'] = copy.deepcopy(route)
                provider.inspect_resource(inspection)
            result = _apply(provider, {
                'resource_kind': kind,
                'operation_id': operation,
                'target_resource': item,
                'draft': draft or {},
                '_provider_route': route,
            })
            remember(kind, operation)
            return result
        except Exception as exc:
            failures[f'{kind}.{operation}'] = {
                'error_type': type(exc).__name__, 'error': str(exc)[:1000],
            }
            raise RuntimeError(
                f'Redis visual operation {kind}.{operation} failed: {exc}'
            ) from exc

    if object_scope == 'replica':
        replica = target('replica', 'replication', role='replica')
        apply('replica', 'inspect', replica)
        apply('replica', 'alter', replica, {
            'changes': {'host': replication_primary_host,
                        'port': replication_primary_port},
        })
        apply('replica', 'execute', replica, {
            'action': 'promote', 'arguments': {},
        })
        return passed, failures

    if object_scope == 'sentinel':
        service = route['sentinel_service']
        sentinel = target('sentinel', service, service=service)
        apply('sentinel', 'inspect', sentinel)
        apply('sentinel', 'alter', sentinel, {
            'changes': {'down-after-milliseconds': 5000},
        })
        apply('sentinel', 'execute', sentinel, {
            'action': 'reset', 'arguments': {},
        })
        return passed, failures

    if object_scope == 'cluster':
        slot = int(cluster_slot)
        if slot < 0 or slot > 16383:
            raise RuntimeError('Redis object cluster slot is out of range')
        cluster = target(
            'cluster-slot', str(slot), slot=slot,
            host=route['host'], port=route['port'],
        )
        apply('cluster-slot', 'inspect', cluster)
        apply('cluster-slot', 'alter', cluster, {
            'changes': {'slot': slot, 'state': 'stable'},
        })
        apply('cluster-slot', 'execute', cluster, {
            'action': 'delete', 'arguments': {'slots': [slot]},
        })
        apply('cluster-slot', 'execute', cluster, {
            'action': 'add', 'arguments': {'slots': [slot]},
        })
        return passed, failures

    def lifecycle(
        kind, values, inserted, selector, changes, delete_selector=None,
    ):
        key = prefix + kind
        item = target(kind, key, key=key, data_type=kind)
        apply(kind, 'create', item, {
            'name': key, 'values': values, 'options': {},
        })
        apply(kind, 'inspect', item)
        apply(kind, 'alter', item, {
            'selector': {}, 'changes': {'ttl_ms': 300000},
        })
        renamed = key + ':renamed'
        apply(kind, 'rename', item, {'new_name': renamed})
        item = target(kind, renamed, key=renamed, data_type=kind)
        insert_draft = {'values': inserted, 'options': {}}
        if kind == 'string':
            insert_draft['name'] = renamed
        apply(kind, 'insert', item, insert_draft)
        apply(kind, 'update', item, {
            'selector': selector, 'changes': changes,
        })
        delete_draft = {'confirmation': renamed}
        if kind != 'string':
            delete_draft['selector'] = (
                selector if delete_selector is None else delete_selector
            )
        apply(kind, 'delete', item, delete_draft)
        if not native.exists(renamed):
            apply(kind, 'create', item, {
                'name': renamed, 'values': values, 'options': {},
            })
        apply(kind, 'drop', item, {'confirmation': renamed})

    # Generic key and string values.
    key_name = prefix + 'key'
    key_item = target('key', key_name, key=key_name, data_type='string')
    apply('key', 'insert', key_item, {
        'name': key_name, 'values': {'value': 'one'}, 'options': {},
    })
    apply('key', 'inspect', key_item)
    apply('key', 'update', key_item, {
        'selector': {'key': key_name}, 'changes': {'value': 'two'},
    })
    apply('key', 'delete', key_item, {'confirmation': key_name})

    lifecycle(
        'string', {'value': 'one'}, {'value': 'two'},
        {'key': 'value'}, {'value': 'three'},
    )
    lifecycle(
        'hash', {'field': 'one'}, {'second': 'two'},
        {'fields': ['field']}, {'field': 'updated'},
    )
    lifecycle(
        'list', {'values': ['one']}, {'values': ['two']},
        {'index': 0}, {'value': 'updated'},
        {'count': 1, 'value': 'updated'},
    )
    lifecycle('set', ['one'], ['two'], ['one'], ['three'])
    lifecycle(
        'sorted-set', [{'member': 'one', 'score': 1}],
        [{'member': 'two', 'score': 2}], ['one'],
        [{'member': 'one', 'score': 3}],
    )
    lifecycle(
        'geospatial', [{
            'member': 'toronto', 'longitude': -79.3832,
            'latitude': 43.6532,
        }], [{
            'member': 'ottawa', 'longitude': -75.6972,
            'latitude': 45.4215,
        }], ['toronto'], [{
            'member': 'toronto', 'longitude': -79.4,
            'latitude': 43.7,
        }],
    )
    lifecycle(
        'bitmap', {'offset': 0, 'value': 1},
        {'offset': 1, 'value': 1}, {'offset': 0},
        {'offset': 0, 'value': 0},
    )
    lifecycle(
        'vector-set', [{'element': 'one', 'vector': [1, 0, 0]}],
        [{'element': 'two', 'vector': [0, 1, 0]}], ['one'],
        [{'element': 'one', 'vector': [0, 0, 1]}],
    )

    hll_key = prefix + 'hyperloglog'
    hll = target(
        'hyperloglog', hll_key, key=hll_key, data_type='hyperloglog'
    )
    apply('hyperloglog', 'create', hll, {
        'name': hll_key, 'values': ['one'], 'options': {},
    })
    apply('hyperloglog', 'inspect', hll)
    apply('hyperloglog', 'alter', hll, {
        'selector': {}, 'changes': {'ttl_ms': 300000},
    })
    hll_renamed = hll_key + ':renamed'
    apply('hyperloglog', 'rename', hll, {'new_name': hll_renamed})
    hll = target(
        'hyperloglog', hll_renamed, key=hll_renamed,
        data_type='hyperloglog',
    )
    apply('hyperloglog', 'insert', hll, {
        'values': ['two'], 'options': {},
    })
    apply('hyperloglog', 'drop', hll, {'confirmation': hll_renamed})

    ttl_key = prefix + 'ttl'
    native.set(ttl_key, 'value')
    ttl = target('ttl', ttl_key, key=ttl_key, data_type='string')
    apply('ttl', 'create', ttl, {
        'milliseconds': 300000, 'condition': 'nx',
    })
    apply('ttl', 'inspect', ttl)
    apply('ttl', 'alter', ttl, {
        'milliseconds': 360000, 'condition': 'xx',
    })
    apply('ttl', 'drop', ttl, {'confirmation': ttl_key})

    stream_key = prefix + 'stream'
    stream = target('stream', stream_key, key=stream_key, data_type='stream')
    first = apply('stream', 'create', stream, {
        'name': stream_key,
        'values': {'id': '*', 'fields': {'field': 'one'}}, 'options': {},
    })['provider_result']['observations'][0]
    apply('stream', 'inspect', stream)
    apply('stream', 'alter', stream, {
        'selector': {}, 'changes': {'ttl_ms': 300000},
    })
    renamed_stream = stream_key + ':renamed'
    apply('stream', 'rename', stream, {'new_name': renamed_stream})
    stream = target(
        'stream', renamed_stream, key=renamed_stream, data_type='stream'
    )
    apply('stream', 'insert', stream, {
        'values': {'id': '*', 'fields': {'field': 'two'}}, 'options': {},
    })
    apply('stream', 'delete', stream, {
        'selector': [first], 'confirmation': renamed_stream,
    })

    group_name = 'cdeadmin_group_' + prefix.rsplit(':', 2)[-2]
    group = target(
        'consumer-group', group_name, stream=renamed_stream,
        group=group_name,
    )
    apply('consumer-group', 'create', group, {
        'stream': renamed_stream, 'name': group_name,
        'start_id': '0', 'make_stream': False,
    })
    apply('consumer-group', 'inspect', group)
    apply('consumer-group', 'alter', group, {'start_id': '0-0'})
    consumer_name = 'cdeadmin_consumer'
    native.execute_command(
        'XREADGROUP', 'GROUP', group_name, consumer_name,
        'COUNT', 1, 'STREAMS', renamed_stream, '>',
    )
    consumer = target(
        'consumer', consumer_name, stream=renamed_stream,
        group=group_name, consumer=consumer_name,
    )
    apply('consumer', 'inspect', consumer)
    apply('consumer', 'drop', consumer, {'confirmation': consumer_name})
    apply('consumer-group', 'drop', group, {'confirmation': group_name})
    apply('stream', 'drop', stream, {'confirmation': renamed_stream})

    channel = prefix + 'channel'
    channel_item = target('pubsub-channel', channel, channel=channel)
    apply('pubsub-channel', 'inspect', channel_item)
    apply('pubsub-channel', 'execute', channel_item, {
        'channel': channel, 'message': 'CDEadmin live qualification',
    })

    module = target('module', 'installed-modules', workspace=True)
    apply('module', 'inspect', module)

    acl_name = 'cdeadmin_live_' + prefix.rsplit(':', 2)[-2]
    acl = target('acl-user', acl_name, username=acl_name)
    apply('acl-user', 'create', acl, {
        'name': acl_name, 'rules': ['on', 'nopass', '+ping', '~*'],
    })
    apply('acl-user', 'inspect', acl)
    apply('acl-user', 'alter', acl, {'rules': ['resetkeys', '~*']})
    apply('acl-user', 'grant', acl, {'rules': ['+get']})
    apply('acl-user', 'revoke', acl, {'rules': ['-get']})
    apply('acl-user', 'drop', acl, {'confirmation': acl_name})

    replica = target('replica', 'replication', role='primary')
    apply('replica', 'inspect', replica)

    return passed, failures


def verify(args):
    import redis

    run_id = uuid.uuid4().hex
    # A literal cluster hash tag keeps the whole qualification corpus in one
    # slot, including the two commands inside MULTI/EXEC and cleanup DEL.
    prefix = f'cdeadmin:qualification:{{{run_id}}}:'
    password = os.environ.get(args.password_environment)
    secret_reference = 'redis-live-admin'

    def acquire(reference, _principal, _purpose, _kind):
        if reference != secret_reference or password is None:
            raise RedisClientError('qualification secret is unavailable')
        return _Lease(password)

    route = {
        'route_id': f'redis-live-{run_id}',
        'host': args.host, 'port': args.port,
        'topology_mode': args.topology_mode,
        'contact_points': [
            item.strip() for item in args.contact_points.split(',')
            if item.strip()
        ],
        'database': args.database,
        'tls_mode': args.tls_mode,
        'connect_timeout': 15, 'socket_timeout': 30,
        'client_name': 'CDEadmin-live-qualification',
        'tool_workspace': str(args.workspace.resolve()),
    }
    if args.sentinel_service:
        route['sentinel_service'] = args.sentinel_service
    if args.username:
        route['username'] = args.username
    if password is not None:
        route.update({
            'credential_reference_id': secret_reference,
            'principal_reference': 'cdeadmin-live-qualifier',
        })
    for key in ('tls_ca_file', 'tls_certificate_file', 'tls_key_file'):
        value = getattr(args, key)
        if value:
            route[key] = str(Path(value).resolve())

    categories = {name: 'not_run' for name in CATEGORIES}
    details = {}
    failures = []
    passed_visual_operations = {}
    visual_operation_failures = {}
    client = RedisClient(acquire)
    permissions = _Permissions(acquire)
    provider = create_provider(SimpleNamespace(
        endpoint_id=f'redis-live-{run_id}', mode='legacy_native',
        runtime_verification_state='verified',
        verified_runtime_family='redis', declared_runtime_family='redis',
        effective_permissions=frozenset({
            'network', 'secret_read', 'data_read', 'data_write',
            'administer', 'execute', 'filesystem',
        }),
    ), permissions, client)
    session = None
    args.workspace.resolve().mkdir(parents=True, exist_ok=True)
    try:
        def dependency():
            observed = getattr(redis, '__version__', '')
            if observed != QUALIFIED_DRIVER_VERSION:
                raise RuntimeError(
                    f'redis-py {observed!r} is not '
                    f'{QUALIFIED_DRIVER_VERSION}'
                )
            return {'driver': 'redis', 'version': observed}

        _record(categories, 'dependency', dependency, details, failures)
        try:
            session = client.open_session({'route': route})
        except Exception as exc:
            failures.append(f'connection: {type(exc).__name__}: {exc}')
            return _report(
                args, categories, details, failures, run_id,
                passed_visual_operations, visual_operation_failures,
            )

        def runtime():
            identity = provider.discover_endpoint({
                'route': route,
            })['verified_runtime']
            if not PROFILE.accepts_runtime_version(identity['version']):
                raise RuntimeError(
                    f'connected Redis is {identity["version"]}, expected '
                    f'{PROFILE.version_requirement}'
                )
            if identity['engine_id'] != 'redis':
                raise RuntimeError('runtime engine identity is not Redis')
            return identity

        _record(categories, 'runtime', runtime, details, failures)

        def resp3():
            token = client.execute(session, {'source': ['HELLO', 3]})
            result = client.describe_result(token)
            return result['payload']['entries'][0]

        _record(categories, 'resp3', resp3, details, failures)
        _record(categories, 'topology', lambda: client.describe_transaction(
            session
        ), details, failures)
        _record(categories, 'resources', lambda: {
            'count': len(client.list_resources({'route': route}))
        }, details, failures)

        native = session.client

        def strings():
            key = (prefix + 'string').encode()
            native.execute_command('SET', key, b'one')
            if native.execute_command('GET', key) != b'one':
                raise RuntimeError('string round trip failed')
            return {'set_get': True}

        def hashes():
            key = (prefix + 'hash').encode()
            native.execute_command('HSET', key, b'field', b'value')
            return {'value': client._json_value(
                native.execute_command('HGET', key, b'field')
            )}

        def lists():
            key = (prefix + 'list').encode()
            native.execute_command('RPUSH', key, b'one', b'two')
            return {'length': native.execute_command('LLEN', key)}

        def sets():
            key = (prefix + 'set').encode()
            native.execute_command('SADD', key, b'one', b'two')
            return {'cardinality': native.execute_command('SCARD', key)}

        def sorted_sets():
            key = (prefix + 'zset').encode()
            native.execute_command('ZADD', key, 1, b'one', 2, b'two')
            return {'cardinality': native.execute_command('ZCARD', key)}

        def streams():
            key = (prefix + 'stream').encode()
            entry = native.execute_command('XADD', key, '*', b'f', b'v')
            native.execute_command('XGROUP', 'CREATE', key, 'group', '0')
            return {'entry_id': client._json_value(entry)}

        def geospatial():
            key = (prefix + 'geo').encode()
            native.execute_command('GEOADD', key, -79.3832, 43.6532, 'toronto')
            return {'position': client._json_value(
                native.execute_command('GEOPOS', key, 'toronto')
            )}

        def bitmaps():
            key = (prefix + 'bitmap').encode()
            native.execute_command('SETBIT', key, 7, 1)
            return {'bit': native.execute_command('GETBIT', key, 7)}

        def hyperloglog():
            key = (prefix + 'hll').encode()
            native.execute_command('PFADD', key, 'one', 'two')
            return {'count': native.execute_command('PFCOUNT', key)}

        def vector_sets():
            key = (prefix + 'vectors').encode()
            native.execute_command('VADD', key, 'VALUES', 3, 1, 0, 0, 'one')
            embedding = native.execute_command('VEMB', key, 'one')
            if not embedding or len(embedding) != 3:
                raise RuntimeError('vector embedding round trip failed')
            native.execute_command('VSETATTR', key, 'one', '{"tag":"live"}')
            return {'dimension': native.execute_command('VDIM', key)}

        def transactions():
            pipeline = native.pipeline(transaction=True)
            pipeline.execute_command('SET', prefix + 'tx:a', 'one')
            pipeline.execute_command('SET', prefix + 'tx:b', 'two')
            values = pipeline.execute()
            state = client.describe_transaction(session)
            if state['common_finality_inference']:
                raise RuntimeError('common code inferred Redis finality')
            return {'exec_replies': client._json_value(values), 'state': state}

        native_data_gates = (
            ('strings', strings), ('hashes', hashes), ('lists', lists),
            ('sets', sets), ('sorted_sets', sorted_sets),
            ('streams', streams), ('geospatial', geospatial),
            ('bitmaps', bitmaps), ('hyperloglog', hyperloglog),
            ('vector_sets', vector_sets), ('transactions', transactions),
        )
        if args.object_scope == 'replica':
            for name, _callback in native_data_gates:
                categories[name] = 'passed'
                details[name] = {
                    'status': 'not_applicable',
                    'reason': (
                        'read-only replica object qualification does not '
                        'issue unrelated data mutations'
                    ),
                }
        else:
            for name, callback in native_data_gates:
                _record(categories, name, callback, details, failures)

        def acl():
            if not args.require_acl_admin:
                return {'status': 'not_required'}
            username = 'cdeadmin_live_' + run_id
            native.execute_command(
                'ACL', 'SETUSER', username, 'on', 'nopass', '+ping', '~*'
            )
            try:
                observed = native.execute_command('ACL', 'GETUSER', username)
                if observed is None:
                    raise RuntimeError('temporary ACL user was not visible')
            finally:
                native.execute_command('ACL', 'DELUSER', username)
            return {'temporary_user_lifecycle': True}

        _record(categories, 'acl', acl, details, failures)

        def fault():
            if not args.require_unknown_outcome:
                return {'status': 'not_required'}
            if args.topology_mode != 'standalone':
                raise RuntimeError(
                    'unknown-outcome injection requires an isolated '
                    'standalone target'
                )
            fault_route = dict(route)
            fault_route.update({
                'route_id': route['route_id'] + '-fault',
                'socket_timeout': 1,
                'health_check_interval': 0,
                'client_name': 'CDEadmin-live-fault',
            })
            fault_session = client.open_session({'route': fault_route})
            key = prefix + 'unknown-outcome-counter'
            try:
                native.execute_command('DEL', key)
                native.execute_command('CLIENT', 'PAUSE', 1500, 'ALL')
                try:
                    client.execute(
                        fault_session, {'source': ['INCR', key]}
                    )
                except RedisUnknownOutcomeError:
                    pass
                else:
                    raise RuntimeError(
                        'timed-out mutation was not classified as unknown'
                    )
                # The server, not CDEadmin, decides whether the already-sent
                # mutation applies.  Observing zero or one increment is a
                # valid unknown outcome; any larger value proves replay.
                time.sleep(0.75)
                raw_observed = native.execute_command('GET', key)
                observed = 0 if raw_observed is None else int(raw_observed)
                if observed not in {0, 1}:
                    raise RuntimeError(
                        'unknown-outcome mutation was replayed; '
                        f'observed counter {observed}'
                    )
                return {
                    'classification': 'unknown-no-automatic-replay',
                    'server_observed_execution_count': observed,
                }
            finally:
                fault_session.close()

        _record(categories, 'fault', fault, details, failures)

        def tooling():
            completed = subprocess.run(
                [str(args.redis_cli.resolve()), '--version'],
                check=True, capture_output=True, text=True, timeout=15,
            )
            output = (completed.stdout + completed.stderr).strip()
            match = next((
                item for item in output.split()
                if item and item[0].isdigit()
            ), '')
            if not PROFILE.accepts_runtime_version(match):
                raise RuntimeError(
                    'redis-cli does not satisfy '
                    f'{PROFILE.version_requirement}'
                )
            return {'redis_cli_version': output}

        _record(categories, 'tooling', tooling, details, failures)

        def visual_objects():
            observed, operation_failures = _visual_objects(
                provider, native, route, prefix,
                object_scope=args.object_scope,
                cluster_slot=args.object_cluster_slot,
                replication_primary_host=args.replication_primary_host,
                replication_primary_port=(
                    args.replication_primary_port or args.port - 1
                ),
            )
            passed_visual_operations.update(observed)
            visual_operation_failures.update(operation_failures)
            return {
                'resource_operation_count': sum(
                    len(values) for values in observed.values()
                ),
                'resource_kinds': sorted(observed),
                'raw_commands_used_for_provider_operations': False,
                'common_finality_interpreted': False,
                'automatic_mutation_retry': False,
            }

        if args.object_scope == 'none':
            categories['visual_objects'] = 'passed'
            details['visual_objects'] = {
                'status': 'not_required',
                'reason': 'no visual object qualification scope requested',
            }
        else:
            _record(
                categories, 'visual_objects', visual_objects,
                details, failures,
            )
    finally:
        if session is not None:
            try:
                # Every qualification key shares one Redis Cluster hash slot,
                # so the cluster-aware iterator and the final DEL are bounded
                # and cannot produce a cross-slot request.
                keys = list(session.client.scan_iter(
                    match=prefix + '*', count=500
                ))
                if keys:
                    session.client.delete(*keys)
                categories['cleanup'] = 'passed'
                details['cleanup'] = {'keys_removed': len(keys)}
            except Exception as exc:
                categories['cleanup'] = 'failed'
                failures.append(f'cleanup: {type(exc).__name__}: {exc}')
            session.close()
        provider.close()
    return _report(
        args, categories, details, failures, run_id,
        passed_visual_operations, visual_operation_failures,
    )


def _report(
    args, categories, details, failures, run_id,
    passed_visual_operations, visual_operation_failures,
):
    return {
        'schema': 'cdeadmin.redis-live-gate.v1',
        'provider_id': PROFILE.provider_id,
        'profile_id': PROFILE.profile_id,
        'reference_server': REFERENCE_SERVER,
        'server_version_requirement': PROFILE.version_requirement,
        'expected_driver': QUALIFIED_DRIVER_VERSION,
        'topology_mode': args.topology_mode,
        'tls_mode': args.tls_mode,
        'categories': categories,
        'details': details,
        'failures': failures,
        'passed': not failures and all(
            value == 'passed' for value in categories.values()
        ),
        'automatic_mutation_retry': False,
        'common_transaction_finality_inference': False,
        'object_evidence': _object_evidence(
            run_id, passed_visual_operations, visual_operation_failures,
            args.object_scope,
        ),
    }


def main():
    args = parser().parse_args()
    report = verify(args)
    output = json.dumps(report, indent=2, sort_keys=True) + '\n'
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output, encoding='utf-8')
    if args.object_evidence:
        args.object_evidence.parent.mkdir(parents=True, exist_ok=True)
        args.object_evidence.write_text(
            json.dumps(
                report['object_evidence'], indent=2, sort_keys=True
            ) + '\n',
            encoding='utf-8',
        )
    sys.stdout.write(output)
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
