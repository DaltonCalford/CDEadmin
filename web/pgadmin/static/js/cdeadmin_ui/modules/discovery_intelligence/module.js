/////////////////////////////////////////////////////////////
// Activated zero-grey Discovery Intelligence module boundary.
/////////////////////////////////////////////////////////////

import React from 'react';
import {Box} from '@mui/material';
import {ProjectAssetClient} from '../../projects/ProjectAssetClient';
import {PLATFORM_SERVICE_IDS} from '../../platform/PlatformServices';
import {PROJECT_ASSET_SERVICE_ID} from '../../integrations/ddn/module';
import {DISCOVERY_COMMAND_CATALOG, DISCOVERY_INTELLIGENCE_MANIFEST,
  DISCOVERY_INTELLIGENCE_MODULE_ID, DISCOVERY_PERMISSION_CATALOG} from
  '../../specifications/ai_discovery_zero_grey';
import {DISCOVERY_INTERFACE_SCREENS, DISCOVERY_SCREEN_GROUPS} from
  './DiscoveryInterfaceContracts';
import {DiscoveryInterfaceWorkspace} from './DiscoveryInterfaceWorkspace';
import {createDiscoveryAIToolBridge} from './DiscoveryAIToolBridge';
import {DISCOVERY_INTELLIGENCE_RUNTIME_SERVICE_ID,
  DiscoveryIntelligenceRuntimeService} from
  './DiscoveryIntelligenceRuntimeService';
import {secureDiscoveryFormCommandArguments} from
  './DiscoveryFormCommandAdapter';

export {DISCOVERY_INTELLIGENCE_MODULE_ID,
  DISCOVERY_INTELLIGENCE_RUNTIME_SERVICE_ID};

const PERMISSIONS = Object.freeze({
  'discovery.search.run': 'discovery.use',
  'discovery.search.save': 'discovery.use',
  'discovery.search.feedback': 'discovery.use',
  'discovery.entity.open': 'discovery.use',
  'discovery.entity.show_surfaces': 'discovery.use',
  'discovery.related.find': 'discovery.use',
  'discovery.product.create': 'discovery.manage_products',
  'discovery.product.update': 'discovery.manage_products',
  'discovery.product.add_member': 'discovery.manage_products',
  'discovery.product.remove_member': 'discovery.manage_products',
  'discovery.product.request_certification': 'discovery.manage_products',
  'discovery.product.status.set': 'discovery.certify',
  'discovery.term.create': 'discovery.curate',
  'discovery.term.update': 'discovery.curate',
  'discovery.term.map': 'discovery.curate',
  'discovery.term.status.set': 'discovery.curate',
  'discovery.metric.create': 'discovery.curate',
  'discovery.metric.update': 'discovery.curate',
  'discovery.metric.certify': 'discovery.certify',
  'discovery.certification.review': 'discovery.certify',
  'discovery.certification.revoke': 'discovery.certify',
  'discovery.access.request.create': 'discovery.request_access',
  'discovery.access.request.submit': 'discovery.request_access',
  'discovery.access.request.approve': 'discovery.approve_access',
  'discovery.access.request.deny': 'discovery.approve_access',
  'discovery.access.provision': 'discovery.approve_access',
  'discovery.access.revoke': 'discovery.approve_access',
  'discovery.index.source.create': 'discovery.manage_index',
  'discovery.index.source.update': 'discovery.manage_index',
  'discovery.index.refresh': 'discovery.manage_index',
  'discovery.index.reconcile': 'discovery.manage_index',
  'discovery.index.publish_revision': 'discovery.manage_index',
  'discovery.ranking.create': 'discovery.manage_ranking',
  'discovery.ranking.update': 'discovery.manage_ranking',
  'discovery.ranking.test': 'discovery.manage_ranking',
  'discovery.ranking.publish': 'discovery.manage_ranking',
  'discovery.synonym.update': 'discovery.curate',
  'discovery.curation.assign': 'discovery.curate',
  'discovery.curation.resolve': 'discovery.curate',
  'discovery.enrichment.accept': 'discovery.curate',
  'discovery.enrichment.reject': 'discovery.curate',
  'discovery.duplicate.link_equivalent': 'discovery.curate',
  'discovery.duplicate.reject': 'discovery.curate',
  'discovery.visibility.test': 'discovery.test_visibility',
  'discovery.analytics.export': 'discovery.admin',
  'discovery.open.query': 'discovery.use',
  'discovery.open.sbsql': 'discovery.use',
  'discovery.open.compatibility': 'discovery.use',
  'discovery.open.bi': 'discovery.use',
  'discovery.ask_ai': 'discovery.use',
});

function label(value) {
  return String(value).replace(/^discovery\./, '').split('.').flatMap((part) =>
    part.split('_')).map((part) => `${part[0].toUpperCase()}${part.slice(1)}`)
    .join(' ');
}

