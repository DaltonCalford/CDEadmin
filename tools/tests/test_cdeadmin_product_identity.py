##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Product identity, packaging, coexistence, and attribution tests."""

from __future__ import annotations

import copy
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType


ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / 'tools'
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import cdeadmin_product_identity as product_gate  # noqa: E402


POLICY_PATH = ROOT / product_gate.DEFAULT_POLICY
POLICY = product_gate.load_json(POLICY_PATH)
IDENTITY_PATH = ROOT / POLICY['identity']
RUNTIME_PATH = ROOT / POLICY['identity_runtime']
SMOKE_PATH = ROOT / POLICY['smoke_plan']
RUNTIME = product_gate.load_identity_runtime(RUNTIME_PATH)


def load_coexistence_runtime():
    packages = (
        ('pgadmin', ROOT / 'web/pgadmin'),
        ('pgadmin.cdeadmin', ROOT / 'web/pgadmin/cdeadmin'),
        ('pgadmin.cdeadmin.product',
         ROOT / 'web/pgadmin/cdeadmin/product'),
    )
    for name, path in packages:
        if name not in sys.modules:
            package = ModuleType(name)
            package.__path__ = [str(path)]
            sys.modules[name] = package
    sys.modules.setdefault('pgadmin.cdeadmin.product.identity', RUNTIME)
    name = 'pgadmin.cdeadmin.product.coexistence'
    if name not in sys.modules:
        specification = importlib.util.spec_from_file_location(
            name,
            ROOT / 'web/pgadmin/cdeadmin/product/coexistence.py',
        )
        module = importlib.util.module_from_spec(specification)
        sys.modules[name] = module
        specification.loader.exec_module(module)
    return sys.modules[name].isolated_profile


