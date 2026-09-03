///////////////////////////////////////////////////////////////////////////
//
// CDEadmin - Multi-engine Database Administration
//
// Copyright (C) 2013 - 2026, The pgAdmin Development Team
// This software is released under the PostgreSQL Licence
//
///////////////////////////////////////////////////////////////////////////

/** Generated CDEadmin provider contract DTOs. Do not edit. */
export const CONTRACT_VERSION = '1.1.0';

export const KNOWN_DISPOSITION_VALUES = [
  'bounded_emulation',
  'deferred',
  'mapped',
  'native',
  'refused',
] as const;
export const KNOWN_MODE_VALUES = [
  'legacy_native',
  'scratchbird_native',
] as const;
export const KNOWN_MUTATION_CLASS_VALUES = [
  'admin',
  'destructive',
  'none',
  'read',
  'write',
] as const;
export const KNOWN_PACKAGE_TYPE_VALUES = [
  'connector',
  'engine',
  'model',
] as const;
export const KNOWN_PERMISSION_ID_VALUES = [
  'administer',
  'backup_admin',
  'data_read',
  'data_write',
  'embedded_runtime',
  'execute',
  'filesystem',
  'maintenance_admin',
  'network',
  'replication_admin',
  'restore_admin',
  'secret_read',
  'security_admin',
  'topology_admin',
  'upgrade_admin',
] as const;
export const KNOWN_RESULT_KIND_VALUES = [
  'binary',
  'cellset',
  'columnar',
  'document',
  'graph',
  'key_value',
  'operation_receipt',
  'plan',
  'scalar',
  'search',
  'spatial',
  'tabular',
  'time_series',
  'vector',
  'wide_column',
] as const;
export const KNOWN_RISK_CLASS_VALUES = [
  'admin',
  'destructive',
  'read',
  'unknown',
  'write',
] as const;
export const KNOWN_SEVERITY_VALUES = [
  'error',
  'fatal',
  'info',
  'unknown',
  'warning',
] as const;
export const KNOWN_SUPPORT_STATE_VALUES = [
  'compatibility_mapped',
  'connector_managed',
  'deferred',
  'experimental',
  'implemented',
  'unsupported',
] as const;

export interface EnvelopeIdentity {
  contract_version: string;
  provider_id: string;
  provider_version: string;
  profile_id: string;
  profile_version: string;
  evidence_reference: string;
  [key: string]: unknown;
}

export interface ProviderPermission {
  permission_id: string;
  granted: boolean;
  scope: Array<string>;
  reason?: string | null;
  [key: string]: unknown;
}

export interface ProviderManifest {
  identity: EnvelopeIdentity;
  package_type: string;
  sdk_compatibility: Record<string, unknown>;
  support_state: string;
  enabled: boolean;
  fixture: boolean;
  production_registration: boolean;
  contracts: Array<string>;
  permissions: Array<ProviderPermission>;
  required_permissions?: Array<string>;
  composition?: Record<string, unknown>;
  extension_schema?: string | null;
  provenance?: Record<string, unknown>;
  [key: string]: unknown;
}

export interface Endpoint {
  identity: EnvelopeIdentity;
  endpoint_id: string;
  mode: string;
  declared_runtime: Record<string, unknown>;
  verified_runtime: Record<string, unknown> | null;
  route: Record<string, unknown>;
  capability_generation: string;
  extensions?: Record<string, unknown>;
  [key: string]: unknown;
}

export interface Capability {
  identity: EnvelopeIdentity;
  capability_id: string;
  support_state: string;
  mutation_class: string;
  enabled: boolean;
  required_permissions: Array<string>;
  scope: Array<string>;
  limits?: Record<string, unknown>;
  refusal_diagnostic?: Diagnostic;
  extensions?: Record<string, unknown>;
  [key: string]: unknown;
}

export interface Resource {
  identity: EnvelopeIdentity;
  endpoint_id: string;
  resource_id: string;
  identity_kind: string;
  resource_kind: string;
  model_family: string;
  display_name: string;
  parent_resource_id?: string | null;
  display_path?: Array<string>;
  authority_path: Array<string>;
  is_virtual: boolean;
  generation: string;
  capability_ids: Array<string>;
  extensions?: Record<string, unknown>;
  [key: string]: unknown;
}

export interface Session {
  identity: EnvelopeIdentity;
  session_id: string;
  endpoint_id: string;
  route_id: string;
  principal_reference: string;
  language_profile: string;
  transaction_model: string;
  provider_state: Record<string, unknown>;
  occurrence_id: string;
  limits?: Record<string, unknown>;
  extensions?: Record<string, unknown>;
  [key: string]: unknown;
}

export interface TransactionPresentation {
  identity: EnvelopeIdentity;
  session_id: string;
  transaction_model: string;
  provider_payload: Record<string, unknown>;
  authority_reference: string;
  extensions?: Record<string, unknown>;
  [key: string]: unknown;
}

export interface Execution {
  identity: EnvelopeIdentity;
  execution_id: string;
  session_id: string;
  language_profile: string;
  source: string;
  parameters: Record<string, unknown>;
  deadline: string | null;
  output_policy: Record<string, unknown>;
  extensions?: Record<string, unknown>;
  [key: string]: unknown;
}

export interface Result {
  identity: EnvelopeIdentity;
  result_id: string;
  execution_id: string;
  result_kind: string;
  schema: Record<string, unknown>;
  stream_reference: string | null;
  complete: boolean;
  continuation?: string | null;
  extensions?: Record<string, unknown>;
  [key: string]: unknown;
}

