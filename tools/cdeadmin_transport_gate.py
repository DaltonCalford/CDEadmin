#!/usr/bin/env python3
"""Validate CDEadmin protocol boundaries and embedded-runtime policy."""

from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path


REQUIRED_PROTOCOLS = frozenset({
    'postgresql_wire', 'mysql_wire', 'http_json', 'grpc',
    'arrow_flight', 'cql', 'resp', 'bolt', 'ignite_binary',
    'mongodb_wire', 'firebird_wire', 'foundationdb_api',
    'embedded_duckdb', 'embedded_sqlite',
})
REQUIRED_PROFILES = {
    'apache_ignite': '2.17.0',
    'cassandra': '5.0.8',
    'clickhouse': '25.12.10.7-stable',
    'cockroachdb': '26.1.3',
    'dolt': '1.86.6',
    'duckdb': '1.5.2',
    'firebird': '5.0.4',
    'foundationdb': '7.3.77',
    'immudb': '1.11.0',
    'influxdb': '3.9.0',
    'mariadb': '12.2.2',
    'milvus': '2.6.5',
    'mongodb': '8.2.6',
    'mysql': '9.7.0',
    'neo4j': '2026.04.0',
    'opensearch': '3.6.0',
    'opensearch_sql_ppl': '3.6.0-sql-ppl',
    'postgresql': '18.3',
    'redis': '8.6.2',
    'sqlite': '3.53.0',
    'tidb': '8.5.6',
    'tikv': '8.5.6',
    'vitess': '23.0.3',
    'xtdb': '2.1.0',
    'yugabytedb': '2025.2.2.2',
}
FORBIDDEN_METHODS = frozenset({
    'authorize', 'begin_transaction', 'commit', 'execute_query',
    'parse_query', 'rollback', 'savepoint',
})
FORBIDDEN_IMPORT_ROOTS = frozenset({
    'cassandra', 'duckdb', 'foundationdb', 'grpc', 'mysql', 'neo4j',
    'psycopg', 'pymongo', 'pyarrow', 'redis', 'requests',
})


def violation(rule, path, detail):
    return {'rule': rule, 'path': str(path), 'detail': detail}


def load_document(path):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError('selection document is unreadable') from exc


