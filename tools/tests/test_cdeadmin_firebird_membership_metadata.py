##########################################################################
# CDEadmin - Multi-engine Database Administration
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
##########################################################################

import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

import pytest

WEB = Path(__file__).resolve().parents[2] / 'web'
if str(WEB) not in sys.path:
    sys.path.insert(0, str(WEB))
if 'pgadmin' not in sys.modules:
    package = ModuleType('pgadmin')
    package.__path__ = [str(WEB / 'pgadmin')]
    sys.modules['pgadmin'] = package

from pgadmin.cdeadmin.providers.firebird.provider import (  # noqa: E402
    _resources, ADMINISTRATION,
)
from pgadmin.cdeadmin.visual_admin.catalog import (  # noqa: E402
    catalog_for_engine,
)


def resources(grants, creators=(), creator_error=False):
    cursor = Mock()
    rows = []

    def execute(source):
        nonlocal rows
        if 'FROM RDB$ROLES WHERE' in source and '= 0' in source:
            rows = [('Role.With.Dot', bytes(8), 'OWNER', None)]
        elif 'FROM RDB$USER_PRIVILEGES' in source:
            rows = grants
        elif 'FROM SEC$DB_CREATORS' in source:
            if creator_error:
                raise PermissionError('denied')
            rows = creators
        else:
            rows = []

    cursor.execute.side_effect = execute
    cursor.fetchall.side_effect = lambda: rows
    connection = Mock(info=SimpleNamespace())
    connection.cursor.return_value = cursor
    result = _resources(connection, {'route': {'database': 'sample.fdb'}})
    cursor.close.assert_called_once()
    return result


def test_membership_identity_flags_and_quoted_names_are_lossless():
    result = resources([
        ('Member', 'Role.With.Dot', 'D', 'M', 'OWNER', 2, 13, 13),
        ('Member', 'Role.With.Dot', 'D', 'M', 'SYSDBA', 2, 13, 13),
        ('User', 'Role.With.Dot', None, 'M', 'OWNER', 0, 8, 13),
    ])
    role = next(item['native'] for item in result
                if item['resource_kind'] == 'role')
    assert len(role['memberships']) == 3
    assert len({item['resource_id'] for item in result
                if item['resource_kind'] == 'privilege'}) == 3
    assert all(grant['field'] is None for grant in role['memberships'])
    assert role['membership_recreation_statements'] == [
        'GRANT DEFAULT "Role.With.Dot" TO ROLE "Member" '
        'WITH ADMIN OPTION GRANTED BY USER "OWNER"',
        'GRANT DEFAULT "Role.With.Dot" TO ROLE "Member" '
        'WITH ADMIN OPTION GRANTED BY USER "SYSDBA"',
        'GRANT "Role.With.Dot" TO USER "User" GRANTED BY USER "OWNER"',
    ]


@pytest.mark.parametrize('user_type,grantor', [(99, 'OWNER'), (8, None)])
def test_unknown_membership_metadata_blocks_the_entire_replay(user_type,
                                                              grantor):
    result = resources([
        ('Valid', 'Role.With.Dot', None, 'M', 'OWNER', 0, 8, 13),
        ('Unknown', 'Role.With.Dot', None, 'M', grantor, 0, user_type, 13),
    ])
    role = next(item['native'] for item in result
                if item['resource_kind'] == 'role')
    assert role['membership_recreation_statements'] == []
    assert role['membership_recreation_unavailable_reason']
    assert len(role['memberships']) == 2


def test_firebird_drop_forms_do_not_offer_fabricated_cascade():
    catalog = ADMINISTRATION.catalog(catalog_for_engine('firebird'))
    for resource in catalog['objects']:
        for operation in resource['operations']:
            if operation['operation_id'] == 'drop':
                assert 'cascade' not in {
                    field['field_id']
                    for field in operation['form']['fields']}


@pytest.mark.parametrize('kind', [
    'role', 'table', 'view', 'procedure', 'domain',
])
def test_firebird_drop_rejects_requested_cascade(kind):
    result = ADMINISTRATION.validate({
        'resource_kind': kind, 'operation_id': 'drop',
        'target_resource': {'display_name': 'EXAMPLE'},
        'draft': {'cascade': True, 'confirmation': 'EXAMPLE'},
    })
    assert any(error['code'] == 'firebird_drop_cascade_unsupported'
               for error in result['errors'])
