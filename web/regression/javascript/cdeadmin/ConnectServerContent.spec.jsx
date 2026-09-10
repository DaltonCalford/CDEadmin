/////////////////////////////////////////////////////////////
//
// CDEadmin - Multi-engine Database Administration
//
// Copyright (C) 2013 - 2026, The pgAdmin Development Team
// This software is released under the PostgreSQL Licence
//
//////////////////////////////////////////////////////////////

import {fireEvent, render, screen} from '@testing-library/react';
import ConnectServerContent from
  '../../../pgadmin/static/js/Dialogs/ConnectServerContent';

jest.mock('../../../pgadmin/static/js/components/Buttons', () => ({
  DefaultButton: ({children, startIcon: _startIcon, ...props}) =>
    <button {...props}>{children}</button>,
  PrimaryButton: ({children, startIcon: _startIcon, ...props}) =>
    <button {...props}>{children}</button>,
}));

describe('provider connection credentials', () => {
  it('reveals a password and submits a temporary alternate principal', () => {
    const onOK = jest.fn();
    render(<ConnectServerContent closeModal={jest.fn()} onOK={onOK}
      data={{
        prompt_password: true, prompt_tunnel_password: false,
        username: 'default_user', server_label: 'Firebird lab',
        allow_save_password: true, allow_user_override: true, errmsg: null,
      }} />);

    fireEvent.click(screen.getByRole('checkbox', {
      name: 'Connect as a different user',
    }));
    const inputs = screen.getAllByTestId('input-text');
    fireEvent.change(inputs[0], {target: {value: 'alternate_user'}});
    fireEvent.change(inputs[1], {target: {value: 'alternate-secret'}});
    expect(inputs[1]).toHaveAttribute('type', 'password');
    fireEvent.click(screen.getByRole('button', {name: 'Show password'}));
    expect(inputs[1]).toHaveAttribute('type', 'text');
    expect(screen.getByRole('checkbox', {name: 'Save Password'}))
      .toBeDisabled();
    fireEvent.click(screen.getByRole('button', {name: 'OK'}));

    const submitted = onOK.mock.calls[0][0];
    expect(submitted.get('connect_as')).toBe('alternate_user');
    expect(submitted.get('password')).toBe('alternate-secret');
    expect(submitted.has('save_password')).toBe(false);
  });
});
