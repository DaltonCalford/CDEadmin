/////////////////////////////////////////////////////////////
// CDEadmin AI retry authority separating model, read and mutation safety.
/////////////////////////////////////////////////////////////

import {abortError, immutable} from '../../platform/serviceUtils';
import {normalizeExecutionFailure} from './AILifecycleContracts';

const TRANSIENT = new Set(['MODEL_PROVIDER_FAILURE', 'CONNECTOR_TRANSPORT_FAILURE',
  'PROVIDER_ERROR', 'MCP_TOOL_ERROR']);

export class AIRetryPolicyService {
  constructor({modelMaximum=2, readMaximum=1, mutationMaximum=1}={}) {
    for(const [name, value] of Object.entries({modelMaximum, readMaximum, mutationMaximum})) if(
      !Number.isInteger(value) || value < 0 || value > 10) throw new TypeError(
      `AI ${name} retry maximum must be from zero to ten.`);
    this.maximum = immutable({model: modelMaximum, read: readMaximum,
      mutation: mutationMaximum});
  }

  async run(kind, operation, {signal, consequentialActionSinceCheckpoint=false,
    providerReadIdempotent=false, explicitlyIdempotent=false,
    backendIdempotencyRef=null, onRetry=() => {}}={}) {
    if(!['model', 'read', 'mutation'].includes(kind)) throw new TypeError(
      'AI retry operation kind is invalid.');
    if(typeof operation !== 'function' || typeof onRetry !== 'function') throw new TypeError(
      'AI retry operation and notification callback are required.');
    const eligible = kind === 'model' ? !consequentialActionSinceCheckpoint :
      kind === 'read' ? providerReadIdempotent === true : explicitlyIdempotent === true ||
        typeof backendIdempotencyRef === 'string' && backendIdempotencyRef.trim().length > 0;
    let attempt = 0;
    for(;;) {
      if(signal?.aborted) throw abortError('AI operation was cancelled.');
      attempt++;
      try { return await operation({attempt, signal}); } catch(error) {
        if(signal?.aborted || error?.name === 'AbortError') throw abortError(
          'AI operation was cancelled.');
        const failure = normalizeExecutionFailure(error, kind === 'model' ?
          'MODEL_PROVIDER_FAILURE' : 'PROVIDER_ERROR');
        if(!eligible || !failure.retryable || !TRANSIENT.has(failure.category) ||
            attempt > this.maximum[kind]) throw failure;
        onRetry(immutable({kind, attempt, category: failure.category,
          message: failure.message, backendIdempotencyRef}));
      }
    }
  }
}
