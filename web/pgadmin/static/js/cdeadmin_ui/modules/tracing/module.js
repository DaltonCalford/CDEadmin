/////////////////////////////////////////////////////////////
// Distributed Tracing module registration and command boundary.
/////////////////////////////////////////////////////////////

import React from 'react';
import PropTypes from 'prop-types';
import {Box} from '@mui/material';
import {ProjectAssetClient} from '../../projects/ProjectAssetClient';
import {PLATFORM_SERVICE_IDS} from '../../platform/PlatformServices';
import {PROJECT_ASSET_SERVICE_ID} from '../../integrations/ddn/module';
import {
  TRACING_ASSET_TYPE, TRACING_MODULE_ID, TRACING_SERVICE_ID,
  validateSourceConfig, validateTraceSamplingPolicy,
} from './contracts';
import {TracingService} from './TracingService';
import {
  TRACING_SURFACES, TracingNavigator, TracingWorkspace, tracingInspector,
} from './TracingWorkspace';

function only(args, fields) {
  const unexpected = Object.keys(args ?? {}).filter((name) => !fields.includes(name));
  return unexpected.length ? `Unknown command argument: ${unexpected[0]}.` : true;
}
function sessionArgs(args, fields=[]) { return only(args, ['sessionId', ...fields]); }
function traceArgs(args, fields=[]) {
  const known = sessionArgs(args, ['traceId', ...fields]); if(known !== true) return known;
  return typeof args.traceId === 'string' && args.traceId ? true : 'Trace ID is required.';
}
function spanArgs(args) {
  const known = traceArgs(args, ['spanId']); if(known !== true) return known;
  return typeof args.spanId === 'string' && args.spanId ? true : 'Span ID is required.';
}
function searchArgs(args) {
  const known = sessionArgs(args, ['filters', 'cursor', 'pageSize']); if(known !== true) return known;
  if(args.filters !== undefined && (!args.filters || Array.isArray(args.filters) ||
      typeof args.filters !== 'object')) return 'Trace filters must be an object.';
  if(args.cursor !== undefined && args.cursor !== null && typeof args.cursor !== 'string') {
    return 'Trace cursor must be a string.';
  }
  if(args.pageSize !== undefined && (!Number.isInteger(args.pageSize) ||
      args.pageSize < 1 || args.pageSize > 1000)) return 'Page size must be from one through 1000.';
  return true;
}
function exportArgs(args) {
  const known = sessionArgs(args, ['traceIds', 'profile']); if(known !== true) return known;
  if(args.traceIds !== undefined && (!Array.isArray(args.traceIds) ||
      args.traceIds.some((id) => typeof id !== 'string' || !id))) return 'Trace IDs must be strings.';
  return args.profile === undefined || (typeof args.profile === 'string' && args.profile) ?
    true : 'Export profile must be a non-empty string.';
}
function runtime(args, context) {
  const service = context.tracing ?? context.services?.[TRACING_SERVICE_ID] ?? context.service;
  if(!service) throw new Error('Distributed tracing runtime service is unavailable.');
  const id = args.sessionId ?? context.sessionId;
  if(!id) throw new Error('Tracing session ID is required.'); return {service, id};
}
function command(input) {
  return {iconKey: 'tool.tracing', surfaces: ['tools', 'toolbar'], macroCallable: false,
    aiEligible: false, auditCategory: 'tracing', validateArguments: () => true,
    enabledWhen: () => true, visibleWhen: () => true,
    disabledReason: 'Tracing action is unavailable.', allowedSecurityGroups: [],
    deniedSecurityGroups: [], createsTask: '', confirmationIntent: 'none', ...input};
}
function commandDefinitions() {
  return [
    command({id: 'trace.search', label: 'Search Traces',
      description: 'Search bounded trace pages with time, resource, service and status filters.',
      permission: ['trace.view'], validateArguments: searchArgs,
      execute: (args, context) => { const {service, id} = runtime(args, context);
        return service.searchTraces(id, args, context); }}),
    command({id: 'trace.open', label: 'Open Trace',
      description: 'Open the selected trace without regenerating its OpenTelemetry identity.',
      permission: ['trace.view'], validateArguments: (args) => traceArgs(args, ['spanId']),
      execute: (args, context) => { const {service, id} = runtime(args, context);
        return service.openTrace(id, args.traceId, args.spanId); }}),
    command({id: 'trace.copy_id', label: 'Copy Trace or Span ID',
      description: 'Return the exact OpenTelemetry trace or span identifier for the clipboard boundary.',
      permission: ['trace.view'], validateArguments: (args) => traceArgs(args, ['spanId']),
      execute: (args, context) => { const {service, id} = runtime(args, context);
        return service.copyId(id, args.traceId, args.spanId); }}),
    command({id: 'trace.resource.open', label: 'Open Related Resource',
      description: 'Resolve the evidenced ResourceRef correlated with a span.',
      permission: ['trace.view'], validateArguments: spanArgs,
      execute: (args, context) => { const {service, id} = runtime(args, context);
        return service.relatedReference(id, args.traceId, args.spanId, 'resource'); }}),
    command({id: 'trace.query.open', label: 'Open Related Query',
      description: 'Resolve the evidenced QueryRef correlated with a span.',
      permission: ['trace.view'], validateArguments: spanArgs,
      execute: (args, context) => { const {service, id} = runtime(args, context);
        return service.relatedReference(id, args.traceId, args.spanId, 'query'); }}),
    command({id: 'trace.export.otel', label: 'Export OpenTelemetry',
      description: 'Export selected traces with explicit OTLP profile and provenance.',
      permission: ['trace.export'], createsTask: 'trace.export', validateArguments: exportArgs,
      execute: (args, context) => { const {service, id} = runtime(args, context);
        return service.export(id, args, context); }}),
    command({id: 'trace.source.configure', label: 'Configure Trace Source',
      description: 'Validate and save an OTLP, internal, provider-native or file source.',
      permission: ['trace.configure_source'], validateArguments: (args) => {
        const known = sessionArgs(args, ['source']); if(known !== true) return known;
        try { validateSourceConfig(args.source); return true; } catch(error) { return error.message; }
      }, execute: (args, context) => { const {service, id} = runtime(args, context);
        return service.configureSource(id, args.source, context); }}),
    command({id: 'trace.sampling.update', label: 'Update Sampling Policy',
      description: 'Validate and save trace sampling, retention and sensitive-attribute policy.',
      permission: ['trace.admin'], validateArguments: (args) => {
        const known = sessionArgs(args, ['policy']); if(known !== true) return known;
        try { validateTraceSamplingPolicy(args.policy); return true; } catch(error) { return error.message; }
      }, execute: (args, context) => { const {service, id} = runtime(args, context);
        return service.updateSampling(id, args.policy, context); }}),
  ];
}

