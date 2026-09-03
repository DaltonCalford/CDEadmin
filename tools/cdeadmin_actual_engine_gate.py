#!/usr/bin/env python3
##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Fail closed on actual-engine pilot activation or provenance drift."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
from pathlib import Path


EXPECTED = (
    ('postgresql', '18.3', 1), ('mysql', '9.7.0', 2),
    ('mariadb', '12.2.2', 2), ('mongodb', '8.2.6', 3),
    ('neo4j', '2026.04.0', 4), ('cassandra', '5.0.8', 5),
    ('redis', '8.6.2', 6), ('xtdb', '2.1.0', 7),
    ('clickhouse', '25.12.10.7-stable', 5),
    ('influxdb', '3.9.0', 6), ('milvus', '2.6.5', 7),
    ('opensearch', '3.6.0', 8),
    ('opensearch_sql_ppl', '3.6.0-sql-ppl', 9),
    ('duckdb', '1.5.2', 6),
    ('firebird', '5.0.4', 7), ('sqlite', '3.53.0', 8),
)
CATEGORIES = {
    'resource', 'language_api', 'result', 'transaction',
    'admin', 'security', 'fault',
}
FORBIDDEN_FOUNDATION_NAMES = {
    'postgresql', 'mysql', 'mariadb', 'mongodb',
    'neo4j', 'cassandra', 'redis', 'clickhouse', 'duckdb', 'firebird',
    'sqlite', 'xtdb', 'influxdb', 'milvus', 'opensearch',
    'opensearch_sql_ppl',
}
FORBIDDEN_AUTHORITY_METHODS = {
    'begin', 'commit', 'rollback', 'savepoint', 'retry', 'recover',
}
ACTIVE_SUPPORT_STATES = {
    'implemented', 'compatibility_mapped', 'connector_managed',
    'experimental',
}


def _is_live_qualified(row):
    return (
        row.get('live_suite') == 'passed' and
        row.get('runtime_state') == 'verified' and
        bool(row.get('live_evidence_reference'))
    )


def _load(path):
    return json.loads(path.read_text(encoding='utf-8'))


def _sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def evaluate(source: Path, donor_root: Path | None = None):
    provider_root = source / 'web/pgadmin/cdeadmin/providers'
    record = _load(provider_root / 'actual_engine_pilots.json')
    selections = _load(
        source / 'web/pgadmin/cdeadmin/transports/'
        'protocol_client_selections.json'
    )
    errors = []
    pilots = record.get('pilots', [])
    observed = tuple(
        (row.get('engine_id'), row.get('exact_profile'), row.get('order'))
        for row in pilots
    )
    if observed != EXPECTED:
        errors.append(
            'pilot order or reference profiles differ from CDE-PREP-150'
        )
    if set(record.get('suite_categories', [])) != CATEGORIES:
        errors.append('provider qualification category set is incomplete')

    selected_profiles = {
        row['engine_id']: row for row in selections['engine_profiles']
    }
    for index, row in enumerate(pilots):
        prefix = f"pilot {row.get('engine_id', index)!r}"
        manifest_path = provider_root / row.get('manifest', '')
        if not manifest_path.is_file():
            errors.append(f'{prefix}: manifest is missing')
            continue
        manifest = _load(manifest_path)
        identity = manifest.get('identity', {})
        for field, expected in (
            ('provider_id', row.get('provider_id')),
            ('profile_id', row.get('profile_id')),
            ('profile_version', row.get('exact_profile')),
        ):
            if identity.get(field) != expected:
                errors.append(f'{prefix}: manifest {field} mismatch')
        qualified = _is_live_qualified(row)
        if index and qualified and (
            not manifest.get('enabled') or
            not manifest.get('production_registration') or
            manifest.get('support_state') not in ACTIVE_SUPPORT_STATES
        ):
            errors.append(f'{prefix}: qualified provider is not activatable')
        if qualified and manifest.get('provenance', {}).get(
            'live_evidence_reference'
        ) != row.get('live_evidence_reference'):
            errors.append(
                f'{prefix}: manifest live evidence does not match pilot'
            )
        if index and not qualified and (
            manifest.get('enabled') or
            manifest.get('production_registration') or
            manifest.get('support_state') != 'deferred'
        ):
            errors.append(f'{prefix}: unqualified provider is activatable')
        if row.get('live_suite') == 'passed' and not qualified:
            errors.append(f'{prefix}: live pass has no verified evidence')
        selected = selected_profiles.get(row.get('engine_id'))
        if selected is None or row.get('protocol_id') not in (
            selected.get('actual_boundaries') or []
        ):
            errors.append(f'{prefix}: advertised protocol is not selected')
        digest = row.get('source_manifest_sha256', '')
        if len(digest) != 64:
            errors.append(f'{prefix}: source manifest digest is invalid')
        if donor_root is not None:
            evidence = (
                donor_root / row.get('source_packet', '') /
                'RELEASE_EVIDENCE_MANIFEST.yaml'
            )
            if not evidence.is_file():
                errors.append(f'{prefix}: donor evidence manifest is missing')
            elif _sha256(evidence) != digest:
                errors.append(f'{prefix}: donor evidence manifest changed')

    builtin_source = (
        provider_root / '__init__.py'
    ).read_text(encoding='utf-8').lower()
    qualified_engines = {'postgresql'} | {
        row['engine_id'] for row in pilots if _is_live_qualified(row)
    }
    for engine in FORBIDDEN_FOUNDATION_NAMES - qualified_engines:
        if engine in builtin_source:
            errors.append(f'unqualified {engine} provider entered built-ins')

    foundation_path = source / 'web/pgadmin/cdeadmin/sdk/actual_engine.py'
    foundation_source = foundation_path.read_text(encoding='utf-8')
    tree = ast.parse(foundation_source, filename=str(foundation_path))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and (
            node.name.lower() in FORBIDDEN_AUTHORITY_METHODS
        ):
            errors.append(
                f'provider foundation owns forbidden authority method '
                f'{node.name!r}'
            )
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            lowered = node.value.lower()
            for engine in FORBIDDEN_FOUNDATION_NAMES:
                if engine in lowered:
                    errors.append(
                        f'provider foundation contains engine branch token '
                        f'{engine!r}'
                    )

    core_sources = list((source / 'web/pgadmin/cdeadmin/core').glob('*.py'))
    for path in core_sources:
        text = path.read_text(encoding='utf-8').lower()
        for engine in FORBIDDEN_FOUNDATION_NAMES - {'postgresql'}:
            if f'providers.{engine}' in text:
                errors.append(
                    f'common core imports {engine} provider in {path.name}'
                )

    return {
        'schema': 'cdeadmin.actual-engine-gate-result.v1',
        'pilot_profiles': len(pilots),
        'suite_categories': len(CATEGORIES),
        'donor_manifests_verified': len(pilots) if donor_root else 0,
        'valid': not errors,
        'errors': errors,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=Path('.'))
    parser.add_argument('--donor-root', type=Path)
    args = parser.parse_args()
    result = evaluate(args.source.resolve(), args.donor_root)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result['valid'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
