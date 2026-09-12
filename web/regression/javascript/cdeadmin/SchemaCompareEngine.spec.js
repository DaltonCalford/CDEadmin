/////////////////////////////////////////////////////////////
// Deterministic identity, diff and dependency-plan behavior.
/////////////////////////////////////////////////////////////

import {
  MATCH_REASONS, compareSnapshots, generateChangePlan,
} from 'sources/cdeadmin_ui/modules/schema_compare';

function ref(provider, name) {
  return {schema: 'cdeadmin.resource-ref.v1', provider, connection: 'local',
    scope: '/', kind: 'database', nativeIdentity: name,
    canonical: `cde-resource://${provider}/local/%2F/database/${name}`};
}

function object(id, name, overrides={}) {
  return {id, kind: 'table', qualifiedName: name, displayName: name.split('.').pop(),
    normalized: {columns: ['id']}, native: {engine: 'native'}, children: [],
    dependencies: [], ...overrides};
}

function snapshot(id, provider, objects) {
  return {schema: 'cdeadmin.schema-compare.snapshot.v1', schemaVersion: 1,
    snapshotId: id, sourceRef: ref(provider, id), revision: `${id}-revision`,
    capturedAt: '2026-09-11T12:00:00Z', providerId: provider,
    providerVersion: '1', supportState: 'supported_native', evidence: {},
    warnings: [], nativeDetails: {}, objects};
}

describe('Schema Comparison deterministic engine', () => {
  it('uses stable ResourceRef identity before native IDs and qualified names', async () => {
    const identity = ref('firebird', 'shared-object');
    const left = snapshot('left', 'firebird', [object('left-a', 'A', {
      nativeId: 'native-a', resourceRef: identity}), object('left-b', 'B')]);
    const right = snapshot('right', 'firebird', [object('right-a', 'RENAMED', {
      nativeId: 'other', resourceRef: identity}), object('right-b', 'B')]);
    const result = await compareSnapshots(left, right);
    expect(result.differences.find((item) => item.leftId === 'left-a').matchReason)
      .toBe(MATCH_REASONS.RESOURCE);
    expect(result.differences.find((item) => item.leftId === 'left-b').matchReason)
      .toBe(MATCH_REASONS.NAME_KIND);
  });

  it('uses provider-native identity only inside the same provider', async () => {
    const same = await compareSnapshots(snapshot('a', 'firebird', [object('a', 'OLD', {
      nativeId: '42'})]), snapshot('b', 'firebird', [object('b', 'NEW', {nativeId: '42'})]));
    expect(same.differences[0].matchReason).toBe(MATCH_REASONS.NATIVE);
    const cross = await compareSnapshots(snapshot('a', 'firebird', [object('a', 'OLD', {
      nativeId: '42'})]), snapshot('b', 'postgresql', [object('b', 'NEW', {nativeId: '42'})]));
    expect(cross.differences).toHaveLength(2);
  });

  it('honours accepted mappings but never auto-accepts rename candidates', async () => {
    const left = snapshot('left', 'firebird', [object('old', 'PUBLIC.OLD')]);
    const right = snapshot('right', 'firebird', [object('new', 'PUBLIC.NEW')]);
    const candidate = await compareSnapshots(left, right);
    expect(candidate.renameCandidates).toHaveLength(1);
    expect(candidate.renameCandidates[0].accepted).toBe(false);
    expect(candidate.differences.map((item) => item.classification).sort())
      .toEqual(['left_only', 'right_only']);
    const mapped = await compareSnapshots(left, right, {acceptedMappings: [{
      mappingId: 'rename-one', leftId: 'old', rightId: 'new',
      category: 'representational_difference', accepted: true,
    }]});
    expect(mapped.differences).toHaveLength(1);
    expect(mapped.differences[0].matchReason).toBe(MATCH_REASONS.MAPPING);
  });

  it('distinguishes identical, equivalent, changed, incompatible and uncomparable', async () => {
    const left = snapshot('left', 'a', [
      object('same', 'same'), object('equivalent', 'equivalent'),
      object('changed', 'changed'), object('kind', 'kind'), object('native', 'native'),
    ]);
    const right = snapshot('right', 'b', [
      object('same2', 'same'), object('equivalent2', 'equivalent', {native: {other: 1}}),
      object('changed2', 'changed', {normalized: {columns: ['id', 'name']}}),
      object('kind2', 'kind', {kind: 'view'}), object('native2', 'native'),
    ]);
    const result = await compareSnapshots(left, right, {
      acceptedMappings: [{mappingId: 'kind-map', leftId: 'kind', rightId: 'kind2',
        category: 'unsupported_mapping', accepted: true}],
      compareNativeProperties: async (a) => a.id === 'native' ?
        {comparable: false, warnings: ['native property unavailable']} : {},
    });
    expect(Object.fromEntries(result.differences.map((item) =>
      [item.qualifiedName, item.classification]))).toEqual({
      changed: 'changed', equivalent: 'semantically_equivalent', kind: 'incompatible',
      native: 'uncomparable', same: 'identical',
    });
  });

  it('requires an explicit exact target and renders every planned operation', async () => {
    const left = snapshot('left', 'firebird', [
      object('parent', 'PUBLIC.PARENT'),
      object('child', 'PUBLIC.CHILD', {dependencies: ['parent']}),
    ]);
    const right = snapshot('right', 'firebird', []);
    const result = await compareSnapshots(left, right);
    await expect(generateChangePlan(result, left, right, {
      renderChangeOperation: async () => ({nativeStatement: 'DDL'}),
    })).rejects.toThrow('target is required');
    const render = jest.fn(async (operation) => ({
      nativeStatement: `CREATE ${operation.qualifiedName}`,
    }));
    const plan = await generateChangePlan(result, left, right, {
      targetRef: right.sourceRef, renderChangeOperation: render,
    });
    expect(plan.targetSide).toBe('right');
    expect(plan.operations.map((item) => item.objectId)).toEqual(['parent', 'child']);
    expect(plan.operations[1].dependencies).toEqual([plan.operations[0].id]);
    expect(render).toHaveBeenCalledTimes(2);
  });

  it('orders destructive drops from dependents to dependencies and records risk', async () => {
    const left = snapshot('left', 'firebird', []);
    const right = snapshot('right', 'firebird', [
      object('parent', 'PUBLIC.PARENT'),
      object('child', 'PUBLIC.CHILD', {dependencies: ['parent']}),
    ]);
    const result = await compareSnapshots(left, right);
    const plan = await generateChangePlan(result, left, right, {
      targetRef: right.sourceRef,
      renderChangeOperation: async (operation) => ({nativeStatement:
        `DROP ${operation.qualifiedName}`}),
    });
    expect(plan.operations.map((item) => item.objectId)).toEqual(['child', 'parent']);
    expect(plan.destructive).toBe(true);
    expect(plan.operations.every((item) => item.risk === 'high')).toBe(true);
  });
});
