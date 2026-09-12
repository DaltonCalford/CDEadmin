/////////////////////////////////////////////////////////////
// First-party Data Contract Manager registration.
/////////////////////////////////////////////////////////////

import React from 'react';
import PropTypes from 'prop-types';
import {Box} from '@mui/material';
import {ProjectAssetClient} from '../../projects/ProjectAssetClient';
import {PLATFORM_SERVICE_IDS} from '../../platform/PlatformServices';
import {PROJECT_ASSET_SERVICE_ID} from '../../integrations/ddn/module';
import {
  CONTRACT_ASSET_TYPE, CONTRACT_MODULE_ID, CONTRACT_STATUSES,
  createContractContent, validateResourceBinding,
} from './contracts';
import {CONTRACT_SERVICE_ID, ContractService} from './ContractService';
import {
  CONTRACT_SURFACES, ContractNavigator, ContractWorkspace, contractInspector,
} from './ContractWorkspace';

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
function serviceFrom(args, context) {
  const service = context.contract ?? context.services?.[CONTRACT_SERVICE_ID] ?? context.service;
  if(!service) throw new Error('Data Contract runtime service is unavailable.');
  const id = args.sessionId ?? context.sessionId;
  if(!id) throw new Error('Data Contract session ID is required.');
  return {service, id};
}
function command(input) {
  return {iconKey: 'tool.contract', surfaces: ['tools', 'toolbar'], macroCallable: false,
    aiEligible: false, auditCategory: 'data_contract', validateArguments: () => true,
    enabledWhen: () => true, visibleWhen: () => true,
    disabledReason: 'Data Contract action is unavailable.', allowedSecurityGroups: [],
    deniedSecurityGroups: [], createsTask: '', confirmationIntent: 'none', ...input};
}

function commands() {
  return [
    command({id: 'contract.create', label: 'Create or Update Data Contract',
      description: 'Create or replace a complete canonical Data Contract definition.',
      permission: ['contract.edit'], validateArguments: (args) => {
        const known = sessionArguments(args, ['content']); if(known !== true) return known;
        try { createContractContent(args.content ?? {}); return true; } catch(error) { return error.message; }
      }, execute: (args, context) => { const {service, id} = serviceFrom(args, context);
        return service.replaceDefinition(id, args.content ?? {}, 'contract.create', context); }}),
    command({id: 'contract.import.odcs', label: 'Import ODCS Contract',
      description: 'Import a version-declared ODCS document without automatically activating it.',
      permission: ['contract.import_export'], createsTask: 'contract.import',
      validateArguments: (args) => {
        const known = sessionArguments(args, ['source']); if(known !== true) return known;
        if(typeof args.source !== 'string' && (!args.source || Array.isArray(args.source) ||
          typeof args.source !== 'object')) return 'ODCS source must be JSON text or an object.';
        return true;
      }, execute: (args, context) => { const {service, id} = serviceFrom(args, context);
        return service.importODCS(id, args.source, context); }}),
    command({id: 'contract.validate', label: 'Validate Data Contract',
      description: 'Validate authored structure separately from live compliance.',
      permission: ['contract.view'], validateArguments: (args) => sessionArguments(args),
      execute: (args, context) => { const {service, id} = serviceFrom(args, context);
        return service.validate(id); }}),
    command({id: 'contract.bind_resource', label: 'Bind Contract Resource',
      description: 'Bind a logical element explicitly to one environment-specific resource.',
      permission: ['contract.edit'], validateArguments: (args) => {
        const known = sessionArguments(args, ['binding']); if(known !== true) return known;
        try { validateResourceBinding(args.binding); return true; } catch(error) { return error.message; }
      }, execute: (args, context) => { const {service, id} = serviceFrom(args, context);
        return service.bindResource(id, args.binding, context); }}),
    command({id: 'contract.sync_metadata', label: 'Compare Live Contract Metadata',
      description: 'Read provider metadata and produce reviewable drift without overwriting the contract.',
      permission: ['contract.compliance'], validateArguments: (args) => {
        const known = sessionArguments(args, ['bindingId']);
        return known === true ? required(args.bindingId, 'Binding ID') : known;
      }, execute: (args, context) => { const {service, id} = serviceFrom(args, context);
        return service.syncMetadata(id, {bindingId: args.bindingId}, context); }}),
    command({id: 'contract.compliance.run', label: 'Run Contract Compliance',
      description: 'Run provider-evidenced schema, quality, SLA and classification compliance.',
      permission: ['contract.compliance'], createsTask: 'contract.compliance.validation',
      validateArguments: (args) => {
        const known = sessionArguments(args, ['qualityResults']); if(known !== true) return known;
        return args.qualityResults === undefined || Array.isArray(args.qualityResults) ? true :
          'Quality results must be an array.';
      }, execute: (args, context) => { const {service, id} = serviceFrom(args, context);
        return service.compliance(id, {qualityResults: args.qualityResults ?? []}, context); }}),
    command({id: 'contract.version.create', label: 'Create Contract Version',
      description: 'Create an immutable named version of the current canonical contract.',
      permission: ['contract.edit'], validateArguments: (args) => {
        const known = sessionArguments(args, ['version']);
        return known === true ? required(args.version, 'Contract version') : known;
      }, execute: (args, context) => { const {service, id} = serviceFrom(args, context);
        return service.createVersion(id, args.version, context); }}),
    command({id: 'contract.status.set', label: 'Set Contract Lifecycle Status',
      description: 'Apply an audited lifecycle transition; activation requires valid structure.',
      permission: ['contract.activate'], confirmationIntent: 'standard_consequential',
      validateArguments: (args) => {
        const known = sessionArguments(args, ['status', 'reason']); if(known !== true) return known;
        if(!CONTRACT_STATUSES.includes(args.status)) return 'Contract lifecycle status is invalid.';
        return args.reason === undefined || typeof args.reason === 'string' ? true :
          'Lifecycle audit reason must be text.';
      }, execute: (args, context) => { const {service, id} = serviceFrom(args, context);
        return service.setStatus(id, args.status, {reason: args.reason,
          currentUser: context.currentUser}); }}),
    command({id: 'contract.export.odcs', label: 'Export ODCS Contract',
      description: 'Export a metadata-only ODCS document with an explicit apiVersion.',
      permission: ['contract.import_export'], validateArguments: (args) => {
        const known = sessionArguments(args, ['apiVersion']); if(known !== true) return known;
        return args.apiVersion === undefined || required(args.apiVersion, 'ODCS apiVersion');
      }, execute: (args, context) => { const {service, id} = serviceFrom(args, context);
        return service.exportODCS(id, {apiVersion: args.apiVersion}, context); }}),
  ];
}

