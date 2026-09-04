##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Typed result descriptor and renderer tests for CDE-PREP-080."""

from __future__ import annotations

import copy
import io
import json
import multiprocessing
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
import zipfile
from pathlib import Path
from types import ModuleType, SimpleNamespace


ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / 'web'
FIXTURE = (
    ROOT / 'tools/tests/fixtures/cdeadmin_results/'
    'non_operational_result_story.json'
)
if str(WEB) not in sys.path:
    sys.path.insert(0, str(WEB))
if 'pgadmin' not in sys.modules:
    pgadmin_package = ModuleType('pgadmin')
    pgadmin_package.__path__ = [str(WEB / 'pgadmin')]
    sys.modules['pgadmin'] = pgadmin_package

from pgadmin.cdeadmin.core import EndpointContext  # noqa: E402
from pgadmin.cdeadmin.results import (  # noqa: E402
    InlineRendererExecutor,
    ProcessRendererExecutor,
    RendererContribution,
    RendererUnavailableError,
    ResultAdapterContribution,
    ResultDescriptorError,
    ResultLimitError,
    ResultRendererRegistry,
    ResultService,
    WorkerIsolationError,
    init_app,
    service_for_app,
)
from pgadmin.cdeadmin.results.renderers import (  # noqa: E402
    export_json,
    export_pdf,
    export_svg,
    export_xlsx,
    render_document,
)
from pgadmin.cdeadmin.results.service import (  # noqa: E402
    MAX_FIXTURE_BYTES,
    MAX_RECORDS,
)


PROVIDER_ID = 'org.example.result-provider'
PROVIDER_VERSION = '1.0.0'
CAPABILITY = 'example.result.tabular.render'


def blocking_renderer(_metadata, _records):
    time.sleep(1)
    return {}


def context(label='one'):
    endpoint_id = uuid.uuid5(uuid.NAMESPACE_URL, f'results:{label}')

    def namespace(purpose):
        return str(uuid.uuid5(endpoint_id, purpose))

    return EndpointContext(
        endpoint_id=str(endpoint_id),
        mode='legacy_native',
        experience_family='relational',
        provider_id=PROVIDER_ID,
        provider_version=PROVIDER_VERSION,
        profile_id='example-result',
        profile_version='1.0.0',
        target_adapter_id='example-adapter',
        target_adapter_version='1.0.0',
        pool_namespace=namespace('pool'),
        session_namespace=namespace('session'),
        cache_namespace=namespace('cache'),
        diagnostic_namespace=namespace('diagnostic'),
    )


def identity():
    return {
        'contract_version': '1.0.0',
        'provider_id': PROVIDER_ID,
        'provider_version': PROVIDER_VERSION,
        'profile_id': 'example-result',
        'profile_version': '1.0.0',
        'evidence_reference': 'cde-prep-080:test-provider',
    }


def result(result_id='production-tabular'):
    return {
        'identity': identity(),
        'result_id': result_id,
        'execution_id': 'execution-one',
        'result_kind': 'tabular',
        'schema': {
            'columns': [
                {'name': 'id'}, {'name': 'name'}, {'name': 'password'},
            ],
        },
        'stream_reference': None,
        'complete': False,
        'continuation': 'provider:opaque/?do-not-parse=true',
        'extensions': {},
    }


def normalized(records=None):
    return {
        'descriptor_version': '1.0.0',
        'capability_id': CAPABILITY,
        'records': records or [
            {'id': 1, 'name': 'Ada', 'password': 'do-not-render'},
            {'id': 2, 'name': 'Grace', 'password': 'do-not-render'},
        ],
        'limits': {
            'max_records': 100,
            'max_page_size': 25,
            'max_record_bytes': 65536,
            'max_descriptor_bytes': 1048576,
        },
        'sampling': {'mode': 'head', 'limit': 100},
        'export_policy': {
            'enabled': True,
            'formats': ['csv', 'json'],
            'max_records': 100,
            'max_bytes': 1048576,
            'redact_keys': ['password'],
        },
        'worker_policy': {'required': False, 'timeout_seconds': 2.0},
        'renderer_id': 'cdeadmin.result.tabular.legacy-grid',
        'component_reference': 'SchemaView/DataGridView',
    }


