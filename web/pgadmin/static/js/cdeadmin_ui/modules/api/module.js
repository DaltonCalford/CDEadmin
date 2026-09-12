/////////////////////////////////////////////////////////////
// First-party API Designer registration and command boundary.
/////////////////////////////////////////////////////////////

import React from 'react';
import PropTypes from 'prop-types';
import {Box} from '@mui/material';
import {ProjectAssetClient} from '../../projects/ProjectAssetClient';
import {PLATFORM_SERVICE_IDS} from '../../platform/PlatformServices';
import {PROJECT_ASSET_SERVICE_ID} from '../../integrations/ddn/module';
import {
  API_ASSET_TYPE, API_MODULE_ID, API_SERVICE_ID, createAPIContent,
  validateDataBinding, validateOperation,
} from './contracts';
import {APIService} from './APIService';
import {APINavigator, API_SURFACES, APIWorkspace, apiInspector} from './APIWorkspace';

function only(args, fields) { const unexpected = Object.keys(args ?? {}).filter(
  (name) => !fields.includes(name)); return unexpected.length ?
  `Unknown command argument: ${unexpected[0]}.` : true; }
function sessionArgs(args, fields=[]) { return only(args, ['sessionId', ...fields]); }
function required(value, label) { return typeof value === 'string' && value.trim() ? true :
  `${label} is required.`; }
function runtime(args, context) {
  const service = context.apiDesigner ?? context.services?.[API_SERVICE_ID] ?? context.service;
  if(!service) throw new Error('API Designer runtime service is unavailable.');
  const id = args.sessionId ?? context.sessionId; if(!id) throw new Error('API Designer session ID is required.');
  return {service, id};
}
function command(input) { return {iconKey: 'tool.api', surfaces: ['tools', 'toolbar'],
  macroCallable: false, aiEligible: false, auditCategory: 'api_designer', validateArguments: () => true,
  enabledWhen: () => true, visibleWhen: () => true, disabledReason: 'API Designer action is unavailable.',
  allowedSecurityGroups: [], deniedSecurityGroups: [], createsTask: '', confirmationIntent: 'none', ...input}; }
