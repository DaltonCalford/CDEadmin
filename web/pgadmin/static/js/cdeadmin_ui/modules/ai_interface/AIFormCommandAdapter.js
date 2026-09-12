/////////////////////////////////////////////////////////////
// Strict UI-form to AI Interface command argument boundary.
/////////////////////////////////////////////////////////////

import {immutable, noRawSecrets, plainObject, platformValue} from
  '../../platform/serviceUtils';

function reference(value, label) {
  if(value && typeof value === 'object') value = value.canonical ?? value.id ?? value.value;
  return platformValue(value, label, 2048);
}

function references(values, label) {
  if(!Array.isArray(values)) throw new TypeError(`${label} must be an array.`);
  return values.map((value) => reference(value, label));
}

function requiredDefault(defaults, field, formId) {
  if(defaults[field] === null || defaults[field] === undefined || defaults[field] === '') {
    throw new Error(`${formId} requires explicit ${field} command context.`);
  }
  return defaults[field];
}

function content(defaults, formId) {
  const value = requiredDefault(defaults, 'content', formId);
  plainObject(value, `${formId} canonical asset content`);
  return {...value};
}

function asset(defaults, formId, nextContent) {
  return {projectId: requiredDefault(defaults, 'projectId', formId),
    content: nextContent, options: defaults.options ?? {}};
}

function connectorProfile(values, defaults, formId) {
  const profile = content(defaults, formId);
  if(formId === 'ai.database_connector') return {...profile,
    connectorClass: values.mode,
    providerId: reference(values.provider, 'AI connector provider'),
    connectionProfileRef: reference(values.connection, 'AI connection profile'),
    dialectId: values.dialect ? reference(values.dialect, 'AI connector dialect') : null,
    workareaSchemaRef: values.workarea ? reference(values.workarea,
      'AI connector workarea') : null,
    principalBinding: platformValue(values.principal, 'AI principal binding'),
    credentialRef: values.credentialRef ?? profile.credentialRef,
    resourceScopeRefs: references(values.allowed ?? [], 'AI allowed resource'),
  };
  return {...profile, name: platformValue(values.name, 'AI connector name'),
    mcpEndpointRef: platformValue(values.endpoint, 'AI MCP endpoint'),
    mcpProtocolProfile: platformValue(values.protocol, 'AI MCP protocol profile'),
    credentialRef: values.credentialRef ?? profile.credentialRef};
}

function agentProfile(values, defaults, formId, commandId) {
  const profile = content(defaults, formId);
  const clone = commandId === 'ai.agent_profile.clone';
  return {...profile,
    profileId: clone ? requiredDefault(defaults, 'cloneProfileId', formId) : profile.profileId,
    name: platformValue(values.name, 'AI agent profile name'),
    description: values.description || null, enabled: commandId.endsWith('.disable') ? false :
      values.enabled, autonomyMode: values.autonomy,
    modelProfileRef: reference(values.model_profile, 'AI model profile'),
    connectorRefs: references(values.connectors, 'AI connector reference'),
    instructionAssetRef: values.instructions ? reference(values.instructions,
      'AI instruction asset') : null,
    toolPolicyRef: reference(values.tool_policy, 'AI tool policy'),
    dataPolicyRef: reference(values.data_policy, 'AI data policy'),
    approvalPolicyRef: reference(values.approval_policy, 'AI approval policy'),
    budgetPolicyRef: reference(values.budget_policy, 'AI budget policy'),
    retentionPolicyRef: reference(values.retention_policy, 'AI retention policy')};
}

function modelProfile(values, defaults, formId, commandId) {
  const profile = content(defaults, formId);
  return {...profile, displayName: platformValue(values.name, 'AI model profile name'),
    runtimeClass: values.runtime, endpointRef: values.endpoint || null,
    modelId: platformValue(values.model, 'AI model ID'),
    credentialRef: values.credentialRef ?? profile.credentialRef,
    toolCallingSupport: values.tool_calling,
    structuredOutputSupport: values.structured_output,
    streamingSupport: values.streaming, contextLimit: values.context_limit,
    dataResidency: values.residency, retentionStatement: values.retention,
    approvedClassificationMax: values.classification,
    enabled: commandId.endsWith('.disable') ? false : profile.enabled};
}

