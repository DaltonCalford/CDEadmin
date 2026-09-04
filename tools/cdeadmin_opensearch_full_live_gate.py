#!/usr/bin/env python3
##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Qualify every OpenSearch 3.6 search administration obligation."""

from __future__ import annotations

import argparse
import copy
import json
import sys
import time
import uuid
from pathlib import Path
from types import ModuleType


ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / 'web'
if str(WEB) not in sys.path:
    sys.path.insert(0, str(WEB))
if 'pgadmin' not in sys.modules:
    package = ModuleType('pgadmin')
    package.__path__ = [str(WEB / 'pgadmin')]
    sys.modules['pgadmin'] = package

from pgadmin.cdeadmin.providers.opensearch.client import (  # noqa: E402
    OpenSearchClient,
)
from pgadmin.cdeadmin.providers.opensearch_sql_ppl.client import (  # noqa: E402
    OpenSearchSQLPPLClient,
)


REFERENCE_PROFILE = '3.6.0'
FULL_OPERATIONS = {
    'index': {
        'alter', 'create', 'delete', 'drop', 'insert', 'inspect', 'update',
    },
    'mapping': {'alter', 'create', 'inspect'},
    'settings': {'alter', 'inspect'},
    'alias': {'alter', 'create', 'drop', 'inspect'},
    'index-template': {'alter', 'create', 'drop', 'inspect'},
    'component-template': {'alter', 'create', 'drop', 'inspect'},
    'ingest-pipeline': {'alter', 'create', 'drop', 'inspect'},
    'shard': {'inspect'},
    'reindex-operation': {'execute'},
    'snapshot': {'execute', 'inspect'},
    'ingest-processor': {'alter', 'create', 'drop', 'inspect'},
    'query-profile': {'execute'},
}
CONCEPT_BINDINGS = {
    'indices': ('index',),
    'mappings': ('mapping',),
    'settings': ('settings',),
    'aliases': ('alias',),
    'templates': ('index-template', 'component-template'),
    'pipelines': ('ingest-pipeline',),
    'shards_and_replicas': ('shard',),
    'reindex_operations': ('reindex-operation',),
    'snapshots': ('snapshot',),
    'ingest_processors': ('ingest-processor',),
    'query_profiling': ('query-profile',),
}


def _route(options):
    return {
        'route_id': f'opensearch-full-{uuid.uuid4().hex[:12]}',
        'host': options.host,
        'port': options.port,
        'tls_mode': 'disable',
        'auth_kind': 'none',
        'connect_timeout': 30,
        'statement_timeout': 180,
    }


def _target(kind, **native):
    return {
        'resource_kind': kind,
        'native': copy.deepcopy(native),
        'extensions': {
            'opensearch': {'native': copy.deepcopy(native)},
            'opensearch_sql_ppl': {'native': copy.deepcopy(native)},
        },
    }


def _admin(client, route, kind, operation, draft=None, target=None):
    request = {
        'resource_kind': kind,
        'operation_id': operation,
        'draft': copy.deepcopy(draft or {}),
        '_provider_route': route,
    }
    if target is not None:
        request['target_resource'] = target
    plan = client.plan_admin_operation(request)
    result = client.apply_admin_operation({
        'provider_payload': plan['provider_payload'],
    })
    if result.get('automatic_retry') is not False or result.get(
            'transaction_finality_interpreted_by_common_code') is not False:
        raise RuntimeError('unsafe common execution policy was observed')
    return result


def _object_evidence(
    engine_id, exact_profile, run_id, operations, failures, details,
):
    passed = {
        kind: sorted(values) for kind, values in operations.items() if values
    }
    missing = {
        kind: sorted(expected.difference(operations.get(kind, set())))
        for kind, expected in FULL_OPERATIONS.items()
        if expected.difference(operations.get(kind, set()))
    }
    concepts = {}
    for concept, kinds in CONCEPT_BINDINGS.items():
        observed = {
            kind: passed[kind] for kind in kinds if kind in passed
        }
        if observed and all(
            FULL_OPERATIONS[kind].issubset(operations.get(kind, set()))
            for kind in kinds
        ):
            concepts[concept] = {
                'status': 'passed', 'operations': observed,
            }
    return {
        'schema': 'cdeadmin.provider-object-live-evidence.v1',
        'engine_id': engine_id,
        'exact_profile': exact_profile,
        'run_id': run_id,
        'evidence_scope': 'search-navigator-and-object-editor-operations',
        'raw_commands_used_for_provider_operations': False,
        'common_transaction_finality_interpreted': False,
        'automatic_mutation_retry': False,
        'passed_resource_operations': passed,
        'missing_resource_operations': missing,
        'operation_failures': failures,
        'concepts': {'search': concepts},
        'details': details,
        'passed': not missing and not failures,
    }


