import {fireEvent, render, screen} from '@testing-library/react';
import FirebirdServiceObservation from '../../../pgadmin/static/js/Dialogs/FirebirdServiceObservation';

describe('Firebird native service result', () => {
  const observation = {
    schema: 'cdeadmin.firebird-service-result.v1',
    operation_id: 'database_statistics', database: '/owned/東京.fdb',
    server_completed: true, output_truncated: false,
    output: ['Database header page information:\n', '    Page size 8192\n', '<native & text>'],
    service_release: {service_handle_released: true},
  };

  it('shows native fields and exact output with the raw receipt collapsed', () => {
    render(<FirebirdServiceObservation observation={observation} title="Statistics" />);
    const panel = screen.getByLabelText('Firebird service result');
    expect(panel).toHaveTextContent('Statistics');
    expect(panel).toHaveTextContent('/owned/東京.fdb');
    expect(panel).toHaveTextContent('Returned');
    expect(panel).toHaveTextContent('Released');
    expect(panel).toHaveTextContent('not independent verification');
    expect(screen.getByLabelText('Firebird native service output').textContent)
      .toBe(observation.output.join(''));
    expect(panel.querySelector('native')).toBeNull();
    const details = panel.querySelector('details');
    expect(details.open).toBe(false);
    fireEvent.click(details.querySelector('summary'));
    expect(details.open).toBe(true);
    expect(JSON.parse(screen.getByLabelText('Firebird native service receipt').textContent))
      .toEqual(observation);
    fireEvent.click(details.querySelector('summary'));
    expect(details.open).toBe(false);
    expect(getComputedStyle(panel.querySelector('dl')).gridTemplateColumns).toContain('16em');
  });

  it.each([false, undefined])('does not infer completion when native status is %s', (completed) => {
    render(<FirebirdServiceObservation observation={{...observation,
      server_completed: completed, service_release: {service_handle_released: false}}} />);
    expect(screen.getByLabelText('Firebird service result')).toHaveTextContent('Completion not reported');
    expect(screen.getByLabelText('Firebird service result')).toHaveTextContent('Release unconfirmed');
  });

  it('does not infer release from an absent receipt', () => {
    render(<FirebirdServiceObservation observation={{...observation, service_release: undefined}} />);
    expect(screen.getByLabelText('Firebird service result')).toHaveTextContent('Not reported');
  });

  it('explicitly reports truncated native output', () => {
    render(<FirebirdServiceObservation observation={{...observation, output_truncated: true}} />);
    expect(screen.getByRole('alert')).toHaveTextContent('displayed text is incomplete');
  });

  it('preserves driver-provided line endings without adding blank lines', () => {
    const lines = ['Header\n', '\n', '  Page size 8192\r\n', 'Final line'];
    render(<FirebirdServiceObservation observation={{...observation, output: lines}} />);
    expect(screen.getByLabelText('Firebird native service output').textContent)
      .toBe(lines.join(''));
  });

  it('shows a no-output observation without inventing a success message', () => {
    render(<FirebirdServiceObservation observation={{...observation, output: []}} />);
    expect(screen.getByText('No textual output was returned.')).toBeVisible();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it.each([undefined, 'not an array', ['valid', {unknown: 1}]])('flags malformed output %s', (output) => {
    render(<FirebirdServiceObservation observation={{...observation, output}} />);
    expect(screen.getByRole('alert')).toHaveTextContent('could not be displayed in full');
    expect(JSON.parse(screen.getByLabelText('Firebird native service receipt').textContent))
      .toEqual({...observation, output});
  });
});
