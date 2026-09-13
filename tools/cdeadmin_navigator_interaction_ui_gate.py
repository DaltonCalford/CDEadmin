#!/usr/bin/env python3
"""Read-only real-browser navigator interaction and screenshot gate.

Does not apply DDL, edit data or modify engine credentials. Use the isolated
QA server to avoid changing a user's explorer/inspector presentation state.
"""
import argparse
import json
import os
import traceback
from pathlib import Path

from selenium.webdriver import ActionChains
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as expected

from cdeadmin_firebird_ui_form_gate import create_driver
from cdeadmin_ui_evidence import (
    expand, wait_for_tree_item, complete_endpoint_prompt, visible_menu_label,
    visible_named_control,
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://127.0.0.1:5052')
    parser.add_argument('--engine', required=True)
    parser.add_argument('--server', required=True)
    parser.add_argument('--database', required=True)
    parser.add_argument('--object', default='work_orders')
    parser.add_argument('--group', default='Tables')
    parser.add_argument('--object-kind', default='Table')
    parser.add_argument('--edit-tab')
    parser.add_argument('--property-tab')
    parser.add_argument('--expect-field', nargs=2, action='append', default=[])
    parser.add_argument('--browse-data', action='store_true')
    parser.add_argument('--object-editor', action='store_true')
    parser.add_argument('--password-env')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--browser-binary')
    parser.add_argument('--width', type=int, default=1440)
    parser.add_argument('--height', type=int, default=1000)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    password = os.environ.get(args.password_env) if args.password_env else None
    driver = create_driver(args)
    result = {'engine': args.engine, 'checks': [], 'mutations_applied': False}
    try:
        wait = WebDriverWait(driver, 45)
        driver.get(args.url.rstrip('/') + '/browser/')
        wait.until(expected.presence_of_element_located((
            By.CSS_SELECTOR, 'button[aria-label="Data Explorer"]')))
        wait.until(expected.invisibility_of_element_located((
            By.ID, 'pg-spinner')))
        driver.find_element(By.CSS_SELECTOR,
                            'button[aria-label="Data Explorer"]').click()
        for parent, child in [('Connectors', args.engine),
                              (args.engine, args.server),
                              (args.server, args.database)]:
            result['stage'] = 'expand ' + parent
            expand(wait, parent)
            wait_for_tree_item(wait, child)
        result['stage'] = 'expand database'
        expand(wait, args.database)
        complete_endpoint_prompt(driver, password)
        wait_for_tree_item(wait, args.group)
        driver.save_screenshot(str(args.output / '01-database-tree.png'))
        group = wait_for_tree_item(wait, args.group)
        result['stage'] = 'group context menu'
        # Center before the pointer action: this navigator virtualizes rows,
        # so implicit WebDriver scrolling can recycle the original element.
        driver.execute_script('arguments[0].scrollIntoView({block:"center"})',
                              group)
        group = wait_for_tree_item(wait, args.group)
        ActionChains(driver).context_click(group).perform()
        new_label = f'New {args.object_kind}'
        wait.until(lambda d: visible_menu_label(d, new_label))
        result['checks'].append(f'new-{args.object_kind.lower()}-group-menu')
        driver.save_screenshot(str(args.output / '02-group-menu.png'))
        wait.until(lambda d: d.execute_script(
            'return !!document.activeElement?.closest("[role=menu]")'))
        ActionChains(driver).send_keys(Keys.ESCAPE).perform()
        wait.until(lambda d: not visible_menu_label(d, new_label))
        driver.execute_script('arguments[0].scrollIntoView({block:"center"})',
                              wait_for_tree_item(wait, args.group))
        expand(wait, args.group)
        result['stage'] = 'table group contents'
        obj = wait_for_tree_item(wait, args.object)
        obj.click()
        result['stage'] = 'single-click inspector'
        wait.until(lambda d: visible_menu_label(d, 'Object properties task'))
        result['checks'].append('single-click-inspector')
        driver.save_screenshot(str(args.output / '03-object-selection.png'))
        ActionChains(driver).context_click(obj).perform()
        labels = (f'Alter {args.object_kind} {args.object}',
                  f'Drop {args.object_kind} {args.object}')
        for label in labels:
            wait.until(lambda d, label=label: visible_menu_label(d, label))
        result['checks'].append('alter-drop-object-menu')
        driver.save_screenshot(str(args.output / '04-object-menu.png'))
        wait.until(lambda d: d.execute_script(
            'return !!document.activeElement?.closest("[role=menu]")'))
        ActionChains(driver).send_keys(Keys.ESCAPE).perform()
        wait.until(lambda d: not visible_menu_label(d, labels[-1]))
        result['stage'] = 'double-click object browser'
        driver.execute_script('arguments[0].scrollIntoView({block:"center"})',
                              wait_for_tree_item(wait, args.object))
        ActionChains(driver).double_click(
            wait_for_tree_item(wait, args.object)).perform()
        wait.until(lambda d: 'Object Browser' in d.find_element(
            By.TAG_NAME, 'body').text)
        result['checks'].append('double-click-object-browser')
        if args.object_editor:
            wait.until(expected.visibility_of_element_located((
                By.CSS_SELECTOR,
                '[role="tablist"][aria-label="Selected object operations"]')))
            properties = wait.until(expected.visibility_of_element_located((
                By.CSS_SELECTOR,
                '[role="tablist"][aria-label="Object properties sections"]')))
            if any(item.is_displayed() for item in driver.find_elements(
                    By.CSS_SELECTOR,
                    '[aria-label="Engine administration tasks"]')):
                raise AssertionError('Object editor exposes unrelated tasks')
            result['object_property_tabs'] = [
                item.text for item in properties.find_elements(
                    By.CSS_SELECTOR, '[role="tab"]')]
            result['checks'].append('object-specific-editor-tabs')
        if args.property_tab:
            def property_tab(d):
                return next((tab for tab in d.find_elements(
                    By.CSS_SELECTOR,
                    '[aria-label="Object properties sections"] [role="tab"]')
                    if tab.is_displayed() and tab.text == args.property_tab),
                    False)

            wait.until(property_tab).click()
            result['checks'].append('native-property-tab-' + args.property_tab)
        driver.save_screenshot(str(args.output / '05-object-browser.png'))
        if args.edit_tab:
            tabs = driver.find_elements(By.CSS_SELECTOR,
                                        '[role="tab"]')
            selected = next(tab for tab in tabs if tab.is_displayed() and
                            tab.text == args.edit_tab)
            selected.click()
            wait.until(lambda d: visible_named_control(
                d, 'Validate and preview'))
            for label_text, expected_value in args.expect_field:
                def matching_field(d):
                    for label in d.find_elements(By.TAG_NAME, 'label'):
                        if label.text.strip() != label_text:
                            continue
                        field_id = label.get_attribute('for')
                        if not field_id:
                            continue
                        field = d.find_element(By.ID, field_id)
                        if field.get_attribute('value') == expected_value:
                            return field
                    return False

                field = wait.until(matching_field)
                if not field.is_displayed() or not field.is_enabled():
                    raise AssertionError('Native field is not editable')
            result['verified_fields'] = [label for label, _ in
                                         args.expect_field]
            driver.save_screenshot(str(args.output / '06-object-editor.png'))
            result['checks'].append('native-edit-form-visible-no-mutation')
        if args.browse_data:
            result['stage'] = 'native data rows'
            wait.until(lambda d: visible_named_control(
                d, 'Browse object data')).click()
            wait.until(lambda d: visible_named_control(
                d, 'Load rows')).click()
            grid = wait.until(expected.visibility_of_element_located((
                By.CSS_SELECTOR,
                '[aria-label="Provider table or view rows"]')))
            result['checks'].append('native-data-grid-visible')
            result['grid_row_count'] = grid.get_attribute('aria-rowcount')
            if int(result['grid_row_count'] or 0) < 3:
                raise AssertionError(
                    'Sample grid contains no native data rows')
            driver.save_screenshot(str(args.output / '06-native-data.png'))
            wait.until(lambda d: visible_named_control(
                d, 'Close data session')).click()
            wait.until(lambda d: not visible_named_control(
                d, 'Close data session').is_enabled())
            result['checks'].append('read-session-closed')
        result['status'] = 'passed'
    except Exception as error:
        result.update(status='failed', error_type=type(error).__name__,
                      error=str(error), traceback=traceback.format_exc())
        result['tree_labels'] = [element.text for element in
                                 driver.find_elements(
                                     By.CSS_SELECTOR, '.file-name')
                                 if element.is_displayed()]
        driver.save_screenshot(str(args.output / 'failure.png'))
    finally:
        result['legacy_selector_status'] = driver.execute_async_script("""
            const done = arguments[arguments.length - 1];
            const app = window.pgAdmin;
            if (!app) { done({unavailable: true}); return; }
            fetch('/sqleditor/new_connection_dialog', {headers: {
              [app.csrf_token_header]: app.csrf_token
            }}).then(async r => done({status: r.status,
              response: r.ok ? null : await r.text()}))
              .catch(e => done({error: String(e)}));
        """)
        result['browser_errors'] = [
            entry for entry in driver.get_log('browser')
            if entry['level'] == 'SEVERE']
        if result.get('status') == 'passed' and result['browser_errors']:
            result['interaction_status'] = 'passed'
            result['status'] = 'failed'
            result['error'] = 'Severe browser errors prevent QA acceptance'
        driver.quit()
        (args.output / 'result.json').write_text(json.dumps(result, indent=2))
    print(json.dumps(result))
    return 0 if result['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
