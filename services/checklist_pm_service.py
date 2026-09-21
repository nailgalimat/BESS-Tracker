"""services/checklist_pm_service.py — PM checklists, from the customer's Excel
file to the filled file and back.

How the work really goes (the user's own words):

  * One PCS checklist and one BESS checklist **per block**, each covering all
    four units. A deviation on one unit is written in the comment
    ("BESS 3: door seal torn"), not as four sheets of paper.
  * A campaign leaves some items out — "we are not doing the RMU this time".
    The row stays in the exported file, empty, with whatever note the user
    wrote; nothing is invented for the customer.
  * The desktop can add items for a campaign; an added item goes at the end of
    its own group.
  * The result is OK / NOK / N/A. Measurements go in the comment. A signature
    is optional.
  * Filled checklists are exported later, on demand, into the customer's own
    workbook — see checklist_excel.

Storage: `checklist_templates` (the workbook, copied beside the database, plus
the row each item sits on), `checklist_runs` (one block's checklist in one
campaign) and `checklist_results` (one row per item). Runs carry a uuid, so a
phone and the desktop can name the same checklist.
"""
import datetime
import hashlib
import os
import shutil
import uuid as _uuid

from database.db_manager import DB_PATH, get_connection
from services import checklist_excel as cx

OK, NOK, NA, EXCLUDED, PENDING = cx.OK, cx.NOK, cx.NA, cx.EXCLUDED, cx.PENDING
RESULTS = (OK, NOK, NA, EXCLUDED, PENDING)


def _now() -> str:
    """UTC, in the server's own format. These stamps decide which side of a
    sync wrote last (see apply_remote_run); a local one would read five hours
    ahead of the server here and always win."""
    return datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d %H:%M:%S')


def templates_dir() -> str:
    """Where the customer's workbooks are kept — beside the database, so a
    rebuilt exe still exports into the file the customer issued."""
    d = os.path.join(os.path.dirname(os.path.abspath(DB_PATH)), 'checklist_templates')
    os.makedirs(d, exist_ok=True)
    return d


# ── templates ────────────────────────────────────────────────────────────────

