/////////////////////////////////////////////////////////////
// Governed Discovery business, product, trust and profile contracts.
/////////////////////////////////////////////////////////////

import {immutable, noRawSecrets, plainObject, platformValue} from
  '../../platform/serviceUtils';

export const DISCOVERY_KNOWLEDGE_KINDS = Object.freeze([
  'BusinessTerm', 'Domain', 'MetricDefinition', 'DataProduct',
  'CertificationProfile',
]);
export const DISCOVERY_CERTIFICATION_STATES = Object.freeze([
  'UNREVIEWED', 'IN_REVIEW', 'CERTIFIED', 'CERTIFIED_WITH_CONDITIONS',
  'REJECTED', 'EXPIRED', 'REVOKED',
]);
export const DISCOVERY_PROFILE_METHODS = Object.freeze([
  'exact', 'estimated', 'sampled', 'provider_reported', 'unknown',
]);

const DEFINITION = Object.freeze({
  BusinessTerm: {fields: ['schemaVersion', 'termId', 'name', 'definition',
    'status', 'domainRef', 'synonyms', 'acronyms', 'mappings', 'ownerRefs',
    'version'], required: ['schemaVersion', 'termId', 'name', 'definition',
    'status', 'synonyms', 'acronyms', 'mappings', 'ownerRefs', 'version']},
  Domain: {fields: ['schemaVersion', 'domainId', 'name', 'description',
    'parentRef', 'ownerRefs', 'stewardRefs', 'status', 'version'],
  required: ['schemaVersion', 'domainId', 'name', 'description', 'ownerRefs',
    'stewardRefs', 'status', 'version']},
  MetricDefinition: {fields: ['schemaVersion', 'metricId', 'name', 'description',
    'businessFormula', 'aggregation', 'grain', 'dimensionRefs', 'filters',
    'timeSemantics', 'semanticModelRef', 'implementationRefs', 'ownerRefs',
    'stewardRefs', 'certification', 'domainRef', 'version'],
  required: ['schemaVersion', 'metricId', 'name', 'description',
    'businessFormula', 'aggregation', 'grain', 'dimensionRefs', 'filters',
    'implementationRefs', 'ownerRefs', 'stewardRefs', 'certification', 'version']},
  DataProduct: {fields: ['schemaVersion', 'productId', 'name', 'summary',
    'domainRef', 'ownerRefs', 'stewardRefs', 'status', 'version', 'resourceRefs',
    'assetRefs', 'semanticModelRefs', 'metricRefs', 'dashboardRefs', 'apiRefs',
    'contractRefs', 'qualityRefs', 'lineageRefs', 'usageDocs', 'accessPolicyRef',
    'slaSummary', 'certification', 'releaseNotes'], required: ['schemaVersion',
    'productId', 'name', 'domainRef', 'status', 'version', 'resourceRefs',
    'assetRefs', 'ownerRefs', 'certification']},
  CertificationProfile: {fields: ['schemaVersion', 'profileId', 'name',
    'targetTypes', 'expirationDays', 'criteria', 'requireOwner',
    'requireContract', 'requireQuality', 'status', 'version'],
  required: ['schemaVersion', 'profileId', 'name', 'targetTypes',
    'expirationDays', 'criteria', 'requireOwner', 'requireContract',
    'requireQuality', 'status', 'version']},
});

const REF_ARRAY_FIELDS = new Set(['ownerRefs', 'stewardRefs', 'dimensionRefs',
  'implementationRefs', 'resourceRefs', 'assetRefs', 'semanticModelRefs',
  'metricRefs', 'dashboardRefs', 'apiRefs', 'contractRefs', 'qualityRefs',
  'lineageRefs', 'targetTypes']);

