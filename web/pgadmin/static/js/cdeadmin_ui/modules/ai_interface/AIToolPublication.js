/////////////////////////////////////////////////////////////
// Derived CDEadmin command and read-service tool publication.
/////////////////////////////////////////////////////////////

import Ajv2020 from 'ajv/dist/2020';
import {immutable, noRawSecrets, plainObject, platformValue} from '../../platform/serviceUtils';
import {stablePlatformId} from '../../platform/PlatformRegistry';
import {validateAIAsset} from './AIAssetContracts';

export const AI_TOOL_CATALOG_SERVICE_ID = 'cdeadmin.ai_interface.tool_catalog';
const jsonBytes = (value) => new TextEncoder().encode(JSON.stringify(value)).length;
const toolName = (id) => `cdeadmin__${id.replaceAll(/[^a-zA-Z0-9_]/g, '__')}`;
function permissions(user) { return new Set(user?.permissions ?? []); }
function validateSchema(schema, label) {
  if(!schema || Array.isArray(schema) || typeof schema !== 'object') throw new TypeError(
    `${label} must be a JSON Schema object.`);
  noRawSecrets(schema, label);
  try { new Ajv2020({strict: false}).compile(schema); } catch(error) {
    throw new TypeError(`${label} is invalid: ${error.message}`);
  }
  return immutable(JSON.parse(JSON.stringify(schema)));
}
function validateWith(schema, value, label) {
  const validate = new Ajv2020({strict: false}).compile(schema);
  if(!validate(value)) throw new TypeError(`${label} does not satisfy its JSON Schema: ${
    validate.errors.map((item) => `${item.instancePath || '/'} ${item.message}`).join('; ')}`);
}
function policyAllows(command, policy) { return !policy.deniedCommandIds.includes(command.id) &&
  (policy.commandIds.includes(command.id) || policy.moduleIds.includes(command.aiModuleId)); }
function approvalRule(risk, toolPolicy, approvalPolicy) {
  const configured = approvalPolicy.riskRules[risk];
  if(configured === 'auto' && Number(risk.slice(1)) > Number(toolPolicy.maxAutomaticRisk.slice(1)))
    return 'review'; return configured;
}
function commandTool(command, toolPolicy, approvalPolicy) {
  return immutable({schema: 'cdeadmin.ai-tool-descriptor.v1', toolName: toolName(command.id),
    kind: 'cdeadmin_command', commandId: command.id, description: command.description,
    inputSchema: validateSchema(command.aiArgumentSchema, `${command.id} input schema`),
    outputSchema: validateSchema(command.aiResultSchema, `${command.id} output schema`),
    exposure: command.aiExposure, riskClass: command.aiRiskClass,
    requiredPermissions: [...command.permission], createsTask: Boolean(command.createsTask),
    taskType: command.createsTask || null, approvalRule: approvalRule(
      command.aiRiskClass, toolPolicy, approvalPolicy),
    contextCostHint: command.aiContextCostHint});
}

