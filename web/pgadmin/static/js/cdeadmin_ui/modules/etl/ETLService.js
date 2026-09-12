/////////////////////////////////////////////////////////////
// ETL Designer orchestration, provider evidence and recovery.
/////////////////////////////////////////////////////////////

import {
  diagnosticsService, platformEventService, stablePlatformId,
} from '../../platform/PlatformRegistry';
import {abortError, immutable, noRawSecrets, plainObject, platformValue} from '../../platform/serviceUtils';
import {
  createETLContent, etlAssetRequest, etlReferenceKey, ETL_MODULE_ID,
  validatePipelineEdge, validatePipelineNode,
} from './contracts';
import {
  normalizePushdownPlan, normalizeStageResult, propagateSchemas,
  topologicalOrder, validateEdgeConnection, validatePipeline, weakestGuarantee,
} from './ETLEngine';

export const ETL_SERVICE_ID = 'etl.runtime';
export const ETL_TASKS = Object.freeze(['etl.preview', 'etl.run', 'etl.deploy.validation']);
export const ETL_EVENTS = Object.freeze([
  'etl.pipeline.changed', 'etl.run.started', 'etl.step.failed',
  'etl.run.completed', 'etl.lineage.emitted',
]);

const SUPPORT_STATES = Object.freeze([
  'supported_native', 'supported_via_cdeadmin', 'supported_via_external_adapter',
  'read_only', 'partial', 'unsupported', 'unknown',
]);
const ERROR_CODES = Object.freeze([
  'configuration_invalid', 'provider_unavailable', 'permission_denied',
  'capability_missing', 'task_failed', 'task_cancelled', 'partial_result',
  'stale_result', 'conflict', 'external_format_invalid', 'internal_error',
]);

function actor(currentUser={}) {
  return String(currentUser.id ?? currentUser.username ?? currentUser.email ?? 'unknown');
}

function providerIdFromRef(reference) {
  return reference?.provider ?? reference?.providerId ?? reference?.provider_id ?? null;
}

const REFERENCE_SCHEMAS = new Set(['cdeadmin.resource-ref.v1', 'cdeadmin.asset-ref.v1',
  'cdeadmin.external-ref.v1', 'cdeadmin.credential-ref.v1']);

function nestedReferences(value, path='configuration', result=[]) {
  if(Array.isArray(value)) value.forEach((item, index) => nestedReferences(
    item, `${path}.${index}`, result
  ));
  else if(value && typeof value === 'object') {
    if(REFERENCE_SCHEMAS.has(value.schema)) result.push({path, reference: value});
    else Object.entries(value).forEach(([key, item]) => nestedReferences(
      item, `${path}.${key}`, result
    ));
  }
  return result;
}

function referenceLabel(reference) {
  return reference.canonical ?? (reference.schema === 'cdeadmin.asset-ref.v1' ?
    `${reference.projectId}/${reference.assetId}` : reference.id) ?? etlReferenceKey(reference);
}

function requireAdapter(adapter, providerId) {
  ['getSourceCapabilities', 'getSinkCapabilities', 'planPushdown',
    'previewRead', 'executeNativeStage'].forEach((method) => {
    if(typeof adapter?.[method] !== 'function') throw new TypeError(
      `ETL adapter ${providerId} requires ${method}().`
    );
  });
}

function stringList(input, field, providerId) {
  if(!Array.isArray(input[field])) throw new TypeError(
    `${providerId} ETL result requires ${field}[].`
  );
  return [...new Set(input[field].map((item) => platformValue(
    item, `${providerId} ${field}`
  )))].sort();
}

export function validateETLProviderResult(input, providerId, operation,
  {allowEmpty=false}={}) {
  plainObject(input, `${providerId} ${operation} result`);
  noRawSecrets(input, `${providerId} ${operation} result`);
  const supportState = platformValue(input.supportState, 'ETL support state');
  if(!SUPPORT_STATES.includes(supportState)) throw new TypeError(
    `${providerId} returned invalid ETL support state.`
  );
  if(!Array.isArray(input.warnings)) throw new TypeError(
    `${providerId} ETL result requires warnings[].`
  );
  const result = immutable({supportState,
    providerVersion: platformValue(input.providerVersion, `${providerId} provider version`),
    evidence: immutable({...plainObject(input.evidence, `${providerId} runtime evidence`)}),
    warnings: input.warnings.map(String),
    nativeDetails: immutable({...plainObject(input.nativeDetails,
      `${providerId} native details`)}),
    readCapabilities: stringList(input, 'readCapabilities', providerId),
    writeCapabilities: stringList(input, 'writeCapabilities', providerId),
    discoveryCapabilities: stringList(input, 'discoveryCapabilities', providerId),
    nativeMechanisms: stringList(input, 'nativeMechanisms', providerId),
    versionConstraints: stringList(input, 'versionConstraints', providerId),
    limitations: stringList(input, 'limitations', providerId),
    runtimeEvidence: input.runtimeEvidence === undefined ? null : immutable({
      ...plainObject(input.runtimeEvidence, `${providerId} runtime evidence`)}),
    value: input.value});
  if(supportState.startsWith('supported') && !allowEmpty && input.value === undefined) {
    throw new TypeError(`${providerId} returned empty success for ${operation}.`);
  }
  return result;
}

