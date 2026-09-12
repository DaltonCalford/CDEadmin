/////////////////////////////////////////////////////////////
// First-party Data Lineage module registration.
/////////////////////////////////////////////////////////////

import React from 'react';
import PropTypes from 'prop-types';
import {Box} from '@mui/material';
import {ProjectAssetClient} from '../../projects/ProjectAssetClient';
import {PLATFORM_SERVICE_IDS} from '../../platform/PlatformServices';
import {PROJECT_ASSET_SERVICE_ID} from '../../integrations/ddn/module';
import {
  LINEAGE_ASSET_TYPE, LINEAGE_EDGE_TYPES, LINEAGE_MODULE_ID, validateLineageRef,
} from './contracts';
import {LINEAGE_SERVICE_ID, LineageService} from './LineageService';
import {
  LINEAGE_SURFACES, LineageNavigator, LineageWorkspace, lineageInspector,
} from './LineageWorkspace';

function serviceFrom(context) {
  const service = context.lineage ?? context.services?.[LINEAGE_SERVICE_ID] ?? context.service;
  if(!service) throw new Error('Data Lineage runtime service is unavailable.');
  return service;
}

function sessionFrom(args, context) {
  const id = args.sessionId ?? context.sessionId;
  if(!id) throw new Error('Data Lineage session ID is required.');
  return {service: serviceFrom(context), id};
}

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

function depthArguments(args, maximum=10, allowed=[]) {
  const known = sessionArguments(args, ['nodeId', 'depth', 'edgeTypes', ...allowed]);
  if(known !== true) return known;
  const node = required(args.nodeId, 'Node ID'); if(node !== true) return node;
  if(args.depth !== undefined && (!Number.isInteger(args.depth) || args.depth < 1 ||
      args.depth > maximum)) return `Depth must be an integer between 1 and ${maximum}.`;
  if(args.edgeTypes !== undefined && (!Array.isArray(args.edgeTypes) ||
      args.edgeTypes.some((type) => !LINEAGE_EDGE_TYPES.includes(type)))) {
    return 'Edge types are invalid.';
  }
  return true;
}

function command(input) {
  return {iconKey: 'tool.lineage', surfaces: ['tools', 'toolbar'], macroCallable: false,
    aiEligible: false, auditCategory: 'data_lineage', validateArguments: () => true,
    enabledWhen: () => true, visibleWhen: () => true,
    disabledReason: 'Data Lineage action is unavailable.', allowedSecurityGroups: [],
    deniedSecurityGroups: [], createsTask: '', confirmationIntent: 'none', ...input};
}

