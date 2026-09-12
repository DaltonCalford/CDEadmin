/////////////////////////////////////////////////////////////
// API Designer runtime, provider admission and task authority.
/////////////////////////////////////////////////////////////

import {immutable, noRawSecrets, plainObject, platformValue} from '../../platform/serviceUtils';
import {diagnosticsService, platformEventService, stablePlatformId} from '../../platform/PlatformRegistry';
import {
  API_MODULE_ID, API_SERVICE_ID, apiAssetRequest, apiReferenceKey, createAPIContent,
  validateAPIRef, validateDataBinding, validateOperation,
} from './contracts';
import {
  ExternalReferenceResolver, exportAsyncAPI, exportOpenAPI, generateCRUDDraft,
  importAsyncAPI, importOpenAPI, validateAPIDefinition,
} from './APIEngine';

export {API_SERVICE_ID};
export const API_TASKS = Object.freeze(['api.import', 'api.validation', 'api.test', 'api.deployment']);
export const API_EVENTS = Object.freeze(['api.changed', 'api.validation.failed',
  'api.test.completed', 'api.deployment.changed']);
const SUPPORT_STATES = Object.freeze(['supported_native', 'supported_via_cdeadmin',
  'supported_via_external_adapter', 'read_only', 'partial', 'unsupported', 'unknown']);
const REQUIRED_ADAPTER_METHODS = Object.freeze(['resolveDataBinding', 'validateProviderOperation',
  'prepareInvocation', 'describeProviderSchema', 'invokePrepared']);

