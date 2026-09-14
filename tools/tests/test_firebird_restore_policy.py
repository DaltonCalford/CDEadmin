"""Native file mode and replication identity are independent choices."""
import copy
from unittest.mock import Mock

import pytest

from tools.cdeadmin_firebird_admin_mapping_gate import RelationalClientError
from pgadmin.cdeadmin.providers.firebird.restore_policy import (
    physical_restore_policy)
from pgadmin.cdeadmin.providers.firebird.provider import (
    ADMINISTRATION, _firebird_service_operation)
from tools.tests.test_firebird_backup_history import fixture


@pytest.mark.parametrize('operation,flags', [
    ('restore_physical', []), ('restore_physical', ['IN_PLACE']),
    ('restore_physical', ['SEQUENCE']),
    ('restore_physical', ['SEQUENCE', 'IN_PLACE']),
    ('fixup_database', []), ('fixup_database', ['SEQUENCE']),
])
def test_policy_survives_exact_plan_and_dispatch_without_mutating_input(
        operation, flags):
    field = ('restore_flags' if operation == 'restore_physical'
             else 'fixup_flags')
    draft = {field: flags}
    if operation == 'restore_physical':
        draft.update(backup_files=['/owned/full.nbk'],
                     restore_database='/owned/destination.fdb')
    original = copy.deepcopy(draft)
    request = {'engine_id': 'firebird', 'resource_kind': 'database',
               'operation_id': operation, 'draft': draft,
               '_provider_route': {'database': '/owned/source.fdb'}}
    expected = physical_restore_policy(operation, draft)
    assert not ADMINISTRATION.validate(request)['errors']
    plan = ADMINISTRATION.plan(request)
    assert plan['command_preview']['restore_policy_requested'] == expected
    plan['command_preview']['restore_policy_requested']['mode'] = 'tampered'
    assert plan['provider_payload']['compiled'][
        'restore_policy_requested'] == expected
    module, server, builder = fixture()
    server.database._srv.return_value = server
    observed = _firebird_service_operation(
        server, operation, '/owned/source.fdb', draft, module)
    assert observed['restore_policy_requested'] == expected
    assert observed['server_completed'] is True
    expected_flags = module.SrvNBackupFlag(0)
    for flag in flags:
        expected_flags |= module.SrvNBackupFlag[flag]
    if operation == 'restore_physical':
        assert server.database.nrestore.call_args.kwargs['flags'] == (
            expected_flags)
    else:
        builder.insert_int.assert_any_call(module.core.SPBItem.OPTIONS,
                                           expected_flags)
    assert draft == original


@pytest.mark.parametrize('operation', ['restore_physical', 'fixup_database'])
def test_invalid_policy_never_reaches_the_native_service(operation):
    field = ('restore_flags' if operation == 'restore_physical'
             else 'fixup_flags')
    server = Mock()
    with pytest.raises(RelationalClientError):
        _firebird_service_operation(server, operation, '/owned/x.fdb',
                                    {field: ['UNKNOWN']}, Mock())
    assert not server.mock_calls


@pytest.mark.parametrize('operation,flags,mode,preserve,access', [
    ('restore_physical', [], 'NEW_DATABASE', False, 'FROM_BACKUP'),
    ('restore_physical', ['SEQUENCE'], 'NEW_DATABASE', True, 'FROM_BACKUP'),
    ('restore_physical', ['IN_PLACE'], 'IN_PLACE', False, 'READ_ONLY'),
    ('restore_physical', ['IN_PLACE', 'SEQUENCE'],
     'IN_PLACE', True, 'READ_ONLY'),
    ('restore_physical', ['SEQUENCE', 'IN_PLACE'],
     'IN_PLACE', True, 'READ_ONLY'),
    ('fixup_database', [], 'FIXUP', False, 'UNCHANGED'),
    ('fixup_database', ['SEQUENCE'], 'FIXUP', True, 'UNCHANGED'),
])
def test_exact_native_policy_without_claiming_observed_state(
        operation, flags, mode, preserve, access):
    field = ('restore_flags' if operation == 'restore_physical'
             else 'fixup_flags')
    draft = {field: flags}
    original = copy.deepcopy(draft)
    assert physical_restore_policy(operation, draft) == {
        'mode': mode,
        'replication_identity': 'PRESERVE' if preserve else 'RESET',
        'offline_required': mode != 'NEW_DATABASE', 'result_access': access}
    assert draft == original


@pytest.mark.parametrize('operation', ['restore_physical', 'fixup_database'])
@pytest.mark.parametrize('flags', [None, []])
def test_native_absent_flag_defaults_are_preserved(operation, flags):
    field = ('restore_flags' if operation == 'restore_physical'
             else 'fixup_flags')
    assert physical_restore_policy(operation, {field: flags}) == (
        physical_restore_policy(operation, {}))


@pytest.mark.parametrize('operation', ['restore_physical', 'fixup_database'])
@pytest.mark.parametrize('flags', [
    False, True, 0, 'SEQUENCE', (), {}, ['UNKNOWN'], [None], [0], [[]]])
def test_malformed_flags_are_rejected(operation, flags):
    field = ('restore_flags' if operation == 'restore_physical'
             else 'fixup_flags')
    with pytest.raises(RelationalClientError):
        physical_restore_policy(operation, {field: flags})


def test_fixup_does_not_invent_in_place_restore_support():
    with pytest.raises(RelationalClientError):
        physical_restore_policy('fixup_database', {
            'fixup_flags': ['IN_PLACE']})


@pytest.mark.parametrize('options', [None, [], '', False])
def test_invalid_options_are_rejected(options):
    with pytest.raises(RelationalClientError):
        physical_restore_policy('restore_physical', options)


def test_unrelated_operation_is_not_relabelled_as_restore():
    with pytest.raises(RelationalClientError):
        physical_restore_policy('backup_physical', {})
