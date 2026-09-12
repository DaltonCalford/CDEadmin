/////////////////////////////////////////////////////////////
// CDC Designer canonical contracts and deterministic source.
/////////////////////////////////////////////////////////////

import {immutable, noRawSecrets, plainObject, platformValue} from '../../platform/serviceUtils';

export const CDC_MODULE_ID = 'cdeadmin.cdc';
export const CDC_ASSET_TYPE = 'cdeadmin.cdc.v1';
export const CDC_ASSET_SCHEMA = 'cdeadmin.cdc.asset.v1';
export const CDC_CAPTURE_MECHANISMS = Object.freeze([
  'log_based', 'logical_replication', 'oplog/change_stream',
  'provider_native_stream', 'trigger_based', 'polling', 'mga_history',
  'external_connector',
]);
export const CDC_START_KINDS = Object.freeze([
  'latest', 'timestamp', 'lsn', 'binlog', 'oplog', 'offset', 'native',
]);
export const CDC_SNAPSHOT_MODES = Object.freeze([
  'none', 'initial', 'incremental', 'provider_native',
]);
export const CDC_OPERATIONS = Object.freeze([
  'create', 'update', 'delete', 'truncate', 'schema', 'transaction', 'custom',
]);
export const CDC_DELIVERY_GUARANTEES = Object.freeze([
  'at_most_once', 'at_least_once', 'effectively_once_with_dedup',
  'exactly_once_proven', 'provider_specific', 'unknown',
]);
export const CDC_SCHEMA_CHANGE_CLASSES = Object.freeze([
  'additive_compatible', 'compatible_with_mapping', 'breaking', 'unknown',
]);
export const CDC_EVOLUTION_ACTIONS = Object.freeze([
  'auto_apply_compatible', 'pause_and_review', 'map_to_existing',
  'dead_letter', 'fail',
]);
export const CDC_RUNTIME_STATES = Object.freeze([
  'provisioning', 'snapshotting', 'catching_up', 'streaming', 'paused',
  'degraded', 'failed', 'stopping', 'stopped',
]);
export const CDC_UI_STATES = Object.freeze([
  'empty', 'loading', 'ready', 'stale', 'partial', 'permission_denied',
  'disconnected', 'validation_error', 'runtime_failure', 'read_only',
  'background_task_active',
]);

const REFERENCE_SCHEMAS = new Set([
  'cdeadmin.resource-ref.v1', 'cdeadmin.asset-ref.v1',
  'cdeadmin.external-ref.v1', 'cdeadmin.credential-ref.v1',
]);

function optionalText(value, label, maximum=8192) {
  if(value === undefined || value === null || value === '') return null;
  return platformValue(value, label, maximum);
}

function object(value, label, maximum=1024 * 1024) {
  const result = plainObject(value ?? {}, label); noRawSecrets(result, label);
  if(JSON.stringify(result).length > maximum) throw new TypeError(`${label} exceeds its size limit.`);
  return immutable({...result});
}

function array(value, label, mapper, maximum=10000) {
  if(!Array.isArray(value) || value.length > maximum) throw new TypeError(
    `${label} must be an array containing no more than ${maximum} items.`
  );
  return value.map(mapper);
}

function stringList(values, label, maximum=1000) {
  return [...new Set(array(values ?? [], label,
    (value) => platformValue(value, `${label} item`), maximum))].sort();
}

function unique(values, label, mapper) {
  const result = array(values ?? [], label, mapper); const ids = new Set();
  result.forEach((item) => {
    if(ids.has(item.id)) throw new TypeError(`Duplicate ${label}: ${item.id}`);
    ids.add(item.id);
  });
  return result.sort((left, right) => left.id.localeCompare(right.id));
}

function nonnegativeInteger(value, label, {maximum=Number.MAX_SAFE_INTEGER}={}) {
  const number = Number(value);
  if(!Number.isInteger(number) || number < 0 || number > maximum) throw new TypeError(
    `${label} must be an integer from zero through ${maximum}.`
  );
  return number;
}

