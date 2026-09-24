"""services/action_list_service.py — the action list, from the customer's
Excel sheet to a technician's phone and back.

These are **organisational** action items: "confirm the EPC purchased the 26
battery packs", "get the HVAC BOM and place the order", "how often must the
gas sensors be replaced?". They are kept in a spreadsheet with five columns —
No. · Topic · Description · Remarks / To do · Target Date — and until now they
were chased by hand.

What they are not, and this is the whole reason for a separate table: they are
not plant work. No block, no equipment, no hours. They must never reach the
customer's monthly report — not section 3.2, not the PM hours, not
availability — so they are deliberately kept out of `work_log_entries`, which
is the plant's work record and the very thing the report is built from.

Three things this module has to get right:

  * **Multi-line cells stay multi-line.** A cell reading "Checklists\\nPhotos\\n
    Report" is three lines of instruction, and flattening it loses the list.
  * **A re-import of the same sheet changes nothing that was already worked
    on.** The sheet is a living document: rows are edited, rows are added.
    An item is recognised by its number and its topic, updated where the file
    really differs, and an item that has gone from the file is left alone
    rather than deleted — somebody may have finished it this morning.
  * **The row the phone sent back wins over nothing else.** Completion carries
    its own stamp, like a checklist's (see routers/action_items.py).
"""
import datetime
import re
import uuid as _uuid
from typing import List, Optional

from database.db_manager import get_connection

# open → the office is waiting; in_progress → somebody has started;
# done → finished; dropped → decided against, kept for the record.
STATUSES = ('open', 'in progress', 'done', 'dropped')
OPEN_STATUSES = ('open', 'in progress')
DONE = 'done'


def _now() -> str:
    """UTC, in the server's own format. These stamps decide which side of a
    sync wrote last; a local one would read five hours ahead of the server
    here and always win."""
    return datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d %H:%M:%S')


def _today() -> str:
    return datetime.date.today().isoformat()


# ── Excel in ─────────────────────────────────────────────────────────────────

# Header text people actually use, mapped to our fields. Matching is on
# lowercase words, so "No.", "№" and "Item No" all land on `seq`.
_IMPORT_ALIASES = {
    'seq':         ('no', 'num', 'number', 'item', 'item no', '#', 'п п', 'номер'),
    'topic':       ('topic', 'subject', 'title', 'area', 'тема', 'предмет'),
    'description': ('description', 'detail', 'details', 'issue', 'описание'),
    'todo':        ('remarks to do', 'to do', 'todo', 'remarks', 'action',
                    'action required', 'next step', 'что сделать', 'действие'),
    'due_date':    ('target date', 'due date', 'due', 'deadline', 'date',
                    'срок', 'дата'),
    'assigned_name': ('owner', 'responsible', 'assignee', 'assigned to',
                      'исполнитель', 'ответственный'),
    'status':      ('status', 'state', 'статус'),
}


def _norm_header(h) -> str:
    return re.sub(r'[^a-zа-я0-9 ]+', ' ', str(h or '').strip().lower()).strip()


def _norm_topic(t) -> str:
    """A topic for matching, not for showing. Case, punctuation and runs of
    whitespace differ every time somebody retypes a line in Excel."""
    return re.sub(r'\s+', ' ', re.sub(r'[^\w\s]+', ' ',
                                      str(t or '').strip().lower())).strip()


def _text(v) -> str:
    """A cell as text, with its line breaks intact and nothing else added.

    pandas hands back NaN for an empty cell and float 3.0 for a number typed
    into a text column; both used to reach the table as "nan" and "3.0".
    """
    try:
        import pandas as pd
        if v is None or (not isinstance(v, str) and pd.isna(v)):
            return ''
    except (TypeError, ValueError):
        if v is None:
            return ''
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    s = str(v).replace('\r\n', '\n').replace('\r', '\n')
    # trailing blank lines only — the breaks inside the cell are the content
    return s.strip('\n \t')


