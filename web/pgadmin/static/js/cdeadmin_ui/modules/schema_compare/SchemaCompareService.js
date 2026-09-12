/////////////////////////////////////////////////////////////
// Schema Comparison orchestration, persistence and provider boundary.
/////////////////////////////////////////////////////////////

import {
  diagnosticsService, platformEventService, stablePlatformId,
} from '../../platform/PlatformRegistry';
import {immutable, noRawSecrets, plainObject, platformValue} from '../../platform/serviceUtils';
import {
  createSchemaCompareContent, exportSchemaComparison, referenceKey,
  schemaCompareAssetRequest, validateMapping, validateReference,
  validateSchemaSnapshot,
} from './contracts';
import {compareSnapshots, generateChangePlan} from './SchemaCompareEngine';

export const SCHEMA_COMPARE_SERVICE_ID = 'schema_compare.runtime';

export const SCHEMA_COMPARE_TASKS = Object.freeze([
  'schema_compare.scan', 'schema_compare.diff',
  'schema_compare.plan.validation', 'schema_compare.plan.apply',
]);

export const SCHEMA_COMPARE_EVENTS = Object.freeze([
  'schema_compare.completed', 'schema_compare.mapping.changed',
  'schema_compare.plan.created', 'schema_compare.apply.completed',
]);

function adapterResult(input, providerId) {
  plainObject(input, `Schema Comparison adapter response from ${providerId}`);
  noRawSecrets(input, `Schema Comparison adapter response from ${providerId}`);
  const supportState = String(input.supportState ?? 'unknown');
  const validStates = [
    'supported_native', 'supported_via_cdeadmin',
    'supported_via_external_adapter', 'read_only', 'partial', 'unsupported',
    'unknown',
  ];
  if(!validStates.includes(supportState)) {
    throw new TypeError(`${providerId} returned an invalid support state.`);
  }
  if(!Array.isArray(input.warnings)) throw new TypeError(
    `${providerId} adapter response requires warnings[].`
  );
  return immutable({
    supportState, providerVersion: platformValue(
      input.providerVersion, `${providerId} provider version`
    ),
    evidence: plainObject(input.evidence, `${providerId} evidence`),
    warnings: [...input.warnings],
    nativeDetails: plainObject(input.nativeDetails, `${providerId} native details`),
    value: input.value,
  });
}

function requireMethods(adapter, providerId) {
  [
    'captureSchemaSnapshot', 'normalizeForCompare',
    'compareNativeProperties', 'renderChangeOperation', 'validateChangePlan',
  ].forEach((method) => {
    if(typeof adapter?.[method] !== 'function') {
      throw new TypeError(`Schema Comparison adapter ${providerId} requires ${method}().`);
    }
  });
}

export class SchemaCompareAdapterRegistry {
  constructor() { this.adapters = new Map(); this.assetResolvers = new Map(); }

  register(providerId, adapter) {
    providerId = stablePlatformId(providerId, 'Schema Comparison provider ID');
    requireMethods(adapter, providerId);
    if(this.adapters.has(providerId)) throw new Error(
      `Schema Comparison adapter already registered: ${providerId}`
    );
    this.adapters.set(providerId, adapter);
    return () => this.adapters.delete(providerId);
  }

  get(providerId) {
    providerId = stablePlatformId(providerId, 'Schema Comparison provider ID');
    const adapter = this.adapters.get(providerId);
    if(!adapter) throw new Error(
      `capability_missing: ${providerId} has no explicit Schema Comparison adapter.`
    );
    return adapter;
  }

  registerAssetResolver(assetType, resolver) {
    assetType = stablePlatformId(assetType, 'Schema Comparison asset type');
    if(typeof resolver !== 'function') throw new TypeError('Asset resolver is required.');
    if(this.assetResolvers.has(assetType)) throw new Error(
      `Schema Comparison asset resolver already registered: ${assetType}`
    );
    this.assetResolvers.set(assetType, resolver);
    return () => this.assetResolvers.delete(assetType);
  }

  assetResolver(assetType) { return this.assetResolvers.get(assetType) ?? null; }
  list() { return [...this.adapters.keys()].sort(); }
}

