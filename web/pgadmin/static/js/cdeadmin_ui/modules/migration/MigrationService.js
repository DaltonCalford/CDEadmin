/////////////////////////////////////////////////////////////
// Migration orchestration, provider evidence and recovery.
/////////////////////////////////////////////////////////////

import {
  diagnosticsService, platformEventService, stablePlatformId,
} from '../../platform/PlatformRegistry';
import {abortError, immutable, noRawSecrets, plainObject, platformValue} from '../../platform/serviceUtils';
import {
  createMigrationContent, exportMigrationDefinition, importMigrationDefinition,
  MIGRATION_MODULE_ID, migrationAssetRequest, migrationReferenceKey,
  validateAssessment, validateCopyCheckpoint, validateMigrationMapping,
} from './contracts';
import {
  applyCheckpoint, canTransition, createCutoverArm, cutoverReadiness,
  normalizeVerificationResult, orderedRunbookSteps, orderedSchemaOperations, rollbackState,
  validateCutoverArm, validateMigrationPlan,
} from './MigrationEngine';

export const MIGRATION_TASKS = Object.freeze(['migration.assessment', 'migration.schema.apply',
  'migration.data.copy', 'migration.validation', 'migration.cutover', 'migration.rollback']);
export const MIGRATION_EVENTS = Object.freeze(['migration.assessment.completed',
  'migration.phase.changed', 'migration.cutover.ready', 'migration.validation.failed',
  'migration.completed']);
const SUPPORT_STATES = Object.freeze(['supported_native', 'supported_via_cdeadmin',
  'supported_via_external_adapter', 'read_only', 'partial', 'unsupported', 'unknown']);
const ADAPTER_METHODS = Object.freeze(['assessSource', 'proposeTypeMappings',
  'prepareSchemaOperation', 'prepareCopyUnit', 'verifyProviderState']);

function actor(user={}) { return String(user.id ?? user.username ?? user.email ?? 'unknown'); }
function providerId(reference) { return reference?.provider ?? reference?.providerId ?? null; }
function adapterRequired(adapter, id) {
  ADAPTER_METHODS.forEach((method) => {
    if(typeof adapter?.[method] !== 'function') throw new TypeError(
      `Migration adapter ${id} requires ${method}().`
    );
  });
}
function strings(input, field, id) {
  if(!Array.isArray(input[field])) throw new TypeError(`${id} migration result requires ${field}[].`);
  return [...new Set(input[field].map((item) => platformValue(item, `${id} ${field}`)))].sort();
}

export function validateMigrationProviderResult(input, id, operation, {allowEmpty=false}={}) {
  plainObject(input, `${id} ${operation} result`); noRawSecrets(input, `${id} ${operation} result`);
  const allowed = ['supportState', 'providerVersion', 'evidence', 'warnings', 'nativeDetails',
    'readCapabilities', 'writeCapabilities', 'discoveryCapabilities', 'nativeMechanisms',
    'versionConstraints', 'limitations', 'runtimeEvidence', 'value'];
  const unknown = Object.keys(input).filter((field) => !allowed.includes(field));
  if(unknown.length) throw new TypeError(
    `${id} ${operation} result contains unsupported field ${unknown[0]}.`
  );
  const supportState = platformValue(input.supportState, 'Migration support state');
  if(!SUPPORT_STATES.includes(supportState)) throw new TypeError(
    `${id} returned invalid migration support state.`
  );
  if(!Array.isArray(input.warnings)) throw new TypeError(`${id} migration result requires warnings[].`);
  const result = immutable({supportState,
    providerVersion: platformValue(input.providerVersion, `${id} provider version`),
    evidence: immutable({...plainObject(input.evidence, `${id} migration evidence`)}),
    warnings: input.warnings.map(String), nativeDetails: immutable({...plainObject(
      input.nativeDetails, `${id} migration native details`)}),
    readCapabilities: strings(input, 'readCapabilities', id),
    writeCapabilities: strings(input, 'writeCapabilities', id),
    discoveryCapabilities: strings(input, 'discoveryCapabilities', id),
    nativeMechanisms: strings(input, 'nativeMechanisms', id),
    versionConstraints: strings(input, 'versionConstraints', id),
    limitations: strings(input, 'limitations', id),
    runtimeEvidence: input.runtimeEvidence == null ? null : immutable({...plainObject(
      input.runtimeEvidence, `${id} migration runtime evidence`)}), value: input.value});
  if(supportState.startsWith('supported') && !allowEmpty && input.value === undefined) throw new TypeError(
    `${id} returned empty success for ${operation}.`
  );
  return result;
}

export class MigrationAdapterRegistry {
  constructor() { this.adapters = new Map(); }
  register(id, adapter) {
    id = stablePlatformId(id, 'Migration provider ID'); adapterRequired(adapter, id);
    if(this.adapters.has(id)) throw new Error(`Migration adapter already registered: ${id}`);
    this.adapters.set(id, adapter); return () => this.adapters.delete(id);
  }
  get(id) { return id ? this.adapters.get(stablePlatformId(id, 'Migration provider ID')) ?? null : null; }
  list() { return [...this.adapters.keys()].sort(); }
}

