import {CommandRegistry} from 'sources/cdeadmin_ui/commands/CommandRegistry';
import {
  AI_AUTHORIZATION_CHECKS, AIAuthorizationService,
} from 'sources/cdeadmin_ui/modules/ai_interface';

const success = () => ({allowed: true, reason: '', evidenceRef: 'evidence:ok'});
const AUTHORITY_NAMES = ['emergencyState', 'modelHealth', 'connectorHealth', 'delegation',
  'connectorPolicy', 'environmentPolicy', 'databasePrecheck', 'dataEgress', 'budget',
  'planValidation', 'approvalValidation', 'backendAuthorization'];
function policies() {
  const queryPolicy = {allowRead: true, allowWrite: true, allowDDL: false,
    allowTransactionControl: false, allowExplain: true, allowSystemCatalog: true,
    allowCrossSurface: false, maxRows: 1000, maxResultBytes: 10485760,
    maxStatementSeconds: 30, maxStatementsPerPlan: 10, maxParallelQueries: 4,
    allowedResourceRefs: ['resource:orders'], blockedResourceRefs: [],
    allowTemporaryObjects: false, allowStoredProcedureCall: false,
    allowExternalSideEffectFunctions: false};
  return {
    agentProfile: {schemaVersion: 1, profileId: 'agent-main', name: 'Agent', description: null,
      enabled: true, autonomyMode: 'BOUNDED_EXECUTION', modelProfileRef: 'model-main',
      connectorRefs: ['connector-main'], toolPolicyRef: 'tool-main', dataPolicyRef: 'data-main',
      approvalPolicyRef: 'approval-main', budgetPolicyRef: 'budget-main',
      retentionPolicyRef: 'retention-main', instructionAssetRef: null, tags: []},
    modelProfile: {schemaVersion: 1, profileId: 'model-main', displayName: 'Model',
      runtimeClass: 'remote', endpointRef: 'endpoint:model', modelId: 'model-1',
      credentialRef: 'keyring:model', toolCallingSupport: true, structuredOutputSupport: true,
      contextLimit: 64000, outputLimit: 8192, streamingSupport: true, dataResidency: 'Canada',
      retentionStatement: 'No training.', approvedClassificationMax: 'INTERNAL', costModel: null,
      enabled: true},
    connectorProfile: {schemaVersion: 1, connectorId: 'connector-main', name: 'Database',
      connectorClass: 'database_provider', enabled: true, providerId: 'provider.firebird',
      connectionProfileRef: 'connection:main', dialectId: 'firebird', workareaSchemaRef: null,
      mcpEndpointRef: null, mcpProtocolProfile: null, principalBinding: 'cdeadmin-ai',
      credentialRef: 'keyring:database', policyRef: 'data-main',
      resourceScopeRefs: ['resource:orders'], state: 'ready', capabilitySnapshotRef: 'capability:1'},
    toolPolicy: {schemaVersion: 1, policyId: 'tool-main', name: 'Tools',
      moduleIds: ['cdeadmin.ai_interface'], commandIds: [], deniedCommandIds: [],
      maxAutomaticRisk: 'R1', allowProjectDrafts: true, allowBackgroundTasks: true},
    dataPolicy: {schemaVersion: 1, policyId: 'data-main', name: 'Data',
      defaultExposureLevel: 'METADATA_SUMMARY', maxClassification: 'INTERNAL',
      allowRemoteSamples: false, allowRemoteSource: false, sensitiveColumnHandling: 'excluded',
      maxSampleRows: 20, maxTextCharacters: 2000, allowedModelProfileRefs: ['model-main'],
      requireLocalForRestricted: true, queryPolicy},
    budgetPolicy: {schemaVersion: 1, policyId: 'budget-main', name: 'Budget',
      maxModelInputTokens: 64000, maxModelOutputTokens: 8192, maxToolCallsPerTurn: 20,
      maxToolCallsPerPlan: 100, maxDatabaseQueriesPerTurn: 10, maxDatabaseRowsPerQuery: 1000,
      maxDatabaseBytesPerQuery: 10485760, maxParallelTools: 4, maxRunMinutes: 30,
      maxBackgroundTasks: 2, maxEstimatedCostPerTurn: 2, maxEstimatedCostPerDay: 10},
    approvalPolicy: {schemaVersion: 1, policyId: 'approval-main', name: 'Approval',
      riskRules: {R0: 'auto', R1: 'auto', R2: 'review_diff', R3: 'review',
        R4: 'confirm_each', R5: 'typed_confirm', R6: 'dual_or_typed'},
      expiryMinutes: {R4: 15, R5: 5, R6: 5}},
  };
}
function command(registry, id, risk='R1', exposure='read_only', extra={}) {
  registry.register({id, description: `Execute ${id}.`, aiExposure: exposure,
    aiRiskClass: risk, aiModuleId: 'cdeadmin.ai_interface',
    aiArgumentSchema: {type: 'object', additionalProperties: false},
    aiResultSchema: {type: 'object'}, execute: jest.fn(async () => ({ok: true})), ...extra});
}
function fixture(overrides={}) {
  const commands = new CommandRegistry();
  command(commands, 'ai.query.compile', 'R0'); command(commands, 'ai.query.execute_read', 'R1');
  command(commands, 'ai.query.execute_mutation', 'R4', 'executable');
  command(commands, 'ai.security.rotate', 'R5', 'executable');
  command(commands, 'ai.production.cutover', 'R6', 'executable');
  command(commands, 'ai.publish.prepare', 'R3', 'executable');
  command(commands, 'ai.secret.export', 'R7', 'executable');
  command(commands, 'ai.asset.draft', 'R2', 'draft_only');
  command(commands, 'ai.metadata.task', 'R0', 'read_only', {createsTask: 'ai.metadata.read'});
  commands.register({id: 'ai.legacy.eligible', aiEligible: true, execute: jest.fn()});
  const authorities = Object.fromEntries(AUTHORITY_NAMES.map((name) => [name, jest.fn(success)]));
  Object.assign(authorities, overrides);
  return {commands, authorities, service: new AIAuthorizationService({commands, ...authorities,
    now: () => '2026-09-12T12:00:00.000Z'})};
}
function context(extra={}) { return {...policies(), currentUser: {id: 'user-one', permissions: [
  'ai.use', 'ai.delegate_read', 'ai.delegate_draft', 'ai.delegate_write',
  'ai.approve_high_risk', 'ai.approve_production', 'ai.admin'], security_groups: []}, ...extra}; }
