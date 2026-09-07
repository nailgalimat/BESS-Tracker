"""
services/docx_renderer.py
--------------------------
Shared helpers for rendering BESS monthly reports as Word documents.

These mirror the ReportLab-based PDF renderers (section_header, kpi_row,
styled_table, fig_to_image, hr) so the same report data can be emitted in
either PDF or DOCX without duplicating the section logic.

Used by:
  - services.bukhara_report_service.generate_bukhara_report(output_format='docx')
  - services.tashkent_report_service.generate_tashkent_report(output_format='docx')
"""
from __future__ import annotations
import io
import matplotlib
matplotlib.use('Agg')

from docx import Document
from docx.shared import Cm, Pt, RGBColor, Inches
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_ALIGN_VERTICAL, WD_TABLE_ALIGNMENT
from docx.oxml.ns import qn
from docx.oxml import OxmlElement


# ── Colour palette (mirror the PDF palette) ──────────────────────────────────
NAVY      = RGBColor(0x1A, 0x2B, 0x45)
BLUE      = RGBColor(0x00, 0x71, 0xE3)
LIGHT_BLU = RGBColor(0xE8, 0xF0, 0xFD)
GREEN     = RGBColor(0x34, 0xC7, 0x59)
ORANGE    = RGBColor(0xFF, 0x95, 0x00)
RED       = RGBColor(0xFF, 0x3B, 0x30)
GREY_LINE = RGBColor(0xE0, 0xE4, 0xEA)
TEXT_MUTE = RGBColor(0x6B, 0x7A, 0x8D)
WHITE     = RGBColor(0xFF, 0xFF, 0xFF)


# ── Low-level XML helpers ────────────────────────────────────────────────────

def _set_cell_bg(cell, rgb_hex: str):
    """Apply a background fill colour to a table cell. rgb_hex is 6 chars."""
    tc_pr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement('w:shd')
    shd.set(qn('w:val'), 'clear')
    shd.set(qn('w:color'), 'auto')
    shd.set(qn('w:fill'), rgb_hex)
    tc_pr.append(shd)


def _make_doc(margin_cm=2.0):
    doc = Document()
    for section in doc.sections:
        section.top_margin = Cm(margin_cm)
        section.bottom_margin = Cm(margin_cm)
        section.left_margin = Cm(margin_cm)
        section.right_margin = Cm(margin_cm)
    # Default font
    style = doc.styles['Normal']
    style.font.name = 'Calibri'
    style.font.size = Pt(10)
    return doc


# ── Section primitives ───────────────────────────────────────────────────────

def add_heading(doc, text, level=2, color=NAVY):
    """Add a styled heading paragraph (mirrors PDF section_header)."""
    p = doc.add_paragraph()
    run = p.add_run(text)
    run.bold = True
    run.font.color.rgb = color
    if level == 1:
        run.font.size = Pt(18)
    elif level == 2:
        run.font.size = Pt(13)
    elif level == 3:
        run.font.size = Pt(11)
    p.paragraph_format.space_before = Pt(8)
    p.paragraph_format.space_after = Pt(4)
    return p


def add_section_banner(doc, text):
    """Light-blue full-width banner heading — mirrors PDF section_header look."""
    table = doc.add_table(rows=1, cols=1)
    cell = table.cell(0, 0)
    _set_cell_bg(cell, 'E8F0FD')
    p = cell.paragraphs[0]
    run = p.add_run(text)
    run.bold = True
    run.font.size = Pt(13)
    run.font.color.rgb = NAVY
    cell.vertical_alignment = WD_ALIGN_VERTICAL.CENTER
    # Some bottom padding
    pPr = cell._tc.get_or_add_tcPr()
    return table


def add_paragraph(doc, text, *, bold=False, italic=False, size=10,
                   color=NAVY, align=None):
    p = doc.add_paragraph()
    if align == 'center':
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    elif align == 'right':
        p.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    # Allow basic HTML-ish <b>...</b> markup the PDF code passes around
    _add_runs(p, text, bold=bold, italic=italic, size=size, color=color)
    return p


