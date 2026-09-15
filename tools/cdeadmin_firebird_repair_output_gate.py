#!/usr/bin/env python3
"""Native repair diagnostics and recovery of an owned index-damaged copy."""

import argparse
import json
import os
import re
import secrets
import struct
import time
import traceback
import uuid
from pathlib import Path
from types import SimpleNamespace

if __package__:
    from .cdeadmin_firebird_external_functions_gate import browser_checks
    from .cdeadmin_firebird_service_security_gate import (
        read_container_file, write_container_file,
    )
    from .cdeadmin_firebird_logical_volumes_gate import (
        docker, published_port, remove_owned, OWNER, _route_arguments,
        _create_client, _configure_client_library, SecretLease,
    )
else:
    from cdeadmin_firebird_external_functions_gate import browser_checks
    from cdeadmin_firebird_service_security_gate import (
        read_container_file, write_container_file,
    )
    from cdeadmin_firebird_logical_volumes_gate import (
        docker, published_port, remove_owned, OWNER, _route_arguments,
        _create_client, _configure_client_library, SecretLease,
    )

from pgadmin.cdeadmin.providers.firebird.error_diagnostics import status_codes
from pgadmin.cdeadmin.sdk.relational import RelationalClientError


MAX_LINES = 2000
MAX_LINE_CHARACTERS = 8192
MAX_CHARACTERS = 262144


def collect_service_output(server):
    """Bound the probe transcript; do not mistake native errors for text.

    A finished service may have unread text. Drain before checking running;
    retain only bounded output and never replay service start.
    """
    lines = []
    characters = 0
    truncated = False
    while True:
        for line in server:
            text = str(line)
            remaining = MAX_CHARACTERS - characters
            if len(lines) < MAX_LINES and remaining > 0:
                kept = text[:min(MAX_LINE_CHARACTERS, remaining)]
                lines.append(kept)
                characters += len(kept)
                truncated = truncated or len(kept) != len(text)
            else:
                truncated = True
        if not server.is_running():
            return lines, truncated


