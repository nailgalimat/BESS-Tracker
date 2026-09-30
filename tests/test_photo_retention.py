"""Photo retention: the desktop is the archive, the server is a staging post.

The server's disk is 1 GB, the phones add ~600 MB a month and nothing ever freed
it. The owner's decision: once a photo has safely reached the office desktop the
server may delete its file. "Safely" is the whole test — the file present locally
AND hashing to the sha256 the server stored at upload. A size check is not
enough, and this database proves why: two photos on one record, both named
image.jpg, overwrote each other locally, so one row pointed at a file of exactly
the right shape that was the WRONG photo, and the only good copy was the
server's.

Two halves:
  * the server (a child process — the backend's `database` / `services` /
    `models` packages share their names with the desktop's, which the harness
    has already imported, so the two cannot live in one interpreter). Its checks
    are replayed here, so a server failure fails this test.
  * the desktop sweep, driven directly with the confirm call replaced by a
    recorder: what it WOULD tell the server to delete is asserted, and no socket
    is opened.
"""
import _harness as H            # must be first: isolates DB, sync, network, history
import hashlib
import json
import os
import subprocess
import sys
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))

# ── Server: the endpoint, in its own process ─────────────────────────────────

print('=== server: POST /images/archived, the 410, the storage report ===')
OUT = os.path.join(H.WORK, 'server_checks.json')
proc = subprocess.run(
    [sys.executable, os.path.join(HERE, '_photo_retention_server.py'), H.WORK, OUT],
    capture_output=True, text=True, encoding='utf-8', errors='replace',
    env=dict(os.environ), timeout=300)
if os.path.exists(OUT):
    with open(OUT, encoding='utf-8') as f:
        for ok, msg in json.load(f):
            H.check(ok, 'server: ' + msg)
else:
    H.check(False, 'the server checks ran at all')
    for line in ((proc.stdout or '') + (proc.stderr or '')).splitlines()[-25:]:
        print('      ' + line)

# ── Desktop: an empty database of our own ────────────────────────────────────

H.fresh_db('retention.db')

import services.image_service as isvc                                # noqa: E402
import services.photo_retention_service as prs                       # noqa: E402
import services.sync_client as sc                                    # noqa: E402
from database.db_manager import get_connection                       # noqa: E402

# Keep the photos out of the repo: in dev mode field_images/ sits next to the
# source tree, and a test must not write there.
FIELD = os.path.join(H.WORK, 'field_images')
isvc._get_field_images_dir = lambda: (os.makedirs(FIELD, exist_ok=True), FIELD)[1]

ENTRY = str(uuid.uuid4())
conn = get_connection()
conn.execute("INSERT INTO work_log_entries (id, category, description, log_date) "
             "VALUES (?, 'fault', 'photo retention', '2026-09-29')", (ENTRY,))
conn.commit()
conn.close()


def sha256_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def add_photo(name, data, sha=None, write=True, status='uploaded'):
    """One work_log_images row, with its local file. *sha* overrides the stored
    hash, which is how a corrupted download is simulated."""
    image_id = str(uuid.uuid4())
    folder = isvc.get_image_dir(ENTRY)
    path = os.path.join(folder, name)
    if write:
        with open(path, 'wb') as f:
            f.write(data)
    thumb = os.path.splitext(path)[0] + '_thumb.jpg'
    with open(thumb, 'wb') as f:
        f.write(b'thumb')
    c = get_connection()
    c.execute("""INSERT INTO work_log_images
                 (id, work_log_id, file_path, thumbnail_path, filename, size_bytes,
                  sha256, uploaded_at, upload_status)
                 VALUES (?,?,?,?,?,?,?,datetime('now'),?)""",
              (image_id, ENTRY, path, thumb, name, len(data),
               sha or sha256_of(data), status))
    c.commit()
    c.close()
    return image_id, path


def stamped(image_id):
    c = get_connection()
    try:
        r = c.execute("SELECT server_archived_at, file_path FROM work_log_images "
                      "WHERE id=?", (image_id,)).fetchone()
        return (r['server_archived_at'], r['file_path']) if r else (None, None)
    finally:
        c.close()


