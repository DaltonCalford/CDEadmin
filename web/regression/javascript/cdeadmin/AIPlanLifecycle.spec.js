/////////////////////////////////////////////////////////////
// CDEadmin AI planning, approval, execution and control tests.
/////////////////////////////////////////////////////////////

import {TaskExecutionService} from 'sources/cdeadmin_ui/platform/PlatformServices';
import {
  AIApprovalService, AIAuditService, AIBackgroundRunService, AIEmergencyControlService,
  AICompatibilityMigrationService, AIExecutionFailure, AIPlanExecutionService,
  AIPlanService, AIRetryPolicyService, aiPlanHash, sha256,
} from 'sources/cdeadmin_ui/modules/ai_interface';

const approvalPolicy = (rules={}) => ({schemaVersion: 1, policyId: 'approval-main',
  name: 'Approval policy', riskRules: {R0: 'auto', R1: 'auto', R2: 'review_diff',
    R3: 'review', R4: 'confirm_each', R5: 'typed_confirm', R6: 'dual_or_typed',
    ...rules}, expiryMinutes: {R4: 15, R5: 5, R6: 5}});

const plan = ({id='plan-main', status='draft', risk='R4', approval=true,
  kind='INVOKE_COMMAND', revision=1, baseContextRevision='context:7'}={}) => ({
  schemaVersion: 1, planId: id, revision, baseContextRevision,
  agentProfileRef: 'agent-main', modelProfileSnapshot: {profileId: 'model-main', revision: 3},
  connectorSnapshots: [{connectorId: 'connector-main', revision: 2,
    principalBinding: 'principal:ai'}], steps: [{
    stepId: 'step-1', kind, status: status === 'draft' ? 'pending' : 'validated',
    commandId: ['READ_METADATA', 'READ_DATA', 'WAIT_TASK'].includes(kind) ? null : 'provider.change',
    connectorRef: 'connector-main', resourceRefs: ['resource:orders'], assetRefs: [],
    arguments: kind === 'WAIT_TASK' ? {taskRef: 'dependency'} : {value: 1},
  }], risks: [risk], requiredApprovals: approval ? ['approval:step-1'] : [],
  validationChecks: ['check:step-1'], status,
});

const user = (id='human-one', permissions=['ai.use', 'ai.delegate_draft',
  'ai.delegate_write', 'ai.approve_high_risk', 'ai.approve_production',
  'ai.export_audit', 'ai.admin']) => ({currentUser: {id, permissions}});

const agentProfile = {schemaVersion: 1, profileId: 'agent-main', name: 'Main agent',
  description: 'Governed database assistant', enabled: true, autonomyMode: 'BOUNDED_EXECUTION',
  modelProfileRef: 'model-main', connectorRefs: ['connector-main'], toolPolicyRef: 'tool-main',
  dataPolicyRef: 'data-main', approvalPolicyRef: 'approval-main', budgetPolicyRef: 'budget-main',
  retentionPolicyRef: 'retention-main', instructionAssetRef: null, tags: []};
const toolPolicy = {schemaVersion: 1, policyId: 'tool-main', name: 'Bounded tools', moduleIds: [],
  commandIds: ['metadata.read'], deniedCommandIds: [], maxAutomaticRisk: 'R1',
  allowProjectDrafts: true, allowBackgroundTasks: true};
const budgetPolicy = {schemaVersion: 1, policyId: 'budget-main', name: 'Bounded budget',
  maxModelInputTokens: 1000, maxModelOutputTokens: 1000, maxToolCallsPerTurn: 2,
  maxToolCallsPerPlan: 3, maxDatabaseQueriesPerTurn: 2, maxDatabaseRowsPerQuery: 10,
  maxDatabaseBytesPerQuery: 10000, maxParallelTools: 1, maxRunMinutes: 10,
  maxBackgroundTasks: 1, maxEstimatedCostPerTurn: 1, maxEstimatedCostPerDay: 5};
const modelProfile = {schemaVersion: 1, profileId: 'model-main', displayName: 'Main model',
  runtimeClass: 'remote', endpointRef: 'endpoint:model', modelId: 'model-1',
  credentialRef: 'keyring:model', toolCallingSupport: true, structuredOutputSupport: true,
  contextLimit: 10000, outputLimit: 1000, streamingSupport: true, dataResidency: 'Canada',
  retentionStatement: 'No training.', approvedClassificationMax: 'INTERNAL', costModel: null,
  enabled: true};
