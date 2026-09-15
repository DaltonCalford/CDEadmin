"""Exact native limbo protocol and recovery-input boundaries."""

import sys
import threading
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

import pytest

WEB = Path(__file__).resolve().parents[2] / 'web'
sys.path.insert(0, str(WEB))
if 'pgadmin' not in sys.modules:
    package = ModuleType('pgadmin')
    package.__path__ = [str(WEB / 'pgadmin')]
    sys.modules['pgadmin'] = package

from pgadmin.cdeadmin.providers.firebird import limbo  # noqa: E402
from pgadmin.cdeadmin.sdk.relational import RelationalClientError  # noqa: E402


def integer(tag, value, size=4):
    return bytes([tag]) + value.to_bytes(size, 'little', signed=True)


def string(tag, value):
    encoded = value.encode('utf-8')
    return bytes([tag]) + len(encoded).to_bytes(2, 'little') + encoded


def native_inventory(connection, identifiers):
    def acquire(request, buffer):
        payload = b''.join(
            bytes([16, 8, 0]) + int(value).to_bytes(8, 'little', signed=True)
            for value in identifiers) + b'\x01'
        assert len(payload) <= len(buffer)
        buffer.raw = payload.ljust(len(buffer), b'\x00')
    connection._att.get_info.side_effect = acquire


def test_inventory_grows_at_truncated_item_boundary_without_merging_reads():
    connection = Mock()
    sizes = []

    def acquire(request, buffer):
        assert request == b'\x10'
        sizes.append(len(buffer))
        # A changed inventory on retry must replace, not merge, the first read.
        payload = (b'\x10\x04\x00\x07\x00\x00\x00\x02\x01'
                   if len(sizes) == 1 else
                   b'\x10\x08\x00' + (2**53 + 1).to_bytes(8, 'little') +
                   b'\x01')
        buffer.raw = payload.ljust(len(buffer), b'\x00')

    connection._att.get_info.side_effect = acquire
    module = SimpleNamespace(DbInfoCode=SimpleNamespace(LIMBO=16))
    assert limbo.inventory(connection, module) == [str(2**53 + 1)]
    assert sizes == [256, 512]


@pytest.mark.parametrize('payload', [
    b'\x00', b'\x03', b'\x10\xff\xff', b'\x10\x03\x00abc\x01',
    b'\x10\x04\x00\x00\x00\x00\x00\x01',
    b'\x10\x04\x00\xff\xff\xff\xff\x01',
    b'\x10\x04\x00\x01\x00\x00\x00' * 2 + b'\x01',
])
def test_inventory_rejects_malformed_items_without_retry(payload):
    connection = Mock()

    def acquire(request, buffer):
        buffer.raw = payload.ljust(len(buffer), b'\x00')

    connection._att.get_info.side_effect = acquire
    module = SimpleNamespace(DbInfoCode=SimpleNamespace(LIMBO=16))
    with pytest.raises(ValueError):
        limbo.inventory(connection, module)
    connection._att.get_info.assert_called_once()


def test_inventory_truncation_has_a_hard_bound_and_never_returns_partial():
    connection = Mock()
    sizes = []

    def acquire(request, buffer):
        sizes.append(len(buffer))
        buffer.raw = b'\x02'.ljust(len(buffer), b'\x00')

    connection._att.get_info.side_effect = acquire
    module = SimpleNamespace(DbInfoCode=SimpleNamespace(LIMBO=16))
    with pytest.raises(ValueError, match='safe bound'):
        limbo.inventory(connection, module)
    assert sizes == [256, 512, 1024, 2048, 4096, 8192, 16384, 32767]


