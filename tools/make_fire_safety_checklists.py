"""Build the two field checklists as Word documents, one per check:

  * gas detectors  -- bump test, 2 detectors per BESS container
  * solenoid valves -- functional test after the fire-system upgrade,
                       1 valve per BESS container

Both are organised by the plant's 9 design zones, list every BESS container
with its registered serial number, and come pre-ticked: the work is already
done, the sheets are the record of it. Black type only -- no accent colour
anywhere, including headings, which Word otherwise renders blue.

Reads the asset register (containers, container_type='Battery') read-only.

    python tools/make_fire_safety_checklists.py [--out DIR] [--blank]

--blank leaves the boxes empty, for the next round.
"""
from __future__ import annotations

import argparse
import datetime
import os
import sqlite3
import sys

from docx import Document
from docx.enum.section import WD_ORIENT
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

BLACK = RGBColor(0x00, 0x00, 0x00)
HDR_BG = 'D9D9D9'          # grey, so the header row never reads as an accent
SUB_BG = 'F2F2F2'
TICK, BOX = '☑', '☐'          # cast ballot box, empty ballot box
SYMBOL_FONT = 'Segoe UI Symbol'          # ships with Windows, renders both
BODY_FONT = 'Calibri'

SITE = 'ACWA RIVERSIDE BESS'
LOCATION = 'Tashkent, Uzbekistan'


# --------------------------------------------------------------------------
# asset register
# --------------------------------------------------------------------------
def load_containers(db_path: str, project_id: int = 1):
    """[(zone, zone_block, plant_block, bess_no, serial)] ordered for the sheet."""
    conn = sqlite3.connect(f'file:{db_path}?mode=ro', uri=True)
    try:
        rows = conn.execute(
            "SELECT zone_number, block_number, container_index, serial_number "
            "  FROM containers "
            " WHERE project_id=? AND container_type='Battery' "
            " ORDER BY zone_number, block_number, container_index",
            (project_id,)).fetchall()
    finally:
        conn.close()
    if not rows:
        raise SystemExit(f'No battery containers for project {project_id} '
                         f'in {db_path}')
    # container_index 3..6 are the block's four battery containers; number
    # them 1..4 the way the app's asset tree shows them.
    idx_base = min(r[2] for r in rows)
    out = []
    for zone, blk, idx, serial in rows:
        out.append({'zone': zone, 'zone_block': blk,
                    'bess': idx - idx_base + 1,
                    'serial': (serial or '').strip()})
    # plant block number = running order of (zone, block) over the whole plant
    seen, plant = [], {}
    for r in out:
        key = (r['zone'], r['zone_block'])
        if key not in plant:
            seen.append(key)
            plant[key] = len(seen)
    for r in out:
        r['plant_block'] = plant[(r['zone'], r['zone_block'])]
    return out


# --------------------------------------------------------------------------
# docx helpers -- everything black, nothing inherited from Word's styles
# --------------------------------------------------------------------------
def _cell_bg(cell, hex_rgb):
    el = OxmlElement('w:shd')
    el.set(qn('w:val'), 'clear')
    el.set(qn('w:color'), 'auto')
    el.set(qn('w:fill'), hex_rgb)
    cell._tc.get_or_add_tcPr().append(el)


def _repeat_header(row):
    pr = row._tr.get_or_add_trPr()
    el = OxmlElement('w:tblHeader')
    el.set(qn('w:val'), 'true')
    pr.append(el)


def _write(cell, text, *, bold=False, size=9, align=None, symbol=False):
    cell.text = ''
    p = cell.paragraphs[0]
    p.paragraph_format.space_before = Pt(1)
    p.paragraph_format.space_after = Pt(1)
    if align is not None:
        p.alignment = align
    run = p.add_run(text)
    run.bold = bold
    run.font.size = Pt(size)
    run.font.color.rgb = BLACK
    run.font.name = SYMBOL_FONT if symbol else BODY_FONT
    return run


def _para(doc, text, *, bold=False, size=10, space_after=4, align=None):
    p = doc.add_paragraph()
    p.paragraph_format.space_after = Pt(space_after)
    p.paragraph_format.space_before = Pt(0)
    if align is not None:
        p.alignment = align
    run = p.add_run(text)
    run.bold = bold
    run.font.size = Pt(size)
    run.font.color.rgb = BLACK
    run.font.name = BODY_FONT
    return p


def _heading(doc, text, size=13):
    """A heading built from a plain paragraph: Word's Heading styles are blue."""
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(10)
    p.paragraph_format.space_after = Pt(4)
    p.keep_with_next = True
    run = p.add_run(text)
    run.bold = True
    run.font.size = Pt(size)
    run.font.color.rgb = BLACK
    run.font.name = BODY_FONT
    return p


