"""Catalog statements retain PSQL and comments without script splitting."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tools.cdeadmin_firebird_admin_mapping_gate import _resources


HEADER = 'BEGIN FUNCTION F RETURNS INTEGER; END'
BODY = "BEGIN FUNCTION F RETURNS INTEGER AS BEGIN /* ; */ RETURN 2; END END"
COMMENT = "Owner's ; notes\n東京"


@pytest.mark.parametrize('dialect', [1, 3])
@pytest.mark.parametrize('kind', ['procedure', 'function', 'trigger'])
@pytest.mark.parametrize('source_body', [None, '', '  ', COMMENT])
@pytest.mark.parametrize('entry', [None, "module!entry'point"])
def test_external_routine_body_is_optional_quoted_text(
        dialect, kind, source_body, entry):
    cursor = Mock()
    rows = []
    fixtures = {
        'procedure': ('RDB$PROCEDURES', (
            'P', None, source_body, COMMENT, 2, 1, None, entry, 'UDR')),
        'function': ('RDB$FUNCTIONS', (
            'P', None, source_body, COMMENT, 0, 1, None, entry, 'UDR',
            1, 0, 0)),
        'trigger': ('RDB$TRIGGERS', (
            'P', 'T', 1, 0, 0, source_body, COMMENT, None, entry, 'UDR')),
    }
    table, row = fixtures[kind]

    def execute(source):
        nonlocal rows
        rows = ([row] if f'FROM {table} WHERE ' in source and
                'COALESCE(RDB$SYSTEM_FLAG, 0) = 0' in source else [])
        if kind == 'function' and 'RDB$FUNCTION_ARGUMENTS A ' in source:
            rows = [('P', None, None, 0, 'RDB$1', None, None, 0, 0,
                     8, 0, 4, 0, 9, None, None, None, None, None, None, None)]

    cursor.execute.side_effect = execute
    cursor.fetchall.side_effect = lambda: rows
    handle = SimpleNamespace(cursor=lambda: cursor,
                             info=SimpleNamespace(sql_dialect=dialect))
    native = next(item['native'] for item in
                  _resources(handle, {'route': {'database': 'fixture'}})
                  if item['resource_kind'] == kind)
    statements = native['recreation_statements']
    assert len(statements) == 2
    expected = 'EXTERNAL'
    if entry:
        expected += " NAME '" + entry.replace("'", "''") + "'"
    expected += ' ENGINE ' + ('UDR' if dialect == 1 else '"UDR"')
    if source_body is not None:
        expected += "\nAS '" + source_body.replace("'", "''") + "'"
    assert statements[0].endswith(expected)
    assert 'SQL SECURITY' not in statements[0]
    if kind == 'function':
        assert 'DETERMINISTIC' in statements[0]
    assert statements[1].endswith("'" + COMMENT.replace("'", "''") + "'")


@pytest.mark.parametrize('flag,validity', [
    (None, 'unknown'), ('unexpected', 'unknown'), (1, 'valid'),
    (True, 'valid'), ('True', 'valid'), ('1', 'valid'), (0, 'invalid'),
    (False, 'invalid'), ('False', 'invalid'), ('0', 'invalid')])
@pytest.mark.parametrize('body', [None, BODY])
def test_package_body_validity_is_observed_not_inferred(flag, validity, body):
    from pgadmin.cdeadmin.providers.firebird import packages
    observed = packages.body_metadata({
        'valid_body': flag, 'body_source': body})
    assert observed == {
        'validity': validity, 'source_available': bool(body)}


@pytest.mark.parametrize('domain', ['RDB$17', 'CUSTOM_DOMAIN', None, 'RDB$X'])
def test_native_gate_normalizes_only_allocated_domain_numbers(domain):
    from tools.cdeadmin_firebird_catalog_dialect_gate import comparable_fields
    native = {'parameters': [{'domain': domain, 'description': COMMENT,
                              'field_type': 8}]}
    actual = comparable_fields(native, ['parameters'])['parameters'][0]
    assert actual['domain'] == (
        '<implicit-domain>' if domain == 'RDB$17' else domain)
    assert actual['description'] == COMMENT
    assert actual['field_type'] == 8
    assert native['parameters'][0]['domain'] == domain


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
@pytest.mark.parametrize('kind', ['procedure', 'function'])
@pytest.mark.parametrize('packaged', [False, True])
@pytest.mark.parametrize('private_flag', [None, 0, 1])
def test_parameter_comments_are_exported_after_routine(
        dialect, comment, kind, packaged, private_flag):
    cursor = Mock()
    rows = []
    source_body = 'BEGIN END' if kind == 'procedure' else (
        'BEGIN RETURN X; END')
    parameter_name = 'X' if dialect == 1 else 'X"Y'
    package = ('PKG' if dialect == 1 else 'Pk"G') if packaged else None
    member_comment = COMMENT if packaged else None

    def execute(source):
        nonlocal rows
        rows = []
        if 'COALESCE(RDB$SYSTEM_FLAG, 0) = 0' in source:
            if packaged and 'FROM RDB$PACKAGES WHERE ' in source:
                rows = [(package, HEADER, None, None, 0, None)]
            if kind == 'procedure' and 'FROM RDB$PROCEDURES WHERE ' in source:
                rows = [('P', package, source_body, member_comment, 2, 1,
                         False, None,
                         None, private_flag)]
            if kind == 'function' and 'FROM RDB$FUNCTIONS WHERE ' in source:
                rows = [('P', package, source_body, member_comment, 0, 1,
                         False, None,
                         None, 0, 0, 0, private_flag)]
            if rows and packaged and kind != 'package' and (
                    'FROM RDB$PROCEDURES WHERE ' in source or
                    'FROM RDB$FUNCTIONS WHERE ' in source):
                rows.append((rows[0][0], 'OTHER', rows[0][2],
                             'Do not leak', *rows[0][4:]))
        if kind == 'procedure' and 'RDB$PROCEDURE_PARAMETERS P ' in source:
            rows = [('P', package, parameter_name, 0, 0, 'RDB$1', None, None,
                     comment, 0, 8, 0, 4, 0, 9, None, None, None, None,
                     None, None)]
        if kind == 'function' and 'RDB$FUNCTION_ARGUMENTS A ' in source:
            assert 'A.RDB$DESCRIPTION' in source
            rows = [('P', package, name, position, 'RDB$1', None, None, 0, 0,
                     8, 0, 4, 0, 9, None, None, None, None, None, None,
                     description) for name, position, description in [
                         (None, 0, None), (parameter_name, 1, comment)]]

    cursor.execute.side_effect = execute
    cursor.fetchall.side_effect = lambda: rows
    handle = SimpleNamespace(cursor=lambda: cursor,
                             info=SimpleNamespace(sql_dialect=dialect))
    resources = _resources(handle, {'route': {'database': 'fixture'}})
    native = next(item['native'] for item in resources if
                  item['resource_kind'] == kind)
    named = next(p for p in native['parameters'] if p['name'])
    assert named['description'] == comment
    if packaged:
        assert native['member_visibility'] == {
            None: 'unknown', 0: 'public', 1: 'private'}[private_flag]
        assert 'recreation_statements' not in native
        native = next(item['native'] for item in resources if
                      item['resource_kind'] == 'package')
    statements = native['recreation_statements']
    assert len(statements) == 1 + int(packaged) + (comment is not None)
    assert 'Do not leak' not in native['ddl']
    prefix = ('PKG.' if dialect == 1 else '"Pk""G".') if packaged else ''
    if packaged:
        target = prefix + ('P' if dialect == 1 else '"P"')
        assert statements[1] == (
            f'COMMENT ON {kind.upper()} {target} IS ' +
            "'" + COMMENT.replace("'", "''") + "'")
    if comment is not None:
        target = prefix + ('P.X' if dialect == 1 else '"P"."X""Y"')
        assert statements[-1] == (
            f'COMMENT ON {kind.upper()} PARAMETER {target} IS ' +
            "'" + comment.replace("'", "''") + "'")


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
