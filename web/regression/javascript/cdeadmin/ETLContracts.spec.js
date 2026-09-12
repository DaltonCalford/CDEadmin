/////////////////////////////////////////////////////////////
// ETL canonical asset, type and project-persistence gates.
/////////////////////////////////////////////////////////////

import {
  createETLContent, etlAssetRequest, etlReferenceKey, ETL_NODE_FAMILIES,
  exportETLAsset, importETLAsset, validateParameter, validatePipelineEdge,
  validatePipelineNode, validatePort, validateSchemaMapping,
} from 'sources/cdeadmin_ui/modules/etl';

const resource = (provider='firebird', name='SOURCE') => ({
  schema: 'cdeadmin.resource-ref.v1', provider,
  canonical: `cde-resource://${provider}/local/database/demo/table/${name}`});
const port = (id, direction, mode='batch', updates={}) => ({id, name: id, direction, mode,
  schemaState: 'known', fields: [{id: 'id', name: 'ID', nativeType: 'INTEGER',
    semanticType: 'integer', nullable: false, nativeDetails: {}}], ...updates});
const node = (id, kind, reference=null, updates={}) => ({id, name: id, kind, config: {},
  resourceRef: reference, ports: kind === 'source' ? [port('out', 'output')] :
    kind === 'sink' ? [port('in', 'input')] : [port('in', 'input'), port('out', 'output')],
  capabilityRequirements: [], executionPreference: 'source_pushdown',
  checkpointEnabled: false, nativeDetails: {}, ...updates});
const edge = (updates={}) => ({id: 'flow', fromNodeId: 'source', fromPort: 'out',
  toNodeId: 'sink', toPort: 'in', mappingPolicy: 'explicit', mappings: [{id: 'id',
    source: 'id', target: 'id', sourceNativeType: 'INTEGER', semanticType: 'integer',
    targetNativeType: 'BIGINT', conversion: 'widen', nullPolicy: 'preserve', nullable: false,
    lossy: false, lossAcknowledged: false, nativeDetails: {}}],
  deliveryGuarantee: 'at_least_once', partitioning: {}, ordering: {}, nativeDetails: {},
  ...updates});
const deployment = () => ({id: 'dev', name: 'Development', environment: 'development',
  bindings: [{id: 'source-dev', nodeId: 'source', resourceRef: resource(), nativeDetails: {}},
    {id: 'sink-dev', nodeId: 'sink', resourceRef: resource('mongodb', 'TARGET'),
      nativeDetails: {}}], parameterBindings: {}, resourceLimits: {}, nativeDetails: {}});
const content = (updates={}) => ({name: 'Cross-engine orders', description: 'ETL test', mode: 'batch',
  parameters: [], nodes: [node('source', 'source', resource()),
    node('sink', 'sink', resource('mongodb', 'TARGET'), {executionPreference: 'target_pushdown'})],
  edges: [edge()], deployments: [deployment()], schedules: [{id: 'nightly', name: 'Nightly',
    enabled: true, trigger: 'cron', expression: '0 1 * * *', timezone: 'UTC',
    deploymentId: 'dev', dependencyRefs: [], parameters: {}, nativeDetails: {}}],
  tests: [{id: 'count', name: 'Count', definition: {minimum: 1}}],
  visualLayout: {source: {x: 20, y: 30}}, extensions: {}, ...updates});