function shape(input, definition, label) {
  plainObject(input, label); noRawSecrets(input, label);
  const unknown = Object.keys(input).find((field) =>
    !definition.fields.includes(field));
  if(unknown) throw new TypeError(`${label} contains unsupported field ${unknown}.`);
  const missing = definition.required.find((field) => !Object.hasOwn(input, field));
  if(missing) throw new TypeError(`${label} requires ${missing}.`);
  if(input.schemaVersion !== 1) throw new TypeError(`${label} schema is invalid.`);
}

function text(value, label, maximum=4096, nullable=false) {
  if(nullable && (value === null || value === undefined)) return null;
  return platformValue(value, label, maximum);
}

function content(value, label, maximum) {
  const result = String(value ?? '').trim();
  const hasControl = [...result].some((character) => character.charCodeAt(0) < 32);
  if(result.length > maximum || hasControl) throw new TypeError(`${label} is invalid.`);
  return result;
}

function stringArray(value, label, {required=false}={}) {
  if(!Array.isArray(value)) throw new TypeError(`${label} must be an array.`);
  const result = value.map((item) => text(item, `${label} value`, 4096));
  if(new Set(result).size !== result.length) throw new TypeError(
    `${label} contains duplicate values.`
  );
  if(required && !result.length) throw new TypeError(`${label} cannot be empty.`);
  return result;
}

function object(value, label) {
  plainObject(value, label); noRawSecrets(value, label); return immutable({...value});
}

function date(value, label, nullable=false) {
  if(nullable && (value === null || value === undefined)) return null;
  value = text(value, label, 64);
  if(Number.isNaN(Date.parse(value))) throw new TypeError(`${label} is invalid.`);
  return value;
}

function boolean(value, label) {
  if(typeof value !== 'boolean') throw new TypeError(`${label} must be boolean.`);
  return value;
}

export function validateBusinessMapping(input) {
  const fields = ['targetRef', 'relationship', 'confidence', 'origin',
    'validFrom', 'validTo', 'approvedBy', 'approvalState'];
  shape({...input, schemaVersion: 1}, {fields: [...fields, 'schemaVersion'],
    required: [...fields, 'schemaVersion']}, 'Business knowledge mapping');
  if(!Number.isFinite(input.confidence) || input.confidence < 0 ||
      input.confidence > 1) throw new TypeError(
    'Business mapping confidence must be between 0 and 1.'
  );
  if(!['SUGGESTED', 'APPROVED', 'REJECTED'].includes(input.approvalState)) {
    throw new TypeError('Business mapping approval state is invalid.');
  }
  const approvedBy = text(input.approvedBy, 'Business mapping approver', 4096, true);
  if(input.approvalState === 'APPROVED' && !approvedBy) throw new TypeError(
    'Approved business mapping requires a human approver.'
  );
  const validFrom = date(input.validFrom, 'Business mapping valid-from', true);
  const validTo = date(input.validTo, 'Business mapping valid-to', true);
  if(validFrom && validTo && Date.parse(validFrom) > Date.parse(validTo)) {
    throw new TypeError('Business mapping validity range is reversed.');
  }
  return immutable({targetRef: text(input.targetRef, 'Business mapping target', 4096),
    relationship: text(input.relationship, 'Business mapping relationship', 256),
    confidence: input.confidence, origin: text(input.origin,
      'Business mapping origin', 256), validFrom, validTo, approvedBy,
    approvalState: input.approvalState});
}

function validateCertificationSummary(value, label) {
  plainObject(value, label); noRawSecrets(value, label);
  if(value.state !== undefined && !DISCOVERY_CERTIFICATION_STATES.includes(
    value.state)) throw new TypeError(`${label} state is invalid.`);
  return immutable({...value});
}

