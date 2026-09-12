/////////////////////////////////////////////////////////////
// CDEadmin AI Interface project-asset persistence authority.
/////////////////////////////////////////////////////////////

import {immutable, noRawSecrets, plainObject, platformValue} from '../../platform/serviceUtils';
import {AI_INTERFACE_MODULE_ID} from '../../specifications/ai_discovery_zero_grey';
import {AI_ASSET_KINDS, AI_ASSET_TYPES, aiAssetIdentity,
  validateAIAsset} from './AIAssetContracts';

export const AI_ASSET_AUTHORITY_SERVICE_ID = 'cdeadmin.ai_interface.assets';
const KIND_BY_TYPE = Object.freeze(Object.fromEntries(Object.entries(AI_ASSET_TYPES)
  .map(([kind, type]) => [type, kind])));

function filePart(value) { return String(value).replace(/[^a-zA-Z0-9._:-]/g, '-'); }
function dependencies(kind, content) { if(kind === 'AIAgentProfile') return [content.modelProfileRef,
  ...content.connectorRefs, content.toolPolicyRef, content.dataPolicyRef, content.approvalPolicyRef,
  content.budgetPolicyRef, content.retentionPolicyRef, content.instructionAssetRef].filter(Boolean);
if(kind === 'AIConnectorProfile') return [content.policyRef];
if(kind === 'AIDataPolicy') return content.allowedModelProfileRefs;
if(kind === 'AIPlan') return [content.agentProfileRef, ...content.steps.flatMap((item) => item.assetRefs)];
return []; }
function resources(kind, content) { if(kind === 'AIConnectorProfile') return content.resourceScopeRefs;
  if(kind === 'AIDataPolicy') return [...content.queryPolicy.allowedResourceRefs,
    ...content.queryPolicy.blockedResourceRefs]; if(kind === 'AIPlan') return content.steps.flatMap(
    (item) => item.resourceRefs); return []; }

export function aiInterfaceAssetRequest(kind, input, options={}) {
  const content = validateAIAsset(kind, input); const logicalId = aiAssetIdentity(kind, content);
  const type = AI_ASSET_TYPES[kind]; const expectedVersion = options.expectedVersion ?? 0;
  if(!Number.isInteger(expectedVersion) || expectedVersion < 0) throw new TypeError(
    'AI asset expected version is invalid.'); const name = options.name ?? content.displayName ??
    content.name ?? logicalId; const request = {asset_type: type, schema_name: type, schema_version: 1,
    name: platformValue(name, 'AI asset name', 256), path: platformValue(options.path ??
      `ai/${kind}/${filePart(logicalId)}.json`, 'AI asset path', 1024), expected_version: expectedVersion,
    content, metadata: {moduleId: AI_INTERFACE_MODULE_ID, assetKind: kind, logicalId},
    dependency_references: [...new Set(dependencies(kind, content))], resource_bindings: [...new Set(
      resources(kind, content))], source_control_eligible: true, editor_capable: true,
    viewer_capable: true, validation_state: 'valid', validation_details: []};
  noRawSecrets(request, 'AI asset request'); return immutable(request);
}

function validateStoredAsset(asset, expectedKind=null, {contentRequired=true}={}) {
  plainObject(asset, 'Stored AI asset');
  noRawSecrets(asset, 'Stored AI asset'); const kind = KIND_BY_TYPE[asset.asset_type];
  if(!kind || (expectedKind && kind !== expectedKind)) throw new TypeError(
    `Stored asset is not ${expectedKind ?? 'an AI Interface asset'}.`);
  if(asset.schema_name !== AI_ASSET_TYPES[kind] || asset.schema_version !== 1) throw new TypeError(
    'Stored AI asset schema is invalid.'); if(!contentRequired) return immutable({...asset, assetKind: kind});
  const content = validateAIAsset(kind, asset.content); return immutable({...asset, content, assetKind: kind}); }

export class AIAssetAuthority {
  constructor({projectAssets}={}) { if(!projectAssets || typeof projectAssets.saveAsset !== 'function' ||
      typeof projectAssets.asset !== 'function' || typeof projectAssets.revisions !== 'function' ||
      typeof projectAssets.deleteAsset !== 'function') throw new TypeError(
    'AI asset authority requires the Project Asset service.'); this.projectAssets = projectAssets; }
  async save(projectId, kind, input, options={}) { projectId = platformValue(projectId,
    'AI asset project ID', 256); const request = aiInterfaceAssetRequest(kind, input, options);
  const assetId = platformValue(options.assetId ?? `${kind}:${request.metadata.logicalId}`,
    'AI asset ID', 256); const saved = await this.projectAssets.saveAsset(projectId, assetId, request);
  return validateStoredAsset(saved, kind); }
  async get(projectId, assetId, {version=null, kind=null}={}) { const result = await this.projectAssets.asset(
    platformValue(projectId, 'AI asset project ID', 256), platformValue(assetId, 'AI asset ID', 256),
    version); return validateStoredAsset(result, kind); }
  async revisions(projectId, assetId, {kind=null}={}) { const values = await this.projectAssets.revisions(
    platformValue(projectId, 'AI asset project ID', 256), platformValue(assetId, 'AI asset ID', 256));
  if(!Array.isArray(values)) throw new TypeError('AI asset revisions response must be an array.');
  return values.map((item) => validateStoredAsset(item, kind, {contentRequired: false})); }
  async remove(projectId, assetId, expectedVersion) { if(!Number.isInteger(expectedVersion) ||
      expectedVersion < 1) throw new TypeError('AI asset delete version is invalid.'); return this.projectAssets
    .deleteAsset(platformValue(projectId, 'AI asset project ID', 256), platformValue(assetId,
      'AI asset ID', 256), expectedVersion); }
  kindForType(type) { return KIND_BY_TYPE[type] ?? null; }
  kinds() { return [...AI_ASSET_KINDS]; }
}