function moduleCommands() {
  return [
    command({id: 'lineage.graph.refresh', label: 'Refresh Lineage',
      description: 'Discover, parse, and reconcile lineage evidence for the selected scope.',
      permission: ['lineage.scan'], createsTask: 'lineage.reconcile',
      validateArguments: (args) => {
        const known = sessionArguments(args, ['scopeRefs', 'retry']);
        if(known !== true) return known;
        try { (args.scopeRefs ?? []).forEach(validateLineageRef); } catch(error) {
          return error.message;
        }
        const maximum = args.retry?.maximum;
        return args.retry === undefined || (args.retry &&
          Object.keys(args.retry).every((key) => key === 'maximum') &&
          Number.isInteger(maximum) && maximum >= 0 && maximum <= 5) ? true :
          'Retry maximum must be an integer from zero through five.';
      },
      execute: (args, context) => {
        const {service, id} = sessionFrom(args, context);
        if(args.scopeRefs) service.configure(id, {scopeRefs: args.scopeRefs});
        return service.refresh(id, args.retry ? {retry: args.retry} : {});
      }}),
    command({id: 'lineage.node.trace_upstream', label: 'Trace Upstream',
      description: 'Traverse bounded upstream lineage from a stable node identity.',
      permission: ['lineage.view'], validateArguments: (args) => depthArguments(args),
      execute: (args, context) => {
        const {service, id} = sessionFrom(args, context);
        return service.trace(id, {...args, direction: 'in'});
      }}),
    command({id: 'lineage.node.trace_downstream', label: 'Trace Downstream',
      description: 'Traverse bounded downstream lineage from a stable node identity.',
      permission: ['lineage.view'], validateArguments: (args) => depthArguments(args),
      execute: (args, context) => {
        const {service, id} = sessionFrom(args, context);
        return service.trace(id, {...args, direction: 'out'});
      }}),
    command({id: 'lineage.impact.run', label: 'Run Impact Analysis',
      description: 'Compute a bounded background impact traversal and risk summary.',
      permission: ['lineage.view'], createsTask: 'lineage.impact.compute',
      validateArguments: (args) => depthArguments(args, 100),
      execute: (args, context) => {
        const {service, id} = sessionFrom(args, context); return service.runImpact(id, args);
      }}),
    command({id: 'lineage.edge.curate', label: 'Curate Lineage Edge',
      description: 'Add explicit user-curated evidence without deleting source evidence.',
      permission: ['lineage.curate'], validateArguments: (args) => {
        const known = sessionArguments(args, ['edgeId', 'type', 'note']);
        if(known !== true) return known;
        const edge = required(args.edgeId, 'Edge ID');
        if(edge !== true) return edge;
        if(args.note !== undefined && (typeof args.note !== 'string' || args.note.length > 4096)) {
          return 'Curation note is invalid.';
        }
        return !args.type || LINEAGE_EDGE_TYPES.includes(args.type) ? true : 'Edge type is invalid.';
      }, execute: (args, context) => {
        const {service, id} = sessionFrom(args, context); return service.curateEdge(id, args);
      }}),
    command({id: 'lineage.edge.suppress_inference', label: 'Suppress Inferred Edge',
      description: 'Suppress inference-only presentation while retaining original evidence.',
      permission: ['lineage.curate'], validateArguments: (args) => {
        const known = sessionArguments(args, ['edgeId']);
        return known === true ? required(args.edgeId, 'Edge ID') : known;
      },
      execute: (args, context) => {
        const {service, id} = sessionFrom(args, context); return service.suppressInference(id, args);
      }}),
    command({id: 'lineage.snapshot.create', label: 'Create Lineage Snapshot',
      description: 'Capture a versioned lineage graph snapshot.', permission: ['lineage.curate'],
      validateArguments: (args) => sessionArguments(args, ['snapshotId']),
      execute: (args, context) => {
        const {service, id} = sessionFrom(args, context); return service.snapshot(id, args);
      }}),
    command({id: 'lineage.snapshot.compare', label: 'Compare Lineage Snapshots',
      description: 'Compare two exact lineage graph revisions.', permission: ['lineage.view'],
      validateArguments: (args) => {
        const known = sessionArguments(args, ['leftId', 'rightId']);
        if(known !== true) return known;
        const left = required(args.leftId, 'Earlier snapshot');
        return left === true ? required(args.rightId, 'Later snapshot') : left;
      },
      execute: (args, context) => {
        const {service, id} = sessionFrom(args, context); return service.compareSnapshots(id, args);
      }}),
    command({id: 'lineage.import.openlineage', label: 'Import OpenLineage',
      description: 'Import versioned OpenLineage events while retaining unknown facets.',
      permission: ['lineage.curate'], createsTask: 'lineage.import',
      validateArguments: (args) => {
        const known = sessionArguments(args, ['events']);
        if(known !== true) return known;
        return args.events && (Array.isArray(args.events) || typeof args.events === 'object') ?
          true : 'OpenLineage event data is required.';
      },
      execute: (args, context) => {
        const {service, id} = sessionFrom(args, context); return service.importOpenLineage(id, args);
      }}),
    command({id: 'lineage.export.openlineage', label: 'Export OpenLineage',
      description: 'Export preserved OpenLineage events without dropping unknown facets.',
      permission: ['lineage.export'], validateArguments: (args) => sessionArguments(args),
      execute: (args, context) => {
        const {service, id} = sessionFrom(args, context); return service.exportOpenLineage(id);
      }}),
    command({id: 'lineage.view.open_ddn', label: 'Open Lineage in DDN',
      description: 'Open a non-authoritative DDN projection bound to stable lineage references.',
      permission: ['lineage.view'], validateArguments: (args) => sessionArguments(args),
      execute: (args, context) => {
        const {service, id} = sessionFrom(args, context); const projection = service.openDDN(id);
        context.openLineageDDN?.(projection); return projection;
      }}),
  ];
}

function surfaceId(name) { return `lineage.${name}`; }

function runtimeForSurface(registry, descriptor, context) {
  return registry.resolve(LINEAGE_SERVICE_ID).then(async (service) => {
    let sessionId = descriptor.restoreRef || descriptor.context?.sessionId;
    if(descriptor.projectId && descriptor.restoreRef) {
      const client = context.services?.[PROJECT_ASSET_SERVICE_ID] ??
        await registry.resolve(PROJECT_ASSET_SERVICE_ID);
      const asset = await client.asset(descriptor.projectId, descriptor.restoreRef);
      if(asset.asset_type !== LINEAGE_ASSET_TYPE) throw new Error(
        'The requested project asset is not a Data Lineage asset.'
      );
      sessionId = `lineage:${descriptor.projectId}:${descriptor.restoreRef}`;
      try { service.get(sessionId); } catch {
        service.create({id: sessionId, content: asset.content});
      }
    }
    if(!sessionId) sessionId = service.create({}).id;
    try { service.get(sessionId); } catch { service.create({id: sessionId}); }
    const surface = descriptor.toolKind.replace('lineage.', '');
    const executeCommand = (id, args, commandContext={}) => context.commands.execute(
      id, {...args, sessionId}, {...context, ...commandContext, service, lineage: service, sessionId}
    );
    return React.createElement(LineageWorkspace, {service, sessionId, surface,
      executeCommand, resources: context.lineageResources ?? [], onExport: context.onExport,
      onStateChange: (session) => context.setSurfaceState?.({lineageSession: session,
        lineageInspector: lineageInspector(session), dirty: session.dirty,
        persistence: session.dirty ? 'dirty' : 'clean', problems: session.problems,
        activeTaskId: session.activeTaskId})});
  });
}

