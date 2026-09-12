/////////////////////////////////////////////////////////////
// Discovery Intelligence form/screen catalogue and reachability map.
/////////////////////////////////////////////////////////////

import {immutable} from '../../platform/serviceUtils';
import {DISCOVERY_COMMAND_CATALOG, DISCOVERY_INTELLIGENCE_MODULE_ID} from
  '../../specifications/ai_discovery_zero_grey';

import accessApproval from '../../specifications/ai_discovery_zero_grey/machine/forms/discovery_access_approval.form.json';
import accessRequest from '../../specifications/ai_discovery_zero_grey/machine/forms/discovery_access_request.form.json';
import advancedSearch from '../../specifications/ai_discovery_zero_grey/machine/forms/discovery_advanced_search.form.json';
import businessTerm from '../../specifications/ai_discovery_zero_grey/machine/forms/discovery_business_term.form.json';
import certificationProfile from '../../specifications/ai_discovery_zero_grey/machine/forms/discovery_certification_profile.form.json';
import certificationRequest from '../../specifications/ai_discovery_zero_grey/machine/forms/discovery_certification_request.form.json';
import certificationReview from '../../specifications/ai_discovery_zero_grey/machine/forms/discovery_certification_review.form.json';
import collection from '../../specifications/ai_discovery_zero_grey/machine/forms/discovery_collection.form.json';
import curationResolution from '../../specifications/ai_discovery_zero_grey/machine/forms/discovery_curation_resolution.form.json';
import dataProduct from '../../specifications/ai_discovery_zero_grey/machine/forms/discovery_data_product.form.json';
import domainEditor from '../../specifications/ai_discovery_zero_grey/machine/forms/discovery_domain_editor.form.json';
import duplicateReview from '../../specifications/ai_discovery_zero_grey/machine/forms/discovery_duplicate_review.form.json';
import embeddingConfig from '../../specifications/ai_discovery_zero_grey/machine/forms/discovery_embedding_config.form.json';
import enrichmentReview from '../../specifications/ai_discovery_zero_grey/machine/forms/discovery_enrichment_review.form.json';
import externalCatalogSource from '../../specifications/ai_discovery_zero_grey/machine/forms/discovery_external_catalog_source.form.json';
import indexBackend from '../../specifications/ai_discovery_zero_grey/machine/forms/discovery_index_backend.form.json';
import indexSource from '../../specifications/ai_discovery_zero_grey/machine/forms/discovery_index_source.form.json';
import metric from '../../specifications/ai_discovery_zero_grey/machine/forms/discovery_metric.form.json';
import ownerSteward from '../../specifications/ai_discovery_zero_grey/machine/forms/discovery_owner_steward.form.json';
import previewPolicy from '../../specifications/ai_discovery_zero_grey/machine/forms/discovery_preview_policy.form.json';
import quickSearch from '../../specifications/ai_discovery_zero_grey/machine/forms/discovery_quick_search.form.json';
import rankingProfile from '../../specifications/ai_discovery_zero_grey/machine/forms/discovery_ranking_profile.form.json';
import recommendationPolicy from '../../specifications/ai_discovery_zero_grey/machine/forms/discovery_recommendation_policy.form.json';
import savedSearch from '../../specifications/ai_discovery_zero_grey/machine/forms/discovery_saved_search.form.json';
import searchFeedback from '../../specifications/ai_discovery_zero_grey/machine/forms/discovery_search_feedback.form.json';
import synonyms from '../../specifications/ai_discovery_zero_grey/machine/forms/discovery_synonyms.form.json';
import systemSurfaceSettings from '../../specifications/ai_discovery_zero_grey/machine/forms/discovery_system_surface_settings.form.json';
import usagePolicy from '../../specifications/ai_discovery_zero_grey/machine/forms/discovery_usage_policy.form.json';
import visibilityTest from '../../specifications/ai_discovery_zero_grey/machine/forms/discovery_visibility_test.form.json';
import zeroResultResolution from '../../specifications/ai_discovery_zero_grey/machine/forms/discovery_zero_result_resolution.form.json';

