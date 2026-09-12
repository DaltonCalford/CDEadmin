/////////////////////////////////////////////////////////////
// Deterministic, provider-honest Schema Comparison engine.
/////////////////////////////////////////////////////////////

import {immutable, plainObject, platformValue} from '../../platform/serviceUtils';
import {
  DIFF_CLASSIFICATIONS, EQUIVALENCE_CATEGORIES, referenceKey,
  validateMapping, validateReference, validateSchemaSnapshot,
} from './contracts';

const MATCH_REASONS = Object.freeze({
  RESOURCE: 'stable_resource_identity', NATIVE: 'provider_native_stable_id',
  MAPPING: 'accepted_mapping', NAME_KIND: 'qualified_name_and_compatible_kind',
});

function canonical(value) {
  if(Array.isArray(value)) return value.map(canonical);
  if(value && typeof value === 'object') return Object.keys(value).sort()
    .reduce((result, key) => ({...result, [key]: canonical(value[key])}), {});
  return value;
}

function equal(left, right) {
  return JSON.stringify(canonical(left)) === JSON.stringify(canonical(right));
}

function objectResourceKey(object) {
  return object.resourceRef ? referenceKey(object.resourceRef) : '';
}

function diffId(left, right) {
  return `diff:${encodeURIComponent(left?.id ?? '-')}:${encodeURIComponent(right?.id ?? '-')}`;
}

function compatibleKind(left, right) { return left.kind === right.kind; }

function classify(left, right, nativeComparison={}) {
  if(!compatibleKind(left, right)) return 'incompatible';
  if(nativeComparison.comparable === false) return 'uncomparable';
  if(nativeComparison.classification &&
      DIFF_CLASSIFICATIONS.includes(nativeComparison.classification)) {
    return nativeComparison.classification;
  }
  const normalizedEqual = equal(left.normalized, right.normalized);
  const nativeEqual = equal(left.native, right.native);
  if(normalizedEqual && nativeEqual && left.qualifiedName === right.qualifiedName) {
    return 'identical';
  }
  if(normalizedEqual) return left.parentId !== right.parentId ?
    'moved' : 'semantically_equivalent';
  if(left.parentId !== right.parentId &&
      left.displayName === right.displayName) return 'moved';
  return 'changed';
}

function candidateEvidence(left, right) {
  const evidence = [];
  if(left.displayName === right.displayName) evidence.push('same_display_name');
  const leftLeaf = left.qualifiedName.split('.').pop();
  const rightLeaf = right.qualifiedName.split('.').pop();
  if(leftLeaf === rightLeaf) evidence.push('same_unqualified_name');
  if(equal(left.normalized, right.normalized)) evidence.push('same_normalized_definition');
  return evidence;
}

function makeDiff(left, right, matchReason, nativeComparison={}) {
  const classification = left && right ? classify(left, right, nativeComparison) :
    left ? 'left_only' : 'right_only';
  return immutable({
    schema: 'cdeadmin.schema-compare.diff-item.v1', id: diffId(left, right),
    classification, leftId: left?.id ?? null, rightId: right?.id ?? null,
    kind: left?.kind ?? right?.kind ?? 'unknown',
    qualifiedName: left?.qualifiedName ?? right?.qualifiedName ?? '',
    matchReason: matchReason ?? '',
    normalizedEqual: Boolean(left && right && equal(left.normalized, right.normalized)),
    nativeEqual: Boolean(left && right && equal(left.native, right.native)),
    nativeDetails: nativeComparison.nativeDetails ?? {},
    warnings: [...(nativeComparison.warnings ?? [])],
  });
}

function uniqueMatch(leftItems, rightItems, predicate, reason, matches, usedLeft,
  usedRight) {
  for(const left of leftItems) {
    if(usedLeft.has(left.id)) continue;
    const candidates = rightItems.filter((right) => !usedRight.has(right.id) &&
      predicate(left, right));
    if(candidates.length !== 1) continue;
    const right = candidates[0];
    matches.push({left, right, reason});
    usedLeft.add(left.id); usedRight.add(right.id);
  }
}

function validateComparisonResponse(value) {
  if(value === undefined || value === null) return {};
  plainObject(value, 'Native property comparison');
  if(value.classification && !DIFF_CLASSIFICATIONS.includes(value.classification)) {
    throw new TypeError('Provider returned an invalid diff classification.');
  }
  return value;
}

