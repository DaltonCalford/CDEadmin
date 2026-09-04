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
import math
import zipfile
from pathlib import Path
from threading import Lock
from xml.sax.saxutils import escape
from typing import Any, Mapping

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    LongTable, PageBreak, Paragraph, SimpleDocTemplate, Spacer, TableStyle,
)

from .models import RendererContribution


PDF_MAX_ROWS = 1000
PDF_MAX_COLUMNS = 24
PDF_MAX_CELL_CHARACTERS = 256
_PDF_FONT_NAME = 'CDEadminRoboto'
_PDF_FONT_BOLD_NAME = 'CDEadminRobotoBold'
_PDF_FONT_LOCK = Lock()


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
    if export_format == 'xlsx':
        return export_xlsx(_metadata, records)
    if export_format == 'svg':
        return export_svg(_metadata, records)
    if export_format == 'pdf':
        return export_pdf(_metadata, records)
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


def _cell_value(value):
    if value is None:
        return ''
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(
            value, ensure_ascii=False, separators=(',', ':'), default=repr
        )
    return str(value)


def _xml_text(value, maximum):
    value = _cell_value(value)[:maximum]
    valid = ''.join(
        character if (
            character in '\t\n\r' or 0x20 <= ord(character) <= 0xD7FF or
            0xE000 <= ord(character) <= 0xFFFD or
            0x10000 <= ord(character) <= 0x10FFFF
        ) else '\uFFFD'
        for character in value
    )
    return escape(valid)


def _tabular_rows(metadata, records):
    names = _column_names(metadata, records)
    rows = [names]
    for record in records:
        if isinstance(record, Mapping):
            rows.append([record.get(name) for name in names])
        elif isinstance(record, (list, tuple)):
            rows.append(list(record))
        else:
            rows.append([record])
    return rows


def _excel_column(index):
    value = ''
    while index:
        index, remainder = divmod(index - 1, 26)
        value = chr(65 + remainder) + value
    return value


def _excel_number(value):
    """Return whether Excel can preserve a value as a finite number."""
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return abs(value) <= 999999999999999
    return isinstance(value, float) and math.isfinite(value)


def export_xlsx(metadata, records):
    """Create a bounded, formula-safe Office Open XML workbook."""
    rows = _tabular_rows(metadata, records)
    sheet_rows = []
    for row_number, row in enumerate(rows, 1):
        cells = []
        for column_number, raw in enumerate(row[:16384], 1):
            reference = f'{_excel_column(column_number)}{row_number}'
            if _excel_number(raw):
                cells.append(
                    f'<c r="{reference}" t="n"><v>{raw}</v></c>'
                )
            else:
                value = _xml_text(raw, 32767)
                cells.append(
                    f'<c r="{reference}" t="inlineStr"><is><t '
                    f'xml:space="preserve">{value}</t></is></c>'
                )
        sheet_rows.append(
            f'<row r="{row_number}">{"".join(cells)}</row>'
        )
    worksheet = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/'
        'spreadsheetml/2006/main"><sheetData>' +
        ''.join(sheet_rows) + '</sheetData></worksheet>'
    )
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('[Content_Types].xml',
                         '<?xml version="1.0" encoding="UTF-8"?>'
                         '<Types xmlns="http://schemas.openxmlformats.org/'
                         'package/2006/content-types">'
                         '<Default Extension="rels" ContentType="application/'
                         'vnd.openxmlformats-package.relationships+xml"/>'
                         '<Default Extension="xml" ContentType="application/'
                         'xml"/><Override PartName="/xl/workbook.xml" '
                         'ContentType="application/vnd.openxmlformats-'
                         'officedocument.spreadsheetml.sheet.main+xml"/>'
                         '<Override PartName="/xl/worksheets/sheet1.xml" '
                         'ContentType="application/vnd.openxmlformats-'
                         'officedocument.spreadsheetml.worksheet+xml"/>'
                         '</Types>')
        archive.writestr('_rels/.rels',
                         '<?xml version="1.0" encoding="UTF-8"?>'
                         '<Relationships xmlns="http://schemas.openxmlformats.'
                         'org/package/2006/relationships"><Relationship '
                         'Id="rId1" Type="http://schemas.openxmlformats.org/'
                         'officeDocument/2006/relationships/officeDocument" '
                         'Target="xl/workbook.xml"/></Relationships>')
        archive.writestr('xl/workbook.xml',
                         '<?xml version="1.0" encoding="UTF-8"?>'
                         '<workbook xmlns="http://schemas.openxmlformats.org/'
                         'spreadsheetml/2006/main" xmlns:r="http://schemas.'
                         'openxmlformats.org/officeDocument/2006/'
                         'relationships"'
                         '><sheets><sheet name="CDEadmin Result" sheetId="1" '
                         'r:id="rId1"/></sheets></workbook>')
        archive.writestr('xl/_rels/workbook.xml.rels',
                         '<?xml version="1.0" encoding="UTF-8"?>'
                         '<Relationships xmlns="http://schemas.openxmlformats.'
                         'org/package/2006/relationships"><Relationship '
                         'Id="rId1" Type="http://schemas.openxmlformats.org/'
                         'officeDocument/2006/relationships/worksheet" '
                         'Target="worksheets/sheet1.xml"/></Relationships>')
        archive.writestr('xl/worksheets/sheet1.xml', worksheet)
    return output.getvalue()


