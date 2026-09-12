/////////////////////////////////////////////////////////////
// ML / Vector canonical contracts and deterministic source.
/////////////////////////////////////////////////////////////

import {immutable, noRawSecrets, plainObject, platformValue} from '../../platform/serviceUtils';

export const ML_VECTOR_MODULE_ID = 'cdeadmin.ml_vector';
export const ML_VECTOR_SERVICE_ID = 'cdeadmin.ml_vector.runtime';
export const ML_VECTOR_ASSET_TYPE = 'cdeadmin.ml_vector.v1';
export const ML_VECTOR_ASSET_SCHEMA = 'cdeadmin.ml-vector.asset.v1';
export const VECTOR_METRICS = Object.freeze(['cosine', 'inner_product', 'euclidean_l2',
  'manhattan_l1', 'hamming', 'jaccard', 'provider_custom']);
export const ML_VECTOR_STATES = Object.freeze(['empty', 'loading', 'ready', 'stale', 'partial',
  'permission_denied', 'disconnected', 'validation_error', 'runtime_failure', 'read_only',
  'background_task_active']);
const REF_SCHEMAS = new Set(['cdeadmin.resource-ref.v1', 'cdeadmin.asset-ref.v1',
  'cdeadmin.external-ref.v1', 'cdeadmin.credential-ref.v1', 'cdeadmin.result-ref.v1']);

function exact(input, fields, label) {
  const unknown = Object.keys(input).filter((field) => !fields.includes(field));
  if(unknown.length) throw new TypeError(`${label} contains unsupported field ${unknown[0]}.`);
}
function text(value, label, maximum=8192, optional=false) {
  if(optional && (value === undefined || value === null || value === '')) return null;
  return platformValue(value, label, maximum);
}
function structuredText(value, label, maximum=16384, optional=false) {
  if(optional && (value === undefined || value === null || value === '')) return null;
  const invalidControl = typeof value === 'string' && [...value].some((character) => {
    const code = character.charCodeAt(0); return code < 32 && ![9, 10, 13].includes(code);
  });
  if(typeof value !== 'string' || !value || value.length > maximum || invalidControl) throw new TypeError(
    `${label} is invalid.`);
  return value;
}
function object(value, label) {
  const result = plainObject(value ?? {}, label); noRawSecrets(result, label);
  return immutable({...result});
}
function list(value, label, mapper, maximum=10000) {
  if(!Array.isArray(value) || value.length > maximum) throw new TypeError(
    `${label} must contain no more than ${maximum} items.`);
  return value.map(mapper);
}
function strings(value, label) {
  return [...new Set(list(value ?? [], label, (item) => text(item, `${label} value`)))].sort();
}
function unique(value, label, mapper) {
  const ids = new Set(); const result = list(value ?? [], label, mapper);
  result.forEach((item) => {
    if(ids.has(item.id)) throw new TypeError(`Duplicate ${label} ID: ${item.id}`); ids.add(item.id);
  });
  return result.sort((left, right) => left.id.localeCompare(right.id));
}
function positive(value, label, {optional=false, maximum=1000000}={}) {
  if(optional && value == null) return null;
  if(!Number.isInteger(value) || value < 1 || value > maximum) throw new TypeError(
    `${label} must be an integer from 1 to ${maximum}.`);
  return value;
}
function finite(value, label) {
  if(typeof value !== 'number' || !Number.isFinite(value)) throw new TypeError(`${label} must be finite.`);
  return value;
}
function validateExtension(input) {
  plainObject(input, 'ML / Vector extension'); exact(input, ['id', 'name', 'value'],
    'ML / Vector extension'); const name = text(input.name, 'ML / Vector extension name');
  if(!name.startsWith('x-')) throw new TypeError('ML / Vector extension names must begin with x-.');
  noRawSecrets(input.value, `ML / Vector extension ${name}`);
  return immutable({id: text(input.id ?? name, 'ML / Vector extension ID'), name,
    value: input.value ?? null});
}
function extensions(value) { return unique(value ?? [], 'ML / Vector extension', validateExtension); }

