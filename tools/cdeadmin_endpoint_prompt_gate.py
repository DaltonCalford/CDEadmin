#!/usr/bin/env python3
"""Real-browser credential-prompt scoping; no engine or user profile writes."""

import json
from types import SimpleNamespace
from urllib.parse import quote

from selenium.webdriver.common.by import By

from tools.cdeadmin_firebird_ui_form_gate import create_driver
from tools.cdeadmin_ui_evidence import complete_endpoint_prompt


CASES = ('verify', 'legacy-connect', 'profile-editor', 'hidden',
         'duplicate', 'missing-secret', 'unrelated-ok')


def run():
    driver = create_driver(SimpleNamespace(
        browser_binary=None, width=1000, height=800))
    results = []
    try:
        for scale in (100, 200, 300):
            for case in CASES:
                record = {'scale': scale, 'case': case, 'passed': False}
                results.append(record)
                try:
                    driver.get('data:text/html,' + quote('''
                        <style>body {margin:20px} button,input {font:inherit}
                        [role=dialog] {border:1px solid;padding:16px}
                        </style>
                        <button id="unrelated">OK</button>
                        <div role="dialog">
                          <h2 id="cdeadmin-modal-title-id-verify-endpoint">
                            Connection verification</h2>
                          <input type="password"
                            autocomplete="current-password"
                            value="original-editor-value">
                          <button data-test="save">OK</button>
                        </div>
                        <script>
                        window.receipts=[];
                        document.addEventListener('click', event => {
                          if (event.target.id === 'unrelated') {
                            receipts.push({unrelated:true}); return;
                          }
                          if (!event.target.matches('[data-test="save"]'))
                            return;
                          const dialog=event.target.closest('[role="dialog"]');
                          receipts.push({trusted:event.isTrusted,
                            correct:dialog.querySelector('input').value ===
                              'owned-qualification-secret'});
                          dialog.remove();
                        });
                        </script>
                    '''))
                    driver.execute_script(
                        'document.body.style.fontSize=arguments[0]+"px"',
                        scale * 16 / 100)
                    if case in {'legacy-connect', 'profile-editor'}:
                        driver.execute_script(
                            'document.querySelector("h2").id=arguments[0]',
                            'cdeadmin-modal-title-' + (
                                'id-connect-server' if case == 'legacy-connect'
                                else 'edit-endpoint-properties'))
                    if case == 'hidden':
                        driver.execute_script(
                            'document.querySelector("[role=dialog]").style.'
                            'display="none"')
                    if case == 'duplicate':
                        driver.execute_script('''
                            const copy=document.querySelector('[role=dialog]')
                              .cloneNode(true);
                            copy.querySelector('h2').id=
                              'cdeadmin-modal-title-id-connect-server';
                            document.body.append(copy);
                        ''')
                    expected_error = case in {'duplicate', 'missing-secret'}
                    try:
                        completed = complete_endpoint_prompt(
                            driver, None if case == 'missing-secret' else
                            'owned-qualification-secret', timeout=0.5)
                    except RuntimeError:
                        if not expected_error:
                            raise
                        completed = False
                        record['rejected_before_typing'] = True
                    else:
                        assert not expected_error
                    successful = case in {
                        'verify', 'legacy-connect', 'unrelated-ok'}
                    assert completed is successful
                    receipts = driver.execute_script('return window.receipts')
                    assert receipts == ([{'trusted': True, 'correct': True}]
                                        if successful else [])
                    if not successful:
                        assert all(field.get_attribute('value') ==
                                   'original-editor-value' for field in
                                   driver.find_elements(By.CSS_SELECTOR,
                                                        'input'))
                    record['passed'] = True
                except Exception as exc:
                    record['error_type'] = type(exc).__name__
    finally:
        driver.quit()
    return {'complete': len(results) == 3 * len(CASES) and
            all(item['passed'] for item in results), 'cases': results}


if __name__ == '__main__':
    result = run()
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result['complete'] else 1)
