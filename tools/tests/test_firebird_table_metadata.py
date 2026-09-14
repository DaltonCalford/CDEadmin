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
            publication=False, not_null_name=None, column_binding=True,
            table_comment=None, column_comment=None, index_direction=0,
            index_metadata=True, primary=False, identity=False):
    cursor = Mock()
    current = []

    def execute(source):
        nonlocal current
        current = []
        if 'FROM RDB$RELATIONS R ORDER BY' in source:
            current = [('T', None, None, table_comment, 128, 0,
                        relation_type, None,
                        None, 'SYSDBA', None, 0, None, int(publication))]
        elif 'FROM RDB$RELATION_FIELDS RF JOIN RDB$RELATIONS R' in source:
            current = [('T', 'V', domain,
                        1 if not_null_name or primary or identity else None,
                        None, 8, 0, 4, 0, None, None, None, None, None,
                        0 if identity else None, 'G' if identity else None,
                        None, 0, column_comment,
                        1 if identity else None, 1 if identity else None)]
        elif 'FROM RDB$RELATION_CONSTRAINTS C JOIN' in source:
            if 'SYSTEM_FLAG, 0) = 0' in source:
                if constraint:
                    current.append(('T', constraint,
                                    'PRIMARY KEY' if primary else 'UNIQUE',
                                    'IX'))
                if not_null_name:
                    current.append(('T', not_null_name, 'NOT NULL', None))
        elif 'FROM RDB$CHECK_CONSTRAINTS CC JOIN RDB$RELATION_CONSTRAINTS' in (
                source):
            if not_null_name and column_binding:
                current = [(not_null_name, 'V')]
        elif constraint and 'FROM RDB$INDICES WHERE' in source:
            if 'SYSTEM_FLAG, 0) = 0' in source and index_metadata:
                current = [('T', 'IX', 1, 0, index_direction, 0,
                            None, None, None)]
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


def test_named_not_null_is_bound_to_its_column():
    assert '"V" INTEGER CONSTRAINT "Named NN" NOT NULL' in (
        catalog(not_null_name='Named NN')['ddl'])


@pytest.mark.parametrize('options', [
    {'primary': True, 'constraint': 'PK'}, {'identity': True},
])
def test_implicit_not_null_does_not_invent_a_constraint(options):
    assert 'NOT NULL' not in catalog(**options)['ddl']


def test_missing_not_null_binding_blocks_incomplete_table_recreation():
    native = catalog(not_null_name='Named NN', column_binding=False)
    assert native['ddl_available'] is False
    assert 'NOT NULL' in native['ddl_unavailable_reason']
    assert not native.get('ddl')


@pytest.mark.parametrize('direction,expected', [
    (None, 'ASCENDING'), (0, 'ASCENDING'), (1, 'DESCENDING'),
])
def test_backing_index_identity_and_direction_are_preserved(
        direction, expected):
    native = catalog(constraint='K', index_direction=direction)
    assert 'USING ' + expected + ' INDEX "IX"' in native['ddl']


@pytest.mark.parametrize('options', [
    {'index_metadata': False}, {'index_direction': 'invalid'},
    {'index_direction': 2},
])
def test_missing_or_unknown_index_metadata_never_drops_the_constraint(options):
    native = catalog(constraint='K', **options)
    assert native['ddl_available'] is False
    assert not native.get('ddl')


def test_comments_replay_as_separate_quoted_native_statements():
    native = catalog(table_comment="  table 'é';  ",
                     column_comment="  column 'é';\n  ")
    assert native['recreation_statements'][1:] == [
        'COMMENT ON TABLE "T" IS \'  table \'\'é\'\';  \'',
        'COMMENT ON COLUMN "T"."V" IS \'  column \'\'é\'\';\n  \'',
    ]
