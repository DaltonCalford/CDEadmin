/////////////////////////////////////////////////////////////
// Strict Discovery form-to-command canonicalization boundary.
/////////////////////////////////////////////////////////////

import {immutable, noRawSecrets, plainObject, platformValue} from
  '../../platform/serviceUtils';

function reference(value, label, nullable=false) {
  if(value && typeof value === 'object') {
    value = value.canonicalRef ?? value.canonical ?? value.id ?? value.value;
  }
  if(nullable && (value === null || value === undefined || value === '')) {
    return null;
  }
  return platformValue(value, label, 4096);
}

function references(value, label) {
  if(!Array.isArray(value)) throw new TypeError(`${label} must be an array.`);
  return value.map((item) => reference(item, label));
}

function evidenceReferences(value, defaults, label) {
  if(value === null || value === undefined || value === '') {
    return references(defaults ?? [], label);
  }
  return references(Array.isArray(value) ? value : [value], label);
}

function requiredDefault(defaults, field, formId) {
  const value = defaults[field];
  if(value === null || value === undefined || value === '') throw new Error(
    `${formId} requires explicit ${field} command context.`
  );
  return value;
}

function canonicalContent(defaults, formId, field='content') {
  const value = requiredDefault(defaults, field, formId);
  plainObject(value, `${formId} canonical ${field}`);
  return {...value};
}

function formDefaults(context, formId, commandId) {
  const all = context.discoveryFormCommandDefaults ?? {};
  const value = all[formId] ?? all[commandId] ?? all;
  plainObject(value, `${formId} command defaults`);
  noRawSecrets(value, `${formId} command defaults`);
  return value;
}

function assetArguments(defaults, formId, content) {
  return {projectId: requiredDefault(defaults, 'projectId', formId), content,
    assetId: defaults.assetId, expectedVersion: defaults.expectedVersion ?? 0};
}

function searchQuery(formId, values, defaults) {
  const query = {...(defaults.query ?? {})};
  if(formId === 'discovery.quick_search') {
    query.text = values.text; query.mode = values.mode;
    query.rankingProfile = values.ranking ? reference(values.ranking,
      'Discovery ranking profile') : null;
    if(values.scope?.length) {
      const field = requiredDefault(defaults, 'scopeQueryField', formId);
      if(!['entityClasses', 'providers', 'domains', 'owners', 'tags',
        'nativeKinds', 'environments'].includes(field)) throw new TypeError(
        'Discovery quick-search scope field is invalid.'
      );
      query[field] = references(values.scope, 'Discovery search scope');
    }
  } else {
    if(values.builder && typeof values.builder === 'object' &&
        !Array.isArray(values.builder)) Object.assign(query, values.builder);
    else if(typeof values.builder === 'string') query.text = values.builder;
    query.mode = query.mode ?? 'ADVANCED_FACETED';
    query.entityClasses = references(values.entity_classes ?? [],
      'Discovery entity class');
    query.providers = references(values.providers ?? [], 'Discovery provider');
    query.domains = references(values.domains ?? [], 'Discovery domain');
    query.accessState = references(values.access ?? [], 'Discovery access state');
    query.rankingProfile = values.ranking ? reference(values.ranking,
      'Discovery ranking profile') : null;
    query.sort = values.sort;
  }
  return query;
}

function savedSearch(formId, values, defaults) {
  const content = canonicalContent(defaults, formId);
  if(formId === 'discovery.saved_search') return {...content,
    name: values.name, scope: values.scope, description: values.description};
  return {...content, query: searchQuery(formId, values, defaults),
    rankingProfileRef: values.ranking ? reference(values.ranking,
      'Discovery ranking profile') : null,
    searchScope: {...(content.searchScope ?? {}),
      selectedRefs: references(values.scope ?? [], 'Discovery search scope')}};
}

function businessTerm(values, defaults, formId) {
  const selectedTargets = [...references(values.resources ?? [],
    'Discovery term resource'), ...references(values.assets ?? [],
    'Discovery term asset'), ...references(values.metrics ?? [],
    'Discovery term metric')];
  const mappingsByTarget = {...canonicalContent(defaults, formId).mappings
    .reduce((result, item) => ({...result, [item.targetRef]: item}), {}),
  ...(defaults.mappingsByTarget ?? {})};
  const mappings = selectedTargets.map((targetRef) => {
    const mapping = mappingsByTarget[targetRef];
    if(!mapping) throw new Error(
      `${formId} requires explicit mapping context for ${targetRef}.`
    );
    return mapping;
  });
  return {...canonicalContent(defaults, formId), name: values.name,
    definition: values.definition, status: values.status,
    domainRef: reference(values.domain, 'Discovery term domain', true),
    synonyms: [...(values.synonyms ?? [])], acronyms: [...(values.acronyms ?? [])],
    mappings,
    ownerRefs: references(values.owner ?? [], 'Discovery term owner')};
}

