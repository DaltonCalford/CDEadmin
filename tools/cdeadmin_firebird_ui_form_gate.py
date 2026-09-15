#!/usr/bin/env python3
##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Exercise every Firebird database service form in a real browser.

The default gate stops at provider plan preview and never changes the selected
database.  ``--apply-live`` is reserved for an orchestrator-provisioned,
disposable database target.  It additionally proves that the rendered Apply
path returns the provider's native, synchronous completion observation.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import traceback
from datetime import datetime, timezone
from pathlib import Path

from selenium import webdriver
from selenium.common.exceptions import TimeoutException
from selenium.webdriver import ActionChains
from selenium.webdriver.chrome.options import Options as ChromeOptions
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.firefox.options import Options as FirefoxOptions
from selenium.webdriver.support import expected_conditions as expected
from selenium.webdriver.support.ui import WebDriverWait

if __package__:
    from .cdeadmin_ui_evidence import (
        ensure_data_explorer,
        _control_evidence,
        browser_binary,
        complete_endpoint_prompt,
        expand,
        fill_fields,
        invoke_context_action,
        visible_named_control,
        wait_for_tree_item,
    )
else:
    from cdeadmin_ui_evidence import (
        ensure_data_explorer,
        _control_evidence,
        browser_binary,
        complete_endpoint_prompt,
        expand,
        fill_fields,
        invoke_context_action,
        visible_named_control,
        wait_for_tree_item,
    )


ROOT = Path(__file__).resolve().parents[1]
CATALOG_PATH = (
    ROOT / 'web/pgadmin/cdeadmin/visual_admin/portfolio_catalog.json'
)
MENU_GROUP_LABELS = {
    'backup': 'Backup',
    'restore': 'Restore',
    'diagnostics': 'Diagnostics and verification',
    'maintenance': 'Maintenance',
    'availability': 'Availability',
}
LIFECYCLE_OPERATIONS = frozenset({'inspect', 'create', 'alter', 'drop'})
FAULT_FIXTURE_OPERATIONS = frozenset({
    'activate_shadow', 'fixup_database',
})
NORMAL_COMPLETION_ORDER = (
    'set_page_cache_size', 'set_sweep_interval',
    'set_space_reservation', 'set_write_mode', 'set_access_mode',
    'set_sql_dialect', 'remove_linger', 'set_replica_mode',
    'upgrade_database', 'database_statistics', 'validate_database',
    'sweep_database', 'backup_logical', 'restore_logical',
    'backup_physical', 'restore_physical', 'repair_database',
    'shutdown_database', 'bring_online',
)
PREVIEW_VALUES = {
    'activate_shadow': {
        'First shadow filename': '/var/lib/firebird/data/cde-preview.shd',
        'Confirm shadow filename': '/var/lib/firebird/data/cde-preview.shd',
        'The original database is stopped or isolated': True,
    },
    'backup_logical': {
        'Backup filename on the Firebird server': (
            '/var/lib/firebird/data/cdeadmin-ui-form-gate.fbk'
        ),
    },
    'restore_logical': {
        'Backup filename on the Firebird server': (
            '/var/lib/firebird/data/cdeadmin-ui-form-gate.fbk'
        ),
        'Restored database filename on the Firebird server': (
            '/var/lib/firebird/data/cdeadmin-ui-form-gate-restore.fdb'
        ),
    },
    'backup_physical': {
        'Physical backup filename on the Firebird server': (
            '/var/lib/firebird/data/cdeadmin-ui-form-gate.nbk'
        ),
    },
    'restore_physical': {
        'Ordered backup files': (
            '["/var/lib/firebird/data/cdeadmin-ui-form-gate.nbk"]'
        ),
        'Restored database filename on the Firebird server': (
            '/var/lib/firebird/data/cdeadmin-ui-form-gate-restore.fdb'
        ),
    },
    'set_page_cache_size': {'Default page buffers': '4096'},
    'set_sweep_interval': {'Sweep interval (transactions; 0 disables '
                           'automatic sweep)': '20000'},
}

# These are deliberately UI-reachable, provider-rejected values.  Values are
# used only in the live browser and are never copied into evidence artifacts.
# A form omitted from this mapping has no meaningful invalid input that its
# rendered controls permit (for example, a required select with a valid
# provider-owned default).  Such forms are recorded explicitly as N/A.
VALIDATION_CASES = {
    'backup_logical': {
        'field_id': 'backup_file',
        'kind': 'required',
        'expected': 'Backup filename on the Firebird server is required.',
    },
    'restore_logical': {
        'field_id': 'backup_file',
        'kind': 'required',
        'expected': 'Backup filename on the Firebird server is required.',
    },
    'backup_physical': {
        'field_id': 'backup_file',
        'kind': 'required',
        'expected': (
            'Physical backup filename on the Firebird server is required.'
        ),
    },
    'restore_physical': {
        'field_id': 'backup_files',
        'kind': 'required',
        'expected': (
            'Ordered backup files is required.'
        ),
    },
    'validate_database': {
        'field_id': 'lock_timeout',
        'kind': 'minimum',
        'label': 'Lock timeout in seconds',
        'invalid_value': '-2',
        'valid_value': '10',
        'expected': 'Lock timeout in seconds is below its minimum.',
    },
    'sweep_database': {
        'field_id': 'parallel_workers',
        'kind': 'minimum',
        'label': 'Parallel workers',
        'invalid_value': '0',
        'valid_value': '1',
        'expected': 'Parallel workers is below its minimum.',
    },
    'database_statistics': {
        'field_id': 'tables',
        'kind': 'json',
        'label': 'Restrict to tables (JSON array)',
        'invalid_value': '{invalid-json',
        'valid_value': '[]',
        'expected': (
            'Restrict to tables (JSON array) must contain valid JSON.'
        ),
    },
    'shutdown_database': {
        'field_id': 'shutdown_timeout',
        'kind': 'minimum',
        'label': 'Timeout in seconds',
        'invalid_value': '-1',
        'valid_value': '0',
        'expected': 'Timeout in seconds is below its minimum.',
    },
    'set_page_cache_size': {
        'field_id': 'page_buffers',
        'kind': 'required',
        'expected': 'Default page buffers is required.',
    },
    'set_sweep_interval': {
        'field_id': 'sweep_interval',
        'kind': 'required',
        'expected': (
            'Sweep interval (transactions; 0 disables automatic sweep) '
            'is required.'
        ),
    },
}

