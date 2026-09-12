/////////////////////////////////////////////////////////////
// Schema Comparison canonical asset and interchange contract gates.
/////////////////////////////////////////////////////////////

import {
  SCHEMA_COMPARE_ASSET_TYPE, createSchemaCompareContent, deterministicJson,
  exportSchemaComparison, importSchemaComparison, schemaCompareAssetRequest,
  validateReference, validateSchemaSnapshot,
} from 'sources/cdeadmin_ui/modules/schema_compare';

function resource(overrides={}) {
  return {schema: 'cdeadmin.resource-ref.v1', provider: 'firebird',
    connection: 'local', scope: '/', kind: 'database', nativeIdentity: 'demo.fdb',
    canonical: 'cde-resource://firebird/local/%2F/database/demo.fdb', ...overrides};
}

function asset(overrides={}) {
  return {schemaVersion: 1, projectId: 'project-one', assetId: 'comparison-one',
    assetType: SCHEMA_COMPARE_ASSET_TYPE, assetVersion: 0,
    path: 'comparisons/one.json', displayName: 'Comparison One', ...overrides};
}

function snapshot(overrides={}) {
  return {schema: 'cdeadmin.schema-compare.snapshot.v1', schemaVersion: 1,
    snapshotId: 'snapshot-one', sourceRef: resource(), revision: 'r1',
    capturedAt: '2026-09-11T12:00:00Z', providerId: 'firebird',
    providerVersion: '5.0.4', supportState: 'supported_native', evidence: {},
    warnings: [], nativeDetails: {}, objects: [{id: 'database', kind: 'database',
      qualifiedName: 'demo.fdb', normalized: {}, native: {}, children: ['table'],
      dependencies: []}, {id: 'table', kind: 'table', qualifiedName: 'PUBLIC.ASSETS',
      parentId: 'database', normalized: {columns: 2}, native: {relationType: 0},
      children: [], dependencies: []}], ...overrides};
}

describe('Schema Comparison contracts', () => {
  it('validates and deeply freezes live, asset and snapshot references', () => {
    expect(validateReference(resource())).toMatchObject({provider: 'firebird'});
    expect(validateReference(asset())).toMatchObject({assetVersion: 0});
    expect(validateReference({schema: 'cdeadmin.schema-compare.snapshot-ref.v1',
      snapshotId: 'snap', revision: '1'})).toMatchObject({snapshotId: 'snap'});
    expect(Object.isFrozen(validateReference(resource()))).toBe(true);
    expect(() => validateReference({schema: 'unknown'})).toThrow('must be a ResourceRef');
  });

  it('requires complete, unique and internally consistent schema snapshots', () => {
    expect(validateSchemaSnapshot(snapshot()).objects).toHaveLength(2);
    expect(() => validateSchemaSnapshot(snapshot({objects: [
      {id: 'x', kind: 'table', qualifiedName: 'x', normalized: {}, native: {}},
      {id: 'x', kind: 'view', qualifiedName: 'x', normalized: {}, native: {}},
    ]}))).toThrow('Duplicate');
    expect(() => validateSchemaSnapshot(snapshot({objects: [{id: 'x', kind: 'table',
      qualifiedName: 'x', parentId: 'missing', normalized: {}, native: {}}]})))
      .toThrow('parent is missing');
    expect(() => validateSchemaSnapshot(snapshot({supportState: 'probably'})))
      .toThrow('support state');
  });

  it('creates only the canonical asset fields and preserves accepted mappings', () => {
    const content = createSchemaCompareContent({leftRef: resource(), rightRef: asset(),
      options: {caseSensitive: false}, acceptedMappings: [{mappingId: 'm1',
        leftId: 'left', rightId: 'right', category: 'exact', accepted: true}],
      ignoredDiffs: ['z', 'a']});
    expect(content).toEqual(expect.objectContaining({
      schema: 'cdeadmin.schema-compare.asset.v1', moduleId: 'cdeadmin.schema_compare',
      ignoredDiffs: ['a', 'z'],
    }));
    expect(Object.isFrozen(content.acceptedMappings[0])).toBe(true);
    expect(() => createSchemaCompareContent({options: {password: 'forbidden'}}))
      .toThrow('Raw credential');
  });

  it('builds an optimistic project-asset request with explicit live bindings', () => {
    const content = createSchemaCompareContent({leftRef: resource(), rightRef: resource({
      provider: 'postgresql', canonical:
        'cde-resource://postgresql/local/%2F/database/demo',
    })});
    const request = schemaCompareAssetRequest(asset({assetVersion: 4}), content);
    expect(request).toMatchObject({asset_type: SCHEMA_COMPARE_ASSET_TYPE,
      expected_version: 4, validation_state: 'valid'});
    expect(request.resource_bindings).toHaveLength(2);
  });

  it('round-trips deterministic credential-free interchange and rejects drift', () => {
    const content = createSchemaCompareContent({leftRef: resource(), rightRef: asset()});
    const exported = exportSchemaComparison(content, {provenance: {author: 'tester'},
      nativeScripts: [{providerId: 'firebird', mediaType: 'text/plain',
        text: 'CREATE TABLE T (ID INTEGER);'}]});
    expect(importSchemaComparison(exported)).toEqual(content);
    expect(exported).toBe(exportSchemaComparison(content, {provenance: {author: 'tester'},
      nativeScripts: [{providerId: 'firebird', mediaType: 'text/plain',
        text: 'CREATE TABLE T (ID INTEGER);'}]}));
    expect(() => importSchemaComparison('{bad')).toThrow('external_format_invalid');
    expect(() => importSchemaComparison({...JSON.parse(exported), version: 2}))
      .toThrow('unsupported schema');
    expect(() => deterministicJson({token: 'forbidden'})).toThrow('Raw credential');
  });
});
