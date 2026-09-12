/////////////////////////////////////////////////////////////
// First-party ETL Designer registration and command boundary.
/////////////////////////////////////////////////////////////

import React from 'react';
import PropTypes from 'prop-types';
import {Box} from '@mui/material';
import {ProjectAssetClient} from '../../projects/ProjectAssetClient';
import {PLATFORM_SERVICE_IDS} from '../../platform/PlatformServices';
import {PROJECT_ASSET_SERVICE_ID} from '../../integrations/ddn/module';
import {
  createETLContent, ETL_ASSET_TYPE, ETL_MODULE_ID, validatePipelineEdge,
  validatePipelineNode, validateSchemaMapping,
} from './contracts';
import {ETL_SERVICE_ID, ETLService} from './ETLService';
import {ETLNavigator, ETL_SURFACES, ETLWorkspace, etlInspector} from './ETLWorkspace';

function required(value, label) {
  return typeof value === 'string' && value.trim() ? true : `${label} is required.`;
}
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
  const service = context.etl ?? context.services?.[ETL_SERVICE_ID] ?? context.service;
  if(!service) throw new Error('ETL runtime service is unavailable.');
  const id = args.sessionId ?? context.sessionId;
  if(!id) throw new Error('ETL session ID is required.');
  return {service, id};
}
function command(input) {
  return {iconKey: 'tool.etl', surfaces: ['tools', 'toolbar'], macroCallable: false,
    aiEligible: false, auditCategory: 'etl', validateArguments: () => true,
    enabledWhen: () => true, visibleWhen: () => true,
    disabledReason: 'ETL action is unavailable.', allowedSecurityGroups: [],
    deniedSecurityGroups: [], createsTask: '', confirmationIntent: 'none', ...input};
}

