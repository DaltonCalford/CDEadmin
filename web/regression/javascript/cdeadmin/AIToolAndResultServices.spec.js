import {CommandRegistry} from 'sources/cdeadmin_ui/commands/CommandRegistry';
import {
  AIReadToolRegistry, AIResultHandleService, AIToolCatalog,
} from 'sources/cdeadmin_ui/modules/ai_interface';

const approvalPolicy = {schemaVersion: 1, policyId: 'approval-main', name: 'Approval',
  riskRules: {R0: 'auto', R1: 'auto', R2: 'review_diff', R3: 'review',
    R4: 'confirm_each', R5: 'typed_confirm', R6: 'dual_or_typed'},
  expiryMinutes: {R4: 15, R5: 5, R6: 5}};
const toolPolicy = {schemaVersion: 1, policyId: 'tool-main', name: 'Tools',
  moduleIds: [], commandIds: ['metadata.read'], deniedCommandIds: [], maxAutomaticRisk: 'R1',
  allowProjectDrafts: true, allowBackgroundTasks: false};
function commandRegistry(result={rows: [{id: 1}]}) {
  const commands = new CommandRegistry(); const execute = jest.fn(async () => result);
  commands.register({id: 'metadata.read', label: 'Read metadata', description: 'Read bounded metadata.',
    permission: ['ai.use', 'metadata.read'], aiExposure: 'read_only', aiRiskClass: 'R1',
    aiModuleId: 'cdeadmin.discovery_intelligence', aiArgumentSchema: {type: 'object',
      additionalProperties: false, required: ['resource'], properties: {resource: {type: 'string'}}},
    aiResultSchema: {type: 'object', additionalProperties: false, required: ['rows'],
      properties: {rows: {type: 'array'}}}, aiContextCostHint: 64, execute});
  commands.register({id: 'legacy.ai.eligible', aiEligible: true, execute: jest.fn()});
  commands.register({id: 'secret.export', description: 'Forbidden secret operation.',
    aiExposure: 'executable', aiRiskClass: 'R7', aiModuleId: 'cdeadmin.security',
    aiArgumentSchema: {type: 'object'}, aiResultSchema: {type: 'object'}, execute: jest.fn()});
  return {commands, execute};
}