def import_template(project_id: int, path: str, name: str = '',
                    kind: str = '') -> int:
    """Read a checklist workbook and keep it. Importing the same file again
    updates the items in place and keeps their ids: a result row points at an
    item id, so deleting and re-inserting the items orphaned every checklist
    already planned. An item is recognised by the row it sits on, else by its
    number and text; one that is gone from the workbook is dropped only when
    nothing was ever ticked against it, and items added here are kept."""
    parsed = cx.parse_template(path)
    name = name or os.path.splitext(os.path.basename(path))[0]
    kind = kind or _guess_kind(name, parsed)
    sha = hashlib.sha256(open(path, 'rb').read()).hexdigest()
    kept = os.path.join(templates_dir(), f"{sha[:12]}_{os.path.basename(path)}")
    if not os.path.exists(kept):
        shutil.copy2(path, kept)

    conn = get_connection()
    try:
        # (project, name) — never the name alone. Two customers issue a file
        # called "01) PCS Checklist", and looking one up by name let the
        # second site take over the first site's template: its workbook, its
        # items and the runs already filled against them.
        row = conn.execute(
            "SELECT id, uuid FROM checklist_templates WHERE name=? AND project_id=?",
            (name, project_id)).fetchone()
        if row is None:
            # Imported before templates knew their project: adopt it rather
            # than leaving the site with two copies of the same checklist.
            row = conn.execute(
                "SELECT id, uuid FROM checklist_templates "
                "WHERE name=? AND project_id IS NULL", (name,)).fetchone()
        if row:
            tid, uid = row['id'], row['uuid'] or str(_uuid.uuid4())
            conn.execute(
                "UPDATE checklist_templates SET uuid=?, project_id=?, kind=?, "
                "source_file=?, source_sha=?, progress_row=?, scope='block', "
                "updated_at=? WHERE id=?",
                (uid, project_id, kind, kept, sha, parsed['progress_row'], _now(), tid))
        else:
            cur = conn.execute(
                "INSERT INTO checklist_templates (uuid, name, description, "
                "container_type, scope, project_id, kind, source_file, source_sha, "
                "progress_row, updated_at) VALUES (?,?,?,?,'block',?,?,?,?,?,?)",
                (str(_uuid.uuid4()), name, '', kind, project_id, kind, kept, sha,
                 parsed['progress_row'], _now()))
            tid = cur.lastrowid

        old = [dict(r) for r in conn.execute(
            "SELECT * FROM checklist_items WHERE template_id=? ORDER BY order_num, id",
            (tid,))]
        by_row, by_text = {}, {}
        for it in old:
            if it['added']:
                continue
            if it['excel_row']:
                by_row.setdefault(it['excel_row'], it)
            by_text.setdefault((str(it['s_no'] or ''),
                                (it['description'] or '').strip()), it)

        # Two passes, and in this order: everything that still reads the same
        # keeps its id, and only then is a leftover row allowed to claim one.
        # A row inserted near the top moves every item below it down one, so a
        # row-first match would hand each item the results of the one above it;
        # and the row pass must not take an item that a later line matches by
        # its text, or the same slip happens one item further down.
        matched, claim = set(), []
        for item in parsed['items']:
            hit = by_text.get((str(item['no'] or ''), item['text'].strip()))
            if hit is not None and hit['id'] not in matched:
                matched.add(hit['id'])
                claim.append(hit['id'])
            else:
                claim.append(None)
        for n, item in enumerate(parsed['items']):
            if claim[n] is not None:
                continue
            hit = by_row.get(item['excel_row'])      # the wording was corrected
            if hit is not None and hit['id'] not in matched:
                matched.add(hit['id'])
                claim[n] = hit['id']

        ordered, equipment = [], []
        for n, item in enumerate(parsed['items']):
            if claim[n] is not None:
                conn.execute(
                    "UPDATE checklist_items SET category=?, description=?, s_no=?, "
                    "equipment=?, excel_row=? WHERE id=?",
                    (item['activity'], item['text'], item['no'], item['equipment'],
                     item['excel_row'], claim[n]))
                iid = claim[n]
            else:
                cur = conn.execute(
                    "INSERT INTO checklist_items (template_id, order_num, category, "
                    "description, expected, s_no, equipment, excel_row, added) "
                    "VALUES (?,0,?,?,'',?,?,?,0)",
                    (tid, item['activity'], item['text'], item['no'],
                     item['equipment'], item['excel_row']))
                iid = cur.lastrowid
                # A checklist already planned gets the customer's new item too,
                # pending — it is on the phone's screen either way.
                conn.execute(
                    "INSERT INTO checklist_results (run_id, item_id, result, comment) "
                    "SELECT id, ?, ?, '' FROM checklist_runs "
                    "WHERE template_id=? AND deleted_at IS NULL",
                    (iid, PENDING, tid))
            ordered.append(iid)
            equipment.append(item['equipment'] or '')

        # An item the customer removed from the workbook goes only if nobody
        # ticked it; otherwise it stays, so the exported file still explains
        # what was done. Items added here are kept and put back at the end of
        # their group, which is where they are written into the file.
        for it in old:
            if it['id'] in matched:
                continue
            group = it['equipment'] or ''
            if not it['added']:
                used = conn.execute(
                    "SELECT 1 FROM checklist_results WHERE item_id=? "
                    "AND result <> '' LIMIT 1", (it['id'],)).fetchone()
                if not used:
                    conn.execute("DELETE FROM checklist_results WHERE item_id=?",
                                 (it['id'],))
                    conn.execute("DELETE FROM checklist_items WHERE id=?", (it['id'],))
                    continue
            at = len(ordered)
            for i in range(len(equipment) - 1, -1, -1):
                if equipment[i] == group:
                    at = i + 1
                    break
            ordered.insert(at, it['id'])
            equipment.insert(at, group)

        for n, iid in enumerate(ordered):
            conn.execute("UPDATE checklist_items SET order_num=? WHERE id=?", (n, iid))
        conn.commit()
        return tid
    finally:
        conn.close()


