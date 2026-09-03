##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Generated CDEadmin provider contract DTOs. Do not edit."""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from typing import Any, Protocol

CONTRACT_VERSION = '1.1.0'

KNOWN_DISPOSITION_VALUES = (
    'bounded_emulation',
    'deferred',
    'mapped',
    'native',
    'refused',
)
KNOWN_MODE_VALUES = (
    'legacy_native',
    'scratchbird_native',
)
KNOWN_MUTATION_CLASS_VALUES = (
    'admin',
    'destructive',
    'none',
    'read',
    'write',
)
KNOWN_PACKAGE_TYPE_VALUES = (
    'connector',
    'engine',
    'model',
)
KNOWN_PERMISSION_ID_VALUES = (
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
)
KNOWN_RESULT_KIND_VALUES = (
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
)
KNOWN_RISK_CLASS_VALUES = (
    'admin',
    'destructive',
    'read',
    'unknown',
    'write',
)
KNOWN_SEVERITY_VALUES = (
    'error',
    'fatal',
    'info',
    'unknown',
    'warning',
)
KNOWN_SUPPORT_STATE_VALUES = (
    'compatibility_mapped',
    'connector_managed',
    'deferred',
    'experimental',
    'implemented',
    'unsupported',
)


class ContractDTO:
    """Generated DTO with lossless extension round trips."""

    def to_dict(self) -> dict[str, Any]:
        """Return all fields without interpretation."""
        result = dict(getattr(self, 'additional_fields', {}))
        for item in fields(self):
            if item.name != 'additional_fields':
                value = getattr(self, item.name)
                result[item.name] = _to_value(value)
        return result


def _to_value(value: Any) -> Any:
    if isinstance(value, ContractDTO):
        return value.to_dict()
    if isinstance(value, list):
        return [_to_value(item) for item in value]
    if isinstance(value, dict):
        return {key: _to_value(item) for key, item in value.items()}
    return value


@dataclass(frozen=True)
class EnvelopeIdentity(ContractDTO):
    """Versioned provenance carried by every exchange envelope."""
    contract_version: str
    provider_id: str
    provider_version: str
    profile_id: str
    profile_version: str
    evidence_reference: str
    additional_fields: dict[str, Any] = field(default_factory=dict, repr=False)


@dataclass(frozen=True)
class ProviderPermission(ContractDTO):
    """ProviderPermission"""
    permission_id: str
    granted: bool
    scope: list[str]
    reason: str | None = None
    additional_fields: dict[str, Any] = field(default_factory=dict, repr=False)


@dataclass(frozen=True)
class ProviderManifest(ContractDTO):
    """ProviderManifest"""
    identity: EnvelopeIdentity
    package_type: str
    sdk_compatibility: dict[str, Any]
    support_state: str
    enabled: bool
    fixture: bool
    production_registration: bool
    contracts: list[str]
    permissions: list[ProviderPermission]
    required_permissions: list[str] = field(default_factory=list)
    composition: dict[str, Any] = field(default_factory=dict)
    extension_schema: str | None = None
    provenance: dict[str, Any] = field(default_factory=dict)
    additional_fields: dict[str, Any] = field(default_factory=dict, repr=False)


@dataclass(frozen=True)
class Endpoint(ContractDTO):
    """Endpoint"""
    identity: EnvelopeIdentity
    endpoint_id: str
    mode: str
    declared_runtime: dict[str, Any]
    verified_runtime: dict[str, Any] | None
    route: dict[str, Any]
    capability_generation: str
    extensions: dict[str, Any] = field(default_factory=dict)
    additional_fields: dict[str, Any] = field(default_factory=dict, repr=False)


@dataclass(frozen=True)
class Capability(ContractDTO):
    """Capability"""
    identity: EnvelopeIdentity
    capability_id: str
    support_state: str
    mutation_class: str
    enabled: bool
    required_permissions: list[str]
    scope: list[str]
    limits: dict[str, Any] = field(default_factory=dict)
    refusal_diagnostic: Diagnostic | None = None
    extensions: dict[str, Any] = field(default_factory=dict)
    additional_fields: dict[str, Any] = field(default_factory=dict, repr=False)


@dataclass(frozen=True)
class Resource(ContractDTO):
    """Resource"""
    identity: EnvelopeIdentity
    endpoint_id: str
    resource_id: str
    identity_kind: str
    resource_kind: str
    model_family: str
    display_name: str
    authority_path: list[str]
    is_virtual: bool
    generation: str
    capability_ids: list[str]
    parent_resource_id: str | None = None
    display_path: list[str] = field(default_factory=list)
    extensions: dict[str, Any] = field(default_factory=dict)
    additional_fields: dict[str, Any] = field(default_factory=dict, repr=False)