export function validateMLVectorRef(input, label='ML / Vector reference', schemas=REF_SCHEMAS) {
  plainObject(input, label); noRawSecrets(input, label); const schema = text(input.schema, `${label} schema`);
  if(!schemas.has(schema)) throw new TypeError(`${label} schema is unsupported.`);
  const identity = schema === 'cdeadmin.resource-ref.v1' ? input.canonical :
    schema === 'cdeadmin.asset-ref.v1' ? `${input.projectId}/${input.assetId}` : input.id;
  text(identity, `${label} identity`);
  if(schema === 'cdeadmin.credential-ref.v1') exact(input,
    ['schema', 'id', 'providerId', 'scope', 'displayName'], label);
  return immutable({...input});
}
export function mlVectorReferenceKey(input) {
  const ref = validateMLVectorRef(input);
  if(ref.schema === 'cdeadmin.resource-ref.v1') return `resource:${ref.canonical}`;
  if(ref.schema === 'cdeadmin.asset-ref.v1') return `asset:${ref.projectId}/${ref.assetId}`;
  return `${ref.schema}:${ref.id}`;
}

export function validateEmbeddingModelRef(input) {
  plainObject(input, 'Embedding model reference'); noRawSecrets(input, 'Embedding model reference');
  exact(input, ['schema', 'modelId', 'versionId', 'providerId', 'external', 'providerIdentity',
    'credentialRef', 'nativeDetails'], 'Embedding model reference');
  if(input.schema && input.schema !== 'cdeadmin.embedding-model-ref.v1') throw new TypeError(
    'Embedding model reference schema is invalid.');
  const external = Boolean(input.external);
  return immutable({schema: 'cdeadmin.embedding-model-ref.v1', modelId: text(input.modelId, 'Embedding model ID'),
    versionId: text(input.versionId, 'Embedding model version ID'),
    providerId: text(input.providerId, 'Embedding model provider ID'), external,
    providerIdentity: text(input.providerIdentity, 'Embedding model provider identity', 4096, !external),
    credentialRef: input.credentialRef ? validateMLVectorRef(input.credentialRef,
      'Embedding model credential', new Set(['cdeadmin.credential-ref.v1'])) : null,
    nativeDetails: object(input.nativeDetails, 'Embedding model native details')});
}

function validateEmbeddingField(input) {
  plainObject(input, 'Embedding field'); noRawSecrets(input, 'Embedding field');
  exact(input, ['schema', 'id', 'name', 'sourceFields', 'modelRef', 'dimensions', 'targetField',
    'normalizationTemplate', 'nativeDetails'], 'Embedding field');
  if(input.schema && input.schema !== 'cdeadmin.embedding-field.v1') throw new TypeError(
    'Embedding field schema is invalid.');
  return immutable({schema: 'cdeadmin.embedding-field.v1', id: text(input.id, 'Embedding field ID'),
    name: text(input.name ?? input.id, 'Embedding field name'),
    sourceFields: strings(input.sourceFields, 'Embedding source fields'),
    modelRef: validateEmbeddingModelRef(input.modelRef),
    dimensions: positive(input.dimensions, 'Embedding dimensions'),
    targetField: text(input.targetField, 'Embedding target field'),
    normalizationTemplate: structuredText(input.normalizationTemplate,
      'Embedding normalization template', 16384, true),
    nativeDetails: object(input.nativeDetails, 'Embedding field native details')});
}

