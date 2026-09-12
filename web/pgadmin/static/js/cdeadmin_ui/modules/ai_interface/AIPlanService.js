/////////////////////////////////////////////////////////////
// CDEadmin deterministic AI plan validation and state authority.
/////////////////////////////////////////////////////////////

import {immutable, platformValue} from '../../platform/serviceUtils';
import {validateAIAsset} from './AIAssetContracts';
import {
  AIExecutionFailure, AI_RISK_CLASS_IDS, aiActor, boundedStrings, exactLifecycleObject,
  stableAIJson, validateAIPreparedOperation,
} from './AILifecycleContracts';

const LIVE_KINDS = new Set(['READ_METADATA', 'READ_DATA', 'COMPILE_QUERY', 'EXECUTE_QUERY',
  'EDIT_ASSET', 'INVOKE_COMMAND', 'START_TASK', 'WAIT_TASK']);
const COMMAND_KINDS = new Set(['COMPILE_QUERY', 'EXECUTE_QUERY', 'CREATE_ASSET_DRAFT',
  'EDIT_ASSET', 'INVOKE_COMMAND', 'START_TASK']);
const PREPARED_KINDS = new Set(['READ_METADATA', 'READ_DATA', 'COMPILE_QUERY', 'EXECUTE_QUERY',
  'CREATE_ASSET_DRAFT', 'EDIT_ASSET', 'INVOKE_COMMAND', 'START_TASK']);
const OPERATION_KINDS = Object.freeze({READ_METADATA: ['metadata_read'],
  READ_DATA: ['data_read', 'query_read'], COMPILE_QUERY: ['query_compile'],
  EXECUTE_QUERY: ['query_read', 'query_mutation'], CREATE_ASSET_DRAFT: ['project_draft'],
  EDIT_ASSET: ['project_draft'], INVOKE_COMMAND: ['command'], START_TASK: ['task']});

function approvalBinding(input, step, riskClass) {
  exactLifecycleObject(input, ['requirementId', 'stepIds', 'connectorRef', 'principalRef',
    'resourceRefs', 'environment', 'riskClass'], 'AI plan approval binding');
  const value = immutable({requirementId: platformValue(input.requirementId,
    'AI plan approval requirement ID'), stepIds: boundedStrings(input.stepIds,
    'AI plan approval step IDs'), connectorRef: platformValue(input.connectorRef,
    'AI plan approval connector'), principalRef: platformValue(input.principalRef,
    'AI plan approval principal'), resourceRefs: boundedStrings(input.resourceRefs,
    'AI plan approval resources'), environment: platformValue(input.environment,
    'AI plan approval environment'), riskClass: platformValue(input.riskClass,
    'AI plan approval risk')});
  if(value.riskClass !== riskClass || !value.stepIds.includes(step.stepId)) throw new TypeError(
    'AI plan approval binding must include the validated step and computed risk.');
  return value;
}

function retryContract(input={}) {
  exactLifecycleObject(input, ['kind', 'providerReadIdempotent', 'explicitlyIdempotent',
    'backendIdempotencyRef'], 'AI plan retry contract');
  const kind = input.kind ?? 'mutation';
  if(!['model', 'read', 'mutation'].includes(kind)) throw new TypeError(
    'AI plan retry kind is invalid.');
  for(const field of ['providerReadIdempotent', 'explicitlyIdempotent']) if(
    typeof (input[field] ?? false) !== 'boolean') throw new TypeError(
    `AI plan retry ${field} must be boolean.`);
  return immutable({kind, providerReadIdempotent: input.providerReadIdempotent === true,
    explicitlyIdempotent: input.explicitlyIdempotent === true,
    backendIdempotencyRef: input.backendIdempotencyRef == null ? null : platformValue(
      input.backendIdempotencyRef, 'AI plan backend idempotency reference')});
}