def _touch_template(conn, template_id: int):
    """The template changed: say so, and queue the checklists already planned
    against it so the change reaches the phones.

    sync_client only uploads the items when the newest template stamp has
    moved, so an item added after a campaign was planned sat on the desktop
    and nobody on site ever saw it. The runs' own `updated_at` is deliberately
    NOT touched — a checklist a phone has already filled must not lose its
    answers to a republish that only added a line.
    """
    conn.execute("UPDATE checklist_templates SET updated_at=? WHERE id=?",
                 (_now(), template_id))
    conn.execute("UPDATE checklist_runs SET sync_status='pending' "
                 "WHERE template_id=? AND deleted_at IS NULL "
                 "AND sync_status='synced'", (template_id,))


def _guess_kind(name: str, parsed: dict) -> str:
    text = (name + ' ' + ' '.join(i['equipment'] for i in parsed['items'][:3])).lower()
    if 'pcs' in text:
        return 'PCS'
    if 'bess' in text or 'battery' in text:
        return 'BESS'
    return ''


def templates(project_id: int = None) -> list:
    conn = get_connection()
    try:
        q = ("SELECT t.*, (SELECT COUNT(*) FROM checklist_items i "
             "WHERE i.template_id=t.id) AS item_count FROM checklist_templates t "
             "WHERE t.source_file IS NOT NULL AND t.source_file <> ''")
        p = []
        if project_id is not None:
            q += " AND (t.project_id = ? OR t.project_id IS NULL)"
            p.append(project_id)
        return [dict(r) for r in conn.execute(q + " ORDER BY t.kind, t.name", p)]
    finally:
        conn.close()


def items(template_id: int) -> list:
    conn = get_connection()
    try:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM checklist_items WHERE template_id=? ORDER BY order_num, id",
            (template_id,))]
    finally:
        conn.close()


def add_item(template_id: int, text: str, equipment: str = '',
             activity: str = '', after_item_id: int = None) -> int:
    """An item the desktop adds for a campaign. It goes at the end of its own
    group, which is where it will be written into the customer's file."""
    its = items(template_id)
    group = equipment or (its[-1]['equipment'] if its else '')
    same = [i for i in its if (i['equipment'] or '') == group] or its
    anchor = next((i for i in its if i['id'] == after_item_id), None) or (same[-1] if same else None)
    order = (anchor['order_num'] + 1) if anchor else len(its)
    conn = get_connection()
    try:
        conn.execute("UPDATE checklist_items SET order_num = order_num + 1 "
                     "WHERE template_id=? AND order_num >= ?", (template_id, order))
        cur = conn.execute(
            "INSERT INTO checklist_items (template_id, order_num, category, "
            "description, expected, s_no, equipment, excel_row, added) "
            "VALUES (?,?,?,?,'',?,?,?,1)",
            (template_id, order, activity or (anchor['category'] if anchor else ''),
             text, f"{anchor['s_no']}a" if anchor else '', group,
             anchor['excel_row'] if anchor else None))
        item_id = cur.lastrowid
        # Checklists already planned get the new item too, pending — otherwise
        # it is on the phone's screen but outside its progress count.
        conn.execute(
            "INSERT INTO checklist_results (run_id, item_id, result, comment) "
            "SELECT id, ?, ?, '' FROM checklist_runs "
            "WHERE template_id=? AND deleted_at IS NULL",
            (item_id, PENDING, template_id))
        _touch_template(conn, template_id)
        conn.commit()
        return item_id
    finally:
        conn.close()


