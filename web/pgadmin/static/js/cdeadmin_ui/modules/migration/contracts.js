/////////////////////////////////////////////////////////////
// Migration Planning canonical assets and runtime contracts.
/////////////////////////////////////////////////////////////

import {immutable, noRawSecrets, plainObject, platformValue} from '../../platform/serviceUtils';

export const MIGRATION_MODULE_ID = 'cdeadmin.migration';
export const MIGRATION_SERVICE_ID = 'migration.runtime';
export const MIGRATION_ASSET_TYPE = 'cdeadmin.migration.v1';
export const MIGRATION_ASSET_SCHEMA = 'cdeadmin.migration.asset.v1';
export const MIGRATION_PHASES = Object.freeze(['discover', 'assess', 'design', 'dry_run',
  'provision', 'initial_copy', 'incremental_sync', 'validate', 'cutover_ready', 'cutover',
  'verify', 'complete', 'rollback']);
export const MIGRATION_STRATEGIES = Object.freeze(['offline', 'online_with_cdc',
  'staged_dual_run', 'copy_then_cutover', 'schema_only', 'data_only', 'validation_only']);
export const ASSESSMENT_CATEGORIES = Object.freeze(['supported_exact', 'supported_with_mapping',
  'supported_with_behavior_change', 'manual_conversion_required', 'blocked', 'not_selected',
  'unknown']);
export const EVIDENCE_SOURCES = Object.freeze(['mapping_rule', 'provider_adapter',
  'executed_test', 'manual_decision', 'unknown']);
export const VERIFICATION_TYPES = Object.freeze(['object_counts', 'row_document_counts',
  'deterministic_hashes', 'sampled_content', 'query_result', 'data_quality_rules',
  'provider_native']);
export const ROLLBACK_CLASSES = Object.freeze(['fully_automatable', 'partially_automatable',
  'manual_runbook', 'not_available_after_point']);
export const MIGRATION_UI_STATES = Object.freeze(['loading', 'empty', 'ready', 'stale',
  'partial', 'permission_denied', 'disconnected', 'validation_error', 'runtime_failure',
  'read_only', 'background_task_active']);

const REFERENCE_SCHEMAS = new Set(['cdeadmin.resource-ref.v1', 'cdeadmin.asset-ref.v1',
  'cdeadmin.external-ref.v1', 'cdeadmin.task-ref.v1', 'cdeadmin.result-ref.v1']);

function object(value, label, maximum=1024 * 1024) {
  const result = plainObject(value ?? {}, label); noRawSecrets(result, label);
  if(JSON.stringify(result).length > maximum) throw new TypeError(`${label} exceeds its size limit.`);
  return immutable({...result});
}
function exact(input, fields, label) {
  const unknown = Object.keys(input).filter((field) => !fields.includes(field));
  if(unknown.length) throw new TypeError(`${label} contains unsupported field ${unknown[0]}.`);
}
function list(value, label, mapper, maximum=10000) {
  if(!Array.isArray(value) || value.length > maximum) throw new TypeError(
    `${label} must be an array containing no more than ${maximum} items.`
  );
  return value.map(mapper);
}
function unique(value, label, mapper, maximum=10000) {
  const result = list(value ?? [], label, mapper, maximum); const ids = new Set();
  result.forEach((item) => {
    if(ids.has(item.id)) throw new TypeError(`Duplicate ${label}: ${item.id}`); ids.add(item.id);
  });
  return result.sort((left, right) => left.id.localeCompare(right.id));
}
function orderedUnique(value, label, mapper, maximum=10000) {
  const result = list(value ?? [], label, mapper, maximum); const ids = new Set();
  result.forEach((item) => {
    if(ids.has(item.id)) throw new TypeError(`Duplicate ${label}: ${item.id}`); ids.add(item.id);
  });
  return result;
}
function validateDependencies(items, label) {
  const ids = new Set(items.map((item) => item.id));
  items.forEach((item) => item.dependencies.forEach((dependency) => {
    if(!ids.has(dependency)) throw new TypeError(
      `${label} ${item.id} has unavailable dependency ${dependency}.`
    );
    if(dependency === item.id) throw new TypeError(`${label} ${item.id} cannot depend on itself.`);
  }));
}
function text(value, label, maximum=16384, optional=false) {
  if(optional && (value === undefined || value === null || value === '')) return null;
  return platformValue(value, label, maximum);
}
function integer(value, label, minimum=0, maximum=Number.MAX_SAFE_INTEGER) {
  if(typeof value === 'boolean' || !Number.isInteger(Number(value)) || Number(value) < minimum ||
      Number(value) > maximum) throw new TypeError(`${label} is invalid.`);
  return Number(value);
}
function number(value, label, minimum=0, maximum=Number.MAX_VALUE) {
  if(typeof value === 'boolean' || !Number.isFinite(Number(value)) || Number(value) < minimum ||
      Number(value) > maximum) throw new TypeError(`${label} is invalid.`);
  return Number(value);
}
function strings(value, label, maximum=10000) {
  return [...new Set(list(value ?? [], label, (item) => text(item, `${label} item`), maximum))].sort();
}

