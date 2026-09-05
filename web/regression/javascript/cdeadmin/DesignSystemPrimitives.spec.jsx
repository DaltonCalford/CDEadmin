/////////////////////////////////////////////////////////////
//
// CDEadmin - Multi-engine Database Administration
//
// Copyright (C) 2013 - 2026, The pgAdmin Development Team
// This software is released under the PostgreSQL Licence
//
//////////////////////////////////////////////////////////////

import InfoIcon from '@mui/icons-material/InfoRounded';
import {fireEvent, render, screen} from '@testing-library/react';
import {withTheme} from '../fake_theme';
import {
  Button,
  Checkbox,
  Dialog,
  IconButton,
  Link,
  NumberField,
  ScrollArea,
  SecretField,
  Select,
  StatusBadge,
  Switch,
  TextArea,
  TextField,
} from 'sources/cdeadmin_ui';

describe('CDEadmin design-system primitives', () => {
  it('maps primary intent through the compatibility button adapter', () => {
    const Component = withTheme(Button);
    render(<Component intent="primary">Connect</Component>);

    expect(screen.getByRole('button', {name: 'Connect'}))
      .toHaveClass('MuiButton-containedPrimary');
  });

  it('exposes loading and destructive action semantics', () => {
    const Component = withTheme(() => <>
      <Button intent="destructive">Delete</Button>
      <Button loading>Connecting</Button>
    </>);
    render(<Component />);

    expect(screen.getByRole('button', {name: 'Delete'}))
      .toHaveClass('MuiButton-colorError');
    expect(screen.getByRole('button', {name: 'Connecting'}))
      .toHaveAttribute('aria-busy', 'true');
    expect(screen.getByRole('button', {name: 'Connecting'})).toBeDisabled();
  });

  it('requires an accessible label for icon presentation', () => {
    const Component = withTheme(IconButton);
    render(<Component label="Connection details" icon={<InfoIcon />} />);

    expect(screen.getByRole('button', {name: 'Connection details'}))
      .toBeInTheDocument();
  });

  it('maps validation messages onto fields', () => {
    const Component = withTheme(TextField);
    render(<Component label="Port" validationMessage="Port is required" />);

    expect(screen.getByText('Port is required')).toBeInTheDocument();
    expect(screen.getByLabelText('Port')).toHaveAttribute('aria-invalid', 'true');
  });

  it('provides semantic number, multiline, and secret fields', () => {
    const Component = withTheme(() => <>
      <NumberField label="Timeout" />
      <TextArea label="Notes" />
      <SecretField label="Password" allowReveal revealed />
    </>);
    render(<Component />);

    expect(screen.getByLabelText('Timeout')).toHaveAttribute('type', 'number');
    expect(screen.getByLabelText('Notes').tagName).toBe('TEXTAREA');
    expect(screen.getByLabelText('Password')).toHaveAttribute('type', 'text');
  });

  it('never communicates status by color alone', () => {
    const Component = withTheme(StatusBadge);
    render(<Component status="warning" label="Reconnecting" live />);

    expect(screen.getByRole('status')).toHaveTextContent('Reconnecting');
  });

  it('normalizes choice values at the public component boundary', () => {
    const onCheckboxChange = jest.fn();
    const onSwitchChange = jest.fn();
    const Component = withTheme(() => <>
      <Checkbox label="Include system objects" onChange={onCheckboxChange} />
      <Switch label="Auto reconnect" onChange={onSwitchChange} />
      <Select
        label="Engine"
        value="firebird"
        options={[{value: 'firebird', label: 'Firebird'}]}
      />
    </>);
    render(<Component />);

    fireEvent.click(screen.getByRole(
      'checkbox', {name: 'Include system objects'}));
    fireEvent.click(screen.getByRole('switch', {name: 'Auto reconnect'}));

    expect(onCheckboxChange).toHaveBeenCalledWith(true, expect.anything());
    expect(onSwitchChange).toHaveBeenCalledWith(true, expect.anything());
    expect(screen.getByLabelText('Engine')).toBeInTheDocument();
  });

  it('provides labelled dialog structure', () => {
    const Component = withTheme(Dialog);
    render(<Component
      open
      title="Delete collection"
      actions={<Button>Cancel</Button>}
    >
      This action cannot be undone.
    </Component>);

    expect(screen.getByRole('dialog', {name: 'Delete collection'}))
      .toBeInTheDocument();
  });

  it('provides safe external links and keyboard-scrollable regions', () => {
    const Component = withTheme(() => <>
      <Link href="https://example.test" external>Documentation</Link>
      <ScrollArea label="Query results">Rows</ScrollArea>
    </>);
    render(<Component />);

    expect(screen.getByRole('link', {name: 'Documentation'}))
      .toHaveAttribute('rel', 'noopener noreferrer');
    expect(screen.getByRole('region', {name: 'Query results'}))
      .toHaveAttribute('tabindex', '0');
  });
});