VALIDATION_NOT_APPLICABLE_REASON = (
    'The form exposes only provider-constrained selections with valid '
    'defaults, optional unconstrained text, or no fields; the rendered UI '
    'has no meaningful invalid user input to submit.'
)


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://127.0.0.1:5052')
    parser.add_argument('--engine', default='Firebird')
    parser.add_argument('--server', default='localhost')
    parser.add_argument('--database', default='cdeadmin_demo.fdb')
    parser.add_argument('--endpoint-password-env')
    parser.add_argument('--output-root', type=Path, required=True)
    parser.add_argument('--summary-output', type=Path, required=True)
    parser.add_argument('--manifest-output', type=Path)
    parser.add_argument(
        '--record-existing-summary', type=Path,
        help='Create occurrence/manifest records from an existing batch '
             'summary without opening a browser.',
    )
    parser.add_argument('--width', type=int, default=1600)
    parser.add_argument('--height', type=int, default=1000)
    parser.add_argument(
        '--theme', choices=('default', 'high-contrast'), default='default',
        help='Presentation theme applied without changing user preferences.',
    )
    parser.add_argument(
        '--font-scale', type=int, choices=(100, 150, 200, 300),
        default=100,
        help='Root rem scale applied for this evidence occurrence.',
    )
    parser.add_argument('--timeout', type=int, default=45)
    parser.add_argument('--browser-binary')
    parser.add_argument(
        '--apply-live', action='store_true',
        help=(
            'Apply each plan to an orchestrator-owned disposable database. '
            'Never use this option with a retained sample or user database.'
        ),
    )
    parser.add_argument(
        '--server-file-prefix',
        help=(
            'Unique server-side filename prefix for live backup/restore '
            'artifacts. Required with --apply-live.'
        ),
    )
    parser.add_argument(
        '--operation', action='append', default=[],
        help='Run only this operation ID. Repeat to select multiple forms.',
    )
    return parser.parse_args()


def firebird_service_forms(catalog_path=CATALOG_PATH):
    """Return exact Firebird service operations and their owned forms."""
    catalog = json.loads(catalog_path.read_text(encoding='utf-8'))
    operations = catalog['operation_profiles']['firebird_database']
    forms = catalog['forms']
    result = []
    for operation in operations:
        if operation['operation_id'] in LIFECYCLE_OPERATIONS:
            continue
        form_id = operation['form_id']
        result.append({
            **operation,
            'form': forms[form_id],
        })
    return result


def create_driver(options):
    binary = browser_binary(options.browser_binary)
    if 'firefox' in Path(binary).name:
        browser_options = FirefoxOptions()
        browser_options.binary_location = binary
        browser_options.add_argument('-headless')
        download_dir = getattr(options, 'download_dir', None)
        if download_dir:
            browser_options.set_preference(
                'browser.download.dir', str(Path(download_dir).resolve())
            )
            browser_options.set_preference(
                'browser.download.folderList', 2
            )
            browser_options.set_preference(
                'browser.helperApps.neverAsk.saveToDisk',
                'text/csv,application/json',
            )
        driver = webdriver.Firefox(options=browser_options)
    else:
        browser_options = ChromeOptions()
        browser_options.binary_location = binary
        for option in (
            '--headless=new', '--no-sandbox', '--disable-dev-shm-usage',
            '--force-device-scale-factor=1',
        ):
            browser_options.add_argument(option)
        browser_options.set_capability(
            'goog:loggingPrefs', {'browser': 'ALL'}
        )
        download_dir = getattr(options, 'download_dir', None)
        if download_dir:
            browser_options.add_experimental_option('prefs', {
                'download.default_directory': str(
                    Path(download_dir).resolve()
                ),
                'download.prompt_for_download': False,
                'download.directory_upgrade': True,
                'safebrowsing.enabled': True,
            })
        driver = webdriver.Chrome(options=browser_options)
    driver.set_window_size(options.width, options.height)
    return driver


def selected_database_actions(driver):
    return driver.execute_script(
        """
        const tree = window.pgAdmin?.Browser?.tree;
        const item = tree?.selected?.();
        const data = item ? tree.itemData(item) : null;
        return data?.cde_context_actions || [];
        """
    )


def evidence_variant(options):
    if options.theme != 'default':
        return options.theme
    if options.font_scale != 100:
        return f'scaled-font-{options.font_scale}'
    return 'default'


def apply_presentation(driver, wait, options):
    """Apply an ephemeral evidence presentation without saving preferences."""
    wait.until(lambda value: bool(value.execute_script(
        'return document.documentElement.dataset.cdeadminProfile'
    )))
    if options.theme == 'high-contrast':
        driver.execute_script(
            r"""
            window.dispatchEvent(new CustomEvent(
              'cdeadmin:accessibility-safe-mode',
              {detail: {enabled: true}}
            ));
            """
        )
        wait.until(lambda value: value.execute_script(
            'return document.documentElement.dataset.cdeadminSafeMode'
        ) == 'true')
    driver.execute_script(
        """
        document.documentElement.style.setProperty(
          'font-size', arguments[0] + '%', 'important'
        );
        document.documentElement.dataset.cdeadminEvidenceFontScale =
          String(arguments[0]);
        """,
        options.font_scale,
    )
    expected_pixels = 16 * options.font_scale / 100
    wait.until(lambda value: abs(float(value.execute_script(
        "return parseFloat(getComputedStyle(document.documentElement)"
        ".fontSize)"
    )) - expected_pixels) < 0.2)