function sessionView(session) {
  return immutable({
    schema: 'cdeadmin.schema-compare.session.v1', id: session.id,
    content: session.content, state: session.state, dirty: session.dirty,
    selectedDiffId: session.selectedDiffId, result: session.result,
    plan: session.plan, validation: session.validation,
    leftSnapshot: session.leftSnapshot, rightSnapshot: session.rightSnapshot,
    activeTaskId: session.activeTaskId, error: session.error,
    createdAt: session.createdAt, updatedAt: session.updatedAt,
  });
}

export class SchemaCompareService {
  constructor({adapters=new SchemaCompareAdapterRegistry(), tasks, relationships,
    search, projectAssets, events=platformEventService,
    diagnostics=diagnosticsService, now=() => new Date().toISOString()}={}) {
    if(!tasks || !relationships || !search || !projectAssets) throw new TypeError(
      'Schema Comparison requires task, relationship, search and project asset services.'
    );
    this.adapters = adapters; this.tasks = tasks; this.relationships = relationships;
    this.search = search; this.projectAssets = projectAssets; this.events = events;
    this.diagnostics = diagnostics; this.now = now; this.sessions = new Map();
    this.snapshots = new Map(); this.listeners = new Set(); this.sequence = 0;
    this.disposers = [];
    this._registerTaskRunners(); this._registerSearch();
  }

  _registerTaskRunners() {
    const runner = (method) => async (request, taskContext) =>
      this[method](request.sessionId, {...request, ...taskContext});
    this.disposers.push(
      this.tasks.register('schema_compare.scan', runner('_scan')),
      this.tasks.register('schema_compare.diff', runner('_runComparison')),
      this.tasks.register('schema_compare.plan.validation', runner('_validatePlan')),
      this.tasks.register('schema_compare.plan.apply', runner('_applyPlan')),
    );
  }

  _registerSearch() {
    this.disposers.push(this.search.register({
      id: 'schema_compare.search',
      types: ['schema_compare.session', 'schema_compare.diff'], priority: 40,
      search: async (query) => {
        const needle = query.toLowerCase(); const results = [];
        for(const session of this.sessions.values()) {
          if(session.id.toLowerCase().includes(needle)) results.push({
            id: session.id, type: 'schema_compare.session', label: session.id,
            reference: {sessionId: session.id},
          });
          for(const diff of session.result?.differences ?? []) {
            if(diff.qualifiedName.toLowerCase().includes(needle)) results.push({
              id: diff.id, type: 'schema_compare.diff', label: diff.qualifiedName,
              description: diff.classification,
              reference: {sessionId: session.id, diffId: diff.id},
            });
          }
        }
        return results;
      },
    }));
  }

  registerSnapshot(snapshot) {
    snapshot = validateSchemaSnapshot(snapshot);
    this.snapshots.set(snapshot.snapshotId, snapshot); return snapshot;
  }

  create(input={}) {
    noRawSecrets(input, 'Schema Comparison session');
    const id = input.id ? platformValue(input.id, 'Schema Comparison session ID') :
      `schema-compare-${++this.sequence}`;
    if(this.sessions.has(id)) throw new Error(`Schema Comparison session exists: ${id}`);
    const content = createSchemaCompareContent(input.content ?? input);
    const at = this.now();
    const session = {id, content, state: content.leftRef && content.rightRef ? 'ready' :
      'empty', dirty: false, selectedDiffId: null, result: null, plan: content.changePlan,
    validation: null, activeTaskId: null, error: '', createdAt: at, updatedAt: at,
    leftSnapshot: null, rightSnapshot: null};
    this.sessions.set(id, session); this._publish(session); return sessionView(session);
  }

  get(id) {
    const session = this.sessions.get(String(id));
    if(!session) throw new Error(`Unknown Schema Comparison session: ${id}`);
    return sessionView(session);
  }

  list() { return [...this.sessions.values()].map(sessionView); }
  subscribe(listener) { this.listeners.add(listener); return () => this.listeners.delete(listener); }
  _publish(session) { this.listeners.forEach((listener) => listener(sessionView(session))); }