def export_svg(metadata, records):
    """Create a standalone, non-scriptable SVG table presentation."""
    rows = _tabular_rows(metadata, records)
    lines = [' | '.join(_cell_value(value) for value in row) for row in rows]
    visible = lines[:201]
    if len(lines) > len(visible):
        visible.append(f'… {len(lines) - len(visible)} additional rows')
    height = max(80, 42 + 20 * len(visible))
    text = ''.join(
        f'<text x="20" y="{34 + index * 20}" '
        f'font-family="monospace" font-size="13">'
        f'{_xml_text(line, 240)}</text>'
        for index, line in enumerate(visible)
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<svg xmlns="http://www.w3.org/2000/svg" width="1200" '
        f'height="{height}" viewBox="0 0 1200 {height}">'
        '<rect width="100%" height="100%" fill="white"/>'
        '<g fill="#172b4d">' + text + '</g></svg>'
    ).encode('utf-8')


def _pdf_fonts():
    """Register CDEadmin-shipped fonts once per rendering process."""
    registered = set(pdfmetrics.getRegisteredFontNames())
    if {_PDF_FONT_NAME, _PDF_FONT_BOLD_NAME}.issubset(registered):
        return
    font_root = Path(__file__).resolve().parents[2] / 'static' / 'fonts'
    regular = font_root / 'Roboto-Regular.ttf'
    bold = font_root / 'Roboto-Bold.ttf'
    if not regular.is_file() or not bold.is_file():
        raise RuntimeError('CDEadmin PDF fonts are unavailable')
    with _PDF_FONT_LOCK:
        registered = set(pdfmetrics.getRegisteredFontNames())
        if _PDF_FONT_NAME not in registered:
            pdfmetrics.registerFont(TTFont(_PDF_FONT_NAME, str(regular)))
        if _PDF_FONT_BOLD_NAME not in registered:
            pdfmetrics.registerFont(TTFont(_PDF_FONT_BOLD_NAME, str(bold)))


def _pdf_page(canvas, document):
    """Draw inert CDEadmin page identity and pagination."""
    canvas.saveState()
    canvas.setFont(_PDF_FONT_NAME, 7)
    canvas.setFillColor(colors.HexColor('#52606d'))
    canvas.drawString(document.leftMargin, 8 * mm, 'CDEadmin result export')
    canvas.drawRightString(
        document.pagesize[0] - document.rightMargin,
        8 * mm,
        f'Page {document.page}',
    )
    canvas.restoreState()