function validationResult(input, step, plan, now) {
  exactLifecycleObject(input, ['valid', 'checkId', 'reason', 'evidenceRef', 'riskClass',
    'approvalRequired', 'approvalBinding', 'preparedOperation', 'retry'],
  'AI plan validation result');
  if(typeof input.valid !== 'boolean' || typeof input.approvalRequired !== 'boolean') throw new TypeError(
    'AI plan validation and approval decisions must be explicit.');
  const checkId = platformValue(input.checkId, 'AI plan validation check ID');
  const riskClass = platformValue(input.riskClass, 'AI plan validation risk class');
  if(!AI_RISK_CLASS_IDS.includes(riskClass)) throw new TypeError('AI plan validation risk is invalid.');
  if(riskClass === 'R7') return immutable({valid: false, checkId,
    reason: 'R7 raw-secret disclosure/export is forbidden to the AI model.', evidenceRef: null,
    riskClass, approvalRequired: false, approvalBinding: null, preparedOperation: null,
    retry: retryContract(input.retry)});
  if(!plan.validationChecks.includes(checkId) || !plan.risks.includes(riskClass)) return immutable({
    valid: false, checkId, reason: 'Computed validation/risk is absent from the reviewed plan.',
    evidenceRef: null, riskClass, approvalRequired: false, approvalBinding: null,
    preparedOperation: null, retry: retryContract(input.retry)});
  const target = input.approvalRequired ? approvalBinding(input.approvalBinding, step,
    riskClass) : null;
  if(target && !plan.requiredApprovals.includes(target.requirementId)) return immutable({
    valid: false, checkId, reason: 'Computed approval requirement is absent from the reviewed plan.',
    evidenceRef: null, riskClass, approvalRequired: false, approvalBinding: null,
    preparedOperation: null, retry: retryContract(input.retry)});
  if(input.valid && PREPARED_KINDS.has(step.kind) && input.preparedOperation == null)
    throw new TypeError(`${step.kind} requires a prepared operation.`);
  const prepared = input.preparedOperation == null ? null : validateAIPreparedOperation(
    input.preparedOperation);
  if(prepared) {
    const snapshot = plan.connectorSnapshots.find((item) => String(item.connectorId ??
      item.connectorRef ?? item.id ?? '') === step.connectorRef);
    if(!snapshot || stableAIJson(prepared.connectorSnapshot) !== stableAIJson(snapshot) ||
        prepared.riskClass !== riskClass || !OPERATION_KINDS[step.kind]?.includes(
      prepared.operationKind) || stableAIJson(prepared.normalizedArguments) !==
          stableAIJson(step.arguments) || stableAIJson([...prepared.canonicalResourceRefs].sort()) !==
          stableAIJson([...step.resourceRefs].sort()) || Date.parse(prepared.expiresAt) <
          Date.parse(now)) throw new TypeError(
      'AI prepared operation is stale or does not exactly match its reviewed plan step.');
    if(target && (target.connectorRef !== step.connectorRef ||
        snapshot.principalBinding && target.principalRef !== snapshot.principalBinding))
      throw new TypeError('AI approval binding does not match the prepared connector principal.');
  }
  return immutable({valid: input.valid, checkId, reason: String(input.reason ?? ''),
    evidenceRef: input.evidenceRef == null ? null : platformValue(input.evidenceRef,
      'AI plan validation evidence'), riskClass, approvalRequired: input.approvalRequired,
    approvalBinding: target, preparedOperation: prepared, retry: retryContract(input.retry)});
}

function planView(record) {
  return immutable({schema: 'cdeadmin.ai-plan-lifecycle.v1', plan: record.plan,
    validations: [...record.validations], approvalCoverage: record.approvalCoverage,
    taskRef: record.taskRef, error: record.error, createdAt: record.createdAt,
    updatedAt: record.updatedAt});
}

export class AIPlanService {
  constructor({approvals, validateStep, contextRevision, audit=null,
    now=() => new Date().toISOString()}={}) {
    if(!approvals || typeof approvals.issue !== 'function' ||
        typeof approvals.coverage !== 'function') throw new TypeError(
      'AI plan service requires approval authority.');
    if(typeof validateStep !== 'function' || typeof contextRevision !== 'function') throw new TypeError(
      'AI plan service requires validation and context-revision authorities.');
    this.approvals = approvals; this.validateStep = validateStep;
    this.contextRevision = contextRevision; this.audit = audit; this.now = now;
    this.plans = new Map(); this.listeners = new Set();
  }

