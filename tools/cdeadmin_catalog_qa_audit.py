#!/usr/bin/env python3
"""Read live provider catalogs and walk every navigator presentation branch.

Run one demo at a time; no seed, DDL, or data-edit operations are issued.
Output intentionally excludes credentials and raw native properties.
"""
import argparse
import importlib
import importlib.util
import json
import sys
import uuid
import hashlib
import traceback
from dataclasses import replace
from collections import Counter
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'web'))
if 'pgadmin' not in sys.modules:
    package = ModuleType('pgadmin')
    package.__path__ = [str(ROOT / 'web/pgadmin')]
    sys.modules['pgadmin'] = package

from pgadmin.cdeadmin.core import EndpointContext  # noqa: E402
from pgadmin.cdeadmin.providers import BUILTIN_PACKAGES  # noqa: E402
from pgadmin.cdeadmin.endpoints.profiles import (  # noqa: E402
    registration_profiles, provider_route_options,
)
from pgadmin.cdeadmin.navigator import (  # noqa: E402
    resource_children, resource_native, _prepared_resources,
)
from pgadmin.cdeadmin.contracts.v1.runtime import (  # noqa: E402
    validate_contract,
)


class CatalogConnection:
    """Observe suppressed catalog-query failures without changing results."""
    def __init__(self, connection, failures):
        self.connection, self.failures = connection, failures

    def __getattr__(self, key):
        return getattr(self.connection, key)

    def cursor(self, *args, **kwargs):
        cursor = self.connection.cursor(*args, **kwargs)
        failures = self.failures

        class Cursor:
            def __getattr__(self, key):
                return getattr(cursor, key)

            def execute(self, source, *args, **kwargs):
                try:
                    return cursor.execute(source, *args, **kwargs)
                except Exception as exc:
                    failures.append({
                        'query_sha256': hashlib.sha256(
                            str(source).encode()).hexdigest(),
                        'error_type': type(exc).__name__,
                        'error_code': str(getattr(exc, 'errno', '')),
                        'sqlstate': str(getattr(exc, 'sqlstate', '')),
                        'source_stack': [
                            {'file': str(Path(frame.filename).relative_to(
                                ROOT)), 'line': frame.lineno}
                            for frame in traceback.extract_stack()[:-1]
                            if Path(frame.filename).is_relative_to(ROOT)
                        ][-5:],
                    })
                    raise
        return Cursor()


class Lease:
    def __init__(self, value):
        self.value = bytearray(value.encode())

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.value[:] = b'\x00' * len(self.value)

    def use(self, callback):
        return callback(memoryview(self.value))


class Permissions:
    def __init__(self, password):
        self.password = password

    def require(self, permission, scope='endpoint'):
        if permission not in {'network', 'filesystem', 'embedded_runtime',
                              'secret_read', 'data_read', 'execute'}:
            raise PermissionError(permission)

    def allows(self, permission, scope='endpoint'):
        return permission in {'network', 'filesystem', 'embedded_runtime',
                              'secret_read', 'data_read', 'execute'}

    def acquire_secret(self, *_args, **_kwargs):
        return Lease(self.password)


def walk(resources, database):
    pending = [({'scope': 'database', 'database': str(database)}, [])]
    visited, reached, branches, errors = set(), set(), [], []
    while pending:
        state, labels = pending.pop()
        token = json.dumps(state, sort_keys=True)
        if token in visited:
            errors.append('repeated navigator state: ' + '/'.join(labels))
            continue
        visited.add(token)
        if len(visited) > 20000:
            errors.append('navigator traversal exceeded 20000 branches')
            break
        try:
            nodes = resource_children(resources, state)
        except Exception as exc:
            errors.append(type(exc).__name__ + ': ' + str(exc))
            continue
        for node in nodes:
            path = [*labels, node['label']]
            branches.append({'path': path, 'type': node['node_type'],
                             'kind': node['resource_kind'],
                             'resource_id': node.get('resource_id')})
            child = {key: state[key] for key in
                     ('database', 'object_scope') if key in state}
            if node['node_type'] == 'group':
                child.update(scope=node.get('scope', 'kind'),
                             resource_kind=node['resource_kind'],
                             parent_path=node['parent_path'])
                if node.get('object_scope'):
                    child['object_scope'] = node['object_scope']
                if state.get('resource_id'):
                    child['resource_id'] = state['resource_id']
            else:
                reached.add(node['resource_id'])
                child.update(scope='resource',
                             resource_id=node['resource_id'],
                             parent_path=node['display_path'])
            if node['has_children']:
                pending.append((child, path))
    eligible = {r['resource_id'] for r in _prepared_resources(
        resources, {'scope': 'database', 'database': str(database)})}
    placements = Counter(node['resource_id'] for node in branches
                         if node.get('resource_id'))
    return {'branches': branches, 'errors': errors,
            'multiply_presented_resource_ids': sorted(
                identity for identity, count in placements.items()
                if count > 1),
            'unreachable_resource_ids': sorted(eligible - reached),
            'reached_count': len(reached)}