@dataclass(frozen=True)
class Session(ContractDTO):
    """Session"""
    identity: EnvelopeIdentity
    session_id: str
    endpoint_id: str
    route_id: str
    principal_reference: str
    language_profile: str
    transaction_model: str
    provider_state: dict[str, Any]
    occurrence_id: str
    limits: dict[str, Any] = field(default_factory=dict)
    extensions: dict[str, Any] = field(default_factory=dict)
    additional_fields: dict[str, Any] = field(default_factory=dict, repr=False)


@dataclass(frozen=True)
class TransactionPresentation(ContractDTO):
    """Opaque provider-native transaction and finality presentation; common
    code must not infer transitions.
    """
    identity: EnvelopeIdentity
    session_id: str
    transaction_model: str
    provider_payload: dict[str, Any]
    authority_reference: str
    extensions: dict[str, Any] = field(default_factory=dict)
    additional_fields: dict[str, Any] = field(default_factory=dict, repr=False)


@dataclass(frozen=True)
class Execution(ContractDTO):
    """Execution"""
    identity: EnvelopeIdentity
    execution_id: str
    session_id: str
    language_profile: str
    source: str
    parameters: dict[str, Any]
    deadline: str | None
    output_policy: dict[str, Any]
    extensions: dict[str, Any] = field(default_factory=dict)
    additional_fields: dict[str, Any] = field(default_factory=dict, repr=False)


@dataclass(frozen=True)
class Result(ContractDTO):
    """Result"""
    identity: EnvelopeIdentity
    result_id: str
    execution_id: str
    result_kind: str
    schema: dict[str, Any]
    stream_reference: str | None
    complete: bool
    continuation: str | None = None
    extensions: dict[str, Any] = field(default_factory=dict)
    additional_fields: dict[str, Any] = field(default_factory=dict, repr=False)


@dataclass(frozen=True)
class Diagnostic(ContractDTO):
    """Diagnostic"""
    identity: EnvelopeIdentity
    diagnostic_id: str
    severity: str
    code: str
    message: str
    retryable: bool
    details: dict[str, Any]
    extensions: dict[str, Any] = field(default_factory=dict)
    additional_fields: dict[str, Any] = field(default_factory=dict, repr=False)


@dataclass(frozen=True)
class Event(ContractDTO):
    """Event"""
    identity: EnvelopeIdentity
    event_id: str
    event_kind: str
    occurred_at: str
    sequence: int
    subject_id: str
    payload: dict[str, Any]
    extensions: dict[str, Any] = field(default_factory=dict)
    additional_fields: dict[str, Any] = field(default_factory=dict, repr=False)


@dataclass(frozen=True)
class Operation(ContractDTO):
    """Operation"""
    identity: EnvelopeIdentity
    operation_id: str
    operation_kind: str
    target_resource_id: str | None
    capability_id: str
    risk_class: str
    provider_state: dict[str, Any]
    terminal: bool
    provider_receipt: dict[str, Any] | None
    extensions: dict[str, Any] = field(default_factory=dict)
    additional_fields: dict[str, Any] = field(default_factory=dict, repr=False)


@dataclass(frozen=True)
class Evidence(ContractDTO):
    """Evidence"""
    identity: EnvelopeIdentity
    evidence_id: str
    evidence_kind: str
    subject_id: str
    collected_at: str
    expires_at: str | None
    digest: str
    location: str
    extensions: dict[str, Any] = field(default_factory=dict)
    additional_fields: dict[str, Any] = field(default_factory=dict, repr=False)


@dataclass(frozen=True)
class Parity(ContractDTO):
    """Parity"""
    identity: EnvelopeIdentity
    parity_id: str
    feature_id: str
    reference_profile: str
    disposition: str
    semantic_deltas: list[str]
    evidence_ids: list[str]
    current: bool
    extensions: dict[str, Any] = field(default_factory=dict)
    additional_fields: dict[str, Any] = field(default_factory=dict, repr=False)


class EndpointProvider(Protocol):
    """Structural EndpointProvider SDK contract."""

    def validate_endpoint(self, request: Endpoint) -> Diagnostic:
        ...

    def discover_endpoint(self, request: Endpoint) -> Endpoint:
        ...


class EndpointValidator(Protocol):
    """Structural EndpointValidator SDK contract."""

    def validate_configuration(self, request: Endpoint) -> list[Diagnostic]:
        ...


class ProtocolProvider(Protocol):
    """Structural ProtocolProvider SDK contract."""

    def negotiate_protocol(self, request: Endpoint) -> Session:
        ...


class EmbeddedRuntimeProvider(Protocol):
    """Structural EmbeddedRuntimeProvider SDK contract."""

    def open_runtime(self, request: Endpoint) -> Session:
        ...


class CompatibilityProfileProvider(Protocol):
    """Structural CompatibilityProfileProvider SDK contract."""

    def describe_compatibility(self, request: Endpoint) -> list[Parity]:
        ...


class CapabilityProvider(Protocol):
    """Structural CapabilityProvider SDK contract."""

    def get_capabilities(self, request: Endpoint) -> list[Capability]:
        ...


