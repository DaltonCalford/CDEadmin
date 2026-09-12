/////////////////////////////////////////////////////////////
// Version-preserving OpenLineage import/export boundary.
/////////////////////////////////////////////////////////////

import {immutable, noRawSecrets, plainObject, platformValue} from '../../platform/serviceUtils';
import {stableDigest} from './LineageEngine';

function jsonClone(value) {
  const text = JSON.stringify(value, (_key, child) => {
    if(child === undefined || ['function', 'symbol', 'bigint'].includes(typeof child)) {
      throw new TypeError('OpenLineage content must be exact JSON data.');
    }
    if(typeof child === 'number' && !Number.isFinite(child)) {
      throw new TypeError('OpenLineage numbers must be finite.');
    }
    return child;
  });
  if(text === undefined) throw new TypeError('OpenLineage content must be exact JSON data.');
  return JSON.parse(text);
}

function datasetRef(dataset) {
  return immutable({schema: 'cdeadmin.external-ref.v1',
    id: `openlineage:dataset:${encodeURIComponent(dataset.namespace)}:${
      encodeURIComponent(dataset.name)}`});
}

function jobRef(job) {
  return immutable({schema: 'cdeadmin.job-ref.v1',
    id: `openlineage:job:${encodeURIComponent(job.namespace)}:${encodeURIComponent(job.name)}`});
}

function datasetNode(dataset) {
  platformValue(dataset.namespace, 'OpenLineage dataset namespace');
  platformValue(dataset.name, 'OpenLineage dataset name');
  return immutable({id: `ol-dataset-${stableDigest([dataset.namespace, dataset.name])}`,
    kind: 'dataset', ref: datasetRef(dataset), namespace: dataset.namespace,
    name: dataset.name, nativeDetails: {facets: dataset.facets ?? {}}});
}

function jobNode(job) {
  platformValue(job.namespace, 'OpenLineage job namespace');
  platformValue(job.name, 'OpenLineage job name');
  return immutable({id: `ol-job-${stableDigest([job.namespace, job.name])}`,
    kind: 'job', ref: jobRef(job), namespace: job.namespace, name: job.name,
    nativeDetails: {facets: job.facets ?? {}}});
}

function fieldMappings(dataset, jobId, direction, evidenceId) {
  const fields = dataset.facets?.schema?.fields ?? [];
  return fields.map((field, index) => ({id: `ol-field-${stableDigest([
    dataset.namespace, dataset.name, field.name, direction,
  ])}`, sourceField: direction === 'input' ? `${dataset.namespace}.${dataset.name}.${field.name}` :
    `${jobId}.${field.name}`, targetField: direction === 'input' ? `${jobId}.${field.name}` :
    `${dataset.namespace}.${dataset.name}.${field.name}`,
  transformation: 'UNKNOWN', evidenceIds: [evidenceId], nativeDetails: {
    position: index, type: field.type ?? null, description: field.description ?? null,
  }}));
}

export function validateOpenLineageEvent(input) {
  plainObject(input, 'OpenLineage event'); noRawSecrets(input, 'OpenLineage event');
  platformValue(input.eventType, 'OpenLineage event type');
  platformValue(input.eventTime, 'OpenLineage event time');
  if(Number.isNaN(Date.parse(input.eventTime))) throw new TypeError(
    'OpenLineage eventTime must be an ISO timestamp.'
  );
  plainObject(input.run, 'OpenLineage run');
  platformValue(input.run.runId, 'OpenLineage run ID');
  plainObject(input.job, 'OpenLineage job');
  platformValue(input.job.namespace, 'OpenLineage job namespace');
  platformValue(input.job.name, 'OpenLineage job name');
  for(const group of ['inputs', 'outputs']) {
    if(input[group] !== undefined && !Array.isArray(input[group])) {
      throw new TypeError(`OpenLineage ${group} must be a list.`);
    }
  }
  return immutable(jsonClone(input));
}

export function importOpenLineage(input) {
  const events = (Array.isArray(input) ? input : [input]).map(validateOpenLineageEvent);
  const nodes = new Map(); const edges = [];
  const imports = events.map((event) => {
    const job = jobNode(event.job); nodes.set(job.id, job);
    const evidenceId = `ol-evidence-${stableDigest([
      event.run.runId, event.eventType, event.eventTime,
    ])}`;
    const evidence = {id: evidenceId, origin: 'openlineage_import', confidence: 1,
      capturedAt: event.eventTime, details: {producer: event.producer ?? null,
        schemaURL: event.schemaURL ?? null, runId: event.run.runId,
        eventType: event.eventType}};
    for(const dataset of event.inputs ?? []) {
      const node = datasetNode(dataset); nodes.set(node.id, node);
      edges.push({id: `ol-edge-${stableDigest([node.id, job.id, evidenceId])}`,
        from: node.id, to: job.id, type: 'reads', origin: 'openlineage_import',
        confidence: 1, evidence: [evidence],
        fieldLineage: fieldMappings(dataset, job.id, 'input', evidenceId),
        nativeDetails: {openLineageDataset: dataset}});
    }
    for(const dataset of event.outputs ?? []) {
      const node = datasetNode(dataset); nodes.set(node.id, node);
      edges.push({id: `ol-edge-${stableDigest([job.id, node.id, evidenceId])}`,
        from: job.id, to: node.id, type: 'writes', origin: 'openlineage_import',
        confidence: 1, evidence: [evidence],
        fieldLineage: fieldMappings(dataset, job.id, 'output', evidenceId),
        nativeDetails: {openLineageDataset: dataset}});
    }
    return immutable({id: `ol-import-${stableDigest(event)}`,
      producer: event.producer ?? null, schemaURL: event.schemaURL ?? null,
      event: immutable(jsonClone(event))});
  });
  return immutable({schema: 'cdeadmin.openlineage-import.v1',
    nodes: [...nodes.values()], edges, imports});
}

export function exportOpenLineage(importRecord) {
  plainObject(importRecord, 'OpenLineage import record');
  const event = validateOpenLineageEvent(importRecord.event);
  return immutable(jsonClone(event));
}

export function exportOpenLineageBundle(records) {
  if(!Array.isArray(records)) throw new TypeError('OpenLineage records must be a list.');
  const events = records.map(exportOpenLineage);
  return `${JSON.stringify(events, null, 2)}\n`;
}
