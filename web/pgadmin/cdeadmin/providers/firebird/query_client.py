"""Firebird asynchronous queries with attachment ownership and cancellation."""

import copy
import threading
from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field

from pgadmin.cdeadmin.sdk.relational import (
    RelationalClientError, RelationalDBAPIClient, _ResultToken,
)
from .error_diagnostics import status_codes
from .query_parameters import normalize_parameters
from .query_limits import query_row_limit
from .service_connection import validate_service_role
from .transaction_sql import (
    start_native_transaction, starts_transaction, transaction_command,
)


@dataclass(eq=False)
class _Query:
    handle: object
    result: dict | None = None
    cancellation_attempted: bool = False
    done: bool = False
    worker: object = None


@dataclass
class _AttachmentState:
    lock: object = field(default_factory=threading.RLock)
    query: _Query | None = None
    cancellation_state_unknown: bool = False
    result_cleanup_failed: bool = False


class FirebirdQueryClient(RelationalDBAPIClient):
    def __init__(self, config, module=None, *, service_connector=None,
                 service_attached=None):
        super().__init__(config, module)
        if service_connector is not None:
            self._server_connector = service_connector
        self._service_attached = service_attached
        self._admission = threading.RLock()
        self._attachment_states = {}
        self._server_handles = set()
        self._queries = []
        self._closed = False
        self._opening = 0
        self._native_operations = 0

    @contextmanager
    def _temporary_operation(self):
        with self._admission:
            if self._closed:
                raise RelationalClientError('Firebird client is closed')
            self._native_operations += 1
        try:
            yield
        finally:
            with self._admission:
                self._native_operations -= 1

    @contextmanager
    def _connecting(self):
        with self._admission:
            if self._closed:
                raise RelationalClientError('Firebird client is closed')
            self._opening += 1
        try:
            yield
        finally:
            with self._admission:
                self._opening -= 1

    def _connect(self, request):
        with self._connecting():
            return super()._connect(request)

    def _connect_server(self, request):
        with self._connecting():
            handle = super()._connect_server(request)
            self._server_handles.add(id(handle))
            if self._service_attached is not None:
                try:
                    self._service_attached(handle)
                except Exception as exc:
                    failure = RelationalClientError(
                        'Firebird service attachment hook failed (' +
                        type(exc).__name__ + ')')
                    self._finish_server_operation(handle, None, failure)
                    raise failure from None
            return handle

    def _forget_connection(self, handle):
        super()._forget_connection(handle)
        self._server_handles.discard(id(handle))

    def _forget_and_close(self, handle):
        if id(handle) in self._server_handles:
            return self._release_server(handle)
        return super()._forget_and_close(handle)

    def _release_server(self, handle):
        # A Services API attachment is not a database TransactionManager.
        # Driver Server has no is_closed(), and detach errors must not be
        # silently forgotten by the generic temporary-connection cleanup.
        try:
            handle.close()
            if handle._svc is not None:
                raise RelationalClientError(
                    'Firebird service handle release is unconfirmed')
        except Exception as exc:
            error = RelationalClientError(
                'Firebird service release did not complete (' +
                type(exc).__name__ + ')')
            error.gds_codes = status_codes(exc)
            raise error from None
        self._forget_connection(handle)
        return {'connection_released': True,
                'service_handle_released': True,
                'rollback_requested': False,
                'driver_observation_only': True}

    def _state(self, handle):
        with self._admission:
            if self._closed:
                raise RelationalClientError('Firebird client is closed')
            if not any(item is handle for item in self._connections):
                raise RelationalClientError('Firebird session is unavailable')
            return self._attachment_states.setdefault(
                id(handle), _AttachmentState())

    @contextmanager
    def _exclusive(self, handle, *, closing=False):
        state = self._state(handle)
        if not state.lock.acquire(blocking=False):
            raise RelationalClientError('Firebird session is busy')
        try:
            if not any(item is handle for item in self._connections):
                raise RelationalClientError('Firebird session is unavailable')
            if state.query is not None:
                raise RelationalClientError(
                    'Firebird query is running; cancel or wait before '
                    'using this session')
            if state.cancellation_state_unknown and not closing:
                raise RelationalClientError(
                    'Firebird cancellation state is unknown; close this '
                    'session and explicitly reconnect')
            if state.result_cleanup_failed and not closing:
                raise RelationalClientError(
                    'Firebird result cleanup failed; close this session '
                    'and explicitly reconnect')
            yield state
        finally:
            state.lock.release()

    def submit_query(self, handle, request):
        # Validation and a private copy precede the worker and native dispatch.
        source = request.get('source')
        if not isinstance(source, str) or not source.strip():
            raise RelationalClientError('Firebird query source is required')
        payload = copy.deepcopy(dict(request))
        self.config.query_parameter_normalizer(payload.get('parameters', ()))
        query_row_limit(payload)
        with self._exclusive(handle) as state:
            query = _Query(handle)
            state.query = query
            self._queries.append(query)
            query.worker = threading.Thread(
                target=self._run_query, args=(query, state, payload),
                name='cdeadmin-firebird-query', daemon=True)
            try:
                query.worker.start()
            except Exception:
                state.query = None
                self._queries.remove(query)
                raise
            return query

    def _run_query(self, query, state, request):
        try:
            token = self._execute_sql(query.handle, request)
            native = self._describe_native_token(token)
            native['payload']['execution_state'] = 'succeeded'
        except Exception as exc:
            codes = tuple(getattr(exc, 'gds_codes', ()))
            native = {
                'result_kind': self.config.profile.result_kind,
                'schema': {'columns': []}, 'complete': True,
                'stream_reference': None,
                'payload': {
                    'rows': [], 'rowcount': None,
                    'execution_state': 'cancelled' if 335544794 in codes
                    else 'failed',
                    'error': {
                        'message': (
                            'Firebird query executed, but result cleanup '
                            'failed. Do not replay the statement.'
                            if getattr(exc, 'native_execution_completed',
                                       False)
                            else ('Firebird query failed and cursor cleanup '
                                  'did not complete. Do not replay the '
                                  'statement; close this session.'
                                  if getattr(exc, 'cursor_cleanup_failed',
                                             False)
                                  else 'Firebird query did not complete.')),
                        'error_type': type(exc).__name__,
                        'native_status_codes': list(codes),
                        'native_execution_completed': bool(getattr(
                            exc, 'native_execution_completed', False)),
                        'cursor_cleanup_failed': bool(getattr(
                            exc, 'cursor_cleanup_failed', False)),
                        'cleanup_status_codes': list(getattr(
                            exc, 'cleanup_gds_codes', ())),
                    },
                },
            }
        with state.lock:
            if state.result_cleanup_failed:
                native['payload']['session_reuse_blocked'] = True
                native['payload']['session_reuse_blocked_reason'] = (
                    'result_cleanup_failed')
            if query.cancellation_attempted:
                # A request can finish just before RAISE reaches the server.
                # Clear a late pending signal before another command is
                # admitted. Never substitute ABORT (attachment rollback).
                try:
                    query.handle._att.cancel_operation(
                        self.module.CancelType.DISABLE)
                    query.handle._att.cancel_operation(
                        self.module.CancelType.ENABLE)
                except Exception as exc:
                    state.cancellation_state_unknown = True
                    native['payload']['session_reuse_blocked'] = True
                    native['payload']['session_reuse_blocked_reason'] = (
                        'cancellation_state_unknown')
                    native['payload']['cancel_cleanup_error_type'] = (
                        type(exc).__name__)
            query.result = native
            query.done = True
            state.query = None

    def describe_result(self, token):
        if not isinstance(token, _Query):
            return self._describe_native_token(token)
        if token not in self._queries:
            raise RelationalClientError('Firebird query token is unavailable')
        state = self._state(token.handle)
        with state.lock:
            if token.done:
                return copy.deepcopy(token.result)
            return {
                'result_kind': self.config.profile.result_kind,
                'schema': {'columns': []}, 'complete': False,
                'stream_reference': None,
                'payload': {'rows': [], 'execution_state': 'running',
                            'cancel_requested': token.cancellation_attempted},
            }

    def cancel(self, token):
        if not isinstance(token, _Query):
            return False  # Synchronous results have already completed.
        if token not in self._queries:
            raise RelationalClientError('Firebird query token is unavailable')
        state = self._state(token.handle)
        with state.lock:
            if token.done or state.query is not token:
                return False
            token.cancellation_attempted = True
            try:
                token.handle._att.cancel_operation(
                    self.module.CancelType.RAISE)
            except Exception as exc:
                raise RelationalClientError(
                    'Firebird cancellation request outcome is unknown (' +
                    type(exc).__name__ + ')') from None
            return True  # Delivery accepted, NOT an observed final outcome.

    def execute(self, handle, request):
        with self._exclusive(handle):
            return self._execute_sql(handle, request)

    def _execute_sql(self, handle, request):
        limit = query_row_limit(request)
        if starts_transaction(request.get('source')):
            return self._start_transaction_sql(handle, request)
        command = transaction_command(request.get('source'))
        if command is None:
            token = super().execute(handle, request)
            if limit is not None and token.columns:
                token.firebird_fetch_observation = {
                    'max_rows': limit,
                    'rows_returned': len(token.rows),
                    'limit_reached': len(token.rows) == limit,
                    'end_of_cursor_observed': len(token.rows) < limit,
                    'total_rows': (len(token.rows)
                                   if len(token.rows) < limit else None),
                    'sql_rewritten': False,
                    'transaction_action_requested': False,
                }
            return token
        if normalize_parameters(request.get('parameters')):
            raise RelationalClientError(
                'Firebird transaction commands do not accept parameters')
        action, retaining = command
        receipt = self._finish_transaction(handle, action, retaining)
        token = _ResultToken(None, handle, (), [], None, closed=True)
        token.firebird_transaction_receipt = receipt
        self._tokens.append(token)
        return token

    def _fetch_query_rows(self, cursor, request):
        limit = query_row_limit(request)
        if limit is None:
            return super()._fetch_query_rows(cursor, request)
        # Do not fetch a look-ahead row: selectable PSQL may have side effects.
        # Reaching the bound does not establish whether another row exists.
        return list(cursor.fetchmany(limit))

    def _close_failed_query_cursor(self, handle, cursor, original_error):
        try:
            cursor.close()
        except Exception as cleanup_error:
            state = self._state(handle)
            with state.lock:
                state.result_cleanup_failed = True
            error = RelationalClientError(
                'Firebird query failed and result cursor cleanup failed; '
                'do not replay the statement. Close this session and '
                'explicitly reconnect')
            error.gds_codes = status_codes(original_error)
            error.cleanup_gds_codes = status_codes(cleanup_error)
            error.cursor_cleanup_failed = True
            raise error from None

    def _start_transaction_sql(self, handle, request):
        if normalize_parameters(request.get('parameters')):
            raise RelationalClientError(
                'Firebird SET TRANSACTION does not accept parameters')
        if handle.main_transaction.is_active():
            raise RelationalClientError(
                'Complete the current Firebird transaction before '
                'SET TRANSACTION; pending work has not been changed')
        try:
            # Native SET TRANSACTION bypasses TransactionManager.begin().
            # Run its idle cleanup so a legacy API handle from the previous
            # transaction cannot be reused by array/event/native operations.
            handle.main_transaction._finish()
            native = start_native_transaction(handle, request['source'])
        except Exception as exc:
            error = RelationalClientError(
                'Firebird SET TRANSACTION did not complete (' +
                type(exc).__name__ + ')')
            error.gds_codes = status_codes(exc)
            raise error from None
        handle.main_transaction._tra = native
        token = _ResultToken(None, handle, (), [], None, closed=True)
        token.firebird_transaction_receipt = {
            'action': 'begin', 'native_call_made': True,
            'observation': 'Firebird returned a native transaction interface',
        }
        self._tokens.append(token)
        return token

    @staticmethod
    def _finish_transaction(handle, action, retaining=False):
        try:
            active = handle.main_transaction.is_active()
            if active:
                getattr(handle, action)(retaining=retaining)
        except Exception as exc:
            error = RelationalClientError(
                'Firebird transaction command outcome is unavailable (' +
                type(exc).__name__ + ')')
            error.gds_codes = status_codes(exc)
            raise error from None
        return {
            'action': action, 'retaining_requested': retaining,
            'native_call_made': bool(active),
            'observation': ('driver method returned' if active else
                            'no active transaction; no native call made'),
        }

    def _describe_native_token(self, token):
        if (isinstance(token, _ResultToken) and
                any(item is token for item in self._tokens) and
                not token.closed and token.cursor is not token.connection):
            try:
                token.cursor.close()
            except Exception as exc:
                state = self._state(token.connection)
                with state.lock:
                    state.result_cleanup_failed = True
                error = RelationalClientError(
                    'Firebird query executed but result cursor release '
                    'failed (' + type(exc).__name__ + ')')
                error.gds_codes = status_codes(exc)
                error.native_execution_completed = True
                raise error from None
            token.closed = True
        result = super().describe_result(token)
        receipt = getattr(token, 'firebird_transaction_receipt', None)
        if receipt is not None:
            result['payload']['transaction_action'] = copy.deepcopy(receipt)
        observation = getattr(token, 'firebird_fetch_observation', None)
        if observation is not None:
            result['payload']['fetch_observation'] = copy.deepcopy(observation)
        return result

    def runtime_identity(self, request, handle=None):
        if handle is None:
            with self._temporary_operation():
                return super().runtime_identity(request, handle)
        with self._exclusive(handle):
            try:
                version = self.config.version_parser(
                    (handle.info.firebird_version,))
            except Exception as exc:
                raise RelationalClientError(
                    'Firebird attachment version is unavailable (' +
                    type(exc).__name__ + ')') from None
            return {'engine_id': self.config.profile.engine_id,
                    'version': version,
                    'build_id': 'firebird:' + version,
                    'protocol_id': self.config.profile.protocol_id}

    def describe_transaction(self, handle):
        with self._exclusive(handle):
            return super().describe_transaction(handle)

    def control_transaction(self, handle, action):
        with self._exclusive(handle):
            if action not in self.transaction_actions:
                raise RelationalClientError(
                    'Firebird transaction action is unavailable')
            return self._finish_transaction(handle, action)

    def close_session(self, handle):
        with self._exclusive(handle, closing=True):
            result = self._release_attachment(handle)
            self._queries[:] = [query for query in self._queries
                                if query.handle is not handle]
            self._tokens[:] = [token for token in self._tokens
                               if token.connection is not handle]
            with self._admission:
                self._attachment_states.pop(id(handle), None)
            return result

    def _release_attachment(self, handle):
        if id(handle) in self._server_handles:
            return self._release_server(handle)
        try:
            return super().close_session(handle)
        except Exception as exc:
            codes = status_codes(exc)
            if 335544856 not in codes:  # isc_att_shutdown, native observation
                raise
            # The server has already shut down this attachment. The installed
            # driver can still retain a transaction wrapper and fail rollback
            # before close reaches detach. Explicitly close local resources;
            # do not turn an unrelated/network error into confirmed shutdown.
            cleanup_error = None
            try:
                self.config.session_releaser(handle)
            except Exception as close_error:
                cleanup_error = type(close_error).__name__
            if handle.is_closed() is not True:
                raise RelationalClientError(
                    'Firebird shutdown observed, but local release is '
                    'unconfirmed') from None
            self._forget_connection(handle)
            return {
                'connection_released': True,
                'rollback_completion_confirmed': False,
                'native_attachment_shutdown_observed': True,
                'native_status_codes': list(codes),
                'local_cleanup_error_type': cleanup_error,
                'driver_observation_only': True,
                'finality_interpreted_by_common_code': False,
            }

    def list_resources(self, request):
        handle = request.get('_provider_session_handle')
        if handle is None:
            with self._temporary_operation():
                return super().list_resources(request)
        with self._exclusive(handle):
            return super().list_resources(request)

    def run_server_operation(self, request, operation_id, database, options):
        with self._temporary_operation():
            request, options = self._service_role_scope(request, options)
            return super().run_server_operation(
                request, operation_id, database, options)

    @staticmethod
    def _service_role_scope(request, options):
        # Firebird 5.0.4's database service start SPBs reject SQL_ROLE_NAME.
        # Service::start forwards the attachment role to the native utility.
        # Bind the reviewed task role to this one owned service attachment;
        # never change the saved endpoint or shared driver configuration.
        if (not isinstance(options, Mapping) or
                not isinstance(request, Mapping)):
            raise RelationalClientError('Firebird service request is invalid')
        scoped_request = copy.deepcopy(dict(request))
        scoped_options = copy.deepcopy(dict(options))
        role = scoped_options.pop('role', None)
        if role is not None and (not isinstance(role, str) or '\x00' in role):
            raise RelationalClientError('Firebird service role is invalid')
        validate_service_role(role)
        if role:
            route = scoped_request.get('route', {})
            if not isinstance(route, Mapping):
                raise RelationalClientError(
                    'Firebird service route is invalid')
            scoped_request['route'] = {
                **route, 'role': role}
        return scoped_request, scoped_options

    def _server_operation_error(self, error):
        codes = status_codes(error)
        diagnostic = ('; native status ' + ', '.join(map(str, codes))
                      if codes else '')
        failure = RelationalClientError(
            'Firebird service operation failed (' + type(error).__name__ +
            diagnostic + '). Completion is unconfirmed; inspect the native '
            'state before deciding what to do. Do not automatically replay '
            'the operation.')
        failure.gds_codes = codes
        return failure

    def plan_admin_operation(self, request):
        with self._temporary_operation():
            plan = super().plan_admin_operation(request)
            payload = plan.get('provider_payload', {})
            if payload.get('compiled', {}).get('driver_operation') == (
                    'firebird-service'):
                # Services credentials may differ from a database attachment.
                # Obtain them before the visual layer retains the one-shot
                # plan. A credential refresh can replace the provider binding
                # and must not strand a reviewed plan during Apply. This only
                # attaches/detaches: no service action is started here.
                request, _options = self._service_role_scope(
                    {'route': payload['route']},
                    payload['compiled'].get('options', {}))
                server = self._connect_server(request)
                self._release_server(server)
            return plan

    def _finish_server_operation(self, server, result, failure):
        try:
            receipt = self._release_server(server)
        except RelationalClientError as exc:
            receipt = {
                'connection_released': False,
                'service_handle_released': False,
                'driver_observation_only': True,
                'native_status_codes': list(status_codes(exc)),
                'message': (
                    'Firebird service handle release is unconfirmed. '
                    'Do not replay the operation; its returned outcome '
                    'is separate from handle cleanup.'),
            }
            # Failed attachments remain owned for explicit client release.
            # Never turn a returned native outcome into an unknown outcome,
            # or replace the original operation failure with a detach error.
            if result is None and failure is None:
                raise
        if result is not None:
            result['service_release'] = receipt
        elif failure is not None:
            failure.service_release = receipt

    def apply_admin_operation(self, request):
        handle = request.get('_provider_session_handle')
        if handle is None:
            with self._temporary_operation():
                return super().apply_admin_operation(request)
        with self._exclusive(handle):
            return super().apply_admin_operation(request)

    def read_admin_rows(self, request):
        handle = request.get('_provider_session_handle')
        if handle is None:
            with self._temporary_operation():
                return super().read_admin_rows(request)
        with self._exclusive(handle):
            return super().read_admin_rows(request)

    def close(self):
        acquired = []
        with self._admission:
            if self._closed:
                return
            if self._opening:
                raise RelationalClientError(
                    'Firebird connections are still opening')
            if self._native_operations:
                raise RelationalClientError(
                    'Firebird native operations are still running')
            try:
                for state in self._attachment_states.values():
                    if not state.lock.acquire(blocking=False):
                        raise RelationalClientError('Firebird session is busy')
                    acquired.append(state.lock)
                    if state.query is not None:
                        raise RelationalClientError(
                            'Firebird queries must finish before closing')
                failures = []
                for handle in tuple(self._connections):
                    try:
                        self._release_attachment(handle)
                    except Exception as exc:
                        failures.append(exc)
                if failures:
                    # Independent attachments still need their release attempt.
                    # Failed handles remain owned for an explicit retry.
                    raise failures[0]
                self._queries.clear()
                self._attachment_states.clear()
                super().close()
                self._closed = True
            finally:
                for lock in reversed(acquired):
                    lock.release()