function unknownProvider(id, operation) {
  return immutable({providerId: id ?? 'unknown', operation, supportState: 'unknown',
    providerVersion: null, evidence: {}, warnings: ['No Migration adapter response is registered.'],
    nativeDetails: {}, readCapabilities: [], writeCapabilities: [], discoveryCapabilities: [],
    nativeMechanisms: [], versionConstraints: [], limitations: [
      'No Migration adapter response is registered.'], runtimeEvidence: null});
}
function runtimeState(content) {
  return {schemaApplied: false, schemaValidated: false, initialCopyComplete: false,
    cdcState: content.cdcPlanRef ? 'unknown' : 'not_applicable', cdcLag: null,
    validationResults: [], validationWaiverRef: null, checkpoints: new Map(),
    completedCutoverSteps: [], completedRollbackSteps: []};
}
function view(session) {
  return immutable({schema: 'cdeadmin.migration-session.v1', id: session.id,
    content: session.content, state: session.state, dirty: session.dirty,
    surface: session.surface, selectedId: session.selectedId, planValidation: session.planValidation,
    providerStatuses: [...session.providerStatuses.values()],
    integrationBindings: [...session.integrationBindings.values()],
    runtime: immutable({...session.runtime,
      checkpoints: [...session.runtime.checkpoints.values()]}), runs: [...session.runs],
    tasks: [...session.taskSnapshots.values()],
    arm: session.arm, interchange: session.interchange,
    activeTaskId: session.activeTaskId, problems: [...session.problems],
    history: [...session.history], error: session.error, createdAt: session.createdAt,
    updatedAt: session.updatedAt});
}

export class MigrationService {
  constructor({tasks, relationships, search, projectAssets,
    adapters=new MigrationAdapterRegistry(), events=platformEventService,
    diagnostics=diagnosticsService, now=() => new Date().toISOString()}={}) {
    if(!tasks || !relationships || !search) throw new TypeError(
      'Migration service requires Task, Relationship and Search services.'
    );
    this.tasks = tasks; this.relationships = relationships; this.search = search;
    this.projectAssets = projectAssets; this.adapters = adapters; this.events = events;
    this.diagnostics = diagnostics; this.now = now; this.sessions = new Map();
    this.listeners = new Set(); this.sequence = 0;
    this.disposers = [
      tasks.register('migration.assessment', (request, context) => this._assessment(
        request.sessionId, {...request, ...context})),
      tasks.register('migration.schema.apply', (request, context) => this._schema(
        request.sessionId, {...request, ...context})),
      tasks.register('migration.data.copy', (request, context) => this._copy(
        request.sessionId, {...request, ...context})),
      tasks.register('migration.validation', (request, context) => this._verify(
        request.sessionId, {...request, ...context})),
      tasks.register('migration.cutover', (request, context) => this._cutover(
        request.sessionId, {...request, ...context})),
      tasks.register('migration.rollback', (request, context) => this._rollback(
        request.sessionId, {...request, ...context})),
      search.register({id: 'migration.search', priority: 39,
        types: ['migration.asset', 'migration.mapping', 'migration.resource', 'migration.run'],
        search: async (query, {context={}}={}) => {
          if(!context.permissions?.includes('migration.view')) return [];
          const needle = query.toLowerCase(); const results = [];
          for(const session of this.sessions.values()) {
            if(`${session.id} ${session.content.name} ${session.content.phase}`.toLowerCase().includes(needle)) {
              results.push({id: session.id, type: 'migration.asset',
                label: session.content.name || session.id, context: `Migration · ${session.content.phase}`});
            }
            session.content.mappingSet.forEach((mapping) => {
              if(`${mapping.id} ${mapping.mappingKind} ${mapping.category}`.toLowerCase().includes(needle)) {
                results.push({id: mapping.id, type: 'migration.mapping', label: mapping.id,
                  context: `${session.id} · ${mapping.category}`});
              }
            });
            [session.content.sourceBinding, session.content.targetBinding].filter(Boolean).forEach((ref) => {
              if(JSON.stringify(ref).toLowerCase().includes(needle)) results.push({
                id: migrationReferenceKey(ref), type: 'migration.resource',
                label: ref.canonical ?? ref.id, context: `${session.id} · live resource`, reference: ref});
            });
            session.runs.forEach((run) => {
              if(`${run.id} ${run.phase} ${run.state}`.toLowerCase().includes(needle)) results.push({
                id: run.id, type: 'migration.run', label: run.id,
                context: `${session.id} · ${run.phase} · ${run.state}`});
            });
          }
          return results;
        }}),
    ];
    if(typeof events?.subscribe === 'function') {
      ['schema_compare.apply.completed', 'schema_compare.plan.created',
        'schema_compare.mapping.changed', 'etl.run.completed', 'etl.pipeline.changed',
        'cdc.run.state_changed', 'cdc.run.failed', 'quality.run.completed',
        'lineage.scan.completed'].forEach((type) =>
        this.disposers.push(events.subscribe(type, (event) => this._integrationEvent(event))));
    }
    this.disposers.push(tasks.subscribe((task) => {
      for(const session of this.sessions.values()) if(session.taskIds.includes(task.id)) {
        session.taskSnapshots.set(task.id, task); this._emit(session);
      }
    }));
  }

