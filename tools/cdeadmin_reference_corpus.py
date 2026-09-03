#!/usr/bin/env python3
##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Validate and plan use of the read-only exact reference corpus."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path


EXPECTED = (
    ('apache_ignite', '2.17.0', 'distributed'),
    ('cassandra', '5.0.8', 'nosql'),
    ('clickhouse', '25.12.10.7-stable', 'analytic'),
    ('cockroachdb', '26.1.3', 'distributed'),
    ('dolt', '1.86.6', 'distributed'),
    ('duckdb', '1.5.2', 'relational'),
    ('firebird', '5.0.4', 'relational'),
    ('foundationdb', '7.3.77', 'distributed'),
    ('immudb', '1.11.0', 'distributed'),
    ('influxdb', '3.9.0', 'analytic'),
    ('mariadb', '12.2.2', 'relational'),
    ('milvus', '2.6.5', 'analytic'),
    ('mongodb', '8.2.6', 'nosql'),
    ('mysql', '9.7.0', 'relational'),
    ('neo4j', '2026.04.0', 'nosql'),
    ('opensearch', '3.6.0', 'analytic'),
    ('opensearch_sql_ppl', '3.6.0-sql-ppl', 'analytic'),
    ('postgresql', '18.3', 'relational'),
    ('redis', '8.6.2', 'nosql'),
    ('sqlite', '3.53.0', 'relational'),
    ('tidb', '8.5.6', 'distributed'),
    ('tikv', '8.5.6', 'distributed'),
    ('vitess', '23.0.3', 'distributed'),
    ('xtdb', '2.1.0', 'nosql'),
    ('yugabytedb', '2025.2.2.2', 'distributed'),
)
FIXTURE_SETS = {
    'relational_core', 'document_wide_column', 'graph', 'vector',
    'timeseries', 'search', 'key_value', 'distributed_consistency',
    'versioned_data', 'analytic_columnar',
}
REQUIRED_GATES = {
    'license_policy_review', 'profile_admission_review',
}
SHA256 = re.compile(r'^[0-9a-f]{64}$')


class CorpusError(RuntimeError):
    """Raised when a reference-corpus invariant is violated."""


def _load(path):
    return json.loads(path.read_text(encoding='utf-8'))


def _sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_relative(value):
    if not isinstance(value, str) or not value:
        return False
    path = Path(value)
    return not path.is_absolute() and '..' not in path.parts


def _yaml_scalar(text, key):
    match = re.search(
        rf'^{re.escape(key)}:\s*([^\n#]+?)\s*$', text, re.MULTILINE
    )
    if not match:
        return None
    return match.group(1).strip('"\'')


def _yaml_list(text, key):
    start = re.search(rf'^{re.escape(key)}:\s*$', text, re.MULTILINE)
    if not start:
        return []
    values = []
    for line in text[start.end():].splitlines()[1:]:
        match = re.match(r'^\s{2}-\s+(.+?)\s*$', line)
        if match:
            values.append(match.group(1).strip('"\''))
            continue
        if line and not line.startswith(' '):
            break
    return values


def _fixture_errors(source, record):
    errors = []
    relative = record['runtime_defaults'].get('fixture_manifest', '')
    if not _safe_relative(relative):
        return ['fixture manifest path is not repository relative']
    manifest_path = source / relative
    if not manifest_path.is_file():
        return ['fixture manifest is missing']
    manifest = _load(manifest_path)
    if manifest.get('classification') != 'public_synthetic_non_sensitive':
        errors.append('fixture classification is not safe for redistribution')
    observed = set()
    for item in manifest.get('files', []):
        path_value = item.get('path', '')
        if not _safe_relative(path_value):
            errors.append('fixture data path is not safely relative')
            continue
        path = manifest_path.parent / path_value
        if not path.is_file():
            errors.append(f'fixture data file {path_value!r} is missing')
            continue
        if _sha256(path) != item.get('sha256'):
            errors.append(f'fixture data file {path_value!r} changed')
        observed.update(item.get('fixture_sets', []))
        payload = _load(path)
        for marker in (
            'contains_credentials', 'contains_personal_data',
            'contains_proprietary_data',
        ):
            if payload.get(marker) is not False:
                errors.append(f'fixture data does not deny {marker!r}')
    if observed != FIXTURE_SETS:
        errors.append('fixture set inventory is incomplete or unexpected')
    return errors


