#!/usr/bin/env python3
##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Audit provider-owned navigator/editor declarations for every profile."""

from __future__ import annotations

import argparse
import copy
import importlib
import json
import sys
import uuid
from pathlib import Path
from types import ModuleType, SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / 'web'
if str(WEB) not in sys.path:
    sys.path.insert(0, str(WEB))
if 'pgadmin' not in sys.modules:
    package = ModuleType('pgadmin')
    package.__path__ = [str(WEB / 'pgadmin')]
    sys.modules['pgadmin'] = package

from pgadmin.cdeadmin.providers import BUILTIN_PACKAGES  # noqa: E402
from pgadmin.cdeadmin.providers.postgresql.preserved_surface import (  # noqa: E402
    SURFACE_ID, concept_declarations,
)
from pgadmin.cdeadmin.visual_admin import (  # noqa: E402
    catalog_for_engine, enrich_engine_experience,
)


DEFERRED_ENGINE_IDS = ('scratchbird',)
PROVIDER_ROOT = WEB / 'pgadmin' / 'cdeadmin' / 'providers'


class _InventoryPermissions:
    """Non-secret permission facade used only to build static descriptors."""

    @staticmethod
    def require(*_args, **_kwargs):
        return None

    @staticmethod
    def allows(*_args, **_kwargs):
        return True

    @staticmethod
    def acquire_secret(*_args, **_kwargs):
        return None


def _namespace():
    return str(uuid.uuid4())


def _context(identity):
    """Build an isolated, unverified context without opening a connection."""
    return SimpleNamespace(
        endpoint_id=_namespace(),
        mode='legacy_native',
        profile_id=identity['profile_id'],
        provider_id=identity['provider_id'],
        provider_version=identity['provider_version'],
        runtime_verification_state='unverified',
        verified_runtime_family=None,
        declared_runtime_family=None,
        effective_permissions=frozenset({'network', 'secret_read'}),
        session_namespace=_namespace(),
        cache_namespace=_namespace(),
        pool_namespace=_namespace(),
    )


def _postgresql_descriptor():
    """Bind PostgreSQL to its audited, preserved native administration UI."""
    descriptor = copy.deepcopy(catalog_for_engine('postgresql'))
    descriptor['administration_surface'] = {
        'surface_id': SURFACE_ID,
        'workflow': 'legacy_preserved',
        'provider_owned': True,
    }
    descriptor['concept_declarations'] = {
        'relational': concept_declarations(),
    }
    return enrich_engine_experience(descriptor)


def provider_catalogs():
    """Return descriptors after each profile's client adapter decorates it.

    Provider clients are constructed but no endpoint is configured and no
    connection/session method is called. This is important: declarations and
    object operations belong to adapters, not to the generic family catalog.
    """
    catalogs = {}
    for relative_path, module_name in BUILTIN_PACKAGES:
        manifest = json.loads(
            (PROVIDER_ROOT / relative_path).read_text(encoding='utf-8')
        )
        identity = manifest['identity']
        profile_id = identity['profile_id']
        if profile_id == 'postgresql-native':
            descriptor = _postgresql_descriptor()
        else:
            module = importlib.import_module(module_name)
            provider = module.create_provider(
                _context(identity), _InventoryPermissions()
            )
            descriptor = provider.visual_admin_descriptor()
        if profile_id in catalogs:
            raise RuntimeError(f'duplicate provider profile: {profile_id}')
        catalogs[profile_id] = {
            'engine_id': descriptor['engine_id'],
            'provider_id': identity['provider_id'],
            'profile_version': identity['profile_version'],
            'descriptor': descriptor,
        }
    return catalogs


def audit(catalogs=None):
    """Return a deterministic, fail-closed structural portfolio inventory.

    Exact-runtime activation is intentionally delegated to the strict live
    engine gates. This gate proves that every in-scope provider profile has a
    final declaration and a usable catalog or declared external surface.
    """
    catalogs = provider_catalogs() if catalogs is None else catalogs
    profiles = {}
    failures = []
    concept_count = 0
    catalogued_count = 0
    not_applicable_count = 0
    blocking_missing_count = 0
    external_surface_count = 0
    declared_count = 0
    declaration_evidence_count = 0
    family_slice_count = 0
    for profile_id in sorted(catalogs):
        profile = catalogs[profile_id]
        coverage = profile['descriptor']['concept_coverage']
        profile_rows = []
        family_slice_count += len(coverage['families'])
        for family in coverage['families']:
            for concept in family['concepts']:
                row = {
                    'family_id': family['family_id'],
                    **concept,
                }
                profile_rows.append(row)
                concept_count += 1
                prefix = (
                    f"{profile_id}.{family['family_id']}."
                    f"{concept['concept_id']}"
                )
                if concept['catalog_state'] == 'catalogued':
                    catalogued_count += 1
                elif concept['catalog_state'] == 'external_surface':
                    external_surface_count += 1
                elif concept['declared_status'] == 'not_applicable':
                    not_applicable_count += 1
                else:
                    blocking_missing_count += 1
                    failures.append(f'{prefix}:missing-catalog-object')
                if concept['declared_status'] is None:
                    failures.append(f'{prefix}:undeclared')
                else:
                    declared_count += 1
                if concept['evidence']:
                    declaration_evidence_count += 1
        profiles[profile_id] = {
            'engine_id': profile['engine_id'],
            'provider_id': profile['provider_id'],
            'profile_version': profile['profile_version'],
            'declaration_ready': coverage['declaration_ready'],
            'activation_ready': coverage['activation_ready'],
            'concepts': profile_rows,
        }
    return {
        'schema': 'cdeadmin.provider-object-coverage-gate.v2',
        'gate': 'provider-navigator-object-editor-structural-coverage',
        'complete': not failures,
        'scope': {
            'profile_ids': sorted(profiles),
            'deferred_engine_ids': list(DEFERRED_ENGINE_IDS),
            'live_activation': 'delegated-to-strict-provider-engine-gates',
        },
        'profile_count': len(profiles),
        'family_slice_count': family_slice_count,
        'concept_count': concept_count,
        'catalogued_count': catalogued_count,
        'external_surface_count': external_surface_count,
        'not_applicable_count': not_applicable_count,
        'blocking_missing_count': blocking_missing_count,
        'declared_count': declared_count,
        'undeclared_count': concept_count - declared_count,
        'declaration_evidence_count': declaration_evidence_count,
        'failures': failures,
        'profiles': profiles,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    options = parser.parse_args(argv)
    result = audit()
    document = json.dumps(result, indent=2, sort_keys=True) + '\n'
    if options.output:
        options.output.parent.mkdir(parents=True, exist_ok=True)
        options.output.write_text(document, encoding='utf-8')
    else:
        sys.stdout.write(document)
    return 0 if result['complete'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
