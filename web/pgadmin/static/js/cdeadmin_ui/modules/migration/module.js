/////////////////////////////////////////////////////////////
// Migration Planning registration and command boundary.
/////////////////////////////////////////////////////////////

import React from 'react';
import PropTypes from 'prop-types';
import {Box} from '@mui/material';
import {ProjectAssetClient} from '../../projects/ProjectAssetClient';
import {PLATFORM_SERVICE_IDS} from '../../platform/PlatformServices';
import {PROJECT_ASSET_SERVICE_ID} from '../../integrations/ddn/module';
import {
  createMigrationContent, MIGRATION_ASSET_TYPE, MIGRATION_MODULE_ID,
  MIGRATION_SERVICE_ID,
} from './contracts';
import {MigrationService} from './MigrationService';
import {
  MIGRATION_SURFACES, MigrationNavigator, MigrationWorkspace, migrationInspector,
} from './MigrationWorkspace';

function only(args, fields) {
  const unexpected = Object.keys(args ?? {}).filter((name) => !fields.includes(name));
  return unexpected.length ? `Unknown command argument: ${unexpected[0]}.` : true;
}
function sessionArgs(args, fields=[]) { return only(args, ['sessionId', ...fields]); }
function retryArgs(args) {
  const known = sessionArgs(args, ['retry']); if(known !== true) return known;
  if(args.retry === undefined) return true;
  return args.retry && Object.keys(args.retry).every((key) => key === 'maximum') &&
    Number.isInteger(args.retry.maximum) && args.retry.maximum >= 0 && args.retry.maximum <= 5 ?
    true : 'Retry maximum must be an integer from zero through five.';
}
function runtime(args, context) {
  const service = context.migration ?? context.services?.[MIGRATION_SERVICE_ID] ?? context.service;
  if(!service) throw new Error('Migration runtime service is unavailable.');
  const id = args.sessionId ?? context.sessionId;
  if(!id) throw new Error('Migration session ID is required.'); return {service, id};
}
function command(input) {
  return {iconKey: 'tool.migration', surfaces: ['tools', 'toolbar'], macroCallable: false,
    aiEligible: false, auditCategory: 'migration', validateArguments: () => true,
    enabledWhen: () => true, visibleWhen: () => true,
    disabledReason: 'Migration action is unavailable.', allowedSecurityGroups: [],
    deniedSecurityGroups: [], createsTask: '', confirmationIntent: 'none', ...input};
}
function commandDefinitions() {
  return [
    command({id: 'migration.project.create', label: 'Create Migration Project',
      description: 'Create or replace a deterministic source-to-target migration definition.',
      permission: ['migration.edit'], validateArguments: (args) => {
        const known = sessionArgs(args, ['definition']); if(known !== true) return known;
        try { createMigrationContent(args.definition); return true; } catch(error) { return error.message; }
      }, execute: (args, context) => { const {service, id} = runtime(args, context);
        return service.replaceDefinition(id, args.definition, context); }}),
    command({id: 'migration.assess.run', label: 'Run Migration Assessment',
      description: 'Discover exact compatibility, evidence, mappings, warnings and blockers.',
      permission: ['migration.execute'], createsTask: 'migration.assessment',
      validateArguments: retryArgs, execute: (args, context) => { const {service, id} = runtime(args, context);
        return service.assess(id, {retry: args.retry}, context); }}),
    command({id: 'migration.mapping.accept', label: 'Record Mapping Decision',
      description: 'Record an explicit accepted, rejected or manual mapping decision.',
      permission: ['migration.edit'], validateArguments: (args) => {
        const known = sessionArgs(args, ['mappingId', 'decision']); if(known !== true) return known;
        return typeof args.mappingId === 'string' && args.mappingId &&
          ['accepted', 'rejected', 'manual'].includes(args.decision) ? true :
          'Mapping ID and reviewed decision are required.';
      }, execute: (args, context) => { const {service, id} = runtime(args, context);
        return service.acceptMapping(id, args.mappingId, args.decision, context); }}),
    command({id: 'migration.plan.validate', label: 'Validate Migration Plan',
      description: 'Validate bindings, decisions, loss, dependencies, verification and rollback.',
      permission: ['migration.edit'], validateArguments: (args) => sessionArgs(args),
      execute: (args, context) => { const {service, id} = runtime(args, context);
        return service.validatePlan(id, context); }}),
    command({id: 'migration.dry_run', label: 'Dry Run Migration',
      description: 'Prepare and validate every schema operation without live mutation.',
      permission: ['migration.execute'], createsTask: 'migration.schema.apply',
      validateArguments: retryArgs, execute: (args, context) => { const {service, id} = runtime(args, context);
        return service.dryRun(id, {retry: args.retry}, context); }}),
    command({id: 'migration.copy.start', label: 'Start or Resume Data Copy',
      description: 'Execute copy units from their last committed recoverable checkpoints.',
      permission: ['migration.execute'], createsTask: 'migration.data.copy',
      confirmationIntent: 'standard_consequential', validateArguments: retryArgs,
      execute: (args, context) => { const {service, id} = runtime(args, context);
        return service.startCopy(id, {retry: args.retry}, context); }}),
    command({id: 'migration.cutover.arm', label: 'Arm Migration Cutover',
      description: 'Arm the exact reviewed plan for a bounded target environment and connection.',
      permission: ['migration.cutover'], createsTask: 'migration.cutover',
      confirmationIntent: 'standard_consequential', validateArguments: (args) => {
        const known = sessionArgs(args, ['confirmationRef', 'environment', 'connection']);
        if(known !== true) return known;
        return ['confirmationRef', 'environment', 'connection'].every((field) =>
          typeof args[field] === 'string' && args[field]) ? true :
          'Confirmation reference, environment and connection are required.';
      }, execute: (args, context) => { const {service, id} = runtime(args, context);
        return service.armCutover(id, {confirmationRef: args.confirmationRef,
          environment: args.environment, connection: args.connection}, context); }}),
    command({id: 'migration.cutover.execute', label: 'Execute Migration Cutover',
      description: 'Execute the unexpired armed plan and require post-cutover verification.',
      permission: ['migration.cutover'], createsTask: 'migration.cutover',
      confirmationIntent: 'standard_consequential', validateArguments: retryArgs,
      execute: (args, context) => { const {service, id} = runtime(args, context);
        return service.executeCutover(id, {retry: args.retry}, context); }}),
    command({id: 'migration.rollback.execute', label: 'Execute Migration Rollback',
      description: 'Execute the reviewed rollback while its feasibility classification permits.',
      permission: ['migration.rollback'], createsTask: 'migration.rollback',
      confirmationIntent: 'standard_consequential', validateArguments: retryArgs,
      execute: (args, context) => { const {service, id} = runtime(args, context);
        return service.executeRollback(id, {retry: args.retry}, context); }}),
    command({id: 'migration.verify.run', label: 'Run Migration Verification',
      description: 'Run counts, canonical hashes, samples, queries, quality or native verification.',
      permission: ['migration.execute'], createsTask: 'migration.validation',
      validateArguments: retryArgs, execute: (args, context) => { const {service, id} = runtime(args, context);
        return service.verify(id, {retry: args.retry}, context); }}),
  ];
}

