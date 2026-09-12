/////////////////////////////////////////////////////////////
// CDC validation, state transitions, envelopes and redaction.
/////////////////////////////////////////////////////////////

import {immutable, plainObject, platformValue} from '../../platform/serviceUtils';
import {
  CDC_EVOLUTION_ACTIONS, CDC_RUNTIME_STATES, createCDCContent,
  validateEventEnvelope, validateReplayRequest,
} from './contracts';

const TRANSITIONS = Object.freeze({
  provisioning: ['snapshotting', 'catching_up', 'streaming', 'failed', 'stopping'],
  snapshotting: ['catching_up', 'failed', 'paused', 'stopping'],
  catching_up: ['streaming', 'failed', 'paused', 'stopping'],
  streaming: ['paused', 'degraded', 'failed', 'stopping'],
  paused: ['snapshotting', 'catching_up', 'streaming', 'stopping', 'failed'],
  degraded: ['streaming', 'paused', 'failed', 'stopping'],
  failed: ['provisioning', 'snapshotting', 'catching_up', 'streaming', 'stopping'],
  stopping: ['stopped', 'failed'],
  stopped: ['provisioning'],
});

export function validateCDCDefinition(input) {
  const content = createCDCContent(input); const details = []; const warnings = [];
  if(!content.name.trim()) details.push('CDC definition name is required.');
  if(!content.source) details.push('A capture source ResourceRef is required.');
  if(!content.sink) details.push('A sink ResourceRef is required.');
  if(content.source && !content.source.nativeMechanism.trim()) {
    details.push('A provider-native capture mechanism name is required.');
  }
  if(content.source?.snapshotPolicy.mode === 'incremental' &&
      !content.source.snapshotPolicy.batchSize) {
    details.push('Incremental snapshots require a positive batch size.');
  }
  const policy = content.schemaEvolutionPolicy;
  policy.changes.forEach((change) => {
    if(['breaking', 'unknown'].includes(change.classification) &&
        change.action === 'auto_apply_compatible') details.push(
      `Schema change ${change.id} cannot be auto-applied.`
    );
    if(['breaking', 'unknown'].includes(change.classification) &&
        change.action !== 'pause_and_review') warnings.push(
      `Schema change ${change.id} bypasses the default pause-and-review safeguard.`
    );
  });
  if(policy.defaultAction === 'auto_apply_compatible') warnings.push(
    'Compatible schema changes may be auto-applied; breaking and unknown changes still pause.'
  );
  if(content.deliveryPolicy.guarantee === 'unknown') warnings.push(
    'End-to-end delivery guarantee is unknown.'
  );
  if(content.deliveryPolicy.guarantee === 'provider_specific') warnings.push(
    'Delivery semantics are provider-specific; review native evidence before execution.'
  );
  return immutable({valid: details.length === 0, details: [...new Set(details)],
    warnings: [...new Set(warnings)]});
}

export function assertRunTransition(from, to) {
  if(!CDC_RUNTIME_STATES.includes(from) || !CDC_RUNTIME_STATES.includes(to)) {
    throw new TypeError('CDC runtime state is invalid.');
  }
  if(!TRANSITIONS[from].includes(to)) throw new Error(
    `Invalid CDC run transition: ${from} → ${to}.`
  );
  return to;
}

export function effectiveSchemaAction(change, policy) {
  plainObject(change, 'CDC schema change'); plainObject(policy, 'CDC evolution policy');
  if(['breaking', 'unknown'].includes(change.classification)) return 'pause_and_review';
  const action = change.action ?? policy.defaultAction;
  if(!CDC_EVOLUTION_ACTIONS.includes(action)) throw new TypeError(
    'CDC schema-change action is invalid.'
  );
  return action;
}

function redactPath(value, path) {
  const parts = String(path).split('.').filter(Boolean); if(!parts.length) return value;
  const copy = Array.isArray(value) ? [...value] : {...value}; let cursor = copy;
  for(let index = 0; index < parts.length - 1; index++) {
    const part = parts[index]; const child = cursor?.[part];
    if(!child || typeof child !== 'object') return copy;
    cursor[part] = Array.isArray(child) ? [...child] : {...child}; cursor = cursor[part];
  }
  if(cursor && Object.prototype.hasOwnProperty.call(cursor, parts.at(-1))) {
    cursor[parts.at(-1)] = '[REDACTED]';
  }
  return copy;
}

export function redactEventEnvelope(input, {canViewPayloads=false, redactedPaths=[]}={}) {
  const event = validateEventEnvelope(input); const result = {...event};
  if(!canViewPayloads) {
    ['key', 'before', 'after', 'changedFields', 'headers', 'metadata'].forEach((field) => {
      if(Object.prototype.hasOwnProperty.call(result, field)) result[field] = '[REDACTED]';
    });
    result.payloadRedacted = true;
  } else {
    for(const path of redactedPaths) {
      const [root, ...rest] = String(path).split('.');
      if(Object.prototype.hasOwnProperty.call(result, root)) {
        result[root] = rest.length ? redactPath(result[root], rest.join('.')) : '[REDACTED]';
      }
    }
    result.payloadRedacted = redactedPaths.length > 0;
  }
  return immutable(result);
}

export function validateReplaySafety(input) {
  const request = validateReplayRequest(input); const details = []; const warnings = [];
  if(!request.idempotencyAssessment.trim()) details.push(
    'Replay requires an idempotency or deduplication assessment.'
  );
  if(!request.schemaCompatibility.trim()) details.push(
    'Replay requires schema compatibility validation.'
  );
  if(request.production && !request.productionConfirmed) details.push(
    'Production replay requires explicit confirmation.'
  );
  if(request.estimatedEventCount === null) warnings.push(
    'The provider did not supply an event-count estimate.'
  );
  return immutable({valid: details.length === 0, request, details, warnings});
}

export function normalizeRunResult(input, {phase}) {
  plainObject(input, 'CDC run result');
  const state = platformValue(input.state, 'CDC run-result state');
  if(!CDC_RUNTIME_STATES.includes(state)) throw new TypeError('CDC run-result state is invalid.');
  const lag = input.lag === undefined || input.lag === null ? null : Number(input.lag);
  const throughput = input.throughput === undefined || input.throughput === null ? null :
    Number(input.throughput);
  if(lag !== null && (!Number.isFinite(lag) || lag < 0)) throw new TypeError(
    'CDC lag must be a non-negative number.'
  );
  if(throughput !== null && (!Number.isFinite(throughput) || throughput < 0)) throw new TypeError(
    'CDC throughput must be a non-negative number.'
  );
  return immutable({schema: 'cdeadmin.cdc-run-result.v1', phase, state, lag, throughput,
    checkpoint: input.checkpoint ?? null, snapshotProgress: input.snapshotProgress ?? null,
    errors: Array.isArray(input.errors) ? input.errors.map(String) : [],
    warnings: Array.isArray(input.warnings) ? input.warnings.map(String) : [],
    evidence: input.evidence ?? {}, nativeDetails: input.nativeDetails ?? {},
    events: Array.isArray(input.events) ? input.events : []});
}
