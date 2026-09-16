"""User replacement keeps plugin identity and redacts credential previews."""
import json

import pytest

from tools.cdeadmin_firebird_admin_mapping_gate import ADMINISTRATION
from pgadmin.cdeadmin.providers.firebird import users
from pgadmin.cdeadmin.sdk.relational import RelationalClientError
from pgadmin.cdeadmin.visual_admin.catalog import catalog_for_engine

TARGET = {'resource_kind': 'user', 'display_name': 'U"TEST',
          'native': {'plugin': 'Srp'}}


@pytest.mark.parametrize('op', sorted(users.OPERATIONS))
def test_native_fields_and_secret_safe_preview(op):
    draft = {'name' if op == 'create_or_alter' else 'confirmation': 'U"TEST',
             'password': "private'password", 'plugin': 'Srp',
             'first_name': 'PrivateFirst', 'middle_name': 'PrivateMiddle',
             'last_name': 'PrivateLast', 'admin_role': 'REVOKE',
             'active_state': 'INACTIVE', 'tags': [
                 {'name': 'department', 'value': 'private-value'},
                 {'name': 'old', 'drop': True}]}
    request = {'resource_kind': 'user', 'operation_id': op, 'draft': draft,
               'target_resource': TARGET,
               '_provider_route': {'database': 'owned'}}
    assert ADMINISTRATION.validate(request) == {'errors': []}
    plan = ADMINISTRATION.plan(request)
    preview = json.dumps(plan['command_preview'])
    for secret in ("private'password", 'PrivateFirst', 'PrivateMiddle',
                   'PrivateLast', 'private-value'):
        assert secret not in preview
    statement = plan['provider_payload']['compiled']['statements'][0]
    assert "PASSWORD 'private''password'" in statement['source']
    assert 'USER "U""TEST"' in statement['source']
    assert 'REVOKE ADMIN ROLE INACTIVE' in statement['source']
    assert 'DROP "old"' in statement['source']
    assert '<redacted>' in preview
    catalog = ADMINISTRATION.catalog(catalog_for_engine('firebird'))
    resource = next(r for r in catalog['objects'] if
                    r['resource_kind'] == 'user')
    action = next(o for o in resource['operations'] if o['operation_id'] == op)
    fields = {f['field_id']: f for f in action['form']['fields']}
    assert fields['password']['sensitive'] is True
    assert fields['tags']['sensitive'] is True
    assert 'host' not in fields
    assert action['confirmation_required'] is True
    assert action['target_required'] is (op == 'recreate')
    if op == 'recreate':
        assert action['mutation_class'] == 'destructive'
        assert 'name' not in fields


@pytest.mark.parametrize('change', [
    {'confirmation': 'wrong'}, {'plugin': 'Legacy_UserManager'},
    {'password': None}, {'password': ''}, {'password': '\x00'},
    {'password': '\ud800'}, {'password': 1}, {'name': 'redirect'},
    {'host': '%'}, {'admin_role': True}, {'active_state': []},
    {'tags': {}}, {'tags': [1]}, {'tags': [{'name': 'a', 'drop': 'yes'}]},
    {'tags': [{'name': 'a', 'value': 'b', 'drop': True}]},
    {'tags': [{'name': 'a=b', 'value': 'c'}]},
    {'tags': [{'name': 'a', 'value': 'b\nc'}]},
])
def test_invalid_recreation(change):
    draft = {'confirmation': TARGET['display_name'], 'password': 'fixture',
             **change}
    with pytest.raises(RelationalClientError):
        users.compile_operation('recreate', draft, TARGET)


def test_inherit_plugin_and_omit_password_on_existing_account_update():
    statement = users.compile_operation('recreate', {
        'confirmation': TARGET['display_name'], 'password': 'fixture'}, TARGET)
    assert statement['source'].endswith('USING PLUGIN "Srp"')
    statement = users.compile_operation('create_or_alter', {
        'name': 'U', 'first_name': ''})
    assert 'PASSWORD' not in statement['source']
    assert statement['source'].endswith("FIRSTNAME ''")


@pytest.mark.parametrize('target', [
    None, {}, {'resource_kind': 'role'},
    {'resource_kind': 'user', 'display_name': 'U'}])
def test_missing_target_or_plugin(target):
    with pytest.raises(RelationalClientError):
        users.compile_operation('recreate', {
            'confirmation': 'U', 'password': 'fixture'}, target)


def test_noop_and_unknown_operation():
    with pytest.raises(RelationalClientError):
        users.compile_operation('create_or_alter', {
            'name': 'U', 'plugin': 'Srp'})
    with pytest.raises(RelationalClientError):
        users.compile_operation('drop', {})
