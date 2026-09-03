#!/usr/bin/env python3
##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Qualify the MongoDB provider against an isolated exact 8.2.6 runtime."""

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
    PermissionGrant,
    PermissionGuard,
)
from pgadmin.cdeadmin.providers.mongodb.provider import (  # noqa: E402
    PROFILE,
    create_provider,
)
from pgadmin.cdeadmin.security import (  # noqa: E402
    EndpointSecretService,
    SecretReference,
)


CATEGORIES = (
    'resource', 'language_api', 'result', 'transaction',
    'admin', 'security', 'fault',
)


def _sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _available_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as handle:
        handle.bind(('127.0.0.1', 0))
        return handle.getsockname()[1]


def _context():
    endpoint_id = str(uuid.uuid5(
        uuid.NAMESPACE_URL, 'cdeadmin-live:mongodb:8.2.6'
    ))

    def child(purpose):
        return str(uuid.uuid5(uuid.UUID(endpoint_id), purpose))

    permissions = frozenset({
        'network', 'secret_read', 'data_read', 'data_write',
        'administer', 'execute', 'filesystem',
    })
    return EndpointContext(
        endpoint_id=endpoint_id,
        mode='legacy_native',
        experience_family='mongodb',
        provider_id=PROFILE.provider_id,
        provider_version='0.1.0',
        profile_id=PROFILE.profile_id,
        profile_version=PROFILE.exact_version,
        target_adapter_id='mongodb-wire-client',
        target_adapter_version='pymongo-4.17.0',
        pool_namespace=child('pool'),
        session_namespace=child('session'),
        cache_namespace=child('cache'),
        diagnostic_namespace=child('diagnostic'),
        effective_permissions=permissions,
        declared_runtime_family='mongodb',
        verified_runtime_family='mongodb',
        verified_runtime_version='8.2.6',
        runtime_verification_state='verified',
        runtime_evidence_reference=(
            'cde-mongodb-live:mongodb-8.2.6:20260901'
        ),
        runtime_identity_generation='mongodb-8.2.6-live',
    )


def _permissions(context, secret_service):
    scopes = {
        'network': {'endpoint'},
        'secret_read': {'endpoint'},
        'data_read': {'endpoint', 'resource'},
        'data_write': {'endpoint', 'resource'},
        'administer': {'endpoint', 'resource'},
        'execute': {'endpoint'},
        'filesystem': {'endpoint', 'resource'},
    }
    grants = {
        name: PermissionGrant(name, frozenset(values))
        for name, values in scopes.items()
    }
    return PermissionGuard(
        grants, context.effective_permissions, context=context,
        secret_service=secret_service,
    )


