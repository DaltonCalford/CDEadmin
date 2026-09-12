/////////////////////////////////////////////////////////////
// Data Quality canonical contracts and deterministic serialization.
/////////////////////////////////////////////////////////////

import {immutable, noRawSecrets, plainObject, platformValue} from '../../platform/serviceUtils';

export const QUALITY_MODULE_ID = 'cdeadmin.quality';
export const QUALITY_ASSET_TYPE = 'cdeadmin.quality.v1';
export const QUALITY_ASSET_SCHEMA = 'cdeadmin.quality.asset.v1';

export const QUALITY_DIMENSIONS = Object.freeze([
  'accuracy', 'completeness', 'validity', 'consistency', 'uniqueness',
  'timeliness', 'freshness', 'referential_integrity', 'volume',
  'distribution', 'drift', 'custom',
]);

export const QUALITY_RULE_FAMILIES = Object.freeze([
  'schema_presence', 'type_compatibility', 'not_null', 'completeness_ratio',
  'uniqueness', 'duplicate_ratio', 'accepted_values', 'regex/pattern',
  'range', 'length', 'referential_integrity', 'cross_resource_match',
  'row_count', 'volume_change', 'freshness', 'timeliness', 'distribution',
  'quantile', 'mean/stddev', 'drift', 'custom_query', 'custom_expression',
  'provider_native',
]);

export const QUALITY_EVALUATION_MODES = Object.freeze([
  'exact', 'sampled', 'approximate', 'provider_reported',
]);
export const QUALITY_THRESHOLD_OPERATORS = Object.freeze([
  '=', '!=', '<', '<=', '>', '>=', 'between', 'outside',
]);
export const QUALITY_SEVERITIES = Object.freeze(['info', 'warning', 'critical']);
export const QUALITY_SAMPLING_MODES = Object.freeze([
  'none_exact', 'first_n', 'random_n', 'percentage', 'provider_native',
  'partition', 'time_window',
]);
export const QUALITY_ACTION_TYPES = Object.freeze([
  'notify', 'block', 'warn', 'open_issue', 'call_command',
]);
export const QUALITY_RESULT_STATES = Object.freeze(['pass', 'fail', 'error']);
export const QUALITY_STATES = Object.freeze([
  'empty', 'loading', 'ready', 'stale', 'partial', 'permission_denied',
  'disconnected', 'validation_error', 'runtime_failure', 'read_only',
  'background_task_active',
]);

const REFERENCE_SCHEMAS = new Set([
  'cdeadmin.resource-ref.v1', 'cdeadmin.asset-ref.v1',
  'cdeadmin.task-ref.v1', 'cdeadmin.result-ref.v1', 'cdeadmin.external-ref.v1',
]);

function finite(value, label, {minimum, maximum}={}) {
  const result = Number(value);
  if(!Number.isFinite(result) || (minimum !== undefined && result < minimum) ||
      (maximum !== undefined && result > maximum)) throw new TypeError(`${label} is invalid.`);
  return result;
}

function optionalString(value, label, maximum=4096) {
  return value === undefined || value === null || value === '' ? null :
    platformValue(value, label, maximum);
}

export function validateQualityRef(input, label='Quality reference') {
  plainObject(input, label); noRawSecrets(input, label);
  const schema = platformValue(input.schema, `${label} schema`);
  if(!REFERENCE_SCHEMAS.has(schema)) throw new TypeError(`${label} schema is unsupported.`);
  const identity = schema === 'cdeadmin.resource-ref.v1' ? input.canonical :
    schema === 'cdeadmin.asset-ref.v1' ? `${input.projectId}/${input.assetId}` : input.id;
  platformValue(identity, `${label} identity`, 4096);
  return immutable({...input});
}

export function qualityReferenceKey(input) {
  const ref = validateQualityRef(input);
  if(ref.schema === 'cdeadmin.resource-ref.v1') return `resource:${ref.canonical}`;
  if(ref.schema === 'cdeadmin.asset-ref.v1') return `asset:${ref.projectId}/${ref.assetId}`;
  return `${ref.schema}:${ref.id}`;
}

export function validateThreshold(input, {ratio=false}={}) {
  if(input === null || input === undefined) return null;
  plainObject(input, 'Quality threshold'); noRawSecrets(input, 'Quality threshold');
  const operator = platformValue(input.operator, 'Threshold operator');
  if(!QUALITY_THRESHOLD_OPERATORS.includes(operator)) throw new TypeError(
    `Invalid threshold operator: ${operator}`
  );
  const bounds = ratio ? {minimum: 0, maximum: 1} : {};
  if(['between', 'outside'].includes(operator)) {
    const lower = finite(input.lower, 'Threshold lower bound', bounds);
    const upper = finite(input.upper, 'Threshold upper bound', bounds);
    if(lower > upper) throw new TypeError('Threshold lower bound must not exceed upper bound.');
    return immutable({operator, lower, upper});
  }
  return immutable({operator, value: finite(input.value, 'Threshold value', bounds)});
}

