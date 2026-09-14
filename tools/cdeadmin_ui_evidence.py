#!/usr/bin/env python3
##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Capture reproducible evidence for one provider database popup task."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from selenium import webdriver
from selenium.webdriver import ActionChains
from selenium.webdriver.chrome.options import Options as ChromeOptions
from selenium.webdriver.firefox.options import Options as FirefoxOptions
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.common.exceptions import (
    ElementClickInterceptedException,
    StaleElementReferenceException,
    TimeoutException,
)
from selenium.webdriver.support import expected_conditions as expected
from selenium.webdriver.support.ui import Select, WebDriverWait


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://127.0.0.1:5052')
    parser.add_argument('--user')
    parser.add_argument('--password-env')
    parser.add_argument(
        '--desktop-mode', action='store_true',
        help='Use an isolated desktop-mode instance with automatic login.',
    )
    parser.add_argument('--engine', required=True)
    parser.add_argument('--interface-id', required=True)
    parser.add_argument('--reference-version', required=True)
    parser.add_argument('--server', required=True)
    parser.add_argument('--database', required=True)
    parser.add_argument(
        '--open-database', action='store_true',
        help='Expand the database node and complete endpoint verification.',
    )
    parser.add_argument(
        '--endpoint-password-env',
        help='Environment variable containing the endpoint password.',
    )
    parser.add_argument(
        '--server-children-url',
        help='Authenticated server-child URL to probe after database open.',
    )
    parser.add_argument('--action-label')
    parser.add_argument(
        '--action-path', action='append', default=[],
        help=(
            'Visible context-menu label to traverse. Repeat for each nested '
            'menu level, ending with the action to invoke.'
        ),
    )
    parser.add_argument(
        '--action-target', choices=('database', 'server'),
        default='database',
        help='Tree node whose context menu owns the requested action.',
    )
    parser.add_argument('--command-id', default='tree.hierarchy')
    parser.add_argument('--form-id', default='object-explorer')
    parser.add_argument('--state', default='initial')
    parser.add_argument('--theme', default='default')
    parser.add_argument('--expected-text')
    parser.add_argument(
        '--fill-field', action='append', default=[], metavar='LABEL=VALUE',
        help=(
            'Fill a visible control by accessible label. Values are never '
            'written to evidence. Repeat for multiple fields.'
        ),
    )
    parser.add_argument(
        '--submit-button',
        help='Click the visible enabled button with this accessible name.',
    )
    parser.add_argument(
        '--expected-after-submit',
        help='Wait for this visible text after clicking --submit-button.',
    )
    parser.add_argument(
        '--confirm-provider-action', action='store_true',
        help='Accept the provider command confirmation before form QA.',
    )
    parser.add_argument(
        '--expect-action-target-removed', action='store_true',
        help='Require the clicked server/database tree item to be removed.',
    )
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--evidence-output', type=Path, required=True)
    parser.add_argument('--width', type=int, default=1600)
    parser.add_argument('--height', type=int, default=1000)
    parser.add_argument('--timeout', type=int, default=90)
    parser.add_argument('--browser-binary')
    return parser.parse_args()


def browser_binary(explicit=None):
    candidates = [
        explicit,
        os.environ.get('CDEADMIN_QA_CHROME'),
        '/usr/bin/google-chrome',
        '/usr/bin/chromium',
    ]
    flatpak = Path('/var/lib/flatpak/app/com.google.Chrome')
    if flatpak.is_dir():
        candidates.extend(str(item) for item in flatpak.glob(
            'x86_64/stable/*/files/extra/chrome'
        ))
    candidates.extend(('/usr/bin/firefox', '/usr/sbin/firefox'))
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return candidate
    raise RuntimeError('A Chrome or Chromium binary is required')


def named_tree_item(driver, label):
    matches = []
    for item in driver.find_elements(By.CSS_SELECTOR, '.file-name'):
        try:
            if item.is_displayed() and item.text == label:
                matches.append(item)
        except StaleElementReferenceException:
            continue
    return matches[-1] if matches else None


