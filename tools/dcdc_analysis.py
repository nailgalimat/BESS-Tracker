"""tools/dcdc_analysis.py — DC-DC hardware fault and low insulation, in depth.

    python tools\\dcdc_analysis.py                 the live database
    python tools\\dcdc_analysis.py --db other.db   another one

Answers, per fault type: which blocks and which of the eight DC-DC positions
carry it, whether a fault takes a whole container or a module or two, what hour
of the day it happens, and how the months line up against the three changes
made to the plant.

Read-only. It never writes to the database.
"""
import argparse
import os
import sqlite3
import sys
from collections import Counter, defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

FAULTS = [
    ('HARDWARE FAULT', '%DC/DC hardware fault%'),
    ('LOW BATTERY INSULATION IMPEDANCE', '%Low battery insulation impedance%'),
]

# What was changed on the plant, so a month can be read against it.
EVENTS = [
    ('2026-05', 'one auxiliary switch disconnected, on R&D advice — cases fell after'),
    ('2026-09', 'LC limit set to 2800 kW around 15-16 Sep  (= 16 x 175 kW, nameplate)'),
    ('2026-10', 'LC limit lowered to 2650 kW on 1 Oct 01:00  (~94.6% of nameplate)'),
]

# Nameplate, from the owner: 175 kW per DC-DC, 8 per BESS, 2 BESS and 2 PCS per LC.
DCDC_KW, DCDC_PER_BESS, BESS_PER_LC, PCS_KW, PCS_PER_LC = 175, 8, 2, 1575, 2


def bar(n, top, width=38):
    return '#' * max(1, int(round(width * n / top))) if n and top else ''


