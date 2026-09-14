##########################################################################
# CDEadmin - Multi-engine Database Administration
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
##########################################################################

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tools.cdeadmin_firebird_admin_mapping_gate import _resources


def catalog(domain='RDB$1', constraint=None, relation_type=0,
            publication=False):
    cursor = Mock()
    current = []

    def execute(source):
        nonlocal current
        current = []
        if 'FROM RDB$RELATIONS R ORDER BY' in source:
            current = [('T', None, None, None, 128, 0, relation_type, None,
                        None, 'SYSDBA', None, 0, None, int(publication))]
        elif 'FROM RDB$RELATION_FIELDS RF JOIN RDB$RELATIONS R' in source:
            current = [('T', 'V', domain, None, None, 8, 0, 4, 0, None,
                        None, None, None, None, None, None, None, 0, None,
                        None, None)]
        elif constraint and 'FROM RDB$RELATION_CONSTRAINTS C JOIN' in source:
            if 'SYSTEM_FLAG, 0) = 0' in source:
                current = [('T', constraint, 'UNIQUE', 'IX')]
        elif constraint and 'FROM RDB$INDICES WHERE' in source:
            if 'SYSTEM_FLAG, 0) = 0' in source:
                current = [('T', 'IX', 1, 0, 0, 0, None, None, None)]
        elif constraint and 'FROM RDB$INDEX_SEGMENTS' in source:
            current = [('IX', 'V', 0)]

    cursor.execute.side_effect = execute
    cursor.fetchall.side_effect = lambda: current
    connection = SimpleNamespace(cursor=lambda: cursor,
                                 info=SimpleNamespace())
    values = _resources(connection, {'route': {'database': 'fixture.fdb'}})
    return next(item['native'] for item in values if
                item['resource_kind'] == 'table' and
                item['display_name'] == 'T')


@pytest.mark.parametrize('domain', ['rdb$domain', 'RDb$domain', 'rDb$domain'])
def test_case_sensitive_domain_reference_is_not_an_implicit_domain(domain):
    assert '"V" "' + domain + '"' in catalog(domain=domain)['ddl']


def test_true_implicit_domain_renders_its_native_type():
    assert '"V" INTEGER' in catalog()['ddl']


@pytest.mark.parametrize('name', ['INTEG_custom', 'integ_custom', 'INTEG_42'])
def test_constraint_prefix_never_discards_native_identity(name):
    assert 'CONSTRAINT "' + name + '" UNIQUE ("V")' in (
        catalog(constraint=name)['ddl'])


@pytest.mark.parametrize('relation_type', [4, 5])
@pytest.mark.parametrize('published', [False, True])
def test_temporary_recreation_overrides_destination_publication_policy(
        relation_type, published):
    ddl = catalog(relation_type=relation_type, publication=published)['ddl']
    state = 'ENABLE' if published else 'DISABLE'
    assert ddl.endswith(';\nALTER TABLE "T" ' + state + ' PUBLICATION;')
