#!/usr/bin/env python3
##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Certify every Firebird database lifecycle form in the real browser UI.

The caller supplies an isolated CDEadmin configuration database.  Register
and remove use a separately created disposable Firebird database.  Create,
alter, and drop use another disposable database and are verified through an
independent Firebird 5 driver connection.  No packaged sample is mutated.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from selenium.webdriver.support.ui import WebDriverWait


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import cdeadmin_sqlite_database_lifecycle_ui_gate as shared  # noqa: E402
from tools.cdeadmin_firebird_ui_form_gate import (  # noqa: E402
    create_driver,
    evidence_variant,
)


ENGINE_ID = 'firebird'
ENGINE_NAME = 'Firebird'
PROFILE_ID = 'firebird-native'
REFERENCE_VERSION = '5.0.4'
COMPLETION_ORDER = (
    'connect', 'edit', 'define', 'remove', 'create', 'alter', 'drop',
)
RENDER_ORDER = (
    'connect', 'edit', 'alter', 'drop', 'remove', 'define', 'create',
)


def arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', required=True)
    parser.add_argument('--config-db', type=Path, required=True)
    parser.add_argument('--database-root', required=True)
    parser.add_argument('--database', required=True)
    parser.add_argument(
        '--engine', choices=(ENGINE_NAME,), default=ENGINE_NAME,
    )
    parser.add_argument('--server', default='localhost')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--firebird-port', type=int, default=53050)
    parser.add_argument('--user', default='SYSDBA')
    parser.add_argument(
        '--password-env', default='CDEADMIN_FIREBIRD_DEMO_PASSWORD'
    )
    parser.add_argument('--client-library', type=Path, required=True)
    parser.add_argument('--output-root', type=Path, required=True)
    parser.add_argument('--summary-output', type=Path, required=True)
    parser.add_argument('--browser-binary')
    parser.add_argument('--width', type=int, default=1600)
    parser.add_argument('--height', type=int, default=1000)
    parser.add_argument(
        '--theme', choices=('default', 'high-contrast'), default='default',
    )
    parser.add_argument(
        '--font-scale', type=int, choices=(100, 150, 200, 300), default=100,
    )
    parser.add_argument('--timeout', type=int, default=90)
    return parser.parse_args(argv)


def _configure_shared(password):
    shared.ENGINE_NAME = ENGINE_NAME
    shared.ENGINE_ID = ENGINE_ID
    shared.PROFILE_ID = PROFILE_ID
    shared.ENDPOINT_PASSWORD = password
    shared.COMMANDS = {
        'define': 'endpoint.firebird.register_database',
        'create': 'endpoint.firebird.create_database',
        **{
            mode: f'database.firebird.{mode}'
            for mode in ('connect', 'edit', 'alter', 'drop', 'remove')
        },
    }


def _load_firebird(options):
    if not options.client_library.is_file():
        raise RuntimeError('Firebird 5 client library is missing')
    os.environ['CDEADMIN_FIREBIRD_CLIENT_LIBRARY'] = str(
        options.client_library.resolve()
    )
    import firebird.driver as driver

    if not driver.fbapi.has_api():
        driver.driver_config.fb_client_library.value = str(
            options.client_library.resolve()
        )
    api = driver.get_api()
    loaded = Path(api.client_library_name).resolve()
    if loaded != options.client_library.resolve():
        raise RuntimeError(
            'Firebird driver loaded a different client library'
        )
    return driver


def _dsn(options, database):
    return f'{options.host}/{options.firebird_port}:{database}'


def _connect(module, options, password, database):
    return module.connect(
        _dsn(options, database), user=options.user, password=password,
    )


def _create(module, options, password, database):
    connection = module.create_database(
        _dsn(options, database), user=options.user, password=password,
        overwrite=False,
    )
    connection.close()


def _drop(module, options, password, database):
    try:
        connection = _connect(module, options, password, database)
    except Exception:
        return False
    dropped = False
    try:
        try:
            connection.drop_database()
        except Exception:
            return False
        else:
            dropped = True
            return True
    finally:
        if not dropped:
            connection.close()


