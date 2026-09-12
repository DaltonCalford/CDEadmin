/////////////////////////////////////////////////////////////
// ML / Vector capability, index, search and evaluation engine.
/////////////////////////////////////////////////////////////

import {immutable, noRawSecrets, plainObject, platformValue} from '../../platform/serviceUtils';
import {createMLVectorContent, validateMetricResult, VECTOR_METRICS} from './contracts';

function exact(input, fields, label) {
  const unknown = Object.keys(input).filter((field) => !fields.includes(field));
  if(unknown.length) throw new TypeError(`${label} contains unsupported field ${unknown[0]}.`);
}
function strings(value, label) {
  if(!Array.isArray(value)) throw new TypeError(`${label} must be an array.`);
  return [...new Set(value.map((item) => platformValue(item, `${label} value`)))].sort();
}
function optionalBoolean(value, label) {
  if(value !== null && value !== undefined && typeof value !== 'boolean') throw new TypeError(
    `${label} must be boolean or unknown.`);
  return value ?? null;
}
function parameterSchema(input) {
  plainObject(input, 'Vector index parameter schema');
  exact(input, ['fields', 'additionalProperties'], 'Vector index parameter schema');
  if(input.additionalProperties !== false) throw new TypeError(
    'Vector index parameter schemas must reject unadvertised controls.');
  if(!Array.isArray(input.fields)) throw new TypeError('Vector index parameter schema requires fields[].');
  const ids = new Set(); const fields = input.fields.map((field) => {
    plainObject(field, 'Vector index parameter field'); exact(field,
      ['id', 'label', 'type', 'required', 'minimum', 'maximum', 'enum', 'default', 'description'],
      'Vector index parameter field'); const id = platformValue(field.id, 'Vector index parameter ID');
    if(ids.has(id)) throw new TypeError(`Duplicate vector index parameter ID: ${id}`); ids.add(id);
    if(!['integer', 'number', 'boolean', 'string'].includes(field.type)) throw new TypeError(
      `Vector index parameter ${id} has an invalid type.`);
    if(typeof field.required !== 'boolean') throw new TypeError(
      `Vector index parameter ${id} requires an explicit required flag.`);
    if(field.enum != null && (!Array.isArray(field.enum) || !field.enum.length)) throw new TypeError(
      `Vector index parameter ${id} enum is invalid.`);
    for(const bound of ['minimum', 'maximum']) if(field[bound] != null &&
        (typeof field[bound] !== 'number' || !Number.isFinite(field[bound]))) throw new TypeError(
      `Vector index parameter ${id} ${bound} must be finite.`);
    if(field.minimum != null && field.maximum != null && field.minimum > field.maximum) throw new TypeError(
      `Vector index parameter ${id} bounds are invalid.`);
    const normalized = {id, label: platformValue(field.label ?? id, 'Vector index parameter label'),
      type: field.type, required: field.required, minimum: field.minimum ?? null,
      maximum: field.maximum ?? null, enum: field.enum ? [...field.enum] : null,
      default: field.default ?? null, description: String(field.description ?? '')};
    if(field.default != null) typedParameter(normalized, field.default);
    return immutable(normalized);
  });
  return immutable({fields, additionalProperties: false});
}

