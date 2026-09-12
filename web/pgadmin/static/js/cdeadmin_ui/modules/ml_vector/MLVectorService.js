/////////////////////////////////////////////////////////////
// ML / Vector runtime, provider admission and task authority.
/////////////////////////////////////////////////////////////

import {immutable, noRawSecrets, plainObject, platformValue} from '../../platform/serviceUtils';
import {diagnosticsService, platformEventService, stablePlatformId} from '../../platform/PlatformRegistry';
import {
  createMLVectorContent, ML_VECTOR_MODULE_ID, ML_VECTOR_SERVICE_ID, mlVectorAssetRequest,
  mlVectorReferenceKey, validateEmbeddingPipeline, validateEvaluationCase, validateModelEntry,
  validateVectorIndex,
} from './contracts';
import {
  normalizeEvaluationResult, normalizeSearchRequest, validateIndexForCapabilities,
  validateMLVectorDefinition, validateVectorCapabilities,
} from './MLVectorEngine';

export {ML_VECTOR_SERVICE_ID};
export const ML_VECTOR_TASKS = Object.freeze(['vector.index.build', 'vector.search.large',
  'embedding.run', 'ml.evaluation.run', 'model.import']);
export const ML_VECTOR_EVENTS = Object.freeze(['vector.index.changed', 'embedding.completed',
  'model.version.registered', 'ml.evaluation.completed']);
const SUPPORT_STATES = Object.freeze(['supported_native', 'supported_via_cdeadmin',
  'supported_via_external_adapter', 'read_only', 'partial', 'unsupported', 'unknown']);
const REQUIRED_ADAPTER_METHODS = Object.freeze(['describeVectorCapabilities', 'validateIndexConfig',
  'prepareVectorSearch', 'executeVectorSearch', 'explainVectorSearch', 'buildVectorIndex',
  'resolveExternalModel', 'runEmbedding', 'runEvaluation', 'importModel']);

function actor(context={}) {
  const user = context.currentUser ?? {};
  return String(user.id ?? user.username ?? user.email ?? context.owner ?? 'unknown');
}
function providerId(reference) { return reference?.providerId ?? reference?.provider ?? null; }
function strings(input, field, id) {
  if(!Array.isArray(input[field])) throw new TypeError(`${id} ML / Vector result requires ${field}[].`);
  return [...new Set(input[field].map((item) => platformValue(item, `${id} ${field}`)))].sort();
}
export function validateMLVectorProviderResult(input, id, operation, {allowEmpty=false}={}) {
  plainObject(input, `${id} ${operation} result`); noRawSecrets(input, `${id} ${operation} result`);
  const allowed = ['supportState', 'providerVersion', 'evidence', 'warnings', 'nativeDetails',
    'readCapabilities', 'writeCapabilities', 'discoveryCapabilities', 'nativeMechanisms',
    'versionConstraints', 'limitations', 'runtimeEvidence', 'value'];
  const unknown = Object.keys(input).filter((field) => !allowed.includes(field));
  if(unknown.length) throw new TypeError(`${id} ${operation} result contains unsupported field ${unknown[0]}.`);
  const supportState = platformValue(input.supportState, 'ML / Vector support state');
  if(!SUPPORT_STATES.includes(supportState)) throw new TypeError('ML / Vector support state is invalid.');
  if(!Array.isArray(input.warnings)) throw new TypeError(`${id} ML / Vector result requires warnings[].`);
  const result = immutable({supportState,
    providerVersion: platformValue(input.providerVersion, `${id} provider version`),
    evidence: immutable({...plainObject(input.evidence, `${id} evidence`)}),
    warnings: input.warnings.map(String), nativeDetails: immutable({...plainObject(
      input.nativeDetails, `${id} native details`)}),
    readCapabilities: strings(input, 'readCapabilities', id),
    writeCapabilities: strings(input, 'writeCapabilities', id),
    discoveryCapabilities: strings(input, 'discoveryCapabilities', id),
    nativeMechanisms: strings(input, 'nativeMechanisms', id),
    versionConstraints: strings(input, 'versionConstraints', id),
    limitations: strings(input, 'limitations', id),
    runtimeEvidence: input.runtimeEvidence == null ? null : immutable({...plainObject(
      input.runtimeEvidence, `${id} runtime evidence`)}), value: input.value});
  if(supportState.startsWith('supported') && !allowEmpty && input.value === undefined) throw new TypeError(
    `${id} returned empty success for ${operation}.`);
  return result;
}