function commands() {
  return [
    command({id: 'etl.pipeline.create', label: 'Create or Update Pipeline',
      description: 'Create or replace a complete deterministic ETL pipeline asset.',
      permission: ['etl.edit'], validateArguments: (args) => {
        const known = sessionArguments(args, ['content']); if(known !== true) return known;
        try { createETLContent(args.content ?? {}); return true; } catch(error) { return error.message; }
      }, execute: (args, context) => { const {service, id} = runtime(args, context);
        return service.replaceDefinition(id, args.content ?? {}, context); }}),
    command({id: 'etl.node.add', label: 'Add Pipeline Node',
      description: 'Add a typed source, transform, target or control node.',
      permission: ['etl.edit'], validateArguments: (args) => {
        const known = sessionArguments(args, ['node']); if(known !== true) return known;
        try { validatePipelineNode(args.node); return true; } catch(error) { return error.message; }
      }, execute: (args, context) => { const {service, id} = runtime(args, context);
        return service.addNode(id, args.node, context); }}),
    command({id: 'etl.edge.connect', label: 'Connect Pipeline Ports',
      description: 'Connect compatible typed output and input ports.',
      permission: ['etl.edit'], validateArguments: (args) => {
        const known = sessionArguments(args, ['edge']); if(known !== true) return known;
        try { validatePipelineEdge(args.edge); return true; } catch(error) { return error.message; }
      }, execute: (args, context) => { const {service, id} = runtime(args, context);
        return service.connectEdge(id, args.edge, context); }}),
    command({id: 'etl.mapping.edit', label: 'Edit Schema Mapping',
      description: 'Replace explicit field mappings while preserving loss annotations.',
      permission: ['etl.edit'], validateArguments: (args) => {
        const known = sessionArguments(args, ['edgeId', 'mappings']); if(known !== true) return known;
        if(required(args.edgeId, 'Edge ID') !== true || !Array.isArray(args.mappings)) {
          return 'Edge ID and mappings array are required.';
        }
        try { args.mappings.forEach(validateSchemaMapping); return true; }
        catch(error) { return error.message; }
      }, execute: (args, context) => { const {service, id} = runtime(args, context);
        return service.editMappings(id, args.edgeId, args.mappings, context); }}),
    command({id: 'etl.pipeline.validate', label: 'Validate Pipeline',
      description: 'Validate graph, modes, schemas, references and loss policy.',
      permission: ['etl.view'], validateArguments: (args) => sessionArguments(args),
      execute: (args, context) => { const {service, id} = runtime(args, context);
        return service.validate(id); }}),
    command({id: 'etl.preview.run', label: 'Preview Pipeline',
      description: 'Read a bounded sample without executing sink writes.',
      permission: ['etl.preview', 'etl.view_samples'], createsTask: 'etl.preview',
      validateArguments: (args) => {
        const known = retryArguments(args, ['nodeIds', 'limit', 'streamScope']);
        if(known !== true) return known;
        if(args.nodeIds !== undefined && (!Array.isArray(args.nodeIds) ||
            args.nodeIds.some((item) => typeof item !== 'string'))) return 'nodeIds must be text IDs.';
        if(args.limit !== undefined && (!Number.isInteger(args.limit) || args.limit < 1 ||
            args.limit > 10000)) return 'Preview limit must be from 1 through 10000.';
        if(args.streamScope !== undefined && args.streamScope !== null &&
            (!args.streamScope || Array.isArray(args.streamScope) ||
              typeof args.streamScope !== 'object')) return 'streamScope must be an object.';
        return true;
      }, execute: (args, context) => { const {service, id} = runtime(args, context);
        return service.preview(id, args, context); }}),
    command({id: 'etl.pipeline.deploy', label: 'Validate Pipeline Deployment',
      description: 'Validate environment bindings and provider-native execution plans.',
      permission: ['etl.deploy'], createsTask: 'etl.deploy.validation',
      confirmationIntent: 'standard_consequential', validateArguments: (args) => {
        const known = retryArguments(args, ['deploymentId']);
        return known === true ? required(args.deploymentId, 'Deployment ID') : known;
      }, execute: (args, context) => { const {service, id} = runtime(args, context);
        return service.deploy(id, args, context); }}),
    command({id: 'etl.run.start', label: 'Run Pipeline',
      description: 'Execute a validated deployment through admitted provider adapters.',
      permission: ['etl.execute'], createsTask: 'etl.run',
      confirmationIntent: 'standard_consequential', validateArguments: (args) => {
        const known = retryArguments(args, ['deploymentId', 'parameters']);
        if(known !== true) return known;
        if(required(args.deploymentId, 'Deployment ID') !== true) return 'Deployment ID is required.';
        return args.parameters === undefined || (args.parameters && !Array.isArray(args.parameters) &&
          typeof args.parameters === 'object') ? true : 'Parameters must be an object.';
      }, execute: (args, context) => { const {service, id} = runtime(args, context);
        return service.startRun(id, args, context); }}),
    command({id: 'etl.run.cancel', label: 'Cancel Pipeline Work',
      description: 'Request cancellation of the active shared ETL Task.',
      permission: ['etl.execute'], createsTask: 'etl.run', validateArguments: (args) => {
        const known = sessionArguments(args, ['taskId']);
        return known === true ? required(args.taskId, 'Task ID') : known;
      }, execute: (args, context) => { const {service, id} = runtime(args, context);
        return service.cancel(id, args.taskId); }}),
    command({id: 'etl.run.resume', label: 'Resume Pipeline from Checkpoint',
      description: 'Start a new audited attempt from a failed or cancelled run checkpoint.',
      permission: ['etl.execute'], createsTask: 'etl.run',
      confirmationIntent: 'standard_consequential', validateArguments: (args) => {
        const known = retryArguments(args, ['runId']);
        return known === true ? required(args.runId, 'Run ID') : known;
      }, execute: (args, context) => { const {service, id} = runtime(args, context);
        return service.resumeRun(id, args, context); }}),
  ];
}

function surfaceRuntime(registry, descriptor, context) {
  return registry.resolve(ETL_SERVICE_ID).then(async (service) => {
    let sessionId = descriptor.restoreRef || descriptor.context?.sessionId;
    if(descriptor.projectId && descriptor.restoreRef) {
      const client = context.services?.[PROJECT_ASSET_SERVICE_ID] ??
        await registry.resolve(PROJECT_ASSET_SERVICE_ID);
      const asset = await client.asset(descriptor.projectId, descriptor.restoreRef);
      if(asset.asset_type !== ETL_ASSET_TYPE) throw new Error(
        'The requested project asset is not an ETL pipeline.'
      );
      sessionId = `etl:${descriptor.projectId}:${descriptor.restoreRef}`;
      try { service.get(sessionId); } catch { service.create({id: sessionId, content: asset.content}); }
    }
    if(!sessionId) sessionId = service.create({}).id;
    try { service.get(sessionId); } catch { service.create({id: sessionId}); }
    const surface = descriptor.toolKind.replace('etl.', '');
    const executeCommand = (id, args, commandContext={}) => context.commands.execute(
      id, {...args, sessionId}, {...context, ...commandContext, service, etl: service, sessionId}
    );
    return React.createElement(ETLWorkspace, {service, sessionId, surface, executeCommand,
      resources: context.etlResources ?? [], currentUser: context.currentUser ?? {},
      credentialReferences: context.etlCredentialReferences ?? [],
      onStateChange: (session) => context.setSurfaceState?.({etlSession: session,
        etlInspector: etlInspector(session), dirty: session.dirty,
        persistence: session.dirty ? 'dirty' : 'clean', problems: session.problems,
        activeTaskId: session.activeTaskId})});
  });
}

