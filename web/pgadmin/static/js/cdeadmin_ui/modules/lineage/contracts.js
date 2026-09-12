/////////////////////////////////////////////////////////////
// Data Lineage canonical contracts and serialization.
/////////////////////////////////////////////////////////////

import {immutable, noRawSecrets, plainObject, platformValue} from '../../platform/serviceUtils';

export const LINEAGE_MODULE_ID = 'cdeadmin.lineage';
export const LINEAGE_ASSET_TYPE = 'cdeadmin.lineage.v1';
export const LINEAGE_ASSET_SCHEMA = 'cdeadmin.lineage.asset.v1';

export const LINEAGE_ORIGINS = Object.freeze([
  'provider_declared', 'project_declared', 'parsed_query', 'execution_plan',
  'etl_declared', 'cdc_declared', 'migration_declared', 'openlineage_import',
  'observed_trace', 'user_curated', 'inferred',
]);

export const LINEAGE_EDGE_TYPES = Object.freeze([
  'reads', 'writes', 'derives', 'transforms', 'copies', 'replicates',
  'publishes', 'subscribes', 'exposes', 'materializes', 'triggers',
  'depends_on', 'governs', 'validates', 'structural_reference',
]);

export const FIELD_TRANSFORMATIONS = Object.freeze([
  'DIRECT_IDENTITY', 'RENAME', 'CAST', 'EXPRESSION', 'AGGREGATE', 'WINDOW',
  'LOOKUP', 'JOIN_KEY', 'FILTER_ONLY', 'GENERATED', 'OPAQUE', 'UNKNOWN',
]);

export const LINEAGE_STATES = Object.freeze([
  'empty', 'loading', 'ready', 'stale', 'partial', 'permission_denied',
  'disconnected', 'validation_error', 'runtime_failure', 'read_only',
  'background_task_active',
]);

export const LINEAGE_PRESENTATION_STATES = Object.freeze([
  'confirmed', 'observed', 'declared', 'inferred', 'conflicted', 'stale',
]);

const REFERENCE_SCHEMAS = new Set([
  'cdeadmin.resource-ref.v1', 'cdeadmin.asset-ref.v1',
  'cdeadmin.job-ref.v1', 'cdeadmin.external-ref.v1',
]);

function optionalTime(value, label) {
  if(value === null || value === undefined || value === '') return null;
  const result = platformValue(value, label, 64);
  if(Number.isNaN(Date.parse(result))) throw new TypeError(`${label} must be an ISO timestamp.`);
  return result;
}

function confidence(value, label='Confidence') {
  const result = Number(value);
  if(!Number.isFinite(result) || result < 0 || result > 1) {
    throw new TypeError(`${label} must be between zero and one.`);
  }
  return result;
}

function validateInterval(validFrom, validTo, label) {
  if(validFrom && validTo && Date.parse(validFrom) > Date.parse(validTo)) {
    throw new TypeError(`${label} validFrom must not be after validTo.`);
  }
}

export function validateLineageRef(input, label='Lineage reference') {
  plainObject(input, label); noRawSecrets(input, label);
  const schema = platformValue(input.schema, `${label} schema`);
  if(!REFERENCE_SCHEMAS.has(schema)) throw new TypeError(`${label} schema is unsupported.`);
  const identity = schema === 'cdeadmin.resource-ref.v1' ? input.canonical :
    schema === 'cdeadmin.asset-ref.v1' ? `${input.projectId}/${input.assetId}` : input.id;
  platformValue(identity, `${label} identity`, 4096);
  return immutable({...input});
}

export function lineageReferenceKey(input) {
  const ref = validateLineageRef(input);
  if(ref.schema === 'cdeadmin.resource-ref.v1') return `resource:${ref.canonical}`;
  if(ref.schema === 'cdeadmin.asset-ref.v1') return `asset:${ref.projectId}/${ref.assetId}`;
  return `${ref.schema}:${ref.id}`;
}

export function validateEvidence(input) {
  plainObject(input, 'Lineage evidence'); noRawSecrets(input, 'Lineage evidence');
  const origin = platformValue(input.origin, 'Evidence origin');
  if(!LINEAGE_ORIGINS.includes(origin)) throw new TypeError(`Invalid evidence origin: ${origin}`);
  const validFrom = optionalTime(input.validFrom, 'Evidence validFrom');
  const validTo = optionalTime(input.validTo, 'Evidence validTo');
  validateInterval(validFrom, validTo, 'Evidence');
  return immutable({schema: 'cdeadmin.lineage-evidence.v1',
    id: platformValue(input.id, 'Evidence ID'), origin,
    confidence: confidence(input.confidence),
    capturedAt: optionalTime(input.capturedAt, 'Evidence capturedAt'),
    validFrom, validTo,
    stale: Boolean(input.stale), providerVersion: input.providerVersion ?? null,
    reference: input.reference ? validateLineageRef(input.reference, 'Evidence reference') : null,
    details: immutable({...plainObject(input.details ?? {}, 'Evidence details')}),
  });
}

