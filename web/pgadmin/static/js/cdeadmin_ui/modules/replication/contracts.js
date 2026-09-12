/////////////////////////////////////////////////////////////
// Replication topology canonical assets and domain contracts.
/////////////////////////////////////////////////////////////

import {immutable, noRawSecrets, plainObject, platformValue} from '../../platform/serviceUtils';

export const REPLICATION_MODULE_ID = 'cdeadmin.replication';
export const REPLICATION_ASSET_TYPE = 'cdeadmin.replication.v1';
export const REPLICATION_ASSET_SCHEMA = 'cdeadmin.replication.asset.v1';
export const REPLICATION_ROLES = Object.freeze([
  'writer_capable', 'read_only_replica', 'peer', 'arbiter/voter',
  'router', 'witness', 'unknown',
]);
export const REPLICATION_HEALTH_STATES = Object.freeze([
  'healthy', 'degraded', 'unhealthy', 'unknown', 'partial',
]);
export const REPLICATION_UI_STATES = Object.freeze([
  'empty', 'loading', 'ready', 'stale', 'partial', 'permission_denied',
  'disconnected', 'validation_error', 'runtime_failure', 'read_only',
  'background_task_active',
]);

const REFERENCE_SCHEMAS = new Set([
  'cdeadmin.resource-ref.v1', 'cdeadmin.asset-ref.v1', 'cdeadmin.external-ref.v1',
]);

function object(value, label, maximum=1024 * 1024) {
  const result = plainObject(value ?? {}, label); noRawSecrets(result, label);
  if(JSON.stringify(result).length > maximum) throw new TypeError(`${label} exceeds its size limit.`);
  return immutable({...result});
}

function array(value, label, mapper, maximum=10000) {
  if(!Array.isArray(value) || value.length > maximum) throw new TypeError(
    `${label} must be an array containing no more than ${maximum} items.`
  );
  return value.map(mapper);
}

function unique(value, label, mapper, maximum=10000) {
  const result = array(value ?? [], label, mapper, maximum); const ids = new Set();
  result.forEach((item) => {
    if(ids.has(item.id)) throw new TypeError(`Duplicate ${label}: ${item.id}`);
    ids.add(item.id);
  });
  return result.sort((left, right) => left.id.localeCompare(right.id));
}

function optionalText(value, label, maximum=16384) {
  if(value === undefined || value === null || value === '') return null;
  return platformValue(value, label, maximum);
}

function finite(value, label, {minimum=0, maximum=Number.MAX_SAFE_INTEGER}={}) {
  const number = Number(value);
  if(!Number.isFinite(number) || number < minimum || number > maximum) throw new TypeError(
    `${label} must be from ${minimum} through ${maximum}.`
  );
  return number;
}

function boolean(value, label, fallback=false) {
  if(value === undefined) return fallback;
  if(typeof value !== 'boolean') throw new TypeError(`${label} must be boolean.`);
  return value;
}

export function validateReplicationReference(input, label='Replication reference') {
  plainObject(input, label); noRawSecrets(input, label);
  const schema = platformValue(input.schema, `${label} schema`);
  if(!REFERENCE_SCHEMAS.has(schema)) throw new TypeError(`${label} schema is unsupported.`);
  if(schema === 'cdeadmin.resource-ref.v1') {
    platformValue(input.canonical, `${label} canonical identity`, 8192);
    platformValue(input.provider ?? input.providerId, `${label} provider`);
  } else if(schema === 'cdeadmin.asset-ref.v1') {
    platformValue(input.projectId, `${label} project ID`);
    platformValue(input.assetId, `${label} asset ID`);
  } else platformValue(input.id, `${label} identity`, 8192);
  return immutable({...input});
}

export function replicationReferenceKey(input) {
  const ref = validateReplicationReference(input);
  if(ref.schema === 'cdeadmin.resource-ref.v1') return `resource:${ref.canonical}`;
  if(ref.schema === 'cdeadmin.asset-ref.v1') return `asset:${ref.projectId}/${ref.assetId}`;
  return `external:${ref.id}`;
}

