/////////////////////////////////////////////////////////////
// Capability-accurate Discovery-to-analysis route planning.
/////////////////////////////////////////////////////////////

import {immutable, noRawSecrets, plainObject, platformValue} from
  '../../platform/serviceUtils';

export const DISCOVERY_ANALYSIS_CONNECTORS = Object.freeze([
  'auto', 'database_provider', 'scratchbird_sbsql',
  'scratchbird_compatibility',
]);

function exact(input, fields, label) {
  plainObject(input, label); noRawSecrets(input, label);
  const unknown = Object.keys(input).find((field) => !fields.includes(field));
  if(unknown) throw new TypeError(`${label} contains unsupported field ${unknown}.`);
  return input;
}

function requireMethod(value, method, label) {
  if(typeof value?.[method] !== 'function') throw new TypeError(
    `${label} requires ${method}.`
  );
}

function references(input) {
  if(!Array.isArray(input) || !input.length || input.length > 50) throw new TypeError(
    'Discovery analysis selection must contain 1 to 50 resource references.'
  );
  const result = input.map((reference) => platformValue(reference,
    'Discovery analysis resource reference', 4096));
  if(new Set(result).size !== result.length) throw new TypeError(
    'Discovery analysis selection contains duplicate resources.'
  );
  return result;
}

function refused(entities, reason, alternatives=[]) {
  return immutable({schema: 'cdeadmin.discovery-analysis-plan.v1',
    status: 'refused', executionAuthorized: false, connectorClass: null,
    dialectId: null, accessSurfaceRefs: [], entities, actions: alternatives,
    diagnostics: [{severity: 'error', code: reason, message: reason}],
    ai: {enabled: false, reason: 'No analysis route was authorized.'}});
}

function routeAllowed(security, route, entities) {
  requireMethod(security, 'allowAnalysisRoute', 'Discovery analysis security');
  return security.allowAnalysisRoute(route, entities) === true;
}

function scratchbirdRoute(entities, requestedConnector, requestedSurfaceId,
  security) {
  for(const entity of entities) {
    if(!String(entity.canonicalRef ?? '').startsWith('scratchbird://')) {
      return refused(entities, 'scratchbird_canonical_identity_required');
    }
  }
  const compatibilityByEntity = entities.map((entity) =>
    entity.authorizedAliases.filter((surface) =>
      surface.kind === 'compatibility_parser'));
  const nativeByEntity = entities.map((entity) => entity.authorizedAliases.filter(
    (surface) => surface.kind === 'sbsql_native' &&
      surface.crossSurfaceVisibility === 'engine_authorized'));
  const rootKey = (surface) =>
    `${surface.surfaceId}:${surface.workareaSchemaRef}:${surface.visibilityScope}`;
  const commonRoots = compatibilityByEntity.slice(1).reduce((common, surfaces) =>
    new Set([...common].filter((key) => surfaces.some((surface) =>
      rootKey(surface) === key))), new Set(compatibilityByEntity[0].map(rootKey)));
  const crossRoot = entities.length > 1 && commonRoots.size === 0;
  if(requestedConnector === 'scratchbird_compatibility') {
    if(!requestedSurfaceId) return refused(entities,
      'compatibility_surface_required', ['open_sbsql']);
    const selected = compatibilityByEntity.map((surfaces) => surfaces.find(
      (surface) => surface.surfaceId === requestedSurfaceId));
    if(selected.some((surface) => !surface)) return refused(entities,
      'compatibility_surface_not_visible_for_every_resource', ['open_sbsql']);
    const selectedRoots = new Set(selected.map((surface) =>
      `${surface.workareaSchemaRef}:${surface.visibilityScope}`));
    if(selectedRoots.size > 1 || crossRoot) return refused(entities,
      'compatibility_cross_root_forbidden', ['open_sbsql']);
    const route = {connectorClass: 'scratchbird_compatibility',
      dialectId: selected[0].dialectId,
      accessSurfaceRefs: selected.map((surface) => surface.surfaceId)};
    if(!routeAllowed(security, route, entities)) return refused(entities,
      'analysis_route_denied');
    return route;
  }
  if(requestedConnector === 'database_provider') return refused(entities,
    'scratchbird_requires_explicit_sbsql_or_compatibility_connector');
  if(nativeByEntity.some((surfaces) => !surfaces.length)) {
    if(requestedConnector === 'scratchbird_sbsql' || crossRoot ||
        entities.length > 1) return refused(entities,
      'sbsql_surface_not_visible_for_every_resource');
    const sole = compatibilityByEntity[0];
    if(sole.length !== 1) return refused(entities,
      'no_unambiguous_authorized_analysis_surface');
    const route = {connectorClass: 'scratchbird_compatibility',
      dialectId: sole[0].dialectId, accessSurfaceRefs: [sole[0].surfaceId]};
    return routeAllowed(security, route, entities) ? route : refused(entities,
      'analysis_route_denied');
  }
  if((crossRoot || entities.length > 1)) {
    requireMethod(security, 'allowCrossSurfaceAnalysis',
      'Discovery cross-surface analysis security');
    if(security.allowCrossSurfaceAnalysis(entities,
      nativeByEntity.flat()) !== true) return refused(entities,
      'sbsql_cross_surface_policy_denied');
  }
  const selected = nativeByEntity.map((surfaces) => surfaces[0]);
  const route = {connectorClass: 'scratchbird_sbsql', dialectId: 'sbsql',
    accessSurfaceRefs: selected.map((surface) => surface.surfaceId)};
  return routeAllowed(security, route, entities) ? route : refused(entities,
    'analysis_route_denied');
}

