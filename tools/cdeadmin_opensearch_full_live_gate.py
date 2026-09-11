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
import os
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
SEARCH_OPERATIONS = {
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


class _SecretLease:
    def __init__(self, value):
        self.value = bytearray(value.encode('utf-8'))

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        for index in range(len(self.value)):
            self.value[index] = 0

    def use(self, callback):
        return callback(memoryview(self.value))


def _route(options, *, security=False):
    route = {
        'route_id': f'opensearch-full-{uuid.uuid4().hex[:12]}',
        'host': options.host,
        'port': options.security_port if security else options.port,
        'tls_mode': 'require' if security else 'disable',
        'auth_kind': 'basic' if security else 'none',
        'connect_timeout': 30,
        'statement_timeout': 180,
    }
    if security:
        route.update({
            'username': options.security_username,
            'credential_reference_id': 'opensearch-security-password',
            'principal_reference': 'cdeadmin-opensearch-live-gate',
        })
    return route


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
    expected_operations,
):
    passed = {
        kind: sorted(values) for kind, values in operations.items() if values
    }
    missing = {
        kind: sorted(expected.difference(operations.get(kind, set())))
        for kind, expected in expected_operations.items()
        if expected.difference(operations.get(kind, set()))
    }
    concepts = {}
    for concept, kinds in CONCEPT_BINDINGS.items():
        observed = {
            kind: passed[kind] for kind in kinds if kind in passed
        }
        if observed and all(
            SEARCH_OPERATIONS[kind].issubset(operations.get(kind, set()))
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
        'data_stream': prefix + '-stream',
        'data_stream_template': prefix + '-stream-template',
        'script': prefix + '-script',
        'policy': prefix + '-policy',
        'user': prefix + '-user',
        'role': prefix + '-role',
        'role_mapping': prefix + '-role-mapping',
        'tenant': prefix + '-tenant',
        'data_source': prefix.replace('-', '_') + '_source',
    }
    engine_id = options.engine
    exact_profile = (
        '3.6.0-sql-ppl'
        if engine_id == 'opensearch_sql_ppl' else REFERENCE_PROFILE
    )
    security_password = (
        os.environ.get(options.security_password_environment)
        if options.security_password_environment else None
    )

    def acquire_secret(reference, _principal, _purpose, _kind):
        if reference != 'opensearch-security-password' or (
                security_password is None):
            raise RuntimeError('OpenSearch security secret is unavailable')
        return _SecretLease(security_password)

    client = (
        OpenSearchSQLPPLClient(acquire_secret)
        if engine_id == 'opensearch_sql_ppl'
        else OpenSearchClient(acquire_secret)
    )
    expected_operations = {
        kind: set(values) for kind, values in client.ADMIN_OPERATIONS.items()
    }
    operations = {kind: set() for kind in expected_operations}
    failures = {}
    details = {'started_at': time.time(), 'names': names}
    created_indices = set()
    created_templates = set()
    pipeline_created = False
    alias_created = False
    repository_created = False
    snapshot_created = False
    stream_created = False
    script_created = False
    policy_created = False
    security_created = set()
    data_source_created = False

    def perform(
        kind, operation, draft=None, target=None, verify_result=None,
        route_override=None,
    ):
        key = f'{kind}.{operation}'
        try:
            result = _admin(
                client, route_override or route, kind, operation,
                draft=draft, target=target
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

        if engine_id == 'opensearch':
            discovered = client.list_resources({'route': route})
            cluster_resource = next(
                item for item in discovered
                if item['resource_kind'] == 'cluster'
            )
            node_resource = next(
                item for item in discovered
                if item['resource_kind'] == 'node'
            )
            perform('cluster', 'inspect', target=_target(
                'cluster', **cluster_resource['native']
            ))
            perform('cluster', 'alter', {
                'definition': {'transient': {
                    'cluster.routing.allocation.enable': 'all',
                }},
            }, _target('cluster', name='cluster'))
            perform('cluster', 'execute', {
                'action': 'reroute', 'definition': {'commands': []},
                'acknowledge_operation': True,
            }, _target('cluster', name='cluster'))
            perform('node', 'inspect', target=_target(
                'node', **node_resource['native']
            ))

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

        if engine_id == 'opensearch':
            document_target = _target(
                'document', name='document-one', index=names['source'],
                _id='document-one',
            )
            perform('document', 'insert', {
                'index': names['source'], 'document_id': 'document-one',
                'document': {'title': 'document', 'value': 11},
            }, document_target)
            perform('document', 'inspect', target=document_target)
            perform('document', 'update', {
                'index': names['source'], 'document_id': 'document-one',
                'document': {'title': 'document altered', 'value': 12},
            }, document_target)
            perform('document', 'delete', {
                'index': names['source'], 'document_id': 'document-one',
                'acknowledge_delete': True,
            }, document_target)

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

        if engine_id == 'opensearch':
            field_target = _target(
                'field', name='provider_field', index=names['source']
            )
            perform('field', 'create', {
                'index': names['source'], 'name': 'provider_field',
                'field_type': 'keyword', 'indexed': True, 'stored': False,
                'advanced_definition': {'ignore_above': 128},
            }, field_target)
            perform('field', 'inspect', target=field_target)
            perform('field', 'alter', {
                'index': names['source'], 'name': 'provider_field',
                'field_type': 'keyword', 'indexed': True, 'stored': False,
                'advanced_definition': {
                    'ignore_above': 128, 'meta': {'revision': '2'},
                },
            }, field_target)

            analysis_definitions = {
                'analyzer': (
                    {'type': 'custom', 'tokenizer': 'standard',
                     'filter': ['lowercase']},
                    {'type': 'custom', 'tokenizer': 'standard',
                     'filter': ['lowercase', 'asciifolding']},
                ),
                'normalizer': (
                    {'type': 'custom', 'filter': ['lowercase']},
                    {'type': 'custom',
                     'filter': ['lowercase', 'asciifolding']},
                ),
                'tokenizer': (
                    {'type': 'edge_ngram', 'min_gram': 2, 'max_gram': 10,
                     'token_chars': ['letter', 'digit']},
                    {'type': 'edge_ngram', 'min_gram': 2, 'max_gram': 12,
                     'token_chars': ['letter', 'digit']},
                ),
            }
            for kind, (created, altered) in analysis_definitions.items():
                object_name = f'cdeadmin_{kind}'
                target = _target(
                    kind, name=object_name, index=names['source']
                )
                perform(kind, 'create', {
                    'index': names['source'], 'name': object_name,
                    'definition': created,
                }, target)
                perform(kind, 'inspect', target=target)
                perform(kind, 'alter', {
                    'index': names['source'], 'name': object_name,
                    'definition': altered,
                }, target)

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

        if engine_id == 'opensearch':
            stream_template_target = _target(
                'index-template', name=names['data_stream_template']
            )
            if perform('index-template', 'create', {
                'name': names['data_stream_template'],
                'index_patterns': [names['data_stream'] + '*'],
                'priority': 200, 'composed_of': [], 'version': 1,
                'data_stream': True, 'settings': {},
                'mappings': {'properties': {
                    '@timestamp': {'type': 'date'},
                }}, 'aliases': {},
                'metadata': {'owner': 'cdeadmin-live-gate'},
            }, stream_template_target):
                created_templates.add((
                    'index-template', names['data_stream_template']
                ))
            stream_target = _target(
                'data-stream', name=names['data_stream']
            )
            if perform('data-stream', 'create', {
                'name': names['data_stream'], 'definition': {},
            }, stream_target):
                stream_created = True
            perform('data-stream', 'inspect', target=stream_target)

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

        if engine_id == 'opensearch':
            script_target = _target('script', name=names['script'])
            if perform('script', 'create', {
                'name': names['script'], 'definition': {'script': {
                    'lang': 'painless', 'source': 'return params.value;',
                }},
            }, script_target):
                script_created = True
            perform('script', 'inspect', target=script_target)
            perform('script', 'alter', {
                'definition': {'script': {
                    'lang': 'painless',
                    'source': 'return params.value + 1;',
                }},
            }, script_target)

            policy_target = _target('policy', name=names['policy'])
            policy_definition = {'policy': {
                'description': 'CDEadmin live qualification',
                'default_state': 'active',
                'states': [{
                    'name': 'active', 'actions': [], 'transitions': [],
                }],
            }}
            if perform('policy', 'create', {
                'name': names['policy'], 'definition': policy_definition,
            }, policy_target):
                policy_created = True
            policy_inspection = perform(
                'policy', 'inspect', target=policy_target
            )
            altered_policy = copy.deepcopy(policy_definition)
            altered_policy['policy']['description'] = (
                'CDEadmin live qualification altered'
            )
            perform('policy', 'alter', {
                'definition': altered_policy,
                'if_seq_no': policy_inspection[
                    'native_response']['_seq_no'],
                'if_primary_term': policy_inspection[
                    'native_response']['_primary_term'],
            }, policy_target)

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
        if perform('repository', 'execute', {
            'action': 'register', 'name': names['repository'],
            'repository_type': 'fs', 'location': options.snapshot_path,
            'compress': True, 'settings': {},
            'acknowledge_operation': True,
        }, repository_target):
            repository_created = True
        perform('repository', 'inspect', target=repository_target)
        perform('repository', 'execute', {
            'action': 'verify', 'name': names['repository'],
            'repository_type': 'fs', 'settings': {},
            'acknowledge_operation': True,
        }, repository_target)
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

        if engine_id == 'opensearch_sql_ppl':
            perform('catalog', 'inspect', target=_target(
                'catalog', name='OpenSearch'
            ))
            data_source_target = _target(
                'data-source', name=names['data_source'],
                dataSourceName=names['data_source'],
            )
            data_source_definition = {
                'name': names['data_source'], 'connector': 'OPENSEARCH',
                'description': 'CDEadmin qualification',
                'allowedRoles': [], 'properties': {},
            }
            if perform('data-source', 'create', {
                'name': names['data_source'],
                'definition': data_source_definition,
            }, data_source_target):
                data_source_created = True
            perform('data-source', 'inspect', target=data_source_target)
            altered_source = copy.deepcopy(data_source_definition)
            altered_source['description'] = 'CDEadmin qualification altered'
            perform('data-source', 'alter', {
                'definition': altered_source,
            }, data_source_target)
            query_target = _target('query', name='SQL', language='sql')
            perform('query', 'inspect', target=query_target)
            perform('query', 'execute', {
                'language': 'sql', 'source': 'SELECT 1 AS answer',
                'parameters': {},
            }, query_target)
            prepared_target = _target(
                'prepared-query', name='SQL prepared query', language='sql'
            )
            perform('prepared-query', 'inspect', target=prepared_target)
            perform('prepared-query', 'execute', {
                'language': 'sql', 'source': 'SELECT ? AS answer',
                'parameters': [{'type': 'integer', 'value': 42}],
            }, prepared_target)
            settings_target = _target(
                'language-settings', name='SQL/PPL settings'
            )
            perform('language-settings', 'inspect', target=settings_target)
            perform('language-settings', 'alter', {
                'definition': {'transient': {
                    'plugins.sql.cursor.keep_alive': '1m',
                }},
            }, settings_target)

        if engine_id == 'opensearch':
            if security_password is None:
                failures['security.setup'] = (
                    'security operations require '
                    '--security-password-environment'
                )
            else:
                security_route = _route(options, security=True)
                security_client = OpenSearchClient(acquire_secret)
                try:
                    security_identity = security_client.runtime_identity({
                        'route': security_route,
                    })
                    if security_identity.get('version') != REFERENCE_PROFILE:
                        raise RuntimeError(
                            'secured OpenSearch runtime is not exact 3.6.0'
                        )
                finally:
                    security_client.close()
                security_definitions = {
                    'user': {
                        'password': 'N7!bQ4@tR9#xW2$k',
                        'backend_roles': [], 'attributes': {},
                    },
                    'role': {
                        'cluster_permissions': ['cluster_composite_ops_ro'],
                        'index_permissions': [{
                            'index_patterns': ['*'],
                            'allowed_actions': ['read'],
                        }], 'tenant_permissions': [],
                    },
                    'role-mapping': {
                        'backend_roles': [], 'hosts': [],
                        'users': [names['user']],
                    },
                    'tenant': {'description': 'CDEadmin qualification'},
                }
                security_names = {
                    'user': names['user'], 'role': names['role'],
                    'role-mapping': names['role'],
                    'tenant': names['tenant'],
                }
                for kind in ('user', 'role', 'role-mapping', 'tenant'):
                    target = _target(kind, name=security_names[kind])
                    if perform(
                        kind, 'create', {
                            'name': security_names[kind],
                            'definition': security_definitions[kind],
                        }, target, route_override=security_route,
                    ):
                        security_created.add(kind)
                    perform(
                        kind, 'inspect', target=target,
                        route_override=security_route,
                    )
                    altered = copy.deepcopy(security_definitions[kind])
                    if kind == 'tenant':
                        altered['description'] += ' altered'
                    perform(
                        kind, 'alter', {'definition': altered}, target,
                        route_override=security_route,
                    )
    except Exception as exc:
        failures['gate.setup'] = f'{type(exc).__name__}: {exc}'
    finally:
        if data_source_created:
            perform('data-source', 'drop', {
                'acknowledge_drop': True,
            }, _target(
                'data-source', name=names['data_source'],
                dataSourceName=names['data_source'],
            ))
        if engine_id == 'opensearch' and security_password is not None:
            security_route = _route(options, security=True)
            security_names = {
                'user': names['user'], 'role': names['role'],
                'role-mapping': names['role'],
                'tenant': names['tenant'],
            }
            for kind in ('role-mapping', 'role', 'user', 'tenant'):
                if kind in security_created:
                    perform(
                        kind, 'drop', {'acknowledge_drop': True},
                        _target(kind, name=security_names[kind]),
                        route_override=security_route,
                    )
        if policy_created:
            perform('policy', 'drop', {'acknowledge_drop': True},
                    _target('policy', name=names['policy']))
        if script_created:
            perform('script', 'drop', {'acknowledge_drop': True},
                    _target('script', name=names['script']))
        if stream_created:
            perform('data-stream', 'drop', {'acknowledge_drop': True},
                    _target('data-stream', name=names['data_stream']))
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
        engine_id, exact_profile, run_id, operations, failures, details,
        expected_operations,
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
    parser.add_argument('--security-port', type=int, default=9201)
    parser.add_argument('--security-username', default='admin')
    parser.add_argument('--security-password-environment')
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
