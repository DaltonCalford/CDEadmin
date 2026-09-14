/////////////////////////////////////////////////////////////
// CDEadmin contribution-host behavioral tests.
/////////////////////////////////////////////////////////////

import {act, fireEvent, render, screen} from '@testing-library/react';
import {withTheme} from '../fake_theme';
import {
  ContributionRegistry, DiagnosticsService, WorkbenchContextService,
} from 'sources/cdeadmin_ui/platform/PlatformRegistry';
import {
  ActiveStatusHost, BottomDrawerHost, InspectorHost,
} from 'sources/cdeadmin_ui/shell/ContributionHosts';

describe('InspectorHost', () => {
  it('keeps long tabs unshrunk and supports roving keyboard navigation', async () => {
    const context = new WorkbenchContextService({surfaceId: 'firebird.table'});
    const inspector = new ContributionRegistry('inspector');
    const toolbox = new ContributionRegistry('toolbox');
    const Component = withTheme(InspectorHost);
    const pages = ['Properties', 'Dependencies and dependents', 'Permissions'];
    render(<Component contextService={context} registry={inspector} toolbox={toolbox}
      corePages={pages.map((label, index) => ({id: String(index), label,
        content: <span>{label} content</span>}))} />);
    const tabs = pages.map((name) => screen.getByRole('tab', {name}));
    expect(tabs[0]).toHaveAttribute('tabindex', '0');
    expect(tabs[1]).toHaveAttribute('tabindex', '-1');
    expect(tabs[1]).toHaveStyle({flexShrink: '0', whiteSpace: 'nowrap',
      minWidth: 'max-content'});
    const key = async (tab, value) => {
      await act(async () => { fireEvent.keyDown(tab, {key: value}); });
    };
    await act(async () => { tabs[0].focus(); });
    await key(tabs[0], 'ArrowRight');
    expect(tabs[1]).toHaveFocus();
    expect(tabs[1]).toHaveAttribute('aria-selected', 'true');
    const panel = screen.getByRole('tabpanel', {name: pages[1]});
    expect(panel).toHaveTextContent('Dependencies and dependents content');
    expect(panel.id).toBe(tabs[1].getAttribute('aria-controls'));
    expect(panel).toHaveAttribute('aria-labelledby', tabs[1].id);
    await key(tabs[1], 'End');
    expect(tabs[2]).toHaveFocus();
    await key(tabs[2], 'ArrowRight');
    expect(tabs[0]).toHaveFocus();
    await key(tabs[0], 'ArrowLeft');
    expect(tabs[2]).toHaveFocus();
    await key(tabs[2], 'Home');
    expect(tabs[0]).toHaveFocus();
    await key(tabs[0], 'Tab');
    expect(tabs[0]).toHaveAttribute('aria-selected', 'true');
    tabs[0].style.direction = 'rtl';
    tabs[1].style.direction = 'rtl';
    await key(tabs[0], 'ArrowLeft');
    expect(tabs[1]).toHaveFocus();
    await key(tabs[1], 'ArrowRight');
    expect(tabs[0]).toHaveFocus();
  });

  it('gives independent inspector hosts unique tab and panel associations', () => {
    const Component = withTheme(InspectorHost);
    const props = {contextService: new WorkbenchContextService(),
      registry: new ContributionRegistry('inspector'),
      toolbox: new ContributionRegistry('toolbox'),
      corePages: [{id: 'properties', label: 'Properties', content: 'Details'}]};
    render(<><Component {...props} /><Component {...props} /></>);
    const tabs = screen.getAllByRole('tab', {name: 'Properties'});
    expect(tabs[0].id).not.toBe(tabs[1].id);
    expect(tabs[0].getAttribute('aria-controls')).not.toBe(
      tabs[1].getAttribute('aria-controls'));
  });

  it('combines core and context-filtered inspector pages and changes tabs', () => {
    const context = new WorkbenchContextService({surfaceId: 'diagram.ddn.viewer',
      validation: [{id: 'one', message: 'A warning'}]});
    const inspector = new ContributionRegistry('inspector');
    inspector.register({id: 'inspector.ddn', moduleId: 'module.ddn',
      label: 'DDN validation',
      when: (value) => value.surfaceId.startsWith('diagram.ddn.'),
      render: (value) => value.validation});
    const toolbox = new ContributionRegistry('toolbox');
    const Component = withTheme(InspectorHost);
    render(<Component contextService={context} registry={inspector} toolbox={toolbox}
      corePages={[{id: 'properties', label: 'Properties',
        content: <span>Core properties</span>}]} />);
    expect(screen.getByRole('tabpanel', {name: 'Properties'})).toHaveTextContent(
      'Core properties'
    );
    fireEvent.click(screen.getByRole('tab', {name: 'DDN validation'}));
    expect(screen.getByRole('tabpanel', {name: 'DDN validation'}))
      .toHaveTextContent('A warning');
    act(() => context.update({surfaceId: 'query.sql'}));
    expect(screen.queryByRole('tab', {name: 'DDN validation'})).not.toBeInTheDocument();
  });

  it('enables toolbox keyboard insertion only when the surface supplies authority', () => {
    const insertToolboxItem = jest.fn();
    const context = new WorkbenchContextService({surfaceId: 'designer',
      insertToolboxItem});
    const inspector = new ContributionRegistry('inspector');
    const toolbox = new ContributionRegistry('toolbox');
    toolbox.register({id: 'toolbox.diagram', moduleId: 'module.ddn',
      label: 'Elements', items: (value) => value.surfaceId === 'designer' ?
        [{id: 'node', label: 'Node', payload: {type: 'node'}}] : []});
    const Component = withTheme(InspectorHost);
    render(<Component contextService={context} registry={inspector} toolbox={toolbox} />);
    const item = screen.getByRole('button', {name: 'Node'});
    fireEvent.keyDown(item, {key: 'Enter'});
    expect(insertToolboxItem).toHaveBeenCalledWith({type: 'node'});
  });
});

