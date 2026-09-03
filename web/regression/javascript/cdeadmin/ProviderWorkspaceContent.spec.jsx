/////////////////////////////////////////////////////////////
//
// CDEadmin - Multi-engine Database Administration
//
// Copyright (C) 2013 - 2026, The pgAdmin Development Team
// This software is released under the PostgreSQL Licence
//
//////////////////////////////////////////////////////////////

import {fireEvent, render, screen, waitFor} from '@testing-library/react';
import ProviderWorkspaceContent from '../../../pgadmin/static/js/Dialogs/ProviderWorkspaceContent';
import getApiInstance from '../../../pgadmin/static/js/api_instance';

jest.mock('../../../pgadmin/static/js/api_instance');

const bootstrap = {
  endpoint: {
    provider_id: 'org.cdeadmin.mysql',
    verified_runtime_family: 'mysql',
  },
  languages: [{language_profile: 'mysql-sql', title: 'MySQL SQL'}],
  resource_page: {items: [{
    resource_id: 'database:example',
    resource_kind: 'database',
    display_name: 'example',
    authority_path: ['database', 'example'],
  }]},
  visual_admin: {
    engine_id: 'mysql',
    engine_name: 'MySQL',
    objects: [{
      resource_kind: 'database',
      title: 'Database',
      operations: [{
        operation_id: 'create',
        title: 'Create',
        mutation_class: 'admin',
        target_required: false,
        confirmation_required: false,
        blockers: ['provider_native_planner_unavailable'],
        form: {fields: [{
          field_id: 'name', label: 'Name', control: 'text', required: true,
        }]},
      }],
    }],
  },
  semantic_models: {
    items: [],
    capabilities: {
      designer: true, revision_history: true, validation: true,
      lineage: true, query_builder: true, pivot_cellset: true,
      execution_available: true,
      provider_compiler: {execution_available: true},
    },
  },
};