export function validateCDCReference(input, label='CDC reference') {
  plainObject(input, label); noRawSecrets(input, label);
  const schema = platformValue(input.schema, `${label} schema`);
  if(!REFERENCE_SCHEMAS.has(schema)) throw new TypeError(`${label} schema is unsupported.`);
  if(schema === 'cdeadmin.resource-ref.v1') {
    platformValue(input.canonical, `${label} canonical identity`, 8192);
  } else if(schema === 'cdeadmin.asset-ref.v1') {
    platformValue(input.projectId, `${label} project ID`);
    platformValue(input.assetId, `${label} asset ID`);
  } else if(schema === 'cdeadmin.credential-ref.v1') {
    platformValue(input.scheme, `${label} credential scheme`);
    platformValue(input.id, `${label} credential ID`);
  } else platformValue(input.id, `${label} identity`, 8192);
  return immutable({...input});
}

export function cdcReferenceKey(input) {
  const ref = validateCDCReference(input);
  if(ref.schema === 'cdeadmin.resource-ref.v1') return `resource:${ref.canonical}`;
  if(ref.schema === 'cdeadmin.asset-ref.v1') return `asset:${ref.projectId}/${ref.assetId}`;
  if(ref.schema === 'cdeadmin.credential-ref.v1') return `credential:${ref.scheme}/${ref.id}`;
  return `external:${ref.id}`;
}

export function validateStartPosition(input={kind: 'latest'}) {
  plainObject(input, 'CDC start position'); noRawSecrets(input, 'CDC start position');
  const kind = platformValue(input.kind ?? 'latest', 'CDC start-position kind');
  if(!CDC_START_KINDS.includes(kind)) throw new TypeError('CDC start-position kind is invalid.');
  const value = optionalText(input.value, 'CDC start-position value', 16384);
  if(kind !== 'latest' && !value) throw new TypeError(`${kind} start position requires a value.`);
  return immutable({schema: 'cdeadmin.cdc-start-position.v1', kind, value,
    nativeDetails: object(input.nativeDetails, 'CDC start-position native details')});
}

export function validateSnapshotPolicy(input={mode: 'none'}) {
  plainObject(input, 'CDC snapshot policy'); noRawSecrets(input, 'CDC snapshot policy');
  const mode = platformValue(input.mode ?? 'none', 'CDC snapshot mode');
  if(!CDC_SNAPSHOT_MODES.includes(mode)) throw new TypeError('CDC snapshot mode is invalid.');
  const batchSize = input.batchSize === undefined || input.batchSize === null ? null :
    nonnegativeInteger(input.batchSize, 'CDC snapshot batch size', {maximum: 10000000});
  if(batchSize === 0) throw new TypeError('CDC snapshot batch size must be greater than zero.');
  if(mode === 'incremental' && batchSize === null) throw new TypeError(
    'Incremental CDC snapshots require a positive batch size.'
  );
  return immutable({schema: 'cdeadmin.cdc-snapshot-policy.v1', mode,
    consistency: optionalText(input.consistency, 'CDC snapshot consistency'), batchSize,
    nativeDetails: object(input.nativeDetails, 'CDC snapshot native details')});
}

export function validateCaptureSource(input) {
  plainObject(input, 'CDC capture source'); noRawSecrets(input, 'CDC capture source');
  const resourceRef = validateCDCReference(input.resourceRef, 'CDC source resource');
  if(resourceRef.schema !== 'cdeadmin.resource-ref.v1') throw new TypeError(
    'CDC source requires a provider ResourceRef.'
  );
  const captureMechanism = platformValue(input.captureMechanism, 'CDC capture mechanism');
  if(!CDC_CAPTURE_MECHANISMS.includes(captureMechanism)) throw new TypeError(
    'CDC capture mechanism is invalid.'
  );
  const credentialRef = input.credentialRef ? validateCDCReference(
    input.credentialRef, 'CDC source credential reference'
  ) : null;
  if(credentialRef && credentialRef.schema !== 'cdeadmin.credential-ref.v1') throw new TypeError(
    'CDC source credential must be a CredentialRef.'
  );
  return immutable({schema: 'cdeadmin.cdc-capture-source.v1', resourceRef,
    captureMechanism,
    nativeMechanism: platformValue(input.nativeMechanism, 'CDC native capture mechanism'),
    requiredPrivileges: stringList(input.requiredPrivileges, 'CDC required privileges'),
    credentialRef, startPosition: validateStartPosition(input.startPosition),
    snapshotPolicy: validateSnapshotPolicy(input.snapshotPolicy),
    nativeDetails: object(input.nativeDetails, 'CDC source native details')});
}

