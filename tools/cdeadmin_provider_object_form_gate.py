#!/usr/bin/env python3
##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Exercise provider-owned object forms through the real CDEadmin browser UI.

This gate is deliberately catalog-driven.  It asks the authenticated provider
workspace for its executable object inventory, verifies that every operation
is represented by an Object Explorer context command, opens the focused
single-task form through the same callback used by that command, verifies all
declared controls, and captures value-free screenshots and plan evidence.

Row update/delete plans are not fabricated here because they require a
single-use identity issued by the provider data grid.  Their forms are still
rendered and recorded; their live completion belongs to the separate grid
transaction gate.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import threading
import traceback
from datetime import datetime, timezone
from pathlib import Path

from selenium.common.exceptions import TimeoutException
from selenium.webdriver import ActionChains
from selenium.webdriver.common.actions.action_builder import ActionBuilder
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait

if __package__:
    from .cdeadmin_firebird_ui_form_gate import (
        accessibility_observation,
        apply_presentation,
        assert_form_controls,
        cancellation_observation,
        close_workspace,
        close_workspace_with_keyboard,
        create_driver,
        evidence_variant,
        layout_observation,
        screenshot,
    )
    from .cdeadmin_ui_evidence import (
        complete_endpoint_prompt,
        ensure_data_explorer,
        expand,
        fill_fields,
        visible_named_control,
        wait_for_tree_item,
    )
else:
    from cdeadmin_firebird_ui_form_gate import (
        accessibility_observation,
        apply_presentation,
        assert_form_controls,
        cancellation_observation,
        close_workspace,
        close_workspace_with_keyboard,
        create_driver,
        evidence_variant,
        layout_observation,
        screenshot,
    )
    from cdeadmin_ui_evidence import (
        complete_endpoint_prompt,
        ensure_data_explorer,
        expand,
        fill_fields,
        visible_named_control,
        wait_for_tree_item,
    )


GRID_IDENTITY_OPERATIONS = frozenset({
    ('table', 'update'), ('table', 'delete'),
})


def _quit_driver(driver, timeout=15):
    """Bound WebDriver shutdown so a failed gate can still emit evidence."""
    completed = threading.Event()

    def quit_browser():
        try:
            driver.quit()
        finally:
            completed.set()

    worker = threading.Thread(target=quit_browser, daemon=True)
    worker.start()
    if completed.wait(timeout):
        return
    process = getattr(getattr(driver, 'service', None), 'process', None)
    if process is not None and process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except Exception:  # pragma: no cover - last-resort harness cleanup
            process.kill()
    completed.wait(5)


def arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', required=True)
    parser.add_argument('--engine', required=True)
    parser.add_argument('--engine-id', required=True)
    parser.add_argument('--interface-id', required=True)
    parser.add_argument('--reference-version', required=True)
    parser.add_argument('--server', required=True)
    parser.add_argument('--database', required=True)
    parser.add_argument('--output-root', type=Path, required=True)
    parser.add_argument('--summary-output', type=Path, required=True)
    parser.add_argument('--browser-binary')
    parser.add_argument(
        '--endpoint-password-env',
        help=(
            'Environment variable holding the disposable endpoint password. '
            'The value is never copied into evidence.'
        ),
    )
    parser.add_argument('--width', type=int, default=1600)
    parser.add_argument('--height', type=int, default=1000)
    parser.add_argument('--timeout', type=int, default=90)
    parser.add_argument('--theme', default='default')
    parser.add_argument('--font-scale', type=int, default=100)
    parser.add_argument(
        '--resource-kind', dest='resource_kinds', action='append',
        help=(
            'Limit the matrix to one resource kind. Repeat for a focused '
            'provider-owned qualification pass.'
        ),
    )
    parser.add_argument(
        '--operation-id', dest='operation_ids', action='append',
        help=(
            'Limit the matrix to one operation ID. Repeat for a focused '
            'provider-owned qualification pass.'
        ),
    )
    return parser.parse_args(argv)


def _context_click_visible_label(driver, wait, element):
    """Right-click the visible label, not its clipped geometric centre."""
    driver.execute_script(
        "arguments[0].scrollIntoView({block:'center', inline:'nearest', "
        "behavior:'instant'});", element)
    point = wait.until(lambda browser: browser.execute_script("""
      const element = arguments[0];
      const rect = element.getBoundingClientRect();
      let left = Math.max(0, rect.left), top = Math.max(0, rect.top);
      let right = Math.min(innerWidth, rect.right);
      let bottom = Math.min(innerHeight, rect.bottom);
      for (let parent = element.parentElement; parent;
           parent = parent.parentElement) {
        const style = getComputedStyle(parent);
        const bounds = parent.getBoundingClientRect();
        if (/(auto|scroll|hidden|clip)/.test(style.overflowX)) {
          left = Math.max(left, bounds.left);
          right = Math.min(right, bounds.right);
        }
        if (/(auto|scroll|hidden|clip)/.test(style.overflowY)) {
          top = Math.max(top, bounds.top);
          bottom = Math.min(bottom, bounds.bottom);
        }
      }
      if (right - left < 2 || bottom - top < 2) return null;
      const x = Math.floor((left + right) / 2);
      const y = Math.floor((top + bottom) / 2);
      const hit = document.elementFromPoint(x, y);
      return element === hit || element.contains(hit) ? {x, y} : null;
    """, element))
    actions = ActionBuilder(driver)
    actions.pointer_action.move_to_location(point['x'], point['y'])
    actions.pointer_action.pointer_down(button=2)
    actions.pointer_action.pointer_up(button=2)
    actions.perform()


