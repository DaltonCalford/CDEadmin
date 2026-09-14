##########################################################################
# CDEadmin - Multi-engine Database Administration
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
##########################################################################

import itertools
import copy
import json
from unittest.mock import Mock

import pytest

from tools.cdeadmin_firebird_admin_mapping_gate import ADMINISTRATION
from pgadmin.cdeadmin.providers.firebird import mappings
from pgadmin.cdeadmin.sdk.relational import RelationalClientError
from pgadmin.cdeadmin.visual_admin.catalog import catalog_for_engine
from tools.reference_engine_demos.generate_firebird_dialect_contract import (
    WEB, supplement_mappings,
)


def draft(mode='PLUGIN'):
    return {'name': 'example', 'using_mode': mode,
            'plugin': 'Srp256' if mode == 'PLUGIN' else '',
            'source_database': '', 'from_type': 'USER',
            'from_any': False, 'from_name': "O'Connor", 'to_type': 'USER',
            'to_name': ''}


@pytest.mark.parametrize('kind,mode,any_name,to_type', itertools.product(
    mappings.KINDS, mappings.MODES, (True, False), ('USER', 'ROLE')))
def test_full_syntax_matrix(kind, mode, any_name, to_type):
    value = {**draft(mode), 'from_any': any_name, 'to_type': to_type}
    sql = mappings.compile_mapping(kind, 'create', value)[0]
    assert ('GLOBAL MAPPING' in sql) is (kind == mappings.KINDS[1])
    assert sql.endswith('TO ' + to_type)
    assert ('FROM ANY "USER"' in sql) is any_name
    if not any_name:
        assert "'O''Connor'" in sql


@pytest.mark.parametrize('mode', mappings.MODES)
def test_database_selector(mode):
    value = {**draft(mode), 'source_database': '/srv/path with "quote".fdb'}
    if mode == 'SERVERWIDE':
        with pytest.raises(RelationalClientError):
            mappings.compile_mapping(mappings.KINDS[0], 'create', value)
    else:
        assert 'IN "/srv/path with ""quote"".fdb"' in (
            mappings.compile_mapping(mappings.KINDS[0], 'create', value)[0])


@pytest.mark.parametrize('changes', [
    {'name': ''}, {'name': 'x' * 64}, {'name': 'x\0y'},
    {'using_mode': 'INVALID'}, {'using_mode': []}, {'plugin': ''},
    {'using_mode': 'ANY', 'plugin': 'Srp'}, {'from_type': ''},
    {'from_name': ''}, {'from_name': 'x\0y'}, {'from_any': 1},
    {'to_type': 'GROUP'}, {'to_name': 'x\0y'},
])
def test_invalid_choices_do_not_compile(changes):
    with pytest.raises(RelationalClientError):
        mappings.compile_mapping(mappings.KINDS[0], 'create',
                                 {**draft(), **changes})


def test_global_comment_projection_boundary_is_not_replayed_as_complete():
    kind = mappings.KINDS[1]
    target = {'display_name': 'example'}
    assert mappings.compile_mapping(kind, 'comment',
                                    {'description': 'a' * 32766}, target)
    for size in (32767, 40000):
        with pytest.raises(RelationalClientError, match='32767'):
            mappings.compile_mapping(kind, 'comment',
                                     {'description': 'a' * size}, target)
    native = mappings.metadata(kind, (
        'example', 'P', None, None, 'USER', '*', 0, None, 'a' * 32767))
    assert native['description_completeness'] == (
        'unverified-at-native-projection-limit')
    assert 'ddl' not in native
    assert '32767' in native['ddl_unavailable_reason']
    assert mappings.compile_mapping(mappings.KINDS[0], 'comment',
                                    {'description': 'a' * 40000}, target)


def test_drop_requires_exact_confirmation_and_scope():
    for kind in mappings.KINDS:
        with pytest.raises(RelationalClientError):
            mappings.compile_mapping(kind, 'drop', {'confirmation': 'other'},
                                     {'display_name': 'example'})
        sql = mappings.compile_mapping(kind, 'drop',
                                       {'confirmation': 'example'},
                                       {'display_name': 'example'})[0]
        assert sql.endswith('MAPPING "example"')
        assert ('GLOBAL' in sql) is (kind == mappings.KINDS[1])


def test_metadata_retains_scope_definition_comment_and_editor_values():
    for kind in mappings.KINDS:
        native = mappings.metadata(kind, (
            'example', 'P', 'Srp256', 'security.db', 'USER', "O'Connor",
            1, 'Read only', '  comment\nwith whitespace  '))
        assert native['mapping_draft']['to_type'] == 'ROLE'
        assert native['mapping_draft']['from_name'] == "O'Connor"
        assert "IS '  comment\nwith whitespace  ';" in native['ddl']
        assert native['authentication_verified'] is False
        assert 'GRANT' not in native['ddl']


def test_unknown_native_mode_is_not_recreated_as_another_mode():
    native = mappings.metadata(mappings.KINDS[0], (
        'example', '?', None, None, 'USER', '*', 0, None, None))
    assert 'ddl' not in native
    assert 'ddl_unavailable_reason' in native


