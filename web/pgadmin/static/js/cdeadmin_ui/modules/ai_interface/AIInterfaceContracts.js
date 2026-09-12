/////////////////////////////////////////////////////////////
// AI Interface form/screen contract catalogue and reachability map.
/////////////////////////////////////////////////////////////

import {immutable} from '../../platform/serviceUtils';
import {AI_COMMAND_CATALOG, AI_INTERFACE_MODULE_ID} from
  '../../specifications/ai_discovery_zero_grey';

import agentProfile from '../../specifications/ai_discovery_zero_grey/machine/forms/ai_agent_profile.form.json';
import approvalPolicy from '../../specifications/ai_discovery_zero_grey/machine/forms/ai_approval_policy.form.json';
import auditExport from '../../specifications/ai_discovery_zero_grey/machine/forms/ai_audit_export.form.json';
import backgroundRun from '../../specifications/ai_discovery_zero_grey/machine/forms/ai_background_run.form.json';
import budgetPolicy from '../../specifications/ai_discovery_zero_grey/machine/forms/ai_budget_policy.form.json';
import capabilityConnector from '../../specifications/ai_discovery_zero_grey/machine/forms/ai_cdeadmin_capability_connector.form.json';
import connectorTest from '../../specifications/ai_discovery_zero_grey/machine/forms/ai_connector_test.form.json';
import contextExposure from '../../specifications/ai_discovery_zero_grey/machine/forms/ai_context_exposure.form.json';
import dataEgressPolicy from '../../specifications/ai_discovery_zero_grey/machine/forms/ai_data_egress_policy.form.json';
import databaseConnector from '../../specifications/ai_discovery_zero_grey/machine/forms/ai_database_connector.form.json';
import emergencyControls from '../../specifications/ai_discovery_zero_grey/machine/forms/ai_emergency_controls.form.json';
import highRiskApproval from '../../specifications/ai_discovery_zero_grey/machine/forms/ai_high_risk_approval.form.json';
import instructionAsset from '../../specifications/ai_discovery_zero_grey/machine/forms/ai_instruction_asset.form.json';
import mcpConnector from '../../specifications/ai_discovery_zero_grey/machine/forms/ai_mcp_connector.form.json';
import modelProvider from '../../specifications/ai_discovery_zero_grey/machine/forms/ai_model_provider.form.json';
import newSession from '../../specifications/ai_discovery_zero_grey/machine/forms/ai_new_session.form.json';
import planReview from '../../specifications/ai_discovery_zero_grey/machine/forms/ai_plan_review.form.json';
import principalBinding from '../../specifications/ai_discovery_zero_grey/machine/forms/ai_principal_binding.form.json';
import queryPolicy from '../../specifications/ai_discovery_zero_grey/machine/forms/ai_query_policy.form.json';
import queryReview from '../../specifications/ai_discovery_zero_grey/machine/forms/ai_query_review.form.json';
import resourceScope from '../../specifications/ai_discovery_zero_grey/machine/forms/ai_resource_scope.form.json';
import retentionPolicy from '../../specifications/ai_discovery_zero_grey/machine/forms/ai_retention_policy.form.json';
import toolPolicy from '../../specifications/ai_discovery_zero_grey/machine/forms/ai_tool_policy.form.json';

