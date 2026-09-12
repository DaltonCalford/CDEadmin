/////////////////////////////////////////////////////////////
// Replication Topology module registration and command boundary.
/////////////////////////////////////////////////////////////

import React from 'react';
import PropTypes from 'prop-types';
import {Box} from '@mui/material';
import {ProjectAssetClient} from '../../projects/ProjectAssetClient';
import {PLATFORM_SERVICE_IDS} from '../../platform/PlatformServices';
import {PROJECT_ASSET_SERVICE_ID} from '../../integrations/ddn/module';
import {
  REPLICATION_ASSET_TYPE, REPLICATION_MODULE_ID, validateFailoverPlan,
  validateReplicationReference,
} from './contracts';
import {
  REPLICATION_SERVICE_ID, ReplicationService,
} from './ReplicationService';
import {
  REPLICATION_SURFACES, ReplicationNavigator, ReplicationWorkspace,
  replicationInspector,
} from './ReplicationWorkspace';

function only(args, fields) {
  const unexpected = Object.keys(args ?? {}).filter((name) => !fields.includes(name));
  return unexpected.length ? `Unknown command argument: ${unexpected[0]}.` : true;
}
function sessionArgs(args, fields=[]) { return only(args, ['sessionId', ...fields]); }
function topologyArgs(args, fields=[]) {
  const known = sessionArgs(args, ['topologyId', ...fields]); if(known !== true) return known;
  return typeof args.topologyId === 'string' && args.topologyId ? true : 'Topology ID is required.';
}
function retryArgs(args) {
  const known = topologyArgs(args, ['retry']); if(known !== true) return known;
  if(args.retry === undefined) return true;
  return args.retry && Object.keys(args.retry).every((key) => key === 'maximum') &&
    Number.isInteger(args.retry.maximum) && args.retry.maximum >= 0 && args.retry.maximum <= 5 ?
    true : 'Retry maximum must be an integer from zero through five.';
}
function planArgs(args, fields=[]) {
  const known = sessionArgs(args, ['planId', ...fields]); if(known !== true) return known;
  return typeof args.planId === 'string' && args.planId ? true : 'Plan ID is required.';
}
function planRetryArgs(args) {
  const known = planArgs(args, ['retry']); if(known !== true) return known;
  if(args.retry === undefined) return true;
  return args.retry && Object.keys(args.retry).every((key) => key === 'maximum') &&
    Number.isInteger(args.retry.maximum) && args.retry.maximum >= 0 && args.retry.maximum <= 5 ?
    true : 'Retry maximum must be an integer from zero through five.';
}
function snapshotArgs(args) {
  const known = topologyArgs(args, ['snapshotId', 'name', 'capturedAt', 'reference']);
  if(known !== true) return known;
  if(!['snapshotId', 'name', 'capturedAt'].every((field) =>
    args[field] === undefined || (typeof args[field] === 'string' && args[field]))) {
    return 'Snapshot identifiers, name and capture time must be non-empty strings.';
  }
  try {
    if(args.reference !== undefined) validateReplicationReference(args.reference);
    return true;
  } catch(error) { return error.message; }
}
function runtime(args, context) {
  const service = context.replication ?? context.services?.[REPLICATION_SERVICE_ID] ?? context.service;
  if(!service) throw new Error('Replication runtime service is unavailable.');
  const id = args.sessionId ?? context.sessionId;
  if(!id) throw new Error('Replication session ID is required.'); return {service, id};
}
function command(input) {
  return {iconKey: 'tool.replication', surfaces: ['tools', 'toolbar'], macroCallable: false,
    aiEligible: false, auditCategory: 'replication', validateArguments: () => true,
    enabledWhen: () => true, visibleWhen: () => true,
    disabledReason: 'Replication action is unavailable.', allowedSecurityGroups: [],
    deniedSecurityGroups: [], createsTask: '', confirmationIntent: 'none', ...input};
}

