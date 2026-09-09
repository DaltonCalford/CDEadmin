#!/usr/bin/env python3
##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Generate DuckDB 1.5.2 native observations and metrics contracts."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

import duckdb


REFERENCE_VERSION = '1.5.2'
ENDPOINTS = (
    ('approx_database_count', 'SELECT * FROM duckdb_approx_database_count()',
     'runtime_metric'),
    ('connection_count', 'SELECT * FROM duckdb_connection_count()',
     'runtime_metric'),
    ('databases', 'SELECT * FROM duckdb_databases()', 'catalog_state'),
    ('external_file_cache', 'SELECT * FROM duckdb_external_file_cache()',
     'cache_state'),
    ('log_contexts', 'SELECT * FROM duckdb_log_contexts()',
     'diagnostic_context'),
    ('logs', 'SELECT * FROM duckdb_logs()', 'diagnostic_event'),
    ('memory', 'SELECT * FROM duckdb_memory()', 'runtime_metric'),
    ('prepared_statements', 'SELECT * FROM duckdb_prepared_statements()',
     'session_state'),
    ('temporary_files', 'SELECT * FROM duckdb_temporary_files()',
     'runtime_metric'),
    ('variables', 'SELECT * FROM duckdb_variables()', 'session_state'),
    ('database_size', 'SELECT * FROM pragma_database_size()',
     'storage_state'),
    ('metadata_info', 'SELECT * FROM pragma_metadata_info()',
     'storage_state'),
    ('platform', 'SELECT * FROM pragma_platform()', 'runtime_identity'),
    ('user_agent', 'SELECT * FROM pragma_user_agent()', 'runtime_identity'),
    ('version', 'SELECT * FROM pragma_version()', 'runtime_identity'),
)

METRIC_COLUMNS = {
    ('approx_database_count', 'approx_count'): ('databases', 'gauge'),
    ('connection_count', 'count'): ('connections', 'gauge'),
    ('external_file_cache', 'nr_bytes'): ('bytes', 'gauge'),
    ('memory', 'memory_usage_bytes'): ('bytes', 'gauge'),
    ('memory', 'temporary_storage_bytes'): ('bytes', 'gauge'),
    ('temporary_files', 'size'): ('bytes', 'gauge'),
    ('database_size', 'block_size'): ('bytes', 'gauge'),
    ('database_size', 'total_blocks'): ('blocks', 'gauge'),
    ('database_size', 'used_blocks'): ('blocks', 'gauge'),
    ('database_size', 'free_blocks'): ('blocks', 'gauge'),
    ('metadata_info', 'total_blocks'): ('blocks', 'gauge'),
    ('metadata_info', 'free_blocks'): ('blocks', 'gauge'),
}


def _sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _slug(value):
    return re.sub(r'[^a-z0-9]+', '_', value.lower()).strip('_')


def _evidence(authority, artifact, digest, format_name, license_id):
    return {
        'authority': authority,
        'artifact': artifact,
        'sha256': digest,
        'format': format_name,
        'license_id': license_id,
    }


def collect_inventory():
    if duckdb.__version__ != REFERENCE_VERSION:
        raise RuntimeError(
            f'DuckDB runtime must be {REFERENCE_VERSION}, got '
            f'{duckdb.__version__}'
        )
    connection = duckdb.connect(':memory:')
    endpoints = []
    try:
        for endpoint_id, query, classification in ENDPOINTS:
            cursor = connection.execute(query)
            columns = [{
                'name': description[0],
                'value_type': str(description[1]),
            } for description in cursor.description]
            cursor.fetchall()
            endpoints.append({
                'endpoint_id': endpoint_id,
                'native_query': query,
                'classification': classification,
                'columns': columns,
            })
    finally:
        connection.close()
    return {
        'schema': 'cdeadmin.engine-metrics-inventory.v1',
        'inventory_id': 'duckdb.metrics-inventory.1.5.2.v1',
        'engine_id': 'duckdb',
        'reference_version': REFERENCE_VERSION,
        'collection_authority': 'DuckDB 1.5.2 runtime catalogs',
        'endpoint_selection': (
            'All no-argument DuckDB runtime, connection, memory, cache, '
            'logging, prepared-statement, temporary-storage, session, '
            'database-size, metadata, platform and version observation '
            'relations exposed by the exact runtime.'
        ),
        'endpoints': endpoints,
        'completeness': {
            'runtime_version': duckdb.__version__,
            'endpoint_count': len(endpoints),
            'column_count': sum(
                len(endpoint['columns']) for endpoint in endpoints
            ),
            'selected_endpoints_exhausted': True,
        },
    }