def _prepare_tree(driver, wait, options):
    driver.get(options.url.rstrip('/') + '/browser/')
    wait.until(lambda value: '/browser/' in value.current_url)
    apply_presentation(driver, wait, options)
    ensure_data_explorer(wait)
    for label, child in (
        ('Connectors', options.engine),
        (options.engine, options.server),
        (options.server, options.database),
    ):
        print(f'expand {label} -> {child}', flush=True)
        expand(wait, label)
        try:
            wait_for_tree_item(wait, child)
        except Exception:
            timings = driver.execute_script("""
              return performance.getEntriesByType('resource').slice(-60)
                .map(item => ({path: new URL(item.name).pathname,
                  duration_ms: item.duration,
                  status: item.responseStatus || null}));
            """)
            options.output_root.mkdir(parents=True, exist_ok=True)
            (options.output_root / 'navigation-timing.json').write_text(
                json.dumps(timings, indent=2) + '\n')
            raise
    database = wait_for_tree_item(wait, options.database)
    _context_click_visible_label(driver, wait, database)
    try:
        wait.until(lambda value: value.execute_script(
            """
            const tree = window.pgAdmin?.Browser?.tree;
            const item = tree?.selected?.();
            const data = item ? tree.itemData(item) : null;
            const selected = Boolean(
              data && data._type === 'cde_database_target' &&
              (data.label === arguments[0] ||
               data._label === arguments[0])
            );
            if (selected) {
              let endpointItem = item;
              while (endpointItem &&
                     tree.itemData(endpointItem)?._type !== 'server') {
                endpointItem = tree.hasParent(endpointItem) ?
                  tree.parent(endpointItem) : null;
              }
              window.__cdeadminQaDatabaseItem = item;
              window.__cdeadminQaEndpointItem = endpointItem;
            }
            return selected;
            """,
            options.database,
        ))
    except TimeoutException as exc:
        selected = driver.execute_script(
            """
            const tree = window.pgAdmin?.Browser?.tree;
            const item = tree?.selected?.();
            const data = item ? tree.itemData(item) : null;
            return data ? {
              id: data._id ?? null,
              type: data._type ?? null,
              label: data.label ?? data._label ?? null,
            } : null;
            """
        )
        raise RuntimeError(
            f'provider database tree selection failed: {selected!r}'
        ) from exc
    ActionChains(driver).send_keys(Keys.ESCAPE).perform()
    driver.execute_script(
        """
        const tree = window.pgAdmin.Browser.tree;
        const item = window.__cdeadminQaEndpointItem;
        const node = window.pgAdmin.Browser.Nodes.server;
        node.callbacks.open_cde_workspace.call(node, {item}, 'resources');
        """
    )
    if options.endpoint_password_env:
        print('complete endpoint verification prompt', flush=True)
        password = os.environ.get(options.endpoint_password_env)
        if password is None:
            raise RuntimeError(
                'endpoint password environment variable is unavailable'
            )
        complete_endpoint_prompt(driver, password, timeout=options.timeout)
    try:
        wait.until(lambda value: value.execute_script(
            """
            const tree = window.pgAdmin?.Browser?.tree;
            const item = window.__cdeadminQaEndpointItem;
            const data = item ? tree.itemData(item) : null;
            return Boolean(data?.cde_session_authenticated &&
              data?.runtime_verification_state === 'verified');
            """
        ))
        wait.until(lambda value: visible_named_control(value, 'Close'))
    except TimeoutException as exc:
        diagnostic = driver.execute_async_script(
            """
            const done = arguments[arguments.length - 1];
            const tree = window.pgAdmin?.Browser?.tree;
            const item = window.__cdeadminQaEndpointItem;
            const data = item ? tree.itemData(item) : null;
            const node = window.pgAdmin?.Browser?.Nodes?.server;
            const base = {
              authenticated: data?.cde_session_authenticated ?? false,
              verification_state:
                data?.runtime_verification_state ?? null,
              profile_id: data?.cde_profile_id ?? null,
              endpoint_url: item && data && node ? node.generate_url(
                item, 'verify_endpoint', data, true
              ) : null,
              visible_dialogs: [...document.querySelectorAll(
                '[role="dialog"]'
              )].filter(element => element.offsetParent !== null).map(
                element => element.innerText.slice(0, 1000)
              ),
              alerts: [...document.querySelectorAll('[role="alert"]')]
                .filter(element => element.offsetParent !== null)
                .map(element => element.innerText.slice(0, 500)),
            };
            if (!base.endpoint_url) {
              done(base);
              return;
            }
            const headers = {};
            if (window.pgAdmin.csrf_token_header &&
                window.pgAdmin.csrf_token) {
              headers[window.pgAdmin.csrf_token_header] =
                window.pgAdmin.csrf_token;
            }
            fetch(base.endpoint_url, {
              method: 'POST', credentials: 'same-origin', headers,
              body: new FormData(),
            }).then(async response => {
              const body = await response.json().catch(() => null);
              done({...base, direct_probe: {
                status: response.status,
                success: body?.success ?? null,
                error: body?.errormsg ?? null,
                verification_state:
                  body?.data?.runtime_verification_state ?? null,
              }});
            }).catch(error => done({
              ...base,
              direct_probe: {error: String(error)},
            }));
            """
        )
        raise RuntimeError(
            'provider endpoint workspace verification failed: ' +
            json.dumps(diagnostic, sort_keys=True)
        ) from exc
    close_workspace(driver, wait)
    print('endpoint workspace verified', flush=True)
    return database


