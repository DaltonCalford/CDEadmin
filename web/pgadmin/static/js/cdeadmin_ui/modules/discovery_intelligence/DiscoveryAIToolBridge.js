/////////////////////////////////////////////////////////////
// Bounded AI-tool publication over the governed Discovery API.
/////////////////////////////////////////////////////////////

import {immutable, noRawSecrets, plainObject} from
  '../../platform/serviceUtils';
import {DISCOVERY_SERVICE_API_METHODS} from './DiscoveryServiceAPI';

export const DISCOVERY_AI_TOOL_IDS = Object.freeze([
  'discovery.search_data', 'discovery.explain_asset',
  'discovery.find_certified_alternative', 'discovery.find_metric',
  'discovery.explain_lineage', 'discovery.compare_candidates',
  'discovery.draft_analysis_plan', 'discovery.request_access_draft',
]);

const OBJECT_OUTPUT_SCHEMA = Object.freeze({type: 'object',
  additionalProperties: false, required: ['rows', 'meta'], properties: {
    rows: {type: 'array', items: {type: 'object'}},
    meta: {type: 'object'},
  }});
const STRING = Object.freeze({type: 'string', minLength: 1, maxLength: 4096});
const SHORT_STRING = Object.freeze({type: 'string', minLength: 1,
  maxLength: 512});
const REF_ARRAY = Object.freeze({type: 'array', minItems: 1, maxItems: 50,
  uniqueItems: true, items: STRING});

const SEARCH_QUERY_SCHEMA = Object.freeze({type: 'object',
  additionalProperties: false, properties: {
    text: {type: 'string', minLength: 1, maxLength: 1000},
    mode: {enum: ['QUICK', 'KEYWORD', 'ADVANCED_FACETED', 'SEMANTIC',
      'BUSINESS_TERM', 'FIELD', 'METRIC', 'SIMILAR_TO', 'GRAPH_RELATED',
      'NATURAL_LANGUAGE']},
    entityClasses: {type: 'array', uniqueItems: true, items: SHORT_STRING},
    providers: {type: 'array', uniqueItems: true, items: SHORT_STRING},
    domains: {type: 'array', uniqueItems: true, items: STRING},
    owners: {type: 'array', uniqueItems: true, items: STRING},
    certification: {type: 'array', uniqueItems: true, items: SHORT_STRING},
    qualityState: {type: 'array', uniqueItems: true, items: SHORT_STRING},
    freshness: {type: 'array', uniqueItems: true, items: SHORT_STRING},
    accessState: {type: 'array', uniqueItems: true, items: SHORT_STRING},
    classification: {type: 'array', uniqueItems: true, items: SHORT_STRING},
    tags: {type: 'array', uniqueItems: true, items: SHORT_STRING},
    nativeKinds: {type: 'array', uniqueItems: true, items: SHORT_STRING},
    environments: {type: 'array', uniqueItems: true, items: SHORT_STRING},
    updatedAfter: {type: ['string', 'null'], maxLength: 64},
    updatedBefore: {type: ['string', 'null'], maxLength: 64},
    minimumUsage: {type: ['number', 'null'], minimum: 0, maximum: 1},
    sort: {enum: ['relevance', 'name', 'updated', 'usage', 'trust',
      'quality', 'freshness']},
    rankingProfile: {type: ['string', 'null'], maxLength: 512},
    pageSize: {type: 'integer', minimum: 1, maximum: 100},
    cursor: {type: ['string', 'null'], maxLength: 4096},
    relatedTo: {type: 'array', uniqueItems: true, maxItems: 50, items: STRING},
  }});

function requireMethods(value, methods, label) {
  for(const method of methods) if(typeof value?.[method] !== 'function') {
    throw new TypeError(`${label} requires ${method}.`);
  }
}

function envelope(rows, meta={}) {
  if(!Array.isArray(rows)) throw new TypeError(
    'Discovery AI tool rows must be an array.'
  );
  const result = {rows: rows.map((row) => immutable({...row})), meta: {...meta}};
  noRawSecrets(result, 'Discovery AI tool result');
  return immutable(result);
}

function resultRows(value) {
  if(Array.isArray(value?.results)) {
    const {results, ...meta} = value;
    return envelope(results, meta);
  }
  if(Array.isArray(value?.rows)) {
    const {rows, ...meta} = value;
    return envelope(rows, meta);
  }
  return envelope([value]);
}