  _update(id, changes, {dirty=true}={}) {
    const session = this.sessions.get(String(id));
    if(!session) throw new Error(`Unknown Schema Comparison session: ${id}`);
    Object.assign(session, changes, {dirty: dirty ? true : session.dirty,
      updatedAt: this.now()});
    this._publish(session); return sessionView(session);
  }

  configure(id, changes={}) {
    noRawSecrets(changes, 'Schema Comparison configuration');
    const current = this.get(id);
    const content = createSchemaCompareContent({...current.content, ...changes,
      comparisonResultRef: null, changePlan: null});
    return this._update(id, {content, state: content.leftRef && content.rightRef ?
      'ready' : 'empty', result: null, plan: null, validation: null, error: ''});
  }

  selectDiff(id, diffId) {
    const session = this.sessions.get(String(id));
    if(!session?.result?.differences.some((item) => item.id === diffId)) {
      throw new Error(`Unknown Schema Comparison difference: ${diffId}`);
    }
    return this._update(id, {selectedDiffId: diffId}, {dirty: false});
  }

  async _resolveSource(reference, context) {
    reference = validateReference(reference, 'Schema Comparison source');
    if(reference.schema === 'cdeadmin.schema-compare.snapshot-ref.v1') {
      const snapshot = this.snapshots.get(reference.snapshotId);
      if(!snapshot || (reference.revision && snapshot.revision !== reference.revision)) {
        throw new Error(`stale_result: snapshot is unavailable: ${reference.snapshotId}`);
      }
      return snapshot;
    }
    if(reference.schemaVersion === 1) {
      const asset = await this.projectAssets.asset(reference.projectId, reference.assetId,
        reference.assetVersion || null);
      const resolver = this.adapters.assetResolver(reference.assetType);
      if(!resolver) throw new Error(
        `capability_missing: no snapshot resolver for ${reference.assetType}.`
      );
      return validateSchemaSnapshot(await resolver(asset, context));
    }
    const adapter = this.adapters.get(reference.provider);
    const captured = adapterResult(await adapter.captureSchemaSnapshot(reference, context),
      reference.provider);
    if(['unsupported', 'unknown'].includes(captured.supportState)) throw new Error(
      `capability_missing: ${reference.provider} cannot capture this schema scope.`
    );
    const normalized = adapterResult(await adapter.normalizeForCompare(
      captured.value, {...context, reference}
    ), reference.provider);
    const source = normalized.value ?? captured.value;
    return validateSchemaSnapshot({...source, sourceRef: reference,
      providerId: reference.provider, providerVersion: normalized.providerVersion ||
        captured.providerVersion, supportState: normalized.supportState,
      evidence: {...captured.evidence, ...normalized.evidence},
      warnings: [...captured.warnings, ...normalized.warnings],
      nativeDetails: {...captured.nativeDetails, ...normalized.nativeDetails}});
  }

  _task(id, type, label, request={}) {
    const session = this.sessions.get(String(id));
    if(!session) throw new Error(`Unknown Schema Comparison session: ${id}`);
    const task = this.tasks.submit({type, label, sessionId: id, ...request}, {
      owner: {moduleId: 'cdeadmin.schema_compare', sessionId: id},
    });
    this._update(id, {activeTaskId: task.id, state: 'background_task_active'},
      {dirty: false});
    return task;
  }

  async scan(id) {
    const task = this._task(id, 'schema_compare.scan', 'Capture schema snapshots');
    return this._wait(id, task);
  }

  async _scan(id, context={}) {
    const session = this.sessions.get(String(id));
    if(!session.content.leftRef || !session.content.rightRef) {
      throw new Error('configuration_invalid: left and right sources are required.');
    }
    context.progress?.(0.05, 'Capturing left schema');
    const leftSnapshot = await this._resolveSource(session.content.leftRef, context);
    context.progress?.(0.5, 'Capturing right schema');
    const rightSnapshot = await this._resolveSource(session.content.rightRef, context);
    this.registerSnapshot(leftSnapshot); this.registerSnapshot(rightSnapshot);
    this._update(id, {leftSnapshot, rightSnapshot, activeTaskId: null, state: 'ready',
      error: ''}, {dirty: false});
    context.progress?.(1, 'Schema snapshots captured');
    return immutable({leftSnapshot, rightSnapshot});
  }

