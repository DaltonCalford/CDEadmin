import {
  AIQueryAuthority, AIResultHandleService,
} from 'sources/cdeadmin_ui/modules/ai_interface';

function prepared(extra={}) { return {preparedId: 'prepared-one', sideEffects: false,
  connectorSnapshot: {connectorId: 'connector-main', revision: 3, capabilitySnapshotRef: 'capability:1'},
  operationKind: 'query', canonicalResourceRefs: ['resource:orders'], accessSurfaceRefs: [],
  normalizedArguments: {rowLimit: 100}, nativeCompiledArtifact: {artifactRef: 'artifact:one'},
  riskClass: 'R1', estimatedEffects: [{kind: 'read', resourceRef: 'resource:orders'}],
  budgets: {databaseQueriesPerTurn: 1, databaseRowsPerQuery: 100},
  validation: [{check: 'provider_compile', passed: true}],
  expiresAt: '2026-09-12T12:05:00.000Z', queryClassification: 'read_only',
  explain: {operator: 'scan'}, connectorId: 'connector-main', connectorRevision: 3,
  preparedAt: '2026-09-12T12:00:00.000Z', ...extra}; }
function fixture({preparedValue=prepared(), executionResult, authorizationResult}={}) {
  const connectors = {get: jest.fn(() => ({profile: {connectorId: 'connector-main',
    dialectId: 'firebird'}, revision: 3})), prepare: jest.fn(async () => preparedValue),
  execute: jest.fn(async () => executionResult ?? ({kind: 'result', resultType: 'tabular',
    value: {rows: [{id: 1, amount: 10}, {id: 2, amount: 20}]},
    schemaSummary: {columns: ['id', 'amount']}, classification: 'INTERNAL',
    permittedOperations: ['slice', 'statistics']})), cancel: jest.fn(async () => ({requested: true}))};
  const authorization = {evaluate: jest.fn(async (operation) => authorizationResult ?? ({
    allowed: true, decisionId: `decision:${operation.operationId}`, completedChecks: []})),
  assertExecutionDecision: jest.fn(() => true)};
  const resultHandles = new AIResultHandleService({authorize: (_descriptor, _operation, context) => context.ok,
    now: () => '2026-09-12T12:00:00.000Z'});
  return {connectors, authorization, resultHandles, service: new AIQueryAuthority({connectors,
    authorization, resultHandles, now: () => '2026-09-12T12:00:00.000Z'})};
}
function request(extra={}) { return {queryId: 'query-one', connectorId: 'connector-main',
  dialectId: 'firebird', source: 'select id, amount from orders',
  requestedResourceRefs: ['resource:orders'], accessSurfaceRefs: [], requestExplain: true,
  estimatedBudget: {databaseQueriesPerTurn: 1}, environment: 'development', ...extra}; }