def _grid(doc, widths):
    t = doc.add_table(rows=1, cols=len(widths))
    t.style = 'Table Grid'
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    t.autofit = False
    for row in t.rows:
        for cell, w in zip(row.cells, widths):
            cell.width = Cm(w)
    return t


def _new_doc():
    doc = Document()
    st = doc.styles['Normal']
    st.font.name = BODY_FONT
    st.font.size = Pt(10)
    st.font.color.rgb = BLACK
    s = doc.sections[0]
    s.orientation = WD_ORIENT.PORTRAIT
    s.page_width, s.page_height = Cm(21.0), Cm(29.7)
    s.left_margin = s.right_margin = Cm(2.0)
    s.top_margin = s.bottom_margin = Cm(1.6)
    return doc


def _info_block(doc, title, subtitle, fields, legend):
    _para(doc, title, bold=True, size=15, space_after=2)
    _para(doc, f'{SITE} — {LOCATION}', size=10, space_after=2)
    _para(doc, subtitle, size=9.5, space_after=8)

    t = _grid(doc, [4.2, 4.3, 4.2, 4.3])
    hdr = t.rows[0]
    for i, (k, v) in enumerate(fields[:2]):
        _write(hdr.cells[i * 2], k, bold=True)
        _write(hdr.cells[i * 2 + 1], v)
    for i in range(0, len(fields) - 2, 2):
        row = t.add_row()
        pair = fields[2 + i:4 + i]
        for j, (k, v) in enumerate(pair):
            _write(row.cells[j * 2], k, bold=True)
            _write(row.cells[j * 2 + 1], v)
        if len(pair) == 1:
            _write(row.cells[2], '')
            _write(row.cells[3], '')
    for row in t.rows:
        for c in (0, 2):
            _cell_bg(row.cells[c], SUB_BG)
    _para(doc, '', size=6, space_after=2)
    _para(doc, legend, size=9, space_after=2)


def _signature_block(doc):
    _heading(doc, 'Confirmation', size=12)
    _para(doc, 'The undersigned confirm that every check recorded above was '
               'carried out on site and that the results are as stated.',
          size=9, space_after=6)
    t = _grid(doc, [4.6, 4.4, 4.2, 3.8])
    hdr = t.rows[0]
    for i, h in enumerate(['Role', 'Name', 'Signature', 'Date']):
        _write(hdr.cells[i], h, bold=True, align=WD_ALIGN_PARAGRAPH.CENTER)
        _cell_bg(hdr.cells[i], HDR_BG)
    _repeat_header(hdr)
    for role in ('Carried out by (Technician)',
                 'Carried out by (Technician)',
                 'Witnessed by (Site Supervisor)',
                 'Reviewed by (O&M Engineer)',
                 'Customer representative'):
        row = t.add_row()
        _write(row.cells[0], role, size=9)
        for c in range(1, 4):
            _write(row.cells[c], '')
        row.height = Cm(1.0)


# --------------------------------------------------------------------------
# the two sheets
# --------------------------------------------------------------------------
def build(doc, containers, *, checks, widths, headers, ticked=True):
    """One zone section per design zone; `checks` is the list of column
    labels that carry a tick box."""
    zones = sorted({c['zone'] for c in containers})
    n_check = len(checks)
    for zi, z in enumerate(zones):
        rows = [c for c in containers if c['zone'] == z]
        blocks = sorted({c['plant_block'] for c in rows})
        span = (f'block {blocks[0]}' if len(blocks) == 1
                else f'blocks {blocks[0]}–{blocks[-1]}')
        if zi:
            doc.add_page_break()
        _heading(doc, f'ZONE {z} — {span}')
        _para(doc, f'{len(blocks)} blocks · {len(rows)} BESS containers · '
                   f'{len(rows) * n_check} '
                   f'{"item" if len(rows) * n_check == 1 else "items"} to check',
              size=9, space_after=4)

        t = _grid(doc, widths)
        hdr = t.rows[0]
        for i, h in enumerate(headers):
            _write(hdr.cells[i], h, bold=True, size=9,
                   align=WD_ALIGN_PARAGRAPH.CENTER)
            _cell_bg(hdr.cells[i], HDR_BG)
        _repeat_header(hdr)

        last_block = None
        for c in rows:
            row = t.add_row()
            cells = row.cells
            # block number only on the first of its four containers, so the
            # eye groups them without a merged cell
            first = c['plant_block'] != last_block
            _write(cells[0], str(c['plant_block']) if first else '', size=9,
                   bold=first, align=WD_ALIGN_PARAGRAPH.CENTER)
            _write(cells[1], str(c['zone_block']) if first else '', size=9,
                   align=WD_ALIGN_PARAGRAPH.CENTER)
            _write(cells[2], f'BESS {c["bess"]}', size=9,
                   align=WD_ALIGN_PARAGRAPH.CENTER)
            _write(cells[3], c['serial'], size=9,
                   align=WD_ALIGN_PARAGRAPH.CENTER)
            for k in range(n_check):
                _write(cells[4 + k], TICK if ticked else BOX, size=12,
                       align=WD_ALIGN_PARAGRAPH.CENTER, symbol=True)
            _write(cells[4 + n_check], '', size=9)
            last_block = c['plant_block']

    doc.add_page_break()
    _signature_block(doc)


