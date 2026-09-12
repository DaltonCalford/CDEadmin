/////////////////////////////////////////////////////////////
// Data Contract Manager canonical contracts and serialization.
/////////////////////////////////////////////////////////////

import {immutable, noRawSecrets, plainObject, platformValue} from '../../platform/serviceUtils';

export const CONTRACT_MODULE_ID = 'cdeadmin.contract';
export const CONTRACT_ASSET_TYPE = 'cdeadmin.contract.v1';
export const CONTRACT_ASSET_SCHEMA = 'cdeadmin.contract.asset.v1';

export const CONTRACT_STATUSES = Object.freeze([
  'proposed', 'draft', 'active', 'deprecated', 'retired',
]);
export const CONTRACT_DRIFT_STATES = Object.freeze([
  'in_sync', 'provider_changed', 'contract_changed', 'both_changed',
  'unbound', 'unknown',
]);
export const CONTRACT_COMPLIANCE_DIMENSIONS = Object.freeze([
  'contract_structure', 'resource_binding', 'schema', 'quality',
  'sla_observation', 'security_classification_metadata',
  'documentation_reference_resolution',
]);
export const CONTRACT_STATES = Object.freeze([
  'empty', 'loading', 'ready', 'stale', 'partial', 'permission_denied',
  'disconnected', 'validation_error', 'runtime_failure', 'read_only',
  'background_task_active',
]);

const REFERENCE_SCHEMAS = new Set([
  'cdeadmin.resource-ref.v1', 'cdeadmin.asset-ref.v1',
  'cdeadmin.external-ref.v1', 'cdeadmin.result-ref.v1',
]);

function optionalText(value, label, maximum=4096) {
  if(value === undefined || value === null || value === '') return null;
  return platformValue(value, label, maximum);
}

function object(value, label) {
  const result = plainObject(value ?? {}, label); noRawSecrets(result, label);
  return immutable({...result});
}

function list(value, label, mapper, maximum=10000) {
  if(!Array.isArray(value) || value.length > maximum) throw new TypeError(
    `${label} must be an array containing no more than ${maximum} items.`
  );
  return value.map(mapper);
}

export function validateContractRef(input, label='Contract reference') {
  plainObject(input, label); noRawSecrets(input, label);
  const schema = platformValue(input.schema, `${label} schema`);
  if(!REFERENCE_SCHEMAS.has(schema)) throw new TypeError(`${label} schema is unsupported.`);
  const identity = schema === 'cdeadmin.resource-ref.v1' ? input.canonical :
    schema === 'cdeadmin.asset-ref.v1' ? `${input.projectId}/${input.assetId}` : input.id;
  platformValue(identity, `${label} identity`, 4096);
  return immutable({...input});
}

export function contractReferenceKey(input) {
  const ref = validateContractRef(input);
  if(ref.schema === 'cdeadmin.resource-ref.v1') return `resource:${ref.canonical}`;
  if(ref.schema === 'cdeadmin.asset-ref.v1') return `asset:${ref.projectId}/${ref.assetId}`;
  return `${ref.schema}:${ref.id}`;
}

function unique(values, mapper, label) {
  const result = list(values ?? [], label, mapper); const ids = new Set();
  result.forEach((item) => {
    const id = item.id ?? contractReferenceKey(item);
    if(ids.has(id)) throw new TypeError(`Duplicate ${label}: ${id}`);
    ids.add(id);
  });
  return result.sort((left, right) => String(left.id ?? contractReferenceKey(left))
    .localeCompare(String(right.id ?? contractReferenceKey(right))));
}

export function validateContractElement(input) {
  plainObject(input, 'Contract element'); noRawSecrets(input, 'Contract element');
  return immutable({schema: 'cdeadmin.contract-element.v1',
    id: platformValue(input.id, 'Contract element ID'),
    name: platformValue(input.name, 'Contract element name'),
    logicalType: platformValue(input.logicalType, 'Contract element logical type'),
    parentId: optionalText(input.parentId, 'Contract element parent ID'),
    description: String(input.description ?? ''), required: Boolean(input.required),
    classification: optionalText(input.classification, 'Contract element classification'),
    constraints: object(input.constraints, 'Contract element constraints'),
    physicalDefinition: object(input.physicalDefinition, 'Contract physical definition'),
    authoritativeDefinitionRefs: unique(input.authoritativeDefinitionRefs ?? [],
      (item) => validateContractRef(item, 'Contract authoritative definition reference'),
      'Contract authoritative definition reference'),
    extensions: object(input.extensions, 'Contract element extensions')});
}