def native_demo_route(engine, item, host):
    """Explicit local-demo routes, separate from registration-default testing.

    TLS is disabled ONLY for this loopback demo pass. These values must never
    become remote-server registration defaults.
    """
    if engine in {'sqlite', 'duckdb'}:
        return {'database': item['database'],
                'filesystem_root': str(Path(item['database']).parent)}
    route = {'host': host, 'port': item['port']}
    if engine not in {'apache_ignite', 'cassandra', 'yugabytedb_ycql',
                      'foundationdb', 'tikv', 'opensearch',
                      'opensearch_sql_ppl'}:
        route['database'] = item['database']
    if item.get('user'):
        key = 'user' if engine in {
            'firebird', 'mysql', 'mariadb', 'cockroachdb', 'dolt',
            'immudb', 'postgresql', 'tidb', 'vitess', 'yugabytedb',
        } else 'username'
        route[key] = item['user']
    if engine in {'clickhouse', 'xtdb', 'influxdb', 'opensearch',
                  'opensearch_sql_ppl', 'milvus'}:
        route['tls_mode'] = 'disable'
    if engine in {'cockroachdb', 'postgresql', 'yugabytedb'}:
        route['sslmode'] = 'disable'
    if engine == 'neo4j':
        route.update(routing=False, tls_mode='disabled')
    if engine == 'redis' and not item.get('password'):
        route.pop('username', None)
        route['auth_mode'] = 'none'
    if engine == 'milvus':
        route['auth_kind'] = 'basic'
    if engine == 'immudb':
        route.update(web_host=host, web_port=item['http_port'],
                     web_tls_mode='disable', web_timeout=30)
    if engine == 'vitess':
        route.update(vtgate_http_host=host,
                     vtgate_http_port=item['http_port'],
                     vtgate_http_tls_mode='disable')
    if engine in {'cassandra', 'yugabytedb_ycql'}:
        route.update(local_dc=item['local_dc'], tls_mode='disabled',
                     compression='none')
    for key in ('cluster_file', 'helper_path', 'pd_endpoints',
                'api_version', 'rest_port', 'control_port',
                'control_sh_path', 'version_api_port', 'contact_points',
                'fdbcli_path', 'fdbbackup_path', 'fdbrestore_path'):
        if key in item:
            route[key] = item[key]
    if engine == 'apache_ignite':
        route['auth_mode'] = item['auth_mode']
    return route


