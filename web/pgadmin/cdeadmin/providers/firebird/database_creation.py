"""Create through an owned Firebird configuration, including worker DPB.

The installed driver's creation helper omits parallel_workers. Keep this
adapter local to the provider; never patch driver globals or change defaults.
"""

import re

from pgadmin.cdeadmin.sdk.relational import RelationalClientError


CONFIG_FIELDS = (
    'trusted_auth', 'timeout', 'cache_size', 'no_linger', 'utf8filename',
    'dummy_packet_interval', 'config', 'set_bind', 'decfloat_round',
    'decfloat_traps', 'parallel_workers', 'db_cache_size', 'forced_writes',
    'page_size', 'reserve_space', 'sweep_interval', 'db_sql_dialect',
    'db_charset',
)


def create_database(module, core, *, database, user=None, password=None,
                    role=None, no_gc=None, no_db_triggers=None,
                    dbkey_scope=None, crypt_callback=None, charset=None,
                    overwrite=False, auth_plugin_list=None,
                    session_time_zone=None):
    """Return the initial creation attachment; never overwrite or reconnect."""
    if overwrite is not False:
        raise RelationalClientError('Firebird creation cannot overwrite')
    if not isinstance(database, str) or not re.fullmatch(
            r'cde_database_[0-9a-f]{24}', database):
        raise RelationalClientError(
            'Firebird creation configuration is invalid')
    config = module.driver_config.get_database(database)
    if config is None:
        raise RelationalClientError(
            'Firebird creation configuration is missing')
    server = module.driver_config.get_server(config.server.value)
    dsn = config.dsn.value
    if (server is None or server.host.value is not None or
            server.port.value is not None or config.database.value is not None
            or not isinstance(dsn, str) or not dsn or '\x00' in dsn):
        raise RelationalClientError('Firebird creation target is invalid')
    workers = config.parallel_workers.value
    if workers is not None and (
            type(workers) is not int or not 0 <= workers <= 32767):
        raise RelationalClientError('Firebird parallel workers are invalid')

    def resolved(value, name, fallback=None):
        if value is not None:
            return value
        value = getattr(config, name).value
        return fallback if value is None else value

    charset = resolved(charset, 'charset')
    if charset:
        charset = charset.upper()
    options = {name: getattr(config, name).value for name in CONFIG_FIELDS}
    options.update(
        user=resolved(user, 'user', server.user.value),
        password=resolved(password, 'password', server.password.value),
        role=resolved(role, 'role'), charset=charset,
        auth_plugin_list=resolved(auth_plugin_list, 'auth_plugin_list'),
        session_time_zone=resolved(session_time_zone, 'session_time_zone'),
        no_gc=no_gc, no_db_triggers=no_db_triggers, dbkey_scope=dbkey_scope,
        sql_dialect=config.db_sql_dialect.value, overwrite=False,
    )
    buffer = core.DPB(**options).get_buffer(for_create=True)
    attachment = connection = None
    try:
        with module.get_api().master.get_dispatcher() as dispatcher:
            if crypt_callback is not None:
                dispatcher.set_dbcrypt_callback(crypt_callback)
            attachment = dispatcher.create_database(
                dsn, buffer, 'utf-8' if config.utf8filename.value
                else core.FS_ENCODING)
            connection = core.Connection(
                attachment, dsn, buffer, config.sql_dialect.value, charset)
        for callback in core.get_callbacks(
                core.ConnectionHook.ATTACHED, connection):
            callback(connection)
        return connection
    except BaseException:
        # No connection has been published to the caller. A detach-retention
        # hook cannot take ownership of a failed creation. Close internals and
        # detach explicitly, but never DROP a database after a callback error.
        try:
            if connection is not None:
                try:
                    connection._close()
                finally:
                    connection._close_internals()
        finally:
            try:
                if attachment is not None:
                    attachment.detach()
            finally:
                if connection is not None:
                    connection._att = None
        raise
