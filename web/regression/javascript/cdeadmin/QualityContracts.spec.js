/////////////////////////////////////////////////////////////
// Data Quality canonical contract and secret-safety gates.
/////////////////////////////////////////////////////////////

import {
  createQualityContent, exportQualityAsset, importQualityAsset,
  qualityAssetRequest, qualityReferenceKey, validateDataSlice,
  validateQualityAction, validateQualityRef, validateQualityRule,
  validateSamplingPolicy, validateThreshold,
} from 'sources/cdeadmin_ui/modules/quality';

const resource = (name='orders') => ({schema: 'cdeadmin.resource-ref.v1',
  provider: 'firebird', canonical: `cde-resource://firebird/local/table/${name}`});
const sample = () => ({schema: 'cdeadmin.external-ref.v1', id: 'sample:orders:42'});
const slice = (updates={}) => ({id: 'orders-slice', resourceRef: resource(),
  samplePolicy: {mode: 'random_n', limit: 500}, nativeDetails: {}, ...updates});
const rule = (updates={}) => ({id: 'not-null-id', name: 'Order ID is present',
  type: 'not_null', dimension: 'completeness', parameters: {column: 'ID'},
  threshold: {operator: '>=', value: 0.99}, ratio: true, severity: 'critical',
  evaluationMode: 'exact', enabled: true, actionIds: [], documentation: '',
  nativeDetails: {}, ...updates});