export function validateFieldLineage(input) {
  plainObject(input, 'Field lineage'); noRawSecrets(input, 'Field lineage');
  const transformation = platformValue(input.transformation, 'Field transformation');
  if(!FIELD_TRANSFORMATIONS.includes(transformation)) throw new TypeError(
    `Invalid field transformation: ${transformation}`
  );
  return immutable({schema: 'cdeadmin.field-lineage.v1',
    id: platformValue(input.id, 'Field lineage ID'),
    sourceField: platformValue(input.sourceField, 'Source field', 4096),
    targetField: input.targetField === null ? null :
      platformValue(input.targetField, 'Target field', 4096),
    transformation, expressionRef: input.expressionRef ?? null,
    evidenceIds: [...new Set((input.evidenceIds ?? []).map((item) =>
      platformValue(item, 'Field evidence ID')))].sort(),
    nativeDetails: immutable({...plainObject(input.nativeDetails ?? {}, 'Field native details')}),
  });
}

export function validateLineageNode(input) {
  plainObject(input, 'Lineage node'); noRawSecrets(input, 'Lineage node');
  const reference = validateLineageRef(input.ref ?? input.reference, 'Lineage node reference');
  return immutable({schema: 'cdeadmin.lineage-node.v1',
    id: platformValue(input.id, 'Lineage node ID'),
    kind: platformValue(input.kind, 'Lineage node kind'), reference,
    namespace: platformValue(input.namespace ?? 'default', 'Lineage namespace'),
    name: platformValue(input.name, 'Lineage node name'),
    classification: input.classification ?? null,
    validFrom: optionalTime(input.validFrom, 'Node validFrom'),
    validTo: optionalTime(input.validTo, 'Node validTo'),
    nativeDetails: immutable({...plainObject(input.nativeDetails ?? {}, 'Node native details')}),
  });
}

export function validateLineageEdge(input) {
  plainObject(input, 'Lineage edge'); noRawSecrets(input, 'Lineage edge');
  const type = platformValue(input.type, 'Lineage edge type');
  if(!LINEAGE_EDGE_TYPES.includes(type)) throw new TypeError(`Invalid lineage edge type: ${type}`);
  const evidence = (input.evidence ?? []).map(validateEvidence);
  if(!evidence.length) throw new TypeError('A lineage edge requires evidence.');
  if(new Set(evidence.map((item) => item.id)).size !== evidence.length) {
    throw new TypeError('Lineage evidence IDs must be unique within an edge.');
  }
  const origins = [...new Set(evidence.map((item) => item.origin))].sort();
  const origin = platformValue(input.origin ?? origins[0], 'Lineage edge origin');
  if(!LINEAGE_ORIGINS.includes(origin)) throw new TypeError(
    `Invalid source origin: ${origin}`
  );
  const presentationState = input.presentationState ?? null;
  if(presentationState !== null && !LINEAGE_PRESENTATION_STATES.includes(
    presentationState
  )) throw new TypeError(`Invalid Lineage presentation state: ${presentationState}`);
  const validFrom = optionalTime(input.validFrom, 'Edge validFrom');
  const validTo = optionalTime(input.validTo, 'Edge validTo');
  validateInterval(validFrom, validTo, 'Edge');
  const fieldLineage = (input.fieldLineage ?? []).map(validateFieldLineage);
  if(new Set(fieldLineage.map((item) => item.id)).size !== fieldLineage.length) {
    throw new TypeError('Field Lineage IDs must be unique within an edge.');
  }
  const evidenceIds = new Set(evidence.map((item) => item.id));
  if(fieldLineage.some((field) => field.evidenceIds.some((id) => !evidenceIds.has(id)))) {
    throw new TypeError('Field Lineage references unknown edge evidence.');
  }
  return immutable({schema: 'cdeadmin.lineage-edge.v1',
    id: platformValue(input.id, 'Lineage edge ID'),
    from: platformValue(input.from, 'Lineage edge source'),
    to: platformValue(input.to, 'Lineage edge target'), type,
    origin,
    confidence: confidence(input.confidence ?? Math.max(...evidence.map((item) =>
      item.confidence))),
    validFrom, validTo, evidence, fieldLineage,
    presentationState,
    suppressed: Boolean(input.suppressed),
    nativeDetails: immutable({...plainObject(input.nativeDetails ?? {}, 'Edge native details')}),
  });
}