export async function compareSnapshots(leftInput, rightInput, {
  acceptedMappings=[], compareNativeProperties=async () => ({}), signal,
}={}) {
  const left = validateSchemaSnapshot(leftInput);
  const right = validateSchemaSnapshot(rightInput);
  const mappings = acceptedMappings.map(validateMapping).filter((item) => item.accepted);
  const leftItems = [...left.objects].sort((a, b) => a.id.localeCompare(b.id));
  const rightItems = [...right.objects].sort((a, b) => a.id.localeCompare(b.id));
  const matches = []; const usedLeft = new Set(); const usedRight = new Set();

  uniqueMatch(leftItems, rightItems,
    (a, b) => objectResourceKey(a) && objectResourceKey(a) === objectResourceKey(b),
    MATCH_REASONS.RESOURCE, matches, usedLeft, usedRight);
  if(left.providerId === right.providerId) uniqueMatch(leftItems, rightItems,
    (a, b) => a.nativeId && a.nativeId === b.nativeId,
    MATCH_REASONS.NATIVE, matches, usedLeft, usedRight);

  for(const mapping of mappings.sort((a, b) => a.mappingId.localeCompare(b.mappingId))) {
    const leftObject = leftItems.find((item) => item.id === mapping.leftId);
    const rightObject = rightItems.find((item) => item.id === mapping.rightId);
    if(!leftObject || !rightObject) throw new TypeError(
      `Accepted mapping ${mapping.mappingId} refers to a missing object.`
    );
    if(usedLeft.has(leftObject.id) || usedRight.has(rightObject.id)) continue;
    matches.push({left: leftObject, right: rightObject, reason: MATCH_REASONS.MAPPING});
    usedLeft.add(leftObject.id); usedRight.add(rightObject.id);
  }

  uniqueMatch(leftItems, rightItems,
    (a, b) => a.qualifiedName === b.qualifiedName && compatibleKind(a, b),
    MATCH_REASONS.NAME_KIND, matches, usedLeft, usedRight);

  const differences = [];
  for(const match of matches) {
    if(signal?.aborted) throw new DOMException('Comparison cancelled.', 'AbortError');
    const native = validateComparisonResponse(await compareNativeProperties(
      match.left, match.right, {leftSnapshot: left, rightSnapshot: right, signal}
    ));
    differences.push(makeDiff(match.left, match.right, match.reason, native));
  }
  const unmatchedLeft = leftItems.filter((item) => !usedLeft.has(item.id));
  const unmatchedRight = rightItems.filter((item) => !usedRight.has(item.id));
  unmatchedLeft.forEach((item) => differences.push(makeDiff(item, null)));
  unmatchedRight.forEach((item) => differences.push(makeDiff(null, item)));

  const renameCandidates = [];
  unmatchedLeft.forEach((leftObject) => unmatchedRight
    .filter((rightObject) => compatibleKind(leftObject, rightObject))
    .forEach((rightObject) => {
      const evidence = candidateEvidence(leftObject, rightObject);
      if(!evidence.length) return;
      renameCandidates.push(immutable({
        schema: 'cdeadmin.schema-compare.rename-candidate.v1',
        id: `rename:${encodeURIComponent(leftObject.id)}:${encodeURIComponent(rightObject.id)}`,
        leftId: leftObject.id, rightId: rightObject.id, kind: leftObject.kind,
        evidence, accepted: false,
      }));
    }));
  differences.sort((a, b) => a.qualifiedName.localeCompare(b.qualifiedName) ||
    a.id.localeCompare(b.id));
  renameCandidates.sort((a, b) => a.id.localeCompare(b.id));
  const counts = DIFF_CLASSIFICATIONS.reduce((result, name) => ({
    ...result, [name]: differences.filter((item) => item.classification === name).length,
  }), {});
  return immutable({
    schema: 'cdeadmin.schema-compare.result.v1', version: 1,
    leftSnapshotId: left.snapshotId, rightSnapshotId: right.snapshotId,
    leftRevision: left.revision, rightRevision: right.revision,
    differences, renameCandidates, counts,
    supportState: left.supportState === 'unsupported' || right.supportState === 'unsupported' ?
      'unsupported' : left.supportState === 'partial' || right.supportState === 'partial' ?
        'partial' : 'supported_native',
    warnings: [...left.warnings, ...right.warnings],
  });
}

function targetSideFor(result, targetRef, leftSnapshot, rightSnapshot) {
  targetRef = validateReference(targetRef, 'Change-plan target');
  const target = referenceKey(targetRef);
  if(target === referenceKey(leftSnapshot.sourceRef)) return 'left';
  if(target === referenceKey(rightSnapshot.sourceRef)) return 'right';
  throw new TypeError('Change-plan target must exactly identify the left or right source.');
}

