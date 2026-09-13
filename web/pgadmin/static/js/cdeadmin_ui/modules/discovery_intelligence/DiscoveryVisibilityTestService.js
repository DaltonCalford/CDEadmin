/////////////////////////////////////////////////////////////
// Authorized, subject-faithful Discovery visibility diagnostics.
/////////////////////////////////////////////////////////////

import {immutable, noRawSecrets, plainObject, platformValue} from
  '../../platform/serviceUtils';

export const DISCOVERY_VISIBILITY_TEST_SERVICE_ID =
  'cdeadmin.discovery_intelligence.visibility_test';

const SEARCH_SECURITY_METHODS = Object.freeze(['admitDocument', 'admitAlias',
  'admitSignal', 'admitFacet', 'admitGraphEdge', 'admitBusinessKnowledge',
  'exposeCanonicalRef']);

function requireMethod(value, method, label) {
  if(typeof value?.[method] !== 'function') throw new TypeError(
    `${label} requires ${method}.`
  );
}

function exact(input, fields, label) {
  plainObject(input, label); noRawSecrets(input, label);
  const unknown = Object.keys(input).find((field) => !fields.includes(field));
  if(unknown) throw new TypeError(`${label} contains unsupported field ${unknown}.`);
}

function subject(operation, args) {
  const [value, related] = args;
  if(operation === 'admitAlias') return {subjectType: 'access_surface',
    subjectRef: value.surfaceId, resourceRef: related.canonicalRef};
  if(operation === 'admitGraphEdge') return {subjectType: 'graph_edge',
    subjectRef: value.edgeId, resourceRef: null};
  if(operation === 'admitBusinessKnowledge') return {
    subjectType: 'business_knowledge', subjectRef: value.termId ??
      value.metricId ?? value.productId ?? value.domainId ?? value.profileId,
    resourceRef: related?.targetRef ?? null};
  if(operation === 'admitFacet') return {subjectType: 'facet',
    subjectRef: String(value), resourceRef: null};
  if(operation === 'admitSignal') return {subjectType: 'signal',
    subjectRef: String(related), resourceRef: value.canonicalRef};
  return {subjectType: 'document',
    subjectRef: value.documentId, resourceRef: value.canonicalRef};
}

function detail(operation, args) {
  if(operation === 'admitDocument') return {dimension: String(args[1])};
  if(operation === 'admitSignal') return {signal: String(args[1])};
  if(operation === 'admitFacet') return {facet: String(args[0])};
  return {};
}

export class DiscoveryVisibilityTestService {
  constructor({search, principalSecurityResolver, maxTraceEntries=5000}={}) {
    requireMethod(search, 'search', 'Discovery visibility search authority');
    requireMethod(principalSecurityResolver, 'resolve',
      'Discovery principal-security resolver');
    if(!Number.isInteger(maxTraceEntries) || maxTraceEntries < 1 ||
        maxTraceEntries > 50000) throw new TypeError(
      'Discovery visibility trace bound must be from 1 to 50000.'
    );
    this.search = search; this.principalSecurityResolver =
      principalSecurityResolver; this.maxTraceEntries = maxTraceEntries;
  }

  async run(input, {security, context={}}={}) {
    exact(input, ['principalRef', 'query', 'profile'],
      'Discovery visibility test request');
    const principalRef = platformValue(input.principalRef,
      'Discovery visibility test principal', 512);
    requireMethod(security, 'testDiscoveryVisibility',
      'Discovery visibility tester security');
    requireMethod(security, 'admitVisibilityDiagnostic',
      'Discovery visibility tester security');
    if(security.testDiscoveryVisibility(principalRef) !== true) throw new Error(
      'Discovery visibility test denied.'
    );
    const resolved = await this.principalSecurityResolver.resolve(principalRef,
      {testerSecurity: security, context});
    plainObject(resolved, 'Discovery resolved principal security');
    if(resolved.principalRef !== principalRef) throw new Error(
      'Discovery principal-security resolver changed the requested principal.'
    );
    const target = resolved.security;
    for(const method of [...SEARCH_SECURITY_METHODS, 'explainDecision']) {
      requireMethod(target, method, 'Discovery target principal security');
    }
    const trace = []; let evaluated = 0;
    const traced = {};
    for(const method of SEARCH_SECURITY_METHODS) traced[method] = (...args) => {
      const allowed = target[method](...args) === true;
      const explanation = target.explainDecision({operation: method, args,
        allowed, principalRef});
      plainObject(explanation, 'Discovery visibility decision explanation');
      noRawSecrets(explanation, 'Discovery visibility decision explanation');
      const reason = platformValue(explanation.reason,
        'Discovery visibility decision reason', 4000);
      const candidate = immutable({sequence: ++evaluated, operation: method,
        allowed, ...subject(method, args), detail: detail(method, args), reason,
        policyRef: explanation.policyRef == null ? null : platformValue(
          explanation.policyRef, 'Discovery visibility policy reference', 512)});
      if(security.admitVisibilityDiagnostic(candidate, principalRef) === true) {
        if(trace.length >= this.maxTraceEntries) throw new Error(
          'Discovery visibility diagnostic exceeded its configured trace bound.'
        );
        trace.push(candidate);
      }
      return allowed;
    };
    const result = await this.search.search(input.query, {security: traced,
      profile: input.profile, context: {...context,
        visibilityTestPrincipalRef: principalRef}});
    const admitted = trace.filter((entry) => entry.allowed).length;
    return immutable({schema: 'cdeadmin.discovery-visibility-test.v1',
      principalRef, result, diagnostics: trace,
      summary: {evaluated, visibleDiagnostics: trace.length, admitted,
        denied: trace.length - admitted}, adminOverride: false});
  }
}
