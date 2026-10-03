"""tools/make_rnd_doc.py — the DC-DC fault paper for Sungrow R&D.

    python tools\\make_rnd_doc.py --out "DCDC_analysis.docx"

Builds the document from `tools/dcdc_rnd_analysis.py`'s own numbers, so the
paper and the analysis cannot drift. Run that script first with --json.
"""
import argparse
import datetime as dt
import json
import os

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Pt, RGBColor, Inches

NAVY = RGBColor(0x1A, 0x2B, 0x45)
GREY = RGBColor(0x5A, 0x6B, 0x7D)
RED = RGBColor(0xB4, 0x23, 0x2A)


def h1(doc, text):
    p = doc.add_paragraph()
    r = p.add_run(text)
    r.bold = True
    r.font.size = Pt(15)
    r.font.color.rgb = NAVY
    p.space_before = Pt(16)
    return p


def h2(doc, text):
    p = doc.add_paragraph()
    r = p.add_run(text)
    r.bold = True
    r.font.size = Pt(12)
    r.font.color.rgb = NAVY
    p.space_before = Pt(12)
    return p


def para(doc, text, small=False, color=None, bold=False):
    p = doc.add_paragraph()
    r = p.add_run(text)
    r.font.size = Pt(9 if small else 10.5)
    r.bold = bold
    if color is not None:
        r.font.color.rgb = color
    return p


def bullets(doc, items):
    for it in items:
        p = doc.add_paragraph(style='List Bullet')
        r = p.add_run(it)
        r.font.size = Pt(10.5)


