#!/usr/bin/env python3
##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Run seven CDEadmin provider categories against an exact live endpoint.

The selected endpoint is treated only as its advertised engine profile. A
temporary least-privilege account is provisioned through the endpoint's local
administrative socket, used through an endpoint-scoped secret lease, and
removed before exit. Credential values are never written to result evidence.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
import tempfile
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
from pgadmin.cdeadmin.providers.mysql_family.provider import (  # noqa: E402
    MARIADB_PROFILE,
    MYSQL_PROFILE,
    create_provider,
)
from pgadmin.cdeadmin.providers.duckdb.provider import (  # noqa: E402
    PROFILE as DUCKDB_PROFILE,
    create_provider as create_duckdb_provider,
)
from pgadmin.cdeadmin.providers.firebird.provider import (  # noqa: E402
    PROFILE as FIREBIRD_PROFILE,
    create_provider as create_firebird_provider,
)
from pgadmin.cdeadmin.providers.sqlite.provider import (  # noqa: E402
    PROFILE as SQLITE_PROFILE,
    create_provider as create_sqlite_provider,
)
from pgadmin.cdeadmin.security import (  # noqa: E402
    EndpointSecretService,
    SecretReference,
)


CATEGORIES = (
    'resource', 'language_api', 'result', 'semantic_query', 'transaction',
    'admin', 'security', 'fault',
)
PROFILES = {
    'mysql': MYSQL_PROFILE,
    'mariadb': MARIADB_PROFILE,
    'duckdb': DUCKDB_PROFILE,
    'firebird': FIREBIRD_PROFILE,
    'sqlite': SQLITE_PROFILE,
}

PROVIDER_FACTORIES = {
    'duckdb': create_duckdb_provider,
    'firebird': create_firebird_provider,
    'sqlite': create_sqlite_provider,
}

TARGET_ADAPTERS = {
    'duckdb': 'embedded-duckdb-helper',
    'firebird': 'firebird-wire-client',
    'mariadb': 'mysql-wire-client',
    'mysql': 'mysql-wire-client',
    'sqlite': 'embedded-sqlite-client',
}


def _context(profile):
    endpoint_id = str(uuid.uuid5(
        uuid.NAMESPACE_URL,
        f'cdeadmin-live:{profile.engine_id}:{profile.exact_version}',
    ))

    def child(purpose):
        return str(uuid.uuid5(uuid.UUID(endpoint_id), purpose))

    permissions = frozenset({
        *profile.required_permissions, 'secret_read', 'data_read',
        'data_write', 'administer', 'execute',
    })
    return EndpointContext(
        endpoint_id=endpoint_id,
        mode='legacy_native',
        experience_family=profile.engine_id,
        provider_id=profile.provider_id,
        provider_version='0.1.0',
        profile_id=profile.profile_id,
        profile_version=profile.exact_version,
        target_adapter_id=TARGET_ADAPTERS[profile.engine_id],
        target_adapter_version='live-qualification',
        pool_namespace=child('pool'),
        session_namespace=child('session'),
        cache_namespace=child('cache'),
        diagnostic_namespace=child('diagnostic'),
        effective_permissions=permissions,
        declared_runtime_family=profile.engine_id,
    )


def _permissions(context, secret_service):
    scopes = {
        'network': {'endpoint'},
        'embedded_runtime': {'endpoint'},
        'filesystem': {'endpoint', 'resource'},
        'secret_read': {'endpoint'},
        'data_read': {'endpoint', 'resource'},
        'data_write': {'endpoint', 'resource'},
        'administer': {'endpoint', 'resource'},
        'execute': {'endpoint'},
    }
    grants = {
        name: PermissionGrant(name, frozenset(values))
        for name, values in scopes.items()
    }
    return PermissionGuard(
        grants,
        context.effective_permissions,
        context=context,
        secret_service=secret_service,
    )