function dataProduct(values, defaults, formId) {
  return {...canonicalContent(defaults, formId), name: values.name,
    summary: values.summary, domainRef: reference(values.domain,
      'Discovery product domain'), version: values.version, status: values.status,
    resourceRefs: references(values.resources ?? [], 'Discovery product resource'),
    assetRefs: references(values.assets ?? [], 'Discovery product asset'),
    metricRefs: references(values.metrics ?? [], 'Discovery product metric'),
    semanticModelRefs: references(values.semantic_models ?? [],
      'Discovery product semantic model'),
    dashboardRefs: references(values.dashboards ?? [],
      'Discovery product dashboard'),
    apiRefs: references(values.apis ?? [], 'Discovery product API'),
    ownerRefs: references(values.owners ?? [], 'Discovery product owner'),
    stewardRefs: references(values.stewards ?? [], 'Discovery product steward'),
    contractRefs: references(values.contracts ?? [], 'Discovery product contract'),
    qualityRefs: references(values.quality ?? [], 'Discovery product quality rule'),
    accessPolicyRef: reference(values.access_policy,
      'Discovery product access policy', true)};
}

function metric(values, defaults, formId) {
  return {...canonicalContent(defaults, formId), name: values.name,
    description: values.description, businessFormula: values.formula,
    aggregation: values.aggregation, grain: values.grain,
    dimensionRefs: references(values.dimensions ?? [], 'Discovery metric dimension'),
    filters: [...(values.filters ?? [])], semanticModelRef: reference(
      values.semantic_model, 'Discovery metric semantic model', true),
    implementationRefs: references(values.implementations ?? [],
      'Discovery metric implementation'), ownerRefs: references(
      values.owner ?? [], 'Discovery metric owner'), domainRef: reference(
      values.domain, 'Discovery metric domain', true)};
}

function certificationRequest(values, defaults, formId) {
  const input = canonicalContent(defaults, formId, 'input');
  return {input: {...input, targetRef: reference(values.target,
    'Discovery certification target'), reviewerRefs: references(
    values.reviewers ?? input.reviewerRefs ?? [], 'Discovery certification reviewer'),
  notes: values.notes}, profile: requiredDefault(defaults, 'profile', formId)};
}

function rankingProfile(values, defaults, formId) {
  return {...canonicalContent(defaults, formId), weights: {
    lexical: values.lexical, semantic: values.semantic, business: values.business,
    trust: values.trust, quality: values.quality, freshness: values.freshness,
    usage: values.usage, context: values.context, lineage: values.lineage,
    documentation: values.documentation}, boosts: {
    exact_visible_qualified_name: values.exact_name,
    exact_business_term: values.exact_term,
    active_certified_data_product: values.cert_product}, penalties: {
    deprecated: values.deprecated, retired_archived: values.retired,
    critical_quality_failure: values.critical_quality,
    freshness_sla_breach: values.freshness_breach,
    no_owner_steward: values.no_owner,
    unstable_breaking_schema: values.unstable_schema}};
}

export function secureDiscoveryFormCommandArguments(args={}) {
  if(!args.formId || !args.values || !args.persistedValues) return args;
  const safeValues = {...args.persistedValues};
  for(const fieldId of new Set(args.secretFieldIds ?? [])) {
    const value = args.values[fieldId];
    if(value !== null && value !== undefined && value !== '') {
      safeValues[`${fieldId}Ref`] = platformValue(value,
        `${fieldId} credential reference`, 2048);
    }
  }
  return Object.freeze({formId: args.formId, values: Object.freeze(safeValues),
    persistedValues: Object.freeze({...args.persistedValues})});
}

