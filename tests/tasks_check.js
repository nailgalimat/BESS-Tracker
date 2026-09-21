/**
 * The phone's Tasks tab, run for real: app.js is loaded into a sandbox with a
 * recording DOM, a memory IndexedDB and a fake server, and a job the office
 * assigned is opened, worked and closed the way a thumb does it.
 *
 * Checked: an assigned job is shown with who it is from and when it is due,
 * the technician's own records still show, opening a job fills THAT record in
 * (no second one is created), what is sent carries the same id and the
 * assignment untouched, a PM job keeps saying PM in the customer's line, the
 * work survives with no network, and the Today / Week / My open segments still
 * do what they did.
 *
 * Then the three failures a QA pass found:
 *
 *   C1  a job filled in offline was wiped by the first version conflict — the
 *       push came back "conflict" and the very same sync applied the server's
 *       copy over the only copy of the technician's text, hours and PTW
 *   M1  a second tap on Save wrote a second record, and a second line in the
 *       customer's 3.2
 *   M2  a job deleted on an older build vanished from Tasks and stayed on the
 *       server: the server answered outcome 'error' and nothing looked at it
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

function today(offset = 0) {
  const d = new Date();
  d.setDate(d.getDate() + offset);
  const p = n => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

// what the office wrote and the server sent to this phone
const JOB = {
  id: 'job-1', project_id: 7, category: 'maintenance', log_date: today(),
  description: 'PM round — September 2026 — block 5',
  fault_name: 'PM round — September 2026 — block 5',
  status: 'open', plant_block: 5, node_lc: 'LC1', node_device: 'BESS 3',
  ptw_no: 'PTW-2609-140', internal_note: '', hours: null,
  time_from: '', time_to: '', spare_parts: '', site_location: '',
  equipment_serial: '', sap_ticket: '', availability_impact: 'none',
  assigned_to: 'u-tech1', assigned_name: 'tech1', assigned_by: 'office',
  due_date: today(), sync_status: 'synced', version: 3,
  created_at: '2026-09-20T08:00:00.000Z', updated_at: '2026-09-20T08:00:00.000Z',
  deleted_at: null, tags: [], image_ids: [],
};
// one the technician wrote themselves, and one for somebody else
const MINE = Object.assign({}, JOB, {
  id: 'mine-1', category: 'fault', description: 'Fuse replaced',
  fault_name: 'Fuse blown', assigned_to: '', assigned_name: '',
  assigned_by: '', due_date: '', plant_block: 3, sync_status: 'synced',
});
const FUTURE = Object.assign({}, JOB, {
  id: 'job-2', log_date: today(3), due_date: today(3), plant_block: 6,
  description: 'PM round — September 2026 — block 6',
  fault_name: 'PM round — September 2026 — block 6',
});

function element() {
  return {
    value: '', textContent: '', innerHTML: '', disabled: false, style: {},
    dataset: {}, classList: { add() {}, remove() {}, toggle() {} },
    querySelectorAll: () => [], appendChild() {}, addEventListener() {},
  };
}

function sandbox(server, entries) {
  const els = {};
  const el = id => (els[id] || (els[id] = element()));
  const store = { entries: {}, meta: {}, checklists: {} };
  for (const e of entries) store.entries[e.id] = JSON.parse(JSON.stringify(e));
  const DB = {
    async getMeta(k, d) { return k in store.meta ? store.meta[k] : d; },
    async setMeta(k, v) { store.meta[k] = v; },
    async saveEntry(e) { store.entries[e.id] = e; },
    async getEntry(id) { return store.entries[id] || null; },
    async getAllEntries() { return Object.values(store.entries); },
    async getPendingEntries() {
      return Object.values(store.entries)
        .filter(e => ['local', 'pending'].includes(e.sync_status));
    },
    async markSynced(id, v) {
      const e = store.entries[id];
      if (e) { e.sync_status = 'synced'; if (v != null) e.version = v; }
    },
    async deleteEntry(id) { delete store.entries[id]; },
    async getImagesForEntry() { return []; },
    async getPendingImages() { return []; },
    async deleteImagesForEntry() {},
    async getAllFieldEvents() { return []; },
    async getPendingFieldEvents() { return []; },
    async getPendingWriteoffs() { return []; },
    async getAllChecklists() { return []; },
    async getPendingChecklists() { return []; },
    async getChecklist() { return null; },
    async saveChecklist() {}, async updateChecklist() {},
  };
  const ctxObj = {
    console, setTimeout, clearTimeout, setInterval, clearInterval,
    navigator: { onLine: true, serviceWorker: { addEventListener() {} } },
    localStorage: {
      _d: { username: 'tech1', user_id: 'u-tech1', last_project_id: '7',
            device_id: 'phone-1', access_token: 'tok' },
      getItem(k) { return this._d[k] === undefined ? null : this._d[k]; },
      setItem(k, v) { this._d[k] = v; }, removeItem(k) { delete this._d[k]; },
    },
    document: {
      addEventListener() {}, getElementById: el, querySelectorAll: () => [],
      createElement: () => element(), body: { classList: { toggle() {} } },
    },
    alert() {}, confirm: () => true,
    fetch: () => Promise.reject(new Error('no network here')),
    indexedDB: {}, crypto: { randomUUID: () => 'uuid-' + Math.random() },
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
  // the server, as routers/sync.py behaves for a technician
  const pushed = [];
  const server = {
    async getProjects() { return [{ id: 7, name: 'TK', num_blocks: 16 }]; },
    async pullDelta() { return { changes: [], cursor: '1', has_more: false }; },
    async pushChanges(changes) {
      pushed.push(...changes);
      return { results: changes.map(c => ({ id: c.id, outcome: 'applied',
                                            server_version: (c.version || 1) + 1 })) };
    },
    async uploadImage() { return {}; },
  };

  const ctx = sandbox(server, [JOB, MINE, FUTURE]);
  const App = ctx.App;

  // ── the list ─────────────────────────────────────────────────────────────
  await App.goTasks();
  let html = ctx._els['tasks-body'].innerHTML;
  check(html.includes('Block 5') && html.includes('PM round — September 2026'),
        'the job the office assigned is on the Tasks tab');
  check(html.includes('From office') && html.includes('due ' + today()),
        'with who it is from and when it is due');
  check(html.includes('Assigned') && html.includes('tcard assigned'),
        'and it is marked as a job, not as one of my own records');
  check(html.includes("App.openTask('job-1')"),
        'tapping it opens the job, not a read-only record view');
  check(html.indexOf('job-1') < html.indexOf('mine-1'),
        'what the office is waiting for comes first');
  check(html.includes('Fuse blown'),
        "the technician's own open record is still listed");
  check(!html.includes('not in this version'),
        'no line claiming office jobs cannot arrive here');

  await App.taskSeg('week');
  html = ctx._els['tasks-body'].innerHTML;
  check(html.includes('job-2'), 'Week reaches forward to a job due in three days');
  await App.taskSeg('open');
  html = ctx._els['tasks-body'].innerHTML;
  check(html.includes('job-1') && html.includes('job-2') && html.includes('mine-1'),
        'My open still lists everything open');
  await App.taskSeg('today');
  html = ctx._els['tasks-body'].innerHTML;
  check(html.includes('job-1') && !html.includes('job-2'),
        'Today is still only today');

  // ── one job, opened and worked ───────────────────────────────────────────
  await App.openTask('job-1');
  check(ctx._els['task-node'].textContent === 'Block 5 · LC1 · BESS 3',
        'the job says exactly where: ' + ctx._els['task-node'].textContent);
  check(ctx._els['task-from'].textContent.includes('From office')
        && ctx._els['task-from'].textContent.includes('due ' + today())
        && ctx._els['task-from'].textContent.includes('PTW-2609-140'),
        'who gave it, when it is due and the permit: '
        + ctx._els['task-from'].textContent);
  check(ctx._els['task-ask'].textContent.includes('PM round — September 2026'),
        'and what is being asked for');
  check(ctx._els['task-desc'].value === '',
        'the "what was done" box starts empty — the office\'s ask is not an answer');

  // nothing written: closing it is refused rather than sending an empty line
  await App.completeTask();
  check(ctx._els['task-error'].style.display === 'block'
        && ctx._store.entries['job-1'].status === 'open',
        'closing it with nothing written is refused');

  ctx._els['task-desc'].value = 'Coolant topped up, level checked';
  ctx._els['task-start'].value = '09:10';
  ctx._els['task-end'].value = '10:40';
  App.recalcTaskHours();
  check(ctx._els['task-hours'].value === '1.5',
        'end − start fills the hours: ' + ctx._els['task-hours'].value);
  ctx._els['task-note'].value = 'pump was warm again';
  await App.saveTask();
  let job = ctx._store.entries['job-1'];
  check(job.status === 'open' && job.internal_note === 'pump was warm again'
        && job.sync_status === 'local',
        'Save keeps it open and waiting to send');

  await App.completeTask();
  job = ctx._store.entries['job-1'];
  check(Object.keys(ctx._store.entries).length === 3,
        'no second record is created: ' + Object.keys(ctx._store.entries).join(', '));
  check(job.status === 'done' && job.hours === 1.5 && job.time_from === '09:10',
        'the job itself is closed, with the hours on it');
  check(/^PM: /.test(job.description),
        'a PM job keeps saying PM in the customer\'s line (3.2 counts it once): '
        + job.description);
  check(job.assigned_to === 'u-tech1' && job.assigned_by === 'office'
        && job.due_date === today(),
        'and it is still the office\'s job — the assignment is untouched');

  const sent = pushed.filter(c => c.id === 'job-1');
  check(sent.length === 1 && sent[0].version === 3,
        'it is pushed as the same record, on the version it was pulled at');
  check(sent[0].payload.status === 'done'
        && sent[0].payload.description.includes('Coolant topped up')
        && sent[0].payload.hours === 1.5,
        'carrying the status, the text and the hours');
  check(!('assigned_to' in sent[0].payload),
        'and not the assignment: only the office decides who does what');

  // ── no signal ────────────────────────────────────────────────────────────
  const off = sandbox(server, [JOB]);
  off.navigator.onLine = false;
  await off.App.goTasks();
  await off.App.openTask('job-1');
  off._els['task-desc'].value = 'done in a container with no signal';
  await off.App.completeTask();
  const kept = off._store.entries['job-1'];
  check(kept.status === 'done' && kept.sync_status === 'local'
        && kept.description.includes('no signal'),
        'work done offline is kept on the phone, marked to send');

  // ── C1: a filled job survives a version conflict ─────────────────────────
  // The office publishes the job (v1), the phone pulls it, the technician
  // fills it in offline (still v1), the office edits the same record (v2).
  // The push comes back "conflict" — and the same sync then applied the
  // server's v2 straight over the work. It is the only copy there is.
  const office = {
    async getProjects() { return []; },
    async pushChanges(changes) {
      return { results: changes.map(c => ({ id: c.id, outcome: 'conflict' })) };
    },
    async pullDelta() {
      return {
        changes: [{ entity: 'work_log', id: 'job-1', updated_at: '2026-09-21T09:00:00Z',
                    data: { ...JOB, version: JOB.version + 1, status: 'open',
                            hours: null, time_from: '', time_to: '',
                            ptw_no: 'PTW-NEW', internal_note: '',
                            description: 'PM round — do block 5 today please' } }],
        cursor: '2', has_more: false,
      };
    },
    async uploadImage() { return {}; },
  };
  const cx = sandbox(office, [JOB]);
  cx.navigator.onLine = false;             // filled in a container, no signal
  await cx.App.openTask('job-1');
  cx._els['task-desc'].value = 'Coolant topped up, filter cleaned';
  cx._els['task-start'].value = '09:10';
  cx._els['task-end'].value = '10:40';
  cx.App.recalcTaskHours();
  cx._els['task-ptw'].value = 'PTW-2609-140';
  await cx.App.completeTask();
  cx.navigator.onLine = true;              // back within reach of the mast
  const res = await cx.App._doSync();
  const onSite = cx._store.entries['job-1'];
  check(onSite.description.includes('Coolant topped up') && onSite.hours === 1.5
        && onSite.ptw_no === 'PTW-2609-140' && onSite.status === 'done',
        'the work done on site is still there after the conflict: '
        + JSON.stringify([onSite.description.slice(0, 24), onSite.hours, onSite.ptw_no]));
  check(onSite.sync_status === 'conflict' && res.conflicts === 1,
        'the record is flagged as a conflict, and the sync says so');
  check(onSite.server_changed === true,
        'and it knows the office has changed it since');
  const renderCard = vm.runInContext('_renderCard', cx);
  check(renderCard(onSite).includes('The office changed this'),
        'the card says so in words the technician can act on');

  // a conflict the office has NOT touched again is left exactly as it was
  const quiet = sandbox({ ...office, async pullDelta() {
    return { changes: [], cursor: '2', has_more: false }; } }, [JOB]);
  quiet.navigator.onLine = false;
  await quiet.App.openTask('job-1');
  quiet._els['task-desc'].value = 'done, nothing else';
  await quiet.App.completeTask();
  quiet.navigator.onLine = true;
  await quiet.App._doSync();
  check(quiet._store.entries['job-1'].server_changed === undefined,
        'a conflict nobody else touched is not dressed up as one');

  // ── M1: one tap, one record ──────────────────────────────────────────────
  let release;
  const slow = sandbox(server, []);
  const held = new Promise(r => { release = r; });
  const realSave = slow.DB.saveEntry;
  slow.DB.saveEntry = async e => { await held; return realSave(e); };
  const field = id => slow.document.getElementById(id);
  field('f-date').value = today();
  field('f-desc').value = 'Fuse replaced on BESS 2';
  field('f-block').value = '5';
  const first = slow.App.saveEntry();
  check(field('save-btn').disabled === true,
        'Save is dead while the record is being written');
  const second = slow.App.saveEntry();       // the impatient second tap
  release();
  await first; await second;
  check(Object.keys(slow._store.entries).length === 1,
        'a double tap writes ONE record, not two: '
        + Object.keys(slow._store.entries).length);
  check(field('save-btn').disabled === false,
        'and the button comes back afterwards');

  // ── M2: a delete the server refuses ──────────────────────────────────────
  const refuse = {
    async getProjects() { return []; },
    async pushChanges(changes) {
      return { results: changes.map(c => ({ id: c.id, outcome: 'error',
                                            message: 'Not your entry' })) };
    },
    async pullDelta() { return { changes: [], cursor: '1', has_more: false }; },
    async uploadImage() { return {}; },
  };
  const dx = sandbox(refuse, [JOB]);
  await dx.App._deleteById('job-1');
  check(dx._store.entries['job-1'].deleted_at,
        'the phone marks the job deleted and queues it');
  await dx.App._doSync();
  const backAgain = dx._store.entries['job-1'];
  check(!backAgain.deleted_at,
        'the server refuses it, so the job comes back — it did not stop '
        + 'being real work');
  check(backAgain.version === JOB.version,
        'on the version the server holds: ' + backAgain.version);
  check(backAgain.sync_status === 'error'
        && /Not your entry/.test(backAgain.last_error || ''),
        'and it says why: ' + backAgain.last_error);
  await dx.App.goTasks();
  check(dx._els['tasks-body'].innerHTML.includes('Block 5'),
        'the job is on the Tasks tab again, not silently gone');

  // the Delete button is hidden by who the job is FOR — the office account
  // often has no username, so assigned_by is legitimately empty
  const noName = { ...JOB, assigned_by: '' };
  check(!renderCard(noName).includes('>Delete<'),
        'a job from an office account with no username still cannot be deleted');
  check(renderCard(MINE).includes('>Delete<'),
        "while the technician's own record still can be");

  console.log(failures ? 'RESULT FAIL (' + failures + ' check(s))' : 'RESULT PASS');
  process.exit(0);
})();
