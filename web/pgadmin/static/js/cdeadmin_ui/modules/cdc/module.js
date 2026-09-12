/////////////////////////////////////////////////////////////
// First-party CDC Designer registration and command boundary.
/////////////////////////////////////////////////////////////

import React from 'react';
import PropTypes from 'prop-types';
import {Box} from '@mui/material';
import {ProjectAssetClient} from '../../projects/ProjectAssetClient';
import {PLATFORM_SERVICE_IDS} from '../../platform/PlatformServices';
import {PROJECT_ASSET_SERVICE_ID} from '../../integrations/ddn/module';
import {
  CDC_ASSET_TYPE, CDC_MODULE_ID, createCDCContent, validateReplayRequest,
} from './contracts';
import {CDC_SERVICE_ID, CDCService} from './CDCService';
import {CDCNavigator, CDC_SURFACES, CDCWorkspace, cdcInspector} from './CDCWorkspace';

function onlyArguments(args, allowed) {
  const unexpected = Object.keys(args ?? {}).filter((name) => !allowed.includes(name));
  return unexpected.length ? `Unknown command argument: ${unexpected[0]}.` : true;
}
function sessionArguments(args, allowed=[]) {
  return onlyArguments(args, ['sessionId', ...allowed]);
}
function retryArguments(args, allowed=[]) {
  const known = sessionArguments(args, [...allowed, 'retry']); if(known !== true) return known;
  if(args.retry === undefined) return true;
  return args.retry && Object.keys(args.retry).every((key) => key === 'maximum') &&
    Number.isInteger(args.retry.maximum) && args.retry.maximum >= 0 && args.retry.maximum <= 5 ?
    true : 'Retry maximum must be an integer from zero through five.';
}
function runtime(args, context) {
  const service = context.cdc ?? context.services?.[CDC_SERVICE_ID] ?? context.service;
  if(!service) throw new Error('CDC runtime service is unavailable.');
  const id = args.sessionId ?? context.sessionId;
  if(!id) throw new Error('CDC session ID is required.');
  return {service, id};
}
function command(input) {
  return {iconKey: 'tool.cdc', surfaces: ['tools', 'toolbar'], macroCallable: false,
    aiEligible: false, auditCategory: 'cdc', validateArguments: () => true,
    enabledWhen: () => true, visibleWhen: () => true,
    disabledReason: 'CDC action is unavailable.', allowedSecurityGroups: [],
    deniedSecurityGroups: [], createsTask: '', confirmationIntent: 'none', ...input};
}

