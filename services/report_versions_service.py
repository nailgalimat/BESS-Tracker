"""services/report_versions_service.py — report versions, their manifests, sent and locked months

Each generation of a monthly report is a new version: `<Project>_LTSA_<YYYY-MM>_vN.docx`
in report_data/p<project>/<YYYY-MM>/versions/, never written over, with a
manifest beside it that says exactly what the numbers were made from:

  input_files          type, name and SHA-256 of every data set file used
  report_inputs        exclusions, manual downtime, PM as downtime, balancing,
                       capacities — exactly what the generator received
  pm_records           the month's PM records as stored (traceability)
  text                 report number, 3.2 lines, recommendations, safety, ...
  history_records      the KPI history the cycles-in-year figure was built on
  alarm_classifications  SHA-256 of the classification CSV
  app                  Python / pandas / openpyxl, git commit or exe date, and a
                       fingerprint of the report code
  confirmations        the open questions the user generated over
  key_numbers          availability, RTE, cycles (month / year / total), SOC,
                       SOH, row counts of 3.1, 3.2, 4.4.1, 5.2

The generated DOCX and its manifest are made read-only. The report that goes
to the customer is the Word-edited file: `mark_sent` stores a copy of it and
its SHA-256 with the version it was made from, and locks the month
(report_workflow_service.assert_month_open). `unlock_month` needs a reason; all
three are logged in report_month_log.

The manifest is internal — it never leaves the app (see internal_vs_customer_data).
"""

import copy
import datetime
import hashlib
import importlib
import inspect
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import time
import types
from typing import List, Optional

import pandas as pd

from database.db_manager import get_connection
import services.parse_cache as pc
import services.month_dataset_service as mds
import services.report_workflow_service as rw

MANIFEST_FORMAT = 1
TEXT_KEYS = ('site_name', 'project_details', 'report_number', 'prepared_by', 'reviewed_by',
             'cm_activities', 'site_visits', 'recommendations', 'planned_next_period',
             'safety_incidents', 'breakdown_incidents')
REPORT_INPUT_KEYS = ('plant_capacity_mw', 'per_block_capacity_mw',
                     'contractual_plant_capacity_mw', 'redundancy_threshold_pct',
                     'yearly_cycle_target', 'pm_activities', 'exclusions',
                     'manual_unavailability', 'balancing_periods')
NUMBERS = (('availability_pct', 'Availability', '{:.2f} %'), ('rte_pct', 'RTE', '{:.2f} %'),
           ('cycles_month', 'Cycles month', '{:.1f}'), ('cycles_in_year', 'Cycles year', '{:.1f}'),
           ('cycles_lifetime', 'Cycles total', '{:.1f}'), ('avg_soc_pct', 'SOC', '{:.1f} %'),
           ('avg_soh_pct', 'SOH', '{:.2f} %'))
ROWS = ('3.1', '3.2', '4.4.1', '5.2')
# modules whose code decides the report's numbers and tables
REPORT_CODE = ('services.tashkent_report_service', 'services.bukhara_report_service',
               'services.availability_service', 'services.report_workflow_service',
               'services.asset_tree_service', 'services.docx_renderer')


# ── build information ────────────────────────────────────────────────────────

def _stable(o) -> str:
    """repr that does not depend on hash ordering (sets) or memory addresses."""
    if isinstance(o, types.CodeType):
        return 'code:' + _code_hash(o)
    if isinstance(o, (set, frozenset)):
        return '{' + ','.join(sorted(_stable(x) for x in o)) + '}'
    if isinstance(o, (tuple, list)):
        return '(' + ','.join(_stable(x) for x in o) + ')'
    if isinstance(o, dict):
        return '{' + ','.join(sorted(_stable(k) + ':' + _stable(v) for k, v in o.items())) + '}'
    if isinstance(o, (str, bytes, int, float, bool, complex, type(None))):
        return repr(o)
    return '<' + type(o).__name__ + '>'


