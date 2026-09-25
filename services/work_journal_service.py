"""services/work_journal_service.py — one journal of work records.

Four screens used to hold the same thing: Work Reports (`work_logs`), Field Log
and Field Records (`work_log_entries`), and the Daily Log. This module reads
them as **one list** with one shape, so the Work page, the Today page and the
tests all count the same rows.

Two stores, deliberately not merged in the database:

  `work_log_entries`  the live record. Written by the phone and now by the
                      desktop as well; carries the plant block, the node, PTW,
                      hours, the customer text and the internal note.
  `work_logs`         the older desktop Work Report. **Read-only** here and
                      labelled "Old format" — its rows stay visible and keep
                      counting for the report (report_workflow_service builds
                      3.2 from both), but new work is not written there.

Block numbers: `work_logs` and the container link number blocks *inside a
zone*; the plant and the customer speak plant-wide (block 57). Records carry
`plant_block` when the node picker set it, otherwise it is translated through
project_service's block map — never guessed.

No PyQt here: the page formats, this module decides.
"""
import datetime
import re
from typing import List, Optional

from database.db_manager import get_connection
from services.project_service import zone_block_to_plant, plant_block_to_zone

# What the status chips mean. The phone writes '', 'open', 'in_progress',
# 'done'; the desktop Work Report writes 'Fixed' / 'Monitoring' / …
OPEN_STATES = ('', 'open', 'in progress', 'in_progress', 'needs visit',
               'needs_visit', 'not resolved', 'monitoring', 'escalated')
DONE_STATES = ('done', 'fixed', 'closed', 'complete', 'completed')

KIND_PM = 'PM'
KIND_FAULT = 'Fault'
KIND_OTHER = 'Other'

PM_CATEGORIES = ('maintenance',)
FAULT_CATEGORIES = ('fault', 'repair')
# the same test report_workflow_service uses to keep PM out of section 3.2
_PM_TEXT = re.compile(r'\bPM\b|preventive', re.IGNORECASE)


def _norm_status(s: str) -> str:
    s = (s or '').strip().lower()
    if s in DONE_STATES:
        return 'Done'
    if s in ('needs visit', 'needs_visit'):
        return 'Needs visit'
    if s in ('in progress', 'in_progress'):
        return 'In progress'
    return 'Open'


def _kind(category: str, fault: str, text: str) -> str:
    """PM only when the record says so — the same test the report uses to keep
    PM out of section 3.2. The old phone form defaulted to "maintenance", so
    most repairs sit in that category; the report treats them as corrective
    work, and so does this list, or a coolant top-up would read as PM here and
    as a repair in the customer's report."""
    cat = (category or '').strip().lower()
    if _PM_TEXT.search(f"{fault or ''} {text or ''}"):
        return KIND_PM
    if cat in FAULT_CATEGORIES or cat in PM_CATEGORIES or (fault or '').strip():
        return KIND_FAULT
    return KIND_OTHER


# "5zone 4block 2bsc", "1zona 4 block", "4zon 5 blok", "2Zone 5b 1lc", "3zone 4bkock"
_LOC_ZB = re.compile(r'(\d+)\s*zon[ea]?\s*(\d+)\s*(?:b\w*)', re.IGNORECASE)
_LOC_LC = re.compile(r'(\d)\s*lc\b', re.IGNORECASE)


def suggest_block(project_id: int, location: str) -> Optional[dict]:
    """A plant block read from the free-text location an older phone record
    carries, for a person to accept — never applied by itself.

    "7zone 3b 1lc 1bsc" → zone 7, block 3 → plant block via the project's block
    map (the only source that knows zone 7 starts where it does). A bare number
    ("22") is offered as the plant block itself. Anything else: no suggestion.
    """
    text = (location or '').strip()
    if not text:
        return None
    m = _LOC_ZB.search(text)
    if m:
        plant = zone_block_to_plant(project_id, int(m.group(1)), int(m.group(2)))
        if not plant:
            return None
        lc = _LOC_LC.search(text)
        return {'block': int(plant), 'lc': f'LC{lc.group(1)}' if lc else '',
                'why': f'zone {m.group(1)} block {m.group(2)}'}
    if text.isdigit():
        n = int(text)
        if plant_block_to_zone(project_id, n)[0] is not None:
            return {'block': n, 'lc': '', 'why': 'the number itself'}
    return None


