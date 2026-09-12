/////////////////////////////////////////////////////////////
//
// CDEadmin - Multi-engine Database Administration
//
// Copyright (C) 2013 - 2026, The pgAdmin Development Team
// This software is released under the PostgreSQL Licence
//
//////////////////////////////////////////////////////////////

import {act, fireEvent, render, screen, waitFor} from '@testing-library/react';
import {withTheme} from '../fake_theme';
import {ProjectExplorer} from 'sources/cdeadmin_ui/projects/ProjectExplorer';

const projects = [{project_id: 'project-one', name: 'Project One', access: 'owner'}];
const state = {assets: [{asset_id: 'diagram-one', name: 'System diagram',
  path: 'diagrams/system.ddn', asset_type: 'ddn-workspace', version: 3,
  validation_state: 'valid'}]};

describe('Project Explorer', () => {
  it('loads projects separately from assets and opens the selected asset', async () => {
    const client = {listProjects: jest.fn().mockResolvedValue(projects),
      project: jest.fn().mockResolvedValue(state)};
    const onOpenAsset = jest.fn();
    const Component = withTheme(ProjectExplorer);
    render(<Component client={client} onOpenAsset={onOpenAsset} />);
    expect(await screen.findByText('Project One')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', {name: 'Expand Project One'}));
    expect(await screen.findByText('System diagram')).toBeInTheDocument();
    fireEvent.doubleClick(screen.getByRole('treeitem', {name: /System diagram/}));
    expect(onOpenAsset).toHaveBeenCalledWith(projects[0], state.assets[0]);
    expect(client.project).toHaveBeenCalledWith('project-one');
  });

  it('filters loaded authored assets by name, path, and type', async () => {
    const client = {listProjects: jest.fn().mockResolvedValue(projects),
      project: jest.fn().mockResolvedValue(state)};
    const Component = withTheme(ProjectExplorer);
    render(<Component client={client} />);
    await screen.findByText('Project One');
    fireEvent.click(screen.getByRole('button', {name: 'Expand Project One'}));
    await screen.findByText('System diagram');
    fireEvent.change(screen.getByLabelText('Filter projects and assets'),
      {target: {value: 'ddn-workspace'}});
    expect(screen.getByText('System diagram')).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Filter projects and assets'),
      {target: {value: 'no-match'}});
    expect(screen.queryByText('Project One')).not.toBeInTheDocument();
  });

  it('reports list failures and retries without hiding the error', async () => {
    const client = {listProjects: jest.fn()
      .mockRejectedValueOnce(new Error('Network unavailable'))
      .mockResolvedValueOnce(projects)};
    const Component = withTheme(ProjectExplorer);
    render(<Component client={client} />);
    expect(await screen.findByRole('alert')).toHaveTextContent('Network unavailable');
    await act(async () => fireEvent.click(screen.getByRole('button', {name: 'Retry'})));
    await waitFor(() => expect(screen.getByText('Project One')).toBeInTheDocument());
    expect(client.listProjects).toHaveBeenCalledTimes(2);
  });

  it('provides intentional empty-state project creation', async () => {
    const onCreateProject = jest.fn();
    const Component = withTheme(ProjectExplorer);
    render(<Component client={{listProjects: () => Promise.resolve([])}}
      onCreateProject={onCreateProject} />);
    const buttons = await screen.findAllByRole('button', {name: 'New project'});
    fireEvent.click(buttons.at(-1));
    expect(onCreateProject).toHaveBeenCalledTimes(1);
  });

  it('creates a validated project through the authenticated client', async () => {
    const client = {listProjects: jest.fn().mockResolvedValue([]),
      createProject: jest.fn().mockResolvedValue({project_id: 'new-project'})};
    const Component = withTheme(ProjectExplorer);
    render(<Component client={client} />);
    const buttons = await screen.findAllByRole('button', {name: 'New project'});
    fireEvent.click(buttons[0]);
    expect(screen.getByRole('dialog', {name: 'New project'})).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText(/Project ID/),
      {target: {value: 'invalid project'}});
    fireEvent.click(screen.getByRole('button', {name: 'Create project'}));
    expect(await screen.findByRole('alert')).toHaveTextContent('without spaces');
    expect(client.createProject).not.toHaveBeenCalled();
    fireEvent.change(screen.getByLabelText(/Project ID/),
      {target: {value: 'new-project'}});
    fireEvent.change(screen.getByLabelText('Project name'),
      {target: {value: 'New Project'}});
    fireEvent.click(screen.getByRole('button', {name: 'Create project'}));
    await waitFor(() => expect(client.createProject).toHaveBeenCalledWith(
      'new-project', {name: 'New Project', description: ''}
    ));
    await waitFor(() => expect(screen.queryByRole('dialog', {name: 'New project'}))
      .not.toBeInTheDocument());
  });
});