def expand(wait, label):
    def toggle(driver):
        collapsed = driver.find_elements(
            By.CSS_SELECTOR, f'button[aria-label="Expand {label}"]'
        )
        try:
            collapsed = [item for item in collapsed if item.is_displayed()]
        except StaleElementReferenceException:
            return None
        if collapsed:
            return ('expand', collapsed[-1])
        expanded = driver.find_elements(
            By.CSS_SELECTOR, f'button[aria-label="Collapse {label}"]'
        )
        try:
            expanded = [item for item in expanded if item.is_displayed()]
        except StaleElementReferenceException:
            return None
        if expanded:
            return ('expanded', expanded[-1])
        return None

    state, _button = wait.until(toggle)
    if state == 'expand':
        wait.until(expected.invisibility_of_element_located(
            (By.ID, 'pg-spinner')
        ))

        def click_when_ready(driver):
            # Opening an unauthenticated database may replace its row and
            # display verification before WebDriver receives click success.
            # Hand control back so the caller can complete that real prompt.
            if visible_menu_label(driver, 'Verify Endpoint'):
                return True
            current_state = toggle(driver)
            if not current_state or current_state[0] != 'expand':
                return current_state and current_state[0] == 'expanded'
            button = current_state[1]
            try:
                if not button.is_enabled():
                    return False
                button.click()
                return True
            except (ElementClickInterceptedException,
                    StaleElementReferenceException):
                return False

        wait.until(click_when_ready)


def wait_for_tree_item(wait, label):
    """Wait until an asynchronously loaded tree child is mounted."""
    return wait.until(lambda driver: named_tree_item(driver, label))


def _xpath_literal(value):
    """Return a string literal that is safe in an XPath expression."""
    if "'" not in value:
        return f"'{value}'"
    if '"' not in value:
        return f'"{value}"'
    parts = value.split("'")
    return "concat(" + ", \"'\", ".join(
        f"'{part}'" for part in parts
    ) + ")"


def visible_menu_label(driver, label):
    """Find the innermost visible menu element with an exact text label."""
    xpath = f"//*[normalize-space(text())={_xpath_literal(label)}]"
    matches = []
    for element in driver.find_elements(By.XPATH, xpath):
        try:
            if element.is_displayed():
                matches.append(element)
        except StaleElementReferenceException:
            continue
    return matches[-1] if matches else None


def visible_named_control(driver, name):
    """Find one displayed form control by its exact accessible name."""
    matches = []
    for element in driver.find_elements(
        By.CSS_SELECTOR,
        'input, textarea, select, button, [role="button"], [role="combobox"]'
    ):
        try:
            if element.is_displayed() and element.accessible_name == name:
                matches.append(element)
        except StaleElementReferenceException:
            continue
    if matches:
        return matches[-1]
    literal = _xpath_literal(name)
    for button in driver.find_elements(
        By.XPATH, f'.//button[normalize-space(.)={literal}]'
    ):
        if button.is_displayed():
            return button
    for label in driver.find_elements(
        By.XPATH, f'.//label[starts-with(normalize-space(.), {literal})]'
    ):
        if not label.is_displayed():
            continue
        target_id = label.get_attribute('for')
        if target_id:
            target = driver.find_elements(By.ID, target_id)
            if target and target[-1].is_displayed():
                return target[-1]
        descendants = label.find_elements(
            By.CSS_SELECTOR, 'input, textarea, select, [role="checkbox"]'
        )
        if descendants:
            # MUI keeps a checkbox's native input visually hidden inside a
            # visible wrapping label. The label is the rendered, interactive
            # accessibility surface even when Selenium marks that native
            # input itself as not displayed.
            return descendants[-1]
        nested = label.find_elements(
            By.XPATH,
            './following::input[1] | ./following::textarea[1] | '
            './following::select[1]',
        )
        if nested and nested[0].is_displayed():
            return nested[0]
    return None


def _multiple_values(value):
    values = json.loads(value)
    if not isinstance(values, list) or any(
            not isinstance(item, str) for item in values):
        raise ValueError('Multiple selections require a JSON string array')
    if len(set(values)) != len(values):
        raise ValueError('Multiple selections must not contain duplicates')
    return values


