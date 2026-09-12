/////////////////////////////////////////////////////////////
// Data Lineage orchestration, provider evidence and persistence.
/////////////////////////////////////////////////////////////

import {
  diagnosticsService, platformEventService, stablePlatformId,
} from '../../platform/PlatformRegistry';
import {
  abortError, immutable, noRawSecrets, plainObject, platformValue,
} from '../../platform/serviceUtils';
import {
  createLineageContent, lineageAssetRequest, lineageSuppressionKey,
  validateEvidence, validateLineageEdge, validateLineageNode,
} from './contracts';
import {
  compareLineageSnapshots, createSnapshot, impactAnalysis, reconcileLineage,
  stableDigest, traverseLineage,
} from './LineageEngine';
import {
  exportOpenLineageBundle, importOpenLineage,
} from './OpenLineageAdapter';

export const LINEAGE_SERVICE_ID = 'lineage.runtime';
export const LINEAGE_TASKS = Object.freeze([
  'lineage.discovery.scan', 'lineage.parsing.extract', 'lineage.reconcile',
  'lineage.impact.compute', 'lineage.import',
]);
export const LINEAGE_EVENTS = Object.freeze([
  'lineage.edge.created', 'lineage.edge.changed', 'lineage.edge.expired',
  'lineage.snapshot.created', 'lineage.scan.completed',
]);

const SUPPORT_STATES = Object.freeze([
  'supported_native', 'supported_via_cdeadmin', 'supported_via_external_adapter',
  'read_only', 'partial', 'unsupported', 'unknown',
]);

function requireAdapter(adapter, providerId) {
  [
    'discoverDeclaredRelationships', 'parseProviderLineage',
    'resolveNativeResource', 'fetchExecutionEvidence', 'supportsFieldLineage',
  ].forEach((method) => {
    if(typeof adapter?.[method] !== 'function') throw new TypeError(
      `Lineage adapter ${providerId} requires ${method}().`
    );
  });
}

function providerResult(input, providerId, operation, {allowEmpty=false}={}) {
  plainObject(input, `${providerId} ${operation} result`);
  noRawSecrets(input, `${providerId} ${operation} result`);
  const supportState = platformValue(input.supportState, 'Lineage support state');
  if(!SUPPORT_STATES.includes(supportState)) throw new TypeError(
    `${providerId} returned invalid Lineage support state.`
  );
  if(!Array.isArray(input.warnings)) throw new TypeError(
    `${providerId} Lineage result requires warnings[].`
  );
  const capabilityFields = ['readCapabilities', 'writeCapabilities',
    'discoveryCapabilities', 'nativeMechanisms', 'versionConstraints', 'limitations'];
  capabilityFields.forEach((field) => {
    if(!Array.isArray(input[field])) throw new TypeError(
      `${providerId} Lineage result requires ${field}[].`
    );
  });
  const result = immutable({supportState,
    providerVersion: platformValue(input.providerVersion, `${providerId} provider version`),
    evidence: plainObject(input.evidence, `${providerId} runtime evidence`),
    warnings: [...input.warnings], ...Object.fromEntries(capabilityFields.map((field) => [field,
      [...new Set(input[field].map((item) => platformValue(item,
        `${providerId} ${field}`)))].sort()])),
    runtimeEvidence: input.runtimeEvidence === undefined ? null :
      plainObject(input.runtimeEvidence, `${providerId} runtime evidence`),
    nativeDetails: plainObject(input.nativeDetails, `${providerId} native details`),
    value: input.value});
  if(supportState.startsWith('supported') && !allowEmpty && input.value === undefined) {
    throw new TypeError(`${providerId} returned empty success for ${operation}.`);
  }
  return result;
}

export class LineageAdapterRegistry {
  constructor() { this.adapters = new Map(); }

