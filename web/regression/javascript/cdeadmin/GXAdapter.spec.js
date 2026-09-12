/////////////////////////////////////////////////////////////
// Loss-aware Great Expectations interoperability gates.
/////////////////////////////////////////////////////////////

import {exportGXSuite, GX_PROFILE, importGXSuite} from 'sources/cdeadmin_ui/modules/quality';

const suite = (expectations=[]) => ({expectation_suite_name: 'orders', expectations,
  meta: {great_expectations_version: '1.7.1', owner: 'quality-team'}});
const expectation = (type, kwargs={column: 'ID'}, meta={severity: 'critical'}) => ({
  expectation_type: type, kwargs, meta});

describe('Great Expectations Data Quality interoperability', () => {
  it('maps every supported expectation family with source version and provenance', () => {
    const sourceTypes = ['expect_column_values_to_not_be_null',
      'expect_column_values_to_be_unique', 'expect_column_values_to_be_in_set',
      'expect_column_values_to_match_regex', 'expect_column_values_to_be_between',
      'expect_table_row_count_to_be_between'];
    const imported = importGXSuite(suite(sourceTypes.map((type) => expectation(type))));
    expect(imported).toMatchObject({profile: GX_PROFILE, sourceVersion: '1.7.1',
      provenance: {suiteName: 'orders', sourceMeta: {owner: 'quality-team'}}});
    expect(imported.content.rules.map((rule) => rule.type)).toEqual([
      'not_null', 'uniqueness', 'accepted_values', 'regex/pattern', 'range', 'row_count']);
  });

  it('preserves unsupported expectations losslessly instead of guessing mappings', () => {
    const unknown = expectation('expect_multicolumn_sum_to_equal', {columns: ['A', 'B'], sum: 4},
      {notes: {format: 'markdown', content: ['keep me']}});
    const imported = importGXSuite(suite([unknown]));
    expect(imported.content.rules).toEqual([]);
    expect(imported.unsupportedSourceContent).toEqual([unknown]);
    expect(exportGXSuite(imported.content, imported).expectations).toEqual([unknown]);
  });

  it('exports supported rules and reports unmapped native rules explicitly', () => {
    const imported = importGXSuite(suite([
      expectation('expect_column_values_to_not_be_null'),
    ]));
    const content = {...imported.content, rules: [...imported.content.rules, {
      id: 'native-freshness', name: 'Fresh data', type: 'freshness', dimension: 'freshness',
      parameters: {minutes: 5}, threshold: null, ratio: false, severity: 'warning',
      evaluationMode: 'provider_reported', enabled: true, generatedSuggestion: false,
      sourceSampleRef: null, sourceRevision: null, actionIds: [], documentation: '',
      nativeDetails: {mechanism: 'monitoring'},
    }]};
    const exported = exportGXSuite(content, imported);
    expect(exported.expectations[0]).toMatchObject({
      expectation_type: 'expect_column_values_to_not_be_null',
      meta: {cdeadminRuleId: 'gx-1'}});
    expect(exported.meta.unsupportedQualityRuleIds).toEqual(['native-freshness']);
  });

  it('rejects malformed suites and secret-bearing extension content', () => {
    expect(() => importGXSuite({version: '1', expectations: {}})).toThrow('expectations[]');
    expect(() => importGXSuite(suite([expectation('unknown', {password: 'bad'})])))
      .toThrow('Raw credential');
    expect(() => exportGXSuite({}, {sourceVersion: '1',
      unsupportedSourceContent: [{accessToken: 'bad'}]})).toThrow('Raw credential');
  });
});
