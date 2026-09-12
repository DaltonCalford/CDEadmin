/////////////////////////////////////////////////////////////
// AI Interface and Discovery normative machine-contract catalogue.
/////////////////////////////////////////////////////////////

import {immutable} from '../../platform/serviceUtils';
import aiManifestDocument from './machine/ai-interface.manifest.json';
import discoveryManifestDocument from './machine/discovery-intelligence.manifest.json';
import aiCommandDocument from './machine/operations/ai-command-catalog.json';
import aiConnectorDocument from './machine/operations/ai-connector-types.json';
import aiPermissionDocument from './machine/operations/ai-permissions.json';
import aiRiskDocument from './machine/operations/ai-risk-classes.json';
import discoveryCommandDocument from './machine/operations/discovery-command-catalog.json';
import discoveryPermissionDocument from './machine/operations/discovery-permissions.json';
import rankingDocument from './machine/operations/discovery-ranking-defaults.json';
import stateMachineDocument from './machine/operations/state-machines.json';

export const AI_INTERFACE_MODULE_ID = 'cdeadmin.ai_interface';
export const DISCOVERY_INTELLIGENCE_MODULE_ID =
  'cdeadmin.discovery_intelligence';

export const AI_INTERFACE_MANIFEST = immutable(aiManifestDocument);
export const DISCOVERY_INTELLIGENCE_MANIFEST = immutable(
  discoveryManifestDocument
);
export const AI_COMMAND_CATALOG = immutable(aiCommandDocument);
export const AI_CONNECTOR_TYPES = immutable(aiConnectorDocument);
export const AI_PERMISSION_CATALOG = immutable(aiPermissionDocument);
export const AI_RISK_CLASSES = immutable(aiRiskDocument);
export const DISCOVERY_COMMAND_CATALOG = immutable(discoveryCommandDocument);
export const DISCOVERY_PERMISSION_CATALOG = immutable(
  discoveryPermissionDocument
);
export const DISCOVERY_RANKING_DEFAULTS = immutable(rankingDocument);
export const AI_DISCOVERY_STATE_MACHINES = immutable(stateMachineDocument);

function fail(message) {
  throw new TypeError(`AI/Discovery contract package is invalid: ${message}`);
}

function uniqueIds(values, label, selector=(value) => value) {
  if(!Array.isArray(values) || values.length === 0) fail(`${label} is empty.`);
  const ids = values.map(selector);
  if(ids.some((id) => typeof id !== 'string' || id.length === 0)) {
    fail(`${label} contains an invalid identity.`);
  }
  if(new Set(ids).size !== ids.length) fail(`${label} contains duplicate identities.`);
  return ids;
}

function exactList(actual, expected, label) {
  if(actual.length !== expected.length || actual.some(
    (value, index) => value !== expected[index]
  )) fail(`${label} does not match its manifest.`);
}

function validateManifest(manifest, expectedId, screens, forms) {
  if(manifest.moduleId !== expectedId || manifest.schemaVersion !== 1 ||
      manifest.specVersion !== '1.0' || manifest.committedFirstParty !== true) {
    fail(`${expectedId} manifest identity/version is invalid.`);
  }
  if(manifest.screenCount !== screens || manifest.formCount !== forms) {
    fail(`${expectedId} screen/form totals are invalid.`);
  }
  uniqueIds(manifest.assetTypes, `${expectedId} asset types`);
  uniqueIds(manifest.commands, `${expectedId} commands`);
  uniqueIds(manifest.permissions, `${expectedId} permissions`);
  uniqueIds(manifest.dependsOn, `${expectedId} dependencies`);
  if(!Array.isArray(manifest.hardRules) || manifest.hardRules.length === 0) {
    fail(`${expectedId} hard rules are absent.`);
  }
}

export function validateAIDiscoveryContractPackage() {
  validateManifest(AI_INTERFACE_MANIFEST, AI_INTERFACE_MODULE_ID, 26, 23);
  validateManifest(
    DISCOVERY_INTELLIGENCE_MANIFEST,
    DISCOVERY_INTELLIGENCE_MODULE_ID,
    38,
    30
  );

  exactList(
    uniqueIds(AI_COMMAND_CATALOG.commands, 'AI command catalogue',
      (command) => command.id),
    AI_INTERFACE_MANIFEST.commands,
    'AI command catalogue'
  );
  exactList(
    uniqueIds(DISCOVERY_COMMAND_CATALOG.commands,
      'Discovery command catalogue', (command) => command.id),
    DISCOVERY_INTELLIGENCE_MANIFEST.commands,
    'Discovery command catalogue'
  );
  exactList(
    uniqueIds(AI_PERMISSION_CATALOG.permissions, 'AI permission catalogue',
      (permission) => permission[0]),
    AI_INTERFACE_MANIFEST.permissions,
    'AI permission catalogue'
  );
  exactList(
    uniqueIds(DISCOVERY_PERMISSION_CATALOG.permissions,
      'Discovery permission catalogue', (permission) => permission[0]),
    DISCOVERY_INTELLIGENCE_MANIFEST.permissions,
    'Discovery permission catalogue'
  );

  const connectorIds = uniqueIds(
    AI_CONNECTOR_TYPES.types,
    'AI connector types',
    (connector) => connector.id
  );
  if(connectorIds.length !== 7) fail('seven AI connector types are required.');
  const risks = uniqueIds(AI_RISK_CLASSES.classes, 'AI risk classes',
    (risk) => risk.id);
  exactList(risks, ['R0', 'R1', 'R2', 'R3', 'R4', 'R5', 'R6', 'R7'],
    'AI risk classes');

  const weightTotal = Object.values(
    DISCOVERY_RANKING_DEFAULTS.balanced.weights
  ).reduce((total, value) => total + value, 0);
  if(Math.abs(weightTotal - 1) > Number.EPSILON * 10) {
    fail('balanced discovery ranking weights do not total 1.00.');
  }
  if(DISCOVERY_RANKING_DEFAULTS.popularity_saturation !== 1000) {
    fail('discovery popularity saturation is invalid.');
  }

  const requiredMachines = {
    ai_connector: 11,
    ai_plan: 10,
    access_request: 11,
    certification: 7,
    data_product: 5,
    index_source: 8,
    index_revision: 6,
  };
  for(const [name, count] of Object.entries(requiredMachines)) {
    const values = uniqueIds(
      AI_DISCOVERY_STATE_MACHINES[name],
      `${name} state machine`
    );
    if(values.length !== count) fail(`${name} state count is invalid.`);
  }

  return immutable({
    specificationVersion: '1.0',
    modules: [AI_INTERFACE_MODULE_ID, DISCOVERY_INTELLIGENCE_MODULE_ID],
    screens: 64,
    forms: 53,
    commands: 96,
    permissions: 26,
    assetTypes: 19,
    connectorTypes: connectorIds.length,
    stateMachines: Object.keys(requiredMachines).length,
  });
}

export const AI_DISCOVERY_CONTRACT_SUMMARY =
  validateAIDiscoveryContractPackage();
