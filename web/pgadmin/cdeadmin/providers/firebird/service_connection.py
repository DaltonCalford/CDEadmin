"""Provider-owned Unicode Firebird Services API attachments.

Firebird 5's dispatcher uses isc_spb_utf8_filename to distinguish UTF-8
attachment/start text from client-local encoding (why.cpp, IntlSpb). The
installed driver exposes the native builder and Server, but its connect_server
does not emit that marker. Do not patch driver globals or its installation.
"""

from pgadmin.cdeadmin.sdk.relational import RelationalClientError


def validate_service_role(role):
    """Reject roles that native service argv construction cannot preserve."""
    if role is not None:
        if not isinstance(role, str) or any(ord(char) <= 32 for char in role):
            # Service::start concatenates this attachment value into utility
            # switches; parseSwitches does not recognize shell/SQL quoting.
            # Do not let a role become another utility argument or silently
            # change a delimited role name. SQL database roles are unaffected.
            raise RelationalClientError(
                'Firebird service role transport cannot safely preserve '
                'whitespace or control characters in a role name')
    return role


def effective_service_role(task_role, default_role=None):
    """Resolve a task override without mutating the saved endpoint role."""
    if task_role is not None and (
            not isinstance(task_role, str) or '\x00' in task_role):
        raise RelationalClientError('Firebird service role is invalid')
    validate_service_role(task_role)
    role = task_role or default_role
    if role is not None and (not isinstance(role, str) or '\x00' in role):
        raise RelationalClientError('Firebird service role is invalid')
    validate_service_role(role)
    return role or None


def connect_service(module, core, *, server, user=None, password=None,
                    expected_db=None, role=None, crypt_callback=None):
    validate_service_role(role)
    config = module.driver_config.get_server(server)
    if config is None:
        raise RelationalClientError(
            'Firebird service configuration is missing')
    host = config.host.value
    if not isinstance(host, str) or not host.endswith('service_mgr'):
        raise RelationalClientError('Firebird service address is invalid')
    api = module.get_api()
    # Always use UTF-8/strict for native service text, independently of a
    # database SQL attachment's character set. Secrets remain leased only.
    buffer = core.SPB_ATTACH(
        user=user if user is not None else config.user.value,
        password=password, trusted_auth=config.trusted_auth.value,
        config=config.config.value,
        auth_plugin_list=config.auth_plugin_list.value,
        expected_db=expected_db, role=role,
        encoding='utf-8', errors='strict',
    ).get_buffer()
    with api.util.get_xpb_builder(core.XpbKind.SPB_ATTACH, buffer) as builder:
        builder.insert_tag(core.SPBItem.UTF8_FILENAME)
        buffer = builder.get_buffer()
    # Allocate the Python owner before obtaining the native attachment.
    # Publish driver ATTACHED hooks only after the client records ownership.
    connection = core.Server(None, buffer, host, 'utf-8', 'strict')
    with api.master.get_dispatcher() as dispatcher:
        if crypt_callback is not None:
            dispatcher.set_dbcrypt_callback(crypt_callback)
        connection._svc = dispatcher.attach_service_manager(host, buffer)
    return connection


def notify_attached(core, connection):
    for callback in core.get_callbacks(core.ServerHook.ATTACHED, connection):
        callback(connection)
