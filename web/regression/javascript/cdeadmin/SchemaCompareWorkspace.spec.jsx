/////////////////////////////////////////////////////////////
// Schema Comparison surface state, keyboard and command routing gates.
/////////////////////////////////////////////////////////////

import {fireEvent, render, screen, within} from '@testing-library/react';
import {withTheme} from '../fake_theme';
import {
  SchemaCompareNavigator, SchemaCompareWorkspace,
} from 'sources/cdeadmin_ui/modules/schema_compare';

const leftRef = {schema: 'cdeadmin.resource-ref.v1', provider: 'firebird',
  canonical: 'cde-resource://firebird/local/%2F/database/left'};
const rightRef = {schema: 'cdeadmin.resource-ref.v1', provider: 'firebird',
  canonical: 'cde-resource://firebird/local/%2F/database/right'};
const difference = {id: 'diff:A:B', classification: 'changed', leftId: 'A', rightId: 'B',
  kind: 'table', qualifiedName: 'PUBLIC.ASSETS', matchReason: 'accepted_mapping'};
const plan = {id: 'plan:one', targetRef: rightRef, targetSide: 'right',
  operations: [{id: 'operation:one', diffId: difference.id, action: 'alter', kind: 'table',
    qualifiedName: 'PUBLIC.ASSETS', risk: 'medium', reversible: false,
    nativeStatement: 'ALTER TABLE ASSETS ADD NAME VARCHAR(80)', dependencies: []}],
  destructive: false, partialSelection: false};

function session(overrides={}) {
  return {schema: 'cdeadmin.schema-compare.session.v1', id: 'session-one', state: 'ready',
    dirty: false, activeTaskId: null, error: '', selectedDiffId: difference.id,
    content: {leftRef, rightRef, options: {}, acceptedMappings: [], ignoredDiffs: []},
    result: {differences: [difference], renameCandidates: [{id: 'rename:A:B',
      leftId: 'A', rightId: 'B', kind: 'table', evidence: ['same_normalized_definition']}],
    counts: {changed: 1}},
    leftSnapshot: {objects: [{id: 'A', kind: 'table', qualifiedName: 'PUBLIC.ASSETS',
      normalized: {columns: 1}, native: {relationType: 0}}]},
    rightSnapshot: {objects: [{id: 'B', kind: 'table', qualifiedName: 'PUBLIC.ASSETS',
      normalized: {columns: 2}, native: {relationType: 0}}]},
    plan, validation: {valid: true, applyReady: true, errors: []}, ...overrides};
}

function fakeService(value=session()) {
  const listeners = new Set();
  return {get: jest.fn(() => value), list: jest.fn(() => [value]),
    subscribe: jest.fn((listener) => { listeners.add(listener);
      return () => listeners.delete(listener); }),
    selectDiff: jest.fn(), reportError: jest.fn(), listeners};
}

describe('SchemaCompareWorkspace', () => {
  const Component = withTheme(SchemaCompareWorkspace);

  it('renders setup in the shared surface and routes run through CommandRegistry authority', () => {
    const service = fakeService(); const executeCommand = jest.fn().mockResolvedValue({});
    render(<Component service={service} sessionId="session-one"
      executeCommand={executeCommand} />);
    expect(screen.getAllByRole('tab')).toHaveLength(6);
    expect(screen.getByRole('heading', {name: 'Compare Setup'})).toBeVisible();
    fireEvent.click(screen.getByRole('button', {name: 'Run comparison'}));
    expect(executeCommand).toHaveBeenCalledWith('schema_compare.run', {},
      expect.objectContaining({sessionId: 'session-one', service}));
  });

  it('supports tree navigation, selection and the object metadata alternative', () => {
    const service = fakeService();
    render(<Component service={service} sessionId="session-one"
      executeCommand={jest.fn().mockResolvedValue({})} surface="diff_tree" />);
    const group = screen.getByRole('treeitem', {name: /changed \(1\)/i});
    fireEvent.keyDown(group, {key: 'ArrowRight'});
    const item = screen.getByRole('treeitem', {name: /PUBLIC.ASSETS/});
    fireEvent.click(item); expect(service.selectDiff).toHaveBeenCalledWith(
      'session-one', difference.id
    );
    fireEvent.doubleClick(item);
    expect(screen.getByRole('heading', {name: 'Left'})).toBeVisible();
    expect(screen.getByText(/"columns": 1/)).toBeVisible();
    expect(screen.getByText(/"columns": 2/)).toBeVisible();
  });

  it('exposes mappings, dependency plans and provider-native apply review', () => {
    const service = fakeService(); const executeCommand = jest.fn().mockResolvedValue('{}');
    render(<Component service={service} sessionId="session-one"
      executeCommand={executeCommand} surface="mapping_review" />);
    expect(screen.getByText(/Candidates are never accepted automatically/)).toBeVisible();
    fireEvent.click(screen.getByRole('tab', {name: 'Change Plan'}));
    expect(screen.getByText('ALTER TABLE ASSETS ADD NAME VARCHAR(80)')).toBeVisible();
    fireEvent.click(screen.getByRole('tab', {name: 'Apply/Export Review'}));
    expect(screen.getByText(/exact plan passed/)).toBeVisible();
    expect(screen.getByText('ALTER TABLE ASSETS ADD NAME VARCHAR(80)')).toBeVisible();
    fireEvent.click(screen.getByRole('button', {name: 'Apply validated plan'}));
    expect(executeCommand).toHaveBeenLastCalledWith('schema_compare.plan.apply',
      {confirmation: ''}, expect.any(Object));
  });

  it('keeps stale results visible and reports runtime failures without discarding state',
    async () => {
      const service = fakeService(session({state: 'stale', error: 'Target disconnected'}));
      render(<Component service={service} sessionId="session-one"
        executeCommand={jest.fn()} surface="object_diff" />);
      expect(screen.getByRole('alert')).toHaveTextContent('Target disconnected');
      expect(screen.getByText(/displayed result is stale/)).toBeVisible();
      expect(screen.getByText(/"columns": 1/)).toBeVisible();
    });
});

describe('SchemaCompareNavigator', () => {
  it('renders sessions as real tree branches with classification counts', () => {
    const service = fakeService(); const onOpen = jest.fn();
    const Navigator = withTheme(SchemaCompareNavigator);
    render(<Navigator service={service} onOpen={onOpen} />);
    const row = screen.getByRole('treeitem', {name: /session-one/});
    fireEvent.click(within(row).getByRole('button', {name: /Expand session-one/}));
    const child = screen.getByRole('treeitem', {name: /changed \(1\)/});
    fireEvent.doubleClick(child);
    expect(onOpen).toHaveBeenCalledWith('session-one', 'diff_tree');
  });
});