  dispose() {
    for(const session of this.sessions.values()) this._clearRelationships(session);
    this.disposers.reverse().forEach((remove) => remove()); this.disposers = [];
  }
  subscribe(listener) { this.listeners.add(listener); return () => this.listeners.delete(listener); }
  _emit(session) { this.listeners.forEach((listener) => listener(view(session))); }
  _touch(session, {dirty=session.dirty}={}) {
    session.updatedAt = this.now(); session.dirty = dirty; this._emit(session); return view(session);
  }
  _session(id) {
    const session = this.sessions.get(String(id));
    if(!session) throw new Error(`Unknown Migration session: ${id}`); return session;
  }
  create(input={}) {
    plainObject(input, 'Migration session'); const id = input.id ?? `migration-${++this.sequence}`;
    if(this.sessions.has(id)) throw new Error(`Migration session already exists: ${id}`);
    const content = createMigrationContent(input.content ?? input); const validation = validateMigrationPlan(content);
    const time = this.now(); const session = {id, content,
      state: !content.sourceBinding && !content.targetBinding ? 'empty' : validation.valid ? 'ready' : 'validation_error',
      dirty: false, surface: 'migration_portfolio', selectedId: null, planValidation: validation,
      providerStatuses: new Map(), integrationBindings: new Map(),
      runtime: runtimeState(content),
      runs: [], taskIds: [], taskSnapshots: new Map(), arm: null, activeTaskId: null,
      problems: [...validation.errors], history: [],
      interchange: null, error: '', createdAt: time, updatedAt: time};
    this.sessions.set(id, session); this._mirrorRelationships(session); this._emit(session); return view(session);
  }
  get(id) { return view(this._session(id)); }
  list() { return [...this.sessions.values()].map(view); }
  select(id, {surface, selectedId=null}={}) {
    const session = this._session(id); if(surface) session.surface = surface;
    session.selectedId = selectedId; return this._touch(session);
  }
  replaceDefinition(id, input, context={}) {
    const session = this._session(id); session.content = createMigrationContent(input);
    session.runtime = runtimeState(session.content); session.runs = [];
    session.taskIds = []; session.taskSnapshots.clear();
    session.integrationBindings.clear(); session.interchange = null;
    session.planValidation = validateMigrationPlan(session.content);
    session.state = session.planValidation.valid ? 'ready' : 'validation_error';
    session.arm = null; session.problems = [...session.planValidation.errors];
    session.history.push(this._audit('migration.project.create', session, context));
    this._mirrorRelationships(session); return this._touch(session, {dirty: true});
  }
  acceptMapping(id, mappingId, decision, context={}) {
    const session = this._session(id); const found = session.content.mappingSet.find((item) => item.id === mappingId);
    if(!['assess', 'design'].includes(session.content.phase)) throw new Error(
      'Migration mappings can change only during assessment or design.'
    );
    if(!found) throw new Error(`Unknown migration mapping: ${mappingId}`);
    if(!['accepted', 'rejected', 'manual'].includes(decision)) throw new TypeError(
      'Migration mapping decision must be accepted, rejected or manual.'
    );
    session.content = createMigrationContent({...session.content, mappingSet: session.content.mappingSet.map(
      (item) => item.id === mappingId ? {...item, decision} : item)});
    this._resetExecution(session); this._revalidate(session); session.history.push(this._audit(
      'migration.mapping.accept', session, context, {mappingId, decision}));
    return this._touch(session, {dirty: true});
  }
  validatePlan(id, context={}) {
    const session = this._session(id); this._revalidate(session);
    session.history.push(this._audit('migration.plan.validate', session, context,
      {valid: session.planValidation.valid})); return this._touch(session);
  }
  assess(id, options={}, context={}) { return this._submit(id, 'migration.assessment',
    'Assess migration compatibility', options, context); }
  dryRun(id, options={}, context={}) { return this._submit(id, 'migration.schema.apply',
    'Dry-run migration schema plan', {...options, mode: 'dry_run'}, context); }
  startCopy(id, options={}, context={}) { return this._submit(id, 'migration.data.copy',
    'Copy migration data', options, context); }
  armCutover(id, input, context={}) { return this._submit(id, 'migration.cutover',
    'Arm migration cutover', {...input, action: 'arm'}, context); }
  executeCutover(id, options={}, context={}) { return this._submit(id, 'migration.cutover',
    'Execute migration cutover', {...options, action: 'execute'}, context); }
  executeRollback(id, options={}, context={}) { return this._submit(id, 'migration.rollback',
    'Execute migration rollback', options, context); }
  verify(id, options={}, context={}) { return this._submit(id, 'migration.validation',
    'Verify migration', options, context); }
  async save(id, request, context={}) {
    const session = this._session(id); if(!this.projectAssets) throw new Error(
      'Project Asset service is unavailable.'
    );
    const payload = migrationAssetRequest({...request, content: session.content});
    try {
      const saved = request.assetId ? await this.projectAssets.update(request.projectId,
        request.assetId, payload) : await this.projectAssets.create(request.projectId, payload);
      session.dirty = false; session.history.push(this._audit('migration.asset.save', session,
        context, {version: saved.version})); this._touch(session); return saved;
    } catch(error) {
      session.error = error.message; session.problems.push(`conflict: ${error.message}`);
      session.state = 'runtime_failure'; this._touch(session, {dirty: true}); throw error;
    }
  }
  exportDefinition(id, {profile='portable', provenance={}}={}) {
    const session = this._session(id);
    return exportMigrationDefinition(session.content, {profile,
      exportedAt: this.now(), provenance: {...(session.interchange?.provenance ?? {}),
        ...provenance, migrationSessionId: session.id}});
  }
  importDefinition(id, envelope, context={}) {
    const session = this._session(id); const imported = importMigrationDefinition(envelope,
      {importedAt: this.now()});
    session.content = imported.content; session.interchange = imported.interchange;
    session.runtime = runtimeState(session.content); session.runs = [];
    session.taskIds = []; session.taskSnapshots.clear(); session.integrationBindings.clear();
    session.arm = null; session.activeTaskId = null; this._revalidate(session);
    session.history.push(this._audit(
      'migration.asset.import', session, context, {schema: imported.interchange.schema,
        version: imported.interchange.version, profile: imported.interchange.profile}));
    this._mirrorRelationships(session); return this._touch(session, {dirty: true});
  }
  bindIntegration(id, {kind, externalSessionId, reference, artifactId=null}, context={}) {
    const session = this._session(id);
    if(!['schema_compare', 'etl', 'cdc', 'quality', 'lineage'].includes(kind)) throw new TypeError(
      'Migration integration kind is invalid.'
    );
    externalSessionId = platformValue(externalSessionId, 'Migration integration session ID');
    if(kind !== 'lineage') artifactId = platformValue(
      artifactId, 'Migration integration artifact or run ID'
    );
    const expected = this._integrationReferences(session, kind);
    const key = migrationReferenceKey(reference);
    if(!expected.some((item) => migrationReferenceKey(item) === key)) throw new Error(
      `Migration ${kind} integration reference is not present in the reviewed plan.`
    );
    const binding = immutable({kind, externalSessionId, reference, artifactId,
      boundAt: this.now(), boundBy: actor(context.currentUser)});
    session.integrationBindings.set(`${kind}:${externalSessionId}`, binding);
    session.history.push(this._audit('migration.integration.bind', session, context,
      {kind, externalSessionId, reference: key}));
    return this._touch(session);
  }
  recordIntegrationState(id, patch, context={}) {
    const session = this._session(id); plainObject(patch, 'Migration integration state');
    const allowed = ['schemaApplied', 'schemaValidated', 'initialCopyComplete', 'cdcState',
      'cdcLag', 'validationWaiverRef'];
    Object.keys(patch).forEach((key) => {
      if(!allowed.includes(key)) throw new TypeError(`Unknown migration integration state: ${key}`);
    });
    Object.assign(session.runtime, patch); session.arm = null;
    session.history.push(this._audit('migration.integration.state', session, context, patch));
    return this._touch(session);
  }

