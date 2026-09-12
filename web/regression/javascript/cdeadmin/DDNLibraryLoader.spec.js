/////////////////////////////////////////////////////////////
// CDEadmin DDN browser asset-loader verification.
/////////////////////////////////////////////////////////////

import {
  DDN_DESIGNER_VERSION,
  DDN_RUNTIME_VERSION,
  loadDDNLibraries,
  resetDDNLibraryLoaderForTests,
} from 'sources/cdeadmin_ui/integrations/ddn';

const viewer = () => ({
  VERSION: DDN_RUNTIME_VERSION,
  createWorkspace: jest.fn(),
  mount: jest.fn(),
});

const designer = () => ({
  VERSION: DDN_DESIGNER_VERSION,
  RUNTIME_VERSION: DDN_RUNTIME_VERSION,
  createSession: jest.fn(),
  mount: jest.fn(),
});

describe('DDN same-origin browser library loader', () => {
  beforeEach(() => {
    resetDDNLibraryLoaderForTests();
    delete window.DDNLive;
    delete window.DDNDesigner;
    window.resourceBasePath = '/mounted/static/js/generated/';
  });

  afterEach(() => {
    document.head.querySelectorAll('[data-cdeadmin-ddn-library]')
      .forEach((element) => element.remove());
    delete window.DDNLive;
    delete window.DDNDesigner;
    delete window.resourceBasePath;
    resetDDNLibraryLoaderForTests();
  });

  it('loads exact Viewer then Designer assets and publishes one validated result', async () => {
    const resultPromise = loadDDNLibraries();
    await Promise.resolve();
    await Promise.resolve();
    const viewerScript = document.querySelector(
      '[data-cdeadmin-ddn-library="viewer/ddn.global.js"]');
    expect(viewerScript.src.endsWith(
      '/mounted/static/vendor/ddn/0.5.0-draft.2/viewer/ddn.global.js')).toBe(true);
    window.DDNLive = viewer();
    viewerScript.dispatchEvent(new Event('load'));
    await Promise.resolve();
    const designerScript = document.querySelector(
      '[data-cdeadmin-ddn-library*="ddn-designer.js"]');
    expect(designerScript.src.endsWith(
      '/mounted/static/vendor/ddn/0.5.0-draft.2/designer-0.1.0-preview.1/ddn-designer.js'))
      .toBe(true);
    window.DDNDesigner = designer();
    designerScript.dispatchEvent(new Event('load'));
    const libraries = await resultPromise;
    expect(libraries).toEqual({viewer: window.DDNLive,
      designer: window.DDNDesigner});
    await expect(loadDDNLibraries()).resolves.toBe(libraries);
  });

  it('rejects a missing asset and permits a clean retry', async () => {
    const first = loadDDNLibraries();
    await Promise.resolve();
    await Promise.resolve();
    document.querySelector('[data-cdeadmin-ddn-library]')
      .dispatchEvent(new Event('error'));
    await expect(first).rejects.toThrow('Unable to load');
    const retry = loadDDNLibraries();
    await Promise.resolve();
    await Promise.resolve();
    expect(document.querySelectorAll('[data-cdeadmin-ddn-library]'))
      .toHaveLength(1);
    const retryScript = document.querySelector('[data-cdeadmin-ddn-library]');
    window.DDNLive = viewer();
    retryScript.dispatchEvent(new Event('load'));
    await Promise.resolve();
    const next = document.querySelector('[data-cdeadmin-ddn-library*="designer"]');
    window.DDNDesigner = designer();
    next.dispatchEvent(new Event('load'));
    await expect(retry).resolves.toEqual(expect.objectContaining({
      viewer: window.DDNLive,
      designer: window.DDNDesigner,
    }));
  });
});
