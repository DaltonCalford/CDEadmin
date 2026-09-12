/////////////////////////////////////////////////////////////
// First-party Schema Comparison module registration.
/////////////////////////////////////////////////////////////

import React from 'react';
import PropTypes from 'prop-types';
import {Box} from '@mui/material';
import {ProjectAssetClient} from '../../projects/ProjectAssetClient';
import {PLATFORM_SERVICE_IDS} from '../../platform/PlatformServices';
import {
  SCHEMA_COMPARE_ASSET_TYPE, SCHEMA_COMPARE_MODULE_ID, validateReference,
} from './contracts';
import {
  SCHEMA_COMPARE_SERVICE_ID, SchemaCompareService,
} from './SchemaCompareService';
import {
  SCHEMA_COMPARE_SURFACES, SchemaCompareNavigator, SchemaCompareWorkspace,
  schemaCompareInspector,
} from './SchemaCompareWorkspace';

const PROJECT_ASSET_SERVICE_ID = 'project.assets';

function serviceFrom(context) {
  const service = context.service ?? context.schemaCompare ??
    context.services?.[SCHEMA_COMPARE_SERVICE_ID];
  if(!service) throw new Error('Schema Comparison runtime service is unavailable.');
  return service;
}

function sessionFrom(args, context) {
  const id = args.sessionId ?? context.sessionId;
  if(!id) throw new Error('Schema Comparison session ID is required.');
  return {service: serviceFrom(context), id};
}

function command(input) {
  return {
    iconKey: 'tool.schema-compare', surfaces: ['tools', 'toolbar'],
    macroCallable: false, aiEligible: false, auditCategory: 'schema_change',
    validateArguments: () => true, enabledWhen: () => true,
    visibleWhen: () => true, disabledReason: 'Schema Comparison action is unavailable.',
    allowedSecurityGroups: [], deniedSecurityGroups: [], createsTask: '',
    confirmationIntent: 'none',
    ...input,
  };
}

function surfaceId(name) { return `schema_compare.${name}`; }

function requiredString(value, label) {
  return typeof value === 'string' && value.trim() ? true : `${label} is required.`;
}

function createSessionCommand(args, context) {
  const service = serviceFrom(context);
  if(args.configureSessionId) return service.configure(args.configureSessionId, {
    ...(args.leftRef ? {leftRef: args.leftRef} : {}),
    ...(args.rightRef ? {rightRef: args.rightRef} : {}),
    ...(args.options ? {options: args.options} : {}),
  });
  const session = service.create(args.content ?? {});
  context.openSurface?.(surfaceId('compare_setup'), {
    toolInstanceId: `schema-compare-${session.id}`, restoreRef: session.id,
    title: args.title ?? 'Schema Comparison',
  });
  return session;
}