const connectorProfile = {schemaVersion: 1, connectorId: 'connector-main', name: 'Database',
  connectorClass: 'database_provider', enabled: true, providerId: 'provider.firebird',
  connectionProfileRef: 'connection:one', dialectId: 'firebird', workareaSchemaRef: null,
  mcpEndpointRef: null, mcpProtocolProfile: null, principalBinding: 'principal:ai',
  credentialRef: 'keyring:database', policyRef: 'data-main', resourceScopeRefs:
    ['resource:orders'], state: 'ready', capabilitySnapshotRef: 'capability:one'};
const dataPolicy = {schemaVersion: 1, policyId: 'data-main', name: 'Internal data',
  defaultExposureLevel: 'METADATA_SUMMARY', maxClassification: 'INTERNAL',
  allowRemoteSamples: false, allowRemoteSource: false, sensitiveColumnHandling: 'excluded',
  maxSampleRows: 10, maxTextCharacters: 1000, allowedModelProfileRefs: ['model-main'],
  requireLocalForRestricted: true, queryPolicy: {allowRead: true, allowWrite: false,
    allowDDL: false, allowTransactionControl: false, allowExplain: true,
    allowSystemCatalog: true, allowCrossSurface: false, maxRows: 100,
    maxResultBytes: 10000, maxStatementSeconds: 30, maxStatementsPerPlan: 3,
    maxParallelQueries: 1, allowedResourceRefs: ['resource:orders'], blockedResourceRefs: [],
    allowTemporaryObjects: false, allowStoredProcedureCall: false,
    allowExternalSideEffectFunctions: false}};
const retentionPolicy = {schemaVersion: 1, policyId: 'retention-main', name: 'Retention',
  retainMessages: true, conversationDays: 30, retainToolResults: false, auditDays: 365,
  retainPromptsInAudit: false};

const validation = ({risk='R4', approval=true, retry={kind: 'mutation',
  providerReadIdempotent: false, explicitlyIdempotent: false,
  backendIdempotencyRef: null}, expiresAt='2099-01-01T00:00:00.000Z'}={}) => async (
  step, {plan: currentPlan}) => ({valid: true,
  checkId: 'check:step-1', reason: '', evidenceRef: 'evidence:validation', riskClass: risk,
  approvalRequired: approval, approvalBinding: approval ? {requirementId: 'approval:step-1',
    stepIds: [step.stepId], connectorRef: 'connector-main', principalRef: 'principal:ai',
    resourceRefs: ['resource:orders'], environment: 'development', riskClass: risk} : null,
  preparedOperation: ['WAIT_TASK', 'VALIDATE', 'REQUEST_APPROVAL'].includes(step.kind) ? null : {
    preparedId: `prepared:${step.stepId}`, connectorSnapshot: currentPlan.connectorSnapshots[0],
    operationKind: {READ_METADATA: 'metadata_read', READ_DATA: 'data_read',
      COMPILE_QUERY: 'query_compile', EXECUTE_QUERY: risk === 'R1' ? 'query_read' :
        'query_mutation', CREATE_ASSET_DRAFT: 'project_draft', EDIT_ASSET: 'project_draft',
      INVOKE_COMMAND: 'command', START_TASK: 'task'}[step.kind],
    canonicalResourceRefs: step.resourceRefs, accessSurfaceRefs: [],
    normalizedArguments: step.arguments, nativeCompiledArtifact: null, riskClass: risk,
    estimatedEffects: [], budgets: {}, validation: [{checkId: 'check:step-1', passed: true}],
    expiresAt}, retry});

function clock() {
  let value = Date.parse('2026-09-12T12:00:00.000Z');
  return {now: () => new Date(value).toISOString(), advance: (milliseconds) => { value += milliseconds; }};
}

function planServices(options={}) {
  const time = options.time ?? clock(); const audit = options.audit ?? new AIAuditService({now: time.now});
  const emergency = options.emergency ?? new AIEmergencyControlService({audit, now: time.now});
  const approvals = options.approvals ?? new AIApprovalService({now: time.now,
    approvalEpoch: () => emergency.approvalEpoch(), audit});
  const plans = new AIPlanService({approvals, audit, now: time.now,
    contextRevision: options.contextRevision ?? (async () => 'context:7'),
    validateStep: options.validateStep ?? validation()});
  return {time, audit, emergency, approvals, plans};
}