  register(providerId, adapter) {
    providerId = stablePlatformId(providerId, 'Lineage provider ID');
    requireAdapter(adapter, providerId);
    if(this.adapters.has(providerId)) throw new Error(`Lineage adapter already registered: ${providerId}`);
    this.adapters.set(providerId, adapter); return () => this.adapters.delete(providerId);
  }

  get(providerId) { return this.adapters.get(stablePlatformId(providerId, 'Lineage provider ID')) ?? null; }
  list() { return [...this.adapters.keys()].sort(); }
}

function sessionView(session) {
  return immutable({schema: 'cdeadmin.lineage-session.v1', id: session.id,
    content: session.content, state: session.state, dirty: session.dirty,
    graph: session.graph, traversal: session.traversal, impact: session.impact,
    snapshots: [...session.snapshots.values()], snapshotDiff: session.snapshotDiff,
    imports: [...session.imports], ingestion: [...session.ingestion.values()],
    selectedNodeId: session.selectedNodeId, selectedEdgeId: session.selectedEdgeId,
    activeTaskId: session.activeTaskId, error: session.error,
    problems: [...session.problems], history: [...session.history],
    createdAt: session.createdAt, updatedAt: session.updatedAt});
}

function structuralSafety(edge) {
  if(edge.nativeDetails?.structuralRelationship !== 'foreign_key') return edge;
  const hasFlowEvidence = edge.evidence.some((item) => [
    'execution_plan', 'observed_trace', 'parsed_query', 'etl_declared',
    'cdc_declared', 'migration_declared',
  ].includes(item.origin));
  return hasFlowEvidence ? edge : {...edge, type: 'structural_reference'};
}

export class LineageService {
  constructor({adapters=new LineageAdapterRegistry(), tasks, relationships, search,
    projectAssets, events=platformEventService, diagnostics=diagnosticsService,
    now=() => new Date().toISOString()}={}) {
    if(!tasks || !relationships || !search || !projectAssets) throw new TypeError(
      'Lineage requires task, relationship, search and project asset services.'
    );
    this.adapters = adapters; this.tasks = tasks; this.relationships = relationships;
    this.search = search; this.projectAssets = projectAssets; this.events = events;
    this.diagnostics = diagnostics; this.now = now; this.sessions = new Map();
    this.listeners = new Set(); this.sequence = 0; this.disposers = [];
    this._registerTasks(); this._registerSearch();
  }

  _registerTasks() {
    const runner = (method) => async (request, context) =>
      this[method](request.sessionId, {...request, ...context});
    this.disposers.push(
      this.tasks.register('lineage.discovery.scan', runner('_discover')),
      this.tasks.register('lineage.parsing.extract', runner('_parse')),
      this.tasks.register('lineage.reconcile', runner('_reconcile')),
      this.tasks.register('lineage.impact.compute', runner('_impact')),
      this.tasks.register('lineage.import', runner('_import')),
    );
  }

  _registerSearch() {
    this.disposers.push(this.search.register({id: 'lineage.search', priority: 35,
      types: ['lineage.asset', 'lineage.node', 'lineage.snapshot', 'lineage.saved_view'],
      search: async (query, {context={}}={}) => {
        if(context.permissions && !context.permissions.includes('lineage.view')) return [];
        const needle = query.toLowerCase(); const results = [];
        for(const session of this.sessions.values()) {
          if(session.id.toLowerCase().includes(needle)) results.push({id: session.id,
            type: 'lineage.asset', label: session.id, context: 'Project lineage asset'});
          for(const node of session.graph.nodes) if(`${node.namespace} ${node.name} ${node.kind}`
            .toLowerCase().includes(needle)) results.push({id: node.id, type: 'lineage.node',
            label: node.name, context: `${node.namespace} · ${node.kind}`});
          for(const snapshot of session.snapshots.values()) if(snapshot.id.toLowerCase()
            .includes(needle)) results.push({id: snapshot.id, type: 'lineage.snapshot',
            label: snapshot.id, context: session.id});
          for(const [id, filter] of Object.entries(session.content.savedFilters)) if(`${id} ${
            JSON.stringify(filter)}`.toLowerCase().includes(needle)) results.push({id,
            type: 'lineage.saved_view', label: id, context: session.id});
        }
        return results;
      }}));
  }

