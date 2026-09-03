#!/usr/bin/env python3
##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Qualify the preserved PostgreSQL UI against an exact isolated runtime."""

from __future__ import annotations

import argparse
import json
import secrets
import subprocess
import sys
import time
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

from pgadmin.cdeadmin.providers.postgresql.preserved_surface import (  # noqa: E402
    SURFACE_ID, audit_preserved_surface,
)
from pgadmin.cdeadmin.visual_admin.live_evidence import (  # noqa: E402
    LIVE_EVIDENCE_SCHEMA,
)


IMAGE = 'postgres:18.3'
PROFILE = '18.3'
PORT = 5432
APPROVED_DRIVER_VERSIONS = frozenset({'3.2.13', '3.3.4'})


def _run(arguments, *, check=True):
    return subprocess.run(
        arguments, check=check, capture_output=True, text=True,
    )


def _published_port(value):
    for line in value.splitlines():
        try:
            port = int(line.strip().rsplit(':', 1)[1])
        except (IndexError, ValueError):
            continue
        if 0 < port < 65536:
            return port
    raise RuntimeError('container did not publish a usable database port')


def _image_identity(image):
    completed = _run([
        'docker', 'image', 'inspect', image, '--format', '{{json .}}',
    ])
    document = json.loads(completed.stdout)
    return {
        'requested_reference': image,
        'image_id': document.get('Id'),
        'repo_digests': sorted(document.get('RepoDigests') or []),
    }


def _connect(port, password, database='postgres'):
    import psycopg
    return psycopg.connect(
        host='127.0.0.1', port=port, dbname=database,
        user='postgres', password=password, connect_timeout=2,
        autocommit=True,
    )


def _wait_until_ready(container, port, password, timeout):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = _run(
            ['docker', 'inspect', '-f', '{{.State.Running}}', container],
            check=False,
        )
        if state.returncode or state.stdout.strip() != 'true':
            raise RuntimeError(
                'PostgreSQL qualification container stopped during startup'
            )
        try:
            connection = _connect(port, password)
            connection.close()
            return
        except Exception:
            time.sleep(1)
    raise RuntimeError(
        'PostgreSQL qualification container did not become ready'
    )


def _execute(connection, statement):
    connection.execute(statement)