function surfaceRuntime(registry, descriptor, context) {
  return registry.resolve(MIGRATION_SERVICE_ID).then(async (service) => {
    let sessionId = descriptor.restoreRef || descriptor.context?.sessionId;
    if(descriptor.projectId && descriptor.restoreRef) {
      const client = context.services?.[PROJECT_ASSET_SERVICE_ID] ??
        await registry.resolve(PROJECT_ASSET_SERVICE_ID);
      const asset = await client.asset(descriptor.projectId, descriptor.restoreRef);
      if(asset.asset_type !== MIGRATION_ASSET_TYPE) throw new Error(
        'The requested project asset is not a migration asset.'
      );
      sessionId = `migration:${descriptor.projectId}:${descriptor.restoreRef}`;
      try { service.get(sessionId); } catch { service.create({id: sessionId, content: asset.content}); }
    }
    if(!sessionId) sessionId = service.create({}).id;
    try { service.get(sessionId); } catch { service.create({id: sessionId}); }
    const surface = descriptor.toolKind.replace('migration.', '');
    const executeCommand = (id, args, commandContext={}) => context.commands.execute(
      id, {...args, sessionId}, {...context, ...commandContext, service, migration: service, sessionId}
    );
    return React.createElement(MigrationWorkspace, {service, sessionId, surface, executeCommand,
      currentUser: context.currentUser ?? {}, onStateChange: (session) =>
        context.setSurfaceState?.({migrationSession: session,
          migrationInspector: migrationInspector(session), dirty: session.dirty,
          persistence: session.dirty ? 'dirty' : 'clean', problems: session.problems,
          activeTaskId: session.activeTaskId})});
  });
}

