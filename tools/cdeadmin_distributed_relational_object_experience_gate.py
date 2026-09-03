#!/usr/bin/env python3
##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Gate distributed/hybrid relational navigator and editor declarations."""

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

from pgadmin.cdeadmin.providers.cockroachdb.provider import (  # noqa: E402
    ADMINISTRATION as COCKROACHDB_ADMINISTRATION,
)
from pgadmin.cdeadmin.providers.dolt.provider import (  # noqa: E402
    ADMINISTRATION as DOLT_ADMINISTRATION,
)
from pgadmin.cdeadmin.providers.immudb.provider import (  # noqa: E402
    ADMINISTRATION as IMMUDB_ADMINISTRATION,
)
from pgadmin.cdeadmin.providers.tidb.provider import (  # noqa: E402
    ADMINISTRATION as TIDB_ADMINISTRATION,
)
from pgadmin.cdeadmin.providers.vitess.provider import (  # noqa: E402
    ADMINISTRATION as VITESS_ADMINISTRATION,
)
from pgadmin.cdeadmin.providers.yugabytedb.provider import (  # noqa: E402
    ADMINISTRATION as YUGABYTEDB_ADMINISTRATION,
)
from pgadmin.cdeadmin.visual_admin import (  # noqa: E402
    apply_live_evidence, catalog_for_engine, enrich_engine_experience,
    load_live_evidence,
)


ADMINISTRATIONS = {
    'cockroachdb': COCKROACHDB_ADMINISTRATION,
    'dolt': DOLT_ADMINISTRATION,
    'immudb': IMMUDB_ADMINISTRATION,
    'tidb': TIDB_ADMINISTRATION,
    'vitess': VITESS_ADMINISTRATION,
    'yugabytedb': YUGABYTEDB_ADMINISTRATION,
}


def provider_catalogs(live_evidence_paths=()):
    catalogs = {
        engine_id: enrich_engine_experience(
            administration.catalog(catalog_for_engine(engine_id))
        )
        for engine_id, administration in ADMINISTRATIONS.items()
    }
    for evidence_path in live_evidence_paths:
        evidence, artifact = load_live_evidence(evidence_path)
        engine_id = evidence.get('engine_id')
        if engine_id not in catalogs:
            raise ValueError(
                'live evidence is not for a distributed/hybrid relational '
                f'engine: {engine_id}'
            )
        catalogs[engine_id] = enrich_engine_experience(
            apply_live_evidence(
                catalogs[engine_id], evidence, artifact=artifact,
            )
        )
    return catalogs


def audit(live_evidence_paths=()):
    engines = {}
    structural_failures = []
    live_failures = []
    for engine_id, catalog in sorted(
            provider_catalogs(live_evidence_paths).items()):
        coverage = copy.deepcopy(catalog['concept_coverage'])
        if not coverage['declaration_ready']:
            structural_failures.append(engine_id)
        if not coverage['activation_ready']:
            live_failures.append(engine_id)
        engines[engine_id] = coverage
    return {
        'schema': 'cdeadmin.distributed-relational-object-gate.v1',
        'gate': 'distributed-hybrid-relational-object-experience',
        'engine_count': len(engines),
        'structural_complete': not structural_failures,
        'live_complete': not live_failures,
        'structural_failures': structural_failures,
        'live_failures': live_failures,
        'engines': engines,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--require-live', action='store_true')
    parser.add_argument(
        '--live-evidence', type=Path, action='append', default=[],
        help='Provider-generated exact object-operation evidence artifact.',
    )
    options = parser.parse_args(argv)
    result = audit(options.live_evidence)
    document = json.dumps(result, indent=2, sort_keys=True) + '\n'
    if options.output:
        options.output.parent.mkdir(parents=True, exist_ok=True)
        options.output.write_text(document, encoding='utf-8')
    else:
        sys.stdout.write(document)
    complete = (
        result['live_complete']
        if options.require_live else result['structural_complete']
    )
    return 0 if complete else 1


if __name__ == '__main__':
    raise SystemExit(main())
