/////////////////////////////////////////////////////////////
// CDEadmin TaskService-backed AI plan execution authority.
/////////////////////////////////////////////////////////////

import {abortError, immutable, noRawSecrets, plainObject, platformValue} from '../../platform/serviceUtils';
import {
  AIExecutionFailure, aiActor, boundedStrings, exactLifecycleObject,
  normalizeExecutionFailure,
} from './AILifecycleContracts';
import {AIRetryPolicyService} from './AIRetryPolicyService';

export const AI_PLAN_EXECUTION_TASK = 'cdeadmin.ai.plan.execution.v1';
const RESULT_STATUSES = Object.freeze(['success', 'partial', 'refused', 'failed',
  'approval_required']);

function resultEnvelope(input) {
  exactLifecycleObject(input, ['status', 'result', 'diagnostics', 'resourceRefs', 'assetRefs',
    'taskRefs', 'nextAllowedActions', 'auditRef'], 'AI execution result');
  if(!RESULT_STATUSES.includes(input.status)) throw new TypeError('AI result status is invalid.');
  if(input.result != null) noRawSecrets(input.result, 'AI execution result payload');
  if(!Array.isArray(input.diagnostics)) throw new TypeError('AI result diagnostics must be an array.');
  input.diagnostics.forEach((item) => plainObject(item, 'AI result diagnostic'));
  return immutable({schema: 'cdeadmin.ai-tool-result.v1', status: input.status,
    result: input.result ?? null, diagnostics: input.diagnostics.map((item) => immutable({...item})),
    resourceRefs: boundedStrings(input.resourceRefs ?? [], 'AI result resource references'),
    assetRefs: boundedStrings(input.assetRefs ?? [], 'AI result asset references'),
    taskRefs: boundedStrings(input.taskRefs ?? [], 'AI result task references'),
    nextAllowedActions: boundedStrings(input.nextAllowedActions ?? [],
      'AI result next actions'), auditRef: input.auditRef == null ? null : platformValue(
      input.auditRef, 'AI result audit reference')});
}

function failureForResult(result) {
  if(result.status === 'partial') return new AIExecutionFailure('PARTIAL_RESULT',
    'AI plan step returned a partial result.', {diagnostics: result.diagnostics});
  if(result.status === 'approval_required') return new AIExecutionFailure('APPROVAL_REQUIRED',
    'AI plan step requires new approval.', {diagnostics: result.diagnostics});
  if(result.status === 'refused') return new AIExecutionFailure('POLICY_DENIED',
    'AI plan step was refused.', {diagnostics: result.diagnostics});
  return new AIExecutionFailure('PROVIDER_ERROR', 'AI plan step failed.',
    {diagnostics: result.diagnostics});
}

export class AIPlanExecutionService {
  constructor({plans, tasks, executeStep, cancelStep=async () => false,
    retry=new AIRetryPolicyService(), emergency=null, audit=null,
    now=() => new Date().toISOString()}={}) {
    if(!plans || typeof plans.executionSnapshot !== 'function' || !tasks ||
        typeof tasks.register !== 'function' || typeof tasks.submit !== 'function') throw new TypeError(
      'AI execution requires Plan and Task services.');
    if(typeof executeStep !== 'function' || typeof cancelStep !== 'function') throw new TypeError(
      'AI execution and cancellation authorities are required.');
    this.plans = plans; this.tasks = tasks; this.executeStep = executeStep;
    this.cancelStep = cancelStep; this.retry = retry; this.emergency = emergency;
    this.audit = audit; this.now = now; this.sequence = 0; this.runs = new Map();
    this.unregisterTask = tasks.register(AI_PLAN_EXECUTION_TASK, (request, taskContext) =>
      this._run(request, taskContext));
  }

  dispose() { this.unregisterTask?.(); this.unregisterTask = null; }