export function validateEventTransform(input) {
  plainObject(input, 'CDC event transform'); noRawSecrets(input, 'CDC event transform');
  const kind = platformValue(input.kind, 'CDC transform kind');
  if(!['filter', 'mapping', 'redaction'].includes(kind)) throw new TypeError(
    'CDC transform kind is invalid.'
  );
  return immutable({schema: 'cdeadmin.cdc-event-transform.v1',
    id: platformValue(input.id, 'CDC transform ID'),
    name: platformValue(input.name ?? input.id, 'CDC transform name'), kind,
    enabled: input.enabled === undefined ? true : Boolean(input.enabled),
    rules: object(input.rules, 'CDC transform rules'),
    schemaMapping: object(input.schemaMapping, 'CDC transform schema mapping'),
    nativeDetails: object(input.nativeDetails, 'CDC transform native details')});
}

export function validateSinkBinding(input) {
  plainObject(input, 'CDC sink binding'); noRawSecrets(input, 'CDC sink binding');
  const resourceRef = validateCDCReference(input.resourceRef, 'CDC sink resource');
  if(resourceRef.schema !== 'cdeadmin.resource-ref.v1') throw new TypeError(
    'CDC sink requires a provider ResourceRef.'
  );
  const credentialRef = input.credentialRef ? validateCDCReference(
    input.credentialRef, 'CDC sink credential reference'
  ) : null;
  if(credentialRef && credentialRef.schema !== 'cdeadmin.credential-ref.v1') throw new TypeError(
    'CDC sink credential must be a CredentialRef.'
  );
  return immutable({schema: 'cdeadmin.cdc-sink-binding.v1', resourceRef, credentialRef,
    serialization: platformValue(input.serialization, 'CDC sink serialization'),
    nativeDetails: object(input.nativeDetails, 'CDC sink native details')});
}

export function validateDeliveryPolicy(input={guarantee: 'unknown'}) {
  plainObject(input, 'CDC delivery policy'); noRawSecrets(input, 'CDC delivery policy');
  const guarantee = platformValue(input.guarantee ?? 'unknown', 'CDC delivery guarantee');
  if(!CDC_DELIVERY_GUARANTEES.includes(guarantee)) throw new TypeError(
    'CDC delivery guarantee is invalid.'
  );
  const proof = object(input.proof, 'CDC delivery proof');
  if(guarantee === 'exactly_once_proven') {
    for(const stage of ['sourceCapture', 'transport', 'sinkApplication']) {
      if(!proof[stage]) throw new TypeError(
        `Exactly-once delivery requires ${stage} proof evidence.`
      );
    }
  }
  return immutable({schema: 'cdeadmin.cdc-delivery-policy.v1', guarantee,
    deduplication: object(input.deduplication, 'CDC delivery deduplication'), proof,
    nativeDetails: object(input.nativeDetails, 'CDC delivery native details')});
}

export function validateSchemaChange(input) {
  plainObject(input, 'CDC schema change'); noRawSecrets(input, 'CDC schema change');
  const classification = platformValue(input.classification, 'CDC schema-change classification');
  if(!CDC_SCHEMA_CHANGE_CLASSES.includes(classification)) throw new TypeError(
    'CDC schema-change classification is invalid.'
  );
  const action = platformValue(input.action, 'CDC schema-change action');
  if(!CDC_EVOLUTION_ACTIONS.includes(action)) throw new TypeError(
    'CDC schema-change action is invalid.'
  );
  if(['breaking', 'unknown'].includes(classification) && action === 'auto_apply_compatible') {
    throw new TypeError('Breaking or unknown schema changes cannot be auto-applied.');
  }
  return immutable({schema: 'cdeadmin.cdc-schema-change.v1',
    id: platformValue(input.id, 'CDC schema-change ID'), classification, action,
    description: String(input.description ?? ''), detectedSchemaVersion:
      optionalText(input.detectedSchemaVersion, 'CDC detected schema version'),
    mapping: object(input.mapping, 'CDC schema-change mapping'),
    nativeDetails: object(input.nativeDetails, 'CDC schema-change native details')});
}

