##########################################################################
#
# CDEadmin AI Interface project-asset validation.
#
##########################################################################

"""Strict server-side validation for governed AI Interface assets."""

import math


class AIInterfaceAssetValidationError(ValueError):
    """An AI Interface asset does not satisfy its normative contract."""


AI_INTERFACE_ASSET_TYPES = {
    'cdeadmin.ai_interface.model_profile.v1': 'AIModelProfile',
    'cdeadmin.ai_interface.connector_profile.v1': 'AIConnectorProfile',
    'cdeadmin.ai_interface.agent_profile.v1': 'AIAgentProfile',
    'cdeadmin.ai_interface.tool_policy.v1': 'AIToolPolicy',
    'cdeadmin.ai_interface.data_policy.v1': 'AIDataPolicy',
    'cdeadmin.ai_interface.approval_policy.v1': 'AIApprovalPolicy',
    'cdeadmin.ai_interface.budget_policy.v1': 'AIBudgetPolicy',
    'cdeadmin.ai_interface.retention_policy.v1': 'AIRetentionPolicy',
    'cdeadmin.ai_interface.instruction_asset.v1': 'AIInstructionAsset',
    'cdeadmin.ai_interface.plan.v1': 'AIPlan',
}
CLASSIFICATIONS = {
    'PUBLIC', 'INTERNAL', 'CONFIDENTIAL', 'RESTRICTED', 'REGULATED',
    'SECRET_REFERENCE_ONLY',
}
CONNECTOR_CLASSES = {
    'cdeadmin_capability', 'database_provider', 'scratchbird_sbsql',
    'scratchbird_compatibility', 'scratchbird_mcp', 'mcp_generic',
    'external_service',
}
CONNECTOR_STATES = {
    'unconfigured', 'disabled', 'validating', 'ready', 'degraded',
    'reauthorization_required', 'permission_failed',
    'version_incompatible', 'unreachable', 'revoked', 'error',
}
STEP_KINDS = {
    'READ_METADATA', 'READ_DATA', 'COMPILE_QUERY', 'EXECUTE_QUERY',
    'CREATE_ASSET_DRAFT', 'EDIT_ASSET', 'INVOKE_COMMAND', 'START_TASK',
    'WAIT_TASK', 'VALIDATE', 'REQUEST_APPROVAL',
}
STEP_STATES = {
    'pending', 'validated', 'approval_required', 'running', 'succeeded',
    'failed', 'skipped', 'cancelled',
}


def _object(value, label):
    if not isinstance(value, dict):
        raise AIInterfaceAssetValidationError(f'{label} must be an object')
    return value


def _exact(value, fields, label):
    unknown = set(value).difference(fields)
    if unknown:
        raise AIInterfaceAssetValidationError(
            f'{label} contains unsupported field {sorted(unknown)[0]}'
        )


def _text(value, label, maximum=8192, optional=False):
    if optional and value is None:
        return
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise AIInterfaceAssetValidationError(f'{label} is invalid')


def _boolean(value, label):
    if not isinstance(value, bool):
        raise AIInterfaceAssetValidationError(f'{label} must be boolean')


def _integer(value, label, minimum=1, optional=False):
    if optional and value is None:
        return
    if (isinstance(value, bool) or not isinstance(value, int) or
            value < minimum):
        raise AIInterfaceAssetValidationError(f'{label} is invalid')


def _number(value, label, optional=False):
    if optional and value is None:
        return
    if (isinstance(value, bool) or not isinstance(value, (int, float)) or
            not math.isfinite(value) or value < 0):
        raise AIInterfaceAssetValidationError(f'{label} is invalid')


def _choice(value, choices, label):
    if value not in choices:
        raise AIInterfaceAssetValidationError(f'{label} is invalid')


def _strings(value, label):
    if not isinstance(value, list) or len(value) > 10000:
        raise AIInterfaceAssetValidationError(
            f'{label} must be a bounded array'
        )
    for item in value:
        _text(item, f'{label} value', 2048)
    if len(value) != len(set(value)):
        raise AIInterfaceAssetValidationError(f'{label} contains duplicates')


def _base(value, fields, label):
    _object(value, label)
    _exact(value, fields, label)
    if value.get('schemaVersion') != 1:
        raise AIInterfaceAssetValidationError(
            f'{label} schema version is invalid'
        )