export function validateVectorCapabilities(input) {
  plainObject(input, 'Vector capabilities'); noRawSecrets(input, 'Vector capabilities');
  exact(input, ['vector_storage', 'vector_dimensions', 'supported_metrics', 'supported_index_families',
    'filtering', 'hybrid_search', 'multi_vector', 'quantization', 'index_build_online',
    'max_dimensions', 'nativeDetails'], 'Vector capabilities');
  for(const field of ['vector_storage', 'vector_dimensions', 'filtering', 'hybrid_search', 'multi_vector']) {
    if(typeof input[field] !== 'boolean') throw new TypeError(`Vector capability ${field} must be boolean.`);
  }
  if(!Array.isArray(input.supported_metrics) || !Array.isArray(input.supported_index_families)) throw new TypeError(
    'Vector capabilities require metric and index-family arrays.');
  const metrics = input.supported_metrics.map((item) => {
    plainObject(item, 'Vector metric capability'); exact(item, ['normalized', 'native'],
      'Vector metric capability'); if(!VECTOR_METRICS.includes(item.normalized)) throw new TypeError(
      `Unknown normalized vector metric ${item.normalized}.`);
    return immutable({normalized: item.normalized,
      native: platformValue(item.native, 'Provider-native vector metric')});
  });
  const families = input.supported_index_families.map((item) => {
    plainObject(item, 'Vector index family'); exact(item,
      ['id', 'label', 'metrics', 'buildModes', 'parameterSchema', 'nativeDetails'], 'Vector index family');
    return immutable({id: platformValue(item.id, 'Vector index family ID'),
      label: platformValue(item.label ?? item.id, 'Vector index family label'),
      metrics: strings(item.metrics, 'Vector index family metrics'),
      buildModes: strings(item.buildModes, 'Vector index family build modes'),
      parameterSchema: parameterSchema(item.parameterSchema),
      nativeDetails: immutable({...plainObject(item.nativeDetails ?? {}, 'Vector index family metadata')})});
  });
  const ids = new Set(); families.forEach((item) => {
    if(ids.has(item.id)) throw new TypeError(`Duplicate vector index family ID: ${item.id}`); ids.add(item.id);
  });
  const maximum = input.max_dimensions;
  if(maximum != null && (!Number.isInteger(maximum) || maximum < 1)) throw new TypeError(
    'Vector maximum dimensions must be a positive integer or unknown.');
  return immutable({vector_storage: input.vector_storage, vector_dimensions: input.vector_dimensions,
    supported_metrics: metrics, supported_index_families: families, filtering: input.filtering,
    hybrid_search: input.hybrid_search, multi_vector: input.multi_vector,
    quantization: optionalBoolean(input.quantization, 'Vector quantization capability'),
    index_build_online: optionalBoolean(input.index_build_online, 'Online index-build capability'),
    max_dimensions: maximum ?? null,
    nativeDetails: immutable({...plainObject(input.nativeDetails ?? {}, 'Vector capability native details')})});
}

function typedParameter(field, value) {
  if(field.type === 'integer' && !Number.isInteger(value)) throw new TypeError(
    `${field.label} must be an integer.`);
  if(field.type === 'number' && (typeof value !== 'number' || !Number.isFinite(value))) throw new TypeError(
    `${field.label} must be a finite number.`);
  if(field.type === 'boolean' && typeof value !== 'boolean') throw new TypeError(
    `${field.label} must be boolean.`);
  if(field.type === 'string' && typeof value !== 'string') throw new TypeError(`${field.label} must be text.`);
  if(field.minimum != null && value < field.minimum) throw new TypeError(
    `${field.label} must be at least ${field.minimum}.`);
  if(field.maximum != null && value > field.maximum) throw new TypeError(
    `${field.label} must be at most ${field.maximum}.`);
  if(field.enum && !field.enum.includes(value)) throw new TypeError(`${field.label} is not an advertised value.`);
}

export function validateIndexForCapabilities(index, capabilityInput) {
  const capabilities = validateVectorCapabilities(capabilityInput);
  const family = capabilities.supported_index_families.find((item) => item.id === index.algorithm);
  if(!family) throw new TypeError(`Index family ${index.algorithm} is not advertised by this provider.`);
  if(!family.metrics.includes(index.nativeMetric)) throw new TypeError(
    `Metric ${index.nativeMetric} is not advertised for ${index.algorithm}.`);
  if(!capabilities.supported_metrics.some((item) => item.normalized === index.normalizedMetric &&
      item.native === index.nativeMetric)) throw new TypeError('Vector metric normalization is not provider-proven.');
  if(capabilities.max_dimensions != null && index.dimensions > capabilities.max_dimensions) throw new TypeError(
    `Vector dimensions exceed provider maximum ${capabilities.max_dimensions}.`);
  if(!family.buildModes.includes(index.buildMode)) throw new TypeError(
    `Build mode ${index.buildMode} is not advertised for ${index.algorithm}.`);
  const fields = new Map(family.parameterSchema.fields.map((field) => [field.id, field]));
  Object.keys(index.providerConfig).forEach((key) => {
    if(!fields.has(key)) throw new TypeError(`Index parameter ${key} is not advertised by the provider.`);
  });
  fields.forEach((field, id) => {
    const value = index.providerConfig[id];
    if(field.required && value === undefined) throw new TypeError(`Index parameter ${id} is required.`);
    if(value !== undefined) typedParameter(field, value);
  });
  return immutable({valid: true, familyId: family.id, normalizedMetric: index.normalizedMetric,
    nativeMetric: index.nativeMetric, dimensions: index.dimensions, providerConfig: index.providerConfig});
}

