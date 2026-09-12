/////////////////////////////////////////////////////////////
//
// CDEadmin - Multi-engine Database Administration
//
// Copyright (C) 2013 - 2026, The pgAdmin Development Team
// This software is released under the PostgreSQL Licence
//
//////////////////////////////////////////////////////////////

import {
  DDN_DESIGNER_VERSION,
  DDN_PERSISTENCE_STATES,
  DDN_RUNTIME_VERSION,
  DDNSessionController,
  ddnAssetPayload,
  loadDDNLibraries,
  resetDDNLibraryLoaderForTests,
  setDDNLibraryLoaderForTests,
  validateDDNAssetRef,
  validateDDNFiles,
  validateDDNSnapshot,
} from 'sources/cdeadmin_ui/integrations/ddn';
import viewer from '../../../pgadmin/static/vendor/ddn/0.5.0-draft.2/viewer/ddn.mjs';
import designer from '../../../pgadmin/static/vendor/ddn/0.5.0-draft.2/designer-0.1.0-preview.1/ddn-designer.mjs';

const assetRef = (version=0) => ({
  schemaVersion: 1,
  projectId: 'project-1',
  assetId: 'model-1',
  assetType: 'ddn-workspace',
  assetVersion: version,
  path: 'models/customer.ddn-project',
  displayName: 'Customer model',
});