def _fill_multiple_options(driver, values):
    options = [option for option in driver.find_elements(
        By.CSS_SELECTOR, '[role="option"]') if option.is_displayed()]
    available = {option.get_attribute('data-value') for option in options}
    if not set(values).issubset(available):
        raise ValueError('Requested selection is not an available option')
    for option in options:
        wanted = option.get_attribute('data-value') in values
        if (option.get_attribute('aria-selected') == 'true') != wanted:
            option.click()
    selected = {option.get_attribute('data-value') for option in
                driver.find_elements(By.CSS_SELECTOR, '[role="option"]')
                if option.is_displayed() and
                option.get_attribute('aria-selected') == 'true'}
    if selected != set(values):
        raise RuntimeError('Multiple selections did not match the request')
    driver.switch_to.active_element.send_keys(Keys.ESCAPE)


def ensure_data_explorer(wait):
    """Open a closed navigator through its activity control."""
    def locate(driver):
        for control in driver.find_elements(By.CSS_SELECTOR, (
                'button[aria-label="Expand Connectors"], '
                'button[aria-label="Collapse Connectors"]')):
            if control.is_displayed():
                return 'open'
        for control in driver.find_elements(By.CSS_SELECTOR, (
                '[data-cdeadmin-qa-key="activity-activity.data"]')):
            if control.is_displayed() and control.is_enabled():
                return control
        return None

    def ready(driver):
        try:
            return locate(driver)
        except StaleElementReferenceException:
            return None

    state = wait.until(ready)
    if state != 'open':
        if state.get_attribute('data-selected') != 'true':
            state.click()
        wait.until(lambda driver: ready(driver) == 'open')


def fill_fields(wait, values, control_root=None):
    """Enter non-recorded QA values into explicitly named controls."""
    for assignment in values:
        if '=' not in assignment:
            raise RuntimeError('--fill-field must use LABEL=VALUE')
        label, value = assignment.split('=', 1)
        if not label:
            raise RuntimeError('--fill-field label must not be empty')
        located = {}

        def find_control(driver, name=label):
            root = control_root(driver) if callable(control_root) else (
                control_root or driver)
            control = visible_named_control(root, name)
            # Native value setters bypass disabled fieldsets. Do not enter
            # values until asynchronous metadata initialization has finished.
            if control is not None and (
                    not control.is_enabled() or
                    control.get_attribute('aria-disabled') == 'true'):
                return None
            if control is not None:
                located['driver'] = driver
            return control

        control = wait.until(find_control)
        driver = located['driver']
        tag_name = control.tag_name.lower()
        role = control.get_attribute('role') or ''
        if tag_name == 'select':
            select = Select(control)
            if select.is_multiple:
                selections = _multiple_values(value)
                select.deselect_all()
                for selection in selections:
                    select.select_by_value(selection)
                continue
            try:
                select.select_by_value(value)
            except Exception:
                select.select_by_visible_text(value)
        elif role == 'combobox':
            control.click()
            listbox = wait.until(lambda current: next((
                box for box in current.find_elements(
                    By.CSS_SELECTOR, '[role="listbox"]')
                if box.is_displayed()), None))
            if listbox.get_attribute('aria-multiselectable') == 'true':
                _fill_multiple_options(driver, _multiple_values(value))
                continue

            def matching_option(current_driver):
                expected_value = value.replace('_', ' ').casefold()
                for option in current_driver.find_elements(
                        By.CSS_SELECTOR, '[role="option"]'):
                    native_value = option.get_attribute('data-value')
                    if option.is_displayed() and (
                        str(native_value) == value or
                        option.text.strip().casefold() == expected_value
                    ):
                        return option
                return None

            wait.until(matching_option).click()
        elif (control.get_attribute('type') == 'checkbox' or
              role == 'checkbox'):
            if value.lower() not in {'true', 'false'}:
                raise ValueError('Checkbox value must be true or false')
            checked = (control.is_selected() if tag_name == 'input' else
                       control.get_attribute('aria-checked') == 'true')
            if checked != (value.lower() == 'true'):
                control.click()
        elif tag_name in {'input', 'textarea'}:
            # send_keys() can interleave with a React controlled-input
            # rerender and silently lose characters.  Invoke the native value
            # setter once, then emit the events React owns and verify the
            # complete value before allowing a destructive live QA action.
            driver.execute_script(
                """
                const element = arguments[0];
                const value = arguments[1];
                const prototype = element.tagName === 'TEXTAREA'
                  ? window.HTMLTextAreaElement.prototype
                  : window.HTMLInputElement.prototype;
                const setter = Object.getOwnPropertyDescriptor(
                  prototype, 'value').set;
                setter.call(element, value);
                element.dispatchEvent(new Event('input', {bubbles: true}));
                element.dispatchEvent(new Event('change', {bubbles: true}));
                """,
                control, value,
            )
            wait.until(lambda _driver, expected_value=value:
                       control.get_attribute('value') == expected_value)
        else:
            control.click()
            control.send_keys(Keys.CONTROL, 'a')
            control.send_keys(Keys.BACKSPACE)
            control.send_keys(value)


