/////////////////////////////////////////////////////////////
//
// CDEadmin - Multi-engine Database Administration
//
// Copyright (C) 2013 - 2026, The pgAdmin Development Team
// This software is released under the PostgreSQL Licence
//
//////////////////////////////////////////////////////////////

import {render, screen} from '@testing-library/react';
import {Icon, ObjectIcon} from 'sources/cdeadmin_ui/icons';
import {
  ICON_CATEGORIES,
  createIconAssignmentDocument,
  inferActionIconKey,
  mergeIconAssignments,
  normalizeIconAssignments,
  listIconDefinitions,
  registerIconDefinition,
  resolveIconDefinition,
} from 'sources/cdeadmin_ui/icons/registry';

describe('CDEadmin semantic icon registry', () => {
  it.each(['shadow', 'storage-file'])('resolves the native %s object icon', (kind) => {
    const canonical = 'object.' + kind.replaceAll('-', '_');
    expect(resolveIconDefinition('object.' + kind)).toEqual(
      expect.objectContaining({key: canonical, kind: 'component'}));
    expect(resolveIconDefinition('object.' + kind, {assignments: {
      [canonical]: 'action.search',
    }}).key).toBe('action.search');
  });
  it('normalizes provider object kinds while honoring icon customization', () => {
    expect(resolveIconDefinition('object.blob-filter').key).toBe('object.blob_filter');
    expect(resolveIconDefinition('object.blob-filter', {assignments: {
      'object.blob_filter': 'action.search',
    }}).key).toBe('action.search');
    expect(resolveIconDefinition('object.blob-filter', {assignments: {
      'object.blob-filter': 'action.refresh', 'object.blob_filter': 'action.search',
    }}).key).toBe('action.refresh');
    expect(resolveIconDefinition('object.blob-filter', {assignments: {
      'object.blob_filter': 'object.blob-filter',
    }}).key).toBe('object.blob_filter');
  });

  it('preserves exact provider registrations before object-kind normalization', () => {
    const removeCanonical = registerIconDefinition({key: 'object.owned_filter',
      category: ICON_CATEGORIES.OBJECT, kind: 'class', className: 'owned-canonical'});
    const removeExact = registerIconDefinition({key: 'object.owned-filter',
      category: ICON_CATEGORIES.OBJECT, kind: 'class', className: 'owned-exact'});
    try {
      expect(resolveIconDefinition('object.owned-filter').className).toBe('owned-exact');
      removeExact();
      expect(resolveIconDefinition('object.owned-filter').className).toBe('owned-canonical');
    } finally {
      removeExact();
      removeCanonical();
    }
  });

  it('maps engine and object keys onto replaceable presentation assets', () => {
    expect(resolveIconDefinition('engine.firebird').className)
      .toBe('icon-engine-type-firebird');
    expect(resolveIconDefinition('object.collection').className)
      .toBe('icon-coll-table');
  });

  it('maps command actions onto attributed theme-aware SVG components', () => {
    expect(resolveIconDefinition('action.connect')).toEqual(
      expect.objectContaining({
        kind: 'component',
        license: 'MIT',
        attribution: 'Hugeicons Free Icons 4.3.0',
      })
    );
    expect(resolveIconDefinition('command.default').kind).toBe('component');
    expect(resolveIconDefinition('action.provider_specific').key)
      .toBe('command.default');
  });

  it('validates layered semantic assignments and rejects cycles safely', () => {
    const organization = {'tool.data-explorer': 'action.search'};
    const user = createIconAssignmentDocument({
      'tool.data-explorer': 'action.refresh',
      'tool.query': 'tool.query',
      '<unsafe>': 'action.delete',
    });
    const assignments = mergeIconAssignments(organization, user);
    expect(normalizeIconAssignments(user)).toEqual({
      'tool.data-explorer': 'action.refresh',
    });
    expect(resolveIconDefinition('tool.data-explorer', {assignments}).key)
      .toBe('action.refresh');
    expect(resolveIconDefinition('tool.query', {assignments: {
      'tool.query': 'action.search', 'action.search': 'tool.query',
    }}).key).toBe('tool.query');
  });

  it('provides concrete icons for every activity-tab icon contract', () => {
    const keys = ['tool.data-explorer', 'tool.project-explorer', 'tool.erd',
      'tool.schema-compare', 'tool.lineage', 'tool.quality', 'tool.contract',
      'tool.etl', 'tool.cdc', 'tool.replication', 'tool.tracing',
      'tool.migration', 'tool.api', 'tool.ml-vector', 'tool.ai', 'tool.search',
      'tool.query', 'tool.scratchrobin'];
    for(const key of keys) {
      expect(resolveIconDefinition(key)).toEqual(expect.objectContaining({
        key, category: ICON_CATEGORIES.TOOL,
      }));
    }
  });

  it('infers specific command actions before ambiguous general actions', () => {
    expect(inferActionIconKey({label: 'Disconnect server'}))
      .toBe('action.disconnect');
    expect(inferActionIconKey({label: 'Create database'}))
      .toBe('action.create_database');
    expect(inferActionIconKey({name: 'restore_backup'}))
      .toBe('action.restore');
    expect(inferActionIconKey({
      label: 'Disconnect server',
      iconKey: 'action.cancel',
    })).toBe('action.cancel');
  });

  it('falls native taxonomy variants back to their correct object family', () => {
    expect(resolveIconDefinition('access.index.vector.hnsw').key)
      .toBe('object.index');
    expect(resolveIconDefinition('vector.collection.partitioned').key)
      .toBe('object.vector_collection');
    expect(resolveIconDefinition('timeseries.measurement.gauge').key)
      .toBe('object.measurement');
  });

  it('supports collision-checked provider definitions with provenance', () => {
    const unregister = registerIconDefinition({
      key: 'object.test_native_kind',
      category: ICON_CATEGORIES.OBJECT,
      className: 'icon-test-native-kind',
      label: 'Test native kind',
      providerId: 'org.cdeadmin.test',
      license: 'MIT',
      attribution: 'Test asset',
    });
    expect(resolveIconDefinition('object.test_native_kind')).toEqual(
      expect.objectContaining({providerId: 'org.cdeadmin.test', license: 'MIT'})
    );
    expect(()=>registerIconDefinition({
      key: 'object.test_native_kind',
      className: 'icon-duplicate',
    })).toThrow('already registered');
    unregister();
    expect(listIconDefinitions().some(
      (item)=>item.key === 'object.test_native_kind'
    )).toBe(false);
  });

  it('rejects executable or unreviewed SVG URL schemes', () => {
    expect(()=>registerIconDefinition({
      key: 'object.unsafe_svg',
      svgUrl: 'javascript:alert(1)',
    })).toThrow('application or HTTPS URL');
  });

  it('renders accessible named icons and decorative tree icons', () => {
    render(<>
      <Icon iconKey="status.warning" label="Connection warning" />
      <ObjectIcon objectType="document" decorative />
      <Icon iconKey="action.refresh" decorative />
    </>);

    expect(screen.getByRole('img', {name: 'Connection warning'}))
      .toHaveAttribute('data-icon-key', 'status.warning');
    expect(document.querySelector('[data-icon-key="object.document"]'))
      .toHaveAttribute('aria-hidden', 'true');
    const actionIcon = document.querySelector(
      '[data-icon-key="action.refresh"]'
    );
    expect(actionIcon?.tagName).toBe('svg');
    expect(actionIcon).toHaveStyle({width: '1em', height: '1em'});
  });
});