class FakeProvider:
    def __init__(self, ctx):
        self.context = ctx
        self.normalized = normalized()
        self.renderer_capabilities = [CAPABILITY]
        self.renderer_endpoint = ctx.endpoint_id

    @staticmethod
    def _describe(binding, _result):
        return copy.deepcopy(binding.instance.normalized)

    @classmethod
    def result_contributions(cls):
        return {
            'adapters': (
                ResultAdapterContribution(
                    'example.tabular.adapter',
                    frozenset({'tabular'}),
                    CAPABILITY,
                    cls._describe,
                ),
            ),
        }

    def select_renderer(self, _result):
        return {
            'identity': identity(),
            'endpoint_id': self.renderer_endpoint,
            'resource_id': 'example:renderer:tabular',
            'identity_kind': 'provider-renderer-id',
            'resource_kind': 'result-renderer',
            'model_family': 'relational',
            'display_name': 'Example tabular renderer',
            'parent_resource_id': None,
            'display_path': ['Example', 'Tabular'],
            'authority_path': ['example', 'renderer', 'tabular'],
            'is_virtual': True,
            'generation': self.context.cache_namespace,
            'capability_ids': list(self.renderer_capabilities),
            'extensions': {},
        }


class FakeRegistry:
    def __init__(self, binding):
        self.binding = binding
        self.calls = 0

    def resolve(self, ctx):
        self.calls += 1
        if ctx.endpoint_id != self.binding.context.endpoint_id:
            raise RuntimeError('wrong endpoint')
        return self.binding


class ExplodingRegistry:
    def __init__(self):
        self.calls = 0

    def resolve(self, _ctx):
        self.calls += 1
        raise AssertionError('fixture must not resolve a provider')


def harness(*, contracts=('ResultRenderer',), executor=None):
    ctx = context()
    provider = FakeProvider(ctx)
    binding = SimpleNamespace(
        context=ctx,
        instance=provider,
        manifest={
            'identity': identity(),
            'contracts': list(contracts),
        },
    )
    registry = FakeRegistry(binding)
    service = ResultService(
        registry, executor=executor or InlineRendererExecutor()
    )
    return ctx, provider, registry, service


