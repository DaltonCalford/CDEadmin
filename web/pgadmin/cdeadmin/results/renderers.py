##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Pure built-in result renderer and exporter callbacks."""

from __future__ import annotations

import csv
import io
import json
from typing import Any, Mapping

from .models import RendererContribution


def render_tabular(metadata, records):
    return {
        'columns': metadata['schema'].get('columns', []),
        'rows': list(records),
    }


def _render_family(metadata, records):
    return {
        'family': metadata['result_kind'],
        'records': list(records),
        'schema': metadata['schema'],
    }


def render_document(metadata, records):
    return _render_family(metadata, records)


def render_bitemporal_document(metadata, records):
    """Preserve document values and make both XTDB time axes explicit."""
    return {
        'family': 'bitemporal_document',
        'records': list(records),
        'schema': metadata['schema'],
        'temporal_fields': {
            'identity': '_id',
            'valid_time': ['_valid_from', '_valid_to'],
            'system_time': ['_system_from', '_system_to'],
        },
    }


def render_graph(metadata, records):
    return _render_family(metadata, records)


def render_key_value(metadata, records):
    return _render_family(metadata, records)


def render_time_series(metadata, records):
    return _render_family(metadata, records)


def render_vector(metadata, records):
    return _render_family(metadata, records)


def render_search(metadata, records):
    return _render_family(metadata, records)


def render_spatial(metadata, records):
    return _render_family(metadata, records)


def render_columnar(metadata, records):
    return {
        'family': 'columnar',
        'columns': metadata['schema'].get('columns', []),
        'rows': list(records),
        'statistics': metadata['schema'].get('statistics', {}),
    }


def render_wide_column(metadata, records):
    return {
        'family': 'wide_column',
        'columns': metadata['schema'].get('columns', []),
        'rows': list(records),
        'native_observation': metadata['schema'].get(
            'native_observation', {}
        ),
    }


def render_cellset(metadata, records):
    """Render sparse provider cell records as a pivot-capable cellset."""
    schema = metadata.get('schema', {})
    return {
        'family': 'cellset',
        'axes': schema.get('axes', {'rows': [], 'columns': [], 'pages': []}),
        'levels': schema.get('levels', []),
        'measures': schema.get('measures', []),
        'cells': list(records),
        'formats': schema.get('formats', {}),
        'drill': schema.get('drill', {'supported': True}),
        'slice': schema.get('slice', []),
    }


def export_json(_metadata, records, export_format):
    if export_format == 'jsonl':
        return b''.join(
            json.dumps(
                record, ensure_ascii=False, separators=(',', ':'),
                default=repr,
            ).encode('utf-8') + b'\n'
            for record in records
        )
    return json.dumps(
        list(records), ensure_ascii=False, separators=(',', ':'),
        default=repr,
    ).encode('utf-8')


def _column_names(metadata: Mapping[str, Any], records) -> list[str]:
    columns = metadata.get('schema', {}).get('columns', [])
    names = []
    for column in columns:
        if isinstance(column, Mapping) and isinstance(
            column.get('name'), str
        ):
            names.append(column['name'])
    if not names and records and isinstance(records[0], Mapping):
        names = [str(key) for key in records[0]]
    return names


def export_tabular(metadata, records, export_format):
    if export_format in {'json', 'jsonl'}:
        return export_json(metadata, records, export_format)
    output = io.StringIO(newline='')
    names = _column_names(metadata, records)
    writer = csv.writer(output)
    writer.writerow(names)
    for record in records:
        if isinstance(record, Mapping):
            writer.writerow([record.get(name) for name in names])
        elif isinstance(record, (list, tuple)):
            writer.writerow(record)
        else:
            writer.writerow([record])
    return output.getvalue().encode('utf-8')


