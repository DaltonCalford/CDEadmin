/////////////////////////////////////////////////////////////
// API Designer canonical contracts and deterministic source.
/////////////////////////////////////////////////////////////

import {immutable, noRawSecrets, plainObject, platformValue} from '../../platform/serviceUtils';

export const API_MODULE_ID = 'cdeadmin.api';
export const API_SERVICE_ID = 'cdeadmin.api.runtime';
export const API_ASSET_TYPE = 'cdeadmin.api.v1';
export const API_ASSET_SCHEMA = 'cdeadmin.api.asset.v1';
export const API_PROFILES = Object.freeze([
  'openapi_http', 'asyncapi_event', 'graphql_schema', 'rpc_extension',
]);
export const API_STATES = Object.freeze(['empty', 'loading', 'ready', 'stale', 'partial',
  'permission_denied', 'disconnected', 'validation_error', 'runtime_failure', 'read_only',
  'background_task_active']);
export const DATA_BINDING_TYPES = Object.freeze(['saved_query', 'provider_resource_read',
  'provider_command', 'stored_procedure/function', 'semantic_model', 'pipeline/macro',
  'custom_backend_handler']);
const REF_SCHEMAS = new Set(['cdeadmin.resource-ref.v1', 'cdeadmin.asset-ref.v1',
  'cdeadmin.external-ref.v1', 'cdeadmin.credential-ref.v1']);
const HTTP_METHODS = new Set(['GET', 'HEAD', 'OPTIONS', 'TRACE', 'POST', 'PUT', 'PATCH', 'DELETE']);

function exact(input, fields, label) {
  const unknown = Object.keys(input).filter((field) => !fields.includes(field));
  if(unknown.length) throw new TypeError(`${label} contains unsupported field ${unknown[0]}.`);
}
function text(value, label, maximum=8192, optional=false) {
  if(optional && (value === undefined || value === null || value === '')) return null;
  return platformValue(value, label, maximum);
}
function object(value, label) {
  const result = plainObject(value ?? {}, label); noRawSecrets(result, label);
  return immutable({...result});
}
function list(value, label, mapper, maximum=10000) {
  if(!Array.isArray(value) || value.length > maximum) throw new TypeError(
    `${label} must contain no more than ${maximum} items.`
  );
  return value.map(mapper);
}
function unique(value, label, mapper) {
  const ids = new Set(); const result = list(value ?? [], label, mapper);
  result.forEach((item) => {
    if(ids.has(item.id)) throw new TypeError(`Duplicate ${label} ID: ${item.id}`); ids.add(item.id);
  });
  return result.sort((left, right) => left.id.localeCompare(right.id));
}
function scalarMap(value, label) {
  plainObject(value ?? {}, label); noRawSecrets(value, label);
  const result = {};
  Object.entries(value ?? {}).sort(([a], [b]) => a.localeCompare(b)).forEach(([key, child]) => {
    platformValue(key, `${label} key`);
    if(!['string', 'number', 'boolean'].includes(typeof child) ||
        (typeof child === 'number' && !Number.isFinite(child))) throw new TypeError(
      `${label}.${key} must be a scalar value.`
    );
    result[key] = child;
  });
  return immutable(result);
}

export function validateAPIRef(input, label='API reference', schemas=REF_SCHEMAS) {
  plainObject(input, label); noRawSecrets(input, label);
  const schema = text(input.schema, `${label} schema`);
  if(!schemas.has(schema)) throw new TypeError(`${label} schema is unsupported.`);
  const identity = schema === 'cdeadmin.resource-ref.v1' ? input.canonical :
    schema === 'cdeadmin.asset-ref.v1' ? `${input.projectId}/${input.assetId}` : input.id;
  text(identity, `${label} identity`);
  if(schema === 'cdeadmin.credential-ref.v1') exact(input,
    ['schema', 'id', 'providerId', 'scope', 'displayName'], label);
  return immutable({...input});
}
export function apiReferenceKey(input) {
  const ref = validateAPIRef(input);
  if(ref.schema === 'cdeadmin.resource-ref.v1') return `resource:${ref.canonical}`;
  if(ref.schema === 'cdeadmin.asset-ref.v1') return `asset:${ref.projectId}/${ref.assetId}`;
  return `${ref.schema}:${ref.id}`;
}

