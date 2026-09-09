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
import {showEndpointVerification} from
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
});
