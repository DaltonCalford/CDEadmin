#!/usr/bin/env python3
##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Verify relational drivers, full sessions, and exact target profiles.

Driver compatibility and endpoint conformance are deliberately independent.
An older compatible client may pass its connection/API suite while the target
still fails exact-profile qualification. Transaction attributes are captured
only as opaque observations; this tool never infers finality.
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import re
import sqlite3
import subprocess
import tempfile
from dataclasses import dataclass, field
from importlib import metadata
from pathlib import Path
from typing import Any, Callable, Mapping


PROFILES = {
    'postgresql': '18.3',
    'mysql': '9.7.0',
    'mariadb': '12.2.2',
    'duckdb': '1.5.2',
    'firebird': '5.0.4',
    'sqlite': '3.53.0',
}
CLIENT_DISTRIBUTIONS = {
    'postgresql': 'psycopg',
    'mysql': 'mysql-connector-python',
    'mariadb': 'mariadb',
    'duckdb': 'duckdb',
    'firebird': 'firebird-driver',
}
CLIENT_MODULES = {
    'postgresql': 'psycopg',
    'mysql': 'mysql.connector',
    'mariadb': 'mariadb',
    'duckdb': 'duckdb',
    'firebird': 'firebird.driver',
}
VERSION_QUERIES = {
    'postgresql': 'SHOW server_version',
    'mysql': 'SELECT VERSION()',
    'mariadb': 'SELECT VERSION()',
    'duckdb': 'SELECT version()',
    'firebird': (
        "SELECT RDB$GET_CONTEXT('SYSTEM', 'ENGINE_VERSION') "
        'FROM RDB$DATABASE'
    ),
    'sqlite': 'SELECT sqlite_version()',
}
METADATA_QUERIES = {
    'postgresql': 'SELECT COUNT(*) FROM information_schema.tables',
    'mysql': 'SELECT COUNT(*) FROM information_schema.tables',
    'mariadb': 'SELECT COUNT(*) FROM information_schema.tables',
    'duckdb': 'SELECT COUNT(*) FROM information_schema.tables',
    'firebird': 'SELECT COUNT(*) FROM RDB$RELATIONS',
    'sqlite': 'SELECT COUNT(*) FROM sqlite_schema',
}
PARAMETER_QUERIES = {
    'postgresql': ('SELECT %s', (42,)),
    'mysql': ('SELECT %s', (42,)),
    'mariadb': ('SELECT ?', (42,)),
    'duckdb': ('SELECT ?', (42,)),
    'firebird': ('SELECT ? FROM RDB$DATABASE', (42,)),
    'sqlite': ('SELECT ?', (42,)),
}


def _version(value: object) -> str | None:
    match = re.search(r'(\d+\.\d+(?:\.\d+)?)', str(value))
    return match.group(1) if match else None


def _first(row: object):
    if isinstance(row, Mapping):
        values = tuple(row.values())
        return values[0] if values else None
    if isinstance(row, (list, tuple)):
        return row[0] if row else None
    return row


def _safe_attribute(connection, name):
    try:
        value = getattr(connection, name)
    except Exception:
        return None
    if callable(value):
        return None
    if isinstance(value, (bool, int, float, str)) or value is None:
        return value
    return type(value).__name__


def _error(exc: Exception) -> dict[str, str]:
    """Return diagnostic type only; connection strings may contain secrets."""
    return {'error_type': type(exc).__name__}


@dataclass
class VerificationResult:
    engine_id: str
    target_profile: str
    driver_status: str = 'blocked'
    target_status: str = 'not_run'
    activation_ready: bool = False
    client_name: str | None = None
    client_version: str | None = None
    observed_target_version: str | None = None
    stages: dict[str, Any] = field(default_factory=dict)
    diagnostics: list[dict[str, str]] = field(default_factory=list)

    def to_dict(self):
        return {
            'engine_id': self.engine_id,
            'target_profile': self.target_profile,
            'driver_status': self.driver_status,
            'target_status': self.target_status,
            'activation_ready': self.activation_ready,
            'client': {
                'name': self.client_name,
                'version': self.client_version,
            },
            'observed_target_version': self.observed_target_version,
            'stages': self.stages,
            'diagnostics': self.diagnostics,
        }


def _driver(engine_id, result):
    if engine_id == 'sqlite':
        result.client_name = 'python-sqlite3'
        result.client_version = sqlite3.sqlite_version
        return sqlite3
    distribution = CLIENT_DISTRIBUTIONS[engine_id]
    result.client_name = distribution
    try:
        result.client_version = metadata.version(distribution)
        return importlib.import_module(CLIENT_MODULES[engine_id])
    except (metadata.PackageNotFoundError, ImportError, ModuleNotFoundError):
        result.stages['dependency'] = 'blocked'
        result.diagnostics.append({
            'code': 'CDE_RELATIONAL_DRIVER_UNAVAILABLE',
            'dependency': distribution,
        })
        return None


