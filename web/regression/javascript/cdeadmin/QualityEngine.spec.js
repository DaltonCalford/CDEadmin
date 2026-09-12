/////////////////////////////////////////////////////////////
// Data Quality evaluation, profiling and drift engine gates.
/////////////////////////////////////////////////////////////

import {
  acceptProfileSuggestion, captureQualityBaseline, compareBaseline,
  normalizeProfileSuggestions, normalizeRuleResult, summarizeRun, thresholdPass,
} from 'sources/cdeadmin_ui/modules/quality';

const ref = (id='orders') => ({schema: 'cdeadmin.external-ref.v1', id});
const resource = () => ({schema: 'cdeadmin.resource-ref.v1', provider: 'firebird',
  canonical: 'cde-resource://firebird/local/table/orders'});
const rule = (updates={}) => ({id: 'r1', name: 'Completeness', type: 'completeness_ratio',
  dimension: 'completeness', parameters: {}, threshold: {operator: '>=', value: 0.9},
  ratio: true, severity: 'critical', evaluationMode: 'exact', enabled: true,
  actionIds: [], documentation: '', nativeDetails: {}, ...updates});

describe('Data Quality engine', () => {
  it('evaluates every threshold operator including inclusive and outside bounds', () => {
    expect(thresholdPass(2, {operator: '=', value: 2})).toBe(true);
    expect(thresholdPass(2, {operator: '!=', value: 1})).toBe(true);
    expect(thresholdPass(1, {operator: '<', value: 2})).toBe(true);
    expect(thresholdPass(2, {operator: '<=', value: 2})).toBe(true);
    expect(thresholdPass(3, {operator: '>', value: 2})).toBe(true);
    expect(thresholdPass(2, {operator: '>=', value: 2})).toBe(true);
    expect(thresholdPass(2, {operator: 'between', lower: 1, upper: 2})).toBe(true);
    expect(thresholdPass(3, {operator: 'outside', lower: 1, upper: 2})).toBe(true);
  });

  it('normalizes pass, fail and explicit provider error without conflating them', () => {
    expect(normalizeRuleResult(rule(), {observedMetric: 0.95, evaluatedCount: 100,
      violationCount: 5}).status).toBe('pass');
    expect(normalizeRuleResult(rule(), {observedMetric: 0.5, evaluatedCount: 100,
      violationCount: 50}).status).toBe('fail');
    expect(normalizeRuleResult(rule(), {status: 'error', error: 'catalog unavailable',
      evaluatedCount: 0, violationCount: 0})).toMatchObject({status: 'error',
      error: 'catalog unavailable'});
  });

  it('validates result modes, counts and sample evidence', () => {
    expect(normalizeRuleResult(rule({threshold: null}), {passed: false, evaluatedCount: 2,
      violationCount: 1, violationSampleRef: ref('sample:1'), dataRevision: 'txn:42'}))
      .toMatchObject({status: 'fail', violationSampleRef: ref('sample:1'), dataRevision: 'txn:42'});
    expect(() => normalizeRuleResult(rule(), {observedMetric: 1, evaluatedCount: 2,
      violationCount: 3})).toThrow('counts');
    expect(() => normalizeRuleResult(rule(), {observedMetric: 1,
      evaluationMode: 'invented'})).toThrow('Invalid result evaluation mode');
  });

  it('summarizes failures and execution errors separately with severity counts', () => {
    const pass = normalizeRuleResult(rule({severity: 'info'}), {observedMetric: 1,
      evaluatedCount: 1, violationCount: 0});
    const fail = normalizeRuleResult(rule({id: 'r2', severity: 'warning'}), {observedMetric: 0,
      evaluatedCount: 1, violationCount: 1});
    const error = normalizeRuleResult(rule({id: 'r3'}), {status: 'error', error: 'offline'});
    expect(summarizeRun([pass, fail, error])).toEqual({counts: {pass: 1, fail: 1, error: 1},
      severities: {info: 0, warning: 1, critical: 1}, total: 3, outcome: 'error'});
  });

  it('captures versioned finite baseline metrics and exact resource evidence', () => {
    expect(captureQualityBaseline({id: 'base-1', metrics: {rows: 10, nulls: 1},
      period: {kind: 'point'}, dataRevision: 'txn:1', resourceRef: resource(),
      capturedAt: '2026-09-11T12:00:00Z'})).toMatchObject({id: 'base-1',
      metrics: {nulls: 1, rows: 10}, dataRevision: 'txn:1'});
    expect(() => captureQualityBaseline({id: 'bad', metrics: {rows: Infinity}, period: {},
      dataRevision: 'x', resourceRef: resource(), capturedAt: 'now'})).toThrow('numeric');
  });

  it('detects drift, missing metrics, zero baselines and tolerance boundaries', () => {
    const baseline = {id: 'base', metrics: {rows: 100, nulls: 0, removed: 2}};
    const drift = compareBaseline(baseline, {rows: 110, nulls: 1, added: 3},
      {rows: 0.1, default: 0});
    expect(drift.changes.find((item) => item.metric === 'rows').drifted).toBe(false);
    expect(drift.changes.find((item) => item.metric === 'nulls')).toMatchObject({ratio: null,
      drifted: true});
    expect(drift.changes.find((item) => item.metric === 'removed').reason)
      .toBe('missing_metric');
    expect(() => compareBaseline(baseline, {rows: 100, nulls: 0, removed: 2},
      {default: -1})).toThrow('tolerance');
  });

  it('normalizes profiler suggestions with immutable provenance and explicit acceptance', () => {
    const suggestions = normalizeProfileSuggestions([rule({threshold: null})], {
      sourceSampleRef: ref('sample:profile'), sourceRevision: 'rev-7'});
    expect(suggestions[0]).toMatchObject({generatedSuggestion: true,
      sourceRevision: 'rev-7'});
    const accepted = acceptProfileSuggestion(suggestions[0]);
    expect(accepted).toMatchObject({generatedSuggestion: false,
      nativeDetails: {acceptedSuggestion: true}});
    expect(() => acceptProfileSuggestion(rule())).toThrow('Only evidenced');
  });

  it('rejects duplicate and unsupported provider suggestions', () => {
    expect(() => normalizeProfileSuggestions([rule(), rule()], {
      sourceSampleRef: ref(), sourceRevision: 'rev'})).toThrow('Duplicate');
    expect(() => normalizeProfileSuggestions([rule({type: 'invented'})], {
      sourceSampleRef: ref(), sourceRevision: 'rev'})).toThrow('unknown rule family');
  });
});
