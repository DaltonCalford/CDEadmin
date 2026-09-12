import {
  ASSESSMENT_CATEGORIES, MIGRATION_PHASES, MIGRATION_STRATEGIES,
  ROLLBACK_CLASSES, VERIFICATION_TYPES, createMigrationContent,
  exportMigrationDefinition, importMigrationDefinition, migrationAssetRequest,
  validateCopyCheckpoint,
} from 'sources/cdeadmin_ui/modules/migration/contracts';
import {definition, mapping} from './MigrationTestUtils';

describe('Migration contracts', () => {
  test('publishes the exact normative phase and strategy taxonomies', () => {
    expect(MIGRATION_PHASES).toHaveLength(13); expect(MIGRATION_STRATEGIES).toHaveLength(7);
    expect(ASSESSMENT_CATEGORIES).toHaveLength(7); expect(VERIFICATION_TYPES).toHaveLength(7);
    expect(ROLLBACK_CLASSES).toHaveLength(4);
  });
  test('round-trips a complete deterministic migration definition', () => {
    const first = createMigrationContent(definition());
    expect(createMigrationContent(JSON.parse(JSON.stringify(first)))).toEqual(first);
    expect(first.schema).toBe('cdeadmin.migration.asset.v1');
  });
  test('requires explicit source and target outside discovery', () => {
    expect(() => createMigrationContent(definition({sourceBinding: null}))).toThrow(
      'require source and target');
  });
  test('requires CDC authority for online migration', () => {
    expect(() => createMigrationContent(definition({strategy: 'online_with_cdc'}))).toThrow('CDC');
  });
  test('preserves but exposes lossy mappings', () => {
    const content = createMigrationContent(definition({mappingSet: [mapping({lossy: true})]}));
    expect(content.mappingSet[0]).toMatchObject({lossy: true, lossAcknowledged: false});
  });
  test('requires portable hash canonicalization rules', () => {
    const check = {...definition().validationPlan[0], type: 'deterministic_hashes'};
    expect(() => createMigrationContent(definition({validationPlan: [check]}))).toThrow(
      'canonicalization');
  });
  test('requires actionable cutover and rollback runbooks', () => {
    expect(() => createMigrationContent(definition({cutoverPlan: {
      ...definition().cutoverPlan, steps: []}}))).toThrow('at least one step');
    expect(() => createMigrationContent(definition({rollbackPlan: {
      ...definition().rollbackPlan, steps: []}}))).toThrow('at least one step');
    expect(() => createMigrationContent(definition({rollbackPlan: {
      ...definition().rollbackPlan, deadline: 'sometime'}}))).toThrow('ISO date-time');
  });
  test('rejects raw secrets recursively', () => {
    expect(() => createMigrationContent(definition({extensions: {password: 'bad'}}))).toThrow(
      /credential/i);
  });
  test('rejects unknown contract fields instead of silently dropping content', () => {
    expect(() => createMigrationContent({...definition(), futureField: true})).toThrow(
      'unsupported field futureField');
    const cutoverPlan = {...definition().cutoverPlan, steps: [{
      ...definition().cutoverPlan.steps[0], guessed: true}]};
    expect(() => createMigrationContent(definition({cutoverPlan}))).toThrow(
      'unsupported field guessed');
  });
  test('builds an optimistic project asset request', () => {
    expect(migrationAssetRequest({content: definition(), expectedVersion: 4})).toMatchObject({
      asset_type: 'cdeadmin.migration.v1', schema_name: 'cdeadmin.migration.v1', expected_version: 4});
  });
  test('round-trips versioned interchange while preserving provenance and refusing loss', () => {
    const exported = exportMigrationDefinition(definition(), {profile: 'complete',
      exportedAt: '2026-09-12T00:00:00Z', provenance: {repository: 'test'}});
    expect(exported).toMatchObject({schema: 'cdeadmin.migration-interchange.v1', version: 1,
      sourceVersion: 1, profile: 'complete', provenance: {repository: 'test'},
      unsupportedContent: []});
    const imported = importMigrationDefinition(exported, {importedAt: '2026-09-12T01:00:00Z'});
    expect(imported.content.extensions.interchangeProvenance).toMatchObject({
      sourceVersion: 1, profile: 'complete', provenance: {repository: 'test'}});
    expect(() => importMigrationDefinition({...exported,
      unsupportedContent: [{path: 'future'}]}, {importedAt: 'now'})).toThrow('without loss');
    expect(() => importMigrationDefinition({...exported, version: 2}, {importedAt: 'now'}))
      .toThrow('unsupported');
  });
  test('validates complete recoverable checkpoint evidence', () => {
    expect(validateCopyCheckpoint({id: 'cp', unitId: 'unit', state: 'committed',
      sourceRevision: 'r1', sourceBoundary: {}, lastCompleted: {id: 9}, targetProgress: {id: 9},
      rowDocumentCount: 9, byteCount: 90, retryState: {}, committedAt: 'now', nativeDetails: {}})
      .state).toBe('committed');
  });
});