def test_global_comment_unicode_is_not_silently_corrupted():
    target = {'display_name': 'example'}
    with pytest.raises(RelationalClientError, match='non-ASCII'):
        mappings.compile_mapping(mappings.KINDS[1], 'comment',
                                 {'description': 'é'}, target)
    assert "IS 'é'" in mappings.compile_mapping(
        mappings.KINDS[0], 'comment', {'description': 'é'}, target)[0]


def test_global_catalog_uses_native_security_virtual_relation_and_blob():
    cursor = Mock()
    blob = Mock()
    blob.read.return_value = '  meaningful whitespace  '
    cursor.fetchall.return_value = [(
        'example  ', 'P', 'Srp  ', None, 'USER ', '*', 0, None, blob)]
    rows = list(mappings.catalog_rows(cursor, True))
    assert 'SEC$GLOBAL_AUTH_MAPPING' in cursor.execute.call_args.args[0]
    assert 'SEC$DESCRIPTION' in cursor.execute.call_args.args[0]
    assert rows[0][-1] == '  meaningful whitespace  '
    blob.close.assert_called_once()


def test_catalog_is_idempotent_and_forms_are_native_and_prefilled():
    catalog = ADMINISTRATION.catalog(catalog_for_engine('firebird'))
    assert ADMINISTRATION.catalog(catalog) == catalog
    for kind in mappings.KINDS:
        descriptor = next(item for item in catalog['objects']
                          if item['resource_kind'] == kind)
        assert {op['operation_id'] for op in descriptor['operations']} == (
            mappings.OPERATIONS)
        alter = next(op for op in descriptor['operations']
                     if op['operation_id'] == 'alter')
        assert alter['title'] == ('Alter global mapping' if
                                  kind == mappings.KINDS[1] else
                                  'Alter local mapping')
        assert all(field['initial_value_path'][0] == 'mapping_draft'
                   for field in alter['form']['fields'])
        assert all(field['submit_unchanged'] is True
                   for field in alter['form']['fields'])
        assert 'privileges' not in descriptor['editor']['sections']


def test_administration_plan_preserves_structured_fields():
    for kind in mappings.KINDS:
        for operation in ('create', 'alter', 'create_or_alter', 'comment'):
            request = {'_provider_route': {'database': 'test.fdb'},
                       'resource_kind': kind, 'operation_id': operation,
                       'target_resource': {'display_name': 'example'},
                       'draft': draft() if operation != 'comment' else {
                           'description': 'comment'}}
            assert ADMINISTRATION.validate(request)['errors'] == []
            assert ADMINISTRATION.plan(request)['command_preview'][
                'statements'][0]['source']


def mapping_evidence():
    checks = [f'{kind}:{mode}:any={any_name}:to={to_type}'
              for kind, mode, any_name, to_type in itertools.product(
                  mappings.KINDS, mappings.MODES, (False, True),
                  ('USER', 'ROLE'))]
    checks += ['scope-separation']
    checks += [f'{kind}:{case}' for kind in mappings.KINDS for case in (
        'permission-denial', 'native-user-mapping-authentication')]
    tasks = {}
    for kind in mappings.KINDS:
        for operation in mappings.OPERATIONS - {'inspect'}:
            tasks[f'visual_admin.{kind}.{operation}'] = {
                'live_execution': 'passed',
                'statements': mappings.compile_mapping(kind, operation, {
                    **draft(), 'description': 'Comment',
                    'confirmation': 'example'}, {'display_name': 'example'}),
            }
    return {'status': 'passed', 'engine_version': '5.0.4',
            'container_removed': True, 'failures': [],
            'checks': checks, 'task_evidence': tasks}


def mapping_contract():
    return json.loads((WEB / 'pgadmin/cdeadmin/providers/firebird/'
                       'firebird_dialect_5_0_4.json').read_text())


def test_mapping_supplement_preserves_prior_proof_and_is_idempotent():
    document = mapping_contract()
    before = copy.deepcopy(document)
    result = supplement_mappings(document, mapping_evidence(), 'a' * 64,
                                 'mapping.json')
    assert document == before
    assert len(result['task_templates']) == 64
    assert supplement_mappings(result, mapping_evidence(), 'a' * 64,
                               'mapping.json') == result
    assert all(item in result['proof_records'] for item in
               document['proof_records'] if 'authentication-mappings' not in
               item['evidence_id'])


@pytest.mark.parametrize('key,value', [
    ('status', 'failed'), ('engine_version', '5.0.5'),
    ('container_removed', False), ('checks', []),
    ('failures', [{'case': 'failed'}]), ('task_evidence', {}),
])
def test_mapping_evidence_must_be_complete_and_clean(key, value):
    proof = mapping_evidence()
    proof[key] = value
    with pytest.raises(ValueError):
        supplement_mappings(mapping_contract(), proof,
                            'a' * 64, 'mapping.json')


def test_mapping_failed_statement_is_not_a_task_pass():
    proof = mapping_evidence()
    next(iter(proof['task_evidence'].values()))['live_execution'] = 'failed'
    with pytest.raises(ValueError):
        supplement_mappings(mapping_contract(), proof,
                            'a' * 64, 'mapping.json')
