#!/usr/bin/env python3
"""Exercise real context pointers against clipping and modal overlays."""

import json
from types import SimpleNamespace
from urllib.parse import quote

from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait

from tools.cdeadmin_firebird_ui_form_gate import create_driver
from tools.cdeadmin_ui_evidence import _context_pointer


def run():
    driver = create_driver(SimpleNamespace(
        browser_binary=None, width=1000, height=800))
    results = []
    try:
        for scale in (100, 200, 300):
            for case in ('visible', 'clipped', 'overlay', 'detached'):
                try:
                    driver.get('data:text/html,' + quote('''
                        <style>
                        body {margin:0}
                        #clip {position:absolute;left:40px;top:80px;
                          width:250px;height:90px;overflow:hidden}
                        .file-entry {width:600px;height:60px;background:cyan}
                        #overlay {position:fixed;inset:0;background:white;
                          z-index:100;display:none}
                        </style>
                        <div id="clip"><div class="file-entry"
                          aria-selected="true"><span>Database</span></div></div>
                        <div id="overlay"></div>
                        <script>
                        window.events=[];
                        document.addEventListener('contextmenu', event => {
                          event.preventDefault();
                          events.push({trusted:event.isTrusted,
                            row:!!event.target.closest('.file-entry'),
                            x:event.clientX,y:event.clientY});
                        });
                        </script>
                    '''))
                    driver.execute_script(
                        'document.body.style.fontSize=arguments[0]+"px"',
                        scale * 16 / 100)
                    target = driver.find_element(By.CLASS_NAME, 'file-entry')
                    if case == 'clipped':
                        driver.execute_script(
                            'document.querySelector("#clip").scrollLeft=300')
                    if case == 'overlay':
                        driver.execute_script(
                            'document.querySelector("#overlay").style.'
                            'display="block"')
                    if case == 'detached':
                        driver.execute_script('arguments[0].remove()', target)
                        target = None
                    before = driver.execute_script('return [scrollX,scrollY]')
                    blocked = case in {'overlay', 'detached'}
                    try:
                        _context_pointer(WebDriverWait(driver, 1), driver,
                                         target)
                        if blocked:
                            raise AssertionError('Obscured row was clicked')
                    except TimeoutException:
                        if not blocked:
                            raise
                    events = driver.execute_script('return window.events')
                    assert len(events) == (0 if blocked else 1)
                    assert all(event['trusted'] and event['row']
                               for event in events)
                    assert before == driver.execute_script(
                        'return [scrollX,scrollY]')
                    if case == 'overlay':
                        driver.execute_script(
                            'document.querySelector("#overlay").remove()')
                        _context_pointer(WebDriverWait(driver, 1), driver,
                                         target)
                        assert driver.execute_script(
                            'return events.length === 1 && events[0].trusted')
                    results.append({'scale': scale, 'case': case,
                                    'passed': True})
                except Exception as exc:
                    results.append({'scale': scale, 'case': case,
                                    'passed': False,
                                    'error_type': type(exc).__name__})
    finally:
        driver.quit()
    return {'complete': all(item['passed'] for item in results),
            'cases': results}


if __name__ == '__main__':
    result = run()
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result['complete'] else 1)