class Recorder:
    """Stands in for the server. Records every id it is asked to free."""

    def __init__(self, outcome='archived', freed=2_500_000):
        self.calls, self.outcome, self.freed = [], outcome, freed

    def __call__(self, ids):
        ids = list(ids)
        self.calls.append(ids)
        return {'results': [{'id': i, 'outcome': self.outcome,
                             'freed_bytes': self.freed} for i in ids],
                'archived': len(ids) if self.outcome == 'archived' else 0,
                'already': len(ids) if self.outcome == 'already' else 0,
                'freed_bytes': self.freed * len(ids)
                if self.outcome == 'archived' else 0}

    def all_ids(self):
        return [i for call in self.calls for i in call]


GOOD = b'\xff\xd8\xff' + b'a good photo, 400 bytes worth' * 13
OTHER = b'\xff\xd8\xff' + b'a completely different photo!' * 13

print('\n=== a verified photo is confirmed; the wrong one never is ===')
good_id, good_path = add_photo('good.jpg', GOOD)
# The real failure from the live database: the row's stored hash belongs to the
# photo the phone uploaded, but the local file is another photo of a similar size.
bad_id, bad_path = add_photo('image.jpg', OTHER, sha=sha256_of(GOOD))
miss_id, miss_path = add_photo('lost.jpg', GOOD, write=False)

rec = Recorder()
rep = prs.sweep(confirm=rec, repair_mismatches=False)

H.check(rec.all_ids() == [good_id],
        'only the hash-verified photo is offered to the server: {}'
        .format([i[:8] for i in rec.all_ids()]))
H.check(bad_id not in rec.all_ids(),
        'THE PHOTO WHOSE HASH DOES NOT MATCH IS NEVER CONFIRMED')
H.check(miss_id not in rec.all_ids(), 'nor is one whose local file is missing')
H.check(rep['confirmed'] == 1 and rep['freed_bytes'] == 2_500_000,
        'the sweep reports what was freed: {} photo(s), {} bytes'
        .format(rep['confirmed'], rep['freed_bytes']))

skipped = {s['id']: s for s in rep['skipped']}
H.check(bad_id in skipped and 'hash' in skipped[bad_id]['reason'],
        'the mismatch is NAMED, with the reason: {}'
        .format(skipped.get(bad_id, {}).get('reason')))
H.check(skipped[bad_id]['filename'] == 'image.jpg',
        'by the file name the owner would recognise')
H.check(miss_id in skipped and 'not there' in skipped[miss_id]['reason'],
        'the missing local copy is named too: {}'
        .format(skipped.get(miss_id, {}).get('reason')))

H.check(stamped(good_id)[0] is not None, 'the confirmed photo is stamped locally')
H.check(stamped(bad_id)[0] is None and stamped(miss_id)[0] is None,
        'the two skipped photos stay unconfirmed, so the server keeps them')
H.check(os.path.isfile(good_path),
        'the LOCAL file is untouched — this desktop is the archive')

print('\n=== a mismatch waits in sync_inbox, so it is reported until resolved ===')
waiting = {w['item_id']: w for w in sc.inbox_waiting()}
H.check(bad_id in waiting and waiting[bad_id]['kind'] == 'photo_verify',
        'the corrupted copy is parked in the inbox')
H.check('hash' in (waiting.get(bad_id, {}).get('error') or ''),
        'with what went wrong: {}'.format(waiting.get(bad_id, {}).get('error')))

print('\n=== running it again re-offers nothing that is already confirmed ===')
rec2 = Recorder()
rep2 = prs.sweep(confirm=rec2, repair_mismatches=False)
H.check(good_id not in rec2.all_ids(),
        'the confirmed photo is not offered a second time')
H.check(rep2['checked'] == 2, 'only the two unconfirmed ones are re-checked: {}'
        .format(rep2['checked']))
H.check(rep2['remaining'] == 2, 'and it says 2 are still unconfirmed')

print('\n=== an "already archived" answer is a no-op, not an error ===')
again_id, _ = add_photo('again.jpg', GOOD)
rec3 = Recorder(outcome='already', freed=0)
rep3 = prs.sweep(confirm=rec3)
H.check(rep3['already'] == 1 and rep3['errors'] == 0,
        'a repeat confirm counts as "already" with no error: {}'.format(rep3))