export class ETLAdapterRegistry {
  constructor() { this.adapters = new Map(); }
  register(providerId, adapter) {
    providerId = stablePlatformId(providerId, 'ETL provider ID'); requireAdapter(adapter, providerId);
    if(this.adapters.has(providerId)) throw new Error(`ETL adapter already registered: ${providerId}`);
    this.adapters.set(providerId, adapter); return () => this.adapters.delete(providerId);
  }
  get(providerId) {
    return providerId ? this.adapters.get(stablePlatformId(providerId, 'ETL provider ID')) ?? null : null;
  }
  list() { return [...this.adapters.keys()].sort(); }
}

function unknownStatus(providerId, operation) {
  return immutable({providerId: providerId ?? 'unknown', operation,
    supportState: 'unknown', providerVersion: null, evidence: {}, warnings: [
      'No ETL adapter response is registered.',
    ], nativeDetails: {}, readCapabilities: [], writeCapabilities: [],
    discoveryCapabilities: [], nativeMechanisms: [], versionConstraints: [],
    limitations: ['No ETL adapter response is registered.'], runtimeEvidence: null});
}

function sessionView(session) {
  return immutable({schema: 'cdeadmin.etl-session.v1', id: session.id,
    content: session.content, state: session.state, dirty: session.dirty,
    selectedNodeId: session.selectedNodeId, selectedEdgeId: session.selectedEdgeId,
    validation: session.validation, schemaPropagation: session.schemaPropagation,
    providerStatuses: [...session.providerStatuses.values()],
    previews: [...session.previews], deploymentResults: [...session.deploymentResults.values()],
    runs: [...session.runs], activeTaskId: session.activeTaskId,
    problems: [...session.problems], history: [...session.history], error: session.error,
    createdAt: session.createdAt, updatedAt: session.updatedAt});
}

export class ETLService {
  constructor({tasks, relationships, search, projectAssets, commands,
    adapters=new ETLAdapterRegistry(), events=platformEventService,
    diagnostics=diagnosticsService, now=() => new Date().toISOString()}={}) {
    if(!tasks || !relationships || !search) throw new TypeError(
      'ETL service requires Task, Relationship and Search services.'
    );
    this.tasks = tasks; this.relationships = relationships; this.search = search;
    this.projectAssets = projectAssets; this.commands = commands; this.adapters = adapters;
    this.events = events; this.diagnostics = diagnostics; this.now = now;
    this.sessions = new Map(); this.listeners = new Set(); this.sequence = 0;
    this.disposers = [
      tasks.register('etl.preview', (request, context) => this._preview(request.sessionId,
        {...request, ...context})),
      tasks.register('etl.deploy.validation', (request, context) => this._deployValidate(
        request.sessionId, {...request, ...context})),
      tasks.register('etl.run', (request, context) => this._runPipeline(request.sessionId,
        {...request, ...context})),
      search.register({id: 'etl.search', priority: 35,
        types: ['etl.asset', 'etl.node', 'etl.resource', 'etl.run'],
        search: async (query, {context={}}={}) => {
          if(!context.permissions?.includes('etl.view')) return [];
          const needle = query.toLowerCase(); const results = [];
          for(const session of this.sessions.values()) {
            if(`${session.id} ${session.content.name}`.toLowerCase().includes(needle)) {
              results.push({id: session.id, type: 'etl.asset',
                label: session.content.name || session.id, context: 'Project ETL asset'});
            }
            session.content.nodes.forEach((node) => {
              if(`${node.name} ${node.kind}`.toLowerCase().includes(needle)) results.push({
                id: node.id, type: 'etl.node', label: node.name,
                context: `${session.id} · ${node.kind}`});
              const label = node.resourceRef?.canonical;
              if(label?.toLowerCase().includes(needle)) results.push({id: etlReferenceKey(
                node.resourceRef), type: 'etl.resource', label,
              context: `${session.id} · live provider resource`, reference: node.resourceRef});
            });
            session.runs.forEach((run) => {
              if(`${run.id} ${run.state}`.toLowerCase().includes(needle)) results.push({
                id: run.id, type: 'etl.run', label: run.id,
                context: `${session.id} · ${run.state}`});
            });
          }
          return results;
        }}),
    ];
  }