function commands() {
  return [
    command({id: 'cdc.definition.create', label: 'Create or Update CDC Definition',
      description: 'Create or replace a complete deterministic CDC definition asset.',
      permission: ['cdc.edit'], validateArguments: (args) => {
        const known = sessionArguments(args, ['content']); if(known !== true) return known;
        try { createCDCContent(args.content ?? {}); return true; } catch(error) { return error.message; }
      }, execute: (args, context) => { const {service, id} = runtime(args, context);
        return service.replaceDefinition(id, args.content ?? {}, context); }}),
    command({id: 'cdc.validate', label: 'Validate CDC Definition',
      description: 'Validate authored semantics, provider mechanisms, privileges and start position.',
      permission: ['cdc.view'], validateArguments: (args) => sessionArguments(args),
      execute: async (args, context) => { const {service, id} = runtime(args, context);
        service.validate(id); return service.discover(id, context); }}),
    command({id: 'cdc.run.start', label: 'Start CDC Run',
      description: 'Provision, snapshot when selected, catch up and stream through provider adapters.',
      permission: ['cdc.execute'], createsTask: 'cdc.stream',
      confirmationIntent: 'standard_consequential', validateArguments: (args) =>
        retryArguments(args, ['runId']),
      execute: (args, context) => { const {service, id} = runtime(args, context);
        return service.start(id, args, context); }}),
    ...['pause', 'resume', 'stop'].map((action) => command({
      id: `cdc.run.${action}`, label: `${action[0].toUpperCase()}${action.slice(1)} CDC Run`,
      description: `${action[0].toUpperCase()}${action.slice(1)} the current provider CDC run.`,
      permission: ['cdc.execute'], createsTask: 'cdc.stream',
      confirmationIntent: 'standard_consequential',
      validateArguments: (args) => sessionArguments(args),
      execute: (args, context) => { const {service, id} = runtime(args, context);
        return service.control(id, action, context); }})),
    command({id: 'cdc.checkpoint.inspect', label: 'Inspect CDC Checkpoint',
      description: 'Read the durable provider source and sink progress without fabricating values.',
      permission: ['cdc.view'], validateArguments: (args) => sessionArguments(args),
      execute: (args, context) => { const {service, id} = runtime(args, context);
        return service.inspectCheckpoint(id, context); }}),
    command({id: 'cdc.replay.prepare', label: 'Prepare CDC Replay',
      description: 'Validate an explicit replay range, target and safeguards as a background task.',
      permission: ['cdc.replay'], createsTask: 'cdc.replay',
      confirmationIntent: 'standard_consequential', validateArguments: (args) => {
        const known = sessionArguments(args, ['request']); if(known !== true) return known;
        try { validateReplayRequest(args.request); return true; } catch(error) { return error.message; }
      }, execute: (args, context) => { const {service, id} = runtime(args, context);
        return service.prepareReplay(id, args.request, context); }}),
    command({id: 'cdc.replay.execute', label: 'Execute CDC Replay',
      description: 'Execute the independently reviewed bounded replay task.',
      permission: ['cdc.replay'], createsTask: 'cdc.replay',
      confirmationIntent: 'standard_consequential', validateArguments: (args) =>
        sessionArguments(args),
      execute: (args, context) => { const {service, id} = runtime(args, context);
        return service.executeReplay(id, context); }}),
    command({id: 'cdc.schema_change.resolve', label: 'Resolve CDC Schema Change',
      description: 'Apply a safe handling decision to a detected source schema change.',
      permission: ['cdc.edit'], validateArguments: (args) => {
        const known = sessionArguments(args, ['changeId', 'action']); if(known !== true) return known;
        return typeof args.changeId === 'string' && typeof args.action === 'string' ? true :
          'Schema change ID and action are required.';
      }, execute: (args, context) => { const {service, id} = runtime(args, context);
        return service.resolveSchemaChange(id, args.changeId, args.action, context); }}),
  ];
}

function surfaceRuntime(registry, descriptor, context) {
  return registry.resolve(CDC_SERVICE_ID).then(async (service) => {
    let sessionId = descriptor.restoreRef || descriptor.context?.sessionId;
    if(descriptor.projectId && descriptor.restoreRef) {
      const client = context.services?.[PROJECT_ASSET_SERVICE_ID] ??
        await registry.resolve(PROJECT_ASSET_SERVICE_ID);
      const asset = await client.asset(descriptor.projectId, descriptor.restoreRef);
      if(asset.asset_type !== CDC_ASSET_TYPE) throw new Error(
        'The requested project asset is not a CDC definition.'
      );
      sessionId = `cdc:${descriptor.projectId}:${descriptor.restoreRef}`;
      try { service.get(sessionId); } catch { service.create({id: sessionId, content: asset.content}); }
    }
    if(!sessionId) sessionId = service.create({}).id;
    try { service.get(sessionId); } catch { service.create({id: sessionId}); }
    const surface = descriptor.toolKind.replace('cdc.', '');
    const executeCommand = (id, args, commandContext={}) => context.commands.execute(
      id, {...args, sessionId}, {...context, ...commandContext, service, cdc: service, sessionId}
    );
    return React.createElement(CDCWorkspace, {service, sessionId, surface, executeCommand,
      resources: context.cdcResources ?? [], currentUser: context.currentUser ?? {},
      credentialReferences: context.cdcCredentialReferences ?? [],
      onStateChange: (session) => context.setSurfaceState?.({cdcSession: session,
        cdcInspector: cdcInspector(session), dirty: session.dirty,
        persistence: session.dirty ? 'dirty' : 'clean', problems: session.problems,
        activeTaskId: session.activeTaskId})});
  });
}

