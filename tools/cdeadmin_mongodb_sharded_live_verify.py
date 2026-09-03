#!/usr/bin/env python3
##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Qualify MongoDB administration against an isolated sharded 8.2.6."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import secrets
import socket
import subprocess
import sys
import tempfile
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

from pgadmin.cdeadmin.core import EndpointContext  # noqa: E402
from pgadmin.cdeadmin.core.registry import (  # noqa: E402
    PermissionGrant, PermissionGuard,
)
from pgadmin.cdeadmin.providers.mongodb.provider import (  # noqa: E402
    PROFILE, create_provider,
)
from pgadmin.cdeadmin.security import (  # noqa: E402
    EndpointSecretService, SecretReference,
)


CATEGORIES = (
    'sharded_discovery', 'topology_mutation', 'replica_set_admin',
    'change_stream_resume', 'authorization_filtering', 'fault',
)


def _port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as handle:
        handle.bind(('127.0.0.1', 0))
        return handle.getsockname()[1]


def _sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


class _Process:
    def __init__(self, name, command, log, port):
        self.name = name
        self.command = list(command)
        self.log = log
        self.port = port
        self.process = None

    def start(self):
        self.log.parent.mkdir(parents=True, exist_ok=True)
        self.process = subprocess.Popen(
            [*self.command, '--logpath', str(self.log), '--logappend'],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def wait_socket(self):
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError(
                    f'{self.name} exited with {self.process.returncode}'
                )
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
                probe.settimeout(0.2)
                if probe.connect_ex(('127.0.0.1', self.port)) == 0:
                    return
            time.sleep(0.2)
        raise RuntimeError(f'{self.name} did not open its socket')

    def stop(self):
        if self.process is None:
            return
        self.process.terminate()
        try:
            self.process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=10)
        self.process = None