  async run(id) {
    const session = this.sessions.get(String(id));
    if(!session) throw new Error(`Unknown Schema Comparison session: ${id}`);
    if(!session.leftSnapshot || !session.rightSnapshot) await this.scan(id);
    const task = this._task(id, 'schema_compare.diff', 'Compare schema snapshots');
    return this._wait(id, task);
  }

  async _runComparison(id, context={}) {
    const session = this.sessions.get(String(id));
    const leftAdapter = session.leftSnapshot.sourceRef.provider ?
      this.adapters.get(session.leftSnapshot.sourceRef.provider) : null;
    const rightAdapter = session.rightSnapshot.sourceRef.provider ?
      this.adapters.get(session.rightSnapshot.sourceRef.provider) : null;
    const compareNativeProperties = async (left, right, compareContext) => {
      const outputs = [];
      if(leftAdapter) outputs.push(adapterResult(await leftAdapter.compareNativeProperties(
        left, right, compareContext
      ), session.leftSnapshot.providerId));
      if(rightAdapter && rightAdapter !== leftAdapter) outputs.push(adapterResult(
        await rightAdapter.compareNativeProperties(right, left, {
          ...compareContext, perspective: 'right',
        }), session.rightSnapshot.providerId));
      const incomparable = outputs.find((item) => item.value?.comparable === false);
      return incomparable?.value ?? {
        comparable: true, nativeDetails: Object.fromEntries(outputs.map((item, index) => [
          index ? 'right' : 'left', item.nativeDetails,
        ])), warnings: outputs.flatMap((item) => item.warnings),
      };
    };
    context.progress?.(0.1, 'Matching schema identities');
    const result = await compareSnapshots(session.leftSnapshot, session.rightSnapshot, {
      acceptedMappings: session.content.acceptedMappings,
      compareNativeProperties, signal: context.signal,
    });
    this._recordRelationships(session, result);
    this._update(id, {result, plan: null, validation: null, activeTaskId: null,
      state: result.supportState === 'partial' ? 'partial' : 'ready', error: ''});
    await this.events.publish('schema_compare.completed', {
      sessionId: id, resultSchema: result.schema, counts: result.counts,
    }, {origin: 'cdeadmin.schema_compare'});
    context.progress?.(1, 'Comparison complete'); return result;
  }

  _recordRelationships(session, result) {
    const sessionNode = `schema-compare-session:${session.id}`;
    this.relationships.upsertNode({id: sessionNode, kind: 'schema_compare.session',
      label: session.id, reference: {sessionId: session.id}});
    [session.content.leftRef, session.content.rightRef].forEach((reference, index) => {
      const id = `schema-compare-source:${encodeURIComponent(referenceKey(reference))}`;
      this.relationships.upsertNode({id, kind: 'schema_compare.source', reference,
        label: index ? 'Right source' : 'Left source'});
      this.relationships.upsertEdge({id: `${sessionNode}:${index ? 'right' : 'left'}`,
        from: sessionNode, to: id, relation: index ? 'compares_right' : 'compares_left',
        origin: 'generated', evidenceRefs: [],
        metadata: {resultSchema: result.schema}});
    });
  }

  async setMapping(id, input) {
    const mapping = validateMapping(input); const session = this.get(id);
    const mappings = session.content.acceptedMappings
      .filter((item) => item.mappingId !== mapping.mappingId);
    if(mapping.accepted) mappings.push(mapping);
    const content = createSchemaCompareContent({...session.content,
      acceptedMappings: mappings, comparisonResultRef: null, changePlan: null});
    const view = this._update(id, {content, result: null, plan: null,
      validation: null, state: 'stale'});
    await this.events.publish('schema_compare.mapping.changed', {
      sessionId: id, mappingId: mapping.mappingId, accepted: mapping.accepted,
    }, {origin: 'cdeadmin.schema_compare'});
    return view;
  }

