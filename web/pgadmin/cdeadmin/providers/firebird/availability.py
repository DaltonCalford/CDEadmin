"""Firebird 5 availability task bounds and native-outcome guidance."""

from collections.abc import Mapping

from pgadmin.cdeadmin.sdk.relational import RelationalClientError
from .service_connection import effective_service_role


# alice.cpp validates 0..32767; jrd.cpp stores the DPB delay as SSHORT.
MAX_SHUTDOWN_SECONDS = 32767
OPERATIONS = frozenset({'shutdown_database', 'bring_online'})
WARNING = (
    'Firebird can change availability before returning a later access error. '
    'After any error, independently inspect the database state; do not replay '
    'the task. Forced shutdown terminates affected attachments and their '
    'pending work. For a non-owner using the gfix service, the active role '
    'needs CHANGE_SHUTDOWN_MODE, ACCESS_SHUTDOWN_DATABASE, USE_GFIX_UTILITY '
    'and IGNORE_DB_TRIGGERS for the qualified maintenance-mode workflow. '
    'Firebird remains the authority for each mode, privilege and outcome. '
    'In Firebird 5.0.4 shared-cache operation, the native grace period can '
    'expire earlier than the requested number of seconds. CDEadmin passes '
    'the requested timeout unchanged and does not retry. SINGLE and FULL '
    'shutdown are not available while the database is in physical-backup '
    'mode; end the backup explicitly only when it is safe to do so.')


def validate(operation, options):
    if not isinstance(operation, str) or operation not in OPERATIONS:
        raise RelationalClientError(
            'Unknown Firebird availability operation')
    if not isinstance(options, Mapping):
        raise RelationalClientError(
            'Firebird availability options are invalid')
    modes = ({'MULTI', 'SINGLE', 'FULL'} if operation == 'shutdown_database'
             else {'NORMAL', 'MULTI', 'SINGLE'})
    mode = options.get('mode', 'FULL' if operation == 'shutdown_database'
                       else 'NORMAL')
    if not isinstance(mode, str) or mode not in modes:
        raise RelationalClientError('Invalid Firebird availability mode')
    if operation == 'shutdown_database':
        method = options.get('method', 'DENY_ATTACHMENTS')
        if not isinstance(method, str) or method not in {
                'FORCED', 'DENY_ATTACHMENTS', 'DENY_TRANSACTIONS'}:
            raise RelationalClientError('Invalid Firebird shutdown method')
        timeout = options.get('shutdown_timeout', 0)
        if (type(timeout) is not int or
                not 0 <= timeout <= MAX_SHUTDOWN_SECONDS):
            raise RelationalClientError(
                'Firebird shutdown timeout must be an integer from 0 '
                'through 32767 seconds')


def selection(operation, database, options, default_role=None):
    """Public, credential-free description of the exact native task."""
    validate(operation, options)
    if not isinstance(database, str) or not database.strip():
        raise RelationalClientError(
            'Firebird availability requires an explicit database target')
    result = {
        'database': database,
        'operation': operation,
        'mode': options.get('mode', 'FULL' if operation == 'shutdown_database'
                            else 'NORMAL'),
        'sql_role': effective_service_role(options.get('role'), default_role),
    }
    if operation == 'shutdown_database':
        result.update(
            method=options.get('method', 'DENY_ATTACHMENTS'),
            timeout_seconds=options.get('shutdown_timeout', 0))
    return result
