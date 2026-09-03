##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Redis 8.6.2 RESP3 provider and visual administration tests."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace


ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / 'web'
if str(WEB) not in sys.path:
    sys.path.insert(0, str(WEB))
if 'pgadmin' not in sys.modules:
    package = ModuleType('pgadmin')
    package.__path__ = [str(WEB / 'pgadmin')]
    sys.modules['pgadmin'] = package

from pgadmin.cdeadmin.providers.redis.client import (  # noqa: E402
    RedisClient,
    RedisClientError,
    RedisDependencyError,
    RedisUnknownOutcomeError,
)
from pgadmin.cdeadmin.providers.redis.provider import PROFILE  # noqa: E402


class TimeoutError_(Exception):
    pass


class ConnectionError_(Exception):
    pass


class WatchError_(Exception):
    pass


class ClusterNode:
    def __init__(self, host, port):
        self.host = host
        self.port = port


class NoBackoff:
    pass


class Retry:
    def __init__(self, backoff, retries):
        self.backoff = backoff
        self.retries = retries


class SecretLease:
    def __init__(self, value=b'correct-horse'):
        self.value = bytearray(value)
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.closed = True
        for index in range(len(self.value)):
            self.value[index] = 0

    def use(self, callback):
        return callback(memoryview(self.value))


class Pipeline:
    def __init__(self, client, transaction):
        self.client = client
        self.transaction = transaction
        self.commands = []
        self.watched = []

    def watch(self, *keys):
        self.watched.extend(keys)

    def multi(self):
        return None

    def execute_command(self, *command):
        self.commands.append(command)
        return b'QUEUED'

    def execute(self):
        return [self.client.execute_command(*item) for item in self.commands]

    def reset(self):
        return None

    def __getattr__(self, name):
        return getattr(self.client, name)


class FakeRedis:
    def __init__(self, factory, **options):
        self.factory = factory
        self.options = options
        self.closed = False
        self.fail = None
        self.values = {b'alpha': b'one'}
        self.hashes = {b'hash': {b'field': b'before'}}
        self.lists = {b'list': [b'first', b'second']}
        self.sets = {b'set': {b'member'}}
        self.zsets = {b'zset': {b'member': 2.5}}
        self.streams = {b'stream': [(b'1-0', {b'f': b'v'})]}
        self.vectors = {b'vectors': {b'item': [b'1', b'0']}}
        self.commands = []

    def close(self):
        self.closed = True

    def ping(self):
        return True

    def get_default_node(self):
        return object()

    def get_redis_connection(self, _node):
        return self

    def info(self, section):
        values = {
            'server': {
                'redis_version': '8.6.2', 'run_id': 'redis-run',
                'redis_mode': 'standalone', 'arch_bits': 64,
            },
            'replication': {'role': 'master', 'connected_slaves': 0},
            'persistence': {'aof_enabled': 1},
            'memory': {'used_memory': 42},
            'stats': {'total_commands_processed': 10},
            'clients': {'connected_clients': 1},
        }
        return values.get(section, {})

    def execute_command(self, *command):
        self.commands.append(command)
        if self.fail is not None:
            raise self.fail
        name = command[0]
        if isinstance(name, bytes):
            name = name.decode('ascii')
        name = name.upper()
        if name == 'HELLO':
            return {b'proto': 3, b'version': b'8.6.2'}
        if name == 'GET':
            return self.get(command[1])
        if name == 'SET':
            self.values[command[1]] = command[2]
            return b'OK'
        if name == 'HSET':
            self.hashes.setdefault(command[1], {})[command[2]] = command[3]
            return 1
        if name == 'SREM':
            return int(command[2] in self.sets.get(command[1], set()))
        if name == 'SADD':
            self.sets.setdefault(command[1], set()).add(command[2])
            return 1
        if name == 'VEMB':
            return self.vectors.get(command[1], {}).get(command[2])
        if name == 'VISMEMBER':
            return int(command[2] in self.vectors.get(command[1], {}))
        if name == 'VRANGE':
            return list(self.vectors.get(command[1], {}))
        return b'OK'

    def pipeline(self, transaction=True):
        return Pipeline(self, transaction)

    def scan(self, cursor=0, count=100):
        keys = list(self.values) + list(self.hashes) + list(self.lists)
        keys += list(self.sets) + list(self.zsets) + list(self.streams)
        keys += list(self.vectors)
        return 0, keys[:count]

    def type(self, key):
        if key in self.hashes:
            return b'hash'
        if key in self.lists:
            return b'list'
        if key in self.sets:
            return b'set'
        if key in self.zsets:
            return b'zset'
        if key in self.streams:
            return b'stream'
        if key in self.vectors:
            return b'vectorset'
        return b'string'

    def get(self, key):
        return self.values.get(key)

    def hscan(self, key, cursor=0, count=100):
        return 0, self.hashes.get(key, {})

    def hget(self, key, field):
        return self.hashes.get(key, {}).get(field)

    def sscan(self, key, cursor=0, count=100):
        return 0, list(self.sets.get(key, set()))

    def sismember(self, key, member):
        return member in self.sets.get(key, set())

    def zscan(self, key, cursor=0, count=100):
        return 0, list(self.zsets.get(key, {}).items())

    def zscore(self, key, member):
        return self.zsets.get(key, {}).get(member)

    def lrange(self, key, start, end):
        return self.lists.get(key, [])[start:end + 1]

    def lindex(self, key, index):
        try:
            return self.lists.get(key, [])[index]
        except IndexError:
            return None

    def xrange(self, key, min='-', max='+', count=100):
        return self.streams.get(key, [])[:count]

    def pttl(self, _key):
        return -1

    def memory_usage(self, _key):
        return 10


