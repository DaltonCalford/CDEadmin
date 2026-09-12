/////////////////////////////////////////////////////////////
// CDEadmin human approval evidence and exact binding authority.
/////////////////////////////////////////////////////////////

import {immutable, platformValue} from '../../platform/serviceUtils';
import {validateAIAsset} from './AIAssetContracts';
import {
  AIExecutionFailure, AI_RISK_CLASS_IDS, aiActor, aiPlanHash, boundedStrings,
  exactLifecycleObject, stableAIJson,
} from './AILifecycleContracts';

const REQUIRED_PERMISSIONS = Object.freeze({R4: 'ai.delegate_write',
  R5: 'ai.approve_high_risk', R6: 'ai.approve_production'});

function riskRule(policy, riskClass) {
  if(!AI_RISK_CLASS_IDS.slice(0, 7).includes(riskClass)) throw new TypeError(
    'AI approval risk class is invalid.');
  return policy.riskRules[riskClass];
}

function requiredMethod(rule, provided) {
  if(rule === 'forbid') throw new AIExecutionFailure('POLICY_DENIED',
    'The approval policy forbids this operation.');
  if(rule === 'auto') return 'review';
  if(rule === 'review_diff' || rule === 'review') return 'review';
  if(rule === 'confirm_each') return 'confirm_each';
  if(rule === 'typed_confirm') return 'typed_confirm';
  if(rule === 'dual_approval') return 'dual_approval';
  if(rule === 'dual_or_typed') {
    if(!['typed_confirm', 'dual_approval'].includes(provided)) throw new AIExecutionFailure(
      'APPROVAL_REQUIRED', 'R6 approval requires typed confirmation or dual approval.');
    return provided;
  }
  throw new TypeError('Unknown AI approval policy rule.');
}

function binding(input) {
  exactLifecycleObject(input, ['requirementId', 'stepIds', 'connectorRef', 'principalRef',
    'resourceRefs', 'environment', 'riskClass'], 'AI approval binding');
  const riskClass = platformValue(input.riskClass, 'AI approval risk class');
  if(!AI_RISK_CLASS_IDS.slice(0, 7).includes(riskClass)) throw new TypeError(
    'AI approval risk class is invalid.');
  return immutable({requirementId: platformValue(input.requirementId,
    'AI approval requirement ID'), stepIds: boundedStrings(input.stepIds,
    'AI approval step IDs'), connectorRef: platformValue(input.connectorRef,
    'AI approval connector reference'), principalRef: platformValue(input.principalRef,
    'AI approval principal reference'), resourceRefs: boundedStrings(input.resourceRefs,
    'AI approval resource references'), environment: platformValue(input.environment,
    'AI approval environment'), riskClass});
}

function sameBinding(left, right) { return stableAIJson(left) === stableAIJson(right); }

function assertPlanBinding(plan, target) {
  const steps = target.stepIds.map((id) => plan.steps.find((item) => item.stepId === id));
  if(steps.some((item) => !item) || !plan.requiredApprovals.includes(target.requirementId) ||
      !plan.risks.includes(target.riskClass) || steps.some((item) =>
    item.connectorRef !== target.connectorRef)) throw new AIExecutionFailure('APPROVAL_STALE',
    'Approval target is not declared by the current plan.');
  const resources = [...new Set(steps.flatMap((item) => item.resourceRefs))].sort();
  if(stableAIJson(resources) !== stableAIJson([...target.resourceRefs].sort()))
    throw new AIExecutionFailure('APPROVAL_STALE',
      'Approval resources do not exactly match the selected plan steps.');
}

export class AIApprovalService {
  constructor({now=() => new Date().toISOString(), approvalEpoch=() => 0,
    audit=null}={}) {
    if(typeof approvalEpoch !== 'function') throw new TypeError(
      'AI approval epoch authority is required.');
    this.now = now; this.approvalEpoch = approvalEpoch; this.audit = audit;
    this.approvals = new Map(); this.sequence = 0;
  }

  confirmationPhrase(plan, expectedBinding) {
    const planValue = validateAIAsset('AIPlan', plan); const target = binding(expectedBinding);
    return `CONFIRM ${target.riskClass} ${planValue.planId} REVISION ${planValue.revision}`;
  }

  issue({plan, approvalPolicy, expectedBinding, method, confirmationText=''}, context={}) {
    const planValue = validateAIAsset('AIPlan', plan);
    if(planValue.status !== 'ready_for_approval') throw new AIExecutionFailure(
      'APPROVAL_STALE', 'Only the current ready-for-approval plan may be approved.');
    const policy = validateAIAsset('AIApprovalPolicy', approvalPolicy);
    const target = binding(expectedBinding); const rule = riskRule(policy, target.riskClass);
    assertPlanBinding(planValue, target);
    const confirmationMethod = requiredMethod(rule, method);
    const actor = aiActor(context, REQUIRED_PERMISSIONS[target.riskClass] ?? 'ai.delegate_draft');
    if(confirmationMethod === 'typed_confirm' && confirmationText !== this.confirmationPhrase(
      planValue, target)) throw new AIExecutionFailure('APPROVAL_REQUIRED',
      'The typed confirmation phrase does not match the current plan revision.');
    if(confirmationMethod !== 'typed_confirm' && confirmationText) throw new TypeError(
      'Typed confirmation text is accepted only for typed confirmation.');
    const createdAt = this.now(); const minutes = policy.expiryMinutes[target.riskClass] ?? 30;
    const expiresAt = new Date(Date.parse(createdAt) + minutes * 60000).toISOString();
    const approvalId = `ai-approval-${++this.sequence}`;
    const evidence = immutable({schema: 'cdeadmin.ai-approval-evidence.v1', approvalId,
      approver: actor.id, planId: planValue.planId, planRevision: planValue.revision,
      planHash: aiPlanHash(planValue), requirementId: target.requirementId,
      stepIds: target.stepIds, connectorRef: target.connectorRef,
      principalRef: target.principalRef, resourceRefs: target.resourceRefs,
      environment: target.environment, riskClass: target.riskClass, createdAt, expiresAt,
      confirmationMethod, approvalEpoch: this.approvalEpoch(), revoked: false});
    this.approvals.set(approvalId, evidence);
    this.audit?.append({eventType: 'ai.approval.issued', initiator: actor.id,
      planId: planValue.planId, approvalRefs: [approvalId], resourceRefs: target.resourceRefs,
      connectorRef: target.connectorRef, principalRef: target.principalRef,
      policyDecisions: [{riskClass: target.riskClass, rule, confirmationMethod}]});
    return evidence;
  }