def gas_detector_sheet(containers, ticked, test_date):
    doc = _new_doc()
    n = len(containers)
    _info_block(
        doc,
        'GAS DETECTOR BUMP TEST — CHECKLIST',
        f'Scope: all {n} BESS containers, 2 gas detectors each — '
        f'{n * 2} detectors across 9 design zones.',
        [('Test date', test_date),
         ('Procedure', 'Gas detector bump test'),
         ('Carried out by', ''),
         ('PTW No.', ''),
         ('Detector type / model', ''),
         ('Test gas (type, conc.)', ''),
         ('Gas cylinder lot No.', ''),
         ('Cylinder expiry date', ''),
         ('Response time limit', ''),
         ('Next bump test due', '')],
        f'{TICK} = bump test passed: the detector responded to the test gas '
        f'and the alarm was raised within the accepted response time.   '
        f'{BOX} = not passed — state the finding and the action in Remarks.',
    )
    build(doc, containers,
          checks=['Detector 1', 'Detector 2'],
          widths=[1.7, 1.5, 2.3, 3.3, 1.9, 1.9, 4.4],
          headers=['Block', 'Blk in zone', 'BESS container', 'Serial number',
                   'Detector 1', 'Detector 2', 'Remarks'],
          ticked=ticked)
    return doc


def solenoid_valve_sheet(containers, ticked, test_date):
    doc = _new_doc()
    n = len(containers)
    _info_block(
        doc,
        'FIRE SUPPRESSION SOLENOID VALVE — FUNCTIONAL TEST CHECKLIST',
        f'Functional test after the fire-detection and suppression system '
        f'upgrade. Scope: all {n} BESS containers, 1 solenoid valve each.',
        [('Test date', test_date),
         ('Procedure', 'Solenoid valve functional test'),
         ('Carried out by', ''),
         ('PTW No.', ''),
         ('Upgrade / modification ref.', ''),
         ('Fire panel type', ''),
         ('Valve type / model', ''),
         ('Actuation method', ''),
         ('Agent released during test', 'No — valve actuation only'),
         ('Next functional test due', '')],
        f'{TICK} = valve operated correctly: it actuated on the command from '
        f'the fire panel, the panel indicated the release, and the system was '
        f'reset to normal afterwards.   {BOX} = did not operate correctly — '
        f'state the finding and the action in Remarks.',
    )
    build(doc, containers,
          checks=['Solenoid valve'],
          widths=[1.7, 1.5, 2.3, 3.3, 2.6, 5.6],
          headers=['Block', 'Blk in zone', 'BESS container', 'Serial number',
                   'Solenoid valve', 'Remarks'],
          ticked=ticked)
    return doc


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--db', default=os.path.join('dist', 'pv_bess_tracker.db'))
    ap.add_argument('--out', default='.')
    ap.add_argument('--project', type=int, default=1)
    ap.add_argument('--blank', action='store_true',
                    help='leave the boxes empty instead of pre-ticked')
    ap.add_argument('--date', default='')
    a = ap.parse_args()

    db = os.path.abspath(a.db)
    if not os.path.exists(db):
        raise SystemExit(f'database not found: {db}')
    containers = load_containers(db, a.project)
    zones = sorted({c['zone'] for c in containers})
    blocks = {c['plant_block'] for c in containers}
    print(f'{len(containers)} BESS containers, {len(blocks)} blocks, '
          f'{len(zones)} zones {zones}')
    if len(containers) != len(blocks) * 4:
        print(f'  note: {len(containers)} containers over {len(blocks)} '
              f'blocks is not 4 per block')

    ticked = not a.blank
    date = a.date or datetime.date.today().strftime('%d %B %Y')
    os.makedirs(a.out, exist_ok=True)
    suffix = '_blank' if a.blank else ''
    jobs = [('Gas_detector_bump_test_checklist', gas_detector_sheet),
            ('Solenoid_valve_functional_test_checklist', solenoid_valve_sheet)]
    for name, fn in jobs:
        path = os.path.join(a.out, f'{name}{suffix}.docx')
        fn(containers, ticked, date).save(path)
        print(f'  wrote {path}  ({os.path.getsize(path) / 1024:.0f} KB)')


if __name__ == '__main__':
    sys.exit(main())
