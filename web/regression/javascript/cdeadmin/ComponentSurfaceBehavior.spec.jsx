/////////////////////////////////////////////////////////////
// Zero-Grey overlays, feedback and workbench component verification.
/////////////////////////////////////////////////////////////

import {act, fireEvent, render, screen, waitFor} from '@testing-library/react';
import {withTheme} from '../fake_theme';
import {
  AboutDialog, Badge, Banner, ColorPicker, CredentialsDialog,
  DestructiveConfirmationDialog, Dialog, Divider, Drawer, DropZone, EmptyState,
  EnvironmentIndicator, FontPicker, FormSection, InspectorSection,
  PreferencesSurface, ProgressBar, SimpleInputDialog, Skeleton, StatusBar,
  StatusDot, Toast, Toolbar, ToolboxItem, UnsavedChangesDialog,
  ValidationMessage, Wizard,
} from 'sources/cdeadmin_ui';

const CREDENTIAL_FIELDS = Object.freeze([
  {id: 'username', label: 'Username', required: true},
  {id: 'password', label: 'Password', type: 'secret', required: true},
]);

describe('feedback and status states', () => {
  it.each(['neutral', 'success', 'warning', 'error', 'info'])(
    'Badge and StatusDot communicate %s with text/accessibility identity', (status) => {
      const Component = withTheme(() => <>
        <Badge status={status} label={'Badge ' + status} />
        <StatusDot status={status} label={'Dot ' + status} />
      </>);
      render(<Component />);
      expect(screen.getByText('Badge ' + status)).toBeInTheDocument();
      expect(screen.getByLabelText('Dot ' + status)).toBeInTheDocument();
    }
  );

  it.each(['info', 'warning', 'error', 'success'])(
    'renders and dismisses %s Banner with semantic live role', (status) => {
      const close = jest.fn(); const Component = withTheme(Banner);
      render(<Component status={status} onClose={close}>Message</Component>);
      expect(screen.getByRole(status === 'error' ? 'alert' : 'status'))
        .toHaveTextContent('Message');
      fireEvent.click(screen.getByTitle('Close'));
      expect(close).toHaveBeenCalled();
    }
  );

  it.each(['development', 'test', 'staging', 'production', 'unknown'])(
    'names EnvironmentIndicator %s without colour-only meaning', (environment) => {
      const Component = withTheme(EnvironmentIndicator);
      render(<Component environment={environment} />);
      expect(screen.getByText(environment.toUpperCase())).toBeInTheDocument();
    }
  );

  it('renders associated validation severity and clamped/indeterminate progress', () => {
    const Component = withTheme(() => <>
      <ValidationMessage id="field-error" status="error">Invalid value</ValidationMessage>
      <ProgressBar value={150} label="Import progress" />
      <ProgressBar status="paused" label="Paused progress" />
    </>);
    render(<Component />);
    expect(screen.getByRole('alert')).toHaveAttribute('id', 'field-error');
    expect(screen.getByRole('status', {name: 'Import progress'})).toHaveTextContent('100%');
    expect(screen.getByRole('status', {name: 'Paused progress'})).toHaveTextContent('paused');
  });

  it('keeps Skeleton geometry and accessible loading identity', () => {
    const Component = withTheme(Skeleton);
    const {container} = render(<Component lines={4} label="Loading metadata" />);
    expect(screen.getByRole('status', {name: 'Loading metadata'}))
      .toHaveAttribute('aria-busy', 'true');
    expect(container.querySelectorAll('[class*="MuiBox-root"]')).toHaveLength(5);
  });
});

