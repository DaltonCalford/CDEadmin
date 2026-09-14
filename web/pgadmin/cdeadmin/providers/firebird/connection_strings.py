"""Firebird TCP addresses, distinct from server-side database filenames.

Authority: Firebird 5.0.4 doc/README.connection_strings and doc/README.IPv6.
No DNS lookup or client-side filesystem interpretation is used here.
"""

import ipaddress
import re

from pgadmin.cdeadmin.sdk.relational import RelationalClientError


def server_host(host):
    """Bracket IPv6 literals; reject a connection string in the host field."""
    if host is None or host == '':
        return None
    if not isinstance(host, str) or any(
            character.isspace() or character in '/\\\x00'
            for character in host):
        raise RelationalClientError('Firebird server host is invalid')
    bracketed = host.startswith('[') and host.endswith(']')
    plain = host[1:-1] if bracketed else host
    if not plain or '[' in plain or ']' in plain:
        raise RelationalClientError('Firebird server host is invalid')
    if ':' in plain:
        try:
            ipaddress.IPv6Address(plain)
        except ValueError:
            raise RelationalClientError(
                'Firebird server host must not contain a port or database'
            ) from None
        return '[' + plain + ']'
    return host


def _port(port):
    if port is None:
        return None
    if isinstance(port, bool) or not isinstance(port, (int, str)):
        raise RelationalClientError('Firebird server port is invalid')
    value = str(port)
    if value.isascii() and value.isdecimal():
        if 1 <= int(value) <= 65535:
            return str(int(value))
    elif re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]*', value):
        # Native Firebird also accepts an operating-system service name.
        return value
    raise RelationalClientError('Firebird server port is invalid')


def _host_identity(host):
    plain = server_host(host).strip('[]')
    try:
        return str(ipaddress.ip_address(plain))
    except ValueError:
        return plain.casefold()


def target_path(database, host=None, port=None):
    """Keep a bare server filename/alias, accepting same-endpoint legacy DSNs.

    A Windows absolute drive path or POSIX filename containing a colon is
    still a filename. An embedded remote address cannot override the selected
    server. Hostless legacy API callers retain their native DSN behavior.
    """
    if not isinstance(database, str) or not database or any(
            character in database for character in ('\x00', '\r', '\n')):
        raise RelationalClientError('Firebird database filename is invalid')
    if not server_host(host):
        return database
    if ':' not in database or database.startswith(('/', '\\')) or (
            re.match(r'^[A-Za-z]:[\\/]', database)):
        return database
    match = re.fullmatch(
        r'(\[[^\]]+\]|[^/:\\]+)(?:/([^:]+))?:(.+)', database)
    if match and '://' not in database:
        address, endpoint_port, path = match.groups()
        if _host_identity(address) == _host_identity(host) and (
                (_port(endpoint_port) or '3050') == (_port(port) or '3050')):
            # Reject nested DSNs, but preserve ordinary drive/absolute paths.
            if ':' not in path or path.startswith(('/', '\\')) or (
                    re.match(r'^[A-Za-z]:[\\/]', path)):
                return path
    raise RelationalClientError(
        'Firebird database must be a filename or alias on the selected '
        'server; configure the host, port and protocol in the server form')


def database_dsn(database, host=None, port=None, protocol=None):
    host = server_host(host)
    port = _port(port)
    path = target_path(database, host, port)
    if protocol is not None:
        if protocol not in {'INET', 'INET4', 'INET6'}:
            raise RelationalClientError('Firebird TCP protocol is invalid')
        address = host or ''
        if host and port:
            address += ':' + port
        return protocol.lower() + '://' + (
            address + '/' if address else '') + path
    address = host
    if host and port:
        address += '/' + port
    return address + ':' + path if address else path


def service_dsn(host=None, port=None, protocol=None):
    return database_dsn('service_mgr', host, port, protocol)
