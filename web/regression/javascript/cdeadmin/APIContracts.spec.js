import {apiAssetRequest, createAPIContent} from 'sources/cdeadmin_ui/modules/api/contracts';
import {apiDefinition, resourceRef} from './APITestUtils';

describe('API Designer contracts', () => {
  test('creates deterministic canonical assets and preserves credential references only', () => {
    const content = apiDefinition(); expect(createAPIContent(content)).toEqual(content);
    expect(apiAssetRequest({content, expectedVersion: 2})).toMatchObject({asset_type: 'cdeadmin.api.v1',
      expected_version: 2, metadata: {profile: 'openapi_http'}});
  });
  test('rejects raw secrets and unknown fields recursively', () => {
    expect(() => createAPIContent({...apiDefinition(), guessed: true})).toThrow('unsupported field guessed');
    expect(() => createAPIContent({...apiDefinition(), info: {title: 'x', password: 'secret'}}))
      .toThrow('Raw credential');
  });
  test('rejects invalid binding and profile semantics', () => {
    expect(() => createAPIContent({...apiDefinition(), profile: 'pretend'})).toThrow('profile');
    expect(() => createAPIContent({...apiDefinition(), operations: [{...apiDefinition().operations[0],
      binding: {...apiDefinition().operations[0].binding, type: 'generic_sql', targetRef: resourceRef}}]}))
      .toThrow('binding type');
  });
});