describe('BottomDrawerHost', () => {
  it('renders diagnostics, core output and live task progress as separate pages', () => {
    const diagnostics = new DiagnosticsService();
    const taskListeners = new Set();
    const tasks = {list: jest.fn(() => [{id: 'task-1', label: 'Backup',
      state: 'running', progress: 0.5, message: 'Copying'}]),
    subscribe: (listener) => { taskListeners.add(listener);
      return () => taskListeners.delete(listener); }};
    diagnostics.report({id: 'problem.one', origin: 'module.test',
      severity: 'warning', message: 'Check metadata'});
    const Component = withTheme(BottomDrawerHost);
    render(<Component diagnostics={diagnostics} tasks={tasks}
      corePages={[{id: 'output', label: 'Output', content: <span>Process output</span>}]} />);
    expect(screen.getByRole('tabpanel', {name: 'Problems'})).toHaveTextContent(
      'Check metadata'
    );
    fireEvent.click(screen.getByRole('tab', {name: 'Output'}));
    expect(screen.getByRole('tabpanel', {name: 'Output'})).toHaveTextContent('Process output');
    fireEvent.click(screen.getByRole('tab', {name: 'Tasks'}));
    expect(screen.getByRole('tabpanel', {name: 'Tasks'})).toHaveTextContent('Backup — Copying');
    act(() => diagnostics.report({id: 'problem.two', origin: 'module.test',
      severity: 'error', message: 'New error'}));
    fireEvent.click(screen.getByRole('tab', {name: 'Problems'}));
    expect(screen.getByRole('tabpanel', {name: 'Problems'})).toHaveTextContent('New error');
  });
});

describe('ActiveStatusHost', () => {
  it('tracks surface, project, connection, transaction and module status', () => {
    const context = new WorkbenchContextService({surfaceId: 'diagram.ddn.designer',
      surfaceTitle: 'Sales model', projectId: 'sales', connectionState: 'connected',
      transactionState: 'active', persistence: 'dirty'});
    const registry = new ContributionRegistry('status');
    registry.register({id: 'status.save', moduleId: 'module.ddn',
      label: 'Save', value: (value) => value.persistence});
    const Component = withTheme(ActiveStatusHost);
    render(<Component contextService={context} registry={registry} />);
    expect(screen.getByRole('status')).toHaveTextContent('Sales model');
    expect(screen.getByRole('status')).toHaveTextContent('Project: sales');
    expect(screen.getByRole('status')).toHaveTextContent('connected');
    expect(screen.getByRole('status')).toHaveTextContent('Transaction: active');
    expect(screen.getByRole('status')).toHaveTextContent('Save: dirty');
    act(() => context.update({surfaceTitle: 'Inventory', transactionState: 'none'}));
    expect(screen.getByRole('status')).toHaveTextContent('Inventory');
    expect(screen.getByRole('status')).not.toHaveTextContent('Transaction:');
  });
});
