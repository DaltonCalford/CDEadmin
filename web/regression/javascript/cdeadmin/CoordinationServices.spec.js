/////////////////////////////////////////////////////////////
// CDEadmin coordination platform-service tests.
/////////////////////////////////////////////////////////////

import {
  FederatedSearchService, PLATFORM_SERVICE_IDS, RelationshipGraphService,
  TASK_STATES, TaskExecutionService, corePlatformServiceDefinitions,
} from 'sources/cdeadmin_ui/platform/PlatformServices';

function deferred() {
  let resolve; const promise = new Promise((done) => { resolve = done; });
  return {promise, resolve};
}

describe('RelationshipGraphService', () => {
  it('supports dependency, impact, filters, updates and cascaded removal', () => {
    const graph = new RelationshipGraphService();
    graph.upsertNode({id: 'dashboard', kind: 'asset.dashboard'});
    graph.upsertNode({id: 'model', kind: 'asset.semantic-model'});
    graph.upsertNode({id: 'table', kind: 'resource.table'});
    graph.upsertEdge({id: 'uses', from: 'dashboard', to: 'model', relation: 'uses'});
    graph.upsertEdge({id: 'reads', from: 'model', to: 'table', relation: 'reads'});
    expect(graph.dependencies('dashboard').levels.flat().map((node) => node.id))
      .toEqual(['dashboard', 'model', 'table']);
    expect(graph.impact('table').levels.flat().map((node) => node.id))
      .toEqual(['table', 'model', 'dashboard']);
    expect(graph.neighbors('model', {direction: 'out', relation: 'reads'})).toHaveLength(1);
    expect(graph.neighbors('dashboard', {direction: 'out'})[0]).toEqual(
      expect.objectContaining({edgeId: 'uses', type: 'uses',
        origin: 'project_declared', evidenceRefs: [], attributes: {}})
    );
    expect(graph.neighbors('model', {direction: 'in'})).toHaveLength(1);
    graph.upsertNode({id: 'model', kind: 'asset.cube', label: 'Updated'});
    expect(graph.snapshot().nodes.find((node) => node.id === 'model').label).toBe('Updated');
    expect(graph.removeNode('model')).toBe(true); expect(graph.snapshot().edges).toEqual([]);
  });

  it('handles cycles and rejects dangling or unsafe relationships', () => {
    const graph = new RelationshipGraphService();
    graph.upsertNode({id: 'a', kind: 'asset.query'});
    graph.upsertNode({id: 'b', kind: 'asset.query'});
    graph.upsertEdge({id: 'ab', from: 'a', to: 'b', relation: 'uses'});
    graph.upsertEdge({id: 'ba', from: 'b', to: 'a', relation: 'uses'});
    expect(graph.dependencies('a').levels.flat()).toHaveLength(2);
    expect(() => graph.upsertEdge({id: 'bad', from: 'a', to: 'missing', relation: 'uses'}))
      .toThrow('endpoints must exist');
    expect(() => graph.upsertNode({id: 'secret', kind: 'asset.query',
      metadata: {password: 'x'}})).toThrow('Raw credential');
    expect(graph.removeEdge('ab')).toBe(true); expect(graph.removeNode('none')).toBe(false);
  });
});