export function etlModuleDefinition(registry) {
  return {id: ETL_MODULE_ID, version: '1.0.0', title: 'ETL Designer',
    iconKey: 'tool.etl', serviceRequirements: [ETL_SERVICE_ID], contributions: {
      surfaces: ETL_SURFACES.map((surface) => ({id: `etl.${surface.id}`,
        title: surface.title, iconKey: 'tool.etl', assetTypes: [ETL_ASSET_TYPE],
        readOnly: false, editable: true, detachable: true, duplicable: true, fullscreen: true,
        restore: (descriptor, context) => surfaceRuntime(registry, descriptor, context),
        checkpoint: async (_descriptor, context) => context.etlSession?.content ?? null,
        canClose: async (_descriptor, context) => context.dirty ? {allowed: false,
          reason: 'Save or discard ETL pipeline changes first.'} : {allowed: true, reason: ''}})),
      commands: commands(),
      activity: [{id: 'activity.etl', label: 'ETL Designer', iconKey: 'tool.etl', priority: 35,
        render: () => <ETLActivity registry={registry} />}],
      inspector: [{id: 'inspector.etl', label: 'ETL', priority: 35,
        when: (context) => context.surfaceId?.startsWith('etl.'),
        render: (context) => context.etlInspector ?? {}}],
      bottom: [
        {id: 'bottom.etl.schema', label: 'Schema', priority: 42,
          when: (context) => context.surfaceId?.startsWith('etl.'),
          render: (context) => context.etlSession?.schemaPropagation ?? {}},
        {id: 'bottom.etl.preview-data', label: 'Preview Data', priority: 43,
          when: (context) => context.surfaceId?.startsWith('etl.') &&
            context.currentUser?.permissions?.includes('etl.view_samples'),
          render: (context) => context.etlSession?.previews?.[0] ?? {}},
        {id: 'bottom.etl.problems', label: 'Problems', priority: 44,
          when: (context) => context.surfaceId?.startsWith('etl.'),
          render: (context) => context.etlSession?.problems ?? []},
        {id: 'bottom.etl.run-log', label: 'Run Log', priority: 45,
          when: (context) => context.surfaceId?.startsWith('etl.'),
          render: (context) => context.etlSession?.runs?.[0] ?? {}}],
      status: [{id: 'status.etl', label: 'ETL', priority: 35,
        when: (context) => context.surfaceId?.startsWith('etl.'),
        value: (context) => context.etlSession?.state ?? 'ready'}]}};
}

function ETLActivity({registry}) {
  const [service, setService] = React.useState(null);
  React.useEffect(() => { registry.resolve(ETL_SERVICE_ID).then(setService); }, [registry]);
  if(!service) return <Box sx={{p: 1}}>Loading ETL pipelines…</Box>;
  return <ETLNavigator service={service} onOpen={(sessionId, surface) =>
    window.dispatchEvent(new CustomEvent('cdeadmin:etl-open', {detail: {sessionId, surface}}))} />;
}
ETLActivity.propTypes = {registry: PropTypes.object};

export function registerETLModule({modules, services, api}={}) {
  if(!modules || !services) throw new TypeError(
    'ETL registration requires module and service registries.'
  );
  const removers = [];
  if(!services.has(PROJECT_ASSET_SERVICE_ID)) removers.push(services.register({
    id: PROJECT_ASSET_SERVICE_ID, version: '1.0.0', factory: () => new ProjectAssetClient(api)}));
  if(!services.has(ETL_SERVICE_ID)) removers.push(services.register({
    id: ETL_SERVICE_ID, version: '1.0.0', dependencies: [PLATFORM_SERVICE_IDS.TASKS,
      PLATFORM_SERVICE_IDS.RELATIONSHIPS, PLATFORM_SERVICE_IDS.SEARCH,
      PROJECT_ASSET_SERVICE_ID], factory: ({services: resolved}) => new ETLService({
      tasks: resolved[PLATFORM_SERVICE_IDS.TASKS],
      relationships: resolved[PLATFORM_SERVICE_IDS.RELATIONSHIPS],
      search: resolved[PLATFORM_SERVICE_IDS.SEARCH],
      projectAssets: resolved[PROJECT_ASSET_SERVICE_ID], commands: modules.commands})}));
  const removeModule = modules.register(etlModuleDefinition(services));
  return () => { removeModule(); removers.reverse().forEach((remove) => remove()); };
}
