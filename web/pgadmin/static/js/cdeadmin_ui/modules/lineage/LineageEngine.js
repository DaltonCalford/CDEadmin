/////////////////////////////////////////////////////////////
// Deterministic lineage reconciliation, traversal and snapshots.
/////////////////////////////////////////////////////////////

import {immutable, plainObject, platformValue} from '../../platform/serviceUtils';
import {
  lineageSuppressionKey, validateLineageEdge, validateLineageNode,
} from './contracts';

const HIGH_CONFIDENCE = 0.8;

function stableStringify(value) {
  if(Array.isArray(value)) return `[${value.map(stableStringify).join(',')}]`;
  if(!value || typeof value !== 'object') return JSON.stringify(value);
  return `{${Object.keys(value).sort().map((key) =>
    `${JSON.stringify(key)}:${stableStringify(value[key])}`).join(',')}}`;
}

export function stableDigest(value) {
  const input = stableStringify(value); let first = 2166136261; let second = 5381;
  for(let index = 0; index < input.length; index++) {
    first = Math.imul(first ^ input.charCodeAt(index), 16777619);
    second = Math.imul(second, 33) ^ input.charCodeAt(index);
  }
  return `${(first >>> 0).toString(16).padStart(8, '0')}${
    (second >>> 0).toString(16).padStart(8, '0')}`;
}

function evidenceState(evidence, conflict=false) {
  if(conflict) return 'conflicted';
  if(evidence.every((item) => item.stale || (item.validTo && Date.parse(item.validTo) < Date.now()))) {
    return 'stale';
  }
  const origins = new Set(evidence.map((item) => item.origin));
  if(origins.has('observed_trace') || origins.has('execution_plan')) return 'observed';
  if(origins.has('provider_declared') || origins.has('project_declared') ||
      origins.has('user_curated')) {
    const compatible = evidence.filter((item) => item.confidence >= HIGH_CONFIDENCE).length >= 2;
    return compatible ? 'confirmed' : 'declared';
  }
  if(evidence.some((item) => item.origin === 'inferred' || item.origin === 'parsed_query')) {
    return 'inferred';
  }
  return 'declared';
}

function edgeKey(edge) { return lineageSuppressionKey(edge); }

export function reconcileLineage({nodes=[], candidateEdges=[], curatedEdges=[],
  suppressedInferenceRules=[], sourcePriority=[]}={}) {
  const nodeMap = new Map(nodes.map((item) => {
    const node = validateLineageNode(item); return [node.id, node];
  }));
  const groups = new Map();
  [...candidateEdges, ...curatedEdges].map(validateLineageEdge).forEach((edge) => {
    if(!nodeMap.has(edge.from) || !nodeMap.has(edge.to)) {
      throw new TypeError(`Lineage edge ${edge.id} references an unknown node.`);
    }
    const values = groups.get(edgeKey(edge)) ?? []; values.push(edge); groups.set(edgeKey(edge), values);
  });
  const suppressions = new Set(suppressedInferenceRules);
  const edges = [...groups.entries()].map(([key, values]) => {
    const evidence = values.flatMap((item) => item.evidence)
      .sort((left, right) => left.id.localeCompare(right.id));
    const distinctDirections = new Set(values.map((item) => `${item.from}:${item.to}:${item.type}`));
    const conflict = distinctDirections.size > 1 || values.some((item) =>
      item.nativeDetails?.conflict === true);
    const priority = new Map(sourcePriority.map((origin, index) => [origin, index]));
    const winner = [...values].sort((left, right) => {
      const leftRank = priority.get(left.origin) ?? Number.MAX_SAFE_INTEGER;
      const rightRank = priority.get(right.origin) ?? Number.MAX_SAFE_INTEGER;
      return leftRank - rightRank || right.confidence - left.confidence ||
        left.id.localeCompare(right.id);
    })[0];
    return validateLineageEdge({...winner, id: `edge-${stableDigest(key)}`, evidence,
      fieldLineage: values.flatMap((item) => item.fieldLineage),
      suppressed: suppressions.has(key) && evidence.every((item) =>
        ['inferred', 'parsed_query'].includes(item.origin)),
      presentationState: evidenceState(evidence, conflict)});
  }).sort((left, right) => left.id.localeCompare(right.id));
  return immutable({schema: 'cdeadmin.lineage-graph.v1', revision: stableDigest({
    nodes: [...nodeMap.values()], edges,
  }), nodes: [...nodeMap.values()].sort((a, b) => a.id.localeCompare(b.id)), edges});
}

