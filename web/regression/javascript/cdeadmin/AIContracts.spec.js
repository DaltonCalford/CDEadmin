import {
  AI_ASSET_SCHEMA, AI_ASSET_TYPE, AI_EVIDENCE_CLASSES, AI_MODES, AI_OUTPUT_TYPES,
  aiAssetRequest, aiReferenceKey, createAIContent, serializeAIContent, validateActionPlan,
  validateAIRef, validateContextScope, validateEvidence, validateProposedAction,
} from 'sources/cdeadmin_ui/modules/ai/contracts';
import {action, aiDefinition, assetRef, evidence, plan, resourceRef} from './AITestUtils';

describe('AI Assistant canonical contracts', () => {
  test('creates the exact deterministic canonical asset', () => {
    const value = aiDefinition(); expect(value).toMatchObject({schema: AI_ASSET_SCHEMA,
      schemaVersion: 1, moduleId: 'cdeadmin.ai'}); expect(Object.isFrozen(value)).toBe(true);
    expect(serializeAIContent(value)).toBe(serializeAIContent({...value,
      savedContextRefs: [...value.savedContextRefs].reverse()}));
  });
  test('exports exact enumerations', () => {
    expect(AI_MODES).toHaveLength(6); expect(AI_OUTPUT_TYPES).toHaveLength(9);
    expect(AI_EVIDENCE_CLASSES).toEqual(['verified_live_metadata', 'observed_runtime_evidence',
      'inferred', 'model_suggestion']);
  });
  test('preserves resource and project asset identity', () => {
    expect(aiReferenceKey(resourceRef)).toBe(`resource:${resourceRef.canonical}`);
    expect(aiReferenceKey(assetRef('one'))).toBe('asset:project-one/one');
  });
  test('rejects unsupported reference and context semantics', () => {
    expect(() => validateAIRef({schema: 'guess', id: 'one'})).toThrow('unsupported');
    expect(() => validateContextScope({...aiDefinition().savedContextRefs[0], exposure: 'implicit'}))
      .toThrow('exposure');
  });
  test('validates claim evidence classes and identifiers', () => {
    expect(validateEvidence(evidence())).toMatchObject({classification: 'verified_live_metadata'});
    expect(() => validateEvidence(evidence({classification: 'verified_by_model'}))).toThrow('classification');
  });
  test('validates typed proposed actions without generic execution blobs', () => {
    expect(validateProposedAction(action())).toMatchObject({commandId: 'demo.update'});
    expect(() => validateProposedAction({...action(), guessed: true})).toThrow('unsupported field guessed');
    expect(() => validateProposedAction({...action(), type: 'proposed_task', taskType: null})).toThrow('task type');
  });
  test('requires plans to reference known action dependencies', () => {
    expect(validateActionPlan(plan())).toMatchObject({revision: 1, targetRevision: '42'});
    expect(() => validateActionPlan(plan({actions: [action({dependencies: ['missing']})]})))
      .toThrow('unknown action');
  });
  test('requires plan context to exist in the same authored asset', () => {
    expect(() => createAIContent({...aiDefinition(), savedContextRefs: [] , savedPlans: [plan()]}))
      .toThrow('unknown context');
  });
  test('rejects raw secret fields recursively', () => {
    expect(() => createAIContent({...aiDefinition(), extensions: [{id: 'x', name: 'x-bad',
      value: {accessToken: 'raw'}}]})).toThrow('Raw credential');
  });
  test('builds a strict project asset request with references separated', () => {
    const request = aiAssetRequest({content: aiDefinition(), expectedVersion: 3});
    expect(request).toMatchObject({asset_type: AI_ASSET_TYPE, schema_name: AI_ASSET_TYPE,
      expected_version: 3, source_control_eligible: true});
    expect(request.dependency_references).toEqual([assetRef('model-profile-one')]);
    expect(request.resource_bindings).toEqual([resourceRef]);
  });
});