describe('AIQueryAuthority', () => {
  it('compiles through the provider and exposes exact human-review evidence', async () => {
    const {service, connectors, authorization} = fixture();
    const review = await service.compile(request(), {plan: {planId: 'plan-one'}});
    expect(review).toEqual(expect.objectContaining({schema: 'cdeadmin.ai-query-review.v1',
      queryClassification: 'read_only', riskClass: 'R1', canonicalResourceRefs: ['resource:orders'],
      explain: {operator: 'scan'}, connectorRevision: 3}));
    expect(service.review('query-one')).toBe(review);
    expect(connectors.prepare).toHaveBeenCalledWith('connector-main', expect.objectContaining({
      operationKind: 'query', dialectId: 'firebird', requestExplain: true}));
    expect(authorization.evaluate).toHaveBeenNthCalledWith(1, expect.objectContaining({
      commandId: 'ai.query.compile', phase: 'plan'}), expect.anything());
    expect(authorization.evaluate).toHaveBeenNthCalledWith(2, expect.objectContaining({
      commandId: 'ai.query.explain', operationKind: 'query_explain'}), expect.anything());
  });

  it('executes provider-classified reads and returns only a bounded result handle', async () => {
    const {service, connectors, authorization, resultHandles} = fixture();
    await service.compile(request()); const execution = await service.execute('query-one', {
      currentUser: {id: 'user-one'}, plan: {planId: 'plan-one'}});
    expect(execution).toEqual(expect.objectContaining({kind: 'result_handle',
      decisionId: 'decision:query-one:execute'}));
    expect(execution.resultHandle.value).toBeUndefined();
    expect(resultHandles.slice(execution.resultHandle.handleId, {offset: 0, limit: 1}, {ok: true}).values)
      .toEqual([{id: 1, amount: 10}]);
    expect(connectors.execute).toHaveBeenCalledWith('connector-main', 'prepared-one', null);
    expect(authorization.evaluate.mock.calls.at(-1)[0]).toEqual(expect.objectContaining({
      commandId: 'ai.query.execute_read', operationKind: 'query_read', phase: 'execute',
      resourceRefs: ['resource:orders']}));
    expect(() => service.review('query-one')).toThrow('Unknown AI query');
  });

  it('routes mutation/unknown classification through approved mutation authority and supports TaskRef', async () => {
    const mutation = prepared({queryClassification: 'mutation', riskClass: 'R4',
      estimatedEffects: [{kind: 'update', resourceRef: 'resource:orders'}]});
    const {service, authorization} = fixture({preparedValue: mutation,
      executionResult: {kind: 'task', taskRef: 'task:mutation-one'}});
    await service.compile(request()); const result = await service.execute('query-one', {
      currentUser: {id: 'user-one'}, approvalEvidence: {approvalId: 'approval-one'}});
    expect(result).toEqual(expect.objectContaining({kind: 'task', taskRef: 'task:mutation-one'}));
    expect(authorization.evaluate.mock.calls.at(-1)[0]).toEqual(expect.objectContaining({
      commandId: 'ai.query.execute_mutation', operationKind: 'query_mutation',
      approvalEvidence: {approvalId: 'approval-one'}}));
    const unknown = fixture({preparedValue: prepared({queryClassification: 'unknown', riskClass: 'R4'}),
      executionResult: {kind: 'task', taskRef: 'task:unknown-one'}});
    await unknown.service.compile(request()); await unknown.service.execute('query-one', {
      currentUser: {id: 'user-one'}, approvalEvidence: {approvalId: 'approval-two'}});
    expect(unknown.authorization.evaluate.mock.calls.at(-1)[0].commandId)
      .toBe('ai.query.execute_mutation');
  });

  it('never infers read-only status from source text and rejects inconsistent provider evidence', async () => {
    await expect(fixture({preparedValue: prepared({queryClassification: 'unknown', riskClass: 'R1'})})
      .service.compile(request({source: 'SELECT 1'}))).rejects.toThrow('classification and risk class disagree');
    await expect(fixture({preparedValue: prepared({validation: [{check: 'compile', passed: false}]})})
      .service.compile(request())).rejects.toThrow('did not pass every provider validation');
    await expect(fixture({preparedValue: prepared({connectorRevision: 2})}).service.compile(request()))
      .rejects.toThrow('snapshot is stale or mismatched');
  });

  it('fails closed on dialect mismatch, policy denial, expiry and unsafe result content', async () => {
    await expect(fixture().service.compile(request({dialectId: 'postgresql'})))
      .rejects.toThrow('dialect does not match');
    await expect(fixture({authorizationResult: {allowed: false, deniedAt: 'resource_scope',
      reason: 'outside scope'}}).service.compile(request())).rejects.toThrow(
      'compilation denied at resource_scope');
    await expect(fixture({preparedValue: prepared({expiresAt: '2026-09-12T11:59:00.000Z'})})
      .service.compile(request())).rejects.toThrow('already stale');
    const unsafe = fixture({executionResult: {kind: 'result', resultType: 'tabular',
      value: {accessToken: 'unsafe'}, schemaSummary: {}, classification: 'INTERNAL',
      permittedOperations: []}}); await unsafe.service.compile(request());
    await expect(unsafe.service.execute('query-one', {currentUser: {id: 'user-one'}}))
      .rejects.toThrow('Raw credential');
  });

  it('cancels the exact provider prepared operation and removes the local review', async () => {
    const {service, connectors} = fixture(); await service.compile(request());
    await expect(service.cancel('query-one')).resolves.toEqual(expect.objectContaining({requested: true}));
    expect(connectors.cancel).toHaveBeenCalledWith('connector-main', 'prepared-one');
    expect(() => service.review('query-one')).toThrow('Unknown AI query');
  });
});