export function migrationModuleDefinition(registry) {
  return {id: MIGRATION_MODULE_ID, version: '1.0.0', title: 'Migration Planning',
    iconKey: 'tool.migration', serviceRequirements: [MIGRATION_SERVICE_ID], contributions: {
      surfaces: MIGRATION_SURFACES.map((surface) => ({id: `migration.${surface.id}`,
        title: surface.title, iconKey: 'tool.migration', assetTypes: [MIGRATION_ASSET_TYPE],
        readOnly: false, editable: true, detachable: true, duplicable: true, fullscreen: true,
        restore: (descriptor, context) => surfaceRuntime(registry, descriptor, context),
        checkpoint: async (_descriptor, context) => context.migrationSession?.content ?? null,
        canClose: async (_descriptor, context) => context.dirty ? {allowed: false,
          reason: 'Save or discard migration asset changes first.'} : {allowed: true, reason: ''}})),
      commands: commandDefinitions(),
      activity: [{id: 'activity.migration', label: 'Migration', iconKey: 'tool.migration',
        priority: 39, render: () => <MigrationActivity registry={registry} />}],
      inspector: [{id: 'inspector.migration', label: 'Migration', priority: 39,
        when: (context) => context.surfaceId?.startsWith('migration.'),
        render: (context) => context.migrationInspector ?? {}}],
      bottom: [
        {id: 'bottom.migration.tasks', label: 'Tasks', priority: 70,
          when: (context) => context.surfaceId?.startsWith('migration.'),
          render: (context) => ({activeTaskId: context.migrationSession?.activeTaskId ?? null})},
        {id: 'bottom.migration.problems', label: 'Problems', priority: 71,
          when: (context) => context.surfaceId?.startsWith('migration.'),
          render: (context) => context.migrationSession?.problems ?? []},
        {id: 'bottom.migration.verification', label: 'Verification', priority: 72,
          when: (context) => context.surfaceId?.startsWith('migration.'),
          render: (context) => context.migrationSession?.runtime?.validationResults ?? []},
        {id: 'bottom.migration.run-log', label: 'Run Log', priority: 73,
          when: (context) => context.surfaceId?.startsWith('migration.'),
          render: (context) => context.migrationSession?.history ?? []}],
      status: [{id: 'status.migration', label: 'Migration', priority: 39,
        when: (context) => context.surfaceId?.startsWith('migration.'),
        value: (context) => context.migrationSession?.content?.phase ?? 'discover'}]}};
}

function MigrationActivity({registry}) {
  const [service, setService] = React.useState(null);
  React.useEffect(() => { registry.resolve(MIGRATION_SERVICE_ID).then(setService); }, [registry]);
  if(!service) return <Box sx={{p: 1}}>Loading migration projects…</Box>;
  return <MigrationNavigator service={service} onOpen={(sessionId, surface) =>
    window.dispatchEvent(new CustomEvent('cdeadmin:migration-open',
      {detail: {sessionId, surface}}))} />;
}
MigrationActivity.propTypes = {registry: PropTypes.object};

export function registerMigrationModule({modules, services, api}={}) {
  if(!modules || !services) throw new TypeError(
    'Migration registration requires module and service registries.'
  );
  const removers = [];
  if(!services.has(PROJECT_ASSET_SERVICE_ID)) removers.push(services.register({
    id: PROJECT_ASSET_SERVICE_ID, version: '1.0.0', factory: () => new ProjectAssetClient(api)}));
  if(!services.has(MIGRATION_SERVICE_ID)) removers.push(services.register({
    id: MIGRATION_SERVICE_ID, version: '1.0.0', dependencies: [PLATFORM_SERVICE_IDS.TASKS,
      PLATFORM_SERVICE_IDS.RELATIONSHIPS, PLATFORM_SERVICE_IDS.SEARCH, PROJECT_ASSET_SERVICE_ID],
    factory: ({services: resolved}) => new MigrationService({
      tasks: resolved[PLATFORM_SERVICE_IDS.TASKS],
      relationships: resolved[PLATFORM_SERVICE_IDS.RELATIONSHIPS],
      search: resolved[PLATFORM_SERVICE_IDS.SEARCH],
      projectAssets: resolved[PROJECT_ASSET_SERVICE_ID]})}));
  const removeModule = modules.register(migrationModuleDefinition(services));
  return () => { removeModule(); removers.reverse().forEach((remove) => remove()); };
}
