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
} from
  'sources/cdeadmin_ui/navigation/providerDatabaseTree';

describe('CDEadmin provider hierarchy behavior', () => {
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
