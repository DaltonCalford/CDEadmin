/////////////////////////////////////////////////////////////
// First-party ML / Vector registration and command boundary.
/////////////////////////////////////////////////////////////

import React from 'react';
import PropTypes from 'prop-types';
import {Box} from '@mui/material';
import {ProjectAssetClient} from '../../projects/ProjectAssetClient';
import {PLATFORM_SERVICE_IDS} from '../../platform/PlatformServices';
import {PROJECT_ASSET_SERVICE_ID} from '../../integrations/ddn/module';
import {
  ML_VECTOR_ASSET_TYPE, ML_VECTOR_MODULE_ID, ML_VECTOR_SERVICE_ID, validateEmbeddingPipeline, validateModelEntry, validateVectorIndex,
} from './contracts';
import {normalizeSearchRequest} from './MLVectorEngine';
import {MLVectorService} from './MLVectorService';
import {
  ML_VECTOR_SURFACES, MLVectorNavigator, MLVectorWorkspace, mlVectorInspector,
} from './MLVectorWorkspace';

function only(args, fields) { const unexpected = Object.keys(args ?? {}).filter(
  (name) => !fields.includes(name)); return unexpected.length ?
  `Unknown command argument: ${unexpected[0]}.` : true; }
function sessionArgs(args, fields=[]) { return only(args, ['sessionId', ...fields]); }
function required(value, label) { return typeof value === 'string' && value.trim() ? true :
  `${label} is required.`; }
function runtime(args, context) { const service = context.mlVector ??
  context.services?.[ML_VECTOR_SERVICE_ID] ?? context.service;
if(!service) throw new Error('ML / Vector runtime service is unavailable.');
const id = args.sessionId ?? context.sessionId;
if(!id) throw new Error('ML / Vector session ID is required.'); return {service, id}; }
function command(input) { return {iconKey: 'tool.ml-vector', surfaces: ['tools', 'toolbar'],
  macroCallable: false, aiEligible: false, auditCategory: 'ml_vector', validateArguments: () => true,
  enabledWhen: () => true, visibleWhen: () => true, disabledReason: 'ML / Vector action is unavailable.',
  allowedSecurityGroups: [], deniedSecurityGroups: [], createsTask: '', confirmationIntent: 'none', ...input}; }
