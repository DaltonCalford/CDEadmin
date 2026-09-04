##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Fail-closed object-experience requirements for provider catalogs."""

from __future__ import annotations

import copy


COVERAGE_SCHEMA = 'cdeadmin.provider-object-coverage.v1'
FINAL_STATES = frozenset({'supported', 'read_only', 'not_applicable'})


def _concept(title, *resource_kinds):
    return {'title': title, 'resource_kinds': resource_kinds}


EXPERIENCE_REQUIREMENTS = {
    'relational': {
        'servers': _concept('Servers', 'server', 'deployment', 'dbms'),
        'databases': _concept(
            'Databases', 'database', 'attached-database'),
        'schemas': _concept('Schemas', 'schema', 'sql-schema'),
        'tables': _concept('Tables', 'table'),
        'views': _concept('Views', 'view'),
        'materialized_views': _concept(
            'Materialized views', 'materialized-view'),
        'columns': _concept('Columns', 'column', 'field'),
        'domains': _concept('Domains', 'domain'),
        'types': _concept('Types', 'type', 'user-defined-type'),
        'sequences': _concept('Sequences', 'sequence'),
        'functions': _concept('Functions', 'function', 'macro'),
        'procedures': _concept('Procedures', 'procedure', 'package'),
        'triggers': _concept('Triggers', 'trigger', 'event-trigger'),
        'indexes': _concept('Indexes', 'index'),
        'constraints': _concept('Constraints', 'constraint'),
        'roles_and_grants': _concept(
            'Roles and grants', 'role', 'privilege', 'grant'),
        'extensions_and_plugins': _concept(
            'Extensions and plugins', 'extension', 'plugin', 'module'),
        'partitions': _concept('Partitions', 'partition'),
        'tablespaces_and_filespaces': _concept(
            'Tablespaces and filespaces', 'tablespace', 'filespace'),
        'replication_objects': _concept(
            'Replication objects', 'publication', 'subscription',
            'replication-channel', 'changefeed', 'replica'),
        'jobs_and_events': _concept(
            'Jobs and events', 'job', 'event', 'schedule', 'scheduler'),
    },
    'document': {
        'databases': _concept('Databases', 'database'),
        'collections': _concept('Collections', 'collection'),
        'documents': _concept('Documents', 'document', 'entity'),
        'validation_rules': _concept(
            'Validation rules', 'validator', 'validation-rule'),
        'indexes': _concept('Indexes', 'index'),
        'views': _concept('Views', 'view'),
        'aggregation_pipelines': _concept(
            'Aggregation pipelines', 'aggregation-pipeline'),
        'users_and_roles': _concept(
            'Users and roles', 'user', 'role', 'privilege'),
        'replica_sets_and_sharding': _concept(
            'Replica sets and sharding', 'replica-set', 'shard', 'router'),
    },
    'graph': {
        'databases': _concept('Databases', 'database'),
        'nodes': _concept('Nodes', 'node', 'graph-node'),
        'relationships': _concept(
            'Relationships', 'relationship', 'graph-edge'),
        'labels': _concept('Labels', 'label', 'node-label'),
        'constraints': _concept('Constraints', 'constraint'),
        'indexes': _concept('Indexes', 'index'),
        'procedures': _concept('Procedures', 'procedure'),
        'graph_projections': _concept(
            'Graph projections', 'graph-projection'),
        'transactions': _concept('Transactions', 'transaction'),
        'query_plans': _concept('Query plans', 'query-plan'),
        'cluster_members': _concept(
            'Cluster members', 'cluster-member', 'member', 'server', 'node'),
    },
    'key_value': {
        'key_browsing': _concept('Key browsing', 'key', 'raw-key'),
        'data_type_editing': _concept(
            'Data-type-aware editing', 'string', 'hash', 'list', 'set',
            'sorted-set', 'bitmap', 'hyperloglog', 'geospatial',
            'vector-set', 'raw-key'),
        'ttl_inspection': _concept('TTL inspection', 'ttl'),
        'expiration_management': _concept(
            'Expiration management', 'expiration', 'ttl'),
        'streams': _concept('Streams', 'stream'),
        'pubsub': _concept('Pub/Sub', 'pubsub-channel'),
        'consumer_groups': _concept('Consumer groups', 'consumer-group'),
        'modules': _concept('Modules', 'module'),
        'acls': _concept('ACLs', 'acl-user', 'role', 'privilege'),
        'replication': _concept('Replication', 'replica', 'replication'),
        'sentinel_or_cluster_state': _concept(
            'Sentinel/cluster state', 'sentinel', 'cluster-slot', 'cluster'),
    },
    'search': {
        'indices': _concept('Indices', 'index', 'search-index'),
        'mappings': _concept('Mappings', 'mapping'),
        'settings': _concept('Settings', 'settings', 'configuration'),
        'aliases': _concept('Aliases', 'alias'),
        'templates': _concept(
            'Templates', 'index-template', 'component-template'),
        'pipelines': _concept('Pipelines', 'ingest-pipeline'),
        'shards_and_replicas': _concept(
            'Shards and replicas', 'shard', 'replica'),
        'reindex_operations': _concept(
            'Reindex operations', 'reindex-operation'),
        'snapshots': _concept('Snapshots', 'snapshot'),
        'ingest_processors': _concept(
            'Ingest processors', 'ingest-processor'),
        'query_profiling': _concept(
            'Query profiling', 'query-profile', 'profiling'),
    },
    'columnar': {
        'native_relations': _concept(
            'Columnar relations', 'table', 'view', 'materialized-view'),
        'projections': _concept('Projections', 'projection'),
        'dictionaries': _concept('Dictionaries', 'dictionary'),
        'data_skipping_indexes': _concept(
            'Data-skipping indexes', 'data-skipping-index'),
        'partitions': _concept('Partitions', 'partition'),
    },
    'time_series': {
        'measurements_or_tables': _concept(
            'Measurements or native tables', 'measurement', 'table'),
        'tags': _concept('Tags', 'tag'),
        'fields': _concept('Fields', 'field'),
        'retention': _concept('Retention', 'retention-policy'),
        'processing': _concept(
            'Processing', 'processing-engine', 'trigger', 'task'),
        'caches': _concept('Caches', 'last-cache', 'distinct-cache'),
    },
    'vector': {
        'collections': _concept('Vector collections', 'collection'),
        'fields': _concept('Vector fields', 'field'),
        'indexes': _concept('Vector indexes', 'vector-index'),
        'partitions': _concept('Partitions', 'partition'),
        'load_state': _concept('Load state', 'load-state'),
        'resource_groups': _concept('Resource groups', 'resource-group'),
    },
    'wide_column': {
        'keyspaces': _concept('Keyspaces', 'keyspace'),
        'tables': _concept('Wide-column tables', 'table'),
        'columns': _concept('Columns', 'column'),
        'types': _concept('Types', 'type', 'user-defined-type'),
        'materialized_views': _concept(
            'Materialized views', 'materialized-view'),
        'replication_and_compaction': _concept(
            'Replication and compaction', 'replication', 'compaction'),
    },
    'semantic': {
        'cubes': _concept('Cubes', 'cube'),
        'dimensions': _concept('Dimensions', 'dimension'),
        'hierarchies': _concept('Hierarchies', 'hierarchy'),
        'levels': _concept('Levels', 'level'),
        'measures': _concept('Measures', 'metric', 'measure'),
        'materializations': _concept(
            'Materializations', 'materialization'),
    },
    'bitemporal': {
        'entities': _concept('Bitemporal entities', 'entity'),
        'valid_time_history': _concept(
            'Valid-time history', 'valid-time'),
        'system_time_history': _concept(
            'System-time history', 'system-time'),
        'transactions': _concept('Transactions', 'transaction'),
        'transaction_log': _concept(
            'Transaction log', 'transaction-log'),
    },
}