class _TemporaryAccount:
    def __init__(self, engine, socket_path):
        self.engine = engine
        self.socket_path = socket_path
        suffix = secrets.token_hex(6)
        self.username = f'cde_live_{suffix}'
        self.database = f'cde_live_{suffix}'
        self.password = secrets.token_urlsafe(32)
        self.connection = None

    @property
    def marker(self):
        return '%s' if self.engine == 'mysql' else '?'

    @property
    def quoted_database(self):
        quote = chr(96)
        return f'{quote}{self.database}{quote}'

    def _connect(self):
        if self.engine == 'mysql':
            import mysql.connector
            return mysql.connector.connect(
                user='root', unix_socket=self.socket_path,
                autocommit=True,
            )
        import mariadb
        return mariadb.connect(
            user='root', unix_socket=self.socket_path,
            autocommit=True,
        )

    def create(self):
        self.connection = self._connect()
        cursor = self.connection.cursor()
        database = self.quoted_database
        try:
            cursor.execute(f'CREATE DATABASE {database}')
            cursor.execute(
                f'CREATE TABLE {database}.qualification '
                '(value INTEGER NOT NULL)'
            )
            cursor.execute(
                f'INSERT INTO {database}.qualification VALUES (42)'
            )
            cursor.execute(
                f"CREATE USER '{self.username}'@'127.0.0.1' "
                f'IDENTIFIED BY {self.marker}',
                (self.password,),
            )
            cursor.execute(
                f"GRANT SELECT ON {database}.* TO "
                f"'{self.username}'@'127.0.0.1'"
            )
        except Exception:
            self.drop()
            raise
        finally:
            cursor.close()

    def drop(self):
        connection = self.connection
        if connection is None:
            try:
                connection = self._connect()
            except Exception:
                return
        cursor = connection.cursor()
        try:
            cursor.execute(
                f"DROP USER IF EXISTS '{self.username}'@'127.0.0.1'"
            )
            cursor.execute(
                f'DROP DATABASE IF EXISTS {self.quoted_database}'
            )
        finally:
            cursor.close()
            connection.close()
            self.connection = None
            self.password = ''


class _FirebirdAccount:
    def __init__(self, host, port, database, admin_user, admin_password):
        suffix = secrets.token_hex(6).upper()
        self.host = host
        self.port = port
        self.database = str(Path(database).resolve())
        self.admin_user = admin_user
        self.admin_password = admin_password
        self.username = f'CDE_LIVE_{suffix}'
        self.password = secrets.token_hex(24)
        self.created = False

    @property
    def dsn(self):
        return f'{self.host}/{self.port}:{self.database}'

    def _admin_connect(self):
        from firebird.driver import connect

        return connect(
            self.dsn, user=self.admin_user, password=self.admin_password
        )

    def create(self):
        from firebird.driver import create_database

        connection = create_database(
            self.dsn, user=self.admin_user, password=self.admin_password,
            overwrite=True,
        )
        cursor = connection.cursor()
        try:
            cursor.execute(
                'CREATE TABLE QUALIFICATION '
                '(QUALIFICATION_VALUE INTEGER NOT NULL)'
            )
            connection.commit()
            cursor.execute('INSERT INTO QUALIFICATION VALUES (?)', (42,))
            connection.commit()
            cursor.execute(
                f"CREATE USER {self.username} PASSWORD '{self.password}'"
            )
            connection.commit()
            cursor.execute(
                f'GRANT SELECT ON QUALIFICATION TO USER {self.username}'
            )
            connection.commit()
            self.created = True
        finally:
            cursor.close()
            connection.close()

    def drop(self):
        try:
            connection = self._admin_connect()
        except Exception:
            self.password = ''
            return
        cursor = connection.cursor()
        try:
            if self.created:
                try:
                    cursor.execute(f'DROP USER {self.username}')
                    connection.commit()
                except Exception:
                    connection.rollback()
            connection.drop_database()
        finally:
            try:
                cursor.close()
            except Exception:
                pass
            try:
                connection.close()
            except Exception:
                pass
            self.password = ''


def _result_payload(provider, operation, engine):
    result = provider.describe_result(operation)
    if not result['complete'] or result['result_kind'] != (
        provider.profile.result_kind
    ):
        raise RuntimeError('live result envelope is incomplete')
    payload = result['extensions'][engine]['payload']
    rows = payload.get('rows') or []
    if not rows or int(rows[0][0]) != 42:
        raise RuntimeError('live result value did not round trip')
    return result


