"""Catalog statements retain PSQL and comments without script splitting."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tools.cdeadmin_firebird_admin_mapping_gate import _resources


HEADER = 'BEGIN FUNCTION F RETURNS INTEGER; END'
BODY = "BEGIN FUNCTION F RETURNS INTEGER AS BEGIN /* ; */ RETURN 2; END END"
COMMENT = "Owner's ; notes\n東京"


def catalog(dialect, security, body, comment):
    cursor = Mock()
    rows = []

    def execute(source):
        nonlocal rows
        rows = []
        if 'COALESCE(RDB$SYSTEM_FLAG, 0) = 0' not in source:
            return
        if 'FROM RDB$PACKAGES WHERE' in source:
            rows = [('P', HEADER, body, comment, 1 if body else 0, security)]
        elif 'RDB$GENERATORS WHERE' in source:
            rows = [('S', 7, 3, 'SYSDBA', comment)]
    cursor.execute.side_effect = execute
    cursor.fetchall.side_effect = lambda: rows
    handle = SimpleNamespace(cursor=lambda: cursor,
                             info=SimpleNamespace(sql_dialect=dialect))
    return {item['resource_kind']: item['native'] for item in
            _resources(handle, {'route': {'database': 'fixture'}}) if
            item['resource_kind'] in {'package', 'sequence'}}


@pytest.mark.parametrize('dialect', [1, 3])
@pytest.mark.parametrize('security', [
    None, 0, 1, False, True, 'False', 'True'])
@pytest.mark.parametrize('body', [None, BODY])
@pytest.mark.parametrize('comment', [None, COMMENT])
def test_package_boundaries_source_security_and_comments(
        dialect, security, body, comment):
    native = catalog(dialect, security, body, comment)['package']
    statements = native['recreation_statements']
    assert len(statements) == 1 + bool(body) + (comment is not None)
    assert statements[0].endswith('AS\n' + HEADER)
    expected = {None: 'INHERIT', 0: 'INVOKER', 1: 'DEFINER',
                'False': 'INVOKER', 'True': 'DEFINER'}[security]
    assert native['package_sql_security'] == expected
    if security is not None:
        assert 'SQL SECURITY ' + expected in statements[0]
    else:
        assert 'SQL SECURITY' not in statements[0]
    if body:
        assert statements[1].endswith('AS\n' + BODY)
    if comment is not None:
        assert statements[-1].endswith("IS 'Owner''s ; notes\n東京'")
    assert native['ddl'] == ';\n\n'.join(statements) + ';'


@pytest.mark.parametrize('dialect', [1, 3])
@pytest.mark.parametrize('comment', [None, '', COMMENT])
def test_sequence_comments_are_separate_without_changing_initial_values(
        dialect, comment):
    native = catalog(dialect, None, None, comment)['sequence']
    statements = native['recreation_statements']
    assert len(statements) == 1 + (comment is not None)
    assert statements[0].endswith('START WITH 7 INCREMENT BY 3')
    assert native['initial_value'] == '7'
    assert native['increment'] == 3
    if comment is not None:
        assert statements[-1].endswith("IS '" +
                                       comment.replace("'", "''") + "'")
    assert native['ddl'] == ';\n'.join(statements) + ';'


@pytest.mark.parametrize('dialect', [1, 3])
@pytest.mark.parametrize('comment', [None, '', COMMENT])
@pytest.mark.parametrize('kind', ['exception', 'procedure', 'trigger'])
def test_object_comments_preserve_statement_boundaries(dialect, comment, kind):
    cursor = Mock()
    rows = []
    source_body = "BEGIN /* Preserve ; and 'quotes' */ END"
    fixtures = {
        'exception': ('RDB$EXCEPTIONS', ('E', "A;B's", comment)),
        'procedure': ('RDB$PROCEDURES', (
            'P', None, source_body, comment, 2, 1, False, None, None)),
        'trigger': ('RDB$TRIGGERS', (
            'TR', 'T', 1, 0, 0, source_body, comment, False, None, None)),
    }
    table, row = fixtures[kind]

    def execute(source):
        nonlocal rows
        rows = ([row] if f'FROM {table} WHERE ' in source and
                'COALESCE(RDB$SYSTEM_FLAG, 0) = 0' in source else [])

    cursor.execute.side_effect = execute
    cursor.fetchall.side_effect = lambda: rows
    handle = SimpleNamespace(cursor=lambda: cursor,
                             info=SimpleNamespace(sql_dialect=dialect))
    objects = _resources(handle, {'route': {'database': 'fixture'}})
    native = next(item['native'] for item in objects if
                  item['resource_kind'] == kind)
    statements = native['recreation_statements']
    assert len(statements) == 1 + (comment is not None)
    if kind != 'exception':
        assert statements[0].endswith(source_body)
    else:
        assert statements[0].endswith("'A;B''s'")
    if comment is not None:
        name = row[0] if dialect == 1 else '"' + row[0] + '"'
        assert statements[1] == (
            f'COMMENT ON {kind.upper()} {name} IS ' +
            "'" + comment.replace("'", "''") + "'")
    assert native['ddl'] == ';\n'.join(statements) + ';'
