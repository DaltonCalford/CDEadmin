#!/usr/bin/env python3
"""Verify minimal routes on an owned server with hostile driver defaults."""

import argparse
import json
import os
import re
import secrets
import time
import uuid
from pathlib import Path
from unittest.mock import patch

if __package__:
    from .cdeadmin_firebird_logical_volumes_gate import (
        docker, published_port, remove_owned, OWNER, _configure_client_library,
        _route_arguments,
    )
else:
    from cdeadmin_firebird_logical_volumes_gate import (
        docker, published_port, remove_owned, OWNER, _configure_client_library,
        _route_arguments,
    )

from pgadmin.cdeadmin.providers.firebird.error_diagnostics import status_codes


def run(image):
    import firebird.driver as native
    from firebird.driver.config import DriverConfig
    _configure_client_library(native)
    result = {'complete': False, 'cases': [], 'failures': [],
              'owned_container_removed': False}
    container = None
    password = secrets.token_urlsafe(24)
    path = '/var/lib/firebird/data/owned_isolation.fdb'
    phase = 'create-owned-server'

    def failure(error):
        result['failures'].append({
            'case': phase, 'error_type': type(error).__name__,
            'native_status_codes': list(status_codes(error))})

    try:
        container = docker(
            'run', '--detach', '--name',
            'cdeadmin-route-isolation-' + uuid.uuid4().hex[:16],
            '--label', 'cdeadmin-owned-gate=' + OWNER,
            '--memory', '512m', '--memory-swap', '512m',
            '--publish', '127.0.0.1::3050',
            '--env', 'FIREBIRD_ROOT_PASSWORD', '--env', 'FIREBIRD_DATABASE',
            image, env=dict(os.environ, FIREBIRD_ROOT_PASSWORD=password,
                            FIREBIRD_DATABASE=path)).decode().strip()
        if not re.fullmatch('[0-9a-f]{64}', container):
            raise ValueError('Invalid owned container identity')
        port = published_port(container)
        route = {'host': '127.0.0.1', 'port': port, 'database': path,
                 'user': 'SYSDBA'}
        phase = 'readiness'
        deadline = time.monotonic() + 45
        while True:
            try:
                with native.connect(**_route_arguments(route, native),
                                    password=password):
                    break
            except native.Error:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.25)
        for phase in ('dsn', 'host', 'named', 'session'):
            try:
                registry = DriverConfig('owned-isolation-' + phase)
                # Invalid local destinations cannot escape this test server.
                if phase == 'dsn':
                    registry.db_defaults.dsn.value = 'invalid-dsn'
                elif phase == 'host':
                    registry.server_defaults.host.value = '127.0.0.1'
                    registry.server_defaults.port.value = str(port)
                elif phase == 'named':
                    registry.register_database(
                        f'127.0.0.1/{port}:{path}').database.value = 'missing'
                else:
                    registry.db_defaults.role.value = 'UNSELECTED'
                    registry.db_defaults.session_time_zone.value = (
                        'Not/A/TimeZone')
                with patch.object(native, 'driver_config', registry), \
                        patch.object(native.core, 'driver_config', registry):
                    with native.connect(**_route_arguments(route, native),
                                        password=password) as handle:
                        with handle.cursor() as cursor:
                            cursor.execute(
                                "SELECT RDB$GET_CONTEXT('SYSTEM', "
                                "'ENGINE_VERSION'), CURRENT_USER, "
                                "CURRENT_ROLE FROM RDB$DATABASE")
                            version, user, role = cursor.fetchone()
                        assert version == '5.0.4'
                        assert user.strip() == 'SYSDBA'
                        assert role.strip() == 'NONE'
                        assert handle.info.name == path
                        handle.rollback()
                result['cases'].append({'case': phase, 'target_verified': True,
                                        'identity_verified': True})
            except Exception as error:
                failure(error)
    except Exception as error:
        failure(error)
    finally:
        if container is not None:
            try:
                remove_owned(container)
                result['owned_container_removed'] = True
            except Exception as error:
                phase = 'cleanup'
                failure(error)
    result['complete'] = (len(result['cases']) == 4 and
                          result['owned_container_removed'] and
                          not result['failures'])
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', default='firebirdsql/firebird:5.0.4')
    parser.add_argument('--output', type=Path, required=True)
    options = parser.parse_args()
    if options.output.exists():
        parser.error('Use a new evidence file')
    result = run(options.image)
    options.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result))
    return 0 if result['complete'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