class FixtureRendererTests(unittest.TestCase):

    def setUp(self):
        self.executor = InlineRendererExecutor()
        self.registry = ExplodingRegistry()
        self.service = ResultService(
            self.registry, executor=self.executor
        )
        self.descriptors = self.service.load_fixture_story(FIXTURE)

    def test_all_required_fixture_result_families_are_admitted(self):
        kinds = {item['result_kind'] for item in self.descriptors}
        self.assertEqual({
            'document', 'graph', 'key_value', 'time_series', 'vector',
            'search', 'spatial', 'columnar', 'cellset',
        }, kinds)
        self.assertEqual(0, self.registry.calls)

    def test_fixture_renderers_are_bounded_and_worker_gated(self):
        worker_kinds = {
            'document', 'graph', 'time_series', 'vector', 'search',
            'spatial', 'columnar',
        }
        for descriptor in self.descriptors:
            if descriptor['result_kind'] == 'cellset':
                continue
            rendered = self.service.render(
                descriptor['result_id'], page_size=1
            )
            self.assertLessEqual(rendered['page']['page_size'], 1)
            self.assertTrue(rendered['redacted'])
            self.assertEqual(
                descriptor['result_kind'] in worker_kinds,
                rendered['worker_isolated'],
            )
        self.assertEqual(len(worker_kinds), self.executor.calls)

    def test_redaction_occurs_before_renderer_and_export(self):
        observed = []

        def render_spy(metadata, records):
            observed.extend(records)
            return {'records': list(records), 'kind': metadata['result_kind']}

        self.service.registry.register_renderer(RendererContribution(
            'test.document.spy',
            frozenset({'document'}),
            'tests/DocumentSpy',
            render_spy,
            export_json,
            frozenset({'json'}),
            fixture_safe=True,
            worker_required=False,
        ))
        rendered = self.service.render(
            'fixture-document', preferred_renderer='test.document.spy'
        )
        exported = self.service.export('fixture-document', 'json')
        self.assertNotIn('fixture-secret', repr(observed))
        self.assertNotIn('fixture-token', repr(observed))
        self.assertNotIn('fixture-secret', repr(rendered))
        self.assertNotIn(b'fixture-secret', exported['content'])
        self.assertNotIn(b'fixture-token', exported['content'])

    def test_provider_continuation_is_returned_as_one_opaque_value(self):
        rendered = self.service.render('fixture-graph', page_size=1)
        self.assertEqual(
            'opaque-fixture-graph-continuation',
            rendered['page']['provider_continuation'],
        )

    def test_cellset_uses_pivot_renderer_and_exports(self):
        rendered = self.service.render('fixture-cellset-unsupported')
        self.assertEqual('cellset', rendered['view_model']['family'])
        self.assertEqual(
            'cdeadmin/results/CubePivotView',
            rendered['component_reference'],
        )
        exported = self.service.export('fixture-cellset-unsupported', 'json')
        self.assertIn(b'North', exported['content'])

    def test_local_paging_cursor_is_descriptor_bound(self):
        first = self.service.render('fixture-document', page_size=1)
        cursor = first['page']['next_cursor']
        second = self.service.render(
            'fixture-document', page_size=1, cursor=cursor
        )
        self.assertEqual(1, second['page']['offset'])
        with self.assertRaises(ResultDescriptorError):
            self.service.render(
                'fixture-graph', page_size=1, cursor=cursor
            )