def _native_state(module, options, password, database):
    connection = _connect(module, options, password, database)
    try:
        cursor = connection.cursor()
        cursor.execute(
            'SELECT MON$PAGE_SIZE, MON$ODS_MAJOR, MON$ODS_MINOR, '
            'MON$SQL_DIALECT, MON$FORCED_WRITES, MON$RESERVE_SPACE '
            'FROM MON$DATABASE'
        )
        monitor = cursor.fetchone()
        cursor.execute(
            'SELECT TRIM(RDB$CHARACTER_SET_NAME), RDB$LINGER, '
            'RDB$SQL_SECURITY FROM RDB$DATABASE'
        )
        database_row = cursor.fetchone()
        cursor.close()
        return {
            'connectable': True,
            'engine_version': str(connection.info.engine_version),
            'page_size': int(monitor[0]),
            'ods': f'{int(monitor[1])}.{int(monitor[2])}',
            'sql_dialect': int(monitor[3]),
            'forced_writes': bool(monitor[4]),
            'reserve_space': bool(monitor[5]),
            'default_character_set': str(database_row[0]).strip(),
            'linger_seconds': database_row[1],
            'default_sql_security': database_row[2],
        }
    finally:
        connection.close()


def _wait_target(wait, options, predicate, message):
    shared._wait_for_native_state(
        wait,
        lambda: predicate(shared._target_rows(options.config_db)),
        message,
    )