def _workspace_probe(driver, resource_kinds=None,
                     collect_context_commands=True):
    """Return provider catalog plus recursively resolved context commands."""
    return driver.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        const selectedKinds = new Set(arguments[0] || []);
        const containers = new Set(['database', 'schema', 'namespace',
          'system', 'system-objects', 'catalog']);
        const app = window.pgAdmin;
        const tree = app?.Browser?.tree;
        const databaseItem = window.__cdeadminQaDatabaseItem ||
          tree?.selected?.();
        const database = databaseItem ? tree.itemData(databaseItem) : null;
        let endpointItem = window.__cdeadminQaEndpointItem || databaseItem;
        while (endpointItem &&
               tree.itemData(endpointItem)?._type !== 'server') {
          endpointItem = tree.hasParent(endpointItem) ?
            tree.parent(endpointItem) : null;
        }
        const endpoint = endpointItem ? tree.itemData(endpointItem) : null;
        const node = app?.Browser?.Nodes?.server;
        if (!database || !endpoint || !node) {
          done({probe_error: 'selected provider hierarchy is incomplete'});
          return;
        }
        const base = node.generate_url(
          endpointItem, 'cde_workspace', endpoint, true
        );
        const separator = base.includes('?') ? '&' : '?';
        const target = encodeURIComponent(database._id);
        const endpointUrl = `${base}${separator}database_target_id=${target}`;
        const headers = {'Content-type': 'application/json'};
        if (app.csrf_token_header && app.csrf_token) {
          headers[app.csrf_token_header] = app.csrf_token;
        }
        const request = (url) => fetch(url, {
          credentials: 'same-origin', headers,
        }).then(async response => ({
          status: response.status, body: await response.json(), url,
        }));
        const post = (url, payload) => fetch(url, {
          method: 'POST', credentials: 'same-origin', headers,
          body: JSON.stringify(payload),
        }).then(async response => ({
          status: response.status, body: await response.json(), url,
        }));
        const commands = [...(endpoint.cde_context_actions || [])].map(
          (action) => ({
            command_id: action.command_id,
            arguments: action.arguments || {},
            enabled: action.enabled !== false,
            label: action.label,
            resource_id: null,
            resource_kind: 'server',
          }));
        const seen = new Set();
        const walk = async (url, depth=0) => {
          if (!url || seen.has(url) || depth > 8) return;
          seen.add(url);
          const response = await request(url);
          if (response.status !== 200 || response.body?.success !== 1) {
            throw new Error(`navigator request failed (${response.status})`);
          }
          for (const item of response.body.data || []) {
            for (const action of item.cde_context_actions || []) {
              commands.push({
                command_id: action.command_id,
                arguments: action.arguments || {},
                enabled: action.enabled !== false,
                label: action.label,
                resource_id: item.cde_resource_id || item._id || null,
                resource_kind: item.cde_resource_kind || null,
              });
            }
            const kind = item.cde_resource_kind;
            if (item.children_url && (!selectedKinds.size || !kind ||
                containers.has(kind) || selectedKinds.has(kind))) {
              await walk(item.children_url, depth + 1);
            }
          }
        };
        (arguments[1] === false ? Promise.resolve() :
          walk(database.children_url))
          .then(() => request(endpointUrl))
          .then(async (workspace) => {
            const value = workspace.body?.data || {};
            const resources = [...(value.resource_page?.items || [])];
            let continuation = value.resource_page?.next_cursor || null;
            const generation = value.resource_page?.generation || null;
            while (continuation) {
              const response = await post(endpointUrl, {
                action: 'resource_page', request: {
                  continuation, generation,
                  database_target_id: database._id,
                },
              });
              if (response.status !== 200 ||
                  response.body?.success !== 1) {
                throw new Error(
                  `resource continuation failed (${response.status})`
                );
              }
              const page = response.body?.data || {};
              resources.push(...(page.items || []));
              continuation = page.next_cursor || null;
            }
            done({
              status: workspace.status,
              endpoint_url: endpointUrl,
              database_target_id: database._id,
              generation,
              engine_id: value.visual_admin?.engine_id || null,
              catalog: value.visual_admin || null,
              resources,
              resource_complete: continuation == null,
              context_commands: commands,
              context_commands_collected: arguments[1] !== false,
            });
          })
          .catch(error => done({probe_error: String(error)}));
        """,
        resource_kinds or [], collect_context_commands,
    )


def _open_focused_form(driver, operation, target, database_target_id, *,
                       workspace='administration'):
    if workspace not in ('administration', 'object'):
        raise ValueError('Choose a task form or object editor workspace')
    driver.execute_script(
        """
        const operation = arguments[0];
        const target = arguments[1];
        const databaseTargetId = arguments[2];
        const workspace = arguments[3];
        const app = window.pgAdmin;
        const item = window.__cdeadminQaEndpointItem;
        const node = app.Browser.Nodes.server;
        node.callbacks.open_cde_workspace.call(node, {item},
          workspace, {
            resource_id: operation.resource_kind === 'database' ?
              databaseTargetId : target?.resource_id,
            database_target_id: databaseTargetId,
            resource_kind: operation.resource_kind,
            operation_id: operation.operation_id,
            task_title: operation.title,
          });
        """,
        operation, target, database_target_id, workspace,
    )


def _native_name(target):
    extensions = target.get('extensions') or {}
    cdeadmin = extensions.get('cdeadmin') or {}
    return cdeadmin.get('native_name') or target.get('display_name')


def _provider_native(target, engine_id):
    native = target.get('native')
    if isinstance(native, dict):
        return native
    extensions = target.get('extensions') or {}
    provider = extensions.get(engine_id) or {}
    value = provider.get('native') or {}
    return value if isinstance(value, dict) else {}


def _preview_values(kind, operation, target, engine_id):
    operation_id = operation['operation_id']
    labels = {
        field['field_id']: field['label']
        for field in operation.get('form', {}).get('fields', [])
    }

    def rendered(values):
        return {
            labels[field_id]: value
            for field_id, value in values.items()
            if field_id in labels
        }

    name = _native_name(target)
    if engine_id == 'firebird':
        if kind == 'trigger' and operation_id in {'create_or_alter', 'recreate'}:
            return rendered({
                'name': 'CDE_UI_TRIGGER', 'confirmation': str(name),
                'declaration': 'INACTIVE ON CONNECT AS BEGIN END',
            })
        if kind == 'function' and operation_id in {'create_or_alter', 'recreate'}:
            return rendered({
                'name': 'CDE_UI_FUNCTION', 'confirmation': str(name),
                'declaration': 'RETURNS INTEGER AS BEGIN RETURN 1; END',
            })
        if kind == 'procedure' and operation_id in {'create_or_alter',
                                                   'recreate'}:
            return rendered({
                'name': 'CDE_UI_PROCEDURE', 'confirmation': str(name),
                'declaration': 'AS BEGIN END',
            })
        if kind == 'exception' and operation_id in {'create_or_alter',
                                                   'recreate'}:
            return rendered({
                'name': 'CDE_UI_EXCEPTION', 'confirmation': str(name),
                'message': 'Exception preview message @1',
            })
        if kind == 'view' and operation_id in {'create_or_alter', 'recreate'}:
            return rendered({
                'name': 'CDE_UI_VIEW', 'confirmation': str(name),
                'definition': 'SELECT 1 AS VALUE FROM RDB$DATABASE',
                'columns': '[{"name": "VALUE"}]',
            })
        if kind == 'shadow':
            return rendered({
                'number': 7, 'mode': 'AUTO', 'conditional': False,
                'filename': '/var/lib/firebird/data/cde_ui_preview.shd',
                'confirmation': str(name), 'preserve_files': True,
            })
        if operation_id in {'grant', 'revoke'} and kind in {
                'table', 'view', 'column', 'procedure', 'function',
                'external-function', 'package', 'sequence', 'exception'}:
            from pgadmin.cdeadmin.providers.firebird.object_privileges import (
                allowed_privileges,
            )
            return rendered({
                'principal_kind': 'USER', 'principal': 'CDE_UI_READER',
                'privileges': json.dumps([allowed_privileges(kind)[0]]),
                'confirmation': 'CDE_UI_READER',
            })
        if kind == 'sequence':
            return rendered({
                'name': 'CDE_UI_SEQUENCE', 'start': '10', 'restart': '10',
                'increment': '1', 'current': '10',
                'description': 'Sequence preview comment',
                'confirmation': str(name),
            })
        if kind == 'package':
            return rendered({
                'name': 'CDE_UI_PACKAGE',
                'header': 'BEGIN PROCEDURE UI_PROBE; END',
                'body': 'BEGIN PROCEDURE UI_PROBE AS BEGIN END END',
                'sql_security': 'INHERIT',
                'description': 'Package preview comment',
                'confirmation': str(name),
            })
        if kind == 'column' and operation_id in {'alter', 'comment'}:
            return rendered({'action': 'POSITION', 'position': 1,
                             'description': 'Column preview comment'})
        if kind in {'authentication-mapping', 'global-authentication-mapping'}:
            return rendered({
                'name': str(name) if operation_id == 'create_or_alter' else
                'CDE_UI_MAPPING_PREVIEW',
                'using_mode': 'PLUGIN', 'plugin': 'Srp256',
                'from_type': 'USER', 'from_any': False,
                'from_name': 'CDE_UI_UNMATCHED', 'to_type': 'ROLE',
                'to_name': 'CDE_UI_ROLE',
                'description': 'Browser mapping comment',
                'confirmation': str(name),
            })
        if operation_id == 'create':
            object_name = f'cdeadmin_ui_{kind.replace("-", "_")}_probe'
            values = {'name': object_name}
            if kind == 'table':
                values['columns'] = json.dumps([
                    {'name': 'ID', 'column_mode': 'STORED',
                     'data_type': 'INTEGER',
                     'constraints': [{'kind': 'PRIMARY KEY'}]},
                    {'name': 'VALUE_TEXT', 'column_mode': 'STORED',
                     'data_type': 'VARCHAR', 'length': 80},
                ])
            elif kind == 'view':
                values['query'] = (
                    'SELECT CUSTOMER_ID, NAME FROM CUSTOMERS'
                )
            elif kind == 'index':
                values.update({
                    'table': 'CUSTOMERS', 'columns': '["NAME"]',
                })
            elif kind == 'column':
                values.update({
                    'table': 'CUSTOMERS', 'column_mode': 'STORED',
                    'data_type': 'VARCHAR', 'length': 80,
                })
            elif kind == 'constraint':
                values.update({
                    'table': 'CUSTOMERS',
                    'properties': json.dumps({
                        'kind': 'CHECK',
                        'expression': 'CUSTOMER_ID > 0',
                    }),
                })
            elif kind == 'domain':
                values['data_type'] = 'VARCHAR(80)'
            elif kind == 'collation':
                values.update({'character_set': 'UTF8',
                               'base_collation': 'UNICODE'})
            elif kind == 'trigger':
                values.update({
                    'table': 'CUSTOMERS', 'timing': 'BEFORE',
                    'events': '["INSERT"]', 'body': 'BEGIN END',
                })
            elif kind == 'procedure':
                values['body'] = 'BEGIN END'
            elif kind == 'function':
                values.update({
                    'returns': 'INTEGER', 'body': 'BEGIN RETURN 1; END',
                })
            elif kind == 'package':
                values.update({
                    'header': 'BEGIN PROCEDURE UI_PROBE; END',
                    'body': 'BEGIN PROCEDURE UI_PROBE AS BEGIN END END',
                })
            elif kind == 'exception':
                values['message'] = 'CDEadmin browser preview exception'
            elif kind == 'user':
                values['password'] = 'ui-preview-only'
            elif kind == 'external-function':
                values.update({'entrypoint': 'owned_value',
                               'module_name': 'cde_owned_udf'})
            elif kind == 'blob-filter':
                values.update({'input_subtype': -81, 'output_subtype': 1,
                               'entrypoint': 'owned_uppercase',
                               'module_name': 'owned_filter'})
            elif kind == 'role':
                values.update({
                    'description': 'Browser role creation preview',
                    'system_privileges': '["USER_MANAGEMENT"]',
                })
            return rendered(values)
        if operation_id == 'alter':
            values_by_kind = {
                'character-set': {'default_collation': 'UTF8'},
                'external-function': {'entrypoint': 'owned_other',
                                      'module_name': 'cde_owned_udf'},
                'view': {
                    'query': 'SELECT CUSTOMER_ID, NAME FROM CUSTOMERS',
                },
                'domain': {'data_type': 'VARCHAR(120)'},
                'sequence': {'restart': '2', 'increment': '1'},
                'trigger': {
                    'timing': 'BEFORE', 'events': '["INSERT"]',
                    'body': 'BEGIN END',
                },
                'procedure': {'body': 'BEGIN END'},
                'function': {
                    'returns': 'INTEGER',
                    'body': 'BEGIN RETURN 1; END',
                },
                'package': {
                    'header': 'PROCEDURE UI_PROBE;',
                    'body': 'PROCEDURE UI_PROBE AS BEGIN END',
                },
                'exception': {
                    'message': 'CDEadmin browser replacement exception',
                },
                'role': {
                    'description': 'Browser role alteration preview',
                    'system_privileges': '["USER_MANAGEMENT"]',
                },
            }
            if kind in values_by_kind:
                return rendered(values_by_kind[kind])
        if kind == 'role' and operation_id in {'grant', 'revoke'}:
            values = {'member': 'SYSDBA', 'member_kind': 'USER'}
            if operation_id == 'revoke':
                values['confirmation'] = str(name)
            return rendered(values)
        if kind == 'privilege' and operation_id in {'grant', 'revoke'}:
            values = {
                'principal': 'SYSDBA', 'object_type': 'TABLE',
                'object_name': 'CUSTOMERS', 'privileges': '["SELECT"]',
            }
            if operation_id == 'revoke':
                values['confirmation'] = str(name)
            return rendered(values)
    if engine_id == 'mariadb':
        if kind == 'user' and operation_id == 'alter':
            return rendered({
                'authentication_mode': 'PASSWORD',
                'password': 'ui-preview-replacement',
            })
        if kind == 'role' and operation_id in {
                'grant', 'revoke', 'set_default'}:
            values = {
                'member': 'cdeadmin_qa_user@%', 'member_kind': 'USER',
            }
            if operation_id == 'grant':
                values['admin_option'] = False
            elif operation_id == 'revoke':
                values.update({
                    'admin_option_only': False,
                    'confirmation': str(name),
                })
            return rendered(values)
        if kind == 'replication-channel':
            if operation_id == 'create':
                return rendered({
                    'name': 'cdeadmin_ui_replication_probe',
                    'master_host': '127.0.0.1',
                    'master_user': 'replicator', 'master_port': '2',
                    'use_gtid': 'NO', 'master_ssl': 'OFF',
                    'verify_server_certificate': 'OFF',
                    'demote_to_slave': 'OFF',
                })
            if operation_id == 'alter':
                return rendered({'connect_retry': '5'})
            if operation_id == 'start':
                return rendered({'thread': 'ALL', 'until_mode': 'NONE'})
            if operation_id == 'stop':
                return rendered({'thread': 'ALL'})
            if operation_id == 'reset':
                return rendered({
                    'delete_connection': True, 'confirmation': str(name),
                })
        if kind == 'system-variable' and operation_id == 'set_global':
            native = _provider_native(target, engine_id)
            return rendered({
                'value_mode': 'VALUE',
                'value': str(native.get('global_value', '')),
            })
        if kind == 'session' and operation_id in {
                'terminate_query', 'terminate_connection'}:
            native = _provider_native(target, engine_id)
            return rendered({
                'termination_mode': 'SOFT',
                'confirmation': str(native.get('id', '')),
            })
        if kind == 'binary-log' and operation_id == 'purge_before':
            return rendered({'confirmation': str(name)})
    if operation_id == 'create':
        object_name = f'cdeadmin_ui_{kind.replace("-", "_")}_probe'
        values = {
            'name': (object_name + '.sqlite' if kind == 'database' else
                     object_name),
        }
        if kind == 'table':
            values.update({
                'parent': 'main',
                'columns': json.dumps([
                    {'name': 'id', 'type': 'INTEGER', 'primary_key': True},
                    {'name': 'value', 'type': 'TEXT'},
                ]),
            })
        elif kind in {'view', 'materialized-view'}:
            values.update({
                'parent': 'main',
                'query': 'SELECT id, value FROM qualification',
            })
        elif kind == 'index':
            values.update({
                'parent': 'main',
                'table': 'main.qualification',
                'columns': '["value"]',
            })
        elif kind == 'sequence':
            values.update({
                'parent': 'main', 'start': '1', 'increment': '1',
            })
        elif kind == 'type':
            values.update({
                'parent': 'main', 'type_kind': 'ENUM',
                'enum_values': '["one", "two"]', 'fields': '[]',
            })
        elif kind == 'macro':
            values.update({
                'parent': 'main', 'parameters': '[]',
                'expression': '1',
            })
        elif kind == 'materialization':
            values.update({
                'database': 'main',
                'select': 'SELECT count(*) AS item_count FROM qualification',
            })
        elif kind == 'secret':
            values.update({
                'secret_type': 'HTTP',
                'scope': 'https://cdeadmin.invalid',
                'properties': '{"bearer_token":"ui-preview-only"}',
            })
        elif kind == 'column':
            values.update({
                'parent': 'main',
                'table': 'main.qualification',
                'data_type': 'TEXT',
            })
        elif kind == 'constraint':
            values.update({
                'parent': 'main',
                'table': 'main.qualification',
                'properties': json.dumps({
                    'kind': 'UNIQUE', 'columns': ['value'],
                }),
            })
        elif kind == 'trigger':
            values.update({
                'parent': 'main',
                'table': 'main.qualification',
                'timing': 'AFTER',
                'body': 'BEGIN SELECT 1; END',
            })
        elif kind in {'virtual-table', 'fts-table'}:
            values.update({
                'module': 'rtree' if kind == 'virtual-table' else 'fts5',
                'columns': (
                    '["id", "min_x", "max_x", "min_y", "max_y"]'
                    if kind == 'virtual-table' else '["content", "category"]'
                ),
            })
        if engine_id in {'mysql', 'mariadb'}:
            schema = 'cdeadmin_demo'
            if kind in {
                    'table', 'view', 'materialized-view', 'index', 'column',
                    'constraint',
                    'trigger', 'procedure', 'function', 'event'}:
                values['parent'] = schema
            if kind in {'view', 'materialized-view'}:
                values['query'] = (
                    'SELECT id, value FROM cdeadmin_demo.qualification'
                )
            elif kind in {'index', 'column', 'constraint', 'trigger'}:
                values['table'] = 'cdeadmin_demo.qualification'
            if kind == 'trigger':
                values.update({
                    'timing': 'BEFORE', 'events': '["INSERT"]',
                    'body': (
                        "SET NEW.value = COALESCE(NEW.value, 'ui-preview')"
                    ),
                })
            elif kind == 'procedure':
                values.update({
                    'parameters': '[]', 'return_parameters': '[]',
                    'body': 'BEGIN SELECT 1; END',
                })
            elif kind == 'function':
                values.update({
                    'parameters': '[]', 'return_parameters': '[]',
                    'returns': 'INTEGER', 'body': 'RETURN 1',
                })
            elif kind == 'event':
                values.update({
                    'schedule': 'EVERY 1 DAY', 'body': 'SELECT 1',
                })
            elif kind == 'package' and engine_id == 'mariadb':
                values.update({
                    'header': 'PROCEDURE cdeadmin_ui_probe();',
                    'body': (
                        'PROCEDURE cdeadmin_ui_probe() BEGIN SELECT 1; END'
                    ),
                })
            elif kind == 'role':
                values['members'] = '[]'
            elif kind == 'user':
                values.update({
                    'host': '%', 'password': 'ui-preview-only',
                })
            elif kind == 'plugin':
                values['library'] = 'cdeadmin_ui_preview.so'
        return rendered(values)
    if operation_id == 'alter':
        if kind == 'table':
            return rendered({
                'add_columns': '[{"name":"ui_note","type":"TEXT"}]',
            })
        if kind == 'macro':
            return rendered({
                'parameters': '[]', 'expression': '2',
            })
        if kind == 'database':
            return rendered({'user_version': '53'})
        if engine_id in {'mysql', 'mariadb'} and kind == 'materialized-view':
            return rendered({
                'query': (
                    'SELECT id FROM cdeadmin_demo.qualification'
                ),
            })
        if engine_id == 'mariadb' and kind == 'sequence':
            return rendered({'restart': '2', 'increment': '1'})
        if engine_id == 'mariadb' and kind == 'package':
            return rendered({
                'header': 'PROCEDURE cdeadmin_ui_probe();',
                'body': (
                    'PROCEDURE cdeadmin_ui_probe() BEGIN SELECT 2; END'
                ),
            })
        if engine_id in {'mysql', 'mariadb'} and kind == 'event':
            return rendered({'schedule': 'EVERY 2 DAY'})
        if engine_id == 'mysql' and kind == 'user':
            return rendered({
                'password': 'ui-preview-replacement', 'active': 'true',
            })
        return rendered({'properties': '{}'})
    if operation_id == 'rename':
        return rendered({'new_name': f'{name}_ui_renamed'})
    if operation_id == 'drop':
        return rendered({'confirmation': str(name)})
    if operation_id == 'insert':
        return rendered({
            'values': '{"id":900001,"value":"ui-preview"}',
        })
    if operation_id == 'execute' and kind == 'extension':
        return rendered({'action': 'LOAD'})
    if engine_id in {'mysql', 'mariadb'} and operation_id in {
            'grant', 'revoke'}:
        values = {
            'principal': 'cdeadmin_qa_user@%',
            'object_type': 'TABLE',
            'object_name': 'cdeadmin_demo.qualification',
            'privileges': '["SELECT"]',
        }
        if operation_id == 'grant':
            values['grant_option'] = False
        else:
            values['confirmation'] = 'cdeadmin_qa_user@%'
        return rendered(values)
    return {}


def _enumerate_operations(catalog, resource_kinds=None, operation_ids=None):
    operations = []
    admitted_kinds = set(resource_kinds or ())
    admitted_operations = set(operation_ids or ())
    for descriptor in catalog.get('objects') or []:
        kind = descriptor.get('resource_kind')
        if admitted_kinds and kind not in admitted_kinds:
            continue
        if kind == 'database':
            # Database create/connect/edit/alter/drop/remove are deliberately
            # rendered by the provider's separate database-target forms.
            # Opening these operations in the generic object editor would
            # lose the retained target identity and is forbidden by design.
            continue
        for operation in descriptor.get('operations') or []:
            if kind == 'server' and operation.get('operation_id') == 'inspect':
                # Server properties already have a dedicated endpoint task;
                # provider-owned server actions remain eligible here.
                continue
            if (
                admitted_operations and
                operation.get('operation_id') not in admitted_operations
            ):
                continue
            operations.append({
                **operation, 'resource_kind': kind,
                'form': operation.get('form') or {},
            })
    return operations


def _context_command_id(engine_id, kind, operation):
    if kind == 'database':
        return f'database.{engine_id}.{operation}'
    if kind == 'server':
        return f'endpoint.{engine_id}.{operation}'
    return f'resource.{engine_id}.{kind}.{operation}'


def _wait_for_operation(wait, operation):
    wait.until(lambda value: operation['title'] in value.find_element(
        'tag name', 'body'
    ).text)


def _validation_observation(driver, wait, operation):
    required = next((
        field for field in operation['form'].get('fields') or []
        if field.get('required') and field.get('control') in {
            'text', 'code', 'json', 'number',
        }
    ), None)
    if required is None:
        return {
            'state': 'not_applicable',
            'reason': 'form has no clearable required field',
        }
    control = wait.until(lambda value: visible_named_control(
        value, required['label']
    ))
    control.click()
    ActionChains(driver).key_down(Keys.CONTROL).send_keys(
        'a'
    ).key_up(Keys.CONTROL).send_keys(Keys.BACKSPACE).perform()
    button = wait.until(lambda value: visible_named_control(
        value, 'Validate and preview'
    ))
    if button.is_enabled():
        button.click()
        wait.until(lambda value: any(
            item.is_displayed()
            for item in value.find_elements('css selector', '[role="alert"]')
        ))
    if driver.find_elements(
            'css selector', '[aria-label="Provider plan preview"]'):
        raise RuntimeError(
            f'{operation["operation_id"]} planned empty required input'
        )
    apply_button = visible_named_control(driver, 'Apply provider plan')
    if apply_button is not None and apply_button.is_enabled():
        raise RuntimeError(
            f'{operation["operation_id"]} enabled Apply for invalid input'
        )
    return {
        'state': 'observed',
        'field_id': required['field_id'],
        'validation_kind': 'required',
        'plan_present': False,
        'apply_enabled': False,
        'provider_operation_executed': False,
    }


def run(options):
    driver = create_driver(options)
    driver.set_script_timeout(max(120, options.timeout * 10))
    wait = WebDriverWait(driver, options.timeout)
    results = []
    failures = []
    probe = None
    try:
        _prepare_tree(driver, wait, options)
        print('read focused provider catalog and context commands', flush=True)
        probe = _workspace_probe(driver, options.resource_kinds)
        if probe.get('probe_error'):
            raise RuntimeError(probe['probe_error'])
        if probe.get('status') != 200:
            raise RuntimeError('provider workspace request did not succeed')
        if probe.get('engine_id') != options.engine_id:
            raise RuntimeError('provider workspace engine does not match')
        catalog = probe['catalog']
        resources = probe['resources']
        commands = {
            item['command_id']: item for item in probe['context_commands']
            if item.get('enabled')
        }
        for operation in _enumerate_operations(
                catalog, options.resource_kinds, options.operation_ids):
            kind = operation['resource_kind']
            operation_id = operation['operation_id']
            if operation.get('execution_available') is not True:
                failures.append({
                    'resource_kind': kind, 'operation_id': operation_id,
                    'error': 'Declared operation is blocked in workspace',
                    'blockers': operation.get('blockers', []),
                })
                continue
            target = next((item for item in resources if
                           item.get('resource_kind') == kind and
                           (not operation.get('target_resource_names') or
                            item.get('display_name') in operation[
                                'target_resource_names'])), None)
            if (
                options.engine_id == 'mariadb' and
                kind == 'system-variable' and
                operation_id == 'set_global'
            ):
                target = next((
                    item for item in resources
                    if item.get('resource_kind') == kind and
                    str(_provider_native(item, options.engine_id).get(
                        'read_only', '')).upper() == 'NO' and
                    'GLOBAL' in str(_provider_native(
                        item, options.engine_id
                    ).get(
                        'variable_scope', '')).upper()
                ), None)
            if target is None and operation.get('target_required') is False:
                parent = next((
                    item for item in resources
                    if item.get('resource_kind') == 'database'
                ), None)
                if parent is not None:
                    target = {
                        **parent,
                        'resource_id': f'cdeadmin-create-scope:{kind}',
                        'resource_kind': kind,
                    }
            if target is None and kind == 'database':
                target = {'resource_id': probe['database_target_id'],
                          'display_name': options.database}
            if target is None:
                failures.append({
                    'resource_kind': kind, 'operation_id': operation_id,
                    'error': 'no live target of this provider kind',
                })
                continue
            command_id = _context_command_id(
                options.engine_id, kind, operation_id
            )
            # Database create has endpoint ownership; database inspect is the
            # dedicated properties command.  Other object operations must be
            # present on a resource popup exactly as rendered.
            if (
                kind != 'database' and command_id not in commands and
                operation.get('target_required') is not False
            ):
                failures.append({
                    'resource_kind': kind, 'operation_id': operation_id,
                    'command_id': command_id,
                    'error': 'Object Explorer context command is missing',
                })
                continue
            try:
                _open_focused_form(
                    driver, operation, target, probe['database_target_id']
                )
                _wait_for_operation(wait, operation)
                controls = assert_form_controls(
                    wait, operation['form'].get('fields') or []
                )
                layout = layout_observation(driver)
                accessibility = accessibility_observation(
                    driver, wait, operation, controls
                )
                directory = options.output_root / command_id
                image = directory / (
                    f'initial-{options.width}x{options.height}-'
                    f'{evidence_variant(options)}.png'
                )
                digest = screenshot(driver, image)

                close_workspace_with_keyboard(driver, wait)
                cancelled = cancellation_observation(driver)
                cancelled_image = directory / (
                    f'cancelled-{options.width}x{options.height}-'
                    f'{evidence_variant(options)}.png'
                )
                cancelled_digest = screenshot(driver, cancelled_image)

                _open_focused_form(
                    driver, operation, target, probe['database_target_id']
                )
                _wait_for_operation(wait, operation)
                assert_form_controls(
                    wait, operation['form'].get('fields') or []
                )
                validation = _validation_observation(
                    driver, wait, operation
                )
                validation_image = None
                validation_digest = None
                validation_layout = None
                if validation['state'] == 'observed':
                    validation_layout = layout_observation(driver)
                    validation_image = directory / (
                        f'validation-error-{options.width}x{options.height}-'
                        f'{evidence_variant(options)}.png'
                    )
                    validation_digest = screenshot(
                        driver, validation_image
                    )
                close_workspace(driver, wait)

                _open_focused_form(
                    driver, operation, target, probe['database_target_id']
                )
                _wait_for_operation(wait, operation)
                assert_form_controls(
                    wait, operation['form'].get('fields') or []
                )
                preview = {'state': 'delegated_to_grid'} if (
                    kind, operation_id
                ) in GRID_IDENTITY_OPERATIONS else None
                preview_layout = None
                if preview is None:
                    values = _preview_values(
                        kind, operation, target, options.engine_id
                    )
                    fill_fields(wait, [
                        f'{label}={value}' for label, value in values.items()
                    ])
                    button = wait.until(lambda value: visible_named_control(
                        value, 'Validate and preview'
                    ))
                    wait.until(lambda _driver: button.is_enabled())
                    button.click()
                    plan = wait.until(lambda value: value.find_element(
                        'css selector', '[aria-label="Provider plan preview"]'
                    ))
                    preview_value = json.loads(plan.text)
                    preview = {
                        'state': preview_value.get('state'),
                        'execution_available': preview_value.get(
                            'execution_available'
                        ),
                        'provider_plan_observed': True,
                    }
                    if preview['state'] != 'ready' or (
                            preview['execution_available'] is not True):
                        raise RuntimeError('provider plan is not ready')
                    preview_layout = layout_observation(driver)
                    preview_image = directory / (
                        f'plan-preview-{options.width}x{options.height}-'
                        f'{evidence_variant(options)}.png'
                    )
                    preview_digest = screenshot(driver, preview_image)
                    preview['screenshot'] = {
                        'path': str(preview_image),
                        'sha256': preview_digest,
                    }
                close_workspace(driver, wait)
                result = {
                    'resource_kind': kind,
                    'operation_id': operation_id,
                    'command_id': command_id,
                    'form_id': operation['form'].get('form_id'),
                    'declared_field_count': len(
                        operation['form'].get('fields') or []
                    ),
                    'rendered_field_count': len(controls),
                    'layout': layout,
                    'state_layouts': {
                        'initial': layout,
                        'validation_error': validation_layout,
                        'plan_preview': preview_layout,
                    },
                    'accessibility': accessibility,
                    'cancellation': cancelled,
                    'validation': validation,
                    'preview': preview,
                    'screenshots': {
                        'initial': {
                            'path': str(image), 'sha256': digest,
                        },
                        'cancelled': {
                            'path': str(cancelled_image),
                            'sha256': cancelled_digest,
                        },
                    },
                    'values_recorded': False,
                }
                if validation_image is not None:
                    result['screenshots']['validation_error'] = {
                        'path': str(validation_image),
                        'sha256': validation_digest,
                    }
                if preview.get('screenshot'):
                    result['screenshots']['plan_preview'] = preview.pop(
                        'screenshot'
                    )
                results.append(result)
            except Exception as exc:
                failure_image = options.output_root / command_id / (
                    f'failure-{options.width}x{options.height}-'
                    f'{evidence_variant(options)}.png'
                )
                failure_image.parent.mkdir(parents=True, exist_ok=True)
                driver.save_screenshot(str(failure_image))
                failures.append({
                    'resource_kind': kind, 'operation_id': operation_id,
                    'command_id': command_id,
                    'error_type': type(exc).__name__, 'error': str(exc),
                    'traceback': traceback.format_exc(),
                    'screenshot': str(failure_image),
                })
                try:
                    close_workspace(driver, wait)
                except Exception as recovery_error:
                    failures.append({
                        'error': 'Cannot recover the browser after failure',
                        'error_type': type(recovery_error).__name__,
                        'remaining_operations_not_tested': True,
                    })
                    break
    except Exception:
        options.output_root.mkdir(parents=True, exist_ok=True)
        driver.save_screenshot(str(options.output_root / 'setup-failure.png'))
        raise
    finally:
        _quit_driver(driver)
    expected = len(_enumerate_operations(
        probe['catalog'], options.resource_kinds, options.operation_ids
    )) if probe and (
        probe.get('catalog')
    ) else 0
    return {
        'schema': 'cdeadmin.provider-object-form-gate.v1',
        'captured_at': datetime.now(timezone.utc).isoformat(),
        'engine_id': options.engine_id,
        'interface_id': options.interface_id,
        'reference_version': options.reference_version,
        'server_label': options.server,
        'database_label': options.database,
        'viewport': f'{options.width}x{options.height}',
        'theme': options.theme,
        'font_scale': options.font_scale,
        'evidence_variant': evidence_variant(options),
        'resource_kind_filter': sorted(set(options.resource_kinds or ())),
        'operation_id_filter': sorted(set(options.operation_ids or ())),
        'expected_operation_count': expected,
        'passed_operation_count': len(results),
        'results': results,
        'failures': failures,
        'resource_page_complete': bool(
            probe and probe.get('resource_complete')
        ),
        'database_operations_delegated_to_lifecycle_forms': True,
        'cancelled_form_count': sum(
            item['cancellation']['dialog_dismissed'] for item in results
        ),
        'validation_observed_form_count': sum(
            item['validation']['state'] == 'observed' for item in results
        ),
        'validation_not_applicable_form_count': sum(
            item['validation']['state'] == 'not_applicable'
            for item in results
        ),
        'observed_context_command_ids': sorted({
            item['command_id'] for item in (probe or {}).get(
                'context_commands', []
            )
        }),
        'credential_values_exported': False,
        'complete': expected > 0 and len(results) == expected and not failures,
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
        'expected_operation_count': result['expected_operation_count'],
        'passed_operation_count': result['passed_operation_count'],
        'failed_operations': [
            f"{item.get('resource_kind', 'browser')}."
            f"{item.get('operation_id', 'recovery')}"
            for item in result['failures']
        ],
        'output': str(options.summary_output),
    }, indent=2, sort_keys=True))
    return 0 if result['complete'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
