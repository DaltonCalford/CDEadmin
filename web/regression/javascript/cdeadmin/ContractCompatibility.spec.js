/////////////////////////////////////////////////////////////
//
// CDEadmin - Multi-engine Database Administration
//
// Copyright (C) 2013 - 2026, The pgAdmin Development Team
// This software is released under the PostgreSQL Licence
//
//////////////////////////////////////////////////////////////

import {
  CONTRACT_VERSION,
  KNOWN_MODE_VALUES,
  KNOWN_MUTATION_CLASS_VALUES,
  KNOWN_PERMISSION_ID_VALUES,
  KNOWN_RESULT_KIND_VALUES,
} from '../../../pgadmin/cdeadmin/static/js/contracts/v1/generated';
import compatibility from '../../../../tools/cdeadmin_sdk_compatibility.json';


describe('CDEadmin provider contract compatibility', ()=>{
  it('matches the declared current and previous v1 matrix', ()=>{
    const versions = compatibility.contract_versions;
    const current = versions.find((item)=>item.role === 'current');
    const previous = versions.find((item)=>item.role === 'previous');

    expect(CONTRACT_VERSION).toBe('1.1.0');
    expect(current).toEqual({
      contract_version: CONTRACT_VERSION,
      role: 'current',
      expected: 'compatible',
    });
    expect(previous).toEqual({
      contract_version: '1.0.0',
      role: 'previous',
      expected: 'compatible',
    });
  });

  it('retains the engine-neutral open-enum presentation surface', ()=>{
    expect(KNOWN_MODE_VALUES).toEqual([
      'legacy_native',
      'scratchbird_native',
    ]);
    expect(KNOWN_MODE_VALUES).not.toContain('scratchbird_emulated_legacy');
    expect(KNOWN_MUTATION_CLASS_VALUES).toContain('destructive');
    expect(KNOWN_PERMISSION_ID_VALUES).toContain('secret_read');
    expect(KNOWN_RESULT_KIND_VALUES).toEqual(expect.arrayContaining([
      'tabular', 'document', 'graph', 'columnar', 'wide_column', 'cellset',
      'vector',
    ]));
  });
});