export function validateReplicationPosition(input) {
  if(input === undefined || input === null) return null;
  plainObject(input, 'Replication position'); noRawSecrets(input, 'Replication position');
  return immutable({schema: 'cdeadmin.replication-position.v1',
    provider: platformValue(input.provider, 'Replication position provider'),
    positionType: platformValue(input.positionType, 'Replication position type'),
    rawValue: platformValue(input.rawValue, 'Replication position raw value', 65536),
    comparableWithinScope: boolean(input.comparableWithinScope,
      'Replication position comparability'),
    observedAt: platformValue(input.observedAt, 'Replication position observation time'),
    nativeDetails: object(input.nativeDetails, 'Replication position native details')});
}

export function validateReplicationHealth(input={}) {
  plainObject(input, 'Replication health'); noRawSecrets(input, 'Replication health');
  const overall = platformValue(input.overall ?? 'unknown', 'Replication overall health');
  if(!REPLICATION_HEALTH_STATES.includes(overall)) throw new TypeError(
    'Replication overall health is invalid.'
  );
  const dimensions = object(input.dimensions, 'Replication health dimensions');
  Object.entries(dimensions).forEach(([name, value]) => {
    platformValue(name, 'Replication health dimension name');
    if(!REPLICATION_HEALTH_STATES.includes(value)) throw new TypeError(
      `Replication health dimension ${name} is invalid.`
    );
  });
  return immutable({schema: 'cdeadmin.replication-health.v1', overall, dimensions,
    policyAllowsUnknownCritical: boolean(input.policyAllowsUnknownCritical,
      'Unknown-critical health policy'),
    providerReported: optionalText(input.providerReported,
      'Provider-reported replication health'),
    nativeDetails: object(input.nativeDetails, 'Replication health native details')});
}

export function validateParticipant(input) {
  plainObject(input, 'Replication participant'); noRawSecrets(input, 'Replication participant');
  const normalizedRole = platformValue(input.normalizedRole ?? 'unknown',
    'Replication participant normalized role');
  if(!REPLICATION_ROLES.includes(normalizedRole)) throw new TypeError(
    'Replication participant normalized role is invalid.'
  );
  return immutable({schema: 'cdeadmin.replication-participant.v1',
    id: platformValue(input.id, 'Replication participant ID'),
    name: platformValue(input.name ?? input.id, 'Replication participant name'),
    resourceRef: validateReplicationReference(input.resourceRef,
      'Replication participant resource'), normalizedRole,
    nativeRole: platformValue(input.nativeRole, 'Replication participant native role'),
    nativeState: platformValue(input.nativeState, 'Replication participant native state'),
    groupId: optionalText(input.groupId, 'Replication participant group ID'),
    health: validateReplicationHealth(input.health),
    position: validateReplicationPosition(input.position),
    storage: object(input.storage, 'Replication participant storage'),
    connections: object(input.connections, 'Replication participant connections'),
    nativeDetails: object(input.nativeDetails, 'Replication participant native details')});
}

export function validateReplicaGroup(input) {
  plainObject(input, 'Replication group'); noRawSecrets(input, 'Replication group');
  const memberIds = [...new Set(array(input.memberIds ?? [], 'Replication group member IDs',
    (id) => platformValue(id, 'Replication group member ID'))) ].sort();
  return immutable({schema: 'cdeadmin.replica-group.v1',
    id: platformValue(input.id, 'Replication group ID'),
    name: platformValue(input.name ?? input.id, 'Replication group name'),
    nativeType: platformValue(input.nativeType, 'Replication group native type'), memberIds,
    quorumState: optionalText(input.quorumState, 'Replication group quorum state'),
    roleMetadata: object(input.roleMetadata, 'Replication group role metadata'),
    nativeDetails: object(input.nativeDetails, 'Replication group native details')});
}

