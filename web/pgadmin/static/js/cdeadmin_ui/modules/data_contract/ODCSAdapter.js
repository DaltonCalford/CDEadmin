/////////////////////////////////////////////////////////////
// Loss-aware, version-preserving Open Data Contract Standard bridge.
/////////////////////////////////////////////////////////////

import {immutable, noRawSecrets, plainObject, platformValue} from '../../platform/serviceUtils';
import {createContractContent} from './contracts';

const KNOWN_TOP_LEVEL = new Set([
  'apiVersion', 'kind', 'name', 'version', 'status', 'domain', 'description',
  'schema', 'dataQuality', 'sla', 'team', 'roles', 'servers',
  'authoritativeDefinitions', 'customProperties',
]);
const MAX_ODCS_BYTES = 16 * 1024 * 1024;

function entries(value) { return Array.isArray(value) ? value : value ? [value] : []; }
function id(value, prefix, index) {
  return String(value?.id ?? value?.name ?? `${prefix}-${index + 1}`);
}
function ref(value) {
  if(value?.schema) return value;
  return {schema: 'cdeadmin.external-ref.v1', id: String(value?.id ?? value)};
}

function mapSchema(source) {
  const elements = [];
  entries(source).forEach((item, objectIndex) => {
    const objectId = id(item, 'object', objectIndex);
    elements.push({id: objectId, name: String(item.name ?? objectId),
      logicalType: String(item.logicalType ?? item.type ?? item.kind ?? 'object'),
      description: String(item.description ?? ''), required: Boolean(item.required),
      classification: item.classification ?? null,
      constraints: item.constraints ?? {}, physicalDefinition: item.physicalDefinition ?? {},
      extensions: item.customProperties ?? {}});
    entries(item.properties).forEach((property, propertyIndex) => {
      const propertyId = id(property, `${objectId}-property`, propertyIndex);
      elements.push({id: propertyId, parentId: objectId,
        name: String(property.name ?? propertyId),
        logicalType: String(property.logicalType ?? property.type ?? property.kind ?? 'property'),
        description: String(property.description ?? ''), required: Boolean(property.required),
        classification: property.classification ?? null,
        constraints: property.constraints ?? {},
        physicalDefinition: property.physicalDefinition ?? {},
        extensions: property.customProperties ?? {}});
    });
  });
  return elements;
}

export function importODCS(input) {
  let source;
  try {
    if(typeof input === 'string') {
      if(input.length > MAX_ODCS_BYTES) throw new TypeError('ODCS source exceeds 16 MiB.');
      source = JSON.parse(input);
    } else {
      const serialized = JSON.stringify(input);
      if(!serialized || serialized.length > MAX_ODCS_BYTES) {
        throw new TypeError('ODCS source is invalid or exceeds 16 MiB.');
      }
      source = JSON.parse(serialized);
    }
  } catch(error) {
    throw new TypeError(`Invalid ODCS source: ${error.message}`);
  }
  plainObject(source, 'ODCS document'); noRawSecrets(source, 'ODCS document');
  const apiVersion = platformValue(source.apiVersion, 'ODCS apiVersion');
  const unknown = Object.fromEntries(Object.entries(source).filter(([key]) =>
    !KNOWN_TOP_LEVEL.has(key)));
  const qualityObligations = entries(source.dataQuality).map((item, index) => ({
    id: id(item, 'quality', index), elementId: item.elementId ?? null,
    importedDefinition: {...item}, severity: String(item.severity ?? 'warning'),
    threshold: item.threshold ?? {}, description: String(item.description ?? ''),
  }));
  const sla = entries(source.sla).map((item, index) => ({
    id: id(item, 'sla', index), elementId: item.elementId ?? null,
    measure: String(item.measure ?? item.type ?? 'unspecified'),
    target: item.target ?? item.value ?? '',
    comparison: String(item.comparison ?? item.operator ?? '='), unit: item.unit ?? null,
    window: item.window ?? {}, description: String(item.description ?? ''),
    extensions: item.customProperties ?? {},
  }));
  const principalGroup = (values, prefix) => entries(values).map((item, index) => ({
    id: id(item, prefix, index), name: String(item.name ?? id(item, prefix, index)),
    principalRefs: entries(item.principals ?? item.members).map(ref),
    responsibilities: entries(item.responsibilities).map(String),
    accessExpectations: entries(item.accessExpectations ?? item.access).map(String),
    support: item.support ?? {}, extensions: item.customProperties ?? {},
  }));
  const servers = entries(source.servers).map((item, index) => ({
    id: id(item, 'server', index), providerId: String(item.providerId ?? item.type ?? 'external'),
    environment: String(item.environment ?? 'unspecified'),
    interface: String(item.interface ?? item.type ?? 'unspecified'),
    resourceRef: item.resourceRef ?? null,
    apiRef: item.apiRef ?? (item.url ? {schema: 'cdeadmin.external-ref.v1', id: item.url} : null),
    nativeDetails: item.nativeDetails ?? {},
  }));
  const authoritativeDefinitions = entries(source.authoritativeDefinitions)
    .map((item, index) => ({id: id(item, 'definition', index),
      type: String(item.type ?? 'external'), uri: item.uri ?? item.url ?? null,
      assetRef: item.assetRef ?? null, description: String(item.description ?? ''),
      extensions: item.customProperties ?? {}}));
  const content = createContractContent({name: String(source.name ?? ''),
    contractVersion: String(source.version ?? '0.1.0'), status: source.status ?? 'proposed',
    domain: String(source.domain ?? ''), description: String(source.description ?? ''),
    elements: mapSchema(source.schema), qualityObligations, sla,
    team: principalGroup(source.team, 'team'), roles: principalGroup(source.roles, 'role'),
    servers, authoritativeDefinitions,
    extensions: {...(source.customProperties ?? {}),
      'org.opendatacontractstandard.import': {apiVersion,
        kind: source.kind ?? null, unknownTopLevel: unknown, originalDocument: source}}});
  return immutable({content, interoperability: {format: 'odcs', apiVersion,
    importedKind: source.kind ?? null,
    preservedUnknownFields: Object.keys(unknown).sort()}});
}

