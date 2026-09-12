/////////////////////////////////////////////////////////////
// CDEadmin bounded background AI-run TaskService authority.
/////////////////////////////////////////////////////////////

import {immutable, noRawSecrets, plainObject, platformValue} from '../../platform/serviceUtils';
import {validateAIAsset} from './AIAssetContracts';
import {AIExecutionFailure, aiActor, boundedStrings, exactLifecycleObject} from './AILifecycleContracts';

export const AI_BACKGROUND_RUN_TASK = 'cdeadmin.ai.background-run.v1';
const TURN_STATES = Object.freeze(['continue', 'completed', 'approval_required', 'failed']);

function turnResult(input) {
  exactLifecycleObject(input, ['status', 'resultRefs', 'taskRefs', 'nextState', 'diagnostics'],
    'AI background turn result');
  if(!TURN_STATES.includes(input.status)) throw new TypeError(
    'AI background turn status is invalid.');
  if(input.nextState != null) { plainObject(input.nextState, 'AI background next state');
    noRawSecrets(input.nextState, 'AI background next state'); }
  if(!Array.isArray(input.diagnostics)) throw new TypeError(
    'AI background turn diagnostics must be an array.');
  return immutable({status: input.status, resultRefs: boundedStrings(input.resultRefs ?? [],
    'AI background result references'), taskRefs: boundedStrings(input.taskRefs ?? [],
    'AI background task references'), nextState: input.nextState == null ? null :
    immutable({...input.nextState}), diagnostics: immutable(input.diagnostics.map((item) =>
    immutable({...plainObject(item, 'AI background diagnostic')})))});
}

export class AIBackgroundRunService {
  constructor({tasks, runTurn, emergency=null, audit=null, clock=() => Date.now(),
    siteMaxMinutes=1440, siteMaxTurns=100, siteMaxBackgroundTasks=10}={}) {
    if(!tasks || typeof tasks.register !== 'function' || typeof runTurn !== 'function') throw new TypeError(
      'AI background run requires Task and model-turn authorities.');
    if(!Number.isInteger(siteMaxMinutes) || siteMaxMinutes < 1 ||
        !Number.isInteger(siteMaxTurns) || siteMaxTurns < 1 ||
        !Number.isInteger(siteMaxBackgroundTasks) || siteMaxBackgroundTasks < 1) throw new TypeError(
      'AI background site limits must be positive integers.');
    this.tasks = tasks; this.runTurn = runTurn; this.emergency = emergency;
    this.audit = audit; this.clock = clock; this.siteMaxMinutes = siteMaxMinutes;
    this.siteMaxTurns = siteMaxTurns; this.siteMaxBackgroundTasks = siteMaxBackgroundTasks;
    this.sequence = 0; this.runs = new Map();
    this.unregisterTask = tasks.register(AI_BACKGROUND_RUN_TASK, (request, context) =>
      this._run(request, context));
  }

  dispose() { this.unregisterTask?.(); this.unregisterTask = null; }

  start(input, context={}) {
    exactLifecycleObject(input, ['agentProfile', 'toolPolicy', 'budgetPolicy', 'goal',
      'contextRefs', 'maxMinutes', 'stopOnApproval'], 'AI background run request');
    const actor = aiActor(context, 'ai.use'); const agent = validateAIAsset(
      'AIAgentProfile', input.agentProfile); const toolPolicy = validateAIAsset(
      'AIToolPolicy', input.toolPolicy); const budget = validateAIAsset(
      'AIBudgetPolicy', input.budgetPolicy);
    if(!agent.enabled || agent.autonomyMode !== 'BOUNDED_EXECUTION' ||
        !toolPolicy.allowBackgroundTasks) throw new AIExecutionFailure('POLICY_DENIED',
      'The agent/tool policy does not allow bounded background execution.');
    const goal = platformValue(input.goal, 'AI background goal', 8000);
    if(!Number.isInteger(input.maxMinutes) || input.maxMinutes < 1 ||
        input.maxMinutes > this.siteMaxMinutes || budget.maxRunMinutes !== null &&
        input.maxMinutes > budget.maxRunMinutes) throw new AIExecutionFailure('BUDGET_EXCEEDED',
      'AI background duration exceeds its explicit budget.');
    const maximumRuns = Math.min(this.siteMaxBackgroundTasks,
      budget.maxBackgroundTasks ?? this.siteMaxBackgroundTasks);
    if(this.runs.size >= maximumRuns) throw new AIExecutionFailure('BUDGET_EXCEEDED',
      'AI background task concurrency exceeds its explicit budget.');
    if(typeof input.stopOnApproval !== 'boolean') throw new TypeError(
      'AI background approval-stop policy must be explicit.');
    const maximumTurns = Math.min(this.siteMaxTurns, budget.maxToolCallsPerPlan ?? this.siteMaxTurns);
    const runId = `ai-background-run-${++this.sequence}`; const taskId = `ai-background-task-${this.sequence}`;
    const request = {id: taskId, type: AI_BACKGROUND_RUN_TASK, runId,
      label: `Bounded AI run: ${goal.slice(0, 80)}`, goal, agentProfile: agent,
      contextRefs: boundedStrings(input.contextRefs ?? [], 'AI background context references'),
      maxMinutes: input.maxMinutes, maximumTurns, stopOnApproval: input.stopOnApproval,
      retry: {maximum: 0}, cancelable: true, resumable: false,
      resourceRefs: [], assetRefs: [], audit: {runId, agentProfileRef: agent.profileId}};
    const task = this.tasks.submit(request, {owner: actor.id, executionContext: context});
    const unregisterEmergency = this.emergency?.registerRun(task.id,
      () => this.tasks.cancel(task.id), [agent.profileId, ...agent.connectorRefs]);
    this.runs.set(runId, {runId, taskId: task.id, unregisterEmergency});
    this.tasks.wait(task.id).then(() => this._finished(runId), () => this._finished(runId));
    this.audit?.append({eventType: 'ai.background_run.started', initiator: actor.id,
      runId, agentProfileRef: agent.profileId, resourceRefs: request.contextRefs,
      taskRefs: [task.id], content: {userRequest: goal}});
    return immutable({schema: 'cdeadmin.ai-run-ref.v1', runId, taskRef: task.id,
      agentProfileRef: agent.profileId, state: 'running'});
  }

