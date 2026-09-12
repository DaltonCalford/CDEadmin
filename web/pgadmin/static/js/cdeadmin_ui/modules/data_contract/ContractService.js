/////////////////////////////////////////////////////////////
// Data Contract Manager orchestration, evidence and compliance.
/////////////////////////////////////////////////////////////

import {diagnosticsService, platformEventService, stablePlatformId} from '../../platform/PlatformRegistry';
import {abortError, immutable, noRawSecrets, plainObject, platformValue} from '../../platform/serviceUtils';
import {
  CONTRACT_MODULE_ID, contractAssetRequest,
  contractReferenceKey, createContractContent, validateResourceBinding,
} from './contracts';
import {
  compareContractVersions, nextStatus, normalizeDrift,
  summarizeCompliance, validateContractStructure,
} from './ContractEngine';
import {exportODCS, importODCS} from './ODCSAdapter';

export const CONTRACT_SERVICE_ID = 'contract.runtime';
export const CONTRACT_TASKS = Object.freeze([
  'contract.import', 'contract.compliance.validation', 'contract.metadata.compare',
]);
export const CONTRACT_EVENTS = Object.freeze([
  'contract.changed', 'contract.activated', 'contract.compliance.failed',
  'contract.drift.detected',
]);

const SUPPORT_STATES = Object.freeze([
  'supported_native', 'supported_via_cdeadmin', 'supported_via_external_adapter',
  'read_only', 'partial', 'unsupported', 'unknown',
]);

function providerIdFor(binding) {
  const ref = binding?.targetRef;
  return ref?.provider ?? ref?.providerId ?? ref?.provider_id ?? null;
}

function auditActor(currentUser={}) {
  return String(currentUser.id ?? currentUser.username ?? currentUser.email ?? 'unknown');
}

function requireAdapter(adapter, providerId) {
  ['mapResourceToContractSchema', 'compareContractToResource',
    'observeSlaMetric', 'validateClassification'].forEach((method) => {
    if(typeof adapter?.[method] !== 'function') throw new TypeError(
      `Data Contract adapter ${providerId} requires ${method}().`
    );
  });
}

function stringList(input, field, providerId) {
  if(!Array.isArray(input[field])) throw new TypeError(
    `${providerId} Data Contract result requires ${field}[].`
  );
  return [...new Set(input[field].map((item) => platformValue(item,
    `${providerId} ${field}`)))].sort();
}