function validateExtension(input) {
  plainObject(input, 'API extension'); exact(input, ['id', 'name', 'value'], 'API extension');
  const name = text(input.name, 'API extension name');
  if(!name.startsWith('x-')) throw new TypeError('API extension names must begin with x-.');
  noRawSecrets(input.value, `API extension ${name}`);
  return immutable({id: text(input.id ?? name, 'API extension ID'), name,
    value: input.value ?? null});
}
function extensions(value) { return unique(value ?? [], 'API extension', validateExtension); }

export function validateServerEnvironment(input) {
  plainObject(input, 'API server'); noRawSecrets(input, 'API server');
  exact(input, ['schema', 'id', 'name', 'environment', 'urlTemplate', 'protocol',
    'credentialRef', 'variables', 'tlsPolicyRef', 'description', 'extensions'], 'API server');
  return immutable({schema: 'cdeadmin.api-server.v1', id: text(input.id, 'API server ID'),
    name: text(input.name ?? input.id, 'API server name'),
    environment: text(input.environment, 'API server environment'),
    urlTemplate: text(input.urlTemplate, 'API server URL template'),
    protocol: text(input.protocol ?? 'https', 'API server protocol'),
    credentialRef: input.credentialRef ? validateAPIRef(input.credentialRef,
      'API server credential', new Set(['cdeadmin.credential-ref.v1'])) : null,
    variables: scalarMap(input.variables ?? {}, 'API server variables'),
    tlsPolicyRef: input.tlsPolicyRef ? validateAPIRef(input.tlsPolicyRef,
      'API server TLS policy', new Set(['cdeadmin.asset-ref.v1'])) : null,
    description: String(input.description ?? ''), extensions: extensions(input.extensions)});
}

export function validateDataBinding(input) {
  plainObject(input, 'API data binding'); noRawSecrets(input, 'API data binding');
  exact(input, ['schema', 'id', 'type', 'targetRef', 'mode', 'queryAssetRef', 'command',
    'inputMap', 'outputMap', 'nativeDetails', 'extensions'], 'API data binding');
  if(!DATA_BINDING_TYPES.includes(input.type)) throw new TypeError('API binding type is invalid.');
  if(!['read', 'write', 'read_write', 'invoke', 'publish', 'subscribe'].includes(input.mode)) {
    throw new TypeError('API binding mode is invalid.');
  }
  return immutable({schema: 'cdeadmin.api-binding.v1', id: text(input.id, 'API binding ID'),
    type: input.type, targetRef: validateAPIRef(input.targetRef, 'API binding target'),
    mode: input.mode, queryAssetRef: input.queryAssetRef ? validateAPIRef(input.queryAssetRef,
      'API query asset', new Set(['cdeadmin.asset-ref.v1'])) : null,
    command: text(input.command, 'API provider command', 8192, true),
    inputMap: object(input.inputMap, 'API binding input map'),
    outputMap: object(input.outputMap, 'API binding output map'),
    nativeDetails: object(input.nativeDetails, 'API binding native details'),
    extensions: extensions(input.extensions)});
}

function validateParameter(input) {
  plainObject(input, 'API parameter'); noRawSecrets(input, 'API parameter');
  exact(input, ['schema', 'id', 'name', 'location', 'required', 'description',
    'schemaRef', 'definition', 'extensions'], 'API parameter');
  if(!['path', 'query', 'header', 'cookie', 'body', 'message_header'].includes(input.location)) {
    throw new TypeError('API parameter location is invalid.');
  }
  return immutable({schema: 'cdeadmin.api-parameter.v1', id: text(input.id, 'API parameter ID'),
    name: text(input.name, 'API parameter name'), location: input.location,
    required: Boolean(input.required), description: String(input.description ?? ''),
    schemaRef: text(input.schemaRef, 'API parameter schema reference', 4096, true),
    definition: object(input.definition, 'API parameter definition'),
    extensions: extensions(input.extensions)});
}
function validateResponse(input) {
  plainObject(input, 'API response'); noRawSecrets(input, 'API response');
  exact(input, ['schema', 'id', 'status', 'description', 'schemaRef', 'headers',
    'examples', 'extensions'], 'API response');
  return immutable({schema: 'cdeadmin.api-response.v1', id: text(input.id, 'API response ID'),
    status: text(input.status, 'API response status'), description: String(input.description ?? ''),
    schemaRef: text(input.schemaRef, 'API response schema reference', 4096, true),
    headers: object(input.headers, 'API response headers'),
    examples: object(input.examples, 'API response examples'), extensions: extensions(input.extensions)});
}

