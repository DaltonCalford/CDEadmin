/////////////////////////////////////////////////////////////
// CDEadmin append-only AI audit metadata and separated content authority.
/////////////////////////////////////////////////////////////

import {immutable, plainObject, platformValue, SECRET_KEY} from '../../platform/serviceUtils';
import {aiActor, boundedStrings} from './AILifecycleContracts';

const FIELDS = Object.freeze(['eventType', 'sessionId', 'runId', 'planId', 'initiator',
  'agentProfileRef', 'modelProfileRef', 'connectorRef', 'principalRef', 'commandId',
  'redactedArguments', 'resourceRefs', 'assetRefs', 'approvalRefs', 'backendResult',
  'taskRefs', 'timing', 'usage', 'policyDecisions', 'diagnostics', 'content']);

function redact(value) {
  if(Array.isArray(value)) return value.map(redact);
  if(!value || typeof value !== 'object') return value;
  return Object.fromEntries(Object.entries(value).map(([key, child]) => {
    const numericTokenMeasure = /tokens$/i.test(key) && typeof child === 'number' &&
      Number.isFinite(child);
    return [key, SECRET_KEY.test(key) && !['credentialRef', 'credential_ref',
      'credentialRefs', 'credential_refs'].includes(key) && !numericTokenMeasure ?
      '[REDACTED]' : redact(child)];
  }));
}

function structured(value, label) {
  if(value == null) return null;
  return immutable(redact({...plainObject(value, label)}));
}

function list(value, label) { return boundedStrings(value ?? [], label); }

function csvCell(value) {
  const text = typeof value === 'string' ? value : JSON.stringify(value ?? '');
  return `"${text.replaceAll('"', '""')}"`;
}

export class AIAuditService {
  constructor({now=() => new Date().toISOString()}={}) {
    this.now = now; this.records = []; this.content = new Map(); this.sequence = 0;
  }

  append(input) {
    plainObject(input, 'AI audit event'); const unknown = Object.keys(input).filter((field) =>
      !FIELDS.includes(field)); if(unknown.length) throw new TypeError(
      `AI audit event contains unsupported field ${unknown[0]}.`);
    const auditId = `ai-audit-${++this.sequence}`; const at = this.now();
    const contentValue = structured(input.content, 'AI audit retained content');
    const record = immutable({schema: 'cdeadmin.ai-audit-record.v1', auditId,
      sequence: this.sequence, at, eventType: platformValue(input.eventType,
        'AI audit event type'), sessionId: input.sessionId == null ? null : platformValue(
        input.sessionId, 'AI audit session ID'), runId: input.runId == null ? null : platformValue(
        input.runId, 'AI audit run ID'), planId: input.planId == null ? null : platformValue(
        input.planId, 'AI audit plan ID'), initiator: platformValue(input.initiator ?? 'system',
        'AI audit initiator'), agentProfileRef: input.agentProfileRef == null ? null : platformValue(
        input.agentProfileRef, 'AI audit agent profile'), modelProfileRef:
        input.modelProfileRef == null ? null : platformValue(input.modelProfileRef,
          'AI audit model profile'), connectorRef: input.connectorRef == null ? null : platformValue(
        input.connectorRef, 'AI audit connector'), principalRef: input.principalRef == null ? null :
        platformValue(input.principalRef, 'AI audit principal'), commandId: input.commandId == null ?
        null : platformValue(input.commandId, 'AI audit command'), redactedArguments: structured(
        input.redactedArguments, 'AI audit arguments'), resourceRefs: list(input.resourceRefs,
        'AI audit resources'), assetRefs: list(input.assetRefs, 'AI audit assets'), approvalRefs: list(
        input.approvalRefs, 'AI audit approvals'), backendResult: structured(input.backendResult,
        'AI audit backend result'), taskRefs: list(input.taskRefs, 'AI audit tasks'), timing: structured(
        input.timing, 'AI audit timing'), usage: structured(input.usage, 'AI audit usage'),
      policyDecisions: Array.isArray(input.policyDecisions) ? immutable(input.policyDecisions.map(
        (item) => structured(item, 'AI audit policy decision'))) : [], diagnostics:
        Array.isArray(input.diagnostics) ? immutable(input.diagnostics.map((item) => structured(
          item, 'AI audit diagnostic'))) : [], contentRef: contentValue ?
        `ai-audit-content:${auditId}` : null});
    this.records.push(record); if(contentValue) this.content.set(record.contentRef, immutable({
      storedAt: at, value: contentValue})); return record;
  }

