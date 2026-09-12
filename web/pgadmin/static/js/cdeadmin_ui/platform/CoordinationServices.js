/////////////////////////////////////////////////////////////
// CDEadmin relationship, task and federated-search authorities.
/////////////////////////////////////////////////////////////

import {stablePlatformId, PlatformRegistryError} from './PlatformRegistry';
import {abortError, immutable, noRawSecrets, plainObject, platformValue} from './serviceUtils';

export class RelationshipGraphService {
  constructor() { this.nodes = new Map(); this.edges = new Map(); }

  upsertNode(input) {
    const id = platformValue(input?.id, 'Relationship node ID');
    const node = immutable({schema: 'cdeadmin.relationship-node.v1', id,
      kind: stablePlatformId(input.kind, 'Relationship node kind'),
      reference: input.reference ?? null, label: String(input.label ?? id),
      metadata: {...(input.metadata ?? {})}});
    noRawSecrets(node.metadata, 'relationship metadata');
    this.nodes.set(id, node); return node;
  }

  removeNode(id) {
    if(!this.nodes.has(id)) return false;
    [...this.edges.values()].filter((edge) => edge.from === id || edge.to === id)
      .forEach((edge) => this.edges.delete(edge.id));
    return this.nodes.delete(id);
  }

  upsertEdge(input) {
    const id = platformValue(input?.id, 'Relationship edge ID');
    const from = platformValue(input.from, 'Relationship source');
    const to = platformValue(input.to, 'Relationship target');
    if(!this.nodes.has(from) || !this.nodes.has(to)) throw new PlatformRegistryError(
      'node_not_found', `Relationship endpoints must exist: ${from} -> ${to}`, id
    );
    const relation = stablePlatformId(input.relation ?? input.type, 'Relationship kind');
    const origin = stablePlatformId(input.origin ?? 'project_declared',
      'Relationship origin');
    const metadata = {...(input.metadata ?? input.attributes ?? {})};
    const edge = immutable({schema: 'cdeadmin.relationship-edge.v1',
      id, edgeId: id, from, to, fromRef: input.fromRef ?? this.nodes.get(from)?.reference ?? null,
      toRef: input.toRef ?? this.nodes.get(to)?.reference ?? null,
      relation, type: relation, origin,
      confidence: input.confidence === undefined ? null : Number(input.confidence),
      validFrom: input.validFrom ?? null, validTo: input.validTo ?? null,
      evidenceRefs: [...(input.evidenceRefs ?? [])], attributes: metadata,
      metadata});
    noRawSecrets(edge.metadata, 'relationship metadata');
    this.edges.set(id, edge); return edge;
  }

  removeEdge(id) { return this.edges.delete(String(id)); }

  neighbors(id, {direction='both', relation}={}) {
    if(!this.nodes.has(id)) throw new PlatformRegistryError(
      'not_found', `Unknown relationship node: ${id}`, id
    );
    return [...this.edges.values()].filter((edge) =>
      (!relation || edge.relation === relation) &&
      (direction === 'out' ? edge.from === id : direction === 'in' ? edge.to === id :
        edge.from === id || edge.to === id)
    );
  }

  traverse(startId, {direction='out', maxDepth=32, relation}={}) {
    if(!this.nodes.has(startId)) throw new PlatformRegistryError(
      'not_found', `Unknown relationship node: ${startId}`, startId
    );
    const visited = new Set([startId]); const levels = [[this.nodes.get(startId)]];
    let frontier = [startId];
    for(let depth = 1; frontier.length && depth <= maxDepth; depth++) {
      const nextIds = [];
      for(const id of frontier) for(const edge of this.neighbors(id, {direction, relation})) {
        const next = direction === 'in' ? edge.from : direction === 'out' ? edge.to :
          edge.from === id ? edge.to : edge.from;
        if(!visited.has(next)) { visited.add(next); nextIds.push(next); }
      }
      if(nextIds.length) levels.push(nextIds.map((id) => this.nodes.get(id)));
      frontier = nextIds;
    }
    return immutable({start: startId, direction, levels,
      edges: [...this.edges.values()].filter((edge) =>
        visited.has(edge.from) && visited.has(edge.to))});
  }

  impact(id, options={}) { return this.traverse(id, {direction: 'in', ...options}); }
  dependencies(id, options={}) { return this.traverse(id, {direction: 'out', ...options}); }
  snapshot() { return immutable({schema: 'cdeadmin.relationship-graph.v1',
    nodes: [...this.nodes.values()], edges: [...this.edges.values()]}); }
}

export const TASK_STATES = Object.freeze({
  QUEUED: 'queued', PREPARING: 'preparing', RUNNING: 'running',
  WAITING: 'waiting', PAUSED: 'paused', SUCCEEDED: 'succeeded',
  SUCCEEDED_WITH_WARNINGS: 'succeeded_with_warnings',
  FAILED: 'failed', CANCEL_REQUESTED: 'cancel_requested',
  CANCELLED: 'cancelled', ABORTED: 'aborted',
  COMPLETED: 'succeeded',
});