import actionApprovalScreen from '../../specifications/ai_discovery_zero_grey/machine/screens/cdeadmin_ai_interface_action_approval.screen.json';
import agentProfileEditorScreen from '../../specifications/ai_discovery_zero_grey/machine/screens/cdeadmin_ai_interface_agent_profile_editor.screen.json';
import agentProfilesScreen from '../../specifications/ai_discovery_zero_grey/machine/screens/cdeadmin_ai_interface_agent_profiles.screen.json';
import aiAdminScreen from '../../specifications/ai_discovery_zero_grey/machine/screens/cdeadmin_ai_interface_ai_admin.screen.json';
import aiWorkbenchScreen from '../../specifications/ai_discovery_zero_grey/machine/screens/cdeadmin_ai_interface_ai_workbench.screen.json';
import approvalPolicyScreen from '../../specifications/ai_discovery_zero_grey/machine/screens/cdeadmin_ai_interface_approval_policy.screen.json';
import auditScreen from '../../specifications/ai_discovery_zero_grey/machine/screens/cdeadmin_ai_interface_audit.screen.json';
import budgetPolicyScreen from '../../specifications/ai_discovery_zero_grey/machine/screens/cdeadmin_ai_interface_budget_policy.screen.json';
import toolCatalogScreen from '../../specifications/ai_discovery_zero_grey/machine/screens/cdeadmin_ai_interface_cdeadmin_tool_catalog.screen.json';
import connectorHealthScreen from '../../specifications/ai_discovery_zero_grey/machine/screens/cdeadmin_ai_interface_connector_health.screen.json';
import contextManagerScreen from '../../specifications/ai_discovery_zero_grey/machine/screens/cdeadmin_ai_interface_context_manager.screen.json';
import dataEgressScreen from '../../specifications/ai_discovery_zero_grey/machine/screens/cdeadmin_ai_interface_data_egress.screen.json';
import connectorWizardScreen from '../../specifications/ai_discovery_zero_grey/machine/screens/cdeadmin_ai_interface_database_connector_wizard.screen.json';
import databaseConnectorsScreen from '../../specifications/ai_discovery_zero_grey/machine/screens/cdeadmin_ai_interface_database_connectors.screen.json';
import mcpConnectorsScreen from '../../specifications/ai_discovery_zero_grey/machine/screens/cdeadmin_ai_interface_mcp_connectors.screen.json';
import mcpToolBrowserScreen from '../../specifications/ai_discovery_zero_grey/machine/screens/cdeadmin_ai_interface_mcp_tool_browser.screen.json';
import modelProviderEditorScreen from '../../specifications/ai_discovery_zero_grey/machine/screens/cdeadmin_ai_interface_model_provider_editor.screen.json';
import modelProvidersScreen from '../../specifications/ai_discovery_zero_grey/machine/screens/cdeadmin_ai_interface_model_providers.screen.json';
import planReviewScreen from '../../specifications/ai_discovery_zero_grey/machine/screens/cdeadmin_ai_interface_plan_review.screen.json';
import queryReviewScreen from '../../specifications/ai_discovery_zero_grey/machine/screens/cdeadmin_ai_interface_query_review.screen.json';
import retentionPolicyScreen from '../../specifications/ai_discovery_zero_grey/machine/screens/cdeadmin_ai_interface_retention_policy.screen.json';
import runMonitorScreen from '../../specifications/ai_discovery_zero_grey/machine/screens/cdeadmin_ai_interface_run_monitor.screen.json';
import scratchBirdAccessScreen from '../../specifications/ai_discovery_zero_grey/machine/screens/cdeadmin_ai_interface_scratchbird_access.screen.json';
import sessionHistoryScreen from '../../specifications/ai_discovery_zero_grey/machine/screens/cdeadmin_ai_interface_session_history.screen.json';
import toolPolicyScreen from '../../specifications/ai_discovery_zero_grey/machine/screens/cdeadmin_ai_interface_tool_policy.screen.json';
import usageCostScreen from '../../specifications/ai_discovery_zero_grey/machine/screens/cdeadmin_ai_interface_usage_cost.screen.json';

export const AI_INTERFACE_FORMS = immutable([
  agentProfile, approvalPolicy, auditExport, backgroundRun, budgetPolicy,
  capabilityConnector, connectorTest, contextExposure, dataEgressPolicy,
  databaseConnector, emergencyControls, highRiskApproval, instructionAsset,
  mcpConnector, modelProvider, newSession, planReview, principalBinding,
  queryPolicy, queryReview, resourceScope, retentionPolicy, toolPolicy,
]);

export const AI_INTERFACE_SCREENS = immutable([
  actionApprovalScreen, agentProfileEditorScreen, agentProfilesScreen,
  aiAdminScreen, aiWorkbenchScreen, approvalPolicyScreen, auditScreen,
  budgetPolicyScreen, toolCatalogScreen, connectorHealthScreen,
  contextManagerScreen, dataEgressScreen, connectorWizardScreen,
  databaseConnectorsScreen, mcpConnectorsScreen, mcpToolBrowserScreen,
  modelProviderEditorScreen, modelProvidersScreen, planReviewScreen,
  queryReviewScreen, retentionPolicyScreen, runMonitorScreen,
  scratchBirdAccessScreen, sessionHistoryScreen, toolPolicyScreen,
  usageCostScreen,
]);

