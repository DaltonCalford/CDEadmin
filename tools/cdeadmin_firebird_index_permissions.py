"""Live index permission checks using a disposable unprivileged account."""

import secrets
import uuid


def verify(connection, client, profile, table, index, result):
    from firebird import driver
    from cdeadmin_firebird_constraint_editor_gate import (
        ADMINISTRATION, _route_arguments,
    )
    from pgadmin.cdeadmin.sdk.relational import RelationalClientError

    username = 'CDE_IX_QA_' + uuid.uuid4().hex[:12].upper()
    password = secrets.token_hex(24)
    created, limited = False, None

    def execute(sql):
        with connection.cursor() as cursor:
            cursor.execute(sql)

    try:
        execute(f'CREATE USER "{username}" PASSWORD \'{password}\'')
        connection.commit()
        created = True
        route = {**profile, 'user': username, 'username': username,
                 'password': password}
        limited = driver.connect(password=password,
                                 **_route_arguments(route, driver))
        with limited.cursor() as cursor:
            cursor.execute('SELECT CURRENT_USER FROM RDB$DATABASE')
            assert cursor.fetchone()[0].strip() == username
        limited.commit()
        for label, operation, draft in (
                ('statistics', 'alter', {'refresh_statistics': True}),
                ('deactivate', 'alter', {'active': False}),
                ('drop', 'drop', {}),
                ('create', 'create', {'name': index + '_DENIED',
                                      'table': table, 'columns': ['ID']})):
            plan = ADMINISTRATION.plan({
                '_provider_route': route, 'resource_kind': 'index',
                'operation_id': operation, 'draft': draft,
                'target_resource': {'display_name': index,
                                    'display_path': [table, index]}})
            try:
                ADMINISTRATION.apply(client, plan, connection=limited)
            except RelationalClientError as error:
                assert 'execution failed (DatabaseError)' in str(error), error
            else:
                raise AssertionError('Unprivileged index operation succeeded')
            # Confirm the same native command fails specifically for privilege,
            # not just because the provider returned a generic failure wrapper.
            source = plan['command_preview']['statements'][0]['source']
            try:
                with limited.cursor() as cursor:
                    cursor.execute(source)
            except driver.DatabaseError as error:
                assert error.sqlstate == '28000', error.sqlstate
            else:
                raise AssertionError('Native privilege check did not fail')
            finally:
                if limited.main_transaction.is_active():
                    limited.rollback()
            result['checks'].append('unprivileged-index-' + label + '-denied')
        with connection.cursor() as cursor:
            cursor.execute('SELECT RDB$INDEX_INACTIVE FROM RDB$INDICES '
                           'WHERE RDB$INDEX_NAME = ?', (index,))
            assert cursor.fetchall() == [(0,)]
            cursor.execute('SELECT COUNT(*) FROM RDB$INDICES '
                           'WHERE RDB$INDEX_NAME = ?', (index + '_DENIED',))
            assert cursor.fetchone()[0] == 0
        connection.commit()
        result['checks'].append('permission-denials-preserve-index-state')
    finally:
        try:
            if limited is not None:
                limited.close()
        finally:
            if created:
                if connection.main_transaction.is_active():
                    connection.rollback()
                execute(f'DROP USER "{username}"')
                connection.commit()
                with connection.cursor() as cursor:
                    cursor.execute('SELECT COUNT(*) FROM SEC$USERS '
                                   'WHERE SEC$USER_NAME = ?', (username,))
                    assert cursor.fetchone()[0] == 0
                connection.commit()
                result['temporary_user_removed'] = True