  dispose() { this.disposers.reverse().forEach((dispose) => dispose()); this.disposers = []; }
  subscribe(listener) { this.listeners.add(listener); return () => this.listeners.delete(listener); }
  _emit(session) { this.listeners.forEach((listener) => listener(sessionView(session))); }
  _touch(session, {dirty=session.dirty}={}) {
    session.updatedAt = this.now(); session.dirty = dirty; this._emit(session); return sessionView(session);
  }

  create(input={}) {
    plainObject(input, 'Lineage session'); noRawSecrets(input, 'Lineage session');
    const id = input.id ?? `lineage-${++this.sequence}`;
    if(this.sessions.has(id)) throw new Error(`Lineage session already exists: ${id}`);
    const at = this.now(); const content = createLineageContent(input.content ?? input);
    const session = {id, content, state: 'empty', dirty: Boolean(input.dirty),
      graph: immutable({schema: 'cdeadmin.lineage-graph.v1', revision: stableDigest({}),
        nodes: [], edges: []}), traversal: null, impact: null, snapshots: new Map(),
      snapshotDiff: null, imports: [], ingestion: new Map(), candidates: [], parsed: [],
      selectedNodeId: null, selectedEdgeId: null, activeTaskId: null, error: '',
      problems: [], history: [], createdAt: at, updatedAt: at};
    this.sessions.set(id, session); this._touch(session); return sessionView(session);
  }

  get(id) {
    const session = this.sessions.get(String(id));
    if(!session) throw new Error(`Unknown Lineage session: ${id}`);
    return sessionView(session);
  }
  list() { return [...this.sessions.values()].map(sessionView); }

  configure(id, changes) {
    const session = this.sessions.get(String(id)); if(!session) return this.get(id);
    session.content = createLineageContent({...session.content, ...changes});
    session.state = session.content.scopeRefs.length ? 'ready' : 'empty';
    return this._touch(session, {dirty: true});
  }

  select(id, {nodeId=null, edgeId=null}={}) {
    const session = this.sessions.get(String(id)); if(!session) return this.get(id);
    if(nodeId && !session.graph.nodes.some((item) => item.id === nodeId)) throw new Error(
      `Unknown Lineage node: ${nodeId}`
    );
    if(edgeId && !session.graph.edges.some((item) => item.id === edgeId)) throw new Error(
      `Unknown Lineage edge: ${edgeId}`
    );
    session.selectedNodeId = nodeId; session.selectedEdgeId = edgeId;
    return this._touch(session);
  }

  _task(session, type, label, extra={}) {
    const task = this.tasks.submit({id: `${session.id}:${type}:${++this.sequence}`,
      type, label, sessionId: session.id, assetRefs: [],
      resourceRefs: session.content.scopeRefs, ...extra}, {
      owner: {moduleId: 'cdeadmin.lineage', sessionId: session.id}});
    session.activeTaskId = task.id; session.state = 'background_task_active'; this._touch(session);
    this.tasks.wait(task.id).then(() => {
      if(session.activeTaskId === task.id) { session.activeTaskId = null;
        session.state = this._settledState(session);
        session.error = ''; this._touch(session); }
    }).catch((error) => this.reportError(session.id, error,
      error.name === 'AbortError' ? 'task_cancelled' : 'task_failed'));
    return task;
  }

  _settledState(session) {
    const states = [...session.ingestion.values()].map((item) => item.supportState);
    if(states.some((item) => ['unknown', 'unsupported', 'partial'].includes(item))) return 'partial';
    if(states.length && states.every((item) => item === 'read_only')) return 'read_only';
    return 'ready';
  }