def _add_runs(p, text, *, bold=False, italic=False, size=10, color=NAVY):
    """Render a string that may contain <b>...</b> markers as alternating
    bold/non-bold runs. Also handles &amp; / &lt; / &gt; HTML entities."""
    import re
    if not text:
        return
    # Decode common HTML entities used in PDF paragraph text
    text = (text.replace('&amp;', '&')
                .replace('&lt;', '<')
                .replace('&gt;', '>')
                .replace('&#160;', ' '))
    parts = re.split(r'(<b>|</b>)', text)
    is_bold = bold
    for part in parts:
        if part == '<b>':
            is_bold = True
        elif part == '</b>':
            is_bold = bold
        elif part:
            run = p.add_run(part)
            run.bold = is_bold
            run.italic = italic
            run.font.size = Pt(size)
            run.font.color.rgb = color


def add_kpi_row(doc, items):
    """Render a row of KPI tiles as a 1-row table with a coloured top border."""
    table = doc.add_table(rows=2, cols=len(items))
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    for i, (val, lbl, accent_hex) in enumerate(items):
        # Top cell — big value
        v_cell = table.cell(0, i)
        vp = v_cell.paragraphs[0]
        vp.alignment = WD_ALIGN_PARAGRAPH.CENTER
        vrun = vp.add_run(str(val))
        vrun.bold = True
        vrun.font.size = Pt(20)
        vrun.font.color.rgb = NAVY
        v_cell.vertical_alignment = WD_ALIGN_VERTICAL.CENTER
        # Coloured top border
        _set_top_border(v_cell, accent_hex.lstrip('#'))

        # Bottom cell — label
        l_cell = table.cell(1, i)
        lp = l_cell.paragraphs[0]
        lp.alignment = WD_ALIGN_PARAGRAPH.CENTER
        lrun = lp.add_run(lbl)
        lrun.font.size = Pt(8)
        lrun.font.color.rgb = TEXT_MUTE
    return table


def _set_top_border(cell, color_hex):
    tc_pr = cell._tc.get_or_add_tcPr()
    tcBorders = OxmlElement('w:tcBorders')
    top = OxmlElement('w:top')
    top.set(qn('w:val'), 'single')
    top.set(qn('w:sz'), '24')
    top.set(qn('w:color'), color_hex)
    tcBorders.append(top)
    tc_pr.append(tcBorders)


def add_styled_table(doc, headers, rows):
    """Table with NAVY header, alternating row backgrounds, grid lines.
    Cells render plain text (HTML <b>...</b> markers are stripped)."""
    table = doc.add_table(rows=len(rows) + 1, cols=len(headers))
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.autofit = True

    # Header
    for i, h in enumerate(headers):
        c = table.cell(0, i)
        _set_cell_bg(c, '1A2B45')
        p = c.paragraphs[0]
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run = p.add_run(str(h))
        run.bold = True
        run.font.size = Pt(8)
        run.font.color.rgb = WHITE

    # Rows
    for r_idx, row in enumerate(rows):
        for c_idx, val in enumerate(row):
            c = table.cell(r_idx + 1, c_idx)
            # Alternating row fill
            if r_idx % 2 == 1:
                _set_cell_bg(c, 'F5F7FA')
            p = c.paragraphs[0]
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            txt = '' if val is None else str(val)
            # Strip HTML tags that may have been in the original PDF paragraph
            import re as _re
            txt = _re.sub(r'<[^>]+>', '', txt)
            run = p.add_run(txt)
            run.font.size = Pt(8)
            run.font.color.rgb = NAVY
    _set_table_grid(table)
    return table