  dispose() {
    for(const session of this.sessions.values()) this._clearRelationships(session);
    this.disposers.reverse().forEach((remove) => remove()); this.disposers = [];
  }
  subscribe(listener) { this.listeners.add(listener); return () => this.listeners.delete(listener); }
  _emit(session) { this.listeners.forEach((listener) => listener(sessionView(session))); }
  _touch(session, {dirty=session.dirty}={}) {
    session.updatedAt = this.now(); session.dirty = dirty; this._emit(session); return sessionView(session);
  }

  create(input={}) {
    plainObject(input, 'ETL session');
    const id = input.id ?? `etl-${++this.sequence}`;
    if(this.sessions.has(id)) throw new Error(`ETL session already exists: ${id}`);
    const content = createETLContent(input.content ?? input); const time = this.now();
    const validation = validatePipeline(content);
    const session = {id, content,
      state: content.nodes.length ? (validation.valid ? 'ready' : 'validation_error') : 'empty',
      dirty: false, selectedNodeId: null, selectedEdgeId: null, validation,
      schemaPropagation: propagateSchemas(content), providerStatuses: new Map(),
      previews: [], deploymentResults: new Map(), runs: [], activeTaskId: null,
      problems: [...validation.details], history: [], error: '', createdAt: time, updatedAt: time};
    this.sessions.set(id, session); this._mirrorRelationships(session); this._emit(session);
    return sessionView(session);
  }

  get(id) {
    const session = this.sessions.get(String(id));
    if(!session) throw new Error(`Unknown ETL session: ${id}`);
    return sessionView(session);
  }
  list() { return [...this.sessions.values()].map(sessionView); }

  select(id, {nodeId=null, edgeId=null}={}) {
    const session = this.sessions.get(String(id)); if(!session) return this.get(id);
    if(nodeId && !session.content.nodes.some((item) => item.id === nodeId)) throw new Error(
      `Unknown ETL node: ${nodeId}`
    );
    if(edgeId && !session.content.edges.some((item) => item.id === edgeId)) throw new Error(
      `Unknown ETL edge: ${edgeId}`
    );
    session.selectedNodeId = nodeId; session.selectedEdgeId = edgeId; return this._touch(session);
  }

  replaceDefinition(id, input, context={}) {
    const session = this.sessions.get(String(id)); if(!session) return this.get(id);
    session.content = createETLContent(input); this._revalidate(session);
    session.history.push({at: this.now(), actor: actor(context.currentUser),
      action: 'etl.pipeline.create'});
    this._mirrorRelationships(session);
    this.events.publish('etl.pipeline.changed', {sessionId: id,
      nodeCount: session.content.nodes.length, edgeCount: session.content.edges.length},
    {origin: ETL_MODULE_ID});
    return this._touch(session, {dirty: true});
  }

  addNode(id, input, context={}) {
    const session = this.sessions.get(String(id)); if(!session) return this.get(id);
    const node = validatePipelineNode(input);
    if(session.content.nodes.some((item) => item.id === node.id)) throw new Error(
      `conflict: ETL node already exists: ${node.id}`
    );
    session.content = createETLContent({...session.content, nodes: [...session.content.nodes, node]});
    session.selectedNodeId = node.id; this._revalidate(session); this._mirrorRelationships(session);
    session.history.push({at: this.now(), actor: actor(context.currentUser),
      action: 'etl.node.add', nodeId: node.id});
    this.events.publish('etl.pipeline.changed', {sessionId: id, nodeId: node.id,
      action: 'node.add'}, {origin: ETL_MODULE_ID});
    return this._touch(session, {dirty: true});
  }

  connectEdge(id, input, context={}) {
    const session = this.sessions.get(String(id)); if(!session) return this.get(id);
    const edge = validatePipelineEdge(input);
    if(session.content.edges.some((item) => item.id === edge.id)) throw new Error(
      `conflict: ETL edge already exists: ${edge.id}`
    );
    const candidate = createETLContent({...session.content, edges: [...session.content.edges, edge]});
    const result = validateEdgeConnection(candidate, edge);
    if(!result.valid) throw new Error(`configuration_invalid: ${result.details.join(' ')}`);
    session.content = candidate; session.selectedEdgeId = edge.id;
    this._revalidate(session); this._mirrorRelationships(session);
    session.history.push({at: this.now(), actor: actor(context.currentUser),
      action: 'etl.edge.connect', edgeId: edge.id});
    this.events.publish('etl.pipeline.changed', {sessionId: id, edgeId: edge.id,
      action: 'edge.connect'}, {origin: ETL_MODULE_ID});
    return this._touch(session, {dirty: true});
  }

  editMappings(id, edgeId, mappings, context={}) {
    const session = this.sessions.get(String(id)); if(!session) return this.get(id);
    const current = session.content.edges.find((item) => item.id === edgeId);
    if(!current) throw new Error(`configuration_invalid: Unknown ETL edge: ${edgeId}`);
    const edge = validatePipelineEdge({...current, mappings});
    session.content = createETLContent({...session.content,
      edges: session.content.edges.map((item) => item.id === edgeId ? edge : item)});
    session.selectedEdgeId = edgeId; this._revalidate(session);
    session.history.push({at: this.now(), actor: actor(context.currentUser),
      action: 'etl.mapping.edit', edgeId});
    this.events.publish('etl.pipeline.changed', {sessionId: id, edgeId,
      action: 'mapping.edit'}, {origin: ETL_MODULE_ID});
    return this._touch(session, {dirty: true});
  }