def layout_observation(driver):
    observation = driver.execute_script(
        """
        const root = document.documentElement;
        const dialog = document.querySelector('[role="dialog"]');
        if (!dialog) return null;
        const rect = dialog.getBoundingClientRect();
        const style = getComputedStyle(dialog);
        const parent = dialog.parentElement;
        const parentStyle = parent ? getComputedStyle(parent) : null;
        const grandparent = parent?.parentElement;
        const grandparentStyle = grandparent ?
          getComputedStyle(grandparent) : null;
        return {
          viewport_width: window.innerWidth,
          viewport_height: window.innerHeight,
          root_font_pixels: parseFloat(getComputedStyle(root).fontSize),
          presentation_profile: root.dataset.cdeadminProfile || '',
          safe_mode: root.dataset.cdeadminSafeMode === 'true',
          dialog_position: style.position,
          dialog_transform: style.transform,
          dialog_css_top: style.top,
          dialog_css_left: style.left,
          dialog_margin: style.margin,
          dialog_class: dialog.className,
          parent_display: parentStyle?.display || '',
          parent_position: parentStyle?.position || '',
          parent_transform: parentStyle?.transform || '',
          parent_css_top: parentStyle?.top || '',
          parent_css_left: parentStyle?.left || '',
          parent_align_items: parentStyle?.alignItems || '',
          parent_justify_content: parentStyle?.justifyContent || '',
          parent_class: parent?.className || '',
          parent_bounds: parent ? (() => {
            const bounds = parent.getBoundingClientRect();
            return {left: bounds.left, top: bounds.top, right: bounds.right,
              bottom: bounds.bottom, width: bounds.width,
              height: bounds.height};
          })() : null,
          grandparent_class: grandparent?.className || '',
          grandparent_display: grandparentStyle?.display || '',
          grandparent_align_items: grandparentStyle?.alignItems || '',
          grandparent_justify_content:
            grandparentStyle?.justifyContent || '',
          grandparent_bounds: grandparent ? (() => {
            const bounds = grandparent.getBoundingClientRect();
            return {left: bounds.left, top: bounds.top, right: bounds.right,
              bottom: bounds.bottom, width: bounds.width,
              height: bounds.height};
          })() : null,
          dialog_bounds: {
            left: rect.left, top: rect.top,
            right: rect.right, bottom: rect.bottom,
            width: rect.width, height: rect.height,
          },
          dialog_fits_viewport:
            rect.left >= -1 && rect.top >= -1 &&
            rect.right <= window.innerWidth + 1 &&
            rect.bottom <= window.innerHeight + 1,
          document_horizontal_overflow:
            root.scrollWidth > root.clientWidth + 2,
          dialog_horizontal_overflow:
            dialog.scrollWidth > dialog.clientWidth + 2,
        };
        """
    )
    if observation is None:
        raise RuntimeError('provider form dialog is not present')
    if not observation['dialog_fits_viewport']:
        raise RuntimeError(
            'provider form dialog exceeds the viewport: ' +
            json.dumps(observation, sort_keys=True)
        )
    if observation['document_horizontal_overflow']:
        raise RuntimeError('provider form causes document horizontal overflow')
    if observation['dialog_horizontal_overflow']:
        raise RuntimeError('provider form has horizontal content overflow')
    return observation


def _prepare_tree_once(driver, wait, options, password):
    driver.get(options.url.rstrip('/') + '/browser/')
    wait.until(lambda value: '/browser/' in value.current_url)
    apply_presentation(driver, wait, options)
    ensure_data_explorer(wait)
    for label, child in (
        ('Connectors', options.engine),
        (options.engine, options.server),
        (options.server, options.database),
    ):
        expand(wait, label)
        wait_for_tree_item(wait, child)
    database = wait_for_tree_item(wait, options.database)
    ActionChains(driver).context_click(database).perform()
    if complete_endpoint_prompt(driver, password):
        ActionChains(driver).send_keys(Keys.ESCAPE).perform()
        database = wait_for_tree_item(wait, options.database)
        ActionChains(driver).context_click(database).perform()
    actions = selected_database_actions(driver)
    ActionChains(driver).send_keys(Keys.ESCAPE).perform()
    return actions


def prepare_tree(driver, wait, options, password):
    """Open the target tree, retrying only pre-action UI navigation once."""
    for attempt in range(2):
        try:
            return _prepare_tree_once(
                driver, wait, options, password
            )
        except TimeoutException:
            if attempt:
                raise
            # No provider form or mutation has been requested at this point.
            # A fresh browser navigation is therefore a safe recovery from an
            # asynchronous Object Explorer mount that missed its wait window.
            driver.get('about:blank')
    raise RuntimeError('unreachable provider tree retry state')


def assert_field_label_geometry(driver, control):
    """Check compact labels independently of accessible-name existence."""
    geometry = driver.execute_script(
        """
        const field = arguments[0].closest('.MuiFormControl-root');
        const label = field?.querySelector('.MuiInputLabel-root');
        const input = field?.querySelector('.MuiInputBase-root');
        if (!label || !input) return null;
        const bounds = element => {
          const r = element.getBoundingClientRect();
          return {top: r.top, bottom: r.bottom, height: r.height};
        };
        return {label: bounds(label), input: bounds(input),
          label_font_size: parseFloat(getComputedStyle(label).fontSize),
          root_font_size: parseFloat(getComputedStyle(
            document.documentElement).fontSize),
          shrink: label.dataset.shrink === 'true'};
        """, control,
    )
    if geometry is not None:
        if geometry.get('label_font_size', 1) < (
                geometry.get('root_font_size', 1) * 0.9):
            raise RuntimeError('Field label did not follow the text scale')
        label, field = geometry['label'], geometry['input']
        if label['height'] <= 0 or label['bottom'] > field['bottom'] + 0.5:
            raise RuntimeError('Field label extends below its input boundary')
        if geometry['shrink'] and label['bottom'] > (
                field['top'] + label['height'] * 0.6):
            raise RuntimeError('Floated field label overlaps the input text')
    return geometry


def assert_form_controls(wait, fields, draft=None):
    """Verify visible controls and the absence of inactive controls.

    Inactive declarations are recorded explicitly, not counted as rendered
    controls. Call again with the changed draft to qualify an activated branch.
    """
    driver = wait._driver
    values = {}
    for field in fields:
        value = field.get('default')
        if value is None:
            if field.get('control') == 'boolean':
                value = False
            elif field.get('array_editor') or field.get(
                    'control') == 'multiselect':
                value = []
            elif field.get('object_editor'):
                value = {}
            else:
                value = ''
        values[field['field_id']] = value
    values.update(draft or {})
    observed = []
    for field in fields:
        label = field['label']
        if not _draft_field_visible(field, values):
            if field.get('array_editor') or field.get('object_editor'):
                selector = ('[role="group"][aria-label=' +
                            json.dumps(label, ensure_ascii=False) + ']')
                present = any(item.is_displayed() for item in
                              driver.find_elements(By.CSS_SELECTOR, selector))
            else:
                present = visible_named_control(driver, label) is not None
            if present:
                raise RuntimeError(
                    f'inactive field {field["field_id"]!r} is displayed')
            observed.append({'field_id': field['field_id'], 'label': label,
                             'control': field['control'], 'visible': False,
                             'absence_verified': True})
            continue
        print(f'  rendered field {field["field_id"]}: {label}', flush=True)
        try:
            if field.get('array_editor') or field.get('object_editor'):
                selector = ('[role="group"][aria-label=' +
                            json.dumps(label, ensure_ascii=False) + ']')
                control = wait.until(
                    expected.visibility_of_element_located(
                        (By.CSS_SELECTOR, selector)))
            else:
                control = wait.until(lambda driver, name=label:
                                     visible_named_control(driver, name))
        except TimeoutException as exc:
            raise RuntimeError(
                f'field {field["field_id"]!r} did not render with '
                f'accessibility label {label!r}'
            ) from exc
        accessible_name = control.accessible_name or driver.execute_script(
            r"""
            const element = arguments[0];
            const ariaLabel = element.getAttribute('aria-label');
            if (ariaLabel) return ariaLabel.trim();
            const labelledBy = element.getAttribute('aria-labelledby');
            if (labelledBy) {
              return labelledBy.split(/\s+/).map((id) =>
                document.getElementById(id)?.textContent?.trim() || ''
              ).filter(Boolean).join(' ');
            }
            if (element.labels?.length) {
              return [...element.labels].map((label) =>
                label.textContent?.trim() || ''
              ).filter(Boolean).join(' ');
            }
            return '';
            """,
            control,
        )
        observed.append({
            'field_id': field['field_id'],
            'label': label,
            'control': field['control'],
            'visible': True,
            'tag': control.tag_name,
            'role': control.get_attribute('role') or '',
            'accessible_name': accessible_name,
            'enabled': control.is_enabled(),
            'label_geometry': assert_field_label_geometry(driver, control),
        })
    return observed


