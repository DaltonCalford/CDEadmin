import {render, screen} from '@testing-library/react';
import FirebirdLimboObservation from '../../../pgadmin/static/js/Dialogs/FirebirdLimboObservation';

describe('Firebird local prepared transaction results', () => {
  const observation = {operation_id: 'inspect_limbo', database: '/owned/東京.fdb',
    attachment_released: true, inventory_complete: true, transactions: []};

  it('shows an empty native inventory without asserting global finality', () => {
    render(<FirebirdLimboObservation observation={observation} />);
    expect(screen.getByText(/No local prepared transactions/)).toBeVisible();
    expect(screen.getByText(/global distributed outcome have not been verified/)).toBeVisible();
    expect(screen.queryByText(/inventory is incomplete/)).not.toBeInTheDocument();
  });

  it('preserves 64-bit IDs and renders metadata as text, not links or markup', () => {
    render(<FirebirdLimboObservation observation={{...observation, transactions: [{
      transaction_id: '9007199254740993', kind: 'distributed', host: 'coordinator',
      native_length_limit_reached: true,
      participants: [{database: '<img src=x onerror=alert(1)>', transaction_id: '9007199254740995'}],
    }]}} />);
    expect(screen.getByText('Local transaction 9007199254740993')).toBeVisible();
    expect(screen.getByText(/Transaction: 9007199254740995/)).toBeVisible();
    expect(screen.getByText(/<img src=x onerror=alert\(1\)>/)).toBeVisible();
    expect(screen.queryByRole('img')).not.toBeInTheDocument();
    expect(screen.queryByRole('link')).not.toBeInTheDocument();
    expect(screen.getByText(/255-byte storage limit/)).toBeVisible();
  });

  it.each([false, undefined])('does not declare an empty incomplete observation complete: %s', (complete) => {
    render(<FirebirdLimboObservation observation={{...observation, inventory_complete: complete}} />);
    expect(screen.getByText(/inventory is incomplete/)).toBeVisible();
    expect(screen.queryByText(/No local prepared transactions/)).not.toBeInTheDocument();
  });

  it.each(['commit', 'rollback'])('separates a returned %s decision from failed cleanup', (decision) => {
    render(<FirebirdLimboObservation observation={{...observation,
      operation_id: `${decision}_limbo_local`, native_decision_requested: decision,
      native_decision_returned: true, attachment_released: false}} />);
    expect(screen.getByText(/native decision call returned/)).toBeVisible();
    expect(screen.getByText(/Attachment release is unconfirmed/)).toBeVisible();
    expect(screen.getByText(/Do not replay the recovery decision/)).toBeVisible();
  });

  it('does not turn an absent decision receipt into success', () => {
    render(<FirebirdLimboObservation observation={{...observation,
      operation_id: 'commit_limbo_local'}} />);
    expect(screen.getByText(/Native decision completion is unconfirmed/)).toBeVisible();
  });
});