def _complete_cases(driver, wait, options, module, password,
                    evidence, cleanup):
    suffix = uuid.uuid4().hex
    root = PurePosixPath(options.database_root)
    registered_path = str(root / f'cdeadmin_ui_registered_{suffix}.fdb')
    created_path = str(root / f'cdeadmin_ui_created_{suffix}.fdb')
    _create(module, options, password, registered_path)
    cleanup.update({
        'registered_database': registered_path,
        'created_database': created_path,
        'database_files_removed': [],
        'unverified_paths': [],
        'native_create_requested': False,
    })
    try:
        shared._open_form(driver, wait, 'connect', options.database)
        shared._submit_target_form(driver, wait, 'connect', {
            'Connection character set': 'UTF8',
            'Default transaction isolation': 'SNAPSHOT',
            'Default transaction access': 'WRITE',
            'Transaction lock timeout (seconds)': '7',
            'Statement timeout (milliseconds; 0 uses database policy)': '1234',
            'Session idle timeout (seconds; 0 uses database policy)': '60',
            'Native autocommit (writes cannot be rolled back later)': 'true',
            'No auto undo (native rollback still applies)': 'true',
            'Ignore records from limbo transactions': 'true',
        })
        _wait_target(
            wait, options,
            lambda rows: any(
                item['display_name'] == options.database and item['active']
                and item['configuration'].get(
                    'transaction_lock_timeout') == 7 and
                item['configuration'].get('statement_timeout_ms') == 1234 and
                item['configuration'].get('session_idle_timeout_seconds') == 60
                and all(item['configuration'].get(key) is True for key in (
                    'transaction_auto_commit', 'transaction_no_auto_undo',
                    'transaction_ignore_limbo'))
                for item in rows
            ),
            'connect did not retain active Firebird session defaults',
        )
        evidence.append({
            'mode': 'connect', 'active_target_observed': True,
            'connection_options_observed': True,
            'preview_state': 'not_applicable',
        })
        shared._capture_completed(driver, options, 'connect', evidence[-1])
        shared._close(driver, wait)

        shared._open_form(driver, wait, 'edit', options.database)
        shared._submit_target_form(driver, wait, 'edit', {
            'Navigator display name': options.database,
            'Connection character set': 'UTF8',
            'Default transaction isolation':
                'READ_COMMITTED_READ_CONSISTENCY',
            'Default transaction access': 'WRITE',
            'Transaction lock timeout (seconds)': '8',
            'Statement timeout (milliseconds; 0 uses database policy)': '2000',
            'Session idle timeout (seconds; 0 uses database policy)': '90',
            'Native autocommit (writes cannot be rolled back later)': 'false',
            'No auto undo (native rollback still applies)': 'false',
            'Ignore records from limbo transactions': 'false',
        })
        _wait_target(
            wait, options,
            lambda rows: any(
                item['display_name'] == options.database and
                item['configuration'].get('transaction_isolation') ==
                'READ_COMMITTED_READ_CONSISTENCY' and
                item['configuration'].get('transaction_lock_timeout') == 8 and
                item['configuration'].get('statement_timeout_ms') == 2000 and
                item['configuration'].get('session_idle_timeout_seconds') == 90
                and all(item['configuration'].get(key) is False for key in (
                    'transaction_auto_commit', 'transaction_no_auto_undo',
                    'transaction_ignore_limbo'))
                for item in rows
            ),
            'edit did not persist Firebird connection defaults',
        )
        evidence.append({
            'mode': 'edit', 'connection_options_observed': True,
            'preview_state': 'not_applicable',
        })
        shared._capture_completed(driver, options, 'edit', evidence[-1])
        shared._close(driver, wait)

        registered_label = PurePosixPath(registered_path).name
        shared._open_form(driver, wait, 'define')
        shared._submit_target_form(driver, wait, 'define', {
            'Firebird database filename or alias': registered_path,
            'Navigator display name': registered_label,
            'Connection character set': 'UTF8',
            'Default transaction isolation': 'SNAPSHOT',
            'Default transaction access': 'WRITE',
            'Transaction lock timeout (seconds)': '9',
            'Statement timeout (milliseconds; 0 uses database policy)': '3000',
            'Session idle timeout (seconds; 0 uses database policy)': '120',
        })
        _wait_target(
            wait, options,
            lambda rows: any(
                item['database'] == registered_path and item['active'] and
                item['configuration'].get('statement_timeout_ms') == 3000 and
                item['configuration'].get(
                    'session_idle_timeout_seconds') == 120
                for item in rows
            ),
            'register did not retain and activate the Firebird database',
        )
        registered_state = _native_state(
            module, options, password, registered_path
        )
        evidence.append({
            'mode': 'define', 'retained_target_observed': True,
            'native_database_preserved': registered_state['connectable'],
            'preview_state': 'not_applicable',
        })
        shared._capture_completed(driver, options, 'define', evidence[-1])
        shared._close(driver, wait)

        shared._refresh_tree(driver, wait, options, registered_label)
        shared._open_form(driver, wait, 'remove', registered_label)
        shared._submit_target_form(driver, wait, 'remove', {
            'Type the native name to confirm': registered_path,
        })
        _wait_target(
            wait, options,
            lambda rows: not any(
                item['database'] == registered_path for item in rows
            ),
            'remove retained the Firebird database registration',
        )
        registered_state = _native_state(
            module, options, password, registered_path
        )
        evidence.append({
            'mode': 'remove', 'target_removed': True,
            'native_database_preserved': registered_state['connectable'],
            'preview_state': 'not_applicable',
        })
        shared._capture_completed(driver, options, 'remove', evidence[-1])
        shared._close(driver, wait)

        shared._refresh_tree(driver, wait, options, options.database)
        shared._open_form(driver, wait, 'create')
        cleanup['native_create_requested'] = True
        lifecycle = shared._submit_lifecycle(
            driver, wait, options, 'create', {
                'Absolute database filename on the Firebird server':
                    created_path,
                'Page size': '16384',
                'Default character set': 'UTF8',
                'Database SQL dialect': '3',
            }
        )
        created_state = _native_state(module, options, password, created_path)
        if (
            not created_state['engine_version'].startswith('5.0') or
            created_state['page_size'] != 16384 or
            created_state['sql_dialect'] != 3 or
            created_state['default_character_set'] != 'UTF8'
        ):
            raise RuntimeError(
                'created Firebird database differs from the provider plan'
            )
        _wait_target(
            wait, options,
            lambda rows: any(
                item['database'] == created_path and item['active']
                for item in rows
            ),
            'created Firebird database was not retained and activated',
        )
        evidence.append({
            'mode': 'create', **lifecycle,
            'native_state_observed': created_state,
            'retained_target_observed': True,
        })
        shared._capture_completed(driver, options, 'create', evidence[-1])
        shared._close(driver, wait)

        created_label = PurePosixPath(created_path).name
        shared._refresh_tree(driver, wait, options, created_label)
        shared._open_form(driver, wait, 'alter', created_label)
        lifecycle = shared._submit_lifecycle(
            driver, wait, options, 'alter', {
                'Default SQL security': 'INVOKER',
            }
        )
        altered_state = _native_state(module, options, password, created_path)
        if altered_state['default_sql_security'] != 0:
            raise RuntimeError(
                'Firebird default SQL security did not change to INVOKER'
            )
        evidence.append({
            'mode': 'alter', **lifecycle,
            'native_state_observed': altered_state,
        })
        shared._capture_completed(driver, options, 'alter', evidence[-1])
        shared._close(driver, wait)

        shared._refresh_tree(driver, wait, options, created_label)
        shared._open_form(driver, wait, 'drop', created_label)
        lifecycle = shared._submit_lifecycle(driver, wait, options, 'drop', {
            'Type the exact Firebird database filename or alias to confirm':
                created_path,
        })
        try:
            _native_state(module, options, password, created_path)
        except Exception:
            pass
        else:
            raise RuntimeError('drop retained a connectable Firebird database')
        _wait_target(
            wait, options,
            lambda rows: not any(
                item['database'] == created_path for item in rows
            ),
            'drop retained the deleted Firebird target',
        )
        evidence.append({
            'mode': 'drop', **lifecycle,
            'native_database_absent': True, 'target_removed': True,
        })
        shared._capture_completed(driver, options, 'drop', evidence[-1])
        shared._close(driver, wait)
        cleanup['database_files_removed'].append(created_path)
        return evidence, cleanup
    finally:
        candidates = [registered_path]
        if cleanup['native_create_requested']:
            candidates.append(created_path)
        for database in candidates:
            if database in cleanup['database_files_removed']:
                continue
            if _drop(module, options, password, database):
                cleanup['database_files_removed'].append(database)
            else:
                cleanup['unverified_paths'].append(database)