describe('AI lifecycle hashing and approval evidence', () => {
  it('uses real SHA-256 and excludes runtime-only plan/step states from approval binding', () => {
    expect(sha256('abc')).toBe('ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad');
    const ready = plan({status: 'ready_for_approval'});
    expect(aiPlanHash(ready)).toBe(aiPlanHash({...ready, status: 'approved',
      steps: ready.steps.map((step) => ({...step, status: 'running'}))}));
    expect(aiPlanHash(ready)).not.toBe(aiPlanHash({...ready, revision: 2}));
  });

  it('binds R5 typed approval to exact revision, steps, principal, resources and environment', () => {
    const time = clock(); let epoch = 0; const approvals = new AIApprovalService({now: time.now,
      approvalEpoch: () => epoch}); const value = plan({status: 'ready_for_approval', risk: 'R5'});
    const target = {requirementId: 'approval:step-1', stepIds: ['step-1'],
      connectorRef: 'connector-main', principalRef: 'principal:ai',
      resourceRefs: ['resource:orders'], environment: 'development', riskClass: 'R5'};
    expect(() => approvals.issue({plan: value, approvalPolicy: approvalPolicy(),
      expectedBinding: target, method: 'typed_confirm', confirmationText: 'wrong'}, user()))
      .toThrow('does not match');
    const evidence = approvals.issue({plan: value, approvalPolicy: approvalPolicy(),
      expectedBinding: target, method: 'typed_confirm', confirmationText:
      approvals.confirmationPhrase(value, target)}, user());
    expect(approvals.validate(evidence, {plan: value, expectedBinding: target,
      approvalPolicy: approvalPolicy()})).toBe(evidence);
    expect(() => approvals.validate(evidence, {plan: value, expectedBinding: {...target,
      environment: 'production'}, approvalPolicy: approvalPolicy()})).toThrow('bound to another');
    expect(() => approvals.issue({plan: value, approvalPolicy: approvalPolicy(),
      expectedBinding: {...target, resourceRefs: ['resource:other']}, method: 'typed_confirm',
      confirmationText: approvals.confirmationPhrase(value, {...target,
        resourceRefs: ['resource:other']})}, user())).toThrow('do not exactly match');
    epoch++;
    expect(() => approvals.validate(evidence, {plan: value, expectedBinding: target,
      approvalPolicy: approvalPolicy()})).toThrow('expired, revoked, changed');
  });

  it('requires distinct human approvers for dual approval and rejects model self-approval', () => {
    const approvals = new AIApprovalService(); const value = plan({status: 'ready_for_approval'});
    const policy = approvalPolicy({R4: 'dual_approval'}); const target = {
      requirementId: 'approval:step-1', stepIds: ['step-1'], connectorRef: 'connector-main',
      principalRef: 'principal:ai', resourceRefs: ['resource:orders'],
      environment: 'development', riskClass: 'R4'};
    expect(() => approvals.issue({plan: value, approvalPolicy: policy,
      expectedBinding: target, method: 'dual_approval'}, {...user('model'), modelActorId: 'model'}))
      .toThrow('cannot perform');
    approvals.issue({plan: value, approvalPolicy: policy, expectedBinding: target,
      method: 'dual_approval'}, user('human-one'));
    approvals.issue({plan: value, approvalPolicy: policy, expectedBinding: target,
      method: 'dual_approval'}, user('human-one'));
    expect(approvals.coverage({plan: value, requirements: [target],
      approvalPolicy: policy}).complete).toBe(false);
    approvals.issue({plan: value, approvalPolicy: policy, expectedBinding: target,
      method: 'dual_approval'}, user('human-two'));
    expect(approvals.coverage({plan: value, requirements: [target],
      approvalPolicy: policy}).complete).toBe(true);
  });

  it('expires evidence and never permits a forbidden policy rule', () => {
    const time = clock(); const approvals = new AIApprovalService({now: time.now});
    const value = plan({status: 'ready_for_approval'}); const target = {
      requirementId: 'approval:step-1', stepIds: ['step-1'], connectorRef: 'connector-main',
      principalRef: 'principal:ai', resourceRefs: ['resource:orders'],
      environment: 'development', riskClass: 'R4'};
    const evidence = approvals.issue({plan: value, approvalPolicy: approvalPolicy(),
      expectedBinding: target, method: 'confirm_each'}, user());
    time.advance(16 * 60000);
    expect(() => approvals.validate(evidence, {plan: value, expectedBinding: target,
      approvalPolicy: approvalPolicy()})).toThrow('expired');
    expect(() => approvals.issue({plan: value, approvalPolicy: approvalPolicy({R4: 'forbid'}),
      expectedBinding: target, method: 'confirm_each'}, user())).toThrow('forbids');
  });
});