def selection_violations(path):
    try:
        document = load_document(path)
    except ValueError as exc:
        return [violation('selection-json', path, str(exc))], {}
    violations = []
    if document.get('schema') != 'cdeadmin.protocol-client-selections.v1':
        violations.append(violation(
            'selection-schema', path, 'unexpected schema identifier'
        ))
    rows = document.get('protocols')
    if not isinstance(rows, list):
        return [violation(
            'protocol-records', path, 'protocols must be an array'
        )], document
    protocols = {}
    for index, row in enumerate(rows):
        label = f'{path}:protocols[{index}]'
        if not isinstance(row, dict):
            violations.append(violation(
                'protocol-record', label, 'record must be an object'
            ))
            continue
        protocol_id = row.get('protocol_id')
        if protocol_id in protocols:
            violations.append(violation(
                'protocol-duplicate', label, str(protocol_id)
            ))
        protocols[protocol_id] = row
        client = row.get('client', {})
        state = client.get('state') if isinstance(client, dict) else None
        package = client.get('package') if isinstance(client, dict) else None
        versions = client.get('versions') if isinstance(client, dict) else None
        if state in {'selected_installed', 'selected_not_installed'}:
            if not package or not isinstance(versions, list) or not versions:
                violations.append(violation(
                    'selected-client-version', label,
                    'selected clients require package and exact versions',
                ))
        elif state == 'boundary_only_unselected':
            if package is not None or versions != []:
                violations.append(violation(
                    'unselected-client-version', label,
                    'unselected client must record null package and empty '
                    'versions',
                ))
        else:
            violations.append(violation(
                'client-state', label, 'client state is invalid'
            ))
        controls = row.get('security_controls')
        if not isinstance(controls, list) or not controls:
            violations.append(violation(
                'security-review', label, 'security controls are required'
            ))
    missing = sorted(REQUIRED_PROTOCOLS - set(protocols))
    extra = sorted(set(protocols) - REQUIRED_PROTOCOLS)
    if missing or extra:
        violations.append(violation(
            'protocol-inventory', path,
            f'missing={missing!r}, extra={extra!r}',
        ))
    profiles = document.get('engine_profiles')
    if not isinstance(profiles, list):
        violations.append(violation(
            'engine-profiles', path, 'engine_profiles must be an array'
        ))
        return violations, document
    observed = {}
    for index, row in enumerate(profiles):
        label = f'{path}:engine_profiles[{index}]'
        if not isinstance(row, dict):
            violations.append(violation(
                'engine-profile', label, 'record must be an object'
            ))
            continue
        engine_id = row.get('engine_id')
        if engine_id in observed:
            violations.append(violation(
                'engine-profile-duplicate', label, str(engine_id)
            ))
        observed[engine_id] = row.get('reference_profile')
        boundaries = row.get('actual_boundaries')
        if not isinstance(boundaries, list) or not boundaries:
            violations.append(violation(
                'engine-boundaries', label, 'actual boundaries are required'
            ))
        elif set(boundaries) - set(protocols):
            violations.append(violation(
                'engine-boundaries', label,
                'engine references an unknown protocol boundary',
            ))
        if 'scratchbird_emulation' in row:
            violations.append(violation(
                'implementation-neutral-profile', label,
                'engine profiles must not select by server implementation',
            ))
    if observed != REQUIRED_PROFILES:
        violations.append(violation(
            'exact-engine-profiles', path,
            'engine/profile inventory does not match the approved portfolio',
        ))
    authority = document.get('authority_boundary', {})
    engine_scope = authority.get('engine_scope', [])
    if 'transaction_finality' not in engine_scope or (
        'recovery' not in engine_scope
    ):
        violations.append(violation(
            'engine-authority', path,
            'finality and recovery must remain engine-owned',
        ))
    return violations, document


def source_violations(root):
    violations = []
    for path in sorted(root.glob('*.py')):
        try:
            tree = ast.parse(path.read_text(encoding='utf-8'), str(path))
        except (OSError, UnicodeDecodeError, SyntaxError) as exc:
            violations.append(violation(
                'transport-syntax', path, type(exc).__name__
            ))
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and (
                node.name in FORBIDDEN_METHODS
            ):
                violations.append(violation(
                    'semantic-authority-method', path,
                    f'forbidden common method {node.name!r}',
                ))
            if isinstance(node, ast.Import):
                names = [item.name for item in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or '']
            else:
                names = []
            for name in names:
                root_name = name.split('.', 1)[0]
                if root_name in FORBIDDEN_IMPORT_ROOTS or (
                    name.startswith('pgadmin.cdeadmin.providers')
                ):
                    violations.append(violation(
                        'semantic-client-import', path,
                        f'common transport imports {name!r}',
                    ))
    return violations


def evaluate(source):
    transport_root = source / 'web/pgadmin/cdeadmin/transports'
    selection_path = transport_root / 'protocol_client_selections.json'
    violations, document = selection_violations(selection_path)
    violations.extend(source_violations(transport_root))
    protocols = document.get('protocols', []) if document else []
    profiles = document.get('engine_profiles', []) if document else []
    return {
        'schema': 'cdeadmin.transport-gate-result.v1',
        'protocol_count': len(protocols),
        'engine_profile_count': len(profiles),
        'violation_count': len(violations),
        'violations': violations,
    }


def parse_args(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', default='.')
    parser.add_argument('--output')
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    result = evaluate(Path(args.source).resolve())
    encoded = json.dumps(result, indent=2, sort_keys=True) + '\n'
    if args.output:
        Path(args.output).write_text(encoded, encoding='utf-8')
    sys.stdout.write(encoded)
    return 1 if result['violations'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