def screenshot(driver, path, *, reset_scroll=True):
    path.parent.mkdir(parents=True, exist_ok=True)
    if reset_scroll:
        driver.execute_script(
            """
            const dialog = document.querySelector('[role="dialog"]');
            const scrollable = dialog && [...dialog.querySelectorAll('*')]
              .find(element =>
                element.scrollHeight > element.clientHeight + 4);
            if (scrollable) scrollable.scrollTop = 0;
            """
        )
    if not driver.save_screenshot(str(path)):
        raise RuntimeError(f'browser did not save {path}')
    return hashlib.sha256(path.read_bytes()).hexdigest()


def screenshot_form_pages(driver, prefix):
    """Capture overlapping viewports of the entire visible engine task form."""
    region = driver.execute_script("""
      const section = [...document.querySelectorAll(
        'section[aria-label="Engine task form"]')].find(
          element => element.getClientRects().length);
      if (!section) return null;
      for (let node = section; node; node = node.parentElement) {
        const style = getComputedStyle(node);
        if (['auto', 'scroll'].includes(style.overflowY) &&
            node.scrollHeight > node.clientHeight + 1) return node;
      }
      return document.scrollingElement;
    """)
    if region is None:
        raise RuntimeError('The engine task form is not visible')
    metrics = driver.execute_script(
        'return {height: arguments[0].clientHeight, '
        'maximum: arguments[0].scrollHeight - arguments[0].clientHeight};',
        region)
    if metrics['height'] <= 0:
        raise RuntimeError('The task form has no visible viewport')
    maximum = max(0, metrics['maximum'])
    step = max(1, metrics['height'] - 64)
    if maximum > step * 99:
        raise RuntimeError('Task form exceeds the bounded screenshot sweep')
    offsets = list(dict.fromkeys([
        *range(0, maximum + 1, step), maximum]))
    if len(offsets) > 100:
        raise RuntimeError('Task form exceeds the bounded screenshot sweep')
    images = []
    for number, offset in enumerate(offsets):
        actual = driver.execute_script(
            'arguments[0].scrollTop = arguments[1]; '
            'return arguments[0].scrollTop;', region, offset)
        if abs(actual - offset) > 1:
            raise RuntimeError('The task form could not reach its viewport')
        path = prefix.parent / (prefix.name + f'-page-{number + 1:02d}.png')
        images.append({'path': str(path), 'scroll_top': actual,
                       'viewport_height': metrics['height'],
                       'sha256': screenshot(driver, path, reset_scroll=False)})
    return images


def completion_values(options, operation_id):
    """Return unrecorded values for one disposable live operation."""
    if not options.apply_live:
        return PREVIEW_VALUES.get(operation_id, {})
    prefix = options.server_file_prefix
    if not isinstance(prefix, str) or not prefix.strip():
        raise RuntimeError(
            '--server-file-prefix is required with --apply-live'
        )
    values = {
        'backup_logical': {
            'Backup filename on the Firebird server': f'{prefix}.fbk',
        },
        'restore_logical': {
            'Backup filename on the Firebird server': f'{prefix}.fbk',
            'Restored database filename on the Firebird server': (
                f'{prefix}-gbak.fdb'
            ),
        },
        'backup_physical': {
            'Physical backup filename on the Firebird server': (
                f'{prefix}.nbk'
            ),
        },
        'restore_physical': {
            'Ordered backup files': json.dumps([
                f'{prefix}.nbk'
            ]),
            'Restored database filename on the Firebird server': (
                f'{prefix}-nbackup.fdb'
            ),
        },
        'set_page_cache_size': {'Default page buffers': '4096'},
        'set_sweep_interval': {
            'Sweep interval (transactions; 0 disables automatic sweep)': (
                '20000'
            ),
        },
    }
    return values.get(operation_id, {})


def click_unobscured(driver, wait, element):
    """Scroll a real control into view and wait for pointer hit testing."""
    driver.execute_script(
        "arguments[0].scrollIntoView({block:'center', behavior:'instant'});",
        element)
    wait.until(lambda browser: browser.execute_script("""
      const element = arguments[0];
      const rect = element.getBoundingClientRect();
      const hit = document.elementFromPoint(
        rect.left + rect.width / 2, rect.top + rect.height / 2);
      return element === hit || element.contains(hit);
    """, element))
    element.click()


def _draft_field_visible(field, draft):
    condition = field.get('visible_when')
    if condition is None:
        return True
    if not isinstance(condition, dict):
        raise ValueError('Invalid field visibility contract')
    if 'all' in condition:
        children = condition['all']
        if not isinstance(children, list) or not children:
            raise ValueError('Invalid visibility conjunction')
        return all(_draft_field_visible({'visible_when': item}, draft)
                   for item in children)
    if 'equals' in condition:
        return draft.get(condition['field_id']) == condition['equals']
    if isinstance(condition.get('in'), list):
        return draft.get(condition['field_id']) in condition['in']
    raise ValueError('Unknown field visibility contract')