export class AIReadToolRegistry {
  constructor() { this.tools = new Map(); }
  register(input) {
    plainObject(input, 'AI read-service tool'); noRawSecrets(input, 'AI read-service tool');
    const id = stablePlatformId(input.id, 'AI read-service tool ID');
    if(this.tools.has(id)) throw new Error(`AI read-service tool is already registered: ${id}`);
    if(typeof input.accessCheck !== 'function' || typeof input.execute !== 'function' ||
        typeof input.classify !== 'function') throw new TypeError(
      `AI read-service tool ${id} requires access, execution and classification functions.`);
    const maxRows = input.maxRows; const maxBytes = input.maxBytes;
    if(!Number.isSafeInteger(maxRows) || maxRows < 1 || !Number.isSafeInteger(maxBytes) || maxBytes < 1)
      throw new TypeError(`AI read-service tool ${id} requires positive result bounds.`);
    const value = immutable({schema: 'cdeadmin.ai-read-tool.v1', id,
      moduleId: stablePlatformId(input.moduleId, 'AI read-service module ID'),
      description: platformValue(input.description, 'AI read-service description', 1000),
      inputSchema: validateSchema(input.inputSchema, `${id} input schema`),
      outputSchema: validateSchema(input.outputSchema, `${id} output schema`),
      requiredPermissions: [...new Set((input.requiredPermissions ?? []).map((item) =>
        stablePlatformId(item, 'AI read-service permission')))], maxRows, maxBytes,
      contextCostHint: Number.isSafeInteger(input.contextCostHint) && input.contextCostHint >= 0 ?
        input.contextCostHint : 0, accessCheck: input.accessCheck, execute: input.execute,
      classify: input.classify});
    this.tools.set(id, value); return () => this.tools.delete(id);
  }
  get(id) { const value = this.tools.get(stablePlatformId(id, 'AI read-service tool ID'));
    if(!value) throw new Error(`Unknown AI read-service tool: ${id}`); return value; }
  published(context={}, effectiveApprovalRule='auto') { const granted = permissions(context.currentUser);
    return [...this.tools.values()]
      .filter((item) => item.requiredPermissions.every((permission) => granted.has(permission)) &&
      item.accessCheck(context) === true).map((item) => immutable({schema: 'cdeadmin.ai-tool-descriptor.v1',
        toolName: toolName(item.id), kind: 'read_service', commandId: null, readToolId: item.id,
        description: item.description, inputSchema: item.inputSchema, outputSchema: item.outputSchema,
        exposure: 'read_only', riskClass: 'R1', requiredPermissions: item.requiredPermissions,
        createsTask: false, taskType: null, approvalRule: effectiveApprovalRule,
        contextCostHint: item.contextCostHint,
        maxRows: item.maxRows, maxBytes: item.maxBytes})); }
  async invoke(id, args, context={}) { const item = this.get(id); const granted = permissions(context.currentUser);
    if(!item.requiredPermissions.every((permission) => granted.has(permission)) ||
        item.accessCheck(context) !== true) throw new Error(`AI read-service access denied: ${id}`);
    noRawSecrets(args, `AI read-service ${id} arguments`); validateWith(item.inputSchema, args,
      `AI read-service ${id} arguments`); const result = await item.execute(immutable({...args}), context);
    noRawSecrets(result, `AI read-service ${id} result`); validateWith(item.outputSchema, result,
      `AI read-service ${id} result`); const rows = Array.isArray(result?.rows) ? result.rows.length : 0;
    if(rows > item.maxRows || jsonBytes(result) > item.maxBytes) throw new Error(
      `AI read-service ${id} exceeded its declared result bound.`);
    const classification = item.classify(result, context);
    if(typeof classification !== 'string' || !classification) throw new TypeError(
      `AI read-service ${id} returned no classification.`);
    return immutable({schema: 'cdeadmin.ai-read-result.v1', toolId: id,
      classification, value: result, rowCount: rows, byteCount: jsonBytes(result)});
  }
}

export class AIToolCatalog {
  constructor({commands, readTools=new AIReadToolRegistry(), authorization}={}) {
    if(!commands || typeof commands.list !== 'function' || typeof commands.resolve !== 'function' ||
        typeof commands.execute !== 'function') throw new TypeError('AI Tool Catalog requires CommandRegistry.');
    if(!(readTools instanceof AIReadToolRegistry)) throw new TypeError('AI read-tool registry is invalid.');
    if(!authorization || typeof authorization.assertExecutionDecision !== 'function') throw new TypeError(
      'AI Tool Catalog requires deterministic authorization.');
    this.commands = commands; this.readTools = readTools; this.authorization = authorization;
  }
  publish({currentUser, toolPolicy: inputToolPolicy, approvalPolicy: inputApprovalPolicy,
    commandCustomizations, ...toolContext}={}) {
    const toolPolicy = validateAIAsset('AIToolPolicy', inputToolPolicy);
    const approvalPolicy = validateAIAsset('AIApprovalPolicy', inputApprovalPolicy); const result = [];
    for(const descriptor of this.commands.list()) {
      if(descriptor.aiExposure === 'hidden' || descriptor.aiRiskClass === 'R7' ||
          !policyAllows(descriptor, toolPolicy)) continue;
      const resolved = this.commands.resolve(descriptor.id, {currentUser, commandCustomizations});
      if(resolved.visible && resolved.enabled) result.push(commandTool(resolved, toolPolicy, approvalPolicy));
    }
    result.push(...this.readTools.published({currentUser, ...toolContext}, approvalRule(
      'R1', toolPolicy, approvalPolicy)));
    return immutable(result.sort((left, right) => left.toolName.localeCompare(right.toolName)));
  }
  async invokeCommand(commandId, args, decision, context={}) {
    commandId = stablePlatformId(commandId, 'AI command ID');
    const command = this.commands.get(commandId); noRawSecrets(args, `AI command ${commandId} arguments`);
    validateWith(command.aiArgumentSchema, args, `AI command ${commandId} arguments`);
    this.authorization.assertExecutionDecision(decision, commandId, args);
    const result = await this.commands.execute(commandId, args, context);
    noRawSecrets(result, `AI command ${commandId} result`); validateWith(command.aiResultSchema, result,
      `AI command ${commandId} result`); return immutable(result);
  }
}