def _code_hash(co) -> str:
    h = hashlib.sha256(co.co_code)
    h.update(repr((co.co_names, co.co_varnames)).encode('utf-8', 'replace'))
    for c in co.co_consts:
        h.update(_stable(c).encode('utf-8', 'replace'))
    return h.hexdigest()


def code_fingerprint() -> str:
    """A hash of the report code's compiled functions and plain constants —
    the same in a source run and the exe built from that source, different
    after any change to how the report is made. Line numbers do not count."""
    h = hashlib.sha256()
    for name in REPORT_CODE:
        mod = importlib.import_module(name)
        for attr in sorted(vars(mod)):
            obj = vars(mod)[attr]
            if isinstance(obj, types.FunctionType) and obj.__module__ == name:
                h.update(f'{name}.{attr}='.encode() + _code_hash(obj.__code__).encode())
            # public UPPER_CASE constants (pattern lists, thresholds); private
            # module state and the DEFAULT_* file locations are left out
            elif isinstance(obj, (str, int, float, tuple, list, dict, set, frozenset)) \
                    and attr.isupper() and not attr.startswith('_') \
                    and not attr.startswith('DEFAULT_'):
                h.update(f'{name}.{attr}='.encode() + _stable(obj).encode('utf-8', 'replace'))
    return h.hexdigest()[:16]


def app_info() -> dict:
    import openpyxl
    info = {'python': sys.version.split()[0], 'pandas': pd.__version__,
            'openpyxl': openpyxl.__version__, 'frozen': bool(getattr(sys, 'frozen', False))}
    try:
        import docx
        info['python_docx'] = getattr(docx, '__version__', '')
    except Exception:                                       # noqa: BLE001
        pass
    if info['frozen']:
        info['exe'] = os.path.basename(sys.executable)
        try:
            info['exe_modified'] = datetime.datetime.fromtimestamp(
                os.path.getmtime(sys.executable)).isoformat(timespec='seconds')
        except OSError:
            pass
    else:
        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
        try:
            info['commit'] = subprocess.run(
                ['git', 'rev-parse', 'HEAD'], cwd=repo, capture_output=True, text=True,
                timeout=10, creationflags=flags).stdout.strip()
            dirty = subprocess.run(
                ['git', 'status', '--porcelain', '--untracked-files=no'], cwd=repo,
                capture_output=True, text=True, timeout=10, creationflags=flags).stdout.strip()
            info['uncommitted_changes'] = bool(dirty)
        except Exception:                                   # noqa: BLE001
            pass
    try:
        info['code_fingerprint'] = code_fingerprint()
    except Exception as e:                                  # noqa: BLE001
        info['code_fingerprint'] = f'unavailable: {e}'
    return info


# ── versions ─────────────────────────────────────────────────────────────────

def _json(o) -> str:
    return json.dumps(o, sort_keys=True, default=str, ensure_ascii=False)


def _sha_text(o) -> str:
    return hashlib.sha256(_json(o).encode('utf-8')).hexdigest()


def _row(r) -> dict:
    d = dict(r)
    try:
        d['summary'] = json.loads(d.get('summary') or '{}')
    except ValueError:
        d['summary'] = {}
    d['docx_abs'] = mds.absolute(d['docx_path'])
    d['manifest_abs'] = mds.absolute(d['manifest_path']) if d.get('manifest_path') else ''
    d['sent_abs'] = mds.absolute(d['sent_docx_path']) if d.get('sent_docx_path') else ''
    return d


def list_versions(project_id: int, year: int, month: int) -> List[dict]:
    """Versions of a month, newest first, each with `changes` — what moved
    in the key numbers and row counts since the version before."""
    conn = get_connection()
    try:
        rows = [_row(r) for r in conn.execute(
            "SELECT * FROM report_versions WHERE project_id=? AND year=? AND month=? "
            "ORDER BY version", (project_id, year, month)).fetchall()]
    finally:
        conn.close()
    prev = None
    for r in rows:
        r['changes'] = describe_changes(prev, r)
        prev = r
    return list(reversed(rows))