def _model(value):
    fields = {
        'schemaVersion', 'profileId', 'displayName', 'runtimeClass',
        'endpointRef', 'modelId', 'credentialRef', 'toolCallingSupport',
        'structuredOutputSupport', 'contextLimit', 'outputLimit',
        'streamingSupport', 'dataResidency', 'retentionStatement',
        'approvedClassificationMax', 'costModel', 'enabled',
    }
    _base(value, fields, 'AI model profile')
    _text(value.get('profileId'), 'AI model profile ID', 256)
    _text(value.get('displayName'), 'AI model profile name', 120)
    _choice(value.get('runtimeClass'), {'local', 'self_hosted', 'remote'},
            'AI model runtime class')
    _text(value.get('endpointRef'), 'AI model endpoint', 2048, optional=True)
    _text(value.get('modelId'), 'AI model ID', 512)
    _text(value.get('credentialRef'), 'AI model credential reference', 2048,
          optional=True)
    for field in ('toolCallingSupport', 'structuredOutputSupport',
                  'streamingSupport', 'enabled'):
        _boolean(value.get(field), f'AI model {field}')
    _integer(
        value.get('contextLimit'), 'AI model context limit', optional=True
    )
    _integer(value.get('outputLimit'), 'AI model output limit', optional=True)
    _text(value.get('dataResidency'), 'AI model data residency', 4000)
    _text(value.get('retentionStatement'), 'AI model retention', 16000)
    _choice(value.get('approvedClassificationMax'), CLASSIFICATIONS,
            'AI model classification')
    if value.get('costModel') is not None:
        _object(value['costModel'], 'AI model cost model')


def _connector(value):
    fields = {
        'schemaVersion', 'connectorId', 'name', 'connectorClass', 'enabled',
        'providerId', 'connectionProfileRef', 'dialectId',
        'workareaSchemaRef', 'mcpEndpointRef', 'mcpProtocolProfile',
        'principalBinding', 'credentialRef', 'policyRef',
        'resourceScopeRefs', 'state', 'capabilitySnapshotRef',
    }
    _base(value, fields, 'AI connector profile')
    for field in ('connectorId', 'name', 'principalBinding', 'policyRef'):
        _text(value.get(field), f'AI connector {field}', 2048)
    _choice(value.get('connectorClass'), CONNECTOR_CLASSES,
            'AI connector class')
    _choice(value.get('state'), CONNECTOR_STATES, 'AI connector state')
    _boolean(value.get('enabled'), 'AI connector enabled state')
    for field in ('providerId', 'connectionProfileRef', 'dialectId',
                  'workareaSchemaRef', 'mcpEndpointRef',
                  'mcpProtocolProfile', 'credentialRef',
                  'capabilitySnapshotRef'):
        _text(value.get(field), f'AI connector {field}', 2048, optional=True)
    _strings(value.get('resourceScopeRefs', []), 'AI connector resources')
    connector_class = value['connectorClass']
    if connector_class in {
            'database_provider', 'scratchbird_sbsql',
            'scratchbird_compatibility'} and not all(
                value.get(field) for field in (
                    'providerId', 'connectionProfileRef', 'credentialRef')):
        raise AIInterfaceAssetValidationError(
            'database connector references are incomplete'
        )
    if (connector_class == 'scratchbird_sbsql' and
            value.get('dialectId') != 'sbsql'):
        raise AIInterfaceAssetValidationError(
            'ScratchBird native connector dialect is invalid'
        )
    if (connector_class == 'scratchbird_compatibility' and
            (not value.get('dialectId') or
             not value.get('workareaSchemaRef'))):
        raise AIInterfaceAssetValidationError(
            'ScratchBird compatibility connector is incomplete'
        )
    if connector_class in {'scratchbird_mcp', 'mcp_generic'} and not all(
            value.get(field) for field in (
                'mcpEndpointRef', 'mcpProtocolProfile')):
        raise AIInterfaceAssetValidationError('MCP connector is incomplete')