  validate(id) {
    const session = this.sessions.get(String(id)); if(!session) return this.get(id);
    this._revalidate(session); return this._touch(session);
  }

  _revalidate(session) {
    session.validation = validatePipeline(session.content);
    session.schemaPropagation = propagateSchemas(session.content);
    session.problems = [...session.validation.details, ...session.validation.warnings];
    session.state = !session.content.nodes.length ? 'empty' :
      session.validation.valid ? 'ready' : 'validation_error';
  }

  _task(session, type, label, extra={}, taskContext={}) {
    const deployment = session.content.deployments.find((item) => item.id === extra.deploymentId);
    const refs = [...session.content.nodes.map((item) => item.resourceRef).filter(Boolean),
      ...(deployment?.bindings.map((item) => item.resourceRef) ?? [])];
    const task = this.tasks.submit({id: `${session.id}:${type}:${++this.sequence}`,
      type, label, sessionId: session.id, resourceRefs: refs, assetRefs: [],
      audit: {moduleId: ETL_MODULE_ID, operation: type, actor: actor(taskContext.currentUser),
        environment: deployment?.environment ?? null,
        confirmationReference: taskContext.confirmationReference ?? null}, ...extra},
    {owner: {moduleId: ETL_MODULE_ID, sessionId: session.id}, ...taskContext});
    session.activeTaskId = task.id; session.state = 'background_task_active'; this._touch(session);
    this.tasks.wait(task.id).then(() => {
      if(session.activeTaskId === task.id) { session.activeTaskId = null;
        session.state = session.validation.valid ? 'ready' : 'validation_error';
        session.error = ''; this._touch(session); }
    }).catch((error) => {
      const code = error.name === 'AbortError' ? 'task_cancelled' :
        String(error.message).split(':')[0].replaceAll(' ', '_');
      this.reportError(session.id, error, ERROR_CODES.includes(code) ? code : 'task_failed');
    });
    return task;
  }

  preview(id, {nodeIds=[], limit=100, streamScope=null, retry}={}, context={}) {
    const session = this.sessions.get(String(id)); if(!session) return this.get(id);
    const validation = this.validate(id).validation;
    if(!validation.valid) throw new Error(`configuration_invalid: ${validation.details.join(' ')}`);
    if(!Number.isInteger(limit) || limit < 1 || limit > 10000) throw new Error(
      'configuration_invalid: Preview limit must be from 1 through 10000.'
    );
    const hasStream = session.content.nodes.some((node) => node.kind === 'source' &&
      node.ports.some((port) => port.direction === 'output' && port.mode === 'stream'));
    if(hasStream && (!streamScope || typeof streamScope !== 'object' || Array.isArray(streamScope))) {
      throw new Error('configuration_invalid: Stream preview requires an explicit time or partition scope.');
    }
    const selected = nodeIds.length ? nodeIds : validation.order;
    selected.forEach((nodeId) => {
      if(!session.content.nodes.some((node) => node.id === nodeId)) throw new Error(
        `configuration_invalid: Unknown preview node: ${nodeId}`
      );
    });
    return this._task(session, 'etl.preview', 'Preview ETL pipeline',
      {nodeIds: selected, limit, streamScope, retry}, context);
  }

  deploy(id, {deploymentId, retry}={}, context={}) {
    const session = this.sessions.get(String(id)); if(!session) return this.get(id);
    const deployment = session.content.deployments.find((item) => item.id === deploymentId);
    if(!deployment) throw new Error(`configuration_invalid: Unknown deployment: ${deploymentId}`);
    const validation = this.validate(id).validation;
    if(!validation.deployable) throw new Error(
      `configuration_invalid: ${[...validation.details, ...validation.warnings].join(' ')}`
    );
    return this._task(session, 'etl.deploy.validation', 'Validate ETL deployment',
      {deploymentId, retry}, context);
  }

  startRun(id, {deploymentId, parameters={}, retry}={}, context={}) {
    const session = this.sessions.get(String(id)); if(!session) return this.get(id);
    plainObject(parameters, 'ETL runtime parameters'); noRawSecrets(parameters, 'ETL runtime parameters');
    const deployed = session.deploymentResults.get(deploymentId);
    if(!deployed || deployed.state !== 'validated') throw new Error(
      'configuration_invalid: Deployment must pass validation before execution.'
    );
    return this._task(session, 'etl.run', 'Run ETL pipeline',
      {deploymentId, parameters, retry, resumeRunId: null}, context);
  }

