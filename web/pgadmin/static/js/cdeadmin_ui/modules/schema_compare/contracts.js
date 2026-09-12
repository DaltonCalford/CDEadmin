/////////////////////////////////////////////////////////////
// CDEadmin Schema Comparison immutable contracts.
/////////////////////////////////////////////////////////////

import {
  immutable, noRawSecrets, plainObject, platformValue,
} from '../../platform/serviceUtils';
import {stablePlatformId} from '../../platform/PlatformRegistry';

export const SCHEMA_COMPARE_MODULE_ID = 'cdeadmin.schema_compare';
export const SCHEMA_COMPARE_ASSET_TYPE = 'cdeadmin.schema_compare.v1';
export const SCHEMA_COMPARE_SCHEMA = 'cdeadmin.schema-compare.asset.v1';
export const SCHEMA_SNAPSHOT_SCHEMA = 'cdeadmin.schema-compare.snapshot.v1';
export const SCHEMA_COMPARE_EXPORT = 'cdeadmin.schema-compare.export.v1';

export const SUPPORT_STATES = Object.freeze([
  'supported_native', 'supported_via_cdeadmin',
  'supported_via_external_adapter', 'read_only', 'partial', 'unsupported',
  'unknown',
]);

export const DIFF_CLASSIFICATIONS = Object.freeze([
  'identical', 'semantically_equivalent', 'changed', 'left_only',
  'right_only', 'rename_candidate', 'moved', 'incompatible', 'uncomparable',
  'unknown',
]);

export const EQUIVALENCE_CATEGORIES = Object.freeze([
  'exact', 'representational_difference', 'compatible_widening',
  'compatible_with_default_change', 'lossy', 'behavioral_difference',
  'unsupported_mapping',
]);

export const SCHEMA_COMPARE_STATES = Object.freeze([
  'loading', 'empty', 'ready', 'stale', 'partial', 'permission_denied',
  'disconnected', 'validation_error', 'runtime_failure', 'read_only',
  'background_task_active',
]);

export const SCHEMA_COMPARE_ERROR_CODES = Object.freeze([
  'configuration_invalid', 'provider_unavailable', 'permission_denied',
  'capability_missing', 'task_failed', 'task_cancelled', 'partial_result',
  'stale_result', 'conflict', 'external_format_invalid', 'internal_error',
]);

const REF_SCHEMAS = Object.freeze([
  'cdeadmin.resource-ref.v1', 'cdeadmin.schema-compare.snapshot-ref.v1',
]);

function array(value, label) {
  if(value === undefined || value === null) return [];
  if(!Array.isArray(value)) throw new TypeError(`${label} must be an array.`);
  return value;
}

function object(value, label, fallback={}) {
  if(value === undefined || value === null) return fallback;
  return plainObject(value, label);
}

function integer(value, label, minimum=0) {
  if(!Number.isSafeInteger(value) || value < minimum) {
    throw new TypeError(`${label} must be an integer of at least ${minimum}.`);
  }
  return value;
}

function cleanJson(value, label) {
  noRawSecrets(value, label);
  try {
    return JSON.parse(JSON.stringify(value));
  } catch(error) {
    throw new TypeError(`${label} must be JSON serializable: ${error.message}`);
  }
}

function sorted(value) {
  if(Array.isArray(value)) return value.map(sorted);
  if(value && typeof value === 'object') return Object.keys(value).sort()
    .reduce((result, key) => ({...result, [key]: sorted(value[key])}), {});
  return value;
}

export function deterministicJson(value, space=2) {
  return JSON.stringify(sorted(cleanJson(value, 'canonical value')), null, space);
}

export function validateReference(input, label='Reference', {optional=false}={}) {
  if(input === null || input === undefined) {
    if(optional) return null;
    throw new TypeError(`${label} is required.`);
  }
  plainObject(input, label);
  noRawSecrets(input, label);
  if(input.schema === 'cdeadmin.resource-ref.v1') {
    const canonical = platformValue(input.canonical, `${label} canonical identity`);
    return immutable({...cleanJson(input, label), canonical});
  }
  if(input.schema === 'cdeadmin.schema-compare.snapshot-ref.v1') {
    return immutable({
      schema: input.schema,
      snapshotId: platformValue(input.snapshotId, `${label} snapshot ID`),
      revision: String(input.revision ?? ''),
    });
  }
  if(Number(input.schemaVersion) === 1 && input.projectId && input.assetId) {
    return immutable({
      schemaVersion: 1,
      projectId: platformValue(input.projectId, `${label} project ID`),
      assetId: platformValue(input.assetId, `${label} asset ID`),
      assetType: platformValue(input.assetType, `${label} asset type`),
      assetVersion: integer(input.assetVersion ?? 0, `${label} asset version`),
      path: platformValue(input.path, `${label} path`),
      displayName: platformValue(input.displayName, `${label} display name`),
    });
  }
  throw new TypeError(
    `${label} must be a ResourceRef, AssetRef, or immutable snapshot reference; `+
    `received ${input.schema || 'an unknown schema'}.`
  );
}