import accessInboxScreen from '../../specifications/ai_discovery_zero_grey/machine/screens/cdeadmin_discovery_intelligence_access_inbox.screen.json';
import accessRequestScreen from '../../specifications/ai_discovery_zero_grey/machine/screens/cdeadmin_discovery_intelligence_access_request.screen.json';
import accessSurfacesScreen from '../../specifications/ai_discovery_zero_grey/machine/screens/cdeadmin_discovery_intelligence_access_surfaces.screen.json';
import advancedSearchScreen from '../../specifications/ai_discovery_zero_grey/machine/screens/cdeadmin_discovery_intelligence_advanced_search.screen.json';
import certificationReviewScreen from '../../specifications/ai_discovery_zero_grey/machine/screens/cdeadmin_discovery_intelligence_certification_review.screen.json';
import certificationsScreen from '../../specifications/ai_discovery_zero_grey/machine/screens/cdeadmin_discovery_intelligence_certifications.screen.json';
import collectionsScreen from '../../specifications/ai_discovery_zero_grey/machine/screens/cdeadmin_discovery_intelligence_collections.screen.json';
import curationQueueScreen from '../../specifications/ai_discovery_zero_grey/machine/screens/cdeadmin_discovery_intelligence_curation_queue.screen.json';
import data360Screen from '../../specifications/ai_discovery_zero_grey/machine/screens/cdeadmin_discovery_intelligence_data_360.screen.json';
import discoveryAdminScreen from '../../specifications/ai_discovery_zero_grey/machine/screens/cdeadmin_discovery_intelligence_discovery_admin.screen.json';
import discoveryHomeScreen from '../../specifications/ai_discovery_zero_grey/machine/screens/cdeadmin_discovery_intelligence_discovery_home.screen.json';
import domainsScreen from '../../specifications/ai_discovery_zero_grey/machine/screens/cdeadmin_discovery_intelligence_domains.screen.json';
import duplicateReviewScreen from '../../specifications/ai_discovery_zero_grey/machine/screens/cdeadmin_discovery_intelligence_duplicate_review.screen.json';
import enrichmentReviewScreen from '../../specifications/ai_discovery_zero_grey/machine/screens/cdeadmin_discovery_intelligence_enrichment_review.screen.json';
import field360Screen from '../../specifications/ai_discovery_zero_grey/machine/screens/cdeadmin_discovery_intelligence_field_360.screen.json';
import glossaryScreen from '../../specifications/ai_discovery_zero_grey/machine/screens/cdeadmin_discovery_intelligence_glossary.screen.json';
import indexHealthScreen from '../../specifications/ai_discovery_zero_grey/machine/screens/cdeadmin_discovery_intelligence_index_health.screen.json';
import indexSourcesScreen from '../../specifications/ai_discovery_zero_grey/machine/screens/cdeadmin_discovery_intelligence_index_sources.screen.json';
import marketplaceScreen from '../../specifications/ai_discovery_zero_grey/machine/screens/cdeadmin_discovery_intelligence_marketplace.screen.json';
import metric360Screen from '../../specifications/ai_discovery_zero_grey/machine/screens/cdeadmin_discovery_intelligence_metric_360.screen.json';
import metricEditorScreen from '../../specifications/ai_discovery_zero_grey/machine/screens/cdeadmin_discovery_intelligence_metric_editor.screen.json';
import productDetailScreen from '../../specifications/ai_discovery_zero_grey/machine/screens/cdeadmin_discovery_intelligence_product_detail.screen.json';
import productEditorScreen from '../../specifications/ai_discovery_zero_grey/machine/screens/cdeadmin_discovery_intelligence_product_editor.screen.json';
import profileStatsScreen from '../../specifications/ai_discovery_zero_grey/machine/screens/cdeadmin_discovery_intelligence_profile_stats.screen.json';
import rankingProfilesScreen from '../../specifications/ai_discovery_zero_grey/machine/screens/cdeadmin_discovery_intelligence_ranking_profiles.screen.json';
import rankingTunerScreen from '../../specifications/ai_discovery_zero_grey/machine/screens/cdeadmin_discovery_intelligence_ranking_tuner.screen.json';
import relatedGraphScreen from '../../specifications/ai_discovery_zero_grey/machine/screens/cdeadmin_discovery_intelligence_related_graph.screen.json';
import safePreviewScreen from '../../specifications/ai_discovery_zero_grey/machine/screens/cdeadmin_discovery_intelligence_safe_preview.screen.json';
import savedSearchesScreen from '../../specifications/ai_discovery_zero_grey/machine/screens/cdeadmin_discovery_intelligence_saved_searches.screen.json';
import searchAnalyticsScreen from '../../specifications/ai_discovery_zero_grey/machine/screens/cdeadmin_discovery_intelligence_search_analytics.screen.json';
import searchResultsScreen from '../../specifications/ai_discovery_zero_grey/machine/screens/cdeadmin_discovery_intelligence_search_results.screen.json';
import searchToAnalysisScreen from '../../specifications/ai_discovery_zero_grey/machine/screens/cdeadmin_discovery_intelligence_search_to_analysis.screen.json';
import semanticIndexScreen from '../../specifications/ai_discovery_zero_grey/machine/screens/cdeadmin_discovery_intelligence_semantic_index.screen.json';
import synonymsScreen from '../../specifications/ai_discovery_zero_grey/machine/screens/cdeadmin_discovery_intelligence_synonyms.screen.json';
import termEditorScreen from '../../specifications/ai_discovery_zero_grey/machine/screens/cdeadmin_discovery_intelligence_term_editor.screen.json';
import usagePopularityScreen from '../../specifications/ai_discovery_zero_grey/machine/screens/cdeadmin_discovery_intelligence_usage_popularity.screen.json';
import visibilityTestScreen from '../../specifications/ai_discovery_zero_grey/machine/screens/cdeadmin_discovery_intelligence_visibility_test.screen.json';
import zeroResultTermsScreen from '../../specifications/ai_discovery_zero_grey/machine/screens/cdeadmin_discovery_intelligence_zero_result_terms.screen.json';

