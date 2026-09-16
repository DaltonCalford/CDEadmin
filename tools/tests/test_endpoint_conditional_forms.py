"""Provider connection forms share conditional, typed and grouped rules."""

import copy

import pytest

import config  # noqa: F401
from pgadmin.cdeadmin.endpoints import EndpointService
from pgadmin.cdeadmin.endpoints.profiles import EndpointRegistrationError
from pgadmin.cdeadmin.endpoints.service import _form_field_is_visible


FORM = {'fields': [
    {'field_id': 'enabled', 'label': 'Enabled', 'control': 'boolean',
     'default': False},
    {'field_id': 'name', 'label': 'Name', 'control': 'text', 'required': True,
     'visible_when': {'field_id': 'enabled', 'equals': True}},
    {'field_id': 'count', 'label': 'Count', 'control': 'number',
     'required': True, 'integer': True, 'default': 128,
     'minimum': 25, 'maximum': 512,
     'visible_when': {'field_id': 'enabled', 'in': [True]}},
]}


def validate(scope, data):
    profile = {'form_contract': {scope: {'forms': {'edit': FORM}}}}
    method = (EndpointService._server_form_values if scope == 'server' else
              EndpointService._database_form_values)
    return method(profile, 'edit', data)


@pytest.mark.parametrize('scope', ['server', 'database'])
def test_inactive_required_fields_are_not_defaulted_or_required(scope):
    assert validate(scope, {}) == {'enabled': False}
    assert validate(scope, {'enabled': False}) == {'enabled': False}
    assert validate(scope, {'enabled': True, 'name': 'owned'}) == {
        'enabled': True, 'name': 'owned', 'count': 128}
    with pytest.raises(EndpointRegistrationError, match='must not be empty'):
        validate(scope, {'enabled': True})


@pytest.mark.parametrize('scope', ['server', 'database'])
@pytest.mark.parametrize('count', [24, 513, 25.5, True, False, float('nan'),
                                   float('inf'), '', None, '128'])
def test_active_integer_fields_are_checked_before_persistence(scope, count):
    with pytest.raises(EndpointRegistrationError):
        validate(scope, {'enabled': True, 'name': 'owned', 'count': count})


@pytest.mark.parametrize('scope', ['server', 'database'])
def test_whole_number_json_values_are_normalized(scope):
    result = validate(
        scope, {'enabled': True, 'name': 'owned', 'count': 128.0})
    assert type(result['count']) is int
    assert result['count'] == 128


@pytest.mark.parametrize('scope', ['server', 'database'])
def test_hidden_submitted_values_are_not_silently_activated(scope):
    with pytest.raises(EndpointRegistrationError, match='unavailable'):
        validate(scope, {'enabled': False, 'count': 256})


@pytest.mark.parametrize('condition', [False, {}, {'all': []}, {'all': False},
                                       {'field_id': 'missing', 'equals': True},
                                       {'field_id': 'enabled', 'bad': True}])
def test_malformed_provider_visibility_conditions_fail_closed(condition):
    with pytest.raises(EndpointRegistrationError):
        _form_field_is_visible({'visible_when': condition}, FORM, {})


def test_compound_visibility_matches_typed_frontend_rules():
    field = {'visible_when': {'all': [
        {'field_id': 'enabled', 'equals': True},
        {'field_id': 'name', 'in': ['owned']},
    ]}}
    assert _form_field_is_visible(field, FORM,
                                  {'enabled': True, 'name': 'owned'})
    assert not _form_field_is_visible(field, FORM,
                                      {'enabled': 1, 'name': 'owned'})
    assert not _form_field_is_visible(field, FORM,
                                      {'enabled': True, 'name': 'other'})


@pytest.mark.parametrize('choice', [None, 'PARENT', 'CUSTOM'])
def test_inherited_groups_are_provider_declared_and_do_not_mutate_saved_data(
        choice):
    fields = [{'field_id': 'policy', 'inherit_server_value': 'PARENT',
               'default': 'PARENT', 'inherit_server_fields': ['one', 'two']},
              {'field_id': 'one'}, {'field_id': 'two'}]
    profile = {'form_contract': {'database': {'forms': {
        'connect': {'fields': fields}}}}}
    saved = {'one': 1, 'two': 2, 'other': 3}
    if choice is not None:
        saved['policy'] = choice
    before = copy.deepcopy(saved)
    result = EndpointService._database_route_options(profile, saved)
    assert result == (saved if choice == 'CUSTOM' else {'other': 3})
    assert saved == before


@pytest.mark.parametrize('group', ['count', [None], ['unknown']])
def test_invalid_inheritance_declarations_are_rejected(group):
    fields = [{'field_id': 'policy', 'default': 'PARENT',
               'inherit_server_value': 'PARENT',
               'inherit_server_fields': group}]
    profile = {'form_contract': {'database': {'forms': {
        'connect': {'fields': fields}}}}}
    with pytest.raises(EndpointRegistrationError, match='inheritance'):
        EndpointService._database_route_options(profile, {})
