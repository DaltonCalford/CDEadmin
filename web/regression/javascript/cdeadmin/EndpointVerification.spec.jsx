/////////////////////////////////////////////////////////////
//
// CDEadmin - Multi-engine Database Administration
//
// Copyright (C) 2013 - 2026, The pgAdmin Development Team
// This software is released under the PostgreSQL Licence
//
//////////////////////////////////////////////////////////////

import {waitFor} from '@testing-library/react';
import pgAdmin from 'sources/pgadmin';
import getApiInstance from '../../../pgadmin/static/js/api_instance';
import {showEndpointVerification, showProviderWorkspace} from
  '../../../pgadmin/static/js/Dialogs/index';

jest.mock('../../../pgadmin/static/js/api_instance');

describe('provider endpoint verification', () => {
  let api;
  let node;

  beforeEach(() => {
    api = {post: jest.fn().mockResolvedValue({data: {success: 1}})};
    getApiInstance.mockReturnValue(api);
    pgAdmin.Browser.notifier.showModal = jest.fn();
    node = {generate_url: jest.fn().mockReturnValue('/verify/1')};
  });

  it('does not invent a password prompt for a passwordless provider',
    async () => {
      const onSuccess = jest.fn();
      showEndpointVerification(
        'Verify Endpoint', node,
        {cde_profile_id: 'embedded-native', label: 'SQLite'},
        {}, {}, onSuccess, jest.fn()
      );
      await waitFor(() => expect(api.post).toHaveBeenCalledTimes(1));
      expect(api.post.mock.calls[0][1]).toBeInstanceOf(FormData);
      expect(pgAdmin.Browser.notifier.showModal).not.toHaveBeenCalled();
      expect(onSuccess).toHaveBeenCalledTimes(1);
    });

  it('prompts before verification when the provider requires a secret',
    () => {
      showEndpointVerification(
        'Verify Endpoint', node,
        {cde_profile_id: 'qualified-native', label: 'Secured engine'},
        {}, {}, jest.fn(), jest.fn()
      );
      expect(pgAdmin.Browser.notifier.showModal).toHaveBeenCalledTimes(1);
      expect(api.post).not.toHaveBeenCalled();
    });

  it.each([null, 'database-one'])(
    'retains the focused service task with database scope %s', (targetId) => {
      showProviderWorkspace('Recover shadow', node, {}, {}, 'administration', {
        resource_kind: 'database', operation_id: 'activate_shadow',
        database_target_id: targetId,
      });
      const content = pgAdmin.Browser.notifier.showModal.mock.calls[0][1](
        jest.fn());
      const url = new URL(content.props.endpointUrl, 'http://localhost');
      expect(url.searchParams.get('focused_operation_id'))
        .toBe('activate_shadow');
      expect(url.searchParams.get('database_target_id')).toBe(targetId);
    });

  it('wires committed profile changes to only the owning navigator endpoint', () => {
    const item = {id: 'owned-endpoint'};
    const data = {cde_endpoint: true, runtime_verification_state: 'verified',
      cde_session_authenticated: true, is_password_saved: true};
    const other = {cde_endpoint: true, runtime_verification_state: 'verified'};
    const previousTree = pgAdmin.Browser.tree;
    pgAdmin.Browser.tree = {
      itemData: jest.fn((value) => value === item ? data : other),
      addIcon: jest.fn(), setLabel: jest.fn(),
    };
    try {
      showProviderWorkspace('Edit endpoint', node, data, item, 'connections', {
        server_mode: 'edit',
      });
      const content = pgAdmin.Browser.notifier.showModal.mock.calls[0][1](
        jest.fn());
      expect(data.runtime_verification_state).toBe('verified');
      content.props.onEndpointProfileSaved({display_name: 'Owned Firebird'});
      expect(data.runtime_verification_state).toBe('stale');
      expect(data.cde_session_authenticated).toBe(false);
      expect(data.is_password_saved).toBe(true);
      expect(other.runtime_verification_state).toBe('verified');
      expect(pgAdmin.Browser.tree.addIcon).toHaveBeenCalledWith(item, {
        icon: 'icon-server-not-connected'});
    } finally {
      pgAdmin.Browser.tree = previousTree;
    }
  });
});