def _semantic_payload(provider, session, engine):
    """Exercise semantic discovery, compilation, execution and cellsets."""
    from pgadmin.cdeadmin.semantic_models.service import SemanticModelService

    model = {
        'contract_version': '1.0.0',
        'name': 'Live qualification model',
        'description': 'Disposable provider semantic-query qualification',
        'sources': [{
            'id': 'qualification',
            'resource_id': f'{engine}:qualification',
            'relation': ['QUALIFICATION'] if engine == 'firebird' else [
                'qualification'
            ],
            'alias': 'qualification',
        }],
        'joins': [],
        'dimensions': [],
        'measures': [{
            'id': 'row_count', 'name': 'Row count',
            'aggregation': 'count', 'field': None, 'format': '0',
        }],
        'default_filters': [],
        'materializations': [],
        'security': {},
        'annotations': {'qualification': True},
    }
    query = {
        'axes': {'rows': [], 'columns': [], 'pages': []},
        'measures': ['row_count'], 'filters': [],
        'totals': False, 'limit': 10,
    }
    descriptor = provider.semantic_model_descriptor()
    if not descriptor.get('execution_available'):
        raise RuntimeError('provider semantic execution is not activated')
    compiled = provider.compile_semantic_query(model, query)
    if not compiled.get('source') or not compiled.get('language_profile'):
        raise RuntimeError('provider semantic compiler returned no query')
    operation = provider.execute_analysis({
        'session_id': session['session_id'],
        'execution_id': f'{engine}-live-semantic-result',
        'semantic_model': model,
        'semantic_query': query,
    })
    result = provider.describe_result(operation)
    payload = result['extensions'][engine]['payload']
    rows = payload.get('rows') or []
    if not rows or int(rows[0][0]) != 1:
        raise RuntimeError('semantic aggregate did not round trip')
    cellset = SemanticModelService.cellset(
        model, query, [{'row_count': int(rows[0][0])}]
    )
    if cellset['family'] != 'cellset' or (
        cellset['cells'][0]['measures']['row_count'] != 1
    ):
        raise RuntimeError('semantic result was not preserved as a cellset')
    return {
        'language_profile': compiled['language_profile'],
        'compiled_source': compiled['source'],
        'cellset_family': cellset['family'],
        'observed_row_count': 1,
    }


def _embedded_route(engine, root, database):
    return {
        'route_id': 'exact-live-qualification',
        'database': str(database),
        'filesystem_root': str(root),
    }


def _prepare_embedded(engine, database):
    if engine == 'duckdb':
        import duckdb

        module = duckdb
        runtime = duckdb.__version__
    else:
        import sqlite3

        module = sqlite3
        runtime = sqlite3.sqlite_version
    expected = PROFILES[engine].exact_version
    if runtime != expected:
        raise RuntimeError(
            f'{engine} embedded runtime is not exact ({runtime})'
        )
    connection = module.connect(str(database))
    try:
        cursor = connection.cursor()
        try:
            cursor.execute(
                'CREATE TABLE qualification (value INTEGER NOT NULL)'
            )
            cursor.execute('INSERT INTO qualification VALUES (?)', (42,))
            connection.commit()
        finally:
            cursor.close()
    finally:
        connection.close()