function runtimeFrom(registry, context={}) {
  const runtime = context.discoveryIntelligence ??
    context.services?.[DISCOVERY_INTELLIGENCE_RUNTIME_SERVICE_ID] ??
    registry.active(DISCOVERY_INTELLIGENCE_RUNTIME_SERVICE_ID);
  if(!runtime || typeof runtime.execute !== 'function') throw new Error(
    'Discovery Intelligence runtime authority is unavailable.'
  );
  return runtime;
}

function taskType(item) {
  if(!item.task) return '';
  if(item.id === 'discovery.access.provision') return 'discovery.access.provision';
  if(item.id === 'discovery.index.refresh' ||
      item.id === 'discovery.index.reconcile') return 'discovery.index.reconcile';
  return `${item.id}.task`;
}

export function discoveryCommandDefinitions(registry) {
  return DISCOVERY_COMMAND_CATALOG.commands.map((item) => Object.freeze({
    id: item.id, label: label(item.id),
    description: `${label(item.id)} through the governed Discovery authority.`,
    iconKey: 'tool.search', permission: [PERMISSIONS[item.id]],
    surfaces: ['tools', 'toolbar'], macroCallable: item.class !== 'live_mutation',
    aiEligible: false, aiExposure: 'hidden', authority: item.authority,
    task: item.task, createsTask: taskType(item),
    auditCategory: `discovery.${item.class}`,
    requiresConfirmation: item.class === 'live_mutation',
    confirmationIntent: item.class === 'live_mutation' ?
      'provider_mutation' : 'none',
    enabledWhen: (context) => {
      const runtime = context.discoveryIntelligence ??
        registry.active(DISCOVERY_INTELLIGENCE_RUNTIME_SERVICE_ID);
      return runtime?.canExecute(item.id, context) === true;
    },
    visibleWhen: () => true,
    disabledReason: `${label(item.id)} authority is not configured.`,
    validateArguments: (args) => args && !Array.isArray(args) &&
      typeof args === 'object' ? true : 'Command arguments must be an object.',
    execute: (args, context) => runtimeFrom(registry, context).execute(
      item.id, args, context),
  }));
}

function permissionDefinitions() {
  return DISCOVERY_PERMISSION_CATALOG.permissions.map(([id, description]) =>
    ({id, description}));
}

function surfaceRuntime(registry, descriptor, context) {
  return registry.resolve(DISCOVERY_INTELLIGENCE_RUNTIME_SERVICE_ID).then(
    (runtime) => React.createElement(DiscoveryInterfaceWorkspace, {
      ...(context.discoveryWorkspace ?? {}), screenId: descriptor.toolKind,
      data: runtime.screenData(descriptor.toolKind),
      commandAvailability: Object.fromEntries(discoveryCommandDefinitions(
        registry).map((command) => [command.id, command.enabledWhen({
        ...context, discoveryIntelligence: runtime})])),
      executeCommand: (id, args, commandContext={}) =>
        context.commands.execute(id, secureDiscoveryFormCommandArguments(args),
          {...context, ...commandContext,
            discoveryIntelligence: runtime}),
      onNavigate: (next) => context.openSurface?.({toolKind: next}),
      onDetach: context.detachSurface ? () =>
        context.detachSurface(descriptor.id) : null,
      onStateChange: context.setSurfaceState,
    }));
}

function DiscoveryActivity() {
  return <Box component="nav" aria-label="Discovery Intelligence">
    {DISCOVERY_SCREEN_GROUPS.map((group) => <Box key={group.id} sx={{p: 1}}>
      <Box component="strong">{group.label}</Box>
      {group.screens.map((screen) => <Box key={screen}
        component="button" type="button" onClick={() => window.dispatchEvent(
          new CustomEvent('cdeadmin:discovery-open', {detail: {screenId:
            `${DISCOVERY_INTELLIGENCE_MODULE_ID}.${screen}`}}))}
        sx={{display: 'block', border: 0, bgcolor: 'transparent',
          cursor: 'pointer'}}>{label(screen)}</Box>)}
    </Box>)}
  </Box>;
}