function moduleCommands() {
  return [
    command({
      id: 'schema_compare.session.create', label: 'New Schema Comparison',
      description: 'Create or configure a versioned Schema Comparison session.',
      permission: ['schema_compare.view'], execute: createSessionCommand,
    }),
    command({
      id: 'schema_compare.run', label: 'Run Schema Comparison',
      description: 'Capture and compare both schema sources through explicit adapters.',
      permission: ['schema_compare.view'], createsTask: 'schema_compare.diff',
      execute: (args, context) => {
        const {service, id} = sessionFrom(args, context); return service.run(id);
      },
    }),
    command({
      id: 'schema_compare.rename.accept', label: 'Accept Rename Candidate',
      description: 'Explicitly accept a reviewed rename candidate.',
      permission: ['schema_compare.edit_mapping'],
      validateArguments: (args) => requiredString(args.candidateId, 'Candidate ID'),
      execute: (args, context) => {
        const {service, id} = sessionFrom(args, context);
        return service.acceptRename(id, args.candidateId, args.category);
      },
    }),
    command({
      id: 'schema_compare.mapping.set', label: 'Set Equivalence Mapping',
      description: 'Create, replace or revoke an explicit equivalence mapping.',
      permission: ['schema_compare.edit_mapping'],
      validateArguments: (args) => args.mapping && typeof args.mapping === 'object' ? true :
        'Mapping is required.',
      execute: (args, context) => {
        const {service, id} = sessionFrom(args, context);
        return service.setMapping(id, args.mapping);
      },
    }),
    command({
      id: 'schema_compare.plan.generate', label: 'Generate Change Plan',
      description: 'Generate dependency-ordered operations for an explicit live target.',
      permission: ['schema_compare.generate_plan'],
      validateArguments: (args) => {
        try { validateReference(args.targetRef, 'Change-plan target'); return true; }
        catch(error) { return error.message; }
      },
      execute: (args, context) => {
        const {service, id} = sessionFrom(args, context);
        return service.generatePlan(id, {targetRef: args.targetRef,
          selectedDiffIds: args.selectedDiffIds ?? null});
      },
    }),
    command({
      id: 'schema_compare.plan.validate', label: 'Validate Change Plan',
      description: 'Validate the exact selected plan through the target adapter.',
      permission: ['schema_compare.generate_plan'],
      createsTask: 'schema_compare.plan.validation',
      execute: (args, context) => {
        const {service, id} = sessionFrom(args, context); return service.validatePlan(id);
      },
    }),
    command({
      id: 'schema_compare.plan.export', label: 'Export Change Plan',
      description: 'Export a versioned plan and provider-native operations.',
      permission: ['schema_compare.export'],
      execute: (args, context) => {
        const {service, id} = sessionFrom(args, context); return service.exportPlan(id, args);
      },
    }),
    command({
      id: 'schema_compare.plan.apply', label: 'Apply Change Plan',
      description: 'Apply the exact validated plan through the target provider adapter.',
      permission: ['schema_compare.apply'], createsTask: 'schema_compare.plan.apply',
      requiresConfirmation: true, intent: 'destructive',
      confirmationIntent: 'review_then_explicit_confirm',
      validateArguments: (args) => typeof args.confirmation === 'string' ? true :
        'Confirmation value is required.',
      execute: (args, context) => {
        const {service, id} = sessionFrom(args, context);
        return service.applyPlan(id, {confirmation: args.confirmation});
      },
    }),
  ];
}

function runtimeForSurface(registry, descriptor, context) {
  return registry.resolve(SCHEMA_COMPARE_SERVICE_ID).then(async (service) => {
    let sessionId = descriptor.restoreRef || descriptor.context?.sessionId;
    if(descriptor.projectId && descriptor.restoreRef) {
      const client = context.services?.[PROJECT_ASSET_SERVICE_ID] ??
        await registry.resolve(PROJECT_ASSET_SERVICE_ID);
      const asset = await client.asset(descriptor.projectId, descriptor.restoreRef);
      if(asset.asset_type !== SCHEMA_COMPARE_ASSET_TYPE) throw new Error(
        'The requested project asset is not a Schema Comparison asset.'
      );
      sessionId = `schema-compare:${descriptor.projectId}:${descriptor.restoreRef}`;
      try { service.get(sessionId); } catch {
        service.create({id: sessionId, content: asset.content});
      }
    }
    if(!sessionId) sessionId = service.create({}).id;
    try { service.get(sessionId); } catch { service.create({id: sessionId}); }
    const surface = descriptor.toolKind.replace('schema_compare.', '');
    const executeCommand = (id, args, commandContext={}) => context.commands.execute(
      id, {...args, sessionId}, {...context, ...commandContext,
        service, schemaCompare: service, sessionId}
    );
    return React.createElement(SchemaCompareWorkspace, {
      service, sessionId, surface, executeCommand,
      resources: context.schemaCompareResources ?? [],
      assets: context.schemaCompareAssets ?? [], onExport: context.onExport,
      onStateChange: (session) => context.setSurfaceState?.({
        schemaCompareSession: session, schemaCompareInspector: schemaCompareInspector(session),
        dirty: session.dirty, persistence: session.dirty ? 'dirty' : 'clean',
        validation: session.validation?.errors ?? [], activeTaskId: session.activeTaskId,
      }),
    });
  });
}

