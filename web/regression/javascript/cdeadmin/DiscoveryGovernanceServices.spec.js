/////////////////////////////////////////////////////////////
// Discovery access, curation and index-administration gates.
/////////////////////////////////////////////////////////////

import {TaskExecutionService} from 'sources/cdeadmin_ui/platform';
import {
  DISCOVERY_ACCESS_PROVISION_TASK, DISCOVERY_ACCESS_STATES,
  DISCOVERY_CURATION_TYPES, DISCOVERY_DUPLICATE_DECISIONS,
  DISCOVERY_INDEX_SOURCE_TYPES, DISCOVERY_RECONCILE_PHASES,
  DiscoveryAccessRequestService, DiscoveryCurationService,
  DiscoveryIndexAdministrationService, DiscoveryIndexSourceRegistry,
  InMemoryDiscoveryAccessRequestStore, InMemoryDiscoveryCurationStore,
  InMemoryDiscoveryIndexConfigurationStore, InMemoryDiscoveryIndexPolicyStore,
  validateCurationEvidence, validateDiscoveryAccessRequest,
  validateDiscoveryIndexBackendConfiguration, validateDiscoveryIndexPolicy,
  validateIndexSourceConfiguration, validateIndexSourceHealth,
} from 'sources/cdeadmin_ui/modules/discovery_intelligence';

const NOW = '2026-09-12T12:00:00.000Z';
const TARGET = 'cde-resource://firebird/local/app/table/customer';

function accessInput(overrides={}) {
  return {requester: 'user:requester', targetRef: TARGET,
    requestedAccess: ['VIEW_SCHEMA', 'QUERY'], environment: 'production',
    reason: 'Prepare the governed monthly report.', duration: 'P30D',
    projectRef: 'project:reporting', ...overrides};
}

function accessSecurity(overrides={}) {
  return {createAccessRequest: () => true, submitAccessRequest: () => true,
    reviewAccessRequest: () => true, planAccessGrant: () => true,
    provisionAccessGrant: () => true, expireAccessGrant: () => true,
    revokeAccessGrant: () => true, viewAccessRequest: () => true,
    viewAccessRequestHistory: () => true, ...overrides};
}

function accessServices({decision='PENDING_APPROVAL', apply=null}={}) {
  const tasks = new TaskExecutionService({now: () => NOW}); let id = 0;
  const provisioner = {apply: apply ?? jest.fn(async () => ({grantRef: 'grant:1'})),
    revoke: jest.fn(async () => ({revoked: true}))};
  const service = new DiscoveryAccessRequestService({
    store: new InMemoryDiscoveryAccessRequestStore(), tasks,
    policy: {evaluate: async () => ({decision, result: {allowed: true},
      approvers: decision === 'PENDING_APPROVAL' ? ['user:approver'] : []}),
    review: async () => ({allowed: true, maximumExpiry: '2026-10-12'})},
    grantPlanner: {plan: async () => ({planRef: 'grant-plan:1',
      operations: [{kind: 'provider_grant'}]})}, provisioner,
    now: () => NOW, idFactory: () => `request-${++id}`});
  return {service, tasks, provisioner};
}