H.check(stamped(again_id)[0] is not None,
        'and it is stamped, so it is not asked about for ever')

print('\n=== if the confirm call fails, nothing is stamped ===')
fail_id, fail_path = add_photo('later.jpg', GOOD)


def exploding(ids):
    raise RuntimeError('server said 503')


rep4 = prs.sweep(confirm=exploding)
H.check(rep4['errors'] == 1 and rep4['confirmed'] == 0,
        'the failure is reported, nothing counted as freed')
H.check(stamped(fail_id)[0] is None,
        'the photo is NOT stamped, so the next sync retries it')
rec5 = Recorder()
rep5 = prs.sweep(confirm=rec5)
H.check(fail_id in rec5.all_ids() and stamped(fail_id)[0] is not None,
        'and the next sweep does retry it')

print('\n=== the automatic pass has an off switch and a per-cycle cap ===')
rec_off = Recorder()
off = prs.sweep(limit=0, confirm=rec_off)
H.check(rec_off.calls == [] and off['checked'] == 0,
        'limit=0 touches nothing on the server (AUTO_LIMIT=0 is the off switch)')
H.check(prs.AUTO_LIMIT > 0,
        'the automatic pass is capped per sync, not unbounded: {}'
        .format(prs.AUTO_LIMIT))
rec_cap = Recorder()
prs.sweep(limit=1, confirm=rec_cap, repair_mismatches=False)
H.check(sum(len(c) for c in rec_cap.calls) <= 1, 'and a cap is honoured')

print('\n=== repairing a mismatch: download it again, verify, then confirm ===')
served = {}


def fake_download(image_id, dest):
    data = served.get(image_id)
    if data is None:
        return False
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, 'wb') as f:
        f.write(data)
    return True


served[bad_id] = GOOD               # the server still has the only good copy
rec6 = Recorder()
rep6 = prs.sweep(confirm=rec6, download=fake_download)
H.check(rep6['repaired'] == 1, 'the wrong local copy was downloaded again')
new_path = stamped(bad_id)[1]
H.check(os.path.normcase(new_path) != os.path.normcase(bad_path),
        'to a name that cannot collide with the other photo: {}'
        .format(os.path.basename(new_path or '')))
H.check(os.path.isfile(bad_path) and open(bad_path, 'rb').read() == OTHER,
        "the other photo's file is left exactly as it was")
H.check(bad_id in rec6.all_ids() and stamped(bad_id)[0] is not None,
        'and only now is it confirmed')
H.check(bad_id not in {w['item_id'] for w in sc.inbox_waiting()},
        'the inbox row is cleared once it verifies')

print('\n=== a repair that still does not verify changes nothing ===')
worse_id, worse_path = add_photo('worse.jpg', OTHER, sha=sha256_of(GOOD))
served[worse_id] = b'\xff\xd8\xffstill the wrong bytes'
rec7 = Recorder()
rep7 = prs.sweep(confirm=rec7, download=fake_download)
H.check(worse_id not in rec7.all_ids() and stamped(worse_id)[0] is None,
        'a photo that will not verify is never confirmed, however many tries')
H.check(rep7['repaired'] == 0, 'and is not counted as repaired')
H.check(worse_id in {w['item_id'] for w in sc.inbox_waiting()},
        'it stays in the inbox for someone to look at')
tries = []
prs.sweep(limit=prs.AUTO_LIMIT, repair_mismatches=False, confirm=Recorder(),
          download=lambda i, d: (tries.append(i), False)[1])
H.check(tries == [],
        'the automatic pass never re-downloads — that would run every 60 s for '
        'ever on a photo that will never verify: {}'.format(tries))
H.check(worse_id in {w['item_id'] for w in sc.inbox_waiting()},
        'it is still reported by the automatic pass, just not re-fetched')

print('\n=== what the owner is shown ===')
st = prs.local_status()
H.check(st['total'] == 6, 'every photo is counted: {}'.format(st['total']))
H.check(st['archived_count'] == 4 and st['on_server_count'] == 2,
        '4 archived here, 2 still on the server: {} / {}'
        .format(st['archived_count'], st['on_server_count']))
