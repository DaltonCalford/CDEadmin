/////////////////////////////////////////////////////////////
// Data Contract structural validation, compliance and version diff.
/////////////////////////////////////////////////////////////

import {immutable, plainObject, platformValue} from '../../platform/serviceUtils';
import {
  CONTRACT_COMPLIANCE_DIMENSIONS, CONTRACT_DRIFT_STATES,
  CONTRACT_STATUSES, createContractContent,
} from './contracts';

function result(dimension, compliant, details=[], evidence=[]) {
  if(!CONTRACT_COMPLIANCE_DIMENSIONS.includes(dimension)) throw new TypeError(
    `Unknown contract compliance dimension: ${dimension}`
  );
  if(compliant !== true && compliant !== false && compliant !== null) throw new TypeError(
    'Contract compliance result must be true, false or unknown.'
  );
  return immutable({dimension, compliant,
    details: details.map(String), evidence: evidence.map((item) => immutable({...item}))});
}

export function validateContractStructure(input) {
  const content = createContractContent(input); const details = []; const warnings = [];
  if(!content.name.trim()) details.push('Contract name is required.');
  if(!content.domain.trim()) details.push('Contract domain is required.');
  if(!content.description.trim()) details.push('Contract description is required.');
  if(!content.elements.length) details.push('At least one logical contract element is required.');
  const parents = new Map(content.elements.map((item) => [item.id, item.parentId]));
  content.elements.forEach((item) => {
    const visited = new Set([item.id]); let parent = item.parentId;
    while(parent) {
      if(visited.has(parent)) { details.push(`Contract element hierarchy contains a cycle at ${item.id}.`); break; }
      visited.add(parent); parent = parents.get(parent);
    }
  });
  const bindingKeys = new Set();
  content.bindings.forEach((binding) => {
    const key = `${binding.elementId}\u0000${binding.environment}`;
    if(bindingKeys.has(key)) details.push(
      `Element ${binding.elementId} has more than one ${binding.environment} binding.`
    );
    bindingKeys.add(key);
    if(!binding.observedRevision) warnings.push(
      `Binding ${binding.id} has no observed live-resource revision.`
    );
  });
  if(content.status === 'active' && !content.bindings.length) warnings.push(
    'Active logical-only contract has no live resource binding.'
  );
  return immutable({schema: 'cdeadmin.contract-validation.v1', valid: !details.length,
    details: [...new Set(details)].sort(), warnings: [...new Set(warnings)].sort(),
    contractVersion: content.contractVersion});
}

export function nextStatus(current, requested, {reason=''}={}) {
  if(!CONTRACT_STATUSES.includes(current) || !CONTRACT_STATUSES.includes(requested)) {
    throw new TypeError('Contract lifecycle status is invalid.');
  }
  if(current === requested) return immutable({status: requested, direction: 'unchanged'});
  const from = CONTRACT_STATUSES.indexOf(current); const to = CONTRACT_STATUSES.indexOf(requested);
  if(to < from && !String(reason).trim()) throw new TypeError(
    'A reverse contract lifecycle transition requires an audit reason.'
  );
  return immutable({status: requested, direction: to > from ? 'forward' : 'reverse',
    reason: String(reason).trim()});
}

export function normalizeDrift(input) {
  plainObject(input, 'Contract drift result');
  const state = platformValue(input.state, 'Contract drift state');
  if(!CONTRACT_DRIFT_STATES.includes(state)) throw new TypeError(`Invalid contract drift state: ${state}`);
  if(!Array.isArray(input.differences)) throw new TypeError('Contract drift requires differences[].');
  return immutable({schema: 'cdeadmin.contract-drift.v1', state,
    differences: input.differences.slice(0, 10000).map((item) => immutable({
      path: platformValue(item.path, 'Contract drift path', 4096),
      kind: platformValue(item.kind, 'Contract drift kind'),
      contractValue: item.contractValue, providerValue: item.providerValue,
      evidence: immutable({...plainObject(item.evidence ?? {}, 'Contract drift evidence')})})),
    observedRevision: input.observedRevision ? String(input.observedRevision) : null});
}

export function summarizeCompliance({structure, bindingResults=[], schemaResults=[],
  qualityResults=[], slaResults=[], classificationResults=[], documentationResults=[]}) {
  const dimensions = [
    result('contract_structure', structure.valid, structure.details),
    result('resource_binding', bindingResults.length ?
      bindingResults.every((item) => item.compliant === true) : null,
    bindingResults.flatMap((item) => item.details ?? [])),
    result('schema', schemaResults.length ? schemaResults.every((item) =>
      item.compliant === true) : null, schemaResults.flatMap((item) => item.details ?? []),
    schemaResults.flatMap((item) => item.evidence ?? [])),
    result('quality', qualityResults.length ? qualityResults.every((item) =>
      item.compliant === true) : null, qualityResults.flatMap((item) => item.details ?? []),
    qualityResults.flatMap((item) => item.evidence ?? [])),
    result('sla_observation', slaResults.length ? slaResults.every((item) =>
      item.compliant === true) : null, slaResults.flatMap((item) => item.details ?? []),
    slaResults.flatMap((item) => item.evidence ?? [])),
    result('security_classification_metadata', classificationResults.length ?
      classificationResults.every((item) => item.compliant === true) : null,
    classificationResults.flatMap((item) => item.details ?? []),
    classificationResults.flatMap((item) => item.evidence ?? [])),
    result('documentation_reference_resolution', documentationResults.length ?
      documentationResults.every((item) => item.compliant === true) : null,
    documentationResults.flatMap((item) => item.details ?? []),
    documentationResults.flatMap((item) => item.evidence ?? [])),
  ];
  const known = dimensions.filter((item) => item.compliant !== null);
  const compliant = known.length > 0 && known.every((item) => item.compliant);
  return immutable({schema: 'cdeadmin.contract-compliance-summary.v1', compliant,
    partial: known.length !== dimensions.length, dimensions});
}

function flatten(value, path='', output=new Map()) {
  if(value && typeof value === 'object') {
    if(Array.isArray(value)) value.forEach((item, index) => flatten(item, `${path}/${index}`, output));
    else Object.keys(value).sort().forEach((key) => flatten(value[key], `${path}/${key}`, output));
  } else output.set(path || '/', value);
  return output;
}

export function compareContractVersions(leftInput, rightInput) {
  const left = createContractContent(leftInput); const right = createContractContent(rightInput);
  const leftValues = flatten(left); const rightValues = flatten(right);
  const paths = [...new Set([...leftValues.keys(), ...rightValues.keys()])].sort();
  const differences = paths.filter((path) => !Object.is(leftValues.get(path), rightValues.get(path)))
    .slice(0, 10000).map((path) => immutable({path,
      state: !leftValues.has(path) ? 'right_only' : !rightValues.has(path) ? 'left_only' : 'changed',
      left: leftValues.get(path), right: rightValues.get(path)}));
  return immutable({schema: 'cdeadmin.contract-version-diff.v1',
    leftVersion: left.contractVersion, rightVersion: right.contractVersion,
    identical: differences.length === 0, differences});
}
