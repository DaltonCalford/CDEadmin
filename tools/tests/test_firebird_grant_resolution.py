##########################################################################
# CDEadmin - Multi-engine Database Administration
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
##########################################################################

from tools.tests.test_cdeadmin_firebird_membership_metadata import resources
import pytest


def grants(rows):
    return [item for item in resources(rows)
            if item['resource_kind'] == 'privilege']


def test_invisible_native_grant_targets_are_not_discarded():
    result = grants([('U', 'Not.Visible', 'OLD', 'U', 'OWNER', 0, 8, 0)])
    assert len(result) == 1
    native = result[0]['native']
    assert native['object_name'] == 'Not.Visible'
    assert native['field'] == 'OLD'
    assert native['target_resolution']['state'] == 'unresolved'
    assert native['target_resolution']['effective_access_verified'] is False
    assert 'not evidence that access is absent' in (
        native['target_resolution']['warning'])


def test_same_display_grants_keep_distinct_native_security_identities():
    rows = [('U', 'Not.Visible', 'V', 'U', 'OWNER', 0, 8, 0),
            ('U', 'Not.Visible', 'V', 'U', 'OTHER', 0, 8, 0),
            ('U', 'Not.Visible', 'V', 'U', 'OWNER', 0, 13, 0),
            ('U', 'Not.Visible', 'V', 'U', 'OWNER', 1, 8, 0),
            ('U', 'Not.Visible', 'V', 'U', 'OWNER', 0, 8, 5)]
    result = grants(rows)
    assert len(result) == len(rows)
    identities = {item['resource_id'] for item in result}
    assert len(identities) == len(rows)
    assert identities == {item['resource_id'] for item in grants(rows[::-1])}


def test_resolved_role_membership_does_not_claim_effective_access():
    result = grants([('U', 'Role.With.Dot', 'D', 'M', 'OWNER', 2, 8, 13)])
    resolution = result[0]['native']['target_resolution']
    assert resolution == {
        'state': 'resolved', 'resource_ids': ['role:Role.With.Dot'],
        'authority': 'native-catalog-name-matching',
        'effective_access_verified': False,
    }


@pytest.mark.parametrize('code,name', [
    (22, 'TABLE'), (23, 'VIEW'), (24, 'PROCEDURE'), (25, 'FUNCTION'),
    (26, 'PACKAGE'), (27, 'SEQUENCE'), (28, 'DOMAIN'), (29, 'EXCEPTION'),
    (30, 'ROLE'), (31, 'CHARACTER SET'), (32, 'COLLATION'), (33, 'FILTER'),
])
def test_ddl_class_grants_are_not_misidentified_as_missing_objects(code, name):
    result = grants([('U', 'SQL$CLASS', None, 'C', 'OWNER', 0, 8, code)])
    resolution = result[0]['native']['target_resolution']
    assert resolution['state'] == 'class-scope'
    assert resolution['ddl_class'] == name
    assert resolution['resource_ids'] == []
    assert 'warning' not in resolution