class _ShardedRuntime:
    def __init__(self, mongod, mongos, log_root):
        self.mongod = mongod.resolve()
        self.mongos = mongos.resolve()
        self.log_root = log_root.resolve()
        self.temporary = tempfile.TemporaryDirectory(
            prefix='cdeadmin-mongodb-sharded-826-'
        )
        self.root = Path(self.temporary.name).resolve()
        self.key_file = self.root / 'cluster.key'
        self.ports = {
            'config': _port(), 'shard_one': _port(),
            'shard_two': _port(), 'router': _port(),
        }
        self.username = 'cdeadmin_sharded_' + secrets.token_hex(5)
        self.password = secrets.token_urlsafe(48)
        self.limited_username = 'cdeadmin_limited_' + secrets.token_hex(4)
        self.limited_password = secrets.token_urlsafe(40)
        self.database = 'cdeadmin_sharded_qualification'
        self.processes = []

    def _mongod_process(self, name, set_name, role, authenticated):
        dbpath = self.root / name
        dbpath.mkdir(parents=True, exist_ok=True)
        command = [
            str(self.mongod), '--dbpath', str(dbpath), '--bind_ip',
            '127.0.0.1', '--port', str(self.ports[name]), '--replSet',
            set_name, f'--{role}', '--setParameter', 'enableTestCommands=0',
        ]
        if authenticated:
            command.extend(['--auth', '--keyFile', str(self.key_file)])
        return _Process(
            name, command, self.log_root / f'{name}.log', self.ports[name]
        )

    def _mongos_process(self, authenticated):
        command = [
            str(self.mongos), '--bind_ip', '127.0.0.1', '--port',
            str(self.ports['router']), '--configdb',
            f"cde-config/127.0.0.1:{self.ports['config']}",
        ]
        if authenticated:
            command.extend(['--keyFile', str(self.key_file)])
        return _Process(
            'router', command, self.log_root / 'router.log',
            self.ports['router'],
        )

    @staticmethod
    def _direct(port, username=None, password=None):
        import pymongo
        values = {
            'host': '127.0.0.1', 'port': port, 'directConnection': True,
            'serverSelectionTimeoutMS': 10000,
        }
        if username:
            values.update({
                'username': username, 'password': password,
                'authSource': 'admin',
            })
        return pymongo.MongoClient(**values)

    @staticmethod
    def _wait_primary(port, set_name, username=None, password=None):
        import pymongo
        values = {
            'host': '127.0.0.1', 'port': port, 'replicaSet': set_name,
            'serverSelectionTimeoutMS': 30000,
        }
        if username:
            values.update({
                'username': username, 'password': password,
                'authSource': 'admin',
            })
        client = pymongo.MongoClient(**values)
        client.admin.command('ping')
        return client

    def _launch(self, authenticated):
        definitions = (
            ('config', 'cde-config', 'configsvr'),
            ('shard_one', 'cde-shard-one', 'shardsvr'),
            ('shard_two', 'cde-shard-two', 'shardsvr'),
        )
        self.processes = [
            self._mongod_process(*definition, authenticated)
            for definition in definitions
        ]
        for process in self.processes:
            process.start()
            process.wait_socket()
        if not authenticated:
            for name, set_name, _role in definitions:
                client = self._direct(self.ports[name])
                client.admin.command({'replSetInitiate': {
                    '_id': set_name,
                    'configsvr': name == 'config',
                    'members': [{
                        '_id': 0,
                        'host': f"127.0.0.1:{self.ports[name]}",
                    }],
                }})
                client.close()
            for name, set_name, _role in definitions:
                client = self._wait_primary(self.ports[name], set_name)
                if name != 'config':
                    client.admin.command({
                        'createUser': self.username, 'pwd': self.password,
                        'roles': [{'role': 'root', 'db': 'admin'}],
                    })
                client.close()
        router = self._mongos_process(authenticated)
        router.start()
        router.wait_socket()
        self.processes.append(router)

    def start(self):
        import pymongo
        self.log_root.mkdir(parents=True, exist_ok=True)
        self.key_file.write_text(
            base64.b64encode(os.urandom(96)).decode('ascii') + '\n',
            encoding='ascii',
        )
        os.chmod(self.key_file, 0o600)
        self._launch(False)
        router = pymongo.MongoClient(
            host='127.0.0.1', port=self.ports['router'],
            serverSelectionTimeoutMS=30000,
        )
        router.admin.command({
            'addShard': f"cde-shard-one/127.0.0.1:{self.ports['shard_one']}"
        })
        router.admin.command({
            'addShard': f"cde-shard-two/127.0.0.1:{self.ports['shard_two']}"
        })
        router.admin.command({
            'createUser': self.username, 'pwd': self.password,
            'roles': [{'role': 'root', 'db': 'admin'}],
        })
        router.close()
        self.stop_processes()
        self._launch(True)
        self.admin_client().admin.command('ping')

    def admin_client(self):
        import pymongo
        return pymongo.MongoClient(
            host='127.0.0.1', port=self.ports['router'],
            username=self.username, password=self.password,
            authSource='admin', serverSelectionTimeoutMS=30000,
        )

    def stop_processes(self):
        for process in reversed(self.processes):
            process.stop()
        self.processes = []

    def stop(self):
        self.stop_processes()
        self.password = ''
        self.limited_password = ''
        self.temporary.cleanup()


def _context():
    endpoint_id = str(uuid.uuid5(
        uuid.NAMESPACE_URL, 'cdeadmin-live:mongodb:8.2.6:sharded'
    ))

    def child(name):
        return str(uuid.uuid5(uuid.UUID(endpoint_id), name))
    permissions = frozenset({
        'network', 'secret_read', 'data_read', 'data_write', 'administer',
        'execute', 'filesystem',
    })
    return EndpointContext(
        endpoint_id=endpoint_id, mode='legacy_native',
        experience_family='mongodb', provider_id=PROFILE.provider_id,
        provider_version='0.1.0', profile_id=PROFILE.profile_id,
        profile_version=PROFILE.exact_version,
        target_adapter_id='mongodb-wire-client',
        target_adapter_version='pymongo-4.17.0',
        pool_namespace=child('pool'), session_namespace=child('session'),
        cache_namespace=child('cache'),
        diagnostic_namespace=child('diagnostic'),
        effective_permissions=permissions,
        declared_runtime_family='mongodb',
        verified_runtime_family='mongodb',
        verified_runtime_version='8.2.6',
        runtime_verification_state='verified',
        runtime_evidence_reference='cde-mongodb-sharded-live:8.2.6',
        runtime_identity_generation='mongodb-8.2.6-sharded-live',
    )


