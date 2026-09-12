/////////////////////////////////////////////////////////////
// Replication topology discovery, control and failover authority.
/////////////////////////////////////////////////////////////

import {diagnosticsService, platformEventService, stablePlatformId} from '../../platform/PlatformRegistry';
import {immutable, noRawSecrets, plainObject, platformValue} from '../../platform/serviceUtils';
import {
  REPLICATION_MODULE_ID, createReplicationContent,
  replicationAssetRequest, replicationReferenceKey, validateFailoverPlan,
  validateLagSample, validateReplicationPosition, validateReplicationTopology,
  validateVisualLayout,
} from './contracts';
import {
  applyLag, applyPosition, expectedTopologyResult, replicationPlanIdentity,
  validateFailoverReview, validateReplicationDefinition,
} from './ReplicationEngine';

export const REPLICATION_SERVICE_ID = 'replication.runtime';
export const REPLICATION_TASKS = Object.freeze([
  'replication.discovery', 'replication.failover.validation', 'replication.failover.execution',
]);
export const REPLICATION_EVENTS = Object.freeze([
  'replication.role.changed', 'replication.link.degraded',
  'replication.lag.threshold', 'replication.failover.completed',
]);

const SUPPORT_STATES = Object.freeze([
  'supported_native', 'supported_via_cdeadmin', 'supported_via_external_adapter',
  'read_only', 'partial', 'unsupported', 'unknown',
]);
const ADAPTER_METHODS = Object.freeze([
  'getCapabilities', 'discoverTopology', 'readReplicationPosition',
  'calculateOrReadLag', 'validateFailover', 'prepareReplicationCommand',
  'executePreparedCommand',
]);
const SEARCHABLE_COMMANDS = Object.freeze([
  ['replication.topology.refresh', 'Refresh Replication Topology'],
  ['replication.link.pause', 'Pause Replication Link'],
  ['replication.link.resume', 'Resume Replication Link'],
  ['replication.failover.plan', 'Create Failover Plan'],
  ['replication.failover.validate', 'Validate Failover Plan'],
  ['replication.failover.arm', 'Arm Failover Plan'],
  ['replication.failover.execute', 'Execute Failover Plan'],
  ['replication.snapshot.create', 'Create Replication Snapshot'],
]);

function actor(user={}) { return String(user.id ?? user.username ?? user.email ?? 'unknown'); }
function providerId(reference) {
  return reference?.provider ?? reference?.providerId ?? reference?.provider_id ?? null;
}
function requireState(session, allowed, operation) {
  if(!allowed.includes(session.state)) throw new Error(
    `state_blocked: ${operation} is unavailable while replication is ${session.state}.`
  );
}

function stringList(input, name, provider) {
  if(!Array.isArray(input[name])) throw new TypeError(`${provider} result requires ${name}[].`);
  return [...new Set(input[name].map((item) => platformValue(item, `${provider} ${name}`)))].sort();
}

export function validateReplicationProviderResult(input, provider, operation,
  {allowEmpty=false}={}) {
  plainObject(input, `${provider} ${operation} result`);
  noRawSecrets(input, `${provider} ${operation} result`);
  const supportState = platformValue(input.supportState, 'Replication support state');
  if(!SUPPORT_STATES.includes(supportState)) throw new TypeError(
    `${provider} returned an invalid replication support state.`
  );
  if(!Array.isArray(input.warnings)) throw new TypeError(`${provider} result requires warnings[].`);
  const result = immutable({supportState,
    providerVersion: platformValue(input.providerVersion, `${provider} provider version`),
    evidence: immutable({...plainObject(input.evidence, `${provider} evidence`)}),
    warnings: input.warnings.map(String),
    nativeDetails: immutable({...plainObject(input.nativeDetails, `${provider} native details`)}),
    readCapabilities: stringList(input, 'readCapabilities', provider),
    writeCapabilities: stringList(input, 'writeCapabilities', provider),
    discoveryCapabilities: stringList(input, 'discoveryCapabilities', provider),
    nativeMechanisms: stringList(input, 'nativeMechanisms', provider),
    versionConstraints: stringList(input, 'versionConstraints', provider),
    limitations: stringList(input, 'limitations', provider),
    runtimeEvidence: input.runtimeEvidence === undefined ? null : immutable({
      ...plainObject(input.runtimeEvidence, `${provider} runtime evidence`)}), value: input.value});
  if(supportState.startsWith('supported') && !allowEmpty && input.value === undefined) {
    throw new TypeError(`${provider} returned empty success for ${operation}.`);
  }
  return result;
}