function definitions() { return [
  command({id: 'api.create', label: 'Create API Definition',
    description: 'Create or replace a canonical API definition without deploying it.',
    permission: ['api.edit'], validateArguments: (args) => { const known = sessionArgs(args, ['definition']);
      if(known !== true) return known; try { createAPIContent(args.definition); return true; }
      catch(error) { return error.message; } }, execute: (args, context) => { const {service, id} = runtime(args,
      context); return service.replaceDefinition(id, args.definition, context); }}),
  ...[['api.import.openapi', 'openapi_http', 'Import OpenAPI 3.2'],
    ['api.import.asyncapi', 'asyncapi_event', 'Import AsyncAPI 3.1']].map(([id, profile, label]) => command({
    id, label, description: `Import a version-declared ${profile} document with provenance.`,
    permission: ['api.edit'], createsTask: 'api.import', validateArguments: (args) => {
      const known = sessionArgs(args, ['source', 'provenance']); if(known !== true) return known;
      return typeof args.source === 'string' || (args.source && typeof args.source === 'object' &&
        !Array.isArray(args.source)) ? true : 'API source must be JSON/YAML text or an object.';
    }, execute: (args, context) => { const {service, id: sessionId} = runtime(args, context);
      return service.import(sessionId, profile, args.source, {provenance: args.provenance}, context); }})),
  command({id: 'api.operation.add', label: 'Add API Operation',
    description: 'Add one explicit HTTP or RPC operation to the authored definition.',
    permission: ['api.edit'], validateArguments: (args) => { const known = sessionArgs(args, ['operation']);
      if(known !== true) return known; try { validateOperation(args.operation); return true; }
      catch(error) { return error.message; } }, execute: (args, context) => { const {service, id} = runtime(args,
      context); return service.addOperation(id, args.operation, context); }}),
  command({id: 'api.binding.set', label: 'Set API Data Binding',
    description: 'Bind an operation explicitly to a resource or project asset.', permission: ['api.edit'],
    validateArguments: (args) => { const known = sessionArgs(args, ['operationId', 'binding']);
      if(known !== true) return known; const id = required(args.operationId, 'Operation ID');
      if(id !== true) return id; try { validateDataBinding(args.binding); return true; }
      catch(error) { return error.message; } }, execute: (args, context) => { const {service, id} = runtime(args,
      context); return service.setBinding(id, args.operationId, args.binding, context); }}),
  command({id: 'api.validate', label: 'Validate API Definition',
    description: 'Validate authored API structure without live invocation.', permission: ['api.view'],
    validateArguments: (args) => sessionArgs(args), execute: (args, context) => { const {service, id} = runtime(
      args, context); return service.validate(id, context); }}),
  command({id: 'api.test.run', label: 'Run API Test',
    description: 'Run a mock/example test or a bounded explicitly targeted live invocation.',
    permission: ['api.test'], createsTask: 'api.test', validateArguments: (args) => {
      const known = sessionArgs(args, ['testId', 'confirmationRef', 'environment', 'maxPreviewBytes']);
      if(known !== true) return known; const id = required(args.testId, 'Test ID'); if(id !== true) return id;
      return args.maxPreviewBytes === undefined || (Number.isInteger(args.maxPreviewBytes) &&
        args.maxPreviewBytes > 0 && args.maxPreviewBytes <= 1024 * 1024) ? true :
        'Response preview limit must be between 1 and 1048576 bytes.';
    }, execute: (args, context) => { const {service, id} = runtime(args, context); return service.test(id,
      args.testId, {confirmationRef: args.confirmationRef, environment: args.environment,
        maxPreviewBytes: args.maxPreviewBytes}, context); }}),
  ...[['api.export.openapi', 'openapi_http', 'Export OpenAPI'],
    ['api.export.asyncapi', 'asyncapi_event', 'Export AsyncAPI']].map(([id, profile, label]) => command({
    id, label, description: 'Export the authored API without silently changing its declared version.',
    permission: ['api.view'], createsTask: 'api.validation', validateArguments: (args) => sessionArgs(args),
    execute: (args, context) => { const {service, id: sessionId} = runtime(args, context);
      return service.export(sessionId, profile, {}, context); }})),
  command({id: 'api.deploy.prepare', label: 'Prepare API Deployment',
    description: 'Prepare a reviewed deployment package; this command never deploys it.',
    permission: ['api.deploy_prepare'], createsTask: 'api.deployment',
    confirmationIntent: 'standard_consequential', validateArguments: (args) => {
      const known = sessionArgs(args, ['deploymentId', 'confirmationRef', 'environment', 'connection']);
      if(known !== true) return known; return ['deploymentId', 'confirmationRef', 'environment', 'connection']
        .every((field) => typeof args[field] === 'string' && args[field]) ? true :
        'Deployment, confirmation, environment and connection are required.';
    }, execute: (args, context) => { const {service, id} = runtime(args, context);
      return service.prepareDeployment(id, args.deploymentId, {confirmationRef: args.confirmationRef,
        environment: args.environment, connection: args.connection}, context); }}),
]; }