def build_contract(inventory, inventory_path, live_path, source_archive):
    live = json.loads(live_path.read_text(encoding='utf-8'))
    if (
            live.get('exact_profile') != REFERENCE_VERSION or
            live.get('graphical_object_activation_ready') is not True or
            live.get('credential_values_exported') is not False):
        raise RuntimeError('DuckDB exact live evidence is not admissible')
    inventory_digest = _sha256(inventory_path)
    live_digest = _sha256(live_path)
    source_digest = _sha256(source_archive)
    classification_evidence = [{
        'evidence_id': 'duckdb-1.5.2-source-observation-catalog',
        **_evidence(
            'DuckDB Foundation', source_archive.name, source_digest,
            'DuckDB 1.5.2 source archive', 'MIT',
        ),
    }, {
        'evidence_id': 'duckdb-1.5.2-runtime-observation-inventory',
        **_evidence(
            'DuckDB 1.5.2 runtime', inventory_path.name, inventory_digest,
            'cdeadmin.engine-metrics-inventory.v1', 'PostgreSQL',
        ),
    }, {
        'evidence_id': 'duckdb-1.5.2-live-observation-gate',
        **_evidence(
            'DuckDB 1.5.2 runtime', live_path.name, live_digest,
            'cdeadmin.relational-provider-live-verification.v1',
            'PostgreSQL',
        ),
    }]
    observations = []
    metrics = []
    for endpoint in inventory['endpoints']:
        endpoint_id = endpoint['endpoint_id']
        for column in endpoint['columns']:
            column_name = column['name']
            observation_id = (
                f'duckdb.{_slug(endpoint_id)}.{_slug(column_name)}'
            )
            metric = METRIC_COLUMNS.get((endpoint_id, column_name))
            observations.append({
                'observation_id': observation_id,
                'native_name': f'{endpoint_id}.{column_name}',
                'scope': 'embedded_database_or_connection',
                'source': endpoint['native_query'],
                'value_type': column['value_type'],
                'observation_class': (
                    'operational_metric' if metric else
                    endpoint['classification']
                ),
                'description': (
                    f'DuckDB native {column_name} observation from '
                    f'{endpoint_id}.'
                ),
                'privilege': 'embedded_runtime',
                'version_condition': REFERENCE_VERSION,
                'collection_cost': 'bounded_native_catalog_scan',
                'poll_interval_seconds': 15 if metric else 60,
                'cardinality': (
                    f'one_per_{endpoint_id}_row'
                ),
                'redaction': (
                    'provider_sensitive_value_policy'
                    if endpoint_id in {'logs', 'variables'} else 'none'
                ),
                'evidence_ids': [
                    'duckdb-1.5.2-source-observation-catalog',
                    'duckdb-1.5.2-runtime-observation-inventory',
                    'duckdb-1.5.2-live-observation-gate',
                ],
            })
            if metric:
                unit, kind = metric
                metrics.append({
                    'metric_id': observation_id,
                    'observation_id': observation_id,
                    'unit': unit,
                    'kind': kind,
                    'reset_behavior': 'current_observation',
                    'aggregation': 'sum_across_native_rows',
                    'evidence_ids': [
                        'duckdb-1.5.2-runtime-observation-inventory',
                        'duckdb-1.5.2-live-observation-gate',
                    ],
                })
    observation_ids = [item['observation_id'] for item in observations]
    metric_ids = [item['metric_id'] for item in metrics]
    return {
        'schema': 'cdeadmin.engine-metrics.v2',
        'contract_id': 'duckdb.metrics.1.5.2.v1',
        'provider_id': 'org.cdeadmin.duckdb',
        'profile_id': 'duckdb-native',
        'engine_id': 'duckdb',
        'interface_id': 'duckdb-native',
        'reference_version': REFERENCE_VERSION,
        'catalog_evidence': _evidence(
            'DuckDB 1.5.2 runtime', inventory_path.name, inventory_digest,
            'cdeadmin.engine-metrics-inventory.v1', 'PostgreSQL',
        ),
        'classification_evidence': classification_evidence,
        'native_observations': observations,
        'metrics': metrics,
        'authoritative_observation_count': len(observation_ids),
        'authoritative_observation_ids': observation_ids,
        'authoritative_metric_count': len(metric_ids),
        'authoritative_metric_ids': metric_ids,
        'live_evidence_ids': ['duckdb-1.5.2-live-observation-gate'],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-archive', type=Path, required=True)
    parser.add_argument('--live-evidence', type=Path, required=True)
    parser.add_argument('--inventory-output', type=Path, required=True)
    parser.add_argument('--contract-output', type=Path, required=True)
    options = parser.parse_args()
    inventory = collect_inventory()
    options.inventory_output.parent.mkdir(parents=True, exist_ok=True)
    options.inventory_output.write_text(
        json.dumps(inventory, indent=2, sort_keys=True) + '\n',
        encoding='utf-8',
    )
    contract = build_contract(
        inventory, options.inventory_output.resolve(),
        options.live_evidence.resolve(), options.source_archive.resolve(),
    )
    options.contract_output.parent.mkdir(parents=True, exist_ok=True)
    options.contract_output.write_text(
        json.dumps(contract, indent=2, sort_keys=True) + '\n',
        encoding='utf-8',
    )
    print(json.dumps({
        'endpoint_count': inventory['completeness']['endpoint_count'],
        'observation_count': len(contract['native_observations']),
        'metric_count': len(contract['metrics']),
    }, indent=2, sort_keys=True))


if __name__ == '__main__':
    main()