def audit(engine, route_mode='registration', database=None, user=None):
    document = json.loads((ROOT / 'tools/reference_engine_demos/runtime/'
                          'connection_profiles.json').read_text())
    item = next(p for p in document['profiles'] if p['engine'] == engine)
    profile_id = {'opensearch_sql_ppl': 'opensearch-sql-ppl',
                  'yugabytedb_ycql': 'yugabytedb-ycql'}.get(
                      engine, engine.replace('_', '-') + '-native')
    registration = next(p for p in registration_profiles()
                        if p['profile_id'] == profile_id)
    module_name = None
    for manifest_path, candidate in BUILTIN_PACKAGES:
        manifest = json.loads((ROOT / 'web/pgadmin/cdeadmin/providers' /
                               manifest_path).read_text())
        if manifest['identity']['profile_id'] == profile_id:
            module_name = candidate
            break
    module = importlib.import_module(module_name)
    permissions = frozenset({'network', 'filesystem', 'embedded_runtime',
                             'secret_read', 'data_read', 'execute'})
    identity = manifest['identity']
    context = EndpointContext(
        endpoint_id=str(uuid.uuid4()), mode='legacy_native',
        experience_family=engine, provider_id=identity['provider_id'],
        provider_version=identity['provider_version'], profile_id=profile_id,
        profile_version=identity['profile_version'],
        target_adapter_id=registration.get('target_adapter_id', 'catalog-qa'),
        target_adapter_version='catalog-qa', pool_namespace=str(uuid.uuid4()),
        session_namespace=str(uuid.uuid4()), cache_namespace=str(uuid.uuid4()),
        diagnostic_namespace=str(uuid.uuid4()),
        effective_permissions=permissions, declared_runtime_family=engine)
    route = {'host': document.get('host', '127.0.0.1'),
             'port': item.get('port'), 'user': item.get('user', 'anonymous'),
             'database': item.get('database', 'default')}
    if registration['route_kind'] == 'embedded_file':
        route = {'database': item['database'],
                 'filesystem_root': str(Path(item['database']).parent)}
    fields = {}
    for field in registration.get('connection_fields', []):
        key = field['field_id']
        if key in item:
            value = item[key]
            if field['control'] == 'text' and isinstance(value, list):
                value = ','.join(str(v) for v in value)
            fields['cde_route_' + key] = value
    if route_mode == 'native-demo':
        route = native_demo_route(engine, item, document['host'])
    else:
        spec = importlib.util.spec_from_file_location(
            'qa_demo_registration', ROOT / 'tools/reference_engine_demos/'
            'register_demo_profiles.py')
        registration_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(registration_module)
        route = registration_module.route_for(
            item, document['host'], registration, provider_route_options)
    if database is not None:
        key = registration.get('database_targeting', {}).get(
            'route_key', 'database')
        if key is None:
            raise ValueError('Provider does not declare database targeting')
        route[key] = database
    if user is not None:
        if engine not in {'mysql', 'mariadb'}:
            raise ValueError('User comparison currently targets MySQL/MariaDB')
        route['user'] = user
        if 'username' in route:
            route['username'] = user
    password = item.get('password') or ''
    credential_source = 'generated-profile'
    if route_mode == 'native-demo' and engine == 'cassandra' and not password:
        # The generated profile omits the password that the demo seeder uses.
        # Keep this workaround visible; do not rewrite saved user credentials.
        spec = importlib.util.spec_from_file_location(
            'demo_estate_credentials', ROOT /
            'tools/reference_engine_demos/demo_estate.py')
        demo = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(demo)
        password = demo.PASSWORD
        credential_source = 'demo-estate-constant-profile-is-missing-password'
    if password:
        route.update(credential_reference_id='catalog-qa',
                     principal_reference='catalog-qa')
    provider = module.create_provider(context, Permissions(password))
    query_failures = []
    connection_failures = []
    client = getattr(provider, 'client', None)
    connector = getattr(client, '_connector', None)
    if callable(connector):
        def observed_connector(*args, **kwargs):
            try:
                return connector(*args, **kwargs)
            except Exception as exc:
                connection_failures.append({
                    'error_type': type(exc).__name__,
                    'error_code': str(getattr(exc, 'errno', '')),
                    'sqlstate': str(getattr(exc, 'sqlstate', '')),
                })
                raise
        client._connector = observed_connector
    client_config = getattr(getattr(provider, 'client', None), 'config', None)
    if client_config is not None and hasattr(client_config, 'metadata_reader'):
        reader = client_config.metadata_reader
        provider.client.config = replace(
            client_config, metadata_reader=lambda connection, request: reader(
                CatalogConnection(connection, query_failures), request))
    try:
        runtime = {}
        try:
            endpoint = provider.discover_endpoint({'route': route})
            runtime = endpoint.get('verified_runtime', {})
        except Exception as exc:
            runtime = {'verification_error': type(exc).__name__ + ': ' +
                       str(exc)}
        try:
            resources = provider.list_resources({'route': route})
        except Exception as exc:
            return {'engine': engine, 'route_mode': route_mode,
                    'error_type': type(exc).__name__, 'error': str(exc),
                    'runtime': runtime,
                    'connection_failures': connection_failures,
                    'catalog_complete': False}
        contract_errors = []
        for resource in resources:
            try:
                validate_contract('Resource', resource)
            except Exception as exc:
                contract_errors.append(str(exc))
        result = walk(resources, route.get('database'))
        ids = [r['resource_id'] for r in resources]
        result.update(engine=engine, profile_id=profile_id,
                      route_mode=route_mode,
                      credential_source=credential_source,
                      resource_count=len(resources),
                      runtime=runtime,
                      kinds=dict(Counter(
                          r['resource_kind'] for r in resources)),
                      duplicate_ids=[k for k, n in Counter(ids).items()
                                     if n > 1],
                      contract_errors=contract_errors,
                      catalog_query_failures=query_failures,
                      provider_catalog_coverage=[
                          {'resource_id': item['resource_id'],
                           'coverage': resource_native(item)[
                               'catalog_coverage']}
                          for item in resources
                          if 'catalog_coverage' in resource_native(item)
                      ],
                      connection_failures=connection_failures,
                      declared_kinds=list(getattr(
                          getattr(provider, 'profile', None),
                          'resource_kinds', ())),
                      catalog_complete=False,
                      completeness_note='Requires native inventory comparison')
        return result
    finally:
        provider.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('engine')
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--database',
                        help='Existing native catalog to inspect')
    parser.add_argument('--user', help='Compare a MySQL/MariaDB catalog user')
    parser.add_argument('--route-mode', choices=(
        'registration', 'native-demo'),
        default='registration')
    args = parser.parse_args()
    try:
        result = audit(args.engine, args.route_mode, args.database, args.user)
    except Exception as exc:
        result = {'engine': args.engine, 'error_type': type(exc).__name__,
                  'error': str(exc), 'catalog_complete': False}
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({k: v for k, v in result.items() if k != 'branches'}))
    sys.exit(1 if any(result.get(key) for key in (
        'error', 'errors', 'unreachable_resource_ids', 'duplicate_ids',
        'contract_errors', 'catalog_query_failures',
        'multiply_presented_resource_ids',
    )) or result.get('runtime', {}).get('verification_error') else 0)