@pytest.mark.parametrize('operation', sorted(limbo.ATTACHMENT_OPERATIONS))
def test_attachment_form_plan_and_catalog_are_native_and_guarded(operation):
    from tools.cdeadmin_firebird_admin_mapping_gate import ADMINISTRATION
    from pgadmin.cdeadmin.visual_admin.catalog import catalog_for_engine
    draft = {'role': 'Recovery Role'}
    if operation != 'inspect_limbo':
        draft.update(transaction_id=str(2**53 + 1),
                     confirmation=str(2**53 + 1),
                     database_confirmation='/owned/東京.fdb',
                     coordinator_reviewed=True)
    request = {'resource_kind': 'database', 'operation_id': operation,
               '_provider_route': {'database': '/owned/東京.fdb'},
               'draft': draft}
    assert not ADMINISTRATION.validate(request)['errors']
    assert not ADMINISTRATION.requires_dialect('database', operation)
    plan = ADMINISTRATION.plan(request)
    assert plan['command_preview']['driver_operation'] == 'firebird-limbo'
    assert plan['command_preview']['statements'] == []
    selection = plan['command_preview']['recovery_selection']
    assert selection['database'] == '/owned/東京.fdb'
    assert selection['scope'] == 'selected_database_only'
    assert selection['global_outcome_inferred'] is False
    assert selection['transaction_id'] == (
        None if operation == 'inspect_limbo' else str(2**53 + 1))
    compiled = ADMINISTRATION._compile(request)
    assert compiled['options'] == draft
    catalog = ADMINISTRATION.catalog(catalog_for_engine('firebird'))
    database = next(item for item in catalog['objects']
                    if item['resource_kind'] == 'database')
    task = next(item for item in database['operations']
                if item['operation_id'] == operation)
    assert task['required_permissions'] == ['maintenance_admin']
    assert task['target_required'] is True
    assert task['confirmation_required'] is (operation != 'inspect_limbo')
    assert task['form'] == limbo.form(operation, ADMINISTRATION._field)


@pytest.mark.parametrize('operation', sorted(limbo.ATTACHMENT_OPERATIONS))
def test_attachment_scope_never_borrows_session_or_mutates_saved_role(
        operation):
    from pgadmin.cdeadmin.providers.firebird.query_client import (
        FirebirdQueryClient)
    request = {'route': {'database': 'owned.fdb', 'role': 'Saved Role'},
               '_provider_session_handle': object()}
    draft = {'role': 'Task Role'}
    if operation != 'inspect_limbo':
        draft.update(transaction_id='1', confirmation='1',
                     database_confirmation='owned.fdb',
                     coordinator_reviewed=True)
    scoped, values = FirebirdQueryClient._limbo_scope(
        request, operation, draft)
    assert scoped == {'route': {'database': 'owned.fdb', 'role': 'Task Role'}}
    assert request['route']['role'] == 'Saved Role'
    assert values['role'] == 'Task Role'


@pytest.mark.parametrize('operation', sorted(limbo.ATTACHMENT_OPERATIONS))
def test_recovery_requires_maintenance_permission_before_native_planning(
        operation):
    from tools.tests.test_cdeadmin_visual_admin import context, Permissions
    from tools.cdeadmin_firebird_admin_mapping_gate import ADMINISTRATION
    from pgadmin.cdeadmin.visual_admin import (
        ProviderVisualAdministration, VisualAdminAccessError)
    client = Mock()
    client.visual_admin_catalog.side_effect = ADMINISTRATION.catalog
    authority = ProviderVisualAdministration(
        context('firebird'), Permissions({'data_read', 'administer'}),
        'firebird', '5.0.4', client,
        operation_gate=lambda *_args: True)
    with pytest.raises(VisualAdminAccessError):
        authority.plan({'resource_kind': 'database',
                        'operation_id': operation, 'draft': {},
                        'target_resource': {'resource_id': 'owned-target'}})
    client.plan_admin_operation.assert_not_called()
    client.apply_admin_operation.assert_not_called()


@pytest.mark.parametrize('operation', [
    'commit_limbo', 'rollback_limbo', 'recover_limbo', 'drop', None, [], {}])