export function cdcModuleDefinition(registry) {
  return {id: CDC_MODULE_ID, version: '1.0.0', title: 'CDC Designer',
    iconKey: 'tool.cdc', serviceRequirements: [CDC_SERVICE_ID], contributions: {
      surfaces: CDC_SURFACES.map((surface) => ({id: `cdc.${surface.id}`,
        title: surface.title, iconKey: 'tool.cdc', assetTypes: [CDC_ASSET_TYPE],
        readOnly: false, editable: true, detachable: true, duplicable: true, fullscreen: true,
        restore: (descriptor, context) => surfaceRuntime(registry, descriptor, context),
        checkpoint: async (_descriptor, context) => context.cdcSession?.content ?? null,
        canClose: async (_descriptor, context) => context.dirty ? {allowed: false,
          reason: 'Save or discard CDC definition changes first.'} : {allowed: true, reason: ''}})),
      commands: commands(),
      activity: [{id: 'activity.cdc', label: 'CDC Designer', iconKey: 'tool.cdc', priority: 36,
        render: () => <CDCActivity registry={registry} />}],
      inspector: [{id: 'inspector.cdc', label: 'CDC', priority: 36,
        when: (context) => context.surfaceId?.startsWith('cdc.'),
        render: (context) => context.cdcInspector ?? {}}],
      bottom: [
        {id: 'bottom.cdc.run-monitor', label: 'Run Monitor', priority: 46,
          when: (context) => context.surfaceId?.startsWith('cdc.'),
          render: (context) => context.cdcSession?.runs?.[0] ?? {}},
        {id: 'bottom.cdc.event-samples', label: 'Event Samples', priority: 47,
          when: (context) => context.surfaceId?.startsWith('cdc.') &&
            context.currentUser?.permissions?.includes('cdc.view_payloads'),
          render: (context) => context.cdcSession?.events ?? []},
        {id: 'bottom.cdc.problems', label: 'Problems', priority: 48,
          when: (context) => context.surfaceId?.startsWith('cdc.'),
          render: (context) => context.cdcSession?.problems ?? []},
        {id: 'bottom.cdc.tasks', label: 'Tasks', priority: 49,
          when: (context) => context.surfaceId?.startsWith('cdc.'),
          render: (context) => ({activeTaskId: context.cdcSession?.activeTaskId ?? null})}],
      status: [{id: 'status.cdc', label: 'CDC', priority: 36,
        when: (context) => context.surfaceId?.startsWith('cdc.'),
        value: (context) => context.cdcSession?.state ?? 'ready'}]}};
}

function CDCActivity({registry}) {
  const [service, setService] = React.useState(null);
  React.useEffect(() => { registry.resolve(CDC_SERVICE_ID).then(setService); }, [registry]);
  if(!service) return <Box sx={{p: 1}}>Loading CDC definitions…</Box>;
  return <CDCNavigator service={service} onOpen={(sessionId, surface) =>
    window.dispatchEvent(new CustomEvent('cdeadmin:cdc-open', {detail: {sessionId, surface}}))} />;
}
CDCActivity.propTypes = {registry: PropTypes.object};

export function registerCDCModule({modules, services, api}={}) {
  if(!modules || !services) throw new TypeError(
    'CDC registration requires module and service registries.'
  );
  const removers = [];
  if(!services.has(PROJECT_ASSET_SERVICE_ID)) removers.push(services.register({
    id: PROJECT_ASSET_SERVICE_ID, version: '1.0.0', factory: () => new ProjectAssetClient(api)}));
  if(!services.has(CDC_SERVICE_ID)) removers.push(services.register({
    id: CDC_SERVICE_ID, version: '1.0.0', dependencies: [PLATFORM_SERVICE_IDS.TASKS,
      PLATFORM_SERVICE_IDS.RELATIONSHIPS, PLATFORM_SERVICE_IDS.SEARCH,
      PROJECT_ASSET_SERVICE_ID], factory: ({services: resolved}) => new CDCService({
      tasks: resolved[PLATFORM_SERVICE_IDS.TASKS],
      relationships: resolved[PLATFORM_SERVICE_IDS.RELATIONSHIPS],
      search: resolved[PLATFORM_SERVICE_IDS.SEARCH],
      projectAssets: resolved[PROJECT_ASSET_SERVICE_ID], commands: modules.commands})}));
  const removeModule = modules.register(cdcModuleDefinition(services));
  return () => { removeModule(); removers.reverse().forEach((remove) => remove()); };
}
