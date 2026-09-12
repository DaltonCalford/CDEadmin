/////////////////////////////////////////////////////////////
// Versioned source configuration, health and full-reconcile tasks.
/////////////////////////////////////////////////////////////

import {immutable, noRawSecrets, plainObject, platformValue} from
  '../../platform/serviceUtils';
import {validateDiscoveryIndexBackendConfiguration,
  validateDiscoveryIndexPolicy, validateIndexSourceConfiguration,
  validateIndexSourceHealth} from
  './DiscoveryGovernanceContracts';

export const DISCOVERY_RECONCILE_TASK = 'discovery.index.reconcile';
export const DISCOVERY_RECONCILE_PHASES = Object.freeze([
  'snapshot_source_configuration', 'discover_entities', 'canonicalize',
  'security_label', 'enrich', 'validate', 'build_candidate_revision',
  'compare_counts_deletions', 'publish_atomically', 'retire_previous_revision',
]);

function requireMethod(value, method, label) {
  if(typeof value?.[method] !== 'function') throw new TypeError(
    `${label} requires ${method}.`
  );
}

export class InMemoryDiscoveryIndexConfigurationStore {
  constructor() { this.current = new Map(); this.history = new Map(); }
  save(input, expectedRevision=0) {
    const value = validateIndexSourceConfiguration(input);
    const prior = this.current.get(value.sourceId);
    if((prior?.revision ?? 0) !== expectedRevision) throw new Error(
      'Discovery index source configuration conflict.'
    );
    const stored = immutable({...value, revision: expectedRevision + 1});
    this.current.set(value.sourceId, stored);
    this.history.set(value.sourceId,
      [...(this.history.get(value.sourceId) ?? []), stored]); return stored;
  }
  get(id) { return this.current.get(String(id)) ?? null; }
  list() { return [...this.current.values()].sort((left, right) =>
    left.sourceId.localeCompare(right.sourceId)); }
  revisions(id) { return [...(this.history.get(String(id)) ?? [])]; }
}

export class InMemoryDiscoveryIndexPolicyStore {
  constructor() { this.items = new Map(); this.history = new Map(); }
  save(kind, input, expectedRevision=0) {
    const value = kind === 'policy' ? validateDiscoveryIndexPolicy(input) :
      kind === 'backend' ? validateDiscoveryIndexBackendConfiguration(input) :
        (() => { throw new TypeError('Unknown Discovery index configuration kind.'); })();
    const id = kind === 'policy' ? value.policyId : value.backendId;
    const key = `${kind}:${id}`; const prior = this.items.get(key);
    if((prior?.revision ?? 0) !== expectedRevision) throw new Error(
      'Discovery index administration configuration conflict.'
    );
    const stored = immutable({...value, configurationKind: kind,
      revision: expectedRevision + 1});
    this.items.set(key, stored); this.history.set(key,
      [...(this.history.get(key) ?? []), stored]); return stored;
  }
  get(kind, id) { return this.items.get(`${kind}:${id}`) ?? null; }
  revisions(kind, id) { return [...(this.history.get(`${kind}:${id}`) ?? [])]; }
}

export class DiscoveryIndexSourceRegistry {
  constructor() { this.adapters = new Map(); }
  register(sourceId, adapter) {
    sourceId = platformValue(sourceId, 'Discovery index source adapter ID', 512);
    if(this.adapters.has(sourceId)) throw new TypeError(
      `Discovery index source adapter ${sourceId} is already registered.`
    );
    for(const method of ['test', 'discover']) requireMethod(adapter, method,
      'Discovery index source adapter');
    this.adapters.set(sourceId, adapter); return () => this.adapters.delete(sourceId);
  }
  adapter(sourceId) { return this.adapters.get(String(sourceId)) ?? null; }
  available() { return [...this.adapters.keys()].sort(); }
}

function initialHealth(sourceId, enabled=true) {
  return validateIndexSourceHealth({sourceId,
    state: enabled ? 'IDLE' : 'DISABLED', lastSuccessfulAt: null,
    lastAttemptedAt: null, documentsAdded: 0, documentsUpdated: 0,
    documentsDeleted: 0, errors: 0, permissionFailures: 0, staleCount: 0,
    queueDepth: 0, averageLatencyMs: 0});
}

