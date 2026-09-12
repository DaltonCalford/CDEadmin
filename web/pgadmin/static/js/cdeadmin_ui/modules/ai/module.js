/////////////////////////////////////////////////////////////
// First-party governed AI Assistant registration boundary.
/////////////////////////////////////////////////////////////

import React from 'react';
import PropTypes from 'prop-types';
import {Box} from '@mui/material';
import {ProjectAssetClient} from '../../projects/ProjectAssetClient';
import {PLATFORM_SERVICE_IDS} from '../../platform/PlatformServices';
import {PROJECT_ASSET_SERVICE_ID} from '../../integrations/ddn/module';
import {
  AI_ASSET_TYPE, AI_MODULE_ID, AI_SERVICE_ID, validateActionPlan, validateContextScope,
} from './contracts';
import {AIAdapterRegistry, AIService} from './AIService';
import {AI_SURFACES, AINavigator, AIWorkspace, aiInspector} from './AIWorkspace';

function only(args, fields) {
  const unknown = Object.keys(args ?? {}).filter((field) => !fields.includes(field));
  return unknown.length ? `Unknown command argument: ${unknown[0]}.` : true;
}
function required(value, label) {
  return typeof value === 'string' && value.trim() ? true : `${label} is required.`;
}
function runtime(args, context) {
  const service = context.ai ?? context.services?.[AI_SERVICE_ID] ?? context.service;
  if(!service) throw new Error('AI Assistant runtime service is unavailable.');
  const id = args.sessionId ?? context.sessionId;
  if(!id) throw new Error('AI Assistant asset session ID is required.');
  return {service, id};
}
function command(input) {
  return {iconKey: 'tool.ai', surfaces: ['tools', 'toolbar'], macroCallable: false,
    aiEligible: false, auditCategory: 'ai_assistant', validateArguments: () => true,
    enabledWhen: () => true, visibleWhen: () => true,
    disabledReason: 'AI Assistant action is unavailable.', allowedSecurityGroups: [],
    deniedSecurityGroups: [], createsTask: '', confirmationIntent: 'none', ...input};
}
function definitions() {
  return [
    command({id: 'ai.session.new', label: 'New AI Session', permission: ['ai.use'],
      validateArguments: (args) => only(args, ['sessionId', 'id', 'projectId', 'mode', 'contextScopeIds']),
      execute: (args, context) => { const {service, id} = runtime(args, context);
        return service.newAssistantSession(id, args, context); }}),
    command({id: 'ai.context.add', label: 'Add AI Context', permission: ['ai.use'],
      validateArguments: (args) => { const known = only(args, ['sessionId', 'scope']);
        if(known !== true) return known; try { validateContextScope(args.scope); return true; }
        catch(error) { return error.message; }},
      execute: (args, context) => { const {service, id} = runtime(args, context);
        return service.addContext(id, args.scope, context); }}),
    command({id: 'ai.context.remove', label: 'Remove AI Context', permission: ['ai.use'],
      validateArguments: (args) => { const known = only(args, ['sessionId', 'scopeId']);
        return known === true ? required(args.scopeId, 'Context scope ID') : known; },
      execute: (args, context) => { const {service, id} = runtime(args, context);
        return service.removeContext(id, args.scopeId, context); }}),
    command({id: 'ai.ask', label: 'Ask AI Assistant', permission: ['ai.use'],
      validateArguments: (args) => { const known = only(args, ['sessionId', 'prompt', 'mode']);
        return known === true ? required(args.prompt, 'Prompt') : known; },
      execute: (args, context) => { const {service, id} = runtime(args, context);
        return service.ask(id, args.prompt, {mode: args.mode}, context); }}),
    command({id: 'ai.plan.create', label: 'Create AI Plan', permission: ['ai.propose'],
      validateArguments: (args) => { const known = only(args, ['sessionId', 'plan']);
        if(known !== true) return known; try { validateActionPlan(args.plan); return true; }
        catch(error) { return error.message; }},
      execute: (args, context) => { const {service, id} = runtime(args, context);
        return service.createPlan(id, args.plan, context); }}),
    command({id: 'ai.plan.validate', label: 'Validate AI Plan', permission: ['ai.propose'],
      validateArguments: (args) => { const known = only(args, ['sessionId', 'planId', 'currentRevision']);
        return known === true ? required(args.planId, 'Plan ID') : known; },
      execute: (args, context) => { const {service, id} = runtime(args, context);
        return service.validatePlan(id, args.planId, {currentRevision: args.currentRevision}, context); }}),
    command({id: 'ai.action.approve', label: 'Approve AI Action', permission: ['ai.execute_approved'],
      validateArguments: (args) => {
        const known = only(args, ['sessionId', 'planId', 'actionIds', 'currentRevision', 'reason']);
        if(known !== true) return known; return required(args.planId, 'Plan ID') === true &&
          Array.isArray(args.actionIds) && args.actionIds.length && args.currentRevision != null ? true :
          'Plan, actions and current target revision are required.'; },
      execute: (args, context) => { const {service, id} = runtime(args, context);
        return service.approveAction(id, args.planId, args.actionIds, args, context); }}),
    command({id: 'ai.action.reject', label: 'Reject AI Action', permission: ['ai.execute_approved'],
      validateArguments: (args) => { const known = only(args,
        ['sessionId', 'planId', 'actionIds', 'currentRevision', 'reason']);
      return known === true && required(args.planId, 'Plan ID') === true && Array.isArray(args.actionIds) &&
        args.actionIds.length && args.currentRevision != null ? true :
        (known === true ? 'Plan, actions and current target revision are required.' : known); },
      execute: (args, context) => { const {service, id} = runtime(args, context);
        return service.rejectAction(id, args.planId, args.actionIds, args, context); }}),
    command({id: 'ai.plan.execute', label: 'Execute Approved AI Plan',
      permission: ['ai.execute_approved'], createsTask: 'ai.plan.execution',
      confirmationIntent: 'standard_consequential', validateArguments: (args) => {
        const known = only(args, ['sessionId', 'planId', 'currentRevision', 'confirmationRef',
          'environment', 'connection']); if(known !== true) return known;
        return ['planId', 'confirmationRef', 'environment', 'connection'].every((field) =>
          required(args[field], field) === true) && args.currentRevision != null ? true :
          'Plan, revision and target-bound confirmation are required.'; },
      execute: (args, context) => { const {service, id} = runtime(args, context);
        return service.executePlan(id, args.planId, args, context); }}),
    command({id: 'ai.output.save_as_asset', label: 'Save AI Output as Asset', permission: ['ai.propose'],
      validateArguments: (args) => { const known = only(args, ['sessionId', 'outputId', 'projectId',
        'assetType', 'schemaName', 'name', 'path']); if(known !== true) return known;
      return ['outputId', 'projectId', 'assetType', 'name', 'path'].every((field) =>
        required(args[field], field) === true) ? true : 'Output asset destination is incomplete.'; },
      execute: (args, context) => { const {service, id} = runtime(args, context);
        return service.saveOutputAsAsset(id, args.outputId, args, context); }}),
  ];
}

