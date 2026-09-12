/////////////////////////////////////////////////////////////
//
// CDEadmin - Multi-engine Database Administration
//
// Copyright (C) 2013 - 2026, The pgAdmin Development Team
// This software is released under the PostgreSQL Licence
//
//////////////////////////////////////////////////////////////

const COMMAND_ID = /^[a-z][a-z0-9]*(?:[._:-][a-z0-9]+)*$/;
const FORBIDDEN_ARGUMENT = /(?:password|passwd|secret|credential|private.?key|access.?token|refresh.?token|csrf)/i;

export const COMMAND_SCHEMA = 'cdeadmin.command.v1';
export const MACRO_SCHEMA = 'cdeadmin.command-macro.v1';
export const AI_COMMAND_EXPOSURES = Object.freeze([
  'hidden', 'read_only', 'draft_only', 'executable',
]);
export const AI_COMMAND_RISKS = Object.freeze([
  'R0', 'R1', 'R2', 'R3', 'R4', 'R5', 'R6', 'R7',
]);

export class CommandError extends Error {
  constructor(code, message, commandId) {
    super(message);
    this.name = 'CommandError';
    this.code = code;
    this.commandId = commandId;
  }
}

function stringArray(value) {
  if(value === undefined || value === null || value === '') return [];
  return Object.freeze((Array.isArray(value) ? value : [value])
    .map((item)=>String(item).trim()).filter(Boolean));
}

function predicate(value, context, defaultValue) {
  if(typeof value === 'function') return Boolean(value(context));
  return value === undefined ? defaultValue : Boolean(value);
}

function assertSafeValue(value, path='arguments', seen=new WeakSet()) {
  if(value === undefined || value === null) return;
  if(['string', 'number', 'boolean'].includes(typeof value)) return;
  if(typeof value !== 'object') {
    throw new CommandError('invalid_arguments', `${path} is not JSON-safe.`);
  }
  if(seen.has(value)) {
    throw new CommandError('invalid_arguments', `${path} contains a cycle.`);
  }
  seen.add(value);
  if(Array.isArray(value)) {
    value.forEach((item, index)=>assertSafeValue(item, `${path}[${index}]`, seen));
  } else {
    Object.entries(value).forEach(([key, child])=>{
      if(FORBIDDEN_ARGUMENT.test(key) && !['credentialRef', 'credential_ref'].includes(key)) {
        throw new CommandError(
          'sensitive_arguments',
          `Sensitive values are forbidden in command arguments: ${path}.${key}`
        );
      }
      assertSafeValue(child, `${path}.${key}`, seen);
    });
  }
  seen.delete(value);
}

function frozenSchema(value, label, required=false) {
  if(value === undefined || value === null) {
    if(required) throw new TypeError(`${label} is required for AI exposure.`);
    return null;
  }
  if(Array.isArray(value) || typeof value !== 'object') {
    throw new TypeError(`${label} must be a JSON Schema object.`);
  }
  const clone = JSON.parse(JSON.stringify(value));
  const freeze = (item) => {
    if(!item || typeof item !== 'object' || Object.isFrozen(item)) return item;
    Object.values(item).forEach(freeze);
    return Object.freeze(item);
  };
  return freeze(clone);
}

export function createCommandDescriptor(input={}) {
  const id = String(input.id ?? '').trim();
  if(!COMMAND_ID.test(id)) {
    throw new TypeError('Command ID must be a stable, namespaced identifier.');
  }
  if(typeof input.execute !== 'function') {
    throw new TypeError(`Command ${id} requires an executable handler.`);
  }
  const aiExposure = String(input.aiExposure ?? 'hidden');
  if(!AI_COMMAND_EXPOSURES.includes(aiExposure)) {
    throw new TypeError(`Command ${id} has an invalid AI exposure.`);
  }
  const aiExposed = aiExposure !== 'hidden';
  const aiRiskClass = input.aiRiskClass == null ? null : String(input.aiRiskClass);
  if((aiExposed && !AI_COMMAND_RISKS.includes(aiRiskClass)) ||
      (!aiExposed && aiRiskClass !== null && !AI_COMMAND_RISKS.includes(aiRiskClass))) {
    throw new TypeError(`Command ${id} has an invalid AI risk class.`);
  }
  const aiModuleId = String(input.aiModuleId ?? '').trim();
  if(aiExposed && !COMMAND_ID.test(aiModuleId)) {
    throw new TypeError(`Command ${id} requires an AI module identity.`);
  }
  if(aiExposed && !String(input.description ?? '').trim()) {
    throw new TypeError(`Command ${id} requires an AI tool description.`);
  }
  const aiContextCostHint = input.aiContextCostHint ?? 0;
  if(!Number.isSafeInteger(aiContextCostHint) || aiContextCostHint < 0) {
    throw new TypeError(`Command ${id} has an invalid AI context cost hint.`);
  }
  const descriptor = {
    schema: COMMAND_SCHEMA,
    schemaVersion: 1,
    id,
    version: Number.isSafeInteger(input.version) && input.version > 0 ?
      input.version : 1,
    label: String(input.label ?? id),
    description: String(input.description ?? ''),
    iconKey: String(input.iconKey ?? 'command.default'),
    permission: stringArray(input.permission),
    allowedSecurityGroups: stringArray(input.allowedSecurityGroups),
    deniedSecurityGroups: stringArray(input.deniedSecurityGroups),
    defaultEnabled: input.defaultEnabled !== false,
    defaultVisible: input.defaultVisible !== false,
    macroCallable: input.macroCallable !== false,
    aiEligible: input.aiEligible === true,
    aiExposure,
    aiRiskClass,
    aiModuleId,
    aiArgumentSchema: frozenSchema(
      input.aiArgumentSchema, `Command ${id} AI argument schema`, aiExposed
    ),
    aiResultSchema: frozenSchema(
      input.aiResultSchema, `Command ${id} AI result schema`, aiExposed
    ),
    aiContextCostHint,
    auditCategory: String(input.auditCategory ?? 'user_action'),
    authority: String(input.authority ?? 'CommandRegistry'),
    task: Boolean(input.task),
    createsTask: String(input.createsTask ?? ''),
    requiresConfirmation: Boolean(input.requiresConfirmation),
    confirmationIntent: String(input.confirmationIntent ?? (
      input.requiresConfirmation ? 'explicit' : 'none')),
    intent: String(input.intent ?? 'default'),
    surfaces: stringArray(input.surfaces),
    enabledWhen: input.enabledWhen ?? (() => true),
    visibleWhen: input.visibleWhen ?? (() => true),
    checkedWhen: input.checkedWhen,
    validateArguments: input.validateArguments ?? (() => true),
    disabledReason: input.disabledReason ?? 'command_disabled',
    execute: input.execute,
  };
  return Object.freeze(descriptor);
}

