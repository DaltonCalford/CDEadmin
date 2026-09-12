/////////////////////////////////////////////////////////////
//
// CDEadmin - Multi-engine Database Administration
//
// Copyright (C) 2013 - 2026, The pgAdmin Development Team
// This software is released under the PostgreSQL Licence
//
//////////////////////////////////////////////////////////////

import AddCircleIcon from '../../../img/command_icons/add-circle.svg?svgr';
import ArrowAllDirectionIcon from '../../../img/command_icons/arrow-all-direction.svg?svgr';
import CalendarClockIcon from '../../../img/command_icons/calendar-clock.svg?svgr';
import CancelCircleIcon from '../../../img/command_icons/cancel-circle.svg?svgr';
import CodeIcon from '../../../img/command_icons/code.svg?svgr';
import CommandLineIcon from '../../../img/command_icons/command-line.svg?svgr';
import ConnectIcon from '../../../img/command_icons/connect.svg?svgr';
import CopyIcon from '../../../img/command_icons/copy01.svg?svgr';
import DatabaseAddIcon from '../../../img/command_icons/database-add.svg?svgr';
import DatabaseBackupIcon from '../../../img/command_icons/database-backup.svg?svgr';
import DatabaseRestoreIcon from '../../../img/command_icons/database-restore.svg?svgr';
import DeleteIcon from '../../../img/command_icons/delete02.svg?svgr';
import DownloadIcon from '../../../img/command_icons/download01.svg?svgr';
import EditIcon from '../../../img/command_icons/edit01.svg?svgr';
import EyeIcon from '../../../img/command_icons/eye.svg?svgr';
import FileAddIcon from '../../../img/command_icons/file-add.svg?svgr';
import FilterIcon from '../../../img/command_icons/filter.svg?svgr';
import GitCompareIcon from '../../../img/command_icons/git-compare.svg?svgr';
import HelpCircleIcon from '../../../img/command_icons/help-circle.svg?svgr';
import HistoryIcon from '../../../img/command_icons/history.svg?svgr';
import InformationCircleIcon from '../../../img/command_icons/information-circle.svg?svgr';
import LinkIcon from '../../../img/command_icons/link01.svg?svgr';
import LockIcon from '../../../img/command_icons/lock.svg?svgr';
import MenuCircleIcon from '../../../img/command_icons/menu-circle.svg?svgr';
import PlayCircleIcon from '../../../img/command_icons/play-circle.svg?svgr';
import PrinterIcon from '../../../img/command_icons/printer.svg?svgr';
import RefreshIcon from '../../../img/command_icons/refresh.svg?svgr';
import SaveIcon from '../../../img/command_icons/save.svg?svgr';
import SearchIcon from '../../../img/command_icons/search01.svg?svgr';
import SettingsIcon from '../../../img/command_icons/settings02.svg?svgr';
import ShieldUserIcon from '../../../img/command_icons/shield-user.svg?svgr';
import SortIcon from '../../../img/command_icons/sort-by-down01.svg?svgr';
import TransactionIcon from '../../../img/command_icons/transaction.svg?svgr';
import UnlinkIcon from '../../../img/command_icons/unlink01.svg?svgr';
import UploadIcon from '../../../img/command_icons/upload01.svg?svgr';

export const ICON_CATEGORIES = Object.freeze({
  ACTION: 'action',
  ENGINE: 'engine',
  OBJECT: 'object',
  STATUS: 'status',
  TOOL: 'tool',
});

const ENGINE_IDS = Object.freeze([
  'apache_ignite', 'cassandra', 'clickhouse', 'cockroachdb', 'dolt',
  'duckdb', 'firebird', 'foundationdb', 'immudb', 'influxdb', 'mariadb',
  'milvus', 'mongodb', 'mysql', 'neo4j', 'opensearch', 'postgresql',
  'redis', 'scratchbird', 'sqlite', 'tidb', 'tikv', 'vitess', 'xtdb',
  'yugabytedb',
]);