function commandDefinitions() {
  return [
    command({id: 'replication.topology.refresh', label: 'Refresh Replication Topology',
      description: 'Discover exact provider-native participants, roles, links, positions and lag.',
      permission: ['replication.view'], createsTask: 'replication.discovery',
      validateArguments: retryArgs, execute: (args, context) => {
        const {service, id} = runtime(args, context);
        return service.refresh(id, args.topologyId, {retry: args.retry}, context);
      }}),
    ...['pause', 'resume'].map((action) => command({id: `replication.link.${action}`,
      label: `${action[0].toUpperCase()}${action.slice(1)} Replication Link`,
      description: `${action} the selected provider-native replication link.`,
      permission: ['replication.control'], confirmationIntent: 'standard_consequential',
      validateArguments: (args) => {
        const known = topologyArgs(args, ['linkId']); if(known !== true) return known;
        return typeof args.linkId === 'string' && args.linkId ? true : 'Link ID is required.';
      }, execute: (args, context) => { const {service, id} = runtime(args, context);
        return service.controlLink(id, args.topologyId, args.linkId, action, context); }})),
    command({id: 'replication.failover.plan', label: 'Create Failover Plan',
      description: 'Create or replace a reviewable provider-specific failover runbook.',
      permission: ['replication.plan_failover'], confirmationIntent: 'standard_consequential',
      validateArguments: (args) => {
        const known = sessionArgs(args, ['plan']); if(known !== true) return known;
        try { validateFailoverPlan(args.plan); return true; } catch(error) { return error.message; }
      }, execute: (args, context) => { const {service, id} = runtime(args, context);
        return service.upsertPlan(id, args.plan, context); }}),
    command({id: 'replication.failover.validate', label: 'Validate Failover Plan',
      description: 'Require provider quorum, candidate, position and data-loss evidence.',
      permission: ['replication.plan_failover'], confirmationIntent: 'standard_consequential',
      validateArguments: (args) => planArgs(args),
      execute: (args, context) => { const {service, id} = runtime(args, context);
        return service.validateFailover(id, args.planId, context); }}),
    command({id: 'replication.failover.arm', label: 'Arm Failover Plan',
      description: 'Arm the exact validated plan revision for a named environment and connection.',
      permission: ['replication.execute_failover'], confirmationIntent: 'standard_consequential',
      validateArguments: (args) => {
        const known = planArgs(args, ['confirmationRef', 'environment', 'connection']);
        if(known !== true) return known;
        return ['confirmationRef', 'environment', 'connection'].every(
          (field) => typeof args[field] === 'string' && args[field]) ? true :
          'Confirmation reference, environment and connection are required.';
      },
      execute: (args, context) => { const {service, id} = runtime(args, context);
        return service.armFailover(id, args.planId, {confirmationRef: args.confirmationRef,
          environment: args.environment, connection: args.connection}, context); }}),
    command({id: 'replication.failover.execute', label: 'Execute Failover Plan',
      description: 'Execute the armed provider command and verify a re-discovered topology.',
      permission: ['replication.execute_failover'], createsTask: 'replication.failover.execution',
      confirmationIntent: 'standard_consequential', validateArguments: planRetryArgs,
      execute: (args, context) => { const {service, id} = runtime(args, context);
        return service.executeFailover(id, args.planId, {retry: args.retry}, context); }}),
    command({id: 'replication.snapshot.create', label: 'Create Replication Snapshot',
      description: 'Capture the current discovered topology as deterministic authored source.',
      permission: ['replication.view'], validateArguments: snapshotArgs,
      execute: (args, context) => { const {service, id} = runtime(args, context);
        return service.createSnapshot(id, args.topologyId, args, context); }}),
  ];
}

function surfaceRuntime(registry, descriptor, context) {
  return registry.resolve(REPLICATION_SERVICE_ID).then(async (service) => {
    let sessionId = descriptor.restoreRef || descriptor.context?.sessionId;
    if(descriptor.projectId && descriptor.restoreRef) {
      const client = context.services?.[PROJECT_ASSET_SERVICE_ID] ??
        await registry.resolve(PROJECT_ASSET_SERVICE_ID);
      const asset = await client.asset(descriptor.projectId, descriptor.restoreRef);
      if(asset.asset_type !== REPLICATION_ASSET_TYPE) throw new Error(
        'The requested project asset is not a replication topology asset.'
      );
      sessionId = `replication:${descriptor.projectId}:${descriptor.restoreRef}`;
      try { service.get(sessionId); } catch { service.create({id: sessionId, content: asset.content}); }
    }
    if(!sessionId) sessionId = service.create({}).id;
    try { service.get(sessionId); } catch { service.create({id: sessionId}); }
    const surface = descriptor.toolKind.replace('replication.', '');
    const executeCommand = (id, args, commandContext={}) => context.commands.execute(
      id, {...args, sessionId}, {...context, ...commandContext, service, replication: service, sessionId}
    );
    return React.createElement(ReplicationWorkspace, {service, sessionId, surface, executeCommand,
      currentUser: context.currentUser ?? {}, onStateChange: (session) =>
        context.setSurfaceState?.({replicationSession: session,
          replicationInspector: replicationInspector(session), dirty: session.dirty,
          persistence: session.dirty ? 'dirty' : 'clean', problems: session.problems,
          activeTaskId: session.activeTaskId})});
  });
}