def preview_excel(path: str, sheet=0) -> dict:
    """Read the sheet and guess which column is which, without writing
    anything. The caller shows the guess and lets the user correct it —
    the same contract as planner_service.preview_excel."""
    import pandas as pd
    df = pd.read_excel(path, sheet_name=sheet)
    df.columns = [str(c) for c in df.columns]
    guess = {}
    for col in df.columns:
        n = _norm_header(col)
        for field, aliases in _IMPORT_ALIASES.items():
            if field in guess.values():
                continue
            if n in aliases:
                guess[col] = field
                break
    # second pass: substring match for anything still unclaimed, so
    # "Remarks / To do" finds `todo` even though it is not an exact alias
    for col in df.columns:
        if col in guess:
            continue
        n = _norm_header(col)
        for field, aliases in _IMPORT_ALIASES.items():
            if field in guess.values():
                continue
            if any(a in n for a in aliases):
                guess[col] = field
                break
    return {'columns': list(df.columns), 'mapping': guess, 'rows': len(df),
            'preview': df.head(8).fillna('').astype(str).to_dict('records')}


def _date(v) -> str:
    """Target Date arrives as a real datetime from Excel; a person may also
    have typed it. Anything unreadable is left empty rather than guessed."""
    if v is None or v == '':
        return ''
    import pandas as pd
    try:
        ts = pd.to_datetime(v, errors='coerce')
    except Exception:                                    # noqa: BLE001
        return ''
    if ts is None or pd.isna(ts):
        return ''
    return ts.date().isoformat()


def _seq(v) -> Optional[int]:
    if v is None or v == '':
        return None
    import pandas as pd
    n = pd.to_numeric(v, errors='coerce')
    return None if pd.isna(n) else int(n)


def read_excel_rows(path: str, mapping: dict, sheet=0) -> List[dict]:
    """The sheet as our fields. Rows with neither a topic nor anything to do
    are spacer rows (the user's own file ends with three numbered blanks) and
    are dropped here rather than imported as empty items."""
    import pandas as pd
    df = pd.read_excel(path, sheet_name=sheet)
    df.columns = [str(c) for c in df.columns]
    inv = {v: k for k, v in (mapping or {}).items()}

    def cell(row, field):
        col = inv.get(field)
        if not col or col not in df.columns:
            return None
        v = row.get(col)
        return None if (not isinstance(v, str) and pd.isna(v)) else v

    out = []
    for n, row in df.iterrows():
        topic = _text(cell(row, 'topic'))
        desc = _text(cell(row, 'description'))
        todo = _text(cell(row, 'todo'))
        if not (topic or desc or todo):
            continue                          # numbered but empty: a spacer
        status = (_text(cell(row, 'status')) or '').strip().lower()
        out.append({
            'seq': _seq(cell(row, 'seq')) if inv.get('seq') else n + 1,
            'topic': topic, 'description': desc, 'todo': todo,
            'due_date': _date(cell(row, 'due_date')),
            'assigned_name': _text(cell(row, 'assigned_name')),
            'status': status if status in STATUSES else '',
            'excel_row': n + 2,               # 1-based, past the header
        })
    return out


# What an import is allowed to overwrite. Everything else on the row — who it
# was given to, whether it is done, the note the technician wrote — belongs to
# this app, never to the spreadsheet.
_IMPORT_FIELDS = ('seq', 'topic', 'description', 'todo', 'due_date')