def test_attachment_dispatch_never_maps_an_unknown_operation_to_rollback(
        operation):
    from tools.tests.test_firebird_query_limits import client_fixture
    client, _handle, _cursor = client_fixture()
    client._connect = Mock()
    with pytest.raises(RelationalClientError, match='Unknown Firebird'):
        client.run_limbo_operation(
            {'route': {'database': 'owned.fdb'}}, operation, {})
    client._connect.assert_not_called()
    assert not client._recoveries


@pytest.mark.parametrize('value', [
    None, True, False, 0, -1, 1.5, '1.0', '1e3', '01', '+1', ' 1', '1 ',
    '١', '1\n', str(2**63), {}, [],
])
def test_invalid_identifiers(value):
    with pytest.raises(ValueError):
        limbo.transaction_id(value)


@pytest.mark.parametrize('value', [1, 2**31, 2**53 + 1, 2**63 - 1])
def test_identifiers_preserve_exact_decimal(value):
    assert limbo.transaction_id(value) == str(value)
    assert limbo.transaction_id(str(value)) == str(value)


def test_decode_single_and_distributed_native_report():
    report = (integer(19, 7) + integer(47, 2**53 + 1, 8) +
              integer(48, 2**32, 8) + string(26, 'host') + integer(18, 15) +
              bytes([21, 22]) + string(27, 'remote') +
              string(28, "/owned/影's.fdb") + string(26, 'host') +
              integer(46, 2**33, 8) + bytes([21, 23]) +
              string(28, '/owned/peer.fdb') + bytes([29, 30]))
    parsed = limbo.parse_report(report)
    assert parsed[0] == {
        'transaction_id': '7', 'kind': 'single', 'participants': []}
    assert parsed[1]['transaction_id'] == str(2**53 + 1)
    assert parsed[2] == {
        'transaction_id': str(2**32), 'kind': 'distributed',
        'advice': 'commit',
        'participants': [
            {'host': 'host', 'transaction_id': '15', 'state': 'limbo',
             'remote_host': 'remote', 'database': "/owned/影's.fdb"},
            {'host': 'host', 'transaction_id': str(2**33),
             'state': 'committed', 'database': '/owned/peer.fdb'},
        ],
    }


@pytest.mark.parametrize('state, label', list(limbo._STATES.items()))
@pytest.mark.parametrize('advice, action', list(limbo._ADVICE.items()))
def test_all_native_state_and_advice_values(state, label, advice, action):
    report = integer(20, 5) + integer(18, 6) + bytes([21, state, 29, advice])
    parsed = limbo.parse_report(report)[0]
    assert parsed['participants'][0]['state'] == label
    assert parsed['advice'] == action


@pytest.mark.parametrize('data', [
    b'\x13', integer(19, 0), integer(19, -1), b'\xff', b'\x1a\xff\xff',
    integer(20, 1), integer(20, 1) + bytes([29, 255]),
    integer(20, 1) + bytes([21, 255]),
    integer(19, 1) + integer(19, 1), integer(19, 1) + string(26, 'host'),
    integer(20, 1) + bytes([26, 1, 0, 255]),
    integer(20, 1) + string(26, 'a\x00b') + bytes([29, 33]),
    integer(20, 1) + bytes([29, 33]) + integer(18, 4),
])
def test_malformed_reports_fail_closed(data):
    with pytest.raises((ValueError, UnicodeError)):
        limbo.parse_report(data)


def test_empty_native_report_is_empty_without_invented_transactions():
    assert limbo.parse_report(b'') == []


