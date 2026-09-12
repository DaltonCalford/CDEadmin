import {fireEvent, render, screen} from '@testing-library/react';
import {withTheme} from '../fake_theme';
import {
  MigrationNavigator, MigrationWorkspace, migrationInspector,
} from 'sources/cdeadmin_ui/modules/migration/MigrationWorkspace';
import usePreferences from '../../../pgadmin/preferences/static/js/store';
import {definition, fixture, user} from './MigrationTestUtils';

function sessionValue(overrides={}) {
  const {service} = fixture(); const session = service.create({id: 'migration-one', content: definition()});
  return {...session, ...overrides};
}
function fakeService(session=sessionValue()) {
  const listeners = new Set(); return {get: jest.fn(() => session), list: jest.fn(() => [session]),
    subscribe: jest.fn((listener) => { listeners.add(listener); return () => listeners.delete(listener); }),
    select: jest.fn(), listeners};
}
function mount(surface='migration_portfolio', overrides={}, properties={}) {
  const session = sessionValue(overrides); const service = fakeService(session);
  const executeCommand = jest.fn().mockResolvedValue(session); const Component = withTheme(MigrationWorkspace);
  render(<Component service={service} sessionId="migration-one" surface={surface}
    executeCommand={executeCommand} currentUser={user()} {...properties} />);
  return {session, service, executeCommand};
}

describe('MigrationWorkspace', () => {
  beforeEach(() => usePreferences.setState({data: [], version: Date.now(),
    isLoading: false, failed: false}));
  test.each([['migration_portfolio', 'Migration portfolio'],
    ['assessment', 'Migration compatibility assessment'],
    ['mapping_designer', 'Migration mapping decisions'],
    ['schema_plan', 'Ordered migration schema plan'],
    ['data_movement', 'Migration copy streams'],
    ['validation', 'Migration validation results'],
    ['cutover_runbook', 'Migration cutover runbook']])(
    'renders the complete %s surface', (surface, name) => {
      mount(surface); expect(screen.getByLabelText(name)).toBeVisible();
      expect(screen.getByRole('navigation', {name: 'Migration surfaces'})).toBeVisible();
    });
  test('run monitor uses explicit empty state before phase execution', () => {
    mount('run_monitor'); expect(screen.getByText('No migration phases have executed.')).toBeVisible();
  });
  test('run monitor exposes task graph dependencies and live metrics', () => {
    mount('run_monitor', {tasks: [{id: 'task-two', label: 'Validate migration',
      state: 'running', dependencies: ['task-one']}], activeTaskId: 'task-two'});
    expect(screen.getByRole('tree', {name: 'Migration task graph'})).toHaveTextContent(
      'after task-one');
    expect(screen.getByLabelText('Migration live metrics')).toHaveTextContent('CDC lag');
  });
  test('assessment routes through the registered command boundary', () => {
    const {executeCommand} = mount('assessment');
    fireEvent.click(screen.getByRole('button', {name: 'Run assessment'}));
    expect(executeCommand).toHaveBeenCalledWith('migration.assess.run', {});
  });
  test('schema plan separates validation from non-mutating dry run', () => {
    const {executeCommand} = mount('schema_plan');
    fireEvent.click(screen.getByRole('button', {name: 'Validate plan'}));
    fireEvent.click(screen.getByRole('button', {name: 'Dry run'}));
    expect(executeCommand.mock.calls.map((item) => item[0])).toEqual([
      'migration.plan.validate', 'migration.dry_run']);
  });
  test('copy surface explicitly labels committed-checkpoint resume', () => {
    const base = sessionValue(); const checkpoint = {unitId: 'orders-copy', state: 'committed',
      rowDocumentCount: 2, byteCount: 128};
    mount('data_movement', {runtime: {...base.runtime, checkpoints: [checkpoint]}});
    expect(screen.getByRole('button', {name: 'Resume from committed checkpoints'})).toBeVisible();
  });
  test('cutover remains disabled and explains independent blockers', () => {
    mount('cutover_runbook'); expect(screen.getByText(/Cutover blockers:/)).toBeVisible();
    expect(screen.getByRole('button', {name: 'Arm cutover'})).toBeDisabled();
  });
  test.each([['permission_denied', 'Permission denied'], ['validation_error', 'validation error'],
    ['runtime_failure', 'runtime failure'], ['partial', 'is partial'], ['disconnected', 'is disconnected'],
    ['read_only', 'is read only'], ['stale', 'is stale']])('renders %s explicitly', (state, message) => {
    mount('migration_portfolio', {state, error: ''});
    expect(screen.getAllByText(new RegExp(message, 'i'))[0]).toBeVisible();
  });
  test.each([['loading', 'Loading'], ['background_task_active', 'Migration background task']])(
    'renders %s while retaining navigation', (state, label) => {
      mount('migration_portfolio', {state});
      expect(screen.getByRole('status', {name: label})).toBeVisible();
      expect(screen.getByRole('navigation', {name: 'Migration surfaces'})).toBeVisible();
    });
  test.each(['cdeadmin_standard', 'compact_expert', 'low_vision', 'high_contrast_light'])(
    'remains operable under %s presentation', (profile) => {
      usePreferences.setState({data: [{id: 1, module: 'misc', name: 'accessibility_profile',
        value: profile}], version: Date.now(), isLoading: false, failed: false});
      const {executeCommand} = mount('assessment');
      fireEvent.click(screen.getByRole('button', {name: 'Run assessment'}));
      expect(executeCommand).toHaveBeenCalledWith('migration.assess.run', {});
    });
  test('provides inspector facts without hiding native plan state', () => {
    expect(migrationInspector(sessionValue())).toMatchObject({phase: 'design', strategy: 'offline',
      source: expect.objectContaining({provider: 'firebird'}),
      target: expect.objectContaining({provider: 'postgresql'})});
  });
  test('opens projects through the shared activity navigator', () => {
    const service = fakeService(); const onOpen = jest.fn(); const Component = withTheme(MigrationNavigator);
    render(<Component service={service} onOpen={onOpen} />);
    fireEvent.click(screen.getByRole('treeitem', {name: /Operations migration/}));
    expect(onOpen).toHaveBeenCalledWith('migration-one', 'migration_portfolio');
  });
});
