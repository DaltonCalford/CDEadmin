/////////////////////////////////////////////////////////////
// Data Contract canonical model and project-asset gates.
/////////////////////////////////////////////////////////////

import {
  contractAssetRequest, contractReferenceKey, createContractContent,
  exportContractAsset, importContractAsset, validateAuthoritativeDefinition,
  validateContractElement, validateResourceBinding, validateServerBinding,
  validateServiceLevel,
} from 'sources/cdeadmin_ui/modules/data_contract';

const resource = () => ({schema: 'cdeadmin.resource-ref.v1', provider: 'mongodb',
  canonical: 'cde-resource://mongodb/local/database/sales/collection/orders'});
const asset = (id='quality') => ({schema: 'cdeadmin.asset-ref.v1',
  projectId: 'governance', assetId: id});
const external = (id) => ({schema: 'cdeadmin.external-ref.v1', id});
const element = (updates={}) => ({id: 'orders', name: 'Orders', logicalType: 'document',
  description: 'Order business object', constraints: {}, physicalDefinition: {},
  extensions: {}, ...updates});
const binding = (updates={}) => ({id: 'orders-prod', elementId: 'orders', targetRef: resource(),
  environment: 'production', bindingStatus: 'observed', observedRevision: 'catalog:42',
  nativeDetails: {collectionType: 'timeseries'}, ...updates});

function content(updates={}) {
  return {name: 'Orders contract', contractVersion: '1.0.0', status: 'draft',
    domain: 'sales', description: 'Governed order data',
    elements: [element(), element({id: 'customer', parentId: 'orders', name: 'Customer',
      logicalType: 'embedded_document'})], bindings: [binding()],
    qualityObligations: [{id: 'quality-orders', qualityRef: asset(), severity: 'critical',
      threshold: {maximumFailures: 0}, description: 'No critical violations'}],
    sla: [{id: 'freshness', measure: 'freshness', target: 5, comparison: '<=', unit: 'minutes',
      window: {rolling: '15m'}, description: 'Fresh order feed'}],
    team: [{id: 'producer', name: 'Order producers', principalRefs: [external('team:sales')],
      responsibilities: ['publish'], accessExpectations: [], support: {}, extensions: {}}],
    roles: [], servers: [{id: 'orders-api', providerId: 'mongodb', environment: 'production',
      interface: 'mongodb-wire', resourceRef: resource(), nativeDetails: {}}],
    authoritativeDefinitions: [{id: 'catalog', type: 'business_glossary',
      uri: 'https://catalog.invalid/orders', description: 'Order vocabulary'}],
    extensions: {}, ...updates};
}

describe('Data Contract contracts', () => {
  it('preserves logical object types and stable parent identities', () => {
    expect(validateContractElement(element())).toMatchObject({logicalType: 'document'});
    expect(createContractContent(content()).elements).toEqual(expect.arrayContaining([
      expect.objectContaining({id: 'customer', parentId: 'orders',
        logicalType: 'embedded_document'}),
    ]));
  });

  it('validates environment-aware ResourceRef and AssetRef bindings', () => {
    expect(validateResourceBinding(binding())).toMatchObject({environment: 'production',
      targetRef: resource()});
    expect(contractReferenceKey(resource())).toContain('resource:');
    expect(contractReferenceKey(asset('schema'))).toBe('asset:governance/schema');
    expect(() => validateResourceBinding(binding({targetRef: {schema: 'unknown', id: 'x'}})))
      .toThrow('unsupported');
  });

  it('supports scalar SLA targets and rejects non-finite measurements', () => {
    expect(validateServiceLevel({id: 'available', measure: 'availability', target: 99.9,
      comparison: '>=', unit: 'percent', window: {period: '30d'}})).toMatchObject({target: 99.9});
    expect(() => validateServiceLevel({id: 'bad', measure: 'latency', target: Infinity,
      comparison: '<=', window: {}})).toThrow('finite scalar');
  });

  it('requires physical server and authoritative-definition references', () => {
    expect(validateServerBinding(content().servers[0])).toMatchObject({providerId: 'mongodb'});
    expect(() => validateServerBinding({id: 'x', providerId: 'p', environment: 'dev',
      interface: 'native'})).toThrow('requires a resource or API');
    expect(validateAuthoritativeDefinition(content().authoritativeDefinitions[0]))
      .toMatchObject({type: 'business_glossary'});
  });

  it('rejects duplicates, dangling links and cyclic direct identities', () => {
    expect(() => createContractContent(content({elements: [element(), element()]})))
      .toThrow('Duplicate');
    expect(() => createContractContent(content({bindings: [binding({elementId: 'missing'})]})))
      .toThrow('unknown contract element');
    expect(() => createContractContent(content({elements: [element({parentId: 'orders'})]})))
      .toThrow('cannot parent itself');
  });

  it('serializes deterministically and rejects secret-bearing extensions', () => {
    const canonical = createContractContent(content());
    expect(importContractAsset(exportContractAsset(canonical))).toEqual(canonical);
    expect(exportContractAsset(canonical)).toBe(exportContractAsset(canonical));
    expect(Object.isFrozen(canonical.elements[0])).toBe(true);
    expect(() => createContractContent(content({extensions: {nested: {accessToken: 'bad'}}})))
      .toThrow('Raw credential');
  });

  it('builds optimistic project requests with exact dependencies and resources', () => {
    expect(contractAssetRequest({projectId: 'p', assetId: 'orders', name: 'Orders',
      path: 'contracts/orders.json', expectedVersion: 4, content: content()})).toMatchObject({
      asset_type: 'cdeadmin.contract.v1', expected_version: 4,
      dependency_references: [asset()], resource_bindings: [resource()],
      metadata: {moduleId: 'cdeadmin.contract', contractVersion: '1.0.0'}});
  });

  it('retains every nested AssetRef dependency and deduplicates serving ResourceRefs', () => {
    const payload = content({
      elements: [element({authoritativeDefinitionRefs: [asset('element-doc')]})],
      authoritativeDefinitions: [{id: 'schema', type: 'schema', assetRef: asset('schema')}],
      servers: [{...content().servers[0], apiRef: asset('api')}],
    });
    const request = contractAssetRequest({projectId: 'p', assetId: 'orders', name: 'Orders',
      path: 'contracts/orders.json', content: payload});
    expect(request.dependency_references).toHaveLength(4);
    expect(request.dependency_references).toEqual(expect.arrayContaining([
      asset(), asset('element-doc'), asset('schema'), asset('api'),
    ]));
    expect(request.resource_bindings).toEqual([resource()]);
  });
});
