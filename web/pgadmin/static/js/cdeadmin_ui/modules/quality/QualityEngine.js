/////////////////////////////////////////////////////////////
// Data Quality threshold, result, profiling and drift engine.
/////////////////////////////////////////////////////////////

import {immutable, plainObject, platformValue} from '../../platform/serviceUtils';
import {
  QUALITY_EVALUATION_MODES, QUALITY_RESULT_STATES, QUALITY_RULE_FAMILIES,
  validateQualityRef, validateQualityRule, validateThreshold,
} from './contracts';

function numeric(value, label) {
  const result = Number(value);
  if(!Number.isFinite(result)) throw new TypeError(`${label} must be numeric.`);
  return result;
}

export function thresholdPass(observed, threshold) {
  threshold = validateThreshold(threshold);
  observed = numeric(observed, 'Observed quality metric');
  if(threshold.operator === '=') return observed === threshold.value;
  if(threshold.operator === '!=') return observed !== threshold.value;
  if(threshold.operator === '<') return observed < threshold.value;
  if(threshold.operator === '<=') return observed <= threshold.value;
  if(threshold.operator === '>') return observed > threshold.value;
  if(threshold.operator === '>=') return observed >= threshold.value;
  if(threshold.operator === 'between') return observed >= threshold.lower && observed <= threshold.upper;
  return observed < threshold.lower || observed > threshold.upper;
}

export function normalizeRuleResult(ruleInput, input) {
  const rule = validateQualityRule(ruleInput); plainObject(input, 'Quality rule result');
  const evaluationMode = platformValue(input.evaluationMode ?? rule.evaluationMode,
    'Result evaluation mode');
  if(!QUALITY_EVALUATION_MODES.includes(evaluationMode)) throw new TypeError(
    `Invalid result evaluation mode: ${evaluationMode}`
  );
  let status;
  if(input.error || input.status === 'error') status = 'error';
  else if(rule.threshold && input.observedMetric !== undefined) {
    status = thresholdPass(input.observedMetric, rule.threshold) ? 'pass' : 'fail';
  } else if(typeof input.passed === 'boolean') status = input.passed ? 'pass' : 'fail';
  else status = platformValue(input.status, 'Quality result status');
  if(!QUALITY_RESULT_STATES.includes(status)) throw new TypeError(`Invalid rule result: ${status}`);
  if(status === 'pass' && input.error) throw new TypeError('Execution errors cannot be passing results.');
  const evaluatedCount = numeric(input.evaluatedCount ?? 0, 'Evaluated count');
  const violationCount = numeric(input.violationCount ?? 0, 'Violation count');
  if(evaluatedCount < 0 || violationCount < 0 || violationCount > evaluatedCount) {
    throw new TypeError('Quality result counts are invalid.');
  }
  return immutable({schema: 'cdeadmin.quality-rule-result.v1',
    id: platformValue(input.id ?? `result:${rule.id}`, 'Rule result ID'), ruleId: rule.id,
    status, evaluationMode, severity: rule.severity,
    observedMetric: input.observedMetric === undefined ? null :
      numeric(input.observedMetric, 'Observed quality metric'),
    threshold: rule.threshold, evaluatedCount, violationCount,
    error: status === 'error' ? platformValue(input.error ?? 'Provider execution failed',
      'Quality execution error', 4096) : null,
    dataRevision: input.dataRevision === undefined ? null :
      platformValue(input.dataRevision, 'Data revision', 4096),
    violationSampleRef: input.violationSampleRef ? validateQualityRef(
      input.violationSampleRef, 'Violation sample reference'
    ) : null,
    diagnostics: immutable({...plainObject(input.diagnostics ?? {}, 'Result diagnostics')}),
    nativeDetails: immutable({...plainObject(input.nativeDetails ?? {}, 'Result native details')}),
  });
}