function requireSupported(result, operation) {
  if(!result.supportState.startsWith('supported')) throw new Error(
    `capability_missing: ${operation} is ${result.supportState}. ${result.limitations.join(' ')}`
  );
  return result;
}

export class ReplicationAdapterRegistry {
  constructor() { this.adapters = new Map(); }
  register(id, adapter) {
    id = stablePlatformId(id, 'Replication provider ID');
    ADAPTER_METHODS.forEach((method) => {
      if(typeof adapter?.[method] !== 'function') throw new TypeError(
        `Replication adapter ${id} requires ${method}().`
      );
    });
    if(this.adapters.has(id)) throw new Error(`Replication adapter already registered: ${id}`);
    this.adapters.set(id, adapter); return () => this.adapters.delete(id);
  }
  get(id) { return id ? this.adapters.get(stablePlatformId(id, 'Replication provider ID')) ?? null : null; }
  list() { return [...this.adapters.keys()].sort(); }
}

function view(session) {
  return immutable({schema: 'cdeadmin.replication-session.v1', id: session.id,
    content: session.content, state: session.state, dirty: session.dirty,
    selectedTopologyId: session.selectedTopologyId, selectedParticipantId: session.selectedParticipantId,
    selectedLinkId: session.selectedLinkId, selection: {...session.selection},
    liveTopologies: [...session.liveTopologies.values()], providerStatuses: [...session.providerStatuses.values()],
    failoverReviews: [...session.failoverReviews.values()], armedPlan: session.armedPlan,
    activeTaskId: session.activeTaskId, problems: [...session.problems], history: [...session.history],
    error: session.error, createdAt: session.createdAt, updatedAt: session.updatedAt});
}

function changedEvents(previous, current, now) {
  if(!previous) return [];
  const events = [];
  current.participants.forEach((participant) => {
    const before = previous.participants.find((item) => item.id === participant.id);
    if(before && (before.normalizedRole !== participant.normalizedRole ||
        before.nativeRole !== participant.nativeRole)) events.push({id: `role:${participant.id}:${now}`,
      topologyId: current.id, participantId: participant.id, type: 'role_changed', occurredAt: now,
      cause: 'provider rediscovery', evidence: {beforeNormalized: before.normalizedRole,
        afterNormalized: participant.normalizedRole, beforeNative: before.nativeRole,
        afterNative: participant.nativeRole}, nativeDetails: {}});
  });
  current.links.forEach((link) => {
    const before = previous.links.find((item) => item.id === link.id);
    if(before && before.health.overall !== link.health.overall &&
        ['degraded', 'unhealthy'].includes(link.health.overall)) events.push({
      id: `link:${link.id}:${now}`, topologyId: current.id, linkId: link.id,
      type: 'link_degraded', occurredAt: now, cause: 'provider rediscovery',
      evidence: {before: before.health.overall, after: link.health.overall}, nativeDetails: {}});
  });
  return events;
}

