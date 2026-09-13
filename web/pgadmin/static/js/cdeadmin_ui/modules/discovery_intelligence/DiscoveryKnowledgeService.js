/////////////////////////////////////////////////////////////
// Project-backed business knowledge, metric and data-product authority.
/////////////////////////////////////////////////////////////

import {immutable, noRawSecrets, plainObject, platformValue} from
  '../../platform/serviceUtils';
import {discoveryKnowledgeIdentity, DISCOVERY_KNOWLEDGE_KINDS,
  validateCertificationRecord,
  validateDiscoveryKnowledgeAsset} from './DiscoveryKnowledgeContracts';
import {discoveryDocumentVisibility} from './DiscoveryDocument';

export const DISCOVERY_KNOWLEDGE_ASSET_TYPES = Object.freeze({
  BusinessTerm: 'cdeadmin.discovery.business_term',
  Domain: 'cdeadmin.discovery.domain',
  MetricDefinition: 'cdeadmin.discovery.metric_definition',
  DataProduct: 'cdeadmin.discovery.data_product',
  CertificationProfile: 'cdeadmin.discovery.certification_profile',
});
const KIND_BY_TYPE = Object.freeze(Object.fromEntries(Object.entries(
  DISCOVERY_KNOWLEDGE_ASSET_TYPES).map(([kind, type]) => [type, kind])));

function filePart(value) { return String(value).replace(/[^a-zA-Z0-9._:-]/g, '-'); }

function references(kind, content) {
  if(kind === 'BusinessTerm') return [content.domainRef,
    ...content.mappings.map((item) => item.targetRef)].filter(Boolean);
  if(kind === 'Domain') return [content.parentRef].filter(Boolean);
  if(kind === 'MetricDefinition') return [content.domainRef,
    content.semanticModelRef, ...content.dimensionRefs,
    ...content.implementationRefs].filter(Boolean);
  if(kind === 'DataProduct') return [content.domainRef, content.accessPolicyRef,
    ...content.assetRefs, ...content.semanticModelRefs, ...content.metricRefs,
    ...content.dashboardRefs, ...content.apiRefs, ...content.contractRefs,
    ...content.qualityRefs, ...content.lineageRefs].filter(Boolean);
  return [];
}

function resourceBindings(kind, content) {
  if(kind === 'BusinessTerm') return content.mappings.map((item) => item.targetRef)
    .filter((item) => item.startsWith('cde-resource://'));
  if(kind === 'MetricDefinition') return content.implementationRefs.filter(
    (item) => item.startsWith('cde-resource://'));
  if(kind === 'DataProduct') return content.resourceRefs;
  return [];
}

export function discoveryKnowledgeAssetRequest(kind, input, options={}) {
  const content = validateDiscoveryKnowledgeAsset(kind, input);
  const logicalId = discoveryKnowledgeIdentity(kind, content);
  const expectedVersion = options.expectedVersion ?? 0;
  if(!Number.isInteger(expectedVersion) || expectedVersion < 0) throw new TypeError(
    'Discovery asset expected version is invalid.'
  );
  const type = DISCOVERY_KNOWLEDGE_ASSET_TYPES[kind];
  const request = {asset_type: type, schema_name: type, schema_version: 1,
    name: platformValue(options.name ?? content.name, 'Discovery asset name', 256),
    path: platformValue(options.path ??
      `discovery/${kind}/${filePart(logicalId)}.json`,
    'Discovery asset path', 1024), expected_version: expectedVersion, content,
    metadata: {moduleId: 'cdeadmin.discovery_intelligence', assetKind: kind,
      logicalId}, dependency_references: [...new Set(references(kind, content))],
    resource_bindings: [...new Set(resourceBindings(kind, content))],
    source_control_eligible: true, editor_capable: true, viewer_capable: true,
    validation_state: 'valid', validation_details: []};
  noRawSecrets(request, 'Discovery asset request'); return immutable(request);
}

function storedAsset(asset, expectedKind=null, {contentRequired=true}={}) {
  plainObject(asset, 'Stored Discovery asset'); noRawSecrets(asset,
    'Stored Discovery asset');
  const kind = KIND_BY_TYPE[asset.asset_type];
  if(!kind || (expectedKind && kind !== expectedKind)) throw new TypeError(
    `Stored asset is not ${expectedKind ?? 'a Discovery knowledge asset'}.`
  );
  if(asset.schema_name !== DISCOVERY_KNOWLEDGE_ASSET_TYPES[kind] ||
      asset.schema_version !== 1) throw new TypeError(
    'Stored Discovery asset schema is invalid.'
  );
  if(!contentRequired) return immutable({...asset, assetKind: kind});
  return immutable({...asset, assetKind: kind,
    content: validateDiscoveryKnowledgeAsset(kind, asset.content)});
}

