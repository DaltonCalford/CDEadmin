/////////////////////////////////////////////////////////////
// Project-backed Discovery ranking and index-source assets.
/////////////////////////////////////////////////////////////

import {immutable, noRawSecrets, plainObject, platformValue} from
  '../../platform/serviceUtils';
import {validateIndexSourceConfiguration} from
  './DiscoveryGovernanceContracts';
import {validateDiscoveryRankingProfile} from './DiscoveryRanking';

export const DISCOVERY_ADMINISTRATION_ASSET_TYPES = Object.freeze({
  RankingProfile: 'cdeadmin.discovery.ranking_profile',
  IndexSourceConfig: 'cdeadmin.discovery.index_source_config',
});

const KIND_BY_TYPE = Object.freeze(Object.fromEntries(Object.entries(
  DISCOVERY_ADMINISTRATION_ASSET_TYPES).map(([kind, type]) => [type, kind])));

function requireMethod(value, method, label) {
  if(typeof value?.[method] !== 'function') throw new TypeError(
    `${label} requires ${method}.`
  );
}

function validate(kind, input) {
  if(kind === 'RankingProfile') return validateDiscoveryRankingProfile(input);
  if(kind === 'IndexSourceConfig') return validateIndexSourceConfiguration(input);
  throw new TypeError(`Unknown Discovery administration asset kind ${kind}.`);
}

function identity(kind, content) {
  return kind === 'RankingProfile' ? content.profileId : content.sourceId;
}

function filePart(value) {
  return String(value).replace(/[^a-zA-Z0-9._:-]/g, '-');
}

export function discoveryAdministrationAssetRequest(kind, input, options={}) {
  const content = validate(kind, input);
  const expectedVersion = options.expectedVersion ?? 0;
  if(!Number.isInteger(expectedVersion) || expectedVersion < 0) throw new TypeError(
    'Discovery administration asset expected version is invalid.'
  );
  const type = DISCOVERY_ADMINISTRATION_ASSET_TYPES[kind];
  const logicalId = identity(kind, content);
  const request = {asset_type: type, schema_name: type, schema_version: 1,
    name: platformValue(options.name ?? content.name,
      'Discovery administration asset name', 256),
    path: platformValue(options.path ??
      `discovery/${kind}/${filePart(logicalId)}.json`,
    'Discovery administration asset path', 1024), expected_version: expectedVersion,
    content, metadata: {moduleId: 'cdeadmin.discovery_intelligence',
      assetKind: kind, logicalId}, dependency_references: [],
    resource_bindings: kind === 'IndexSourceConfig' &&
      Array.isArray(content.scope) ? [...content.scope] : [],
    source_control_eligible: true, editor_capable: true, viewer_capable: true,
    validation_state: 'valid', validation_details: []};
  noRawSecrets(request, 'Discovery administration asset request');
  return immutable(request);
}

function storedAsset(asset, expectedKind=null, {contentRequired=true}={}) {
  plainObject(asset, 'Stored Discovery administration asset');
  noRawSecrets(asset, 'Stored Discovery administration asset');
  const kind = KIND_BY_TYPE[asset.asset_type];
  if(!kind || (expectedKind && kind !== expectedKind) ||
      asset.schema_name !== DISCOVERY_ADMINISTRATION_ASSET_TYPES[kind] ||
      asset.schema_version !== 1) throw new TypeError(
    `Stored asset is not ${expectedKind ?? 'a Discovery administration asset'}.`
  );
  return immutable({...asset, assetKind: kind,
    ...(contentRequired ? {content: validate(kind, asset.content)} : {})});
}

export class DiscoveryAdministrationAssetAuthority {
  constructor({projectAssets}={}) {
    for(const method of ['saveAsset', 'asset', 'revisions', 'deleteAsset']) {
      requireMethod(projectAssets, method,
        'Discovery administration Project Asset service');
    }
    this.projectAssets = projectAssets;
  }

