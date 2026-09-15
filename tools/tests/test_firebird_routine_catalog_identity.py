"""Names shared by standalone and packaged routines retain native identity."""

from types import SimpleNamespace
from unittest.mock import Mock

from tools.cdeadmin_firebird_admin_mapping_gate import _resources


def catalog(computed=False):
    cursor = Mock()
    current = []

    def execute(source, *_parameters):
        nonlocal current
        current = []
        if 'SYSTEM_FLAG, 0) > 0' in source:
            return
        if 'FROM RDB$FUNCTIONS WHERE' in source and (
                'RDB$MODULE_NAME IS NULL' in source):
            current = [('F', package, None, None, 0, 1, 0, None, None,
                        0, 0, 0) for package in (None, 'PK', 'PK2')]
        elif 'FROM RDB$PROCEDURES WHERE' in source:
            current = [('P', package, None, None, 0, 1, 0, None, None)
                       for package in (None, 'PK', 'PK2')]
            current += [(name, None, None, None, 0, 1, 0, None, None)
                        for name in ('CALL0', 'CALL1', 'CALL2')]
        elif 'FROM RDB$PACKAGES WHERE' in source:
            current = [(name, None, None, None, 1, 0)
                       for name in ('PK', 'PK2')]
        elif 'FROM RDB$DEPENDENCIES' in source:
            current = [(name, 5, 'F', 15, None, package) for name, package in
                       (('CALL0', None), ('CALL1', 'PK'), ('CALL2', 'PK2'))]
            if computed:
                current.append(('RDB$COMPUTED', 3, 'F', 15, None, 'PK'))
        elif computed and 'FROM RDB$RELATIONS R ORDER BY' in source:
            current = [('V0', 'SELECT PK.F() AS C FROM RDB$DATABASE',
                        None, None, 128, 0, 1, None, None, 'SYSDBA', None,
                        0, None, 0)]
        elif computed and 'FROM RDB$RELATION_FIELDS RF JOIN' in source:
            current = [('V0', 'C', 'RDB$COMPUTED', None, None, 8, 0, 4, 0,
                        None, None, None, None, None, None, None,
                        'PK.F()', 0, None, None, None)]
        elif 'FROM RDB$USER_PRIVILEGES' in source:
            current = [('Reader', 'F', None, 'X', 'SYSDBA', 0, 8, 15),
                       ('PackageReader', 'PK', None, 'X', 'SYSDBA', 0, 8, 18)]

    cursor.execute.side_effect = execute
    cursor.fetchall.side_effect = lambda: current
    return _resources(SimpleNamespace(cursor=lambda: cursor,
                                      info=SimpleNamespace()), {})


def test_standalone_and_packaged_routines_do_not_overwrite_each_other():
    resources = catalog()
    for kind, name in (('function', 'F'), ('procedure', 'P')):
        routines = [item for item in resources if
                    item['resource_kind'] == kind and
                    item['display_name'] == name]
        assert len(routines) == 3
        assert len({item['resource_id'] for item in routines}) == 3
        assert {tuple(item['display_path']) for item in routines} == {
            (name,), ('PK', name), ('PK2', name)}
        assert {tuple(item['authority_path']) for item in routines} == {
            (kind, name), ('PK', kind, name), ('PK2', kind, name)}
        for routine in routines:
            package = routine['native'].get('package')
            if package:
                parent = next(item for item in resources if
                              item['resource_kind'] == 'package' and
                              item['display_name'] == package)
                assert routine['native']['navigator_parent_resource_id'] == (
                    parent['resource_id'])
                administration = routine['native']['administration']
                assert administration['definition_owner'] == {
                    'resource_id': parent['resource_id'],
                    'resource_kind': 'package', 'display_name': package}
                assert set(administration['allowed_operations']) == {
                    'inspect', 'comment', 'grant', 'revoke'}
            else:
                assert 'administration' not in routine['native']


def test_dependencies_select_the_exact_package_not_every_same_named_function():
    resources = catalog()
    for package, caller in ((None, 'CALL0'), ('PK', 'CALL1'),
                            ('PK2', 'CALL2')):
        routine = next(item for item in resources if
                       item['resource_kind'] == 'function' and
                       item['native'].get('package') == package)
        assert [item['object_name'] for item in
                routine['native']['dependents']] == [caller]
        dependent = next(item for item in resources if
                         item['display_name'] == caller)
        assert dependent['native']['dependencies'][0]['package_name'] == (
            package)


def test_standalone_grant_does_not_leak_to_package_members():
    resources = catalog()
    routines = [item for item in resources if
                item['resource_kind'] == 'function']
    for routine in routines:
        native = routine['native']
        grantees = [item['grantee'] for item in native.get('privileges', [])]
        if native.get('package'):
            assert 'Reader' not in grantees
            inherited = native['package_privileges']
            assert inherited['package'] == native['package']
            assert inherited['effective_access_verified'] is False
            assert [item['grantee'] for item in inherited['privileges']] == (
                ['PackageReader'] if native['package'] == 'PK' else [])
        else:
            assert grantees == ['Reader']
            assert native['privileges'][0]['target_resolution']['state'] == (
                'resolved')


def test_computed_dependencies_resolve_real_columns_and_relation_owners():
    resources = catalog(computed=True)
    function = next(item for item in resources if
                    item['resource_kind'] == 'function' and
                    item['native'].get('package') == 'PK')
    dependency = next(item for item in function['native']['dependents'] if
                      item['object_name'] == 'RDB$COMPUTED')
    resolution = dependency['dependent_resolution']
    assert resolution['state'] == 'resolved'
    assert resolution['authority'] == 'RDB$RELATION_FIELDS.RDB$FIELD_SOURCE'
    assert {(item['resource_kind'], tuple(item['display_path'])) for item in
            resolution['resources']} == {('column', ('V0', 'C')),
                                         ('view', ('V0',))}
    for kind in ('column', 'view'):
        owner = next(item for item in resources if
                     item['resource_kind'] == kind)
        assert owner['native']['dependencies'][0]['via_computed_field'] == (
            'RDB$COMPUTED')
    for other in resources:
        if other['resource_kind'] == 'function' and other['native'].get(
                'package') != 'PK':
            assert not any(item['object_name'] == 'RDB$COMPUTED' for item in
                           other['native'].get('dependents', []))
