#!/usr/bin/env python3
##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Generate Firebird 5.0.4 native observations and operational metrics.

The complete observation inventory comes from the live engine catalog. Metric
semantics and descriptions come only from admitted, digest-checked upstream
documentation and driver source. Unknown fields fail generation instead of
receiving an inferred classification. No credential or local path is emitted.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from pathlib import Path


CATALOG_SHA256 = (
    'b11c1aba700a13f67961511b42f2a93911f2a8ac4eea3e612a9d62bd8d37b742'
)
MONITORING_DOCUMENT_SHA256 = (
    'fd3697770b079d59b3727ef308ae15008142c205cdf9751c85bd20d64b1d8f2a'
)
DRIVER_SOURCE_SHA256 = (
    '3024a04558e70629e7dd7980cde953cc068be751ccbf016193bb0c19b6aa5c1b'
)
REFERENCE_VERSION = '5.0.4'


def _metric_semantics():
    """Return only quantitative fields explicitly described by authority."""
    result = {
        ('MON$DATABASE', 'MON$PAGE_SIZE'):
            ('bytes', 'gauge', 'none'),
        ('MON$DATABASE', 'MON$OLDEST_TRANSACTION'):
            ('transaction_number', 'gauge', 'none'),
        ('MON$DATABASE', 'MON$OLDEST_ACTIVE'):
            ('transaction_number', 'gauge', 'none'),
        ('MON$DATABASE', 'MON$OLDEST_SNAPSHOT'):
            ('transaction_number', 'gauge', 'none'),
        ('MON$DATABASE', 'MON$NEXT_TRANSACTION'):
            ('transaction_number', 'gauge', 'none'),
        ('MON$DATABASE', 'MON$PAGE_BUFFERS'):
            ('pages', 'gauge', 'none'),
        ('MON$DATABASE', 'MON$SWEEP_INTERVAL'):
            ('transactions', 'configuration_gauge', 'none'),
        ('MON$DATABASE', 'MON$PAGES'):
            ('pages', 'gauge', 'none'),
        ('MON$DATABASE', 'MON$CRYPT_PAGE'):
            ('page_number', 'progress_gauge', 'none'),
        ('MON$DATABASE', 'MON$NEXT_ATTACHMENT'):
            ('attachment_number', 'gauge', 'none'),
        ('MON$DATABASE', 'MON$NEXT_STATEMENT'):
            ('statement_number', 'gauge', 'none'),
        ('MON$ATTACHMENTS', 'MON$PARALLEL_WORKERS'):
            ('workers', 'configuration_gauge', 'none'),
    }
    for name in (
            'MON$PAGE_READS', 'MON$PAGE_WRITES', 'MON$PAGE_FETCHES',
            'MON$PAGE_MARKS'):
        result[('MON$IO_STATS', name)] = (
            'pages', 'counter', 'not_documented_by_authority'
        )
    for name in (
            'MON$RECORD_SEQ_READS', 'MON$RECORD_IDX_READS',
            'MON$RECORD_INSERTS', 'MON$RECORD_UPDATES',
            'MON$RECORD_DELETES', 'MON$RECORD_BACKOUTS',
            'MON$RECORD_PURGES', 'MON$RECORD_EXPUNGES',
            'MON$RECORD_LOCKS', 'MON$RECORD_WAITS',
            'MON$RECORD_CONFLICTS', 'MON$BACKVERSION_READS',
            'MON$FRAGMENT_READS', 'MON$RECORD_RPT_READS',
            'MON$RECORD_IMGC'):
        result[('MON$RECORD_STATS', name)] = (
            'records', 'counter', 'not_documented_by_authority'
        )
    for name in (
            'MON$MEMORY_USED', 'MON$MEMORY_ALLOCATED',
            'MON$MAX_MEMORY_USED', 'MON$MAX_MEMORY_ALLOCATED'):
        result[('MON$MEMORY_USAGE', name)] = ('bytes', 'gauge', 'none')
    result[('firebird.driver.ServerInfoProvider3', 'connection_count')] = (
        'connections', 'gauge', 'none'
    )
    return result


METRIC_SEMANTICS = _metric_semantics()

