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

Video shares all of it. Also proved here: a clip is accepted on the SAME upload
route with its OWN size cap and its own mime list, validated by magic bytes; an
oversized clip is refused with the video cap in the message while a photo is
still refused at the photo cap; the uploaded poster frame becomes the row's
thumbnail (there is no ffmpeg here and must not be); the poster is fetchable on
its own so the office desktop can hold it; archiving a clip frees the clip AND
its poster; and an older client that sends neither poster nor duration, and
reads neither field, sees exactly the API it saw before.
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
            'archived_by': r.archived_by, 'mime_type': r.mime_type,
            'duration_ms': r.duration_ms}
    finally:
        db.close()


def webm(payload_kb=40) -> bytes:
    """Bytes that a magic-byte sniffer must read as WebM: the EBML header, then
    filler. Not a playable clip — nothing on the server ever decodes one, which
    is the whole point of the poster frame."""
    return b'\x1a\x45\xdf\xa3' + (b'\x42\x86\x81\x01' * 4) + os.urandom(payload_kb * 1024)


def mp4(payload_kb=40) -> bytes:
    """An ISO base-media header with an MP4 brand, then filler."""
    return (b'\x00\x00\x00\x20' + b'ftyp' + b'isom'
            + b'\x00\x00\x02\x00isomiso2avc1mp41' + os.urandom(payload_kb * 1024))


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
    check('mime_type' not in OLD_COLS and 'duration_ms' not in OLD_COLS,
          'nor the two video columns')
    check('mime_type' in now_cols and 'duration_ms' in now_cols,
          'and booting adds those in place too, on the SAME table — a clip is '
          'a row here, not a second store: {}'.format(now_cols[-2:]))
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

    print('\n=== a clip goes up the same route, under its own cap ===')
    from services.storage_service import (MAX_BYTES, MAX_VIDEO_BYTES,
                                          ALLOWED_VIDEO_MIMES)
    check(MAX_VIDEO_BYTES != MAX_BYTES and MAX_VIDEO_BYTES > MAX_BYTES,
          'video has its OWN cap, larger than the photo one: %d MB vs %d MB'
          % (MAX_VIDEO_BYTES // (1024 * 1024), MAX_BYTES // (1024 * 1024)))
    check(MAX_VIDEO_BYTES >= 8 * 1024 * 1024,
          'with headroom for a 30-second clip: %d MB'
          % (MAX_VIDEO_BYTES // (1024 * 1024)))

    poster_jpeg = jpeg((30, 90, 160), (854, 480))
    r = c.post('/worklogs/%s/images' % entry_id, headers=A,
               files={'file': ('clip.webm', webm(220), 'video/webm'),
                      'poster': ('poster.jpg', poster_jpeg, 'image/jpeg')},
               data={'duration_ms': '29840'})
    clip = r.json() if r.status_code == 201 else {}
    check(r.status_code == 201, 'POST a clip to /worklogs/{id}/images -> %s'
          % r.status_code)
    check(clip.get('mime_type') == 'video/webm',
          'the server read it as WebM from its magic bytes, not from the '
          'declared type: %s' % clip.get('mime_type'))
    check(clip.get('duration_ms') == 29840,
          'and stored its length: %s ms' % clip.get('duration_ms'))
    check(clip.get('has_poster') is True, 'and it has a poster frame')

    cr = row(clip['id'])
    check(on_disk(cr['file_path']) and on_disk(cr['thumbnail_path']),
          'the clip and its poster are both on the server disk')
    check(cr['thumbnail_path'].endswith('_thumb.jpg'),
          'THE POSTER IS THE ROW\'S THUMBNAIL — there is no ffmpeg here to '
          'make one: %s' % os.path.basename(cr['thumbnail_path']))
    check(os.path.getsize(get_file_path(cr['thumbnail_path'])) == len(poster_jpeg),
          'kept at the size the phone sent, not shrunk to 320x240 — the '
          'caption burned into it has to stay readable')
    check(cr['width'] == 854 and cr['height'] == 480,
          "the row's dimensions come from the poster: %sx%s"
          % (cr['width'], cr['height']))
    check((cr['sha256'] or '') and len(cr['sha256']) == 64,
          'and the clip is hashed exactly like a photo, so the desktop can '
          'verify it before anything is deleted')

    dl = c.get('/images/%s/download' % clip['id'], headers=A)
    check(dl.status_code == 200 and len(dl.content) == cr['size_bytes'],
          'the clip downloads in full: %s, %s bytes' % (dl.status_code, len(dl.content)))
    po = c.get('/images/%s/poster' % clip['id'], headers=A)
    check(po.status_code == 200 and po.content == poster_jpeg,
          'and the poster is fetchable on its own, byte for byte: %s'
          % po.status_code)

    # MP4 too — that is what iOS records and what Windows plays natively
    r2 = c.post('/worklogs/%s/images' % entry_id, headers=A,
                files={'file': ('clip.mp4', mp4(60), 'application/octet-stream')})
    m = r2.json() if r2.status_code == 201 else {}
    check(m.get('mime_type') == 'video/mp4',
          'an MP4 is recognised as well, whatever it claims to be: %s'
          % m.get('mime_type'))
    check(m.get('has_poster') is False and row(m['id'])['thumbnail_path'] is None,
          'a clip that arrives without a poster is still stored — losing the '
          'clip over a missing still would be the wrong trade')

    print('\n=== the caps are separate, and each says which one it is ===')
    big_clip = c.post('/worklogs/%s/images' % entry_id, headers=A,
                      files={'file': ('huge.webm',
                                      webm(MAX_VIDEO_BYTES // 1024 + 64),
                                      'video/webm')})
    detail = (big_clip.json().get('detail') or '') if big_clip.headers.get(
        'content-type', '').startswith('application/json') else big_clip.text
    check(big_clip.status_code == 413,
          'a clip over the video cap is refused: %s' % big_clip.status_code)
    check('Clip' in detail and str(MAX_VIDEO_BYTES // (1024 * 1024)) in detail,
          'and the message names the VIDEO cap, not the photo one: "%s"' % detail)

    # A photo is still measured against the photo cap, so raising one limit
    # cannot quietly raise the other.
    big_photo = c.post('/worklogs/%s/images' % entry_id, headers=A,
                       files={'file': ('huge.jpg',
                                       b'\xff\xd8\xff' + os.urandom(MAX_BYTES + 4096),
                                       'image/jpeg')})
    pdetail = (big_photo.json().get('detail') or '') if big_photo.headers.get(
        'content-type', '').startswith('application/json') else big_photo.text
    check(big_photo.status_code == 413,
          'a photo over the photo cap is still refused: %s' % big_photo.status_code)
    check(str(MAX_BYTES // (1024 * 1024)) in pdetail,
          'against the photo cap: "%s"' % pdetail)
    # and one that fits the photo cap but not the video one is fine
    ok_photo = c.post('/worklogs/%s/images' % entry_id, headers=A,
                      files={'file': ('fine.jpg', jpeg((1, 2, 3)), 'image/jpeg')})
    check(ok_photo.status_code == 201,
          'an ordinary photo is unaffected by any of this: %s'
          % ok_photo.status_code)

    print('\n=== mime validation is by magic bytes, for video too ===')
    check(sorted(ALLOWED_VIDEO_MIMES) == ['video/mp4', 'video/quicktime', 'video/webm'],
          'the video allow-list is what a phone records: %s'
          % sorted(ALLOWED_VIDEO_MIMES))
    # The declared content type is never believed. A file that calls itself
    # video/webm but is a Windows executable is NOT filed as video: no video
    # mime, no poster, and — the part that matters — it does not get the video
    # size cap, so claiming to be a clip cannot buy 40 MB of the server's disk.
    # (It is still stored, as an unrecognised photo, through the fallback this
    # route has always had: the phones upload formats the sniffer does not
    # match byte for byte, and refusing those would stop photos that work.)
    bad = c.post('/worklogs/%s/images' % entry_id, headers=A,
                 files={'file': ('evil.webm', b'MZ\x90\x00' + os.urandom(2048),
                                 'video/webm')})
    bb = bad.json() if bad.status_code == 201 else {}
    check(bb.get('mime_type') != 'video/webm'
          and not str(bb.get('mime_type') or '').startswith('video/'),
          'a file that CLAIMS video/webm but is an .exe is not filed as '
          'video: %s' % bb.get('mime_type'))
    check(bb.get('has_poster') is False, 'and gets no poster')
    over_claim = c.post('/worklogs/%s/images' % entry_id, headers=A,
                        files={'file': ('lie.webm',
                                        b'MZ\x90\x00' + os.urandom(MAX_BYTES + 4096),
                                        'video/webm')})
    check(over_claim.status_code == 413,
          "and CANNOT buy the video cap by claiming to be a clip — it is "
          "measured against the photo cap: %s" % over_claim.status_code)
    # a poster that is not an image is dropped, and does not take the clip down
    pj = c.post('/worklogs/%s/images' % entry_id, headers=A,
                files={'file': ('c2.webm', webm(8), 'video/webm'),
                       'poster': ('p.jpg', b'MZ\x90\x00' + os.urandom(512),
                                  'image/jpeg')})
    pjb = pj.json() if pj.status_code == 201 else {}
    check(pj.status_code == 201 and pjb.get('has_poster') is False,
          'a poster that is not really an image is dropped, and the clip is '
          'still accepted: %s / has_poster=%s' % (pj.status_code, pjb.get('has_poster')))

    print('\n=== archiving a clip frees the clip AND its poster ===')
    before_clip = row(clip['id'])
    want = sum(os.path.getsize(get_file_path(p)) for p in
               (before_clip['file_path'], before_clip['thumbnail_path']))
    ar = c.post('/images/archived', headers=A, json={'image_ids': [clip['id']]})
    ab = ar.json() if ar.status_code == 200 else {}
    check(ab.get('archived') == 1, 'the same POST /images/archived handles it — '
          'there is no second retention path')
    check(ab.get('freed_bytes') == want,
          'and it reports both files freed: %s of %s expected'
          % (ab.get('freed_bytes'), want))
    check(not on_disk(before_clip['file_path'])
          and not on_disk(before_clip['thumbnail_path']),
          'neither the clip nor its poster is left on the server disk')
    after_clip = row(clip['id'])
    check(after_clip is not None and after_clip['mime_type'] == 'video/webm'
          and after_clip['duration_ms'] == 29840
          and after_clip['sha256'] == before_clip['sha256'],
          'the row and every field on it survive, clip metadata included')
    pgone = c.get('/images/%s/poster' % clip['id'], headers=A)
    pdet = (pgone.json().get('detail') or '') if pgone.headers.get(
        'content-type', '').startswith('application/json') else pgone.text
    check(pgone.status_code == 410 and 'office desktop' in pdet.lower(),
          'and asking for the poster says the office has it, not 404: %s'
          % pgone.status_code)
    check(c.get('/images/%s/poster' % ok_photo.json()['id'],
                headers=A).status_code == 200,
          "a photo's thumbnail is served by the same route")
    nop = c.get('/images/%s/poster' % m['id'], headers=A)
    check(nop.status_code == 404,
          'a clip that never had a poster answers a plain 404: %s' % nop.status_code)

    print('\n=== an older client sees the API it has always seen ===')
    # No poster part, no duration field — exactly what the phone sent before
    # this feature existed.
    old = c.post('/worklogs/%s/images' % entry_id, headers=A,
                 files={'file': ('legacy.jpg', jpeg((80, 80, 80)), 'image/jpeg')})
    ob = old.json() if old.status_code == 201 else {}
    check(old.status_code == 201, 'an upload with no video fields -> %s'
          % old.status_code)
    check(ob.get('mime_type') == 'image/jpeg' and ob.get('duration_ms') is None,
          'is a photo with no length: %s / %s'
          % (ob.get('mime_type'), ob.get('duration_ms')))
    check(row(ob['id'])['thumbnail_path'] is not None,
          'and Pillow still makes its thumbnail from the photo itself')
    lst2 = c.get('/worklogs/%s/images' % entry_id, headers=A)
    items2 = {i['id']: i for i in lst2.json()} if lst2.status_code == 200 else {}
    check(items2.get(ob['id'], {}).get('mime_type') == 'image/jpeg'
          and items2.get(clip['id'], {}).get('mime_type') == 'video/webm',
          'the listing tells them apart by mime type, and a client that does '
          'not read the field sees the payload it always saw')
    check(items2.get(LEGACY_IMG, {}).get('mime_type') is None,
          'a row written before the column existed reports mime_type null — '
          'which reads as a photo, as it always was')

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
