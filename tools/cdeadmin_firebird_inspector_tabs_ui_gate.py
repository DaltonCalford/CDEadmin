#!/usr/bin/env python3
"""Verify inspector geometry and keyboard navigation on a Firebird endpoint."""

import json
import os
import traceback

from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait

from cdeadmin_firebird_database_lifecycle_ui_gate import (
    arguments, _configure_shared, shared,
)
from cdeadmin_firebird_ui_form_gate import create_driver, screenshot


def run(options):
    password = os.environ.get(options.password_env)
    if not password:
        raise RuntimeError('The isolated endpoint credential is required')
    options.endpoint_password_env = options.password_env
    _configure_shared(password)
    browser = create_driver(options)
    browser.set_script_timeout(120)
    wait = WebDriverWait(browser, options.timeout)
    result = {'complete': False, 'checks': [], 'failures': [],
              'scope': 'inspector-tabs-on-firebird-endpoint',
              'read_only': True, 'font_scale': options.font_scale,
              'theme': options.theme, 'credential_values_exported': False}
    selector = '[role="tablist"][aria-label="Inspector and Toolbox pages"]'

    def tabs():
        return browser.find_element(By.CSS_SELECTOR, selector).find_elements(
            By.CSS_SELECTOR, '[role="tab"]')

    def active():
        return browser.find_element(By.CSS_SELECTOR,
                                    selector + ' [aria-selected="true"]')

    try:
        browser.get(options.url.rstrip('/') + '/browser/')
        shared._prepare_tree(browser, wait, options, options.database)
        wait.until(lambda _driver: len(tabs()) >= 2)
        for width in (280, 520):
            splitter = browser.find_element(
                By.CSS_SELECTOR, '[role="separator"]'
                '[aria-label="Resize Inspector"]')
            resize_key = Keys.ARROW_LEFT if width == 280 else Keys.ARROW_RIGHT
            splitter.send_keys(Keys.SHIFT, resize_key * 40, Keys.NULL)
            wait.until(lambda _driver: splitter.get_attribute(
                'aria-valuenow') == str(width))
            labels = [tab.text for tab in tabs()]
            for index in range(len(labels)):
                selected = active()
                if index == 0:
                    selected.send_keys(Keys.HOME)
                else:
                    selected.send_keys(Keys.ARROW_RIGHT)
                wait.until(lambda _driver: active().text == labels[index])
                wait.until(lambda _driver: browser.execute_script(
                    'return document.activeElement === arguments[0]',
                    active()))
                geometry = browser.execute_script('''
                  const list = document.querySelector(arguments[0]);
                  const bounds = list.getBoundingClientRect();
                  const tabs = [...list.querySelectorAll('[role="tab"]')];
                  const selected = tabs.find(t =>
                    t.getAttribute('aria-selected') === 'true');
                  const rect = selected.getBoundingClientRect();
                  const panel = document.getElementById(
                    selected.getAttribute('aria-controls'));
                  return {labels: tabs.map(t => t.textContent.trim()),
                    selected: selected.textContent.trim(),
                    widths: tabs.map(t => t.getBoundingClientRect().width),
                    unshrunk: tabs.every(t =>
                      t.scrollWidth <= t.clientWidth + 2),
                    nonoverlapping: tabs.every((t, i) => !i ||
                      t.getBoundingClientRect().left >=
                      tabs[i-1].getBoundingClientRect().right - 1),
                    selectedVisible: rect.left >= bounds.left - 1 &&
                      rect.right <= bounds.right + 1,
                    labelledPanel: !!panel &&
                      panel.getAttribute('aria-labelledby') === selected.id,
                    singleTabStop: tabs.filter(t => t.tabIndex === 0).length
                      === 1, stripWidth: bounds.width};
                ''', selector)
                assert geometry['unshrunk'], 'Tab text is clipped'
                assert geometry['nonoverlapping'], 'Tab buttons overlap'
                assert geometry['labelledPanel'], 'Panel association is absent'
                assert geometry['singleTabStop'], 'Roving tab stop is invalid'
                # A very long label may exceed the entire narrow inspector;
                # it must retain its full width and remain scrollable.
                if geometry['widths'][index] <= geometry['stripWidth']:
                    assert geometry['selectedVisible'], (
                        'Keyboard-selected tab was not scrolled into view')
                path = options.output_root / (
                    f'inspector-{width}-page-{index}.png')
                digest = screenshot(browser, path, reset_scroll=False)
                result['checks'].append({
                    'width_setting': width, 'geometry': geometry,
                    'screenshot': str(path), 'sha256': digest})
            active().send_keys(Keys.HOME)
        result['complete'] = len(result['checks']) >= 4
    except Exception as exc:
        result['failures'].append({'error_type': type(exc).__name__,
                                   'traceback': traceback.format_exc()})
        path = options.output_root / 'failure.png'
        screenshot(browser, path, reset_scroll=False)
    finally:
        browser.quit()
    return result


def main():
    options = arguments()
    result = run(options)
    options.summary_output.parent.mkdir(parents=True, exist_ok=True)
    options.summary_output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({'complete': result['complete'],
                      'checks': len(result['checks']),
                      'failures': result['failures']}, indent=2))
    return 0 if result['complete'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