export function validateEvolutionPolicy(input={defaultAction: 'pause_and_review'}) {
  plainObject(input, 'CDC schema-evolution policy'); noRawSecrets(input, 'CDC evolution policy');
  const defaultAction = platformValue(
    input.defaultAction ?? 'pause_and_review', 'CDC default evolution action'
  );
  if(!CDC_EVOLUTION_ACTIONS.includes(defaultAction)) throw new TypeError(
    'CDC default evolution action is invalid.'
  );
  return immutable({schema: 'cdeadmin.cdc-evolution-policy.v1', defaultAction,
    changes: unique(input.changes ?? [], 'CDC schema change', validateSchemaChange),
    nativeDetails: object(input.nativeDetails, 'CDC evolution native details')});
}

export function validateAlert(input) {
  plainObject(input, 'CDC alert'); noRawSecrets(input, 'CDC alert');
  const event = platformValue(input.event, 'CDC alert event');
  const threshold = object(input.threshold, 'CDC alert threshold');
  if(event === 'lag' && (!Number.isFinite(Number(threshold.maximum)) ||
      Number(threshold.maximum) < 0)) throw new TypeError(
    'CDC lag alert requires a non-negative maximum threshold.'
  );
  return immutable({schema: 'cdeadmin.cdc-alert.v1', id: platformValue(input.id, 'CDC alert ID'),
    event, threshold,
    enabled: input.enabled === undefined ? true : Boolean(input.enabled),
    nativeDetails: object(input.nativeDetails, 'CDC alert native details')});
}

export function createCDCContent(input={}) {
  plainObject(input, 'CDC asset content'); noRawSecrets(input, 'CDC asset content');
  const filters = unique(input.filters ?? [], 'CDC filter', validateEventTransform);
  const transforms = unique(input.transforms ?? [], 'CDC transform', validateEventTransform);
  if(filters.some((item) => item.kind !== 'filter')) throw new TypeError(
    'CDC filters may contain only filter transforms.'
  );
  if(transforms.some((item) => item.kind === 'filter')) throw new TypeError(
    'CDC filter transforms belong in the filters collection.'
  );
  return immutable({schema: CDC_ASSET_SCHEMA, schemaVersion: 1, moduleId: CDC_MODULE_ID,
    name: String(input.name ?? ''), description: String(input.description ?? ''),
    source: input.source ? validateCaptureSource(input.source) : null,
    filters, transforms,
    sink: input.sink ? validateSinkBinding(input.sink) : null,
    deliveryPolicy: validateDeliveryPolicy(input.deliveryPolicy),
    schemaEvolutionPolicy: validateEvolutionPolicy(input.schemaEvolutionPolicy),
    alerts: unique(input.alerts ?? [], 'CDC alert', validateAlert),
    visualLayout: object(input.visualLayout, 'CDC visual layout'),
    extensions: object(input.extensions, 'CDC extensions')});
}

function canonical(value) {
  if(Array.isArray(value)) return value.map(canonical);
  if(!value || typeof value !== 'object') return value;
  return Object.fromEntries(Object.keys(value).sort().map((key) => [key, canonical(value[key])]));
}

export function exportCDCAsset(input) {
  return `${JSON.stringify(canonical(createCDCContent(input)), null, 2)}\n`;
}

export function importCDCAsset(source) {
  if(typeof source !== 'string' || !source || source.length > 16 * 1024 * 1024) {
    throw new TypeError('CDC source is invalid or exceeds 16 MiB.');
  }
  try { return createCDCContent(JSON.parse(source)); }
  catch(error) { throw new TypeError(`Invalid CDC source: ${error.message}`); }
}

