/////////////////////////////////////////////////////////////
// Replication health, topology composition and failover safety.
/////////////////////////////////////////////////////////////

import {immutable, noRawSecrets, plainObject, platformValue} from '../../platform/serviceUtils';
import {
  REPLICATION_HEALTH_STATES, createReplicationContent, validateFailoverPlan,
  validateLagSample, validateReplicationPosition, validateReplicationTopology,
} from './contracts';

const CRITICAL_HEALTH_DIMENSIONS = Object.freeze([
  'connectivity', 'linkState', 'quorum', 'errors', 'provider',
]);

export function overallReplicationHealth(input, {allowUnknownCritical=false}={}) {
  plainObject(input, 'Replication health dimensions');
  const values = Object.entries(input); values.forEach(([name, state]) => {
    platformValue(name, 'Replication health dimension');
    if(!REPLICATION_HEALTH_STATES.includes(state)) throw new TypeError(
      `Replication health dimension ${name} is invalid.`
    );
  });
  if(values.some(([, state]) => state === 'unhealthy')) return 'unhealthy';
  if(values.some(([, state]) => state === 'degraded')) return 'degraded';
  if(values.some(([, state]) => state === 'partial')) return 'partial';
  if(!allowUnknownCritical && CRITICAL_HEALTH_DIMENSIONS.some(
    (name) => !Object.hasOwn(input, name) || input[name] === 'unknown'
  )) return 'unknown';
  if(values.some(([, state]) => state === 'unknown')) return 'partial';
  return 'healthy';
}

export function validateReplicationDefinition(input) {
  const content = createReplicationContent(input); const details = []; const warnings = [];
  if(!content.name.trim()) details.push('Replication asset name is required.');
  if(!content.savedTopologies.length) details.push('At least one scoped topology is required.');
  content.savedTopologies.forEach((topology) => {
    topology.participants.forEach((participant) => {
      if(participant.normalizedRole === 'unknown') warnings.push(
        `Participant ${participant.id} has an unknown normalized role; native role remains authoritative.`
      );
      const computed = overallReplicationHealth(participant.health.dimensions,
        {allowUnknownCritical: participant.health.policyAllowsUnknownCritical});
      if(participant.health.overall === 'healthy' && computed !== 'healthy') details.push(
        `Participant ${participant.id} cannot be healthy while critical health is ${computed}.`
      );
    });
    topology.links.forEach((link) => {
      const computed = overallReplicationHealth(link.health.dimensions,
        {allowUnknownCritical: link.health.policyAllowsUnknownCritical});
      if(link.health.overall === 'healthy' && computed !== 'healthy') details.push(
        `Replication link ${link.id} cannot be healthy while critical health is ${computed}.`
      );
    });
  });
  return immutable({valid: details.length === 0, details: [...new Set(details)],
    warnings: [...new Set(warnings)]});
}

export function applyPosition(topologyInput, participantId, positionInput) {
  const topology = validateReplicationTopology(topologyInput);
  const position = validateReplicationPosition(positionInput);
  if(!topology.participants.some((item) => item.id === participantId)) throw new Error(
    `Unknown replication participant: ${participantId}`
  );
  return validateReplicationTopology({...topology, participants: topology.participants.map(
    (item) => item.id === participantId ? {...item, position} : item
  )});
}

export function applyLag(topologyInput, linkId, sampleInput) {
  const topology = validateReplicationTopology(topologyInput);
  const sample = validateLagSample(sampleInput);
  if(sample.linkId !== linkId || !topology.links.some((item) => item.id === linkId)) throw new Error(
    `Lag sample does not match replication link ${linkId}.`
  );
  return validateReplicationTopology({...topology,
    lagSamples: [...topology.lagSamples.filter((item) => item.id !== sample.id), sample]});
}

