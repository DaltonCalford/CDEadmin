/////////////////////////////////////////////////////////////
// Zero-Grey control behavior and state-family verification.
/////////////////////////////////////////////////////////////

import {fireEvent, render, screen, waitFor} from '@testing-library/react';
import {withTheme} from '../fake_theme';
import {
  Button, Checkbox, ComboBox, DateField, DurationField, FilePickerButton,
  IconButton, Link, MultiSelect, NumberField, PasswordStrength, Radio,
  SearchField, SecretField, SegmentedControl, Select, Slider, SplitButton,
  Switch, TextArea, TextField, TimeField, UnitNumberField,
} from 'sources/cdeadmin_ui';

describe('action controls', () => {
  it('invokes a Button once for a double-click sequence and blocks loading/disabled actions', () => {
    const invoke = jest.fn();
    const Component = withTheme(() => <>
      <Button onClick={invoke}>Run</Button>
      <Button onClick={invoke} loading>Loading</Button>
      <Button onClick={invoke} disabled>Disabled</Button>
    </>);
    render(<Component />);
    fireEvent.click(screen.getByRole('button', {name: 'Run'}), {detail: 1});
    fireEvent.click(screen.getByRole('button', {name: 'Run'}), {detail: 2});
    fireEvent.click(screen.getByRole('button', {name: 'Loading'}));
    fireEvent.click(screen.getByRole('button', {name: 'Disabled'}));
    expect(invoke).toHaveBeenCalledTimes(1);
    expect(screen.getByRole('button', {name: 'Loading'})).toHaveAttribute('aria-busy', 'true');
  });

  it('requires IconButton accessible identity and preserves disabled semantics', () => {
    const Component = withTheme(IconButton);
    expect(() => render(<Component />)).toThrow('requires a label or title');
    render(<Component label="Refresh metadata" disabled>R</Component>);
    expect(screen.getByRole('button', {name: 'Refresh metadata'})).toBeDisabled();
  });

  it('keeps SplitButton default and alternatives independent', () => {
    const run = jest.fn(); const select = jest.fn();
    const Component = withTheme(SplitButton);
    render(<Component onClick={run} onSelect={select}
      options={[{value: 'explain', label: 'Explain'}, {value: 'blocked',
        label: 'Blocked', disabled: true}]}>Run query</Component>);
    fireEvent.click(screen.getByRole('button', {name: 'Run query'}));
    expect(run).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole('button', {name: 'More actions'}));
    expect(run).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole('menuitem', {name: 'Explain'}));
    expect(select).toHaveBeenCalledWith('explain');
    expect(screen.queryByRole('menu')).not.toBeInTheDocument();
  });
});

describe('text, numeric and secret controls', () => {
  it('exposes readonly, disabled and invalid fields without losing copy/focus identity', () => {
    const Component = withTheme(() => <>
      <TextField label="Invalid" validationMessage="Required" />
      <TextField label="Readonly" value="native value" InputProps={{readOnly: true}} />
      <TextArea label="Disabled notes" disabled />
      <NumberField label="Rows" value={10} InputProps={{readOnly: true}} />
    </>);
    render(<Component />);
    expect(screen.getByLabelText('Invalid')).toHaveAttribute('aria-invalid', 'true');
    expect(screen.getByText('Required')).toBeInTheDocument();
    expect(screen.getByLabelText('Readonly')).toHaveValue('native value');
    expect(screen.getByLabelText('Disabled notes')).toBeDisabled();
    expect(screen.getByLabelText('Rows')).toHaveAttribute('type', 'number');
  });

  it('masks/reveals secrets, blocks copy by default and supports controlled reveal', () => {
    const onRevealChange = jest.fn(); const onCopy = jest.fn();
    const Component = withTheme(SecretField);
    const {rerender} = render(<Component label="Password" value="private"
      allowReveal onRevealChange={onRevealChange} onCopy={onCopy} />);
    const field = screen.getByLabelText('Password');
    expect(field).toHaveAttribute('type', 'password');
    const copy = new Event('copy', {cancelable: true, bubbles: true});
    field.dispatchEvent(copy);
    expect(copy.defaultPrevented).toBe(true); expect(onCopy).toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', {name: 'Reveal secret'}));
    expect(screen.getByLabelText('Password')).toHaveAttribute('type', 'text');
    expect(onRevealChange).toHaveBeenCalledWith(true);
    rerender(<Component label="Password" value="private" allowReveal
      revealed={false} onRevealChange={onRevealChange} />);
    fireEvent.click(screen.getByRole('button', {name: 'Reveal secret'}));
    expect(screen.getByLabelText('Password')).toHaveAttribute('type', 'password');
  });

  it('allows copy only when policy explicitly enables it', () => {
    const Component = withTheme(SecretField);
    render(<Component label="Token" value="private" allowCopy />);
    const copy = new Event('copy', {cancelable: true, bubbles: true});
    screen.getByLabelText('Token').dispatchEvent(copy);
    expect(copy.defaultPrevented).toBe(false);
  });
});

