#!/usr/bin/env python3
##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Export the exhaustive active-provider registration form QA matrix."""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path
from types import ModuleType


ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / 'web'
if str(WEB) not in sys.path:
    sys.path.insert(0, str(WEB))
if 'pgadmin' not in sys.modules:
    package = ModuleType('pgadmin')
    package.__path__ = [str(WEB / 'pgadmin')]
    sys.modules['pgadmin'] = package

from pgadmin.cdeadmin.endpoints import registration_profiles  # noqa: E402


VIEWPORTS = ('narrow', 'standard', 'wide', 'scaled-font', 'high-contrast')
BASE_STATES = ('initial', 'validation_error', 'cancelled')
MUTATION_STATES = ('preview', 'completed')
DESTRUCTIVE_STATES = ('safe_delete_confirmation',)


def _arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    return parser.parse_args()


def _states(scope, operation_id):
    result = list(BASE_STATES)
    if operation_id not in {'define', 'connect'}:
        result.extend(MUTATION_STATES)
    if operation_id in {'drop', 'remove'}:
        result.extend(DESTRUCTIVE_STATES)
    return tuple(result)


def main():
    args = _arguments()
    rows = []
    for profile in registration_profiles():
        for scope in ('server', 'database'):
            forms = profile['form_contract'][scope]['forms']
            for operation_id, form in forms.items():
                supported = form.get('supported') is True
                for viewport in VIEWPORTS:
                    for state in _states(scope, operation_id):
                        rows.append({
                            'engine_id': profile['engine_id'],
                            'interface_id': profile['interface_id'],
                            'profile_id': profile['profile_id'],
                            'reference_version': profile['profile_version'],
                            'scope': scope,
                            'operation_id': operation_id,
                            'form_id': form['form_id'],
                            'field_count': len(form.get('fields') or []),
                            'supported': str(supported).lower(),
                            'viewport_or_mode': viewport,
                            'state': state,
                            'qa_status': (
                                'not_run' if supported else
                                'not_applicable_evidence_required'
                            ),
                            'screenshot_path': '',
                            'occurrence_path': '',
                            'interaction_result_path': '',
                            'transaction_proof_id': '',
                            'disabled_reason': (
                                form.get('disabled_reason') or ''
                            ),
                        })
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('w', encoding='utf-8', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print(f'{len(rows)} registration-form evidence obligations')


if __name__ == '__main__':
    main()
