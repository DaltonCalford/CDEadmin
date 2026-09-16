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
from importlib.metadata import version as distribution_version
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


def creation_ownership_case(native, port, password, *, fail_hook):
    """Observe real driver hooks, pending DDL and attachment release."""
    from firebird.base.hooks import hook_manager
    from pgadmin.cdeadmin.providers.firebird.provider import (
        _database_create_arguments, create_owned_database,
    )
    target = (f'127.0.0.1/{port}:/var/lib/firebird/data/owned_hook_' +
              uuid.uuid4().hex + '.fdb')
    args = _database_create_arguments(
        {'host': '127.0.0.1', 'port': port}, target, {}, native)
    seen = []
    failure = RuntimeError('Owned attachment hook failure')

    def attached(connection):
        seen.append(connection)
        connection.execute_immediate('CREATE TABLE OWNED_HOOK (ID INTEGER)')
        if fail_hook:
            raise failure

    event = native.core.ConnectionHook.ATTACHED
    owner = native.core.Connection
    hook_manager.add_hook(event, owner, attached)
    handle = None
    try:
        try:
            handle = create_owned_database(
                native, native.core, **args, user='SYSDBA', password=password)
        except RuntimeError as error:
            assert fail_hook and error is failure
        else:
            assert not fail_hook
            assert handle.main_transaction.is_active()
            handle.commit()
    finally:
        hook_manager.remove_hook(event, owner, attached)
        if handle is not None:
            handle.close()
    assert len(seen) == 1 and seen[0].is_closed()
    with native.connect(target, user='SYSDBA', password=password) as reopened:
        with reopened.cursor() as cursor:
            cursor.execute('SELECT COUNT(*) FROM MON$ATTACHMENTS '
                           'WHERE MON$SYSTEM_FLAG = 0')
            assert cursor.fetchone()[0] == 1
            cursor.execute('SELECT COUNT(*) FROM RDB$RELATIONS '
                           "WHERE RDB$RELATION_NAME = 'OWNED_HOOK'")
            assert cursor.fetchone()[0] == (0 if fail_hook else 1)
        reopened.rollback()
        reopened.drop_database()
    return {'hook_failed': fail_hook, 'hook_called_once': True,
            'initial_attachment_closed': True, 'reopened_attachments': 1,
            'pending_ddl_rolled_back' if fail_hook else
            'callback_ddl_committed_by_caller': True,
            'database_preserved_after_callback': True,
            'owned_database_dropped': True}


def run(image, *, provider_creation=False, server_mode='Super',
        creation_ownership=False):
    if server_mode not in {'Super', 'SuperClassic', 'Classic'}:
        raise ValueError('Invalid Firebird server mode')
    if creation_ownership and not provider_creation:
        raise ValueError('Creation ownership requires provider creation')
    import firebird.driver as native
    _configure_client_library(native)
    result = {'complete': False, 'checks': [], 'failures': [],
              'removed_policies': [], 'provider_forms_qualified': False,
              'driver_version': distribution_version('firebird-driver'),
              'actual_task_worker_counts_qualified': False,
              'server_mode': server_mode, 'observed_server_modes': [],
              'provider_creation': provider_creation,
              'creation_ownership': creation_ownership}
    operations = (('provider_create',) if provider_creation else
                  ('attach', 'driver_create', 'native_create'))

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
                '--env', 'FIREBIRD_CONF_MaxParallelWorkers',
                '--env', 'FIREBIRD_CONF_ServerMode', image,
                env=dict(os.environ, FIREBIRD_ROOT_PASSWORD=password,
                         FIREBIRD_DATABASE=path,
                         FIREBIRD_CONF_ParallelWorkers=str(default),
                         FIREBIRD_CONF_MaxParallelWorkers=str(maximum),
                         FIREBIRD_CONF_ServerMode=server_mode)
            ).decode().strip()
            if not re.fullmatch('[0-9a-f]{64}', container):
                raise ValueError('Owned container identity is invalid')
            port = published_port(container)
            dsn = f'127.0.0.1/{port}:{path}'

            def connect(request=None, operation='attach'):
                create = operation != 'attach'
                if operation == 'provider_create':
                    from pgadmin.cdeadmin.providers.firebird.provider import (
                        _database_create_arguments, create_owned_database,
                    )
                    target = (f'127.0.0.1/{port}:/var/lib/firebird/data/'
                              + 'owned_' + uuid.uuid4().hex + '.fdb')
                    arguments = _database_create_arguments(
                        {'host': '127.0.0.1', 'port': port}, target, {},
                        native)
                    private = native.driver_config.get_database(
                        arguments['database'])
                    private.parallel_workers.value = request
                    return create_owned_database(
                        native, native.core, **arguments,
                        user='SYSDBA', password=password)
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
                            cursor.execute(
                                'SELECT RDB$CONFIG_VALUE FROM RDB$CONFIG '
                                "WHERE RDB$CONFIG_NAME = 'ServerMode'")
                            actual_mode = cursor.fetchone()[0]
                    assert version == '5.0.4'
                    assert actual_mode.lower() == server_mode.lower()
                    result['observed_server_modes'].append(actual_mode)
                    break
                except native.Error:
                    if time.monotonic() >= deadline:
                        raise
                    time.sleep(0.25)
            for operation in operations:
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
            if creation_ownership:
                for fail_hook in (False, True):
                    phase = f'{policy}-creation-hook-fails-{fail_hook}'
                    try:
                        result['checks'].append(dict(
                            creation_ownership_case(
                                native, port, password, fail_hook=fail_hook),
                            case=phase))
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
    expected = len(POLICIES) * (len(REQUESTS) * len(operations) +
                                (2 if creation_ownership else 0))
    result['complete'] = (
        len(result['checks']) == expected and
        not result['failures'] and len(result['removed_policies']) == 2)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', default='firebirdsql/firebird:5.0.4')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--provider-creation', action='store_true')
    parser.add_argument('--creation-ownership', action='store_true')
    parser.add_argument('--server-mode', default='Super',
                        choices=('Super', 'SuperClassic', 'Classic'))
    options = parser.parse_args()
    if options.output.exists():
        parser.error('Use a new evidence file')
    result = run(options.image, provider_creation=options.provider_creation,
                 server_mode=options.server_mode,
                 creation_ownership=options.creation_ownership)
    options.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result))
    return 0 if result['complete'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