@pytest.mark.parametrize('field, value', [
    ('transaction_id', '01'), ('confirmation', '2'),
    ('database_confirmation', 'other.fdb'), ('coordinator_reviewed', 1),
    ('coordinator_reviewed', False), ('extra', 'value'), ('role', 'bad role'),
])
def test_recovery_requires_exact_identity_and_coordinator_review(field, value):
    draft = {
        'transaction_id': '1',
        'confirmation': '1',
        'database_confirmation': 'owned.fdb',
        'coordinator_reviewed': True}
    with pytest.raises((ValueError, RelationalClientError)):
        limbo.validate('commit_limbo', {**draft, field: value}, 'owned.fdb')


def frame(payload, trailer=1):
    return bytes([66]) + len(payload).to_bytes(2, 'little') + payload + bytes([
        trailer])


def test_service_reader_drains_chunks_after_worker_stops():
    server = SimpleNamespace(response=SimpleNamespace(clear=Mock(), raw=b''),
                             _svc=Mock(), _make_request=Mock(),
                             is_running=Mock(return_value=False))
    frames = iter([frame(b'\x13\x01'), frame(b'\x00\x00\x00'), frame(b'')])
    server._svc.query.side_effect = lambda *_: setattr(
        server.response, 'raw', next(frames))
    assert limbo._read_report(server) == integer(19, 1)
    assert server._svc.query.call_count == 3


def test_service_reader_timeout_never_returns_a_partial_report(monkeypatch):
    server = SimpleNamespace(response=SimpleNamespace(clear=Mock(), raw=b''),
                             _svc=Mock(), _make_request=Mock(),
                             is_running=Mock(return_value=True))
    clock = iter((1000, 1001, 1003))
    monkeypatch.setattr(limbo.time, 'monotonic', lambda: next(clock))
    server._svc.query.side_effect = lambda *_: setattr(
        server.response, 'raw', frame(b'\x13', 4))
    with pytest.raises(TimeoutError, match='unconfirmed'):
        limbo._read_report(server, timeout=2)
    server._svc.query.assert_called_once()


def test_service_reader_rejects_oversized_accumulated_report(monkeypatch):
    server = SimpleNamespace(response=SimpleNamespace(clear=Mock(), raw=b''),
                             _svc=Mock(), _make_request=Mock(),
                             is_running=Mock(return_value=True))
    monkeypatch.setattr(limbo, 'MAX_OUTPUT', 3)
    server._svc.query.side_effect = lambda *_: setattr(
        server.response, 'raw', frame(b'1234'))
    with pytest.raises(ValueError, match='safe bound'):
        limbo._read_report(server)
    server._svc.query.assert_called_once()


@pytest.mark.parametrize('raw', [b'', b'\x01', b'\x42\xff\xff\x01',
                                 frame(b'', 3), frame(b'', 255)])
def test_service_reader_rejects_invalid_frames(raw):
    server = SimpleNamespace(response=SimpleNamespace(clear=Mock(), raw=raw),
                             _svc=Mock(), _make_request=Mock())
    with pytest.raises(ValueError):
        limbo._read_report(server)


def description_field(tag, value):
    return bytes([tag, len(value)]) + value


def description(path=b'host/3050:/owned/a.fdb', identifier=2**53 + 1):
    return (b'\x01' + description_field(1, b'coordinator') +
            description_field(2, path) + description_field(
                3, identifier.to_bytes(8, 'little', signed=True)))


def test_stored_description_is_metadata_not_peer_authorization():
    result = limbo.parse_description(description(
        "untrusted.example:/owned/影's.fdb".encode('utf-8')))
    assert result == {
        'host': 'coordinator', 'native_length_limit_reached': False,
        'participants': [{'database': "untrusted.example:/owned/影's.fdb",
                          'transaction_id': str(2**53 + 1)}]}
    assert 'state' not in result['participants'][0]


def test_native_description_length_boundary_is_not_hidden():
    result = limbo.parse_description(description(b'a' * 255))
    assert result['native_length_limit_reached'] is True
    assert result['participants'][0]['database'] == 'a' * 255


