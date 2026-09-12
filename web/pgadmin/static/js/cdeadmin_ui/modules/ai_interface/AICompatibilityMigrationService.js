/////////////////////////////////////////////////////////////
// CDEadmin precursor AI asset compatibility migration authority.
/////////////////////////////////////////////////////////////

import {immutable, noRawSecrets, platformValue} from '../../platform/serviceUtils';
import {aiReferenceKey, createAIContent} from '../ai/contracts';
import {AI_ASSET_KINDS, validateAIAsset} from './AIAssetContracts';
import {AIExecutionFailure, aiActor, exactLifecycleObject} from './AILifecycleContracts';

const PACKAGE_FIELDS = Object.freeze(['legacyContent', 'modelProfile', 'connectorProfiles',
  'agentProfile', 'toolPolicy', 'dataPolicy', 'approvalPolicy', 'budgetPolicy',
  'retentionPolicy', 'instructionAsset', 'plans']);

function exactSet(left, right) {
  return left.length === right.length && [...left].sort().every((item, index) =>
    item === [...right].sort()[index]);
}

function validatePlanMapping(legacy, migrated) {
  if(migrated.status !== 'draft' || migrated.planId !== legacy.id ||
      migrated.revision !== legacy.revision || migrated.baseContextRevision !==
      legacy.targetRevision || migrated.steps.length !== legacy.actions.length)
    throw new TypeError(`Migrated plan ${legacy.id} does not preserve its exact identity/revision.`);
  legacy.actions.forEach((action) => {
    const step = migrated.steps.find((item) => item.stepId === action.id);
    const expectedKind = {proposed_command: 'INVOKE_COMMAND', proposed_task: 'START_TASK',
      draft_asset_change: 'CREATE_ASSET_DRAFT'}[action.type];
    if(!step || step.kind !== expectedKind || action.commandId && step.commandId !== action.commandId ||
        action.taskType && step.arguments.taskType !== action.taskType ||
        action.assetChangeRef && !step.assetRefs.includes(aiReferenceKey(action.assetChangeRef)) ||
        !step.resourceRefs.includes(aiReferenceKey(action.targetRef))) throw new TypeError(
      `Migrated plan step ${action.id} does not preserve its precursor action target.`);
  });
}

export class AICompatibilityMigrationService {
  constructor({assets, audit=null}={}) {
    if(!assets || typeof assets.save !== 'function' || typeof assets.remove !== 'function')
      throw new TypeError('AI compatibility migration requires AI Asset authority.');
    this.assets = assets; this.audit = audit;
  }

