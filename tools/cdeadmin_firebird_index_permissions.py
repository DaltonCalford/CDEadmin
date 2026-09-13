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
        # Use the provider's class-wide privilege planner, not an invented
        # per-index GRANT. Reconnect to test fresh attachment authority.

        def privilege(operation, principal=username, kind='USER'):
            plan = ADMINISTRATION.plan({
                '_provider_route': profile, 'resource_kind': 'privilege',
                'operation_id': operation, 'draft': {
                    'principal': principal, 'privilege_scope': 'ddl_class',
                    'ddl_class': 'TABLE', 'ddl_privileges': ['ALTER ANY'],
                    'ddl_principal_kind': kind}})
            ADMINISTRATION.apply(client, plan, connection=connection)
            connection.commit()

        limited.close()
        limited = None
        privilege('grant')
        try:
            limited = driver.connect(password=password,
                                     **_route_arguments(route, driver))
            plan = ADMINISTRATION.plan({
                '_provider_route': route, 'resource_kind': 'index',
                'operation_id': 'alter',
                'draft': {'refresh_statistics': True},
                'target_resource': {'display_name': index,
                                    'display_path': [table, index]}})
            ADMINISTRATION.apply(client, plan, connection=limited)
            limited.commit()
            result['checks'].append('ddl-grant-enables-index-statistics')
        finally:
            if limited is not None:
                limited.close()
                limited = None
            privilege('revoke')
        limited = driver.connect(password=password,
                                 **_route_arguments(route, driver))
        try:
            with limited.cursor() as cursor:
                cursor.execute(f'SET STATISTICS INDEX "{index}"')
        except driver.DatabaseError as error:
            assert error.sqlstate == '28000', error.sqlstate
        else:
            raise AssertionError('Revoked DDL privilege still succeeded')
        finally:
            if limited.main_transaction.is_active():
                limited.rollback()
        result['checks'].append('ddl-revoke-restores-index-statistics-denial')
        limited.close()
        limited = None
        role = username + '_R'
        execute(f'CREATE ROLE "{role}"')
        connection.commit()
        try:
            privilege('grant', role, 'ROLE')

            def membership(operation, member=username, kind='USER',
                           selected_role=role, **options):
                plan = ADMINISTRATION.plan({
                    '_provider_route': profile, 'resource_kind': 'role',
                    'operation_id': operation,
                    'target_resource': {'display_name': selected_role},
                    'draft': {'member': member, 'member_kind': kind,
                              **options}})
                ADMINISTRATION.apply(client, plan, connection=connection)
                connection.commit()

            membership('grant', admin_option=True, grantor='SYSDBA')
            with connection.cursor() as cursor:
                cursor.execute('SELECT RDB$GRANT_OPTION FROM '
                               'RDB$USER_PRIVILEGES WHERE RDB$USER = ? '
                               'AND RDB$RELATION_NAME = ?', (username, role))
                assert cursor.fetchone()[0] == 2  # WITH_ADMIN_OPTION
            connection.commit()
            membership('revoke', admin_option_only=True, grantor='SYSDBA')
            with connection.cursor() as cursor:
                cursor.execute('SELECT RDB$GRANT_OPTION FROM '
                               'RDB$USER_PRIVILEGES WHERE RDB$USER = ? '
                               'AND RDB$RELATION_NAME = ?', (username, role))
                assert cursor.fetchone()[0] == 0
            connection.commit()
            result['checks'].append('provider-role-admin-option-grant-revoke')

            def membership_state(default, admin):
                with connection.cursor() as cursor:
                    cursor.execute(
                        'SELECT RDB$FIELD_NAME, RDB$GRANT_OPTION FROM '
                        'RDB$USER_PRIVILEGES WHERE RDB$USER = ? AND '
                        'RDB$RELATION_NAME = ? AND RDB$PRIVILEGE = ?',
                        (username, role, 'M'))
                    rows = cursor.fetchall()
                    assert len(rows) == 1, rows
                    assert bool(rows[0][0]) == default, rows
                    assert rows[0][1] == (2 if admin else 0), rows
                connection.commit()

            for default_only, admin_only in ((True, False), (False, True),
                                             (True, True)):
                membership('grant', default_role=True, admin_option=True)
                membership_state(True, True)
                membership('revoke', default_role=default_only,
                           admin_option_only=admin_only)
                membership_state(not default_only, not admin_only)
                result['checks'].append(
                    f'combined-role-revoke-default-{default_only}'
                    f'-admin-{admin_only}-preserves-membership')
            membership('revoke')
            membership('grant')

            def role_check(label, active, allowed, selected_role=None):
                role_route = {**route}
                role_route.pop('role', None)
                if active:
                    role_route['role'] = selected_role or role
                attachment = driver.connect(
                    password=password, **_route_arguments(role_route, driver))
                try:
                    with attachment.cursor() as cursor:
                        cursor.execute('SELECT CURRENT_USER, CURRENT_ROLE '
                                       'FROM RDB$DATABASE')
                        user_value, role_value = cursor.fetchone()
                        assert user_value.strip() == username
                        if not active:
                            assert role_value.strip() == 'NONE', role_value
                        if allowed and active:
                            assert role_value.strip() == (
                                selected_role or role), role_value
                    attachment.commit()
                    plan = ADMINISTRATION.plan({
                        '_provider_route': role_route,
                        'resource_kind': 'index', 'operation_id': 'alter',
                        'draft': {'refresh_statistics': True},
                        'target_resource': {'display_name': index,
                                            'display_path': [table, index]}})
                    if allowed:
                        ADMINISTRATION.apply(client, plan,
                                             connection=attachment)
                        attachment.commit()
                    else:
                        try:
                            ADMINISTRATION.apply(client, plan,
                                                 connection=attachment)
                        except RelationalClientError as error:
                            assert 'execution failed (DatabaseError)' in str(
                                error), error
                        else:
                            raise AssertionError(
                                'Inactive/revoked role worked')
                        with attachment.cursor() as cursor:
                            try:
                                cursor.execute(
                                    f'SET STATISTICS INDEX "{index}"')
                            except driver.DatabaseError as error:
                                assert error.sqlstate == '28000', (
                                    error.sqlstate)
                            else:
                                raise AssertionError('Missing native denial')
                        attachment.rollback()
                    result['checks'].append(label)
                finally:
                    attachment.close()

            role_check('role-member-without-activation-denied', False, False)
            role_check('active-role-allows-index-statistics', True, True)
            privilege('revoke', role, 'ROLE')
            role_check('role-ddl-revoke-denies-index-statistics', True, False)
            privilege('grant', role, 'ROLE')
            membership('revoke')
            role_check('role-membership-revoke-denies-statistics', True, False)
            membership('grant', default_role=True)
            role_check('default-role-without-explicit-activation', False, True)
            membership('revoke', default_role=True)
            role_check('default-role-revocation-removes-implicit-access',
                       False, False)
            role_check('default-revocation-preserves-explicit-membership',
                       True, True)
            membership('revoke')
            nested = role + '_N'
            execute(f'CREATE ROLE "{nested}"')
            connection.commit()
            try:
                membership('grant', admin_option=True)
                for allowed in (True, False):
                    attachment = driver.connect(
                        password=password, **_route_arguments(route, driver))
                    try:
                        plan = ADMINISTRATION.plan({
                            '_provider_route': route,
                            'resource_kind': 'role', 'operation_id': 'grant',
                            'target_resource': {'display_name': role},
                            'draft': {'member': nested,
                                      'member_kind': 'ROLE'}})
                        if allowed:
                            forged = ADMINISTRATION.plan({
                                '_provider_route': route,
                                'resource_kind': 'role',
                                'operation_id': 'grant',
                                'target_resource': {'display_name': role},
                                'draft': {'member': nested,
                                          'member_kind': 'ROLE',
                                          'grantor': 'SYSDBA'}})
                            try:
                                ADMINISTRATION.apply(client, forged,
                                                     connection=attachment)
                            except RelationalClientError as error:
                                assert 'DatabaseError' in str(error), error
                            else:
                                raise AssertionError(
                                    'Member impersonated SYSDBA grantor')
                            result['checks'].append(
                                'role-admin-cannot-impersonate-grantor')
                        try:
                            ADMINISTRATION.apply(client, plan,
                                                 connection=attachment)
                        except RelationalClientError as error:
                            if allowed:
                                raise
                            assert 'execution failed (DatabaseError)' in str(
                                error), error
                        else:
                            assert allowed, 'Delegation without admin option'
                        if attachment.main_transaction.is_active():
                            attachment.rollback()
                    finally:
                        attachment.close()
                    if allowed:
                        membership('revoke', admin_option_only=True)
                result['checks'].append('role-delegation-allow-then-deny')
                membership('grant', nested, 'ROLE', default_role=True)
                membership('grant', selected_role=nested)
                role_check('nested-default-role-inherits-ddl-privilege',
                           True, True, nested)
                membership('revoke', nested, 'ROLE')
                role_check('nested-role-edge-revocation-removes-access',
                           True, False, nested)
                leaf = role + '_L'
                execute(f'CREATE ROLE "{leaf}"')
                connection.commit()
                try:
                    membership('grant', nested, 'ROLE', default_role=True)
                    membership('grant', leaf, 'ROLE', selected_role=nested,
                               default_role=True)
                    membership('grant', selected_role=leaf)
                    role_check('three-role-chain-inherits-ddl-privilege',
                               True, True, leaf)
                    membership('revoke', leaf, 'ROLE', selected_role=nested)
                    role_check('three-role-chain-cut-removes-ddl-privilege',
                               True, False, leaf)
                finally:
                    if connection.main_transaction.is_active():
                        connection.rollback()
                    execute(f'DROP ROLE "{leaf}"')
                    connection.commit()
                    with connection.cursor() as cursor:
                        cursor.execute('SELECT COUNT(*) FROM RDB$ROLES '
                                       'WHERE RDB$ROLE_NAME = ?', (leaf,))
                        assert cursor.fetchone()[0] == 0
                        cursor.execute(
                            'SELECT COUNT(*) FROM RDB$USER_PRIVILEGES '
                            'WHERE RDB$USER = ? OR RDB$RELATION_NAME = ?',
                            (leaf, leaf))
                        assert cursor.fetchone()[0] == 0
                    connection.commit()
                    result['temporary_leaf_role_removed'] = True
            finally:
                if connection.main_transaction.is_active():
                    connection.rollback()
                execute(f'DROP ROLE "{nested}"')
                connection.commit()
                with connection.cursor() as cursor:
                    cursor.execute('SELECT COUNT(*) FROM RDB$ROLES '
                                   'WHERE RDB$ROLE_NAME = ?', (nested,))
                    assert cursor.fetchone()[0] == 0
                    cursor.execute('SELECT COUNT(*) FROM RDB$USER_PRIVILEGES '
                                   'WHERE RDB$USER = ? OR '
                                   'RDB$RELATION_NAME = ?', (nested, nested))
                    assert cursor.fetchone()[0] == 0
                connection.commit()
                result['temporary_nested_role_removed'] = True
        finally:
            if connection.main_transaction.is_active():
                connection.rollback()
            execute(f'DROP ROLE "{role}"')
            connection.commit()
            with connection.cursor() as cursor:
                cursor.execute('SELECT COUNT(*) FROM RDB$ROLES '
                               'WHERE RDB$ROLE_NAME = ?', (role,))
                assert cursor.fetchone()[0] == 0
                cursor.execute('SELECT COUNT(*) FROM RDB$USER_PRIVILEGES '
                               'WHERE RDB$USER = ? OR RDB$RELATION_NAME = ?',
                               (role, role))
                assert cursor.fetchone()[0] == 0
            connection.commit()
            result['temporary_role_removed'] = True
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