  resumeRun(id, {runId, retry}={}, context={}) {
    const session = this.sessions.get(String(id)); if(!session) return this.get(id);
    const previous = session.runs.find((item) => item.id === runId);
    if(!previous || !['failed', 'cancelled'].includes(previous.state) || !previous.checkpoint) {
      throw new Error('configuration_invalid: Only a failed or cancelled run with a checkpoint can resume.');
    }
    return this._task(session, 'etl.run', 'Resume ETL pipeline from checkpoint', {
      deploymentId: previous.deploymentId, parameters: previous.parameters,
      resumeRunId: runId, retry}, context);
  }

  cancel(id, taskId) {
    const session = this.sessions.get(String(id)); if(!session) return this.get(id);
    if(!taskId || session.activeTaskId !== taskId) throw new Error(
      'configuration_invalid: The task is not active for this ETL session.'
    );
    return this.tasks.cancel(taskId);
  }

  _deploymentResource(session, node, deployment) {
    return deployment?.bindings.find((item) => item.nodeId === node.id)?.resourceRef ??
      node.resourceRef ?? null;
  }

  _providerForNode(session, node, deployment) {
    const direct = this._deploymentResource(session, node, deployment);
    return direct ? providerIdFromRef(direct) : null;
  }

  async _provider(session, providerId, method, args, context, {allowEmpty=false}={}) {
    const adapter = this.adapters.get(providerId);
    if(!adapter) { session.providerStatuses.set(providerId ?? 'unknown',
      unknownStatus(providerId, method)); throw new Error(
      `capability_missing: ETL provider support is unknown for ${providerId ?? 'unbound node'}.`
    ); }
    if(context.signal?.aborted) throw abortError();
    const result = validateETLProviderResult(await adapter[method](...args),
      providerId, method, {allowEmpty});
    session.providerStatuses.set(providerId, immutable({...result, providerId,
      operation: method, value: undefined}));
    result.warnings.forEach((warning) => context.warning?.(warning, {providerId}));
    if(!result.supportState.startsWith('supported')) throw new Error(
      `capability_missing: ${providerId} reports ${result.supportState} for ${method}.`
    );
    return {adapter, result};
  }

  async _preview(id, context) {
    const session = this.sessions.get(String(id)); const outputs = new Map(); const stages = [];
    const order = topologicalOrder(session.content).order.filter((nodeId) =>
      context.nodeIds.includes(nodeId));
    for(const [index, nodeId] of order.entries()) {
      if(context.signal?.aborted) throw abortError();
      const node = session.content.nodes.find((item) => item.id === nodeId);
      context.phase?.('previewing', `Previewing ${node.name}`);
      const inputs = session.content.edges.filter((edge) => edge.toNodeId === node.id)
        .map((edge) => outputs.get(edge.fromNodeId)).filter(Boolean);
      let value;
      if(node.kind === 'sink') value = {state: 'proposed', rows: inputs.reduce(
        (total, item) => total + item.rows, 0), bytes: inputs.reduce(
        (total, item) => total + item.bytes, 0), output: inputs.flatMap(
        (item) => Array.isArray(item.output) ? item.output : []),
      written: false, deliveryGuarantee: weakestGuarantee(inputs.map(
        (item) => item.deliveryGuarantee))};
      else {
        const providerId = this._providerForNode(session, node, null);
        const reference = this._deploymentResource(session, node, null);
        const response = node.kind === 'source' ? await this._provider(session, providerId,
          'previewRead', [reference, {limit: context.limit, streamScope: context.streamScope,
            node}], context) : await this._provider(session, providerId,
          'executeNativeStage', [{node, inputs, preview: true, limit: context.limit}], context);
        value = response.result.value;
      }
      const stage = normalizeStageResult(value, node, {preview: true});
      if(Array.isArray(stage.output) && stage.output.length > context.limit) throw new TypeError(
        `Provider preview for ${node.id} exceeded the ${context.limit}-record budget.`
      );
      outputs.set(node.id, stage); stages.push(stage);
      context.progress?.((index + 1) / order.length, `${index + 1}/${order.length} preview steps`);
    }
    const preview = immutable({schema: 'cdeadmin.etl-preview.v1',
      id: `etl-preview-${++this.sequence}`, taskRef: {schema: 'cdeadmin.external-ref.v1',
        id: context.id ?? 'etl-preview-task'}, createdAt: this.now(), limit: context.limit,
      streamScope: context.streamScope, stages, sideEffects: false});
    session.previews.unshift(preview); session.history.push({at: this.now(),
      actor: actor(context.currentUser), action: 'etl.preview.run', previewId: preview.id});
    context.resultReference?.({schema: 'cdeadmin.result-ref.v1', id: preview.id});
    this._touch(session); return preview;
  }

