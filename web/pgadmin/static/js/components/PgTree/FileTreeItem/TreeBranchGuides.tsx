/////////////////////////////////////////////////////////////
//
// CDEadmin - Multi-engine Database Administration
//
// Copyright (C) 2013 - 2026, The pgAdmin Development Team
// This software is released under the PostgreSQL Licence
//
//////////////////////////////////////////////////////////////

import {FileOrDir} from 'react-aspen';

export interface ITreeBranchSegment {
  kind: 'ancestor' | 'current'
  continues: boolean
  isLast: boolean
}

function siblingsOf(item: FileOrDir): FileOrDir[] {
  const siblings = item?.parent?.children;
  return Array.isArray(siblings) ? siblings : [];
}

export function isLastTreeSibling(item: FileOrDir): boolean {
  const siblings = siblingsOf(item);
  if(siblings.length === 0) {
    return true;
  }
  const last = siblings[siblings.length - 1];
  return last === item || (last?.id !== undefined && last.id === item?.id);
}

/**
 * Return one segment for each visible indentation level. Ancestor segments
 * carry vertical lines only while later siblings remain. The current segment
 * draws either a tee or an end elbow into the node's disclosure/leaf marker.
 */
export function treeBranchSegments(item: FileOrDir): ITreeBranchSegment[] {
  const visibleDepth = Math.max(0, Number(item?.depth ?? 0) - 1);
  if(visibleDepth === 0) {
    return [];
  }

  const ancestorSegments: ITreeBranchSegment[] = [];
  let ancestor = item.parent as FileOrDir;
  for(let index = 0; index < visibleDepth - 1 && ancestor; index += 1) {
    const continues = !isLastTreeSibling(ancestor);
    ancestorSegments.unshift({
      kind: 'ancestor',
      continues,
      isLast: !continues,
    });
    ancestor = ancestor.parent as FileOrDir;
  }

  const isLast = isLastTreeSibling(item);
  return [
    ...ancestorSegments,
    {kind: 'current', continues: !isLast, isLast},
  ];
}

export function TreeBranchGuides({item}: {item: FileOrDir}) {
  const segments = treeBranchSegments(item);
  return <span className="tree-branch" aria-hidden="true">
    {segments.map((segment, index) => <span
      key={`${segment.kind}-${index}`}
      className={[
        'tree-branch-segment',
        segment.kind,
        segment.continues ? 'continues' : '',
        segment.isLast ? 'is-last' : '',
      ].filter(Boolean).join(' ')}
    />)}
  </span>;
}
