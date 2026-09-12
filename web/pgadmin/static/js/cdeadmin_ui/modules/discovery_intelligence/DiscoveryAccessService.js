/////////////////////////////////////////////////////////////
// Governed access request, approval and provider provisioning lifecycle.
/////////////////////////////////////////////////////////////

import {immutable, noRawSecrets, plainObject, platformValue} from
  '../../platform/serviceUtils';
import {validateDiscoveryAccessRequest} from './DiscoveryGovernanceContracts';

export const DISCOVERY_ACCESS_PROVISION_TASK = 'discovery.access.provision';

function requireMethod(value, method, label) {
  if(typeof value?.[method] !== 'function') throw new TypeError(
    `${label} requires ${method}.`
  );
}

export class InMemoryDiscoveryAccessRequestStore {
  constructor() { this.current = new Map(); this.history = new Map(); }
  append(input) {
    const request = validateDiscoveryAccessRequest(input);
    this.current.set(request.requestId, request);
    this.history.set(request.requestId,
      [...(this.history.get(request.requestId) ?? []), request]);
    return request;
  }
  get(requestId) { return this.current.get(String(requestId)) ?? null; }
  revisions(requestId) { return [...(this.history.get(String(requestId)) ?? [])]; }
  list() { return [...this.current.values()].sort((left, right) =>
    left.requestId.localeCompare(right.requestId)); }
}

function decisionResult(input) {
  plainObject(input, 'Discovery access policy decision');
  noRawSecrets(input, 'Discovery access policy decision');
  if(!['AUTO_APPROVED', 'PENDING_APPROVAL', 'DENIED'].includes(input.decision)) {
    throw new TypeError('Discovery access policy decision is invalid.');
  }
  if(!input.result || typeof input.result !== 'object') throw new TypeError(
    'Discovery access policy decision requires evidence.'
  );
  return immutable({decision: input.decision, result: {...input.result},
    approvers: [...(input.approvers ?? [])]});
}

export class DiscoveryAccessRequestService {
  constructor({store, policy, grantPlanner, provisioner, tasks,
    now=() => new Date().toISOString(), idFactory}={}) {
    for(const method of ['append', 'get', 'revisions']) requireMethod(store,
      method, 'Discovery access request store');
    requireMethod(policy, 'evaluate', 'Discovery access policy');
    requireMethod(policy, 'review', 'Discovery access policy');
    requireMethod(grantPlanner, 'plan', 'Discovery provider grant planner');
    requireMethod(provisioner, 'apply', 'Discovery provider provisioner');
    requireMethod(provisioner, 'revoke', 'Discovery provider provisioner');
    for(const method of ['register', 'submit']) requireMethod(tasks, method,
      'Discovery access TaskService');
    if(typeof idFactory !== 'function') throw new TypeError(
      'Discovery access requests require an ID authority.'
    );
    this.store = store; this.policy = policy; this.grantPlanner = grantPlanner;
    this.provisioner = provisioner; this.tasks = tasks; this.now = now;
    this.idFactory = idFactory;
    this.unregister = tasks.register(DISCOVERY_ACCESS_PROVISION_TASK,
      (task, context) => this._runProvision(task, context));
  }

  create(input, {security}={}) {
    requireMethod(security, 'createAccessRequest', 'Discovery access security');
    if(security.createAccessRequest(input.targetRef) !== true) throw new Error(
      'Discovery access request creation denied.'
    );
    return this.store.append(validateDiscoveryAccessRequest({...input,
      schemaVersion: 1, requestId: this.idFactory(), policyResult: null,
      approvers: [], status: 'DRAFT', providerGrantPlanRef: null}));
  }

  async submit(requestId, {security}={}) {
    requireMethod(security, 'submitAccessRequest', 'Discovery access security');
    const request = this._state(requestId, ['DRAFT']);
    if(security.submitAccessRequest(request) !== true) throw new Error(
      'Discovery access request submission denied.'
    );
    this.store.append({...request, status: 'SUBMITTED'});
    const evaluated = decisionResult(await this.policy.evaluate(request, {security}));
    return this.store.append({...request, policyResult: evaluated.result,
      approvers: evaluated.approvers, status: evaluated.decision});
  }

  async decide(requestId, decision, {actorRef, note, expiration=null, security}={}) {
    requireMethod(security, 'reviewAccessRequest', 'Discovery access security');
    const request = this._state(requestId, ['PENDING_APPROVAL']);
    if(!['APPROVED', 'DENIED'].includes(decision)) throw new TypeError(
      'Discovery access review decision is invalid.'
    );
    actorRef = platformValue(actorRef, 'Discovery access reviewer', 512);
    note = platformValue(note, 'Discovery access review note', 4000);
    if(security.reviewAccessRequest(request, decision) !== true) throw new Error(
      'Discovery access request review denied.'
    );
    const policyReview = await this.policy.review(request, {decision, actorRef,
      note, expiration, security});
    plainObject(policyReview, 'Discovery access policy review');
    if(policyReview.allowed !== true) throw new Error(
      `Discovery access policy denied review: ${policyReview.reason ?? 'not allowed'}`
    );
    return this.store.append({...request, status: decision,
      policyResult: {...request.policyResult, review: {actorRef, note,
        expiration, reviewedAt: this.now(), policy: policyReview}}});
  }