  _integrationReferences(session, kind) {
    if(kind === 'schema_compare') return [session.content.schemaPlan?.schemaCompareRef].filter(Boolean);
    if(kind === 'etl') return session.content.dataMovePlan.units.map((item) => item.transformRef)
      .filter(Boolean);
    if(kind === 'cdc') return [session.content.cdcPlanRef].filter(Boolean);
    if(kind === 'quality') return session.content.validationPlan.map((item) => item.qualityAssetRef)
      .filter(Boolean);
    const lineage = session.content.extensions?.lineageAssetRef;
    return lineage ? [lineage] : [];
  }
  _integrationEvent(event) {
    plainObject(event.payload ?? {}, 'Migration integration event');
    noRawSecrets(event.payload ?? {}, 'Migration integration event');
    const sourceSessionId = String(event.payload?.sessionId ?? '');
    if(!sourceSessionId) return;
    const kind = event.type.startsWith('schema_compare.') ? 'schema_compare' :
      event.type.startsWith('etl.') ? 'etl' : event.type.startsWith('cdc.') ? 'cdc' :
        event.type.startsWith('quality.') ? 'quality' : 'lineage';
    for(const session of this.sessions.values()) {
      const binding = session.integrationBindings.get(`${kind}:${sourceSessionId}`);
      if(!binding) continue;
      const eventArtifactId = kind === 'schema_compare' ? event.payload.planId :
        ['etl', 'cdc', 'quality'].includes(kind) ? event.payload.runId : null;
      const definitionChanged = ['schema_compare.plan.created',
        'schema_compare.mapping.changed', 'etl.pipeline.changed'].includes(event.type);
      if(!definitionChanged && kind !== 'lineage' && eventArtifactId !== binding.artifactId) continue;
      let preserveFailureState = false;
      if(event.type === 'schema_compare.apply.completed') {
        session.runtime.schemaApplied = true; session.runtime.schemaValidated = true;
      } else if(['schema_compare.plan.created', 'schema_compare.mapping.changed'].includes(event.type)) {
        session.runtime.schemaApplied = false; session.runtime.schemaValidated = false;
        session.runtime.initialCopyComplete = false; session.state = 'stale';
        preserveFailureState = true;
        session.problems.push('Linked Schema Comparison plan changed; reassess downstream phases.');
      } else if(event.type === 'etl.run.completed') {
        session.runtime.initialCopyComplete = event.payload.state === 'succeeded';
      } else if(event.type === 'etl.pipeline.changed') {
        session.runtime.initialCopyComplete = false; session.state = 'stale';
        preserveFailureState = true;
        session.problems.push('Linked ETL pipeline changed; data movement is stale.');
      } else if(event.type === 'cdc.run.state_changed') {
        session.runtime.cdcState = event.payload.state;
      } else if(event.type === 'cdc.run.failed') {
        session.runtime.cdcState = 'failed'; session.state = 'runtime_failure';
        preserveFailureState = true;
        session.problems.push('Linked CDC run failed.');
      } else if(event.type === 'quality.run.completed') {
        const state = event.payload.outcome === 'pass' ? 'passed' : 'failed';
        const boundKey = migrationReferenceKey(binding.reference);
        session.runtime.validationResults = session.content.validationPlan.map((definition) =>
          definition.qualityAssetRef && migrationReferenceKey(definition.qualityAssetRef) === boundKey ?
            immutable({id: definition.id, state, sourceValue: null, targetValue: null,
              evidence: {eventType: event.type, runId: event.payload.runId,
                summary: event.payload.summary}}) : session.runtime.validationResults.find(
              (item) => item.id === definition.id)).filter(Boolean);
      }
      const evidence = immutable({integration: kind, eventType: event.type,
        sourceSessionId, payload: event.payload, reference: binding.reference});
      session.runs.push(this._run(`integration:${kind}`, 'succeeded', evidence));
      session.history.push(this._audit('migration.integration.event', session,
        {currentUser: {id: `service:${event.origin || kind}`}}, evidence));
      session.arm = null;
      if(!preserveFailureState) this._revalidate(session);
      this._touch(session);
    }
  }