def _permissions(context, secrets_service):
    scopes = {
        'network': {'endpoint'}, 'secret_read': {'endpoint'},
        'data_read': {'endpoint', 'resource'},
        'data_write': {'endpoint', 'resource'},
        'administer': {'endpoint', 'resource'}, 'execute': {'endpoint'},
        'filesystem': {'endpoint', 'resource'},
    }
    return PermissionGuard(
        {name: PermissionGrant(name, frozenset(value))
         for name, value in scopes.items()},
        context.effective_permissions, context=context,
        secret_service=secrets_service,
    )


def _apply(provider, route, resource_kind, operation_id, target, draft):
    plan = provider.plan_visual_admin({
        'resource_kind': resource_kind, 'operation_id': operation_id,
        'target_resource': target, 'draft': draft,
        '_provider_route': route,
    })
    if plan['state'] != 'ready':
        raise RuntimeError('provider topology plan is blocked')
    return provider.apply_visual_admin({
        'plan_id': plan['plan_id'], 'plan_digest': plan['plan_digest'],
        'confirmed': True,
    })


def verify(mongod, mongos, log_root):
    import pymongo
    context = _context()
    runtime = _ShardedRuntime(mongod, mongos, log_root)
    categories = {name: 'not_run' for name in CATEGORIES}
    failures = []
    secret_service = EndpointSecretService()
    provider = None
    reference = str(uuid.uuid5(
        uuid.UUID(context.endpoint_id), 'root-password'
    ))
    limited_reference = str(uuid.uuid5(
        uuid.UUID(context.endpoint_id), 'limited-password'
    ))

    def category(name, callback):
        try:
            callback()
        except Exception as exc:
            message = str(exc)
            for value in (runtime.password, runtime.limited_password):
                if value:
                    message = message.replace(value, '[redacted]')
            categories[name] = 'failed'
            failures.append({
                'category': name, 'error_type': type(exc).__name__,
                'message': message,
            })
        else:
            categories[name] = 'passed'

    try:
        runtime.start()
        values = {
            'root': lambda: runtime.password.encode(),
            'limited': lambda: runtime.limited_password.encode(),
        }
        secret_service.register_resolver(
            'sharded.ephemeral', lambda locator, *_args: values[locator]()
        )
        for ref, locator in (
            (reference, 'root'), (limited_reference, 'limited'),
        ):
            secret_service.register_reference(SecretReference(
                reference_id=ref, endpoint_id=context.endpoint_id,
                endpoint_mode=context.mode,
                secret_kind='database_password',
                storage_kind='ephemeral_test_account',
                resolver_id='sharded.ephemeral', locator=locator,
                allowed_purposes=frozenset({
                    'connect', 'administer', 'provider_tool',
                }),
                authority_scope='legacy_engine_auth',
            ))
        provider = create_provider(
            context, _permissions(context, secret_service)
        )
        route = {
            'route_id': 'sharded-live', 'host': '127.0.0.1',
            'port': runtime.ports['router'], 'database': runtime.database,
            'username': runtime.username, 'auth_source': 'admin',
            'credential_reference_id': reference,
            'principal_reference': 'sharded-live',
            'server_selection_timeout_ms': 10000,
            'tool_workspace': str(runtime.root / 'tools'),
        }
        request = {'route': route, 'capability_generation': 'sharded-live'}
        provider.discover_endpoint(request)
        state = {'resources': [], 'generation': None, 'resume': None}

        def discovery():
            state['resources'] = provider.list_resources(request)
            kinds = {item['resource_kind'] for item in state['resources']}
            if not {'router', 'shard', 'balancer'}.issubset(kinds):
                raise RuntimeError(
                    'sharded discovery resources are incomplete'
                )
            shards = [
                item for item in state['resources']
                if item['resource_kind'] == 'shard'
            ]
            if len(shards) != 2:
                raise RuntimeError(
                    'both shard replica sets were not discovered'
                )
            state['generation'] = next(
                item['generation'] for item in state['resources']
                if item['resource_kind'] == 'deployment'
            )

        category('sharded_discovery', discovery)

        def topology():
            admin = runtime.admin_client()
            resources = provider.list_resources(request)
            deployment = next(
                item for item in resources
                if item['resource_kind'] == 'deployment'
            )
            router = next(
                item for item in resources if item['resource_kind'] == 'router'
            )
            balancer = next(
                item for item in resources
                if item['resource_kind'] == 'balancer'
            )
            shard_two = next(
                item for item in resources
                if item['resource_kind'] == 'shard' and
                item['display_name'] == 'cde-shard-two'
            )
            # Prove remove/add while the second shard is empty. Performing
            # this after sharding data would correctly require a drain phase.
            for _attempt in range(20):
                result = _apply(
                    provider, route, 'shard', 'execute', shard_two, {
                        'action': 'remove_shard', 'arguments': {},
                        'confirmation': 'remove-empty-shard',
                    }
                )['provider_result']['observation']
                if result.get('state') == 'completed':
                    break
                time.sleep(0.25)
            else:
                raise RuntimeError('empty shard removal did not complete')
            _apply(provider, route, 'deployment', 'execute', deployment, {
                'action': 'add_shard',
                'arguments': {
                    'connection': (
                        'cde-shard-two/127.0.0.1:' +
                        str(runtime.ports['shard_two'])
                    )
                },
                'confirmation': 'add-shard',
            })
            admin.admin.command({'enableSharding': runtime.database})
            collection = admin[runtime.database]['events']
            collection.create_index([('tenant', 1)])
            collection.insert_many([
                {'tenant': -1, 'value': 'low'},
                {'tenant': 1, 'value': 'high'},
            ])
            admin.admin.command({
                'shardCollection': f'{runtime.database}.events',
                'key': {'tenant': 1},
            })
            _apply(provider, route, 'zone', 'create', None, {
                'options': {
                    'action': 'assign_shard', 'shard': 'cde-shard-one',
                    'zone': 'cde-zone-one',
                }
            })
            _apply(provider, route, 'zone', 'create', None, {
                'options': {
                    'namespace': f'{runtime.database}.events',
                    'min': {'tenant': -1000}, 'max': {'tenant': 0},
                    'zone': 'cde-zone-one',
                }
            })
            zones = [
                item for item in provider.list_resources(request)
                if item['resource_kind'] == 'zone'
            ]
            if not zones:
                raise RuntimeError('zone range was not discovered')
            _apply(provider, route, 'balancer', 'execute', balancer, {
                'action': 'stop', 'arguments': {},
                'confirmation': 'stop-balancer',
            })
            _apply(provider, route, 'balancer', 'execute', balancer, {
                'action': 'start', 'arguments': {},
                'confirmation': 'start-balancer',
            })
            _apply(provider, route, 'router', 'execute', router, {
                'action': 'flush_configuration', 'arguments': {},
                'confirmation': 'flush-router',
            })
            current = provider.list_resources(request)
            if len([x for x in current if x['resource_kind'] == 'shard']) != 2:
                raise RuntimeError('removed shard was not added back')
            generation = next(
                item['generation'] for item in current
                if item['resource_kind'] == 'deployment'
            )
            if generation == state['generation']:
                raise RuntimeError('topology generation did not invalidate')
            admin.close()

        category('topology_mutation', topology)

        def replica_admin():
            direct_route = {
                **route, 'route_id': 'shard-one-direct',
                'port': runtime.ports['shard_one'],
                'replica_set': 'cde-shard-one', 'direct_connection': False,
            }
            direct_request = {'route': direct_route}
            direct_provider = create_provider(
                context, _permissions(context, secret_service)
            )
            direct_provider.discover_endpoint(direct_request)
            resources = direct_provider.list_resources(direct_request)
            target = next(
                item for item in resources
                if item['resource_kind'] == 'replica-set'
            )
            client = runtime._wait_primary(
                runtime.ports['shard_one'], 'cde-shard-one',
                runtime.username, runtime.password,
            )
            config = client.admin.command({'replSetGetConfig': 1})['config']
            config['version'] += 1
            config = direct_provider.client._extended_json(config)
            _apply(direct_provider, direct_route, 'replica-set', 'alter',
                   target, {'changes': {'config': config},
                            'definition': '', 'online': True})
            # A single-member primary correctly refuses replSetFreeze. Prove
            # the provider returns that native refusal without converting it
            # into a successful failover observation.
            try:
                _apply(direct_provider, direct_route, 'replica-set', 'execute',
                       target, {
                           'action': 'freeze', 'arguments': {'seconds': 0},
                           'confirmation': 'unfreeze-member',
                       })
            except Exception:
                pass
            else:
                raise RuntimeError('primary freeze refusal was not preserved')
            client.close()
            direct_provider.close()

        category('replica_set_admin', replica_admin)

        def streams():
            session = provider.open_session({'route': route})
            source = {
                'operation': 'watch', 'database': runtime.database,
                'collection': 'events', 'batch_size': 10,
                'max_await_time_ms': 100,
            }
            operation = provider.execute({
                'session_id': session['session_id'],
                'execution_id': 'stream-one', 'source': json.dumps(source),
            })
            provider.describe_result({
                'operation_id': operation['operation_id']
            })
            admin = runtime.admin_client()
            admin[runtime.database].events.with_options(
                write_concern=pymongo.WriteConcern('majority')
            ).insert_one({
                'tenant': 5, 'value': 'resume-one',
            })
            payload = None
            for _attempt in range(100):
                result = provider.describe_result({
                    'operation_id': operation['operation_id']
                })
                payload = result['extensions']['mongodb']['payload']
                if payload['documents']:
                    state['resume'] = payload['resume_token'] or (
                        payload['documents'][-1].get('_id')
                    )
                    break
                time.sleep(0.1)
            if state['resume'] is None:
                raise RuntimeError(
                    'change stream produced no resumable event; last=' +
                    str(payload)[:500]
                )
            provider.cancel({'operation_id': operation['operation_id']})
            resumed = provider.execute({
                'session_id': session['session_id'],
                'execution_id': 'stream-two',
                'source': json.dumps({
                    **source, 'resume_after': state['resume'],
                }),
            })
            admin[runtime.database].events.with_options(
                write_concern=pymongo.WriteConcern('majority')
            ).insert_one({
                'tenant': 6, 'value': 'resume-two',
            })
            for _attempt in range(100):
                result = provider.describe_result({
                    'operation_id': resumed['operation_id']
                })
                documents = result['extensions']['mongodb']['payload'][
                    'documents'
                ]
                if documents:
                    break
                time.sleep(0.1)
            else:
                raise RuntimeError('resumed change stream produced no event')
            provider.cancel({'operation_id': resumed['operation_id']})
            admin.close()

        category('change_stream_resume', streams)

        def authorization():
            admin = runtime.admin_client()
            admin.admin.command({
                'createUser': runtime.limited_username,
                'pwd': runtime.limited_password,
                'roles': [{'role': 'read', 'db': runtime.database}],
            })
            limited_provider = create_provider(
                context, _permissions(context, secret_service)
            )
            limited_route = {
                **route, 'route_id': 'limited',
                'username': runtime.limited_username,
                'credential_reference_id': limited_reference,
            }
            resources = limited_provider.list_resources({
                'route': limited_route
            })
            databases = {
                item['display_name'] for item in resources
                if item['resource_kind'] == 'database'
            }
            if runtime.database not in databases or 'config' in databases:
                raise RuntimeError('authorization filtering is incorrect')
            limited_provider.close()
            admin.admin.command({'dropUser': runtime.limited_username})
            admin.close()

        category('authorization_filtering', authorization)

        def fault():
            resources = provider.list_resources(request)
            deployment = next(
                item for item in resources
                if item['resource_kind'] == 'deployment'
            )
            try:
                _apply(provider, route, 'deployment', 'execute', deployment, {
                    'action': 'unknown-topology-action', 'arguments': {},
                    'confirmation': 'must-fail',
                })
            except Exception:
                return
            raise RuntimeError('unknown topology action was admitted')

        category('fault', fault)
    except Exception as exc:
        failures.append({
            'category': 'setup', 'error_type': type(exc).__name__,
            'message': str(exc).replace(runtime.password, '[redacted]'),
        })
    finally:
        if provider is not None:
            provider.close()
        runtime.stop()
    passed = all(value == 'passed' for value in categories.values())
    return {
        'schema': 'cdeadmin.mongodb-sharded-live-verification.v1',
        'engine_id': 'mongodb', 'exact_profile': '8.2.6',
        'activation_ready': passed, 'categories': categories,
        'failures': failures, 'mongod_sha256': _sha256(mongod),
        'mongos_sha256': _sha256(mongos),
        'pymongo_version': pymongo.version,
        'common_transaction_finality_interpreted': False,
        'secret_values_exported': False,
        'log_root': str(log_root.resolve()),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mongod', type=Path, required=True)
    parser.add_argument('--mongos', type=Path, required=True)
    parser.add_argument('--log-root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = verify(args.mongod, args.mongos, args.log_root)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, indent=2, sort_keys=True) + '\n',
        encoding='utf-8',
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result['activation_ready'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