def complete_endpoint_prompt(driver, password, timeout=5):
    """Complete endpoint verification if the selected node opens its dialog."""
    try:
        password_input = WebDriverWait(driver, timeout).until(
            expected.visibility_of_element_located((
                By.CSS_SELECTOR,
                '[role=dialog] input[type=password], '
                '.MuiDialog-root input[type=password]',
            ))
        )
    except TimeoutException:
        return False
    if not password:
        raise RuntimeError(
            'database requested a password; provide '
            '--endpoint-password-env'
        )
    password_input.send_keys(password)
    confirm = WebDriverWait(driver, timeout).until(
        lambda value: visible_named_control(value, 'OK')
    )
    confirm.click()
    WebDriverWait(driver, timeout).until(
        expected.invisibility_of_element(password_input)
    )
    return True


def invoke_context_action(
        wait, driver, database, labels, endpoint_password=None,
        endpoint_prompt_timeout=5):
    """Open a database context menu and traverse its visible label path."""
    def open_selected_context_menu(target=None):
        driver.execute_script(
            """
            const supplied = arguments[0];
            if (supplied) supplied.click();
            // Selection from click can be queued by the double-click handler.
            // A supplied target must never fall back to the previously
            // selected server/database while that click is pending.
            const target = supplied?.closest('.file-entry') || supplied ||
              document.querySelector('.file-entry[aria-selected="true"]');
            if (!target) throw new Error('selected tree row is unavailable');
            const bounds = target.getBoundingClientRect();
            target.dispatchEvent(new MouseEvent('contextmenu', {
              bubbles: true,
              cancelable: true,
              view: window,
              clientX: bounds.left + Math.min(24, bounds.width / 2),
              clientY: bounds.top + Math.min(12, bounds.height / 2),
            }));
            """,
            target,
        )

    open_selected_context_menu(database)
    if complete_endpoint_prompt(
            driver, endpoint_password, timeout=endpoint_prompt_timeout):
        ActionChains(driver).send_keys(Keys.ESCAPE).perform()
        open_selected_context_menu()
    selected_context = driver.execute_script(
        """
        const tree = window.pgAdmin?.Browser?.tree;
        const item = tree?.selected?.();
        const data = item ? tree.itemData(item) : null;
        return {
          type: data?._type || null,
          label: data?.label || data?._label || null,
          commands: (data?.cde_context_actions || [])
            .map(action => action.command_id),
        };
        """
    )
    print(
        'selected context ' + json.dumps(selected_context, sort_keys=True),
        flush=True,
    )
    for position, label in enumerate(labels):
        print(f'find context menu item {label}', flush=True)
        element = wait.until(
            lambda value, item=label: visible_menu_label(value, item)
        )
        if position == len(labels) - 1:
            clickable = driver.execute_script(
                """
                return arguments[0].closest(
                  '[role="menuitem"], button, a'
                ) || arguments[0];
                """,
                element,
            )
            wait.until(lambda _driver: clickable.is_enabled())
            ActionChains(driver).move_to_element(clickable).click().perform()
            complete_endpoint_prompt(
                driver, endpoint_password, timeout=endpoint_prompt_timeout
            )
        else:
            ActionChains(driver).move_to_element(element).perform()