  _resetExecution(session) {
    session.runtime = runtimeState(session.content); session.arm = null;
  }
  _phase(session, next) {
    if(!canTransition(session.content.phase, next)) throw new Error(
      `Migration phase cannot transition from ${session.content.phase} to ${next}.`
    );
    session.content = createMigrationContent({...session.content, phase: next});
    this.events.publish('migration.phase.changed', {sessionId: session.id, phase: next},
      {origin: MIGRATION_MODULE_ID});
  }

  _submit(id, type, label, request, context) {
    const session = this._session(id); if(session.state === 'read_only') throw new Error(
      'Migration is read only.'
    );
    const task = this.tasks.submit({type, sessionId: id, label, retry: request.retry,
      cancelable: true, resumable: type === 'migration.data.copy',
      resourceRefs: [session.content.sourceBinding, session.content.targetBinding].filter(Boolean),
      audit: this._audit(type, session, context), ...request}, {owner: actor(context.currentUser)});
    session.taskIds.push(task.id); session.taskSnapshots.set(task.id, task);
    session.activeTaskId = task.id; session.state = 'background_task_active'; this._touch(session);
    this.tasks.wait(task.id).catch((error) => {
      session.activeTaskId = null; session.state = error.name === 'AbortError' ? 'ready' : 'runtime_failure';
      session.error = error.message; session.problems.push(error.message); this._touch(session);
    }); return task;
  }
  async _provider(reference, operation, input, {allowEmpty=false, optionalMethod=false}={}) {
    const id = providerId(reference); const adapter = this.adapters.get(id);
    if(!adapter) return unknownProvider(id, operation);
    if(typeof adapter[operation] !== 'function') {
      if(optionalMethod) return unknownProvider(id, operation);
      throw new Error(`Migration adapter ${id} does not implement ${operation}.`);
    }
    return validateMigrationProviderResult(await adapter[operation](input), id, operation, {allowEmpty});
  }
  _status(session, id, operation, result) {
    session.providerStatuses.set(`${id}:${operation}`, immutable({providerId: id, operation, ...result}));
    if(['unknown', 'unsupported', 'partial', 'read_only'].includes(result.supportState)) {
      session.state = result.supportState === 'read_only' ? 'read_only' : result.supportState;
    }
  }
  async _assessment(id, context) {
    const session = this._session(id); const source = session.content.sourceBinding;
    if(!canTransition(session.content.phase, 'assess')) throw new Error(
      `Migration assessment cannot run during ${session.content.phase}.`
    );
    const sourceId = providerId(source); const targetId = providerId(session.content.targetBinding);
    context.phase('discover', 'Discovering source and target compatibility.'); context.progress(0.1);
    const result = await this._provider(source, 'assessSource', {scope: source,
      targetProvider: targetId, target: session.content.targetBinding, signal: context.signal});
    this._status(session, sourceId, 'assessSource', result);
    if(!result.supportState.startsWith('supported')) throw new Error(
      `Assessment is ${result.supportState}: ${result.limitations.join(' ')}`
    );
    const assessment = validateAssessment(result.value); context.progress(0.6);
    const sourceTypes = [...new Set(assessment.findings.map((item) => item.nativeDetails?.sourceType)
      .filter(Boolean))].sort(); const mappingEvidence = []; const proposedMappings = [];
    for(const sourceType of sourceTypes) {
      if(context.signal?.aborted) throw abortError();
      const mapping = await this._provider(session.content.targetBinding, 'proposeTypeMappings',
        {sourceType, sourceProvider: sourceId, targetProvider: targetId, signal: context.signal});
      this._status(session, targetId, `proposeTypeMappings:${sourceType}`, mapping);
      if(mapping.supportState.startsWith('supported')) {
        if(!Array.isArray(mapping.value)) throw new TypeError(
          `Migration adapter ${targetId} type-mapping proposal must be an array.`
        );
        mapping.value.forEach((item) => {
          const proposal = validateMigrationMapping(item);
          if(proposal.decision !== 'unresolved') throw new TypeError(
            `Migration adapter ${targetId} cannot auto-accept mapping ${proposal.id}.`
          );
          proposedMappings.push(proposal);
        });
      }
      mappingEvidence.push({sourceType, result: mapping});
    }
    const reviewed = new Map(session.content.mappingSet.map((item) => [item.id, item]));
    proposedMappings.forEach((item) => { if(!reviewed.has(item.id)) reviewed.set(item.id, item); });
    session.content = createMigrationContent({...session.content, assessment,
      mappingSet: [...reviewed.values()]});
    this._resetExecution(session); this._phase(session, 'assess');
    session.planValidation = validateMigrationPlan(session.content); session.dirty = true;
    session.runs.push(this._run('assess', 'succeeded', {assessmentId: assessment.id, mappingEvidence}));
    this.events.publish('migration.assessment.completed', {sessionId: id,
      assessmentId: assessment.id, findingCount: assessment.findings.length}, {origin: MIGRATION_MODULE_ID});
    context.progress(1); this._finish(session);
    return immutable({assessment, mappingEvidence});
  }
  async _schema(id, context) {
    const session = this._session(id); if(context.mode !== 'dry_run') throw new Error(
      'Schema application must be orchestrated by the Schema Comparison command authority.'
    );
    if(!canTransition(session.content.phase, 'dry_run')) throw new Error(
      `Migration dry run cannot execute during ${session.content.phase}.`
    );
    const validation = validateMigrationPlan(session.content);
    if(!validation.valid) throw new Error(`Migration plan is invalid: ${validation.errors.join(' ')}`);
    const order = orderedSchemaOperations(session.content.schemaPlan); const prepared = [];
    for(let index = 0; index < order.order.length; index++) {
      if(context.signal?.aborted) throw abortError();
      const operation = session.content.schemaPlan.operations.find((item) => item.id === order.order[index]);
      const result = await this._provider(session.content.targetBinding, 'prepareSchemaOperation',
        {operation, dryRun: true, signal: context.signal});
      this._status(session, providerId(session.content.targetBinding),
        `prepareSchemaOperation:${operation.id}`, result);
      if(!result.supportState.startsWith('supported')) throw new Error(
        `Schema operation ${operation.id} is ${result.supportState}.`
      );
      prepared.push({operationId: operation.id, result}); context.progress((index + 1) /
        Math.max(1, order.order.length), `Prepared ${operation.id}`);
    }
    session.runtime.schemaValidated = true; this._phase(session, 'dry_run'); session.dirty = true;
    session.runs.push(this._run('dry_run', 'succeeded', {prepared}));
    this._finish(session); return immutable({prepared, mutated: false});
  }
  async _copy(id, context) {
    const session = this._session(id); const units = session.content.dataMovePlan.units;
    if(!units.length) throw new Error('Migration data move plan has no copy units.');
    if(session.content.strategy !== 'data_only' && session.content.schemaPlan?.operations.length &&
        (session.runtime.schemaApplied !== true || session.runtime.schemaValidated !== true)) {
      throw new Error('Migration schema must be applied and validated before data copy.');
    }
    if(!canTransition(session.content.phase, 'initial_copy')) throw new Error(
      `Migration data copy cannot run during ${session.content.phase}.`
    );
    const target = session.content.targetBinding; const adapter = this.adapters.get(providerId(target));
    if(typeof adapter?.executeCopyUnit !== 'function') throw new Error(
      `Migration adapter ${providerId(target) ?? 'unknown'} cannot execute copy units.`
    );
    const results = new Array(units.length); let cursor = 0; let completed = 0; let failure = null;
    const copyUnit = async (unit, index) => {
      if(context.signal?.aborted) throw abortError(); await context.waitIfPaused();
      let previous = session.runtime.checkpoints.get(unit.id) ?? null;
      const prepared = await this._provider(target, 'prepareCopyUnit', {unit,
        source: session.content.sourceBinding, target, resumeCheckpoint: previous,
        recoverCommittedCheckpoint: true,
        signal: context.signal});
      this._status(session, providerId(target), `prepareCopyUnit:${unit.id}`, prepared);
      if(!prepared.supportState.startsWith('supported')) throw new Error(
        `Copy unit ${unit.id} is ${prepared.supportState}.`
      );
      if(!Object.hasOwn(prepared.value ?? {}, 'resumeCheckpoint') ||
          prepared.runtimeEvidence?.checkpointLookup?.durable !== true ||
          prepared.runtimeEvidence?.checkpointLookup?.authoritative !== true) throw new Error(
        `Copy unit ${unit.id} did not prove durable checkpoint recovery.`
      );
      if(prepared.value.resumeCheckpoint) {
        const recovered = validateCopyCheckpoint(prepared.value.resumeCheckpoint);
        if(recovered.state !== 'committed') throw new Error(
          `Copy unit ${unit.id} recovered a non-committed checkpoint.`
        );
        previous = applyCheckpoint(previous, recovered);
        session.runtime.checkpoints.set(unit.id, previous);
      }
      const executed = validateMigrationProviderResult(await adapter.executeCopyUnit({unit,
        prepared: prepared.value, resumeCheckpoint: previous, signal: context.signal,
        progress: context.progress}), providerId(target), 'executeCopyUnit');
      this._status(session, providerId(target), `executeCopyUnit:${unit.id}`, executed);
      if(!executed.supportState.startsWith('supported')) throw new Error(
        `Copy unit ${unit.id} execution is ${executed.supportState}.`
      );
      const persistence = executed.runtimeEvidence?.checkpointPersistence;
      if(persistence?.durable !== true || persistence.noDuplicateOrSkip !== true ||
          persistence.resumedFrom !== (previous?.id ?? null) ||
          !String(persistence.receipt ?? '').trim()) {
        throw new Error(`Copy unit ${unit.id} did not prove durable checkpoint persistence.`);
      }
      const checkpoint = applyCheckpoint(previous, validateCopyCheckpoint(executed.value.checkpoint));
      if(checkpoint.state !== 'committed') throw new Error(
        `Copy unit ${unit.id} did not produce a committed checkpoint.`
      );
      session.runtime.checkpoints.set(unit.id, checkpoint); results[index] = {unitId: unit.id,
        checkpoint, evidence: executed.evidence,
        metrics: executed.runtimeEvidence?.metrics ?? {rowDocumentCount: checkpoint.rowDocumentCount,
          byteCount: checkpoint.byteCount}}; completed++;
      context.progress(completed / units.length, `Committed ${unit.id}`); this._touch(session);
    };
    const worker = async () => {
      while(!failure) {
        const index = cursor++; if(index >= units.length) return;
        try { await copyUnit(units[index], index); } catch(error) { failure ??= error; }
      }
    };
    const concurrency = Math.min(session.content.dataMovePlan.concurrency, units.length);
    await Promise.all(Array.from({length: concurrency}, () => worker()));
    if(failure) throw failure;
    session.runtime.initialCopyComplete = true; this._phase(session, 'initial_copy'); session.dirty = true;
    session.runs.push(this._run('initial_copy', 'succeeded', {results}));
    this._finish(session); return immutable({results});
  }
  async _verify(id, context) {
    const session = this._session(id); const completing = session.content.phase === 'verify';
    if(!canTransition(session.content.phase, completing ? 'complete' : 'validate')) throw new Error(
      `Migration verification cannot run during ${session.content.phase}.`
    );
    const results = [];
    for(let index = 0; index < session.content.validationPlan.length; index++) {
      if(context.signal?.aborted) throw abortError(); const definition = session.content.validationPlan[index];
      const result = await this._provider(session.content.targetBinding, 'verifyProviderState',
        {phase: 'validate', definition, source: session.content.sourceBinding,
          target: session.content.targetBinding, signal: context.signal});
      this._status(session, providerId(session.content.targetBinding),
        `verifyProviderState:${definition.id}`, result);
      if(!result.supportState.startsWith('supported')) throw new Error(
        `Verification ${definition.id} is ${result.supportState}.`
      );
      results.push(normalizeVerificationResult(result.value, definition));
      context.progress((index + 1) / Math.max(1, session.content.validationPlan.length));
    }
    session.runtime.validationResults = results; const failed = results.filter((item) => item.state === 'failed');
    const nextPhase = completing && !failed.length ? 'complete' : completing ? 'verify' : 'validate';
    this._phase(session, nextPhase); session.dirty = true;
    session.runs.push(this._run(nextPhase, failed.length ? 'failed' : 'succeeded', {results}));
    if(failed.length) this.events.publish('migration.validation.failed', {sessionId: id,
      verificationIds: failed.map((item) => item.id)}, {origin: MIGRATION_MODULE_ID});
    if(nextPhase === 'complete') this.events.publish('migration.completed', {sessionId: id,
      verificationIds: results.map((item) => item.id)}, {origin: MIGRATION_MODULE_ID});
    const readiness = cutoverReadiness(session.content, session.runtime);
    if(!completing && readiness.ready) this.events.publish('migration.cutover.ready', {sessionId: id, ...readiness},
      {origin: MIGRATION_MODULE_ID}); this._finish(session); return immutable({results, readiness});
  }
  async _cutover(id, context) {
    const session = this._session(id);
    if(context.action === 'arm') {
      if(!canTransition(session.content.phase, 'cutover_ready')) throw new Error(
        `Migration cutover cannot be armed during ${session.content.phase}.`
      );
      session.arm = createCutoverArm(session.content, session.runtime, {actor: context.actor ??
        context.owner ?? 'unknown', confirmationRef: context.confirmationRef,
      environment: context.environment, connection: context.connection}, new Date(this.now()));
      this._phase(session, 'cutover_ready');
      session.dirty = true; session.runs.push(this._run('cutover_ready', 'succeeded', {arm: session.arm}));
      this.events.publish('migration.cutover.ready', {sessionId: id, expiresAt: session.arm.expiresAt},
        {origin: MIGRATION_MODULE_ID}); this._finish(session); return session.arm;
    }
    if(context.action !== 'execute') throw new Error('Migration cutover action is invalid.');
    const arm = validateCutoverArm(session.arm, session.content, new Date(this.now()));
    if(!arm.valid) throw new Error(arm.reason);
    if(!canTransition(session.content.phase, 'cutover')) throw new Error(
      `Migration cutover cannot execute during ${session.content.phase}.`
    );
    this._phase(session, 'cutover');
    const adapter = this.adapters.get(providerId(session.content.targetBinding));
    if(typeof adapter?.executeCutoverStep !== 'function') throw new Error(
      `Migration adapter ${providerId(session.content.targetBinding) ?? 'unknown'} cannot execute cutover.`
    );
    const sequence = orderedRunbookSteps(session.content.cutoverPlan.steps);
    if(sequence.cyclic) throw new Error(
      `Cutover runbook contains a dependency cycle: ${sequence.blocked.join(', ')}.`
    );
    const completed = [...session.runtime.completedCutoverSteps];
    for(const stepId of sequence.order) {
      if(completed.includes(stepId)) continue;
      const step = session.content.cutoverPlan.steps.find((item) => item.id === stepId);
      if(context.signal?.aborted) throw abortError();
      const result = validateMigrationProviderResult(await adapter.executeCutoverStep({step,
        arm: session.arm, signal: context.signal}), providerId(session.content.targetBinding),
      'executeCutoverStep');
      this._status(session, providerId(session.content.targetBinding),
        `executeCutoverStep:${step.id}`, result);
      if(!result.supportState.startsWith('supported')) throw new Error(
        `Cutover step ${step.id} is ${result.supportState}.`
      );
      completed.push(step.id); session.runtime.completedCutoverSteps = [...completed];
      context.progress(completed.length / session.content.cutoverPlan.steps.length, `Completed ${step.id}`);
    }
    const verified = await this._provider(session.content.targetBinding, 'verifyProviderState',
      {phase: 'cutover', expected: session.content.cutoverPlan.postChecks, signal: context.signal});
    this._status(session, providerId(session.content.targetBinding),
      'verifyProviderState:cutover', verified);
    if(!verified.supportState.startsWith('supported') || verified.value?.state !== 'passed') throw new Error(
      'Post-cutover provider verification did not pass.'
    );
    session.arm = null; this._phase(session, 'verify');
    session.dirty = true; session.runs.push(this._run('cutover', 'succeeded', {completed, verified}));
    this._finish(session); return immutable({completed, verified});
  }
  async _rollback(id, context) {
    const session = this._session(id); const status = rollbackState(session.content.rollbackPlan,
      session.runtime.completedCutoverSteps, new Date(this.now()));
    if(!status.available) throw new Error(status.message);
    if(!canTransition(session.content.phase, 'rollback')) throw new Error(
      `Migration rollback cannot run during ${session.content.phase}.`
    );
    const adapter = this.adapters.get(providerId(session.content.targetBinding));
    if(typeof adapter?.executeRollbackStep !== 'function') throw new Error(
      `Migration adapter ${providerId(session.content.targetBinding) ?? 'unknown'} cannot execute rollback.`
    );
    const sequence = orderedRunbookSteps(session.content.rollbackPlan.steps);
    if(sequence.cyclic) throw new Error(
      `Rollback runbook contains a dependency cycle: ${sequence.blocked.join(', ')}.`
    );
    const completed = [...session.runtime.completedRollbackSteps];
    for(const stepId of sequence.order) {
      if(completed.includes(stepId)) continue;
      const step = session.content.rollbackPlan.steps.find((item) => item.id === stepId);
      if(context.signal?.aborted) throw abortError();
      const result = validateMigrationProviderResult(await adapter.executeRollbackStep({step,
        signal: context.signal}), providerId(session.content.targetBinding), 'executeRollbackStep');
      this._status(session, providerId(session.content.targetBinding),
        `executeRollbackStep:${step.id}`, result);
      if(!result.supportState.startsWith('supported')) throw new Error(
        `Rollback step ${step.id} is ${result.supportState}.`
      );
      completed.push(step.id); session.runtime.completedRollbackSteps = [...completed];
      context.progress(completed.length / session.content.rollbackPlan.steps.length);
    }
    session.arm = null; this._phase(session, 'rollback');
    session.dirty = true; session.runs.push(this._run('rollback', 'succeeded', {completed}));
    this._finish(session); return immutable({completed});
  }
  _finish(session) {
    session.activeTaskId = null; session.error = ''; this._revalidate(session); this._touch(session);
  }
  _revalidate(session) {
    session.planValidation = validateMigrationPlan(session.content);
    session.problems = [...session.planValidation.errors];
    session.state = session.planValidation.valid ? 'ready' : 'validation_error';
  }
  _run(phase, state, evidence) {
    return immutable({schema: 'cdeadmin.migration-run.v1', id: `run-${++this.sequence}`,
      phase, state, at: this.now(), evidence});
  }
  _audit(action, session, context={}, details={}) {
    return immutable({at: this.now(), actor: actor(context.currentUser), action,
      target: session.id, phase: session.content.phase,
      environment: context.environment ?? null, connection: context.connection ?? null,
      confirmationRef: context.confirmationRef ?? null, details});
  }
  _clearRelationships(session) {
    [...this.relationships.edges.values()].filter((edge) => edge.origin === MIGRATION_MODULE_ID &&
      edge.metadata?.sessionId === session.id).forEach((edge) => this.relationships.removeEdge(edge.id));
    [...this.relationships.nodes.values()].filter((node) => node.metadata?.migrationSessionId === session.id)
      .forEach((node) => this.relationships.removeNode(node.id));
  }
  _mirrorRelationships(session) {
    this._clearRelationships(session); const assetNode = `migration:${session.id}`;
    this.relationships.upsertNode({id: assetNode, kind: 'migration.asset',
      label: session.content.name || session.id, metadata: {migrationSessionId: session.id}});
    const refs = [['source', session.content.sourceBinding], ['target', session.content.targetBinding],
      ['schema_plan', session.content.schemaPlan?.schemaCompareRef], ['cdc_plan', session.content.cdcPlanRef],
      ...session.content.validationPlan.map((item) => [`validation:${item.id}`, item.qualityAssetRef])]
      .filter(([, reference]) => reference);
    refs.forEach(([relation, reference]) => {
      const key = migrationReferenceKey(reference); const node = `migration-ref:${key}`;
      this.relationships.upsertNode({id: node, kind: reference.schema.replace('cdeadmin.', '').replace('.v1', ''),
        reference, label: reference.canonical ?? reference.id ?? reference.assetId,
        metadata: {migrationSessionId: session.id}});
      this.relationships.upsertEdge({id: `${assetNode}:${relation}:${key}`, from: assetNode, to: node,
        relation, origin: MIGRATION_MODULE_ID, evidenceRefs: [], metadata: {sessionId: session.id}});
    });
  }
}