def fill_form_values(driver, wait, fields, values, control_root=None):
    """Fill declared controls, including nested visual records, through UI."""
    by_label = {field['label']: field for field in fields}
    draft = {field['field_id']: values.get(
        field['label'], field.get('default')) for field in fields}
    for field in fields:
        value = draft[field['field_id']]
        if field.get('control') == 'boolean' and isinstance(value, str):
            if value.lower() in ('true', 'false'):
                draft[field['field_id']] = value.lower() == 'true'
    for label, value in values.items():
        field = by_label.get(label)
        if field is None:
            raise ValueError('Unknown form label: ' + label)
        if not _draft_field_visible(field, draft):
            continue
        schema = field.get('array_editor') or field.get('object_editor')
        if schema is None:
            if (field.get('control') in {'multiselect', 'json'} and
                    not isinstance(value, str)):
                value = json.dumps(value, ensure_ascii=False)
            fill_fields(wait, [f'{label}={value}'], control_root=control_root)
            continue
        if isinstance(value, str):
            value = json.loads(value)
        single = 'object_editor' in field
        items = [value] if single else value
        if not isinstance(items, list):
            raise ValueError('Visual records must be a list')
        root = control_root or driver
        selector = ('[role="group"][aria-label=' +
                    json.dumps(label, ensure_ascii=False) + ']')

        def find_group(_driver):
            return next((item for item in root.find_elements(
                By.CSS_SELECTOR, selector) if item.is_displayed()), None)

        group = wait.until(find_group)
        if not single:
            removal = re.compile(re.escape(label) + r' \d+: Remove\Z')
            while True:
                buttons = [button for button in group.find_elements(
                    By.CSS_SELECTOR, 'button') if button.is_displayed() and
                    removal.fullmatch(button.accessible_name)]
                if not buttons:
                    break
                click_unobscured(driver, wait, buttons[-1])
                group = wait.until(find_group)
        for index, record in enumerate(items):
            if not single:
                button = visible_named_control(group, 'Add ' + label + ' item')
                click_unobscured(driver, wait, button)
            group = wait.until(find_group)
            boxes = group.find_elements(By.XPATH, './div')
            record_box = boxes[index]
            if schema.get('item_kind') == 'string':
                fill_fields(wait, [f'{label} {index + 1}={record}'],
                            control_root=record_box)
            else:
                children = schema.get('fields', [])
                child_labels = {child['field_id']: child['label']
                                for child in children}
                if (not isinstance(record, dict) or
                        set(record) - set(child_labels)):
                    raise ValueError('Invalid visual record fields')
                fill_form_values(driver, wait, children,
                                 {child_labels[key]: item
                                  for key, item in record.items()}, record_box)


def plan_preview(driver, wait, operation, values=None):
    values = (
        PREVIEW_VALUES.get(operation['operation_id'], {})
        if values is None else values
    )
    fill_form_values(driver, wait, operation.get('form', {}).get('fields', []),
                     values)
    button = wait.until(
        lambda value: visible_named_control(value, 'Validate and preview')
    )
    wait.until(lambda _driver: button.is_enabled())
    click_unobscured(driver, wait, button)
    preview = wait.until(expected.visibility_of_element_located((
        By.CSS_SELECTOR, '[aria-label="Provider plan preview"]',
    )))
    plan = json.loads(preview.text)
    if plan.get('state') != 'ready':
        raise RuntimeError(
            f'{operation["operation_id"]} plan is not ready'
        )
    if plan.get('execution_available') is not True:
        raise RuntimeError(
            f'{operation["operation_id"]} execution is unavailable'
        )
    return plan


def apply_plan(driver, wait, operation, endpoint_password=None):
    """Apply a disposable plan and retain only value-free finality evidence."""
    confirmation = visible_named_control(
        driver, 'I confirm this provider-planned operation.'
    )
    if confirmation is not None:
        driver.execute_script('arguments[0].click()', confirmation)
    button = wait.until(
        lambda value: visible_named_control(value, 'Apply provider plan')
    )
    wait.until(lambda _driver: button.is_enabled())
    button.click()
    complete_endpoint_prompt(
        driver, endpoint_password, timeout=5
    )
    rendered = wait.until(expected.visibility_of_element_located((
        By.CSS_SELECTOR, '[aria-label="Provider operation result"]',
    )))
    value = json.loads(rendered.text)
    observation = value.get('driver_observation')
    if value.get('accepted') is not True or not isinstance(
            observation, dict):
        raise RuntimeError(
            f'{operation["operation_id"]} returned no accepted provider '
            'observation'
        )
    if observation.get('operation_id') != operation['operation_id'] or (
            observation.get('server_completed') is not True):
        raise RuntimeError(
            f'{operation["operation_id"]} did not report native server '
            'completion'
        )
    return {
        'provider_accepted': True,
        'provider_operation_id': observation['operation_id'],
        'server_completed': True,
        'output_line_count': len(observation.get('output') or []),
        'provider_finality_authority': True,
        'common_finality_inference': False,
        'provider_values_recorded': False,
    }


def close_workspace(driver, wait):
    close = wait.until(
        lambda value: visible_named_control(value, 'Close')
    )
    dialog = close.find_element(By.XPATH, 'ancestor::*[@role="dialog"]')
    close.click()
    wait.until(expected.staleness_of(dialog))