export class DiscoveryKnowledgeAssetAuthority {
  constructor({projectAssets}={}) {
    if(!projectAssets || typeof projectAssets.saveAsset !== 'function' ||
        typeof projectAssets.asset !== 'function' ||
        typeof projectAssets.revisions !== 'function' ||
        typeof projectAssets.deleteAsset !== 'function') throw new TypeError(
      'Discovery asset authority requires the Project Asset service.'
    );
    this.projectAssets = projectAssets;
  }

  async save(projectId, kind, input, options={}) {
    projectId = platformValue(projectId, 'Discovery asset project ID');
    const request = discoveryKnowledgeAssetRequest(kind, input, options);
    const assetId = platformValue(options.assetId ??
      `${kind}:${request.metadata.logicalId}`, 'Discovery asset ID');
    return storedAsset(await this.projectAssets.saveAsset(projectId, assetId,
      request), kind);
  }

  async get(projectId, assetId, {version=null, kind=null}={}) {
    return storedAsset(await this.projectAssets.asset(platformValue(projectId,
      'Discovery asset project ID'), platformValue(assetId,
      'Discovery asset ID'), version), kind);
  }

  async revisions(projectId, assetId, {kind=null}={}) {
    const items = await this.projectAssets.revisions(platformValue(projectId,
      'Discovery asset project ID'), platformValue(assetId,
      'Discovery asset ID'));
    if(!Array.isArray(items)) throw new TypeError(
      'Discovery asset revisions response must be an array.'
    );
    return items.map((item) => storedAsset(item, kind, {contentRequired: false}));
  }

  remove(projectId, assetId, expectedVersion) {
    if(!Number.isInteger(expectedVersion) || expectedVersion < 1) throw new TypeError(
      'Discovery asset delete version is invalid.'
    );
    return this.projectAssets.deleteAsset(platformValue(projectId,
      'Discovery asset project ID'), platformValue(assetId,
      'Discovery asset ID'), expectedVersion);
  }
}

function normalized(value) {
  return String(value ?? '').normalize('NFKC').toLocaleLowerCase().trim();
}

function mappingActive(mapping, now) {
  return mapping.approvalState === 'APPROVED' &&
    (!mapping.validFrom || Date.parse(mapping.validFrom) <= now) &&
    (!mapping.validTo || Date.parse(mapping.validTo) >= now);
}

export class BusinessKnowledgeService {
  constructor({assets, now=() => Date.now()}={}) {
    if(!assets || typeof assets.save !== 'function' ||
        typeof assets.get !== 'function') throw new TypeError(
      'Business knowledge requires the Discovery asset authority.'
    );
    this.assets = assets; this.now = now;
    this.byKind = new Map(DISCOVERY_KNOWLEDGE_KINDS.map((kind) =>
      [kind, new Map()]));
  }

  register(kind, input) {
    const content = validateDiscoveryKnowledgeAsset(kind, input);
    const id = discoveryKnowledgeIdentity(kind, content);
    if(kind === 'Domain') this._assertDomainAcyclic(id, content.parentRef);
    this.byKind.get(kind).set(id, content); return content;
  }

  async save(projectId, kind, input, options={}) {
    const content = validateDiscoveryKnowledgeAsset(kind, input);
    if(kind === 'Domain') this._assertDomainAcyclic(content.domainId,
      content.parentRef);
    const saved = await this.assets.save(projectId, kind, content, options);
    this.register(kind, saved.content); return saved;
  }

  async load(projectId, assetId, options={}) {
    const asset = await this.assets.get(projectId, assetId, options);
    this.register(asset.assetKind, asset.content); return asset;
  }

  get(kind, id) {
    if(!DISCOVERY_KNOWLEDGE_KINDS.includes(kind)) throw new TypeError(
      `Unknown Discovery knowledge asset kind ${kind}.`
    );
    return this.byKind.get(kind).get(platformValue(id,
      'Discovery knowledge identity')) ?? null;
  }

  list(kind) {
    if(!DISCOVERY_KNOWLEDGE_KINDS.includes(kind)) throw new TypeError(
      `Unknown Discovery knowledge asset kind ${kind}.`
    );
    return [...this.byKind.get(kind).values()].sort((left, right) =>
      discoveryKnowledgeIdentity(kind, left).localeCompare(
        discoveryKnowledgeIdentity(kind, right)));
  }

