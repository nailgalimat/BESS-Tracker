"""services/photo_retention_service.py — this desktop is the photo archive.

The server is a staging post. A phone uploads a photo, this desktop downloads
it, and once the local file's SHA-256 matches the hash the server stored at
upload time, the desktop tells the server it may delete its own copy
(``POST /images/archived``). The server keeps the row and all of its metadata,
so a record still shows the photo exists — only the bytes are gone from there.

Three rules this module exists to enforce:

  * **Verified, not assumed.** A photo is confirmed only when the local file is
    present *and* hashes to the stored sha256. Size is not enough: two photos on
    one record both named ``image.jpg`` used to overwrite each other locally, so
    a file of exactly the right size can be the wrong photo. That has actually
    happened in this database, and the only good copy of the loser was the
    server's — deleting it on a size check would have destroyed it.
  * **A mismatch is repaired or reported, never confirmed.** Because the server
    still has the file at that point, the manual sweep fixes a mismatch by
    downloading it again to a name that cannot collide (the automatic pass does
    not, or a photo that will never verify would re-download itself every minute
    for ever). Either way it is named in the sweep's result and waits in
    ``sync_inbox`` so the failure is reported until someone looks at it.
  * **Nothing is deleted on a guess.** Only ``server_archived_at IS NULL`` rows
    are offered, so an interrupted confirm simply retries on the next sweep, and
    a repeat confirm is a no-op on the server.

``server_archived_at`` marks the SERVER's copy as gone. The local file is the
archive and is never deleted by anything here.

**A clip goes through all of this unchanged.** Short video is a row in the same
``work_log_images`` table, so it is verified by the same sha256, confirmed by
the same call, counted in the same figures and freed from the same disk — there
is no second retention path and there must not be. One thing is added: a clip's
poster frame is a second file the server also deletes on confirm, and unlike a
photo's thumbnail it cannot be regenerated here (no ffmpeg, on either side). So
a clip whose poster this desktop was told about and does not hold is **not**
confirmed — state ``noposter`` — and the manual sweep fetches it. The clip's own
bytes are still judged exactly as a photo's: nothing is confirmed unverified.
"""

import os
from typing import Optional

from database.db_manager import get_connection

# The server caps one confirm call at 500 ids; stay under it.
CONFIRM_BATCH = 200

# Photos hashed per automatic pass at the end of a sync. A sync runs every 60 s,
# so the cap is what stops the first sweep from reading hundreds of megabytes in
# one cycle — and in the steady state there are only a few new photos anyway.
# Set it to 0 to leave the server's files alone until the button is pressed.
AUTO_LIMIT = 60

_REASONS = {
    'missing':  'the local file is not there',
    'mismatch': 'the local file does not match the stored hash',
    'nosha':    'no hash was stored, so the copy cannot be verified',
    'noposter': "the clip's poster frame is not on this computer, and the "
                'server is the only place it can still be fetched from',
}


# ── Reading rows ──────────────────────────────────────────────────────────────

def _fetch(where: str, params: tuple = (), limit: Optional[int] = None) -> list:
    sql = ("SELECT id, work_log_id, file_path, thumbnail_path, filename, "
           "       size_bytes, sha256, uploaded_at, upload_status, "
           "       server_archived_at, mime_type, duration_ms "
           "FROM work_log_images WHERE " + where + " ORDER BY uploaded_at")
    if limit:
        sql += " LIMIT %d" % int(limit)
    conn = get_connection()
    try:
        return [dict(r) for r in conn.execute(sql, params)]
    finally:
        conn.close()


def _candidates(limit: Optional[int] = None) -> list:
    """Photos the server may still be holding a file for.

    ``upload_status='uploaded'`` only: a 'local' row has not been pushed yet
    (the server has nothing to free, and stamping it now would stop the sweep
    ever offering it once it *is* pushed), and a 'remote' row has no local file
    to verify.
    """
    return _fetch("upload_status='uploaded' AND server_archived_at IS NULL",
                  limit=limit)


# ── Verifying one photo ───────────────────────────────────────────────────────

def _sha256(path: str) -> str:
    import hashlib
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(65536), b''):
            h.update(chunk)
    return h.hexdigest()


