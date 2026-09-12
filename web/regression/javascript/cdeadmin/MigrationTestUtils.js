import {
  FederatedSearchService, RelationshipGraphService, TaskExecutionService,
} from 'sources/cdeadmin_ui/platform/CoordinationServices';
import {
  MigrationAdapterRegistry, MigrationService,
} from 'sources/cdeadmin_ui/modules/migration/MigrationService';

export const sourceRef = Object.freeze({schema: 'cdeadmin.resource-ref.v1',
  canonical: 'firebird://source/operations', provider: 'firebird'});
export const targetRef = Object.freeze({schema: 'cdeadmin.resource-ref.v1',
  canonical: 'postgresql://target/operations', provider: 'postgresql'});

export function finding(overrides={}) {
  return {id: 'orders-table', sourceRef, targetRef, objectKind: 'table',
    category: 'supported_exact', evidenceSource: 'provider_adapter',
    evidence: {rule: 'table'}, message: 'Exact table mapping', blocker: false,
    waived: false, waiverRef: null, nativeDetails: {sourceType: 'INTEGER'}, ...overrides};
}
export function assessment(overrides={}) {
  return {id: 'assessment-one', sourceRevision: 'source-rev-1', targetRevision: 'target-rev-1',
    findings: [finding()], estimates: {rows: 2}, evidence: {observed: true},
    completedAt: '2026-09-12T00:00:00Z', nativeDetails: {}, ...overrides};
}
export function mapping(overrides={}) {
  return {id: 'orders-map', sourceRef, targetRef, mappingKind: 'type',
    category: 'supported_exact', decision: 'accepted', sourceNativeType: 'INTEGER',
    targetNativeType: 'integer', expression: null, lossy: false, lossAcknowledged: false,
    behaviorChange: false, evidence: {rule: 'integer'}, nativeDetails: {}, ...overrides};
}
export function schemaOperation(overrides={}) {
  return {id: 'create-orders', action: 'create', sourceRef, targetRef, dependencies: [],
    preconditions: [{kind: 'absent'}], rollback: [{action: 'drop'}], risk: 'low',
    nativeDefinition: {statement: 'CREATE TABLE orders (...)'}, ...overrides};
}
export function cutoverStep(overrides={}) {
  return {id: 'freeze', label: 'Freeze source writes', action: 'freeze', dependencies: [],
    preconditions: [{check: 'source_writable'}], expectedEvidence: {frozen: true},
    providerMutation: true, nativeDetails: {}, ...overrides};
}
export function rollbackStep(overrides={}) {
  return {id: 'unfreeze', label: 'Unfreeze source writes', action: 'unfreeze', dependencies: [],
    preconditions: [], expectedEvidence: {writable: true}, providerMutation: true,
    nativeDetails: {}, ...overrides};
}
export function definition(overrides={}) {
  return {name: 'Operations migration', description: 'Firebird to PostgreSQL',
    sourceBinding: sourceRef, targetBinding: targetRef, strategy: 'offline', phase: 'design',
    assessment: assessment(), mappingSet: [mapping()],
    schemaPlan: {id: 'schema-one', operations: [schemaOperation()], schemaCompareRef: {
      schema: 'cdeadmin.asset-ref.v1', projectId: 'project-one', assetId: 'compare-one'},
    expectedTargetRevision: 'target-rev-1', nativeDetails: {}},
    dataMovePlan: {id: 'move-one', units: [{id: 'orders-copy', sourceRef, targetRef,
      partition: {range: 'all'}, orderingKey: ['id'], batchSize: 1000, parallelism: 1,
      restartPolicy: 'resume_committed', transformRef: null, nativeDetails: {}}],
    concurrency: 1, consistencyBoundary: {snapshot: 'source-rev-1'}, errorPolicy: {stop: true},
    nativeDetails: {}}, cdcPlanRef: null,
    validationPlan: [{id: 'orders-count', type: 'row_document_counts', sourceRef, targetRef,
      canonicalization: null, policy: {equal: true}, blocking: true, qualityAssetRef: null,
      nativeDetails: {}}],
    cutoverPlan: {id: 'cutover-one', steps: [cutoverStep(), cutoverStep({id: 'endpoint-switch',
      label: 'Switch endpoint', action: 'switch', dependencies: ['freeze']})], cdcLagThreshold: null,
    armWindowMinutes: 30, targetEnvironment: 'production', targetConnection: 'target-one',
    endpointChange: {route: 'target'}, postChecks: [{check: 'target_readable'}], nativeDetails: {}},
    rollbackPlan: {id: 'rollback-one', classification: 'fully_automatable',
      pointOfNoReturn: 'endpoint-switch', deadline: null, conditions: [{when: 'verification_failed'}],
      steps: [rollbackStep()], recoverability: {source: true}, nativeDetails: {}},
    waivers: [], extensions: {}, ...overrides};
}