H.check(st['archived_bytes'] > 0 and st['on_server_bytes'] > 0,
        'with bytes on both sides: {} / {}'
        .format(st['archived_bytes'], st['on_server_bytes']))
H.check(len(st['unverified']) == 1 and st['unverified'][0]['item_id'] == worse_id,
        'and the one that cannot be verified is listed: {}'.format(st['unverified']))
text = prs.describe(rep6)
H.check('SKIPPED' in text and 'MB' in text,
        'the result reads as a sentence: {}'.format(text))

print('\n=== two photos of one record can no longer overwrite each other ===')
# The cause of the live mismatch: phones name every photo image.jpg, and the
# downloader wrote them all to <record>/image.jpg.
c = get_connection()
first = str(uuid.uuid4())
c.execute("""INSERT INTO work_log_images (id, work_log_id, file_path, filename,
             size_bytes, sha256, upload_status) VALUES (?,?,?,?,?,?, 'remote')""",
          (first, ENTRY, '', 'shot.jpg', len(GOOD), sha256_of(GOOD)))
second = str(uuid.uuid4())
c.execute("""INSERT INTO work_log_images (id, work_log_id, file_path, filename,
             size_bytes, sha256, upload_status) VALUES (?,?,?,?,?,?, 'remote')""",
          (second, ENTRY, '', 'shot.jpg', len(OTHER), sha256_of(OTHER)))
c.commit()
c.close()
sc.download_image_file = lambda image_id, dest: fake_download(image_id, dest)
served[first], served[second] = GOOD, OTHER
p1 = isvc.download_remote_image(first, ENTRY)
p2 = isvc.download_remote_image(second, ENTRY)
H.check(p1 and p2 and os.path.normcase(p1) != os.path.normcase(p2),
        'the second photo gets its own path: {} vs {}'
        .format(os.path.basename(p1 or ''), os.path.basename(p2 or '')))
H.check(open(p1, 'rb').read() == GOOD and open(p2, 'rb').read() == OTHER,
        'and each row now points at its own photo')
H.check(prs.verify(dict(id=first, file_path=p1, sha256=sha256_of(GOOD))) == 'ok'
        and prs.verify(dict(id=second, file_path=p2, sha256=sha256_of(OTHER))) == 'ok',
        'so both verify, and both can be freed on the server')

print('\n=== Project -> Synchronisation shows the state ===')
# The owner runs this desktop himself, so the figures and the action live on the
# screen he already opens, not in a log. Sync is off in tests, so the dialog
# never starts its storage thread and no socket is opened.
from PyQt5.QtWidgets import QApplication                             # noqa: E402

app = QApplication.instance() or QApplication([])
import ui.sync_settings_dialog as ssd                                # noqa: E402

now = prs.local_status()          # the two re-downloaded photos count from here on
dlg = ssd.SyncSettingsDialog()
here, server = dlg._ph_here_lbl.text(), dlg._ph_server_lbl.text()
H.check(here.startswith('{} photo(s)'.format(now['archived_count']))
        and 'server has dropped' in here,
        'it shows what this PC has archived: "{}"'.format(here))
H.check(server.startswith('{} photo(s)'.format(now['on_server_count']))
        and now['on_server_count'] > 0,
        'and what the server is still holding: "{}"'.format(server))
H.check('kB' in here or 'MB' in here or 'bytes' in here,
        'with a size, not just a count')
H.check('could not be verified' in dlg._ph_warn_lbl.text(),
        'the unverifiable photo is called out: "{}"'
        .format(dlg._ph_warn_lbl.text()[:80]))
H.check(dlg._sweep_btn.isEnabled(), 'the "free space" action is offered')

dlg._on_storage({'on_server_count': 2, 'on_server_bytes': 760,
                 'archived_count': 4, 'archived_bytes': 1520,
                 'disk': {'total_mb': 1024.0, 'used_mb': 930.0,
                          'free_mb': 94.0, 'used_pct': 90.8}})
disk = dlg._ph_disk_lbl.text()
H.check('930' in disk and '1024' in disk and '91%' in disk,
        "the server's own disk figure is shown: \"{}\"".format(disk))
H.check('bold' in dlg._ph_disk_lbl.styleSheet(),
        'and a nearly-full disk is highlighted')
dlg.close()

H.finish()
