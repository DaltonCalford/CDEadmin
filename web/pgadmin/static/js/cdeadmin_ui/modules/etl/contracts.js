/////////////////////////////////////////////////////////////
// ETL Designer canonical contracts and deterministic source.
/////////////////////////////////////////////////////////////

import {immutable, noRawSecrets, plainObject, platformValue} from '../../platform/serviceUtils';

export const ETL_MODULE_ID = 'cdeadmin.etl';
export const ETL_ASSET_TYPE = 'cdeadmin.etl.v1';
export const ETL_ASSET_SCHEMA = 'cdeadmin.etl.asset.v1';
export const ETL_NODE_FAMILIES = Object.freeze([
  'source', 'sink', 'filter', 'project', 'map', 'join', 'lookup',
  'aggregate', 'sort', 'union', 'split', 'deduplicate', 'window',
  'script', 'quality_gate', 'checkpoint', 'branch', 'merge',
  'custom_provider',
]);
export const ETL_PORT_MODES = Object.freeze(['batch', 'stream', 'either']);
export const ETL_SCHEMA_STATES = Object.freeze([
  'known', 'partial', 'unknown', 'incompatible',
]);
export const ETL_PIPELINE_MODES = Object.freeze([
  'batch', 'micro_batch', 'streaming', 'hybrid',
]);
export const ETL_EXECUTION_LOCATIONS = Object.freeze([
  'source_pushdown', 'cdeadmin_runtime', 'target_pushdown',
  'external_runtime',
]);
export const ETL_ERROR_ACTIONS = Object.freeze([
  'retry', 'dead_letter', 'reject', 'stop',
]);
export const ETL_DELIVERY_GUARANTEES = Object.freeze([
  'exactly_once', 'at_least_once', 'at_most_once', 'best_effort', 'unknown',
]);
export const ETL_STATES = Object.freeze([
  'empty', 'loading', 'ready', 'stale', 'partial', 'permission_denied',
  'disconnected', 'validation_error', 'runtime_failure', 'read_only',
  'background_task_active',
]);

const REFERENCE_SCHEMAS = new Set([
  'cdeadmin.resource-ref.v1', 'cdeadmin.asset-ref.v1',
  'cdeadmin.external-ref.v1', 'cdeadmin.credential-ref.v1',
]);

function optionalText(value, label, maximum=4096) {
  if(value === undefined || value === null || value === '') return null;
  return platformValue(value, label, maximum);
}

function object(value, label, maximum=1024 * 1024) {
  const result = plainObject(value ?? {}, label);
  noRawSecrets(result, label);
  const encoded = JSON.stringify(result);
  if(encoded.length > maximum) throw new TypeError(`${label} exceeds its size limit.`);
  return immutable({...result});
}

function array(value, label, mapper, maximum=10000) {
  if(!Array.isArray(value) || value.length > maximum) throw new TypeError(
    `${label} must be an array containing no more than ${maximum} items.`
  );
  return value.map(mapper);
}

function unique(values, label, mapper) {
  const result = array(values ?? [], label, mapper); const ids = new Set();
  result.forEach((item) => {
    if(ids.has(item.id)) throw new TypeError(`Duplicate ${label}: ${item.id}`);
    ids.add(item.id);
  });
  return result.sort((left, right) => left.id.localeCompare(right.id));
}

function stringList(values, label, maximum=1000) {
  return [...new Set(array(values ?? [], label,
    (value) => platformValue(value, `${label} item`), maximum))].sort();
}

function finite(value, label, {minimum, integer=false}={}) {
  const number = Number(value);
  if(!Number.isFinite(number) || (integer && !Number.isInteger(number)) ||
      (minimum !== undefined && number < minimum)) {
    throw new TypeError(`${label} is invalid.`);
  }
  return number;
}

export function validateETLReference(input, label='ETL reference') {
  plainObject(input, label); noRawSecrets(input, label);
  const schema = platformValue(input.schema, `${label} schema`);
  if(!REFERENCE_SCHEMAS.has(schema)) throw new TypeError(`${label} schema is unsupported.`);
  if(schema === 'cdeadmin.resource-ref.v1') {
    platformValue(input.canonical, `${label} canonical identity`, 8192);
  } else if(schema === 'cdeadmin.asset-ref.v1') {
    platformValue(input.projectId, `${label} project ID`);
    platformValue(input.assetId, `${label} asset ID`);
  } else if(schema === 'cdeadmin.credential-ref.v1') {
    platformValue(input.scheme, `${label} credential scheme`);
    platformValue(input.id, `${label} credential ID`);
  } else platformValue(input.id, `${label} identity`, 8192);
  return immutable({...input});
}

