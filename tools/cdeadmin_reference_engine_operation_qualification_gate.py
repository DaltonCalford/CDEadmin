#!/usr/bin/env python3
##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Qualify every final provider-catalog operation against live evidence.

The older portfolio gate proves that each declared experience concept has
evidence.  It does not prove that later additions to a provider's final visual
administration catalog are present in that evidence.  This gate closes that
hole by comparing the final provider-owned catalog, operation by operation,
with exact-profile live evidence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.cdeadmin_provider_object_coverage_gate import (  # noqa: E402
    provider_catalogs,
)
from tools.cdeadmin_reference_engine_object_experience_gate import (  # noqa: E402
    evaluate as evaluate_portfolio_gate,
    expected_profiles,
)


SCHEMA = 'cdeadmin.reference-engine-operation-qualification-gate.v1'
LIVE_SCHEMA = 'cdeadmin.provider-object-live-evidence.v1'
LIVE_TOKEN = re.compile(
    r'^live:(?P<path>.+)#sha256=(?P<sha>[0-9a-fA-F]{64})#'
    r'(?P<family>[^/]+)/(?P<concept>[^/]+)$'
)


def _sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _operation_map(descriptor):
    operations = {}
    invalid_forms = []
    preserved_surface = (
        (descriptor.get('administration_surface') or {}).get('workflow') ==
        'legacy_preserved'
    )
    for resource in descriptor.get('objects', []):
        kind = resource.get('resource_kind')
        if not isinstance(kind, str) or not kind:
            continue
        for operation in resource.get('operations', []):
            if operation.get('native_supported') is False:
                continue
            operation_id = operation.get('operation_id')
            if not isinstance(operation_id, str) or not operation_id:
                continue
            operations.setdefault(kind, set()).add(operation_id)
            provider_form = (
                operation.get('graphical_ready') is True and
                operation.get('graphical_form_authority') in {
                    'engine-catalog', 'provider-adapter',
                }
            )
            if not isinstance(operation.get('form'), Mapping) or not (
                    provider_form or preserved_surface):
                invalid_forms.append(f'{kind}.{operation_id}')
    return operations, sorted(set(invalid_forms))


def _merge_operations(target, source):
    if not isinstance(source, Mapping):
        return
    for resource_kind, operation_ids in source.items():
        if not isinstance(operation_ids, list):
            continue
        target.setdefault(resource_kind, set()).update(
            item for item in operation_ids
            if isinstance(item, str) and item
        )


def _direct_live_operations(document):
    operations = {}
    _merge_operations(operations, document.get('passed_resource_operations'))
    if operations:
        return operations
    for family in (document.get('concepts') or {}).values():
        if not isinstance(family, Mapping):
            continue
        for concept in family.values():
            if isinstance(concept, Mapping) and concept.get(
                    'status') == 'passed':
                _merge_operations(operations, concept.get('operations'))
    return operations


def _coverage(document, engine_id):
    engines = document.get('engines')
    if isinstance(engines, Mapping):
        return engines.get(engine_id)
    return document.get('coverage')


def _validate_live_token(token, expected_engine, expected_profile):
    match = LIVE_TOKEN.match(token)
    if match is None:
        return None, None, 'invalid-live-evidence-token'
    path = Path(match.group('path'))
    if not path.is_file():
        return None, None, 'live-evidence-file-missing'
    digest = _sha256(path)
    if digest.lower() != match.group('sha').lower():
        return None, None, 'live-evidence-digest-mismatch'
    try:
        document = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None, None, 'live-evidence-document-invalid'
    if document.get('schema') != LIVE_SCHEMA:
        return None, None, 'live-evidence-schema-mismatch'
    if document.get('engine_id') != expected_engine:
        return None, None, 'live-evidence-engine-mismatch'
    if str(document.get('exact_profile')) != str(expected_profile):
        return None, None, 'live-evidence-profile-mismatch'
    if document.get('operation_failures'):
        return None, None, 'live-evidence-declares-operation-failures'
    if document.get('raw_commands_used_for_provider_operations') is True or (
            document.get('raw_commands_used') is True):
        return None, None, 'live-evidence-used-raw-command-fallback'
    return str(path), document, None


def _aggregate_live_operations(document, engine_id, profile_version):
    operations = {}
    failures = []
    paths = set()
    coverage = _coverage(document, engine_id)
    if not isinstance(coverage, Mapping):
        return operations, paths, ['engine-coverage-missing']
    for family in coverage.get('families', []):
        family_id = family.get('family_id')
        for concept in family.get('concepts', []):
            live = concept.get('live_operations')
            if not live:
                continue
            concept_id = concept.get('concept_id')
            valid_token = False
            for token in concept.get('evidence', []):
                if not isinstance(token, str) or not token.startswith('live:'):
                    continue
                path, leaf, failure = _validate_live_token(
                    token, engine_id, profile_version
                )
                if failure:
                    failures.append(
                        f'{family_id}.{concept_id}:{failure}'
                    )
                else:
                    valid_token = True
                    paths.add(path)
                    # A strict concept gate may intentionally project only a
                    # subset of a richer exact-runtime object artifact.  The
                    # leaf's passed_resource_operations remain authoritative
                    # for final-catalog qualification and prevent later
                    # provider additions from being hidden by that projection.
                    _merge_operations(
                        operations, leaf.get('passed_resource_operations')
                    )
            if not valid_token:
                failures.append(
                    f'{family_id}.{concept_id}:exact-live-evidence-missing'
                )
                continue
            _merge_operations(operations, live)
    return operations, paths, failures


