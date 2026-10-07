"""Build the FSS solenoid valve functional test record as a Word document.

The method half is taken from Method_Statement_FSS_Test_Compartment.docx,
keeping only what describes how the test is performed — the cause-and-effect
logic, the equipment, the pre-test conditions, the five phases and the
acceptance criteria. The HSE half of that document (risk assessment, PPE,
permits, notifications, emergency response) is deliberately not reproduced:
it lives in the Method Statement, which stays the controlling document.

Section 4, test tools and instruments, is left blank on purpose — the site
fills in what was actually used.

The record half is the site's own checklist: every BESS container in the
asset register, by design zone, pre-ticked because the test is done.

    python tools/make_fss_solenoid_test_record.py [--out DIR] [--blank]
    [--valves-per-container N]
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
SOURCE = 'Method_Statement_FSS_Test_Compartment.docx'


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


def _head(t, labels, widths):
    for i, h in enumerate(labels):
        _w(t.rows[0].cells[i], h, bold=True, align=WD_ALIGN_PARAGRAPH.CENTER)
        _bg(t.rows[0].cells[i], HDR_BG)
    _repeat(t.rows[0])
    return t


def _table(doc, labels, widths, rows, center=()):
    t = _head(_grid(doc, widths), labels, widths)
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
def build(containers, ticked, test_date, vpc):
    doc = _doc()
    n = len(containers)
    total = n * vpc

    _p(doc, 'FIRE SUPPRESSION SYSTEM', bold=True, size=11, after=0)
    _p(doc, 'SOLENOID VALVE FUNCTIONAL TEST — RECORD', bold=True, size=15,
       after=2)
    _p(doc, f'{SITE} — {LOCATION}', size=10, after=2)
    _p(doc, 'Detection, FACP logic and solenoid valve actuation — dry test, '
            'no water discharge.', size=9.5, after=2)
    _p(doc, f'Scope: {n} BESS containers across 9 design zones'
            + (f', {vpc} solenoid valves each ({total} valves).'
               if vpc > 1 else f' ({total} solenoid valves).'), size=9.5,
       after=8)

    t = _grid(doc, [4.2, 4.3, 4.2, 4.3])
    fields = [('Test date', test_date), ('Document ref.', ''),
              ('Carried out by', ''), ('Witnessed by', ''),
              ('Method Statement ref.', SOURCE), ('Revision', '')]
    for i in range(0, len(fields), 2):
        row = t.rows[0] if i == 0 else t.add_row()
        for j, (k, v) in enumerate(fields[i:i + 2]):
            _w(row.cells[j * 2], k, bold=True)
            _w(row.cells[j * 2 + 1], v)
    for row in t.rows:
        _bg(row.cells[0], SUB_BG); _bg(row.cells[2], SUB_BG)

    _p(doc, '', size=6, after=2)
    _p(doc, f'{TICK} = pass   ·   {BOX} = fail, state the finding and the '
            f'action in Remarks', size=9, after=2)

    # 1 ─────────────────────────────────────────────────────────────────────
    _h(doc, '1.  PURPOSE AND SCOPE')
    _p(doc, 'To prove that the fire alarm control panel (FACP) receives and '
            'correctly processes the signals from the smoke and heat '
            'detectors, that the programmed voting logic operates only when '
            'the full detection group is in alarm, and that the solenoid '
            'valve for that container actuates and then closes on '
            'reset.', size=10, after=4)
    _p(doc, 'The test is carried out DRY: the fire water pipework is drained '
            'and no water is discharged at any point. Wet tests, flow tests, '
            'pump tests, changes to the FACP program, device addressing or '
            'field wiring are outside this scope.', size=10, after=4)
    _p(doc, f'This record reproduces the test method from {SOURCE}. The HSE '
            f'content of that document — risk assessment, PPE, permits, '
            f'notifications, impairment control and emergency response — is '
            f'not repeated here; the Method Statement remains the '
            f'controlling document and applies in full.', size=9,
       italic=True, after=4)

    # 2 ─────────────────────────────────────────────────────────────────────
    _h(doc, '2.  SYSTEM DESCRIPTION AND CAUSE-AND-EFFECT LOGIC')
    _p(doc, 'Each BESS container is protected by smoke and heat detection '
            'reporting to the FACP, and by a water-based suppression system '
            'whose release is controlled by a single DC24V solenoid valve. '
            'The valve is driven from an FACP output relay through an '
            'addressable input/output module.', size=10, after=4)
    _p(doc, 'When two smoke detectors AND two heat detectors in the same '
            'container are in alarm simultaneously, the FACP energises the '
            'output relay, which supplies DC24V to that container\'s solenoid '
            'valve. No earlier activation may operate the relay — the full '
            'group of four is what releases the output.', size=10, after=6)
    _table(doc, ['Protected unit', 'Detection group in alarm',
                 'FACP output relay', 'Actuated device'],
           [4.0, 5.4, 3.6, 4.0],
           [['BESS container', '2 × smoke + 2 × heat', 'KM (per container)',
             'Solenoid valve (1 per container)']],
           center=(2,))
    _p(doc, 'The Method Statement is written for a four-compartment '
            'arrangement with relays KM1–KM4 and four valves. The '
            'installation recorded here has one solenoid valve per BESS '
            'container, driven by the four detectors of that container; the '
            'test sequence is otherwise identical.', size=9, italic=True,
       after=4)

    # 3 ─────────────────────────────────────────────────────────────────────
    _h(doc, '3.  EQUIPMENT UNDER TEST')
    _table(doc, ['No.', 'Name', 'Model', 'Qty', 'Reference'],
           [1.2, 5.0, 5.2, 1.6, 4.0],
           [['1', 'Input / output module', 'JKM-FCM-1C', '1 pc',
             'Loop address per schedule'],
            ['2', 'Solenoid valve', 'USLP1DH13E3F50M (Sanlixin)', '1 pc',
             'DC24V, DN40, water/air, 0.5–16 kgf/cm²'],
            ['3', 'Smoke detectors', 'Per approved device schedule', '2 pcs',
             'Addresses per schedule'],
            ['4', 'Heat detectors', 'Per approved device schedule', '2 pcs',
             'Addresses per schedule'],
            ['5', 'Fire alarm control panel', 'Per approved submittal',
             '1 set', 'Programmed cause-and-effect']],
           center=(0, 3))
    _p(doc, 'Quantities are per BESS container.', size=9, italic=True,
       after=2)
    _p(doc, 'The solenoid valve coil is the point at which magnetic pull is '
            'checked with the screwdriver blade.', size=9, italic=True,
       after=4)

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
    _p(doc, 'All five conditions confirmed before the first detector is '
            'activated.', size=9, italic=True, after=6)
    pre = [
        'The FACP has been loaded with the corresponding program, and the '
        'cause-and-effect logic matches the approved matrix.',
        'The I/O modules and solenoid valves are fully wired to the FACP.',
        'All devices have been addressed in accordance with the program.',
        'Site wiring is complete and the power supply is switched on, with '
        'the FACP showing no faults.',
        'The fire water pipework has been drained — the container must NOT '
        'be tested with water in the pipe.',
    ]
    t = _head(_grid(doc, [1.2, 13.4, 2.4]),
              ['No.', 'Pre-test condition', 'Confirmed'], None)
    for i, txt in enumerate(pre, 1):
        c = t.add_row().cells
        _w(c[0], str(i), size=9, align=WD_ALIGN_PARAGRAPH.CENTER)
        _w(c[1], txt, size=9)
        _w(c[2], TICK if ticked else BOX, size=12,
           align=WD_ALIGN_PARAGRAPH.CENTER, symbol=True)
    _p(doc, '', size=4, after=2)
    _note(doc, 'MANDATORY',
          'Condition 5 is absolute. A solenoid valve that opens with water in '
          'the line will discharge into the container.')

    # 6 ─────────────────────────────────────────────────────────────────────
    _h(doc, '6.  TEST METHOD')
    _p(doc, 'Five phases. Three positions are held throughout: the FACP, the '
            'detector being activated, and the solenoid valve. Observations '
            'are recorded live, not reconstructed afterwards.', size=10,
       after=6)

    phases = [
        ('Phase 1 — Preparation and set-up', [
            'Confirm every pre-test condition in Section 5 has been met, '
            'including the drained pipework.',
            'Take up positions: FACP Operator at the panel; Field Technician '
            '— Detection at the container; Field Technician — Valve at the '
            'solenoid valve.',
            'The FACP Operator records the panel starting state — no faults, '
            'no disablements, no active alarms.',
            'Confirm the identity and address of each of the four detectors '
            'to be activated, and the order of activation.']),
        ('Phase 2 — Detector activation and FACP indication', [
            'Activate the first detector of the group with the test gun. '
            'Apply the agent per the detector manufacturer\'s instructions; '
            'do not over-apply.',
            'Confirm the detector local alarm LED illuminates, and that the '
            'alarm is received at the FACP with the correct address and '
            'description. Record the time.',
            'Observe and record the JKM-FCM-1C module indicator lights, '
            'confirming they operate as expected for the alarm condition.',
            'Confirm the output relay has NOT operated and the solenoid '
            'valve has NOT actuated. The technician at the valve confirms no '
            'valve activity.',
            'Repeat for the second and third detectors in sequence, '
            'recording each result and confirming after each that the output '
            'has not operated.']),
        ('Phase 3 — Solenoid valve actuation', [
            'The technician at the valve confirms they are in position, '
            'watching and listening. All other personnel remain clear of the '
            'valve and the pipework.',
            'Activate the fourth and final detector of the group.',
            'The FACP Operator confirms the fourth alarm is received, the '
            'group condition is satisfied and the output relay operates. '
            'Record the time and the panel indication.',
            'At the valve, confirm operation by an audible click as the '
            'solenoid pulls in, and by the magnetic pull felt when the '
            'screwdriver blade is brought to the coil. Both are attempted '
            'and recorded.',
            'Where the design permits and the Authorised Electrical Person '
            'is present, the DC24V at the coil terminals may additionally be '
            'verified with a multimeter.',
            'Record the result. Photograph the panel indication and the '
            'valve position.']),
        ('Phase 4 — Reset and return to normal', [
            'Reset the fire alarm control panel.',
            'Confirm all modules cease operation and the output relay drops '
            'out.',
            'At the valve, confirm it closes — no magnetic pull at the coil, '
            'and where verified electrically, loss of the DC24V supply.',
            'Allow the detectors to clear of test agent. Where a detector '
            'does not reset, ventilate and allow more time; do not disable '
            'the device to force a reset.',
            'Confirm the FACP has returned to the normal quiescent state '
            'with no standing alarms, faults or disablements. Record the '
            'time.']),
        ('Phase 5 — Reinstatement', [
            'Remove all test equipment and carry out a tool count.',
            'Restore any device, circuit or zone disabled for the test, and '
            'confirm at the panel that nothing remains disabled.',
            'Restore the fire water pipework per the commissioning sequence.',
            'Complete and sign this record.',
            'Raise any defect found on the project defect register, with the '
            'responsible party and the target re-test date.']),
    ]
    for title, steps in phases:
        _p(doc, title, bold=True, size=10, after=2, before=8)
        for i, s in enumerate(steps, 1):
            p = doc.add_paragraph()
            p.paragraph_format.left_indent = Cm(0.8)
            p.paragraph_format.space_after = Pt(2)
            r = p.add_run(f'{i}.  {s}')
            r.font.size = Pt(9.5)
            r.font.color.rgb = BLACK
            r.font.name = BODY_FONT

    _p(doc, '', size=4, after=2)
    _note(doc, 'SEQUENCE',
          'Trigger the detectors one at a time and confirm each result '
          'before moving to the next. Activating two at once destroys the '
          'evidence of the voting logic and makes a wrong result impossible '
          'to diagnose.')
    _note(doc, 'RESET',
          'If any module remains operated or the valve remains energised '
          'after reset, stop, treat it as a defect and do not proceed until '
          'it is rectified. A valve that fails to de-energise is a live '
          'discharge risk as soon as the water supply is restored.')

    # 7 ─────────────────────────────────────────────────────────────────────
    _h(doc, '7.  ACCEPTANCE CRITERIA')
    _p(doc, 'The test is a PASS only when every criterion is met and '
            'recorded. Any single failure fails the test for that '
            'container.', size=9, italic=True, after=6)
    crit = [
        ('Each of the four detectors, when activated, raises an alarm at the '
         'FACP with the correct address and description', 'Panel record'),
        ('The local alarm LED illuminates on each activated detector',
         'Visual, recorded'),
        ('The module indicator lights operate correctly at each stage',
         'Visual, recorded'),
        ('The output relay does NOT operate after the first, second or third '
         'activation', 'Panel and valve observation'),
        ('The output relay operates after the fourth activation completes '
         'the 2-smoke + 2-heat group', 'Panel record'),
        ('The solenoid valve actuates, confirmed by audible click and/or '
         'magnetic pull at the coil', 'Field observation'),
        ('No water is discharged at any point during the test',
         'Visual, pipework confirmed drained'),
        ('On reset, all modules cease operation and the solenoid valve '
         'closes', 'Panel and field observation'),
        ('The FACP returns to the normal state with no standing alarms, '
         'faults or disablements', 'Panel record'),
    ]
    _table(doc, ['No.', 'Criterion', 'Evidence'], [1.2, 11.0, 4.8],
           [[str(i), c, e] for i, (c, e) in enumerate(crit, 1)], center=(0,))

    # 8 ─────────────────────────────────────────────────────────────────────
    doc.add_page_break()
    _h(doc, '8.  TEST RECORD BY CONTAINER', before=0)
    _p(doc, 'One line per BESS container, by design zone. The three checks '
            'are acceptance criteria 5, 6 and 8: the output relay operated '
            'only on the fourth activation, the valve actuated, and the '
            'valve closed on reset.', size=9, italic=True, after=4)

    zones = sorted({c['zone'] for c in containers})
    widths = [1.6, 1.4, 2.2, 3.2, 2.3, 2.1, 2.3, 1.9]
    labels = ['Block', 'Blk in zone', 'BESS container', 'Serial number',
              'Relay on 4th', 'Valve actuated', 'Closed on reset', 'Remarks']
    for zi, z in enumerate(zones):
        rows = [c for c in containers if c['zone'] == z]
        blocks = sorted({c['plant_block'] for c in rows})
        if zi:
            doc.add_page_break()
        _h(doc, f'ZONE {z} — blocks {blocks[0]}–{blocks[-1]}', size=11,
           before=0 if zi else 10)
        _p(doc, f'{len(blocks)} blocks · {len(rows)} BESS containers · '
                f'{len(rows) * vpc} solenoid valves', size=9, after=4)
        t = _head(_grid(doc, widths), labels, widths)
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
            for k in (4, 5, 6):
                _w(cells[k], TICK if ticked else BOX, size=12,
                   align=WD_ALIGN_PARAGRAPH.CENTER, symbol=True)
            _w(cells[7], '', size=9)
            last = c['plant_block']

    # 9 ─────────────────────────────────────────────────────────────────────
    doc.add_page_break()
    _h(doc, '9.  RESULT AND CONFIRMATION', before=0)
    _p(doc, f'Overall result for the {n} containers recorded above:        '
            f'PASS  {TICK if ticked else BOX}          FAIL  {BOX}',
       bold=True, size=11, after=6)
    _p(doc, 'Defects raised (reference numbers):', size=10, after=2)
    t = _grid(doc, [17.0])
    t.rows[0].height = Cm(1.4)
    _w(t.rows[0].cells[0], '', size=9)
    _p(doc, '', size=6, after=4)
    _p(doc, 'The undersigned confirm that the test recorded above was carried '
            'out on site in accordance with the method in Sections 5 to 7, '
            'and that the results are as stated.', size=9, after=6)
    t = _head(_grid(doc, [4.6, 4.4, 4.2, 3.8]),
              ['Role', 'Name', 'Signature', 'Date'], None)
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
    ap.add_argument('--valves-per-container', type=int, default=1)
    ap.add_argument('--date', default='')
    a = ap.parse_args()

    db = os.path.abspath(a.db)
    if not os.path.exists(db):
        raise SystemExit(f'database not found: {db}')
    containers = load_containers(db, a.project)
    print(f'{len(containers)} BESS containers, '
          f'{len({c["plant_block"] for c in containers})} blocks, '
          f'{len({c["zone"] for c in containers})} zones, '
          f'{a.valves_per_container} valve(s) per container')
    os.makedirs(a.out, exist_ok=True)
    date = a.date or datetime.date.today().strftime('%d %B %Y')
    path = os.path.join(
        a.out,
        'FSS_Solenoid_valve_functional_test_record'
        + ('_blank' if a.blank else '') + '.docx')
    build(containers, not a.blank, date, a.valves_per_container).save(path)
    print(f'  wrote {path}  ({os.path.getsize(path) / 1024:.0f} KB)')


if __name__ == '__main__':
    sys.exit(main())