  async execute(planId, input={}, context={}) {
    exactLifecycleObject(input, ['label'], 'AI plan execution request');
    const actor = aiActor(context, 'ai.delegate_write');
    const lifecycle = await this.plans.executionSnapshot(planId, context);
    const runId = `ai-run-${++this.sequence}`; const taskId = `ai-plan-task-${this.sequence}`;
    const request = {id: taskId, type: AI_PLAN_EXECUTION_TASK,
      label: input.label ?? `Execute AI plan ${planId}`, planId, planRevision:
      lifecycle.plan.revision, runId, resourceRefs: [...new Set(lifecycle.plan.steps.flatMap(
        (step) => step.resourceRefs))], assetRefs: [...new Set(lifecycle.plan.steps.flatMap(
        (step) => step.assetRefs))], retry: {maximum: 0}, cancelable: true, resumable: false,
      audit: {planId, runId}};
    const task = this.tasks.submit(request, {owner: actor.id, executionContext: context});
    this.plans.markRunning(planId, task.id);
    const unregisterEmergency = this.emergency?.registerRun(task.id,
      () => this.tasks.cancel(task.id), [planId, ...lifecycle.plan.connectorSnapshots.map(
        (item) => String(item.connectorId ?? item.connectorRef ?? item.id ?? '')).filter(Boolean)]);
    this.runs.set(runId, {runId, taskId: task.id, planId, unregisterEmergency});
    this.tasks.wait(task.id).then(() => { unregisterEmergency?.(); this.runs.delete(runId); },
      () => { unregisterEmergency?.(); this.runs.delete(runId); });
    this.audit?.append({eventType: 'ai.plan.execution_started', initiator: actor.id,
      runId, planId, agentProfileRef: lifecycle.plan.agentProfileRef,
      resourceRefs: request.resourceRefs, assetRefs: request.assetRefs, taskRefs: [task.id]});
    return immutable({schema: 'cdeadmin.ai-run-ref.v1', runId, planId,
      planRevision: lifecycle.plan.revision, taskRef: task.id, state: 'running'});
  }

  cancel(runId, reason, context={}) {
    const actor = aiActor(context, 'ai.use'); const run = this.runs.get(String(runId));
    if(!run) return false;
    reason = platformValue(reason, 'AI run cancellation reason', 2000);
    const requested = this.tasks.cancel(run.taskId);
    this.audit?.append({eventType: 'ai.run.cancel_requested', initiator: actor.id,
      runId: run.runId, planId: run.planId, taskRefs: [run.taskId],
      diagnostics: [{message: reason, requested}]}); return requested;
  }

