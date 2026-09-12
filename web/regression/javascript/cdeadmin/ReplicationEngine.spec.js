import {
  applyLag, applyPosition, expectedTopologyResult, overallReplicationHealth,
  replicationPlanIdentity, topologyGraph, validateFailoverReview,
  validateReplicationDefinition,
} from 'sources/cdeadmin_ui/modules/replication/ReplicationEngine';
import {
  definition, failoverPlan, failoverReview, lagSample, replicationHealth,
  replicationPosition, topology,
} from './ReplicationTestUtils';

describe('Replication Topology engine', () => {
  test('does not label unknown critical health as healthy', () => {
    expect(overallReplicationHealth({})).toBe('unknown');
    expect(overallReplicationHealth({connectivity: 'healthy', linkState: 'healthy',
      quorum: 'healthy', errors: 'healthy', provider: 'healthy'})).toBe('healthy');
    expect(overallReplicationHealth({connectivity: 'unhealthy'})).toBe('unhealthy');
    expect(overallReplicationHealth({connectivity: 'unknown'},
      {allowUnknownCritical: true})).toBe('partial');
  });

  test('rejects a healthy claim when critical dimensions are unknown', () => {
    const item = topology(); item.participants[0].health = replicationHealth('healthy',
      {dimensions: {connectivity: 'healthy'}});
    const result = validateReplicationDefinition(definition({savedTopologies: [item]}));
    expect(result.valid).toBe(false);
    expect(result.details.join(' ')).toMatch(/cannot be healthy/i);
  });

  test('preserves exact positions without calculating arbitrary differences', () => {
    const positioned = applyPosition(topology(), 'replica', replicationPosition('opaque:xyz'));
    expect(positioned.participants[1].position.rawValue).toBe('opaque:xyz');
    expect(positioned.lagSamples).toEqual([]);
    const withLag = applyLag(positioned, 'link-one', lagSample({value: 7,
      source: 'provider_reported', calculation: {providerMetric: 'seconds_behind'}}));
    expect(withLag.lagSamples[0]).toMatchObject({value: 7, source: 'provider_reported'});
  });

  test('builds a visual graph without changing native roles or links', () => {
    const source = topology(); const graph = topologyGraph(source, {positions: {primary: {x: 7, y: 8}}});
    expect(graph.nodes[0]).toMatchObject({id: 'primary', kind: 'writer_capable',
      namespace: 'primary · healthy', position: {x: 7, y: 8}});
    expect(graph.edges[0]).toMatchObject({from: 'primary', to: 'replica',
      type: 'physical streaming'});
    expect(source.participants[0].nativeRole).toBe('primary');
  });

  test.each(['quorumSafe', 'candidateEligible', 'positionSafe', 'dataLossAssessed'])(
    'fails closed without %s provider evidence', (field) => {
      const review = validateFailoverReview(failoverPlan(), failoverReview({[field]: false}));
      expect(review.valid).toBe(false); expect(review.details).not.toHaveLength(0);
    });

  test('requires every declared precondition result', () => {
    const review = validateFailoverReview(failoverPlan(), failoverReview({preconditions: []}));
    expect(review.valid).toBe(false);
    expect(review.details).toContain('Missing precondition result quorum.');
  });

  test('verifies provider rediscovery against expected native or normalized roles', () => {
    const plan = failoverPlan();
    const promoted = topology({participants: [
      {...topology().participants[0], normalizedRole: 'read_only_replica', nativeRole: 'standby'},
      {...topology().participants[1], normalizedRole: 'writer_capable', nativeRole: 'primary'}]});
    expect(expectedTopologyResult(plan, promoted).valid).toBe(true);
    expect(expectedTopologyResult(plan, topology()).valid).toBe(false);
  });

  test('identifies the exact immutable plan revision', () => {
    expect(replicationPlanIdentity(failoverPlan())).toBe(replicationPlanIdentity(failoverPlan()));
    expect(replicationPlanIdentity(failoverPlan({targetRole: 'leader'})))
      .not.toBe(replicationPlanIdentity(failoverPlan()));
  });
});