function policyAsset(formId, values, defaults) {
  const policy = content(defaults, formId);
  if(formId === 'ai.tool_policy') return {...policy, moduleIds: values.modules,
    commandIds: values.commands, deniedCommandIds: values.deny,
    maxAutomaticRisk: values.max_auto_risk,
    allowProjectDrafts: values.allow_project_drafts,
    allowBackgroundTasks: values.allow_background};
  if(formId === 'ai.data_egress_policy') return {...policy,
    defaultExposureLevel: values.default_level,
    maxClassification: values.max_classification,
    allowRemoteSamples: values.remote_samples,
    allowRemoteSource: values.remote_source,
    sensitiveColumnHandling: values.sensitive_columns,
    maxSampleRows: values.max_sample_rows,
    maxTextCharacters: values.max_text_chars,
    allowedModelProfileRefs: references(values.allowed_model_profiles,
      'AI allowed model profile'),
    requireLocalForRestricted: values.require_local_for_restricted};
  if(formId === 'ai.approval_policy') return {...policy, riskRules: {
    R0: values.r0, R1: values.r1, R2: values.r2, R3: values.r3,
    R4: values.r4, R5: values.r5, R6: values.r6}, expiryMinutes: {
    R4: values.r4_minutes, R5: values.r5_minutes, R6: values.r6_minutes}};
  if(formId === 'ai.budget_policy') return {...policy,
    maxModelInputTokens: values.input_tokens,
    maxModelOutputTokens: values.output_tokens,
    maxToolCallsPerTurn: values.tool_calls_turn,
    maxToolCallsPerPlan: values.tool_calls_plan,
    maxDatabaseQueriesPerTurn: values.queries,
    maxDatabaseRowsPerQuery: values.rows,
    maxDatabaseBytesPerQuery: values.bytes,
    maxParallelTools: values.parallel, maxRunMinutes: values.minutes,
    maxEstimatedCostPerTurn: values.cost_turn};
  return {...policy, retainMessages: values.retain_messages,
    conversationDays: values.days, retainToolResults: values.retain_tool_results,
    auditDays: values.audit_days,
    retainPromptsInAudit: values.retain_prompts_in_audit};
}

function formDefaults(context, formId, commandId) {
  const all = context.aiFormCommandDefaults ?? {};
  const value = all[formId] ?? all[commandId] ?? all;
  plainObject(value, `${formId} command defaults`);
  noRawSecrets(value, `${formId} command defaults`);
  return value;
}

export function adaptAIFormCommandArguments(commandId, submission, context={}) {
  if(!submission?.formId || !submission.values) return submission;
  const formId = platformValue(submission.formId, 'AI form ID');
  const values = submission.values;
  plainObject(values, `${formId} values`); noRawSecrets(values, `${formId} values`);
  const defaults = formDefaults(context, formId, commandId);
  let result;
  switch(formId) {
  case 'ai.new_session': result = {name: values.name,
    agentProfileRef: reference(values.agent_profile, 'AI agent profile'), mode: values.mode,
    projectId: values.project ? reference(values.project, 'AI project') : null,
    connectorRefs: references(values.connector_set, 'AI connector'),
    contextRefs: references(values.initial_context, 'AI initial context')}; break;
  case 'ai.context_exposure': result = {
    sessionId: requiredDefault(defaults, 'sessionId', formId), context: {
      reference: reference(values.ref, 'AI context reference'),
      exposureLevel: values.level, classification: defaults.classification ?? 'INTERNAL',
      reason: values.reason}}; break;
  case 'ai.connector_test': result = {connectorId: reference(values.connector,
    'AI connector')}; break;
  case 'ai.database_connector': {
    const profile = connectorProfile(values, defaults, formId);
    result = commandId === 'ai.connector.test' ? {connectorId: profile.connectorId} :
      {...asset(defaults, formId, profile), profile}; delete result.content; break;
  }
  case 'ai.mcp_connector': {
    const profile = connectorProfile(values, defaults, formId);
    result = ['ai.connector.test', 'ai.mcp.tools.refresh'].includes(commandId) ?
      {connectorId: profile.connectorId} : {...asset(defaults, formId, profile),
        connectorId: profile.connectorId, profile}; delete result.content; break;
  }
  case 'ai.agent_profile': result = asset(defaults, formId,
    agentProfile(values, defaults, formId, commandId)); break;
  case 'ai.model_provider': {
    const profile = modelProfile(values, defaults, formId, commandId);
    result = commandId === 'ai.model_profile.test' ? {profile} :
      asset(defaults, formId, profile); break;
  }
  case 'ai.tool_policy': case 'ai.data_egress_policy':
  case 'ai.approval_policy': case 'ai.budget_policy':
  case 'ai.retention_policy': result = asset(defaults, formId,
    policyAsset(formId, values, defaults)); break;
  case 'ai.audit_export': result = {from: values.from, to: values.to,
    profiles: references(values.profiles, 'AI audit profile'),
    connectors: references(values.connectors, 'AI audit connector'),
    includeMessageText: values.include_message_text, format: values.format,
    ...(defaults.retentionPolicy ? {retentionPolicy: defaults.retentionPolicy} : {})}; break;
  case 'ai.emergency_controls': result = {reason: platformValue(values.reason,
    'AI emergency reason'), scope: references(values.scope, 'AI emergency scope')}; break;
  case 'ai.high_risk_approval': result = {
    planId: requiredDefault(defaults, 'planId', formId), approval: {
      ...requiredDefault(defaults, 'approval', formId), typedConfirmation: values.typed,
      environment: values.environment, principalRef: values.principal}}; break;
  case 'ai.plan_review': result = {planId: requiredDefault(defaults, 'planId', formId),
    ...(commandId === 'ai.plan.reject' ? {reason: requiredDefault(defaults,
      'reason', formId)} : {}), ...(commandId === 'ai.plan.approve' ? {
      approval: requiredDefault(defaults, 'approval', formId)} : {})}; break;
  case 'ai.query_review': result = {queryId: requiredDefault(defaults,
    'queryId', formId)}; break;
  default: throw new Error(`No command adapter is defined for ${formId}.`);
  }
  noRawSecrets(result, `${formId} command arguments`);
  return immutable(result);
}