export function validateResourceBinding(input) {
  plainObject(input, 'Contract resource binding'); noRawSecrets(input, 'Contract resource binding');
  return immutable({schema: 'cdeadmin.contract-binding.v1',
    id: platformValue(input.id, 'Contract binding ID'),
    elementId: platformValue(input.elementId, 'Contract binding element ID'),
    targetRef: validateContractRef(input.targetRef, 'Contract binding target'),
    environment: platformValue(input.environment, 'Contract binding environment'),
    bindingStatus: platformValue(input.bindingStatus, 'Contract binding status'),
    observedRevision: optionalText(input.observedRevision, 'Observed resource revision'),
    nativeDetails: object(input.nativeDetails, 'Contract binding native details')});
}

export function validateQualityObligation(input) {
  plainObject(input, 'Contract quality obligation');
  noRawSecrets(input, 'Contract quality obligation');
  const qualityRef = input.qualityRef ? validateContractRef(
    input.qualityRef, 'Quality obligation reference'
  ) : null;
  const importedDefinition = input.importedDefinition === undefined ||
    input.importedDefinition === null ? null :
    object(input.importedDefinition, 'Imported quality definition');
  if(!qualityRef && !importedDefinition) throw new TypeError(
    'Quality obligation requires a quality reference or imported definition.'
  );
  return immutable({schema: 'cdeadmin.contract-quality-obligation.v1',
    id: platformValue(input.id, 'Quality obligation ID'),
    elementId: optionalText(input.elementId, 'Quality obligation element ID'),
    qualityRef, importedDefinition,
    severity: platformValue(input.severity, 'Quality obligation severity'),
    threshold: object(input.threshold, 'Quality obligation threshold'),
    description: String(input.description ?? ''),
    extensions: object(input.extensions, 'Quality obligation extensions')});
}

export function validateServiceLevel(input) {
  plainObject(input, 'Contract service level'); noRawSecrets(input, 'Contract service level');
  if(!['string', 'number', 'boolean'].includes(typeof input.target) ||
      (typeof input.target === 'number' && !Number.isFinite(input.target))) {
    throw new TypeError('Service-level target must be a finite scalar value.');
  }
  return immutable({schema: 'cdeadmin.contract-service-level.v1',
    id: platformValue(input.id, 'Service-level ID'),
    measure: platformValue(input.measure, 'Service-level measure'),
    target: input.target, comparison: platformValue(input.comparison, 'Service-level comparison'),
    unit: optionalText(input.unit, 'Service-level unit'),
    window: object(input.window, 'Service-level window'),
    elementId: optionalText(input.elementId, 'Service-level element ID'),
    description: String(input.description ?? ''),
    extensions: object(input.extensions, 'Service-level extensions')});
}

function validatePrincipalRole(input, label) {
  plainObject(input, label); noRawSecrets(input, label);
  return immutable({schema: label === 'Contract team role' ?
    'cdeadmin.contract-team-role.v1' : 'cdeadmin.contract-access-role.v1',
  id: platformValue(input.id, `${label} ID`), name: platformValue(input.name, `${label} name`),
  principalRefs: unique(input.principalRefs ?? [],
    (item) => validateContractRef(item, `${label} principal`), `${label} principal`),
  responsibilities: [...new Set(list(input.responsibilities ?? [], `${label} responsibilities`,
    (item) => platformValue(item, `${label} responsibility`)))].sort(),
  accessExpectations: [...new Set(list(input.accessExpectations ?? [],
    `${label} access expectations`,
    (item) => platformValue(item, `${label} access expectation`)))].sort(),
  support: object(input.support, `${label} support`),
  extensions: object(input.extensions, `${label} extensions`)});
}

export const validateTeamRole = (input) => validatePrincipalRole(input, 'Contract team role');
export const validateAccessRole = (input) => validatePrincipalRole(input, 'Contract access role');

export function validateServerBinding(input) {
  plainObject(input, 'Contract server binding'); noRawSecrets(input, 'Contract server binding');
  const resourceRef = input.resourceRef ? validateContractRef(
    input.resourceRef, 'Contract server resource'
  ) : null;
  const apiRef = input.apiRef ? validateContractRef(input.apiRef, 'Contract server API') : null;
  if(!resourceRef && !apiRef) throw new TypeError(
    'Contract server binding requires a resource or API reference.'
  );
  return immutable({schema: 'cdeadmin.contract-server-binding.v1',
    id: platformValue(input.id, 'Contract server binding ID'),
    providerId: platformValue(input.providerId, 'Contract server provider ID'),
    environment: platformValue(input.environment, 'Contract server environment'),
    interface: platformValue(input.interface, 'Contract server interface'),
    resourceRef, apiRef, nativeDetails: object(input.nativeDetails, 'Contract server native details')});
}

export function validateAuthoritativeDefinition(input) {
  plainObject(input, 'Authoritative definition'); noRawSecrets(input, 'Authoritative definition');
  const assetRef = input.assetRef ? validateContractRef(
    input.assetRef, 'Authoritative definition asset'
  ) : null;
  const uri = optionalText(input.uri, 'Authoritative definition URI', 8192);
  if(!assetRef && !uri) throw new TypeError(
    'Authoritative definition requires an AssetRef or URI.'
  );
  return immutable({schema: 'cdeadmin.contract-authoritative-definition.v1',
    id: platformValue(input.id, 'Authoritative definition ID'),
    type: platformValue(input.type, 'Authoritative definition type'),
    uri, assetRef, description: String(input.description ?? ''),
    extensions: object(input.extensions, 'Authoritative definition extensions')});
}

