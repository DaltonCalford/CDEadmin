/////////////////////////////////////////////////////////////
//
// CDEadmin - Multi-engine Database Administration
//
// Copyright (C) 2013 - 2026, The pgAdmin Development Team
// This software is released under the PostgreSQL Licence
//
//////////////////////////////////////////////////////////////

import {act, fireEvent, render, screen, waitFor} from '@testing-library/react';
import ProviderWorkspaceContent, {
  DatabaseTargetWorkspace,
  ResultControls,
  ServerProfileWorkspace,
  semanticCrossFilter,
} from '../../../pgadmin/static/js/Dialogs/ProviderWorkspaceContent';
import getApiInstance from '../../../pgadmin/static/js/api_instance';

jest.mock('../../../pgadmin/static/js/api_instance');

const bootstrap = {
  endpoint: {
    provider_id: 'org.cdeadmin.mysql',
    verified_runtime_family: 'mysql',
  },
  grid_workspace: {
    schema: 'cdeadmin.provider-grid-workspace.v1',
    adoption_state: 'passed',
    activation_gates: [{
      gate_id: 'public_grid_boundary', state: 'passed',
    }],
    runtime_gate: {
      gate_id: 'live_provider_verification', state: 'passed',
    },
  },
  languages: [{
    language_profile: 'mysql-sql', title: 'MySQL SQL',
    starter_source: 'SELECT 42',
    source_presets: [{label: 'MySQL scalar query', source: 'SELECT 42'}],
  }],
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

  it('renders and submits an exact provider-owned endpoint form', async () => {
    const post = jest.fn().mockResolvedValue({display_name: 'SQLite local'});
    render(<ServerProfileWorkspace registration={{
      display_name: 'localhost',
      primary_route: {route_id: 'route-one', configuration: {timeout: 5}},
      forms: {forms: {edit: {
        form_id: 'cdeadmin.sqlite-native.server.edit.v1',
        operation_id: 'edit', title: 'Edit SQLite 3.53 server', fields: [
          {field_id: 'name', label: 'Connection profile name',
            control: 'text', required: true},
          {field_id: 'timeout', route_key: 'timeout',
            label: 'Busy timeout (seconds)', control: 'number',
            required: false, default: 5},
        ],
      }}}}} post={post} setError={jest.fn()} />);
    expect(screen.getByText('Edit SQLite 3.53 server')).toBeInTheDocument();
    fireEvent.change(screen.getByRole('textbox', {
      name: 'Connection profile name',
    }), {target: {value: 'SQLite local'}});
    fireEvent.click(screen.getByRole('button', {
      name: 'Save endpoint profile',
    }));
    await waitFor(() => expect(post).toHaveBeenCalledWith({
      action: 'endpoint_profile_update', request: {
        name: 'SQLite local', timeout: 5,
      },
    }));
  });

  it('requires the exact profile name before removing an endpoint', async () => {
    const post = jest.fn().mockResolvedValue({removed: true});
    const onRemoved = jest.fn();
    render(<ServerProfileWorkspace registration={{
      display_name: 'SQLite local',
      primary_route: {route_id: 'route-one', configuration: {}},
      forms: {forms: {remove: {
        form_id: 'cdeadmin.sqlite-native.server.remove.v1',
        operation_id: 'remove', title: 'Remove SQLite 3.53 server', fields: [
          {field_id: 'confirmation',
            label: 'Type the connection profile name to confirm',
            control: 'text', required: true},
        ],
      }}}}} post={post} setError={jest.fn()} initialMode="remove"
    onRemoved={onRemoved} />);
    const remove = screen.getByRole('button', {
      name: 'Remove endpoint registration',
    });
    expect(remove).toBeDisabled();
    fireEvent.change(screen.getByRole('textbox', {
      name: 'Type the connection profile name to confirm',
    }), {target: {value: 'SQLite local'}});
    expect(remove).toBeEnabled();
    fireEvent.click(remove);
    await waitFor(() => expect(post).toHaveBeenCalledWith({
      action: 'endpoint_profile_remove', request: {
        confirmation: 'SQLite local',
      },
    }));
    expect(onRemoved).toHaveBeenCalledWith({removed: true});
  });

  it('renders database and server observations on one properties task', async () => {
    api.get.mockResolvedValue({data: {data: {
      ...bootstrap,
      endpoint: {
        ...bootstrap.endpoint,
        provider_id: 'org.cdeadmin.firebird',
        verified_runtime_family: 'firebird',
        verified_runtime_version: '5.0.4',
      },
      database_targets: {
        targets: [{target_id: 'database-one', display_name: 'example.fdb',
          database: '/firebird/data/example.fdb', active: true,
          configuration: {charset: 'UTF8', transaction_isolation: 'SNAPSHOT'}}],
      },
      resource_page: {items: [{
        resource_id: 'server:Firebird', resource_kind: 'server',
        display_name: 'Firebird',
        extensions: {firebird: {native: {architecture: 'Firebird/linux'}}},
      }, {
        resource_id: 'database:example.fdb', resource_kind: 'database',
        display_name: 'example.fdb',
        extensions: {firebird: {native: {page_size: '8192',
          ods_major: '13', ods_minor: '1', sql_dialect: '3',
          default_character_set: 'UTF8', forced_writes: '1'}}},
      }]},
    }}});
    render(<ProviderWorkspaceContent closeModal={jest.fn()}
      endpointUrl="/workspace/1" initialTab="properties"
      initialContext={{resource_id: 'database-one'}} />);
    expect(await screen.findByText('example.fdb — Firebird database properties'))
      .toBeInTheDocument();
    expect(screen.getByText('Firebird server observations')).toBeInTheDocument();
    expect(screen.getByText('Firebird/linux')).toBeInTheDocument();
    expect(screen.getByText('Firebird format and dialect')).toBeInTheDocument();
    expect(screen.getByText('Firebird storage and durability')).toBeInTheDocument();
    expect(screen.getByText('Firebird connection defaults')).toBeInTheDocument();
    expect(screen.getByText('Database filename or alias')).toBeInTheDocument();
    expect(screen.getByText('/firebird/data/example.fdb')).toBeInTheDocument();
    expect(screen.getByText('8192')).toBeInTheDocument();
    expect(screen.queryByRole('tab')).not.toBeInTheDocument();
  });

  it('shows exact dialect and metrics blockers without engine fallbacks', async () => {
    api.get.mockResolvedValue({data: {data: {
      ...bootstrap,
      engine_contracts: {
        state: 'blocked',
        dialect: {state: 'blocked'}, metrics: {state: 'blocked'},
      },
    }}});
    render(<ProviderWorkspaceContent closeModal={jest.fn()}
      endpointUrl="/workspace/1" initialTab="studio" />);
    expect(await screen.findByLabelText('Exact engine contract status'))
      .toHaveTextContent('dialect, metrics');
    expect(screen.getByLabelText('Exact engine contract status'))
      .toHaveTextContent('will not substitute another engine family');
  });

  it('renders and submits the exact provider-owned database form', async () => {
    const databaseForm = {
      form_set_id: 'cdeadmin.firebird-native.database.forms.v1',
      lifecycle_resource_kind: 'database',
      forms: {
        define: {
          form_id: 'cdeadmin.firebird-native.database.define.v1',
          operation_id: 'define', title: 'Define Firebird database',
          supported: true, fields: [
            {field_id: 'database', label: 'Firebird database filename or alias',
              control: 'text', required: true},
            {field_id: 'display_name', label: 'Navigator display name',
              control: 'text', required: false},
            {field_id: 'charset', label: 'Connection character set',
              control: 'text', required: false, default: 'UTF8'},
          ],
        },
        create: {form_id: 'firebird-create', operation_id: 'create',
          title: 'Create Firebird database', supported: false,
          disabled_reason: 'Unavailable in this test.', fields: []},
      },
    };
    const post = jest.fn().mockResolvedValue({
      forms: databaseForm, target_management: true,
      targets: [], active_target_id: '',
    });
    render(<DatabaseTargetWorkspace initialCatalog={{
      forms: databaseForm, target_management: true,
      targets: [], active_target_id: '',
    }} visualCatalog={{objects: []}} resources={[]} post={post}
    setError={jest.fn()} initialMode="attach" />);

    fireEvent.change(screen.getByRole('textbox', {
      name: /Firebird database filename or alias/,
    }), {target: {value: '/firebird/data/example.fdb'}});
    fireEvent.change(screen.getByRole('textbox', {
      name: /Navigator display name/,
    }), {
      target: {value: 'Example Firebird'},
    });
    fireEvent.click(screen.getByRole('button', {
      name: 'Define Firebird database',
    }));

    await waitFor(() => expect(post).toHaveBeenCalledWith({
      action: 'database_target_attach', request: {
        database: '/firebird/data/example.fdb',
        display_name: 'Example Firebird', charset: 'UTF8',
      },
    }));
    expect(screen.queryByLabelText(
      'Database filename, path, or native name'
    )).not.toBeInTheDocument();
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

  it('requests the next provider-owned result page from its occurrence', async () => {
    const post = jest.fn();
    const onProviderContinuation = jest.fn().mockResolvedValue();
    render(<ResultControls rendered={{
      descriptor: {
        result_id: 'stream-page-one', export_formats: [],
        provider_continuation: 'operation-one',
      },
      page: {next_cursor: null, page_size: 500},
    }} history={[]} post={post} onRendered={jest.fn()}
    setError={jest.fn()} setBusy={jest.fn()}
    onProviderContinuation={onProviderContinuation} />);

    fireEvent.click(screen.getByText('Next result page'));

    await waitFor(() => expect(onProviderContinuation).toHaveBeenCalled());
    expect(post).not.toHaveBeenCalled();
  });

  it('loads provider resources through the workspace endpoint', async () => {
    render(<ProviderWorkspaceContent
      closeModal={jest.fn()}
      endpointUrl="/workspace/1"
    />);
    fireEvent.click(await screen.findByRole('treeitem', {name: /database/i}));
    expect(await screen.findByText('example')).toBeInTheDocument();
    expect(screen.getAllByText('database')).toHaveLength(2);
    expect(screen.queryByRole('tab')).not.toBeInTheDocument();
    expect(api.get).toHaveBeenCalledWith('/workspace/1');
  });

  it('requests credentials and reloads after a workspace 401', async () => {
    const onCredentialRequired = jest.fn();
    api.get
      .mockRejectedValueOnce({response: {status: 401}})
      .mockResolvedValueOnce({data: {data: bootstrap}});
    render(<ProviderWorkspaceContent
      closeModal={jest.fn()}
      endpointUrl="/workspace/1"
      onCredentialRequired={onCredentialRequired}
    />);
    await waitFor(() => expect(onCredentialRequired).toHaveBeenCalledTimes(1));
    expect(onCredentialRequired).toHaveBeenCalledWith(expect.any(Function));
    await act(async () => {
      onCredentialRequired.mock.calls[0][0]();
    });
    fireEvent.click(await screen.findByRole('treeitem', {name: /database/i}));
    expect(await screen.findByText('example')).toBeInTheDocument();
    expect(api.get).toHaveBeenCalledTimes(2);
  });

  it('does not invent a generic SQL starter for a provider dialect', async () => {
    api.get.mockResolvedValue({data: {data: {
      ...bootstrap,
      languages: [{language_profile: 'unproven-sql', title: 'Unproven SQL'}],
    }}});
    render(<ProviderWorkspaceContent closeModal={jest.fn()}
      endpointUrl="/workspace/1" initialTab="studio" />);
    expect(await screen.findByText(
      /has not supplied an evidence-bound query template/
    )).toBeInTheDocument();
    expect(screen.getByLabelText('Query source')).toHaveValue('');
    expect(screen.queryByDisplayValue('SELECT 1')).not.toBeInTheDocument();
    expect(screen.queryByRole('tab')).not.toBeInTheDocument();
  });

  it('shows workspace tabs only for explicit drag-drop composition', async () => {
    render(<ProviderWorkspaceContent closeModal={jest.fn()}
      endpointUrl="/workspace/1"
      initialContext={{composition_mode: 'tabbed'}} />);
    expect(await screen.findByRole('tab', {name: 'Resource Explorer'}))
      .toBeInTheDocument();
  });

  it('uses a focused provider database dialog without workspace tabs', async () => {
    const createFields = [
      {field_id: 'database_path', label: 'Absolute database filename on the Firebird server', control: 'text', required: true},
      {field_id: 'page_size', label: 'Page size', control: 'select', required: false, default: '8192', options: [
        {value: '4096', label: '4096'}, {value: '8192', label: '8192'},
      ]},
      {field_id: 'default_charset', label: 'Default character set', control: 'text', required: false, default: 'UTF8'},
      {field_id: 'sql_dialect', label: 'Database SQL dialect', control: 'select', required: false, default: '3', options: [
        {value: '1', label: '1'}, {value: '3', label: '3'},
      ]},
    ];
    api.get.mockResolvedValue({data: {data: {
      ...bootstrap,
      database_targets: {
        form_set_id: 'unused', target_management: true, targets: [],
        forms: {
          form_set_id: 'cdeadmin.firebird-native.database.forms.v1',
          lifecycle_resource_kind: 'database',
          forms: {create: {
            form_id: 'cdeadmin.firebird-native.database.create.v1',
            operation_id: 'create', title: 'Create Firebird database',
            supported: true, fields: createFields,
          }},
        },
      },
      visual_admin: {
        engine_id: 'firebird', engine_name: 'Firebird', objects: [{
          resource_kind: 'database', title: 'Database', operations: [{
            operation_id: 'create', title: 'Create Firebird database',
            execution_available: true, target_required: false,
            confirmation_required: false,
            form: {form_id: 'firebird_database_create', fields: createFields},
          }],
        }],
      },
    }}});
    render(<ProviderWorkspaceContent closeModal={jest.fn()}
      endpointUrl="/workspace/1" initialTab="connections"
      initialContext={{database_mode: 'create'}} />);
    expect(await screen.findByRole('heading', {
      name: 'Create Firebird database',
    })).toBeInTheDocument();
    expect(screen.queryByRole('tab')).not.toBeInTheDocument();
    expect(screen.getByRole('combobox', {name: 'Page size'}))
      .toHaveTextContent('8192');
    expect(screen.queryByText(/Cubes & Semantic Models/))
      .not.toBeInTheDocument();
  });

  it('maps a database target UUID to its sole provider database resource', async () => {
    const operation = {
      operation_id: 'backup_logical', title: 'Logical backup (gbak)',
      mutation_class: 'admin', target_required: true,
      target_resource_kinds: ['database'], confirmation_required: false,
      execution_available: true, graphical_ready: true, blockers: [],
      form: {form_id: 'firebird_backup_logical', fields: [{
        field_id: 'backup_file',
        label: 'Backup filename on the Firebird server',
        control: 'text', required: true,
      }]},
    };
    api.get.mockResolvedValue({data: {data: {
      ...bootstrap,
      endpoint: {
        provider_id: 'org.cdeadmin.firebird',
        verified_runtime_family: 'firebird',
      },
      resource_page: {items: [{
        resource_id: 'database:cdeadmin_demo.fdb',
        resource_kind: 'database', display_name: 'cdeadmin_demo.fdb',
        authority_path: ['database', 'cdeadmin_demo.fdb'],
      }]},
      visual_admin: {
        engine_id: 'firebird', engine_name: 'Firebird', objects: [{
          resource_kind: 'database', title: 'Database',
          operations: [operation],
        }],
      },
    }}});
    api.post.mockResolvedValue({data: {data: {
      resource_id: 'database:cdeadmin_demo.fdb',
      resource_kind: 'database', display_name: 'cdeadmin_demo.fdb',
    }}});
    render(<ProviderWorkspaceContent closeModal={jest.fn()}
      endpointUrl="/workspace/1" initialTab="administration"
      initialContext={{
        resource_id: 'retained-database-target-uuid',
        resource_kind: 'database', operation_id: 'backup_logical',
      }} />);
    expect(await screen.findByRole('heading', {
      name: 'Logical backup (gbak)',
    })).toBeInTheDocument();
    expect(screen.getByRole('combobox', {name: 'Target resource'}))
      .toHaveTextContent('cdeadmin_demo.fdb');
    await waitFor(() => expect(api.post).toHaveBeenCalledWith(
      '/workspace/1', {action: 'resource_inspect', request: {
        resource_id: 'database:cdeadmin_demo.fdb', generation: undefined,
        database_target_id: 'retained-database-target-uuid',
      }}
    ));
  });

  it('keeps the context-selected provider resource when peers share its kind', async () => {
    const operation = {
      operation_id: 'set_global', title: 'Set runtime global value',
      mutation_class: 'admin', target_required: true,
      confirmation_required: false, execution_available: true,
      graphical_ready: true, blockers: [],
      form: {form_id: 'mariadb_set_global', fields: []},
    };
    const resources = [{
      resource_id: 'system-variable:READ_ONLY',
      resource_kind: 'system-variable', display_name: 'READ_ONLY',
      authority_path: ['Configuration', 'system-variable', 'READ_ONLY'],
    }, {
      resource_id: 'system-variable:MAX_CONNECTIONS',
      resource_kind: 'system-variable', display_name: 'MAX_CONNECTIONS',
      authority_path: [
        'Configuration', 'system-variable', 'MAX_CONNECTIONS',
      ],
    }];
    api.get.mockResolvedValue({data: {data: {
      ...bootstrap,
      resource_page: {items: resources},
      visual_admin: {
        engine_id: 'mariadb', engine_name: 'MariaDB', objects: [{
          resource_kind: 'system-variable', title: 'System variable',
          operations: [operation],
        }],
      },
    }}});
    api.post.mockImplementation((_url, payload) => Promise.resolve({data: {
      data: resources.find((item) =>
        item.resource_id === payload.request.resource_id),
    }}));
    render(<ProviderWorkspaceContent closeModal={jest.fn()}
      endpointUrl="/workspace/1" initialTab="administration"
      initialContext={{
        resource_id: 'system-variable:MAX_CONNECTIONS',
        resource_kind: 'system-variable', operation_id: 'set_global',
      }} />);
    expect(await screen.findByRole('heading', {
      name: 'Set runtime global value',
    })).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole('combobox', {
      name: 'Target resource',
    })).toHaveTextContent('MAX_CONNECTIONS'));
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
    expect(await screen.findByRole('combobox', {
      name: 'Object properties task',
    })).toHaveTextContent('properties');
    expect(screen.queryByRole('tab')).not.toBeInTheDocument();
    expect(screen.getByRole('tabpanel', {name: 'properties object section'}))
      .toHaveTextContent('generation-one');
    expect(api.post).toHaveBeenCalledWith('/workspace/1', {
      action: 'resource_inspect', request: {
        resource_id: 'database:example', generation: undefined,
      },
    });
  });

  it('does not inspect an attachment-free service-operation target', async () => {
    const onCredentialRequired = jest.fn((retry) => retry());
    let applyAttempts = 0;
    const operation = {
      operation_id: 'bring_online', title: 'Bring database online',
      mutation_class: 'admin', target_required: true,
      target_resource_kinds: ['database'], confirmation_required: false,
      execution_available: true, graphical_ready: true, blockers: [],
      workspace_scope: 'server_service',
      form: {form_id: 'firebird_bring_online', fields: []},
    };
    api.get.mockResolvedValue({data: {data: {
      ...bootstrap,
      resource_page: {items: [{
        resource_id: 'database-service-target:database-one',
        resource_kind: 'database', display_name: 'offline.fdb',
        extensions: {cdeadmin: {
          database_target_id: 'database-one', service_scope_only: true,
        }},
      }]},
      visual_admin: {
        engine_id: 'firebird', engine_name: 'Firebird', objects: [{
          resource_kind: 'database', title: 'Database',
          operations: [operation],
        }],
      },
    }}});
    api.post.mockImplementation((_url, payload) => {
      const responses = {
        visual_admin_validate: {valid: true, errors: []},
        visual_admin_plan: {
          state: 'ready', execution_available: true,
          plan_id: 'service-plan', plan_digest: 'service-digest',
        },
        visual_admin_apply: {
          accepted: true,
          driver_observation: {server_completed: true},
        },
      };
      if (payload.action === 'visual_admin_apply' && applyAttempts++ === 0) {
        return Promise.reject({response: {status: 401}});
      }
      return Promise.resolve({data: {data: responses[payload.action]}});
    });
    render(<ProviderWorkspaceContent closeModal={jest.fn()}
      endpointUrl="/workspace/1" initialTab="administration"
      onCredentialRequired={onCredentialRequired}
      initialContext={{
        resource_id: 'database-one', resource_kind: 'database',
        operation_id: 'bring_online',
      }} />);
    expect(await screen.findByRole('heading', {
      name: 'Bring database online',
    })).toBeInTheDocument();
    expect(screen.getByRole('combobox', {name: 'Target resource'}))
      .toHaveTextContent('offline.fdb');
    expect(api.post).not.toHaveBeenCalled();
    fireEvent.click(screen.getByText('Validate and preview'));
    await screen.findByLabelText('Provider plan preview');
    fireEvent.click(screen.getByText('Apply provider plan'));
    await screen.findByLabelText('Provider operation result');
    expect(onCredentialRequired).toHaveBeenCalledTimes(1);
    expect(api.post).toHaveBeenCalledWith('/workspace/1', {
      action: 'visual_admin_apply', request: {
        plan_id: 'service-plan', plan_digest: 'service-digest',
        confirmed: false, database_target_id: 'database-one',
      },
    });
  });

  it('opens provider-owned object actions from the resource context menu', async () => {
    const inspectOperation = {
      operation_id: 'inspect', title: 'Inspect MySQL database',
      mutation_class: 'read', target_required: true,
      target_resource_kinds: ['database'], confirmation_required: false,
      execution_available: true, graphical_ready: true, blockers: [],
      form: {form_id: 'mysql-database-inspect', fields: []},
    };
    api.get.mockResolvedValue({data: {data: {
      ...bootstrap,
      visual_admin: {
        ...bootstrap.visual_admin,
        objects: [{...bootstrap.visual_admin.objects[0],
          editor: {sections: ['definition']},
          operations: [inspectOperation]}],
      },
    }}});
    api.post.mockResolvedValue({data: {data: {
      ...bootstrap.resource_page.items[0], generation: 'generation-one',
      extensions: {mysql: {native: {character_set: 'utf8mb4'}}},
    }}});
    render(<ProviderWorkspaceContent closeModal={jest.fn()}
      endpointUrl="/workspace/1" />);
    fireEvent.click(await screen.findByRole('treeitem', {name: /database/i}));
    const resource = await screen.findByRole('treeitem', {name: /example/i});
    fireEvent.contextMenu(resource, {clientX: 120, clientY: 80});
    fireEvent.click(await screen.findByText('Inspect object'));
    expect(await screen.findByRole('heading', {
      name: /Inspect MySQL database/,
    })).toBeInTheDocument();
    expect(await screen.findByRole('table', {
      name: 'Native provider properties',
    }))
      .toHaveTextContent('character set');
  });

  it('surfaces unique native tasks but omits unsupported tasks', async () => {
    api.get.mockResolvedValue({data: {data: {
      ...bootstrap,
      visual_admin: {
        ...bootstrap.visual_admin,
        objects: [{...bootstrap.visual_admin.objects[0], operations: [{
          operation_id: 'flashback', title: 'Flashback database',
          mutation_class: 'admin', target_required: true,
          execution_available: true, graphical_ready: true,
          native_supported: true, blockers: [], form: {fields: []},
        }, {
          operation_id: 'vacuum', title: 'Vacuum database',
          mutation_class: 'admin', target_required: true,
          execution_available: false, graphical_ready: true,
          native_supported: false,
          blockers: ['provider_operation_unavailable'], form: {fields: []},
        }]}],
      },
    }}});
    render(<ProviderWorkspaceContent closeModal={jest.fn()}
      endpointUrl="/workspace/1" initialTab="administration" />);
    expect(await screen.findByText('Flashback database')).toBeInTheDocument();
    expect(screen.queryByText('Vacuum database')).not.toBeInTheDocument();
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
      initialContext={{resource_id: 'database-one'}}
    />);
    expect(await screen.findByText('MySQL SQL')).toBeInTheDocument();
    expect(screen.getByLabelText('Provider grid activation status'))
      .toHaveTextContent('all provider workspace grid gates passed');
    fireEvent.click(screen.getByText('Run'));
    expect(await screen.findByText('42')).toBeInTheDocument();
    await waitFor(() => expect(api.post).toHaveBeenCalledTimes(3));
    expect(api.post.mock.calls.map((call) => call[1].action)).toEqual([
      'open_session', 'execute', 'poll',
    ]);
    expect(api.post.mock.calls.map((call) =>
      call[1].database_target_id
    )).toEqual(['database-one', 'database-one', 'database-one']);
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
        database_target_id: null,
      }
    ));
    expect(await screen.findByText(/driver_observation_only/))
      .toBeInTheDocument();
    fireEvent.click(screen.getByText('Close query session'));
    await waitFor(() => expect(api.post).toHaveBeenLastCalledWith(
      '/workspace/1', {
        action: 'close_session', session_id: 'session-one',
        database_target_id: null,
      }
    ));
    expect(screen.getByText('Close query session')).toBeDisabled();
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
        starter_source: '{"operation":"command","database":"admin","command":{"ping":1}}',
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
      languages: [{
        language_profile: 'cypher', title: 'Cypher',
        starter_source: 'RETURN 1 AS value',
      }],
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
      languages: [{
        language_profile: 'cql-3', title: 'CQL 3',
        starter_source: 'SELECT cluster_name FROM system.local',
      }],
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
        starter_source: 'SELECT version() AS version',
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

  it('opens a context command as one focused provider task form', async () => {
    render(<ProviderWorkspaceContent
      closeModal={jest.fn()}
      endpointUrl="/workspace/1"
      initialTab="administration"
      initialContext={{resource_kind: 'database', operation_id: 'create'}}
    />);
    expect(await screen.findByRole('heading', {name: 'Create'}))
      .toBeInTheDocument();
    expect(screen.queryByLabelText('Object type')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('Operation')).not.toBeInTheDocument();
    expect(screen.queryByRole('tab')).not.toBeInTheDocument();
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
    const createForm = {
      form_id: 'cdeadmin.mysql-native.database.create.v1',
      operation_id: 'create', title: 'Create database', supported: true,
      fields: [{
        field_id: 'name', label: 'Name', control: 'text', required: true,
      }, {
        field_id: 'definition', label: 'Provider-native definition',
        control: 'code', required: false,
      }, {
        field_id: 'options', label: 'Execution options', control: 'json',
        required: false, default: {},
      }],
    };
    const databaseTargets = {
      multiple: true, target_management: true, server_verification: true,
      active_target_id: null, legacy_route_database: null, targets: [],
      forms: {
        form_set_id: 'cdeadmin.mysql-native.database.forms.v1',
        lifecycle_resource_kind: 'database',
        forms: {create: createForm},
      },
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
    fireEvent.click(screen.getByRole('button', {name: 'Create database'}));
    expect(await screen.findByText(/completed the native database operation/))
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
        extensions: {cdeadmin: {database_target_id: 'database-one'}},
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
        open_session: {session_id: 'grid-session'},
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
        visual_admin_apply: {provider_result: {
          accepted: true, staged_in_provider_session: true,
        }},
        transaction_action: {provider_payload: {
          driver_observation_only: true,
          finality_interpreted_by_common_code: false,
        }},
        close_session: {
          session_id: 'grid-session', provider_closed: true,
        },
      };
      return Promise.resolve({data: {data: responses[payload.action]}});
    });
    const {unmount} = render(<ProviderWorkspaceContent closeModal={jest.fn()}
      endpointUrl="/workspace/1" initialTab="data" />);
    fireEvent.click(await screen.findByText('Load rows'));
    const name = await screen.findByDisplayValue('first');
    expect(screen.getByRole('textbox', {name: 'name value'})).toBe(name);
    expect(screen.getByRole('textbox', {name: 'name new value'}))
      .toHaveAttribute('placeholder', 'New value');
    fireEvent.change(name, {target: {value: 'second'}});
    fireEvent.click(screen.getByText('Save'));
    await waitFor(() => expect(api.post).toHaveBeenCalledTimes(6));
    expect(api.post.mock.calls.map((call) => call[1].action)).toEqual([
      'open_session', 'visual_admin_rows', 'visual_admin_validate',
      'visual_admin_plan', 'visual_admin_apply', 'visual_admin_rows',
    ]);
    expect(api.post.mock.calls[2][1].request.draft).toEqual({
      selector: {identity_token: 'row-one'},
      changes: {name: 'second'},
      concurrency_token: 'row-one',
    });
    expect(api.post.mock.calls[0][1]).toEqual({
      action: 'open_session', language_profile: 'mysql-sql',
      database_target_id: 'database-one',
    });
    expect(api.post.mock.calls[1][1].request.continuation).toBeNull();
    expect(api.post.mock.calls[1][1].request.database_target_id)
      .toBe('database-one');
    expect(api.post.mock.calls[2][1].request.database_target_id)
      .toBe('database-one');
    expect(api.post.mock.calls[4][1].request.session_id)
      .toBe('grid-session');
    expect(api.post.mock.calls[4][1].request.database_target_id)
      .toBe('database-one');
    expect(await screen.findByLabelText('Staged provider grid changes'))
      .toHaveTextContent('1 grid change');
    fireEvent.click(screen.getByText('Rollback changes'));
    await waitFor(() => expect(api.post).toHaveBeenCalledTimes(8));
    expect(api.post.mock.calls[6][1]).toEqual({
      action: 'transaction_action', session_id: 'grid-session',
      transaction_action: 'rollback',
      database_target_id: 'database-one',
    });
    fireEvent.click(screen.getByText('Close data session'));
    await waitFor(() => expect(api.post).toHaveBeenCalledTimes(9));
    expect(api.post.mock.calls[8][1]).toEqual({
      action: 'close_session', session_id: 'grid-session',
      database_target_id: 'database-one',
    });
    expect(screen.getByText('Close data session')).toBeDisabled();
    unmount();
  });

  it('opens the selected provider view in the owning database scope', async () => {
    const table = {
      resource_id: 'table:ASSETS', resource_kind: 'table',
      display_name: 'ASSETS', display_path: ['ASSETS'],
    };
    const view = {
      resource_id: 'view:OPEN_WORK_ORDERS', resource_kind: 'view',
      display_name: 'OPEN_WORK_ORDERS', display_path: ['OPEN_WORK_ORDERS'],
    };
    api.get.mockResolvedValue({data: {data: {
      ...bootstrap,
      resource_page: {items: [table, view]},
      visual_admin: {
        ...bootstrap.visual_admin,
        objects: [{resource_kind: 'table', operations: []}, {
          resource_kind: 'view', operations: [],
        }],
      },
    }}});
    api.post.mockImplementation((_url, payload) => Promise.resolve({data: {
      data: payload.action === 'open_session' ? {
        session_id: 'view-session',
      } : {
        columns: [{name: 'WORK_ORDER_ID', editable: false}],
        rows: [{values: {WORK_ORDER_ID: 1001}, identity_token: null}],
        editable: false, identity_policy: 'read-only-view',
      },
    }}));
    render(<ProviderWorkspaceContent closeModal={jest.fn()}
      endpointUrl="/workspace/1" initialTab="data" initialContext={{
        resource_id: view.resource_id, resource_kind: 'view',
        database_target_id: 'firebird-database-one',
      }} />);
    expect(await screen.findByRole('combobox', {name: 'Table or view'}))
      .toHaveTextContent('OPEN_WORK_ORDERS');
    fireEvent.click(screen.getByText('Load rows'));
    expect(await screen.findByDisplayValue('1001')).toBeInTheDocument();
    expect(api.post.mock.calls[0][1]).toEqual({
      action: 'open_session', language_profile: 'mysql-sql',
      database_target_id: 'firebird-database-one',
    });
    expect(api.post.mock.calls[1][1].request.target_resource).toEqual(view);
    expect(api.post.mock.calls[1][1].request.database_target_id)
      .toBe('firebird-database-one');
    expect(screen.getByText(/This view is read-only/)).toBeInTheDocument();
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

  it('opens the MongoDB collection owning the selected child resource', async () => {
    const firstCollection = {
      resource_id: 'mongodb:collection:example:first',
      resource_kind: 'collection', display_name: 'first',
      display_path: ['MongoDB', 'example', 'first'],
    };
    const selectedCollection = {
      resource_id: 'mongodb:collection:example:selected',
      resource_kind: 'collection', display_name: 'selected',
      display_path: ['MongoDB', 'example', 'selected'],
    };
    const selectedIndex = {
      resource_id: 'mongodb:index:example:selected:lookup',
      resource_kind: 'index', display_name: 'lookup',
      display_path: ['MongoDB', 'example', 'selected', 'lookup'],
    };
    api.get.mockResolvedValue({data: {data: {
      ...bootstrap,
      resource_page: {items: [firstCollection, selectedCollection,
        selectedIndex]},
      visual_admin: {
        engine_id: 'mongodb', model_family: 'document',
        objects: [{resource_kind: 'document', operations: []}],
      },
    }}});
    api.post.mockResolvedValue({data: {data: {documents: []}}});
    render(<ProviderWorkspaceContent closeModal={jest.fn()}
      endpointUrl="/workspace/1" initialTab="data" initialContext={{
        resource_id: selectedIndex.resource_id, resource_kind: 'index',
      }} />);
    const collectionSelector = await screen.findByRole('combobox', {
      name: 'Collection',
    });
    await waitFor(() => expect(collectionSelector)
      .toHaveTextContent('MongoDB.example.selected'));
    fireEvent.click(screen.getByText('Load documents'));
    await waitFor(() => expect(api.post).toHaveBeenCalledTimes(1));
    expect(api.post.mock.calls[0][1].request.target_resource)
      .toEqual(selectedCollection);
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
    expect(await screen.findByText('0.5')).toBeInTheDocument();
    expect(api.post.mock.calls[0][1]).toEqual({
      action: 'visual_admin_rows',
      request: {target_resource: table, limit: 200, continuation: null},
    });
  });

  it('opens the analytic container owning the selected child resource', async () => {
    const firstTable = {
      resource_id: 'influxdb:table:metrics:first', resource_kind: 'table',
      display_name: 'first', display_path: ['metrics', 'first'],
    };
    const selectedTable = {
      resource_id: 'influxdb:table:metrics:selected', resource_kind: 'table',
      display_name: 'selected', display_path: ['metrics', 'selected'],
    };
    const selectedField = {
      resource_id: 'influxdb:field:metrics:selected:usage',
      resource_kind: 'field', display_name: 'usage',
      display_path: ['metrics', 'selected', 'usage'],
    };
    api.get.mockResolvedValue({data: {data: {
      ...bootstrap,
      resource_page: {items: [firstTable, selectedTable, selectedField]},
      visual_admin: {
        engine_id: 'influxdb', model_family: 'time-series-analytic',
        objects: [{resource_kind: 'table', operations: []}],
      },
    }}});
    api.post.mockResolvedValue({data: {data: {records: []}}});
    render(<ProviderWorkspaceContent closeModal={jest.fn()}
      endpointUrl="/workspace/1" initialTab="data" initialContext={{
        resource_id: selectedField.resource_id, resource_kind: 'field',
      }} />);
    const containerSelector = await screen.findByRole('combobox', {
      name: 'Analytic data container',
    });
    await waitFor(() => expect(containerSelector)
      .toHaveTextContent('metrics.selected'));
    fireEvent.click(screen.getByText('Load data'));
    await waitFor(() => expect(api.post).toHaveBeenCalledTimes(1));
    expect(api.post.mock.calls[0][1].request.target_resource)
      .toEqual(selectedTable);
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
    expect(screen.getByLabelText('Semantic-model task'))
      .toHaveTextContent('Model');
    expect(screen.getByText(/Relational and multidimensional/))
      .toBeInTheDocument();
    expect(screen.queryByRole('tab')).not.toBeInTheDocument();
    fireEvent.change(screen.getByRole('combobox', {
      name: 'Semantic-model task',
    }), {target: {value: 'query'}});
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
    fireEvent.change(screen.getByRole('combobox', {
      name: 'Semantic-model task',
    }), {target: {value: 'relationships'}});
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
    fireEvent.change(await screen.findByRole('combobox', {
      name: 'Semantic-model task',
    }), {target: {value: 'security'}});
    expect(screen.getByText('Row-level security')).toBeInTheDocument();
    expect(screen.getByLabelText('Policy field')).toBeInTheDocument();
    expect(screen.getByLabelText('Trusted principal claim')).toHaveValue('user_id');
    expect(screen.getByText('Tenant filtering')).toBeInTheDocument();
    fireEvent.change(screen.getByRole('combobox', {
      name: 'Semantic-model task',
    }), {target: {value: 'presentation'}});
    expect(screen.getByText('Chart builder')).toBeInTheDocument();
    expect(screen.getByText('Dashboard builder')).toBeInTheDocument();
    fireEvent.change(screen.getByRole('combobox', {
      name: 'Semantic-model task',
    }), {target: {value: 'reports'}});
    expect(screen.getByText('Report builder')).toBeInTheDocument();
    expect(screen.getByLabelText('Delivery profile')).toBeInTheDocument();
    expect(screen.getByLabelText('Scheduled export format'))
      .toBeInTheDocument();
    expect(screen.getByLabelText('Recipients or object filename'))
      .toBeInTheDocument();
    expect(screen.getByText(/operator-configured worker authority/))
      .toBeInTheDocument();
    fireEvent.change(screen.getByRole('combobox', {
      name: 'Semantic-model task',
    }), {target: {value: 'diagnostics'}});
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