function operationFor(diff, sourceObject, targetObject, targetSide) {
  const sourceOnly = targetSide === 'right' ? 'left_only' : 'right_only';
  const targetOnly = targetSide === 'right' ? 'right_only' : 'left_only';
  let action = 'alter';
  if(diff.classification === sourceOnly) action = 'create';
  else if(diff.classification === targetOnly) action = 'drop';
  else if(diff.classification === 'moved') action = 'move';
  if(['identical', 'semantically_equivalent'].includes(diff.classification)) return null;
  const object = action === 'drop' ? targetObject : sourceObject;
  if(!object) return null;
  const destructive = action === 'drop' || diff.classification === 'incompatible';
  return {
    schema: 'cdeadmin.schema-compare.change-operation.v1',
    id: `operation:${diff.id}:${targetSide}`, diffId: diff.id, action,
    kind: object.kind, objectId: object.id, qualifiedName: object.qualifiedName,
    sourceObjectId: sourceObject?.id ?? null, targetObjectId: targetObject?.id ?? null,
    dependencies: [], destructive, risk: destructive ? 'high' :
      action === 'alter' || action === 'move' ? 'medium' : 'low',
    reversible: action === 'create' || action === 'move',
    nativeStatement: '', nativeDetails: {}, warnings: [],
  };
}

function topologicalOrder(operations) {
  const byId = new Map(operations.map((item) => [item.id, item]));
  const visiting = new Set(); const visited = new Set(); const result = [];
  const visit = (id) => {
    if(visited.has(id)) return;
    if(visiting.has(id)) throw new TypeError(`Change-plan dependency cycle at ${id}.`);
    visiting.add(id);
    const operation = byId.get(id);
    operation.dependencies.filter((dependency) => byId.has(dependency))
      .sort().forEach(visit);
    visiting.delete(id); visited.add(id); result.push(operation);
  };
  [...byId.keys()].sort().forEach(visit);
  return result;
}

export async function generateChangePlan(result, leftInput, rightInput, {
  targetRef, selectedDiffIds=null, renderChangeOperation, signal,
}={}) {
  plainObject(result, 'Comparison result');
  const left = validateSchemaSnapshot(leftInput);
  const right = validateSchemaSnapshot(rightInput);
  if(typeof renderChangeOperation !== 'function') {
    throw new TypeError('Target provider renderChangeOperation adapter is required.');
  }
  const targetSide = targetSideFor(result, targetRef, left, right);
  const selected = selectedDiffIds ? new Set(selectedDiffIds) : null;
  const leftObjects = new Map(left.objects.map((item) => [item.id, item]));
  const rightObjects = new Map(right.objects.map((item) => [item.id, item]));
  const operations = result.differences.filter((item) => !selected || selected.has(item.id))
    .map((diff) => operationFor(diff,
      targetSide === 'right' ? leftObjects.get(diff.leftId) : rightObjects.get(diff.rightId),
      targetSide === 'right' ? rightObjects.get(diff.rightId) : leftObjects.get(diff.leftId),
      targetSide))
    .filter(Boolean);
  const byObject = new Map(operations.map((item) => [item.objectId, item]));
  operations.forEach((operation) => {
    const source = targetSide === 'right' ? leftObjects.get(operation.sourceObjectId) :
      rightObjects.get(operation.sourceObjectId);
    const target = targetSide === 'right' ? rightObjects.get(operation.targetObjectId) :
      leftObjects.get(operation.targetObjectId);
    if(operation.action !== 'drop') operation.dependencies = (source?.dependencies ?? [])
      .map((id) => byObject.get(id)?.id).filter(Boolean);
    else (target?.dependencies ?? []).forEach((id) => {
      const dependencyDrop = byObject.get(id);
      if(dependencyDrop?.action === 'drop') dependencyDrop.dependencies.push(operation.id);
    });
  });
  const ordered = topologicalOrder(operations);
  const rendered = [];
  for(const operation of ordered) {
    if(signal?.aborted) throw new DOMException('Plan generation cancelled.', 'AbortError');
    const output = await renderChangeOperation(immutable({...operation}), {
      targetRef, targetSide, leftSnapshot: left, rightSnapshot: right, signal,
    });
    plainObject(output, `Rendered operation ${operation.id}`);
    rendered.push(immutable({...operation,
      nativeStatement: platformValue(output.nativeStatement,
        `Rendered operation ${operation.id} native statement`, 1000000),
      nativeDetails: output.nativeDetails ?? {}, warnings: output.warnings ?? [],
    }));
  }
  return immutable({
    schema: 'cdeadmin.schema-compare.change-plan.v1', version: 1,
    id: `plan:${result.leftSnapshotId}:${result.rightSnapshotId}:${targetSide}`,
    targetRef: validateReference(targetRef), targetSide,
    sourceSnapshotId: targetSide === 'right' ? left.snapshotId : right.snapshotId,
    targetSnapshotId: targetSide === 'right' ? right.snapshotId : left.snapshotId,
    comparisonRevisions: {left: left.revision, right: right.revision},
    partialSelection: selected !== null, operations: rendered,
    destructive: rendered.some((item) => item.destructive),
    generatedAt: new Date().toISOString(), validation: null,
  });
}

export function equivalenceMapping(input) {
  const category = String(input?.category ?? '');
  if(!EQUIVALENCE_CATEGORIES.includes(category)) {
    throw new TypeError('An explicit equivalence category is required.');
  }
  return validateMapping({...input, accepted: input.accepted === true});
}

export {MATCH_REASONS};
