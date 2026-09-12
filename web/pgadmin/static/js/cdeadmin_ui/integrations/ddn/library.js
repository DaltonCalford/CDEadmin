/////////////////////////////////////////////////////////////
//
// CDEadmin - Multi-engine Database Administration
//
// Copyright (C) 2013 - 2026, The pgAdmin Development Team
// This software is released under the PostgreSQL Licence
//
//////////////////////////////////////////////////////////////

export const DDN_RUNTIME_VERSION = '0.5.0-draft.2';
export const DDN_DESIGNER_VERSION = '0.1.0-preview.1';

let librariesPromise;
const scriptLoads = new Map();

function staticAssetUrl(path) {
  if(typeof document === 'undefined') {
    throw new Error('DDN browser libraries require a document host.');
  }
  const generated = window.resourceBasePath || '/static/js/generated/';
  const base = new URL(generated, document.baseURI);
  return new URL(`../../vendor/ddn/0.5.0-draft.2/${path}`, base).href;
}

function loadScript(path, available) {
  if(available()) return Promise.resolve();
  const source = staticAssetUrl(path);
  if(scriptLoads.has(source)) return scriptLoads.get(source);
  const load = new Promise((resolve, reject) => {
    const script = document.createElement('script');
    script.src = source;
    script.async = true;
    script.dataset.cdeadminDdnLibrary = path;
    script.addEventListener('load', () => {
      scriptLoads.delete(source);
      if(available()) resolve();
      else {
        script.remove();
        reject(new Error(`DDN library did not publish its public API: ${path}`));
      }
    }, {once: true});
    script.addEventListener('error', () => {
      scriptLoads.delete(source);
      script.remove();
      reject(new Error(`Unable to load the bundled DDN library: ${path}`));
    }, {once: true});
    document.head.appendChild(script);
  });
  scriptLoads.set(source, load);
  return load;
}

async function browserLibraryLoader() {
  await loadScript('viewer/ddn.global.js', () => Boolean(window.DDNLive));
  await loadScript('designer-0.1.0-preview.1/ddn-designer.js',
    () => Boolean(window.DDNDesigner));
  return [window.DDNLive, window.DDNDesigner];
}

let libraryLoader = browserLibraryLoader;

function requireFunction(value, name) {
  if(typeof value !== 'function') {
    throw new Error(`DDN public API is missing ${name}.`);
  }
}

function validateLibraries(viewer, designer) {
  if(viewer?.VERSION !== DDN_RUNTIME_VERSION) {
    throw new Error(
      `DDN runtime ${DDN_RUNTIME_VERSION} is required; received `+
      `${viewer?.VERSION || 'an unidentified runtime'}.`
    );
  }
  if(designer?.VERSION !== DDN_DESIGNER_VERSION) {
    throw new Error(
      `DDN Designer ${DDN_DESIGNER_VERSION} is required; received `+
      `${designer?.VERSION || 'an unidentified designer'}.`
    );
  }
  if(designer?.RUNTIME_VERSION !== DDN_RUNTIME_VERSION) {
    throw new Error(
      `DDN Designer requires runtime ${DDN_RUNTIME_VERSION}; received `+
      `${designer?.RUNTIME_VERSION || 'no runtime identity'}.`
    );
  }
  requireFunction(viewer.createWorkspace, 'createWorkspace()');
  requireFunction(viewer.mount, 'mount()');
  requireFunction(designer.createSession, 'createSession()');
  requireFunction(designer.mount, 'Designer.mount()');
  return Object.freeze({viewer, designer});
}

export function loadDDNLibraries() {
  if(!librariesPromise) {
    librariesPromise = Promise.resolve().then(() => libraryLoader())
      .then(([viewer, designer]) => validateLibraries(viewer, designer))
      .catch((error) => {
        librariesPromise = undefined;
        throw error;
      });
  }
  return librariesPromise;
}

export function setDDNLibraryLoaderForTests(loader) {
  if(process.env.NODE_ENV !== 'test' || typeof loader !== 'function') {
    throw new Error('DDN library loader replacement is restricted to tests.');
  }
  librariesPromise = undefined;
  libraryLoader = loader;
}

export function resetDDNLibraryLoaderForTests() {
  librariesPromise = undefined;
  libraryLoader = browserLibraryLoader;
  scriptLoads.clear();
}
