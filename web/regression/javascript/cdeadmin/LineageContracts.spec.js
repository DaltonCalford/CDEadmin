/////////////////////////////////////////////////////////////
// Data Lineage canonical contract and secret-safety gates.
/////////////////////////////////////////////////////////////

import {
  createLineageContent, exportLineageAsset, importLineageAsset,
  lineageAssetRequest, lineageReferenceKey, validateEvidence,
  validateFieldLineage, validateLineageEdge, validateLineageNode,
} from 'sources/cdeadmin_ui/modules/lineage';

const ref = (name='orders') => ({schema: 'cdeadmin.resource-ref.v1',
  providerId: 'firebird', canonical: `cde-resource://firebird/local/table/${name}`});
const evidence = (updates={}) => ({id: 'ev-1', origin: 'provider_declared', confidence: 0.9,
  capturedAt: '2026-09-11T12:00:00Z', details: {catalog: 'RDB$DEPENDENCIES'}, ...updates});
const edge = (updates={}) => ({id: 'edge-1', from: 'a', to: 'b', type: 'depends_on',
  origin: 'provider_declared', evidence: [evidence()], fieldLineage: [], ...updates});

describe('Data Lineage contracts', () => {
  it('validates stable resource, asset, job and external references', () => {
    expect(lineageReferenceKey(ref())).toContain('resource:');
    expect(lineageReferenceKey({schema: 'cdeadmin.asset-ref.v1', projectId: 'p',
      assetId: 'a'})).toBe('asset:p/a');
    expect(lineageReferenceKey({schema: 'cdeadmin.job-ref.v1', id: 'job:1'}))
      .toBe('cdeadmin.job-ref.v1:job:1');
    expect(() => lineageReferenceKey({schema: 'unknown', id: 'x'})).toThrow('unsupported');
  });

  it('enforces evidence origins, confidence and temporal validity', () => {
    expect(validateEvidence(evidence())).toMatchObject({origin: 'provider_declared'});
    expect(() => validateEvidence(evidence({origin: 'guess'}))).toThrow('Invalid evidence origin');
    expect(() => validateEvidence(evidence({confidence: 1.1}))).toThrow('between zero and one');
    expect(() => validateEvidence(evidence({capturedAt: 'yesterday'}))).toThrow('ISO timestamp');
    expect(() => validateEvidence(evidence({validFrom: '2026-09-12T00:00:00Z',
      validTo: '2026-09-11T00:00:00Z'}))).toThrow('must not be after');
  });

  it('keeps filter-only field influence distinct from value production', () => {
    expect(validateFieldLineage({id: 'field-1', sourceField: 'orders.status',
      targetField: null, transformation: 'FILTER_ONLY', evidenceIds: ['ev-1']}))
      .toMatchObject({targetField: null, transformation: 'FILTER_ONLY'});
    expect(() => validateFieldLineage({id: 'field-1', sourceField: 'x', targetField: 'y',
      transformation: 'MAGIC'})).toThrow('Invalid field transformation');
  });

  it('requires typed edges with evidence and stable nodes with native detail', () => {
    expect(validateLineageNode({id: 'a', kind: 'table', ref: ref(), namespace: 'main',
      name: 'orders', nativeDetails: {relationType: 0}})).toMatchObject({name: 'orders'});
    expect(validateLineageEdge(edge())).toMatchObject({type: 'depends_on'});
    expect(() => validateLineageEdge(edge({type: 'magic'}))).toThrow('Invalid lineage edge');
    expect(() => validateLineageEdge(edge({evidence: []}))).toThrow('requires evidence');
    expect(() => validateLineageEdge(edge({fieldLineage: [{id: 'field', sourceField: 'a',
      targetField: 'b', transformation: 'DIRECT_IDENTITY', evidenceIds: ['missing']}]})))
      .toThrow('unknown edge evidence');
  });

  it('creates deterministic, sorted, versioned canonical assets', () => {
    const content = createLineageContent({scopeRefs: [ref('z'), ref('a')],
      sourcePolicies: [{id: 'policy', sourcePriority: ['provider_declared'],
        inferenceEnabled: false, retentionDays: 30}], savedFilters: {active: {type: 'writes'}},
      curatedEdges: [edge()], suppressedInferenceRules: ['z', 'a'], snapshotRefs: []});
    expect(content).toMatchObject({schema: 'cdeadmin.lineage.asset.v1',
      moduleId: 'cdeadmin.lineage', suppressedInferenceRules: ['a', 'z']});
    expect(content.scopeRefs[0].canonical).toContain('/a');
    expect(exportLineageAsset(content)).toBe(exportLineageAsset(content));
    expect(importLineageAsset(exportLineageAsset(content))).toEqual(content);
    expect(Object.isFrozen(content.curatedEdges[0])).toBe(true);
  });

  it('rejects duplicate identities and credential-like fields at every depth', () => {
    expect(() => createLineageContent({scopeRefs: [ref(), ref()]})).toThrow('Duplicate');
    expect(() => createLineageContent({sourcePolicies: [{id: 'same', sourcePriority: []},
      {id: 'same', sourcePriority: []}]})).toThrow('Duplicate Lineage source policy');
    expect(() => createLineageContent({savedFilters: {nested: {accessToken: 'bad'}}}))
      .toThrow('Raw credential');
    expect(() => validateEvidence(evidence({details: {password: 'bad'}})))
      .toThrow('Raw credential');
  });

  it('builds optimistic project requests with only stable live bindings', () => {
    const content = createLineageContent({scopeRefs: [ref()]});
    expect(lineageAssetRequest({projectId: 'p', assetId: 'a', name: 'Lineage',
      path: 'lineage/main.json', expectedVersion: 4, content})).toMatchObject({
      asset_type: 'cdeadmin.lineage.v1', expected_version: 4,
      resource_bindings: [ref()], validation_state: 'valid'});
  });
});
