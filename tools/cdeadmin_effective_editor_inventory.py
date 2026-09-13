#!/usr/bin/env python3
"""Collect provider-adapted editor catalogs without opening engine connections.

This examines installed implementations, not native engine completeness or
live permissions. Endpoints remain unverified and no secrets are supplied.
"""

import argparse
import importlib
import json
import sys
import uuid
from pathlib import Path
from types import ModuleType
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'web'))
if 'pgadmin' not in sys.modules:
    package = ModuleType('pgadmin')
    package.__path__ = [str(ROOT / 'web/pgadmin')]
    sys.modules['pgadmin'] = package

from pgadmin.cdeadmin.core import EndpointContext  # noqa: E402
from pgadmin.cdeadmin.core.registry import (  # noqa: E402
    PermissionGrant, PermissionGuard,
)
from pgadmin.cdeadmin.providers import BUILTIN_PACKAGES  # noqa: E402
from pgadmin.cdeadmin.visual_admin import (  # noqa: E402
    ProviderVisualAdministration, catalog_for_engine, enrich_engine_experience,
)


def collect():
    results = []
    for manifest_path, module_name in BUILTIN_PACKAGES:
        manifest = json.loads((ROOT / 'web/pgadmin/cdeadmin/providers' /
                               manifest_path).read_text())
        identity = manifest['identity']
        family = manifest['composition']['experience_families'][0]
        if family == 'scratchbird':
            continue
        result = {'profile_id': identity['profile_id'],
                  'profile_version': identity['profile_version'],
                  'engine_id': family,
                  'module': module_name}
        provider = None
        try:
            granted = {item['permission_id']: PermissionGrant(
                item['permission_id'], frozenset(item['scope']))
                for item in manifest['permissions'] if item['granted']}
            context = EndpointContext(
                endpoint_id=str(uuid.uuid4()), mode='legacy_native',
                experience_family=family, **{key: identity[key] for key in (
                    'provider_id', 'provider_version', 'profile_id',
                    'profile_version')},
                target_adapter_id=manifest['composition'][
                    'target_adapter_ids'][0],
                target_adapter_version='offline-inventory',
                pool_namespace=str(uuid.uuid4()),
                session_namespace=str(uuid.uuid4()),
                cache_namespace=str(uuid.uuid4()),
                diagnostic_namespace=str(uuid.uuid4()),
                effective_permissions=frozenset(granted),
                declared_runtime_family=family)
            guard = PermissionGuard(granted, context.effective_permissions,
                                    context=context)
            if identity['profile_id'] == 'postgresql-native':
                # Use the provider's side-effect-free surface module. Importing
                # its Flask-facing provider requires a running app's registry.
                surface = importlib.import_module(
                    'pgadmin.cdeadmin.providers.postgresql.preserved_surface')
                baseline = catalog_for_engine(family)
                catalog = surface.adapt_catalog(baseline)
                ProviderVisualAdministration._mark_graphical_form_authority(
                    baseline, catalog)
                supported = surface.preserved_operations()
                for obj in catalog['objects']:
                    for operation in obj['operations']:
                        operation['native_supported'] = (
                            operation['operation_id'] in supported.get(
                                obj['resource_kind'], ()))
                        operation['graphical_ready'] = operation.get(
                            'graphical_form_authority') in {
                                'engine-catalog', 'provider-adapter'}
                catalog = enrich_engine_experience(catalog)
                result['collection_mode'] = 'provider-surface-module'
            else:
                module = importlib.import_module(module_name)
                provider = module.create_provider(context, guard)
                catalog = provider.visual_admin_descriptor()
                result['collection_mode'] = 'provider-instance'
            result.update(status='collected', catalog=catalog)
        except Exception as error:
            result.update(status='collection-failed',
                          error_type=type(error).__name__, error=str(error))
        finally:
            if provider is not None:
                try:
                    provider.close()
                except Exception as error:
                    result.update(status='collection-failed',
                                  close_error_type=type(error).__name__)
        results.append(result)
    return {'schema': 'cdeadmin.effective-editor-inventory.v1',
            'live_connections': False, 'secrets_supplied': False,
            'native_completeness_verified': False,
            'excluded_engines': ['scratchbird'], 'profiles': results}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    with patch('socket.socket.connect', side_effect=RuntimeError(
            'Network connections are forbidden during offline inventory')):
        result = collect()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, default=str) + '\n')
    summary = [{'profile_id': item['profile_id'], 'status': item['status'],
                'objects': len(item.get('catalog', {}).get('objects', [])),
                'error': item.get('error')}
               for item in result['profiles']]
    print(json.dumps(summary, indent=2))
    return int(any(item['status'] != 'collected'
                   for item in result['profiles']))


if __name__ == '__main__':
    raise SystemExit(main())