function exact(input, fields, label) {
  plainObject(input, label); noRawSecrets(input, label);
  const unknown = Object.keys(input).find((field) => !fields.includes(field));
  if(unknown) throw new TypeError(`${label} contains unsupported field ${unknown}.`);
  return input;
}

function toolDefinitions(api, analysis) {
  return [{
    id: 'discovery.search_data', description:
      'Search only security-visible Discovery entities with bounded results.',
    inputSchema: {type: 'object', additionalProperties: false,
      required: ['query'], properties: {query: SEARCH_QUERY_SCHEMA}}, maxRows: 100,
    execute: async ({query}, context) => resultRows(await api.search(query,
      {security: context.discoverySecurity, context})),
  }, {
    id: 'discovery.explain_asset', description:
      'Read the security-trimmed identity, metadata, surfaces and signals for one asset.',
    inputSchema: {type: 'object', additionalProperties: false,
      required: ['canonicalRef'], properties: {canonicalRef: STRING}}, maxRows: 1,
    execute: async (input, context) => resultRows(await api.get_entity(input,
      {security: context.discoverySecurity})),
  }, {
    id: 'discovery.find_certified_alternative', description:
      'Find visible certified alternatives related by the governed Discovery index.',
    inputSchema: {type: 'object', additionalProperties: false,
      required: ['canonicalRef'], properties: {canonicalRef: STRING,
        limit: {type: 'integer', minimum: 1, maximum: 50}}}, maxRows: 50,
    execute: async ({canonicalRef, limit=20}, context) => {
      const entity = await api.get_entity({canonicalRef},
        {security: context.discoverySecurity});
      return resultRows(await api.search({text: entity.name,
        certification: ['CERTIFIED', 'CERTIFIED_WITH_CONDITIONS'],
        pageSize: limit}, {security: context.discoverySecurity, context}));
    },
  }, {
    id: 'discovery.find_metric', description:
      'Resolve one governed metric or search visible metric definitions.',
    inputSchema: {type: 'object', additionalProperties: false, oneOf: [
      {required: ['metricId']}, {required: ['query']}], properties: {
      metricId: SHORT_STRING,
      query: {type: 'string', minLength: 1, maxLength: 1000},
      limit: {type: 'integer', minimum: 1, maximum: 50}}}, maxRows: 50,
    execute: async ({metricId, query, limit=20}, context) => metricId ?
      resultRows(api.get_metric({metricId}, {security:
        context.discoverySecurity})) : resultRows(await api.search({text: query,
        entityClasses: ['METRIC'], pageSize: limit}, {security:
        context.discoverySecurity, context})),
  }, {
    id: 'discovery.explain_lineage', description:
      'Read a bounded security-trimmed lineage neighborhood for visible assets.',
    inputSchema: {type: 'object', additionalProperties: false,
      required: ['canonicalRefs'], properties: {canonicalRefs: REF_ARRAY,
        limit: {type: 'integer', minimum: 1, maximum: 200}}}, maxRows: 200,
    execute: async (input, context) => resultRows(await api.get_related(input,
      {security: context.discoverySecurity})),
  }, {
    id: 'discovery.compare_candidates', description:
      'Compare only security-visible candidate context and governed trust evidence.',
    inputSchema: {type: 'object', additionalProperties: false,
      required: ['canonicalRefs'], properties: {canonicalRefs: REF_ARRAY}},
    maxRows: 50,
    execute: async ({canonicalRefs}, context) => {
      const rows = [];
      for(const canonicalRef of canonicalRefs) {
        const entity = await api.get_entity({canonicalRef},
          {security: context.discoverySecurity});
        let trust = null;
        try { trust = await api.get_trust({canonicalRef},
          {security: context.discoverySecurity, context}); }
        catch(error) {
          if(!/not found/i.test(error.message)) throw error;
        }
        rows.push({entity, trust});
      }
      return envelope(rows, {comparisonBasis: 'visible_governed_evidence'});
    },
  }, {
    id: 'discovery.draft_analysis_plan', description:
      'Draft a non-executing analysis route from visible governed resources.',
    inputSchema: {type: 'object', additionalProperties: false,
      required: ['canonicalRefs', 'question'], properties: {
        canonicalRefs: REF_ARRAY,
        question: {type: 'string', minLength: 1, maxLength: 4000},
        requestedConnector: {enum: ['auto', 'database_provider',
          'scratchbird_sbsql', 'scratchbird_compatibility']},
        requestedSurfaceId: {type: ['string', 'null'], maxLength: 512}}},
    maxRows: 50,
    execute: async (input, context) => resultRows(await analysis.plan(input,
      {security: context.discoverySecurity})),
  }, {
    id: 'discovery.request_access_draft', description:
      'Create a non-persisted access-request proposal for a visible target.',
    requiredPermissions: ['ai.use', 'discovery.use', 'discovery.request_access'],
    inputSchema: {type: 'object', additionalProperties: false,
      required: ['requester', 'targetRef', 'requestedAccess', 'environment',
        'reason', 'duration'], properties: {requester: SHORT_STRING,
        targetRef: STRING, requestedAccess: {type: 'array', minItems: 1,
          uniqueItems: true, items: {enum: ['DISCOVER_METADATA', 'VIEW_SCHEMA',
            'VIEW_PROFILE_STATS', 'VIEW_SAMPLE', 'QUERY', 'EXPORT',
            'ADMINISTER']}}, environment: SHORT_STRING,
        reason: {type: 'string', minLength: 1, maxLength: 4000},
        duration: {type: 'string', minLength: 1, maxLength: 128},
        projectRef: {type: ['string', 'null'], maxLength: 512}}}, maxRows: 1,
    execute: async (input, context) => {
      exact(input, ['requester', 'targetRef', 'requestedAccess', 'environment',
        'reason', 'duration', 'projectRef'], 'Discovery access draft tool');
      await api.get_entity({canonicalRef: input.targetRef},
        {security: context.discoverySecurity});
      return envelope([{schema: 'cdeadmin.discovery-access-proposal.v1',
        ...input, projectRef: input.projectRef ?? null, status: 'PROPOSED',
        persisted: false}], {nextAction: 'discovery.access.request.create'});
    },
  }];
}