export class DiscoveryIndexAdministrationService {
  constructor({configurations, policies=null, registry, indexService, tasks,
    now=() => new Date().toISOString(), revisionFactory}={}) {
    for(const method of ['save', 'get', 'list', 'revisions']) requireMethod(
      configurations, method, 'Discovery index configuration store');
    requireMethod(registry, 'adapter', 'Discovery index source registry');
    for(const method of ['publish', 'health']) requireMethod(indexService,
      method, 'Discovery index service');
    for(const method of ['register', 'submit']) requireMethod(tasks, method,
      'Discovery index TaskService');
    if(typeof revisionFactory !== 'function') throw new TypeError(
      'Discovery index administration requires a revision authority.'
    );
    this.configurations = configurations; this.registry = registry;
    if(policies) for(const method of ['save', 'get', 'revisions']) requireMethod(
      policies, method, 'Discovery index policy store');
    this.policies = policies;
    this.indexService = indexService; this.tasks = tasks; this.now = now;
    this.revisionFactory = revisionFactory; this.sourceHealth = new Map();
    this.unregister = tasks.register(DISCOVERY_RECONCILE_TASK,
      (request, context) => this._run(request, context));
  }

  saveSource(input, {expectedRevision=0, security}={}) {
    requireMethod(security, 'administerDiscoveryIndex',
      'Discovery index administration security');
    if(security.administerDiscoveryIndex(input.sourceId, 'configure') !== true) {
      throw new Error('Discovery index source configuration denied.');
    }
    const value = this.configurations.save(input, expectedRevision);
    if(!this.sourceHealth.has(value.sourceId)) this.sourceHealth.set(value.sourceId,
      initialHealth(value.sourceId, value.enabled));
    else this._health(value.sourceId, {state: value.enabled ? 'IDLE' : 'DISABLED'});
    return value;
  }

  saveAdministrationConfiguration(kind, input, {expectedRevision=0,
    security}={}) {
    if(!this.policies) throw new TypeError(
      'Discovery index policy store is unavailable.'
    );
    requireMethod(security, 'administerDiscoveryIndex',
      'Discovery index administration security');
    if(security.administerDiscoveryIndex(kind, 'configure') !== true) throw new Error(
      'Discovery index administration configuration denied.'
    );
    return this.policies.save(kind, input, expectedRevision);
  }

  async testSource(sourceId, {security}={}) {
    const config = this._config(sourceId);
    requireMethod(security, 'administerDiscoveryIndex',
      'Discovery index administration security');
    if(security.administerDiscoveryIndex(config.sourceId, 'test') !== true) {
      throw new Error('Discovery index source test denied.');
    }
    const adapter = this._adapter(config); const started = Date.now();
    try {
      const result = await adapter.test(config, {security});
      plainObject(result, 'Discovery index source test result');
      noRawSecrets(result, 'Discovery index source test result');
      this._health(config.sourceId, {state: config.enabled ? 'IDLE' : 'DISABLED',
        lastAttemptedAt: this.now(), lastSuccessfulAt: this.now(),
        averageLatencyMs: Math.max(0, Date.now() - started)});
      return immutable({...result, available: true});
    } catch(error) {
      this._failure(config, error, started); throw error;
    }
  }

  refresh(sourceId, {security, owner=null, deletionApproval=false}={}) {
    const config = this._config(sourceId);
    requireMethod(security, 'administerDiscoveryIndex',
      'Discovery index administration security');
    if(!config.enabled) throw new TypeError('Discovery index source is disabled.');
    if(security.administerDiscoveryIndex(config.sourceId, 'refresh') !== true) {
      throw new Error('Discovery index source refresh denied.');
    }
    this._adapter(config);
    this._health(config.sourceId, {state: 'QUEUED', queueDepth: 1});
    return this.tasks.submit({type: DISCOVERY_RECONCILE_TASK,
      label: `Reconcile Discovery source ${config.name}`,
      sourceId: config.sourceId, configurationRevision: config.revision,
      deletionApproval, cancelable: true, resumable: false,
      resourceRefs: Array.isArray(config.scope) ? config.scope : [],
      audit: {sourceId: config.sourceId,
        configurationRevision: config.revision}}, {security, owner});
  }

  sourceStatus(sourceId, {security}={}) {
    const config = this._config(sourceId);
    requireMethod(security, 'viewDiscoveryIndexHealth',
      'Discovery index health security');
    if(security.viewDiscoveryIndexHealth(sourceId) !== true) throw new Error(
      'Discovery index source health access denied.'
    );
    return immutable({configuration: config,
      health: this.sourceHealth.get(config.sourceId) ?? initialHealth(
        config.sourceId, config.enabled)});
  }

