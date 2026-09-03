#!/usr/bin/env python3
##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Qualify the MongoDB provider's exact TLS and authentication route."""

from __future__ import annotations

import argparse
import json
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


def _port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as handle:
        handle.bind(('127.0.0.1', 0))
        return handle.getsockname()[1]


class _TLSRuntime:
    def __init__(self, mongod, log):
        self.mongod = mongod.resolve()
        self.log = log.resolve()
        self.temporary = tempfile.TemporaryDirectory(
            prefix='cdeadmin-mongodb-tls-826-'
        )
        self.root = Path(self.temporary.name).resolve()
        self.dbpath = self.root / 'data'
        self.port = _port()
        self.username = 'cdeadmin_tls_' + secrets.token_hex(5)
        self.password = secrets.token_urlsafe(48)
        self.database = 'cdeadmin_tls_working'
        self.process = None
        self.ca = self.root / 'ca.crt'
        self.server_pem = self.root / 'server.pem'
        self.invalid_ca = self.root / 'invalid-ca.crt'

    def certificates(self):
        ca_key = self.root / 'ca.key'
        server_key = self.root / 'server.key'
        server_csr = self.root / 'server.csr'
        server_crt = self.root / 'server.crt'
        invalid_key = self.root / 'invalid-ca.key'
        commands = (
            ['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes',
             '-keyout', str(ca_key), '-out', str(self.ca), '-days', '2',
             '-subj', '/CN=CDEadmin MongoDB Test CA'],
            ['openssl', 'req', '-newkey', 'rsa:2048', '-nodes', '-keyout',
             str(server_key), '-out', str(server_csr), '-subj',
             '/CN=localhost', '-addext',
             'subjectAltName=DNS:localhost,IP:127.0.0.1'],
            ['openssl', 'x509', '-req', '-in', str(server_csr), '-CA',
             str(self.ca), '-CAkey', str(ca_key), '-CAcreateserial', '-out',
             str(server_crt), '-days', '2', '-copy_extensions', 'copy'],
            ['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes',
             '-keyout', str(invalid_key), '-out', str(self.invalid_ca),
             '-days', '2', '-subj', '/CN=Untrusted Test CA'],
        )
        for command in commands:
            subprocess.run(
                command, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, check=True,
            )
        self.server_pem.write_bytes(
            server_crt.read_bytes() + server_key.read_bytes()
        )
        for path in (ca_key, server_key, self.server_pem, invalid_key):
            path.chmod(0o600)

    def _command(self, authenticated):
        command = [
            str(self.mongod), '--dbpath', str(self.dbpath), '--bind_ip',
            '127.0.0.1', '--port', str(self.port), '--logpath',
            str(self.log), '--logappend', '--tlsMode', 'requireTLS',
            '--tlsCertificateKeyFile', str(self.server_pem), '--tlsCAFile',
            str(self.ca), '--setParameter', 'enableTestCommands=0',
        ]
        if authenticated:
            command.append('--auth')
        return command

    def start_process(self, authenticated):
        self.process = subprocess.Popen(
            self._command(authenticated), stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError('TLS mongod exited during startup')
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
                probe.settimeout(0.2)
                if probe.connect_ex(('127.0.0.1', self.port)) == 0:
                    return
            time.sleep(0.2)
        raise RuntimeError('TLS mongod did not open its socket')

    def client(self, authenticated):
        import pymongo
        values = {
            'host': '127.0.0.1', 'port': self.port, 'tls': True,
            'tlsCAFile': str(self.ca),
            'tlsCertificateKeyFile': str(self.server_pem),
            'serverSelectionTimeoutMS': 10000,
        }
        if authenticated:
            values.update({
                'username': self.username, 'password': self.password,
                'authSource': 'admin',
            })
        client = pymongo.MongoClient(**values)
        client.admin.command('ping')
        return client

    def stop_process(self):
        if self.process is None:
            return
        self.process.terminate()
        try:
            self.process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=10)
        self.process = None

    def start(self):
        self.dbpath.mkdir(parents=True)
        self.log.parent.mkdir(parents=True, exist_ok=True)
        self.certificates()
        self.start_process(False)
        client = self.client(False)
        client.admin.command({
            'createUser': self.username, 'pwd': self.password,
            'roles': [{'role': 'root', 'db': 'admin'}],
        })
        client[self.database].qualification.insert_one({'tls': True})
        client.close()
        self.stop_process()
        self.start_process(True)
        self.client(True).close()

    def stop(self):
        self.stop_process()
        self.password = ''
        self.temporary.cleanup()


