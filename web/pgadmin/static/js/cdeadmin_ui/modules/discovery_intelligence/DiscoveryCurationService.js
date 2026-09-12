/////////////////////////////////////////////////////////////
// Evidence-preserving curation, duplicate and zero-result authority.
/////////////////////////////////////////////////////////////

import {immutable, noRawSecrets, plainObject, platformValue} from
  '../../platform/serviceUtils';
import {DISCOVERY_DUPLICATE_DECISIONS, DISCOVERY_ZERO_RESULT_ACTIONS,
  validateCurationEvidence, validateCurationItem,
  validateDuplicateCandidate} from './DiscoveryGovernanceContracts';

function requireMethod(value, method, label) {
  if(typeof value?.[method] !== 'function') throw new TypeError(
    `${label} requires ${method}.`
  );
}

export class InMemoryDiscoveryCurationStore {
  constructor() {
    this.items = new Map(); this.itemHistory = new Map();
    this.evidence = new Map(); this.duplicates = new Map();
  }
  appendItem(input) {
    const item = validateCurationItem(input); this.items.set(item.itemId, item);
    this.itemHistory.set(item.itemId,
      [...(this.itemHistory.get(item.itemId) ?? []), item]); return item;
  }
  item(id) { return this.items.get(String(id)) ?? null; }
  revisions(id) { return [...(this.itemHistory.get(String(id)) ?? [])]; }
  list() { return [...this.items.values()]; }
  appendEvidence(input) {
    const value = validateCurationEvidence(input);
    if(this.evidence.has(value.evidenceId)) throw new TypeError(
      `Curation evidence ${value.evidenceId} already exists.`
    );
    this.evidence.set(value.evidenceId, value); return value;
  }
  evidenceRecord(id) { return this.evidence.get(String(id)) ?? null; }
  appendDuplicate(input) {
    const value = validateDuplicateCandidate(input);
    if(this.duplicates.has(value.candidateId)) throw new TypeError(
      `Duplicate candidate ${value.candidateId} already exists.`
    );
    this.duplicates.set(value.candidateId, value); return value;
  }
  duplicate(id) { return this.duplicates.get(String(id)) ?? null; }
}

export class DiscoveryCurationService {
  constructor({store, mutationAuthority, relationshipAuthority,
    now=() => new Date().toISOString(), idFactory}={}) {
    for(const method of ['appendItem', 'item', 'appendEvidence',
      'appendDuplicate', 'duplicate']) requireMethod(store, method,
      'Discovery curation store');
    requireMethod(mutationAuthority, 'apply', 'Discovery curation mutation');
    requireMethod(relationshipAuthority, 'record',
      'Discovery relationship authority');
    if(typeof idFactory !== 'function') throw new TypeError(
      'Discovery curation requires an ID authority.'
    );
    this.store = store; this.mutationAuthority = mutationAuthority;
    this.relationshipAuthority = relationshipAuthority; this.now = now;
    this.idFactory = idFactory; this.candidateItems = new Map();
  }

  open(input, {security}={}) {
    requireMethod(security, 'createCurationItem', 'Discovery curation security');
    if(security.createCurationItem(input.targetRef, input.type) !== true) {
      throw new Error('Discovery curation creation denied.');
    }
    return this.store.appendItem({...input, schemaVersion: 1,
      itemId: this.idFactory('item'), status: 'OPEN', assigneeRef: null,
      comments: [], createdAt: this.now(), resolvedAt: null,
      resolutionRef: null});
  }

  assign(itemId, assigneeRef, {security, dueAt=null}={}) {
    const item = this._item(itemId, ['OPEN', 'ASSIGNED']);
    requireMethod(security, 'assignCurationItem', 'Discovery curation security');
    assigneeRef = platformValue(assigneeRef, 'Discovery curation assignee', 512);
    if(security.assignCurationItem(item, assigneeRef) !== true) throw new Error(
      'Discovery curation assignment denied.'
    );
    return this.store.appendItem({...item, status: 'ASSIGNED', assigneeRef, dueAt});
  }