export function referenceKey(input) {
  const reference = validateReference(input);
  if(REF_SCHEMAS.includes(reference.schema)) {
    return reference.canonical ??
      `snapshot:${reference.snapshotId}@${reference.revision}`;
  }
  return `asset:${reference.projectId}/${reference.assetId}@${reference.assetVersion}`;
}

function schemaObject(input, index) {
  plainObject(input, `Schema object ${index}`);
  noRawSecrets(input, `Schema object ${index}`);
  const id = platformValue(input.id, `Schema object ${index} ID`);
  const kind = stablePlatformId(input.kind, `Schema object ${id} kind`);
  const qualifiedName = platformValue(
    input.qualifiedName, `Schema object ${id} qualified name`
  );
  return immutable({
    id, kind, qualifiedName,
    displayName: String(input.displayName ?? qualifiedName),
    parentId: input.parentId ? platformValue(
      input.parentId, `Schema object ${id} parent ID`
    ) : null,
    nativeId: input.nativeId === undefined || input.nativeId === null ? null :
      platformValue(input.nativeId, `Schema object ${id} native ID`),
    resourceRef: input.resourceRef ? validateReference(
      input.resourceRef, `Schema object ${id} ResourceRef`
    ) : null,
    normalized: cleanJson(object(
      input.normalized, `Schema object ${id} normalized properties`
    ), `Schema object ${id} normalized properties`),
    native: cleanJson(object(
      input.native, `Schema object ${id} native properties`
    ), `Schema object ${id} native properties`),
    children: array(input.children, `Schema object ${id} children`)
      .map((value) => platformValue(value, `Schema object ${id} child`)),
    dependencies: array(input.dependencies, `Schema object ${id} dependencies`)
      .map((value) => platformValue(value, `Schema object ${id} dependency`)),
    security: cleanJson(object(
      input.security, `Schema object ${id} security`
    ), `Schema object ${id} security`),
    storage: cleanJson(object(
      input.storage, `Schema object ${id} storage`
    ), `Schema object ${id} storage`),
    accessPaths: cleanJson(array(
      input.accessPaths, `Schema object ${id} access paths`
    ), `Schema object ${id} access paths`),
    comment: String(input.comment ?? ''),
  });
}

export function validateSchemaSnapshot(input) {
  plainObject(input, 'Schema snapshot');
  noRawSecrets(input, 'Schema snapshot');
  if(input.schema !== SCHEMA_SNAPSHOT_SCHEMA) {
    throw new TypeError(`Schema snapshot must use ${SCHEMA_SNAPSHOT_SCHEMA}.`);
  }
  const supportState = String(input.supportState ?? 'unknown');
  if(!SUPPORT_STATES.includes(supportState)) {
    throw new TypeError('Schema snapshot support state is invalid.');
  }
  const objects = array(input.objects, 'Schema snapshot objects')
    .map(schemaObject);
  const ids = new Set();
  objects.forEach((item) => {
    if(ids.has(item.id)) throw new TypeError(`Duplicate schema object ID: ${item.id}.`);
    ids.add(item.id);
  });
  objects.forEach((item) => {
    if(item.parentId && !ids.has(item.parentId)) {
      throw new TypeError(`Schema object parent is missing: ${item.parentId}.`);
    }
    [...item.children, ...item.dependencies].forEach((id) => {
      if(!ids.has(id)) throw new TypeError(`Schema object reference is missing: ${id}.`);
    });
  });
  return immutable({
    schema: SCHEMA_SNAPSHOT_SCHEMA,
    schemaVersion: integer(input.schemaVersion ?? 1, 'Snapshot schema version', 1),
    snapshotId: platformValue(input.snapshotId, 'Snapshot ID'),
    sourceRef: validateReference(input.sourceRef, 'Snapshot source'),
    revision: String(input.revision ?? ''),
    capturedAt: platformValue(input.capturedAt, 'Snapshot capture time'),
    providerId: stablePlatformId(input.providerId, 'Snapshot provider ID'),
    providerVersion: String(input.providerVersion ?? ''),
    supportState,
    evidence: cleanJson(object(input.evidence, 'Snapshot evidence'), 'Snapshot evidence'),
    warnings: array(input.warnings, 'Snapshot warnings').map(String),
    nativeDetails: cleanJson(object(
      input.nativeDetails, 'Snapshot native details'
    ), 'Snapshot native details'),
    objects,
  });
}