export function validateMigrationReference(input, label='Migration reference') {
  plainObject(input, label); noRawSecrets(input, label);
  const schema = text(input.schema, `${label} schema`);
  if(!REFERENCE_SCHEMAS.has(schema)) throw new TypeError(`${label} schema is unsupported.`);
  if(schema === 'cdeadmin.resource-ref.v1') {
    text(input.canonical, `${label} canonical identity`, 8192);
    text(input.provider ?? input.providerId, `${label} provider`);
  } else if(schema === 'cdeadmin.asset-ref.v1') {
    text(input.projectId, `${label} project ID`); text(input.assetId, `${label} asset ID`);
  } else text(input.id, `${label} identity`, 8192);
  return immutable({...input});
}

export function migrationReferenceKey(input) {
  const ref = validateMigrationReference(input);
  if(ref.schema === 'cdeadmin.resource-ref.v1') return `resource:${ref.canonical}`;
  if(ref.schema === 'cdeadmin.asset-ref.v1') return `asset:${ref.projectId}/${ref.assetId}@${
    ref.assetVersion ?? ref.version ?? 'latest'}`;
  return `${ref.schema}:${ref.id}`;
}

export function validateAssessmentFinding(input) {
  plainObject(input, 'Migration assessment finding'); noRawSecrets(input, 'Migration finding');
  exact(input, ['schema', 'id', 'sourceRef', 'targetRef', 'objectKind', 'category',
    'evidenceSource', 'evidence', 'message', 'blocker', 'waived', 'waiverRef',
    'nativeDetails'], 'Migration assessment finding');
  const category = text(input.category, 'Migration assessment category');
  if(!ASSESSMENT_CATEGORIES.includes(category)) throw new TypeError(
    'Migration assessment category is invalid.'
  );
  const evidenceSource = text(input.evidenceSource ?? 'unknown', 'Migration evidence source');
  if(!EVIDENCE_SOURCES.includes(evidenceSource)) throw new TypeError(
    'Migration evidence source is invalid.'
  );
  return immutable({schema: 'cdeadmin.migration-finding.v1',
    id: text(input.id, 'Migration finding ID'),
    sourceRef: validateMigrationReference(input.sourceRef, 'Migration finding source'),
    targetRef: input.targetRef == null ? null : validateMigrationReference(
      input.targetRef, 'Migration finding target'),
    objectKind: text(input.objectKind, 'Migration finding object kind'), category,
    evidenceSource, evidence: object(input.evidence, 'Migration finding evidence'),
    message: text(input.message, 'Migration finding message', 16384),
    blocker: category === 'blocked' || Boolean(input.blocker), waived: Boolean(input.waived),
    waiverRef: text(input.waiverRef, 'Migration finding waiver reference', 4096, true),
    nativeDetails: object(input.nativeDetails, 'Migration finding native details')});
}