def get_version(version_id: int) -> Optional[dict]:
    conn = get_connection()
    try:
        r = conn.execute("SELECT * FROM report_versions WHERE id=?", (int(version_id),)).fetchone()
    finally:
        conn.close()
    return _row(r) if r else None


def describe_changes(prev: Optional[dict], cur: dict) -> str:
    if prev is None:
        return 'first version'
    a, b = prev['summary'], cur['summary']
    bits = []
    for key, label, fmt in NUMBERS:
        x, y = a.get(key), b.get(key)
        fx = fmt.format(x) if x is not None else '—'
        fy = fmt.format(y) if y is not None else '—'
        if fx != fy:
            bits.append(f'{label} {fx} → {fy}')
    ra, rb = a.get('rows') or {}, b.get('rows') or {}
    for k in ROWS:
        if ra.get(k) != rb.get(k):
            bits.append(f'{k} rows {ra.get(k, "—")} → {rb.get(k, "—")}')
    notes = []
    if a.get('inputs_sha') != b.get('inputs_sha'):
        notes.append('availability inputs changed')
    if a.get('files_sha') != b.get('files_sha'):
        notes.append('data files changed')
    if a.get('text_sha') != b.get('text_sha'):
        notes.append('customer text changed')
    if a.get('code') != b.get('code'):
        notes.append('made by different report code')
    if not bits:
        bits.append(f"No number changes vs v{prev['version']}")
    return ' · '.join(bits + notes)


def next_version(project_id: int, year: int, month: int) -> int:
    conn = get_connection()
    try:
        r = conn.execute("SELECT MAX(version) FROM report_versions WHERE project_id=? "
                         "AND year=? AND month=?", (project_id, year, month)).fetchone()
        return int(r[0] or 0) + 1
    finally:
        conn.close()


def docx_name(project_name: str, year: int, month: int, n: int) -> str:
    safe = re.sub(r'[^\w\-]+', '_', project_name or 'Project').strip('_') or 'Project'
    return f'{safe}_LTSA_{year:04d}-{month:02d}_v{n}.docx'


def _read_only(path):
    try:
        os.chmod(path, stat.S_IREAD | stat.S_IRGRP | stat.S_IROTH)
    except OSError:
        pass