describe('Discovery access contract and lifecycle', () => {
  test('copies the exact eleven access states and seven dimensions', () => {
    expect(DISCOVERY_ACCESS_STATES).toEqual(['DRAFT', 'SUBMITTED',
      'AUTO_APPROVED', 'PENDING_APPROVAL', 'APPROVED', 'DENIED',
      'PROVISIONING', 'ACTIVE', 'EXPIRED', 'REVOKED', 'FAILED']);
    expect(validateDiscoveryAccessRequest({schemaVersion: 1, requestId: 'one',
      ...accessInput(), status: 'DRAFT'}).requestedAccess).toEqual([
      'VIEW_SCHEMA', 'QUERY']);
  });

  test('rejects unknown access fields, permissions and raw secrets', () => {
    expect(() => validateDiscoveryAccessRequest({schemaVersion: 1,
      requestId: 'one', ...accessInput(), requestedAccess: ['MADE_UP'],
      status: 'DRAFT'})).toThrow(/requested access/);
    expect(() => validateDiscoveryAccessRequest({schemaVersion: 1,
      requestId: 'one', ...accessInput(), status: 'DRAFT', password: 'bad'}))
      .toThrow(/unsupported field password|Raw credential/);
  });

  test('creates a draft only after request authorization', () => {
    const {service} = accessServices();
    expect(service.create(accessInput(), {security: accessSecurity()}))
      .toMatchObject({requestId: 'request-1', status: 'DRAFT'});
    expect(() => service.create(accessInput(), {security: accessSecurity({
      createAccessRequest: () => false})})).toThrow(/creation denied/);
  });

  test.each(['AUTO_APPROVED', 'PENDING_APPROVAL', 'DENIED'])(
    'submits through policy to %s', async (decision) => {
      const {service} = accessServices({decision});
      const request = service.create(accessInput(), {security: accessSecurity()});
      await expect(service.submit(request.requestId, {security: accessSecurity()}))
        .resolves.toMatchObject({status: decision, policyResult: {allowed: true}});
      expect(service.history(request.requestId, {security: accessSecurity()})
        .map((item) => item.status)).toEqual(['DRAFT', 'SUBMITTED', decision]);
    });

  test('requires approver permission and policy review for a decision', async () => {
    const {service} = accessServices();
    const request = service.create(accessInput(), {security: accessSecurity()});
    await service.submit(request.requestId, {security: accessSecurity()});
    await expect(service.decide(request.requestId, 'APPROVED', {
      actorRef: 'user:approver', note: 'Approved for reporting.',
      security: accessSecurity()})).resolves.toMatchObject({status: 'APPROVED'});
  });

  test('keeps provider grant planning separate from approval', async () => {
    const {service} = accessServices({decision: 'AUTO_APPROVED'});
    const draft = service.create(accessInput(), {security: accessSecurity()});
    const approved = await service.submit(draft.requestId,
      {security: accessSecurity()});
    expect(approved.providerGrantPlanRef).toBeNull();
    await expect(service.prepareGrant(draft.requestId,
      {security: accessSecurity()})).resolves.toMatchObject({
      request: {providerGrantPlanRef: 'grant-plan:1'},
      plan: {operations: [{kind: 'provider_grant'}]}});
  });

  test('runs grant application as a consequential TaskService task', async () => {
    const {service, tasks, provisioner} = accessServices({
      decision: 'AUTO_APPROVED'});
    const draft = service.create(accessInput(), {security: accessSecurity()});
    await service.submit(draft.requestId, {security: accessSecurity()});
    await service.prepareGrant(draft.requestId, {security: accessSecurity()});
    const task = service.provision(draft.requestId,
      {security: accessSecurity(), owner: 'user:approver'});
    expect(task.type).toBe(DISCOVERY_ACCESS_PROVISION_TASK);
    await tasks.wait(task.id);
    expect(provisioner.apply).toHaveBeenCalledTimes(1);
    expect(service.request(draft.requestId, {security: accessSecurity()}).status)
      .toBe('ACTIVE');
  });

  test('preserves approval evidence and marks failed provisioning accurately', async () => {
    const error = new Error('provider rejected grant');
    const {service, tasks} = accessServices({decision: 'AUTO_APPROVED',
      apply: jest.fn(async () => { throw error; })});
    const draft = service.create(accessInput(), {security: accessSecurity()});
    await service.submit(draft.requestId, {security: accessSecurity()});
    await service.prepareGrant(draft.requestId, {security: accessSecurity()});
    const task = service.provision(draft.requestId, {security: accessSecurity()});
    await expect(tasks.wait(task.id)).rejects.toThrow(/provider rejected grant/);
    const failed = service.request(draft.requestId, {security: accessSecurity()});
    expect(failed).toMatchObject({status: 'FAILED',
      policyResult: {allowed: true, provisioningFailure: {
        message: 'provider rejected grant'}}});
  });

  test('expires and revokes only active grants through their own authorities', async () => {
    const activate = async () => {
      const fixture = accessServices({decision: 'AUTO_APPROVED'});
      const draft = fixture.service.create(accessInput(), {security: accessSecurity()});
      await fixture.service.submit(draft.requestId, {security: accessSecurity()});
      await fixture.service.prepareGrant(draft.requestId, {security: accessSecurity()});
      const task = fixture.service.provision(draft.requestId,
        {security: accessSecurity()}); await fixture.tasks.wait(task.id);
      return {...fixture, requestId: draft.requestId};
    };
    const expiring = await activate();
    expect(expiring.service.expire(expiring.requestId,
      {security: accessSecurity()}).status).toBe('EXPIRED');
    const revoking = await activate();
    await expect(revoking.service.revoke(revoking.requestId, {
      reason: 'Project completed.', security: accessSecurity()}))
      .resolves.toMatchObject({status: 'REVOKED'});
    expect(revoking.provisioner.revoke).toHaveBeenCalledTimes(1);
  });
});