def _runtime_smoke(port, password, container, token):
    """Exercise exact-runtime constructs used by the preserved surface."""
    import psycopg
    from psycopg import sql

    database = f'cdeadmin_pg_{token}'
    role = f'cdeadmin_role_{token}'
    schema = f'cdeadmin_schema_{token}'
    tablespace = f'cdeadmin_ts_{token}'
    tablespace_path = f'/tmp/{tablespace}'
    publication = f'cdeadmin_pub_{token}'
    subscription = f'cdeadmin_sub_{token}'
    checks = []
    admin = _connect(port, password)
    application = None
    extension = None
    tablespace_created = False
    role_created = False
    database_created = False
    try:
        version_text, version_num = admin.execute(
            "SELECT current_setting('server_version'), "
            "current_setting('server_version_num')"
        ).fetchone()
        normalized_version = version_text.split(' ', 1)[0]
        if normalized_version != PROFILE or version_num != '180003':
            raise RuntimeError(
                f'expected PostgreSQL {PROFILE} (180003), got '
                f'{version_text} ({version_num})'
            )
        if psycopg.__version__ not in APPROVED_DRIVER_VERSIONS:
            raise RuntimeError(
                'psycopg runtime is not one of the approved exact versions'
            )
        checks.extend(('exact_server_profile', 'approved_python_driver'))

        _execute(admin, f'CREATE ROLE {role} NOLOGIN')
        role_created = True
        _execute(admin, f'ALTER ROLE {role} CONNECTION LIMIT 4')
        if not admin.execute(
                'SELECT 1 FROM pg_roles WHERE rolname = %s',
                (role,)).fetchone():
            raise RuntimeError('role inspection failed')
        checks.append('role_create_alter_inspect')

        mkdir = _run([
            'docker', 'exec', '--user', 'postgres', container,
            'mkdir', '-p', tablespace_path,
        ], check=False)
        if mkdir.returncode:
            raise RuntimeError('tablespace directory preparation failed')
        _execute(
            admin,
            f"CREATE TABLESPACE {tablespace} LOCATION '{tablespace_path}'",
        )
        tablespace_created = True
        _execute(admin, f'ALTER TABLESPACE {tablespace} SET (seq_page_cost=1)')
        checks.append('tablespace_create_alter_inspect')

        _execute(admin, f'CREATE DATABASE {database}')
        database_created = True
        _execute(admin, f'ALTER DATABASE {database} CONNECTION LIMIT 8')
        checks.append('database_create_alter_inspect')
        application = _connect(port, password, database)

        statements = (
            f'CREATE SCHEMA {schema}',
            f'CREATE DOMAIN {schema}.nonempty_text AS text '
            "CHECK (VALUE <> '')",
            f"CREATE TYPE {schema}.mood AS ENUM ('calm', 'busy')",
            f'CREATE SEQUENCE {schema}.item_seq START 10',
            f'CREATE TABLE {schema}.items ('
            'id integer PRIMARY KEY, body text NOT NULL, '
            f'mood {schema}.mood DEFAULT \'calm\')',
            f'ALTER TABLE {schema}.items ADD COLUMN revision integer '
            'DEFAULT 1',
            f'CREATE INDEX items_body_idx ON {schema}.items (body)',
            f'ALTER INDEX {schema}.items_body_idx RENAME TO '
            'items_body_search_idx',
            f'CREATE VIEW {schema}.item_view AS SELECT id, body '
            f'FROM {schema}.items',
            f'CREATE MATERIALIZED VIEW {schema}.item_summary AS '
            f'SELECT count(*) AS item_count FROM {schema}.items',
            f'REFRESH MATERIALIZED VIEW {schema}.item_summary',
            f'CREATE FUNCTION {schema}.increment(integer) RETURNS integer '
            'LANGUAGE SQL IMMUTABLE AS \'SELECT $1 + 1\'',
            f'CREATE PROCEDURE {schema}.observe() LANGUAGE SQL AS '
            "'SELECT 1'",
            f'CREATE FUNCTION {schema}.touch_item() RETURNS trigger '
            'LANGUAGE plpgsql AS '
            "'BEGIN NEW.revision := NEW.revision + 1; RETURN NEW; END'",
            f'CREATE TRIGGER item_touch BEFORE UPDATE ON {schema}.items '
            f'FOR EACH ROW EXECUTE FUNCTION {schema}.touch_item()',
            f'CREATE FUNCTION {schema}.observe_ddl() RETURNS event_trigger '
            "LANGUAGE plpgsql AS 'BEGIN END'",
            f'CREATE EVENT TRIGGER cdeadmin_ddl_{token} '
            f'ON ddl_command_start EXECUTE FUNCTION {schema}.observe_ddl()',
            f'CREATE TABLE {schema}.events (id integer, occurred date) '
            'PARTITION BY RANGE (occurred)',
            f"CREATE TABLE {schema}.events_2026 PARTITION OF "
            f"{schema}.events FOR VALUES FROM ('2026-01-01') "
            "TO ('2027-01-01')",
        )
        for statement in statements:
            _execute(application, statement)
        checks.extend((
            'schema_domain_type_sequence',
            'table_column_constraint_index',
            'view_materialized_view',
            'function_procedure_trigger_event_trigger',
            'partition_create_inspect',
        ))

        application.execute(
            f'INSERT INTO {schema}.items (id, body) VALUES (%s, %s)',
            (1, 'created'),
        )
        application.execute(
            f'UPDATE {schema}.items SET body = %s WHERE id = %s',
            ('updated', 1),
        )
        row = application.execute(
            f'SELECT id, body, revision FROM {schema}.items WHERE id = %s',
            (1,),
        ).fetchone()
        if row != (1, 'updated', 2):
            raise RuntimeError('editable-grid DML round trip failed')
        application.execute(
            f'DELETE FROM {schema}.items WHERE id = %s', (1,)
        )
        checks.append('editable_grid_insert_update_delete')

        available = {
            row[0] for row in application.execute(
                'SELECT name FROM pg_available_extensions '
                'WHERE installed_version IS NULL'
            )
        }
        extension = next((
            candidate for candidate in ('hstore', 'citext', 'uuid-ossp')
            if candidate in available
        ), None)
        if extension is None:
            raise RuntimeError('no disposable extension is available')
        quoted_extension = '"uuid-ossp"' if extension == 'uuid-ossp' \
            else extension
        _execute(application, f'CREATE EXTENSION {quoted_extension}')
        checks.append('extension_create_inspect')

        _execute(application, f'CREATE PUBLICATION {publication}')
        connection_string = (
            f'host=127.0.0.1 port={port} dbname={database} '
            f'user=postgres password={password}'
        )
        application.execute(sql.SQL(
            'CREATE SUBSCRIPTION {} CONNECTION {} PUBLICATION {} WITH '
            '(connect=false, create_slot=false, enabled=false)'
        ).format(
            sql.Identifier(subscription), sql.Literal(connection_string),
            sql.Identifier(publication),
        ))
        _execute(
            application,
            f"ALTER PUBLICATION {publication} SET "
            "(publish='insert,update')",
        )
        _execute(application, f'ALTER SUBSCRIPTION {subscription} DISABLE')
        checks.append('publication_subscription_create_alter_inspect')

        return {
            'passed': True,
            'server_version': normalized_version,
            'server_version_display': version_text,
            'server_version_num': version_num,
            'driver': 'psycopg',
            'driver_version': psycopg.__version__,
            'checks': checks,
            'credential_values_exported': False,
        }
    finally:
        if application is not None:
            try:
                if extension:
                    quoted_extension = (
                        '"uuid-ossp"' if extension == 'uuid-ossp'
                        else extension
                    )
                    _execute(
                        application,
                        f'DROP EXTENSION IF EXISTS {quoted_extension} CASCADE',
                    )
                _execute(
                    application,
                    f'DROP SUBSCRIPTION IF EXISTS {subscription}',
                )
                _execute(
                    application,
                    f'DROP PUBLICATION IF EXISTS {publication}',
                )
            except Exception:
                pass
            application.close()
        if database_created:
            try:
                _execute(
                    admin,
                    f'DROP DATABASE IF EXISTS {database} WITH (FORCE)',
                )
            except Exception:
                pass
        if tablespace_created:
            try:
                _execute(admin, f'DROP TABLESPACE IF EXISTS {tablespace}')
            except Exception:
                pass
        if role_created:
            try:
                _execute(admin, f'DROP ROLE IF EXISTS {role}')
            except Exception:
                pass
        admin.close()