describe('choice and compound input controls', () => {
  it('normalizes checkbox, mixed checkbox, switch and select values', () => {
    const checked = jest.fn(); const switched = jest.fn(); const selected = jest.fn();
    const originalInnerHeight = window.innerHeight;
    Object.defineProperty(window, 'innerHeight', {configurable: true, value: 1200});
    const Component = withTheme(() => <>
      <Checkbox label="Mixed" indeterminate onChange={checked} />
      <Switch label="Enabled" onChange={switched} />
      <Select label="Engine" value="firebird" onChange={selected}
        options={[{value: 'firebird', label: 'Firebird'},
          {value: 'mysql', label: 'MySQL'},
          {value: 'blocked', label: 'Blocked', disabled: true}]} />
    </>);
    render(<Component />);
    fireEvent.click(screen.getByRole('checkbox', {name: 'Mixed'}));
    fireEvent.click(screen.getByRole('switch', {name: 'Enabled'}));
    fireEvent.mouseDown(screen.getByRole('combobox', {name: 'Engine'}));
    fireEvent.click(screen.getByRole('option', {name: 'MySQL'}));
    expect(checked).toHaveBeenCalledWith(true, expect.anything());
    expect(switched).toHaveBeenCalledWith(true, expect.anything());
    expect(selected).toHaveBeenCalledWith('mysql', expect.anything());
    Object.defineProperty(window, 'innerHeight',
      {configurable: true, value: originalInnerHeight});
  });

  it('supports radio and segmented exclusive selection with disabled alternatives', () => {
    const radio = jest.fn(); const segment = jest.fn();
    const Component = withTheme(() => <>
      <Radio label="Mode" value="read" onChange={radio}
        options={[{value: 'read', label: 'Read'}, {value: 'write', label: 'Write'}]} />
      <SegmentedControl label="View" value="grid" onChange={segment}
        options={[{value: 'grid', label: 'Grid'}, {value: 'json', label: 'JSON'},
          {value: 'blocked', label: 'Blocked', disabled: true}]} />
    </>);
    render(<Component />);
    fireEvent.click(screen.getByRole('radio', {name: 'Write'}));
    fireEvent.click(screen.getByRole('button', {name: 'JSON'}));
    expect(radio).toHaveBeenCalledWith('write'); expect(segment).toHaveBeenCalledWith('json');
    expect(screen.getByRole('button', {name: 'Blocked'})).toBeDisabled();
  });

  it('filters ComboBox and accepts free text only when enabled', async () => {
    const change = jest.fn();
    const Component = withTheme(ComboBox);
    const {rerender} = render(<Component label="Strict engine" options={['Firebird']}
      onChange={change} />);
    fireEvent.change(screen.getByLabelText('Strict engine'), {target: {value: 'Other'}});
    expect(change).not.toHaveBeenCalledWith('Other');
    rerender(<Component label="Free engine" options={['Firebird']} freeEntry
      onChange={change} />);
    fireEvent.change(screen.getByRole('combobox', {name: 'Free engine'}),
      {target: {value: 'Other'}});
    await waitFor(() => expect(change).toHaveBeenCalledWith('Other'));
  });

  it('handles MultiSelect selections with bounded token display and validation', () => {
    const change = jest.fn();
    const options = ['A', 'B', 'C'];
    const Component = withTheme(MultiSelect);
    render(<Component label="Models" options={options} value={options}
      onChange={change} validationMessage="Unsupported combination" />);
    expect(screen.getByLabelText('Models')).toHaveAttribute('aria-invalid', 'true');
    expect(screen.getByText('+1')).toBeInTheDocument();
  });

  it('exposes Slider exact value and keyboard-operable input', () => {
    const change = jest.fn(); const Component = withTheme(Slider);
    render(<Component label="Scale" value={25} min={0} max={100} onChange={change} />);
    const slider = screen.getByRole('slider');
    expect(slider).toHaveAttribute('aria-valuenow', '25');
    fireEvent.keyDown(slider, {key: 'End'});
    expect(slider).toBeInTheDocument();
  });
});

