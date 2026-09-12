/////////////////////////////////////////////////////////////
// Provider-classified AI query compile, review and execute authority.
/////////////////////////////////////////////////////////////

import {immutable, noRawSecrets, plainObject, platformValue} from '../../platform/serviceUtils';
import {AI_COMMAND_RISKS} from '../../commands/CommandRegistry';
import {AIResultHandleService} from './AIResultHandleService';

export const AI_QUERY_AUTHORITY_SERVICE_ID = 'cdeadmin.ai_interface.query_authority';
const CLASSIFICATIONS = ['read_only', 'mutation', 'unknown'];
function exact(value, fields, label) { plainObject(value, label); const unknown = Object.keys(value)
  .filter((field) => !fields.includes(field)); if(unknown.length) throw new TypeError(
  `${label} contains unsupported field ${unknown[0]}.`); }
function strings(value, label) { if(!Array.isArray(value) || value.length > 10000) throw new TypeError(
  `${label} must be a bounded array.`); const result = value.map((item) => platformValue(
  item, `${label} value`, 2048)); if(new Set(result).size !== result.length) throw new TypeError(
  `${label} contains duplicates.`); return result; }
function preparedQuery(value, connectorView) {
  const fields = ['preparedId', 'sideEffects', 'connectorSnapshot', 'operationKind',
    'canonicalResourceRefs', 'accessSurfaceRefs', 'normalizedArguments', 'nativeCompiledArtifact',
    'riskClass', 'estimatedEffects', 'budgets', 'validation', 'expiresAt', 'queryClassification',
    'explain', 'connectorId', 'connectorRevision', 'preparedAt'];
  exact(value, fields, 'Prepared AI query'); noRawSecrets(value, 'Prepared AI query');
  if(value.sideEffects !== false || value.operationKind !== 'query') throw new TypeError(
    'Provider query preparation must be side-effect free and typed as query.');
  exact(value.connectorSnapshot, ['connectorId', 'revision', 'capabilitySnapshotRef'],
    'Prepared query connector snapshot');
  if(value.connectorSnapshot.connectorId !== connectorView.profile.connectorId ||
      value.connectorSnapshot.revision !== connectorView.revision ||
      value.connectorId !== connectorView.profile.connectorId || value.connectorRevision !== connectorView.revision)
    throw new Error('Prepared query connector snapshot is stale or mismatched.');
  if(!CLASSIFICATIONS.includes(value.queryClassification)) throw new TypeError(
    'Provider query classification is invalid.');
  if(!AI_COMMAND_RISKS.includes(value.riskClass) || value.riskClass === 'R7') throw new TypeError(
    'Prepared query risk class is invalid.');
  const risk = AI_COMMAND_RISKS.indexOf(value.riskClass);
  if((value.queryClassification === 'read_only' && risk > 1) ||
      (value.queryClassification !== 'read_only' && risk < 4)) throw new TypeError(
    'Provider query classification and risk class disagree.');
  if(!Array.isArray(value.estimatedEffects) || !Array.isArray(value.validation) ||
      value.validation.some((item) => item?.passed !== true)) throw new Error(
    'Prepared query did not pass every provider validation check.');
  plainObject(value.normalizedArguments, 'Prepared query normalized arguments');
  plainObject(value.budgets, 'Prepared query budgets');
  const expiry = Date.parse(value.expiresAt); if(!Number.isFinite(expiry)) throw new TypeError(
    'Prepared query expiry is invalid.');
  return immutable({...value, preparedId: platformValue(value.preparedId, 'Prepared query ID'),
    canonicalResourceRefs: strings(value.canonicalResourceRefs, 'Prepared query canonical resources'),
    accessSurfaceRefs: strings(value.accessSurfaceRefs, 'Prepared query access surfaces')});
}
function queryRequest(input) {
  const fields = ['queryId', 'connectorId', 'dialectId', 'source', 'requestedResourceRefs',
    'accessSurfaceRefs', 'requestExplain', 'estimatedBudget', 'environment'];
  exact(input, fields, 'AI query request'); noRawSecrets(input, 'AI query request');
  if(typeof input.requestExplain !== 'boolean') throw new TypeError('AI query explain request must be boolean.');
  return immutable({queryId: platformValue(input.queryId, 'AI query ID'),
    connectorId: platformValue(input.connectorId, 'AI query connector ID'),
    dialectId: platformValue(input.dialectId, 'AI query dialect'),
    source: platformValue(input.source, 'AI query source', 1048576),
    requestedResourceRefs: strings(input.requestedResourceRefs ?? [], 'AI requested query resources'),
    accessSurfaceRefs: strings(input.accessSurfaceRefs ?? [], 'AI query access surfaces'),
    requestExplain: input.requestExplain,
    estimatedBudget: immutable({...plainObject(input.estimatedBudget ?? {}, 'AI query estimated budget')}),
    environment: platformValue(input.environment, 'AI query environment')});
}