function userHasPermission(command, currentUser={}) {
  if(command.permission.length === 0) return true;
  const permissions = new Set(currentUser.permissions ?? []);
  return command.permission.every((permission)=>permissions.has(permission));
}

function userHasSecurityGroup(command, currentUser={}) {
  const groups = new Set(currentUser.security_groups ?? []);
  if(command.deniedSecurityGroups.some((group)=>groups.has(group))) return false;
  return command.allowedSecurityGroups.length === 0 ||
    command.allowedSecurityGroups.some((group)=>groups.has(group));
}

export class CommandRegistry {
  constructor() {
    this.commands = new Map();
    this.listeners = new Set();
  }

  register(input) {
    const command = createCommandDescriptor(input);
    if(this.commands.has(command.id)) {
      throw new Error(`Command is already registered: ${command.id}`);
    }
    this.commands.set(command.id, command);
    return ()=>this.commands.delete(command.id);
  }

  ensure(input) {
    return this.commands.has(input.id) ? this.get(input.id) :
      (this.register(input), this.get(input.id));
  }

  has(id) {
    return this.commands.has(id);
  }

  get(id) {
    const command = this.commands.get(id);
    if(!command) throw new CommandError('not_found', `Unknown command: ${id}`, id);
    return command;
  }

  list() {
    return [...this.commands.values()];
  }

  subscribe(listener) {
    this.listeners.add(listener);
    return ()=>this.listeners.delete(listener);
  }

  resolve(id, context={}) {
    const command = this.get(id);
    const customization = context.commandCustomizations?.[id] ?? {};
    const permitted = userHasPermission(command, context.currentUser) &&
      userHasSecurityGroup(command, context.currentUser);
    const visible = permitted && command.defaultVisible &&
      customization.visible !== false &&
      predicate(command.visibleWhen, context, true);
    const enabled = visible && command.defaultEnabled &&
      customization.enabled !== false &&
      predicate(command.enabledWhen, context, true);
    const explicitReason = typeof command.disabledReason === 'function' ?
      command.disabledReason(context) : command.disabledReason;
    const disabledReason = !permitted ? 'permission_denied' :
      (!visible ? 'command_hidden' : (!enabled ?
        String(explicitReason || 'command_disabled') : ''));
    return Object.freeze({
      ...command,
      label: String(customization.label ?? command.label),
      iconKey: String(customization.iconKey ?? command.iconKey),
      shortcut: customization.shortcut ?? context.shortcut ?? null,
      visible,
      enabled,
      checked: predicate(command.checkedWhen, context, false),
      disabledReason,
    });
  }

  async execute(id, args={}, context={}) {
    const resolved = this.resolve(id, context);
    if(!resolved.visible) {
      throw new CommandError(
        resolved.disabledReason, `Command is not available: ${id}`, id
      );
    }
    if(!resolved.enabled) {
      throw new CommandError('command_disabled', `Command is disabled: ${id}`, id);
    }
    assertSafeValue(args);
    if(typeof resolved.validateArguments === 'function') {
      const validation = resolved.validateArguments(args, context);
      if(validation !== true && validation !== undefined) {
        throw new CommandError(
          'invalid_arguments', String(validation || 'Invalid command arguments.'), id
        );
      }
    }
    this.listeners.forEach((listener)=>listener({type: 'before', command: resolved, args}));
    try {
      const result = await resolved.execute(args, context);
      this.listeners.forEach((listener)=>listener({type: 'after', command: resolved, args, result}));
      return result;
    } catch(error) {
      this.listeners.forEach((listener)=>listener({type: 'error', command: resolved, args, error}));
      throw error;
    }
  }

  async executeMacro(macro, context={}) {
    if(macro?.schema !== MACRO_SCHEMA || !Array.isArray(macro.steps)) {
      throw new CommandError('invalid_macro', 'Invalid command macro.');
    }
    if(macro.steps.length > 1000) {
      throw new CommandError('invalid_macro', 'A macro cannot exceed 1000 steps.');
    }
    const results = [];
    for(const [index, step] of macro.steps.entries()) {
      const command = this.get(step.commandId);
      if(!command.macroCallable) {
        throw new CommandError(
          'macro_forbidden', `Command cannot be called by a macro: ${command.id}`,
          command.id
        );
      }
      results.push(await this.execute(
        command.id, step.arguments ?? {}, {...context, macroStep: index}
      ));
    }
    return results;
  }
}

export const commandRegistry = new CommandRegistry();