export function validateAssessment(input) {
  if(input == null) return null;
  plainObject(input, 'Migration assessment'); noRawSecrets(input, 'Migration assessment');
  exact(input, ['schema', 'id', 'sourceRevision', 'targetRevision', 'findings', 'estimates',
    'evidence', 'completedAt', 'nativeDetails'], 'Migration assessment');
  const findings = unique(input.findings, 'Migration findings', validateAssessmentFinding);
  return immutable({schema: 'cdeadmin.migration-assessment.v1',
    id: text(input.id, 'Migration assessment ID'),
    sourceRevision: text(input.sourceRevision, 'Migration source revision', 4096),
    targetRevision: text(input.targetRevision, 'Migration target revision', 4096),
    findings, estimates: object(input.estimates, 'Migration estimates'),
    evidence: object(input.evidence, 'Migration assessment evidence'),
    completedAt: text(input.completedAt, 'Migration assessment completion time'),
    nativeDetails: object(input.nativeDetails, 'Migration assessment native details')});
}

export function validateMigrationMapping(input) {
  plainObject(input, 'Migration mapping'); noRawSecrets(input, 'Migration mapping');
  exact(input, ['schema', 'id', 'sourceRef', 'targetRef', 'mappingKind', 'category', 'decision',
    'sourceNativeType', 'targetNativeType', 'expression', 'lossy', 'lossAcknowledged',
    'behaviorChange', 'evidence', 'nativeDetails'], 'Migration mapping');
  const category = text(input.category, 'Migration mapping category');
  if(!ASSESSMENT_CATEGORIES.includes(category)) throw new TypeError('Migration mapping category is invalid.');
  const decision = text(input.decision ?? 'unresolved', 'Migration mapping decision');
  if(!['unresolved', 'accepted', 'rejected', 'manual'].includes(decision)) throw new TypeError(
    'Migration mapping decision is invalid.'
  );
  return immutable({schema: 'cdeadmin.migration-mapping.v1',
    id: text(input.id, 'Migration mapping ID'),
    sourceRef: validateMigrationReference(input.sourceRef, 'Migration mapping source'),
    targetRef: input.targetRef == null ? null : validateMigrationReference(
      input.targetRef, 'Migration mapping target'),
    mappingKind: text(input.mappingKind, 'Migration mapping kind'), category, decision,
    sourceNativeType: text(input.sourceNativeType, 'Migration source native type', 4096, true),
    targetNativeType: text(input.targetNativeType, 'Migration target native type', 4096, true),
    expression: text(input.expression, 'Migration mapping expression', 65536, true),
    lossy: Boolean(input.lossy), lossAcknowledged: Boolean(input.lossAcknowledged),
    behaviorChange: Boolean(input.behaviorChange),
    evidence: object(input.evidence, 'Migration mapping evidence'),
    nativeDetails: object(input.nativeDetails, 'Migration mapping native details')});
}

export function validateSchemaOperation(input) {
  plainObject(input, 'Migration schema operation'); noRawSecrets(input, 'Migration schema operation');
  exact(input, ['schema', 'id', 'action', 'sourceRef', 'targetRef', 'dependencies',
    'preconditions', 'rollback', 'risk', 'nativeDefinition'], 'Migration schema operation');
  return immutable({schema: 'cdeadmin.migration-schema-operation.v1',
    id: text(input.id, 'Migration schema operation ID'),
    action: text(input.action, 'Migration schema operation action'),
    sourceRef: input.sourceRef == null ? null : validateMigrationReference(
      input.sourceRef, 'Migration schema operation source'),
    targetRef: validateMigrationReference(input.targetRef, 'Migration schema operation target'),
    dependencies: strings(input.dependencies, 'Migration schema dependencies'),
    preconditions: list(input.preconditions ?? [], 'Migration schema preconditions',
      (item) => object(item, 'Migration schema precondition')),
    rollback: list(input.rollback ?? [], 'Migration schema rollback',
      (item) => object(item, 'Migration schema rollback item')),
    risk: text(input.risk ?? 'unknown', 'Migration schema operation risk'),
    nativeDefinition: object(input.nativeDefinition, 'Migration schema native definition')});
}