def verify(row: dict) -> str:
    """'ok' | 'missing' | 'mismatch' | 'nosha' | 'noposter' for one row.

    'noposter' only ever applies to a clip, and only when the row already names
    a poster file that is not on disk. Confirming then would have the server
    delete the poster as well as the clip, and nothing here could ever make
    another one. A clip the server never sent a poster for (`thumbnail_path`
    empty) is judged exactly like a photo — the clip is the evidence, and
    holding it on the server for ever over a missing still is the failure the
    1 GB disk cannot afford.
    """
    path = row.get('file_path') or ''
    if not (path and os.path.isfile(path)):
        return 'missing'
    want = (row.get('sha256') or '').strip().lower()
    if not want:
        return 'nosha'
    try:
        got = _sha256(path)
    except OSError:
        return 'missing'
    if got != want:
        return 'mismatch'
    poster = row.get('thumbnail_path') or ''
    if poster and not os.path.isfile(poster):
        from services.image_service import is_video
        if is_video(row):
            return 'noposter'
    return 'ok'


# ── Repairing a mismatch ──────────────────────────────────────────────────────

def _repair_dest(row: dict) -> str:
    """A local path that cannot be the one another photo already occupies."""
    from services.image_service import get_image_dir
    folder = get_image_dir(row['work_log_id'])
    name = row.get('filename') or (row['id'] + '.jpg')
    stem, ext = os.path.splitext(os.path.basename(name))
    return os.path.join(folder, '{}_{}{}'.format(stem or 'photo',
                                                 str(row['id'])[:8], ext or '.jpg'))


def _refetch_poster(row: dict) -> bool:
    """Fetch a clip's poster frame again, so the clip can be confirmed."""
    try:
        from services.image_service import ensure_poster
    except Exception:                                        # noqa: BLE001
        return False
    try:
        # ensure_poster short-circuits on a poster that is already there, and
        # this row's is not — its path is stale, so clear it and let the
        # downloader choose the name. It writes the new path to the database;
        # copy it back so the re-verify in the sweep looks at the new file.
        probe = dict(row, thumbnail_path='')
        if not ensure_poster(probe):
            return False
        row['thumbnail_path'] = probe.get('thumbnail_path') or ''
        return True
    except Exception:                                        # noqa: BLE001
        return False


def repair(row: dict, download=None) -> bool:
    """Download the server's copy again, to a name that cannot collide, and
    point the row at it — but only if what arrives hashes correctly. The old
    local file is left alone: it is another photo's, which is how the mismatch
    happened in the first place.
    """
    if download is None:
        from services.sync_client import download_image_file as download
    dest = _repair_dest(row)
    try:
        if not download(row['id'], dest):
            return False
    except Exception:                                        # noqa: BLE001
        return False
    if not os.path.isfile(dest):
        return False
    want = (row.get('sha256') or '').strip().lower()
    try:
        if not want or _sha256(dest) != want:
            os.remove(dest)          # still wrong: keep nothing misleading
            return False
    except OSError:
        return False

    thumb = os.path.splitext(dest)[0] + '_thumb.jpg'
    try:
        from services.image_service import _generate_thumbnail
        if not _generate_thumbnail(dest, thumb):
            thumb = None
    except Exception:                                        # noqa: BLE001
        thumb = None

    conn = get_connection()
    try:
        conn.execute("UPDATE work_log_images SET file_path=?, thumbnail_path=? WHERE id=?",
                     (dest, thumb, row['id']))
        conn.commit()
    finally:
        conn.close()
    row['file_path'] = dest
    row['thumbnail_path'] = thumb
    return True


# ── Stamping ──────────────────────────────────────────────────────────────────

def _stamp(image_ids: list) -> None:
    if not image_ids:
        return
    conn = get_connection()
    try:
        conn.executemany(
            "UPDATE work_log_images SET server_archived_at=datetime('now') "
            "WHERE id=? AND server_archived_at IS NULL",
            [(i,) for i in image_ids])
        conn.commit()
    finally:
        conn.close()


# ── The sweep ─────────────────────────────────────────────────────────────────

