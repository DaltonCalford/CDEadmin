##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Resolve an isolated CDEadmin runtime profile without filesystem writes."""

from __future__ import annotations

import os
from pathlib import Path, PurePath, PureWindowsPath

from .identity import load_identity, validate_identity


SUPPORTED_PROFILES = frozenset({
    'linux-desktop', 'linux-server', 'macos', 'windows', 'container',
})


def _join(base, *parts, windows=False):
    path_type = PureWindowsPath if windows else PurePath
    return str(path_type(base).joinpath(*parts))


def _resolved_directories(profile, environment, home):
    if profile == 'linux-desktop':
        return {
            'configuration': _join(
                environment.get('XDG_CONFIG_HOME', _join(home, '.config')),
                'cdeadmin',
            ),
            'data': _join(
                environment.get(
                    'XDG_DATA_HOME', _join(home, '.local', 'share')
                ),
                'cdeadmin',
            ),
            'cache': _join(
                environment.get('XDG_CACHE_HOME', _join(home, '.cache')),
                'cdeadmin',
            ),
            'logs': _join(
                environment.get(
                    'XDG_STATE_HOME', _join(home, '.local', 'state')
                ),
                'cdeadmin', 'log',
            ),
            'runtime': _join(
                environment.get('XDG_RUNTIME_DIR', '/tmp'), 'cdeadmin'
            ),
        }
    if profile == 'linux-server':
        return {
            'configuration': '/etc/cdeadmin',
            'data': '/var/lib/cdeadmin',
            'cache': '/var/cache/cdeadmin',
            'logs': '/var/log/cdeadmin',
            'runtime': '/run/cdeadmin',
        }
    if profile == 'macos':
        return {
            'configuration': _join(
                home, 'Library', 'Application Support', 'CDEadmin', 'config'
            ),
            'data': _join(
                home, 'Library', 'Application Support', 'CDEadmin'
            ),
            'cache': _join(home, 'Library', 'Caches', 'CDEadmin'),
            'logs': _join(home, 'Library', 'Logs', 'CDEadmin'),
            'runtime': _join(
                environment.get('TMPDIR', '/tmp'), 'cdeadmin'
            ),
        }
    if profile == 'windows':
        roaming = environment.get(
            'APPDATA', _join(home, 'AppData', 'Roaming', windows=True)
        )
        local = environment.get(
            'LOCALAPPDATA', _join(home, 'AppData', 'Local', windows=True)
        )
        return {
            'configuration': _join(roaming, 'CDEadmin', windows=True),
            'data': _join(roaming, 'CDEadmin', windows=True),
            'cache': _join(local, 'CDEadmin', 'Cache', windows=True),
            'logs': _join(local, 'CDEadmin', 'Logs', windows=True),
            'runtime': _join(local, 'CDEadmin', 'Runtime', windows=True),
        }
    if profile == 'container':
        return {
            'configuration': '/cdeadmin/config',
            'data': '/var/lib/cdeadmin',
            'cache': '/var/cache/cdeadmin',
            'logs': '/var/log/cdeadmin',
            'runtime': '/run/cdeadmin',
        }
    raise ValueError(
        f'unsupported CDEadmin runtime profile {profile!r}; '
        f'expected one of {sorted(SUPPORTED_PROFILES)!r}'
    )


def isolated_profile(profile, environment=None, home=None, identity=None):
    """Return pgAdmin-compatible settings using only CDEadmin namespaces."""
    identity = identity or load_identity()
    validate_identity(identity)
    environment = dict(os.environ if environment is None else environment)
    home = str(home or environment.get('HOME') or Path.home())
    directories = _resolved_directories(profile, environment, home)
    windows = profile == 'windows'
    data = directories['data']
    logs = directories['logs']
    common = identity['namespaces']['cdeadmin']['common']
    coexistence = identity['coexistence']
    return {
        'APP_NAME': identity['product']['display_name'],
        'APP_SHORT_NAME': identity['product']['short_name'],
        'APP_PATH': identity['product']['short_name'],
        'DATA_DIR': data,
        'CONFIG_DIR': directories['configuration'],
        'CACHE_DIR': directories['cache'],
        'LOG_FILE': _join(logs, 'cdeadmin.log', windows=windows),
        'RUNTIME_DIR': directories['runtime'],
        'SQLITE_PATH': _join(data, common['database'], windows=windows),
        'CDEADMIN_OPERATION_STORE_PATH': _join(
            data, 'cdeadmin-operations.json', windows=windows
        ),
        'SESSION_DB_PATH': _join(data, 'sessions', windows=windows),
        'STORAGE_DIR': _join(data, 'storage', windows=windows),
        'SESSION_COOKIE_NAME': common['cookie'],
        'ENVIRONMENT_PREFIX': common['environment'],
        'DESKTOP_STORE_NAME': common['desktop_store'],
        'KEYRING_SERVICE': common['keyring'],
        'DEFAULT_SERVER_PORT': coexistence['default_server_port'],
        'UPGRADE_CHECK_ENABLED': False,
        'UPGRADE_CHECK_KEY': identity['update_channel']['channel_id'],
        'UPGRADE_CHECK_URL': None,
    }
