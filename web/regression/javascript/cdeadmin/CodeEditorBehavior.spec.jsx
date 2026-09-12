/////////////////////////////////////////////////////////////
// CDEadmin semantic code-editor boundary verification.
/////////////////////////////////////////////////////////////

jest.mock('sources/components/ReactCodeMirror', () => ({
  __esModule: true,
  default: function MockCodeMirror(props) {
    return <div data-testid="code-mirror" data-mode={props.mode}>Editor</div>;
  },
}));

import {render, screen} from '@testing-library/react';
import {withTheme} from '../fake_theme';
import CodeEditor from 'sources/cdeadmin_ui/editors/CodeEditor';

describe('CDEadmin CodeEditor boundary', () => {
  it('provides labelled editor identity and forwards editor configuration', () => {
    const Component = withTheme(CodeEditor);
    render(<Component label="Firebird procedure source" mode="firebird" />);
    expect(screen.getByRole('region', {name: 'Firebird procedure source'}))
      .toBeInTheDocument();
    expect(screen.getByTestId('code-mirror')).toHaveAttribute('data-mode', 'firebird');
  });

  it('exposes independent loading and error states without removing the editor', () => {
    const Component = withTheme(CodeEditor);
    const {rerender} = render(<Component loading label="SQL editor" />);
    expect(screen.getByRole('region', {name: 'SQL editor'}))
      .toHaveAttribute('aria-busy', 'true');
    expect(screen.getByText('Loading editor')).toBeInTheDocument();
    rerender(<Component error="Parser unavailable" label="SQL editor" />);
    expect(screen.getByRole('alert')).toHaveTextContent('Parser unavailable');
    expect(screen.getByTestId('code-mirror')).toBeInTheDocument();
  });
});
