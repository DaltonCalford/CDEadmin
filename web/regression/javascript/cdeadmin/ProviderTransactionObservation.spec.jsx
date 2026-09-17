import {fireEvent, render, screen} from '@testing-library/react';
import {withTheme} from '../fake_theme';
import ProviderTransactionObservation from 'sources/Dialogs/ProviderTransactionObservation';

const available = (value) => ({available: true, value});
const observation = (state='active', fields={}) => ({
  transaction_model: 'firebird-native-transaction',
  provider_payload: {state, native_observation: 'Firebird transaction info', fields: {
    transaction_id: available(321), isolation: available('SNAPSHOT'),
    read_only: available(false), lock_timeout_seconds: available(-1),
    snapshot_number: available(0), oldest_interesting_at_start: available(123),
    oldest_active_at_start: available(124), oldest_snapshot_at_start: available(125),
    ...fields,
  }},
});

describe('Native Firebird transaction presentation', () => {
  const Component = withTheme(ProviderTransactionObservation);

  it('shows native values with labels and keeps raw details collapsed', () => {
    render(<Component transaction={observation()} label="Query transaction" />);
    expect(screen.getByRole('region', {name: 'Query transaction'})).toBeInTheDocument();
    ['Transaction ID', '321', 'SNAPSHOT', 'Read/write', 'Wait indefinitely',
      'Snapshot number', '0', '123', '124', '125'].forEach((text) => {
      expect(screen.getByText(text)).toBeVisible();
    });
    const summary = screen.getByText('Native observation details');
    expect(summary.parentElement).not.toHaveAttribute('open');
    fireEvent.click(summary);
    expect(summary.parentElement).toHaveAttribute('open');
    fireEvent.click(summary);
    expect(summary.parentElement).not.toHaveAttribute('open');
    const grid = screen.getByText('Transaction ID').closest('dl');
    expect(getComputedStyle(grid).gridTemplateColumns).toContain('16em');
  });

  it.each([
    [0, 'No wait'], [3, '3 seconds'], [-1, 'Wait indefinitely'],
  ])('renders native lock timeout %s', (value, expected) => {
    render(<Component transaction={observation('active', {
      lock_timeout_seconds: available(value), read_only: available(true),
    })} />);
    expect(screen.getByText(expected)).toBeVisible();
    expect(screen.getByText('Read only')).toBeVisible();
  });

  it.each([
    ['idle', 'Idle — no active transaction'], ['closed', 'Closed'],
    ['unknown', 'Transaction state unavailable'],
  ])('does not present stale native fields for %s state', (state, expected) => {
    render(<Component transaction={observation(state)} />);
    expect(screen.getByText(expected)).toBeVisible();
    expect(screen.queryByText('Transaction ID')).not.toBeInTheDocument();
  });

  it('marks missing information as unavailable, not zero or false', () => {
    render(<Component transaction={observation('active', {
      read_only: {available: false}, snapshot_number: undefined,
    })} />);
    expect(screen.getAllByText('Unavailable')).toHaveLength(2);
    expect(screen.queryByText('Read/write')).not.toBeInTheDocument();
    expect(screen.queryByText('0')).not.toBeInTheDocument();
  });

  it('preserves another engine’s native presentation without Firebird fields', () => {
    render(<Component transaction={{transaction_model: 'other',
      provider_payload: {in_transaction: true}}} label="Other transaction" />);
    expect(screen.getByLabelText('Other transaction')).toHaveTextContent('in_transaction');
    expect(screen.queryByText('Firebird transaction')).not.toBeInTheDocument();
  });

  it.each(['idle', 'active', 'closed', 'unknown'])('labels both dialect observations in %s state', (state) => {
    const value = observation(state);
    value.provider_payload.attachment_fields = {
      client_sql_dialect: available(3), database_sql_dialect: available(1),
    };
    render(<Component transaction={value} />);
    expect(screen.getByText('Client SQL dialect').nextElementSibling).toHaveTextContent('3');
    expect(screen.getByText('Stored database SQL dialect').nextElementSibling).toHaveTextContent('1');
    expect(screen.getByText(/Client SQL dialect controls statement interpretation/)).toBeVisible();
  });

  it('does not infer an unavailable database dialect from the client dialect', () => {
    const value = observation('idle');
    value.provider_payload.attachment_fields = {
      client_sql_dialect: available(3), database_sql_dialect: {available: false},
    };
    render(<Component transaction={value} />);
    expect(screen.getByText('Client SQL dialect').nextElementSibling).toHaveTextContent('3');
    expect(screen.getByText('Stored database SQL dialect').nextElementSibling).toHaveTextContent('Unavailable');
  });
});
