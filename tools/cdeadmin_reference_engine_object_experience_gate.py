#!/usr/bin/env python3
##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Fail closed across every reference-provider strict object gate."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PROVIDER_ROOT = ROOT / 'web/pgadmin/cdeadmin/providers'
BUILTINS = PROVIDER_ROOT / '__init__.py'


def _sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def expected_profiles():
    """Derive the complete in-tree profile set from the built-in registry."""
    import ast

    tree = ast.parse(BUILTINS.read_text(encoding='utf-8'))
    packages = None
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and
                target.id == 'BUILTIN_PACKAGES' for target in node.targets):
            packages = ast.literal_eval(node.value)
            break
    if packages is None:
        raise RuntimeError('BUILTIN_PACKAGES is unavailable')
    profiles = {}
    for relative_path, _module_name in packages:
        manifest = json.loads(
            (PROVIDER_ROOT / relative_path).read_text(encoding='utf-8')
        )
        identity = manifest['identity']
        profile_id = identity['profile_id']
        engine_id = manifest.get('composition', {}).get(
            'experience_families', [profile_id.split('-native')[0]]
        )[0]
        profiles[profile_id] = {
            'engine_id': engine_id,
            'provider_id': identity['provider_id'],
            'profile_version': identity['profile_version'],
        }
    return profiles


def _concept_failures(coverage):
    failures = []
    for family in coverage.get('families', []):
        for concept in family.get('concepts', []):
            prefix = f"{family.get('family_id')}.{concept.get('concept_id')}"
            if concept.get('missing_live_operations'):
                failures.append(f'{prefix}:missing-live-operations')
            live_required = concept.get('declared_status') in {
                'supported', 'read_only',
            }
            if live_required and concept.get('live_evidence') is not True:
                failures.append(f'{prefix}:live-evidence-missing')
    return failures


def _evaluate_gate(document, engine_id):
    schema = document.get('schema')
    failures = []
    if schema == 'cdeadmin.provider-object-live-evidence.v1':
        if document.get('engine_id') != engine_id:
            failures.append('engine-identity-mismatch')
        if document.get('passed') is not True:
            failures.append('provider-object-evidence-failed')
        if document.get('missing_resource_operations'):
            failures.append('missing-live-operations')
        if document.get('operation_failures'):
            failures.append('live-operation-failures')
        if document.get('raw_commands_used_for_provider_operations'):
            failures.append('raw-command-fallback-used')
        if document.get('automatic_mutation_retry') is True:
            failures.append('automatic-mutation-retry-admitted')
        if document.get('common_transaction_finality_interpreted') is True:
            failures.append('common-finality-interpretation-admitted')
        return failures

    if schema in {
            'cdeadmin.relational-object-experience-gate.v1',
            'cdeadmin.distributed-relational-object-gate.v1'}:
        if document.get('structural_complete') is not True:
            failures.append('structural-gate-failed')
        if document.get('live_complete') is not True:
            failures.append('live-gate-failed')
        if document.get('structural_failures'):
            failures.append('structural-failures-present')
        if document.get('live_failures'):
            failures.append('live-failures-present')
        engine = (document.get('engines') or {}).get(engine_id)
        if not isinstance(engine, dict):
            failures.append('engine-result-missing')
            return failures
        if engine.get('declaration_ready') is not True:
            failures.append('declaration-not-ready')
        if engine.get('activation_ready') is not True:
            failures.append('activation-not-ready')
        if engine.get('blocking_missing_count') != 0:
            failures.append('blocking-concepts-present')
        failures.extend(_concept_failures(engine))
        return failures

    coverage = document.get('coverage')
    if not isinstance(coverage, dict):
        return ['unsupported-evidence-schema']
    engine_token = engine_id.replace('_', '-').lower()
    gate_token = str(document.get('gate', '')).replace('_', '-').lower()
    if engine_token not in gate_token:
        failures.append('engine-gate-identity-mismatch')
    if document.get('structural_complete') is not True:
        failures.append('structural-gate-failed')
    if document.get('live_complete') is not True:
        failures.append('live-gate-failed')
    if coverage.get('declaration_ready') is not True:
        failures.append('declaration-not-ready')
    if coverage.get('activation_ready') is not True:
        failures.append('activation-not-ready')
    if coverage.get('blocking_missing_count') != 0:
        failures.append('blocking-concepts-present')
    failures.extend(_concept_failures(coverage))
    return failures


def evaluate(index_path, profiles=None):
    expected = expected_profiles() if profiles is None else profiles
    index = json.loads(index_path.read_text(encoding='utf-8'))
    supplied = index.get('profiles') or {}
    failures = []
    expected_ids = set(expected)
    supplied_ids = set(supplied)
    for profile_id in sorted(expected_ids - supplied_ids):
        failures.append(f'{profile_id}:evidence-missing')
    for profile_id in sorted(supplied_ids - expected_ids):
        failures.append(f'{profile_id}:unexpected-profile')
    results = {}
    for profile_id in sorted(expected_ids.intersection(supplied_ids)):
        specification = supplied[profile_id]
        if isinstance(specification, str):
            specification = {'path': specification}
        path = Path(specification.get('path', ''))
        if not path.is_absolute():
            path = (index_path.parent / path).resolve()
        row_failures = []
        digest = None
        if not path.is_file():
            row_failures.append('evidence-file-missing')
        else:
            digest = _sha256(path)
            expected_digest = specification.get('sha256')
            if expected_digest and expected_digest != digest:
                row_failures.append('evidence-digest-mismatch')
            try:
                document = json.loads(path.read_text(encoding='utf-8'))
            except (OSError, ValueError):
                row_failures.append('evidence-document-invalid')
            else:
                row_failures.extend(_evaluate_gate(
                    document, expected[profile_id]['engine_id']
                ))
                exact_profile = document.get('exact_profile')
                if exact_profile is not None and str(exact_profile) != str(
                        expected[profile_id]['profile_version']):
                    row_failures.append('exact-profile-mismatch')
        failures.extend(
            f'{profile_id}:{failure}' for failure in row_failures
        )
        results[profile_id] = {
            **expected[profile_id],
            'evidence_path': str(path),
            'evidence_sha256': digest,
            'passed': not row_failures,
            'failures': row_failures,
        }
    return {
        'schema': 'cdeadmin.reference-engine-object-experience-gate.v1',
        'gate': 'all-reference-provider-object-experience',
        'scope': {
            'native_scratchbird': 'deferred',
            'profile_ids': sorted(expected),
        },
        'profile_count': len(expected),
        'passed_profile_count': sum(
            row['passed'] for row in results.values()
        ),
        'failed_profile_count': sum(
            not row['passed'] for row in results.values()
        ) + len(expected_ids - supplied_ids),
        'complete': not failures,
        'failures': failures,
        'profiles': results,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence-index', type=Path, required=True)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args(argv)
    result = evaluate(args.evidence_index.resolve())
    document = json.dumps(result, indent=2, sort_keys=True) + '\n'
    print(document, end='')
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(document, encoding='utf-8')
    return 0 if result['complete'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
