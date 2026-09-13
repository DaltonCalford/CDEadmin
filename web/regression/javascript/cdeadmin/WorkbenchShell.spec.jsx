/////////////////////////////////////////////////////////////
//
// CDEadmin - Multi-engine Database Administration
//
// Copyright (C) 2013 - 2026, The pgAdmin Development Team
// This software is released under the PostgreSQL Licence
//
//////////////////////////////////////////////////////////////

import {act, fireEvent, render, screen} from '@testing-library/react';
import {withTheme} from '../fake_theme';
import {
  DEFAULT_WORKBENCH_LAYOUT, normalizeWorkbenchLayout, WorkbenchLayoutStore,
  WorkbenchShell,
} from 'sources/cdeadmin_ui/shell/WorkbenchShell';

const activities = [
  {id: 'activity.data', label: 'Data Explorer', iconKey: 'tool.data-explorer'},
  {id: 'activity.projects', label: 'Project Explorer', iconKey: 'tool.project-explorer'},
];

describe('Zero-Grey workbench shell', () => {
  beforeEach(() => window.localStorage.clear());
  it('hosts every required shell region with distinct explorer identities', () => {
    const Component = withTheme(WorkbenchShell);
    render(<Component activities={activities} navigationViews={{
      'activity.data': <div>Live resources</div>,
      'activity.projects': <div>Authored assets</div>,
    }} inspector={<div>Properties</div>} drawer={<div>Problems</div>}
    status={<div>Connected</div>} initialLayout={{drawerVisible: true}}>
      <div>Editor surface</div>
    </Component>);

    expect(screen.getByRole('navigation', {name: 'Application activities'}))
      .toBeInTheDocument();
    const activityTabs = screen.getByRole('navigation', {
      name: 'Application activities',
    }).querySelectorAll('button');
    expect(activityTabs).toHaveLength(2);
    expect([...activityTabs].every((button) => button.querySelector(
      '[data-icon-key]'))).toBe(true);
    expect(activityTabs[0]).toHaveAttribute('data-selected', 'true');
    expect(activityTabs[0]).toHaveAttribute('data-visual-scale', '1.15');
    expect(activityTabs[0]).toHaveAttribute('data-visual-brightness', '1');
    expect(activityTabs[1]).toHaveAttribute('data-selected', 'false');
    expect(activityTabs[1]).toHaveAttribute('data-visual-scale', '1');
    expect(activityTabs[1]).toHaveAttribute('data-visual-brightness', '0.85');
    expect(screen.getByRole('complementary', {name: 'Data Explorer'}))
      .toHaveTextContent('Live resources');
    expect(screen.getByRole('main', {name: 'Main workbench'}))
      .toHaveTextContent('Editor surface');
    expect(screen.getByRole('complementary', {name: 'Inspector'}))
      .toHaveTextContent('Properties');
    expect(screen.getByRole('region', {name: 'Problems, Output, Tasks and Logs'}))
      .toHaveTextContent('Problems');
    expect(screen.getByRole('status', {name: 'Surface status'}))
      .toHaveTextContent('Connected');
  });

  it('switches Data and Project explorers without conflating their content', () => {
    const Component = withTheme(WorkbenchShell);
    render(<Component activities={activities} navigationViews={{
      'activity.data': <div>Live resources</div>,
      'activity.projects': <div>Authored assets</div>,
    }} initialLayout={{inspectorVisible: false}} />);
    fireEvent.click(screen.getByRole('button', {name: 'Project Explorer'}));
    expect(screen.getByRole('complementary', {name: 'Project Explorer'}))
      .toHaveTextContent('Authored assets');
    expect(screen.queryByText('Live resources')).not.toBeInTheDocument();
  });

  it('runs action tabs and can hide navigation for a workspace surface', () => {
    const onSelect = jest.fn();
    const Component = withTheme(WorkbenchShell);
    render(<Component activities={[...activities, {
      id: 'activity.workspace.query', label: 'Query Tool',
      iconKey: 'tool.query', navigationVisible: false, onSelect,
    }]} navigationViews={{}} initialLayout={{inspectorVisible: false}} />);
    fireEvent.click(screen.getByRole('button', {name: 'Query Tool'}));
    expect(onSelect).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole('complementary', {name: 'Query Tool'}))
      .not.toBeInTheDocument();
    expect(screen.getByRole('button', {name: 'Query Tool'}))
      .toHaveAttribute('aria-current', 'page');
  });

  it('reflects an externally activated workspace in the selected activity tab', () => {
    const Component = withTheme(WorkbenchShell);
    render(<Component activities={[...activities, {
      id: 'activity.workspace.query', label: 'Query Tool', iconKey: 'tool.query',
    }]} activeActivityOverride="activity.workspace.query"
    navigationViews={{}} initialLayout={{inspectorVisible: false}} />);
    expect(screen.getByRole('button', {name: 'Query Tool'}))
      .toHaveAttribute('aria-current', 'page');
    expect(screen.getByRole('button', {name: 'Data Explorer'}))
      .not.toHaveAttribute('aria-current');
  });

  it('keeps unavailable activity tabs visible, icon-bearing, and disabled', () => {
    const Component = withTheme(WorkbenchShell);
    render(<Component activities={[...activities, {
      id: 'activity.workspace.schema-diff', label: 'Schema Diff',
      iconKey: 'tool.schema-compare', disabled: true,
    }]} navigationViews={{}} initialLayout={{inspectorVisible: false}} />);
    const tab = screen.getByRole('button', {name: 'Schema Diff'});
    expect(tab).toBeDisabled();
    expect(tab.querySelector('[data-icon-key="tool.schema-compare"]'))
      .toBeInTheDocument();
  });

  it('does not visually select an activity rejected by its permission authority', () => {
    const Component = withTheme(WorkbenchShell);
    render(<Component activities={[...activities, {
      id: 'activity.denied', label: 'Denied Tool', iconKey: 'action.lock',
      navigationVisible: false, onSelect: () => false,
    }]} navigationViews={{'activity.data': <div>Live resources</div>}}
    initialLayout={{inspectorVisible: false}} />);
    fireEvent.click(screen.getByRole('button', {name: 'Denied Tool'}));
    expect(screen.getByRole('button', {name: 'Data Explorer'}))
      .toHaveAttribute('aria-current', 'page');
    expect(screen.getByRole('complementary', {name: 'Data Explorer'}))
      .toBeInTheDocument();
  });

  it('collapses and restores navigation, inspector, and bottom drawer', () => {
    const Component = withTheme(WorkbenchShell);
    render(<Component activities={activities} navigationViews={{}}
      inspector="Details" drawer="Tasks"
      initialLayout={{activeActivity: 'activity.data'}} />);
    fireEvent.click(screen.getByRole('button', {name: 'Hide navigation'}));
    expect(screen.queryByRole('complementary', {name: 'Data Explorer'}))
      .not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', {name: 'Show navigation'}));
    expect(screen.getByRole('complementary', {name: 'Data Explorer'}))
      .toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', {name: 'Hide Inspector'}));
    fireEvent.click(screen.getByRole('button', {name: 'Show Inspector'}));
    expect(screen.getByRole('complementary', {name: 'Inspector'}))
      .toHaveTextContent('Details');
    fireEvent.click(screen.getByRole('button', {name: 'Show bottom drawer'}));
    expect(screen.getByRole('region', {name: 'Problems, Output, Tasks and Logs'}))
      .toHaveTextContent('Tasks');
  });

  it('resizes panels by keyboard and persists device-local geometry', () => {
    const values = new Map();
    const storage = {getItem: (key) => values.get(key),
      setItem: (key, value) => values.set(key, value),
      removeItem: (key) => values.delete(key)};
    const store = new WorkbenchLayoutStore(storage, 'layout');
    const Component = withTheme(WorkbenchShell);
    render(<Component activities={activities} navigationViews={{}}
      store={store} initialLayout={{navigationWidth: 288}} />);
    fireEvent.keyDown(screen.getByRole('separator', {name: 'Resize navigation'}),
      {key: 'ArrowRight'});
    expect(JSON.parse(values.get('layout')).navigationWidth).toBe(292);
    expect(store.load().navigationWidth).toBe(292);
    expect(store.reset()).toBe(DEFAULT_WORKBENCH_LAYOUT);
  });

  it('rejects stale and malformed layout data and clamps geometry', () => {
    expect(normalizeWorkbenchLayout({navigationWidth: 1, inspectorWidth: 9999,
      drawerHeight: Number.NaN})).toMatchObject({
      navigationWidth: 220, inspectorWidth: 520, drawerHeight: 240,
    });
    const storage = {getItem: () => '{broken', setItem: jest.fn(),
      removeItem: jest.fn()};
    expect(new WorkbenchLayoutStore(storage).load()).toEqual(DEFAULT_WORKBENCH_LAYOUT);
    storage.getItem = () => JSON.stringify({schema: 'future.layout.v9'});
    expect(new WorkbenchLayoutStore(storage).load()).toEqual(DEFAULT_WORKBENCH_LAYOUT);
  });

  it('accepts the command-registry activity event', () => {
    const Component = withTheme(WorkbenchShell);
    render(<Component activities={activities} navigationViews={{
      'activity.data': <div>Live resources</div>,
      'activity.projects': <div>Authored assets</div>,
    }} initialLayout={{activeActivity: 'activity.data', navigationVisible: false}} />);
    act(() => window.dispatchEvent(new CustomEvent('cdeadmin:show-activity', {
      detail: 'activity.projects',
    })));
    expect(screen.getByRole('complementary', {name: 'Project Explorer'}))
      .toHaveTextContent('Authored assets');
  });
});