export function topologyGraph(topologyInput, layout={}) {
  const topology = validateReplicationTopology(topologyInput);
  const positions = layout.positions ?? {};
  return immutable({nodes: topology.participants.map((participant) => ({id: participant.id,
    name: participant.name, kind: participant.normalizedRole,
    namespace: `${participant.nativeRole} · ${participant.health.overall}`,
    reference: participant.resourceRef, position: positions[participant.id]})),
  edges: topology.links.map((link) => ({id: link.id, from: link.sourceParticipantId,
    to: link.targetParticipantId, type: link.mechanism, state: link.nativeState}))});
}

function reviewItem(input, label) {
  plainObject(input, label); noRawSecrets(input, label);
  return immutable({id: platformValue(input.id, `${label} ID`),
    passed: input.passed === true, message: String(input.message ?? ''),
    evidence: immutable({...plainObject(input.evidence ?? {}, `${label} evidence`)})});
}

export function validateFailoverReview(planInput, input) {
  const plan = validateFailoverPlan(planInput); plainObject(input, 'Failover review');
  noRawSecrets(input, 'Failover review');
  const preconditions = Array.isArray(input.preconditions) ? input.preconditions.map(
    (item) => reviewItem(item, 'Failover precondition result')
  ) : [];
  const required = new Set(plan.preconditions.map((item) => item.id));
  const observed = new Set(preconditions.map((item) => item.id));
  const details = [];
  required.forEach((id) => { if(!observed.has(id)) details.push(`Missing precondition result ${id}.`); });
  preconditions.filter((item) => required.has(item.id) && !item.passed).forEach(
    (item) => details.push(`Precondition ${item.id} did not pass.`));
  if(input.quorumSafe !== true) details.push('Provider quorum safety is not proven.');
  if(input.candidateEligible !== true) details.push('Provider candidate eligibility is not proven.');
  if(input.positionSafe !== true) details.push('Replication position safety is not proven.');
  if(input.dataLossAssessed !== true) details.push('Data-loss risk is not provider-assessed.');
  return immutable({schema: 'cdeadmin.replication-failover-review.v1', planId: plan.id,
    valid: details.length === 0, details, preconditions,
    quorumSafe: input.quorumSafe === true, candidateEligible: input.candidateEligible === true,
    positionSafe: input.positionSafe === true, dataLossAssessed: input.dataLossAssessed === true,
    estimatedRPO: input.estimatedRPO ?? null, estimatedRTO: input.estimatedRTO ?? null,
    evidence: immutable({...plainObject(input.evidence ?? {}, 'Failover review evidence')}),
    nativeDetails: immutable({...plainObject(input.nativeDetails ?? {},
      'Failover review native details')})});
}

export function expectedTopologyResult(planInput, topologyInput) {
  const plan = validateFailoverPlan(planInput); const topology = validateReplicationTopology(topologyInput);
  const details = []; const roles = plainObject(plan.expectedTopology.participantRoles ?? {},
    'Expected participant roles');
  const states = plainObject(plan.expectedTopology.linkStates ?? {}, 'Expected link states');
  Object.entries(roles).forEach(([id, role]) => {
    const participant = topology.participants.find((item) => item.id === id);
    if(!participant) details.push(`Expected participant ${id} is absent.`);
    else if(participant.normalizedRole !== role && participant.nativeRole !== role) details.push(
      `Participant ${id} has role ${participant.nativeRole}/${participant.normalizedRole}, expected ${role}.`
    );
  });
  Object.entries(states).forEach(([id, state]) => {
    const link = topology.links.find((item) => item.id === id);
    if(!link) details.push(`Expected link ${id} is absent.`);
    else if(link.nativeState !== state) details.push(
      `Replication link ${id} is ${link.nativeState}, expected ${state}.`
    );
  });
  return immutable({valid: details.length === 0, details});
}

export function replicationPlanIdentity(planInput) {
  const plan = validateFailoverPlan(planInput);
  return JSON.stringify(plan);
}
