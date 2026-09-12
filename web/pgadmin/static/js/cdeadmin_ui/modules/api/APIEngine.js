/////////////////////////////////////////////////////////////
// API format, validation, generation and resolver behavior.
/////////////////////////////////////////////////////////////

import yaml from 'js-yaml';
import {immutable, plainObject, platformValue} from '../../platform/serviceUtils';
import {
  API_PROFILES, createAPIContent, validateAPIRef, validateOperation,
} from './contracts';

const METHOD_KEYS = ['get', 'put', 'post', 'delete', 'options', 'head', 'patch', 'trace'];
const SECRET_VALUE_KEY = /(?:password|passwd|client.?secret|access.?token|refresh.?token|authorization|api.?key)/i;
const SAFE_DESCRIPTOR_KEY = /^(?:tokenUrl|passwordFormat)$/i;

function rejectSecretValues(value, path='document', schemaProperties=false) {
  if(Array.isArray(value)) return value.forEach((item, index) => rejectSecretValues(
    item, `${path}[${index}]`, false));
  if(!value || typeof value !== 'object') return;
  Object.entries(value).forEach(([key, child]) => {
    const descriptor = schemaProperties && child && typeof child === 'object' && !Array.isArray(child);
    if(SECRET_VALUE_KEY.test(key) && !SAFE_DESCRIPTOR_KEY.test(key) && !descriptor) throw new TypeError(
      `Raw secret material is forbidden at ${path}.${key}.`
    );
    rejectSecretValues(child, `${path}.${key}`, key === 'properties');
  });
}
function parseSource(source) {
  let document = source;
  if(typeof source === 'string') {
    if(new TextEncoder().encode(source).length > 16 * 1024 * 1024) throw new TypeError(
      'API source exceeds the 16 MiB import limit.');
    try { document = yaml.load(source, {json: true}); } catch(error) {
      throw new TypeError(`API source is invalid: ${error.message}`);
    }
  }
  plainObject(document, 'API source'); rejectSecretValues(document); return document;
}
function stableId(prefix, value) {
  const text = String(value || prefix).trim().toLowerCase().replace(/[^a-z0-9._:-]+/g, '-');
  return `${prefix}-${text || 'item'}`.slice(0, 240);
}
function extensionPairs(object={}) {
  return Object.entries(object).filter(([key]) => key.startsWith('x-')).map(([name, value]) =>
    ({id: name, name, value}));
}
function internalSource(document) {
  return {id: 'x-cdeadmin-import-source', name: 'x-cdeadmin-import-source',
    value: JSON.stringify(document)};
}
function schemaEntry(name, value={}) {
  const required = new Set(value.required ?? []); const properties = value.properties ?? {};
  return {id: stableId('schema', name), name, kind: value.type ?? 'object',
    definition: {source: JSON.stringify(Object.fromEntries(Object.entries(value)
      .filter(([key]) => key !== 'properties' && key !== 'required')))},
    fields: Object.entries(properties).map(([field, definition]) => ({id: stableId(name, field),
      name: field, definition: {source: JSON.stringify(definition)}, required: required.has(field),
      description: definition?.description ?? ''})), description: value.description ?? '',
    physicalBindings: [], extensions: extensionPairs(value)};
}
function securityEntry(name, value={}) {
  const flows = {};
  Object.entries(value.flows ?? {}).forEach(([flow, definition]) => {
    flows[flow] = {...definition};
    if(Object.hasOwn(flows[flow], 'tokenUrl')) {
      flows[flow].credentialEndpoint = flows[flow].tokenUrl; delete flows[flow].tokenUrl;
    }
  });
  return {id: stableId('security', name), name, type: value.type ?? 'unknown',
    scheme: value.scheme ?? null, location: value.in ?? null, parameterName: value.name ?? null,
    flows: {source: JSON.stringify(flows)}, scopes: {}, credentialRef: null,
    description: value.description ?? '',
    extensions: extensionPairs(value)};
}
function schemaRef(value) { return value?.$ref ?? value?.schema?.$ref ?? null; }
function responseEntries(responses={}) {
  return Object.entries(responses).map(([status, value]) => ({id: stableId('response', status),
    status, description: value?.description ?? '',
    schemaRef: schemaRef(value?.content?.['application/json']) ?? null,
    headers: value?.headers ?? {}, examples: value?.content?.['application/json']?.examples ?? {},
    extensions: extensionPairs(value)}));
}
function parameterEntries(parameters=[]) {
  return parameters.filter((item) => !item.$ref).map((item) => ({
    id: stableId('parameter', `${item.in}-${item.name}`), name: item.name,
    location: item.in, required: item.required ?? item.in === 'path',
    description: item.description ?? '', schemaRef: item.schema?.$ref ?? null,
    definition: item.schema?.$ref ? {} : {source: JSON.stringify(item.schema ?? {})},
    extensions: extensionPairs(item)}));
}
function openAPIOperations(document) {
  const operations = [];
  Object.entries(document.paths ?? {}).forEach(([path, item]) => METHOD_KEYS.forEach((method) => {
    if(!item?.[method]) return; const operation = item[method];
    operations.push({id: operation.operationId ?? stableId(method, path), name: operation.operationId ??
      `${method.toUpperCase()} ${path}`, method: method.toUpperCase(), path,
    summary: operation.summary ?? '', description: operation.description ?? '',
    parameters: parameterEntries([...(item.parameters ?? []), ...(operation.parameters ?? [])]),
    requestSchemaRef: schemaRef(operation.requestBody?.content?.['application/json']),
    responses: responseEntries(operation.responses), binding: null,
    securityRequirementIds: (operation.security ?? []).flatMap(Object.keys)
      .map((name) => stableId('security', name)), policyIds: [], tags: operation.tags ?? [],
    deprecated: operation.deprecated, callbacks: {source: JSON.stringify(operation.callbacks ?? {})},
    extensions: extensionPairs(operation)});
  })); return operations;
}