function curationSecurity(overrides={}) {
  return {createCurationItem: () => true, assignCurationItem: () => true,
    commentCurationItem: () => true, resolveCurationItem: () => true,
    createDuplicateCandidate: () => true, resolveDuplicateCandidate: () => true,
    resolveZeroResult: () => true, admitDocumentRef: () => true,
    viewCurationQueue: () => true, viewCurationEvidence: () => true,
    ...overrides};
}

function curationServices() {
  let id = 0; const store = new InMemoryDiscoveryCurationStore();
  const relationships = {record: jest.fn(async () => 'relationship:one')};
  return {store, relationships, service: new DiscoveryCurationService({store,
    mutationAuthority: {apply: async ({action, value}) => ({
      fieldOrRelationship: action, before: null, after: value,
      mutationRef: 'knowledge-revision:2'})}, relationshipAuthority: relationships,
    now: () => NOW, idFactory: (kind) => `${kind}-${++id}`})};
}

describe('Discovery curation and duplicate review', () => {
  test('includes every normative curation queue type', () => {
    expect(DISCOVERY_CURATION_TYPES).toHaveLength(10);
    expect(DISCOVERY_CURATION_TYPES).toContain('zero_result_term');
    expect(DISCOVERY_CURATION_TYPES).toContain('unresolved_access_surface');
  });

  test('opens, assigns and comments with revision history', () => {
    const {service, store} = curationServices();
    const item = service.open({type: 'missing_owner', targetRef: TARGET,
      summary: 'Owner is missing.', evidenceRefs: ['profile:one']},
    {security: curationSecurity()});
    service.assign(item.itemId, 'user:curator', {dueAt: NOW,
      security: curationSecurity()});
    service.comment(item.itemId, {actorRef: 'user:curator',
      text: 'Investigating ownership.', security: curationSecurity()});
    expect(store.revisions(item.itemId)).toHaveLength(3);
    expect(store.item(item.itemId)).toMatchObject({status: 'ASSIGNED',
      assigneeRef: 'user:curator', comments: [{text: 'Investigating ownership.'}]});
  });

  test('records the complete manual curation before/after evidence', async () => {
    const {service, store} = curationServices();
    const item = service.open({type: 'missing_description', targetRef: TARGET,
      summary: 'Description is missing.', evidenceRefs: ['profile:one']},
    {security: curationSecurity()});
    const result = await service.resolve(item.itemId, {action: 'description',
      value: 'Customer master.', actorRef: 'user:curator', reason: 'Owner supplied.',
      sourceEvidenceRefs: ['ticket:one'], security: curationSecurity()});
    expect(result).toMatchObject({item: {status: 'RESOLVED'}, evidence: {
      actorRef: 'user:curator', targetRef: TARGET,
      fieldOrRelationship: 'description', before: null,
      after: 'Customer master.', reason: 'Owner supplied.',
      sourceEvidenceRefs: ['ticket:one']}});
    expect(store.evidenceRecord(result.evidence.evidenceId)).toEqual(result.evidence);
    expect(service.evidence(result.evidence.evidenceId,
      {security: curationSecurity()})).toEqual(result.evidence);
  });

  test('dismissal also preserves reason and source evidence', () => {
    const {service} = curationServices();
    const item = service.open({type: 'broken_lineage', targetRef: TARGET,
      summary: 'Potential broken edge.', evidenceRefs: ['scan:one']},
    {security: curationSecurity()});
    expect(service.dismiss(item.itemId, {actorRef: 'user:curator',
      reason: 'Source intentionally removed.', sourceEvidenceRefs: ['ticket:two'],
      security: curationSecurity()})).toMatchObject({
      item: {status: 'DISMISSED'}, evidence: {after: 'DISMISSED'}});
  });

  test('automatically collapses candidates already sharing canonical identity', () => {
    const {service, store} = curationServices();
    expect(service.addDuplicateCandidate({leftRef: TARGET, rightRef: TARGET,
      signals: [{kind: 'scratchbird_uuid'}]}, {security: curationSecurity()}))
      .toEqual({alreadyCanonical: true, canonicalRef: TARGET, candidate: null});
    expect(store.duplicates.size).toBe(0);
  });

  test.each(DISCOVERY_DUPLICATE_DECISIONS)(
    'records %s without merging independent provider identities', async (decision) => {
      const {service, relationships} = curationServices();
      const created = service.addDuplicateCandidate({leftRef: TARGET,
        rightRef: 'cde-resource://mysql/local/app/table/customer',
        signals: [{kind: 'schema_fingerprint', score: 0.9}]},
      {security: curationSecurity()});
      expect(created.queueItem.type).toBe('duplicate_candidate');
      const result = await service.decideDuplicate(created.candidate.candidateId,
        {decision, actorRef: 'user:curator', reason: 'Reviewed evidence.',
          sourceEvidenceRefs: ['scan:one'], security: curationSecurity()});
      expect(result.identitiesMerged).toBe(false);
      expect(relationships.record).toHaveBeenCalledTimes(
        decision === 'not_duplicate' ? 0 : 1);
    });

  test('rejects duplicate decisions without explicit security permission', async () => {
    const {service} = curationServices();
    const created = service.addDuplicateCandidate({leftRef: TARGET,
      rightRef: 'asset:two', signals: [{kind: 'name'}]},
    {security: curationSecurity()});
    await expect(service.decideDuplicate(created.candidate.candidateId, {
      decision: 'equivalent_to', actorRef: 'user:curator', reason: 'Reason.',
      sourceEvidenceRefs: ['scan:one'], security: curationSecurity({
        resolveDuplicateCandidate: () => false})})).rejects.toThrow(/denied/);
  });

  test('resolves zero-result curation with action evidence only for visible targets',
    async () => {
      const {service} = curationServices();
      await expect(service.resolveZeroResult({query: 'custmer', frequency: 12,
        action: 'add_synonym', targetRef: TARGET,
        actorRef: 'user:curator', note: 'Verified intended synonym.',
        sourceEvidenceRefs: ['analytics:one']}, {security: curationSecurity()}))
        .resolves.toMatchObject({item: {type: 'zero_result_term', status: 'RESOLVED'},
          evidence: {fieldOrRelationship: 'add_synonym', after: TARGET}});
      await expect(service.resolveZeroResult({query: 'secret', frequency: 3,
        action: 'map_existing_term', targetRef: 'asset:hidden',
        actorRef: 'user:curator', note: 'Map term.',
        sourceEvidenceRefs: ['analytics:two']}, {security: curationSecurity({
        admitDocumentRef: () => false})})).rejects.toThrow(/not visible/);
    });

  test('filters and independently authorizes the curation queue', () => {
    const {service} = curationServices();
    service.open({type: 'missing_owner', targetRef: TARGET,
      summary: 'Owner missing.', evidenceRefs: ['scan:one']},
    {security: curationSecurity()});
    service.open({type: 'broken_lineage', targetRef: 'asset:two',
      summary: 'Lineage broken.', evidenceRefs: ['scan:two']},
    {security: curationSecurity()});
    expect(service.queue({type: 'missing_owner', security: curationSecurity()}))
      .toHaveLength(1);
    expect(() => service.queue({security: curationSecurity({
      viewCurationQueue: () => false})})).toThrow(/access denied/);
  });

  test('curation evidence requires source evidence and rejects secrets', () => {
    expect(() => validateCurationEvidence({schemaVersion: 1, evidenceId: 'one',
      actorRef: 'user:one', time: NOW, targetRef: TARGET,
      fieldOrRelationship: 'owner', before: null, after: 'team:data',
      reason: 'Verified.', sourceEvidenceRefs: []})).toThrow(/source evidence/);
  });
});

