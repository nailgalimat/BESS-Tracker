"""Build the combustible gas detector bump test record as a Word document.

Same shape as the solenoid valve record: the method first, then the site's
own checklist. The method follows the project test procedure sections 7.3
(Testing of Gas detectors/sensors) and 7.4 (System energization). Its HSE
content — risk assessment, PPE, permits, notifications, emergency response —
is not reproduced; the Method Statement remains the controlling document.

Section 4, test tools and instruments, is left blank on purpose.

Two gas detectors per BESS container, one on each side of the container.

    python tools/make_fss_gas_detector_test_record.py [--out DIR] [--blank]
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

BLACK = RGBColor(0, 0, 0)
HDR_BG, SUB_BG = 'D9D9D9', 'F2F2F2'
TICK, BOX = '☑', '☐'
SYMBOL_FONT, BODY_FONT = 'Segoe UI Symbol', 'Calibri'
SITE, LOCATION = 'ACWA RIVERSIDE BESS', 'Tashkent, Uzbekistan'
SOURCE = 'Project FSS Test Procedure, sections 7.3 and 7.4'
DETECTORS_PER_CONTAINER = 2


# ── asset register ──────────────────────────────────────────────────────────
def load_containers(db_path, project_id=1):
    conn = sqlite3.connect(f'file:{db_path}?mode=ro', uri=True)
    try:
        rows = conn.execute(
            "SELECT zone_number, block_number, container_index, serial_number "
            "FROM containers WHERE project_id=? AND container_type='Battery' "
            "ORDER BY zone_number, block_number, container_index",
            (project_id,)).fetchall()
    finally:
        conn.close()
    if not rows:
        raise SystemExit(f'no battery containers for project {project_id}')
    base = min(r[2] for r in rows)
    out = [{'zone': z, 'zone_block': b, 'bess': i - base + 1,
            'serial': (s or '').strip()} for z, b, i, s in rows]
    order, plant = [], {}
    for r in out:
        k = (r['zone'], r['zone_block'])
        if k not in plant:
            order.append(k)
            plant[k] = len(order)
    for r in out:
        r['plant_block'] = plant[(r['zone'], r['zone_block'])]
    return out


# ── docx helpers: everything black, no Word heading styles ──────────────────
def _bg(cell, hexrgb):
    el = OxmlElement('w:shd')
    el.set(qn('w:val'), 'clear'); el.set(qn('w:color'), 'auto')
    el.set(qn('w:fill'), hexrgb)
    cell._tc.get_or_add_tcPr().append(el)


def _repeat(row):
    el = OxmlElement('w:tblHeader'); el.set(qn('w:val'), 'true')
    row._tr.get_or_add_trPr().append(el)


def _w(cell, text, *, bold=False, size=9, align=None, symbol=False):
    cell.text = ''
    p = cell.paragraphs[0]
    p.paragraph_format.space_before = Pt(1)
    p.paragraph_format.space_after = Pt(1)
    if align is not None:
        p.alignment = align
    r = p.add_run(text)
    r.bold = bold
    r.font.size = Pt(size)
    r.font.color.rgb = BLACK
    r.font.name = SYMBOL_FONT if symbol else BODY_FONT
    return r


def _p(doc, text='', *, bold=False, italic=False, size=10, after=4, before=0):
    p = doc.add_paragraph()
    p.paragraph_format.space_after = Pt(after)
    p.paragraph_format.space_before = Pt(before)
    r = p.add_run(text)
    r.bold, r.italic = bold, italic
    r.font.size = Pt(size)
    r.font.color.rgb = BLACK
    r.font.name = BODY_FONT
    return p


def _h(doc, text, size=12, before=12):
    p = _p(doc, text, bold=True, size=size, after=4, before=before)
    p.keep_with_next = True
    return p


def _grid(doc, widths):
    t = doc.add_table(rows=1, cols=len(widths))
    t.style = 'Table Grid'
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    t.autofit = False
    for row in t.rows:
        for c, w in zip(row.cells, widths):
            c.width = Cm(w)
    return t


def _head(t, labels):
    for i, h in enumerate(labels):
        _w(t.rows[0].cells[i], h, bold=True, align=WD_ALIGN_PARAGRAPH.CENTER)
        _bg(t.rows[0].cells[i], HDR_BG)
    _repeat(t.rows[0])
    return t


def _table(doc, labels, widths, rows, center=()):
    t = _head(_grid(doc, widths), labels)
    for r in rows:
        cells = t.add_row().cells
        for i, v in enumerate(r):
            _w(cells[i], str(v), size=9,
               align=WD_ALIGN_PARAGRAPH.CENTER if i in center else None)
    return t


def _note(doc, label, text):
    t = _grid(doc, [2.6, 14.4])
    _w(t.rows[0].cells[0], label, bold=True, size=9)
    _w(t.rows[0].cells[1], text, size=9)
    _bg(t.rows[0].cells[0], SUB_BG)
    _bg(t.rows[0].cells[1], SUB_BG)
    _p(doc, '', size=4, after=2)


def _steps(doc, title, steps):
    _p(doc, title, bold=True, size=10, after=2, before=8)
    for i, s in enumerate(steps, 1):
        p = doc.add_paragraph()
        p.paragraph_format.left_indent = Cm(0.8)
        p.paragraph_format.space_after = Pt(2)
        r = p.add_run(f'{i}.  {s}')
        r.font.size = Pt(9.5)
        r.font.color.rgb = BLACK
        r.font.name = BODY_FONT


def _doc():
    d = Document()
    st = d.styles['Normal']
    st.font.name, st.font.size, st.font.color.rgb = BODY_FONT, Pt(10), BLACK
    s = d.sections[0]
    s.orientation = WD_ORIENT.PORTRAIT
    s.page_width, s.page_height = Cm(21.0), Cm(29.7)
    s.left_margin = s.right_margin = Cm(2.0)
    s.top_margin = s.bottom_margin = Cm(1.6)
    return d


# ── the document ────────────────────────────────────────────────────────────
def build(containers, ticked, test_date):
    doc = _doc()
    n = len(containers)
    total = n * DETECTORS_PER_CONTAINER

    _p(doc, 'FIRE SUPPRESSION SYSTEM', bold=True, size=11, after=0)
    _p(doc, 'COMBUSTIBLE GAS DETECTOR BUMP TEST — RECORD', bold=True, size=15,
       after=2)
    _p(doc, f'{SITE} — {LOCATION}', size=10, after=2)
    _p(doc, 'Functional response test of the combustible gas detectors by '
            'applying test gas at the sensor.', size=9.5, after=2)
    _p(doc, f'Scope: {n} BESS containers across 9 design zones, '
            f'{DETECTORS_PER_CONTAINER} gas detectors each — {total} '
            f'detectors.', size=9.5, after=8)

    t = _grid(doc, [4.2, 4.3, 4.2, 4.3])
    fields = [('Test date', test_date), ('Document ref.', ''),
              ('Carried out by', ''), ('Witnessed by', ''),
              ('Procedure ref.', SOURCE), ('Revision', '')]
    for i in range(0, len(fields), 2):
        row = t.rows[0] if i == 0 else t.add_row()
        for j, (k, v) in enumerate(fields[i:i + 2]):
            _w(row.cells[j * 2], k, bold=True)
            _w(row.cells[j * 2 + 1], v)
    for row in t.rows:
        _bg(row.cells[0], SUB_BG); _bg(row.cells[2], SUB_BG)

    _p(doc, '', size=6, after=2)
    _p(doc, f'{TICK} = pass: the detector responded to the test gas and the '
            f'alarm was raised.   {BOX} = fail, state the finding and the '
            f'action in Remarks.', size=9, after=2)

    # 1 ─────────────────────────────────────────────────────────────────────
    _h(doc, '1.  PURPOSE AND SCOPE')
    _p(doc, 'To prove that each combustible gas detector responds to gas at '
            'the sensor and raises the corresponding alarm, and that the '
            'container is returned to normal operation afterwards. The test '
            'proves response, not calibrated accuracy: it is a bump test, '
            'not a calibration.', size=10, after=4)
    _p(doc, 'Included: application of test gas to each of the two detectors '
            'in every BESS container, verification of the resulting signal, '
            'recording of the result including failures, and restoration of '
            'the container to normal operation.', size=10, after=4)
    _p(doc, 'Excluded: calibration or re-ranging of any detector, changes to '
            'the FACP program, device addressing or field wiring, and testing '
            'of the smoke detectors, heat detectors, solenoid valves, fire '
            'intake and exhaust fans, which are covered separately.',
       size=10, after=4)
    _p(doc, f'This record reproduces the method from {SOURCE}. The HSE '
            f'content of the governing Method Statement — risk assessment, '
            f'PPE, permits, notifications, impairment control and emergency '
            f'response — is not repeated here; that document remains '
            f'controlling and applies in full.', size=9, italic=True, after=4)

    # 2 ─────────────────────────────────────────────────────────────────────
    _h(doc, '2.  SYSTEM DESCRIPTION')
    _p(doc, 'Each BESS container is fitted with two combustible gas '
            'detectors, mounted one on each side of the container, reporting '
            'to the fire alarm control panel through the addressable '
            'input/output module. They detect flammable off-gas released by '
            'a cell in thermal runaway, which precedes smoke and heat, and '
            'are therefore the earliest indication the system has.',
       size=10, after=6)
    _table(doc, ['Protected unit', 'Device', 'Quantity', 'Position'],
           [4.0, 5.4, 3.0, 4.6],
           [['BESS container', 'Combustible gas detector',
             f'{DETECTORS_PER_CONTAINER} per container',
             'One on each side of the container']],
           center=(2,))

    # 3 ─────────────────────────────────────────────────────────────────────
    _h(doc, '3.  TEST GAS')
    _p(doc, 'Ethanol was used as the test gas. The calibration gas specified '
            'by the detector manufacturer was not available on site, and the '
            'manufacturer confirmed that ethanol is acceptable for a '
            'functional response (bump) test of these detectors. The '
            'confirmation is held with the project records.', size=10,
       after=4)
    _note(doc, 'NOTE',
          'A bump test with a substitute gas proves that the sensor responds '
          'and that the signal path to the panel works. It does not verify '
          'the detector\'s calibrated reading, and does not replace '
          'calibration at the manufacturer\'s stated interval.')

    # 4 ─────────────────────────────────────────────────────────────────────
    _h(doc, '4.  TEST TOOLS AND INSTRUMENTS')
    _p(doc, 'To be completed with the tools actually used on site.', size=9,
       italic=True, after=6)
    _table(doc, ['No.', 'Tool', 'Model / spec.', 'Qty',
                 'Calibration ref. / validity'],
           [1.2, 5.0, 4.6, 1.6, 4.6],
           [[str(i), '', '', '', ''] for i in range(1, 9)], center=(0, 3))

    # 5 ─────────────────────────────────────────────────────────────────────
    doc.add_page_break()
    _h(doc, '5.  PRE-TEST CONDITIONS', before=0)
    _p(doc, 'Confirmed for each container before the first detector is '
            'tested.', size=9, italic=True, after=6)
    pre = [
        'The battery containers have been shut down from the LC and the '
        'operating mode changed to Local Mode.',
        'The system reports no faults and no standing alarms. Anything '
        'pre-existing is recorded first, so it is not mistaken for a test '
        'result.',
        'The two gas detectors of the container have been located and '
        'identified against the device schedule.',
        'The test gas bottle is placed at a suitable location and secured.',
        'The test pipe is properly connected to the gas bottle and the '
        'connection checked for leaks.',
    ]
    t = _head(_grid(doc, [1.2, 13.4, 2.4]),
              ['No.', 'Pre-test condition', 'Confirmed'])
    for i, txt in enumerate(pre, 1):
        c = t.add_row().cells
        _w(c[0], str(i), size=9, align=WD_ALIGN_PARAGRAPH.CENTER)
        _w(c[1], txt, size=9)
        _w(c[2], TICK if ticked else BOX, size=12,
           align=WD_ALIGN_PARAGRAPH.CENTER, symbol=True)
    _p(doc, '', size=4, after=2)

    # 6 ─────────────────────────────────────────────────────────────────────
    _h(doc, '6.  TEST METHOD')
    _p(doc, 'Carried out container by container. Each detector is tested '
            'individually and its result recorded before moving to the next.',
       size=10, after=6)
    _steps(doc, 'Phase 1 — Isolation and set-up', [
        'Shut down the battery containers from the LC and change the '
        'operating mode to Local Mode.',
        'Confirm the system reports no faults or alarms, and record the '
        'starting state.',
        'Locate the two gas detectors of the container, typically one on '
        'each side.',
        'Place the test gas bottle at a suitable location and connect the '
        'test pipe to it.',
    ])
    _steps(doc, 'Phase 2 — Detector response', [
        'Release the test gas through the pipe to the first gas detector to '
        'activate it. Apply at the sensor head and allow it to respond; do '
        'not over-apply.',
        'Verify the result once the signal is triggered: the detector '
        'indicates locally and the alarm is received at the panel with the '
        'correct address and description.',
        'Record the result for that detector — pass or fail. A failure is '
        'recorded as such, not retried until it passes.',
        'Repeat for the second detector of the container and record its '
        'result.',
        'Allow both detectors to clear of test gas and confirm they return '
        'to normal.',
    ])
    _steps(doc, 'Phase 3 — Reinstatement', [
        'Carry out a visual inspection of the work area to confirm it is '
        'ready to resume normal operation.',
        'Change the operating mode back to Remote/Local and confirm the '
        'system reports no faults or alarms.',
        'Close all doors and remove barricades.',
        'Record any defect found on the project defect register, with the '
        'responsible party and the target re-test date.',
        'Move to the next container.',
    ])
    _p(doc, '', size=4, after=2)
    _note(doc, 'SEQUENCE',
          'Test one detector at a time and record its result before moving '
          'to the next. Applying gas to both at once proves only that '
          'something responded, and makes a single failed detector '
          'impossible to identify.')
    _note(doc, 'ON FAILURE',
          'A detector that does not respond is recorded as failed and raised '
          'on the defect register. Do not adjust, clean or re-range it to '
          'obtain a pass — the record must show what was found.')

    # 7 ─────────────────────────────────────────────────────────────────────
    _h(doc, '7.  ACCEPTANCE CRITERIA')
    _p(doc, 'The test is a PASS for a container only when both of its '
            'detectors meet every criterion below.', size=9, italic=True,
       after=6)
    crit = [
        ('Each gas detector responds to the test gas applied at the sensor',
         'Field observation, recorded'),
        ('The resulting alarm is received with the correct address and '
         'description', 'Panel record'),
        ('Each detector clears and returns to normal once the gas disperses',
         'Field and panel observation'),
        ('No detector required adjustment, cleaning or re-ranging to respond',
         'Recorded; otherwise a defect'),
        ('The system reports no faults or standing alarms after the test',
         'Panel record'),
        ('The container is returned to Remote/Local mode, doors closed and '
         'barricades removed', 'Visual, confirmed'),
    ]
    _table(doc, ['No.', 'Criterion', 'Evidence'], [1.2, 11.0, 4.8],
           [[str(i), c, e] for i, (c, e) in enumerate(crit, 1)], center=(0,))

    # 8 ─────────────────────────────────────────────────────────────────────
    doc.add_page_break()
    _h(doc, '8.  TEST RECORD BY CONTAINER', before=0)
    _p(doc, 'One line per BESS container, by design zone. Detector 1 and '
            'Detector 2 are the two devices of that container, one on each '
            'side.', size=9, italic=True, after=4)

    zones = sorted({c['zone'] for c in containers})
    widths = [1.6, 1.4, 2.2, 3.3, 2.2, 2.2, 4.1]
    labels = ['Block', 'Blk in zone', 'BESS container', 'Serial number',
              'Detector 1', 'Detector 2', 'Remarks']
    for zi, z in enumerate(zones):
        rows = [c for c in containers if c['zone'] == z]
        blocks = sorted({c['plant_block'] for c in rows})
        if zi:
            doc.add_page_break()
        _h(doc, f'ZONE {z} — blocks {blocks[0]}–{blocks[-1]}', size=11,
           before=0 if zi else 10)
        _p(doc, f'{len(blocks)} blocks · {len(rows)} BESS containers · '
                f'{len(rows) * DETECTORS_PER_CONTAINER} gas detectors',
           size=9, after=4)
        t = _head(_grid(doc, widths), labels)
        last = None
        for c in rows:
            cells = t.add_row().cells
            first = c['plant_block'] != last
            _w(cells[0], str(c['plant_block']) if first else '', size=9,
               bold=first, align=WD_ALIGN_PARAGRAPH.CENTER)
            _w(cells[1], str(c['zone_block']) if first else '', size=9,
               align=WD_ALIGN_PARAGRAPH.CENTER)
            _w(cells[2], f'BESS {c["bess"]}', size=9,
               align=WD_ALIGN_PARAGRAPH.CENTER)
            _w(cells[3], c['serial'], size=9, align=WD_ALIGN_PARAGRAPH.CENTER)
            for k in (4, 5):
                _w(cells[k], TICK if ticked else BOX, size=12,
                   align=WD_ALIGN_PARAGRAPH.CENTER, symbol=True)
            _w(cells[6], '', size=9)
            last = c['plant_block']

    # 9 ─────────────────────────────────────────────────────────────────────
    doc.add_page_break()
    _h(doc, '9.  RESULT AND CONFIRMATION', before=0)
    _p(doc, f'Overall result for the {total} detectors recorded above:        '
            f'PASS  {TICK if ticked else BOX}          FAIL  {BOX}',
       bold=True, size=11, after=6)
    _p(doc, 'Detectors failed and defects raised (reference numbers):',
       size=10, after=2)
    t = _grid(doc, [17.0])
    t.rows[0].height = Cm(1.4)
    _w(t.rows[0].cells[0], '', size=9)
    _p(doc, '', size=6, after=4)
    _p(doc, 'The undersigned confirm that the test recorded above was carried '
            'out on site in accordance with the method in Sections 5 to 7, '
            'and that the results are as stated.', size=9, after=6)
    t = _head(_grid(doc, [4.6, 4.4, 4.2, 3.8]),
              ['Role', 'Name', 'Signature', 'Date'])
    for role in ('Carried out by (Technician)',
                 'Carried out by (Technician)',
                 'Test Supervisor',
                 'Witnessed by (Site Supervisor)',
                 'Reviewed by (O&M Engineer)',
                 'Customer representative'):
        row = t.add_row()
        _w(row.cells[0], role, size=9)
        for c in range(1, 4):
            _w(row.cells[c], '')
        row.height = Cm(1.0)
    return doc


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--db', default=os.path.join('dist', 'pv_bess_tracker.db'))
    ap.add_argument('--out', default='.')
    ap.add_argument('--project', type=int, default=1)
    ap.add_argument('--blank', action='store_true')
    ap.add_argument('--date', default='')
    a = ap.parse_args()

    db = os.path.abspath(a.db)
    if not os.path.exists(db):
        raise SystemExit(f'database not found: {db}')
    containers = load_containers(db, a.project)
    print(f'{len(containers)} BESS containers, '
          f'{len({c["plant_block"] for c in containers})} blocks, '
          f'{len({c["zone"] for c in containers})} zones, '
          f'{len(containers) * DETECTORS_PER_CONTAINER} gas detectors')
    os.makedirs(a.out, exist_ok=True)
    date = a.date or datetime.date.today().strftime('%d %B %Y')
    path = os.path.join(
        a.out,
        'FSS_Gas_detector_bump_test_record'
        + ('_blank' if a.blank else '') + '.docx')
    build(containers, not a.blank, date).save(path)
    print(f'  wrote {path}  ({os.path.getsize(path) / 1024:.0f} KB)')


if __name__ == '__main__':
    sys.exit(main())
