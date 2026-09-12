/////////////////////////////////////////////////////////////
//
// CDEadmin - Multi-engine Database Administration
//
// Copyright (C) 2013 - 2026, The pgAdmin Development Team
// This software is released under the PostgreSQL Licence
//
//////////////////////////////////////////////////////////////

import {loadDDNLibraries} from './library';
import {
  ddnAssetPayload,
  validateDDNAssetRef,
  validateDDNFiles,
  validateDDNSnapshot,
} from './contracts';

export const DDN_PERSISTENCE_STATES = Object.freeze({
  CLEAN: 'clean',
  DIRTY: 'dirty',
  SAVING: 'saving',
  SAVED_NEWER_EDITS: 'saved-newer-edits',
  SAVE_FAILED: 'save-failed',
  EXTERNAL_CONFLICT: 'external-conflict',
});

export class DDNSessionController {
  static async create(options={}) {
    const libraries = await loadDDNLibraries();
    return new DDNSessionController(libraries, options);
  }

  constructor(libraries, options={}) {
    this.libraries = libraries;
    this.assetRef = options.assetRef ?
      validateDDNAssetRef(options.assetRef) : null;
    const sessionOptions = {
      readOnly: Boolean(options.readOnly),
      validation: options.validation || 'strict',
      overrides: options.overrides,
    };
    if(options.snapshot) {
      const snapshot = validateDDNSnapshot(options.snapshot);
      sessionOptions.files = snapshot.files;
      sessionOptions.entry = snapshot.entry;
      sessionOptions.view = snapshot.view;
      sessionOptions.overrides = snapshot.overrides;
    } else if(options.files) {
      sessionOptions.files = validateDDNFiles(options.files);
      sessionOptions.entry = options.entry;
      sessionOptions.view = options.view;
    }
    this.session = libraries.designer.createSession(sessionOptions);
    this.persistenceState = DDN_PERSISTENCE_STATES.CLEAN;
    this.lastError = null;
    this.listeners = new Set();
    this.destroyed = false;
    this.unsubscribe = this.session.subscribe((event) => {
      if(event.type === 'change') {
        this.persistenceState = DDN_PERSISTENCE_STATES.DIRTY;
      }
      this._emit({type: 'session', event});
    });
  }

  _alive() {
    if(this.destroyed) throw new Error('DDN session has been destroyed.');
  }

  _emit(event) {
    for(const listener of this.listeners) listener(this.state(), event);
  }

  state() {
    return Object.freeze({
      entry: this.session.entry,
      view: this.session.view,
      revision: this.session.revision,
      readOnly: this.session.readOnly,
      validation: this.session.validation,
      persistence: this.persistenceState,
      history: Object.freeze({...this.session.history()}),
      lastError: this.lastError,
    });
  }

  subscribe(listener) {
    this._alive();
    if(typeof listener !== 'function') {
      throw new TypeError('DDN session listener must be a function.');
    }
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  inspect() { this._alive(); return this.session.inspect(); }
  sourceOf(id) { this._alive(); return this.session.sourceOf(id); }
  getFiles() { this._alive(); return this.session.getFiles(); }
  render() { this._alive(); return this.session.render(); }
  review(all=false) { this._alive(); return this.session.review(all); }
  exportSVG() { this._alive(); return this.session.exportSVG(); }
  snapshot() { this._alive(); return validateDDNSnapshot(this.session.snapshot()); }
  evaluateDecision(input) { this._alive(); return this.session.evaluateDecision(input); }
  simulateLifecycle(events, expected) {
    this._alive();
    return this.session.simulateLifecycle(events, expected);
  }

  prepare(command) { this._alive(); return this.session.prepare(command); }
  commit(plan) { this._alive(); return this.session.commit(plan); }
  execute(command) { this._alive(); return this.session.execute(command); }
  updateSource(file, source) { this._alive(); this.session.updateSource(file, source); }
  replaceFiles(files) { this._alive(); this.session.replaceFiles(validateDDNFiles(files)); }
  renameFile(from, to) { this._alive(); this.session.renameFile(from, to); }
  removeFile(path) { this._alive(); this.session.removeFile(path); }
  undo() { this._alive(); return this.session.undo(); }
  redo() { this._alive(); return this.session.redo(); }
  setReadOnly(value) { this._alive(); this.session.setReadOnly(Boolean(value)); }
  setValidation(value) { this._alive(); this.session.setValidation(value); }
  setView(entry, view) { this._alive(); this.session.setView(entry, view); }
  setOptions(value) { this._alive(); this.session.setOptions(value); }
  getOptions() { this._alive(); return this.session.getOptions(); }
  resetOptions() { this._alive(); this.session.resetOptions(); }
  load(snapshot) { this._alive(); this.session.load(validateDDNSnapshot(snapshot)); }

  async save(saveAsset) {
    this._alive();
    if(!this.assetRef) throw new Error('DDN session is not bound to an AssetRef.');
    if(typeof saveAsset !== 'function') {
      throw new TypeError('DDN asset save authority must be a function.');
    }
    const revision = this.session.revision;
    const payload = ddnAssetPayload(this.assetRef, this.session.snapshot());
    this.persistenceState = DDN_PERSISTENCE_STATES.SAVING;
    this.lastError = null;
    this._emit({type: 'saving', revision});
    try {
      const result = await saveAsset(payload, {
        expectedAssetVersion: this.assetRef.assetVersion,
        sourceRevision: revision,
      });
      if(result?.assetRef) this.assetRef = validateDDNAssetRef(result.assetRef);
      this.persistenceState = this.session.revision === revision ?
        DDN_PERSISTENCE_STATES.CLEAN :
        DDN_PERSISTENCE_STATES.SAVED_NEWER_EDITS;
      this._emit({type: 'saved', revision, result});
      return result;
    } catch(error) {
      this.lastError = error;
      this.persistenceState = error?.code === 'asset_conflict' ?
        DDN_PERSISTENCE_STATES.EXTERNAL_CONFLICT :
        DDN_PERSISTENCE_STATES.SAVE_FAILED;
      this._emit({type: 'save-error', revision, error});
      throw error;
    }
  }

  destroy() {
    if(this.destroyed) return;
    this.unsubscribe?.();
    this.listeners.clear();
    this.session.destroy();
    this.destroyed = true;
  }
}