export function validateSamplingPolicy(input={mode: 'none_exact'}) {
  plainObject(input, 'Sampling policy'); noRawSecrets(input, 'Sampling policy');
  const mode = platformValue(input.mode, 'Sampling mode');
  if(!QUALITY_SAMPLING_MODES.includes(mode)) throw new TypeError(`Invalid sampling mode: ${mode}`);
  const output = {mode};
  if(['first_n', 'random_n'].includes(mode)) {
    const limit = finite(input.limit, 'Sampling limit', {minimum: 1, maximum: 1000000});
    if(!Number.isInteger(limit)) throw new TypeError('Sampling limit must be an integer.');
    output.limit = limit;
  }
  if(mode === 'percentage') output.percentage = finite(
    input.percentage, 'Sampling percentage', {minimum: 0.000001, maximum: 1}
  );
  if(mode === 'partition') output.partition = platformValue(
    input.partition, 'Sampling partition', 4096
  );
  if(mode === 'time_window') {
    output.window = immutable({...plainObject(input.window, 'Sampling time window')});
  }
  if(input.nativeDetails !== undefined) output.nativeDetails = immutable({
    ...plainObject(input.nativeDetails, 'Sampling native details'),
  });
  return immutable(output);
}

export function validateDataSlice(input) {
  plainObject(input, 'Data slice'); noRawSecrets(input, 'Data slice');
  const filter = input.filter === undefined || input.filter === null ? null :
    immutable({...plainObject(input.filter, 'Data slice filter')});
  return immutable({schema: 'cdeadmin.quality-data-slice.v1',
    id: platformValue(input.id, 'Data slice ID'),
    resourceRef: validateQualityRef(input.resourceRef, 'Data slice resource'),
    partition: optionalString(input.partition, 'Data slice partition'),
    window: input.window === undefined || input.window === null ? null :
      immutable({...plainObject(input.window, 'Data slice window')}),
    filter, samplePolicy: validateSamplingPolicy(input.samplePolicy),
    nativeDetails: immutable({...plainObject(input.nativeDetails ?? {},
      'Data slice native details')}),
  });
}

export function validateQualityRule(input) {
  plainObject(input, 'Quality rule'); noRawSecrets(input, 'Quality rule');
  const type = platformValue(input.type, 'Quality rule type');
  if(!QUALITY_RULE_FAMILIES.includes(type)) throw new TypeError(`Invalid rule family: ${type}`);
  const dimension = platformValue(input.dimension, 'Quality dimension');
  if(!QUALITY_DIMENSIONS.includes(dimension)) throw new TypeError(
    `Invalid quality dimension: ${dimension}`
  );
  const severity = platformValue(input.severity, 'Quality severity');
  if(!QUALITY_SEVERITIES.includes(severity)) throw new TypeError(`Invalid severity: ${severity}`);
  const evaluationMode = platformValue(input.evaluationMode, 'Evaluation mode');
  if(!QUALITY_EVALUATION_MODES.includes(evaluationMode)) throw new TypeError(
    `Invalid evaluation mode: ${evaluationMode}`
  );
  const actionIds = [...new Set((input.actionIds ?? []).map((id) =>
    platformValue(id, 'Quality action ID')))].sort();
  const generatedSuggestion = Boolean(input.generatedSuggestion);
  const sourceSampleRef = input.sourceSampleRef ? validateQualityRef(
    input.sourceSampleRef, 'Suggestion source sample'
  ) : null;
  const sourceRevision = optionalString(input.sourceRevision, 'Suggestion source revision');
  if(generatedSuggestion && (!sourceSampleRef || !sourceRevision)) throw new TypeError(
    'Generated quality suggestions require a source sample and source revision.'
  );
  return immutable({schema: 'cdeadmin.quality-rule.v1',
    id: platformValue(input.id, 'Quality rule ID'),
    name: platformValue(input.name, 'Quality rule name'), type, dimension,
    scope: input.scope ? validateDataSlice(input.scope) : null,
    parameters: immutable({...plainObject(input.parameters ?? {}, 'Quality rule parameters')}),
    threshold: validateThreshold(input.threshold, {ratio: Boolean(input.ratio)}),
    ratio: Boolean(input.ratio), severity, evaluationMode,
    enabled: input.enabled !== false, generatedSuggestion,
    sourceSampleRef, sourceRevision,
    actionIds, documentation: String(input.documentation ?? ''),
    nativeDetails: immutable({...plainObject(input.nativeDetails ?? {},
      'Quality rule native details')}),
  });
}

