/////////////////////////////////////////////////////////////
// First-party Data Quality module registration.
/////////////////////////////////////////////////////////////

import React from 'react';
import PropTypes from 'prop-types';
import {Box} from '@mui/material';
import {ProjectAssetClient} from '../../projects/ProjectAssetClient';
import {PLATFORM_SERVICE_IDS} from '../../platform/PlatformServices';
import {PROJECT_ASSET_SERVICE_ID} from '../../integrations/ddn/module';
import {
  createQualityContent, QUALITY_ASSET_TYPE, QUALITY_MODULE_ID,
  validateQualityRef, validateQualityRule,
} from './contracts';
import {QUALITY_SERVICE_ID, QualityService} from './QualityService';
import {
  QUALITY_SURFACES, QualityNavigator, QualityWorkspace, qualityInspector,
} from './QualityWorkspace';

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
  const maximum = args.retry?.maximum;
  return args.retry && Object.keys(args.retry).every((key) => key === 'maximum') &&
    Number.isInteger(maximum) && maximum >= 0 && maximum <= 5 ? true :
    'Retry maximum must be an integer from zero through five.';
}
function baselineArguments(args) {
  const known = retryArguments(args, ['baselineId', 'period', 'tolerances']);
  if(known !== true) return known;
  if(args.baselineId !== undefined && required(args.baselineId, 'Baseline ID') !== true) {
    return 'Baseline ID is required when supplied.';
  }
  for(const name of ['period', 'tolerances']) {
    if(args[name] !== undefined && (!args[name] || Array.isArray(args[name]) ||
        typeof args[name] !== 'object')) return `${name} must be an object.`;
  }
  if(args.tolerances && Object.values(args.tolerances).some((value) =>
    typeof value !== 'number' || !Number.isFinite(value) || value < 0)) {
    return 'Every drift tolerance must be a finite non-negative number.';
  }
  return true;
}
function sessionFrom(args, context) {
  const service = context.quality ?? context.services?.[QUALITY_SERVICE_ID] ?? context.service;
  if(!service) throw new Error('Data Quality runtime service is unavailable.');
  const id = args.sessionId ?? context.sessionId;
  if(!id) throw new Error('Data Quality session ID is required.');
  return {service, id};
}
function command(input) {
  return {iconKey: 'tool.quality', surfaces: ['tools', 'toolbar'], macroCallable: false,
    aiEligible: false, auditCategory: 'data_quality', validateArguments: () => true,
    enabledWhen: () => true, visibleWhen: () => true,
    disabledReason: 'Data Quality action is unavailable.', allowedSecurityGroups: [],
    deniedSecurityGroups: [], createsTask: '', confirmationIntent: 'none', ...input};
}