export function validateSchemaPlan(input) {
  if(input == null) return null;
  plainObject(input, 'Migration schema plan'); noRawSecrets(input, 'Migration schema plan');
  exact(input, ['schema', 'id', 'operations', 'schemaCompareRef', 'expectedTargetRevision',
    'nativeDetails'], 'Migration schema plan');
  const operations = unique(input.operations, 'Migration schema operations', validateSchemaOperation);
  const ids = new Set(operations.map((item) => item.id));
  operations.forEach((item) => item.dependencies.forEach((dependency) => {
    if(!ids.has(dependency)) throw new TypeError(
      `Migration schema operation ${item.id} has unavailable dependency ${dependency}.`
    );
  }));
  return immutable({schema: 'cdeadmin.migration-schema-plan.v1',
    id: text(input.id, 'Migration schema plan ID'), operations,
    schemaCompareRef: input.schemaCompareRef == null ? null : validateMigrationReference(
      input.schemaCompareRef, 'Schema Comparison plan reference'),
    expectedTargetRevision: text(input.expectedTargetRevision,
      'Migration expected target revision', 4096, true),
    nativeDetails: object(input.nativeDetails, 'Migration schema plan native details')});
}

export function validateCopyUnit(input) {
  plainObject(input, 'Migration copy unit'); noRawSecrets(input, 'Migration copy unit');
  exact(input, ['schema', 'id', 'sourceRef', 'targetRef', 'partition', 'orderingKey',
    'batchSize', 'parallelism', 'restartPolicy', 'transformRef', 'nativeDetails'],
  'Migration copy unit');
  return immutable({schema: 'cdeadmin.migration-copy-unit.v1',
    id: text(input.id, 'Migration copy unit ID'),
    sourceRef: validateMigrationReference(input.sourceRef, 'Migration copy source'),
    targetRef: validateMigrationReference(input.targetRef, 'Migration copy target'),
    partition: object(input.partition, 'Migration copy partition'),
    orderingKey: strings(input.orderingKey, 'Migration copy ordering keys'),
    batchSize: integer(input.batchSize ?? 1000, 'Migration copy batch size', 1, 1000000),
    parallelism: integer(input.parallelism ?? 1, 'Migration copy parallelism', 1, 1024),
    restartPolicy: text(input.restartPolicy ?? 'resume_committed', 'Migration copy restart policy'),
    transformRef: input.transformRef == null ? null : validateMigrationReference(
      input.transformRef, 'Migration copy transform'),
    nativeDetails: object(input.nativeDetails, 'Migration copy native details')});
}

export function validateDataMovePlan(input) {
  plainObject(input, 'Migration data move plan'); noRawSecrets(input, 'Migration data move plan');
  exact(input, ['schema', 'id', 'units', 'concurrency', 'consistencyBoundary', 'errorPolicy',
    'nativeDetails'], 'Migration data move plan');
  return immutable({schema: 'cdeadmin.migration-data-move-plan.v1',
    id: text(input.id, 'Migration data move plan ID'),
    units: unique(input.units, 'Migration copy units', validateCopyUnit),
    concurrency: integer(input.concurrency ?? 1, 'Migration copy concurrency', 1, 1024),
    consistencyBoundary: object(input.consistencyBoundary, 'Migration consistency boundary'),
    errorPolicy: object(input.errorPolicy, 'Migration copy error policy'),
    nativeDetails: object(input.nativeDetails, 'Migration data move native details')});
}

export function validateVerification(input) {
  plainObject(input, 'Migration verification'); noRawSecrets(input, 'Migration verification');
  exact(input, ['schema', 'id', 'type', 'sourceRef', 'targetRef', 'canonicalization', 'policy',
    'blocking', 'qualityAssetRef', 'nativeDetails'], 'Migration verification');
  const type = text(input.type, 'Migration verification type');
  if(!VERIFICATION_TYPES.includes(type)) throw new TypeError('Migration verification type is invalid.');
  if(type === 'deterministic_hashes' && !input.canonicalization) throw new TypeError(
    'Deterministic hash verification requires canonicalization rules.'
  );
  return immutable({schema: 'cdeadmin.migration-verification.v1',
    id: text(input.id, 'Migration verification ID'), type,
    sourceRef: validateMigrationReference(input.sourceRef, 'Migration verification source'),
    targetRef: validateMigrationReference(input.targetRef, 'Migration verification target'),
    canonicalization: input.canonicalization == null ? null : object(
      input.canonicalization, 'Migration verification canonicalization'),
    policy: object(input.policy, 'Migration verification policy'),
    blocking: input.blocking !== false,
    qualityAssetRef: input.qualityAssetRef == null ? null : validateMigrationReference(
      input.qualityAssetRef, 'Migration quality asset'),
    nativeDetails: object(input.nativeDetails, 'Migration verification native details')});
}

