/////////////////////////////////////////////////////////////
// Migration assessment, planning, checkpoint and cutover rules.
/////////////////////////////////////////////////////////////

import {immutable, plainObject, platformValue} from '../../platform/serviceUtils';
import {
  ASSESSMENT_CATEGORIES, createMigrationContent, MIGRATION_PHASES,
  ROLLBACK_CLASSES, validateAssessment, validateCopyCheckpoint,
} from './contracts';

const PHASE_TRANSITIONS = Object.freeze({
  discover: ['assess', 'design'],
  assess: ['assess', 'design', 'dry_run'],
  design: ['assess', 'dry_run', 'initial_copy', 'validate'],
  dry_run: ['assess', 'dry_run', 'provision', 'initial_copy', 'validate'],
  provision: ['provision', 'initial_copy', 'validate'],
  initial_copy: ['initial_copy', 'incremental_sync', 'validate'],
  incremental_sync: ['incremental_sync', 'validate'],
  validate: ['validate', 'cutover_ready'],
  cutover_ready: ['validate', 'cutover_ready', 'cutover', 'rollback'],
  cutover: ['cutover', 'verify', 'rollback'],
  verify: ['verify', 'complete', 'rollback'],
  complete: [], rollback: [],
});

export function orderedSchemaOperations(plan) {
  if(!plan) return immutable({order: [], cyclic: false, blocked: []});
  const incoming = new Map(plan.operations.map((item) => [item.id, item.dependencies.length]));
  const outgoing = new Map(plan.operations.map((item) => [item.id, []]));
  plan.operations.forEach((item) => item.dependencies.forEach((dependency) =>
    outgoing.get(dependency).push(item.id)));
  const queue = plan.operations.filter((item) => incoming.get(item.id) === 0)
    .map((item) => item.id).sort(); const order = [];
  while(queue.length) {
    const id = queue.shift(); order.push(id);
    outgoing.get(id).sort().forEach((next) => {
      incoming.set(next, incoming.get(next) - 1);
      if(incoming.get(next) === 0) { queue.push(next); queue.sort(); }
    });
  }
  return immutable({order, cyclic: order.length !== plan.operations.length,
    blocked: plan.operations.map((item) => item.id).filter((id) => !order.includes(id)).sort()});
}

export function orderedRunbookSteps(steps=[]) {
  const incoming = new Map(steps.map((item, index) => [item.id, {
    count: item.dependencies.length, index}]));
  const outgoing = new Map(steps.map((item) => [item.id, []]));
  steps.forEach((item) => item.dependencies.forEach((dependency) =>
    outgoing.get(dependency)?.push(item.id)));
  const compare = (left, right) => incoming.get(left).index - incoming.get(right).index ||
    left.localeCompare(right);
  const queue = steps.filter((item) => incoming.get(item.id).count === 0)
    .map((item) => item.id).sort(compare); const order = [];
  while(queue.length) {
    const id = queue.shift(); order.push(id);
    outgoing.get(id).sort(compare).forEach((next) => {
      const state = incoming.get(next); state.count--;
      if(state.count === 0) { queue.push(next); queue.sort(compare); }
    });
  }
  return immutable({order, cyclic: order.length !== steps.length,
    blocked: steps.map((item) => item.id).filter((id) => !order.includes(id))});
}

export function assessmentSummary(input) {
  const assessment = validateAssessment(input);
  if(!assessment) return immutable({total: 0, categories: {}, blocking: [], unresolved: []});
  const categories = Object.fromEntries(ASSESSMENT_CATEGORIES.map((category) => [category, 0]));
  assessment.findings.forEach((item) => { categories[item.category]++; });
  return immutable({total: assessment.findings.length, categories,
    blocking: assessment.findings.filter((item) => item.blocker && !item.waived).map((item) => item.id),
    unresolved: assessment.findings.filter((item) => item.category === 'unknown' ||
      item.category === 'manual_conversion_required').map((item) => item.id)});
}

