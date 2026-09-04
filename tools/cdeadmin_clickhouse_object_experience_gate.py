#!/usr/bin/env python3
##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Gate ClickHouse columnar and semantic object experiences."""

from __future__ import annotations

import argparse
import copy
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

from pgadmin.cdeadmin.providers.clickhouse.client import (  # noqa: E402
    ClickHouseClient,
)
from pgadmin.cdeadmin.visual_admin import (  # noqa: E402
    apply_live_evidence, catalog_for_engine, enrich_engine_experience,
    load_live_evidence,
)


def provider_catalog(live_evidence_paths=()):
    adapter = ClickHouseClient.__new__(ClickHouseClient)
    catalog = enrich_engine_experience(
        adapter.visual_admin_catalog(catalog_for_engine('clickhouse'))
    )
    for evidence_path in live_evidence_paths:
        evidence, artifact = load_live_evidence(evidence_path)
        catalog = enrich_engine_experience(apply_live_evidence(
            catalog, evidence, artifact=artifact,
        ))
    return catalog


def audit(live_evidence_paths=()):
    coverage = copy.deepcopy(
        provider_catalog(live_evidence_paths)['concept_coverage']
    )
    return {
        'schema': 'cdeadmin.clickhouse-object-gate.v1',
        'gate': 'clickhouse-columnar-semantic-object-experience',
        'structural_complete': coverage['declaration_ready'],
        'live_complete': coverage['activation_ready'],
        'coverage': coverage,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--require-live', action='store_true')
    parser.add_argument(
        '--live-evidence', type=Path, action='append', default=[],
    )
    options = parser.parse_args(argv)
    result = audit(options.live_evidence)
    document = json.dumps(result, indent=2, sort_keys=True) + '\n'
    if options.output:
        options.output.parent.mkdir(parents=True, exist_ok=True)
        options.output.write_text(document, encoding='utf-8')
    else:
        sys.stdout.write(document)
    complete = result[
        'live_complete' if options.require_live else 'structural_complete'
    ]
    return 0 if complete else 1


if __name__ == '__main__':
    raise SystemExit(main())