describe('AI emergency and audit authorities', () => {
  it('blocks writes/all AI, invalidates approvals and preserves emergency audit evidence', () => {
    const audit = new AIAuditService(); const emergency = new AIEmergencyControlService({audit});
    const operation = {commandId: 'provider.update', riskClass: 'R4'};
    expect(emergency.authorizationCheck({operation}).allowed).toBe(true);
    emergency.disableWrites({reason: 'Incident response'}, user());
    expect(emergency.authorizationCheck({operation}).reason).toContain('writes are disabled');
    expect(emergency.approvalEpoch()).toBe(1);
    emergency.disableAll({reason: 'Containment'}, user());
    expect(emergency.authorizationCheck({operation}).reason).toContain('module is disabled');
    expect(audit.list().map((item) => item.eventType)).toEqual([
      'ai.emergency.disable_writes', 'ai.emergency.disable_all']);
  });

  it('revokes scoped authorities, clears catalogs, forces reauthorization and cancels runs', () => {
    const emergency = new AIEmergencyControlService(); const cancel = jest.fn();
    emergency.registerRun('run-one', cancel, ['agent-one']);
    emergency.killRuns({scope: ['agent-one'], reason: 'Stop run'}, user());
    expect(cancel).toHaveBeenCalledWith('Stop run');
    emergency.setModelsDisabled(['model-one'], true, 'Model issue', user());
    expect(emergency.authorizationCheck({operation: {riskClass: 'R0'}, context:
      {modelProfile: {profileId: 'model-one'}}}).allowed).toBe(false);
    const clear = jest.fn(); emergency.clearToolCatalogs(clear, 'Refresh required', user());
    expect(clear).toHaveBeenCalled();
    const before = emergency.snapshot().reauthorizationRevision;
    emergency.forceReauthorization('Credential event', user());
    expect(emergency.snapshot().reauthorizationRevision).toBe(before + 1);
  });

  it('redacts structured secret fields and separates retained content from metadata', () => {
    const audit = new AIAuditService(); const record = audit.append({eventType: 'ai.test',
      initiator: 'human', redactedArguments: {password: 'raw', nested: {token: 'raw'}},
      backendResult: {status: 'ok', secretValue: 'raw'}, content: {prompt: 'Explain orders'}});
    expect(record.redactedArguments).toEqual({password: '[REDACTED]',
      nested: {token: '[REDACTED]'}});
    expect(audit.get(record.auditId).content).toBeUndefined();
    expect(audit.get(record.auditId, {includeContent: true}).content)
      .toEqual({prompt: 'Explain orders'});
    expect(JSON.stringify(record)).not.toContain('Explain orders');
  });

  it('requires elevated policy for content export and applies independent retention', () => {
    const time = clock(); const audit = new AIAuditService({now: time.now});
    audit.append({eventType: 'ai.test', initiator: 'human', connectorRef: 'connector-main',
      content: {prompt: 'Explain orders'}}); time.advance(2 * 86400000);
    const request = {from: '2026-09-01T00:00:00.000Z', to: time.now(), profiles: [],
      connectors: ['connector-main'], includeMessageText: true, format: 'json',
      retentionPolicy: {retainPromptsInAudit: true}};
    expect(() => audit.export(request, user('auditor', ['ai.export_audit']))).toThrow(
      'administration permission');
    expect(audit.export(request, user()).recordCount).toBe(1);
    expect(audit.applyRetention({retainMessages: true, conversationDays: 1, auditDays: 30},
      {at: time.now()})).toEqual({metadataRemoved: 0, contentRemaining: 0,
      metadataRemaining: 1});
    expect(audit.list({includeContent: true})[0].content).toBeNull();
  });
});

describe('AI retry policy', () => {
  it('retries only transient model failures before consequential actions', async () => {
    const retry = new AIRetryPolicyService({modelMaximum: 2}); let calls = 0;
    const operation = async () => { calls++; if(calls < 3) throw new AIExecutionFailure(
      'MODEL_PROVIDER_FAILURE', 'temporary', {retryable: true}); return 'done'; };
    await expect(retry.run('model', operation)).resolves.toBe('done'); expect(calls).toBe(3);
    calls = 0;
    await expect(retry.run('model', operation, {consequentialActionSinceCheckpoint: true}))
      .rejects.toThrow('temporary'); expect(calls).toBe(1);
  });

  it('requires provider read idempotency and explicit mutation idempotency', async () => {
    const retry = new AIRetryPolicyService({readMaximum: 1, mutationMaximum: 1});
    let reads = 0; const read = async () => { reads++; if(reads === 1) throw new AIExecutionFailure(
      'CONNECTOR_TRANSPORT_FAILURE', 'again', {retryable: true}); return 'rows'; };
    await expect(retry.run('read', read, {providerReadIdempotent: true})).resolves.toBe('rows');
    let writes = 0; const mutation = async () => { writes++; if(writes === 1)
      throw new AIExecutionFailure('PROVIDER_ERROR', 'again', {retryable: true}); return 'changed'; };
    await expect(retry.run('mutation', mutation)).rejects.toThrow('again');
    expect(writes).toBe(1); writes = 0;
    await expect(retry.run('mutation', mutation, {backendIdempotencyRef: 'backend:token-ref'}))
      .resolves.toBe('changed'); expect(writes).toBe(2);
  });
});