def import_from_excel(project_id: int, path: str, mapping: dict, sheet=0,
                      log=print) -> dict:
    """Bring the action list in, or bring it in again.

    Matching, in order: the same number *and* the same topic, then the same
    topic on its own (the user inserted a row and every number below it
    shifted). Anything else is a new item. An item in the database that is no
    longer in the file is NOT deleted — it is counted as `untouched` and
    reported, because somebody may have been working on it all week.
    """
    rows = read_excel_rows(path, mapping, sheet=sheet)
    existing = items(project_id, include_done=True)
    by_pair, by_topic = {}, {}
    for it in existing:
        key = _norm_topic(it['topic'])
        by_pair[(it['seq'], key)] = it
        by_topic.setdefault(key, []).append(it)

    added, updated, unchanged, matched = 0, 0, 0, set()
    problems = []
    conn = get_connection()
    try:
        for r in rows:
            key = _norm_topic(r['topic'])
            hit = by_pair.get((r['seq'], key))
            if hit is None and len(by_topic.get(key, ())) == 1:
                hit = by_topic[key][0]
            if hit is not None and hit['id'] in matched:
                hit = None                    # the file repeats a topic
            if hit is None:
                conn.execute("""
                    INSERT INTO action_items
                        (uuid, project_id, seq, topic, description, todo,
                         due_date, assigned_name, status, source, source_ref,
                         sync_status, updated_at)
                    VALUES (?,?,?,?,?,?,?,?,?,'excel',?, 'local', ?)
                """, (str(_uuid.uuid4()), project_id, r['seq'], r['topic'],
                      r['description'], r['todo'], r['due_date'],
                      r['assigned_name'], r['status'] or 'open', path, _now()))
                added += 1
                continue
            matched.add(hit['id'])
            changed = {f: r[f] for f in _IMPORT_FIELDS
                       if (r[f] or '') != (hit.get(f) or '')
                       and not (f == 'seq' and r[f] is None)}
            if not changed:
                unchanged += 1
                continue
            sets = ', '.join(f'{f}=?' for f in changed)
            conn.execute(
                f"UPDATE action_items SET {sets}, source_ref=?, "
                f"sync_status='local', updated_at=? WHERE id=?",
                list(changed.values()) + [path, _now(), hit['id']])
            updated += 1
        conn.commit()
    finally:
        conn.close()

    untouched = [it for it in existing if it['id'] not in matched]
    log(f'Action list: {added} new, {updated} updated, {unchanged} unchanged, '
        f'{len(untouched)} already here but not in the file.')
    return {'added': added, 'updated': updated, 'unchanged': unchanged,
            'untouched': [it['topic'] for it in untouched],
            'rows': len(rows), 'problems': problems}


# ── Reading ──────────────────────────────────────────────────────────────────

def _row(r) -> dict:
    d = dict(r)
    d['overdue'] = bool(d.get('due_date')
                        and d['due_date'] < _today()
                        and (d.get('status') or '') in OPEN_STATUSES)
    return d


def items(project_id: int, status: str = None, assigned_to: str = None,
          include_done: bool = True, search: str = '') -> List[dict]:
    """The project's action items, soonest due first. Items with no date come
    last — a row nobody has dated is not more urgent than one due tomorrow."""
    q = ["SELECT * FROM action_items WHERE deleted_at IS NULL"]
    p = []
    if project_id is not None:
        q.append("AND project_id=?")
        p.append(project_id)
    if status:
        q.append("AND status=?")
        p.append(status)
    elif not include_done:
        q.append("AND status IN ('open','in progress')")
    if assigned_to:
        q.append("AND assigned_to=?")
        p.append(str(assigned_to))
    if search:
        q.append("AND (topic LIKE ? OR description LIKE ? OR todo LIKE ?)")
        p += ['%{}%'.format(search)] * 3
    q.append("ORDER BY CASE WHEN due_date='' THEN 1 ELSE 0 END, due_date, seq, id")
    conn = get_connection()
    try:
        return [_row(r) for r in conn.execute(' '.join(q), p)]
    finally:
        conn.close()


def get(uuid: str) -> Optional[dict]:
    conn = get_connection()
    try:
        r = conn.execute("SELECT * FROM action_items WHERE uuid=?",
                         (uuid,)).fetchone()
        return _row(r) if r else None
    finally:
        conn.close()


def due_soon(project_id: int, today: datetime.date = None,
             days: int = 7) -> List[dict]:
    """What the Today screen shows: open items already overdue or due within
    the next `days`. One query, so the count on the panel and the list its
    link opens can never drift apart."""
    today = today or datetime.date.today()
    horizon = (today + datetime.timedelta(days=days)).isoformat()
    return [it for it in items(project_id, include_done=False)
            if it.get('due_date') and it['due_date'] <= horizon]