  async _run(request, taskContext) {
    const context = taskContext.executionContext ?? {}; const lifecycle = this.plans.get(request.planId);
    if(lifecycle.plan.revision !== request.planRevision || lifecycle.plan.status !== 'running')
      throw new AIExecutionFailure('APPROVAL_STALE', 'AI plan changed before its task began.');
    const outputs = []; const startedAt = this.now(); let active = null;
    const onAbort = () => { if(active) Promise.resolve(this.cancelStep({plan: lifecycle.plan,
      step: active.step, preparedOperation: active.validation.preparedOperation,
      reason: 'Task cancellation requested.'}, context)).catch(() => false); };
    taskContext.signal.addEventListener('abort', onAbort);
    try {
      for(let index = 0; index < lifecycle.plan.steps.length; index++) {
        const step = lifecycle.plan.steps[index]; const validation = lifecycle.validations.find(
          (item) => item.stepId === step.stepId);
        if(!validation?.valid) throw new AIExecutionFailure('POLICY_DENIED',
          `AI plan step ${step.stepId} has no current successful validation.`);
        if(taskContext.signal.aborted) throw abortError('AI run was cancelled.');
        taskContext.phase(`step:${index + 1}/${lifecycle.plan.steps.length}`, step.stepId);
        if(['REQUEST_APPROVAL', 'VALIDATE'].includes(step.kind)) {
          this.plans.markStep(request.planId, step.stepId, 'succeeded');
          outputs.push(immutable({stepId: step.stepId, status: 'success', result: null})); continue;
        }
        this.plans.markStep(request.planId, step.stepId, 'running'); active = {step, validation};
        const retryOptions = {...validation.retry, signal: taskContext.signal,
          onRetry: (details) => taskContext.warning(`AI step ${step.stepId} retry`, details)};
        let result;
        if(step.kind === 'WAIT_TASK') {
          const taskRef = platformValue(step.arguments.taskRef ?? step.arguments.taskId,
            'AI wait task reference');
          try { result = resultEnvelope({status: 'success', result: await this.tasks.wait(taskRef),
            diagnostics: [], resourceRefs: [], assetRefs: [], taskRefs: [taskRef],
            nextAllowedActions: [], auditRef: null}); } catch(error) {
            throw new AIExecutionFailure('TASK_FAILED', error.message, {cause: error});
          }
        } else result = await this.retry.run(validation.retry.kind, async ({attempt, signal}) =>
          resultEnvelope(await this.executeStep({plan: lifecycle.plan, step,
            preparedOperation: validation.preparedOperation, approvalEvidence:
            this._approvalEvidence(lifecycle, validation), attempt, signal}, context)), retryOptions);
        active = null;
        if(result.status !== 'success') throw failureForResult(result);
        result.taskRefs.forEach((reference) => taskContext.resultReference({type: 'TaskRef',
          reference})); result.resourceRefs.forEach((reference) => taskContext.resultReference({
          type: 'ResourceRef', reference})); result.assetRefs.forEach((reference) =>
          taskContext.resultReference({type: 'AssetRef', reference}));
        this.plans.markStep(request.planId, step.stepId, 'succeeded');
        outputs.push(immutable({stepId: step.stepId, ...result}));
        taskContext.progress((index + 1) / lifecycle.plan.steps.length, step.stepId);
        this.audit?.append({eventType: 'ai.plan.step_executed', initiator:
          String(taskContext.owner ?? 'system'), runId: request.runId, planId: request.planId,
        connectorRef: step.connectorRef, commandId: step.commandId,
        principalRef: validation.approvalBinding?.principalRef ?? null,
        redactedArguments: step.arguments, resourceRefs: step.resourceRefs,
        assetRefs: step.assetRefs, approvalRefs: this._approvalEvidence(lifecycle,
          validation).map((item) => item.approvalId), backendResult: {status: result.status,
          diagnostics: result.diagnostics}, taskRefs: result.taskRefs});
      }
      this.plans.markTerminal(request.planId, 'succeeded');
      this.audit?.append({eventType: 'ai.plan.execution_finished', initiator:
        String(taskContext.owner ?? 'system'), runId: request.runId, planId: request.planId,
      taskRefs: [request.id], timing: {startedAt, finishedAt: this.now()},
      backendResult: {status: 'success', stepCount: outputs.length}});
      return immutable({schema: 'cdeadmin.ai-plan-execution-result.v1', status: 'success',
        planId: request.planId, runId: request.runId, steps: outputs});
    } catch(error) {
      const cancelled = taskContext.signal.aborted || error?.name === 'AbortError';
      const failure = cancelled ? error : normalizeExecutionFailure(error);
      if(active) this.plans.markStep(request.planId, active.step.stepId,
        cancelled ? 'cancelled' : 'failed');
      this.plans.markTerminal(request.planId, cancelled ? 'cancelled' : 'failed',
        failure.message);
      this.audit?.append({eventType: cancelled ? 'ai.plan.execution_cancelled' :
        'ai.plan.execution_failed', initiator: String(taskContext.owner ?? 'system'),
      runId: request.runId, planId: request.planId, taskRefs: [request.id],
      diagnostics: [{category: cancelled ? 'TASK_FAILED' : failure.category,
        message: failure.message}], timing: {startedAt, finishedAt: this.now()}});
      throw failure;
    } finally { taskContext.signal.removeEventListener('abort', onAbort); }
  }

  _approvalEvidence(lifecycle, validation) {
    if(!validation.approvalRequired) return immutable([]);
    const coverage = lifecycle.approvalCoverage?.requirements.find((item) =>
      item.requirementId === validation.approvalBinding.requirementId);
    return immutable((coverage?.approvalRefs ?? []).map((id) => this.plans.approvals.get(id)));
  }
}

export {resultEnvelope as validateAIExecutionResult};