export function validateRunbookStep(input, label='Migration runbook step') {
  plainObject(input, label); noRawSecrets(input, label);
  exact(input, ['schema', 'id', 'label', 'action', 'dependencies', 'preconditions',
    'expectedEvidence', 'providerMutation', 'nativeDetails'], label);
  return immutable({schema: 'cdeadmin.migration-runbook-step.v1',
    id: text(input.id, `${label} ID`), label: text(input.label, `${label} label`),
    action: text(input.action, `${label} action`, 16384),
    dependencies: strings(input.dependencies, `${label} dependencies`),
    preconditions: list(input.preconditions ?? [], `${label} preconditions`,
      (item) => object(item, `${label} precondition`)),
    expectedEvidence: object(input.expectedEvidence, `${label} expected evidence`),
    providerMutation: Boolean(input.providerMutation),
    nativeDetails: object(input.nativeDetails, `${label} native details`)});
}

export function validateCutoverPlan(input) {
  plainObject(input, 'Migration cutover plan'); noRawSecrets(input, 'Migration cutover plan');
  exact(input, ['schema', 'id', 'steps', 'cdcLagThreshold', 'armWindowMinutes',
    'targetEnvironment', 'targetConnection', 'endpointChange', 'postChecks', 'nativeDetails'],
  'Migration cutover plan');
  const steps = orderedUnique(input.steps, 'Migration cutover steps', (item) => validateRunbookStep(
    item, 'Migration cutover step'));
  if(!steps.length) throw new TypeError('Migration cutover plan requires at least one step.');
  validateDependencies(steps, 'Migration cutover step');
  return immutable({schema: 'cdeadmin.migration-cutover-plan.v1',
    id: text(input.id, 'Migration cutover plan ID'), steps,
    cdcLagThreshold: input.cdcLagThreshold == null ? null : number(
      input.cdcLagThreshold, 'Migration CDC lag threshold'),
    armWindowMinutes: integer(input.armWindowMinutes ?? 30,
      'Migration cutover arm window', 1, 1440),
    targetEnvironment: text(input.targetEnvironment, 'Migration target environment'),
    targetConnection: text(input.targetConnection, 'Migration target connection'),
    endpointChange: object(input.endpointChange, 'Migration endpoint change'),
    postChecks: list(input.postChecks ?? [], 'Migration post-cutover checks',
      (item) => object(item, 'Migration post-cutover check')),
    nativeDetails: object(input.nativeDetails, 'Migration cutover native details')});
}

export function validateRollbackPlan(input) {
  plainObject(input, 'Migration rollback plan'); noRawSecrets(input, 'Migration rollback plan');
  exact(input, ['schema', 'id', 'classification', 'pointOfNoReturn', 'deadline', 'conditions',
    'steps', 'recoverability', 'nativeDetails'], 'Migration rollback plan');
  const classification = text(input.classification, 'Migration rollback classification');
  if(!ROLLBACK_CLASSES.includes(classification)) throw new TypeError(
    'Migration rollback classification is invalid.'
  );
  const deadline = text(input.deadline, 'Migration rollback deadline', 128, true);
  if(deadline && !Number.isFinite(Date.parse(deadline))) throw new TypeError(
    'Migration rollback deadline must be an ISO date-time.'
  );
  return immutable({schema: 'cdeadmin.migration-rollback-plan.v1',
    id: text(input.id, 'Migration rollback plan ID'), classification,
    pointOfNoReturn: text(input.pointOfNoReturn, 'Migration point of no return', 4096, true), deadline,
    conditions: list(input.conditions ?? [], 'Migration rollback conditions',
      (item) => object(item, 'Migration rollback condition')),
    steps: (() => {
      const steps = orderedUnique(input.steps, 'Migration rollback steps',
        (item) => validateRunbookStep(item, 'Migration rollback step'));
      if(!steps.length) throw new TypeError('Migration rollback plan requires at least one step.');
      validateDependencies(steps, 'Migration rollback step'); return steps;
    })(),
    recoverability: object(input.recoverability, 'Migration recoverability'),
    nativeDetails: object(input.nativeDetails, 'Migration rollback native details')});
}