export function importOpenAPI(source, provenance={}) {
  const document = parseSource(source); const version = String(document.openapi ?? '');
  if(!version.startsWith('3.2')) throw new TypeError(
    `OpenAPI 3.2 is required; received ${version || 'no declared version'}.`
  );
  return createAPIContent({profile: 'openapi_http', externalSpecVersion: version,
    info: {...(document.info ?? {}), importProvenance: provenance},
    servers: (document.servers ?? []).map((server, index) => ({id: stableId('server', index),
      name: server.description || `Server ${index + 1}`, environment: server['x-environment'] ?? 'unspecified',
      urlTemplate: server.url, protocol: String(server.url).split(':')[0], variables: Object.fromEntries(
        Object.entries(server.variables ?? {}).map(([key, item]) => [key, item.default ?? ''])),
      credentialRef: null, tlsPolicyRef: null, description: server.description ?? '',
      extensions: extensionPairs(server)})), operations: openAPIOperations(document), channels: [], messages: [],
    schemas: Object.entries(document.components?.schemas ?? {}).map(([name, value]) => schemaEntry(name, value)),
    security: Object.entries(document.components?.securitySchemes ?? {})
      .map(([name, value]) => securityEntry(name, value)), policies: [], tests: [], deploymentBindings: [],
    externalReferences: [], extensions: [...extensionPairs(document), internalSource(document)]});
}

export function importAsyncAPI(source, provenance={}) {
  const document = parseSource(source); const version = String(document.asyncapi ?? '');
  if(!version.startsWith('3.1')) throw new TypeError(
    `AsyncAPI 3.1 is required; received ${version || 'no declared version'}.`
  );
  const messages = Object.entries(document.components?.messages ?? {}).map(([name, value]) => ({
    id: stableId('message', name), name, payloadSchemaRef: schemaRef(value.payload),
    headerSchemaRef: schemaRef(value.headers), correlationId: value.correlationId?.location ?? null,
    contentType: value.contentType ?? document.defaultContentType ?? 'application/json',
    bindings: value.bindings ?? {}, extensions: extensionPairs(value)}));
  const channels = Object.entries(document.channels ?? {}).map(([name, value]) => ({
    id: stableId('channel', name), name, address: value.address ?? name,
    serverIds: (value.servers ?? []).map((id) => stableId('server', String(id).replace('#/servers/', ''))),
    messageIds: Object.keys(value.messages ?? {}).map((id) => stableId('message', id)),
    operations: {}, bindings: value.bindings ?? {}, extensions: extensionPairs(value)}));
  return createAPIContent({profile: 'asyncapi_event', externalSpecVersion: version,
    info: {...(document.info ?? {}), importProvenance: provenance},
    servers: Object.entries(document.servers ?? {}).map(([name, server]) => ({id: stableId('server', name),
      name, environment: server['x-environment'] ?? 'unspecified', urlTemplate: server.host,
      protocol: server.protocol, variables: {}, credentialRef: null, tlsPolicyRef: null,
      description: server.description ?? '', extensions: extensionPairs(server)})), operations: [], channels,
    messages, schemas: Object.entries(document.components?.schemas ?? {})
      .map(([name, value]) => schemaEntry(name, value)),
    security: Object.entries(document.components?.securitySchemes ?? {})
      .map(([name, value]) => securityEntry(name, value)), policies: [], tests: [], deploymentBindings: [],
    externalReferences: [], extensions: [...extensionPairs(document), internalSource(document)]});
}