export function validateReplicationLink(input) {
  plainObject(input, 'Replication link'); noRawSecrets(input, 'Replication link');
  return immutable({schema: 'cdeadmin.replication-link.v1',
    id: platformValue(input.id, 'Replication link ID'),
    name: platformValue(input.name ?? input.id, 'Replication link name'),
    sourceParticipantId: platformValue(input.sourceParticipantId,
      'Replication link source participant'),
    targetParticipantId: platformValue(input.targetParticipantId,
      'Replication link target participant'),
    mechanism: platformValue(input.mechanism, 'Replication link mechanism'),
    nativeState: platformValue(input.nativeState, 'Replication link native state'),
    health: validateReplicationHealth(input.health),
    position: validateReplicationPosition(input.position),
    checkpoint: object(input.checkpoint, 'Replication link checkpoint'),
    errors: array(input.errors ?? [], 'Replication link errors',
      (error) => platformValue(error, 'Replication link error', 65536), 1000),
    nativeDetails: object(input.nativeDetails, 'Replication link native details')});
}

export function validateLagSample(input) {
  plainObject(input, 'Replication lag sample'); noRawSecrets(input, 'Replication lag sample');
  return immutable({schema: 'cdeadmin.replication-lag-sample.v1',
    id: platformValue(input.id, 'Replication lag sample ID'),
    linkId: platformValue(input.linkId, 'Replication lag link ID'),
    observedAt: platformValue(input.observedAt, 'Replication lag observation time'),
    unit: platformValue(input.unit, 'Replication lag unit'),
    value: finite(input.value, 'Replication lag value'),
    source: platformValue(input.source, 'Replication lag source'),
    calculation: object(input.calculation, 'Replication lag calculation'),
    nativeDetails: object(input.nativeDetails, 'Replication lag native details')});
}

export function validateTopologyEvent(input) {
  plainObject(input, 'Replication topology event'); noRawSecrets(input, 'Replication event');
  return immutable({schema: 'cdeadmin.replication-event.v1',
    id: platformValue(input.id, 'Replication event ID'),
    topologyId: platformValue(input.topologyId, 'Replication event topology ID'),
    participantId: optionalText(input.participantId, 'Replication event participant ID'),
    linkId: optionalText(input.linkId, 'Replication event link ID'),
    type: platformValue(input.type, 'Replication event type'),
    occurredAt: platformValue(input.occurredAt, 'Replication event time'),
    cause: optionalText(input.cause, 'Replication event cause', 65536),
    evidence: object(input.evidence, 'Replication event evidence'),
    nativeDetails: object(input.nativeDetails, 'Replication event native details')});
}

export function validateReplicationTopology(input) {
  plainObject(input, 'Replication topology'); noRawSecrets(input, 'Replication topology');
  const participants = unique(input.participants, 'Replication participants', validateParticipant);
  const groups = unique(input.groups, 'Replication groups', validateReplicaGroup);
  const links = unique(input.links, 'Replication links', validateReplicationLink);
  const participantIds = new Set(participants.map((item) => item.id));
  const groupIds = new Set(groups.map((item) => item.id));
  participants.forEach((item) => {
    if(item.groupId && !groupIds.has(item.groupId)) throw new TypeError(
      `Replication participant ${item.id} references unknown group ${item.groupId}.`
    );
  });
  groups.forEach((group) => group.memberIds.forEach((id) => {
    if(!participantIds.has(id)) throw new TypeError(
      `Replication group ${group.id} references unknown participant ${id}.`
    );
  }));
  links.forEach((link) => {
    if(!participantIds.has(link.sourceParticipantId) ||
        !participantIds.has(link.targetParticipantId)) throw new TypeError(
      `Replication link ${link.id} has a dangling participant.`
    );
  });
  const lagSamples = unique(input.lagSamples, 'Replication lag samples', validateLagSample, 100000);
  lagSamples.forEach((sample) => {
    if(!links.some((link) => link.id === sample.linkId)) throw new TypeError(
      `Replication lag sample ${sample.id} references unknown link ${sample.linkId}.`
    );
  });
  return immutable({schema: 'cdeadmin.replication-topology.v1',
    id: platformValue(input.id, 'Replication topology ID'),
    name: platformValue(input.name ?? input.id, 'Replication topology name'),
    scopeRef: validateReplicationReference(input.scopeRef, 'Replication topology scope'),
    providerId: platformValue(input.providerId, 'Replication topology provider ID'),
    revision: platformValue(input.revision, 'Replication topology revision'),
    participants, groups, links, lagSamples,
    conflictPolicy: object(input.conflictPolicy, 'Replication conflict policy'),
    healthPolicy: object(input.healthPolicy, 'Replication health policy'),
    events: unique(input.events, 'Replication events', validateTopologyEvent, 100000),
    nativeDetails: object(input.nativeDetails, 'Replication topology native details')});
}

