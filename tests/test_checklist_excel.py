"""The customer's checklist workbook, read and written back.

The workbook must come back as the customer's own file: the Excel 365 cell
checkboxes (featurePropertyBag), the logo, the merges, the comment and the
printer settings all survive, because everything except the sheet XML is
copied byte for byte. Checked here against the real PCS and BESS checklists;
the test skips where they are not present.
"""
import os
import zipfile

import _harness as H            # must be first
import openpyxl

import services.checklist_excel as cx

PM_DIR = os.environ.get('BESS_PM_DIR',
                        r'C:\Users\user1\Desktop\ACWA BESS Tashkent\PM')
PCS = os.path.join(PM_DIR, '01) PCS Checklist.xlsx')
BESS = os.path.join(PM_DIR, '02) BESS Checklist.xlsx')
if not (os.path.isfile(PCS) and os.path.isfile(BESS)):
    H.skip('the PM checklist workbooks are not on this computer')

# ── reading ──────────────────────────────────────────────────────────────
pcs = cx.parse_template(PCS)
bess = cx.parse_template(BESS)
H.check(len(pcs['items']) == 12 and pcs['progress_row'] == 20,
        'the PCS checklist has {} items, Progress on row {}'.format(
            len(pcs['items']), pcs['progress_row']))
H.check(len(bess['items']) == 92 and bess['progress_row'] == 100,
        'the BESS checklist has {} items, Progress on row {}'.format(
            len(bess['items']), bess['progress_row']))
first = pcs['items'][0]
H.check(first['excel_row'] == 8 and first['no'] == '1'
        and 'flammable' in first['text'],
        'the first item is row 8, No 1: {}…'.format(first['text'][:40]))
H.check(all(i['equipment'] for i in bess['items']),
        'every BESS item carries its equipment group')
merged_group = [i for i in bess['items'] if i['excel_row'] in (9, 10, 11)]
H.check(all(i['activity'] == bess['items'][0]['activity'] for i in merged_group),
        'a group label merged over several rows is read for each of them: {}'
        .format(merged_group[0]['activity'][:40]))

# ── writing ──────────────────────────────────────────────────────────────
out = os.path.join(H.WORK, 'PCS filled.xlsx')
rows = {i['excel_row']: i for i in pcs['items']}
results = {r: {'result': cx.OK, 'comment': ''} for r in rows}
results[9] = {'result': cx.NOK, 'comment': 'PCS 2: fan bearing noisy — replaced'}
results[10] = {'result': cx.NA, 'comment': ''}
results[11] = {'result': cx.EXCLUDED, 'comment': 'not in scope this campaign'}
cx.write_filled(PCS, out, {
    'plant': 'ACWA Riverside BESS', 'date': '20.09.2026',
    'location': 'Block 33 · LC1', 'serial': 'A2561724523',
    'name': 'Field team',
}, results, added=[{
    'after_row': 19, 'no': '12a', 'equipment': 'PCS',
    'text': 'Check the anti-condensation heater (added for this campaign)',
    'result': cx.OK, 'comment': 'PCS 1-4: heaters warm',
}])

wb = openpyxl.load_workbook(out)
ws = wb.active
H.check(ws['C4'].value == 'ACWA Riverside BESS' and ws['E5'].value == 'A2561724523'
        and ws['E4'].value == '20.09.2026',
        'the header carries plant, date and serial')
H.check(ws['E8'].value is True, 'OK ticks the box')
H.check(ws['E9'].value is False and 'fan bearing' in (ws['G9'].value or ''),
        'NOK unticks it and writes the comment')
H.check(ws['E10'].value in (None, ''), 'N/A leaves the box empty')
H.check(ws['E11'].value in (None, '') and 'not in scope' in (ws['G11'].value or ''),
        'an excluded item keeps its row, empty, with the note')
H.check(ws['A20'].value == '12a' and 'heater' in (ws['D20'].value or '')
        and ws['E20'].value is True,
        'the added item is row 20, at the end of its group')
H.check((ws['B21'].value or '').lower() == 'progress'
        and 'E8:E20' in (ws['E21'].value or ''),
        'Progress moved down a row and counts the new one: {}'.format(ws['E21'].value))
H.check(ws['F20'].value is not None, 'the new row keeps the "Verified / Done" formula')
wb.close()

# ── what openpyxl would have destroyed ───────────────────────────────────
src_parts = set(zipfile.ZipFile(PCS).namelist())
out_zip = zipfile.ZipFile(out)
out_parts = set(out_zip.namelist())
lost = {p for p in src_parts - out_parts if 'calcChain' not in p}
H.check(not lost, 'every part of the customer\'s file is still there ({} parts)'
        .format(len(out_parts)))
H.check(any('featurePropertyBag' in p for p in out_parts),
        'the cell checkboxes survive')
H.check('xl/calcChain.xml' not in out_parts,
        'the stale formula chain is dropped after the row insert')
H.check('fullCalcOnLoad="1"' in out_zip.read('xl/workbook.xml').decode('utf-8'),
        'and Excel recalculates the sheet when it opens it')
for part in src_parts & out_parts:
    # the VML holds the comment's anchor row, which the insert moves
    if part.startswith('xl/media/') or part.endswith('.xml') and 'drawings' in part:
        H.check(zipfile.ZipFile(PCS).read(part) == out_zip.read(part),
                'the logo / drawing part {} is copied byte for byte'.format(
                    os.path.basename(part)))
H.check('ref="E21"' in out_zip.read('xl/comments1.xml').decode('utf-8'),
        'the comment on the Progress row moved down with it')

# ── the big one, with no added rows ──────────────────────────────────────
out2 = os.path.join(H.WORK, 'BESS filled.xlsx')
res2 = {i['excel_row']: {'result': cx.OK, 'comment': ''} for i in bess['items']}
res2[99] = {'result': cx.NOK, 'comment': 'BESS 3: door seal torn'}
cx.write_filled(BESS, out2, {'plant': 'ACWA Riverside BESS', 'date': '20.09.2026'}, res2)
wb2 = openpyxl.load_workbook(out2)
ws2 = wb2.active
H.check(ws2['E99'].value is False and ws2['E98'].value is True
        and (ws2['B100'].value or '').lower() == 'progress',
        'the 92-item checklist is written without moving anything')
out2_parts = set(zipfile.ZipFile(out2).namelist())
H.check('xl/calcChain.xml' in out2_parts,
        'without a row insert the formula chain is left alone')
H.check(any('printerSettings' in p for p in out2_parts)
        and any('printerSettings' in p for p in zipfile.ZipFile(BESS).namelist()),
        'the printer settings openpyxl would have dropped are still there')
wb2.close()

H.finish()
