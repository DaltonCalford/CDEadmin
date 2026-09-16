/////////////////////////////////////////////////////////////
// CDEadmin - Multi-engine Database Administration
// Copyright (C) 2013 - 2026, The pgAdmin Development Team
// This software is released under the PostgreSQL Licence
//////////////////////////////////////////////////////////////

import {render, screen} from '@testing-library/react';
import ProviderWorkspaceContent from '../../../pgadmin/static/js/Dialogs/ProviderWorkspaceContent';
import getApiInstance from '../../../pgadmin/static/js/api_instance';

jest.mock('../../../pgadmin/static/js/api_instance');

describe('Firebird cache properties', () => {
  it.each([0, 64, null].flatMap(stored => [undefined, 'SERVER_DEFAULT', 'NATIVE_DEFAULT', 'CUSTOM']
    .map(policy => [stored, policy])))(
    'keeps stored buffers %s and saved policy %s distinct from observed allocation', async (stored, policy) => {
      getApiInstance.mockReturnValue({get: jest.fn(async () => ({data: {data: {
        endpoint: {provider_id: 'org.cdeadmin.firebird', verified_runtime_family: 'firebird'},
        database_targets: {targets: [{target_id: 'owned-db', database: '/owned.fdb', display_name: 'owned.fdb',
          configuration: {attachment_cache_policy: policy, attachment_cache_pages: 256}}]},
        resource_page: {items: [{resource_id: 'database:owned', resource_kind: 'database', display_name: 'owned.fdb',
          extensions: {firebird: {native: {page_cache_size: 128, page_buffers: '128', stored_page_buffers: stored}}}}]},
      }}})), post: jest.fn()});
      render(<ProviderWorkspaceContent endpointUrl="/owned" initialTab="properties"
        initialContext={{resource_id: 'owned-db'}} />);
      expect(await screen.findByText('Page cache size (pages)')).toBeInTheDocument();
      expect(screen.getByText('Monitoring page cache allocation (pages)').nextElementSibling).toHaveTextContent('128');
      expect(screen.queryByText('Configured page buffers')).not.toBeInTheDocument();
      if (policy === undefined) {
        expect(screen.queryByText('Attachment page-cache policy')).not.toBeInTheDocument();
      } else {
        expect(screen.getByText('Attachment page-cache policy').nextElementSibling)
          .toHaveTextContent({SERVER_DEFAULT: 'Use server preference', NATIVE_DEFAULT: 'Native default',
            CUSTOM: 'Request cache pages'}[policy]);
      }
      if (policy === 'CUSTOM') {
        expect(screen.getByText('Requested attachment cache pages').nextElementSibling).toHaveTextContent('256');
      } else {
        expect(screen.queryByText('Requested attachment cache pages')).not.toBeInTheDocument();
      }
      if (stored === null) {
        expect(screen.queryByText('Stored page-buffer override (0 uses server default)')).not.toBeInTheDocument();
      } else {
        expect(screen.getByText('Stored page-buffer override (0 uses server default)').nextElementSibling)
          .toHaveTextContent(String(stored));
      }
    });
});