function validateBusinessTerm(input) {
  shape(input, DEFINITION.BusinessTerm, 'BusinessTerm');
  if(!['DRAFT', 'REVIEW', 'APPROVED', 'DEPRECATED', 'RETIRED'].includes(
    input.status)) throw new TypeError('BusinessTerm status is invalid.');
  const mappings = input.mappings.map(validateBusinessMapping);
  const keys = mappings.map((item) => `${item.targetRef}\0${item.relationship}`);
  if(new Set(keys).size !== keys.length) throw new TypeError(
    'BusinessTerm contains a duplicate target relationship.'
  );
  return immutable({schemaVersion: 1, termId: text(input.termId,
    'BusinessTerm ID'), name: text(input.name, 'BusinessTerm name', 160),
  definition: text(input.definition, 'BusinessTerm definition', 10000),
  status: input.status, domainRef: text(input.domainRef, 'BusinessTerm domain',
    4096, true), synonyms: stringArray(input.synonyms, 'BusinessTerm synonyms'),
  acronyms: stringArray(input.acronyms, 'BusinessTerm acronyms'), mappings,
  ownerRefs: stringArray(input.ownerRefs, 'BusinessTerm owners'),
  version: text(input.version, 'BusinessTerm version', 128)});
}

function validateDomain(input) {
  shape(input, DEFINITION.Domain, 'Domain');
  if(!['draft', 'active', 'deprecated', 'retired'].includes(input.status)) {
    throw new TypeError('Domain status is invalid.');
  }
  return immutable({schemaVersion: 1, domainId: text(input.domainId, 'Domain ID'),
    name: text(input.name, 'Domain name', 160), description: text(input.description,
      'Domain description', 4000), parentRef: text(input.parentRef,
      'Domain parent', 4096, true), ownerRefs: stringArray(input.ownerRefs,
      'Domain owners', {required: true}), stewardRefs: stringArray(input.stewardRefs,
      'Domain stewards'), status: input.status,
    version: text(input.version, 'Domain version', 128)});
}

function validateMetric(input) {
  shape(input, DEFINITION.MetricDefinition, 'MetricDefinition');
  if(!['sum', 'count', 'distinct_count', 'average', 'min', 'max', 'ratio',
    'custom'].includes(input.aggregation)) throw new TypeError(
    'MetricDefinition aggregation is invalid.'
  );
  if(!Array.isArray(input.filters)) throw new TypeError(
    'MetricDefinition filters must be an array.'
  );
  const filters = input.filters.map((item) => object(item,
    'MetricDefinition filter'));
  return immutable({schemaVersion: 1, metricId: text(input.metricId,
    'MetricDefinition ID'), name: text(input.name, 'MetricDefinition name', 160),
  description: text(input.description, 'MetricDefinition description', 4000),
  businessFormula: text(input.businessFormula, 'MetricDefinition formula', 10000),
  aggregation: input.aggregation, grain: text(input.grain,
    'MetricDefinition grain', 1024), dimensionRefs: stringArray(input.dimensionRefs,
    'MetricDefinition dimensions'), filters,
  timeSemantics: text(input.timeSemantics, 'MetricDefinition time semantics',
    4096, true), semanticModelRef: text(input.semanticModelRef,
    'MetricDefinition semantic model', 4096, true), implementationRefs:
    stringArray(input.implementationRefs, 'MetricDefinition implementations'),
  ownerRefs: stringArray(input.ownerRefs, 'MetricDefinition owners', {required: true}),
  stewardRefs: stringArray(input.stewardRefs, 'MetricDefinition stewards'),
  certification: validateCertificationSummary(input.certification,
    'MetricDefinition certification'), domainRef: text(input.domainRef,
    'MetricDefinition domain', 4096, true), version: text(input.version,
    'MetricDefinition version', 128)});
}

