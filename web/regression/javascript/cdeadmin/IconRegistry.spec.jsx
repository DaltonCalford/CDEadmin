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
  listIconDefinitions,
  registerIconDefinition,
  resolveIconDefinition,
} from 'sources/cdeadmin_ui/icons/registry';

describe('CDEadmin semantic icon registry', () => {
  it('maps engine and object keys onto replaceable presentation assets', () => {
    expect(resolveIconDefinition('engine.firebird').className)
      .toBe('icon-engine-type-firebird');
    expect(resolveIconDefinition('object.collection').className)
      .toBe('icon-coll-table');
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
    </>);

    expect(screen.getByRole('img', {name: 'Connection warning'}))
      .toHaveAttribute('data-icon-key', 'status.warning');
    expect(document.querySelector('[data-icon-key="object.document"]'))
      .toHaveAttribute('aria-hidden', 'true');
  });
});
