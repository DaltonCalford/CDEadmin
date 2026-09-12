/////////////////////////////////////////////////////////////
// Data Quality orchestration, provider, permission and task gates.
/////////////////////////////////////////////////////////////

import {
  DiagnosticsService, FederatedSearchService, PlatformEventService,
  RelationshipGraphService, TaskExecutionService,
} from 'sources/cdeadmin_ui/platform';
import {
  QualityAdapterRegistry, QualityService, validateQualityProviderResult,
} from 'sources/cdeadmin_ui/modules/quality';

const now = () => '2026-09-11T12:00:00Z';
const resource = (provider='firebird') => ({schema: 'cdeadmin.resource-ref.v1', provider,
  canonical: `cde-resource://${provider}/local/table/orders`, kind: 'table'});
const sampleRef = (id='violations') => ({schema: 'cdeadmin.external-ref.v1', id});
const scope = (provider='firebird') => ({id: 'orders-slice', resourceRef: resource(provider),
  samplePolicy: {mode: 'none_exact'}, nativeDetails: {database: 'demo.fdb'}});
const rule = (updates={}) => ({id: 'rule-one', name: 'Order ID present', type: 'not_null',
  dimension: 'completeness', parameters: {column: 'ID'}, threshold: null, ratio: false,
  severity: 'critical', evaluationMode: 'exact', enabled: true, actionIds: [],
  documentation: '', nativeDetails: {}, ...updates});

function result(value, updates={}) {
  return {supportState: 'supported_native', providerVersion: '5.0.4',
    evidence: {runtime: 'verified'}, warnings: [], nativeDetails: {},
    readCapabilities: ['profile', 'validate'], writeCapabilities: [],
    discoveryCapabilities: ['rule_families'], nativeMechanisms: ['system_catalog', 'sql'],
    versionConstraints: ['>=5.0'], limitations: [], runtimeEvidence: {live: true},
    value, ...updates};
}

function adapter() {
  return {
    listSupportedRuleFamilies: jest.fn(async () => result([
      'not_null', 'row_count', 'uniqueness', 'provider_native',
    ])),
    prepareQualityExecution: jest.fn(async () => result({executionId: 'exec-1'})),
    executeProviderNativeRule: jest.fn(async ({rule: current}) => result({
      id: `result:${current.id}`, passed: false, evaluationMode: current.evaluationMode,
      evaluatedCount: 10, violationCount: 1, dataRevision: 'txn:42',
      violationSampleRef: sampleRef(`sample:${current.id}`), diagnostics: {query: 'bounded'}})),
    profileDataSlice: jest.fn(async (_slice, budget) => result({metrics: {rows: 10, nulls: 1},
      sourceSampleRef: sampleRef(`profile:${budget}`), sourceRevision: 'txn:42', suggestions: [
        rule({id: 'suggested-row-count', name: 'Stable volume', type: 'row_count',
          dimension: 'volume', parameters: {minimum: 1}}),
      ]})),
    fetchViolationSample: jest.fn(async () => result({rows: [{ID: null}],
      sensitivityState: 'restricted', total: 1})),
  };
}

function harness({withAdapter=true, commands=null}={}) {
  const tasks = new TaskExecutionService({now});
  const relationships = new RelationshipGraphService();
  const search = new FederatedSearchService(); const events = new PlatformEventService();
  const diagnostics = new DiagnosticsService(); const adapters = new QualityAdapterRegistry();
  const native = adapter(); if(withAdapter) adapters.register('firebird', native);
  const projectAssets = {saveAsset: jest.fn(async () => ({version: 3}))};
  const service = new QualityService({tasks, relationships, search, events, diagnostics,
    adapters, projectAssets, commands, now});
  return {service, tasks, relationships, search, events, diagnostics, adapters,
    native, projectAssets};
}

