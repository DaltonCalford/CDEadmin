/////////////////////////////////////////////////////////////
// CDEadmin AI emergency-state and active-run control authority.
/////////////////////////////////////////////////////////////

import {immutable, platformValue} from '../../platform/serviceUtils';
import {AI_RISK_CLASS_IDS, aiActor, boundedStrings, exactLifecycleObject} from './AILifecycleContracts';

function scopeValues(value, label) { return new Set(boundedStrings(value ?? [], label)); }

export class AIEmergencyControlService {
  constructor({audit=null, now=() => new Date().toISOString()}={}) {
    this.audit = audit; this.now = now; this.approvalRevision = 0;
    this.toolCatalogRevision = 0; this.reauthorizationRevision = 0;
    this.state = {moduleEnabled: true, writesEnabled: true, standingApprovalsEnabled: true,
      disabledModelRefs: new Set(), revokedAgentRefs: new Set(),
      revokedCredentialRefs: new Set(), disabledCommandIds: new Set()};
    this.activeRuns = new Map();
  }

  approvalEpoch() { return this.approvalRevision; }

  snapshot() {
    return immutable({schema: 'cdeadmin.ai-emergency-state.v1',
      moduleEnabled: this.state.moduleEnabled, writesEnabled: this.state.writesEnabled,
      standingApprovalsEnabled: this.state.standingApprovalsEnabled,
      disabledModelRefs: [...this.state.disabledModelRefs].sort(),
      revokedAgentRefs: [...this.state.revokedAgentRefs].sort(),
      revokedCredentialRefs: [...this.state.revokedCredentialRefs].sort(),
      disabledCommandIds: [...this.state.disabledCommandIds].sort(),
      approvalRevision: this.approvalRevision, toolCatalogRevision: this.toolCatalogRevision,
      reauthorizationRevision: this.reauthorizationRevision});
  }

  _admin(context) { return aiActor(context, 'ai.admin'); }
  _reason(value) { return platformValue(value, 'AI emergency-control reason', 2000); }
  _record(eventType, actor, reason, details={}) {
    return this.audit?.append({eventType, initiator: actor.id,
      diagnostics: [{message: reason, ...details}]}) ?? null;
  }

  configure(input, context={}) {
    exactLifecycleObject(input, ['moduleEnabled', 'writesEnabled', 'standingApprovalsEnabled',
      'reason'], 'AI emergency configuration');
    const actor = this._admin(context); const reason = this._reason(input.reason);
    for(const field of ['moduleEnabled', 'writesEnabled', 'standingApprovalsEnabled']) if(
      typeof input[field] !== 'boolean') throw new TypeError(`${field} must be boolean.`);
    const approvalsWereRestricted = this.state.standingApprovalsEnabled &&
      !input.standingApprovalsEnabled || this.state.writesEnabled && !input.writesEnabled ||
      this.state.moduleEnabled && !input.moduleEnabled;
    this.state.moduleEnabled = input.moduleEnabled; this.state.writesEnabled = input.writesEnabled;
    this.state.standingApprovalsEnabled = input.standingApprovalsEnabled;
    if(approvalsWereRestricted) this.approvalRevision++;
    this._record('ai.emergency.configured', actor, reason, {state: this.snapshot()});
    return this.snapshot();
  }

  disableAll(input, context={}) {
    const actor = this._admin(context); const reason = this._reason(input?.reason);
    this.state.moduleEnabled = false; this.approvalRevision++; this.reauthorizationRevision++;
    const killed = this._killRuns(input?.scope ?? [], reason);
    this._record('ai.emergency.disable_all', actor, reason, {killed}); return this.snapshot();
  }

  disableWrites(input, context={}) {
    const actor = this._admin(context); const reason = this._reason(input?.reason);
    this.state.writesEnabled = false; this.approvalRevision++;
    this._record('ai.emergency.disable_writes', actor, reason); return this.snapshot();
  }

  invalidateApprovals(input, context={}) {
    const actor = this._admin(context); const reason = this._reason(input?.reason);
    this.approvalRevision++;
    this._record('ai.emergency.invalidate_approvals', actor, reason,
      {approvalRevision: this.approvalRevision}); return this.snapshot();
  }

  setModelsDisabled(modelRefs, disabled, reason, context={}) {
    const actor = this._admin(context); reason = this._reason(reason);
    const values = scopeValues(modelRefs, 'AI emergency model references');
    values.forEach((item) => disabled ? this.state.disabledModelRefs.add(item) :
      this.state.disabledModelRefs.delete(item));
    if(disabled) this.approvalRevision++;
    this._record('ai.emergency.model_state', actor, reason, {modelRefs: [...values], disabled});
    return this.snapshot();
  }

