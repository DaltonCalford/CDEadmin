##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Capability-gated descriptor admission, rendering, paging and export."""

from __future__ import annotations

import base64
import binascii
import copy
import hashlib
import json
import math
import multiprocessing
import queue
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from pgadmin.cdeadmin.contracts.v1.runtime import (
    ContractValidationError,
    validate_contract,
)
from pgadmin.cdeadmin.security.redaction import redact

from .models import (
    ExportPolicy,
    RendererContribution,
    RendererUnavailableError,
    RenderedResultPage,
    ResultAdapterContribution,
    ResultDescriptor,
    ResultDescriptorError,
    ResultLimitError,
    ResultLimits,
    SamplingPolicy,
    WorkerIsolationError,
    WorkerPolicy,
)
from .renderers import builtin_renderers


DESCRIPTOR_VERSION = '1.0.0'
MAX_FIXTURE_BYTES = 2 * 1024 * 1024
MAX_DESCRIPTOR_BYTES = 4 * 1024 * 1024
MAX_RECORD_BYTES = 256 * 1024
MAX_RECORDS = 10_000
MAX_PAGE_SIZE = 500
MAX_EXPORT_BYTES = 8 * 1024 * 1024
MAX_EXPORT_RECORDS = 10_000
MAX_STORED_DESCRIPTORS = 256
MAX_STORED_BYTES = 64 * 1024 * 1024
MAX_COMPARISON_CHANGES = 500
FIXTURE_MARKER = 'cdeadmin_fixture'


def _worker_call(callback, metadata, records, extra):
    if extra is None:
        return callback(metadata, records)
    return callback(metadata, records, extra)


def _worker_entry(output, callback, metadata, records, extra):
    try:
        output.put((
            'ok', _worker_call(callback, metadata, records, extra)
        ))
    except BaseException as exc:  # pragma: no cover - child process boundary
        output.put(('error', type(exc).__name__, str(exc)))


class ProcessRendererExecutor:
    """Run rich renderer work in a short-lived isolated process."""

    def run(self, callback, metadata, records, timeout_seconds, extra=None):
        methods = multiprocessing.get_all_start_methods()
        start_method = 'fork' if 'fork' in methods else 'spawn'
        context = multiprocessing.get_context(start_method)
        output = context.Queue(maxsize=1)
        process = context.Process(
            target=_worker_entry,
            args=(output, callback, metadata, records, extra),
        )
        process.start()
        try:
            process.join(timeout_seconds)
            if process.is_alive():
                process.terminate()
                process.join()
                raise WorkerIsolationError(
                    'result renderer exceeded its worker timeout'
                )
            try:
                response = output.get(timeout=0.5)
            except queue.Empty as exc:
                raise WorkerIsolationError(
                    'result renderer worker returned no response'
                ) from exc
            if response[0] != 'ok':
                raise WorkerIsolationError(
                    'result renderer failed in its isolated worker: '
                    f'{response[1]}'
                )
            return response[1]
        except WorkerIsolationError:
            if process.is_alive():
                process.terminate()
                process.join()
            raise
        finally:
            output.close()
            output.join_thread()


class InlineRendererExecutor:
    """Deterministic test executor preserving the isolation API boundary."""

    def __init__(self):
        self.calls = 0

    def run(self, callback, metadata, records, timeout_seconds, extra=None):
        self.calls += 1
        return _worker_call(callback, metadata, records, extra)