def _install_menu_trace(driver):
    """Capture bounded event/geometry evidence without input values."""
    driver.execute_script('''
      const trace = [];
      window.__cdeFirebirdMenuTrace = trace;
      const describe = (node) => node instanceof Element ? {
        tag: node.tagName, role: node.getAttribute('role'),
        className: String(node.className || '').slice(0, 160),
        scrollTop: node.scrollTop, scrollLeft: node.scrollLeft,
      } : {tag: 'document'};
      const record = (kind, target) => {
        trace.push({time: performance.now(), kind, target: describe(target),
          menus: [...document.querySelectorAll('.szh-menu')].map(menu => ({
            label: menu.getAttribute('aria-label'),
            className: menu.className, height: menu.offsetHeight,
          }))});
        if (trace.length > 200) trace.shift();
      };
      for (const kind of ['contextmenu', 'click', 'scroll', 'focusin',
                          'focusout']) {
        document.addEventListener(kind, event => record(kind, event.target),
                                  {capture: true, passive: true});
      }
      document.addEventListener('keydown', event => {
        if (event.key === 'Escape') record('Escape', event.target);
      }, {capture: true, passive: true});
      const containsMenu = node => node instanceof Element &&
        (node.matches('.szh-menu') || node.querySelector('.szh-menu'));
      new MutationObserver(changes => {
        if (changes.some(change =>
          change.target instanceof Element &&
          (change.target.closest('.szh-menu') ||
           [...change.addedNodes, ...change.removedNodes]
             .some(containsMenu)))) {
          record('menu-mutation', document.activeElement);
        }
      }).observe(document.body, {subtree: true, childList: true,
                                 attributes: true,
                                 attributeFilter: ['class', 'style']});
    ''')