  refresh(id, options={}) {
    const session = this.sessions.get(String(id)); if(!session) return this.get(id);
    if(!session.content.scopeRefs.length) throw new Error('configuration_invalid: Select a scope first.');
    session.candidates = []; session.parsed = [];
    session.ingestion.clear(); session.problems = [];
    const retry = options.retry ? {retry: options.retry} : {};
    const scan = this._task(session, 'lineage.discovery.scan', 'Discover declared lineage', {
      options, ...retry});
    const parsing = this._task(session, 'lineage.parsing.extract', 'Extract query lineage', {
      options, dependencies: [scan.id], ...retry});
    return this._task(session, 'lineage.reconcile', 'Reconcile lineage evidence', {
      options, dependencies: [parsing.id], ...retry});
  }

  _groupScopes(session) {
    const groups = new Map();
    session.content.scopeRefs.forEach((reference) => {
      const providerId = reference.provider ?? reference.providerId ??
        reference.provider_id ?? null;
      const values = groups.get(providerId ?? 'unknown') ?? [];
      values.push(reference); groups.set(providerId ?? 'unknown', values);
    });
    return groups;
  }

  async _providerStage(session, method, destination, context) {
    const groups = this._groupScopes(session); let completed = 0;
    for(const [providerId, scopes] of groups) {
      if(context.signal?.aborted) throw abortError();
      const adapter = providerId === 'unknown' ? null : this.adapters.get(providerId);
      if(!adapter) {
        session.ingestion.set(providerId, immutable({providerId, supportState: 'unknown',
          providerVersion: null, warnings: ['No Lineage adapter response is registered.'],
          readCapabilities: [], writeCapabilities: [], discoveryCapabilities: [],
          nativeMechanisms: [], versionConstraints: [], limitations: [
            'No Lineage adapter response is registered.',
          ], runtimeEvidence: null, nativeDetails: {}, evidence: {},
          operationStates: {[method]: 'unknown'}, state: 'unknown'}));
        session.problems.push({code: 'capability_missing', providerId,
          message: 'Lineage capability is unknown because no adapter response is registered.'});
      } else {
        const response = providerResult(await adapter[method](scopes, context.options ?? {}),
          providerId, method);
        const prior = session.ingestion.get(providerId);
        const combine = (field) => [...new Set([...(prior?.[field] ?? []),
          ...response[field]])].sort();
        const supportState = prior && prior.supportState !== response.supportState ?
          'partial' : response.supportState;
        session.ingestion.set(providerId, immutable({...response, supportState,
          value: undefined, warnings: combine('warnings'),
          readCapabilities: combine('readCapabilities'),
          writeCapabilities: combine('writeCapabilities'),
          discoveryCapabilities: combine('discoveryCapabilities'),
          nativeMechanisms: combine('nativeMechanisms'),
          versionConstraints: combine('versionConstraints'),
          limitations: combine('limitations'),
          evidence: {...(prior?.evidence ?? {}), [method]: response.evidence},
          runtimeEvidence: response.runtimeEvidence ?? prior?.runtimeEvidence ?? null,
          nativeDetails: {...(prior?.nativeDetails ?? {}), [method]: response.nativeDetails},
          operationStates: {...(prior?.operationStates ?? {}), [method]: response.supportState},
          state: supportState}));
        response.warnings.forEach((warning) => context.warning?.(warning, {providerId}));
        if(response.supportState.startsWith('supported')) {
          const value = plainObject(response.value, `${providerId} Lineage payload`);
          if(!Array.isArray(value.nodes) || !Array.isArray(value.edges)) throw new TypeError(
            `${providerId} Lineage payload requires nodes[] and edges[].`
          );
          if(!value.nodes.length && !value.edges.length && !response.nativeDetails.emptyReason) {
            throw new TypeError(`${providerId} returned unexplained empty Lineage success.`);
          }
          const nodes = value.nodes.map(validateLineageNode);
          const policy = session.content.sourcePolicies.find((item) =>
            item.providerId === providerId) ?? null;
          const edges = value.edges.map((item) => {
            const nativeDetails = {...(item.nativeDetails ?? {}), providerId};
            const edge = structuralSafety({...item, nativeDetails});
            if(!policy?.retentionDays) return edge;
            const cutoff = Date.parse(this.now()) - policy.retentionDays * 86400000;
            return {...edge, evidence: edge.evidence.map((evidence) => ({...evidence,
              stale: evidence.stale || Boolean(evidence.capturedAt &&
                Date.parse(evidence.capturedAt) < cutoff)}))};
          }).filter((edge) => policy?.inferenceEnabled !== false || !edge.evidence.every(
            (item) => ['inferred', 'parsed_query'].includes(item.origin)
          )).map(validateLineageEdge);
          for(const unresolved of value.unresolvedIdentifiers ?? []) {
            session.problems.push({code: 'unresolved_lineage', providerId,
              message: String(unresolved)});
          }
          destination.push({nodes, edges, providerId, response});
        }
      }
      completed++; context.progress?.(completed / groups.size, `${method}: ${providerId}`);
    }
  }