# ── Writing ──────────────────────────────────────────────────────────────────

_FIELDS = ('seq', 'topic', 'description', 'todo', 'due_date', 'assigned_to',
           'assigned_name', 'status', 'done_at', 'done_note', 'done_by',
           'source', 'source_ref')


def save(project_id: int, uuid: str = None, **fields) -> str:
    """Add or change one item by hand. Returns its uuid."""
    vals = {k: v for k, v in fields.items() if k in _FIELDS}
    st = (vals.get('status') or '').strip().lower()
    if st and st not in STATUSES:
        raise ValueError(f"Unknown status {st!r} — one of {', '.join(STATUSES)}")
    if st:
        vals['status'] = st
    conn = get_connection()
    try:
        if uuid and conn.execute("SELECT 1 FROM action_items WHERE uuid=?",
                                 (uuid,)).fetchone():
            if vals:
                sets = ', '.join(f'{k}=?' for k in vals)
                conn.execute(
                    f"UPDATE action_items SET {sets}, sync_status='local', "
                    f"updated_at=? WHERE uuid=?",
                    list(vals.values()) + [_now(), uuid])
                conn.commit()
            return uuid
        uid = uuid or str(_uuid.uuid4())
        vals.setdefault('status', 'open')
        vals.setdefault('source', 'desktop')
        cols = ['uuid', 'project_id', 'sync_status', 'updated_at'] + list(vals)
        conn.execute(
            "INSERT INTO action_items ({}) VALUES ({})".format(
                ','.join(cols), ','.join('?' * len(cols))),
            [uid, project_id, 'local', _now()] + list(vals.values()))
        conn.commit()
        return uid
    finally:
        conn.close()


def mark_done(uuid: str, note: str = '', by: str = '', at: str = None,
              source: str = 'desktop') -> Optional[dict]:
    """Close an item. `at` is when it was really finished — a phone in a
    container finishes at 12:30 and uploads at 14:00."""
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE action_items SET status='done', done_at=?, done_note=?, "
            "done_by=?, source=?, sync_status='local', updated_at=? "
            "WHERE uuid=? AND deleted_at IS NULL",
            (at or _now(), note or '', by or '', source, _now(), uuid))
        conn.commit()
    finally:
        conn.close()
    return get(uuid)


def reopen(uuid: str) -> Optional[dict]:
    conn = get_connection()
    try:
        conn.execute("UPDATE action_items SET status='open', done_at='', "
                     "sync_status='local', updated_at=? WHERE uuid=?",
                     (_now(), uuid))
        conn.commit()
    finally:
        conn.close()
    return get(uuid)


def assign(uuid: str, user_id: str = '', user_name: str = '') -> Optional[dict]:
    """Give an item to a person. The name is cached with it, so a phone that
    has never seen the account list still shows who it is for.

    An item this database does not have is not invented: `save` would insert
    one with no project, and a row belonging to no plant is offered to the
    server for ever and shown nowhere.
    """
    if not get(uuid):
        return None
    save(None, uuid, assigned_to=str(user_id or ''),
         assigned_name=user_name or '')
    return get(uuid)


def delete(uuid: str) -> None:
    """Soft — the phones have to hear that it is gone, and the office may want
    to know what was dropped."""
    conn = get_connection()
    try:
        conn.execute("UPDATE action_items SET deleted_at=?, sync_status='local', "
                     "updated_at=? WHERE uuid=?", (_now(), _now(), uuid))
        conn.commit()
    finally:
        conn.close()


# ── Excel out ────────────────────────────────────────────────────────────────