def verify(options):
    route = _route(options)
    run_id = uuid.uuid4().hex[:12]
    prefix = f'cdeadmin-{run_id}'
    names = {
        'source': prefix + '-source',
        'destination': prefix + '-destination',
        'restored': prefix + '-restored',
        'alias': prefix + '-alias',
        'component': prefix + '-component',
        'template': prefix + '-template',
        'pipeline': prefix + '-pipeline',
        'repository': prefix + '-repository',
        'snapshot': prefix + '-snapshot',
    }
    engine_id = options.engine
    exact_profile = (
        '3.6.0-sql-ppl'
        if engine_id == 'opensearch_sql_ppl' else REFERENCE_PROFILE
    )
    client = (
        OpenSearchSQLPPLClient()
        if engine_id == 'opensearch_sql_ppl' else OpenSearchClient()
    )
    operations = {kind: set() for kind in FULL_OPERATIONS}
    failures = {}
    details = {'started_at': time.time(), 'names': names}
    created_indices = set()
    created_templates = set()
    pipeline_created = False
    alias_created = False
    repository_created = False
    snapshot_created = False

    def perform(kind, operation, draft=None, target=None, verify_result=None):
        key = f'{kind}.{operation}'
        try:
            result = _admin(
                client, route, kind, operation, draft=draft, target=target
            )
            if verify_result is not None:
                verify_result(result['native_response'])
            operations[kind].add(operation)
            details[key] = {
                'status': 'passed',
                'http_status': result.get('http_status'),
            }
            return result
        except Exception as exc:
            failures[key] = f'{type(exc).__name__}: {exc}'
            details[key] = {
                'status': 'failed', 'error_type': type(exc).__name__,
            }
            return None

    def require_profile(native):
        if not isinstance(native, dict) or 'profile' not in native:
            raise RuntimeError('native query profile was not returned')

    def require_reindex(native):
        if not isinstance(native, dict) or native.get('total', 0) < 1:
            raise RuntimeError('native reindex response moved no documents')

    try:
        identity = client.runtime_identity({'route': route})
        if identity.get('version') != exact_profile:
            raise RuntimeError(
                'exact OpenSearch 3.6.0 identity was not proven'
            )
        details['runtime_identity'] = identity

        if perform('index', 'create', {
            'name': names['source'],
            'number_of_shards': 1,
            'number_of_replicas': 0,
            'mappings': {'properties': {
                'title': {'type': 'text'},
                'value': {'type': 'integer'},
            }},
            'aliases': {},
            'advanced_settings': {},
        }):
            created_indices.add(names['source'])
        source_target = _target(
            'index', name=names['source'], index=names['source']
        )
        perform('index', 'inspect', target=source_target)
        perform('index', 'alter', {
            'number_of_replicas': 0,
            'refresh_interval': '1s',
            'advanced_settings': {},
        }, source_target)
        perform('index', 'insert', {
            'index': names['source'], 'document_id': 'one',
            'document': {'title': 'source', 'value': 1},
        })
        perform('index', 'update', {
            'index': names['source'], 'document_id': 'one',
            'document': {'value': 2},
        }, source_target)
        perform('index', 'insert', {
            'index': names['source'], 'document_id': 'delete-me',
            'document': {'title': 'temporary', 'value': 0},
        })
        perform('index', 'delete', {
            'index': names['source'], 'document_id': 'delete-me',
            'acknowledge_delete': True,
        }, source_target)

        mapping_target = _target(
            'mapping', name=names['source'], index=names['source']
        )
        perform('mapping', 'create', {
            'index': names['source'], 'properties': {
                'created_field': {'type': 'keyword'},
            }, 'dynamic_templates': [],
        }, mapping_target)
        perform('mapping', 'inspect', target=mapping_target)
        perform('mapping', 'alter', {
            'index': names['source'], 'properties': {
                'altered_field': {'type': 'date'},
            }, 'dynamic_templates': [],
        }, mapping_target)

        settings_target = _target(
            'settings', name=names['source'], index=names['source']
        )
        perform('settings', 'inspect', target=settings_target)
        perform('settings', 'alter', {
            'index': names['source'], 'number_of_replicas': 0,
            'refresh_interval': '500ms', 'advanced_settings': {},
        }, settings_target)

        alias_target = _target(
            'alias', name=names['alias'], index=names['source']
        )
        if perform('alias', 'create', {
            'name': names['alias'], 'index': names['source'],
            'filter': {'range': {'value': {'gte': 0}}},
        }):
            alias_created = True
        perform('alias', 'inspect', target=alias_target)
        perform('alias', 'alter', {
            'index': names['source'],
            'filter': {'range': {'value': {'gte': 1}}},
        }, alias_target)

        component_target = _target(
            'component-template', name=names['component']
        )
        if perform('component-template', 'create', {
            'name': names['component'], 'version': 1,
            'settings': {'number_of_replicas': 0},
            'mappings': {'properties': {'component': {'type': 'keyword'}}},
            'aliases': {}, 'metadata': {'owner': 'cdeadmin-live-gate'},
        }):
            created_templates.add(('component-template', names['component']))
        perform('component-template', 'inspect', target=component_target)
        perform('component-template', 'alter', {
            'version': 2, 'settings': {'number_of_replicas': 0},
            'mappings': {'properties': {'component': {'type': 'keyword'}}},
            'aliases': {}, 'metadata': {'revision': 2},
        }, component_target)

        template_target = _target('index-template', name=names['template'])
        if perform('index-template', 'create', {
            'name': names['template'],
            'index_patterns': [prefix + '-matched-*'],
            'priority': 100, 'composed_of': [names['component']],
            'version': 1, 'settings': {}, 'mappings': {}, 'aliases': {},
            'metadata': {'owner': 'cdeadmin-live-gate'},
        }):
            created_templates.add(('index-template', names['template']))
        perform('index-template', 'inspect', target=template_target)
        perform('index-template', 'alter', {
            'index_patterns': [prefix + '-matched-*'],
            'priority': 101, 'composed_of': [names['component']],
            'version': 2, 'settings': {}, 'mappings': {}, 'aliases': {},
            'metadata': {'revision': 2},
        }, template_target)

        pipeline_target = _target('ingest-pipeline', name=names['pipeline'])
        if perform('ingest-pipeline', 'create', {
            'name': names['pipeline'], 'description': 'live gate',
            'version': 1, 'processors': [], 'on_failure': [],
        }):
            pipeline_created = True
        processor_target = _target(
            'ingest-processor', name='set-gate', pipeline=names['pipeline'],
            position=0, processor_type='set', pipeline_version=2,
        )
        perform('ingest-processor', 'create', {
            'pipeline': names['pipeline'], 'processor_type': 'set',
            'tag': 'set-gate', 'configuration': {
                'field': 'gate', 'value': 'created',
            }, 'expected_pipeline_version': 1,
        })
        perform('ingest-processor', 'inspect', target=processor_target)
        perform('ingest-processor', 'alter', {
            'pipeline': names['pipeline'], 'position': 0,
            'processor_type': 'set', 'tag': 'set-gate',
            'configuration': {'field': 'gate', 'value': 'altered'},
            'expected_pipeline_version': 2,
        }, processor_target)
        processor_target['extensions']['opensearch']['native'][
            'pipeline_version'] = 3
        perform('ingest-processor', 'drop', {
            'acknowledge_drop': True, 'expected_pipeline_version': 3,
        }, processor_target)
        perform('ingest-pipeline', 'inspect', target=pipeline_target)
        perform('ingest-pipeline', 'alter', {
            'description': 'live gate altered', 'version': 5,
            'processors': [], 'on_failure': [],
        }, pipeline_target)

        resources = client.list_resources({'route': route})
        shard = next((
            item for item in resources
            if item['resource_kind'] == 'shard' and
            item['native'].get('index') == names['source']
        ), None)
        if shard is None:
            failures['shard.discovery'] = 'source shard was not discovered'
        else:
            perform('shard', 'inspect', target=_target(
                'shard', **shard['native']
            ))

        perform('query-profile', 'execute', {
            'index': names['source'],
            'query': {'match_all': {}}, 'aggregations': {}, 'size': 0,
            'explain': False, 'acknowledge_operation': True,
        }, _target('query-profile', name='query-profile'), require_profile)

        reindex = perform('reindex-operation', 'execute', {
            'source_index': names['source'],
            'destination_index': names['destination'],
            'query': {'match_all': {}}, 'conflicts': 'abort',
            'refresh': True, 'wait_for_completion': True,
            'acknowledge_operation': True,
        }, _target('reindex-operation', name='reindex'), require_reindex)
        if reindex:
            created_indices.add(names['destination'])

        repository_target = _target(
            'repository', name=names['repository']
        )
        if _admin(client, route, 'repository', 'execute', {
            'action': 'register', 'name': names['repository'],
            'repository_type': 'fs', 'location': options.snapshot_path,
            'compress': True, 'settings': {},
            'acknowledge_operation': True,
        }):
            repository_created = True
        snapshot_target = _target(
            'snapshot', name=names['snapshot'],
            repository=names['repository'],
        )
        if perform('snapshot', 'execute', {
            'action': 'create', 'repository': names['repository'],
            'name': names['snapshot'], 'indices': names['source'],
            'include_global_state': False, 'ignore_unavailable': False,
            'partial': False, 'wait_for_completion': True,
            'acknowledge_operation': True,
        }, snapshot_target):
            snapshot_created = True
        perform('snapshot', 'inspect', target=snapshot_target)
        restored = perform('snapshot', 'execute', {
            'action': 'restore', 'repository': names['repository'],
            'name': names['snapshot'], 'indices': names['source'],
            'include_global_state': False, 'ignore_unavailable': False,
            'partial': False, 'rename_pattern': '(.+)',
            'rename_replacement': names['restored'],
            'wait_for_completion': True, 'acknowledge_operation': True,
        }, snapshot_target)
        if restored:
            created_indices.add(names['restored'])
    except Exception as exc:
        failures['gate.setup'] = f'{type(exc).__name__}: {exc}'
    finally:
        if alias_created:
            perform('alias', 'drop', {'acknowledge_drop': True},
                    _target('alias', name=names['alias'],
                            index=names['source']))
        if pipeline_created:
            perform('ingest-pipeline', 'drop', {
                'acknowledge_drop': True,
            }, _target('ingest-pipeline', name=names['pipeline']))
        for kind, name in sorted(created_templates, reverse=True):
            perform(kind, 'drop', {'acknowledge_drop': True},
                    _target(kind, name=name))
        if snapshot_created:
            try:
                _admin(client, route, 'snapshot', 'execute', {
                    'action': 'delete', 'repository': names['repository'],
                    'name': names['snapshot'],
                    'acknowledge_operation': True,
                }, _target('snapshot', name=names['snapshot'],
                           repository=names['repository']))
            except Exception as exc:
                failures['cleanup.snapshot'] = type(exc).__name__
        if repository_created:
            try:
                _admin(client, route, 'repository', 'execute', {
                    'action': 'delete', 'name': names['repository'],
                    'repository_type': 'fs', 'settings': {},
                    'acknowledge_operation': True,
                }, _target('repository', name=names['repository']))
            except Exception as exc:
                failures['cleanup.repository'] = type(exc).__name__
        for index in sorted(created_indices):
            result = perform('index', 'drop', {
                'acknowledge_drop': True,
            }, _target('index', name=index, index=index))
            if result is None and index != names['source']:
                failures.pop('index.drop', None)
        client.close()
    details['finished_at'] = time.time()
    return _object_evidence(
        engine_id, exact_profile, run_id, operations, failures, details
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        '--engine', choices=('opensearch', 'opensearch_sql_ppl'),
        default='opensearch',
    )
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=9200)
    parser.add_argument('--snapshot-path', default='/mnt/snapshots')
    parser.add_argument('--output', type=Path)
    options = parser.parse_args(argv)
    result = verify(options)
    document = json.dumps(result, indent=2, sort_keys=True) + '\n'
    if options.output:
        options.output.parent.mkdir(parents=True, exist_ok=True)
        options.output.write_text(document, encoding='utf-8')
    else:
        sys.stdout.write(document)
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