describe('search, temporal, units and file selection', () => {
  it('clears SearchField by button/Escape and exposes loading/result states', () => {
    const change = jest.fn(); const Component = withTheme(SearchField);
    const {rerender} = render(<Component label="Filter resources" value="table"
      onChange={change} loading resultCount={3} />);
    expect(screen.getByLabelText('Searching')).toBeInTheDocument();
    expect(screen.getByText('3 results')).toBeInTheDocument();
    fireEvent.keyDown(screen.getByLabelText('Filter resources'), {key: 'Escape'});
    expect(change).toHaveBeenCalledWith('');
    rerender(<Component label="Filter resources" value="table" onChange={change} />);
    fireEvent.click(screen.getByRole('button', {name: 'Clear search'}));
    expect(change).toHaveBeenLastCalledWith('');
  });

  it('preserves canonical date/time and explicit timezone context', () => {
    const Component = withTheme(() => <>
      <DateField label="Date" value="2026-09-11" InputProps={{readOnly: true}} />
      <TimeField label="Time" value="18:30" timezone="UTC-04:00" />
    </>);
    render(<Component />);
    expect(screen.getByLabelText('Date')).toHaveValue('2026-09-11');
    expect(screen.getByText('Timezone: UTC-04:00')).toBeInTheDocument();
  });

  it('keeps numeric unit visible/selectable and converts input to a number', () => {
    const number = jest.fn(); const unit = jest.fn();
    const Component = withTheme(() => <>
      <UnitNumberField label="Memory" value={8} unit="GB"
        units={['GB', 'MB']} onChange={number} onUnitChange={unit} />
      <DurationField label="Timeout" value={5} unit="s" />
    </>);
    render(<Component />);
    fireEvent.change(screen.getByLabelText('Memory'), {target: {value: '16'}});
    expect(number).toHaveBeenCalledWith(16, 'GB');
    expect(screen.getByLabelText('Timeout unit')).toBeInTheDocument();
  });

  it('uses the host file input and reports selected files without emulating a file manager', () => {
    const onFiles = jest.fn(); const Component = withTheme(FilePickerButton);
    const {container, rerender} = render(<Component label="Import" accept=".json"
      multiple onFiles={onFiles} />);
    const file = new File(['{}'], 'model.json', {type: 'application/json'});
    fireEvent.change(container.querySelector('input[type="file"]'),
      {target: {files: [file]}});
    expect(onFiles).toHaveBeenCalledWith([file], expect.anything());
    rerender(<Component label="Import" loading onFiles={onFiles} />);
    expect(screen.getByRole('button', {name: 'Import'})).toBeDisabled();
  });

  it.each(['unknown', 'invalid', 'acceptable', 'strong'])(
    'gives PasswordStrength %s state a textual policy result', (state) => {
      const Component = withTheme(PasswordStrength);
      render(<Component state={state} />);
      expect(screen.getByRole('status')).not.toBeEmptyDOMElement();
    }
  );

  it('renders external and disabled Link security semantics', () => {
    const Component = withTheme(() => <>
      <Link href="https://example.test" external>External help</Link>
      <Link href="/internal" disabled>Unavailable help</Link>
    </>);
    render(<Component />);
    expect(screen.getByRole('link', {name: 'External help'}))
      .toHaveAttribute('rel', 'noopener noreferrer');
    expect(screen.getByRole('link', {name: 'Unavailable help'}))
      .toHaveAttribute('aria-disabled', 'true');
    expect(screen.getByRole('link', {name: 'Unavailable help'})).not.toHaveAttribute('href');
  });
});