def _verify_packet(row, donor_root):
    errors = []
    packet = donor_root / row['source_packet']
    manifest = packet / 'RELEASE_EVIDENCE_MANIFEST.yaml'
    tree = packet / 'TREE_MANIFEST.sha256'
    source_archive = packet / row['source_archive']
    license_path = packet / row['license_artifact']
    prefix = f"{row['engine_id']} {row['reference_profile']}"
    for label, path in (
        ('packet', packet), ('evidence manifest', manifest),
        ('tree manifest', tree), ('source archive', source_archive),
        ('license artifact', license_path),
    ):
        if not path.exists():
            errors.append(f'{prefix}: {label} is missing')
    if errors:
        return errors
    if _sha256(manifest) != row['packet_manifest_sha256']:
        errors.append(f'{prefix}: evidence manifest digest changed')
    if _sha256(tree) != row['tree_manifest_sha256']:
        errors.append(f'{prefix}: tree manifest digest changed')
    if _sha256(source_archive) != row['source_archive_sha256']:
        errors.append(f'{prefix}: source archive digest changed')
    expected_license = row.get('license_artifact_sha256')
    if expected_license and (
        not license_path.is_file() or _sha256(license_path) != expected_license
    ):
        errors.append(f'{prefix}: license artifact digest changed')

    text = manifest.read_text(encoding='utf-8')
    for key, expected in (
        ('candidate_version_label', row['source_candidate_version']),
        ('proof_status', row['proof_status']),
        ('structural_audit_status', 'passed'),
    ):
        if _yaml_scalar(text, key) != expected:
            errors.append(f'{prefix}: donor {key} does not match inventory')
    donor_gates = _yaml_list(text, 'release_proven_remaining_gates')
    if not donor_gates:
        donor_gates = _yaml_list(text, 'remaining_gates')
    if donor_gates != row['remaining_gates']:
        errors.append(f'{prefix}: donor remaining-gate set changed')
    return errors


def evaluate(source: Path, donor_root: Path | None = None):
    """Return structural, admission, and optional donor verification state."""
    record_path = source / 'tools/cdeadmin_reference_corpus.json'
    errors = []
    if not record_path.is_file():
        return {
            'schema': 'cdeadmin.reference-corpus-result.v1',
            'valid': False,
            'errors': ['reference corpus inventory is missing'],
        }
    record = _load(record_path)
    if record.get('schema') != 'cdeadmin.reference-corpus.v1':
        errors.append('reference corpus schema is not v1')
    if record.get('target_mode') != 'legacy_native':
        errors.append('reference corpus may only target legacy_native')
    if record.get('scratchbird_emulation_state') != 'not_run':
        errors.append('reference work changed ScratchBird emulation status')
    policy = record.get('policy', {})
    if policy.get('frozen_clone_substitution') != 'forbidden':
        errors.append('frozen clone substitution is not forbidden')
    if policy.get('active_corpus_authority') != (
        'exact_release_packet_root_supplied_by_operator'
    ):
        errors.append('active exact-release corpus authority is not explicit')
    if policy.get('deprecated_clone_catalog_disposition') != (
        'deprecated_non_authoritative_not_used'
    ):
        errors.append('deprecated clone catalog is treated as authoritative')
    if policy.get('source_packet_mount') != 'read_only':
        errors.append('source packet mount is not read-only')
    if policy.get('redistribution_default') != 'not_approved':
        errors.append('redistribution does not fail closed')
    if policy.get('unapproved_or_nonredistributable_access') != (
        'authorized_reviewers_only'
    ):
        errors.append('restricted donor inputs are not access-controlled')

    profiles = record.get('profiles', [])
    observed = tuple(
        (row.get('engine_id'), row.get('reference_profile'),
         row.get('family')) for row in profiles
    )
    if observed != EXPECTED:
        errors.append('the exact 25-profile inventory or ordering changed')
    ids = [row.get('engine_id') for row in profiles]
    if len(ids) != len(set(ids)):
        errors.append('reference corpus contains duplicate engine IDs')

    defaults = record.get('runtime_defaults', {})
    if defaults.get('definition_state') != (
        'blocked_pending_approval_and_runtime_pins'
    ):
        errors.append('runtime defaults do not fail closed')
    if defaults.get('base_image_digest') is not None or (
        defaults.get('runtime_artifact_digest') is not None
    ):
        errors.append('unapproved runtime digest entered shared defaults')
    if defaults.get('source_mount_mode') != 'ro' or not defaults.get(
        'rootfs_read_only'
    ):
        errors.append('runtime immutability defaults are incomplete')
    if defaults.get('live_suite') != 'not_run':
        errors.append('unqualified live suite is not marked not_run')

    for row in profiles:
        prefix = f"{row.get('engine_id', '<unknown>')} profile"
        for field in (
            'source_packet', 'source_archive', 'license_artifact',
        ):
            if not _safe_relative(row.get(field)):
                errors.append(f'{prefix}: {field} is not safely relative')
        for field in (
            'packet_manifest_sha256', 'tree_manifest_sha256',
            'source_archive_sha256',
        ):
            if not SHA256.fullmatch(row.get(field, '')):
                errors.append(f'{prefix}: {field} is not an exact digest')
        license_digest = row.get('license_artifact_sha256')
        if license_digest is not None and not SHA256.fullmatch(
            license_digest
        ):
            errors.append(f'{prefix}: license artifact digest is invalid')
        if not REQUIRED_GATES.issubset(set(row.get('remaining_gates', []))):
            errors.append(f'{prefix}: mandatory admission gates are absent')
        runtime = row.get('runtime', {})
        if not runtime.get('recipe') or not runtime.get('topology'):
            errors.append(f'{prefix}: runtime recipe is incomplete')
        if not runtime.get('version_probe'):
            errors.append(f'{prefix}: exact-version probe is missing')
        fixtures = set(row.get('fixture_sets', []))
        if not fixtures or not fixtures.issubset(FIXTURE_SETS):
            errors.append(f'{prefix}: fixture selection is invalid')
        if donor_root is not None:
            errors.extend(_verify_packet(row, donor_root))

    errors.extend(_fixture_errors(source, record))
    blocked = sum(bool(row.get('remaining_gates')) for row in profiles)
    result = {
        'schema': 'cdeadmin.reference-corpus-result.v1',
        'profile_count': len(profiles),
        'family_counts': {
            family: sum(row.get('family') == family for row in profiles)
            for family in ('relational', 'nosql', 'analytic', 'distributed')
        },
        'donor_packets_verified': len(profiles) if donor_root else 0,
        'runnable_profiles': len(profiles) - blocked,
        'blocked_profiles': blocked,
        'source_provenance_state': 'structurally_complete_not_approved',
        'runtime_state': 'definitions_only_not_provisioned',
        'redistribution_state': 'not_approved',
        'scratchbird_emulation_state': record.get(
            'scratchbird_emulation_state'
        ),
        'valid': not errors,
        'errors': errors,
    }
    return result