class Factory:
    def __init__(self):
        self.clients = []
        self.cluster_options = []

    def redis(self, **options):
        value = FakeRedis(self, **options)
        self.clients.append(value)
        return value

    def cluster(self, **options):
        self.cluster_options.append(options)
        return self.redis(**options)


class Sentinel:
    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs
        self.closed = False

    def master_for(self, _service, **options):
        return FakeRedis(None, **options)

    def close(self):
        self.closed = True


def fake_module(version='6.4.0'):
    factory = Factory()
    return SimpleNamespace(
        __version__=version,
        Redis=factory.redis,
        RedisCluster=factory.cluster,
        ClusterNode=ClusterNode,
        Sentinel=Sentinel,
        TimeoutError=TimeoutError_,
        ConnectionError=ConnectionError_,
        WatchError=WatchError_,
        retry=SimpleNamespace(Retry=Retry),
        backoff=SimpleNamespace(NoBackoff=NoBackoff),
    ), factory


def route(**changes):
    value = {
        'host': '127.0.0.1', 'port': 6379, 'database': 0,
        'topology_mode': 'standalone', 'tls_mode': 'disabled',
    }
    value.update(changes)
    return value


def target(kind, key):
    return {
        'resource_kind': kind,
        'extensions': {'redis': {'native': {
            'key': key, 'data_type': kind,
        }}},
    }