def delete_item(item_id: int):
    """Only an added item can be deleted — a customer's own item is excluded,
    never removed, or the exported file would no longer be their document."""
    conn = get_connection()
    try:
        row = conn.execute("SELECT added, template_id FROM checklist_items "
                           "WHERE id=?", (item_id,)).fetchone()
        if not row or not row['added']:
            raise ValueError("An item of the customer's checklist cannot be "
                             "deleted — exclude it instead; the row stays in "
                             "the file with your note.")
        conn.execute("DELETE FROM checklist_results WHERE item_id=?", (item_id,))
        conn.execute("DELETE FROM checklist_items WHERE id=?", (item_id,))
        _touch_template(conn, row['template_id'])
        conn.commit()
    finally:
        conn.close()


# ── runs ─────────────────────────────────────────────────────────────────────

def plan_runs(project_id: int, template_id: int, blocks, run_date: str,
              campaign: str = '', excluded_item_ids=(), note: str = '',
              excluded_notes: dict = None) -> list:
    """One checklist per block for a campaign, ready for the phones. Returns
    the run uuids. A block already planned for this template and campaign is
    not planned twice.

    An excluded item keeps its row in the exported file with the note the user
    wrote for it (`excluded_notes` = {item_id: note}) — nothing is invented for
    the customer, so an item left out with no note goes out empty."""
    from services.project_service import plant_block_to_zone
    excluded = set(excluded_item_ids or ())
    notes = {int(k): v for k, v in (excluded_notes or {}).items()}
    its = items(template_id)
    out = []
    conn = get_connection()
    try:
        for blk in blocks:
            blk = int(blk)
            exists = conn.execute(
                "SELECT uuid FROM checklist_runs WHERE project_id=? AND template_id=? "
                "AND plant_block=? AND campaign=? AND deleted_at IS NULL",
                (project_id, template_id, blk, campaign)).fetchone()
            if exists:
                out.append(exists['uuid'])
                continue
            # plant_block is the real one. zone_number / block_number are the
            # legacy NOT NULL columns of this table: they carry the per-zone
            # numbering when the project has a container map, and 0 when it has
            # none — never a guess, which would name the wrong block.
            z, b = plant_block_to_zone(project_id, blk)
            uid = str(_uuid.uuid4())
            cur = conn.execute(
                "INSERT INTO checklist_runs (uuid, template_id, project_id, "
                "zone_number, block_number, plant_block, run_date, campaign, "
                "status, notes, source, sync_status, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,'In Progress',?,'desktop','local',?)",
                (uid, template_id, project_id, z or 0, b or 0, blk, run_date,
                 campaign, note, _now()))
            run_id = cur.lastrowid
            for i in its:
                out_of_scope = i['id'] in excluded
                conn.execute(
                    "INSERT INTO checklist_results (run_id, item_id, result, comment) "
                    "VALUES (?,?,?,?)",
                    (run_id, i['id'], EXCLUDED if out_of_scope else PENDING,
                     notes.get(i['id'], '') if out_of_scope else ''))
            out.append(uid)
        conn.commit()
        return out
    finally:
        conn.close()


def runs(project_id: int, campaign: str = None, block: int = None,
         include_done: bool = True) -> list:
    conn = get_connection()
    try:
        q = ("SELECT r.*, t.name AS template_name, t.kind, "
             "(SELECT COUNT(*) FROM checklist_results x WHERE x.run_id=r.id) AS n_items, "
             "(SELECT COUNT(*) FROM checklist_results x WHERE x.run_id=r.id "
             "   AND x.result IN ('OK','NOK','N/A')) AS n_done, "
             "(SELECT COUNT(*) FROM checklist_results x WHERE x.run_id=r.id "
             "   AND x.result='NOK') AS n_nok "
             "FROM checklist_runs r JOIN checklist_templates t ON t.id=r.template_id "
             # the archived commissioning checklists share these tables; a PM
             # checklist is one that came out of a customer workbook
             "WHERE t.source_file IS NOT NULL AND t.source_file <> '' "
             "AND r.project_id=? AND r.deleted_at IS NULL")
        p = [project_id]
        if campaign is not None:
            q += " AND r.campaign=?"; p.append(campaign)
        if block is not None:
            q += " AND r.plant_block=?"; p.append(int(block))
        if not include_done:
            q += " AND r.status <> 'Done'"
        return [dict(r) for r in conn.execute(
            q + " ORDER BY r.run_date DESC, r.plant_block", p)]
    finally:
        conn.close()