describe('AIToolCatalog', () => {
  it('derives only explicitly exposed, permitted and policy-allowed commands', () => {
    const {commands} = commandRegistry(); const authorization = {assertExecutionDecision: jest.fn()};
    const catalog = new AIToolCatalog({commands, authorization});
    const published = catalog.publish({currentUser: {permissions: ['ai.use', 'metadata.read']},
      toolPolicy, approvalPolicy});
    expect(published).toEqual([expect.objectContaining({kind: 'cdeadmin_command',
      commandId: 'metadata.read', riskClass: 'R1', approvalRule: 'auto', contextCostHint: 64,
      requiredPermissions: ['ai.use', 'metadata.read']})]);
    expect(published[0].toolName).toBe('cdeadmin__metadata__read');
    expect(Object.isFrozen(published[0].inputSchema)).toBe(true);
    expect(catalog.publish({currentUser: {permissions: ['ai.use']}, toolPolicy,
      approvalPolicy})).toEqual([]);
    expect(catalog.publish({currentUser: {permissions: ['ai.use', 'metadata.read']},
      toolPolicy: {...toolPolicy, deniedCommandIds: ['metadata.read']}, approvalPolicy})).toEqual([]);
    expect(catalog.publish({currentUser: {permissions: ['ai.use', 'metadata.read']},
      toolPolicy: {...toolPolicy, maxAutomaticRisk: 'R0'}, approvalPolicy})[0].approvalRule).toBe('review');
  });

  it('requires a genuine final decision and validates arguments and results', async () => {
    const {commands, execute} = commandRegistry();
    const authorization = {assertExecutionDecision: jest.fn(() => true)};
    const catalog = new AIToolCatalog({commands, authorization}); const decision = {allowed: true};
    await expect(catalog.invokeCommand('metadata.read', {resource: 'orders'}, decision,
      {currentUser: {permissions: ['ai.use', 'metadata.read']}})).resolves.toEqual({rows: [{id: 1}]});
    expect(authorization.assertExecutionDecision).toHaveBeenCalledWith(
      decision, 'metadata.read', {resource: 'orders'});
    expect(execute).toHaveBeenCalledTimes(1);
    await expect(catalog.invokeCommand('metadata.read', {resource: 'orders', invented: true}, decision,
      {currentUser: {permissions: ['ai.use', 'metadata.read']}})).rejects.toThrow('JSON Schema');
    expect(execute).toHaveBeenCalledTimes(1);
  });

  it('publishes and invokes bounded read-service tools with repeated access checks', async () => {
    const reads = new AIReadToolRegistry(); const accessCheck = jest.fn((context) => context.scope === 'orders');
    const remove = reads.register({id: 'discovery.sample', moduleId: 'cdeadmin.discovery_intelligence',
      description: 'Read a bounded classified sample.', requiredPermissions: ['ai.use'], maxRows: 2,
      maxBytes: 4096, contextCostHint: 32, inputSchema: {type: 'object', additionalProperties: false,
        required: ['limit'], properties: {limit: {type: 'integer', minimum: 1, maximum: 2}}},
      outputSchema: {type: 'object', additionalProperties: false, required: ['rows'],
        properties: {rows: {type: 'array'}}}, accessCheck,
      execute: async ({limit}) => ({rows: [{id: 1}, {id: 2}].slice(0, limit)}),
      classify: () => 'INTERNAL'});
    const {commands} = commandRegistry(); const catalog = new AIToolCatalog({commands, readTools: reads,
      authorization: {assertExecutionDecision: jest.fn()}});
    const published = catalog.publish({currentUser: {permissions: ['ai.use']}, scope: 'orders',
      toolPolicy: {...toolPolicy, commandIds: []}, approvalPolicy});
    expect(published).toEqual([expect.objectContaining({kind: 'read_service',
      readToolId: 'discovery.sample', maxRows: 2, maxBytes: 4096})]);
    await expect(reads.invoke('discovery.sample', {limit: 2}, {
      currentUser: {permissions: ['ai.use']}, scope: 'orders'})).resolves.toEqual(
      expect.objectContaining({classification: 'INTERNAL', rowCount: 2}));
    await expect(catalog.invokeReadTool('discovery.sample', {limit: 1}, {
      currentUser: {permissions: ['ai.use']}, scope: 'orders'})).resolves.toEqual(
      expect.objectContaining({classification: 'INTERNAL', rowCount: 1}));
    await expect(reads.invoke('discovery.sample', {limit: 2}, {
      currentUser: {permissions: ['ai.use']}, scope: 'other'})).rejects.toThrow('access denied');
    expect(accessCheck).toHaveBeenCalledTimes(4); expect(remove()).toBe(true);
  });

  it('refuses unbounded, schema-invalid and secret-bearing read-service results', async () => {
    const reads = new AIReadToolRegistry();
    const base = {moduleId: 'cdeadmin.discovery_intelligence', description: 'Bounded read.',
      requiredPermissions: ['ai.use'], maxRows: 1, maxBytes: 4096,
      inputSchema: {type: 'object'}, outputSchema: {type: 'object'}, accessCheck: () => true,
      classify: () => 'INTERNAL'};
    reads.register({...base, id: 'read.too_many', execute: async () => ({rows: [{id: 1}, {id: 2}]})});
    await expect(reads.invoke('read.too_many', {}, {currentUser: {permissions: ['ai.use']}}))
      .rejects.toThrow('result bound');
    reads.register({...base, id: 'read.secret', execute: async () => ({accessToken: 'unsafe'})});
    await expect(reads.invoke('read.secret', {}, {currentUser: {permissions: ['ai.use']}}))
      .rejects.toThrow('Raw credential');
    expect(() => reads.register({...base, id: 'read.unbounded', maxRows: 0,
      execute: async () => ({})})).toThrow('positive result bounds');
    expect(() => reads.register({...base, id: 'read.secret_schema', execute: async () => ({}),
      inputSchema: {type: 'object', properties: {accessToken: {type: 'string'}}}}))
      .toThrow('Raw credential');
  });
});