def generate_version(project_id: int, year: int, month: int, params: dict,
                     confirmations: Optional[List[dict]] = None, note: str = '',
                     progress=None) -> dict:
    """Generate the month's report from its data set as the next version.

    `params` are the generator arguments that come from the database and the
    page (report_workflow_service.report_inputs and the customer text — see
    MonthlyReportsPage._report_params); the SCADA file arguments come from the
    data set. Raises when a required file is missing or a stored copy no longer
    matches its hash; nothing is written then. Returns the version row."""
    log = progress or (lambda m: None)
    site = mds.site_type(project_id)
    missing = mds.missing_required(project_id, year, month)
    if missing:
        raise ValueError('Files missing from the data set: ' + ', '.join(missing))
    files = []
    for f in mds.list_files(project_id, year, month):
        if not os.path.isfile(f['abs_path']):
            raise FileNotFoundError(f"{f['label']}: stored copy missing ({f['stored_path']})")
        if pc.file_sha256(f['abs_path']) != f['sha256']:
            raise RuntimeError(f"{f['label']}: the stored copy changed on disk since it was added "
                               f"({f['stored_path']}) — add the file again")
        files.append(f)
    paths = mds.generation_paths(project_id, year, month)

    proj = rw.get_project(project_id) or {}
    vdir = os.path.join(mds.month_dir(project_id, year, month), 'versions')
    os.makedirs(vdir, exist_ok=True)
    n = next_version(project_id, year, month)
    while True:                       # never write over a file that is there
        docx = os.path.join(vdir, docx_name(proj.get('name', ''), year, month, n))
        manifest = docx[:-5] + '.manifest.json'
        if not os.path.exists(docx) and not os.path.exists(manifest):
            break
        n += 1

    import services.bukhara_report_service as B
    if site == 'bukhara':
        gen = B.generate_bukhara_report
        history_path = B.DEFAULT_HISTORY_PATH_BUKHARA
    else:
        import services.tashkent_report_service as T
        gen = T.generate_tashkent_report
        history_path = B.DEFAULT_HISTORY_PATH
    history = B.load_history_records(history_path)
    history_json = json.loads(_json(history))

    summary = {}
    kwargs = dict(params)
    kwargs.update(paths)
    kwargs.update(output_path=docx, output_format='docx', progress_callback=progress,
                  summary_out=summary, history_records=copy.deepcopy(history))
    if site != 'bukhara':
        kwargs['project_id'] = project_id      # the month's alarms → equipment history
    accepted = set(inspect.signature(gen).parameters)
    kwargs = {k: v for k, v in kwargs.items() if k in accepted}

    log(f'Generating v{n} from the data set ({len(files)} files)…')
    t0 = time.time()
    gen(**kwargs)
    elapsed = round(time.time() - t0, 1)
    if not os.path.isfile(docx):
        raise RuntimeError('The DOCX was not written — see the log above.')
    _read_only(docx)

    inputs = {k: params.get(k) for k in REPORT_INPUT_KEYS}
    text = {k: params.get(k) for k in TEXT_KEYS if k in params}
    cls_path = B.DEFAULT_ALARM_CLASSIFICATIONS_CSV
    file_list = [{'type': f['data_type'], 'label': f['label'], 'original_name': f['original_name'],
                  'stored_path': f['stored_path'], 'sha256': f['sha256'],
                  'size_bytes': f['size_bytes'], 'added_at': f['added_at']} for f in files]
    app = app_info()
    key_numbers = {k: summary.get(k) for k, _, _ in NUMBERS}
    key_numbers['rows'] = summary.get('rows') or {}
    doc = {
        'manifest_format': MANIFEST_FORMAT,
        'project': {'id': project_id, 'name': proj.get('name'), 'site_type': site},
        'period': {'year': year, 'month': month},
        'version': n,
        'generated_at': datetime.datetime.now().isoformat(timespec='seconds'),
        'generation_seconds': elapsed,
        'docx': {'file': os.path.basename(docx), 'sha256': pc.file_sha256(docx)},
        'app': app,
        'input_files': file_list,
        'report_inputs': json.loads(_json(inputs)),
        'pm_records': json.loads(_json(rw.get_pm_activities(project_id, year, month))),
        'text': json.loads(_json(text)),
        'history_records': history_json,
        'alarm_classifications': {
            'file': os.path.basename(cls_path),
            'sha256': pc.file_sha256(cls_path) if os.path.isfile(cls_path) else None},
        'confirmations': list(confirmations or []),
        'confirmation_note': note or '',
        'key_numbers': key_numbers,
    }
    with open(manifest, 'w', encoding='utf-8') as fh:
        json.dump(doc, fh, indent=1, ensure_ascii=False, default=str)
    _read_only(manifest)

    row_summary = dict(key_numbers, inputs_sha=_sha_text(doc['report_inputs']),
                       files_sha=_sha_text(sorted(f['sha256'] for f in file_list)),
                       text_sha=_sha_text(doc['text']), code=app.get('code_fingerprint'),
                       generation_seconds=elapsed, confirmed=len(doc['confirmations']))
    conn = get_connection()
    try:
        cur = conn.execute("""
            INSERT INTO report_versions (project_id, year, month, version, docx_path,
                docx_sha256, manifest_path, manifest_sha256, summary)
            VALUES (?,?,?,?,?,?,?,?,?)""",
            (project_id, year, month, n, mds.rel(docx), doc['docx']['sha256'],
             mds.rel(manifest), pc.file_sha256(manifest), _json(row_summary)))
        conn.commit()
        vid = cur.lastrowid
    finally:
        conn.close()
    log(f'v{n} saved: {os.path.basename(docx)} ({elapsed:g} s)')
    return get_version(vid)


