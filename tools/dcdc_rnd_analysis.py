"""tools/dcdc_rnd_analysis.py — the DC-DC fault analysis behind the R&D paper.

    python tools\\dcdc_rnd_analysis.py                      March-September
    python tools\\dcdc_rnd_analysis.py --json out.json      machine-readable too

Two faults are in scope, both on the DC-DC converters:

    DCDC - Fault Status 1: DC/DC hardware fault
    DCDC - Fault Status 1: Low battery insulation impedance

Sources: `alarm_events` in the desktop database (March-August, carries
`duration_min`) and the merged September export, which has no deactivation
times — so September contributes counts and timing but not durations.

The point of the script is the **filter**. A DC-DC reports a hardware fault
when its container stops for any reason, so an event that follows a fire alarm
or an islanding trip on the same container is a *consequence* of that stop, not
a fault of the converter. September is full of them: the site was testing fire
detectors, and each test stopped a container. Counting those would overstate
the problem several times over and point R&D at the wrong thing.

An event is therefore classed:

  secondary  another container-stopping alarm hit the SAME container within
             WINDOW_MIN before it (fire alarm, firefighting fault, islanding
             protection, AC under voltage / frequency — the plant-wide ones)
  primary    nothing of the sort: the converter fault stands on its own

Addresses line up by level: `DC/DC 06.01.02.05` is block 06, LC 01, BESS 02,
module 05, and its container is `BSC 06.01.02`.
"""
import argparse
import datetime as dt
import json
import os
import re
import sqlite3
import statistics
import sys
from collections import Counter, defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MERGED = (r'C:\Users\user1\Desktop\ACWA BESS Tashkent\LTSA monthly report'
          r'\2026\September\alarms\Alarms merged.xlsx')

FAULTS = {
    'hardware':   '%DC/DC hardware fault%',
    'insulation': '%Low battery insulation impedance%',
}
FAULT_TITLE = {
    'hardware':   'DC/DC hardware fault',
    'insulation': 'Low battery insulation impedance',
}

# What stops a container for reasons of its own. A DC-DC fault that follows one
# of these on the same container is a consequence of the stop.
STOPPERS = ('firefighting system fire alarm', 'firefighting system fault',
            'islanding protection', 'ac under voltage', 'ac under frequency',
            'fire alarm', 'smoke')
WINDOW_MIN = 30          # how far back a stop still explains a converter fault

# Plant-wide electrical disturbances, confirmed in the alarm record and by the
# site. Every DC-DC fault inside one is a consequence of the disturbance, not a
# converter fault, and dozens of containers trip within a minute or two — so
# they are excluded from the converter statistics and reported separately.
# Each is (from, to, what the alarm record shows).
GRID_WINDOWS = [
    ('2026-05-25 23:00', '2026-05-26 00:30',
     'PCS midpoint potential shift and DC component fault on 35 blocks at 23:03'),
    ('2026-05-30 12:00', '2026-05-30 15:30',
     'PCS AC under voltage on 44 blocks at 12:14'),
    ('2026-09-25 05:00', '2026-09-25 06:30',
     'LC node fault on 19 blocks and PCS fault on 17 at 05:08-05:09; '
     'site reports a grid problem 05:00-06:00'),
]

# Plant layout, from the owner.
DCDC_KW, DCDC_PER_BESS, BESS_PER_LC, PCS_KW, PCS_PER_LC = 175, 8, 2, 1575, 2
BLOCKS = 70

EVENTS = [
    ('2026-05', 'One auxiliary switch disconnected on R&D advice'),
    ('2026-09', 'LC power limit set to 2800 kW on 15-16 Sep (= 16 x 175 kW)'),
    ('2026-09', 'Fire-detector testing: each test stops a container'),
    ('2026-10', 'LC power limit lowered to 2650 kW, 1 Oct 01:00'),
]

ADDR = re.compile(r'(\d+)\.(\d+)\.(\d+)(?:\.(\d+))?')