  async globalHealth({security}={}) {
    requireMethod(security, 'viewDiscoveryIndexHealth',
      'Discovery index health security');
    if(security.viewDiscoveryIndexHealth('*') !== true) throw new Error(
      'Discovery global index health access denied.'
    );
    const sources = this.configurations.list().map((config) => ({config,
      health: this.sourceHealth.get(config.sourceId) ?? null}));
    const mandatoryUnknown = sources.some(({config, health}) => config.enabled &&
      config.mandatory && !health?.lastSuccessfulAt);
    const states = sources.map(({health}) => health?.state).filter(Boolean);
    const state = mandatoryUnknown ? 'degraded' : states.includes('FAILED') ? 'failed' :
      states.includes('AUTH_REQUIRED') ? 'auth_required' :
        states.includes('DEGRADED') ? 'degraded' : 'healthy';
    return immutable({state, mandatoryUnknown, sources,
      index: await this.indexService.health()});
  }

  async _run(request, context) {
    const config = this._config(request.sourceId);
    if(config.revision !== request.configurationRevision) throw new TypeError(
      'Discovery source configuration changed before task execution.'
    );
    const adapter = this._adapter(config); const started = Date.now();
    this._health(config.sourceId, {state: 'RUNNING', queueDepth: 0,
      lastAttemptedAt: this.now()});
    try {
      const advance = async (phase) => {
        const index = DISCOVERY_RECONCILE_PHASES.indexOf(phase);
        context.phase(phase); context.progress(index /
          DISCOVERY_RECONCILE_PHASES.length, phase);
        await context.waitIfPaused();
        if(context.signal.aborted) throw new Error('Discovery reconcile cancelled.');
      };
      await advance('snapshot_source_configuration');
      await advance('discover_entities');
      const discovered = await adapter.discover(config, {security: context.security,
        signal: context.signal});
      plainObject(discovered, 'Discovery source discovery result');
      noRawSecrets(discovered, 'Discovery source discovery result');
      if(!Array.isArray(discovered.items)) throw new TypeError(
        'Discovery source adapter must return candidate items.'
      );
      for(const phase of ['canonicalize', 'security_label', 'enrich', 'validate',
        'build_candidate_revision', 'compare_counts_deletions']) await advance(phase);
      const prior = await this.indexService.health();
      await advance('publish_atomically');
      const result = await this.indexService.publish({
        revision: this.revisionFactory(config.sourceId), items: discovered.items,
        embeddings: discovered.embeddings ?? {},
        priorDocumentCount: prior.documentCount ?? 0,
        mandatorySource: config.mandatory,
        deletionApproval: request.deletionApproval === true},
      {authorization: context.security});
      if(result.approvalRequired) {
        this._health(config.sourceId, {state: 'PAUSED',
          staleCount: prior.documentCount ?? 0,
          averageLatencyMs: Math.max(0, Date.now() - started)});
        return immutable({...result, sourceId: config.sourceId,
          state: 'awaiting_deletion_approval'});
      }
      await advance('retire_previous_revision');
      const metrics = discovered.metrics ?? {};
      this._health(config.sourceId, {state: 'IDLE', lastSuccessfulAt: this.now(),
        documentsAdded: metrics.added ?? result.documentCount,
        documentsUpdated: metrics.updated ?? 0,
        documentsDeleted: metrics.deleted ?? 0, errors: 0,
        permissionFailures: 0, staleCount: 0,
        averageLatencyMs: Math.max(0, Date.now() - started)});
      return immutable({...result, sourceId: config.sourceId, state: 'ACTIVE'});
    } catch(error) {
      this._failure(config, error, started); throw error;
    }
  }

  _config(sourceId) {
    const value = this.configurations.get(platformValue(sourceId,
      'Discovery index source ID', 512));
    if(!value) throw new Error(`Discovery index source ${sourceId} was not found.`);
    return value;
  }
  _adapter(config) {
    const adapter = this.registry.adapter(config.sourceId);
    if(!adapter) throw new Error(
      `Discovery index source adapter ${config.sourceId} is unavailable.`
    );
    return adapter;
  }
  _health(sourceId, changes) {
    const current = this.sourceHealth.get(sourceId) ?? initialHealth(sourceId);
    this.sourceHealth.set(sourceId, validateIndexSourceHealth({...current,
      ...changes, sourceId}));
  }
  _failure(config, error, started) {
    const current = this.sourceHealth.get(config.sourceId) ??
      initialHealth(config.sourceId);
    this._health(config.sourceId, {state: error.code === 'auth_required' ?
      'AUTH_REQUIRED' : current.lastSuccessfulAt ? 'DEGRADED' : 'FAILED',
    lastAttemptedAt: this.now(), errors: current.errors + 1,
    permissionFailures: current.permissionFailures +
      (error.code === 'permission_denied' ? 1 : 0),
    staleCount: current.lastSuccessfulAt ? Math.max(1, current.staleCount) : 0,
    queueDepth: 0, averageLatencyMs: Math.max(0, Date.now() - started)});
  }
}