def _set_table_grid(table):
    """Apply a thin grey grid to every cell border."""
    tbl = table._tbl
    tblPr = tbl.tblPr
    tblBorders = OxmlElement('w:tblBorders')
    for edge in ('top', 'left', 'bottom', 'right', 'insideH', 'insideV'):
        e = OxmlElement(f'w:{edge}')
        e.set(qn('w:val'), 'single')
        e.set(qn('w:sz'), '4')
        e.set(qn('w:color'), 'E0E4EA')
        tblBorders.append(e)
    tblPr.append(tblBorders)


def add_image_from_fig(doc, fig, width_inches=6.5):
    """Save a matplotlib figure as PNG bytes and embed it in the document."""
    import matplotlib.pyplot as plt
    buf = io.BytesIO()
    fig.savefig(buf, format='png', dpi=150, bbox_inches='tight',
                facecolor='white', edgecolor='none')
    buf.seek(0)
    plt.close(fig)
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = p.add_run()
    run.add_picture(buf, width=Inches(width_inches))
    return p


def add_caption(doc, text):
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = p.add_run(text)
    run.italic = True
    run.font.size = Pt(8)
    run.font.color.rgb = TEXT_MUTE
    return p


def add_hr(doc):
    """Insert a thin horizontal rule by adding a paragraph with bottom border."""
    p = doc.add_paragraph()
    pPr = p._p.get_or_add_pPr()
    pBdr = OxmlElement('w:pBdr')
    bottom = OxmlElement('w:bottom')
    bottom.set(qn('w:val'), 'single')
    bottom.set(qn('w:sz'), '6')
    bottom.set(qn('w:color'), 'E0E4EA')
    pBdr.append(bottom)
    pPr.append(pBdr)


def add_cover(doc, report_month, site_name, n_blocks, period_str):
    """Cover page — banner + info table."""
    # Banner
    table = doc.add_table(rows=2, cols=1)
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    c1 = table.cell(0, 0)
    _set_cell_bg(c1, '1A2B45')
    p1 = c1.paragraphs[0]
    p1.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r1 = p1.add_run('BESS OPERATIONS REPORT')
    r1.bold = True; r1.font.size = Pt(26); r1.font.color.rgb = WHITE
    c2 = table.cell(1, 0)
    _set_cell_bg(c2, '1A2B45')
    p2 = c2.paragraphs[0]
    p2.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r2 = p2.add_run(report_month)
    r2.font.size = Pt(16); r2.font.color.rgb = RGBColor(0x8F, 0xA3, 0xBE)

    doc.add_paragraph()
    info_rows = [
        ('Site / Project:',   site_name),
        ('Reporting period:', period_str),
        ('Blocks monitored:', f'{n_blocks} blocks'),
        ('Report type:',      'Monthly Operations & Performance Summary'),
    ]
    info = doc.add_table(rows=len(info_rows), cols=2)
    for i, (label, val) in enumerate(info_rows):
        c_l = info.cell(i, 0); c_v = info.cell(i, 1)
        _set_cell_bg(c_l, 'F5F7FA'); _set_cell_bg(c_v, 'F5F7FA')
        pl = c_l.paragraphs[0]; runl = pl.add_run(label)
        runl.bold = True; runl.font.size = Pt(10); runl.font.color.rgb = NAVY
        pv = c_v.paragraphs[0]; runv = pv.add_run(val)
        runv.font.size = Pt(10); runv.font.color.rgb = NAVY
    _set_table_grid(info)

    doc.add_paragraph()
    add_paragraph(doc,
        'This report summarises the operation and performance of the site for '
        'the reporting month. All figures are based on the site\'s own '
        'monitoring data — energy meters, state-of-charge and availability '
        'records.',
        size=8, color=TEXT_MUTE
    )
    doc.add_page_break()


def save_doc(doc, output_path):
    doc.save(output_path)
    return output_path


def make_doc(margin_cm=2.0):
    """Public constructor used by the report services."""
    return _make_doc(margin_cm)
