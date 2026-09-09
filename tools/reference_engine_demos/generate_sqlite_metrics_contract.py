#!/usr/bin/env python3
##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Generate exact SQLite 3.53.0 observation and metrics contracts."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import zipfile
from pathlib import Path


REFERENCE_VERSION = '3.53.0'
HEADER_SUFFIX = '/src/sqlite.h.in'
PRAGMAS = (
    ('page_count', 'storage_metric'),
    ('freelist_count', 'storage_metric'),
    ('page_size', 'storage_metric'),
    ('max_page_count', 'storage_capacity'),
    ('data_version', 'change_generation'),
    ('schema_version', 'change_generation'),
    ('cache_size', 'configuration'),
    ('cache_spill', 'configuration'),
    ('journal_mode', 'configuration'),
    ('locking_mode', 'configuration'),
    ('synchronous', 'configuration'),
    ('temp_store', 'configuration'),
    ('wal_autocheckpoint', 'configuration'),
    ('journal_size_limit', 'configuration'),
    ('auto_vacuum', 'configuration'),
    ('application_id', 'application_metadata'),
    ('user_version', 'application_metadata'),
    ('secure_delete', 'configuration'),
    ('query_only', 'session_state'),
    ('read_uncommitted', 'session_state'),
    ('recursive_triggers', 'session_state'),
    ('busy_timeout', 'session_state'),
    ('foreign_keys', 'session_state'),
)
METRICS = {
    'page_count': ('pages', 'gauge', 'current_database_state'),
    'freelist_count': ('pages', 'gauge', 'current_database_state'),
    'page_size': ('bytes', 'gauge', 'database_reconfiguration'),
    'max_page_count': ('pages', 'gauge', 'database_reconfiguration'),
    'data_version': ('generation', 'gauge', 'connection_relative_change'),
    'schema_version': ('generation', 'gauge', 'schema_change'),
}


def _sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _evidence(authority, artifact, digest, format_name, license_id):
    return {
        'authority': authority,
        'artifact': artifact,
        'sha256': digest,
        'format': format_name,
        'license_id': license_id,
    }


def _status_inventory(source_archive):
    with zipfile.ZipFile(source_archive) as archive:
        matches = [
            name for name in archive.namelist()
            if name.endswith(HEADER_SUFFIX)
        ]
        if len(matches) != 1:
            raise RuntimeError('SQLite public header is unavailable')
        text = archive.read(matches[0]).decode('utf-8')
    records = []
    pattern = re.compile(
        r'^#define\s+SQLITE_(?P<family>DBSTATUS|STATUS)_(?P<name>[A-Z0-9_]+)'
        r'\s+(?P<value>\d+)', re.MULTILINE,
    )
    for match in pattern.finditer(text):
        family = match.group('family')
        name = match.group('name')
        if family == 'DBSTATUS' and name == 'MAX':
            continue
        records.append({
            'native_name': f'SQLITE_{family}_{name}',
            'numeric_id': int(match.group('value')),
            'access_path': (
                'sqlite3_db_status' if family == 'DBSTATUS'
                else 'sqlite3_status64'
            ),
            'scope': 'connection_handle' if family == 'DBSTATUS' else (
                'library_process'
            ),
            'dbapi_accessible': False,
            'admission_state': 'not_exposed_by_cpython_sqlite3_dbapi',
        })
    if len(records) != 24:
        raise RuntimeError(
            f'Expected 24 SQLite C status counters, found {len(records)}'
        )
    return records


def collect_inventory(source_archive):
    if sqlite3.sqlite_version != REFERENCE_VERSION:
        raise RuntimeError(
            f'SQLite runtime must be {REFERENCE_VERSION}, got '
            f'{sqlite3.sqlite_version}'
        )
    connection = sqlite3.connect(':memory:')
    observations = []
    try:
        available = {
            row[0] for row in connection.execute('PRAGMA pragma_list')
        }
        for name, classification in PRAGMAS:
            if name not in available:
                raise RuntimeError(f'SQLite PRAGMA is absent: {name}')
            source = f'PRAGMA main.{name}'
            row = connection.execute(source).fetchone()
            if row is None or len(row) != 1:
                raise RuntimeError(
                    f'SQLite scalar observation is unavailable: {name}'
                )
            observations.append({
                'endpoint_id': name,
                'native_query': source,
                'classification': classification,
                'value_type': type(row[0]).__name__,
            })
    finally:
        connection.close()
    c_status = _status_inventory(source_archive)
    return {
        'schema': 'cdeadmin.engine-metrics-inventory.v1',
        'inventory_id': 'sqlite.metrics-inventory.3.53.0.v1',
        'engine_id': 'sqlite',
        'reference_version': REFERENCE_VERSION,
        'collection_authority': (
            'SQLite 3.53.0 runtime PRAGMA catalog and public C header'
        ),
        'endpoint_selection': (
            'Every safe, no-argument scalar database/storage/change/session '
            'PRAGMA admitted by the provider, plus every SQLITE_STATUS and '
            'SQLITE_DBSTATUS counter declared by the exact public header.'
        ),
        'endpoints': observations,
        'native_c_status_surface': c_status,
        'completeness': {
            'runtime_version': sqlite3.sqlite_version,
            'dbapi_scalar_observation_count': len(observations),
            'native_c_status_count': len(c_status),
            'selected_endpoints_exhausted': True,
            'native_c_status_constants_exhausted': True,
            'driver_boundary_disclosed': True,
        },
    }


