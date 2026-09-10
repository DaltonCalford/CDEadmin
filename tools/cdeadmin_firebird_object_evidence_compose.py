#!/usr/bin/env python3
##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Compose Firebird SQL-editor and service-manager live evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


PROFILE = '5.0.4'
FAULT_POSTCONDITION = 'fixup_database_normal_state'


def _sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _passed_operations(document, *, fault=False):
    if document.get('reference_profile') != PROFILE or not document.get(
            'passed'):
        raise ValueError(
            'Firebird service evidence did not pass profile 5.0.4'
        )
    results = document.get('results')
    if not isinstance(results, list) or not results or not all(
            isinstance(item, dict) and item.get('passed') is True
            for item in results):
        raise ValueError('Firebird service evidence has incomplete results')
    operations = {
        str(item.get('operation')) for item in results
        if item.get('operation')
    }
    if fault:
        operations.discard(FAULT_POSTCONDITION)
    return operations


def compose_documents(base, services, faults, artifacts):
    if base.get('engine_id') != 'firebird' or base.get(
            'exact_profile') != PROFILE:
        raise ValueError('base evidence is not for Firebird 5.0.4')
    if base.get('operation_failures') or base.get('raw_commands_used'):
        raise ValueError('base Firebird object evidence did not pass')
    operations = set(base.get('passed_resource_operations', {}).get(
        'database', []
    ))
    operations.update(_passed_operations(services))
    operations.update(_passed_operations(faults, fault=True))
    required = {
        'inspect', 'create', 'alter', 'drop',
        'backup_logical', 'restore_logical', 'backup_physical',
        'restore_physical', 'validate_database', 'repair_database',
        'sweep_database', 'database_statistics', 'shutdown_database',
        'bring_online', 'set_page_cache_size', 'set_sweep_interval',
        'set_space_reservation', 'set_write_mode', 'set_access_mode',
        'set_sql_dialect', 'activate_shadow', 'remove_linger',
        'fixup_database', 'set_replica_mode', 'upgrade_database',
    }
    missing = required.difference(operations)
    if missing:
        raise ValueError(
            'Firebird database operation evidence is incomplete: ' +
            ', '.join(sorted(missing))
        )
    result = json.loads(json.dumps(base))
    result['evidence_scope'] = (
        'inspection-visual-editor-and-service-manager-operations'
    )
    result.setdefault('passed_resource_operations', {})['database'] = sorted(
        operations
    )
    concepts = result.setdefault('concepts', {}).setdefault(
        'relational', {}
    )
    databases = concepts.setdefault('databases', {
        'status': 'passed', 'operations': {},
    })
    databases.setdefault('operations', {})['database'] = sorted(operations)
    result['supplemental_live_evidence'] = artifacts
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', type=Path, required=True)
    parser.add_argument('--services', type=Path, required=True)
    parser.add_argument('--faults', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    options = parser.parse_args(argv)
    documents = [
        json.loads(path.read_text(encoding='utf-8'))
        for path in (options.base, options.services, options.faults)
    ]
    artifacts = [
        {'path': str(path), 'sha256': _sha256(path)}
        for path in (options.services, options.faults)
    ]
    result = compose_documents(*documents, artifacts)
    options.output.parent.mkdir(parents=True, exist_ok=True)
    options.output.write_text(
        json.dumps(result, indent=2, sort_keys=True) + '\n',
        encoding='utf-8',
    )
    print(json.dumps({
        'engine_id': 'firebird', 'exact_profile': PROFILE,
        'database_operation_count': len(
            result['passed_resource_operations']['database']
        ),
        'output': str(options.output),
    }, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
