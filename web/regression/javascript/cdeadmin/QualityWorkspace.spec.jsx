/////////////////////////////////////////////////////////////
// Data Quality surface interaction, state and navigation gates.
/////////////////////////////////////////////////////////////

import {fireEvent, render, screen, waitFor, within} from '@testing-library/react';
import {withTheme} from '../fake_theme';
import {QualityNavigator, QualityWorkspace, qualityInspector}
  from 'sources/cdeadmin_ui/modules/quality';
import usePreferences from '../../../pgadmin/preferences/static/js/store';

const resourceRef = {schema: 'cdeadmin.resource-ref.v1', provider: 'firebird',
  canonical: 'cde-resource://firebird/local/table/orders'};
const sampleRef = {schema: 'cdeadmin.external-ref.v1', id: 'sample:orders'};
const qualityRule = {schema: 'cdeadmin.quality-rule.v1', id: 'rule-one',
  name: 'Order ID present', type: 'not_null', dimension: 'completeness',
  parameters: {column: 'ID'}, threshold: null, ratio: false, severity: 'critical',
  evaluationMode: 'exact', enabled: true, generatedSuggestion: false,
  sourceSampleRef: null, sourceRevision: null, actionIds: ['block-publish'],
  documentation: 'Primary key must be populated.', nativeDetails: {catalog: 'RDB$RELATION_FIELDS'}};
const result = {schema: 'cdeadmin.quality-rule-result.v1', id: 'result-one',
  ruleId: 'rule-one', status: 'fail', evaluationMode: 'exact', severity: 'critical',
  observedMetric: 0.9, threshold: null, evaluatedCount: 10, violationCount: 1,
  error: null, dataRevision: 'txn:42', violationSampleRef: sampleRef,
  diagnostics: {query: 'bounded'}, nativeDetails: {plan: 'index'}};
const run = {schema: 'cdeadmin.quality-run.v1', id: 'run-one',
  taskRef: {schema: 'cdeadmin.task-ref.v1', id: 'task-one'},
  startedAt: '2026-09-11T11:59:00Z', finishedAt: '2026-09-11T12:00:00Z',
  dataRevision: 'txn:42', engine: {providerId: 'firebird', providerVersion: '5.0.4'},
  evaluationModes: ['exact'], results: [result], summary: {outcome: 'fail', total: 1,
    counts: {pass: 0, fail: 1, error: 0}, severities: {info: 0, warning: 0, critical: 1}},
  actionResults: [{actionId: 'block-publish', state: 'blocked_checkpoint'}]};
const suggestion = {...qualityRule, id: 'suggestion-one', name: 'Suggested uniqueness',
  type: 'uniqueness', dimension: 'uniqueness', actionIds: [], generatedSuggestion: true,
  sourceSampleRef: sampleRef, sourceRevision: 'txn:42'};

function value(overrides={}) {
  return {schema: 'cdeadmin.quality-session.v1', id: 'quality-one', state: 'ready', dirty: true,
    error: '', problems: [], activeTaskId: null, selectedRuleId: 'rule-one',
    selectedRunId: 'run-one', providerStatus: {supportState: 'supported_native',
      providerVersion: '5.0.4'}, validation: {valid: true, details: []},
    content: {schema: 'cdeadmin.quality.asset.v1', schemaVersion: 1,
      moduleId: 'cdeadmin.quality', name: 'Orders quality', owner: 'quality-team', tags: ['orders'],
      rules: [qualityRule], parameters: {}, defaultScope: {id: 'orders-scope', resourceRef,
        partition: null, window: null, filter: null, samplePolicy: {mode: 'none_exact'},
        nativeDetails: {}}, severityPolicy: {}, schedule: {cron: '0 2 * * *', timezone: 'UTC'},
      actions: [{schema: 'cdeadmin.quality-action.v1', id: 'block-publish', type: 'block',
        label: 'Block publish', severities: ['critical'], enabled: true, parameters: {}}],
      baselineRefs: []},
    runs: [run], baselines: [{schema: 'cdeadmin.quality-baseline.v1', id: 'base-one',
      metrics: {rows: 10}, period: {}, dataRevision: 'txn:41', resourceRef,
      capturedAt: '2026-09-10T12:00:00Z', drift: {drifted: true, changes: [{metric: 'rows',
        drifted: true}]}}], profile: {schema: 'cdeadmin.quality-profile.v1',
      providerId: 'firebird', providerVersion: '5.0.4', budget: 1000,
      metrics: {rows: 10}, sourceSampleRef: sampleRef, sourceRevision: 'txn:42',
      suggestions: [suggestion], acceptedRuleIds: []},
    failedSamples: [{resultId: 'result-one', rows: [{ID: null, NOTE: 'invalid'}],
      sensitivityState: 'restricted', total: 1, bounded: true}], history: [],
    interoperability: null, ...overrides};
}