  comment(itemId, {actorRef, text, security}={}) {
    const item = this._item(itemId, ['OPEN', 'ASSIGNED']);
    requireMethod(security, 'commentCurationItem', 'Discovery curation security');
    actorRef = platformValue(actorRef, 'Discovery curation comment actor', 512);
    text = platformValue(text, 'Discovery curation comment', 4000);
    if(security.commentCurationItem(item) !== true) throw new Error(
      'Discovery curation comment denied.'
    );
    return this.store.appendItem({...item, comments: [...item.comments,
      {actorRef, text, time: this.now()}]});
  }

  async resolve(itemId, {action, value=null, actorRef, reason,
    sourceEvidenceRefs, security}={}) {
    const item = this._item(itemId, ['OPEN', 'ASSIGNED']);
    requireMethod(security, 'resolveCurationItem', 'Discovery curation security');
    actorRef = platformValue(actorRef, 'Discovery curation actor', 512);
    reason = platformValue(reason, 'Discovery curation reason', 4000);
    if(security.resolveCurationItem(item, action) !== true) throw new Error(
      'Discovery curation resolution denied.'
    );
    const result = await this.mutationAuthority.apply({item, action, value},
      {security});
    plainObject(result, 'Discovery curation mutation result');
    noRawSecrets(result, 'Discovery curation mutation result');
    const evidence = this.store.appendEvidence({schemaVersion: 1,
      evidenceId: this.idFactory('evidence'), actorRef, time: this.now(),
      targetRef: item.targetRef,
      fieldOrRelationship: result.fieldOrRelationship,
      before: result.before, after: result.after, reason, sourceEvidenceRefs});
    const resolved = this.store.appendItem({...item, status: 'RESOLVED',
      resolvedAt: this.now(), resolutionRef: evidence.evidenceId});
    return immutable({item: resolved, evidence,
      mutationRef: result.mutationRef ?? null});
  }

  dismiss(itemId, {actorRef, reason, sourceEvidenceRefs, security}={}) {
    const item = this._item(itemId, ['OPEN', 'ASSIGNED']);
    requireMethod(security, 'resolveCurationItem', 'Discovery curation security');
    if(security.resolveCurationItem(item, 'dismiss') !== true) throw new Error(
      'Discovery curation dismissal denied.'
    );
    const evidence = this.store.appendEvidence({schemaVersion: 1,
      evidenceId: this.idFactory('evidence'), actorRef, time: this.now(),
      targetRef: item.targetRef, fieldOrRelationship: 'curation_status',
      before: item.status, after: 'DISMISSED', reason, sourceEvidenceRefs});
    return immutable({item: this.store.appendItem({...item, status: 'DISMISSED',
      resolvedAt: this.now(), resolutionRef: evidence.evidenceId}), evidence});
  }

  addDuplicateCandidate(input, {security}={}) {
    requireMethod(security, 'createDuplicateCandidate',
      'Discovery duplicate security');
    if(input.leftRef === input.rightRef) return immutable({
      alreadyCanonical: true, canonicalRef: input.leftRef, candidate: null});
    if(security.createDuplicateCandidate(input.leftRef, input.rightRef) !== true) {
      throw new Error('Discovery duplicate candidate creation denied.');
    }
    const candidate = this.store.appendDuplicate({...input, schemaVersion: 1,
      candidateId: this.idFactory('duplicate'), createdAt: this.now()});
    const queueItem = this.store.appendItem({schemaVersion: 1,
      itemId: this.idFactory('item'), type: 'duplicate_candidate',
      targetRef: candidate.leftRef,
      summary: `Review possible duplicate ${candidate.leftRef} and ${candidate.rightRef}.`,
      evidenceRefs: [`duplicate-candidate:${candidate.candidateId}`], status: 'OPEN',
      assigneeRef: null, dueAt: null, comments: [], createdAt: this.now(),
      resolvedAt: null, resolutionRef: null});
    this.candidateItems.set(candidate.candidateId, queueItem.itemId);
    return immutable({alreadyCanonical: false, candidate, queueItem});
  }