def verify_embedded(engine):
    profile = PROFILES[engine]
    context = _context(profile)
    categories = {name: 'not_run' for name in CATEGORIES}
    error_type = None
    error_message = None
    provider = None
    secret_service = EndpointSecretService()
    removed = False
    try:
        with tempfile.TemporaryDirectory(
            prefix=f'cdeadmin-{engine}-live-'
        ) as temporary:
            root = Path(temporary).resolve()
            suffix = 'duckdb' if engine == 'duckdb' else 'sqlite'
            database = root / f'qualification.{suffix}'
            _prepare_embedded(engine, database)
            route = _embedded_route(engine, root, database)
            request = {
                'route': route,
                'capability_generation': 'exact-live-qualification',
            }
            provider = PROVIDER_FACTORIES[engine](
                context, _permissions(context, secret_service)
            )
            discovered = provider.discover_endpoint(request)
            if discovered['verified_runtime']['version'] != (
                profile.exact_version
            ):
                raise RuntimeError('exact runtime identity was not verified')

            resources = provider.list_resources(request)
            if not any(
                item['resource_kind'] == 'table' and
                item['display_name'] == 'qualification'
                for item in resources
            ):
                raise RuntimeError('live resource category found no table')
            categories['resource'] = 'passed'

            languages = provider.describe_language({})
            if languages[0]['display_name'] != profile.language_name:
                raise RuntimeError(
                    'live language category did not match profile'
                )
            categories['language_api'] = 'passed'

            session = provider.open_session(request)
            operation = provider.execute({
                'session_id': session['session_id'],
                'execution_id': f'{engine}-live-result',
                'source': 'SELECT ? AS value',
                'parameters': (42,),
            })
            _result_payload(provider, operation, engine)
            categories['result'] = 'passed'

            _semantic_payload(provider, session, engine)
            categories['semantic_query'] = 'passed'

            transaction = provider.describe_transaction(session)
            presentation = transaction['provider_payload']
            if presentation.get(
                'finality_interpreted_by_common_code'
            ) is not False:
                raise RuntimeError(
                    'common code interpreted transaction finality'
                )
            if presentation.get('driver_observation_only') is not True:
                raise RuntimeError('transaction observation is not opaque')
            categories['transaction'] = 'passed'

            descriptor = provider.visual_admin_descriptor()
            if not descriptor.get('objects'):
                raise RuntimeError('visual administration is unavailable')
            if len(provider.list_tools({})) != len(profile.admin_tools):
                raise RuntimeError('live admin tool catalog is incomplete')
            categories['admin'] = 'passed'

            security = provider.describe_security(request)
            if security['resource_kind'] != 'security-descriptor':
                raise RuntimeError('live security descriptor is invalid')
            categories['security'] = 'passed'

            invalid_failed = False
            try:
                provider.execute({
                    'session_id': session['session_id'],
                    'execution_id': f'{engine}-live-fault',
                    'source': 'SELECT * FROM cdeadmin_live_missing_object',
                })
            except Exception:
                invalid_failed = True
            escape_failed = False
            escape_request = {
                'route': _embedded_route(
                    engine, root, root.parent / f'escape.{suffix}'
                )
            }
            try:
                provider.discover_endpoint(escape_request)
            except Exception:
                escape_failed = True
            if not invalid_failed or not escape_failed:
                raise RuntimeError(
                    'embedded fault or filesystem escape did not fail'
                )
            categories['fault'] = 'passed'
        removed = not database.exists()
    except Exception as exc:
        error_type = type(exc).__name__
        error_message = str(exc)
    finally:
        if provider is not None:
            provider.close()

    passed = all(value == 'passed' for value in categories.values())
    return {
        'schema': 'cdeadmin.relational-provider-live-verification.v1',
        'engine_id': engine,
        'exact_profile': profile.exact_version,
        'activation_ready': passed,
        'categories': categories,
        'secret_access_events': len(secret_service.audit_events()),
        'credential_values_exported': False,
        'common_transaction_finality_interpreted': False,
        'temporary_database_removed': removed,
        'filesystem_escape_refused': categories['fault'] == 'passed',
        'error_type': error_type,
        'error_message': error_message,
    }


