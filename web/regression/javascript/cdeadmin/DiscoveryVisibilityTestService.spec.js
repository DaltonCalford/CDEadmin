/////////////////////////////////////////////////////////////
// Subject-faithful Discovery visibility diagnostic gates.
/////////////////////////////////////////////////////////////

import {DiscoveryVisibilityTestService} from
  'sources/cdeadmin_ui/modules/discovery_intelligence';

const PRINCIPAL = 'user:target';
const DOCUMENT = {documentId: 'document-one', canonicalRef: 'resource:one'};

function targetSecurity(overrides={}) {
  return {admitDocument: () => true, admitAlias: () => false,
    admitSignal: () => true, admitFacet: () => true,
    admitGraphEdge: () => false, admitBusinessKnowledge: () => true,
    exposeCanonicalRef: () => true,
    explainDecision: ({operation, allowed}) => ({
      reason: `${operation}:${allowed ? 'allowed' : 'denied'}`,
      policyRef: `policy:${operation}`}), ...overrides};
}

function testerSecurity(overrides={}) {
  return {testDiscoveryVisibility: () => true,
    admitVisibilityDiagnostic: () => true, ...overrides};
}

function searchAuthority() {
  return {search: jest.fn(async (query, {security, context}) => {
    const alias = {surfaceId: 'surface:one'};
    const edge = {edgeId: 'edge:one'};
    const knowledge = {termId: 'term:one'};
    security.admitDocument(DOCUMENT, 'DISCOVER_IDENTITY');
    security.admitAlias(alias, DOCUMENT);
    security.admitSignal(DOCUMENT, 'quality');
    security.admitFacet('provider');
    security.admitGraphEdge(edge);
    security.admitBusinessKnowledge(knowledge, {targetRef: 'resource:one'});
    security.exposeCanonicalRef(DOCUMENT);
    return {query, principalRef: context.visibilityTestPrincipalRef,
      results: [{name: 'visible'}]};
  })};
}

function service({target=targetSecurity(), search=searchAuthority(), max=5000,
  resolvedPrincipal=PRINCIPAL}={}) {
  const resolver = {resolve: jest.fn(async () => ({
    principalRef: resolvedPrincipal, security: target}))};
  return {service: new DiscoveryVisibilityTestService({search,
    principalSecurityResolver: resolver, maxTraceEntries: max}), resolver, search};
}

describe('DiscoveryVisibilityTestService', () => {
  test('runs the real search authority as the requested subject and explains every gate',
    async () => {
      const runtime = service();
      const result = await runtime.service.run({principalRef: PRINCIPAL,
        query: {text: 'customer'}}, {security: testerSecurity(),
        context: {requestId: 'request-one'}});
      expect(result).toMatchObject({
        schema: 'cdeadmin.discovery-visibility-test.v1',
        principalRef: PRINCIPAL, adminOverride: false,
        result: {principalRef: PRINCIPAL, results: [{name: 'visible'}]},
        summary: {evaluated: 7, visibleDiagnostics: 7, admitted: 5, denied: 2}});
      expect(result.diagnostics.map((entry) => entry.operation)).toEqual([
        'admitDocument', 'admitAlias', 'admitSignal', 'admitFacet',
        'admitGraphEdge', 'admitBusinessKnowledge', 'exposeCanonicalRef']);
      expect(result.diagnostics[0]).toMatchObject({sequence: 1,
        subjectType: 'document', subjectRef: 'document-one',
        resourceRef: 'resource:one', detail: {dimension: 'DISCOVER_IDENTITY'},
        reason: 'admitDocument:allowed', policyRef: 'policy:admitDocument'});
      expect(runtime.resolver.resolve).toHaveBeenCalledWith(PRINCIPAL,
        expect.objectContaining({context: {requestId: 'request-one'}}));
    });

  test('requires diagnostic permission without invoking the target resolver', async () => {
    const runtime = service();
    await expect(runtime.service.run({principalRef: PRINCIPAL,
      query: {text: 'customer'}}, {security: testerSecurity({
      testDiscoveryVisibility: () => false})})).rejects.toThrow(
      /visibility test denied/);
    expect(runtime.resolver.resolve).not.toHaveBeenCalled();
  });

  test('cannot switch principal or accept an administrative override', async () => {
    const runtime = service({resolvedPrincipal: 'user:other'});
    await expect(runtime.service.run({principalRef: PRINCIPAL,
      query: {text: 'customer'}}, {security: testerSecurity()})).rejects.toThrow(
      /changed the requested principal/);
    await expect(runtime.service.run({principalRef: PRINCIPAL,
      query: {text: 'customer'}, adminOverride: true},
    {security: testerSecurity()})).rejects.toThrow(/unsupported field adminOverride/);
  });

  test('filters diagnostics through tester security without changing target results',
    async () => {
      const runtime = service();
      const result = await runtime.service.run({principalRef: PRINCIPAL,
        query: {text: 'customer'}}, {security: testerSecurity({
        admitVisibilityDiagnostic: (entry) => entry.allowed})});
      expect(result.result.results).toEqual([{name: 'visible'}]);
      expect(result.summary).toEqual({evaluated: 7, visibleDiagnostics: 5,
        admitted: 5, denied: 0});
      expect(result.diagnostics.every((entry) => entry.allowed)).toBe(true);
    });

  test('fails closed on unbounded traces and raw-secret explanations', async () => {
    await expect(service({max: 1}).service.run({principalRef: PRINCIPAL,
      query: {text: 'customer'}}, {security: testerSecurity()})).rejects.toThrow(
      /trace bound/);
    const unsafe = targetSecurity({explainDecision: () => ({reason: 'unsafe',
      password: 'raw'})});
    await expect(service({target: unsafe}).service.run({principalRef: PRINCIPAL,
      query: {text: 'customer'}}, {security: testerSecurity()})).rejects.toThrow(
      /Raw credential field is forbidden/);
  });
});