class ProductIdentityContractTests(unittest.TestCase):

    def setUp(self):
        self.identity = RUNTIME.load_identity(IDENTITY_PATH)

    def test_identity_is_an_explicit_independent_hard_fork(self):
        self.assertEqual('1.1.0', self.identity['identity_version'])
        self.assertEqual(
            'hard-fork', self.identity['product']['identity_status']
        )
        self.assertEqual(
            {
                'product': 'pgAdmin 4',
                'version': '9.17',
                'relationship': 'independent hard fork',
            },
            self.identity['product']['forked_from'],
        )
        self.assertFalse(self.identity['product']['release_ready'])

    def test_working_product_name_is_cdeadmin(self):
        self.assertEqual('CDEadmin', self.identity['product']['display_name'])
        self.assertEqual('cdeadmin', self.identity['product']['short_name'])

    def test_runtime_branding_and_version_match_identity_contract(self):
        def load_module(name, path):
            specification = importlib.util.spec_from_file_location(name, path)
            module = importlib.util.module_from_spec(specification)
            specification.loader.exec_module(module)
            return module

        branding = load_module('cdeadmin_branding', ROOT / 'web/branding.py')
        version = load_module('cdeadmin_version', ROOT / 'web/version.py')
        self.assertEqual(
            self.identity['product']['display_name'], branding.APP_NAME
        )
        self.assertEqual(
            self.identity['product']['short_name'], branding.APP_SHORT_NAME
        )
        self.assertEqual(
            self.identity['product']['version'], version.CDEADMIN_VERSION
        )
        self.assertEqual(
            self.identity['product']['forked_from']['version'],
            version.UPSTREAM_BASE_VERSION,
        )

    def test_namespace_matrix_has_no_pgadmin_collision(self):
        self.assertEqual([], RUNTIME.namespace_collisions(self.identity))

    def test_common_namespace_matrix_covers_all_state_scopes(self):
        common = self.identity['namespaces']['cdeadmin']['common']
        self.assertTrue(RUNTIME.REQUIRED_NAMESPACE_SCOPES.issubset(common))

    def test_platform_namespaces_cover_all_delivery_platforms(self):
        namespaces = self.identity['namespaces']['cdeadmin']
        self.assertTrue({
            'linux_desktop', 'linux_server', 'macos', 'windows', 'container',
        }.issubset(namespaces))

    def test_coexistence_is_import_only_and_non_mutating(self):
        coexistence = self.identity['coexistence']
        self.assertTrue(coexistence['import_only'])
        self.assertFalse(coexistence['mutate_pgadmin_profile'])
        self.assertFalse(coexistence['shared_database'])
        self.assertFalse(coexistence['shared_secret_store'])
        self.assertEqual(5051, coexistence['default_server_port'])

    def test_package_identifiers_do_not_reuse_pgadmin(self):
        identifiers = self.identity['packaging']['identifiers']
        forbidden = {'pgadmin', 'pgadmin4', 'pgadmin 4'}
        self.assertFalse(
            {value.casefold() for value in identifiers.values()} & forbidden
        )

    def test_update_channel_is_disabled_and_separate(self):
        update = self.identity['update_channel']
        self.assertFalse(update['enabled'])
        self.assertEqual('cdeadmin', update['channel_id'])
        self.assertIsNone(update['feed_url'])
        self.assertFalse(update['reuse_pgadmin_feed'])

    def test_signing_keys_are_unassigned_and_never_reused(self):
        for record in self.identity['signing'].values():
            self.assertEqual('unassigned', record['status'])
            self.assertFalse(record['reuse_upstream_key'])
            self.assertTrue(
                record['key_id'].startswith('UNASSIGNED-CDEADMIN-')
            )

    def test_exact_namespace_collision_is_rejected(self):
        altered = copy.deepcopy(self.identity)
        altered['namespaces']['cdeadmin']['common']['database'] = \
            altered['namespaces']['pgadmin']['common']['database']
        with self.assertRaisesRegex(
                RUNTIME.ProductIdentityError, 'namespaces collide'):
            RUNTIME.validate_identity(altered)

    def test_case_insensitive_namespace_collision_is_rejected(self):
        altered = copy.deepcopy(self.identity)
        altered['namespaces']['cdeadmin']['common']['environment'] = \
            'pgadmin_'
        with self.assertRaisesRegex(
                RUNTIME.ProductIdentityError, 'namespaces collide'):
            RUNTIME.validate_identity(altered)

    def test_release_ready_flag_cannot_be_enabled_early(self):
        altered = copy.deepcopy(self.identity)
        altered['product']['release_ready'] = True
        with self.assertRaisesRegex(
                RUNTIME.ProductIdentityError, 'cannot be release-ready'):
            RUNTIME.validate_identity(altered)

    def test_update_feed_cannot_be_assigned_early(self):
        altered = copy.deepcopy(self.identity)
        altered['update_channel']['feed_url'] = \
            'https://www.pgadmin.org/versions.json'
        with self.assertRaisesRegex(
                RUNTIME.ProductIdentityError, 'must remain unassigned'):
            RUNTIME.validate_identity(altered)

    def test_upstream_package_identifier_is_rejected(self):
        altered = copy.deepcopy(self.identity)
        altered['packaging']['identifiers']['linux_package'] = 'pgadmin4'
        with self.assertRaisesRegex(
                RUNTIME.ProductIdentityError, 'reuses pgAdmin identity'):
            RUNTIME.validate_identity(altered)

    def test_linux_desktop_profile_uses_xdg_cdeadmin_namespaces(self):
        profile = load_coexistence_runtime()(
            'linux-desktop',
            environment={
                'HOME': '/users/tester',
                'XDG_CONFIG_HOME': '/profiles/config',
                'XDG_DATA_HOME': '/profiles/data',
                'XDG_CACHE_HOME': '/profiles/cache',
                'XDG_STATE_HOME': '/profiles/state',
                'XDG_RUNTIME_DIR': '/profiles/runtime',
            },
            identity=self.identity,
        )
        self.assertEqual('/profiles/config/cdeadmin', profile['CONFIG_DIR'])
        self.assertEqual('/profiles/data/cdeadmin', profile['DATA_DIR'])
        self.assertEqual('cdeadmin_session', profile['SESSION_COOKIE_NAME'])

    def test_linux_server_profile_never_uses_pgadmin_state_paths(self):
        profile = load_coexistence_runtime()(
            'linux-server', environment={'HOME': '/users/tester'},
            identity=self.identity,
        )
        state = {
            value for key, value in profile.items()
            if key.endswith('_DIR') or key.endswith('_PATH') or
            key == 'LOG_FILE'
        }
        self.assertTrue(all('cdeadmin' in value.casefold() for value in state))
        self.assertTrue(
            all('pgadmin' not in value.casefold() for value in state)
        )

    def test_windows_profile_uses_independent_roaming_and_local_state(self):
        profile = load_coexistence_runtime()(
            'windows',
            environment={
                'APPDATA': r'C:\Profiles\Roaming',
                'LOCALAPPDATA': r'C:\Profiles\Local',
            },
            home=r'C:\Users\tester',
            identity=self.identity,
        )
        self.assertEqual(
            r'C:\Profiles\Roaming\CDEadmin', profile['DATA_DIR']
        )
        self.assertEqual(
            r'C:\Profiles\Local\CDEadmin\Cache', profile['CACHE_DIR']
        )

    def test_resolved_profiles_disable_update_checks(self):
        for platform in ('linux-desktop', 'linux-server', 'macos',
                         'windows', 'container'):
            profile = load_coexistence_runtime()(
                platform, environment={'HOME': '/users/tester'},
                identity=self.identity,
            )
            self.assertFalse(profile['UPGRADE_CHECK_ENABLED'])
            self.assertIsNone(profile['UPGRADE_CHECK_URL'])
            self.assertEqual('cdeadmin', profile['UPGRADE_CHECK_KEY'])