  get(approvalId) {
    const value = this.approvals.get(String(approvalId));
    if(!value) throw new AIExecutionFailure('APPROVAL_STALE',
      `Unknown approval evidence: ${approvalId}`);
    return value;
  }

  validate(evidence, {plan, expectedBinding, approvalPolicy}) {
    const stored = this.get(evidence?.approvalId); const planValue = validateAIAsset('AIPlan', plan);
    const target = binding(expectedBinding); const policy = validateAIAsset(
      'AIApprovalPolicy', approvalPolicy); const rule = riskRule(policy, target.riskClass);
    assertPlanBinding(planValue, target);
    const current = stored === evidence && !stored.revoked && stored.approvalEpoch ===
      this.approvalEpoch() && Date.parse(stored.expiresAt) >= Date.parse(this.now()) &&
      stored.planId === planValue.planId && stored.planRevision === planValue.revision &&
      stored.planHash === aiPlanHash(planValue) && sameBinding(target, {
      requirementId: stored.requirementId, stepIds: stored.stepIds,
      connectorRef: stored.connectorRef, principalRef: stored.principalRef,
      resourceRefs: stored.resourceRefs, environment: stored.environment,
      riskClass: stored.riskClass,
    });
    if(!current) throw new AIExecutionFailure('APPROVAL_STALE',
      'Approval evidence is expired, revoked, changed, or bound to another plan target.');
    requiredMethod(rule, stored.confirmationMethod); return stored;
  }

  coverage({plan, requirements, approvalPolicy}) {
    const planValue = validateAIAsset('AIPlan', plan); const policy = validateAIAsset(
      'AIApprovalPolicy', approvalPolicy);
    const details = requirements.map((item) => {
      const target = binding(item); const rule = riskRule(policy, target.riskClass);
      const valid = [...this.approvals.values()].filter((evidence) => {
        try { this.validate(evidence, {plan: planValue, expectedBinding: target,
          approvalPolicy: policy}); return true; } catch { return false; }
      });
      const distinctApprovers = new Set(valid.map((item) => item.approver)).size;
      const complete = rule === 'dual_approval' ? distinctApprovers >= 2 :
        rule === 'dual_or_typed' ? valid.some((item) => item.confirmationMethod ===
          'typed_confirm') || distinctApprovers >= 2 : valid.length >= 1;
      return immutable({requirementId: target.requirementId, rule,
        approvalRefs: valid.map((item) => item.approvalId), distinctApprovers, complete});
    });
    return immutable({complete: details.every((item) => item.complete), requirements: details});
  }

  authorizationCheck({operation, approvalPolicy}={}) {
    try {
      const lifecycle = operation?.plan; const plan = lifecycle?.plan;
      const validation = lifecycle?.validations?.find((item) => item.stepId ===
        operation.operationId);
      if(!plan || !validation?.approvalRequired || validation.riskClass !== operation.riskClass)
        throw new AIExecutionFailure('APPROVAL_STALE',
          'No exact validated plan step matches the authorization operation.');
      const supplied = Array.isArray(operation.approvalEvidence) ? operation.approvalEvidence :
        operation.approvalEvidence == null ? [] : [operation.approvalEvidence];
      const valid = supplied.map((evidence) => this.validate(evidence, {plan,
        expectedBinding: validation.approvalBinding, approvalPolicy}));
      const rule = riskRule(validateAIAsset('AIApprovalPolicy', approvalPolicy),
        validation.approvalBinding.riskClass);
      const distinct = new Set(valid.map((item) => item.approver)).size;
      const complete = rule === 'dual_approval' ? distinct >= 2 : rule === 'dual_or_typed' ?
        distinct >= 2 || valid.some((item) => item.confirmationMethod === 'typed_confirm') :
        valid.length >= 1;
      if(!complete) throw new AIExecutionFailure('APPROVAL_REQUIRED',
        'The supplied approval evidence does not satisfy this exact plan step.');
      return immutable({allowed: true, reason: '', evidenceRef:
        `ai-approval-coverage:${validation.approvalBinding.requirementId}`});
    } catch(error) { return immutable({allowed: false, reason: error.message,
      evidenceRef: null}); }
  }

  invalidate({planId=null, approvalIds=null, reason, initiator='system'}={}) {
    const message = platformValue(reason, 'AI approval invalidation reason', 2000);
    const selected = approvalIds == null ? null : new Set(boundedStrings(approvalIds,
      'AI approval invalidation IDs'));
    const invalidated = [];
    for(const [id, value] of this.approvals.entries()) if(!value.revoked &&
      (!planId || value.planId === planId) && (!selected || selected.has(id))) {
      this.approvals.set(id, immutable({...value, revoked: true})); invalidated.push(id);
    }
    if(invalidated.length) this.audit?.append({eventType: 'ai.approval.invalidated', initiator,
      planId, approvalRefs: invalidated, diagnostics: [{message}]});
    return immutable(invalidated);
  }
}