function objectArgument(args, field, validate, label, extra=[]) {
  const known = sessionArgs(args, [field, ...extra]); if(known !== true) return known;
  try { validate(args[field]); return true; } catch(error) { return `${label}: ${error.message}`; }
}
function definitions() { return [
  command({id: 'vector.index.create_plan', label: 'Create Vector Index Plan',
    description: 'Add a provider-neutral index plan with explicit provider-native algorithm controls.',
    permission: ['ml_vector.edit'], validateArguments: (args) => {
      const id = required(args.designId, 'Vector design ID'); if(id !== true) return id;
      return objectArgument(args, 'index', validateVectorIndex, 'Invalid vector index', ['designId']);
    }, execute: (args, context) => { const {service, id} = runtime(args, context);
      return service.createIndexPlan(id, args.designId, args.index, context); }}),
  command({id: 'vector.index.validate', label: 'Validate Vector Index',
    description: 'Validate the index exclusively against current provider-advertised capabilities.',
    permission: ['ml_vector.view'], validateArguments: (args) => {
      const known = sessionArgs(args, ['designId', 'indexId']); if(known !== true) return known;
      return required(args.designId, 'Vector design ID') === true ? required(args.indexId,
        'Vector index ID') : 'Vector design ID is required.';
    }, execute: (args, context) => { const {service, id} = runtime(args, context);
      return service.validateIndex(id, args.designId, args.indexId, context); }}),
  command({id: 'vector.search.run', label: 'Run Vector Search',
    description: 'Run a bounded provider-prepared vector or text similarity search.',
    permission: ['ml_vector.search'], createsTask: 'vector.search.large',
    validateArguments: (args) => objectArgument(args, 'request', normalizeSearchRequest,
      'Invalid vector search'), execute: (args, context) => { const {service, id} = runtime(args, context);
      return service.searchVectors(id, args.request, context); }}),
  command({id: 'vector.search.explain', label: 'Explain Vector Search',
    description: 'Request a provider-native execution explanation without running the query.',
    permission: ['ml_vector.search'], validateArguments: (args) => objectArgument(args, 'request',
      normalizeSearchRequest, 'Invalid vector search'), execute: (args, context) => {
      const {service, id} = runtime(args, context); return service.explainSearch(id, args.request, context); }}),
  command({id: 'embedding.pipeline.create', label: 'Create Embedding Pipeline',
    description: 'Define explicit source normalization, model, target, batching and data-sharing policy.',
    permission: ['ml_vector.edit'], validateArguments: (args) => objectArgument(args, 'pipeline',
      validateEmbeddingPipeline, 'Invalid embedding pipeline'), execute: (args, context) => {
      const {service, id} = runtime(args, context); return service.createPipeline(id, args.pipeline, context); }}),
  command({id: 'embedding.run', label: 'Run Embedding Pipeline',
    description: 'Run an embedding pipeline with explicit external-model and sensitive-data controls.',
    permission: ['ml_vector.edit'], createsTask: 'embedding.run', validateArguments: (args) => {
      const known = sessionArgs(args, ['pipelineId', 'selectionRef']); if(known !== true) return known;
      return required(args.pipelineId, 'Embedding pipeline ID');
    }, execute: (args, context) => { const {service, id} = runtime(args, context);
      return service.runEmbedding(id, args.pipelineId, {selectionRef: args.selectionRef}, context); }}),
  command({id: 'model.register', label: 'Register Model',
    description: 'Register immutable model versions and their referenced lineage artifacts.',
    permission: ['ml_vector.edit'], validateArguments: (args) => objectArgument(args, 'model',
      validateModelEntry, 'Invalid model'), execute: (args, context) => { const {service, id} = runtime(args,
      context); return service.registerModel(id, args.model, context); }}),
  command({id: 'model.version.tag', label: 'Tag Model Version',
    description: 'Update mutable tag associations without modifying the immutable model version.',
    permission: ['ml_vector.edit'], validateArguments: (args) => {
      const known = sessionArgs(args, ['modelId', 'versionId', 'tags']); if(known !== true) return known;
      return required(args.modelId, 'Model ID') === true && required(args.versionId,
        'Model version ID') === true && Array.isArray(args.tags) ? true :
        'Model ID, version ID and tags are required.';
    }, execute: (args, context) => { const {service, id} = runtime(args, context);
      return service.tagVersion(id, args.modelId, args.versionId, args.tags, context); }}),
  command({id: 'model.alias.set', label: 'Set Model Alias',
    description: 'Move a mutable model alias after target-bound consequential confirmation.',
    permission: ['ml_vector.admin'], confirmationIntent: 'standard_consequential',
    validateArguments: (args) => { const known = sessionArgs(args,
      ['modelId', 'versionId', 'alias', 'confirmationRef']); if(known !== true) return known;
    return ['modelId', 'versionId', 'alias', 'confirmationRef'].every((field) =>
      typeof args[field] === 'string' && args[field]) ? true :
      'Model, version, alias and confirmation are required.'; }, execute: (args, context) => {
      const {service, id} = runtime(args, context); return service.setAlias(id, args.modelId,
        args.alias, args.versionId, {confirmationRef: args.confirmationRef,
          modelId: args.modelId, versionId: args.versionId}, context); }}),
  command({id: 'ml.evaluation.run', label: 'Run ML Evaluation',
    description: 'Run reproducible evaluation against explicit ground truth and dataset revision.',
    permission: ['ml_vector.view'], createsTask: 'ml.evaluation.run', validateArguments: (args) => {
      const known = sessionArgs(args, ['evaluationId']); if(known !== true) return known;
      return required(args.evaluationId, 'Evaluation ID');
    }, execute: (args, context) => { const {service, id} = runtime(args, context);
      return service.runEvaluation(id, args.evaluationId, {}, context); }}),
]; }