export function validateEventEnvelope(input) {
  plainObject(input, 'CDC event envelope'); noRawSecrets(input, 'CDC event envelope');
  const operation = platformValue(input.operation, 'CDC event operation');
  if(!CDC_OPERATIONS.includes(operation)) throw new TypeError('CDC event operation is invalid.');
  const result = {schema: 'cdeadmin.cdc-event-envelope.v1', operation,
    sourceResourceRef: validateCDCReference(input.sourceResourceRef, 'CDC event source'),
    captureTime: platformValue(input.captureTime, 'CDC event capture time'),
    sourcePosition: object(input.sourcePosition, 'CDC event source position')};
  ['eventId', 'eventTime', 'transactionId', 'schemaVersion'].forEach((field) => {
    if(input[field] !== undefined) result[field] = optionalText(input[field], `CDC event ${field}`);
  });
  ['key', 'before', 'after', 'changedFields', 'headers', 'metadata'].forEach((field) => {
    if(input[field] !== undefined) result[field] = input[field];
  });
  noRawSecrets(result, 'CDC event envelope');
  return immutable(result);
}

export function validateCheckpoint(input) {
  plainObject(input, 'CDC checkpoint'); noRawSecrets(input, 'CDC checkpoint');
  return immutable({schema: 'cdeadmin.cdc-checkpoint.v1',
    nativeCursor: object(input.nativeCursor, 'CDC native checkpoint cursor'),
    taskRevision: platformValue(input.taskRevision, 'CDC checkpoint task revision'),
    capturedAt: platformValue(input.capturedAt, 'CDC checkpoint capture time'),
    nativeDetails: object(input.nativeDetails, 'CDC checkpoint native details')});
}

export function validateReplayRequest(input) {
  plainObject(input, 'CDC replay request'); noRawSecrets(input, 'CDC replay request');
  const target = validateCDCReference(input.target, 'CDC replay target');
  if(target.schema !== 'cdeadmin.resource-ref.v1') throw new TypeError(
    'CDC replay target must be a provider ResourceRef.'
  );
  const from = platformValue(input.from, 'CDC replay start', 16384);
  const to = platformValue(input.to, 'CDC replay end', 16384);
  if(from === to) throw new TypeError('CDC replay range must have distinct boundaries.');
  const estimatedEventCount = input.estimatedEventCount === undefined ||
    input.estimatedEventCount === null ? null : nonnegativeInteger(
      input.estimatedEventCount, 'CDC replay event estimate'
    );
  return immutable({schema: 'cdeadmin.cdc-replay-request.v1', from, to, target,
    estimatedEventCount,
    idempotencyAssessment: optionalText(
      input.idempotencyAssessment, 'CDC replay idempotency assessment', 16384
    ) ?? '',
    schemaCompatibility: optionalText(
      input.schemaCompatibility, 'CDC replay schema compatibility', 16384
    ) ?? '',
    production: Boolean(input.production),
    productionConfirmed: Boolean(input.productionConfirmed),
    nativeDetails: object(input.nativeDetails, 'CDC replay native details')});
}

export function cdcAssetRequest({projectId, assetId, name, path,
  expectedVersion=0, content}) {
  const canonicalContent = createCDCContent(content); const references = [];
  if(canonicalContent.source) references.push(canonicalContent.source.resourceRef);
  if(canonicalContent.source?.credentialRef) references.push(canonicalContent.source.credentialRef);
  if(canonicalContent.sink) references.push(canonicalContent.sink.resourceRef);
  if(canonicalContent.sink?.credentialRef) references.push(canonicalContent.sink.credentialRef);
  const uniqueRefs = [...new Map(references.map((ref) => [cdcReferenceKey(ref), ref])).values()];
  return immutable({asset_type: CDC_ASSET_TYPE,
    name: platformValue(name, 'CDC asset name'), path,
    schema_name: CDC_ASSET_TYPE, schema_version: 1,
    expected_version: expectedVersion, content: canonicalContent,
    metadata: {moduleId: CDC_MODULE_ID,
      captureMechanism: canonicalContent.source?.captureMechanism ?? null},
    dependency_references: uniqueRefs.filter((ref) =>
      ref.schema === 'cdeadmin.asset-ref.v1'),
    resource_bindings: uniqueRefs.filter((ref) =>
      ref.schema === 'cdeadmin.resource-ref.v1'),
    validation_state: 'valid', validation_details: [], projectId, assetId});
}