  subscribe(listener) { this.listeners.add(listener); return () => this.listeners.delete(listener); }
  _publish(record) { const view = planView(record); this.listeners.forEach((listener) => listener(view));
    return view; }
  _record(planId) { const record = this.plans.get(String(planId)); if(!record) throw new Error(
    `Unknown AI plan: ${planId}`); return record; }
  _setPlan(record, updates={}) {
    record.plan = validateAIAsset('AIPlan', {...record.plan, ...updates});
    record.updatedAt = this.now(); return this._publish(record);
  }
  _setStep(record, stepId, status) {
    return this._setPlan(record, {steps: record.plan.steps.map((step) =>
      step.stepId === stepId ? {...step, status} : step)});
  }

  create(input, context={}) {
    const plan = validateAIAsset('AIPlan', input);
    if(plan.status !== 'draft' || !plan.steps.length) throw new TypeError(
      'A new AI plan must be a non-empty draft.');
    if(this.plans.has(plan.planId)) throw new Error(`AI plan already exists: ${plan.planId}`);
    const time = this.now(); const record = {plan, validations: [], approvalPolicy: null,
      approvalCoverage: null, taskRef: null, error: null, createdAt: time, updatedAt: time};
    this.plans.set(plan.planId, record); const actor = aiActor(context, 'ai.delegate_draft');
    this.audit?.append({eventType: 'ai.plan.created', initiator: actor.id, planId: plan.planId,
      agentProfileRef: plan.agentProfileRef, resourceRefs: [...new Set(plan.steps.flatMap(
        (step) => step.resourceRefs))], content: {modelProposal: plan}});
    return this._publish(record);
  }

  get(planId) { return planView(this._record(planId)); }
  list() { return immutable([...this.plans.values()].map(planView)); }

  revise(planId, expectedRevision, replacement, context={}) {
    const record = this._record(planId); const next = validateAIAsset('AIPlan', replacement);
    if(record.plan.revision !== expectedRevision || next.planId !== record.plan.planId ||
        next.revision !== expectedRevision + 1 || next.status !== 'draft') throw new AIExecutionFailure(
      'APPROVAL_STALE', 'AI plan revision does not continue the current draft exactly.');
    const actor = aiActor(context, 'ai.delegate_draft');
    this.approvals.invalidate({planId, reason: 'Plan content was revised.', initiator: actor.id});
    record.plan = next; record.validations = []; record.approvalPolicy = null;
    record.approvalCoverage = null; record.taskRef = null; record.error = null;
    record.updatedAt = this.now(); this.audit?.append({eventType: 'ai.plan.revised',
      initiator: actor.id, planId, content: {modelProposal: next}}); return this._publish(record);
  }

