/////////////////////////////////////////////////////////////
// Data Contract validation, lifecycle, compliance and diff gates.
/////////////////////////////////////////////////////////////

import {
  compareContractVersions, nextStatus, normalizeDrift,
  summarizeCompliance, validateContractStructure,
} from 'sources/cdeadmin_ui/modules/data_contract';

const element = (updates={}) => ({id: 'root', name: 'Root', logicalType: 'document',
  description: 'Root object', constraints: {}, physicalDefinition: {}, extensions: {}, ...updates});
const valid = (updates={}) => ({name: 'Contract', contractVersion: '1.0', status: 'draft',
  domain: 'sales', description: 'Description', elements: [element()], ...updates});
const observation = (compliant) => ({compliant, details: [], evidence: []});

describe('Data Contract engine', () => {
  it('validates authored structure independently from live compliance', () => {
    expect(validateContractStructure(valid())).toMatchObject({valid: true});
    expect(validateContractStructure(valid({name: '', description: '', elements: []})))
      .toMatchObject({valid: false, details: expect.arrayContaining([
        'Contract name is required.', 'Contract description is required.',
        'At least one logical contract element is required.'])});
  });

  it('detects deep hierarchy cycles and duplicate environment bindings', () => {
    expect(validateContractStructure(valid({elements: [element({parentId: 'child'}),
      element({id: 'child', name: 'Child', parentId: 'root', logicalType: 'property'})]})))
      .toMatchObject({valid: false, details: expect.arrayContaining([
        expect.stringContaining('cycle')])});
    const targetRef = {schema: 'cdeadmin.resource-ref.v1', provider: 'mongodb', canonical: 'r'};
    const make = (id) => ({id, elementId: 'root', targetRef, environment: 'production',
      bindingStatus: 'observed', nativeDetails: {}});
    expect(validateContractStructure(valid({bindings: [make('one'), make('two')]})).valid)
      .toBe(false);
  });

  it('allows forward lifecycle changes and requires a reason for reverse changes', () => {
    expect(nextStatus('draft', 'active')).toMatchObject({direction: 'forward'});
    expect(nextStatus('active', 'draft', {reason: 'Rework required'}))
      .toMatchObject({direction: 'reverse', reason: 'Rework required'});
    expect(() => nextStatus('active', 'draft')).toThrow('audit reason');
  });

  it('normalizes only specified drift states and bounded evidence', () => {
    expect(normalizeDrift({state: 'provider_changed', differences: [{path: '/schema/name',
      kind: 'changed', contractValue: 'a', providerValue: 'b', evidence: {revision: '42'}}]}))
      .toMatchObject({state: 'provider_changed', differences: [expect.objectContaining({
        path: '/schema/name'})]});
    expect(() => normalizeDrift({state: 'invented', differences: []})).toThrow('Invalid');
  });

  it('keeps structural validity separate from live noncompliance and partial observations', () => {
    const summary = summarizeCompliance({structure: validateContractStructure(valid()),
      bindingResults: [observation(true)], schemaResults: [observation(false)],
      qualityResults: [], slaResults: [], classificationResults: [observation(true)]});
    expect(summary).toMatchObject({compliant: false, partial: true});
    expect(summary.dimensions.find((item) => item.dimension === 'contract_structure').compliant)
      .toBe(true);
    expect(summary.dimensions.find((item) => item.dimension === 'schema').compliant).toBe(false);
    expect(summary.dimensions.find((item) => item.dimension ===
      'documentation_reference_resolution').compliant).toBeNull();
    expect(summarizeCompliance({structure: validateContractStructure(valid()),
      documentationResults: [observation(true)]}).dimensions.find((item) =>
      item.dimension === 'documentation_reference_resolution').compliant).toBe(true);
  });

  it('produces deterministic path-level version differences', () => {
    const changed = compareContractVersions(valid(), valid({contractVersion: '2.0',
      description: 'Changed'}));
    expect(changed.identical).toBe(false);
    expect(changed.differences.map((item) => item.path)).toEqual(expect.arrayContaining([
      '/contractVersion', '/description']));
    expect(compareContractVersions(valid(), valid())).toMatchObject({identical: true,
      differences: []});
  });
});