def export_pdf(metadata, records):
    """Create a bounded, font-embedded, paginated PDF result table."""
    _pdf_fonts()
    rows = _tabular_rows(metadata, records)
    original_columns = len(rows[0]) if rows else 0
    original_records = max(0, len(rows) - 1)
    columns = min(original_columns, PDF_MAX_COLUMNS)
    visible = [row[:columns] for row in rows[:PDF_MAX_ROWS + 1]]
    omitted_rows = max(0, original_records - PDF_MAX_ROWS)
    omitted_columns = max(0, original_columns - PDF_MAX_COLUMNS)
    output = io.BytesIO()
    document = SimpleDocTemplate(
        output, pagesize=landscape(A4), title='CDEadmin Result',
        author='CDEadmin', subject='Provider result export',
        leftMargin=10 * mm, rightMargin=10 * mm,
        topMargin=12 * mm, bottomMargin=14 * mm,
        pageCompression=1, invariant=1,
    )
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        'CDEadminPDFTitle', parent=styles['Title'],
        fontName=_PDF_FONT_BOLD_NAME, fontSize=14, leading=17,
        alignment=TA_CENTER, textColor=colors.HexColor('#172b4d'),
    )
    cell_style = ParagraphStyle(
        'CDEadminPDFCell', parent=styles['BodyText'],
        fontName=_PDF_FONT_NAME, fontSize=6.5, leading=8,
        textColor=colors.HexColor('#172b4d'),
    )
    header_style = ParagraphStyle(
        'CDEadminPDFHeader', parent=cell_style,
        fontName=_PDF_FONT_BOLD_NAME, textColor=colors.white,
    )
    story = [Paragraph('CDEadmin Result', title_style), Spacer(1, 4 * mm)]
    if columns:
        table_rows = []
        for row_number, row in enumerate(visible):
            style = header_style if row_number == 0 else cell_style
            table_rows.append([
                Paragraph(_xml_text(value, PDF_MAX_CELL_CHARACTERS), style)
                for value in row
            ])
        width = (
            document.pagesize[0] - document.leftMargin - document.rightMargin
        )
        table = LongTable(
            table_rows, repeatRows=1,
            colWidths=[width / columns] * columns,
            splitByRow=1, splitInRow=1, hAlign='LEFT',
        )
        table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#172b4d')),
            ('GRID', (0, 0), (-1, -1), 0.25,
             colors.HexColor('#bcccdc')),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('LEFTPADDING', (0, 0), (-1, -1), 2),
            ('RIGHTPADDING', (0, 0), (-1, -1), 2),
            ('TOPPADDING', (0, 0), (-1, -1), 2),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), (
                colors.white, colors.HexColor('#f5f7fa'),
            )),
        ]))
        story.append(table)
    else:
        story.append(Paragraph('No tabular columns.', cell_style))
    notices = []
    if omitted_rows:
        notices.append(f'{omitted_rows} additional rows omitted')
    if omitted_columns:
        notices.append(f'{omitted_columns} additional columns omitted')
    if notices:
        story.extend((PageBreak(), Paragraph(
            'Export bounds: ' + '; '.join(notices) + '.', cell_style
        )))
    document.build(story, onFirstPage=_pdf_page, onLaterPages=_pdf_page)
    return output.getvalue()


def export_portable(metadata, records, export_format):
    if export_format in {'json', 'jsonl'}:
        return export_json(metadata, records, export_format)
    if export_format == 'xlsx':
        return export_xlsx(metadata, records)
    if export_format == 'svg':
        return export_svg(metadata, records)
    if export_format == 'pdf':
        return export_pdf(metadata, records)
    return export_tabular(metadata, records, export_format)


def export_tabular(metadata, records, export_format):
    if export_format == 'xlsx':
        return export_xlsx(metadata, records)
    if export_format == 'svg':
        return export_svg(metadata, records)
    if export_format == 'pdf':
        return export_pdf(metadata, records)
    if export_format in {'json', 'jsonl'}:
        return export_json(metadata, records, export_format)
    output = io.StringIO(newline='')
    writer = csv.writer(output)
    writer.writerows(_tabular_rows(metadata, records))
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
            frozenset({'csv', 'json', 'xlsx', 'svg', 'pdf'}),
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
            render_document, export_json,
            frozenset({'json', 'jsonl', 'xlsx', 'svg', 'pdf'}),
            False, True,
        ),
        RendererContribution(
            'cdeadmin.result.bitemporal-document.inspector',
            frozenset({'document'}),
            'cdeadmin/results/BitemporalDocumentView',
            render_bitemporal_document, export_tabular,
            frozenset({'csv', 'json', 'jsonl', 'xlsx', 'svg', 'pdf'}),
            False, True,
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
            render_graph, export_json,
            frozenset({'json', 'jsonl', 'xlsx', 'svg', 'pdf'}),
            False, True,
        ),
        RendererContribution(
            'cdeadmin.result.key-value.inspector',
            frozenset({'key_value'}),
            'cdeadmin/results/KeyValueView',
            render_key_value, export_json,
            frozenset({'json', 'jsonl', 'xlsx', 'svg', 'pdf'}), False, True,
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
            frozenset({'csv', 'json', 'jsonl', 'xlsx', 'svg', 'pdf'}),
            False, True,
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
            frozenset({'json', 'jsonl', 'xlsx', 'svg', 'pdf'}), False, True,
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
            frozenset({'json', 'jsonl', 'xlsx', 'svg', 'pdf'}), False, True,
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
            frozenset({'csv', 'json', 'jsonl', 'xlsx', 'svg', 'pdf'}),
            False, True,
        ),
        RendererContribution(
            'cdeadmin.result.wide-column.grid',
            frozenset({'wide_column'}),
            'cdeadmin/results/WideColumnView',
            render_wide_column, export_tabular,
            frozenset({'csv', 'json', 'jsonl', 'xlsx', 'svg', 'pdf'}),
            False, True,
        ),
        RendererContribution(
            'cdeadmin.result.cellset.pivot',
            frozenset({'cellset'}),
            'cdeadmin/results/CubePivotView',
            render_cellset, export_portable,
            frozenset({'json', 'jsonl', 'xlsx', 'svg', 'pdf'}), True, True,
        ),
    )