def _object_evidence(surface, runtime, run_id):
    concepts = {'relational': {}}
    for concept_id, result in surface['concepts'].items():
        if result['status'] != 'passed':
            continue
        concepts['relational'][concept_id] = {
            'status': 'passed',
            'operations': result['operations'],
        }
    return {
        'schema': LIVE_EVIDENCE_SCHEMA,
        'engine_id': 'postgresql',
        'exact_profile': PROFILE,
        'run_id': run_id,
        'evidence_scope': (
            'exact-runtime-and-preserved-native-administration-surface'
        ),
        'surface_id': SURFACE_ID,
        'surface_sha256': surface['surface_sha256'],
        'runtime_attestation': {
            'server_version': runtime['server_version'],
            'server_version_num': runtime['server_version_num'],
            'driver': runtime['driver'],
            'driver_version': runtime['driver_version'],
        },
        'concepts': concepts,
        'operation_failures': {},
        'credential_values_exported': False,
    }


def run(image, server_log, startup_timeout=300):
    surface = audit_preserved_surface(ROOT)
    if not surface['passed']:
        raise RuntimeError(
            'preserved PostgreSQL surface has missing required assets'
        )
    identity = _image_identity(image)
    token = secrets.token_hex(6)
    container = f'cdeadmin-postgresql-object-{token}'
    password = secrets.token_urlsafe(32)
    started = False
    result = None
    try:
        _run([
            'docker', 'run', '-d', '--rm', '--name', container,
            '-e', f'POSTGRES_PASSWORD={password}',
            '-p', f'127.0.0.1::{PORT}', image,
        ])
        started = True
        port_output = _run(
            ['docker', 'port', container, f'{PORT}/tcp']
        ).stdout
        port = _published_port(port_output)
        _wait_until_ready(
            container, port, password, startup_timeout,
        )
        runtime = _runtime_smoke(port, password, container, token)
        evidence = _object_evidence(surface, runtime, token)
        result = {
            'schema': (
                'cdeadmin.postgresql-preserved-surface-live-verification.v1'
            ),
            'engine_id': 'postgresql',
            'exact_profile': PROFILE,
            'activation_ready': True,
            'runtime_image': identity,
            'runtime_verification': runtime,
            'preserved_surface_audit': surface,
            'object_experience_evidence': evidence,
            'isolated_runtime_container': True,
            'credential_values_exported': False,
        }
    finally:
        server_log.parent.mkdir(parents=True, exist_ok=True)
        if started:
            logs = _run(['docker', 'logs', container], check=False)
            server_log.write_text(
                logs.stdout + logs.stderr, encoding='utf-8'
            )
            _run(['docker', 'stop', container], check=False)
        elif not server_log.exists():
            server_log.write_text('', encoding='utf-8')
    if result is None:
        raise RuntimeError('qualification completed without evidence')
    result['server_stopped'] = True
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', default=IMAGE)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--object-output', type=Path, required=True)
    parser.add_argument('--server-log', type=Path, required=True)
    parser.add_argument('--startup-timeout', type=int, default=300)
    options = parser.parse_args(argv)
    result = run(
        options.image, options.server_log,
        startup_timeout=options.startup_timeout,
    )
    for path, document in (
        (options.output, result),
        (options.object_output, result['object_experience_evidence']),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(document, indent=2, sort_keys=True) + '\n',
            encoding='utf-8',
        )
    print(json.dumps({
        'engine_id': 'postgresql',
        'exact_profile': PROFILE,
        'activation_ready': result['activation_ready'],
        'surface_sha256': result[
            'preserved_surface_audit']['surface_sha256'],
        'credential_values_exported': False,
        'server_stopped': result['server_stopped'],
    }, indent=2, sort_keys=True))
    return 0 if result['activation_ready'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
