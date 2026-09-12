/////////////////////////////////////////////////////////////
// Accessible graph/table visualization behavior gates.
/////////////////////////////////////////////////////////////

import {createEvent, fireEvent, render, screen} from '@testing-library/react';
import {withTheme} from '../fake_theme';
import {GraphSurface} from 'sources/cdeadmin_ui';

const nodes = [{id: 'source', name: 'Source', kind: 'table', namespace: 'demo',
  reference: {id: 'source'}}, {id: 'target', name: 'Target', kind: 'view',
  namespace: 'demo', reference: {id: 'target'}}];
const edges = [{id: 'edge', from: 'source', to: 'target', type: 'derives'}];

describe('GraphSurface', () => {
  const Component = withTheme(GraphSurface);

  it('renders a keyboard-selectable bounded SVG graph with textual identity', () => {
    const select = jest.fn(); const selectEdge = jest.fn();
    render(<Component nodes={nodes} edges={edges} selectedId="source"
      onSelect={select} onSelectEdge={selectEdge} label="Lineage graph" />);
    expect(screen.getByRole('img', {name: /2 nodes and 1 edges/})).toBeVisible();
    const target = screen.getByRole('button', {name: /Target, view, demo/});
    fireEvent.keyDown(target, {key: 'Enter'}); expect(select).toHaveBeenCalledWith('target');
    fireEvent.click(screen.getByRole('button', {name: /derives edge from source to target/}));
    expect(selectEdge).toHaveBeenCalledWith('edge');
    expect(screen.getByText('derives')).toBeVisible();
  });

  it('honors bounded saved visual positions independently of graph levels', () => {
    render(<Component nodes={[{...nodes[0], level: 7, position: {x: 410, y: 205}}, nodes[1]]}
      edges={edges} />);
    const selected = screen.getByRole('button', {name: /Source, table, demo/});
    expect(selected.querySelector('rect')).toHaveAttribute('x', '410');
    expect(selected.querySelector('rect')).toHaveAttribute('y', '205');
  });

  it('moves visual position only through pointer and keyboard alternatives', () => {
    const move = jest.fn();
    render(<Component nodes={nodes} edges={edges} onPositionChange={move} />);
    const source = screen.getByRole('button', {name: /Source, table, demo/});
    fireEvent.keyDown(source, {key: 'ArrowRight'});
    expect(move).toHaveBeenCalledWith('source', {x: 32, y: 24});
    const start = createEvent.dragStart(source, {dataTransfer: {setData: jest.fn()}});
    Object.defineProperties(start, {clientX: {value: 10}, clientY: {value: 10}});
    fireEvent(source, start);
    const end = createEvent.dragEnd(source);
    Object.defineProperties(end, {clientX: {value: 30}, clientY: {value: 50}});
    fireEvent(source, end);
    expect(move).toHaveBeenLastCalledWith('source', {x: 44, y: 64});
  });

  it('provides a data-grid alternative and over-budget state in text', () => {
    const select = jest.fn(); const selectEdge = jest.fn();
    const {rerender} = render(<Component nodes={nodes} edges={edges} mode="table"
      onSelect={select} onSelectEdge={selectEdge} label="Lineage graph" />);
    expect(screen.getByLabelText('Lineage graph table')).toBeVisible();
    expect(screen.getByText('source → target')).toBeVisible();
    rerender(<Component nodes={nodes} edges={edges} overBudget label="Lineage graph" />);
    expect(screen.getByRole('status')).toHaveTextContent('clustered progressive subset');
  });

  it('renders a useful empty state rather than a blank canvas', () => {
    render(<Component nodes={[]} edges={[]} />);
    expect(screen.getByText('No graph data is available.')).toBeVisible();
  });
});