export function replicationModuleDefinition(registry) {
  return {id: REPLICATION_MODULE_ID, version: '1.0.0', title: 'Replication Topology',
    iconKey: 'tool.replication', serviceRequirements: [REPLICATION_SERVICE_ID], contributions: {
      surfaces: REPLICATION_SURFACES.map((surface) => ({id: `replication.${surface.id}`,
        title: surface.title, iconKey: 'tool.replication', assetTypes: [REPLICATION_ASSET_TYPE],
        readOnly: false, editable: true, detachable: true, duplicable: true, fullscreen: true,
        restore: (descriptor, context) => surfaceRuntime(registry, descriptor, context),
        checkpoint: async (_descriptor, context) => context.replicationSession?.content ?? null,
        canClose: async (_descriptor, context) => context.dirty ? {allowed: false,
          reason: 'Save or discard replication asset changes first.'} : {allowed: true, reason: ''}})),
      commands: commandDefinitions(),
      activity: [{id: 'activity.replication', label: 'Replication', iconKey: 'tool.replication',
        priority: 37, render: () => <ReplicationActivity registry={registry} />}],
      inspector: [{id: 'inspector.replication', label: 'Replication', priority: 37,
        when: (context) => context.surfaceId?.startsWith('replication.'),
        render: (context) => context.replicationInspector ?? {}}],
      bottom: [
        {id: 'bottom.replication.lag-history', label: 'Lag History', priority: 50,
          when: (context) => context.surfaceId?.startsWith('replication.'),
          render: (context) => context.replicationSession?.liveTopologies ?? []},
        {id: 'bottom.replication.problems', label: 'Problems', priority: 51,
          when: (context) => context.surfaceId?.startsWith('replication.'),
          render: (context) => context.replicationSession?.problems ?? []},
        {id: 'bottom.replication.failover', label: 'Failover Plan', priority: 52,
          when: (context) => context.surfaceId?.startsWith('replication.'),
          render: (context) => context.replicationSession?.failoverReviews ?? []},
        {id: 'bottom.replication.tasks', label: 'Tasks', priority: 53,
          when: (context) => context.surfaceId?.startsWith('replication.'),
          render: (context) => ({activeTaskId: context.replicationSession?.activeTaskId ?? null})}],
      status: [{id: 'status.replication', label: 'Replication', priority: 37,
        when: (context) => context.surfaceId?.startsWith('replication.'),
        value: (context) => context.replicationSession?.state ?? 'ready'}]}};
}

function ReplicationActivity({registry}) {
  const [service, setService] = React.useState(null);
  React.useEffect(() => { registry.resolve(REPLICATION_SERVICE_ID).then(setService); }, [registry]);
  if(!service) return <Box sx={{p: 1}}>Loading replication assets…</Box>;
  return <ReplicationNavigator service={service} onOpen={(sessionId, surface) =>
    window.dispatchEvent(new CustomEvent('cdeadmin:replication-open',
      {detail: {sessionId, surface}}))} />;
}
ReplicationActivity.propTypes = {registry: PropTypes.object};

export function registerReplicationModule({modules, services, api}={}) {
  if(!modules || !services) throw new TypeError(
    'Replication registration requires module and service registries.'
  );
  const removers = [];
  if(!services.has(PROJECT_ASSET_SERVICE_ID)) removers.push(services.register({
    id: PROJECT_ASSET_SERVICE_ID, version: '1.0.0', factory: () => new ProjectAssetClient(api)}));
  if(!services.has(REPLICATION_SERVICE_ID)) removers.push(services.register({
    id: REPLICATION_SERVICE_ID, version: '1.0.0', dependencies: [PLATFORM_SERVICE_IDS.TASKS,
      PLATFORM_SERVICE_IDS.RELATIONSHIPS, PLATFORM_SERVICE_IDS.SEARCH, PROJECT_ASSET_SERVICE_ID],
    factory: ({services: resolved}) => new ReplicationService({
      tasks: resolved[PLATFORM_SERVICE_IDS.TASKS],
      relationships: resolved[PLATFORM_SERVICE_IDS.RELATIONSHIPS],
      search: resolved[PLATFORM_SERVICE_IDS.SEARCH],
      projectAssets: resolved[PROJECT_ASSET_SERVICE_ID]})}));
  const removeModule = modules.register(replicationModuleDefinition(services));
  return () => { removeModule(); removers.reverse().forEach((remove) => remove()); };
}
