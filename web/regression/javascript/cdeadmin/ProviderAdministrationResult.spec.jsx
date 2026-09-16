/////////////////////////////////////////////////////////////
// CDEadmin - Multi-engine Database Administration
// Copyright (C) 2013 - 2026, The pgAdmin Development Team
// This software is released under the PostgreSQL Licence
//////////////////////////////////////////////////////////////

import {render, screen} from '@testing-library/react';
import ProviderAdministrationResult from '../../../pgadmin/static/js/Dialogs/ProviderAdministrationResult';
import FirebirdServiceObservation from '../../../pgadmin/static/js/Dialogs/FirebirdServiceObservation';
import FirebirdLimboObservation from '../../../pgadmin/static/js/Dialogs/FirebirdLimboObservation';

jest.mock('../../../pgadmin/static/js/Dialogs/FirebirdServiceObservation', () =>
  jest.fn(() => <div>Service observation</div>));
jest.mock('../../../pgadmin/static/js/Dialogs/FirebirdLimboObservation', () =>
  jest.fn(() => <div>Limbo observation</div>));

describe('provider administration response presentation', () => {
  it('preserves raw observations and labels the original target without interpreting finality', () => {
    const result = {provider_result: {accepted: false, commit_requested: false,
      driver_observation_only: true, error: '<script>not markup</script>'}};
    const {container, rerender} = render(<ProviderAdministrationResult result={result}
      title="Drop" target={{resource_id: 'table:<original>', display_name: '<original>'}} />);
    expect(screen.getByLabelText('Provider response target')).toHaveTextContent('Drop — <original> [table:<original>]');
    expect(JSON.parse(screen.getByLabelText('Provider operation result').textContent)).toEqual(result.provider_result);
    expect(screen.getByText(/Finality remains provider-owned/)).toBeInTheDocument();
    expect(container.querySelector('script')).toBeNull();
    rerender(<ProviderAdministrationResult result={{state: 'unknown'}} />);
    expect(JSON.parse(screen.getByLabelText('Provider operation result').textContent)).toEqual({state: 'unknown'});
    expect(screen.queryByLabelText('Provider response target')).not.toBeInTheDocument();
    rerender(<ProviderAdministrationResult result={null} />);
    expect(container).toBeEmptyDOMElement();
  });

  it('retains service cleanup and failed registration warnings', () => {
    render(<ProviderAdministrationResult result={{provider_result: {driver_observation: {
      service_release: {service_handle_released: false},
    }}, workspace_follow_up: [
      {action: 'attach', state: 'failed', message: 'Registration needs attention'},
      {action: 'other', state: 'complete', message: 'Not a failure'},
      {action: 'unknown', state: 'failed', message: null},
    ]}} />);
    expect(screen.getByLabelText('Firebird service cleanup required')).toHaveTextContent('Do not replay the operation');
    expect(screen.getAllByLabelText('Connection registration follow-up required')).toHaveLength(1);
    expect(screen.getByLabelText('Connection registration follow-up required')).toHaveTextContent('Registration needs attention');
    expect(screen.queryByText('Not a failure')).not.toBeInTheDocument();
  });

  it.each(['service', 'limbo'])('preserves the specialized %s renderer', (kind) => {
    const observation = {schema: `cdeadmin.firebird-${kind}-result.v1`, marker: 'unchanged'};
    render(<ProviderAdministrationResult title="Native task"
      result={{provider_result: {driver_observation: observation}}} />);
    const renderer = kind === 'service' ? FirebirdServiceObservation : FirebirdLimboObservation;
    expect(renderer.mock.calls.at(-1)[0].observation).toBe(observation);
    if (kind === 'service') expect(renderer.mock.calls.at(-1)[0].title).toBe('Native task');
    expect(screen.queryByLabelText('Provider operation result')).not.toBeInTheDocument();
    expect(screen.getByText(/Finality remains provider-owned/)).toBeInTheDocument();
  });
});
