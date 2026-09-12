/////////////////////////////////////////////////////////////
// Temporary, bounded and authorization-preserving AI result handles.
/////////////////////////////////////////////////////////////

import {immutable, noRawSecrets, plainObject, platformValue} from '../../platform/serviceUtils';
import {AI_CLASSIFICATIONS} from './AIAssetContracts';

export const AI_RESULT_HANDLE_SERVICE_ID = 'cdeadmin.ai_interface.result_handles';
const RESULT_TYPES = ['tabular', 'document', 'graph', 'key_value', 'time_series', 'vector',
  'search', 'scalar', 'native'];
const OPERATIONS = ['slice', 'statistics', 'summary'];
const bytes = (value) => new TextEncoder().encode(JSON.stringify(value)).length;
function exact(value, fields, label) { plainObject(value, label); const unknown = Object.keys(value)
  .filter((field) => !fields.includes(field)); if(unknown.length) throw new TypeError(
  `${label} contains unsupported field ${unknown[0]}.`); }
function records(value) { if(Array.isArray(value)) return value;
  for(const key of ['rows', 'documents', 'items', 'points', 'matches']) if(Array.isArray(value?.[key]))
    return value[key]; return null; }

export class AIResultHandleService {
  constructor({authorize, now=() => new Date().toISOString(), defaultTtlSeconds=900,
    maximumBytes=52428800, maximumRows=100000}={}) {
    if(typeof authorize !== 'function') throw new TypeError('AI result handles require access authorization.');
    if(!Number.isSafeInteger(defaultTtlSeconds) || defaultTtlSeconds < 1 ||
        !Number.isSafeInteger(maximumBytes) || maximumBytes < 1 ||
        !Number.isSafeInteger(maximumRows) || maximumRows < 1) throw new TypeError(
      'AI result-handle limits must be positive integers.');
    this.authorize = authorize; this.now = now; this.defaultTtlSeconds = defaultTtlSeconds;
    this.maximumBytes = maximumBytes; this.maximumRows = maximumRows; this.sequence = 0;
    this.handles = new Map();
  }
  create(input) {
    const fields = ['handleId', 'resultType', 'value', 'schemaSummary', 'classification',
      'expiresAt', 'permittedOperations', 'resourceRefs', 'ownerId', 'connectorId', 'authorizationRef'];
    exact(input, fields, 'AI result handle'); noRawSecrets(input, 'AI result handle');
    if(!RESULT_TYPES.includes(input.resultType)) throw new TypeError('AI result type is invalid.');
    if(!AI_CLASSIFICATIONS.includes(input.classification)) throw new TypeError(
      'AI result classification is invalid.');
    const valueBytes = bytes(input.value); const rows = records(input.value);
    const rowCount = rows?.length ?? null;
    if(valueBytes > this.maximumBytes || (rowCount != null && rowCount > this.maximumRows))
      throw new Error('AI result exceeds temporary handle storage limits.');
    if(!Array.isArray(input.permittedOperations ?? [])) throw new TypeError(
      'AI result permitted operations must be an array.');
    const permitted = [...new Set(['summary', ...(input.permittedOperations ?? ['slice', 'statistics'])])];
    if(permitted.some((item) => !OPERATIONS.includes(item))) throw new TypeError(
      'AI result handle contains an invalid follow-up operation.');
    const nowMs = Date.parse(this.now()); const expiresAt = input.expiresAt ??
      new Date(nowMs + this.defaultTtlSeconds * 1000).toISOString();
    if(!Number.isFinite(Date.parse(expiresAt)) || Date.parse(expiresAt) <= nowMs) throw new TypeError(
      'AI result handle expiry must be in the future.');
    const handleId = platformValue(input.handleId ?? `result-handle-${++this.sequence}`,
      'AI result handle ID'); if(this.handles.has(handleId)) throw new Error(
      `AI result handle already exists: ${handleId}`);
    if(!Array.isArray(input.resourceRefs)) throw new TypeError(
      'AI result resource references must be an array.');
    const record = {descriptor: immutable({schema: 'cdeadmin.ai-result-handle.v1', handleId,
      resultType: input.resultType, rowCount, byteCount: valueBytes,
      schemaSummary: immutable({...plainObject(input.schemaSummary, 'AI result schema summary')}),
      classification: input.classification, expiry: expiresAt, permittedOperations: permitted,
      resourceRefs: [...new Set(input.resourceRefs ?? [])].map((item) => platformValue(
        item, 'AI result resource reference', 2048)), ownerId: platformValue(input.ownerId,
        'AI result owner ID'), connectorId: platformValue(input.connectorId, 'AI result connector ID'),
      authorizationRef: platformValue(input.authorizationRef, 'AI result authorization reference')}),
    value: immutable(input.value)};
    this.handles.set(handleId, record); return record.descriptor;
  }
  descriptor(handleId, context={}) { const record = this._authorized(handleId, 'summary', context);
    return record.descriptor; }
  modelDescriptor(handleId, context={}) { const value = this.descriptor(handleId, context);
    return immutable({handleId: value.handleId, resultType: value.resultType, rowCount: value.rowCount,
      byteCount: value.byteCount, schemaSummary: value.schemaSummary, classification: value.classification,
      expiry: value.expiry, permittedOperations: value.permittedOperations}); }
  slice(handleId, {offset=0, limit=100}={}, context={}) {
    const record = this._authorized(handleId, 'slice', context); const values = records(record.value);
    if(!values) throw new Error('AI result does not support record slicing.');
    if(!Number.isSafeInteger(offset) || offset < 0 || !Number.isSafeInteger(limit) ||
        limit < 1 || limit > 1000) throw new TypeError('AI result slice bounds are invalid.');
    const result = immutable({schema: 'cdeadmin.ai-result-slice.v1', handleId: record.descriptor.handleId,
      offset, limit, total: values.length, values: values.slice(offset, offset + limit)});
    if(bytes(result) > 1048576) throw new Error('AI result slice exceeds the one-MiB model boundary.');
    return result;
  }
  statistics(handleId, fields, context={}) {
    const record = this._authorized(handleId, 'statistics', context); const values = records(record.value);
    if(!values) throw new Error('AI result does not support statistics.');
    if(!Array.isArray(fields) || fields.length < 1 || fields.length > 100) throw new TypeError(
      'AI result statistic fields must be a bounded array.');
    const unique = [...new Set(fields.map((field) => platformValue(field, 'AI statistic field')))];
    if(unique.length !== fields.length) throw new TypeError('AI statistic fields contain duplicates.');
    const statistics = Object.fromEntries(unique.map((field) => { const numbers = values.map((item) =>
      item?.[field]).filter((item) => typeof item === 'number' && Number.isFinite(item));
    return [field, numbers.length ? {count: numbers.length, minimum: Math.min(...numbers),
      maximum: Math.max(...numbers), average: numbers.reduce((sum, item) => sum + item, 0) /
        numbers.length} : {count: 0, minimum: null, maximum: null, average: null}]; }));
    return immutable({schema: 'cdeadmin.ai-result-statistics.v1', handleId: record.descriptor.handleId,
      rowCount: values.length, statistics});
  }
  revoke(handleId, context={}) { const record = this._authorized(handleId, 'summary', context);
    this.handles.delete(record.descriptor.handleId); return true; }
  sweep() { const now = Date.parse(this.now()); let removed = 0; for(const [id, record] of this.handles)
    if(Date.parse(record.descriptor.expiry) <= now) { this.handles.delete(id); removed += 1; } return removed; }
  _authorized(handleId, operation, context) {
    handleId = platformValue(handleId, 'AI result handle ID'); const record = this.handles.get(handleId);
    if(!record) throw new Error(`Unknown AI result handle: ${handleId}`);
    if(Date.parse(record.descriptor.expiry) <= Date.parse(this.now())) { this.handles.delete(handleId);
      throw new Error(`AI result handle expired: ${handleId}`); }
    if(!record.descriptor.permittedOperations.includes(operation)) throw new Error(
      `AI result handle does not permit ${operation}.`);
    if(this.authorize(record.descriptor, operation, context) !== true) throw new Error(
      `AI result handle access denied: ${handleId}`);
    return record;
  }
}