function createSession(h, updates={}) {
  return h.service.create({name: 'Orders quality', defaultScope: scope(),
    rules: [rule()], actions: [], ...updates});
}

async function run(h, options={}, context={}) {
  const session = createSession(h); const task = h.service.run(session.id, options, context);
  const value = await h.tasks.wait(task.id); await Promise.resolve();
  return {task, value, session: h.service.get(session.id)};
}

describe('QualityAdapterRegistry and provider envelope', () => {
  it('requires all five normative provider methods and does not infer support', () => {
    const registry = new QualityAdapterRegistry();
    expect(() => registry.register('firebird', {})).toThrow('listSupportedRuleFamilies');
    expect(registry.get('firebird')).toBeNull();
    registry.register('firebird', adapter()); expect(registry.list()).toEqual(['firebird']);
    expect(() => registry.register('firebird', adapter())).toThrow('already registered');
  });

  it('requires the complete explicit provider result envelope and rejects empty success', () => {
    expect(() => validateQualityProviderResult({supportState: 'supported_native'},
      'firebird', 'profile')).toThrow('warnings[]');
    expect(() => validateQualityProviderResult(result(undefined), 'firebird', 'profile'))
      .toThrow('empty success');
    expect(validateQualityProviderResult(result(null), 'firebird', 'profile'))
      .toMatchObject({supportState: 'supported_native', value: null});
    expect(() => validateQualityProviderResult(result({}, {nativeDetails: {password: 'bad'}}),
      'firebird', 'profile')).toThrow('Raw credential');
  });
});

