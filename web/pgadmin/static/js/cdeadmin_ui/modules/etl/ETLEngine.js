/////////////////////////////////////////////////////////////
// ETL graph validation, propagation and execution semantics.
/////////////////////////////////////////////////////////////

import {immutable, plainObject, platformValue} from '../../platform/serviceUtils';
import {
  createETLContent, ETL_DELIVERY_GUARANTEES, ETL_EXECUTION_LOCATIONS,
} from './contracts';

const GUARANTEE_STRENGTH = Object.freeze({
  exactly_once: 4, at_least_once: 3, at_most_once: 2, best_effort: 1, unknown: 0,
});

export function modesCompatible(from, to) {
  return from === 'either' || to === 'either' || from === to;
}

function endpoint(content, edge) {
  const fromNode = content.nodes.find((item) => item.id === edge.fromNodeId);
  const toNode = content.nodes.find((item) => item.id === edge.toNodeId);
  return {fromNode, toNode,
    fromPort: fromNode?.ports.find((item) => item.id === edge.fromPort),
    toPort: toNode?.ports.find((item) => item.id === edge.toPort)};
}

export function validateEdgeConnection(contentInput, edgeInput) {
  const content = createETLContent(contentInput);
  const edge = content.edges.find((item) => item.id === edgeInput.id) ?? edgeInput;
  const {fromNode, toNode, fromPort, toPort} = endpoint(content, edge);
  const details = []; const warnings = [];
  if(!fromNode || !toNode) details.push('Both edge nodes must exist.');
  if(!fromPort || fromPort.direction !== 'output') details.push('Source port must be an output.');
  if(!toPort || toPort.direction !== 'input') details.push('Target port must be an input.');
  if(fromPort && toPort && !modesCompatible(fromPort.mode, toPort.mode)) {
    details.push(`Port modes are incompatible: ${fromPort.mode} → ${toPort.mode}.`);
  }
  if(fromPort?.schemaState === 'incompatible' || toPort?.schemaState === 'incompatible') {
    details.push('An edge endpoint has an incompatible schema.');
  }
  edge.mappings?.forEach((mapping) => {
    if(mapping.lossy && !mapping.lossAcknowledged) warnings.push(
      `Lossy mapping ${mapping.id} requires explicit acknowledgment before deployment.`
    );
  });
  return immutable({valid: !details.length, deployable: !details.length && !warnings.length,
    details, warnings});
}

export function topologicalOrder(contentInput) {
  const content = createETLContent(contentInput);
  const incoming = new Map(content.nodes.map((node) => [node.id, 0]));
  const outgoing = new Map(content.nodes.map((node) => [node.id, []]));
  content.edges.forEach((edge) => {
    incoming.set(edge.toNodeId, incoming.get(edge.toNodeId) + 1);
    outgoing.get(edge.fromNodeId).push(edge.toNodeId);
  });
  const queue = content.nodes.filter((node) => incoming.get(node.id) === 0)
    .map((node) => node.id).sort(); const result = [];
  while(queue.length) {
    const id = queue.shift(); result.push(id);
    outgoing.get(id).sort().forEach((next) => {
      incoming.set(next, incoming.get(next) - 1);
      if(incoming.get(next) === 0) { queue.push(next); queue.sort(); }
    });
  }
  return immutable({order: result, cyclic: result.length !== content.nodes.length,
    blockedNodeIds: content.nodes.map((node) => node.id).filter((id) => !result.includes(id)).sort()});
}

export function validatePipeline(contentInput) {
  const content = createETLContent(contentInput); const details = []; const warnings = [];
  if(!content.name.trim()) details.push('Pipeline name is required.');
  if(!content.nodes.some((node) => node.kind === 'source')) {
    details.push('At least one source node is required.');
  }
  if(!content.nodes.some((node) => node.kind === 'sink')) {
    details.push('At least one sink node is required.');
  }
  content.edges.forEach((edge) => {
    const status = validateEdgeConnection(content, edge);
    details.push(...status.details.map((item) => `${edge.id}: ${item}`));
    warnings.push(...status.warnings);
  });
  const order = topologicalOrder(content);
  if(order.cyclic) details.push(`Pipeline contains a data-flow cycle: ${order.blockedNodeIds.join(', ')}.`);
  content.nodes.forEach((node) => {
    const incoming = content.edges.filter((edge) => edge.toNodeId === node.id);
    const outgoing = content.edges.filter((edge) => edge.fromNodeId === node.id);
    if(node.kind !== 'source' && !incoming.length) warnings.push(`Node ${node.id} has no input edge.`);
    if(node.kind !== 'sink' && !outgoing.length) warnings.push(`Node ${node.id} has no output edge.`);
    if(['source', 'sink', 'custom_provider'].includes(node.kind) && !node.resourceRef &&
        node.executionPreference !== 'external_runtime') {
      warnings.push(`Node ${node.id} has no provider resource binding.`);
    }
  });
  const deployments = new Set(content.deployments.map((item) => item.id));
  content.schedules.forEach((schedule) => {
    if(!deployments.has(schedule.deploymentId)) details.push(
      `Schedule ${schedule.id} references an unknown deployment.`
    );
  });
  return immutable({valid: !details.length, deployable: !details.length && !warnings.some(
    (item) => item.includes('Lossy mapping')), details: [...new Set(details)],
  warnings: [...new Set(warnings)], order: order.order});
}