export function createMigrationContent(input={}) {
  plainObject(input, 'Migration asset content'); noRawSecrets(input, 'Migration asset content');
  exact(input, ['schema', 'schemaVersion', 'moduleId', 'name', 'description', 'sourceBinding',
    'targetBinding', 'strategy', 'phase', 'assessment', 'mappingSet', 'schemaPlan',
    'dataMovePlan', 'cdcPlanRef', 'validationPlan', 'cutoverPlan', 'rollbackPlan', 'waivers',
    'extensions'], 'Migration asset content');
  const strategy = text(input.strategy ?? 'offline', 'Migration strategy');
  if(!MIGRATION_STRATEGIES.includes(strategy) && !strategy.startsWith('native:')) {
    throw new TypeError('Migration strategy is invalid.');
  }
  const phase = text(input.phase ?? 'discover', 'Migration phase');
  if(!MIGRATION_PHASES.includes(phase)) throw new TypeError('Migration phase is invalid.');
  const content = immutable({schema: MIGRATION_ASSET_SCHEMA, schemaVersion: 1,
    moduleId: MIGRATION_MODULE_ID, name: String(input.name ?? ''),
    description: String(input.description ?? ''),
    sourceBinding: input.sourceBinding == null ? null : validateMigrationReference(
      input.sourceBinding, 'Migration source binding'),
    targetBinding: input.targetBinding == null ? null : validateMigrationReference(
      input.targetBinding, 'Migration target binding'),
    strategy, phase, assessment: validateAssessment(input.assessment),
    mappingSet: unique(input.mappingSet, 'Migration mappings', validateMigrationMapping),
    schemaPlan: validateSchemaPlan(input.schemaPlan),
    dataMovePlan: validateDataMovePlan(input.dataMovePlan ?? {id: 'data-move', units: []}),
    cdcPlanRef: input.cdcPlanRef == null ? null : validateMigrationReference(
      input.cdcPlanRef, 'Migration CDC plan'),
    validationPlan: unique(input.validationPlan, 'Migration verifications', validateVerification),
    cutoverPlan: input.cutoverPlan == null ? null : validateCutoverPlan(input.cutoverPlan),
    rollbackPlan: input.rollbackPlan == null ? null : validateRollbackPlan(input.rollbackPlan),
    waivers: list(input.waivers ?? [], 'Migration waivers',
      (item) => object(item, 'Migration waiver')),
    extensions: object(input.extensions, 'Migration extensions')});
  if((content.sourceBinding === null || content.targetBinding === null) && phase !== 'discover') {
    throw new TypeError('Configured migration phases require source and target bindings.');
  }
  if(strategy === 'online_with_cdc' && !content.cdcPlanRef) throw new TypeError(
    'Online migration requires a CDC plan reference.'
  );
  return content;
}

export function migrationAssetRequest(input={}) {
  const content = createMigrationContent(input.content ?? input);
  return immutable({asset_type: MIGRATION_ASSET_TYPE, schema_name: MIGRATION_ASSET_TYPE,
    schema_version: 1, name: input.name ?? (content.name || 'Migration plan'),
    path: input.path ?? 'migration/migration.json', expected_version: input.expectedVersion ?? 0,
    content, metadata: {moduleId: MIGRATION_MODULE_ID, ...(input.metadata ?? {})},
    dependency_references: input.dependencyReferences ?? [],
    resource_bindings: input.resourceBindings ?? [],
    validation_state: input.validationState ?? 'valid',
    validation_details: input.validationDetails ?? []});
}