export function schemaCompareModuleDefinition(registry) {
  return {
    id: SCHEMA_COMPARE_MODULE_ID, version: '1.0.0', title: 'Schema Comparison',
    iconKey: 'tool.schema-compare', serviceRequirements: [SCHEMA_COMPARE_SERVICE_ID],
    contributions: {
      surfaces: SCHEMA_COMPARE_SURFACES.map((surface) => ({
        id: surfaceId(surface.id), title: surface.title,
        iconKey: 'tool.schema-compare', assetTypes: [SCHEMA_COMPARE_ASSET_TYPE],
        readOnly: false, editable: true, detachable: true, duplicable: true,
        fullscreen: true,
        restore: (descriptor, context) => runtimeForSurface(registry, descriptor, context),
        checkpoint: async (_descriptor, context) =>
          context.schemaCompareSession?.content ?? null,
        canClose: async (_descriptor, context) => context.dirty ? {
          allowed: false, reason: 'Save or discard Schema Comparison changes first.',
        } : {allowed: true, reason: ''},
      })),
      commands: moduleCommands(),
      activity: [{
        id: 'activity.schema_compare', label: 'Schema Diff',
        iconKey: 'tool.schema-compare', priority: 30,
        render: () => <SchemaCompareActivity registry={registry} />,
      }],
      inspector: [{
        id: 'inspector.schema_compare', label: 'Schema Comparison', priority: 30,
        when: (context) => context.surfaceId?.startsWith('schema_compare.'),
        render: (context) => context.schemaCompareInspector ?? {},
      }],
      bottom: [
        {id: 'bottom.schema_compare.plan', label: 'Change Plan', priority: 30,
          when: (context) => context.surfaceId?.startsWith('schema_compare.'),
          render: (context) => context.schemaCompareSession?.plan?.operations ?? []},
        {id: 'bottom.schema_compare.native', label: 'Generated Native Script', priority: 31,
          when: (context) => context.surfaceId?.startsWith('schema_compare.'),
          render: (context) => context.schemaCompareSession?.plan?.operations
            ?.map((item) => item.nativeStatement).join('\n\n') ?? ''},
      ],
      status: [{
        id: 'status.schema_compare', label: 'Schema Comparison', priority: 30,
        when: (context) => context.surfaceId?.startsWith('schema_compare.'),
        value: (context) => context.schemaCompareSession?.state ?? 'ready',
      }],
    },
  };
}

function SchemaCompareActivity({registry}) {
  const [service, setService] = React.useState(null);
  React.useEffect(() => { registry.resolve(SCHEMA_COMPARE_SERVICE_ID).then(setService); },
    [registry]);
  if(!service) return <Box sx={{p: 1}}>Loading Schema Comparison…</Box>;
  return <SchemaCompareNavigator service={service}
    onOpen={(sessionId, surface) => window.dispatchEvent(new CustomEvent(
      'cdeadmin:schema-compare-open', {detail: {sessionId, surface}}
    ))} />;
}

SchemaCompareActivity.propTypes = {registry: PropTypes.object};

export function registerSchemaCompareModule({modules, services, api}={}) {
  if(!modules || !services) throw new TypeError(
    'Schema Comparison registration requires module and service registries.'
  );
  const removers = [];
  if(!services.has(PROJECT_ASSET_SERVICE_ID)) removers.push(services.register({
    id: PROJECT_ASSET_SERVICE_ID, version: '1.0.0',
    factory: () => new ProjectAssetClient(api),
  }));
  if(!services.has(SCHEMA_COMPARE_SERVICE_ID)) removers.push(services.register({
    id: SCHEMA_COMPARE_SERVICE_ID, version: '1.0.0',
    dependencies: [PLATFORM_SERVICE_IDS.TASKS, PLATFORM_SERVICE_IDS.RELATIONSHIPS,
      PLATFORM_SERVICE_IDS.SEARCH, PROJECT_ASSET_SERVICE_ID],
    factory: ({services: resolved}) => new SchemaCompareService({
      tasks: resolved[PLATFORM_SERVICE_IDS.TASKS],
      relationships: resolved[PLATFORM_SERVICE_IDS.RELATIONSHIPS],
      search: resolved[PLATFORM_SERVICE_IDS.SEARCH],
      projectAssets: resolved[PROJECT_ASSET_SERVICE_ID],
    }),
  }));
  const removeModule = modules.register(schemaCompareModuleDefinition(services));
  return () => { removeModule(); removers.reverse().forEach((remove) => remove()); };
}