function moduleCommands() {
  return [
    command({id: 'quality.ruleset.create', label: 'Create Quality Rule Set',
      description: 'Create or initialize a complete typed quality rule-set definition.',
      permission: ['quality.edit'], validateArguments: (args) => {
        const known = sessionArguments(args, ['content']); if(known !== true) return known;
        try { createQualityContent(args.content ?? {}); return true; } catch(error) { return error.message; }
      }, execute: (args, context) => {
        const {service, id} = sessionFrom(args, context);
        return service.replaceDefinition(id, args.content ?? {});
      }}),
    command({id: 'quality.rule.add', label: 'Add Quality Rule',
      description: 'Add an explicitly typed rule or accept an evidenced profiler suggestion.',
      permission: ['quality.edit'], validateArguments: (args) => {
        const known = sessionArguments(args, ['rule']); if(known !== true) return known;
        try { validateQualityRule(args.rule); return true; } catch(error) { return error.message; }
      }, execute: (args, context) => {
        const {service, id} = sessionFrom(args, context); return service.addRule(id, args.rule);
      }}),
    command({id: 'quality.rule.test', label: 'Test Quality Rule',
      description: 'Execute one quality rule through the admitted provider adapter.',
      permission: ['quality.execute'], createsTask: 'quality.validation.run',
      validateArguments: (args) => {
        const known = retryArguments(args, ['ruleId']);
        return known === true ? required(args.ruleId, 'Rule ID') : known;
      }, execute: (args, context) => {
        const {service, id} = sessionFrom(args, context);
        return service.run(id, {ruleId: args.ruleId, retry: args.retry}, context);
      }}),
    command({id: 'quality.ruleset.run', label: 'Run Quality Rule Set',
      description: 'Execute every enabled rule through the admitted provider adapter.',
      permission: ['quality.execute'], createsTask: 'quality.validation.run',
      validateArguments: (args) => retryArguments(args), execute: (args, context) => {
        const {service, id} = sessionFrom(args, context);
        return service.run(id, {retry: args.retry}, context);
      }}),
    command({id: 'quality.run.cancel', label: 'Cancel Quality Run',
      description: 'Request cancellation of the active shared Task.',
      permission: ['quality.execute'], validateArguments: (args) => {
        const known = sessionArguments(args, ['taskId']);
        return known === true ? required(args.taskId, 'Task ID') : known;
      }, execute: (args, context) => {
        const {service, id} = sessionFrom(args, context); return service.cancel(id, args.taskId);
      }}),
    command({id: 'quality.baseline.capture', label: 'Capture Quality Baseline',
      description: 'Capture provider-evidenced metrics as a versioned runtime baseline.',
      permission: ['quality.execute'], createsTask: 'quality.baseline.capture',
      validateArguments: baselineArguments,
      execute: (args, context) => {
        const {service, id} = sessionFrom(args, context); return service.captureBaseline(id, args);
      }}),
    command({id: 'quality.profile.run', label: 'Profile Data Slice',
      description: 'Profile a bounded slice and propose evidenced draft rules.',
      permission: ['quality.execute'], createsTask: 'quality.profile.run',
      validateArguments: (args) => {
        const known = retryArguments(args, ['budget']); if(known !== true) return known;
        return Number.isInteger(args.budget) && args.budget >= 1 && args.budget <= 1000000 ? true :
          'Profile budget must be an integer from 1 through 1000000.';
      }, execute: (args, context) => {
        const {service, id} = sessionFrom(args, context); return service.profile(id, args);
      }}),
    command({id: 'quality.result.export', label: 'Export Quality Result',
      description: 'Export metadata results or separately authorized failed-row samples.',
      permission: ['quality.view'], validateArguments: (args) => {
        const known = sessionArguments(args, ['runId', 'includeSamples', 'format']);
        if(known !== true) return known; const run = required(args.runId, 'Run ID');
        if(run !== true) return run;
        if(args.includeSamples !== undefined && typeof args.includeSamples !== 'boolean') {
          return 'includeSamples must be boolean.';
        }
        return args.format === undefined || ['json', 'csv'].includes(args.format) ? true :
          'Export format must be json or csv.';
      }, execute: (args, context) => {
        const {service, id} = sessionFrom(args, context);
        return service.exportResult(id, args, context.currentUser);
      }}),
    command({id: 'quality.contract.sync', label: 'Link Data Contract',
      description: 'Create an explicit governed relationship to a Data Contract asset.',
      permission: ['quality.admin'], validateArguments: (args) => {
        const known = sessionArguments(args, ['contractRef']); if(known !== true) return known;
        try { validateQualityRef(args.contractRef, 'Data Contract reference'); return true; }
        catch(error) { return error.message; }
      }, execute: (args, context) => {
        const {service, id} = sessionFrom(args, context);
        return service.syncContract(id, args.contractRef);
      }}),
  ];
}

function runtimeForSurface(registry, descriptor, context) {
  return registry.resolve(QUALITY_SERVICE_ID).then(async (service) => {
    let sessionId = descriptor.restoreRef || descriptor.context?.sessionId;
    if(descriptor.projectId && descriptor.restoreRef) {
      const client = context.services?.[PROJECT_ASSET_SERVICE_ID] ??
        await registry.resolve(PROJECT_ASSET_SERVICE_ID);
      const asset = await client.asset(descriptor.projectId, descriptor.restoreRef);
      if(asset.asset_type !== QUALITY_ASSET_TYPE) throw new Error(
        'The requested project asset is not a Data Quality asset.'
      );
      sessionId = `quality:${descriptor.projectId}:${descriptor.restoreRef}`;
      try { service.get(sessionId); } catch { service.create({id: sessionId, content: asset.content}); }
    }
    if(!sessionId) sessionId = service.create({}).id;
    try { service.get(sessionId); } catch { service.create({id: sessionId}); }
    const surface = descriptor.toolKind.replace('quality.', '');
    const executeCommand = (id, args, commandContext={}) => context.commands.execute(
      id, {...args, sessionId}, {...context, ...commandContext, service, quality: service, sessionId}
    );
    return React.createElement(QualityWorkspace, {service, sessionId, surface, executeCommand,
      resources: context.qualityResources ?? [], currentUser: context.currentUser ?? {},
      onExport: context.onExport, onStateChange: (session) => context.setSurfaceState?.({
        qualitySession: session, qualityInspector: qualityInspector(session), dirty: session.dirty,
        persistence: session.dirty ? 'dirty' : 'clean', problems: session.problems,
        activeTaskId: session.activeTaskId})});
  });
}

