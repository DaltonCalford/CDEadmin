"""Read Firebird's retained transaction without starting or completing it."""

from enum import Enum

from pgadmin.cdeadmin.sdk.relational import RelationalClientError
from .error_diagnostics import status_codes


def release_session(connection):
    """Do not report an attachment as released after a close failure."""
    try:
        connection.close()
        if connection.is_closed() is not True:
            raise RelationalClientError(
                'Firebird driver did not confirm attachment closure')
    except RelationalClientError:
        raise
    except Exception as exc:
        codes = status_codes(exc)
        error = RelationalClientError(
            'Firebird session release failed (' + type(exc).__name__ + ')')
        error.gds_codes = codes
        raise error from None


def observe_transaction(connection):
    result = {
        'driver_observation_only': True,
        'finality_interpreted_by_common_code': False,
        'native_observation': 'Firebird TransactionManager / transaction info',
        'fields': {},
        'attachment_fields': {},
    }
    # These are distinct native observations. Database dialect must never be
    # used as a guess for the dialect used to prepare client SQL. Neither
    # accessor executes SQL or starts the caller's transaction.
    for name, read, supported in (
        ('client_sql_dialect', lambda: connection.sql_dialect, (1, 2, 3)),
        ('database_sql_dialect', lambda: connection.info.sql_dialect, (1, 3)),
    ):
        try:
            value = read()
            if type(value) is not int or value not in supported:
                raise ValueError('Invalid native dialect observation')
            result['attachment_fields'][name] = {
                'available': True, 'value': value}
        except Exception as exc:
            result['attachment_fields'][name] = {
                'available': False, 'error_type': type(exc).__name__}
    try:
        transaction = connection.main_transaction
        active = transaction.is_active()
        closed = transaction.is_closed()
    except Exception as exc:
        result.update(state='unknown', error_type=type(exc).__name__)
        return result
    result.update(in_transaction=bool(active),
                  state='closed' if closed else 'active' if active else 'idle')
    if closed or not active:
        return result
    try:
        info = transaction.info
    except Exception as exc:
        result['information_error_type'] = type(exc).__name__
        return result
    readers = {
        'transaction_id': lambda: info.id,
        'isolation': lambda: info.isolation,
        'read_only': lambda: info.is_read_only(),
        'lock_timeout_seconds': lambda: info.lock_timeout,
        'oldest_interesting_at_start': lambda: info.oit,
        'oldest_active_at_start': lambda: info.oat,
        'oldest_snapshot_at_start': lambda: info.ost,
        'snapshot_number': lambda: info.snapshot_number,
    }
    for name, read in readers.items():
        try:
            value = read()
            if isinstance(value, Enum):
                value = value.name
            if not isinstance(value, (bool, int, str)):
                raise TypeError('Unsupported native information value')
            result['fields'][name] = {'available': True, 'value': value}
        except Exception as exc:
            result['fields'][name] = {
                'available': False, 'error_type': type(exc).__name__}
    return result
