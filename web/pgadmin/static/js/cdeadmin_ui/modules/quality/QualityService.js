/////////////////////////////////////////////////////////////
// Data Quality orchestration, provider evidence and runtime results.
/////////////////////////////////////////////////////////////

import {
  diagnosticsService, platformEventService, stablePlatformId,
} from '../../platform/PlatformRegistry';
import {
  abortError, immutable, noRawSecrets, plainObject, platformValue,
} from '../../platform/serviceUtils';
import {
  createQualityContent, qualityAssetRequest, qualityReferenceKey,
  QUALITY_RULE_FAMILIES, validateQualityRef,
  validateQualityRule,
} from './contracts';
import {
  acceptProfileSuggestion, captureQualityBaseline, compareBaseline,
  normalizeProfileSuggestions, normalizeRuleResult, summarizeRun,
} from './QualityEngine';
import {exportGXSuite, importGXSuite} from './GXAdapter';

export const QUALITY_SERVICE_ID = 'quality.runtime';
export const QUALITY_TASKS = Object.freeze([
  'quality.validation.run', 'quality.profile.run', 'quality.baseline.capture',
]);
export const QUALITY_EVENTS = Object.freeze([
  'quality.run.started', 'quality.rule.failed', 'quality.run.completed',
  'quality.baseline.changed',
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

function providerIdFor(slice) {
  const ref = slice?.resourceRef;
  return ref?.provider ?? ref?.providerId ?? ref?.provider_id ?? null;
}

function requireAdapter(adapter, providerId) {
  ['listSupportedRuleFamilies', 'prepareQualityExecution',
    'executeProviderNativeRule', 'profileDataSlice', 'fetchViolationSample']
    .forEach((method) => {
      if(typeof adapter?.[method] !== 'function') throw new TypeError(
        `Data Quality adapter ${providerId} requires ${method}().`
      );
    });
}

function stringList(input, field, providerId) {
  if(!Array.isArray(input[field])) throw new TypeError(
    `${providerId} Data Quality result requires ${field}[].`
  );
  return [...new Set(input[field].map((item) => platformValue(
    item, `${providerId} ${field}`
  )))].sort();
}

export function validateQualityProviderResult(input, providerId, operation,
  {allowEmpty=false}={}) {
  plainObject(input, `${providerId} ${operation} result`);
  noRawSecrets(input, `${providerId} ${operation} result`);
  const supportState = platformValue(input.supportState, 'Data Quality support state');
  if(!SUPPORT_STATES.includes(supportState)) throw new TypeError(
    `${providerId} returned invalid Data Quality support state.`
  );
  if(!Array.isArray(input.warnings)) throw new TypeError(
    `${providerId} Data Quality result requires warnings[].`
  );
  const nativeDetails = plainObject(input.nativeDetails, `${providerId} native details`);
  const result = immutable({supportState,
    providerVersion: platformValue(input.providerVersion, `${providerId} provider version`),
    evidence: immutable({...plainObject(input.evidence, `${providerId} runtime evidence`)}),
    warnings: [...input.warnings].map((item) => String(item)), nativeDetails,
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

export class QualityAdapterRegistry {
  constructor() { this.adapters = new Map(); }
  register(providerId, adapter) {
    providerId = stablePlatformId(providerId, 'Data Quality provider ID');
    requireAdapter(adapter, providerId);
    if(this.adapters.has(providerId)) throw new Error(
      `Data Quality adapter already registered: ${providerId}`
    );
    this.adapters.set(providerId, adapter); return () => this.adapters.delete(providerId);
  }
  get(providerId) {
    return this.adapters.get(stablePlatformId(providerId, 'Data Quality provider ID')) ?? null;
  }
  list() { return [...this.adapters.keys()].sort(); }
}

function emptyStatus(providerId, operation) {
  return immutable({providerId: providerId ?? 'unknown', supportState: 'unknown',
    providerVersion: null, evidence: {}, warnings: [
      'No Data Quality adapter response is registered.',
    ], nativeDetails: {}, readCapabilities: [], writeCapabilities: [],
    discoveryCapabilities: [], nativeMechanisms: [], versionConstraints: [],
    limitations: ['No Data Quality adapter response is registered.'],
    runtimeEvidence: null, operation, supportedRuleFamilies: []});
}

function sessionView(session) {
  return immutable({schema: 'cdeadmin.quality-session.v1', id: session.id,
    content: session.content, state: session.state, dirty: session.dirty,
    selectedRuleId: session.selectedRuleId, selectedRunId: session.selectedRunId,
    providerStatus: session.providerStatus, validation: session.validation,
    runs: [...session.runs], baselines: [...session.baselines.values()],
    profile: session.profile, failedSamples: [...session.failedSamples.entries()]
      .map(([resultId, sample]) => ({resultId, ...sample})),
    activeTaskId: session.activeTaskId, problems: [...session.problems],
    history: [...session.history], error: session.error,
    interoperability: session.interoperability,
    createdAt: session.createdAt, updatedAt: session.updatedAt});
}

export class QualityService {
  constructor({adapters=new QualityAdapterRegistry(), tasks, relationships, search,
    projectAssets, commands=null, events=platformEventService,
    diagnostics=diagnosticsService, now=() => new Date().toISOString()}={}) {
    if(!tasks || !relationships || !search || !projectAssets) throw new TypeError(
      'Data Quality requires task, relationship, search and project asset services.'
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
      this.tasks.register('quality.validation.run', runner('_validateRun')),
      this.tasks.register('quality.profile.run', runner('_profileRun')),
      this.tasks.register('quality.baseline.capture', runner('_baselineCapture')),
    );
  }

  _registerSearch() {
    this.disposers.push(this.search.register({id: 'quality.search', priority: 36,
      types: ['quality.asset', 'quality.resource', 'quality.rule', 'quality.run',
        'quality.baseline'],
      search: async (query, {context={}}={}) => {
        if(context.permissions && !context.permissions.includes('quality.view')) return [];
        const needle = query.toLowerCase(); const results = [];
        for(const session of this.sessions.values()) {
          if(`${session.id} ${session.content.name}`.toLowerCase().includes(needle)) {
            results.push({id: session.id, type: 'quality.asset', label: session.content.name ||
              session.id, context: 'Project Data Quality asset'});
          }
          session.content.rules.forEach((rule) => {
            if(`${rule.name} ${rule.type} ${rule.dimension}`.toLowerCase().includes(needle)) {
              results.push({id: rule.id, type: 'quality.rule', label: rule.name,
                context: `${session.id} · ${rule.dimension}`});
            }
          });
          if(session.content.defaultScope) {
            const reference = session.content.defaultScope.resourceRef;
            const label = reference.canonical ?? reference.id;
            if(String(label).toLowerCase().includes(needle)) results.push({
              id: qualityReferenceKey(reference), type: 'quality.resource', label,
              context: `${session.id} · live provider resource`, reference});
          }
          session.runs.forEach((run) => {
            if(run.id.toLowerCase().includes(needle)) results.push({id: run.id,
              type: 'quality.run', label: run.id, context: `${session.id} · ${run.summary.outcome}`});
          });
          session.baselines.forEach((baseline) => {
            if(baseline.id.toLowerCase().includes(needle)) results.push({id: baseline.id,
              type: 'quality.baseline', label: baseline.id, context: session.id});
          });
        }
        return results;
      }}));
  }

  dispose() { this.disposers.reverse().forEach((dispose) => dispose()); this.disposers = []; }
  subscribe(listener) { this.listeners.add(listener); return () => this.listeners.delete(listener); }
  _emit(session) { this.listeners.forEach((listener) => listener(sessionView(session))); }
  _touch(session, {dirty=session.dirty}={}) {
    session.updatedAt = this.now(); session.dirty = dirty; this._emit(session);
    return sessionView(session);
  }

  create(input={}) {
    plainObject(input, 'Data Quality session'); noRawSecrets(input, 'Data Quality session');
    const id = input.id ?? `quality-${++this.sequence}`;
    if(this.sessions.has(id)) throw new Error(`Data Quality session already exists: ${id}`);
    const content = createQualityContent(input.content ?? input);
    const time = this.now(); const session = {id, content,
      state: content.rules.length || content.defaultScope ? 'ready' : 'empty', dirty: false,
      selectedRuleId: null, selectedRunId: null, providerStatus: null,
      validation: {valid: false, details: []}, runs: [], baselines: new Map(), profile: null,
      failedSamples: new Map(), activeTaskId: null, problems: [], history: [], error: '',
      interoperability: null, createdAt: time, updatedAt: time};
    this.sessions.set(id, session); this._mirrorRelationships(session); this._emit(session);
    return sessionView(session);
  }

  get(id) {
    const session = this.sessions.get(String(id));
    if(!session) throw new Error(`Unknown Data Quality session: ${id}`);
    return sessionView(session);
  }
  list() { return [...this.sessions.values()].map(sessionView); }

  select(id, {ruleId=null, runId=null}={}) {
    const session = this.sessions.get(String(id)); if(!session) return this.get(id);
    if(ruleId && !session.content.rules.some((rule) => rule.id === ruleId)) throw new Error(
      `Unknown Data Quality rule: ${ruleId}`
    );
    if(runId && !session.runs.some((run) => run.id === runId)) throw new Error(
      `Unknown Data Quality run: ${runId}`
    );
    session.selectedRuleId = ruleId; session.selectedRunId = runId;
    return this._touch(session);
  }

  replaceDefinition(id, input) {
    const session = this.sessions.get(String(id)); if(!session) return this.get(id);
    session.content = createQualityContent(input); session.validation = this.validateDefinition(id);
    session.state = session.content.rules.length || session.content.defaultScope ? 'ready' : 'empty';
    this._mirrorRelationships(session); return this._touch(session, {dirty: true});
  }

  addRule(id, input) {
    const session = this.sessions.get(String(id)); if(!session) return this.get(id);
    let rule = validateQualityRule(input);
    if(rule.generatedSuggestion) rule = acceptProfileSuggestion(rule);
    if(session.content.rules.some((item) => item.id === rule.id)) throw new Error(
      `Data Quality rule already exists: ${rule.id}`
    );
    session.content = createQualityContent({...session.content,
      rules: [...session.content.rules, rule]});
    if(session.profile && input.generatedSuggestion) session.profile = immutable({
      ...session.profile,
      acceptedRuleIds: [...new Set([...session.profile.acceptedRuleIds, rule.id])].sort(),
    });
    session.selectedRuleId = rule.id; session.history.push({at: this.now(), action: 'rule.add',
      ruleId: rule.id}); this._mirrorRelationships(session);
    return this._touch(session, {dirty: true});
  }

  validateDefinition(id, {ruleId=null}={}) {
    const session = this.sessions.get(String(id)); if(!session) return this.get(id);
    const details = [];
    if(!session.content.defaultScope) details.push('A default data scope is required.');
    const rules = ruleId ? session.content.rules.filter((rule) => rule.id === ruleId) :
      session.content.rules.filter((rule) => rule.enabled);
    if(!rules.length) details.push('At least one enabled quality rule is required.');
    rules.forEach((rule) => rule.actionIds.forEach((actionId) => {
      if(!session.content.actions.some((action) => action.id === actionId)) {
        details.push(`Rule ${rule.id} references unknown action ${actionId}.`);
      }
    }));
    const value = immutable({valid: !details.length, details});
    session.validation = value; return value;
  }

  _task(session, type, label, extra={}, taskContext={}) {
    const task = this.tasks.submit({id: `${session.id}:${type}:${++this.sequence}`,
      type, label, sessionId: session.id,
      resourceRefs: session.content.defaultScope ?
        [session.content.defaultScope.resourceRef] : [], assetRefs: [], ...extra}, {
      owner: {moduleId: 'cdeadmin.quality', sessionId: session.id}, ...taskContext});
    session.activeTaskId = task.id; session.state = 'background_task_active'; this._touch(session);
    this.tasks.wait(task.id).then(() => {
      if(session.activeTaskId === task.id) {
        session.activeTaskId = null; session.state = this._settledState(session);
        session.error = ''; this._touch(session);
      }
    }).catch((error) => this.reportError(session.id, error,
      error.name === 'AbortError' ? 'task_cancelled' : 'task_failed'));
    return task;
  }

  _settledState(session) {
    const support = session.providerStatus?.supportState;
    if(['unknown', 'unsupported', 'partial'].includes(support)) return 'partial';
    if(support === 'read_only') return 'read_only';
    return session.content.rules.length || session.content.defaultScope ? 'ready' : 'empty';
  }

  run(id, {ruleId=null, retry}={}, commandContext={}) {
    const session = this.sessions.get(String(id)); if(!session) return this.get(id);
    const validation = this.validateDefinition(id, {ruleId});
    if(!validation.valid) throw new Error(`configuration_invalid: ${validation.details.join(' ')}`);
    return this._task(session, 'quality.validation.run', ruleId ? 'Test Data Quality rule' :
      'Run Data Quality rule set', {ruleId, retry}, commandContext);
  }

  async _adapterResult(session, method, args, context, {allowEmpty=false}={}) {
    const providerId = providerIdFor(session.content.defaultScope);
    const adapter = providerId ? this.adapters.get(providerId) : null;
    if(!adapter) {
      session.providerStatus = emptyStatus(providerId, method);
      throw new Error('capability_missing: Data Quality provider support is unknown.');
    }
    if(context.signal?.aborted) throw abortError();
    const result = validateQualityProviderResult(await adapter[method](...args), providerId,
      method, {allowEmpty});
    session.providerStatus = immutable({...result, providerId, value: undefined,
      operation: method});
    result.warnings.forEach((warning) => context.warning?.(warning, {providerId}));
    if(!result.supportState.startsWith('supported')) throw new Error(
      `capability_missing: ${providerId} reports ${result.supportState} for ${method}.`
    );
    return {adapter, providerId, result};
  }

  async _validateRun(id, context) {
    const session = this.sessions.get(String(id));
    const rules = (context.ruleId ? session.content.rules.filter((rule) =>
      rule.id === context.ruleId) : session.content.rules).filter((rule) => rule.enabled);
    const runId = `quality-run-${++this.sequence}`; const startedAt = this.now();
    await this.events.publish('quality.run.started', {sessionId: id, runId,
      ruleCount: rules.length}, {origin: 'cdeadmin.quality'});
    context.phase?.('preparing', 'Preparing provider-side quality execution');
    const providerId = providerIdFor(session.content.defaultScope);
    const adapter = providerId ? this.adapters.get(providerId) : null;
    if(!adapter) { session.providerStatus = emptyStatus(providerId, 'validation');
      throw new Error('capability_missing: Data Quality provider support is unknown.'); }
    const familyResponse = await this._adapterResult(session, 'listSupportedRuleFamilies',
      [session.content.defaultScope.resourceRef.kind ?? 'resource'], context, {allowEmpty: true});
    if(!Array.isArray(familyResponse.result.value)) throw new TypeError(
      `${providerId} rule-family discovery requires an array.`
    );
    if(!familyResponse.result.value.length && !String(
      familyResponse.result.nativeDetails.emptyReason ?? '').trim()) throw new TypeError(
      `${providerId} returned an empty rule-family list without nativeDetails.emptyReason.`
    );
    const families = new Set(familyResponse.result.value.map((family) => {
      if(!QUALITY_RULE_FAMILIES.includes(family)) throw new TypeError(
        `${providerId} returned unknown quality rule family: ${family}`
      );
      return family;
    }));
    session.providerStatus = immutable({...session.providerStatus,
      supportedRuleFamilies: [...families].sort()});
    const missing = rules.filter((rule) => !families.has(rule.type));
    if(missing.length) throw new Error(`capability_missing: Unsupported rule families: ${
      missing.map((rule) => rule.type).join(', ')}`);
    const prepared = await this._adapterResult(session, 'prepareQualityExecution',
      [{rules, parameters: session.content.parameters}, session.content.defaultScope], context);
    const results = [];
    for(const [index, rule] of rules.entries()) {
      if(context.signal?.aborted) throw abortError();
      context.phase?.('evaluating', `Evaluating ${rule.name}`);
      let result;
      try {
        const response = await this._adapterResult(session, 'executeProviderNativeRule',
          [{rule, execution: prepared.result.value,
            slice: rule.scope ?? session.content.defaultScope}], context);
        result = normalizeRuleResult(rule, response.result.value);
        if(result.violationSampleRef && context.currentUser?.permissions?.includes(
          'quality.view_samples'
        )) {
          const sample = await this._adapterResult(session, 'fetchViolationSample',
            [result, 100], context);
          const payload = plainObject(sample.result.value, 'Violation sample');
          if(!Array.isArray(payload.rows) || payload.rows.length > 100) throw new TypeError(
            'Violation sample must contain at most 100 rows.'
          );
          const total = Number(payload.total ?? payload.rows.length);
          if(!Number.isInteger(total) || total < payload.rows.length) throw new TypeError(
            'Violation sample total must be an integer no smaller than the returned rows.'
          );
          session.failedSamples.set(result.id, immutable({rows: payload.rows,
            sensitivityState: platformValue(payload.sensitivityState,
              'Sample sensitivity state'), total,
            bounded: true}));
        }
      } catch(error) {
        if(error.name === 'AbortError') throw error;
        result = normalizeRuleResult(rule, {id: `result:${runId}:${rule.id}`, status: 'error',
          error: error.message, evaluationMode: rule.evaluationMode,
          evaluatedCount: 0, violationCount: 0, diagnostics: {providerId}});
      }
      results.push(result);
      if(result.status !== 'pass') await this.events.publish('quality.rule.failed', {
        sessionId: id, runId, ruleId: rule.id, status: result.status,
        severity: rule.severity}, {origin: 'cdeadmin.quality'});
      context.progress?.((index + 1) / rules.length, `${index + 1}/${rules.length} rules`);
    }
    const summary = summarizeRun(results); const run = immutable({
      schema: 'cdeadmin.quality-run.v1', id: runId,
      taskRef: {schema: 'cdeadmin.task-ref.v1', id: context.id ?? `task:${runId}`},
      startedAt, finishedAt: this.now(), dataRevision: results.find(
        (result) => result.dataRevision)?.dataRevision ?? null,
      engine: {providerId, providerVersion: session.providerStatus.providerVersion},
      evaluationModes: [...new Set(results.map((result) => result.evaluationMode))].sort(),
      results, summary, actionResults: []});
    const actionResults = await this._executeActions(session, run, context);
    const completed = immutable({...run, actionResults}); session.runs.unshift(completed);
    session.selectedRunId = runId; session.history.push({at: this.now(), action: 'run', runId,
      outcome: summary.outcome});
    context.resultReference?.({schema: 'cdeadmin.result-ref.v1', id: runId});
    await this.events.publish('quality.run.completed', {sessionId: id, runId,
      outcome: summary.outcome, summary}, {origin: 'cdeadmin.quality'});
    this._touch(session); return completed;
  }

  async _executeActions(session, run, context) {
    const failedRuleIds = new Set(run.results.filter((result) => result.status !== 'pass')
      .map((result) => result.ruleId));
    const failedRules = session.content.rules.filter((rule) => failedRuleIds.has(rule.id));
    const actionIds = new Set(failedRules.flatMap((rule) => rule.actionIds));
    const results = [];
    for(const action of session.content.actions.filter((item) => item.enabled &&
      actionIds.has(item.id) && failedRules.some((rule) => rule.actionIds.includes(item.id) &&
        item.severities.includes(rule.severity)))) {
      if(action.type === 'call_command') {
        try {
          if(!this.commands) throw new Error('Command authority is unavailable.');
          await this.commands.execute(action.commandId, action.parameters, {
            currentUser: context.currentUser, qualityRun: run, scheduled: context.scheduled === true});
          results.push(immutable({actionId: action.id, state: 'executed'}));
        } catch(error) {
          results.push(immutable({actionId: action.id, state: 'blocked', reason: error.message}));
          context.diagnostic?.({code: 'permission_denied', message:
            `Quality action ${action.id} was blocked: ${error.message}`});
        }
      } else results.push(immutable({actionId: action.id,
        state: action.type === 'block' ? 'blocked_checkpoint' : 'recorded'}));
    }
    return immutable(results);
  }

  cancel(id, taskId) {
    const session = this.sessions.get(String(id)); if(!session) return this.get(id);
    if(!taskId || taskId !== session.activeTaskId) throw new Error(
      'configuration_invalid: The task is not active for this quality session.'
    );
    return this.tasks.cancel(taskId);
  }

  profile(id, {budget, retry}={}) {
    const session = this.sessions.get(String(id)); if(!session) return this.get(id);
    if(!session.content.defaultScope) throw new Error('configuration_invalid: Select a scope first.');
    if(!Number.isInteger(budget) || budget < 1 || budget > 1000000) throw new Error(
      'configuration_invalid: Profile budget must be an integer from 1 through 1000000.'
    );
    return this._task(session, 'quality.profile.run', 'Profile Data Quality scope',
      {budget, retry});
  }

  async _profileRun(id, context) {
    const session = this.sessions.get(String(id)); context.phase?.('profiling',
      'Profiling bounded data slice');
    const response = await this._adapterResult(session, 'profileDataSlice',
      [session.content.defaultScope, context.budget], context);
    const payload = plainObject(response.result.value, 'Quality profile');
    plainObject(payload.metrics, 'Quality profile metrics');
    const metrics = Object.fromEntries(Object.entries(payload.metrics).map(([name, value]) => {
      const number = Number(value);
      if(!Number.isFinite(number)) throw new TypeError(`Quality profile metric ${name} is invalid.`);
      return [platformValue(name, 'Quality profile metric name'), number];
    }));
    const sourceSampleRef = validateQualityRef(payload.sourceSampleRef, 'Profile sample reference');
    const sourceRevision = platformValue(payload.sourceRevision, 'Profile source revision');
    const suggestions = normalizeProfileSuggestions(payload.suggestions ?? [], {
      sourceSampleRef, sourceRevision});
    session.profile = immutable({schema: 'cdeadmin.quality-profile.v1',
      providerId: response.providerId, providerVersion: response.result.providerVersion,
      budget: context.budget, metrics: immutable(metrics),
      sourceSampleRef, sourceRevision, suggestions, acceptedRuleIds: []});
    context.progress?.(1, 'Profile complete'); this._touch(session); return session.profile;
  }

  captureBaseline(id, {baselineId, period={}, tolerances={}, retry}={}) {
    const session = this.sessions.get(String(id)); if(!session) return this.get(id);
    if(!session.content.defaultScope) throw new Error('configuration_invalid: Select a scope first.');
    if(baselineId && session.baselines.has(baselineId)) throw new Error(
      `conflict: Data Quality baseline already exists: ${baselineId}`
    );
    return this._task(session, 'quality.baseline.capture', 'Capture Data Quality baseline',
      {baselineId, period, tolerances, retry});
  }

  async _baselineCapture(id, context) {
    const session = this.sessions.get(String(id)); context.phase?.('profiling',
      'Collecting baseline metrics');
    const response = await this._adapterResult(session, 'profileDataSlice',
      [session.content.defaultScope, 1000000], context);
    const payload = plainObject(response.result.value, 'Baseline profile');
    const baseline = captureQualityBaseline({id: context.baselineId ??
      `baseline-${++this.sequence}`, metrics: payload.metrics,
    period: context.period, dataRevision: payload.sourceRevision,
    resourceRef: session.content.defaultScope.resourceRef, capturedAt: this.now()});
    const previous = [...session.baselines.values()].at(-1);
    const drift = previous ? compareBaseline(previous, baseline.metrics, context.tolerances) : null;
    session.baselines.set(baseline.id, immutable({...baseline, drift}));
    const ref = {schema: 'cdeadmin.external-ref.v1', id: `quality-baseline:${baseline.id}`};
    session.content = createQualityContent({...session.content,
      baselineRefs: [...session.content.baselineRefs, ref]});
    session.history.push({at: this.now(), action: 'baseline.capture', baselineId: baseline.id});
    await this.events.publish('quality.baseline.changed', {sessionId: id,
      baselineId: baseline.id, drifted: Boolean(drift?.drifted)}, {origin: 'cdeadmin.quality'});
    context.resultReference?.({schema: 'cdeadmin.result-ref.v1', id: baseline.id});
    context.progress?.(1, 'Baseline captured'); this._touch(session, {dirty: true});
    return session.baselines.get(baseline.id);
  }

  exportResult(id, {runId, includeSamples=false, format='json'}={}, currentUser={}) {
    const session = this.sessions.get(String(id)); if(!session) return this.get(id);
    const run = session.runs.find((item) => item.id === runId);
    if(!run) throw new Error(`configuration_invalid: Unknown Data Quality run: ${runId}`);
    if(!['json', 'csv'].includes(format)) throw new Error('external_format_invalid: Invalid format.');
    if(includeSamples && !currentUser.permissions?.includes('quality.export_samples')) {
      throw new Error('permission_denied: Failed-row export permission is required.');
    }
    const samples = includeSamples ? Object.fromEntries(run.results.map((result) => [result.id,
      session.failedSamples.get(result.id) ?? null])) : undefined;
    const payload = {schema: 'cdeadmin.quality-result-export.v1', profile: '1.0',
      contentClass: includeSamples ? 'live_rows' : 'metadata_only', run, ...(includeSamples ?
        {samples} : {})};
    noRawSecrets(payload, 'Data Quality export');
    if(format === 'json') return `${JSON.stringify(payload, null, 2)}\n`;
    const quote = (value) => `"${String(value ?? '').replaceAll('"', '""')}"`;
    if(includeSamples) return ['rule_id,sensitivity,row_json', ...run.results.flatMap((result) => {
      const sample = session.failedSamples.get(result.id); if(!sample) return [];
      return sample.rows.map((row) => [result.ruleId, sample.sensitivityState,
        JSON.stringify(row)].map(quote).join(','));
    })].join('\n') + '\n';
    return ['rule_id,status,severity,evaluation_mode,observed_metric,violations',
      ...run.results.map((result) => [result.ruleId, result.status, result.severity,
        result.evaluationMode, result.observedMetric, result.violationCount].map(quote).join(','))]
      .join('\n') + '\n';
  }

  importGX(id, suite) {
    const session = this.sessions.get(String(id)); if(!session) return this.get(id);
    const imported = importGXSuite(suite); session.interoperability = imported;
    session.content = createQualityContent({...session.content,
      rules: [...session.content.rules, ...imported.content.rules]});
    return this._touch(session, {dirty: true});
  }

  exportGX(id) {
    const session = this.sessions.get(String(id)); if(!session) return this.get(id);
    return exportGXSuite(session.content, session.interoperability ?? {});
  }

  syncContract(id, contractRef) {
    const session = this.sessions.get(String(id)); if(!session) return this.get(id);
    contractRef = validateQualityRef(contractRef, 'Data Contract reference');
    const source = `quality:${id}`; const target = `quality-contract:${qualityReferenceKey(contractRef)}`;
    this.relationships.upsertNode({id: source, kind: 'quality.asset', label: session.content.name || id});
    this.relationships.upsertNode({id: target, kind: 'contract.asset', label: qualityReferenceKey(
      contractRef), reference: contractRef});
    const edge = this.relationships.upsertEdge({id: `${source}:contract:${qualityReferenceKey(
      contractRef)}`, from: source, to: target, relation: 'validates', origin: 'project_declared',
    evidenceRefs: [{id: session.id, origin: 'project_declared'}],
    metadata: {moduleId: 'cdeadmin.quality'}});
    session.history.push({at: this.now(), action: 'contract.sync', contractRef});
    this._touch(session); return edge;
  }

  _mirrorRelationships(session) {
    const assetNode = `quality:${session.id}`;
    this.relationships.upsertNode({id: assetNode, kind: 'quality.asset',
      label: session.content.name || session.id});
    if(session.content.defaultScope) {
      const resourceNode = `quality-resource:${qualityReferenceKey(
        session.content.defaultScope.resourceRef)}`;
      this.relationships.upsertNode({id: resourceNode, kind: 'provider.resource',
        reference: session.content.defaultScope.resourceRef,
        label: qualityReferenceKey(session.content.defaultScope.resourceRef)});
      this.relationships.upsertEdge({id: `${assetNode}:validates:${resourceNode}`,
        from: assetNode, to: resourceNode, relation: 'validates', origin: 'project_declared',
        evidenceRefs: [{id: session.content.defaultScope.id, origin: 'project_declared'}],
        metadata: {moduleId: 'cdeadmin.quality'}});
    }
  }

  async save(id, {projectId, assetId, name, path, expectedVersion}) {
    const session = this.sessions.get(String(id)); if(!session) return this.get(id);
    const saved = await this.projectAssets.saveAsset(projectId, assetId, qualityAssetRequest({
      projectId, assetId, name, path, expectedVersion, content: session.content}));
    session.dirty = false; session.history.push({at: this.now(), action: 'save', assetId,
      version: saved.version}); this._touch(session); return saved;
  }

  reportError(id, error, code='internal_error') {
    const session = this.sessions.get(String(id)); if(!session) return;
    if(!ERROR_CODES.includes(code)) code = 'internal_error';
    const states = {permission_denied: 'permission_denied', provider_unavailable: 'disconnected',
      capability_missing: 'partial', configuration_invalid: 'validation_error',
      external_format_invalid: 'validation_error', partial_result: 'partial',
      stale_result: 'stale'};
    if(code === 'task_cancelled') session.state = this._settledState(session);
    else session.state = states[code] ?? 'runtime_failure';
    session.error = error.message; session.activeTaskId = null;
    const diagnostic = {code, message: error.message, sessionId: id, at: this.now()};
    session.problems.push(diagnostic);
    this.diagnostics.report({id: `quality.${code}`, origin: 'cdeadmin.quality', code,
      severity: 'error', message: error.message, suggestedCommandId: 'quality.ruleset.run',
      references: [{sessionId: id}]}); this._touch(session);
  }
}
