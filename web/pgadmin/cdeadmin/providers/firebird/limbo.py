##########################################################################
# CDEadmin - Multi-engine Database Administration
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
##########################################################################

"""Native Firebird prepared inventory and explicitly owned recovery handles.

The application uses attachment inventory and native transaction interfaces.
Service helpers retain exact-version qualification of native listing and
recovery; they do not advertise unqualified distributed service behavior.
Wire authority: Firebird 5.0.4 alice/tdr.cpp, jrd/svc.cpp,
and impl/consts_pub.h.
Transaction IDs remain decimal strings at the JSON/UI boundary.
"""

import re
import ctypes
import threading
import time
from collections.abc import Mapping

from .character_metadata import text
from .service_connection import validate_service_role


OPERATIONS = frozenset({
    'inspect_limbo', 'commit_limbo', 'rollback_limbo', 'recover_limbo'})
ATTACHMENT_OPERATIONS = frozenset({
    'inspect_limbo', 'commit_limbo_local', 'rollback_limbo_local'})
WARNING = (
    'Recovery changes only the selected database participant. It does not '
    'establish a global distributed outcome. Review the coordinator decision '
    'and every participating database before choosing commit or rollback. '
    'Stored participant paths are historical metadata, not verified endpoints '
    'or permission to reuse credentials. A lost response is not proof of '
    'rollback; never automatically replay a recovery decision.')
MAX_TRANSACTION = 9223372036854775807
MAX_OUTPUT = 16 * 1024 * 1024
_STATES = {22: 'limbo', 23: 'committed', 24: 'rolled_back', 25: 'unknown'}
_ADVICE = {30: 'commit', 31: 'rollback', 33: 'unknown'}


def transaction_id(value):
    if type(value) is int:
        value = str(value)
    if (not isinstance(value, str) or
            not re.fullmatch(r'[1-9][0-9]{0,18}', value) or
            int(value) > MAX_TRANSACTION):
        raise ValueError(
            'Enter an exact positive signed-64-bit transaction ID')
    return value


def validate(operation, draft, database):
    if (not isinstance(operation, str) or
            operation not in OPERATIONS | ATTACHMENT_OPERATIONS or
            not isinstance(draft, Mapping)):
        raise ValueError('Unknown Firebird limbo operation or invalid draft')
    allowed = {'role'} if operation == 'inspect_limbo' else {
        'role', 'transaction_id', 'confirmation', 'database_confirmation',
        'coordinator_reviewed'}
    if set(draft) - allowed:
        raise ValueError('Unknown Firebird limbo form fields')
    database = text(database, 'Database filename or alias')
    if not database:
        raise ValueError('An exact database filename or alias is required')
    role = draft.get('role')
    if role == '':
        role = None
    if operation in ATTACHMENT_OPERATIONS:
        if role is not None:
            role = text(role, 'SQL role')
            if len(role) > 63:
                raise ValueError('SQL role exceeds 63 characters')
    else:
        validate_service_role(role)
    result = {'role': role}
    if operation != 'inspect_limbo':
        identifier = transaction_id(draft.get('transaction_id'))
        if draft.get('confirmation') != identifier:
            raise ValueError('Repeat the exact transaction ID to confirm')
        if draft.get('database_confirmation') != database:
            raise ValueError('Confirm the exact database filename or alias')
        if draft.get('coordinator_reviewed') is not True:
            raise ValueError('Confirm review of the distributed coordinator '
                             'and all participating database states')
        result['transaction_id'] = identifier
    return result


def form(operation, field):
    titles = {
        'inspect_limbo': 'Inspect Firebird prepared transactions',
        'commit_limbo_local': 'Commit a local prepared transaction',
        'rollback_limbo_local': 'Roll back a local prepared transaction',
    }
    if operation not in titles:
        raise ValueError('Unknown Firebird attachment recovery form')
    fields = []
    if operation != 'inspect_limbo':
        fields = [
            field(
                'transaction_id',
                'Local transaction ID',
                'text',
                True,
                WARNING),
            field(
                'confirmation',
                'Confirm transaction ID',
                'text',
                True,
                'Repeat the exact decimal transaction ID.'),
            field(
                'database_confirmation',
                'Confirm database filename or alias',
                'text',
                True,
                'Repeat the exact selected database target.'),
            field(
                'coordinator_reviewed',
                'Coordinator and participant states reviewed',
                'boolean',
                True,
                WARNING,
                default=False),
        ]
    fields.append(field('role', 'SQL role for this attachment', 'text', False,
                        'Blank uses the connection profile role. This does '
                        'not change the saved profile.'))
    return {'form_id': 'firebird_' + operation,
            'title': titles[operation], 'fields': fields}


