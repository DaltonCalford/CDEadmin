/////////////////////////////////////////////////////////////
//
// CDEadmin - Multi-engine Database Administration
//
// Copyright (C) 2013 - 2026, The pgAdmin Development Team
// This software is released under the PostgreSQL Licence
//
//////////////////////////////////////////////////////////////

export const DDN_ASSET_SCHEMA = 'cdeadmin.ddn-asset.v1';
export const DDN_SURFACE_SCHEMA = 'cdeadmin.ddn-surface.v1';
export const DDN_SNAPSHOT_FORMATS = Object.freeze([
  'ddn-workspace@1',
  'ddn-live-snapshot@0.1',
]);

const SAFE_ID = /^[a-zA-Z0-9][a-zA-Z0-9._:-]{0,255}$/;
const MAX_FILES = 1024;
const MAX_FILE_BYTES = 8 * 1024 * 1024;
const MAX_WORKSPACE_BYTES = 64 * 1024 * 1024;

function stableId(value, name) {
  const normalized = String(value ?? '').trim();
  if(!SAFE_ID.test(normalized)) {
    throw new TypeError(`${name} must be a stable opaque identifier.`);
  }
  return normalized;
}
function validPath(path) {
  return typeof path === 'string' && path.length > 0 && path.length <= 1024 &&
    !path.startsWith('/') && !path.includes('\\') &&
    !path.split('/').some((part) => !part || part === '.' || part === '..' ||
      part.includes('\0'));
}

export function validateDDNFiles(value) {
  if(!value || Array.isArray(value) || typeof value !== 'object') {
    throw new TypeError('DDN files must be an object keyed by relative path.');
  }
  const entries = Object.entries(value);
  if(entries.length === 0 || entries.length > MAX_FILES) {
    throw new TypeError(`DDN workspaces require 1-${MAX_FILES} source files.`);
  }
  let total = 0;
  const files = {};
  for(const [path, source] of entries) {
    if(!validPath(path) || !path.toLowerCase().endsWith('.ddn')) {
      throw new TypeError(`Invalid DDN source path: ${path}`);
    }
    if(typeof source !== 'string') {
      throw new TypeError(`DDN source ${path} must be text.`);
    }
    const bytes = new TextEncoder().encode(source).byteLength;
    if(bytes > MAX_FILE_BYTES) {
      throw new TypeError(`DDN source ${path} exceeds its size limit.`);
    }
    total += bytes;
    if(total > MAX_WORKSPACE_BYTES) {
      throw new TypeError('DDN workspace exceeds its size limit.');
    }
    files[path] = source;
  }
  return Object.freeze(files);
}

export function validateDDNAssetRef(value) {
  if(!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new TypeError('DDN AssetRef must be an object.');
  }
  return Object.freeze({
    schemaVersion: Number(value.schemaVersion ?? 1),
    projectId: stableId(value.projectId, 'Project ID'),
    assetId: stableId(value.assetId, 'Asset ID'),
    assetType: value.assetType === 'ddn-workspace' ? value.assetType :
      (() => { throw new TypeError('DDN asset type must be ddn-workspace.'); })(),
    assetVersion: Number.isSafeInteger(value.assetVersion) &&
      value.assetVersion >= 0 ? value.assetVersion :
      (() => { throw new TypeError('Asset version must be non-negative.'); })(),
    path: String(value.path ?? ''),
    displayName: String(value.displayName ?? ''),
  });
}

export function validateDDNSnapshot(value) {
  if(!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new TypeError('DDN snapshot must be an object.');
  }
  if(!DDN_SNAPSHOT_FORMATS.includes(value.format)) {
    throw new TypeError(`Unsupported DDN snapshot format: ${value.format}`);
  }
  const entry = String(value.entry ?? '').trim();
  const view = String(value.view ?? '').trim();
  if(!validPath(entry) || !view) {
    throw new TypeError('DDN snapshot requires a valid entry and view.');
  }
  return Object.freeze({
    ...value,
    files: validateDDNFiles(value.files),
    entry,
    view,
  });
}

export function ddnAssetPayload(assetRef, snapshot) {
  return Object.freeze({
    schema: DDN_ASSET_SCHEMA,
    assetRef: validateDDNAssetRef(assetRef),
    snapshot: validateDDNSnapshot(snapshot),
  });
}