function surfaceRuntime(registry, descriptor, context) {
  return registry.resolve(CONTRACT_SERVICE_ID).then(async (service) => {
    let sessionId = descriptor.restoreRef || descriptor.context?.sessionId;
    if(descriptor.projectId && descriptor.restoreRef) {
      const client = context.services?.[PROJECT_ASSET_SERVICE_ID] ??
        await registry.resolve(PROJECT_ASSET_SERVICE_ID);
      const asset = await client.asset(descriptor.projectId, descriptor.restoreRef);
      if(asset.asset_type !== CONTRACT_ASSET_TYPE) throw new Error(
        'The requested project asset is not a Data Contract.'
      );
      sessionId = `contract:${descriptor.projectId}:${descriptor.restoreRef}`;
      try { service.get(sessionId); } catch { service.create({id: sessionId, content: asset.content}); }
    }
    if(!sessionId) sessionId = service.create({}).id;
    try { service.get(sessionId); } catch { service.create({id: sessionId}); }
    const surface = descriptor.toolKind.replace('contract.', '');
    const executeCommand = (id, args, commandContext={}) => context.commands.execute(
      id, {...args, sessionId}, {...context, ...commandContext, service, contract: service, sessionId}
    );
    return React.createElement(ContractWorkspace, {service, sessionId, surface, executeCommand,
      resources: context.contractResources ?? [], currentUser: context.currentUser ?? {},
      onExport: context.onExport, onStateChange: (session) => context.setSurfaceState?.({
        contractSession: session, contractInspector: contractInspector(session), dirty: session.dirty,
        persistence: session.dirty ? 'dirty' : 'clean', problems: session.problems,
        activeTaskId: session.activeTaskId})});
  });
}

