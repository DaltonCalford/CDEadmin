#!/usr/bin/env python3
##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Fail closed unless every connection capability has live evidence."""

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
from pgadmin.cdeadmin.endpoints.connection_capabilities import (  # noqa: E402
    CONNECTION_CAPABILITY_CATEGORIES,
)


STATES = frozenset({'passed', 'not_applicable', 'not_run', 'failed'})


class LiveMatrixError(ValueError):
    """The live qualification matrix is malformed or overclaims support."""


def initialize_matrix():
    """Create a fail-closed matrix without manufacturing live evidence."""
    rows = {}
    for profile in registration_profiles():
        rows[profile['profile_id']] = {}
        declarations = profile['connection_capabilities']['categories']
        for category, declaration in declarations.items():
            rows[profile['profile_id']][category] = {
                'state': (
                    'not_applicable'
                    if declaration['state'] == 'not_applicable'
                    else 'not_run'
                ),
                'evidence': [],
            }
    return {
        'schema': 'cdeadmin.connection-live-matrix.v1',
        'profiles': rows,
    }


def audit(matrix, evidence_root=None):
    if not isinstance(matrix, dict) or matrix.get('schema') != (
            'cdeadmin.connection-live-matrix.v1'):
        raise LiveMatrixError('live matrix schema is invalid')
    rows = matrix.get('profiles')
    if not isinstance(rows, dict):
        raise LiveMatrixError('live matrix profiles must be an object')
    profiles = {
        item['profile_id']: item for item in registration_profiles()
    }
    if set(rows) != set(profiles):
        missing = sorted(set(profiles) - set(rows))
        unknown = sorted(set(rows) - set(profiles))
        raise LiveMatrixError(
            f'profile coverage mismatch; missing={missing}; unknown={unknown}'
        )
    root = Path(evidence_root).resolve() if evidence_root else None
    failures = []
    normalized = {}
    for profile_id, profile in profiles.items():
        categories = rows[profile_id]
        if not isinstance(categories, dict) or set(categories) != set(
                CONNECTION_CAPABILITY_CATEGORIES):
            raise LiveMatrixError(
                f'{profile_id} category coverage is incomplete')
        normalized[profile_id] = {}
        declarations = profile['connection_capabilities']['categories']
        for category in CONNECTION_CAPABILITY_CATEGORIES:
            item = categories[category]
            if not isinstance(item, dict) or item.get('state') not in STATES:
                raise LiveMatrixError(
                    f'{profile_id}.{category} state is invalid')
            state = item['state']
            evidence = item.get('evidence', [])
            if not isinstance(evidence, list) or any(
                    not isinstance(value, str) or not value.strip()
                    for value in evidence):
                raise LiveMatrixError(
                    f'{profile_id}.{category} evidence is invalid')
            if state == 'passed' and not evidence:
                raise LiveMatrixError(
                    f'{profile_id}.{category} passed without evidence')
            if state == 'not_applicable' and declarations[category][
                    'state'] != 'not_applicable':
                raise LiveMatrixError(
                    f'{profile_id}.{category} is not declared not applicable')
            missing_evidence = []
            if root is not None:
                for reference in evidence:
                    path = (root / reference).resolve()
                    if root not in path.parents or not path.is_file():
                        missing_evidence.append(reference)
            if state not in {'passed', 'not_applicable'}:
                failures.append(f'{profile_id}.{category}:{state}')
            if missing_evidence:
                failures.append(
                    f'{profile_id}.{category}:missing-evidence:' +
                    ','.join(missing_evidence)
                )
            normalized[profile_id][category] = {
                'state': state,
                'evidence': list(evidence),
            }
    return {
        'gate': 'cdeadmin-live-connection-mode-qualification',
        'profile_count': len(profiles),
        'category_count': (
            len(profiles) * len(CONNECTION_CAPABILITY_CATEGORIES)
        ),
        'complete': not failures,
        'failures': failures,
        'profiles': normalized,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('matrix', type=Path, nargs='?')
    parser.add_argument('--initialize', action='store_true')
    parser.add_argument('--evidence-root', type=Path)
    parser.add_argument('--output', type=Path)
    options = parser.parse_args(argv)
    try:
        if options.initialize:
            result = initialize_matrix()
        else:
            if options.matrix is None:
                raise LiveMatrixError('matrix path is required')
            matrix = json.loads(options.matrix.read_text(encoding='utf-8'))
            result = audit(matrix, options.evidence_root)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        result = {
            'gate': 'cdeadmin-live-connection-mode-qualification',
            'complete': False,
            'failures': [f'{type(exc).__name__}: {exc}'],
        }
    document = json.dumps(result, indent=2, sort_keys=True) + '\n'
    if options.output:
        options.output.parent.mkdir(parents=True, exist_ok=True)
        options.output.write_text(document, encoding='utf-8')
    else:
        sys.stdout.write(document)
    return 0 if options.initialize or result['complete'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
