/////////////////////////////////////////////////////////////
// Discovery ranking/index-source project asset authorities.
/////////////////////////////////////////////////////////////

import {BALANCED_DISCOVERY_RANKING_PROFILE,
  DiscoveryAdministrationAssetAuthority,
  DiscoveryIndexSourceAssetService, DiscoveryRankingProfileService,
  discoveryAdministrationAssetRequest} from
  'sources/cdeadmin_ui/modules/discovery_intelligence';

class ProjectAssets {
  constructor() { this.current = new Map(); this.history = new Map(); }
  async saveAsset(projectId, assetId, request) {
    const key = `${projectId}/${assetId}`; const prior = this.current.get(key);
    if((prior?.version ?? 0) !== request.expected_version) {
      throw new Error('project asset conflict');
    }
    const value = {...request, project_id: projectId, asset_id: assetId,
      version: (prior?.version ?? 0) + 1};
    this.current.set(key, value); this.history.set(key,
      [...(this.history.get(key) ?? []), value]); return value;
  }
  async asset(projectId, assetId) {
    return this.current.get(`${projectId}/${assetId}`);
  }
  async revisions(projectId, assetId) {
    return this.history.get(`${projectId}/${assetId}`) ?? [];
  }
  async deleteAsset(projectId, assetId, expectedVersion) {
    const key = `${projectId}/${assetId}`;
    if(this.current.get(key)?.version !== expectedVersion) {
      throw new Error('project asset conflict');
    }
    return this.current.delete(key);
  }
}

function source(overrides={}) {
  return {schemaVersion: 1, sourceId: 'firebird-local',
    name: 'Firebird local metadata', type: 'provider_metadata',
    scope: ['cde-resource://firebird/local/database/demo'], enabled: true,
    mode: 'MANUAL', schedule: null, mandatory: true,
    credentialRef: 'cde-secret://local/firebird-demo',
    respectProviderVisibility: true, version: '1', ...overrides};
}

describe('Discovery administration assets', () => {
  test('builds exact source and ranking project asset requests', () => {
    expect(discoveryAdministrationAssetRequest('IndexSourceConfig', source()))
      .toMatchObject({asset_type: 'cdeadmin.discovery.index_source_config',
        metadata: {assetKind: 'IndexSourceConfig', logicalId: 'firebird-local'},
        resource_bindings: [
          'cde-resource://firebird/local/database/demo']});
    expect(discoveryAdministrationAssetRequest('RankingProfile',
      BALANCED_DISCOVERY_RANKING_PROFILE)).toMatchObject({
      asset_type: 'cdeadmin.discovery.ranking_profile',
      metadata: {assetKind: 'RankingProfile', logicalId:
        'cdeadmin.discovery.ranking.balanced'}});
  });

  test('persists, reloads, revisions and removes both asset kinds', async () => {
    const authority = new DiscoveryAdministrationAssetAuthority({
      projectAssets: new ProjectAssets()});
    const ranking = new DiscoveryRankingProfileService({assets: authority});
    const savedProfile = await ranking.save('project-one',
      BALANCED_DISCOVERY_RANKING_PROFILE);
    expect(ranking.resolve(BALANCED_DISCOVERY_RANKING_PROFILE.profileId))
      .toEqual(BALANCED_DISCOVERY_RANKING_PROFILE);
    await authority.save('project-one', 'IndexSourceConfig', source());
    const updated = await authority.save('project-one', 'IndexSourceConfig',
      source({name: 'Updated Firebird metadata'}), {
        assetId: 'IndexSourceConfig:firebird-local', expectedVersion: 1});
    expect(updated).toMatchObject({version: 2,
      content: {name: 'Updated Firebird metadata'}});
    await expect(authority.revisions('project-one',
      'IndexSourceConfig:firebird-local', {kind: 'IndexSourceConfig'}))
      .resolves.toHaveLength(2);
    await expect(authority.remove('project-one', savedProfile.asset_id,
      savedProfile.version)).resolves.toBe(true);
  });

  test('rejects unknown kinds, conflicts and embedded credentials', async () => {
    const authority = new DiscoveryAdministrationAssetAuthority({
      projectAssets: new ProjectAssets()});
    expect(() => discoveryAdministrationAssetRequest('Unknown', source()))
      .toThrow(/Unknown Discovery administration asset/);
    expect(() => discoveryAdministrationAssetRequest('IndexSourceConfig',
      source({password: 'not-allowed'}))).toThrow();
    await authority.save('project-one', 'IndexSourceConfig', source());
    await expect(authority.save('project-one', 'IndexSourceConfig', source(),
      {assetId: 'IndexSourceConfig:firebird-local', expectedVersion: 0}))
      .rejects.toThrow('project asset conflict');
  });

  test('authorizes index configuration before persisting its project asset',
    async () => {
      const assets = {save: jest.fn(), get: jest.fn()};
      const administration = {saveSource: jest.fn()};
      const service = new DiscoveryIndexSourceAssetService({assets,
        administration});
      const security = {administerDiscoveryIndex: jest.fn(() => false)};
      await expect(service.save('project-one', source(), {security}))
        .rejects.toThrow('configuration denied');
      expect(security.administerDiscoveryIndex).toHaveBeenCalledWith(
        'firebird-local', 'configure');
      expect(assets.save).not.toHaveBeenCalled();
      expect(administration.saveSource).not.toHaveBeenCalled();
    });
});