  acceptRename(id, candidateId, category='representational_difference') {
    const session = this.sessions.get(String(id));
    const candidate = session?.result?.renameCandidates.find((item) => item.id === candidateId);
    if(!candidate) throw new Error(`Unknown rename candidate: ${candidateId}`);
    return this.setMapping(id, {mappingId: `mapping:${candidate.leftId}:${candidate.rightId}`,
      leftId: candidate.leftId, rightId: candidate.rightId, category,
      version: 1, confidence: null, evidence: candidate.evidence, accepted: true});
  }

  async generatePlan(id, {targetRef, selectedDiffIds=null}={}) {
    const session = this.sessions.get(String(id));
    if(!session?.result) throw new Error('stale_result: run comparison before planning.');
    targetRef = validateReference(targetRef, 'Change-plan target');
    if(targetRef.schema !== 'cdeadmin.resource-ref.v1') throw new Error(
      'capability_missing: change-plan target must be a live ResourceRef.'
    );
    const adapter = this.adapters.get(targetRef.provider);
    const plan = await generateChangePlan(session.result, session.leftSnapshot,
      session.rightSnapshot, {targetRef, selectedDiffIds,
        renderChangeOperation: async (operation, context) => {
          const response = adapterResult(await adapter.renderChangeOperation(
            operation, context
          ), targetRef.provider);
          return {...response.value, nativeDetails: response.nativeDetails,
            warnings: response.warnings};
        }});
    this._update(id, {plan, validation: null, state: 'ready'});
    await this.events.publish('schema_compare.plan.created', {
      sessionId: id, planId: plan.id, operationCount: plan.operations.length,
    }, {origin: 'cdeadmin.schema_compare'});
    return plan;
  }

  async validatePlan(id) {
    const task = this._task(id, 'schema_compare.plan.validation',
      'Validate Schema Comparison change plan');
    return this._wait(id, task);
  }

  async _validatePlan(id, context={}) {
    const session = this.sessions.get(String(id));
    if(!session?.plan) throw new Error('configuration_invalid: generate a plan first.');
    const adapter = this.adapters.get(session.plan.targetRef.provider);
    context.progress?.(0.1, 'Validating target and selected operations');
    const response = adapterResult(await adapter.validateChangePlan(session.plan, {
      signal: context.signal, partialSelection: session.plan.partialSelection,
    }), session.plan.targetRef.provider);
    plainObject(response.value, 'Change-plan validation result');
    const validation = immutable({
      schema: 'cdeadmin.schema-compare.plan-validation.v1',
      valid: response.value.valid === true,
      checkedAt: this.now(), supportState: response.supportState,
      evidence: response.evidence, warnings: [...response.warnings,
        ...(response.value.warnings ?? [])], errors: [...(response.value.errors ?? [])],
      targetRevision: String(response.value.targetRevision ?? ''),
      partialSelectionValidated: session.plan.partialSelection ?
        response.value.partialSelectionValidated === true : true,
      applySupported: response.value.applySupported === true,
      materializedOperationIds: [...(response.value.materializedOperationIds ?? [])],
    });
    const operationIds = new Set(validation.materializedOperationIds);
    const allMaterialized = session.plan.operations.every((item) => operationIds.has(item.id));
    const exactValidation = immutable({...validation,
      fullyMaterialized: allMaterialized,
      applyReady: validation.valid && validation.applySupported && allMaterialized,
      warnings: [...validation.warnings, ...(!validation.applySupported ?
        ['Target adapter did not declare apply support.'] : []),
      ...(!allMaterialized ?
        ['Target adapter did not materialize every selected operation.'] : [])],
    });
    this._update(id, {validation: exactValidation, activeTaskId: null,
      state: exactValidation.valid ? 'ready' : 'validation_error'}, {dirty: false});
    context.progress?.(1, 'Plan validation complete'); return exactValidation;
  }

