"""Observed database dialect for generated identifiers, never SQL rewriting."""
from contextlib import contextmanager
from contextvars import ContextVar
import re

from pgadmin.cdeadmin.sdk.relational import RelationalClientError

_DATABASE_DIALECT = ContextVar(
    'firebird_generated_database_dialect', default=3)


def observed_dialects(connection):
    try:
        database = connection.info.sql_dialect
        client = connection.sql_dialect
    except Exception as exc:
        raise RelationalClientError(
            'Firebird DDL dialect observation failed (' +
            type(exc).__name__ + ')') from None
    if (type(database) is not int or database not in (1, 3) or
            type(client) is not int or client != 3):
        raise RelationalClientError(
            'Firebird generated administration requires an observed '
            'database dialect 1 or 3 and client dialect 3')
    return {'database_sql_dialect': database, 'client_sql_dialect': client}


@contextmanager
def generated_dialect(database):
    if type(database) is not int or database not in (1, 3):
        raise RelationalClientError('Invalid generated Firebird DDL dialect')
    token = _DATABASE_DIALECT.set(database)
    try:
        yield
    finally:
        _DATABASE_DIALECT.reset(token)


def identifier_sql(value, database=None):
    dialect = _DATABASE_DIALECT.get() if database is None else database
    if dialect == 1:
        if not re.fullmatch(r'[A-Z_][A-Z0-9_$]{0,62}', value, re.ASCII):
            raise RelationalClientError(
                'Dialect-1 generated identifiers require an uppercase '
                'unquoted name; no case conversion has been performed')
        return value
    return '"' + value.replace('"', '""') + '"'


def verify_binding(connection, expected):
    if (not isinstance(expected, dict) or
            set(expected) != {'database_sql_dialect', 'client_sql_dialect'} or
            type(expected['database_sql_dialect']) is not int or
            expected['database_sql_dialect'] not in (1, 3) or
            type(expected['client_sql_dialect']) is not int or
            expected['client_sql_dialect'] != 3 or
            observed_dialects(connection) != expected):
        raise RelationalClientError(
            'Firebird DDL dialect changed or its binding is invalid; '
            'create a new preview. Nothing has been executed')
