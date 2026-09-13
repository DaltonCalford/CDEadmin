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
    try:
        for allowed in (False, True, False):
            if allowed:
                privilege('grant', 'ALTER ANY')
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
            assert value['description'] == expected_comment, value
        result['checks'].append('role-comment-deny-grant-allow-revoke-deny')
    finally:
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
        result['checks'].append('role-recreation-as-owner-preserves-ownership')
    finally:
        if attachment is not None:
            attachment.close()
        if created:
            apply(connection, profile, 'role', 'drop',
                  {'confirmation': owned}, owned)
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
