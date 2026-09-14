import {fireEvent, render, screen} from '@testing-library/react';
import {withTheme} from '../fake_theme';
import {Splitter} from 'sources/cdeadmin_ui/layout/WorkbenchChrome';

describe('Workbench splitter', () => {
  it.each(['vertical', 'horizontal'])('converts the numeric hit-size token for %s layout', (orientation) => {
    const Component = withTheme(Splitter);
    render(<Component value={320} orientation={orientation} />);
    const splitter = screen.getByRole('separator');
    expect(splitter).toHaveStyle(orientation === 'vertical' ? {
      width: 'calc(var(--cde-resize-handle-size, 8) * 1px)', height: '100%',
    } : {height: 'calc(var(--cde-resize-handle-size, 8) * 1px)', width: '100%'});
    expect(splitter).toHaveAttribute('tabindex', '0');
    expect(splitter).toHaveAttribute('aria-orientation', orientation);
  });

  it.each([
    ['vertical', 'ArrowLeft', 316], ['vertical', 'ArrowRight', 324],
    ['horizontal', 'ArrowUp', 316], ['horizontal', 'ArrowDown', 324],
  ])('supports the %s %s keyboard direction', (orientation, key, expected) => {
    const change = jest.fn();
    const Component = withTheme(Splitter);
    render(<Component value={320} min={280} max={520}
      orientation={orientation} onChange={change} />);
    fireEvent.keyDown(screen.getByRole('separator'), {key});
    expect(change).toHaveBeenCalledWith(expected);
  });

  it('clamps larger keyboard steps and ignores unrelated keys', () => {
    const change = jest.fn();
    const Component = withTheme(Splitter);
    render(<Component value={284} min={280} max={288} onChange={change} />);
    const splitter = screen.getByRole('separator');
    fireEvent.keyDown(splitter, {key: 'ArrowLeft', shiftKey: true});
    fireEvent.keyDown(splitter, {key: 'ArrowRight', shiftKey: true});
    fireEvent.keyDown(splitter, {key: 'Tab'});
    expect(change.mock.calls).toEqual([[280], [288]]);
  });
});
