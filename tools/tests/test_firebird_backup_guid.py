"""Prevent Firebird GUID backups from entering native numeric-level parsing."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tools.cdeadmin_firebird_admin_mapping_gate import RelationalClientError
from pgadmin.cdeadmin.providers.firebird.backup_guid import (
    normalize_backup_guid,
)
from pgadmin.cdeadmin.providers.firebird.provider import (
    ADMINISTRATION, _firebird_service_operation,
)


UUID = '00112233-4455-6677-8899-aabbccddeeff'
NATIVE = '{' + UUID.upper() + '}'


@pytest.mark.parametrize('value', [UUID, UUID.upper(), '{' + UUID + '}',
                                   NATIVE, '  ' + UUID + '  '])
def test_guid_is_always_unambiguous_in_plan_and_driver_dispatch(value):
    assert normalize_backup_guid(value) == NATIVE
    draft = {'database_guid': value, 'backup_level': 7,
             'backup_file': '/owned/unused.nbk'}
    request = {'engine_id': 'firebird', 'resource_kind': 'database',
               'operation_id': 'backup_physical', 'draft': draft,
               '_provider_route': {'database': '/owned/x.fdb'}}
    assert not ADMINISTRATION.validate(request)['errors']
    plan = ADMINISTRATION.plan(request)
    compiled = plan['provider_payload']['compiled']
    assert plan['command_preview']['backup_selection'] == {
        'mode': 'guid', 'guid': NATIVE}
    assert compiled['options']['database_guid'] == NATIVE
    assert draft['database_guid'] == value
    server = Mock()
    module = SimpleNamespace(SrvNBackupFlag=Mock(return_value=0))
    observed = _firebird_service_operation(
        server, 'backup_physical', '/owned/x.fdb', draft, module)
    assert observed['backup_selection_requested'] == {
        'mode': 'guid', 'guid': NATIVE}
    assert server.database.nbackup.call_args.kwargs['guid'] == NATIVE
    assert server.database.nbackup.call_args.kwargs['level'] == 7
    assert draft['database_guid'] == value


@pytest.mark.parametrize('value', [None, ''])
def test_empty_guid_is_not_a_guid_request(value):
    assert normalize_backup_guid(value) is None
    plan = ADMINISTRATION.plan({
        'engine_id': 'firebird', 'resource_kind': 'database',
        'operation_id': 'backup_physical',
        'draft': {'database_guid': value, 'backup_level': 2},
        '_provider_route': {'database': '/owned/x.fdb'}})
    options = plan['provider_payload']['compiled']['options']
    assert plan['command_preview']['backup_selection'] == {
        'mode': 'level', 'level': 2}
    assert 'database_guid' not in options
    assert options['backup_level'] == 2


@pytest.mark.parametrize('value', [
    'not-a-guid', '0', '1', ' ', UUID.replace('-', ''),
    '{' + UUID, UUID + '}', '{{' + UUID + '}}', NATIVE + 'extra',
    UUID[:-1] + 'G', 'urn:uuid:' + UUID, [], {}, True, 0, b'guid',
    UUID[:8] + '\x00' + UUID[8:],
])
def test_invalid_guid_never_reaches_a_native_level(value):
    with pytest.raises(RelationalClientError):
        normalize_backup_guid(value)
    errors = ADMINISTRATION.validate({
        'engine_id': 'firebird', 'resource_kind': 'database',
        'operation_id': 'backup_physical', 'draft': {'database_guid': value},
    })['errors']
    assert any(item['code'] == 'invalid_firebird_backup_guid'
               for item in errors)
    server = Mock()
    with pytest.raises(RelationalClientError):
        _firebird_service_operation(server, 'backup_physical', '/owned/x.fdb',
                                    {'database_guid': value}, Mock())
    assert not server.mock_calls