export class TaskExecutionService {
  constructor({now=() => new Date().toISOString()}={}) {
    this.now = now; this.runners = new Map(); this.tasks = new Map();
    this.listeners = new Set(); this.sequence = 0;
  }

  register(type, runner) {
    type = stablePlatformId(type, 'Task type');
    if(typeof runner !== 'function') throw new TypeError('Task runner is required.');
    if(this.runners.has(type)) throw new PlatformRegistryError(
      'duplicate', `Task runner already registered: ${type}`, type
    );
    this.runners.set(type, runner); return () => this.runners.delete(type);
  }

  subscribe(listener) { this.listeners.add(listener); return () => this.listeners.delete(listener); }
  _publish(task) { this.listeners.forEach((listener) => listener(this.view(task.id))); }

  submit(input, context={}) {
    plainObject(input, 'Task request'); noRawSecrets(input, 'task');
    const type = stablePlatformId(input.type, 'Task type');
    if(!this.runners.has(type)) throw new PlatformRegistryError(
      'task_unavailable', `No task runner for ${type}`, type
    );
    const id = input.id ?? `task-${++this.sequence}`;
    if(this.tasks.has(id)) throw new PlatformRegistryError('duplicate', `Task exists: ${id}`, id);
    const dependencies = [...(input.dependencies ?? [])];
    dependencies.forEach((dependency) => {
      if(!this.tasks.has(dependency)) throw new PlatformRegistryError(
        'dependency_not_found', `Unknown task dependency: ${dependency}`, dependency
      );
    });
    const task = {id, type, label: String(input.label ?? type), request: immutable({...input}),
      owner: context.owner ?? null, state: TASK_STATES.QUEUED, progress: null,
      phase: 'queued', message: '', result: null, resultRefs: [], error: '',
      diagnostics: [], logs: [], dependencies,
      controller: new AbortController(), pauseGate: null,
      retry: {maximum: Math.max(0, Number(input.retry?.maximum ?? 0)), attempt: 0},
      resourceRefs: [...(input.resourceRefs ?? [])], assetRefs: [...(input.assetRefs ?? [])],
      cancelable: input.cancelable !== false, resumable: input.resumable !== false,
      audit: input.audit ?? null, hasWarnings: false,
      createdAt: this.now(), startedAt: null, finishedAt: null};
    this.tasks.set(id, task); this._publish(task); task.promise = this._run(task, context);
    task.promise.catch(() => {});
    return this.view(id);
  }

  async _run(task, context) {
    try {
      task.state = TASK_STATES.PREPARING; task.phase = 'preparing'; this._publish(task);
      await Promise.all(task.dependencies.map((id) => this.tasks.get(id).promise));
      if(task.controller.signal.aborted) throw abortError('Task was cancelled.');
      for(;;) {
        task.retry.attempt++; task.state = TASK_STATES.RUNNING; task.phase = 'running';
        task.startedAt ??= this.now(); this._publish(task);
        try {
          task.result = await this.runners.get(task.type)(task.request, {
            ...context, signal: task.controller.signal,
            progress: (progress, message='') => {
              if(!Number.isFinite(progress) || progress < 0 || progress > 1) throw new TypeError(
                'Task progress must be between zero and one.'
              );
              task.progress = progress; task.message = String(message); this._publish(task);
            },
            phase: (phase, message='') => {
              task.phase = platformValue(phase, 'Task phase');
              if(message) task.message = String(message); this._publish(task);
            },
            warning: (message, details={}) => {
              task.hasWarnings = true; task.logs.push(immutable({at: this.now(),
                level: 'warning', message: String(message), details})); this._publish(task);
            },
            diagnostic: (diagnostic) => {
              task.diagnostics.push(immutable({...diagnostic})); this._publish(task);
            },
            resultReference: (reference) => {
              task.resultRefs.push(immutable({...reference})); this._publish(task);
            },
            log: (level, message, details={}) => {
              task.logs.push(immutable({at: this.now(), level: String(level),
                message: String(message), details})); this._publish(task);
            },
            waitIfPaused: async () => { if(task.pauseGate) await task.pauseGate.promise; },
          });
          task.progress = 1; task.state = task.hasWarnings ?
            TASK_STATES.SUCCEEDED_WITH_WARNINGS : TASK_STATES.SUCCEEDED;
          task.phase = task.state;
          task.finishedAt = this.now(); this._publish(task); return task.result;
        } catch(error) {
          if(task.controller.signal.aborted) throw abortError('Task was cancelled.');
          if(task.retry.attempt > task.retry.maximum) throw error;
          task.logs.push(immutable({at: this.now(), level: 'warning',
            message: `Retrying after: ${error.message}`, details: {}}));
        }
      }
    } catch(error) {
      task.state = task.controller.signal.aborted ? TASK_STATES.CANCELLED : TASK_STATES.FAILED;
      task.phase = task.state;
      task.error = error.message; task.finishedAt = this.now(); this._publish(task); throw error;
    }
  }