function parsedDefinition(schema) {
  const base = schema.definition?.source ? JSON.parse(schema.definition.source) : {...schema.definition};
  if(schema.fields.length) {
    base.type ??= schema.kind; base.properties = {}; const required = [];
    schema.fields.forEach((field) => {
      base.properties[field.name] = field.definition?.source ? JSON.parse(field.definition.source) :
        {...field.definition}; if(field.required) required.push(field.name);
    });
    if(required.length) base.required = required;
  }
  schema.extensions.forEach((item) => { base[item.name] = item.value; }); return base;
}
function sourceBase(content) {
  const source = content.extensions.find((item) => item.name === 'x-cdeadmin-import-source')?.value;
  return source ? JSON.parse(source) : {};
}
function applyExtensions(target, values) {
  values.filter((item) => item.name !== 'x-cdeadmin-import-source')
    .forEach((item) => { target[item.name] = item.value; }); return target;
}

export function exportOpenAPI(input) {
  const content = createAPIContent(input);
  if(content.profile !== 'openapi_http') throw new TypeError('OpenAPI export requires openapi_http profile.');
  const document = sourceBase(content); document.openapi = content.externalSpecVersion ?? '3.2.0';
  document.info = {...content.info}; delete document.info.importProvenance;
  document.paths = {};
  content.operations.forEach((operation) => {
    const target = document.paths[operation.path] ??= {}; const value = {operationId: operation.id,
      summary: operation.summary, description: operation.description, tags: operation.tags,
      deprecated: operation.deprecated, parameters: operation.parameters.map((item) => ({name: item.name,
        in: item.location, required: item.required, description: item.description,
        schema: item.schemaRef ? {$ref: item.schemaRef} : JSON.parse(item.definition.source ?? '{}')})),
      responses: Object.fromEntries(operation.responses.map((item) => [item.status,
        {description: item.description, ...(item.schemaRef ? {content: {'application/json':
        {schema: {$ref: item.schemaRef}, examples: item.examples}}} : {}), headers: item.headers}]))};
    if(operation.requestSchemaRef) value.requestBody = {content: {'application/json':
      {schema: {$ref: operation.requestSchemaRef}}}};
    if(operation.securityRequirementIds.length) value.security = operation.securityRequirementIds
      .map((id) => ({[content.security.find((item) => item.id === id)?.name ?? id]: []}));
    target[operation.method.toLowerCase()] = applyExtensions(value, operation.extensions);
  });
  document.servers = content.servers.map((item) => applyExtensions({url: item.urlTemplate,
    description: item.description, variables: Object.fromEntries(Object.entries(item.variables)
      .map(([key, value]) => [key, {default: value}]))}, item.extensions));
  document.components ??= {}; document.components.schemas = Object.fromEntries(content.schemas
    .map((item) => [item.name, parsedDefinition(item)]));
  document.components.securitySchemes = Object.fromEntries(content.security.map((item) => {
    const flows = item.flows.source ? JSON.parse(item.flows.source) :
      JSON.parse(JSON.stringify(item.flows)); Object.values(flows).forEach((flow) => {
      if(Object.hasOwn(flow, 'credentialEndpoint')) { flow.tokenUrl = flow.credentialEndpoint;
        delete flow.credentialEndpoint; }
    });
    return [item.name, applyExtensions({type: item.type, ...(item.scheme ? {scheme: item.scheme} : {}),
      ...(item.location ? {in: item.location} : {}), ...(item.parameterName ? {name: item.parameterName} : {}),
      ...(Object.keys(flows).length ? {flows} : {})}, item.extensions)];
  }));
  return immutable(applyExtensions(document, content.extensions));
}