@pytest.mark.parametrize('value', [
    b'', b'\x02', b'\x01', b'\x01\x01', b'\x01\x01\x00',
    b'\x01\x01\xffa', b'\x01' + description_field(9, b'a'),
    description() + b'\x00', description() + description_field(1, b'other'),
    description(b'\xff'), description(b'a\x00b'), description(identifier=-1),
    b'\x01' + description_field(2, b'path') + description_field(2, b'other'),
    b'\x01' + description_field(3, (1).to_bytes(4, 'little')),
    b'\x01' + description_field(1, b'host') + description_field(2, b'path'),
])
def test_invalid_stored_descriptions_fail_closed(value):
    with pytest.raises((ValueError, UnicodeError)):
        limbo.parse_description(value)


def test_attachment_metadata_has_independent_read_only_rollback_transaction():
    connection = Mock()
    native_inventory(connection, [2, 3])
    manager = connection.transaction_manager.return_value
    manager.__enter__ = Mock(return_value=manager)
    manager.__exit__ = Mock()
    cursor = Mock()
    cursor.__enter__ = Mock(return_value=cursor)
    cursor.__exit__ = Mock()
    manager.cursor.return_value = cursor
    cursor.fetchone.side_effect = [None, (1, description())]
    module = SimpleNamespace(
        DbInfoCode=SimpleNamespace(
            LIMBO=16),
        tpb=Mock(
            return_value=b'read-only'),
        Isolation=SimpleNamespace(
            READ_COMMITTED_RECORD_VERSION=4),
        TraAccessMode=SimpleNamespace(
            READ=8),
        DefaultAction=SimpleNamespace(
            ROLLBACK=2))
    records = limbo.inspect_attachment(connection, module)
    assert [item['transaction_id'] for item in records] == ['2', '3']
    assert [item['kind'] for item in records] == ['single', 'distributed']
    assert all(item['peer_states_observed'] is False for item in records)
    module.tpb.assert_called_once_with(isolation=4, access_mode=8)
    connection.transaction_manager.assert_called_once_with(
        default_tpb=b'read-only', default_action=2)
    connection.commit.assert_not_called()
    connection.rollback.assert_not_called()
    connection.main_transaction.assert_not_called()
    manager.__exit__.assert_called_once()


def recovery_rig(failure=None):
    events = []
    created = []

    class Handle:
        def __init__(self, name):
            self.name = name
            self._refcnt = 1
            created.append(self)

        def join(self, other):
            events.append('join')
            if failure == 'join':
                raise RuntimeError('join failed')
            self._refcnt = other._refcnt = 0
            return Handle('joined')

        def decide(self, action):
            events.append(action)
            if failure == 'decision':
                raise RuntimeError('response unavailable')
            self._refcnt = 0

        def commit(self):
            self.decide('commit')

        def rollback(self):
            self.decide('rollback')

        def release(self):
            assert all(connection.is_closed() for connection in connections)
            events.append('release-' + self.name)
            self._refcnt = 0

    connections = []
    identifiers = [3, 2**53 + 1]
    for number, identifier in enumerate(identifiers):
        connection = Mock()
        connection.main_transaction.is_active.return_value = False
        connection._transactions = []
        connection.is_closed.return_value = False
        native_inventory(connection, [identifier])

        def close(connection=connection, number=number):
            events.append('close-' + str(number))
            connection.is_closed.return_value = True

        def reconnect(_value, number=number):
            events.append('reconnect-' + str(number))
            if failure == 'reconnect' and number == 1:
                raise RuntimeError('reconnect failed')
            return Handle(str(number))

        connection.close.side_effect = close
        connection._att.reconnect_transaction.side_effect = reconnect
        connections.append(connection)
    module = SimpleNamespace(DbInfoCode=SimpleNamespace(LIMBO=16))
    return SimpleNamespace(
        connections=connections, participants=list(
            zip(connections, identifiers)),
        events=events, created=created, module=module)