export const AI_FORM_COMPONENTS = immutable([
  'AssetPicker', 'Banner', 'Checkbox', 'CodeEditor', 'ComboBox',
  'ConnectionSelector', 'DataGrid', 'DateField', 'EnvironmentIndicator',
  'MultiSelect', 'NumberField', 'ResourcePicker', 'SecretField',
  'SegmentedControl', 'Select', 'TextArea', 'TextField', 'ToggleSwitch',
  'UnitNumberField',
]);

export const AI_SCREEN_FORM_BINDINGS = immutable({
  'cdeadmin.ai_interface.action_approval': ['ai.high_risk_approval'],
  'cdeadmin.ai_interface.agent_profile_editor': [
    'ai.agent_profile', 'ai.instruction_asset'],
  'cdeadmin.ai_interface.agent_profiles': ['ai.agent_profile'],
  'cdeadmin.ai_interface.ai_admin': ['ai.emergency_controls'],
  'cdeadmin.ai_interface.ai_workbench': [
    'ai.new_session', 'ai.context_exposure', 'ai.background_run'],
  'cdeadmin.ai_interface.approval_policy': ['ai.approval_policy'],
  'cdeadmin.ai_interface.audit': ['ai.audit_export'],
  'cdeadmin.ai_interface.budget_policy': ['ai.budget_policy'],
  'cdeadmin.ai_interface.cdeadmin_tool_catalog': [
    'ai.cdeadmin_capability_connector'],
  'cdeadmin.ai_interface.connector_health': ['ai.connector_test'],
  'cdeadmin.ai_interface.context_manager': ['ai.context_exposure'],
  'cdeadmin.ai_interface.data_egress': [
    'ai.data_egress_policy', 'ai.query_policy'],
  'cdeadmin.ai_interface.database_connector_wizard': [
    'ai.database_connector', 'ai.principal_binding', 'ai.resource_scope',
    'ai.query_policy'],
  'cdeadmin.ai_interface.database_connectors': ['ai.database_connector'],
  'cdeadmin.ai_interface.mcp_connectors': ['ai.mcp_connector'],
  'cdeadmin.ai_interface.mcp_tool_browser': ['ai.mcp_connector'],
  'cdeadmin.ai_interface.model_provider_editor': ['ai.model_provider'],
  'cdeadmin.ai_interface.model_providers': ['ai.model_provider'],
  'cdeadmin.ai_interface.plan_review': ['ai.plan_review'],
  'cdeadmin.ai_interface.query_review': ['ai.query_review'],
  'cdeadmin.ai_interface.retention_policy': ['ai.retention_policy'],
  'cdeadmin.ai_interface.run_monitor': ['ai.background_run'],
  'cdeadmin.ai_interface.scratchbird_access': [
    'ai.principal_binding', 'ai.resource_scope'],
  'cdeadmin.ai_interface.session_history': ['ai.new_session'],
  'cdeadmin.ai_interface.tool_policy': ['ai.tool_policy'],
  'cdeadmin.ai_interface.usage_cost': [],
});

export const AI_SCREEN_GROUPS = immutable([
  {id: 'work', label: 'Work', screens: [
    'ai_workbench', 'context_manager', 'plan_review', 'query_review',
    'action_approval', 'run_monitor']},
  {id: 'identity', label: 'Profiles and identity', screens: [
    'agent_profiles', 'agent_profile_editor', 'model_providers',
    'model_provider_editor', 'scratchbird_access']},
  {id: 'connectors', label: 'Connectors and tools', screens: [
    'database_connectors', 'database_connector_wizard', 'mcp_connectors',
    'mcp_tool_browser', 'cdeadmin_tool_catalog', 'connector_health']},
  {id: 'governance', label: 'Governance', screens: [
    'tool_policy', 'data_egress', 'approval_policy', 'budget_policy',
    'retention_policy', 'ai_admin']},
  {id: 'history', label: 'History and usage', screens: [
    'session_history', 'audit', 'usage_cost']},
]);

export const AI_FORM_BY_ID = immutable(Object.fromEntries(
  AI_INTERFACE_FORMS.map((form) => [form.form_id, form])));