  get(auditId, {includeContent=false}={}) {
    const record = this.records.find((item) => item.auditId === auditId);
    if(!record) throw new Error(`Unknown AI audit record: ${auditId}`);
    return this._view(record, includeContent);
  }

  _view(record, includeContent) {
    return immutable({...record, content: includeContent && record.contentRef ?
      this.content.get(record.contentRef)?.value ?? null : undefined});
  }

  list({from=null, to=null, profiles=[], connectors=[], includeContent=false}={}) {
    const start = from == null ? -Infinity : Date.parse(from); const end = to == null ?
      Infinity : Date.parse(to);
    if(Number.isNaN(start) || Number.isNaN(end) || start > end) throw new TypeError(
      'AI audit date range is invalid.');
    const profileSet = new Set(boundedStrings(profiles, 'AI audit profile filters'));
    const connectorSet = new Set(boundedStrings(connectors, 'AI audit connector filters'));
    return immutable(this.records.filter((item) => Date.parse(item.at) >= start &&
      Date.parse(item.at) <= end && (!profileSet.size || profileSet.has(item.agentProfileRef)) &&
      (!connectorSet.size || connectorSet.has(item.connectorRef))).map((item) =>
      this._view(item, includeContent)));
  }

  export(input, context={}) {
    plainObject(input, 'AI audit export'); const fields = ['from', 'to', 'profiles', 'connectors',
      'includeMessageText', 'format', 'retentionPolicy']; const unknown = Object.keys(input).filter(
      (field) => !fields.includes(field)); if(unknown.length) throw new TypeError(
      `AI audit export contains unsupported field ${unknown[0]}.`);
    const actor = aiActor(context, 'ai.export_audit');
    const includeContent = input.includeMessageText === true;
    if(typeof input.includeMessageText !== 'boolean') throw new TypeError(
      'AI audit message-content selection must be explicit.');
    if(includeContent && (!actor.permissions.includes('ai.admin') ||
        input.retentionPolicy?.retainPromptsInAudit !== true)) throw new Error(
      'Conversation content export requires AI administration permission and retention policy.');
    if(!['json', 'csv'].includes(input.format)) throw new TypeError('AI audit export format is invalid.');
    const records = this.list({from: input.from, to: input.to, profiles: input.profiles ?? [],
      connectors: input.connectors ?? [], includeContent});
    if(input.format === 'json') return immutable({mediaType: 'application/json',
      fileName: 'cdeadmin-ai-audit.json', data: JSON.stringify(records, null, 2),
      recordCount: records.length});
    const columns = ['auditId', 'at', 'eventType', 'initiator', 'sessionId', 'runId', 'planId',
      'agentProfileRef', 'modelProfileRef', 'connectorRef', 'principalRef', 'commandId',
      'resourceRefs', 'assetRefs', 'approvalRefs', 'taskRefs', 'policyDecisions', 'diagnostics'];
    if(includeContent) columns.push('content');
    const data = [columns.map(csvCell).join(','), ...records.map((record) => columns.map((column) =>
      csvCell(record[column])).join(','))].join('\n');
    return immutable({mediaType: 'text/csv', fileName: 'cdeadmin-ai-audit.csv', data,
      recordCount: records.length});
  }

  applyRetention(retentionPolicy, {at=this.now()}={}) {
    const auditDays = retentionPolicy?.auditDays; const conversationDays =
      retentionPolicy?.conversationDays;
    if(!Number.isInteger(auditDays) || auditDays < 1) throw new TypeError(
      'AI audit retention days must be positive.');
    const nowValue = Date.parse(at); if(Number.isNaN(nowValue)) throw new TypeError(
      'AI audit retention time is invalid.');
    const contentDays = retentionPolicy.retainMessages === true &&
      Number.isInteger(conversationDays) ? conversationDays : 0;
    for(const [reference, item] of this.content.entries()) if(nowValue - Date.parse(item.storedAt) >=
      contentDays * 86400000) this.content.delete(reference);
    const cutoff = nowValue - auditDays * 86400000;
    const removed = this.records.filter((item) => Date.parse(item.at) < cutoff);
    removed.forEach((item) => { if(item.contentRef) this.content.delete(item.contentRef); });
    this.records = this.records.filter((item) => Date.parse(item.at) >= cutoff);
    return immutable({metadataRemoved: removed.length,
      contentRemaining: this.content.size, metadataRemaining: this.records.length});
  }
}

export {redact as redactAIAuditValue};