def section(title):
    print('\n' + title)
    print('-' * len(title))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--db', default=os.path.join(ROOT, 'dist', 'pv_bess_tracker.db'))
    ap.add_argument('--daily', metavar='YYYY-MM',
                    help='day by day for one month, to find the day something changed')
    a = ap.parse_args()
    if not os.path.isfile(a.db):
        sys.exit('no database at {}'.format(a.db))
    conn = sqlite3.connect('file:{}?mode=ro'.format(a.db.replace('\\', '/')), uri=True)

    per_lc = DCDC_PER_BESS * BESS_PER_LC * DCDC_KW
    print('=' * 72)
    print('DC-DC ANALYSIS'.center(72))
    print('=' * 72)
    print('Per LC: {} DC-DC x {} kW = {} kW   |   {} PCS x {} kW = {} kW'.format(
        DCDC_PER_BESS * BESS_PER_LC, DCDC_KW, per_lc,
        PCS_PER_LC, PCS_KW, PCS_PER_LC * PCS_KW))
    print('The PCS can pull {} kW more than the DC side can deliver ({:.1f}%).'.format(
        PCS_PER_LC * PCS_KW - per_lc,
        100.0 * (PCS_PER_LC * PCS_KW - per_lc) / per_lc))
    print('At a 2800 kW limit every module sits at exactly {} kW when they share'.format(DCDC_KW))
    print('perfectly. They never do, so some pass nameplate while the LC is "within".')
    print('At 2650 kW each module averages {:.1f} kW, about {:.1f}% of nameplate.'.format(
        2650.0 / 16, 100 * (2650.0 / 16) / DCDC_KW))

    if a.daily:
        y, m = int(a.daily[:4]), int(a.daily[5:7])
        print('\nDAY BY DAY — {}\n'.format(a.daily) + '=' * 72)
        print('A step down that lasts is a change that worked; a quiet spell that')
        print('ends by itself is not.\n')
        for label, like in FAULTS:
            rows = conn.execute(
                "SELECT substr(activated, 9, 2) d, COUNT(*) FROM alarm_events "
                "WHERE trigger_name LIKE ? AND year=? AND month=? "
                "GROUP BY d ORDER BY d", (like, y, m)).fetchall()
            byday = {int(d): n for d, n in rows if d}
            hi = max(byday.values()) if byday else 1
            tot = sum(byday.values())
            print('{}   {} event(s)'.format(label, tot))
            print('-' * len(label))
            half1 = sum(n for d, n in byday.items() if d <= 15)
            for d in range(1, 32):
                n = byday.get(d, 0)
                print('  {:02d}  {:5d}  {}'.format(d, n, bar(n, hi, 44)))
            print('  1st-15th: {}    16th-end: {}\n'.format(half1, tot - half1))
        conn.close()
        return

    months = [r[0] for r in conn.execute(
        "SELECT DISTINCT printf('%04d-%02d', year, month) FROM alarm_events "
        "ORDER BY 1")]
    print('\nAlarm data in the database: {} .. {}'.format(
        months[0] if months else '-', months[-1] if months else '-'))
    for ym, what in EVENTS:
        print('  {}  {}{}'.format(ym, what,
                                  '' if ym in months else '   <-- NOT IMPORTED YET'))

    for label, like in FAULTS:
        rows = conn.execute(
            "SELECT printf('%04d-%02d', year, month) ym, block, lc, unit, sub, "
            "       activated, COALESCE(duration_min, 0) "
            "FROM alarm_events WHERE trigger_name LIKE ? AND sub IS NOT NULL "
            "ORDER BY activated", (like,)).fetchall()
        print('\n\n' + '=' * 72)
        print('{}   —   {} events'.format(label, len(rows)))
        print('=' * 72)
        if not rows:
            continue

        # ── by month, against what was changed ──────────────────────────────
        section('BY MONTH')
        bym = Counter(r[0] for r in rows)
        hi = max(bym.values())
        for ym in sorted(bym):
            note = next((w for m, w in EVENTS if m == ym), '')
            print('  {}  {:5d}  {:<38} {}'.format(
                ym, bym[ym], bar(bym[ym], hi), '<- ' + note if note else ''))

        # ── which blocks ────────────────────────────────────────────────────
        section('BLOCKS MOST AFFECTED  (block, how many events, in how many months)')
        byblk = Counter(r[1] for r in rows)
        mon_of = defaultdict(set)
        for r in rows:
            mon_of[r[1]].add(r[0])
        hi = max(byblk.values())
        for blk, n in byblk.most_common(12):
            print('  block {:<4} {:5d}  {:<30} {} month(s)'.format(
                blk, n, bar(n, hi, 30), len(mon_of[blk])))
        print('  ... {} block(s) affected in total, out of 70'.format(len(byblk)))

        # ── which of the eight positions ────────────────────────────────────
        section('DC-DC POSITION WITHIN THE BESS  (is any of the eight worse?)')
        bysub = Counter(r[4] for r in rows)
        hi = max(bysub.values()) if bysub else 1
        for s in range(1, 9):
            n = bysub.get(s, 0)
            print('  module {}  {:5d}  {}'.format(s, n, bar(n, hi, 34)))
        section('BESS WITHIN THE LC')
        byunit = Counter(r[3] for r in rows)
        for u in sorted(byunit):
            print('  BESS {}  {:5d}'.format(u, byunit[u]))

        # ── whole container, or a module or two ─────────────────────────────
        section('HOW MANY MODULES FAIL TOGETHER  (same BESS, same timestamp)')
        groups = defaultdict(set)
        for ym, blk, lc, unit, sub, act, dur in rows:
            groups[(blk, lc, unit, act)].add(sub)
        sizes = Counter(len(v) for v in groups.values())
        hi = max(sizes.values())
        for k in sorted(sizes):
            tag = '  <- the whole container' if k == 8 else ''
            print('  {} module(s) at once  {:5d} occasion(s)  {}{}'.format(
                k, sizes[k], bar(sizes[k], hi, 26), tag))
        whole = sizes.get(8, 0)
        print('  {} occasions in total; {} took all eight ({:.1f}%).'.format(
            len(groups), whole, 100.0 * whole / len(groups)))

        # ── time of day ─────────────────────────────────────────────────────
        section('HOUR OF DAY  (when the fault is raised)')
        byhr = Counter(int(r[5][11:13]) for r in rows if len(r[5] or '') >= 13)
        hi = max(byhr.values()) if byhr else 1
        for h in range(24):
            n = byhr.get(h, 0)
            print('  {:02d}:00  {:5d}  {}'.format(h, n, bar(n, hi, 34)))

        # ── the same module, again and again ────────────────────────────────
        section('MODULES THAT CAME BACK  (same module, in several months)')
        permod = defaultdict(set)
        cnt = Counter()
        for ym, blk, lc, unit, sub, act, dur in rows:
            key = '{}.{}.{}.{}'.format(blk, lc, unit, sub)
            permod[key].add(ym)
            cnt[key] += 1
        repeat = sorted(((len(m), cnt[k], k) for k, m in permod.items()),
                        reverse=True)[:12]
        for nm, ne, key in repeat:
            print('  {:<16} {} month(s), {} event(s)'.format(key, nm, ne))
        once = sum(1 for m in permod.values() if len(m) == 1)
        print('  {} module(s) saw this fault; {} in one month only, {} in several.'
              .format(len(permod), once, len(permod) - once))

    conn.close()
    print('\n' + '=' * 72)
    print('Block.LC.BESS.module — 69.01.02.05 is block 69, LC 1, BESS 2, module 5.')
    print('=' * 72)


if __name__ == '__main__':
    main()