def run_detail(run_uuid: str) -> dict:
    """The run with every item and its result, in checklist order."""
    conn = get_connection()
    try:
        r = conn.execute(
            "SELECT r.*, t.name AS template_name, t.kind, t.source_file "
            "FROM checklist_runs r JOIN checklist_templates t ON t.id=r.template_id "
            "WHERE r.uuid=?", (run_uuid,)).fetchone()
        if not r:
            return None
        run = dict(r)
        run['items'] = [dict(x) for x in conn.execute(
            "SELECT i.id AS item_id, i.s_no, i.equipment, i.category AS activity, "
            "i.description AS text, i.excel_row, i.added, i.order_num, "
            "COALESCE(res.result,'') AS result, COALESCE(res.comment,'') AS comment "
            "FROM checklist_items i LEFT JOIN checklist_results res "
            "  ON res.item_id=i.id AND res.run_id=? "
            "WHERE i.template_id=? ORDER BY i.order_num, i.id",
            (run['id'], run['template_id']))]
        return run
    finally:
        conn.close()


def save_results(run_uuid: str, results: dict, status: str = None,
                 signed_by: str = None, ptw_no: str = None, serial: str = None,
                 notes: str = None, source: str = None) -> dict:
    """Write what was ticked. `results` is {item_id: {'result','comment'}}.
    Only the items passed are touched, so a phone sending half a checklist
    does not blank the rest."""
    conn = get_connection()
    try:
        r = conn.execute("SELECT id, template_id FROM checklist_runs WHERE uuid=?",
                         (run_uuid,)).fetchone()
        if not r:
            raise ValueError(f"No checklist run {run_uuid}")
        run_id = r['id']
        for item_id, val in (results or {}).items():
            res = (val or {}).get('result', PENDING)
            if res not in RESULTS:
                raise ValueError(f"Unknown result {res!r} — use OK, NOK, N/A or Excluded")
            comment = (val or {}).get('comment', '') or ''
            done = conn.execute(
                "UPDATE checklist_results SET result=?, comment=? "
                "WHERE run_id=? AND item_id=?", (res, comment, run_id, int(item_id)))
            if done.rowcount == 0:
                # No row yet — an item added after this run was planned. An id
                # from another template is a phone working off an old copy;
                # ignore it rather than fail the whole save.
                own = conn.execute(
                    "SELECT 1 FROM checklist_items WHERE id=? AND template_id=?",
                    (int(item_id), r['template_id'])).fetchone()
                if not own:
                    continue
                conn.execute("INSERT INTO checklist_results (run_id, item_id, "
                             "result, comment) VALUES (?,?,?,?)",
                             (run_id, int(item_id), res, comment))
        sets, vals = ["updated_at=?"], [_now()]
        for col, v in (('status', status), ('signed_by', signed_by),
                       ('ptw_no', ptw_no), ('serial', serial), ('notes', notes),
                       ('source', source)):
            if v is not None:
                sets.append(f"{col}=?"); vals.append(v)
        sets.append("sync_status = CASE WHEN sync_status='local' THEN 'local' "
                    "ELSE 'pending' END")
        conn.execute(f"UPDATE checklist_runs SET {', '.join(sets)} WHERE id=?",
                     vals + [run_id])
        conn.commit()
    finally:
        conn.close()
    return progress(run_uuid)