function surfaceRuntime(registry, descriptor, context) {
  return registry.resolve(TRACING_SERVICE_ID).then(async (service) => {
    let sessionId = descriptor.restoreRef || descriptor.context?.sessionId;
    if(descriptor.projectId && descriptor.restoreRef) {
      const client = context.services?.[PROJECT_ASSET_SERVICE_ID] ??
        await registry.resolve(PROJECT_ASSET_SERVICE_ID);
      const asset = await client.asset(descriptor.projectId, descriptor.restoreRef);
      if(asset.asset_type !== TRACING_ASSET_TYPE) throw new Error(
        'The requested project asset is not a distributed tracing asset.'
      );
      sessionId = `tracing:${descriptor.projectId}:${descriptor.restoreRef}`;
      try { service.get(sessionId); } catch { service.create({id: sessionId, content: asset.content}); }
    }
    if(!sessionId) sessionId = service.create({}).id;
    try { service.get(sessionId); } catch { service.create({id: sessionId}); }
    const surface = descriptor.toolKind.replace('tracing.', '');
    const executeCommand = (id, args, commandContext={}) => context.commands.execute(
      id, {...args, sessionId}, {...context, ...commandContext, service, tracing: service, sessionId}
    );
    return React.createElement(TracingWorkspace, {service, sessionId, surface, executeCommand,
      currentUser: context.currentUser ?? {}, onStateChange: (session) =>
        context.setSurfaceState?.({tracingSession: session,
          tracingInspector: tracingInspector(session), dirty: session.dirty,
          persistence: session.dirty ? 'dirty' : 'clean', problems: session.problems,
          activeTaskId: session.activeTaskId})});
  });
}