def sweep(limit: Optional[int] = None, repair_mismatches: bool = True,
          confirm=None, download=None, progress=None) -> dict:
    """Verify local copies and tell the server which files it may delete.

    Safe to run as often as you like: a photo already confirmed is never looked
    at again, and a photo that cannot be verified is never confirmed.

    `repair_mismatches` is off in the automatic pass at the end of a sync, so a
    photo that will never verify cannot have itself re-downloaded every minute
    for ever. The button on the Synchronisation screen leaves it on: repairing is
    then one deliberate attempt per press, with the result in front of the person
    who pressed it.

    Returns {'checked', 'confirmed', 'already', 'not_on_server', 'repaired',
             'freed_bytes', 'errors', 'skipped': [{id, filename, reason}],
             'remaining'} — `remaining` is how many candidates are still
    unconfirmed after this pass, so a bounded run can say there is more to do.
    """
    if confirm is None:
        from services.sync_client import confirm_server_archive as confirm

    rep = {'checked': 0, 'confirmed': 0, 'already': 0, 'not_on_server': 0,
           'repaired': 0, 'freed_bytes': 0, 'errors': 0, 'skipped': [],
           'remaining': 0}
    if limit is not None and limit <= 0:
        # AUTO_LIMIT = 0 switches the automatic pass off; only the button sweeps.
        # (limit=None is the unlimited one, which is what the button passes.)
        return rep
    rows = _candidates(limit)
    ready = []
    # What is already parked, read once: clearing the inbox per photo would be
    # one connection each for hundreds of photos that were never in it.
    parked = {w['item_id'] for w in _waiting()}

    for i, row in enumerate(rows, 1):
        if progress:
            try:
                progress(i, len(rows))
            except Exception:                                # noqa: BLE001
                pass
        rep['checked'] += 1
        state = verify(row)
        if state == 'mismatch' and repair_mismatches:
            if repair(row, download=download):
                rep['repaired'] += 1
                state = verify(row)
        if state == 'noposter' and repair_mismatches:
            # Same rule as a mismatch: the deliberate sweep tries once, with the
            # result in front of the person who pressed the button; the automatic
            # pass never re-fetches, or a poster that will never arrive would be
            # asked for every 60 s for ever.
            if _refetch_poster(row):
                rep['repaired'] += 1
                state = verify(row)
        if state == 'ok':
            if row['id'] in parked:
                _inbox_clear(row['id'])
            ready.append(row)
            continue
        rep['skipped'].append({'id': row['id'],
                               'filename': row.get('filename') or row['id'],
                               'work_log_id': row.get('work_log_id') or '',
                               'reason': _REASONS.get(state, state)})
        if state == 'noposter':
            _inbox_park(row, _REASONS['noposter'])
        if state == 'mismatch':
            # Surface it: a corrupted copy is exactly when deleting the only
            # good one would be unforgivable, so it is reported until resolved.
            _inbox_park(row, _REASONS['mismatch'])

    for start in range(0, len(ready), CONFIRM_BATCH):
        chunk = ready[start:start + CONFIRM_BATCH]
        try:
            resp = confirm([r['id'] for r in chunk]) or {}
        except Exception as ex:                              # noqa: BLE001
            rep['errors'] += 1
            rep['error_text'] = str(ex)[:200]
            break                       # unconfirmed: retried on the next sweep
        by_id = {r.get('id'): r for r in (resp.get('results') or [])}
        done = []
        for r in chunk:
            outcome = (by_id.get(r['id']) or {}).get('outcome', '')
            if outcome == 'archived':
                rep['confirmed'] += 1
                done.append(r['id'])
            elif outcome == 'already':
                rep['already'] += 1
                done.append(r['id'])
            elif outcome == 'not_found':
                # The server has no such photo, so it is holding no file for it.
                # Stamped so the sweep stops re-hashing this file for ever.
                rep['not_on_server'] += 1
                done.append(r['id'])
            else:                        # 'forbidden' or nothing came back
                rep['errors'] += 1
        rep['freed_bytes'] += int(resp.get('freed_bytes') or 0)
        _stamp(done)

    rep['remaining'] = len(_candidates())
    return rep


# ── sync_inbox: where a failure waits so it is not silently forgotten ─────────

_KIND = 'photo_verify'


def _inbox_park(row: dict, reason: str) -> None:
    try:
        from services.sync_client import inbox_put
        inbox_put(_KIND, {'id': row['id'], 'filename': row.get('filename') or '',
                          'work_log_id': row.get('work_log_id') or ''}, reason)
    except Exception:                                        # noqa: BLE001
        pass


def _inbox_clear(image_id: str) -> None:
    try:
        from services.sync_client import inbox_clear
        inbox_clear(_KIND, image_id)
    except Exception:                                        # noqa: BLE001
        pass


# ── What the owner sees ───────────────────────────────────────────────────────

