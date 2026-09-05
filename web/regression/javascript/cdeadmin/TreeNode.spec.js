/////////////////////////////////////////////////////////////
//
// CDEadmin - Multi-engine Database Administration
//
// Copyright (C) 2013 - 2026, The pgAdmin Development Team
// This software is released under the PostgreSQL Licence
//
//////////////////////////////////////////////////////////////

import {
  createTreeNodeDescriptor,
  descriptorFromTreeMetadata,
  treeNodeAriaLabel,
} from 'sources/cdeadmin_ui/navigation/TreeNode';

describe('CDEadmin tree-node contract', () => {
  it('normalizes provider-specific metadata without losing identity', () => {
    const node = descriptorFromTreeMetadata({
      id: 'firebird:server:local',
      _label: 'Local Firebird',
      _type: 'server',
      provider_id: 'firebird',
      engine_id: 'firebird',
      engine_family: 'relational',
      connection_status: 'connected',
      info_label: 'Firebird 5.0',
      icon_key: 'engine.firebird',
      expandable: true,
    }, 3);

    expect(node).toEqual(expect.objectContaining({
      id: 'firebird:server:local',
      label: 'Local Firebird',
      kind: 'server',
      providerId: 'firebird',
      engineId: 'firebird',
      engineFamily: 'relational',
      iconKey: 'engine.firebird',
      childCount: 3,
    }));
    expect(node.capabilities.expandable).toBe(true);
    expect(treeNodeAriaLabel(node)).toBe(
      'Local Firebird, server, Firebird 5.0, connected, 3 children');
  });

  it('maps engine roots and preserves non-relational object types', () => {
    const engine = descriptorFromTreeMetadata({
      id: 'engine:mongodb',
      _label: 'MongoDB',
      _type: 'engine_type',
    });
    const collection = descriptorFromTreeMetadata({
      id: 'collection:events',
      _label: 'events',
      _type: 'collection',
      provider_id: 'mongodb',
    });

    expect(engine.kind).toBe('engine');
    expect(collection.kind).toBe('object');
    expect(collection.objectType).toBe('collection');
  });

  it('rejects anonymous public descriptors', () => {
    expect(() => createTreeNodeDescriptor({label: 'Missing id'}))
      .toThrow('Tree node id is required');
    expect(() => createTreeNodeDescriptor({id: 'missing-label'}))
      .toThrow('Tree node label is required');
  });

  it('freezes descriptors and capability declarations', () => {
    const node = createTreeNodeDescriptor({
      id: 'redis:key:queue',
      label: 'queue',
      objectType: 'stream',
      providerId: 'redis',
      capabilities: {editable: true, removable: true},
    });

    expect(Object.isFrozen(node)).toBe(true);
    expect(Object.isFrozen(node.capabilities)).toBe(true);
    expect(node.capabilities.editable).toBe(true);
  });
});