describe('bounded AI background runs', () => {
  it('runs through TaskService within explicit duration/turn budgets', async () => {
    const tasks = new TaskExecutionService(); const turn = jest.fn()
      .mockResolvedValueOnce({status: 'continue', resultRefs: ['result:one'], taskRefs: [],
        nextState: {cursor: 1}, diagnostics: []})
      .mockResolvedValueOnce({status: 'completed', resultRefs: ['result:two'], taskRefs: [],
        nextState: null, diagnostics: []});
    const runs = new AIBackgroundRunService({tasks, runTurn: turn});
    const run = runs.start({agentProfile, toolPolicy, budgetPolicy, goal: 'Review schema health',
      contextRefs: ['resource:orders'], maxMinutes: 5, stopOnApproval: true}, user());
    await expect(tasks.wait(run.taskRef)).resolves.toEqual(expect.objectContaining({
      status: 'success', turns: 2, resultRefs: ['result:one', 'result:two']}));
    expect(turn).toHaveBeenNthCalledWith(2, expect.objectContaining({priorState: {cursor: 1},
      maximumTurns: 3}), expect.any(Object)); runs.dispose();
  });

  it('stops for approval without self-approving and enforces policy/time budgets', async () => {
    const tasks = new TaskExecutionService(); const runs = new AIBackgroundRunService({tasks,
      runTurn: async () => ({status: 'approval_required', resultRefs: [], taskRefs: [],
        nextState: null, diagnostics: [{code: 'approval_required'}]})});
    const run = runs.start({agentProfile, toolPolicy, budgetPolicy, goal: 'Prepare a change',
      contextRefs: [], maxMinutes: 5, stopOnApproval: false}, user());
    await expect(tasks.wait(run.taskRef)).resolves.toEqual(expect.objectContaining({
      status: 'approval_required', stoppedForApproval: true, turns: 1}));
    expect(() => runs.start({agentProfile, toolPolicy: {...toolPolicy,
      allowBackgroundTasks: false}, budgetPolicy, goal: 'No', contextRefs: [], maxMinutes: 5,
    stopOnApproval: true}, user())).toThrow('does not allow');
    expect(() => runs.start({agentProfile, toolPolicy, budgetPolicy, goal: 'Too long',
      contextRefs: [], maxMinutes: 11, stopOnApproval: true}, user())).toThrow('duration exceeds');
    runs.dispose();
  });

  it('fails at a hard turn ceiling instead of creating an unbounded agent', async () => {
    const tasks = new TaskExecutionService(); const runs = new AIBackgroundRunService({tasks,
      siteMaxTurns: 2, runTurn: async () => ({status: 'continue', resultRefs: [], taskRefs: [],
        nextState: {}, diagnostics: []})});
    const run = runs.start({agentProfile, toolPolicy, budgetPolicy, goal: 'Never ending',
      contextRefs: [], maxMinutes: 5, stopOnApproval: true}, user());
    await expect(tasks.wait(run.taskRef)).rejects.toMatchObject({category: 'BUDGET_EXCEEDED'});
    expect(tasks.view(run.taskRef).retry.maximum).toBe(0); runs.dispose();
  });

  it('enforces the background-task concurrency budget', async () => {
    let release; const hold = new Promise((resolve) => { release = resolve; });
    const tasks = new TaskExecutionService(); const runs = new AIBackgroundRunService({tasks,
      runTurn: async () => { await hold; return {status: 'completed', resultRefs: [], taskRefs: [],
        nextState: null, diagnostics: []}; }});
    const request = {agentProfile, toolPolicy, budgetPolicy, goal: 'One active run',
      contextRefs: [], maxMinutes: 5, stopOnApproval: true};
    const first = runs.start(request, user());
    expect(() => runs.start({...request, goal: 'Second active run'}, user())).toThrow(
      'concurrency exceeds'); release(); await tasks.wait(first.taskRef); runs.dispose();
  });
});