def local_status() -> dict:
    """Counts and bytes for the Synchronisation screen.

    'archived' = this desktop holds it and the server has been told it may drop
    its copy. 'on_server' = the server is probably still holding a file, either
    because it has not been verified yet or because verification failed.
    """
    conn = get_connection()
    try:
        row = conn.execute("""
            SELECT
              COUNT(*)                                                    AS total,
              COALESCE(SUM(size_bytes), 0)                                AS total_bytes,
              SUM(CASE WHEN server_archived_at IS NOT NULL THEN 1 ELSE 0 END)
                                                                          AS archived_count,
              COALESCE(SUM(CASE WHEN server_archived_at IS NOT NULL
                                THEN size_bytes ELSE 0 END), 0)           AS archived_bytes,
              SUM(CASE WHEN upload_status='uploaded' AND server_archived_at IS NULL
                       THEN 1 ELSE 0 END)                                 AS on_server_count,
              COALESCE(SUM(CASE WHEN upload_status='uploaded' AND server_archived_at IS NULL
                                THEN size_bytes ELSE 0 END), 0)           AS on_server_bytes,
              SUM(CASE WHEN upload_status='remote' THEN 1 ELSE 0 END)      AS not_downloaded,
              -- Clips, counted apart so the Synchronisation screen can say how
              -- much of the figure is video: a clip is ~3 MB against a photo's
              -- ~0.5 MB, which is exactly what the owner needs to see when the
              -- server's 1 GB disk is the thing under pressure. They are
              -- INSIDE the totals above, not beside them — one pipeline.
              SUM(CASE WHEN LOWER(COALESCE(mime_type,'')) LIKE 'video/%'
                       THEN 1 ELSE 0 END)                                   AS video_count,
              COALESCE(SUM(CASE WHEN LOWER(COALESCE(mime_type,'')) LIKE 'video/%'
                                THEN size_bytes ELSE 0 END), 0)             AS video_bytes,
              SUM(CASE WHEN LOWER(COALESCE(mime_type,'')) LIKE 'video/%'
                        AND server_archived_at IS NOT NULL
                       THEN 1 ELSE 0 END)                                   AS video_archived,
              SUM(CASE WHEN LOWER(COALESCE(mime_type,'')) LIKE 'video/%'
                        AND upload_status='uploaded' AND server_archived_at IS NULL
                       THEN 1 ELSE 0 END)                                   AS video_on_server
            FROM work_log_images
        """).fetchone()
        out = {k: (row[k] or 0) for k in row.keys()}
    finally:
        conn.close()
    out['unverified'] = [
        {'item_id': w['item_id'], 'error': w['error']}
        for w in _waiting()
    ]
    return out


def _waiting() -> list:
    try:
        from services.sync_client import inbox_waiting
        return [w for w in inbox_waiting() if w.get('kind') == _KIND]
    except Exception:                                        # noqa: BLE001
        return []


def human_bytes(n) -> str:
    """'822 MB' — the figure the owner is actually looking for."""
    n = float(n or 0)
    for unit, step in (('GB', 1024 ** 3), ('MB', 1024 ** 2), ('kB', 1024)):
        if n >= step:
            return '{:,.1f} {}'.format(n / step, unit).replace(',', ' ')
    return '{:.0f} bytes'.format(n)


def describe(rep: dict) -> str:
    """The sweep's result as something the owner can read.

    Still worded as photos: a clip is one of them, and the sweep does not treat
    it differently. The Synchronisation screen says how many of the figure are
    clips, which is where that question is actually asked.
    """
    bits = ['Checked {} photo(s).'.format(rep.get('checked', 0))]
    if rep.get('confirmed'):
        bits.append('The server freed {} file(s), {}.'
                    .format(rep['confirmed'], human_bytes(rep.get('freed_bytes'))))
    elif rep.get('checked'):
        bits.append('Nothing new to free.')
    if rep.get('already'):
        bits.append('{} were already archived.'.format(rep['already']))
    if rep.get('repaired'):
        bits.append('{} local copy(ies) were wrong and were downloaded again.'
                    .format(rep['repaired']))
    if rep.get('not_on_server'):
        bits.append('{} are no longer on the server.'.format(rep['not_on_server']))
    if rep.get('skipped'):
        names = ', '.join(s['filename'] for s in rep['skipped'][:5])
        more = '' if len(rep['skipped']) <= 5 else ' …'
        bits.append('SKIPPED and still on the server — {}: {}{}'
                    .format(len(rep['skipped']), names, more))
    if rep.get('errors'):
        bits.append('{} error(s): {}'.format(rep['errors'],
                                             rep.get('error_text', 'see the log')))
    if rep.get('remaining'):
        bits.append('{} still to check.'.format(rep['remaining']))
    return ' '.join(bits)