  _finished(runId) { const run = this.runs.get(runId); run?.unregisterEmergency?.();
    this.runs.delete(runId); }

  cancel(runId, reason, context={}) {
    const actor = aiActor(context, 'ai.use'); const run = this.runs.get(String(runId));
    if(!run) return false; reason = platformValue(reason, 'AI background cancellation reason', 2000);
    const result = this.tasks.cancel(run.taskId); this.audit?.append({eventType:
      'ai.background_run.cancel_requested', initiator: actor.id, runId, taskRefs: [run.taskId],
    diagnostics: [{message: reason, requested: result}]}); return result;
  }

  async _run(request, taskContext) {
    const deadline = this.clock() + request.maxMinutes * 60000; let state = null;
    const resultRefs = []; const taskRefs = [];
    for(let turn = 1; turn <= request.maximumTurns; turn++) {
      if(taskContext.signal.aborted) throw new AIExecutionFailure('TASK_FAILED',
        'AI background run was cancelled.');
      if(this.clock() >= deadline) throw new AIExecutionFailure('BUDGET_EXCEEDED',
        'AI background run reached its time budget.');
      taskContext.phase(`turn:${turn}/${request.maximumTurns}`, 'Running bounded AI turn');
      const result = turnResult(await this.runTurn({runId: request.runId, goal: request.goal,
        agentProfile: request.agentProfile, contextRefs: request.contextRefs, priorState: state,
        turn, maximumTurns: request.maximumTurns, deadline}, {...taskContext,
        executionContext: taskContext.executionContext ?? {}}));
      state = result.nextState; resultRefs.push(...result.resultRefs); taskRefs.push(...result.taskRefs);
      result.diagnostics.forEach((item) => taskContext.diagnostic(item));
      result.taskRefs.forEach((reference) => taskContext.resultReference({type: 'TaskRef', reference}));
      result.resultRefs.forEach((reference) => taskContext.resultReference({type: 'ResultRef', reference}));
      taskContext.progress(turn / request.maximumTurns, `Completed AI turn ${turn}`);
      if(result.status === 'approval_required') return immutable({schema:
        'cdeadmin.ai-background-run-result.v1', status: 'approval_required', runId: request.runId,
      stoppedForApproval: true, resultRefs: [...new Set(resultRefs)], taskRefs: [...new Set(taskRefs)],
      turns: turn});
      if(result.status === 'failed') throw new AIExecutionFailure('MODEL_PROVIDER_FAILURE',
        'AI background run turn failed.', {diagnostics: result.diagnostics});
      if(result.status === 'completed') return immutable({schema:
        'cdeadmin.ai-background-run-result.v1', status: 'success', runId: request.runId,
      stoppedForApproval: false, resultRefs: [...new Set(resultRefs)], taskRefs: [...new Set(taskRefs)],
      turns: turn});
    }
    throw new AIExecutionFailure('BUDGET_EXCEEDED',
      'AI background run reached its bounded turn limit.');
  }
}

export {turnResult as validateAIBackgroundTurnResult};