describe('ETL canonical contracts', () => {
  it('implements every normative node family without coercing native configuration', () => {
    expect(ETL_NODE_FAMILIES).toHaveLength(19);
    ETL_NODE_FAMILIES.forEach((kind) => expect(validatePipelineNode(node(kind, kind,
      ['source', 'sink', 'custom_provider'].includes(kind) ? resource() : null)))
      .toMatchObject({kind}));
    expect(validatePipelineNode(node('documents', 'source', resource('mongodb'), {
      config: {collectionType: 'timeseries'}}))).toMatchObject({
      config: {collectionType: 'timeseries'}});
  });

  it('retains typed port schema, streaming metadata and native field semantics', () => {
    expect(validatePort(port('events', 'output', 'stream', {partitioning: {key: 'tenant'},
      watermark: {field: 'observed_at'}, ordering: ['observed_at']}))).toMatchObject({
      mode: 'stream', schemaState: 'known', fields: [expect.objectContaining({
        nativeType: 'INTEGER', semanticType: 'integer'})], watermark: {field: 'observed_at'}});
    expect(() => validatePort(port('bad', 'sideways'))).toThrow('direction');
  });

  it('retains explicit native/semantic/target mapping and loss acknowledgment', () => {
    expect(validateSchemaMapping({...edge().mappings[0], lossy: true,
      lossAcknowledged: true})).toMatchObject({sourceNativeType: 'INTEGER',
      semanticType: 'integer', targetNativeType: 'BIGINT', lossy: true,
      lossAcknowledged: true});
    expect(validatePipelineEdge(edge())).toMatchObject({deliveryGuarantee: 'at_least_once'});
  });

  it('uses credential references for secret parameters and rejects embedded values', () => {
    expect(validateParameter({id: 'auth', name: 'Authentication', type: 'credential',
      required: true, secret: true, credentialRef: {schema: 'cdeadmin.credential-ref.v1',
        scheme: 'keyring', id: 'etl-dev'}, description: '', nativeDetails: {}}))
      .toMatchObject({secret: true, default: null});
    expect(() => validateParameter({id: 'bad', type: 'text', secret: true,
      default: 'value'})).toThrow('cannot contain');
    expect(() => createETLContent(content({extensions: {accessToken: 'bad'}})))
      .toThrow('Raw credential');
  });

  it('requires an explicit provider resource for dead-letter routing', () => {
    expect(validatePipelineNode(node('sink', 'sink', resource('mongodb'), {errorRoute: {
      action: 'dead_letter', maximumAttempts: 0, targetRef: resource('mongodb', 'DEAD_LETTER'),
      retryPolicy: {}, nativeDetails: {}}}))).toMatchObject({errorRoute: {
      action: 'dead_letter', targetRef: expect.objectContaining({provider: 'mongodb'})}});
    expect(() => validatePipelineNode(node('sink', 'sink', resource('mongodb'), {errorRoute: {
      action: 'dead_letter', targetRef: {schema: 'cdeadmin.asset-ref.v1', projectId: 'p',
        assetId: 'not-a-live-target'}}}))).toThrow('provider ResourceRef');
  });

  it('rejects duplicate identities and dangling graph/deployment/schedule links', () => {
    expect(() => createETLContent(content({nodes: [node('source', 'source'),
      node('source', 'sink')]}))).toThrow('Duplicate');
    expect(() => createETLContent(content({edges: [edge({toNodeId: 'missing'})]})))
      .toThrow('unknown node');
    expect(() => createETLContent(content({deployments: [{...deployment(), bindings: [{
      id: 'bad', nodeId: 'missing', resourceRef: resource(), nativeDetails: {}}]}]})))
      .toThrow('unknown node');
    expect(() => createETLContent(content({schedules: [{...content().schedules[0],
      deploymentId: 'missing'}]}))).toThrow('unknown deployment');
    expect(() => createETLContent(content({deployments: [{...deployment(), bindings: [{
      id: 'bad', nodeId: 'source', resourceRef: {schema: 'cdeadmin.asset-ref.v1', projectId: 'p',
        assetId: 'not-live'}, nativeDetails: {}}]}]}))).toThrow('provider ResourceRef');
  });

  it('serializes deterministically while excluding runtime counters and selections', () => {
    const canonical = createETLContent(content({runtimeCounters: {rows: 42},
      selectedNodeId: 'source'}));
    const text = exportETLAsset(canonical);
    expect(importETLAsset(text)).toEqual(canonical);
    expect(text).not.toContain('runtimeCounters'); expect(text).not.toContain('selectedNodeId');
    expect(Object.isFrozen(canonical.nodes[0].ports[0])).toBe(true);
  });

  it('builds exact optimistic dependencies and provider resource bindings', () => {
    const schemaRef = {schema: 'cdeadmin.asset-ref.v1', projectId: 'p', assetId: 'contract'};
    const value = content({nodes: [node('source', 'source', resource(), {ports: [
      port('out', 'output', 'batch', {schemaRef})]}), node('sink', 'sink', resource(
      'mongodb', 'TARGET'
    ), {executionPreference: 'target_pushdown'})]});
    const request = etlAssetRequest({projectId: 'p', assetId: 'orders', name: 'Orders',
      path: 'etl/orders.json', expectedVersion: 7, content: value});
    expect(request).toMatchObject({asset_type: 'cdeadmin.etl.v1', expected_version: 7,
      dependency_references: [schemaRef]});
    expect(request.resource_bindings).toHaveLength(2);
    expect(etlReferenceKey(schemaRef)).toBe('asset:p/contract');
  });
});
