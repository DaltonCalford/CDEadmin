/////////////////////////////////////////////////////////////
// CDC Designer orchestration, provider evidence and recovery.
/////////////////////////////////////////////////////////////

import {
  diagnosticsService, platformEventService, stablePlatformId,
} from '../../platform/PlatformRegistry';
import {immutable, noRawSecrets, plainObject, platformValue} from '../../platform/serviceUtils';
import {
  CDC_MODULE_ID, cdcAssetRequest, cdcReferenceKey, createCDCContent,
  validateCheckpoint, validateEventEnvelope, validateReplayRequest,
} from './contracts';
import {
  assertRunTransition, effectiveSchemaAction, normalizeRunResult,
  redactEventEnvelope, validateCDCDefinition, validateReplaySafety,
} from './CDCEngine';

export const CDC_SERVICE_ID = 'cdc.runtime';
export const CDC_TASKS = Object.freeze([
  'cdc.provision', 'cdc.snapshot', 'cdc.stream', 'cdc.replay',
  'cdc.schema_validation',
]);
export const CDC_EVENTS = Object.freeze([
  'cdc.run.state_changed', 'cdc.lag.threshold', 'cdc.schema.changed',
  'cdc.checkpoint.advanced', 'cdc.run.failed',
]);

const SUPPORT_STATES = Object.freeze([
  'supported_native', 'supported_via_cdeadmin', 'supported_via_external_adapter',
  'read_only', 'partial', 'unsupported', 'unknown',
]);
const REQUIRED_ADAPTER_METHODS = Object.freeze([
  'getCapabilities', 'listCaptureMechanisms', 'validateCapturePrivileges',
  'resolveStartPosition', 'validateSink', 'provision', 'snapshot', 'stream',
  'controlRun', 'readCheckpoint', 'validateSchemaChange', 'inspectEvents', 'replay',
]);
const SEARCHABLE_COMMANDS = Object.freeze([
  ['cdc.definition.create', 'Create or Update CDC Definition'],
  ['cdc.validate', 'Validate CDC Definition'],
  ['cdc.run.start', 'Start CDC Run'],
  ['cdc.run.pause', 'Pause CDC Run'],
  ['cdc.run.resume', 'Resume CDC Run'],
  ['cdc.run.stop', 'Stop CDC Run'],
  ['cdc.checkpoint.inspect', 'Inspect CDC Checkpoint'],
  ['cdc.replay.prepare', 'Prepare CDC Replay'],
  ['cdc.replay.execute', 'Execute CDC Replay'],
  ['cdc.schema_change.resolve', 'Resolve CDC Schema Change'],
]);

function actor(currentUser={}) {
  return String(currentUser.id ?? currentUser.username ?? currentUser.email ?? 'unknown');
}

function providerIdFromRef(reference) {
  return reference?.provider ?? reference?.providerId ?? reference?.provider_id ?? null;
}

function requireAdapter(adapter, providerId) {
  REQUIRED_ADAPTER_METHODS.forEach((method) => {
    if(typeof adapter?.[method] !== 'function') throw new TypeError(
      `CDC adapter ${providerId} requires ${method}().`
    );
  });
}

function stringList(value, field, providerId) {
  if(!Array.isArray(value[field])) throw new TypeError(
    `${providerId} CDC result requires ${field}[].`
  );
  return [...new Set(value[field].map((item) => platformValue(
    item, `${providerId} ${field} item`
  )))].sort();
}