def run(image, browser_options=None):
    import firebird.driver as native
    _configure_client_library(native)
    password = secrets.token_urlsafe(24)
    container = client = connection = None
    result = {'complete': False, 'checks': [], 'failures': [],
              'owned_container_removed': False,
              'credential_values_exported': False}
    database = '/var/lib/firebird/data/owned_output.fdb'
    phase = 'create-owned-container'
    try:
        container = docker(
            'run', '--detach', '--name',
            'cdeadmin-repair-output-' + uuid.uuid4().hex[:16],
            '--label', 'cdeadmin-owned-gate=' + OWNER,
            '--publish', '127.0.0.1::3050', '--env', 'FIREBIRD_ROOT_PASSWORD',
            '--env', 'FIREBIRD_DATABASE', image,
            env=dict(os.environ, FIREBIRD_ROOT_PASSWORD=password,
                     FIREBIRD_DATABASE=database)).decode().strip()
        if not re.fullmatch('[0-9a-f]{64}', container):
            raise ValueError('Owned container identity is invalid')
        route = {'host': '127.0.0.1', 'port': published_port(container),
                 'user': 'SYSDBA', 'timeout': 2, 'auth_plugin_list': 'Srp256',
                 'credential_reference_id': 'owned-output',
                 'principal_reference': 'owned-qa'}
        phase = 'readiness'
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            try:
                connection = native.connect(
                    password=password, **_route_arguments(
                        {**route, 'database': database}, native))
                break
            except native.Error:
                time.sleep(0.25)
        if connection is None:
            raise RuntimeError('Owned server did not become ready')
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT RDB$GET_CONTEXT('SYSTEM', 'ENGINE_VERSION') "
                'FROM RDB$DATABASE')
            result['engine_version'] = cursor.fetchone()[0]
        assert result['engine_version'] == '5.0.4'
        with connection.cursor() as cursor:
            cursor.execute('CREATE TABLE OUTPUT_MARKER '
                           '(ID INTEGER PRIMARY KEY, NOTE VARCHAR(32))')
            connection.commit()
            cursor.execute("INSERT INTO OUTPUT_MARKER VALUES (1, 'preserve')")
            connection.commit()
            cursor.execute('SELECT MON$PAGE_SIZE FROM MON$DATABASE')
            page_size = cursor.fetchone()[0]
            cursor.execute('SELECT P.RDB$PAGE_NUMBER, R.RDB$RELATION_ID '
                           'FROM RDB$PAGES P JOIN RDB$RELATIONS R '
                           'ON R.RDB$RELATION_ID = P.RDB$RELATION_ID '
                           "WHERE R.RDB$RELATION_NAME = 'OUTPUT_MARKER' "
                           'AND P.RDB$PAGE_TYPE = 6')
            root_page, relation_id = cursor.fetchone()
        connection.close()
        connection = None
        # Stop only this newly created, labelled fixture before copying bytes.
        # The pristine database is never modified by the corruption injector.
        phase = 'prepare-owned-damaged-copy'
        assert docker('inspect', '--format',
                      '{{index .Config.Labels "cdeadmin-owned-gate"}}',
                      container).decode().strip() == OWNER
        docker('stop', '--time', '20', container)
        pristine, uid, gid = read_container_file(container, database)
        root = root_page * page_size
        assert pristine[root] == 6
        assert struct.unpack_from('<H', pristine, root + 16)[0] == relation_id
        assert struct.unpack_from('<H', pristine, root + 18)[0] == 1
        index_page = struct.unpack_from('<I', pristine, root + 20)[0]
        index = index_page * page_size
        assert pristine[index] == 7
        assert struct.unpack_from('<H', pristine, index + 28)[0] == relation_id
        assert pristine[index + 32] == 0
        damaged = bytearray(pristine)
        struct.pack_into('<H', damaged, index + 28, relation_id + 1)
        damaged_database = '/var/lib/firebird/data/owned_output_damaged.fdb'
        write_container_file(container, '/var/lib/firebird/data',
                             'owned_output_damaged.fdb', bytes(damaged),
                             uid, gid, 0o660)
        if browser_options is not None:
            for scale in browser_options.font_scale or (100, 200, 300):
                write_container_file(
                    container, '/var/lib/firebird/data',
                    'owned_repair_damage_browser_' + str(scale) + '.fdb',
                    bytes(damaged), uid, gid, 0o660)
        assert read_container_file(container, database)[0] == pristine
        result['damage_fixture'] = {
            'kind': 'one copied index-page relation identifier mismatch',
            'pristine_file_unchanged_by_injector': True,
            'relation_id': relation_id, 'index_page': index_page,
            'page_size': page_size, 'damaged_original_user_data': False}
        docker('start', container)
        route['port'] = published_port(container)
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            try:
                connection = native.connect(
                    password=password, **_route_arguments(
                        {**route, 'database': database}, native))
                break
            except native.Error:
                time.sleep(0.25)
        if connection is None:
            raise RuntimeError('Owned server did not restart')
        connection.close()
        connection = None
        client = _create_client(SimpleNamespace(
            acquire_secret=lambda *_args: SecretLease(password)))

        def apply(operation, draft, target=damaged_database):
            request = {
                'resource_kind': 'database', 'operation_id': operation,
                'draft': draft,
                '_provider_route': {**route, 'database': target}}
            assert not client.config.administration.validate(request)['errors']
            plan = client.plan_admin_operation(request)
            return client.apply_admin_operation(plan)

        cases = [(native.SrvRepairFlag.ICU, count, database)
                 for count in (0, 1, 2, 32767)]
        cases.extend((flags, None, damaged_database) for flags in (
            native.SrvRepairFlag.VALIDATE_DB | native.SrvRepairFlag.FULL |
            native.SrvRepairFlag.CHECK_DB,
            native.SrvRepairFlag.MEND_DB | native.SrvRepairFlag.CHECK_DB))
        for flags, workers, target in cases:
            phase = 'native-output-' + str(int(flags)) + '-' + str(workers)
            check = {'case': phase, 'passed': False}
            result['checks'].append(check)
            server = None
            try:
                server = client._connect_server({'route': route})
                core = native.core
                server._reset_output()
                with native.get_api().util.get_xpb_builder(
                        core.XpbKind.SPB_START) as spb:
                    spb.insert_tag(core.ServerAction.REPAIR)
                    spb.insert_string(core.SPBItem.DBNAME, target,
                                      encoding=server.encoding)
                    spb.insert_int(core.SPBItem.OPTIONS, flags)
                    if workers is not None:
                        spb.insert_int(core.SrvRepairOption.PARALLEL_WORKERS,
                                       workers)
                    server._svc.start(spb.get_buffer())
                lines, truncated = collect_service_output(server)
                text = ''.join(lines)
                check.update(output=lines, output_truncated=truncated)
                assert not truncated
                if workers is None:
                    assert 'Summary of validation errors' in text
                    assert 'index page errors' in text.lower()
                else:
                    # Source permits warnings, but the native service need
                    # not emit a textual warning. Preserve what it returns.
                    check['worker_warning_observed'] = (
                        'Wrong parallel workers value' in text)
                check['passed'] = True
            except Exception as error:
                codes = list(status_codes(error))
                if workers is None and codes == [335740952, 335740986]:
                    check.update(passed=True, native_status_codes=codes,
                                 validation_findings_reported_as_error=True)
                else:
                    result['failures'].append({
                        'case': phase, 'type': type(error).__name__,
                        'native_status_codes': codes})
            finally:
                if server is not None:
                    client.close_session(server)
        provider_cases = [(action, modifiers, no_linger)
                          for action, modifiers in (
                ('VALIDATE_DB', []), ('VALIDATE_DB', ['FULL', 'CHECK_DB']),
                ('MEND_DB', ['CHECK_DB']), ('CORRUPTION_CHECK', []),
                ('REPAIR', [])) for no_linger in (False, True)]
        for action, modifiers, no_linger in provider_cases:
            phase = 'provider-damaged-' + action + '-'.join(modifiers)
            phase += '-no-linger-' + str(no_linger)
            check = {'case': phase, 'passed': False}
            result['checks'].append(check)
            try:
                try:
                    apply('repair_database', {
                        'repair_action': action,
                        'repair_modifiers': modifiers, 'no_linger': no_linger})
                except RelationalClientError as error:
                    assert error.native_status_codes == (335740952, 335740986)
                    assert error.service_release['service_handle_released']
                    assert 'Do not automatically replay' in str(error)
                    check.update(
                        passed=True, native_status_codes=list(
                            error.native_status_codes),
                        validation_findings_reported_as_error=True,
                        service_attachment_released=True)
                else:
                    raise AssertionError('Damaged index reported success')
            except Exception as error:
                result['failures'].append({
                    'case': phase, 'type': type(error).__name__,
                    'native_status_codes': list(status_codes(error))})
        stages = ('damaged-natural-rows', 'backup-damaged-data',
                  'restore-new-database', 'restored-index-and-validation',
                  'damage-not-magically-repaired')
        restored = '/var/lib/firebird/data/owned_output_restored.fdb'
        backup = '/var/lib/firebird/data/owned_output_recovery.fbk'
        for phase in stages:
            check = {'case': phase, 'passed': False}
            result['checks'].append(check)
            try:
                if phase in ('damaged-natural-rows',
                             'restored-index-and-validation'):
                    target = (restored if phase.startswith('restored')
                              else damaged_database)
                    connection = native.connect(
                        password=password, **_route_arguments(
                            {**route, 'database': target}, native))
                    with connection.cursor() as cursor:
                        if phase.startswith('restored'):
                            cursor.execute(
                                'SELECT RDB$INDEX_NAME '
                                'FROM RDB$RELATION_CONSTRAINTS '
                                "WHERE RDB$RELATION_NAME = 'OUTPUT_MARKER' "
                                "AND RDB$CONSTRAINT_TYPE = 'PRIMARY KEY'")
                            index_name = cursor.fetchone()[0].strip()
                            plan = 'INDEX ("' + index_name.replace(
                                '"', '""') + '")'
                        else:
                            plan = 'NATURAL'
                        cursor.execute(
                            'SELECT ID, NOTE FROM OUTPUT_MARKER WHERE ID = 1 '
                            'PLAN (OUTPUT_MARKER ' + plan + ')')
                        assert cursor.fetchall() == [(1, 'preserve')]
                    connection.close()
                    connection = None
                    if phase.startswith('restored'):
                        apply('repair_database', {
                            'repair_action': 'VALIDATE_DB',
                            'repair_modifiers': ['FULL', 'CHECK_DB']},
                            restored)
                    check['row_and_plan_verified'] = True
                elif phase == 'backup-damaged-data':
                    apply('backup_logical', {
                        'backup_file': backup, 'backup_flags': []})
                elif phase == 'restore-new-database':
                    apply('restore_logical', {
                        'backup_file': backup, 'restore_database': restored,
                        'replace_existing': False, 'restore_flags': []})
                else:
                    docker('stop', '--time', '20', container)
                    observed = read_container_file(container,
                                                   damaged_database)[0]
                    assert struct.unpack_from(
                        '<H', observed, index + 28)[0] == relation_id + 1
                    check['original_index_damage_still_present'] = True
                check['passed'] = True
            except Exception as error:
                result['failures'].append({
                    'case': phase, 'type': type(error).__name__,
                    'native_status_codes': list(status_codes(error))})
            finally:
                if connection is not None:
                    connection.close()
                    connection = None
        if browser_options is not None:
            phase = 'restart-owned-browser-server'
            docker('start', container)
            route['port'] = published_port(container)
            deadline = time.monotonic() + 45
            while time.monotonic() < deadline:
                try:
                    connection = native.connect(
                        password=password, **_route_arguments(
                            {**route, 'database': database}, native))
                    break
                except native.Error:
                    time.sleep(0.25)
            if connection is None:
                raise RuntimeError('Owned browser server did not restart')
            connection.close()
            connection = None
            result['browser_checks'] = []
            for scale in browser_options.font_scale or (100, 200, 300):
                folder = browser_options.build_root / ('browser-' + str(scale))
                folder.mkdir(parents=True, exist_ok=False)
                browser_route = {
                    **route, 'database':
                    '/var/lib/firebird/data/owned_repair_damage_browser_' +
                    str(scale) + '.fdb'}
                selected = SimpleNamespace(**{
                    **vars(browser_options), 'font_scale': [scale]})
                result['browser_checks'].extend(browser_checks(
                    selected, browser_route, password, container, folder,
                    gate_kind='repair-damage',
                    fixture_kind='firebird-repair-damage-qualification'))
    except Exception as error:
        result['failures'].append({
            'case': phase, 'type': type(error).__name__,
            'native_status_codes': list(status_codes(error)),
            'frames': [{'file': Path(frame.filename).name,
                        'line': frame.lineno, 'function': frame.name}
                       for frame in traceback.extract_tb(
                           error.__traceback__)]})
    finally:
        for kind, handle in (('database', connection), ('client', client)):
            if handle is not None:
                try:
                    handle.close()
                except Exception as error:
                    result['failures'].append({
                        'case': 'release-owned-' + kind,
                        'type': type(error).__name__})
        if container is not None:
            try:
                remove_owned(container)
                result['owned_container_removed'] = True
            except Exception as error:
                result['failures'].append({
                    'case': 'remove-owned-container',
                    'type': type(error).__name__})
    browser_count = (len(browser_options.font_scale or (100, 200, 300))
                     if browser_options is not None else 0)
    result['complete'] = (len(result['checks']) == 21 and
                          len(result.get('browser_checks', [])) ==
                          browser_count and
                          all(item['passed'] for item in result['checks']) and
                          not result['failures'] and
                          all(item['passed'] for item in result.get(
                              'browser_checks', [])) and
                          result['owned_container_removed'])
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', default='firebirdsql/firebird:5.0.4')
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--browser', action='store_true')
    parser.add_argument('--build-root', type=Path)
    parser.add_argument('--source-config-db', type=Path,
                        default=Path('/var/lib/cdeadmin/cdeadmin.db'))
    parser.add_argument('--desktop-user', default='dalton.calford@gmail.com')
    parser.add_argument('--font-scale', type=int, action='append',
                        choices=(100, 200, 300))
    options = parser.parse_args()
    if options.output.exists():
        parser.error('Use a new evidence file')
    if options.browser:
        if options.build_root is None or options.build_root.exists():
            parser.error('Browser tests require a new --build-root directory')
        options.build_root.mkdir(parents=True, exist_ok=False)
    result = run(options.image, options if options.browser else None)
    options.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result))
    return 0 if result['complete'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