function operation(extra={}) { return {operationId: 'operation-one', commandId: 'ai.query.execute_read',
  operationKind: 'query_read', resourceRefs: ['resource:orders'], accessSurfaceRefs: [],
  environment: 'development', modelVisibleContent: null,
  estimatedBudget: {databaseQueriesPerTurn: 1, databaseRowsPerQuery: 100},
  normalizedArguments: {queryId: 'query-one', limit: 100}, riskClass: 'R1',
  plan: {planId: 'plan-one'}, approvalEvidence: null, phase: 'execute', ...extra}; }

describe('AIAuthorizationService', () => {
  it('evaluates all seventeen checks in exact order and issues a single-use execution decision', async () => {
    const {service} = fixture(); const decision = await service.evaluate(operation(), context());
    expect(decision.allowed).toBe(true);
    expect(decision.completedChecks.map((item) => item.name)).toEqual(AI_AUTHORIZATION_CHECKS);
    expect(() => service.assertExecutionDecision(decision, 'ai.query.execute_read',
      {queryId: 'query-one', limit: 99})).toThrow('current final AI execution authorization');
    expect(service.assertExecutionDecision(decision, 'ai.query.execute_read',
      {limit: 100, queryId: 'query-one'})).toBe(true);
    expect(() => service.assertExecutionDecision(decision, 'ai.query.execute_read',
      {queryId: 'query-one', limit: 100})).toThrow(
      'current final AI execution authorization');
    expect(() => service.assertExecutionDecision({...decision}, 'ai.query.execute_read',
      {queryId: 'query-one', limit: 100})).toThrow();
  });

  it.each([
    ['emergencyState', 'emergency_state'], ['modelHealth', 'model_profile'],
    ['connectorHealth', 'connector'], ['delegation', 'delegation'],
    ['connectorPolicy', 'connector_policy'], ['environmentPolicy', 'environment_policy'],
    ['databasePrecheck', 'database_principal'], ['dataEgress', 'data_egress'],
    ['budget', 'budget'], ['planValidation', 'plan'],
  ])('stops at the first denied external authority: %s', async (authority, check) => {
    const denied = jest.fn(() => ({allowed: false, reason: `${check} denied`, evidenceRef: 'evidence:no'}));
    const {service, authorities} = fixture({[authority]: denied});
    const decision = await service.evaluate(operation(), context());
    expect(decision).toEqual(expect.objectContaining({allowed: false, deniedAt: check,
      reason: `${check} denied`}));
    const index = AI_AUTHORIZATION_CHECKS.indexOf(check);
    expect(decision.completedChecks).toHaveLength(index + 1);
    for(const later of AUTHORITY_NAMES.slice(AUTHORITY_NAMES.indexOf(authority) + 1)) {
      if(AI_AUTHORIZATION_CHECKS.indexOf(later) > index) expect(authorities[later]).not.toHaveBeenCalled();
    }
  });

  it('fails independently at agent, user, exposure, tool and resource checks', async () => {
    const {service} = fixture();
    const disabledAgent = context(); disabledAgent.agentProfile.enabled = false;
    await expect(service.evaluate(operation(), disabledAgent)).resolves.toMatchObject({deniedAt: 'agent_profile'});
    const noUse = context(); noUse.currentUser.permissions = noUse.currentUser.permissions.filter(
      (item) => item !== 'ai.use');
    await expect(service.evaluate(operation(), noUse)).resolves.toMatchObject({deniedAt: 'ai_use_permission'});
    await expect(service.evaluate(operation({commandId: 'ai.legacy.eligible'}), context()))
      .resolves.toMatchObject({deniedAt: 'tool_exposure'});
    await expect(service.evaluate(operation({riskClass: 'R0'}), context()))
      .resolves.toMatchObject({deniedAt: 'tool_exposure'});
    const deniedTool = context(); deniedTool.toolPolicy.deniedCommandIds = ['ai.query.execute_read'];
    await expect(service.evaluate(operation(), deniedTool)).resolves.toMatchObject({deniedAt: 'tool_policy'});
    await expect(service.evaluate(operation({resourceRefs: ['resource:other']}), context()))
      .resolves.toMatchObject({deniedAt: 'resource_scope'});
    const noDraft = context(); noDraft.toolPolicy.allowProjectDrafts = false;
    await expect(service.evaluate(operation({commandId: 'ai.asset.draft', riskClass: 'R2',
      operationKind: 'project_draft'}), noDraft)).resolves.toMatchObject({deniedAt: 'tool_policy'});
    const noBackground = context(); noBackground.toolPolicy.allowBackgroundTasks = false;
    await expect(service.evaluate(operation({commandId: 'ai.metadata.task', riskClass: 'R0',
      operationKind: 'task'}), noBackground)).resolves.toMatchObject({deniedAt: 'tool_policy'});
  });

  it('enforces native-SBsql cross-surface, egress, bounded-budget and explicit-unlimited semantics', async () => {
    const {service} = fixture();
    await expect(service.evaluate(operation({accessSurfaceRefs: ['pg', 'mysql']}), context()))
      .resolves.toMatchObject({deniedAt: 'resource_scope'});
    const scratchBird = context(); scratchBird.connectorProfile = {...scratchBird.connectorProfile,
      connectorClass: 'scratchbird_sbsql', providerId: 'scratchbird', dialectId: 'sbsql'};
    scratchBird.dataPolicy.queryPolicy.allowCrossSurface = true;
    await expect(service.evaluate(operation({accessSurfaceRefs: ['pg', 'mysql']}), scratchBird))
      .resolves.toMatchObject({allowed: true});
    const overEgress = operation({modelVisibleContent: {classification: 'CONFIDENTIAL',
      exposureLevel: 'SAFE_SAMPLE', remote: true, containsSamples: true, containsSource: false,
      sampleRows: 21, textCharacters: 100}});
    await expect(service.evaluate(overEgress, context())).resolves.toMatchObject({deniedAt: 'data_egress'});
    await expect(service.evaluate(operation({estimatedBudget: {databaseRowsPerQuery: 1001}}), context()))
      .resolves.toMatchObject({deniedAt: 'budget'});
    const unlimited = context(); unlimited.budgetPolicy.maxDatabaseRowsPerQuery = null;
    unlimited.currentUser.permissions = unlimited.currentUser.permissions.filter((item) => item !== 'ai.admin');
    await expect(service.evaluate(operation(), unlimited)).resolves.toMatchObject({allowed: true});
    const reviewedRead = context(); reviewedRead.toolPolicy.maxAutomaticRisk = 'R0';
    await expect(service.evaluate(operation(), reviewedRead)).resolves.toMatchObject({deniedAt: 'approval'});
  });

  it.each([
    ['ai.metadata.task', 'R0', 'task', 'ai.delegate_read'],
    ['ai.query.execute_read', 'R1', 'query_read', 'ai.delegate_read'],
    ['ai.asset.draft', 'R2', 'project_draft', 'ai.delegate_draft'],
    ['ai.publish.prepare', 'R3', 'command', 'ai.delegate_draft'],
    ['ai.query.execute_mutation', 'R4', 'query_mutation', 'ai.delegate_write'],
    ['ai.security.rotate', 'R5', 'command', 'ai.approve_high_risk'],
    ['ai.production.cutover', 'R6', 'command', 'ai.approve_production'],
  ])('enforces risk-specific delegation for %s', async (commandId, riskClass,
    operationKind, permission) => {
    const {service} = fixture(); const denied = context(); denied.currentUser.permissions =
      denied.currentUser.permissions.filter((item) => item !== permission);
    await expect(service.evaluate(operation({commandId, riskClass, operationKind,
      approvalEvidence: {approvalId: 'approval-one'}}), denied)).resolves.toMatchObject({
      deniedAt: 'delegation'});
  });

  it('requires risk-specific delegation, approval and final backend authorization', async () => {
    const backend = jest.fn(() => ({allowed: false, reason: 'backend denied', evidenceRef: 'backend:no'}));
    const {service} = fixture({backendAuthorization: backend});
    const mutation = operation({commandId: 'ai.query.execute_mutation', operationKind: 'query_mutation',
      riskClass: 'R4', approvalEvidence: {approvalId: 'approval-one'}});
    const noWrite = context(); noWrite.currentUser.permissions = noWrite.currentUser.permissions.filter(
      (item) => item !== 'ai.delegate_write');
    await expect(service.evaluate(mutation, noWrite)).resolves.toMatchObject({deniedAt: 'delegation'});
    await expect(service.evaluate({...mutation, approvalEvidence: null}, context()))
      .resolves.toMatchObject({deniedAt: 'approval'});
    const approvalDenied = fixture({approvalValidation: jest.fn(() => ({allowed: false,
      reason: 'stale approval', evidenceRef: 'approval:stale'}))}).service;
    await expect(approvalDenied.evaluate(mutation, context())).resolves.toMatchObject({
      deniedAt: 'approval', reason: 'stale approval'});
    await expect(service.evaluate(mutation, context())).resolves.toMatchObject({deniedAt: 'backend_authorization'});
    await expect(service.evaluate(operation({commandId: 'ai.secret.export', riskClass: 'R7'}), context()))
      .resolves.toMatchObject({deniedAt: 'delegation'});
  });

  it('requires every independent authority at construction', async () => {
    const {commands, authorities} = fixture(); delete authorities.databasePrecheck;
    expect(() => new AIAuthorizationService({commands, ...authorities})).toThrow(
      'requires databasePrecheck authority');
    await expect(fixture().service.evaluate({...operation(), normalizedArguments: {password: 'unsafe'}},
      context())).rejects.toThrow('Raw credential');
  });

  it('expires an otherwise valid argument-bound execution decision', async () => {
    const {commands, authorities} = fixture(); let now = '2026-09-12T12:00:00.000Z';
    const service = new AIAuthorizationService({commands, ...authorities, decisionTtlSeconds: 1,
      now: () => now}); const decision = await service.evaluate(operation(), context());
    now = '2026-09-12T12:00:02.000Z';
    expect(() => service.assertExecutionDecision(decision, 'ai.query.execute_read',
      {queryId: 'query-one', limit: 100})).toThrow('current final AI execution authorization');
  });
});
