##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Filesystem containment shared by embedded database providers."""

from __future__ import annotations

import os
import re
import urllib.parse
from collections.abc import Mapping

from pgadmin.cdeadmin.sdk import RelationalClientError


def contained_database(route):
    """Resolve an embedded database without permitting path escape.

    In-memory databases are deliberately opt-in. File databases require an
    absolute approved root and are resolved through existing symlinks before
    they reach the native connector.
    """
    database = route.get('database')
    if database == ':memory:':
        if route.get('allow_memory') is not True:
            raise RelationalClientError(
                'embedded in-memory database is not approved by the route'
            )
        return database
    if not isinstance(database, str) or not database.strip():
        raise RelationalClientError(
            'embedded route requires a database file'
        )
    database = database.strip()
    if not os.path.isabs(database):
        raise RelationalClientError(
            'embedded database file must be absolute'
        )
    root = route.get('filesystem_root')
    if not isinstance(root, str) or not root.strip() or not os.path.isabs(
        root.strip()
    ):
        raise RelationalClientError(
            'embedded route requires an absolute filesystem root'
        )
    root_path = os.path.realpath(root.strip())
    database_path = os.path.realpath(database)
    try:
        contained = os.path.commonpath((root_path, database_path)) == root_path
    except ValueError:
        contained = False
    if not contained:
        raise RelationalClientError(
            'embedded database file escapes the approved filesystem root'
        )
    return database_path


def duckdb_arguments(route):
    """Return only approved DuckDB connector arguments."""
    result = {'database': contained_database(route)}
    if 'read_only' in route:
        if not isinstance(route['read_only'], bool):
            raise RelationalClientError(
                'DuckDB read_only must be true or false'
            )
        result['read_only'] = route['read_only']
    config = route.get('config')
    if config is not None:
        if not isinstance(config, Mapping):
            raise RelationalClientError('DuckDB config must be an object')
        if not all(
            isinstance(key, str) and key.strip() and
            isinstance(value, (str, int, float, bool)) and
            value is not None
            for key, value in config.items()
        ):
            raise RelationalClientError(
                'DuckDB config must contain named scalar options'
            )
        result['config'] = dict(config)
    return result


def sqlite_arguments(route):
    """Return only approved SQLite connector arguments."""
    if route.get('uri'):
        raise RelationalClientError(
            'raw SQLite URI routes are unavailable at the filesystem boundary'
        )
    timeout = route.get('timeout', 5.0)
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or (
        timeout < 0
    ):
        raise RelationalClientError('SQLite timeout must not be negative')
    database = contained_database(route)
    result = {
        'database': database,
        'timeout': float(timeout),
        'check_same_thread': False,
    }
    mode = route.get('uri_mode', 'default')
    cache = route.get('uri_cache', 'default')
    if mode not in {'default', 'ro', 'rw', 'rwc'}:
        raise RelationalClientError('SQLite file open mode is invalid')
    if cache not in {'default', 'shared', 'private'}:
        raise RelationalClientError('SQLite shared-cache mode is invalid')
    query = {}
    if mode != 'default':
        query['mode'] = mode
    if cache != 'default':
        query['cache'] = cache
    for route_name, uri_name in (
        ('uri_immutable', 'immutable'), ('uri_nolock', 'nolock')
    ):
        value = route.get(route_name, False)
        if not isinstance(value, bool):
            raise RelationalClientError(
                f'SQLite {route_name} must be true or false'
            )
        if value:
            query[uri_name] = '1'
    vfs = route.get('uri_vfs')
    if vfs is not None:
        if not isinstance(vfs, str) or not re.fullmatch(
            r'[A-Za-z0-9_.-]{1,128}', vfs
        ):
            raise RelationalClientError('SQLite VFS name is invalid')
        query['vfs'] = vfs
    if query:
        result['database'] = 'file:' + urllib.parse.quote(database, safe='/')
        result['database'] += '?' + urllib.parse.urlencode(query)
        result['uri'] = True
    else:
        result['uri'] = False
    detection = route.get('detect_types', 'none')
    detection_values = {'none': 0, 'decltypes': 1, 'colnames': 2, 'both': 3}
    if detection not in detection_values:
        raise RelationalClientError('SQLite type detection mode is invalid')
    result['detect_types'] = detection_values[detection]
    isolation = route.get('isolation_level', 'legacy')
    if isolation not in {
        'legacy', 'deferred', 'immediate', 'exclusive', 'autocommit'
    }:
        raise RelationalClientError('SQLite isolation level is invalid')
    if isolation != 'legacy':
        result['isolation_level'] = (
            None if isolation == 'autocommit' else isolation.upper()
        )
    cached = route.get('cached_statements', 128)
    if isinstance(cached, bool) or not isinstance(cached, int) or (
        not 0 <= cached <= 100000
    ):
        raise RelationalClientError(
            'SQLite cached statement count is invalid'
        )
    result['cached_statements'] = cached
    return result
