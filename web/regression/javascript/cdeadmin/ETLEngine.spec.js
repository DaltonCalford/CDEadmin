/////////////////////////////////////////////////////////////
// ETL graph, schema, loss and delivery semantic gates.
/////////////////////////////////////////////////////////////

import {
  normalizePushdownPlan, normalizeStageResult, propagateSchemas,
  topologicalOrder, validateEdgeConnection, validatePipeline, weakestGuarantee,
} from 'sources/cdeadmin_ui/modules/etl';

const port = (id, direction, mode='batch', updates={}) => ({id, name: id, direction, mode,
  schemaState: 'known', fields: [{id: 'id', name: 'ID', semanticType: 'integer',
    nullable: false, nativeDetails: {}}], ...updates});
const node = (id, kind, ports) => ({id, name: id, kind, config: {}, resourceRef: null, ports,
  capabilityRequirements: [], executionPreference: 'external_runtime',
  checkpointEnabled: false, nativeDetails: {}});
const content = (updates={}) => ({name: 'ETL', description: '', mode: 'batch', parameters: [],
  nodes: [node('a', 'source', [port('out', 'output')]),
    node('b', 'map', [port('in', 'input'), port('out', 'output')]),
    node('c', 'sink', [port('in', 'input')])], edges: [
    {id: 'ab', fromNodeId: 'a', fromPort: 'out', toNodeId: 'b', toPort: 'in',
      mappingPolicy: 'explicit', mappings: [], deliveryGuarantee: 'exactly_once',
      partitioning: {}, ordering: {}, nativeDetails: {}},
    {id: 'bc', fromNodeId: 'b', fromPort: 'out', toNodeId: 'c', toPort: 'in',
      mappingPolicy: 'explicit', mappings: [], deliveryGuarantee: 'at_least_once',
      partitioning: {}, ordering: {}, nativeDetails: {}}], deployments: [], schedules: [],
  tests: [], visualLayout: {}, extensions: {}, ...updates});

describe('ETL engine', () => {
  it('orders the graph deterministically and detects cycles', () => {
    expect(topologicalOrder(content())).toMatchObject({order: ['a', 'b', 'c'], cyclic: false});
    const cyclic = content({edges: [...content().edges, {id: 'ca', fromNodeId: 'c',
      fromPort: 'out', toNodeId: 'a', toPort: 'in', mappingPolicy: 'explicit', mappings: [],
      deliveryGuarantee: 'unknown', partitioning: {}, ordering: {}, nativeDetails: {}}],
    nodes: [node('a', 'source', [port('in', 'input'), port('out', 'output')]),
      node('b', 'map', [port('in', 'input'), port('out', 'output')]),
      node('c', 'sink', [port('in', 'input'), port('out', 'output')])]});
    expect(topologicalOrder(cyclic)).toMatchObject({cyclic: true,
      blockedNodeIds: ['a', 'b', 'c']});
  });

  it('rejects batch-to-stream connections before mutation', () => {
    const mixed = content({nodes: [node('a', 'source', [port('out', 'output', 'batch')]),
      node('b', 'map', [port('in', 'input', 'stream'), port('out', 'output')]),
      node('c', 'sink', [port('in', 'input')])]});
    expect(validateEdgeConnection(mixed, mixed.edges[0])).toMatchObject({valid: false,
      details: [expect.stringContaining('incompatible')]});
  });

  it('marks unacknowledged lossy mapping and prevents deployment', () => {
    const mapping = {id: 'loss', source: 'id', target: 'id', conversion: 'narrow',
      nullPolicy: 'preserve', nullable: true, lossy: true, lossAcknowledged: false,
      nativeDetails: {}};
    const value = content({edges: [{...content().edges[0], mappings: [mapping]},
      content().edges[1]]});
    expect(validatePipeline(value)).toMatchObject({valid: true, deployable: false,
      warnings: [expect.stringContaining('explicit acknowledgment')]});
    expect(propagateSchemas(value)).toMatchObject({compatible: false,
      records: [expect.objectContaining({problems: [expect.stringContaining('loss_unacknowledged')]}) ,
        expect.any(Object)]});
  });

  it('preserves explicit provider pushdown location and reason', () => {
    expect(normalizePushdownPlan({location: 'source_pushdown', reason: 'Native predicate',
      providerPlan: {operator: 'filter'}, warnings: []}, 'b')).toMatchObject({
      nodeId: 'b', location: 'source_pushdown', reason: 'Native predicate'});
    expect(() => normalizePushdownPlan({location: 'invented', reason: 'x'}, 'b'))
      .toThrow('invalid');
  });

  it('reports only the weakest relevant stage delivery guarantee', () => {
    expect(weakestGuarantee(['exactly_once', 'at_least_once', 'best_effort']))
      .toBe('best_effort');
    expect(weakestGuarantee([])).toBe('unknown');
  });

  it('refuses provider sink writes during preview and validates counters', () => {
    const sink = content().nodes[2];
    expect(() => normalizeStageResult({state: 'succeeded', rows: 1, bytes: 4,
      written: true}, sink, {preview: true})).toThrow('must not write');
    expect(() => normalizeStageResult({state: 'succeeded', rows: -1}, sink)).toThrow('counters');
    expect(normalizeStageResult({state: 'succeeded', rows: 1, bytes: 4,
      written: false, deliveryGuarantee: 'exactly_once'}, sink)).toMatchObject({
      deliveryGuarantee: 'exactly_once'});
  });
});