function actor(context={}) {
  const user = context.currentUser ?? {}; return String(user.id ?? user.username ?? user.email ??
    context.owner ?? 'unknown');
}
function providerId(reference) { return reference?.providerId ?? reference?.provider ?? null; }
function strings(input, field, id) {
  if(!Array.isArray(input[field])) throw new TypeError(`${id} API result requires ${field}[].`);
  return [...new Set(input[field].map((item) => platformValue(item, `${id} ${field}`)))].sort();
}
export function validateAPIProviderResult(input, id, operation, {allowEmpty=false}={}) {
  plainObject(input, `${id} ${operation} result`); noRawSecrets(input, `${id} ${operation} result`);
  const allowed = ['supportState', 'providerVersion', 'evidence', 'warnings', 'nativeDetails',
    'readCapabilities', 'writeCapabilities', 'discoveryCapabilities', 'nativeMechanisms',
    'versionConstraints', 'limitations', 'runtimeEvidence', 'value'];
  const unknown = Object.keys(input).filter((field) => !allowed.includes(field));
  if(unknown.length) throw new TypeError(`${id} ${operation} result contains unsupported field ${unknown[0]}.`);
  const supportState = platformValue(input.supportState, 'API support state');
  if(!SUPPORT_STATES.includes(supportState)) throw new TypeError('API support state is invalid.');
  if(!Array.isArray(input.warnings)) throw new TypeError(`${id} API result requires warnings[].`);
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

export class APIAdapterRegistry {
  constructor() { this.adapters = new Map(); }
  register(id, adapter) {
    id = stablePlatformId(id, 'API provider ID');
    REQUIRED_ADAPTER_METHODS.forEach((method) => {
      if(typeof adapter?.[method] !== 'function') throw new TypeError(`API adapter ${id} requires ${method}().`);
    });
    if(this.adapters.has(id)) throw new Error(`API adapter already registered: ${id}`);
    this.adapters.set(id, adapter); return () => this.adapters.delete(id);
  }
  get(id) { return id ? this.adapters.get(stablePlatformId(id, 'API provider ID')) ?? null : null; }
  list() { return [...this.adapters.keys()].sort(); }
}
function unknownProvider(id, operation) {
  return immutable({providerId: id ?? 'unknown', operation, supportState: 'unknown', providerVersion: null,
    evidence: {}, warnings: ['No API Designer adapter response is registered.'], nativeDetails: {},
    readCapabilities: [], writeCapabilities: [], discoveryCapabilities: [], nativeMechanisms: [],
    versionConstraints: [], limitations: ['No API Designer adapter response is registered.'],
    runtimeEvidence: null});
}
function runtimeState() { return {testResults: [], deploymentPlans: [], providerSchemas: new Map()}; }
function sessionView(session) {
  return immutable({schema: 'cdeadmin.api-session.v1', id: session.id, content: session.content,
    state: session.state, dirty: session.dirty, surface: session.surface, selectedId: session.selectedId,
    validation: session.validation, providerStatuses: [...session.providerStatuses.values()],
    runtime: immutable({...session.runtime, testResults: [...session.runtime.testResults],
      deploymentPlans: [...session.runtime.deploymentPlans],
      providerSchemas: [...session.runtime.providerSchemas.values()]}),
    tasks: [...session.taskSnapshots.values()], activeTaskId: session.activeTaskId,
    history: [...session.history], problems: [...session.problems], error: session.error,
    createdAt: session.createdAt, updatedAt: session.updatedAt});
}

export class APIService {
  constructor({tasks, relationships, search, projectAssets, adapters=new APIAdapterRegistry(),
    events=platformEventService, diagnostics=diagnosticsService, resolverFactory=(options) =>
      new ExternalReferenceResolver(options), now=() => new Date().toISOString()}={}) {
    if(!tasks || !relationships || !search) throw new TypeError(
      'API service requires Task, Relationship and Search services.');
    this.tasks = tasks; this.relationships = relationships; this.search = search;
    this.projectAssets = projectAssets; this.adapters = adapters; this.events = events;
    this.diagnostics = diagnostics; this.resolverFactory = resolverFactory; this.now = now;
    this.sessions = new Map(); this.listeners = new Set(); this.sequence = 0;
    this.disposers = [
      tasks.register('api.import', (request, context) => this._import(request.sessionId,
        {...request, ...context})),
      tasks.register('api.validation', (request, context) => this._providerValidation(
        request.sessionId, {...request, ...context})),
      tasks.register('api.test', (request, context) => this._test(request.sessionId,
        {...request, ...context})),
      tasks.register('api.deployment', (request, context) => this._deployment(
        request.sessionId, {...request, ...context})),
      search.register({id: 'api.search', priority: 41,
        types: ['api.asset', 'api.operation', 'api.channel', 'api.schema', 'api.resource'],
        search: async (query, {context={}}={}) => {
          if(!context.permissions?.includes('api.view')) return [];
          const needle = query.toLowerCase(); const result = [];
          for(const session of this.sessions.values()) {
            if(`${session.id} ${session.content.info.title ?? ''}`.toLowerCase().includes(needle)) result.push({
              id: session.id, type: 'api.asset', label: session.content.info.title || session.id,
              context: `API · ${session.content.profile}`});
            [['operation', session.content.operations], ['channel', session.content.channels],
              ['schema', session.content.schemas]].forEach(([type, values]) => values.forEach((item) => {
              if(`${item.id} ${item.name}`.toLowerCase().includes(needle)) result.push({id: item.id,
                type: `api.${type}`, label: item.name, context: `${session.id} · ${type}`});
            }));
            session.content.operations.map((item) => item.binding?.targetRef).filter(Boolean)
              .forEach((reference) => { if(JSON.stringify(reference).toLowerCase().includes(needle)) result.push({
                id: apiReferenceKey(reference), type: 'api.resource', label: reference.canonical ?? reference.id,
                context: `${session.id} · live binding`, reference}); });
          }
          return result;
        }}),
      tasks.subscribe((task) => {
        for(const session of this.sessions.values()) if(session.taskIds.includes(task.id)) {
          session.taskSnapshots.set(task.id, task); this._emit(session);
        }
      }),
    ];
  }
  dispose() { for(const session of this.sessions.values()) this._clearRelationships(session);
    this.disposers.reverse().forEach((remove) => remove()); this.disposers = []; }
  subscribe(listener) { this.listeners.add(listener); return () => this.listeners.delete(listener); }
  _emit(session) { this.listeners.forEach((listener) => listener(sessionView(session))); }
  _touch(session, {dirty=session.dirty}={}) { session.updatedAt = this.now(); session.dirty = dirty;
    this._emit(session); return sessionView(session); }
  _session(id) { const session = this.sessions.get(String(id));
    if(!session) throw new Error(`Unknown API Designer session: ${id}`); return session; }
  create(input={}) {
    plainObject(input, 'API session'); const id = input.id ?? `api-${++this.sequence}`;
    if(this.sessions.has(id)) throw new Error(`API session already exists: ${id}`);
    const content = createAPIContent(input.content ?? input); const validation = validateAPIDefinition(content);
    const time = this.now(); const session = {id, content,
      state: !content.operations.length && !content.channels.length ? 'empty' : validation.valid ? 'ready' :
        'validation_error', dirty: false, surface: 'api_explorer', selectedId: null, validation,
      providerStatuses: new Map(), runtime: runtimeState(), taskIds: [], taskSnapshots: new Map(),
      activeTaskId: null, history: [], problems: [...validation.errors], error: '', createdAt: time,
      updatedAt: time};
    this.sessions.set(id, session); this._mirrorRelationships(session); this._emit(session);
    return sessionView(session);
  }
  get(id) { return sessionView(this._session(id)); }
  list() { return [...this.sessions.values()].map(sessionView); }
  select(id, {surface, selectedId=null}={}) { const session = this._session(id);
    if(surface) session.surface = surface; session.selectedId = selectedId; return this._touch(session); }
  replaceDefinition(id, input, context={}) {
    const session = this._session(id); session.content = createAPIContent(input);
    session.runtime = runtimeState(); session.taskIds = []; session.taskSnapshots.clear();
    session.activeTaskId = null; session.providerStatuses.clear(); this._revalidate(session);
    session.history.push(this._audit('api.create', session, context)); this._mirrorRelationships(session);
    this.events.publish('api.changed', {sessionId: id, reason: 'definition_replaced'},
      {origin: API_MODULE_ID}); return this._touch(session, {dirty: true});
  }
  addOperation(id, operation, context={}) {
    const session = this._session(id); operation = validateOperation(operation);
    if(session.content.operations.some((item) => item.id === operation.id)) throw new Error(
      `API operation already exists: ${operation.id}`);
    session.content = createAPIContent({...session.content,
      operations: [...session.content.operations, operation]}); this._definitionChanged(session,
      'api.operation.add', context, {operationId: operation.id}); return this._touch(session, {dirty: true});
  }
  setBinding(id, operationId, binding, context={}) {
    const session = this._session(id); binding = validateDataBinding(binding); let found = false;
    session.content = createAPIContent({...session.content, operations: session.content.operations.map((item) => {
      if(item.id !== operationId) return item; found = true; return {...item, binding};
    })});
    if(!found) throw new Error(`Unknown API operation: ${operationId}`);
    this._definitionChanged(session, 'api.binding.set', context, {operationId,
      target: apiReferenceKey(binding.targetRef)}); return this._touch(session, {dirty: true});
  }
  proposeBinding(id, operationId, resourceRef) {
    const session = this._session(id); const operation = session.content.operations.find(
      (item) => item.id === operationId); if(!operation) throw new Error(`Unknown API operation: ${operationId}`);
    const targetRef = validateAPIRef(resourceRef, 'API drop resource');
    return immutable({schema: 'cdeadmin.api-binding-proposal.v1', applied: false, operationId,
      binding: {id: `binding-${operationId}`, type: 'provider_resource_read', targetRef,
        mode: 'read', command: null, inputMap: {}, outputMap: {}, nativeDetails: {}, extensions: []}});
  }
  generateCRUD(id, input, context={}) {
    const session = this._session(id); const draft = generateCRUDDraft(input);
    session.history.push(this._audit('api.crud.draft', session, context,
      {resource: apiReferenceKey(draft.resourceRef), operationIds: draft.operations.map((item) => item.id)}));
    return draft;
  }
  applyCRUDDraft(id, draft, context={}) {
    plainObject(draft, 'API CRUD draft'); if(draft.schema !== 'cdeadmin.api-crud-draft.v1' ||
        draft.deployed !== false) throw new TypeError('Reviewed CRUD draft is invalid.');
    const session = this._session(id); const existing = new Set(session.content.operations.map((item) => item.id));
    draft.operations.forEach((item) => { if(existing.has(item.id)) throw new Error(
      `API operation already exists: ${item.id}`); });
    session.content = createAPIContent({...session.content,
      operations: [...session.content.operations, ...draft.operations]});
    this._definitionChanged(session, 'api.crud.apply_draft', context,
      {operationIds: draft.operations.map((item) => item.id)}); return this._touch(session, {dirty: true});
  }
  validate(id, context={}) { const session = this._session(id); this._revalidate(session);
    session.history.push(this._audit('api.validate', session, context, session.validation));
    if(!session.validation.valid) this.events.publish('api.validation.failed',
      {sessionId: id, errors: session.validation.errors}, {origin: API_MODULE_ID});
    return this._touch(session); }
  import(id, profile, source, options={}, context={}) { return this._submit(id, 'api.import',
    `Import ${profile}`, {profile, source, ...options}, context); }
  test(id, testId, options={}, context={}) { return this._submit(id, 'api.test',
    `Run API test ${testId}`, {testId, ...options, canInvokeLiveWrite:
      context.currentUser?.permissions?.includes('api.invoke_live_write') === true}, context); }
  prepareDeployment(id, deploymentId, options={}, context={}) { return this._submit(id,
    'api.deployment', `Prepare API deployment ${deploymentId}`, {deploymentId, ...options}, context); }
  export(id, profile, options={}, context={}) { return this._submit(id, 'api.validation',
    `Export ${profile}`, {action: 'export', profile, ...options}, context); }
  async save(id, request, context={}) {
    const session = this._session(id); if(!this.projectAssets) throw new Error('Project Asset service is unavailable.');
    const payload = apiAssetRequest({...request, content: session.content});
    try { const saved = request.assetId ? await this.projectAssets.update(request.projectId,
      request.assetId, payload) : await this.projectAssets.create(request.projectId, payload);
    session.dirty = false; session.history.push(this._audit('api.asset.save', session, context,
      {version: saved.version})); this._touch(session); return saved; } catch(error) {
      session.error = error.message; session.state = 'runtime_failure'; session.problems.push(
        `conflict: ${error.message}`); this._touch(session, {dirty: true}); throw error;
    }
  }
  async resolveExternal(id, uri, options={}, context={}) {
    const session = this._session(id); const resolver = this.resolverFactory(options);
    const resolved = await resolver.resolve(uri, {baseURI: options.baseURI});
    session.content = createAPIContent({...session.content, externalReferences: [
      ...session.content.externalReferences.filter((item) => item.uri !== resolved.uri),
      {id: `external-${session.content.externalReferences.length + 1}`, uri: resolved.uri,
        provenance: resolved.provenance, contentDigest: options.contentDigest ?? null}]});
    session.history.push(this._audit('api.external.resolve', session, context, {uri: resolved.uri}));
    return this._touch(session, {dirty: true});
  }
  _definitionChanged(session, action, context, details) {
    session.runtime = runtimeState(); session.providerStatuses.clear(); this._revalidate(session);
    session.history.push(this._audit(action, session, context, details)); this._mirrorRelationships(session);
    this.events.publish('api.changed', {sessionId: session.id, reason: action}, {origin: API_MODULE_ID});
  }
  _submit(id, type, label, request, context) {
    const session = this._session(id); if(session.state === 'read_only') throw new Error('API asset is read only.');
    const task = this.tasks.submit({type, sessionId: id, label, cancelable: true, resumable: false,
      resourceRefs: session.content.operations.map((item) => item.binding?.targetRef).filter(Boolean),
      audit: this._audit(type, session, context), ...request}, {owner: actor(context)});
    session.taskIds.push(task.id); session.taskSnapshots.set(task.id, task);
    session.activeTaskId = task.id; session.state = 'background_task_active'; this._touch(session);
    this.tasks.wait(task.id).catch((error) => { session.activeTaskId = null;
      session.state = error.name === 'AbortError' ? 'ready' : 'runtime_failure'; session.error = error.message;
      session.problems.push(error.message); this._touch(session); }); return task;
  }
  async _provider(reference, method, input) {
    const id = providerId(reference); const adapter = this.adapters.get(id);
    if(!adapter) return unknownProvider(id, method);
    return validateAPIProviderResult(await adapter[method](input), id, method);
  }
  _status(session, reference, method, result) {
    const id = providerId(reference); session.providerStatuses.set(`${id}:${method}`,
      immutable({providerId: id, operation: method, ...result}));
    if(['unknown', 'unsupported', 'partial', 'read_only'].includes(result.supportState)) {
      session.state = result.supportState === 'read_only' ? 'read_only' : result.supportState;
    }
  }
  async _import(id, context) {
    const session = this._session(id); context.phase('parse', 'Parsing versioned API source.');
    const content = context.profile === 'openapi_http' ? importOpenAPI(context.source,
      context.provenance) : context.profile === 'asyncapi_event' ? importAsyncAPI(context.source,
      context.provenance) : null;
    if(!content) throw new Error(`${context.profile} requires its separate import adapter.`);
    context.progress(0.7); session.content = content; session.runtime = runtimeState();
    session.providerStatuses.clear(); this._revalidate(session); session.dirty = true;
    session.history.push(this._audit(`api.import.${context.profile}`, session, context,
      {version: content.externalSpecVersion})); this._mirrorRelationships(session);
    this.events.publish('api.changed', {sessionId: id, reason: 'import'}, {origin: API_MODULE_ID});
    context.progress(1); this._finish(session); return content;
  }
  async _providerValidation(id, context) {
    const session = this._session(id);
    if(context.action === 'export') {
      const document = context.profile === 'openapi_http' ? exportOpenAPI(session.content) :
        context.profile === 'asyncapi_event' ? exportAsyncAPI(session.content) : null;
      if(!document) throw new Error(`${context.profile} requires its separate export adapter.`);
      context.progress(1); this._finish(session); return immutable({profile: context.profile,
        version: session.content.externalSpecVersion, metadataOnly: true, document});
    }
    const validation = validateAPIDefinition(session.content); const providerResults = [];
    for(let index = 0; index < session.content.operations.length; index++) {
      const operation = session.content.operations[index]; if(!operation.binding) continue;
      const resolved = await this._provider(operation.binding.targetRef, 'resolveDataBinding',
        {binding: operation.binding, operation, signal: context.signal});
      this._status(session, operation.binding.targetRef, `resolveDataBinding:${operation.id}`, resolved);
      if(!resolved.supportState.startsWith('supported')) throw new Error(
        `API binding ${operation.binding.id} is ${resolved.supportState}.`);
      const described = await this._provider(operation.binding.targetRef, 'describeProviderSchema',
        {binding: operation.binding, operation, resolved: resolved.value, signal: context.signal});
      this._status(session, operation.binding.targetRef, `describeProviderSchema:${operation.id}`, described);
      if(!described.supportState.startsWith('supported')) throw new Error(
        `API schema discovery for ${operation.id} is ${described.supportState}.`);
      session.runtime.providerSchemas.set(`${providerId(operation.binding.targetRef)}:${operation.binding.id}`,
        immutable({id: operation.binding.id, providerId: providerId(operation.binding.targetRef),
          operationId: operation.id, schema: described.value, evidence: described.evidence}));
      const checked = await this._provider(operation.binding.targetRef, 'validateProviderOperation',
        {binding: operation.binding, operation, resolved: resolved.value,
          providerSchema: described.value, signal: context.signal});
      this._status(session, operation.binding.targetRef, `validateProviderOperation:${operation.id}`, checked);
      if(!checked.supportState.startsWith('supported')) throw new Error(
        `API operation ${operation.id} is ${checked.supportState}.`);
      providerResults.push({operationId: operation.id, resolved, described, checked});
      context.progress((index + 1) / Math.max(1, session.content.operations.length));
    }
    session.validation = immutable({...validation, providerResults});
    if(!validation.valid) this.events.publish('api.validation.failed', {sessionId: id,
      errors: validation.errors}, {origin: API_MODULE_ID});
    this._finish(session, {preserveValidation: true}); return session.validation;
  }
  async _test(id, context) {
    const session = this._session(id); const test = session.content.tests.find((item) => item.id === context.testId);
    if(!test) throw new Error(`Unknown API test: ${context.testId}`);
    const operation = test.operationId ? session.content.operations.find((item) => item.id === test.operationId) : null;
    if(test.mode === 'live' && !test.environmentId) throw new Error('Live API tests require an explicit environment.');
    if(test.mode === 'live' && operation && !operation.idempotent && (!context.confirmationRef ||
        context.environment !== test.environmentId)) throw new Error(
      'Non-idempotent live API tests require target-bound write confirmation.');
    if(test.mode === 'live' && operation && !operation.idempotent && !context.canInvokeLiveWrite) throw new Error(
      'Non-idempotent live API tests require api.invoke_live_write permission.');
    let result;
    if(test.mode !== 'live') result = {status: 'passed', mode: test.mode,
      evidence: {validatedLocally: true}, response: {status: test.expected.status ?? 'mock'}};
    else {
      if(!operation?.binding) throw new Error('Live API tests require an operation data binding.');
      const prepared = await this._provider(operation.binding.targetRef, 'prepareInvocation',
        {test, operation, environment: test.environmentId, signal: context.signal});
      this._status(session, operation.binding.targetRef, `prepareInvocation:${test.id}`, prepared);
      if(!prepared.supportState.startsWith('supported')) throw new Error(
        `API test preparation is ${prepared.supportState}.`);
      const adapter = this.adapters.get(providerId(operation.binding.targetRef));
      const invoked = validateAPIProviderResult(await adapter.invokePrepared({prepared: prepared.value,
        confirmationRef: context.confirmationRef, signal: context.signal}),
      providerId(operation.binding.targetRef), 'invokePrepared');
      this._status(session, operation.binding.targetRef, `invokePrepared:${test.id}`, invoked);
      if(!invoked.supportState.startsWith('supported')) throw new Error(`API invocation is ${invoked.supportState}.`);
      const maximum = Math.min(Number(context.maxPreviewBytes ?? 65536), 1024 * 1024);
      const encoded = JSON.stringify(invoked.value?.body ?? null); result = {status: invoked.value?.status,
        mode: 'live', evidence: invoked.evidence, response: {status: invoked.value?.status,
          headerFields: this._redactHeaders(invoked.value?.headers ?? {}, test.sensitiveHeaderNames),
          bodyPreview: encoded.slice(0, maximum), truncated: encoded.length > maximum}};
    }
    noRawSecrets(result, 'API test result'); const record = immutable({id: `test-result-${++this.sequence}`,
      testId: test.id, environmentId: test.environmentId, at: this.now(), ...result});
    session.runtime.testResults.push(record); session.history.push(this._audit('api.test.run', session,
      context, {testId: test.id, resultId: record.id, status: record.status}));
    this.events.publish('api.test.completed', {sessionId: id, testId: test.id,
      resultId: record.id, status: record.status}, {origin: API_MODULE_ID});
    context.progress(1); this._finish(session); return record;
  }
  async _deployment(id, context) {
    const session = this._session(id); const binding = session.content.deploymentBindings.find(
      (item) => item.id === context.deploymentId);
    if(!binding) throw new Error(`Unknown API deployment binding: ${context.deploymentId}`);
    if(!context.confirmationRef || context.environment !== binding.environment ||
        context.connection !== apiReferenceKey(binding.targetRef)) throw new Error(
      'Deployment preparation requires target-bound consequential confirmation.');
    const validation = validateAPIDefinition(session.content);
    if(!validation.valid) throw new Error(`API definition is invalid: ${validation.errors.join(' ')}`);
    const format = session.content.profile === 'openapi_http' ? 'openapi_http' :
      session.content.profile === 'asyncapi_event' ? 'asyncapi_event' : null;
    if(!format) throw new Error(`${session.content.profile} requires its separate deployment adapter.`);
    const document = format === 'openapi_http' ? exportOpenAPI(session.content) : exportAsyncAPI(session.content);
    const plan = immutable({schema: 'cdeadmin.api-deployment-plan.v1', id: `deployment-${++this.sequence}`,
      deploymentBindingId: binding.id, environment: binding.environment,
      targetRef: binding.targetRef, confirmationRef: context.confirmationRef,
      preparedOnly: true, deployExecuted: false, profile: format,
      externalSpecVersion: session.content.externalSpecVersion, document, createdAt: this.now()});
    session.runtime.deploymentPlans.push(plan); session.history.push(this._audit('api.deploy.prepare',
      session, context, {planId: plan.id})); this.events.publish('api.deployment.changed',
      {sessionId: id, planId: plan.id, state: 'prepared'}, {origin: API_MODULE_ID});
    context.progress(1); this._finish(session); return plan;
  }
  _redactHeaders(headers, configured=[]) {
    plainObject(headers, 'API response headers'); const sensitive = new Set([
      'authorization', 'proxy-authorization', 'cookie', 'set-cookie', ...configured.map((item) => item.toLowerCase())]);
    return Object.entries(headers).map(([name, value]) => ({name,
      value: sensitive.has(name.toLowerCase()) ? '[REDACTED]' : String(value),
      redacted: sensitive.has(name.toLowerCase())}));
  }
  _finish(session, {preserveValidation=false}={}) { session.activeTaskId = null; session.error = '';
    if(preserveValidation) { session.problems = [...session.validation.errors];
      session.state = session.validation.valid ? 'ready' : 'validation_error'; }
    else this._revalidate(session); this._touch(session); }
  _revalidate(session) { session.validation = validateAPIDefinition(session.content);
    session.problems = [...session.validation.errors]; session.state = session.validation.valid ? 'ready' :
      'validation_error'; }
  _audit(action, session, context={}, details={}) { return immutable({at: this.now(), actor: actor(context),
    action, target: session.id, assetRevision: context.assetRevision ?? null,
    environment: context.environment ?? null, connection: context.connection ?? null,
    confirmationRef: context.confirmationRef ?? null, details}); }
  _clearRelationships(session) {
    [...this.relationships.edges.values()].filter((edge) => edge.origin === API_MODULE_ID &&
      edge.metadata?.sessionId === session.id).forEach((edge) => this.relationships.removeEdge(edge.id));
    [...this.relationships.nodes.values()].filter((node) => node.metadata?.apiSessionId === session.id)
      .forEach((node) => this.relationships.removeNode(node.id));
  }
  _mirrorRelationships(session) {
    this._clearRelationships(session); const assetNode = `api:${session.id}`;
    this.relationships.upsertNode({id: assetNode, kind: 'api.asset',
      label: session.content.info.title || session.id, metadata: {apiSessionId: session.id}});
    const refs = [...session.content.operations.map((item) => ['operation_binding', item.binding?.targetRef,
      item.id]), ...session.content.schemas.flatMap((item) => item.physicalBindings.map((ref) =>
      ['schema_binding', ref, item.id])), ...session.content.deploymentBindings.map((item) =>
      ['deployment_target', item.targetRef, item.id])].filter(([, ref]) => ref);
    refs.forEach(([relation, reference, owner]) => { const key = apiReferenceKey(reference);
      const node = `api-ref:${key}`; this.relationships.upsertNode({id: node,
        kind: reference.schema.replace('cdeadmin.', '').replace('.v1', ''), reference,
        label: reference.canonical ?? reference.id ?? reference.assetId,
        metadata: {apiSessionId: session.id}}); this.relationships.upsertEdge({
        id: `${assetNode}:${relation}:${owner}:${key}`, from: assetNode, to: node, relation,
        origin: API_MODULE_ID, evidenceRefs: [], metadata: {sessionId: session.id, owner}}); });
  }
}
