/////////////////////////////////////////////////////////////
// Zero-Grey navigation, picker, tab and palette verification.
/////////////////////////////////////////////////////////////

import {fireEvent, render, screen, waitFor} from '@testing-library/react';
import {withTheme} from '../fake_theme';
import {
  AssetPicker, Breadcrumbs, ColumnPicker, CommandPalette, ConnectionSelector,
  KeyboardShortcutHint, ListRow, Pagination, ResourcePicker, Tab, TreeRow,
} from 'sources/cdeadmin_ui';

describe('tree, list, tab and breadcrumb navigation', () => {
  it('supports TreeRow selection/open/hierarchy/context/check states and disabled gating', () => {
    const select = jest.fn(); const open = jest.fn(); const toggle = jest.fn();
    const context = jest.fn(); const check = jest.fn();
    const Component = withTheme(TreeRow);
    const {rerender} = render(<Component label="APP.CUSTOMERS" level={3}
      expandable warning="Stale metadata" checked={false} onSelect={select}
      onOpen={open} onToggle={toggle} onContextMenu={context} onCheck={check} />);
    const row = screen.getByRole('treeitem');
    expect(row).toHaveAttribute('aria-level', '3');
    expect(row).not.toHaveAttribute('aria-busy');
    fireEvent.click(row); fireEvent.doubleClick(row);
    fireEvent.keyDown(row, {key: 'ArrowRight'});
    fireEvent.contextMenu(row);
    fireEvent.click(screen.getByRole('checkbox', {name: 'Select APP.CUSTOMERS'}));
    expect(select).toHaveBeenCalled(); expect(open).toHaveBeenCalled();
    expect(toggle).toHaveBeenCalledWith(true); expect(context).toHaveBeenCalled();
    expect(check).toHaveBeenCalledWith(true, expect.anything());
    rerender(<Component label="APP.CUSTOMERS" disabled onSelect={select} onOpen={open} />);
    const calls = select.mock.calls.length; fireEvent.click(screen.getByRole('treeitem'));
    expect(select).toHaveBeenCalledTimes(calls);
  });

  it('supports ListRow select/open, status and disabled behavior', () => {
    const select = jest.fn(); const open = jest.fn();
    const Component = withTheme(ListRow);
    const {rerender} = render(<Component label="Orders" status="warning"
      selected trailing="v3" onSelect={select} onOpen={open} />);
    const row = screen.getByRole('option', {name: /Orders/});
    expect(row).toHaveAttribute('data-status', 'warning');
    fireEvent.keyDown(row, {key: 'Enter'}); fireEvent.doubleClick(row);
    expect(select).toHaveBeenCalled(); expect(open).toHaveBeenCalled();
    rerender(<Component label="Orders" status="error" disabled
      onSelect={select} onOpen={open} />);
    expect(screen.getByRole('option')).toHaveAttribute('aria-disabled', 'true');
  });

  it('activates/closes/marks/reorders tabs with keyboard and middle pointer', () => {
    const activate = jest.fn(); const close = jest.fn(); const drag = jest.fn();
    const Component = withTheme(Tab);
    render(<Component label="Customer query" active dirty attention closable
      onActivate={activate} onClose={close} onDragStart={drag} />);
    const tab = screen.getByRole('tab', {name: /Customer query/});
    expect(tab).toHaveAttribute('aria-selected', 'true');
    expect(tab).toHaveAttribute('draggable', 'true');
    expect(screen.getByText(/Customer query/)).toHaveTextContent('•');
    fireEvent.keyDown(tab, {key: 'Enter'}); fireEvent.mouseDown(tab, {button: 1});
    fireEvent.dragStart(tab);
    expect(activate).toHaveBeenCalled(); expect(close).toHaveBeenCalled();
    expect(drag).toHaveBeenCalled();
  });

  it('collapses long Breadcrumbs while retaining navigable ancestors/current identity', () => {
    const navigate = jest.fn(); const Component = withTheme(Breadcrumbs);
    const items = Array.from({length: 8}, (_value, index) => ({
      id: String(index), label: index === 7 ? 'Current table' : 'Level ' + index,
    }));
    render(<Component items={items} onNavigate={navigate} />);
    expect(screen.getByText('Current table')).toHaveAttribute('aria-current', 'page');
    const ancestor = screen.getByRole('button', {name: 'Level 0'});
    fireEvent.click(ancestor); expect(navigate).toHaveBeenCalledWith(items[0]);
  });
});