  setAgentsRevoked(agentRefs, revoked, reason, context={}) {
    const actor = this._admin(context); reason = this._reason(reason);
    const values = scopeValues(agentRefs, 'AI emergency agent references');
    values.forEach((item) => revoked ? this.state.revokedAgentRefs.add(item) :
      this.state.revokedAgentRefs.delete(item));
    if(revoked) this.approvalRevision++;
    this._record('ai.emergency.agent_state', actor, reason, {agentRefs: [...values], revoked});
    return this.snapshot();
  }

  setCredentialsRevoked(credentialRefs, revoked, reason, context={}) {
    const actor = this._admin(context); reason = this._reason(reason);
    const values = scopeValues(credentialRefs, 'AI emergency credential references');
    values.forEach((item) => revoked ? this.state.revokedCredentialRefs.add(item) :
      this.state.revokedCredentialRefs.delete(item));
    if(revoked) { this.approvalRevision++; this.reauthorizationRevision++; }
    this._record('ai.emergency.credential_state', actor, reason,
      {credentialRefs: [...values], revoked}); return this.snapshot();
  }

  setCommandsDisabled(commandIds, disabled, reason, context={}) {
    const actor = this._admin(context); reason = this._reason(reason);
    const values = scopeValues(commandIds, 'AI emergency command IDs');
    values.forEach((item) => disabled ? this.state.disabledCommandIds.add(item) :
      this.state.disabledCommandIds.delete(item));
    if(disabled) this.approvalRevision++;
    this._record('ai.emergency.command_state', actor, reason,
      {commandIds: [...values], disabled}); return this.snapshot();
  }

  clearToolCatalogs(clear, reason, context={}) {
    const actor = this._admin(context); reason = this._reason(reason);
    if(typeof clear !== 'function') throw new TypeError('AI tool-catalog clear callback is required.');
    clear(); this.toolCatalogRevision++;
    this._record('ai.emergency.tool_catalogs_cleared', actor, reason,
      {toolCatalogRevision: this.toolCatalogRevision}); return this.snapshot();
  }

  forceReauthorization(reason, context={}) {
    const actor = this._admin(context); reason = this._reason(reason);
    this.reauthorizationRevision++; this.approvalRevision++;
    this._record('ai.emergency.reauthorization_required', actor, reason,
      {reauthorizationRevision: this.reauthorizationRevision}); return this.snapshot();
  }

  registerRun(runId, cancel, scopeRefs=[]) {
    runId = platformValue(runId, 'AI run ID');
    if(typeof cancel !== 'function') throw new TypeError('AI run cancel callback is required.');
    if(this.activeRuns.has(runId)) throw new Error(`AI run is already registered: ${runId}`);
    this.activeRuns.set(runId, {cancel, scopeRefs: new Set(boundedStrings(scopeRefs,
      'AI run scope references'))}); return () => this.activeRuns.delete(runId);
  }

  killRuns(input, context={}) {
    const actor = this._admin(context); const reason = this._reason(input?.reason);
    const killed = this._killRuns(input?.scope ?? [], reason);
    this._record('ai.emergency.runs_killed', actor, reason, {killed}); return immutable(killed);
  }

  _killRuns(scope, reason) {
    const values = scopeValues(scope, 'AI emergency run scope'); const killed = [];
    for(const [id, run] of this.activeRuns.entries()) if(!values.size ||
      [...values].some((item) => run.scopeRefs.has(item))) {
      run.cancel(reason); killed.push(id);
    }
    return killed;
  }

  authorizationCheck({operation, context}={}) {
    const op = operation ?? {}; const connector = context?.connectorProfile ?? {};
    const agent = context?.agentProfile ?? {}; const model = context?.modelProfile ?? {};
    const denied = !this.state.moduleEnabled ? 'The AI module is disabled.' :
      this.state.disabledModelRefs.has(model.profileId) ? 'The model profile is disabled.' :
        this.state.revokedAgentRefs.has(agent.profileId) ? 'The agent profile is revoked.' :
          this.state.revokedCredentialRefs.has(connector.credentialRef) ?
            'The connector credential is revoked.' : this.state.disabledCommandIds.has(op.commandId) ?
              'The command is disabled for AI.' : !this.state.writesEnabled &&
        AI_RISK_CLASS_IDS.indexOf(op.riskClass) >= AI_RISK_CLASS_IDS.indexOf('R4') ?
                'AI live writes are disabled.' : context?.reauthorizationRevision != null &&
        context.reauthorizationRevision !== this.reauthorizationRevision ?
                  'AI reauthorization is required.' : null;
    return immutable({allowed: denied == null, reason: denied ?? '',
      evidenceRef: `ai-emergency-state:${this.reauthorizationRevision}:${this.approvalRevision}`});
  }
}