  async _discover(id, context) {
    const session = this.sessions.get(String(id));
    const found = []; await this._providerStage(session,
      'discoverDeclaredRelationships', found, context);
    found.forEach((value) => session.candidates.push(...value.edges));
    session.discoveredNodes = found.flatMap((value) => value.nodes);
    return {providers: found.length, candidates: session.candidates.length};
  }

  async resolveNativeResource(providerId, identity) {
    const adapter = this.adapters.get(providerId);
    if(!adapter) return immutable({supportState: 'unknown', providerVersion: null,
      evidence: {}, warnings: ['No Lineage adapter response is registered.'],
      readCapabilities: [], writeCapabilities: [], discoveryCapabilities: [],
      nativeMechanisms: [], versionConstraints: [], limitations: [
        'No Lineage adapter response is registered.',
      ], runtimeEvidence: null, nativeDetails: {}, value: undefined});
    return providerResult(await adapter.resolveNativeResource(identity), providerId,
      'resolveNativeResource');
  }

  async fetchExecutionEvidence(providerId, queryOrRun) {
    const adapter = this.adapters.get(providerId);
    if(!adapter) return this.resolveNativeResource(providerId, queryOrRun);
    const response = providerResult(await adapter.fetchExecutionEvidence(queryOrRun),
      providerId, 'fetchExecutionEvidence');
    if(response.supportState.startsWith('supported')) {
      const value = plainObject(response.value, `${providerId} execution evidence`);
      if(!Array.isArray(value.nodes) || !Array.isArray(value.edges)) throw new TypeError(
        `${providerId} execution evidence requires nodes[] and edges[].`
      );
      const graph = reconcileLineage({nodes: value.nodes.map(validateLineageNode),
        candidateEdges: value.edges.map((edge) => validateLineageEdge(
          structuralSafety({...edge, nativeDetails: {
            ...(edge.nativeDetails ?? {}), providerId,
          }})
        ))});
      return immutable({...response, value: graph});
    }
    return response;
  }

  async fieldLineageSupport(providerId) {
    const adapter = this.adapters.get(providerId);
    if(!adapter) return this.resolveNativeResource(providerId, providerId);
    const response = providerResult(await adapter.supportsFieldLineage(), providerId,
      'supportsFieldLineage');
    if(response.supportState.startsWith('supported') && typeof response.value !== 'boolean') {
      throw new TypeError(`${providerId} field Lineage support must be boolean.`);
    }
    return response;
  }

  async _parse(id, context) {
    const session = this.sessions.get(String(id));
    const parsed = []; await this._providerStage(session, 'parseProviderLineage', parsed, context);
    parsed.forEach((value) => session.parsed.push(...value.edges));
    session.parsedNodes = parsed.flatMap((value) => value.nodes);
    return {providers: parsed.length, candidates: session.parsed.length};
  }