export function adaptDiscoveryFormCommandArguments(commandId, submission,
  context={}) {
  if(!submission?.formId || !submission.values) return submission;
  const formId = platformValue(submission.formId, 'Discovery form ID');
  const values = submission.values;
  plainObject(values, `${formId} values`); noRawSecrets(values,
    `${formId} values`);
  const defaults = formDefaults(context, formId, commandId);
  let result;
  switch(formId) {
  case 'discovery.quick_search': case 'discovery.advanced_search':
    result = commandId === 'discovery.search.save' ? assetArguments(defaults,
      formId, savedSearch(formId, values, defaults)) : {
      input: searchQuery(formId, values, defaults), profile: defaults.profile};
    break;
  case 'discovery.saved_search': result = assetArguments(defaults, formId,
    savedSearch(formId, values, defaults)); break;
  case 'discovery.business_term': result = assetArguments(defaults, formId,
    businessTerm(values, defaults, formId)); break;
  case 'discovery.data_product':
    result = commandId === 'discovery.product.update' ? assetArguments(defaults,
      formId, dataProduct(values, defaults, formId)) : {
      input: requiredDefault(defaults, 'certificationInput', formId),
      profile: requiredDefault(defaults, 'profile', formId)}; break;
  case 'discovery.metric':
    result = commandId === 'discovery.metric.update' ? assetArguments(defaults,
      formId, metric(values, defaults, formId)) : {
      input: requiredDefault(defaults, 'certificationInput', formId),
      profile: requiredDefault(defaults, 'profile', formId)}; break;
  case 'discovery.certification_request': result = certificationRequest(values,
    defaults, formId); break;
  case 'discovery.certification_review': result = {
    requestId: requiredDefault(defaults, 'requestId', formId),
    review: {state: values.decision, conditions: values.conditions,
      expiresAt: values.expires, notes: values.notes},
    profile: requiredDefault(defaults, 'profile', formId)}; break;
  case 'discovery.access_request': result = {input: {
    requester: requiredDefault(defaults, 'requester', formId),
    targetRef: reference(values.target, 'Discovery access target'),
    requestedAccess: references(values.access ?? [], 'Discovery requested access'),
    environment: values.environment, reason: values.reason,
    duration: values.duration, projectRef: reference(values.project,
      'Discovery access project', true)}}; break;
  case 'discovery.access_approval': result = {
    requestId: requiredDefault(defaults, 'requestId', formId),
    actorRef: requiredDefault(defaults, 'actorRef', formId), note: values.note,
    expiration: values.expiration}; break;
  case 'discovery.index_source': {
    const content = {...canonicalContent(defaults, formId), name: values.name,
      type: values.type, scope: references(values.scope ?? [],
        'Discovery index source scope'), enabled: values.enabled,
      mode: values.mode, schedule: values.schedule || null,
      mandatory: values.mandatory,
      credentialRef: values.credentialRef ?? defaults.content.credentialRef ?? null,
      respectProviderVisibility: values.respect_provider_visibility};
    result = commandId === 'discovery.index.refresh' ? {sourceId: content.sourceId} :
      {...assetArguments(defaults, formId, content),
        expectedConfigurationRevision:
          defaults.expectedConfigurationRevision ?? 0}; break;
  }
  case 'discovery.ranking_profile': {
    const profile = rankingProfile(values, defaults, formId);
    result = commandId === 'discovery.ranking.test' ? {profile,
      candidates: defaults.candidates ?? [], query: defaults.query ?? {}} :
      assetArguments(defaults, formId, profile); break;
  }
  case 'discovery.synonyms': {
    const content = canonicalContent(defaults, formId);
    result = {input: {...content, synonyms: values.entries},
      expectedRevision: defaults.expectedRevision ?? 0}; break;
  }
  case 'discovery.curation_resolution': result = {
    itemId: reference(values.item, 'Discovery curation item'),
    action: values.action, value: values.value,
    actorRef: requiredDefault(defaults, 'actorRef', formId),
    reason: defaults.reason ?? values.evidence,
    sourceEvidenceRefs: evidenceReferences(values.evidence,
      defaults.sourceEvidenceRefs, 'Discovery curation evidence')}; break;
  case 'discovery.enrichment_review': result = {
    suggestionId: requiredDefault(defaults, 'suggestionId', formId),
    proposedValue: values.proposed,
    actorRef: requiredDefault(defaults, 'actorRef', formId),
    reason: defaults.reason ?? `Reviewed ${values.field}`}; break;
  case 'discovery.duplicate_review': result = {
    candidateId: requiredDefault(defaults, 'candidateId', formId),
    decision: values.relationship,
    actorRef: requiredDefault(defaults, 'actorRef', formId),
    reason: defaults.reason ?? 'Reviewed duplicate candidate.',
    sourceEvidenceRefs: evidenceReferences(values.evidence,
      defaults.sourceEvidenceRefs, 'Discovery duplicate evidence')}; break;
  case 'discovery.visibility_test': result = {input: {
    principalRef: reference(values.principal, 'Discovery visibility principal'),
    query: typeof values.query === 'object' ? values.query : {
      text: values.query, mode: 'QUICK'},
    profile: values.ranking && typeof values.ranking === 'object' ?
      values.ranking : requiredDefault(defaults, 'profile', formId)}}; break;
  case 'discovery.zero_result_resolution': result = {zeroResult: {
    query: values.query, frequency: values.frequency, action: values.resolution,
    targetRef: reference(values.target, 'Discovery zero-result target', true),
    actorRef: requiredDefault(defaults, 'actorRef', formId), note: values.note,
    sourceEvidenceRefs: references(defaults.sourceEvidenceRefs ?? [],
      'Discovery zero-result evidence')}}; break;
  case 'discovery.search_feedback': result = {input: {
    actorScope: requiredDefault(defaults, 'actorScope', formId),
    resultRef: reference(values.result, 'Discovery feedback result'),
    type: values.type, note: values.note,
    queryRef: defaults.queryRef ?? null}}; break;
  default: throw new Error(`No command adapter is defined for ${formId}.`);
  }
  noRawSecrets(result, `${formId} command arguments`);
  return immutable(result);
}