export function validateVectorIndex(input) {
  plainObject(input, 'Vector index plan'); noRawSecrets(input, 'Vector index plan');
  exact(input, ['schema', 'id', 'name', 'fieldId', 'algorithm', 'normalizedMetric', 'nativeMetric',
    'dimensions', 'providerConfig', 'buildMode', 'description', 'extensions'], 'Vector index plan');
  if(input.schema && input.schema !== 'cdeadmin.vector-index-plan.v1') throw new TypeError(
    'Vector index schema is invalid.');
  if(!VECTOR_METRICS.includes(input.normalizedMetric)) throw new TypeError('Vector metric is invalid.');
  if(input.normalizedMetric === 'provider_custom' && !input.nativeMetric) throw new TypeError(
    'Provider-custom vector metrics require the provider-native metric name.');
  return immutable({schema: 'cdeadmin.vector-index-plan.v1', id: text(input.id, 'Vector index ID'),
    name: text(input.name ?? input.id, 'Vector index name'), fieldId: text(input.fieldId, 'Vector field ID'),
    algorithm: text(input.algorithm, 'Vector index algorithm'), normalizedMetric: input.normalizedMetric,
    nativeMetric: text(input.nativeMetric, 'Provider-native vector metric', 1024, true),
    dimensions: positive(input.dimensions, 'Vector index dimensions'),
    providerConfig: object(input.providerConfig, 'Vector index provider configuration'),
    buildMode: text(input.buildMode, 'Vector index build mode'),
    description: String(input.description ?? ''), extensions: extensions(input.extensions)});
}

export function validateVectorDesign(input) {
  plainObject(input, 'Vector design'); noRawSecrets(input, 'Vector design');
  exact(input, ['schema', 'id', 'name', 'resourceRef', 'dimensions', 'fields', 'indexes',
    'description', 'nativeDetails', 'extensions'], 'Vector design');
  if(input.schema && input.schema !== 'cdeadmin.vector-design.v1') throw new TypeError(
    'Vector design schema is invalid.');
  const fields = unique(input.fields ?? [], 'Embedding field', validateEmbeddingField);
  const indexes = unique(input.indexes ?? [], 'Vector index', validateVectorIndex);
  const fieldIds = new Set(fields.map((field) => field.id));
  indexes.forEach((index) => { if(!fieldIds.has(index.fieldId)) throw new TypeError(
    `Vector index ${index.id} references unknown field ${index.fieldId}.`); });
  return immutable({schema: 'cdeadmin.vector-design.v1', id: text(input.id, 'Vector design ID'),
    name: text(input.name ?? input.id, 'Vector design name'),
    resourceRef: validateMLVectorRef(input.resourceRef, 'Vector design resource',
      new Set(['cdeadmin.resource-ref.v1'])),
    dimensions: positive(input.dimensions, 'Vector design dimensions'), fields, indexes,
    description: String(input.description ?? ''),
    nativeDetails: object(input.nativeDetails, 'Vector design native details'),
    extensions: extensions(input.extensions)});
}

export function validateEmbeddingPipeline(input) {
  plainObject(input, 'Embedding pipeline'); noRawSecrets(input, 'Embedding pipeline');
  exact(input, ['schema', 'id', 'name', 'resourceRef', 'sourceFields', 'normalizationTemplate',
    'modelRef', 'outputDimensions', 'targetField', 'batching', 'rateLimitPolicy', 'dataSharingPolicy',
    'description', 'extensions'], 'Embedding pipeline');
  if(input.schema && input.schema !== 'cdeadmin.embedding-pipeline.v1') throw new TypeError(
    'Embedding pipeline schema is invalid.');
  const policy = object(input.dataSharingPolicy, 'Embedding data-sharing policy');
  if(typeof policy.containsSensitiveData !== 'boolean' ||
      typeof policy.approvedForSensitiveData !== 'boolean') throw new TypeError(
    'Embedding data-sharing policy requires explicit sensitivity decisions.');
  return immutable({schema: 'cdeadmin.embedding-pipeline.v1', id: text(input.id, 'Embedding pipeline ID'),
    name: text(input.name ?? input.id, 'Embedding pipeline name'),
    resourceRef: validateMLVectorRef(input.resourceRef, 'Embedding source resource',
      new Set(['cdeadmin.resource-ref.v1'])), sourceFields: strings(input.sourceFields,
      'Embedding pipeline source fields'), normalizationTemplate: structuredText(input.normalizationTemplate,
      'Embedding normalization template', 16384, true),
    modelRef: validateEmbeddingModelRef(input.modelRef),
    outputDimensions: positive(input.outputDimensions, 'Embedding output dimensions'),
    targetField: text(input.targetField, 'Embedding target field'),
    batching: object(input.batching, 'Embedding batching policy'),
    rateLimitPolicy: object(input.rateLimitPolicy, 'Embedding rate-limit policy'),
    dataSharingPolicy: policy, description: String(input.description ?? ''),
    extensions: extensions(input.extensions)});
}

