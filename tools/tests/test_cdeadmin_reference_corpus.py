##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Exact reference-corpus provisioning tests for CDE-PREP-160."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.cdeadmin_reference_corpus import (
    CorpusError,
    EXPECTED,
    _verify_packet,
    build_run_plan,
    evaluate,
)


ROOT = Path(__file__).resolve().parents[2]
INVENTORY = ROOT / 'tools/cdeadmin_reference_corpus.json'
FIXTURE_ROOT = ROOT / 'tools/tests/fixtures/cdeadmin_reference_corpus'


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ReferenceCorpusPolicyTests(unittest.TestCase):

    def make_source_copy(self, root):
        tools = root / 'tools'
        fixtures = tools / 'tests/fixtures/cdeadmin_reference_corpus'
        fixtures.parent.mkdir(parents=True)
        shutil.copytree(FIXTURE_ROOT, fixtures)
        shutil.copy2(INVENTORY, tools / INVENTORY.name)
        return tools / INVENTORY.name, fixtures

    def test_repository_gate_covers_all_exact_profiles(self):
        result = evaluate(ROOT)
        self.assertTrue(result['valid'], result['errors'])
        self.assertEqual(25, result['profile_count'])
        self.assertEqual(
            {'analytic': 5, 'distributed': 9,
             'nosql': 5, 'relational': 6},
            result['family_counts'],
        )
        self.assertEqual(0, result['donor_packets_verified'])

    def test_inventory_order_is_the_requested_exact_release_matrix(self):
        record = json.loads(INVENTORY.read_text(encoding='utf-8'))
        observed = tuple(
            (row['engine_id'], row['reference_profile'], row['family'])
            for row in record['profiles']
        )
        self.assertEqual(EXPECTED, observed)
        self.assertEqual(25, len({row[0] for row in observed}))

    def test_every_profile_is_blocked_until_real_approval_and_pins(self):
        record = json.loads(INVENTORY.read_text(encoding='utf-8'))
        defaults = record['runtime_defaults']
        self.assertIsNone(defaults['base_image_digest'])
        self.assertIsNone(defaults['runtime_artifact_digest'])
        self.assertEqual('not_run', defaults['live_suite'])
        for row in record['profiles']:
            self.assertIn('license_policy_review', row['remaining_gates'])
            self.assertIn('profile_admission_review', row['remaining_gates'])
        result = evaluate(ROOT)
        self.assertEqual(25, result['blocked_profiles'])
        self.assertEqual(0, result['runnable_profiles'])
        self.assertEqual('not_run', result['scratchbird_emulation_state'])

    def test_read_only_and_restricted_access_controls_fail_closed(self):
        record = json.loads(INVENTORY.read_text(encoding='utf-8'))
        policy = record['policy']
        defaults = record['runtime_defaults']
        self.assertEqual('forbidden', policy['frozen_clone_substitution'])
        self.assertEqual('read_only', policy['source_packet_mount'])
        self.assertEqual('not_approved', policy['redistribution_default'])
        self.assertEqual(
            'authorized_reviewers_only',
            policy['unapproved_or_nonredistributable_access'],
        )
        self.assertEqual('ro', defaults['source_mount_mode'])
        self.assertTrue(defaults['rootfs_read_only'])

    def test_sanitized_seed_is_content_addressed_and_model_complete(self):
        manifest = json.loads(
            (FIXTURE_ROOT / 'seed_manifest.json').read_text(encoding='utf-8')
        )
        item = manifest['files'][0]
        seed_path = FIXTURE_ROOT / item['path']
        seed = json.loads(seed_path.read_text(encoding='utf-8'))
        self.assertEqual(item['sha256'], sha256(seed_path))
        self.assertFalse(seed['contains_credentials'])
        self.assertFalse(seed['contains_personal_data'])
        self.assertFalse(seed['contains_proprietary_data'])
        self.assertEqual(set(item['fixture_sets']), set(seed['fixture_sets']))

    def test_deprecated_clone_catalog_is_non_authoritative(self):
        record = json.loads(INVENTORY.read_text(encoding='utf-8'))
        policy = record['policy']
        self.assertEqual(
            'exact_release_packet_root_supplied_by_operator',
            policy['active_corpus_authority'],
        )
        self.assertEqual(
            'deprecated_non_authoritative_not_used',
            policy['deprecated_clone_catalog_disposition'],
        )
        serialized = INVENTORY.read_text(encoding='utf-8')
        self.assertNotIn('local_existing', serialized)
        self.assertNotIn('/home/', serialized)

    def test_malformed_inventory_exercises_all_fail_closed_controls(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary)
            inventory, _ = self.make_source_copy(source)
            record = json.loads(inventory.read_text(encoding='utf-8'))
            record['schema'] = 'wrong'
            record['target_mode'] = 'scratchbird_emulation'
            record['scratchbird_emulation_state'] = 'passed'
            record['policy'].update({
                'frozen_clone_substitution': 'allowed',
                'active_corpus_authority': 'clone_catalog',
                'deprecated_clone_catalog_disposition': 'authoritative',
                'source_packet_mount': 'rw',
                'redistribution_default': 'approved',
                'unapproved_or_nonredistributable_access': 'public',
            })
            record['profiles'][0]['engine_id'] = record['profiles'][1][
                'engine_id'
            ]
            defaults = record['runtime_defaults']
            defaults.update({
                'definition_state': 'ready', 'base_image_digest': 'unpinned',
                'source_mount_mode': 'rw', 'rootfs_read_only': False,
                'live_suite': 'passed',
            })
            row = record['profiles'][0]
            row.update({
                'source_packet': '/unsafe', 'packet_manifest_sha256': 'bad',
                'license_artifact_sha256': 'bad', 'remaining_gates': [],
                'runtime': {}, 'fixture_sets': ['unknown'],
            })
            inventory.write_text(json.dumps(record), encoding='utf-8')
            result = evaluate(source)
            self.assertFalse(result['valid'])
            self.assertGreaterEqual(len(result['errors']), 15)

    def test_fixture_errors_reject_unsafe_missing_or_tainted_data(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary)
            inventory, fixtures = self.make_source_copy(source)
            manifest_path = fixtures / 'seed_manifest.json'
            manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
            manifest['classification'] = 'unknown'
            manifest['files'][0]['sha256'] = '0' * 64
            manifest['files'][0]['fixture_sets'] = ['relational_core']
            manifest['files'].extend([
                {'path': '/absolute', 'sha256': '0' * 64,
                 'fixture_sets': []},
                {'path': 'missing.json', 'sha256': '0' * 64,
                 'fixture_sets': []},
            ])
            manifest_path.write_text(json.dumps(manifest), encoding='utf-8')
            seed_path = fixtures / 'baseline_seed.json'
            seed = json.loads(seed_path.read_text(encoding='utf-8'))
            seed['contains_credentials'] = True
            seed_path.write_text(json.dumps(seed), encoding='utf-8')
            result = evaluate(source)
            self.assertFalse(result['valid'])
            self.assertTrue(any(
                'classification' in e for e in result['errors']
            ))
            self.assertTrue(any(
                'safely relative' in e for e in result['errors']
            ))
            self.assertTrue(any('missing' in e for e in result['errors']))
            self.assertTrue(any('credentials' in e for e in result['errors']))

    def test_missing_inventory_and_fixture_manifest_fail_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary)
            self.assertFalse(evaluate(source)['valid'])
            inventory, _ = self.make_source_copy(source)
            record = json.loads(inventory.read_text(encoding='utf-8'))
            record['runtime_defaults']['fixture_manifest'] = '../unsafe'
            inventory.write_text(json.dumps(record), encoding='utf-8')
            self.assertFalse(evaluate(source)['valid'])
            record['runtime_defaults']['fixture_manifest'] = 'missing.json'
            inventory.write_text(json.dumps(record), encoding='utf-8')
            self.assertFalse(evaluate(source)['valid'])

    def test_external_donor_audit_is_opt_in_and_read_only(self):
        donor = os.environ.get('CDEADMIN_DONOR_ROOT')
        if not donor:
            self.skipTest('CDEADMIN_DONOR_ROOT is not set')
        root = Path(donor)
        before = {
            row[0]: sha256(
                root / next(
                    item['source_packet']
                    for item in json.loads(
                        INVENTORY.read_text(encoding='utf-8')
                    )['profiles'] if item['engine_id'] == row[0]
                ) / 'RELEASE_EVIDENCE_MANIFEST.yaml'
            ) for row in EXPECTED
        }
        result = evaluate(ROOT, root)
        after = {
            engine: sha256(
                root / next(
                    item['source_packet']
                    for item in json.loads(
                        INVENTORY.read_text(encoding='utf-8')
                    )['profiles'] if item['engine_id'] == engine
                ) / 'RELEASE_EVIDENCE_MANIFEST.yaml'
            ) for engine in before
        }
        self.assertTrue(result['valid'], result['errors'])
        self.assertEqual(25, result['donor_packets_verified'])
        self.assertEqual(before, after)