def _agent(value):
    fields = {
        'schemaVersion', 'profileId', 'name', 'description', 'enabled',
        'autonomyMode', 'modelProfileRef', 'connectorRefs', 'toolPolicyRef',
        'dataPolicyRef', 'approvalPolicyRef', 'budgetPolicyRef',
        'retentionPolicyRef', 'instructionAssetRef', 'tags',
    }
    _base(value, fields, 'AI agent profile')
    for field in ('profileId', 'name', 'modelProfileRef', 'toolPolicyRef',
                  'dataPolicyRef', 'approvalPolicyRef', 'budgetPolicyRef',
                  'retentionPolicyRef'):
        _text(value.get(field), f'AI agent {field}', 2048)
    _text(value.get('description'), 'AI agent description', 4000,
          optional=True)
    _text(value.get('instructionAssetRef'), 'AI agent instruction', 2048,
          optional=True)
    _boolean(value.get('enabled'), 'AI agent enabled state')
    _choice(value.get('autonomyMode'), {
        'ASK_ONLY', 'DRAFT_ONLY', 'READ_ONLY_TOOLS', 'BOUNDED_EXECUTION',
    }, 'AI agent autonomy')
    _strings(value.get('connectorRefs'), 'AI agent connectors')
    _strings(value.get('tags', []), 'AI agent tags')


def _tool_policy(value):
    fields = {
        'schemaVersion', 'policyId', 'name', 'moduleIds', 'commandIds',
        'deniedCommandIds', 'maxAutomaticRisk', 'allowProjectDrafts',
        'allowBackgroundTasks',
    }
    _base(value, fields, 'AI tool policy')
    _text(value.get('policyId'), 'AI tool policy ID', 256)
    _text(value.get('name'), 'AI tool policy name', 120)
    for field in ('moduleIds', 'commandIds', 'deniedCommandIds'):
        _strings(value.get(field), f'AI tool policy {field}')
    _choice(value.get('maxAutomaticRisk'), {'R0', 'R1'},
            'AI tool automatic risk')
    _boolean(value.get('allowProjectDrafts'), 'AI project-draft policy')
    _boolean(value.get('allowBackgroundTasks'), 'AI background policy')


def _query_policy(value):
    fields = {
        'allowRead', 'allowWrite', 'allowDDL', 'allowTransactionControl',
        'allowExplain', 'allowSystemCatalog', 'allowCrossSurface', 'maxRows',
        'maxResultBytes', 'maxStatementSeconds', 'maxStatementsPerPlan',
        'maxParallelQueries', 'allowedResourceRefs', 'blockedResourceRefs',
        'allowTemporaryObjects', 'allowStoredProcedureCall',
        'allowExternalSideEffectFunctions',
    }
    _object(value, 'AI query policy')
    _exact(value, fields, 'AI query policy')
    for field in (
            'allowRead', 'allowWrite', 'allowDDL',
            'allowTransactionControl', 'allowExplain',
            'allowSystemCatalog', 'allowCrossSurface',
            'allowTemporaryObjects', 'allowStoredProcedureCall',
            'allowExternalSideEffectFunctions'):
        _boolean(value.get(field), f'AI query {field}')
    for field in ('maxRows', 'maxResultBytes', 'maxStatementSeconds',
                  'maxStatementsPerPlan', 'maxParallelQueries'):
        _integer(value.get(field), f'AI query {field}')
    for field in ('allowedResourceRefs', 'blockedResourceRefs'):
        _strings(value.get(field), f'AI query {field}')
    if value['allowCrossSurface'] and not value['allowRead']:
        raise AIInterfaceAssetValidationError(
            'AI cross-surface querying requires read access'
        )


