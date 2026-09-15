/////////////////////////////////////////////////////////////
// ScratchRobin CDE Administrator — Firebird profile preferences
// Copyright (C) 2013 - 2026, The pgAdmin Development Team
// This software is released under the PostgreSQL Licence
/////////////////////////////////////////////////////////////
import {fireEvent, render, screen, waitFor} from '@testing-library/react';
import {DatabaseTargetWorkspace, ServerProfileWorkspace} from '../../../pgadmin/static/js/Dialogs/ProviderWorkspaceContent';
import firebirdManifest from '../../../pgadmin/cdeadmin/providers/firebird/provider_manifest.json';

jest.mock('../../../pgadmin/static/js/api_instance');

describe.each([
  {fieldId: 'decfloat_round', label: 'Initial DECFLOAT rounding mode',
    override: 'HALF_EVEN', initial: 'UP'},
  {fieldId: 'no_linger', label: 'Attachment linger policy',
    override: 'SUPPRESS', initial: 'NATIVE_DEFAULT'},
])('Firebird database $fieldId preferences', ({fieldId, label, override, initial}) => {
  const nativeField = firebirdManifest.registration.connection_fields.find(
    (field) => field.field_id === fieldId);
  const field = {...nativeField, default: 'SERVER_DEFAULT',
    inherit_server_value: 'SERVER_DEFAULT', options: [
      {value: 'SERVER_DEFAULT', label: 'Use server preference'},
      ...nativeField.options,
    ]};
  const titles = {define: 'Define Firebird database',
    connect: 'Connect Firebird database', edit: 'Edit Firebird database'};

  function catalog(preference=initial) {
    return {
      forms: {form_set_id: 'cdeadmin.firebird-native.database.forms.v1',
        lifecycle_resource_kind: 'database', forms: Object.fromEntries(
          Object.entries(titles).map(([mode, title]) => [mode, {
            form_id: `cdeadmin.firebird-native.database.${mode}.v1`,
            operation_id: mode, supported: true, title, fields: [
              ...(mode === 'define' ? [{field_id: 'database', control: 'text',
                label: 'Firebird database filename or alias', required: true}] : []),
              field,
            ],
          }]))},
      targets: [{target_id: 'owned-target', database: '/owned/example.fdb',
        display_name: 'Owned database', configuration: {[fieldId]: preference}}],
      target_management: true, active_target_id: 'owned-target',
    };
  }

  function show(mode, preference=initial, post=jest.fn().mockResolvedValue(catalog())) {
    render(<DatabaseTargetWorkspace initialCatalog={catalog(preference)}
      visualCatalog={{objects: []}} resources={[]} post={post}
      setError={jest.fn()} initialMode={mode} initialTargetId="owned-target" focused />);
    return post;
  }

  let originalHeight;
  beforeEach(() => {
    originalHeight = window.innerHeight;
    // The shared virtual-grid fixture gives every element an 800px height.
    // Match the existing provider-form tests; real browser gates test layout.
    window.innerHeight = 1200;
  });
  afterEach(() => { window.innerHeight = originalHeight; });

  it.each(['NATIVE_DEFAULT', override])('saves server preference %s and notifies its owner',
    async (choice) => {
      const saved = {display_name: 'Owned localhost'};
      const post = jest.fn().mockResolvedValue(saved);
      const onSaved = jest.fn();
      render(<ServerProfileWorkspace registration={{
        display_name: 'Owned localhost', primary_route: {
          route_id: 'owned-route', configuration: {[fieldId]: initial}},
        forms: {forms: {edit: {form_id: 'cdeadmin.firebird-native.server.edit.v1',
          title: 'Edit Firebird server', fields: [nativeField]}}},
      }} post={post} setError={jest.fn()} onSaved={onSaved} />);
      fireEvent.mouseDown(screen.getByRole('combobox', {name: label}));
      fireEvent.click(await screen.findByRole('option', {
        name: nativeField.options.find(item => item.value === choice).label,
        exact: true,
      }));
      fireEvent.click(screen.getByRole('button', {name: 'Save endpoint profile'}));
      await waitFor(() => expect(post).toHaveBeenCalledWith({
        action: 'endpoint_profile_update', request: {[fieldId]: choice},
      }));
      await waitFor(() => expect(onSaved).toHaveBeenCalledWith(saved));
      expect(onSaved).toHaveBeenCalledTimes(1);
    });

  it.each(Object.keys(titles).flatMap((mode) => [
    'SERVER_DEFAULT', 'NATIVE_DEFAULT', override,
  ].map((choice) => [mode, choice])))(
    '%s sends the explicit %s choice without interpreting it in the renderer',
    async (mode, choice) => {
      const post = show(mode);
      if(mode === 'define') fireEvent.change(screen.getByRole('textbox', {
        name: /Firebird database filename or alias/,
      }), {target: {value: '/owned/new.fdb'}});
      const option = field.options.find((item) => item.value === choice);
      fireEvent.mouseDown(screen.getByRole('combobox', {
        name: label,
      }));
      fireEvent.click(await screen.findByRole('option', {name: option.label, exact: true}));
      fireEvent.click(screen.getByRole('button', {name: titles[mode]}));
      await waitFor(() => expect(post).toHaveBeenCalledWith({
        action: {define: 'database_target_attach', connect: 'database_target_activate',
          edit: 'database_target_update'}[mode],
        request: {[fieldId]: choice,
          ...(mode === 'define' ? {database: '/owned/new.fdb'} : {target_id: 'owned-target'})},
      }));
    });

  it.each(['SERVER_DEFAULT', 'NATIVE_DEFAULT', override])(
    'reopens the saved %s choice', (choice) => {
      show('edit', choice);
      expect(screen.getByRole('combobox', {name: label}))
        .toHaveTextContent(field.options.find((item) => item.value === choice).label);
    });

  it.each(Object.keys(titles))('identifies the exact %s form for QA capture', (mode) => {
    show(mode);
    expect(screen.getByRole('region', {name: 'Engine database form'}))
      .toHaveAttribute('data-form-id', `cdeadmin.firebird-native.database.${mode}.v1`);
  });
});