export const DISCOVERY_INTERFACE_FORMS = immutable([
  accessApproval, accessRequest, advancedSearch, businessTerm,
  certificationProfile, certificationRequest, certificationReview, collection,
  curationResolution, dataProduct, domainEditor, duplicateReview,
  embeddingConfig, enrichmentReview, externalCatalogSource, indexBackend,
  indexSource, metric, ownerSteward, previewPolicy, quickSearch, rankingProfile,
  recommendationPolicy, savedSearch, searchFeedback, synonyms,
  systemSurfaceSettings, usagePolicy, visibilityTest, zeroResultResolution,
]);

export const DISCOVERY_INTERFACE_SCREENS = immutable([
  accessInboxScreen, accessRequestScreen, accessSurfacesScreen,
  advancedSearchScreen, certificationReviewScreen, certificationsScreen,
  collectionsScreen, curationQueueScreen, data360Screen, discoveryAdminScreen,
  discoveryHomeScreen, domainsScreen, duplicateReviewScreen,
  enrichmentReviewScreen, field360Screen, glossaryScreen, indexHealthScreen,
  indexSourcesScreen, marketplaceScreen, metric360Screen, metricEditorScreen,
  productDetailScreen, productEditorScreen, profileStatsScreen,
  rankingProfilesScreen, rankingTunerScreen, relatedGraphScreen,
  safePreviewScreen, savedSearchesScreen, searchAnalyticsScreen,
  searchResultsScreen, searchToAnalysisScreen, semanticIndexScreen,
  synonymsScreen, termEditorScreen, usagePopularityScreen,
  visibilityTestScreen, zeroResultTermsScreen,
]);

export const DISCOVERY_FORM_COMPONENTS = immutable([
  'AssetPicker', 'Checkbox', 'CodeEditor', 'ComboBox', 'ConnectionSelector',
  'DataGrid', 'DateField', 'DurationField', 'MultiSelect', 'NumberField',
  'ResourcePicker', 'SearchField', 'SecretField', 'SegmentedControl', 'Select',
  'TextArea', 'TextField', 'ToggleSwitch',
]);