const OBJECT_CLASSES = Object.freeze({
  unknown: 'icon-object',
  server: 'icon-server',
  instance: 'icon-server',
  cluster: 'icon-server-group',
  database: 'icon-database',
  catalog: 'icon-database',
  schema: 'icon-schema',
  keyspace: 'icon-schema',
  table: 'icon-table',
  columnar_table: 'icon-table',
  wide_column_table: 'icon-table',
  view: 'icon-view',
  materialized_view: 'icon-mview',
  mview: 'icon-mview',
  column: 'icon-column',
  domain: 'icon-domain',
  type: 'icon-type',
  sequence: 'icon-sequence',
  function: 'icon-function',
  procedure: 'icon-procedure',
  package: 'icon-package',
  trigger: 'icon-trigger',
  index: 'icon-index',
  constraint: 'icon-check_constraint',
  role: 'icon-role',
  user: 'icon-role',
  grant: 'icon-security',
  privilege: 'icon-security',
  extension: 'icon-extension',
  plugin: 'icon-extension',
  partition: 'icon-partition',
  tablespace: 'icon-tablespace',
  filespace: 'icon-tablespace',
  replication: 'icon-publication',
  job: 'icon-pga_job',
  event: 'icon-event_trigger',
  collection: 'icon-coll-table',
  document: 'icon-file',
  validation_rule: 'icon-check_constraint',
  aggregation_pipeline: 'icon-query-tool',
  replica_set: 'icon-server-group',
  sharding: 'icon-partition',
  node: 'icon-server',
  relationship: 'icon-dependency',
  label: 'icon-collation',
  graph_projection: 'icon-erd',
  transaction: 'icon-commit',
  query_plan: 'icon-query-tool',
  cluster_member: 'icon-server',
  key: 'icon-key',
  ttl: 'icon-clock',
  stream: 'icon-query-tool',
  pubsub: 'icon-publication',
  consumer_group: 'icon-role',
  module: 'icon-extension',
  acl: 'icon-security',
  index_alias: 'icon-synonym',
  template: 'icon-template',
  pipeline: 'icon-query-tool',
  shard: 'icon-partition',
  snapshot: 'icon-backup',
  ingest_processor: 'icon-query-tool',
  measurement: 'icon-statistics',
  tag: 'icon-collation',
  field: 'icon-column',
  retention_policy: 'icon-clock',
  vector_collection: 'icon-coll-table',
  vector: 'icon-type',
  semantic_model: 'icon-erd',
  cube: 'icon-table',
  measure: 'icon-statistics',
  hierarchy: 'icon-schema',
});

const ACTION_COMPONENTS = Object.freeze({
  about: InformationCircleIcon,
  add: AddCircleIcon,
  alter: EditIcon,
  attach: LinkIcon,
  backup: DatabaseBackupIcon,
  cancel: CancelCircleIcon,
  close: CancelCircleIcon,
  commit: SaveIcon,
  compare: GitCompareIcon,
  connect: ConnectIcon,
  copy: CopyIcon,
  create: AddCircleIcon,
  create_database: DatabaseAddIcon,
  delete: DeleteIcon,
  detach: UnlinkIcon,
  disconnect: UnlinkIcon,
  documentation: HelpCircleIcon,
  drop: DeleteIcon,
  edit: EditIcon,
  execute: PlayCircleIcon,
  export: DownloadIcon,
  filter: FilterIcon,
  float: ArrowAllDirectionIcon,
  help: HelpCircleIcon,
  import: UploadIcon,
  lock: LockIcon,
  move: ArrowAllDirectionIcon,
  new: FileAddIcon,
  open: EyeIcon,
  print: PrinterIcon,
  properties: InformationCircleIcon,
  refresh: RefreshIcon,
  rename: EditIcon,
  restore: DatabaseRestoreIcon,
  rollback: HistoryIcon,
  save: SaveIcon,
  schedule: CalendarClockIcon,
  script: CodeIcon,
  search: SearchIcon,
  security: ShieldUserIcon,
  settings: SettingsIcon,
  sort: SortIcon,
  terminal: CommandLineIcon,
  transaction: TransactionIcon,
  unlock: LockIcon,
  view: EyeIcon,
});

