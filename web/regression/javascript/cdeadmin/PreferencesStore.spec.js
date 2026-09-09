/////////////////////////////////////////////////////////////
//
// CDEadmin - Multi-engine Database Administration
//
// Copyright (C) 2013 - 2026, The pgAdmin Development Team
// This software is released under the PostgreSQL Licence
//
//////////////////////////////////////////////////////////////

const mockApi = {
  get: jest.fn(),
  put: jest.fn(),
};

jest.mock('../../../pgadmin/static/js/api_instance', () =>
  jest.fn(() => mockApi)
);

import usePreferences from '../../../pgadmin/preferences/static/js/store';

describe('CDEadmin preference store write contracts', () => {
  beforeEach(() => {
    mockApi.get.mockReset().mockResolvedValue({data: []});
    mockApi.put.mockReset().mockResolvedValue({data: {success: true}});
    usePreferences.setState({data: {}, version: 0, failed: false});
  });

  it('uses the authenticated full-record endpoint for command preferences', async () => {
    const records = [{
      category_id: 2,
      id: 7,
      mid: 4,
      name: 'show_connector_firebird',
      value: true,
    }];

    await usePreferences.getState().setPreferences(records);

    expect(mockApi.put).toHaveBeenCalledWith('/preferences/', records);
    expect(mockApi.get).toHaveBeenCalledWith('/preferences/get_all');
  });

  it('retains the form endpoint for query-tool quick preferences', async () => {
    const form = new FormData();
    form.append('pref_data', JSON.stringify([{
      module: 'sqleditor',
      name: 'underlined_query_execute_warning',
      value: false,
    }]));

    await usePreferences.getState().setPreference(form);

    expect(mockApi.put).toHaveBeenCalledWith('/preferences/update', form);
  });
});