function surfaceRuntime(registry, descriptor, context) {
  return registry.resolve(AI_SERVICE_ID).then(async (service) => {
    let sessionId = descriptor.restoreRef || descriptor.context?.sessionId;
    if(descriptor.projectId && descriptor.restoreRef) {
      const client = context.services?.[PROJECT_ASSET_SERVICE_ID] ??
        await registry.resolve(PROJECT_ASSET_SERVICE_ID);
      const asset = await client.asset(descriptor.projectId, descriptor.restoreRef);
      if(asset.asset_type !== AI_ASSET_TYPE) throw new Error('The requested project asset is not an AI asset.');
      sessionId = `ai:${descriptor.projectId}:${descriptor.restoreRef}`;
      try { service.get(sessionId); } catch { service.create({id: sessionId, content: asset.content}); }
    }
    if(!sessionId) sessionId = service.create({}).id;
    try { service.get(sessionId); } catch { service.create({id: sessionId}); }
    const surface = descriptor.toolKind.replace('ai.', '');
    const executeCommand = (id, args, commandContext={}) => context.commands.execute(id,
      {...args, sessionId}, {...context, ...commandContext, service, ai: service, sessionId});
    return React.createElement(AIWorkspace, {service, sessionId, surface, executeCommand,
      currentUser: context.currentUser ?? {}, onStateChange: (session) => context.setSurfaceState?.({
        aiSession: session, aiInspector: aiInspector(session), dirty: session.dirty,
        persistence: session.dirty ? 'dirty' : 'clean', problems: session.problems,
        activeTaskId: session.activeTaskId})});
  });
}