def build_run_plan(source, donor_root, engine_id, work_root):
    """Build a non-executing, fail-closed plan for an exact profile."""
    source = source.resolve()
    donor_root = donor_root.resolve()
    work_root = work_root.resolve()
    if work_root == donor_root or donor_root in work_root.parents:
        raise CorpusError('writable work root may not be inside donor corpus')
    if work_root in donor_root.parents:
        raise CorpusError('donor corpus may not be inside writable work root')
    record = _load(source / 'tools/cdeadmin_reference_corpus.json')
    row = next(
        (item for item in record['profiles']
         if item['engine_id'] == engine_id), None
    )
    if row is None:
        raise CorpusError(f'unknown exact reference profile {engine_id!r}')
    packet_errors = _verify_packet(row, donor_root)
    if packet_errors:
        raise CorpusError('; '.join(packet_errors))
    defaults = record['runtime_defaults']
    blockers = list(row['remaining_gates'])
    if defaults['base_image_digest'] is None:
        blockers.append('base_image_digest_pin')
    if defaults['runtime_artifact_digest'] is None:
        blockers.append('runtime_artifact_digest_pin')
    return {
        'schema': 'cdeadmin.reference-run-plan.v1',
        'engine_id': engine_id,
        'reference_profile': row['reference_profile'],
        'target_mode': 'legacy_native',
        'runnable': not blockers,
        'blockers': blockers,
        'source': {
            'packet': str(donor_root / row['source_packet']),
            'archive': row['source_archive'],
            'sha256': row['source_archive_sha256'],
            'mount_mode': 'ro',
        },
        'workspace': {
            'root': str(work_root),
            'source_extraction': 'ephemeral_work',
            'data': 'ephemeral_data',
            'evidence': 'content_addressed_evidence',
        },
        'container': {
            'rootfs_read_only': True,
            'network': 'isolated_by_default',
            'base_image_digest': defaults['base_image_digest'],
            'runtime_artifact_digest': defaults['runtime_artifact_digest'],
        },
        'runtime': row['runtime'],
        'fixtures': {
            'manifest': defaults['fixture_manifest'],
            'mount_mode': 'ro',
            'selected_sets': row['fixture_sets'],
        },
        'live_suite': 'not_run',
        'scratchbird_emulation_state': 'not_run',
    }


def main():  # pragma: no cover - thin CLI wrapper
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=Path('.'))
    parser.add_argument('--donor-root', type=Path)
    parser.add_argument('--profile')
    parser.add_argument('--work-root', type=Path)
    parser.add_argument('--require-runnable', action='store_true')
    args = parser.parse_args()
    source = args.source.resolve()
    if bool(args.profile) != bool(args.work_root):
        parser.error('--profile and --work-root must be supplied together')
    if args.profile and args.donor_root is None:
        parser.error('--profile requires --donor-root')
    if args.profile:
        try:
            result = build_run_plan(
                source, args.donor_root, args.profile, args.work_root
            )
        except CorpusError as exc:
            result = {
                'schema': 'cdeadmin.reference-run-plan.v1',
                'runnable': False, 'errors': [str(exc)],
            }
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if result.get('runnable') or not args.require_runnable else 1
    result = evaluate(source, args.donor_root)
    print(json.dumps(result, indent=2, sort_keys=True))
    if not result.get('valid'):
        return 1
    if args.require_runnable and result.get('runnable_profiles') != 25:
        return 1
    return 0


if __name__ == '__main__':  # pragma: no cover
    raise SystemExit(main())
