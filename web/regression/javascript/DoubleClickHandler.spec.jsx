import {act, fireEvent, render, screen} from '@testing-library/react';
import DoubleClickHandler from '../../pgadmin/static/js/components/PgTree/FileTreeItem/DoubleClickHandler';

describe('tree click and context-menu ownership', () => {
  beforeEach(() => jest.useFakeTimers());
  afterEach(() => jest.useRealTimers());

  function setup() {
    const single = jest.fn();
    const double = jest.fn();
    const context = jest.fn();
    const rendered = render(<DoubleClickHandler onSingleClick={single} onDoubleClick={double}>
      <div role="treeitem" onContextMenu={context}>Owned tree object</div>
    </DoubleClickHandler>);
    return {...rendered, single, double, context, item: screen.getByRole('treeitem')};
  }

  it('dispatches one delayed single click', () => {
    const view = setup();
    fireEvent.click(view.item);
    act(() => jest.advanceTimersByTime(249));
    expect(view.single).not.toHaveBeenCalled();
    act(() => jest.advanceTimersByTime(1));
    expect(view.single).toHaveBeenCalledTimes(1);
    expect(view.double).not.toHaveBeenCalled();
  });

  it('dispatches a double click without a subsequent single action', () => {
    const view = setup();
    fireEvent.click(view.item);
    act(() => jest.advanceTimersByTime(100));
    fireEvent.click(view.item);
    act(() => jest.advanceTimersByTime(500));
    expect(view.double).toHaveBeenCalledTimes(1);
    expect(view.single).not.toHaveBeenCalled();
  });

  it.each([1, 2])('cancels %s queued clicks when the context menu is requested', (count) => {
    const view = setup();
    for(let i = 0; i < count; i++) fireEvent.click(view.item);
    fireEvent.contextMenu(view.item);
    expect(view.context).toHaveBeenCalledTimes(1);
    act(() => jest.advanceTimersByTime(500));
    expect(view.single).not.toHaveBeenCalled();
    expect(view.double).not.toHaveBeenCalled();
    fireEvent.click(view.item);
    act(() => jest.advanceTimersByTime(250));
    expect(view.single).toHaveBeenCalledTimes(1);
  });

  it('does not dispatch an old node action after unmount', () => {
    const view = setup();
    fireEvent.click(view.item);
    view.unmount();
    act(() => jest.advanceTimersByTime(500));
    expect(view.single).not.toHaveBeenCalled();
    expect(view.double).not.toHaveBeenCalled();
  });
});