def parse_report(data, encoding='utf-8'):
    """Decode the native tagged stream; reject unknown or malformed input."""
    if not isinstance(data, bytes) or len(data) > MAX_OUTPUT:
        raise ValueError('Invalid or oversized Firebird limbo response')
    position = 0
    records = []
    current = participant = None
    last_rank = -1

    def take(size):
        nonlocal position
        if size < 0 or position + size > len(data):
            raise ValueError('Truncated Firebird limbo response')
        value = data[position:position + size]
        position += size
        return value

    def number(size):
        return transaction_id(int.from_bytes(
            take(size), 'little', signed=True))

    def finish_participant():
        nonlocal participant, last_rank
        if participant is not None:
            current['participants'].append(participant)
            participant = None
        last_rank = -1

    def finish_record():
        if current is not None:
            finish_participant()
            if current['kind'] == 'distributed' and 'advice' not in current:
                raise ValueError('Distributed limbo advice is missing')

    while position < len(data):
        tag = take(1)[0]
        if tag in (19, 20, 47, 48):
            finish_record()
            identifier = number(8 if tag in (47, 48) else 4)
            if any(item['transaction_id'] == identifier for item in records):
                raise ValueError('Duplicate Firebird limbo transaction ID')
            current = {
                'transaction_id': identifier,
                'kind': 'single' if tag in (19, 47) else 'distributed',
                'participants': [],
            }
            records.append(current)
            continue
        if current is None or current['kind'] != 'distributed':
            raise ValueError(
                'Limbo participant has no distributed transaction')
        if 'advice' in current:
            raise ValueError('Unexpected data after native recovery advice')
        if tag == 29:
            finish_participant()
            advice = take(1)[0]
            if advice not in _ADVICE:
                raise ValueError('Unknown native recovery advice')
            current['advice'] = _ADVICE[advice]
            continue
        rank = {26: 0, 18: 1, 46: 1, 21: 2, 27: 3, 28: 4}.get(tag)
        if rank is None:
            raise ValueError('Unknown Firebird limbo response tag')
        if participant is not None and rank <= last_rank:
            finish_participant()
        if participant is None:
            participant = {}
        last_rank = rank
        if tag in (26, 27, 28):
            length = int.from_bytes(take(2), 'little')
            value = take(length).decode(encoding, errors='strict')
            if '\x00' in value:
                raise ValueError('NUL in native limbo description')
            participant[{26: 'host', 27: 'remote_host',
                         28: 'database'}[tag]] = (value)
        elif tag in (18, 46):
            participant['transaction_id'] = number(8 if tag == 46 else 4)
        else:
            state = take(1)[0]
            if state not in _STATES:
                raise ValueError('Unknown native participant state')
            participant['state'] = _STATES[state]
    finish_record()
    return records


def parse_description(data, encoding='utf-8'):
    """Decode the engine's stored TDR; never treat its paths as credentials.

    Authority: prepareCommit/buildPrepareInfo in DistributedTransaction.cpp,
    and alice_meta.epp::get_description. The native writer uses one-byte
    lengths and can truncate fields at 255 bytes, so that boundary is explicit.
    """
    if (not isinstance(data, bytes) or len(data) > MAX_OUTPUT or
            not data or data[0] != 1):
        raise ValueError('Invalid Firebird transaction description version')
    position = 1
    result = {'participants': [], 'native_length_limit_reached': False}
    path = None
    while position < len(data):
        if position + 2 > len(data):
            raise ValueError('Truncated native transaction description')
        tag, length = data[position:position + 2]
        position += 2
        if not length or position + length > len(data):
            raise ValueError('Invalid native transaction description field')
        value = data[position:position + length]
        position += length
        if length == 255:
            result['native_length_limit_reached'] = True
        if tag in (1, 2):
            value = value.decode(encoding, errors='strict')
            if '\x00' in value:
                raise ValueError('NUL in native transaction description')
            if tag == 1:
                if ('host' in result or path is not None or
                        result['participants']):
                    raise ValueError('Unexpected coordinator host field')
                result['host'] = value
            else:
                if path is not None:
                    raise ValueError('A participant transaction ID is missing')
                path = value
        elif tag == 3:
            if path is None or length not in (4, 8):
                raise ValueError('Invalid participant transaction ID field')
            result['participants'].append({
                'database': path,
                'transaction_id': transaction_id(int.from_bytes(
                    value, 'little', signed=True)),
            })
            path = None
        else:
            raise ValueError('Unknown native transaction description field')
    if path is not None or not result['participants'] or 'host' not in result:
        raise ValueError('Incomplete native transaction description')
    return result


def _metadata_transaction(connection, module):
    return connection.transaction_manager(
        default_tpb=module.tpb(
            isolation=module.Isolation.READ_COMMITTED_RECORD_VERSION,
            access_mode=module.TraAccessMode.READ),
        default_action=module.DefaultAction.ROLLBACK)