  _sourcePriority(session) {
    return [...new Set(session.content.sourcePolicies.flatMap(
      (policy) => policy.sourcePriority
    ))];
  }

  async _reconcile(id, context) {
    const session = this.sessions.get(String(id)); context.phase?.('reconciling', 'Reconciling evidence');
    const nodes = [...(session.discoveredNodes ?? []), ...(session.parsedNodes ?? [])];
    const byId = new Map(nodes.map((item) => [item.id, item]));
    const graph = reconcileLineage({nodes: [...byId.values()],
      candidateEdges: [...session.candidates, ...session.parsed],
      curatedEdges: session.content.curatedEdges,
      suppressedInferenceRules: session.content.suppressedInferenceRules,
      sourcePriority: this._sourcePriority(session)});
    await this._replaceGraph(session, graph);
    context.progress?.(1, 'Lineage graph reconciled');
    await this.events.publish('lineage.scan.completed', {sessionId: id,
      revision: session.graph.revision, nodeCount: session.graph.nodes.length,
      edgeCount: session.graph.edges.length}, {origin: 'cdeadmin.lineage'});
    this._touch(session); return session.graph;
  }

  async _replaceGraph(session, graph) {
    const previous = new Map(session.graph.edges.map((edge) => [edge.id, edge]));
    const current = new Map(graph.edges.map((edge) => [edge.id, edge]));
    const currentNodes = new Set(graph.nodes.map((node) => node.id));
    for(const edge of previous.values()) if(!current.has(edge.id)) {
      this.relationships.removeEdge(`lineage:${session.id}:${edge.id}`);
    }
    for(const node of session.graph.nodes) if(!currentNodes.has(node.id)) {
      this.relationships.removeNode(`lineage:${session.id}:${node.id}`);
    }
    session.graph = graph; this._mirrorRelationships(session);
    for(const edge of graph.edges) {
      const prior = previous.get(edge.id);
      if(!prior) await this.events.publish('lineage.edge.created', {
        sessionId: session.id, edgeId: edge.id, origin: edge.origin,
      }, {origin: 'cdeadmin.lineage'});
      else if(stableDigest(prior) !== stableDigest(edge)) await this.events.publish(
        'lineage.edge.changed', {sessionId: session.id, edgeId: edge.id,
          origin: edge.origin}, {origin: 'cdeadmin.lineage'}
      );
    }
    for(const edge of previous.values()) if(!current.has(edge.id)) {
      await this.events.publish('lineage.edge.expired', {
        sessionId: session.id, edgeId: edge.id, origin: edge.origin,
      }, {origin: 'cdeadmin.lineage'});
    }
  }

  _mirrorRelationships(session) {
    session.graph.nodes.forEach((node) => this.relationships.upsertNode({
      id: `lineage:${session.id}:${node.id}`, kind: `lineage.${node.kind}`,
      reference: node.reference, label: node.name,
      metadata: {namespace: node.namespace, classification: node.classification}}));
    session.graph.edges.forEach((edge) => this.relationships.upsertEdge({
      id: `lineage:${session.id}:${edge.id}`,
      from: `lineage:${session.id}:${edge.from}`, to: `lineage:${session.id}:${edge.to}`,
      relation: edge.type, origin: edge.origin, confidence: edge.confidence,
      validFrom: edge.validFrom, validTo: edge.validTo,
      evidenceRefs: edge.evidence.map((item) => ({id: item.id, origin: item.origin})),
      metadata: {presentationState: edge.presentationState, suppressed: edge.suppressed}}));
  }

  trace(id, {nodeId, direction, depth=1, edgeTypes=null}={}) {
    const session = this.sessions.get(String(id)); if(!session) return this.get(id);
    session.traversal = traverseLineage(session.graph, nodeId, {direction, depth, edgeTypes});
    session.selectedNodeId = nodeId; return this._touch(session);
  }

