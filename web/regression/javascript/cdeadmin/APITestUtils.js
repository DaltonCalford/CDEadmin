import {
  FederatedSearchService, RelationshipGraphService, TaskExecutionService,
} from 'sources/cdeadmin_ui/platform/CoordinationServices';
import {APIAdapterRegistry, APIService} from 'sources/cdeadmin_ui/modules/api/APIService';
import {createAPIContent} from 'sources/cdeadmin_ui/modules/api/contracts';
import {importOpenAPI} from 'sources/cdeadmin_ui/modules/api/APIEngine';

export const resourceRef = Object.freeze({schema: 'cdeadmin.resource-ref.v1',
  canonical: 'firebird://localhost/example/orders', providerId: 'firebird'});
export function openapi(overrides={}) { return {openapi: '3.2.0',
  info: {title: 'Orders API', version: '1.0.0'}, servers: [{url: 'https://api.example.test'}],
  paths: {'/orders': {get: {operationId: 'list-orders', summary: 'List orders',
    responses: {'200': {description: 'Orders'}}}, 'x-path-note': true}},
  components: {schemas: {Order: {type: 'object', required: ['id'], properties: {
    id: {type: 'integer'}, password: {type: 'string', format: 'password'}}}},
  securitySchemes: {oauth: {type: 'oauth2', flows: {clientCredentials: {
    tokenUrl: 'https://identity.example.test/token', scopes: {'orders:read': 'Read orders'}}}}}},
  'x-root-note': {owner: 'test'}, ...overrides}; }
export function apiDefinition(overrides={}) {
  const imported = importOpenAPI(openapi(), {source: 'test'}); const operation = imported.operations[0];
  return createAPIContent({...imported, operations: [{...operation, binding: {id: 'orders-binding',
    type: 'provider_resource_read', targetRef: resourceRef, mode: 'read', command: null,
    inputMap: {}, outputMap: {}, nativeDetails: {}, extensions: []},
  securityRequirementIds: []}], tests: [{id: 'live-list', name: 'Live list', operationId: operation.id,
    channelId: null, environmentId: imported.servers[0].id, mode: 'live', parameters: {}, headers: {},
    body: {}, expected: {status: 200}, sensitiveHeaderNames: ['x-private'], extensions: []}], ...overrides});
}
export function providerResult(value, overrides={}) { return {supportState: 'supported_native',
  providerVersion: 'firebird-test', evidence: {observed: true}, warnings: [], nativeDetails: {},
  readCapabilities: ['api.read'], writeCapabilities: ['api.invoke'],
  discoveryCapabilities: ['api.schema'], nativeMechanisms: ['provider-adapter'],
  versionConstraints: [], limitations: [], runtimeEvidence: {observed: true}, value, ...overrides}; }
export function adapter(calls=[]) { const record = (name, value) => async (input) => {
  calls.push({name, input}); return providerResult(typeof value === 'function' ? value(input) : value); };
return {resolveDataBinding: record('resolveDataBinding', {resolved: true}),
  validateProviderOperation: record('validateProviderOperation', {valid: true}),
  prepareInvocation: record('prepareInvocation', {requestId: 'request-one'}),
  describeProviderSchema: record('describeProviderSchema', {fields: [{name: 'id'}]}),
  invokePrepared: record('invokePrepared', {status: 200,
    headers: {authorization: 'not-stored', 'content-type': 'application/json', 'x-private': 'hidden'},
    body: [{id: 1}]})}; }
export function fixture(options={}) { const tasks = new TaskExecutionService({now: () =>
  '2026-09-12T00:00:00Z'}); const relationships = new RelationshipGraphService();
const search = new FederatedSearchService(); const adapters = new APIAdapterRegistry();
const calls = []; adapters.register('firebird', options.adapter ?? adapter(calls));
const service = new APIService({tasks, relationships, search, adapters,
  projectAssets: options.projectAssets, events: options.events,
  now: () => '2026-09-12T00:00:00Z'}); return {service, tasks, relationships, search, adapters, calls}; }
export function user() { return {id: 'api-user', permissions: ['api.view', 'api.edit', 'api.test',
  'api.deploy_prepare', 'api.invoke_live_write', 'api.admin']}; }