def _control_evidence(driver):
    """Inventory rendered controls without recording entered values."""
    controls = []
    selectors = (
        'button, input, textarea, [role="button"], [role="combobox"], '
        '[role="checkbox"], [role="radio"], [role="tab"], select'
    )
    for position, element in enumerate(driver.find_elements(
            By.CSS_SELECTOR, selectors)):
        if not element.is_displayed():
            continue
        controls.append({
            'position': position,
            'tag': element.tag_name,
            'role': element.get_attribute('role') or '',
            'accessible_name': element.accessible_name or '',
            'input_type': element.get_attribute('type') or '',
            'enabled': element.is_enabled(),
            'displayed': True,
        })
    return controls


def _selected_tree_evidence(driver):
    """Return non-secret identity and command data for the selected node."""
    return driver.execute_script(
        """
        const tree = window.pgAdmin?.Browser?.tree;
        const item = tree?.selected?.();
        const data = item ? tree.itemData(item) : null;
        if (!data) return null;
        return {
          id: data._id ?? null,
          type: data._type ?? null,
          label: data.label ?? data._label ?? null,
          engine_id: data.cde_engine_id ?? null,
          profile_id: data.cde_profile_id ?? null,
          actions: (data.cde_context_actions || []).map((action) => ({
            command_id: action.command_id,
            arguments: action.arguments,
          })),
        };
        """
    )