function validateDataProduct(input) {
  shape(input, DEFINITION.DataProduct, 'DataProduct');
  if(!['DRAFT', 'REVIEW', 'CERTIFIED', 'DEPRECATED', 'RETIRED'].includes(
    input.status)) throw new TypeError('DataProduct status is invalid.');
  const result = {schemaVersion: 1, productId: text(input.productId,
    'DataProduct ID'), name: text(input.name, 'DataProduct name', 160),
  summary: content(input.summary, 'DataProduct summary', 4000),
  domainRef: text(input.domainRef, 'DataProduct domain'),
  ownerRefs: stringArray(input.ownerRefs, 'DataProduct owners'),
  stewardRefs: stringArray(input.stewardRefs ?? [], 'DataProduct stewards'),
  status: input.status, version: text(input.version, 'DataProduct version', 128),
  usageDocs: content(input.usageDocs, 'DataProduct usage documentation', 65536),
  accessPolicyRef: text(input.accessPolicyRef, 'DataProduct access policy', 4096,
    true), slaSummary: object(input.slaSummary ?? {}, 'DataProduct SLA summary'),
  certification: validateCertificationSummary(input.certification,
    'DataProduct certification'), releaseNotes: content(input.releaseNotes,
    'DataProduct release notes', 65536)};
  for(const field of DEFINITION.DataProduct.fields.filter((field) =>
    REF_ARRAY_FIELDS.has(field))) result[field] = stringArray(input[field] ?? [],
    `DataProduct ${field}`);
  if(['REVIEW', 'CERTIFIED'].includes(result.status) && !result.ownerRefs.length) {
    throw new TypeError('Reviewed DataProduct requires an owner.');
  }
  if(result.status === 'CERTIFIED' && result.certification.state !== 'CERTIFIED' &&
      result.certification.state !== 'CERTIFIED_WITH_CONDITIONS') throw new TypeError(
    'DataProduct cannot claim CERTIFIED without a current certification record.'
  );
  return immutable(result);
}

function validateCertificationProfile(input) {
  shape(input, DEFINITION.CertificationProfile, 'CertificationProfile');
  if(!['draft', 'published', 'retired'].includes(input.status)) throw new TypeError(
    'CertificationProfile status is invalid.'
  );
  if(!Number.isInteger(input.expirationDays) || input.expirationDays < 1) {
    throw new TypeError('CertificationProfile expiration days is invalid.');
  }
  if(!Array.isArray(input.criteria) || !input.criteria.length) throw new TypeError(
    'CertificationProfile criteria cannot be empty.'
  );
  const criteria = input.criteria.map((item) => {
    const fields = ['criterionId', 'required', 'source', 'evidenceRule'];
    shape({...item, schemaVersion: 1}, {fields: [...fields, 'schemaVersion'],
      required: [...fields, 'schemaVersion']}, 'Certification criterion');
    return immutable({criterionId: text(item.criterionId,
      'Certification criterion ID'), required: boolean(item.required,
      'Certification criterion required'), source: text(item.source,
      'Certification criterion source'), evidenceRule: text(item.evidenceRule,
      'Certification criterion evidence rule', 4096)});
  });
  if(new Set(criteria.map((item) => item.criterionId)).size !== criteria.length) {
    throw new TypeError('CertificationProfile criterion IDs must be unique.');
  }
  return immutable({schemaVersion: 1, profileId: text(input.profileId,
    'CertificationProfile ID'), name: text(input.name,
    'CertificationProfile name', 120), targetTypes: stringArray(input.targetTypes,
    'CertificationProfile target types', {required: true}),
  expirationDays: input.expirationDays, criteria,
  requireOwner: boolean(input.requireOwner, 'CertificationProfile require-owner'),
  requireContract: boolean(input.requireContract,
    'CertificationProfile require-contract'), requireQuality: boolean(
    input.requireQuality, 'CertificationProfile require-quality'),
  status: input.status, version: text(input.version,
    'CertificationProfile version', 128)});
}

export function validateDiscoveryKnowledgeAsset(kind, input) {
  if(!DISCOVERY_KNOWLEDGE_KINDS.includes(kind)) throw new TypeError(
    `Unknown Discovery knowledge asset kind ${kind}.`
  );
  return ({BusinessTerm: validateBusinessTerm, Domain: validateDomain,
    MetricDefinition: validateMetric, DataProduct: validateDataProduct,
    CertificationProfile: validateCertificationProfile})[kind](input);
}