function surfaceRuntime(registry, descriptor, context) {
  return registry.resolve(ML_VECTOR_SERVICE_ID).then(async (service) => {
    let sessionId = descriptor.restoreRef || descriptor.context?.sessionId;
    if(descriptor.projectId && descriptor.restoreRef) {
      const client = context.services?.[PROJECT_ASSET_SERVICE_ID] ??
        await registry.resolve(PROJECT_ASSET_SERVICE_ID);
      const asset = await client.asset(descriptor.projectId, descriptor.restoreRef);
      if(asset.asset_type !== ML_VECTOR_ASSET_TYPE) throw new Error(
        'The requested project asset is not an ML / Vector asset.');
      sessionId = `ml-vector:${descriptor.projectId}:${descriptor.restoreRef}`;
      try { service.get(sessionId); } catch { service.create({id: sessionId, content: asset.content}); }
    }
    if(!sessionId) sessionId = service.create({}).id;
    try { service.get(sessionId); } catch { service.create({id: sessionId}); }
    const surface = descriptor.toolKind.replace('ml-vector.', '');
    const executeCommand = (id, args, commandContext={}) => context.commands.execute(id,
      {...args, sessionId}, {...context, ...commandContext, service, mlVector: service, sessionId});
    return React.createElement(MLVectorWorkspace, {service, sessionId, surface, executeCommand,
      currentUser: context.currentUser ?? {}, onStateChange: (session) => context.setSurfaceState?.({
        mlVectorSession: session, mlVectorInspector: mlVectorInspector(session), dirty: session.dirty,
        persistence: session.dirty ? 'dirty' : 'clean', problems: session.problems,
        activeTaskId: session.activeTaskId})});
  });
}
export function mlVectorModuleDefinition(registry) { return {id: ML_VECTOR_MODULE_ID, version: '1.0.0',
  title: 'ML / Vector Tooling', iconKey: 'tool.ml-vector', serviceRequirements: [ML_VECTOR_SERVICE_ID],
  contributions: {surfaces: ML_VECTOR_SURFACES.map((surface) => ({id: `ml-vector.${surface.id}`,
    title: surface.title, iconKey: 'tool.ml-vector', assetTypes: [ML_VECTOR_ASSET_TYPE], readOnly: false,
    editable: true, detachable: true, duplicable: true, fullscreen: true,
    restore: (descriptor, context) => surfaceRuntime(registry, descriptor, context),
    checkpoint: async (_descriptor, context) => context.mlVectorSession?.content ?? null,
    canClose: async (_descriptor, context) => context.dirty ? {allowed: false,
      reason: 'Save or discard ML / Vector asset changes first.'} : {allowed: true, reason: ''}})),
  commands: definitions(), activity: [{id: 'activity.ml-vector', label: 'ML / Vector',
    iconKey: 'tool.ml-vector', priority: 44, render: () => <MLVectorActivity registry={registry} />}],
  inspector: [{id: 'inspector.ml-vector', label: 'ML / Vector', priority: 44,
    when: (context) => context.surfaceId?.startsWith('ml-vector.'),
    render: (context) => context.mlVectorInspector ?? {}}],
  bottom: [{id: 'bottom.ml-vector.problems', label: 'Problems', priority: 90,
    when: (context) => context.surfaceId?.startsWith('ml-vector.'),
    render: (context) => context.mlVectorSession?.problems ?? []},
  {id: 'bottom.ml-vector.results', label: 'Results', priority: 91,
    when: (context) => context.surfaceId?.startsWith('ml-vector.'),
    render: (context) => context.mlVectorSession?.runtime?.searchResults ?? []},
  {id: 'bottom.ml-vector.tasks', label: 'Tasks', priority: 92,
    when: (context) => context.surfaceId?.startsWith('ml-vector.'),
    render: (context) => context.mlVectorSession?.tasks ?? []}],
  status: [{id: 'status.ml-vector', label: 'ML / Vector', priority: 44,
    when: (context) => context.surfaceId?.startsWith('ml-vector.'),
    value: (context) => context.mlVectorSession?.state ?? 'ready'}]}}; }
function MLVectorActivity({registry}) { const [service, setService] = React.useState(null);
  React.useEffect(() => { registry.resolve(ML_VECTOR_SERVICE_ID).then(setService); }, [registry]);
  if(!service) return <Box sx={{p: 1}}>Loading ML / Vector assets…</Box>;
  return <MLVectorNavigator service={service} onOpen={(sessionId, surface) => window.dispatchEvent(
    new CustomEvent('cdeadmin:ml-vector-open', {detail: {sessionId, surface}}))} />; }
MLVectorActivity.propTypes = {registry: PropTypes.object};
export function registerMLVectorModule({modules, services, api}={}) {
  if(!modules || !services) throw new TypeError('ML / Vector registration requires module and service registries.');
  const removers = [];
  if(!services.has(PROJECT_ASSET_SERVICE_ID)) removers.push(services.register({id: PROJECT_ASSET_SERVICE_ID,
    version: '1.0.0', factory: () => new ProjectAssetClient(api)}));
  if(!services.has(ML_VECTOR_SERVICE_ID)) removers.push(services.register({id: ML_VECTOR_SERVICE_ID,
    version: '1.0.0', dependencies: [PLATFORM_SERVICE_IDS.TASKS, PLATFORM_SERVICE_IDS.RELATIONSHIPS,
      PLATFORM_SERVICE_IDS.SEARCH, PROJECT_ASSET_SERVICE_ID], factory: ({services: resolved}) =>
      new MLVectorService({tasks: resolved[PLATFORM_SERVICE_IDS.TASKS],
        relationships: resolved[PLATFORM_SERVICE_IDS.RELATIONSHIPS],
        search: resolved[PLATFORM_SERVICE_IDS.SEARCH],
        projectAssets: resolved[PROJECT_ASSET_SERVICE_ID]})}));
  const removeModule = modules.register(mlVectorModuleDefinition(services));
  return () => { removeModule(); removers.reverse().forEach((remove) => remove()); };
}