  async prepareGrant(requestId, {security}={}) {
    requireMethod(security, 'planAccessGrant', 'Discovery access security');
    const request = this._state(requestId, ['AUTO_APPROVED', 'APPROVED']);
    if(security.planAccessGrant(request) !== true) throw new Error(
      'Discovery provider grant planning denied.'
    );
    const plan = await this.grantPlanner.plan(request, {security});
    plainObject(plan, 'Discovery provider grant plan');
    noRawSecrets(plan, 'Discovery provider grant plan');
    const planRef = platformValue(plan.planRef,
      'Discovery provider grant plan reference', 512);
    return immutable({request: this.store.append({...request,
      providerGrantPlanRef: planRef}), plan: immutable({...plan})});
  }

  provision(requestId, {security, owner=null}={}) {
    requireMethod(security, 'provisionAccessGrant', 'Discovery access security');
    const request = this._state(requestId, ['AUTO_APPROVED', 'APPROVED']);
    if(!request.providerGrantPlanRef) throw new TypeError(
      'Provider grant plan must be prepared before provisioning.'
    );
    if(security.provisionAccessGrant(request) !== true) throw new Error(
      'Discovery provider grant provisioning denied.'
    );
    return this.tasks.submit({type: DISCOVERY_ACCESS_PROVISION_TASK,
      label: `Provision access ${request.requestId}`, requestId: request.requestId,
      resourceRefs: [request.targetRef], cancelable: true, resumable: false,
      audit: {requestId: request.requestId,
        providerGrantPlanRef: request.providerGrantPlanRef}}, {owner, security});
  }

  expire(requestId, {security}={}) {
    requireMethod(security, 'expireAccessGrant', 'Discovery access security');
    const request = this._state(requestId, ['ACTIVE']);
    if(security.expireAccessGrant(request) !== true) throw new Error(
      'Discovery access expiration denied.'
    );
    return this.store.append({...request, status: 'EXPIRED'});
  }

  async revoke(requestId, {security, reason}={}) {
    requireMethod(security, 'revokeAccessGrant', 'Discovery access security');
    const request = this._state(requestId, ['ACTIVE']);
    reason = platformValue(reason, 'Discovery access revocation reason', 4000);
    if(security.revokeAccessGrant(request) !== true) throw new Error(
      'Discovery access revocation denied.'
    );
    await this.provisioner.revoke(request, {reason, security});
    return this.store.append({...request, status: 'REVOKED',
      policyResult: {...request.policyResult, revocation: {reason,
        revokedAt: this.now()}}});
  }

  request(requestId, {security}={}) {
    requireMethod(security, 'viewAccessRequest', 'Discovery access security');
    const value = this._state(requestId);
    if(security.viewAccessRequest(value) !== true) throw new Error(
      'Discovery access request view denied.'
    );
    return value;
  }

  history(requestId, {security}={}) {
    requireMethod(security, 'viewAccessRequestHistory', 'Discovery access security');
    const request = this._state(requestId);
    if(security.viewAccessRequestHistory(request) !== true) throw new Error(
      'Discovery access request history denied.'
    );
    return immutable(this.store.revisions(requestId));
  }

  async _runProvision(task, context) {
    const request = this._state(task.requestId, ['AUTO_APPROVED', 'APPROVED']);
    this.store.append({...request, status: 'PROVISIONING'});
    context.phase('provider_grant', 'Applying provider grant plan.');
    try {
      const result = await this.provisioner.apply(request, {security: context.security,
        signal: context.signal, progress: context.progress});
      plainObject(result, 'Discovery provider grant result');
      noRawSecrets(result, 'Discovery provider grant result');
      this.store.append({...request, status: 'ACTIVE'});
      return immutable({...result, requestId: request.requestId,
        status: 'ACTIVE'});
    } catch(error) {
      this.store.append({...request, status: 'FAILED', policyResult: {
        ...request.policyResult, provisioningFailure: {message: error.message,
          failedAt: this.now()}}});
      throw error;
    }
  }

  _state(requestId, allowed=null) {
    const request = this.store.get(platformValue(requestId,
      'Discovery access request ID', 512));
    if(!request) throw new Error(`Discovery access request ${requestId} was not found.`);
    if(allowed && !allowed.includes(request.status)) throw new TypeError(
      `Discovery access request state ${request.status} is not valid for this action.`
    );
    return request;
  }
}