def build_contract(inventory, inventory_path, live_path, source_archive):
    live = json.loads(live_path.read_text(encoding='utf-8'))
    if (
            live.get('exact_profile') != REFERENCE_VERSION or
            live.get('dialect_qualification_ready') is not True or
            live.get('credential_values_exported') is not False):
        raise RuntimeError('SQLite exact live evidence is not admissible')
    evidence = [{
        'evidence_id': 'sqlite-3.53.0-public-status-catalog',
        **_evidence(
            'SQLite Consortium', source_archive.name,
            _sha256(source_archive), 'SQLite 3.53.0 source archive',
            'blessing',
        ),
    }, {
        'evidence_id': 'sqlite-3.53.0-runtime-observation-inventory',
        **_evidence(
            'SQLite 3.53.0 runtime', inventory_path.name,
            _sha256(inventory_path),
            'cdeadmin.engine-metrics-inventory.v1', 'PostgreSQL',
        ),
    }, {
        'evidence_id': 'sqlite-3.53.0-live-observation-gate',
        **_evidence(
            'SQLite 3.53.0 runtime', live_path.name, _sha256(live_path),
            'cdeadmin.relational-provider-live-verification.v1',
            'PostgreSQL',
        ),
    }]
    observations = []
    metrics = []
    for endpoint in inventory['endpoints']:
        name = endpoint['endpoint_id']
        observation_id = f'sqlite.main.{name}'
        metric = METRICS.get(name)
        observations.append({
            'observation_id': observation_id,
            'native_name': name,
            'scope': 'main_database_or_connection',
            'source': endpoint['native_query'],
            'value_type': endpoint['value_type'],
            'observation_class': (
                'operational_metric' if metric else
                endpoint['classification']
            ),
            'description': f'SQLite native scalar PRAGMA {name}.',
            'privilege': 'embedded_runtime',
            'version_condition': REFERENCE_VERSION,
            'collection_cost': 'single_scalar_pragma',
            'poll_interval_seconds': 15 if metric else 60,
            'cardinality': 'one_per_open_main_database',
            'redaction': 'none',
            'evidence_ids': [
                'sqlite-3.53.0-runtime-observation-inventory',
                'sqlite-3.53.0-live-observation-gate',
            ],
        })
        if metric:
            unit, kind, reset = metric
            metrics.append({
                'metric_id': observation_id,
                'observation_id': observation_id,
                'unit': unit,
                'kind': kind,
                'reset_behavior': reset,
                'aggregation': 'one_per_open_main_database',
                'evidence_ids': [
                    'sqlite-3.53.0-runtime-observation-inventory',
                    'sqlite-3.53.0-live-observation-gate',
                ],
            })
    observation_ids = [item['observation_id'] for item in observations]
    metric_ids = [item['metric_id'] for item in metrics]
    return {
        'schema': 'cdeadmin.engine-metrics.v2',
        'contract_id': 'sqlite.metrics.3.53.0.v1',
        'provider_id': 'org.cdeadmin.sqlite',
        'profile_id': 'sqlite-native',
        'engine_id': 'sqlite',
        'interface_id': 'sqlite-native',
        'reference_version': REFERENCE_VERSION,
        'catalog_evidence': {
            key: value for key, value in evidence[0].items()
            if key != 'evidence_id'
        },
        'classification_evidence': evidence,
        'native_observations': observations,
        'metrics': metrics,
        'authoritative_observation_count': len(observation_ids),
        'authoritative_observation_ids': observation_ids,
        'authoritative_metric_count': len(metric_ids),
        'authoritative_metric_ids': metric_ids,
        'unavailable_native_c_status_surface': (
            inventory['native_c_status_surface']
        ),
        'driver_boundary': (
            'CPython sqlite3 does not expose sqlite3_status64 or '
            'sqlite3_db_status. These counters are inventoried but are not '
            'misrepresented as collectable provider metrics.'
        ),
        'live_evidence_ids': ['sqlite-3.53.0-live-observation-gate'],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-archive', type=Path, required=True)
    parser.add_argument('--live-evidence', type=Path, required=True)
    parser.add_argument('--inventory-output', type=Path, required=True)
    parser.add_argument('--contract-output', type=Path, required=True)
    options = parser.parse_args()
    inventory = collect_inventory(options.source_archive.resolve())
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
        'dbapi_observation_count': len(contract['native_observations']),
        'metric_count': len(contract['metrics']),
        'native_c_status_count': len(
            inventory['native_c_status_surface']
        ),
    }, indent=2, sort_keys=True))


if __name__ == '__main__':
    main()
