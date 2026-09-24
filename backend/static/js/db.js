/**
 * db.js — IndexedDB wrapper for BESS Field Log PWA
 * Stores entries, images (base64) and metadata (cursor, last sync).
 */
const DB = (() => {
  const DB_NAME    = 'bess_field_log';
  const DB_VERSION = 5;
  let _db = null;

  // ── Open / upgrade ──────────────────────────────────────────────────────────
  function open() {
    if (_db) return Promise.resolve(_db);
    return new Promise((resolve, reject) => {
      const req = indexedDB.open(DB_NAME, DB_VERSION);

      req.onupgradeneeded = e => {
        const db = e.target.result;

        if (!db.objectStoreNames.contains('entries')) {
          const s = db.createObjectStore('entries', { keyPath: 'id' });
          s.createIndex('sync_status', 'sync_status', { unique: false });
          s.createIndex('log_date',    'log_date',    { unique: false });
          s.createIndex('category',    'category',    { unique: false });
          s.createIndex('updated_at',  'updated_at',  { unique: false });
        }

        if (!db.objectStoreNames.contains('images')) {
          const img = db.createObjectStore('images', { keyPath: 'id' });
          img.createIndex('entry_id',      'entry_id',      { unique: false });
          img.createIndex('upload_status', 'upload_status', { unique: false });
        }

        if (!db.objectStoreNames.contains('meta')) {
          db.createObjectStore('meta', { keyPath: 'key' });
        }

        // v2: offline queue of material write-offs
        if (!db.objectStoreNames.contains('writeoffs')) {
          const wo = db.createObjectStore('writeoffs', { keyPath: 'id' });
          wo.createIndex('sync_status', 'sync_status', { unique: false });
        }

        // v3: offline queue of field events (PM / downtime / exclusion)
        if (!db.objectStoreNames.contains('events')) {
          const ev = db.createObjectStore('events', { keyPath: 'id' });
          ev.createIndex('sync_status', 'sync_status', { unique: false });
        }

        // v4: the PM checklists assigned to this project, filled offline.
        // Keyed by the run uuid the desktop planned, so the same checklist has
        // one name on the phone, on the server and in the office.
        if (!db.objectStoreNames.contains('checklists')) {
          const cl = db.createObjectStore('checklists', { keyPath: 'uuid' });
          cl.createIndex('sync_status', 'sync_status', { unique: false });
          cl.createIndex('project_id',  'project_id',  { unique: false });
        }

        // v5: the office's action items assigned to this person. No block, no
        // hours — organisational work, kept apart from the plant's records so
        // that nothing here can end up in the customer's monthly report.
        if (!db.objectStoreNames.contains('actions')) {
          const ac = db.createObjectStore('actions', { keyPath: 'uuid' });
          ac.createIndex('sync_status', 'sync_status', { unique: false });
          ac.createIndex('project_id',  'project_id',  { unique: false });
        }
      };

      req.onsuccess = e => { _db = e.target.result; resolve(_db); };
      req.onerror   = e => reject(e.target.error);
    });
  }

  async function store(name, mode = 'readonly') {
    const db = await open();
    return db.transaction(name, mode).objectStore(name);
  }

  function wrap(req) {
    return new Promise((res, rej) => {
      req.onsuccess = e => res(e.target.result);
      req.onerror   = e => rej(e.target.error);
    });
  }

  // ── Entries ─────────────────────────────────────────────────────────────────

  async function saveEntry(entry) {
    return wrap((await store('entries', 'readwrite')).put(entry));
  }

  async function getEntry(id) {
    return wrap((await store('entries')).get(id));
  }

  async function getAllEntries() {
    return wrap((await store('entries')).getAll());
  }

  async function getPendingEntries() {
    const s = await store('entries');
    return new Promise((res, rej) => {
      const results = [];
      const req = s.openCursor();
      req.onsuccess = e => {
        const cur = e.target.result;
        if (!cur) { res(results); return; }
        if (['local', 'pending'].includes(cur.value.sync_status)) {
          results.push(cur.value);
        }
        cur.continue();
      };
      req.onerror = e => rej(e.target.error);
    });
  }

  async function markSynced(id, serverVersion) {
    const s = await store('entries', 'readwrite');
    const entry = await wrap(s.get(id));
    if (entry) {
      entry.sync_status = 'synced';
      if (serverVersion != null) entry.version = serverVersion;
      return wrap(s.put(entry));
    }
  }

  async function deleteEntry(id) {
    return wrap((await store('entries', 'readwrite')).delete(id));
  }

  // ── Images ──────────────────────────────────────────────────────────────────

  async function saveImage(img) {
    return wrap((await store('images', 'readwrite')).put(img));
  }

  async function getImagesForEntry(entryId) {
    const s = await store('images');
    return wrap(s.index('entry_id').getAll(entryId));
  }

  async function deleteImage(id) {
    return wrap((await store('images', 'readwrite')).delete(id));
  }

  async function deleteImagesForEntry(entryId) {
    const imgs = await getImagesForEntry(entryId);
    for (const img of imgs) await deleteImage(img.id);
  }

  async function getPendingImages() {
    const s = await store('images');
    return new Promise((res, rej) => {
      const results = [];
      const req = s.index('upload_status').openCursor(IDBKeyRange.only('local'));
      req.onsuccess = e => {
        const cur = e.target.result;
        if (!cur) { res(results); return; }
        results.push(cur.value);
        cur.continue();
      };
      req.onerror = e => rej(e.target.error);
    });
  }

  // ── Write-offs (offline queue) ───────────────────────────────────────────────

  async function saveWriteoff(wo) {
    return wrap((await store('writeoffs', 'readwrite')).put(wo));
  }

  async function getPendingWriteoffs() {
    const s = await store('writeoffs');
    return new Promise((res, rej) => {
      const results = [];
      const req = s.openCursor();
      req.onsuccess = e => {
        const cur = e.target.result;
        if (!cur) { res(results); return; }
        if (cur.value.sync_status !== 'synced') results.push(cur.value);
        cur.continue();
      };
      req.onerror = e => rej(e.target.error);
    });
  }

  async function deleteWriteoff(id) {
    return wrap((await store('writeoffs', 'readwrite')).delete(id));
  }

  // ── Field events (offline queue) ─────────────────────────────────────────────

  async function saveFieldEvent(ev) {
    return wrap((await store('events', 'readwrite')).put(ev));
  }

  async function getPendingFieldEvents() {
    const all = await getAllFieldEvents();
    return all.filter(e => e.sync_status !== 'synced');
  }

  // Every event ever recorded on this phone, newest first. Sent events are
  // KEPT — a PM record that vanishes the moment it uploads leaves the
  // engineer with no way to see what they reported.
  async function getAllFieldEvents() {
    const s = await store('events');
    return new Promise((res, rej) => {
      const results = [];
      const req = s.openCursor();
      req.onsuccess = e => {
        const cur = e.target.result;
        if (!cur) {
          results.sort((a, b) => String(b.created_at || '')
                                  .localeCompare(String(a.created_at || '')));
          res(results);
          return;
        }
        results.push(cur.value);
        cur.continue();
      };
      req.onerror = e => rej(e.target.error);
    });
  }

  async function updateFieldEvent(id, patch) {
    const s = await store('events', 'readwrite');
    const rec = await wrap(s.get(id));
    if (!rec) return null;
    const next = Object.assign({}, rec, patch);
    await wrap((await store('events', 'readwrite')).put(next));
    return next;
  }

  async function deleteFieldEvent(id) {
    return wrap((await store('events', 'readwrite')).delete(id));
  }

  // ── PM checklists ───────────────────────────────────────────────────────────
  // A run is kept whatever its state: sent ones stay so a mistake can still be
  // corrected on site, the way the paper copy could be.

  async function saveChecklist(run) {
    return wrap((await store('checklists', 'readwrite')).put(run));
  }

  async function getChecklist(uuid) {
    return wrap((await store('checklists')).get(uuid));
  }

  async function getAllChecklists() {
    const all = await wrap((await store('checklists')).getAll());
    all.sort((a, b) => String(b.run_date || '').localeCompare(String(a.run_date || ''))
                       || (a.plant_block || 0) - (b.plant_block || 0));
    return all;
  }

  async function getPendingChecklists() {
    const all = await getAllChecklists();
    return all.filter(r => r.sync_status && r.sync_status !== 'synced');
  }

  /** Patch one checklist run, read and write in ONE transaction.
   *
   *  Two transactions meant an answer ticked between the read and the write
   *  was thrown away. And `expectUpdatedAt` guards the longer race: the
   *  upload takes seconds, and an answer ticked while it was in flight used
   *  to be marked 'synced' and never left the phone. Pass the stamp the
   *  record had when the upload started; if it has moved, the patch is
   *  skipped and the run stays waiting to send.
   */
  async function updateChecklist(uuid, patch, expectUpdatedAt) {
    const db = await open();
    return new Promise((res, rej) => {
      const tx = db.transaction('checklists', 'readwrite');
      const s  = tx.objectStore('checklists');
      const get = s.get(uuid);
      get.onsuccess = () => {
        const rec = get.result;
        if (!rec) { res(null); return; }
        if (expectUpdatedAt !== undefined && expectUpdatedAt !== null
            && String(rec.updated_at || '') !== String(expectUpdatedAt)) {
          res(rec);                  // changed underneath — leave it as it is
          return;
        }
        const next = Object.assign({}, rec, patch);
        const put = s.put(next);
        put.onsuccess = () => res(next);
        put.onerror   = e => rej(e.target.error);
      };
      get.onerror = e => rej(e.target.error);
      tx.onerror  = e => rej(e.target.error);
    });
  }

  // ── Action items ────────────────────────────────────────────────────────────
  // The office's list, the part of it that was given to this person. Kept
  // whatever its state, like a checklist: a note is corrected on site until
  // the office exports the sheet.

  async function saveAction(item) {
    return wrap((await store('actions', 'readwrite')).put(item));
  }

  async function getAction(uuid) {
    return wrap((await store('actions')).get(uuid));
  }

  async function getAllActions() {
    const all = await wrap((await store('actions')).getAll());
    // soonest due first; an item nobody dated is not more urgent than one due
    // tomorrow, so it goes last
    all.sort((a, b) => (a.due_date ? 0 : 1) - (b.due_date ? 0 : 1)
                       || String(a.due_date || '').localeCompare(String(b.due_date || ''))
                       || (a.seq || 0) - (b.seq || 0));
    return all;
  }

  async function getPendingActions() {
    const all = await getAllActions();
    return all.filter(a => a.sync_status && a.sync_status !== 'synced');
  }

  /** Patch one action item, read and write in ONE transaction — the same race
   *  the checklists have: an upload takes seconds, and a note typed while it
   *  was in flight must not be marked 'synced' without ever leaving the phone.
   *  Pass the stamp the record had when the upload started. */
  async function updateAction(uuid, patch, expectUpdatedAt) {
    const db = await open();
    return new Promise((res, rej) => {
      const tx = db.transaction('actions', 'readwrite');
      const s  = tx.objectStore('actions');
      const get = s.get(uuid);
      get.onsuccess = () => {
        const rec = get.result;
        if (!rec) { res(null); return; }
        if (expectUpdatedAt !== undefined && expectUpdatedAt !== null
            && String(rec.updated_at || '') !== String(expectUpdatedAt)) {
          res(rec);                  // changed underneath — leave it as it is
          return;
        }
        const next = Object.assign({}, rec, patch);
        const put = s.put(next);
        put.onsuccess = () => res(next);
        put.onerror   = e => rej(e.target.error);
      };
      get.onerror = e => rej(e.target.error);
      tx.onerror  = e => rej(e.target.error);
    });
  }

  // ── Meta (cursor, settings) ─────────────────────────────────────────────────

  async function getMeta(key, defaultVal = null) {
    const rec = await wrap((await store('meta')).get(key));
    return rec ? rec.value : defaultVal;
  }

  async function setMeta(key, value) {
    return wrap((await store('meta', 'readwrite')).put({ key, value }));
  }

  // ── Public API ───────────────────────────────────────────────────────────────
  return {
    saveEntry, getEntry, getAllEntries, getPendingEntries,
    markSynced, deleteEntry,
    saveImage, getImagesForEntry, deleteImage,
    deleteImagesForEntry, getPendingImages,
    saveWriteoff, getPendingWriteoffs, deleteWriteoff,
    saveFieldEvent, getPendingFieldEvents, getAllFieldEvents,
    updateFieldEvent, deleteFieldEvent,
    saveChecklist, getChecklist, getAllChecklists, getPendingChecklists,
    updateChecklist,
    saveAction, getAction, getAllActions, getPendingActions, updateAction,
    getMeta, setMeta,
  };
})();