export function validateContractProviderResult(input, providerId, operation,
  {allowEmpty=false}={}) {
  plainObject(input, `${providerId} ${operation} result`);
  noRawSecrets(input, `${providerId} ${operation} result`);
  const supportState = platformValue(input.supportState, 'Data Contract support state');
  if(!SUPPORT_STATES.includes(supportState)) throw new TypeError(
    `${providerId} returned invalid Data Contract support state.`
  );
  if(!Array.isArray(input.warnings)) throw new TypeError(
    `${providerId} Data Contract result requires warnings[].`
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

export class ContractAdapterRegistry {
  constructor() { this.adapters = new Map(); }
  register(providerId, adapter) {
    providerId = stablePlatformId(providerId, 'Data Contract provider ID');
    requireAdapter(adapter, providerId);
    if(this.adapters.has(providerId)) throw new Error(
      `Data Contract adapter already registered: ${providerId}`
    );
    this.adapters.set(providerId, adapter); return () => this.adapters.delete(providerId);
  }
  get(providerId) {
    return this.adapters.get(stablePlatformId(providerId, 'Data Contract provider ID')) ?? null;
  }
  list() { return [...this.adapters.keys()].sort(); }
}

function unsupported(providerId, operation) {
  return immutable({providerId: providerId ?? 'unknown', operation,
    supportState: 'unknown', providerVersion: null, evidence: {}, warnings: [
      'No Data Contract adapter response is registered.',
    ], nativeDetails: {}, readCapabilities: [], writeCapabilities: [],
    discoveryCapabilities: [], nativeMechanisms: [], versionConstraints: [],
    limitations: ['No Data Contract adapter response is registered.'], runtimeEvidence: null});
}

function view(session) {
  return immutable({schema: 'cdeadmin.contract-session.v1', id: session.id,
    content: session.content, state: session.state, dirty: session.dirty,
    validation: session.validation, selectedElementId: session.selectedElementId,
    selectedBindingId: session.selectedBindingId,
    providerStatuses: [...session.providerStatuses.values()],
    metadataComparison: session.metadataComparison,
    complianceRuns: [...session.complianceRuns], drift: [...session.drift],
    versions: [...session.versions.values()], versionDiff: session.versionDiff,
    interoperability: session.interoperability, activeTaskId: session.activeTaskId,
    problems: [...session.problems], history: [...session.history], error: session.error,
    createdAt: session.createdAt, updatedAt: session.updatedAt});
}

function complianceInput(input, label) {
  const value = plainObject(input, label); noRawSecrets(value, label);
  if(typeof value.compliant !== 'boolean') throw new TypeError(`${label} requires compliant.`);
  if(!Array.isArray(value.details) || !Array.isArray(value.evidence)) throw new TypeError(
    `${label} requires details[] and evidence[].`
  );
  return immutable({compliant: value.compliant, details: value.details.map(String),
    evidence: value.evidence.map((item) => immutable({...plainObject(item, `${label} evidence`)}))});
}

export class ContractService {
  constructor({adapters=new ContractAdapterRegistry(), tasks, relationships, search,
    projectAssets, commands=null, events=platformEventService,
    diagnostics=diagnosticsService, now=() => new Date().toISOString()}={}) {
    if(!tasks || !relationships || !search || !projectAssets) throw new TypeError(
      'Data Contract Manager requires task, relationship, search and project asset services.'
    );
    this.adapters = adapters; this.tasks = tasks; this.relationships = relationships;
    this.search = search; this.projectAssets = projectAssets; this.commands = commands;
    this.events = events; this.diagnostics = diagnostics; this.now = now;
    this.sessions = new Map(); this.listeners = new Set(); this.sequence = 0;
    this.disposers = []; this._registerTasks(); this._registerSearch();
  }

  _registerTasks() {
    const runner = (method) => async (request, context) =>
      this[method](request.sessionId, {...request, ...context});
    this.disposers.push(
      this.tasks.register('contract.import', runner('_importODCS')),
      this.tasks.register('contract.compliance.validation', runner('_compliance')),
      this.tasks.register('contract.metadata.compare', runner('_metadataCompare')),
    );
  }

  _registerSearch() {
    this.disposers.push(this.search.register({id: 'contract.search', priority: 38,
      types: ['contract.asset', 'contract.element', 'contract.binding', 'contract.compliance'],
      search: async (query, {context={}}={}) => {
        if(context.permissions && !context.permissions.includes('contract.view')) return [];
        const needle = String(query).toLowerCase(); const found = [];
        for(const session of this.sessions.values()) {
          if(`${session.content.name} ${session.content.domain} ${session.content.description}`
            .toLowerCase().includes(needle)) found.push({id: session.id, type: 'contract.asset',
            label: session.content.name || session.id,
            context: `${session.content.domain} · ${session.content.status}`});
          session.content.elements.forEach((element) => {
            if(`${element.name} ${element.logicalType} ${element.description}`
              .toLowerCase().includes(needle)) found.push({id: element.id,
              type: 'contract.element', label: element.name,
              context: `${session.id} · ${element.logicalType}`});
          });
          session.content.bindings.forEach((binding) => {
            const label = binding.targetRef.canonical ?? binding.targetRef.id;
            if(String(label).toLowerCase().includes(needle)) found.push({id: binding.id,
              type: 'contract.binding', label, context: `${session.id} · ${binding.environment}`,
              reference: binding.targetRef});
          });
          session.complianceRuns.forEach((run) => {
            if(run.id.toLowerCase().includes(needle)) found.push({id: run.id,
              type: 'contract.compliance', label: run.id,
              context: `${session.id} · ${run.summary.compliant ? 'compliant' : 'noncompliant'}`});
          });
        }
        return found;
      }}));
  }

  dispose() {
    this.sessions.forEach((session) => this._clearRelationships(session));
    this.disposers.reverse().forEach((remove) => remove()); this.disposers = [];
  }
  subscribe(listener) { this.listeners.add(listener); return () => this.listeners.delete(listener); }
  _emit(session) { this.listeners.forEach((listener) => listener(view(session))); }
  _touch(session, {dirty=session.dirty}={}) {
    session.updatedAt = this.now(); session.dirty = dirty; this._emit(session); return view(session);
  }

  create(input={}) {
    plainObject(input, 'Data Contract session'); noRawSecrets(input, 'Data Contract session');
    const id = input.id ?? `contract-${++this.sequence}`;
    if(this.sessions.has(id)) throw new Error(`Data Contract session already exists: ${id}`);
    const content = createContractContent(input.content ?? input); const time = this.now();
    const session = {id, content,
      state: content.elements.length || content.name ? 'ready' : 'empty', dirty: false,
      validation: validateContractStructure(content), selectedElementId: null,
      selectedBindingId: null, providerStatuses: new Map(), metadataComparison: null,
      complianceRuns: [], drift: [], versions: new Map([[content.contractVersion, content]]),
      versionDiff: null, interoperability: null, activeTaskId: null,
      problems: [], history: [], error: '', createdAt: time, updatedAt: time};
    this.sessions.set(id, session); this._mirrorRelationships(session); this._emit(session);
    return view(session);
  }

  get(id) {
    const session = this.sessions.get(String(id));
    if(!session) throw new Error(`Unknown Data Contract session: ${id}`);
    return view(session);
  }
  list() { return [...this.sessions.values()].map(view); }

  select(id, {elementId=null, bindingId=null}={}) {
    const session = this.sessions.get(String(id)); if(!session) return this.get(id);
    if(elementId && !session.content.elements.some((item) => item.id === elementId)) {
      throw new Error(`Unknown Contract element: ${elementId}`);
    }
    if(bindingId && !session.content.bindings.some((item) => item.id === bindingId)) {
      throw new Error(`Unknown Contract binding: ${bindingId}`);
    }
    session.selectedElementId = elementId; session.selectedBindingId = bindingId;
    return this._touch(session);
  }

  replaceDefinition(id, input, action='edit', context={}) {
    const session = this.sessions.get(String(id)); if(!session) return this.get(id);
    session.content = createContractContent(input);
    session.validation = validateContractStructure(session.content);
    session.state = session.content.elements.length || session.content.name ? 'ready' : 'empty';
    session.history.push({at: this.now(), actor: auditActor(context.currentUser), action,
      contractVersion: session.content.contractVersion});
    this._mirrorRelationships(session); this.events.publish('contract.changed', {
      sessionId: id, contractVersion: session.content.contractVersion, action,
    }, {origin: CONTRACT_MODULE_ID});
    return this._touch(session, {dirty: true});
  }

  validate(id) {
    const session = this.sessions.get(String(id)); if(!session) return this.get(id);
    session.validation = validateContractStructure(session.content);
    session.state = session.validation.valid ? 'ready' : 'validation_error';
    return this._touch(session);
  }

  bindResource(id, input, context={}) {
    const session = this.sessions.get(String(id)); if(!session) return this.get(id);
    const binding = validateResourceBinding(input);
    const current = session.content.bindings.filter((item) => item.id !== binding.id);
    return this.replaceDefinition(id, {...session.content, bindings: [...current, binding]},
      'bind_resource', context);
  }

  _task(session, type, label, extra={}, taskContext={}) {
    const environments = [...new Set(session.content.bindings.map((item) => item.environment))]
      .sort();
    const task = this.tasks.submit({id: `${session.id}:${type}:${++this.sequence}`,
      type, label, sessionId: session.id,
      resourceRefs: session.content.bindings.map((item) => item.targetRef)
        .filter((item) => item.schema === 'cdeadmin.resource-ref.v1'), assetRefs: [],
      audit: {moduleId: CONTRACT_MODULE_ID, operation: type,
        actor: auditActor(taskContext.currentUser), environments,
        confirmationReference: taskContext.confirmationReference ?? null}, ...extra}, {
      owner: {moduleId: CONTRACT_MODULE_ID, sessionId: session.id}, ...taskContext});
    session.activeTaskId = task.id; session.state = 'background_task_active'; this._touch(session);
    this.tasks.wait(task.id).then(() => {
      if(session.activeTaskId === task.id) { session.activeTaskId = null;
        session.state = session.validation.valid ? 'ready' : 'validation_error';
        session.error = ''; this._touch(session); }
    }).catch((error) => {
      const reported = error.name === 'AbortError' ? 'task_cancelled' :
        String(error.message).split(':')[0].replaceAll(' ', '_');
      this.reportError(session.id, error, [
        'configuration_invalid', 'provider_unavailable', 'permission_denied',
        'capability_missing', 'partial_result', 'stale_result', 'conflict',
        'external_format_invalid', 'internal_error',
      ].includes(reported) ? reported : 'task_failed');
    });
    return task;
  }

  importODCS(id, source, commandContext={}) {
    const session = this.sessions.get(String(id)); if(!session) return this.get(id);
    if(typeof source !== 'string' && (!source || Array.isArray(source) || typeof source !== 'object')) {
      throw new TypeError('ODCS import source must be JSON text or an object.');
    }
    let checkedSource;
    try {
      const encoded = typeof source === 'string' ? source : JSON.stringify(source);
      if(!encoded || encoded.length > 16 * 1024 * 1024) throw new TypeError(
        'ODCS source is invalid or exceeds 16 MiB.'
      );
      checkedSource = JSON.parse(encoded);
    } catch(error) {
      throw new TypeError(`external_format_invalid: ${error.message}`);
    }
    noRawSecrets(checkedSource, 'ODCS import source');
    return this._task(session, 'contract.import', 'Import ODCS data contract',
      {source: checkedSource}, commandContext);
  }

  async _importODCS(id, context) {
    const session = this.sessions.get(String(id)); context.phase?.('validating', 'Validating ODCS');
    if(context.signal?.aborted) throw abortError();
    const imported = importODCS(context.source); let content = imported.content;
    if(content.status === 'active') content = createContractContent({...content, status: 'proposed',
      extensions: {...content.extensions, 'cdeadmin.importedStatus': 'active'}});
    session.content = content; session.interoperability = imported.interoperability;
    session.versions.set(content.contractVersion, content);
    session.validation = validateContractStructure(content); session.history.push({at: this.now(),
      actor: auditActor(context.currentUser), action: 'import.odcs',
      apiVersion: imported.interoperability.apiVersion});
    this._mirrorRelationships(session); context.progress?.(1, 'ODCS contract imported');
    this._touch(session, {dirty: true}); return imported.interoperability;
  }

  exportODCS(id, options={}, context={}) {
    const session = this.sessions.get(String(id)); if(!session) return this.get(id);
    const result = exportODCS(session.content, options); session.history.push({at: this.now(),
      actor: auditActor(context.currentUser), action: 'export.odcs',
      apiVersion: result.apiVersion}); this._touch(session);
    return result;
  }

  async _provider(session, binding, method, args, context, {allowEmpty=false}={}) {
    const providerId = providerIdFor(binding); const adapter = providerId ?
      this.adapters.get(providerId) : null;
    if(!adapter) { session.providerStatuses.set(providerId ?? 'unknown',
      unsupported(providerId, method)); throw new Error(
      'capability_missing: Data Contract provider support is unknown.'
    ); }
    if(context.signal?.aborted) throw abortError();
    const result = validateContractProviderResult(await adapter[method](...args),
      providerId, method, {allowEmpty});
    session.providerStatuses.set(providerId, immutable({...result, providerId,
      operation: method, value: undefined}));
    result.warnings.forEach((warning) => context.warning?.(warning, {providerId}));
    if(!result.supportState.startsWith('supported')) throw new Error(
      `capability_missing: ${providerId} reports ${result.supportState} for ${method}.`
    );
    return {providerId, result};
  }

  syncMetadata(id, {bindingId}, context={}) {
    const session = this.sessions.get(String(id)); if(!session) return this.get(id);
    const binding = session.content.bindings.find((item) => item.id === bindingId);
    if(!binding) throw new Error(`configuration_invalid: Unknown Contract binding: ${bindingId}`);
    return this._metadataCompare(id, {...context, bindingId});
  }

  compareMetadata(id, {bindingId}, commandContext={}) {
    const session = this.sessions.get(String(id)); if(!session) return this.get(id);
    return this._task(session, 'contract.metadata.compare', 'Compare contract to live metadata',
      {bindingId}, commandContext);
  }

  async _metadataCompare(id, context) {
    const session = this.sessions.get(String(id));
    const bindings = context.bindingId ? session.content.bindings.filter((item) =>
      item.id === context.bindingId) : session.content.bindings;
    if(!bindings.length) throw new Error('configuration_invalid: A resource binding is required.');
    const comparisons = [];
    for(const [index, binding] of bindings.entries()) {
      const mapped = await this._provider(session, binding, 'mapResourceToContractSchema',
        [binding.targetRef], context);
      const compared = await this._provider(session, binding, 'compareContractToResource',
        [session.content, binding], context);
      const drift = normalizeDrift(plainObject(compared.result.value,
        'Provider contract comparison').drift);
      comparisons.push(immutable({bindingId: binding.id, providerId: compared.providerId,
        mappedSchema: mapped.result.value, drift}));
      if(drift.state !== 'in_sync') await this.events.publish('contract.drift.detected', {
        sessionId: id, bindingId: binding.id, state: drift.state,
      }, {origin: CONTRACT_MODULE_ID});
      context.progress?.((index + 1) / bindings.length, `Compared ${binding.id}`);
    }
    session.metadataComparison = immutable({schema: 'cdeadmin.contract-metadata-comparison.v1',
      comparisons, generatedAt: this.now()}); session.drift = comparisons.map((item) => item.drift);
    session.history.push({at: this.now(), actor: auditActor(context.currentUser),
      action: 'metadata.compare', bindingIds: comparisons.map((item) => item.bindingId)});
    this._touch(session); return session.metadataComparison;
  }

  compliance(id, {qualityResults=[]}={}, commandContext={}) {
    const session = this.sessions.get(String(id)); if(!session) return this.get(id);
    const structure = validateContractStructure(session.content);
    if(!structure.valid) throw new Error(`configuration_invalid: ${structure.details.join(' ')}`);
    const normalizedQuality = qualityResults.map((item) => complianceInput(item,
      'Quality compliance result'));
    return this._task(session, 'contract.compliance.validation',
      'Run live Data Contract compliance', {qualityResults: normalizedQuality}, commandContext);
  }

  async _compliance(id, context) {
    const session = this.sessions.get(String(id)); const structure = validateContractStructure(session.content);
    const bindings = []; const schema = []; const sla = []; const classifications = [];
    session.drift = [];
    for(const [index, binding] of session.content.bindings.entries()) {
      context.phase?.('provider_validation', `Validating ${binding.id}`);
      bindings.push({compliant: true, details: [], evidence: [{bindingId: binding.id}]});
      const compared = await this._provider(session, binding, 'compareContractToResource',
        [session.content, binding], context);
      const comparison = plainObject(compared.result.value, 'Provider comparison');
      schema.push(complianceInput(comparison.compliance, 'Schema compliance result'));
      const drift = normalizeDrift(comparison.drift); session.drift.push(drift);
      if(drift.state !== 'in_sync') await this.events.publish('contract.drift.detected', {
        sessionId: id, bindingId: binding.id, state: drift.state,
      }, {origin: CONTRACT_MODULE_ID});
      const classified = await this._provider(session, binding, 'validateClassification',
        [binding], context);
      classifications.push(complianceInput(classified.result.value,
        'Classification compliance result'));
      for(const obligation of session.content.sla.filter((item) =>
        !item.elementId || item.elementId === binding.elementId)) {
        const observed = await this._provider(session, binding, 'observeSlaMetric',
          [binding, obligation], context);
        sla.push(complianceInput(observed.result.value, 'SLA compliance result'));
      }
      context.progress?.((index + 1) / Math.max(1, session.content.bindings.length),
        `Validated ${binding.id}`);
    }
    const summary = summarizeCompliance({structure, bindingResults: bindings,
      schemaResults: schema, qualityResults: context.qualityResults ?? [],
      slaResults: sla, classificationResults: classifications});
    const run = immutable({schema: 'cdeadmin.contract-compliance-run.v1',
      id: `contract-compliance-${++this.sequence}`, contractVersion: session.content.contractVersion,
      startedAt: context.startedAt ?? this.now(), finishedAt: this.now(), summary,
      providerStatuses: [...session.providerStatuses.values()], drift: [...session.drift]});
    session.complianceRuns.unshift(run); session.complianceRuns.splice(1000);
    session.history.push({at: this.now(), actor: auditActor(context.currentUser),
      action: 'compliance.run', runId: run.id, result: summary.compliant ?
        'compliant' : 'noncompliant'});
    context.resultReference?.({schema: 'cdeadmin.result-ref.v1', id: run.id});
    if(!summary.compliant) await this.events.publish('contract.compliance.failed', {
      sessionId: id, runId: run.id,
    }, {origin: CONTRACT_MODULE_ID});
    this._touch(session); return run;
  }

  createVersion(id, version, context={}) {
    const session = this.sessions.get(String(id)); if(!session) return this.get(id);
    version = platformValue(version, 'New contract version');
    if(session.versions.has(version)) throw new Error(`conflict: Contract version already exists: ${version}`);
    const prior = session.content; session.versions.set(prior.contractVersion, prior);
    session.content = createContractContent({...prior, contractVersion: version});
    session.versions.set(version, session.content); session.history.push({at: this.now(),
      actor: auditActor(context.currentUser), action: 'version.create',
      from: prior.contractVersion, to: version});
    return this._touch(session, {dirty: true});
  }

  setStatus(id, status, {reason='', currentUser={}}={}) {
    const session = this.sessions.get(String(id)); if(!session) return this.get(id);
    const oldStatus = session.content.status;
    const transition = nextStatus(oldStatus, status, {reason});
    if(status === 'active') {
      if(!currentUser.permissions?.includes('contract.activate')) throw new Error(
        'permission_denied: contract.activate permission is required.'
      );
      const validation = validateContractStructure(session.content);
      if(!validation.valid) throw new Error(`configuration_invalid: ${validation.details.join(' ')}`);
    }
    session.content = createContractContent({...session.content, status});
    session.history.push({at: this.now(), actor: auditActor(currentUser),
      action: 'status.set', from: oldStatus,
      to: status, direction: transition.direction, reason: transition.reason ?? ''});
    if(status === 'active') this.events.publish('contract.activated', {sessionId: id,
      contractVersion: session.content.contractVersion}, {origin: CONTRACT_MODULE_ID});
    this.events.publish('contract.changed', {sessionId: id, action: 'status.set', status},
      {origin: CONTRACT_MODULE_ID});
    return this._touch(session, {dirty: true});
  }

  compareVersions(id, leftVersion, rightVersion) {
    const session = this.sessions.get(String(id)); if(!session) return this.get(id);
    const left = session.versions.get(leftVersion); const right = session.versions.get(rightVersion);
    if(!left || !right) throw new Error('Both Data Contract versions must exist.');
    session.versionDiff = compareContractVersions(left, right); return this._touch(session);
  }

  cancel(id, taskId) {
    const session = this.sessions.get(String(id)); if(!session) return this.get(id);
    if(session.activeTaskId !== taskId) throw new Error(`Unknown active Data Contract task: ${taskId}`);
    return this.tasks.cancel(taskId);
  }

  _mirrorRelationships(session) {
    const contractNode = `contract:${session.id}`;
    this._clearRelationships(session, {keepRoot: true});
    this.relationships.upsertNode({id: contractNode, kind: 'contract.asset',
      reference: {schema: 'cdeadmin.asset-ref.v1', projectId: 'runtime', assetId: session.id},
      label: session.content.name || session.id,
      metadata: {domain: session.content.domain, status: session.content.status,
        contractVersion: session.content.contractVersion}});
    session.content.elements.forEach((element) => this.relationships.upsertNode({
      id: `${contractNode}:element:${element.id}`, kind: 'contract.element',
      reference: {schema: 'cdeadmin.external-ref.v1', id: `${session.id}/${element.id}`},
      label: element.name, metadata: {logicalType: element.logicalType}}));
    session.content.bindings.forEach((binding) => {
      const targetId = `contract-ref:${contractReferenceKey(binding.targetRef)}`;
      this.relationships.upsertNode({id: targetId, kind: 'contract.bound-resource',
        reference: binding.targetRef,
        label: binding.targetRef.canonical ?? binding.targetRef.id ?? binding.id,
        metadata: {environment: binding.environment}});
      this.relationships.upsertEdge({id: `${contractNode}:binding:${binding.id}`,
        from: `${contractNode}:element:${binding.elementId}`, to: targetId,
        relation: 'binds_to', origin: 'project_declared', confidence: 1,
        evidenceRefs: binding.observedRevision ? [{id: binding.observedRevision,
          origin: 'provider_observed'}] : [], metadata: {environment: binding.environment,
          bindingStatus: binding.bindingStatus}});
    });
    session.content.qualityObligations.filter((item) => item.qualityRef).forEach((item) => {
      const targetId = `contract-ref:${contractReferenceKey(item.qualityRef)}`;
      this.relationships.upsertNode({id: targetId, kind: 'contract.quality-obligation',
        reference: item.qualityRef, label: item.id, metadata: {severity: item.severity}});
      this.relationships.upsertEdge({id: `${contractNode}:quality:${item.id}`,
        from: contractNode, to: targetId, relation: 'governed_by_quality',
        origin: 'project_declared', confidence: 1, evidenceRefs: [], metadata: {}});
    });
    session.content.authoritativeDefinitions.forEach((item) => {
      const reference = item.assetRef ?? (item.uri ?
        {schema: 'cdeadmin.external-ref.v1', id: item.uri} : null);
      if(!reference) return;
      const targetId = `contract-ref:${contractReferenceKey(reference)}`;
      this.relationships.upsertNode({id: targetId, kind: 'contract.authoritative-definition',
        reference, label: item.id, metadata: {type: item.type}});
      this.relationships.upsertEdge({id: `${contractNode}:definition:${item.id}`,
        from: contractNode, to: targetId, relation: 'defined_by',
        origin: 'project_declared', confidence: 1, evidenceRefs: [], metadata: {type: item.type}});
    });
    session.content.elements.forEach((element) => {
      element.authoritativeDefinitionRefs.forEach((reference, index) => {
        const targetId = `contract-ref:${contractReferenceKey(reference)}`;
        this.relationships.upsertNode({id: targetId, kind: 'contract.semantic-reference',
          reference, label: reference.id ?? reference.assetId ?? reference.canonical,
          metadata: {}});
        this.relationships.upsertEdge({id:
          `${contractNode}:element-reference:${element.id}:${index}`,
        from: `${contractNode}:element:${element.id}`, to: targetId,
        relation: 'documented_by', origin: 'project_declared', confidence: 1,
        evidenceRefs: [], metadata: {}});
      });
    });
  }

  _clearRelationships(session, {keepRoot=false}={}) {
    const contractNode = `contract:${session.id}`;
    const snapshot = this.relationships.snapshot();
    const ownedEdges = snapshot.edges.filter((edge) => edge.id.startsWith(`${contractNode}:`));
    const possibleOrphans = new Set(ownedEdges.map((edge) => edge.to)
      .filter((id) => id.startsWith('contract-ref:')));
    ownedEdges.forEach((edge) => this.relationships.removeEdge(edge.id));
    snapshot.nodes.filter((node) => node.id.startsWith(`${contractNode}:element:`))
      .forEach((node) => this.relationships.removeNode(node.id));
    const remaining = this.relationships.snapshot();
    possibleOrphans.forEach((id) => {
      if(!remaining.edges.some((edge) => edge.from === id || edge.to === id)) {
        this.relationships.removeNode(id);
      }
    });
    if(!keepRoot) this.relationships.removeNode(contractNode);
  }

  async save(id, {projectId, assetId, name, path, expectedVersion}) {
    const session = this.sessions.get(String(id)); if(!session) return this.get(id);
    const saved = await this.projectAssets.saveAsset(projectId, assetId, contractAssetRequest({
      projectId, assetId, name, path, expectedVersion, content: session.content}));
    session.dirty = false; session.history.push({at: this.now(), action: 'save',
      assetId, version: saved.version}); this._touch(session); return saved;
  }

  reportError(id, error, code='internal_error') {
    const session = this.sessions.get(String(id)); if(!session) return;
    const states = {permission_denied: 'permission_denied', provider_unavailable: 'disconnected',
      capability_missing: 'partial', configuration_invalid: 'validation_error',
      external_format_invalid: 'validation_error', stale_result: 'stale'};
    if(code === 'task_cancelled') session.state = session.validation.valid ? 'ready' : 'validation_error';
    else session.state = states[code] ?? 'runtime_failure';
    session.error = error.message; session.activeTaskId = null;
    const diagnostic = {code, message: error.message, sessionId: id, at: this.now()};
    session.problems.push(diagnostic); this.diagnostics.report({id: `contract.${code}`,
      origin: CONTRACT_MODULE_ID, code, severity: 'error', message: error.message,
      suggestedCommandId: 'contract.validate', references: [{sessionId: id}]});
    this._touch(session);
  }
}