export const DISCOVERY_SCREEN_FORM_BINDINGS = immutable({
  'cdeadmin.discovery_intelligence.access_inbox': ['discovery.access_approval'],
  'cdeadmin.discovery_intelligence.access_request': ['discovery.access_request'],
  'cdeadmin.discovery_intelligence.access_surfaces': [],
  'cdeadmin.discovery_intelligence.advanced_search': ['discovery.advanced_search'],
  'cdeadmin.discovery_intelligence.certification_review': ['discovery.certification_review'],
  'cdeadmin.discovery_intelligence.certifications': [
    'discovery.certification_request', 'discovery.certification_profile'],
  'cdeadmin.discovery_intelligence.collections': ['discovery.collection'],
  'cdeadmin.discovery_intelligence.curation_queue': ['discovery.curation_resolution'],
  'cdeadmin.discovery_intelligence.data_360': [
    'discovery.owner_steward', 'discovery.preview_policy'],
  'cdeadmin.discovery_intelligence.discovery_admin': [
    'discovery.system_surface_settings', 'discovery.usage_policy',
    'discovery.recommendation_policy'],
  'cdeadmin.discovery_intelligence.discovery_home': ['discovery.quick_search'],
  'cdeadmin.discovery_intelligence.domains': ['discovery.domain_editor'],
  'cdeadmin.discovery_intelligence.duplicate_review': ['discovery.duplicate_review'],
  'cdeadmin.discovery_intelligence.enrichment_review': ['discovery.enrichment_review'],
  'cdeadmin.discovery_intelligence.field_360': [],
  'cdeadmin.discovery_intelligence.glossary': ['discovery.business_term'],
  'cdeadmin.discovery_intelligence.index_health': ['discovery.index_backend'],
  'cdeadmin.discovery_intelligence.index_sources': [
    'discovery.index_source', 'discovery.external_catalog_source'],
  'cdeadmin.discovery_intelligence.marketplace': [],
  'cdeadmin.discovery_intelligence.metric_360': [],
  'cdeadmin.discovery_intelligence.metric_editor': ['discovery.metric'],
  'cdeadmin.discovery_intelligence.product_detail': [],
  'cdeadmin.discovery_intelligence.product_editor': ['discovery.data_product'],
  'cdeadmin.discovery_intelligence.profile_stats': [],
  'cdeadmin.discovery_intelligence.ranking_profiles': ['discovery.ranking_profile'],
  'cdeadmin.discovery_intelligence.ranking_tuner': ['discovery.ranking_profile'],
  'cdeadmin.discovery_intelligence.related_graph': [],
  'cdeadmin.discovery_intelligence.safe_preview': ['discovery.preview_policy'],
  'cdeadmin.discovery_intelligence.saved_searches': ['discovery.saved_search'],
  'cdeadmin.discovery_intelligence.search_analytics': ['discovery.search_feedback'],
  'cdeadmin.discovery_intelligence.search_results': [
    'discovery.quick_search', 'discovery.search_feedback'],
  'cdeadmin.discovery_intelligence.search_to_analysis': [],
  'cdeadmin.discovery_intelligence.semantic_index': ['discovery.embedding_config'],
  'cdeadmin.discovery_intelligence.synonyms': ['discovery.synonyms'],
  'cdeadmin.discovery_intelligence.term_editor': ['discovery.business_term'],
  'cdeadmin.discovery_intelligence.usage_popularity': ['discovery.usage_policy'],
  'cdeadmin.discovery_intelligence.visibility_test': ['discovery.visibility_test'],
  'cdeadmin.discovery_intelligence.zero_result_terms': ['discovery.zero_result_resolution'],
});

export const DISCOVERY_SCREEN_GROUPS = immutable([
  {id: 'discover', label: 'Discover', screens: [
    'discovery_home', 'search_results', 'advanced_search', 'related_graph',
    'safe_preview', 'search_to_analysis']},
  {id: 'knowledge', label: 'Knowledge and products', screens: [
    'data_360', 'field_360', 'glossary', 'term_editor', 'domains',
    'metric_360', 'metric_editor', 'marketplace', 'product_detail',
    'product_editor', 'profile_stats']},
  {id: 'trust', label: 'Trust and access', screens: [
    'certifications', 'certification_review', 'access_inbox', 'access_request',
    'access_surfaces', 'curation_queue', 'duplicate_review',
    'enrichment_review', 'visibility_test']},
  {id: 'personal', label: 'Saved work', screens: [
    'saved_searches', 'collections']},
  {id: 'administration', label: 'Discovery administration', screens: [
    'discovery_admin', 'index_sources', 'index_health', 'semantic_index',
    'ranking_profiles', 'ranking_tuner', 'synonyms', 'usage_popularity',
    'search_analytics', 'zero_result_terms']},
]);

export const DISCOVERY_FORM_BY_ID = immutable(Object.fromEntries(
  DISCOVERY_INTERFACE_FORMS.map((form) => [form.form_id, form])));
export const DISCOVERY_SCREEN_BY_ID = immutable(Object.fromEntries(
  DISCOVERY_INTERFACE_SCREENS.map((screen) => [screen.screen_id, screen])));

export function shortDiscoveryScreenId(screenId) {
  return String(screenId).replace(`${DISCOVERY_INTELLIGENCE_MODULE_ID}.`, '');
}

function invalid(message) {
  throw new TypeError(`Discovery Intelligence surface contracts are invalid: ${message}`);
}

const FORM_STATES = ['default', 'loading', 'invalid', 'permission-denied', 'error'];
const SCREEN_STATES = ['default', 'loading', 'empty', 'error', 'permission',
  'disconnected_or_stale'];