class _Runtime:
    def __init__(self, binary, server_log):
        self.binary = binary.resolve()
        self.server_log = server_log.resolve()
        self.port = _available_port()
        self.temporary = tempfile.TemporaryDirectory(
            prefix='cdeadmin-mongodb-826-live-'
        )
        self.root = Path(self.temporary.name).resolve()
        self.database_root = self.root / 'data'
        self.key_file = self.root / 'replica.key'
        self.process = None
        self.username = 'cdeadmin_live_' + secrets.token_hex(6)
        self.password = secrets.token_urlsafe(48)
        self.database = 'cdeadmin_qualification'
        self.account_removed = False

    def _command(self, authenticated):
        value = [
            str(self.binary), '--dbpath', str(self.database_root),
            '--logpath', str(self.server_log), '--logappend',
            '--bind_ip', '127.0.0.1', '--port', str(self.port),
            '--replSet', 'cdeadmin-rs', '--setParameter',
            'enableTestCommands=0',
        ]
        if authenticated:
            value.extend(['--auth', '--keyFile', str(self.key_file)])
        return value

    def _launch(self, authenticated):
        self.process = subprocess.Popen(
            self._command(authenticated),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        import pymongo

        deadline = time.monotonic() + 45
        last_error = None
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError(
                    f'mongod exited with status {self.process.returncode}'
                )
            try:
                arguments = {
                    'host': '127.0.0.1', 'port': self.port,
                    'directConnection': True,
                    'serverSelectionTimeoutMS': 500,
                }
                if authenticated:
                    arguments.update({
                        'username': self.username,
                        'password': self.password,
                        'authSource': 'admin',
                    })
                client = pymongo.MongoClient(**arguments)
                client.admin.command('ping')
                return client
            except Exception as exc:
                last_error = exc
                time.sleep(0.2)
        raise RuntimeError(
            f'mongod did not become ready ({type(last_error).__name__})'
        )

    @staticmethod
    def _shutdown(client):
        try:
            client.admin.command({'shutdown': 1, 'force': True})
        except Exception:
            pass
        try:
            client.close()
        except Exception:
            pass

    def _wait_process(self):
        if self.process is None:
            return
        try:
            self.process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=10)
        self.process = None

    def start(self):
        import pymongo

        self.database_root.mkdir(parents=True)
        self.server_log.parent.mkdir(parents=True, exist_ok=True)
        self.key_file.write_text(
            base64.b64encode(os.urandom(96)).decode('ascii') + '\n',
            encoding='ascii',
        )
        os.chmod(self.key_file, 0o600)
        direct = self._launch(authenticated=False)
        direct.admin.command({'replSetInitiate': {
            '_id': 'cdeadmin-rs',
            'members': [{
                '_id': 0,
                'host': f'127.0.0.1:{self.port}',
            }],
        }})
        primary = pymongo.MongoClient(
            host='127.0.0.1', port=self.port,
            replicaSet='cdeadmin-rs',
            serverSelectionTimeoutMS=30000,
        )
        primary.admin.command('ping')
        database = primary[self.database]
        database.create_collection(
            'qualification',
            validator={'value': {'$type': 'int'}},
        )
        database.qualification.insert_one({'value': 42, 'key': 'initial'})
        primary.admin.command({
            'createUser': self.username,
            'pwd': self.password,
            'roles': [
                # The ephemeral qualifier must exercise the complete visual
                # administration surface, including role privilege changes.
                {'role': 'root', 'db': 'admin'},
            ],
        })
        self._shutdown(primary)
        self._wait_process()
        authenticated = self._launch(authenticated=True)
        authenticated.close()

    def admin_client(self):
        import pymongo

        return pymongo.MongoClient(
            host='127.0.0.1', port=self.port,
            username=self.username, password=self.password,
            authSource='admin', replicaSet='cdeadmin-rs',
            serverSelectionTimeoutMS=5000,
        )

    def stop(self):
        try:
            if self.process is not None:
                self._shutdown(self.admin_client())
                self._wait_process()
        finally:
            self.password = ''
            self.account_removed = True
            self.temporary.cleanup()


def _apply(provider, request):
    plan = provider.plan_visual_admin(request)
    if plan['state'] != 'ready':
        raise RuntimeError('MongoDB visual plan is not ready')
    return provider.apply_visual_admin({
        'plan_id': plan['plan_id'],
        'plan_digest': plan['plan_digest'],
        'confirmed': True,
    })