export function lineageSuppressionKey({from, to, type}) {
  return [from, to, type].map((value) => encodeURIComponent(
    platformValue(value, 'Suppression identity')
  )).join('|');
}

function uniqueSorted(values, mapper) {
  const mapped = values.map(mapper);
  const keys = new Set();
  return mapped.filter((item) => {
    const key = item.id ?? lineageReferenceKey(item);
    if(keys.has(key)) throw new TypeError(`Duplicate lineage identity: ${key}`);
    keys.add(key); return true;
  }).sort((left, right) => String(left.id ?? lineageReferenceKey(left))
    .localeCompare(String(right.id ?? lineageReferenceKey(right))));
}

export function createLineageContent(input={}) {
  plainObject(input, 'Lineage asset content'); noRawSecrets(input, 'Lineage asset content');
  const scopeRefs = uniqueSorted(input.scopeRefs ?? [], (item) => validateLineageRef(item));
  const curatedEdges = uniqueSorted(input.curatedEdges ?? [], validateLineageEdge);
  const sourcePolicyIds = new Set();
  const sourcePolicies = [...(input.sourcePolicies ?? [])].map((policy) => {
    plainObject(policy, 'Lineage source policy');
    const retentionDays = Number(policy.retentionDays ?? 0);
    if(!Number.isFinite(retentionDays) || retentionDays < 0) throw new TypeError(
      'Lineage retentionDays must be a finite non-negative number.'
    );
    const id = platformValue(policy.id, 'Source policy ID');
    if(sourcePolicyIds.has(id)) throw new TypeError(`Duplicate Lineage source policy: ${id}`);
    sourcePolicyIds.add(id);
    return immutable({id,
      sourcePriority: [...(policy.sourcePriority ?? [])].map((origin) => {
        if(!LINEAGE_ORIGINS.includes(origin)) throw new TypeError(`Invalid source origin: ${origin}`);
        return origin;
      }), inferenceEnabled: Boolean(policy.inferenceEnabled),
      retentionDays,
      providerId: policy.providerId ?? null});
  }).sort((left, right) => left.id.localeCompare(right.id));
  const suppressedInferenceRules = [...new Set((input.suppressedInferenceRules ?? [])
    .map((item) => platformValue(item, 'Suppression rule')))].sort();
  const snapshotRefs = uniqueSorted(input.snapshotRefs ?? [], (item) =>
    validateLineageRef(item, 'Snapshot reference'));
  return immutable({schema: LINEAGE_ASSET_SCHEMA, schemaVersion: 1,
    moduleId: LINEAGE_MODULE_ID, scopeRefs, sourcePolicies,
    savedFilters: immutable({...plainObject(input.savedFilters ?? {}, 'Saved filters')}),
    ...(input.layoutPreferences ? {layoutPreferences:
      immutable({...plainObject(input.layoutPreferences, 'Layout preferences')})} : {}),
    curatedEdges, suppressedInferenceRules, snapshotRefs});
}

function canonical(value) {
  if(Array.isArray(value)) return value.map(canonical);
  if(!value || typeof value !== 'object') return value;
  return Object.fromEntries(Object.keys(value).sort().map((key) => [key, canonical(value[key])]));
}

export function exportLineageAsset(input) {
  return `${JSON.stringify(canonical(createLineageContent(input)), null, 2)}\n`;
}

export function importLineageAsset(text) {
  if(typeof text !== 'string' || text.length > 8 * 1024 * 1024) {
    throw new TypeError('Lineage source is invalid or exceeds 8 MiB.');
  }
  return createLineageContent(JSON.parse(text));
}

export function lineageAssetRequest({projectId, assetId, name, path, expectedVersion,
  content}) {
  platformValue(projectId, 'Project ID'); platformValue(assetId, 'Asset ID');
  return immutable({asset_type: LINEAGE_ASSET_TYPE, name: platformValue(name, 'Asset name'),
    path: platformValue(path, 'Asset path', 1024), schema_name: LINEAGE_ASSET_TYPE,
    schema_version: 1, expected_version: Number(expectedVersion ?? 0),
    content: createLineageContent(content), metadata: {}, dependency_references: [],
    resource_bindings: content.scopeRefs ?? [], source_control_eligible: true,
    editor_capable: true, viewer_capable: true, validation_state: 'valid',
    validation_details: []});
}