class ResultRendererRegistry:
    """Exact-kind adapter and renderer registration."""

    def __init__(self, renderers=None):
        self._adapters: dict[
            tuple[str | None, str], ResultAdapterContribution
        ] = {}
        self._renderers: dict[str, RendererContribution] = {}
        self._renderers_by_kind: dict[
            str, list[RendererContribution]
        ] = {}
        for renderer in renderers or builtin_renderers():
            self.register_renderer(renderer)

    def register_adapter(self, adapter, provider_id=None):
        for result_kind in adapter.result_kinds:
            key = (provider_id, result_kind)
            if key in self._adapters:
                raise ResultDescriptorError(
                    'result adapter already exists for provider/result kind'
                )
            self._adapters[key] = adapter

    def register_renderer(self, renderer):
        if renderer.renderer_id in self._renderers:
            raise ResultDescriptorError('renderer ID is already registered')
        self._renderers[renderer.renderer_id] = renderer
        for result_kind in renderer.result_kinds:
            self._renderers_by_kind.setdefault(result_kind, []).append(
                renderer
            )

    def adapter(self, result_kind, provider_id=None):
        try:
            return self._adapters[(provider_id, result_kind)]
        except KeyError as exc:
            raise RendererUnavailableError(
                'provider result adapter is unavailable'
            ) from exc

    def renderer(self, descriptor, preferred=None):
        renderer_id = preferred or descriptor.renderer_id
        if renderer_id is not None:
            renderer = self._renderers.get(renderer_id)
            if renderer is None or (
                descriptor.result_kind not in renderer.result_kinds
            ):
                raise RendererUnavailableError(
                    'requested result renderer is unavailable'
                )
        else:
            choices = self._renderers_by_kind.get(
                descriptor.result_kind, []
            )
            if not choices:
                raise RendererUnavailableError(
                    'result kind has no registered renderer'
                )
            renderer = choices[0]
        if descriptor.fixture and not renderer.fixture_safe:
            raise RendererUnavailableError(
                'renderer is not admitted for fixture data'
            )
        return renderer


@dataclass
class _StoredDescriptor:
    descriptor: ResultDescriptor
    generation: str
    provider_capabilities: frozenset[str]
    encoded_bytes: int
    endpoint_id: str | None


