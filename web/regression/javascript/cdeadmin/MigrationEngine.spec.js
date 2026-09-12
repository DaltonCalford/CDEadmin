import {
  applyCheckpoint, canTransition, createCutoverArm, cutoverReadiness, migrationPlanRevision,
  orderedRunbookSteps, orderedSchemaOperations, rollbackState, validateCutoverArm,
  validateMigrationPlan,
} from 'sources/cdeadmin_ui/modules/migration/MigrationEngine';
import {definition, mapping, schemaOperation} from './MigrationTestUtils';

describe('Migration engine', () => {
  test('orders schema dependencies and rejects cycles', () => {
    const plan = {...definition().schemaPlan, operations: [schemaOperation(), schemaOperation({
      id: 'index-orders', dependencies: ['create-orders']})]};
    expect(orderedSchemaOperations(plan).order).toEqual(['create-orders', 'index-orders']);
    const cyclic = {...plan, operations: [schemaOperation({dependencies: ['index-orders']}),
      schemaOperation({id: 'index-orders', dependencies: ['create-orders']})]};
    expect(orderedSchemaOperations(cyclic)).toMatchObject({cyclic: true,
      blocked: ['create-orders', 'index-orders']});
  });
  test('orders cutover runbooks by dependencies and blocks cycles', () => {
    const steps = [{id: 'switch', dependencies: ['freeze']},
      {id: 'freeze', dependencies: []}, {id: 'verify', dependencies: ['switch']}];
    expect(orderedRunbookSteps(steps).order).toEqual(['freeze', 'switch', 'verify']);
    expect(orderedRunbookSteps([{id: 'one', dependencies: ['two']},
      {id: 'two', dependencies: ['one']}])).toMatchObject({cyclic: true,
      blocked: ['one', 'two']});
  });
  test('enforces the migration phase state machine without terminal rewinds', () => {
    expect(canTransition('design', 'dry_run')).toBe(true);
    expect(canTransition('initial_copy', 'validate')).toBe(true);
    expect(canTransition('cutover_ready', 'cutover')).toBe(true);
    expect(canTransition('cutover', 'rollback')).toBe(true);
    expect(canTransition('verify', 'complete')).toBe(true);
    expect(canTransition('complete', 'assess')).toBe(false);
    expect(canTransition('rollback', 'cutover')).toBe(false);
    expect(canTransition('unknown', 'assess')).toBe(false);
  });
  test('keeps ambiguous and lossy mappings blocked until reviewed', () => {
    expect(validateMigrationPlan(definition({mappingSet: [mapping({decision: 'unresolved'})]})).valid)
      .toBe(false);
    expect(validateMigrationPlan(definition({mappingSet: [mapping({lossy: true})]})).errors[0])
      .toContain('Lossy');
    expect(validateMigrationPlan(definition({mappingSet: [mapping({lossy: true,
      lossAcknowledged: true})]})).valid).toBe(true);
  });
  test('prevents checkpoint regression and resumes the exact committed boundary', () => {
    const base = {id: 'cp', unitId: 'unit', state: 'committed', sourceRevision: 'r1',
      sourceBoundary: {snapshot: 1}, lastCompleted: {id: 5}, targetProgress: {id: 5},
      rowDocumentCount: 5, byteCount: 50, retryState: {}, committedAt: 'now', nativeDetails: {}};
    expect(() => applyCheckpoint(base, {...base, state: 'prepared', lastCompleted: {id: 4}}))
      .toThrow('last committed checkpoint');
    expect(() => applyCheckpoint(base, {...base, rowDocumentCount: 4})).toThrow('backwards');
  });
  test('gates cutover on all independent readiness evidence', () => {
    const ready = {schemaApplied: true, schemaValidated: true, initialCopyComplete: true,
      validationResults: [{id: 'orders-count', state: 'passed'}]};
    expect(cutoverReadiness(definition(), ready).ready).toBe(true);
    expect(cutoverReadiness(definition(), {...ready, initialCopyComplete: false}).blockers)
      .toContain('Initial copy is not complete.');
    expect(cutoverReadiness(definition(), {...ready, validationResults: []}).blockers[0])
      .toContain('has not passed');
    expect(cutoverReadiness(definition({rollbackPlan: {...definition().rollbackPlan,
      deadline: '2026-09-12T00:10:00Z'}}), ready, new Date('2026-09-12T00:11:00Z')).blockers)
      .toContain('Rollback plan is no longer available.');
  });
  test('binds an expiring arm to the exact reviewed plan revision', () => {
    const runtime = {schemaApplied: true, schemaValidated: true, initialCopyComplete: true,
      validationResults: [{id: 'orders-count', state: 'passed'}]};
    const arm = createCutoverArm(definition(), runtime, {actor: 'reviewer', confirmationRef: 'ok',
      environment: 'production', connection: 'target-one'}, new Date('2026-09-12T00:00:00Z'));
    expect(arm.planRevision).toBe(migrationPlanRevision(definition()));
    expect(validateCutoverArm(arm, definition(), new Date('2026-09-12T00:29:00Z')).valid).toBe(true);
    expect(validateCutoverArm(arm, definition(), new Date('2026-09-12T00:31:00Z')).reason)
      .toContain('expired');
    expect(validateCutoverArm(arm, definition({cutoverPlan: {...definition().cutoverPlan,
      targetConnection: 'changed'}}), new Date('2026-09-12T00:01:00Z')).reason).toContain('changed');
  });
  test('changes rollback classification after the point of no return', () => {
    const plan = {...definition().rollbackPlan, pointOfNoReturn: 'endpoint-switch'};
    expect(rollbackState(plan, []).available).toBe(true);
    expect(rollbackState(plan, ['endpoint-switch'])).toMatchObject({
      classification: 'not_available_after_point', available: false});
    expect(rollbackState({...plan, deadline: '2026-09-12T00:10:00Z'}, [],
      new Date('2026-09-12T00:11:00Z'))).toMatchObject({available: false,
      deadlineExpired: true});
  });
});