function validateModelVersion(input) {
  plainObject(input, 'Model version'); noRawSecrets(input, 'Model version');
  exact(input, ['schema', 'id', 'version', 'artifactRef', 'originatingRunRef', 'datasetRefs',
    'evaluationRefs', 'deploymentRefs', 'contentDigest', 'description', 'nativeDetails'], 'Model version');
  if(input.schema && input.schema !== 'cdeadmin.model-version.v1') throw new TypeError(
    'Model version schema is invalid.');
  return immutable({schema: 'cdeadmin.model-version.v1', id: text(input.id, 'Model version ID'),
    version: text(input.version, 'Model version'), artifactRef: validateMLVectorRef(
      input.artifactRef, 'Model artifact', new Set(['cdeadmin.asset-ref.v1', 'cdeadmin.external-ref.v1'])),
    originatingRunRef: input.originatingRunRef ? validateMLVectorRef(input.originatingRunRef,
      'Originating model run', new Set(['cdeadmin.result-ref.v1'])) : null,
    datasetRefs: list(input.datasetRefs ?? [], 'Model dataset references', (item) =>
      validateMLVectorRef(item, 'Model dataset reference')),
    evaluationRefs: list(input.evaluationRefs ?? [], 'Model evaluation references', (item) =>
      validateMLVectorRef(item, 'Model evaluation reference')),
    deploymentRefs: list(input.deploymentRefs ?? [], 'Model deployment references', (item) =>
      validateMLVectorRef(item, 'Model deployment reference')),
    contentDigest: text(input.contentDigest, 'Model content digest'),
    description: String(input.description ?? ''),
    nativeDetails: object(input.nativeDetails, 'Model version native details')});
}

export function validateModelEntry(input) {
  plainObject(input, 'Model entry'); noRawSecrets(input, 'Model entry');
  exact(input, ['schema', 'id', 'name', 'description', 'tags', 'aliases', 'versionTags',
    'versions', 'extensions'], 'Model entry');
  if(input.schema && input.schema !== 'cdeadmin.model-entry.v1') throw new TypeError(
    'Model entry schema is invalid.');
  const versions = unique(input.versions ?? [], 'Model version', validateModelVersion);
  const versionIds = new Set(versions.map((item) => item.id));
  const versionTags = unique(input.versionTags ?? [], 'Model version tag set', (item) => {
    plainObject(item, 'Model version tag set'); exact(item, ['id', 'versionId', 'tags'],
      'Model version tag set'); const versionId = text(item.versionId, 'Tagged model version ID');
    if(!versionIds.has(versionId)) throw new TypeError(`Unknown tagged model version ${versionId}.`);
    return immutable({id: text(item.id, 'Model version tag set ID'), versionId,
      tags: strings(item.tags, 'Model version tags')});
  });
  const aliases = unique(input.aliases ?? [], 'Model alias', (item) => {
    plainObject(item, 'Model alias'); exact(item, ['id', 'name', 'versionId'], 'Model alias');
    const versionId = text(item.versionId, 'Aliased model version ID');
    if(!versionIds.has(versionId)) throw new TypeError(`Unknown aliased model version ${versionId}.`);
    return immutable({id: text(item.id, 'Model alias ID'), name: text(item.name, 'Model alias name'), versionId});
  });
  return immutable({schema: 'cdeadmin.model-entry.v1', id: text(input.id, 'Model ID'),
    name: text(input.name ?? input.id, 'Model name'), description: String(input.description ?? ''),
    tags: strings(input.tags, 'Model tags'), aliases, versionTags, versions,
    extensions: extensions(input.extensions)});
}

