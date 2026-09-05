/////////////////////////////////////////////////////////////
//
// CDEadmin - Multi-engine Database Administration
//
// Copyright (C) 2013 - 2026, The pgAdmin Development Team
// This software is released under the PostgreSQL Licence
//
//////////////////////////////////////////////////////////////

import test from 'node:test';
import assert from 'node:assert/strict';
import {
  publicDisplay,
  validatePlacementEvent,
  validateWindowRegistration,
} from '../src/js/workspaceProtocol.js';

test('validates a secret-free placement notification', () => {
  const result = validatePlacementEvent({
    schema: 'cdeadmin.workspace-placement-event.v1',
    toolInstanceId: 'query:1',
    toolKind: 'query_editor',
    placement: 'new-window',
    revision: 4,
  });
  assert.deepEqual(result, {
    schema: 'cdeadmin.workspace-placement-event.v1',
    toolInstanceId: 'query:1',
    toolKind: 'query_editor',
    placement: 'new-window',
    revision: 4,
  });
  assert.equal(Object.isFrozen(result), true);
});

test('rejects invalid identifiers, revisions, and placement operations', () => {
  assert.throws(()=>validatePlacementEvent({
    schema: 'cdeadmin.workspace-placement-event.v1',
    toolInstanceId: '../../unsafe',
    toolKind: 'query_editor',
    placement: 'new-window',
    revision: 0,
  }));
  assert.throws(()=>validatePlacementEvent({
    schema: 'cdeadmin.workspace-placement-event.v1',
    toolInstanceId: 'query:1',
    toolKind: 'query_editor',
    placement: 'arbitrary-operation',
    revision: -1,
  }));
});

test('normalizes window registration and public display geometry', () => {
  assert.deepEqual(validateWindowRegistration({
    workspaceId: 'query_workspace', role: 'detached-tool',
  }), {
    windowId: '', workspaceId: 'query_workspace', role: 'detached-tool',
  });
  assert.deepEqual(publicDisplay({
    id: 2,
    label: 'External',
    scaleFactor: 1.5,
    workArea: {x: 1920, y: 0, width: 2560, height: 1440},
  }, 1), {
    id: '2',
    label: 'External',
    primary: false,
    scaleFactor: 1.5,
    workArea: {x: 1920, y: 0, width: 2560, height: 1440},
  });
});