export function validateAlertPolicy(input) {
  plainObject(input, 'Replication alert policy'); noRawSecrets(input, 'Replication alert policy');
  return immutable({schema: 'cdeadmin.replication-alert-policy.v1',
    id: platformValue(input.id, 'Replication alert policy ID'),
    topologyId: platformValue(input.topologyId, 'Replication alert topology ID'),
    linkId: optionalText(input.linkId, 'Replication alert link ID'),
    metric: platformValue(input.metric, 'Replication alert metric'),
    maximum: finite(input.maximum, 'Replication alert maximum'),
    unit: platformValue(input.unit, 'Replication alert unit'),
    enabled: boolean(input.enabled, 'Replication alert enabled', true),
    nativeDetails: object(input.nativeDetails, 'Replication alert native details')});
}

function validatePlanItem(input, label) {
  plainObject(input, label); noRawSecrets(input, label);
  const targetRef = input.targetRef ? validateReplicationReference(input.targetRef,
    `${label} target`) : null;
  return immutable({id: platformValue(input.id, `${label} ID`),
    label: platformValue(input.label ?? input.id, `${label} label`),
    action: platformValue(input.action, `${label} action`), targetRef,
    parameters: object(input.parameters, `${label} parameters`),
    evidenceRequirements: object(input.evidenceRequirements, `${label} evidence requirements`),
    nativeDetails: object(input.nativeDetails, `${label} native details`)});
}

function validateEstimate(input, label) {
  if(input === undefined || input === null) return null;
  plainObject(input, label); noRawSecrets(input, label);
  return immutable({value: finite(input.value, `${label} value`),
    unit: platformValue(input.unit, `${label} unit`),
    evidence: object(input.evidence, `${label} evidence`)});
}

export function validateFailoverPlan(input) {
  plainObject(input, 'Replication failover plan'); noRawSecrets(input, 'Replication failover plan');
  const preconditions = unique(input.preconditions, 'Failover preconditions',
    (item) => validatePlanItem(item, 'Failover precondition'));
  const commands = unique(input.commands, 'Failover commands',
    (item) => validatePlanItem(item, 'Failover command'));
  const verification = unique(input.verification, 'Failover verification steps',
    (item) => validatePlanItem(item, 'Failover verification'));
  const rollback = unique(input.rollback, 'Failover rollback steps',
    (item) => validatePlanItem(item, 'Failover rollback'));
  if(!preconditions.length || !commands.length || !verification.length || !rollback.length) {
    throw new TypeError('Failover plans require preconditions, commands, verification and rollback.');
  }
  return immutable({schema: 'cdeadmin.replication-failover-plan.v1',
    id: platformValue(input.id, 'Failover plan ID'),
    name: platformValue(input.name ?? input.id, 'Failover plan name'),
    topologyId: platformValue(input.topologyId, 'Failover plan topology ID'),
    candidateParticipantId: platformValue(input.candidateParticipantId,
      'Failover candidate participant ID'),
    targetRole: platformValue(input.targetRole, 'Failover target role'),
    preconditions, commands, expectedTopology: object(input.expectedTopology,
      'Expected failover topology'), verification, rollback,
    dataLossRisk: platformValue(input.dataLossRisk, 'Failover data-loss risk'),
    estimatedRPO: validateEstimate(input.estimatedRPO, 'Failover estimated RPO'),
    estimatedRTO: validateEstimate(input.estimatedRTO, 'Failover estimated RTO'),
    nativeDetails: object(input.nativeDetails, 'Failover plan native details')});
}

export function validateVisualLayout(input) {
  plainObject(input, 'Replication visual layout'); noRawSecrets(input, 'Replication visual layout');
  const positions = object(input.positions, 'Replication visual positions');
  Object.entries(positions).forEach(([id, point]) => {
    platformValue(id, 'Replication visual participant ID'); plainObject(point, 'Visual position');
    finite(point.x, 'Visual position x', {maximum: 10000});
    finite(point.y, 'Visual position y', {maximum: 10000});
  });
  return immutable({schema: 'cdeadmin.replication-layout.v1',
    id: platformValue(input.id, 'Replication layout ID'),
    topologyId: platformValue(input.topologyId, 'Replication layout topology ID'), positions,
    nativeDetails: object(input.nativeDetails, 'Replication layout native details')});
}