def inventory(connection, module):
    """Read repeated native LIMBO items, retrying only read-only truncation.

    INF_put_item places isc_info_truncated after the last complete item, not
    necessarily at byte zero. Some driver versions only test byte zero before
    checking the response terminator. Parse item boundaries instead; never
    publish the partial inventory or merge observations from different reads.
    """
    tag = int(module.DbInfoCode.LIMBO)
    size = 256
    while True:
        buffer = ctypes.create_string_buffer(size)
        connection._att.get_info(bytes([tag]), buffer)
        raw = buffer.raw
        position = 0
        values = []
        seen = set()
        while position < size:
            item = raw[position]
            position += 1
            if item == 1:
                return values
            if item == 2:
                break
            if item != tag or position + 2 > size:
                raise ValueError('Invalid native limbo inventory item')
            length = int.from_bytes(raw[position:position + 2], 'little')
            position += 2
            if length not in (4, 8) or position + length >= size:
                raise ValueError('Invalid native limbo inventory integer')
            value = transaction_id(int.from_bytes(
                raw[position:position + length], 'little', signed=True))
            position += length
            if value in seen:
                raise ValueError('Duplicate native limbo inventory entries')
            seen.add(value)
            values.append(value)
        else:
            raise ValueError('Native limbo inventory terminator is missing')
        if size == 32767:
            raise ValueError('Native limbo inventory exceeds the safe bound')
        size = min(size * 2, 32767)


def inspect_attachment(connection, module):
    """Read local native inventory and TDRs without reattaching to peer hosts.

    The native reader expands its buffer or raises; unlike gfix's fixed
    1024-byte inventory, it does not silently report a partial list.
    Participant descriptions do not establish the current state of peer DBs.
    """
    identifiers = inventory(connection, module)
    records = []
    with _metadata_transaction(connection, module) as manager:
        with manager.cursor() as cursor:
            for identifier in identifiers:
                cursor.execute(
                    'SELECT RDB$TRANSACTION_STATE, '
                    'RDB$TRANSACTION_DESCRIPTION FROM RDB$TRANSACTIONS '
                    'WHERE RDB$TRANSACTION_ID = ?', (int(identifier),))
                row = cursor.fetchone()
                record = {'transaction_id': identifier,
                          'local_inventory_state': 'limbo',
                          'participants': [], 'kind': 'single',
                          'peer_states_observed': False}
                if row:
                    record['recorded_local_state'] = {
                        1: 'limbo', 2: 'committed', 3: 'rolled_back',
                    }.get(row[0], 'unknown')
                    description = row[1]
                    if description is not None:
                        if hasattr(description, 'read'):
                            description = description.read(MAX_OUTPUT + 1)
                        decoded = parse_description(description)
                        record.update(decoded, kind='distributed')
                records.append(record)
    return records