export function contractModuleDefinition(registry) {
  return {id: CONTRACT_MODULE_ID, version: '1.0.0', title: 'Data Contract Manager',
    iconKey: 'tool.contract', serviceRequirements: [CONTRACT_SERVICE_ID], contributions: {
      surfaces: CONTRACT_SURFACES.map((surface) => ({id: `contract.${surface.id}`,
        title: surface.title, iconKey: 'tool.contract', assetTypes: [CONTRACT_ASSET_TYPE],
        readOnly: false, editable: true, detachable: true, duplicable: true, fullscreen: true,
        restore: (descriptor, context) => surfaceRuntime(registry, descriptor, context),
        checkpoint: async (_descriptor, context) => context.contractSession?.content ?? null,
        canClose: async (_descriptor, context) => context.dirty ? {allowed: false,
          reason: 'Save or discard Data Contract changes first.'} : {allowed: true, reason: ''}})),
      commands: commands(),
      activity: [{id: 'activity.contract', label: 'Data Contracts', iconKey: 'tool.contract',
        priority: 34, render: () => <ContractActivity registry={registry} />}],
      inspector: [{id: 'inspector.contract', label: 'Data Contract', priority: 34,
        when: (context) => context.surfaceId?.startsWith('contract.'),
        render: (context) => context.contractInspector ?? {}}],
      bottom: [
        {id: 'bottom.contract.validation', label: 'Validation', priority: 38,
          when: (context) => context.surfaceId?.startsWith('contract.'),
          render: (context) => context.contractSession?.validation ?? {}},
        {id: 'bottom.contract.compliance', label: 'Compliance', priority: 39,
          when: (context) => context.surfaceId?.startsWith('contract.'),
          render: (context) => context.contractSession?.complianceRuns?.[0] ?? {}},
        {id: 'bottom.contract.problems', label: 'Problems', priority: 40,
          when: (context) => context.surfaceId?.startsWith('contract.'),
          render: (context) => context.contractSession?.problems ?? []},
        {id: 'bottom.contract.git-diff', label: 'Git Diff', priority: 41,
          when: (context) => context.surfaceId?.startsWith('contract.'),
          render: (context) => context.contractSession?.versionDiff ?? {}}],
      status: [{id: 'status.contract', label: 'Data Contract', priority: 34,
        when: (context) => context.surfaceId?.startsWith('contract.'),
        value: (context) => context.contractSession?.state ?? 'ready'}]}};
}

function ContractActivity({registry}) {
  const [service, setService] = React.useState(null);
  React.useEffect(() => { registry.resolve(CONTRACT_SERVICE_ID).then(setService); }, [registry]);
  if(!service) return <Box sx={{p: 1}}>Loading Data Contracts…</Box>;
  return <ContractNavigator service={service} onOpen={(sessionId, surface) =>
    window.dispatchEvent(new CustomEvent('cdeadmin:contract-open', {detail: {sessionId, surface}}))} />;
}
ContractActivity.propTypes = {registry: PropTypes.object};

export function registerContractModule({modules, services, api}={}) {
  if(!modules || !services) throw new TypeError(
    'Data Contract registration requires module and service registries.'
  );
  const removers = [];
  if(!services.has(PROJECT_ASSET_SERVICE_ID)) removers.push(services.register({
    id: PROJECT_ASSET_SERVICE_ID, version: '1.0.0', factory: () => new ProjectAssetClient(api)}));
  if(!services.has(CONTRACT_SERVICE_ID)) removers.push(services.register({
    id: CONTRACT_SERVICE_ID, version: '1.0.0', dependencies: [PLATFORM_SERVICE_IDS.TASKS,
      PLATFORM_SERVICE_IDS.RELATIONSHIPS, PLATFORM_SERVICE_IDS.SEARCH,
      PROJECT_ASSET_SERVICE_ID], factory: ({services: resolved}) => new ContractService({
      tasks: resolved[PLATFORM_SERVICE_IDS.TASKS],
      relationships: resolved[PLATFORM_SERVICE_IDS.RELATIONSHIPS],
      search: resolved[PLATFORM_SERVICE_IDS.SEARCH],
      projectAssets: resolved[PROJECT_ASSET_SERVICE_ID], commands: modules.commands})}));
  const removeModule = modules.register(contractModuleDefinition(services));
  return () => { removeModule(); removers.reverse().forEach((remove) => remove()); };
}