export function providerResult(value, overrides={}) {
  return {supportState: 'supported_native', providerVersion: 'test-1',
    evidence: {runtime: true}, warnings: [], nativeDetails: {},
    readCapabilities: ['migration.read'], writeCapabilities: ['migration.write'],
    discoveryCapabilities: ['migration.assess'], nativeMechanisms: ['native-test'],
    versionConstraints: [], limitations: [], runtimeEvidence: {observed: true,
      checkpointLookup: {durable: true, authoritative: true, storeKind: 'test'},
      checkpointPersistence: {durable: true, receipt: 'test-receipt',
        resumedFrom: null, noDuplicateOrSkip: true}}, value, ...overrides};
}

export function adapter(calls=[]) {
  const checkpoints = new Map();
  const record = (name, output) => async (input) => {
    calls.push({name, input}); return providerResult(typeof output === 'function' ? output(input) : output);
  };
  return {assessSource: record('assessSource', assessment()),
    proposeTypeMappings: record('proposeTypeMappings', [mapping({id: 'integer-proposal',
      decision: 'unresolved'})]),
    prepareSchemaOperation: record('prepareSchemaOperation', {prepared: true}),
    prepareCopyUnit: record('prepareCopyUnit', (input) => ({prepared: true,
      resumeCheckpoint: checkpoints.get(input.unit.id) ?? null})),
    verifyProviderState: record('verifyProviderState', {state: 'passed', sourceValue: 2,
      targetValue: 2, evidence: {checked: true}}),
    executeCopyUnit: async (input) => {
      calls.push({name: 'executeCopyUnit', input});
      const checkpoint = {
        id: `checkpoint-${input.unit.id}`, unitId: input.unit.id, state: 'committed',
        sourceRevision: 'source-rev-1', sourceBoundary: {snapshot: 'source-rev-1'},
        lastCompleted: {id: 2}, targetProgress: {id: 2}, rowDocumentCount: 2,
        byteCount: 128, retryState: {attempt: 1}, committedAt: '2026-09-12T00:00:00Z',
        nativeDetails: {}};
      checkpoints.set(input.unit.id, checkpoint); return providerResult({checkpoint}, {
        runtimeEvidence: {observed: true,
          checkpointPersistence: {durable: true, receipt: `receipt-${checkpoint.id}`,
            resumedFrom: input.resumeCheckpoint?.id ?? null, noDuplicateOrSkip: true}}});
    },
    executeCutoverStep: record('executeCutoverStep', {completed: true}),
    executeRollbackStep: record('executeRollbackStep', {completed: true})};
}

export function fixture(options={}) {
  const tasks = new TaskExecutionService({now: () => options.now ?? '2026-09-12T00:00:00Z'});
  const relationships = new RelationshipGraphService(); const search = new FederatedSearchService();
  const adapters = new MigrationAdapterRegistry(); const calls = options.calls ?? [];
  adapters.register('firebird', options.sourceAdapter ?? adapter(calls));
  adapters.register('postgresql', options.targetAdapter ?? adapter(calls));
  const service = new MigrationService({tasks, relationships, search, adapters,
    projectAssets: options.projectAssets, events: options.events,
    now: () => options.now ?? '2026-09-12T00:00:00Z'});
  return {service, tasks, relationships, search, adapters, calls};
}

export function user() {
  return {id: 'reviewer', permissions: ['migration.view', 'migration.edit',
    'migration.execute', 'migration.cutover', 'migration.rollback', 'migration.admin']};
}