export class AIQueryAuthority {
  constructor({connectors, authorization, resultHandles, now=() => new Date().toISOString()}={}) {
    if(!connectors || typeof connectors.get !== 'function' || typeof connectors.prepare !== 'function' ||
        typeof connectors.execute !== 'function' || typeof connectors.cancel !== 'function') throw new TypeError(
      'AI query authority requires the connector service.');
    if(!authorization || typeof authorization.evaluate !== 'function' ||
        typeof authorization.assertExecutionDecision !== 'function') throw new TypeError(
      'AI query authority requires deterministic authorization.');
    if(!(resultHandles instanceof AIResultHandleService)) throw new TypeError(
      'AI query authority requires result-handle service.');
    this.connectors = connectors; this.authorization = authorization;
    this.resultHandles = resultHandles; this.now = now; this.queries = new Map();
  }
  async compile(input, context={}) {
    const request = queryRequest(input); if(this.queries.has(request.queryId)) throw new Error(
      `AI query already exists: ${request.queryId}`); const connector = this.connectors.get(request.connectorId);
    if(connector.profile.dialectId && connector.profile.dialectId !== request.dialectId) throw new Error(
      'AI query dialect does not match the connector.');
    const decision = await this.authorization.evaluate({operationId: `${request.queryId}:compile`,
      commandId: 'ai.query.compile', operationKind: 'query_compile',
      resourceRefs: request.requestedResourceRefs, accessSurfaceRefs: request.accessSurfaceRefs,
      environment: request.environment, modelVisibleContent: null,
      estimatedBudget: request.estimatedBudget, normalizedArguments: {queryId: request.queryId}, riskClass: 'R0',
      plan: context.plan ?? null,
      approvalEvidence: null, phase: 'plan'}, context);
    if(!decision.allowed) throw new Error(`AI query compilation denied at ${decision.deniedAt}: ${decision.reason}`);
    if(request.requestExplain) { const explainDecision = await this.authorization.evaluate({
      operationId: `${request.queryId}:explain`, commandId: 'ai.query.explain',
      operationKind: 'query_explain', resourceRefs: request.requestedResourceRefs,
      accessSurfaceRefs: request.accessSurfaceRefs, environment: request.environment,
      modelVisibleContent: null, estimatedBudget: request.estimatedBudget,
      normalizedArguments: {queryId: request.queryId}, riskClass: 'R0', plan: context.plan ?? null,
      approvalEvidence: null, phase: 'plan'}, context);
    if(!explainDecision.allowed) throw new Error(
      `AI query explain denied at ${explainDecision.deniedAt}: ${explainDecision.reason}`); }
    const prepared = preparedQuery(await this.connectors.prepare(request.connectorId, immutable({
      operationKind: 'query', dialectId: request.dialectId, source: request.source,
      requestedResourceRefs: request.requestedResourceRefs,
      accessSurfaceRefs: request.accessSurfaceRefs, requestExplain: request.requestExplain,
    })), connector);
    if(Date.parse(prepared.expiresAt) <= Date.parse(this.now())) throw new Error('Prepared AI query is already stale.');
    const record = immutable({schema: 'cdeadmin.ai-query-review.v1', queryId: request.queryId,
      connectorId: request.connectorId, connectorRevision: connector.revision, dialectId: request.dialectId,
      source: request.source, queryClassification: prepared.queryClassification,
      riskClass: prepared.riskClass, canonicalResourceRefs: prepared.canonicalResourceRefs,
      accessSurfaceRefs: prepared.accessSurfaceRefs, estimatedEffects: prepared.estimatedEffects,
      budgets: prepared.budgets, validation: prepared.validation, explain: prepared.explain ?? null,
      preparedId: prepared.preparedId, expiresAt: prepared.expiresAt, environment: request.environment});
    this.queries.set(request.queryId, {request, prepared, review: record}); return record;
  }
  review(queryId) { return this._record(queryId).review; }
  async execute(queryId, context={}) {
    const record = this._record(queryId); if(Date.parse(record.prepared.expiresAt) <= Date.parse(this.now())) {
      this.queries.delete(record.review.queryId); throw new Error('Prepared AI query has expired.'); }
    const read = record.prepared.queryClassification === 'read_only';
    const commandId = read ? 'ai.query.execute_read' : 'ai.query.execute_mutation';
    const decision = await this.authorization.evaluate({operationId: `${record.review.queryId}:execute`, commandId,
      operationKind: read ? 'query_read' : 'query_mutation',
      resourceRefs: record.prepared.canonicalResourceRefs,
      accessSurfaceRefs: record.prepared.accessSurfaceRefs, environment: record.review.environment,
      modelVisibleContent: null, estimatedBudget: record.prepared.budgets,
      normalizedArguments: record.prepared.normalizedArguments, riskClass: record.prepared.riskClass,
      plan: context.plan ?? null, approvalEvidence: context.approvalEvidence ?? null,
      phase: 'execute'}, context);
    if(!decision.allowed) throw new Error(`AI query execution denied at ${decision.deniedAt}: ${decision.reason}`);
    this.authorization.assertExecutionDecision(decision, commandId, record.prepared.normalizedArguments);
    const result = await this.connectors.execute(record.review.connectorId,
      record.prepared.preparedId, context.approvalEvidence ?? null); noRawSecrets(result, 'AI query result');
    this.queries.delete(record.review.queryId);
    if(result?.kind === 'task') { exact(result, ['kind', 'taskRef'], 'AI query task result');
      return immutable({schema: 'cdeadmin.ai-query-execution.v1',
        queryId: record.review.queryId, kind: 'task', taskRef: platformValue(result.taskRef,
          'AI query task reference'), decisionId: decision.decisionId}); }
    exact(result, ['kind', 'resultType', 'value', 'schemaSummary', 'classification', 'permittedOperations'],
      'AI query result'); if(result.kind !== 'result') throw new TypeError('AI query result kind is invalid.');
    const handle = this.resultHandles.create({resultType: result.resultType, value: result.value,
      schemaSummary: result.schemaSummary, classification: result.classification,
      permittedOperations: result.permittedOperations, resourceRefs: record.prepared.canonicalResourceRefs,
      ownerId: platformValue(context.currentUser?.id, 'AI query result owner'),
      connectorId: record.review.connectorId, authorizationRef: decision.decisionId});
    return immutable({schema: 'cdeadmin.ai-query-execution.v1', queryId: record.review.queryId,
      kind: 'result_handle', resultHandle: handle, decisionId: decision.decisionId});
  }
  async cancel(queryId) { const record = this._record(queryId); const result = await this.connectors.cancel(
    record.review.connectorId, record.prepared.preparedId); this.queries.delete(record.review.queryId);
  return immutable({schema: 'cdeadmin.ai-query-cancellation.v1', queryId: record.review.queryId,
    requested: true, providerResult: result}); }
  _record(queryId) { queryId = platformValue(queryId, 'AI query ID'); const value = this.queries.get(queryId);
    if(!value) throw new Error(`Unknown AI query: ${queryId}`); return value; }
}