def run(options):
    password = os.environ.get(options.password_env)
    if not password:
        raise RuntimeError(
            f'{options.password_env} must contain the test credential'
        )
    options.endpoint_password_env = options.password_env
    _configure_shared(password)
    module = _load_firebird(options)
    driver = create_driver(options)
    driver.set_script_timeout(max(120, options.timeout * 10))
    wait = WebDriverWait(driver, options.timeout)
    rendered = []
    completed = []
    cleanup = {}
    failures = []

    def record_failure(phase, exc):
        failure_path = options.output_root / (phase + '-failure.png')
        failure_path.parent.mkdir(parents=True, exist_ok=True)
        failure = {'phase': phase, 'error_type': type(exc).__name__,
                   'error': str(exc), 'traceback': traceback.format_exc()}
        try:
            driver.save_screenshot(str(failure_path))
            failure['screenshot'] = str(failure_path)
            failure['menu_trace'] = driver.execute_script(
                'return window.__cdeFirebirdMenuTrace || []')
        except Exception as screenshot_error:
            failure['screenshot_error_type'] = type(screenshot_error).__name__
        failures.append(failure)

    try:
        driver.get(options.url.rstrip('/') + '/browser/')
        shared._prepare_tree(driver, wait, options, options.database)
        _install_menu_trace(driver)
        forms = shared._catalog_forms(driver)
        if forms.get('__error__'):
            raise RuntimeError(forms['__error__'])
        try:
            _complete_cases(driver, wait, options, module, password,
                            completed, cleanup)
        except Exception as exc:
            record_failure('execution', exc)
        for mode in RENDER_ORDER:
            try:
                shared._refresh_tree(driver, wait, options, options.database)
                database_label = (
                    options.database if mode not in {'define', 'create'}
                    else None)
                rendered.append(shared._render_case(
                    driver, wait, options, forms, mode, database_label))
            except Exception as exc:
                record_failure('render-' + mode, exc)
    except Exception as exc:
        record_failure('setup', exc)
    finally:
        try:
            driver.quit()
        except Exception as exc:
            record_failure('browser-cleanup', exc)
    passed_modes = {item['mode'] for item in rendered}
    completed_modes = {item['mode'] for item in completed}
    return {
        'schema': 'cdeadmin.firebird-database-lifecycle-ui-gate.v1',
        'captured_at': datetime.now(timezone.utc).isoformat(),
        'engine_id': ENGINE_ID, 'interface_id': PROFILE_ID,
        'reference_version': REFERENCE_VERSION,
        'viewport': f'{options.width}x{options.height}',
        'theme': options.theme,
        'font_scale': options.font_scale,
        'evidence_variant': evidence_variant(options),
        'expected_modes': list(COMPLETION_ORDER),
        'rendered': rendered, 'completed': completed,
        'cleanup': cleanup,
        'cancelled_form_count': sum(
            item['cancellation']['dialog_dismissed'] for item in rendered
        ),
        'validation_observed_form_count': sum(
            item['validation']['state'] == 'observed' for item in rendered
        ),
        'validation_not_applicable_form_count': sum(
            item['validation']['state'] == 'not_applicable'
            for item in rendered
        ),
        'provider_plan_preview_count': sum(
            item.get('plan_state') == 'ready' for item in completed
        ),
        'completed_screenshot_count': sum(
            'completed' in item.get('screenshots', {}) for item in completed
        ),
        'failures': failures,
        'credential_values_exported': False,
        'provider_values_recorded': False,
        'complete': (
            passed_modes == set(COMPLETION_ORDER) and
            completed_modes == set(COMPLETION_ORDER) and not failures and
            not cleanup.get('unverified_paths')
        ),
    }


def main(argv=None):
    options = arguments(argv)
    result = run(options)
    options.summary_output.parent.mkdir(parents=True, exist_ok=True)
    options.summary_output.write_text(
        json.dumps(result, indent=2, sort_keys=True) + '\n',
        encoding='utf-8',
    )
    print(json.dumps({
        'complete': result['complete'],
        'rendered_modes': [item['mode'] for item in result['rendered']],
        'completed_modes': [item['mode'] for item in result['completed']],
        'failures': [item['error'] for item in result['failures']],
        'output': str(options.summary_output),
    }, indent=2, sort_keys=True))
    return 0 if result['complete'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