export function validateOperation(input) {
  plainObject(input, 'API operation'); noRawSecrets(input, 'API operation');
  exact(input, ['schema', 'id', 'name', 'method', 'path', 'action', 'summary', 'description',
    'parameters', 'requestSchemaRef', 'responses', 'binding', 'securityRequirementIds',
    'policyIds', 'tags', 'deprecated', 'idempotent', 'callbacks', 'extensions'], 'API operation');
  const method = text(input.method ?? input.action, 'API operation method').toUpperCase();
  if(!HTTP_METHODS.has(method) && !method.startsWith('RPC:')) throw new TypeError(
    'API operation method/action is invalid.'
  );
  const path = text(input.path, 'API operation path/address');
  if(HTTP_METHODS.has(method) && !path.startsWith('/')) throw new TypeError(
    'HTTP operation paths must begin with /.');
  return immutable({schema: 'cdeadmin.api-operation.v1', id: text(input.id, 'API operation ID'),
    name: text(input.name ?? input.id, 'API operation name'), method, path,
    summary: String(input.summary ?? ''), description: String(input.description ?? ''),
    parameters: unique(input.parameters ?? [], 'API parameter', validateParameter),
    requestSchemaRef: text(input.requestSchemaRef, 'API request schema reference', 4096, true),
    responses: unique(input.responses ?? [], 'API response', validateResponse),
    binding: input.binding ? validateDataBinding(input.binding) : null,
    securityRequirementIds: [...new Set(list(input.securityRequirementIds ?? [],
      'API security requirement IDs', (item) => text(item, 'API security requirement ID')))].sort(),
    policyIds: [...new Set(list(input.policyIds ?? [], 'API policy IDs',
      (item) => text(item, 'API policy ID')))].sort(),
    tags: [...new Set(list(input.tags ?? [], 'API operation tags',
      (item) => text(item, 'API operation tag')))].sort(),
    deprecated: Boolean(input.deprecated), idempotent: input.idempotent === undefined ?
      ['GET', 'HEAD', 'OPTIONS', 'TRACE', 'PUT', 'DELETE'].includes(method) : Boolean(input.idempotent),
    callbacks: object(input.callbacks, 'API operation callbacks'),
    extensions: extensions(input.extensions)});
}

export function validateSchemaDefinition(input) {
  plainObject(input, 'API schema definition'); noRawSecrets(input, 'API schema definition');
  exact(input, ['schema', 'id', 'name', 'kind', 'definition', 'fields', 'physicalBindings',
    'description', 'extensions'], 'API schema definition');
  const fields = unique(input.fields ?? [], 'API schema field', (field) => {
    plainObject(field, 'API schema field'); exact(field,
      ['id', 'name', 'definition', 'required', 'description'], 'API schema field');
    return immutable({id: text(field.id, 'API schema field ID'),
      name: text(field.name, 'API schema field name'),
      definition: object(field.definition, 'API schema field definition'),
      required: Boolean(field.required), description: String(field.description ?? '')});
  });
  return immutable({schema: 'cdeadmin.api-schema.v1', id: text(input.id, 'API schema ID'),
    name: text(input.name ?? input.id, 'API schema name'),
    kind: text(input.kind ?? 'object', 'API schema kind'),
    definition: object(input.definition, 'API schema definition metadata'), fields,
    physicalBindings: list(input.physicalBindings ?? [], 'API schema physical bindings',
      (item) => validateAPIRef(item, 'API schema physical binding')),
    description: String(input.description ?? ''), extensions: extensions(input.extensions)});
}

export function validateMessage(input) {
  plainObject(input, 'API message'); noRawSecrets(input, 'API message');
  exact(input, ['schema', 'id', 'name', 'payloadSchemaRef', 'headerSchemaRef', 'correlationId',
    'contentType', 'bindings', 'extensions'], 'API message');
  return immutable({schema: 'cdeadmin.api-message.v1', id: text(input.id, 'API message ID'),
    name: text(input.name ?? input.id, 'API message name'),
    payloadSchemaRef: text(input.payloadSchemaRef, 'API message payload schema reference', 4096, true),
    headerSchemaRef: text(input.headerSchemaRef, 'API message header schema reference', 4096, true),
    correlationId: text(input.correlationId, 'API message correlation ID', 4096, true),
    contentType: text(input.contentType ?? 'application/json', 'API message content type'),
    bindings: object(input.bindings, 'API message bindings'), extensions: extensions(input.extensions)});
}

