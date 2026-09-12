import {
  createMLVectorContent, mlVectorAssetRequest, serializeMLVectorContent,
} from 'sources/cdeadmin_ui/modules/ml_vector/contracts';
import {assetRef, mlVectorDefinition} from './MLVectorTestUtils';

const mutableDefinition = () => JSON.parse(JSON.stringify(mlVectorDefinition()));

describe('ML / Vector contracts', () => {
  test('creates deterministic complete assets with separate runtime identity', () => {
    const content = mlVectorDefinition(); expect(createMLVectorContent(content)).toEqual(content);
    expect(serializeMLVectorContent(content)).toBe(serializeMLVectorContent({...content}));
    expect(mlVectorAssetRequest({content, expectedVersion: 3})).toMatchObject({
      asset_type: 'cdeadmin.ml_vector.v1', expected_version: 3,
      resource_bindings: [{canonical: expect.stringContaining('documents')}]});
  });
  test('retains normalized and provider-native metric identity', () => {
    const index = mlVectorDefinition().vectorDesigns[0].indexes[0];
    expect(index).toMatchObject({normalizedMetric: 'cosine', nativeMetric: 'COSINE'});
  });
  test('rejects unknown fields, raw credentials and invented metrics', () => {
    expect(() => createMLVectorContent({...mlVectorDefinition(), guessed: true})).toThrow(
      'unsupported field guessed');
    expect(() => createMLVectorContent({...mlVectorDefinition(), extensions: [{id: 'x-one',
      name: 'x-one', value: {accessToken: 'forbidden'}}]})).toThrow('Raw credential');
    const content = mutableDefinition(); content.vectorDesigns[0].indexes[0].normalizedMetric = 'angular';
    expect(() => createMLVectorContent(content)).toThrow('metric');
  });
  test('requires provider-custom metric identity and valid index dimensions', () => {
    const content = mutableDefinition(); const index = content.vectorDesigns[0].indexes[0];
    index.normalizedMetric = 'provider_custom'; index.nativeMetric = null;
    expect(() => createMLVectorContent(content)).toThrow('provider-native');
    index.nativeMetric = 'SPECIAL'; index.dimensions = 0;
    expect(() => createMLVectorContent(content)).toThrow('dimensions');
  });
  test('keeps model versions immutable and aliases/tags separate', () => {
    const model = mlVectorDefinition().modelEntries[0]; expect(Object.isFrozen(model.versions[0])).toBe(true);
    expect(model.aliases[0]).toMatchObject({name: 'production', versionId: 'minilm-v1'});
    expect(model.versionTags[0]).toMatchObject({versionId: 'minilm-v1', tags: ['approved']});
  });
  test('requires ground truth and dataset revision for quality metrics', () => {
    const content = mutableDefinition(); content.evaluationCases[0].groundTruthRef = null;
    expect(() => createMLVectorContent(content)).toThrow('ground truth');
  });
  test('rejects dangling model, index and alias references', () => {
    let content = mutableDefinition(); content.deploymentBindings[0].modelVersionId = 'missing';
    expect(() => createMLVectorContent(content)).toThrow('unknown model version');
    content = mutableDefinition(); content.evaluationCases[0].indexId = 'missing';
    expect(() => createMLVectorContent(content)).toThrow('unknown vector index');
    content = mutableDefinition(); content.modelEntries[0].aliases[0].versionId = 'missing';
    expect(() => createMLVectorContent(content)).toThrow('Unknown aliased');
  });
  test('allows references but never raw external-model credentials', () => {
    const content = mutableDefinition(); content.modelEntries[0].versions[0].artifactRef = assetRef('new-model');
    expect(createMLVectorContent(content).modelEntries[0].versions[0].artifactRef).toEqual(assetRef('new-model'));
    content.embeddingPipelines[0].modelRef.credentialRef = {schema: 'cdeadmin.credential-ref.v1',
      id: 'model-key', providerId: 'vault', scope: 'model', displayName: 'Model key', password: 'bad'};
    expect(() => createMLVectorContent(content)).toThrow('Raw credential');
  });
});