  prepare(input) {
    exactLifecycleObject(input, PACKAGE_FIELDS, 'AI compatibility migration');
    const legacy = createAIContent(input.legacyContent);
    if(!Array.isArray(input.connectorProfiles) || !input.connectorProfiles.length ||
        !Array.isArray(input.plans)) throw new TypeError(
      'AI migration requires connector profiles and an explicit plan mapping array.');
    const content = {AIModelProfile: validateAIAsset('AIModelProfile', input.modelProfile),
      AIConnectorProfile: input.connectorProfiles.map((item) => validateAIAsset(
        'AIConnectorProfile', item)), AIAgentProfile: validateAIAsset('AIAgentProfile',
        input.agentProfile), AIToolPolicy: validateAIAsset('AIToolPolicy', input.toolPolicy),
      AIDataPolicy: validateAIAsset('AIDataPolicy', input.dataPolicy),
      AIApprovalPolicy: validateAIAsset('AIApprovalPolicy', input.approvalPolicy),
      AIBudgetPolicy: validateAIAsset('AIBudgetPolicy', input.budgetPolicy),
      AIRetentionPolicy: validateAIAsset('AIRetentionPolicy', input.retentionPolicy),
      AIInstructionAsset: input.instructionAsset == null ? null : validateAIAsset(
        'AIInstructionAsset', input.instructionAsset), AIPlan: input.plans.map((item) =>
        validateAIAsset('AIPlan', item))};
    const connectorIds = content.AIConnectorProfile.map((item) => item.connectorId);
    const agent = content.AIAgentProfile;
    if(agent.modelProfileRef !== content.AIModelProfile.profileId ||
        !exactSet(agent.connectorRefs, connectorIds) || agent.toolPolicyRef !==
        content.AIToolPolicy.policyId || agent.dataPolicyRef !== content.AIDataPolicy.policyId ||
        agent.approvalPolicyRef !== content.AIApprovalPolicy.policyId ||
        agent.budgetPolicyRef !== content.AIBudgetPolicy.policyId || agent.retentionPolicyRef !==
        content.AIRetentionPolicy.policyId || agent.instructionAssetRef !==
        (content.AIInstructionAsset?.instructionId ?? null)) throw new TypeError(
      'Migrated agent references do not resolve to the supplied independent assets.');
    const precursorTools = [...legacy.sessionPolicy.allowedReadToolIds,
      ...legacy.sessionPolicy.allowedProposalCommandIds];
    if(precursorTools.some((item) => !content.AIToolPolicy.commandIds.includes(item)) ||
        content.AIBudgetPolicy.maxToolCallsPerPlan !== null &&
        content.AIBudgetPolicy.maxToolCallsPerPlan < legacy.sessionPolicy.maximumPlanSteps)
      throw new TypeError('Migrated tool/budget policies narrow a precursor plan incompatibly.');
    if(content.AIRetentionPolicy.retainMessages !==
        legacy.conversationPersistencePolicy.persistMessages ||
        content.AIRetentionPolicy.retainMessages && content.AIRetentionPolicy.conversationDays !==
        legacy.conversationPersistencePolicy.retentionDays) throw new TypeError(
      'Migrated retention policy does not preserve the precursor conversation policy.');
    if(content.AIPlan.length !== legacy.savedPlans.length) throw new TypeError(
      'Every precursor plan requires one explicit migration mapping.');
    legacy.savedPlans.forEach((item) => validatePlanMapping(item,
      content.AIPlan.find((candidate) => candidate.planId === item.id) ?? {}));
    const records = [];
    for(const kind of AI_ASSET_KINDS) {
      const values = kind === 'AIConnectorProfile' || kind === 'AIPlan' ? content[kind] :
        content[kind] == null ? [] : [content[kind]];
      values.forEach((value) => records.push(immutable({kind, content: value})));
    }
    return immutable({schema: 'cdeadmin.ai-compatibility-migration.v1', sourceSchema:
      legacy.schema, sourceModuleId: legacy.moduleId, sourceName: legacy.name,
    records, legacyPreserved: true, validation: immutable({valid: true,
      assetKinds: [...new Set(records.map((item) => item.kind))], connectorCount:
        content.AIConnectorProfile.length, planCount: content.AIPlan.length})});
  }

  async migrate(projectId, input, context={}) {
    const actor = aiActor(context, 'ai.manage_agent_profiles');
    const permissions = new Set(actor.permissions);
    for(const permission of ['ai.manage_connectors', 'ai.manage_model_profiles',
      'ai.delegate_draft']) if(!permissions.has(permission)) throw new AIExecutionFailure(
      'AUTHORIZATION_DENIED', `AI migration requires ${permission}.`);
    projectId = platformValue(projectId, 'AI migration project ID'); const prepared = this.prepare(input);
    noRawSecrets(prepared, 'AI migration package'); const saved = [];
    try {
      for(const record of prepared.records) saved.push(await this.assets.save(projectId,
        record.kind, record.content, {expectedVersion: 0}));
    } catch(error) {
      const rollbackFailures = [];
      for(const asset of [...saved].reverse()) try { await this.assets.remove(projectId,
        asset.asset_id, asset.version); } catch(rollbackError) { rollbackFailures.push({
        assetId: asset.asset_id, message: rollbackError.message}); }
      this.audit?.append({eventType: 'ai.migration.failed', initiator: actor.id,
        assetRefs: saved.map((item) => item.asset_id), diagnostics: [{message: error.message},
          ...rollbackFailures]});
      throw new AIExecutionFailure('INTERNAL_ORCHESTRATOR_ERROR',
        rollbackFailures.length ? 'AI migration failed and rollback was incomplete.' :
          'AI migration failed and created assets were rolled back.',
        {cause: error, diagnostics: rollbackFailures});
    }
    this.audit?.append({eventType: 'ai.migration.completed', initiator: actor.id,
      agentProfileRef: input.agentProfile.profileId, assetRefs: saved.map((item) => item.asset_id),
      backendResult: {status: 'success', migratedAssetCount: saved.length,
        precursorPreserved: true}});
    return immutable({schema: 'cdeadmin.ai-compatibility-migration-result.v1',
      projectId, sourceSchema: prepared.sourceSchema, legacyPreserved: true,
      assets: saved});
  }
}