const TOKEN_PROFILES = ['scratchbird_dark', 'scratchbird_light', 'standard',
  'compact_expert', 'low_vision', 'motor_assistance', 'high_contrast'];

function exactValues(actual, expected) {
  return actual.length === expected.length && actual.every((value, index) =>
    value === expected[index]);
}

export function validateDiscoveryInterfaceSurfaceContracts() {
  if(DISCOVERY_INTERFACE_FORMS.length !== 30) invalid('exactly 30 forms are required.');
  if(DISCOVERY_INTERFACE_SCREENS.length !== 38) invalid('exactly 38 screens are required.');
  const formIds = DISCOVERY_INTERFACE_FORMS.map((form) => form.form_id);
  const screenIds = DISCOVERY_INTERFACE_SCREENS.map((screen) => screen.screen_id);
  if(new Set(formIds).size !== formIds.length) invalid('form IDs are duplicated.');
  if(new Set(screenIds).size !== screenIds.length) invalid('screen IDs are duplicated.');
  const commandIds = new Set(DISCOVERY_COMMAND_CATALOG.commands.map(({id}) => id));
  const renderedComponents = new Set();
  for(const form of DISCOVERY_INTERFACE_FORMS) {
    if(form.module !== DISCOVERY_INTELLIGENCE_MODULE_ID || form.spec_gaps.length) {
      invalid(`${form.form_id} has the wrong module or unresolved gaps.`);
    }
    if(!exactValues(form.states, FORM_STATES) || !form.keyboard ||
        !Array.isArray(form.security_notes)) {
      invalid(`${form.form_id} has incomplete state, keyboard or security behavior.`);
    }
    form.sections.forEach((section) => section.fields.forEach((field) =>
      renderedComponents.add(field.component)));
    form.actions.forEach((action) => {
      if(action.command && !commandIds.has(action.command)) {
        invalid(`${form.form_id} references unknown command ${action.command}.`);
      }
    });
  }
  if(DISCOVERY_FORM_COMPONENTS.length !== renderedComponents.size ||
      DISCOVERY_FORM_COMPONENTS.some((item) => !renderedComponents.has(item))) {
    invalid('form component vocabulary is not fully supported.');
  }
  const coveredForms = new Set();
  for(const screen of DISCOVERY_INTERFACE_SCREENS) {
    if(screen.module !== DISCOVERY_INTELLIGENCE_MODULE_ID || screen.spec_gaps.length) {
      invalid(`${screen.screen_id} has the wrong module or unresolved gaps.`);
    }
    if(!exactValues(Object.keys(screen.states), SCREEN_STATES) ||
        !exactValues(screen.tokens_profiles || [], TOKEN_PROFILES) ||
        !screen.accessibility || !screen.keyboard || !screen.pointer ||
        !screen.persistence || !screen.multi_window ||
        !screen.functional_regression.length) {
      invalid(`${screen.screen_id} has incomplete interaction or state behavior.`);
    }
    const bindings = DISCOVERY_SCREEN_FORM_BINDINGS[screen.screen_id];
    if(!bindings) invalid(`${screen.screen_id} has no form binding.`);
    bindings.forEach((formId) => {
      if(!DISCOVERY_FORM_BY_ID[formId]) invalid(`${screen.screen_id} binds unknown ${formId}.`);
      coveredForms.add(formId);
    });
    [...screen.entry_commands, ...screen.toolbar_commands].forEach((command) => {
      if(!commandIds.has(command)) invalid(`${screen.screen_id} references unknown ${command}.`);
    });
  }
  if(coveredForms.size !== DISCOVERY_INTERFACE_FORMS.length) {
    invalid('not every Discovery form is reachable from a screen.');
  }
  const grouped = DISCOVERY_SCREEN_GROUPS.flatMap(({screens}) => screens);
  if(grouped.length !== 38 || new Set(grouped).size !== 38 || grouped.some((id) =>
    !DISCOVERY_SCREEN_BY_ID[`${DISCOVERY_INTELLIGENCE_MODULE_ID}.${id}`])) {
    invalid('screen navigation is incomplete or duplicated.');
  }
  return immutable({forms: formIds.length, screens: screenIds.length,
    commands: commandIds.size, components: [...renderedComponents].sort()});
}

export const DISCOVERY_INTERFACE_SURFACE_SUMMARY =
  validateDiscoveryInterfaceSurfaceContracts();