  search({query, parsed, security, limit=200}={}) {
    if(typeof security?.admitBusinessKnowledge !== 'function') throw new TypeError(
      'Business search requires explicit business-knowledge admission.'
    );
    if(!Number.isInteger(limit) || limit < 1 || limit > 200) throw new TypeError(
      'Business candidate limit must be an integer from 1 to 200.'
    );
    const target = normalized(parsed?.lexicalText ?? query?.text);
    if(!target) return immutable([]);
    const candidates = new Map();
    for(const term of this.byKind.get('BusinessTerm').values()) {
      if(!['APPROVED', 'DEPRECATED'].includes(term.status) ||
          security.admitBusinessKnowledge(term) !== true) continue;
      const name = normalized(term.name); const synonyms = term.synonyms.map(normalized);
      const acronyms = term.acronyms.map(normalized);
      const relevance = target === name ? 1 : acronyms.includes(target) ? 0.95 :
        synonyms.includes(target) ? 0.9 : name.includes(target) ? 0.75 :
          [...synonyms, ...acronyms].some((item) => item.includes(target)) ? 0.65 : 0;
      if(!relevance) continue;
      for(const mapping of term.mappings) if(mappingActive(mapping, this.now()) &&
          security.admitBusinessKnowledge(term, mapping) === true) {
        const score = relevance * mapping.confidence;
        const candidate = candidates.get(mapping.targetRef);
        const evidence = {termId: term.termId, termName: term.name,
          relationship: mapping.relationship, exact: relevance === 1,
          deprecatedTerm: term.status === 'DEPRECATED'};
        if(!candidate || candidate.score < score) candidates.set(mapping.targetRef,
          {canonicalRef: mapping.targetRef, score, evidence: [evidence]});
        else if(candidate.score === score) candidate.evidence.push(evidence);
      }
    }
    return immutable([...candidates.values()].sort((left, right) =>
      right.score - left.score || left.canonicalRef.localeCompare(
        right.canonicalRef)).slice(0, limit));
  }

  _assertDomainAcyclic(domainId, parentRef) {
    if(!parentRef) return;
    const seen = new Set([domainId]); let current = parentRef;
    while(current) {
      if(seen.has(current)) throw new TypeError(
        'Discovery Domain hierarchy cannot contain a cycle.'
      );
      seen.add(current); current = this.byKind.get('Domain').get(current)?.parentRef;
    }
  }
}

const PRODUCT_TRANSITIONS = Object.freeze({DRAFT: ['REVIEW', 'DEPRECATED'],
  REVIEW: ['DRAFT', 'CERTIFIED', 'DEPRECATED'],
  CERTIFIED: ['DEPRECATED'], DEPRECATED: ['REVIEW', 'RETIRED'], RETIRED: []});

export class DataProductService {
  constructor({knowledge, documentAuthority}={}) {
    if(!knowledge || typeof knowledge.save !== 'function' ||
        typeof knowledge.get !== 'function') throw new TypeError(
      'Data product service requires BusinessKnowledgeService.'
    );
    if(typeof documentAuthority?.resolve !== 'function') throw new TypeError(
      'Data product service requires a resource document authority.'
    );
    this.knowledge = knowledge; this.documentAuthority = documentAuthority;
  }

  async save(projectId, input, options={}) {
    const content = validateDiscoveryKnowledgeAsset('DataProduct', input);
    for(const reference of content.resourceRefs) {
      const document = await this.documentAuthority.resolve(reference);
      if(!document || !discoveryDocumentVisibility(document).dataProductCandidate) {
        throw new Error(
          `Resource ${reference} is not eligible for Data Product membership.`
        );
      }
    }
    return this.knowledge.save(projectId, 'DataProduct', content, options);
  }

  async setStatus(projectId, assetId, status, {expectedVersion,
    certification=null}={}) {
    const asset = await this.knowledge.assets.get(projectId, assetId,
      {kind: 'DataProduct'});
    const current = asset.content;
    if(!PRODUCT_TRANSITIONS[current.status]?.includes(status)) throw new TypeError(
      `DataProduct transition ${current.status} -> ${status} is not allowed.`
    );
    if(status === 'CERTIFIED' && !certification) throw new TypeError(
      'DataProduct certification requires a revision-bound certification record.'
    );
    if(status === 'CERTIFIED') {
      certification = validateCertificationRecord(certification);
      if(!['CERTIFIED', 'CERTIFIED_WITH_CONDITIONS'].includes(
        certification.state) || certification.targetRef !== assetId ||
          certification.targetRevision !== String(asset.version)) throw new TypeError(
        'DataProduct certification does not bind the current product revision.'
      );
    }
    return this.save(projectId, {...current, status,
      certification: certification ?? current.certification},
    {assetId, expectedVersion});
  }

  async changeMember(projectId, assetId, collection, reference, operation,
    expectedVersion) {
    const memberFields = ['resourceRefs', 'assetRefs', 'semanticModelRefs',
      'metricRefs', 'dashboardRefs', 'apiRefs', 'contractRefs', 'qualityRefs',
      'lineageRefs'];
    if(!memberFields.includes(collection)) throw new TypeError(
      'DataProduct member collection is invalid.'
    );
    if(!['add', 'remove'].includes(operation)) throw new TypeError(
      'DataProduct member operation is invalid.'
    );
    reference = platformValue(reference, 'DataProduct member reference', 4096);
    const asset = await this.knowledge.assets.get(projectId, assetId,
      {kind: 'DataProduct'});
    const members = new Set(asset.content[collection]);
    if(operation === 'add') members.add(reference); else members.delete(reference);
    return this.save(projectId, {...asset.content, [collection]: [...members]},
      {assetId, expectedVersion});
  }
}