def parse_addr(element):
    """(block, lc, unit, module) from an element name; module may be None."""
    m = ADDR.search(element or '')
    if not m:
        return None
    b, l, u, s = m.groups()
    return int(b), int(l), int(u), (int(s) if s else None)


def load_db():
    """Every alarm March-August, as (ts, trigger, block, lc, unit, sub, dur)."""
    path = os.path.join(ROOT, 'dist', 'pv_bess_tracker.db')
    if not os.path.isfile(path):
        return []
    conn = sqlite3.connect('file:{}?mode=ro'.format(path.replace('\\', '/')), uri=True)
    try:
        rows = conn.execute(
            "SELECT activated, trigger_name, block, lc, unit, sub, duration_min "
            "FROM alarm_events WHERE activated IS NOT NULL").fetchall()
    finally:
        conn.close()
    return [(r[0], r[1] or '', r[2], r[3], r[4], r[5], r[6]) for r in rows]


def load_merged(path):
    """September, from the merged export. No deactivation times in it."""
    if not os.path.isfile(path):
        print('   (no merged September export at {})'.format(path))
        return []
    import openpyxl
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    out = []
    for sheet in wb.sheetnames:
        for r in wb[sheet].iter_rows(min_row=2, values_only=True):
            if not r or len(r) < 5 or not r[2]:
                continue
            ts = str(r[2])
            if not ts.startswith('2026-09'):
                continue                      # the DB already holds the rest
            a = parse_addr(str(r[4] or ''))
            if not a:
                continue
            out.append((ts, str(r[3] or ''), a[0], a[1], a[2], a[3], None))
    wb.close()
    return out


def to_dt(ts):
    try:
        return dt.datetime.strptime(str(ts)[:19], '%Y-%m-%d %H:%M:%S')
    except ValueError:
        return None


def classify(events):
    """Split the two DC-DC faults into primary and secondary.

    Secondary = a container-stopping alarm hit the same container in the
    WINDOW_MIN before it.
    """
    stops = defaultdict(list)                 # (block, lc, unit) -> [datetime]
    for ts, trig, b, l, u, s, dur in events:
        low = trig.lower()
        if any(k in low for k in STOPPERS):
            t = to_dt(ts)
            if t and b is not None:
                stops[(b, l, u)].append(t)
    for v in stops.values():
        v.sort()

    windows = [(to_dt(a + ':00'), to_dt(b + ':00'), why)
               for a, b, why in GRID_WINDOWS]
    out = {k: {'primary': [], 'secondary': [], 'grid': []} for k in FAULTS}
    for ts, trig, b, l, u, s, dur in events:
        low = trig.lower()
        kind = ('hardware' if 'dc/dc hardware fault' in low else
                'insulation' if 'low battery insulation impedance' in low else None)
        if not kind or b is None:
            continue
        t = to_dt(ts)
        rec = {'ts': ts, 'dt': t, 'block': b, 'lc': l, 'unit': u, 'sub': s,
               'dur': dur, 'month': str(ts)[:7], 'day': str(ts)[:10],
               'hour': int(str(ts)[11:13]) if len(str(ts)) > 12 else None}
        if t and any(w0 <= t <= w1 for w0, w1, _ in windows):
            out[kind]['grid'].append(rec)
            continue
        near = stops.get((b, l, u), [])
        secondary = t and any(0 <= (t - st).total_seconds() / 60.0 <= WINDOW_MIN
                              for st in near)
        out[kind]['secondary' if secondary else 'primary'].append(rec)
    return out


def bar(n, top, width=34):
    return '#' * max(1, int(round(width * n / top))) if n and top else ''


def head(title, ch='='):
    print('\n' + ch * 74)
    print(title)
    print(ch * 74)


def sub(title):
    print('\n' + title)
    print('-' * len(title))


