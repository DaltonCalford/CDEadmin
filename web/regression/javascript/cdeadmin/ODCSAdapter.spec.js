/////////////////////////////////////////////////////////////
// Version-aware and loss-aware ODCS interoperability gates.
/////////////////////////////////////////////////////////////

import {exportODCS, importODCS} from 'sources/cdeadmin_ui/modules/data_contract';

const source = (updates={}) => ({apiVersion: 'v3.1.0', kind: 'DataContract',
  name: 'Customer events', version: '2.3.0', status: 'draft', domain: 'customer',
  description: 'Customer event documents', schema: [{id: 'event', name: 'Event',
    type: 'document', properties: [{id: 'payload', name: 'Payload', type: 'map'}]}],
  customProperties: {'company.owner': 'data-platform'},
  'x-preserved': {nativeObject: 'event_stream'}, ...updates});

describe('ODCS adapter', () => {
  it('retains declared apiVersion, kind, source extensions and logical document semantics', () => {
    const imported = importODCS(source());
    expect(imported.interoperability).toMatchObject({apiVersion: 'v3.1.0',
      importedKind: 'DataContract', preservedUnknownFields: ['x-preserved']});
    expect(imported.content.elements).toEqual(expect.arrayContaining([
      expect.objectContaining({id: 'event', logicalType: 'document'}),
      expect.objectContaining({id: 'payload', parentId: 'event', logicalType: 'map'}),
    ]));
    expect(imported.content.elements.some((item) => item.logicalType === 'table')).toBe(false);
  });

  it('round-trips mapped sections while declaring the export profile', () => {
    const imported = importODCS(source()); const exported = exportODCS(imported.content);
    expect(exported).toMatchObject({apiVersion: 'v3.1.0',
      profile: 'cdeadmin.contract.odcs.v1', document: {apiVersion: 'v3.1.0',
        kind: 'DataContract', name: 'Customer events'}});
    expect(exported.document.schema[0]).toMatchObject({id: 'event', type: 'document',
      properties: [expect.objectContaining({id: 'payload', type: 'map'})]});
    expect(exported.document['x-preserved']).toEqual({nativeObject: 'event_stream'});
  });

  it('preserves unknown compatible top-level fields instead of silently dropping them', () => {
    const imported = importODCS(source({pricing: {currency: 'CAD'},
      vendorExtension: {mode: 'append'}}));
    const exported = exportODCS(imported.content);
    expect(exported.document).toMatchObject({pricing: {currency: 'CAD'},
      vendorExtension: {mode: 'append'}});
  });

  it('preserves safe unknown fields nested inside mapped schema and service-level sections', () => {
    const imported = importODCS(source({schema: [{id: 'event', name: 'Event', type: 'document',
      serialization: 'avro', properties: [{id: 'payload', name: 'Payload', type: 'map',
        mapKeyType: 'string'}]}], sla: [{id: 'freshness', measure: 'freshness', target: 5,
      operator: '<=', window: {rolling: '15m'}, measurementSource: 'change-stream'}]}));
    const document = exportODCS(imported.content).document;
    expect(document.schema[0]).toMatchObject({serialization: 'avro',
      properties: [expect.objectContaining({mapKeyType: 'string'})]});
    expect(document.sla[0]).toMatchObject({measurementSource: 'change-stream',
      comparison: '<='});
  });

  it('requires an explicit version for new internal-only exports', () => {
    expect(() => exportODCS({name: 'New', domain: 'd', description: 'd'}))
      .toThrow('apiVersion');
  });

  it('rejects malformed and secret-bearing external documents', () => {
    expect(() => importODCS({kind: 'DataContract'})).toThrow('apiVersion');
    expect(() => importODCS(source({customProperties: {password: 'bad'}})))
      .toThrow('Raw credential');
    const cyclic = source(); cyclic.self = cyclic;
    expect(() => importODCS(cyclic)).toThrow('Invalid ODCS source');
  });
});
