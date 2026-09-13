/////////////////////////////////////////////////////////////
// Application-wide, opt-in visual element identity authority.
/////////////////////////////////////////////////////////////

export const QA_VISUAL_MODE_STORAGE_KEY =
  'cdeadmin.qa.visual-identities.enabled.v1';
export const QA_VISUAL_MODE_EVENT = 'cdeadmin:qa-visual-mode';
export const QA_VISUAL_ID_ATTRIBUTE = 'data-cdeadmin-qa-id';
export const QA_VISUAL_KEY_ATTRIBUTE = 'data-cdeadmin-qa-key';

const EXCLUDED_ELEMENTS = new Set(['SCRIPT', 'STYLE', 'LINK', 'META', 'BASE',
  'TITLE', 'TEMPLATE', 'NOSCRIPT']);
const MAX_ID_LENGTH = 512;

export function readQAVisualMode(storage=window.localStorage) {
  try { return storage?.getItem(QA_VISUAL_MODE_STORAGE_KEY) === 'true'; }
  catch { return false; }
}

export function writeQAVisualMode(storage, enabled) {
  try { storage?.setItem(QA_VISUAL_MODE_STORAGE_KEY, enabled ? 'true' : 'false'); }
  catch { /* Blocked storage must not block authentication. */ }
  return Boolean(enabled);
}

export function requestQAVisualMode(enabled, target=window) {
  writeQAVisualMode(target.localStorage, enabled);
  target.dispatchEvent(new target.CustomEvent(QA_VISUAL_MODE_EVENT, {
    detail: {enabled: Boolean(enabled)}}));
}

function hash(value) {
  let result = 0x811c9dc5;
  for(const character of String(value)) {
    result ^= character.charCodeAt(0);
    result = Math.imul(result, 0x01000193);
  }
  return (result >>> 0).toString(36).padStart(7, '0');
}

function safeToken(value, maximum=48) {
  const token = String(value ?? '').trim().toLowerCase()
    .replace(/[^a-z0-9_-]+/g, '-').replace(/^-+|-+$/g, '');
  if(!token) return '';
  return token.length <= maximum ? token : `${token.slice(0, maximum - 8)}-${
    hash(token)}`;
}

function isElement(value) {
  return value?.nodeType === 1;
}

function visualElement(element) {
  return isElement(element) && !EXCLUDED_ELEMENTS.has(element.tagName) &&
    !element.hasAttribute('data-cdeadmin-qa-overlay');
}

function semanticSegment(element) {
  const tag = safeToken(element.localName || element.tagName || 'element');
  const explicit = element.getAttribute(QA_VISUAL_KEY_ATTRIBUTE);
  if(explicit) return `${tag}.${safeToken(explicit)}`;
  const attributes = [['id', 'id'], ['data-testid', 'test'], ['name', 'name'],
    ['role', 'role'], ['data-command-id', 'command'],
    ['data-surface-id', 'surface']];
  for(const [attribute, kind] of attributes) {
    const value = element.getAttribute(attribute);
    if(value) return `${tag}.${kind}-${hash(value)}`;
  }
  const parent = element.parentElement;
  if(!parent) return tag;
  const peers = [...parent.children].filter((candidate) =>
    candidate.localName === element.localName &&
    !EXCLUDED_ELEMENTS.has(candidate.tagName));
  return `${tag}.${peers.indexOf(element) + 1}`;
}

function rootScope(documentValue, framePrefix='') {
  let locationKey = 'document';
  try {
    locationKey = `${documentValue.location.origin}${documentValue.location.pathname}`;
  } catch { /* Use the non-sensitive default for inaccessible location. */ }
  return `cde.qa.v1.${framePrefix ? `frame-${hash(framePrefix)}.` : ''}doc-${
    hash(locationKey)}`;
}

function generatedIdentity(element, documentValue, framePrefix='') {
  const segments = []; let current = element;
  while(current && current !== documentValue.documentElement) {
    segments.unshift(semanticSegment(current)); current = current.parentElement;
  }
  const candidate = `${rootScope(documentValue, framePrefix)}.${
    segments.join('.')}`;
  return candidate.length <= MAX_ID_LENGTH ? candidate : `${rootScope(
    documentValue, framePrefix)}.path-${hash(candidate)}`;
}