def _live_operations(document, engine_id, profile_version, evidence_path):
    if document.get('schema') == LIVE_SCHEMA:
        failures = []
        if document.get('engine_id') != engine_id:
            failures.append('live-evidence-engine-mismatch')
        if str(document.get('exact_profile')) != str(profile_version):
            failures.append('live-evidence-profile-mismatch')
        if document.get('passed') is not True:
            failures.append('live-evidence-not-passed')
        if document.get('missing_resource_operations'):
            failures.append('live-evidence-declares-missing-operations')
        if document.get('operation_failures'):
            failures.append('live-evidence-declares-operation-failures')
        return (
            _direct_live_operations(document), {str(evidence_path)}, failures
        )
    return _aggregate_live_operations(
        document, engine_id, profile_version
    )


def _serialized(operation_map):
    return {
        kind: sorted(operation_ids)
        for kind, operation_ids in sorted(operation_map.items())
    }


def _difference(left, right):
    return {
        kind: sorted(operation_ids - right.get(kind, set()))
        for kind, operation_ids in sorted(left.items())
        if operation_ids - right.get(kind, set())
    }


def qualify(index_path, catalogs=None, profiles=None):
    """Return an exact-profile, operation-level qualification result."""
    index_path = Path(index_path).resolve()
    profiles = expected_profiles() if profiles is None else profiles
    catalogs = provider_catalogs() if catalogs is None else catalogs
    portfolio_result = evaluate_portfolio_gate(index_path, profiles)
    index = json.loads(index_path.read_text(encoding='utf-8'))
    supplied = index.get('profiles') or {}
    results = {}
    failures = list(portfolio_result.get('failures', []))
    catalog_operation_count = 0
    live_operation_count = 0
    missing_operation_count = 0
    evidence_only_operation_count = 0

    for profile_id in sorted(profiles):
        profile = profiles[profile_id]
        row_failures = []
        catalog = catalogs.get(profile_id)
        runtime_engine_id = profile['engine_id']
        if not isinstance(catalog, Mapping):
            row_failures.append('final-provider-catalog-missing')
            catalog_operations, invalid_forms = {}, []
        else:
            descriptor = catalog.get('descriptor') or {}
            runtime_engine_id = descriptor.get('engine_id')
            if not isinstance(runtime_engine_id, str) or not runtime_engine_id:
                row_failures.append('final-provider-engine-missing')
                runtime_engine_id = profile['engine_id']
            catalog_operations, invalid_forms = _operation_map(descriptor)
            if invalid_forms:
                row_failures.append('graphical-form-qualification-missing')

        specification = supplied.get(profile_id) or {}
        if isinstance(specification, str):
            specification = {'path': specification}
        evidence_path = Path(specification.get('path', ''))
        if not evidence_path.is_absolute():
            evidence_path = (index_path.parent / evidence_path).resolve()
        live_operations = {}
        exact_paths = set()
        if not evidence_path.is_file():
            row_failures.append('strict-evidence-file-missing')
        else:
            try:
                document = json.loads(evidence_path.read_text(
                    encoding='utf-8'
                ))
            except (OSError, ValueError):
                row_failures.append('strict-evidence-document-invalid')
            else:
                live_operations, exact_paths, evidence_failures = (
                    _live_operations(
                        document, runtime_engine_id,
                        profile['profile_version'], evidence_path,
                    )
                )
                row_failures.extend(evidence_failures)

        missing = _difference(catalog_operations, live_operations)
        evidence_only = _difference(live_operations, catalog_operations)
        if missing:
            row_failures.append('catalog-operations-lack-live-evidence')
        if evidence_only:
            row_failures.append('live-operations-absent-from-final-catalog')
        catalog_count = sum(map(len, catalog_operations.values()))
        live_count = sum(map(len, live_operations.values()))
        missing_count = sum(map(len, missing.values()))
        evidence_only_count = sum(map(len, evidence_only.values()))
        catalog_operation_count += catalog_count
        live_operation_count += live_count
        missing_operation_count += missing_count
        evidence_only_operation_count += evidence_only_count
        row_failures = sorted(set(row_failures))
        failures.extend(
            f'{profile_id}:{failure}' for failure in row_failures
        )
        results[profile_id] = {
            **profile,
            'catalog_operation_count': catalog_count,
            'live_operation_count': live_count,
            'missing_live_operation_count': missing_count,
            'evidence_only_operation_count': evidence_only_count,
            'catalog_operations': _serialized(catalog_operations),
            'live_operations': _serialized(live_operations),
            'missing_live_operations': missing,
            'evidence_only_operations': evidence_only,
            'invalid_graphical_forms': invalid_forms,
            'exact_evidence_paths': sorted(exact_paths),
            'passed': not row_failures,
            'failures': row_failures,
        }

    return {
        'schema': SCHEMA,
        'gate': 'all-final-provider-catalog-operations-exact-live',
        'scope': {
            'native_scratchbird': 'deferred',
            'profile_ids': sorted(profiles),
        },
        'profile_count': len(profiles),
        'passed_profile_count': sum(row['passed'] for row in results.values()),
        'failed_profile_count': sum(
            not row['passed'] for row in results.values()
        ),
        'catalog_operation_count': catalog_operation_count,
        'live_operation_count': live_operation_count,
        'missing_live_operation_count': missing_operation_count,
        'evidence_only_operation_count': evidence_only_operation_count,
        'complete': not failures,
        'failures': sorted(set(failures)),
        'profiles': results,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence-index', type=Path, required=True)
    parser.add_argument('--output', type=Path)
    options = parser.parse_args(argv)
    result = qualify(options.evidence_index)
    document = json.dumps(result, indent=2, sort_keys=True) + '\n'
    if options.output:
        options.output.parent.mkdir(parents=True, exist_ok=True)
        options.output.write_text(document, encoding='utf-8')
    else:
        sys.stdout.write(document)
    return 0 if result['complete'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