describe('Toast lifecycle', () => {
  beforeEach(() => jest.useFakeTimers());
  afterEach(() => jest.useRealTimers());

  it.each([['success', 6000], ['info', 6000], ['warning', 10000]])(
    'auto-dismisses %s after %sms', (status, duration) => {
      const close = jest.fn(); const Component = withTheme(Toast);
      render(<Component status={status} onClose={close}>Saved</Component>);
      act(() => jest.advanceTimersByTime(duration - 1));
      expect(close).not.toHaveBeenCalled();
      act(() => jest.advanceTimersByTime(1));
      expect(close).toHaveBeenCalledWith('timeout');
    }
  );

  it('keeps error persistent and pauses/resumes timed Toast on hover', () => {
    const close = jest.fn(); const Component = withTheme(Toast);
    const {rerender} = render(<Component status="error" onClose={close}>Failed</Component>);
    act(() => jest.advanceTimersByTime(60000)); expect(close).not.toHaveBeenCalled();
    rerender(<Component status="info" duration={1000} onClose={close}>Info</Component>);
    const toast = screen.getByRole('status');
    fireEvent.mouseEnter(toast); act(() => jest.advanceTimersByTime(2000));
    expect(close).not.toHaveBeenCalled();
    fireEvent.mouseLeave(toast); act(() => jest.advanceTimersByTime(1000));
    expect(close).toHaveBeenCalledWith('timeout');
  });

  it('renders progress and explicit dismissal behavior', () => {
    const close = jest.fn(); const Component = withTheme(Toast);
    render(<Component status="progress" progress={45} onClose={close}
      title="Export">Rows</Component>);
    expect(screen.getByText('45%')).toBeInTheDocument();
    fireEvent.click(screen.getByTitle('Close'));
    expect(close).toHaveBeenCalledWith('dismiss');
  });
});

describe('workbench chrome', () => {
  it('groups Toolbar and StatusBar content with explicit region identity/state', () => {
    const Component = withTheme(() => <>
      <Toolbar label="Query commands" trailing={<span>Tail</span>}>Run</Toolbar>
      <StatusBar status="warning" label="Query status">Transaction active</StatusBar>
      <Divider aria-label="Action group divider" />
    </>);
    render(<Component />);
    expect(screen.getByRole('toolbar', {name: 'Query commands'})).toHaveTextContent('RunTail');
    expect(screen.getByRole('status', {name: 'Query status'})).toHaveAttribute(
      'data-status', 'warning'
    );
    expect(screen.getByRole('separator', {name: 'Action group divider'})).toBeInTheDocument();
  });

  it('expands/collapses InspectorSection and FormSection and respects disabled lock', () => {
    const Component = withTheme(() => <>
      <InspectorSection title="Identity">Resource details</InspectorSection>
      <FormSection title="Advanced" defaultExpanded={false}>Options</FormSection>
      <InspectorSection title="Locked" locked>Immutable</InspectorSection>
    </>);
    render(<Component />);
    fireEvent.click(screen.getByRole('button', {name: 'Collapse Identity'}));
    expect(screen.getByRole('button', {name: 'Expand Identity'})).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', {name: 'Expand Advanced'}));
    expect(screen.getByText('Options')).toBeVisible();
    expect(screen.getByRole('button', {name: 'Collapse Locked'})).toBeDisabled();
  });

  it('resizes/expands Drawer and inserts ToolboxItem by keyboard/double click', () => {
    const resize = jest.fn(); const insert = jest.fn();
    const Component = withTheme(() => <>
      <Drawer open height={240} onHeightChange={resize}>Tasks</Drawer>
      <ToolboxItem label="Node" payload={{type: 'node'}} onInsert={insert} />
      <ToolboxItem label="Disabled edge" payload={{type: 'edge'}} disabled
        onInsert={insert} />
    </>);
    render(<Component />);
    fireEvent.keyDown(screen.getByRole('separator'), {key: 'ArrowDown'});
    expect(resize).toHaveBeenCalledWith(244);
    const node = screen.getByRole('button', {name: 'Node'});
    fireEvent.keyDown(node, {key: ' '}); fireEvent.doubleClick(node);
    expect(insert).toHaveBeenCalledTimes(2);
    expect(screen.getByRole('button', {name: 'Disabled edge'}))
      .toHaveAttribute('aria-disabled', 'true');
  });

  it('shows valid/invalid DropZone states, reason, Escape cancellation and valid drop', () => {
    const drop = jest.fn(); const validate = jest.fn((transfer) => transfer.valid ?
      {valid: true} : {valid: false, reason: 'Tables only'});
    const Component = withTheme(DropZone);
    render(<Component label="Diagram canvas" validate={validate} onDrop={drop}>Canvas</Component>);
    const zone = screen.getByRole('group', {name: 'Diagram canvas'});
    fireEvent.dragOver(zone, {dataTransfer: {valid: false}});
    expect(zone).toHaveAttribute('data-drop-state', 'drag_invalid');
    expect(screen.getByRole('alert')).toHaveTextContent('Tables only');
    fireEvent.keyDown(zone, {key: 'Escape'});
    expect(zone).toHaveAttribute('data-drop-state', 'idle');
    const dataTransfer = {valid: true, setData: jest.fn()};
    fireEvent.drop(zone, {dataTransfer});
    expect(drop).toHaveBeenCalledWith(dataTransfer, expect.anything());
    expect(zone).toHaveAttribute('data-drop-state', 'idle');
  });
});