function sourceConfig(overrides={}) {
  return {schemaVersion: 1, sourceId: 'firebird-metadata', name: 'Firebird',
    type: 'provider_metadata', scope: [TARGET], enabled: true,
    mode: 'FULL_RECONCILE', schedule: '0 2 * * *', mandatory: true,
    credentialRef: 'credential:firebird', respectProviderVisibility: true,
    version: '1', ...overrides};
}

function indexSecurity(overrides={}) {
  return {administerDiscoveryIndex: () => true,
    viewDiscoveryIndexHealth: () => true, ...overrides};
}

function indexPolicy(overrides={}) {
  return {schemaVersion: 1, policyId: 'default',
    sourceRefs: ['firebird-metadata'], backendRef: 'backend:managed',
    fieldWeights: {name: 3, description: 1.5},
    synonyms: {customer: ['client']}, stopWords: ['the'],
    embeddingProfileRef: null, rankingProfileRefs: ['balanced'],
    retentionDays: 90, usageWindows: ['7d', '30d'],
    curationWorkflowRef: 'workflow:default',
    securityTrimProfileRef: 'security:default', reconcileScope: [TARGET],
    version: '1', ...overrides};
}

function backendConfig(overrides={}) {
  return {schemaVersion: 1, backendId: 'managed', type: 'cdeadmin_managed',
    connectionRef: null, credentialRef: null,
    indexNamespace: 'cdeadmin_discovery', lexical: true, vector: false,
    facets: true, graph: false, version: '1', ...overrides};
}