export function discoveryIntelligenceModuleDefinition(registry) {
  return {id: DISCOVERY_INTELLIGENCE_MODULE_ID, version: '1.0.0',
    title: 'Discovery Intelligence', iconKey: 'tool.search',
    serviceRequirements: [DISCOVERY_INTELLIGENCE_RUNTIME_SERVICE_ID],
    contributions: {permissions: permissionDefinitions(),
      commands: discoveryCommandDefinitions(registry),
      surfaces: DISCOVERY_INTERFACE_SCREENS.map((screen) => ({
        id: screen.screen_id,
        title: label(screen.screen_id.replace(
          `${DISCOVERY_INTELLIGENCE_MODULE_ID}.`, '')),
        iconKey: 'tool.search',
        assetTypes: [...DISCOVERY_INTELLIGENCE_MANIFEST.assetTypes],
        readOnly: false, editable: true, detachable: true, duplicable: true,
        fullscreen: true,
        restore: (descriptor, context) => surfaceRuntime(registry,
          descriptor, context),
        checkpoint: async (_descriptor, context) =>
          context.discoveryState ?? null,
        canClose: async (_descriptor, context) => context.dirty ?
          {allowed: false,
            reason: 'Save or discard Discovery changes first.'} :
          {allowed: true, reason: ''}})),
      activity: [{id: 'activity.discovery-intelligence',
        label: 'Discovery Intelligence', iconKey: 'tool.search', priority: 46,
        render: () => <DiscoveryActivity />}],
      inspector: [{id: 'inspector.discovery-intelligence',
        label: 'Discovery Intelligence', priority: 46,
        when: (context) => context.surfaceId?.startsWith(
          `${DISCOVERY_INTELLIGENCE_MODULE_ID}.`),
        render: (context) => context.discoveryInspector ?? {}}],
      bottom: [{id: 'bottom.discovery-intelligence.tasks', label: 'Tasks',
        priority: 96, when: (context) => context.surfaceId?.startsWith(
          `${DISCOVERY_INTELLIGENCE_MODULE_ID}.`),
        render: (context) => context.discoveryTasks ?? []}],
      status: [{id: 'status.discovery-intelligence',
        label: 'Discovery Intelligence', priority: 46,
        when: (context) => context.surfaceId?.startsWith(
          `${DISCOVERY_INTELLIGENCE_MODULE_ID}.`),
        value: (context) => context.discoveryState ?? 'ready'}]},
    activate: async ({services}) => {
      const runtime = services[DISCOVERY_INTELLIGENCE_RUNTIME_SERVICE_ID];
      const removers = [runtime.federatedSearch.register({
        id: 'discovery-intelligence', types: ['discovery'], priority: 30,
        search: async (query, {limit, context}) => {
          const result = await runtime.search.search({text: query,
            pageSize: Math.min(limit, 200)}, {security:
              context.discoverySecurity, context});
          return result.results.map((item) => ({type: 'discovery',
            id: item.resultRef, label: item.name,
            detail: item.description ?? item.entityClass,
            reference: item.canonicalRef}));
        }})];
      if(runtime.aiInterface?.toolCatalog?.readTools &&
          runtime.options.aiEgress) {
        const bridge = createDiscoveryAIToolBridge({
          readTools: runtime.aiInterface.toolCatalog.readTools,
          discoveryAPI: runtime.api, analysis: runtime.analysis,
          egress: runtime.options.aiEgress});
        removers.push(() => bridge.dispose());
      }
      runtime.moduleDisposers = removers;
      return runtime;
    },
    deactivate: async ({runtime}) => {
      runtime.moduleDisposers?.splice(0).reverse().forEach((remove) => remove());
    }};
}

export function registerDiscoveryIntelligenceModule({modules, services, api,
  options={}}={}) {
  if(!modules || !services) throw new TypeError(
    'Discovery registration requires module and service registries.'
  );
  const removers = [];
  if(!services.has(PROJECT_ASSET_SERVICE_ID)) removers.push(services.register({
    id: PROJECT_ASSET_SERVICE_ID, version: '1.0.0',
    factory: () => new ProjectAssetClient(api)}));
  if(!services.has(DISCOVERY_INTELLIGENCE_RUNTIME_SERVICE_ID)) {
    removers.push(services.register({
      id: DISCOVERY_INTELLIGENCE_RUNTIME_SERVICE_ID, version: '1.0.0',
      dependencies: [PROJECT_ASSET_SERVICE_ID,
        PLATFORM_SERVICE_IDS.RESOURCE_IDENTITIES, PLATFORM_SERVICE_IDS.TASKS,
        PLATFORM_SERVICE_IDS.RELATIONSHIPS, PLATFORM_SERVICE_IDS.SEARCH],
      factory: ({services: resolved, registry}) =>
        new DiscoveryIntelligenceRuntimeService({
          projectAssets: resolved[PROJECT_ASSET_SERVICE_ID],
          identities: resolved[PLATFORM_SERVICE_IDS.RESOURCE_IDENTITIES],
          tasks: resolved[PLATFORM_SERVICE_IDS.TASKS],
          relationships: resolved[PLATFORM_SERVICE_IDS.RELATIONSHIPS],
          federatedSearch: resolved[PLATFORM_SERVICE_IDS.SEARCH],
          options: {...options, aiInterface: options.aiInterface ??
            registry.active(options.aiInterfaceServiceId ??
              'cdeadmin.ai_interface.runtime')}})}));
  }
  const removeModule = modules.register(
    discoveryIntelligenceModuleDefinition(services));
  return () => { removeModule(); removers.reverse().forEach((remove) => remove()); };
}
