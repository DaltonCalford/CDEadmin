#!/usr/bin/env python3
"""Observe native Firebird attachment worker settings on owned servers.

This qualifies attachment settings, not the number of workers a task starts.
"""

import argparse
import json
import os
import re
import secrets
import time
import uuid
from importlib.metadata import version
from pathlib import Path

if __package__:
    from .cdeadmin_firebird_logical_volumes_gate import (
        docker, published_port, remove_owned, OWNER, _configure_client_library,
    )
else:
    from cdeadmin_firebird_logical_volumes_gate import (
        docker, published_port, remove_owned, OWNER, _configure_client_library,
    )

from pgadmin.cdeadmin.providers.firebird.error_diagnostics import status_codes


POLICIES = ((1, 2), (2, 4))
REQUESTS = (None, 0, 1, 2, 4, 5, 32767)
OBSERVE = ("SELECT RDB$GET_CONTEXT('SYSTEM', 'PARALLEL_WORKERS') "
           "FROM RDB$DATABASE")


def run(image):
    import firebird.driver as native
    _configure_client_library(native)
    result = {'complete': False, 'checks': [], 'failures': [],
              'removed_policies': [], 'provider_forms_qualified': False,
              'driver_version': version('firebird-driver'),
              'actual_task_worker_counts_qualified': False}

    def failure(case, error):
        result['failures'].append({
            'case': case, 'error_type': type(error).__name__,
            'native_status_codes': list(status_codes(error))})

    for default, maximum in POLICIES:
        container = None
        password = secrets.token_urlsafe(24)
        path = '/var/lib/firebird/data/owned_parallel.fdb'
        policy = f'default-{default}-maximum-{maximum}'
        phase = policy + '-create-owned-server'
        try:
            container = docker(
                'run', '--detach', '--name',
                'cdeadmin-attachment-workers-' + uuid.uuid4().hex[:16],
                '--label', 'cdeadmin-owned-gate=' + OWNER,
                '--memory', '512m', '--memory-swap', '512m',
                '--publish', '127.0.0.1::3050',
                '--env', 'FIREBIRD_ROOT_PASSWORD',
                '--env', 'FIREBIRD_DATABASE',
                '--env', 'FIREBIRD_CONF_ParallelWorkers',
                '--env', 'FIREBIRD_CONF_MaxParallelWorkers', image,
                env=dict(os.environ, FIREBIRD_ROOT_PASSWORD=password,
                         FIREBIRD_DATABASE=path,
                         FIREBIRD_CONF_ParallelWorkers=str(default),
                         FIREBIRD_CONF_MaxParallelWorkers=str(maximum))
            ).decode().strip()
            if not re.fullmatch('[0-9a-f]{64}', container):
                raise ValueError('Owned container identity is invalid')
            port = published_port(container)
            dsn = f'127.0.0.1/{port}:{path}'

            def connect(request=None, operation='attach'):
                create = operation != 'attach'
                name = 'owned_parallel_' + uuid.uuid4().hex
                config = native.driver_config.register_database(name)
                config.dsn.value = (dsn if not create else
                                    f'127.0.0.1/{port}:/var/lib/firebird/data/'
                                    + name + '.fdb')
                config.user.value = None
                config.password.value = None
                config.parallel_workers.value = request
                if operation == 'native_create':
                    dpb = native.core.DPB(
                        user='SYSDBA', password=password,
                        parallel_workers=request, sql_dialect=3,
                        db_sql_dialect=3).get_buffer(for_create=True)
                    dispatcher = (
                        native.core.a.get_api().master.get_dispatcher())
                    with dispatcher as api:
                        attachment = api.create_database(
                            config.dsn.value, dpb, 'utf-8')
                    return native.core.Connection(
                        attachment, config.dsn.value, dpb, 3, None)
                return (native.create_database if create else native.connect)(
                    name, user='SYSDBA', password=password)

            phase = policy + '-readiness'
            deadline = time.monotonic() + 45
            while True:
                try:
                    with connect() as handle:
                        with handle.cursor() as cursor:
                            cursor.execute(
                                "SELECT RDB$GET_CONTEXT('SYSTEM', "
                                "'ENGINE_VERSION') FROM RDB$DATABASE")
                            version = cursor.fetchone()[0]
                    assert version == '5.0.4'
                    break
                except native.Error:
                    if time.monotonic() >= deadline:
                        raise
                    time.sleep(0.25)
            for operation in ('attach', 'driver_create', 'native_create'):
                for requested in REQUESTS:
                    phase = f'{policy}-{operation}-request-{requested}'
                    try:
                        observed = []
                        # Driver 1.10.11's create_database omits the configured
                        # worker setting from its DPB. Direct native creation
                        # isolates the engine's actual support from that gap.
                        expected = (default if requested is None or
                                    operation == 'driver_create' else
                                    min(requested, maximum))
                        with connect(requested, operation) as handle:
                            for stage in ('initial', 'rollback', 'reset'):
                                if stage == 'rollback':
                                    handle.rollback()
                                if stage == 'reset':
                                    handle.rollback()
                                    with handle.cursor() as cursor:
                                        cursor.execute('ALTER SESSION RESET')
                                with handle.cursor() as cursor:
                                    cursor.execute(OBSERVE)
                                    observed.append(int(cursor.fetchone()[0]))
                            assert observed == [expected] * 3
                        result['checks'].append({
                            'case': phase, 'requested': requested,
                            'policy_default': default, 'policy_max': maximum,
                            'operation': operation, 'observed': observed})
                    except Exception as error:
                        failure(phase, error)
        except Exception as error:
            failure(phase, error)
        finally:
            if container is not None:
                try:
                    remove_owned(container)
                    result['removed_policies'].append(policy)
                except Exception as error:
                    failure(policy + '-cleanup', error)
    result['complete'] = (
        len(result['checks']) == len(POLICIES) * len(REQUESTS) * 3 and
        not result['failures'] and len(result['removed_policies']) == 2)
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