function tooltip(documentValue) {
  const element = documentValue.createElement('div');
  element.setAttribute('data-cdeadmin-qa-overlay', 'true');
  element.setAttribute('aria-hidden', 'true');
  Object.assign(element.style, {position: 'fixed', display: 'none',
    zIndex: '2147483647', pointerEvents: 'none',
    maxWidth: 'min(720px, calc(100vw - 24px))', padding: '5px 8px',
    color: '#ffffff', background: '#111827', border: '1px solid #67e8f9',
    borderRadius: '3px', fontFamily: 'monospace', fontSize: '12px',
    lineHeight: '1.4', overflowWrap: 'anywhere',
    boxShadow: '0 2px 8px rgba(0,0,0,.45)'});
  documentValue.body.appendChild(element); return element;
}

function eventElement(event) {
  const path = event.composedPath?.() ?? [event.target];
  return path.find((value) => isElement(value) &&
    value.hasAttribute?.(QA_VISUAL_ID_ATTRIBUTE)) ?? null;
}

export class QAVisualIdentityController {
  constructor({windowValue=window, storage=windowValue.localStorage,
    observerFactory=(callback) => new windowValue.MutationObserver(callback)}={}) {
    this.window = windowValue; this.storage = storage;
    this.observerFactory = observerFactory; this.enabled = false;
    this.started = false; this.roots = new Map(); this.claims = new Map();
    this.assigned = new Set();
    this.onModeRequest = (event) => this.setEnabled(event.detail?.enabled ?? true);
    this.onStorage = (event) => {
      if(event.key === QA_VISUAL_MODE_STORAGE_KEY) this.setEnabled(
        event.newValue === 'true', {persist: false});
    };
  }

  start() {
    if(this.started) return this;
    this.started = true;
    this.window.addEventListener(QA_VISUAL_MODE_EVENT, this.onModeRequest);
    this.window.addEventListener('storage', this.onStorage);
    this.setEnabled(readQAVisualMode(this.storage), {persist: false});
    return this;
  }

  stop() {
    if(!this.started) return;
    this.setEnabled(false, {persist: false});
    this.window.removeEventListener(QA_VISUAL_MODE_EVENT, this.onModeRequest);
    this.window.removeEventListener('storage', this.onStorage);
    this.started = false;
  }

  setEnabled(enabled, {persist=true}={}) {
    enabled = Boolean(enabled);
    if(persist) writeQAVisualMode(this.storage, enabled);
    if(this.enabled === enabled) return enabled;
    this.enabled = enabled;
    if(enabled) this.observeDocument(this.window.document);
    else this.clear();
    return enabled;
  }

  observeDocument(documentValue, framePrefix='') {
    if(!documentValue?.documentElement || this.roots.has(documentValue)) return;
    const state = {framePrefix, observer: this.createObserver(documentValue,
      framePrefix), overlay: null, listeners: {}, shadow: false};
    const show = (event) => this.showHint(documentValue, event);
    const hide = () => this.hideHint(documentValue);
    state.listeners = {pointerover: show, pointermove: show, pointerout: hide,
      focusin: show, focusout: hide};
    for(const [name, listener] of Object.entries(state.listeners)) {
      documentValue.addEventListener(name, listener, true);
    }
    state.observer.observe(documentValue.documentElement,
      {childList: true, subtree: true});
    this.roots.set(documentValue, state);
    documentValue.documentElement.dataset.cdeadminQaVisualIds = 'true';
    this.scan(documentValue.body ?? documentValue.documentElement,
      documentValue, framePrefix);
  }

  createObserver(documentValue, framePrefix) {
    return this.observerFactory((records) => {
      for(const record of records) for(const node of record.removedNodes) {
        this.releaseTree(node);
      }
      for(const record of records) for(const node of record.addedNodes) {
        this.scan(node, documentValue, framePrefix);
      }
    });
  }