export function validateCDCProviderResult(input, providerId, operation,
  {allowEmpty=false}={}) {
  plainObject(input, `${providerId} ${operation} result`);
  noRawSecrets(input, `${providerId} ${operation} result`);
  const supportState = platformValue(input.supportState, 'CDC support state');
  if(!SUPPORT_STATES.includes(supportState)) throw new TypeError(
    `${providerId} returned invalid CDC support state.`
  );
  if(!Array.isArray(input.warnings)) throw new TypeError(
    `${providerId} CDC result requires warnings[].`
  );
  const result = immutable({supportState,
    providerVersion: platformValue(input.providerVersion, `${providerId} provider version`),
    evidence: immutable({...plainObject(input.evidence, `${providerId} CDC evidence`)}),
    warnings: input.warnings.map(String),
    nativeDetails: immutable({...plainObject(input.nativeDetails,
      `${providerId} CDC native details`)}),
    readCapabilities: stringList(input, 'readCapabilities', providerId),
    writeCapabilities: stringList(input, 'writeCapabilities', providerId),
    discoveryCapabilities: stringList(input, 'discoveryCapabilities', providerId),
    nativeMechanisms: stringList(input, 'nativeMechanisms', providerId),
    versionConstraints: stringList(input, 'versionConstraints', providerId),
    limitations: stringList(input, 'limitations', providerId),
    runtimeEvidence: input.runtimeEvidence === undefined ? null : immutable({
      ...plainObject(input.runtimeEvidence, `${providerId} CDC runtime evidence`)}),
    value: input.value});
  if(supportState.startsWith('supported') && !allowEmpty && input.value === undefined) {
    throw new TypeError(`${providerId} returned empty success for ${operation}.`);
  }
  return result;
}

function admitted(result, operation) {
  if(!result.supportState.startsWith('supported')) throw new Error(
    `capability_missing: ${operation} is ${result.supportState}. ${result.limitations.join(' ')}`
  );
  return result;
}

export class CDCAdapterRegistry {
  constructor() { this.adapters = new Map(); }
  register(providerId, adapter) {
    providerId = stablePlatformId(providerId, 'CDC provider ID'); requireAdapter(adapter, providerId);
    if(this.adapters.has(providerId)) throw new Error(`CDC adapter already registered: ${providerId}`);
    this.adapters.set(providerId, adapter); return () => this.adapters.delete(providerId);
  }
  get(providerId) {
    return providerId ? this.adapters.get(stablePlatformId(providerId, 'CDC provider ID')) ?? null : null;
  }
  list() { return [...this.adapters.keys()].sort(); }
}

function unknownStatus(providerId, operation) {
  return immutable({providerId: providerId ?? 'unknown', operation, supportState: 'unknown',
    providerVersion: null, evidence: {}, warnings: ['No CDC adapter response is registered.'],
    nativeDetails: {}, readCapabilities: [], writeCapabilities: [],
    discoveryCapabilities: [], nativeMechanisms: [], versionConstraints: [],
    limitations: ['No CDC adapter response is registered.'], runtimeEvidence: null,
    value: undefined});
}

function sessionView(session) {
  return immutable({schema: 'cdeadmin.cdc-session.v1', id: session.id,
    content: session.content, state: session.state, dirty: session.dirty,
    selection: session.selection, validation: session.validation,
    providerStatuses: [...session.providerStatuses.values()], runs: session.runs.map((run) => ({
      ...run, errors: [...run.errors],
    })),
    checkpoints: {...session.checkpoints}, events: [...session.events],
    replayReview: session.replayReview, schemaChanges: [...session.schemaChanges],
    activeTaskId: session.activeTaskId, problems: [...session.problems],
    history: [...session.history], error: session.error,
    createdAt: session.createdAt, updatedAt: session.updatedAt});
}

