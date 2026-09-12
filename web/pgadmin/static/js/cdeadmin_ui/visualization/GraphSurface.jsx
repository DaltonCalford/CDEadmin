/////////////////////////////////////////////////////////////
// Accessible bounded graph visualization boundary.
/////////////////////////////////////////////////////////////

import {useMemo, useRef} from 'react';
import PropTypes from 'prop-types';
import {Box} from '@mui/material';
import DataGrid from '../data/DataGrid';
import {EmptyState} from '../feedback/EmptyState';

function layout(nodes) {
  const groups = new Map();
  nodes.forEach((node, index) => {
    const level = Number.isInteger(node.level) ? node.level : Math.floor(index / 8);
    const values = groups.get(level) ?? []; values.push(node); groups.set(level, values);
  });
  const positions = new Map();
  [...groups.entries()].sort(([a], [b]) => a - b).forEach(([level, values]) => {
    values.forEach((node, index) => {
      const supplied = node.position;
      positions.set(node.id, supplied && Number.isFinite(Number(supplied.x)) &&
        Number.isFinite(Number(supplied.y)) ? {
          x: Math.max(0, Math.min(10000, Number(supplied.x))),
          y: Math.max(0, Math.min(10000, Number(supplied.y))),
        } : {x: 24 + level * 220, y: 24 + index * 72});
    });
  });
  return positions;
}

