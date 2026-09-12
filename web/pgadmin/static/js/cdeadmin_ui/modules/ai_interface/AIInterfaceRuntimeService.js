/////////////////////////////////////////////////////////////
// Activated AI Interface command authority and session runtime.
/////////////////////////////////////////////////////////////

import {immutable, noRawSecrets, plainObject, platformValue} from
  '../../platform/serviceUtils';
import {validateAIAsset} from './AIAssetContracts';
import {aiActor, exactLifecycleObject} from './AILifecycleContracts';
import {adaptAIFormCommandArguments} from './AIFormCommandAdapter';

export const AI_INTERFACE_RUNTIME_SERVICE_ID =
  'cdeadmin.ai_interface.runtime';
export const AI_SESSION_TURN_TASK = 'cdeadmin.ai-interface.session-turn';

function actor(context, permission) { return aiActor(context, permission); }
function required(value, label) { return platformValue(value, label, 2048); }
function exact(input, fields, label) {
  exactLifecycleObject(input, fields, label);
  return input;
}
function sessionView(record) {
  return immutable({...record, context: record.context.map((item) => immutable({...item})),
    messages: record.messages.map((item) => immutable({...item}))});
}
function retention(input) {
  return validateAIAsset('AIRetentionPolicy', input);
}

export class AIInterfaceRuntimeService {
  constructor({assets, connectors, plans, planExecution, queries, audit,
    emergency, backgroundRuns, tasks, toolCatalog=null,
    modelTester=null, sessionResponder=null}={}) {
    const requiredAuthorities = {assets: ['save'], connectors: ['create', 'get'],
      plans: ['create', 'get'], planExecution: ['execute', 'cancel'],
      queries: ['compile', 'review', 'execute'], audit: ['append', 'export'],
      emergency: ['snapshot', 'disableAll'], backgroundRuns: ['start', 'cancel'],
      tasks: ['register', 'submit', 'wait', 'cancel']};
    const supplied = {assets, connectors, plans, planExecution, queries, audit,
      emergency, backgroundRuns, tasks};
    for(const [name, methods] of Object.entries(requiredAuthorities)) {
      const authority = supplied[name];
      if(!authority || methods.some((method) =>
        typeof authority[method] !== 'function')) throw new TypeError(
        `AI Interface runtime requires complete ${name} authority.`
      );
    }
    if(toolCatalog && (typeof toolCatalog.publish !== 'function' ||
        typeof toolCatalog.invokeCommand !== 'function')) throw new TypeError(
      'AI Interface tool-catalog authority is incomplete.'
    );
    if(modelTester !== null && typeof modelTester !== 'function') throw new TypeError(
      'AI model test authority must be callable.'
    );
    if(sessionResponder !== null && typeof sessionResponder !== 'function') throw new TypeError(
      'AI session response authority must be callable.'
    );
    this.assets = assets; this.connectors = connectors; this.plans = plans;
    this.planExecution = planExecution; this.queries = queries; this.audit = audit;
    this.emergency = emergency; this.backgroundRuns = backgroundRuns;
    this.tasks = tasks; this.toolCatalog = toolCatalog;
    this.modelTester = modelTester; this.sessionResponder = sessionResponder;
    this.sessions = new Map(); this.savedAssets = new Map(); this.sequence = 0;
    this.unregisterTurn = tasks.register(AI_SESSION_TURN_TASK,
      (request, taskContext) => this._runTurn(request, taskContext));
  }

  dispose() { this.unregisterTurn?.(); this.unregisterTurn = null; }

  capabilities() {
    return immutable({modelTesting: this.modelTester !== null,
      sessionTurns: this.sessionResponder !== null,
      toolCatalog: this.toolCatalog !== null,
      connectorClasses: this.connectors.adapters.list().map((item) =>
        item.connectorClass)});
  }

  screenData(screenId) {
    const name = String(screenId).replace('cdeadmin.ai_interface.', '');
    const values = {
      ai_workbench: this.listSessions(), session_history: this.listSessions(),
      database_connectors: this.connectors.list(),
      connector_health: this.connectors.list(), mcp_connectors: this.connectors.list()
        .filter((item) => ['scratchbird_mcp', 'mcp_generic'].includes(
          item.profile.connectorClass)),
      agent_profiles: [...this.savedAssets.values()].filter((item) =>
        item.assetKind === 'AIAgentProfile'),
      model_providers: [...this.savedAssets.values()].filter((item) =>
        item.assetKind === 'AIModelProfile'),
      plan_review: this.plans.list(), run_monitor: [...this.planExecution.runs.values(),
        ...this.backgroundRuns.runs.values()], audit: this.audit.list(),
      usage_cost: this.audit.list().filter((item) => item.usage != null),
      ai_admin: [this.emergency.snapshot()],
    };
    return immutable(values[name] ?? []);
  }

