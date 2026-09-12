import {
  TracingAdapterRegistry, TracingService, validateTracingProviderResult,
} from 'sources/cdeadmin_ui/modules/tracing/TracingService';
import {
  FederatedSearchService, RelationshipGraphService, TaskExecutionService,
} from 'sources/cdeadmin_ui/platform/CoordinationServices';
import {PlatformEventService} from 'sources/cdeadmin_ui/platform/PlatformRegistry';
import {
  adapter, definition, providerResult, serviceFixture, source, trace,
} from './TracingTestUtils';

describe('Distributed Tracing service', () => {
  test('requires all four provider responsibilities and unique registration', () => {
    const registry = new TracingAdapterRegistry();
    expect(() => registry.register('bad', {})).toThrow('ingestNativeTrace');
    registry.register('postgresql', adapter());
    expect(() => registry.register('postgresql', adapter())).toThrow('already registered');
    expect(registry.list()).toEqual(['postgresql']);
  });

  test('strictly validates every support state and rejects empty success', () => {
    expect(validateTracingProviderResult(providerResult({}), 'postgresql', 'test'))
      .toMatchObject({supportState: 'supported_native'});
    expect(() => validateTracingProviderResult(providerResult(undefined),
      'postgresql', 'test')).toThrow('empty success');
    for(const supportState of ['supported_native', 'supported_via_cdeadmin',
      'supported_via_external_adapter', 'read_only', 'partial', 'unsupported', 'unknown']) {
      expect(validateTracingProviderResult(providerResult(undefined, {supportState}),
        'postgresql', 'test', {allowEmpty: true}).supportState).toBe(supportState);
    }
    expect(() => validateTracingProviderResult({...providerResult({}), warnings: undefined},
      'postgresql', 'test')).toThrow('warnings');
  });

  test('creates, edits and mirrors only authored source relationships', () => {
    const {service, relationships} = serviceFixture();
    expect(service.create({id: 'tracing-one'}).state).toBe('empty');
    const changed = service.replaceDefinition('tracing-one', definition(),
      {currentUser: {id: 'author'}});
    expect(changed).toMatchObject({state: 'ready', dirty: true});
    expect(relationships.snapshot().edges[0]).toMatchObject({type: 'ingests_from',
      origin: 'cdeadmin.tracing'});
    service.dispose(); expect(relationships.snapshot().edges).toEqual([]);
  });

  test('configures sources and policies as deterministic authored changes', () => {
    const {service} = serviceFixture(); service.create({id: 'tracing-one'});
    const configured = service.configureSource('tracing-one', source({id: 'internal',
      name: 'Internal', sourceType: 'cdeadmin_internal', providerId: null,
      resourceRef: null, credentialRef: null, sensitivityPolicyId: null}));
    expect(configured).toMatchObject({state: 'ready', dirty: true});
    const updated = service.updateSampling('tracing-one', {id: 'safe', name: 'Safe', rate: 0.5,
      tailCriteria: {}, sensitiveAttributePolicy: {}, retention: {}, enabled: true});
    expect(updated.content.samplingPolicies[0].rate).toBe(0.5);
  });

  test('ingests, correlates and redacts provider-native spans before storage', async () => {
    const events = new PlatformEventService(); const observed = [];
    events.subscribe('trace.ingested', (event) => observed.push(event));
    const fixture = serviceFixture({events}); fixture.service.create({
      id: 'tracing-one', content: definition()});
    const record = trace({spans: [{...trace().spans[0], attributes: {
      'db.statement': 'select private_data', safe: 'visible'}}]});
    const result = await fixture.service.ingest('tracing-one', 'provider-one', record);
    expect(result.trace.spans[0].attributes).toEqual({
      'db.statement': '[REDACTED]', safe: 'visible'});
    expect(fixture.calls.map((item) => item.name)).toEqual([
      'ingestNativeTrace', 'correlateDatabaseOperation', 'redactSensitiveAttributes']);
    expect(fixture.service.get('tracing-one').sourceCounters[0]).toMatchObject({
      accepted: 1, rejected: 0}); expect(observed).toHaveLength(1);
    expect(fixture.relationships.snapshot().edges.map((item) => item.type))
      .toEqual(expect.arrayContaining(['observed_correlation']));
  });

  test('marks failed ingestion degraded without claiming success', async () => {
    const broken = adapter(); broken.ingestNativeTrace = async () => {
      throw new Error('provider unavailable');
    };
    const fixture = serviceFixture({adapter: broken}); fixture.service.create({
      id: 'tracing-one', content: definition()});
    await expect(fixture.service.ingest('tracing-one', 'provider-one', trace()))
      .rejects.toThrow('provider unavailable');
    expect(fixture.service.get('tracing-one')).toMatchObject({state: 'partial',
      sourceCounters: [expect.objectContaining({accepted: 0, rejected: 1})]});
  });

  test('supports internal traces without requiring provider instrumentation', async () => {
    const fixture = serviceFixture(); fixture.service.create({id: 'internal-one', content: definition({
      sourceConfigs: [source({id: 'internal', name: 'Internal',
        sourceType: 'cdeadmin_internal', providerId: null, resourceRef: null,
        credentialRef: null})]})});
    const result = await fixture.service.ingest('internal-one', 'internal', trace({sourceId: 'internal'}));
    expect(result.trace.traceId).toBe(trace().traceId); expect(fixture.calls).toEqual([]);
  });

  test('applies deterministic sampling and records dropped counts', async () => {
    const fixture = serviceFixture(); fixture.service.create({id: 'tracing-one', content: definition({
      samplingPolicies: [{...definition().samplingPolicies[0], rate: 0,
        tailCriteria: {errors: true}}]})});
    const result = await fixture.service.ingest('tracing-one', 'provider-one', trace());
    expect(result).toMatchObject({trace: null, dropped: true});
    expect(fixture.service.get('tracing-one').sourceCounters[0]).toMatchObject({
      accepted: 0, dropped: 1});
  });

  test('searches bounded pages across internal and provider stores', async () => {
    const fixture = serviceFixture(); fixture.service.create({id: 'tracing-one', content: definition()});
    await fixture.service.ingest('tracing-one', 'provider-one', trace()); fixture.calls.splice(0);
    const result = await fixture.service.searchTraces('tracing-one', {filters: {status: 'OK'},
      pageSize: 1});
    expect(result.result.items).toHaveLength(1); expect(result.result.searchableRange.from).toBeTruthy();
    expect(fixture.calls.map((item) => item.name)).toEqual(['queryTraceStore']);
  });

  test('runs large search, service-map aggregation and export as shared tasks', async () => {
    const fixture = serviceFixture(); fixture.service.create({id: 'tracing-one', content: definition()});
    await fixture.service.ingest('tracing-one', 'provider-one', trace());
    const search = fixture.service.searchLarge('tracing-one', {pageSize: 100});
    await fixture.tasks.wait(search.activeTaskId);
    const map = fixture.service.aggregateMap('tracing-one', {from: trace().startTime, to: trace().endTime});
    const mapResult = await fixture.tasks.wait(map.activeTaskId);
    expect(mapResult.label).toMatch(/^Observed /);
    const exported = fixture.service.export('tracing-one');
    const output = await fixture.tasks.wait(exported.activeTaskId);
    expect(output).toMatchObject({profile: 'OTLP JSON 1.0', provenance: {traceCount: 1}});
  });

  test('opens exact IDs and resolves evidenced resource and query references', async () => {
    const fixture = serviceFixture(); fixture.service.create({id: 'tracing-one', content: definition()});
    await fixture.service.ingest('tracing-one', 'provider-one', trace());
    await fixture.service.searchTraces('tracing-one');
    expect(fixture.service.openTrace('tracing-one', trace().traceId)).toMatchObject({
      selectedTraceId: trace().traceId, selectedSpanId: trace().rootSpanId});
    expect(fixture.service.copyId('tracing-one', trace().traceId)).toEqual({
      kind: 'trace', value: trace().traceId});
    expect(fixture.service.relatedReference('tracing-one', trace().traceId,
      trace().rootSpanId, 'resource')).toMatchObject({schema: 'cdeadmin.resource-ref.v1'});
    expect(fixture.service.relatedReference('tracing-one', trace().traceId,
      trace().rootSpanId, 'query')).toMatchObject({schema: 'cdeadmin.query-ref.v1'});
  });

  test('expires only telemetry and retains authored project definitions', async () => {
    const fixture = serviceFixture(); const initial = fixture.service.create({
      id: 'tracing-one', content: definition()});
    await fixture.service.ingest('tracing-one', 'provider-one', trace());
    expect(fixture.service.expire('tracing-one', [trace().traceId]).removed).toEqual([trace().traceId]);
    expect(fixture.service.get('tracing-one').content).toEqual(initial.content);
  });

  test('persists optimistically and surfaces conflicts without changing authored state', async () => {
    const projectAssets = {update: jest.fn().mockResolvedValue({asset_id: 'trace-asset', version: 4}),
      create: jest.fn()}; const fixture = serviceFixture({projectAssets});
    fixture.service.create({id: 'tracing-one', content: definition()});
    fixture.service.updateSampling('tracing-one', definition().samplingPolicies[0]);
    await fixture.service.save('tracing-one', {projectId: 'project-one', assetId: 'trace-asset',
      expectedVersion: 3});
    expect(projectAssets.update).toHaveBeenCalledWith('project-one', 'trace-asset',
      expect.objectContaining({expected_version: 3}));
    expect(fixture.service.get('tracing-one').dirty).toBe(false);
  });

  test('preserves dirty authored state on optimistic concurrency conflict', async () => {
    const conflict = Object.assign(new Error('asset conflict'), {code: 'asset_conflict'});
    const projectAssets = {update: jest.fn().mockRejectedValue(conflict), create: jest.fn()};
    const fixture = serviceFixture({projectAssets}); fixture.service.create({
      id: 'tracing-one', content: definition()});
    fixture.service.updateSampling('tracing-one', {...definition().samplingPolicies[0], rate: 0.25});
    const before = fixture.service.get('tracing-one').content;
    await expect(fixture.service.save('tracing-one', {projectId: 'project-one',
      assetId: 'trace-asset', expectedVersion: 3})).rejects.toMatchObject({code: 'asset_conflict'});
    expect(fixture.service.get('tracing-one')).toMatchObject({dirty: true, content: before});
  });

  test('federated search separates commands, assets and live traces by type', async () => {
    const fixture = serviceFixture(); fixture.service.create({id: 'tracing-one', content: definition()});
    await fixture.service.ingest('tracing-one', 'provider-one', trace());
    const results = async (query, permissions=['trace.view']) => (await fixture.search.search(query,
      {context: {permissions}})).groups.flatMap((group) => group.results);
    expect((await results('search traces'))[0].type).toBe('tracing.command');
    expect((await results('reference tracing'))[0].type).toBe('tracing.asset');
    expect((await results(trace().traceId))[0].type).toBe('tracing.trace');
    expect(await results('reference', [])).toEqual([]);
  });

  test('fails closed without a provider adapter', async () => {
    const service = new TracingService({tasks: new TaskExecutionService(),
      relationships: new RelationshipGraphService(), search: new FederatedSearchService(),
      adapters: new TracingAdapterRegistry()});
    service.create({id: 'tracing-one', content: definition()});
    await expect(service.ingest('tracing-one', 'provider-one', trace()))
      .rejects.toThrow('no tracing adapter');
  });
});