export const AI_SCREEN_BY_ID = immutable(Object.fromEntries(
  AI_INTERFACE_SCREENS.map((screen) => [screen.screen_id, screen])));

export function shortAIScreenId(screenId) {
  return String(screenId).replace(`${AI_INTERFACE_MODULE_ID}.`, '');
}

function invalid(message) {
  throw new TypeError(`AI Interface surface contracts are invalid: ${message}`);
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

export function validateAIInterfaceSurfaceContracts() {
  if(AI_INTERFACE_FORMS.length !== 23) invalid('exactly 23 forms are required.');
  if(AI_INTERFACE_SCREENS.length !== 26) invalid('exactly 26 screens are required.');
  const formIds = AI_INTERFACE_FORMS.map((form) => form.form_id);
  const screenIds = AI_INTERFACE_SCREENS.map((screen) => screen.screen_id);
  if(new Set(formIds).size !== formIds.length) invalid('form IDs are duplicated.');
  if(new Set(screenIds).size !== screenIds.length) invalid('screen IDs are duplicated.');
  const commandIds = new Set(AI_COMMAND_CATALOG.commands.map(({id}) => id));
  const renderedComponents = new Set();
  for(const form of AI_INTERFACE_FORMS) {
    if(form.module !== AI_INTERFACE_MODULE_ID || form.spec_gaps.length) {
      invalid(`${form.form_id} has the wrong module or unresolved gaps.`);
    }
    if(!exactValues(form.states, FORM_STATES) || !form.keyboard ||
        !Array.isArray(form.security_notes)) {
      invalid(`${form.form_id} has incomplete state, keyboard or security behavior.`);
    }
    for(const section of form.sections) {
      for(const field of section.fields) renderedComponents.add(field.component);
    }
    for(const action of form.actions) {
      if(action.command && !commandIds.has(action.command)) {
        invalid(`${form.form_id} references unknown command ${action.command}.`);
      }
    }
  }
  if(AI_FORM_COMPONENTS.length !== renderedComponents.size ||
      AI_FORM_COMPONENTS.some((component) => !renderedComponents.has(component))) {
    invalid('form component vocabulary is not fully supported.');
  }
  const coveredForms = new Set();
  for(const screen of AI_INTERFACE_SCREENS) {
    if(screen.module !== AI_INTERFACE_MODULE_ID || screen.spec_gaps.length) {
      invalid(`${screen.screen_id} has the wrong module or unresolved gaps.`);
    }
    if(!exactValues(Object.keys(screen.states), SCREEN_STATES) ||
        !exactValues(screen.tokens_profiles || [], TOKEN_PROFILES) ||
        !screen.accessibility || !screen.keyboard || !screen.pointer ||
        !screen.persistence || !screen.multi_window ||
        !screen.functional_regression.length) {
      invalid(`${screen.screen_id} has incomplete interaction or state behavior.`);
    }
    const bindings = AI_SCREEN_FORM_BINDINGS[screen.screen_id];
    if(!bindings) invalid(`${screen.screen_id} has no form binding.`);
    bindings.forEach((formId) => {
      if(!AI_FORM_BY_ID[formId]) invalid(`${screen.screen_id} binds unknown ${formId}.`);
      coveredForms.add(formId);
    });
    [...screen.entry_commands, ...screen.toolbar_commands].forEach((command) => {
      if(!commandIds.has(command)) invalid(`${screen.screen_id} references unknown ${command}.`);
    });
  }
  if(coveredForms.size !== AI_INTERFACE_FORMS.length) {
    invalid('not every AI form is reachable from a screen.');
  }
  const grouped = AI_SCREEN_GROUPS.flatMap(({screens}) => screens);
  if(grouped.length !== 26 || new Set(grouped).size !== 26 ||
      grouped.some((id) => !AI_SCREEN_BY_ID[`${AI_INTERFACE_MODULE_ID}.${id}`])) {
    invalid('screen navigation is incomplete or duplicated.');
  }
  return immutable({forms: formIds.length, screens: screenIds.length,
    commands: commandIds.size, components: [...renderedComponents].sort()});
}

export const AI_INTERFACE_SURFACE_SUMMARY =
  validateAIInterfaceSurfaceContracts();