  runImpact(id, options) {
    const session = this.sessions.get(String(id)); if(!session) return this.get(id);
    if(!options?.nodeId) throw new Error('Impact analysis requires a node ID.');
    return this._task(session, 'lineage.impact.compute', 'Compute lineage impact', {options});
  }

  async _impact(id, context) {
    const session = this.sessions.get(String(id)); context.progress?.(0.2, 'Traversing dependents');
    session.impact = impactAnalysis(session.graph, context.options.nodeId, context.options);
    context.resultReference?.({schema: 'cdeadmin.result-ref.v1',
      id: `lineage-impact:${id}:${session.graph.revision}`});
    context.progress?.(1, 'Impact analysis complete'); this._touch(session); return session.impact;
  }

  async curateEdge(id, {edgeId, type, note=''}) {
    const session = this.sessions.get(String(id)); if(!session) return this.get(id);
    const original = session.graph.edges.find((item) => item.id === edgeId);
    if(!original) throw new Error(`Unknown Lineage edge: ${edgeId}`);
    const evidence = validateEvidence({id: `curation-${stableDigest([edgeId, type, note])}`,
      origin: 'user_curated', confidence: 1, capturedAt: this.now(), details: {note}});
    const curated = validateLineageEdge({...original,
      id: `curated-${stableDigest([edgeId, type ?? original.type])}`,
      type: type ?? original.type, origin: 'user_curated',
      evidence: [...original.evidence, evidence], presentationState: 'confirmed'});
    session.content = createLineageContent({...session.content,
      curatedEdges: [...session.content.curatedEdges.filter((item) => item.id !== curated.id), curated]});
    session.history.push({at: this.now(), action: 'curate', edgeId, curatedEdgeId: curated.id});
    const graph = reconcileLineage({nodes: session.graph.nodes,
      candidateEdges: [...session.candidates, ...session.parsed],
      curatedEdges: session.content.curatedEdges,
      suppressedInferenceRules: session.content.suppressedInferenceRules,
      sourcePriority: this._sourcePriority(session)});
    await this._replaceGraph(session, graph);
    return this._touch(session, {dirty: true});
  }

  async suppressInference(id, {edgeId}) {
    const session = this.sessions.get(String(id)); if(!session) return this.get(id);
    const edge = session.graph.edges.find((item) => item.id === edgeId);
    if(!edge) throw new Error(`Unknown Lineage edge: ${edgeId}`);
    if(!edge.evidence.every((item) => ['inferred', 'parsed_query'].includes(item.origin))) {
      throw new Error('Only inference-only lineage may be suppressed.');
    }
    const key = lineageSuppressionKey(edge);
    session.content = createLineageContent({...session.content,
      suppressedInferenceRules: [...session.content.suppressedInferenceRules, key]});
    const graph = reconcileLineage({nodes: session.graph.nodes,
      candidateEdges: [...session.candidates, ...session.parsed],
      curatedEdges: session.content.curatedEdges,
      suppressedInferenceRules: session.content.suppressedInferenceRules,
      sourcePriority: this._sourcePriority(session)});
    await this._replaceGraph(session, graph);
    return this._touch(session, {dirty: true});
  }

  async snapshot(id, {snapshotId}={}) {
    const session = this.sessions.get(String(id)); if(!session) return this.get(id);
    snapshotId ??= `snapshot-${stableDigest([id, this.now(), session.graph.revision])}`;
    const snapshot = createSnapshot(session.graph, {id: snapshotId,
      capturedAt: this.now(), scopeRefs: session.content.scopeRefs});
    session.snapshots.set(snapshot.id, snapshot);
    const ref = {schema: 'cdeadmin.external-ref.v1', id: `lineage-snapshot:${snapshot.id}`};
    session.content = createLineageContent({...session.content,
      snapshotRefs: [...session.content.snapshotRefs, ref]});
    session.history.push({at: this.now(), action: 'snapshot', snapshotId: snapshot.id});
    await this.events.publish('lineage.snapshot.created', {sessionId: id,
      snapshotId: snapshot.id, graphRevision: snapshot.graphRevision},
    {origin: 'cdeadmin.lineage'});
    return this._touch(session, {dirty: true});
  }