export function propagateSchemas(contentInput) {
  const content = createETLContent(contentInput); const records = [];
  content.edges.forEach((edge) => {
    const {fromNode, toNode, fromPort, toPort} = endpoint(content, edge);
    const problems = [];
    if(!modesCompatible(fromPort.mode, toPort.mode)) problems.push('mode_incompatible');
    if(['unknown', 'partial'].includes(fromPort.schemaState)) problems.push(
      `source_schema_${fromPort.schemaState}`
    );
    if(toPort.schemaState === 'incompatible') problems.push('target_schema_incompatible');
    const sourceFields = new Map(fromPort.fields.map((field) => [field.id, field]));
    const targetFields = new Map(toPort.fields.map((field) => [field.id, field]));
    edge.mappings.forEach((mapping) => {
      if(!sourceFields.has(mapping.source)) problems.push(`missing_source:${mapping.source}`);
      if(!targetFields.has(mapping.target)) problems.push(`missing_target:${mapping.target}`);
      if(mapping.lossy && !mapping.lossAcknowledged) problems.push(`loss_unacknowledged:${mapping.id}`);
    });
    records.push(immutable({id: edge.id, fromNodeId: fromNode.id, fromPort: fromPort.id,
      toNodeId: toNode.id, toPort: toPort.id,
      sourceSchemaState: fromPort.schemaState, targetSchemaState: toPort.schemaState,
      mappingCount: edge.mappings.length,
      state: problems.length ? 'incompatible' : fromPort.schemaState === 'known' &&
        toPort.schemaState === 'known' ? 'known' : 'partial', problems}));
  });
  return immutable({schema: 'cdeadmin.etl-schema-propagation.v1', records,
    compatible: records.every((item) => item.state !== 'incompatible')});
}

export function normalizePushdownPlan(input, nodeId) {
  plainObject(input, 'ETL pushdown plan');
  const location = platformValue(input.location, 'ETL pushdown location');
  if(!ETL_EXECUTION_LOCATIONS.includes(location)) throw new TypeError(
    'ETL pushdown location is invalid.'
  );
  return immutable({schema: 'cdeadmin.etl-pushdown-plan.v1', nodeId,
    location, reason: platformValue(input.reason, 'ETL pushdown reason', 8192),
    providerPlan: input.providerPlan ?? null, warnings: (input.warnings ?? []).map(String)});
}

export function weakestGuarantee(guarantees) {
  if(!Array.isArray(guarantees) || !guarantees.length) return 'unknown';
  guarantees.forEach((value) => {
    if(!ETL_DELIVERY_GUARANTEES.includes(value)) throw new TypeError(
      `Invalid ETL delivery guarantee: ${value}`
    );
  });
  return [...guarantees].sort((left, right) =>
    GUARANTEE_STRENGTH[left] - GUARANTEE_STRENGTH[right])[0];
}

export function normalizeStageResult(input, node, {preview=false}={}) {
  plainObject(input, 'ETL stage result');
  if(node.kind === 'sink' && preview && input.written === true) throw new TypeError(
    'Preview must not write sink data.'
  );
  const rows = Number(input.rows ?? 0); const bytes = Number(input.bytes ?? 0);
  if(!Number.isInteger(rows) || rows < 0 || !Number.isInteger(bytes) || bytes < 0) {
    throw new TypeError('ETL stage counters must be non-negative integers.');
  }
  const guarantee = input.deliveryGuarantee ?? 'unknown';
  if(!ETL_DELIVERY_GUARANTEES.includes(guarantee)) throw new TypeError(
    'ETL stage delivery guarantee is invalid.'
  );
  return immutable({schema: 'cdeadmin.etl-stage-result.v1', nodeId: node.id,
    state: platformValue(input.state ?? 'succeeded', 'ETL stage state'), rows, bytes,
    throughput: Number.isFinite(Number(input.throughput)) ? Number(input.throughput) : null,
    deliveryGuarantee: guarantee, output: input.output ?? null,
    checkpoint: input.checkpoint ?? null, written: Boolean(input.written),
    partition: input.partition ?? null, diagnostics: input.diagnostics ?? {},
    lineage: Array.isArray(input.lineage) ? input.lineage : []});
}