def _data_policy(value):
    fields = {
        'schemaVersion', 'policyId', 'name', 'defaultExposureLevel',
        'maxClassification', 'allowRemoteSamples', 'allowRemoteSource',
        'sensitiveColumnHandling', 'maxSampleRows', 'maxTextCharacters',
        'allowedModelProfileRefs', 'requireLocalForRestricted', 'queryPolicy',
    }
    _base(value, fields, 'AI data policy')
    _text(value.get('policyId'), 'AI data policy ID', 256)
    _text(value.get('name'), 'AI data policy name', 120)
    _choice(value.get('defaultExposureLevel'), {
        'IDENTITY_ONLY', 'METADATA_SUMMARY', 'SCHEMA', 'STATISTICS',
        'SAFE_SAMPLE', 'FULL_ALLOWED_CONTENT',
    }, 'AI exposure level')
    _choice(value.get('maxClassification'), CLASSIFICATIONS,
            'AI data classification')
    for field in ('allowRemoteSamples', 'allowRemoteSource',
                  'requireLocalForRestricted'):
        _boolean(value.get(field), f'AI data {field}')
    _choice(value.get('sensitiveColumnHandling'), {
        'excluded', 'masked', 'tokenized', 'aggregated_only', 'allowed',
    }, 'AI sensitive-column handling')
    _integer(value.get('maxSampleRows'), 'AI sample rows', minimum=0)
    _integer(value.get('maxTextCharacters'), 'AI text characters')
    _strings(value.get('allowedModelProfileRefs'), 'AI allowed models')
    _query_policy(value.get('queryPolicy'))


def _approval_policy(value):
    fields = {'schemaVersion', 'policyId', 'name', 'riskRules',
              'expiryMinutes'}
    _base(value, fields, 'AI approval policy')
    _text(value.get('policyId'), 'AI approval policy ID', 256)
    _text(value.get('name'), 'AI approval policy name', 120)
    rules = _object(value.get('riskRules'), 'AI approval risk rules')
    choices = {
        'R0': {'auto', 'review', 'forbid'},
        'R1': {'auto', 'review', 'forbid'},
        'R2': {'auto', 'review_diff', 'review', 'forbid'},
        'R3': {'review', 'forbid'},
        'R4': {'confirm_each', 'dual_approval', 'forbid'},
        'R5': {'typed_confirm', 'dual_approval', 'forbid'},
        'R6': {'typed_confirm', 'dual_approval', 'dual_or_typed', 'forbid'},
    }
    _exact(rules, set(choices), 'AI approval risk rules')
    for risk, allowed in choices.items():
        _choice(rules.get(risk), allowed, f'AI approval {risk} rule')
    expiry = _object(value.get('expiryMinutes'), 'AI approval expiry')
    _exact(expiry, {'R4', 'R5', 'R6'}, 'AI approval expiry')
    for risk in ('R4', 'R5', 'R6'):
        _integer(expiry.get(risk), f'AI approval {risk} expiry')


def _budget_policy(value):
    fields = {
        'schemaVersion', 'policyId', 'name', 'maxModelInputTokens',
        'maxModelOutputTokens', 'maxToolCallsPerTurn', 'maxToolCallsPerPlan',
        'maxDatabaseQueriesPerTurn', 'maxDatabaseRowsPerQuery',
        'maxDatabaseBytesPerQuery', 'maxParallelTools', 'maxRunMinutes',
        'maxBackgroundTasks', 'maxEstimatedCostPerTurn',
        'maxEstimatedCostPerDay',
    }
    _base(value, fields, 'AI budget policy')
    missing = fields.difference(value)
    if missing:
        raise AIInterfaceAssetValidationError(
            f'AI budget policy requires explicit {sorted(missing)[0]}'
        )
    _text(value.get('policyId'), 'AI budget policy ID', 256)
    _text(value.get('name'), 'AI budget policy name', 120)
    for field in fields.difference({
            'schemaVersion', 'policyId', 'name', 'maxEstimatedCostPerTurn',
            'maxEstimatedCostPerDay'}):
        _integer(value.get(field), f'AI budget {field}', optional=True)
    for field in ('maxEstimatedCostPerTurn', 'maxEstimatedCostPerDay'):
        _number(value.get(field), f'AI budget {field}', optional=True)


def _retention_policy(value):
    fields = {
        'schemaVersion', 'policyId', 'name', 'retainMessages',
        'conversationDays', 'retainToolResults', 'auditDays',
        'retainPromptsInAudit',
    }
    _base(value, fields, 'AI retention policy')
    _text(value.get('policyId'), 'AI retention policy ID', 256)
    _text(value.get('name'), 'AI retention policy name', 120)
    for field in ('retainMessages', 'retainToolResults',
                  'retainPromptsInAudit'):
        _boolean(value.get(field), f'AI retention {field}')
    _integer(value.get('conversationDays'), 'AI conversation retention',
             optional=not value.get('retainMessages'))
    _integer(value.get('auditDays'), 'AI audit retention')