ENGINE_EXPERIENCE_FAMILIES = {
    'apache_ignite': ('relational', 'key_value'),
    'cassandra': ('wide_column',),
    'clickhouse': ('columnar', 'semantic'),
    'cockroachdb': ('relational',),
    'dolt': ('relational',),
    'duckdb': ('relational', 'columnar', 'semantic'),
    'firebird': ('relational',),
    'foundationdb': ('key_value',),
    'immudb': ('relational', 'key_value', 'document'),
    'influxdb': ('time_series', 'semantic'),
    'mariadb': ('relational',),
    'milvus': ('vector',),
    'mongodb': ('document',),
    'mysql': ('relational',),
    'neo4j': ('graph',),
    'opensearch': ('search',),
    'opensearch_sql_ppl': ('search',),
    'postgresql': ('relational',),
    'redis': ('key_value',),
    'scratchbird': (
        'relational', 'document', 'graph', 'key_value', 'search',
        'columnar', 'time_series', 'vector', 'wide_column', 'semantic',
    ),
    'sqlite': ('relational',),
    'tidb': ('relational',),
    'tikv': ('key_value',),
    'vitess': ('relational',),
    'xtdb': ('document', 'relational', 'bitemporal'),
    # This catalog is the YSQL profile. YCQL is a separately registered
    # provider and must not borrow object claims from the PostgreSQL wire.
    'yugabytedb': ('relational',),
}