function indexServices({publish=null, discover=null}={}) {
  const tasks = new TaskExecutionService({now: () => NOW});
  const registry = new DiscoveryIndexSourceRegistry();
  registry.register('firebird-metadata', {test: async () => ({version: '5.0.4'}),
    discover: discover ?? (async () => ({items: [{kind: 'resource'}],
      embeddings: {}, metrics: {added: 1, updated: 0, deleted: 0}}))});
  const index = {publish: publish ?? jest.fn(async () => ({published: true,
    approvalRequired: false, revision: 'index-1', documentCount: 1})),
  health: jest.fn(async () => ({ready: true, activeRevision: 'prior',
    documentCount: 0}))};
  let revision = 0;
  const service = new DiscoveryIndexAdministrationService({
    configurations: new InMemoryDiscoveryIndexConfigurationStore(),
    policies: new InMemoryDiscoveryIndexPolicyStore(), registry,
    indexService: index, tasks, now: () => NOW,
    revisionFactory: () => `index-${++revision}`});
  service.saveSource(sourceConfig(), {security: indexSecurity()});
  return {service, tasks, registry, index};
}

describe('Discovery index source administration', () => {
  test('covers every normative source type and full reconcile phase', () => {
    expect(DISCOVERY_INDEX_SOURCE_TYPES).toHaveLength(19);
    expect(DISCOVERY_RECONCILE_PHASES).toHaveLength(10);
    expect(DISCOVERY_RECONCILE_PHASES.at(-1)).toBe('retire_previous_revision');
  });

  test('requires visibility enforcement for provider sources', () => {
    expect(() => validateIndexSourceConfiguration(sourceConfig({
      respectProviderVisibility: false}))).toThrow(/must respect/);
    expect(validateIndexSourceConfiguration(sourceConfig()).credentialRef)
      .toBe('credential:firebird');
  });

  test('versions configuration with optimistic conflict detection', () => {
    const store = new InMemoryDiscoveryIndexConfigurationStore();
    expect(store.save(sourceConfig())).toMatchObject({revision: 1});
    expect(() => store.save(sourceConfig({version: '2'}), 0)).toThrow(/conflict/);
    expect(store.save(sourceConfig({version: '2'}), 1)).toMatchObject({revision: 2});
    expect(store.revisions('firebird-metadata')).toHaveLength(2);
  });

  test('versions every index administration control as policy/backend data', () => {
    const store = new InMemoryDiscoveryIndexPolicyStore();
    expect(validateDiscoveryIndexPolicy(indexPolicy())).toMatchObject({
      fieldWeights: {name: 3}, synonyms: {customer: ['client']},
      securityTrimProfileRef: 'security:default'});
    expect(validateDiscoveryIndexBackendConfiguration(backendConfig()))
      .toMatchObject({lexical: true, facets: true, vector: false});
    expect(store.save('policy', indexPolicy())).toMatchObject({revision: 1,
      configurationKind: 'policy'});
    expect(store.save('backend', backendConfig())).toMatchObject({revision: 1,
      configurationKind: 'backend'});
  });

  test('saves administration policy only through its command permission', () => {
    const {service} = indexServices();
    expect(service.saveAdministrationConfiguration('policy', indexPolicy(),
      {security: indexSecurity()})).toMatchObject({revision: 1});
    expect(() => service.saveAdministrationConfiguration('backend',
      backendConfig(), {security: indexSecurity({
        administerDiscoveryIndex: () => false})})).toThrow(/denied/);
  });

  test('rejects backends that cannot provide required lexical/facet features', () => {
    expect(() => validateDiscoveryIndexBackendConfiguration(backendConfig({
      lexical: false}))).toThrow(/requires lexical/);
    expect(() => validateDiscoveryIndexBackendConfiguration(backendConfig({
      type: 'external_search_adapter'}))).toThrow(/connection reference/);
  });

  test('does not advertise an unavailable source adapter', () => {
    const registry = new DiscoveryIndexSourceRegistry();
    expect(registry.available()).toEqual([]);
    expect(() => registry.register('incomplete', {test: () => true}))
      .toThrow(/discover/);
  });

  test('tests a configured source and records complete health', async () => {
    const {service} = indexServices();
    await expect(service.testSource('firebird-metadata',
      {security: indexSecurity()})).resolves.toMatchObject({
      available: true, version: '5.0.4'});
    expect(service.sourceStatus('firebird-metadata',
      {security: indexSecurity()}).health).toMatchObject({state: 'IDLE',
      lastSuccessfulAt: NOW, errors: 0, permissionFailures: 0, staleCount: 0});
  });

  test('runs reconcile as a TaskService task and atomically publishes', async () => {
    const {service, tasks, index} = indexServices();
    const task = service.refresh('firebird-metadata', {security: indexSecurity()});
    await expect(tasks.wait(task.id)).resolves.toMatchObject({state: 'ACTIVE'});
    expect(index.publish).toHaveBeenCalledWith(expect.objectContaining({
      revision: 'index-1', mandatorySource: true, deletionApproval: false}),
    {authorization: expect.any(Object)});
    expect(service.sourceStatus('firebird-metadata',
      {security: indexSecurity()}).health).toMatchObject({state: 'IDLE',
      documentsAdded: 1, staleCount: 0});
  });

  test('pauses on deletion safety approval without switching health green', async () => {
    const {service, tasks} = indexServices({publish: jest.fn(async () => ({
      published: false, approvalRequired: true, activeRevision: 'prior'}))});
    const task = service.refresh('firebird-metadata', {security: indexSecurity()});
    await expect(tasks.wait(task.id)).resolves.toMatchObject({
      state: 'awaiting_deletion_approval'});
    expect(service.sourceStatus('firebird-metadata',
      {security: indexSecurity()}).health.state).toBe('PAUSED');
  });

  test('records failure while the prior index remains the index authority', async () => {
    const {service, tasks, index} = indexServices({publish:
      jest.fn(async () => { throw new Error('candidate validation failed'); })});
    const task = service.refresh('firebird-metadata', {security: indexSecurity()});
    await expect(tasks.wait(task.id)).rejects.toThrow(/candidate validation failed/);
    expect(index.health).toHaveBeenCalled();
    expect(service.sourceStatus('firebird-metadata',
      {security: indexSecurity()}).health.state).toBe('FAILED');
  });

  test('global health cannot be green while a mandatory source is unknown', async () => {
    const {service} = indexServices();
    await expect(service.globalHealth({security: indexSecurity()})).resolves
      .toMatchObject({state: 'degraded', mandatoryUnknown: true});
  });

  test('separates configure, refresh and health permissions', async () => {
    const {service} = indexServices();
    expect(() => service.refresh('firebird-metadata', {security: indexSecurity({
      administerDiscoveryIndex: () => false})})).toThrow(/refresh denied/);
    await expect(service.globalHealth({security: indexSecurity({
      viewDiscoveryIndexHealth: () => false})})).rejects.toThrow(/access denied/);
  });

  test('validates every health counter and source state', () => {
    expect(() => validateIndexSourceHealth({sourceId: 'one', state: 'GREEN'}))
      .toThrow(/state is invalid/);
    expect(() => validateIndexSourceHealth({sourceId: 'one', state: 'IDLE',
      errors: -1})).toThrow(/errors/);
  });
});