def _required_environment(engine_id):
    requirements = {
        'postgresql': ('CDEADMIN_VERIFY_POSTGRESQL_DSN',),
        'mysql': ('CDEADMIN_VERIFY_MYSQL_USER',
                  'CDEADMIN_VERIFY_MYSQL_DATABASE'),
        'mariadb': ('CDEADMIN_VERIFY_MARIADB_USER',
                    'CDEADMIN_VERIFY_MARIADB_DATABASE'),
        'firebird': ('CDEADMIN_VERIFY_FIREBIRD_DATABASE',),
    }
    return tuple(
        name for name in requirements.get(engine_id, ())
        if not os.environ.get(name)
    )


def _connection(engine_id, module):
    if engine_id == 'postgresql':
        return module.connect(os.environ.get(
            'CDEADMIN_VERIFY_POSTGRESQL_DSN', 'dbname=postgres'
        ))
    if engine_id == 'mysql':
        return module.connect(
            host=os.environ.get('CDEADMIN_VERIFY_MYSQL_HOST', '127.0.0.1'),
            port=int(os.environ.get('CDEADMIN_VERIFY_MYSQL_PORT', '3306')),
            user=os.environ['CDEADMIN_VERIFY_MYSQL_USER'],
            password=os.environ.get('CDEADMIN_VERIFY_MYSQL_PASSWORD'),
            database=os.environ['CDEADMIN_VERIFY_MYSQL_DATABASE'],
        )
    if engine_id == 'mariadb':
        return module.connect(
            host=os.environ.get(
                'CDEADMIN_VERIFY_MARIADB_HOST', '127.0.0.1'
            ),
            port=int(os.environ.get('CDEADMIN_VERIFY_MARIADB_PORT', '3306')),
            user=os.environ['CDEADMIN_VERIFY_MARIADB_USER'],
            password=os.environ.get('CDEADMIN_VERIFY_MARIADB_PASSWORD'),
            database=os.environ['CDEADMIN_VERIFY_MARIADB_DATABASE'],
        )
    if engine_id == 'duckdb':
        return module.connect(os.environ.get(
            'CDEADMIN_VERIFY_DUCKDB_DATABASE', ':memory:'
        ))
    if engine_id == 'firebird':
        options = {
            'database': os.environ['CDEADMIN_VERIFY_FIREBIRD_DATABASE'],
        }
        for key, environment in (
            ('user', 'CDEADMIN_VERIFY_FIREBIRD_USER'),
            ('password', 'CDEADMIN_VERIFY_FIREBIRD_PASSWORD'),
            ('role', 'CDEADMIN_VERIFY_FIREBIRD_ROLE'),
            ('charset', 'CDEADMIN_VERIFY_FIREBIRD_CHARSET'),
        ):
            if os.environ.get(environment):
                options[key] = os.environ[environment]
        return module.connect(**options)
    return module.connect(os.environ.get(
        'CDEADMIN_VERIFY_SQLITE_DATABASE', ':memory:'
    ))


def verify_connection(engine_id, connector: Callable | None = None):
    result = VerificationResult(engine_id, PROFILES[engine_id])
    module = _driver(engine_id, result)
    if module is None:
        return result
    result.stages['dependency'] = 'passed'
    missing = _required_environment(engine_id)
    if missing:
        result.stages['configuration'] = 'blocked'
        result.diagnostics.append({
            'code': 'CDE_RELATIONAL_CONNECTION_INPUT_REQUIRED',
            'environment_names': ','.join(missing),
        })
        return result
    connection = None
    cursor = None
    try:
        connection = connector() if connector else _connection(
            engine_id, module
        )
        result.stages['connect'] = 'passed'
        cursor = connection.cursor()
        cursor.execute(VERSION_QUERIES[engine_id])
        observed = _version(_first(cursor.fetchone()))
        result.observed_target_version = observed
        if observed == result.target_profile:
            result.target_status = 'exact_match'
            result.stages['target_identity'] = 'passed'
        else:
            result.target_status = 'profile_mismatch'
            result.stages['target_identity'] = 'failed'
        query, parameters = PARAMETER_QUERIES[engine_id]
        cursor.execute(query, parameters)
        value = _first(cursor.fetchone())
        if value != 42:
            raise RuntimeError('parameter round trip returned wrong value')
        result.stages['parameter_roundtrip'] = 'passed'
        cursor.execute(METADATA_QUERIES[engine_id])
        if _first(cursor.fetchone()) is None:
            raise RuntimeError('metadata query returned no value')
        result.stages['metadata'] = 'passed'
        result.stages['transaction_observation'] = {
            'status': 'passed',
            'opaque': True,
            'finality_inferred': False,
            'autocommit': _safe_attribute(connection, 'autocommit'),
            'in_transaction': _safe_attribute(connection, 'in_transaction'),
            'isolation_level': _safe_attribute(connection, 'isolation_level'),
        }
        result.driver_status = 'passed'
        result.activation_ready = result.target_status == 'exact_match'
    except Exception as exc:
        result.driver_status = 'failed'
        result.diagnostics.append({
            'code': 'CDE_RELATIONAL_CONNECTION_VERIFICATION_FAILED',
            **_error(exc),
        })
    finally:
        if cursor is not None:
            try:
                cursor.close()
            except Exception:
                pass
        if connection is not None:
            try:
                connection.close()
                result.stages['close'] = 'passed'
            except Exception as exc:
                result.stages['close'] = 'failed'
                result.diagnostics.append({
                    'code': 'CDE_RELATIONAL_CONNECTION_CLOSE_FAILED',
                    **_error(exc),
                })
                result.activation_ready = False
    return result