def accessibility_observation(driver, wait, operation, controls):
    """Verify dialog semantics and keyboard focus containment."""
    dialog = wait.until(expected.visibility_of_element_located((
        By.CSS_SELECTOR, '[role="dialog"]',
    )))
    visible_title = wait.until(expected.visibility_of_element_located((
        By.CSS_SELECTOR, '[role="dialog"] .MuiDialogTitle-root',
    ))).text.strip()
    if not visible_title:
        raise RuntimeError(
            f'{operation["operation_id"]} dialog has no visible title'
        )
    dialog_name = wait.until(lambda _driver: next((name for name in (
        dialog.accessible_name or '',
        dialog.get_attribute('aria-label') or '',
    ) if name.strip() == visible_title), False))
    if dialog.get_attribute('aria-modal') != 'true':
        raise RuntimeError(
            f'{operation["operation_id"]} dialog is not aria-modal'
        )
    unnamed = [
        item['field_id'] for item in controls
        if item.get('visible', True) and not item.get('accessible_name')
    ]
    if unnamed:
        raise RuntimeError(
            f'{operation["operation_id"]} has unnamed fields: ' +
            ', '.join(unnamed)
        )

    focusable_count = driver.execute_script(
        """
        const dialog = document.querySelector('[role="dialog"]');
        return [...dialog.querySelectorAll(
          'button, input, textarea, select, [href], [tabindex]'
        )].filter((element) => {
          const style = getComputedStyle(element);
          return !element.disabled && element.tabIndex >= 0 &&
            style.visibility !== 'hidden' && style.display !== 'none' &&
            element.getClientRects().length > 0;
        }).length;
        """
    )
    if focusable_count < 2:
        raise RuntimeError(
            f'{operation["operation_id"]} has no usable keyboard path'
        )

    focus_trace = []
    for _position in range(focusable_count + 1):
        ActionChains(driver).send_keys(Keys.TAB).perform()
        active = driver.switch_to.active_element
        inside = driver.execute_script(
            "return document.querySelector('[role=dialog]').contains("
            "arguments[0])", active
        )
        if not inside:
            raise RuntimeError(
                f'{operation["operation_id"]} keyboard focus escaped dialog'
            )
        focus_trace.append({
            'tag': active.tag_name,
            'role': active.get_attribute('role') or '',
            'accessible_name': active.accessible_name or '',
        })

    driver.execute_script(
        """
        const dialog = document.querySelector('[role="dialog"]');
        const focusable = [...dialog.querySelectorAll(
          'button, input, textarea, select, [href], [tabindex]'
        )].filter((element) => {
          const style = getComputedStyle(element);
          return !element.disabled && element.tabIndex >= 0 &&
            style.visibility !== 'hidden' && style.display !== 'none' &&
            element.getClientRects().length > 0;
        });
        focusable[0].focus();
        """
    )
    ActionChains(driver).key_down(Keys.SHIFT).send_keys(
        Keys.TAB
    ).key_up(Keys.SHIFT).perform()
    wait.until(lambda value: value.execute_script(
        "return document.querySelector('[role=dialog]').contains("
        "document.activeElement)"
    ))

    accessibility_api = 'webdriver-computed-accessible-name'
    accessibility_tree_dialog_count = 1
    return {
        'state': 'observed',
        'dialog_role': dialog.get_attribute('role'),
        'dialog_name': dialog_name,
        'visible_dialog_title': visible_title,
        'aria_modal': True,
        'declared_fields_named': len(controls),
        'unnamed_fields': [],
        'focusable_control_count': focusable_count,
        'focus_contained': True,
        'reverse_tab_contained': True,
        'accessibility_api': accessibility_api,
        'accessibility_tree_dialog_count': (
            accessibility_tree_dialog_count
        ),
        'focus_trace': focus_trace,
        'control_values_recorded': False,
    }


def close_workspace_with_keyboard(driver, wait):
    """Dismiss the active task through its keyboard escape path."""
    dialog = wait.until(expected.visibility_of_element_located((
        By.CSS_SELECTOR, '[role="dialog"]',
    )))
    ActionChains(driver).send_keys(Keys.ESCAPE).perform()
    wait.until(expected.staleness_of(dialog))


def open_workspace(driver, wait, options, password, action, operation):
    """Open one exact provider form from its database context action."""
    group_label = MENU_GROUP_LABELS[action['menu_group']]
    database = wait_for_tree_item(wait, options.database)
    invoke_context_action(
        wait, driver, database, [group_label, action['label']], password,
        endpoint_prompt_timeout=1,
    )
    if action['requires_confirmation']:
        confirm = wait.until(
            lambda value: visible_named_control(value, 'Continue')
        )
        wait.until(lambda _driver: confirm.is_enabled())
        confirm.click()
    complete_endpoint_prompt(driver, password, timeout=1)
    wait.until(lambda value: operation['title'] in value.find_element(
        By.TAG_NAME, 'body'
    ).text)


def cancellation_observation(driver):
    """Prove that dismissing a task removes its provider form."""
    dialog_present = driver.execute_script(
        "return Boolean(document.querySelector('[role=dialog]'))"
    )
    if dialog_present:
        raise RuntimeError('provider form remained visible after Close')
    return {
        'dialog_present': False,
        'dialog_dismissed': True,
        'dismissal_input': 'keyboard_escape',
        'provider_plan_requested': False,
        'provider_operation_executed': False,
    }


def validation_error(driver, wait, operation):
    """Exercise one provider-declared invalid case without planning."""
    case = VALIDATION_CASES.get(operation['operation_id'])
    if case is None:
        return {
            'state': 'not_applicable',
            'reason': VALIDATION_NOT_APPLICABLE_REASON,
        }
    if case.get('label'):
        fill_fields(wait, [
            f'{case["label"]}={case["invalid_value"]}'
        ])
    button = wait.until(
        lambda value: visible_named_control(value, 'Validate and preview')
    )
    wait.until(lambda _driver: button.is_enabled())
    button.click()

    def matching_alert(value):
        for alert in value.find_elements(By.CSS_SELECTOR, '[role=alert]'):
            if alert.is_displayed() and case['expected'] in alert.text:
                return alert
        return None

    alert = wait.until(matching_alert)
    if driver.find_elements(
            By.CSS_SELECTOR, '[aria-label="Provider plan preview"]'):
        raise RuntimeError(
            f'{operation["operation_id"]} produced a plan for invalid input'
        )
    apply_button = wait.until(
        lambda value: visible_named_control(value, 'Apply provider plan')
    )
    if apply_button.is_enabled():
        raise RuntimeError(
            f'{operation["operation_id"]} enabled Apply for invalid input'
        )
    result = {
        'state': 'observed',
        'field_id': case['field_id'],
        'validation_kind': case['kind'],
        'message': alert.text,
        'plan_present': False,
        'apply_enabled': False,
        'provider_operation_executed': False,
    }
    return result


def restore_valid_input(wait, operation):
    """Restore a range/JSON validation case before requesting its preview."""
    case = VALIDATION_CASES.get(operation['operation_id'])
    if case and case.get('label'):
        fill_fields(wait, [f'{case["label"]}={case["valid_value"]}'])


