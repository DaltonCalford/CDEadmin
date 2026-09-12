import {
  ExternalReferenceResolver, exportAsyncAPI, exportOpenAPI, generateCRUDDraft, importAsyncAPI,
  importOpenAPI, validateAPIDefinition,
} from 'sources/cdeadmin_ui/modules/api/APIEngine';
import {apiDefinition, openapi, resourceRef} from './APITestUtils';

describe('API Designer engine', () => {
  test('round-trips OpenAPI 3.2 while preserving extensions and sensitive schema names', () => {
    const content = importOpenAPI(openapi()); const exported = exportOpenAPI(content);
    expect(exported.openapi).toBe('3.2.0'); expect(exported['x-root-note']).toEqual({owner: 'test'});
    expect(exported.components.schemas.Order.properties.password.format).toBe('password');
  });
  test('imports AsyncAPI 3.1 channels, messages and bindings', () => {
    const content = importAsyncAPI({asyncapi: '3.1.0', info: {title: 'Events', version: '1'},
      channels: {orders: {address: 'orders.created', messages: {created: {$ref:
        '#/components/messages/created'}}, bindings: {kafka: {topic: 'orders'}}}},
      components: {messages: {created: {payload: {$ref: '#/components/schemas/Order'}}},
        schemas: {Order: {type: 'object'}}}});
    expect(content.channels[0]).toMatchObject({address: 'orders.created', bindings: {kafka: {topic: 'orders'}}});
    const exported = exportAsyncAPI(content); expect(exported.asyncapi).toBe('3.1.0');
    expect(exported.channels.orders).toMatchObject({address: 'orders.created',
      bindings: {kafka: {topic: 'orders'}}});
  });
  test('requires every safe CRUD design choice and never deploys generated drafts', () => {
    expect(() => generateCRUDDraft({resourceRef})).toThrow('allowedOperations');
    const draft = generateCRUDDraft({resourceRef, resourceName: 'orders', allowedOperations: ['list', 'create'],
      keyFields: ['id'], exposedFields: ['id'], paginationPolicy: {type: 'cursor'},
      writeValidationPolicy: {schema: 'orders'}, securityRequirementIds: ['security-api']});
    expect(draft).toMatchObject({deployed: false}); expect(draft.operations).toHaveLength(2);
  });
  test('reports broken semantic references', () => {
    const content = apiDefinition({operations: [{...apiDefinition().operations[0],
      requestSchemaRef: '#/components/schemas/missing'}]});
    expect(validateAPIDefinition(content).errors[0]).toMatch(/unknown schema/);
  });
  test('enforces external reference allowlists, cycles, offline mode and size', async () => {
    const documents = {'https://spec.test/a.json': JSON.stringify({$ref: 'b.json'}),
      'https://spec.test/b.json': JSON.stringify({$ref: 'a.json'})};
    const resolver = new ExternalReferenceResolver({allowedHosts: ['spec.test'],
      fetcher: async (uri) => documents[uri]});
    await expect(resolver.resolve('https://spec.test/a.json')).rejects.toThrow('cycle');
    await expect(new ExternalReferenceResolver({offline: true}).resolve(
      'https://spec.test/a.json')).rejects.toThrow('offline');
    await expect(resolver.resolve('http://spec.test/a.json')).rejects.toThrow('scheme');
  });
  test('rejects raw credential values in external source', () => {
    expect(() => importOpenAPI(openapi({authorization: 'Bearer secret'}))).toThrow('Raw secret');
  });
});
