"""Server half of test_photo_retention.py — runs in its own process.

The backend's top-level packages (database, services, models) share their names
with the desktop's, so the two cannot live in one interpreter: the harness
imports the desktop ones. The parent test launches this file as a child process
and replays its checks as its own:

    python tests/_photo_retention_server.py <work dir> <results.json>

Everything is local: DATABASE_URL is a fresh file in the parent's work dir,
UPLOAD_DIR is a fresh folder, SECRET_KEY is random per run, and TestClient never
opens a socket. The live server is never contacted.

What is proved here: confirming an archive deletes the file and the thumbnail
while the row, its metadata and its place in the listing survive; the listing
flags it; a second confirm is a no-op; downloading an archived photo says where
the photo is instead of reading as data loss; an unconfirmed photo is kept for
ever; and the storage report is numbers without paths.
"""
import io
import json
import os
import sys
import time
import uuid

WORK, OUT = sys.argv[1], sys.argv[2]
BACKEND = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'backend')
ADMIN_PW = 'pw-' + uuid.uuid4().hex[:16]
OTHER_PW = 'op-' + uuid.uuid4().hex[:16]
UPLOADS = os.path.join(WORK, 'srv_uploads')

# Set before importing the app: config.py reads os.environ at import time, and
# load_dotenv() does not override what is already set.
os.environ.update(
    DATABASE_URL='sqlite:///' + os.path.join(WORK, 'photos.db').replace('\\', '/'),
    UPLOAD_DIR=UPLOADS,
    SECRET_KEY='test-' + uuid.uuid4().hex,
    FIRST_ADMIN_USERNAME='admin', FIRST_ADMIN_PASSWORD=ADMIN_PW)
if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace',
                                  line_buffering=True)
sys.path.insert(0, BACKEND)
os.chdir(WORK)                      # keeps bess_server.log out of the repo

# ── A database in the state the live server is in: work_log_images WITHOUT the
# two new columns, and a photo row already in it. The deploy has to migrate this
# in place, not rebuild it, and the row must still be there afterwards.
import sqlite3                                                       # noqa: E402

OLD_DB = os.path.join(WORK, 'photos.db')
_old = sqlite3.connect(OLD_DB)
_old.execute("""CREATE TABLE work_log_images (
                  id TEXT PRIMARY KEY, work_log_id TEXT NOT NULL,
                  file_path TEXT NOT NULL, thumbnail_path TEXT,
                  filename TEXT DEFAULT '', size_bytes BIGINT DEFAULT 0,
                  sha256 TEXT DEFAULT '', width INTEGER, height INTEGER,
                  taken_at TEXT, uploaded_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL, upload_status TEXT DEFAULT 'uploaded')""")
LEGACY_IMG = str(uuid.uuid4())
_old.execute("INSERT INTO work_log_images (id, work_log_id, file_path, filename, "
             "size_bytes, uploaded_at, updated_at) VALUES (?,?,?,?,?,?,?)",
             (LEGACY_IMG, str(uuid.uuid4()), 'legacy/old.jpg', 'old.jpg', 4242,
              '2026-08-01 10:00:00', '2026-08-01 10:00:00'))
_old.commit()
OLD_COLS = [r[1] for r in _old.execute("PRAGMA table_info(work_log_images)")]
_old.close()

from fastapi.testclient import TestClient                            # noqa: E402
import main                                                          # noqa: E402
from database import SessionLocal                                    # noqa: E402
from models.db_models import WorkLogImage                            # noqa: E402
from services.storage_service import get_file_path                   # noqa: E402

results = []


def check(cond, msg):
    cond = bool(cond)
    print(('   ok    ' if cond else '   FAIL  ') + msg)
    results.append([cond, msg])
    return cond


def jpeg(colour, size=(90, 70)) -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.new('RGB', size, colour).save(buf, 'JPEG', quality=80)
    return buf.getvalue()