export class DiscoveryAIToolBridge {
  constructor({readTools, discoveryAPI, analysis, egress}={}) {
    requireMethods(readTools, ['register'], 'Discovery AI read-tool registry');
    requireMethods(discoveryAPI, DISCOVERY_SERVICE_API_METHODS,
      'Discovery AI service API');
    requireMethods(analysis, ['plan'], 'Discovery analysis authority');
    requireMethods(egress, ['admit', 'trim', 'classify'],
      'Discovery AI egress authority');
    this.readTools = readTools; this.discoveryAPI = discoveryAPI;
    this.analysis = analysis; this.egress = egress; this.unregister = [];
  }

  register() {
    if(this.unregister.length) throw new Error(
      'Discovery AI tools are already registered.'
    );
    for(const definition of toolDefinitions(this.discoveryAPI, this.analysis)) {
      const requiredPermissions = definition.requiredPermissions ??
        ['ai.use', 'discovery.use'];
      const execute = definition.execute;
      this.unregister.push(this.readTools.register({id: definition.id,
        moduleId: 'cdeadmin.discovery_intelligence',
        description: definition.description, inputSchema: definition.inputSchema,
        outputSchema: OBJECT_OUTPUT_SCHEMA, requiredPermissions,
        maxRows: definition.maxRows, maxBytes: 512 * 1024,
        contextCostHint: Math.min(definition.maxRows * 64, 4096),
        accessCheck: (context) => context.discoveryEnabled !== false &&
          this.egress.admit(definition.id, context) === true,
        execute: async (args, context) => {
          if(!context.discoverySecurity) throw new Error(
            'Discovery security context is unavailable.'
          );
          const source = await execute(args, context);
          const trimmed = await this.egress.trim(definition.id, source, context);
          plainObject(trimmed, 'Discovery AI egress result');
          noRawSecrets(trimmed, 'Discovery AI egress result');
          if(!Array.isArray(trimmed.rows) || !trimmed.meta ||
              Array.isArray(trimmed.meta) || typeof trimmed.meta !== 'object') {
            throw new TypeError('Discovery AI egress result is invalid.');
          }
          if(trimmed.rows.length > source.rows.length) throw new Error(
            'Discovery AI egress authority cannot add result rows.'
          );
          return immutable({rows: trimmed.rows, meta: trimmed.meta});
        },
        classify: (result, context) => this.egress.classify(
          definition.id, result, context)}));
    }
    return () => this.dispose();
  }

  dispose() {
    const unregister = this.unregister.splice(0).reverse();
    return unregister.reduce((removed, callback) => callback() || removed,
      false);
  }
}

export function createDiscoveryAIToolBridge(input) {
  const bridge = new DiscoveryAIToolBridge(input);
  bridge.register();
  return bridge;
}