def verify(binary, server_log):
    import pymongo

    context = _context()
    runtime = _Runtime(binary, server_log)
    categories = {name: 'not_run' for name in CATEGORIES}
    failures = []
    provider = None
    resources = []
    session = None
    secret_service = EndpointSecretService()
    reference_id = str(uuid.uuid5(
        uuid.UUID(context.endpoint_id), 'database-password'
    ))

    def category(name, callback):
        try:
            callback()
        except Exception as exc:
            message = str(exc)
            if runtime.password:
                message = message.replace(runtime.password, '[redacted]')
            categories[name] = 'failed'
            failures.append({
                'category': name,
                'error_type': type(exc).__name__,
                'message': message,
            })
        else:
            categories[name] = 'passed'

    try:
        if pymongo.version != '4.17.0' or not pymongo.has_c():
            raise RuntimeError('exact PyMongo 4.17.0 C extension is required')
        runtime.start()
        secret_service.register_resolver(
            'live.ephemeral',
            lambda *_args: runtime.password.encode('utf-8'),
        )
        secret_service.register_reference(SecretReference(
            reference_id=reference_id,
            endpoint_id=context.endpoint_id,
            endpoint_mode=context.mode,
            secret_kind='database_password',
            storage_kind='ephemeral_test_account',
            resolver_id='live.ephemeral',
            locator='ephemeral:mongodb:qualification',
            allowed_purposes=frozenset({
                'connect', 'administer', 'provider_tool',
            }),
            authority_scope='legacy_engine_auth',
        ))
        provider = create_provider(
            context, _permissions(context, secret_service)
        )
        route = {
            'route_id': 'exact-live-qualification',
            'host': '127.0.0.1', 'port': runtime.port,
            'database': runtime.database,
            'username': runtime.username, 'auth_source': 'admin',
            'replica_set': 'cdeadmin-rs',
            'credential_reference_id': reference_id,
            'principal_reference': 'cdeadmin-live-qualifier',
            'tool_workspace': str(runtime.root / 'tools'),
            'server_selection_timeout_ms': 5000,
        }
        request = {
            'route': route,
            'capability_generation': 'exact-live-qualification',
        }
        discovered = provider.discover_endpoint(request)
        if discovered['verified_runtime']['version'] != '8.2.6':
            raise RuntimeError('exact runtime identity was not verified')

        def resource_gate():
            nonlocal resources
            resources = provider.list_resources(request)
            kinds = {item['resource_kind'] for item in resources}
            required = {
                'deployment', 'replica-set', 'database', 'collection',
                'document', 'index', 'validator', 'user', 'change-stream',
            }
            document_request = {
                **request, 'database': runtime.database,
                'collection': 'qualification', 'include_documents': True,
            }
            resources.extend(provider.list_resources(document_request))
            kinds.update(item['resource_kind'] for item in resources)
            if not required.issubset(kinds):
                raise RuntimeError(
                    'resource discovery missing: ' +
                    ', '.join(sorted(required.difference(kinds)))
                )

        category('resource', resource_gate)

        def language_gate():
            language = provider.describe_language({})[0]
            if language['display_name'] != 'MongoDB Query API (JSON)':
                raise RuntimeError('MongoDB Query API profile is unavailable')
            contribution = provider.data_studio_contributions()[
                'languages'
            ][0]
            if contribution.editor_mode != 'application/json':
                raise RuntimeError('MongoDB editor is not JSON-native')

        category('language_api', language_gate)

        def result_gate():
            nonlocal session
            session = provider.open_session(request)
            operation = provider.execute({
                'session_id': session['session_id'],
                'execution_id': 'mongodb-live-result',
                'source': json.dumps({
                    'operation': 'find', 'database': runtime.database,
                    'collection': 'qualification',
                    'filter': {'value': 42}, 'limit': 10,
                }),
            })
            result = provider.describe_result(operation)
            documents = result['extensions']['mongodb']['payload'][
                'documents'
            ]
            if not documents or documents[0]['value'] != {'$numberInt': '42'}:
                raise RuntimeError('document result did not round trip')
            for suffix, query in (
                ('aggregate', {
                    'operation': 'aggregate',
                    'database': runtime.database,
                    'collection': 'qualification',
                    'pipeline': [{'$match': {'value': 42}}],
                }),
                ('command', {
                    'operation': 'command', 'database': 'admin',
                    'command': {'ping': 1},
                }),
            ):
                native_operation = provider.execute({
                    'session_id': session['session_id'],
                    'execution_id': f'mongodb-live-{suffix}',
                    'source': json.dumps(query),
                })
                native_result = provider.describe_result(native_operation)
                if not native_result['complete']:
                    raise RuntimeError(
                        f'{suffix} result did not complete'
                    )
            renderer = provider.select_renderer(result)
            native = renderer['extensions']['mongodb']
            if native['component_reference'] != (
                'cdeadmin/results/DocumentTreeView'
            ):
                raise RuntimeError('production document renderer unavailable')

        category('result', result_gate)

        def transaction_gate():
            if session is None:
                raise RuntimeError('result category did not open a session')
            value = provider.describe_transaction(session)['provider_payload']
            if value.get('driver_observation_only') is not True:
                raise RuntimeError('transaction state is not driver-observed')
            if value.get('finality_interpreted_by_common_code') is not False:
                raise RuntimeError('common code interpreted finality')

        category('transaction', transaction_gate)

        def admin_gate():
            descriptor = provider.visual_admin_descriptor()
            if len(descriptor['objects']) != 25:
                raise RuntimeError('MongoDB administration catalog incomplete')
            database_target = next(
                item for item in provider.list_resources(request)
                if item['resource_kind'] == 'database' and
                item['display_name'] == runtime.database
            )
            collection_name = 'visual_' + secrets.token_hex(4)
            base = {
                'resource_kind': 'collection', 'operation_id': 'create',
                'target_resource': None,
                'draft': {
                    'name': collection_name, 'definition': '',
                    'options': {'database': runtime.database},
                },
                '_provider_route': route,
            }
            _apply(provider, base)
            current = provider.list_resources(request)
            target = next(
                item for item in current
                if item['resource_kind'] == 'collection' and
                item['display_name'] == collection_name
            )
            _apply(provider, {
                'resource_kind': 'collection', 'operation_id': 'alter',
                'target_resource': target,
                'draft': {
                    'changes': {'validationLevel': 'moderate'},
                    'definition': '', 'online': False,
                },
                '_provider_route': route,
            })
            renamed_collection = collection_name + '_renamed'
            _apply(provider, {
                'resource_kind': 'collection', 'operation_id': 'rename',
                'target_resource': target,
                'draft': {'new_name': renamed_collection},
                '_provider_route': route,
            })
            current = provider.list_resources(request)
            target = next(
                item for item in current
                if item['resource_kind'] == 'collection' and
                item['display_name'] == renamed_collection
            )
            collection_name = renamed_collection
            _apply(provider, {
                'resource_kind': 'collection', 'operation_id': 'insert',
                'target_resource': target,
                'draft': {
                    'values': {'key': 'bulk-one', 'value': 10},
                    'options': {},
                },
                '_provider_route': route,
            })
            _apply(provider, {
                'resource_kind': 'collection', 'operation_id': 'update',
                'target_resource': target,
                'draft': {
                    'selector': {'key': 'bulk-one'},
                    'changes': {'$set': {'value': 11}},
                },
                '_provider_route': route,
            })
            _apply(provider, {
                'resource_kind': 'collection', 'operation_id': 'delete',
                'target_resource': target,
                'draft': {
                    'selector': {'key': 'bulk-one'},
                    'confirmation': 'delete-documents',
                },
                '_provider_route': route,
            })
            _apply(provider, {
                'resource_kind': 'document', 'operation_id': 'insert',
                'target_resource': target,
                'draft': {
                    'values': {'key': 'grid-one', 'value': 1},
                    'options': {},
                },
                '_provider_route': route,
            })
            page = provider.read_visual_admin_rows({
                'target_resource': target, '_provider_route': route,
                'limit': 20,
            })
            if page['documents'][0]['key'] != 'grid-one':
                raise RuntimeError('document grid read failed')
            selector = {'_id': page['documents'][0]['_id']}
            _apply(provider, {
                'resource_kind': 'document', 'operation_id': 'update',
                'target_resource': target,
                'draft': {
                    'selector': selector,
                    'changes': {'$set': {'value': 2}},
                },
                '_provider_route': route,
            })
            _apply(provider, {
                'resource_kind': 'index', 'operation_id': 'create',
                'target_resource': target,
                'draft': {
                    'name': 'key_lookup', 'definition': '',
                    'options': {'keys': [['key', 1]], 'unique': True},
                },
                '_provider_route': route,
            })
            _apply(provider, {
                'resource_kind': 'validator', 'operation_id': 'create',
                'target_resource': target,
                'draft': {
                    'name': 'validator', 'definition': '',
                    'options': {
                        'validator': {'key': {'$type': 'string'}},
                    },
                },
                '_provider_route': route,
            })
            current = provider.list_resources(request)
            index = next(
                item for item in current
                if item['resource_kind'] == 'index' and
                item['display_name'] == 'key_lookup'
            )
            validator = next(
                item for item in current
                if item['resource_kind'] == 'validator' and
                item['authority_path'][-2] == collection_name
            )
            _apply(provider, {
                'resource_kind': 'index', 'operation_id': 'alter',
                'target_resource': index,
                'draft': {
                    'changes': {'hidden': True},
                    'definition': '', 'online': False,
                },
                '_provider_route': route,
            })
            _apply(provider, {
                'resource_kind': 'index', 'operation_id': 'drop',
                'target_resource': index,
                'draft': {'cascade': False, 'confirmation': 'drop-index'},
                '_provider_route': route,
            })
            _apply(provider, {
                'resource_kind': 'validator', 'operation_id': 'alter',
                'target_resource': validator,
                'draft': {
                    'changes': {
                        'validator': {'value': {'$type': 'number'}},
                    },
                    'definition': '', 'online': False,
                },
                '_provider_route': route,
            })
            _apply(provider, {
                'resource_kind': 'validator', 'operation_id': 'drop',
                'target_resource': validator,
                'draft': {
                    'cascade': False, 'confirmation': 'drop-validator',
                },
                '_provider_route': route,
            })
            view_name = collection_name + '_view'
            _apply(provider, {
                'resource_kind': 'view', 'operation_id': 'create',
                'target_resource': None,
                'draft': {
                    'name': view_name, 'definition': '',
                    'options': {
                        'database': runtime.database,
                        'view_on': collection_name,
                        'pipeline': [{'$match': {'value': {'$gte': 0}}}],
                    },
                },
                '_provider_route': route,
            })
            current = provider.list_resources(request)
            view = next(
                item for item in current
                if item['resource_kind'] == 'view' and
                item['display_name'] == view_name
            )
            _apply(provider, {
                'resource_kind': 'view', 'operation_id': 'alter',
                'target_resource': view,
                'draft': {
                    'changes': {'pipeline': [{'$match': {'value': 2}}]},
                    'definition': '', 'online': False,
                },
                '_provider_route': route,
            })
            _apply(provider, {
                'resource_kind': 'document', 'operation_id': 'delete',
                'target_resource': target,
                'draft': {
                    'selector': selector,
                    'confirmation': 'delete-document',
                },
                '_provider_route': route,
            })
            _apply(provider, {
                'resource_kind': 'view', 'operation_id': 'drop',
                'target_resource': view,
                'draft': {'cascade': False, 'confirmation': 'drop-view'},
                '_provider_route': route,
            })
            role_name = 'visual_role_' + secrets.token_hex(3)
            _apply(provider, {
                'resource_kind': 'role', 'operation_id': 'create',
                'target_resource': None,
                'draft': {
                    'name': role_name, 'definition': '',
                    'options': {
                        'database': runtime.database,
                        'privileges': [], 'roles': [],
                    },
                },
                '_provider_route': route,
            })
            current = provider.list_resources(request)
            role = next(
                item for item in current
                if item['resource_kind'] == 'role' and
                item['display_name'] == role_name
            )
            privilege = [{
                'resource': {
                    'db': runtime.database, 'collection': 'qualification',
                },
                'actions': ['find'],
            }]
            _apply(provider, {
                'resource_kind': 'role', 'operation_id': 'alter',
                'target_resource': role,
                'draft': {
                    'changes': {'privileges': [], 'roles': []},
                    'definition': '', 'online': False,
                },
                '_provider_route': route,
            })
            for operation_id in ('grant', 'revoke'):
                _apply(provider, {
                    'resource_kind': 'role',
                    'operation_id': operation_id,
                    'target_resource': role,
                    'draft': {
                        'principal': role_name,
                        'privileges': privilege,
                        **(
                            {'options': {}}
                            if operation_id == 'grant'
                            else {'confirmation': 'revoke-privilege'}
                        ),
                    },
                    '_provider_route': route,
                })
            user_name = 'visual_user_' + secrets.token_hex(3)
            _apply(provider, {
                'resource_kind': 'user', 'operation_id': 'create',
                'target_resource': None,
                'draft': {
                    'name': user_name, 'definition': '',
                    'options': {
                        'database': runtime.database,
                        'credential_reference_id': reference_id,
                        'roles': [],
                    },
                },
                '_provider_route': route,
            })
            current = provider.list_resources(request)
            user = next(
                item for item in current
                if item['resource_kind'] == 'user' and
                item['display_name'] == user_name
            )
            _apply(provider, {
                'resource_kind': 'user', 'operation_id': 'alter',
                'target_resource': user,
                'draft': {
                    'changes': {'roles': []},
                    'definition': '', 'online': False,
                },
                '_provider_route': route,
            })
            role_assignment = [{'role': 'read', 'db': runtime.database}]
            for operation_id in ('grant', 'revoke'):
                _apply(provider, {
                    'resource_kind': 'user',
                    'operation_id': operation_id,
                    'target_resource': user,
                    'draft': {
                        'principal': user_name,
                        'privileges': role_assignment,
                        **(
                            {'options': {}}
                            if operation_id == 'grant'
                            else {'confirmation': 'revoke-role'}
                        ),
                    },
                    '_provider_route': route,
                })
            _apply(provider, {
                'resource_kind': 'user', 'operation_id': 'drop',
                'target_resource': user,
                'draft': {'cascade': False, 'confirmation': 'drop-user'},
                '_provider_route': route,
            })
            _apply(provider, {
                'resource_kind': 'role', 'operation_id': 'drop',
                'target_resource': role,
                'draft': {'cascade': False, 'confirmation': 'drop-role'},
                '_provider_route': route,
            })
            current = provider.list_resources(request)
            tool_targets = {
                item['resource_kind']: item for item in current
                if item['resource_kind'] in {
                    'backup', 'restore', 'import', 'export', 'shell',
                }
            }
            export_path = 'qualification.jsonl'
            _apply(provider, {
                'resource_kind': 'export', 'operation_id': 'execute',
                'target_resource': tool_targets['export'],
                'draft': {
                    'action': 'json_lines',
                    'arguments': {
                        'path': export_path, 'database': runtime.database,
                        'collection': collection_name,
                        'max_documents': 1000,
                    },
                    'confirmation': 'export-documents',
                },
                '_provider_route': route,
            })
            import_collection = collection_name + '_imported'
            _apply(provider, {
                'resource_kind': 'collection', 'operation_id': 'create',
                'target_resource': None,
                'draft': {
                    'name': import_collection, 'definition': '',
                    'options': {'database': runtime.database},
                },
                '_provider_route': route,
            })
            _apply(provider, {
                'resource_kind': 'import', 'operation_id': 'execute',
                'target_resource': tool_targets['import'],
                'draft': {
                    'action': 'json_lines',
                    'arguments': {
                        'path': export_path, 'database': runtime.database,
                        'collection': import_collection,
                    },
                    'confirmation': 'import-documents',
                },
                '_provider_route': route,
            })
            archive_path = 'qualification.archive'
            _apply(provider, {
                'resource_kind': 'backup', 'operation_id': 'execute',
                'target_resource': tool_targets['backup'],
                'draft': {
                    'action': 'archive',
                    'arguments': {
                        'path': archive_path, 'database': runtime.database,
                    },
                    'confirmation': 'backup-database',
                },
                '_provider_route': route,
            })
            restored_database = runtime.database + '_restored'
            _apply(provider, {
                'resource_kind': 'restore', 'operation_id': 'execute',
                'target_resource': tool_targets['restore'],
                'draft': {
                    'action': 'archive',
                    'arguments': {
                        'path': archive_path, 'drop': True,
                        'namespace_from': runtime.database + '.*',
                        'namespace_to': restored_database + '.*',
                    },
                    'confirmation': 'restore-database',
                },
                '_provider_route': route,
            })
            shell_result = _apply(provider, {
                'resource_kind': 'shell', 'operation_id': 'execute',
                'target_resource': tool_targets['shell'],
                'draft': {
                    'action': 'script',
                    'arguments': {
                        'database': runtime.database,
                        'script': 'return db.runCommand({ping: 1});',
                    },
                    'confirmation': 'run-mongosh-script',
                },
                '_provider_route': route,
            })
            if shell_result['provider_result']['return_code'] != 0:
                raise RuntimeError('mongosh workflow did not complete')
            runtime.admin_client().drop_database(restored_database)
            current = provider.list_resources(request)
            imported_target = next(
                item for item in current
                if item['resource_kind'] == 'collection' and
                item['display_name'] == import_collection
            )
            _apply(provider, {
                'resource_kind': 'collection', 'operation_id': 'drop',
                'target_resource': imported_target,
                'draft': {
                    'cascade': False, 'confirmation': 'drop-imported',
                },
                '_provider_route': route,
            })
            _apply(provider, {
                'resource_kind': 'collection', 'operation_id': 'drop',
                'target_resource': target,
                'draft': {
                    'cascade': False, 'confirmation': 'drop-collection',
                },
                '_provider_route': route,
            })
            _apply(provider, {
                'resource_kind': 'database', 'operation_id': 'drop',
                'target_resource': database_target,
                'draft': {
                    'cascade': False, 'confirmation': 'drop-database',
                },
                '_provider_route': route,
            })

        category('admin', admin_gate)

        def security_gate():
            descriptor = provider.describe_security(request)
            users = descriptor['extensions']['mongodb']['native'][
                'authenticated_users'
            ]
            if not any(item.get('user') == runtime.username for item in users):
                raise RuntimeError('authenticated principal is not reported')
            wrong = pymongo.MongoClient(
                host='127.0.0.1', port=runtime.port,
                username=runtime.username, password='known-wrong-password',
                authSource='admin', directConnection=True,
                serverSelectionTimeoutMS=500,
            )
            try:
                wrong.admin.command('ping')
            except Exception:
                return
            finally:
                wrong.close()
            raise RuntimeError('wrong MongoDB credential was accepted')

        category('security', security_gate)

        def fault_gate():
            if session is None:
                raise RuntimeError('result category did not open a session')
            try:
                provider.execute({
                    'session_id': session['session_id'],
                    'execution_id': 'mongodb-live-fault',
                    'source': json.dumps({
                        'operation': 'command',
                        'database': runtime.database,
                        'command': {'dropDatabase': 1},
                    }),
                })
            except Exception as exc:
                if runtime.password in str(exc):
                    raise RuntimeError('fault exposed secret material')
            else:
                raise RuntimeError('write command entered read query port')
            diagnostic = provider.validate_endpoint({
                'route': {'host': 'localhost', 'password': runtime.password}
            })
            if diagnostic['code'] != 'CDE_ACTUAL_INLINE_CREDENTIAL_FORBIDDEN':
                raise RuntimeError('inline credential was not refused')

        category('fault', fault_gate)
    except Exception as exc:
        message = str(exc)
        if runtime.password:
            message = message.replace(runtime.password, '[redacted]')
        failures.append({
            'category': 'setup', 'error_type': type(exc).__name__,
            'message': message,
        })
    finally:
        if provider is not None:
            provider.close()
        runtime.stop()

    passed = all(value == 'passed' for value in categories.values())
    return {
        'schema': 'cdeadmin.mongodb-provider-live-verification.v1',
        'engine_id': 'mongodb',
        'exact_profile': '8.2.6',
        'server_binary_sha256': _sha256(binary),
        'pymongo_version': pymongo.version,
        'pymongo_c_extension': pymongo.has_c(),
        'activation_ready': passed,
        'categories': categories,
        'failures': failures,
        'secret_access_events': len(secret_service.audit_events()),
        'credential_values_exported': False,
        'common_transaction_finality_interpreted': False,
        'temporary_account_removed': runtime.account_removed,
        'server_log': str(server_log.resolve()),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mongod', type=Path, required=True)
    parser.add_argument('--server-log', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if not args.mongod.is_file():
        parser.error('--mongod must identify an installed binary')
    result = verify(args.mongod, args.server_log)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, indent=2, sort_keys=True) + '\n',
        encoding='utf-8',
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result['activation_ready'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
