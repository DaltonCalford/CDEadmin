import {createAIContent} from 'sources/cdeadmin_ui/modules/ai/contracts';

export const resourceRef = Object.freeze({schema: 'cdeadmin.resource-ref.v1',
  canonical: 'firebird://localhost/cdeadmin_demo.fdb', providerId: 'firebird'});
export const assetRef = (id) => ({schema: 'cdeadmin.asset-ref.v1', projectId: 'project-one', assetId: id});
export const evidence = (overrides={}) => ({schema: 'cdeadmin.ai-evidence.v1', id: 'metadata-one',
  kind: 'live_metadata', classification: 'verified_live_metadata', sourceRef: resourceRef,
  claimIds: ['claim-one'], summary: 'Observed table metadata', revision: '42',
  contentDigest: 'sha256:metadata', nativeDetails: {}, ...overrides});
export const action = (overrides={}) => ({schema: 'cdeadmin.ai-proposed-action.v1', id: 'action-one',
  type: 'proposed_command', commandId: 'demo.update', taskType: null, assetChangeRef: null,
  targetRef: resourceRef, arguments: {table: 'ASSETS'}, rationale: 'Apply the reviewed change',
  effects: ['updates metadata'], permissions: ['db.write'], rollbackNote: 'Restore the prior definition',
  dependencies: [], validation: {valid: true}, evidenceRefs: [resourceRef], diff: {after: 'draft'},
  extensions: [], ...overrides});
export const plan = (overrides={}) => ({schema: 'cdeadmin.ai-action-plan.v1', id: 'plan-one',
  name: 'Reviewed metadata update', revision: 1, targetRevision: '42', contextScopeIds: ['database'],
  actions: [action()], validation: {valid: true}, rollbackNotes: 'Restore prior metadata',
  evidenceRefs: [resourceRef], description: 'A reviewed command plan', extensions: [], ...overrides});
export function aiDefinition(overrides={}) {
  return createAIContent({name: 'Database assistant', description: 'Governed database help',
    sessionPolicy: {id: 'database-policy', allowedModes: ['ask', 'explain', 'draft', 'plan', 'review',
      'execute_with_confirmation'], defaultMode: 'ask', allowedReadToolIds: ['metadata.describe'],
    allowedProposalCommandIds: ['demo.update'], maximumPlanSteps: 10, requireEvidence: true,
    nativeDetails: {}}, savedContextRefs: [{schema: 'cdeadmin.ai-context-scope.v1', id: 'database',
      name: 'Demo database metadata', reference: resourceRef, type: 'database_metadata',
      environment: 'development', sensitivity: 'internal', exposure: 'metadata_only', timeRange: {},
      description: 'Explicit metadata scope', nativeDetails: {}}], savedPlans: [],
    modelProfileRef: assetRef('model-profile-one'), conversationPersistencePolicy: {
      persistMessages: false, storePrompts: false, storeResponses: false, retentionDays: null,
      nativeDetails: {}}, extensions: [], ...overrides});
}
export function modelProfile(overrides={}) {
  return {profileId: 'local-model', backendType: 'local', modelIdentifier: 'test-model',
    providerId: 'test-ai', endpointRef: null, credentialRef: null, contextLimit: 8192,
    dataPolicy: {metadata: true}, toolPolicy: {readOnlyDefault: true}, retentionPolicy: null,
    approvedForSensitiveData: false, ...overrides};
}
export function output(overrides={}) {
  return {schema: 'cdeadmin.ai-output.v1', id: 'output-one', type: 'explanation',
    text: 'The ASSETS table was verified from live metadata.', claims: [{id: 'claim-one',
      text: 'The ASSETS table exists.', classification: 'verified_live_metadata',
      evidenceIds: ['metadata-one']}], evidence: [evidence()], plan: null, diff: {}, uncertainty: '',
    nativeDetails: {}, ...overrides};
}
export function providerResult(value, overrides={}) {
  return {supportState: 'supported_native', providerVersion: 'test-ai-1', evidence: {observed: true},
    warnings: [], nativeDetails: {}, readCapabilities: ['metadata.describe'],
    writeCapabilities: ['proposal'], discoveryCapabilities: ['model.profile'],
    nativeMechanisms: ['local-test-adapter'], versionConstraints: [], limitations: [],
    runtimeEvidence: {observed: true}, value, ...overrides};
}
export function adapter(calls=[]) {
  const record = (name, value) => async (input) => { calls.push({name, input});
    return providerResult(typeof value === 'function' ? await value(input) : value); };
  return {registerReadTool: record('registerReadTool', {registered: ['metadata.describe']}),
    registerProposalTool: record('registerProposalTool', {registered: ['demo.update']}),
    sanitizeContext: record('sanitizeContext', ({request}) => request),
    generateResponse: record('generateResponse', output()),
    validateProposedAction: record('validateProposedAction', {valid: true}),
    executeApprovedAction: record('executeApprovedAction', async ({execute}) => execute())};
}
export function user(overrides={}) {
  return {id: 'ai-user', permissions: ['ai.use', 'ai.use_sensitive_metadata', 'ai.use_sensitive_data',
    'ai.propose', 'ai.execute_approved', 'ai.admin', 'db.write'], ...overrides};
}
