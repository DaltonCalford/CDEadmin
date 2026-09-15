/////////////////////////////////////////////////////////////
//
// CDEadmin - Multi-engine Database Administration
//
// Copyright (C) 2013 - 2026, The pgAdmin Development Team
// This software is released under the PostgreSQL Licence
//
//////////////////////////////////////////////////////////////

import {allowsSoleChildAutomation} from
  'sources/cdeadmin_ui/navigation/providerHierarchy';
import {
  beforeOpenProviderDatabase, providerEndpointSessionReady,
  invalidateProviderEndpointProfile,
} from
  'sources/cdeadmin_ui/navigation/providerDatabaseTree';

describe('CDEadmin provider hierarchy behavior', () => {
  it.each([false, true])('invalidates edited profiles with saved credentials %s', (saved) => {
    const server = {cde_endpoint: true, runtime_verification_state: 'verified',
      cde_session_authenticated: true, connected: false,
      is_password_saved: saved, verified_runtime_family: 'firebird',
      verified_runtime_version: '5.0.4', runtime_evidence_reference: 'old',
      declared_runtime_family: 'firebird', username: 'old-user'};
    const item = {id: 7};
    const database = {_id: 'target-1'};
    const tree = {itemData: jest.fn((value) => value === item ? server : database),
      addIcon: jest.fn(), setLabel: jest.fn(), parent: () => item};
    const result = {display_name: 'Firebird laboratory', route_catalog: {
      routes: [{configuration: {user: 'new-user'}}]}};
    expect(invalidateProviderEndpointProfile(tree, item, result)).toBe(true);
    expect(providerEndpointSessionReady(server)).toBe(false);
    expect(server).toMatchObject({runtime_verification_state: 'stale',
      cde_session_authenticated: false, connected: false,
      verified_runtime_family: null, verified_runtime_version: null,
      runtime_evidence_reference: null, declared_runtime_family: 'firebird',
      is_password_saved: saved, username: 'new-user',
      label: 'Firebird laboratory', _label: 'Firebird laboratory'});
    expect(tree.addIcon).toHaveBeenCalledWith(item, {
      icon: 'icon-server-not-connected'});
    expect(tree.setLabel).toHaveBeenCalledWith(item, {
      label: 'Firebird laboratory'});
    const verify = jest.fn();
    expect(beforeOpenProviderDatabase(tree, {
      callbacks: {verify_cde_endpoint: verify}}, database)).toBe(false);
    expect(verify).toHaveBeenCalledWith(expect.objectContaining({
      item, databaseTargetId: 'target-1'}));
    // A new native verification, not the old saved flag, restores readiness.
    server.runtime_verification_state = 'verified';
    server.cde_session_authenticated = true;
    expect(providerEndpointSessionReady(server)).toBe(true);
  });

  it.each([undefined, {}, {cde_endpoint: false, connected: true}])(
    'does not invalidate an absent or preserved PostgreSQL node %j', (data) => {
      const before = data && {...data};
      const tree = {itemData: () => data, addIcon: jest.fn(), setLabel: jest.fn()};
      expect(invalidateProviderEndpointProfile(tree, {})).toBe(false);
      expect(data).toEqual(before);
      expect(tree.addIcon).not.toHaveBeenCalled();
      expect(tree.setLabel).not.toHaveBeenCalled();
    });

  it('retains unrelated identity when the response omits presentation fields', () => {
    const data = {cde_endpoint: true, username: 'current-user', label: 'current'};
    const tree = {itemData: () => data, addIcon: jest.fn(), setLabel: jest.fn()};
    expect(invalidateProviderEndpointProfile(tree, {}, null)).toBe(true);
    expect(data.username).toBe('current-user');
    expect(data.label).toBe('current');
    expect(tree.setLabel).not.toHaveBeenCalled();
  });
  it('does not auto-expand or auto-select a provider endpoint child', () => {
    expect(allowsSoleChildAutomation({cde_endpoint: true})).toBe(false);
  });

  it('preserves inherited sole-child behavior outside provider trees', () => {
    expect(allowsSoleChildAutomation({cde_endpoint: false})).toBe(true);
    expect(allowsSoleChildAutomation({})).toBe(true);
  });

  it('verifies the owning endpoint before opening its database child', () => {
    const serverItem = {id: 7};
    const databaseItem = {id: 'database-1'};
    const verify = jest.fn();
    const serverNode = {callbacks: {verify_cde_endpoint: verify}};
    const tree = {
      parent: jest.fn(() => serverItem),
      itemData: jest.fn((item) => item === databaseItem ? {_id: 'target-1'} : ({
        cde_endpoint: true,
        runtime_verification_state: 'stale',
      })),
    };

    expect(beforeOpenProviderDatabase(
      tree, serverNode, databaseItem
    )).toBe(false);
    expect(verify).toHaveBeenCalledWith({
      item: serverItem,
      openOnSuccess: true,
      openOnSuccessItem: databaseItem,
      databaseTargetId: 'target-1',
    });
  });

  it('opens a database child after the owning endpoint is authenticated', () => {
    const tree = {
      parent: jest.fn(() => ({id: 7})),
      itemData: jest.fn(() => ({
        cde_endpoint: true,
        runtime_verification_state: 'verified',
        cde_session_authenticated: true,
      })),
    };

    expect(beforeOpenProviderDatabase(
      tree, {callbacks: {}}, {id: 'database-1'}
    )).toBe(true);
  });

  it('does not confuse persisted verification with an active session', () => {
    expect(providerEndpointSessionReady({
      cde_endpoint: true,
      runtime_verification_state: 'verified',
      is_password_saved: false,
      cde_session_authenticated: false,
    })).toBe(false);
    expect(providerEndpointSessionReady({
      cde_endpoint: true,
      runtime_verification_state: 'verified',
      is_password_saved: true,
    })).toBe(true);
  });
});