export function summarizeRun(results) {
  const counts = {pass: 0, fail: 0, error: 0};
  const severities = {info: 0, warning: 0, critical: 0};
  results.forEach((result) => {
    if(!QUALITY_RESULT_STATES.includes(result.status)) throw new TypeError(
      `Unknown quality result state: ${result.status}`
    );
    counts[result.status]++;
    if(result.status !== 'pass') severities[result.severity]++;
  });
  return immutable({counts, severities, total: results.length,
    outcome: counts.error ? 'error' : counts.fail ? 'fail' : 'pass'});
}

export function captureQualityBaseline({id, metrics, period, dataRevision, resourceRef,
  capturedAt}) {
  plainObject(metrics, 'Baseline metrics');
  const normalized = Object.fromEntries(Object.entries(metrics).sort(([a], [b]) =>
    a.localeCompare(b)).map(([name, value]) => [platformValue(name, 'Baseline metric'),
    numeric(value, `Baseline metric ${name}`)]));
  return immutable({schema: 'cdeadmin.quality-baseline.v1',
    id: platformValue(id, 'Baseline ID'), metrics: immutable(normalized),
    period: immutable({...plainObject(period, 'Baseline period')}),
    dataRevision: platformValue(dataRevision, 'Baseline data revision'),
    resourceRef: validateQualityRef(resourceRef, 'Baseline resource'),
    capturedAt: platformValue(capturedAt, 'Baseline capture time')});
}

export function compareBaseline(baseline, currentMetrics, tolerances={}) {
  plainObject(baseline, 'Quality baseline'); plainObject(currentMetrics, 'Current metrics');
  const changes = Object.keys({...baseline.metrics, ...currentMetrics}).sort().map((metric) => {
    const before = baseline.metrics[metric]; const after = currentMetrics[metric];
    if(before === undefined || after === undefined) return immutable({metric, before: before ?? null,
      after: after ?? null, delta: null, ratio: null, drifted: true, reason: 'missing_metric'});
    const delta = numeric(after, metric) - numeric(before, metric);
    const ratio = before === 0 ? (after === 0 ? 0 : null) : Math.abs(delta / before);
    const tolerance = finiteTolerance(tolerances[metric] ?? tolerances.default ?? 0);
    return immutable({metric, before, after, delta, ratio,
      drifted: ratio === null ? delta !== 0 : ratio > tolerance, reason: 'threshold'});
  });
  return immutable({schema: 'cdeadmin.quality-drift.v1', baselineId: baseline.id,
    changes, drifted: changes.some((item) => item.drifted)});
}

function finiteTolerance(value) {
  const result = Number(value);
  if(!Number.isFinite(result) || result < 0) throw new TypeError('Drift tolerance is invalid.');
  return result;
}

export function normalizeProfileSuggestions(input, {sourceSampleRef, sourceRevision}) {
  if(!Array.isArray(input)) throw new TypeError('Profile suggestions must be an array.');
  const seen = new Set();
  return immutable(input.map((candidate) => {
    if(!QUALITY_RULE_FAMILIES.includes(candidate.type)) throw new TypeError(
      `Profiler proposed unknown rule family: ${candidate.type}`
    );
    const rule = validateQualityRule({...candidate, generatedSuggestion: true,
      sourceSampleRef, sourceRevision});
    if(seen.has(rule.id)) throw new TypeError(`Duplicate profile suggestion: ${rule.id}`);
    seen.add(rule.id); return rule;
  }).sort((left, right) => left.id.localeCompare(right.id)));
}

export function acceptProfileSuggestion(input) {
  const rule = validateQualityRule(input);
  if(!rule.generatedSuggestion || !rule.sourceSampleRef || !rule.sourceRevision) {
    throw new TypeError('Only evidenced generated suggestions can be accepted.');
  }
  return validateQualityRule({...rule, generatedSuggestion: false,
    nativeDetails: {...rule.nativeDetails, acceptedSuggestion: true}});
}
