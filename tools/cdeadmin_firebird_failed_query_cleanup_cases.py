"""Controlled cursor faults over real owned Firebird database attachments."""

from contextlib import ExitStack
from dataclasses import replace
from unittest.mock import patch

from pgadmin.cdeadmin.sdk.relational import RelationalClientError


class CursorFault:
    """Inject at the provider boundary, not driver-internal cursor reset."""

    def __init__(self, native, stage, close_failure):
        self.native = native
        self.stage = stage
        self.close_failure = close_failure
        self.close_calls = 0

    def __getattr__(self, name):
        return getattr(self.native, name)

    def fetchmany(self, count):
        rows = self.native.fetchmany(count)
        if self.stage == 'fetch':
            assert rows == [(1,)]
            raise RuntimeError('owned-fetch-result-fault')
        return rows

    def close(self):
        self.close_calls += 1
        if self.close_failure:
            raise RuntimeError('owned-cursor-close-fault')
        return self.native.close()


def qualify_failed_cursor_cleanup(make_client, route, result):
    """The caller owns the disposable database, never a user connection."""
    owner = make_client()
    owner_handle = owner.open_session({'route': route})

    def sql(client, handle, source):
        return client.describe_result(client.execute(handle, {
            'source': source}))['payload']['rows']

    try:
        sql(owner, owner_handle,
            'CREATE TABLE OWNED_QUERY_FAILURES (N INTEGER PRIMARY KEY)')
        owner.control_transaction(owner_handle, 'commit')
        for asynchronous in (False, True):
            for stage in ('execute', 'fetch', 'columns', 'values'):
                for close_failure in (False, True):
                    case = (f'failed-cursor-{asynchronous}-{stage}-'
                            f'cleanup-{close_failure}')
                    client = make_client()
                    handle = None
                    try:
                        handle = client.open_session({'route': route})
                        sql(client, handle,
                            'INSERT INTO OWNED_QUERY_FAILURES VALUES (1)')
                        cursor = CursorFault(handle.cursor(), stage,
                                             close_failure)
                        columns = client.config.query_columns_reader

                        def failed_columns(value):
                            columns(value)
                            raise RuntimeError('owned-column-metadata-fault')

                        def failed_values(value):
                            raise RuntimeError('owned-value-conversion-fault')

                        if stage == 'columns':
                            client.config = replace(
                                client.config,
                                query_columns_reader=failed_columns)
                        elif stage == 'values':
                            client.config = replace(
                                client.config,
                                query_value_normalizer=failed_values)
                        request = {
                            'source': ('SELECT OWNED_MISSING_COLUMN FROM '
                                       'OWNED_QUERY_FAILURES' if
                                       stage == 'execute' else
                                       'SELECT N FROM OWNED_QUERY_FAILURES'),
                            'output_policy': {'max_rows': 2}}
                        with ExitStack() as patches:
                            factory = patches.enter_context(patch.object(
                                handle, 'cursor', return_value=cursor))
                            if asynchronous:
                                query = client.submit_query(handle, request)
                                query.worker.join(30)
                                assert query.done
                                payload = client.describe_result(query)[
                                    'payload']
                                assert payload['execution_state'] == 'failed'
                                assert payload.get('session_reuse_blocked',
                                                   False) is close_failure
                                codes = payload['error']['native_status_codes']
                            else:
                                try:
                                    client.execute(handle, request)
                                except RelationalClientError as error:
                                    codes = list(error.gds_codes)
                                    assert getattr(error,
                                                   'cursor_cleanup_failed',
                                                   False) is close_failure
                                else:
                                    raise AssertionError('Query fault hidden')
                            assert bool(codes) is (stage == 'execute')
                            assert cursor.close_calls == 1
                            assert handle.main_transaction.is_active()
                            if close_failure:
                                count = factory.call_count
                                for operation in (client.execute,
                                                  client.submit_query):
                                    try:
                                        operation(handle, {
                                            'source':
                                            'SELECT 1 FROM RDB$DATABASE'})
                                    except RelationalClientError as error:
                                        assert 'reconnect' in str(error)
                                    else:
                                        raise AssertionError('Unsafe reuse')
                                assert factory.call_count == count
                        if not close_failure:
                            client.config = replace(
                                client.config, query_columns_reader=None,
                                query_value_normalizer=None)
                            rows = sql(client, handle,
                                       'SELECT N FROM OWNED_QUERY_FAILURES')
                            assert rows == [(1,)]
                        client.close_session(handle)
                        assert handle.is_closed()
                        handle = None
                        assert sql(owner, owner_handle,
                                   'SELECT N FROM OWNED_QUERY_FAILURES') == []
                        owner.control_transaction(owner_handle, 'rollback')
                        result['cases'].append(case)
                    except Exception as error:
                        result['failures'].append({
                            'case': case, 'error_type': type(error).__name__})
                    finally:
                        if handle is not None:
                            client.close_session(handle)
                        client.close()
    finally:
        owner.close_session(owner_handle)
        owner.close()