export function etlReferenceKey(input) {
  const ref = validateETLReference(input);
  if(ref.schema === 'cdeadmin.resource-ref.v1') return `resource:${ref.canonical}`;
  if(ref.schema === 'cdeadmin.asset-ref.v1') return `asset:${ref.projectId}/${ref.assetId}`;
  if(ref.schema === 'cdeadmin.credential-ref.v1') return `credential:${ref.scheme}/${ref.id}`;
  return `external:${ref.id}`;
}

export function validateSchemaField(input) {
  plainObject(input, 'ETL schema field'); noRawSecrets(input, 'ETL schema field');
  const nullable = input.nullable === undefined ? true : Boolean(input.nullable);
  return immutable({schema: 'cdeadmin.etl-schema-field.v1',
    id: platformValue(input.id, 'ETL schema field ID'),
    name: platformValue(input.name, 'ETL schema field name'),
    nativeType: optionalText(input.nativeType, 'ETL schema native type'),
    semanticType: optionalText(input.semanticType, 'ETL schema semantic type'),
    nullable,
    precision: input.precision === undefined || input.precision === null ? null :
      finite(input.precision, 'ETL schema precision', {minimum: 0, integer: true}),
    scale: input.scale === undefined || input.scale === null ? null :
      finite(input.scale, 'ETL schema scale', {integer: true}),
    timezone: optionalText(input.timezone, 'ETL schema timezone'),
    encoding: optionalText(input.encoding, 'ETL schema encoding'),
    path: optionalText(input.path, 'ETL schema field path', 8192),
    nativeDetails: object(input.nativeDetails, 'ETL schema native details')});
}

export function validatePort(input) {
  plainObject(input, 'ETL port'); noRawSecrets(input, 'ETL port');
  const direction = platformValue(input.direction, 'ETL port direction');
  if(!['input', 'output'].includes(direction)) throw new TypeError('ETL port direction is invalid.');
  const mode = platformValue(input.mode, 'ETL port mode');
  if(!ETL_PORT_MODES.includes(mode)) throw new TypeError('ETL port mode is invalid.');
  const schemaState = platformValue(input.schemaState, 'ETL port schema state');
  if(!ETL_SCHEMA_STATES.includes(schemaState)) throw new TypeError(
    'ETL port schema state is invalid.'
  );
  const schemaRef = input.schemaRef ? validateETLReference(input.schemaRef,
    'ETL port schema reference') : null;
  return immutable({schema: 'cdeadmin.etl-port.v1',
    id: platformValue(input.id, 'ETL port ID'),
    name: platformValue(input.name ?? input.id, 'ETL port name'), direction, mode,
    schemaState, schemaRef,
    fields: unique(input.fields ?? [], 'ETL schema field', validateSchemaField),
    cardinality: optionalText(input.cardinality, 'ETL port cardinality'),
    ordering: stringList(input.ordering ?? [], 'ETL port ordering'),
    partitioning: object(input.partitioning, 'ETL port partitioning'),
    watermark: object(input.watermark, 'ETL port watermark'),
    nativeDetails: object(input.nativeDetails, 'ETL port native details')});
}

export function validateErrorRoute(input) {
  if(input === undefined || input === null) return null;
  plainObject(input, 'ETL error route'); noRawSecrets(input, 'ETL error route');
  const action = platformValue(input.action, 'ETL error action');
  if(!ETL_ERROR_ACTIONS.includes(action)) throw new TypeError('ETL error action is invalid.');
  const maximumAttempts = input.maximumAttempts === undefined ? 0 : finite(
    input.maximumAttempts, 'ETL error maximum attempts', {minimum: 0, integer: true}
  );
  if(maximumAttempts > 100) throw new TypeError('ETL error maximum attempts exceeds 100.');
  const targetRef = input.targetRef ? validateETLReference(input.targetRef,
    'ETL error-route target') : null;
  if(action === 'dead_letter' && targetRef?.schema !== 'cdeadmin.resource-ref.v1') {
    throw new TypeError('ETL dead-letter routes require a provider ResourceRef target.');
  }
  return immutable({schema: 'cdeadmin.etl-error-route.v1', action, maximumAttempts,
    targetRef,
    retryPolicy: object(input.retryPolicy, 'ETL retry policy'),
    nativeDetails: object(input.nativeDetails, 'ETL error-route native details')});
}

