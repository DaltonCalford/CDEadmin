"""Native selector round trips on an unassigned disposable Firebird role."""

import uuid


def verify(connection, client, profile, result):
    from pgadmin.cdeadmin.providers.firebird.provider import (
        ADMINISTRATION, _resources)
    from pgadmin.cdeadmin.providers.relational_admin import (
        FIREBIRD_SYSTEM_PRIVILEGES)

    name = 'CDE_SP_' + uuid.uuid4().hex[:12].upper()
    created = False

    def apply(operation, draft):
        plan = ADMINISTRATION.plan({
            '_provider_route': profile, 'resource_kind': 'role',
            'operation_id': operation, 'target_resource': {
                'display_name': name}, 'draft': draft})
        ADMINISTRATION.apply(client, plan, connection=connection)

    def state():
        with connection.cursor() as cursor:
            cursor.execute('SELECT RDB$SYSTEM_PRIVILEGES FROM RDB$ROLES '
                           'WHERE RDB$ROLE_NAME = ?', (name,))
            rows = cursor.fetchall()
            assert len(rows) == 1
            return bytes(rows[0][0])

    try:
        with connection.cursor() as cursor:
            cursor.execute('SELECT TRIM(RDB$TYPE_NAME) FROM RDB$TYPES '
                           'WHERE RDB$FIELD_NAME = ?',
                           ('RDB$SYSTEM_PRIVILEGES',))
            native = {row[0] for row in cursor.fetchall()}
            assert native == set(FIREBIRD_SYSTEM_PRIVILEGES), native
        connection.commit()
        result['checks'].append('system-privilege-selector-native-catalog')
        cases = [(privilege, [privilege])
                 for privilege in FIREBIRD_SYSTEM_PRIVILEGES]
        cases.extend((('empty', []),
                      ('all', list(FIREBIRD_SYSTEM_PRIVILEGES))))
        for privilege, selected in cases:
            comment = ("  Owner's notes\n" + 'x' * 50000 + '\n  '
                       if privilege == 'all' else '')
            # Firebird stores an empty comment as NULL.
            expected_comment = comment or None
            apply('create', {'name': name, 'system_privileges': selected,
                             'description': comment})
            connection.commit()
            created = True
            original = state()
            assert bool(any(original)) == bool(selected), privilege
            connection.commit()
            resource = next(item for item in _resources(connection, {
                'route': profile}) if item['resource_kind'] == 'role' and
                item['display_name'] == name)
            native = resource['native']
            assert native['system_privileges'] == selected, native
            assert native['unknown_system_privilege_bits'] == [], native
            assert native['description'] == expected_comment, native
            statements = native['recreation_statements']
            connection.commit()
            apply('drop', {'confirmation': name})
            connection.commit()
            created = False
            with connection.cursor() as cursor:
                for statement in statements:
                    cursor.execute(statement)
            connection.commit()
            created = True
            assert state() == original, privilege
            connection.commit()
            result['checks'].append('system-privilege-metadata-replay-' +
                                    privilege)
            with connection.cursor() as cursor:
                cursor.execute('SELECT RDB$DESCRIPTION FROM RDB$ROLES '
                               'WHERE RDB$ROLE_NAME = ?', (name,))
                assert cursor.fetchone()[0] == expected_comment
            connection.commit()
            apply('alter', {'description': "Edited role's comment\n "})
            connection.commit()
            apply('alter', {'clear_description': True})
            connection.rollback()
            with connection.cursor() as cursor:
                cursor.execute('SELECT RDB$DESCRIPTION FROM RDB$ROLES '
                               'WHERE RDB$ROLE_NAME = ?', (name,))
                assert cursor.fetchone()[0] == "Edited role's comment\n "
            connection.commit()
            apply('alter', {'clear_description': True})
            connection.commit()
            with connection.cursor() as cursor:
                cursor.execute('SELECT RDB$DESCRIPTION FROM RDB$ROLES '
                               'WHERE RDB$ROLE_NAME = ?', (name,))
                assert cursor.fetchone()[0] is None
            connection.commit()
            assert state() == original, privilege
            connection.commit()
            result['checks'].append('role-comment-lifecycle-' + privilege)
            apply('alter', {'drop_system_privileges': True})
            assert not any(state()), privilege
            connection.rollback()
            assert state() == original, privilege
            connection.commit()
            apply('alter', {'drop_system_privileges': True})
            connection.commit()
            assert not any(state()), privilege
            connection.commit()
            apply('alter', {'system_privileges': selected} if selected else
                  {'drop_system_privileges': True})
            connection.commit()
            assert state() == original, privilege
            connection.commit()
            apply('drop', {'confirmation': name})
            connection.commit()
            created = False
            result['checks'].append('system-privilege-roundtrip-' + privilege)
    finally:
        if connection.main_transaction.is_active():
            connection.rollback()
        if created:
            apply('drop', {'confirmation': name})
            connection.commit()
        with connection.cursor() as cursor:
            cursor.execute('SELECT COUNT(*) FROM RDB$ROLES '
                           'WHERE RDB$ROLE_NAME = ?', (name,))
            assert cursor.fetchone()[0] == 0
        connection.commit()
        result['temporary_system_privilege_role_removed'] = True