class NativeRecovery:
    """Own fresh attachments and reconstructed native transaction handles.

    The provider must authorize every connection before constructing this
    owner, retain it until close succeeds, and never borrow query sessions.
    Native metadata is not an endpoint/credential authorization mechanism.
    Cleanup detaches attachments before releasing unresolved prepared handles;
    it never issues COMMIT, ROLLBACK or a DTC default action as cleanup.
    """

    def __init__(self, participants, decision, module):
        if decision not in ('commit', 'rollback') or not participants:
            raise ValueError('Choose an explicit native recovery decision')
        self.participants = tuple((connection, transaction_id(identifier))
                                  for connection, identifier in participants)
        if not self.participants:
            raise ValueError('Recovery requires at least one participant')
        if len({id(connection) for connection, _ in self.participants}) != len(
                self.participants):
            raise ValueError(
                'Recovery requires independently owned attachments')
        self.decision = decision
        self.module = module
        self.started = False
        self.dispatched = False
        self.returned = False
        self.closed = False
        self._handles = []
        self._borrowed_connections = set()
        self._lock = threading.Lock()

    def run(self):
        if not self._lock.acquire(blocking=False):
            raise ValueError('Native recovery is busy')
        try:
            return self._run()
        finally:
            self._lock.release()

    def _run(self):
        if self.started or self.closed:
            raise ValueError('Native recovery cannot be replayed')
        self.started = True
        # Check all participants before reconnecting any prepared handle.
        for connection, _ in self.participants:
            if connection.main_transaction.is_active() or any(
                    manager.is_active()
                    for manager in connection._transactions):
                self._borrowed_connections.add(id(connection))
        if self._borrowed_connections:
            raise ValueError('Recovery cannot borrow an active transaction')
        for connection, identifier in self.participants:
            if identifier not in inventory(connection, self.module):
                raise ValueError(
                    'A reviewed transaction is no longer in limbo')
        joined = None
        for connection, identifier in self.participants:
            value = int(identifier)
            handle = connection._att.reconnect_transaction(
                value.to_bytes(4 if value <= 2147483647 else 8, 'little'))
            self._handles.append(handle)
            if joined is None:
                joined = handle
            else:
                joined = joined.join(handle)
                self._handles.append(joined)
        self.dispatched = True
        getattr(joined, self.decision)()
        self.returned = True
        return {
            'native_decision_requested': self.decision,
            'native_decision_returned': True,
            'participant_transaction_ids': [
                identifier for _, identifier in self.participants],
            'automatic_mutation_retry': False,
            'provider_finality_authority': True,
        }

    def close(self):
        if not self._lock.acquire(blocking=False):
            raise ValueError('Native recovery is busy')
        try:
            return self._close()
        finally:
            self._lock.release()

    def _close(self):
        if self.closed:
            return
        if not self.started:
            # Reject borrowed active handles even if dispatch was never called.
            for connection, _ in self.participants:
                if connection.main_transaction.is_active() or any(
                        manager.is_active()
                        for manager in connection._transactions):
                    self._borrowed_connections.add(id(connection))
        failures = []
        for connection, _ in self.participants:
            if id(connection) in self._borrowed_connections:
                continue
            try:
                if connection.is_closed() is not True:
                    connection.close()
                if connection.is_closed() is not True:
                    raise RuntimeError(
                        'Recovery attachment release unconfirmed')
            except Exception as error:
                failures.append(error)
        # Releasing a prepared remote transaction before attachment detach can
        # roll it back. Retain every native handle if any detach is
        # unconfirmed.
        if not failures:
            for handle in self._handles:
                try:
                    if handle._refcnt > 0:
                        handle.release()
                except Exception as error:
                    failures.append(error)
        if failures:
            raise failures[0]
        self._handles.clear()
        self.closed = True


def _read_report(server, timeout=120):
    """Drain binary service frames, including chunks after the worker exits."""
    deadline = time.monotonic() + timeout
    chunks = []
    size = 0
    while time.monotonic() < deadline:
        server.response.clear()
        server._svc.query(server._make_request(2), bytes([66]),
                          server.response.raw)
        raw = bytes(server.response.raw)
        if len(raw) < 4 or raw[0] != 66:
            raise ValueError('Unexpected Firebird limbo service frame')
        length = int.from_bytes(raw[1:3], 'little')
        boundary = 3 + length
        if boundary >= len(raw) or raw[boundary] not in (1, 2, 4, 64):
            raise ValueError('Malformed Firebird limbo service frame')
        size += length
        if size > MAX_OUTPUT:
            raise ValueError('Firebird limbo report exceeds the safe bound')
        chunks.append(raw[3:boundary])
        if not length and raw[boundary] == 1 and not server.is_running():
            return b''.join(chunks)
    raise TimeoutError('Firebird limbo service completion is unconfirmed')


def inspect(service, database, module):
    server = service._srv()
    core = module.core
    server._reset_output()
    with module.get_api().util.get_xpb_builder(core.XpbKind.SPB_START) as spb:
        spb.insert_tag(core.ServerAction.REPAIR)
        spb.insert_string(
            core.SPBItem.DBNAME,
            database,
            encoding=server.encoding)
        spb.insert_int(core.SPBItem.OPTIONS,
                       module.SrvRepairFlag.LIST_LIMBO_TRANS)
        server._svc.start(spb.get_buffer())
    return parse_report(_read_report(server), server.encoding)


def resolve(service, database, operation, identifier, module):
    """Dispatch one explicitly reviewed native recovery choice, never ALL."""
    identifier = transaction_id(identifier)
    choices = {'commit_limbo': 'COMMIT_TRANS',
               'rollback_limbo': 'ROLLBACK_TRANS',
               'recover_limbo': 'RECOVER_TWO_PHASE'}
    if operation not in choices:
        raise ValueError('Unknown Firebird limbo resolution')
    server = service._srv()
    core = module.core
    value = int(identifier)
    name = choices[operation] + ('_64' if value > 2147483647 else '')
    server._reset_output()
    with module.get_api().util.get_xpb_builder(core.XpbKind.SPB_START) as spb:
        spb.insert_tag(core.ServerAction.REPAIR)
        spb.insert_string(
            core.SPBItem.DBNAME,
            database,
            encoding=server.encoding)
        insert = spb.insert_bigint if value > 2147483647 else spb.insert_int
        insert(getattr(core.SrvRepairOption, name), value)
        server._svc.start(spb.get_buffer())
    # A recovery response can contain participant descriptions. It is a native
    # observation, not evidence that every distributed participant committed.
    return _read_report(server)
