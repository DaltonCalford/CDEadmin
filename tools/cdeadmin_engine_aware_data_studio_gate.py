#!/usr/bin/env python3
"""Verify the implemented engine-aware Data Studio integration surface."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


REQUIRED_VIEWS = (
    'DocumentDataGrid', 'GraphDataStudio', 'KeyValueDataGrid',
    'StructuredDataGrid', 'TimeSeriesView', 'VectorView', 'SearchView',
    'WideColumnView', 'ColumnarView', 'CubePivotView', 'QueryPlanView',
    'DataMovementWorkspace',
)
REQUIRED_ACTIONS = (
    'result_page', 'result_export', 'result_compare',
    'transaction_action', 'visual_admin_bulk_plan',
    'visual_admin_bulk_apply', 'visual_admin_rows_cancel',
)
REQUIRED_LANGUAGE_PROFILES = (
    'cypher', 'cql-3', 'redis-resp3-command', 'mongodb-query-api-json',
    'opensearch-sql-ppl', 'influxdb3-sql-influxql',
)
FORBIDDEN_COMMON_TRANSACTION_TOKENS = (
    'TX_STATUS_IDLE', 'TX_STATUS_ACTIVE', 'TX_STATUS_INTRANS',
    'TX_STATUS_INERROR', 'PQTRANS_',
)


def evaluate(source: Path):
    ui_path = source / (
        'web/pgadmin/static/js/Dialogs/ProviderWorkspaceContent.jsx'
    )
    route_path = source / (
        'web/pgadmin/browser/server_groups/servers/__init__.py'
    )
    studio_root = source / 'web/pgadmin/cdeadmin/data_studio'
    findings = []
    ui = ui_path.read_text(encoding='utf-8')
    routes = route_path.read_text(encoding='utf-8')
    common = '\n'.join(
        path.read_text(encoding='utf-8')
        for path in studio_root.glob('*.py')
    )
    for token in REQUIRED_VIEWS:
        if f'function {token}' not in ui:
            findings.append({'rule': 'specialized-view', 'missing': token})
    for token in REQUIRED_ACTIONS:
        if token not in ui or token not in routes:
            findings.append({'rule': 'workspace-action', 'missing': token})
    for token in REQUIRED_LANGUAGE_PROFILES:
        if token not in ui:
            findings.append({'rule': 'language-profile', 'missing': token})
    for token in FORBIDDEN_COMMON_TRANSACTION_TOKENS:
        if token in common:
            findings.append({
                'rule': 'transaction-authority', 'forbidden': token,
            })
    safeguards = {
        'result_endpoint_binding': 'endpoint_id=context.endpoint_id',
        'bulk_explicit_confirmation': "request.get('confirmed') is not True",
        'bulk_no_atomicity_claim': "'atomicity': 'not-claimed'",
        'bulk_no_retry': "'automatic_retry': False",
    }
    workspace = (
        source / 'web/pgadmin/cdeadmin/workspace/service.py'
    ).read_text(encoding='utf-8')
    for name, token in safeguards.items():
        if token not in workspace:
            findings.append({'rule': 'safeguard', 'missing': name})
    return {
        'schema': 'cdeadmin.engine-aware-data-studio-gate.v1',
        'views': len(REQUIRED_VIEWS),
        'actions': len(REQUIRED_ACTIONS),
        'language_profiles': len(REQUIRED_LANGUAGE_PROFILES),
        'violations': findings,
        'passed': not findings,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=Path.cwd())
    parser.add_argument('--json', action='store_true')
    arguments = parser.parse_args()
    result = evaluate(arguments.source.resolve())
    if arguments.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(
            'engine_aware_data_studio_gate=' +
            ('passed' if result['passed'] else 'failed')
        )
    raise SystemExit(0 if result['passed'] else 1)


if __name__ == '__main__':
    main()
