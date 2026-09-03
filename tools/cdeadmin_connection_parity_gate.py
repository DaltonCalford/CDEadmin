#!/usr/bin/env python3
##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Audit every active endpoint profile against the connection contract."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from types import ModuleType


ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / 'web'
if str(WEB) not in sys.path:
    sys.path.insert(0, str(WEB))
if 'pgadmin' not in sys.modules:
    package = ModuleType('pgadmin')
    package.__path__ = [str(WEB / 'pgadmin')]
    sys.modules['pgadmin'] = package

from pgadmin.cdeadmin.endpoints import registration_profiles  # noqa: E402


def audit():
    profiles = []
    for profile in registration_profiles():
        declaration = profile['connection_capabilities']
        incomplete = [
            name for name, item in declaration['categories'].items()
            if item['state'] not in {'complete', 'not_applicable'}
        ]
        profiles.append({
            'profile_id': profile['profile_id'],
            'engine_id': profile['engine_id'],
            'interface_id': profile['interface_id'],
            'contract_version': declaration['contract_version'],
            'complete': not incomplete,
            'incomplete_categories': incomplete,
            'categories': declaration['categories'],
        })
    incomplete_profiles = [
        item['profile_id'] for item in profiles if not item['complete']
    ]
    return {
        'gate': 'cdeadmin-complete-reference-engine-connections',
        'profile_count': len(profiles),
        'complete': not incomplete_profiles,
        'incomplete_profiles': incomplete_profiles,
        'profiles': profiles,
    }


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument(
        '--allow-incomplete', action='store_true',
        help='write the baseline without returning a failing status',
    )
    parser.add_argument('--output', type=Path)
    options = parser.parse_args(argv)
    result = audit()
    encoded = json.dumps(result, indent=2, sort_keys=True) + '\n'
    if options.output:
        options.output.parent.mkdir(parents=True, exist_ok=True)
        options.output.write_text(encoded, encoding='utf-8')
    else:
        sys.stdout.write(encoded)
    return 0 if result['complete'] or options.allow_incomplete else 1


if __name__ == '__main__':
    raise SystemExit(main())
