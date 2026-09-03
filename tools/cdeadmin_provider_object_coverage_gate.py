#!/usr/bin/env python3
##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Audit provider navigator/editor concept declarations for every engine."""

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

from pgadmin.cdeadmin.visual_admin import (  # noqa: E402
    PORTFOLIO_ENGINE_IDS, catalog_for_engine,
)


def audit():
    """Return a deterministic, fail-closed portfolio inventory."""
    engines = {}
    failures = []
    concept_count = 0
    catalogued_count = 0
    missing_count = 0
    external_surface_count = 0
    declared_count = 0
    live_evidence_count = 0
    for engine_id in PORTFOLIO_ENGINE_IDS:
        coverage = catalog_for_engine(engine_id)['concept_coverage']
        engine_rows = []
        for family in coverage['families']:
            for concept in family['concepts']:
                row = {
                    'family_id': family['family_id'],
                    **concept,
                }
                engine_rows.append(row)
                concept_count += 1
                if concept['catalog_state'] == 'catalogued':
                    catalogued_count += 1
                elif concept['catalog_state'] == 'external_surface':
                    external_surface_count += 1
                else:
                    missing_count += 1
                    if concept['declared_status'] != 'not_applicable':
                        failures.append(
                            f"{engine_id}.{family['family_id']}."
                            f"{concept['concept_id']}:missing-catalog-object"
                        )
                if concept['declared_status'] is None:
                    failures.append(
                        f"{engine_id}.{family['family_id']}."
                        f"{concept['concept_id']}:undeclared"
                    )
                else:
                    declared_count += 1
                if concept['live_evidence']:
                    live_evidence_count += 1
                elif concept['declared_status'] in {
                        'supported', 'read_only'}:
                    failures.append(
                        f"{engine_id}.{family['family_id']}."
                        f"{concept['concept_id']}:missing-live-evidence"
                    )
        engines[engine_id] = {
            'activation_ready': coverage['activation_ready'],
            'concepts': engine_rows,
        }
    return {
        'schema': 'cdeadmin.provider-object-coverage-gate.v1',
        'gate': 'provider-navigator-object-editor-coverage',
        'complete': not failures,
        'engine_count': len(engines),
        'concept_count': concept_count,
        'catalogued_count': catalogued_count,
        'missing_catalog_count': missing_count,
        'external_surface_count': external_surface_count,
        'declared_count': declared_count,
        'undeclared_count': concept_count - declared_count,
        'live_evidence_count': live_evidence_count,
        'failures': failures,
        'engines': engines,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    parser.add_argument(
        '--inventory', action='store_true',
        help='record the incomplete baseline without returning a failure',
    )
    options = parser.parse_args(argv)
    result = audit()
    document = json.dumps(result, indent=2, sort_keys=True) + '\n'
    if options.output:
        options.output.parent.mkdir(parents=True, exist_ok=True)
        options.output.write_text(document, encoding='utf-8')
    else:
        sys.stdout.write(document)
    return 0 if options.inventory or result['complete'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