function fakeService(session=value()) {
  const listeners = new Set();
  return {get: jest.fn(() => session), list: jest.fn(() => [session]),
    subscribe: jest.fn((listener) => { listeners.add(listener);
      return () => listeners.delete(listener); }),
    select: jest.fn(), reportError: jest.fn(), listeners};
}

function mount(surface='quality_overview', overrides={}, properties={}) {
  const session = value(overrides); const service = fakeService(session);
  const executeCommand = jest.fn().mockResolvedValue('quality-export');
  const Component = withTheme(QualityWorkspace);
  render(<Component service={service} sessionId="quality-one" surface={surface}
    executeCommand={executeCommand} currentUser={{permissions: ['quality.view_samples',
      'quality.export_samples']}} {...properties} />);
  return {session, service, executeCommand};
}

describe('QualityWorkspace', () => {
  beforeEach(() => {
    usePreferences.setState({data: [], version: Date.now(), isLoading: false, failed: false});
  });

  it('renders all seven surfaces and exact overview health/run evidence', () => {
    const {executeCommand} = mount();
    expect(screen.getAllByRole('tab')).toHaveLength(7);
    expect(screen.getByText('Rules 1/1')).toBeVisible();
    expect(screen.getByText('Critical failures 1')).toBeVisible();
    expect(screen.getByLabelText('Recent quality runs and trends')).toBeVisible();
    fireEvent.click(screen.getByRole('button', {name: 'Run rule set'}));
    expect(executeCommand).toHaveBeenCalledWith('quality.ruleset.run', {},
      expect.objectContaining({sessionId: 'quality-one'}));
  });

  it('searches, selects, tests and creates a fully typed rule', () => {
    const {service, executeCommand} = mount('rule_set_designer');
    expect(screen.getByLabelText('Quality rule grid')).toBeVisible();
    fireEvent.change(screen.getByLabelText('Search quality rules'), {target: {value: 'not_null'}});
    expect(screen.getByText('Order ID present')).toBeVisible();
    fireEvent.click(screen.getByRole('button', {name: 'Order ID present'}));
    expect(service.select).toHaveBeenCalledWith('quality-one', {ruleId: 'rule-one'});
    fireEvent.click(screen.getByRole('button', {name: 'Test'}));
    expect(executeCommand).toHaveBeenCalledWith('quality.rule.test', {ruleId: 'rule-one'},
      expect.any(Object));
    fireEvent.change(screen.getByLabelText('Stable rule ID'), {target: {value: 'rule-two'}});
    fireEvent.change(screen.getByLabelText('Rule name'), {target: {value: 'Second rule'}});
    fireEvent.click(screen.getByRole('button', {name: 'Add rule'}));
    expect(executeCommand).toHaveBeenCalledWith('quality.rule.add', {rule: expect.objectContaining({
      id: 'rule-two', name: 'Second rule', type: 'not_null', dimension: 'completeness'})},
    expect.any(Object));
    fireEvent.change(screen.getByLabelText('Selected rule name'),
      {target: {value: 'Order identifier present'}});
    fireEvent.change(screen.getByLabelText('Rule parameters JSON'),
      {target: {value: '{"column":"ORDER_ID"}'}});
    const consoleImplementation = console.error.getMockImplementation();
    console.error.mockImplementation(() => {});
    fireEvent.mouseDown(screen.getByRole('combobox', {name: 'Threshold operator'}));
    fireEvent.click(screen.getByRole('option', {name: '>='}));
    fireEvent.change(screen.getByLabelText('Threshold value'), {target: {value: '0.98'}});
    fireEvent.click(screen.getByRole('checkbox', {name: 'Ratio threshold (stored 0..1)'}));
    console.error.mockImplementation(consoleImplementation); console.error.mockClear();
    fireEvent.click(screen.getByRole('button', {name: 'Save rule changes'}));
    expect(executeCommand).toHaveBeenCalledWith('quality.ruleset.create', {content:
      expect.objectContaining({rules: [expect.objectContaining({name: 'Order identifier present',
        parameters: {column: 'ORDER_ID'}, ratio: true,
        threshold: {operator: '>=', value: 0.98}})]})}, expect.any(Object));
    fireEvent.click(screen.getByRole('button', {name: 'Remove rule from rule set'}));
    expect(executeCommand).toHaveBeenCalledWith('quality.ruleset.create', {content:
      expect.objectContaining({rules: []})}, expect.any(Object));
  });

  it('binds a single provider resource with an explicit sampling policy', () => {
    const resources = [{id: 'orders', label: 'Orders', type: 'table', path: 'firebird/demo',
      reference: resourceRef}];
    const {executeCommand} = mount('data_scope_slice_designer', {content: {
      ...value().content, defaultScope: null}}, {resources});
    fireEvent.click(screen.getByRole('button', {name: 'Select resource and apply scope'}));
    fireEvent.doubleClick(screen.getByRole('option', {name: /Orders/}));
    expect(executeCommand).toHaveBeenCalledWith('quality.ruleset.create', {content:
      expect.objectContaining({defaultScope: expect.objectContaining({resourceRef,
        samplePolicy: {mode: 'none_exact'}})})}, expect.any(Object));
  });

  it('edits scope constraints safely and reports malformed filter objects inline', () => {
    const {executeCommand} = mount('data_scope_slice_designer');
    fireEvent.change(screen.getByLabelText('Partition or slice identity'),
      {target: {value: 'FY2026'}});
    fireEvent.change(screen.getByLabelText('Filter object JSON'),
      {target: {value: '{"STATUS":"OPEN"}'}});
    fireEvent.click(screen.getByRole('button', {name: 'Apply current scope'}));
    expect(executeCommand).toHaveBeenCalledWith('quality.ruleset.create', {content:
      expect.objectContaining({defaultScope: expect.objectContaining({partition: 'FY2026',
        filter: {STATUS: 'OPEN'}})})}, expect.any(Object));
    fireEvent.change(screen.getByLabelText('Filter object JSON'), {target: {value: '[1,2]'}});
    fireEvent.click(screen.getByRole('button', {name: 'Apply current scope'}));
    expect(screen.getByRole('alert')).toHaveTextContent('Filter must be a JSON object');
  });

  it('keeps metadata export distinct from viewing and exporting protected rows', async () => {
    const onExport = jest.fn(); const {executeCommand} = mount('validation_run_results', {},
      {onExport});
    expect(screen.getByLabelText('Quality rule results')).toBeVisible();
    expect(screen.getByLabelText('Protected failed-row samples')).toBeVisible();
    fireEvent.click(screen.getByRole('button', {name: 'Export metadata result'}));
    await waitFor(() => expect(onExport).toHaveBeenCalledWith('quality-export'));
    fireEvent.click(screen.getByRole('button', {name: 'Export failed rows'}));
    expect(executeCommand).toHaveBeenCalledWith('quality.result.export', {
      runId: 'run-one', format: 'json', includeSamples: true}, expect.any(Object));
  });

  it('shows protected sample rows but disables their export without export permission', () => {
    mount('validation_run_results', {}, {currentUser: {permissions: ['quality.view_samples']}});
    expect(screen.getByLabelText('Protected failed-row samples')).toBeVisible();
    expect(screen.getByRole('button', {name: 'Export failed rows'})).toBeDisabled();
  });

  it('profiles bounded slices and makes every suggestion an explicit acceptance action', () => {
    const {executeCommand} = mount('profiler');
    expect(screen.getByText(/Source revision: txn:42/)).toBeVisible();
    expect(screen.getByLabelText('Generated quality rule suggestions')).toBeVisible();
    fireEvent.change(screen.getByLabelText('Maximum rows/statistical budget'),
      {target: {value: '250'}});
    fireEvent.click(screen.getByRole('button', {name: 'Run bounded profile'}));
    expect(executeCommand).toHaveBeenCalledWith('quality.profile.run', {budget: 250},
      expect.any(Object));
    fireEvent.click(screen.getByRole('button', {name: 'Accept Suggested uniqueness'}));
    expect(executeCommand).toHaveBeenCalledWith('quality.rule.add', {rule: suggestion},
      expect.any(Object));
  });

  it('renders baseline drift and run history without merging either evidence model', () => {
    mount('quality_history');
    expect(screen.getByLabelText('Quality baselines and drift')).toBeVisible();
    expect(screen.getByLabelText('Quality run history')).toBeVisible();
    expect(screen.getByText('base-one')).toBeVisible();
    expect(screen.getByText('rows')).toBeVisible();
  });

  it('renders production schedules/actions and links contracts through the shared picker', () => {
    const contractRef = {schema: 'cdeadmin.asset-ref.v1', projectId: 'p', assetId: 'contract'};
    const resources = [{id: 'contract', label: 'Orders contract', type: 'data-contract',
      reference: contractRef}];
    const {executeCommand} = mount('schedule_actions', {}, {resources});
    expect(screen.getByLabelText('Canonical schedule object JSON')).toHaveValue(
      '{"cron":"0 2 * * *","timezone":"UTC"}');
    expect(screen.getByLabelText('Quality failure actions')).toBeVisible();
    fireEvent.change(screen.getByLabelText('Canonical schedule object JSON'),
      {target: {value: '{"cron":"0 3 * * *","timezone":"UTC"}'}});
    fireEvent.click(screen.getByRole('button', {name: 'Save schedule definition'}));
    expect(executeCommand).toHaveBeenCalledWith('quality.ruleset.create', {content:
      expect.objectContaining({schedule: {cron: '0 3 * * *', timezone: 'UTC'}})},
    expect.any(Object));
    fireEvent.click(screen.getByRole('button', {name: 'Block publish'}));
    fireEvent.click(screen.getByRole('button', {name: 'Save failure action'}));
    expect(executeCommand).toHaveBeenCalledWith('quality.ruleset.create', {content:
      expect.objectContaining({actions: [expect.objectContaining({id: 'block-publish'})]})},
    expect.any(Object));
    fireEvent.click(screen.getByRole('button', {name: 'Remove failure action'}));
    expect(screen.getByRole('alert')).toHaveTextContent('Action is used by rules: rule-one');
    fireEvent.click(screen.getByRole('button', {name: 'Link Data Contract'}));
    fireEvent.doubleClick(screen.getByRole('option', {name: /Orders contract/}));
    expect(executeCommand).toHaveBeenCalledWith('quality.contract.sync', {contractRef},
      expect.any(Object));
  });

  it('preserves safe authored state across active, stale and failed runtime states', async () => {
    const failure = new Error('provider_unavailable: Firebird is offline');
    const session = value({state: 'stale', error: 'Evidence is stale'});
    const service = fakeService(session); const executeCommand = jest.fn().mockRejectedValue(failure);
    const Component = withTheme(QualityWorkspace);
    render(<Component service={service} sessionId="quality-one" executeCommand={executeCommand} />);
    expect(screen.getByRole('alert')).toHaveTextContent('Evidence is stale');
    expect(screen.getByText(/Last safe authored state remains visible/)).toBeVisible();
    fireEvent.click(screen.getByRole('button', {name: 'Run rule set'}));
    await waitFor(() => expect(service.reportError).toHaveBeenCalledWith(
      'quality-one', failure, 'provider_unavailable'));
  });

  it.each(['cdeadmin_standard', 'compact_expert', 'low_vision', 'high_contrast_light'])(
    'remains operable under the %s presentation profile', (profile) => {
      usePreferences.setState({data: [{id: 1, module: 'misc', name: 'accessibility_profile',
        value: profile}], version: Date.now(), isLoading: false, failed: false});
      const {executeCommand} = mount('quality_overview');
      fireEvent.click(screen.getByRole('button', {name: 'Capture baseline'}));
      expect(executeCommand).toHaveBeenCalledWith('quality.baseline.capture', {}, expect.any(Object));
    });

  it('produces rule-specific inspector facts including provider details and actions', () => {
    expect(qualityInspector(value())).toEqual({'Rule definition': 'Order ID present · not_null',
      Parameters: '{"column":"ID"}', 'Scope override': 'orders-scope',
      'Failure action': 'block-publish', Documentation: 'Primary key must be populated.'});
  });
});

describe('QualityNavigator', () => {
  it('provides a real tree for assets, rule sets, scopes, runs, baselines and schedules', () => {
    const service = fakeService(); const onOpen = jest.fn();
    const Navigator = withTheme(QualityNavigator); render(<Navigator service={service}
      onOpen={onOpen} />);
    const asset = screen.getByRole('treeitem', {name: /Orders quality/});
    fireEvent.click(within(asset).getByRole('button', {name: /Expand Orders quality/}));
    for(const label of ['Rule Sets', 'Scopes', 'Runs', 'Baselines', 'Schedules']) {
      expect(screen.getByRole('treeitem', {name: label})).toBeVisible();
    }
    fireEvent.doubleClick(screen.getByRole('treeitem', {name: 'Runs'}));
    expect(onOpen).toHaveBeenCalledWith('quality-one', 'validation_run_results');
  });
});