export function validateMapping(input, index=0) {
  plainObject(input, `Mapping ${index}`);
  const category = String(input.category ?? '');
  if(!EQUIVALENCE_CATEGORIES.includes(category)) {
    throw new TypeError(`Mapping ${index} category is invalid.`);
  }
  return immutable({
    mappingId: platformValue(input.mappingId, `Mapping ${index} ID`),
    leftId: platformValue(input.leftId, `Mapping ${index} left ID`),
    rightId: platformValue(input.rightId, `Mapping ${index} right ID`),
    category,
    version: integer(input.version ?? 1, `Mapping ${index} version`, 1),
    confidence: input.confidence === undefined ? null : Math.max(
      0, Math.min(1, Number(input.confidence))
    ),
    evidence: cleanJson(array(input.evidence, `Mapping ${index} evidence`),
      `Mapping ${index} evidence`),
    accepted: input.accepted === true,
  });
}

export function createSchemaCompareContent(input={}) {
  plainObject(input, 'Schema Comparison content');
  noRawSecrets(input, 'Schema Comparison content');
  const content = {
    schema: SCHEMA_COMPARE_SCHEMA,
    schemaVersion: integer(input.schemaVersion ?? 1, 'Asset schema version', 1),
    moduleId: SCHEMA_COMPARE_MODULE_ID,
    leftRef: validateReference(input.leftRef, 'Left reference', {optional: true}),
    rightRef: validateReference(input.rightRef, 'Right reference', {optional: true}),
    options: cleanJson(object(input.options, 'Comparison options'), 'Comparison options'),
    acceptedMappings: array(input.acceptedMappings, 'Accepted mappings')
      .map(validateMapping),
    ignoredDiffs: array(input.ignoredDiffs, 'Ignored differences')
      .map((value) => platformValue(value, 'Ignored difference ID')).sort(),
    comparisonResultRef: input.comparisonResultRef ? validateReference(
      input.comparisonResultRef, 'Comparison result reference'
    ) : null,
    changePlan: input.changePlan ? cleanJson(
      input.changePlan, 'Change plan'
    ) : null,
  };
  return immutable(sorted(content));
}

export function schemaCompareAssetRequest(assetRef, content, overrides={}) {
  assetRef = validateReference(assetRef, 'Schema Comparison AssetRef');
  if(assetRef.assetType !== SCHEMA_COMPARE_ASSET_TYPE) {
    throw new TypeError(`AssetRef must use ${SCHEMA_COMPARE_ASSET_TYPE}.`);
  }
  content = createSchemaCompareContent(content);
  const request = {
    asset_type: SCHEMA_COMPARE_ASSET_TYPE,
    name: assetRef.displayName,
    path: assetRef.path,
    schema_name: SCHEMA_COMPARE_ASSET_TYPE,
    schema_version: content.schemaVersion,
    expected_version: assetRef.assetVersion,
    content,
    metadata: cleanJson(overrides.metadata ?? {}, 'Asset metadata'),
    dependency_references: cleanJson(
      overrides.dependency_references ?? [], 'Asset dependencies'
    ),
    resource_bindings: [content.leftRef, content.rightRef]
      .filter((item) => item?.schema === 'cdeadmin.resource-ref.v1'),
    permission_reference: overrides.permission_reference ?? '',
    classification_reference: overrides.classification_reference ?? '',
    source_control_eligible: true,
    editor_capable: overrides.editor_capable !== false,
    viewer_capable: true,
    validation_state: content.leftRef && content.rightRef ? 'valid' : 'invalid',
    validation_details: content.leftRef && content.rightRef ? [] : [
      {code: 'configuration_invalid', message: 'Left and right sources are required.'},
    ],
  };
  noRawSecrets(request, 'Schema Comparison asset request');
  return immutable(request);
}

export function exportSchemaComparison(content, {
  profile='cdeadmin.schema_compare.v1', provenance={}, nativeScripts=[],
}={}) {
  content = createSchemaCompareContent(content);
  const envelope = {
    schema: SCHEMA_COMPARE_EXPORT,
    version: 1,
    profile: platformValue(profile, 'Export profile'),
    metadataOnly: nativeScripts.length === 0,
    authenticationMaterial: 'none',
    provenance: cleanJson(provenance, 'Export provenance'),
    content,
    nativeScripts: array(nativeScripts, 'Native scripts').map((item) => ({
      providerId: stablePlatformId(item.providerId, 'Script provider ID'),
      mediaType: platformValue(item.mediaType, 'Script media type'),
      text: String(item.text ?? ''),
    })),
  };
  noRawSecrets(envelope, 'Schema Comparison export');
  return deterministicJson(envelope);
}

export function importSchemaComparison(source) {
  let parsed;
  try {
    parsed = typeof source === 'string' ? JSON.parse(source) : source;
  } catch(error) {
    throw new TypeError(`external_format_invalid: ${error.message}`);
  }
  plainObject(parsed, 'Schema Comparison import');
  if(parsed.schema !== SCHEMA_COMPARE_EXPORT || parsed.version !== 1) {
    throw new TypeError('external_format_invalid: unsupported schema or version.');
  }
  if(parsed.authenticationMaterial !== 'none') {
    throw new TypeError('external_format_invalid: credential-bearing imports are forbidden.');
  }
  noRawSecrets(parsed, 'Schema Comparison import');
  return createSchemaCompareContent(parsed.content);
}