export default function GraphSurface({nodes=[], edges=[], mode='graph', selectedId,
  selectedEdgeId, onSelect, onSelectEdge, onPositionChange,
  label='Relationship graph', overBudget=false}) {
  const positions = useMemo(() => layout(nodes), [nodes]);
  const dragStarts = useRef(new Map());
  if(!nodes.length) return <EmptyState message="No graph data is available." />;
  if(mode === 'table') {
    const rows = [...nodes.map((node) => ({...node, recordType: 'node',
      referenceText: node.reference?.canonical ?? node.reference?.id ?? '',
      selected: node.id === selectedId ? 'Selected' : ''})),
    ...edges.map((edge) => ({...edge, recordType: 'edge', name: `${edge.from} → ${edge.to}`,
      kind: edge.type, namespace: 'edge', referenceText: edge.id,
      selected: edge.id === selectedEdgeId ? 'Selected' : ''}))];
    return <DataGrid gridId="cdeadmin/graph-surface" aria-label={`${label} table`}
      columns={[{key: 'name', name: 'Name'}, {key: 'kind', name: 'Kind'},
        {key: 'namespace', name: 'Namespace'}, {key: 'referenceText', name: 'Reference'},
        {key: 'selected', name: 'Selection'}]} rows={rows} readOnly enableRowSelect
      rowKeyGetter={(row) => `${row.recordType}:${row.id}`} onItemSelect={(row) => {
        if(!row) return;
        row.recordType === 'edge' ? onSelectEdge?.(row.id) : onSelect?.(row.id);
      }} />;
  }
  const width = Math.max(760, ...[...positions.values()].map((item) => item.x + 200));
  const height = Math.max(480, ...[...positions.values()].map((item) => item.y + 60));
  return <Box role="group" aria-label={label} sx={{height: '100%', overflow: 'auto'}}>
    {overBudget && <Box role="status" sx={{p: 1}}>
      Graph budget reached; a clustered progressive subset is displayed.
    </Box>}
    <Box component="svg" viewBox={`0 0 ${width} ${height}`} width={width} height={height}
      role="img" aria-label={`${label}: ${nodes.length} nodes and ${edges.length} edges`}>
      <title>{label}</title>
      {edges.map((edge) => {
        const from = positions.get(edge.from); const to = positions.get(edge.to);
        if(!from || !to) return null;
        const selected = edge.id === selectedEdgeId;
        const select = () => onSelectEdge?.(edge.id);
        return <g key={edge.id} role="button" tabIndex="0"
          aria-label={`${edge.type} edge from ${edge.from} to ${edge.to}`}
          onClick={select} onKeyDown={(event) => {
            if(event.key === 'Enter' || event.key === ' ') {
              event.preventDefault(); select();
            }
          }}>
          <line x1={from.x + 164} y1={from.y + 22} x2={to.x} y2={to.y + 22}
            stroke={selected ? 'var(--cde-color-focus)' : 'currentColor'}
            strokeWidth={selected ? 4 : 1.5} />
          <text x={(from.x + to.x + 164) / 2} y={(from.y + to.y + 44) / 2 - 4}
            fill="currentColor" fontSize="10" textAnchor="middle">{edge.type}</text>
        </g>;
      })}
      {nodes.map((node) => {
        const point = positions.get(node.id); const selected = node.id === selectedId;
        return <g key={node.id} role="button" tabIndex="0"
          draggable={Boolean(onPositionChange)}
          aria-label={`${node.name}, ${node.kind}, ${node.namespace}`}
          onDragStart={(event) => {
            const x = Number(event.clientX); const y = Number(event.clientY);
            dragStarts.current.set(node.id, {
              x: Number.isFinite(x) ? x : 0, y: Number.isFinite(y) ? y : 0,
            });
            event.dataTransfer?.setData('application/x-cdeadmin-graph-node', node.id);
          }}
          onDragEnd={(event) => {
            const start = dragStarts.current.get(node.id); dragStarts.current.delete(node.id);
            if(!start || !onPositionChange) return;
            const bounds = event.currentTarget.ownerSVGElement?.getBoundingClientRect();
            const scaleX = bounds?.width ? width / bounds.width : 1;
            const scaleY = bounds?.height ? height / bounds.height : 1;
            const endX = Number(event.clientX); const endY = Number(event.clientY);
            onPositionChange(node.id, {x: Math.max(0, Math.min(10000,
              point.x + ((Number.isFinite(endX) ? endX : start.x) - start.x) * scaleX)),
            y: Math.max(0, Math.min(10000,
              point.y + ((Number.isFinite(endY) ? endY : start.y) - start.y) * scaleY))});
          }}
          onClick={() => onSelect?.(node.id)} onKeyDown={(event) => {
            if(event.key === 'Enter' || event.key === ' ') {
              event.preventDefault(); onSelect?.(node.id);
            } else if(onPositionChange && ['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown']
              .includes(event.key)) {
              event.preventDefault(); const step = event.shiftKey ? 40 : 8;
              const delta = {ArrowLeft: [-step, 0], ArrowRight: [step, 0],
                ArrowUp: [0, -step], ArrowDown: [0, step]}[event.key];
              onPositionChange(node.id, {x: Math.max(0, Math.min(10000, point.x + delta[0])),
                y: Math.max(0, Math.min(10000, point.y + delta[1]))});
            }
          }}>
          <rect x={point.x} y={point.y} width="164" height="44" rx="0"
            fill={selected ? 'var(--cde-color-selection)' : 'var(--cde-color-surface-raised)'}
            stroke={selected ? 'var(--cde-color-focus)' : 'currentColor'}
            strokeWidth={selected ? 3 : 1} />
          <text x={point.x + 8} y={point.y + 18} fill="currentColor" fontSize="12">
            {String(node.name).slice(0, 22)}
          </text>
          <text x={point.x + 8} y={point.y + 34} fill="currentColor" fontSize="9">
            {node.kind} · {node.namespace}
          </text>
        </g>;
      })}
    </Box>
  </Box>;
}

GraphSurface.propTypes = {
  nodes: PropTypes.array, edges: PropTypes.array,
  mode: PropTypes.oneOf(['graph', 'table']), selectedId: PropTypes.string,
  selectedEdgeId: PropTypes.string, onSelect: PropTypes.func,
  onSelectEdge: PropTypes.func, onPositionChange: PropTypes.func,
  label: PropTypes.string, overBudget: PropTypes.bool,
};