export function exportAsyncAPI(input) {
  const content = createAPIContent(input);
  if(content.profile !== 'asyncapi_event') throw new TypeError('AsyncAPI export requires asyncapi_event profile.');
  const document = sourceBase(content); document.asyncapi = content.externalSpecVersion ?? '3.1.0';
  document.info = {...content.info}; delete document.info.importProvenance;
  document.servers = Object.fromEntries(content.servers.map((item) => [item.name,
    applyExtensions({host: item.urlTemplate, protocol: item.protocol, description: item.description},
      item.extensions)]));
  document.channels = Object.fromEntries(content.channels.map((item) => [item.name,
    applyExtensions({address: item.address,
      messages: Object.fromEntries(item.messageIds.map((id) => [id, {$ref: `#/components/messages/${id}`} ])),
      bindings: item.bindings}, item.extensions)]));
  document.components ??= {}; document.components.schemas = Object.fromEntries(content.schemas
    .map((item) => [item.name, parsedDefinition(item)]));
  document.components.messages = Object.fromEntries(content.messages.map((item) => [item.name,
    applyExtensions({contentType: item.contentType,
      ...(item.payloadSchemaRef ? {payload: {$ref: item.payloadSchemaRef}} : {}),
      ...(item.headerSchemaRef ? {headers: {$ref: item.headerSchemaRef}} : {}),
      ...(item.correlationId ? {correlationId: {location: item.correlationId}} : {}),
      bindings: item.bindings}, item.extensions)]));
  return immutable(applyExtensions(document, content.extensions));
}

export function validateAPIDefinition(input) {
  const content = createAPIContent(input); const errors = []; const warnings = [];
  const schemaIds = new Set(content.schemas.flatMap((item) => [item.id, `#/components/schemas/${item.name}`]));
  const securityIds = new Set(content.security.map((item) => item.id));
  const policyIds = new Set(content.policies.map((item) => item.id));
  const serverIds = new Set(content.servers.map((item) => item.id));
  const messageIds = new Set(content.messages.map((item) => item.id));
  const operationIds = new Set(content.operations.map((item) => item.id));
  content.operations.forEach((operation) => {
    [operation.requestSchemaRef, ...operation.parameters.map((item) => item.schemaRef),
      ...operation.responses.map((item) => item.schemaRef)].filter(Boolean).forEach((ref) => {
      if(!schemaIds.has(ref)) errors.push(`Operation ${operation.id} references unknown schema ${ref}.`);
    });
    operation.securityRequirementIds.forEach((id) => {
      if(!securityIds.has(id)) errors.push(`Operation ${operation.id} references unknown security ${id}.`);
    });
    operation.policyIds.forEach((id) => {
      if(!policyIds.has(id)) errors.push(`Operation ${operation.id} references unknown policy ${id}.`);
    });
    if(!operation.responses.length && content.profile === 'openapi_http') warnings.push(
      `Operation ${operation.id} has no response definition.`);
  });
  content.channels.forEach((channel) => {
    channel.serverIds.forEach((id) => { if(!serverIds.has(id)) errors.push(
      `Channel ${channel.id} references unknown server ${id}.`); });
    channel.messageIds.forEach((id) => { if(!messageIds.has(id)) errors.push(
      `Channel ${channel.id} references unknown message ${id}.`); });
  });
  content.tests.forEach((test) => {
    if(test.operationId && !operationIds.has(test.operationId)) errors.push(
      `Test ${test.id} references unknown operation ${test.operationId}.`);
    if(test.environmentId && !serverIds.has(test.environmentId)) errors.push(
      `Test ${test.id} references unknown environment ${test.environmentId}.`);
  });
  if(content.profile === 'openapi_http' && !String(content.externalSpecVersion ?? '3.2').startsWith('3.2')) {
    errors.push('OpenAPI profile requires a 3.2 external specification version.');
  }
  if(content.profile === 'asyncapi_event' && !String(content.externalSpecVersion ?? '3.1').startsWith('3.1')) {
    errors.push('AsyncAPI profile requires a 3.1 external specification version.');
  }
  return immutable({valid: !errors.length, errors: [...new Set(errors)].sort(),
    warnings: [...new Set(warnings)].sort()});
}

