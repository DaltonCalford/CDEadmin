#!/usr/bin/env python3
##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Run the fail-closed XTDB 2.1.0 provider activation suite."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request
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

from pgadmin.cdeadmin.providers.xtdb.client import XTDBClient  # noqa: E402
from pgadmin.cdeadmin.providers.xtdb.provider import (  # noqa: E402
    XTDBPilotProvider,
)
from pgadmin.cdeadmin.semantic_models.service import (  # noqa: E402
    SemanticModelService,
)


class _Lease:
    def __init__(self, value):
        self.value = value.encode('utf-8')

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

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


def _parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=5432)
    parser.add_argument('--database', default='xtdb')
    parser.add_argument('--username', default='xtdb')
    parser.add_argument('--password-env')
    parser.add_argument('--healthz-url', default='http://127.0.0.1:8080')
    parser.add_argument('--output', type=Path)
    parser.add_argument(
        '--destructive-disposable-runtime', action='store_true',
        help='admit isolated ERASE, user, and attach/detach checks',
    )
    return parser


def _execute(client, session, source, parameters=()):
    result = client.describe_result(client.execute(session, {
        'source': source, 'parameters': list(parameters),
    }))
    return result['payload']


def main():
    args = _parser().parse_args()
    password = None
    if args.password_env:
        password = os.environ.get(args.password_env)
        if password is None:
            raise SystemExit('password environment variable is absent')

    def acquire(_reference, _principal, _purpose, _secret_type):
        return _Lease(password or '')

    route = {
        'route_id': 'xtdb-live-gate', 'host': args.host, 'port': args.port,
        'database': args.database, 'username': args.username,
        'tls_mode': 'disable', 'connect_timeout': 20,
        'statement_timeout': 60, 'application_name': 'CDEadmin-live-gate',
        'healthz_url': args.healthz_url,
    }
    if password is not None:
        route.update({
            'credential_reference_id': 'live-gate-secret',
            'principal_reference': 'live-gate-principal',
        })
    client = XTDBClient(acquire)
    evidence = {
        'schema': 'cdeadmin.xtdb-live-gate.v1',
        'expected_version': '2.1.0', 'driver_version': '3.3.4',
        'started_at': time.time(), 'categories': {},
        'automatic_mutation_retry': False,
        'common_finality_interpretation': False,
    }

    def category(name, callback):
        started = time.monotonic()
        try:
            details = callback() or {}
            evidence['categories'][name] = {
                'status': 'passed', 'details': details,
                'duration_seconds': round(time.monotonic() - started, 3),
            }
        except Exception as exc:
            evidence['categories'][name] = {
                'status': 'failed', 'exception_type': type(exc).__name__,
                'error_message': str(exc),
                'duration_seconds': round(time.monotonic() - started, 3),
            }

    category('runtime_identity', lambda: client.runtime_identity({
        'route': route
    }))

    def health():
        states = {}
        for check in ('started', 'alive', 'ready'):
            url = args.healthz_url.rstrip('/') + '/healthz/' + check
            with urllib.request.urlopen(url, timeout=10) as response:
                states[check] = {
                    'status': response.status,
                    'body': response.read(256).decode('utf-8', 'replace'),
                }
        return states

    category('health', health)

    def finish_block():
        plan = client.plan_admin_operation({
            'resource_kind': 'health',
            'operation_id': 'execute',
            'target_resource': {
                'native': {'healthz_url': args.healthz_url},
            },
            'draft': {
                'action': 'finish-block',
                'acknowledge_operation': True,
            },
            '_provider_route': route,
        })
        result = client.apply_admin_operation({
            'provider_payload': plan['provider_payload'],
        })
        if result.get('http_status') != 200:
            raise RuntimeError('XTDB finish-block did not return HTTP 200')
        return result

    category('finish_block', finish_block)
    session = None
    suffix = uuid.uuid4().hex[:12]
    table = f'cdeadmin_xtdb_{suffix}'
    attached = f'cdeadmin_db_{suffix}'
    username = f'cdeadmin_user_{suffix}'
    try:
        session = client.open_session({'route': route})
        category('sql', lambda: _execute(
            client, session, 'SELECT %s AS value', ('connected',)
        ))

        def documents():
            _execute(
                client, session,
                f'INSERT INTO "public"."{table}" '
                '(_id, name, nested, region, amount) '
                'VALUES (%s, %s, %s, %s, %s)',
                ('one', 'Ada', {'language': 'analytical engine'},
                 'North', 42.5),
            )
            payload = _execute(
                client, session,
                f'SELECT * FROM "public"."{table}" WHERE _id = %s',
                ('one',),
            )
            if not payload['rows']:
                raise RuntimeError('inserted XTDB document was not observed')
            return {'rows_observed': len(payload['rows'])}

        category('document_crud', documents)

        def bitemporal():
            _execute(
                client, session,
                f'UPDATE "public"."{table}" SET name = %s '
                'WHERE _id = %s', ('Grace', 'one'),
            )
            payload = _execute(
                client, session,
                f'SELECT *, _valid_from, _system_from '
                f'FROM "public"."{table}" FOR ALL VALID_TIME '
                'FOR ALL SYSTEM_TIME WHERE _id = %s', ('one',),
            )
            if len(payload['rows']) < 2:
                raise RuntimeError('XTDB system-time history was not observed')
            return {'history_rows_observed': len(payload['rows'])}

        category('bitemporal_history', bitemporal)

        def semantic_query():
            context = SimpleNamespace(
                endpoint_id=f'xtdb-semantic-{suffix}',
                session_namespace=f'xtdb-semantic-session-{suffix}',
                mode='legacy_native', runtime_verification_state='verified',
                verified_runtime_family='xtdb',
                declared_runtime_family='xtdb',
            )
            provider = XTDBPilotProvider(
                context, _Permissions(acquire), XTDBClient(acquire)
            )
            model = {
                'contract_version': '1.0.0',
                'name': 'XTDB live semantic model',
                'description': 'Disposable live qualification model',
                'sources': [{
                    'id': 'documents',
                    'resource_id': f'xtdb:public:{table}',
                    'relation': ['public', table], 'alias': 'documents',
                }],
                'joins': [],
                'dimensions': [{
                    'id': 'region', 'name': 'Region',
                    'field': {'source_id': 'documents', 'field': 'region'},
                    'hierarchies': [{
                        'id': 'region_hierarchy', 'name': 'Region',
                        'levels': [{
                            'id': 'region_level', 'name': 'Region',
                            'field': {
                                'source_id': 'documents', 'field': 'region',
                            },
                        }],
                    }],
                }],
                'measures': [{
                    'id': 'total_amount', 'name': 'Total amount',
                    'aggregation': 'sum',
                    'field': {'source_id': 'documents', 'field': 'amount'},
                    'format': '0',
                }],
                'default_filters': [], 'materializations': [],
                'security': {}, 'annotations': {'qualification': True},
            }
            query = {
                'axes': {
                    'rows': ['region_level'],
                    'columns': [], 'pages': [],
                },
                'measures': ['total_amount'], 'filters': [],
                'totals': False, 'limit': 100,
            }
            try:
                provider_session = provider.open_session({'route': route})
                compiled = provider.compile_semantic_query(model, query)
                operation = provider.execute_analysis({
                    'session_id': provider_session['session_id'],
                    'execution_id': f'xtdb-semantic-{suffix}',
                    'semantic_model': model, 'semantic_query': query,
                })
                described = provider.describe_result(operation)
                rows = described['extensions']['xtdb']['payload']['rows']
                if len(rows) != 1 or rows[0].get('region_level') != 'North':
                    raise RuntimeError(
                        f'XTDB semantic dimension changed: {rows!r}'
                    )
                if float(rows[0].get('total_amount')) != 42.5:
                    raise RuntimeError(
                        f'XTDB semantic aggregate changed: {rows!r}'
                    )
                cellset = SemanticModelService.cellset(model, query, rows)
                if cellset['family'] != 'cellset' or len(
                    cellset['cells']
                ) != 1:
                    raise RuntimeError(
                        'XTDB semantic rows were not preserved as a cellset'
                    )
                return {
                    'compiled_source': compiled['source'],
                    'language_profile': compiled['language_profile'],
                    'cellset_family': cellset['family'],
                    'cell_count': len(cellset['cells']),
                }
            finally:
                provider.close()

        category('semantic_query', semantic_query)

        def transaction():
            _execute(client, session, 'BEGIN READ ONLY')
            _execute(client, session, 'SELECT 1 AS value')
            _execute(client, session, 'COMMIT')
            state = client.describe_transaction(session)
            if state['finality_interpreted_by_common_code']:
                raise RuntimeError('common code interpreted XTDB finality')
            return state

        category('transaction_observation', transaction)
        category('resources', lambda: {
            'resource_count': len(client.list_resources({'route': route})),
            'security': client.describe_security({'route': route})['native'],
        })

        if args.destructive_disposable_runtime:
            def databases():
                _execute(client, session, f'ATTACH DATABASE "{attached}"')
                _execute(client, session, f'DETACH DATABASE "{attached}"')
                return {'attach_and_detach_observed': True}

            category('multi_database', databases)

            def users():
                _execute(
                    client, session,
                    f'CREATE USER {username} WITH PASSWORD '
                    "'temporary-live-gate-password'",
                )
                _execute(
                    client, session,
                    f'ALTER USER {username} WITH PASSWORD '
                    "'temporary-live-gate-password-two'",
                )
                observed = _execute(
                    client, session,
                    'SELECT username, usesuper FROM pg_catalog.pg_user '
                    'WHERE username = %s', (username,),
                )
                if len(observed['rows']) != 1 or (
                    observed['rows'][0].get('username') != username
                ):
                    raise RuntimeError(
                        'XTDB user name was not stored exactly as submitted'
                    )
                return {
                    'create_and_alter_observed': True,
                    'exact_username_observed': True,
                    'password_recorded': False,
                }

            category('user_administration', users)
        else:
            for name in ('multi_database', 'user_administration'):
                evidence['categories'][name] = {
                    'status': 'blocked',
                    'reason': 'disposable runtime admission was not supplied',
                }
    finally:
        if session is not None and args.destructive_disposable_runtime:
            category('isolated_cleanup', lambda: _execute(
                client, session,
                f'ERASE FROM "public"."{table}" WHERE _id = %s', ('one',),
            ))
        client.close()

    required = {
        'runtime_identity', 'health', 'finish_block', 'sql', 'document_crud',
        'bitemporal_history', 'semantic_query', 'transaction_observation',
        'resources',
        'multi_database', 'user_administration', 'isolated_cleanup',
    }
    evidence['completed_at'] = time.time()
    evidence['passed'] = all(
        evidence['categories'].get(name, {}).get('status') == 'passed'
        for name in required
    )
    document = json.dumps(
        evidence, indent=2, sort_keys=True, default=str
    ) + '\n'
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(document, encoding='utf-8')
    print(document, end='')
    return 0 if evidence['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