describe('AIResultHandleService', () => {
  it.each(['tabular', 'document', 'graph', 'key_value', 'time_series', 'vector',
    'search', 'scalar', 'native'])('represents %s results without exposing inline values', (resultType) => {
    const service = new AIResultHandleService({authorize: () => true,
      now: () => '2026-09-12T12:00:00.000Z'});
    const descriptor = service.create({handleId: `handle-${resultType}`, resultType,
      value: {items: [{id: 1}]}, schemaSummary: {model: resultType}, classification: 'INTERNAL',
      permittedOperations: ['slice'], resourceRefs: ['resource:one'], ownerId: 'user-one',
      connectorId: 'connector-one', authorizationRef: 'decision-one'});
    expect(descriptor.resultType).toBe(resultType); expect(descriptor.value).toBeUndefined();
    expect(service.slice(descriptor.handleId, {limit: 1}, {}).values).toEqual([{id: 1}]);
  });

  it('keeps values behind an expiring descriptor and provides bounded slices and statistics', () => {
    const authorize = jest.fn((_descriptor, _operation, context) => context.userId === 'user-one');
    const service = new AIResultHandleService({authorize, now: () => '2026-09-12T12:00:00.000Z'});
    const descriptor = service.create({resultType: 'tabular', value: {rows: [
      {id: 1, amount: 10}, {id: 2, amount: 20}, {id: 3, amount: null}]},
    schemaSummary: {columns: ['id', 'amount']}, classification: 'INTERNAL',
    permittedOperations: ['slice', 'statistics'], resourceRefs: ['resource:orders'],
    ownerId: 'user-one', connectorId: 'connector-main', authorizationRef: 'decision-one'});
    expect(descriptor).toEqual(expect.objectContaining({rowCount: 3, resultType: 'tabular',
      permittedOperations: ['summary', 'slice', 'statistics']}));
    expect(descriptor.value).toBeUndefined();
    expect(service.modelDescriptor(descriptor.handleId, {userId: 'user-one'}).schemaSummary)
      .toEqual({columns: ['id', 'amount']});
    expect(service.slice(descriptor.handleId, {offset: 1, limit: 2}, {userId: 'user-one'}).values)
      .toEqual([{id: 2, amount: 20}, {id: 3, amount: null}]);
    expect(service.statistics(descriptor.handleId, ['amount'], {userId: 'user-one'}).statistics.amount)
      .toEqual({count: 2, minimum: 10, maximum: 20, average: 15});
    expect(authorize).toHaveBeenCalledTimes(3);
  });

  it('rechecks access, expiry, bounds, duplicate identity and permitted operations', () => {
    let now = '2026-09-12T12:00:00.000Z';
    const service = new AIResultHandleService({authorize: (_descriptor, _operation, context) => context.ok,
      now: () => now, maximumRows: 2, maximumBytes: 4096});
    const input = {handleId: 'handle-one', resultType: 'document', value: [{id: 1}],
      schemaSummary: {shape: 'document'}, classification: 'INTERNAL', permittedOperations: [],
      resourceRefs: ['resource:document'], ownerId: 'user-one', connectorId: 'connector-main',
      authorizationRef: 'decision-one', expiresAt: '2026-09-12T12:01:00.000Z'};
    service.create(input);
    expect(() => service.descriptor('handle-one', {ok: false})).toThrow('access denied');
    expect(() => service.slice('handle-one', {}, {ok: true})).toThrow('does not permit slice');
    expect(() => service.create(input)).toThrow('already exists');
    expect(() => service.create({...input, handleId: 'too-many', value: [{}, {}, {}]}))
      .toThrow('storage limits');
    now = '2026-09-12T12:02:00.000Z';
    expect(() => service.descriptor('handle-one', {ok: true})).toThrow('expired');
    expect(service.sweep()).toBe(0);
  });
});