export function validatePipelineNode(input) {
  plainObject(input, 'ETL pipeline node'); noRawSecrets(input, 'ETL pipeline node');
  const kind = platformValue(input.kind, 'ETL pipeline node kind');
  if(!ETL_NODE_FAMILIES.includes(kind)) throw new TypeError(`Invalid ETL node family: ${kind}`);
  const executionPreference = input.executionPreference ?? 'cdeadmin_runtime';
  if(!ETL_EXECUTION_LOCATIONS.includes(executionPreference)) throw new TypeError(
    'ETL node execution preference is invalid.'
  );
  const resourceRef = input.resourceRef ? validateETLReference(input.resourceRef,
    'ETL node resource') : null;
  if(resourceRef && resourceRef.schema !== 'cdeadmin.resource-ref.v1') throw new TypeError(
    'ETL node resource must be a ResourceRef.'
  );
  return immutable({schema: 'cdeadmin.etl-node.v1',
    id: platformValue(input.id, 'ETL pipeline node ID'),
    name: platformValue(input.name, 'ETL pipeline node name'), kind,
    config: object(input.config, 'ETL pipeline node configuration'), resourceRef,
    ports: unique(input.ports ?? [], 'ETL port', validatePort),
    capabilityRequirements: stringList(input.capabilityRequirements ?? [],
      'ETL node capability requirements'), executionPreference,
    errorRoute: validateErrorRoute(input.errorRoute),
    checkpointEnabled: Boolean(input.checkpointEnabled),
    nativeDetails: object(input.nativeDetails, 'ETL pipeline node native details')});
}

export function validateSchemaMapping(input) {
  plainObject(input, 'ETL schema mapping'); noRawSecrets(input, 'ETL schema mapping');
  return immutable({schema: 'cdeadmin.etl-schema-mapping.v1',
    id: platformValue(input.id, 'ETL schema mapping ID'),
    source: platformValue(input.source, 'ETL mapping source', 8192),
    target: platformValue(input.target, 'ETL mapping target', 8192),
    sourceNativeType: optionalText(input.sourceNativeType, 'ETL source native type'),
    semanticType: optionalText(input.semanticType, 'ETL normalized semantic type'),
    targetNativeType: optionalText(input.targetNativeType, 'ETL target native type'),
    conversion: platformValue(input.conversion ?? 'identity', 'ETL mapping conversion', 8192),
    nullPolicy: platformValue(input.nullPolicy ?? 'preserve', 'ETL mapping null policy'),
    nullable: input.nullable === undefined ? true : Boolean(input.nullable),
    precision: input.precision === undefined || input.precision === null ? null :
      finite(input.precision, 'ETL mapping precision', {minimum: 0, integer: true}),
    scale: input.scale === undefined || input.scale === null ? null :
      finite(input.scale, 'ETL mapping scale', {integer: true}),
    timezone: optionalText(input.timezone, 'ETL mapping timezone'),
    encoding: optionalText(input.encoding, 'ETL mapping encoding'),
    lossy: Boolean(input.lossy),
    lossAcknowledged: Boolean(input.lossAcknowledged),
    nativeDetails: object(input.nativeDetails, 'ETL mapping native details')});
}

export function validatePipelineEdge(input) {
  plainObject(input, 'ETL pipeline edge'); noRawSecrets(input, 'ETL pipeline edge');
  const deliveryGuarantee = input.deliveryGuarantee ?? 'unknown';
  if(!ETL_DELIVERY_GUARANTEES.includes(deliveryGuarantee)) throw new TypeError(
    'ETL edge delivery guarantee is invalid.'
  );
  return immutable({schema: 'cdeadmin.etl-edge.v1',
    id: platformValue(input.id, 'ETL pipeline edge ID'),
    fromNodeId: platformValue(input.fromNodeId, 'ETL edge source node ID'),
    fromPort: platformValue(input.fromPort, 'ETL edge source port ID'),
    toNodeId: platformValue(input.toNodeId, 'ETL edge target node ID'),
    toPort: platformValue(input.toPort, 'ETL edge target port ID'),
    mappingPolicy: platformValue(input.mappingPolicy ?? 'explicit', 'ETL mapping policy'),
    mappings: unique(input.mappings ?? [], 'ETL schema mapping', validateSchemaMapping),
    deliveryGuarantee,
    partitioning: object(input.partitioning, 'ETL edge partitioning'),
    ordering: object(input.ordering, 'ETL edge ordering'),
    nativeDetails: object(input.nativeDetails, 'ETL edge native details')});
}