describe('pagination, connection and column controls', () => {
  it('paginates known totals without fabricating unknown totals and gates loading', () => {
    const change = jest.fn(); const Component = withTheme(Pagination);
    const {rerender} = render(<Component page={2} pageSize={25} count={60}
      onChange={change} />);
    expect(screen.getByText('Page 2 of 3')).toBeInTheDocument();
    expect(screen.getByText('26–50 of 60')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', {name: 'Previous page'}));
    fireEvent.click(screen.getByRole('button', {name: 'Next page'}));
    expect(change.mock.calls.map((call) => call[0])).toEqual([1, 3]);
    rerender(<Component page={1} pageSize={100} onChange={change} loading />);
    expect(screen.getByText('1–100')).toBeInTheDocument();
    expect(screen.queryByText(/ of 0/)).not.toBeInTheDocument();
    expect(screen.getByRole('button', {name: 'Next page'})).toBeDisabled();
  });

  it.each([
    ['connected', 'success'], ['connecting', 'warning'], ['disconnected', 'error'],
    ['readonly', 'info'], ['production', 'warning'], ['warning', 'warning'],
  ])('shows explicit ConnectionSelector %s and environment identity', (state) => {
    const Component = withTheme(ConnectionSelector);
    render(<Component state={state} environment="production" value="fb"
      connections={[{id: 'fb', provider: 'Firebird', label: 'Local'}]} />);
    expect(screen.getByText(state)).toBeInTheDocument();
    expect(screen.getByText('PRODUCTION')).toBeInTheDocument();
    expect(screen.getByLabelText('Connection')).toBeInTheDocument();
  });

  it('filters/toggles/resets ColumnPicker and enforces whole-picker disabled state', () => {
    const change = jest.fn(); const reset = jest.fn();
    const Component = withTheme(ColumnPicker);
    const originalInnerHeight = window.innerHeight;
    Object.defineProperty(window, 'innerHeight', {configurable: true, value: 1200});
    const anchor = document.createElement('button'); document.body.appendChild(anchor);
    const {rerender} = render(<Component open anchorEl={anchor}
      columns={[{name: 'id', label: 'Identifier'}, {name: 'name', label: 'Name'}]}
      visible={['id']} onChange={change} onReset={reset} />);
    fireEvent.change(screen.getByLabelText('Filter columns'), {target: {value: 'Name'}});
    expect(screen.queryByRole('checkbox', {name: 'Identifier'})).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('checkbox', {name: 'Name'}));
    expect(change).toHaveBeenCalledWith(['id', 'name']);
    fireEvent.click(screen.getByRole('button', {name: 'Restore default columns'}));
    expect(reset).toHaveBeenCalled();
    rerender(<Component open anchorEl={anchor} disabled
      columns={[{name: 'id', label: 'Identifier'}]} visible={['id']} />);
    fireEvent.click(screen.getByRole('button', {name: 'Clear search'}));
    expect(screen.getByRole('checkbox', {name: 'Identifier'})).toBeDisabled();
    expect(screen.getByRole('button', {name: 'Restore default columns'})).toBeDisabled();
    anchor.remove();
    Object.defineProperty(window, 'innerHeight',
      {configurable: true, value: originalInnerHeight});
  });

  it('renders platform-resolved shortcut hints with disabled state', () => {
    const Component = withTheme(KeyboardShortcutHint);
    render(<Component shortcut="Ctrl+K" disabled />);
    expect(screen.getByText('Ctrl+K')).toHaveAttribute('aria-disabled', 'true');
  });
});

describe('resource/asset pickers and command palette', () => {
  it('selects ResourceRef-shaped entries singly and filters within the live scope', () => {
    const confirm = jest.fn(); const Component = withTheme(ResourcePicker);
    render(<Component open items={[
      {id: 'ref-1', label: 'Customers', path: 'fb/local/demo/APP', type: 'table'},
      {id: 'ref-2', label: 'Orders', path: 'mongo/local/demo', type: 'collection'},
    ]} onConfirm={confirm} />);
    fireEvent.change(screen.getByLabelText('Filter resources'), {target: {value: 'Customers'}});
    expect(screen.queryByText('Orders')).not.toBeInTheDocument();
    fireEvent.doubleClick(screen.getByRole('option', {name: /Customers/}));
    expect(confirm).toHaveBeenCalledWith(expect.objectContaining({id: 'ref-1'}));
  });

  it('supports multi-AssetRef selection and explicit empty/error/permission states', () => {
    const confirm = jest.fn(); const Component = withTheme(AssetPicker);
    const {rerender} = render(<Component open multiple items={[
      {id: 'asset-1', label: 'Model', path: 'diagrams/model', type: 'ddn-workspace'},
      {id: 'asset-2', label: 'Query', path: 'queries/q1', type: 'query'},
    ]} onConfirm={confirm} />);
    fireEvent.click(screen.getByRole('option', {name: /Model/}));
    fireEvent.click(screen.getByRole('option', {name: /Query/}));
    fireEvent.click(screen.getByRole('button', {name: 'Select'}));
    expect(confirm).toHaveBeenCalledWith(expect.arrayContaining([
      expect.objectContaining({id: 'asset-1'}), expect.objectContaining({id: 'asset-2'}),
    ]));
    rerender(<Component open items={[]} state="permission" />);
    expect(screen.getByRole('alert')).toHaveTextContent('do not have permission');
    rerender(<Component open items={[]} error="Asset authority unavailable" />);
    expect(screen.getByRole('alert')).toHaveTextContent('Asset authority unavailable');
  });

  it('keeps disconnected state specific to ResourcePicker', () => {
    const Component = withTheme(ResourcePicker);
    render(<Component open items={[]} state="disconnected" />);
    expect(screen.getByRole('alert')).toHaveTextContent('provider connection is disconnected');
  });

  it('groups/filters/navigates/invokes CommandPalette results and shows no-results/loading', async () => {
    const invoke = jest.fn(); const close = jest.fn();
    const Component = withTheme(CommandPalette);
    const {rerender} = render(<Component open onInvoke={invoke} onClose={close}
      commands={[{id: 'refresh', label: 'Refresh metadata'}]}
      resources={[{id: 'table', label: 'Customers'}]}
      assets={[{id: 'model', label: 'Sales model'}]} />);
    expect(screen.getByText('Commands')).toBeInTheDocument();
    expect(screen.getByText('Resources')).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText(/Search commands/), {target: {value: 'Sales'}});
    await waitFor(() => expect(screen.queryByText('Customers')).not.toBeInTheDocument());
    fireEvent.keyDown(screen.getByLabelText(/Search commands/), {key: 'Enter'});
    expect(invoke).toHaveBeenCalledWith(expect.objectContaining({id: 'model'}));
    expect(close).toHaveBeenCalled();
    rerender(<Component open commands={[]} resources={[]} assets={[]} />);
    expect(screen.getByText('No matching commands or items')).toBeInTheDocument();
    rerender(<Component open loading commands={[]} />);
    expect(screen.getByText('Searching')).toBeInTheDocument();
  });
});
