/////////////////////////////////////////////////////////////
//
// CDEadmin - Multi-engine Database Administration
//
// Copyright (C) 2013 - 2026, The pgAdmin Development Team
// This software is released under the PostgreSQL Licence
//
//////////////////////////////////////////////////////////////

import {fireEvent, render, screen, waitFor} from '@testing-library/react';
import ProviderWorkspaceContent, {
  ResultControls,
  semanticCrossFilter,
} from '../../../pgadmin/static/js/Dialogs/ProviderWorkspaceContent';
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
  operational_workspace: {
    schema: 'cdeadmin.operational-workspace.v1',
    engine_id: 'mysql',
    distributed: false,
    categories: ['runtime'],
    topology: {available: false, resource_kinds: []},
    facets: [{
      facet_id: 'health',
      title: 'Server and cluster health',
      category: 'runtime',
      summary: 'Provider-reported availability and health state.',
      catalog_state: 'operational',
      unavailable_reason: null,
      resource_kinds: ['database'],
      discovered_resource_count: 1,
      operations: [{
        operation_id: 'create', resource_kind: 'database', title: 'Create',
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
      time_intelligence_operations: ['as_of', 'range', 'period_to_date',
        'period_comparison'],
      time_intelligence_periods: ['day', 'week', 'month', 'quarter', 'year',
        'fiscal_quarter', 'fiscal_year'],
      analytical_window_operations: ['running_sum', 'moving_average', 'lag'],
      scheduled_report_execution: false,
      analytical_profile: {
        title: 'Relational and multidimensional',
        semantic_family: 'relational',
        source_kinds: ['table', 'view', 'materialized-view'],
        source_classifications: ['fact', 'dimension', 'bridge', 'lookup'],
        dimension_kinds: ['attribute', 'time', 'geography'],
        relationship_kinds: ['join', 'bridge'],
        measure_kinds: ['aggregate', 'calculated'],
        grain_vocabulary: 'fact-key',
      },
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

  it('maps chart selections to provider-compiled semantic filters', () => {
    const definition = {dimensions: [{
      id: 'region', field: {source_id: 'sales', field: 'region'},
      hierarchies: [{levels: [{
        id: 'region_level',
        field: {source_id: 'sales', field: 'customer.region'},
      }]}],
    }]};
    const chart = {encodings: {x: 'region_level'}};
    expect(semanticCrossFilter(definition, chart, {value: 'North'}))
      .toEqual({
        field: {source_id: 'sales', field: 'customer.region'},
        operator: 'eq', value: 'North',
      });
    expect(semanticCrossFilter(definition, chart, {value: null}))
      .toEqual({
        field: {source_id: 'sales', field: 'customer.region'},
        operator: 'is_null',
      });
    expect(semanticCrossFilter(definition, {encodings: {x: 'measure'}},
      {value: 42})).toBeNull();
  });

  it('delivers a retained export through a named server-side profile', async () => {
    const post = jest.fn().mockResolvedValue({
      state: 'delivered', automatic_retry: false,
    });
    render(<ResultControls rendered={{
      descriptor: {result_id: 'result-one', export_formats: ['pdf']},
      page: {},
    }} history={[]} post={post} onRendered={jest.fn()}
    setError={jest.fn()} setBusy={jest.fn()} allowedFormats={['pdf']}
    deliveryProfiles={[{
      profile_id: 'archive', label: 'Report archive', kind: 's3',
      allowed_formats: ['pdf'],
    }]} />);
    fireEvent.change(screen.getByLabelText('Object filename'), {
      target: {value: 'quarterly-report.pdf'},
    });
    fireEvent.click(screen.getByText('Deliver'));
    await waitFor(() => expect(post).toHaveBeenCalledWith({
      action: 'result_delivery', request: {
        request_key: expect.stringMatching(
          /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/
        ),
        result_id: 'result-one', format: 'pdf', profile_id: 'archive',
        target: {object_name: 'quarterly-report.pdf'},
      },
    }));
    expect(await screen.findByText(/Delivery state.*delivered/))
      .toBeInTheDocument();
    expect(screen.getByText(/Automatic retry is disabled/))
      .toBeInTheDocument();
  });

  it('loads provider resources through the workspace endpoint', async () => {
    render(<ProviderWorkspaceContent
      closeModal={jest.fn()}
      endpointUrl="/workspace/1"
    />);
    fireEvent.click(await screen.findByRole('treeitem', {name: /database/i}));
    expect(await screen.findByText('example')).toBeInTheDocument();
    expect(screen.getAllByText('database')).toHaveLength(2);
    expect(api.get).toHaveBeenCalledWith('/workspace/1');
  });

  it('navigates the provider tree by keyboard and opens the linked editor', async () => {
    api.post.mockResolvedValue({data: {data: {
      ...bootstrap.resource_page.items[0], generation: 'generation-one',
      extensions: {mysql: {native: {character_set: 'utf8mb4'}}},
    }}});
    render(<ProviderWorkspaceContent closeModal={jest.fn()}
      endpointUrl="/workspace/1" />);
    const branch = await screen.findByRole('treeitem', {name: /database/i});
    fireEvent.focus(branch);
    fireEvent.keyDown(branch, {key: 'ArrowRight'});
    expect(branch).toHaveAttribute('aria-expanded', 'true');
    const resource = await screen.findByRole('treeitem', {name: /example/i});
    fireEvent.keyDown(resource, {key: 'Enter'});
    expect(await screen.findByRole('navigation', {
      name: 'Selected object breadcrumb',
    })).toHaveTextContent('database / example');
    fireEvent.click(screen.getByText('Open object editor'));
    expect(await screen.findByRole('textbox', {name: /Name/}))
      .toBeInTheDocument();
    expect(await screen.findByRole('tab', {name: 'properties'}))
      .toBeInTheDocument();
    expect(screen.getByRole('tabpanel', {name: 'properties object section'}))
      .toHaveTextContent('generation-one');
    expect(api.post).toHaveBeenCalledWith('/workspace/1', {
      action: 'resource_inspect', request: {
        resource_id: 'database:example', generation: undefined,
      },
    });
  });

  it('loads generation-bound navigator continuation pages', async () => {
    api.get.mockResolvedValue({data: {data: {
      ...bootstrap,
      resource_page: {
        ...bootstrap.resource_page, generation: 'generation-one',
        next_cursor: 'cursor-one', total_count: 2,
      },
    }}});
    api.post.mockResolvedValue({data: {data: {
      generation: 'generation-one', next_cursor: null, total_count: 2,
      items: [{
        resource_id: 'schema:example:public', resource_kind: 'schema',
        display_name: 'public', display_path: ['database', 'example', 'public'],
        authority_path: ['database', 'example', 'schema', 'public'],
      }],
    }}});
    render(<ProviderWorkspaceContent closeModal={jest.fn()}
      endpointUrl="/workspace/1" />);
    fireEvent.click(await screen.findByText('Load more provider objects'));
    await waitFor(() => expect(api.post).toHaveBeenCalledWith(
      '/workspace/1', {
        action: 'resource_page', request: {
          continuation: 'cursor-one', generation: 'generation-one',
        },
      }
    ));
    fireEvent.change(screen.getByLabelText('Filter provider objects'), {
      target: {value: 'public'},
    });
    expect(await screen.findByText('public')).toBeInTheDocument();
  });

  it('refreshes only the active provider generation', async () => {
    api.get.mockResolvedValue({data: {data: {
      ...bootstrap,
      resource_page: {
        ...bootstrap.resource_page, generation: 'generation-one',
      },
    }}});
    api.post.mockResolvedValue({data: {data: {
      ...bootstrap.resource_page, generation: 'generation-two',
      items: [{
        ...bootstrap.resource_page.items[0], display_name: 'refreshed',
      }],
    }}});
    render(<ProviderWorkspaceContent closeModal={jest.fn()}
      endpointUrl="/workspace/1" />);
    fireEvent.click(await screen.findByText('Refresh provider objects'));
    await waitFor(() => expect(api.post).toHaveBeenCalledWith(
      '/workspace/1', {
        action: 'resource_refresh', request: {
          generation: 'generation-one',
        },
      }
    ));
    fireEvent.click(await screen.findByRole('treeitem', {name: /database/i}));
    fireEvent.click(await screen.findByRole('treeitem', {name: /example/i}));
    expect(await screen.findByText('refreshed')).toBeInTheDocument();
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

  it('uses provider transaction controls without inferring finality', async () => {
    api.get.mockResolvedValue({data: {data: {
      ...bootstrap,
      languages: [{language_profile: 'mysql-sql', title: 'MySQL SQL',
        transaction_actions: ['commit', 'rollback']}],
    }}});
    api.post.mockImplementation((_url, payload) => Promise.resolve({data: {
      data: payload.action === 'open_session' ? {session_id: 'session-one'} : {
        session_id: 'session-one', transaction_model: 'mysql-native',
        authority_reference: 'provider:org.cdeadmin.mysql',
        provider_payload: {driver_observation_only: true},
      },
    }}));
    render(<ProviderWorkspaceContent closeModal={jest.fn()}
      endpointUrl="/workspace/1" initialTab="studio" />);
    fireEvent.click(await screen.findByText('commit'));
    await waitFor(() => expect(api.post).toHaveBeenLastCalledWith(
      '/workspace/1', {
        action: 'transaction_action', session_id: 'session-one',
        transaction_action: 'commit',
      }
    ));
    expect(await screen.findByText(/driver_observation_only/))
      .toBeInTheDocument();
  });

  it('pages, exports, and compares endpoint-bound retained results', async () => {
    const rendered = {
      descriptor: {result_id: 'result-two', export_formats: ['json']},
      component_reference: 'SchemaView/DataGridView',
      page: {next_cursor: 'cursor-two', page_size: 1},
      view_model: {columns: [{name: 'answer'}], rows: [{answer: 42}]},
    };
    api.post.mockImplementation((_url, payload) => {
      const values = {
        open_session: {session_id: 'session-one'},
        execute: {occurrence_id: 'occurrence-one'},
        poll: {occurrence: {operation: {terminal: true}}, rendered_result: rendered},
        result_page: {...rendered, page: {next_cursor: null, page_size: 1},
          view_model: {columns: [{name: 'answer'}], rows: [{answer: 43}]}},
        result_export: {content_base64: 'W3siYW5zd2VyIjo0Mn1d',
          media_type: 'application/json', filename: 'result.json'},
      };
      return Promise.resolve({data: {data: values[payload.action]}});
    });
    render(<ProviderWorkspaceContent closeModal={jest.fn()}
      endpointUrl="/workspace/1" initialTab="studio" />);
    fireEvent.click(await screen.findByText('Run'));
    fireEvent.click(await screen.findByText('Next result page'));
    expect(await screen.findByText('43')).toBeInTheDocument();
    fireEvent.click(screen.getByText('Export JSON'));
    await waitFor(() => expect(api.post).toHaveBeenCalledWith('/workspace/1', {
      action: 'result_export', request: {
        result_id: 'result-two', format: 'json',
      },
    }));
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

  it('creates a database from the engine-specific connection form', async () => {
    const databaseTargets = {
      multiple: true, server_verification: true,
      active_target_id: null, legacy_route_database: null, targets: [],
    };
    api.get.mockResolvedValue({data: {data: {
      ...bootstrap,
      endpoint: {
        ...bootstrap.endpoint, route_management_available: true,
      },
      database_targets: databaseTargets,
      visual_admin: {
        ...bootstrap.visual_admin,
        objects: [{
          ...bootstrap.visual_admin.objects[0],
          operations: [{
            ...bootstrap.visual_admin.objects[0].operations[0],
            blockers: [], execution_available: true,
          }],
        }],
      },
    }}});
    api.post.mockImplementation((_url, payload) => {
      const responses = {
        route_list: {
          supports_multiple_routes: false, default_port: 3050,
          database_targeting: {multiple: true}, connection_fields: [],
          routes: [{
            route_id: 'route-one', priority: 1,
            configuration: {host: 'localhost', port: 3050, user: 'SYSDBA'},
            health: {},
          }],
        },
        visual_admin_validate: {valid: true, errors: []},
        visual_admin_plan: {
          state: 'ready', execution_available: true,
          plan_id: 'database-plan', plan_digest: 'database-digest',
        },
        visual_admin_apply: {
          provider_result: {driver_returned: true},
          database_targets: {
            ...databaseTargets, active_target_id: 'database-one',
            targets: [{
              target_id: 'database-one', active: true,
              display_name: 'sample.fdb', database: '/data/sample.fdb',
            }],
          },
        },
        resource_refresh: bootstrap.resource_page,
      };
      return Promise.resolve({data: {data: responses[payload.action]}});
    });
    render(<ProviderWorkspaceContent closeModal={jest.fn()}
      endpointUrl="/workspace/1" initialTab="connections" />);
    fireEvent.change(await screen.findByRole('textbox', {name: /Name/}), {
      target: {value: 'sample.fdb'},
    });
    fireEvent.click(screen.getByText('Validate and preview'));
    await screen.findByText(/database-plan/);
    fireEvent.click(screen.getByText('Create database'));
    expect(await screen.findByText(/completed the database operation/))
      .toBeInTheDocument();
    expect(api.post).toHaveBeenCalledWith('/workspace/1', {
      action: 'visual_admin_apply', request: {
        plan_id: 'database-plan', plan_digest: 'database-digest',
        confirmed: false,
      },
    });
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
    fireEvent.click(await screen.findByText(
      'Operation progress and history'
    ));
    expect(await screen.findByText(/restart-safe audit record/)).toBeInTheDocument();
    expect(screen.getByText('Observe provider state')).toBeDisabled();
    expect(screen.getByText('Request cancellation')).toBeDisabled();
    expect(screen.getByText('Validate post-state')).toBeDisabled();
  });

  it('renders provider-declared operational facets and commands', async () => {
    render(<ProviderWorkspaceContent closeModal={jest.fn()}
      endpointUrl="/workspace/1" initialTab="operations" />);
    expect(await screen.findAllByText('Server and cluster health'))
      .toHaveLength(2);
    expect(screen.getByText('Provider observations')).toBeInTheDocument();
    expect(screen.getByRole('button', {name: 'Validate and preview'}))
      .toBeInTheDocument();
    expect(screen.getByText(/does not infer success/)).toBeInTheDocument();
  });

  it('visualizes provider-authoritative distributed topology paths', async () => {
    const topologyBootstrap = {
      ...bootstrap,
      resource_page: {generation: 'topology-one', items: [{
        resource_id: 'node:one', resource_kind: 'node',
        display_name: 'node-one',
        authority_path: ['cluster-a', 'zone-one', 'node-one'],
        extensions: {provider: {native: {
          status: 'provider-online', role: 'provider-leader',
        }}},
      }]},
      operational_workspace: {
        schema: 'cdeadmin.operational-workspace.v1',
        engine_id: 'distributed-test', distributed: true,
        categories: ['distributed'],
        topology: {available: true, authority:
          'provider-resource-authority-path', resource_kinds: ['node']},
        facets: [{
          facet_id: 'topology', title: 'Topology visualization',
          category: 'distributed', summary: 'Provider topology.',
          catalog_state: 'observable', unavailable_reason: null,
          resource_kinds: ['node'], discovered_resource_count: 1,
          operations: [],
        }],
      },
    };
    api.get.mockResolvedValue({data: {data: topologyBootstrap}});
    render(<ProviderWorkspaceContent closeModal={jest.fn()}
      endpointUrl="/workspace/1" initialTab="operations" />);
    expect(await screen.findByLabelText('Provider topology visualization'))
      .toHaveTextContent('cluster-a → zone-one → node-one');
    expect(screen.getByText(/provider-leader/)).toBeInTheDocument();
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

  it('previews and explicitly confirms provider bulk imports', async () => {
    api.get.mockResolvedValue({data: {data: {
      ...bootstrap,
      visual_admin: {...bootstrap.visual_admin, objects: [{
        ...bootstrap.visual_admin.objects[0], operations: [{
          ...bootstrap.visual_admin.objects[0].operations[0], blockers: [],
          execution_available: true,
        }],
      }]},
    }}});
    api.post.mockImplementation((_url, payload) => Promise.resolve({data: {
      data: payload.action === 'visual_admin_bulk_plan' ? {
        ready: true, atomicity: 'not-claimed', plans: [{plan: {
          plan_id: 'plan-one', plan_digest: 'digest-one',
          command_preview: 'CREATE DATABASE imported',
        }}],
      } : {complete: true, applied_count: 1, automatic_retry: false},
    }}));
    render(<ProviderWorkspaceContent closeModal={jest.fn()}
      endpointUrl="/workspace/1" initialTab="movement" />);
    const source = await screen.findByRole('textbox', {
      name: 'Import records / provider form drafts',
    });
    fireEvent.change(source, {target: {value: '[{"name":"imported"}]'}});
    fireEvent.click(screen.getByText('Validate and preview batch'));
    expect(await screen.findByLabelText('Bulk operation preview'))
      .toHaveTextContent('CREATE DATABASE imported');
    fireEvent.click(screen.getByLabelText(
      'I confirm every provider-planned mutation in this non-atomic batch.'
    ));
    fireEvent.click(screen.getByText('Apply confirmed batch'));
    expect(await screen.findByLabelText('Bulk operation result'))
      .toHaveTextContent('applied_count');
    expect(api.post).toHaveBeenLastCalledWith('/workspace/1', {
      action: 'visual_admin_bulk_apply', request: {
        confirmed: true,
        plans: [{plan_id: 'plan-one', plan_digest: 'digest-one'}],
      },
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
    expect(screen.getByText('Relationships')).toBeInTheDocument();
    expect(screen.getByText('Measures')).toBeInTheDocument();
    expect(screen.getByText('Cube query')).toBeInTheDocument();
    expect(screen.getByText('Parameters & security')).toBeInTheDocument();
    expect(screen.getByText('Charts & dashboards')).toBeInTheDocument();
    expect(screen.getByText('Reports & schedules')).toBeInTheDocument();
    expect(screen.getByText('Materializations')).toBeInTheDocument();
    expect(screen.getByText('Lineage')).toBeInTheDocument();
    expect(screen.getByText('Diagnostics')).toBeInTheDocument();
    expect(screen.getAllByText('Revisions')).toHaveLength(2);
    expect(screen.getByText(/Relational and multidimensional/))
      .toBeInTheDocument();
    fireEvent.click(screen.getByText('Cube query'));
    expect(screen.getByLabelText('Time operation')).toBeInTheDocument();
    expect(screen.getByText('Native analytical windows')).toBeInTheDocument();
    expect(screen.getByLabelText('Window operation')).toHaveTextContent(
      'running sum'
    );
    expect(screen.getByLabelText('Drill-through fields (source.field, ...)'))
      .toBeInTheDocument();
  });

  it('uses provider-family vocabulary in the semantic model designer', async () => {
    api.get.mockResolvedValue({data: {data: {
      ...bootstrap,
      resource_page: {items: [{
        resource_id: 'node:person', resource_kind: 'node',
        display_name: 'Person', display_path: ['graph', 'Person'],
      }]},
      semantic_models: {...bootstrap.semantic_models, capabilities: {
        ...bootstrap.semantic_models.capabilities,
        analytical_profile: {
          title: 'Graph analytics', semantic_family: 'graph',
          source_kinds: ['graph', 'node', 'relationship'],
          source_classifications: ['node-set', 'relationship-set', 'path-set'],
          dimension_kinds: ['label', 'property', 'path', 'community'],
          relationship_kinds: ['native-edge', 'path-pattern'],
          measure_kinds: ['property-aggregate', 'path-count', 'score'],
          grain_vocabulary: 'node-relationship-path',
        },
      }},
    }}});
    render(<ProviderWorkspaceContent closeModal={jest.fn()}
      endpointUrl="/workspace/1" initialTab="semantic" />);
    expect(await screen.findByText(/Graph analytics/)).toBeInTheDocument();
    expect(screen.getByRole('combobox', {name: 'Source kind'}))
      .toHaveTextContent('node');
    expect(screen.getByRole('combobox', {name: 'Classification'}))
      .toHaveTextContent('node-set');
    fireEvent.click(screen.getByText('Relationships'));
    expect(await screen.findByLabelText('Semantic relationship diagram'))
      .toHaveTextContent('node-set · node');
    expect(screen.getByRole('combobox', {name: 'Relationship kind'}))
      .toHaveTextContent('native-edge');
  });

  it('provides security, chart, dashboard, report and diagnostics workspaces', async () => {
    api.post.mockResolvedValue({data: {data: {
      schema: 'cdeadmin.semantic-query-diagnostics.v1',
      reproducibility: {model_digest: 'model-digest'},
    }}});
    render(<ProviderWorkspaceContent closeModal={jest.fn()}
      endpointUrl="/workspace/1" initialTab="semantic" />);
    fireEvent.click(await screen.findByText('Parameters & security'));
    expect(screen.getByText('Row-level security')).toBeInTheDocument();
    expect(screen.getByLabelText('Policy field')).toBeInTheDocument();
    expect(screen.getByLabelText('Trusted principal claim')).toHaveValue('user_id');
    expect(screen.getByText('Tenant filtering')).toBeInTheDocument();
    fireEvent.click(screen.getByText('Charts & dashboards'));
    expect(screen.getByText('Chart builder')).toBeInTheDocument();
    expect(screen.getByText('Dashboard builder')).toBeInTheDocument();
    fireEvent.click(screen.getByText('Reports & schedules'));
    expect(screen.getByText('Report builder')).toBeInTheDocument();
    expect(screen.getByLabelText('Delivery profile')).toBeInTheDocument();
    expect(screen.getByLabelText('Scheduled export format'))
      .toBeInTheDocument();
    expect(screen.getByLabelText('Recipients or object filename'))
      .toBeInTheDocument();
    expect(screen.getByText(/operator-configured worker authority/))
      .toBeInTheDocument();
    fireEvent.click(screen.getByText('Diagnostics'));
    fireEvent.click(screen.getByText('Refresh query diagnostics'));
    expect(await screen.findByText(/model-digest/)).toBeInTheDocument();
    expect(api.post).toHaveBeenLastCalledWith('/workspace/1', {
      action: 'semantic_query_diagnostics', request: expect.any(Object),
    });
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