  async validate(planId, {approvalPolicy, ...context}={}) {
    const record = this._record(planId); aiActor(context, 'ai.delegate_draft');
    if(!['draft', 'invalid', 'stale', 'ready_for_approval'].includes(record.plan.status)) throw new Error(
      `AI plan cannot be validated from ${record.plan.status}.`);
    this._setPlan(record, {status: 'validating'}); record.error = null;
    const currentRevision = await this.contextRevision(record.plan, context);
    if(currentRevision !== record.plan.baseContextRevision) {
      record.validations = []; record.approvalCoverage = null;
      record.error = 'The plan base context revision is stale.';
      this._setPlan(record, {status: 'stale'}); return this._publish(record);
    }
    const connectorIds = new Set(record.plan.connectorSnapshots.map((item) =>
      String(item.connectorId ?? item.connectorRef ?? item.id ?? '')));
    const results = [];
    for(const step of record.plan.steps) {
      let structuralError = null;
      if(COMMAND_KINDS.has(step.kind) && !step.commandId) structuralError =
        `${step.kind} requires a command.`;
      if(LIVE_KINDS.has(step.kind) && step.connectorRef && !connectorIds.has(step.connectorRef))
        structuralError = `Connector snapshot is absent for ${step.connectorRef}.`;
      if(structuralError) {
        results.push(immutable({stepId: step.stepId, valid: false, checkId: 'structure',
          reason: structuralError, evidenceRef: null, riskClass: 'R0', approvalRequired: false,
          approvalBinding: null, preparedOperation: null, retry: retryContract()})); continue;
      }
      try { results.push(immutable({stepId: step.stepId, ...validationResult(
        await this.validateStep(step, immutable({plan: record.plan, context})), step,
        record.plan, this.now())})); } catch(error) { results.push(immutable({stepId: step.stepId,
        valid: false, checkId: 'validator', reason: error.message, evidenceRef: null,
        riskClass: 'R0', approvalRequired: false, approvalBinding: null,
        preparedOperation: null, retry: retryContract()})); }
    }
    const exactSets = (left, right) => stableAIJson([...new Set(left)].sort()) ===
      stableAIJson([...new Set(right)].sort());
    const computedRisks = results.map((item) => item.riskClass);
    const computedChecks = results.filter((item) => item.checkId !== 'structure' &&
      item.checkId !== 'validator').map((item) => item.checkId);
    const computedApprovals = results.filter((item) => item.approvalRequired).map((item) =>
      item.approvalBinding.requirementId);
    if(!exactSets(computedRisks, record.plan.risks) ||
        !exactSets(computedChecks, record.plan.validationChecks) ||
        !exactSets(computedApprovals, record.plan.requiredApprovals)) results.push(immutable({
      stepId: '$plan', valid: false, checkId: 'reviewed-plan-exactness',
      reason: 'Reviewed risks, validation checks, or approval requirements are not exact.',
      evidenceRef: null, riskClass: 'R0', approvalRequired: false, approvalBinding: null,
      preparedOperation: null, retry: retryContract()}));
    record.validations = results; record.approvalPolicy = validateAIAsset(
      'AIApprovalPolicy', approvalPolicy);
    const status = results.every((item) => item.valid) ? results.some((item) =>
      item.approvalRequired) ? 'ready_for_approval' : 'approved' : 'invalid';
    record.approvalCoverage = status === 'approved' ? immutable({complete: true,
      requirements: []}) : null;
    this._setPlan(record, {status, steps: record.plan.steps.map((step) => ({...step,
      status: results.find((item) => item.stepId === step.stepId)?.valid ?
        results.find((item) => item.stepId === step.stepId).approvalRequired ?
          'approval_required' : 'validated' : 'failed'}))});
    this.audit?.append({eventType: 'ai.plan.validated', initiator: aiActor(context).id,
      planId, policyDecisions: results.map((item) => ({stepId: item.stepId, valid: item.valid,
        checkId: item.checkId, riskClass: item.riskClass, approvalRequired: item.approvalRequired,
        evidenceRef: item.evidenceRef})), diagnostics: results.filter((item) => !item.valid).map(
        (item) => ({message: item.reason}))}); return this._publish(record);
  }

  approve(planId, input, context={}) {
    const record = this._record(planId);
    if(record.plan.status !== 'ready_for_approval') throw new AIExecutionFailure(
      'APPROVAL_STALE', 'AI plan is not ready for approval.');
    const requirement = record.validations.find((item) => item.approvalRequired &&
      item.approvalBinding.requirementId === input.requirementId);
    if(!requirement) throw new AIExecutionFailure('APPROVAL_STALE',
      'The approval requirement is not part of the current plan validation.');
    const evidence = this.approvals.issue({plan: record.plan,
      approvalPolicy: record.approvalPolicy, expectedBinding: requirement.approvalBinding,
      method: input.method, confirmationText: input.confirmationText ?? ''}, context);
    const requirements = record.validations.filter((item) => item.approvalRequired).map(
      (item) => item.approvalBinding);
    record.approvalCoverage = this.approvals.coverage({plan: record.plan, requirements,
      approvalPolicy: record.approvalPolicy});
    if(record.approvalCoverage.complete) this._setPlan(record, {status: 'approved'});
    else this._publish(record);
    return immutable({evidence, lifecycle: planView(record)});
  }

