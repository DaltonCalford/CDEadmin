/////////////////////////////////////////////////////////////
// Data Lineage reconciliation, traversal and temporal graph gates.
/////////////////////////////////////////////////////////////

import {
  compareLineageSnapshots, createSnapshot, impactAnalysis, reconcileLineage,
  stableDigest, traverseLineage,
} from 'sources/cdeadmin_ui/modules/lineage';

const ref = (id) => ({schema: 'cdeadmin.external-ref.v1', id});
const node = (id, updates={}) => ({id, kind: 'dataset', ref: ref(id), namespace: 'demo',
  name: id, nativeDetails: {}, ...updates});
const evidence = (id, origin='inferred', confidence=0.6, updates={}) => ({id, origin,
  confidence, capturedAt: '2026-09-11T12:00:00Z', details: {}, ...updates});
const edge = (id, from, to, type='derives', evidences=[evidence(`ev-${id}`)], updates={}) => ({
  id, from, to, type, origin: evidences[0].origin, evidence: evidences,
  fieldLineage: [], nativeDetails: {}, ...updates});

describe('LineageEngine', () => {
  it('reconciles duplicate relationships while preserving every evidence record', () => {
    const graph = reconcileLineage({nodes: [node('a'), node('b')], candidateEdges: [
      edge('one', 'a', 'b', 'derives', [evidence('parsed', 'parsed_query', 0.7)]),
      edge('two', 'a', 'b', 'derives', [evidence('plan', 'execution_plan', 0.95)]),
    ]});
    expect(graph.edges).toHaveLength(1);
    expect(graph.edges[0].evidence.map((item) => item.id)).toEqual(['parsed', 'plan']);
    expect(graph.edges[0].presentationState).toBe('observed');
  });

  it('uses configured source priority without deleting lower-priority evidence', () => {
    const graph = reconcileLineage({nodes: [node('a'), node('b')], candidateEdges: [
      edge('observed', 'a', 'b', 'derives', [evidence('runtime', 'observed_trace', 1)]),
      edge('declared', 'a', 'b', 'derives', [evidence('catalog', 'provider_declared', 0.7)]),
    ], sourcePriority: ['provider_declared', 'observed_trace']});
    expect(graph.edges[0].origin).toBe('provider_declared');
    expect(graph.edges[0].evidence.map((item) => item.id)).toEqual(['catalog', 'runtime']);
  });

  it.each([
    [[evidence('a'), evidence('b')], 'inferred'],
    [[evidence('a', 'provider_declared', 0.9)], 'declared'],
    [[evidence('a', 'provider_declared', 0.9),
      evidence('b', 'project_declared', 0.9)], 'confirmed'],
    [[evidence('a', 'inferred', 0.5, {stale: true})], 'stale'],
  ])('classifies evidence presentation without changing its origin', (items, state) => {
    const graph = reconcileLineage({nodes: [node('a'), node('b')],
      candidateEdges: [edge('one', 'a', 'b', 'derives', items)]});
    expect(graph.edges[0].presentationState).toBe(state);
    expect(graph.edges[0].evidence.map((item) => item.origin))
      .toEqual(items.map((item) => item.origin));
  });

  it('marks explicit native conflict and applies inference-only suppression', () => {
    const graph = reconcileLineage({nodes: [node('a'), node('b')],
      candidateEdges: [edge('one', 'a', 'b', 'derives', undefined,
        {nativeDetails: {conflict: true}})],
      suppressedInferenceRules: ['a|b|derives']});
    expect(graph.edges[0]).toMatchObject({presentationState: 'conflicted', suppressed: true});
  });

  it('rejects edges to unknown nodes and preserves stable graph revisions', () => {
    expect(() => reconcileLineage({nodes: [node('a')],
      candidateEdges: [edge('one', 'a', 'missing')]})).toThrow('unknown node');
    const input = {nodes: [node('a'), node('b')],
      candidateEdges: [edge('one', 'a', 'b')]};
    expect(reconcileLineage(input).revision).toBe(reconcileLineage(input).revision);
    expect(stableDigest(input)).toBe(stableDigest(input));
  });

  it('traverses upstream/downstream to an explicit bounded depth', () => {
    const graph = reconcileLineage({nodes: ['a', 'b', 'c', 'd'].map(node), candidateEdges: [
      edge('ab', 'a', 'b'), edge('bc', 'b', 'c'), edge('cd', 'c', 'd'),
    ]});
    expect(traverseLineage(graph, 'a', {direction: 'out', depth: 3}).nodes)
      .toHaveLength(4);
    expect(traverseLineage(graph, 'd', {direction: 'in', depth: 2}).nodes
      .map((item) => item.id)).toEqual(['d', 'c', 'b']);
    expect(traverseLineage(graph, 'b', {direction: 'both', depth: 1}).nodes)
      .toHaveLength(3);
    expect(() => traverseLineage(graph, 'a', {depth: 11})).toThrow('between 1 and 10');
  });

  it('switches over-budget interactive traversal to a bounded clustered result', () => {
    const nodes = Array.from({length: 502}, (_, index) => node(`n${index}`));
    const edges = nodes.slice(1).map((item, index) => edge(`e${index}`, 'n0', item.id));
    const graph = reconcileLineage({nodes, candidateEdges: edges});
    const result = traverseLineage(graph, 'n0', {depth: 1});
    expect(result).toMatchObject({overBudget: true, clustered: true});
    expect(result.nodes).toHaveLength(500); expect(result.omitted.nodes).toBe(2);
    expect(result.levels.flat()).toHaveLength(500);
  });

  it('computes background impact beyond UI depth and summarizes risk', () => {
    const graph = reconcileLineage({nodes: [node('a'), node('b', {
      nativeDetails: {critical: true}}), node('job', {kind: 'job'})],
    candidateEdges: [edge('ab', 'a', 'b'), edge('bj', 'b', 'job')]});
    const result = impactAnalysis(graph, 'a', {depth: 20});
    expect(result.riskSummary).toEqual({high: 1, medium: 1, low: 0});
    expect(result.affectedRefs).toHaveLength(2);
  });

  it('creates exact snapshots and detects node and edge changes', () => {
    const first = reconcileLineage({nodes: [node('a'), node('b')],
      candidateEdges: [edge('ab', 'a', 'b')]});
    const second = reconcileLineage({nodes: [node('a'), node('b'), node('c')],
      candidateEdges: [edge('ab', 'a', 'b'), edge('bc', 'b', 'c')]});
    const left = createSnapshot(first, {id: 's1', capturedAt: '2026-09-11T12:00:00Z'});
    const right = createSnapshot(second, {id: 's2', capturedAt: '2026-09-11T13:00:00Z'});
    expect(compareLineageSnapshots(left, right)).toMatchObject({
      nodes: {added: ['c'], removed: [], changed: []},
      edges: {added: [second.edges.find((item) => item.to === 'c').id]}});
  });
});