const ACTION_INFERENCE = Object.freeze([
  ['create_database', /\b(?:create|new|register|add)[ _-]+database\b/],
  ['disconnect', /\bdisconnect(?:ed|ion)?\b/],
  ['detach', /\bdetach\b/],
  ['rollback', /\broll[ _-]?back\b/],
  ['restore', /\brestore\b/],
  ['backup', /\bbackup\b/],
  ['documentation', /\b(?:documentation|manual|docs)\b/],
  ['properties', /\b(?:properties|details|information)\b/],
  ['security', /\b(?:security|permissions?|privileges?|grants?|roles?|users?)\b/],
  ['transaction', /\btransactions?\b/],
  ['schedule', /\b(?:schedule|jobs?|events?)\b/],
  ['compare', /\b(?:compare|comparison|diff)\b/],
  ['settings', /\b(?:settings?|preferences?|configure|configuration|manage)\b/],
  ['execute', /\b(?:execute|run|start|play)\b/],
  ['terminal', /\b(?:terminal|console|shell|command[ _-]?line|psql)\b/],
  ['script', /\b(?:script|code|sql)\b/],
  ['connect', /\bconnect(?:ion)?\b/],
  ['attach', /\b(?:attach|link)\b/],
  ['delete', /\b(?:delete|remove)\b/],
  ['drop', /\bdrop\b/],
  ['cancel', /\b(?:cancel|abort|stop)\b/],
  ['close', /\b(?:close|exit|quit)\b/],
  ['import', /\b(?:import|upload)\b/],
  ['export', /\b(?:export|download)\b/],
  ['refresh', /\b(?:refresh|reload|rescan|sync)\b/],
  ['search', /\b(?:search|find|locate)\b/],
  ['filter', /\bfilter\b/],
  ['sort', /\bsort\b/],
  ['copy', /\b(?:copy|duplicate|clone)\b/],
  ['rename', /\brename\b/],
  ['alter', /\balter\b/],
  ['edit', /\b(?:edit|modify|update)\b/],
  ['commit', /\bcommit\b/],
  ['save', /\bsave\b/],
  ['print', /\bprint\b/],
  ['lock', /\block\b/],
  ['unlock', /\bunlock\b/],
  ['float', /\b(?:float|tear[ _-]?off|undock)\b/],
  ['move', /\b(?:move|reorder)\b/],
  ['new', /\bnew\b/],
  ['create', /\b(?:create|register|add)\b/],
  ['open', /\bopen\b/],
  ['view', /\b(?:view|show|inspect|browse)\b/],
  ['about', /\babout\b/],
  ['help', /\bhelp\b/],
]);

const STATUS_CLASSES = Object.freeze({
  connected: 'fa fa-check-circle',
  disconnected: 'fa fa-circle-o',
  error: 'fa fa-exclamation-circle',
  loading: 'fa fa-spinner fa-spin',
  locked: 'fa fa-lock',
  offline: 'fa fa-minus-circle',
  readonly: 'fa fa-eye',
  running: 'fa fa-play-circle',
  stopped: 'fa fa-stop-circle',
  warning: 'fa fa-exclamation-triangle',
});

const TOOL_CLASSES = Object.freeze({
  dashboard: 'icon-dashboard',
  dataflow: 'icon-erd',
  datapump: 'fa fa-exchange',
  erd: 'icon-erd',
  mind_map: 'fa fa-sitemap',
  query: 'icon-query-tool',
  report: 'fa fa-bar-chart',
  whiteboard: 'fa fa-pencil-square-o',
});