  async _deployValidate(id, context) {
    const session = this.sessions.get(String(id)); const deployment = session.content.deployments
      .find((item) => item.id === context.deploymentId); const validation = validatePipeline(session.content);
    if(!validation.deployable) throw new Error(
      `configuration_invalid: ${[...validation.details, ...validation.warnings].join(' ')}`
    );
    const plans = [];
    for(const [index, nodeId] of validation.order.entries()) {
      if(context.signal?.aborted) throw abortError();
      const node = session.content.nodes.find((item) => item.id === nodeId);
      const reference = this._deploymentResource(session, node, deployment);
      const providerId = this._providerForNode(session, node, deployment);
      context.phase?.('validating', `Validating ${node.name}`);
      if(node.kind === 'source') await this._provider(session, providerId,
        'getSourceCapabilities', [reference], context);
      if(node.kind === 'sink') await this._provider(session, providerId,
        'getSinkCapabilities', [reference], context);
      const response = await this._provider(session, providerId, 'planPushdown',
        [node, {deployment, pipeline: session.content}], context);
      plans.push(normalizePushdownPlan(response.result.value, node.id));
      context.progress?.((index + 1) / validation.order.length,
        `${index + 1}/${validation.order.length} deployment steps`);
    }
    const result = immutable({schema: 'cdeadmin.etl-deployment-result.v1',
      id: `etl-deployment-${++this.sequence}`, deploymentId: deployment.id,
      environment: deployment.environment, state: 'validated', validatedAt: this.now(), plans,
      providerStatuses: [...session.providerStatuses.values()]});
    session.deploymentResults.set(deployment.id, result); session.history.push({at: this.now(),
      actor: actor(context.currentUser), action: 'etl.pipeline.deploy',
      deploymentId: deployment.id, environment: deployment.environment});
    context.resultReference?.({schema: 'cdeadmin.result-ref.v1', id: result.id});
    this._touch(session); return result;
  }

  async _executeStage(session, run, node, inputs, deployment, previous, context) {
    const providerId = this._providerForNode(session, node, deployment);
    const resourceRef = this._deploymentResource(session, node, deployment);
    const request = {node, resourceRef, inputs, deployment, parameters: context.parameters,
      checkpoint: previous?.checkpoint ?? null, preview: false};
    const route = node.errorRoute; let attempt = 0;
    const totalAttempts = route?.action === 'retry' ? route.maximumAttempts + 1 : 1;
    for(;;) {
      attempt++;
      try {
        const response = await this._provider(session, providerId, 'executeNativeStage',
          [request], context);
        const normalized = normalizeStageResult(response.result.value, node);
        if(!['succeeded', 'succeeded_with_warnings'].includes(normalized.state)) throw new Error(
          `task_failed: ETL stage ${node.id} returned ${normalized.state}.`
        );
        return {response, stage: immutable({...normalized, attempts: attempt,
          errorAction: null, routedTargetRef: null})};
      } catch(error) {
        await this.events.publish('etl.step.failed', {sessionId: session.id,
          runId: run.id, nodeId: node.id, attempt, error: error.message,
          errorAction: route?.action ?? 'stop'}, {origin: ETL_MODULE_ID});
        if(error.name === 'AbortError') throw error;
        if(route?.action === 'retry' && attempt < totalAttempts) {
          context.warning?.(`Retrying ${node.name} after attempt ${attempt}.`, {
            nodeId: node.id, attempt, maximumAttempts: route.maximumAttempts,
            retryPolicy: route.retryPolicy});
          continue;
        }
        if(route?.action === 'reject') {
          const stage = normalizeStageResult({state: 'succeeded_with_warnings', rows: 0,
            bytes: 0, output: null, written: false, deliveryGuarantee: 'best_effort',
            diagnostics: {rejected: true, error: error.message}}, node);
          return {response: {result: {evidence: {errorRoute: 'reject'}}},
            stage: immutable({...stage, attempts: attempt, errorAction: 'reject',
              routedTargetRef: null})};
        }
        if(route?.action === 'dead_letter') {
          const routeProviderId = providerIdFromRef(route.targetRef);
          const routed = await this._provider(session, routeProviderId, 'executeNativeStage', [{
            node, resourceRef: route.targetRef, inputs: [], deployment,
            parameters: context.parameters, checkpoint: null, preview: false,
            errorRoute: {sourceResourceRef: resourceRef, failedNodeId: node.id,
              message: error.message, attempt},
          }], context);
          const routedStage = normalizeStageResult(routed.result.value, node);
          if(!['succeeded', 'succeeded_with_warnings'].includes(routedStage.state)) {
            throw new Error(`task_failed: Dead-letter route for ${node.id} returned ${
              routedStage.state}.`);
          }
          return {response: routed, stage: immutable({...routedStage,
            state: 'succeeded_with_warnings', output: null, attempts: attempt,
            errorAction: 'dead_letter', routedTargetRef: route.targetRef,
            diagnostics: {...routedStage.diagnostics, routedError: error.message}})};
        }
        throw error;
      }
    }
  }