def export_excel(project_id: int, path: str, include_done: bool = True) -> dict:
    """The same five columns the list arrived in, plus what we now know:
    the status, who did it and when. Multi-line cells are written back as
    multi-line cells."""
    import pandas as pd
    from openpyxl.styles import Alignment

    rows = []
    for it in items(project_id, include_done=include_done):
        rows.append({
            'No.': it.get('seq'),
            'Topic': it.get('topic') or '',
            'Description': it.get('description') or '',
            'Remarks / To do': it.get('todo') or '',
            'Target Date': it.get('due_date') or '',
            'Status': (it.get('status') or '').title(),
            'Assigned to': it.get('assigned_name') or '',
            'Done by': it.get('done_by') or '',
            'Done on': (it.get('done_at') or '')[:10],
            'Completion note': it.get('done_note') or '',
        })
    df = pd.DataFrame(rows, columns=[
        'No.', 'Topic', 'Description', 'Remarks / To do', 'Target Date',
        'Status', 'Assigned to', 'Done by', 'Done on', 'Completion note'])
    with pd.ExcelWriter(path, engine='openpyxl') as xl:
        df.to_excel(xl, sheet_name='Action list', index=False)
        ws = xl.sheets['Action list']
        for col, width in zip('ABCDEFGHIJ',
                              (6, 30, 46, 40, 13, 12, 16, 16, 12, 34)):
            ws.column_dimensions[col].width = width
        # wrap_text, or every \n in a Description reads as one long line
        for row in ws.iter_rows(min_row=1):
            for cell in row:
                cell.alignment = Alignment(vertical='top', wrap_text=True)
    return {'rows': len(df), 'path': path}


# ── sync ─────────────────────────────────────────────────────────────────────
# The desktop owns the list; the phone owns the completion of what it was
# given. Only this module writes the table — sync_client hands it what came
# off the wire.

def items_for_sync(project_id: int = None) -> List[dict]:
    """Items this desktop has not published yet, in the server's shape."""
    q = "SELECT * FROM action_items WHERE sync_status <> 'synced' AND uuid IS NOT NULL"
    p = []
    if project_id is not None:
        q += " AND project_id=?"
        p.append(project_id)
    conn = get_connection()
    try:
        rows = [dict(r) for r in conn.execute(q, p)]
    finally:
        conn.close()
    return [{'uuid': r['uuid'], 'project_id': r['project_id'],
             'seq': r['seq'], 'topic': r['topic'] or '',
             'description': r['description'] or '', 'todo': r['todo'] or '',
             'due_date': r['due_date'] or '',
             'assigned_to': r['assigned_to'] or '',
             'assigned_name': r['assigned_name'] or '',
             'status': r['status'] or 'open', 'done_at': r['done_at'] or '',
             'done_note': r['done_note'] or '', 'done_by': r['done_by'] or '',
             'updated_at': r['updated_at'] or _now(),
             'deleted_at': r['deleted_at']} for r in rows]


def mark_synced(uuid: str) -> None:
    conn = get_connection()
    try:
        conn.execute("UPDATE action_items SET sync_status='synced' WHERE uuid=?",
                     (uuid,))
        conn.commit()
    finally:
        conn.close()


def apply_remote(payload: dict) -> bool:
    """Take an item a phone finished. True when it was written here.

    Last writer by updated_at wins, so an item corrected in the office after
    the phone sent it keeps the office's version. The desktop owns the list,
    so a uuid this database has never seen belongs to another desktop and is
    ignored rather than invented.
    """
    uid = payload.get('uuid')
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT id, updated_at, status, done_note, done_by, done_at "
            "FROM action_items WHERE uuid=?", (uid,)).fetchone()
    finally:
        conn.close()
    if not row:
        return False
    theirs = str(payload.get('updated_at') or '')
    mine = str(row['updated_at'] or '')
    if mine and theirs and mine > theirs:
        return False
    same = all(str(payload.get(f) or '') == str(row[f] or '')
               for f in ('status', 'done_note', 'done_by', 'done_at'))
    if same:
        # Our own push, echoed back by the server: nothing to write, the item
        # is simply confirmed as published.
        mark_synced(uid)
        return False
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE action_items SET status=?, done_at=?, done_note=?, "
            "done_by=?, source='phone', sync_status='synced', updated_at=? "
            "WHERE uuid=?",
            (payload.get('status') or 'done', payload.get('done_at') or '',
             payload.get('done_note') or '', payload.get('done_by') or '',
             theirs or _now(), uid))
        conn.commit()
    finally:
        conn.close()
    return True