describe('ProviderWorkspaceContent', () => {
  let api;

  beforeEach(() => {
    api = {get: jest.fn(), post: jest.fn()};
    getApiInstance.mockReturnValue(api);
    api.get.mockResolvedValue({data: {data: bootstrap}});
  });

  it('loads provider resources through the workspace endpoint', async () => {
    render(<ProviderWorkspaceContent
      closeModal={jest.fn()}
      endpointUrl="/workspace/1"
    />);
    expect(await screen.findByText('example')).toBeInTheDocument();
    expect(screen.getByText('database')).toBeInTheDocument();
    expect(api.get).toHaveBeenCalledWith('/workspace/1');
  });

  it('opens a provider session, executes and renders rows', async () => {
    api.post.mockImplementation((_url, payload) => {
      const values = {
        open_session: {session_id: 'session-one'},
        execute: {occurrence_id: 'occurrence-one'},
        poll: {
          occurrence: {operation: {terminal: true}},
          rendered_result: {
            component_reference: 'SchemaView/DataGridView',
            view_model: {
              columns: [{name: 'answer'}],
              rows: [{answer: 42}],
            },
          },
        },
      };
      return Promise.resolve({data: {data: values[payload.action]}});
    });
    render(<ProviderWorkspaceContent
      closeModal={jest.fn()}
      endpointUrl="/workspace/1"
      initialTab="studio"
    />);
    expect(await screen.findByText('MySQL SQL')).toBeInTheDocument();
    fireEvent.click(screen.getByText('Run'));
    expect(await screen.findByText('42')).toBeInTheDocument();
    await waitFor(() => expect(api.post).toHaveBeenCalledTimes(3));
    expect(api.post.mock.calls.map((call) => call[1].action)).toEqual([
      'open_session', 'execute', 'poll',
    ]);
  });

  it('renders document results as a structured JSON tree', async () => {
    const documentBootstrap = {
      ...bootstrap,
      endpoint: {
        provider_id: 'org.cdeadmin.mongodb',
        verified_runtime_family: 'mongodb',
      },
      languages: [{
        language_profile: 'mongodb-query-api-json',
        title: 'MongoDB Query API (JSON)',
      }],
    };
    api.get.mockResolvedValue({data: {data: documentBootstrap}});
    api.post.mockImplementation((_url, payload) => {
      const values = {
        open_session: {session_id: 'mongo-session'},
        execute: {occurrence_id: 'mongo-operation'},
        poll: {
          occurrence: {operation: {terminal: true}},
          rendered_result: {
            component_reference: 'cdeadmin/results/DocumentTreeView',
            view_model: {
              family: 'document',
              records: [{_id: {$oid: '0123456789abcdef01234567'}, value: 42}],
            },
          },
        },
      };
      return Promise.resolve({data: {data: values[payload.action]}});
    });
    render(<ProviderWorkspaceContent closeModal={jest.fn()}
      endpointUrl="/workspace/1" initialTab="studio" />);
    fireEvent.click(await screen.findByText('Run'));
    const results = await screen.findByLabelText('Document results');
    expect(results).toHaveTextContent('0123456789abcdef01234567');
    expect(results).toHaveTextContent('42');
  });

  it('renders Neo4j graph results with an accessible element table', async () => {
    api.get.mockResolvedValue({data: {data: {
      ...bootstrap,
      endpoint: {provider_id: 'org.cdeadmin.neo4j', verified_runtime_family: 'neo4j'},
      languages: [{language_profile: 'cypher', title: 'Cypher'}],
      visual_admin: {...bootstrap.visual_admin, model_family: 'graph'},
    }}});
    api.post.mockImplementation((_url, payload) => Promise.resolve({data: {data: {
      open_session: {session_id: 'neo4j-session'},
      execute: {occurrence_id: 'neo4j-operation'},
      poll: {
        occurrence: {operation: {terminal: true}},
        rendered_result: {
          component_reference: 'cdeadmin/results/GraphView',
          view_model: {family: 'graph', records: [{
            n: {kind: 'node', element_id: '4:one', labels: ['Person'],
              properties: {name: 'Alice'}},
          }]},
        },
      },
    }[payload.action]}}));
    render(<ProviderWorkspaceContent closeModal={jest.fn()}
      endpointUrl="/workspace/1" initialTab="studio" />);
    fireEvent.click(await screen.findByText('Run'));
    const results = await screen.findByLabelText('Graph results');
    expect(results).toHaveTextContent('Person');
    expect(results).toHaveTextContent('Alice');
    expect(screen.getByLabelText('Neo4j graph visualization')).toBeInTheDocument();
  });

  it('renders Cassandra wide-column results with native CQL types', async () => {
    api.get.mockResolvedValue({data: {data: {
      ...bootstrap,
      endpoint: {
        provider_id: 'org.cdeadmin.cassandra',
        verified_runtime_family: 'cassandra',
      },
      languages: [{language_profile: 'cql-3', title: 'CQL 3'}],
      visual_admin: {...bootstrap.visual_admin, model_family: 'wide-column'},
    }}});
    api.post.mockImplementation((_url, payload) => Promise.resolve({data: {data: {
      open_session: {session_id: 'cassandra-session'},
      execute: {occurrence_id: 'cassandra-operation'},
      poll: {
        occurrence: {operation: {terminal: true}},
        rendered_result: {
          component_reference: 'cdeadmin/results/WideColumnView',
          view_model: {
            family: 'wide_column',
            columns: [{name: 'tenant', type: 'text'},
              {name: 'payload', type: 'blob'}],
            rows: [{tenant: 'north', payload: {$binary: 'Ynl0ZXM='}}],
            native_observation: {warnings: ['Replica observation warning']},
          },
        },
      },
    }[payload.action]}}));
    render(<ProviderWorkspaceContent closeModal={jest.fn()}
      endpointUrl="/workspace/1" initialTab="studio" />);
    fireEvent.click(await screen.findByText('Run'));
    const results = await screen.findByLabelText('Wide-column results');
    expect(results).toHaveTextContent('tenant');
    expect(results).toHaveTextContent('blob');
    expect(results).toHaveTextContent('Ynl0ZXM=');
    expect(results).toHaveTextContent('Replica observation warning');
  });

  it('renders ClickHouse columnar results with native types', async () => {
    api.get.mockResolvedValue({data: {data: {
      ...bootstrap,
      endpoint: {
        provider_id: 'org.cdeadmin.clickhouse',
        verified_runtime_family: 'clickhouse',
      },
      languages: [{
        language_profile: 'clickhouse-sql', title: 'ClickHouse SQL',
      }],
      visual_admin: {
        ...bootstrap.visual_admin, model_family: 'columnar-analytic',
      },
    }}});
    api.post.mockImplementation((_url, payload) => Promise.resolve({data: {data: {
      open_session: {session_id: 'clickhouse-session'},
      execute: {occurrence_id: 'clickhouse-operation'},
      poll: {
        occurrence: {operation: {terminal: true}},
        rendered_result: {
          component_reference: 'cdeadmin/results/ColumnarView',
          view_model: {
            family: 'columnar',
            columns: [{name: 'category', type: 'LowCardinality(String)'},
              {name: 'total', type: 'Int64'}],
            rows: [{category: 'one', total: 30}],
            statistics: {rows_read: 3},
          },
        },
      },
    }[payload.action]}}));
    render(<ProviderWorkspaceContent closeModal={jest.fn()}
      endpointUrl="/workspace/1" initialTab="studio" />);
    fireEvent.click(await screen.findByText('Run'));
    const results = await screen.findByLabelText('Columnar results');
    expect(results).toHaveTextContent('category');
    expect(results).toHaveTextContent('LowCardinality(String)');
    expect(results).toHaveTextContent('one');
    expect(results).toHaveTextContent('30');
  });

  it('renders provider-declared administration forms and blocked plans', async () => {
    api.post.mockImplementation((_url, payload) => {
      if (payload.action === 'visual_admin_validate') {
        return Promise.resolve({data: {data: {valid: true, errors: []}}});
      }
      return Promise.resolve({data: {data: {
        state: 'blocked',
        execution_available: false,
        blockers: ['provider_native_planner_unavailable'],
      }}});
    });
    render(<ProviderWorkspaceContent
      closeModal={jest.fn()}
      endpointUrl="/workspace/1"
      initialTab="administration"
    />);
    const nameField = await screen.findByRole('textbox', {name: /Name/});
    expect(nameField).toBeInTheDocument();
    fireEvent.change(nameField, {target: {value: 'sample'}});
    fireEvent.click(screen.getByText('Validate and preview'));
    expect(await screen.findByLabelText('Provider plan preview')).toHaveTextContent(
      'provider_native_planner_unavailable'
    );
    expect(screen.getByText('Apply provider plan')).toBeDisabled();
  });

  it('shows provider-owned administration observations', async () => {
    const readyBootstrap = {
      ...bootstrap,
      visual_admin: {
        ...bootstrap.visual_admin,
        objects: [{
          ...bootstrap.visual_admin.objects[0],
          operations: [{
            ...bootstrap.visual_admin.objects[0].operations[0], blockers: [],
          }],
        }],
      },
    };
    api.get.mockResolvedValue({data: {data: readyBootstrap}});
    api.post.mockImplementation((_url, payload) => {
      const responses = {
        visual_admin_validate: {valid: true, errors: []},
        visual_admin_plan: {
          state: 'ready', execution_available: true,
          plan_id: 'plan-one', plan_digest: 'digest-one',
        },
        visual_admin_apply: {
          provider_result: {
            acknowledged: true, local_process_observation_only: true,
          },
        },
      };
      return Promise.resolve({data: {data: responses[payload.action]}});
    });
    render(<ProviderWorkspaceContent closeModal={jest.fn()}
      endpointUrl="/workspace/1" initialTab="administration" />);
    fireEvent.change(await screen.findByRole('textbox', {name: /Name/}), {
      target: {value: 'sample'},
    });
    fireEvent.click(screen.getByText('Validate and preview'));
    await screen.findByLabelText('Provider plan preview');
    fireEvent.click(screen.getByText('Apply provider plan'));
    const result = await screen.findByLabelText('Provider operation result');
    expect(result).toHaveTextContent('local_process_observation_only');
    expect(result).toHaveTextContent('true');
  });

  it('renders and submits typed provider multiselect fields', async () => {
    const readyBootstrap = {
      ...bootstrap,
      visual_admin: {
        ...bootstrap.visual_admin,
        objects: [{
          resource_kind: 'permission', title: 'Permission', operations: [{
            operation_id: 'grant_sql', title: 'Grant SQL privileges',
            mutation_class: 'security', target_required: false,
            confirmation_required: false, blockers: [],
            form: {fields: [
              {field_id: 'username', label: 'User', control: 'text',
                required: true},
              {field_id: 'privileges', label: 'Privileges',
                control: 'multiselect', required: true, options: [
                  {value: 'SELECT', label: 'SELECT'},
                  {value: 'INSERT', label: 'INSERT'},
                ]},
            ]},
          }],
        }],
      },
    };
    api.get.mockResolvedValue({data: {data: readyBootstrap}});
    api.post.mockImplementation((_url, payload) => Promise.resolve({data: {
      data: payload.action === 'visual_admin_validate' ?
        {valid: true, errors: []} : {
          state: 'ready', execution_available: true,
          plan_id: 'permission-plan', plan_digest: 'permission-digest',
        },
    }}));
    render(<ProviderWorkspaceContent closeModal={jest.fn()}
      endpointUrl="/workspace/1" initialTab="administration" />);
    fireEvent.change(await screen.findByRole('textbox', {name: /User/}), {
      target: {value: 'operator'},
    });
    const privileges = screen.getByRole('combobox', {name: /Privileges/});
    const selectInput = privileges.parentElement.querySelector('input');
    fireEvent.change(selectInput, {target: {value: 'SELECT'}});
    fireEvent.click(screen.getByText('Validate and preview'));
    await screen.findByLabelText('Provider plan preview');
    expect(api.post.mock.calls[0][1].request.draft).toEqual({
      username: 'operator', privileges: ['SELECT'],
    });
  });

  it('shows durable operations as review-only after provider restart', async () => {
    api.post.mockResolvedValue({data: {data: {
      restart_safe_audit: true,
      items: [{
        operation_id: 'operation-one', operation_kind: 'backup',
        resource_kind: 'database', stage: 'completed', durable_audit: true,
        live_provider_handle_available: false, cancellable: true,
        provider_finality_authority: true,
        automatic_mutation_retry: false,
      }],
    }}});
    render(<ProviderWorkspaceContent closeModal={jest.fn()}
      endpointUrl="/workspace/1" initialTab="operations" />);
    expect(await screen.findByText(/restart-safe audit record/)).toBeInTheDocument();
    expect(screen.getByText('Observe provider state')).toBeDisabled();
    expect(screen.getByText('Request cancellation')).toBeDisabled();
    expect(screen.getByText('Validate post-state')).toBeDisabled();
  });

  it('edits rows through provider-issued identity plans', async () => {
    const gridBootstrap = {
      ...bootstrap,
      resource_page: {items: [{
        resource_id: 'table:example:widgets', resource_kind: 'table',
        display_name: 'widgets', display_path: ['example', 'widgets'],
        authority_path: ['example', 'table', 'widgets'],
      }]},
      visual_admin: {
        ...bootstrap.visual_admin,
        objects: [{
          resource_kind: 'table', title: 'Table', operations: [
            {operation_id: 'insert', execution_available: true},
            {operation_id: 'update', execution_available: true},
            {operation_id: 'delete', execution_available: true},
          ],
        }],
      },
    };
    api.get.mockResolvedValue({data: {data: gridBootstrap}});
    api.post.mockImplementation((_url, payload) => {
      const responses = {
        visual_admin_rows: {
          columns: [
            {name: 'id', key: true, editable: true},
            {name: 'name', key: false, editable: true},
          ],
          rows: [{
            values: {id: 1, name: 'first'}, identity_token: 'row-one',
          }],
          editable: true,
        },
        visual_admin_validate: {valid: true, errors: []},
        visual_admin_plan: {
          state: 'ready', execution_available: true,
          plan_id: 'plan-one', plan_digest: 'digest-one',
        },
        visual_admin_apply: {provider_result: {accepted: true}},
      };
      return Promise.resolve({data: {data: responses[payload.action]}});
    });
    render(<ProviderWorkspaceContent closeModal={jest.fn()}
      endpointUrl="/workspace/1" initialTab="data" />);
    fireEvent.click(await screen.findByText('Load rows'));
    const name = await screen.findByDisplayValue('first');
    fireEvent.change(name, {target: {value: 'second'}});
    fireEvent.click(screen.getByText('Save'));
    await waitFor(() => expect(api.post).toHaveBeenCalledTimes(5));
    expect(api.post.mock.calls.map((call) => call[1].action)).toEqual([
      'visual_admin_rows', 'visual_admin_validate', 'visual_admin_plan',
      'visual_admin_apply', 'visual_admin_rows',
    ]);
    expect(api.post.mock.calls[1][1].request.draft).toEqual({
      selector: {identity_token: 'row-one'},
      changes: {name: 'second'},
      concurrency_token: 'row-one',
    });
  });

  it('loads and edits MongoDB documents through provider plans', async () => {
    const collection = {
      resource_id: 'mongodb:collection:example:widgets',
      resource_kind: 'collection', display_name: 'widgets',
      display_path: ['MongoDB', 'example', 'widgets'],
      authority_path: ['mongodb', 'collection', 'example', 'widgets'],
    };
    const documentBootstrap = {
      ...bootstrap,
      resource_page: {items: [collection]},
      visual_admin: {
        engine_id: 'mongodb', model_family: 'document',
        objects: [{
          resource_kind: 'document', title: 'Document', operations: [
            {operation_id: 'insert', execution_available: true},
            {operation_id: 'update', execution_available: true},
            {operation_id: 'delete', execution_available: true},
          ],
        }],
      },
    };
    api.get.mockResolvedValue({data: {data: documentBootstrap}});
    api.post.mockImplementation((_url, payload) => {
      const responses = {
        visual_admin_rows: {
          documents: [{
            _id: {$oid: '0123456789abcdef01234567'}, name: 'first',
          }],
        },
        visual_admin_validate: {valid: true, errors: []},
        visual_admin_plan: {
          state: 'ready', execution_available: true,
          plan_id: 'plan-one', plan_digest: 'digest-one',
        },
        visual_admin_apply: {provider_result: {acknowledged: true}},
      };
      return Promise.resolve({data: {data: responses[payload.action]}});
    });
    render(<ProviderWorkspaceContent closeModal={jest.fn()}
      endpointUrl="/workspace/1" initialTab="data" />);
    fireEvent.click(await screen.findByText('Load documents'));
    const editor = await screen.findByRole('textbox', {
      name: 'Document JSON',
    });
    fireEvent.change(editor, {target: {value: JSON.stringify({
      _id: {$oid: '0123456789abcdef01234567'}, name: 'second',
    })}});
    fireEvent.click(screen.getByText('Save'));
    await waitFor(() => expect(api.post).toHaveBeenCalledTimes(5));
    expect(api.post.mock.calls[1][1].request).toEqual({
      resource_kind: 'document', operation_id: 'update',
      target_resource: collection,
      draft: {
        selector: {_id: {$oid: '0123456789abcdef01234567'}},
        changes: {name: 'second'},
      },
    });
  });

  it('loads and edits Neo4j nodes through provider-owned plans', async () => {
    const graph = {
      resource_id: 'neo4j:graph:neo4j', resource_kind: 'graph',
      display_name: 'neo4j', authority_path: ['neo4j', 'graph', 'neo4j'],
    };
    api.get.mockResolvedValue({data: {data: {
      ...bootstrap,
      resource_page: {items: [graph]},
      visual_admin: {
        engine_id: 'neo4j', model_family: 'graph', objects: [
          {resource_kind: 'node', operations: [
            {operation_id: 'insert', execution_available: true},
            {operation_id: 'update', execution_available: true},
            {operation_id: 'delete', execution_available: true},
          ]},
          {resource_kind: 'relationship', operations: [
            {operation_id: 'insert', execution_available: true},
            {operation_id: 'update', execution_available: true},
            {operation_id: 'delete', execution_available: true},
          ]},
        ],
      },
    }}});
    api.post.mockImplementation((_url, payload) => Promise.resolve({data: {data: {
      visual_admin_rows: {records: [{n: {
        kind: 'node', element_id: '4:one', labels: ['Person'],
        properties: {name: 'Alice'},
      }}]},
      visual_admin_validate: {valid: true, errors: []},
      visual_admin_plan: {state: 'ready', execution_available: true,
        plan_id: 'graph-plan', plan_digest: 'graph-digest'},
      visual_admin_apply: {provider_result: {records: []}},
    }[payload.action]}}));
    render(<ProviderWorkspaceContent closeModal={jest.fn()}
      endpointUrl="/workspace/1" initialTab="data" />);
    fireEvent.click(await screen.findByText('Load graph'));
    const editor = await screen.findByRole('textbox', {name: 'Node properties'});
    fireEvent.change(editor, {target: {value: '{"name":"Alicia"}'}});
    fireEvent.click(screen.getByText('Save'));
    await waitFor(() => expect(api.post).toHaveBeenCalledTimes(5));
    expect(api.post.mock.calls[1][1].request).toEqual({
      resource_kind: 'node', operation_id: 'update',
      target_resource: {
        resource_kind: 'node', resource_id: 'neo4j:node:4:one',
        extensions: {neo4j: {native: {element_id: '4:one'}}},
        display_name: '4:one',
      },
      draft: {
        selector: {element_id: '4:one'},
        changes: {properties: {name: 'Alicia'}},
      },
    });
  });

  it.each([
    ['cdeadmin/results/TimeSeriesView', 'Time-series results', {
      schema: {time_field: 'time'},
      records: [{time: '2026-09-02T12:00:00Z', usage: 0.5}],
    }],
    ['cdeadmin/results/VectorView', 'Vector results', {
      records: [{id: 7, distance: 0.1, entity: {title: 'nearest'}}],
    }],
    ['cdeadmin/results/SearchView', 'Search results', {
      records: [{_index: 'docs', _id: 'one', _score: 1,
        _source: {title: 'matched'}}],
    }],
  ])('renders analytic result component %s', async (
    componentReference, accessibleName, viewModel
  ) => {
    api.post.mockImplementation((_url, payload) => Promise.resolve({data: {
      data: {
        open_session: {session_id: 'analytic-session'},
        execute: {occurrence_id: 'analytic-operation'},
        poll: {
          occurrence: {operation: {terminal: true}},
          rendered_result: {
            component_reference: componentReference,
            view_model: viewModel,
          },
        },
      }[payload.action],
    }}));
    render(<ProviderWorkspaceContent closeModal={jest.fn()}
      endpointUrl="/workspace/1" initialTab="studio" />);
    await screen.findByText('MySQL SQL');
    fireEvent.click(screen.getByText('Run'));
    expect(await screen.findByLabelText(accessibleName)).toBeInTheDocument();
  });

  it('browses provider-owned analytic data pages', async () => {
    const table = {
      resource_id: 'influxdb:table:cpu', resource_kind: 'table',
      display_name: 'cpu', display_path: ['metrics', 'cpu'],
      authority_path: ['influxdb', 'table', 'metrics', 'cpu'],
    };
    api.get.mockResolvedValue({data: {data: {
      ...bootstrap,
      resource_page: {items: [table]},
      visual_admin: {
        engine_id: 'influxdb', model_family: 'time-series-analytic',
        objects: [{resource_kind: 'table', operations: []}],
      },
    }}});
    api.post.mockResolvedValue({data: {data: {
      records: [{time: '2026-09-02T12:00:00Z', usage: 0.5}],
      continuation: null,
    }}});
    render(<ProviderWorkspaceContent closeModal={jest.fn()}
      endpointUrl="/workspace/1" initialTab="data" />);
    fireEvent.click(await screen.findByText('Load data'));
    expect(await screen.findByText(/"usage": 0.5/)).toBeInTheDocument();
    expect(api.post.mock.calls[0][1]).toEqual({
      action: 'visual_admin_rows',
      request: {target_resource: table, limit: 200, continuation: null},
    });
  });

  it('provides the complete semantic model designer workspace', async () => {
    api.get.mockResolvedValue({data: {data: {
      ...bootstrap,
      resource_page: {items: [{
        resource_id: 'table:sales', resource_kind: 'table',
        display_name: 'sales', display_path: ['analytics', 'sales'],
      }]},
    }}});
    render(<ProviderWorkspaceContent closeModal={jest.fn()}
      endpointUrl="/workspace/1" initialTab="semantic" />);
    expect(await screen.findByLabelText('Model name')).toBeInTheDocument();
    expect(screen.getByText('Dimensions & hierarchies')).toBeInTheDocument();
    expect(screen.getByText('Measures')).toBeInTheDocument();
    expect(screen.getByText('Cube query')).toBeInTheDocument();
    expect(screen.getByText('Materializations')).toBeInTheDocument();
    expect(screen.getByText('Lineage')).toBeInTheDocument();
    expect(screen.getAllByText('Revisions')).toHaveLength(2);
  });

  it('renders semantic query results in the pivot workspace', async () => {
    api.post.mockImplementation((_url, payload) => Promise.resolve({data: {
      data: {
        open_session: {session_id: 'semantic-session'},
        execute: {occurrence_id: 'semantic-operation'},
        poll: {
          occurrence: {operation: {terminal: true}},
          rendered_result: {
            component_reference: 'cdeadmin/results/CubePivotView',
            view_model: {
              family: 'cellset',
              axes: {rows: ['region'], columns: [], pages: []},
              levels: ['region'], measures: ['revenue'],
              cells: [{coordinates: {region: 'North'},
                measures: {revenue: 42.5}}], slice: [],
            },
          },
        },
      }[payload.action],
    }}));
    render(<ProviderWorkspaceContent closeModal={jest.fn()}
      endpointUrl="/workspace/1" initialTab="studio" />);
    fireEvent.click(await screen.findByText('Run'));
    const pivot = await screen.findByLabelText('Cube pivot results');
    expect(pivot).toHaveTextContent('North');
    expect(pivot).toHaveTextContent('42.5');
    expect(pivot).toHaveTextContent('Drill down');
    expect(pivot).toHaveTextContent('Transpose axes');
  });
});