class RedisProviderTestCase(unittest.TestCase):
    def setUp(self):
        module, self.factory = fake_module()
        self.client = RedisClient(module=module)

    def tearDown(self):
        self.client.close()

    def test_profile_baseline_and_qualified_manifest_are_active(self):
        self.assertEqual('8.6.2', PROFILE.exact_version)
        self.assertEqual('data-structure-key-value', PROFILE.model_family)
        manifest = json.loads((
            WEB / 'pgadmin/cdeadmin/providers/redis/provider_manifest.json'
        ).read_text(encoding='utf-8'))
        self.assertTrue(manifest['enabled'])
        self.assertEqual('experimental', manifest['support_state'])

    def test_dependency_version_and_surface_are_qualified(self):
        module, _factory = fake_module('6.3.0')
        with self.assertRaises(RedisDependencyError):
            RedisClient(module=module)
        module, _factory = fake_module()
        module.ClusterNode = None
        with self.assertRaises(RedisDependencyError):
            RedisClient(module=module)

    def test_standalone_route_uses_resp3_and_binary_replies(self):
        session = self.client.open_session({'route': route()})
        self.assertEqual(3, session.client.options['protocol'])
        self.assertFalse(session.client.options['retry_on_timeout'])
        self.assertEqual(0, session.client.options['retry'].retries)
        token = self.client.execute(session, {'source': ['GET', 'alpha']})
        result = self.client.describe_result(token)
        self.assertEqual('one', result['payload']['entries'][0]['value'])
        identity = self.client.runtime_identity({}, handle=session)
        self.assertEqual('8.6.2', identity['version'])
        self.assertEqual(3, identity['native']['protocol_version'])

    def test_cluster_uses_cluster_node_objects_and_database_zero(self):
        session = self.client.open_session({'route': route(
            topology_mode='cluster', contact_points=['redis-two:6380']
        )})
        nodes = self.factory.cluster_options[-1]['startup_nodes']
        self.assertTrue(all(isinstance(item, ClusterNode) for item in nodes))
        self.assertNotIn('db', self.factory.cluster_options[-1])
        session.close()
        with self.assertRaises(RedisClientError):
            self.client.open_session({'route': route(
                topology_mode='cluster', database=2
            )})
        with self.assertRaisesRegex(RedisClientError, 'client_name'):
            self.client.open_session({'route': route(
                client_name='invalid client name'
            )})

    def test_acl_secret_and_cluster_lifecycle_controls_are_forwarded(self):
        acquired = []

        def acquire(_reference, _principal, _purpose, kind):
            lease = SecretLease()
            acquired.append((kind, lease))
            return lease

        module, factory = fake_module()
        adapter = RedisClient(acquire, module)
        session = adapter.open_session({'route': route(
            topology_mode='cluster', auth_mode='acl', username='operator',
            credential_references={'database_password': 'credential-one'},
            principal_reference='principal-one',
            cluster_require_full_coverage=False,
            cluster_dynamic_startup_nodes=False,
            cluster_reinitialize_steps=7,
            cluster_error_retry_attempts=4,
            max_connections=250,
        )})
        options = factory.cluster_options[-1]
        self.assertEqual('operator', options['username'])
        self.assertEqual('correct-horse', options['password'])
        self.assertFalse(options['require_full_coverage'])
        self.assertFalse(options['dynamic_startup_nodes'])
        self.assertEqual(7, options['reinitialize_steps'])
        self.assertEqual(4, options['cluster_error_retry_attempts'])
        self.assertEqual(250, options['max_connections'])
        self.assertEqual('database_password', acquired[0][0])
        self.assertTrue(acquired[0][1].closed)
        self.assertEqual({0}, set(acquired[0][1].value))
        session.close()

    def test_mutation_timeout_is_unknown_and_never_replayed(self):
        session = self.client.open_session({'route': route()})
        session.client.fail = TimeoutError_()
        with self.assertRaises(RedisUnknownOutcomeError):
            self.client.execute(session, {'source': ['SET', 'a', 'b']})
        self.assertEqual(1, len(session.client.commands))
        with self.assertRaises(RedisClientError):
            self.client.execute(session, {'source': ['GET', 'a']})

    def test_command_surface_rejects_inline_authentication_material(self):
        session = self.client.open_session({'route': route()})
        for command in (
            ['AUTH', 'inline-password'],
            ['HELLO', 3, 'AUTH', 'user', 'inline-password'],
            ['ACL', 'SETUSER', 'reader', '>inline-password'],
            ['CONFIG', 'SET', 'requirepass', 'inline-password'],
            ['MIGRATE', 'host', 6379, 'key', 0, 1000,
             'AUTH', 'inline-password'],
        ):
            with self.subTest(command=command):
                with self.assertRaises(RedisClientError):
                    self.client.execute(session, {'source': command})
        self.assertEqual([], session.client.commands)

    def test_catalog_covers_native_data_structures_and_no_retry(self):
        catalog = {'objects': [
            {'resource_kind': kind, 'operations': [
                {'operation_id': operation}
            ]}
            for kind, operation in (
                ('hash', 'insert'), ('stream', 'update'),
                ('vector-set', 'insert'), ('transaction', 'execute'),
            )
        ]}
        value = self.client.visual_admin_catalog(catalog)
        self.assertFalse(value['automatic_mutation_retry'])
        self.assertTrue(value['native_outcomes_are_opaque'])
        self.assertIsNotNone(value['objects'][0]['operations'][0]['form'])
        self.assertFalse(self.client.supports_admin_operation(
            'stream', 'update'
        ))

    def test_row_identity_is_route_bound_single_use_and_concurrent(self):
        page = self.client.read_admin_rows({
            '_provider_route': route(),
            'target_resource': target('hash', 'hash'),
            'limit': 10,
        })
        row = page['rows'][0]
        self.assertIn('identity_token', row)
        self.assertEqual('field', row['values']['selector'])
        request = {
            'resource_kind': 'hash', 'operation_id': 'update',
            'target_resource': target('hash', 'hash'),
            '_provider_route': route(),
            'draft': {
                'selector': {'identity_token': row['identity_token']},
                'changes': {'value': 'after'},
            },
        }
        plan = self.client.plan_admin_operation(request)
        result = self.client.apply_admin_operation({
            'provider_payload': plan['provider_payload']
        })
        self.assertEqual('observed', result['native_outcome'])
        self.assertEqual(
            b'after', self.factory.clients[-1].hashes[b'hash'][b'field']
        )
        with self.assertRaises(RedisClientError):
            self.client.apply_admin_operation({
                'provider_payload': plan['provider_payload']
            })

    def test_native_compile_covers_stream_vector_acl_and_transaction(self):
        stream = self.client._compile_admin({
            'resource_kind': 'stream', 'operation_id': 'insert',
            'draft': {'values': {'fields': {'f': 'v'}}},
            'native': {'key': 'events'}, '_provider_route': route(),
        })
        self.assertEqual(b'XADD', stream['commands'][0][0])
        vector = self.client._compile_admin({
            'resource_kind': 'vector-set', 'operation_id': 'insert',
            'draft': {'values': [{
                'element': 'one', 'vector': [1, 0], 'quantization': 'q8',
            }]},
            'native': {'key': 'vectors'}, '_provider_route': route(),
        })
        self.assertEqual(b'VADD', vector['commands'][0][0])
        transaction = self.client._compile_admin({
            'resource_kind': 'transaction', 'operation_id': 'execute',
            'draft': {'commands': [['SET', 'a', 'b']], 'watch_keys': ['a']},
            'native': {}, '_provider_route': route(),
        })
        self.assertTrue(transaction['transactional'])
        acl = self.client._compile_admin({
            'resource_kind': 'acl-user', 'operation_id': 'create',
            'draft': {
                'name': 'reader', 'rules': ['on', '+get'],
                'password_credential_reference': 'secret-one',
            },
            'native': {}, '_provider_route': route(),
        }, preview=True)
        self.assertIn(b'[credential-reference]', acl['commands'][0])

    def test_frontend_has_key_value_editor_and_renderer(self):
        source = (WEB / 'pgadmin/static/js/Dialogs/'
                  'ProviderWorkspaceContent.jsx').read_text(encoding='utf-8')
        self.assertIn('function KeyValueDataGrid', source)
        self.assertIn('function KeyValueView', source)
        self.assertIn("'data-structure-key-value'", source)


if __name__ == '__main__':
    unittest.main()
