import {fireEvent, render, screen, waitFor} from '@testing-library/react';
import {withTheme} from '../fake_theme';
import {APINavigator, APIWorkspace, apiInspector} from 'sources/cdeadmin_ui/modules/api/APIWorkspace';
import usePreferences from '../../../pgadmin/preferences/static/js/store';
import {apiDefinition, fixture, resourceRef, user} from './APITestUtils';

function sessionValue(overrides={}) { const {service} = fixture();
  return {...service.create({id: 'api-one', content: apiDefinition()}), ...overrides}; }
function fakeService(session=sessionValue()) { const listeners = new Set();
  return {get: jest.fn(() => session), list: jest.fn(() => [session]),
    subscribe: jest.fn((listener) => { listeners.add(listener); return () => listeners.delete(listener); }),
    select: jest.fn(), listeners}; }
function mount(surface='api_explorer', overrides={}) { const session = sessionValue(overrides);
  const service = fakeService(session); const executeCommand = jest.fn().mockResolvedValue(session);
  const Component = withTheme(APIWorkspace); render(<Component service={service} sessionId="api-one"
    surface={surface} executeCommand={executeCommand} currentUser={user()} resources={[
      {id: 'orders', label: 'Orders', type: 'table', reference: resourceRef}]} />);
  return {session, service, executeCommand}; }
describe('API Designer workspace', () => {
  beforeEach(() => usePreferences.setState({data: [], version: Date.now(), isLoading: false, failed: false}));
  test.each([['api_explorer', 'tree', 'API definition explorer'],
    ['operation_designer', 'grid', 'API operations'],
    ['event_api_designer', 'text', 'No AsyncAPI channels'],
    ['schema_designer', 'grid', 'API reusable schemas'],
    ['security_policy', 'grid', 'API security and policy'],
    ['test_console', 'toolbar', 'API test controls'],
    ['docs_preview', 'article', 'API documentation preview'],
    ['source', 'toolbar', 'API source controls']])(
    'renders the complete %s surface', (surface, role, label) => { mount(surface);
      const element = role === 'text' ? screen.getByText(new RegExp(label, 'i')) :
        screen.getByRole(role, {name: label});
      expect(element).toBeVisible();
      expect(screen.getByRole('navigation', {name: 'API Designer surfaces'})).toBeVisible(); });
  test('routes operation creation through CommandRegistry boundary', () => {
    const {executeCommand} = mount('operation_designer'); fireEvent.change(screen.getByLabelText('Operation ID'),
      {target: {value: 'create-order'}}); fireEvent.click(screen.getByRole('button', {name: 'Add operation'}));
    expect(executeCommand).toHaveBeenCalledWith('api.operation.add', expect.objectContaining({operation:
      expect.objectContaining({id: 'create-order'})}));
  });
  test('reviews a provider resource before applying a data binding command', async () => {
    const {executeCommand} = mount('operation_designer');
    fireEvent.click(screen.getByRole('button', {name: 'Choose resource'}));
    fireEvent.click(screen.getByText('Orders')); fireEvent.click(screen.getByRole('button', {name: 'Select'}));
    await waitFor(() => expect(screen.getByRole('button', {name: 'Apply binding'})).toBeVisible());
    fireEvent.click(screen.getByRole('button', {name: 'Apply binding'}));
    expect(executeCommand).toHaveBeenCalledWith('api.binding.set', expect.objectContaining({
      operationId: 'list-orders', binding: expect.objectContaining({targetRef: resourceRef})}));
  });
  test('validates and exports only through registered commands', () => {
    let result = mount('api_explorer'); fireEvent.click(screen.getByRole('button',
      {name: 'Validate definition'})); expect(result.executeCommand).toHaveBeenCalledWith('api.validate', {});
    result = mount('source'); fireEvent.click(screen.getByRole('button', {name: 'Export source'}));
    expect(result.executeCommand).toHaveBeenCalledWith('api.export.openapi', {});
  });
  test('prepares deployment packages through target-bound command arguments', () => {
    const content = apiDefinition({deploymentBindings: [{id: 'gateway', name: 'Gateway', environment: 'dev',
      targetRef: resourceRef, gatewayProfile: 'http', credentialRef: null, config: {}, extensions: []}]});
    const {executeCommand} = mount('security_policy', {content}); fireEvent.change(screen.getByLabelText(
      'Deployment confirmation reference'), {target: {value: 'review-one'}});
    fireEvent.click(screen.getByRole('button', {name: 'Prepare deployment package'}));
    expect(executeCommand).toHaveBeenCalledWith('api.deploy.prepare', {deploymentId: 'gateway',
      confirmationRef: 'review-one', environment: 'dev', connection: `resource:${resourceRef.canonical}`});
  });
  test('shows live environment prominently and invokes only through its command', () => {
    const {executeCommand} = mount('test_console'); expect(screen.getByText(/LIVE environment/)).toBeVisible();
    fireEvent.click(screen.getByRole('button', {name: 'Run selected test'}));
    expect(executeCommand).toHaveBeenCalledWith('api.test.run', expect.objectContaining({testId: 'live-list'}));
  });
  test.each([['permission_denied', 'Permission denied'], ['runtime_failure', 'runtime failure'],
    ['partial', 'is partial'], ['disconnected', 'is disconnected'], ['read_only', 'is read only'],
    ['stale', 'is stale'], ['validation_error', 'validation error']])('renders %s explicitly', (state, label) => {
    mount('api_explorer', {state, error: ''}); expect(screen.getAllByText(new RegExp(label, 'i'))[0]).toBeVisible();
  });
  test.each(['cdeadmin_standard', 'compact_expert', 'low_vision', 'high_contrast_light'])(
    'remains operable under %s presentation', (profile) => { usePreferences.setState({data: [{id: 1,
      module: 'misc', name: 'accessibility_profile', value: profile}], version: Date.now(),
    isLoading: false, failed: false}); const {executeCommand} = mount('operation_designer');
    expect(screen.getByRole('button', {name: 'Add operation'})).toBeDisabled(); expect(executeCommand).not.toHaveBeenCalled(); });
  test('provides inspector facts and opens through shared activity navigator', () => {
    expect(apiInspector(sessionValue())).toMatchObject({profile: 'openapi_http', externalSpecVersion: '3.2.0'});
    const service = fakeService(); const onOpen = jest.fn(); const Component = withTheme(APINavigator);
    render(<Component service={service} onOpen={onOpen} />); fireEvent.click(screen.getByRole('treeitem',
      {name: /Orders API/})); expect(onOpen).toHaveBeenCalledWith('api-one', 'api_explorer');
  });
});