@pytest.mark.parametrize('decision', ['commit', 'rollback'])
def test_native_coordinator_dispatches_once_without_cleanup_decisions(
        decision):
    rig = recovery_rig()
    recovery = limbo.NativeRecovery(rig.participants, decision, rig.module)
    receipt = recovery.run()
    assert receipt['native_decision_returned'] is True
    assert receipt['participant_transaction_ids'] == ['3', str(2**53 + 1)]
    rig.connections[0]._att.reconnect_transaction.assert_called_once_with(
        (3).to_bytes(4, 'little'))
    rig.connections[1]._att.reconnect_transaction.assert_called_once_with(
        (2**53 + 1).to_bytes(8, 'little'))
    with pytest.raises(ValueError, match='cannot be replayed'):
        recovery.run()
    recovery.close()
    recovery.close()
    assert rig.events == ['reconnect-0', 'reconnect-1', 'join', decision,
                          'close-0', 'close-1']
    assert recovery.closed is True


@pytest.mark.parametrize('phase', ['reconnect', 'join', 'decision'])
def test_failed_native_recovery_detaches_before_releasing_prepared_handles(
        phase):
    rig = recovery_rig(phase)
    recovery = limbo.NativeRecovery(rig.participants, 'commit', rig.module)
    with pytest.raises(RuntimeError):
        recovery.run()
    assert recovery.returned is False
    assert recovery.dispatched is (phase == 'decision')
    recovery.close()
    assert recovery.closed is True
    assert 'rollback' not in rig.events
    releases = [index for index, value in enumerate(rig.events)
                if value.startswith('release-')]
    assert releases and min(releases) > rig.events.index('close-1')
    assert rig.events.count('commit') == (1 if phase == 'decision' else 0)


def test_failed_detach_retains_all_native_handles_until_explicit_close_retry():
    rig = recovery_rig('decision')
    recovery = limbo.NativeRecovery(rig.participants, 'commit', rig.module)
    with pytest.raises(RuntimeError):
        recovery.run()
    original_close = rig.connections[0].close.side_effect
    rig.connections[0].close.side_effect = RuntimeError('detach failed')
    with pytest.raises(RuntimeError, match='detach failed'):
        recovery.close()
    assert recovery.closed is False
    assert rig.connections[1].is_closed() is True
    assert not any(value.startswith('release-') for value in rig.events)
    assert recovery._handles
    rig.connections[0].close.side_effect = original_close
    recovery.close()
    assert recovery.closed is True
    assert rig.events.count('commit') == 1
    assert 'rollback' not in rig.events


def test_active_caller_transaction_is_not_rolled_back_by_rejected_recovery():
    rig = recovery_rig()
    rig.connections[1].main_transaction.is_active.return_value = True
    recovery = limbo.NativeRecovery(rig.participants, 'commit', rig.module)
    with pytest.raises(ValueError, match='cannot borrow'):
        recovery.run()
    recovery.close()
    rig.connections[1].close.assert_not_called()
    rig.connections[1].commit.assert_not_called()
    rig.connections[1].rollback.assert_not_called()
    assert not any(value.startswith('reconnect-') for value in rig.events)


def test_all_native_inventory_preconditions_precede_first_reconnect():
    rig = recovery_rig()
    native_inventory(rig.connections[1], [])
    recovery = limbo.NativeRecovery(rig.participants, 'commit', rig.module)
    with pytest.raises(ValueError, match='no longer in limbo'):
        recovery.run()
    assert rig.events == []
    recovery.close()


def test_empty_participant_iterator_is_rejected():
    with pytest.raises(ValueError, match='at least one'):
        limbo.NativeRecovery(iter(()), 'commit', object())


def test_close_without_dispatch_does_not_release_borrowed_active_handle():
    rig = recovery_rig()
    rig.connections[0].main_transaction.is_active.return_value = True
    recovery = limbo.NativeRecovery(rig.participants, 'commit', rig.module)
    recovery.close()
    rig.connections[0].close.assert_not_called()
    rig.connections[0].commit.assert_not_called()
    rig.connections[0].rollback.assert_not_called()
    assert recovery.closed
    with pytest.raises(ValueError, match='cannot be replayed'):
        recovery.run()