def progress(run_uuid: str) -> dict:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS n, "
            "SUM(result IN ('OK','NOK','N/A')) AS done, "
            "SUM(result='NOK') AS nok, SUM(result='Excluded') AS excluded "
            "FROM checklist_results res JOIN checklist_runs r ON r.id=res.run_id "
            "WHERE r.uuid=?", (run_uuid,)).fetchone()
        return {'items': row['n'] or 0, 'done': row['done'] or 0,
                'nok': row['nok'] or 0, 'excluded': row['excluded'] or 0}
    finally:
        conn.close()


def delete_run(run_uuid: str):
    conn = get_connection()
    try:
        conn.execute("UPDATE checklist_runs SET deleted_at=?, updated_at=?, "
                     "sync_status = CASE WHEN sync_status='local' THEN 'local' "
                     "ELSE 'pending' END WHERE uuid=?", (_now(), _now(), run_uuid))
        conn.commit()
    finally:
        conn.close()


def campaigns(project_id: int) -> list:
    """The project's campaigns, newest first, with how far each one is."""
    conn = get_connection()
    try:
        return [dict(r) for r in conn.execute(
            "SELECT r.campaign, MIN(r.run_date) AS date_from, "
            "MAX(r.run_date) AS date_to, COUNT(*) AS n_runs, "
            "SUM(r.status='Done') AS n_done "
            "FROM checklist_runs r JOIN checklist_templates t ON t.id=r.template_id "
            "WHERE t.source_file IS NOT NULL AND t.source_file <> '' "
            "AND r.project_id=? AND r.deleted_at IS NULL "
            "GROUP BY r.campaign ORDER BY date_from DESC, r.campaign", (project_id,))]
    finally:
        conn.close()


# ── sync ─────────────────────────────────────────────────────────────────────
# The desktop owns templates and planned runs; the phone fills them in. Only
# this module writes the tables — sync_client hands it what came off the wire.

def templates_for_sync(project_id: int = None) -> list:
    """Templates as the server stores them: the item list travels with the
    template, so a phone offline since yesterday still has the text."""
    out = []
    for t in templates(project_id):
        out.append({
            'uuid': t['uuid'], 'project_id': t['project_id'], 'name': t['name'],
            'kind': t['kind'] or '',
            'items': [{'item_id': i['id'], 's_no': i['s_no'] or '',
                       'equipment': i['equipment'] or '',
                       'activity': i['category'] or '',
                       'text': i['description'] or '',
                       'added': int(i['added'] or 0)}
                      for i in items(t['id'])],
            'updated_at': t['updated_at'] or _now(),
        })
    return out


def runs_for_sync(project_id: int = None) -> list:
    """Runs this desktop has not published yet, in the server's shape."""
    conn = get_connection()
    try:
        q = ("SELECT r.*, t.uuid AS template_uuid FROM checklist_runs r "
             "JOIN checklist_templates t ON t.id=r.template_id "
             "WHERE r.sync_status <> 'synced'")
        p = []
        if project_id is not None:
            q += " AND r.project_id=?"
            p.append(project_id)
        rows = [dict(r) for r in conn.execute(q, p)]
        for r in rows:
            r['results'] = {str(x['item_id']): {'result': x['result'] or '',
                                                'comment': x['comment'] or ''}
                            for x in conn.execute(
                                "SELECT item_id, result, comment FROM checklist_results "
                                "WHERE run_id=?", (r['id'],))}
    finally:
        conn.close()
    return [{'uuid': r['uuid'], 'project_id': r['project_id'],
             'template_uuid': r['template_uuid'], 'plant_block': r['plant_block'],
             'campaign': r['campaign'] or '', 'run_date': r['run_date'],
             'status': r['status'] or '', 'results': r['results'],
             'ptw_no': r['ptw_no'] or '', 'serial': r['serial'] or '',
             'filled_by': r['signed_by'] or '', 'notes': r['notes'] or '',
             'updated_at': r['updated_at'] or _now(),
             'deleted_at': r['deleted_at']} for r in rows]


def mark_run_synced(run_uuid: str):
    conn = get_connection()
    try:
        conn.execute("UPDATE checklist_runs SET sync_status='synced' WHERE uuid=?",
                     (run_uuid,))
        conn.commit()
    finally:
        conn.close()


