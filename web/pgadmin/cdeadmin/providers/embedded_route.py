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
        result['read_only'] = bool(route['read_only'])
    config = route.get('config')
    if config is not None:
        if not isinstance(config, Mapping):
            raise RelationalClientError('DuckDB config must be an object')
        result['config'] = dict(config)
    return result


def sqlite_arguments(route):
    """Return only approved SQLite connector arguments."""
    if route.get('uri'):
        raise RelationalClientError(
            'SQLite URI routes are unavailable at the filesystem boundary'
        )
    timeout = route.get('timeout', 5.0)
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or (
        timeout <= 0
    ):
        raise RelationalClientError('SQLite timeout must be positive')
    return {
        'database': contained_database(route),
        'timeout': float(timeout),
        'uri': False,
        'check_same_thread': False,
    }