export function validateParameter(input) {
  plainObject(input, 'ETL parameter');
  const secret = Boolean(input.secret);
  if(secret && input.default !== undefined && input.default !== null) throw new TypeError(
    'Secret ETL parameters cannot contain a default value.'
  );
  const credentialRef = input.credentialRef ? validateETLReference(
    input.credentialRef, 'ETL parameter credential reference'
  ) : null;
  if(credentialRef && credentialRef.schema !== 'cdeadmin.credential-ref.v1') throw new TypeError(
    'ETL parameter credential reference must be a CredentialRef.'
  );
  if(secret && !credentialRef) throw new TypeError(
    'Secret ETL parameters require a CredentialRef.'
  );
  if(!secret) noRawSecrets(input, 'ETL parameter');
  else Object.entries(input).forEach(([key, value]) => {
    if(!['secret', 'credentialRef'].includes(key)) noRawSecrets({[key]: value}, 'ETL parameter');
  });
  return immutable({schema: 'cdeadmin.etl-parameter.v1',
    id: platformValue(input.id, 'ETL parameter ID'),
    name: platformValue(input.name ?? input.id, 'ETL parameter name'),
    type: platformValue(input.type, 'ETL parameter type'),
    required: Boolean(input.required), secret, credentialRef,
    default: secret ? null : (input.default ?? null),
    description: String(input.description ?? ''),
    nativeDetails: object(input.nativeDetails, 'ETL parameter native details')});
}

function validateBinding(input) {
  plainObject(input, 'ETL deployment binding'); noRawSecrets(input, 'ETL deployment binding');
  const resourceRef = validateETLReference(input.resourceRef, 'ETL deployment resource');
  if(resourceRef.schema !== 'cdeadmin.resource-ref.v1') throw new TypeError(
    'ETL deployment binding requires a provider ResourceRef.'
  );
  return immutable({schema: 'cdeadmin.etl-deployment-binding.v1',
    id: platformValue(input.id, 'ETL deployment binding ID'),
    nodeId: platformValue(input.nodeId, 'ETL deployment binding node ID'),
    resourceRef,
    nativeDetails: object(input.nativeDetails, 'ETL deployment binding native details')});
}

export function validateDeployment(input) {
  plainObject(input, 'ETL deployment'); noRawSecrets(input, 'ETL deployment');
  return immutable({schema: 'cdeadmin.etl-deployment.v1',
    id: platformValue(input.id, 'ETL deployment ID'),
    name: platformValue(input.name ?? input.id, 'ETL deployment name'),
    environment: platformValue(input.environment, 'ETL deployment environment'),
    bindings: unique(input.bindings ?? [], 'ETL deployment binding', validateBinding),
    parameterBindings: object(input.parameterBindings, 'ETL deployment parameter bindings'),
    resourceLimits: object(input.resourceLimits, 'ETL deployment resource limits'),
    nativeDetails: object(input.nativeDetails, 'ETL deployment native details')});
}

export function validateSchedule(input) {
  plainObject(input, 'ETL schedule'); noRawSecrets(input, 'ETL schedule');
  return immutable({schema: 'cdeadmin.etl-schedule.v1',
    id: platformValue(input.id, 'ETL schedule ID'),
    name: platformValue(input.name ?? input.id, 'ETL schedule name'),
    enabled: Boolean(input.enabled),
    trigger: platformValue(input.trigger, 'ETL schedule trigger'),
    expression: platformValue(input.expression, 'ETL schedule expression', 8192),
    timezone: platformValue(input.timezone ?? 'UTC', 'ETL schedule timezone'),
    deploymentId: platformValue(input.deploymentId, 'ETL schedule deployment ID'),
    dependencyRefs: array(input.dependencyRefs ?? [], 'ETL schedule dependency references',
      (item) => validateETLReference(item, 'ETL schedule dependency')),
    parameters: object(input.parameters, 'ETL schedule parameters'),
    nativeDetails: object(input.nativeDetails, 'ETL schedule native details')});
}

