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
import {
  DDNDesignerSurface,
  DDNViewerSurface,
  loadDDNLibraries,
  resetDDNLibraryLoaderForTests,
  setDDNLibraryLoaderForTests,
} from 'sources/cdeadmin_ui/integrations/ddn';
import viewer from '../../../pgadmin/static/vendor/ddn/0.5.0-draft.2/viewer/ddn.mjs';
import designer from '../../../pgadmin/static/vendor/ddn/0.5.0-draft.2/designer-0.1.0-preview.1/ddn-designer.mjs';

const assetRef = {
  schemaVersion: 1,
  projectId: 'project-1',
  assetId: 'diagram-1',
  assetType: 'ddn-workspace',
  assetVersion: 2,
  path: 'diagrams/model.ddn-project',
  displayName: 'Model',
};

describe('CDEadmin DDN workbench surfaces', () => {
  let libraries;
  let files;

  beforeAll(async () => {
    if(window.HTMLDialogElement) {
      window.HTMLDialogElement.prototype.showModal = function() {
        this.open = true;
      };
      window.HTMLDialogElement.prototype.close = function() {
        this.open = false;
      };
    }
    setDDNLibraryLoaderForTests(async () => [viewer, designer]);
    libraries = await loadDDNLibraries();
    libraries.viewer.setTextProvider((text, size) => ({
      width: String(text).length * size * 0.64,
      ascent: size * 0.85,
      descent: size * 0.25,
    }), 'cdeadmin-surface-jest-metrics');
    files = libraries.designer.blankFiles('data');
  });

  afterAll(() => {
    libraries.viewer.setTextProvider(null);
    resetDDNLibraryLoaderForTests();
  });

  it('mounts, renders, reports diagnostics, exports, and destroys a real viewer', async () => {
    const onReady = jest.fn();
    const onExport = jest.fn();
    const executeCommand = jest.fn((_id, context) => context.invoke());
    const Component = withTheme(DDNViewerSurface);
    const {container, unmount} = render(<Component files={files}
      onReady={onReady} onExport={onExport} executeCommand={executeCommand} />);

    await waitFor(() => expect(onReady).toHaveBeenCalledTimes(1));
    expect(container.querySelector('ddn-example')).toBeInTheDocument();
    expect(screen.getByText('0 diagnostics')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', {name: 'Export SVG'}));
    expect(executeCommand).toHaveBeenCalledWith('ddn.viewer.export',
      expect.objectContaining({enabled: true}));
    expect(onExport).toHaveBeenCalledWith(expect.stringContaining('<svg'),
      expect.objectContaining({entry: expect.stringMatching(/\.ddn$/),
        view: expect.any(String)}));
    expect(() => unmount()).not.toThrow();
  });

  it('hosts the real Designer controller with change, undo, save, and export authority', async () => {
    const onReady = jest.fn();
    const onChange = jest.fn();
    const onExport = jest.fn();
    const executeCommand = jest.fn((_id, context) => context.invoke());
    const saveAsset = jest.fn(async (_payload, options) => ({
      assetRef: {...assetRef, assetVersion: options.expectedAssetVersion + 1},
    }));
    const Component = withTheme(DDNDesignerSurface);
    const {container, unmount} = render(<Component files={files} assetRef={assetRef}
      onReady={onReady} onChange={onChange} onExport={onExport}
      saveAsset={saveAsset} executeCommand={executeCommand} />);

    await waitFor(() => expect(onReady).toHaveBeenCalledTimes(1));
    const controller = onReady.mock.calls[0][0];
    const target = controller.inspect().writeTargets[0].id;
    await act(async () => controller.execute({type: 'create', id: 'invoice',
      name: 'Invoice', writeTo: target}));
    await waitFor(() => expect(screen.getByText('Unsaved changes')).toBeInTheDocument());
    expect(onChange).toHaveBeenCalled();
    expect(screen.getByRole('button', {name: 'Undo'})).toBeEnabled();
    fireEvent.click(screen.getByRole('button', {name: 'Undo'}));
    expect(executeCommand).toHaveBeenCalledWith('ddn.designer.undo',
      expect.objectContaining({enabled: true}));
    expect(screen.getByRole('button', {name: 'Redo'})).toBeEnabled();
    fireEvent.click(screen.getByRole('button', {name: 'Redo'}));
    fireEvent.click(screen.getByRole('button', {name: 'Save'}));
    expect(executeCommand).toHaveBeenCalledWith('ddn.designer.save',
      expect.objectContaining({enabled: true}));
    await waitFor(() => expect(saveAsset).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(screen.getByText('Saved')).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', {name: 'Export SVG'}));
    expect(executeCommand).toHaveBeenCalledWith('ddn.designer.export',
      expect.objectContaining({enabled: true}));
    expect(onExport).toHaveBeenCalledWith(expect.stringContaining('<svg'),
      expect.objectContaining({format: 'ddn-workspace@1'}));
    expect(container.querySelector('[aria-label="DDN Designer"]')).toBeInTheDocument();
    expect(() => unmount()).not.toThrow();
  });

  it('enforces read-only Designer UI and exposes initialization errors', async () => {
    const Component = withTheme(DDNDesignerSurface);
    const {rerender} = render(<Component files={files} readOnly />);
    await waitFor(() => expect(screen.getByRole('button', {name: 'Undo'})).toBeDisabled());
    expect(screen.getByRole('button', {name: 'Save'})).toBeDisabled();

    rerender(<Component files={{'broken.ddn': 'not valid DDN'}} />);
    await waitFor(() => expect(screen.getByRole('alert')).toBeInTheDocument());
  });

  it('publishes the exact public DDN catalogue and creates from the shared toolbox', async () => {
    const onReady = jest.fn();
    const onChange = jest.fn();
    const executeCommand = jest.fn((_id, context) => context.invoke());
    const Component = withTheme(DDNDesignerSurface);
    render(<Component files={files} onReady={onReady} onChange={onChange}
      executeCommand={executeCommand} />);
    await waitFor(() => expect(onReady).toHaveBeenCalledTimes(1));
    const toolbox = onReady.mock.calls[0][1];
    expect(toolbox.items).toHaveLength(libraries.designer.catalogue.kinds.length);
    expect(toolbox.items.map((item) => item.id)).toEqual(
      libraries.designer.catalogue.kinds.map((item) => item.id)
    );
    const table = toolbox.items.find((item) => item.id === 'table');
    act(() => toolbox.insert(table.payload));
    expect(screen.getByRole('dialog', {name: 'Create Table'})).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Definition identifier'),
      {target: {value: 'customers'}});
    fireEvent.change(screen.getByLabelText('Display name'),
      {target: {value: 'Customers'}});
    fireEvent.click(screen.getByRole('button', {name: 'Create definition'}));
    await waitFor(() => expect(executeCommand).toHaveBeenCalledWith(
      'ddn.designer.create', expect.objectContaining({enabled: true})
    ));
    await waitFor(() => expect(screen.queryByRole('dialog',
      {name: 'Create Table'})).not.toBeInTheDocument());
    expect(onChange).toHaveBeenCalled();
  });
});