def _workspace_contract_probe(driver):
    """Read the authenticated provider workspace's non-secret form surface."""
    return driver.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        const app = window.pgAdmin;
        const tree = app?.Browser?.tree;
        let item = tree?.selected?.();
        while (item && tree.itemData(item)?._type !== 'server') {
          item = tree.hasParent(item) ? tree.parent(item) : null;
        }
        const data = item ? tree.itemData(item) : null;
        const node = app?.Browser?.Nodes?.server;
        if (!item || !data || !node) {
          done({probe_error: 'provider endpoint node is unavailable'});
          return;
        }
        const url = node.generate_url(item, 'cde_workspace', data, true);
        const headers = {'Content-type': 'application/json'};
        if (app.csrf_token_header && app.csrf_token) {
          headers[app.csrf_token_header] = app.csrf_token;
        }
        fetch(url, {credentials: 'same-origin', headers: headers})
          .then(async response => ({
            status: response.status,
            body: await response.json(),
          }))
          .then(result => {
            const workspace = result.body?.data || {};
            const targets = workspace.database_targets || {};
            const databaseObject = (workspace.visual_admin?.objects || [])
              .find(candidate => candidate.resource_kind ===
                targets.forms?.lifecycle_resource_kind);
            done({
              url: url,
              status: result.status,
              error: result.body?.errormsg ?? null,
              active_target_id: targets.active_target_id ?? null,
              target_ids: (targets.targets || []).map(target =>
                target.target_id),
              form_modes: Object.keys(targets.forms?.forms || {}),
              lifecycle_resource_kind:
                targets.forms?.lifecycle_resource_kind ?? null,
              lifecycle_operation_ids: (databaseObject?.operations || [])
                .map(operation => operation.operation_id),
            });
          })
          .catch(error => done({probe_error: String(error)}));
        """
    )


def _write_evidence(args, driver, runtime_probe=None):
    screenshot_digest = hashlib.sha256(args.output.read_bytes()).hexdigest()
    evidence = {
        'schema': 'cdeadmin.ui-form-evidence.v1',
        'captured_at': datetime.now(timezone.utc).isoformat(),
        'engine_id': args.engine,
        'interface_id': args.interface_id,
        'reference_version': args.reference_version,
        'server_label': args.server,
        'database_label': args.database,
        'command_id': args.command_id,
        'form_id': args.form_id,
        'action_label': args.action_label,
        'action_path': args.action_path,
        'action_target': args.action_target,
        'state': args.state,
        'viewport': {'width': args.width, 'height': args.height},
        'theme': args.theme,
        'expected_text': args.expected_text,
        'expected_text_observed': (
            not args.expected_text or
            args.expected_text in driver.find_element(By.TAG_NAME, 'body').text
        ),
        'expected_after_submit': args.expected_after_submit,
        'expected_after_submit_observed': (
            not args.expected_after_submit or
            args.expected_after_submit in driver.find_element(
                By.TAG_NAME, 'body'
            ).text
        ),
        'selected_tree_node': _selected_tree_evidence(driver),
        'workspace_invocations': driver.execute_script(
            'return window.__cdeadminQaWorkspaceInvocations || [];'
        ),
        'workspace_component_props': driver.execute_script(
            """
            const element = document.querySelector('[role="dialog"]');
            if (!element) return null;
            const key = Object.keys(element).find((candidate) =>
              candidate.startsWith('__reactFiber$'));
            let fiber = key ? element[key] : null;
            while (fiber) {
              if (fiber.elementType?.name === 'ProviderWorkspaceContent') {
                return {
                  initial_tab: fiber.memoizedProps?.initialTab || null,
                  initial_context: fiber.memoizedProps?.initialContext || {},
                };
              }
              fiber = fiber.return;
            }
            return null;
            """
        ),
        'controls': _control_evidence(driver),
        'screenshot': {
            'filename': args.output.name,
            'sha256': screenshot_digest,
        },
    }
    if runtime_probe is not None:
        evidence['runtime_probe'] = runtime_probe
    args.evidence_output.parent.mkdir(parents=True, exist_ok=True)
    args.evidence_output.write_text(
        json.dumps(evidence, indent=2, sort_keys=True) + '\n',
        encoding='utf-8',
    )


def main():
    args = arguments()
    password = None
    if not args.desktop_mode:
        if not args.user or not args.password_env:
            raise SystemExit(
                '--user and --password-env are required in server mode'
            )
        password = os.environ.get(args.password_env)
        if not password:
            raise SystemExit(f'{args.password_env} must be set')
    binary = browser_binary(args.browser_binary)
    if 'firefox' in Path(binary).name:
        options = FirefoxOptions()
        options.binary_location = binary
        options.add_argument('-headless')
        driver = webdriver.Firefox(options=options)
    else:
        options = ChromeOptions()
        options.binary_location = binary
        for option in (
            '--headless=new', '--no-sandbox', '--disable-dev-shm-usage',
            '--force-device-scale-factor=1',
        ):
            options.add_argument(option)
        driver = webdriver.Chrome(options=options)
    driver.set_window_size(args.width, args.height)
    wait = WebDriverWait(driver, args.timeout)
    runtime_probe = None
    try:
        if args.desktop_mode:
            print('desktop automatic login', flush=True)
            driver.get(args.url.rstrip('/') + '/browser/')
        else:
            print('login', flush=True)
            driver.get(args.url.rstrip('/') + '/login')
            wait.until(expected.presence_of_element_located(
                (By.NAME, 'email')
            )).send_keys(args.user)
            driver.find_element(By.NAME, 'password').send_keys(password)
            driver.find_element(
                By.CSS_SELECTOR, 'button[type=submit]'
            ).click()
        wait.until(lambda value: '/browser/' in value.current_url)
        driver.execute_script(
            """
            const callbacks =
              window.pgAdmin?.Browser?.Nodes?.server?.callbacks;
            if (callbacks?.open_cde_workspace &&
                !callbacks.open_cde_workspace.__cdeadminQaWrapped) {
              const original = callbacks.open_cde_workspace;
              const wrapped = function(args, tab, context) {
                window.__cdeadminQaWorkspaceInvocations ||= [];
                window.__cdeadminQaWorkspaceInvocations.push({
                  tab: tab || null,
                  context: context || {},
                });
                return original.call(this, args, tab, context);
              };
              wrapped.__cdeadminQaWrapped = true;
              callbacks.open_cde_workspace = wrapped;
            }
            """
        )

        hierarchy = [
            ('Connectors', args.engine),
            (args.engine, args.server),
        ]
        if args.action_target == 'database' or args.open_database:
            hierarchy.append((args.server, args.database))
        for label, child_label in hierarchy:
            print(f'expand {label}', flush=True)
            expand(wait, label)
            wait_for_tree_item(wait, child_label)
        server = wait_for_tree_item(wait, args.server)
        database = (
            wait_for_tree_item(wait, args.database)
            if args.action_target == 'database' or args.open_database
            else None
        )
        if args.open_database:
            endpoint_password = os.environ.get(
                args.endpoint_password_env or ''
            )
            ActionChains(driver).context_click(database).perform()
            complete_endpoint_prompt(driver, endpoint_password)
            ActionChains(driver).send_keys(Keys.ESCAPE).perform()
            print(f'expand database {args.database}', flush=True)
            expand(wait, args.database)
            wait.until(lambda driver: driver.find_elements(
                By.CSS_SELECTOR,
                f'button[aria-label="Collapse {args.database}"]',
            ))
            wait.until(lambda driver: not driver.find_elements(
                By.CSS_SELECTOR,
                '.alert-danger, [role=alert].MuiAlert-standardError',
            ))
            runtime_probe = _workspace_contract_probe(driver)
            if args.server_children_url:
                child_probe = driver.execute_async_script(
                    """
                    const url = arguments[0];
                    const databaseLabel = arguments[1];
                    const done = arguments[arguments.length - 1];
                    const app = window.pgAdmin || {};
                    const headers = {'Content-type': 'application/json'};
                    if (app.csrf_token_header && app.csrf_token) {
                      headers[app.csrf_token_header] = app.csrf_token;
                    }
                    const fetchOptions = {
                      credentials: 'same-origin', headers: headers,
                    };
                    fetch(url, fetchOptions)
                      .then(async response => ({
                        status: response.status,
                        body: await response.json(),
                      }))
                      .then(async first => {
                        const entries = first.body.data || [];
                        const target = entries.find(
                          entry => entry.label === databaseLabel
                        );
                        if (!target?.children_url) {
                          return {
                            server_children_status: first.status,
                            database_target_found: false,
                          };
                        }
                        const secondResponse = await fetch(
                          target.children_url,
                          fetchOptions
                        );
                        const second = await secondResponse.json();
                        return {
                          server_children_status: first.status,
                          database_target_found: true,
                          database_children_status: secondResponse.status,
                          database_children_success: second.success,
                          database_children_error: second.errormsg || null,
                          database_child_count: Array.isArray(second.data) ?
                            second.data.length : null,
                          csrf_header_available: Boolean(
                            app.csrf_token_header && app.csrf_token
                          ),
                        };
                      })
                      .then(done)
                      .catch(error => done({probe_error: String(error)}));
                    """,
                    args.server_children_url,
                    args.database,
                )
                runtime_probe['navigator_probe'] = child_probe
        action_path = args.action_path or (
            [args.action_label] if args.action_label else []
        )
        if action_path:
            print(f'open {" > ".join(action_path)}', flush=True)
            action_node = database if args.action_target == 'database' else (
                server
            )
            invoke_context_action(
                wait, driver, action_node, action_path,
                os.environ.get(args.endpoint_password_env or ''),
            )
        if args.confirm_provider_action:
            confirm = wait.until(
                lambda value: visible_named_control(value, 'Continue')
            )
            wait.until(lambda _driver: confirm.is_enabled())
            confirm.click()
        if action_path:
            complete_endpoint_prompt(
                driver,
                os.environ.get(args.endpoint_password_env or ''),
            )
        if args.expected_text:
            wait.until(lambda value: args.expected_text in value.find_element(
                By.TAG_NAME, 'body'
            ).text)
        if args.fill_field:
            fill_fields(wait, args.fill_field)
        if args.submit_button:
            submit = wait.until(
                lambda driver: visible_named_control(
                    driver, args.submit_button
                )
            )
            wait.until(lambda _driver: submit.is_enabled())
            submit.click()
            if args.expected_after_submit:
                wait.until(
                    lambda value: args.expected_after_submit in
                    value.find_element(By.TAG_NAME, 'body').text
                )
        if args.expect_action_target_removed:
            wait.until(expected.staleness_of(action_node))
        time.sleep(1)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        if not driver.save_screenshot(str(args.output)):
            raise RuntimeError('browser did not save the screenshot')
        _write_evidence(args, driver, runtime_probe)
        print(f'captured {args.output}', flush=True)
    except Exception:
        failure = args.output.with_name(args.output.stem + '-failure.png')
        failure.parent.mkdir(parents=True, exist_ok=True)
        driver.save_screenshot(str(failure))
        print(f'failure screenshot {failure}', flush=True)
        raise
    finally:
        driver.quit()


if __name__ == '__main__':
    main()