class ReferenceCorpusPacketTests(unittest.TestCase):

    def make_packet(self, root):
        packet = root / 'sample/1.0'
        (packet / 'source-archive').mkdir(parents=True)
        (packet / 'license').mkdir()
        archive = packet / 'source-archive/sample-1.0.tar.gz'
        license_path = packet / 'license/LICENSE'
        tree = packet / 'TREE_MANIFEST.sha256'
        archive.write_bytes(b'exact source')
        license_path.write_bytes(b'license evidence')
        tree.write_text('tree evidence\n', encoding='utf-8')
        manifest = packet / 'RELEASE_EVIDENCE_MANIFEST.yaml'
        manifest.write_text(
            'candidate_version_label: 1.0\n'
            'proof_status: structurally_complete\n'
            'structural_audit_status: passed\n'
            'release_proven_remaining_gates:\n'
            '  - license_policy_review\n'
            '  - profile_admission_review\n',
            encoding='utf-8',
        )
        row = {
            'engine_id': 'sample', 'reference_profile': '1.0',
            'source_packet': 'sample/1.0',
            'source_candidate_version': '1.0',
            'packet_manifest_sha256': sha256(manifest),
            'tree_manifest_sha256': sha256(tree),
            'source_archive': 'source-archive/sample-1.0.tar.gz',
            'source_archive_sha256': sha256(archive),
            'license_artifact': 'license/LICENSE',
            'license_artifact_sha256': sha256(license_path),
            'proof_status': 'structurally_complete',
            'remaining_gates': [
                'license_policy_review', 'profile_admission_review'
            ],
        }
        return packet, row

    def test_packet_verifier_accepts_exact_critical_artifacts(self):
        with tempfile.TemporaryDirectory() as temporary:
            _, row = self.make_packet(Path(temporary))
            self.assertEqual([], _verify_packet(row, Path(temporary)))

    def test_packet_verifier_rejects_source_mutation(self):
        with tempfile.TemporaryDirectory() as temporary:
            packet, row = self.make_packet(Path(temporary))
            (packet / row['source_archive']).write_bytes(b'mutated')
            errors = _verify_packet(row, Path(temporary))
            self.assertTrue(any('source archive digest' in e for e in errors))

    def test_packet_verifier_rejects_missing_and_all_identity_drift(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _, row = self.make_packet(root)
            missing = dict(row, source_packet='missing/1.0')
            self.assertTrue(_verify_packet(missing, root))
            changed = dict(
                row, packet_manifest_sha256='0' * 64,
                tree_manifest_sha256='0' * 64,
                license_artifact_sha256='0' * 64,
                source_candidate_version='wrong', proof_status='wrong',
                remaining_gates=['wrong'],
            )
            errors = _verify_packet(changed, root)
            self.assertTrue(any(
                'evidence manifest digest' in e for e in errors
            ))
            self.assertTrue(any('tree manifest digest' in e for e in errors))
            self.assertTrue(any(
                'license artifact digest' in e for e in errors
            ))
            self.assertTrue(any(
                'candidate_version_label' in e for e in errors
            ))
            self.assertTrue(any('remaining-gate set' in e for e in errors))

    def test_plan_never_places_writable_work_inside_donor(self):
        with tempfile.TemporaryDirectory() as temporary:
            donor = Path(temporary) / 'donor'
            donor.mkdir()
            with self.assertRaisesRegex(CorpusError, 'inside donor corpus'):
                build_run_plan(ROOT, donor, 'postgresql', donor / 'work')

    def test_plan_rejects_nested_roots_unknown_profile_and_bad_packet(self):
        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary) / 'work'
            donor = work / 'donor'
            donor.mkdir(parents=True)
            with self.assertRaisesRegex(CorpusError, 'inside writable work'):
                build_run_plan(ROOT, donor, 'postgresql', work)
            donor = Path(temporary) / 'separate-donor'
            donor.mkdir()
            with self.assertRaisesRegex(CorpusError, 'unknown exact'):
                build_run_plan(ROOT, donor, 'unknown', work)
            with patch(
                'tools.cdeadmin_reference_corpus._verify_packet',
                return_value=['changed'],
            ):
                with self.assertRaisesRegex(CorpusError, 'changed'):
                    build_run_plan(ROOT, donor, 'postgresql', work)

    def test_plan_is_non_executing_and_blocked(self):
        with tempfile.TemporaryDirectory() as temporary:
            donor = Path(temporary) / 'donor'
            work = Path(temporary) / 'work'
            donor.mkdir()
            with patch(
                'tools.cdeadmin_reference_corpus._verify_packet',
                return_value=[],
            ):
                plan = build_run_plan(
                    ROOT, donor, 'postgresql', work
                )
            self.assertFalse(plan['runnable'])
            self.assertEqual('ro', plan['source']['mount_mode'])
            self.assertTrue(plan['container']['rootfs_read_only'])
            self.assertEqual('not_run', plan['live_suite'])
            self.assertEqual('not_run', plan['scratchbird_emulation_state'])


if __name__ == '__main__':
    unittest.main()