  async decideDuplicate(candidateId, {decision, actorRef, reason,
    sourceEvidenceRefs, security}={}) {
    requireMethod(security, 'resolveDuplicateCandidate',
      'Discovery duplicate security');
    if(!DISCOVERY_DUPLICATE_DECISIONS.includes(decision)) throw new TypeError(
      'Discovery duplicate decision is invalid.'
    );
    const candidate = this.store.duplicate(candidateId);
    if(!candidate) throw new Error(`Duplicate candidate ${candidateId} was not found.`);
    if(security.resolveDuplicateCandidate(candidate, decision) !== true) {
      throw new Error('Discovery duplicate decision denied.');
    }
    let relationshipRef = null;
    if(decision !== 'not_duplicate') relationshipRef = await
    this.relationshipAuthority.record({fromRef: candidate.leftRef,
      toRef: candidate.rightRef, relationship: decision,
      evidenceRefs: sourceEvidenceRefs}, {security});
    const evidence = this.store.appendEvidence({schemaVersion: 1,
      evidenceId: this.idFactory('evidence'), actorRef, time: this.now(),
      targetRef: candidate.leftRef, fieldOrRelationship: 'duplicate_relationship',
      before: null, after: {rightRef: candidate.rightRef, decision,
        relationshipRef}, reason, sourceEvidenceRefs});
    const itemId = this.candidateItems.get(candidate.candidateId);
    if(itemId) {
      const item = this.store.item(itemId);
      this.store.appendItem({...item, status: decision === 'not_duplicate' ?
        'DISMISSED' : 'RESOLVED', resolvedAt: this.now(),
      resolutionRef: evidence.evidenceId});
    }
    return immutable({candidate, decision, relationshipRef, evidence,
      identitiesMerged: false});
  }

  async resolveZeroResult(input, {security}={}) {
    if(!DISCOVERY_ZERO_RESULT_ACTIONS.includes(input.action)) throw new TypeError(
      'Discovery zero-result action is invalid.'
    );
    requireMethod(security, 'resolveZeroResult',
      'Discovery zero-result security');
    requireMethod(security, 'admitDocumentRef',
      'Discovery zero-result security');
    if(!Number.isInteger(input.frequency) || input.frequency < 1) throw new TypeError(
      'Discovery zero-result frequency is invalid.'
    );
    if(['add_synonym', 'map_existing_term'].includes(input.action) &&
        !input.targetRef) throw new TypeError(
      'Discovery zero-result mapping action requires a visible target.'
    );
    if(input.targetRef && security.admitDocumentRef(input.targetRef) !== true) {
      throw new Error('Discovery zero-result target is not visible.');
    }
    if(security.resolveZeroResult(input.action) !== true) throw new Error(
      'Discovery zero-result resolution denied.'
    );
    const item = this.open({type: 'zero_result_term',
      targetRef: `discovery-query://${encodeURIComponent(platformValue(input.query,
        'Discovery zero-result query', 1000))}`,
      summary: `Zero-result query (${input.frequency}): ${input.query}`,
      evidenceRefs: input.sourceEvidenceRefs}, {security});
    return this.resolve(item.itemId, {action: input.action,
      value: input.targetRef ?? null, actorRef: input.actorRef, reason: input.note,
      sourceEvidenceRefs: input.sourceEvidenceRefs, security});
  }

  queue({status=null, type=null, assigneeRef=null, security}={}) {
    requireMethod(security, 'viewCurationQueue', 'Discovery curation security');
    if(security.viewCurationQueue() !== true) throw new Error(
      'Discovery curation queue access denied.'
    );
    return immutable(this.store.list().filter((item) =>
      (!status || item.status === status) && (!type || item.type === type) &&
      (!assigneeRef || item.assigneeRef === assigneeRef)).filter((item) =>
      security.viewCurationQueue(item) === true).sort((left, right) =>
      (left.dueAt ?? '9999').localeCompare(right.dueAt ?? '9999') ||
      left.itemId.localeCompare(right.itemId)));
  }

  evidence(evidenceId, {security}={}) {
    requireMethod(security, 'viewCurationEvidence', 'Discovery curation security');
    const value = this.store.evidenceRecord(platformValue(evidenceId,
      'Discovery curation evidence ID', 512));
    if(!value) throw new Error(`Curation evidence ${evidenceId} was not found.`);
    if(security.viewCurationEvidence(value) !== true) throw new Error(
      'Discovery curation evidence access denied.'
    );
    return value;
  }

  _item(itemId, states) {
    const item = this.store.item(platformValue(itemId,
      'Discovery curation item ID', 512));
    if(!item) throw new Error(`Curation item ${itemId} was not found.`);
    if(states && !states.includes(item.status)) throw new TypeError(
      `Curation item state ${item.status} is not valid for this action.`
    );
    return item;
  }
}