  compareSnapshots(id, {leftId, rightId}) {
    const session = this.sessions.get(String(id)); if(!session) return this.get(id);
    const left = session.snapshots.get(leftId); const right = session.snapshots.get(rightId);
    if(!left || !right) throw new Error('Both Lineage snapshots must exist.');
    session.snapshotDiff = compareLineageSnapshots(left, right); return this._touch(session);
  }

  importOpenLineage(id, {events}) {
    const session = this.sessions.get(String(id)); if(!session) return this.get(id);
    return this._task(session, 'lineage.import', 'Import OpenLineage events', {events});
  }

  async _import(id, context) {
    const session = this.sessions.get(String(id)); context.progress?.(0.1, 'Validating OpenLineage');
    const imported = importOpenLineage(context.events);
    session.imports.push(...imported.imports); session.candidates.push(...imported.edges);
    const nodes = new Map([...session.graph.nodes, ...imported.nodes].map((item) => [item.id, item]));
    const graph = reconcileLineage({nodes: [...nodes.values()],
      candidateEdges: [...session.candidates, ...session.parsed],
      curatedEdges: session.content.curatedEdges,
      suppressedInferenceRules: session.content.suppressedInferenceRules,
      sourcePriority: this._sourcePriority(session)});
    await this._replaceGraph(session, graph);
    context.progress?.(1, 'OpenLineage imported');
    this._touch(session, {dirty: true}); return imported;
  }

  exportOpenLineage(id) {
    const session = this.sessions.get(String(id)); if(!session) return this.get(id);
    return exportOpenLineageBundle(session.imports);
  }

  openDDN(id) {
    const session = this.sessions.get(String(id)); if(!session) return this.get(id);
    return immutable({schema: 'cdeadmin.lineage-ddn-projection.v1', authoritative: false,
      source: {moduleId: 'cdeadmin.lineage', sessionId: id,
        graphRevision: session.graph.revision},
      nodes: session.graph.nodes.map((node) => ({id: node.id, label: node.name,
        kind: node.kind, ref: node.reference})),
      edges: session.graph.edges.map((edge) => ({id: edge.id, from: edge.from,
        to: edge.to, type: edge.type, origin: edge.origin}))});
  }

  async save(id, {projectId, assetId, name, path, expectedVersion}) {
    const session = this.sessions.get(String(id)); if(!session) return this.get(id);
    const saved = await this.projectAssets.saveAsset(projectId, assetId, lineageAssetRequest({
      projectId, assetId, name, path, expectedVersion, content: session.content}));
    session.dirty = false; session.history.push({at: this.now(), action: 'save',
      assetId, version: saved.version}); this._touch(session); return saved;
  }

  reportError(id, error, code='internal_error') {
    const session = this.sessions.get(String(id)); if(!session) return;
    const states = {permission_denied: 'permission_denied', provider_unavailable: 'disconnected',
      capability_missing: 'partial', configuration_invalid: 'validation_error',
      external_format_invalid: 'validation_error', stale_result: 'stale'};
    if(code === 'task_cancelled') session.state = this._settledState(session);
    else session.state = states[code] ?? 'runtime_failure';
    session.error = error.message;
    session.activeTaskId = null; const diagnostic = {code, message: error.message,
      sessionId: id, at: this.now()}; session.problems.push(diagnostic);
    this.diagnostics.report({id: `lineage.${code}`, origin: 'cdeadmin.lineage',
      code, severity: 'error', message: error.message,
      suggestedCommandId: 'lineage.graph.refresh', references: [{sessionId: id}]});
    this._touch(session);
  }
}
