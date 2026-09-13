#!/usr/bin/env python3
"""Read PostgreSQL catalogs through the real application and preserved driver.

Only an isolated CDEadmin configuration is written; the engine is read-only.
"""
import argparse
import builtins
import json
from pathlib import Path
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'web'))


def run(workspace):
    builtins.SERVER_MODE = None
    # The application entry point imports config first. Importing provider
    # packages through a fabricated pgadmin module causes circular imports.
    import config
    config.SERVER_MODE = False
    config.TESTING = True
    config.DATA_DIR = str(workspace)
    config.SQLITE_PATH = str(workspace / 'application.db')
    config.SESSION_DB_PATH = str(workspace / 'sessions')
    config.STORAGE_DIR = str(workspace / 'storage')
    config.LOG_FILE = str(workspace / 'application.log')
    config.AZURE_CREDENTIAL_CACHE_DIR = str(workspace / 'azure')
    from pgadmin import create_app
    from pgadmin.model import db, User, ServerGroup, Server
    from flask_login import login_user
    from pgadmin.utils.driver import get_driver
    from pgadmin.cdeadmin.providers.postgresql.provider import (
        PostgreSQLProvider,
    )
    from cdeadmin_catalog_qa_audit import Permissions
    app = create_app('CDEadmin-postgresql-catalog-QA')
    profiles_path = (ROOT / 'tools/reference_engine_demos/runtime/'
                     'connection_profiles.json')
    profile = next(p for p in json.loads(profiles_path.read_text())['profiles']
                   if p['engine'] == 'postgresql')
    with app.test_request_context('/'):
        user = User.query.first()
        login_user(user)
        group = ServerGroup.query.filter_by(
            user_id=user.id, name='Catalog QA').first() or ServerGroup(
                user_id=user.id, name='Catalog QA')
        db.session.add(group)
        db.session.flush()
        server = Server(user_id=user.id, servergroup_id=group.id,
                        name='PostgreSQL catalog QA', host='127.0.0.1',
                        port=profile['port'], username=profile['user'],
                        maintenance_db=profile['database'], save_password=0)
        db.session.add(server)
        db.session.commit()
        driver = get_driver('psycopg3', app)
        manager = driver.delegated_connection_manager(server.id, user.id)
        connection = manager.connection()
        connected, message = connection.connect(
            user=profile['user'], password=profile['password'])
        if not connected:
            raise RuntimeError(message)
        # Retain the real driver/manager, bypassing only interactive session
        # lookup: this app configuration is an isolated delegated QA account.

        class Driver:
            def connection_manager(self, server_id):
                if server_id != server.id:
                    raise ValueError('unexpected QA server identity')
                return manager

            def version(self):
                return driver.version()

        context = SimpleNamespace(
            endpoint_id='postgresql-catalog-qa',
            cache_namespace='catalog-qa', session_namespace='catalog-qa',
            provider_id='org.pgadmin.postgresql', provider_version='9.17.0',
            profile_id='postgresql-native', profile_version='18.3',
        )
        provider = PostgreSQLProvider(
            context, Permissions(''), driver=Driver())
        try:
            runtime = provider.discover_endpoint({'route': {
                'server_id': server.id}})
            request = {'extensions': {'postgresql': {
                'server_id': server.id, 'child_kind': 'database'}}}
            databases = provider.list_resources(request)
            selected = next(item for item in databases
                            if item['display_name'] == profile['database'])
            route = dict(selected['extensions']['postgresql'],
                         database_id=selected['extensions'][
                             'postgresql']['object_id'],
                         child_kind='schema', show_system_objects=True)
            schemas = provider.list_resources({
                **selected, 'extensions': {'postgresql': route}})
            tables = []
            for schema in schemas:
                route = dict(schema['extensions']['postgresql'],
                             schema_id=schema['extensions'][
                                 'postgresql']['object_id'],
                             child_kind='table', show_system_objects=True)
                tables.extend(provider.list_resources({
                    **schema, 'extensions': {'postgresql': route}}))
            return {'verified_runtime': runtime['verified_runtime'],
                    'databases': len(databases), 'schemas': len(schemas),
                    'tables': len(tables),
                    'scope': 'preserved database/schema/table catalogs',
                    'full_object_coverage': False}
        finally:
            provider.close()
            manager.release()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', type=Path, required=True)
    args = parser.parse_args()
    args.workspace.mkdir(parents=True, exist_ok=True)
    try:
        result = run(args.workspace.resolve())
    except Exception as exc:
        result = {'error_type': type(exc).__name__, 'error': str(exc)}
    (args.workspace / 'result.json').write_text(json.dumps(result, indent=2))
    print(json.dumps(result))
    sys.exit(1 if 'error' in result else 0)
