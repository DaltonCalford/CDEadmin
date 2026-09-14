##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Contract tests for the live Firebird rendered-form gate."""

import csv
import json
import tempfile
import unittest
from unittest.mock import Mock
from pathlib import Path
from types import SimpleNamespace

from tools.cdeadmin_firebird_ui_form_gate import (
    FAULT_FIXTURE_OPERATIONS,
    MENU_GROUP_LABELS,
    NORMAL_COMPLETION_ORDER,
    PREVIEW_VALUES,
    VALIDATION_CASES,
    VALIDATION_NOT_APPLICABLE_REASON,
    completion_values,
    evidence_variant,
    firebird_service_forms,
    record_screenshot_evidence,
    screenshot,
    screenshot_form_pages,
)


class FirebirdUIFormGateTests(unittest.TestCase):

    def test_form_screenshot_sweep_overlaps_and_reaches_the_bottom(self):
        with tempfile.TemporaryDirectory(prefix='cde-screenshot-test-') as tmp:
            driver = Mock()
            driver.execute_script.side_effect = [
                object(), {'height': 200, 'maximum': 300}, 0, 136, 272, 300]
            driver.save_screenshot.side_effect = lambda destination: (
                Path(destination).write_bytes(b'PNG') > 0)
            images = screenshot_form_pages(driver, Path(tmp) / 'form')
            self.assertEqual([item['scroll_top'] for item in images],
                             [0, 136, 272, 300])
            self.assertTrue(all(item['viewport_height'] == 200
                                for item in images))
            self.assertEqual(driver.execute_script.call_count, 6)

    def test_sweep_rejects_missing_unreachable_or_unbounded_viewports(self):
        for responses in (
                [None], [object(), {'height': 0, 'maximum': 1}],
                [object(), {'height': 200, 'maximum': 10000000}],
                [object(), {'height': 200, 'maximum': 300}, 15]):
            with self.subTest(responses=responses):
                driver = Mock()
                driver.execute_script.side_effect = responses
                with self.assertRaises(RuntimeError):
                    screenshot_form_pages(driver, Path('unused'))
                driver.save_screenshot.assert_not_called()

    def test_screenshots_can_preserve_the_requested_scroll_position(self):
        with tempfile.TemporaryDirectory(prefix='cde-screenshot-test-') as tmp:
            for reset in (True, False):
                with self.subTest(reset_scroll=reset):
                    driver = Mock()
                    driver.save_screenshot.side_effect = lambda destination: (
                        Path(destination).write_bytes(b'PNG') > 0)
                    path = Path(tmp) / 'shot.png'
                    digest = screenshot(driver, path, reset_scroll=reset)
                    self.assertEqual(path.read_bytes(), b'PNG')
                    self.assertEqual(len(digest), 64)
                    self.assertEqual(driver.execute_script.call_count,
                                     int(reset))

    def test_gate_covers_every_firebird_database_service_form(self):
        operations = firebird_service_forms()
        self.assertEqual(21, len(operations))
        self.assertEqual(21, len({
            operation['operation_id'] for operation in operations
        }))
        self.assertEqual(21, len({
            operation['form_id'] for operation in operations
        }))
        self.assertEqual({
            'activate_shadow', 'backup_logical', 'backup_physical',
            'bring_online', 'database_statistics', 'fixup_database',
            'remove_linger', 'repair_database', 'restore_logical',
            'restore_physical', 'set_access_mode', 'set_page_cache_size',
            'set_replica_mode', 'set_space_reservation',
            'set_sql_dialect', 'set_sweep_interval', 'set_write_mode',
            'shutdown_database', 'sweep_database', 'upgrade_database',
            'validate_database',
        }, {operation['operation_id'] for operation in operations})

    def test_normal_completion_order_excludes_only_fault_fixtures(self):
        operation_ids = {
            operation['operation_id']
            for operation in firebird_service_forms()
        }
        self.assertEqual(
            operation_ids,
            set(NORMAL_COMPLETION_ORDER).union(FAULT_FIXTURE_OPERATIONS),
        )
        self.assertEqual(
            len(NORMAL_COMPLETION_ORDER), len(set(NORMAL_COMPLETION_ORDER))
        )
        self.assertEqual('shutdown_database', NORMAL_COMPLETION_ORDER[-2])
        self.assertEqual('bring_online', NORMAL_COMPLETION_ORDER[-1])

    def test_live_completion_paths_are_unique_and_server_side(self):
        options = SimpleNamespace(
            apply_live=True,
            server_file_prefix='/var/lib/firebird/data/unique-ui-gate',
        )
        logical = completion_values(options, 'restore_logical')
        physical = completion_values(options, 'restore_physical')
        self.assertEqual(
            '/var/lib/firebird/data/unique-ui-gate.fbk',
            logical['Backup filename on the Firebird server'],
        )
        self.assertIn(
            'unique-ui-gate-nbackup.fdb',
            physical[
                'Restored database filename on the Firebird server'
            ],
        )
        self.assertEqual({}, completion_values(options, 'bring_online'))

    def test_required_fields_without_defaults_have_safe_preview_values(self):
        missing = []
        for operation in firebird_service_forms():
            values = PREVIEW_VALUES.get(operation['operation_id'], {})
            for field in operation['form']['fields']:
                if field.get('required') and 'default' not in field:
                    if field['label'] not in values:
                        missing.append(
                            f'{operation["operation_id"]}.'
                            f'{field["field_id"]}'
                        )
        self.assertEqual([], missing)

    def test_all_service_menu_groups_have_exact_visible_labels(self):
        self.assertEqual({
            'backup', 'restore', 'diagnostics', 'maintenance',
            'availability',
        }, set(MENU_GROUP_LABELS))

    def test_physical_service_forms_expose_operation_specific_flags(self):
        operations = {
            item['operation_id']: item for item in firebird_service_forms()
        }

        def options(operation_id, field_id):
            field = next(
                item for item in operations[operation_id]['form']['fields']
                if item['field_id'] == field_id
            )
            return {item['value'] for item in field['options']}

        self.assertEqual(
            {'NO_TRIGGERS'}, options('backup_physical', 'backup_flags')
        )
        self.assertEqual(
            {'IN_PLACE', 'SEQUENCE'},
            options('restore_physical', 'restore_flags'),
        )
        self.assertEqual(
            {'SEQUENCE'}, options('fixup_database', 'fixup_flags')
        )

    def test_validation_cases_cover_only_ui_reachable_invalid_inputs(self):
        self.assertEqual({
            'backup_logical', 'backup_physical', 'database_statistics',
            'restore_logical', 'restore_physical', 'set_page_cache_size',
            'set_sweep_interval', 'shutdown_database', 'sweep_database',
            'validate_database',
        }, set(VALIDATION_CASES))
        operations = {
            operation['operation_id']: operation
            for operation in firebird_service_forms()
        }
        for operation_id, case in VALIDATION_CASES.items():
            fields = {
                field['field_id']: field
                for field in operations[operation_id]['form']['fields']
            }
            field = fields[case['field_id']]
            self.assertTrue(case['expected'])
            if case['kind'] == 'required':
                self.assertTrue(field['required'])
                self.assertNotIn('default', field)
            else:
                self.assertEqual(field['label'], case['label'])
                self.assertIn('invalid_value', case)
                self.assertIn('valid_value', case)

        not_applicable = set(operations).difference(VALIDATION_CASES)
        self.assertEqual(11, len(not_applicable))
        self.assertIn('no meaningful invalid user input',
                      VALIDATION_NOT_APPLICABLE_REASON)

    def test_evidence_variant_distinguishes_theme_and_font_scale(self):
        self.assertEqual('default', evidence_variant(SimpleNamespace(
            theme='default', font_scale=100,
        )))
        self.assertEqual('scaled-font-150', evidence_variant(SimpleNamespace(
            theme='default', font_scale=150,
        )))
        self.assertEqual('high-contrast', evidence_variant(SimpleNamespace(
            theme='high-contrast', font_scale=100,
        )))

    def test_screenshots_receive_occurrences_and_deduplicated_manifest_rows(
            self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            screenshot = root / 'form/initial-1600x1000-default.png'
            screenshot.parent.mkdir()
            screenshot.write_bytes(b'png evidence')
            options = SimpleNamespace(
                manifest_output=root / 'screenshot_manifest.csv',
                width=1600,
                height=1000,
            )
            evidence = {
                'captured_at': '2026-09-07T12:00:00+00:00',
                'engine_id': 'firebird',
                'interface_id': 'firebird-native',
                'reference_version': '5.0.4',
                'server_label': 'localhost',
                'database_label': 'cdeadmin_demo.fdb',
                'viewport': '1600x1000',
                'theme': 'default',
                'font_scale': 100,
                'forms': [{
                    'command_id': 'database.firebird.validate_database',
                    'form_id': 'firebird_validate_database',
                    'operation_id': 'validate_database',
                    'declared_field_count': 1,
                    'observed_fields': [{'field_id': 'lock_timeout'}],
                    'layout': {'initial': {
                        'dialog_fits_viewport': True,
                    }},
                    'plan_state': 'ready',
                    'execution_available': True,
                    'completion': None,
                    'screenshots': {'initial': {
                        'path': str(screenshot),
                        'sha256': 'example-sha256',
                    }},
                }],
            }

            record_screenshot_evidence(options, evidence)
            evidence['forms'][0]['screenshots']['initial']['sha256'] = (
                'replacement-sha256'
            )
            record_screenshot_evidence(options, evidence)

            occurrence = json.loads(
                screenshot.with_name(
                    screenshot.stem + '.occurrence.json'
                ).read_text(encoding='utf-8')
            )
            with options.manifest_output.open(
                    newline='', encoding='utf-8') as source:
                rows = list(csv.DictReader(source))
        self.assertFalse(occurrence['control_values_recorded'])
        self.assertFalse(occurrence['credential_values_exported'])
        self.assertEqual('initial', occurrence['state'])
        self.assertEqual('default', occurrence['theme'])
        self.assertEqual(100, occurrence['font_scale'])
        self.assertTrue(occurrence['layout']['dialog_fits_viewport'])
        self.assertEqual(
            'replacement-sha256', occurrence['screenshot']['sha256']
        )
        self.assertEqual(1, len(rows))
        self.assertEqual('100%', rows[0]['font_scale'])
        self.assertIn('replacement-sha256', rows[0]['interaction_result'])


if __name__ == '__main__':
    unittest.main()