def verify_sqlite_cross_version(sqlite353_cli: Path):
    result = {
        'client_runtime': sqlite3.sqlite_version,
        'target_runtime': None,
        'status': 'failed',
        'stages': {},
        'diagnostics': [],
    }
    try:
        version_process = subprocess.run(
            [str(sqlite353_cli), '--version'], check=True,
            capture_output=True, text=True,
        )
        result['target_runtime'] = _version(version_process.stdout)
        if result['target_runtime'] != PROFILES['sqlite']:
            raise RuntimeError('exact SQLite target is unavailable')
        result['stages']['exact_target'] = 'passed'
        with tempfile.TemporaryDirectory(
            prefix='cdeadmin-sqlite-cross-version-'
        ) as directory:
            root = Path(directory)
            made_by_target = root / 'made-by-353.sqlite'
            made_by_client = root / 'made-by-345.sqlite'
            subprocess.run([
                str(sqlite353_cli), str(made_by_target),
                'CREATE TABLE cross_version(id INTEGER PRIMARY KEY, '
                "payload TEXT); INSERT INTO cross_version VALUES(1, 'v353');"
            ], check=True, capture_output=True, text=True)
            with sqlite3.connect(made_by_target) as connection:
                row = connection.execute(
                    'SELECT id, payload FROM cross_version'
                ).fetchone()
                if row != (1, 'v353'):
                    raise RuntimeError('older client could not read target DB')
                connection.execute(
                    'INSERT INTO cross_version VALUES(?, ?)', (2, 'v345')
                )
            result['stages']['target_to_older_client'] = 'passed'
            check = subprocess.run([
                str(sqlite353_cli), str(made_by_target),
                "SELECT COUNT(*), group_concat(payload, ',') "
                'FROM cross_version ORDER BY id;'
            ], check=True, capture_output=True, text=True)
            if check.stdout.strip() != '2|v353,v345':
                raise RuntimeError('target could not read older client write')
            result['stages']['older_client_to_target'] = 'passed'
            with sqlite3.connect(made_by_client) as connection:
                connection.execute(
                    'CREATE TABLE client_file(value INTEGER)'
                )
                connection.execute(
                    'INSERT INTO client_file VALUES(?)', (42,)
                )
            check = subprocess.run([
                str(sqlite353_cli), str(made_by_client),
                'SELECT value FROM client_file; PRAGMA integrity_check;'
            ], check=True, capture_output=True, text=True)
            if check.stdout.strip().splitlines() != ['42', 'ok']:
                raise RuntimeError('target rejected older client database')
            result['stages']['older_file_to_target'] = 'passed'
        result['status'] = 'passed'
    except Exception as exc:
        result['diagnostics'].append({
            'code': 'CDE_SQLITE_CROSS_VERSION_VERIFICATION_FAILED',
            **_error(exc),
        })
    return result


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        '--engine', action='append', choices=tuple(PROFILES) + ('all',),
        help='Engine to verify; repeat as needed (default: all).',
    )
    parser.add_argument('--sqlite353-cli', type=Path)
    parser.add_argument('--output', type=Path)
    return parser.parse_args(argv)


def _verification_succeeded(results, sqlite_cross_version):
    connections_qualified = all(
        row['driver_status'] == 'passed' and row['activation_ready']
        for row in results
    )
    cross_version_qualified = (
        sqlite_cross_version is None or
        sqlite_cross_version['status'] == 'passed'
    )
    return connections_qualified and cross_version_qualified


def main(argv=None):
    args = parse_args(argv)
    selected = args.engine or ['all']
    engines = tuple(PROFILES) if 'all' in selected else tuple(dict.fromkeys(
        selected
    ))
    results = [verify_connection(engine).to_dict() for engine in engines]
    sqlite_cross_version = None
    if args.sqlite353_cli:
        sqlite_cross_version = verify_sqlite_cross_version(
            args.sqlite353_cli.resolve()
        )
    document = {
        'schema': 'cdeadmin.relational-driver-verification.v1',
        'results': results,
        'sqlite_cross_version': sqlite_cross_version,
        'summary': {
            'driver_passed': sum(
                row['driver_status'] == 'passed' for row in results
            ),
            'activation_ready': sum(
                row['activation_ready'] for row in results
            ),
            'blocked': sum(
                row['driver_status'] == 'blocked' for row in results
            ),
            'failed': sum(
                row['driver_status'] == 'failed' for row in results
            ),
        },
    }
    encoded = json.dumps(document, indent=2, sort_keys=True) + '\n'
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding='utf-8')
    print(encoded, end='')
    return 0 if _verification_succeeded(
        results, sqlite_cross_version
    ) else 1


if __name__ == '__main__':
    raise SystemExit(main())
