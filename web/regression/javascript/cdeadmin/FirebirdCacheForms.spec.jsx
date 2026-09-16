/////////////////////////////////////////////////////////////
// CDEadmin - Multi-engine Database Administration
// Copyright (C) 2013 - 2026, The pgAdmin Development Team
// This software is released under the PostgreSQL Licence
//////////////////////////////////////////////////////////////

import {fireEvent, render, screen, waitFor} from '@testing-library/react';
import {DatabaseTargetWorkspace, ServerProfileWorkspace}
  from '../../../pgadmin/static/js/Dialogs/ProviderWorkspaceContent';
import manifest from '../../../pgadmin/cdeadmin/providers/firebird/provider_manifest.json';

const policyLabel = 'Attachment page-cache policy';
const pageField = manifest.registration.connection_fields.find(
  field => field.field_id === 'attachment_cache_pages');

function mountForm(scope, policy, pages) {
  const fields = manifest.registration.connection_fields.filter(
    field => field.field_id.startsWith('attachment_cache_')).map(field =>
    scope === 'database' && field.field_id === 'attachment_cache_policy' ? {
      ...field, default: 'SERVER_DEFAULT', options: [
        {value: 'SERVER_DEFAULT', label: 'Use server preference'}, ...field.options],
    } : field);
  const configuration = {attachment_cache_policy: policy, attachment_cache_pages: pages};
  const post = jest.fn().mockResolvedValue({});
  const setError = jest.fn();
  const form = {form_id: `owned-firebird-${scope}`, title: 'Save owned database', fields};
  if (scope === 'server') {
    render(<ServerProfileWorkspace registration={{
      primary_route: {route_id: 'owned-route', configuration},
      forms: {forms: {edit: form}},
    }} post={post} setError={setError} />);
  } else {
    render(<DatabaseTargetWorkspace initialCatalog={{
      forms: {form_set_id: 'owned-firebird-database', forms: {edit: form}},
      target_management: true, active_target_id: 'owned-target',
      targets: [{target_id: 'owned-target', configuration}],
    }} visualCatalog={{objects: []}} resources={[]} post={post}
    setError={setError} initialMode="edit" focused />);
  }
  return {post, button: screen.getByRole('button', {name: scope === 'server' ?
    'Save endpoint profile' : 'Save owned database'})};
}

describe.each(['server', 'database'])('Firebird %s cache form', scope => {
  let originalHeight;
  beforeEach(() => {
    originalHeight = window.innerHeight;
    window.innerHeight = 1200;
  });
  afterEach(() => { window.innerHeight = originalHeight; });
  it.each([25, 49, 256])('submits a saved custom count %s as a number', async pages => {
    const {post, button} = mountForm(scope, 'CUSTOM', pages);
    expect(screen.getByRole('spinbutton', {name: pageField.label})).toHaveValue(pages);
    fireEvent.click(button);
    await waitFor(() => expect(post).toHaveBeenCalledWith({
      action: scope === 'server' ? 'endpoint_profile_update' : 'database_target_update',
      request: {...(scope === 'database' ? {target_id: 'owned-target'} : {}),
        attachment_cache_policy: 'CUSTOM', attachment_cache_pages: pages},
    }));
  });

  it('requires a visible count, but omits the hidden count after selecting native default', async () => {
    const {post, button} = mountForm(scope, 'CUSTOM', 256);
    fireEvent.change(screen.getByRole('spinbutton', {name: pageField.label}),
      {target: {value: ''}});
    expect(button).toBeDisabled();
    fireEvent.click(button);
    expect(post).not.toHaveBeenCalled();
    fireEvent.mouseDown(screen.getByRole('combobox', {name: policyLabel}));
    fireEvent.click(await screen.findByRole('option', {name: 'Native default', exact: true}));
    expect(screen.queryByRole('spinbutton', {name: pageField.label})).not.toBeInTheDocument();
    expect(button).not.toBeDisabled();
    fireEvent.click(button);
    await waitFor(() => expect(post).toHaveBeenCalledWith({
      action: scope === 'server' ? 'endpoint_profile_update' : 'database_target_update',
      request: {...(scope === 'database' ? {target_id: 'owned-target'} : {}),
        attachment_cache_policy: 'NATIVE_DEFAULT'},
    }));
  });

  it('does not submit a stale saved count under native default', async () => {
    const {post, button} = mountForm(scope, 'NATIVE_DEFAULT', 512);
    expect(screen.queryByRole('spinbutton', {name: pageField.label})).not.toBeInTheDocument();
    fireEvent.click(button);
    await waitFor(() => expect(post).toHaveBeenCalled());
    expect(post.mock.calls[0][0].request).not.toHaveProperty('attachment_cache_pages');
  });

  if (scope === 'database') {
    it('saves inheritance without a stale local count', async () => {
      const {post, button} = mountForm(scope, 'CUSTOM', 512);
      fireEvent.mouseDown(screen.getByRole('combobox', {name: policyLabel}));
      fireEvent.click(await screen.findByRole('option', {name: 'Use server preference', exact: true}));
      expect(screen.queryByRole('spinbutton', {name: pageField.label})).not.toBeInTheDocument();
      fireEvent.click(button);
      await waitFor(() => expect(post).toHaveBeenCalledWith({
        action: 'database_target_update', request: {
          target_id: 'owned-target', attachment_cache_policy: 'SERVER_DEFAULT'},
      }));
    });
  }
});
