import {fireEvent, render, screen} from '@testing-library/react';
import {withTheme} from '../fake_theme';
import {AIWorkspace, AINavigator, aiInspector} from 'sources/cdeadmin_ui/modules/ai/AIWorkspace';
import {AIService} from 'sources/cdeadmin_ui/modules/ai/AIService';
import {
  FederatedSearchService, RelationshipGraphService, TaskExecutionService,
} from 'sources/cdeadmin_ui/platform/CoordinationServices';
import usePreferences from '../../../pgadmin/preferences/static/js/store';
import {aiDefinition, output, plan, user} from './AITestUtils';

function sessionValue(overrides={}) {
  const service = new AIService({tasks: new TaskExecutionService(), relationships: new RelationshipGraphService(),
    search: new FederatedSearchService(), commands: {get: jest.fn()}}); const base = service.create({id: 'ai-one',
    content: aiDefinition({savedPlans: [plan()]})}); return {...base, runtime: {...base.runtime,
    activeSessionId: 'assistant-one', activePlanId: 'plan-one', assistantSessions: [{id: 'assistant-one',
      mode: 'ask', contextScopeIds: ['database']}], outputs: [output({diff: {before: 'old', after: 'draft new'}})],
    approvals: [], toolInvocations: [], planValidations: [], executionResults: []},
  history: [{at: '2026-09-12T00:00:00Z', actor: 'ai-user', action: 'ai.ask', target: 'ai-one', details: {}}],
  ...overrides};
}
function fakeService(session=sessionValue()) { const listeners = new Set(); return {get: jest.fn(() => session),
  list: jest.fn(() => [session]), subscribe: jest.fn((listener) => { listeners.add(listener);
    return () => listeners.delete(listener); }), select: jest.fn(), listeners}; }
function mount(surface='assistant_dock', overrides={}) { const session = sessionValue(overrides);
  const service = fakeService(session); const executeCommand = jest.fn().mockResolvedValue(session);
  const Component = withTheme(AIWorkspace); render(<Component service={service} sessionId="ai-one"
    surface={surface} executeCommand={executeCommand} currentUser={user()} />);
  return {session, service, executeCommand};
}

describe('AI Assistant workspace', () => {
  beforeEach(() => usePreferences.setState({data: [], version: Date.now(), isLoading: false, failed: false}));
  test.each([['assistant_dock', 'Assistant conversation'], ['evidence_viewer', 'AI evidence'],
    ['plan_review', 'AI proposed plan actions'], ['diff_review', 'AI proposed diffs'],
    ['ai_settings', 'AI model and governance settings'], ['ai_audit', 'AI audit activity']])(
    'renders the complete %s surface', (surface, label) => { mount(surface);
      expect(screen.getByLabelText(label)).toBeVisible();
      expect(screen.getByRole('navigation', {name: 'AI Assistant surfaces'})).toBeVisible(); });
  test('creates sessions and sends prompts through registered commands', () => {
    const {executeCommand} = mount(); fireEvent.click(screen.getByRole('button', {name: 'New session'}));
    expect(executeCommand).toHaveBeenCalledWith('ai.session.new', {mode: 'ask', contextScopeIds: ['database']});
    fireEvent.change(screen.getByLabelText('AI prompt'), {target: {value: 'Explain ASSETS'}});
    fireEvent.click(screen.getByRole('button', {name: 'Ask'})); expect(executeCommand).toHaveBeenCalledWith(
      'ai.ask', {prompt: 'Explain ASSETS', mode: 'ask'});
  });
  test('saves generated output through the project asset command', () => {
    const {executeCommand} = mount(); fireEvent.change(screen.getByLabelText('Output project ID'),
      {target: {value: 'project-one'}}); fireEvent.click(screen.getByRole('button', {name: 'Save latest output'}));
    expect(executeCommand).toHaveBeenCalledWith('ai.output.save_as_asset', {outputId: 'output-one',
      projectId: 'project-one', assetType: 'cdeadmin.note.v1', schemaName: 'cdeadmin.note.v1',
      name: 'AI output', path: 'ai/output.json'});
  });
  test('adds visible metadata-only context with explicit provider and identity', () => {
    const {executeCommand} = mount(); fireEvent.change(screen.getByLabelText('Context name'),
      {target: {value: 'Local database'}}); fireEvent.change(screen.getByLabelText('Provider ID'),
      {target: {value: 'firebird'}}); fireEvent.change(screen.getByLabelText('Canonical resource reference'),
      {target: {value: 'firebird://localhost/demo'}}); fireEvent.click(screen.getByRole('button',
      {name: 'Add context'})); expect(executeCommand).toHaveBeenCalledWith('ai.context.add', {scope:
      expect.objectContaining({id: 'Local database', exposure: 'metadata_only', reference:
        {schema: 'cdeadmin.resource-ref.v1', canonical: 'firebird://localhost/demo', providerId: 'firebird'}})});
  });
  test('validates, approves, rejects and executes through distinct plan commands', () => {
    const {executeCommand} = mount('plan_review'); fireEvent.click(screen.getByRole('button', {name: 'Validate'}));
    expect(executeCommand).toHaveBeenCalledWith('ai.plan.validate', {planId: 'plan-one', currentRevision: '42'});
    fireEvent.click(screen.getByRole('gridcell', {name: 'action-one'}));
    fireEvent.click(screen.getByRole('button', {name: 'Approve selected'}));
    expect(executeCommand).toHaveBeenCalledWith('ai.action.approve', expect.objectContaining({planId: 'plan-one'}));
    fireEvent.click(screen.getByRole('button', {name: 'Reject selected'}));
    expect(executeCommand).toHaveBeenCalledWith('ai.action.reject', expect.objectContaining({planId: 'plan-one'}));
    fireEvent.change(screen.getByLabelText('Confirmation reference'), {target: {value: 'review-one'}});
    fireEvent.click(screen.getByRole('button', {name: 'Execute approved plan'}));
    expect(executeCommand).toHaveBeenCalledWith('ai.plan.execute', expect.objectContaining({planId: 'plan-one',
      currentRevision: '42', confirmationRef: 'review-one'}));
  });
  test.each([['permission_denied', 'Permission denied'], ['runtime_failure', 'failure'],
    ['partial', 'is partial'], ['disconnected', 'is disconnected'], ['read_only', 'is read only'],
    ['stale', 'is stale'], ['validation_error', 'validation error']])('renders %s explicitly', (state, label) => {
    mount('assistant_dock', {state, error: ''}); expect(screen.getAllByText(new RegExp(label, 'i'))[0]).toBeVisible();
  });
  test.each(['cdeadmin_standard', 'compact_expert', 'low_vision', 'high_contrast_light'])(
    'remains operable under %s presentation', (profile) => { usePreferences.setState({data: [{id: 1,
      module: 'misc', name: 'accessibility_profile', value: profile}], version: Date.now(),
    isLoading: false, failed: false}); mount('evidence_viewer'); expect(screen.getByLabelText('AI evidence')).toBeVisible(); });
  test('provides inspector facts and shared activity navigation', () => {
    expect(aiInspector(sessionValue())).toMatchObject({activePlanId: 'plan-one', approvalCount: 0});
    const service = fakeService(); const onOpen = jest.fn(); const Component = withTheme(AINavigator);
    render(<Component service={service} onOpen={onOpen} />); fireEvent.click(screen.getByRole('treeitem',
      {name: /Database assistant/})); expect(onOpen).toHaveBeenCalledWith('ai-one', 'assistant_dock');
  });
});