  listSessions() {
    return immutable([...this.sessions.values()].map(sessionView).sort((left, right) =>
      right.updatedAt.localeCompare(left.updatedAt)));
  }

  getSession(sessionId) { return sessionView(this._session(sessionId)); }

  newSession(input, context={}) {
    exact(input, ['sessionId', 'name', 'agentProfileRef', 'mode', 'projectId',
      'connectorRefs', 'contextRefs'], 'AI session creation');
    const owner = actor(context, 'ai.manage_own_sessions');
    const mode = String(input.mode ?? 'ASK');
    if(!['ASK', 'DISCOVER', 'ANALYZE', 'DRAFT', 'PLAN', 'OPERATE'].includes(mode)) {
      throw new TypeError('AI session mode is invalid.');
    }
    const sessionId = input.sessionId ?? `ai-session-${++this.sequence}`;
    if(this.sessions.has(sessionId)) throw new Error(
      `AI session already exists: ${sessionId}`
    );
    const now = new Date().toISOString();
    const record = {schema: 'cdeadmin.ai-interface-session.v1', sessionId,
      name: String(input.name ?? sessionId), owner: owner.id,
      agentProfileRef: required(input.agentProfileRef, 'AI agent profile reference'),
      mode, projectId: input.projectId == null ? null : required(
        input.projectId, 'AI session project'), connectorRefs: [...new Set(
        (input.connectorRefs ?? []).map((item) => required(item,
          'AI session connector reference')))], context: (input.contextRefs ?? []).map(
        (reference) => ({reference: required(reference, 'AI session context reference'),
          exposureLevel: 'IDENTITY_ONLY', classification: 'INTERNAL'})),
      messages: [], activeTaskRef: null, state: 'ready', archived: false,
      createdAt: now, updatedAt: now};
    noRawSecrets(record, 'AI session'); this.sessions.set(sessionId, record);
    this.audit.append({eventType: 'ai.session.new', sessionId, initiator: owner.id,
      agentProfileRef: record.agentProfileRef, resourceRefs: record.context.map(
        (item) => item.reference)});
    return sessionView(record);
  }

  archiveSession(sessionId, context={}) {
    const record = this._owned(sessionId, context); record.archived = true;
    record.state = 'archived'; record.updatedAt = new Date().toISOString();
    this.audit.append({eventType: 'ai.session.archived', sessionId: record.sessionId,
      initiator: actor(context, 'ai.manage_own_sessions').id});
    return sessionView(record);
  }

  addContext(sessionId, input, context={}) {
    const record = this._owned(sessionId, context);
    exact(input, ['reference', 'exposureLevel', 'classification', 'source',
      'freshness', 'sizeEstimate'], 'AI session context');
    const reference = required(input.reference, 'AI context reference');
    if(record.context.some((item) => item.reference === reference)) throw new Error(
      `AI context already exists: ${reference}`
    );
    const item = this._contextItem(input); record.context.push(item);
    this._touch(record); this.audit.append({eventType: 'ai.context.added',
      sessionId, initiator: actor(context, 'ai.use').id,
      resourceRefs: [reference], redactedArguments: {exposureLevel: item.exposureLevel,
        classification: item.classification}});
    return sessionView(record);
  }

  removeContext(sessionId, reference, context={}) {
    const record = this._owned(sessionId, context); reference = required(
      reference, 'AI context reference');
    if(!record.context.some((item) => item.reference === reference)) throw new Error(
      `Unknown AI context: ${reference}`
    );
    record.context = record.context.filter((item) => item.reference !== reference);
    this._touch(record); this.audit.append({eventType: 'ai.context.removed',
      sessionId, initiator: actor(context, 'ai.use').id, resourceRefs: [reference]});
    return sessionView(record);
  }

  setContextExposure(sessionId, input, context={}) {
    const record = this._owned(sessionId, context);
    exact(input, ['reference', 'exposureLevel', 'classification', 'reason'],
      'AI context exposure change');
    const reference = required(input.reference, 'AI context reference');
    const prior = record.context.find((item) => item.reference === reference);
    if(!prior) throw new Error(`Unknown AI context: ${reference}`);
    const next = this._contextItem({...prior, ...input});
    if(['CONFIDENTIAL', 'RESTRICTED', 'REGULATED'].includes(next.classification) &&
        !context.currentUser?.permissions?.includes('ai.view_sensitive_context')) {
      throw new Error('The current user lacks ai.view_sensitive_context.');
    }
    record.context = record.context.map((item) => item.reference === reference ?
      next : item); this._touch(record);
    this.audit.append({eventType: 'ai.context.exposure_changed', sessionId,
      initiator: actor(context, 'ai.use').id, resourceRefs: [reference],
      diagnostics: [{message: required(input.reason,
        'AI context exposure reason')}], redactedArguments: {
        exposureLevel: next.exposureLevel, classification: next.classification}});
    return sessionView(record);
  }