export function validateQualityAction(input) {
  plainObject(input, 'Quality action'); noRawSecrets(input, 'Quality action');
  const type = platformValue(input.type, 'Quality action type');
  if(!QUALITY_ACTION_TYPES.includes(type)) throw new TypeError(`Invalid quality action: ${type}`);
  const severities = [...new Set((input.severities ?? QUALITY_SEVERITIES).map((severity) => {
    if(!QUALITY_SEVERITIES.includes(severity)) throw new TypeError(
      `Invalid quality action severity: ${severity}`
    );
    return severity;
  }))].sort();
  const output = {schema: 'cdeadmin.quality-action.v1',
    id: platformValue(input.id, 'Quality action ID'), type,
    label: platformValue(input.label ?? input.id, 'Quality action label'),
    severities, enabled: input.enabled !== false,
    parameters: immutable({...plainObject(input.parameters ?? {}, 'Quality action parameters')})};
  if(type === 'call_command') output.commandId = platformValue(
    input.commandId, 'Quality action command ID'
  );
  return immutable(output);
}

function unique(values, mapper, label) {
  const result = values.map(mapper); const ids = new Set();
  result.forEach((item) => {
    const id = item.id ?? qualityReferenceKey(item);
    if(ids.has(id)) throw new TypeError(`Duplicate ${label}: ${id}`);
    ids.add(id);
  });
  return result.sort((left, right) => String(left.id ?? qualityReferenceKey(left))
    .localeCompare(String(right.id ?? qualityReferenceKey(right))));
}

export function createQualityContent(input={}) {
  plainObject(input, 'Data Quality asset content'); noRawSecrets(input, 'Data Quality content');
  const rules = unique(input.rules ?? [], validateQualityRule, 'Quality rule');
  const actions = unique(input.actions ?? [], validateQualityAction, 'Quality action');
  const actionIds = new Set(actions.map((item) => item.id));
  rules.forEach((rule) => rule.actionIds.forEach((id) => {
    if(!actionIds.has(id)) throw new TypeError(`Quality rule references unknown action: ${id}`);
  }));
  const baselineRefs = unique(input.baselineRefs ?? [], (item) => validateQualityRef(
    item, 'Quality baseline reference'
  ), 'Quality baseline reference');
  const severityPolicy = immutable({...plainObject(input.severityPolicy ?? {},
    'Quality severity policy')});
  if(severityPolicy.weights !== undefined) {
    const weights = plainObject(severityPolicy.weights, 'Quality severity weights');
    Object.keys(weights).forEach((severity) => {
      if(!QUALITY_SEVERITIES.includes(severity)) throw new TypeError(
        `Unknown quality severity weight: ${severity}`
      );
      finite(weights[severity], `${severity} severity weight`, {minimum: 0});
    });
  }
  const schedule = input.schedule === undefined || input.schedule === null ? null :
    immutable({...plainObject(input.schedule, 'Quality schedule')});
  return immutable({schema: QUALITY_ASSET_SCHEMA, schemaVersion: 1,
    moduleId: QUALITY_MODULE_ID,
    name: String(input.name ?? ''), owner: String(input.owner ?? ''),
    tags: [...new Set((input.tags ?? []).map((tag) => platformValue(tag, 'Quality tag')))].sort(),
    rules, parameters: immutable({...plainObject(input.parameters ?? {}, 'Quality parameters')}),
    defaultScope: input.defaultScope ? validateDataSlice(input.defaultScope) : null,
    severityPolicy, schedule, actions, baselineRefs});
}

function canonical(value) {
  if(Array.isArray(value)) return value.map(canonical);
  if(!value || typeof value !== 'object') return value;
  return Object.fromEntries(Object.keys(value).sort().map((key) => [key, canonical(value[key])]));
}

export function exportQualityAsset(input) {
  return `${JSON.stringify(canonical(createQualityContent(input)), null, 2)}\n`;
}

export function importQualityAsset(text) {
  if(typeof text !== 'string' || text.length > 8 * 1024 * 1024) throw new TypeError(
    'Data Quality source is invalid or exceeds 8 MiB.'
  );
  return createQualityContent(JSON.parse(text));
}

export function qualityAssetRequest({projectId, assetId, name, path,
  expectedVersion=0, content}) {
  const canonicalContent = createQualityContent(content);
  return immutable({asset_type: QUALITY_ASSET_TYPE, name: platformValue(name, 'Asset name'), path,
    schema_name: QUALITY_ASSET_TYPE, schema_version: 1, expected_version: expectedVersion,
    content: canonicalContent, metadata: {moduleId: QUALITY_MODULE_ID},
    dependency_references: canonicalContent.baselineRefs,
    resource_bindings: canonicalContent.defaultScope ?
      [canonicalContent.defaultScope.resourceRef] : [],
    validation_state: 'valid', validation_details: [], projectId, assetId});
}