def load_manifest(path: str) -> dict:
    with open(path, encoding='utf-8') as fh:
        return json.load(fh)


# ── sent and locked ──────────────────────────────────────────────────────────

def _log(project_id, year, month, action, version=None, reason=''):
    conn = get_connection()
    try:
        conn.execute("INSERT INTO report_month_log (project_id, year, month, action, version, reason) "
                     "VALUES (?,?,?,?,?,?)", (project_id, year, month, action, version, reason or ''))
        conn.commit()
    finally:
        conn.close()


def mark_sent(version_id: int, final_docx: str) -> dict:
    """Record the Word-edited DOCX that went to the customer: a copy and its
    SHA-256 are kept with the version it is based on, and the month locks."""
    v = get_version(version_id)
    if not v:
        raise ValueError(f'no report version {version_id}')
    if not os.path.isfile(final_docx):
        raise FileNotFoundError(final_docx)
    if not final_docx.lower().endswith('.docx'):
        raise ValueError('pick the final Word file (.docx) that was sent')
    sha = pc.file_sha256(final_docx)
    sdir = os.path.join(mds.month_dir(v['project_id'], v['year'], v['month']), 'sent')
    os.makedirs(sdir, exist_ok=True)
    name = os.path.basename(final_docx)
    dest = os.path.join(sdir, f"v{v['version']}__{sha[:10]}__{mds._safe_name(name)}")
    if not os.path.exists(dest):
        shutil.copyfile(final_docx, dest)
        _read_only(dest)
    conn = get_connection()
    try:
        conn.execute("""UPDATE report_versions SET sent_at=datetime('now'), sent_docx_path=?,
                        sent_docx_sha256=?, sent_original_name=? WHERE id=?""",
                     (mds.rel(dest), sha, name, int(version_id)))
        conn.commit()
    finally:
        conn.close()
    rw.set_month_lock(v['project_id'], v['year'], v['month'], True)
    _log(v['project_id'], v['year'], v['month'], 'sent', v['version'], name)
    return get_version(version_id)


def unlock_month(project_id: int, year: int, month: int, reason: str):
    """Make a sent month's inputs editable again. The reason is required and logged."""
    reason = (reason or '').strip()
    if len(reason) < 3:
        raise ValueError('say why the month is unlocked')
    if not rw.month_locked_at(project_id, year, month):
        raise ValueError(f'{rw.MONTHS_EN[month]} {year} is not locked')
    rw.set_month_lock(project_id, year, month, False)
    _log(project_id, year, month, 'unlocked', latest_sent(project_id, year, month) and
         latest_sent(project_id, year, month)['version'], reason)


def lock_month(project_id: int, year: int, month: int):
    """Lock an unlocked month again (it needs a version marked as sent)."""
    sent = latest_sent(project_id, year, month)
    if not sent:
        raise ValueError('mark the final DOCX as sent first — that locks the month')
    rw.set_month_lock(project_id, year, month, True)
    _log(project_id, year, month, 'locked', sent['version'])


def latest_sent(project_id: int, year: int, month: int) -> Optional[dict]:
    conn = get_connection()
    try:
        r = conn.execute("SELECT * FROM report_versions WHERE project_id=? AND year=? AND month=? "
                         "AND sent_at IS NOT NULL ORDER BY sent_at DESC, version DESC LIMIT 1",
                         (project_id, year, month)).fetchone()
    finally:
        conn.close()
    return _row(r) if r else None


def month_status(project_id: int, year: int, month: int) -> dict:
    conn = get_connection()
    try:
        log = [dict(r) for r in conn.execute(
            "SELECT * FROM report_month_log WHERE project_id=? AND year=? AND month=? "
            "ORDER BY id", (project_id, year, month)).fetchall()]
    finally:
        conn.close()
    return {'locked_at': rw.month_locked_at(project_id, year, month),
            'sent': latest_sent(project_id, year, month), 'log': log}
