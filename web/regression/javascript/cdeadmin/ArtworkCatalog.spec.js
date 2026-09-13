/////////////////////////////////////////////////////////////
// Canonical ScratchRobin artwork catalog integrity gates.
/////////////////////////////////////////////////////////////

import fs from 'fs';
import path from 'path';
import catalog from '../../../pgadmin/static/assets/cdeadmin/manifest.json';
import {ENGINE_IDS, listIconDefinitions} from
  'sources/cdeadmin_ui/icons/registry';

const ROOT = path.resolve(
  __dirname, '../../../pgadmin/static/assets/cdeadmin'
);
const PGADMIN_ROOT = path.resolve(__dirname, '../../../pgadmin');
const ARTWORK_EXTENSIONS = new Set([
  '.gif', '.jpeg', '.jpg', '.png', '.svg', '.webp',
]);

function authoredRuntimeArtwork(directory, found=[]) {
  for(const entry of fs.readdirSync(directory, {withFileTypes: true})) {
    const item = path.join(directory, entry.name);
    const relative = path.relative(PGADMIN_ROOT, item).replaceAll('\\', '/');
    if(entry.isDirectory()) {
      if(relative === 'static/assets/cdeadmin' ||
          relative.startsWith('static/js/generated') ||
          relative.startsWith('static/js/cdeadmin_ui/specifications')) continue;
      authoredRuntimeArtwork(item, found);
    } else if((relative.startsWith('static/') || relative.includes('/static/')) &&
        ARTWORK_EXTENSIONS.has(path.extname(entry.name).toLowerCase())) {
      found.push(relative);
    }
  }
  return found;
}

describe('CDEadmin artwork catalog', () => {
  it('owns the versioned canonical artwork collections', () => {
    expect(catalog).toEqual(expect.objectContaining({
      schema: 'cdeadmin.artwork-catalog.v1',
      root: '/static/assets/cdeadmin',
      assignment_contract: 'cdeadmin.icon-assignments.v1',
    }));
    for(const collection of ['branding', 'engines', 'commands',
      'authentication', 'objects', 'explain', 'controls', 'themes',
      'backgrounds', 'tools', 'profiles']) {
      expect(fs.statSync(path.join(
        ROOT, catalog.collections[collection].path
      )).isDirectory()).toBe(true);
    }
  });

  it('contains artwork and a semantic definition for every engine', () => {
    const definitions = new Set(listIconDefinitions().map((item)=>item.key));
    for(const engine of ENGINE_IDS) {
      expect(fs.existsSync(path.join(ROOT, 'engines', `${engine}.svg`)))
        .toBe(true);
      expect(definitions.has(`engine.${engine}`)).toBe(true);
    }
  });

  it('keeps product and authentication artwork in the catalog', () => {
    expect(fs.existsSync(path.join(
      ROOT, 'branding', 'scratchrobincde.svg'
    ))).toBe(true);
    expect(fs.existsSync(path.join(ROOT, 'auth', 'login.svg'))).toBe(true);
    expect(fs.existsSync(path.join(ROOT, 'commands', 'HUGEICONS_LICENSE.md')))
      .toBe(true);
    expect(JSON.parse(fs.readFileSync(path.join(
      ROOT, 'profiles', 'cdeadmin-standard.json'
    ))).schema).toBe('cdeadmin.interface-profile.v1');
  });

  it('keeps authored runtime artwork out of feature-local static folders', () => {
    expect(authoredRuntimeArtwork(PGADMIN_ROOT)).toEqual([]);
    expect(fs.readdirSync(path.join(ROOT, 'objects'))).toHaveLength(147);
    expect(fs.readdirSync(path.join(ROOT, 'explain'))).toHaveLength(57);
  });
});