export function tracingModuleDefinition(registry) {
  return {id: TRACING_MODULE_ID, version: '1.0.0', title: 'Distributed Tracing',
    iconKey: 'tool.tracing', serviceRequirements: [TRACING_SERVICE_ID], contributions: {
      surfaces: TRACING_SURFACES.map((surface) => ({id: `tracing.${surface.id}`,
        title: surface.title, iconKey: 'tool.tracing', assetTypes: [TRACING_ASSET_TYPE],
        readOnly: false, editable: true, detachable: true, duplicable: true, fullscreen: true,
        restore: (descriptor, context) => surfaceRuntime(registry, descriptor, context),
        checkpoint: async (_descriptor, context) => context.tracingSession?.content ?? null,
        canClose: async (_descriptor, context) => context.dirty ? {allowed: false,
          reason: 'Save or discard tracing asset changes first.'} : {allowed: true, reason: ''}})),
      commands: commandDefinitions(),
      activity: [{id: 'activity.tracing', label: 'Distributed Tracing', iconKey: 'tool.tracing',
        priority: 38, render: () => <TracingActivity registry={registry} />}],
      inspector: [{id: 'inspector.tracing', label: 'Tracing', priority: 38,
        when: (context) => context.surfaceId?.startsWith('tracing.'),
        render: (context) => context.tracingInspector ?? {}}],
      bottom: [
        {id: 'bottom.tracing.problems', label: 'Problems', priority: 60,
          when: (context) => context.surfaceId?.startsWith('tracing.'),
          render: (context) => context.tracingSession?.problems ?? []},
        {id: 'bottom.tracing.logs', label: 'Logs', priority: 61,
          when: (context) => context.surfaceId?.startsWith('tracing.'),
          render: (context) => context.tracingSession?.history ?? []},
        {id: 'bottom.tracing.related-queries', label: 'Related Queries', priority: 62,
          when: (context) => context.surfaceId?.startsWith('tracing.'),
          render: (context) => context.tracingInspector?.correlations ?? []},
        {id: 'bottom.tracing.export', label: 'Export', priority: 63,
          when: (context) => context.surfaceId?.startsWith('tracing.'),
          render: (context) => ({activeTaskId: context.tracingSession?.activeTaskId ?? null})}],
      status: [{id: 'status.tracing', label: 'Tracing', priority: 38,
        when: (context) => context.surfaceId?.startsWith('tracing.'),
        value: (context) => context.tracingSession?.state ?? 'ready'}]}};
}

function TracingActivity({registry}) {
  const [service, setService] = React.useState(null);
  React.useEffect(() => { registry.resolve(TRACING_SERVICE_ID).then(setService); }, [registry]);
  if(!service) return <Box sx={{p: 1}}>Loading tracing assets…</Box>;
  return <TracingNavigator service={service} onOpen={(sessionId, surface) =>
    window.dispatchEvent(new CustomEvent('cdeadmin:tracing-open',
      {detail: {sessionId, surface}}))} />;
}
TracingActivity.propTypes = {registry: PropTypes.object};

export function registerTracingModule({modules, services, api}={}) {
  if(!modules || !services) throw new TypeError(
    'Tracing registration requires module and service registries.'
  );
  const removers = [];
  if(!services.has(PROJECT_ASSET_SERVICE_ID)) removers.push(services.register({
    id: PROJECT_ASSET_SERVICE_ID, version: '1.0.0', factory: () => new ProjectAssetClient(api)}));
  if(!services.has(TRACING_SERVICE_ID)) removers.push(services.register({
    id: TRACING_SERVICE_ID, version: '1.0.0', dependencies: [PLATFORM_SERVICE_IDS.TASKS,
      PLATFORM_SERVICE_IDS.RELATIONSHIPS, PLATFORM_SERVICE_IDS.SEARCH, PROJECT_ASSET_SERVICE_ID],
    factory: ({services: resolved}) => new TracingService({
      tasks: resolved[PLATFORM_SERVICE_IDS.TASKS],
      relationships: resolved[PLATFORM_SERVICE_IDS.RELATIONSHIPS],
      search: resolved[PLATFORM_SERVICE_IDS.SEARCH],
      projectAssets: resolved[PROJECT_ASSET_SERVICE_ID]})}));
  const removeModule = modules.register(tracingModuleDefinition(services));
  return () => { removeModule(); removers.reverse().forEach((remove) => remove()); };
}