export function validateMigrationPlan(input) {
  const content = createMigrationContent(input); const errors = []; const warnings = [];
  if(!content.name.trim()) errors.push('Migration project name is required.');
  if(!content.sourceBinding) errors.push('Migration source binding is required.');
  if(!content.targetBinding) errors.push('Migration target binding is required.');
  if(content.sourceBinding && content.targetBinding &&
      JSON.stringify(content.sourceBinding) === JSON.stringify(content.targetBinding)) {
    errors.push('Migration source and target must be distinct bindings.');
  }
  const summary = assessmentSummary(content.assessment);
  summary.blocking.forEach((id) => errors.push(`Blocking assessment finding is unresolved: ${id}.`));
  summary.unresolved.forEach((id) => warnings.push(`Assessment decision remains unresolved: ${id}.`));
  content.mappingSet.forEach((mapping) => {
    if(mapping.decision === 'unresolved') errors.push(`Mapping decision is unresolved: ${mapping.id}.`);
    if(mapping.lossy && !mapping.lossAcknowledged) errors.push(
      `Lossy mapping requires explicit acknowledgement: ${mapping.id}.`
    );
    if(mapping.behaviorChange) warnings.push(`Mapping changes behavior: ${mapping.id}.`);
  });
  const schemaOrder = orderedSchemaOperations(content.schemaPlan);
  if(schemaOrder.cyclic) errors.push(`Schema plan contains a dependency cycle: ${schemaOrder.blocked.join(', ')}.`);
  const cutoverOrder = orderedRunbookSteps(content.cutoverPlan?.steps ?? []);
  if(cutoverOrder.cyclic) errors.push(
    `Cutover runbook contains a dependency cycle: ${cutoverOrder.blocked.join(', ')}.`
  );
  const rollbackOrder = orderedRunbookSteps(content.rollbackPlan?.steps ?? []);
  if(rollbackOrder.cyclic) errors.push(
    `Rollback runbook contains a dependency cycle: ${rollbackOrder.blocked.join(', ')}.`
  );
  if(content.rollbackPlan?.pointOfNoReturn && !content.cutoverPlan?.steps.some(
    (item) => item.id === content.rollbackPlan.pointOfNoReturn
  )) errors.push('Rollback point of no return must identify a cutover runbook step.');
  if(!content.rollbackPlan && !['discover', 'assess', 'design'].includes(content.phase)) {
    errors.push('Migration execution phases require a rollback plan.');
  }
  if(!content.validationPlan.length && !['discover', 'assess', 'design'].includes(content.phase)) {
    warnings.push('Migration execution has no verification definitions.');
  }
  return immutable({valid: errors.length === 0, errors: [...new Set(errors)],
    warnings: [...new Set(warnings)], assessment: summary, schemaOrder: schemaOrder.order,
    cutoverOrder: cutoverOrder.order, rollbackOrder: rollbackOrder.order});
}

export function canTransition(from, to) {
  if(!MIGRATION_PHASES.includes(from) || !MIGRATION_PHASES.includes(to)) return false;
  return PHASE_TRANSITIONS[from].includes(to);
}

export function applyCheckpoint(previous, nextInput) {
  const next = validateCopyCheckpoint(nextInput);
  if(!previous) {
    if(next.state === 'committed' && !Object.keys(next.lastCompleted).length) throw new TypeError(
      'A committed initial checkpoint requires a completed source range.'
    );
    return next;
  }
  previous = validateCopyCheckpoint(previous);
  if(previous.unitId !== next.unitId) throw new TypeError('Checkpoint unit identity cannot change.');
  if(previous.state === 'committed' && next.state === 'prepared' &&
      JSON.stringify(next.lastCompleted) !== JSON.stringify(previous.lastCompleted)) {
    throw new TypeError('A resumed copy must start from the last committed checkpoint.');
  }
  if(next.rowDocumentCount < previous.rowDocumentCount || next.byteCount < previous.byteCount) {
    throw new TypeError('Migration checkpoint counters cannot move backwards.');
  }
  if(previous.sourceRevision && next.sourceRevision && previous.sourceRevision !== next.sourceRevision) {
    throw new TypeError('Migration checkpoint source revision changed during resume.');
  }
  return next;
}

export function normalizeVerificationResult(input, definition) {
  plainObject(input, 'Migration verification result');
  const state = platformValue(input.state, 'Migration verification state');
  if(!['passed', 'failed', 'warning', 'unknown'].includes(state)) throw new TypeError(
    'Migration verification state is invalid.'
  );
  if(definition.type === 'deterministic_hashes' && !input.canonicalizationEvidence) {
    throw new TypeError('Hash verification result requires canonicalization evidence.');
  }
  return immutable({schema: 'cdeadmin.migration-verification-result.v1', id: definition.id,
    type: definition.type, state, sourceValue: input.sourceValue ?? null,
    targetValue: input.targetValue ?? null, differences: Array.isArray(input.differences) ?
      input.differences : [], canonicalizationEvidence: input.canonicalizationEvidence ?? null,
    evidence: input.evidence ?? {}, nativeDetails: input.nativeDetails ?? {}});
}