  async save(projectId, kind, input, options={}) {
    projectId = platformValue(projectId,
      'Discovery administration project ID');
    const request = discoveryAdministrationAssetRequest(kind, input, options);
    const assetId = platformValue(options.assetId ??
      `${kind}:${request.metadata.logicalId}`,
    'Discovery administration asset ID');
    return storedAsset(await this.projectAssets.saveAsset(projectId, assetId,
      request), kind);
  }

  async get(projectId, assetId, {version=null, kind=null}={}) {
    return storedAsset(await this.projectAssets.asset(platformValue(projectId,
      'Discovery administration project ID'), platformValue(assetId,
      'Discovery administration asset ID'), version), kind);
  }

  async revisions(projectId, assetId, {kind=null}={}) {
    const values = await this.projectAssets.revisions(platformValue(projectId,
      'Discovery administration project ID'), platformValue(assetId,
      'Discovery administration asset ID'));
    if(!Array.isArray(values)) throw new TypeError(
      'Discovery administration revisions response must be an array.'
    );
    return immutable(values.map((item) => storedAsset(item, kind,
      {contentRequired: false})));
  }

  remove(projectId, assetId, expectedVersion) {
    if(!Number.isInteger(expectedVersion) || expectedVersion < 1) throw new TypeError(
      'Discovery administration asset delete version is invalid.'
    );
    return this.projectAssets.deleteAsset(platformValue(projectId,
      'Discovery administration project ID'), platformValue(assetId,
      'Discovery administration asset ID'), expectedVersion);
  }
}

export class DiscoveryRankingProfileService {
  constructor({assets}={}) {
    for(const method of ['save', 'get', 'revisions', 'remove']) requireMethod(
      assets, method, 'Discovery ranking profile asset authority');
    this.assets = assets; this.profiles = new Map();
  }

  register(input) {
    const profile = validateDiscoveryRankingProfile(input);
    this.profiles.set(profile.profileId, profile); return profile;
  }

  async save(projectId, input, options={}) {
    const saved = await this.assets.save(projectId, 'RankingProfile', input,
      options);
    this.register(saved.content); return saved;
  }

  async load(projectId, assetId, options={}) {
    const asset = await this.assets.get(projectId, assetId,
      {...options, kind: 'RankingProfile'});
    this.register(asset.content); return asset;
  }

  get(profileId) {
    return this.profiles.get(platformValue(profileId,
      'Discovery ranking profile ID')) ?? null;
  }

  resolve(profileId) {
    const profile = this.get(profileId);
    return profile?.status === 'published' ? profile : null;
  }
}

export class DiscoveryIndexSourceAssetService {
  constructor({assets, administration}={}) {
    requireMethod(assets, 'save', 'Discovery index-source asset authority');
    requireMethod(assets, 'get', 'Discovery index-source asset authority');
    requireMethod(administration, 'saveSource',
      'Discovery index-source administration');
    this.assets = assets; this.administration = administration;
  }

  async save(projectId, input, {assetId, expectedVersion=0,
    expectedConfigurationRevision=0, security}={}) {
    const content = validateIndexSourceConfiguration(input);
    requireMethod(security, 'administerDiscoveryIndex',
      'Discovery index administration security');
    if(security.administerDiscoveryIndex(content.sourceId, 'configure') !== true) {
      throw new Error('Discovery index source configuration denied.');
    }
    const saved = await this.assets.save(projectId, 'IndexSourceConfig', content,
      {assetId, expectedVersion});
    try {
      const configuration = this.administration.saveSource(content,
        {expectedRevision: expectedConfigurationRevision, security});
      return immutable({asset: saved, configuration});
    } catch(error) {
      error.persistedAsset = saved;
      throw error;
    }
  }

  async load(projectId, assetId, {expectedConfigurationRevision=0,
    security}={}) {
    const asset = await this.assets.get(projectId, assetId,
      {kind: 'IndexSourceConfig'});
    return immutable({asset, configuration: this.administration.saveSource(
      asset.content, {expectedRevision: expectedConfigurationRevision, security})});
  }
}
