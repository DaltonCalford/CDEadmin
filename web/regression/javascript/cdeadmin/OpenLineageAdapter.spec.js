/////////////////////////////////////////////////////////////
// OpenLineage version/facet-preserving interoperability gates.
/////////////////////////////////////////////////////////////

import {
  exportOpenLineage, exportOpenLineageBundle, importOpenLineage,
  validateOpenLineageEvent,
} from 'sources/cdeadmin_ui/modules/lineage';

function event(updates={}) {
  return {eventType: 'COMPLETE', eventTime: '2026-09-11T12:00:00Z',
    producer: 'https://example.test/producer', schemaURL: 'https://openlineage.io/spec/2-0-2/OpenLineage.json',
    run: {runId: 'run-1', facets: {customRun: {_producer: 'x', value: 4}}},
    job: {namespace: 'demo', name: 'transform', facets: {unknownJob: {kept: true}}},
    inputs: [{namespace: 'demo', name: 'source', facets: {schema: {fields: [
      {name: 'id', type: 'INTEGER'}, {name: 'status', type: 'TEXT'},
    ]}, unknownInput: {nested: ['preserved']}}}],
    outputs: [{namespace: 'demo', name: 'target', facets: {unknownOutput: {kept: true}}}],
    ...updates};
}

describe('OpenLineage adapter', () => {
  it('imports read/write edges, nodes, fields and exact provenance', () => {
    const result = importOpenLineage(event());
    expect(result.nodes).toHaveLength(3); expect(result.edges).toHaveLength(2);
    expect(result.edges.map((item) => item.type)).toEqual(['reads', 'writes']);
    expect(result.edges[0].fieldLineage).toHaveLength(2);
    expect(result.edges[0].evidence[0]).toMatchObject({origin: 'openlineage_import',
      details: {producer: 'https://example.test/producer', runId: 'run-1'}});
  });

  it('round-trips producer, schema URL and every unknown facet without loss', () => {
    const original = event(); const imported = importOpenLineage(original);
    expect(exportOpenLineage(imported.imports[0])).toEqual(original);
    expect(JSON.parse(exportOpenLineageBundle(imported.imports))).toEqual([original]);
  });

  it('accepts multiple versioned events and creates stable import identities', () => {
    const values = [event(), event({eventType: 'START', eventTime: '2026-09-11T11:00:00Z'})];
    const first = importOpenLineage(values); const second = importOpenLineage(values);
    expect(first.imports.map((item) => item.id)).toEqual(second.imports.map((item) => item.id));
    expect(first.imports).toHaveLength(2);
  });

  it('rejects malformed events and credential-bearing facets', () => {
    expect(() => validateOpenLineageEvent({})).toThrow();
    expect(() => importOpenLineage(event({eventTime: 'invalid'}))).toThrow('ISO timestamp');
    expect(() => importOpenLineage(event({run: {runId: 'run', facets: {
      password: {value: 'forbidden'}}}}))).toThrow('Raw credential');
    expect(() => importOpenLineage(event({job: {namespace: 'demo', name: 'job',
      facets: {notJson: undefined}}}))).toThrow('exact JSON');
  });
});