describe('CDEadmin DDN library integration', () => {
  let libraries;

  beforeAll(async () => {
    setDDNLibraryLoaderForTests(async () => [viewer, designer]);
    libraries = await loadDDNLibraries();
    // JSDOM deliberately has no canvas implementation. DDN's documented
    // measurement provider keeps these integration tests deterministic and
    // exercises the same public hook used by non-canvas hosts.
    libraries.viewer.setTextProvider((text, size) => ({
      width: String(text).length * size * 0.64,
      ascent: size * 0.85,
      descent: size * 0.25,
    }), 'cdeadmin-jest-metrics');
  });

  afterAll(() => {
    libraries.viewer.setTextProvider(null);
    resetDDNLibraryLoaderForTests();
  });

  it('loads and verifies the exact approved public libraries once', async () => {
    const again = await loadDDNLibraries();

    expect(again).toBe(libraries);
    expect(libraries.viewer.VERSION).toBe(DDN_RUNTIME_VERSION);
    expect(libraries.designer.VERSION).toBe(DDN_DESIGNER_VERSION);
    expect(libraries.designer.RUNTIME_VERSION).toBe(DDN_RUNTIME_VERSION);
    expect(libraries.viewer.createWorkspace).toEqual(expect.any(Function));
    expect(libraries.designer.createSession).toEqual(expect.any(Function));
  });

  it.each([
    [null, 'must be an object'],
    [{}, 'require'],
    [{'../escape.ddn': 'ddn "0.5";'}, 'Invalid DDN source path'],
    [{'model.txt': 'not ddn'}, 'Invalid DDN source path'],
    [{'model.ddn': 42}, 'must be text'],
  ])('rejects invalid source workspaces %#', (files, message) => {
    expect(() => validateDDNFiles(files)).toThrow(message);
  });

  it('validates AssetRef and snapshot identity independently', () => {
    const files = libraries.designer.blankFiles('data');
    const session = libraries.designer.createSession({files});
    const snapshot = session.snapshot();
    const payload = ddnAssetPayload(assetRef(), snapshot);

    expect(payload.assetRef.projectId).toBe('project-1');
    expect(payload.snapshot.files['model.ddn']).toContain('data model');
    expect(() => validateDDNAssetRef({...assetRef(), assetType: 'table'}))
      .toThrow('ddn-workspace');
    expect(() => validateDDNSnapshot({...snapshot, format: 'svg'}))
      .toThrow('Unsupported DDN snapshot format');
    session.destroy();
  });

  it('renders every view from a real source workspace and retains source maps', async () => {
    const files = libraries.designer.blankFiles('data');
    const workspace = libraries.viewer.createWorkspace(files);
    const entries = workspace.entries().filter((item) => item.views.length);

    expect(entries.length).toBeGreaterThan(0);
    for(const entry of entries) {
      for(const view of entry.views) {
        const result = await workspace.render({
          entry: entry.file,
          view: view.id,
        });
        expect(result.svg).toContain('<svg');
        expect(result.entry).toBe(entry.file);
        expect(result.view).toBe(view.id);
        expect(result.sourceMap).toEqual(expect.any(Object));
        expect(result.modelFingerprint).toEqual(expect.any(String));
      }
    }
    workspace.destroy();
    expect(() => workspace.destroy()).not.toThrow();
  });

  it('executes, prepares, confirms, commits, undoes and redoes source changes', async () => {
    const controller = await DDNSessionController.create({
      files: libraries.designer.blankFiles('data'),
      assetRef: assetRef(),
    });
    const target = controller.inspect().writeTargets[0].id;
    const created = controller.execute({
      type: 'create', id: 'customer', name: 'Customer',
      kind: 'object', writeTo: target,
      fields: [{id: 'customer_id', name: 'Customer ID'}],
    });

    expect(created.status).toBe('valid');
    expect(controller.state().persistence).toBe(DDN_PERSISTENCE_STATES.DIRTY);
    const customer = controller.inspect().definitions.find(
      (item) => item.local === 'customer');
    const plan = controller.prepare({
      type: 'label', id: customer.id, value: 'Account',
    });
    expect(plan.files[0].before).toContain('Customer');
    expect(plan.files[0].after).toContain('Account');
    expect(controller.inspect().definitions.find(
      (item) => item.id === customer.id).name).toBe('Customer');

    controller.commit(plan);
    expect(controller.inspect().definitions.find(
      (item) => item.id === customer.id).name).toBe('Account');
    expect(controller.undo()).toBe(true);
    expect(controller.inspect().definitions.find(
      (item) => item.id === customer.id).name).toBe('Customer');
    expect(controller.redo()).toBe(true);
    expect(controller.review(true).views[0].status).toBe('valid');
    expect(controller.render().svg).toContain('<svg');
    expect(controller.exportSVG()).toContain('<svg');
    controller.destroy();
    expect(() => controller.inspect()).toThrow('destroyed');
    expect(() => controller.destroy()).not.toThrow();
  });

  it('enforces read-only sessions and rejects invalid semantic changes', async () => {
    const controller = await DDNSessionController.create({
      files: libraries.designer.blankFiles('data'),
      readOnly: true,
    });

    expect(() => controller.execute({type: 'delete', id: 'missing'}))
      .toThrow('read-only');
    controller.setReadOnly(false);
    expect(() => controller.execute({type: 'unsupported'}))
      .toThrow('Unsupported command');
    controller.destroy();
  });

  it('persists exact snapshots and updates optimistic AssetRef versions', async () => {
    const controller = await DDNSessionController.create({
      files: libraries.designer.blankFiles('data'),
      assetRef: assetRef(4),
    });
    const target = controller.inspect().writeTargets[0].id;
    controller.execute({type: 'create', id: 'order', writeTo: target});
    const save = jest.fn(async (payload, options) => {
      expect(payload.snapshot.files['model.ddn']).toContain('object order');
      expect(options.expectedAssetVersion).toBe(4);
      return {assetRef: assetRef(5)};
    });

    await controller.save(save);
    expect(controller.state().persistence).toBe(DDN_PERSISTENCE_STATES.CLEAN);
    expect(controller.assetRef.assetVersion).toBe(5);
    controller.destroy();
  });

  it('does not mark newer edits clean when an earlier save completes', async () => {
    let release;
    const pending = new Promise((resolve) => { release = resolve; });
    const controller = await DDNSessionController.create({
      files: libraries.designer.blankFiles('data'),
      assetRef: assetRef(),
    });
    const target = controller.inspect().writeTargets[0].id;
    controller.execute({type: 'create', id: 'first', writeTo: target});
    const saving = controller.save(async () => {
      await pending;
      return {assetRef: assetRef(1)};
    });
    controller.execute({type: 'create', id: 'second', writeTo: target});
    release();
    await saving;

    expect(controller.state().persistence)
      .toBe(DDN_PERSISTENCE_STATES.SAVED_NEWER_EDITS);
    controller.destroy();
  });

  it.each([
    ['asset_conflict', DDN_PERSISTENCE_STATES.EXTERNAL_CONFLICT],
    ['network_error', DDN_PERSISTENCE_STATES.SAVE_FAILED],
  ])('retains dirty source after %s', async (code, state) => {
    const controller = await DDNSessionController.create({
      files: libraries.designer.blankFiles('data'),
      assetRef: assetRef(),
    });
    const error = Object.assign(new Error(code), {code});

    await expect(controller.save(async () => { throw error; }))
      .rejects.toBe(error);
    expect(controller.state().persistence).toBe(state);
    expect(controller.state().lastError).toBe(error);
    expect(controller.getFiles()).toEqual(expect.any(Object));
    controller.destroy();
  });
});
