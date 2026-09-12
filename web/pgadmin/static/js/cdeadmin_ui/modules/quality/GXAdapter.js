/////////////////////////////////////////////////////////////
// Loss-aware Great Expectations interoperability mapping.
/////////////////////////////////////////////////////////////

import {immutable, noRawSecrets, plainObject, platformValue} from '../../platform/serviceUtils';
import {createQualityContent, validateQualityRule} from './contracts';

export const GX_PROFILE = 'cdeadmin.gx-interop.v1';

const GX_TO_RULE = Object.freeze({
  expect_column_values_to_not_be_null: 'not_null',
  expect_column_values_to_be_unique: 'uniqueness',
  expect_column_values_to_be_in_set: 'accepted_values',
  expect_column_values_to_match_regex: 'regex/pattern',
  expect_column_values_to_be_between: 'range',
  expect_table_row_count_to_be_between: 'row_count',
});
const RULE_TO_GX = Object.freeze(Object.fromEntries(Object.entries(GX_TO_RULE)
  .map(([gx, quality]) => [quality, gx])));

export function importGXSuite(input) {
  plainObject(input, 'GX suite'); noRawSecrets(input, 'GX suite');
  const version = platformValue(input.meta?.great_expectations_version ?? input.version,
    'GX version');
  const expectations = input.expectations;
  if(!Array.isArray(expectations)) throw new TypeError('GX suite requires expectations[].');
  const preserved = []; const rules = [];
  expectations.forEach((expectation, index) => {
    plainObject(expectation, 'GX expectation');
    const gxType = platformValue(expectation.expectation_type, 'GX expectation type');
    const type = GX_TO_RULE[gxType];
    if(!type) { preserved.push(immutable({...expectation})); return; }
    rules.push(validateQualityRule({id: `gx-${index + 1}`, name: gxType, type,
      dimension: type === 'uniqueness' ? 'uniqueness' : type === 'not_null' ?
        'completeness' : 'validity', parameters: expectation.kwargs ?? {},
      threshold: null, severity: expectation.meta?.severity ?? 'warning',
      evaluationMode: 'exact', enabled: true, actionIds: [], documentation: '',
      nativeDetails: {gxExpectationType: gxType, gxMeta: expectation.meta ?? {}}}));
  });
  return immutable({profile: GX_PROFILE, sourceVersion: version,
    provenance: immutable({suiteName: String(input.expectation_suite_name ?? ''),
      sourceMeta: immutable({...plainObject(input.meta ?? {}, 'GX metadata')})}),
    content: createQualityContent({name: input.expectation_suite_name ?? '', rules}),
    unsupportedSourceContent: immutable(preserved)});
}

export function exportGXSuite(contentInput, interoperability={}) {
  const content = createQualityContent(contentInput);
  const sourceVersion = platformValue(interoperability.sourceVersion ?? 'unknown', 'GX version');
  const unsupported = interoperability.unsupportedSourceContent ?? [];
  if(!Array.isArray(unsupported)) throw new TypeError('Unsupported GX content must be an array.');
  unsupported.forEach((item) => noRawSecrets(item, 'Unsupported GX content'));
  const mapped = []; const unmapped = [];
  content.rules.forEach((rule) => {
    const expectationType = RULE_TO_GX[rule.type];
    if(!expectationType) { unmapped.push(rule.id); return; }
    mapped.push({expectation_type: expectationType, kwargs: {...rule.parameters},
      meta: {severity: rule.severity, evaluationMode: rule.evaluationMode,
        cdeadminRuleId: rule.id}});
  });
  return immutable({expectation_suite_name: content.name,
    expectations: [...mapped, ...unsupported],
    meta: {great_expectations_version: sourceVersion, cdeadmin_profile: GX_PROFILE,
      unsupportedQualityRuleIds: unmapped, sourceProvenance: interoperability.provenance ?? {}}});
}