def zone_label(project_id: int, plant_block) -> str:
    """'Z8/B2' for a plant block, '' when the project does not map."""
    if not plant_block:
        return ''
    z, b = plant_block_to_zone(project_id, int(plant_block))
    return f'Z{z}/B{b}' if z else ''


# The node's device → the project container it names. A block holds one LC
# cabinet, one PCS / converter (its four units share that serial) and four
# batteries in container_index order (BESS 1..4). "LC1" alone or "MV station"
# names no single container.
_DEV = re.compile(r'^\s*(BESS|PCS|LC)\b\s*(\d+)?', re.IGNORECASE)
_DEV_TYPE = {'BESS': 'Battery', 'PCS': 'PCS / Converter', 'LC': 'LC Cabinet'}


def container_for_node(project_id: int, plant_block, device: str):
    """(container_id, serial) of the container the node names, or (None, '')."""
    m = _DEV.match(device or '')
    if not plant_block or not m:
        return None, ''
    z, b = plant_block_to_zone(project_id, int(plant_block))
    if z is None:
        return None, ''
    kind = m.group(1).upper()
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT id, serial_number FROM containers WHERE project_id=? "
            "AND zone_number=? AND block_number=? AND container_type=? "
            "ORDER BY container_index", (project_id, z, b, _DEV_TYPE[kind])).fetchall()
    finally:
        conn.close()
    n = int(m.group(2)) if (kind == 'BESS' and m.group(2)) else 1
    if (kind == 'BESS' and not m.group(2)) or not 1 <= n <= len(rows):
        return None, ''
    return rows[n - 1][0], (rows[n - 1][1] or '').strip()


# ── Excel export ─────────────────────────────────────────────────────────────

_EXPORT_COLS = [('Date', 'date', 11), ('Block', 'block', 7), ('Zone', 'zone_label', 8),
                ('LC', 'lc', 11), ('Device', 'device', 11), ('Serial No.', 'serial', 15),
                ('Type', 'kind', 7), ('Fault', 'title', 34),
                ('What was done', 'work_done', 50), ('Status', 'status', 11),
                ('PTW No.', 'ptw', 14), ('SAP', 'sap', 12), ('Start', 'time_from', 7),
                ('End', 'time_to', 7), ('Hours', 'hours', 7),
                ('Availability', 'impact', 12), ('Source', 'source', 10)]
_EXPORT_TEXT = {'source': {'phone': 'Phone', 'desktop': 'Desktop', 'old': 'Old format'},
                'impact': {'none': '', 'counts': 'Counts', 'excluded': 'Excluded'}}