def _context():
    endpoint = str(uuid.uuid5(
        uuid.NAMESPACE_URL, 'cdeadmin-live:mongodb:8.2.6:tls'
    ))

    def child(name):
        return str(uuid.uuid5(uuid.UUID(endpoint), name))

    permissions = frozenset({
        'network', 'secret_read', 'data_read', 'data_write', 'administer',
        'execute',
    })
    return EndpointContext(
        endpoint_id=endpoint, mode='legacy_native',
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
        runtime_evidence_reference='cde-mongodb-tls-live:8.2.6',
        runtime_identity_generation='mongodb-8.2.6-tls-live',
    )


def verify(mongod, log):
    runtime = _TLSRuntime(mongod, log)
    context = _context()
    secrets_service = EndpointSecretService()
    reference = str(uuid.uuid5(uuid.UUID(context.endpoint_id), 'password'))
    failures = []
    checks = {
        'trusted_tls': False, 'separate_auth_source': False,
        'client_certificate': False, 'invalid_ca_refused': False,
        'secret_redacted': False,
    }
    provider = None
    try:
        runtime.start()
        secrets_service.register_resolver(
            'tls.ephemeral', lambda *_args: runtime.password.encode()
        )
        secrets_service.register_reference(SecretReference(
            reference_id=reference, endpoint_id=context.endpoint_id,
            endpoint_mode=context.mode, secret_kind='database_password',
            storage_kind='ephemeral_test_account',
            resolver_id='tls.ephemeral', locator='tls-root',
            allowed_purposes=frozenset({'connect', 'administer'}),
            authority_scope='legacy_engine_auth',
        ))
        scopes = {
            'network': {'endpoint'}, 'secret_read': {'endpoint'},
            'data_read': {'endpoint', 'resource'},
            'data_write': {'endpoint', 'resource'},
            'administer': {'endpoint', 'resource'}, 'execute': {'endpoint'},
        }
        permissions = PermissionGuard(
            {name: PermissionGrant(name, frozenset(value))
             for name, value in scopes.items()},
            context.effective_permissions, context=context,
            secret_service=secrets_service,
        )
        provider = create_provider(context, permissions)
        route = {
            'route_id': 'tls-live', 'host': '127.0.0.1',
            'port': runtime.port, 'database': runtime.database,
            'username': runtime.username, 'auth_source': 'admin',
            'credential_reference_id': reference,
            'principal_reference': 'tls-live', 'tls': True,
            'tls_ca_file': str(runtime.ca),
            'tls_certificate_key_file': str(runtime.server_pem),
            'connect_timeout_ms': 5000,
            'server_selection_timeout_ms': 5000,
            'socket_timeout_ms': 10000,
        }
        identity = provider.discover_endpoint({'route': route})[
            'verified_runtime'
        ]
        checks['trusted_tls'] = identity['version'] == '8.2.6'
        resources = provider.list_resources({'route': route})
        checks['separate_auth_source'] = any(
            item['resource_kind'] == 'database' and
            item['display_name'] == runtime.database
            for item in resources
        )
        checks['client_certificate'] = True
        bad_route = {**route, 'tls_ca_file': str(runtime.invalid_ca)}
        try:
            provider.discover_endpoint({'route': bad_route})
        except Exception as exc:
            value = str(exc)
            checks['invalid_ca_refused'] = True
            checks['secret_redacted'] = runtime.password not in value
        else:
            raise RuntimeError('untrusted MongoDB CA was accepted')
    except Exception as exc:
        message = str(exc)
        if runtime.password:
            message = message.replace(runtime.password, '[redacted]')
        failures.append({
            'error_type': type(exc).__name__, 'message': message,
        })
    finally:
        if provider is not None:
            provider.close()
        runtime.stop()
    return {
        'schema': 'cdeadmin.mongodb-tls-live-verification.v1',
        'engine_id': 'mongodb', 'exact_profile': '8.2.6',
        'activation_ready': all(checks.values()) and not failures,
        'checks': checks, 'failures': failures,
        'secret_values_exported': False,
        'server_log': str(log.resolve()),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mongod', type=Path, required=True)
    parser.add_argument('--server-log', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
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