  ask(sessionId, input, context={}) {
    if(!this.sessionResponder) throw new Error(
      'No governed AI model/session responder is configured.'
    );
    const record = this._owned(sessionId, context);
    exact(input, ['prompt', 'retentionPolicy'], 'AI session request');
    if(record.archived) throw new Error('Archived AI sessions cannot run requests.');
    if(record.activeTaskRef) throw new Error('The AI session already has an active request.');
    const policy = retention(input.retentionPolicy);
    const prompt = required(input.prompt, 'AI session prompt');
    const task = this.tasks.submit({id: `${sessionId}:turn:${++this.sequence}`,
      type: AI_SESSION_TURN_TASK, label: `AI session ${record.name}`,
      sessionId, prompt, retentionPolicy: policy, cancelable: true,
      resumable: false, retry: {maximum: 0}, audit: {sessionId}},
    {owner: actor(context, 'ai.use').id, executionContext: context});
    record.activeTaskRef = task.id; record.state = 'running'; this._touch(record);
    this.tasks.wait(task.id).then(() => this._finishTurn(record),
      () => this._finishTurn(record));
    return task;
  }

  cancelSession(sessionId, reason, context={}) {
    const record = this._owned(sessionId, context);
    reason = required(reason, 'AI cancellation reason');
    const cancelled = record.activeTaskRef ? this.tasks.cancel(record.activeTaskRef) : false;
    this.audit.append({eventType: 'ai.session.cancel_requested', sessionId,
      initiator: actor(context, 'ai.use').id,
      taskRefs: record.activeTaskRef ? [record.activeTaskRef] : [],
      diagnostics: [{message: reason, cancelled}]});
    return cancelled;
  }

  async saveAsset(projectId, kind, content, options={}, context={}) {
    const saved = await this.assets.save(required(projectId, 'AI asset project'),
      kind, content, options);
    this.savedAssets.set(`${projectId}/${saved.asset_id}`, saved);
    this.audit.append({eventType: 'ai.asset.saved', initiator: actor(
      context, this._assetPermission(kind)).id, assetRefs: [saved.asset_id],
    backendResult: {kind, version: saved.version}});
    return saved;
  }

  async createConnector(projectId, profile, options={}, context={}) {
    actor(context, 'ai.manage_connectors');
    const canonical = validateAIAsset('AIConnectorProfile', profile);
    const saved = await this.saveAsset(projectId, 'AIConnectorProfile', canonical,
      options, context);
    const runtime = this.connectors.create(canonical);
    return immutable({asset: saved, runtime});
  }

  async updateConnector(projectId, connectorId, profile, options={}, context={}) {
    actor(context, 'ai.manage_connectors');
    const canonical = validateAIAsset('AIConnectorProfile', profile);
    if(canonical.connectorId !== connectorId) throw new TypeError(
      'AI connector identity cannot change during update.'
    );
    const saved = await this.saveAsset(projectId, 'AIConnectorProfile', canonical,
      options, context);
    const runtime = await this.connectors.update(connectorId, canonical, context);
    return immutable({asset: saved, runtime});
  }

  async testModel(profile, context={}) {
    actor(context, 'ai.manage_model_profiles');
    if(!this.modelTester) throw new Error('No AI model test authority is configured.');
    const canonical = validateAIAsset('AIModelProfile', profile);
    const result = await this.modelTester(canonical, context);
    plainObject(result, 'AI model test result'); noRawSecrets(result,
      'AI model test result');
    return immutable({...result});
  }

  toolProposal(input, context={}) {
    actor(context, 'ai.delegate_draft');
    exact(input, ['commandId', 'arguments'], 'AI CDEadmin command proposal');
    if(!this.toolCatalog) throw new Error('AI Tool Catalog is unavailable.');
    const command = this.toolCatalog.commands.get(required(input.commandId,
      'AI proposed command'));
    if(command.aiExposure === 'hidden' || command.aiRiskClass === 'R7') throw new Error(
      'The requested CDEadmin command is not AI-eligible.'
    );
    noRawSecrets(input.arguments ?? {}, 'AI proposed command arguments');
    return immutable({schema: 'cdeadmin.ai-command-proposal.v1',
      commandId: command.id, arguments: immutable({...input.arguments}),
      riskClass: command.aiRiskClass, authority: command.authority});
  }