def builtin_renderers():
    """Return common renderers; fixture-only families remain inert."""
    return (
        RendererContribution(
            'cdeadmin.result.tabular.legacy-grid',
            frozenset({'tabular'}),
            'SchemaView/DataGridView',
            render_tabular,
            export_tabular,
            frozenset({'csv', 'json'}),
            fixture_safe=True,
            worker_required=False,
        ),
        RendererContribution(
            'cdeadmin.result.document.fixture',
            frozenset({'document'}),
            'cdeadmin/results/DocumentFixture',
            render_document, export_json, frozenset({'json'}), True, True,
        ),
        RendererContribution(
            'cdeadmin.result.document.tree',
            frozenset({'document'}),
            'cdeadmin/results/DocumentTreeView',
            render_document, export_json, frozenset({'json', 'jsonl'}),
            False, True,
        ),
        RendererContribution(
            'cdeadmin.result.bitemporal-document.inspector',
            frozenset({'document'}),
            'cdeadmin/results/BitemporalDocumentView',
            render_bitemporal_document, export_tabular,
            frozenset({'csv', 'json', 'jsonl'}), False, True,
        ),
        RendererContribution(
            'cdeadmin.result.graph.fixture',
            frozenset({'graph'}),
            'cdeadmin/results/GraphFixture',
            render_graph, export_json, frozenset({'json'}), True, True,
        ),
        RendererContribution(
            'cdeadmin.result.graph.canvas',
            frozenset({'graph'}),
            'cdeadmin/results/GraphView',
            render_graph, export_json, frozenset({'json', 'jsonl'}),
            False, True,
        ),
        RendererContribution(
            'cdeadmin.result.key-value.fixture',
            frozenset({'key_value'}),
            'cdeadmin/results/KeyValueFixture',
            render_key_value, export_json,
            frozenset({'json'}), True, False,
        ),
        RendererContribution(
            'cdeadmin.result.time-series.fixture',
            frozenset({'time_series'}),
            'cdeadmin/results/TimeSeriesFixture',
            render_time_series, export_json,
            frozenset({'json'}), True, True,
        ),
        RendererContribution(
            'cdeadmin.result.time-series.explorer',
            frozenset({'time_series'}),
            'cdeadmin/results/TimeSeriesView',
            render_time_series, export_tabular,
            frozenset({'csv', 'json', 'jsonl'}), False, True,
        ),
        RendererContribution(
            'cdeadmin.result.vector.fixture',
            frozenset({'vector'}),
            'cdeadmin/results/VectorFixture',
            render_vector, export_json, frozenset({'json'}), True, True,
        ),
        RendererContribution(
            'cdeadmin.result.vector.explorer',
            frozenset({'vector'}),
            'cdeadmin/results/VectorView',
            render_vector, export_json,
            frozenset({'json', 'jsonl'}), False, True,
        ),
        RendererContribution(
            'cdeadmin.result.search.fixture',
            frozenset({'search'}),
            'cdeadmin/results/SearchFixture',
            render_search, export_json, frozenset({'json'}), True, True,
        ),
        RendererContribution(
            'cdeadmin.result.search.hits',
            frozenset({'search'}),
            'cdeadmin/results/SearchView',
            render_search, export_json,
            frozenset({'json', 'jsonl'}), False, True,
        ),
        RendererContribution(
            'cdeadmin.result.spatial.fixture',
            frozenset({'spatial'}),
            'cdeadmin/results/SpatialFixture',
            render_spatial, export_json, frozenset({'json'}), True, True,
        ),
        RendererContribution(
            'cdeadmin.result.columnar.fixture',
            frozenset({'columnar'}),
            'cdeadmin/results/ColumnarFixture',
            render_columnar, export_json,
            frozenset({'json'}), True, True,
        ),
        RendererContribution(
            'cdeadmin.result.columnar.grid',
            frozenset({'columnar'}),
            'cdeadmin/results/ColumnarView',
            render_columnar, export_tabular,
            frozenset({'csv', 'json', 'jsonl'}), False, True,
        ),
        RendererContribution(
            'cdeadmin.result.wide-column.grid',
            frozenset({'wide_column'}),
            'cdeadmin/results/WideColumnView',
            render_wide_column, export_tabular,
            frozenset({'csv', 'json', 'jsonl'}), False, True,
        ),
        RendererContribution(
            'cdeadmin.result.cellset.pivot',
            frozenset({'cellset'}),
            'cdeadmin/results/CubePivotView',
            render_cellset, export_json,
            frozenset({'json', 'jsonl'}), True, True,
        ),
    )