function externalRoute(entities, requestedConnector, security) {
  if(requestedConnector.startsWith('scratchbird_')) return refused(entities,
    'scratchbird_connector_cannot_address_external_resources');
  const providers = new Set(entities.map((entity) => entity.provider));
  if(providers.size > 1) {
    const route = {connectorClass: 'multi_connection_orchestration',
      dialectId: null, accessSurfaceRefs: []};
    return routeAllowed(security, route, entities) ? route : refused(entities,
      'cross_provider_orchestration_not_authorized');
  }
  const route = {connectorClass: 'database_provider',
    dialectId: entities[0].provider, accessSurfaceRefs: []};
  return routeAllowed(security, route, entities) ? route : refused(entities,
    'analysis_route_denied');
}

export class DiscoveryAnalysisService {
  constructor({discoveryAPI, aiInterface=null}={}) {
    requireMethod(discoveryAPI, 'get_entity',
      'Discovery analysis service API');
    if(aiInterface !== null) requireMethod(aiInterface,
      'requestDiscoveryAnalysis', 'Discovery AI Interface');
    this.discoveryAPI = discoveryAPI; this.aiInterface = aiInterface;
  }

  capabilities() {
    return immutable({deterministicPlanning: true,
      aiInterpretation: this.aiInterface !== null,
      aiDisabledReason: this.aiInterface === null ?
        'AI Interface discovery assistance is not configured.' : null});
  }

  async plan(input, {security}={}) {
    exact(input, ['canonicalRefs', 'question', 'requestedConnector',
      'requestedSurfaceId'], 'Discovery analysis request');
    const canonicalRefs = references(input.canonicalRefs);
    const question = platformValue(input.question,
      'Discovery analysis question', 4000);
    const requestedConnector = input.requestedConnector ?? 'auto';
    if(!DISCOVERY_ANALYSIS_CONNECTORS.includes(requestedConnector)) throw new TypeError(
      'Discovery analysis connector request is invalid.'
    );
    const requestedSurfaceId = input.requestedSurfaceId == null ? null :
      platformValue(input.requestedSurfaceId,
        'Discovery analysis surface ID', 512);
    const entities = [];
    for(const canonicalRef of canonicalRefs) entities.push(
      await this.discoveryAPI.get_entity({canonicalRef}, {security}));
    const scratchbird = entities.filter((entity) =>
      entity.provider === 'scratchbird');
    if(scratchbird.length && scratchbird.length !== entities.length) {
      return refused(entities, 'mixed_scratchbird_external_transaction_forbidden',
        ['multi_connection_orchestration']);
    }
    const candidate = scratchbird.length ? scratchbirdRoute(entities,
      requestedConnector, requestedSurfaceId, security) : externalRoute(
      entities, requestedConnector, security);
    if(candidate.status === 'refused') return candidate;
    return immutable({schema: 'cdeadmin.discovery-analysis-plan.v1',
      status: 'draft', executionAuthorized: false, question,
      connectorClass: candidate.connectorClass, dialectId: candidate.dialectId,
      accessSurfaceRefs: [...new Set(candidate.accessSurfaceRefs)], entities,
      actions: candidate.connectorClass === 'scratchbird_sbsql' ?
        ['open_sbsql', 'open_query', 'open_bi', 'add_to_project'] :
        candidate.connectorClass === 'scratchbird_compatibility' ?
          ['open_compatibility', 'open_query', 'add_to_project'] :
          ['open_query', 'open_bi', 'add_to_project'],
      diagnostics: [], ai: {enabled: this.aiInterface !== null,
        reason: this.aiInterface === null ?
          'AI Interface discovery assistance is not configured.' : null}});
  }

  async interpret(input, {security, aiContext={}}={}) {
    const plan = await this.plan(input, {security});
    if(plan.status === 'refused' || !this.aiInterface) return immutable({plan,
      interpretation: null, ai: this.capabilities()});
    const interpretation = await this.aiInterface.requestDiscoveryAnalysis({
      question: plan.question, connectorClass: plan.connectorClass,
      dialectId: plan.dialectId, accessSurfaceRefs: plan.accessSurfaceRefs,
      entities: plan.entities.map((entity) => ({resultRef: entity.resultRef,
        canonicalRef: entity.canonicalRef, entityClass: entity.entityClass,
        name: entity.name, description: entity.description,
        businessTerms: entity.businessTerms, schemaSummary: entity.schemaSummary,
        accessState: entity.accessState})), executionAuthorized: false}, aiContext);
    plainObject(interpretation, 'Discovery AI analysis interpretation');
    noRawSecrets(interpretation, 'Discovery AI analysis interpretation');
    return immutable({plan, interpretation: {...interpretation},
      ai: this.capabilities()});
  }
}