export function discoveryKnowledgeIdentity(kind, input) {
  const value = validateDiscoveryKnowledgeAsset(kind, input);
  return value[{BusinessTerm: 'termId', Domain: 'domainId',
    MetricDefinition: 'metricId', DataProduct: 'productId',
    CertificationProfile: 'profileId'}[kind]];
}

export function validateCertificationRecord(input) {
  const fields = ['schemaVersion', 'certificationId', 'targetRef',
    'targetRevision', 'profileRef', 'state', 'reviewerRefs', 'criteriaEvidence',
    'reviewedAt', 'expiresAt', 'conditions', 'notes', 'supersedesRef'];
  shape(input, {fields, required: fields}, 'Certification record');
  if(!DISCOVERY_CERTIFICATION_STATES.includes(input.state)) throw new TypeError(
    'Certification record state is invalid.'
  );
  if(!Array.isArray(input.criteriaEvidence)) throw new TypeError(
    'Certification criteria evidence must be an array.'
  );
  const criteriaEvidence = input.criteriaEvidence.map((item) =>
    object(item, 'Certification criterion evidence'));
  const reviewedAt = date(input.reviewedAt, 'Certification review time', true);
  const expiresAt = date(input.expiresAt, 'Certification expiration time', true);
  if(reviewedAt && expiresAt && Date.parse(reviewedAt) >= Date.parse(expiresAt)) {
    throw new TypeError('Certification expiration must follow its review time.');
  }
  if(input.state === 'CERTIFIED_WITH_CONDITIONS' && !String(
    input.conditions ?? '').trim()) throw new TypeError(
    'Conditional certification requires conditions.'
  );
  return immutable({schemaVersion: 1, certificationId: text(
    input.certificationId, 'Certification ID'), targetRef: text(input.targetRef,
    'Certification target', 4096), targetRevision: text(input.targetRevision,
    'Certification target revision'), profileRef: text(input.profileRef,
    'Certification profile reference', 4096), state: input.state,
  reviewerRefs: stringArray(input.reviewerRefs, 'Certification reviewers'),
  criteriaEvidence, reviewedAt, expiresAt, conditions: content(input.conditions,
    'Certification conditions', 10000), notes: content(input.notes,
    'Certification notes', 10000), supersedesRef: text(input.supersedesRef,
    'Superseded certification reference', 4096, true)});
}

export function validateProfileStatistic(input) {
  const fields = ['metric', 'value', 'method', 'sampleSize', 'capturedAt',
    'resourceRevision', 'classification'];
  shape({...input, schemaVersion: 1}, {fields: [...fields, 'schemaVersion'],
    required: [...fields, 'schemaVersion']}, 'Discovery profile statistic');
  if(!DISCOVERY_PROFILE_METHODS.includes(input.method)) throw new TypeError(
    'Discovery profile statistic method is invalid.'
  );
  if(!['string', 'number', 'boolean'].includes(typeof input.value) ||
      (typeof input.value === 'number' && !Number.isFinite(input.value))) {
    throw new TypeError('Discovery profile statistic value must be scalar.');
  }
  if(input.sampleSize !== null && (!Number.isInteger(input.sampleSize) ||
      input.sampleSize < 0)) throw new TypeError(
    'Discovery profile statistic sample size is invalid.'
  );
  return immutable({metric: text(input.metric, 'Discovery profile metric'),
    value: input.value, method: input.method, sampleSize: input.sampleSize,
    capturedAt: date(input.capturedAt, 'Discovery profile capture time'),
    resourceRevision: text(input.resourceRevision,
      'Discovery profile resource revision', 4096, true),
    classification: text(input.classification,
      'Discovery profile classification', 256)});
}