  exportPlan(id, options={}) {
    const session = this.sessions.get(String(id));
    if(!session?.plan) throw new Error('configuration_invalid: generate a plan first.');
    const content = createSchemaCompareContent({...session.content,
      changePlan: session.plan});
    return exportSchemaComparison(content, {...options,
      provenance: {...options.provenance, sessionId: id,
        leftRevision: session.leftSnapshot?.revision,
        rightRevision: session.rightSnapshot?.revision},
      nativeScripts: session.plan.operations.map((operation) => ({
        providerId: session.plan.targetRef.provider,
        mediaType: 'text/plain', text: operation.nativeStatement,
      }))});
  }

  async applyPlan(id, {confirmation}={}) {
    const session = this.sessions.get(String(id));
    if(!session?.plan) throw new Error('configuration_invalid: generate a plan first.');
    if(!session.validation?.applyReady ||
        (session.plan.partialSelection && !session.validation.partialSelectionValidated)) {
      throw new Error('configuration_invalid: the exact change plan must pass validation.');
    }
    if(session.plan.destructive && confirmation !== session.plan.id) throw new Error(
      'configuration_invalid: destructive apply requires the exact plan ID as confirmation.'
    );
    const task = this._task(id, 'schema_compare.plan.apply',
      'Apply Schema Comparison change plan', {confirmation});
    return this._wait(id, task);
  }

  async _applyPlan(id, context={}) {
    const session = this.sessions.get(String(id));
    const adapter = this.adapters.get(session.plan.targetRef.provider);
    if(typeof adapter.applyChangePlan !== 'function') throw new Error(
      `capability_missing: ${session.plan.targetRef.provider} does not support plan apply.`
    );
    context.progress?.(0.05, 'Applying provider-native change plan');
    const response = adapterResult(await adapter.applyChangePlan(session.plan, {
      signal: context.signal, confirmation: context.confirmation,
      validation: session.validation, progress: context.progress,
    }), session.plan.targetRef.provider);
    plainObject(response.value, 'Change-plan apply result');
    const auditRef = platformValue(response.value.auditRef, 'Apply audit reference');
    const result = immutable({
      schema: 'cdeadmin.schema-compare.apply-result.v1', applied: true,
      auditRef, providerId: session.plan.targetRef.provider,
      operationResults: [...(response.value.operationResults ?? [])],
      warnings: [...response.warnings, ...(response.value.warnings ?? [])],
      completedAt: this.now(),
    });
    this._update(id, {activeTaskId: null, state: 'stale'}, {dirty: false});
    await this.events.publish('schema_compare.apply.completed', {
      sessionId: id, planId: session.plan.id, auditRef,
    }, {origin: 'cdeadmin.schema_compare'});
    context.progress?.(1, 'Change plan applied'); return result;
  }

  async save(id, assetRef, overrides={}) {
    const session = this.sessions.get(String(id));
    if(!session) throw new Error(`Unknown Schema Comparison session: ${id}`);
    const request = schemaCompareAssetRequest(assetRef, {
      ...session.content, changePlan: session.plan,
    }, overrides);
    try {
      const result = await this.projectAssets.saveAsset(
        assetRef.projectId, assetRef.assetId, request
      );
      this._update(id, {dirty: false}, {dirty: false}); return result;
    } catch(error) {
      if(error.code === 'asset_version_conflict') this._update(id, {
        state: 'stale', error: error.message,
      }, {dirty: false});
      throw error;
    }
  }

  reportError(id, error, code='internal_error') {
    const session = this.sessions.get(String(id));
    if(session) this._update(id, {state: 'runtime_failure', error: error.message},
      {dirty: false});
    return this.diagnostics.report({
      id: `schema-compare.${code}`, origin: 'cdeadmin.schema_compare',
      severity: 'error', code, message: error.message,
      suggestedCommandId: 'schema_compare.run',
    });
  }

  async _wait(id, task) {
    try { return await this.tasks.wait(task.id); }
    catch(error) {
      this._update(id, {activeTaskId: null, state: error.name === 'AbortError' ?
        'ready' : 'runtime_failure', error: error.message}, {dirty: false});
      this.reportError(id, error, error.name === 'AbortError' ?
        'task_cancelled' : 'task_failed');
      throw error;
    }
  }

  dispose() { this.disposers.reverse().forEach((dispose) => dispose()); }
}
