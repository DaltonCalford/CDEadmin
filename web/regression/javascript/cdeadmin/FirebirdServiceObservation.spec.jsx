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

  it.each([
    [{mode: 'guid', guid: '{00112233-4455-6677-8899-AABBCCDDEEFF}'}, 'GUID: {00112233-4455-6677-8899-AABBCCDDEEFF}'],
    [{mode: 'level', level: 0}, 'Level: 0'],
    [{mode: 'level', level: 3}, 'Level: 3'],
    [null, 'Not reported'],
    [{mode: 'level', level: -1}, 'Not reported'],
    [{mode: 'level', level: '0'}, 'Not reported'],
    [{mode: 'guid', guid: {}}, 'Not reported'],
  ])('labels the requested backup selection without inferring it: %j', (selection, text) => {
    render(<FirebirdServiceObservation observation={{...observation,
      backup_selection_requested: selection}} />);
    expect(screen.getByText('Requested backup selection').nextElementSibling).toHaveTextContent(text);
  });

  it.each([
    [{unit: 'ROWS', value: 1}, 'Newest rows (timestamp cutoff): 1'],
    [{unit: 'DAYS', value: 7}, 'Calendar days including today: 7'],
    [null, 'Not reported'],
    [{unit: 'OTHER', value: 1}, 'Not reported'],
    [{unit: 'ROWS', value: 0}, 'Not reported'],
    [{unit: 'DAYS', value: '7'}, 'Not reported'],
  ])('shows requested history retention, not an asserted database result: %j', (retention, text) => {
    render(<FirebirdServiceObservation observation={{...observation,
      history_retention_requested: retention}} />);
    expect(screen.getByText('Requested backup-history retention').nextElementSibling).toHaveTextContent(text);
  });

  it('does not add backup fields to unrelated service observations', () => {
    render(<FirebirdServiceObservation observation={observation} />);
    expect(screen.queryByText('Requested backup selection')).not.toBeInTheDocument();
    expect(screen.queryByText('Requested backup-history retention')).not.toBeInTheDocument();
    expect(screen.queryByText('Requested backup read I/O')).not.toBeInTheDocument();
  });

  it.each([
    ['NATIVE', 'Native default'], ['ON', 'Direct reads ON'], ['OFF', 'Direct reads OFF'],
    [null, 'Not reported'], [false, 'Not reported'], ['toString', 'Not reported'],
    [{toString: null}, 'Not reported'], ['unknown', 'Not reported'],
  ])('shows requested backup I/O policy without asserting observed OS behavior: %j', (policy, text) => {
    render(<FirebirdServiceObservation observation={{...observation, backup_io_requested: policy}} />);
    expect(screen.getByText('Requested backup read I/O').nextElementSibling).toHaveTextContent(text);
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