export function validateMLVectorDefinition(input) {
  const content = createMLVectorContent(input); const errors = []; const warnings = [];
  content.embeddingPipelines.forEach((pipeline) => {
    const policy = pipeline.dataSharingPolicy;
    if(pipeline.modelRef.external && policy.containsSensitiveData && !policy.approvedForSensitiveData) errors.push(
      `Embedding pipeline ${pipeline.id} is not approved to send sensitive data to ${pipeline.modelRef.providerIdentity}.`);
    if(pipeline.outputDimensions < 1) errors.push(`Embedding pipeline ${pipeline.id} dimensions are invalid.`);
  });
  content.vectorDesigns.forEach((design) => design.indexes.forEach((index) => {
    const field = design.fields.find((item) => item.id === index.fieldId);
    if(field && (field.dimensions !== index.dimensions || design.dimensions !== index.dimensions)) errors.push(
      `Vector index ${index.id} dimensions do not match its field and design.`);
  }));
  if(!content.vectorDesigns.length) warnings.push('No vector designs are configured.');
  return immutable({valid: !errors.length, errors: [...new Set(errors)].sort(),
    warnings: [...new Set(warnings)].sort()});
}

export function normalizeSearchRequest(input, {maximumK=10000}={}) {
  plainObject(input, 'Vector search request'); noRawSecrets(input, 'Vector search request');
  exact(input, ['designId', 'indexId', 'queryVector', 'queryText', 'filters', 'k', 'outputFields'],
    'Vector search request');
  const hasVector = Array.isArray(input.queryVector); const hasText = typeof input.queryText === 'string' &&
    input.queryText.length > 0;
  if(hasVector === hasText) throw new TypeError('Vector search requires exactly one vector or text query source.');
  if(hasVector && (!input.queryVector.length || input.queryVector.some((value) =>
    typeof value !== 'number' || !Number.isFinite(value)))) throw new TypeError('Query vector is invalid.');
  if(hasVector && input.queryVector.length > 1000000) throw new TypeError('Query vector exceeds its size limit.');
  if(hasText && input.queryText.length > 65536) throw new TypeError('Vector search text exceeds its size limit.');
  if(!Number.isInteger(input.k) || input.k < 1 || input.k > maximumK) throw new TypeError(
    `Vector search k must be from 1 to ${maximumK}.`);
  return immutable({designId: platformValue(input.designId, 'Vector search design ID'),
    indexId: platformValue(input.indexId, 'Vector search index ID'),
    queryVector: hasVector ? [...input.queryVector] : null, queryText: hasText ? input.queryText : null,
    filters: immutable({...plainObject(input.filters ?? {}, 'Vector search filters')}), k: input.k,
    outputFields: strings(input.outputFields ?? [], 'Vector search output fields')});
}

export function normalizeEvaluationResult(input, evaluationCase) {
  plainObject(input, 'Vector evaluation result'); noRawSecrets(input, 'Vector evaluation result');
  exact(input, ['metrics', 'latencyMs', 'throughput', 'indexSizeBytes', 'buildTimeMs', 'evidence'],
    'Vector evaluation result'); plainObject(input.metrics, 'Vector evaluation metrics');
  const metrics = Object.fromEntries(Object.entries(input.metrics).map(([name, value]) =>
    [platformValue(name, 'Evaluation metric name'), validateMetricResult(value, `Evaluation metric ${name}`)]));
  evaluationCase.metrics.forEach((name) => {
    if(!Object.hasOwn(metrics, name)) throw new TypeError(`Evaluation result is missing metric ${name}.`);
  });
  const latencyMs = validateMetricResult(input.latencyMs, 'Evaluation latency');
  const throughput = validateMetricResult(input.throughput, 'Evaluation throughput');
  if(latencyMs < 0 || throughput < 0) throw new TypeError('Evaluation latency and throughput cannot be negative.');
  return immutable({metrics, latencyMs, throughput,
    indexSizeBytes: input.indexSizeBytes == null ? null : validateMetricResult(
      input.indexSizeBytes, 'Evaluation index size'),
    buildTimeMs: input.buildTimeMs == null ? null : validateMetricResult(
      input.buildTimeMs, 'Evaluation build time'),
    evidence: immutable({...plainObject(input.evidence, 'Vector evaluation evidence')})});
}
