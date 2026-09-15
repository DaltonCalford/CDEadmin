"""Recovery target validation and exact gstat header classification."""

import pytest
from types import SimpleNamespace

from tools.cdeadmin_firebird_admin_mapping_gate import ADMINISTRATION
from pgadmin.cdeadmin.providers.firebird import shadow_activation as activation
from pgadmin.cdeadmin.sdk.relational import RelationalClientError
from pgadmin.cdeadmin.providers.firebird.provider import (
    _firebird_service_operation,
)
from pgadmin.cdeadmin.visual_admin.catalog import catalog_for_engine


def draft(**changes):
    return {'shadow_filename': "/owned/影's.shd",
            'confirmation': "/owned/影's.shd", 'original_isolated': True,
            **changes}


def header(attributes='force write, active shadow', sequence='0'):
    return ['Database header page information:',
            '\tSequence number\t\t' + sequence,
            '\tAttributes\t\t' + attributes,
            '    Variable header data:', '\t*END*']


def test_recovery_target_preserves_the_exact_first_shadow_filename():
    assert activation.validate(draft(), '/owned/primary.fdb') == {
        'shadow_filename': "/owned/影's.shd", 'role': None}


@pytest.mark.parametrize('changes', [
    {'original_isolated': False}, {'original_isolated': 1},
    {'original_isolated': 'yes'}, {'confirmation': '/owned/other'},
    {'confirmation': None}, {'shadow_filename': ''},
    {'shadow_filename': '/owned/line\nfile'},
    {'shadow_filename': '/owned/trailing '}, {'filename': '/owned/unknown'},
    {'role': 'x' * 64},
])
def test_invalid_activation_requests_fail_before_server_access(changes):
    with pytest.raises(RelationalClientError):
        activation.validate(draft(**changes))


def test_the_selected_primary_is_not_substituted_for_a_shadow_target():
    with pytest.raises(RelationalClientError, match='original database'):
        activation.validate(draft(), "/owned/影's.shd")


def test_native_primary_header_must_explicitly_identify_an_active_shadow():
    assert activation.verify_header(header()) == {
        'first_file_verified': True, 'active_shadow_verified': True}


@pytest.mark.parametrize('lines', [
    [], ['active shadow'], header(attributes='force write'),
    header(attributes='not active shadow'), header(sequence='1'),
    header() + header(), header()[:3],
    ['Database header page information:', 'Sequence number 0',
     'Variable header data:', 'Attributes active shadow'],
    header(attributes='force write') + ['Attributes active shadow'],
])
def test_nonshadow_ambiguous_partial_and_injected_headers_are_rejected(lines):
    with pytest.raises(RelationalClientError):
        activation.verify_header(lines)


def test_truncated_native_output_is_never_a_recovery_authorization():
    with pytest.raises(RelationalClientError, match='unavailable'):
        activation.verify_header(header(), truncated=True)


def test_activation_form_has_only_explicit_recovery_and_role_controls():
    form = activation.form(ADMINISTRATION._field)
    fields = {item['field_id']: item for item in form['fields']}
    assert set(fields) == {'shadow_filename', 'confirmation',
                           'original_isolated', 'role'}
    assert fields['original_isolated']['default'] is False
    assert fields['original_isolated']['required'] is True
    assert activation.WARNING in fields['shadow_filename']['help']


@pytest.mark.parametrize('route', [
    {'host': 'localhost'}, {'database': '/owned/primary.fdb'}])
def test_provider_plan_is_bound_to_the_explicit_shadow_not_route_database(
        route):
    request = {'resource_kind': 'database', 'operation_id': 'activate_shadow',
               '_provider_route': route, 'draft': draft()}
    assert not ADMINISTRATION.validate(request)['errors']
    plan = ADMINISTRATION.plan(request)
    assert plan['command_preview']['driver_operation'] == 'firebird-service'
    compiled = ADMINISTRATION._compile(request)
    assert compiled['database'] == "/owned/影's.shd"
    assert compiled['options'] == draft()
    assert activation.WARNING in compiled['warnings']


def test_provider_catalog_retains_a_server_scoped_explicit_recovery_form():
    catalog = ADMINISTRATION.catalog(catalog_for_engine('firebird'))
    database = next(item for item in catalog['objects']
                    if item['resource_kind'] == 'database')
    operation = next(item for item in database['operations']
                     if item['operation_id'] == 'activate_shadow')
    assert operation['target_required'] is False
    assert operation['workspace_scope'] == 'server_service'
    assert operation['form'] == activation.form(ADMINISTRATION._field)


@pytest.mark.parametrize('lines,allowed', [
    (header(), True), (header(attributes='force write'), False),
    (header(sequence='1'), False), (header() + ['padding'] * 2000, False),
])
def test_service_dispatch_checks_the_native_header_before_any_activation(
        lines, allowed):
    calls = []

    def statistics(**values):
        calls.append(('header', values['database'], values.get('role')))
        assert values['flags'] == 4
        for line in lines:
            values['callback'](line)

    def activate(**values):
        calls.append(('activate', values['database'], values['role']))

    server = SimpleNamespace(database=SimpleNamespace(
        get_statistics=statistics, activate_shadow=activate))
    module = SimpleNamespace(SrvStatFlag=SimpleNamespace(HDR_PAGES=4))
    if allowed:
        result = _firebird_service_operation(
            server, 'activate_shadow', "/owned/影's.shd",
            draft(role='RECOVERY_OPERATOR'), module)
        assert result['server_completed'] is True
        assert calls == [('header', "/owned/影's.shd", None),
                         ('activate', "/owned/影's.shd", 'RECOVERY_OPERATOR')]
    else:
        with pytest.raises(RelationalClientError):
            _firebird_service_operation(server, 'activate_shadow',
                                        "/owned/影's.shd", draft(), module)
        assert calls == [('header', "/owned/影's.shd", None)]
