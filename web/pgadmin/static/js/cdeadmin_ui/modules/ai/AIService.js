/////////////////////////////////////////////////////////////
// Governed AI runtime, model admission and execution authority.
/////////////////////////////////////////////////////////////

import {immutable, noRawSecrets, plainObject, platformValue} from '../../platform/serviceUtils';
import {diagnosticsService, platformEventService, stablePlatformId} from '../../platform/PlatformRegistry';
import {
  AI_MODULE_ID, AI_SERVICE_ID, aiAssetRequest, aiReferenceKey, createAIContent,
  validateActionPlan, validateAIRef, validateContextScope,
} from './contracts';
import {
  buildGovernedModelRequest, targetRevisionKey, validateAIContent, validateApproval,
  validateAssistantOutput, validatePlanForExecution, validateToolInvocations,
} from './AIEngine';

export {AI_SERVICE_ID};
export const AI_TASKS = Object.freeze(['ai.request', 'ai.plan.validation', 'ai.plan.execution']);
export const AI_EVENTS = Object.freeze(['ai.plan.created', 'ai.action.approved',
  'ai.action.rejected', 'ai.plan.executed']);
const SUPPORT_STATES = Object.freeze(['supported_native', 'supported_via_cdeadmin',
  'supported_via_external_adapter', 'read_only', 'partial', 'unsupported', 'unknown']);
const REQUIRED_ADAPTER_METHODS = Object.freeze(['registerReadTool', 'registerProposalTool',
  'sanitizeContext', 'generateResponse', 'validateProposedAction', 'executeApprovedAction']);

function actor(context={}) { const user = context.currentUser ?? {};
  return String(user.id ?? user.username ?? user.email ?? context.owner ?? 'unknown'); }
function strings(input, field, id) { if(!Array.isArray(input[field])) throw new TypeError(
  `${id} AI result requires ${field}[].`); return [...new Set(input[field].map((item) =>
  platformValue(item, `${id} ${field}`)))].sort(); }
export function validateAIProviderResult(input, id, operation, {allowEmpty=false}={}) {
  plainObject(input, `${id} ${operation} result`); noRawSecrets(input, `${id} ${operation} result`);
  const allowed = ['supportState', 'providerVersion', 'evidence', 'warnings', 'nativeDetails',
    'readCapabilities', 'writeCapabilities', 'discoveryCapabilities', 'nativeMechanisms',
    'versionConstraints', 'limitations', 'runtimeEvidence', 'value']; const unknown = Object.keys(input)
    .filter((field) => !allowed.includes(field)); if(unknown.length) throw new TypeError(
    `${id} ${operation} result contains unsupported field ${unknown[0]}.`);
  const supportState = platformValue(input.supportState, 'AI support state');
  if(!SUPPORT_STATES.includes(supportState)) throw new TypeError('AI support state is invalid.');
  if(!Array.isArray(input.warnings)) throw new TypeError(`${id} AI result requires warnings[].`);
  const result = immutable({supportState, providerVersion: platformValue(input.providerVersion,
    `${id} provider version`), evidence: immutable({...plainObject(input.evidence, `${id} evidence`)}),
  warnings: input.warnings.map(String), nativeDetails: immutable({...plainObject(input.nativeDetails,
    `${id} native details`)}), readCapabilities: strings(input, 'readCapabilities', id),
  writeCapabilities: strings(input, 'writeCapabilities', id), discoveryCapabilities: strings(input,
    'discoveryCapabilities', id), nativeMechanisms: strings(input, 'nativeMechanisms', id),
  versionConstraints: strings(input, 'versionConstraints', id), limitations: strings(input,
    'limitations', id), runtimeEvidence: input.runtimeEvidence == null ? null : immutable({...plainObject(
    input.runtimeEvidence, `${id} runtime evidence`)}), value: input.value});
  if(supportState.startsWith('supported') && !allowEmpty && input.value === undefined) throw new TypeError(
    `${id} returned empty success for ${operation}.`); return result;
}
export class AIAdapterRegistry { constructor() { this.adapters = new Map(); }
  register(id, adapter) { id = stablePlatformId(id, 'AI provider ID'); REQUIRED_ADAPTER_METHODS.forEach((method) => {
    if(typeof adapter?.[method] !== 'function') throw new TypeError(`AI adapter ${id} requires ${method}().`);
  }); if(this.adapters.has(id)) throw new Error(`AI adapter already registered: ${id}`);
  this.adapters.set(id, adapter); return () => this.adapters.delete(id); }
  get(id) { return id ? this.adapters.get(stablePlatformId(id, 'AI provider ID')) ?? null : null; }
  list() { return [...this.adapters.keys()].sort(); } }
