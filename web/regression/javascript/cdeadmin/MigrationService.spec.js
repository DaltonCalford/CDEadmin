import {
  MigrationAdapterRegistry, MigrationService, validateMigrationProviderResult,
} from 'sources/cdeadmin_ui/modules/migration/MigrationService';
import {
  FederatedSearchService, RelationshipGraphService, TaskExecutionService,
} from 'sources/cdeadmin_ui/platform/CoordinationServices';
import {PlatformEventService} from 'sources/cdeadmin_ui/platform/PlatformRegistry';
import {adapter, assessment, definition, fixture, providerResult, user} from './MigrationTestUtils';

describe('Migration service', () => {
  test('admits only complete exact provider adapters and evidence envelopes', () => {
    const registry = new MigrationAdapterRegistry();
    expect(() => registry.register('partial', {assessSource() {}})).toThrow('proposeTypeMappings');
    expect(() => validateMigrationProviderResult({supportState: 'supported_native'},
      'provider', 'assessSource')).toThrow('warnings');
    expect(() => validateMigrationProviderResult({...providerResult({ok: true}), guessed: true},
      'provider', 'assessSource')).toThrow('unsupported field guessed');
    expect(validateMigrationProviderResult(providerResult({ok: true}), 'provider', 'operation'))
      .toMatchObject({supportState: 'supported_native', providerVersion: 'test-1'});
  });

  test('runs assessment and retains exact evidence without inferred support', async () => {
    const events = {publish: jest.fn()}; const {service, tasks, calls} = fixture({events});
    const session = service.create({id: 'migration-one', content: definition()});
    const task = service.assess(session.id, {}, {currentUser: user()}); await tasks.wait(task.id);
    const current = service.get(session.id);
    expect(current.content.assessment.id).toBe('assessment-one');
    expect(current.content.mappingSet).toContainEqual(expect.objectContaining({
      id: 'integer-proposal', decision: 'unresolved'}));
    expect(calls.map((item) => item.name)).toContain('assessSource');
    expect(calls.map((item) => item.name)).toContain('proposeTypeMappings');
    expect(events.publish).toHaveBeenCalledWith('migration.assessment.completed',
      expect.objectContaining({sessionId: session.id}), expect.any(Object));
    expect(current.tasks[0]).toMatchObject({type: 'migration.assessment', state: 'succeeded'});
  });

  test('keeps unknown provider response distinct and fails closed', async () => {
    const tasks = new TaskExecutionService(); const relationships = new RelationshipGraphService();
    const search = new FederatedSearchService(); const service = new MigrationService({tasks,
      relationships, search, adapters: new MigrationAdapterRegistry()});
    const session = service.create({content: definition()}); const task = service.assess(session.id);
    await expect(tasks.wait(task.id)).rejects.toThrow('unknown');
    expect(service.get(session.id).providerStatuses[0].supportState).toBe('unknown');
  });

  test('dry run prepares every operation and performs no mutation', async () => {
    const {service, tasks, calls} = fixture(); const session = service.create({content: definition()});
    const task = service.dryRun(session.id, {}, {currentUser: user()});
    const result = await tasks.wait(task.id);
    expect(result).toMatchObject({mutated: false});
    expect(calls.filter((item) => item.name === 'prepareSchemaOperation')).toHaveLength(1);
    expect(calls.filter((item) => item.name.startsWith('execute'))).toEqual([]);
  });

  test('copy commits checkpoints and subsequent runs resume from them', async () => {
    const {service, tasks, calls} = fixture(); const session = service.create({content: definition()});
    service.recordIntegrationState(session.id, {schemaApplied: true, schemaValidated: true});
    let task = service.startCopy(session.id, {}, {currentUser: user()}); await tasks.wait(task.id);
    expect(service.get(session.id).runtime.checkpoints[0]).toMatchObject({
      unitId: 'orders-copy', state: 'committed', rowDocumentCount: 2});
    task = service.startCopy(session.id, {}, {currentUser: user()}); await tasks.wait(task.id);
    const preparations = calls.filter((item) => item.name === 'prepareCopyUnit');
    expect(preparations[1].input.resumeCheckpoint).toMatchObject({state: 'committed'});
  });

  test('rediscovers the durable committed checkpoint after a runtime restart', async () => {
    const calls = []; const durable = adapter(calls);
    let runtime = fixture({calls, targetAdapter: durable});
    let session = runtime.service.create({content: definition()});
    runtime.service.recordIntegrationState(session.id, {schemaApplied: true, schemaValidated: true});
    let task = runtime.service.startCopy(session.id); await runtime.tasks.wait(task.id);
    runtime.service.dispose();
    runtime = fixture({calls, targetAdapter: durable});
    session = runtime.service.create({content: definition()});
    runtime.service.recordIntegrationState(session.id, {schemaApplied: true, schemaValidated: true});
    task = runtime.service.startCopy(session.id); await runtime.tasks.wait(task.id);
    const execution = calls.filter((item) => item.name === 'executeCopyUnit').at(-1);
    expect(execution.input.resumeCheckpoint).toMatchObject({state: 'committed',
      unitId: 'orders-copy'});
  });

  test('refuses copy providers without durable checkpoint evidence', async () => {
    const target = adapter([]);
    target.prepareCopyUnit = async () => providerResult({prepared: true,
      resumeCheckpoint: null}, {runtimeEvidence: {checkpointLookup: {durable: false}}});
    const {service, tasks} = fixture({targetAdapter: target});
    const session = service.create({content: definition()});
    service.recordIntegrationState(session.id, {schemaApplied: true, schemaValidated: true});
    const task = service.startCopy(session.id);
    await expect(tasks.wait(task.id)).rejects.toThrow('durable checkpoint recovery');
  });

  test('honors bounded copy concurrency and returns deterministic unit order', async () => {
    let active = 0; let maximum = 0; const target = adapter([]);
    target.executeCopyUnit = async ({unit}) => {
      active++; maximum = Math.max(maximum, active);
      await new Promise((resolve) => setTimeout(resolve, unit.id === 'copy-b' ? 8 : 3));
      active--;
      return providerResult({checkpoint: {id: `cp-${unit.id}`, unitId: unit.id,
        state: 'committed', sourceRevision: 'source-rev-1', sourceBoundary: {},
        lastCompleted: {id: 2}, targetProgress: {id: 2}, rowDocumentCount: 2,
        byteCount: 128, retryState: {}, committedAt: 'now', nativeDetails: {}}});
    };
    const base = definition().dataMovePlan.units[0];
    const content = definition({dataMovePlan: {...definition().dataMovePlan, concurrency: 2,
      units: ['copy-a', 'copy-b', 'copy-c'].map((id) => ({...base, id}))}});
    const {service, tasks} = fixture({targetAdapter: target}); const session = service.create({content});
    service.recordIntegrationState(session.id, {schemaApplied: true, schemaValidated: true});
    const task = service.startCopy(session.id); const result = await tasks.wait(task.id);
    expect(maximum).toBe(2); expect(result.results.map((item) => item.unitId))
      .toEqual(['copy-a', 'copy-b', 'copy-c']);
  });

  test('preserves the last committed checkpoint on retry failure', async () => {
    const calls = []; const target = adapter(calls); let attempts = 0;
    target.executeCopyUnit = async (input) => {
      calls.push({name: 'executeCopyUnit', input}); attempts++;
      if(attempts === 2) throw new Error('target unavailable');
      return providerResult({checkpoint: {id: 'cp', unitId: input.unit.id, state: 'committed',
        sourceRevision: 'source-rev-1', sourceBoundary: {}, lastCompleted: {id: 2},
        targetProgress: {id: 2}, rowDocumentCount: 2, byteCount: 128, retryState: {},
        committedAt: 'now', nativeDetails: {}}});
    };
    const {service, tasks} = fixture({calls, targetAdapter: target});
    const session = service.create({content: definition()});
    service.recordIntegrationState(session.id, {schemaApplied: true, schemaValidated: true});
    let task = service.startCopy(session.id); await tasks.wait(task.id);
    task = service.startCopy(session.id); await expect(tasks.wait(task.id)).rejects.toThrow(
      'target unavailable');
    expect(service.get(session.id).runtime.checkpoints[0].lastCompleted).toEqual({id: 2});
  });

  test('arms, executes, verifies and completes an exact reviewed cutover', async () => {
    const events = {publish: jest.fn()}; const {service, tasks} = fixture({events});
    const session = service.create({content: definition()});
    service.recordIntegrationState(session.id, {schemaApplied: true, schemaValidated: true,
      initialCopyComplete: true});
    let task = service.verify(session.id, {}, {currentUser: user()}); await tasks.wait(task.id);
    task = service.armCutover(session.id, {confirmationRef: 'approval-one',
      environment: 'production', connection: 'target-one'}, {currentUser: user()});
    await tasks.wait(task.id); expect(service.get(session.id).arm).toMatchObject({
      confirmationRef: 'approval-one', environment: 'production'});
    task = service.executeCutover(session.id, {}, {currentUser: user()}); await tasks.wait(task.id);
    expect(service.get(session.id).content.phase).toBe('verify');
    task = service.verify(session.id, {}, {currentUser: user()}); await tasks.wait(task.id);
    expect(service.get(session.id).content.phase).toBe('complete');
    expect(events.publish).toHaveBeenCalledWith('migration.completed', expect.any(Object),
      expect.any(Object));
  });

  test('invalidates an arm when the reviewed plan changes', async () => {
    const {service, tasks} = fixture(); const session = service.create({content: definition()});
    service.recordIntegrationState(session.id, {schemaApplied: true, schemaValidated: true,
      initialCopyComplete: true});
    let task = service.verify(session.id); await tasks.wait(task.id);
    task = service.armCutover(session.id, {confirmationRef: 'approval-one',
      environment: 'production', connection: 'target-one'}); await tasks.wait(task.id);
    service.replaceDefinition(session.id, definition({cutoverPlan: {...definition().cutoverPlan,
      endpointChange: {route: 'target-two'}}}));
    expect(service.get(session.id).arm).toBeNull();
  });

  test('executes rollback only while the explicit classification permits', async () => {
    const {service, tasks, calls} = fixture(); const session = service.create({content: definition({
      phase: 'cutover_ready'})});
    const task = service.executeRollback(session.id, {}, {currentUser: user()});
    await tasks.wait(task.id); expect(service.get(session.id).content.phase).toBe('rollback');
    expect(calls.map((item) => item.name)).toContain('executeRollbackStep');
  });

  test('mirrors resource and module-asset relationships with migration origin', () => {
    const {service, relationships} = fixture(); service.create({id: 'm', content: definition()});
    const snapshot = relationships.snapshot();
    expect(snapshot.edges.some((item) => item.origin === 'cdeadmin.migration' &&
      item.relation === 'source')).toBe(true);
    expect(snapshot.edges.some((item) => item.relation === 'schema_plan')).toBe(true);
  });

  test('coordinates exact linked module sessions through platform events', async () => {
    const events = new PlatformEventService(); const {service} = fixture({events});
    const transformRef = {schema: 'cdeadmin.asset-ref.v1', projectId: 'project-one',
      assetId: 'etl-one'};
    const qualityRef = {schema: 'cdeadmin.asset-ref.v1', projectId: 'project-one',
      assetId: 'quality-one'};
    const content = definition({dataMovePlan: {...definition().dataMovePlan,
      units: [{...definition().dataMovePlan.units[0], transformRef}]},
    validationPlan: [{...definition().validationPlan[0], qualityAssetRef: qualityRef}]});
    const session = service.create({content});
    service.bindIntegration(session.id, {kind: 'schema_compare', externalSessionId: 'compare-runtime',
      reference: content.schemaPlan.schemaCompareRef, artifactId: 'schema-one'});
    service.bindIntegration(session.id, {kind: 'etl', externalSessionId: 'etl-runtime',
      reference: transformRef, artifactId: 'etl-run-one'});
    service.bindIntegration(session.id, {kind: 'quality', externalSessionId: 'quality-runtime',
      reference: qualityRef, artifactId: 'run-one'});
    await events.publish('schema_compare.apply.completed', {sessionId: 'compare-runtime',
      planId: 'different-plan'}, {origin: 'cdeadmin.schema_compare'});
    expect(service.get(session.id).runtime.schemaApplied).toBe(false);
    await events.publish('schema_compare.apply.completed', {sessionId: 'compare-runtime',
      planId: 'schema-one'},
    {origin: 'cdeadmin.schema_compare'});
    await events.publish('etl.run.completed', {sessionId: 'etl-runtime', runId: 'etl-run-one',
      state: 'succeeded'},
    {origin: 'cdeadmin.etl'});
    await events.publish('quality.run.completed', {sessionId: 'quality-runtime', runId: 'run-one',
      outcome: 'pass', summary: {passed: 1}}, {origin: 'cdeadmin.quality'});
    expect(service.get(session.id).runtime).toMatchObject({schemaApplied: true,
      schemaValidated: true, initialCopyComplete: true,
      validationResults: [{id: 'orders-count', state: 'passed'}]});
    await events.publish('schema_compare.plan.created', {sessionId: 'compare-runtime'},
      {origin: 'cdeadmin.schema_compare'});
    expect(service.get(session.id)).toMatchObject({state: 'stale', runtime: {
      schemaApplied: false, schemaValidated: false, initialCopyComplete: false}});
    expect(() => service.bindIntegration(session.id, {kind: 'cdc', externalSessionId: 'wrong',
      reference: qualityRef, artifactId: 'run'})).toThrow('not present');
  });

  test('imports and exports with provenance through the runtime boundary', () => {
    const {service} = fixture(); const session = service.create({content: definition()});
    const exported = service.exportDefinition(session.id, {profile: 'portable',
      provenance: {source: 'test'}});
    const other = service.create({content: definition({name: 'Other'})});
    service.recordIntegrationState(other.id, {schemaApplied: true, schemaValidated: true,
      initialCopyComplete: true});
    service.importDefinition(other.id, exported, {currentUser: user()});
    expect(service.get(other.id)).toMatchObject({dirty: true,
      interchange: {profile: 'portable', provenance: {source: 'test',
        migrationSessionId: session.id}}, runtime: {schemaApplied: false,
        schemaValidated: false, initialCopyComplete: false}, runs: [], tasks: [],
      integrationBindings: []});
  });

  test('search distinguishes assets, live resources, mappings and runs', async () => {
    const {service, search} = fixture(); service.create({content: definition()});
    const result = await search.search('operations', {context: {permissions: ['migration.view']}});
    expect(result.groups[0].results.map((item) => item.type)).toEqual(expect.arrayContaining([
      'migration.asset', 'migration.resource']));
    expect((await search.search('operations', {context: {permissions: []}})).total).toBe(0);
  });

  test('keeps dirty authored state after optimistic persistence conflict', async () => {
    const projectAssets = {update: jest.fn().mockRejectedValue(new Error('asset conflict'))};
    const {service} = fixture({projectAssets}); const session = service.create({content: definition()});
    service.acceptMapping(session.id, 'orders-map', 'manual');
    await expect(service.save(session.id, {projectId: 'p', assetId: 'a', expectedVersion: 1}))
      .rejects.toThrow('asset conflict');
    expect(service.get(session.id)).toMatchObject({dirty: true, state: 'runtime_failure'});
  });

  test('assessment payload remains provider-owned and validated', () => {
    expect(assessment().findings[0].evidenceSource).toBe('provider_adapter');
  });
});