export class CDCService {
  constructor({tasks, relationships, search, projectAssets, commands,
    adapters=new CDCAdapterRegistry(), events=platformEventService,
    diagnostics=diagnosticsService, now=() => new Date().toISOString()}={}) {
    if(!tasks || !relationships || !search) throw new TypeError(
      'CDC service requires Task, Relationship and Search services.'
    );
    this.tasks = tasks; this.relationships = relationships; this.search = search;
    this.projectAssets = projectAssets; this.commands = commands; this.adapters = adapters;
    this.events = events; this.diagnostics = diagnostics; this.now = now;
    this.sessions = new Map(); this.listeners = new Set(); this.sequence = 0;
    this.disposers = [
      tasks.register('cdc.provision', (request, context) => this._provision(request, context)),
      tasks.register('cdc.snapshot', (request, context) => this._snapshot(request, context)),
      tasks.register('cdc.stream', (request, context) => this._stream(request, context)),
      tasks.register('cdc.replay', (request, context) => this._replay(request, context)),
      tasks.register('cdc.schema_validation', (request, context) =>
        this._schemaValidation(request, context)),
      search.register({id: 'cdc.search', priority: 36,
        types: ['cdc.asset', 'cdc.resource', 'cdc.run', 'cdc.schema_change', 'cdc.command'],
        search: async (query, {context={}}={}) => {
          if(!context.permissions?.includes('cdc.view')) return [];
          const needle = query.toLowerCase(); const results = SEARCHABLE_COMMANDS
            .filter(([id, label]) => `${id} ${label}`.toLowerCase().includes(needle))
            .map(([id, label]) => ({id, type: 'cdc.command', label,
              context: 'CDC command', commandId: id}));
          for(const session of this.sessions.values()) {
            if(`${session.id} ${session.content.name}`.toLowerCase().includes(needle)) results.push({
              id: session.id, type: 'cdc.asset', label: session.content.name || session.id,
              context: 'Project CDC definition'});
            [session.content.source?.resourceRef, session.content.sink?.resourceRef]
              .filter(Boolean).forEach((reference) => {
                if(reference.canonical.toLowerCase().includes(needle)) results.push({
                  id: cdcReferenceKey(reference), type: 'cdc.resource',
                  label: reference.canonical, context: `${session.id} · live provider resource`,
                  reference});
              });
            session.runs.forEach((run) => {
              if(`${run.id} ${run.state}`.toLowerCase().includes(needle)) results.push({
                id: run.id, type: 'cdc.run', label: run.id,
                context: `${session.id} · ${run.state}`});
            });
            session.schemaChanges.forEach((change) => {
              if(`${change.id} ${change.description}`.toLowerCase().includes(needle)) results.push({
                id: change.id, type: 'cdc.schema_change', label: change.description || change.id,
                context: `${session.id} · ${change.classification}`});
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
  _session(id) {
    const session = this.sessions.get(String(id));
    if(!session) throw new Error(`Unknown CDC session: ${id}`);
    return session;
  }

  create(input={}) {
    plainObject(input, 'CDC session'); const id = input.id ?? `cdc-${++this.sequence}`;
    if(this.sessions.has(id)) throw new Error(`CDC session already exists: ${id}`);
    const content = createCDCContent(input.content ?? input); const validation = validateCDCDefinition(content);
    const time = this.now(); const session = {id, content,
      state: !content.source && !content.sink ? 'empty' : validation.valid ? 'ready' : 'validation_error',
      dirty: false, selection: {surface: 'cdc_designer', stage: 'source'}, validation,
      providerStatuses: new Map(), runs: [], checkpoints: {snapshot: null, stream: null},
      events: [], replayReview: null,
      schemaChanges: [...content.schemaEvolutionPolicy.changes], activeTaskId: null,
      problems: [...validation.details], history: [], error: '', createdAt: time, updatedAt: time};
    this.sessions.set(id, session); this._mirrorRelationships(session); this._emit(session);
    return sessionView(session);
  }
  get(id) { return sessionView(this._session(id)); }
  list() { return [...this.sessions.values()].map(sessionView); }
  select(id, selection={}) {
    const session = this._session(id); session.selection = {...session.selection, ...selection};
    return this._touch(session);
  }
  replaceDefinition(id, input, context={}) {
    const session = this._session(id); session.content = createCDCContent(input);
    session.schemaChanges = [...session.content.schemaEvolutionPolicy.changes];
    this._revalidate(session); session.history.push({at: this.now(), actor: actor(context.currentUser),
      action: 'cdc.definition.create'}); this._mirrorRelationships(session);
    return this._touch(session, {dirty: true});
  }
  validate(id) { const session = this._session(id); this._revalidate(session); return this._touch(session); }

  _adapter(reference, operation) {
    const providerId = providerIdFromRef(reference); const adapter = this.adapters.get(providerId);
    if(!adapter) throw new Error(`capability_missing: ${operation}: no CDC adapter for ${providerId ?? 'unknown'}.`);
    return {adapter, providerId};
  }
  async _call(reference, method, input, options={}) {
    const {adapter, providerId} = this._adapter(reference, method);
    const response = await adapter[method](input, options);
    return admitted(validateCDCProviderResult(response, providerId, method,
      {allowEmpty: options.allowEmpty === true}), method);
  }

  async discover(id, context={}) {
    const session = this._session(id); this._revalidate(session);
    if(!session.content.source) throw new Error('configuration_invalid: CDC source is required.');
    const source = session.content.source; const providerId = providerIdFromRef(source.resourceRef);
    const {adapter} = this._adapter(source.resourceRef, 'getCapabilities');
    const [capabilities, mechanisms, privileges, position] = await Promise.all([
      adapter.getCapabilities(source.resourceRef, context),
      adapter.listCaptureMechanisms(source.resourceRef, context),
      adapter.validateCapturePrivileges(source, context),
      adapter.resolveStartPosition(source, context),
    ]).then((results) => results.map((value, index) => validateCDCProviderResult(
      value, providerId, ['getCapabilities', 'listCaptureMechanisms',
        'validateCapturePrivileges', 'resolveStartPosition'][index]
    )));
    if(mechanisms.supportState.startsWith('supported') &&
        (!Array.isArray(mechanisms.value) || !mechanisms.value.includes(source.nativeMechanism))) {
      throw new Error(`capability_missing: provider did not advertise ${source.nativeMechanism}.`);
    }
    const supportState = [capabilities, mechanisms, privileges, position]
      .map((item) => item.supportState).find((value) => ['unsupported', 'unknown',
        'read_only', 'partial'].includes(value)) ?? capabilities.supportState;
    const status = immutable({providerId, operation: 'discovery', supportState:
      supportState, providerVersion: capabilities.providerVersion,
    evidence: {capabilities: capabilities.evidence, mechanisms: mechanisms.evidence,
      privileges: privileges.evidence, position: position.evidence},
    warnings: [...new Set([capabilities, mechanisms, privileges, position]
      .flatMap((item) => item.warnings))], nativeDetails: capabilities.nativeDetails,
    readCapabilities: capabilities.readCapabilities,
    writeCapabilities: capabilities.writeCapabilities,
    discoveryCapabilities: capabilities.discoveryCapabilities,
    nativeMechanisms: Array.isArray(mechanisms.value) ? mechanisms.value : [],
    versionConstraints: capabilities.versionConstraints,
    limitations: [...new Set([capabilities, mechanisms, privileges, position]
      .flatMap((item) => item.limitations))], runtimeEvidence: capabilities.runtimeEvidence,
    value: {position: position.value}});
    session.providerStatuses.set(providerId, status); session.state =
      status.supportState === 'read_only' ? 'read_only' :
        status.supportState.startsWith('supported') ? 'ready' : 'partial';
    session.problems = [...status.warnings, ...status.limitations]; return this._touch(session);
  }

  async start(id, options={}, context={}) {
    const session = this._session(id); this._revalidate(session);
    if(!session.validation.valid) throw new Error(
      `configuration_invalid: ${session.validation.details.join(' ')}`
    );
    const discovered = await this.discover(id, context);
    if(!discovered.providerStatuses[0]?.supportState.startsWith('supported')) throw new Error(
      `capability_missing: CDC source is ${discovered.providerStatuses[0]?.supportState ?? 'unknown'}.`
    );
    const source = session.content.source; const sink = session.content.sink;
    await this._call(sink.resourceRef, 'validateSink', {sink,
      deliveryPolicy: session.content.deliveryPolicy}, context);
    const runId = options.runId ?? `cdc-run-${++this.sequence}`;
    const common = {sessionId: id, runId, retry: options.retry ?? {maximum: 0},
      resourceRefs: [source.resourceRef, sink.resourceRef], cancelable: true, resumable: true,
      audit: {actor: actor(context.currentUser), command: 'cdc.run.start',
        environment: context.environment ?? 'unknown'}};
    const provision = this.tasks.submit({type: 'cdc.provision', label: `Provision ${session.content.name}`,
      ...common}, {...context, owner: actor(context.currentUser)});
    let dependency = provision.id; let snapshot = null;
    if(source.snapshotPolicy.mode !== 'none') {
      snapshot = this.tasks.submit({type: 'cdc.snapshot', label: `Snapshot ${session.content.name}`,
        ...common, dependencies: [provision.id]}, {...context, owner: actor(context.currentUser)});
      dependency = snapshot.id;
    }
    const stream = this.tasks.submit({type: 'cdc.stream', label: `Stream ${session.content.name}`,
      ...common, dependencies: [dependency], action: 'start'},
    {...context, owner: actor(context.currentUser)});
    const run = {id: runId, state: 'provisioning', provisionTaskId: provision.id,
      snapshotTaskId: snapshot?.id ?? null, streamTaskId: stream.id,
      snapshotCheckpoint: null, streamCheckpoint: null, lag: null, throughput: null,
      startedAt: this.now(), finishedAt: null, errors: []};
    session.runs.unshift(run); session.activeTaskId = stream.id;
    session.state = 'background_task_active'; session.history.push({at: this.now(),
      actor: actor(context.currentUser), action: 'cdc.run.start', runId});
    this._publishState(session, run, 'provisioning'); return this._touch(session);
  }

  async control(id, action, context={}) {
    if(!['pause', 'resume', 'stop'].includes(action)) throw new TypeError('CDC control action is invalid.');
    const session = this._session(id); const run = session.runs[0];
    if(!run) throw new Error('configuration_invalid: no CDC run is available.');
    const source = session.content.source; const task = this.tasks.submit({type: 'cdc.stream',
      label: `${action} ${session.content.name}`, sessionId: id, runId: run.id, action,
      resourceRefs: [source.resourceRef], cancelable: false, resumable: false,
      audit: {actor: actor(context.currentUser), command: `cdc.run.${action}`}},
    {...context, owner: actor(context.currentUser)});
    session.activeTaskId = task.id; session.state = 'background_task_active';
    session.history.push({at: this.now(), actor: actor(context.currentUser),
      action: `cdc.run.${action}`, runId: run.id}); return this._touch(session);
  }

  async inspectCheckpoint(id, context={}) {
    const session = this._session(id); const run = session.runs[0];
    if(!run) throw new Error('configuration_invalid: no CDC run is available.');
    const result = await this._call(session.content.source.resourceRef, 'readCheckpoint',
      {definition: session.content, run}, context);
    const checkpoint = validateCheckpoint(result.value); session.checkpoints.stream = checkpoint;
    run.streamCheckpoint = checkpoint;
    this.events.publish('cdc.checkpoint.advanced', {sessionId: id, runId: run.id,
      checkpoint}, {origin: CDC_MODULE_ID}); return this._touch(session);
  }

  async inspectEvents(id, options={}, context={}) {
    const session = this._session(id); const limit = Number(options.limit ?? 100);
    if(!session.content.source) throw new Error('configuration_invalid: CDC source is required.');
    if(!Number.isInteger(limit) || limit < 1 || limit > 1000) throw new TypeError(
      'CDC event sample limit must be from 1 through 1000.'
    );
    const result = await this._call(session.content.source.resourceRef, 'inspectEvents',
      {definition: session.content, run: session.runs[0] ?? null, limit}, context);
    if(!Array.isArray(result.value)) throw new TypeError('CDC event inspection requires an array.');
    const canViewPayloads = context.currentUser?.permissions?.includes('cdc.view_payloads') === true;
    session.events = result.value.slice(0, limit).map((event) => redactEventEnvelope(
      validateEventEnvelope(event), {canViewPayloads, redactedPaths: options.redactedPaths ?? []}
    ));
    return this._touch(session);
  }

  prepareReplay(id, input, context={}) {
    const session = this._session(id); const safety = validateReplaySafety(input);
    if(!session.content.source) throw new Error('configuration_invalid: CDC source is required.');
    if(!safety.valid) throw new Error(`configuration_invalid: ${safety.details.join(' ')}`);
    const task = this.tasks.submit({type: 'cdc.replay', label: `Prepare replay ${session.content.name}`,
      sessionId: id, request: safety.request, action: 'prepare', retry: {maximum: 0},
      resourceRefs: [session.content.source.resourceRef, safety.request.target],
      cancelable: true, resumable: false,
      audit: {actor: actor(context.currentUser), command: 'cdc.replay.prepare'}},
    {...context, owner: actor(context.currentUser)});
    session.activeTaskId = task.id; session.state = 'background_task_active';
    session.problems = [...safety.warnings]; return this._touch(session);
  }

  executeReplay(id, context={}) {
    const session = this._session(id); const review = session.replayReview;
    if(!review?.valid) throw new Error('configuration_invalid: prepare replay before execution.');
    const task = this.tasks.submit({type: 'cdc.replay', label: `Replay ${session.content.name}`,
      sessionId: id, request: review.request, retry: {maximum: 0},
      resourceRefs: [session.content.source.resourceRef, review.request.target],
      cancelable: true, resumable: false,
      audit: {actor: actor(context.currentUser), command: 'cdc.replay.execute',
        productionConfirmation: review.request.productionConfirmed}},
    {...context, owner: actor(context.currentUser)});
    session.activeTaskId = task.id; session.state = 'background_task_active';
    session.history.push({at: this.now(), actor: actor(context.currentUser),
      action: 'cdc.replay.execute'}); return this._touch(session);
  }

  resolveSchemaChange(id, changeId, action, context={}) {
    const session = this._session(id); const change = session.schemaChanges.find(
      (item) => item.id === changeId
    );
    if(!change) throw new Error(`Unknown CDC schema change: ${changeId}`);
    const effective = effectiveSchemaAction({...change, action}, session.content.schemaEvolutionPolicy);
    session.schemaChanges = session.schemaChanges.map((item) => item.id === changeId ?
      immutable({...item, action: effective}) : item);
    session.content = createCDCContent({...session.content, schemaEvolutionPolicy: {
      ...session.content.schemaEvolutionPolicy, changes: session.schemaChanges}});
    session.history.push({at: this.now(), actor: actor(context.currentUser),
      action: 'cdc.schema_change.resolve', changeId, resolution: effective});
    this.events.publish('cdc.schema.changed', {sessionId: id, changeId,
      classification: change.classification, resolution: effective}, {origin: CDC_MODULE_ID});
    return this._touch(session, {dirty: true});
  }

  validateSchemaChange(id, changeId, context={}) {
    const session = this._session(id);
    if(!session.content.source) throw new Error('configuration_invalid: CDC source is required.');
    if(!session.schemaChanges.some((item) => item.id === changeId)) throw new Error(
      `Unknown CDC schema change: ${changeId}`
    );
    const task = this.tasks.submit({type: 'cdc.schema_validation',
      label: `Validate schema change ${changeId}`, sessionId: id, changeId,
      resourceRefs: [session.content.source.resourceRef], cancelable: true,
      resumable: false, audit: {actor: actor(context.currentUser),
        command: 'cdc.schema_validation'}}, {...context, owner: actor(context.currentUser)});
    session.activeTaskId = task.id; session.state = 'background_task_active';
    return this._touch(session);
  }

  async save(id, request, context={}) {
    const session = this._session(id);
    if(!this.projectAssets) throw new Error('Project Asset service is unavailable.');
    const payload = cdcAssetRequest({...request, content: session.content});
    const saved = request.assetId ? await this.projectAssets.update(
      request.projectId, request.assetId, payload
    ) : await this.projectAssets.create(request.projectId, payload);
    session.dirty = false; session.history.push({at: this.now(), actor: actor(context.currentUser),
      action: 'asset.saved', assetId: saved.asset_id, version: saved.version});
    this._touch(session); return saved;
  }

  async _provision(request, context) {
    const session = this._session(request.sessionId); const run = this._run(session, request.runId);
    context.phase('provisioning'); context.progress(0.1, 'Validating source and sink provisioning.');
    try {
      const result = await this._call(session.content.source.resourceRef, 'provision',
        {definition: session.content, run}, {...context, signal: context.signal});
      context.progress(1, 'CDC provisioning completed.'); return result.value;
    } catch(error) { this._failRun(session, run, error); throw error; }
  }

  async _snapshot(request, context) {
    const session = this._session(request.sessionId); const run = this._run(session, request.runId);
    this._publishState(session, run, 'snapshotting'); context.phase('snapshotting');
    try {
      const result = await this._call(session.content.source.resourceRef, 'snapshot',
        {definition: session.content, run}, {...context, signal: context.signal,
          progress: context.progress});
      const normalized = normalizeRunResult(result.value, {phase: 'snapshot'});
      if(normalized.state !== 'catching_up') throw new Error(
        `task_failed: provider returned invalid snapshot completion state ${normalized.state}.`
      );
      run.snapshotCheckpoint = normalized.checkpoint;
      session.checkpoints.snapshot = normalized.checkpoint;
      context.progress(1, 'Initial snapshot completed.'); this._publishState(session, run, 'catching_up');
      return normalized;
    } catch(error) { this._failRun(session, run, error); throw error; }
  }

  async _stream(request, context) {
    const session = this._session(request.sessionId); const run = this._run(session, request.runId);
    if(request.action && request.action !== 'start') {
      const result = await this._call(session.content.source.resourceRef, 'controlRun',
        {definition: session.content, run, action: request.action},
        {...context, signal: context.signal});
      const target = request.action === 'pause' ? 'paused' : request.action === 'resume' ?
        'streaming' : 'stopping'; this._publishState(session, run, target);
      if(request.action === 'stop') this._publishState(session, run, 'stopped');
      session.activeTaskId = null; session.state = run.state === 'stopped' ? 'ready' :
        run.state === 'paused' ? 'ready' : 'background_task_active'; this._touch(session);
      return result.value;
    }
    this._publishState(session, run, 'streaming'); context.phase('streaming');
    try {
      const result = await this._call(session.content.source.resourceRef, 'stream',
        {definition: session.content, run}, {...context, signal: context.signal,
          progress: context.progress, waitIfPaused: context.waitIfPaused});
      const normalized = normalizeRunResult(result.value, {phase: 'stream'});
      run.lag = normalized.lag; run.throughput = normalized.throughput;
      run.streamCheckpoint = normalized.checkpoint;
      session.checkpoints.stream = normalized.checkpoint;
      if(normalized.checkpoint) this.events.publish('cdc.checkpoint.advanced',
        {sessionId: session.id, runId: run.id, checkpoint: normalized.checkpoint},
        {origin: CDC_MODULE_ID});
      session.events = normalized.events.slice(0, 100).map((event) => redactEventEnvelope(
        event, {canViewPayloads: false}
      ));
      session.content.alerts.filter((alert) => alert.enabled && alert.event === 'lag' &&
        Number.isFinite(Number(alert.threshold.maximum)) && normalized.lag !== null &&
        normalized.lag > Number(alert.threshold.maximum)).forEach((alert) =>
        this.events.publish('cdc.lag.threshold', {sessionId: session.id, runId: run.id,
          alertId: alert.id, lag: normalized.lag, maximum: Number(alert.threshold.maximum)},
        {origin: CDC_MODULE_ID}));
      if(!['streaming', 'paused', 'degraded', 'failed', 'stopped'].includes(normalized.state)) {
        throw new Error(`task_failed: provider returned invalid stream completion state ${
          normalized.state}.`);
      }
      if(normalized.state === 'stopped') {
        this._publishState(session, run, 'stopping');
        this._publishState(session, run, 'stopped');
      } else this._publishState(session, run, normalized.state);
      if(['stopped', 'failed'].includes(normalized.state)) run.finishedAt = this.now();
      session.activeTaskId = null; session.state = normalized.state === 'failed' ?
        'runtime_failure' : 'ready'; this._touch(session); return normalized;
    } catch(error) { this._failRun(session, run, error); throw error; }
  }

  async _replay(request, context) {
    const session = this._session(request.sessionId); const safety = validateReplaySafety(
      validateReplayRequest(request.request)
    );
    if(!safety.valid) throw new Error(`configuration_invalid: ${safety.details.join(' ')}`);
    try {
      context.phase(request.action === 'prepare' ? 'preparing_replay' : 'replay');
      context.progress(0.05, 'Validating bounded replay.');
      const result = await this._call(session.content.source.resourceRef, 'replay',
        {definition: session.content, request: safety.request,
          action: request.action === 'prepare' ? 'prepare' : 'execute'},
        {...context, signal: context.signal, progress: context.progress});
      if(request.action === 'prepare') {
        const estimate = result.value?.estimatedEventCount;
        const preparedRequest = estimate === undefined ? safety.request :
          validateReplayRequest({...safety.request, estimatedEventCount: estimate});
        session.replayReview = immutable({...validateReplaySafety(preparedRequest),
          preparedAt: this.now(), preparedBy: actor(context.currentUser),
          providerEvidence: result.evidence, providerWarnings: result.warnings});
      }
      context.progress(1, request.action === 'prepare' ? 'Replay review prepared.' :
        'Replay completed.'); session.activeTaskId = null; session.state = 'ready';
      session.history.push({at: this.now(), actor: actor(context.currentUser),
        action: request.action === 'prepare' ? 'cdc.replay.prepared' : 'cdc.replay.completed',
        evidence: result.evidence}); this._touch(session);
      return result.value;
    } catch(error) { this._taskFailure(session, error); throw error; }
  }

  async _schemaValidation(request, context) {
    const session = this._session(request.sessionId);
    const change = session.schemaChanges.find((item) => item.id === request.changeId);
    if(!change) throw new Error(`Unknown CDC schema change: ${request.changeId}`);
    try {
      const result = await this._call(session.content.source.resourceRef, 'validateSchemaChange',
        {change, policy: session.content.schemaEvolutionPolicy}, context);
      context.progress(1, 'Schema change validation completed.'); session.activeTaskId = null;
      session.state = 'ready'; session.history.push({at: this.now(),
        actor: actor(context.currentUser), action: 'cdc.schema_validation',
        changeId: change.id, evidence: result.evidence});
      this._touch(session); return result.value;
    } catch(error) { this._taskFailure(session, error); throw error; }
  }

  _run(session, runId) {
    const run = session.runs.find((item) => item.id === runId);
    if(!run) throw new Error(`Unknown CDC run: ${runId}`); return run;
  }
  _publishState(session, run, next) {
    if(run.state !== next) assertRunTransition(run.state, next);
    run.state = next; this.events.publish('cdc.run.state_changed',
      {sessionId: session.id, runId: run.id, state: next}, {origin: CDC_MODULE_ID});
  }
  _failRun(session, run, error) {
    if(run.state !== 'failed') {
      try { this._publishState(session, run, 'failed'); } catch { run.state = 'failed'; }
    }
    run.errors.push(error.message); run.finishedAt = this.now(); session.activeTaskId = null;
    session.state = 'runtime_failure'; session.error = error.message;
    session.problems = [...new Set([...session.problems, error.message])];
    this.events.publish('cdc.run.failed', {sessionId: session.id, runId: run.id,
      error: error.message}, {origin: CDC_MODULE_ID}); this._touch(session);
  }
  _taskFailure(session, error) {
    session.activeTaskId = null; session.state = 'runtime_failure'; session.error = error.message;
    session.problems = [...new Set([...session.problems, error.message])]; this._touch(session);
  }
  _revalidate(session) {
    session.validation = validateCDCDefinition(session.content);
    session.problems = [...session.validation.details, ...session.validation.warnings];
    session.state = session.validation.valid ? 'ready' : 'validation_error';
  }
  _clearRelationships(session) {
    const prefix = `cdc:${session.id}`; const graph = this.relationships.snapshot();
    graph.edges.filter((edge) => edge.id.startsWith(prefix)).forEach(
      (edge) => this.relationships.removeEdge(edge.id));
    graph.nodes.filter((node) => node.id.startsWith(prefix)).forEach(
      (node) => this.relationships.removeNode(node.id));
  }
  _mirrorRelationships(session) {
    this._clearRelationships(session); const asset = `cdc:${session.id}`;
    this.relationships.upsertNode({id: asset, kind: 'cdc.asset',
      label: session.content.name || session.id});
    const pairs = [
      ['source', session.content.source?.resourceRef, 'reads_from'],
      ['sink', session.content.sink?.resourceRef, 'writes_to'],
    ];
    pairs.filter(([, ref]) => ref).forEach(([role, ref, type]) => {
      const resource = `${asset}:resource:${role}:${cdcReferenceKey(ref)}`;
      this.relationships.upsertNode({id: resource, kind: 'provider.resource',
        label: ref.canonical, reference: ref});
      this.relationships.upsertEdge({id: `cdc:${session.id}:${role}`, from: asset, to: resource,
        type, origin: CDC_MODULE_ID, metadata: {sessionId: session.id, role,
          origin: 'authored_definition'}});
    });
  }
}

export function unsupportedCDCProviderResult(providerId, operation, limitation) {
  return immutable({...unknownStatus(providerId, operation), supportState: 'unsupported',
    limitations: [String(limitation)], warnings: [String(limitation)]});
}