def row(image_id):
    db = SessionLocal()
    try:
        r = db.query(WorkLogImage).filter(WorkLogImage.id == image_id).first()
        return None if r is None else {
            'file_path': r.file_path, 'thumbnail_path': r.thumbnail_path,
            'filename': r.filename, 'size_bytes': r.size_bytes, 'sha256': r.sha256,
            'width': r.width, 'height': r.height, 'uploaded_at': r.uploaded_at,
            'updated_at': r.updated_at, 'file_archived_at': r.file_archived_at,
            'archived_by': r.archived_by}
    finally:
        db.close()


def on_disk(rel):
    return bool(rel) and os.path.isfile(get_file_path(rel))


with TestClient(main.app) as c:
    print('=== the deploy migrates an existing database in place ===')
    check('file_archived_at' not in OLD_COLS,
          'the fixture started without the retention columns')
    now_cols = [r[1] for r in sqlite3.connect(OLD_DB).execute(
        "PRAGMA table_info(work_log_images)")]
    check('file_archived_at' in now_cols and 'archived_by' in now_cols,
          'booting adds them: {}'.format(now_cols[-2:]))
    legacy = row(LEGACY_IMG)
    check(legacy is not None and legacy['size_bytes'] == 4242,
          'and the photo row that was already there is untouched')
    check(legacy is not None and legacy['file_archived_at'] is None,
          'unarchived, so its file is still the server\'s to keep')

    tok = c.post('/auth/login', json={'username': 'admin', 'password': ADMIN_PW,
                                      'device_id': 'desktop-A'}).json()['access_token']
    A = {'Authorization': 'Bearer ' + tok}

    # An entry to hang photos on, through the proven push path.
    entry_id = str(uuid.uuid4())
    c.post('/sync/push', headers=A, json={
        'device_id': 'desktop-A', 'idempotency_key': str(uuid.uuid4()),
        'changes': [{'entity': 'work_log', 'id': entry_id, 'action': 'upsert',
                     'version': 1,
                     'payload': {'project_id': None, 'category': 'fault',
                                 'description': 'photo retention',
                                 'log_date': '2026-09-29', 'tags': []}}]})

    def upload(colour, name):
        r = c.post('/worklogs/%s/images' % entry_id, headers=A,
                   files={'file': (name, jpeg(colour), 'image/jpeg')})
        return r.json() if r.status_code == 201 else None

    kept = upload((10, 40, 90), 'kept.jpg')       # never confirmed
    gone = upload((200, 30, 30), 'gone.jpg')      # confirmed below
    check(kept and gone, 'two photos uploaded')
    before = row(gone['id'])
    check(on_disk(before['file_path']) and on_disk(before['thumbnail_path']),
          'the uploaded file and its thumbnail are on the server disk')

    print('=== the desktop confirms; the file goes, the record stays ===')
    r = c.post('/images/archived', headers=A, json={'image_ids': [gone['id']]})
    body = r.json() if r.status_code == 200 else {}
    check(r.status_code == 200, 'POST /images/archived -> %s' % r.status_code)
    check(body.get('archived') == 1 and body.get('freed_bytes', 0) > 0,
          'it reports 1 archived and the bytes it freed: %s' % body.get('freed_bytes'))
    check((body.get('results') or [{}])[0].get('outcome') == 'archived',
          'the per-photo outcome is "archived"')

    after = row(gone['id'])
    check(after is not None, 'the work_log_images ROW SURVIVES (file-only delete)')
    check(not on_disk(before['file_path']), 'the file is gone from the server disk')
    check(not on_disk(before['thumbnail_path']), 'the thumbnail is gone too')
    check(all(after[k] == before[k] for k in
              ('filename', 'size_bytes', 'sha256', 'width', 'height', 'uploaded_at')),
          'filename, size, sha256 and dimensions are untouched')
    check(bool(after['file_archived_at']), 'the row is stamped file_archived_at')
    check(bool(after['archived_by']), 'and with who confirmed it')
    check(after['updated_at'] == before['updated_at'],
          'updated_at is NOT bumped, so every client is not re-sent the row')

    print('\n=== the listing still shows it, flagged ===')
    lst = c.get('/worklogs/%s/images' % entry_id, headers=A)
    items = {i['id']: i for i in lst.json()} if lst.status_code == 200 else {}
    check(gone['id'] in items, 'GET /worklogs/{id}/images still lists the archived photo')
    check(items.get(gone['id'], {}).get('archived') is True,
          'it is flagged archived=true, so a client can say "held on the office desktop"')
    check(bool(items.get(gone['id'], {}).get('archived_at')), 'with the date it happened')
    check(items.get(kept['id'], {}).get('archived') is False,
          'the photo still on the server is archived=false')

    print('\n=== the pull says so too, for a client that has never seen it ===')
    # A pull only serves rows whose second has fully passed (sync_cursor.py).
    time.sleep(2.2)
    pull = c.get('/sync/pull', headers=A, params={'since': '0',
                                                 'device_id': 'desktop-B'})
    imgs = {ch['id']: ch['data'] for ch in pull.json().get('changes', [])
            if ch['entity'] == 'work_log_image'} if pull.status_code == 200 else {}
    check(imgs.get(gone['id'], {}).get('archived') is True,
          'the delta marks the archived photo archived, so a second desktop need '
          'not ask for a file that is not there')
    check(imgs.get(kept['id'], {}).get('archived') is False,
          'and leaves the others alone')
    check('sha256' in imgs.get(gone['id'], {}),
          'while still sending the hash that identifies the office copy')

    print('\n=== confirming twice is a no-op, not an error ===')
    r2 = c.post('/images/archived', headers=A, json={'image_ids': [gone['id']]})
    b2 = r2.json() if r2.status_code == 200 else {}
    check(r2.status_code == 200, 'a repeat confirm answers 200, not an error')
    check(b2.get('already') == 1 and b2.get('archived') == 0 and b2.get('freed_bytes') == 0,
          'it is counted as "already", nothing is freed twice')
    check(row(gone['id'])['file_archived_at'] == after['file_archived_at'],
          'the first archive timestamp is not overwritten')

    print('\n=== downloading an archived photo says where it is ===')
    d = c.get('/images/%s/download' % gone['id'], headers=A)
    detail = (d.json().get('detail') or '') if d.headers.get(
        'content-type', '').startswith('application/json') else d.text
    check(d.status_code == 410,
          'HTTP 410 Gone, not a bare 404 that reads like data loss: %s' % d.status_code)
    check('office desktop' in detail.lower() and 'no longer' in detail.lower(),
          'and it says the office desktop has it: "%s"' % detail[:90])
    check('nothing is lost' in detail.lower(), 'and that nothing is lost')
    check(c.get('/images/%s/download' % uuid.uuid4(), headers=A).status_code == 404,
          'an unknown id is still a plain 404 — the two cases stay distinguishable')

    print('\n=== an older client that never confirms loses nothing ===')
    k = row(kept['id'])
    check(on_disk(k['file_path']) and on_disk(k['thumbnail_path']),
          'the unconfirmed photo is still on disk (no time-based deletion)')
    check(k['file_archived_at'] is None, 'and unstamped')
    dl = c.get('/images/%s/download' % kept['id'], headers=A)
    check(dl.status_code == 200 and len(dl.content) == k['size_bytes'],
          'it still downloads in full: %s, %s bytes' % (dl.status_code, len(dl.content)))

    print('\n=== a bad batch does not sink the good ids in it ===')
    third = upload((20, 160, 60), 'third.jpg')
    missing = str(uuid.uuid4())
    r3 = c.post('/images/archived', headers=A,
                json={'image_ids': [missing, third['id']]})
    b3 = r3.json() if r3.status_code == 200 else {}
    outcomes = {x['id']: x['outcome'] for x in (b3.get('results') or [])}
    check(r3.status_code == 200, 'a batch with an unknown id answers 200')
    check(outcomes.get(missing) == 'not_found', 'the unknown id comes back not_found')
    check(outcomes.get(third['id']) == 'archived', 'the real one beside it is archived')
    check(c.post('/images/archived', headers=A, json={'image_ids': []}).status_code == 200,
          'an empty list is accepted and does nothing')
    over = c.post('/images/archived', headers=A,
                  json={'image_ids': [str(uuid.uuid4()) for _ in range(501)]})
    check(over.status_code == 413, 'more than 500 ids in one call is refused: %s'
          % over.status_code)

    print('\n=== who may confirm ===')
    c.post('/auth/register', headers=A,
           json={'username': 'tech1', 'password': OTHER_PW, 'role': 'engineer'})
    otok = c.post('/auth/login', json={'username': 'tech1', 'password': OTHER_PW,
                                       'device_id': 'phone-T'}).json()['access_token']
    T = {'Authorization': 'Bearer ' + otok}
    r4 = c.post('/images/archived', headers=T, json={'image_ids': [kept['id']]})
    b4 = r4.json() if r4.status_code == 200 else {}
    check((b4.get('results') or [{}])[0].get('outcome') == 'forbidden',
          "someone else's photo cannot be archived by them")
    check(on_disk(row(kept['id'])['file_path']),
          'and the file is still there after the refused attempt')
    check(c.get('/images/storage', headers=T).status_code == 403,
          'the storage report is admin only')

    print('\n=== the state is visible, as numbers without paths ===')
    s = c.get('/images/storage', headers=A)
    st = s.json() if s.status_code == 200 else {}
    check(s.status_code == 200, 'GET /images/storage -> %s' % s.status_code)
    check(st.get('archived_count') == 2 and st.get('on_server_count') == 2,
          'it counts 2 archived and 2 still here (one of them the legacy row): '
          '%s / %s' % (st.get('archived_count'), st.get('on_server_count')))
    check(st.get('on_server_bytes') == k['size_bytes'] + legacy['size_bytes'],
          'and the bytes still held are those two photos, not the archived ones')
    disk = st.get('disk') or {}
    check(disk.get('total_mb', 0) > 0 and disk.get('free_mb', 0) > 0,
          'disk total/free are real numbers: %s MB total, %s MB free'
          % (disk.get('total_mb'), disk.get('free_mb')))
    check(0 <= disk.get('used_pct', -1) <= 100, 'used_pct is a percentage: %s'
          % disk.get('used_pct'))
    check(all(isinstance(v, (int, float)) for v in disk.values()),
          'every disk value is a number — no paths')
    blob = json.dumps(st)
    check('/' not in blob.replace('\\/', '') and '\\\\' not in blob,
          'the whole report contains no path: %s' % blob[:120])

    print('\n=== /healthz: unauthenticated, numbers only ===')
    h = c.get('/healthz')
    hb = h.json() if h.status_code == 200 else {}
    hdisk = (hb.get('storage') or {}).get('disk') or {}
    check(h.status_code == 200, '/healthz needs no token: %s' % h.status_code)
    check(hdisk.get('total_mb', 0) > 0 and 'used_pct' in hdisk,
          'it now reports disk usage: %s' % hdisk)
    check(all(isinstance(v, (int, float)) for v in hdisk.values()),
          'as numbers only')
    hblob = json.dumps(hb)
    check('/' not in hblob and UPLOADS.replace('\\', '/') not in hblob.replace('\\\\', '/'),
          'and it leaks no path: %s' % hblob[:160])

with open(OUT, 'w', encoding='utf-8') as f:
    json.dump(results, f)
print('\nRESULT %s' % ('FAIL' if any(not ok for ok, _ in results) else 'PASS'))
sys.stdout.flush()
os._exit(0)