export class MLVectorAdapterRegistry {
  constructor() { this.adapters = new Map(); }
  register(id, adapter) {
    id = stablePlatformId(id, 'ML / Vector provider ID');
    REQUIRED_ADAPTER_METHODS.forEach((method) => {
      if(typeof adapter?.[method] !== 'function') throw new TypeError(
        `ML / Vector adapter ${id} requires ${method}().`);
    });
    if(this.adapters.has(id)) throw new Error(`ML / Vector adapter already registered: ${id}`);
    this.adapters.set(id, adapter); return () => this.adapters.delete(id);
  }
  get(id) { return id ? this.adapters.get(stablePlatformId(id, 'ML / Vector provider ID')) ?? null : null; }
  list() { return [...this.adapters.keys()].sort(); }
}

function unknownProvider(id, operation) {
  return immutable({providerId: id ?? 'unknown', operation, supportState: 'unknown', providerVersion: null,
    evidence: {}, warnings: ['No ML / Vector adapter response is registered.'], nativeDetails: {},
    readCapabilities: [], writeCapabilities: [], discoveryCapabilities: [], nativeMechanisms: [],
    versionConstraints: [], limitations: ['No ML / Vector adapter response is registered.'],
    runtimeEvidence: null});
}
function runtimeState() { return {capabilitySnapshots: new Map(), validationResults: [], searchResults: [],
  explainResults: [], embeddingRuns: [], evaluationResults: [], indexBuilds: [], modelImports: []}; }
function runtimeView(runtime) { return immutable({capabilitySnapshots: [...runtime.capabilitySnapshots.values()],
  validationResults: [...runtime.validationResults], searchResults: [...runtime.searchResults],
  explainResults: [...runtime.explainResults], embeddingRuns: [...runtime.embeddingRuns],
  evaluationResults: [...runtime.evaluationResults], indexBuilds: [...runtime.indexBuilds],
  modelImports: [...runtime.modelImports]}); }
function sessionView(session) {
  return immutable({schema: 'cdeadmin.ml-vector-session.v1', id: session.id, content: session.content,
    state: session.state, dirty: session.dirty, surface: session.surface, selectedId: session.selectedId,
    validation: session.validation, providerStatuses: [...session.providerStatuses.values()],
    runtime: runtimeView(session.runtime), tasks: [...session.taskSnapshots.values()],
    activeTaskId: session.activeTaskId, history: [...session.history], problems: [...session.problems],
    error: session.error, createdAt: session.createdAt, updatedAt: session.updatedAt});
}