function validateExperiment(input) {
  plainObject(input, 'ML experiment'); noRawSecrets(input, 'ML experiment');
  exact(input, ['schema', 'id', 'name', 'inputRefs', 'parameters', 'metricDefinitions',
    'artifactRefs', 'datasetRevisionRef', 'description', 'extensions'], 'ML experiment');
  if(input.schema && input.schema !== 'cdeadmin.ml-experiment.v1') throw new TypeError(
    'ML experiment schema is invalid.');
  return immutable({schema: 'cdeadmin.ml-experiment.v1', id: text(input.id, 'ML experiment ID'),
    name: text(input.name ?? input.id, 'ML experiment name'), inputRefs: list(input.inputRefs ?? [],
      'ML experiment inputs', (item) => validateMLVectorRef(item, 'ML experiment input')),
    parameters: object(input.parameters, 'ML experiment parameters'),
    metricDefinitions: object(input.metricDefinitions, 'ML experiment metric definitions'),
    artifactRefs: list(input.artifactRefs ?? [], 'ML experiment artifacts', (item) =>
      validateMLVectorRef(item, 'ML experiment artifact')),
    datasetRevisionRef: validateMLVectorRef(input.datasetRevisionRef, 'ML experiment dataset revision'),
    description: String(input.description ?? ''), extensions: extensions(input.extensions)});
}

export function validateEvaluationCase(input) {
  plainObject(input, 'Vector evaluation case'); noRawSecrets(input, 'Vector evaluation case');
  exact(input, ['schema', 'id', 'name', 'vectorDesignId', 'indexId', 'querySource', 'filters', 'k',
    'metrics', 'groundTruthRef', 'datasetRevisionRef', 'expected', 'description', 'extensions'],
  'Vector evaluation case');
  if(input.schema && input.schema !== 'cdeadmin.vector-evaluation-case.v1') throw new TypeError(
    'Vector evaluation case schema is invalid.');
  const metrics = strings(input.metrics, 'Vector evaluation metrics');
  if(metrics.length && (!input.groundTruthRef || !input.datasetRevisionRef)) throw new TypeError(
    'Vector quality metrics require ground truth and dataset revision references.');
  return immutable({schema: 'cdeadmin.vector-evaluation-case.v1', id: text(input.id, 'Vector evaluation case ID'),
    name: text(input.name ?? input.id, 'Vector evaluation case name'),
    vectorDesignId: text(input.vectorDesignId, 'Evaluation vector design ID'),
    indexId: text(input.indexId, 'Evaluation vector index ID'),
    querySource: object(input.querySource, 'Evaluation query source'),
    filters: object(input.filters, 'Evaluation filters'), k: positive(input.k, 'Evaluation k', {maximum: 10000}),
    metrics, groundTruthRef: validateMLVectorRef(input.groundTruthRef, 'Evaluation ground truth'),
    datasetRevisionRef: validateMLVectorRef(input.datasetRevisionRef, 'Evaluation dataset revision'),
    expected: object(input.expected, 'Evaluation expectations'),
    description: String(input.description ?? ''), extensions: extensions(input.extensions)});
}

function validateDeployment(input) {
  plainObject(input, 'ML deployment binding'); noRawSecrets(input, 'ML deployment binding');
  exact(input, ['schema', 'id', 'name', 'environment', 'endpointRef', 'modelId', 'modelVersionId',
    'vectorDesignId', 'indexId', 'credentialRef', 'policy', 'config', 'extensions'],
  'ML deployment binding');
  if(input.schema && input.schema !== 'cdeadmin.ml-deployment.v1') throw new TypeError(
    'ML deployment schema is invalid.');
  return immutable({schema: 'cdeadmin.ml-deployment.v1', id: text(input.id, 'ML deployment ID'),
    name: text(input.name ?? input.id, 'ML deployment name'),
    environment: text(input.environment, 'ML deployment environment'),
    endpointRef: validateMLVectorRef(input.endpointRef, 'ML deployment endpoint'),
    modelId: text(input.modelId, 'ML deployment model ID'),
    modelVersionId: text(input.modelVersionId, 'ML deployment model version ID'),
    vectorDesignId: text(input.vectorDesignId, 'ML deployment vector design ID'),
    indexId: text(input.indexId, 'ML deployment vector index ID'),
    credentialRef: input.credentialRef ? validateMLVectorRef(input.credentialRef,
      'ML deployment credential', new Set(['cdeadmin.credential-ref.v1'])) : null,
    policy: object(input.policy, 'ML deployment policy'), config: object(input.config,
      'ML deployment config'), extensions: extensions(input.extensions)});
}