  async _runPipeline(id, context) {
    const session = this.sessions.get(String(id)); const deployment = session.content.deployments
      .find((item) => item.id === context.deploymentId);
    const previous = context.resumeRunId ? session.runs.find((item) => item.id === context.resumeRunId) : null;
    const completed = new Map((previous?.stages ?? []).filter((item) => item.state === 'succeeded')
      .map((item) => [item.nodeId, item]));
    const run = {schema: 'cdeadmin.etl-run.v1', id: `etl-run-${++this.sequence}`,
      deploymentId: deployment.id, environment: deployment.environment,
      parameters: immutable({...context.parameters}), resumedFrom: previous?.id ?? null,
      startedAt: this.now(), finishedAt: null, state: 'running', stages: [], checkpoint: null,
      deliveryGuarantee: 'unknown', error: ''};
    session.runs.unshift(run); await this.events.publish('etl.run.started', {
      sessionId: id, runId: run.id, deploymentId: deployment.id,
      resumedFrom: previous?.id ?? null}, {origin: ETL_MODULE_ID});
    try {
      const outputs = new Map(); const order = topologicalOrder(session.content).order;
      for(const [index, nodeId] of order.entries()) {
        if(context.signal?.aborted) throw abortError();
        const node = session.content.nodes.find((item) => item.id === nodeId);
        const oldStage = completed.get(node.id);
        if(oldStage) { run.stages.push(immutable({...oldStage, resumedWithoutReplay: true}));
          outputs.set(node.id, oldStage); continue; }
        context.phase?.('executing', `Executing ${node.name}`);
        const inputs = session.content.edges.filter((edge) => edge.toNodeId === node.id)
          .map((edge) => outputs.get(edge.fromNodeId)).filter(Boolean);
        const {response, stage} = await this._executeStage(session, run, node, inputs,
          deployment, previous, context);
        run.stages.push(stage); outputs.set(node.id, stage);
        if(stage.checkpoint) run.checkpoint = immutable({nodeId: node.id,
          position: stage.checkpoint, partition: stage.partition});
        await this._emitLineage(session, run, node, stage, response.result);
        context.progress?.((index + 1) / order.length, `${index + 1}/${order.length} stages`);
      }
      run.state = 'succeeded'; run.finishedAt = this.now();
      run.deliveryGuarantee = weakestGuarantee(run.stages.map((item) => item.deliveryGuarantee));
      session.runs[0] = immutable({...run}); session.history.push({at: this.now(),
        actor: actor(context.currentUser), action: 'etl.run.start', runId: run.id,
        result: 'succeeded', environment: deployment.environment});
      context.resultReference?.({schema: 'cdeadmin.result-ref.v1', id: run.id});
      await this.events.publish('etl.run.completed', {sessionId: id, runId: run.id,
        state: run.state, deliveryGuarantee: run.deliveryGuarantee}, {origin: ETL_MODULE_ID});
      this._touch(session); return session.runs[0];
    } catch(error) {
      run.state = error.name === 'AbortError' ? 'cancelled' : 'failed'; run.error = error.message;
      run.finishedAt = this.now(); run.deliveryGuarantee = weakestGuarantee(
        run.stages.map((item) => item.deliveryGuarantee)
      ); session.runs[0] = immutable({...run}); session.history.push({at: this.now(),
        actor: actor(context.currentUser), action: previous ? 'etl.run.resume' : 'etl.run.start',
        runId: run.id, result: run.state, environment: deployment.environment});
      await this.events.publish('etl.run.completed', {sessionId: id, runId: run.id,
        state: run.state}, {origin: ETL_MODULE_ID}); this._touch(session); throw error;
    }
  }

  async _emitLineage(session, run, node, stage, providerResult) {
    const edges = [];
    for(const edge of session.content.edges.filter((item) => item.fromNodeId === node.id)) {
      edges.push({fromNodeId: edge.fromNodeId, toNodeId: edge.toNodeId,
        edgeId: edge.id, evidence: providerResult.evidence});
    }
    if(stage.lineage.length) edges.push(...stage.lineage);
    if(edges.length) await this.events.publish('etl.lineage.emitted', {sessionId: session.id,
      runId: run.id, nodeId: node.id, edges,
      origin: 'observed_trace', evidence: providerResult.evidence}, {origin: ETL_MODULE_ID});
  }

  async save(id, options) {
    const session = this.sessions.get(String(id)); if(!session) return this.get(id);
    if(!this.projectAssets) throw new Error('internal_error: Project asset service is unavailable.');
    try {
      const request = etlAssetRequest({...options, content: session.content});
      const saved = await this.projectAssets.saveAsset(options.projectId, options.assetId, request);
      session.history.push({at: this.now(), action: 'save', assetVersion: saved.version});
      return this._touch(session, {dirty: false});
    } catch(error) { this.reportError(id, error, error.message.includes('conflict') ?
      'conflict' : 'internal_error'); throw error; }
  }