export interface Diagnostic {
  identity: EnvelopeIdentity;
  diagnostic_id: string;
  severity: string;
  code: string;
  message: string;
  retryable: boolean;
  details: Record<string, unknown>;
  extensions?: Record<string, unknown>;
  [key: string]: unknown;
}

export interface Event {
  identity: EnvelopeIdentity;
  event_id: string;
  event_kind: string;
  occurred_at: string;
  sequence: number;
  subject_id: string;
  payload: Record<string, unknown>;
  extensions?: Record<string, unknown>;
  [key: string]: unknown;
}

export interface Operation {
  identity: EnvelopeIdentity;
  operation_id: string;
  operation_kind: string;
  target_resource_id: string | null;
  capability_id: string;
  risk_class: string;
  provider_state: Record<string, unknown>;
  terminal: boolean;
  provider_receipt: Record<string, unknown> | null;
  extensions?: Record<string, unknown>;
  [key: string]: unknown;
}

export interface Evidence {
  identity: EnvelopeIdentity;
  evidence_id: string;
  evidence_kind: string;
  subject_id: string;
  collected_at: string;
  expires_at: string | null;
  digest: string;
  location: string;
  extensions?: Record<string, unknown>;
  [key: string]: unknown;
}

export interface Parity {
  identity: EnvelopeIdentity;
  parity_id: string;
  feature_id: string;
  reference_profile: string;
  disposition: string;
  semantic_deltas: Array<string>;
  evidence_ids: Array<string>;
  current: boolean;
  extensions?: Record<string, unknown>;
  [key: string]: unknown;
}

export interface EndpointProvider {
  validate_endpoint(request: Endpoint): Promise<Diagnostic>;
  discover_endpoint(request: Endpoint): Promise<Endpoint>;
}

export interface EndpointValidator {
  validate_configuration(request: Endpoint): Promise<Array<Diagnostic>>;
}

export interface ProtocolProvider {
  negotiate_protocol(request: Endpoint): Promise<Session>;
}

export interface EmbeddedRuntimeProvider {
  open_runtime(request: Endpoint): Promise<Session>;
}

export interface CompatibilityProfileProvider {
  describe_compatibility(request: Endpoint): Promise<Array<Parity>>;
}

export interface CapabilityProvider {
  get_capabilities(request: Endpoint): Promise<Array<Capability>>;
}

export interface ResourceProvider {
  list_resources(request: Resource): Promise<Array<Resource>>;
  inspect_resource(request: Resource): Promise<Resource>;
}

export interface MetadataProvider {
  describe_metadata(request: Resource): Promise<Resource>;
}

export interface MutationProvider {
  mutate_resource(request: Operation): Promise<Operation>;
}

export interface LanguageProvider {
  describe_language(request: Endpoint): Promise<Array<Resource>>;
}

export interface CompletionProvider {
  complete(request: Execution): Promise<Result>;
}

export interface ExplainProvider {
  explain(request: Execution): Promise<Result>;
}

export interface SessionProvider {
  open_session(request: Endpoint): Promise<Session>;
  describe_transaction(request: Session): Promise<TransactionPresentation>;
}

export interface TransactionPresentationProvider {
  describe_transaction(request: Session): Promise<TransactionPresentation>;
}

export interface ExecutionProvider {
  execute(request: Execution): Promise<Operation>;
  cancel(request: Operation): Promise<Operation>;
}

export interface ResultProvider {
  describe_result(request: Operation): Promise<Result>;
}

export interface ResultRenderer {
  select_renderer(request: Result): Promise<Resource>;
}

export interface DiagnosticProvider {
  translate_diagnostic(request: Diagnostic): Promise<Diagnostic>;
}

export interface EventProvider {
  get_events(request: Operation): Promise<Array<Event>>;
}

export interface OperationProvider {
  get_operation(request: Operation): Promise<Operation>;
}

export interface ParityEvidenceProvider {
  get_evidence(request: Parity): Promise<Evidence>;
}

export interface ObservabilityProvider {
  observe(request: Endpoint): Promise<Array<Event>>;
}

export interface SecurityProvider {
  describe_security(request: Resource): Promise<Resource>;
}

export interface TopologyProvider {
  describe_topology(request: Endpoint): Promise<Array<Resource>>;
}

export interface DataMovementProvider {
  move_data(request: Operation): Promise<Operation>;
}

export interface ToolProvider {
  list_tools(request: Endpoint): Promise<Array<Resource>>;
}

export interface ModelProvider {
  describe_model(request: Resource): Promise<Resource>;
}

export interface ModelDesignerProvider {
  validate_design(request: Resource): Promise<Array<Diagnostic>>;
}

export interface SemanticLayerProvider {
  describe_semantics(request: Resource): Promise<Resource>;
}

export interface AnalyticalQueryProvider {
  execute_analysis(request: Execution): Promise<Operation>;
}

export interface PipelineProvider {
  describe_pipeline(request: Resource): Promise<Resource>;
}

export interface MaterializationProvider {
  refresh_materialization(request: Operation): Promise<Operation>;
}

export interface VisualizationProvider {
  describe_visualization(request: Result): Promise<Resource>;
}

export interface ModelConversionProvider {
  describe_conversion(request: Resource): Promise<Parity>;
}

export interface DistributedControlProvider {
  control_operation(request: Operation): Promise<Operation>;
}