export function validateChannel(input) {
  plainObject(input, 'API channel'); noRawSecrets(input, 'API channel');
  exact(input, ['schema', 'id', 'name', 'address', 'serverIds', 'messageIds', 'operations',
    'bindings', 'extensions'], 'API channel');
  return immutable({schema: 'cdeadmin.api-channel.v1', id: text(input.id, 'API channel ID'),
    name: text(input.name ?? input.id, 'API channel name'),
    address: text(input.address, 'API channel address'),
    serverIds: [...new Set(list(input.serverIds ?? [], 'API channel server IDs',
      (item) => text(item, 'API channel server ID')))].sort(),
    messageIds: [...new Set(list(input.messageIds ?? [], 'API channel message IDs',
      (item) => text(item, 'API channel message ID')))].sort(),
    operations: object(input.operations, 'API channel operations'),
    bindings: object(input.bindings, 'API channel bindings'), extensions: extensions(input.extensions)});
}

export function validateSecurity(input) {
  plainObject(input, 'API security requirement'); noRawSecrets(input, 'API security requirement');
  exact(input, ['schema', 'id', 'name', 'type', 'scheme', 'location', 'parameterName',
    'flows', 'scopes', 'credentialRef', 'description', 'extensions'], 'API security requirement');
  return immutable({schema: 'cdeadmin.api-security.v1', id: text(input.id, 'API security ID'),
    name: text(input.name ?? input.id, 'API security name'), type: text(input.type, 'API security type'),
    scheme: text(input.scheme, 'API security scheme', 1024, true),
    location: text(input.location, 'API security location', 128, true),
    parameterName: text(input.parameterName, 'API security parameter name', 1024, true),
    flows: object(input.flows, 'API security flows'),
    scopes: scalarMap(input.scopes ?? {}, 'API security scopes'),
    credentialRef: input.credentialRef ? validateAPIRef(input.credentialRef,
      'API security credential', new Set(['cdeadmin.credential-ref.v1'])) : null,
    description: String(input.description ?? ''), extensions: extensions(input.extensions)});
}

function validatePolicy(input) {
  plainObject(input, 'API policy'); noRawSecrets(input, 'API policy');
  exact(input, ['schema', 'id', 'name', 'type', 'config', 'extensions'], 'API policy');
  return immutable({schema: 'cdeadmin.api-policy.v1', id: text(input.id, 'API policy ID'),
    name: text(input.name ?? input.id, 'API policy name'), type: text(input.type, 'API policy type'),
    config: object(input.config, 'API policy configuration'), extensions: extensions(input.extensions)});
}
function validateTest(input) {
  plainObject(input, 'API test'); noRawSecrets(input, 'API test');
  exact(input, ['schema', 'id', 'name', 'operationId', 'channelId', 'environmentId', 'mode',
    'parameters', 'headers', 'body', 'expected', 'sensitiveHeaderNames', 'extensions'], 'API test');
  if(!['mock', 'example', 'live'].includes(input.mode)) throw new TypeError('API test mode is invalid.');
  return immutable({schema: 'cdeadmin.api-test.v1', id: text(input.id, 'API test ID'),
    name: text(input.name ?? input.id, 'API test name'),
    operationId: text(input.operationId, 'API test operation ID', 1024, true),
    channelId: text(input.channelId, 'API test channel ID', 1024, true),
    environmentId: text(input.environmentId, 'API test environment ID', 1024, true), mode: input.mode,
    parameters: scalarMap(input.parameters ?? {}, 'API test parameters'),
    headers: scalarMap(input.headers ?? {}, 'API test headers'), body: object(input.body, 'API test body'),
    expected: object(input.expected, 'API test expectation'),
    sensitiveHeaderNames: [...new Set(list(input.sensitiveHeaderNames ?? [],
      'API sensitive header names', (item) => text(item, 'API sensitive header name')))].sort(),
    extensions: extensions(input.extensions)});
}
function validateDeployment(input) {
  plainObject(input, 'API deployment binding'); noRawSecrets(input, 'API deployment binding');
  exact(input, ['schema', 'id', 'name', 'environment', 'targetRef', 'gatewayProfile',
    'credentialRef', 'config', 'extensions'], 'API deployment binding');
  return immutable({schema: 'cdeadmin.api-deployment.v1', id: text(input.id, 'API deployment ID'),
    name: text(input.name ?? input.id, 'API deployment name'),
    environment: text(input.environment, 'API deployment environment'),
    targetRef: validateAPIRef(input.targetRef, 'API deployment target'),
    gatewayProfile: text(input.gatewayProfile, 'API deployment gateway profile'),
    credentialRef: input.credentialRef ? validateAPIRef(input.credentialRef,
      'API deployment credential', new Set(['cdeadmin.credential-ref.v1'])) : null,
    config: object(input.config, 'API deployment config'), extensions: extensions(input.extensions)});
}

