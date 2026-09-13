/////////////////////////////////////////////////////////////
//
// CDEadmin - Multi-engine Database Administration
//
// Copyright (C) 2013 - 2026, The pgAdmin Development Team
// This software is released under the PostgreSQL Licence
//
//////////////////////////////////////////////////////////////

import {fireEvent, render, screen} from '@testing-library/react';
import {FileType, ItemType} from 'react-aspen';

import {FileTreeItem} from
  'sources/components/PgTree/FileTreeItem';
import {FileTreeX} from 'sources/components/PgTree/FileTreeX';
import {FileTreeXEvent} from 'sources/components/PgTree/types';
import pgAdmin from 'sources/pgadmin';
import {manageTreeEvents} from 'sources/tree/tree';

describe('CDEadmin tree expander', () => {
  it('opens provider objects on activation without toggling their branch', async () => {
    const tree = new FileTreeX({model: {root: {}}, onEvent: jest.fn()});
    tree.setActiveFile = jest.fn().mockResolvedValue();
    tree.toggleDirectory = jest.fn();
    const dispatch = jest.spyOn(tree.events, 'dispatch');
    const item = {_metadata: {data: {_type: 'cde_resource'}}};
    const event = {};
    await tree.handleItemDoubleClicked(event, item);
    expect(tree.setActiveFile).toHaveBeenCalledWith(item);
    expect(dispatch).toHaveBeenCalledWith(FileTreeXEvent.onTreeEvents, event, 'activated', item);
    expect(tree.toggleDirectory).not.toHaveBeenCalled();
  });
  it('dispatches a directory toggle immediately and only once', () => {
    const data = {
      id: 'engine_type_firebird',
      _id: 'firebird',
      _type: 'engine_type',
      _label: 'Firebird',
      label: 'Firebird',
      inode: true,
      icon_key: 'engine.firebird',
    };
    const item = {
      id: 7,
      depth: 2,
      expanded: false,
      children: [],
      parent: {parent: null, path: '/browser', children: []},
      _metadata: {data},
      getMetadata: () => data,
    };
    item.parent.children.push(item);
    const onClick = jest.fn();

    render(<FileTreeItem
      item={item}
      itemType={ItemType.Directory}
      decorations={{
        classlist: [],
        addChangeListener: jest.fn(),
        removeChangeListener: jest.fn(),
      }}
      onClick={onClick}
      onDoubleClick={jest.fn()}
      onContextMenu={jest.fn()}
      onMouseEnter={jest.fn()}
      onMouseLeave={jest.fn()}
      changeDirectoryCount={jest.fn()}
      events={{dispatch: jest.fn()}}
    />);

    fireEvent.click(screen.getByRole('button', {
      name: 'Expand Firebird',
    }));

    expect(onClick).toHaveBeenCalledTimes(1);
    expect(onClick.mock.calls[0][1]).toBe(item);
    expect(onClick.mock.calls[0][2]).toBe(ItemType.Directory);
  });

  it('does not open a directory when beforeopen refuses it', async () => {
    const tree = new FileTreeX({});
    const openDirectory = jest.fn();
    tree.fileTreeHandle = {openDirectory};
    tree.events.add(FileTreeXEvent.onTreeEvents,
      (_event, eventName) => eventName !== 'beforeopen');
    const directory = {
      id: 10,
      type: FileType.Directory,
      expanded: false,
    };

    await tree.toggleDirectory(directory);

    expect(openDirectory).not.toHaveBeenCalled();
  });

  it('propagates a provider node beforeopen refusal through the tree bridge', () => {
    const previousBrowser = pgAdmin.Browser;
    const beforeopen = jest.fn(() => false);
    const data = {_type: 'cde_database_target', id: 'database-1'};
    const item = {
      parent: {path: '/browser/server-1'},
      _metadata: {data},
      getMetadata: () => data,
    };
    pgAdmin.Browser = {
      Nodes: {cde_database_target: {callbacks: {beforeopen}}},
      tree: {addNewNode: jest.fn()},
      Events: {trigger: jest.fn()},
    };

    try {
      expect(manageTreeEvents({}, 'beforeopen', item)).toBe(false);
      expect(beforeopen).toHaveBeenCalledWith(
        item, data, pgAdmin.Browser, [], 'beforeopen'
      );
    } finally {
      pgAdmin.Browser = previousBrowser;
    }
  });
});