describe('QualityService', () => {
  it('creates, validates, selects and edits complete rule sets with relationship bindings', () => {
    const h = harness(); let session = createSession(h);
    expect(h.service.validateDefinition(session.id)).toEqual({valid: true, details: []});
    session = h.service.addRule(session.id, rule({id: 'rule-two', name: 'Rows exist',
      type: 'row_count', dimension: 'volume'}));
    expect(session).toMatchObject({dirty: true, selectedRuleId: 'rule-two'});
    expect(() => h.service.select(session.id, {ruleId: 'unknown'})).toThrow('Unknown');
    expect(h.relationships.snapshot().edges).toEqual(expect.arrayContaining([
      expect.objectContaining({relation: 'validates', origin: 'project_declared'}),
    ]));
  });

  it('runs provider-native rules, retains exact evidence and separates failed samples', async () => {
    const h = harness(); const started = []; const failed = []; const completed = [];
    h.events.subscribe('quality.run.started', (event) => started.push(event));
    h.events.subscribe('quality.rule.failed', (event) => failed.push(event));
    h.events.subscribe('quality.run.completed', (event) => completed.push(event));
    const completedRun = await run(h, {}, {currentUser: {permissions: ['quality.view_samples']}});
    expect(completedRun.value).toMatchObject({summary: {outcome: 'fail',
      counts: {pass: 0, fail: 1, error: 0}}, engine: {providerId: 'firebird'},
    evaluationModes: ['exact']});
    expect(completedRun.session.failedSamples[0]).toMatchObject({bounded: true,
      sensitivityState: 'restricted', rows: [{ID: null}]});
    expect(h.native.prepareQualityExecution).toHaveBeenCalledTimes(1);
    expect(h.native.executeProviderNativeRule).toHaveBeenCalledTimes(1);
    expect(h.native.fetchViolationSample).toHaveBeenCalledWith(expect.objectContaining({
      status: 'fail'}), 100);
    expect(started).toHaveLength(1); expect(failed).toHaveLength(1);
    expect(completed).toHaveLength(1);
    expect(h.tasks.view(completedRun.task.id).resultRefs[0].id).toContain('quality-run');
  });

  it('does not fetch protected failed rows without view permission', async () => {
    const h = harness(); const completedRun = await run(h);
    expect(completedRun.session.failedSamples).toEqual([]);
    expect(h.native.fetchViolationSample).not.toHaveBeenCalled();
  });

  it('executes exact SQL not-null/uniqueness and labelled provider-native rules independently',
    async () => {
      const h = harness(); const session = createSession(h, {rules: [rule(), rule({
        id: 'unique-id', name: 'Order ID unique', type: 'uniqueness',
        dimension: 'uniqueness'}), rule({id: 'native-check', name: 'Firebird native check',
        type: 'provider_native', dimension: 'custom', evaluationMode: 'provider_reported',
        nativeDetails: {mechanism: 'RDB$VALID_BLR'}})]});
      const task = h.service.run(session.id); const completed = await h.tasks.wait(task.id);
      expect(completed.results.map((item) => [item.ruleId, item.evaluationMode])).toEqual([
        ['native-check', 'provider_reported'], ['rule-one', 'exact'], ['unique-id', 'exact'],
      ]);
      expect(h.native.executeProviderNativeRule).toHaveBeenCalledTimes(3);
      expect(h.native.executeProviderNativeRule.mock.calls[0][0].rule).toMatchObject({
        type: 'provider_native', nativeDetails: {mechanism: 'RDB$VALID_BLR'}});
    });

  it('turns an individual provider rule failure into an error result, not a false failure', async () => {
    const h = harness(); h.native.executeProviderNativeRule.mockRejectedValueOnce(
      new Error('query timeout'));
    const completedRun = await run(h);
    expect(completedRun.value.summary).toMatchObject({outcome: 'error',
      counts: {pass: 0, fail: 0, error: 1}});
    expect(completedRun.value.results[0]).toMatchObject({status: 'error', error: 'query timeout'});
  });

  it('rejects unknown and unexplained empty capabilities without pretending unsupported', async () => {
    const missing = harness({withAdapter: false}); const missingSession = createSession(missing, {
      defaultScope: scope('unregistered')});
    const missingTask = missing.service.run(missingSession.id);
    await expect(missing.tasks.wait(missingTask.id)).rejects.toThrow('capability_missing');
    await Promise.resolve();
    expect(missing.service.get(missingSession.id).providerStatus.supportState).toBe('unknown');

    const empty = harness(); empty.native.listSupportedRuleFamilies.mockResolvedValueOnce(
      result([])); const emptySession = createSession(empty); const emptyTask = empty.service.run(
      emptySession.id);
    await expect(empty.tasks.wait(emptyTask.id)).rejects.toThrow('empty rule-family');
  });

  it('honors explicit unsupported families and allows explained empty discovery', async () => {
    const h = harness(); h.native.listSupportedRuleFamilies.mockResolvedValueOnce(result([], {
      nativeDetails: {emptyReason: 'This resource exposes no supported rule families'}}));
    const session = createSession(h); const task = h.service.run(session.id);
    await expect(h.tasks.wait(task.id)).rejects.toThrow('Unsupported rule families: not_null');
    expect(h.service.get(session.id).providerStatus.supportedRuleFamilies).toEqual([]);
  });

  it('retries transient discovery failures and supports shared-task cancellation', async () => {
    const h = harness(); h.native.listSupportedRuleFamilies.mockRejectedValueOnce(
      new Error('temporary catalog lock'));
    const session = createSession(h); const task = h.service.run(session.id,
      {retry: {maximum: 1}});
    await expect(h.tasks.wait(task.id)).resolves.toMatchObject({summary: {outcome: 'fail'}});
    expect(h.native.listSupportedRuleFamilies).toHaveBeenCalledTimes(2);

    const next = createSession(h, {id: 'cancel-quality'}); const cancelled = h.service.run(next.id);
    expect(h.service.cancel(next.id, cancelled.id)).toBe(true);
    await expect(h.tasks.wait(cancelled.id)).rejects.toMatchObject({name: 'AbortError'});
    expect(() => h.service.cancel(next.id, 'other-task')).toThrow('not active');
  });

  it('profiles bounded data, produces evidenced suggestions and accepts only explicit choices', async () => {
    const h = harness(); const session = createSession(h); const task = h.service.profile(session.id,
      {budget: 250}); const profile = await h.tasks.wait(task.id);
    expect(profile).toMatchObject({budget: 250, sourceRevision: 'txn:42',
      metrics: {rows: 10, nulls: 1}});
    expect(profile.suggestions[0]).toMatchObject({generatedSuggestion: true,
      sourceSampleRef: sampleRef('profile:250')});
    expect(h.service.get(session.id).content.rules).toHaveLength(1);
    const updated = h.service.addRule(session.id, profile.suggestions[0]);
    expect(updated.content.rules.find((item) => item.id === 'suggested-row-count'))
      .toMatchObject({generatedSuggestion: false, nativeDetails: {acceptedSuggestion: true}});
    expect(updated.profile.acceptedRuleIds).toEqual(['suggested-row-count']);
    expect(() => h.service.profile(session.id, {budget: 0})).toThrow('Profile budget');
  });

  it('rejects malformed profile metrics and unbounded sample metadata', async () => {
    const badProfile = harness(); badProfile.native.profileDataSlice.mockResolvedValueOnce(result({
      metrics: {rows: Infinity}, sourceSampleRef: sampleRef(), sourceRevision: 'txn',
      suggestions: []}));
    let session = createSession(badProfile); let task = badProfile.service.profile(session.id,
      {budget: 10});
    await expect(badProfile.tasks.wait(task.id)).rejects.toThrow('metric rows is invalid');

    const badSample = harness(); badSample.native.fetchViolationSample.mockResolvedValueOnce(result({
      rows: [{ID: null}, {ID: null}], sensitivityState: 'restricted', total: 1}));
    session = createSession(badSample); task = badSample.service.run(session.id, {}, {
      currentUser: {permissions: ['quality.view_samples']}});
    const completed = await badSample.tasks.wait(task.id);
    expect(completed.results[0]).toMatchObject({status: 'error',
      error: expect.stringContaining('no smaller')});
  });

  it('captures immutable baselines, compares drift and emits change evidence', async () => {
    const h = harness(); const changes = [];
    h.events.subscribe('quality.baseline.changed', (event) => changes.push(event));
    const session = createSession(h);
    let task = h.service.captureBaseline(session.id, {baselineId: 'base-one'});
    await h.tasks.wait(task.id);
    h.native.profileDataSlice.mockResolvedValueOnce(result({metrics: {rows: 12, nulls: 1},
      sourceSampleRef: sampleRef('profile:next'), sourceRevision: 'txn:43', suggestions: []}));
    task = h.service.captureBaseline(session.id, {baselineId: 'base-two',
      tolerances: {rows: 0.1, default: 0}}); const second = await h.tasks.wait(task.id);
    expect(second.drift).toMatchObject({drifted: true, baselineId: 'base-one'});
    expect(h.service.get(session.id)).toMatchObject({dirty: true,
      content: {baselineRefs: [sampleRef('quality-baseline:base-one'),
        sampleRef('quality-baseline:base-two')]}});
    expect(changes).toHaveLength(2);
    expect(() => h.service.captureBaseline(session.id, {baselineId: 'base-two'}))
      .toThrow('already exists');
  });

  it('executes command actions through command authority and records blocked operations', async () => {
    const commands = {execute: jest.fn().mockResolvedValue({sent: true})}; const h = harness({commands});
    const actions = [{id: 'notify', type: 'call_command', label: 'Notify', severities: ['critical'],
      enabled: true, commandId: 'notification.send', parameters: {channel: 'quality'}}];
    let session = createSession(h, {actions, rules: [rule({actionIds: ['notify']})]});
    let task = h.service.run(session.id, {}, {currentUser: {permissions: ['quality.execute']}});
    let completed = await h.tasks.wait(task.id);
    expect(commands.execute).toHaveBeenCalledWith('notification.send', {channel: 'quality'},
      expect.objectContaining({qualityRun: expect.any(Object)}));
    expect(completed.actionResults).toEqual([{actionId: 'notify', state: 'executed'}]);

    commands.execute.mockRejectedValueOnce(new Error('permission denied'));
    task = h.service.run(session.id, {}, {scheduled: true}); completed = await h.tasks.wait(task.id);
    expect(completed.actionResults[0]).toMatchObject({state: 'blocked',
      reason: 'permission denied'});
    expect(commands.execute.mock.calls.at(-1)[2].scheduled).toBe(true);

    session = createSession(h, {id: 'warning-rule', actions,
      rules: [rule({severity: 'warning', actionIds: ['notify']})]});
    task = h.service.run(session.id); completed = await h.tasks.wait(task.id);
    expect(completed.actionResults).toEqual([]);
  });

  it('exports metadata and protected samples with distinct permission checks', async () => {
    const h = harness(); const completedRun = await run(h, {}, {
      currentUser: {permissions: ['quality.view_samples']}});
    const id = completedRun.session.id; const runId = completedRun.value.id;
    expect(JSON.parse(h.service.exportResult(id, {runId}))).toMatchObject({
      contentClass: 'metadata_only'});
    expect(h.service.exportResult(id, {runId, format: 'csv'})).toContain('rule_id,status');
    expect(() => h.service.exportResult(id, {runId, includeSamples: true},
      {permissions: ['quality.view']})).toThrow('permission_denied');
    expect(JSON.parse(h.service.exportResult(id, {runId, includeSamples: true},
      {permissions: ['quality.export_samples']}))).toMatchObject({contentClass: 'live_rows',
      samples: expect.any(Object)});
    expect(h.service.exportResult(id, {runId, includeSamples: true, format: 'csv'},
      {permissions: ['quality.export_samples']})).toContain('rule_id,sensitivity,row_json');
  });

  it('round-trips Great Expectations, links contracts, saves and indexes search', async () => {
    const h = harness(); const session = createSession(h);
    h.service.importGX(session.id, {expectation_suite_name: 'imported', expectations: [{
      expectation_type: 'expect_column_values_to_be_unique', kwargs: {column: 'ID'}}],
    meta: {great_expectations_version: '1.7.1'}});
    expect(h.service.exportGX(session.id).expectations).toHaveLength(2);
    const contract = {schema: 'cdeadmin.asset-ref.v1', projectId: 'project', assetId: 'contract'};
    expect(h.service.syncContract(session.id, contract)).toMatchObject({relation: 'validates'});
    await h.service.save(session.id, {projectId: 'project', assetId: 'quality', name: 'Quality',
      path: 'quality/orders.json', expectedVersion: 2});
    expect(h.projectAssets.saveAsset).toHaveBeenCalledWith('project', 'quality',
      expect.objectContaining({asset_type: 'cdeadmin.quality.v1', expected_version: 2}));
    expect(h.service.get(session.id).dirty).toBe(false);
    expect((await h.search.search('Order ID')).total).toBeGreaterThan(0);
    expect((await h.search.search('cde-resource://firebird')).groups[0].results[0])
      .toMatchObject({type: 'quality.resource', reference: resource()});
    expect((await h.search.search('Order ID', {context: {permissions: []}})).total).toBe(0);
  });

  it('preserves authored state and emits durable normalized diagnostics on failure', () => {
    const h = harness(); const session = createSession(h);
    h.service.reportError(session.id, new Error('provider offline'), 'provider_unavailable');
    expect(h.service.get(session.id)).toMatchObject({state: 'disconnected',
      content: session.content, error: 'provider offline'});
    expect(h.diagnostics.list({origin: 'cdeadmin.quality'})[0])
      .toMatchObject({code: 'provider_unavailable'});
  });
});