describe('precursor AI compatibility migration', () => {
  function migrationInput() {
    return {legacyContent: {name: 'Legacy assistant', description: 'Precursor asset',
      sessionPolicy: {id: 'legacy-policy', allowedModes: ['ask', 'plan'], defaultMode: 'ask',
        allowedReadToolIds: ['metadata.read'], allowedProposalCommandIds: [],
        maximumPlanSteps: 3, requireEvidence: true, nativeDetails: {}}, savedContextRefs: [],
      savedPlans: [], modelProfileRef: null, conversationPersistencePolicy: {
        persistMessages: true, storePrompts: false, storeResponses: true, retentionDays: 30,
        nativeDetails: {}}, extensions: []}, modelProfile,
    connectorProfiles: [connectorProfile], agentProfile, toolPolicy, dataPolicy,
    approvalPolicy: approvalPolicy(), budgetPolicy, retentionPolicy,
    instructionAsset: null, plans: []};
  }

  it('validates all independent target assets and preserves the precursor asset', async () => {
    const assets = {save: jest.fn(async (_projectId, kind, content) => ({
      asset_id: `${kind}:saved`, assetKind: kind, content, version: 1})),
    remove: jest.fn(async () => ({}))};
    const migrations = new AICompatibilityMigrationService({assets});
    const prepared = migrations.prepare(migrationInput());
    expect(prepared).toEqual(expect.objectContaining({sourceSchema: 'cdeadmin.ai.asset.v1',
      legacyPreserved: true, validation: expect.objectContaining({valid: true,
        connectorCount: 1, planCount: 0})}));
    const result = await migrations.migrate('project-one', migrationInput(), user('migration-user',
      ['ai.manage_agent_profiles', 'ai.manage_connectors', 'ai.manage_model_profiles',
        'ai.delegate_draft']));
    expect(result.legacyPreserved).toBe(true); expect(result.assets).toHaveLength(8);
    expect(assets.save).toHaveBeenCalledTimes(8); expect(assets.remove).not.toHaveBeenCalled();
  });

  it('rejects incomplete mappings before writes and compensates every created asset on failure', async () => {
    const input = migrationInput(); input.agentProfile = {...input.agentProfile,
      modelProfileRef: 'different-model'};
    const assets = {save: jest.fn(), remove: jest.fn()};
    const migrations = new AICompatibilityMigrationService({assets});
    expect(() => migrations.prepare(input)).toThrow('references do not resolve');
    expect(assets.save).not.toHaveBeenCalled();
    let call = 0; assets.save.mockImplementation(async (_projectId, kind, content) => {
      call++; if(call === 3) throw new Error('storage conflict');
      return {asset_id: `${kind}:${call}`, assetKind: kind, content, version: 1};
    }); assets.remove.mockResolvedValue({});
    await expect(migrations.migrate('project-one', migrationInput(), user('migration-user',
      ['ai.manage_agent_profiles', 'ai.manage_connectors', 'ai.manage_model_profiles',
        'ai.delegate_draft']))).rejects.toThrow('created assets were rolled back');
    expect(assets.remove).toHaveBeenCalledTimes(2);
  });
});

