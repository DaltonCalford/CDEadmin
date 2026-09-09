#!/usr/bin/env python3
##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Generate MariaDB 12.2.2 native-status and metrics contracts."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

import mariadb


REFERENCE_VERSION = '12.2.2'
STATUS_SOURCE = Path('sql/mysqld.cc')

# These are deliberately explicit. A runtime value being numeric does not
# prove whether it is a counter, gauge, duration, high-water mark, or
# resettable
# statistic. Each admitted item is defined by MariaDB's status registry and its
# documented status-variable semantics; every other runtime status remains a
# visible native observation without invented metric behavior.
METRICS = {
    'Aborted_clients': (
        'connections', 'counter', 'flush_status',
        'Client connections terminated without a clean close.'),
    'Aborted_connects': (
        'connection_attempts', 'counter', 'flush_status',
        'Failed server connection attempts.'),
    'Bytes_received': (
        'bytes', 'counter', 'flush_status',
        'Bytes received from clients.'),
    'Bytes_sent': (
        'bytes', 'counter', 'flush_status',
        'Bytes sent to clients.'),
    'Connections': (
        'connection_attempts', 'counter', 'server_restart',
        'Connection attempts, whether successful or unsuccessful.'),
    'Created_tmp_disk_tables': (
        'tables', 'counter', 'flush_status',
        'On-disk temporary tables created.'),
    'Created_tmp_files': (
        'files', 'counter', 'flush_status',
        'Temporary files created.'),
    'Created_tmp_tables': (
        'tables', 'counter', 'flush_status',
        'In-memory temporary tables created.'),
    'Max_tmp_space_used': (
        'bytes', 'gauge', 'flush_status',
        'Maximum temporary space used since the status reset.'),
    'Max_used_connections': (
        'connections', 'gauge', 'flush_status',
        'Maximum concurrently open connections since the status reset.'),
    'Memory_used': (
        'bytes', 'gauge', 'current_observation',
        'Current server memory use reported by MariaDB.'),
    'Open_files': (
        'files', 'gauge', 'current_observation',
        'Regular files currently open by the server.'),
    'Open_tables': (
        'tables', 'gauge', 'current_observation',
        'Tables currently open, excluding temporary tables.'),
    'Opened_files': (
        'files', 'counter', 'server_restart',
        'Files opened since server startup.'),
    'Opened_tables': (
        'tables', 'counter', 'flush_status',
        'Tables opened since the status reset.'),
    'Prepared_stmt_count': (
        'statements', 'gauge', 'current_observation',
        'Prepared statements currently present.'),
    'Queries': (
        'statements', 'counter', 'flush_status',
        'Statements executed, including statements in stored programs.'),
    'Questions': (
        'statements', 'counter', 'flush_status',
        'Client statements executed, subject to MariaDB exclusions.'),
    'Rows_read': (
        'rows', 'counter', 'flush_status',
        'Rows read since the status reset.'),
    'Rows_sent': (
        'rows', 'counter', 'flush_status',
        'Rows sent to clients since the status reset.'),
    'Slow_queries': (
        'queries', 'counter', 'flush_status',
        'Queries classified as slow since the status reset.'),
    'Threads_cached': (
        'threads', 'gauge', 'current_observation',
        'Threads currently held in the thread cache.'),
    'Threads_connected': (
        'connections', 'gauge', 'current_observation',
        'Clients currently connected to the server.'),
    'Threads_created': (
        'threads', 'counter', 'server_restart',
        'Threads created to service client connections.'),
    'Threads_running': (
        'connections', 'gauge', 'current_observation',
        'Client connections currently running a command.'),
    'Tmp_space_used': (
        'bytes', 'gauge', 'current_observation',
        'Temporary space currently used.'),
    'Uptime': (
        'seconds', 'gauge', 'server_restart',
        'Seconds elapsed since server startup.'),
    'Uptime_since_flush_status': (
        'seconds', 'gauge', 'flush_status',
        'Seconds elapsed since the last FLUSH STATUS.'),
}
METRICS_BY_LOWER = {
    name.lower(): value for name, value in METRICS.items()
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


def _value_type(value):
    text = str(value)
    if re.fullmatch(r'[+-]?[0-9]+', text):
        return 'integer'
    if re.fullmatch(r'[+-]?(?:[0-9]+\.[0-9]*|[0-9]*\.[0-9]+)', text):
        return 'decimal'
    if text.upper() in {'ON', 'OFF', 'YES', 'NO', 'TRUE', 'FALSE'}:
        return 'boolean'
    return 'text'


def _source_definitions(source_root):
    path = source_root / STATUS_SOURCE
    text = path.read_text(encoding='utf-8')
    definitions = {}
    for match in re.finditer(
            r'(?m)^\s*\{"(?P<name>[A-Za-z0-9_]+)"\s*,(?P<body>[^\n]+)',
            text):
        body = match.group('body').strip()
        show_type = re.findall(r'\bSHOW_[A-Z0-9_]+\b', body)
        definitions.setdefault(match.group('name').lower(), {
            'source': f'{STATUS_SOURCE.as_posix()}:'
                      f'{text.count(chr(10), 0, match.start()) + 1}',
            'show_type': show_type[-1] if show_type else 'SHOW_FUNC',
            'definition': body,
        })
    missing = sorted(
        name for name in METRICS if name.lower() not in definitions
    )
    if missing:
        raise RuntimeError(
            'MariaDB metric definitions are absent from the exact source: ' +
            ', '.join(missing)
        )
    return definitions


def collect_inventory(source_root, profile_path):
    profile = next(
        item for item in json.loads(profile_path.read_text(
            encoding='utf-8'
        ))['profiles'] if item['engine'] == 'mariadb'
    )
    definitions = _source_definitions(source_root)
    connection = mariadb.connect(
        host='127.0.0.1', port=profile['port'], user=profile['user'],
        password=profile['password'], database=profile['database'],
        connect_timeout=10,
    )
    try:
        cursor = connection.cursor()
        cursor.execute('SELECT VERSION()')
        version = str(cursor.fetchone()[0]).split('-', 1)[0]
        if version != REFERENCE_VERSION:
            raise RuntimeError(
                f'MariaDB runtime must be {REFERENCE_VERSION}, got {version}'
            )
        cursor.execute(
            'SELECT VARIABLE_NAME, VARIABLE_VALUE '
            'FROM INFORMATION_SCHEMA.GLOBAL_STATUS ORDER BY VARIABLE_NAME'
        )
        observations = []
        for name, value in cursor.fetchall():
            definition = definitions.get(str(name).lower())
            observations.append({
                'native_name': str(name),
                'value_type': _value_type(value),
                'registry_source': (
                    definition['source'] if definition else
                    'runtime_or_plugin_status_registry'
                ),
                'show_type': (
                    definition['show_type'] if definition else
                    'runtime_defined'
                ),
            })
        cursor.close()
    finally:
        connection.close()
    available = {item['native_name'].lower() for item in observations}
    missing = sorted(
        name for name in METRICS if name.lower() not in available
    )
    if missing:
        raise RuntimeError(
            'MariaDB exact runtime omitted admitted metrics: ' +
            ', '.join(missing)
        )
    return {
        'schema': 'cdeadmin.engine-metrics-inventory.v1',
        'inventory_id': 'mariadb.metrics-inventory.12.2.2.v1',
        'engine_id': 'mariadb',
        'reference_version': REFERENCE_VERSION,
        'collection_authority': (
            'MariaDB 12.2.2 INFORMATION_SCHEMA.GLOBAL_STATUS'
        ),
        'endpoint_selection': (
            'Every global status variable exposed by the exact reference '
            'runtime, including enabled plugin status variables.'
        ),
        'observations': observations,
        'completeness': {
            'runtime_version': version,
            'observation_count': len(observations),
            'global_status_catalog_exhausted': True,
            'admitted_metric_count': len(METRICS),
        },
    }


def build_contract(inventory, inventory_path, live_path, source_archive):
    live = json.loads(live_path.read_text(encoding='utf-8'))
    if (
            live.get('exact_profile') != REFERENCE_VERSION or
            live.get('graphical_object_activation_ready') is not True or
            live.get('server_stopped') is not True or
            live.get('credential_values_exported') is not False):
        raise RuntimeError('MariaDB exact live evidence is not admissible')
    inventory_digest = _sha256(inventory_path)
    live_digest = _sha256(live_path)
    source_digest = _sha256(source_archive)
    source_id = 'mariadb-12.2.2-source-status-registry'
    runtime_id = 'mariadb-12.2.2-runtime-global-status'
    live_id = 'mariadb-12.2.2-live-observation-gate'
    classification_evidence = [{
        'evidence_id': source_id,
        **_evidence(
            'MariaDB Foundation', source_archive.name, source_digest,
            'MariaDB 12.2.2 source archive', 'GPL-2.0-only',
        ),
    }, {
        'evidence_id': runtime_id,
        **_evidence(
            'MariaDB 12.2.2 runtime', inventory_path.name,
            inventory_digest, 'cdeadmin.engine-metrics-inventory.v1',
            'PostgreSQL',
        ),
    }, {
        'evidence_id': live_id,
        **_evidence(
            'MariaDB 12.2.2 runtime', live_path.name, live_digest,
            'cdeadmin.relational-provider-live-verification.v1',
            'PostgreSQL',
        ),
    }]
    observations = []
    metrics = []
    for item in inventory['observations']:
        native_name = item['native_name']
        observation_id = f'mariadb.global_status.{_slug(native_name)}'
        metric = METRICS_BY_LOWER.get(native_name.lower())
        observations.append({
            'observation_id': observation_id,
            'native_name': native_name,
            'scope': 'global_server',
            'source': (
                'SELECT VARIABLE_NAME, VARIABLE_VALUE FROM '
                'INFORMATION_SCHEMA.GLOBAL_STATUS'
            ),
            'value_type': item['value_type'],
            'observation_class': (
                'operational_metric' if metric else
                'native_status_observation'
            ),
            'description': (
                metric[3] if metric else
                f'MariaDB native global status observation {native_name}.'
            ),
            'privilege': 'authenticated_session',
            'version_condition': REFERENCE_VERSION,
            'collection_cost': 'single_global_status_catalog_scan',
            'poll_interval_seconds': 15 if metric else 60,
            'cardinality': 'one_per_server',
            'redaction': (
                'none' if item['value_type'] != 'text' else
                'provider_sensitive_value_policy'
            ),
            'evidence_ids': [source_id, runtime_id, live_id],
            'registry_source': item['registry_source'],
            'show_type': item['show_type'],
        })
        if metric:
            unit, kind, reset_behavior, _description = metric
            metrics.append({
                'metric_id': observation_id,
                'observation_id': observation_id,
                'unit': unit,
                'kind': kind,
                'reset_behavior': reset_behavior,
                'aggregation': (
                    'sum_across_servers' if kind == 'counter' else
                    'none'
                ),
                'evidence_ids': [source_id, runtime_id, live_id],
            })
    observation_ids = [item['observation_id'] for item in observations]
    metric_ids = [item['metric_id'] for item in metrics]
    return {
        'schema': 'cdeadmin.engine-metrics.v2',
        'contract_id': 'mariadb.metrics.12.2.2.v1',
        'provider_id': 'org.cdeadmin.mariadb',
        'profile_id': 'mariadb-native',
        'engine_id': 'mariadb',
        'interface_id': 'mariadb-native',
        'reference_version': REFERENCE_VERSION,
        'catalog_evidence': _evidence(
            'MariaDB 12.2.2 runtime', inventory_path.name,
            inventory_digest, 'cdeadmin.engine-metrics-inventory.v1',
            'PostgreSQL',
        ),
        'classification_evidence': classification_evidence,
        'native_observations': observations,
        'metrics': metrics,
        'authoritative_observation_count': len(observation_ids),
        'authoritative_observation_ids': observation_ids,
        'authoritative_metric_count': len(metric_ids),
        'authoritative_metric_ids': metric_ids,
        'live_evidence_ids': [live_id],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-root', type=Path, required=True)
    parser.add_argument('--source-archive', type=Path, required=True)
    parser.add_argument('--connection-profiles', type=Path, required=True)
    parser.add_argument('--live-evidence', type=Path, required=True)
    parser.add_argument('--inventory-output', type=Path, required=True)
    parser.add_argument('--contract-output', type=Path, required=True)
    options = parser.parse_args()
    inventory = collect_inventory(
        options.source_root.resolve(),
        options.connection_profiles.resolve(),
    )
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
        'observation_count': len(contract['native_observations']),
        'metric_count': len(contract['metrics']),
    }, indent=2, sort_keys=True))


if __name__ == '__main__':
    main()