export function createETLContent(input={}) {
  plainObject(input, 'ETL asset content');
  // A boolean `secret` flag is authored metadata; all actual values remain forbidden.
  Object.entries(input).forEach(([key, value]) => {
    if(key !== 'parameters') noRawSecrets({[key]: value}, 'ETL asset content');
  });
  const mode = input.mode ?? 'batch';
  if(!ETL_PIPELINE_MODES.includes(mode)) throw new TypeError(`Invalid ETL pipeline mode: ${mode}`);
  const nodes = unique(input.nodes ?? [], 'ETL pipeline node', validatePipelineNode);
  const nodeIds = new Set(nodes.map((item) => item.id));
  const edges = unique(input.edges ?? [], 'ETL pipeline edge', validatePipelineEdge);
  edges.forEach((edge) => {
    const from = nodes.find((item) => item.id === edge.fromNodeId);
    const to = nodes.find((item) => item.id === edge.toNodeId);
    if(!from || !to) throw new TypeError(`ETL edge ${edge.id} references an unknown node.`);
    const fromPort = from.ports.find((item) => item.id === edge.fromPort);
    const toPort = to.ports.find((item) => item.id === edge.toPort);
    if(!fromPort || fromPort.direction !== 'output') throw new TypeError(
      `ETL edge ${edge.id} source port is missing or is not an output.`
    );
    if(!toPort || toPort.direction !== 'input') throw new TypeError(
      `ETL edge ${edge.id} target port is missing or is not an input.`
    );
  });
  const parameters = unique(input.parameters ?? [], 'ETL parameter', validateParameter);
  const deployments = unique(input.deployments ?? [], 'ETL deployment', validateDeployment);
  deployments.forEach((deployment) => deployment.bindings.forEach((binding) => {
    if(!nodeIds.has(binding.nodeId)) throw new TypeError(
      `ETL deployment ${deployment.id} binds unknown node ${binding.nodeId}.`
    );
  }));
  const deploymentIds = new Set(deployments.map((item) => item.id));
  const schedules = unique(input.schedules ?? [], 'ETL schedule', validateSchedule);
  schedules.forEach((schedule) => {
    if(!deploymentIds.has(schedule.deploymentId)) throw new TypeError(
      `ETL schedule ${schedule.id} references unknown deployment ${schedule.deploymentId}.`
    );
  });
  return immutable({schema: ETL_ASSET_SCHEMA, schemaVersion: 1,
    moduleId: ETL_MODULE_ID, name: String(input.name ?? ''),
    description: String(input.description ?? ''), mode, parameters, nodes, edges,
    deployments, schedules,
    tests: unique(input.tests ?? [], 'ETL test', (test) => {
      plainObject(test, 'ETL test'); noRawSecrets(test, 'ETL test');
      return immutable({id: platformValue(test.id, 'ETL test ID'),
        name: platformValue(test.name ?? test.id, 'ETL test name'),
        definition: object(test.definition, 'ETL test definition')});
    }),
    visualLayout: object(input.visualLayout, 'ETL visual layout'),
    extensions: object(input.extensions, 'ETL extensions')});
}

function canonical(value) {
  if(Array.isArray(value)) return value.map(canonical);
  if(!value || typeof value !== 'object') return value;
  return Object.fromEntries(Object.keys(value).sort().map((key) => [key, canonical(value[key])]));
}

export function exportETLAsset(input) {
  return `${JSON.stringify(canonical(createETLContent(input)), null, 2)}\n`;
}

export function importETLAsset(source) {
  if(typeof source !== 'string' || !source || source.length > 16 * 1024 * 1024) {
    throw new TypeError('ETL source is invalid or exceeds 16 MiB.');
  }
  try { return createETLContent(JSON.parse(source)); }
  catch(error) { throw new TypeError(`Invalid ETL source: ${error.message}`); }
}

export function etlAssetRequest({projectId, assetId, name, path,
  expectedVersion=0, content}) {
  const canonicalContent = createETLContent(content);
  const references = [
    ...canonicalContent.nodes.map((item) => item.resourceRef).filter(Boolean),
    ...canonicalContent.deployments.flatMap((item) => item.bindings.map(
      (binding) => binding.resourceRef)),
    ...canonicalContent.schedules.flatMap((item) => item.dependencyRefs),
    ...canonicalContent.nodes.flatMap((item) => item.ports.map((port) => port.schemaRef)
      .filter(Boolean)),
  ];
  const dependencies = [...new Map(references.filter((item) =>
    item.schema === 'cdeadmin.asset-ref.v1').map((item) => [etlReferenceKey(item), item])).values()];
  const resources = [...new Map(references.filter((item) =>
    item.schema === 'cdeadmin.resource-ref.v1').map((item) => [etlReferenceKey(item), item])).values()];
  return immutable({asset_type: ETL_ASSET_TYPE,
    name: platformValue(name, 'ETL asset name'), path,
    schema_name: ETL_ASSET_TYPE, schema_version: 1,
    expected_version: expectedVersion, content: canonicalContent,
    metadata: {moduleId: ETL_MODULE_ID, mode: canonicalContent.mode},
    dependency_references: dependencies, resource_bindings: resources,
    validation_state: 'valid', validation_details: [], projectId, assetId});
}