describe('Data Quality contracts', () => {
  it('accepts every supported stable reference identity and rejects invented schemas', () => {
    expect(qualityReferenceKey(resource())).toContain('resource:');
    expect(qualityReferenceKey({schema: 'cdeadmin.asset-ref.v1', projectId: 'p', assetId: 'a'}))
      .toBe('asset:p/a');
    expect(qualityReferenceKey({schema: 'cdeadmin.result-ref.v1', id: 'result:1'}))
      .toBe('cdeadmin.result-ref.v1:result:1');
    expect(validateQualityRef(sample())).toEqual(sample());
    expect(() => validateQualityRef({schema: 'invented', id: 'x'})).toThrow('unsupported');
  });

  it('validates all threshold operators, bounded ratios and ordered ranges', () => {
    for(const operator of ['=', '!=', '<', '<=', '>', '>=']) {
      expect(validateThreshold({operator, value: 0.5}, {ratio: true})).toEqual({operator,
        value: 0.5});
    }
    expect(validateThreshold({operator: 'between', lower: 10, upper: 20}))
      .toEqual({operator: 'between', lower: 10, upper: 20});
    expect(validateThreshold({operator: 'outside', lower: 10, upper: 20}))
      .toEqual({operator: 'outside', lower: 10, upper: 20});
    expect(() => validateThreshold({operator: 'between', lower: 2, upper: 1}))
      .toThrow('must not exceed');
    expect(() => validateThreshold({operator: '>=', value: 1.1}, {ratio: true}))
      .toThrow('invalid');
  });

  it('validates every sampling mode and its mode-specific bounds', () => {
    expect(validateSamplingPolicy({mode: 'none_exact'})).toEqual({mode: 'none_exact'});
    expect(validateSamplingPolicy({mode: 'first_n', limit: 1})).toMatchObject({limit: 1});
    expect(validateSamplingPolicy({mode: 'random_n', limit: 1000000})).toMatchObject({limit: 1000000});
    expect(validateSamplingPolicy({mode: 'percentage', percentage: 0.25}))
      .toMatchObject({percentage: 0.25});
    expect(validateSamplingPolicy({mode: 'partition', partition: 'FY2026'}))
      .toMatchObject({partition: 'FY2026'});
    expect(validateSamplingPolicy({mode: 'time_window', window: {hours: 24}}))
      .toMatchObject({window: {hours: 24}});
    expect(validateSamplingPolicy({mode: 'provider_native', nativeDetails: {sample: 'pages'}}))
      .toMatchObject({nativeDetails: {sample: 'pages'}});
    expect(() => validateSamplingPolicy({mode: 'first_n', limit: 1.5})).toThrow('integer');
  });

  it('creates typed scopes and rules without erasing provider-native details', () => {
    expect(validateDataSlice(slice({partition: 'p1', filter: {status: 'OPEN'}})))
      .toMatchObject({schema: 'cdeadmin.quality-data-slice.v1', partition: 'p1',
        filter: {status: 'OPEN'}});
    expect(validateQualityRule(rule({nativeDetails: {collation: 'UNICODE'}})))
      .toMatchObject({type: 'not_null', nativeDetails: {collation: 'UNICODE'}});
    expect(() => validateQualityRule(rule({type: 'made_up'}))).toThrow('Invalid rule family');
    expect(() => validateQualityRule(rule({dimension: 'made_up'}))).toThrow('Invalid quality dimension');
  });

  it('requires immutable profiler suggestions to carry source evidence', () => {
    expect(() => validateQualityRule(rule({generatedSuggestion: true})))
      .toThrow('require a source sample');
    expect(validateQualityRule(rule({generatedSuggestion: true, sourceSampleRef: sample(),
      sourceRevision: 'snapshot-42'}))).toMatchObject({generatedSuggestion: true,
      sourceRevision: 'snapshot-42'});
  });

  it('validates failure actions and command authority bindings', () => {
    expect(validateQualityAction({id: 'stop', type: 'block', label: 'Stop delivery',
      severities: ['critical'], enabled: true, parameters: {checkpoint: 'publish'}}))
      .toMatchObject({type: 'block'});
    expect(validateQualityAction({id: 'notify', type: 'call_command', label: 'Notify owner',
      commandId: 'notification.send', enabled: true, parameters: {channel: 'quality'}}))
      .toMatchObject({commandId: 'notification.send'});
    expect(() => validateQualityAction({id: 'bad', type: 'call_command', enabled: true,
      parameters: {}})).toThrow('command ID');
  });

  it('creates deterministic complete assets and enforces relationship integrity', () => {
    const content = createQualityContent({name: 'Orders', tags: ['z', 'a', 'a'],
      defaultScope: slice(), rules: [rule()], actions: [], severityPolicy: {
        weights: {critical: 5, warning: 2, info: 1}}, baselineRefs: [sample()]});
    expect(content).toMatchObject({schema: 'cdeadmin.quality.asset.v1',
      moduleId: 'cdeadmin.quality', tags: ['a', 'z']});
    expect(Object.isFrozen(content.rules[0])).toBe(true);
    expect(importQualityAsset(exportQualityAsset(content))).toEqual(content);
    expect(exportQualityAsset(content)).toBe(exportQualityAsset(content));
    expect(() => createQualityContent({rules: [rule(), rule()]})).toThrow('Duplicate');
    expect(() => createQualityContent({rules: [rule({actionIds: ['missing']})]}))
      .toThrow('unknown action');
  });

  it('rejects malformed severity policies and raw credentials at every depth', () => {
    expect(() => createQualityContent({severityPolicy: {weights: 'heavy'}}))
      .toThrow('must be an object');
    expect(() => createQualityContent({severityPolicy: {weights: {unknown: 2}}}))
      .toThrow('Unknown quality severity');
    expect(() => createQualityContent({severityPolicy: {weights: {critical: Infinity}}}))
      .toThrow('invalid');
    expect(() => createQualityContent({parameters: {nested: {accessToken: 'secret'}}}))
      .toThrow('Raw credential');
  });

  it('builds optimistic project requests with stable resource and baseline bindings', () => {
    const content = createQualityContent({defaultScope: slice(), baselineRefs: [sample()]});
    expect(qualityAssetRequest({projectId: 'p', assetId: 'quality', name: 'Quality',
      path: 'quality/orders.json', expectedVersion: 7, content})).toMatchObject({
      asset_type: 'cdeadmin.quality.v1', expected_version: 7,
      dependency_references: [sample()], resource_bindings: [resource()],
      validation_state: 'valid'});
  });
});