export function validateSnapshotRef(input) {
  plainObject(input, 'Replication snapshot reference'); noRawSecrets(input, 'Snapshot reference');
  return immutable({schema: 'cdeadmin.replication-snapshot-ref.v1',
    id: platformValue(input.id, 'Replication snapshot ID'),
    topologyId: platformValue(input.topologyId, 'Replication snapshot topology ID'),
    capturedAt: platformValue(input.capturedAt, 'Replication snapshot capture time'),
    reference: validateReplicationReference(input.reference, 'Replication snapshot reference'),
    nativeDetails: object(input.nativeDetails, 'Replication snapshot native details')});
}

export function createReplicationContent(input={}) {
  plainObject(input, 'Replication asset content'); noRawSecrets(input, 'Replication asset content');
  const content = immutable({schema: REPLICATION_ASSET_SCHEMA, schemaVersion: 1,
    moduleId: REPLICATION_MODULE_ID, name: String(input.name ?? ''),
    description: String(input.description ?? ''),
    savedTopologies: unique(input.savedTopologies, 'Saved replication topologies',
      validateReplicationTopology),
    alertPolicies: unique(input.alertPolicies, 'Replication alert policies', validateAlertPolicy),
    failoverPlans: unique(input.failoverPlans, 'Replication failover plans', validateFailoverPlan),
    visualLayouts: unique(input.visualLayouts, 'Replication visual layouts', validateVisualLayout),
    snapshotRefs: unique(input.snapshotRefs, 'Replication snapshot references', validateSnapshotRef),
    extensions: object(input.extensions, 'Replication extensions')});
  const topologyIds = new Set(content.savedTopologies.map((item) => item.id));
  for(const item of [...content.alertPolicies, ...content.failoverPlans,
    ...content.visualLayouts, ...content.snapshotRefs]) {
    if(!topologyIds.has(item.topologyId)) throw new TypeError(
      `Replication item ${item.id} references unknown topology ${item.topologyId}.`
    );
  }
  content.failoverPlans.forEach((plan) => {
    const topology = content.savedTopologies.find((item) => item.id === plan.topologyId);
    if(!topology.participants.some((item) => item.id === plan.candidateParticipantId)) {
      throw new TypeError(`Failover plan ${plan.id} references an unknown candidate.`);
    }
  });
  content.visualLayouts.forEach((layout) => {
    const topology = content.savedTopologies.find((item) => item.id === layout.topologyId);
    const participantIds = new Set(topology.participants.map((item) => item.id));
    Object.keys(layout.positions).forEach((id) => {
      if(!participantIds.has(id)) throw new TypeError(
        `Replication layout ${layout.id} references unknown participant ${id}.`
      );
    });
  });
  return content;
}

export function exportReplicationContent(input) {
  return JSON.stringify(createReplicationContent(input), null, 2);
}

export function importReplicationContent(text) {
  if(typeof text !== 'string' || text.length > 16 * 1024 * 1024) throw new TypeError(
    'Replication import must be JSON no larger than 16 MiB.'
  );
  try { return createReplicationContent(JSON.parse(text)); } catch(error) {
    throw new TypeError(`external_format_invalid: ${error.message}`);
  }
}

export function replicationAssetRequest(input) {
  plainObject(input, 'Replication asset request');
  const expectedVersion = Number(input.expectedVersion ?? 0);
  if(!Number.isInteger(expectedVersion) || expectedVersion < 0) throw new TypeError(
    'Replication expected version must be a non-negative integer.'
  );
  return immutable({asset_type: REPLICATION_ASSET_TYPE, schema_name: REPLICATION_ASSET_TYPE,
    schema_version: 1, name: platformValue(input.name, 'Replication asset name'),
    path: platformValue(input.path, 'Replication asset path'),
    expected_version: expectedVersion, content: createReplicationContent(input.content)});
}