function schemaTree(elements) {
  const children = new Map();
  elements.forEach((item) => {
    const key = item.parentId ?? null; const group = children.get(key) ?? [];
    group.push(item); children.set(key, group);
  });
  const map = (item) => ({id: item.id, name: item.name, type: item.logicalType,
    description: item.description, required: item.required,
    ...(item.classification ? {classification: item.classification} : {}),
    ...(Object.keys(item.constraints).length ? {constraints: item.constraints} : {}),
    ...(Object.keys(item.physicalDefinition).length ?
      {physicalDefinition: item.physicalDefinition} : {}),
    ...(Object.keys(item.extensions).length ? {customProperties: item.extensions} : {}),
    ...(children.has(item.id) ? {properties: children.get(item.id).map(map)} : {})});
  return (children.get(null) ?? []).map(map);
}

function identity(value) { return String(value?.id ?? value?.name ?? ''); }

function mergeList(canonical, original) {
  const candidates = entries(original);
  return canonical.map((item, index) => ({...(candidates.find((candidate) =>
    identity(candidate) && identity(candidate) === identity(item)) ?? candidates[index] ?? {}),
  ...item}));
}

function mergeSchema(canonical, original) {
  const candidates = entries(original);
  return canonical.map((item, index) => {
    const prior = candidates.find((candidate) => identity(candidate) &&
      identity(candidate) === identity(item)) ?? candidates[index] ?? {};
    return {...prior, ...item, ...(item.properties ?
      {properties: mergeSchema(item.properties, prior.properties)} : {})};
  });
}

function principalSection(values) {
  return values.map((item) => ({id: item.id, name: item.name,
    principals: item.principalRefs, responsibilities: item.responsibilities,
    accessExpectations: item.accessExpectations, support: item.support,
    customProperties: item.extensions}));
}

function serverSection(values) {
  return values.map((item) => ({id: item.id, providerId: item.providerId,
    environment: item.environment, interface: item.interface,
    resourceRef: item.resourceRef, apiRef: item.apiRef, nativeDetails: item.nativeDetails}));
}

function definitionSection(values) {
  return values.map((item) => ({id: item.id, type: item.type, uri: item.uri,
    assetRef: item.assetRef, description: item.description, customProperties: item.extensions}));
}

export function exportODCS(input, {apiVersion}={}) {
  const content = createContractContent(input);
  const imported = content.extensions['org.opendatacontractstandard.import'] ?? {};
  const version = platformValue(apiVersion ?? imported.apiVersion,
    'ODCS export apiVersion');
  const unknown = plainObject(imported.unknownTopLevel ?? {}, 'ODCS preserved fields');
  const original = plainObject(imported.originalDocument ?? {}, 'ODCS original document');
  noRawSecrets(unknown, 'ODCS preserved fields');
  const customProperties = Object.fromEntries(Object.entries(content.extensions)
    .filter(([key]) => key !== 'org.opendatacontractstandard.import'));
  const canonicalSchema = schemaTree(content.elements);
  const quality = content.qualityObligations.map((item) => item.importedDefinition ?? ({
    id: item.id, elementId: item.elementId, severity: item.severity,
    threshold: item.threshold, description: item.description,
    qualityRef: item.qualityRef}));
  const serviceLevels = content.sla.map((item) => ({id: item.id, elementId: item.elementId,
    measure: item.measure, target: item.target, comparison: item.comparison,
    unit: item.unit, window: item.window, description: item.description,
    customProperties: item.extensions}));
  const document = {...original, ...unknown, apiVersion: version,
    kind: imported.kind ?? 'DataContract', name: content.name,
    version: content.contractVersion, status: content.status, domain: content.domain,
    description: content.description, schema: mergeSchema(canonicalSchema, original.schema),
    dataQuality: mergeList(quality, original.dataQuality),
    sla: mergeList(serviceLevels, original.sla),
    team: mergeList(principalSection(content.team), original.team),
    roles: mergeList(principalSection(content.roles), original.roles),
    servers: mergeList(serverSection(content.servers), original.servers),
    authoritativeDefinitions: mergeList(definitionSection(
      content.authoritativeDefinitions), original.authoritativeDefinitions),
    customProperties,
    'x-cdeadmin-export': {profile: 'cdeadmin.contract.odcs.v1',
      internalSchema: content.schema, preservedUnknownFields: Object.keys(unknown).sort()}};
  noRawSecrets(document, 'ODCS export');
  return immutable({apiVersion: version, profile: 'cdeadmin.contract.odcs.v1', document});
}