describe('standard dialogs and surfaces', () => {
  it('runs Dialog safe default action, blocks it while busy and exposes validation', () => {
    const apply = jest.fn(); const Component = withTheme(Dialog);
    const {rerender} = render(<Component open title="Edit model"
      validationError="Fix dimensions" defaultAction={apply}>Content</Component>);
    const dialog = screen.getByRole('dialog', {name: 'Edit model'});
    fireEvent.keyDown(dialog, {key: 'Enter'});
    expect(apply).toHaveBeenCalledTimes(1);
    rerender(<Component open busy title="Edit model" defaultAction={apply}>Content</Component>);
    fireEvent.keyDown(screen.getByRole('dialog'), {key: 'Enter'});
    expect(apply).toHaveBeenCalledTimes(1);
    expect(screen.getByRole('dialog')).toHaveAttribute('aria-busy', 'true');
  });

  it('executes every explicit unsaved-changes path and defaults Enter to save', () => {
    const save = jest.fn(); const discard = jest.fn(); const cancel = jest.fn();
    const Component = withTheme(UnsavedChangesDialog);
    render(<Component open asset="Sales model" onSave={save}
      onDiscard={discard} onCancel={cancel} />);
    expect(screen.getByRole('dialog', {name: 'Save changes to “Sales model”?'}))
      .toHaveTextContent('project authority');
    fireEvent.keyDown(screen.getByRole('dialog'), {key: 'Enter'});
    fireEvent.click(screen.getByRole('button', {name: 'Don’t Save'}));
    fireEvent.click(screen.getByRole('button', {name: 'Cancel'}));
    expect(save).toHaveBeenCalledTimes(1);
    expect(discard).toHaveBeenCalledTimes(1);
    expect(cancel).toHaveBeenCalledTimes(1);
  });

  it('requires exact typed confirmation for high-impact production destruction', () => {
    const confirm = jest.fn(); const cancel = jest.fn();
    const Component = withTheme(DestructiveConfirmationDialog);
    render(<Component open verb="Drop" targetIdentity="PROD.SALES"
      environment="production" consequence="All rows will be unavailable."
      dependencies={['report.sales']} reversibility="Restore from backup only."
      highImpact onConfirm={confirm} onCancel={cancel} />);
    expect(screen.getByText(/report.sales/)).toBeInTheDocument();
    const action = screen.getByRole('button', {name: 'Drop'});
    expect(action).toBeDisabled();
    fireEvent.keyDown(screen.getByRole('dialog'), {key: 'Enter'});
    expect(confirm).not.toHaveBeenCalled();
    fireEvent.change(screen.getByLabelText('Type “PROD.SALES” to confirm'),
      {target: {value: 'PROD.SALES'}});
    fireEvent.click(action);
    expect(confirm).toHaveBeenCalledTimes(1);
  });

  it('collects provider credential fields ephemerally with validation and reveal', () => {
    const connect = jest.fn(); const cancel = jest.fn();
    const Component = withTheme(CredentialsDialog);
    const {rerender} = render(<Component open connection="Firebird localhost"
      authenticationMethod="Password" fields={CREDENTIAL_FIELDS}
      onConnect={connect} onCancel={cancel} />);
    expect(screen.getByRole('button', {name: 'Connect'})).toBeDisabled();
    fireEvent.change(screen.getByLabelText('Username'), {target: {value: 'sysdba'}});
    fireEvent.change(screen.getByLabelText('Password'), {target: {value: 'private'}});
    expect(screen.getByLabelText('Password')).toHaveAttribute('type', 'password');
    fireEvent.click(screen.getByRole('button', {name: 'Reveal secret'}));
    expect(screen.getByLabelText('Password')).toHaveAttribute('type', 'text');
    rerender(<Component open connection="Firebird localhost"
      authenticationMethod="Password" fields={[...CREDENTIAL_FIELDS]}
      busy={false} onConnect={connect} onCancel={cancel} />);
    expect(screen.getByLabelText('Username')).toHaveValue('sysdba');
    expect(screen.getByLabelText('Password')).toHaveValue('private');
    fireEvent.keyDown(screen.getByRole('dialog'), {key: 'Enter'});
    expect(connect).toHaveBeenCalledWith({username: 'sysdba', password: 'private'});
    rerender(<Component open={false} connection="Firebird localhost"
      authenticationMethod="Password" fields={CREDENTIAL_FIELDS}
      onConnect={connect} onCancel={cancel} />);
    rerender(<Component open connection="Firebird localhost"
      authenticationMethod="Password" fields={CREDENTIAL_FIELDS}
      onConnect={connect} onCancel={cancel} />);
    expect(screen.getByLabelText('Password')).toHaveValue('');
  });

  it('blocks invalid simple input and applies only the reviewed draft', () => {
    const apply = jest.fn(); const cancel = jest.fn();
    const Component = withTheme(SimpleInputDialog);
    render(<Component open title="Rename asset" label="Name" value=""
      validation={(value) => value.trim() ? '' : 'Name is required.'}
      onApply={apply} onCancel={cancel} />);
    expect(screen.getByRole('button', {name: 'Apply'})).toBeDisabled();
    fireEvent.change(screen.getByLabelText('Name'), {target: {value: 'Model 2'}});
    fireEvent.keyDown(screen.getByRole('dialog'), {key: 'Enter'});
    expect(apply).toHaveBeenCalledWith('Model 2');
  });

  it('previews approved FontPicker choices and commits/cancels draft state', () => {
    const confirm = jest.fn(); const close = jest.fn();
    const Component = withTheme(FontPicker);
    render(<Component open value="Fira Code" size={12}
      onConfirm={confirm} onClose={close} />);
    expect(screen.getByRole('combobox', {name: 'Font family'})).toHaveTextContent('Fira Code');
    fireEvent.change(screen.getByLabelText('Font size'), {target: {value: '18'}});
    expect(screen.getByLabelText('Font preview')).toHaveTextContent('ScratchRobin');
    fireEvent.click(screen.getByRole('button', {name: 'Apply font'}));
    expect(confirm).toHaveBeenCalledWith({family: 'Fira Code', size: 18});
    fireEvent.click(screen.getByRole('button', {name: 'Cancel'}));
    expect(close).toHaveBeenCalled();
  });

  it('separates ColorPicker palettes, validates canonical hex/contrast and commits safely', () => {
    const confirm = jest.fn(); const Component = withTheme(ColorPicker);
    render(<Component open value="#0077B6" background="#FFFFFF"
      minimumContrast={4.5} recent={['#123456']} onConfirm={confirm} />);
    expect(screen.getByRole('region', {name: 'Theme'})).toBeInTheDocument();
    expect(screen.getByRole('region', {name: 'Data Visualization'})).toBeInTheDocument();
    expect(screen.getByRole('region', {name: 'Recent'})).toBeInTheDocument();
    expect(screen.getByRole('region', {name: 'Custom colour'})).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Hex colour'), {target: {value: '#GGGGGG'}});
    expect(screen.getByRole('button', {name: 'Apply colour'})).toBeDisabled();
    expect(screen.getByText('Use #RRGGBB notation.')).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Hex colour'), {target: {value: '#000000'}});
    fireEvent.click(screen.getByRole('button', {name: 'Apply colour'}));
    expect(confirm).toHaveBeenCalledWith('#000000');
  });

  it('redacts AboutDialog secrets and executes diagnostics/credits/close actions', async () => {
    const copied = jest.fn(); const credits = jest.fn(); const close = jest.fn();
    const Component = withTheme(AboutDialog);
    render(<Component open version="1.0" build="abc"
      diagnostics={{host: 'desktop', password: 'never', nested: {accessToken: 'never'}}}
      credits={{ddn: 'MIT'}} onCopyDiagnostics={copied}
      onCredits={credits} onClose={close} />);
    expect(screen.getByRole('dialog', {name: 'About ScratchRobin CDE Admin'}))
      .not.toHaveTextContent('never');
    fireEvent.click(screen.getByRole('button', {name: 'Copy Diagnostics'}));
    await waitFor(() => expect(copied).toHaveBeenCalledWith(
      expect.not.stringContaining('never')
    ));
    fireEvent.click(screen.getByRole('button', {name: 'Credits & Licenses'}));
    expect(credits).toHaveBeenCalledWith({ddn: 'MIT'});
    fireEvent.click(screen.getByRole('button', {name: 'Close'}));
    expect(close).toHaveBeenCalled();
  });

  it('validates each Wizard step, supports back, and uses explicit final operation verb', async () => {
    const finish = jest.fn(); const Component = withTheme(Wizard);
    const steps = [
      {id: 'source', label: 'Source', validate: jest.fn(() => 'Choose source'),
        content: <span>Source content</span>},
      {id: 'review', label: 'Review', finishLabel: 'Create pipeline',
        content: <span>Review effects</span>},
    ];
    const {rerender} = render(<Component open title="Pipeline" steps={steps}
      onFinish={finish} />);
    fireEvent.click(screen.getByRole('button', {name: 'Next'}));
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('Choose source'));
    steps[0].validate.mockReturnValue(true);
    fireEvent.click(screen.getByRole('button', {name: 'Next'}));
    await waitFor(() => expect(screen.getByText('Review effects')).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', {name: 'Back'}));
    expect(screen.getByText('Source content')).toBeInTheDocument();
    rerender(<Component open title="Pipeline" initialStep={1} steps={steps}
      onFinish={finish} />);
    fireEvent.click(screen.getByRole('button', {name: 'Create pipeline'}));
    await waitFor(() => expect(finish).toHaveBeenCalled());
  });

  it('previews/resets/applies PreferencesSurface and reports restart/filtered-empty state', () => {
    const preview = jest.fn(); const apply = jest.fn(); const reset = jest.fn();
    const sections = [{id: 'visual', label: 'Visual', keywords: 'theme',
      render: ({values, update}) => <button onClick={() =>
        update({theme: values.theme === 'dark' ? 'light' : 'dark'})}>Toggle theme</button>}];
    const Component = withTheme(PreferencesSurface);
    render(<Component sections={sections} values={{theme: 'dark'}}
      restartRequired={['theme']} onPreview={preview} onApply={apply} onReset={reset} />);
    expect(screen.getByRole('button', {name: 'Apply'})).toBeDisabled();
    fireEvent.click(screen.getByRole('button', {name: 'Toggle theme'}));
    expect(preview).toHaveBeenCalledWith({theme: 'light'}, {theme: 'light'});
    expect(screen.getByText('Restart required for one or more changes.')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', {name: 'Apply'}));
    expect(apply).toHaveBeenCalledWith({theme: 'light'});
    fireEvent.click(screen.getByRole('button', {name: 'Reset'}));
    expect(reset).toHaveBeenCalledWith({theme: 'dark'});
    fireEvent.change(screen.getByLabelText('Search settings'), {target: {value: 'missing'}});
    expect(screen.getByText('No preference sections match this search.')).toBeInTheDocument();
  });

  it('provides EmptyState cause plus primary recovery action', () => {
    const retry = jest.fn(); const Component = withTheme(EmptyState);
    render(<Component message="Connection failed" actionLabel="Retry" onAction={retry} />);
    expect(screen.getByText('Connection failed')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', {name: 'Retry'}));
    expect(retry).toHaveBeenCalled();
  });
});