  executeTool(input, context={}) {
    actor(context, 'ai.delegate_write');
    exact(input, ['commandId', 'arguments', 'decision'],
      'AI CDEadmin command execution');
    if(!this.toolCatalog) throw new Error('AI Tool Catalog is unavailable.');
    return this.toolCatalog.invokeCommand(input.commandId, input.arguments,
      input.decision, context);
  }

  async invokeMCP(input, context={}) {
    actor(context, 'ai.delegate_read');
    exact(input, ['connectorId', 'toolId', 'arguments', 'approvalEvidence'],
      'AI MCP invocation');
    const prepared = await this.connectors.prepare(input.connectorId, {
      operationKind: 'tool', toolId: required(input.toolId, 'AI MCP tool ID'),
      arguments: input.arguments ?? {}});
    if(!['R0', 'R1'].includes(prepared.riskClass) || prepared.sideEffects === true) {
      throw new Error('Direct MCP invocation is restricted to prepared R0-R1 tools.');
    }
    return this.connectors.execute(input.connectorId, prepared.preparedId,
      input.approvalEvidence ?? null);
  }

  async execute(commandId, args={}, context={}) {
    args = adaptAIFormCommandArguments(commandId, args, context);
    const {screenId: _screenId, ...commandArgs} = args;
    args = commandArgs;
    const asset = (kind) => this.saveAsset(args.projectId, kind,
      args.content, args.options ?? {}, context);
    switch(commandId) {
    case 'ai.session.new': return this.newSession(args, context);
    case 'ai.session.archive': return this.archiveSession(args.sessionId, context);
    case 'ai.session.ask': return this.ask(args.sessionId,
      {prompt: args.prompt, retentionPolicy: args.retentionPolicy}, context);
    case 'ai.session.cancel': return this.cancelSession(args.sessionId,
      args.reason, context);
    case 'ai.context.add': return this.addContext(args.sessionId, args.context, context);
    case 'ai.context.remove': return this.removeContext(args.sessionId,
      args.reference, context);
    case 'ai.context.set_exposure': return this.setContextExposure(args.sessionId,
      args.exposure, context);
    case 'ai.plan.create': return this.plans.create(args.plan, context);
    case 'ai.plan.validate': return this.plans.validate(args.planId,
      {approvalPolicy: args.approvalPolicy, budgetPolicy: args.budgetPolicy,
        ...context});
    case 'ai.plan.approve': return this.plans.approve(args.planId,
      args.approval, context);
    case 'ai.plan.reject': return this.plans.reject(args.planId,
      args.reason, context);
    case 'ai.plan.execute': return this.planExecution.execute(args.planId,
      args.options ?? {}, context);
    case 'ai.connector.create': return this.createConnector(args.projectId,
      args.profile, args.options, context);
    case 'ai.connector.update': return this.updateConnector(args.projectId,
      args.connectorId, args.profile, args.options, context);
    case 'ai.connector.test': return this.connectors.test(args.connectorId, context);
    case 'ai.connector.enable': return this.connectors.enable(args.connectorId, context);
    case 'ai.connector.disable': return this.connectors.disable(args.connectorId);
    case 'ai.connector.revoke': return this.connectors.revoke(args.connectorId);
    case 'ai.connector.refresh_capabilities': return this.connectors
      .refreshCapabilities(args.connectorId);
    case 'ai.agent_profile.create': return asset('AIAgentProfile');
    case 'ai.agent_profile.update': return asset('AIAgentProfile');
    case 'ai.agent_profile.clone': return asset('AIAgentProfile');
    case 'ai.agent_profile.enable': return asset('AIAgentProfile');
    case 'ai.agent_profile.disable': return asset('AIAgentProfile');
    case 'ai.model_profile.create': return asset('AIModelProfile');
    case 'ai.model_profile.update': return asset('AIModelProfile');
    case 'ai.model_profile.test': return this.testModel(args.profile, context);
    case 'ai.model_profile.disable': return asset('AIModelProfile');
    case 'ai.tool_policy.update': return asset('AIToolPolicy');
    case 'ai.data_policy.update': return asset('AIDataPolicy');
    case 'ai.approval_policy.update': return asset('AIApprovalPolicy');
    case 'ai.budget_policy.update': return asset('AIBudgetPolicy');
    case 'ai.retention_policy.update': return asset('AIRetentionPolicy');
    case 'ai.query.compile': return this.queries.compile({...args.request,
      requestExplain: false}, context);
    case 'ai.query.explain': return this.queries.compile({...args.request,
      requestExplain: true}, context);
    case 'ai.query.execute_read': return this._executeQuery(args.queryId,
      'read_only', context);
    case 'ai.query.execute_mutation': return this._executeQuery(args.queryId,
      'mutation', context);
    case 'ai.cdeadmin.command.propose': return this.toolProposal(args, context);
    case 'ai.cdeadmin.command.execute': return this.executeTool(args, context);
    case 'ai.mcp.tools.refresh': return this.connectors.refreshCapabilities(
      args.connectorId);
    case 'ai.mcp.tool.invoke': return this.invokeMCP(args, context);
    case 'ai.audit.export': return this.audit.export(args, context);
    case 'ai.run.cancel': return this.planExecution.cancel(args.runId,
      args.reason, context) || this.backgroundRuns.cancel(args.runId,
      args.reason, context);
    case 'ai.emergency.disable_writes': return this.emergency.disableWrites(args, context);
    case 'ai.emergency.disable_all': return this.emergency.disableAll(args, context);
    case 'ai.emergency.invalidate_approvals': return this.emergency
      .invalidateApprovals(args, context);
    default: throw new Error(`Unsupported AI Interface command: ${commandId}`);
    }
  }

