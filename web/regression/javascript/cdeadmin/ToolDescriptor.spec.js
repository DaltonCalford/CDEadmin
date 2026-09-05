/////////////////////////////////////////////////////////////
//
// CDEadmin - Multi-engine Database Administration
//
// Copyright (C) 2013 - 2026, The pgAdmin Development Team
// This software is released under the PostgreSQL Licence
//
//////////////////////////////////////////////////////////////

import {
  createToolDescriptor,
  TOOL_KINDS,
  toolKindFromPanelId,
} from 'sources/cdeadmin_ui/workspace/ToolDescriptor';
import {
  ToolFactoryRegistry,
} from 'sources/cdeadmin_ui/workspace/ToolRegistry';

describe('CDEadmin durable tool descriptors', () => {
  it('creates an immutable secret-free placement and context descriptor', () => {
    const descriptor = createToolDescriptor({
      toolInstanceId: 'id-query-tool_42',
      toolKind: TOOL_KINDS.QUERY_EDITOR,
      restoreRef: 'id-query-tool_42',
      context: {
        providerId: 'org.cdeadmin.firebird',
        endpointId: 7,
        databaseTargetId: 11,
        sessionHandle: 'session:42',
        resultHandle: 'result:81',
      },
      presentation: {title: 'Invoices', iconKey: 'tool.query'},
      placement: {workspaceId: 'queries', windowId: 'main', revision: 3},
      state: {dirty: true, transactionState: 'active'},
      capabilities: {detachable: true, duplicable: true},
    });

    expect(descriptor).toEqual(expect.objectContaining({
      schema: 'cdeadmin.tool-instance.v1',
      toolInstanceId: 'id-query-tool_42',
      restoreRef: 'id-query-tool_42',
    }));
    expect(descriptor.context.providerId).toBe('org.cdeadmin.firebird');
    expect(descriptor.placement.revision).toBe(3);
    expect(Object.isFrozen(descriptor)).toBe(true);
    expect(Object.isFrozen(descriptor.context)).toBe(true);
  });

  it('rejects credentials, query content, and arbitrary launch URLs', () => {
    [
      {password: 'unsafe'},
      {accessToken: 'unsafe'},
      {query: 'select secret from private_table'},
      {toolUrl: 'https://attacker.invalid'},
      {nested: {privateKey: 'unsafe'}},
    ].forEach((unsafe)=>expect(()=>createToolDescriptor({
      toolInstanceId: 'tool-1',
      toolKind: 'query_editor',
      ...unsafe,
    })).toThrow('forbidden'));
  });

  it('classifies existing restorable panel identities', () => {
    expect(toolKindFromPanelId('id-query-tool_9'))
      .toBe(TOOL_KINDS.QUERY_EDITOR);
    expect(toolKindFromPanelId('id-erd-tool_4')).toBe(TOOL_KINDS.ERD);
    expect(toolKindFromPanelId('custom-provider-tool'))
      .toBe(TOOL_KINDS.GENERIC);
  });

  it('restores through a registered factory and refuses placeholders', () => {
    const registry = new ToolFactoryRegistry();
    expect(()=>registry.register({toolKind: 'query_editor'}))
      .toThrow('requires a restore handler');

    const restore = jest.fn(()=>({id: 'restored-panel'}));
    registry.register({
      toolKind: 'query_editor',
      detachable: true,
      duplicable: true,
      requiresLiveSession: true,
      restore,
    });
    const descriptor = createToolDescriptor({
      toolInstanceId: 'tool-1',
      toolKind: 'query_editor',
      capabilities: {detachable: true},
    });

    expect(registry.restore(descriptor, {restoreState: 'opaque'}))
      .toEqual({id: 'restored-panel'});
    expect(restore).toHaveBeenCalledWith(
      descriptor, {restoreState: 'opaque'}
    );
  });
});