def run_case(driver, wait, options, password, action, operation):
    operation_id = operation['operation_id']
    open_workspace(driver, wait, options, password, action, operation)
    controls = assert_form_controls(wait, operation['form']['fields'])
    initial_layout = layout_observation(driver)
    directory = options.output_root / f'database.firebird.{operation_id}'
    viewport = f'{options.width}x{options.height}'
    variant = evidence_variant(options)
    initial_path = directory / f'initial-{viewport}-{variant}.png'
    initial_hash = screenshot(driver, initial_path)

    accessibility = accessibility_observation(
        driver, wait, operation, controls
    )
    close_workspace_with_keyboard(driver, wait)
    cancelled_layout = cancellation_observation(driver)
    cancelled_path = directory / f'cancelled-{viewport}-{variant}.png'
    cancelled_hash = screenshot(driver, cancelled_path)

    open_workspace(driver, wait, options, password, action, operation)
    assert_form_controls(wait, operation['form']['fields'])
    validation = validation_error(driver, wait, operation)
    validation_layout = None
    validation_path = None
    validation_hash = None
    if validation['state'] == 'observed':
        validation_layout = layout_observation(driver)
        validation_path = (
            directory / f'validation-error-{viewport}-{variant}.png'
        )
        validation_hash = screenshot(driver, validation_path)
        restore_valid_input(wait, operation)
    values = completion_values(options, operation_id)
    if values:
        # Validation recovery may have populated the standard preview values.
        # Replace them with the unique disposable paths used by the live gate.
        fill_fields(wait, [
            f'{label}={value}' for label, value in values.items()
        ])
    plan = plan_preview(driver, wait, operation, values={})
    preview_layout = layout_observation(driver)
    preview_path = directory / f'plan-preview-{viewport}-{variant}.png'
    preview_hash = screenshot(driver, preview_path)
    rendered_controls = _control_evidence(driver)
    completion = None
    completed_layout = None
    completed_path = None
    completed_hash = None
    if options.apply_live:
        completion = apply_plan(
            driver, wait, operation, password
        )
        completed_layout = layout_observation(driver)
        completed_path = (
            directory / f'completed-{viewport}-{variant}.png'
        )
        completed_hash = screenshot(driver, completed_path)
    close_workspace(driver, wait)
    result = {
        'command_id': action['command_id'],
        'menu_group': action['menu_group'],
        'menu_label': action['label'],
        'mutation_class': action['mutation_class'],
        'requires_confirmation': action['requires_confirmation'],
        'operation_id': operation_id,
        'form_id': operation['form_id'],
        'form_title': operation['form']['title'],
        'declared_field_count': len(operation['form']['fields']),
        'observed_fields': controls,
        'rendered_control_count': len(rendered_controls),
        'layout': {
            'initial': initial_layout,
            'cancelled': cancelled_layout,
            'validation_error': validation_layout,
            'plan_preview': preview_layout,
            'completed': completed_layout,
        },
        'cancellation': {
            'state': 'observed',
            **cancelled_layout,
        },
        'validation': validation,
        'accessibility': accessibility,
        'plan_state': plan['state'],
        'execution_available': plan['execution_available'],
        'plan_digest': plan['plan_digest'],
        'completion': completion,
        'screenshots': {
            'initial': {
                'path': str(initial_path), 'sha256': initial_hash,
            },
            'cancelled': {
                'path': str(cancelled_path), 'sha256': cancelled_hash,
            },
            'plan_preview': {
                'path': str(preview_path), 'sha256': preview_hash,
            },
        },
    }
    if validation_path is not None:
        result['screenshots']['validation_error'] = {
            'path': str(validation_path), 'sha256': validation_hash,
        }
    if completed_path is not None:
        result['screenshots']['completed'] = {
            'path': str(completed_path), 'sha256': completed_hash,
        }
    return result


MANIFEST_FIELDS = (
    'interface_id', 'reference_version', 'profile_id', 'server_id',
    'database_target_id', 'command_id', 'form_id', 'resource_kind',
    'resource_id', 'state', 'viewport', 'device_scale', 'font_scale',
    'theme', 'locale', 'screenshot_path', 'occurrence_path',
    'interaction_result', 'transaction_proof_id', 'captured_at_utc',
)