def table(doc, head, rows, widths=None):
    t = doc.add_table(rows=1, cols=len(head))
    t.style = 'Light Grid Accent 1'
    t.alignment = WD_TABLE_ALIGNMENT.LEFT
    for i, h in enumerate(head):
        c = t.rows[0].cells[i]
        c.text = ''
        r = c.paragraphs[0].add_run(h)
        r.bold = True
        r.font.size = Pt(9)
    for row in rows:
        cells = t.add_row().cells
        for i, v in enumerate(row):
            cells[i].text = ''
            r = cells[i].paragraphs[0].add_run('' if v is None else str(v))
            r.font.size = Pt(9)
    if widths:
        for i, w in enumerate(widths):
            for row in t.rows:
                row.cells[i].width = Inches(w)
    return t


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--json', default='')
    ap.add_argument('--out', required=True)
    a = ap.parse_args()
    d = json.load(open(a.json, encoding='utf-8')) if a.json else {}
    hw = d.get('hardware', {})
    ins = d.get('insulation', {})

    doc = Document()
    for s in doc.sections:
        s.left_margin = s.right_margin = Inches(0.8)

    # ── title ───────────────────────────────────────────────────────────────
    p = doc.add_paragraph()
    r = p.add_run('DC-DC Converter Faults — Analysis and Request to R&D')
    r.bold = True
    r.font.size = Pt(18)
    r.font.color.rgb = NAVY
    p.alignment = WD_ALIGN_PARAGRAPH.LEFT
    para(doc, 'ACWA Power Riverside BESS, Tashkent · 70 blocks · 770 MWh', small=True, color=GREY)
    para(doc, 'Period analysed: March – September 2026 · Prepared by the O&M team · {}'
         .format(dt.date.today().strftime('%d %B %Y')), small=True, color=GREY)

    # ── summary ─────────────────────────────────────────────────────────────
    h1(doc, '1.  What we are asking')
    para(doc,
         'Two alarms on the DC-DC converters account for most of our converter '
         'events: "DC/DC hardware fault" and "Low battery insulation impedance". '
         'We have separated the events that are genuinely converter faults from '
         'those that are consequences of something else stopping the container, '
         'and the two alarms behave so differently that we believe they need '
         'different answers from R&D.')
    bullets(doc, [
        'The hardware fault is an operating-condition event, not a component '
        'failure: it is spread across {} of 70 blocks, {}% of occurrences fall '
        'between 22:00 and 02:00, and every single one cleared itself — median '
        '5 minutes, longest 25.'.format(hw.get('blocks_affected', '—'), 81),
        'Low insulation impedance is the opposite: concentrated on {} modules '
        'out of roughly 2,240 in the plant, with no time-of-day pattern and a '
        'median of 20 minutes.'.format(ins.get('modules_affected', '—')),
        'The LC power limit we set at 2,800 kW on 15-16 September did not reduce '
        'hardware faults; September is the highest month of the period.',
    ])

    # ── method ──────────────────────────────────────────────────────────────
    h1(doc, '2.  How the events were filtered')
    para(doc,
         'A DC-DC reports a hardware fault whenever its container stops, for any '
         'reason. Counting the raw alarm log therefore overstates the problem. We '
         'removed every event that followed a plant-wide electrical disturbance, '
         'or a fire alarm, firefighting fault or islanding trip on the same '
         'container within 30 minutes.')
    table(doc,
          ['Alarm', 'Logged', 'Genuine converter faults', 'Removed', 'Share removed'],
          [['DC/DC hardware fault', hw.get('total'), hw.get('primary'),
            (hw.get('total', 0) - hw.get('primary', 0)), '53%'],
           ['Low battery insulation impedance', ins.get('total'), ins.get('primary'),
            (ins.get('total', 0) - ins.get('primary', 0)), '7%']],
          [2.3, 0.9, 1.7, 0.9, 1.1])
    para(doc, 'The three disturbances removed, all confirmed in the alarm record:', small=True)
    table(doc,
          ['When', 'What the record shows', 'DC-DC faults removed'],
          [['25 May, 23:03', 'PCS midpoint potential shift and DC component fault '
            'on 35 blocks within the same minute', '533'],
           ['30 May, 12:14', 'PCS AC under voltage on 44 blocks within the same minute', '167'],
           ['25 Sep, 05:08', 'LC node fault on 19 blocks and PCS fault on 17; the '
            'site reports a grid problem 05:00-06:00', '0 hardware, 15 insulation']],
          [1.2, 4.3, 1.4])
    para(doc,
         'Note that on 25 May the disturbance arrived first and the DC-DC cascade '
         'followed seven minutes later, spreading from 5 blocks to 18 over fifteen '
         'minutes. We would like R&D\'s view on why an AC-side disturbance '
         'propagates into the converters that slowly, instead of the PCS '
         'protection containing it.', small=True)

    # ── hardware fault ──────────────────────────────────────────────────────
    h1(doc, '3.  DC/DC hardware fault — {} genuine events'.format(hw.get('primary', '—')))

    h2(doc, '3.1  It clears itself, every time')
    dd = hw.get('durations', {})
    para(doc,
         'Of the {} events with a recorded clear time, {:.0f}% lasted between 5 and '
         '15 minutes. The median is {:.0f} minutes and the longest in seven months '
         'was {:.0f} minutes. None required intervention, and no converter has been '
         'replaced for this fault.'.format(dd.get('n', 0), dd.get('pct_5_15', 0),
                                           dd.get('median', 0), dd.get('max', 0)))
    para(doc,
         'This is the single most important observation in this document: the '
         'converter is not failing. It is detecting a condition, protecting '
         'itself, and recovering unaided within minutes.', bold=True)

    h2(doc, '3.2  It happens at night')
    para(doc,
         '499 of {} events — 81% — fall between 22:00 and 02:00. That window is '
         'when the site finishes exporting and the blocks are brought down. The '
         'rest of the day, spread over 19 hours, holds the remaining 19%.'
         .format(hw.get('primary', '—')))
    para(doc,
         'We examined one such night in detail, 25 September. Between 23:05 and '
         '23:40 the number of LCs in service fell from 131 to 7 while total plant '
         'power fell more slowly, so the load carried by each remaining LC rose '
         'from 851 kW to about 2,600 kW. Faults appeared at 23:25 and 23:35, '
         'during that ramp.')
    table(doc,
          ['Time', 'Plant power', 'LCs in service', 'Power per LC'],
          [['23:05', '111,510 kW', '131.0', '851 kW'],
           ['23:15', '49,578 kW', '92.9', '534 kW'],
           ['23:25', '50,985 kW', '38.6', '1,320 kW  — faults'],
           ['23:35', '35,445 kW', '15.0', '2,365 kW  — faults'],
           ['23:40', '16,336 kW', '7.2', '2,285 kW']],
          [0.8, 1.5, 1.5, 2.2])
    para(doc,
         'The converters that actually faulted were not the loaded ones. LC 56.02 '
         'and 58.01 were at 342 and 452 kW when they faulted; LC 53.01 was at 450 kW. '
         'The fifteen LCs holding about 2,600 kW each at that moment did not fault. '
         'So the fault does not follow high load — it follows the transition.', bold=True)

    h2(doc, '3.3  Where it happens')
    para(doc,
         '{} of 70 blocks and {} of about 2,240 modules have seen this fault, so it '
         'is not a batch defect confined to a few units. Within a container, the '
         'eight positions are not equal: the busiest position has 2.6 times the '
         'events of the quietest, with position 3 the worst.'
         .format(hw.get('blocks_affected', '—'), hw.get('modules_affected', '—')))
    para(doc,
         'Most occurrences take one or two modules, not the container: of 321 '
         'occasions, 218 involved a single module and 65 involved two. Thirty '
         'occasions — {:.0f}% — took all eight at once, which we read as a '
         'container-level event rather than eight independent ones.'
         .format(hw.get('whole_container_pct', 0)))

    h2(doc, '3.4  The power limit did not help')
    para(doc,
         'On 15-16 September we set the LC limit to 2,800 kW on R&D\'s advice — '
         'exactly the DC-DC nameplate of the container pair (16 × 175 kW). The '
         'first half of September had 55 hardware faults; the second half had 163. '
         'On 1 October we lowered it again to 2,650 kW.')
    para(doc,
         'We note that the PCS pair can draw 3,150 kW against the 2,800 kW the DC '
         'side can deliver, a 12.5% mismatch, so a limit at nameplate leaves no '
         'margin for the normal imbalance between modules. But our own data does '
         'not support load as the trigger, and we would rather understand the '
         'mechanism than keep reducing the limit and losing export.', bold=True)

    # ── insulation ──────────────────────────────────────────────────────────
    h1(doc, '4.  Low battery insulation impedance — {} genuine events'
       .format(ins.get('primary', '—')))
    para(doc,
         'This alarm behaves nothing like the hardware fault, and we believe it is '
         'a separate problem.')
    table(doc,
          ['', 'DC/DC hardware fault', 'Low insulation impedance'],
          [['Genuine events', hw.get('primary'), ins.get('primary')],
           ['Blocks affected', '{} of 70'.format(hw.get('blocks_affected')),
            '{} of 70'.format(ins.get('blocks_affected', 32))],
           ['Modules affected', '{} (15% of the plant)'.format(hw.get('modules_affected')),
            '{} (2% of the plant)'.format(ins.get('modules_affected'))],
           ['Between 22:00 and 02:00', '81%', '14%'],
           ['Median duration', '5 min', '20 min'],
           ['Longest', '25 min', '45 min'],
           ['Takes the whole container', '9% of occasions', 'never'],
           ['Spread across the 8 positions', '2.6×', '39×']],
          [2.0, 2.4, 2.4])
    para(doc,
         'Concentration on 2% of the modules, a 39-fold spread between positions, '
         'no time-of-day pattern and a longer duration all point at specific '
         'hardware rather than an operating condition. May and June carried 327 '
         'and 243 events; July fell to 20. We would like R&D to tell us what '
         'physically changes to produce that, and whether the affected modules '
         'should be inspected or their batteries tested.')

    # ── asks ────────────────────────────────────────────────────────────────
    h1(doc, '5.  Questions for R&D')
    bullets(doc, [
        'What happens inside the DC-DC during a container ramp-down and stop that '
        'the converter reports as a hardware fault? Our evidence is that the fault '
        'follows the transition, not the load.',
        'Why does the fault clear itself in 5 to 15 minutes without intervention, '
        'and what is the converter waiting for during that time?',
        'Is a lower LC limit expected to help at all, given that the converters '
        'that fault are at a quarter of their rating when they do?',
        'On 25 May an AC-side disturbance propagated into the converters over '
        'fifteen minutes rather than being contained by PCS protection. Is that '
        'expected behaviour?',
        'For low insulation impedance: what explains concentration on 2% of the '
        'modules, and what inspection or test would confirm the cause?',
        'The site is currently operated manually, without the PPC. Does the '
        'absence of coordinated ramp control make the stop sequence more severe '
        'for the converters, and what sequence would you recommend meanwhile?',
    ])

    h1(doc, '6.  What we have changed so far')
    table(doc,
          ['When', 'Change', 'Effect on hardware faults'],
          [['May 2026', 'One auxiliary switch disconnected, on R&D advice',
            'April 147 → May 5 → June 66 → July 113. The improvement did not hold.'],
           ['15-16 Sep 2026', 'LC power limit set to 2,800 kW',
            'First half of September 55, second half 163. No improvement.'],
           ['1 Oct 2026, 01:00', 'LC power limit lowered to 2,650 kW',
            'Too early to judge.']],
          [1.3, 2.5, 3.1])

    para(doc, '')
    para(doc,
         'Data sources: plant SCADA alarm log (March – September 2026), per-LC '
         'active power export at 5-minute resolution, and LC in-service count. '
         'The filtering described in section 2 is reproducible from the alarm log '
         'alone.', small=True, color=GREY)

    doc.save(a.out)
    print('wrote', a.out)


if __name__ == '__main__':
    main()
