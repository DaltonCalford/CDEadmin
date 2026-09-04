##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Typed result descriptors and renderer contributions."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any, Callable, Mapping


RESULT_KINDS = frozenset({
    'tabular', 'document', 'graph', 'key_value', 'time_series',
    'vector', 'search', 'spatial', 'columnar', 'wide_column', 'cellset',
    'scalar',
    'binary', 'plan', 'operation_receipt',
})
SAMPLING_MODES = frozenset({'none', 'head', 'stride'})


class ResultRegistryError(RuntimeError):
    """Base error for common result and renderer operations."""


class ResultDescriptorError(ResultRegistryError):
    """A result descriptor cannot be admitted safely."""


class RendererUnavailableError(ResultRegistryError):
    """No renderer can be selected within capability and policy limits."""


class ResultLimitError(ResultRegistryError):
    """A descriptor, page, render, or export exceeds a platform bound."""


class WorkerIsolationError(ResultRegistryError):
    """A renderer did not complete inside its isolated worker policy."""


def _required_string(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ResultDescriptorError(f'{field_name} must not be empty')
    return value.strip()


@dataclass(frozen=True)
class ResultLimits:
    """Provider-requested limits after platform cap admission."""

    max_records: int
    max_page_size: int
    max_record_bytes: int
    max_descriptor_bytes: int


@dataclass(frozen=True)
class SamplingPolicy:
    """Deterministic sampling policy applied before local paging."""

    mode: str
    limit: int

    def __post_init__(self):
        if self.mode not in SAMPLING_MODES:
            raise ResultDescriptorError('sampling mode is unknown')
        if isinstance(self.limit, bool) or not isinstance(self.limit, int):
            raise ResultDescriptorError('sampling limit must be an integer')
        if self.limit < 1:
            raise ResultDescriptorError('sampling limit must be positive')


@dataclass(frozen=True)
class ExportPolicy:
    """Formats, redaction, and byte/record bounds for local export."""

    enabled: bool
    formats: frozenset[str]
    max_records: int
    max_bytes: int
    redact_keys: frozenset[str]


@dataclass(frozen=True)
class WorkerPolicy:
    """Whether rendering must cross an isolated process boundary."""

    required: bool
    timeout_seconds: float


@dataclass(frozen=True)
class ResultDescriptor:
    """Normalized common descriptor around the contract-v1 Result envelope."""

    result: Mapping[str, Any]
    capability_id: str
    records: tuple[Any, ...]
    schema: Mapping[str, Any]
    limits: ResultLimits
    sampling: SamplingPolicy
    export_policy: ExportPolicy
    worker_policy: WorkerPolicy
    renderer_id: str | None
    component_reference: str
    production: bool
    fixture: bool

    @property
    def result_id(self) -> str:
        return str(self.result['result_id'])

    @property
    def execution_id(self) -> str:
        return str(self.result['execution_id'])

    @property
    def result_kind(self) -> str:
        return str(self.result['result_kind'])

    @property
    def provider_continuation(self) -> str | None:
        return self.result.get('continuation')

    def metadata(self) -> dict[str, Any]:
        return {
            'result_id': self.result_id,
            'execution_id': self.execution_id,
            'result_kind': self.result_kind,
            'schema': copy.deepcopy(dict(self.schema)),
            'complete': bool(self.result['complete']),
            'provider_continuation': self.provider_continuation,
            'capability_id': self.capability_id,
            'renderer_id': self.renderer_id,
            'component_reference': self.component_reference,
            'export_formats': sorted(self.export_policy.formats)
            if self.export_policy.enabled else [],
            'production': self.production,
            'fixture': self.fixture,
        }


@dataclass(frozen=True)
class ResultAdapterContribution:
    """Provider normalization callback for selected result kinds."""

    adapter_id: str
    result_kinds: frozenset[str]
    required_capability: str
    describe: Callable[
        [object, Mapping[str, Any]], Mapping[str, Any]
    ]

    def __post_init__(self):
        object.__setattr__(
            self, 'adapter_id',
            _required_string(self.adapter_id, 'adapter_id'),
        )
        object.__setattr__(
            self, 'required_capability',
            _required_string(
                self.required_capability, 'required_capability'
            ),
        )
        if not self.result_kinds or not self.result_kinds.issubset(
            RESULT_KINDS
        ):
            raise ResultDescriptorError('adapter result kinds are invalid')
        if not callable(self.describe):
            raise ResultDescriptorError('adapter callback must be callable')


@dataclass(frozen=True)
class RendererContribution:
    """Pure renderer/export callbacks selected by descriptor kind."""

    renderer_id: str
    result_kinds: frozenset[str]
    component_reference: str
    render: Callable[[Mapping[str, Any], tuple[Any, ...]], object]
    exporter: Callable[
        [Mapping[str, Any], tuple[Any, ...], str], bytes
    ] | None
    export_formats: frozenset[str]
    fixture_safe: bool
    worker_required: bool

    def __post_init__(self):
        for name in ('renderer_id', 'component_reference'):
            object.__setattr__(
                self, name,
                _required_string(getattr(self, name), name),
            )
        if not self.result_kinds or not self.result_kinds.issubset(
            RESULT_KINDS
        ):
            raise ResultDescriptorError('renderer result kinds are invalid')
        if not callable(self.render):
            raise ResultDescriptorError('renderer callback must be callable')
        if self.exporter is not None and not callable(self.exporter):
            raise ResultDescriptorError('renderer exporter must be callable')


@dataclass(frozen=True)
class RenderedResultPage:
    """One bounded, redacted renderer response."""

    descriptor: Mapping[str, Any]
    renderer_id: str
    component_reference: str
    view_model: object
    offset: int
    page_size: int
    sampled_count: int
    source_count: int
    next_cursor: str | None
    provider_continuation: str | None
    redacted: bool
    worker_isolated: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            'descriptor': copy.deepcopy(dict(self.descriptor)),
            'renderer_id': self.renderer_id,
            'component_reference': self.component_reference,
            'view_model': copy.deepcopy(self.view_model),
            'page': {
                'offset': self.offset,
                'page_size': self.page_size,
                'sampled_count': self.sampled_count,
                'source_count': self.source_count,
                'next_cursor': self.next_cursor,
                'provider_continuation': self.provider_continuation,
            },
            'redacted': self.redacted,
            'worker_isolated': self.worker_isolated,
        }
