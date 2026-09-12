import {
  APIAdapterRegistry, APIService, validateAPIProviderResult,
} from 'sources/cdeadmin_ui/modules/api/APIService';
import {
  FederatedSearchService, RelationshipGraphService, TaskExecutionService,
} from 'sources/cdeadmin_ui/platform/CoordinationServices';
import {
  adapter, apiDefinition, fixture, providerResult, resourceRef, user,
} from './APITestUtils';

describe('API Designer service', () => {
  test('admits only complete exact provider adapters and result envelopes', () => {
    const registry = new APIAdapterRegistry();
    expect(() => registry.register('partial', {resolveDataBinding() {}})).toThrow('validateProviderOperation');
    expect(() => validateAPIProviderResult({supportState: 'supported_native'}, 'provider', 'operation'))
      .toThrow('warnings');
    expect(() => validateAPIProviderResult({...providerResult({ok: true}), guessed: true},
      'provider', 'operation')).toThrow('unsupported field guessed');
  });

  test('validates every bound provider operation with explicit evidence', async () => {
    const {service, tasks, calls} = fixture(); const session = service.create({content: apiDefinition()});
    const task = tasks.submit({type: 'api.validation', sessionId: session.id, label: 'Validate'});
    const result = await tasks.wait(task.id);
    expect(result.providerResults[0]).toMatchObject({operationId: 'list-orders',
      described: {supportState: 'supported_native'}, checked: {supportState: 'supported_native'}});
    expect(calls.map((item) => item.name)).toEqual(['resolveDataBinding', 'describeProviderSchema',
      'validateProviderOperation']);
    expect(service.get(session.id).runtime.providerSchemas[0]).toMatchObject({operationId: 'list-orders',
      schema: {fields: [{name: 'id'}]}});
  });

  test('keeps unknown provider support distinct and fails closed', async () => {
    const tasks = new TaskExecutionService(); const relationships = new RelationshipGraphService();
    const search = new FederatedSearchService(); const service = new APIService({tasks, relationships, search});
    const session = service.create({content: apiDefinition()});
    const task = tasks.submit({type: 'api.validation', sessionId: session.id, label: 'Validate'});
    await expect(tasks.wait(task.id)).rejects.toThrow('unknown');
    expect(service.get(session.id).providerStatuses[0]).toMatchObject({supportState: 'unknown'});
  });

  test('runs bounded live tests and redacts sensitive response headers', async () => {
    const {service, tasks} = fixture(); const session = service.create({content: apiDefinition()});
    const task = service.test(session.id, 'live-list', {maxPreviewBytes: 8}, {currentUser: user()});
    const result = await tasks.wait(task.id);
    expect(result.response).toMatchObject({truncated: true, headerFields: expect.arrayContaining([
      {name: 'authorization', value: '[REDACTED]', redacted: true},
      {name: 'x-private', value: '[REDACTED]', redacted: true}])});
    expect(JSON.stringify(result)).not.toContain('not-stored');
  });

  test('requires target-bound confirmation for non-idempotent live tests', async () => {
    const base = apiDefinition(); const content = apiDefinition({operations: [{...base.operations[0],
      method: 'POST', idempotent: false}]}); const {service, tasks} = fixture();
    const session = service.create({content}); let task = service.test(session.id, 'live-list');
    await expect(tasks.wait(task.id)).rejects.toThrow('write confirmation');
    task = service.test(session.id, 'live-list', {confirmationRef: 'approval',
      environment: content.tests[0].environmentId}, {currentUser: user()}); await expect(tasks.wait(task.id)).resolves
      .toMatchObject({status: 200});
  });

  test('requires the live-write permission independently of confirmation', async () => {
    const base = apiDefinition(); const content = apiDefinition({operations: [{...base.operations[0],
      method: 'POST', idempotent: false}]}); const {service, tasks} = fixture();
    const session = service.create({content}); const task = service.test(session.id, 'live-list',
      {confirmationRef: 'approval', environment: content.tests[0].environmentId},
      {currentUser: {id: 'reader', permissions: ['api.test']}});
    await expect(tasks.wait(task.id)).rejects.toThrow('api.invoke_live_write');
  });

  test('keeps drag/drop binding as a reviewable non-mutating proposal', () => {
    const {service} = fixture(); const session = service.create({content: apiDefinition()});
    const before = service.get(session.id).content;
    const proposal = service.proposeBinding(session.id, 'list-orders', resourceRef);
    expect(proposal).toMatchObject({applied: false, operationId: 'list-orders'});
    expect(service.get(session.id).content).toEqual(before);
  });

  test('prepares deployment without deploying and binds confirmation to the target', async () => {
    const content = apiDefinition({deploymentBindings: [{id: 'gateway-dev', name: 'Dev Gateway',
      environment: 'dev', targetRef: resourceRef, gatewayProfile: 'test-gateway',
      credentialRef: null, config: {}, extensions: []}]});
    const {service, tasks} = fixture(); const session = service.create({content});
    let task = service.prepareDeployment(session.id, 'gateway-dev', {confirmationRef: 'approval',
      environment: 'wrong', connection: `resource:${resourceRef.canonical}`});
    await expect(tasks.wait(task.id)).rejects.toThrow('target-bound');
    task = service.prepareDeployment(session.id, 'gateway-dev', {confirmationRef: 'approval',
      environment: 'dev', connection: `resource:${resourceRef.canonical}`});
    await expect(tasks.wait(task.id)).resolves.toMatchObject({preparedOnly: true, deployExecuted: false});
  });

  test('imports and exports through shared tasks without changing declared versions', async () => {
    const {service, tasks} = fixture(); const session = service.create({});
    let task = service.import(session.id, 'openapi_http', {
      openapi: '3.2.7', info: {title: 'Imported', version: '1'}, paths: {}});
    await tasks.wait(task.id); expect(service.get(session.id).content.externalSpecVersion).toBe('3.2.7');
    task = service.export(session.id, 'openapi_http'); const result = await tasks.wait(task.id);
    expect(result).toMatchObject({version: '3.2.7', metadataOnly: true,
      document: {openapi: '3.2.7'}});
  });

  test('resets runtime evidence whenever authored definitions change', async () => {
    const {service, tasks} = fixture(); const session = service.create({content: apiDefinition()});
    let task = service.test(session.id, 'live-list'); await tasks.wait(task.id);
    expect(service.get(session.id).runtime.testResults).toHaveLength(1);
    service.addOperation(session.id, {...apiDefinition().operations[0], id: 'another'});
    expect(service.get(session.id).runtime.testResults).toEqual([]);
  });

  test('contributes typed search results and durable resource relationships', async () => {
    const {service, search, relationships} = fixture(); service.create({id: 'api-one', content: apiDefinition()});
    const found = await search.search('orders', {context: {permissions: ['api.view']}});
    expect(found.groups[0].results.map((item) => item.type)).toEqual(expect.arrayContaining([
      'api.asset', 'api.operation', 'api.resource']));
    expect(relationships.snapshot().edges).toContainEqual(expect.objectContaining({origin: 'cdeadmin.api',
      relation: 'operation_binding'}));
    expect((await search.search('orders', {context: {permissions: []}})).total).toBe(0);
  });

  test('preserves dirty authored state on optimistic persistence conflicts', async () => {
    const projectAssets = {update: jest.fn().mockRejectedValue(new Error('asset conflict'))};
    const {service} = fixture({projectAssets}); const session = service.create({content: apiDefinition()});
    await expect(service.save(session.id, {projectId: 'p', assetId: 'a', expectedVersion: 1}))
      .rejects.toThrow('asset conflict');
    expect(service.get(session.id)).toMatchObject({dirty: true, state: 'runtime_failure'});
  });

  test('does not admit a generic adapter for an unregistered engine', () => {
    const calls = []; const registry = new APIAdapterRegistry(); registry.register('firebird', adapter(calls));
    expect(registry.get('postgresql')).toBeNull();
  });
});