export function createMLVectorContent(input={}) {
  plainObject(input, 'ML / Vector asset'); noRawSecrets(input, 'ML / Vector asset');
  exact(input, ['schema', 'schemaVersion', 'moduleId', 'name', 'description', 'vectorDesigns',
    'embeddingPipelines', 'modelEntries', 'experiments', 'evaluationCases', 'deploymentBindings',
    'extensions'], 'ML / Vector asset');
  const content = {schema: ML_VECTOR_ASSET_SCHEMA, schemaVersion: 1, moduleId: ML_VECTOR_MODULE_ID,
    name: String(input.name ?? ''), description: String(input.description ?? ''),
    vectorDesigns: unique(input.vectorDesigns ?? [], 'Vector design', validateVectorDesign),
    embeddingPipelines: unique(input.embeddingPipelines ?? [], 'Embedding pipeline',
      validateEmbeddingPipeline), modelEntries: unique(input.modelEntries ?? [], 'Model entry',
      validateModelEntry), experiments: unique(input.experiments ?? [], 'ML experiment', validateExperiment),
    evaluationCases: unique(input.evaluationCases ?? [], 'Vector evaluation case', validateEvaluationCase),
    deploymentBindings: unique(input.deploymentBindings ?? [], 'ML deployment', validateDeployment),
    extensions: extensions(input.extensions)};
  const designs = new Map(content.vectorDesigns.map((item) => [item.id, item]));
  content.evaluationCases.forEach((item) => {
    const design = designs.get(item.vectorDesignId); if(!design) throw new TypeError(
      `Evaluation ${item.id} references unknown vector design ${item.vectorDesignId}.`);
    if(!design.indexes.some((index) => index.id === item.indexId)) throw new TypeError(
      `Evaluation ${item.id} references unknown vector index ${item.indexId}.`);
  });
  const models = new Map(content.modelEntries.map((item) => [item.id, item]));
  content.deploymentBindings.forEach((item) => {
    const model = models.get(item.modelId); if(!model?.versions.some((version) =>
      version.id === item.modelVersionId)) throw new TypeError(
      `Deployment ${item.id} references unknown model version ${item.modelVersionId}.`);
    const design = designs.get(item.vectorDesignId); if(!design?.indexes.some((index) =>
      index.id === item.indexId)) throw new TypeError(
      `Deployment ${item.id} references unknown vector index ${item.indexId}.`);
  });
  return immutable(content);
}

function canonical(value) {
  if(Array.isArray(value)) return value.map(canonical);
  if(!value || typeof value !== 'object') return value;
  return Object.fromEntries(Object.keys(value).sort().map((key) => [key, canonical(value[key])]));
}
export function serializeMLVectorContent(input) {
  return JSON.stringify(canonical(createMLVectorContent(input)), null, 2);
}
export function mlVectorAssetRequest({name, path, expectedVersion=0, content}) {
  const value = createMLVectorContent(content); return {asset_type: ML_VECTOR_ASSET_TYPE,
    schema_name: ML_VECTOR_ASSET_TYPE, schema_version: 1,
    name: text(name || value.name || 'ML / Vector design', 'ML / Vector asset name', 256),
    path: text(path || 'ml-vector/design.json', 'ML / Vector asset path', 1024),
    expected_version: expectedVersion, content: value, metadata: {moduleId: ML_VECTOR_MODULE_ID},
    dependency_references: [], resource_bindings: value.vectorDesigns.map((item) => item.resourceRef),
    source_control_eligible: true, editor_capable: true, viewer_capable: true,
    validation_state: 'unknown', validation_details: []};
}

export function validateMetricResult(value, label='Metric value') {
  return finite(value, label);
}