class ResultService:
    """Admit typed results and produce bounded redacted render/export views."""

    def __init__(
        self, provider_registry, registry=None, executor=None, *,
        max_stored_descriptors=MAX_STORED_DESCRIPTORS,
        max_stored_bytes=MAX_STORED_BYTES,
    ):
        if (
            isinstance(max_stored_descriptors, bool) or
            not isinstance(max_stored_descriptors, int) or
            not 1 <= max_stored_descriptors <= MAX_STORED_DESCRIPTORS
        ):
            raise ResultLimitError('stored descriptor count limit is invalid')
        if (
            isinstance(max_stored_bytes, bool) or
            not isinstance(max_stored_bytes, int) or
            not 1024 <= max_stored_bytes <= MAX_STORED_BYTES
        ):
            raise ResultLimitError('stored descriptor byte limit is invalid')
        self.provider_registry = provider_registry
        self.registry = registry or ResultRendererRegistry()
        self.executor = executor or ProcessRendererExecutor()
        self._descriptors: dict[str, _StoredDescriptor] = {}
        self._contributed_providers: set[tuple[str, str]] = set()
        self.max_stored_descriptors = max_stored_descriptors
        self.max_stored_bytes = max_stored_bytes
        self._stored_bytes = 0
        self._lock = threading.RLock()

    def admit_provider_result(
        self, context, result_payload, provider_capabilities
    ):
        result = self._contract('Result', result_payload)
        binding = self.provider_registry.resolve(context)
        if 'ResultRenderer' not in binding.manifest.get('contracts', ()):
            raise RendererUnavailableError(
                'provider does not declare result renderer selection'
            )
        self._ensure_contributions(binding)
        selector = getattr(binding.instance, 'select_renderer', None)
        if not callable(selector):
            raise RendererUnavailableError(
                'provider has no result renderer selector'
            )
        renderer_resource = self._contract(
            'Resource', selector(copy.deepcopy(result))
        )
        if (
            renderer_resource['endpoint_id'] != context.endpoint_id or
            renderer_resource['resource_kind'] != 'result-renderer'
        ):
            raise RendererUnavailableError(
                'provider selected an invalid renderer resource'
            )
        provider_id = binding.manifest['identity']['provider_id']
        adapter = self.registry.adapter(result['result_kind'], provider_id)
        capabilities = frozenset(provider_capabilities)
        if (
            adapter.required_capability not in capabilities or
            adapter.required_capability not in
            renderer_resource['capability_ids']
        ):
            raise RendererUnavailableError(
                'provider renderer capability is not admitted'
            )
        raw_descriptor = adapter.describe(binding, copy.deepcopy(result))
        descriptor = self._descriptor(
            result,
            raw_descriptor,
            production=True,
            fixture=False,
            expected_capability=adapter.required_capability,
        )
        return self._store(
            descriptor, capabilities, endpoint_id=context.endpoint_id
        )

    def load_fixture_story(self, path: Path):
        try:
            size = path.stat().st_size
        except OSError as exc:
            raise ResultDescriptorError(
                'result fixture cannot be read'
            ) from exc
        if size > MAX_FIXTURE_BYTES:
            raise ResultLimitError('result fixture exceeds the file limit')
        try:
            payload = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ResultDescriptorError('result fixture is malformed') from exc
        marker = payload.get(FIXTURE_MARKER, {})
        if payload.get('production') is not False or not (
            isinstance(marker, Mapping) and marker.get('non_production')
        ):
            raise ResultDescriptorError(
                'result fixture lacks a non-production marker'
            )
        admitted = []
        for item in payload.get('results', ()):
            if not isinstance(item, Mapping):
                raise ResultDescriptorError(
                    'result fixture entry must be an object'
                )
            result = self._contract('Result', item.get('result'))
            descriptor = self._descriptor(
                result,
                item.get('descriptor'),
                production=False,
                fixture=True,
                expected_capability=None,
            )
            admitted.append(self._store(
                descriptor, frozenset(), endpoint_id=None
            ))
        return tuple(admitted)

    def descriptor(self, result_id, *, endpoint_id=None):
        stored = self._stored(result_id)
        self._require_endpoint(stored, endpoint_id)
        return stored.descriptor.metadata()

    def release(self, result_id, *, endpoint_id=None):
        """Release retained records and invalidate all local page cursors."""
        with self._lock:
            stored = self._descriptors.get(result_id)
            if stored is None:
                return False
            self._require_endpoint(stored, endpoint_id)
            self._descriptors.pop(result_id)
            self._stored_bytes -= stored.encoded_bytes
        return True

    def render(
        self, result_id, *, page_size=None, cursor=None,
        preferred_renderer=None, endpoint_id=None,
    ):
        stored = self._stored(result_id)
        self._require_endpoint(stored, endpoint_id)
        descriptor = stored.descriptor
        self._require_production_capability(stored)
        renderer = self.registry.renderer(
            descriptor, preferred_renderer
        )
        page_size = self._page_size(descriptor, page_size)
        records, sampled = self._sample(descriptor)
        offset = self._decode_cursor(
            cursor, stored
        ) if cursor else 0
        if offset > len(records):
            raise ResultDescriptorError(
                'result page cursor exceeds sampled records'
            )
        page = records[offset:offset + page_size]
        next_cursor = None
        if offset + page_size < len(records):
            next_cursor = self._encode_cursor(
                stored, offset + page_size
            )
        redacted_page = tuple(redact(
            item, descriptor.export_policy.redact_keys
        ) for item in page)
        metadata = descriptor.metadata()
        use_worker = (
            descriptor.worker_policy.required or
            renderer.worker_required
        )
        if use_worker:
            view_model = self.executor.run(
                renderer.render,
                metadata,
                redacted_page,
                descriptor.worker_policy.timeout_seconds,
            )
        else:
            view_model = renderer.render(metadata, redacted_page)
        return RenderedResultPage(
            metadata,
            renderer.renderer_id,
            descriptor.component_reference or
            renderer.component_reference,
            view_model,
            offset,
            len(redacted_page),
            len(records),
            len(descriptor.records),
            next_cursor,
            descriptor.provider_continuation,
            True,
            use_worker,
        ).to_dict() | {'sampling_applied': sampled}

    def export(self, result_id, export_format, *, endpoint_id=None):
        stored = self._stored(result_id)
        self._require_endpoint(stored, endpoint_id)
        descriptor = stored.descriptor
        self._require_production_capability(stored)
        renderer = self.registry.renderer(descriptor)
        policy = descriptor.export_policy
        if not policy.enabled or export_format not in policy.formats:
            raise RendererUnavailableError(
                'result export format is not admitted by policy'
            )
        if (
            renderer.exporter is None or
            export_format not in renderer.export_formats
        ):
            raise RendererUnavailableError(
                'renderer does not support the requested export format'
            )
        records, _sampled = self._sample(descriptor)
        records = records[:policy.max_records]
        redacted_records = tuple(redact(
            item, policy.redact_keys
        ) for item in records)
        metadata = descriptor.metadata()
        use_worker = (
            descriptor.worker_policy.required or
            renderer.worker_required
        )
        if use_worker:
            content = self.executor.run(
                renderer.exporter,
                metadata,
                redacted_records,
                descriptor.worker_policy.timeout_seconds,
                export_format,
            )
        else:
            content = renderer.exporter(
                metadata, redacted_records, export_format
            )
        if not isinstance(content, bytes):
            raise ResultDescriptorError(
                'renderer export must return bytes'
            )
        maximum = min(policy.max_bytes, MAX_EXPORT_BYTES)
        if len(content) > maximum:
            raise ResultLimitError('rendered export exceeds its byte limit')
        return {
            'result_id': descriptor.result_id,
            'format': export_format,
            'content': content,
            'bytes': len(content),
            'records': len(redacted_records),
            'redacted': True,
            'worker_isolated': use_worker,
        }

    def compare(self, left_result_id, right_result_id, *, endpoint_id):
        """Compare two redacted result presentations from one endpoint.

        This is deliberately a presentation comparison, not an engine-level
        equality or transaction-visibility decision.
        """
        left = self._stored(left_result_id)
        right = self._stored(right_result_id)
        self._require_endpoint(left, endpoint_id)
        self._require_endpoint(right, endpoint_id)
        self._require_production_capability(left)
        self._require_production_capability(right)
        left_records, left_sampled = self._sample(left.descriptor)
        right_records, right_sampled = self._sample(right.descriptor)
        left_records = tuple(redact(
            item, left.descriptor.export_policy.redact_keys
        ) for item in left_records)
        right_records = tuple(redact(
            item, right.descriptor.export_policy.redact_keys
        ) for item in right_records)
        changed = []
        unchanged = 0
        for index in range(max(len(left_records), len(right_records))):
            left_value = left_records[index] \
                if index < len(left_records) else None
            right_value = right_records[index] \
                if index < len(right_records) else None
            if self._canonical(left_value) == self._canonical(right_value):
                unchanged += 1
                continue
            if len(changed) < MAX_COMPARISON_CHANGES:
                changed.append({
                    'index': index,
                    'left_present': index < len(left_records),
                    'right_present': index < len(right_records),
                    'left': copy.deepcopy(left_value),
                    'right': copy.deepcopy(right_value),
                })
        return {
            'schema': 'cdeadmin.result-presentation-comparison.v1',
            'comparison_kind': 'ordered-redacted-presentation',
            'semantic_equality_inferred': False,
            'endpoint_id': endpoint_id,
            'left': self._comparison_summary(left, left_sampled),
            'right': self._comparison_summary(right, right_sampled),
            'schema_equal': self._canonical(
                left.descriptor.schema
            ) == self._canonical(right.descriptor.schema),
            'unchanged_count': unchanged,
            'changed_count': max(
                len(left_records), len(right_records)
            ) - unchanged,
            'changes': changed,
            'changes_truncated': (
                max(len(left_records), len(right_records)) - unchanged
            ) > len(changed),
            'redacted': True,
        }

    def _descriptor(
        self, result, raw, *, production, fixture, expected_capability
    ):
        if not isinstance(raw, Mapping):
            raise ResultDescriptorError(
                'normalized result descriptor must be an object'
            )
        if raw.get('descriptor_version') != DESCRIPTOR_VERSION:
            raise ResultDescriptorError(
                'result descriptor version is unsupported'
            )
        capability_id = self._required_string(
            raw.get('capability_id'), 'capability_id'
        )
        if (
            expected_capability is not None and
            capability_id != expected_capability
        ):
            raise RendererUnavailableError(
                'descriptor changed the selected renderer capability'
            )
        records = raw.get('records')
        if not isinstance(records, list):
            raise ResultDescriptorError(
                'result descriptor records must be an array'
            )
        limits = self._limits(raw.get('limits', {}))
        self._admit_record_bounds(records, result['schema'], limits)
        sampling = self._sampling(
            raw.get('sampling', {}), limits, len(records)
        )
        export_policy = self._export_policy(
            raw.get('export_policy', {}), limits
        )
        worker_policy = self._worker_policy(
            raw.get('worker_policy', {})
        )
        renderer_id = raw.get('renderer_id')
        if renderer_id is not None:
            renderer_id = self._required_string(
                renderer_id, 'renderer_id'
            )
        component_reference = self._required_string(
            raw.get('component_reference'), 'component_reference'
        )
        return ResultDescriptor(
            copy.deepcopy(result),
            capability_id,
            tuple(copy.deepcopy(records)),
            copy.deepcopy(result['schema']),
            limits,
            sampling,
            export_policy,
            worker_policy,
            renderer_id,
            component_reference,
            production,
            fixture,
        )

    def _limits(self, raw):
        if not isinstance(raw, Mapping):
            raise ResultDescriptorError('result limits must be an object')
        return ResultLimits(
            self._bounded_int(
                raw.get('max_records', 500),
                'limits.max_records', MAX_RECORDS,
            ),
            self._bounded_int(
                raw.get('max_page_size', 100),
                'limits.max_page_size', MAX_PAGE_SIZE,
            ),
            self._bounded_int(
                raw.get('max_record_bytes', 64 * 1024),
                'limits.max_record_bytes', MAX_RECORD_BYTES,
            ),
            self._bounded_int(
                raw.get('max_descriptor_bytes', 1024 * 1024),
                'limits.max_descriptor_bytes', MAX_DESCRIPTOR_BYTES,
            ),
        )

    def _sampling(self, raw, limits, record_count):
        if not isinstance(raw, Mapping):
            raise ResultDescriptorError('sampling policy must be an object')
        mode = raw.get('mode', 'head')
        default_limit = min(limits.max_records, max(record_count, 1))
        limit = raw.get('limit', default_limit)
        policy = SamplingPolicy(mode, limit)
        if policy.limit > limits.max_records:
            raise ResultLimitError('sampling limit exceeds result limits')
        return policy

    def _export_policy(self, raw, limits):
        if not isinstance(raw, Mapping):
            raise ResultDescriptorError('export policy must be an object')
        formats = raw.get('formats', ['json'])
        redact_keys = raw.get('redact_keys', [])
        if not isinstance(formats, list) or not all(
            isinstance(item, str) and item for item in formats
        ):
            raise ResultDescriptorError('export formats are invalid')
        if not isinstance(redact_keys, list) or not all(
            isinstance(item, str) and item for item in redact_keys
        ):
            raise ResultDescriptorError('export redaction keys are invalid')
        max_records = self._bounded_int(
            raw.get('max_records', limits.max_records),
            'export_policy.max_records', MAX_EXPORT_RECORDS,
        )
        max_bytes = self._bounded_int(
            raw.get('max_bytes', 1024 * 1024),
            'export_policy.max_bytes', MAX_EXPORT_BYTES,
        )
        return ExportPolicy(
            bool(raw.get('enabled', False)),
            frozenset(formats),
            max_records,
            max_bytes,
            frozenset(redact_keys),
        )

    def _worker_policy(self, raw):
        if not isinstance(raw, Mapping):
            raise ResultDescriptorError('worker policy must be an object')
        timeout = raw.get('timeout_seconds', 2.0)
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
            raise ResultDescriptorError('worker timeout must be numeric')
        if not 0.05 <= timeout <= 30:
            raise ResultLimitError('worker timeout is outside platform limits')
        return WorkerPolicy(bool(raw.get('required', False)), float(timeout))

    def _admit_record_bounds(self, records, schema, limits):
        if len(records) > MAX_RECORDS:
            raise ResultLimitError('result record count exceeds platform cap')
        encoded = json.dumps(
            {
                'schema': schema,
                'records': records,
            },
            ensure_ascii=False, separators=(',', ':'), default=repr,
        ).encode('utf-8')
        maximum = min(limits.max_descriptor_bytes, MAX_DESCRIPTOR_BYTES)
        if len(encoded) > maximum:
            raise ResultLimitError('result descriptor exceeds its byte limit')
        record_maximum = min(limits.max_record_bytes, MAX_RECORD_BYTES)
        for record in records:
            encoded_record = json.dumps(
                record, ensure_ascii=False, separators=(',', ':'),
                default=repr,
            ).encode('utf-8')
            if len(encoded_record) > record_maximum:
                raise ResultLimitError(
                    'result record exceeds its byte limit'
                )

    def _sample(self, descriptor):
        records = descriptor.records
        policy = descriptor.sampling
        if policy.mode == 'none':
            if len(records) > descriptor.limits.max_records:
                raise ResultLimitError(
                    'unsampled result exceeds its admitted record limit'
                )
            return records, False
        if len(records) <= policy.limit:
            return records, False
        if policy.mode == 'head':
            return records[:policy.limit], True
        stride = max(1, math.ceil(len(records) / policy.limit))
        sampled = tuple(records[index] for index in range(
            0, len(records), stride
        ))[:policy.limit]
        return sampled, True

    def _store(self, descriptor, capabilities, *, endpoint_id):
        generation = str(uuid.uuid4())
        encoded_bytes = len(json.dumps(
            {
                'schema': descriptor.schema,
                'records': descriptor.records,
            },
            ensure_ascii=False,
            separators=(',', ':'),
            default=repr,
        ).encode('utf-8'))
        with self._lock:
            if descriptor.result_id in self._descriptors:
                raise ResultDescriptorError(
                    'result descriptor identity is already admitted'
                )
            if len(self._descriptors) >= self.max_stored_descriptors:
                raise ResultLimitError(
                    'result descriptor store reached its count limit'
                )
            if self._stored_bytes + encoded_bytes > self.max_stored_bytes:
                raise ResultLimitError(
                    'result descriptor store reached its byte limit'
                )
            self._descriptors[descriptor.result_id] = _StoredDescriptor(
                descriptor,
                generation,
                frozenset(capabilities),
                encoded_bytes,
                endpoint_id,
            )
            self._stored_bytes += encoded_bytes
        return descriptor.metadata()

    def _stored(self, result_id):
        try:
            return self._descriptors[result_id]
        except KeyError as exc:
            raise ResultDescriptorError(
                'result descriptor is unavailable'
            ) from exc

    @staticmethod
    def _require_endpoint(stored, endpoint_id):
        if stored.endpoint_id is None and endpoint_id is None:
            return
        if stored.endpoint_id != endpoint_id:
            raise ResultDescriptorError(
                'result descriptor belongs to another endpoint'
            )

    @staticmethod
    def _canonical(value):
        return json.dumps(
            value, sort_keys=True, separators=(',', ':'), default=repr
        )

    def _comparison_summary(self, stored, sampled):
        descriptor = stored.descriptor
        records, _sampling_applied = self._sample(descriptor)
        records = tuple(redact(
            item, descriptor.export_policy.redact_keys
        ) for item in records)
        digest = hashlib.sha256(self._canonical(records).encode(
            'utf-8'
        )).hexdigest()
        return {
            'result_id': descriptor.result_id,
            'result_kind': descriptor.result_kind,
            'record_count': len(records),
            'sampling_applied': sampled,
            'presentation_sha256': digest,
        }

    @staticmethod
    def _require_production_capability(stored):
        descriptor = stored.descriptor
        if descriptor.production and (
            descriptor.capability_id not in stored.provider_capabilities
        ):
            raise RendererUnavailableError(
                'production renderer capability is no longer admitted'
            )

    def _ensure_contributions(self, binding):
        identity = binding.manifest['identity']
        key = (identity['provider_id'], identity['provider_version'])
        with self._lock:
            if key in self._contributed_providers:
                return
            contributor = getattr(
                binding.instance, 'result_contributions', None
            )
            if not callable(contributor):
                raise RendererUnavailableError(
                    'provider has no result adapter contributions'
                )
            payload = contributor()
            if not isinstance(payload, Mapping) or set(payload) != {
                'adapters'
            }:
                raise ResultDescriptorError(
                    'provider result contributions are invalid'
                )
            for adapter in payload['adapters']:
                if not isinstance(adapter, ResultAdapterContribution):
                    raise ResultDescriptorError(
                        'provider result adapter is invalid'
                    )
                owner = getattr(adapter.describe, '__self__', None)
                if owner is not None and not isinstance(owner, type):
                    raise ResultDescriptorError(
                        'result adapter captures endpoint state'
                    )
                self.registry.register_adapter(
                    adapter, identity['provider_id']
                )
            self._contributed_providers.add(key)

    @staticmethod
    def _contract(name, payload):
        try:
            return validate_contract(name, payload)
        except ContractValidationError as exc:
            raise ResultDescriptorError(
                f'invalid {name} envelope'
            ) from exc

    @staticmethod
    def _required_string(value, field_name):
        if not isinstance(value, str) or not value.strip():
            raise ResultDescriptorError(
                f'{field_name} must not be empty'
            )
        return value.strip()

    @staticmethod
    def _bounded_int(value, field_name, maximum):
        if isinstance(value, bool) or not isinstance(value, int):
            raise ResultDescriptorError(f'{field_name} must be an integer')
        if not 1 <= value <= maximum:
            raise ResultLimitError(
                f'{field_name} is outside platform limits'
            )
        return value

    @staticmethod
    def _page_size(descriptor, value):
        if value is None:
            return min(descriptor.limits.max_page_size, 100)
        if isinstance(value, bool) or not isinstance(value, int):
            raise ResultDescriptorError('page size must be an integer')
        maximum = min(descriptor.limits.max_page_size, MAX_PAGE_SIZE)
        if not 1 <= value <= maximum:
            raise ResultLimitError('page size is outside descriptor limits')
        return value

    @staticmethod
    def _encode_cursor(stored, offset):
        payload = json.dumps({
            'result_id': stored.descriptor.result_id,
            'generation': stored.generation,
            'offset': offset,
        }, sort_keys=True, separators=(',', ':')).encode('utf-8')
        return base64.urlsafe_b64encode(payload).decode('ascii').rstrip('=')

    @staticmethod
    def _decode_cursor(cursor, stored):
        if not isinstance(cursor, str) or not cursor:
            raise ResultDescriptorError('result page cursor is invalid')
        try:
            padding = '=' * (-len(cursor) % 4)
            payload = json.loads(base64.urlsafe_b64decode(
                cursor + padding
            ).decode('utf-8'))
        except (
            ValueError, UnicodeDecodeError, json.JSONDecodeError,
            binascii.Error,
        ) as exc:
            raise ResultDescriptorError(
                'result page cursor is invalid'
            ) from exc
        if not isinstance(payload, Mapping) or set(payload) != {
            'result_id', 'generation', 'offset'
        }:
            raise ResultDescriptorError('result page cursor is invalid')
        if (
            payload['result_id'] != stored.descriptor.result_id or
            payload['generation'] != stored.generation
        ):
            raise ResultDescriptorError(
                'result page cursor belongs to another descriptor'
            )
        offset = payload['offset']
        invalid_offset = (
            isinstance(offset, bool) or
            not isinstance(offset, int) or
            offset < 0
        )
        if invalid_offset:
            raise ResultDescriptorError('result page cursor offset is invalid')
        return offset