  reject(planId, reason, context={}) {
    const record = this._record(planId); const actor = aiActor(context, 'ai.delegate_draft');
    if(!['ready_for_approval', 'approved'].includes(record.plan.status)) throw new Error(
      'Only a reviewable AI plan can be rejected.');
    reason = platformValue(reason, 'AI plan rejection reason', 2000);
    this.approvals.invalidate({planId, reason, initiator: actor.id});
    record.error = reason; this._setPlan(record, {status: 'cancelled', steps:
      record.plan.steps.map((step) => ({...step, status: ['succeeded', 'skipped'].includes(
        step.status) ? step.status : 'cancelled'}))});
    this.audit?.append({eventType: 'ai.plan.rejected', initiator: actor.id, planId,
      diagnostics: [{message: reason}]}); return this._publish(record);
  }

  async executionSnapshot(planId, context={}) {
    const record = this._record(planId);
    if(record.plan.status !== 'approved' || !record.validations.every((item) => item.valid))
      throw new AIExecutionFailure('APPROVAL_REQUIRED', 'AI plan is not approved and valid.');
    if(record.validations.some((item) => item.approvalRequired)) {
      const coverage = this.approvals.coverage({plan: record.plan, requirements:
        record.validations.filter((item) => item.approvalRequired).map((item) =>
          item.approvalBinding), approvalPolicy: record.approvalPolicy});
      if(!coverage.complete) throw new AIExecutionFailure('APPROVAL_STALE',
        'AI plan approval evidence is no longer complete.');
      record.approvalCoverage = coverage;
    }
    if(record.validations.some((item) => item.preparedOperation &&
        Date.parse(item.preparedOperation.expiresAt) < Date.parse(this.now()))) {
      this.approvals.invalidate({planId, reason: 'Prepared operation expired before execution.',
        initiator: aiActor(context).id});
      this._setPlan(record, {status: 'stale'});
      throw new AIExecutionFailure('APPROVAL_STALE',
        'An AI prepared operation expired after validation.');
    }
    if(await this.contextRevision(record.plan, context) !== record.plan.baseContextRevision) {
      this.approvals.invalidate({planId, reason: 'Plan context changed before execution.',
        initiator: aiActor(context).id});
      this._setPlan(record, {status: 'stale'});
      throw new AIExecutionFailure('APPROVAL_STALE',
        'AI plan context changed after validation and approval.');
    }
    return planView(record);
  }

  authorizationCheck({operation}={}) {
    try {
      const requested = operation?.plan?.plan ?? operation?.plan;
      const record = this._record(requested?.planId);
      const step = record.plan.steps.find((item) => item.stepId === operation.operationId);
      const validation = record.validations.find((item) => item.stepId === operation.operationId);
      const acceptableState = operation.phase === 'execute' ? record.plan.status === 'running' :
        ['validating', 'ready_for_approval', 'approved'].includes(record.plan.status);
      const exactStep = step && step.commandId === operation.commandId &&
        step.connectorRef === (operation.connectorRef ?? step.connectorRef) &&
        stableAIJson(step.arguments) === stableAIJson(operation.normalizedArguments) &&
        stableAIJson(step.resourceRefs) === stableAIJson(operation.resourceRefs);
      if(!acceptableState || !exactStep || operation.phase === 'execute' && !validation?.valid)
        throw new AIExecutionFailure('POLICY_DENIED',
          'The operation does not match a current validated plan step.');
      return immutable({allowed: true, reason: '', evidenceRef:
        validation?.evidenceRef ?? `ai-plan:${record.plan.planId}:${record.plan.revision}`});
    } catch(error) { return immutable({allowed: false, reason: error.message,
      evidenceRef: null}); }
  }

  markRunning(planId, taskRef) { const record = this._record(planId);
    record.taskRef = platformValue(taskRef, 'AI plan task reference'); record.error = null;
    return this._setPlan(record, {status: 'running'}); }
  markStep(planId, stepId, status) { if(!['running', 'succeeded', 'failed', 'skipped',
    'cancelled'].includes(status)) throw new TypeError('AI execution step state is invalid.');
  return this._setStep(this._record(planId), stepId, status); }
  markTerminal(planId, status, error=null) { if(!['succeeded', 'failed', 'cancelled'].includes(status))
    throw new TypeError('AI terminal plan state is invalid.'); const record = this._record(planId);
  record.error = error == null ? null : String(error); return this._setPlan(record, {status}); }
}