def apply_remote_run(payload: dict) -> bool:
    """Take a checklist filled on a phone. True when it was written here.

    Last writer by updated_at wins: a run corrected on the desktop after the
    phone sent its copy keeps the desktop's answers, and a phone that filled it
    later overwrites them. The desktop plans the runs, so a uuid this database
    has never seen belongs to another desktop and is ignored rather than
    invented.
    """
    uid = payload.get('uuid')
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT updated_at, sync_status FROM checklist_runs WHERE uuid=?",
            (uid,)).fetchone()
    finally:
        conn.close()
    if not row:
        return False
    theirs = str(payload.get('updated_at') or '')
    mine = str(row['updated_at'] or '')
    if mine and theirs and mine > theirs:
        return False
    results = {}
    for k, v in (payload.get('results') or {}).items():
        try:
            results[int(k)] = {'result': (v or {}).get('result', PENDING),
                               'comment': (v or {}).get('comment', '') or ''}
        except (TypeError, ValueError):
            continue
    run = run_detail(uid) or {}
    here = {i['item_id']: (i['result'], i['comment']) for i in run.get('items', [])}
    same = all(here.get(i) == (v['result'], v['comment']) for i, v in results.items())
    for field, col in (('status', 'status'), ('ptw_no', 'ptw_no'),
                       ('serial', 'serial'), ('filled_by', 'signed_by')):
        if payload.get(field) and payload[field] != (run.get(col) or ''):
            same = False
    if same:
        # Our own push, echoed back by the server. Nothing to write; the run is
        # simply confirmed as published.
        mark_run_synced(uid)
        return False
    save_results(uid, results, status=payload.get('status') or None,
                 signed_by=payload.get('filled_by') or None,
                 ptw_no=payload.get('ptw_no') or None,
                 serial=payload.get('serial') or None, source='phone')
    mark_run_synced(uid)
    return True


# ── export ───────────────────────────────────────────────────────────────────

def export_run(run_uuid: str, out_dir: str, plant_name: str = '') -> str:
    """Write the filled checklist as a copy of the customer's own workbook."""
    run = run_detail(run_uuid)
    if not run:
        raise ValueError(f"No checklist run {run_uuid}")
    src = run['source_file']
    if not (src and os.path.isfile(src)):
        raise FileNotFoundError(
            "The workbook this checklist was imported from is missing — "
            "import the customer's file again.")

    results, added = {}, []
    for it in run['items']:
        if it['added']:
            added.append({'after_row': it['excel_row'], 'no': it['s_no'],
                          'equipment': it['equipment'], 'text': it['text'],
                          'result': it['result'], 'comment': it['comment']})
        elif it['excel_row']:
            results[it['excel_row']] = {'result': it['result'],
                                        'comment': it['comment']}
    node = f"Block {run['plant_block']}" if run['plant_block'] else ''
    header = {'plant': plant_name, 'date': _fmt_date(run['run_date']),
              'location': node, 'serial': run['serial'] or '',
              'name': run['signed_by'] or run['engineer'] or ''}
    name = (f"{run['kind'] or run['template_name']} Block {run['plant_block']} "
            f"{run['run_date']}.xlsx").replace('/', '-')
    return cx.write_filled(src, os.path.join(out_dir, name), header, results, added)


def export_campaign(project_id: int, campaign: str, out_dir: str,
                    plant_name: str = '') -> list:
    """Every filled checklist of a campaign, one file per block."""
    out = []
    for r in runs(project_id, campaign=campaign):
        try:
            out.append(export_run(r['uuid'], out_dir, plant_name))
        except Exception as ex:                          # noqa: BLE001
            out.append(f"! Block {r['plant_block']}: {ex}")
    return out


def _fmt_date(d: str) -> str:
    try:
        return datetime.date.fromisoformat((d or '')[:10]).strftime('%d.%m.%Y')
    except ValueError:
        return d or ''