export function qualityModuleDefinition(registry) {
  return {id: QUALITY_MODULE_ID, version: '1.0.0', title: 'Data Quality',
    iconKey: 'tool.quality', serviceRequirements: [QUALITY_SERVICE_ID], contributions: {
      surfaces: QUALITY_SURFACES.map((surface) => ({id: `quality.${surface.id}`,
        title: surface.title, iconKey: 'tool.quality', assetTypes: [QUALITY_ASSET_TYPE],
        readOnly: false, editable: true, detachable: true, duplicable: true, fullscreen: true,
        restore: (descriptor, context) => runtimeForSurface(registry, descriptor, context),
        checkpoint: async (_descriptor, context) => context.qualitySession?.content ?? null,
        canClose: async (_descriptor, context) => context.dirty ? {allowed: false,
          reason: 'Save or discard Data Quality changes first.'} : {allowed: true, reason: ''}})),
      commands: moduleCommands(),
      activity: [{id: 'activity.quality', label: 'Data Quality', iconKey: 'tool.quality',
        priority: 32, render: () => <QualityActivity registry={registry} />}],
      inspector: [{id: 'inspector.quality', label: 'Data Quality', priority: 32,
        when: (context) => context.surfaceId?.startsWith('quality.'),
        render: (context) => context.qualityInspector ?? {}}],
      bottom: [
        {id: 'bottom.quality.validation', label: 'Validation', priority: 34,
          when: (context) => context.surfaceId?.startsWith('quality.'),
          render: (context) => context.qualitySession?.validation ?? {}},
        {id: 'bottom.quality.run-output', label: 'Run Output', priority: 35,
          when: (context) => context.surfaceId?.startsWith('quality.'),
          render: (context) => context.qualitySession?.runs?.[0] ?? {}},
        {id: 'bottom.quality.failed-samples', label: 'Failed Samples', priority: 36,
          when: (context) => context.surfaceId?.startsWith('quality.') &&
            context.currentUser?.permissions?.includes('quality.view_samples'),
          render: (context) => context.qualitySession?.failedSamples ?? []}],
      status: [{id: 'status.quality', label: 'Data Quality', priority: 32,
        when: (context) => context.surfaceId?.startsWith('quality.'),
        value: (context) => context.qualitySession?.state ?? 'ready'}]}};
}

function QualityActivity({registry}) {
  const [service, setService] = React.useState(null);
  React.useEffect(() => { registry.resolve(QUALITY_SERVICE_ID).then(setService); }, [registry]);
  if(!service) return <Box sx={{p: 1}}>Loading Data Quality…</Box>;
  return <QualityNavigator service={service} onOpen={(sessionId, surface) =>
    window.dispatchEvent(new CustomEvent('cdeadmin:quality-open', {detail: {sessionId, surface}}))} />;
}
QualityActivity.propTypes = {registry: PropTypes.object};

export function registerQualityModule({modules, services, api}={}) {
  if(!modules || !services) throw new TypeError(
    'Data Quality registration requires module and service registries.'
  );
  const removers = [];
  if(!services.has(PROJECT_ASSET_SERVICE_ID)) removers.push(services.register({
    id: PROJECT_ASSET_SERVICE_ID, version: '1.0.0', factory: () => new ProjectAssetClient(api)}));
  if(!services.has(QUALITY_SERVICE_ID)) removers.push(services.register({
    id: QUALITY_SERVICE_ID, version: '1.0.0',
    dependencies: [PLATFORM_SERVICE_IDS.TASKS, PLATFORM_SERVICE_IDS.RELATIONSHIPS,
      PLATFORM_SERVICE_IDS.SEARCH, PROJECT_ASSET_SERVICE_ID],
    factory: ({services: resolved}) => new QualityService({
      tasks: resolved[PLATFORM_SERVICE_IDS.TASKS],
      relationships: resolved[PLATFORM_SERVICE_IDS.RELATIONSHIPS],
      search: resolved[PLATFORM_SERVICE_IDS.SEARCH],
      projectAssets: resolved[PROJECT_ASSET_SERVICE_ID], commands: modules.commands})}));
  const removeModule = modules.register(qualityModuleDefinition(services));
  return () => { removeModule(); removers.reverse().forEach((remove) => remove()); };
}