def _relative_evidence_path(path, manifest):
    try:
        return path.resolve().relative_to(manifest.parent.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def record_screenshot_evidence(options, evidence):
    """Write a value-free occurrence and manifest row per batch PNG."""
    if options.manifest_output is None:
        return
    manifest = options.manifest_output
    manifest.parent.mkdir(parents=True, exist_ok=True)
    existing = []
    if manifest.exists():
        with manifest.open(newline='', encoding='utf-8') as source:
            existing = list(csv.DictReader(source))

    def row_key(row):
        return (
            row['interface_id'], row['command_id'], row['state'],
            row['screenshot_path'],
        )

    existing_indexes = {
        row_key(row): index for index, row in enumerate(existing)
    }
    captured_at = evidence['captured_at']
    viewport = evidence['viewport']
    rows = []
    for form in evidence['forms']:
        for source_state, screenshot_value in form['screenshots'].items():
            state = (
                'preview' if source_state == 'plan_preview'
                else source_state
            )
            screenshot_path = Path(screenshot_value['path'])
            occurrence_path = screenshot_path.with_name(
                screenshot_path.stem + '.occurrence.json'
            )
            occurrence = {
                'schema': 'cdeadmin.ui-form-evidence.v1',
                'captured_at': captured_at,
                'engine_id': evidence['engine_id'],
                'interface_id': evidence['interface_id'],
                'reference_version': evidence['reference_version'],
                'server_label': evidence['server_label'],
                'database_label': evidence['database_label'],
                'command_id': form['command_id'],
                'form_id': form['form_id'],
                'operation_id': form['operation_id'],
                'state': state,
                'viewport': viewport,
                'theme': evidence['theme'],
                'font_scale': evidence['font_scale'],
                'locale': 'en-US',
                'declared_field_count': form.get(
                    'declared_field_count', len(form['observed_fields'])
                ),
                'observed_fields': form['observed_fields'],
                'layout': form.get('layout', {}).get(source_state),
                'plan_state': (
                    form['plan_state'] if state == 'preview' else None
                ),
                'execution_available': (
                    form.get(
                        'execution_available', form['plan_state'] == 'ready'
                    )
                    if state == 'preview' else None
                ),
                'cancellation': (
                    form['cancellation'] if state == 'cancelled' else None
                ),
                'validation': (
                    form['validation']
                    if state == 'validation_error' else None
                ),
                'accessibility': (
                    form.get('accessibility')
                    if state == 'initial' else None
                ),
                'completion': (
                    form.get('completion') if state == 'completed' else None
                ),
                'screenshot': {
                    'path': str(screenshot_path),
                    'sha256': screenshot_value['sha256'],
                },
                'control_values_recorded': False,
                'credential_values_exported': False,
            }
            occurrence_path.write_text(
                json.dumps(occurrence, indent=2, sort_keys=True) + '\n',
                encoding='utf-8',
            )
            relative_screenshot = _relative_evidence_path(
                screenshot_path, manifest
            )
            row = {
                'interface_id': evidence['interface_id'],
                'reference_version': evidence['reference_version'],
                'profile_id': evidence['interface_id'],
                'server_id': evidence['server_label'],
                'database_target_id': evidence['database_label'],
                'command_id': form['command_id'],
                'form_id': form['form_id'],
                'resource_kind': 'database',
                'resource_id': evidence['database_label'],
                'state': state,
                'viewport': viewport,
                'device_scale': '1',
                'font_scale': f'{evidence["font_scale"]}%',
                'theme': evidence['theme'],
                'locale': 'en-US',
                'screenshot_path': relative_screenshot,
                'occurrence_path': _relative_evidence_path(
                    occurrence_path, manifest
                ),
                'interaction_result': {
                    'cancelled': (
                        'provider form dismissed by keyboard Escape without '
                        'requesting a plan or executing an operation'
                    ),
                    'validation_error': (
                        'provider rejected invalid input; no plan was '
                        'created and Apply remained disabled'
                    ),
                    'preview': (
                        'provider form rendered and plan reached ready state'
                    ),
                    'completed': (
                        'provider accepted the disposable operation and its '
                        'native observation reported completion without '
                        'common-code finality inference'
                    ),
                }.get(state, 'provider form rendered') +
                f'; sha256={screenshot_value["sha256"]}',
                'transaction_proof_id': '',
                'captured_at_utc': captured_at,
            }
            key = row_key(row)
            if key in existing_indexes:
                existing[existing_indexes[key]] = row
            else:
                existing_indexes[key] = len(existing) + len(rows)
                rows.append(row)
    with manifest.open('w', newline='', encoding='utf-8') as target:
        writer = csv.DictWriter(target, fieldnames=MANIFEST_FIELDS)
        writer.writeheader()
        writer.writerows(existing + rows)


def main():
    options = arguments()
    if options.record_existing_summary is not None:
        if options.manifest_output is None:
            raise SystemExit(
                '--manifest-output is required with '
                '--record-existing-summary'
            )
        evidence = json.loads(options.record_existing_summary.read_text(
            encoding='utf-8'
        ))
        record_screenshot_evidence(options, evidence)
        return
    if not options.endpoint_password_env:
        raise SystemExit('--endpoint-password-env is required')
    password = os.environ.get(options.endpoint_password_env)
    if not password:
        raise SystemExit(f'{options.endpoint_password_env} must be set')
    operations = firebird_service_forms()
    if options.apply_live:
        if not options.server_file_prefix:
            raise SystemExit(
                '--server-file-prefix is required with --apply-live'
            )
        if options.database == 'cdeadmin_demo.fdb':
            raise SystemExit(
                '--apply-live refuses the packaged cdeadmin_demo.fdb target'
            )
    if options.operation:
        requested = set(options.operation)
        known = {item['operation_id'] for item in operations}
        unknown = sorted(requested - known)
        if unknown:
            raise SystemExit(
                'unknown Firebird form operations: ' + ', '.join(unknown)
            )
        operations = [
            item for item in operations
            if item['operation_id'] in requested
        ]
    if options.apply_live:
        unsupported = sorted(
            {item['operation_id'] for item in operations}.intersection(
                FAULT_FIXTURE_OPERATIONS
            )
        )
        if unsupported:
            raise SystemExit(
                'fault-state operation requires its dedicated fixture: ' +
                ', '.join(unsupported)
            )
        order = {
            operation_id: position for position, operation_id in enumerate(
                NORMAL_COMPLETION_ORDER
            )
        }
        operations.sort(key=lambda item: order[item['operation_id']])
    driver = create_driver(options)
    wait = WebDriverWait(driver, options.timeout)
    results = []
    failures = []
    try:
        actions = prepare_tree(driver, wait, options, password)
        actions_by_operation = {
            action.get('arguments', {}).get('operation_id'): action
            for action in actions
            if action.get('command_id', '').startswith('database.firebird.')
        }
        for operation in operations:
            operation_id = operation['operation_id']
            print(f'Firebird form {operation_id}', flush=True)
            action = actions_by_operation.get(operation_id)
            if action is None:
                failures.append({
                    'operation_id': operation_id,
                    'error': 'database popup action is missing',
                })
                continue
            try:
                results.append(run_case(
                    driver, wait, options, password, action, operation
                ))
            except Exception as exc:
                failure_path = (
                    options.output_root /
                    f'database.firebird.{operation_id}' /
                    f'failure-{options.width}x{options.height}-'
                    f'{evidence_variant(options)}.png'
                )
                failure_hash = screenshot(driver, failure_path)
                failures.append({
                    'operation_id': operation_id,
                    'error_type': type(exc).__name__,
                    'error': str(exc),
                    'traceback': traceback.format_exc(),
                    'screenshot': {
                        'path': str(failure_path),
                        'sha256': failure_hash,
                    },
                })
                break
    finally:
        driver.quit()
    evidence = {
        'schema': 'cdeadmin.firebird-rendered-form-gate.v1',
        'captured_at': datetime.now(timezone.utc).isoformat(),
        'engine_id': 'firebird',
        'interface_id': 'firebird-native',
        'reference_version': '5.0.4',
        'server_label': options.server,
        'database_label': options.database,
        'viewport': f'{options.width}x{options.height}',
        'theme': options.theme,
        'font_scale': options.font_scale,
        'evidence_variant': evidence_variant(options),
        'credential_values_exported': False,
        'mutation_policy': (
            'orchestrator-owned-disposable-database'
            if options.apply_live else 'plan-preview-only'
        ),
        'expected_form_count': len(operations),
        'passed_form_count': len(results),
        'cancelled_form_count': sum(
            item['cancellation']['state'] == 'observed'
            for item in results
        ),
        'keyboard_cancelled_form_count': sum(
            item['cancellation'].get('dismissal_input') == 'keyboard_escape'
            for item in results
        ),
        'accessibility_observed_form_count': sum(
            item['accessibility']['state'] == 'observed'
            for item in results
        ),
        'validation_observed_form_count': sum(
            item['validation']['state'] == 'observed'
            for item in results
        ),
        'validation_not_applicable_form_count': sum(
            item['validation']['state'] == 'not_applicable'
            for item in results
        ),
        'provider_completed_form_count': sum(
            item.get('completion', {}).get('server_completed') is True
            for item in results
        ),
        'complete': (
            len(results) == len(operations) and not failures and
            (
                not options.apply_live or
                all(item.get('completion', {}).get(
                    'server_completed'
                ) is True for item in results)
            )
        ),
        'forms': results,
        'failures': failures,
    }
    options.summary_output.parent.mkdir(parents=True, exist_ok=True)
    options.summary_output.write_text(
        json.dumps(evidence, indent=2, sort_keys=True) + '\n',
        encoding='utf-8',
    )
    record_screenshot_evidence(options, evidence)
    if not evidence['complete']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