describe('AI plan lifecycle and TaskService execution', () => {
  it('validates, approves and executes an exact plan through the shared Task service', async () => {
    const services = planServices(); services.plans.create(plan(), user());
    await services.plans.validate('plan-main', {approvalPolicy: approvalPolicy(), ...user()});
    expect(services.plans.get('plan-main').plan.status).toBe('ready_for_approval');
    const approved = services.plans.approve('plan-main', {requirementId: 'approval:step-1',
      method: 'confirm_each'}, user());
    expect(services.plans.get('plan-main').plan.status).toBe('approved');
    const lifecycle = services.plans.get('plan-main'); const operation = {operationId: 'step-1',
      commandId: 'provider.change', normalizedArguments: {value: 1},
      resourceRefs: ['resource:orders'], riskClass: 'R4', phase: 'plan', plan: lifecycle,
      approvalEvidence: [approved.evidence]};
    expect(services.plans.authorizationCheck({operation}).allowed).toBe(true);
    expect(services.approvals.authorizationCheck({operation,
      approvalPolicy: approvalPolicy()}).allowed).toBe(true);
    expect(services.approvals.authorizationCheck({operation: {...operation,
      approvalEvidence: []}, approvalPolicy: approvalPolicy()}).allowed).toBe(false);
    const tasks = new TaskExecutionService({now: services.time.now});
    const executeStep = jest.fn(async () => ({status: 'success', result: {changed: 1},
      diagnostics: [], resourceRefs: ['resource:orders'], assetRefs: [], taskRefs: [],
      nextAllowedActions: [], auditRef: null}));
    const executor = new AIPlanExecutionService({plans: services.plans, tasks, executeStep,
      emergency: services.emergency, audit: services.audit, now: services.time.now});
    const run = await executor.execute('plan-main', {}, user());
    await expect(tasks.wait(run.taskRef)).resolves.toEqual(expect.objectContaining({status: 'success'}));
    expect(services.plans.get('plan-main').plan.status).toBe('succeeded');
    expect(executeStep).toHaveBeenCalledWith(expect.objectContaining({approvalEvidence:
      [expect.objectContaining({approvalId: 'ai-approval-1'})]}), expect.any(Object));
    expect(tasks.view(run.taskRef).retry.maximum).toBe(0); executor.dispose();
  });

  it('marks stale context before execution and invalidates revision-bound evidence', async () => {
    let contextRevision = 'context:7'; const services = planServices({contextRevision:
      async () => contextRevision}); services.plans.create(plan(), user());
    await services.plans.validate('plan-main', {approvalPolicy: approvalPolicy(), ...user()});
    services.plans.approve('plan-main', {requirementId: 'approval:step-1',
      method: 'confirm_each'}, user()); contextRevision = 'context:8';
    await expect(services.plans.executionSnapshot('plan-main', user())).rejects.toThrow(
      'context changed');
    expect(services.plans.get('plan-main').plan.status).toBe('stale');
  });

  it('refuses a prepared operation that expires between approval and execution', async () => {
    const time = clock(); const services = planServices({time, validateStep: validation({
      expiresAt: '2026-09-12T12:01:00.000Z'})}); services.plans.create(plan(), user());
    await services.plans.validate('plan-main', {approvalPolicy: approvalPolicy(), ...user()});
    services.plans.approve('plan-main', {requirementId: 'approval:step-1',
      method: 'confirm_each'}, user()); time.advance(61000);
    await expect(services.plans.executionSnapshot('plan-main', user())).rejects.toThrow(
      'prepared operation expired');
    expect(services.plans.get('plan-main').plan.status).toBe('stale');
  });

  it('requires an exact next revision and revokes all evidence when content changes', async () => {
    const services = planServices(); services.plans.create(plan(), user());
    await services.plans.validate('plan-main', {approvalPolicy: approvalPolicy(), ...user()});
    const approved = services.plans.approve('plan-main', {requirementId: 'approval:step-1',
      method: 'confirm_each'}, user()).evidence;
    expect(() => services.plans.revise('plan-main', 2, plan({revision: 3}), user()))
      .toThrow('does not continue');
    const replacement = plan({revision: 2}); replacement.steps[0].arguments = {value: 2};
    services.plans.revise('plan-main', 1, replacement, user());
    expect(services.plans.get('plan-main')).toEqual(expect.objectContaining({
      plan: expect.objectContaining({revision: 2, status: 'draft'}), validations: []}));
    expect(() => services.approvals.get(approved.approvalId)).not.toThrow();
    expect(services.approvals.get(approved.approvalId).revoked).toBe(true);
  });

  it('refuses understated risk/check/approval claims and connector snapshot drift', async () => {
    const services = planServices(); const understated = plan(); understated.risks = ['R1'];
    services.plans.create(understated, user());
    await services.plans.validate('plan-main', {approvalPolicy: approvalPolicy(), ...user()});
    expect(services.plans.get('plan-main')).toEqual(expect.objectContaining({
      plan: expect.objectContaining({status: 'invalid'}),
      validations: expect.arrayContaining([expect.objectContaining({valid: false,
        reason: expect.stringContaining('absent from the reviewed plan')})])}));
    const other = plan({id: 'missing-connector'}); other.steps[0].connectorRef = 'not-snapshotted';
    services.plans.create(other, user());
    await services.plans.validate('missing-connector', {approvalPolicy: approvalPolicy(), ...user()});
    expect(services.plans.get('missing-connector').validations[0].reason)
      .toContain('Connector snapshot is absent');
    const rawSecret = plan({id: 'raw-secret', risk: 'R7', approval: false});
    const forbidden = planServices({validateStep: validation({risk: 'R7', approval: false})});
    forbidden.plans.create(rawSecret, user());
    await forbidden.plans.validate('raw-secret', {approvalPolicy: approvalPolicy(), ...user()});
    expect(forbidden.plans.get('raw-secret').validations[0].reason).toContain('R7 raw-secret');
    const validStep = validation(); const tampered = planServices({validateStep: async (...args) => {
      const result = await validStep(...args); return {...result, preparedOperation:
        {...result.preparedOperation, normalizedArguments: {value: 999}}}; }});
    tampered.plans.create(plan({id: 'tampered'}), user());
    await tampered.plans.validate('tampered', {approvalPolicy: approvalPolicy(), ...user()});
    expect(tampered.plans.get('tampered').validations[0].reason)
      .toContain('does not exactly match');
  });

  it('never lets generic TaskService retry a mutation and records typed provider failure', async () => {
    const services = planServices(); services.plans.create(plan(), user());
    await services.plans.validate('plan-main', {approvalPolicy: approvalPolicy(), ...user()});
    services.plans.approve('plan-main', {requirementId: 'approval:step-1',
      method: 'confirm_each'}, user()); const tasks = new TaskExecutionService(); let calls = 0;
    const executor = new AIPlanExecutionService({plans: services.plans, tasks,
      executeStep: async () => { calls++; throw new AIExecutionFailure('PROVIDER_ERROR',
        'backend failed', {retryable: true}); }, audit: services.audit});
    const run = await executor.execute('plan-main', {}, user());
    await expect(tasks.wait(run.taskRef)).rejects.toMatchObject({category: 'PROVIDER_ERROR'});
    expect(calls).toBe(1); expect(tasks.view(run.taskRef).retry.maximum).toBe(0);
    expect(services.plans.get('plan-main').plan.status).toBe('failed'); executor.dispose();
  });

  it('keeps cancel requested visible until cooperative cancellation is confirmed', async () => {
    let release; const hold = new Promise((resolve) => { release = resolve; });
    const services = planServices({validateStep: validation({risk: 'R1', approval: false,
      retry: {kind: 'read', providerReadIdempotent: true, explicitlyIdempotent: false,
        backendIdempotencyRef: null}})}); services.plans.create(plan({risk: 'R1',
      approval: false, kind: 'READ_DATA'}), user());
    await services.plans.validate('plan-main', {approvalPolicy: approvalPolicy(), ...user()});
    const tasks = new TaskExecutionService(); const cancelStep = jest.fn(async () => true);
    const executor = new AIPlanExecutionService({plans: services.plans, tasks, cancelStep,
      executeStep: async ({signal}) => { await hold; if(signal.aborted) throw new Error('cancelled');
        return {status: 'success', result: {}, diagnostics: [], resourceRefs: [], assetRefs: [],
          taskRefs: [], nextAllowedActions: [], auditRef: null}; }});
    const run = await executor.execute('plan-main', {}, user());
    expect(executor.cancel(run.runId, 'User requested cancellation', user())).toBe(true);
    expect(tasks.view(run.taskRef).state).toBe('cancel_requested'); release();
    await expect(tasks.wait(run.taskRef)).rejects.toMatchObject({name: 'AbortError'});
    expect(tasks.view(run.taskRef).state).toBe('cancelled'); expect(cancelStep).toHaveBeenCalled();
    expect(services.plans.get('plan-main').plan.status).toBe('cancelled'); executor.dispose();
  });

  it('preserves partial as a distinct terminal failure category', async () => {
    const services = planServices({validateStep: validation({risk: 'R1', approval: false,
      retry: {kind: 'read', providerReadIdempotent: true, explicitlyIdempotent: false,
        backendIdempotencyRef: null}})}); services.plans.create(plan({risk: 'R1',
      approval: false, kind: 'READ_DATA'}), user());
    await services.plans.validate('plan-main', {approvalPolicy: approvalPolicy(), ...user()});
    const tasks = new TaskExecutionService(); const executor = new AIPlanExecutionService({
      plans: services.plans, tasks, executeStep: async () => ({status: 'partial', result: {},
        diagnostics: [{code: 'truncated'}], resourceRefs: [], assetRefs: [], taskRefs: [],
        nextAllowedActions: [], auditRef: null})});
    const run = await executor.execute('plan-main', {}, user());
    await expect(tasks.wait(run.taskRef)).rejects.toMatchObject({category: 'PARTIAL_RESULT'});
    expect(services.plans.get('plan-main').plan.status).toBe('failed'); executor.dispose();
  });
});