def test_native_recovery_rejects_close_and_replay_during_dispatch():
    rig = recovery_rig()
    entered, finish = threading.Event(), threading.Event()
    original = rig.connections[0]._att.reconnect_transaction.side_effect

    def reconnect(value):
        handle = original(value)

        def commit():
            entered.set()
            assert finish.wait(5)
            handle._refcnt = 0

        handle.commit = commit
        return handle

    rig.connections[0]._att.reconnect_transaction.side_effect = reconnect
    recovery = limbo.NativeRecovery(rig.participants[:1], 'commit', rig.module)
    errors = []

    def run():
        try:
            recovery.run()
        except Exception as error:
            errors.append(error)

    worker = threading.Thread(target=run)
    worker.start()
    try:
        assert entered.wait(5)
        for action in (recovery.close, recovery.run):
            with pytest.raises(ValueError, match='busy'):
                action()
        rig.connections[0].close.assert_not_called()
    finally:
        finish.set()
        worker.join(5)
    assert not worker.is_alive() and not errors
    recovery.close()


@pytest.mark.parametrize('decision', ['commit', 'rollback'])
@pytest.mark.parametrize('decision_fails', [False, True])
@pytest.mark.parametrize('detach_fails', [False, True])
def test_provider_retains_recovery_owner_and_retries_only_cleanup(
        decision, decision_fails, detach_fails):
    from tools.tests.test_firebird_query_limits import client_fixture
    client, _unused, _cursor = client_fixture()
    rig = recovery_rig('decision' if decision_fails else None)
    handle = rig.connections[0]
    rig.connections[1].is_closed.return_value = True
    client.module = rig.module
    client._connections[:] = [handle]
    client._connect = Mock(return_value=handle)
    close = handle.close.side_effect
    if detach_fails:
        handle.close.side_effect = RuntimeError('PRIVATE detach details')
    draft = {
        'transaction_id': '3',
        'confirmation': '3',
        'database_confirmation': 'owned.fdb',
        'coordinator_reviewed': True}
    request = {'route': {'database': 'owned.fdb'}}
    if decision_fails:
        with pytest.raises(RelationalClientError) as caught:
            client.run_limbo_operation(
                request, decision + '_limbo_local', draft)
        assert 'PRIVATE' not in str(caught.value)
        assert caught.value.attachment_released is not detach_fails
    else:
        observation = client.run_limbo_operation(
            request, decision + '_limbo_local', draft)
        assert observation['native_decision_returned'] is True
        assert observation['global_outcome_inferred'] is False
        assert observation['attachment_released'] is not detach_fails
    assert len(client._recoveries) == (1 if detach_fails else 0)
    if detach_fails:
        assert handle in client._connections
        assert not any(item.startswith('release-') for item in rig.events)
        from pgadmin.cdeadmin.core.registry import (
            ProviderRegistry, ProviderReleaseError)
        registration = SimpleNamespace(
            bindings={'owned': SimpleNamespace(instance=client)},
            unreleased_instances=[], release_failure_count=0)
        with pytest.raises(ProviderReleaseError):
            ProviderRegistry._close_bindings(registration)
        assert registration.bindings['owned'].instance is client
        assert registration.release_failure_count == 1
        assert not any(item.startswith('release-') for item in rig.events)
        handle.close.side_effect = close
        ProviderRegistry._close_bindings(registration)
        assert registration.bindings == {}
        assert registration.release_failure_count == 0
    else:
        client.close()
    assert not client._recoveries
    assert rig.events.count(decision) == 1
    assert ('rollback' if decision == 'commit' else 'commit') not in rig.events
    assert not client._connections
