"""Owned UI copies preserve defaults unless explicitly retargeted."""

import ast
import json
from pathlib import Path
import sqlite3

import pytest


@pytest.mark.parametrize('endpoint_user', [None, 'OWNED_RECOVERY_OPERATOR'])
def test_retarget_scopes_user_to_one_endpoint(tmp_path, endpoint_user):
    source = Path(__file__).resolve().parents[1] / (
        'cdeadmin_firebird_ui_completed_orchestrator.py')
    function = next(node for node in ast.walk(ast.parse(source.read_text()))
                    if isinstance(node, ast.FunctionDef)
                    and node.name == '_retarget_config')
    namespace = {'sqlite3': sqlite3, 'json': json, 'Path': Path}
    exec(compile(ast.Module(body=[function], type_ignores=[]),
                 str(source), 'exec'), namespace)
    database = tmp_path / 'owned.db'
    with sqlite3.connect(database) as connection:
        connection.executescript('''
            CREATE TABLE user (id INTEGER, email TEXT);
            CREATE TABLE server (id INTEGER, user_id INTEGER, name TEXT,
                                 host TEXT, port INTEGER, username TEXT);
            CREATE TABLE cde_endpoint
                (id INTEGER, legacy_server_id INTEGER, profile_id TEXT);
            CREATE TABLE cde_endpoint_route
                (id INTEGER, endpoint_id INTEGER, priority INTEGER,
                 configuration TEXT);
            CREATE TABLE cde_endpoint_database_target
                (id INTEGER, endpoint_id INTEGER, active INTEGER,
                 display_name TEXT, database TEXT, updated_at TEXT);
            CREATE TABLE cde_endpoint_runtime_identity
                (endpoint_id INTEGER, verification_state TEXT,
                 verified_runtime_family TEXT, verified_runtime_version TEXT);
        ''')
        for identifier, email in ((1, 'owned@example.test'),
                                  (2, 'unrelated@example.test')):
            connection.execute('INSERT INTO user VALUES (?, ?)',
                               (identifier, email))
            connection.execute('INSERT INTO server VALUES (?, ?, ?, ?, ?, ?)',
                               (identifier, identifier, 'original', 'old',
                                3050, 'ORIGINAL'))
            connection.execute('INSERT INTO cde_endpoint VALUES (?, ?, ?)',
                               (identifier, identifier, 'firebird-native'))
            connection.execute(
                'INSERT INTO cde_endpoint_route VALUES (?, ?, 0, ?)',
                (identifier, identifier, json.dumps({
                    'user': 'ORIGINAL', 'database': 'old.fdb',
                    'charset': 'UTF8'})))
            connection.execute('INSERT INTO cde_endpoint_database_target '
                               'VALUES (?, ?, 1, ?, ?, NULL)',
                               (identifier, identifier, 'old', 'old.fdb'))
            connection.execute('INSERT INTO cde_endpoint_runtime_identity '
                               'VALUES (?, ?, ?, ?)',
                               (identifier, 'verified', 'firebird', '5.0.4'))
    namespace['_retarget_config'](
        database, 'owned@example.test', '/owned/new.fdb', 'new', 53051,
        endpoint_user=endpoint_user)
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            'SELECT username, host, port FROM server WHERE id = 1'
        ).fetchone() == (endpoint_user or 'ORIGINAL', '127.0.0.1', 53051)
        assert connection.execute(
            'SELECT username, host, port FROM server WHERE id = 2'
        ).fetchone() == ('ORIGINAL', 'old', 3050)
        route = json.loads(connection.execute(
            'SELECT configuration FROM cde_endpoint_route WHERE id = 1'
        ).fetchone()[0])
        assert route['user'] == (endpoint_user or 'ORIGINAL')
        assert route['charset'] == 'UTF8'
        assert 'database' not in route
        assert json.loads(connection.execute(
            'SELECT configuration FROM cde_endpoint_route WHERE id = 2'
        ).fetchone()[0])['database'] == 'old.fdb'
