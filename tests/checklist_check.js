/**
 * The phone's checklist screen, run for real: app.js is loaded into a sandbox
 * with a recording DOM, a memory IndexedDB and a fake server, and the checklist
 * is filled the way a thumb fills it. Prints one "ok"/"FAIL" line per check,
 * then RESULT.
 *
 * Checked: the items are shown grouped with OK / NOK / N/A and a comment, an
 * item left out of the campaign cannot be ticked, every tap is stored at once,
 * progress counts only what is in scope, the answers are sent to the server,
 * and a checklist with unsent answers is not overwritten by the server's copy.
 */
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const APP = path.join(__dirname, '..', 'backend', 'static', 'js', 'app.js');
let failures = 0;
function check(cond, msg) {
  console.log((cond ? '   ok    ' : '   FAIL  ') + msg);
  if (!cond) failures++;
}

const RUN = {
  uuid: 'run-12', project_id: 7, template_uuid: 'tpl-1', plant_block: 12,
  campaign: 'PM Sep 2026', run_date: '2026-09-21', status: 'In Progress',
  ptw_no: '', serial: 'A2561724523', filled_by: '', sync_status: 'synced',
  results: { '11': { result: '', comment: '' },
             '12': { result: '', comment: '' },
             '13': { result: 'Excluded', comment: 'RMU not in this PM' } },
};
const TPL = {
  uuid: 'tpl-1', name: 'PCS Checklist', kind: 'PCS', items: [
    { item_id: 11, s_no: '1', equipment: 'PCS', activity: 'Visual', text: 'Check the cabinet door seals' },
    { item_id: 12, s_no: '2', equipment: 'PCS', activity: 'Visual', text: 'Check the cooling fans' },
    { item_id: 13, s_no: '3', equipment: 'RMU', activity: 'Visual', text: 'Check the RMU' },
  ],
};

function element() {
  return {
    value: '', textContent: '', innerHTML: '', disabled: false, style: {},
    dataset: {}, classList: { add() {}, remove() {}, toggle() {} },
    querySelectorAll: () => [], appendChild() {}, addEventListener() {},
  };
}

function sandbox(server) {
  const els = {};
  const el = id => (els[id] || (els[id] = element()));
  const store = { checklists: {}, meta: {} };
  const DB = {
    async getMeta(k, d) { return k in store.meta ? store.meta[k] : d; },
    async setMeta(k, v) { store.meta[k] = v; },
    async saveChecklist(r) { store.checklists[r.uuid] = r; },
    async getChecklist(u) { return store.checklists[u] || null; },
    async getAllChecklists() { return Object.values(store.checklists); },
    async getPendingChecklists() {
      return Object.values(store.checklists)
        .filter(r => r.sync_status && r.sync_status !== 'synced');
    },
    async updateChecklist(u, patch) {
      const cur = store.checklists[u];
      if (!cur) return null;
      return (store.checklists[u] = Object.assign({}, cur, patch));
    },
    async getAllEntries() { return []; },
    async getAllFieldEvents() { return []; },
    async getPendingEntries() { return []; },
    async getPendingWriteoffs() { return []; },
    async getPendingFieldEvents() { return []; },
  };
  const ctxObj = {
    console, setTimeout, clearTimeout, setInterval, clearInterval,
    navigator: { onLine: true, serviceWorker: { addEventListener() {} } },
    localStorage: {
      _d: { username: 'tech1', last_project_id: '7' },
      getItem(k) { return this._d[k] === undefined ? null : this._d[k]; },
      setItem(k, v) { this._d[k] = v; }, removeItem(k) { delete this._d[k]; },
    },
    document: {
      addEventListener() {}, getElementById: el, querySelectorAll: () => [],
      createElement: () => element(), body: { classList: { toggle() {} } },
    },
    alert() {}, fetch: () => Promise.reject(new Error('no network here')),
    indexedDB: {}, crypto: { randomUUID: () => 'uuid-' + Math.random() },
    // each sandbox gets its own handle on the same server, so a test that
    // makes one phone fail does not break the next phone
    scrollTo() {}, DB, API: Object.assign({}, server),
  };
  ctxObj.window = ctxObj;
  ctxObj.self = ctxObj;
  vm.createContext(ctxObj);
  vm.runInContext(fs.readFileSync(APP, 'utf8'), ctxObj, { filename: 'app.js' });
  ctxObj.App = vm.runInContext('App', ctxObj);
  ctxObj._els = els;
  ctxObj._store = store;
  return ctxObj;
}

