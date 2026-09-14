#!/usr/bin/env python3
##########################################################################
# CDEadmin - Multi-engine Database Administration
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
##########################################################################

"""Inspect table metadata in focused browser forms using owned fixtures."""

import json
import sys
import traceback
import uuid
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.cdeadmin_firebird_role_ui_gate import arguments  # noqa: E402
from tools.cdeadmin_firebird_columns_ui_gate import (  # noqa: E402
    cleanup_column_fixtures,
)
from tools import cdeadmin_provider_object_form_gate as forms  # noqa: E402
from tools.cdeadmin_firebird_admin_mapping_gate import (  # noqa: E402
    _create_client, _resources, _route_arguments,
)
from tools.cdeadmin_firebird_ui_form_gate import (  # noqa: E402
    create_driver, screenshot,
)
from tools.cdeadmin_ui_evidence import fill_fields  # noqa: E402
from tools.cdeadmin_provider_object_form_gate import (  # noqa: E402
    close_workspace,
)
from selenium.webdriver.support.ui import WebDriverWait  # noqa: E402


def run(options, profiles):
    import firebird.driver as driver
    document = json.loads(profiles.read_text())
    route = next(dict(item) for item in document['profiles']
                 if item['engine'] == 'firebird')
    route.setdefault('host', document.get('host', '127.0.0.1'))
    assert route['database'] == options.database_path
    _create_client(SimpleNamespace(acquire_secret=None))
    native = driver.connect(password=route['password'],
                            **_route_arguments(route, driver))
    prefix = 'CDE_UI_META_' + uuid.uuid4().hex[:10].upper()
    domain = 'rdb$' + prefix
    tables = []
    domain_created = False
    role_created = False
    role = prefix + '_ROLE'
    browser = None
    result = {'passed': False, 'checks': [], 'failures': [],
              'fixtures_removed': False, 'credential_values_exported': False,
              'context_commands_collected': False}

    def quote(name):
        return '"' + name.replace('"', '""') + '"'

    def execute(sql):
        with native.cursor() as cursor:
            cursor.execute(sql)
        native.commit()

    try:
        execute('CREATE DOMAIN ' + quote(domain) +
                ' AS INTEGER DEFAULT 7 CHECK (VALUE > 0)')
        domain_created = True
        execute('CREATE ROLE ' + quote(role))
        role_created = True
        variants = [
            ('DOMAIN', 'V ' + quote(domain), False),
            ('KEY', 'V INTEGER CONSTRAINT ' + quote(prefix + '_NN') +
             ' NOT NULL, CONSTRAINT ' + quote(prefix + '_K') +
             ' UNIQUE (V) USING DESCENDING INDEX ' + quote(prefix + '_IX'),
             False),
            ('IDENTITY', 'V BIGINT GENERATED ALWAYS AS IDENTITY', False),
            ('TEMPORARY', 'V INTEGER', True),
            ('SECURITY', 'V INTEGER', False),
        ]
        for label, definition, temporary in variants:
            name = prefix + '_' + label
            execute(('CREATE GLOBAL TEMPORARY TABLE ' if temporary else
                     'CREATE TABLE ') + quote(name) + ' (' + definition + ')' +
                    (' ON COMMIT PRESERVE ROWS' if temporary else ''))
            tables.append(name)
            if temporary:
                execute('ALTER TABLE ' + quote(name) + ' ENABLE PUBLICATION')
            execute('COMMENT ON TABLE ' + quote(name) +
                    " IS '  table ''é''; text  '")
            execute('COMMENT ON COLUMN ' + quote(name) +
                    ".V IS '  column ''é''; text  '")
            if label == 'SECURITY':
                execute('GRANT UPDATE(V) ON ' + quote(name) +
                        ' TO ROLE ' + quote(role))
                execute('ALTER TABLE ' + quote(name) +
                        ' ALTER COLUMN V TO W')
        expected = {item['display_name']: item['native'] for item in
                    _resources(native, {'route': route}) if
                    item['resource_kind'] == 'table' and
                    item['display_name'] in tables}
        native.commit()
        browser = create_driver(options)
        browser.set_script_timeout(120)
        wait = WebDriverWait(browser, options.timeout)
        forms._prepare_tree(browser, wait, options)
        probe = forms._workspace_probe(browser, ['table'],
                                       collect_context_commands=False)
        operation = next(iter(forms._enumerate_operations(
            probe['catalog'], ['table'], ['inspect'])))
        for name in tables:
            resource = next(item for item in probe['resources'] if
                            item['resource_kind'] == 'table' and
                            item['display_name'] == name)
            forms._open_focused_form(browser, operation, resource,
                                     probe['database_target_id'])
            forms._wait_for_operation(wait, operation)
            warnings_visible = []
            for message in expected[name].get('catalog_warnings', []):
                wait.until(lambda web: any(
                    node.is_displayed() and message in node.text for node in
                    web.find_elements('css selector', '[role="alert"]')))
                warnings_visible.append(message)
            fill_fields(wait, [
                'Object properties task=Creation statement (DDL)'])
            panel = wait.until(lambda web: next((
                node for node in web.find_elements(
                    'css selector', '[role="tabpanel"][aria-label="'
                    'ddl object section"]')
                if node.is_displayed()), None))
            ddl = expected[name]['ddl']
            wait.until(lambda _web: panel.get_attribute('textContent') == ddl)
            paths = []
            for position in ('top', 'bottom'):
                browser.execute_script(
                    'arguments[0].scrollTop = arguments[1] === "top" ? 0 : '
                    'arguments[0].scrollHeight;', panel, position)
                path = options.output_root / (name + '-' + position + '.png')
                paths.append({'path': str(path),
                              'sha256': screenshot(browser, path,
                                                   reset_scroll=False)})
            fill_fields(wait, ['Object properties task=Columns'])
            columns_panel = wait.until(lambda web: next((
                node for node in web.find_elements(
                    'css selector', '[aria-label="columns object section"]')
                if node.is_displayed()), None))
            assert ('W' if name.endswith('_SECURITY') else 'V') in (
                columns_panel.text)
            if name.endswith('_SECURITY'):
                assert warnings_visible
                fill_fields(wait, [
                    'Object properties task=Privileges and grants'])
                security_panel = wait.until(lambda web: next((
                    node for node in web.find_elements(
                        'css selector', '[aria-label="privileges '
                        'object section"]') if node.is_displayed()), None))
                assert role in security_panel.text
                assert 'unresolved' in security_panel.text
                path = options.output_root / (name + '-grants.png')
                paths.append({'path': str(path),
                              'sha256': screenshot(browser, path)})
            result['checks'].append({'table': name, 'ddl': ddl,
                                     'exact_ddl_rendered': True,
                                     'columns_rendered': True,
                                     'screenshots': paths,
                                     'catalog_warnings': warnings_visible})
            close_workspace(browser, wait)
        result['passed'] = len(result['checks']) == len(variants)
    except Exception:
        result['failures'].append({'traceback': traceback.format_exc()})
        if browser is not None:
            try:
                path = options.output_root / 'failure.png'
                result['failure_screenshot'] = str(path)
                screenshot(browser, path)
            except Exception as error:
                result['diagnostic_error_type'] = type(error).__name__
    finally:
        if role_created:
            try:
                if native.main_transaction.is_active():
                    native.rollback()
                execute('DROP ROLE ' + quote(role))
                with native.cursor() as cursor:
                    cursor.execute('SELECT COUNT(*) FROM RDB$ROLES WHERE '
                                   'RDB$ROLE_NAME = ?', (role,))
                    assert cursor.fetchone()[0] == 0
                native.commit()
                result['role_removed'] = True
            except Exception:
                result['role_removed'] = False
                result['failures'].append({
                    'case': 'role-cleanup', 'owned_role': role,
                    'traceback': traceback.format_exc()})
        cleanup = cleanup_column_fixtures(
            browser, native, tables, domain if domain_created else None)
        result['fixtures_removed'] = cleanup['fixtures_removed']
        result['failures'].extend(cleanup['errors'])
        result['passed'] = result['passed'] and not result['failures']
    return result


def main():
    options, profiles = arguments()
    result = run(options, profiles)
    options.summary_output.parent.mkdir(parents=True, exist_ok=True)
    options.summary_output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({'passed': result['passed'],
                      'checks': len(result['checks']),
                      'fixtures_removed': result['fixtures_removed']}))
    return 0 if result['passed'] and result['fixtures_removed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
