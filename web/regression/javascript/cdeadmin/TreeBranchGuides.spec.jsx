/////////////////////////////////////////////////////////////
//
// CDEadmin - Multi-engine Database Administration
//
// Copyright (C) 2013 - 2026, The pgAdmin Development Team
// This software is released under the PostgreSQL Licence
//
//////////////////////////////////////////////////////////////

import {render} from '@testing-library/react';
import {
  TreeBranchGuides,
  isLastTreeSibling,
  treeBranchSegments,
} from 'sources/components/PgTree/FileTreeItem/TreeBranchGuides';

function node(id, depth, parent=null) {
  return {id, depth, parent, children: []};
}

describe('CDEadmin branching tree guides', () => {
  it('draws tees, end elbows, and continuing ancestor lines', () => {
    const hiddenRoot = node('hidden', 0);
    const connectors = node('connectors', 1, hiddenRoot);
    const projects = node('projects', 1, hiddenRoot);
    hiddenRoot.children = [connectors, projects];
    const firebird = node('firebird', 2, connectors);
    const mysql = node('mysql', 2, connectors);
    connectors.children = [firebird, mysql];
    const local = node('local', 3, firebird);
    firebird.children = [local];

    expect(isLastTreeSibling(firebird)).toBe(false);
    expect(isLastTreeSibling(mysql)).toBe(true);
    expect(treeBranchSegments(firebird)).toEqual([
      {kind: 'current', continues: true, isLast: false},
    ]);
    expect(treeBranchSegments(local)).toEqual([
      {kind: 'ancestor', continues: true, isLast: false},
      {kind: 'current', continues: false, isLast: true},
    ]);
  });

  it('renders one non-textual segment per visible branch depth', () => {
    const root = node('root', 0);
    const parent = node('parent', 1, root);
    root.children = [parent];
    const child = node('child', 2, parent);
    parent.children = [child];

    const {container} = render(<TreeBranchGuides item={child} />);
    expect(container.querySelector('.tree-branch'))
      .toHaveAttribute('aria-hidden', 'true');
    expect(container.querySelectorAll('.tree-branch-segment')).toHaveLength(1);
    expect(container.querySelector('.tree-branch-segment.current.is-last'))
      .toBeInTheDocument();
  });
});