const TAXONOMY_FALLBACKS = Object.freeze([
  ['relation.view.materialized', 'object.materialized_view'],
  ['relation.view', 'object.view'],
  ['relation.column', 'object.column'],
  ['relation.partition', 'object.partition'],
  ['relation.sequence', 'object.sequence'],
  ['relation.table', 'object.table'],
  ['columnar.table', 'object.columnar_table'],
  ['wide_column.table', 'object.wide_column_table'],
  ['access.index', 'object.index'],
  ['constraint.', 'object.constraint'],
  ['routine.procedure', 'object.procedure'],
  ['routine.trigger', 'object.trigger'],
  ['routine.package', 'object.package'],
  ['routine.', 'object.function'],
  ['namespace.keyspace', 'object.keyspace'],
  ['namespace.schema', 'object.schema'],
  ['namespace.', 'object.database'],
  ['security.role', 'object.role'],
  ['security.user', 'object.user'],
  ['security.', 'object.privilege'],
  ['replication.', 'object.replication'],
  ['operation.job', 'object.job'],
  ['operation.event', 'object.event'],
  ['operation.snapshot', 'object.snapshot'],
  ['topology.node', 'object.node'],
  ['topology.', 'object.cluster'],
  ['keyvalue.stream', 'object.stream'],
  ['keyvalue.ttl', 'object.ttl'],
  ['keyvalue.', 'object.key'],
  ['graph.relationship', 'object.relationship'],
  ['graph.node', 'object.node'],
  ['graph.', 'object.graph_projection'],
  ['search.pipeline', 'object.pipeline'],
  ['search.', 'object.index'],
  ['timeseries.measurement', 'object.measurement'],
  ['timeseries.tag', 'object.tag'],
  ['timeseries.', 'object.field'],
  ['vector.collection', 'object.vector_collection'],
  ['vector.', 'object.vector'],
  ['semantic.', 'object.semantic_model'],
  ['storage.filespace', 'object.filespace'],
  ['storage.tablespace', 'object.tablespace'],
]);

const SAFE_CLASS = /^[a-zA-Z0-9_-]+(?:\s+[a-zA-Z0-9_-]+)*$/;
const SAFE_KEY = /^[a-z0-9][a-z0-9._-]*$/;

function title(value) {
  return String(value).replace(/[._-]+/g, ' ').replace(/\b\w/g, (c)=>c.toUpperCase());
}

function definition(key, category, className, label=title(key.split('.').pop())) {
  return Object.freeze({key, category, kind: 'class', className, label});
}

function componentDefinition(
  key, category, component, label=title(key.split('.').pop())
) {
  return Object.freeze({
    key,
    category,
    kind: 'component',
    component,
    label,
    license: 'MIT',
    attribution: 'Hugeicons Free Icons 4.3.0',
  });
}

const BUILTIN_DEFINITIONS = [
  ...ENGINE_IDS.map((id)=>definition(
    `engine.${id}`, ICON_CATEGORIES.ENGINE, `icon-engine-type-${id}`,
    title(id)
  )),
  ...Object.entries(OBJECT_CLASSES).map(([name, className])=>definition(
    `object.${name}`, ICON_CATEGORIES.OBJECT, className
  )),
  componentDefinition(
    'command.default', ICON_CATEGORIES.ACTION, MenuCircleIcon, 'Command'
  ),
  componentDefinition(
    'tool.schema-compare', ICON_CATEGORIES.TOOL, GitCompareIcon, 'Schema Comparison'
  ),
  componentDefinition(
    'tool.lineage', ICON_CATEGORIES.TOOL, LinkIcon, 'Data Lineage'
  ),
  componentDefinition(
    'tool.quality', ICON_CATEGORIES.TOOL, FilterIcon, 'Data Quality'
  ),
  componentDefinition(
    'tool.contract', ICON_CATEGORIES.TOOL, ShieldUserIcon, 'Data Contract Manager'
  ),
  componentDefinition(
    'tool.etl', ICON_CATEGORIES.TOOL, ArrowAllDirectionIcon, 'ETL Designer'
  ),
  componentDefinition(
    'tool.cdc', ICON_CATEGORIES.TOOL, TransactionIcon, 'CDC Designer'
  ),
  componentDefinition(
    'tool.replication', ICON_CATEGORIES.TOOL, RefreshIcon, 'Replication Topology'
  ),
  componentDefinition(
    'tool.tracing', ICON_CATEGORIES.TOOL, SearchIcon, 'Distributed Tracing'
  ),
  ...Object.entries(ACTION_COMPONENTS).map(([name, component])=>componentDefinition(
    `action.${name}`, ICON_CATEGORIES.ACTION, component
  )),
  ...Object.entries(STATUS_CLASSES).map(([name, className])=>definition(
    `status.${name}`, ICON_CATEGORIES.STATUS, className
  )),
  ...Object.entries(TOOL_CLASSES).map(([name, className])=>definition(
    `tool.${name}`, ICON_CATEGORIES.TOOL, className
  )),
];

const builtins = new Map(BUILTIN_DEFINITIONS.map((item)=>[item.key, item]));
const providerDefinitions = new Map();