SERVICE_DESCRIPTIONS = {
    'architecture': 'Server implementation description.',
    'attached_databases': 'List of attached databases.',
    'capabilities': 'Server capabilities.',
    'connection_count': 'Number of database attachments.',
    'engine_version': 'Firebird version as major.minor number.',
    'home_directory': 'Server home directory.',
    'lock_directory': 'Directory with lock files.',
    'manager_version': 'Service manager version.',
    'message_directory': 'Directory with message files.',
    'security_database': 'Path to security database.',
    'version': 'Firebird version as a semantic-version string.',
}


def _arguments():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        '--database', default=(
            '127.0.0.1/53050:/var/lib/firebird/data/cdeadmin_demo.fdb'
        )
    )
    parser.add_argument('--service', default='127.0.0.1/53050:service_mgr')
    parser.add_argument('--user', default='SYSDBA')
    parser.add_argument(
        '--password-env', default='CDEADMIN_FIREBIRD_DEMO_PASSWORD'
    )
    parser.add_argument('--monitoring-document', type=Path, required=True)
    parser.add_argument('--driver-source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    return parser.parse_args()


def _observation_id(source, name):
    return 'firebird.' + '.'.join(
        item.lower().replace('$', '_').strip('_')
        for item in (source, name)
    )


def _sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _monitoring_descriptions(path):
    relation = None
    result = {}
    for line in path.read_text(encoding='utf-8').splitlines():
        match = re.match(r'\s+(MON\$[A-Z_]+) \(([^)]+)\)', line)
        if match:
            relation = match.group(1)
            continue
        match = re.match(r'\s+- (MON\$[A-Z_]+)\s+\((.+)\)\s*$', line)
        if match and relation:
            result[(relation, match.group(1))] = match.group(2)
    return result


def _monitoring_fields(connection):
    cursor = connection.cursor()
    try:
        cursor.execute(
            'SELECT TRIM(RF.RDB$RELATION_NAME), '
            'TRIM(RF.RDB$FIELD_NAME), F.RDB$FIELD_TYPE, '
            'COALESCE(F.RDB$FIELD_SUB_TYPE, 0), F.RDB$FIELD_LENGTH, '
            'F.RDB$FIELD_SCALE FROM RDB$RELATION_FIELDS RF '
            'JOIN RDB$FIELDS F ON F.RDB$FIELD_NAME=RF.RDB$FIELD_SOURCE '
            "WHERE RF.RDB$RELATION_NAME STARTING WITH 'MON$' "
            'ORDER BY RF.RDB$RELATION_NAME, RF.RDB$FIELD_POSITION'
        )
        return cursor.fetchall()
    finally:
        cursor.close()


def _service_properties(service):
    info = service.info
    return sorted(
        name for name in dir(type(info))
        if isinstance(getattr(type(info), name, None), property)
    )


def _monitor_observation(row, descriptions):
    relation, field, field_type, subtype, length, scale = row
    key = (relation, field)
    if key not in descriptions:
        raise RuntimeError(
            f'No exact-version description exists for {relation}.{field}'
        )
    return {
        'observation_id': _observation_id(relation, field),
        'native_name': field,
        'scope': relation,
        'source': relation,
        'value_type': (
            f'RDB$FIELD_TYPE={field_type};SUB_TYPE={subtype};'
            f'LENGTH={length};SCALE={scale}'
        ),
        'observation_class': (
            'operational_metric' if key in METRIC_SEMANTICS else
            'native_attribute'
        ),
        'description': descriptions[key],
        'privilege': 'Firebird monitoring visibility rules',
        'version_condition': REFERENCE_VERSION,
        'collection_cost': 'native_monitoring_relation_snapshot',
        'poll_interval_seconds': 15,
        'cardinality': f'one_per_visible_{relation.lower()}',
        'redaction': 'provider_authorized_value',
        'evidence_ids': ['firebird-5.0.4-monitoring-tables'],
    }


def _service_observation(name):
    if name not in SERVICE_DESCRIPTIONS:
        raise RuntimeError(
            f'No exact driver description exists for service property {name}'
        )
    source = 'firebird.driver.ServerInfoProvider3'
    key = (source, name)
    return {
        'observation_id': _observation_id('service_mgr.info', name),
        'native_name': name,
        'scope': 'server',
        'source': source,
        'value_type': 'driver_native_property',
        'observation_class': (
            'operational_metric' if key in METRIC_SEMANTICS else
            'native_attribute'
        ),
        'description': SERVICE_DESCRIPTIONS[name],
        'privilege': 'service-manager authentication',
        'version_condition': REFERENCE_VERSION,
        'collection_cost': 'service_manager_info_read',
        'poll_interval_seconds': 60,
        'cardinality': 'one_per_server',
        'redaction': 'provider_authorized_value',
        'evidence_ids': ['firebird-driver-1.10.11-server-info'],
    }


def _metric(observation):
    key = (observation['source'], observation['native_name'])
    unit, kind, reset = METRIC_SEMANTICS[key]
    return {
        'metric_id': observation['observation_id'],
        'observation_id': observation['observation_id'],
        'unit': unit,
        'kind': kind,
        'reset_behavior': reset,
        'aggregation': 'none',
        'evidence_ids': observation['evidence_ids'],
    }


def main():
    args = _arguments()
    password = os.environ.get(args.password_env)
    if not password:
        raise SystemExit(f'{args.password_env} must be set')
    if _sha256(args.monitoring_document) != MONITORING_DOCUMENT_SHA256:
        raise SystemExit('Monitoring documentation is not Firebird 5.0.4')
    if _sha256(args.driver_source) != DRIVER_SOURCE_SHA256:
        raise SystemExit('Driver source is not the admitted 1.10.11 artifact')
    descriptions = _monitoring_descriptions(args.monitoring_document)

    from firebird.driver import connect, connect_server

    connection = connect(
        args.database, user=args.user, password=password
    )
    service = connect_server(
        args.service, user=args.user, password=password
    )
    try:
        observations = [
            _monitor_observation(row, descriptions)
            for row in _monitoring_fields(connection)
        ]
        observations.extend(
            _service_observation(name)
            for name in _service_properties(service)
        )
    finally:
        service.close()
        connection.close()

    metrics = [
        _metric(item) for item in observations
        if item['observation_class'] == 'operational_metric'
    ]
    document = {
        'schema': 'cdeadmin.engine-metrics.v2',
        'contract_id': 'firebird.metrics.5.0.4.v2',
        'provider_id': 'org.cdeadmin.firebird',
        'profile_id': 'firebird-native',
        'engine_id': 'firebird',
        'interface_id': 'firebird-native-interface',
        'reference_version': REFERENCE_VERSION,
        'catalog_evidence': {
            'authority': 'upstream exact-version catalog source',
            'artifact': 'Firebird 5.0.4 src/jrd/relations.h',
            'sha256': CATALOG_SHA256,
            'format': 'Firebird relation definition macros',
            'license_id': 'IPL-1.0',
        },
        'classification_evidence': [{
            'evidence_id': 'firebird-5.0.4-monitoring-tables',
            'authority': 'upstream exact-version monitoring documentation',
            'artifact': 'Firebird 5.0.4 README.monitoring_tables',
            'sha256': MONITORING_DOCUMENT_SHA256,
            'format': 'Firebird upstream text documentation',
            'license_id': 'IPL-1.0',
        }, {
            'evidence_id': 'firebird-driver-1.10.11-server-info',
            'authority': 'admitted exact-version driver implementation',
            'artifact': 'firebird-driver 1.10.11 core.py',
            'sha256': DRIVER_SOURCE_SHA256,
            'format': 'Python source with public property documentation',
            'license_id': 'MIT',
        }],
        'native_observations': observations,
        'authoritative_observation_count': len(observations),
        'authoritative_observation_ids': [
            item['observation_id'] for item in observations
        ],
        'metrics': metrics,
        'authoritative_metric_count': len(metrics),
        'authoritative_metric_ids': [
            metric['metric_id'] for metric in metrics
        ],
        'live_evidence_ids': [
            'firebird-5.0.4-live-monitoring-catalog',
            'firebird-driver-1.10.11-live-service-info',
        ],
    }
    args.output.write_text(
        json.dumps(document, indent=2, ensure_ascii=False) + '\n',
        encoding='utf-8',
    )


if __name__ == '__main__':
    main()