class ResourceProvider(Protocol):
    """Structural ResourceProvider SDK contract."""

    def list_resources(self, request: Resource) -> list[Resource]:
        ...

    def inspect_resource(self, request: Resource) -> Resource:
        ...


class MetadataProvider(Protocol):
    """Structural MetadataProvider SDK contract."""

    def describe_metadata(self, request: Resource) -> Resource:
        ...


class MutationProvider(Protocol):
    """Structural MutationProvider SDK contract."""

    def mutate_resource(self, request: Operation) -> Operation:
        ...


class LanguageProvider(Protocol):
    """Structural LanguageProvider SDK contract."""

    def describe_language(self, request: Endpoint) -> list[Resource]:
        ...


class CompletionProvider(Protocol):
    """Structural CompletionProvider SDK contract."""

    def complete(self, request: Execution) -> Result:
        ...


class ExplainProvider(Protocol):
    """Structural ExplainProvider SDK contract."""

    def explain(self, request: Execution) -> Result:
        ...


class SessionProvider(Protocol):
    """Structural SessionProvider SDK contract."""

    def open_session(self, request: Endpoint) -> Session:
        ...

    def describe_transaction(
            self, request: Session) -> TransactionPresentation:
        ...


class TransactionPresentationProvider(Protocol):
    """Structural TransactionPresentationProvider SDK contract."""

    def describe_transaction(
            self, request: Session) -> TransactionPresentation:
        ...


class ExecutionProvider(Protocol):
    """Structural ExecutionProvider SDK contract."""

    def execute(self, request: Execution) -> Operation:
        ...

    def cancel(self, request: Operation) -> Operation:
        ...


class ResultProvider(Protocol):
    """Structural ResultProvider SDK contract."""

    def describe_result(self, request: Operation) -> Result:
        ...


class ResultRenderer(Protocol):
    """Structural ResultRenderer SDK contract."""

    def select_renderer(self, request: Result) -> Resource:
        ...


class DiagnosticProvider(Protocol):
    """Structural DiagnosticProvider SDK contract."""

    def translate_diagnostic(self, request: Diagnostic) -> Diagnostic:
        ...


class EventProvider(Protocol):
    """Structural EventProvider SDK contract."""

    def get_events(self, request: Operation) -> list[Event]:
        ...


class OperationProvider(Protocol):
    """Structural OperationProvider SDK contract."""

    def get_operation(self, request: Operation) -> Operation:
        ...


class ParityEvidenceProvider(Protocol):
    """Structural ParityEvidenceProvider SDK contract."""

    def get_evidence(self, request: Parity) -> Evidence:
        ...


class ObservabilityProvider(Protocol):
    """Structural ObservabilityProvider SDK contract."""

    def observe(self, request: Endpoint) -> list[Event]:
        ...


class SecurityProvider(Protocol):
    """Structural SecurityProvider SDK contract."""

    def describe_security(self, request: Resource) -> Resource:
        ...


class TopologyProvider(Protocol):
    """Structural TopologyProvider SDK contract."""

    def describe_topology(self, request: Endpoint) -> list[Resource]:
        ...


class DataMovementProvider(Protocol):
    """Structural DataMovementProvider SDK contract."""

    def move_data(self, request: Operation) -> Operation:
        ...


class ToolProvider(Protocol):
    """Structural ToolProvider SDK contract."""

    def list_tools(self, request: Endpoint) -> list[Resource]:
        ...


class ModelProvider(Protocol):
    """Structural ModelProvider SDK contract."""

    def describe_model(self, request: Resource) -> Resource:
        ...


class ModelDesignerProvider(Protocol):
    """Structural ModelDesignerProvider SDK contract."""

    def validate_design(self, request: Resource) -> list[Diagnostic]:
        ...


class SemanticLayerProvider(Protocol):
    """Structural SemanticLayerProvider SDK contract."""

    def describe_semantics(self, request: Resource) -> Resource:
        ...


class AnalyticalQueryProvider(Protocol):
    """Structural AnalyticalQueryProvider SDK contract."""

    def execute_analysis(self, request: Execution) -> Operation:
        ...


class PipelineProvider(Protocol):
    """Structural PipelineProvider SDK contract."""

    def describe_pipeline(self, request: Resource) -> Resource:
        ...


class MaterializationProvider(Protocol):
    """Structural MaterializationProvider SDK contract."""

    def refresh_materialization(self, request: Operation) -> Operation:
        ...


class VisualizationProvider(Protocol):
    """Structural VisualizationProvider SDK contract."""

    def describe_visualization(self, request: Result) -> Resource:
        ...


class ModelConversionProvider(Protocol):
    """Structural ModelConversionProvider SDK contract."""

    def describe_conversion(self, request: Resource) -> Parity:
        ...


class DistributedControlProvider(Protocol):
    """Structural DistributedControlProvider SDK contract."""

    def control_operation(self, request: Operation) -> Operation:
        ...