function normalizeDefinition(input) {
  const key = String(input?.key ?? '').trim().toLowerCase();
  if(!SAFE_KEY.test(key)) {
    throw new TypeError('Icon key must contain only lowercase semantic segments.');
  }
  const category = String(input.category ?? key.split('.')[0]);
  if(!Object.values(ICON_CATEGORIES).includes(category)) {
    throw new TypeError(`Unknown icon category: ${category}`);
  }
  const kind = input.svgUrl ? 'svg' : 'class';
  const className = String(input.className ?? '').trim();
  const svgUrl = String(input.svgUrl ?? '').trim();
  if(kind === 'class' && (!className || !SAFE_CLASS.test(className))) {
    throw new TypeError('Class icon definitions require safe CSS class names.');
  }
  if(kind === 'svg' && !svgUrl) {
    throw new TypeError('SVG icon definitions require a URL.');
  }
  if(kind === 'svg' && !/^(?:\/|https:\/\/)/.test(svgUrl)) {
    throw new TypeError('SVG icon URLs must use an application or HTTPS URL.');
  }
  return Object.freeze({
    key,
    category,
    kind,
    className,
    svgUrl,
    label: String(input.label ?? title(key.split('.').pop())),
    license: String(input.license ?? ''),
    attribution: String(input.attribution ?? ''),
    providerId: String(input.providerId ?? ''),
  });
}

export function registerIconDefinition(input) {
  const item = normalizeDefinition(input);
  if(builtins.has(item.key) || providerDefinitions.has(item.key)) {
    throw new Error(`Icon key is already registered: ${item.key}`);
  }
  providerDefinitions.set(item.key, item);
  return ()=>providerDefinitions.delete(item.key);
}

function familyFallback(key) {
  const match = TAXONOMY_FALLBACKS.find(([prefix])=>key.startsWith(prefix));
  if(match) return builtins.get(match[1]);
  if(key.startsWith('action.') || key.startsWith('command.')) {
    return builtins.get('command.default');
  }
  return builtins.get('object.unknown');
}

export function resolveIconDefinition(iconKey, options={}) {
  let key = String(iconKey ?? '').trim().toLowerCase();
  if(providerDefinitions.has(key)) return providerDefinitions.get(key);
  if(builtins.has(key)) return builtins.get(key);

  if(SAFE_CLASS.test(key) && (key.startsWith('icon-') || key.startsWith('fa '))) {
    return Object.freeze({
      key: `legacy.${key.replace(/\s+/g, '.')}`,
      category: options.category ?? ICON_CATEGORIES.OBJECT,
      kind: 'class',
      className: key,
      label: String(options.label ?? title(key)),
      legacy: true,
    });
  }

  if(!key.includes('.')) {
    const objectKey = `object.${key.replace(/-/g, '_')}`;
    if(builtins.has(objectKey)) return builtins.get(objectKey);
  }
  if(key.startsWith('engine.') && SAFE_KEY.test(key)) {
    return definition(key, ICON_CATEGORIES.ENGINE,
      `icon-engine-type-${key.slice(7)}`);
  }
  return familyFallback(key || 'object.unknown');
}

export function listIconDefinitions({category}={}) {
  return Object.freeze([...builtins.values(), ...providerDefinitions.values()]
    .filter((item)=>!category || item.category === category));
}

export function semanticObjectIconKey(objectType) {
  const normalized = String(objectType ?? 'unknown').trim().toLowerCase()
    .replace(/^coll-/, '').replace(/-/g, '_');
  return builtins.has(`object.${normalized}`) ?
    `object.${normalized}` : `object.${normalized || 'unknown'}`;
}

export function semanticEngineIconKey(engineId) {
  return `engine.${String(engineId ?? '').trim().toLowerCase()}`;
}

export function inferActionIconKey(action={}) {
  if(action.iconKey) return action.iconKey;
  const source = [action.id, action.name, action.label, action.description]
    .filter(Boolean).join(' ').toLowerCase().replace(/[^a-z0-9]+/g, ' ');
  const match = ACTION_INFERENCE.find(([, pattern])=>pattern.test(source));
  return match ? `action.${match[0]}` : '';
}
