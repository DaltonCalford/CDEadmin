import {
  REPLICATION_HEALTH_STATES, REPLICATION_ROLES, REPLICATION_UI_STATES,
  createReplicationContent, exportReplicationContent, importReplicationContent,
  replicationAssetRequest, validateFailoverPlan, validateReplicationTopology,
} from 'sources/cdeadmin_ui/modules/replication/contracts';
import {definition, failoverPlan, participant, topology} from './ReplicationTestUtils';

describe('Replication Topology contracts', () => {
  test('publishes exact normalized roles, health and UI states', () => {
    expect(REPLICATION_ROLES).toEqual(['writer_capable', 'read_only_replica', 'peer',
      'arbiter/voter', 'router', 'witness', 'unknown']);
    expect(REPLICATION_HEALTH_STATES).toEqual(['healthy', 'degraded', 'unhealthy',
      'unknown', 'partial']);
    expect(REPLICATION_UI_STATES).toHaveLength(11);
  });

  test.each(REPLICATION_ROLES)('preserves normalized role %s beside the native role', (role) => {
    const value = topology({participants: [participant('one', role, `native-${role}`)],
      groups: [{id: 'group-one', name: 'g', nativeType: 'native-group', memberIds: ['one'],
        quorumState: null, roleMetadata: {}, nativeDetails: {}}], links: []});
    const saved = validateReplicationTopology(value);
    expect(saved.participants[0]).toMatchObject({normalizedRole: role,
      nativeRole: `native-${role}`});
  });

  test('rejects dangling group, participant, link and lag references', () => {
    expect(() => validateReplicationTopology(topology({participants: [
      participant('one', 'peer', 'native', {groupId: 'missing'})]}))).toThrow('unknown group');
    expect(() => validateReplicationTopology(topology({links: [{...topology().links[0],
      targetParticipantId: 'missing'}]}))).toThrow('dangling participant');
    expect(() => validateReplicationTopology(topology({lagSamples: [{id: 'bad',
      linkId: 'missing', observedAt: 'now', unit: 'bytes', value: 1, source: 'provider',
      calculation: {}, nativeDetails: {}}]}))).toThrow('unknown link');
  });

  test('requires complete, provider-native failover runbooks', () => {
    for(const collection of ['preconditions', 'commands', 'verification', 'rollback']) {
      expect(() => validateFailoverPlan(failoverPlan({[collection]: []}))).toThrow('require');
    }
    expect(validateFailoverPlan(failoverPlan()).targetRole).toBe('primary');
  });

  test('validates relationships between assets and topologies', () => {
    expect(() => createReplicationContent(definition({alertPolicies: [{id: 'bad',
      topologyId: 'missing', metric: 'lag', maximum: 1, unit: 's', enabled: true,
      nativeDetails: {}}]}))).toThrow('unknown topology');
    expect(() => createReplicationContent(definition({failoverPlans: [
      failoverPlan({candidateParticipantId: 'missing'})]}))).toThrow('unknown candidate');
    expect(() => createReplicationContent(definition({visualLayouts: [{id: 'bad',
      topologyId: 'topology-one', positions: {missing: {x: 1, y: 2}},
      nativeDetails: {}}]}))).toThrow('unknown participant');
  });

  test('round-trips deterministic source and rejects raw secrets', () => {
    const encoded = exportReplicationContent(definition());
    expect(exportReplicationContent(importReplicationContent(encoded))).toBe(encoded);
    expect(() => createReplicationContent(definition({extensions: {password: 'bad'}})))
      .toThrow(/credential/i);
    expect(() => importReplicationContent('{bad')).toThrow('external_format_invalid');
  });

  test('builds a strict optimistic project-asset request', () => {
    const request = replicationAssetRequest({name: 'Replication', path: 'replication/r.json',
      expectedVersion: 4, content: definition()});
    expect(request).toMatchObject({asset_type: 'cdeadmin.replication.v1',
      schema_name: 'cdeadmin.replication.v1', expected_version: 4});
    expect(() => replicationAssetRequest({name: 'x', path: 'x', expectedVersion: 'bad',
      content: definition()})).toThrow('expected version');
  });
});