class ProductIdentityGateTests(unittest.TestCase):

    def test_source_anchors_are_inventoried_without_global_rename(self):
        anchors, errors = product_gate.check_source_anchors(ROOT, POLICY)
        self.assertEqual([], errors)
        self.assertEqual(5, len(anchors))
        self.assertTrue(all(item['present'] for item in anchors))

    def test_active_product_surfaces_use_cdeadmin_identity(self):
        surfaces, errors = product_gate.check_product_surfaces(ROOT, POLICY)
        self.assertEqual([], errors)
        self.assertGreaterEqual(len(surfaces), 10)
        self.assertTrue(all(item['valid'] for item in surfaces))

    def test_obsolete_upstream_product_banner_is_absent(self):
        matches, errors = product_gate.check_product_banners(ROOT, POLICY)
        self.assertEqual([], matches)
        self.assertEqual([], errors)

    def test_obsolete_upstream_product_banner_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)
            (source / 'source.py').write_text(
                '# pgAdmin 4 - PostgreSQL Tools\n', encoding='utf-8'
            )
            policy = {
                'forbidden_product_banners': [
                    'pgAdmin 4 - PostgreSQL Tools'
                ],
                'inventory_excluded_directories': [],
            }
            matches, errors = product_gate.check_product_banners(
                source, policy
            )
        self.assertEqual(1, len(matches))
        self.assertTrue(errors)

    def test_upstream_and_cdeadmin_notices_are_complete(self):
        notices, errors = product_gate.check_notices(ROOT, POLICY)
        self.assertEqual([], errors)
        self.assertTrue(notices['cdeadmin']['preserved'])
        self.assertTrue(
            all(item['preserved'] for item in notices['upstream'])
        )

    def test_smoke_plan_covers_every_selected_delivery_mode(self):
        identity = RUNTIME.load_identity(IDENTITY_PATH)
        result, errors = product_gate.check_smoke_plan(
            ROOT, identity, POLICY
        )
        self.assertEqual([], errors)
        self.assertEqual(
            set(identity['packaging']['selected_delivery_modes']),
            {item['mode'] for item in result['delivery_modes']},
        )
        self.assertTrue(
            all(item['planned'] for item in result['delivery_modes'])
        )

    def test_smoke_plan_has_all_identity_and_coexistence_assertions(self):
        plan = product_gate.load_json(SMOKE_PATH)
        self.assertTrue({
            'cdeadmin-identity', 'isolated-namespaces',
            'upstream-attribution', 'pgadmin-coexistence',
            'independent-update-channel', 'independent-signing-identity',
        }.issubset(plan['common_assertions']))

    def test_inventory_counts_terms_by_product_surface(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)
            (source / 'web').mkdir()
            (source / 'pkg').mkdir()
            (source / 'web/app.py').write_text(
                'pgAdmin 4 PGADMIN_ pgadmin4', encoding='utf-8'
            )
            (source / 'pkg/build.sh').write_text(
                'pgAdmin 4 pgAdmin', encoding='utf-8'
            )
            policy = {
                'inventory_terms': ['pgAdmin 4', 'pgadmin4', 'PGADMIN_'],
                'inventory_excluded_directories': [],
            }
            result = product_gate.branding_inventory(source, policy)
        self.assertEqual(2, result['scanned_files'])
        self.assertEqual(2, result['occurrences']['pgAdmin 4']['total'])
        self.assertEqual(
            1,
            result['occurrences']['PGADMIN_']['by_surface'][
                'web-application'
            ],
        )

    def test_inventory_excludes_generated_and_dependency_trees(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)
            (source / 'node_modules').mkdir()
            (source / 'generated').mkdir()
            (source / 'node_modules/a.js').write_text(
                'pgAdmin 4', encoding='utf-8'
            )
            (source / 'generated/a.js').write_text(
                'pgAdmin 4', encoding='utf-8'
            )
            result = product_gate.branding_inventory(source, {
                'inventory_terms': ['pgAdmin 4'],
                'inventory_excluded_directories': [
                    'node_modules', 'generated'
                ],
            })
        self.assertEqual(0, result['scanned_files'])
        self.assertEqual(0, result['occurrences']['pgAdmin 4']['total'])

    def test_missing_protected_notice_is_a_gate_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)
            (source / 'NOTICE.md').write_text(
                'complete CDEadmin notice', encoding='utf-8'
            )
            policy = {
                'protected_upstream_notices': ['LICENSE'],
                'upstream_notice_fragments': ['pgAdmin 4'],
                'notice': 'NOTICE.md',
                'cdeadmin_notice_fragments': ['complete CDEadmin notice'],
            }
            _result, errors = product_gate.check_notices(source, policy)
        self.assertTrue(errors)
        self.assertIn('protected upstream notice unavailable', errors[0])

    def test_complete_repository_gate_passes(self):
        result = product_gate.evaluate(
            ROOT, POLICY_PATH, include_inventory=False
        )
        self.assertTrue(result['valid'], result['errors'])
        self.assertEqual([], result['namespace_collisions'])
        self.assertFalse(result['release_ready'])

    def test_live_brand_inventory_covers_all_primary_surfaces(self):
        result = product_gate.evaluate(
            ROOT, POLICY_PATH, include_inventory=True
        )
        inventory = result['branding_inventory']
        surfaces = inventory['matched_files_by_surface']
        self.assertGreater(inventory['matched_files'], 0)
        self.assertTrue({
            'web-application', 'packaging', 'desktop-runtime'
        }.issubset(surfaces))


if __name__ == '__main__':
    unittest.main()