export class MLVectorService {
  constructor({tasks, relationships, search, projectAssets, adapters=new MLVectorAdapterRegistry(),
    events=platformEventService, diagnostics=diagnosticsService, now=() => new Date().toISOString()}={}) {
    if(!tasks || !relationships || !search) throw new TypeError(
      'ML / Vector service requires Task, Relationship and Search services.');
    this.tasks = tasks; this.relationships = relationships; this.search = search;
    this.projectAssets = projectAssets; this.adapters = adapters; this.events = events;
    this.diagnostics = diagnostics; this.now = now; this.sessions = new Map();
    this.listeners = new Set(); this.sequence = 0;
    this.disposers = [
      tasks.register('vector.index.build', (request, context) => this._buildIndex(request.sessionId,
        {...request, ...context})),
      tasks.register('vector.search.large', (request, context) => this._search(request.sessionId,
        {...request, ...context})),
      tasks.register('embedding.run', (request, context) => this._embedding(request.sessionId,
        {...request, ...context})),
      tasks.register('ml.evaluation.run', (request, context) => this._evaluation(request.sessionId,
        {...request, ...context})),
      tasks.register('model.import', (request, context) => this._importModel(request.sessionId,
        {...request, ...context})),
      search.register({id: 'ml-vector.search', priority: 44,
        types: ['ml_vector.asset', 'ml_vector.design', 'ml_vector.index', 'ml_vector.pipeline',
          'ml_vector.model', 'ml_vector.experiment', 'ml_vector.deployment'],
        search: async (query, {context={}}={}) => {
          if(!context.permissions?.includes('ml_vector.view')) return [];
          const needle = query.toLowerCase(); const result = [];
          for(const session of this.sessions.values()) {
            if(`${session.id} ${session.content.name}`.toLowerCase().includes(needle)) result.push({
              id: session.id, type: 'ml_vector.asset', label: session.content.name || session.id,
              context: 'ML / Vector'});
            const groups = [['design', session.content.vectorDesigns],
              ['pipeline', session.content.embeddingPipelines], ['model', session.content.modelEntries],
              ['experiment', session.content.experiments], ['deployment', session.content.deploymentBindings]];
            groups.forEach(([type, values]) => values.forEach((item) => {
              if(`${item.id} ${item.name}`.toLowerCase().includes(needle)) result.push({id: item.id,
                type: `ml_vector.${type}`, label: item.name, context: session.id});
            }));
            session.content.vectorDesigns.forEach((design) => design.indexes.forEach((index) => {
              if(`${index.id} ${index.name}`.toLowerCase().includes(needle)) result.push({id: index.id,
                type: 'ml_vector.index', label: index.name, context: `${session.id} · ${design.name}`});
            }));
          }
          return result;
        }}),
      tasks.subscribe((task) => { for(const session of this.sessions.values()) if(session.taskIds.includes(task.id)) {
        session.taskSnapshots.set(task.id, task); this._emit(session);
      }}),
    ];
  }
  dispose() { for(const session of this.sessions.values()) this._clearRelationships(session);
    this.disposers.reverse().forEach((remove) => remove()); this.disposers = []; }
  subscribe(listener) { this.listeners.add(listener); return () => this.listeners.delete(listener); }
  _emit(session) { this.listeners.forEach((listener) => listener(sessionView(session))); }
  _touch(session, {dirty=session.dirty}={}) { session.updatedAt = this.now(); session.dirty = dirty;
    this._emit(session); return sessionView(session); }
  _session(id) { const session = this.sessions.get(String(id));
    if(!session) throw new Error(`Unknown ML / Vector session: ${id}`); return session; }
  create(input={}) {
    plainObject(input, 'ML / Vector session'); const id = input.id ?? `ml-vector-${++this.sequence}`;
    if(this.sessions.has(id)) throw new Error(`ML / Vector session already exists: ${id}`);
    const content = createMLVectorContent(input.content ?? input); const validation = validateMLVectorDefinition(content);
    const time = this.now(); const session = {id, content,
      state: !content.vectorDesigns.length && !content.modelEntries.length ? 'empty' :
        validation.valid ? 'ready' : 'validation_error', dirty: false, surface: 'vector_explorer',
      selectedId: null, validation, providerStatuses: new Map(), runtime: runtimeState(), taskIds: [],
      taskSnapshots: new Map(), activeTaskId: null, history: [], problems: [...validation.errors],
      error: '', createdAt: time, updatedAt: time};
    this.sessions.set(id, session); this._mirrorRelationships(session); this._emit(session);
    return sessionView(session);
  }
  get(id) { return sessionView(this._session(id)); }
  list() { return [...this.sessions.values()].map(sessionView); }
  select(id, {surface, selectedId=null}={}) { const session = this._session(id);
    if(surface) session.surface = surface; session.selectedId = selectedId; return this._touch(session); }
  replaceDefinition(id, input, context={}) { const session = this._session(id);
    session.content = createMLVectorContent(input); this._definitionChanged(session, 'ml_vector.asset.replace', context);
    return this._touch(session, {dirty: true}); }
  createIndexPlan(id, designId, input, context={}) {
    const session = this._session(id); const index = validateVectorIndex(input); let found = false;
    session.content = createMLVectorContent({...session.content,
      vectorDesigns: session.content.vectorDesigns.map((design) => {
        if(design.id !== designId) return design; found = true;
        if(design.indexes.some((item) => item.id === index.id)) throw new Error(
          `Vector index already exists: ${index.id}`);
        return {...design, indexes: [...design.indexes, index]};
      })});
    if(!found) throw new Error(`Unknown vector design: ${designId}`);
    this._definitionChanged(session, 'vector.index.create_plan', context, {designId, indexId: index.id});
    this.events.publish('vector.index.changed', {sessionId: id, designId, indexId: index.id,
      reason: 'plan_created'}, {origin: ML_VECTOR_MODULE_ID});
    return this._touch(session, {dirty: true});
  }
  createPipeline(id, input, context={}) { const session = this._session(id);
    const pipeline = validateEmbeddingPipeline(input);
    if(session.content.embeddingPipelines.some((item) => item.id === pipeline.id)) throw new Error(
      `Embedding pipeline already exists: ${pipeline.id}`);
    session.content = createMLVectorContent({...session.content,
      embeddingPipelines: [...session.content.embeddingPipelines, pipeline]});
    this._definitionChanged(session, 'embedding.pipeline.create', context, {pipelineId: pipeline.id});
    return this._touch(session, {dirty: true});
  }
  registerModel(id, input, context={}) { const session = this._session(id); const model = validateModelEntry(input);
    if(session.content.modelEntries.some((item) => item.id === model.id)) throw new Error(
      `Model already exists: ${model.id}`);
    session.content = createMLVectorContent({...session.content,
      modelEntries: [...session.content.modelEntries, model]});
    this._definitionChanged(session, 'model.register', context, {modelId: model.id});
    model.versions.forEach((version) => this.events.publish('model.version.registered',
      {sessionId: id, modelId: model.id, versionId: version.id, contentDigest: version.contentDigest},
      {origin: ML_VECTOR_MODULE_ID})); return this._touch(session, {dirty: true});
  }
  tagVersion(id, modelId, versionId, tags, context={}) { const session = this._session(id);
    return this._updateModel(session, modelId, (model) => {
      if(!model.versions.some((item) => item.id === versionId)) throw new Error(`Unknown model version: ${versionId}`);
      const entry = {id: `${versionId}-tags`, versionId, tags};
      return {...model, versionTags: [...model.versionTags.filter((item) => item.versionId !== versionId), entry]};
    }, 'model.version.tag', context, {versionId});
  }
  setAlias(id, modelId, alias, versionId, options={}, context={}) { const session = this._session(id);
    if(!options.confirmationRef || options.modelId !== modelId || options.versionId !== versionId) throw new Error(
      'Model alias changes require target-bound consequential confirmation.');
    return this._updateModel(session, modelId, (model) => {
      if(!model.versions.some((item) => item.id === versionId)) throw new Error(`Unknown model version: ${versionId}`);
      const item = {id: alias, name: alias, versionId};
      return {...model, aliases: [...model.aliases.filter((entry) => entry.name !== alias), item]};
    }, 'model.alias.set', {...context, confirmationRef: options.confirmationRef}, {alias, versionId});
  }
  _updateModel(session, modelId, update, action, context, details) {
    let found = false; session.content = createMLVectorContent({...session.content,
      modelEntries: session.content.modelEntries.map((model) => {
        if(model.id !== modelId) return model; found = true; return update(model);
      })}); if(!found) throw new Error(`Unknown model: ${modelId}`);
    this._definitionChanged(session, action, context, {modelId, ...details});
    return this._touch(session, {dirty: true});
  }
  async validateIndex(id, designId, indexId, context={}) {
    const session = this._session(id); const {design, index} = this._index(session, designId, indexId);
    const described = await this._provider(design.resourceRef, 'describeVectorCapabilities',
      {resourceRef: design.resourceRef, signal: context.signal});
    this._status(session, design.resourceRef, `describeVectorCapabilities:${designId}`, described);
    if(!described.supportState.startsWith('supported')) throw new Error(
      `Vector capability discovery is ${described.supportState}.`);
    const capabilities = validateVectorCapabilities(described.value);
    const local = validateIndexForCapabilities(index, capabilities);
    const checked = await this._provider(design.resourceRef, 'validateIndexConfig',
      {resourceRef: design.resourceRef, design, index, capabilities, signal: context.signal});
    this._status(session, design.resourceRef, `validateIndexConfig:${indexId}`, checked);
    if(!checked.supportState.startsWith('supported')) throw new Error(`Vector index validation is ${checked.supportState}.`);
    const record = immutable({id: `index-validation-${++this.sequence}`, designId, indexId,
      providerId: providerId(design.resourceRef), local, provider: checked.value, evidence: checked.evidence,
      at: this.now()}); session.runtime.capabilitySnapshots.set(providerId(design.resourceRef),
      immutable({providerId: providerId(design.resourceRef), capabilities, evidence: described.evidence,
        providerVersion: described.providerVersion, at: this.now()}));
    session.runtime.validationResults.push(record); session.history.push(this._audit('vector.index.validate',
      session, context, {designId, indexId, resultId: record.id})); this._touch(session); return record;
  }
  searchVectors(id, request, context={}) { return this._submit(id, 'vector.search.large',
    'Run vector search', {request}, context); }
  async explainSearch(id, request, context={}) {
    const session = this._session(id); const normalized = normalizeSearchRequest(request);
    const {design, index} = this._index(session, normalized.designId, normalized.indexId);
    const prepared = await this._prepareSearch(session, design, index, normalized, context);
    const adapter = this.adapters.get(providerId(design.resourceRef));
    const explained = validateMLVectorProviderResult(await adapter.explainVectorSearch({
      resourceRef: design.resourceRef, design, index, request: normalized, prepared: prepared.value,
      signal: context.signal}), providerId(design.resourceRef), 'explainVectorSearch');
    this._status(session, design.resourceRef, `explainVectorSearch:${index.id}`, explained);
    if(!explained.supportState.startsWith('supported')) throw new Error(
      `Vector search explain is ${explained.supportState}.`);
    const record = immutable({id: `explain-${++this.sequence}`, designId: design.id, indexId: index.id,
      providerId: providerId(design.resourceRef), plan: explained.value, evidence: explained.evidence,
      at: this.now()}); session.runtime.explainResults.push(record);
    session.history.push(this._audit('vector.search.explain', session, context, {resultId: record.id}));
    this._touch(session); return record;
  }
  runEmbedding(id, pipelineId, options={}, context={}) { return this._submit(id, 'embedding.run',
    `Run embedding pipeline ${pipelineId}`, {pipelineId, ...options,
      mayUseExternalModel: context.currentUser?.permissions?.includes('ml_vector.use_external_model') === true},
    context); }
  runEvaluation(id, evaluationId, options={}, context={}) { return this._submit(id, 'ml.evaluation.run',
    `Run ML evaluation ${evaluationId}`, {evaluationId, ...options}, context); }
  buildIndex(id, designId, indexId, options={}, context={}) { return this._submit(id, 'vector.index.build',
    `Build vector index ${indexId}`, {designId, indexId, ...options}, context); }
  importModel(id, input, options={}, context={}) { return this._submit(id, 'model.import',
    'Import model artifact', {input, ...options}, context); }
  async save(id, request, context={}) { const session = this._session(id);
    if(!this.projectAssets) throw new Error('Project Asset service is unavailable.');
    const payload = mlVectorAssetRequest({...request, content: session.content});
    try { const saved = request.assetId ? await this.projectAssets.update(request.projectId,
      request.assetId, payload) : await this.projectAssets.create(request.projectId, payload);
    session.dirty = false; session.history.push(this._audit('ml_vector.asset.save', session, context,
      {version: saved.version})); this._touch(session); return saved; } catch(error) {
      session.error = error.message; session.state = 'runtime_failure'; session.problems.push(
        `conflict: ${error.message}`); this._touch(session, {dirty: true}); throw error;
    }
  }
  _index(session, designId, indexId) { const design = session.content.vectorDesigns.find(
    (item) => item.id === designId); if(!design) throw new Error(`Unknown vector design: ${designId}`);
  const index = design.indexes.find((item) => item.id === indexId);
  if(!index) throw new Error(`Unknown vector index: ${indexId}`); return {design, index}; }
  _submit(id, type, label, request, context) { const session = this._session(id);
    if(session.state === 'read_only') throw new Error('ML / Vector asset is read only.');
    const task = this.tasks.submit({type, sessionId: id, label, cancelable: true, resumable: false,
      audit: this._audit(type, session, context), ...request}, {owner: actor(context)});
    session.taskIds.push(task.id); session.taskSnapshots.set(task.id, task); session.activeTaskId = task.id;
    session.state = 'background_task_active'; this._touch(session);
    this.tasks.wait(task.id).catch((error) => { session.activeTaskId = null;
      session.state = error.name === 'AbortError' ? 'ready' : 'runtime_failure'; session.error = error.message;
      session.problems.push(error.message); this._touch(session); }); return task;
  }
  async _provider(reference, method, input) { const id = providerId(reference); const adapter = this.adapters.get(id);
    if(!adapter) return unknownProvider(id, method);
    return validateMLVectorProviderResult(await adapter[method](input), id, method);
  }
  _status(session, reference, method, result) { const id = providerId(reference);
    session.providerStatuses.set(`${id}:${method}`, immutable({providerId: id, operation: method, ...result}));
    if(['unknown', 'unsupported', 'partial', 'read_only'].includes(result.supportState)) session.state =
      result.supportState === 'read_only' ? 'read_only' : result.supportState;
  }
  async _prepareSearch(session, design, index, request, context) {
    await this.validateIndex(session.id, design.id, index.id, context);
    const prepared = await this._provider(design.resourceRef, 'prepareVectorSearch',
      {resourceRef: design.resourceRef, design, index, request, signal: context.signal});
    this._status(session, design.resourceRef, `prepareVectorSearch:${index.id}`, prepared);
    if(!prepared.supportState.startsWith('supported')) throw new Error(
      `Vector search preparation is ${prepared.supportState}.`); return prepared;
  }
  async _search(id, context) { const session = this._session(id);
    const request = normalizeSearchRequest(context.request); const {design, index} = this._index(
      session, request.designId, request.indexId); context.phase('prepare', 'Validating provider vector plan.');
    const prepared = await this._prepareSearch(session, design, index, request, context); context.progress(0.45);
    const adapter = this.adapters.get(providerId(design.resourceRef)); const executed = validateMLVectorProviderResult(
      await adapter.executeVectorSearch({resourceRef: design.resourceRef, design, index, request,
        prepared: prepared.value, signal: context.signal}), providerId(design.resourceRef), 'executeVectorSearch');
    this._status(session, design.resourceRef, `executeVectorSearch:${index.id}`, executed);
    if(!executed.supportState.startsWith('supported')) throw new Error(`Vector search is ${executed.supportState}.`);
    plainObject(executed.value, 'Vector search value'); const maximum = Math.min(request.k, 1000);
    if(!Array.isArray(executed.value.rows)) throw new TypeError('Vector search result requires rows[].');
    const rows = executed.value.rows.slice(0, maximum); noRawSecrets(rows, 'Vector search preview');
    const record = immutable({id: `search-${++this.sequence}`, designId: design.id, indexId: index.id,
      providerId: providerId(design.resourceRef), rows, total: Number(executed.value.total ?? rows.length),
      truncated: executed.value.rows.length > maximum || Boolean(executed.value.truncated),
      cursorRef: executed.value.cursorRef ?? null, evidence: executed.evidence, at: this.now()});
    session.runtime.searchResults.push(record); session.history.push(this._audit('vector.search.run',
      session, context, {resultId: record.id, rowCount: rows.length})); context.progress(1);
    this._finish(session); return record;
  }
  async _embedding(id, context) { const session = this._session(id); const pipeline =
    session.content.embeddingPipelines.find((item) => item.id === context.pipelineId);
  if(!pipeline) throw new Error(`Unknown embedding pipeline: ${context.pipelineId}`);
  if(pipeline.modelRef.external && !pipeline.modelRef.providerIdentity) throw new Error(
    'External embedding requires a visible provider identity.');
  if(pipeline.modelRef.external && pipeline.dataSharingPolicy.containsSensitiveData &&
        !pipeline.dataSharingPolicy.approvedForSensitiveData) throw new Error(
    'External sensitive embedding requires an approved data-sharing policy.');
  if(pipeline.modelRef.external && pipeline.dataSharingPolicy.containsSensitiveData &&
        !context.mayUseExternalModel) throw new Error('External sensitive embedding requires ml_vector.use_external_model.');
  const resolved = await this._provider(pipeline.resourceRef, 'resolveExternalModel',
    {modelRef: pipeline.modelRef, credentialRef: pipeline.modelRef.credentialRef, signal: context.signal});
  this._status(session, pipeline.resourceRef, `resolveExternalModel:${pipeline.id}`, resolved);
  if(!resolved.supportState.startsWith('supported')) throw new Error(`Embedding model resolution is ${resolved.supportState}.`);
  const adapter = this.adapters.get(providerId(pipeline.resourceRef)); const executed = validateMLVectorProviderResult(
    await adapter.runEmbedding({pipeline, model: resolved.value, selectionRef: context.selectionRef ?? null,
      signal: context.signal}), providerId(pipeline.resourceRef), 'runEmbedding');
  this._status(session, pipeline.resourceRef, `runEmbedding:${pipeline.id}`, executed);
  if(!executed.supportState.startsWith('supported')) throw new Error(`Embedding run is ${executed.supportState}.`);
  const record = immutable({id: `embedding-${++this.sequence}`, pipelineId: pipeline.id,
    providerId: providerId(pipeline.resourceRef), result: executed.value, evidence: executed.evidence,
    at: this.now()}); noRawSecrets(record, 'Embedding run record'); session.runtime.embeddingRuns.push(record);
  session.history.push(this._audit('embedding.run', session, context, {pipelineId: pipeline.id,
    resultId: record.id})); this.events.publish('embedding.completed', {sessionId: id,
    pipelineId: pipeline.id, resultId: record.id}, {origin: ML_VECTOR_MODULE_ID});
  context.progress(1); this._finish(session); return record;
  }
  async _evaluation(id, context) { const session = this._session(id); const evaluation =
    session.content.evaluationCases.find((item) => item.id === context.evaluationId);
  if(!evaluation) throw new Error(`Unknown ML evaluation: ${context.evaluationId}`);
  validateEvaluationCase(evaluation); const {design, index} = this._index(session,
    evaluation.vectorDesignId, evaluation.indexId); await this.validateIndex(id, design.id, index.id, context);
  const adapter = this.adapters.get(providerId(design.resourceRef)); const executed = validateMLVectorProviderResult(
    await adapter.runEvaluation({evaluation, design, index, signal: context.signal}),
    providerId(design.resourceRef), 'runEvaluation'); this._status(session, design.resourceRef,
    `runEvaluation:${evaluation.id}`, executed);
  if(!executed.supportState.startsWith('supported')) throw new Error(`ML evaluation is ${executed.supportState}.`);
  const metrics = normalizeEvaluationResult(executed.value, evaluation); const record = immutable({
    id: `evaluation-${++this.sequence}`, evaluationId: evaluation.id, providerId: providerId(design.resourceRef),
    groundTruthRef: evaluation.groundTruthRef, datasetRevisionRef: evaluation.datasetRevisionRef,
    metrics, evidence: executed.evidence, at: this.now()}); session.runtime.evaluationResults.push(record);
  session.history.push(this._audit('ml.evaluation.run', session, context, {evaluationId: evaluation.id,
    resultId: record.id})); this.events.publish('ml.evaluation.completed', {sessionId: id,
    evaluationId: evaluation.id, resultId: record.id}, {origin: ML_VECTOR_MODULE_ID});
  context.progress(1); this._finish(session); return record;
  }
  async _buildIndex(id, context) { const session = this._session(id);
    if(!context.confirmationRef || !context.environment || !context.connection) throw new Error(
      'Vector index build requires target-bound consequential confirmation.');
    const {design, index} = this._index(session, context.designId, context.indexId);
    if(context.connection !== mlVectorReferenceKey(design.resourceRef)) throw new Error(
      'Vector index build confirmation does not match the target resource.');
    await this.validateIndex(id, design.id, index.id, context); const adapter = this.adapters.get(
      providerId(design.resourceRef)); const executed = validateMLVectorProviderResult(await adapter.buildVectorIndex({
      resourceRef: design.resourceRef, design, index, signal: context.signal}), providerId(design.resourceRef),
    'buildVectorIndex'); this._status(session, design.resourceRef, `buildVectorIndex:${index.id}`, executed);
    if(!executed.supportState.startsWith('supported')) throw new Error(`Vector index build is ${executed.supportState}.`);
    const record = immutable({id: `index-build-${++this.sequence}`, designId: design.id, indexId: index.id,
      result: executed.value, evidence: executed.evidence, at: this.now()}); session.runtime.indexBuilds.push(record);
    this.events.publish('vector.index.changed', {sessionId: id, designId: design.id, indexId: index.id,
      reason: 'built'}, {origin: ML_VECTOR_MODULE_ID}); context.progress(1); this._finish(session); return record;
  }
  async _importModel(id, context) { const session = this._session(id);
    plainObject(context.input, 'Model import request'); noRawSecrets(context.input, 'Model import request');
    const provider = platformValue(context.providerId, 'Model import provider ID');
    const adapter = this.adapters.get(provider); if(!adapter) throw new Error(`Model import provider ${provider} is unknown.`);
    const imported = validateMLVectorProviderResult(await adapter.importModel({input: context.input,
      signal: context.signal}), provider, 'importModel'); if(!imported.supportState.startsWith('supported')) throw new Error(
      `Model import is ${imported.supportState}.`); const model = validateModelEntry(imported.value);
    if(session.content.modelEntries.some((item) => item.id === model.id)) throw new Error(`Model already exists: ${model.id}`);
    session.content = createMLVectorContent({...session.content,
      modelEntries: [...session.content.modelEntries, model]});
    this._definitionChanged(session, 'model.import', context, {modelId: model.id});
    const record = immutable({
      id: `model-import-${++this.sequence}`, providerId: provider, modelId: model.id,
      evidence: imported.evidence, at: this.now()}); session.runtime.modelImports.push(record);
    session.history.push(this._audit('model.import.completed', session, context,
      {modelId: model.id, resultId: record.id}));
    model.versions.forEach((version) => this.events.publish('model.version.registered', {sessionId: id,
      modelId: model.id, versionId: version.id, contentDigest: version.contentDigest},
    {origin: ML_VECTOR_MODULE_ID})); context.progress(1); this._finish(session); return record;
  }
  _definitionChanged(session, action, context, details={}) { session.runtime = runtimeState();
    session.providerStatuses.clear(); this._revalidate(session); session.history.push(this._audit(action,
      session, context, details)); this._mirrorRelationships(session); }
  _finish(session) { session.activeTaskId = null; session.error = ''; this._revalidate(session); this._touch(session); }
  _revalidate(session) { session.validation = validateMLVectorDefinition(session.content);
    session.problems = [...session.validation.errors]; session.state = session.validation.valid ? 'ready' :
      'validation_error'; }
  _audit(action, session, context={}, details={}) { return immutable({at: this.now(), actor: actor(context),
    action, target: session.id, assetRevision: context.assetRevision ?? null,
    environment: context.environment ?? null, connection: context.connection ?? null,
    confirmationRef: context.confirmationRef ?? null, details}); }
  _clearRelationships(session) { [...this.relationships.edges.values()].filter((edge) =>
    edge.origin === ML_VECTOR_MODULE_ID && edge.metadata?.sessionId === session.id)
    .forEach((edge) => this.relationships.removeEdge(edge.id)); [...this.relationships.nodes.values()].filter(
    (node) => node.metadata?.mlVectorSessionId === session.id).forEach((node) =>
    this.relationships.removeNode(node.id)); }
  _mirrorRelationships(session) { this._clearRelationships(session); const assetNode = `ml-vector:${session.id}`;
    this.relationships.upsertNode({id: assetNode, kind: 'ml_vector.asset', label: session.content.name || session.id,
      metadata: {mlVectorSessionId: session.id}}); const refs = [
      ...session.content.vectorDesigns.map((item) => ['vector_storage', item.resourceRef, item.id]),
      ...session.content.embeddingPipelines.map((item) => ['embedding_source', item.resourceRef, item.id]),
      ...session.content.modelEntries.flatMap((model) => model.versions.map((version) =>
        ['model_artifact', version.artifactRef, `${model.id}:${version.id}`])),
      ...session.content.experiments.flatMap((item) => item.inputRefs.map((ref) =>
        ['experiment_input', ref, item.id])),
      ...session.content.deploymentBindings.map((item) => ['deployment_endpoint', item.endpointRef, item.id])];
    refs.forEach(([relation, reference, owner]) => { const key = mlVectorReferenceKey(reference);
      const node = `ml-vector-ref:${key}`; this.relationships.upsertNode({id: node,
        kind: reference.schema.replace('cdeadmin.', '').replace('.v1', ''), reference,
        label: reference.canonical ?? reference.assetId ?? reference.id,
        metadata: {mlVectorSessionId: session.id}}); this.relationships.upsertEdge({
        id: `${assetNode}:${relation}:${owner}:${key}`, from: assetNode, to: node, relation,
        origin: ML_VECTOR_MODULE_ID, evidenceRefs: [], metadata: {sessionId: session.id, owner}}); }); }
}
