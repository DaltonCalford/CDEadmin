##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Authoritative, owner-scoped project and asset persistence.

Live database objects remain ResourceRefs. This service stores only authored
project assets and their explicit ResourceRef bindings. Every update uses an
optimistic version and writes an immutable saved revision in the same
transaction.
"""

from __future__ import annotations

import json
import math
import re
import uuid
from datetime import datetime


APP_EXTENSION_KEY = 'cdeadmin_project_asset_service'
SAFE_ID = re.compile(r'^[a-zA-Z0-9][a-zA-Z0-9._:-]{0,255}$')
SAFE_ASSET_TYPE = re.compile(r'^[a-z][a-z0-9_.-]{0,63}$')
SAFE_PATH = re.compile(r'^[^\\\x00-\x1f]{1,1024}$')
SECRET_KEY = re.compile(
    r'(?:password|passwd|secret|private.?key|access.?token|refresh.?token)',
    re.IGNORECASE,
)
ACCESS = {'viewer': 1, 'editor': 2, 'manager': 3, 'owner': 4}
VALIDATION_STATES = frozenset({'unknown', 'valid', 'warning', 'invalid'})
MAX_ASSET_BYTES = 64 * 1024 * 1024
MAX_METADATA_BYTES = 256 * 1024
MAX_DDN_FILES = 1024
MAX_DDN_FILE_BYTES = 8 * 1024 * 1024
SCHEMA_COMPARE_ASSET_TYPE = 'cdeadmin.schema_compare.v1'
SCHEMA_COMPARE_SCHEMA = 'cdeadmin.schema-compare.asset.v1'
SCHEMA_COMPARE_DIFF_STATES = frozenset({
    'identical', 'semantically_equivalent', 'changed', 'left_only',
    'right_only', 'rename_candidate', 'moved', 'incompatible',
    'uncomparable', 'unknown',
})
LINEAGE_ASSET_TYPE = 'cdeadmin.lineage.v1'
LINEAGE_SCHEMA = 'cdeadmin.lineage.asset.v1'
LINEAGE_ORIGINS = frozenset({
    'provider_declared', 'project_declared', 'parsed_query',
    'execution_plan', 'etl_declared', 'cdc_declared',
    'migration_declared', 'openlineage_import', 'observed_trace',
    'user_curated', 'inferred',
})
LINEAGE_EDGE_TYPES = frozenset({
    'reads', 'writes', 'derives', 'transforms', 'copies', 'replicates',
    'publishes', 'subscribes', 'exposes', 'materializes', 'triggers',
    'depends_on', 'governs', 'validates', 'structural_reference',
})
LINEAGE_FIELD_TRANSFORMATIONS = frozenset({
    'DIRECT_IDENTITY', 'RENAME', 'CAST', 'EXPRESSION', 'AGGREGATE',
    'WINDOW', 'LOOKUP', 'JOIN_KEY', 'FILTER_ONLY', 'GENERATED',
    'OPAQUE', 'UNKNOWN',
})
LINEAGE_PRESENTATION_STATES = frozenset({
    'confirmed', 'observed', 'declared', 'inferred', 'conflicted', 'stale',
})
QUALITY_ASSET_TYPE = 'cdeadmin.quality.v1'
QUALITY_SCHEMA = 'cdeadmin.quality.asset.v1'
QUALITY_DIMENSIONS = frozenset({
    'accuracy', 'completeness', 'validity', 'consistency', 'uniqueness',
    'timeliness', 'freshness', 'referential_integrity', 'volume',
    'distribution', 'drift', 'custom',
})
QUALITY_RULE_FAMILIES = frozenset({
    'schema_presence', 'type_compatibility', 'not_null',
    'completeness_ratio', 'uniqueness', 'duplicate_ratio',
    'accepted_values', 'regex/pattern', 'range', 'length',
    'referential_integrity', 'cross_resource_match', 'row_count',
    'volume_change', 'freshness', 'timeliness', 'distribution',
    'quantile', 'mean/stddev', 'drift', 'custom_query',
    'custom_expression', 'provider_native',
})
QUALITY_EVALUATION_MODES = frozenset({
    'exact', 'sampled', 'approximate', 'provider_reported',
})
QUALITY_THRESHOLD_OPERATORS = frozenset({
    '=', '!=', '<', '<=', '>', '>=', 'between', 'outside',
})
QUALITY_SEVERITIES = frozenset({'info', 'warning', 'critical'})
QUALITY_SAMPLING_MODES = frozenset({
    'none_exact', 'first_n', 'random_n', 'percentage',
    'provider_native', 'partition', 'time_window',
})
QUALITY_ACTION_TYPES = frozenset({
    'notify', 'block', 'warn', 'open_issue', 'call_command',
})
CONTRACT_ASSET_TYPE = 'cdeadmin.contract.v1'
CONTRACT_SCHEMA = 'cdeadmin.contract.asset.v1'
CONTRACT_STATUSES = frozenset({
    'proposed', 'draft', 'active', 'deprecated', 'retired',
})
ETL_ASSET_TYPE = 'cdeadmin.etl.v1'
ETL_SCHEMA = 'cdeadmin.etl.asset.v1'
ETL_NODE_FAMILIES = frozenset({
    'source', 'sink', 'filter', 'project', 'map', 'join', 'lookup',
    'aggregate', 'sort', 'union', 'split', 'deduplicate', 'window',
    'script', 'quality_gate', 'checkpoint', 'branch', 'merge',
    'custom_provider',
})
ETL_PIPELINE_MODES = frozenset({
    'batch', 'micro_batch', 'streaming', 'hybrid',
})
ETL_PORT_MODES = frozenset({'batch', 'stream', 'either'})
ETL_SCHEMA_STATES = frozenset({
    'known', 'partial', 'unknown', 'incompatible',
})
ETL_EXECUTION_LOCATIONS = frozenset({
    'source_pushdown', 'cdeadmin_runtime', 'target_pushdown',
    'external_runtime',
})
ETL_ERROR_ACTIONS = frozenset({'retry', 'dead_letter', 'reject', 'stop'})
ETL_DELIVERY_GUARANTEES = frozenset({
    'exactly_once', 'at_least_once', 'at_most_once', 'best_effort', 'unknown',
})
CDC_ASSET_TYPE = 'cdeadmin.cdc.v1'
CDC_SCHEMA = 'cdeadmin.cdc.asset.v1'
CDC_CAPTURE_MECHANISMS = frozenset({
    'log_based', 'logical_replication', 'oplog/change_stream',
    'provider_native_stream', 'trigger_based', 'polling', 'mga_history',
    'external_connector',
})
CDC_START_KINDS = frozenset({
    'latest', 'timestamp', 'lsn', 'binlog', 'oplog', 'offset', 'native',
})
CDC_SNAPSHOT_MODES = frozenset({
    'none', 'initial', 'incremental', 'provider_native',
})
CDC_DELIVERY_GUARANTEES = frozenset({
    'at_most_once', 'at_least_once', 'effectively_once_with_dedup',
    'exactly_once_proven', 'provider_specific', 'unknown',
})
CDC_SCHEMA_CHANGE_CLASSES = frozenset({
    'additive_compatible', 'compatible_with_mapping', 'breaking', 'unknown',
})
CDC_EVOLUTION_ACTIONS = frozenset({
    'auto_apply_compatible', 'pause_and_review', 'map_to_existing',
    'dead_letter', 'fail',
})
REPLICATION_ASSET_TYPE = 'cdeadmin.replication.v1'
REPLICATION_SCHEMA = 'cdeadmin.replication.asset.v1'
REPLICATION_ROLES = frozenset({
    'writer_capable', 'read_only_replica', 'peer', 'arbiter/voter',
    'router', 'witness', 'unknown',
})
REPLICATION_HEALTH_STATES = frozenset({
    'healthy', 'degraded', 'unhealthy', 'unknown', 'partial',
})
TRACING_ASSET_TYPE = 'cdeadmin.tracing.v1'
TRACING_SCHEMA = 'cdeadmin.tracing.asset.v1'
TRACING_SOURCE_TYPES = frozenset({
    'otlp_grpc', 'otlp_http', 'cdeadmin_internal', 'provider_native',
    'imported_file',
})
TRACING_VIEW_KINDS = frozenset({
    'waterfall', 'service_resource_map', 'trace_table',
    'query_correlation',
})
MIGRATION_ASSET_TYPE = 'cdeadmin.migration.v1'
MIGRATION_SCHEMA = 'cdeadmin.migration.asset.v1'
MIGRATION_PHASES = frozenset({
    'discover', 'assess', 'design', 'dry_run', 'provision',
    'initial_copy', 'incremental_sync', 'validate', 'cutover_ready',
    'cutover', 'verify', 'complete', 'rollback',
})
MIGRATION_STRATEGIES = frozenset({
    'offline', 'online_with_cdc', 'staged_dual_run',
    'copy_then_cutover', 'schema_only', 'data_only', 'validation_only',
})
MIGRATION_ASSESSMENT_CATEGORIES = frozenset({
    'supported_exact', 'supported_with_mapping',
    'supported_with_behavior_change', 'manual_conversion_required',
    'blocked', 'not_selected', 'unknown',
})
MIGRATION_EVIDENCE_SOURCES = frozenset({
    'mapping_rule', 'provider_adapter', 'executed_test',
    'manual_decision', 'unknown',
})
MIGRATION_VERIFICATION_TYPES = frozenset({
    'object_counts', 'row_document_counts', 'deterministic_hashes',
    'sampled_content', 'query_result', 'data_quality_rules',
    'provider_native',
})
MIGRATION_ROLLBACK_CLASSES = frozenset({
    'fully_automatable', 'partially_automatable', 'manual_runbook',
    'not_available_after_point',
})
API_ASSET_TYPE = 'cdeadmin.api.v1'
API_SCHEMA = 'cdeadmin.api.asset.v1'
API_PROFILES = frozenset({
    'openapi_http', 'asyncapi_event', 'graphql_schema', 'rpc_extension',
})
API_BINDING_TYPES = frozenset({
    'saved_query', 'provider_resource_read', 'provider_command',
    'stored_procedure/function', 'semantic_model', 'pipeline/macro',
    'custom_backend_handler',
})
API_BINDING_MODES = frozenset({
    'read', 'write', 'read_write', 'invoke', 'publish', 'subscribe',
})
API_HTTP_METHODS = frozenset({
    'GET', 'HEAD', 'OPTIONS', 'TRACE', 'POST', 'PUT', 'PATCH', 'DELETE',
})


class ProjectAssetError(RuntimeError):
    status_code = 400


class ProjectAssetNotFound(ProjectAssetError):
    status_code = 404


class ProjectAssetForbidden(ProjectAssetError):
    status_code = 403


class ProjectAssetConflict(ProjectAssetError):
    status_code = 409
    code = 'asset_conflict'


def _safe_id(value, label):
    value = '' if value is None else str(value).strip()
    if not SAFE_ID.fullmatch(value):
        raise ProjectAssetError(f'{label} must be a stable opaque identifier')
    return value


def _text(value, label, maximum, *, empty=False):
    value = '' if value is None else str(value).strip()
    if (not value and not empty) or len(value) > maximum:
        raise ProjectAssetError(f'{label} is invalid')
    return value


def _json(value, label, maximum=MAX_METADATA_BYTES):
    try:
        encoded = json.dumps(
            value, ensure_ascii=False, sort_keys=True,
            separators=(',', ':'),
        )
    except (TypeError, ValueError) as exc:
        raise ProjectAssetError(f'{label} must be JSON serializable') from exc
    if len(encoded.encode('utf-8')) > maximum:
        raise ProjectAssetError(f'{label} exceeds its size limit')
    return encoded


def _secret_free(value, path='metadata'):
    if isinstance(value, list):
        for index, child in enumerate(value):
            _secret_free(child, f'{path}[{index}]')
    elif isinstance(value, dict):
        for key, child in value.items():
            if SECRET_KEY.search(str(key)):
                if key in ('secret', 'isSecret') and isinstance(child, bool):
                    continue
                raise ProjectAssetError(
                    f'secret field is forbidden in project assets: '
                    f'{path}.{key}'
                )
            _secret_free(child, f'{path}.{key}')


def _path(value):
    value = str(value or '').strip().replace('\\', '/')
    if not SAFE_PATH.fullmatch(value) or value.startswith('/') or any(
        part in ('', '.', '..') for part in value.split('/')
    ):
        raise ProjectAssetError('asset path must be a safe relative path')
    return value


def _ddn_snapshot(value):
    if not isinstance(value, dict):
        raise ProjectAssetError('DDN snapshot must be an object')
    if value.get('format') not in (
        'ddn-workspace@1', 'ddn-live-snapshot@0.1'
    ):
        raise ProjectAssetError('DDN snapshot format is unsupported')
    files = value.get('files')
    if not isinstance(files, dict) or not 1 <= len(files) <= MAX_DDN_FILES:
        raise ProjectAssetError('DDN source file count is invalid')
    total = 0
    for name, source in files.items():
        if not isinstance(name, str) or not name.endswith('.ddn'):
            raise ProjectAssetError('DDN source path is invalid')
        _path(name)
        if not isinstance(source, str):
            raise ProjectAssetError('DDN source must be text')
        size = len(source.encode('utf-8'))
        if size > MAX_DDN_FILE_BYTES:
            raise ProjectAssetError('DDN source file exceeds its size limit')
        total += size
    if total > MAX_ASSET_BYTES:
        raise ProjectAssetError('DDN workspace exceeds its size limit')
    if not isinstance(value.get('entry'), str) or not isinstance(
            value.get('view'), str):
        raise ProjectAssetError('DDN snapshot requires entry and view')
    return value


def _schema_compare_reference(value, label, *, optional=False):
    if value is None and optional:
        return None
    if not isinstance(value, dict):
        raise ProjectAssetError(f'{label} must be a reference object')
    if value.get('schema') == 'cdeadmin.resource-ref.v1':
        for field in (
            'provider', 'connection', 'scope', 'kind', 'nativeIdentity',
            'canonical',
        ):
            _text(value.get(field), f'{label} {field}', 4096)
        return value
    if value.get('schema') == 'cdeadmin.schema-compare.snapshot-ref.v1':
        _text(value.get('snapshotId'), f'{label} snapshot ID', 1024)
        return value
    if value.get('schemaVersion') == 1:
        for field in (
            'projectId', 'assetId', 'assetType', 'path', 'displayName',
        ):
            _text(value.get(field), f'{label} {field}', 1024)
        version = value.get('assetVersion')
        if (isinstance(version, bool) or not isinstance(version, int) or
                version < 0):
            raise ProjectAssetError(f'{label} asset version is invalid')
        return value
    raise ProjectAssetError(f'{label} reference type is unsupported')


def _schema_compare_plan(value):
    if value is None:
        return None
    if not isinstance(value, dict) or value.get('schema') != (
            'cdeadmin.schema-compare.change-plan.v1'):
        raise ProjectAssetError('Schema Comparison change plan is invalid')
    if value.get('targetSide') not in ('left', 'right'):
        raise ProjectAssetError(
            'Schema Comparison change plan requires an explicit target side'
        )
    _schema_compare_reference(value.get('targetRef'), 'change-plan target')
    operations = value.get('operations')
    if not isinstance(operations, list):
        raise ProjectAssetError(
            'Schema Comparison change-plan operations must be a list'
        )
    seen = set()
    for operation in operations:
        if not isinstance(operation, dict):
            raise ProjectAssetError(
                'Schema Comparison change-plan operation must be an object'
            )
        operation_id = _text(
            operation.get('id'), 'change-plan operation ID', 4096
        )
        if operation_id in seen:
            raise ProjectAssetError(
                'Schema Comparison change-plan operation IDs must be unique'
            )
        seen.add(operation_id)
        if operation.get('action') not in ('create', 'alter', 'move', 'drop'):
            raise ProjectAssetError(
                'Schema Comparison change-plan operation action is invalid'
            )
        if operation.get('risk') not in ('low', 'medium', 'high'):
            raise ProjectAssetError(
                'Schema Comparison change-plan operation risk is invalid'
            )
        if not isinstance(operation.get('dependencies', []), list):
            raise ProjectAssetError(
                'Schema Comparison operation dependencies must be a list'
            )
        _text(
            operation.get('nativeStatement'),
            'provider-native change statement', MAX_ASSET_BYTES,
        )
    for operation in operations:
        if any(dependency not in seen for dependency in
               operation.get('dependencies', [])):
            raise ProjectAssetError(
                'Schema Comparison operation dependency is missing'
            )
    return value


def _schema_compare_content(value):
    if not isinstance(value, dict) or value.get('schema') != (
            SCHEMA_COMPARE_SCHEMA):
        raise ProjectAssetError('Schema Comparison asset schema is invalid')
    if value.get('schemaVersion') != 1 or value.get('moduleId') != (
            'cdeadmin.schema_compare'):
        raise ProjectAssetError(
            'Schema Comparison asset version or module is invalid'
        )
    _schema_compare_reference(
        value.get('leftRef'), 'left source', optional=True
    )
    _schema_compare_reference(
        value.get('rightRef'), 'right source', optional=True
    )
    if not isinstance(value.get('options', {}), dict):
        raise ProjectAssetError('Schema Comparison options must be an object')
    if not isinstance(value.get('ignoredDiffs', []), list):
        raise ProjectAssetError(
            'Schema Comparison ignored differences must be a list'
        )
    mappings = value.get('acceptedMappings', [])
    if not isinstance(mappings, list):
        raise ProjectAssetError(
            'Schema Comparison accepted mappings must be a list'
        )
    for mapping in mappings:
        if not isinstance(mapping, dict) or not mapping.get('accepted'):
            raise ProjectAssetError(
                'Schema Comparison persisted mappings must be accepted'
            )
        for field in ('mappingId', 'leftId', 'rightId', 'category'):
            _text(mapping.get(field), f'mapping {field}', 4096)
        if mapping.get('category') not in (
            'exact', 'representational_difference',
            'compatible_widening', 'compatible_with_default_change',
            'lossy', 'behavioral_difference', 'unsupported_mapping',
        ):
            raise ProjectAssetError(
                'Schema Comparison mapping category is invalid'
            )
    _schema_compare_plan(value.get('changePlan'))
    _secret_free(value, 'Schema Comparison asset content')
    return value


def _lineage_reference(value, label):
    if not isinstance(value, dict):
        raise ProjectAssetError(f'{label} must be a reference object')
    schema = value.get('schema')
    if schema == 'cdeadmin.resource-ref.v1':
        _text(value.get('canonical'), f'{label} canonical identity', 4096)
    elif schema == 'cdeadmin.asset-ref.v1':
        _text(value.get('projectId'), f'{label} project ID', 1024)
        _text(value.get('assetId'), f'{label} asset ID', 1024)
    elif schema in (
            'cdeadmin.job-ref.v1', 'cdeadmin.external-ref.v1'):
        _text(value.get('id'), f'{label} identity', 4096)
    else:
        raise ProjectAssetError(f'{label} reference type is unsupported')
    return value


def _lineage_time(value, label):
    if value is None:
        return
    text = _text(value, label, 64)
    try:
        datetime.fromisoformat(text.replace('Z', '+00:00'))
    except ValueError as error:
        raise ProjectAssetError(f'{label} must be an ISO timestamp') from error


def _lineage_interval(value, label):
    _lineage_time(value.get('validFrom'), f'{label} validFrom')
    _lineage_time(value.get('validTo'), f'{label} validTo')
    if value.get('validFrom') and value.get('validTo'):
        start = datetime.fromisoformat(
            value['validFrom'].replace('Z', '+00:00')
        )
        end = datetime.fromisoformat(value['validTo'].replace('Z', '+00:00'))
        if start.timestamp() > end.timestamp():
            raise ProjectAssetError(
                f'{label} validFrom must not be after validTo'
            )


def _lineage_confidence(value, label):
    if (isinstance(value, bool) or not isinstance(value, (int, float)) or
            not 0 <= value <= 1):
        raise ProjectAssetError(f'{label} is invalid')


def _lineage_evidence(value):
    if not isinstance(value, dict) or value.get('schema') != (
            'cdeadmin.lineage-evidence.v1'):
        raise ProjectAssetError('Lineage evidence schema is invalid')
    _text(value.get('id'), 'Lineage evidence ID', 1024)
    if value.get('origin') not in LINEAGE_ORIGINS:
        raise ProjectAssetError('Lineage evidence origin is invalid')
    _lineage_confidence(value.get('confidence'), 'Lineage evidence confidence')
    _lineage_time(value.get('capturedAt'), 'Lineage evidence capturedAt')
    _lineage_interval(value, 'Lineage evidence')
    if not isinstance(value.get('stale'), bool):
        raise ProjectAssetError('Lineage evidence stale must be boolean')
    if value.get('providerVersion') is not None:
        _text(
            value['providerVersion'],
            'Lineage evidence provider version', 1024
        )
    if not isinstance(value.get('details'), dict):
        raise ProjectAssetError('Lineage evidence details must be an object')
    if value.get('reference') is not None:
        _lineage_reference(value['reference'], 'Lineage evidence')


def _lineage_edge(value):
    if not isinstance(value, dict) or value.get('schema') != (
            'cdeadmin.lineage-edge.v1'):
        raise ProjectAssetError('Curated Lineage edge schema is invalid')
    for field in ('id', 'from', 'to'):
        _text(value.get(field), f'Lineage edge {field}', 4096)
    if value.get('type') not in LINEAGE_EDGE_TYPES:
        raise ProjectAssetError('Lineage edge type is invalid')
    if value.get('origin') not in LINEAGE_ORIGINS:
        raise ProjectAssetError('Lineage edge origin is invalid')
    _lineage_confidence(value.get('confidence'), 'Lineage edge confidence')
    _lineage_interval(value, 'Lineage edge')
    state = value.get('presentationState')
    if state is not None and state not in LINEAGE_PRESENTATION_STATES:
        raise ProjectAssetError('Lineage presentation state is invalid')
    if not isinstance(value.get('suppressed'), bool):
        raise ProjectAssetError('Lineage edge suppressed must be boolean')
    if not isinstance(value.get('nativeDetails'), dict):
        raise ProjectAssetError('Lineage edge nativeDetails must be an object')
    evidence = value.get('evidence')
    if not isinstance(evidence, list) or not evidence:
        raise ProjectAssetError('Lineage edge requires evidence')
    evidence_ids = set()
    for item in evidence:
        _lineage_evidence(item)
        if item['id'] in evidence_ids:
            raise ProjectAssetError(
                'Lineage evidence IDs must be unique within an edge'
            )
        evidence_ids.add(item['id'])
    fields = value.get('fieldLineage', [])
    if not isinstance(fields, list):
        raise ProjectAssetError('Field lineage must be a list')
    field_ids = set()
    for field in fields:
        if not isinstance(field, dict) or field.get('schema') != (
                'cdeadmin.field-lineage.v1'):
            raise ProjectAssetError('Field lineage schema is invalid')
        for name in ('id', 'sourceField'):
            _text(field.get(name), f'Field lineage {name}', 4096)
        if field['id'] in field_ids:
            raise ProjectAssetError(
                'Field Lineage IDs must be unique within an edge'
            )
        field_ids.add(field['id'])
        if field.get('targetField') is not None:
            _text(field['targetField'], 'Field lineage target', 4096)
        if field.get('transformation') not in (
                LINEAGE_FIELD_TRANSFORMATIONS):
            raise ProjectAssetError('Field transformation is invalid')
        if not isinstance(field.get('evidenceIds'), list):
            raise ProjectAssetError('Field evidenceIds must be a list')
        for evidence_id in field['evidenceIds']:
            _text(evidence_id, 'Field evidence ID', 1024)
            if evidence_id not in evidence_ids:
                raise ProjectAssetError(
                    'Field evidence ID does not reference edge evidence'
                )
        if not isinstance(field.get('nativeDetails'), dict):
            raise ProjectAssetError('Field nativeDetails must be an object')


def _lineage_content(value):
    if not isinstance(value, dict) or value.get('schema') != LINEAGE_SCHEMA:
        raise ProjectAssetError('Data Lineage asset schema is invalid')
    if value.get('schemaVersion') != 1 or value.get('moduleId') != (
            'cdeadmin.lineage'):
        raise ProjectAssetError('Data Lineage asset version is invalid')
    scopes = value.get('scopeRefs')
    if not isinstance(scopes, list):
        raise ProjectAssetError('Data Lineage scopeRefs must be a list')
    for reference in scopes:
        _lineage_reference(reference, 'Data Lineage scope')
    policies = value.get('sourcePolicies')
    if not isinstance(policies, list):
        raise ProjectAssetError('Lineage sourcePolicies must be a list')
    policy_ids = set()
    for policy in policies:
        if not isinstance(policy, dict):
            raise ProjectAssetError('Lineage source policy must be an object')
        policy_id = _text(
            policy.get('id'), 'Lineage source policy ID', 1024
        )
        if policy_id in policy_ids:
            raise ProjectAssetError('Lineage source policy IDs must be unique')
        policy_ids.add(policy_id)
        if not isinstance(policy.get('sourcePriority'), list) or any(
                origin not in LINEAGE_ORIGINS
                for origin in policy.get('sourcePriority')):
            raise ProjectAssetError('Lineage source priority is invalid')
        if not isinstance(policy.get('inferenceEnabled'), bool):
            raise ProjectAssetError(
                'Lineage inferenceEnabled must be boolean'
            )
        retention = policy.get('retentionDays')
        if (isinstance(retention, bool) or not isinstance(
                retention, (int, float)) or retention < 0):
            raise ProjectAssetError('Lineage retentionDays is invalid')
        if policy.get('providerId') is not None:
            _text(policy['providerId'], 'Lineage provider ID', 1024)
    if not isinstance(value.get('savedFilters'), dict):
        raise ProjectAssetError('Lineage savedFilters must be an object')
    if value.get('layoutPreferences') is not None and not isinstance(
            value.get('layoutPreferences'), dict):
        raise ProjectAssetError('Lineage layoutPreferences must be an object')
    edges = value.get('curatedEdges')
    if not isinstance(edges, list):
        raise ProjectAssetError('Lineage curatedEdges must be a list')
    seen = set()
    for edge in edges:
        _lineage_edge(edge)
        if edge['id'] in seen:
            raise ProjectAssetError('Lineage edge IDs must be unique')
        seen.add(edge['id'])
    rules = value.get('suppressedInferenceRules')
    if not isinstance(rules, list):
        raise ProjectAssetError(
            'Lineage suppressedInferenceRules must be a list'
        )
    for rule in rules:
        _text(rule, 'Lineage suppression rule', 8192)
    snapshots = value.get('snapshotRefs')
    if not isinstance(snapshots, list):
        raise ProjectAssetError('Lineage snapshotRefs must be a list')
    for reference in snapshots:
        _lineage_reference(reference, 'Lineage snapshot')
    _secret_free(value, 'Data Lineage asset content')
    return value


def _quality_reference(value, label):
    if not isinstance(value, dict):
        raise ProjectAssetError(f'{label} must be a reference object')
    schema = value.get('schema')
    if schema == 'cdeadmin.resource-ref.v1':
        _text(value.get('canonical'), f'{label} canonical identity', 4096)
    elif schema == 'cdeadmin.asset-ref.v1':
        _text(value.get('projectId'), f'{label} project ID', 1024)
        _text(value.get('assetId'), f'{label} asset ID', 1024)
    elif schema in (
            'cdeadmin.task-ref.v1', 'cdeadmin.result-ref.v1',
            'cdeadmin.external-ref.v1'):
        _text(value.get('id'), f'{label} identity', 4096)
    else:
        raise ProjectAssetError(f'{label} reference type is unsupported')


def _quality_number(value, label, minimum=None, maximum=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ProjectAssetError(f'{label} must be numeric')
    if not math.isfinite(value):
        raise ProjectAssetError(f'{label} must be finite')
    if minimum is not None and value < minimum:
        raise ProjectAssetError(f'{label} is below its minimum')
    if maximum is not None and value > maximum:
        raise ProjectAssetError(f'{label} exceeds its maximum')


def _quality_sampling(value):
    if not isinstance(value, dict):
        raise ProjectAssetError('Quality samplePolicy must be an object')
    mode = value.get('mode')
    if mode not in QUALITY_SAMPLING_MODES:
        raise ProjectAssetError('Quality sampling mode is invalid')
    if mode in ('first_n', 'random_n'):
        limit = value.get('limit')
        if isinstance(limit, bool) or not isinstance(limit, int):
            raise ProjectAssetError(
                'Quality sampling limit must be an integer'
            )
        _quality_number(limit, 'Quality sampling limit', 1, 1000000)
    if mode == 'percentage':
        _quality_number(
            value.get('percentage'), 'Quality sampling percentage',
            0.000001, 1,
        )
    if mode == 'partition':
        _text(value.get('partition'), 'Quality sampling partition', 4096)
    if mode == 'time_window' and not isinstance(value.get('window'), dict):
        raise ProjectAssetError('Quality sampling window must be an object')
    if value.get('nativeDetails') is not None and not isinstance(
            value.get('nativeDetails'), dict):
        raise ProjectAssetError(
            'Quality sampling nativeDetails must be an object'
        )


def _quality_slice(value):
    if not isinstance(value, dict) or value.get('schema') != (
            'cdeadmin.quality-data-slice.v1'):
        raise ProjectAssetError('Quality data slice schema is invalid')
    _text(value.get('id'), 'Quality data slice ID', 1024)
    _quality_reference(value.get('resourceRef'), 'Quality data slice resource')
    if value.get('partition') is not None:
        _text(value['partition'], 'Quality data slice partition', 4096)
    for field in ('window', 'filter'):
        if value.get(field) is not None and not isinstance(
                value.get(field), dict):
            raise ProjectAssetError(
                f'Quality data slice {field} must be an object'
            )
    _quality_sampling(value.get('samplePolicy'))
    if not isinstance(value.get('nativeDetails'), dict):
        raise ProjectAssetError(
            'Quality data slice nativeDetails must be an object'
        )


def _quality_threshold(value, ratio=False):
    if value is None:
        return
    if not isinstance(value, dict) or value.get('operator') not in (
            QUALITY_THRESHOLD_OPERATORS):
        raise ProjectAssetError('Quality threshold is invalid')
    minimum = 0 if ratio else None
    maximum = 1 if ratio else None
    if value['operator'] in ('between', 'outside'):
        _quality_number(
            value.get('lower'), 'Quality threshold lower', minimum, maximum
        )
        _quality_number(
            value.get('upper'), 'Quality threshold upper', minimum, maximum
        )
        if value['lower'] > value['upper']:
            raise ProjectAssetError(
                'Quality threshold lower must not exceed upper'
            )
    else:
        _quality_number(
            value.get('value'), 'Quality threshold value', minimum, maximum
        )


def _quality_rule(value):
    if not isinstance(value, dict) or value.get('schema') != (
            'cdeadmin.quality-rule.v1'):
        raise ProjectAssetError('Quality rule schema is invalid')
    for field in ('id', 'name'):
        _text(value.get(field), f'Quality rule {field}', 1024)
    if value.get('type') not in QUALITY_RULE_FAMILIES:
        raise ProjectAssetError('Quality rule family is invalid')
    if value.get('dimension') not in QUALITY_DIMENSIONS:
        raise ProjectAssetError('Quality rule dimension is invalid')
    if value.get('severity') not in QUALITY_SEVERITIES:
        raise ProjectAssetError('Quality rule severity is invalid')
    if value.get('evaluationMode') not in QUALITY_EVALUATION_MODES:
        raise ProjectAssetError('Quality rule evaluation mode is invalid')
    for field in ('ratio', 'enabled', 'generatedSuggestion'):
        if not isinstance(value.get(field), bool):
            raise ProjectAssetError(f'Quality rule {field} must be boolean')
    if value.get('scope') is not None:
        _quality_slice(value['scope'])
    if not isinstance(value.get('parameters'), dict):
        raise ProjectAssetError('Quality rule parameters must be an object')
    _quality_threshold(value.get('threshold'), value.get('ratio'))
    if value.get('sourceSampleRef') is not None:
        _quality_reference(
            value['sourceSampleRef'], 'Quality suggestion source sample'
        )
    if value.get('sourceRevision') is not None:
        _text(
            value['sourceRevision'], 'Quality suggestion source revision',
            4096,
        )
    if value.get('generatedSuggestion') and (
            value.get('sourceSampleRef') is None or
            value.get('sourceRevision') is None):
        raise ProjectAssetError(
            'Generated quality suggestions require source evidence'
        )
    if not isinstance(value.get('actionIds'), list):
        raise ProjectAssetError('Quality rule actionIds must be a list')
    for action_id in value['actionIds']:
        _text(action_id, 'Quality rule action ID', 1024)
    if not isinstance(value.get('documentation'), str):
        raise ProjectAssetError('Quality rule documentation must be text')
    if not isinstance(value.get('nativeDetails'), dict):
        raise ProjectAssetError('Quality rule nativeDetails must be an object')


def _quality_action(value):
    if not isinstance(value, dict) or value.get('schema') != (
            'cdeadmin.quality-action.v1'):
        raise ProjectAssetError('Quality action schema is invalid')
    for field in ('id', 'label'):
        _text(value.get(field), f'Quality action {field}', 1024)
    if value.get('type') not in QUALITY_ACTION_TYPES:
        raise ProjectAssetError('Quality action type is invalid')
    if not isinstance(value.get('severities'), list) or any(
            severity not in QUALITY_SEVERITIES
            for severity in value.get('severities', [])):
        raise ProjectAssetError('Quality action severities are invalid')
    if not isinstance(value.get('enabled'), bool):
        raise ProjectAssetError('Quality action enabled must be boolean')
    if not isinstance(value.get('parameters'), dict):
        raise ProjectAssetError('Quality action parameters must be an object')
    if value['type'] == 'call_command':
        _text(value.get('commandId'), 'Quality action command ID', 1024)


def _quality_content(value):
    if not isinstance(value, dict) or value.get('schema') != QUALITY_SCHEMA:
        raise ProjectAssetError('Data Quality asset schema is invalid')
    if value.get('schemaVersion') != 1 or value.get('moduleId') != (
            'cdeadmin.quality'):
        raise ProjectAssetError('Data Quality asset version is invalid')
    for field in ('name', 'owner'):
        if not isinstance(value.get(field), str):
            raise ProjectAssetError(f'Data Quality {field} must be text')
    tags = value.get('tags')
    if not isinstance(tags, list):
        raise ProjectAssetError('Data Quality tags must be a list')
    for tag in tags:
        _text(tag, 'Data Quality tag', 1024)
    if not isinstance(value.get('parameters'), dict):
        raise ProjectAssetError('Data Quality parameters must be an object')
    if value.get('defaultScope') is not None:
        _quality_slice(value['defaultScope'])
    rules = value.get('rules')
    if not isinstance(rules, list):
        raise ProjectAssetError('Data Quality rules must be a list')
    rule_ids = set()
    for rule in rules:
        _quality_rule(rule)
        if rule['id'] in rule_ids:
            raise ProjectAssetError('Data Quality rule IDs must be unique')
        rule_ids.add(rule['id'])
    actions = value.get('actions')
    if not isinstance(actions, list):
        raise ProjectAssetError('Data Quality actions must be a list')
    action_ids = set()
    for action in actions:
        _quality_action(action)
        if action['id'] in action_ids:
            raise ProjectAssetError('Data Quality action IDs must be unique')
        action_ids.add(action['id'])
    for rule in rules:
        if any(action_id not in action_ids for action_id in rule['actionIds']):
            raise ProjectAssetError(
                'Data Quality rule references an unknown action'
            )
    policy = value.get('severityPolicy')
    if not isinstance(policy, dict):
        raise ProjectAssetError(
            'Data Quality severityPolicy must be an object'
        )
    weights = policy.get('weights')
    if weights is not None:
        if not isinstance(weights, dict):
            raise ProjectAssetError(
                'Data Quality severity weights must be an object'
            )
        for severity, weight in weights.items():
            if severity not in QUALITY_SEVERITIES:
                raise ProjectAssetError(
                    'Data Quality severity weight is unknown'
                )
            _quality_number(
                weight, f'Data Quality {severity} weight', minimum=0
            )
    if value.get('schedule') is not None and not isinstance(
            value.get('schedule'), dict):
        raise ProjectAssetError('Data Quality schedule must be an object')
    baselines = value.get('baselineRefs')
    if not isinstance(baselines, list):
        raise ProjectAssetError('Data Quality baselineRefs must be a list')
    baseline_ids = set()
    for reference in baselines:
        _quality_reference(reference, 'Data Quality baseline')
        identity = json.dumps(reference, sort_keys=True, separators=(',', ':'))
        if identity in baseline_ids:
            raise ProjectAssetError(
                'Data Quality baseline references must be unique'
            )
        baseline_ids.add(identity)
    _secret_free(value, 'Data Quality asset content')
    return value


def _contract_reference(value, label):
    if not isinstance(value, dict):
        raise ProjectAssetError(f'{label} must be a reference object')
    schema = value.get('schema')
    if schema == 'cdeadmin.resource-ref.v1':
        _text(value.get('canonical'), f'{label} canonical identity', 4096)
    elif schema == 'cdeadmin.asset-ref.v1':
        _text(value.get('projectId'), f'{label} project ID', 1024)
        _text(value.get('assetId'), f'{label} asset ID', 1024)
    elif schema in ('cdeadmin.external-ref.v1', 'cdeadmin.result-ref.v1'):
        _text(value.get('id'), f'{label} identity', 4096)
    else:
        raise ProjectAssetError(f'{label} reference type is unsupported')


def _contract_object(value, label):
    if not isinstance(value, dict):
        raise ProjectAssetError(f'{label} must be an object')


def _contract_unique(values, label, validator):
    if not isinstance(values, list):
        raise ProjectAssetError(f'{label} must be a list')
    identities = set()
    for value in values:
        validator(value)
        identity = value.get('id')
        if identity in identities:
            raise ProjectAssetError(f'{label} IDs must be unique')
        identities.add(identity)
    return identities


def _contract_element(value):
    if not isinstance(value, dict) or value.get('schema') != (
            'cdeadmin.contract-element.v1'):
        raise ProjectAssetError('Contract element schema is invalid')
    for field in ('id', 'name', 'logicalType'):
        _text(value.get(field), f'Contract element {field}', 1024)
    for field in ('description',):
        if not isinstance(value.get(field), str):
            raise ProjectAssetError(f'Contract element {field} must be text')
    if value.get('parentId') is not None:
        _text(value['parentId'], 'Contract element parent ID', 1024)
    if value.get('classification') is not None:
        _text(value['classification'], 'Contract element classification', 1024)
    if not isinstance(value.get('required'), bool):
        raise ProjectAssetError('Contract element required must be boolean')
    for field in ('constraints', 'physicalDefinition', 'extensions'):
        _contract_object(value.get(field), f'Contract element {field}')
    references = value.get('authoritativeDefinitionRefs')
    if not isinstance(references, list):
        raise ProjectAssetError(
            'Contract element authoritativeDefinitionRefs must be a list'
        )
    for reference in references:
        _contract_reference(reference, 'Contract element definition')


def _contract_binding(value):
    if not isinstance(value, dict) or value.get('schema') != (
            'cdeadmin.contract-binding.v1'):
        raise ProjectAssetError('Contract binding schema is invalid')
    for field in ('id', 'elementId', 'environment', 'bindingStatus'):
        _text(value.get(field), f'Contract binding {field}', 1024)
    _contract_reference(value.get('targetRef'), 'Contract binding target')
    if value.get('observedRevision') is not None:
        _text(
            value['observedRevision'], 'Contract binding observed revision',
            4096,
        )
    _contract_object(
        value.get('nativeDetails'), 'Contract binding nativeDetails'
    )


def _contract_quality(value):
    if not isinstance(value, dict) or value.get('schema') != (
            'cdeadmin.contract-quality-obligation.v1'):
        raise ProjectAssetError(
            'Contract quality obligation schema is invalid'
        )
    _text(value.get('id'), 'Contract quality obligation ID', 1024)
    if value.get('elementId') is not None:
        _text(value['elementId'], 'Contract quality element ID', 1024)
    if value.get('qualityRef') is not None:
        _contract_reference(value['qualityRef'], 'Contract quality reference')
    if value.get('importedDefinition') is not None:
        _contract_object(
            value['importedDefinition'], 'Imported quality definition'
        )
    if (value.get('qualityRef') is None and
            value.get('importedDefinition') is None):
        raise ProjectAssetError(
            'Contract quality obligation requires a definition'
        )
    _text(value.get('severity'), 'Contract quality severity', 1024)
    _contract_object(value.get('threshold'), 'Contract quality threshold')
    if not isinstance(value.get('description'), str):
        raise ProjectAssetError('Contract quality description must be text')
    _contract_object(value.get('extensions'), 'Contract quality extensions')


def _contract_sla(value):
    if not isinstance(value, dict) or value.get('schema') != (
            'cdeadmin.contract-service-level.v1'):
        raise ProjectAssetError('Contract service-level schema is invalid')
    for field in ('id', 'measure', 'comparison'):
        _text(value.get(field), f'Contract service-level {field}', 1024)
    target = value.get('target')
    if isinstance(target, bool):
        pass
    elif isinstance(target, (int, float)):
        if not math.isfinite(target):
            raise ProjectAssetError(
                'Contract service-level target must be finite'
            )
    elif not isinstance(target, str):
        raise ProjectAssetError(
            'Contract service-level target must be a scalar'
        )
    for field in ('unit', 'elementId'):
        if value.get(field) is not None:
            _text(value[field], f'Contract service-level {field}', 1024)
    if not isinstance(value.get('description'), str):
        raise ProjectAssetError(
            'Contract service-level description must be text'
        )
    for field in ('window', 'extensions'):
        _contract_object(value.get(field), f'Contract service-level {field}')


def _contract_role(value):
    if not isinstance(value, dict) or value.get('schema') not in (
            'cdeadmin.contract-team-role.v1',
            'cdeadmin.contract-access-role.v1'):
        raise ProjectAssetError('Contract role schema is invalid')
    for field in ('id', 'name'):
        _text(value.get(field), f'Contract role {field}', 1024)
    references = value.get('principalRefs')
    if not isinstance(references, list):
        raise ProjectAssetError('Contract role principalRefs must be a list')
    for reference in references:
        _contract_reference(reference, 'Contract role principal')
    for field in ('responsibilities', 'accessExpectations'):
        entries = value.get(field)
        if not isinstance(entries, list):
            raise ProjectAssetError(f'Contract role {field} must be a list')
        for entry in entries:
            _text(entry, f'Contract role {field} entry', 1024)
    for field in ('support', 'extensions'):
        _contract_object(value.get(field), f'Contract role {field}')


def _contract_server(value):
    if not isinstance(value, dict) or value.get('schema') != (
            'cdeadmin.contract-server-binding.v1'):
        raise ProjectAssetError('Contract server binding schema is invalid')
    for field in ('id', 'providerId', 'environment', 'interface'):
        _text(value.get(field), f'Contract server binding {field}', 1024)
    for field in ('resourceRef', 'apiRef'):
        if value.get(field) is not None:
            _contract_reference(
                value[field], f'Contract server binding {field}'
            )
    if value.get('resourceRef') is None and value.get('apiRef') is None:
        raise ProjectAssetError(
            'Contract server binding requires a resource or API reference'
        )
    _contract_object(
        value.get('nativeDetails'), 'Contract server nativeDetails'
    )


def _contract_definition(value):
    if not isinstance(value, dict) or value.get('schema') != (
            'cdeadmin.contract-authoritative-definition.v1'):
        raise ProjectAssetError(
            'Contract authoritative definition schema is invalid'
        )
    for field in ('id', 'type'):
        _text(value.get(field), f'Contract definition {field}', 1024)
    if value.get('uri') is not None:
        _text(value['uri'], 'Contract definition URI', 8192)
    if value.get('assetRef') is not None:
        _contract_reference(value['assetRef'], 'Contract definition asset')
    if value.get('uri') is None and value.get('assetRef') is None:
        raise ProjectAssetError(
            'Contract definition requires a URI or AssetRef'
        )
    if not isinstance(value.get('description'), str):
        raise ProjectAssetError('Contract definition description must be text')
    _contract_object(value.get('extensions'), 'Contract definition extensions')


def _contract_content(value):
    if not isinstance(value, dict) or value.get('schema') != CONTRACT_SCHEMA:
        raise ProjectAssetError('Data Contract asset schema is invalid')
    if value.get('schemaVersion') != 1 or value.get('moduleId') != (
            'cdeadmin.contract'):
        raise ProjectAssetError('Data Contract asset version is invalid')
    _text(value.get('contractVersion'), 'Data Contract version', 1024)
    if value.get('status') not in CONTRACT_STATUSES:
        raise ProjectAssetError('Data Contract status is invalid')
    for field in ('name', 'domain', 'description'):
        if not isinstance(value.get(field), str):
            raise ProjectAssetError(f'Data Contract {field} must be text')
    element_ids = _contract_unique(
        value.get('elements'), 'Contract elements', _contract_element
    )
    parents = {}
    for element in value['elements']:
        parent = element.get('parentId')
        if parent is not None and parent not in element_ids:
            raise ProjectAssetError(
                'Contract element references an unknown parent'
            )
        if parent == element['id']:
            raise ProjectAssetError('Contract element cannot parent itself')
        parents[element['id']] = parent
    for element_id in element_ids:
        visited = {element_id}
        parent = parents[element_id]
        while parent is not None:
            if parent in visited:
                raise ProjectAssetError(
                    'Contract element hierarchy contains a cycle'
                )
            visited.add(parent)
            parent = parents[parent]
    bindings = value.get('bindings')
    _contract_unique(bindings, 'Contract bindings', _contract_binding)
    quality = value.get('qualityObligations')
    _contract_unique(
        quality, 'Contract quality obligations', _contract_quality
    )
    service_levels = value.get('sla')
    _contract_unique(service_levels, 'Contract service levels', _contract_sla)
    for entry in bindings + quality + service_levels:
        if (entry.get('elementId') is not None and
                entry['elementId'] not in element_ids):
            raise ProjectAssetError(
                'Contract content references an unknown element'
            )
    _contract_unique(value.get('team'), 'Contract team roles', _contract_role)
    _contract_unique(
        value.get('roles'), 'Contract access roles', _contract_role
    )
    _contract_unique(
        value.get('servers'), 'Contract server bindings', _contract_server
    )
    _contract_unique(
        value.get('authoritativeDefinitions'),
        'Contract authoritative definitions', _contract_definition,
    )
    _contract_object(value.get('extensions'), 'Data Contract extensions')
    _secret_free(value, 'Data Contract asset content')
    return value


def _etl_object(value, label):
    if not isinstance(value, dict):
        raise ProjectAssetError(f'{label} must be an object')
    return value


def _etl_list(value, label):
    if not isinstance(value, list) or len(value) > 10000:
        raise ProjectAssetError(f'{label} must be a bounded list')
    return value


def _etl_unique(values, label, validator):
    identities = set()
    for value in _etl_list(values, label):
        validator(value)
        identity = value.get('id')
        if identity in identities:
            raise ProjectAssetError(f'{label} IDs must be unique')
        identities.add(identity)
    return identities


def _etl_reference(value, label):
    _etl_object(value, label)
    schema = value.get('schema')
    if schema == 'cdeadmin.resource-ref.v1':
        _text(value.get('canonical'), f'{label} canonical identity', 8192)
    elif schema == 'cdeadmin.asset-ref.v1':
        _text(value.get('projectId'), f'{label} project ID', 1024)
        _text(value.get('assetId'), f'{label} asset ID', 1024)
    elif schema == 'cdeadmin.external-ref.v1':
        _text(value.get('id'), f'{label} identity', 8192)
    elif schema == 'cdeadmin.credential-ref.v1':
        _text(value.get('scheme'), f'{label} scheme', 1024)
        _text(value.get('id'), f'{label} identity', 1024)
    else:
        raise ProjectAssetError(f'{label} reference type is unsupported')


def _etl_nonnegative_integer(value, label, *, maximum=None):
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ProjectAssetError(f'{label} must be a non-negative integer')
    if maximum is not None and value > maximum:
        raise ProjectAssetError(f'{label} exceeds its maximum')


def _etl_schema_field(value):
    if not isinstance(value, dict) or value.get('schema') != (
            'cdeadmin.etl-schema-field.v1'):
        raise ProjectAssetError('ETL schema field schema is invalid')
    for field in ('id', 'name'):
        _text(value.get(field), f'ETL schema field {field}', 1024)
    for field in (
            'nativeType', 'semanticType', 'timezone', 'encoding', 'path'):
        if value.get(field) is not None:
            _text(value[field], f'ETL schema field {field}', 8192)
    if not isinstance(value.get('nullable'), bool):
        raise ProjectAssetError('ETL schema field nullable must be boolean')
    for field in ('precision', 'scale'):
        if value.get(field) is not None:
            if (isinstance(value[field], bool) or
                    not isinstance(value[field], int)):
                raise ProjectAssetError(
                    f'ETL schema field {field} must be an integer'
                )
    if value.get('precision') is not None and value['precision'] < 0:
        raise ProjectAssetError(
            'ETL schema field precision cannot be negative'
        )
    _etl_object(value.get('nativeDetails'), 'ETL schema field nativeDetails')


def _etl_port(value):
    if not isinstance(value, dict) or value.get('schema') != (
            'cdeadmin.etl-port.v1'):
        raise ProjectAssetError('ETL port schema is invalid')
    for field in ('id', 'name'):
        _text(value.get(field), f'ETL port {field}', 1024)
    if value.get('direction') not in ('input', 'output'):
        raise ProjectAssetError('ETL port direction is invalid')
    if value.get('mode') not in ETL_PORT_MODES:
        raise ProjectAssetError('ETL port mode is invalid')
    if value.get('schemaState') not in ETL_SCHEMA_STATES:
        raise ProjectAssetError('ETL port schema state is invalid')
    if value.get('schemaRef') is not None:
        _etl_reference(value['schemaRef'], 'ETL port schema reference')
    _etl_unique(value.get('fields'), 'ETL schema fields', _etl_schema_field)
    if value.get('cardinality') is not None:
        _text(value['cardinality'], 'ETL port cardinality', 1024)
    for field in ('ordering',):
        for item in _etl_list(value.get(field), f'ETL port {field}'):
            _text(item, f'ETL port {field} item', 1024)
    for field in ('partitioning', 'watermark', 'nativeDetails'):
        _etl_object(value.get(field), f'ETL port {field}')


def _etl_error_route(value):
    if value is None:
        return
    if not isinstance(value, dict) or value.get('schema') != (
            'cdeadmin.etl-error-route.v1'):
        raise ProjectAssetError('ETL error route schema is invalid')
    if value.get('action') not in ETL_ERROR_ACTIONS:
        raise ProjectAssetError('ETL error route action is invalid')
    _etl_nonnegative_integer(
        value.get('maximumAttempts'), 'ETL error maximum attempts', maximum=100
    )
    if value.get('targetRef') is not None:
        _etl_reference(value['targetRef'], 'ETL error-route target')
    if (value.get('action') == 'dead_letter' and
            (value.get('targetRef') is None or
             value['targetRef'].get('schema') !=
             'cdeadmin.resource-ref.v1')):
        raise ProjectAssetError(
            'ETL dead-letter routes require a provider ResourceRef target'
        )
    for field in ('retryPolicy', 'nativeDetails'):
        _etl_object(value.get(field), f'ETL error route {field}')


def _etl_node(value):
    if not isinstance(value, dict) or value.get('schema') != (
            'cdeadmin.etl-node.v1'):
        raise ProjectAssetError('ETL node schema is invalid')
    for field in ('id', 'name'):
        _text(value.get(field), f'ETL node {field}', 1024)
    if value.get('kind') not in ETL_NODE_FAMILIES:
        raise ProjectAssetError('ETL node family is invalid')
    if value.get('executionPreference') not in ETL_EXECUTION_LOCATIONS:
        raise ProjectAssetError('ETL execution preference is invalid')
    if value.get('resourceRef') is not None:
        _etl_reference(value['resourceRef'], 'ETL node resource')
        if value['resourceRef'].get('schema') != 'cdeadmin.resource-ref.v1':
            raise ProjectAssetError('ETL node resource must be a ResourceRef')
    _etl_unique(value.get('ports'), 'ETL ports', _etl_port)
    for capability in _etl_list(
            value.get('capabilityRequirements'),
            'ETL node capability requirements'):
        _text(capability, 'ETL node capability requirement', 1024)
    _etl_error_route(value.get('errorRoute'))
    if not isinstance(value.get('checkpointEnabled'), bool):
        raise ProjectAssetError('ETL checkpointEnabled must be boolean')
    for field in ('config', 'nativeDetails'):
        _etl_object(value.get(field), f'ETL node {field}')


def _etl_mapping(value):
    if not isinstance(value, dict) or value.get('schema') != (
            'cdeadmin.etl-schema-mapping.v1'):
        raise ProjectAssetError('ETL schema mapping schema is invalid')
    for field in ('id', 'source', 'target', 'conversion', 'nullPolicy'):
        _text(value.get(field), f'ETL schema mapping {field}', 8192)
    for field in (
            'sourceNativeType', 'semanticType', 'targetNativeType',
            'timezone', 'encoding'):
        if value.get(field) is not None:
            _text(value[field], f'ETL schema mapping {field}', 1024)
    for field in ('nullable', 'lossy', 'lossAcknowledged'):
        if not isinstance(value.get(field), bool):
            raise ProjectAssetError(
                f'ETL schema mapping {field} must be boolean'
            )
    for field in ('precision', 'scale'):
        if value.get(field) is not None and (
                isinstance(value[field], bool) or
                not isinstance(value[field], int)):
            raise ProjectAssetError(
                f'ETL schema mapping {field} must be an integer'
            )
    if value.get('precision') is not None and value['precision'] < 0:
        raise ProjectAssetError(
            'ETL schema mapping precision cannot be negative'
        )
    _etl_object(value.get('nativeDetails'), 'ETL schema mapping nativeDetails')


def _etl_edge(value):
    if not isinstance(value, dict) or value.get('schema') != (
            'cdeadmin.etl-edge.v1'):
        raise ProjectAssetError('ETL edge schema is invalid')
    for field in (
            'id', 'fromNodeId', 'fromPort', 'toNodeId', 'toPort',
            'mappingPolicy'):
        _text(value.get(field), f'ETL edge {field}', 1024)
    if value.get('deliveryGuarantee') not in ETL_DELIVERY_GUARANTEES:
        raise ProjectAssetError('ETL edge delivery guarantee is invalid')
    _etl_unique(value.get('mappings'), 'ETL schema mappings', _etl_mapping)
    for field in ('partitioning', 'ordering', 'nativeDetails'):
        _etl_object(value.get(field), f'ETL edge {field}')


def _etl_parameter(value):
    if not isinstance(value, dict) or value.get('schema') != (
            'cdeadmin.etl-parameter.v1'):
        raise ProjectAssetError('ETL parameter schema is invalid')
    for field in ('id', 'name', 'type'):
        _text(value.get(field), f'ETL parameter {field}', 1024)
    for field in ('required', 'secret'):
        if not isinstance(value.get(field), bool):
            raise ProjectAssetError(f'ETL parameter {field} must be boolean')
    if value.get('secret'):
        if value.get('default') is not None:
            raise ProjectAssetError(
                'Secret ETL parameters cannot contain a default value'
            )
        if value.get('credentialRef') is None:
            raise ProjectAssetError(
                'Secret ETL parameters require a CredentialRef'
            )
    if value.get('credentialRef') is not None:
        _etl_reference(
            value['credentialRef'], 'ETL parameter credential reference'
        )
        if value['credentialRef'].get('schema') != (
                'cdeadmin.credential-ref.v1'):
            raise ProjectAssetError(
                'ETL parameter credential must be a CredentialRef'
            )
    if not isinstance(value.get('description'), str):
        raise ProjectAssetError('ETL parameter description must be text')
    _etl_object(value.get('nativeDetails'), 'ETL parameter nativeDetails')


def _etl_deployment_binding(value):
    if not isinstance(value, dict) or value.get('schema') != (
            'cdeadmin.etl-deployment-binding.v1'):
        raise ProjectAssetError('ETL deployment binding schema is invalid')
    for field in ('id', 'nodeId'):
        _text(value.get(field), f'ETL deployment binding {field}', 1024)
    _etl_reference(value.get('resourceRef'), 'ETL deployment resource')
    if value['resourceRef'].get('schema') != 'cdeadmin.resource-ref.v1':
        raise ProjectAssetError(
            'ETL deployment resource must be a ResourceRef'
        )
    _etl_object(
        value.get('nativeDetails'), 'ETL deployment binding nativeDetails'
    )


def _etl_deployment(value):
    if not isinstance(value, dict) or value.get('schema') != (
            'cdeadmin.etl-deployment.v1'):
        raise ProjectAssetError('ETL deployment schema is invalid')
    for field in ('id', 'name', 'environment'):
        _text(value.get(field), f'ETL deployment {field}', 1024)
    _etl_unique(
        value.get('bindings'), 'ETL deployment bindings',
        _etl_deployment_binding,
    )
    for field in ('parameterBindings', 'resourceLimits', 'nativeDetails'):
        _etl_object(value.get(field), f'ETL deployment {field}')


def _etl_schedule(value):
    if not isinstance(value, dict) or value.get('schema') != (
            'cdeadmin.etl-schedule.v1'):
        raise ProjectAssetError('ETL schedule schema is invalid')
    for field in (
            'id', 'name', 'trigger', 'expression', 'timezone', 'deploymentId'):
        _text(value.get(field), f'ETL schedule {field}', 8192)
    if not isinstance(value.get('enabled'), bool):
        raise ProjectAssetError('ETL schedule enabled must be boolean')
    for reference in _etl_list(
            value.get('dependencyRefs'), 'ETL schedule dependencyRefs'):
        _etl_reference(reference, 'ETL schedule dependency')
    for field in ('parameters', 'nativeDetails'):
        _etl_object(value.get(field), f'ETL schedule {field}')


def _etl_content(value):
    if not isinstance(value, dict) or value.get('schema') != ETL_SCHEMA:
        raise ProjectAssetError('ETL asset schema is invalid')
    if value.get('schemaVersion') != 1 or value.get('moduleId') != (
            'cdeadmin.etl'):
        raise ProjectAssetError('ETL asset version or module is invalid')
    if value.get('mode') not in ETL_PIPELINE_MODES:
        raise ProjectAssetError('ETL pipeline mode is invalid')
    for field in ('name', 'description'):
        if not isinstance(value.get(field), str):
            raise ProjectAssetError(f'ETL pipeline {field} must be text')
    _etl_unique(value.get('parameters'), 'ETL parameters', _etl_parameter)
    node_ids = _etl_unique(value.get('nodes'), 'ETL nodes', _etl_node)
    port_index = {
        node['id']: {port['id']: port for port in node['ports']}
        for node in value['nodes']
    }
    _etl_unique(value.get('edges'), 'ETL edges', _etl_edge)
    incoming = {node_id: 0 for node_id in node_ids}
    outgoing = {node_id: [] for node_id in node_ids}
    for edge in value['edges']:
        source = edge['fromNodeId']
        target = edge['toNodeId']
        if source not in node_ids or target not in node_ids:
            raise ProjectAssetError('ETL edge references an unknown node')
        source_port = port_index[source].get(edge['fromPort'])
        target_port = port_index[target].get(edge['toPort'])
        if source_port is None or source_port['direction'] != 'output':
            raise ProjectAssetError('ETL edge source must be an output port')
        if target_port is None or target_port['direction'] != 'input':
            raise ProjectAssetError('ETL edge target must be an input port')
        if not (
                source_port['mode'] == 'either' or
                target_port['mode'] == 'either' or
                source_port['mode'] == target_port['mode']):
            raise ProjectAssetError('ETL edge port modes are incompatible')
        incoming[target] += 1
        outgoing[source].append(target)
    queue = sorted(
        node_id for node_id, count in incoming.items() if count == 0
    )
    visited = []
    while queue:
        node_id = queue.pop(0)
        visited.append(node_id)
        for target in sorted(outgoing[node_id]):
            incoming[target] -= 1
            if incoming[target] == 0:
                queue.append(target)
                queue.sort()
    if len(visited) != len(node_ids):
        raise ProjectAssetError('ETL pipeline graph contains a cycle')
    deployment_ids = _etl_unique(
        value.get('deployments'), 'ETL deployments', _etl_deployment
    )
    for deployment in value['deployments']:
        for binding in deployment['bindings']:
            if binding['nodeId'] not in node_ids:
                raise ProjectAssetError(
                    'ETL deployment binds an unknown node'
                )
    _etl_unique(value.get('schedules'), 'ETL schedules', _etl_schedule)
    for schedule in value['schedules']:
        if schedule['deploymentId'] not in deployment_ids:
            raise ProjectAssetError(
                'ETL schedule references an unknown deployment'
            )
    _etl_unique(value.get('tests'), 'ETL tests', lambda test: (
        _text(test.get('id'), 'ETL test ID', 1024),
        _text(test.get('name'), 'ETL test name', 1024),
        _etl_object(test.get('definition'), 'ETL test definition'),
    ) if isinstance(test, dict) else (_ for _ in ()).throw(
        ProjectAssetError('ETL test must be an object')
    ))
    for field in ('visualLayout', 'extensions'):
        _etl_object(value.get(field), f'ETL pipeline {field}')
    _secret_free(value, 'ETL asset content')
    return value


def _cdc_object(value, label):
    if value is None:
        value = {}
    if not isinstance(value, dict):
        raise ProjectAssetError(f'{label} must be an object')
    _secret_free(value, label)
    return value


def _cdc_list(value, label):
    if value is None:
        value = []
    if not isinstance(value, list) or len(value) > 10000:
        raise ProjectAssetError(f'{label} must be a bounded array')
    return value


def _cdc_unique(values, label, validator):
    identifiers = set()
    for item in _cdc_list(values, label):
        validator(item)
        identifier = item.get('id')
        if identifier in identifiers:
            raise ProjectAssetError(f'duplicate {label} ID: {identifier}')
        identifiers.add(identifier)
    return identifiers


def _cdc_reference(value, label):
    _cdc_object(value, label)
    schema = value.get('schema')
    if schema not in {
            'cdeadmin.resource-ref.v1', 'cdeadmin.asset-ref.v1',
            'cdeadmin.external-ref.v1', 'cdeadmin.credential-ref.v1'}:
        raise ProjectAssetError(f'{label} schema is unsupported')
    if schema == 'cdeadmin.resource-ref.v1':
        _text(value.get('canonical'), f'{label} canonical identity', 8192)
    elif schema == 'cdeadmin.asset-ref.v1':
        _text(value.get('projectId'), f'{label} project ID', 1024)
        _text(value.get('assetId'), f'{label} asset ID', 1024)
    elif schema == 'cdeadmin.credential-ref.v1':
        _text(value.get('scheme'), f'{label} scheme', 1024)
        _text(value.get('id'), f'{label} ID', 1024)
    else:
        _text(value.get('id'), f'{label} ID', 8192)
    return value


def _cdc_native_objects(value, fields, label):
    for field in fields:
        _cdc_object(value.get(field), f'{label} {field}')


def _cdc_start_position(value):
    _cdc_object(value, 'CDC start position')
    if value.get('schema') != 'cdeadmin.cdc-start-position.v1':
        raise ProjectAssetError('CDC start-position schema is invalid')
    kind = value.get('kind')
    if kind not in CDC_START_KINDS:
        raise ProjectAssetError('CDC start-position kind is invalid')
    if kind != 'latest':
        _text(value.get('value'), 'CDC start-position value', 16384)
    elif value.get('value') is not None:
        raise ProjectAssetError(
            'latest CDC start position cannot have a value'
        )
    _cdc_object(value.get('nativeDetails'), 'CDC start native details')


def _cdc_snapshot_policy(value):
    _cdc_object(value, 'CDC snapshot policy')
    if value.get('schema') != 'cdeadmin.cdc-snapshot-policy.v1':
        raise ProjectAssetError('CDC snapshot-policy schema is invalid')
    if value.get('mode') not in CDC_SNAPSHOT_MODES:
        raise ProjectAssetError('CDC snapshot mode is invalid')
    if value.get('consistency') is not None:
        _text(value.get('consistency'), 'CDC snapshot consistency', 8192)
    batch_size = value.get('batchSize')
    if batch_size is not None and (
            isinstance(batch_size, bool) or not isinstance(batch_size, int) or
            batch_size < 1 or batch_size > 10000000):
        raise ProjectAssetError('CDC snapshot batch size is invalid')
    if value.get('mode') == 'incremental' and batch_size is None:
        raise ProjectAssetError('incremental CDC snapshot requires batch size')
    _cdc_object(value.get('nativeDetails'), 'CDC snapshot native details')


def _cdc_capture_source(value):
    _cdc_object(value, 'CDC capture source')
    if value.get('schema') != 'cdeadmin.cdc-capture-source.v1':
        raise ProjectAssetError('CDC capture-source schema is invalid')
    resource = _cdc_reference(value.get('resourceRef'), 'CDC source resource')
    if resource.get('schema') != 'cdeadmin.resource-ref.v1':
        raise ProjectAssetError('CDC source requires a ResourceRef')
    if value.get('captureMechanism') not in CDC_CAPTURE_MECHANISMS:
        raise ProjectAssetError('CDC capture mechanism is invalid')
    _text(value.get('nativeMechanism'), 'CDC native mechanism', 8192)
    for privilege in _cdc_list(
            value.get('requiredPrivileges'), 'CDC required privileges'):
        _text(privilege, 'CDC required privilege', 1024)
    if value.get('credentialRef') is not None:
        credential = _cdc_reference(
            value.get('credentialRef'), 'CDC source credential')
        if credential.get('schema') != 'cdeadmin.credential-ref.v1':
            raise ProjectAssetError(
                'CDC source credential requires CredentialRef'
            )
    _cdc_start_position(value.get('startPosition'))
    _cdc_snapshot_policy(value.get('snapshotPolicy'))
    _cdc_object(value.get('nativeDetails'), 'CDC source native details')


def _cdc_transform(value):
    _cdc_object(value, 'CDC transform')
    if value.get('schema') != 'cdeadmin.cdc-event-transform.v1':
        raise ProjectAssetError('CDC event-transform schema is invalid')
    _text(value.get('id'), 'CDC transform ID', 1024)
    _text(value.get('name'), 'CDC transform name', 1024)
    if value.get('kind') not in {'filter', 'mapping', 'redaction'}:
        raise ProjectAssetError('CDC transform kind is invalid')
    if not isinstance(value.get('enabled'), bool):
        raise ProjectAssetError('CDC transform enabled must be boolean')
    _cdc_native_objects(
        value, ('rules', 'schemaMapping', 'nativeDetails'), 'CDC transform')


def _cdc_sink(value):
    _cdc_object(value, 'CDC sink')
    if value.get('schema') != 'cdeadmin.cdc-sink-binding.v1':
        raise ProjectAssetError('CDC sink schema is invalid')
    resource = _cdc_reference(value.get('resourceRef'), 'CDC sink resource')
    if resource.get('schema') != 'cdeadmin.resource-ref.v1':
        raise ProjectAssetError('CDC sink requires a ResourceRef')
    if value.get('credentialRef') is not None:
        credential = _cdc_reference(
            value.get('credentialRef'), 'CDC sink credential')
        if credential.get('schema') != 'cdeadmin.credential-ref.v1':
            raise ProjectAssetError(
                'CDC sink credential requires CredentialRef'
            )
    _text(value.get('serialization'), 'CDC sink serialization', 8192)
    _cdc_object(value.get('nativeDetails'), 'CDC sink native details')


def _cdc_delivery(value):
    _cdc_object(value, 'CDC delivery policy')
    if value.get('schema') != 'cdeadmin.cdc-delivery-policy.v1':
        raise ProjectAssetError('CDC delivery-policy schema is invalid')
    if value.get('guarantee') not in CDC_DELIVERY_GUARANTEES:
        raise ProjectAssetError('CDC delivery guarantee is invalid')
    _cdc_native_objects(
        value, ('deduplication', 'proof', 'nativeDetails'), 'CDC delivery')
    if value.get('guarantee') == 'exactly_once_proven':
        proof = value.get('proof')
        if any(not proof.get(field) for field in (
                'sourceCapture', 'transport', 'sinkApplication')):
            raise ProjectAssetError(
                'exactly-once CDC delivery requires end-to-end proof'
            )


def _cdc_schema_change(value):
    _cdc_object(value, 'CDC schema change')
    if value.get('schema') != 'cdeadmin.cdc-schema-change.v1':
        raise ProjectAssetError('CDC schema-change schema is invalid')
    _text(value.get('id'), 'CDC schema-change ID', 1024)
    if value.get('classification') not in CDC_SCHEMA_CHANGE_CLASSES:
        raise ProjectAssetError('CDC schema-change classification is invalid')
    if value.get('action') not in CDC_EVOLUTION_ACTIONS:
        raise ProjectAssetError('CDC schema-change action is invalid')
    if (value.get('classification') in {'breaking', 'unknown'} and
            value.get('action') == 'auto_apply_compatible'):
        raise ProjectAssetError(
            'breaking or unknown CDC changes cannot be auto-applied'
        )
    if not isinstance(value.get('description'), str):
        raise ProjectAssetError('CDC schema-change description must be text')
    if value.get('detectedSchemaVersion') is not None:
        _text(
            value.get('detectedSchemaVersion'),
            'CDC detected schema version', 8192,
        )
    _cdc_native_objects(
        value, ('mapping', 'nativeDetails'), 'CDC schema change')


def _cdc_evolution(value):
    _cdc_object(value, 'CDC evolution policy')
    if value.get('schema') != 'cdeadmin.cdc-evolution-policy.v1':
        raise ProjectAssetError('CDC evolution-policy schema is invalid')
    if value.get('defaultAction') not in CDC_EVOLUTION_ACTIONS:
        raise ProjectAssetError('CDC default evolution action is invalid')
    _cdc_unique(
        value.get('changes'), 'CDC schema changes', _cdc_schema_change)
    _cdc_object(value.get('nativeDetails'), 'CDC evolution native details')


def _cdc_alert(value):
    _cdc_object(value, 'CDC alert')
    if value.get('schema') != 'cdeadmin.cdc-alert.v1':
        raise ProjectAssetError('CDC alert schema is invalid')
    _text(value.get('id'), 'CDC alert ID', 1024)
    _text(value.get('event'), 'CDC alert event', 1024)
    if not isinstance(value.get('enabled'), bool):
        raise ProjectAssetError('CDC alert enabled must be boolean')
    _cdc_native_objects(
        value, ('threshold', 'nativeDetails'), 'CDC alert')
    if value.get('event') == 'lag':
        maximum = value.get('threshold', {}).get('maximum')
        if (isinstance(maximum, bool) or
                not isinstance(maximum, (int, float)) or maximum < 0):
            raise ProjectAssetError(
                'CDC lag alert requires a non-negative maximum threshold'
            )


def _cdc_content(value):
    if not isinstance(value, dict) or value.get('schema') != CDC_SCHEMA:
        raise ProjectAssetError('CDC asset schema is invalid')
    if (value.get('schemaVersion') != 1 or
            value.get('moduleId') != 'cdeadmin.cdc'):
        raise ProjectAssetError('CDC asset version or module is invalid')
    for field in ('name', 'description'):
        if not isinstance(value.get(field), str):
            raise ProjectAssetError(f'CDC definition {field} must be text')
    if value.get('source') is not None:
        _cdc_capture_source(value.get('source'))
    if value.get('sink') is not None:
        _cdc_sink(value.get('sink'))
    _cdc_unique(value.get('filters'), 'CDC filters', _cdc_transform)
    _cdc_unique(value.get('transforms'), 'CDC transforms', _cdc_transform)
    if any(item.get('kind') != 'filter' for item in value.get('filters', [])):
        raise ProjectAssetError(
            'CDC filters may contain only filter transforms'
        )
    if any(
            item.get('kind') == 'filter'
            for item in value.get('transforms', [])):
        raise ProjectAssetError(
            'CDC filter transforms belong in the filters collection'
        )
    _cdc_delivery(value.get('deliveryPolicy'))
    _cdc_evolution(value.get('schemaEvolutionPolicy'))
    _cdc_unique(value.get('alerts'), 'CDC alerts', _cdc_alert)
    _cdc_native_objects(
        value, ('visualLayout', 'extensions'), 'CDC definition')
    _secret_free(value, 'CDC asset content')
    return value


def _replication_object(value, label):
    if value is None:
        value = {}
    if not isinstance(value, dict):
        raise ProjectAssetError(f'{label} must be an object')
    _secret_free(value, label)
    return value


def _replication_list(value, label, *, maximum=10000):
    if value is None:
        value = []
    if not isinstance(value, list) or len(value) > maximum:
        raise ProjectAssetError(f'{label} must be a bounded array')
    return value


def _replication_unique(values, label, validator, *, maximum=10000):
    identifiers = set()
    for item in _replication_list(values, label, maximum=maximum):
        validator(item)
        identifier = item.get('id')
        if identifier in identifiers:
            raise ProjectAssetError(f'duplicate {label} ID: {identifier}')
        identifiers.add(identifier)
    return identifiers


def _replication_reference(value, label):
    _replication_object(value, label)
    schema = value.get('schema')
    if schema == 'cdeadmin.resource-ref.v1':
        _text(value.get('canonical'), f'{label} canonical identity', 8192)
        _text(
            value.get('provider') or value.get('providerId'),
            f'{label} provider', 1024,
        )
    elif schema == 'cdeadmin.asset-ref.v1':
        _text(value.get('projectId'), f'{label} project ID', 1024)
        _text(value.get('assetId'), f'{label} asset ID', 1024)
    elif schema == 'cdeadmin.external-ref.v1':
        _text(value.get('id'), f'{label} identity', 8192)
    else:
        raise ProjectAssetError(f'{label} schema is unsupported')
    return value


def _replication_health(value, label):
    _replication_object(value, label)
    if value.get('schema') != 'cdeadmin.replication-health.v1':
        raise ProjectAssetError(f'{label} schema is invalid')
    if value.get('overall') not in REPLICATION_HEALTH_STATES:
        raise ProjectAssetError(f'{label} overall state is invalid')
    dimensions = _replication_object(
        value.get('dimensions'), f'{label} dimensions')
    if any(state not in REPLICATION_HEALTH_STATES
           for state in dimensions.values()):
        raise ProjectAssetError(f'{label} dimension state is invalid')
    if not isinstance(value.get('policyAllowsUnknownCritical'), bool):
        raise ProjectAssetError(
            f'{label} unknown-critical policy must be boolean')
    if value.get('providerReported') is not None:
        _text(value.get('providerReported'), f'{label} provider state', 8192)
    _replication_object(value.get('nativeDetails'), f'{label} native details')


def _replication_position(value, label):
    if value is None:
        return
    _replication_object(value, label)
    if value.get('schema') != 'cdeadmin.replication-position.v1':
        raise ProjectAssetError(f'{label} schema is invalid')
    for field in ('provider', 'positionType', 'rawValue', 'observedAt'):
        _text(value.get(field), f'{label} {field}', 65536)
    if not isinstance(value.get('comparableWithinScope'), bool):
        raise ProjectAssetError(f'{label} comparability must be boolean')
    _replication_object(value.get('nativeDetails'), f'{label} native details')


def _replication_participant(value):
    _replication_object(value, 'Replication participant')
    if value.get('schema') != 'cdeadmin.replication-participant.v1':
        raise ProjectAssetError('Replication participant schema is invalid')
    for field in ('id', 'name', 'nativeRole', 'nativeState'):
        _text(value.get(field), f'Replication participant {field}', 8192)
    if value.get('normalizedRole') not in REPLICATION_ROLES:
        raise ProjectAssetError(
            'Replication participant normalized role is invalid')
    _replication_reference(
        value.get('resourceRef'), 'Replication participant resource')
    if value.get('groupId') is not None:
        _text(value.get('groupId'), 'Replication participant group ID', 1024)
    _replication_health(value.get('health'), 'Replication participant health')
    _replication_position(
        value.get('position'), 'Replication participant position')
    for field in ('storage', 'connections', 'nativeDetails'):
        _replication_object(
            value.get(field), f'Replication participant {field}')


def _replication_group(value):
    _replication_object(value, 'Replication group')
    if value.get('schema') != 'cdeadmin.replica-group.v1':
        raise ProjectAssetError('Replication group schema is invalid')
    for field in ('id', 'name', 'nativeType'):
        _text(value.get(field), f'Replication group {field}', 8192)
    if value.get('quorumState') is not None:
        _text(value.get('quorumState'), 'Replication group quorum state', 8192)
    members = _replication_list(
        value.get('memberIds'), 'Replication group members')
    for member in members:
        _text(member, 'Replication group member ID', 1024)
    if len(set(members)) != len(members):
        raise ProjectAssetError('Replication group members must be unique')
    for field in ('roleMetadata', 'nativeDetails'):
        _replication_object(value.get(field), f'Replication group {field}')


def _replication_link(value):
    _replication_object(value, 'Replication link')
    if value.get('schema') != 'cdeadmin.replication-link.v1':
        raise ProjectAssetError('Replication link schema is invalid')
    for field in (
            'id', 'name', 'sourceParticipantId', 'targetParticipantId',
            'mechanism', 'nativeState'):
        _text(value.get(field), f'Replication link {field}', 8192)
    _replication_health(value.get('health'), 'Replication link health')
    _replication_position(value.get('position'), 'Replication link position')
    for error in _replication_list(
            value.get('errors'), 'Replication link errors', maximum=1000):
        _text(error, 'Replication link error', 65536)
    for field in ('checkpoint', 'nativeDetails'):
        _replication_object(value.get(field), f'Replication link {field}')


def _replication_nonnegative_number(value, label):
    if (isinstance(value, bool) or not isinstance(value, (int, float)) or
            not math.isfinite(value) or value < 0):
        raise ProjectAssetError(
            f'{label} must be a finite non-negative number')


def _replication_lag(value):
    _replication_object(value, 'Replication lag sample')
    if value.get('schema') != 'cdeadmin.replication-lag-sample.v1':
        raise ProjectAssetError('Replication lag sample schema is invalid')
    for field in ('id', 'linkId', 'observedAt', 'unit', 'source'):
        _text(value.get(field), f'Replication lag sample {field}', 8192)
    _replication_nonnegative_number(
        value.get('value'), 'Replication lag sample value')
    for field in ('calculation', 'nativeDetails'):
        _replication_object(value.get(field), f'Replication lag {field}')


def _replication_event(value):
    _replication_object(value, 'Replication event')
    if value.get('schema') != 'cdeadmin.replication-event.v1':
        raise ProjectAssetError('Replication event schema is invalid')
    for field in ('id', 'topologyId', 'type', 'occurredAt'):
        _text(value.get(field), f'Replication event {field}', 8192)
    for field in ('participantId', 'linkId', 'cause'):
        if value.get(field) is not None:
            _text(value.get(field), f'Replication event {field}', 65536)
    for field in ('evidence', 'nativeDetails'):
        _replication_object(value.get(field), f'Replication event {field}')


def _replication_topology(value):
    _replication_object(value, 'Replication topology')
    if value.get('schema') != 'cdeadmin.replication-topology.v1':
        raise ProjectAssetError('Replication topology schema is invalid')
    for field in ('id', 'name', 'providerId', 'revision'):
        _text(value.get(field), f'Replication topology {field}', 8192)
    scope = _replication_reference(
        value.get('scopeRef'), 'Replication topology scope')
    if scope.get('schema') != 'cdeadmin.resource-ref.v1':
        raise ProjectAssetError(
            'Replication topology scope must be a ResourceRef')
    if (scope.get('provider') or scope.get('providerId')) != (
            value.get('providerId')):
        raise ProjectAssetError(
            'Replication topology provider must match its scope')
    participant_ids = _replication_unique(
        value.get('participants'), 'Replication participants',
        _replication_participant,
    )
    group_ids = _replication_unique(
        value.get('groups'), 'Replication groups', _replication_group)
    link_ids = _replication_unique(
        value.get('links'), 'Replication links', _replication_link)
    for participant in value.get('participants', []):
        if (participant.get('groupId') is not None and
                participant.get('groupId') not in group_ids):
            raise ProjectAssetError(
                'Replication participant references an unknown group')
    for group in value.get('groups', []):
        if any(member not in participant_ids
               for member in group.get('memberIds', [])):
            raise ProjectAssetError(
                'Replication group references an unknown participant')
    for link in value.get('links', []):
        if (link.get('sourceParticipantId') not in participant_ids or
                link.get('targetParticipantId') not in participant_ids):
            raise ProjectAssetError(
                'Replication link references an unknown participant')
    _replication_unique(
        value.get('lagSamples'), 'Replication lag samples',
        _replication_lag, maximum=100000,
    )
    if any(sample.get('linkId') not in link_ids
           for sample in value.get('lagSamples', [])):
        raise ProjectAssetError(
            'Replication lag sample references an unknown link')
    _replication_unique(
        value.get('events'), 'Replication events', _replication_event,
        maximum=100000,
    )
    if any(event.get('topologyId') != value.get('id')
           for event in value.get('events', [])):
        raise ProjectAssetError(
            'Replication event topology does not match its container')
    for field in ('conflictPolicy', 'healthPolicy', 'nativeDetails'):
        _replication_object(value.get(field), f'Replication topology {field}')


def _replication_plan_item(value, label):
    _replication_object(value, label)
    for field in ('id', 'label', 'action'):
        _text(value.get(field), f'{label} {field}', 8192)
    if value.get('targetRef') is not None:
        _replication_reference(value.get('targetRef'), f'{label} target')
    for field in ('parameters', 'evidenceRequirements', 'nativeDetails'):
        _replication_object(value.get(field), f'{label} {field}')


def _replication_estimate(value, label):
    if value is None:
        return
    _replication_object(value, label)
    _replication_nonnegative_number(value.get('value'), f'{label} value')
    _text(value.get('unit'), f'{label} unit', 1024)
    _replication_object(value.get('evidence'), f'{label} evidence')


def _replication_failover(value):
    _replication_object(value, 'Replication failover plan')
    if value.get('schema') != 'cdeadmin.replication-failover-plan.v1':
        raise ProjectAssetError('Replication failover plan schema is invalid')
    for field in (
            'id', 'name', 'topologyId', 'candidateParticipantId',
            'targetRole', 'dataLossRisk'):
        _text(value.get(field), f'Replication failover plan {field}', 8192)
    for field, label in (
            ('preconditions', 'Replication failover preconditions'),
            ('commands', 'Replication failover commands'),
            ('verification', 'Replication failover verification'),
            ('rollback', 'Replication failover rollback')):
        identifiers = _replication_unique(
            value.get(field), label,
            lambda item, item_label=label: _replication_plan_item(
                item, item_label),
        )
        if not identifiers:
            raise ProjectAssetError(
                'Replication failover plans require preconditions, commands, '
                'verification and rollback')
    _replication_object(
        value.get('expectedTopology'),
        'Replication failover expected topology',
    )
    _replication_estimate(
        value.get('estimatedRPO'), 'Replication failover estimated RPO')
    _replication_estimate(
        value.get('estimatedRTO'), 'Replication failover estimated RTO')
    _replication_object(
        value.get('nativeDetails'), 'Replication failover native details')


def _replication_alert(value):
    _replication_object(value, 'Replication alert policy')
    if value.get('schema') != 'cdeadmin.replication-alert-policy.v1':
        raise ProjectAssetError('Replication alert policy schema is invalid')
    for field in ('id', 'topologyId', 'metric', 'unit'):
        _text(value.get(field), f'Replication alert policy {field}', 8192)
    if value.get('linkId') is not None:
        _text(value.get('linkId'), 'Replication alert link ID', 1024)
    _replication_nonnegative_number(
        value.get('maximum'), 'Replication alert maximum')
    if not isinstance(value.get('enabled'), bool):
        raise ProjectAssetError('Replication alert enabled must be boolean')
    _replication_object(
        value.get('nativeDetails'), 'Replication alert native details')


def _replication_layout(value):
    _replication_object(value, 'Replication visual layout')
    if value.get('schema') != 'cdeadmin.replication-layout.v1':
        raise ProjectAssetError('Replication visual layout schema is invalid')
    for field in ('id', 'topologyId'):
        _text(value.get(field), f'Replication visual layout {field}', 1024)
    positions = _replication_object(
        value.get('positions'), 'Replication visual positions')
    for identity, point in positions.items():
        _text(identity, 'Replication visual participant ID', 1024)
        _replication_object(point, 'Replication visual position')
        for axis in ('x', 'y'):
            _replication_nonnegative_number(
                point.get(axis), f'Replication visual {axis}')
            if point[axis] > 10000:
                raise ProjectAssetError(
                    f'Replication visual {axis} exceeds its maximum')
    _replication_object(
        value.get('nativeDetails'), 'Replication layout native details')


def _replication_snapshot(value):
    _replication_object(value, 'Replication snapshot reference')
    if value.get('schema') != 'cdeadmin.replication-snapshot-ref.v1':
        raise ProjectAssetError('Replication snapshot schema is invalid')
    for field in ('id', 'topologyId', 'capturedAt'):
        _text(value.get(field), f'Replication snapshot {field}', 8192)
    _replication_reference(
        value.get('reference'), 'Replication snapshot reference')
    _replication_object(
        value.get('nativeDetails'), 'Replication snapshot native details')


def _replication_content(value):
    if (not isinstance(value, dict) or
            value.get('schema') != REPLICATION_SCHEMA):
        raise ProjectAssetError('Replication asset schema is invalid')
    if (value.get('schemaVersion') != 1 or
            value.get('moduleId') != 'cdeadmin.replication'):
        raise ProjectAssetError(
            'Replication asset version or module is invalid')
    for field in ('name', 'description'):
        if not isinstance(value.get(field), str):
            raise ProjectAssetError(
                f'Replication definition {field} must be text')
    topology_ids = _replication_unique(
        value.get('savedTopologies'), 'Replication topologies',
        _replication_topology,
    )
    _replication_unique(
        value.get('alertPolicies'), 'Replication alert policies',
        _replication_alert,
    )
    _replication_unique(
        value.get('failoverPlans'), 'Replication failover plans',
        _replication_failover,
    )
    _replication_unique(
        value.get('visualLayouts'), 'Replication visual layouts',
        _replication_layout,
    )
    _replication_unique(
        value.get('snapshotRefs'), 'Replication snapshot references',
        _replication_snapshot,
    )
    for collection in (
            value.get('alertPolicies', []), value.get('failoverPlans', []),
            value.get('visualLayouts', []), value.get('snapshotRefs', [])):
        if any(item.get('topologyId') not in topology_ids
               for item in collection):
            raise ProjectAssetError(
                'Replication item references an unknown topology')
    topology_index = {
        item['id']: item for item in value.get('savedTopologies', [])
    }
    for plan in value.get('failoverPlans', []):
        participants = {
            item['id']
            for item in topology_index[plan['topologyId']]['participants']
        }
        if plan.get('candidateParticipantId') not in participants:
            raise ProjectAssetError(
                'Replication failover plan references an unknown candidate')
    for layout in value.get('visualLayouts', []):
        participants = {
            item['id']
            for item in topology_index[layout['topologyId']]['participants']
        }
        if any(identity not in participants
               for identity in layout.get('positions', {})):
            raise ProjectAssetError(
                'Replication layout references an unknown participant')
    _replication_object(value.get('extensions'), 'Replication extensions')
    _secret_free(value, 'Replication asset content')
    return value


def _tracing_object(value, label):
    if not isinstance(value, dict):
        raise ProjectAssetError(f'{label} must be an object')
    return value


def _tracing_reference(value, label):
    _tracing_object(value, label)
    schema = value.get('schema')
    if schema == 'cdeadmin.resource-ref.v1':
        _text(value.get('canonical'), f'{label} canonical identity', 8192)
        _text(value.get('provider') or value.get('providerId'),
              f'{label} provider', 1024)
    elif schema == 'cdeadmin.asset-ref.v1':
        _text(value.get('projectId'), f'{label} project ID', 1024)
        _text(value.get('assetId'), f'{label} asset ID', 1024)
    elif schema in (
            'cdeadmin.external-ref.v1', 'cdeadmin.result-ref.v1',
            'cdeadmin.task-ref.v1', 'cdeadmin.query-ref.v1'):
        _text(value.get('id'), f'{label} identity', 8192)
    else:
        raise ProjectAssetError(f'{label} schema is unsupported')


def _tracing_unique(values, label, validator, maximum=10000):
    if not isinstance(values, list) or len(values) > maximum:
        raise ProjectAssetError(
            f'{label} must be a bounded list')
    identities = set()
    for item in values:
        validator(item)
        identity = item.get('id')
        if identity in identities:
            raise ProjectAssetError(f'{label} IDs must be unique')
        identities.add(identity)
    return identities


def _tracing_saved_search(value):
    _tracing_object(value, 'Tracing saved search')
    if value.get('schema') != 'cdeadmin.tracing-saved-search.v1':
        raise ProjectAssetError('Tracing saved search schema is invalid')
    for field in ('id', 'name'):
        _text(value.get(field), f'Tracing saved search {field}', 1024)
    if not isinstance(value.get('description'), str):
        raise ProjectAssetError(
            'Tracing saved search description must be text')
    for field in ('filters', 'sort'):
        _tracing_object(value.get(field), f'Tracing saved search {field}')
    page_size = value.get('pageSize')
    if (isinstance(page_size, bool) or not isinstance(page_size, int) or
            not 1 <= page_size <= 1000):
        raise ProjectAssetError(
            'Tracing saved search pageSize must be from one through 1000')


def _tracing_saved_view(value):
    _tracing_object(value, 'Tracing saved view')
    if value.get('schema') != 'cdeadmin.tracing-saved-view.v1':
        raise ProjectAssetError('Tracing saved view schema is invalid')
    for field in ('id', 'name'):
        _text(value.get(field), f'Tracing saved view {field}', 1024)
    if value.get('kind') not in TRACING_VIEW_KINDS:
        raise ProjectAssetError('Tracing saved view kind is invalid')
    if value.get('searchId') is not None:
        _text(value['searchId'], 'Tracing saved view search ID', 1024)
    for field in ('layout',):
        _tracing_object(value.get(field), f'Tracing saved view {field}')
    if value.get('observedWindow') is not None:
        _tracing_object(
            value['observedWindow'], 'Tracing saved view observed window')
    if not isinstance(value.get('description'), str):
        raise ProjectAssetError(
            'Tracing saved view description must be text')


def _tracing_source(value):
    _tracing_object(value, 'Trace source configuration')
    if value.get('schema') != 'cdeadmin.trace-source.v1':
        raise ProjectAssetError('Trace source schema is invalid')
    for field in ('id', 'name'):
        _text(value.get(field), f'Trace source {field}', 1024)
    source_type = value.get('sourceType')
    if source_type not in TRACING_SOURCE_TYPES:
        raise ProjectAssetError('Trace source type is invalid')
    if not isinstance(value.get('enabled'), bool):
        raise ProjectAssetError('Trace source enabled must be boolean')
    for field in ('endpoint', 'providerId', 'sensitivityPolicyId'):
        if value.get(field) is not None:
            _text(value[field], f'Trace source {field}', 8192)
    if source_type in ('otlp_grpc', 'otlp_http') and not value.get('endpoint'):
        raise ProjectAssetError('OTLP trace source requires an endpoint')
    if source_type == 'provider_native' and (
            not value.get('providerId') or value.get('resourceRef') is None):
        raise ProjectAssetError(
            'Provider-native trace source requires provider and resource')
    if value.get('resourceRef') is not None:
        _tracing_reference(value['resourceRef'], 'Trace source resource')
    if value.get('credentialRef') is not None:
        reference = _tracing_object(
            value['credentialRef'], 'Trace source credential reference')
        if reference.get('schema') != 'cdeadmin.credential-ref.v1':
            raise ProjectAssetError(
                'Trace source credential reference schema is invalid')
        _text(reference.get('id'), 'Trace source credential ID', 4096)
    for field in ('transport', 'nativeDetails'):
        _tracing_object(value.get(field), f'Trace source {field}')


def _tracing_sampling(value):
    _tracing_object(value, 'Tracing sampling policy')
    if value.get('schema') != 'cdeadmin.tracing-sampling-policy.v1':
        raise ProjectAssetError('Tracing sampling policy schema is invalid')
    for field in ('id', 'name'):
        _text(value.get(field), f'Tracing sampling policy {field}', 1024)
    rate = value.get('rate')
    if (isinstance(rate, bool) or not isinstance(rate, (int, float)) or
            not math.isfinite(rate) or not 0 <= rate <= 1):
        raise ProjectAssetError(
            'Tracing sampling rate must be a finite number from zero to one')
    if not isinstance(value.get('enabled'), bool):
        raise ProjectAssetError('Tracing sampling enabled must be boolean')
    for field in ('tailCriteria', 'sensitiveAttributePolicy', 'retention'):
        _tracing_object(value.get(field), f'Tracing sampling {field}')


def _tracing_content(value):
    if (not isinstance(value, dict) or
            value.get('schema') != TRACING_SCHEMA):
        raise ProjectAssetError('Tracing asset schema is invalid')
    if (value.get('schemaVersion') != 1 or
            value.get('moduleId') != 'cdeadmin.tracing'):
        raise ProjectAssetError(
            'Tracing asset version or module is invalid')
    for field in ('name', 'description'):
        if not isinstance(value.get(field), str):
            raise ProjectAssetError(
                f'Tracing definition {field} must be text')
    search_ids = _tracing_unique(
        value.get('savedSearches'), 'Tracing saved searches',
        _tracing_saved_search,
    )
    _tracing_unique(
        value.get('savedViews'), 'Tracing saved views',
        _tracing_saved_view,
    )
    source_ids = _tracing_unique(
        value.get('sourceConfigs'), 'Trace source configurations',
        _tracing_source,
    )
    policy_ids = _tracing_unique(
        value.get('samplingPolicies'), 'Tracing sampling policies',
        _tracing_sampling,
    )
    for view in value.get('savedViews', []):
        if (view.get('searchId') is not None and
                view['searchId'] not in search_ids):
            raise ProjectAssetError(
                'Tracing saved view references an unknown search')
    for source in value.get('sourceConfigs', []):
        if (source.get('sensitivityPolicyId') is not None and
                source['sensitivityPolicyId'] not in policy_ids):
            raise ProjectAssetError(
                'Trace source references an unknown sensitivity policy')
    if value.get('retentionPolicyRef') is not None:
        _tracing_reference(
            value['retentionPolicyRef'],
            'Tracing retention policy reference',
        )
    _tracing_object(value.get('extensions'), 'Tracing extensions')
    if len(source_ids) > 10000:
        raise ProjectAssetError('Trace source count exceeds its limit')
    _secret_free(value, 'Tracing asset content')
    return value


def _migration_object(value, label):
    if not isinstance(value, dict):
        raise ProjectAssetError(f'{label} must be an object')
    return value


def _migration_exact(value, fields, label):
    unknown = [field for field in value if field not in fields]
    if unknown:
        raise ProjectAssetError(
            f'{label} contains unsupported field {unknown[0]}')


def _migration_reference(value, label, optional=False):
    if value is None and optional:
        return
    _migration_object(value, label)
    schema = value.get('schema')
    if schema == 'cdeadmin.resource-ref.v1':
        _text(value.get('canonical'), f'{label} canonical identity', 8192)
        _text(value.get('provider') or value.get('providerId'),
              f'{label} provider', 1024)
    elif schema == 'cdeadmin.asset-ref.v1':
        _text(value.get('projectId'), f'{label} project ID', 1024)
        _text(value.get('assetId'), f'{label} asset ID', 1024)
    elif schema in (
            'cdeadmin.external-ref.v1', 'cdeadmin.task-ref.v1',
            'cdeadmin.result-ref.v1'):
        _text(value.get('id'), f'{label} identity', 8192)
    else:
        raise ProjectAssetError(f'{label} schema is unsupported')


def _migration_list(value, label, maximum=10000):
    if not isinstance(value, list) or len(value) > maximum:
        raise ProjectAssetError(f'{label} must be a bounded list')
    return value


def _migration_unique(value, label, validator):
    identities = set()
    for item in _migration_list(value, label):
        validator(item)
        identity = item.get('id')
        if identity in identities:
            raise ProjectAssetError(f'{label} IDs must be unique')
        identities.add(identity)
    return identities


def _migration_dependency_order(items, label):
    identities = {item['id'] for item in items}
    incoming = {}
    outgoing = {identity: [] for identity in identities}
    positions = {item['id']: index for index, item in enumerate(items)}
    for item in items:
        dependencies = item.get('dependencies', [])
        if item['id'] in dependencies:
            raise ProjectAssetError(
                f'{label} {item["id"]} cannot depend on itself')
        missing = [dependency for dependency in dependencies
                   if dependency not in identities]
        if missing:
            raise ProjectAssetError(
                f'{label} dependency is unavailable: {missing[0]}')
        incoming[item['id']] = len(dependencies)
        for dependency in dependencies:
            outgoing[dependency].append(item['id'])
    queue = sorted((identity for identity, count in incoming.items()
                    if count == 0), key=positions.get)
    ordered = []
    while queue:
        identity = queue.pop(0)
        ordered.append(identity)
        for dependent in sorted(outgoing[identity], key=positions.get):
            incoming[dependent] -= 1
            if incoming[dependent] == 0:
                queue.append(dependent)
                queue.sort(key=positions.get)
    if len(ordered) != len(items):
        raise ProjectAssetError(f'{label} contains a dependency cycle')
    return ordered


def _migration_finding(value):
    _migration_object(value, 'Migration finding')
    if value.get('schema') != 'cdeadmin.migration-finding.v1':
        raise ProjectAssetError('Migration finding schema is invalid')
    _migration_exact(value, (
        'schema', 'id', 'sourceRef', 'targetRef', 'objectKind', 'category',
        'evidenceSource', 'evidence', 'message', 'blocker', 'waived',
        'waiverRef', 'nativeDetails'), 'Migration finding')
    for field in ('id', 'objectKind', 'message'):
        _text(value.get(field), f'Migration finding {field}', 16384)
    _migration_reference(value.get('sourceRef'), 'Migration finding source')
    _migration_reference(
        value.get('targetRef'), 'Migration finding target', optional=True)
    if value.get('category') not in MIGRATION_ASSESSMENT_CATEGORIES:
        raise ProjectAssetError('Migration finding category is invalid')
    if value.get('evidenceSource') not in MIGRATION_EVIDENCE_SOURCES:
        raise ProjectAssetError('Migration finding evidence source is invalid')
    for field in ('blocker', 'waived'):
        if not isinstance(value.get(field), bool):
            raise ProjectAssetError(
                f'Migration finding {field} must be boolean')
    if value.get('waiverRef') is not None:
        _text(value['waiverRef'], 'Migration finding waiver', 4096)
    for field in ('evidence', 'nativeDetails'):
        _migration_object(value.get(field), f'Migration finding {field}')


def _migration_assessment(value):
    if value is None:
        return
    _migration_object(value, 'Migration assessment')
    if value.get('schema') != 'cdeadmin.migration-assessment.v1':
        raise ProjectAssetError('Migration assessment schema is invalid')
    _migration_exact(value, (
        'schema', 'id', 'sourceRevision', 'targetRevision', 'findings',
        'estimates', 'evidence', 'completedAt', 'nativeDetails'),
        'Migration assessment')
    for field in (
            'id', 'sourceRevision', 'targetRevision', 'completedAt'):
        _text(value.get(field), f'Migration assessment {field}', 4096)
    _migration_unique(value.get('findings'), 'Migration findings',
                      _migration_finding)
    for field in ('estimates', 'evidence', 'nativeDetails'):
        _migration_object(value.get(field), f'Migration assessment {field}')


def _migration_mapping(value):
    _migration_object(value, 'Migration mapping')
    if value.get('schema') != 'cdeadmin.migration-mapping.v1':
        raise ProjectAssetError('Migration mapping schema is invalid')
    _migration_exact(value, (
        'schema', 'id', 'sourceRef', 'targetRef', 'mappingKind', 'category',
        'decision', 'sourceNativeType', 'targetNativeType', 'expression',
        'lossy', 'lossAcknowledged', 'behaviorChange', 'evidence',
        'nativeDetails'), 'Migration mapping')
    for field in ('id', 'mappingKind'):
        _text(value.get(field), f'Migration mapping {field}', 4096)
    _migration_reference(value.get('sourceRef'), 'Migration mapping source')
    _migration_reference(
        value.get('targetRef'), 'Migration mapping target', optional=True)
    if value.get('category') not in MIGRATION_ASSESSMENT_CATEGORIES:
        raise ProjectAssetError('Migration mapping category is invalid')
    if value.get('decision') not in (
            'unresolved', 'accepted', 'rejected', 'manual'):
        raise ProjectAssetError('Migration mapping decision is invalid')
    for field in ('lossy', 'lossAcknowledged', 'behaviorChange'):
        if not isinstance(value.get(field), bool):
            raise ProjectAssetError(
                f'Migration mapping {field} must be boolean')
    for field in ('evidence', 'nativeDetails'):
        _migration_object(value.get(field), f'Migration mapping {field}')


def _migration_schema_operation(value):
    _migration_object(value, 'Migration schema operation')
    if value.get('schema') != 'cdeadmin.migration-schema-operation.v1':
        raise ProjectAssetError(
            'Migration schema operation schema is invalid')
    _migration_exact(value, (
        'schema', 'id', 'action', 'sourceRef', 'targetRef', 'dependencies',
        'preconditions', 'rollback', 'risk', 'nativeDefinition'),
        'Migration schema operation')
    for field in ('id', 'action', 'risk'):
        _text(value.get(field), f'Migration schema operation {field}', 4096)
    _migration_reference(
        value.get('sourceRef'), 'Migration schema operation source',
        optional=True,
    )
    _migration_reference(
        value.get('targetRef'), 'Migration schema operation target')
    for field in ('dependencies', 'preconditions', 'rollback'):
        _migration_list(value.get(field),
                        f'Migration schema operation {field}')
    for dependency in value.get('dependencies'):
        _text(dependency, 'Migration schema dependency', 4096)
    _migration_object(value.get('nativeDefinition'),
                      'Migration schema native definition')


def _migration_schema_plan(value):
    if value is None:
        return
    _migration_object(value, 'Migration schema plan')
    if value.get('schema') != 'cdeadmin.migration-schema-plan.v1':
        raise ProjectAssetError('Migration schema plan schema is invalid')
    _migration_exact(value, (
        'schema', 'id', 'operations', 'schemaCompareRef',
        'expectedTargetRevision', 'nativeDetails'), 'Migration schema plan')
    _text(value.get('id'), 'Migration schema plan ID', 4096)
    operation_ids = _migration_unique(
        value.get('operations'), 'Migration schema operations',
        _migration_schema_operation,
    )
    for operation in value.get('operations'):
        if any(item not in operation_ids
               for item in operation.get('dependencies')):
            raise ProjectAssetError(
                'Migration schema dependency is unavailable')
    _migration_reference(
        value.get('schemaCompareRef'), 'Migration Schema Comparison plan',
        optional=True,
    )
    _migration_object(value.get('nativeDetails'),
                      'Migration schema plan native details')


def _migration_copy_unit(value):
    _migration_object(value, 'Migration copy unit')
    if value.get('schema') != 'cdeadmin.migration-copy-unit.v1':
        raise ProjectAssetError('Migration copy unit schema is invalid')
    _migration_exact(value, (
        'schema', 'id', 'sourceRef', 'targetRef', 'partition',
        'orderingKey', 'batchSize', 'parallelism', 'restartPolicy',
        'transformRef', 'nativeDetails'), 'Migration copy unit')
    _text(value.get('id'), 'Migration copy unit ID', 4096)
    _migration_reference(value.get('sourceRef'), 'Migration copy source')
    _migration_reference(value.get('targetRef'), 'Migration copy target')
    for field in ('batchSize', 'parallelism'):
        number = value.get(field)
        if (isinstance(number, bool) or not isinstance(number, int) or
                number < 1):
            raise ProjectAssetError(
                f'Migration copy {field} must be a positive integer')
    for field in ('partition', 'nativeDetails'):
        _migration_object(value.get(field), f'Migration copy {field}')
    for key in _migration_list(
            value.get('orderingKey'), 'Migration copy ordering keys'):
        _text(key, 'Migration copy ordering key', 4096)
    _text(value.get('restartPolicy'),
          'Migration copy restart policy', 4096)
    _migration_reference(
        value.get('transformRef'), 'Migration copy transform', optional=True)


def _migration_data_move(value):
    _migration_object(value, 'Migration data move plan')
    if value.get('schema') != 'cdeadmin.migration-data-move-plan.v1':
        raise ProjectAssetError('Migration data move plan schema is invalid')
    _migration_exact(value, (
        'schema', 'id', 'units', 'concurrency', 'consistencyBoundary',
        'errorPolicy', 'nativeDetails'), 'Migration data move plan')
    _text(value.get('id'), 'Migration data move plan ID', 4096)
    _migration_unique(
        value.get('units'), 'Migration copy units', _migration_copy_unit)
    concurrency = value.get('concurrency')
    if (isinstance(concurrency, bool) or not isinstance(concurrency, int) or
            concurrency < 1):
        raise ProjectAssetError(
            'Migration data move concurrency must be positive')
    for field in ('consistencyBoundary', 'errorPolicy', 'nativeDetails'):
        _migration_object(value.get(field),
                          f'Migration data move {field}')


def _migration_verification(value):
    _migration_object(value, 'Migration verification')
    if value.get('schema') != 'cdeadmin.migration-verification.v1':
        raise ProjectAssetError('Migration verification schema is invalid')
    _migration_exact(value, (
        'schema', 'id', 'type', 'sourceRef', 'targetRef',
        'canonicalization', 'policy', 'blocking', 'qualityAssetRef',
        'nativeDetails'), 'Migration verification')
    _text(value.get('id'), 'Migration verification ID', 4096)
    if value.get('type') not in MIGRATION_VERIFICATION_TYPES:
        raise ProjectAssetError('Migration verification type is invalid')
    if not isinstance(value.get('blocking'), bool):
        raise ProjectAssetError(
            'Migration verification blocking must be boolean')
    _migration_reference(value.get('sourceRef'),
                         'Migration verification source')
    _migration_reference(value.get('targetRef'),
                         'Migration verification target')
    if (value.get('type') == 'deterministic_hashes' and
            not isinstance(value.get('canonicalization'), dict)):
        raise ProjectAssetError(
            'Migration hash verification requires canonicalization')
    if value.get('canonicalization') is not None:
        _migration_object(value['canonicalization'],
                          'Migration verification canonicalization')
    _migration_object(value.get('policy'), 'Migration verification policy')
    _migration_reference(
        value.get('qualityAssetRef'), 'Migration quality asset',
        optional=True,
    )
    _migration_object(value.get('nativeDetails'),
                      'Migration verification native details')


def _migration_runbook_step(value, label):
    _migration_object(value, label)
    if value.get('schema') != 'cdeadmin.migration-runbook-step.v1':
        raise ProjectAssetError(f'{label} schema is invalid')
    _migration_exact(value, (
        'schema', 'id', 'label', 'action', 'dependencies', 'preconditions',
        'expectedEvidence', 'providerMutation', 'nativeDetails'), label)
    for field in ('id', 'label', 'action'):
        _text(value.get(field), f'{label} {field}', 16384)
    for field in ('dependencies', 'preconditions'):
        _migration_list(value.get(field), f'{label} {field}')
    for dependency in value.get('dependencies'):
        _text(dependency, f'{label} dependency', 4096)
    if not isinstance(value.get('providerMutation'), bool):
        raise ProjectAssetError(
            f'{label} providerMutation must be boolean')
    for field in ('expectedEvidence', 'nativeDetails'):
        _migration_object(value.get(field), f'{label} {field}')


def _migration_cutover(value):
    if value is None:
        return
    _migration_object(value, 'Migration cutover plan')
    if value.get('schema') != 'cdeadmin.migration-cutover-plan.v1':
        raise ProjectAssetError('Migration cutover plan schema is invalid')
    _migration_exact(value, (
        'schema', 'id', 'steps', 'cdcLagThreshold', 'armWindowMinutes',
        'targetEnvironment', 'targetConnection', 'endpointChange',
        'postChecks', 'nativeDetails'), 'Migration cutover plan')
    _text(value.get('id'), 'Migration cutover plan ID', 4096)
    _migration_unique(
        value.get('steps'), 'Migration cutover steps',
        lambda item: _migration_runbook_step(
            item, 'Migration cutover step'),
    )
    _migration_dependency_order(
        value.get('steps'), 'Migration cutover runbook')
    if not value.get('steps'):
        raise ProjectAssetError(
            'Migration cutover plan requires at least one step')
    arm_window = value.get('armWindowMinutes')
    if (isinstance(arm_window, bool) or not isinstance(arm_window, int) or
            not 1 <= arm_window <= 1440):
        raise ProjectAssetError(
            'Migration cutover arm window is invalid')
    lag = value.get('cdcLagThreshold')
    if (lag is not None and (isinstance(lag, bool) or not isinstance(
            lag, (int, float)) or not math.isfinite(lag) or lag < 0)):
        raise ProjectAssetError(
            'Migration cutover CDC lag threshold is invalid')
    for field in ('targetEnvironment', 'targetConnection'):
        _text(value.get(field), f'Migration cutover {field}', 4096)
    _migration_object(value.get('endpointChange'),
                      'Migration cutover endpoint change')
    _migration_list(value.get('postChecks'),
                    'Migration post-cutover checks')
    _migration_object(value.get('nativeDetails'),
                      'Migration cutover native details')


def _migration_rollback(value):
    if value is None:
        return
    _migration_object(value, 'Migration rollback plan')
    if value.get('schema') != 'cdeadmin.migration-rollback-plan.v1':
        raise ProjectAssetError('Migration rollback plan schema is invalid')
    _migration_exact(value, (
        'schema', 'id', 'classification', 'pointOfNoReturn', 'deadline',
        'conditions', 'steps', 'recoverability', 'nativeDetails'),
        'Migration rollback plan')
    _text(value.get('id'), 'Migration rollback plan ID', 4096)
    if value.get('classification') not in MIGRATION_ROLLBACK_CLASSES:
        raise ProjectAssetError(
            'Migration rollback classification is invalid')
    for field in ('pointOfNoReturn', 'deadline'):
        if value.get(field) is not None:
            _text(value[field], f'Migration rollback {field}', 4096)
    if value.get('deadline') is not None:
        try:
            datetime.fromisoformat(value['deadline'].replace('Z', '+00:00'))
        except ValueError as exc:
            raise ProjectAssetError(
                'Migration rollback deadline must be an ISO date-time') \
                from exc
    _migration_list(value.get('conditions'),
                    'Migration rollback conditions')
    _migration_unique(
        value.get('steps'), 'Migration rollback steps',
        lambda item: _migration_runbook_step(
            item, 'Migration rollback step'),
    )
    _migration_dependency_order(
        value.get('steps'), 'Migration rollback runbook')
    if not value.get('steps'):
        raise ProjectAssetError(
            'Migration rollback plan requires at least one step')
    for field in ('recoverability', 'nativeDetails'):
        _migration_object(value.get(field), f'Migration rollback {field}')


def _migration_content(value):
    if (not isinstance(value, dict) or
            value.get('schema') != MIGRATION_SCHEMA):
        raise ProjectAssetError('Migration asset schema is invalid')
    if (value.get('schemaVersion') != 1 or
            value.get('moduleId') != 'cdeadmin.migration'):
        raise ProjectAssetError(
            'Migration asset version or module is invalid')
    _migration_exact(value, (
        'schema', 'schemaVersion', 'moduleId', 'name', 'description',
        'sourceBinding', 'targetBinding', 'strategy', 'phase', 'assessment',
        'mappingSet', 'schemaPlan', 'dataMovePlan', 'cdcPlanRef',
        'validationPlan', 'cutoverPlan', 'rollbackPlan', 'waivers',
        'extensions'), 'Migration asset content')
    for field in ('name', 'description'):
        if not isinstance(value.get(field), str):
            raise ProjectAssetError(
                f'Migration definition {field} must be text')
    strategy = value.get('strategy')
    if (strategy not in MIGRATION_STRATEGIES and
            not str(strategy).startswith('native:')):
        raise ProjectAssetError('Migration strategy is invalid')
    if value.get('phase') not in MIGRATION_PHASES:
        raise ProjectAssetError('Migration phase is invalid')
    _migration_reference(
        value.get('sourceBinding'), 'Migration source', optional=True)
    _migration_reference(
        value.get('targetBinding'), 'Migration target', optional=True)
    if (value.get('phase') != 'discover' and
            (value.get('sourceBinding') is None or
             value.get('targetBinding') is None)):
        raise ProjectAssetError(
            'Configured migration phases require source and target')
    _migration_assessment(value.get('assessment'))
    _migration_unique(
        value.get('mappingSet'), 'Migration mappings',
        _migration_mapping,
    )
    _migration_schema_plan(value.get('schemaPlan'))
    _migration_data_move(value.get('dataMovePlan'))
    _migration_reference(
        value.get('cdcPlanRef'), 'Migration CDC plan', optional=True)
    if strategy == 'online_with_cdc' and value.get('cdcPlanRef') is None:
        raise ProjectAssetError(
            'Online migration requires a CDC plan reference')
    _migration_unique(
        value.get('validationPlan'), 'Migration verifications',
        _migration_verification,
    )
    _migration_cutover(value.get('cutoverPlan'))
    _migration_rollback(value.get('rollbackPlan'))
    point = (value.get('rollbackPlan') or {}).get('pointOfNoReturn')
    if point and point not in {
            item['id'] for item in
            (value.get('cutoverPlan') or {}).get('steps', [])}:
        raise ProjectAssetError(
            'Migration point of no return must identify a cutover step')
    _migration_list(value.get('waivers'), 'Migration waivers')
    _migration_object(value.get('extensions'), 'Migration extensions')
    _secret_free(value, 'Migration asset content')
    return value


def _api_object(value, label):
    if not isinstance(value, dict):
        raise ProjectAssetError(f'{label} must be an object')
    return value


def _api_exact(value, fields, label):
    unknown = [field for field in value if field not in fields]
    if unknown:
        raise ProjectAssetError(
            f'{label} contains unsupported field {unknown[0]}')


def _api_list(value, label, maximum=10000):
    if not isinstance(value, list) or len(value) > maximum:
        raise ProjectAssetError(f'{label} must be a bounded list')
    return value


def _api_scalar_map(value, label):
    _api_object(value, label)
    for key, item in value.items():
        _text(key, f'{label} key', 1024)
        if (not isinstance(item, (str, int, float, bool)) or
                isinstance(item, float) and not math.isfinite(item)):
            raise ProjectAssetError(f'{label} values must be scalar')
    _secret_free(value, label)


def _api_unique(value, label, validator):
    identities = set()
    for item in _api_list(value, label):
        validator(item)
        identity = item.get('id')
        if identity in identities:
            raise ProjectAssetError(f'{label} IDs must be unique')
        identities.add(identity)
    return identities


def _api_extensions(value, label):
    def validate(item):
        _api_object(item, f'{label} extension')
        _api_exact(item, ('id', 'name', 'value'),
                   f'{label} extension')
        name = _text(item.get('name'), f'{label} extension name', 1024)
        if not name.startswith('x-'):
            raise ProjectAssetError(
                f'{label} extension names must begin with x-')
        _text(item.get('id'), f'{label} extension ID', 1024)
        _secret_free(item.get('value'), f'{label} extension value')

    _api_unique(value, f'{label} extensions', validate)


def _api_reference(value, label, schemas=None, optional=False):
    if value is None and optional:
        return
    _api_object(value, label)
    schema = value.get('schema')
    allowed = schemas or {
        'cdeadmin.resource-ref.v1', 'cdeadmin.asset-ref.v1',
        'cdeadmin.external-ref.v1', 'cdeadmin.credential-ref.v1',
    }
    if schema not in allowed:
        raise ProjectAssetError(f'{label} schema is unsupported')
    if schema == 'cdeadmin.resource-ref.v1':
        _text(value.get('canonical'), f'{label} canonical identity', 8192)
        _text(value.get('providerId') or value.get('provider'),
              f'{label} provider', 1024)
    elif schema == 'cdeadmin.asset-ref.v1':
        _text(value.get('projectId'), f'{label} project ID', 1024)
        _text(value.get('assetId'), f'{label} asset ID', 1024)
    else:
        _text(value.get('id'), f'{label} identity', 8192)
    if schema == 'cdeadmin.credential-ref.v1':
        _api_exact(value, (
            'schema', 'id', 'providerId', 'scope', 'displayName'), label)
    _secret_free(value, label)


def _api_binding(value):
    _api_object(value, 'API data binding')
    if value.get('schema') != 'cdeadmin.api-binding.v1':
        raise ProjectAssetError('API data binding schema is invalid')
    _api_exact(value, (
        'schema', 'id', 'type', 'targetRef', 'mode', 'queryAssetRef',
        'command', 'inputMap', 'outputMap', 'nativeDetails', 'extensions'),
        'API data binding')
    _text(value.get('id'), 'API data binding ID', 1024)
    if value.get('type') not in API_BINDING_TYPES:
        raise ProjectAssetError('API binding type is invalid')
    if value.get('mode') not in API_BINDING_MODES:
        raise ProjectAssetError('API binding mode is invalid')
    _api_reference(value.get('targetRef'), 'API binding target')
    _api_reference(
        value.get('queryAssetRef'), 'API query asset',
        {'cdeadmin.asset-ref.v1'}, optional=True)
    if value.get('command') is not None:
        _text(value['command'], 'API provider command', 8192)
    for field in ('inputMap', 'outputMap', 'nativeDetails'):
        _api_object(value.get(field), f'API binding {field}')
    _api_extensions(value.get('extensions'), 'API binding')


def _api_parameter(value):
    _api_object(value, 'API parameter')
    if value.get('schema') != 'cdeadmin.api-parameter.v1':
        raise ProjectAssetError('API parameter schema is invalid')
    _api_exact(value, (
        'schema', 'id', 'name', 'location', 'required', 'description',
        'schemaRef', 'definition', 'extensions'), 'API parameter')
    for field in ('id', 'name'):
        _text(value.get(field), f'API parameter {field}', 1024)
    if value.get('location') not in {
            'path', 'query', 'header', 'cookie', 'body',
            'message_header'}:
        raise ProjectAssetError('API parameter location is invalid')
    if not isinstance(value.get('required'), bool):
        raise ProjectAssetError('API parameter required must be boolean')
    if value.get('schemaRef') is not None:
        _text(value['schemaRef'], 'API parameter schema reference', 4096)
    _api_object(value.get('definition'), 'API parameter definition')
    _api_extensions(value.get('extensions'), 'API parameter')


def _api_response(value):
    _api_object(value, 'API response')
    if value.get('schema') != 'cdeadmin.api-response.v1':
        raise ProjectAssetError('API response schema is invalid')
    _api_exact(value, (
        'schema', 'id', 'status', 'description', 'schemaRef', 'headers',
        'examples', 'extensions'), 'API response')
    for field in ('id', 'status'):
        _text(value.get(field), f'API response {field}', 1024)
    if value.get('schemaRef') is not None:
        _text(value['schemaRef'], 'API response schema reference', 4096)
    _api_object(value.get('headers'), 'API response headers')
    _api_object(value.get('examples'), 'API response examples')
    _api_extensions(value.get('extensions'), 'API response')


def _api_operation(value):
    _api_object(value, 'API operation')
    if value.get('schema') != 'cdeadmin.api-operation.v1':
        raise ProjectAssetError('API operation schema is invalid')
    _api_exact(value, (
        'schema', 'id', 'name', 'method', 'path', 'summary', 'description',
        'parameters', 'requestSchemaRef', 'responses', 'binding',
        'securityRequirementIds', 'policyIds', 'tags', 'deprecated',
        'idempotent', 'callbacks', 'extensions'), 'API operation')
    for field in ('id', 'name', 'method', 'path'):
        _text(value.get(field), f'API operation {field}', 8192)
    method = value.get('method')
    if method not in API_HTTP_METHODS and not method.startswith('RPC:'):
        raise ProjectAssetError('API operation method is invalid')
    if method in API_HTTP_METHODS and not value['path'].startswith('/'):
        raise ProjectAssetError('HTTP operation path must begin with /')
    _api_unique(value.get('parameters'), 'API parameters', _api_parameter)
    _api_unique(value.get('responses'), 'API responses', _api_response)
    if value.get('requestSchemaRef') is not None:
        _text(value['requestSchemaRef'],
              'API request schema reference', 4096)
    if value.get('binding') is not None:
        _api_binding(value['binding'])
    for field in ('securityRequirementIds', 'policyIds', 'tags'):
        for item in _api_list(value.get(field), f'API operation {field}'):
            _text(item, f'API operation {field} value', 1024)
    for field in ('deprecated', 'idempotent'):
        if not isinstance(value.get(field), bool):
            raise ProjectAssetError(
                f'API operation {field} must be boolean')
    _api_object(value.get('callbacks'), 'API operation callbacks')
    _api_extensions(value.get('extensions'), 'API operation')


def _api_schema(value):
    _api_object(value, 'API schema')
    if value.get('schema') != 'cdeadmin.api-schema.v1':
        raise ProjectAssetError('API schema definition schema is invalid')
    _api_exact(value, (
        'schema', 'id', 'name', 'kind', 'definition', 'fields',
        'physicalBindings', 'description', 'extensions'), 'API schema')
    for field in ('id', 'name', 'kind'):
        _text(value.get(field), f'API schema {field}', 1024)
    _api_object(value.get('definition'), 'API schema definition')

    def validate_field(field):
        _api_object(field, 'API schema field')
        _api_exact(field, (
            'id', 'name', 'definition', 'required', 'description'),
            'API schema field')
        for item in ('id', 'name'):
            _text(field.get(item), f'API schema field {item}', 1024)
        if not isinstance(field.get('required'), bool):
            raise ProjectAssetError(
                'API schema field required must be boolean')
        _api_object(field.get('definition'),
                    'API schema field definition')

    _api_unique(value.get('fields'), 'API schema fields', validate_field)
    for reference in _api_list(
            value.get('physicalBindings'), 'API schema physical bindings'):
        _api_reference(reference, 'API schema physical binding')
    _api_extensions(value.get('extensions'), 'API schema')


def _api_message(value):
    _api_object(value, 'API message')
    if value.get('schema') != 'cdeadmin.api-message.v1':
        raise ProjectAssetError('API message schema is invalid')
    _api_exact(value, (
        'schema', 'id', 'name', 'payloadSchemaRef', 'headerSchemaRef',
        'correlationId', 'contentType', 'bindings', 'extensions'),
        'API message')
    for field in ('id', 'name', 'contentType'):
        _text(value.get(field), f'API message {field}', 4096)
    for field in ('payloadSchemaRef', 'headerSchemaRef', 'correlationId'):
        if value.get(field) is not None:
            _text(value[field], f'API message {field}', 4096)
    _api_object(value.get('bindings'), 'API message bindings')
    _api_extensions(value.get('extensions'), 'API message')


def _api_channel(value):
    _api_object(value, 'API channel')
    if value.get('schema') != 'cdeadmin.api-channel.v1':
        raise ProjectAssetError('API channel schema is invalid')
    _api_exact(value, (
        'schema', 'id', 'name', 'address', 'serverIds', 'messageIds',
        'operations', 'bindings', 'extensions'), 'API channel')
    for field in ('id', 'name', 'address'):
        _text(value.get(field), f'API channel {field}', 8192)
    for field in ('serverIds', 'messageIds'):
        for item in _api_list(value.get(field), f'API channel {field}'):
            _text(item, f'API channel {field} value', 1024)
    _api_object(value.get('operations'), 'API channel operations')
    _api_object(value.get('bindings'), 'API channel bindings')
    _api_extensions(value.get('extensions'), 'API channel')


def _api_security(value):
    _api_object(value, 'API security requirement')
    if value.get('schema') != 'cdeadmin.api-security.v1':
        raise ProjectAssetError('API security schema is invalid')
    _api_exact(value, (
        'schema', 'id', 'name', 'type', 'scheme', 'location',
        'parameterName', 'flows', 'scopes', 'credentialRef',
        'description', 'extensions'), 'API security requirement')
    for field in ('id', 'name', 'type'):
        _text(value.get(field), f'API security {field}', 1024)
    for field in ('scheme', 'location', 'parameterName'):
        if value.get(field) is not None:
            _text(value[field], f'API security {field}', 1024)
    _api_object(value.get('flows'), 'API security flows')
    _api_scalar_map(value.get('scopes'), 'API security scopes')
    _api_reference(
        value.get('credentialRef'), 'API security credential',
        {'cdeadmin.credential-ref.v1'}, optional=True)
    _api_extensions(value.get('extensions'), 'API security')


def _api_server(value):
    _api_object(value, 'API server')
    if value.get('schema') != 'cdeadmin.api-server.v1':
        raise ProjectAssetError('API server schema is invalid')
    _api_exact(value, (
        'schema', 'id', 'name', 'environment', 'urlTemplate', 'protocol',
        'credentialRef', 'variables', 'tlsPolicyRef', 'description',
        'extensions'), 'API server')
    for field in ('id', 'name', 'environment', 'urlTemplate', 'protocol'):
        _text(value.get(field), f'API server {field}', 8192)
    _api_reference(
        value.get('credentialRef'), 'API server credential',
        {'cdeadmin.credential-ref.v1'}, optional=True)
    _api_reference(
        value.get('tlsPolicyRef'), 'API server TLS policy',
        {'cdeadmin.asset-ref.v1'}, optional=True)
    _api_scalar_map(value.get('variables'), 'API server variables')
    _api_extensions(value.get('extensions'), 'API server')


def _api_simple_entity(value, label, schema, fields):
    _api_object(value, label)
    if value.get('schema') != schema:
        raise ProjectAssetError(f'{label} schema is invalid')
    _api_exact(value, fields, label)
    for field in ('id', 'name'):
        _text(value.get(field), f'{label} {field}', 1024)
    _api_extensions(value.get('extensions'), label)


def _api_content(value):
    if not isinstance(value, dict) or value.get('schema') != API_SCHEMA:
        raise ProjectAssetError('API asset schema is invalid')
    if (value.get('schemaVersion') != 1 or
            value.get('moduleId') != 'cdeadmin.api'):
        raise ProjectAssetError('API asset version or module is invalid')
    _api_exact(value, (
        'schema', 'schemaVersion', 'moduleId', 'profile',
        'externalSpecVersion', 'info', 'servers', 'operations', 'channels',
        'messages', 'schemas', 'security', 'policies', 'tests',
        'deploymentBindings', 'externalReferences', 'extensions'),
        'API asset content')
    if value.get('profile') not in API_PROFILES:
        raise ProjectAssetError('API profile is invalid')
    if value.get('externalSpecVersion') is not None:
        _text(value['externalSpecVersion'],
              'External API specification version', 64)
    _api_object(value.get('info'), 'API information')
    server_ids = _api_unique(value.get('servers'), 'API servers', _api_server)
    operation_ids = _api_unique(
        value.get('operations'), 'API operations', _api_operation)
    channel_ids = _api_unique(
        value.get('channels'), 'API channels', _api_channel)
    message_ids = _api_unique(
        value.get('messages'), 'API messages', _api_message)
    schema_ids = _api_unique(
        value.get('schemas'), 'API schemas', _api_schema)
    security_ids = _api_unique(
        value.get('security'), 'API security requirements', _api_security)

    def policy(item):
        _api_simple_entity(item, 'API policy', 'cdeadmin.api-policy.v1', (
            'schema', 'id', 'name', 'type', 'config', 'extensions'))
        _text(item.get('type'), 'API policy type', 1024)
        _api_object(item.get('config'), 'API policy config')

    policy_ids = _api_unique(value.get('policies'), 'API policies', policy)

    def test(item):
        _api_simple_entity(item, 'API test', 'cdeadmin.api-test.v1', (
            'schema', 'id', 'name', 'operationId', 'channelId',
            'environmentId', 'mode', 'parameters', 'headers', 'body',
            'expected', 'sensitiveHeaderNames', 'extensions'))
        if item.get('mode') not in {'mock', 'example', 'live'}:
            raise ProjectAssetError('API test mode is invalid')
        if item.get('operationId') is not None:
            _text(item['operationId'], 'API test operation ID', 1024)
        if item.get('channelId') is not None:
            _text(item['channelId'], 'API test channel ID', 1024)
        if item.get('environmentId') is not None:
            _text(item['environmentId'], 'API test environment ID', 1024)
        _api_scalar_map(item.get('parameters'), 'API test parameters')
        _api_scalar_map(item.get('headers'), 'API test headers')
        _api_object(item.get('body'), 'API test body')
        _api_object(item.get('expected'), 'API test expectation')
        for name in _api_list(
                item.get('sensitiveHeaderNames'),
                'API sensitive header names'):
            _text(name, 'API sensitive header name', 1024)

    _api_unique(value.get('tests'), 'API tests', test)

    def deployment(item):
        _api_simple_entity(
            item, 'API deployment', 'cdeadmin.api-deployment.v1', (
                'schema', 'id', 'name', 'environment', 'targetRef',
                'gatewayProfile', 'credentialRef', 'config', 'extensions'))
        for field in ('environment', 'gatewayProfile'):
            _text(item.get(field), f'API deployment {field}', 1024)
        _api_reference(item.get('targetRef'), 'API deployment target')
        _api_reference(
            item.get('credentialRef'), 'API deployment credential',
            {'cdeadmin.credential-ref.v1'}, optional=True)
        _api_object(item.get('config'), 'API deployment config')

    _api_unique(
        value.get('deploymentBindings'), 'API deployments', deployment)

    def external(item):
        _api_object(item, 'API external reference')
        _api_exact(item, ('id', 'uri', 'provenance', 'contentDigest'),
                   'API external reference')
        _text(item.get('id'), 'API external reference ID', 1024)
        _text(item.get('uri'), 'API external reference URI', 8192)
        if item.get('contentDigest') is not None:
            _text(item['contentDigest'],
                  'API external reference digest', 256)
        _api_object(item.get('provenance'),
                    'API external reference provenance')

    _api_unique(
        value.get('externalReferences'), 'API external references', external)
    _api_extensions(value.get('extensions'), 'API asset')
    for operation in value.get('operations'):
        if (operation.get('requestSchemaRef') is not None and
                operation['requestSchemaRef'] not in schema_ids):
            raise ProjectAssetError(
                'API operation references an unknown request schema')
        for response in operation.get('responses'):
            if (response.get('schemaRef') is not None and
                    response['schemaRef'] not in schema_ids):
                raise ProjectAssetError(
                    'API response references an unknown schema')
        if any(item not in security_ids for item in
               operation.get('securityRequirementIds')):
            raise ProjectAssetError(
                'API operation references unknown security')
        if any(item not in policy_ids for item in operation.get('policyIds')):
            raise ProjectAssetError('API operation references unknown policy')
    for channel in value.get('channels'):
        if any(item not in server_ids for item in channel.get('serverIds')):
            raise ProjectAssetError('API channel references unknown server')
        if any(item not in message_ids
               for item in channel.get('messageIds')):
            raise ProjectAssetError('API channel references unknown message')
    for message in value.get('messages'):
        for field in ('payloadSchemaRef', 'headerSchemaRef'):
            if (message.get(field) is not None and
                    message[field] not in schema_ids):
                raise ProjectAssetError(
                    'API message references unknown schema')
    for item in value.get('tests'):
        if (item.get('operationId') is not None and
                item['operationId'] not in operation_ids):
            raise ProjectAssetError('API test references unknown operation')
        if (item.get('channelId') is not None and
                item['channelId'] not in channel_ids):
            raise ProjectAssetError('API test references unknown channel')
        if (item.get('environmentId') is not None and
                item['environmentId'] not in server_ids):
            raise ProjectAssetError('API test references unknown server')
    _secret_free(value, 'API asset content')
    return value


def validate_asset_request(value, project_key, asset_key):
    if not isinstance(value, dict):
        raise ProjectAssetError('asset request must be an object')
    asset_type = str(value.get('asset_type') or '').strip()
    schema_name = str(value.get('schema_name') or '').strip()
    content = value.get('content')
    if value.get('schema') == 'cdeadmin.ddn-asset.v1':
        reference = value.get('assetRef') or {}
        if reference.get('projectId') != project_key or (
                reference.get('assetId') != asset_key):
            raise ProjectAssetError('DDN AssetRef does not match its route')
        asset_type = 'ddn-workspace'
        schema_name = 'cdeadmin.ddn-asset.v1'
        content = _ddn_snapshot(value.get('snapshot'))
        value = {
            **value,
            'name': reference.get('displayName'),
            'path': reference.get('path'),
            'expected_version': reference.get('assetVersion'),
            'schema_version': reference.get('schemaVersion'),
            'asset_type': asset_type,
            'schema_name': schema_name,
            'content': content,
        }
    if asset_type == SCHEMA_COMPARE_ASSET_TYPE:
        if schema_name != SCHEMA_COMPARE_ASSET_TYPE:
            raise ProjectAssetError(
                'Schema Comparison asset schema name is invalid'
            )
        content = _schema_compare_content(content)
    if asset_type == LINEAGE_ASSET_TYPE:
        if schema_name != LINEAGE_ASSET_TYPE:
            raise ProjectAssetError(
                'Data Lineage asset schema name is invalid'
            )
        content = _lineage_content(content)
    if asset_type == QUALITY_ASSET_TYPE:
        if schema_name != QUALITY_ASSET_TYPE:
            raise ProjectAssetError(
                'Data Quality asset schema name is invalid'
            )
        content = _quality_content(content)
    if asset_type == CONTRACT_ASSET_TYPE:
        if schema_name != CONTRACT_ASSET_TYPE:
            raise ProjectAssetError(
                'Data Contract asset schema name is invalid'
            )
        content = _contract_content(content)
    if asset_type == ETL_ASSET_TYPE:
        if schema_name != ETL_ASSET_TYPE:
            raise ProjectAssetError('ETL asset schema name is invalid')
        content = _etl_content(content)
    if asset_type == CDC_ASSET_TYPE:
        if schema_name != CDC_ASSET_TYPE:
            raise ProjectAssetError('CDC asset schema name is invalid')
        content = _cdc_content(content)
    if asset_type == REPLICATION_ASSET_TYPE:
        if schema_name != REPLICATION_ASSET_TYPE:
            raise ProjectAssetError(
                'Replication asset schema name is invalid')
        content = _replication_content(content)
    if asset_type == TRACING_ASSET_TYPE:
        if schema_name != TRACING_ASSET_TYPE:
            raise ProjectAssetError(
                'Tracing asset schema name is invalid')
        content = _tracing_content(content)
    if asset_type == MIGRATION_ASSET_TYPE:
        if schema_name != MIGRATION_ASSET_TYPE:
            raise ProjectAssetError(
                'Migration asset schema name is invalid')
        content = _migration_content(content)
    if asset_type == API_ASSET_TYPE:
        if schema_name != API_ASSET_TYPE:
            raise ProjectAssetError('API asset schema name is invalid')
        content = _api_content(content)
    if not SAFE_ASSET_TYPE.fullmatch(asset_type):
        raise ProjectAssetError('asset type is invalid')
    expected = value.get('expected_version')
    if (isinstance(expected, bool) or not isinstance(expected, int) or
            expected < 0):
        raise ProjectAssetError('expected asset version is invalid')
    schema_version = value.get('schema_version', 1)
    if isinstance(schema_version, bool) or not isinstance(
            schema_version, int) or schema_version < 1:
        raise ProjectAssetError('asset schema version is invalid')
    metadata = value.get('metadata') or {}
    dependencies = value.get('dependency_references') or []
    bindings = value.get('resource_bindings') or []
    validation_details = value.get('validation_details') or []
    for candidate, label in (
        (metadata, 'asset metadata'),
        (dependencies, 'dependency references'),
        (bindings, 'resource bindings'),
        (validation_details, 'validation details'),
    ):
        _secret_free(candidate, label)
    _secret_free(content, 'asset content')
    content_text = _json(content, 'asset content', MAX_ASSET_BYTES)
    validation_state = str(
        value.get('validation_state') or 'unknown'
    ).strip()
    if validation_state not in VALIDATION_STATES:
        raise ProjectAssetError('asset validation state is invalid')
    return {
        'asset_type': asset_type,
        'name': _text(value.get('name'), 'asset name', 256),
        'path': _path(value.get('path')),
        'schema_name': _text(schema_name, 'asset schema', 128),
        'schema_version': schema_version,
        'expected_version': expected,
        'content': content_text,
        'asset_metadata': _json(metadata, 'asset metadata'),
        'dependency_references': _json(
            dependencies, 'dependency references'
        ),
        'resource_bindings': _json(bindings, 'resource bindings'),
        'permission_reference': _text(
            value.get('permission_reference'), 'permission reference', 256,
            empty=True,
        ) or None,
        'classification_reference': _text(
            value.get('classification_reference'),
            'classification reference', 256, empty=True,
        ) or None,
        'source_control_eligible': bool(
            value.get('source_control_eligible', True)
        ),
        'editor_capable': bool(value.get('editor_capable', True)),
        'viewer_capable': bool(value.get('viewer_capable', True)),
        'validation_state': validation_state,
        'validation_details': _json(
            validation_details, 'validation details'
        ),
    }


class ProjectAssetRepository:
    def __init__(self):
        from pgadmin.model import (
            CDEProject, CDEProjectAsset, CDEProjectAssetRevision,
            CDEProjectMember, db,
        )
        self.db = db
        self.project_model = CDEProject
        self.asset_model = CDEProjectAsset
        self.revision_model = CDEProjectAssetRevision
        self.member_model = CDEProjectMember

    def project(self, owner_id, project_key):
        return self.project_model.query.filter_by(
            user_id=owner_id, project_key=project_key
        ).first()

    def project_by_key(self, project_key):
        return self.project_model.query.filter_by(
            project_key=project_key
        ).first()

    def accessible_project(self, user_id, role_ids, project_key):
        owned = self.project(user_id, project_key)
        if owned is not None:
            return owned, 'owner'
        memberships = self.member_model.query.join(self.project_model).filter(
            self.project_model.project_key == project_key,
            ((self.member_model.principal_type == 'user') &
             (self.member_model.principal_id == user_id)) |
            ((self.member_model.principal_type == 'role') &
             self.member_model.principal_id.in_(role_ids or [-1])),
        ).all()
        if not memberships:
            return None, None
        membership = max(
            memberships, key=lambda item: ACCESS[item.access_level]
        )
        return membership.project, membership.access_level

    def projects(self, user_id, role_ids):
        owned = self.project_model.query.filter_by(user_id=user_id).all()
        shared = self.member_model.query.join(self.project_model).filter(
            ((self.member_model.principal_type == 'user') &
             (self.member_model.principal_id == user_id)) |
            ((self.member_model.principal_type == 'role') &
             self.member_model.principal_id.in_(role_ids or [-1])),
        ).all()
        result = {item.id: (item, 'owner') for item in owned}
        for membership in shared:
            existing = result.get(membership.project_id)
            if (existing is None or ACCESS[membership.access_level] >
                    ACCESS[existing[1]]):
                result[membership.project_id] = (
                    membership.project, membership.access_level
                )
        return list(result.values())

    def asset(self, project_id, asset_key):
        return self.asset_model.query.filter_by(
            project_id=project_id, asset_key=asset_key
        ).first()

    def new_project(self, **values):
        return self.project_model(**values)

    def new_asset(self, **values):
        return self.asset_model(**values)

    def new_revision(self, **values):
        return self.revision_model(**values)

    def new_member(self, **values):
        return self.member_model(**values)

    def add(self, row):
        self.db.session.add(row)

    def delete(self, row):
        self.db.session.delete(row)

    def commit(self):
        self.db.session.commit()

    def rollback(self):
        self.db.session.rollback()


class ProjectAssetService:
    def __init__(self, repository):
        self.repository = repository

    @staticmethod
    def _require(access, needed):
        if not access or ACCESS[access] < ACCESS[needed]:
            raise ProjectAssetForbidden(
                f'project {needed} access is required'
            )

    def list_projects(self, user_id, role_ids=()):
        return [self._project(row, access) for row, access in
                self.repository.projects(user_id, role_ids)]

    def create_project(self, user_id, project_key, request):
        project_key = _safe_id(project_key, 'project ID')
        if self.repository.project_by_key(project_key):
            raise ProjectAssetConflict('project already exists')
        request = request if isinstance(request, dict) else {}
        row = self.repository.new_project(
            id=str(uuid.uuid4()), user_id=user_id, project_key=project_key,
            name=_text(
                request.get('name') or project_key, 'project name', 128
            ),
            description=_text(
                request.get('description'), 'project description', 65536,
                empty=True,
            ),
            classification_reference=_text(
                request.get('classification_reference'),
                'classification reference', 256, empty=True
            ) or None,
            permission_reference=_text(
                request.get('permission_reference'),
                'permission reference', 256, empty=True
            ) or None,
            source_control_eligible=bool(
                request.get('source_control_eligible', True)
            ), revision=0,
        )
        self.repository.add(row)
        self.repository.commit()
        return self._project(row, 'owner')

    def update_project(self, user_id, role_ids, project_key, request):
        project, access = self._access(
            user_id, role_ids, project_key, 'manager'
        )
        request = request if isinstance(request, dict) else {}
        expected = request.get('expected_revision')
        if isinstance(expected, bool) or not isinstance(expected, int):
            raise ProjectAssetError('expected project revision is invalid')
        if project.revision != expected:
            raise ProjectAssetConflict('project revision has changed')
        if 'name' in request:
            project.name = _text(request['name'], 'project name', 128)
        if 'description' in request:
            project.description = _text(
                request['description'], 'project description', 65536,
                empty=True,
            )
        for field in (
            'classification_reference', 'permission_reference'
        ):
            if field in request:
                setattr(project, field, _text(
                    request[field], field.replace('_', ' '), 256,
                    empty=True,
                ) or None)
        if 'source_control_eligible' in request:
            project.source_control_eligible = bool(
                request['source_control_eligible']
            )
        project.revision += 1
        self.repository.commit()
        return self._project(project, access)

    def delete_project(self, user_id, role_ids, project_key, expected):
        project, _access = self._access(
            user_id, role_ids, project_key, 'manager'
        )
        if project.user_id != user_id:
            raise ProjectAssetForbidden(
                'only the project owner can delete the project'
            )
        if project.revision != expected:
            raise ProjectAssetConflict('project revision has changed')
        self.repository.delete(project)
        self.repository.commit()
        return {'project_id': project.project_key,
                'deleted_revision': expected}

    def project_state(self, user_id, role_ids, project_key):
        row, access = self.repository.accessible_project(
            user_id, role_ids, _safe_id(project_key, 'project ID')
        )
        if row is None:
            raise ProjectAssetNotFound('project was not found')
        return {
            **self._project(row, access),
            'assets': [self._asset(asset, include_content=False,
                                   access=access)
                       for asset in row.assets],
            'members': [self._member(member) for member in row.members]
            if access in ('owner', 'manager') else [],
        }

    def get_asset(self, user_id, role_ids, project_key, asset_key,
                  version=None):
        project, access = self._access(
            user_id, role_ids, project_key, 'viewer'
        )
        asset = self._asset_row(project, asset_key)
        if version is None or version == asset.version:
            result = self._asset(
                asset, include_content=True, access=access
            )
            result['assetRef'] = self._asset_ref(project, asset)
            return result
        revision = next((item for item in asset.revisions
                         if item.version == version), None)
        if revision is None:
            raise ProjectAssetNotFound('asset revision was not found')
        return self._revision(asset, revision, access)

    def save_asset(self, user_id, role_ids, project_key, asset_key, request):
        project, access = self._access(
            user_id, role_ids, project_key, 'editor'
        )
        asset_key = _safe_id(asset_key, 'asset ID')
        values = validate_asset_request(request, project_key, asset_key)
        expected = values.pop('expected_version')
        row = self.repository.asset(project.id, asset_key)
        if row is None:
            if expected != 0:
                raise ProjectAssetConflict('asset version has changed')
            row = self.repository.new_asset(
                id=str(uuid.uuid4()), project_id=project.id,
                user_id=project.user_id, asset_key=asset_key, version=1,
                **values,
            )
            self.repository.add(row)
        else:
            if row.version != expected:
                raise ProjectAssetConflict('asset version has changed')
            row.version += 1
            for key, value in values.items():
                setattr(row, key, value)
        revision = self.repository.new_revision(
            id=str(uuid.uuid4()), asset_id=row.id, user_id=project.user_id,
            version=row.version, schema_name=row.schema_name,
            schema_version=row.schema_version, content=row.content,
            asset_metadata=row.asset_metadata,
            dependency_references=row.dependency_references,
            resource_bindings=row.resource_bindings,
            validation_state=row.validation_state,
            validation_details=row.validation_details,
        )
        self.repository.add(revision)
        project.revision += 1
        self.repository.commit()
        result = self._asset(row, include_content=True, access=access)
        result['assetRef'] = self._asset_ref(project, row)
        return result

    def delete_asset(self, user_id, role_ids, project_key, asset_key,
                     expected):
        project, _access = self._access(
            user_id, role_ids, project_key, 'editor'
        )
        row = self._asset_row(project, asset_key)
        if row.version != expected:
            raise ProjectAssetConflict('asset version has changed')
        self.repository.delete(row)
        project.revision += 1
        self.repository.commit()
        return {'asset_id': asset_key, 'deleted_version': expected}

    def list_revisions(self, user_id, role_ids, project_key, asset_key):
        project, access = self._access(
            user_id, role_ids, project_key, 'viewer'
        )
        asset = self._asset_row(project, asset_key)
        return [self._revision(asset, row, access, include_content=False)
                for row in sorted(asset.revisions,
                                  key=lambda item: item.version,
                                  reverse=True)]

    def set_member(self, user_id, role_ids, project_key, request):
        project, _access = self._access(
            user_id, role_ids, project_key, 'manager'
        )
        if project.user_id != user_id:
            raise ProjectAssetForbidden(
                'only the project owner can change access'
            )
        principal_type = str(request.get('principal_type') or '')
        access_level = str(request.get('access_level') or '')
        principal_id = request.get('principal_id')
        if principal_type not in ('user', 'role') or access_level not in (
                'viewer', 'editor', 'manager') or isinstance(
                    principal_id, bool) or not isinstance(principal_id, int):
            raise ProjectAssetError('project member is invalid')
        existing = next((item for item in project.members
                         if item.principal_type == principal_type and
                         item.principal_id == principal_id), None)
        if existing:
            existing.access_level = access_level
            row = existing
        else:
            row = self.repository.new_member(
                id=str(uuid.uuid4()), project_id=project.id,
                principal_type=principal_type, principal_id=principal_id,
                access_level=access_level,
            )
            self.repository.add(row)
        project.revision += 1
        self.repository.commit()
        return self._member(row)

    def remove_member(self, user_id, role_ids, project_key,
                      principal_type, principal_id):
        project, _access = self._access(
            user_id, role_ids, project_key, 'manager'
        )
        if project.user_id != user_id:
            raise ProjectAssetForbidden(
                'only the project owner can change access'
            )
        if principal_type not in ('user', 'role'):
            raise ProjectAssetError('project member type is invalid')
        row = next((item for item in project.members
                    if item.principal_type == principal_type and
                    item.principal_id == principal_id), None)
        if row is None:
            raise ProjectAssetNotFound('project member was not found')
        self.repository.delete(row)
        project.revision += 1
        self.repository.commit()
        return {'principal_type': principal_type,
                'principal_id': principal_id, 'removed': True}

    def _access(self, user_id, role_ids, project_key, needed):
        project, access = self.repository.accessible_project(
            user_id, role_ids, _safe_id(project_key, 'project ID')
        )
        if project is None:
            raise ProjectAssetNotFound('project was not found')
        self._require(access, needed)
        return project, access

    def _asset_row(self, project, asset_key):
        row = self.repository.asset(
            project.id, _safe_id(asset_key, 'asset ID')
        )
        if row is None:
            raise ProjectAssetNotFound('asset was not found')
        return row

    @staticmethod
    def _project(row, access):
        return {'project_id': row.project_key, 'name': row.name,
                'description': row.description, 'revision': row.revision,
                'access': access,
                'source_control_eligible': row.source_control_eligible,
                'classification_reference': row.classification_reference,
                'permission_reference': row.permission_reference,
                'created_at': _iso(getattr(row, 'created_at', None)),
                'updated_at': _iso(getattr(row, 'updated_at', None))}

    @staticmethod
    def _asset_ref(project, row):
        return {'schemaVersion': 1, 'projectId': project.project_key,
                'assetId': row.asset_key, 'assetType': row.asset_type,
                'assetVersion': row.version, 'path': row.path,
                'displayName': row.name}

    @classmethod
    def _asset(cls, row, include_content, access=None):
        value = {'asset_id': row.asset_key, 'asset_type': row.asset_type,
                 'name': row.name, 'path': row.path,
                 'schema_name': row.schema_name,
                 'schema_version': row.schema_version, 'version': row.version,
                 'metadata': json.loads(row.asset_metadata),
                 'dependency_references': json.loads(
                     row.dependency_references
                 ),
                 'resource_bindings': json.loads(row.resource_bindings),
                 'permission_reference': row.permission_reference,
                 'classification_reference': row.classification_reference,
                 'source_control_eligible': row.source_control_eligible,
                 'editor_capable': row.editor_capable,
                 'viewer_capable': row.viewer_capable,
                 'validation_state': row.validation_state,
                 'validation_details': json.loads(row.validation_details),
                 'created_at': _iso(getattr(row, 'created_at', None)),
                 'updated_at': _iso(getattr(row, 'updated_at', None))}
        if access is not None:
            value['access'] = access
        if include_content:
            value['content'] = json.loads(row.content)
        return value

    @staticmethod
    def _revision(asset, row, access, include_content=True):
        value = {
            'asset_id': asset.asset_key,
            'asset_type': asset.asset_type,
            'name': asset.name,
            'path': asset.path,
            'version': row.version,
            'schema_name': row.schema_name,
            'schema_version': row.schema_version,
            'metadata': json.loads(row.asset_metadata),
            'dependency_references': json.loads(
                row.dependency_references
            ),
            'resource_bindings': json.loads(row.resource_bindings),
            'validation_state': row.validation_state,
            'validation_details': json.loads(row.validation_details),
            'historical': row.version != asset.version,
            'access': access,
            'created_at': _iso(getattr(row, 'created_at', None)),
        }
        if include_content:
            value['content'] = json.loads(row.content)
        return value

    @staticmethod
    def _member(row):
        return {'principal_type': row.principal_type,
                'principal_id': row.principal_id,
                'access_level': row.access_level}


def init_app(app):
    existing = app.extensions.get(APP_EXTENSION_KEY)
    if existing is not None:
        return existing
    service = ProjectAssetService(ProjectAssetRepository())
    app.extensions[APP_EXTENSION_KEY] = service
    _register_routes(app)
    return service


def _iso(value):
    return value.isoformat() if hasattr(value, 'isoformat') else None


def service_for_app(app):
    try:
        return app.extensions[APP_EXTENSION_KEY]
    except (AttributeError, KeyError) as exc:
        raise ProjectAssetError(
            'CDEadmin project asset service is not initialized'
        ) from exc


def _register_routes(app):
    from flask import Blueprint, current_app, jsonify, request
    from flask_security import current_user
    from pgadmin.user_login_check import pga_login_required

    api = Blueprint('cdeadmin_project_assets', __name__)

    def principal():
        return current_user.id, tuple(role.id for role in current_user.roles)

    def invoke(callback):
        service = service_for_app(current_app)
        try:
            return jsonify(success=1, data=callback(service))
        except ProjectAssetError as exc:
            service.repository.rollback()
            payload = {'success': 0, 'errormsg': str(exc)}
            if isinstance(exc, ProjectAssetConflict):
                payload['code'] = exc.code
            return jsonify(**payload), exc.status_code
        except Exception:
            service.repository.rollback()
            current_app.logger.exception('Unexpected project asset failure')
            return jsonify(success=0,
                           errormsg='Project asset request failed.'), 500

    @api.route('/cdeadmin/api/projects', methods=['GET'])
    @pga_login_required
    def projects():
        user_id, role_ids = principal()
        return invoke(lambda service: service.list_projects(user_id, role_ids))

    @api.route('/cdeadmin/api/projects/<project_id>',
               methods=['GET', 'POST', 'PUT', 'DELETE'])
    @pga_login_required
    def project(project_id):
        user_id, role_ids = principal()
        body = request.get_json(silent=True) or {}
        if request.method == 'PUT':
            return invoke(lambda service: service.create_project(
                user_id, project_id, body
            ))
        if request.method == 'POST':
            return invoke(lambda service: service.update_project(
                user_id, role_ids, project_id, body
            ))
        if request.method == 'DELETE':
            return invoke(lambda service: service.delete_project(
                user_id, role_ids, project_id,
                body.get('expected_revision'),
            ))
        return invoke(lambda service: service.project_state(
            user_id, role_ids, project_id
        ))

    @api.route('/cdeadmin/api/projects/<project_id>/members',
               methods=['PUT'])
    @pga_login_required
    def member(project_id):
        user_id, role_ids = principal()
        return invoke(lambda service: service.set_member(
            user_id, role_ids, project_id,
            request.get_json(silent=True) or {},
        ))

    @api.route(
        '/cdeadmin/api/projects/<project_id>/members/'
        '<principal_type>/<int:principal_id>', methods=['DELETE']
    )
    @pga_login_required
    def remove_member(project_id, principal_type, principal_id):
        user_id, role_ids = principal()
        return invoke(lambda service: service.remove_member(
            user_id, role_ids, project_id,
            principal_type, principal_id,
        ))

    @api.route(
        '/cdeadmin/api/projects/<project_id>/assets/<asset_id>/revisions',
        methods=['GET']
    )
    @pga_login_required
    def revisions(project_id, asset_id):
        user_id, role_ids = principal()
        return invoke(lambda service: service.list_revisions(
            user_id, role_ids, project_id, asset_id
        ))

    @api.route('/cdeadmin/api/projects/<project_id>/assets/<asset_id>',
               methods=['GET', 'PUT', 'DELETE'])
    @pga_login_required
    def asset(project_id, asset_id):
        user_id, role_ids = principal()
        if request.method == 'GET':
            version = request.args.get('version', type=int)
            return invoke(lambda service: service.get_asset(
                user_id, role_ids, project_id, asset_id, version
            ))
        if request.method == 'DELETE':
            body = request.get_json(silent=True) or {}
            return invoke(lambda service: service.delete_asset(
                user_id, role_ids, project_id, asset_id,
                body.get('expected_version'),
            ))
        return invoke(lambda service: service.save_asset(
            user_id, role_ids, project_id, asset_id,
            request.get_json(silent=True) or {},
        ))

    app.register_blueprint(api)


__all__ = (
    'APP_EXTENSION_KEY', 'ProjectAssetConflict', 'ProjectAssetError',
    'ProjectAssetForbidden', 'ProjectAssetNotFound', 'ProjectAssetRepository',
    'ProjectAssetService', 'init_app', 'service_for_app',
    'validate_asset_request',
)
