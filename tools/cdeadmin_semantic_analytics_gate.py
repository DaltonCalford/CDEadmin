#!/usr/bin/env python3
##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Audit provider-family semantic analytics and end-user product coverage."""

from __future__ import annotations

import argparse
import importlib
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / 'web'
for path in (ROOT, WEB):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from tools.cdeadmin_provider_object_coverage_gate import (  # noqa: E402
    DEFERRED_ENGINE_IDS, provider_catalogs,
)
from pgadmin.cdeadmin.semantic_models.profiles import (  # noqa: E402
    SEMANTIC_PROFILE_SCHEMA, analytical_profile,
)


REQUIRED_CAPABILITIES = frozenset({
    'relationship_diagram', 'grain', 'hierarchies', 'measures',
    'calculated_measures', 'time_intelligence', 'parameters', 'filters',
    'drill_down', 'drill_through', 'pivot', 'cross_filtering', 'charts',
    'dashboards', 'reports', 'schedules', 'row_level_security',
    'tenant_filtering', 'metric_certification', 'lineage', 'versioning',
    'diagnostics', 'reproducibility',
})

NATIVE_COMPILERS = {
    'mongodb-native': {
        'kind': 'mongodb-aggregation',
        'provider_module': 'pgadmin.cdeadmin.providers.mongodb.provider',
        'compiler_module': 'pgadmin.cdeadmin.providers.mongodb.semantic',
        'callable': 'compile_mongodb_aggregation',
    },
    'neo4j-native': {
        'kind': 'neo4j-cypher',
        'provider_module': 'pgadmin.cdeadmin.providers.neo4j.provider',
        'compiler_module': 'pgadmin.cdeadmin.providers.neo4j.semantic',
        'callable': 'compile_neo4j_cypher',
    },
    'opensearch-native': {
        'kind': 'opensearch-composite-aggregation',
        'provider_module': 'pgadmin.cdeadmin.providers.opensearch.provider',
        'compiler_module': 'pgadmin.cdeadmin.providers.opensearch.semantic',
        'callable': 'compile_opensearch_aggregation',
    },
}


def _integration_failures(root):
    requirements = {
        'web/pgadmin/cdeadmin/semantic_models/models.py': (
            "model['relationships']", "model['parameters']",
            "model['visualizations']", "model['dashboards']",
            "model['schedules']", "model['reports']", 'tenant_filter',
            'certification', "query['cross_filters']", "query['drill']",
        ),
        'web/pgadmin/cdeadmin/semantic_models/service.py': (
            'reproducibility_manifest', 'def diagnostics',
            'exact_data_replay_requires_provider_snapshot',
        ),
        'web/pgadmin/static/js/Dialogs/ProviderWorkspaceContent.jsx': (
            'Semantic relationship diagram', 'Declared grain fields',
            'Time role', 'Metric owner', 'Add cross-filter',
            'Chart builder', 'Dashboard builder', 'Report builder',
            'Row-level security', 'Tenant filtering', 'Query diagnostics',
            'semanticCrossFilter', 'Run dashboard',
        ),
    }
    failures = []
    for relative, needles in requirements.items():
        path = root / relative
        if not path.is_file():
            failures.append(f'integration:missing-file:{relative}')
            continue
        source = path.read_text(encoding='utf-8')
        failures.extend(
            f'integration:{relative}:missing:{needle}'
            for needle in needles if needle not in source
        )
    return failures


def _native_compiler_failures(catalogs):
    failures = []
    activated = {}
    for profile_id, declaration in NATIVE_COMPILERS.items():
        if profile_id not in catalogs:
            failures.append(f'{profile_id}:provider-profile-missing')
            continue
        try:
            provider_module = importlib.import_module(
                declaration['provider_module']
            )
            compiler_module = importlib.import_module(
                declaration['compiler_module']
            )
        except ImportError:
            failures.append(f'{profile_id}:native-compiler-import-failed')
            continue
        profile = getattr(provider_module, 'PROFILE', None)
        compiler = getattr(compiler_module, declaration['callable'], None)
        if getattr(profile, 'semantic_compiler_kind', None) != declaration[
                'kind']:
            failures.append(f'{profile_id}:native-compiler-not-activated')
            continue
        if not callable(compiler):
            failures.append(f'{profile_id}:native-compiler-not-callable')
            continue
        if profile.profile_id != profile_id:
            failures.append(f'{profile_id}:native-compiler-profile-mismatch')
            continue
        activated[profile_id] = declaration['kind']
    return failures, activated


def audit(catalogs=None, root=ROOT):
    catalogs = provider_catalogs() if catalogs is None else catalogs
    failures = _integration_failures(root)
    compiler_failures, native_compilers = _native_compiler_failures(catalogs)
    failures.extend(compiler_failures)
    profiles = {}
    families = {}
    for profile_id in sorted(catalogs):
        descriptor = catalogs[profile_id]['descriptor']
        profile = analytical_profile(descriptor.get('model_family'))
        missing = sorted(REQUIRED_CAPABILITIES.difference(
            key for key, enabled in profile['designer_capabilities'].items()
            if enabled
        ))
        if profile['schema'] != SEMANTIC_PROFILE_SCHEMA:
            failures.append(f'{profile_id}:invalid-schema')
        if not profile['recognized_model_family']:
            failures.append(f'{profile_id}:unknown-model-family')
        if missing:
            failures.append(
                f'{profile_id}:missing-capabilities:{",".join(missing)}'
            )
        for field in (
            'source_kinds', 'source_classifications', 'dimension_kinds',
            'relationship_kinds', 'measure_kinds',
        ):
            if not profile[field]:
                failures.append(f'{profile_id}:empty-{field}')
        family = profile['semantic_family']
        families[family] = families.get(family, 0) + 1
        profiles[profile_id] = {
            'engine_id': descriptor['engine_id'],
            'provider_model_family': descriptor.get('model_family'),
            'semantic_family': family,
            'source_kinds': profile['source_kinds'],
            'dimension_kinds': profile['dimension_kinds'],
        }
    if len(families) < 8:
        failures.append('portfolio:insufficient-family-diversity')
    return {
        'schema': 'cdeadmin.semantic-analytics-gate.v1',
        'gate': 'provider-semantic-analytics-product-coverage',
        'complete': not failures,
        'profile_count': len(profiles),
        'semantic_family_count': len(families),
        'semantic_families': dict(sorted(families.items())),
        'required_capability_count': len(REQUIRED_CAPABILITIES),
        'native_compiler_count': len(native_compilers),
        'native_compilers': native_compilers,
        'scope': {
            'profile_ids': sorted(profiles),
            'deferred_engine_ids': list(DEFERRED_ENGINE_IDS),
        },
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