def _instruction(value):
    fields = {
        'schemaVersion', 'instructionId', 'name', 'scope', 'instructions',
        'locked', 'versionNote',
    }
    _base(value, fields, 'AI instruction asset')
    _text(value.get('instructionId'), 'AI instruction ID', 256)
    _text(value.get('name'), 'AI instruction name', 120)
    _choice(value.get('scope'), {'user', 'project', 'team', 'organization'},
            'AI instruction scope')
    _text(value.get('instructions'), 'AI instructions', 262144)
    _boolean(value.get('locked'), 'AI instruction locked state')
    _text(value.get('versionNote'), 'AI instruction version note', 500,
          optional=True)


def _plan_step(value):
    fields = {
        'stepId', 'kind', 'status', 'commandId', 'connectorRef',
        'resourceRefs', 'assetRefs', 'arguments',
    }
    _object(value, 'AI plan step')
    _exact(value, fields, 'AI plan step')
    _text(value.get('stepId'), 'AI plan step ID', 256)
    _choice(value.get('kind'), STEP_KINDS, 'AI plan step kind')
    _choice(value.get('status'), STEP_STATES, 'AI plan step state')
    _text(value.get('commandId'), 'AI plan command ID', 256, optional=True)
    _text(value.get('connectorRef'), 'AI plan connector', 256, optional=True)
    _strings(value.get('resourceRefs', []), 'AI plan resources')
    _strings(value.get('assetRefs', []), 'AI plan assets')
    _object(value.get('arguments'), 'AI plan arguments')


def _plan(value):
    fields = {
        'schemaVersion', 'planId', 'revision', 'baseContextRevision',
        'agentProfileRef', 'modelProfileSnapshot', 'connectorSnapshots',
        'steps', 'risks', 'requiredApprovals', 'validationChecks', 'status',
    }
    _base(value, fields, 'AI plan')
    _text(value.get('planId'), 'AI plan ID', 256)
    _integer(value.get('revision'), 'AI plan revision')
    _text(value.get('baseContextRevision'), 'AI base-context revision', 2048)
    _text(value.get('agentProfileRef'), 'AI agent profile reference', 256)
    _object(value.get('modelProfileSnapshot'), 'AI model profile snapshot')
    snapshots = value.get('connectorSnapshots')
    if not isinstance(snapshots, list):
        raise AIInterfaceAssetValidationError(
            'AI connector snapshots must be an array'
        )
    for snapshot in snapshots:
        _object(snapshot, 'AI connector snapshot')
    steps = value.get('steps')
    if not isinstance(steps, list):
        raise AIInterfaceAssetValidationError('AI plan steps must be an array')
    for step in steps:
        _plan_step(step)
    step_ids = [step.get('stepId') for step in steps]
    if len(step_ids) != len(set(step_ids)):
        raise AIInterfaceAssetValidationError(
            'AI plan step IDs must be unique'
        )
    for field in ('risks', 'requiredApprovals', 'validationChecks'):
        _strings(value.get(field), f'AI plan {field}')
    _choice(value.get('status'), {
        'draft', 'validating', 'invalid', 'ready_for_approval', 'approved',
        'running', 'succeeded', 'failed', 'cancelled', 'stale',
    }, 'AI plan state')


VALIDATORS = {
    'AIModelProfile': _model,
    'AIConnectorProfile': _connector,
    'AIAgentProfile': _agent,
    'AIToolPolicy': _tool_policy,
    'AIDataPolicy': _data_policy,
    'AIApprovalPolicy': _approval_policy,
    'AIBudgetPolicy': _budget_policy,
    'AIRetentionPolicy': _retention_policy,
    'AIInstructionAsset': _instruction,
    'AIPlan': _plan,
}


def validate_ai_interface_asset(
        asset_type, schema_name, schema_version, value):
    """Validate and return one exact AI Interface asset value."""
    kind = AI_INTERFACE_ASSET_TYPES.get(asset_type)
    if kind is None:
        return None
    if schema_name != asset_type:
        raise AIInterfaceAssetValidationError(
            'AI Interface asset schema name is invalid'
        )
    if schema_version != 1:
        raise AIInterfaceAssetValidationError(
            'AI Interface asset schema version is invalid'
        )
    VALIDATORS[kind](value)
    return value