  reportError(id, error, code='internal_error') {
    const session = this.sessions.get(String(id)); if(!session) return;
    const normalized = ERROR_CODES.includes(code) ? code : 'internal_error';
    session.error = String(error?.message ?? error); session.problems.push(session.error);
    session.activeTaskId = null;
    session.state = normalized === 'permission_denied' ? 'permission_denied' :
      normalized === 'provider_unavailable' ? 'disconnected' :
        ['capability_missing', 'partial_result'].includes(normalized) ? 'partial' :
          normalized === 'stale_result' ? 'stale' :
            normalized === 'configuration_invalid' ? 'validation_error' : 'runtime_failure';
    this.diagnostics.report({id: `etl.${normalized}`, code: normalized, message: session.error,
      origin: ETL_MODULE_ID, severity: 'error', context: {sessionId: id}});
    return this._touch(session);
  }

  _clearRelationships(session) {
    const prefix = `etl:${session.id}`; const graph = this.relationships.snapshot();
    graph.edges.filter((item) => item.id.startsWith(prefix)).forEach(
      (item) => this.relationships.removeEdge(item.id));
    graph.nodes.filter((item) => item.id.startsWith(prefix)).forEach(
      (item) => this.relationships.removeNode(item.id));
  }

  _mirrorRelationships(session) {
    this._clearRelationships(session); const root = `etl:${session.id}`;
    this.relationships.upsertNode({id: root, kind: 'etl.asset',
      label: session.content.name || session.id});
    session.content.nodes.forEach((node) => {
      const nodeId = `${root}:node:${node.id}`;
      this.relationships.upsertNode({id: nodeId, kind: `etl.${node.kind}`, label: node.name});
      this.relationships.upsertEdge({id: `${root}:contains:${node.id}`, from: root, to: nodeId,
        relation: 'contains', origin: 'project_declared'});
      if(node.resourceRef) {
        const refId = `${root}:reference:${etlReferenceKey(node.resourceRef)}`;
        this.relationships.upsertNode({id: refId, kind: 'provider.resource',
          label: node.resourceRef.canonical, reference: node.resourceRef});
        this.relationships.upsertEdge({id: `${root}:resource:${node.id}`, from: nodeId, to: refId,
          relation: node.kind === 'source' ? 'reads' : node.kind === 'sink' ? 'writes' :
            'depends_on', origin: 'etl_declared'});
      }
      node.ports.filter((port) => port.schemaRef).forEach((port) => {
        const refId = `${root}:schema:${node.id}:${port.id}`;
        this.relationships.upsertNode({id: refId, kind: port.schemaRef.schema ===
          'cdeadmin.asset-ref.v1' ? 'project.asset' : 'provider.resource',
        label: referenceLabel(port.schemaRef), reference: port.schemaRef});
        this.relationships.upsertEdge({id: `${root}:schema-edge:${node.id}:${port.id}`,
          from: nodeId, to: refId, relation: 'governs', origin: 'etl_declared',
          metadata: {portId: port.id, referenceRole: 'schema'}});
      });
      nestedReferences(node.config).forEach(({path, reference}, index) => {
        const refId = `${root}:config:${node.id}:${index}`;
        this.relationships.upsertNode({id: refId, kind: reference.schema ===
          'cdeadmin.asset-ref.v1' ? 'project.asset' : reference.schema ===
            'cdeadmin.resource-ref.v1' ? 'provider.resource' : 'external.reference',
        label: referenceLabel(reference), reference});
        this.relationships.upsertEdge({id: `${root}:config-edge:${node.id}:${index}`,
          from: nodeId, to: refId, relation: node.kind === 'quality_gate' ? 'validates' :
            'depends_on', origin: 'etl_declared', metadata: {path}});
      });
    });
    session.content.edges.forEach((edge) => this.relationships.upsertEdge({
      id: `${root}:flow:${edge.id}`, from: `${root}:node:${edge.fromNodeId}`,
      to: `${root}:node:${edge.toNodeId}`, relation: 'transforms', origin: 'etl_declared',
      metadata: {edgeId: edge.id, deliveryGuarantee: edge.deliveryGuarantee}}));
    session.content.schedules.forEach((schedule) => schedule.dependencyRefs.forEach(
      (reference, index) => {
        const refId = `${root}:schedule:${schedule.id}:${index}`;
        this.relationships.upsertNode({id: refId, kind: reference.schema ===
          'cdeadmin.asset-ref.v1' ? 'project.asset' : reference.schema ===
            'cdeadmin.resource-ref.v1' ? 'provider.resource' : 'external.reference',
        label: referenceLabel(reference), reference});
        this.relationships.upsertEdge({id: `${root}:schedule-edge:${schedule.id}:${index}`,
          from: root, to: refId, relation: 'depends_on', origin: 'etl_declared',
          metadata: {scheduleId: schedule.id}});
      }));
  }
}