export function aiModuleDefinition(registry) {
  return {id: AI_MODULE_ID, version: '1.0.0', title: 'AI Database Assistant', iconKey: 'tool.ai',
    serviceRequirements: [AI_SERVICE_ID], contributions: {surfaces: AI_SURFACES.map((surface) => ({
      id: `ai.${surface.id}`, title: surface.title, iconKey: 'tool.ai', assetTypes: [AI_ASSET_TYPE],
      readOnly: false, editable: true, detachable: true, duplicable: true, fullscreen: true,
      restore: (descriptor, context) => surfaceRuntime(registry, descriptor, context),
      checkpoint: async (_descriptor, context) => context.aiSession?.content ?? null,
      canClose: async (_descriptor, context) => context.dirty ? {allowed: false,
        reason: 'Save or discard AI asset changes first.'} : {allowed: true, reason: ''}})),
    commands: definitions(), activity: [{id: 'activity.ai', label: 'AI Assistant', iconKey: 'tool.ai',
      priority: 45, render: () => <AIActivity registry={registry} />}],
    inspector: [{id: 'inspector.ai', label: 'AI Assistant', priority: 45,
      when: (context) => context.surfaceId?.startsWith('ai.'),
      render: (context) => context.aiInspector ?? {}}],
    bottom: [{id: 'bottom.ai.plan', label: 'Plan', priority: 93,
      when: (context) => context.surfaceId?.startsWith('ai.'),
      render: (context) => context.aiSession?.content?.savedPlans ?? []},
    {id: 'bottom.ai.problems', label: 'Problems', priority: 94,
      when: (context) => context.surfaceId?.startsWith('ai.'),
      render: (context) => context.aiSession?.problems ?? []},
    {id: 'bottom.ai.tool-activity', label: 'Tool Activity', priority: 95,
      when: (context) => context.surfaceId?.startsWith('ai.'),
      render: (context) => context.aiSession?.runtime?.toolInvocations ?? []},
    {id: 'bottom.ai.tasks', label: 'Tasks', priority: 96,
      when: (context) => context.surfaceId?.startsWith('ai.'),
      render: (context) => context.aiSession?.tasks ?? []}],
    status: [{id: 'status.ai', label: 'AI Assistant', priority: 45,
      when: (context) => context.surfaceId?.startsWith('ai.'),
      value: (context) => context.aiSession?.state ?? 'ready'}]}};
}

function AIActivity({registry}) {
  const [service, setService] = React.useState(null);
  React.useEffect(() => { registry.resolve(AI_SERVICE_ID).then(setService); }, [registry]);
  if(!service) return <Box sx={{p: 1}}>Loading AI assets…</Box>;
  return <AINavigator service={service} onOpen={(sessionId, surface) => window.dispatchEvent(
    new CustomEvent('cdeadmin:ai-open', {detail: {sessionId, surface}}))} />;
}
AIActivity.propTypes = {registry: PropTypes.object};

export function registerAIModule({modules, services, api, adapters=new AIAdapterRegistry(),
  modelProfileResolver, contextReader}={}) {
  if(!modules || !services) throw new TypeError('AI registration requires module and service registries.');
  const removers = [];
  if(!services.has(PROJECT_ASSET_SERVICE_ID)) removers.push(services.register({id: PROJECT_ASSET_SERVICE_ID,
    version: '1.0.0', factory: () => new ProjectAssetClient(api)}));
  if(!services.has(AI_SERVICE_ID)) removers.push(services.register({id: AI_SERVICE_ID, version: '1.0.0',
    dependencies: [PLATFORM_SERVICE_IDS.TASKS, PLATFORM_SERVICE_IDS.RELATIONSHIPS,
      PLATFORM_SERVICE_IDS.SEARCH, PROJECT_ASSET_SERVICE_ID], factory: ({services: resolved}) => new AIService({
      tasks: resolved[PLATFORM_SERVICE_IDS.TASKS], relationships: resolved[PLATFORM_SERVICE_IDS.RELATIONSHIPS],
      search: resolved[PLATFORM_SERVICE_IDS.SEARCH], projectAssets: resolved[PROJECT_ASSET_SERVICE_ID],
      commands: modules.commands, adapters, modelProfileResolver, contextReader})}));
  const removeModule = modules.register(aiModuleDefinition(services));
  return () => { removeModule(); removers.reverse().forEach((remove) => remove()); };
}
