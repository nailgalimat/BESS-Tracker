/**
 * db.js — IndexedDB wrapper for BESS Field Log PWA
 * Stores entries, images (base64) and metadata (cursor, last sync).
 */
const DB = (() => {
  const DB_NAME    = 'bess_field_log';
  const DB_VERSION = 3;
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
    const s = await store('events');
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

  async function deleteFieldEvent(id) {
    return wrap((await store('events', 'readwrite')).delete(id));
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
    saveFieldEvent, getPendingFieldEvents, deleteFieldEvent,
    getMeta, setMeta,
  };
})();