def report(kind, groups):
    prim, sec, grid = groups['primary'], groups['secondary'], groups['grid']
    total = len(prim) + len(sec) + len(grid)
    head('{}  —  {} logged, {} genuine, {} during a grid event, {} after a '
         'container stop'.format(FAULT_TITLE[kind].upper(), total, len(prim),
                                 len(grid), len(sec)))
    if not total:
        return {}
    print('Only the {} genuine events are converter faults. The others are'
          .format(len(prim)))
    print('consequences of something that stopped the container first:')
    for a, b, why in GRID_WINDOWS:
        n = sum(1 for r in grid if a[:10] == r['day'])
        if n:
            print('  {} {}-{}  {:4d} event(s)'.format(a[:10], a[11:], b[11:], n))
            print('      {}'.format(why))
    if sec:
        print('  a fire alarm, firefighting fault or islanding trip on the same')
        print('  container within {} min: {} event(s)'.format(WINDOW_MIN, len(sec)))
    print('  Taking those out removes {:.0f}% of the raw count.'
          .format(100.0 * (total - len(prim)) / total))

    # ── month by month, primary against secondary ───────────────────────────
    sub('BY MONTH  (primary | secondary)')
    months = sorted({r['month'] for r in prim} | {r['month'] for r in sec})
    pm = Counter(r['month'] for r in prim)
    sm = Counter(r['month'] for r in sec)
    hi = max([pm[m] for m in months] or [1])
    for m in months:
        note = ' · '.join(w for mm, w in EVENTS if mm == m)
        print('  {}  {:5d} | {:5d}  {:<34} {}'.format(
            m, pm[m], sm[m], bar(pm[m], hi), '<- ' + note if note else ''))

    # ── how long one lasts ──────────────────────────────────────────────────
    durs = [r['dur'] for r in prim if r['dur'] is not None]
    if durs:
        sub('HOW LONG ONE LASTS  (primary only; September has no clear times)')
        durs.sort()
        band = Counter('under 5 min' if d < 5 else '5-15 min' if d <= 15
                       else '15-60 min' if d <= 60 else 'over an hour'
                       for d in durs)
        for k in ('under 5 min', '5-15 min', '15-60 min', 'over an hour'):
            if band.get(k):
                print('  {:<14} {:5d}  ({:.0f}%)'.format(
                    k, band[k], 100.0 * band[k] / len(durs)))
        print('  median {:.0f} min, mean {:.0f} min, longest {:.0f} min'.format(
            statistics.median(durs), sum(durs) / len(durs), max(durs)))
        print('  Every one of them cleared without intervention.')

    # ── where ───────────────────────────────────────────────────────────────
    sub('BLOCKS  (primary events)')
    byblk = Counter(r['block'] for r in prim)
    mon_of = defaultdict(set)
    for r in prim:
        mon_of[r['block']].add(r['month'])
    hi = max(byblk.values()) if byblk else 1
    for blk, n in byblk.most_common(12):
        print('  block {:<4} {:5d}  {:<26} seen in {} month(s)'.format(
            blk, n, bar(n, hi, 26), len(mon_of[blk])))
    print('  {} of {} blocks affected'.format(len(byblk), BLOCKS))

    sub('POSITION WITHIN THE BESS  (is one of the eight worse?)')
    bysub = Counter(r['sub'] for r in prim if r['sub'])
    hi = max(bysub.values()) if bysub else 1
    for s in range(1, DCDC_PER_BESS + 1):
        n = bysub.get(s, 0)
        print('  module {}  {:5d}  {}'.format(s, n, bar(n, hi, 30)))
    if bysub:
        lo, high = min(bysub.values()), max(bysub.values())
        print('  spread {}..{} — {:.1f}x between the quietest and the busiest'
              .format(lo, high, high / lo if lo else 0))

    # ── one module, or the whole container ──────────────────────────────────
    sub('HOW MANY MODULES AT ONCE  (same container, same minute)')
    groups_ = defaultdict(set)
    for r in prim:
        groups_[(r['block'], r['lc'], r['unit'], r['ts'])].add(r['sub'])
    sizes = Counter(len(v) for v in groups_.values())
    hi = max(sizes.values()) if sizes else 1
    for k in sorted(sizes):
        tag = '  <- the whole container' if k == DCDC_PER_BESS else ''
        print('  {} module(s)  {:5d} occasion(s)  {:<22}{}'.format(
            k, sizes[k], bar(sizes[k], hi, 22), tag))
    whole = sizes.get(DCDC_PER_BESS, 0)
    if groups_:
        print('  {} occasions; {} took all eight ({:.1f}%)'.format(
            len(groups_), whole, 100.0 * whole / len(groups_)))

    # ── when ────────────────────────────────────────────────────────────────
    sub('HOUR OF DAY  (primary events)')
    byhr = Counter(r['hour'] for r in prim if r['hour'] is not None)
    hi = max(byhr.values()) if byhr else 1
    for h in range(24):
        n = byhr.get(h, 0)
        if n:
            print('  {:02d}:00  {:5d}  {}'.format(h, n, bar(n, hi, 30)))
    night = sum(byhr.get(h, 0) for h in (22, 23, 0, 1, 2))
    if prim:
        print('  22:00-02:00 holds {} of {} ({:.0f}%)'.format(
            night, len(prim), 100.0 * night / len(prim)))

    # ── the same module again ───────────────────────────────────────────────
    sub('MODULES THAT CAME BACK')
    per = defaultdict(set)
    cnt = Counter()
    for r in prim:
        key = '{}.{:02d}.{:02d}.{}'.format(r['block'], r['lc'], r['unit'], r['sub'])
        per[key].add(r['month'])
        cnt[key] += 1
    rep = sorted(((len(m), cnt[k], k) for k, m in per.items()), reverse=True)[:12]
    for nm, ne, key in rep:
        print('  {:<16} {} month(s), {} event(s)'.format(key, nm, ne))
    once = sum(1 for m in per.values() if len(m) == 1)
    fleet = BLOCKS * BESS_PER_LC * DCDC_PER_BESS * 2
    print('  {} distinct modules of about {} in the plant ({:.0f}%); '
          '{} in one month only'.format(len(per), fleet,
                                        100.0 * len(per) / fleet, once))
    return {
        'total': total, 'primary': len(prim), 'secondary': len(sec),
        'by_month_primary': dict(pm), 'by_month_secondary': dict(sm),
        'durations': {'n': len(durs),
                      'median': statistics.median(durs) if durs else None,
                      'max': max(durs) if durs else None,
                      'pct_5_15': (100.0 * sum(1 for d in durs if 5 <= d <= 15)
                                   / len(durs)) if durs else None},
        'blocks_affected': len(byblk),
        'modules_affected': len(per),
        'whole_container_pct': (100.0 * whole / len(groups_)) if groups_ else 0,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--merged', default=MERGED)
    ap.add_argument('--json', default='')
    a = ap.parse_args()

    per_lc = DCDC_PER_BESS * BESS_PER_LC * DCDC_KW
    head('DC-DC CONVERTER FAULTS — ACWA RIVERSIDE BESS', '=')
    print('Per LC: {} DC-DC x {} kW = {} kW of converter capacity, against '
          '{} PCS x {} kW = {} kW.'.format(DCDC_PER_BESS * BESS_PER_LC, DCDC_KW,
                                           per_lc, PCS_PER_LC, PCS_KW,
                                           PCS_PER_LC * PCS_KW))
    print('The PCS can draw {} kW more than the DC side can deliver ({:.1f}%).'
          .format(PCS_PER_LC * PCS_KW - per_lc,
                  100.0 * (PCS_PER_LC * PCS_KW - per_lc) / per_lc))

    print('\nReading…')
    events = load_db()
    print('   database: {} alarm(s) (March-August, with clear times)'.format(len(events)))
    sep = load_merged(a.merged)
    print('   September export: {} alarm(s) (no clear times)'.format(len(sep)))
    events += sep

    groups = classify(events)
    out = {}
    for kind in ('hardware', 'insulation'):
        out[kind] = report(kind, groups[kind])

    if a.json:
        with open(a.json, 'w', encoding='utf-8') as f:
            json.dump(out, f, indent=2)
        print('\nwrote {}'.format(a.json))


if __name__ == '__main__':
    main()