export function exportMigrationDefinition(input, options={}) {
  plainObject(options, 'Migration export options'); noRawSecrets(options, 'Migration export options');
  exact(options, ['profile', 'provenance', 'exportedAt'], 'Migration export options');
  const profile = text(options.profile ?? 'portable', 'Migration export profile', 128);
  const content = createMigrationContent(input); const provenance = object(
    options.provenance ?? {}, 'Migration export provenance');
  return immutable({schema: 'cdeadmin.migration-interchange.v1', version: 1,
    moduleId: MIGRATION_MODULE_ID, assetType: MIGRATION_ASSET_TYPE,
    sourceSchema: content.schema, sourceVersion: content.schemaVersion,
    profile, exportedAt: text(options.exportedAt, 'Migration export time', 128),
    provenance, content, unsupportedContent: []});
}

export function importMigrationDefinition(input, options={}) {
  plainObject(input, 'Migration interchange document');
  noRawSecrets(input, 'Migration interchange document');
  exact(input, ['schema', 'version', 'moduleId', 'assetType', 'sourceSchema', 'sourceVersion',
    'profile', 'exportedAt', 'provenance', 'content', 'unsupportedContent'],
  'Migration interchange document');
  plainObject(options, 'Migration import options'); noRawSecrets(options, 'Migration import options');
  exact(options, ['importedAt'], 'Migration import options');
  if(input.schema !== 'cdeadmin.migration-interchange.v1' || input.version !== 1 ||
      input.assetType !== MIGRATION_ASSET_TYPE || input.sourceVersion !== 1) {
    throw new TypeError('Migration interchange version or asset type is unsupported.');
  }
  if(!Array.isArray(input.unsupportedContent) || input.unsupportedContent.length) throw new TypeError(
    'Migration import contains unsupported source content and cannot be imported without loss.'
  );
  const source = createMigrationContent(input.content);
  const importedAt = text(options.importedAt, 'Migration import time', 128);
  const interchange = immutable({schema: input.schema, version: input.version,
    sourceSchema: text(input.sourceSchema, 'Migration source schema'),
    sourceVersion: input.sourceVersion, profile: text(input.profile, 'Migration import profile', 128),
    exportedAt: text(input.exportedAt, 'Migration original export time', 128),
    importedAt, provenance: object(input.provenance, 'Migration import provenance')});
  return immutable({content: createMigrationContent({...source, extensions: {
    ...source.extensions, interchangeProvenance: interchange}}), interchange});
}

export function validateCopyCheckpoint(input) {
  plainObject(input, 'Migration copy checkpoint'); noRawSecrets(input, 'Migration checkpoint');
  exact(input, ['schema', 'id', 'unitId', 'state', 'sourceRevision', 'sourceBoundary',
    'lastCompleted', 'targetProgress', 'rowDocumentCount', 'byteCount', 'retryState',
    'committedAt', 'nativeDetails'], 'Migration copy checkpoint');
  const state = text(input.state, 'Migration checkpoint state');
  if(!['prepared', 'committed', 'failed'].includes(state)) throw new TypeError(
    'Migration checkpoint state is invalid.'
  );
  return immutable({schema: 'cdeadmin.migration-copy-checkpoint.v1',
    id: text(input.id, 'Migration checkpoint ID'),
    unitId: text(input.unitId, 'Migration checkpoint unit ID'), state,
    sourceRevision: text(input.sourceRevision, 'Migration checkpoint source revision', 8192, true),
    sourceBoundary: object(input.sourceBoundary, 'Migration checkpoint source boundary'),
    lastCompleted: object(input.lastCompleted, 'Migration checkpoint last completed range'),
    targetProgress: object(input.targetProgress, 'Migration checkpoint target progress'),
    rowDocumentCount: integer(input.rowDocumentCount ?? 0, 'Migration checkpoint row count'),
    byteCount: integer(input.byteCount ?? 0, 'Migration checkpoint byte count'),
    retryState: object(input.retryState, 'Migration checkpoint retry state'),
    committedAt: text(input.committedAt, 'Migration checkpoint committed time', 128, true),
    nativeDetails: object(input.nativeDetails, 'Migration checkpoint native details')});
}