function surfaceRuntime(registry, descriptor, context) {
  return registry.resolve(API_SERVICE_ID).then(async (service) => {
    let sessionId = descriptor.restoreRef || descriptor.context?.sessionId;
    if(descriptor.projectId && descriptor.restoreRef) {
      const client = context.services?.[PROJECT_ASSET_SERVICE_ID] ??
        await registry.resolve(PROJECT_ASSET_SERVICE_ID);
      const asset = await client.asset(descriptor.projectId, descriptor.restoreRef);
      if(asset.asset_type !== API_ASSET_TYPE) throw new Error('The requested project asset is not an API definition.');
      sessionId = `api:${descriptor.projectId}:${descriptor.restoreRef}`;
      try { service.get(sessionId); } catch { service.create({id: sessionId, content: asset.content}); }
    }
    if(!sessionId) sessionId = service.create({}).id;
    try { service.get(sessionId); } catch { service.create({id: sessionId}); }
    const surface = descriptor.toolKind.replace('api.', '');
    const executeCommand = (id, args, commandContext={}) => context.commands.execute(id,
      {...args, sessionId}, {...context, ...commandContext, service, apiDesigner: service, sessionId});
    return React.createElement(APIWorkspace, {service, sessionId, surface, executeCommand,
      currentUser: context.currentUser ?? {}, resources: context.apiResources ?? [],
      onStateChange: (session) => context.setSurfaceState?.({
        apiSession: session, apiInspector: apiInspector(session), dirty: session.dirty,
        persistence: session.dirty ? 'dirty' : 'clean', problems: session.problems,
        activeTaskId: session.activeTaskId})});
  });
}
export function apiModuleDefinition(registry) { return {id: API_MODULE_ID, version: '1.0.0',
  title: 'API Designer', iconKey: 'tool.api', serviceRequirements: [API_SERVICE_ID], contributions: {
    surfaces: API_SURFACES.map((surface) => ({id: `api.${surface.id}`, title: surface.title,
      iconKey: 'tool.api', assetTypes: [API_ASSET_TYPE], readOnly: false, editable: true,
      detachable: true, duplicable: true, fullscreen: true,
      restore: (descriptor, context) => surfaceRuntime(registry, descriptor, context),
      checkpoint: async (_descriptor, context) => context.apiSession?.content ?? null,
      canClose: async (_descriptor, context) => context.dirty ? {allowed: false,
        reason: 'Save or discard API definition changes first.'} : {allowed: true, reason: ''}})),
    commands: definitions(), activity: [{id: 'activity.api', label: 'API Designer', iconKey: 'tool.api',
      priority: 41, render: () => <APIActivity registry={registry} />}],
    inspector: [{id: 'inspector.api', label: 'API', priority: 41,
      when: (context) => context.surfaceId?.startsWith('api.'),
      render: (context) => context.apiInspector ?? {}}],
    bottom: [{id: 'bottom.api.problems', label: 'Problems', priority: 80,
      when: (context) => context.surfaceId?.startsWith('api.'),
      render: (context) => context.apiSession?.problems ?? []},
    {id: 'bottom.api.test-results', label: 'Test Results', priority: 81,
      when: (context) => context.surfaceId?.startsWith('api.'),
      render: (context) => context.apiSession?.runtime?.testResults ?? []},
    {id: 'bottom.api.source', label: 'Source', priority: 82,
      when: (context) => context.surfaceId?.startsWith('api.'),
      render: (context) => context.apiSession?.content ?? {}},
    {id: 'bottom.api.tasks', label: 'Tasks', priority: 83,
      when: (context) => context.surfaceId?.startsWith('api.'),
      render: (context) => context.apiSession?.tasks ?? []}],
    status: [{id: 'status.api', label: 'API', priority: 41,
      when: (context) => context.surfaceId?.startsWith('api.'),
      value: (context) => context.apiSession?.state ?? 'ready'}]}}; }
function APIActivity({registry}) { const [service, setService] = React.useState(null);
  React.useEffect(() => { registry.resolve(API_SERVICE_ID).then(setService); }, [registry]);
  if(!service) return <Box sx={{p: 1}}>Loading API definitions…</Box>;
  return <APINavigator service={service} onOpen={(sessionId, surface) => window.dispatchEvent(
    new CustomEvent('cdeadmin:api-open', {detail: {sessionId, surface}}))} />; }
APIActivity.propTypes = {registry: PropTypes.object};
export function registerAPIModule({modules, services, api}={}) {
  if(!modules || !services) throw new TypeError('API registration requires module and service registries.');
  const removers = [];
  if(!services.has(PROJECT_ASSET_SERVICE_ID)) removers.push(services.register({id: PROJECT_ASSET_SERVICE_ID,
    version: '1.0.0', factory: () => new ProjectAssetClient(api)}));
  if(!services.has(API_SERVICE_ID)) removers.push(services.register({id: API_SERVICE_ID,
    version: '1.0.0', dependencies: [PLATFORM_SERVICE_IDS.TASKS, PLATFORM_SERVICE_IDS.RELATIONSHIPS,
      PLATFORM_SERVICE_IDS.SEARCH, PROJECT_ASSET_SERVICE_ID], factory: ({services: resolved}) => new APIService({
      tasks: resolved[PLATFORM_SERVICE_IDS.TASKS], relationships: resolved[PLATFORM_SERVICE_IDS.RELATIONSHIPS],
      search: resolved[PLATFORM_SERVICE_IDS.SEARCH], projectAssets: resolved[PROJECT_ASSET_SERVICE_ID]})}));
  const removeModule = modules.register(apiModuleDefinition(services));
  return () => { removeModule(); removers.reverse().forEach((remove) => remove()); };
}