function unknownProvider(id, operation) { return immutable({providerId: id ?? 'unknown', operation,
  supportState: 'unknown', providerVersion: null, evidence: {}, warnings: ['No AI adapter is registered.'],
  nativeDetails: {}, readCapabilities: [], writeCapabilities: [], discoveryCapabilities: [],
  nativeMechanisms: [], versionConstraints: [], limitations: ['No AI adapter is registered.'],
  runtimeEvidence: null}); }
function runtimeState() { return {assistantSessions: [], messages: [], outputs: [], approvals: [],
  toolInvocations: [], planValidations: [], executionResults: [], activeSessionId: null, activePlanId: null}; }
function sessionView(session) { return immutable({schema: 'cdeadmin.ai-session.v1', id: session.id,
  content: session.content, state: session.state, dirty: session.dirty, surface: session.surface,
  selectedId: session.selectedId, validation: session.validation,
  providerStatuses: [...session.providerStatuses.values()], runtime: immutable({...session.runtime,
    assistantSessions: [...session.runtime.assistantSessions], messages: [...session.runtime.messages],
    outputs: [...session.runtime.outputs], approvals: [...session.runtime.approvals],
    toolInvocations: [...session.runtime.toolInvocations], planValidations: [...session.runtime.planValidations],
    executionResults: [...session.runtime.executionResults]}), tasks: [...session.taskSnapshots.values()],
  activeTaskId: session.activeTaskId, history: [...session.history], problems: [...session.problems],
  error: session.error, createdAt: session.createdAt, updatedAt: session.updatedAt}); }