class DescriptorLimitTests(unittest.TestCase):

    @staticmethod
    def _story():
        return json.loads(FIXTURE.read_text(encoding='utf-8'))

    def _load(self, payload):
        service = ResultService(
            ExplodingRegistry(), executor=InlineRendererExecutor()
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'story.json'
            path.write_text(json.dumps(payload), encoding='utf-8')
            return service.load_fixture_story(path)

    def test_malformed_descriptor_version_fails_closed(self):
        story = self._story()
        story['results'][0]['descriptor']['descriptor_version'] = '99.0.0'
        with self.assertRaises(ResultDescriptorError):
            self._load(story)

    def test_oversized_fixture_file_fails_before_json_parsing(self):
        service = ResultService(ExplodingRegistry())
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'large.json'
            path.write_text('x' * (MAX_FIXTURE_BYTES + 1), encoding='utf-8')
            with self.assertRaises(ResultLimitError):
                service.load_fixture_story(path)

    def test_oversized_record_fails_descriptor_admission(self):
        story = self._story()
        descriptor = story['results'][0]['descriptor']
        descriptor['limits']['max_record_bytes'] = 1024
        descriptor['records'] = [{'value': 'x' * 2048}]
        with self.assertRaises(ResultLimitError):
            self._load(story)

    def test_platform_record_count_cap_fails_before_rendering(self):
        ctx, provider, _registry, service = harness()
        provider.normalized['records'] = list(range(MAX_RECORDS + 1))
        with self.assertRaises(ResultLimitError):
            service.admit_provider_result(ctx, result(), {CAPABILITY})

    def test_stride_sampling_bounds_large_view(self):
        ctx, provider, _registry, service = harness()
        provider.normalized = normalized(list(range(100)))
        provider.normalized['limits']['max_records'] = 10
        provider.normalized['sampling'] = {'mode': 'stride', 'limit': 10}
        service.admit_provider_result(ctx, result(), {CAPABILITY})
        rendered = service.render(
            'production-tabular', page_size=10,
            endpoint_id=ctx.endpoint_id,
        )
        self.assertTrue(rendered['sampling_applied'])
        self.assertEqual(10, rendered['page']['sampled_count'])
        self.assertEqual(100, rendered['page']['source_count'])

    def test_page_and_export_bounds_fail_closed(self):
        ctx, provider, _registry, service = harness()
        provider.normalized['limits']['max_page_size'] = 1
        provider.normalized['export_policy']['max_bytes'] = 10
        service.admit_provider_result(ctx, result(), {CAPABILITY})
        with self.assertRaises(ResultLimitError):
            service.render(
                'production-tabular', page_size=2,
                endpoint_id=ctx.endpoint_id,
            )
        with self.assertRaises(ResultLimitError):
            service.export(
                'production-tabular', 'csv', endpoint_id=ctx.endpoint_id
            )

    def test_descriptor_store_is_bounded_and_explicitly_released(self):
        ctx, _provider, registry, _service = harness()
        service = ResultService(
            registry,
            executor=InlineRendererExecutor(),
            max_stored_descriptors=1,
            max_stored_bytes=1024 * 1024,
        )
        service.admit_provider_result(ctx, result('first'), {CAPABILITY})
        with self.assertRaises(ResultLimitError):
            service.admit_provider_result(
                ctx, result('second'), {CAPABILITY}
            )
        self.assertTrue(service.release('first', endpoint_id=ctx.endpoint_id))
        service.admit_provider_result(ctx, result('second'), {CAPABILITY})
        self.assertFalse(service.release('missing'))
        with self.assertRaises(ResultDescriptorError):
            service.descriptor('first')


class ProductionRendererTests(unittest.TestCase):

    def test_pdf_export_embeds_unicode_fonts_and_has_no_active_content(self):
        content = export_pdf({'schema': {'columns': [
            {'name': 'name'}, {'name': 'value'},
        ]}}, ({'name': 'Café Привет', 'value': '<script>bad</script>'},))
        self.assertTrue(content.startswith(b'%PDF-'))
        self.assertIn(b'%%EOF', content)
        self.assertIn(b'/FontFile2', content)
        self.assertIn(b'/ToUnicode', content)
        self.assertNotIn(b'/JavaScript', content)
        self.assertNotIn(b'/OpenAction', content)
        if shutil.which('pdftotext'):
            extracted = subprocess.run(
                ['pdftotext', '-', '-'], input=content,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                check=True,
            ).stdout.decode('utf-8')
            self.assertIn('Café Привет', extracted)
            self.assertIn('<script>bad</script>', extracted)

    def test_pdf_export_is_deterministic_and_reports_bounds(self):
        records = [{'value': index} for index in range(1002)]
        metadata = {'schema': {'columns': [{'name': 'value'}]}}
        first = export_pdf(metadata, records)
        second = export_pdf(metadata, records)
        self.assertEqual(first, second)
        if shutil.which('pdftotext'):
            extracted = subprocess.run(
                ['pdftotext', '-', '-'], input=first,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                check=True,
            ).stdout.decode('utf-8')
            self.assertIn('2 additional rows omitted', extracted)
            self.assertNotIn('\n1000\n', extracted)
        columns = [{'name': f'column_{index}'} for index in range(30)]
        row = {
            f'column_{index}': 'bounded-value-' * 100 for index in range(30)
        }
        wide = export_pdf({'schema': {'columns': columns}}, [row])
        self.assertTrue(wide.startswith(b'%PDF-'))
        if shutil.which('pdftotext'):
            extracted = subprocess.run(
                ['pdftotext', '-', '-'], input=wide,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                check=True,
            ).stdout.decode('utf-8')
            self.assertIn('6 additional columns omitted', extracted)

    def test_xlsx_export_is_valid_formula_safe_office_open_xml(self):
        content = export_xlsx({'schema': {'columns': [
            {'name': 'name'}, {'name': 'amount'}, {'name': 'identifier'},
        ]}}, ({
            'name': '=HYPERLINK("bad")',
            'amount': 42,
            'identifier': 1234567890123456,
        },))
        self.assertTrue(content.startswith(b'PK'))
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            self.assertIsNone(archive.testzip())
            sheet = archive.read('xl/worksheets/sheet1.xml')
        self.assertIn(b'inlineStr', sheet)
        self.assertIn(b'=HYPERLINK', sheet)
        self.assertNotIn(b'<f>', sheet)
        self.assertIn(b'<v>42</v>', sheet)
        self.assertIn(b'1234567890123456</t>', sheet)

    def test_svg_export_is_standalone_escaped_and_non_scriptable(self):
        content = export_svg({'schema': {'columns': [{'name': 'value'}]}}, (
            {'value': '<script>alert(1)</script>\x00'},
        ))
        self.assertTrue(content.startswith(b'<?xml'))
        self.assertIn(b'&lt;script&gt;', content)
        self.assertNotIn(b'<script>', content)
        self.assertNotIn(b'\x00', content)

    def test_document_json_lines_export_is_bounded_record_framing(self):
        exported = export_json({}, (
            {'_id': {'$oid': '0123456789abcdef01234567'}, 'value': 1},
            {'value': 2},
        ), 'jsonl')
        lines = exported.decode('utf-8').splitlines()
        self.assertEqual(2, len(lines))
        self.assertEqual(1, json.loads(lines[0])['value'])
        self.assertEqual(2, json.loads(lines[1])['value'])
        self.assertTrue(exported.endswith(b'\n'))

    def test_production_selection_requires_manifest_and_capability(self):
        ctx, _provider, _registry, service = harness(contracts=())
        with self.assertRaises(RendererUnavailableError):
            service.admit_provider_result(ctx, result(), {CAPABILITY})
        ctx, _provider, _registry, service = harness()
        with self.assertRaises(RendererUnavailableError):
            service.admit_provider_result(ctx, result(), set())

    def test_renderer_resource_must_declare_same_capability_and_endpoint(self):
        ctx, provider, _registry, service = harness()
        provider.renderer_capabilities = []
        with self.assertRaises(RendererUnavailableError):
            service.admit_provider_result(ctx, result(), {CAPABILITY})
        ctx, provider, _registry, service = harness()
        provider.renderer_endpoint = context('other').endpoint_id
        with self.assertRaises(RendererUnavailableError):
            service.admit_provider_result(ctx, result(), {CAPABILITY})

    def test_descriptor_cannot_change_provider_selected_capability(self):
        ctx, provider, _registry, service = harness()
        provider.normalized['capability_id'] = 'different.capability'
        with self.assertRaises(RendererUnavailableError):
            service.admit_provider_result(ctx, result(), {CAPABILITY})

    def test_tabular_adapter_reuses_existing_data_grid_and_exports(self):
        ctx, _provider, _registry, service = harness()
        admitted = service.admit_provider_result(
            ctx, result(), {CAPABILITY}
        )
        rendered = service.render(
            admitted['result_id'], endpoint_id=ctx.endpoint_id
        )
        exported = service.export(
            admitted['result_id'], 'csv', endpoint_id=ctx.endpoint_id
        )
        self.assertEqual(
            'SchemaView/DataGridView', rendered['component_reference']
        )
        self.assertNotIn('do-not-render', repr(rendered))
        self.assertNotIn(b'do-not-render', exported['content'])
        self.assertTrue((
            WEB / 'pgadmin/static/js/SchemaView/DataGridView/index.js'
        ).is_file())
        self.assertEqual([], list(
            (WEB / 'pgadmin/cdeadmin/results').rglob('*.jsx')
        ))

    def test_production_results_are_endpoint_bound(self):
        ctx, _provider, _registry, service = harness()
        admitted = service.admit_provider_result(
            ctx, result(), {CAPABILITY}
        )
        with self.assertRaises(ResultDescriptorError):
            service.render(admitted['result_id'])
        with self.assertRaises(ResultDescriptorError):
            service.render(
                admitted['result_id'],
                endpoint_id=context('other').endpoint_id,
            )
        rendered = service.render(
            admitted['result_id'], endpoint_id=ctx.endpoint_id
        )
        self.assertEqual(admitted['result_id'], rendered[
            'descriptor'
        ]['result_id'])

    def test_comparison_is_redacted_ordered_and_not_semantic(self):
        ctx, provider, _registry, service = harness()
        service.admit_provider_result(
            ctx, result('left'), {CAPABILITY}
        )
        provider.normalized = normalized([
            {'id': 1, 'name': 'Changed', 'password': 'second-secret'},
        ])
        service.admit_provider_result(
            ctx, result('right'), {CAPABILITY}
        )
        compared = service.compare(
            'left', 'right', endpoint_id=ctx.endpoint_id
        )
        self.assertEqual(
            'ordered-redacted-presentation', compared['comparison_kind']
        )
        self.assertFalse(compared['semantic_equality_inferred'])
        self.assertGreater(compared['changed_count'], 0)
        self.assertNotIn('second-secret', repr(compared))

    def test_unknown_result_kind_has_no_implicit_renderer(self):
        ctx, _provider, _registry, service = harness()
        payload = result('unknown-result')
        payload['result_kind'] = 'future-provider-kind'
        with self.assertRaises(RendererUnavailableError):
            service.admit_provider_result(ctx, payload, {CAPABILITY})

    def test_result_adapter_callback_cannot_capture_endpoint_instance(self):
        ctx, provider, _registry, service = harness()
        provider.result_contributions = lambda: {
            'adapters': (
                ResultAdapterContribution(
                    'unsafe.adapter', frozenset({'tabular'}), CAPABILITY,
                    provider.select_renderer,
                ),
            ),
        }
        with self.assertRaises(ResultDescriptorError):
            service.admit_provider_result(ctx, result(), {CAPABILITY})

    def test_process_executor_crosses_real_worker_boundary(self):
        executor = ProcessRendererExecutor()
        actual = executor.run(
            render_document,
            {'result_kind': 'document', 'schema': {}},
            ({'id': 1},),
            2.0,
        )
        self.assertEqual('document', actual['family'])

    def test_pdf_export_crosses_real_worker_boundary(self):
        executor = ProcessRendererExecutor()
        content = executor.run(
            export_json,
            {'schema': {'columns': [{'name': 'value'}]}},
            ({'value': 'worker PDF'},),
            3.0,
            'pdf',
        )
        self.assertTrue(content.startswith(b'%PDF-'))
        self.assertIn(b'/FontFile2', content)

    def test_worker_timeout_terminates_isolated_process(self):
        before = {process.pid for process in multiprocessing.active_children()}
        executor = ProcessRendererExecutor()
        with self.assertRaises(WorkerIsolationError):
            executor.run(blocking_renderer, {}, (), 0.05)
        after = {process.pid for process in multiprocessing.active_children()}
        self.assertEqual(before, after)

    def test_application_result_service_initialization_is_idempotent(self):
        app = SimpleNamespace(extensions={})
        registry = object()
        first = init_app(app, registry)
        self.assertIs(first, init_app(app, registry))
        self.assertIs(first, service_for_app(app))

    def test_renderer_registry_rejects_duplicate_ids(self):
        registry = ResultRendererRegistry()
        renderer = RendererContribution(
            'custom.scalar', frozenset({'scalar'}), 'custom/Scalar',
            render_document, export_json, frozenset({'json'}), True, False,
        )
        registry.register_renderer(renderer)
        with self.assertRaises(ResultDescriptorError):
            registry.register_renderer(renderer)

    def test_result_adapters_are_isolated_by_provider_identity(self):
        registry = ResultRendererRegistry()
        first = ResultAdapterContribution(
            'first.tabular', frozenset({'tabular'}), 'first.render',
            FakeProvider._describe,
        )
        second = ResultAdapterContribution(
            'second.tabular', frozenset({'tabular'}), 'second.render',
            FakeProvider._describe,
        )
        registry.register_adapter(first, 'provider.first')
        registry.register_adapter(second, 'provider.second')
        self.assertIs(first, registry.adapter('tabular', 'provider.first'))
        self.assertIs(second, registry.adapter(
            'tabular', 'provider.second'
        ))


if __name__ == '__main__':
    unittest.main()
