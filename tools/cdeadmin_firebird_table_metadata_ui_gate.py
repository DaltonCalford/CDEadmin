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
        variants = [
            ('DOMAIN', 'V ' + quote(domain), False),
            ('KEY', 'V INTEGER CONSTRAINT ' + quote(prefix + '_NN') +
             ' NOT NULL, CONSTRAINT ' + quote(prefix + '_K') +
             ' UNIQUE (V) USING DESCENDING INDEX ' + quote(prefix + '_IX'),
             False),
            ('IDENTITY', 'V BIGINT GENERATED ALWAYS AS IDENTITY', False),
            ('TEMPORARY', 'V INTEGER', True),
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
            assert 'V' in columns_panel.text
            result['checks'].append({'table': name, 'ddl': ddl,
                                     'exact_ddl_rendered': True,
                                     'columns_rendered': True,
                                     'screenshots': paths})
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
        cleanup = cleanup_column_fixtures(
            browser, native, tables, domain if domain_created else None)
        result['fixtures_removed'] = cleanup['fixtures_removed']
        result['failures'].extend(cleanup['errors'])
        result['passed'] = result['passed'] and not cleanup['errors']
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