export function lineageModuleDefinition(registry) {
  return {id: LINEAGE_MODULE_ID, version: '1.0.0', title: 'Data Lineage',
    iconKey: 'tool.lineage', serviceRequirements: [LINEAGE_SERVICE_ID], contributions: {
      surfaces: LINEAGE_SURFACES.map((surface) => ({id: surfaceId(surface.id),
        title: surface.title, iconKey: 'tool.lineage', assetTypes: [LINEAGE_ASSET_TYPE],
        readOnly: false, editable: true, detachable: true, duplicable: true, fullscreen: true,
        restore: (descriptor, context) => runtimeForSurface(registry, descriptor, context),
        checkpoint: async (_descriptor, context) => context.lineageSession?.content ?? null,
        canClose: async (_descriptor, context) => context.dirty ? {allowed: false,
          reason: 'Save or discard Data Lineage changes first.'} : {allowed: true, reason: ''}})),
      commands: moduleCommands(),
      activity: [{id: 'activity.lineage', label: 'Lineage', iconKey: 'tool.lineage',
        priority: 31, render: () => <LineageActivity registry={registry} />}],
      inspector: [{id: 'inspector.lineage', label: 'Lineage', priority: 31,
        when: (context) => context.surfaceId?.startsWith('lineage.'),
        render: (context) => context.lineageInspector ?? {}}],
      bottom: [
        {id: 'bottom.lineage.evidence', label: 'Evidence', priority: 32,
          when: (context) => context.surfaceId?.startsWith('lineage.'),
          render: (context) => context.lineageSession?.graph?.edges.flatMap(
            (edge) => edge.evidence) ?? []},
        {id: 'bottom.lineage.history', label: 'Change History', priority: 33,
          when: (context) => context.surfaceId?.startsWith('lineage.'),
          render: (context) => context.lineageSession?.history ?? []}],
      status: [{id: 'status.lineage', label: 'Lineage', priority: 31,
        when: (context) => context.surfaceId?.startsWith('lineage.'),
        value: (context) => context.lineageSession?.state ?? 'ready'}]}};
}

function LineageActivity({registry}) {
  const [service, setService] = React.useState(null);
  React.useEffect(() => { registry.resolve(LINEAGE_SERVICE_ID).then(setService); }, [registry]);
  if(!service) return <Box sx={{p: 1}}>Loading Data Lineage…</Box>;
  return <LineageNavigator service={service} onOpen={(sessionId, surface) =>
    window.dispatchEvent(new CustomEvent('cdeadmin:lineage-open', {
      detail: {sessionId, surface}}))} />;
}

LineageActivity.propTypes = {registry: PropTypes.object};

export function registerLineageModule({modules, services, api}={}) {
  if(!modules || !services) throw new TypeError(
    'Data Lineage registration requires module and service registries.'
  );
  const removers = [];
  if(!services.has(PROJECT_ASSET_SERVICE_ID)) removers.push(services.register({
    id: PROJECT_ASSET_SERVICE_ID, version: '1.0.0', factory: () => new ProjectAssetClient(api)}));
  if(!services.has(LINEAGE_SERVICE_ID)) removers.push(services.register({
    id: LINEAGE_SERVICE_ID, version: '1.0.0',
    dependencies: [PLATFORM_SERVICE_IDS.TASKS, PLATFORM_SERVICE_IDS.RELATIONSHIPS,
      PLATFORM_SERVICE_IDS.SEARCH, PROJECT_ASSET_SERVICE_ID],
    factory: ({services: resolved}) => new LineageService({
      tasks: resolved[PLATFORM_SERVICE_IDS.TASKS],
      relationships: resolved[PLATFORM_SERVICE_IDS.RELATIONSHIPS],
      search: resolved[PLATFORM_SERVICE_IDS.SEARCH],
      projectAssets: resolved[PROJECT_ASSET_SERVICE_ID]})}));
  const removeModule = modules.register(lineageModuleDefinition(services));
  return () => { removeModule(); removers.reverse().forEach((remove) => remove()); };
}