export class AIService {
  constructor({tasks, relationships, search, projectAssets, commands, metadata, providers,
    adapters=new AIAdapterRegistry(), modelProfileResolver=async () => null,
    contextReader=async () => [], events=platformEventService, diagnostics=diagnosticsService,
    now=() => new Date().toISOString()}={}) {
    if(!tasks || !relationships || !search || !commands) throw new TypeError(
      'AI service requires Task, Relationship, Search and Command services.');
    this.tasks = tasks; this.relationships = relationships; this.search = search; this.commands = commands;
    this.metadata = metadata; this.providers = providers; this.projectAssets = projectAssets;
    this.adapters = adapters; this.modelProfileResolver = modelProfileResolver;
    this.contextReader = contextReader; this.events = events; this.diagnostics = diagnostics; this.now = now;
    this.sessions = new Map(); this.listeners = new Set(); this.sequence = 0;
    this.disposers = [tasks.register('ai.request', (request, context) => this._request(request.sessionId,
      {...request, ...context})), tasks.register('ai.plan.validation', (request, context) =>
      this._validatePlan(request.sessionId, {...request, ...context})),
    tasks.register('ai.plan.execution', (request, context) => this._executePlan(request.sessionId,
      {...request, ...context})), search.register({id: 'ai.search', priority: 45,
      types: ['ai.asset', 'ai.context', 'ai.plan', 'ai.output'], search: async (query, {context={}}={}) => {
        if(!context.permissions?.includes('ai.use')) return []; const needle = query.toLowerCase(); const result = [];
        for(const session of this.sessions.values()) { if(`${session.id} ${session.content.name}`.toLowerCase()
          .includes(needle)) result.push({id: session.id, type: 'ai.asset', label: session.content.name || session.id,
          context: 'AI Assistant'}); session.content.savedContextRefs.forEach((item) => {
          if(`${item.id} ${item.name}`.toLowerCase().includes(needle)) result.push({id: item.id,
            type: 'ai.context', label: item.name, context: session.id}); }); session.content.savedPlans.forEach((item) => {
          if(`${item.id} ${item.name}`.toLowerCase().includes(needle)) result.push({id: item.id,
            type: 'ai.plan', label: item.name, context: session.id}); }); session.runtime.outputs.forEach((item) => {
          if(`${item.id} ${item.text}`.toLowerCase().includes(needle)) result.push({id: item.id,
            type: 'ai.output', label: item.type, context: session.id}); }); } return result; }}),
    tasks.subscribe((task) => { for(const session of this.sessions.values()) if(session.taskIds.includes(task.id)) {
      session.taskSnapshots.set(task.id, task); this._emit(session); } })];
  }
  dispose() { for(const session of this.sessions.values()) this._clearRelationships(session);
    this.disposers.reverse().forEach((remove) => remove()); this.disposers = []; }
  subscribe(listener) { this.listeners.add(listener); return () => this.listeners.delete(listener); }
  _emit(session) { this.listeners.forEach((listener) => listener(sessionView(session))); }
  _touch(session, {dirty=session.dirty}={}) { session.updatedAt = this.now(); session.dirty = dirty;
    this._emit(session); return sessionView(session); }
  _session(id) { const session = this.sessions.get(String(id)); if(!session) throw new Error(
    `Unknown AI asset session: ${id}`); return session; }
  create(input={}) { plainObject(input, 'AI asset session'); const id = input.id ?? `ai-${++this.sequence}`;
    if(this.sessions.has(id)) throw new Error(`AI asset session already exists: ${id}`);
    const content = createAIContent(input.content ?? input); const validation = validateAIContent(content);
    const time = this.now(); const session = {id, content, state: validation.valid ? 'ready' : 'validation_error',
      dirty: false, surface: 'assistant_dock', selectedId: null, validation, providerStatuses: new Map(),
      runtime: runtimeState(), taskIds: [], taskSnapshots: new Map(), activeTaskId: null, history: [],
      problems: [...validation.errors], error: '', createdAt: time, updatedAt: time};
    this.sessions.set(id, session); this._mirrorRelationships(session); this._emit(session); return sessionView(session); }
  get(id) { return sessionView(this._session(id)); }
  list() { return [...this.sessions.values()].map(sessionView); }
  select(id, {surface, selectedId=null}={}) { const session = this._session(id); if(surface) session.surface = surface;
    session.selectedId = selectedId; return this._touch(session); }
  newAssistantSession(id, input={}, context={}) { const session = this._session(id);
    plainObject(input, 'Assistant session'); const mode = input.mode ?? session.content.sessionPolicy.defaultMode;
    if(!session.content.sessionPolicy.allowedModes.includes(mode)) throw new Error(`AI mode ${mode} is not allowed.`);
    const contextScopeIds = [...new Set(input.contextScopeIds ?? [])]; const available = new Set(
      session.content.savedContextRefs.map((item) => item.id)); if(contextScopeIds.some((item) => !available.has(item)))
      throw new Error('Assistant session references unavailable context.'); const record = immutable({
      id: input.id ?? `assistant-${++this.sequence}`, projectId: input.projectId ?? null, mode,
      modelProfileRef: session.content.modelProfileRef, contextScopeIds, createdBy: actor(context),
      createdAt: this.now()}); session.runtime.assistantSessions.push(record);
    session.runtime.activeSessionId = record.id; session.history.push(this._audit('ai.session.new', session,
      context, {assistantSessionId: record.id})); return this._touch(session); }
  addContext(id, scopeInput, context={}) { const session = this._session(id); const scope = validateContextScope(scopeInput);
    if(session.content.savedContextRefs.some((item) => item.id === scope.id)) throw new Error(
      `AI context scope already exists: ${scope.id}`); session.content = createAIContent({...session.content,
      savedContextRefs: [...session.content.savedContextRefs, scope]}); const active = this._active(session);
    if(active) this._replaceAssistant(session, active.id, {...active,
      contextScopeIds: [...new Set([...active.contextScopeIds, scope.id])]});
    this._definitionChanged(session, 'ai.context.add', context, {scopeId: scope.id});
    return this._touch(session, {dirty: true}); }
  removeContext(id, scopeId, context={}) { const session = this._session(id);
    if(session.content.savedPlans.some((plan) => plan.contextScopeIds.includes(scopeId))) throw new Error(
      'AI context is referenced by a saved plan.'); if(!session.content.savedContextRefs.some((item) =>
      item.id === scopeId)) throw new Error(`Unknown AI context scope: ${scopeId}`);
    session.content = createAIContent({...session.content,
      savedContextRefs: session.content.savedContextRefs.filter((item) => item.id !== scopeId)});
    session.runtime.assistantSessions = session.runtime.assistantSessions.map((item) => immutable({...item,
      contextScopeIds: item.contextScopeIds.filter((id) => id !== scopeId)}));
    this._definitionChanged(session, 'ai.context.remove', context, {scopeId});
    return this._touch(session, {dirty: true}); }
  async ask(id, prompt, options={}, context={}) { return this._request(id, {prompt, mode: options.mode,
    currentUser: context.currentUser, owner: actor(context), direct: true}); }
  createPlan(id, planInput, context={}) { const session = this._session(id); const plan = validateActionPlan(planInput);
    if(session.content.savedPlans.some((item) => item.id === plan.id)) throw new Error(`AI plan already exists: ${plan.id}`);
    session.content = createAIContent({...session.content, savedPlans: [...session.content.savedPlans, plan]});
    session.runtime.activePlanId = plan.id; session.runtime.approvals = session.runtime.approvals.filter((item) =>
      item.planId !== plan.id); this._definitionChanged(session, 'ai.plan.create', context, {planId: plan.id});
    this.events.publish('ai.plan.created', {sessionId: id, planId: plan.id, revision: plan.revision},
      {origin: AI_MODULE_ID}); return this._touch(session, {dirty: true}); }
  async validatePlan(id, planId, options={}, context={}) { return this._validatePlan(id,
    {planId, currentRevision: options.currentRevision, currentUser: context.currentUser, direct: true}); }
  approveAction(id, planId, actionIds, input, context={}) { return this._decision(id, planId, actionIds,
    'approved', input, context); }
  rejectAction(id, planId, actionIds, input, context={}) { return this._decision(id, planId, actionIds,
    'rejected', input, context); }
  executePlan(id, planId, options={}, context={}) { return this._submit(id, 'ai.plan.execution',
    `Execute approved AI plan ${planId}`, {planId, ...options, executionContext: {
      currentUser: context.currentUser, services: context.services, commandCustomizations: context.commandCustomizations}},
    context); }
  requestAsTask(id, prompt, options={}, context={}) { return this._submit(id, 'ai.request', 'AI request',
    {prompt, mode: options.mode, currentUser: context.currentUser}, context); }
  validatePlanAsTask(id, planId, options={}, context={}) { return this._submit(id, 'ai.plan.validation',
    `Validate AI plan ${planId}`, {planId, currentRevision: options.currentRevision,
      currentUser: context.currentUser}, context); }
  async saveOutputAsAsset(id, outputId, request, context={}) { const session = this._session(id);
    if(!this.projectAssets) throw new Error('Project Asset service is unavailable.'); const output =
      session.runtime.outputs.find((item) => item.id === outputId); if(!output) throw new Error(`Unknown AI output: ${outputId}`);
    plainObject(request, 'AI output asset request'); noRawSecrets(request, 'AI output asset request');
    const payload = {asset_type: platformValue(request.assetType, 'Output asset type'),
      schema_name: platformValue(request.schemaName ?? request.assetType, 'Output schema name'), schema_version: 1,
      name: platformValue(request.name, 'Output asset name'), path: platformValue(request.path, 'Output asset path'),
      expected_version: 0, content: output.plan ?? output.diff ?? {type: output.type, text: output.text,
        evidence: output.evidence}, metadata: {origin: AI_MODULE_ID, outputId}, dependency_references: output.evidence
        .map((item) => item.sourceRef), resource_bindings: [], source_control_eligible: true, editor_capable: true,
      viewer_capable: true, validation_state: 'unknown', validation_details: []}; noRawSecrets(payload,
      'AI output asset payload'); const saved = await this.projectAssets.create(request.projectId, payload);
    session.history.push(this._audit('ai.output.save_as_asset', session, context, {outputId,
      assetId: saved.asset_id, assetType: request.assetType})); this._touch(session); return saved; }
  async save(id, request, context={}) { const session = this._session(id);
    if(!this.projectAssets) throw new Error('Project Asset service is unavailable.'); const payload = aiAssetRequest({
      ...request, content: session.content}); try { const saved = request.assetId ? await this.projectAssets.update(
      request.projectId, request.assetId, payload) : await this.projectAssets.create(request.projectId, payload);
    session.dirty = false; session.history.push(this._audit('ai.asset.save', session, context,
      {version: saved.version})); this._touch(session); return saved; } catch(error) { session.error = error.message;
      session.state = 'runtime_failure'; session.problems.push(`conflict: ${error.message}`);
      this._touch(session, {dirty: true}); throw error; } }
  _active(session) { return session.runtime.assistantSessions.find((item) =>
    item.id === session.runtime.activeSessionId) ?? null; }
  _replaceAssistant(session, id, value) { session.runtime.assistantSessions = session.runtime.assistantSessions
    .map((item) => item.id === id ? immutable(value) : item); }
  _plan(session, id) { const plan = session.content.savedPlans.find((item) => item.id === id);
    if(!plan) throw new Error(`Unknown AI plan: ${id}`); return plan; }
  async _profile(session) { if(!session.content.modelProfileRef) return null;
    const profile = await this.modelProfileResolver(session.content.modelProfileRef); if(!profile) return null;
    plainObject(profile, 'AI model profile'); noRawSecrets(profile, 'AI model profile');
    const allowed = ['profileId', 'backendType', 'modelIdentifier', 'providerId', 'endpointRef',
      'credentialRef', 'contextLimit', 'dataPolicy', 'toolPolicy', 'retentionPolicy',
      'approvedForSensitiveData']; const unknown = Object.keys(profile).filter((field) =>
      !allowed.includes(field)); if(unknown.length) throw new TypeError(
      `AI model profile contains unsupported field ${unknown[0]}.`);
    for(const field of ['profileId', 'backendType', 'modelIdentifier', 'providerId']) platformValue(profile[field],
      `AI model profile ${field}`); if(!['local', 'self_hosted', 'remote'].includes(profile.backendType)) throw new TypeError(
      'AI model backend type is invalid.'); if(typeof profile.approvedForSensitiveData !== 'boolean') throw new TypeError(
      'AI model sensitive-data approval must be explicit.');
    if(profile.endpointRef) validateAIRef(profile.endpointRef, 'AI model endpoint');
    if(profile.credentialRef) validateAIRef(profile.credentialRef, 'AI model credential',
      new Set(['cdeadmin.credential-ref.v1']));
    if(profile.contextLimit != null && (!Number.isInteger(profile.contextLimit) || profile.contextLimit < 1))
      throw new TypeError('AI model context limit must be a positive integer.');
    for(const field of ['dataPolicy', 'toolPolicy']) plainObject(profile[field], `AI model ${field}`);
    if(profile.retentionPolicy != null) plainObject(profile.retentionPolicy, 'AI model retention policy');
    return immutable({...profile}); }
  async _provider(id, method, input) { const adapter = this.adapters.get(id); if(!adapter) return unknownProvider(id, method);
    return validateAIProviderResult(await adapter[method](input), id, method); }
  _status(session, id, method, result) { session.providerStatuses.set(`${id}:${method}`,
    immutable({providerId: id, operation: method, ...result})); if(['unknown', 'unsupported', 'partial',
    'read_only'].includes(result.supportState)) session.state = result.supportState === 'read_only' ?
    'read_only' : result.supportState; }
  async _request(id, context) { const session = this._session(id); const active = this._active(session);
    if(!active) throw new Error('Create an assistant session before asking a question.'); const profile = await this._profile(session);
    if(!profile) throw new Error('AI model profile is not configured or cannot be resolved.');
    const scopes = session.content.savedContextRefs.filter((item) => active.contextScopeIds.includes(item.id));
    if(profile.backendType === 'remote' && scopes.some((scope) => ['sensitive', 'restricted'].includes(scope.sensitivity)) &&
        !profile.approvedForSensitiveData) throw new Error('Remote model is not approved for sensitive context.');
    const contextItems = await this.contextReader(scopes, {metadataOnlyDefault: true, currentUser: context.currentUser});
    let request = buildGovernedModelRequest({prompt: context.prompt, mode: context.mode ?? active.mode,
      content: session.content, contextItems, permissions: context.currentUser?.permissions ?? []});
    const sanitized = await this._provider(profile.providerId, 'sanitizeContext', {request, profile,
      signal: context.signal});
    this._status(session, profile.providerId, 'sanitizeContext', sanitized); if(!sanitized.supportState.startsWith('supported'))
      throw new Error(`AI context sanitization is ${sanitized.supportState}.`); request = sanitized.value;
    noRawSecrets(request, 'Sanitized AI model request');
    const readTools = await this._provider(profile.providerId, 'registerReadTool', {request, profile,
      allowedToolIds: session.content.sessionPolicy.allowedReadToolIds, signal: context.signal});
    const proposalTools = await this._provider(profile.providerId, 'registerProposalTool', {request, profile,
      allowedCommandIds: session.content.sessionPolicy.allowedProposalCommandIds, signal: context.signal});
    this._status(session, profile.providerId, 'registerReadTool', readTools);
    this._status(session, profile.providerId, 'registerProposalTool', proposalTools);
    for(const [kind, result] of [['read', readTools], ['proposal', proposalTools]]) {
      if(!result.supportState.startsWith('supported')) throw new Error(`AI ${kind} tool registration is ${
        result.supportState}.`); const allowedIds = kind === 'read' ?
        session.content.sessionPolicy.allowedReadToolIds :
        session.content.sessionPolicy.allowedProposalCommandIds;
      session.runtime.toolInvocations.push(immutable({id: `tool-${++this.sequence}`,
        schema: 'cdeadmin.ai-tool-registration.v1', kind, providerId: profile.providerId,
        allowedIds, sanitizedArguments: {allowedIds}, outcome: 'registered', result: result.value,
        at: this.now()}));
    }
    const generated = await this._provider(profile.providerId,
      'generateResponse', {request, profile, readTools: readTools.value,
        proposalTools: proposalTools.value, signal: context.signal});
    this._status(session, profile.providerId, 'generateResponse', generated);
    if(!generated.supportState.startsWith('supported')) throw new Error(`AI request is ${generated.supportState}.`);
    const output = validateAssistantOutput(generated.value, {requireEvidence:
      session.content.sessionPolicy.requireEvidence}); const promptRecord = immutable({id: `message-${++this.sequence}`,
      assistantSessionId: active.id, role: 'user', text: session.content.conversationPersistencePolicy.storePrompts ?
        context.prompt : '[not persisted by policy]', at: this.now()}); const responseRecord = immutable({
      id: `message-${++this.sequence}`, assistantSessionId: active.id, role: 'assistant', outputId: output.id,
      text: session.content.conversationPersistencePolicy.storeResponses ? output.text : '[not persisted by policy]',
      at: this.now()}); const toolInvocations = validateToolInvocations(
      output.nativeDetails.toolInvocations ?? [], session.content).map((item) => immutable({...item,
      providerId: profile.providerId, at: this.now()}));
    session.runtime.toolInvocations.push(...toolInvocations);
    if(session.content.conversationPersistencePolicy.persistMessages)
      session.runtime.messages.push(promptRecord, responseRecord); session.runtime.outputs.push(output);
    session.history.push(this._audit('ai.ask', session, context, {outputId: output.id, profileId: profile.profileId}));
    this._mirrorRelationships(session); this._finish(session); return output; }
  async _validatePlan(id, context) { const session = this._session(id); const plan = this._plan(session, context.planId);
    const local = validatePlanForExecution(plan, session.content, this.commands, context.currentRevision);
    const providerResults = []; const profile = await this._profile(session); if(profile) for(const action of plan.actions) {
      const checked = await this._provider(profile.providerId, 'validateProposedAction', {action, plan,
        currentRevision: context.currentRevision}); this._status(session, profile.providerId,
        `validateProposedAction:${action.id}`, checked); providerResults.push({actionId: action.id, result: checked}); }
    const result = immutable({id: `plan-validation-${++this.sequence}`, planId: plan.id, planRevision: plan.revision,
      targetRevision: plan.targetRevision, valid: local.valid && providerResults.every((item) =>
        item.result.supportState.startsWith('supported') && item.result.value?.valid === true),
      errors: [...local.errors, ...providerResults.filter((item) => !item.result.supportState.startsWith('supported') ||
        item.result.value?.valid !== true).map((item) => `Provider rejected action ${item.actionId}.`)],
      order: local.order, providerResults, at: this.now()}); session.runtime.planValidations.push(result);
    session.history.push(this._audit('ai.plan.validate', session, context, {planId: plan.id, valid: result.valid}));
    this._touch(session); return result; }
  _decision(id, planId, actionIds, decision, input={}, context={}) { const session = this._session(id);
    const plan = this._plan(session, planId); const approval = validateApproval({schema: 'cdeadmin.ai-approval.v1',
      id: input.id ?? `approval-${++this.sequence}`, planId, planRevision: plan.revision,
      targetRevision: String(input.currentRevision), actionIds, decision, actor: actor(context),
      reason: String(input.reason ?? ''), approvedAt: this.now()}, plan, actor(context), input.currentRevision);
    session.runtime.approvals = session.runtime.approvals.filter((item) => !(item.planId === planId &&
      item.actionIds.some((actionId) => actionIds.includes(actionId)))); session.runtime.approvals.push(approval);
    const event = decision === 'approved' ? 'ai.action.approved' : 'ai.action.rejected';
    session.history.push(this._audit(event, session, context, {planId, actionIds, approvalId: approval.id}));
    this.events.publish(event, {sessionId: id, planId, actionIds, approvalId: approval.id},
      {origin: AI_MODULE_ID}); return this._touch(session); }
  _submit(id, type, label, request, context) { const session = this._session(id);
    const task = this.tasks.submit({type, sessionId: id, label, cancelable: true, resumable: false,
      audit: this._audit(type, session, context), ...request}, {owner: actor(context)}); session.taskIds.push(task.id);
    session.taskSnapshots.set(task.id, task); session.activeTaskId = task.id; session.state = 'background_task_active';
    this._touch(session); this.tasks.wait(task.id).catch((error) => { session.activeTaskId = null;
      session.state = error.name === 'AbortError' ? 'ready' : 'runtime_failure'; session.error = error.message;
      session.problems.push(error.message); this._touch(session); }); return task; }
  async _executePlan(id, context) { const session = this._session(id); const plan = this._plan(session, context.planId);
    if(!context.confirmationRef || context.environment == null || !context.connection) throw new Error(
      'AI plan execution requires target-bound consequential confirmation.');
    const validation = await this._validatePlan(id, {planId: plan.id, currentRevision: context.currentRevision,
      currentUser: context.executionContext?.currentUser}); if(!validation.valid) throw new Error(
      `AI plan is not executable: ${validation.errors.join(' ')}`); const approvals = session.runtime.approvals.filter(
      (item) => item.planId === plan.id && item.decision === 'approved' && item.planRevision === plan.revision &&
      item.targetRevision === String(context.currentRevision)); const approved = new Set(approvals.flatMap((item) =>
      item.actionIds)); if(plan.actions.some((item) => !approved.has(item.id))) throw new Error(
      'Every AI plan action requires a current revision-bound approval.');
    const profile = await this._profile(session); if(!profile) throw new Error(
      'AI model profile is not configured or cannot be resolved.');
    const results = []; for(const actionId of validation.order) { const action = plan.actions.find((item) => item.id === actionId);
      if(context.connection !== aiReferenceKey(action.targetRef)) throw new Error(
        `AI execution confirmation does not match action target ${action.id}.`);
      const commandContext = {...context.executionContext, currentUser: context.executionContext?.currentUser,
        aiApproval: approvals.find((item) => item.actionIds.includes(action.id)), confirmationRef: context.confirmationRef,
        environment: context.environment, connection: context.connection}; let executions = 0;
      const execute = async () => { if(executions++) throw new Error(
        `AI action ${action.id} attempted more than one command execution.`); return this.commands.execute(
        action.commandId, action.arguments, commandContext); };
      const mediated = await this._provider(profile.providerId, 'executeApprovedAction', {action, plan,
        approval: commandContext.aiApproval, execute}); this._status(session, profile.providerId,
        `executeApprovedAction:${action.id}`, mediated); if(!mediated.supportState.startsWith('supported')) throw new Error(
        `AI action execution is ${mediated.supportState}.`); if(executions !== 1) throw new Error(
        `AI action ${action.id} did not execute through CommandRegistry exactly once.`);
      noRawSecrets(mediated.value, `AI action ${action.id} result`);
      results.push(immutable({actionId: action.id, commandId: action.commandId, result: mediated.value,
        approvalId: commandContext.aiApproval.id})); context.progress(results.length / plan.actions.length); }
    const record = immutable({id: `execution-${++this.sequence}`, planId: plan.id,
      approvalRevisionKey: targetRevisionKey(plan), results, at: this.now()}); session.runtime.executionResults.push(record);
    session.history.push(this._audit('ai.plan.execute', session, context, {planId: plan.id,
      executionId: record.id})); this.events.publish('ai.plan.executed', {sessionId: id, planId: plan.id,
      executionId: record.id}, {origin: AI_MODULE_ID}); this._finish(session); return record; }
  _definitionChanged(session, action, context, details={}) { session.runtime.approvals = [];
    session.runtime.planValidations = []; this._revalidate(session); session.history.push(this._audit(action,
      session, context, details)); this._mirrorRelationships(session); }
  _finish(session) { session.activeTaskId = null; session.error = ''; this._revalidate(session); this._touch(session); }
  _revalidate(session) { session.validation = validateAIContent(session.content);
    session.problems = [...session.validation.errors]; session.state = session.validation.valid ? 'ready' :
      'validation_error'; }
  _audit(action, session, context={}, details={}) { const record = {at: this.now(), actor: actor(context), action,
    target: session.id, assetRevision: context.assetRevision ?? null, environment: context.environment ?? null,
    connection: context.connection ?? null, confirmationRef: context.confirmationRef ?? null, details};
  noRawSecrets(record, 'AI audit record'); return immutable(record); }
  _clearRelationships(session) { [...this.relationships.edges.values()].filter((edge) => edge.origin === AI_MODULE_ID &&
    edge.metadata?.sessionId === session.id).forEach((edge) => this.relationships.removeEdge(edge.id));
  [...this.relationships.nodes.values()].filter((node) => node.metadata?.aiSessionId === session.id)
    .forEach((node) => this.relationships.removeNode(node.id)); }
  _mirrorRelationships(session) { this._clearRelationships(session); const assetNode = `ai:${session.id}`;
    this.relationships.upsertNode({id: assetNode, kind: 'ai.asset', label: session.content.name || session.id,
      metadata: {aiSessionId: session.id}}); const refs = [
      ...session.content.savedContextRefs.map((item) => ['context', item.reference, item.id]),
      ...(session.content.modelProfileRef ? [['model_profile', session.content.modelProfileRef, 'model']] : []),
      ...session.content.savedPlans.flatMap((plan) => plan.actions.map((action) =>
        ['proposed_target', action.targetRef, `${plan.id}:${action.id}`])),
      ...session.runtime.outputs.flatMap((output) => output.evidence.map((item) =>
        ['evidence', item.sourceRef, `${output.id}:${item.id}`]))]; refs.forEach(([relation, reference, owner]) => {
      const key = aiReferenceKey(reference); const node = `ai-ref:${session.id}:${key}`;
      this.relationships.upsertNode({id: node,
        kind: reference.schema.replace('cdeadmin.', '').replace('.v1', ''), reference,
        label: reference.canonical ?? reference.assetId ?? reference.id, metadata: {aiSessionId: session.id}});
      this.relationships.upsertEdge({id: `${assetNode}:${relation}:${owner}:${key}`, from: assetNode, to: node,
        relation, origin: AI_MODULE_ID, evidenceRefs: [], metadata: {sessionId: session.id, owner}}); }); }
}