  scan(root, documentValue=root?.ownerDocument ?? this.window.document,
    framePrefix='') {
    if(!this.enabled || !root) return;
    const elements = isElement(root) ? [root, ...root.querySelectorAll('*')] : [];
    for(const element of elements) {
      if(!visualElement(element)) continue;
      this.assign(element, documentValue, framePrefix);
      if(element.shadowRoot) this.observeRoot(element.shadowRoot, documentValue,
        `${framePrefix}.${element.getAttribute(QA_VISUAL_ID_ATTRIBUTE)}`);
      if(element.localName === 'iframe') this.observeFrame(element, framePrefix);
    }
  }

  observeRoot(root, documentValue, framePrefix) {
    if(this.roots.has(root)) return;
    const observer = this.createObserver(documentValue, framePrefix);
    observer.observe(root, {childList: true, subtree: true});
    this.roots.set(root, {observer, overlay: null, listeners: {},
      framePrefix, shadow: true});
    for(const element of root.children) this.scan(element, documentValue,
      framePrefix);
  }

  observeFrame(frame, framePrefix) {
    const connect = () => {
      try {
        const documentValue = frame.contentDocument;
        if(documentValue?.documentElement) this.observeDocument(documentValue,
          `${framePrefix}.${frame.getAttribute(QA_VISUAL_ID_ATTRIBUTE)}`);
      } catch { /* Cross-origin frames remain one identified visual item. */ }
    };
    connect(); frame.addEventListener('load', connect, {once: true});
  }

  assign(element, documentValue, framePrefix) {
    if(element.hasAttribute(QA_VISUAL_ID_ATTRIBUTE)) {
      const existing = element.getAttribute(QA_VISUAL_ID_ATTRIBUTE);
      if(this.claims.get(existing) === element) return existing;
    }
    const base = generatedIdentity(element, documentValue, framePrefix);
    let identity = base; let instance = 1;
    while(this.claims.has(identity) && this.claims.get(identity) !== element) {
      identity = `${base}.instance-${++instance}`;
    }
    element.setAttribute(QA_VISUAL_ID_ATTRIBUTE, identity);
    this.claims.set(identity, element); this.assigned.add(element);
    return identity;
  }

  releaseTree(root) {
    if(!isElement(root)) return;
    for(const element of [root, ...root.querySelectorAll(
      `[${QA_VISUAL_ID_ATTRIBUTE}]`)]) this.release(element);
  }

  release(element) {
    const identity = element.getAttribute?.(QA_VISUAL_ID_ATTRIBUTE);
    if(identity && this.claims.get(identity) === element) this.claims.delete(identity);
    this.assigned.delete(element);
  }

  showHint(documentValue, event) {
    const target = eventElement(event);
    if(!target) return this.hideHint(documentValue);
    const state = this.roots.get(documentValue);
    if(!state) return;
    state.overlay ??= tooltip(documentValue);
    state.overlay.textContent = target.getAttribute(QA_VISUAL_ID_ATTRIBUTE);
    const rect = target.getBoundingClientRect();
    const x = Number.isFinite(event.clientX) && event.clientX > 0 ?
      event.clientX : rect.left;
    const y = Number.isFinite(event.clientY) && event.clientY > 0 ?
      event.clientY : rect.bottom;
    state.overlay.style.left = `${Math.max(8, Math.min(x + 12,
      documentValue.documentElement.clientWidth - 300))}px`;
    state.overlay.style.top = `${Math.max(8, Math.min(y + 12,
      documentValue.documentElement.clientHeight - 50))}px`;
    state.overlay.style.display = 'block';
  }

  hideHint(documentValue) {
    const overlay = this.roots.get(documentValue)?.overlay;
    if(overlay) overlay.style.display = 'none';
  }

  clear() {
    for(const [root, state] of this.roots) {
      state.observer.disconnect();
      if(!state.shadow) for(const [name, listener] of Object.entries(
        state.listeners)) root.removeEventListener(name, listener, true);
      state.overlay?.remove();
      if(root.documentElement) delete root.documentElement.dataset.cdeadminQaVisualIds;
    }
    for(const element of this.assigned) {
      element.removeAttribute?.(QA_VISUAL_ID_ATTRIBUTE);
    }
    this.roots.clear(); this.claims.clear(); this.assigned.clear();
  }
}