export function createContractContent(input={}) {
  plainObject(input, 'Data Contract asset content'); noRawSecrets(input, 'Data Contract content');
  const status = input.status ?? 'proposed';
  if(!CONTRACT_STATUSES.includes(status)) throw new TypeError(`Invalid contract status: ${status}`);
  const elements = unique(input.elements ?? [], validateContractElement, 'Contract element');
  const elementIds = new Set(elements.map((item) => item.id));
  elements.forEach((item) => {
    if(item.parentId && !elementIds.has(item.parentId)) throw new TypeError(
      `Contract element ${item.id} has unknown parent ${item.parentId}.`
    );
    if(item.parentId === item.id) throw new TypeError(`Contract element ${item.id} cannot parent itself.`);
  });
  const bindings = unique(input.bindings ?? [], validateResourceBinding, 'Contract binding');
  const qualityObligations = unique(input.qualityObligations ?? [], validateQualityObligation,
    'Contract quality obligation');
  const sla = unique(input.sla ?? [], validateServiceLevel, 'Contract service level');
  [...bindings, ...qualityObligations, ...sla].forEach((item) => {
    if(item.elementId && !elementIds.has(item.elementId)) throw new TypeError(
      `${item.id} references unknown contract element ${item.elementId}.`
    );
  });
  return immutable({schema: CONTRACT_ASSET_SCHEMA, schemaVersion: 1,
    moduleId: CONTRACT_MODULE_ID,
    contractVersion: platformValue(input.contractVersion ?? '0.1.0', 'Contract version'),
    status, name: String(input.name ?? ''), domain: String(input.domain ?? ''),
    description: String(input.description ?? ''),
    elements, bindings, qualityObligations, sla,
    team: unique(input.team ?? [], validateTeamRole, 'Contract team role'),
    roles: unique(input.roles ?? [], validateAccessRole, 'Contract access role'),
    servers: unique(input.servers ?? [], validateServerBinding, 'Contract server binding'),
    authoritativeDefinitions: unique(input.authoritativeDefinitions ?? [],
      validateAuthoritativeDefinition, 'Authoritative definition'),
    extensions: object(input.extensions, 'Data Contract extensions')});
}

function canonical(value) {
  if(Array.isArray(value)) return value.map(canonical);
  if(!value || typeof value !== 'object') return value;
  return Object.fromEntries(Object.keys(value).sort().map((key) => [key, canonical(value[key])]));
}

export function exportContractAsset(input) {
  return `${JSON.stringify(canonical(createContractContent(input)), null, 2)}\n`;
}

export function importContractAsset(text) {
  if(typeof text !== 'string' || text.length > 16 * 1024 * 1024) throw new TypeError(
    'Data Contract source is invalid or exceeds 16 MiB.'
  );
  try { return createContractContent(JSON.parse(text)); }
  catch(error) { throw new TypeError(`Invalid Data Contract source: ${error.message}`); }
}

export function contractAssetRequest({projectId, assetId, name, path,
  expectedVersion=0, content}) {
  const canonicalContent = createContractContent(content);
  const referenced = [
    ...canonicalContent.qualityObligations.flatMap((item) => item.qualityRef ? [item.qualityRef] : []),
    ...canonicalContent.authoritativeDefinitions.flatMap((item) => item.assetRef ? [item.assetRef] : []),
    ...canonicalContent.elements.flatMap((item) => item.authoritativeDefinitionRefs),
    ...canonicalContent.servers.flatMap((item) => [item.resourceRef, item.apiRef].filter(Boolean)),
  ];
  const dependencies = [...new Map(referenced.filter((item) =>
    item.schema === 'cdeadmin.asset-ref.v1').map((item) =>
    [contractReferenceKey(item), item])).values()];
  const resourceBindings = [...new Map([
    ...canonicalContent.bindings.map((item) => item.targetRef),
    ...canonicalContent.servers.map((item) => item.resourceRef).filter(Boolean),
  ].filter((item) => item.schema === 'cdeadmin.resource-ref.v1').map((item) =>
    [contractReferenceKey(item), item])).values()];
  return immutable({asset_type: CONTRACT_ASSET_TYPE,
    name: platformValue(name, 'Asset name'), path,
    schema_name: CONTRACT_ASSET_TYPE, schema_version: 1,
    expected_version: expectedVersion, content: canonicalContent,
    metadata: {moduleId: CONTRACT_MODULE_ID, contractVersion: canonicalContent.contractVersion},
    dependency_references: dependencies,
    resource_bindings: resourceBindings,
    validation_state: 'valid', validation_details: [], projectId, assetId});
}
