#!/usr/bin/env python3
##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Audit provider-specific operational workspace declarations."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / 'web'
for path in (ROOT, WEB):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from tools.cdeadmin_provider_object_coverage_gate import (  # noqa: E402
    DEFERRED_ENGINE_IDS, provider_catalogs,
)
from pgadmin.cdeadmin.visual_admin.operational_workspace import (  # noqa: E402
    FACETS, OPERATIONAL_WORKSPACE_SCHEMA, build_operational_workspace,
)


def integration_violations(root=ROOT):
    """Require the workspace projection to be mounted in API and UI."""
    requirements = {
        'web/pgadmin/cdeadmin/workspace/service.py': (
            'build_operational_workspace', "'operational_workspace':",
        ),
        'web/pgadmin/static/js/Dialogs/ProviderWorkspaceContent.jsx': (
            'function OperationalWorkspace', 'function TopologyView',
            'Refresh provider state', 'Operation progress and history',
            "action: 'visual_admin_validate'",
            "action: 'visual_admin_plan'", "action: 'visual_admin_apply'",
            "visual_admin_operation_${suffix}",
        ),
    }
    failures = []
    for relative, needles in requirements.items():
        path = root / relative
        if not path.is_file():
            failures.append(f'integration:missing-file:{relative}')
            continue
        source = path.read_text(encoding='utf-8')
        for needle in needles:
            if needle not in source:
                failures.append(
                    f'integration:{relative}:missing:{needle}'
                )
    return failures


def audit(catalogs=None):
    catalogs = provider_catalogs() if catalogs is None else catalogs
    required_general = {
        item.facet_id for item in FACETS if not item.distributed
    }
    required_distributed = {
        item.facet_id for item in FACETS if item.distributed
    }
    profiles = {}
    failures = integration_violations()
    state_counts = {'operational': 0, 'observable': 0, 'unavailable': 0}
    for profile_id in sorted(catalogs):
        descriptor = catalogs[profile_id]['descriptor']
        workspace = build_operational_workspace(descriptor)
        facets = {item['facet_id']: item for item in workspace['facets']}
        missing = sorted(required_general.difference(facets))
        if workspace['distributed']:
            missing.extend(sorted(required_distributed.difference(facets)))
        if missing:
            failures.append(f'{profile_id}:missing:{",".join(missing)}')
        if workspace['schema'] != OPERATIONAL_WORKSPACE_SCHEMA:
            failures.append(f'{profile_id}:invalid-schema')
        contract = workspace['execution_contract']
        if not contract.get('provider_compilation_required'):
            failures.append(f'{profile_id}:common-compilation-admitted')
        if not contract.get('provider_finality_authority'):
            failures.append(f'{profile_id}:common-finality-admitted')
        if contract.get('automatic_mutation_retry') is not False:
            failures.append(f'{profile_id}:automatic-retry-admitted')
        declared_operations = {
            (object_item['resource_kind'], operation['operation_id'])
            for object_item in descriptor.get('objects', [])
            for operation in object_item.get('operations', [])
        }
        for facet in facets.values():
            state = facet.get('catalog_state')
            if state not in state_counts:
                failures.append(
                    f'{profile_id}.{facet["facet_id"]}:invalid-state'
                )
                continue
            state_counts[state] += 1
            if state == 'unavailable' and not facet.get(
                    'unavailable_reason'):
                failures.append(
                    f'{profile_id}.{facet["facet_id"]}:silent-unavailable'
                )
            for operation in facet.get('operations', []):
                key = (
                    operation.get('resource_kind'),
                    operation.get('operation_id'),
                )
                if key not in declared_operations:
                    failures.append(
                        f'{profile_id}.{facet["facet_id"]}:'
                        'invented-operation'
                    )
        profiles[profile_id] = {
            'engine_id': workspace['engine_id'],
            'distributed': workspace['distributed'],
            'facet_count': len(facets),
            'operational_count': sum(
                item['catalog_state'] == 'operational'
                for item in facets.values()
            ),
            'observable_count': sum(
                item['catalog_state'] == 'observable'
                for item in facets.values()
            ),
            'unavailable_count': sum(
                item['catalog_state'] == 'unavailable'
                for item in facets.values()
            ),
        }
    return {
        'schema': 'cdeadmin.operational-workspace-gate.v1',
        'gate': 'provider-operational-workspace-structural-coverage',
        'complete': not failures,
        'scope': {
            'profile_ids': sorted(profiles),
            'deferred_engine_ids': list(DEFERRED_ENGINE_IDS),
            'live_activation': 'delegated-to-strict-provider-engine-gates',
        },
        'profile_count': len(profiles),
        'general_facet_count': len(required_general),
        'distributed_facet_count': len(required_distributed),
        'state_counts': state_counts,
        'integration_complete': not any(
            item.startswith('integration:') for item in failures
        ),
        'failures': failures,
        'profiles': profiles,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    options = parser.parse_args(argv)
    result = audit()
    document = json.dumps(result, indent=2, sort_keys=True) + '\n'
    if options.output:
        options.output.parent.mkdir(parents=True, exist_ok=True)
        options.output.write_text(document, encoding='utf-8')
    else:
        sys.stdout.write(document)
    return 0 if result['complete'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
