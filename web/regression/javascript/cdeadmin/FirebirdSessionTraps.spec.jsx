import {fireEvent, render, screen} from '@testing-library/react';
import FirebirdSessionTraps, {FIREBIRD_TRAP_COMMANDS}
  from '../../../pgadmin/static/js/Dialogs/FirebirdSessionTraps';

describe('Firebird retained-session trap controls', () => {
  it('requires an existing session and explicit one-use confirmation', () => {
    const onExecute = jest.fn();
    const {rerender} = render(<FirebirdSessionTraps onExecute={onExecute} />);
    expect(screen.getByRole('button', {name: 'Inspect DECFLOAT traps'})).toBeDisabled();
    expect(screen.getByRole('button', {name: 'Disable all DECFLOAT traps'})).toBeDisabled();
    rerender(<FirebirdSessionTraps sessionId="one" onExecute={onExecute} />);
    fireEvent.click(screen.getByRole('button', {name: 'Inspect DECFLOAT traps'}));
    expect(onExecute).toHaveBeenLastCalledWith(FIREBIRD_TRAP_COMMANDS.inspect);
    const confirm = screen.getByRole('checkbox');
    fireEvent.click(confirm);
    fireEvent.click(screen.getByRole('button', {name: 'Disable all DECFLOAT traps'}));
    expect(onExecute).toHaveBeenLastCalledWith('SET DECFLOAT TRAPS TO');
    expect(confirm).not.toBeChecked();
    expect(screen.getByRole('button', {name: 'Disable all DECFLOAT traps'})).toBeDisabled();
  });

  it('does not carry authorization to another session or allow busy actions', () => {
    const onExecute = jest.fn();
    const {rerender} = render(<FirebirdSessionTraps sessionId="one" onExecute={onExecute} />);
    fireEvent.click(screen.getByRole('checkbox'));
    rerender(<FirebirdSessionTraps sessionId="two" onExecute={onExecute} />);
    expect(screen.getByRole('checkbox')).not.toBeChecked();
    fireEvent.click(screen.getByRole('checkbox'));
    rerender(<FirebirdSessionTraps sessionId="two" disabled onExecute={onExecute} />);
    for (const button of screen.getAllByRole('button')) {
      expect(button).toBeDisabled();
      fireEvent.click(button);
    }
    expect(onExecute).not.toHaveBeenCalled();
    expect(screen.getByText(/Commit and rollback do not restore/)).toBeVisible();
  });
});