export function createAPIContent(input={}) {
  plainObject(input, 'API asset'); noRawSecrets(input, 'API asset');
  exact(input, ['schema', 'schemaVersion', 'moduleId', 'profile', 'externalSpecVersion', 'info',
    'servers', 'operations', 'channels', 'messages', 'schemas', 'security', 'policies', 'tests',
    'deploymentBindings', 'externalReferences', 'extensions'], 'API asset');
  const profile = input.profile ?? 'openapi_http';
  if(!API_PROFILES.includes(profile)) throw new TypeError('API profile is invalid.');
  const content = {schema: API_ASSET_SCHEMA, schemaVersion: 1, moduleId: API_MODULE_ID, profile,
    externalSpecVersion: text(input.externalSpecVersion, 'External API specification version', 64, true),
    info: object(input.info, 'API information'),
    servers: unique(input.servers ?? [], 'API server', validateServerEnvironment),
    operations: unique(input.operations ?? [], 'API operation', validateOperation),
    channels: unique(input.channels ?? [], 'API channel', validateChannel),
    messages: unique(input.messages ?? [], 'API message', validateMessage),
    schemas: unique(input.schemas ?? [], 'API schema', validateSchemaDefinition),
    security: unique(input.security ?? [], 'API security', validateSecurity),
    policies: unique(input.policies ?? [], 'API policy', validatePolicy),
    tests: unique(input.tests ?? [], 'API test', validateTest),
    deploymentBindings: unique(input.deploymentBindings ?? [], 'API deployment', validateDeployment),
    externalReferences: unique(input.externalReferences ?? [], 'API external reference', (item) => {
      plainObject(item, 'API external reference'); noRawSecrets(item, 'API external reference');
      exact(item, ['id', 'uri', 'provenance', 'contentDigest'], 'API external reference');
      return immutable({id: text(item.id, 'API external reference ID'),
        uri: text(item.uri, 'API external reference URI'),
        provenance: object(item.provenance, 'API external reference provenance'),
        contentDigest: text(item.contentDigest, 'API external reference digest', 256, true)});
    }), extensions: extensions(input.extensions)};
  return immutable(content);
}

function canonical(value) {
  if(Array.isArray(value)) return value.map(canonical);
  if(!value || typeof value !== 'object') return value;
  return Object.fromEntries(Object.keys(value).sort().map((key) => [key, canonical(value[key])]));
}
export function serializeAPIContent(input) { return JSON.stringify(canonical(createAPIContent(input)), null, 2); }
export function apiAssetRequest({name, path, expectedVersion=0, content}) {
  const canonicalContent = createAPIContent(content);
  return {asset_type: API_ASSET_TYPE, schema_name: API_ASSET_TYPE, schema_version: 1,
    name: text(name || canonicalContent.info.title || 'API definition', 'API asset name', 256),
    path: text(path || 'apis/api.json', 'API asset path', 1024), expected_version: expectedVersion,
    content: canonicalContent, metadata: {profile: canonicalContent.profile,
      externalSpecVersion: canonicalContent.externalSpecVersion},
    dependency_references: [], resource_bindings: canonicalContent.operations
      .map((item) => item.binding?.targetRef).filter(Boolean), source_control_eligible: true,
    editor_capable: true, viewer_capable: true, validation_state: 'unknown', validation_details: []};
}