export function generateCRUDDraft(input) {
  plainObject(input, 'CRUD generator input');
  const required = ['resourceRef', 'allowedOperations', 'keyFields', 'exposedFields',
    'paginationPolicy', 'writeValidationPolicy', 'securityRequirementIds'];
  required.forEach((field) => { if(input[field] == null) throw new TypeError(
    `CRUD generation requires ${field}.`); });
  const resource = validateAPIRef(input.resourceRef, 'CRUD resource');
  if(!Array.isArray(input.allowedOperations) || !input.allowedOperations.length) throw new TypeError(
    'CRUD generation requires at least one explicitly allowed operation.');
  if(!Array.isArray(input.keyFields) || !input.keyFields.length) throw new TypeError(
    'CRUD generation requires key identity.');
  if(!Array.isArray(input.exposedFields) || !input.exposedFields.length) throw new TypeError(
    'CRUD generation requires explicit field exposure.');
  if(!Array.isArray(input.securityRequirementIds) || !input.securityRequirementIds.length) {
    throw new TypeError('CRUD generation requires an explicit security requirement.');
  }
  plainObject(input.paginationPolicy, 'CRUD pagination policy');
  plainObject(input.writeValidationPolicy, 'CRUD write validation policy');
  const name = platformValue(input.resourceName ?? resource.canonical, 'CRUD resource name');
  const base = input.basePath ?? `/${name.toLowerCase().replace(/[^a-z0-9]+/g, '-')}`;
  const matrix = {list: ['GET', base, 'read'], read: ['GET', `${base}/{key}`, 'read'],
    create: ['POST', base, 'write'], update: ['PUT', `${base}/{key}`, 'write'],
    delete: ['DELETE', `${base}/{key}`, 'write']};
  const operations = input.allowedOperations.map((action) => {
    if(!matrix[action]) throw new TypeError(`CRUD operation ${action} is unsupported.`);
    const [method, path, mode] = matrix[action]; return validateOperation({id: `${stableId('crud', name)}-${action}`,
      name: `${action} ${name}`, method, path, summary: `Draft ${action} operation`, parameters: [],
      responses: [{id: 'response-default', status: 'default', description: 'Generated draft response',
        headers: {}, examples: {}, extensions: []}], binding: {id: `${stableId('binding', name)}-${action}`,
        type: mode === 'read' ? 'provider_resource_read' : 'provider_command', targetRef: resource,
        mode, command: mode === 'write' ? action : null, inputMap: {fields: input.exposedFields,
          keys: input.keyFields}, outputMap: {}, nativeDetails: {}, extensions: []},
      securityRequirementIds: input.securityRequirementIds, policyIds: [], tags: ['generated-draft'],
      callbacks: {}, extensions: []});
  });
  return immutable({schema: 'cdeadmin.api-crud-draft.v1', deployed: false, resourceRef: resource,
    paginationPolicy: {...input.paginationPolicy}, writeValidationPolicy: {...input.writeValidationPolicy},
    operations});
}

export class ExternalReferenceResolver {
  constructor({fetcher, allowedSchemes=['https:'], allowedHosts=[], maxDepth=8,
    maxBytes=4 * 1024 * 1024, offline=false, cache=new Map()}={}) {
    this.fetcher = fetcher; this.policy = {allowedSchemes, allowedHosts, maxDepth, maxBytes, offline};
    this.cache = cache;
  }
  async resolve(uri, {baseURI, stack=[]}={}) {
    const absolute = new URL(uri, baseURI).href; const url = new URL(absolute);
    if(stack.includes(absolute)) throw new Error(`External reference cycle detected: ${absolute}`);
    if(stack.length >= this.policy.maxDepth) throw new Error('External reference depth limit exceeded.');
    if(!this.policy.allowedSchemes.includes(url.protocol)) throw new Error(
      `External reference scheme is not allowed: ${url.protocol}`);
    if(this.policy.allowedHosts.length && !this.policy.allowedHosts.includes(url.hostname)) throw new Error(
      `External reference host is not allowed: ${url.hostname}`);
    if(this.cache.has(absolute)) return this.cache.get(absolute);
    if(this.policy.offline) throw new Error(`External reference is unavailable in offline mode: ${absolute}`);
    if(typeof this.fetcher !== 'function') throw new Error('External reference fetcher is unavailable.');
    const response = await this.fetcher(absolute); const body = typeof response === 'string' ? response :
      response?.body;
    if(typeof body !== 'string') throw new Error('External reference fetch returned no document.');
    if(new TextEncoder().encode(body).length > this.policy.maxBytes) throw new Error(
      'External reference size limit exceeded.');
    const document = parseSource(body); const nested = [];
    const visit = async (value) => {
      if(Array.isArray(value)) { for(const child of value) await visit(child); return; }
      if(!value || typeof value !== 'object') return;
      for(const [key, child] of Object.entries(value)) {
        if(key === '$ref' && typeof child === 'string' && !child.startsWith('#')) nested.push(
          await this.resolve(child, {baseURI: absolute, stack: [...stack, absolute]}));
        else await visit(child);
      }
    };
    await visit(document); const result = immutable({uri: absolute, document, nested,
      provenance: {resolvedAt: new Date().toISOString(), scheme: url.protocol, host: url.hostname}});
    this.cache.set(absolute, result); return result;
  }
}

export function profileExporter(profile) {
  if(!API_PROFILES.includes(profile)) throw new TypeError('API profile is invalid.');
  if(profile === 'openapi_http') return exportOpenAPI;
  if(profile === 'asyncapi_event') return exportAsyncAPI;
  throw new Error(`${profile} export requires its separate profile adapter.`);
}
