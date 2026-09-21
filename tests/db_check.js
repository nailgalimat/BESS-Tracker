/**
 * The phone's IndexedDB wrapper, run for real: db.js is loaded into a sandbox
 * with a small in-memory IndexedDB that behaves like the real one — requests
 * complete on their own turn of the event loop, and a readwrite transaction
 * holds its store until it is done.
 *
 * What this pins down (M6): a checklist answer ticked while the checklist is
 * being uploaded must not be marked "sent". updateChecklist read in one
 * transaction and wrote in another, so an answer that arrived in between was
 * thrown away; and the run was stamped 'synced' even when what was uploaded
 * was no longer what the phone held, so the answer never left the phone at
 * all.
 */
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const DBJS = path.join(__dirname, '..', 'backend', 'static', 'js', 'db.js');
let failures = 0;
function check(cond, msg) {
  console.log((cond ? '   ok    ' : '   FAIL  ') + msg);
  if (!cond) failures++;
}

// ── a small IndexedDB ──────────────────────────────────────────────────────
// Enough of the real thing for db.js: keyPath stores, indexes, cursors, and
// transactions that run one at a time per store, each request completing on
// its own microtask the way the browser does.
function fakeIndexedDB() {
  const data = {};                       // store name -> { key: value }
  const meta = {};                       // store name -> { keyPath, indexes }
  let busy = Promise.resolve();          // serialises transactions, as IDB does

  function objectStore(name, mode, gate) {
    const rows = data[name];
    const keyPath = meta[name].keyPath;
    const fire = fn => {
      const req = { onsuccess: null, onerror: null, result: undefined };
      gate.push(() => {
        try {
          req.result = fn();
          if (req.onsuccess) req.onsuccess({ target: req });
        } catch (err) {
          if (req.onerror) req.onerror({ target: { error: err } });
        }
      });
      return req;
    };
    const store = {
      get: k => fire(() => rows[k]),
      getAll: () => fire(() => Object.values(rows)),
      put: v => fire(() => {
        if (mode !== 'readwrite') throw new Error('read-only transaction');
        rows[v[keyPath]] = v;
        return v[keyPath];
      }),
      delete: k => fire(() => { delete rows[k]; }),
      createIndex() {},
      index: () => ({ getAll: () => fire(() => Object.values(rows)),
                      openCursor: () => fire(() => null) }),
      openCursor: () => fire(() => null),
    };
    return store;
  }

  return {
    open(name, version) {
      const req = { onsuccess: null, onerror: null, onupgradeneeded: null };
      const db = {
        objectStoreNames: { contains: n => n in data },
        createObjectStore(n, opts) {
          data[n] = {};
          meta[n] = { keyPath: opts.keyPath };
          return objectStore(n, 'readwrite', []);
        },
        transaction(names, mode = 'readonly') {
          const gate = [];
          const tx = { oncomplete: null, onerror: null,
                       objectStore: n => objectStore(n, mode, gate) };
          // The transaction starts once the one before it has finished, and
          // stays open while the caller is still queueing requests — which is
          // what makes read-then-write inside one transaction atomic, and
          // read-then-write across two transactions not.
          busy = busy.then(async () => {
            for (;;) {
              while (gate.length) {
                gate.shift()();
                await null;              // each request on its own turn
              }
              await new Promise(r => setTimeout(r, 0));
              if (!gate.length) break;
            }
            if (tx.oncomplete) tx.oncomplete({});
          });
          return tx;
        },
      };
      setTimeout(() => {
        if (req.onupgradeneeded) req.onupgradeneeded({ target: { result: db } });
        req.result = db;
        if (req.onsuccess) req.onsuccess({ target: { result: db } });
      }, 0);
      return req;
    },
  };
}

function sandbox() {
  const ctxObj = { console, setTimeout, clearTimeout,
                   indexedDB: fakeIndexedDB(), IDBKeyRange: { only: v => v } };
  ctxObj.window = ctxObj;
  ctxObj.self = ctxObj;
  vm.createContext(ctxObj);
  vm.runInContext(fs.readFileSync(DBJS, 'utf8'), ctxObj, { filename: 'db.js' });
  ctxObj.DB = vm.runInContext('DB', ctxObj);
  return ctxObj;
}

const RUN = {
  uuid: 'run-12', project_id: 7, template_uuid: 'tpl-1', plant_block: 12,
  campaign: 'PM Sep 2026', run_date: '2026-09-21', status: 'In Progress',
  ptw_no: '', serial: '', filled_by: '', sync_status: 'local',
  updated_at: '2026-09-21T09:00:00.000Z',
  results: { 11: { result: 'OK', comment: '' },
             12: { result: '', comment: '' } },
};

(async () => {
  const { DB } = sandbox();

  await DB.saveChecklist(JSON.parse(JSON.stringify(RUN)));
  let got = await DB.getChecklist('run-12');
  check(got && got.plant_block === 12, 'the checklist is stored and read back');
  check((await DB.getPendingChecklists()).length === 1,
        'and it is waiting to be sent');

  // a plain patch still works
  await DB.updateChecklist('run-12', { ptw_no: 'PTW-2609-140' });
  got = await DB.getChecklist('run-12');
  check(got.ptw_no === 'PTW-2609-140' && got.plant_block === 12,
        'a patch changes what it names and leaves the rest alone');

  // ── the race: an answer ticked while the upload is in flight ────────────
  // The sync reads the run, POSTs it (seconds), and marks it sent. Anything
  // the technician ticks in between is on the phone and NOT on the server —
  // so it must stay unsent.
  const sending = await DB.getChecklist('run-12');       // what the sync uploads
  const ticked = Object.assign({}, sending, {
    results: Object.assign({}, sending.results,
                           { 12: { result: 'NOK', comment: 'fan noisy' } }),
    sync_status: 'local', updated_at: '2026-09-21T09:00:05.000Z',
  });
  await DB.saveChecklist(ticked);                        // ← the thumb, mid-upload
  await DB.updateChecklist('run-12', { sync_status: 'synced', last_error: '' },
                           sending.updated_at);          // ← the upload finishes

  const after = await DB.getChecklist('run-12');
  check(after.results['12'].result === 'NOK'
        && after.results['12'].comment === 'fan noisy',
        'the answer ticked during the upload is still on the phone');
  check(after.sync_status !== 'synced',
        'and the run is NOT marked sent — the server has not seen that answer: '
        + after.sync_status);
  check((await DB.getPendingChecklists()).length === 1,
        'so the next sync picks it up');

  // when nothing changed underneath, the run IS marked sent
  const stable = await DB.getChecklist('run-12');
  await DB.updateChecklist('run-12', { sync_status: 'synced', last_error: '' },
                           stable.updated_at);
  check((await DB.getChecklist('run-12')).sync_status === 'synced',
        'an upload nobody interrupted does mark the run sent');
  check((await DB.getPendingChecklists()).length === 0,
        'and it stops being pending');

  check(await DB.updateChecklist('gone', { sync_status: 'synced' }) === null,
        'patching a run this phone does not have answers null, not a new row');

  console.log(failures ? 'RESULT FAIL (' + failures + ' check(s))' : 'RESULT PASS');
  process.exit(0);
})();