export function cutoverReadiness(contentInput, runtime={}, now=new Date()) {
  const content = createMigrationContent(contentInput); const blockers = [];
  const assessment = assessmentSummary(content.assessment);
  if(assessment.blocking.length) blockers.push('Blocking assessment issues remain unresolved.');
  if(runtime.schemaApplied !== true || runtime.schemaValidated !== true) blockers.push(
    'Schema plan is not applied and validated.'
  );
  if(!['schema_only', 'validation_only'].includes(content.strategy) && runtime.initialCopyComplete !== true) {
    blockers.push('Initial copy is not complete.');
  }
  if(content.strategy === 'online_with_cdc') {
    if(runtime.cdcState !== 'running') blockers.push('CDC catch-up is not running.');
    if(!Number.isFinite(runtime.cdcLag) || runtime.cdcLag > content.cutoverPlan?.cdcLagThreshold) {
      blockers.push('CDC lag is outside the configured threshold.');
    }
  }
  const resultById = new Map((runtime.validationResults ?? []).map((item) => [item.id, item]));
  const incompleteValidation = content.validationPlan.filter((check) => check.blocking &&
    resultById.get(check.id)?.state !== 'passed');
  if(incompleteValidation.length && !runtime.validationWaiverRef) blockers.push(
    `Blocking validation has not passed: ${incompleteValidation.map((item) => item.id).join(', ')}.`
  );
  if(!content.cutoverPlan) blockers.push('Cutover plan is required.');
  if(!content.rollbackPlan) blockers.push('Rollback plan is required.');
  else if(!rollbackState(content.rollbackPlan, runtime.completedCutoverSteps ?? [], now).available) {
    blockers.push('Rollback plan is no longer available.');
  }
  return immutable({ready: blockers.length === 0, blockers,
    targetEnvironment: content.cutoverPlan?.targetEnvironment ?? null,
    targetConnection: content.cutoverPlan?.targetConnection ?? null});
}

export function migrationPlanRevision(contentInput) {
  const content = createMigrationContent(contentInput);
  return `${content.schemaVersion}:${JSON.stringify({assessment: content.assessment, mappingSet: content.mappingSet,
    schemaPlan: content.schemaPlan, dataMovePlan: content.dataMovePlan,
    cdcPlanRef: content.cdcPlanRef, validationPlan: content.validationPlan,
    cutoverPlan: content.cutoverPlan, rollbackPlan: content.rollbackPlan})}`;
}

export function createCutoverArm(contentInput, runtime, input, now=new Date()) {
  const content = createMigrationContent(contentInput); const readiness = cutoverReadiness(
    content, runtime, now);
  if(!readiness.ready) throw new Error(`Cutover cannot be armed: ${readiness.blockers.join(' ')}`);
  plainObject(input, 'Migration cutover arm');
  const confirmationRef = platformValue(input.confirmationRef, 'Migration confirmation reference');
  const actor = platformValue(input.actor, 'Migration cutover actor');
  if(input.environment !== readiness.targetEnvironment || input.connection !== readiness.targetConnection) {
    throw new Error('Cutover target environment and connection must match the reviewed plan.');
  }
  const armedAt = new Date(now); const expiresAt = new Date(
    armedAt.getTime() + content.cutoverPlan.armWindowMinutes * 60000);
  return immutable({schema: 'cdeadmin.migration-cutover-arm.v1',
    planId: content.cutoverPlan.id, planRevision: migrationPlanRevision(content),
    confirmationRef, actor, environment: input.environment, connection: input.connection,
    armedAt: armedAt.toISOString(), expiresAt: expiresAt.toISOString()});
}

export function validateCutoverArm(arm, contentInput, now=new Date()) {
  const content = createMigrationContent(contentInput);
  if(!arm || arm.planId !== content.cutoverPlan?.id) return immutable({valid: false,
    reason: 'Cutover plan is not armed.'});
  if(arm.planRevision !== migrationPlanRevision(content)) {
    return immutable({valid: false, reason: 'Cutover plan changed after it was armed.'});
  }
  if(new Date(arm.expiresAt).getTime() <= new Date(now).getTime()) return immutable({valid: false,
    reason: 'Cutover arm has expired.'});
  return immutable({valid: true, reason: ''});
}

export function rollbackState(plan, completedSteps=[], now=new Date()) {
  if(!plan) return immutable({classification: null, available: false,
    message: 'No rollback plan is defined.'});
  const completed = new Set(completedSteps);
  const crossed = plan.pointOfNoReturn && completed.has(plan.pointOfNoReturn);
  const expired = plan.deadline && new Date(plan.deadline).getTime() <= new Date(now).getTime();
  const classification = crossed || expired ? 'not_available_after_point' : plan.classification;
  if(!ROLLBACK_CLASSES.includes(classification)) throw new TypeError('Rollback classification is invalid.');
  return immutable({classification, available: classification !== 'not_available_after_point',
    pointOfNoReturnCrossed: Boolean(crossed), deadlineExpired: Boolean(expired), message: crossed ?
      `Rollback is unavailable after ${plan.pointOfNoReturn}.` : expired ?
        `Rollback deadline ${plan.deadline} has expired.` : `Rollback is ${classification}.`});
}