export class ReplicationService {
  constructor({tasks, relationships, search, projectAssets,
    adapters=new ReplicationAdapterRegistry(), events=platformEventService,
    diagnostics=diagnosticsService, now=() => new Date().toISOString()}={}) {
    if(!tasks || !relationships || !search) throw new TypeError(
      'Replication service requires Task, Relationship and Search services.'
    );
    this.tasks = tasks; this.relationships = relationships; this.search = search;
    this.projectAssets = projectAssets; this.adapters = adapters; this.events = events;
    this.diagnostics = diagnostics; this.now = now; this.sessions = new Map();
    this.listeners = new Set(); this.sequence = 0;
    this.disposers = [
      tasks.register('replication.discovery', (request, context) => this._discover(request, context)),
      tasks.register('replication.failover.validation', (request, context) =>
        this._validateFailoverTask(request, context)),
      tasks.register('replication.failover.execution', (request, context) =>
        this._executeFailover(request, context)),
      search.register({id: 'replication.search', priority: 37,
        types: ['replication.asset', 'replication.resource', 'replication.command',
          'replication.plan', 'replication.event'], search: async (query, {context={}}={}) => {
          if(!context.permissions?.includes('replication.view')) return [];
          const needle = query.toLowerCase(); const results = SEARCHABLE_COMMANDS
            .filter(([id, label]) => `${id} ${label}`.toLowerCase().includes(needle))
            .map(([id, label]) => ({id, type: 'replication.command', label,
              context: 'Replication command', commandId: id}));
          for(const session of this.sessions.values()) {
            if(`${session.id} ${session.content.name}`.toLowerCase().includes(needle)) results.push({
              id: session.id, type: 'replication.asset', label: session.content.name || session.id,
              context: 'Project replication asset'});
            session.content.savedTopologies.forEach((topology) => {
              [topology.scopeRef, ...topology.participants.map((item) => item.resourceRef)]
                .forEach((reference) => {
                  if(replicationReferenceKey(reference).toLowerCase().includes(needle)) results.push({
                    id: replicationReferenceKey(reference), type: 'replication.resource',
                    label: reference.canonical ?? reference.id, context: `${topology.name} · live resource`,
                    reference});
                });
            });
            session.content.failoverPlans.forEach((plan) => {
              if(`${plan.id} ${plan.name}`.toLowerCase().includes(needle)) results.push({id: plan.id,
                type: 'replication.plan', label: plan.name, context: `${plan.topologyId} · failover plan`});
            });
            [...session.liveTopologies.values()].flatMap((item) => item.events).forEach((event) => {
              if(`${event.id} ${event.type} ${event.cause}`.toLowerCase().includes(needle)) results.push({
                id: event.id, type: 'replication.event', label: event.type,
                context: `${event.topologyId} · ${event.occurredAt}`});
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
  _emit(session) { this.listeners.forEach((listener) => listener(view(session))); }
  _touch(session, {dirty=session.dirty}={}) {
    session.updatedAt = this.now(); session.dirty = dirty; this._emit(session); return view(session);
  }
  _session(id) {
    const session = this.sessions.get(String(id));
    if(!session) throw new Error(`Unknown replication session: ${id}`); return session;
  }
  _topology(session, topologyId=session.selectedTopologyId) {
    const topology = session.content.savedTopologies.find((item) => item.id === topologyId);
    if(!topology) throw new Error(`Unknown saved replication topology: ${topologyId}`); return topology;
  }
  _plan(session, planId) {
    const plan = session.content.failoverPlans.find((item) => item.id === planId);
    if(!plan) throw new Error(`Unknown failover plan: ${planId}`); return plan;
  }

  create(input={}) {
    plainObject(input, 'Replication session'); const id = input.id ?? `replication-${++this.sequence}`;
    if(this.sessions.has(id)) throw new Error(`Replication session already exists: ${id}`);
    const content = createReplicationContent(input.content ?? input);
    const validation = validateReplicationDefinition(content); const time = this.now();
    const session = {id, content, state: !content.savedTopologies.length ? 'empty' :
      validation.valid ? 'ready' : 'validation_error', dirty: false,
    selectedTopologyId: content.savedTopologies[0]?.id ?? null,
    selectedParticipantId: null, selectedLinkId: null,
    selection: {surface: 'topology_explorer'}, liveTopologies: new Map(),
    providerStatuses: new Map(), failoverReviews: new Map(), armedPlan: null,
    activeTaskId: null, problems: [...validation.details, ...validation.warnings], history: [],
    error: '', createdAt: time, updatedAt: time};
    this.sessions.set(id, session); this._mirrorRelationships(session); this._emit(session);
    return view(session);
  }
  get(id) { return view(this._session(id)); }
  list() { return [...this.sessions.values()].map(view); }
  select(id, selection={}) {
    const session = this._session(id);
    for(const key of ['surface', 'topologyId', 'participantId', 'linkId']) {
      if(selection[key] !== undefined) {
        if(key === 'topologyId') session.selectedTopologyId = selection[key];
        else if(key === 'participantId') session.selectedParticipantId = selection[key];
        else if(key === 'linkId') session.selectedLinkId = selection[key];
        else session.selection.surface = selection[key];
      }
    }
    return this._touch(session);
  }
  replaceDefinition(id, input, context={}) {
    const session = this._session(id);
    requireState(session, ['empty', 'ready', 'stale', 'partial', 'disconnected',
      'validation_error', 'runtime_failure'], 'definition editing');
    session.content = createReplicationContent(input);
    const validation = validateReplicationDefinition(session.content);
    session.state = validation.valid ? 'ready' : session.content.savedTopologies.length ?
      'validation_error' : 'empty'; session.problems = [...validation.details, ...validation.warnings];
    session.error = ''; session.selectedTopologyId ??= session.content.savedTopologies[0]?.id ?? null;
    session.history.push({at: this.now(), actor: actor(context.currentUser),
      action: 'replication.definition.update'}); this._mirrorRelationships(session);
    return this._touch(session, {dirty: true});
  }

  _adapter(reference, operation) {
    const id = providerId(reference); const adapter = this.adapters.get(id);
    if(!adapter) throw new Error(`capability_missing: ${operation}: no replication adapter for ${id ?? 'unknown'}.`);
    return {adapter, id};
  }
  async _call(reference, method, input, context={}, options={}) {
    const {adapter, id} = this._adapter(reference, method);
    const raw = await adapter[method](input, context);
    return requireSupported(validateReplicationProviderResult(raw, id, method, options), method);
  }

  refresh(id, topologyId, options={}, context={}) {
    const session = this._session(id); const topology = this._topology(session, topologyId);
    requireState(session, ['ready', 'partial', 'read_only', 'runtime_failure'],
      'topology discovery');
    const task = this.tasks.submit({type: 'replication.discovery', label: `Discover ${topology.name}`,
      sessionId: id, topologyId: topology.id, retry: options.retry ?? {maximum: 0},
      resourceRefs: [topology.scopeRef], cancelable: true, resumable: false,
      audit: {actor: actor(context.currentUser), command: 'replication.topology.refresh',
        environment: context.environment ?? 'unknown'}}, {...context, owner: actor(context.currentUser)});
    session.activeTaskId = task.id; session.state = 'background_task_active';
    session.history.push({at: this.now(), actor: actor(context.currentUser),
      action: 'replication.topology.refresh', taskId: task.id}); return this._touch(session);
  }

  async _readTopology(saved, context) {
    const {adapter, id} = this._adapter(saved.scopeRef, 'discoverTopology');
    const capability = validateReplicationProviderResult(await adapter.getCapabilities(
      saved.scopeRef, context), id, 'getCapabilities', {allowEmpty: true});
    if(!capability.supportState.startsWith('supported')) return {status: capability, topology: null};
    let topology = validateReplicationTopology((await this._call(saved.scopeRef, 'discoverTopology',
      {scopeRef: saved.scopeRef, topologyId: saved.id}, context)).value);
    if(topology.providerId !== id || providerId(topology.scopeRef) !== id) throw new Error(
      'task_failed: discovered topology provider identity does not match its scope.'
    );
    const positions = await Promise.all(topology.participants.map(async (participant) => ({
      id: participant.id, value: validateReplicationPosition((await this._call(saved.scopeRef,
        'readReplicationPosition', {topology, participant}, context)).value)})));
    positions.forEach((item) => { topology = applyPosition(topology, item.id, item.value); });
    const samples = await Promise.all(topology.links.map(async (link) => ({id: link.id,
      value: validateLagSample((await this._call(saved.scopeRef, 'calculateOrReadLag',
        {topology, link}, context)).value)})));
    samples.forEach((item) => { topology = applyLag(topology, item.id, item.value); });
    return {status: capability, topology};
  }

  async _discover(request, context) {
    const session = this._session(request.sessionId); const saved = this._topology(session, request.topologyId);
    context.phase('discovering'); context.progress(0.05, 'Discovering provider topology.');
    try {
      const previous = session.liveTopologies.get(saved.id) ?? null;
      const {status, topology} = await this._readTopology(saved, {...context, signal: context.signal});
      session.providerStatuses.set(saved.providerId, status);
      if(!topology) {
        session.activeTaskId = null; session.state = status.supportState === 'read_only' ?
          'read_only' : 'partial'; session.problems = [...status.warnings, ...status.limitations];
        this._touch(session); return status;
      }
      const additions = changedEvents(previous, topology, this.now());
      const current = validateReplicationTopology({...topology,
        events: [...topology.events, ...additions]});
      session.liveTopologies.set(saved.id, current); session.activeTaskId = null;
      session.state = 'ready'; session.error = ''; session.problems = [...status.warnings,
        ...status.limitations];
      additions.forEach((item) => this.events.publish(item.type === 'role_changed' ?
        'replication.role.changed' : 'replication.link.degraded', item,
      {origin: REPLICATION_MODULE_ID}));
      this._emitLagAlerts(session, current); context.progress(1, 'Topology discovery completed.');
      this._touch(session); return current;
    } catch(error) { this._failure(session, error); throw error; }
  }

  _emitLagAlerts(session, topology) {
    session.content.alertPolicies.filter((policy) => policy.enabled &&
      policy.topologyId === topology.id).forEach((policy) => {
      const samples = topology.lagSamples.filter((item) => !policy.linkId ||
        item.linkId === policy.linkId).filter((item) => item.unit === policy.unit);
      const latest = samples.at(-1);
      if(latest && latest.value > policy.maximum) this.events.publish('replication.lag.threshold',
        {sessionId: session.id, topologyId: topology.id, alertId: policy.id,
          linkId: latest.linkId, value: latest.value, maximum: policy.maximum, unit: latest.unit},
        {origin: REPLICATION_MODULE_ID});
    });
  }

  updateLayout(id, topologyId, participantId, position, context={}) {
    const session = this._session(id); this._topology(session, topologyId);
    requireState(session, ['ready', 'stale', 'partial', 'disconnected',
      'validation_error', 'runtime_failure'], 'visual layout editing');
    const existing = session.content.visualLayouts.find((item) => item.topologyId === topologyId);
    const layout = validateVisualLayout({id: existing?.id ?? `layout-${topologyId}`, topologyId,
      positions: {...(existing?.positions ?? {}), [participantId]: position},
      nativeDetails: existing?.nativeDetails ?? {}});
    session.content = createReplicationContent({...session.content,
      visualLayouts: [...session.content.visualLayouts.filter((item) => item.id !== layout.id), layout]});
    session.history.push({at: this.now(), actor: actor(context.currentUser),
      action: 'replication.layout.move', topologyId, participantId});
    return this._touch(session, {dirty: true});
  }

  async controlLink(id, topologyId, linkId, action, context={}) {
    if(!['pause', 'resume'].includes(action)) throw new TypeError('Replication link action is invalid.');
    const session = this._session(id); const saved = this._topology(session, topologyId);
    requireState(session, ['ready'], 'replication link control');
    const topology = session.liveTopologies.get(topologyId);
    const link = topology?.links.find((item) => item.id === linkId);
    if(!link) throw new Error(`configuration_invalid: unknown live replication link ${linkId}.`);
    const prepared = await this._call(saved.scopeRef, 'prepareReplicationCommand',
      {action: `link.${action}`, topology, link, environment: context.environment}, context);
    const executed = await this._call(saved.scopeRef, 'executePreparedCommand',
      {prepared: prepared.value, action: `link.${action}`}, context);
    const nativeState = platformValue(executed.value?.nativeState,
      'Controlled replication link native state');
    const updated = validateReplicationTopology({...topology, links: topology.links.map(
      (item) => item.id === linkId ? {...item, nativeState} : item
    )});
    session.liveTopologies.set(topologyId, updated); session.history.push({at: this.now(),
      actor: actor(context.currentUser), action: `replication.link.${action}`,
      topologyId, linkId, evidence: executed.evidence}); return this._touch(session);
  }

  upsertPlan(id, input, context={}) {
    const session = this._session(id); const plan = validateFailoverPlan(input);
    requireState(session, ['ready', 'stale', 'partial', 'disconnected',
      'validation_error', 'runtime_failure'], 'failover plan editing');
    this._topology(session, plan.topologyId);
    session.content = createReplicationContent({...session.content,
      failoverPlans: [...session.content.failoverPlans.filter((item) => item.id !== plan.id), plan]});
    session.failoverReviews.delete(plan.id); if(session.armedPlan?.planId === plan.id) session.armedPlan = null;
    session.history.push({at: this.now(), actor: actor(context.currentUser),
      action: 'replication.failover.plan', planId: plan.id});
    this._mirrorRelationships(session); return this._touch(session, {dirty: true});
  }

  async validateFailover(id, planId, context={}) {
    const session = this._session(id); const plan = this._plan(session, planId);
    requireState(session, ['ready'], 'failover validation');
    const saved = this._topology(session, plan.topologyId);
    const topology = session.liveTopologies.get(plan.topologyId);
    if(!topology) throw new Error('configuration_invalid: refresh topology before failover validation.');
    const result = await this._call(saved.scopeRef, 'validateFailover', {plan, topology}, context);
    const review = validateFailoverReview(plan, result.value);
    session.failoverReviews.set(plan.id, review); session.history.push({at: this.now(),
      actor: actor(context.currentUser), action: 'replication.failover.validate',
      planId, valid: review.valid, evidence: result.evidence}); return this._touch(session);
  }

  armFailover(id, planId, input, context={}) {
    const session = this._session(id); const plan = this._plan(session, planId);
    requireState(session, ['ready'], 'failover arming');
    const review = session.failoverReviews.get(planId);
    if(!review?.valid) throw new Error('configuration_invalid: validate failover before arming.');
    plainObject(input, 'Failover arm request'); noRawSecrets(input, 'Failover arm request');
    const confirmationRef = platformValue(input.confirmationRef, 'Failover confirmation reference');
    const environment = platformValue(input.environment, 'Failover target environment');
    const connection = platformValue(input.connection, 'Failover target connection');
    session.armedPlan = immutable({schema: 'cdeadmin.replication-armed-plan.v1', planId,
      planIdentity: replicationPlanIdentity(plan), confirmationRef, environment, connection,
      actor: actor(context.currentUser), armedAt: this.now()});
    session.history.push({at: this.now(), actor: actor(context.currentUser),
      action: 'replication.failover.arm', planId, confirmationRef, environment, connection});
    return this._touch(session);
  }

  executeFailover(id, planId, options={}, context={}) {
    const session = this._session(id); const plan = this._plan(session, planId);
    requireState(session, ['ready'], 'failover execution');
    if(!session.armedPlan || session.armedPlan.planId !== planId ||
        session.armedPlan.planIdentity !== replicationPlanIdentity(plan)) throw new Error(
      'configuration_invalid: the exact failover plan revision is not armed.'
    );
    const saved = this._topology(session, plan.topologyId);
    const common = {sessionId: id, planId, retry: options.retry ?? {maximum: 0},
      resourceRefs: [saved.scopeRef], cancelable: true, resumable: false,
      audit: {actor: actor(context.currentUser), command: 'replication.failover.execute',
        confirmationRef: session.armedPlan.confirmationRef,
        environment: session.armedPlan.environment, connection: session.armedPlan.connection}};
    const validation = this.tasks.submit({type: 'replication.failover.validation',
      label: `Validate ${plan.name}`, ...common}, {...context, owner: actor(context.currentUser)});
    const execution = this.tasks.submit({type: 'replication.failover.execution',
      label: `Execute ${plan.name}`, ...common, dependencies: [validation.id]},
    {...context, owner: actor(context.currentUser)});
    session.activeTaskId = execution.id; session.state = 'background_task_active';
    session.history.push({at: this.now(), actor: actor(context.currentUser),
      action: 'replication.failover.execute', planId, taskId: execution.id});
    return this._touch(session);
  }

  async _validateFailoverTask(request, context) {
    const session = this._session(request.sessionId); const plan = this._plan(session, request.planId);
    const saved = this._topology(session, plan.topologyId);
    const topology = session.liveTopologies.get(plan.topologyId);
    try {
      context.phase('validating_failover');
      const result = await this._call(saved.scopeRef, 'validateFailover', {plan, topology},
        {...context, signal: context.signal});
      const review = validateFailoverReview(plan, result.value);
      session.failoverReviews.set(plan.id, review);
      if(!review.valid) throw new Error(`task_failed: ${review.details.join(' ')}`);
      context.progress(1, 'Failover preconditions passed.'); this._touch(session); return review;
    } catch(error) { this._failure(session, error); throw error; }
  }

  async _executeFailover(request, context) {
    const session = this._session(request.sessionId); const plan = this._plan(session, request.planId);
    const saved = this._topology(session, plan.topologyId);
    const before = session.liveTopologies.get(plan.topologyId);
    try {
      context.phase('executing_failover'); context.progress(0.1, 'Preparing provider command.');
      const prepared = await this._call(saved.scopeRef, 'prepareReplicationCommand',
        {action: 'failover', plan, topology: before,
          arm: session.armedPlan}, {...context, signal: context.signal});
      const executed = await this._call(saved.scopeRef, 'executePreparedCommand',
        {action: 'failover', prepared: prepared.value}, {...context, signal: context.signal});
      context.progress(0.65, 'Re-discovering topology after failover.');
      const discovered = await this._readTopology(saved, {...context, signal: context.signal});
      if(!discovered.topology) throw new Error('task_failed: topology unavailable after failover.');
      const verification = expectedTopologyResult(plan, discovered.topology);
      if(!verification.valid) throw new Error(`task_failed: ${verification.details.join(' ')}`);
      const additions = changedEvents(before, discovered.topology, this.now());
      const topology = validateReplicationTopology({...discovered.topology,
        events: [...discovered.topology.events, ...additions]});
      session.liveTopologies.set(plan.topologyId, topology); session.armedPlan = null;
      session.activeTaskId = null; session.state = 'ready'; session.error = '';
      additions.forEach((item) => this.events.publish(item.type === 'role_changed' ?
        'replication.role.changed' : 'replication.link.degraded', item,
      {origin: REPLICATION_MODULE_ID}));
      this.events.publish('replication.failover.completed', {sessionId: session.id,
        planId: plan.id, topologyId: plan.topologyId, evidence: executed.evidence,
        verification}, {origin: REPLICATION_MODULE_ID});
      context.progress(1, 'Failover completed and topology verified.'); this._touch(session);
      return immutable({planId: plan.id, providerResult: executed.value, topology, verification});
    } catch(error) { this._failure(session, error); throw error; }
  }

  createSnapshot(id, topologyId, input={}, context={}) {
    const session = this._session(id); const live = session.liveTopologies.get(topologyId);
    requireState(session, ['ready'], 'replication snapshot creation');
    if(!live) throw new Error('configuration_invalid: refresh topology before creating a snapshot.');
    const snapshotId = platformValue(input.snapshotId ?? `snapshot-${++this.sequence}`,
      'Replication snapshot ID');
    const capturedAt = input.capturedAt ?? this.now();
    const snapshot = validateReplicationTopology({...live, id: snapshotId,
      name: input.name ?? `${live.name} snapshot`, revision: `snapshot:${capturedAt}`});
    const savedTopologies = [...session.content.savedTopologies.filter((item) => item.id !== snapshotId),
      snapshot];
    const snapshotRefs = input.reference ? [...session.content.snapshotRefs, {id: snapshotId,
      topologyId, capturedAt, reference: input.reference, nativeDetails: {}}] :
      session.content.snapshotRefs;
    session.content = createReplicationContent({...session.content, savedTopologies, snapshotRefs});
    session.history.push({at: this.now(), actor: actor(context.currentUser),
      action: 'replication.snapshot.create', topologyId, snapshotId});
    this._mirrorRelationships(session); return this._touch(session, {dirty: true});
  }

  async save(id, request, context={}) {
    const session = this._session(id);
    if(!this.projectAssets) throw new Error('Project Asset service is unavailable.');
    const payload = replicationAssetRequest({...request, content: session.content});
    const saved = request.assetId ? await this.projectAssets.update(request.projectId,
      request.assetId, payload) : await this.projectAssets.create(request.projectId, payload);
    session.dirty = false; session.history.push({at: this.now(), actor: actor(context.currentUser),
      action: 'asset.saved', assetId: saved.asset_id, version: saved.version});
    this._touch(session); return saved;
  }

  _failure(session, error) {
    session.activeTaskId = null; session.state = error.name === 'AbortError' ? 'ready' :
      'runtime_failure'; session.error = error.name === 'AbortError' ? '' : error.message;
    session.problems = [...new Set([...session.problems,
      `${error.name === 'AbortError' ? 'task_cancelled' : 'task_failed'}: ${error.message}`])];
    this._touch(session);
  }
  _clearRelationships(session) {
    const prefix = `replication:${session.id}`; const graph = this.relationships.snapshot();
    graph.edges.filter((edge) => edge.id.startsWith(prefix)).forEach(
      (edge) => this.relationships.removeEdge(edge.id));
    graph.nodes.filter((node) => node.id.startsWith(prefix)).forEach(
      (node) => this.relationships.removeNode(node.id));
  }
  _mirrorRelationships(session) {
    this._clearRelationships(session); const asset = `replication:${session.id}`;
    this.relationships.upsertNode({id: asset, kind: 'replication.asset',
      label: session.content.name || session.id});
    session.content.savedTopologies.forEach((topology) => {
      const topId = `${asset}:topology:${topology.id}`;
      this.relationships.upsertNode({id: topId, kind: 'replication.topology',
        label: topology.name, reference: topology.scopeRef});
      this.relationships.upsertEdge({id: `${asset}:topology:${topology.id}`,
        from: asset, to: topId, type: 'models', origin: REPLICATION_MODULE_ID,
        metadata: {topologyId: topology.id, origin: 'authored_definition'}});
      topology.participants.forEach((participant) => {
        const node = `${topId}:participant:${participant.id}`;
        this.relationships.upsertNode({id: node, kind: 'replication.participant',
          label: participant.name, reference: participant.resourceRef});
        this.relationships.upsertEdge({id: `${asset}:participant:${topology.id}:${participant.id}`,
          from: topId, to: node, type: 'contains', origin: REPLICATION_MODULE_ID,
          metadata: {topologyId: topology.id, role: participant.nativeRole}});
      });
      topology.links.forEach((link) => {
        this.relationships.upsertEdge({id: `${asset}:link:${topology.id}:${link.id}`,
          from: `${topId}:participant:${link.sourceParticipantId}`,
          to: `${topId}:participant:${link.targetParticipantId}`,
          type: 'replicates_to', origin: REPLICATION_MODULE_ID,
          metadata: {topologyId: topology.id, linkId: link.id,
            mechanism: link.mechanism, origin: 'provider_declared'}});
      });
    });
  }
}

export function unsupportedReplicationProviderResult(provider, operation, limitation) {
  return immutable({supportState: 'unsupported', providerVersion: String(provider),
    evidence: {}, warnings: [String(limitation)], nativeDetails: {}, readCapabilities: [],
    writeCapabilities: [], discoveryCapabilities: [], nativeMechanisms: [], versionConstraints: [],
    limitations: [String(limitation)], runtimeEvidence: null, operation, value: undefined});
}