(async () => {
  // The server, as routers/checklists.py behaves: a POST merges the answers
  // into its copy, and the GET that follows hands that copy back.
  const posted = [];
  const onServer = JSON.parse(JSON.stringify(RUN));
  const server = {
    async getProjects() { return [{ id: 7, name: 'TK', num_blocks: 16 }]; },
    async getChecklists() {
      return JSON.parse(JSON.stringify({ templates: [TPL], runs: [onServer] }));
    },
    async postChecklistResults(uuid, payload) {
      posted.push({ uuid, payload });
      Object.assign(onServer.results, payload.results || {});
      onServer.status = payload.status || onServer.status;
      onServer.ptw_no = payload.ptw_no || onServer.ptw_no;
      onServer.filled_by = payload.filled_by || onServer.filled_by;
      return onServer;
    },
  };
  const ctx = sandbox(server);
  const App = ctx.App;

  // ── the list arrives from the server and is kept on the phone ────────────
  await App.goChecklists();
  check(ctx._store.checklists['run-12'] &&
        ctx._store.checklists['run-12'].sync_status === 'synced',
        'the assigned checklist is stored on the phone');
  const list = ctx._els['cl-list'].innerHTML;
  check(list.includes('Block 12') && list.includes('PM Sep 2026')
        && list.includes('PCS Checklist'),
        'the list shows the block, the campaign and which checklist it is');
  check(list.includes('0 of 2 done') && list.includes('1 not in this campaign'),
        'progress counts only what is in scope: ' +
        (list.match(/\d+ of \d+ done[^<]*/) || [''])[0]);

  // ── one checklist, on the screen ─────────────────────────────────────────
  await App.openChecklist('run-12');
  const items = ctx._els['cl-items'].innerHTML;
  check(items.includes('Check the cabinet door seals')
        && items.includes('Check the cooling fans'),
        'the items are on the screen, with their text');
  check((items.match(/class="cl-group"/g) || []).length === 2,
        'grouped by their equipment group');
  check((items.match(/App\.setChecklistResult\(11, '(OK|NOK|N\/A)'\)/g) || []).length === 3,
        'each item offers OK, NOK and N/A');
  check(items.includes('cl-item out') && items.includes('Not in this campaign')
        && items.includes('RMU not in this PM')
        && !items.includes("setChecklistResult(13"),
        'the excluded item is greyed with its note and cannot be ticked');
  check(ctx._els['cl-serial'].value === 'A2561724523',
        'the serial the project knows is filled in');

  // ── filling it ───────────────────────────────────────────────────────────
  await App.setChecklistResult(11, 'OK');
  await App.setChecklistResult(12, 'NOK');
  await App.setChecklistComment(12, 'BESS 3: door seal torn');
  let run = ctx._store.checklists['run-12'];
  check(run.results['11'].result === 'OK' && run.results['12'].result === 'NOK'
        && run.results['12'].comment === 'BESS 3: door seal torn',
        'every tap is stored at once, comment included');
  check(run.sync_status === 'local', 'and the checklist is marked as waiting to send');
  check(run.status === 'Done',
        'with every in-scope item answered it counts as done: ' + run.status);
  check(ctx._els['cl-progress'].innerHTML.includes('2 of 2')
        && ctx._els['cl-progress'].innerHTML.includes('1 NOK'),
        'the progress strip says 2 of 2 · 1 NOK');

  await App.setChecklistResult(11, 'OK');            // the same answer again
  run = ctx._store.checklists['run-12'];
  check(run.results['11'].result === '' && run.status === 'In Progress',
        'tapping the same answer again clears it — a mis-tap is not an answer');
  await App.setChecklistResult(11, 'OK');

  ctx._els['cl-ptw'].value = 'PTW-2609-140';
  ctx._els['cl-signed'].value = 'Field team';
  await App.saveChecklistHead();
  run = ctx._store.checklists['run-12'];
  check(run.ptw_no === 'PTW-2609-140' && run.filled_by === 'Field team',
        'PTW and the signature are kept with it');

  // ── sending, and what the server may overwrite ───────────────────────────
  const r = await App._syncChecklists();
  check(posted.length === 1 && posted[0].uuid === 'run-12'
        && posted[0].payload.results['12'].comment === 'BESS 3: door seal torn'
        && posted[0].payload.ptw_no === 'PTW-2609-140' && !r.error,
        'Send posts the answers, the PTW and the signature to the office');
  check(ctx._store.checklists['run-12'].sync_status === 'synced',
        'and the checklist stops waiting');
  check(ctx._store.checklists['run-12'].results['12'].comment === 'BESS 3: door seal torn',
        'the pull that follows brings back what was sent, not an empty copy');

  // ── nothing is lost when the send fails ──────────────────────────────────
  const solo = sandbox(server);
  await solo.App.goChecklists();
  await solo.App.openChecklist('run-12');
  await solo.App.setChecklistResult(11, 'N/A');
  await solo.App.setChecklistComment(11, 'written in the container, no signal');
  const before = JSON.stringify(solo._store.checklists['run-12'].results);
  solo.API.postChecklistResults = async () => { throw new Error('Offline'); };
  await solo.App._syncChecklists();
  const kept = solo._store.checklists['run-12'];
  check(JSON.stringify(kept.results) === before && kept.sync_status === 'error',
        'a send that fails leaves the answers on the phone, marked not sent');
  check(kept.results['11'].comment === 'written in the container, no signal',
        'and the pull in the same sync does not overwrite them');

  // ── a checklist the office cancelled leaves the phone ────────────────────
  const gone = sandbox(server);
  await gone.App.goChecklists();
  gone.API.getChecklists = async () => ({ templates: [TPL], runs: [] });
  await gone.App._syncChecklists();
  check(!!gone._store.checklists['run-12'].deleted_at,
        'a checklist the office cancelled disappears from the phone');

  const held = sandbox(server);
  await held.App.goChecklists();
  await held.App.openChecklist('run-12');
  await held.App.setChecklistResult(11, 'OK');
  held.API.getChecklists = async () => ({ templates: [TPL], runs: [] });
  held.API.postChecklistResults = async () => { throw new Error('Offline'); };
  await held.App._syncChecklists();
  check(!held._store.checklists['run-12'].deleted_at,
        'but not while this phone still holds answers nobody else has');

  // ── the phone sends WHEN it was filled, not when it found signal ─────────
  // The office may have corrected the checklist while this phone was out of
  // reach; the server can only put the two in order if the phone says when it
  // wrote its copy.
  check(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}/.test(posted[0].payload.updated_at || ''),
        'what is posted carries the phone\'s own stamp: '
        + posted[0].payload.updated_at);

  // ── every page of the assigned list, not the first 200 ───────────────────
  // Three checklists on each of 70 blocks is 210 runs. The server pages at
  // 200, and the phone has to keep asking — the blocks past the cut used to
  // reach no phone at all, with nothing on screen to say so.
  const MANY = [];
  for (let i = 0; i < 210; i++) {
    MANY.push(Object.assign({}, RUN, {
      uuid: 'run-' + String(i).padStart(3, '0'), plant_block: i + 1,
      results: { 11: { result: '', comment: '' } },
    }));
  }
  const asked = [];
  const paged = Object.assign({}, server, {
    async getChecklists(pid, after) {
      asked.push(after || '');
      const from = MANY.findIndex(r => r.uuid > (after || '')) ;
      const page = MANY.slice(from < 0 ? MANY.length : from, (from < 0 ? 0 : from) + 200);
      return JSON.parse(JSON.stringify({
        templates: [TPL], runs: page,
        cursor: page.length ? page[page.length - 1].uuid : (after || ''),
        has_more: page.length === 200,
      }));
    },
  });
  const big = sandbox(paged);
  await big.App.goChecklists();
  check(Object.keys(big._store.checklists).length === 210,
        'the phone keeps asking until the list is complete: '
        + Object.keys(big._store.checklists).length + ' of 210 in '
        + asked.length + ' call(s)');
  check(asked.length >= 2 && asked[1],
        'and the second call carries the cursor from the first: '
        + JSON.stringify(asked.slice(0, 2)));
  check(!!big._store.checklists['run-209']
        && !big._store.checklists['run-209'].deleted_at,
        'a checklist on the last page is there, and is not pruned as cancelled');

  console.log(failures ? 'RESULT FAIL (' + failures + ' check(s))' : 'RESULT PASS');
  process.exit(0);
})();