  _assetPermission(kind) {
    if(kind === 'AIModelProfile') return 'ai.manage_model_profiles';
    if(kind === 'AIConnectorProfile') return 'ai.manage_connectors';
    if(kind === 'AIAgentProfile') return 'ai.manage_agent_profiles';
    return 'ai.admin';
  }

  _session(sessionId) {
    const record = this.sessions.get(String(sessionId));
    if(!record) throw new Error(`Unknown AI session: ${sessionId}`);
    return record;
  }

  _owned(sessionId, context) {
    const record = this._session(sessionId); const current = actor(context,
      'ai.use');
    if(record.owner !== current.id && !current.permissions.includes('ai.admin')) {
      throw new Error('AI session belongs to another user.');
    }
    return record;
  }

  _contextItem(input) {
    const exposureLevel = String(input.exposureLevel ?? 'IDENTITY_ONLY');
    const classification = String(input.classification ?? 'INTERNAL');
    if(!['IDENTITY_ONLY', 'METADATA_SUMMARY', 'SCHEMA', 'STATISTICS',
      'SAFE_SAMPLE', 'FULL_ALLOWED_CONTENT'].includes(exposureLevel)) throw new TypeError(
      'AI context exposure level is invalid.'
    );
    if(!['PUBLIC', 'INTERNAL', 'CONFIDENTIAL', 'RESTRICTED', 'REGULATED',
      'SECRET_REFERENCE_ONLY'].includes(classification)) throw new TypeError(
      'AI context classification is invalid.'
    );
    return immutable({reference: required(input.reference, 'AI context reference'),
      exposureLevel, classification, source: String(input.source ?? ''),
      freshness: String(input.freshness ?? ''),
      sizeEstimate: Number.isFinite(input.sizeEstimate) ? input.sizeEstimate : null});
  }

  _touch(record) { record.updatedAt = new Date().toISOString(); }

  _finishTurn(record) {
    record.activeTaskRef = null;
    if(!record.archived) record.state = 'ready';
    this._touch(record);
  }

  async _runTurn(request, taskContext) {
    const record = this._session(request.sessionId);
    const result = await this.sessionResponder({session: sessionView(record),
      prompt: request.prompt, context: record.context, signal: taskContext.signal},
    taskContext.executionContext ?? {});
    plainObject(result, 'AI session response'); noRawSecrets(result,
      'AI session response');
    const response = immutable({...result});
    if(request.retentionPolicy.retainMessages) record.messages.push(
      immutable({role: 'user', text: request.prompt, at: new Date().toISOString()}),
      immutable({role: 'assistant', response, at: new Date().toISOString()}));
    this.audit.append({eventType: 'ai.session.response', sessionId: record.sessionId,
      initiator: record.owner, agentProfileRef: record.agentProfileRef,
      taskRefs: [request.id], content: request.retentionPolicy.retainPromptsInAudit ?
        {prompt: request.prompt} : null, backendResult: {status: 'success'}});
    return response;
  }

  _executeQuery(queryId, expected, context) {
    const review = this.queries.review(queryId);
    const actual = review.queryClassification === 'read_only' ? 'read_only' : 'mutation';
    if(actual !== expected) throw new Error(
      `AI query classification is ${review.queryClassification}, not ${expected}.`
    );
    return this.queries.execute(queryId, context);
  }
}