def export_excel(rows: List[dict], path: str, include_internal: bool = False) -> int:
    """The listed records as one sheet, no photos. The internal diagnosis
    note is ours: it goes in only when asked for. Returns the row count."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter
    cols = list(_EXPORT_COLS)
    if include_internal:
        cols.insert(9, ('Internal note', 'internal_note', 40))
    wb = Workbook()
    ws = wb.active
    ws.title = 'Work records'
    for c, (head, _k, width) in enumerate(cols, 1):
        cell = ws.cell(row=1, column=c, value=head)
        cell.font = Font(bold=True, color='FFFFFF')
        cell.fill = PatternFill('solid', fgColor='1A2B45')
        cell.alignment = Alignment(vertical='center')
        ws.column_dimensions[get_column_letter(c)].width = width
    wrap = Alignment(wrap_text=True, vertical='top')
    top = Alignment(vertical='top')
    for i, r in enumerate(rows, 2):
        for c, (_h, k, _w) in enumerate(cols, 1):
            v = r.get(k)
            if k in _EXPORT_TEXT:
                v = _EXPORT_TEXT[k].get(v, v)
            if k == 'date' and v:
                try:
                    v = datetime.date.fromisoformat(v[:10])
                except ValueError:
                    pass
            cell = ws.cell(row=i, column=c, value='' if v is None else v)
            if isinstance(v, str) and v.startswith('='):
                cell.data_type = 's'           # phone text, not a formula
            if isinstance(v, datetime.date):
                cell.number_format = 'dd.mm.yyyy'
            cell.alignment = wrap if k in ('title', 'work_done', 'internal_note') else top
    ws.freeze_panes = 'A2'
    ws.auto_filter.ref = f"A1:{get_column_letter(len(cols))}{max(1, len(rows) + 1)}"
    wb.save(path)
    return len(rows)


# ── Photos in readable folders ───────────────────────────────────────────────
# The app keeps photos under field_images/<record id>/. A person looks for
# "13 Sep, block 33, the compressor", so every record with photos also gets a
# copy in <photos root>/<project>/<date Block N LC device - fault>/. A hidden
# .record file names the record, so an edited record's folder is renamed, not
# duplicated. Files there are never deleted by the app.
_BAD_CH = re.compile(r'[<>:"/\\|?*\x00-\x1f]+')
_MARKER = '.record'


def photos_root() -> str:
    import os
    return os.environ.get('BESS_PHOTOS_DIR') or os.path.join(
        os.path.expanduser('~'), 'Documents', 'BESS Tracker Photos')


def _clean(s: str, maxlen: int) -> str:
    s = re.sub(r'\s+', ' ', _BAD_CH.sub(' ', s or '')).strip(' .')
    return s[:maxlen].rstrip(' .')


def photo_folder_name(row: dict) -> str:
    """'2026-09-13 Block 33 LC1 BESS 3 - Compressor alarm'"""
    bits = [row.get('date') or 'no date',
            f"Block {row['block']}" if row.get('block') else 'No block']
    bits += [x for x in (row.get('lc'), row.get('device')) if x]
    what = row.get('title') or row.get('work_done') or ''
    what = re.sub(r'^PM:\s*(?=PM\b)', '', what)          # "PM: PM as per …"
    name = ' '.join(bits) + (f' - {what}' if what else '')
    return _clean(name, 90) or str(row['ref'])


def _folder_index(root: str) -> dict:
    """{record id: folder name} from the .record markers under root."""
    import os
    out = {}
    if os.path.isdir(root):
        for d in os.listdir(root):
            try:
                with open(os.path.join(root, d, _MARKER), encoding='utf-8') as f:
                    out[f.read().strip()] = d
            except OSError:
                pass
    return out


def _record_dir(root: str, row: dict, index: dict = None) -> str:
    import os
    if index is None:
        index = _folder_index(root)
    want = photo_folder_name(row)
    ref = str(row['ref'])
    have = index.get(ref)
    # a second record with the same date, node and text is numbered "(2)"
    if have is not None and re.fullmatch(re.escape(want) + r'( \(\d+\))?', have):
        return os.path.join(root, have)
    target, k = os.path.join(root, want), 2
    while os.path.exists(target):
        target, k = os.path.join(root, f'{want} ({k})'), k + 1
    if have is not None:
        try:
            os.rename(os.path.join(root, have), target)
        except OSError:                      # open in Explorer: keep the old name
            return os.path.join(root, have)
        index[ref] = os.path.basename(target)
        return target
    os.makedirs(target)
    index[ref] = os.path.basename(target)
    marker = os.path.join(target, _MARKER)
    with open(marker, 'w', encoding='utf-8') as f:
        f.write(str(row['ref']))
    try:
        import ctypes
        ctypes.windll.kernel32.SetFileAttributesW(marker, 2)     # hidden
    except Exception:                                            # noqa: BLE001
        pass
    return target


def mirror_record_photos(project_name: str, row: dict, indexes: dict = None) -> Optional[dict]:
    """Copy one record's photos into its readable folder. None if it has none.
    Returns {'folder', 'copied', 'missing'}; a photo still on the server is
    'missing' until a sync downloads it."""
    import os
    import shutil
    from services.image_service import get_images_for_log
    if row.get('old_format'):
        return None
    imgs = get_images_for_log(row['ref'])
    if not imgs:
        return None
    root = os.path.join(photos_root(), _clean(project_name, 60) or 'Project')
    if indexes is not None and root not in indexes:
        indexes[root] = _folder_index(root)
    folder = _record_dir(root, row, None if indexes is None else indexes[root])
    # {file: size} from one directory read; phones often name every photo
    # "image.jpg", so a same-named file of another size is another photo
    present = {e.name: e.stat().st_size for e in os.scandir(folder) if e.is_file()}
    copied = missing = 0
    for img in imgs:
        src = img.get('file_path') or ''
        name = _clean(img.get('filename') or os.path.basename(src), 80) or f"{img['id']}.jpg"
        stem, ext = os.path.splitext(name)
        alt = f"{stem}_{str(img['id'])[:8]}{ext}"
        if alt in present or (name in present and present[name] == img.get('size_bytes')):
            continue
        if not os.path.isfile(src):
            missing += 1
            continue
        size = os.path.getsize(src)
        if name in present:
            if present[name] == size:
                continue
            name = alt
        shutil.copy2(src, os.path.join(folder, name))
        present[name] = size
        copied += 1
    return {'folder': folder, 'copied': copied, 'missing': missing}


def mirror_photos() -> dict:
    """Every project's records with photos → readable folders. Run after a
    sync; only what is not there yet is copied."""
    from services.project_service import get_all_projects
    out = {'records': 0, 'copied': 0, 'missing': 0}
    seen, indexes = set(), {}
    for p in get_all_projects():
        for row in records(p.id, include_old=False):
            if row['ref'] in seen:
                continue
            seen.add(row['ref'])
            res = mirror_record_photos(p.name if row.get('project_id') else 'No project',
                                       row, indexes)
            if res:
                out['records'] += 1
                out['copied'] += res['copied']
                out['missing'] += res['missing']
    return out


def node_text(row: dict) -> str:
    """'Block 57 · Z8/B2 · LC1 · PCS 2' — as much of it as is known."""
    bits = []
    if row.get('block'):
        bits.append(f"Block {row['block']}")
        if row.get('zone_label'):
            bits.append(row['zone_label'])
    else:
        bits.append('No block')
    for key in ('lc', 'device'):
        if row.get(key):
            bits.append(row[key])
    return ' · '.join(bits)


def _age_days(date_str: str, today: datetime.date = None) -> Optional[int]:
    try:
        d = datetime.date.fromisoformat((date_str or '')[:10])
    except ValueError:
        return None
    return ((today or datetime.date.today()) - d).days


def records(project_id: int, date_from: str = None, date_to: str = None,
            include_old: bool = True, today: datetime.date = None) -> List[dict]:
    """Every work record of the project in the period, newest first.

    Each row: key, source, editable, date, block, zone_label, lc, device,
    node, kind, title, work_done, internal_note, status, ptw, sap, hours,
    sync, age_days, parts (the phone's free-text materials note),
    ref (the row id in its own table)."""
    conn = get_connection()
    out = []
    try:
        q = ("SELECT e.*, c.zone_number, c.block_number, c.container_index "
             "FROM work_log_entries e LEFT JOIN containers c ON e.container_id = c.id "
             "WHERE e.deleted_at IS NULL AND (e.project_id = ? OR e.project_id IS NULL)")
        p = [project_id]
        if date_from:
            q += " AND e.log_date >= ?"; p.append(date_from)
        if date_to:
            q += " AND e.log_date <= ?"; p.append(date_to)
        for r in conn.execute(q, p).fetchall():
            r = dict(r)
            block = r.get('plant_block') or zone_block_to_plant(
                project_id, r.get('zone_number'), r.get('block_number'))
            row = {
                'key': 'e:' + str(r['id']), 'ref': r['id'], 'source': 'phone'
                if (r.get('sync_status') or '') != 'local' else 'desktop',
                'editable': True, 'old_format': False,
                'date': (r.get('log_date') or '')[:10],
                'block': int(block) if block else None,
                'zone_label': zone_label(project_id, block),
                'lc': r.get('node_lc') or '', 'device': r.get('node_device') or '',
                'location': r.get('site_location') or '',
                'kind': _kind(r.get('category'), r.get('fault_name'),
                              r.get('description')),
                'title': (r.get('fault_name') or '').strip(),
                'work_done': (r.get('description') or '').strip(),
                'internal_note': (r.get('internal_note') or '').strip(),
                # what the field said it used, in its own words ("2 fuses,
                # 63 A"). The office writes the stock off against it, so the
                # card has to show it; the structured rows live in
                # worklog_spare_parts.
                'parts': (r.get('spare_parts') or '').strip(),
                'status': _norm_status(r.get('status')),
                'ptw': r.get('ptw_no') or '', 'sap': r.get('sap_ticket') or '',
                'hours': r.get('hours'), 'time_from': r.get('time_from') or '',
                'time_to': r.get('time_to') or '',
                'impact': r.get('availability_impact') or 'none',
                # who the office gave it to; the name travels with the record
                # so a list read offline still shows a person, not an id
                'assignee': r.get('assigned_to') or '',
                'assignee_name': r.get('assigned_name') or '',
                'assigned_by': r.get('assigned_by') or '',
                'due': (r.get('due_date') or '')[:10],
                'sync': (r.get('sync_status') or 'local'),
                'category': r.get('category') or '',
                'container_id': r.get('container_id'),
                'serial': (r.get('equipment_serial') or '').strip(),
                'project_id': r.get('project_id'),
            }
            row['node'] = node_text(row)
            row['age_days'] = _age_days(row['date'], today)
            out.append(row)

        if include_old:
            q = ("SELECT w.*, c.container_index FROM work_logs w "
                 "LEFT JOIN containers c ON w.container_id = c.id "
                 "WHERE w.project_id = ?")
            p = [project_id]
            if date_from:
                q += " AND w.date >= ?"; p.append(date_from)
            if date_to:
                q += " AND w.date <= ?"; p.append(date_to)
            for r in conn.execute(q, p).fetchall():
                r = dict(r)
                block = zone_block_to_plant(project_id, r.get('zone_number'),
                                            r.get('block_number'))
                row = {
                    'key': 'w:' + str(r['id']), 'ref': r['id'], 'source': 'old',
                    'editable': False, 'old_format': True,
                    'date': (r.get('date') or '')[:10],
                    'block': int(block) if block else None,
                    'zone_label': zone_label(project_id, block),
                    'lc': '', 'device': '', 'location': '',
                    'kind': _kind('fault', r.get('fault_description'),
                                  r.get('work_performed')),
                    'title': (r.get('fault_description') or '').strip(),
                    'work_done': (r.get('work_performed') or '').strip(),
                    'internal_note': (r.get('comments') or '').strip(),
                    'parts': '',            # the old format never had one
                    'status': _norm_status(r.get('status')),
                    'ptw': '', 'sap': r.get('sap_ticket') or '',
                    'hours': None, 'time_from': r.get('start_time') or '',
                    'time_to': r.get('end_time') or '',
                    'impact': r.get('availability_impact') or 'none',
                    'assignee': '', 'assignee_name': '', 'assigned_by': '',
                    'due': '',
                    'sync': 'old', 'category': 'fault',
                    'container_id': r.get('container_id'),
                    'serial': (r.get('serial_number') or '').strip(),
                }
                row['node'] = node_text(row)
                row['age_days'] = _age_days(row['date'], today)
                out.append(row)
    finally:
        conn.close()
    out.sort(key=lambda r: (r['date'] or '', r['key']), reverse=True)
    return out


# ── Filters. The Work page's tabs and chips, as data. ────────────────────────

# the "Nobody" choice on the Assigned-to chip, which is not a user id
ASSIGNED_NOBODY = '-'


def is_open(r: dict) -> bool:
    return r['status'] in ('Open', 'In progress', 'Needs visit')


def is_conflict(r: dict) -> bool:
    return r.get('sync') == 'conflict'


def has_no_block(r: dict) -> bool:
    return not r.get('block')


TABS = (
    ('all', 'All', lambda r: True),
    ('open', 'Open', is_open),
    ('noblock', 'No block', has_no_block),
    ('conflict', 'Conflicts', is_conflict),
)


def filter_rows(rows, tab='all', kind=None, status=None, block=None,
                ptw_only=False, source=None, text=None, assignee=None):
    """The same filtering the page's chips do — so a count and the list it
    opens can never disagree."""
    fn = dict((t[0], t[2]) for t in TABS).get(tab, lambda r: True)
    out = []
    for r in rows:
        if not fn(r):
            continue
        if kind and r['kind'] != kind:
            continue
        if status and r['status'] != status:
            continue
        if block and r.get('block') != int(block):
            continue
        if ptw_only and not r.get('ptw'):
            continue
        if source and r.get('source') != source:
            continue
        # '' as the chip's value means "any"; ASSIGNED_NOBODY asks for the
        # records nobody has been given, which is a real question in the office
        if assignee is not None and assignee != '':
            if assignee == ASSIGNED_NOBODY:
                if r.get('assignee'):
                    continue
            elif r.get('assignee') != assignee:
                continue
        if text:
            hay = ' '.join(str(r.get(k) or '') for k in
                           ('title', 'work_done', 'node', 'ptw', 'sap',
                            'internal_note', 'location')).lower()
            if text.lower() not in hay:
                continue
        out.append(r)
    return out


def open_faults(project_id: int, today: datetime.date = None) -> List[dict]:
    """Unfinished fault records, oldest first — the Today block and the list
    its counter opens are the same query."""
    rows = [r for r in records(project_id, today=today)
            if r['kind'] == KIND_FAULT and is_open(r)]
    rows.sort(key=lambda r: (r['date'] or ''))
    return rows


def month_range(year: int, month: int):
    import calendar
    last = calendar.monthrange(year, month)[1]
    return f'{year:04d}-{month:02d}-01', f'{year:04d}-{month:02d}-{last:02d}'


# ── Writing. One store: every new record is a work_log_entries row, which is
# the row the phones already speak, so a desktop edit syncs out like any other.
# `work_logs` is never written here — those rows are the old format.
STATUS_TO_DB = {'Open': 'open', 'In progress': 'in_progress',
                'Needs visit': 'needs_visit', 'Done': 'done'}
KIND_TO_CATEGORY = {KIND_FAULT: 'fault', KIND_PM: 'maintenance',
                    KIND_OTHER: 'other'}


class ReadOnlyRecord(RuntimeError):
    """A `work_logs` row — the old format — cannot be edited here."""


def get(key: str, project_id: int) -> Optional[dict]:
    for r in records(project_id):
        if r['key'] == key:
            return r
    return None


def save(project_id: int, key: str = None, **fields) -> str:
    """Create or update a record; returns its key.

    fields: date, block, lc, device, kind, status, title, work_done,
    internal_note, ptw, sap, hours, time_from, time_to, impact, parts,
    container_id, assignee, assignee_name, assigned_by, due.
    """
    import services.worklog_entry_service as wes
    if key and key.startswith('w:'):
        raise ReadOnlyRecord(
            'This record is in the old Work Report format and is read-only. '
            'Create a new record instead — the old one stays in the report.')

    kind = fields.get('kind') or KIND_FAULT
    work_done = fields.get('work_done') or ''
    if kind == KIND_PM and work_done and not _PM_TEXT.search(work_done):
        # A PM is reported to the customer through its PM record (section 3.1).
        # Section 3.2 takes maintenance-category records whose text does not say
        # PM, so "Coolant topped up" on a PM record would reach the customer
        # twice — once as PM hours, once as corrective work. Saying "PM" in the
        # customer's own line is both true and what keeps it out of 3.2.
        work_done = 'PM: ' + work_done

    payload = {
        'log_date': (fields.get('date') or '')[:10],
        'category': KIND_TO_CATEGORY.get(kind, 'other'),
        'description': work_done,
        'fault_name': fields.get('title') or '',
        'status': STATUS_TO_DB.get(fields.get('status') or 'Open', 'open'),
        'sap_ticket': fields.get('sap') or '',
        'plant_block': int(fields['block']) if fields.get('block') else None,
        'node_lc': fields.get('lc') or '',
        'node_device': fields.get('device') or '',
        'ptw_no': fields.get('ptw') or '',
        'time_from': fields.get('time_from') or '',
        'time_to': fields.get('time_to') or '',
        'hours': fields.get('hours'),
        'internal_note': fields.get('internal_note') or '',
        'availability_impact': fields.get('impact') or 'none',
    }
    # Who the job is with. Only written when the caller says so: a phone that
    # edits a record does not send it, and an absent key must keep the
    # assignment rather than clear it (the technician would lose the job).
    if 'assignee' in fields:
        payload['assigned_to'] = (fields.get('assignee') or '').strip()
        payload['assigned_name'] = (fields.get('assignee_name') or '').strip()
        if 'assigned_by' in fields:
            payload['assigned_by'] = (fields.get('assigned_by') or '').strip()
    if 'due' in fields:
        payload['due_date'] = (fields.get('due') or '')[:10]
    # What the card does not show stays as it is on an edit — the phone's
    # location text, its parts and its container link used to be blanked.
    for src, col in (('location', 'site_location'), ('parts', 'spare_parts'),
                     ('container_id', 'container_id'), ('serial', 'equipment_serial')):
        if src in fields:
            payload[col] = fields[src] if col == 'container_id' else (fields[src] or '').strip()
    # the node names one project container: link it, and take its serial
    # unless one was typed (a swapped unit carries a new number)
    cid, serial = container_for_node(project_id, fields.get('block'), fields.get('device'))
    if cid is not None:
        payload['container_id'] = cid
        if not payload.get('equipment_serial'):
            payload['equipment_serial'] = serial
    if key:
        wes.update_worklog_entry(key[2:], **payload)
        return key
    entry_id = wes.save_worklog_entry(project_id=project_id, **payload)
    return 'e:' + entry_id


def delete(key: str):
    import services.worklog_entry_service as wes
    if key.startswith('w:'):
        raise ReadOnlyRecord('Old-format records are deleted on the '
                             'Work Reports page in the archive.')
    wes.delete_worklog_entry(key[2:])


def seen_before(project_id: int, row: dict, limit: int = 6) -> List[dict]:
    """The same fault on the same node before — what the engineer asks first."""
    title = (row.get('title') or '').strip().lower()
    if not title:
        return []
    out = [r for r in records(project_id)
           if r['key'] != row.get('key')
           and (r.get('title') or '').strip().lower() == title
           and (row.get('block') is None or r.get('block') == row.get('block'))]
    out.sort(key=lambda r: r['date'] or '', reverse=True)
    return out[:limit]


def guidance(project_id: int, title: str) -> dict:
    """Our own note first, then the vendor's — both clearly labelled, and
    neither ever reaches the customer's report."""
    out = {'note': '', 'vendor': ''}
    if not title:
        return out
    try:
        import services.asset_tree_service as ats
        n = ats.get_fault_note(project_id, title)
        if n:
            out['note'] = n.get('note') or ''
        ref = ats.get_fault_reference(title)
        if ref:
            out['vendor'] = (ref.get('cause') or '') + (
                ('\n' + ref['action']) if ref.get('action') else '')
    except Exception:                                    # noqa: BLE001
        pass
    return out


def needs_record(project_id: int, year: int = None, month: int = None,
                 limit: int = 100) -> List[dict]:
    """SCADA faults with no work record against them — the tab that used to
    live on Equipment."""
    try:
        import services.asset_tree_service as ats
        return ats.get_unreported_faults(project_id, year=year, month=month,
                                         limit=limit)
    except Exception:                                    # noqa: BLE001
        return []