export function traverseLineage(graph, startId, {direction='out', depth=1,
  edgeTypes=null, includeSuppressed=false, interactive=true}={}) {
  plainObject(graph, 'Lineage graph'); platformValue(startId, 'Traversal start ID');
  const maximum = interactive ? 10 : 100;
  if(!Number.isInteger(depth) || depth < 1 || depth > maximum) throw new TypeError(
    `Traversal depth must be between 1 and ${maximum}.`
  );
  if(!['in', 'out', 'both'].includes(direction)) throw new TypeError('Traversal direction is invalid.');
  const nodeMap = new Map(graph.nodes.map((item) => [item.id, item]));
  if(!nodeMap.has(startId)) throw new TypeError(`Unknown lineage node: ${startId}`);
  const allowed = edgeTypes ? new Set(edgeTypes) : null;
  const usable = graph.edges.filter((edge) => (includeSuppressed || !edge.suppressed) &&
    (!allowed || allowed.has(edge.type)));
  const visited = new Set([startId]); let frontier = [startId];
  const visibleEdges = new Map(); const levels = [[nodeMap.get(startId)]];
  for(let hop = 1; frontier.length && hop <= depth; hop++) {
    const next = [];
    for(const current of frontier) for(const edge of usable) {
      let target = null;
      if((direction === 'out' || direction === 'both') && edge.from === current) target = edge.to;
      if((direction === 'in' || direction === 'both') && edge.to === current) target = edge.from;
      if(!target) continue;
      visibleEdges.set(edge.id, edge);
      if(!visited.has(target)) { visited.add(target); next.push(target); }
    }
    if(next.length) levels.push(next.map((id) => nodeMap.get(id)));
    frontier = next;
  }
  const overBudget = interactive && (visited.size > 500 || visibleEdges.size > 1000);
  const nodes = [...visited].slice(0, interactive ? 500 : undefined).map((id) => nodeMap.get(id));
  const nodeIds = new Set(nodes.map((item) => item.id));
  const edges = [...visibleEdges.values()].filter((edge) =>
    nodeIds.has(edge.from) && nodeIds.has(edge.to)).slice(0, interactive ? 1000 : undefined);
  const boundedLevels = levels.map((level) => level.filter((node) => nodeIds.has(node.id)))
    .filter((level) => level.length);
  return immutable({schema: 'cdeadmin.lineage-traversal.v1', startId, direction, depth,
    levels: boundedLevels, nodes, edges, overBudget, clustered: overBudget,
    omitted: {nodes: Math.max(0, visited.size - nodes.length),
      edges: Math.max(0, visibleEdges.size - edges.length)}});
}

export function impactAnalysis(graph, startId, options={}) {
  const result = traverseLineage(graph, startId, {...options, direction: options.direction ?? 'out',
    interactive: false});
  const risk = result.nodes.slice(1).map((node) => ({nodeId: node.id,
    classification: node.classification ?? 'unclassified',
    risk: node.nativeDetails?.critical ? 'high' : node.kind.includes('job') ? 'medium' : 'low'}));
  return immutable({...result, schema: 'cdeadmin.lineage-impact.v1', affectedRefs:
    result.nodes.slice(1).map((item) => item.reference), riskSummary: {
    high: risk.filter((item) => item.risk === 'high').length,
    medium: risk.filter((item) => item.risk === 'medium').length,
    low: risk.filter((item) => item.risk === 'low').length}, risk});
}

export function createSnapshot(graph, {id, capturedAt, scopeRefs=[]}={}) {
  plainObject(graph, 'Lineage graph');
  const content = {nodes: graph.nodes, edges: graph.edges};
  return immutable({schema: 'cdeadmin.lineage-snapshot.v1',
    id: platformValue(id, 'Snapshot ID'), capturedAt: platformValue(capturedAt, 'Captured at'),
    scopeRefs: [...scopeRefs], graphRevision: stableDigest(content), ...content});
}

export function compareLineageSnapshots(left, right) {
  plainObject(left, 'Left snapshot'); plainObject(right, 'Right snapshot');
  const compare = (kind) => {
    const before = new Map(left[kind].map((item) => [item.id, stableDigest(item)]));
    const after = new Map(right[kind].map((item) => [item.id, stableDigest(item)]));
    return {added: [...after.keys()].filter((id) => !before.has(id)),
      removed: [...before.keys()].filter((id) => !after.has(id)),
      changed: [...after.keys()].filter((id) => before.has(id) && before.get(id) !== after.get(id))};
  };
  return immutable({schema: 'cdeadmin.lineage-snapshot-diff.v1',
    leftId: left.id, rightId: right.id, nodes: compare('nodes'), edges: compare('edges')});
}