def concept_coverage_for_engine(engine):
    """Return declaration evidence without inferring provider support."""
    engine_id = engine['engine_id']
    resource_kinds = {item['resource_kind'] for item in engine['objects']}
    declarations = engine.get('concept_declarations', {})
    families = []
    undeclared_count = 0
    missing_catalog_count = 0
    blocking_missing_count = 0
    live_evidence_missing_count = 0
    live_operation_missing_count = 0
    family_ids = engine.get(
        'experience_families', ENGINE_EXPERIENCE_FAMILIES[engine_id]
    )
    if (
        not isinstance(family_ids, (list, tuple)) or not family_ids or
        any(item not in EXPERIENCE_REQUIREMENTS for item in family_ids)
    ):
        family_ids = ENGINE_EXPERIENCE_FAMILIES[engine_id]
    for family_id in family_ids:
        concepts = []
        family_declarations = declarations.get(family_id, {})
        for concept_id, requirement in EXPERIENCE_REQUIREMENTS[
                family_id].items():
            declaration = family_declarations.get(concept_id)
            if isinstance(declaration, str):
                declared_status = declaration
                declared_kinds = ()
                reason = None
                evidence = []
                external_surface = None
                operation_obligations = {}
                live_operations = {}
            elif isinstance(declaration, dict):
                declared_status = declaration.get('status')
                declared_kinds = declaration.get('resource_kinds', ())
                if not isinstance(declared_kinds, (list, tuple)) or any(
                        not isinstance(item, str) or not item
                        for item in declared_kinds):
                    declared_kinds = ()
                    declared_status = None
                reason = declaration.get('reason')
                evidence = declaration.get('evidence', [])
                external_surface = declaration.get('external_surface')
                operation_obligations = declaration.get(
                    'operation_obligations', {})
                live_operations = declaration.get('live_operations', {})
                if not isinstance(reason, (str, type(None))):
                    reason = None
                    declared_status = None
                if not isinstance(evidence, list) or any(
                        not isinstance(item, str) or not item
                        for item in evidence):
                    evidence = []
                    declared_status = None
                if not isinstance(external_surface, (str, type(None))):
                    external_surface = None
                    declared_status = None
                if not isinstance(operation_obligations, dict) or any(
                        not isinstance(kind, str) or not kind or
                        not isinstance(operations, list) or
                        any(not isinstance(item, str) or not item
                            for item in operations)
                        for kind, operations in
                        operation_obligations.items()):
                    operation_obligations = {}
                    declared_status = None
                if not isinstance(live_operations, dict) or any(
                        not isinstance(kind, str) or not kind or
                        not isinstance(operations, list) or
                        any(not isinstance(item, str) or not item
                            for item in operations)
                        for kind, operations in live_operations.items()):
                    live_operations = {}
                    declared_status = None
            else:
                declared_status = None
                declared_kinds = ()
                reason = None
                evidence = []
                external_surface = None
                operation_obligations = {}
                live_operations = {}
            candidates = set(requirement['resource_kinds']).union(
                declared_kinds
            )
            matches = sorted(resource_kinds.intersection(candidates))
            if declared_status not in FINAL_STATES:
                declared_status = None
                undeclared_count += 1
            if not matches and external_surface is None:
                missing_catalog_count += 1
                if declared_status != 'not_applicable':
                    blocking_missing_count += 1
            live_evidence = any(
                item.startswith('live:') for item in evidence
            )
            if declared_status in {'supported', 'read_only'} and not (
                    live_evidence):
                live_evidence_missing_count += 1
            missing_live_operations = {
                kind: sorted(set(operations).difference(
                    live_operations.get(kind, [])
                ))
                for kind, operations in operation_obligations.items()
                if set(operations).difference(live_operations.get(kind, []))
            }
            live_operation_missing_count += sum(
                len(operations)
                for operations in missing_live_operations.values()
            )
            concepts.append({
                'concept_id': concept_id,
                'title': requirement['title'],
                'candidate_resource_kinds': list(
                    requirement['resource_kinds']),
                'catalog_resource_kinds': matches,
                'catalog_state': (
                    'catalogued' if matches else
                    'external_surface' if external_surface else 'missing'
                ),
                'declared_status': declared_status,
                'activation_state': declared_status or 'undeclared',
                'declaration_reason': reason,
                'evidence': list(evidence),
                'external_surface': external_surface,
                'live_evidence': live_evidence,
                'operation_obligations': copy.deepcopy(
                    operation_obligations),
                'live_operations': copy.deepcopy(live_operations),
                'missing_live_operations': missing_live_operations,
            })
        families.append({
            'family_id': family_id,
            'concepts': concepts,
        })
    return {
        'schema': COVERAGE_SCHEMA,
        'families': families,
        'undeclared_count': undeclared_count,
        'missing_catalog_count': missing_catalog_count,
        'blocking_missing_count': blocking_missing_count,
        'live_evidence_missing_count': live_evidence_missing_count,
        'live_operation_missing_count': live_operation_missing_count,
        'declaration_ready': undeclared_count == 0 and
        blocking_missing_count == 0,
        'activation_ready': undeclared_count == 0 and
        blocking_missing_count == 0 and
        live_evidence_missing_count == 0 and
        live_operation_missing_count == 0,
        'support_inferred_from_catalog': False,
    }


__all__ = (
    'COVERAGE_SCHEMA', 'ENGINE_EXPERIENCE_FAMILIES',
    'EXPERIENCE_REQUIREMENTS', 'FINAL_STATES',
    'concept_coverage_for_engine',
)
