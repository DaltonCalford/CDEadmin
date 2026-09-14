"""Role ownership and comment authority on disposable native fixtures."""


def verify(connection, client, profile, route, password, username, role,
           result):
    from firebird import driver
    from pgadmin.cdeadmin.providers.firebird.provider import (
        ADMINISTRATION, _resources, _route_arguments)
    from pgadmin.cdeadmin.sdk.relational import RelationalClientError

    def apply(attachment, selected_route, kind, operation, draft, name=role):
        plan = ADMINISTRATION.plan({
            '_provider_route': selected_route, 'resource_kind': kind,
            'operation_id': operation, 'draft': draft,
            'target_resource': {'display_name': name}})
        ADMINISTRATION.apply(client, plan, connection=attachment)
        if kind == 'role' and operation in {'grant', 'revoke'}:
            result.setdefault('role_dialect_task_evidence', {})[
                'visual_admin.role.' + operation] = {
                    'command_preview': plan['command_preview'],
                    'live_execution': 'passed',
                }

    def privilege(operation, permission):
        apply(connection, profile, 'privilege', operation, {
            'privilege_scope': 'ddl_class', 'ddl_class': 'ROLE',
            'ddl_privileges': [permission], 'ddl_principal_kind': 'USER',
            'principal': username})
        connection.commit()

    def inspect(name):
        value = next(item for item in _resources(
            connection, {'route': profile})
                     if item['resource_kind'] == 'role' and
                     item['display_name'] == name)['native']
        connection.commit()
        return value

    expected_comment = inspect(role).get('description')
    alter_granted = False
    try:
        for allowed in (False, True, False):
            if allowed:
                privilege('grant', 'ALTER ANY')
                alter_granted = True
            attachment = driver.connect(
                password=password, **_route_arguments(route, driver))
            try:
                try:
                    apply(attachment, route, 'role', 'alter', {
                        'description': 'Authorized comment'})
                except RelationalClientError as error:
                    if allowed:
                        raise
                    assert 'DatabaseError' in str(error), error
                else:
                    assert allowed, 'Unauthorized role comment succeeded'
                    attachment.commit()
            finally:
                attachment.close()
            value = inspect(role)
            assert value['owner'] == 'SYSDBA', value
            if allowed:
                assert value['description'] == 'Authorized comment'
                expected_comment = 'Authorized comment'
                privilege('revoke', 'ALTER ANY')
                alter_granted = False
            assert value['description'] == expected_comment, value
        result['checks'].append('role-comment-deny-grant-allow-revoke-deny')
    finally:
        if alter_granted:
            privilege('revoke', 'ALTER ANY')

    owned = role + '_O'
    created = False
    privilege('grant', 'CREATE')
    attachment = None
    try:
        attachment = driver.connect(
            password=password, **_route_arguments(route, driver))
        apply(attachment, route, 'role', 'create', {
            'name': owned, 'description': 'Owned role'}, owned)
        attachment.commit()
        created = True
        value = inspect(owned)
        assert value['owner'] == username, value
        assert value['recreation_requirements']['execute_as_user'] == username
        result['checks'].append('role-owner-is-creating-user')
        apply(attachment, route, 'role', 'alter', {
            'description': 'Owner-edited comment'}, owned)
        attachment.commit()
        assert inspect(owned)['description'] == 'Owner-edited comment'
        result['checks'].append('role-owner-can-edit-comment')
        try:
            apply(attachment, route, 'role', 'alter', {
                'system_privileges': ['MONITOR_ANY_ATTACHMENT']}, owned)
        except RelationalClientError as error:
            assert 'DatabaseError' in str(error), error
        else:
            raise AssertionError('Ownership granted privileged-role authority')
        assert inspect(owned)['system_privileges'] == []
        result['checks'].append('role-owner-without-system-authority-denied')
        apply(connection, profile, 'role', 'alter', {
            'system_privileges': ['CREATE_PRIVILEGED_ROLES']})
        apply(connection, profile, 'role', 'grant', {
            'member': username, 'member_kind': 'USER', 'default_role': True})
        connection.commit()
        attachment.close()
        attachment = driver.connect(
            password=password, **_route_arguments(route, driver))
        apply(attachment, route, 'role', 'alter', {
            'system_privileges': ['MONITOR_ANY_ATTACHMENT']}, owned)
        attachment.commit()
        result['checks'].append('role-owner-with-system-authority-allowed')
        apply(attachment, route, 'role', 'grant', {
            'member': role, 'member_kind': 'ROLE', 'default_role': True,
            'admin_option': True}, owned)
        attachment.commit()
        apply(connection, profile, 'role', 'grant', {
            'member': role, 'member_kind': 'ROLE', 'default_role': True,
            'admin_option': True}, owned)
        connection.commit()
        apply(attachment, route, 'role', 'grant', {
            'member': username, 'member_kind': 'USER'}, owned)
        attachment.commit()
        membership = inspect(owned)
        grants = membership['memberships']
        assert len(grants) == 3, grants
        assert {grant['grantor'] for grant in grants} == {username, 'SYSDBA'}
        for grant in grants:
            is_role = grant['user_type'] == 13
            assert grant['grantee'] == (role if is_role else username), grants
            assert grant['default_role'] is is_role, grants
            assert grant['grant_option'] == (2 if is_role else 0), grants
            assert grant['field'] is None, grants
        apply(connection, profile, 'role', 'revoke', {
            'member': role, 'member_kind': 'ROLE'}, owned)
        apply(connection, profile, 'role', 'revoke', {
            'member': username, 'member_kind': 'USER'}, owned)
        connection.commit()
        assert inspect(owned)['memberships'] == []
        with connection.cursor() as cursor:
            for statement in membership['membership_recreation_statements']:
                cursor.execute(statement)
        connection.commit()
        assert inspect(owned)['memberships'] == grants
        result['role_memberships_replayed'] = len(grants)
        result['checks'].append('role-membership-default-admin-grantor-replay')
        apply(connection, profile, 'role', 'revoke', {
            'member': role, 'member_kind': 'ROLE'}, owned)
        apply(connection, profile, 'role', 'revoke', {
            'member': username, 'member_kind': 'USER'}, owned)
        connection.commit()
        value = inspect(owned)
        apply(attachment, route, 'role', 'drop',
              {'confirmation': owned}, owned)
        attachment.commit()
        created = False
        with attachment.cursor() as cursor:
            for statement in value['recreation_statements']:
                cursor.execute(statement)
        attachment.commit()
        created = True
        replayed = inspect(owned)
        assert replayed['owner'] == username, replayed
        assert replayed['description'] == value['description'], replayed
        assert replayed['system_privileges'] == ['MONITOR_ANY_ATTACHMENT']
        assert replayed['recreation_requirements'][
            'requires_privileged_role_creation_authority'] is True
        result['checks'].append('role-recreation-as-owner-preserves-ownership')
        result['checks'].append('privileged-role-owner-recreation')
    finally:
        if attachment is not None:
            attachment.close()
        if created:
            apply(connection, profile, 'role', 'drop',
                  {'confirmation': owned}, owned)
            connection.commit()
        apply(connection, profile, 'role', 'revoke', {
            'member': username, 'member_kind': 'USER'})
        apply(connection, profile, 'role', 'alter', {
            'drop_system_privileges': True})
        connection.commit()
        privilege('revoke', 'CREATE')
        with connection.cursor() as cursor:
            cursor.execute('SELECT COUNT(*) FROM RDB$ROLES '
                           'WHERE RDB$ROLE_NAME = ?', (owned,))
            assert cursor.fetchone()[0] == 0
            cursor.execute('SELECT COUNT(*) FROM RDB$USER_PRIVILEGES '
                           'WHERE RDB$USER = ? OR RDB$RELATION_NAME = ?',
                           (owned, owned))
            assert cursor.fetchone()[0] == 0
        connection.commit()
        result['temporary_owned_role_removed'] = True