  view(id) {
    const task = this.tasks.get(String(id));
    if(!task) throw new PlatformRegistryError('not_found', `Unknown task: ${id}`, id);
    return immutable({schema: 'cdeadmin.task.v1', id: task.id, type: task.type,
      label: task.label, owner: task.owner, state: task.state, progress: task.progress,
      phase: task.phase, message: task.message, result: task.result,
      resultRefs: [...task.resultRefs], error: task.error,
      diagnostics: [...task.diagnostics], logs: [...task.logs],
      dependencies: [...task.dependencies], retry: {...task.retry},
      resourceRefs: [...task.resourceRefs], assetRefs: [...task.assetRefs],
      cancelable: task.cancelable, resumable: task.resumable,
      recoveryAvailable: task.retry.maximum > task.retry.attempt,
      audit: task.audit,
      createdAt: task.createdAt, startedAt: task.startedAt, finishedAt: task.finishedAt});
  }

  list({state, type}={}) {
    if(state === 'completed') state = TASK_STATES.SUCCEEDED;
    return [...this.tasks.values()].filter((task) =>
      (!state || task.state === state) && (!type || task.type === type)
    ).map((task) => this.view(task.id));
  }

  cancel(id) {
    const task = this.tasks.get(id);
    if(!task || !task.cancelable || [TASK_STATES.SUCCEEDED,
      TASK_STATES.SUCCEEDED_WITH_WARNINGS, TASK_STATES.FAILED,
      TASK_STATES.CANCELLED, TASK_STATES.ABORTED].includes(task.state)) return false;
    task.state = TASK_STATES.CANCEL_REQUESTED; task.phase = 'cancel_requested';
    this._publish(task); task.controller.abort(); task.pauseGate?.resolve(); return true;
  }

  pause(id) {
    const task = this.tasks.get(id);
    if(!task || !task.resumable || task.state !== TASK_STATES.RUNNING ||
        task.pauseGate) return false;
    let resolve; const promise = new Promise((done) => { resolve = done; });
    task.pauseGate = {promise, resolve}; task.state = TASK_STATES.PAUSED;
    this._publish(task); return true;
  }

  resume(id) {
    const task = this.tasks.get(id);
    if(!task || task.state !== TASK_STATES.PAUSED || !task.pauseGate) return false;
    task.pauseGate.resolve(); task.pauseGate = null; task.state = TASK_STATES.RUNNING;
    this._publish(task); return true;
  }

  wait(id) {
    const task = this.tasks.get(id);
    return task ? task.promise : Promise.reject(new PlatformRegistryError(
      'not_found', `Unknown task: ${id}`, id
    ));
  }
}

export class FederatedSearchService {
  constructor() { this.providers = new Map(); }

  register(input) {
    const id = stablePlatformId(input?.id, 'Search provider ID');
    if(typeof input.search !== 'function') throw new TypeError('Search provider requires search().');
    if(this.providers.has(id)) throw new PlatformRegistryError(
      'duplicate', `Search provider already registered: ${id}`, id
    );
    const value = immutable({id, types: [...new Set((input.types ?? []).map(String))],
      priority: Number(input.priority ?? 100), search: input.search});
    this.providers.set(id, value); return () => this.providers.delete(id);
  }

  async search(query, {types, limit=100, signal, context={}}={}) {
    query = String(query ?? '').trim();
    if(!query) return immutable({query, groups: [], total: 0});
    const requested = types ? new Set(types) : null;
    const providers = [...this.providers.values()].filter((provider) =>
      !requested || provider.types.some((type) => requested.has(type))
    );
    const settled = await Promise.all(providers.map(async (provider) => {
      if(signal?.aborted) throw abortError('Search was cancelled.');
      try {
        const results = await provider.search(query, {types, limit, signal, context});
        return {provider, results: (results ?? []).slice(0, limit).map((result) => {
          if(!provider.types.includes(result.type)) throw new TypeError(
            `Search provider ${provider.id} returned undeclared type ${result.type}.`
          );
          return immutable({...result, providerId: provider.id});
        }), error: ''};
      } catch(error) {
        if(error.name === 'AbortError') throw error;
        return {provider, results: [], error: error.message};
      }
    }));
    const groups = settled.sort((a, b) => a.provider.priority - b.provider.priority)
      .map(({provider, results, error}) => immutable({providerId: provider.id,
        types: provider.types, results, error}));
    return immutable({query, groups, total: groups.reduce(
      (sum, group) => sum + group.results.length, 0
    )});
  }
}