describe('TaskExecutionService', () => {
  it('reports progress/logs and completes dependencies in order', async () => {
    const order = []; const service = new TaskExecutionService({now: () => 'now'});
    service.register('task.work', async (request, context) => {
      order.push(request.id); context.progress(0.5, 'half');
      context.log('info', 'working'); return request.id;
    });
    const events = []; service.subscribe((task) => events.push(`${task.id}:${task.state}`));
    const first = service.submit({id: 'first', type: 'task.work'});
    const second = service.submit({id: 'second', type: 'task.work', dependencies: ['first']});
    await service.wait(first.id); await service.wait(second.id);
    expect(order).toEqual(['first', 'second']);
    expect(service.view('first')).toEqual(expect.objectContaining({
      state: TASK_STATES.COMPLETED, progress: 1, result: 'first'}));
    expect(service.view('first').logs[0].message).toBe('working');
    expect(service.list({state: 'completed'})).toHaveLength(2);
    expect(events).toContain('first:running');
    expect(events).toContain('first:queued');
    expect(events).toContain('first:preparing');
  });

  it('retries exact failures and records terminal failure', async () => {
    let calls = 0; const service = new TaskExecutionService();
    service.register('task.retry', async () => {
      calls++; if(calls < 2) throw new Error('again'); return 'done';
    });
    const retried = service.submit({id: 'retry', type: 'task.retry', retry: {maximum: 1}});
    await expect(service.wait(retried.id)).resolves.toBe('done');
    expect(service.view('retry').retry.attempt).toBe(2);
    service.register('task.fail', async () => { throw new Error('terminal'); });
    const failed = service.submit({id: 'failed', type: 'task.fail'});
    await expect(service.wait(failed.id)).rejects.toThrow('terminal');
    expect(service.view('failed')).toEqual(expect.objectContaining({
      state: TASK_STATES.FAILED, error: 'terminal'}));
  });

  it('exposes phases, bindings, diagnostics, result refs, warnings and audit state',
    async () => {
      const service = new TaskExecutionService({now: () => 'now'});
      service.register('task.rich', async (_request, context) => {
        context.phase('catalog', 'Reading metadata');
        context.warning('Native property was not comparable');
        context.diagnostic({code: 'native_uncomparable', severity: 'warning'});
        context.resultReference({schema: 'result-ref', id: 'result-one'});
        return {result: true};
      });
      const task = service.submit({id: 'rich', type: 'task.rich',
        resourceRefs: [{canonical: 'resource:one'}], assetRefs: [{assetId: 'asset-one'}],
        audit: {category: 'metadata_read'}});
      await service.wait(task.id);
      expect(service.view(task.id)).toEqual(expect.objectContaining({
        state: TASK_STATES.SUCCEEDED_WITH_WARNINGS,
        phase: TASK_STATES.SUCCEEDED_WITH_WARNINGS,
        resourceRefs: [{canonical: 'resource:one'}], assetRefs: [{assetId: 'asset-one'}],
        diagnostics: [{code: 'native_uncomparable', severity: 'warning'}],
        resultRefs: [{schema: 'result-ref', id: 'result-one'}],
        cancelable: true, resumable: true, audit: {category: 'metadata_read'},
      }));
    });

  it('pauses, resumes and cancels cooperative tasks', async () => {
    const started = deferred(); const finish = deferred();
    const service = new TaskExecutionService();
    service.register('task.long', async (_request, context) => {
      started.resolve(); await finish.promise; await context.waitIfPaused();
      if(context.signal.aborted) throw new Error('aborted'); return 'ok';
    });
    const paused = service.submit({id: 'paused', type: 'task.long'});
    await started.promise; expect(service.pause(paused.id)).toBe(true);
    expect(service.view(paused.id).state).toBe(TASK_STATES.PAUSED);
    expect(service.resume(paused.id)).toBe(true); finish.resolve();
    await expect(service.wait(paused.id)).resolves.toBe('ok');

    const hold = deferred();
    service.register('task.cancel', async (_request, context) => {
      await hold.promise; if(context.signal.aborted) throw new Error('aborted');
    });
    const cancelled = service.submit({id: 'cancelled', type: 'task.cancel'});
    expect(service.cancel(cancelled.id)).toBe(true); hold.resolve();
    await expect(service.wait(cancelled.id)).rejects.toMatchObject({name: 'AbortError'});
    expect(service.view(cancelled.id).state).toBe(TASK_STATES.CANCELLED);
  });

  it('rejects unknown runners/dependencies/duplicate IDs/secrets/bad progress', async () => {
    const service = new TaskExecutionService();
    expect(() => service.submit({type: 'task.none'})).toThrow('No task runner');
    service.register('task.bad', async (_request, context) => context.progress(2));
    expect(() => service.submit({id: 'unsafe', type: 'task.bad', password: 'x'}))
      .toThrow('Raw credential');
    expect(() => service.submit({id: 'missing-dependency', type: 'task.bad',
      dependencies: ['none']})).toThrow('Unknown task dependency');
    const bad = service.submit({id: 'bad-progress', type: 'task.bad'});
    expect(() => service.submit({id: 'bad-progress', type: 'task.bad'})).toThrow('Task exists');
    await expect(service.wait(bad.id)).rejects.toThrow('between zero and one');
  });
});

describe('FederatedSearchService', () => {
  it('federates typed groups by priority and preserves provider errors', async () => {
    const service = new FederatedSearchService();
    service.register({id: 'search.assets', types: ['asset'], priority: 20,
      search: async () => [{id: 'a', type: 'asset', label: 'Query'}]});
    service.register({id: 'search.resources', types: ['resource'], priority: 10,
      search: async () => { throw new Error('catalog offline'); }});
    const result = await service.search('query');
    expect(result.groups.map((group) => group.providerId))
      .toEqual(['search.resources', 'search.assets']);
    expect(result.groups[0].error).toBe('catalog offline'); expect(result.total).toBe(1);
    expect((await service.search('')).groups).toEqual([]);
  });

  it('filters by type, caps results, rejects undeclared types and honours abort', async () => {
    const service = new FederatedSearchService(); const resources = jest.fn(async () => [
      {id: 'one', type: 'resource'}, {id: 'two', type: 'resource'}]);
    service.register({id: 'search.resources', types: ['resource'], search: resources});
    service.register({id: 'search.bad', types: ['asset'], search: async () => [
      {id: 'x', type: 'command'}]});
    const filtered = await service.search('x', {types: ['resource'], limit: 1});
    expect(filtered.total).toBe(1); expect(filtered.groups).toHaveLength(1);
    const bad = await service.search('x', {types: ['asset']});
    expect(bad.groups[0].error).toContain('undeclared type');
    const controller = new AbortController(); controller.abort();
    await expect(service.search('x', {signal: controller.signal})).rejects
      .toMatchObject({name: 'AbortError'});
  });
});

describe('core platform service definitions', () => {
  it('defines every controlling authority once with executable factories', () => {
    const definitions = corePlatformServiceDefinitions({storage: null});
    expect(definitions.map((definition) => definition.id).sort())
      .toEqual(Object.values(PLATFORM_SERVICE_IDS).sort());
    expect(new Set(definitions.map((definition) => definition.id)).size)
      .toBe(definitions.length);
    definitions.forEach((definition) => {
      expect(definition.version).toBe('1.0.0');
      expect(definition.factory).toEqual(expect.any(Function));
    });
  });
});