def verify(engine, host, port, socket_path=None, account=None):
    profile = PROFILES[engine]
    context = _context(profile)
    account = account or _TemporaryAccount(engine, socket_path)
    provider = None
    categories = {name: 'not_run' for name in CATEGORIES}
    error_type = None
    error_message = None
    secret_service = EndpointSecretService()
    reference_id = str(uuid.uuid5(
        uuid.UUID(context.endpoint_id), 'database-password'
    ))
    try:
        account.create()
        secret_service.register_resolver(
            'live.ephemeral',
            lambda *_args: account.password.encode('utf-8'),
        )
        secret_service.register_reference(SecretReference(
            reference_id=reference_id,
            endpoint_id=context.endpoint_id,
            endpoint_mode=context.mode,
            secret_kind='database_password',
            storage_kind='ephemeral_test_account',
            resolver_id='live.ephemeral',
            locator=f'ephemeral:{engine}:qualification',
            allowed_purposes=frozenset({'connect'}),
            authority_scope='legacy_engine_auth',
        ))
        factory = PROVIDER_FACTORIES.get(engine, create_provider)
        provider = factory(context, _permissions(context, secret_service))
        route = {
            'route_id': 'exact-live-qualification',
            'host': host,
            'port': port,
            'user': account.username,
            'database': account.database,
            'credential_reference_id': reference_id,
            'principal_reference': 'cdeadmin-live-qualifier',
            'connection_timeout': 10,
        }
        request = {
            'route': route,
            'capability_generation': 'exact-live-qualification',
        }
        discovered = provider.discover_endpoint(request)
        if discovered['verified_runtime']['version'] != profile.exact_version:
            raise RuntimeError('exact runtime identity was not verified')

        resources = provider.list_resources(request)
        if not any(
            item['resource_kind'] == 'table' for item in resources
        ):
            raise RuntimeError('live resource category found no table')
        categories['resource'] = 'passed'

        languages = provider.describe_language({})
        if languages[0]['display_name'] != profile.language_name:
            raise RuntimeError('live language category did not match profile')
        categories['language_api'] = 'passed'

        session = provider.open_session(request)
        marker = '%s' if engine == 'mysql' else '?'
        source = f'SELECT {marker} AS value'
        if engine == 'firebird':
            source = (
                'SELECT CAST(? AS INTEGER) AS QUALIFICATION_VALUE '
                'FROM RDB$DATABASE'
            )
        operation = provider.execute({
            'session_id': session['session_id'],
            'execution_id': f'{engine}-live-result',
            'source': source,
            'parameters': (42,),
        })
        _result_payload(provider, operation, engine)
        categories['result'] = 'passed'

        _semantic_payload(provider, session, engine)
        categories['semantic_query'] = 'passed'

        transaction = provider.describe_transaction(session)
        presentation = transaction['provider_payload']
        common_finality = presentation.get(
            'finality_interpreted_by_common_code'
        )
        if common_finality is not False:
            raise RuntimeError('common code interpreted transaction finality')
        if presentation.get('driver_observation_only') is not True:
            raise RuntimeError('transaction observation is not opaque')
        categories['transaction'] = 'passed'

        if len(provider.list_tools({})) != len(profile.admin_tools):
            raise RuntimeError('live admin tool catalog is incomplete')
        categories['admin'] = 'passed'

        security = provider.describe_security(request)
        if security['resource_kind'] != 'security-descriptor':
            raise RuntimeError('live security descriptor is invalid')
        categories['security'] = 'passed'

        try:
            provider.execute({
                'session_id': session['session_id'],
                'execution_id': f'{engine}-live-fault',
                'source': 'SELECT * FROM cdeadmin_live_missing_object',
            })
        except Exception as exc:
            if account.password and account.password in str(exc):
                raise RuntimeError('provider fault exposed secret material')
            diagnostic = provider.translate_diagnostic({
                'code': 'CDE_RELATIONAL_LIVE_EXPECTED_FAULT',
                'exception_type': type(exc).__name__,
                'retryable': False,
            })
            if account.password and account.password in json.dumps(diagnostic):
                raise RuntimeError('translated diagnostic exposed secret')
        else:
            raise RuntimeError('invalid live query did not fail')
        categories['fault'] = 'passed'
    except Exception as exc:
        error_type = type(exc).__name__
        error_message = str(exc)
        if account.password:
            error_message = error_message.replace(
                account.password, '[redacted]'
            )
    finally:
        if provider is not None:
            provider.close()
        account.drop()

    passed = all(value == 'passed' for value in categories.values())
    return {
        'schema': 'cdeadmin.relational-provider-live-verification.v1',
        'engine_id': engine,
        'exact_profile': profile.exact_version,
        'activation_ready': passed,
        'categories': categories,
        'secret_access_events': len(secret_service.audit_events()),
        'credential_values_exported': False,
        'common_transaction_finality_interpreted': False,
        'temporary_account_removed': account.password == '',
        'error_type': error_type,
        'error_message': error_message,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--engine', choices=sorted(PROFILES), required=True)
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int)
    parser.add_argument('--admin-socket')
    parser.add_argument('--admin-user', default='SYSDBA')
    parser.add_argument('--admin-password-env')
    parser.add_argument('--database')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.engine in {'duckdb', 'sqlite'}:
        result = verify_embedded(args.engine)
    elif args.engine == 'firebird':
        if (
            args.port is None or not args.database or
            not args.admin_password_env
        ):
            parser.error(
                '--port, --database, and --admin-password-env are required '
                'for Firebird'
            )
        admin_password = os.environ.get(args.admin_password_env)
        if not admin_password:
            parser.error('Firebird admin password environment is empty')
        account = _FirebirdAccount(
            args.host, args.port, args.database,
            args.admin_user, admin_password,
        )
        result = verify(
            args.engine, args.host, args.port, account=account
        )
    else:
        if args.port is None or not args.admin_socket:
            parser.error(
                '--port and --admin-socket are required for MySQL/MariaDB'
            )
        result = verify(
            args.engine, args.host, args.port, args.admin_socket
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, indent=2, sort_keys=True) + '\n',
        encoding='utf-8',
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result['activation_ready'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
